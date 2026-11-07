"""Frozen interfaces between the premise_switch parts. See docs/CONTRACTS.md.

Cards build against these types so they can run in parallel. Changing this file
needs the owner's approval and a version bump in docs/CONTRACTS.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class InboundMessage:
    """One `message` event, normalised by the client from the Switch frame."""

    room_id: str             # data.room_id
    room_name: str           # from list_rooms
    message_id: str          # payload.message_id, the dedupe key
    sequence: int            # data.sequence: per agent across rooms, not per room
    sender: str              # payload.sender
    sender_name: str         # payload.sender_name, for display
    sender_handle: str       # participant `name`, the value mentions must use
    sender_is_agent: bool    # from list_participants `type`
    body: str                # payload.body
    timestamp_ms: int        # payload.timestamp
    thread_id: str | None    # payload.thread_id
    addressed: bool          # payload.addressed


@dataclass(frozen=True)
class Reply:
    """What to post. `mention` non-empty means send_targeted_message, else post_message."""

    body: str
    thread_id: str | None = None
    mention: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProposalNotice:
    """A drift proposal to announce once per drift per room."""

    room_id: str
    drift_key: str           # the proposal's `key`, for example "fact=value"
    body: str


@dataclass(frozen=True)
class LinkedRoom:
    target_room_id: str
    target_room_name: str
    label: str
    is_member: bool          # list_linked_rooms `access` == "member"


class RoomGateway(Protocol):
    """Everything the rest of the package may do to Switch. Implemented by client.py."""

    async def post(self, room_id: str, reply: Reply) -> str:
        """Post into the room and return the event id."""

    async def history(self, room_id: str, limit: int = 50, since: str | None = None) -> list[InboundMessage]:
        """Read room history through read_context."""

    async def upsert_document(self, room_id: str, name: str, description: str, instructions: str, content: str) -> str:
        """Create or update this agent's room document by name and return its id."""

    async def linked_rooms(self, room_id: str) -> list[LinkedRoom]:
        """Rooms linked from this room."""


class ToolBackend(Protocol):
    """premise.graph.tools.GraphTools satisfies this. Synchronous: call it off the event loop."""

    def call(self, name: str, arguments: dict[str, Any]) -> Any: ...


class Responder(Protocol):
    """Answer + Guard, implemented by answer.py. None means stay silent."""

    def respond(self, message: InboundMessage) -> Reply | None: ...


class Listener(Protocol):
    """Listen, implemented by listen.py. Both methods are synchronous."""

    def record(self, message: InboundMessage) -> None:
        """Journal one message. Must ignore agents and be idempotent on message_id."""

    def run_once(self) -> list[ProposalNotice]:
        """Run the v2 pipeline over the journal and return proposals not yet announced."""
