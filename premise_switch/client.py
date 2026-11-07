"""Switch HTTP operations and per-room WebSocket transport."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from premise_switch.contracts import InboundMessage
from premise_switch.state import StateStore


logger = logging.getLogger(__name__)
# websockets DEBUG logs include Authorization and frame bodies. Give this
# connection a private, disabled logger instead of changing global logging.
_wire_logger = logging.Logger("premise_switch.private_transport")
_wire_logger.disabled = True


async def register_once(server_url: str, registration_key: str, state: StateStore) -> None:
    """Explicit provisioning only; startup must use the stored identity."""
    if state.agent_id:
        return
    async with httpx.AsyncClient(base_url=server_url.rstrip("/"), timeout=30) as http:
        response = await http.post("/agents", headers={"Authorization": f"Bearer {registration_key}"}, json={
            "name": "premise", "description": "Deterministic decision memory and change checks",
            "connector_type": "premise", "integration_profile": {
                "connection_model": "always_on", "message_exchange": True,
                "pre_invocation_mediation": [], "post_invocation_mediation": [], "event_reporting": [],
            },
        })
        if response.status_code == 409:
            raise RuntimeError("Premise is already registered. Restore the owner's existing state.json "
                               "with agent_id and api_key; ask the owner to recover credentials if lost. "
                               "Automatic overwrite is disabled.")
        response.raise_for_status()
        result = response.json()
        state.set_agent(result["id"], result["api_key"])


class SwitchClient:
    """One stable connection and one ordered handler queue per room.

    Callbacks may be asynchronous or synchronous (the latter run in a thread).
    on_room_join receives (room_id, frame_data). run() never registers an agent.
    """

    def __init__(self, server_url: str, state: StateStore, on_message: Callable,
                 on_room_join: Callable | None = None, room_poll_seconds: float = 15):
        if not state.agent_id or not state.api_key:
            raise RuntimeError("Load existing Switch state or explicitly call register_once before startup")
        if room_poll_seconds <= 0:
            raise ValueError("room_poll_seconds must be positive")
        self.server_url = server_url.rstrip("/")
        self.state = state
        self.on_message = on_message
        self.on_room_join = on_room_join
        self.room_poll_seconds = room_poll_seconds
        self._http = httpx.AsyncClient(base_url=self.server_url, timeout=30,
                                      headers={"Authorization": f"Bearer {state.api_key}"})
        self._stop = asyncio.Event()
        self._done = asyncio.Event()
        self._runner: asyncio.Task | None = None
        self._rooms: dict[str, asyncio.Task] = {}
        self._room_names: dict[str, str] = {}
        self._participants: dict[str, list[dict[str, Any]]] = {}

    async def _op(self, operation: str, arguments: dict[str, Any], room_id: str | None = None) -> Any:
        headers = {}
        if room_id is not None:
            headers["X-Switch-Connection-Id"] = self.state.room(room_id)["connection_id"]
        response = await self._http.post(f"/agents/{quote(self.state.agent_id, safe='')}/ops/{operation}",
                                         headers=headers, json=arguments)
        response.raise_for_status()
        return response.json()["result"]

    async def run(self) -> None:
        if self._runner is not None:
            raise RuntimeError("SwitchClient.run may only be called once")
        self._runner = asyncio.current_task()
        try:
            while not self._stop.is_set():
                try:
                    await self._discover_rooms()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {401, 403}:
                        raise
                    logger.warning("room_poll_failed exception_type=%s", type(exc).__name__)
                except httpx.RequestError as exc:
                    logger.warning("room_poll_failed exception_type=%s", type(exc).__name__)
                await self._wait(self.room_poll_seconds)
        finally:
            self._stop.set()
            for task in self._rooms.values():
                task.cancel()
            await asyncio.gather(*self._rooms.values(), return_exceptions=True)
            self._rooms.clear()
            await self._http.aclose()
            self._done.set()

    async def stop(self) -> None:
        self._stop.set()
        if self._runner is None:
            await self._http.aclose()
        elif asyncio.current_task() is not self._runner:
            await self._done.wait()

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _discover_rooms(self) -> None:
        rooms = {r["room_id"]: r["name"] for r in await self._op("list_rooms", {}) if not r.get("archived")}
        self._room_names.update(rooms)
        for room_id in self._rooms.keys() - rooms.keys():
            task = self._rooms.pop(room_id)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._participants.pop(room_id, None)
        for room_id in rooms:
            if room_id not in self._rooms and not self.state.room(room_id).get("halted"):
                self._rooms[room_id] = asyncio.create_task(self._serve_room(room_id))

    def _socket_url(self, room_id: str) -> str:
        room = self.state.room(room_id)
        params = {"connection_id": room["connection_id"], "rooms": room_id, "filter": "all",
                  "start_from": room["cursor"] if room["cursor"] is not None else "head"}
        if room["generation"] is not None:
            params["expected_generation"] = room["generation"]
        base = urlsplit(self.server_url)
        return urlunsplit(("wss" if base.scheme == "https" else "ws", base.netloc,
                           f"{base.path}/agents/{quote(self.state.agent_id, safe='')}/connection/ws",
                           urlencode(params), ""))

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        ceiling = min(30.0, 2.0 ** min(attempt + 1, 5))
        return random.uniform(max(1.0, ceiling / 2), ceiling)

    def _halt(self, room_id: str, reason: str) -> None:
        self.state.room(room_id)["halted"] = reason
        self.state.save()
        logger.warning("room_stopped room_id=%s", room_id)

    async def _serve_room(self, room_id: str) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        worker = asyncio.create_task(self._consume(room_id, queue))
        attempt = 0
        try:
            while not self._stop.is_set() and not self.state.room(room_id).get("halted"):
                try:
                    async with connect(self._socket_url(room_id),
                                       additional_headers={"Authorization": f"Bearer {self.state.api_key}"},
                                       ping_interval=None, logger=_wire_logger) as ws:
                        await self._read(room_id, ws, queue)
                except ConnectionClosed as exc:
                    if exc.rcvd is not None and exc.rcvd.code in {4400, 4401, 4403, 4404, 4409}:
                        self._halt(room_id, "connection_refused")
                except InvalidStatus as exc:
                    if exc.response.status_code in {400, 401, 403, 404, 409}:
                        self._halt(room_id, "handshake_refused")
                    logger.warning("socket_failed room_id=%s exception_type=%s", room_id, type(exc).__name__)
                except (OSError, TimeoutError, ValueError) as exc:
                    logger.warning("socket_failed room_id=%s exception_type=%s", room_id, type(exc).__name__)
                if self.state.room(room_id).get("halted"):
                    break
                await self._wait(self._retry_delay(attempt))
                attempt += 1
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _read(self, room_id: str, ws, queue: asyncio.Queue) -> None:
        async for raw in ws:
            frame = json.loads(raw)
            event, data = frame["event"], frame.get("data", {})
            if event == "ping":
                await ws.send(json.dumps({"type": "pong", "cursor": self.state.room(room_id)["cursor"] or 0}))
            elif event == "connection_state":
                room = self.state.room(room_id)
                room["generation"] = data["generation"]
                if room["cursor"] is None:
                    room["cursor"] = data.get("cursor", 0)
                self.state.save()
            elif event == "evicted":
                if data.get("code") in {"taken_over", "closed"}:
                    self._halt(room_id, data["code"])
                return
            elif event == "refused":
                if data.get("status") in {400, 401, 403, 404, 409}:
                    self._halt(room_id, "refused")
                return
            else:
                queue.put_nowait(frame)

    async def _consume(self, room_id: str, queue: asyncio.Queue) -> None:
        while True:
            frame = await queue.get()
            try:
                data = frame.get("data", {})
                if frame["event"] == "message":
                    message = await self._normalise(room_id, data["payload"], data["sequence"])
                    if message is not None and self.state.remember(room_id, message.message_id):
                        await self._callback(self.on_message, room_id, message)
                elif frame["event"] == "room_join":
                    self._participants.pop(room_id, None)
                    if self.on_room_join is not None:
                        await self._callback(self.on_room_join, room_id, room_id, data)
                self.state.advance(room_id, data.get("sequence", frame.get("id")))
            finally:
                queue.task_done()

    async def _callback(self, callback: Callable, room_id: str, *args) -> None:
        try:
            if inspect.iscoroutinefunction(callback):
                await callback(*args)
            else:
                result = await asyncio.to_thread(callback, *args)
                if inspect.isawaitable(result):
                    await result
        except Exception as exc:
            logger.warning("handler_failed room_id=%s exception_type=%s", room_id, type(exc).__name__)

    async def _normalise(self, room_id: str, payload: dict[str, Any], sequence: int = 0,
                         *, addressed: bool | None = None) -> InboundMessage | None:
        sender = payload["sender"]
        if sender == self.state.agent_id:
            return None

        def find():
            participants = self._participants.get(room_id, [])
            return (next((p for p in participants if p["id"] == sender), None)
                    or next((p for p in participants if p["name"] == sender), None))

        participant = find()
        if participant is None:
            try:
                self._participants[room_id] = await self._op("list_participants", {}, room_id)
            except httpx.HTTPError as exc:
                logger.warning("participants_failed room_id=%s exception_type=%s", room_id, type(exc).__name__)
            participant = find()
        if participant is not None and participant["id"] == self.state.agent_id:
            return None
        return InboundMessage(
            room_id=room_id, room_name=self._room_names.get(room_id, room_id),
            message_id=payload.get("message_id", payload.get("id", "")), sequence=sequence,
            sender=sender, sender_name=payload.get("sender_name", sender),
            sender_handle=participant["name"] if participant else sender,
            sender_is_agent=participant is None or participant.get("type") != "user",
            body=payload.get("body", ""), timestamp_ms=payload.get("timestamp", 0),
            thread_id=payload.get("thread_id"),
            addressed=bool(payload.get("addressed", False)) if addressed is None else addressed,
        )
