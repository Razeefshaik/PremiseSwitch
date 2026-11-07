"""Real local Switch protocol double; all cross-thread controls are awaited."""
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import socket
import threading
import time
from copy import deepcopy
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect


class FakeSwitch:
    def __init__(self):
        self.app = FastAPI()
        self.requests: list[dict[str, Any]] = []
        self.connections: list[dict[str, str]] = []
        self.pongs: list[dict[str, Any]] = []
        self.heartbeat_evictions: list[str] = []
        self.rooms = {"room-1": "payments"}
        self.participants = {"room-1": self.default_participants()}
        self.histories: dict[str, list[dict[str, Any]]] = {}
        self.links: dict[str, list[dict[str, Any]]] = {}
        self.sockets: dict[str, WebSocket] = {}
        self.conflict = False
        self.refusal: int | None = None
        self.generation = 0
        self.sequence = 0
        self.heartbeat_interval = 0.05
        self.heartbeat_timeout = 0.3
        self._register_routes()

    @staticmethod
    def default_participants():
        return [{"id": "user-1", "name": "razeef", "type": "user", "status": None, "alias": None},
                {"id": "agent-1", "name": "premise", "type": "agent", "status": None, "alias": None},
                {"id": "coder-1", "name": "coder.razeef", "type": "agent", "status": None, "alias": None}]

    def _register_routes(self):
        @self.app.post("/agents")
        async def register(request: Request):
            body = await request.json()
            self.requests.append({"operation": "register", "arguments": body, "headers": dict(request.headers)})
            if self.conflict:
                raise HTTPException(409, "already registered")
            assert "overwrite" not in body
            return {"id": "agent-1", "api_key": "fake-agent-secret"}

        @self.app.post("/agents/{agent_id}/ops/{operation}")
        async def op(agent_id: str, operation: str, request: Request):
            arguments = await request.json()
            header = request.headers.get("X-Switch-Connection-Id")
            self.requests.append({"operation": operation, "arguments": arguments,
                                  "connection_id": header, "headers": dict(request.headers)})
            if request.headers.get("authorization") != "Bearer fake-agent-secret" or agent_id != "agent-1":
                raise HTTPException(401)
            operations = {
                "list_rooms": self._list_rooms, "list_participants": self._list_participants,
                "post_message": self._post_message, "send_targeted_message": self._send_targeted_message,
                "read_context": self._read_context, "list_linked_rooms": self._list_linked_rooms,
                "create_room_document": self._create_document, "update_room_document": self._update_document,
            }
            if operation not in operations:
                raise HTTPException(404)
            method = operations[operation]
            try:
                inspect.signature(method).bind(**arguments)
            except TypeError:
                raise HTTPException(400, "invalid arguments") from None
            room_id = next((r for r, ws in self.sockets.items()
                            if ws.query_params["connection_id"] == header), None)
            if operation != "list_rooms" and room_id is None:
                raise HTTPException(409, "dead connection")
            return {"result": method(**arguments)(room_id)}

        @self.app.websocket("/agents/{agent_id}/connection/ws")
        async def connect(ws: WebSocket, agent_id: str):
            await ws.accept()
            query = dict(ws.query_params)
            self.requests.append({"operation": "websocket", "arguments": query, "headers": dict(ws.headers)})
            if ws.headers.get("authorization") != "Bearer fake-agent-secret":
                await ws.close(4401)
                return
            self.connections.append(query)
            if self.refusal is not None:
                await ws.send_json({"event": "refused", "data": {"status": self.refusal, "detail": "refused"}})
                await ws.close(4000 + self.refusal)
                return
            room_id = query["rooms"]
            assert "," not in room_id and query["filter"] == "all"
            self.sockets[room_id] = ws
            self.generation += 1
            await ws.send_json({"event": "connection_state", "data": {
                "connection_id": query["connection_id"], "agent_id": agent_id,
                "generation": self.generation, "rooms": [room_id], "cursor": 0,
                "heartbeat_interval_seconds": self.heartbeat_interval}})
            last_pong = time.monotonic()

            async def beats():
                while True:
                    await asyncio.sleep(self.heartbeat_interval)
                    if time.monotonic() - last_pong > self.heartbeat_timeout:
                        self.heartbeat_evictions.append(room_id)
                        await ws.send_json({"event": "evicted", "data": {"code": "heartbeat_lapsed"}})
                        await ws.close(1000)
                        return
                    await ws.send_json({"event": "ping", "data": {}})

            task = asyncio.create_task(beats())
            try:
                while True:
                    data = await ws.receive_json()
                    self.pongs.append({"room_id": room_id, "time": time.monotonic(), **data})
                    assert set(data) == {"type", "cursor"} and data["type"] == "pong"
                    last_pong = time.monotonic()
            except (WebSocketDisconnect, RuntimeError):
                pass
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if self.sockets.get(room_id) is ws:
                    del self.sockets[room_id]

    def _list_rooms(self, include_archived=False):
        return lambda room: [{"room_id": rid, "name": name, "description": "", "connected": rid in self.sockets,
                              "archived": False} for rid, name in self.rooms.items()]

    def _list_participants(self):
        return lambda room: self.participants[room]

    def _post_message(self, body, thread_id=None):
        return lambda room: {"event_id": f"event-{len(self.requests)}"}

    def _send_targeted_message(self, body, target_names=None, target_roles=None, thread_id=None):
        assert target_names or target_roles
        return self._post_message(body, thread_id)

    def _read_context(self, limit=50, since=None, before=None, room_id=None):
        def read(room):
            from datetime import datetime
            entries = self.histories.get(room_id or room, [])
            if since:
                cutoff = int(datetime.fromisoformat(since).timestamp() * 1000)
                entries = [t for t in entries if t["root"]["timestamp"] >= cutoff]
            if before:
                cutoff = int(datetime.fromisoformat(before).timestamp() * 1000)
                entries = [t for t in entries if t["root"]["timestamp"] < cutoff]
            selected = entries[-limit:]
            return {"threads": selected, "truncated": len(entries) > len(selected),
                    "oldest_timestamp": selected[0]["root"]["timestamp"] if selected else None}
        return read

    def _list_linked_rooms(self):
        return lambda room: self.links.get(room, [])

    def _create_document(self, name, description, instructions, content):
        return lambda room: {"document_id": f"doc-{len(self.requests)}"}

    def _update_document(self, document_id, name=None, description=None, instructions=None, content=None):
        return lambda room: {"document_id": document_id, "status": "ok"}

    def __enter__(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="critical", access_log=False,
                                                   ws="websockets-sansio", lifespan="off"))
        self.loop = asyncio.new_event_loop()

        def serve():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.server.serve(sockets=[sock]))
            self.loop.close()

        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 5
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("fake Switch failed to start")
            time.sleep(0.01)
        return self

    def __exit__(self, *args):
        self.server.should_exit = True
        self.thread.join(timeout=5)
        assert not self.thread.is_alive(), "fake Switch did not stop"

    async def _control(self, coroutine):
        future: concurrent.futures.Future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        return await asyncio.wrap_future(future)

    async def send_frame(self, room_id, frame):
        async def send():
            await self.sockets[room_id].send_json(deepcopy(frame))
        await self._control(send())

    async def replay(self, room_id, frame):
        await self.send_frame(room_id, frame)

    async def drop_socket(self, room_id, code=1012):
        async def drop():
            await self.sockets[room_id].close(code)
        await self._control(drop())

    async def evict(self, room_id, code):
        await self.send_frame(room_id, {"event": "evicted", "data": {"code": code, "reason": "test"}})

    async def gap(self, room_id, resumed_at):
        await self.send_frame(room_id, {"event": "gap", "data": {"from_sequence": 0,
                              "resumed_at": resumed_at, "rooms": [room_id], "reason": "test"}})

    async def add_room(self, room_id, name="new room"):
        async def add():
            self.rooms[room_id] = name
            self.participants[room_id] = self.default_participants()
        await self._control(add())

    async def remove_room(self, room_id):
        async def remove():
            del self.rooms[room_id]
        await self._control(remove())

    def message(self, message_id="m-1", room_id="room-1", **payload):
        self.sequence += 1
        return {"event": "message", "id": self.sequence, "data": {
            "type": "message", "room_id": room_id, "sequence": self.sequence, "payload": {
                "message_id": message_id, "sender": "user-1", "sender_name": "Razeef",
                "sender_kind": "user", "body": "private room text", "timestamp": 1793000000000,
                "thread_id": None, "addressed": True, "attachments": [], **payload}}}
