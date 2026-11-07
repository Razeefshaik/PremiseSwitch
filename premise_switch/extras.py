"""Optional Switch presentation helpers; wiring remains with the adapter owner."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
import json
import logging
from typing import Any

from .contracts import RoomGateway
from .format import escape

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

    async def record(self, room_id: str, kind: str, summary: str | Mapping[str, Any]) -> bool:
        """Return success; retain bounded rows for the next attempt on failure."""
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
                await self.gateway.upsert_document(
                    room_id, LEDGER_NAME, "Recent Premise verdicts and proposals, newest first.",
                    "Historical summaries only. Ask for a fresh check before editing config; a human approves merges.",
                    _HEADER + "".join(rows))
            return True
        except Exception as exc:
            log.warning("ledger_failed exception_type=%s", type(exc).__name__)
            return False
