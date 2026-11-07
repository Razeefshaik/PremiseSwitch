"""Atomic, private Switch credentials and replay state (never room text)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4


class StateStore:
    RECENT_LIMIT = 2000

    def __init__(self, dir: str | Path | None = None):
        self.directory = Path(dir if dir is not None else os.environ.get("SWITCH_STATE_DIR", ".runtime/switch"))
        self.path = self.directory / "state.json"
        self._data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self._data.setdefault("rooms", {})

    def __repr__(self) -> str:
        return f"StateStore(agent_id={self.agent_id!r}, api_key=<redacted>)"

    @property
    def agent_id(self) -> str | None:
        return self._data.get("agent_id")

    @property
    def api_key(self) -> str | None:
        return self._data.get("api_key")

    def set_agent(self, agent_id: str, api_key: str) -> None:
        self._data.update(agent_id=agent_id, api_key=api_key)
        self.save()

    def room(self, room_id: str) -> dict[str, Any]:
        rooms = self._data["rooms"]
        if room_id not in rooms:
            rooms[room_id] = {"connection_id": str(uuid4()), "generation": None,
                              "cursor": None, "recent_message_ids": [], "documents": {}}
            self.save()
        return rooms[room_id]

    def remember(self, room_id: str, message_id: str) -> bool:
        """Durably reserve an id before invoking code that might send a reply."""
        recent = self.room(room_id)["recent_message_ids"]
        if message_id in recent:
            return False
        recent.append(message_id)
        del recent[:-self.RECENT_LIMIT]
        self.save()
        return True

    def advance(self, room_id: str, cursor: int | None) -> None:
        room = self.room(room_id)
        if cursor is not None and (room["cursor"] is None or cursor > room["cursor"]):
            room["cursor"] = cursor
            self.save()

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                if os.name != "nt":
                    os.fchmod(file.fileno(), 0o600)
                json.dump(self._data, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
