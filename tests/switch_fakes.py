"""In-memory stand-ins for the premise_switch contracts. Needs no FalkorDB and no Switch."""
from __future__ import annotations

from typing import Any

from premise_switch.contracts import InboundMessage, LinkedRoom, Reply


def message(body: str = "hello", **overrides: Any) -> InboundMessage:
    values: dict[str, Any] = {
        "room_id": "room-1", "room_name": "kestrel-dev", "message_id": "m-1", "sequence": 1,
        "sender": "user-1", "sender_name": "Razeef", "sender_handle": "razeef", "sender_is_agent": False,
        "body": body, "timestamp_ms": 1_793_000_000_000, "thread_id": None, "addressed": True,
    }
    values.update(overrides)
    return InboundMessage(**values)


class FakeTools:
    """Returns canned results by tool name. A callable result can inspect the arguments or raise."""

    def __init__(self, results: dict[str, Any] | None = None):
        self.results = results or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        result = self.results.get(name)
        return result(arguments) if callable(result) else result


class FakeGateway:
    def __init__(self) -> None:
        self.posts: list[tuple[str, Reply]] = []
        self.documents: dict[tuple[str, str], str] = {}
        self.links: dict[str, list[LinkedRoom]] = {}
        self.messages: dict[str, list[InboundMessage]] = {}

    async def post(self, room_id: str, reply: Reply) -> str:
        self.posts.append((room_id, reply))
        return f"event-{len(self.posts)}"

    async def history(self, room_id: str, limit: int = 50, since: str | None = None) -> list[InboundMessage]:
        return self.messages.get(room_id, [])[-limit:]

    async def upsert_document(self, room_id: str, name: str, description: str, instructions: str, content: str) -> str:
        self.documents[(room_id, name)] = content
        return f"doc-{name}"

    async def linked_rooms(self, room_id: str) -> list[LinkedRoom]:
        return self.links.get(room_id, [])
