"""Optional Switch presentation helpers; wiring remains with the adapter owner."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
import json
import logging
import os
import re
from typing import Any

from .contracts import Reply, RoomGateway
from .format import HELP, escape

log = logging.getLogger(__name__)
LEDGER_NAME = "Premise decision ledger"
# Switch's room-document operation permits 1 MiB of UTF-8 content. Stay well below it.
LEDGER_MAX_BYTES = 16_000
LEDGER_MAX_ROWS = 100
_HEADER = "| Time (UTC) | Kind | Verdict / proposal | Change set | Thread |\n| --- | --- | --- | --- | --- |\n"


def _cell(value: Any, limit: int = 400) -> str:
    text = " ".join(str(value).split())[:limit]
    return escape(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                  .replace("|", "&#124;").replace("`", "'"))


class DecisionLedger:
    """One bounded document per room; rows live for this instance's lifetime.

    Await record(room_id, "verdict" | "proposal", summary). Summary may be
    plain text, or a mapping with verdict, changes and thread_id. A proposal
    mapping can use summary for its description. Never pass credentials.
    """

    def __init__(self, gateway: RoomGateway):
        self.gateway = gateway
        self._rows: dict[str, list[str]] = defaultdict(list)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def flush(self, room_id: str) -> bool:
        """Write the room's rows as one document update."""
        try:
            async with self._locks[room_id]:
                await self.gateway.upsert_document(
                    room_id, LEDGER_NAME, "Recent Premise verdicts and proposals, newest first.",
                    "Historical summaries only. Ask for a fresh check before editing config; a human approves merges.",
                    _HEADER + "".join(self._rows[room_id]))
            return True
        except Exception as exc:
            log.warning("ledger_failed exception_type=%s", type(exc).__name__)
            return False

    async def record(self, room_id: str, kind: str, summary: str | Mapping[str, Any], *, write: bool = True) -> bool:
        """Return success; retain bounded rows for the next attempt on failure.

        write=False only stores the row, for callers that batch updates with flush(). Every
        document update posts a notice in the room, so batching keeps rooms readable.
        """
        try:
            async with self._locks[room_id]:
                if kind not in {"verdict", "proposal"}:
                    raise ValueError("Unsupported ledger row kind")
                if isinstance(summary, Mapping):
                    status = summary.get("verdict") or summary.get("summary") or kind
                    changes = summary.get("changes", "")
                    thread = summary.get("thread_id") or "top-level"
                else:
                    status, changes, thread = kind, summary, "top-level"
                if not isinstance(changes, str):
                    changes = json.dumps(changes, ensure_ascii=False, sort_keys=True)
                stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                row = "| " + " | ".join(_cell(v) for v in (stamp, kind, status, changes, thread)) + " |\n"
                rows = [row, *self._rows[room_id]][:LEDGER_MAX_ROWS]
                while len((_HEADER + "".join(rows)).encode("utf-8")) > LEDGER_MAX_BYTES:
                    rows.pop()
                self._rows[room_id] = rows
                if not write:
                    return True
                await self.gateway.upsert_document(
                    room_id, LEDGER_NAME, "Recent Premise verdicts and proposals, newest first.",
                    "Historical summaries only. Ask for a fresh check before editing config; a human approves merges.",
                    _HEADER + "".join(rows))
            return True
        except Exception as exc:
            log.warning("ledger_failed exception_type=%s", type(exc).__name__)
            return False


def reviewer_mentions(reviewers, handles=None) -> str:
    """Render IDs or an {id: display_name} mapping using only approved humans.

    handles is the SWITCH_REVIEWER_HANDLES string or its parsed mapping. None
    reads the environment. The owner must only allowlist human usernames.
    Graph names are display text, never a source of mention targets.
    """
    if handles is None:
        handles = os.getenv("SWITCH_REVIEWER_HANDLES", "")
    if isinstance(handles, str):
        entries = (part.partition("=") for part in handles.split(","))
        handles = {person.strip(): username.strip() for person, sep, username in entries if sep and person.strip()}
    approved = {
        person: username for person, username in handles.items()
        if isinstance(username, str) and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", username)
        and username not in {"all", "channel", "here", "premise"}
    }
    people = reviewers.items() if isinstance(reviewers, Mapping) else ((person, person) for person in reviewers)
    rendered = []
    for person, name in people:
        label = "@" + approved[person] if person in approved else _cell(name, 100)
        if label not in rendered:
            rendered.append(label)
    return "Reviewers: " + ", ".join(rendered) if rendered else ""


def intro_message(room_name: str) -> str:
    """Deterministic introduction without an active agent mention."""
    return (f"I'm Premise, the decision memory for {_cell(room_name, 100)}. "
            "I check proposed config changes against recorded decisions and listen for evidence. "
            "Never merge; a human approves.\n" + HELP + "\ndeterministic")


def room_join_intro(room_id: str, data: Mapping[str, Any], *, agent_id: str,
                    room_name: str, seen: set[str]) -> Reply | None:
    """Call from on_room_join(room_id, data); caller owns the per-run seen set.

    Reserve before posting for at-most-once attempts. Pass a persisted set if
    introductions should also be suppressed across process restarts.
    """
    is_self = (bool(agent_id) and data.get("member") == agent_id) or data.get("member_name") == "premise"
    if not is_self or room_id in seen:
        return None
    seen.add(room_id)
    return Reply(intro_message(room_name))


async def related_rooms_line(gateway: RoomGateway, room_id: str) -> str:
    """Only disclose linked rooms the current agent belongs to."""
    try:
        related = []
        for room in await gateway.linked_rooms(room_id):
            if room.is_member is not True:
                continue
            part = f"#{_cell(room.target_room_name, 100)} ({_cell(room.label, 100)})"
            if part not in related and len("Related: " + ", ".join([*related, part])) <= 1000:
                related.append(part)
        return "Related: " + ", ".join(related) if related else ""
    except Exception as exc:
        log.warning("linked_rooms_failed exception_type=%s", type(exc).__name__)
        return ""
