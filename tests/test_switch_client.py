"""C02 protocol tests against an actual local HTTP/WebSocket server."""
from __future__ import annotations

import json
import os
import stat

import pytest

from premise_switch.client import register_once
from premise_switch.state import StateStore
from tests.fake_switch import FakeSwitch


def test_state_preserves_live_identity_and_hides_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SWITCH_STATE_DIR", str(tmp_path))
    (tmp_path / "state.json").write_text(json.dumps({"agent_id": "existing", "api_key": "secret"}))
    state = StateStore()
    connection = state.room("r")["connection_id"]
    assert "secret" not in repr(state)
    loaded = StateStore(tmp_path)
    assert (loaded.agent_id, loaded.api_key) == ("existing", "secret")
    assert loaded.room("r")["connection_id"] == connection
    if os.name != "nt":
        assert stat.S_IMODE(state.path.stat().st_mode) == 0o600


def test_bounded_dedupe_and_monotonic_cursor(tmp_path):
    state = StateStore(tmp_path)
    for n in range(2002):
        assert state.remember("r", str(n))
    assert len(state.room("r")["recent_message_ids"]) == 2000
    assert not state.remember("r", "2001")
    assert "0" not in state.room("r")["recent_message_ids"]
    state.advance("r", 10)
    state.advance("r", 3)
    assert StateStore(tmp_path).room("r")["cursor"] == 10
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_failure_preserves_previous_file(tmp_path, monkeypatch):
    state = StateStore(tmp_path)
    state.set_agent("existing", "secret")
    before = state.path.read_bytes()

    def fail_replace(*args):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        state.set_agent("new", "other")
    assert state.path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_registration_once_without_settings_calls(tmp_path):
    with FakeSwitch() as server:
        state = StateStore(tmp_path)
        await register_once(server.url, "fake-registration-secret", state)
        await register_once(server.url, "unused", StateStore(tmp_path))
        assert [r["operation"] for r in server.requests] == ["register"]
        assert state.agent_id == "agent-1"
        assert state.api_key == "fake-agent-secret"
        assert "overwrite" not in server.requests[0]["arguments"]


@pytest.mark.asyncio
async def test_conflict_requires_owner_recovery(tmp_path):
    with FakeSwitch() as server:
        server.conflict = True
        state = StateStore(tmp_path)
        with pytest.raises(RuntimeError, match="Restore the owner's existing state.json"):
            await register_once(server.url, "fake-registration-secret", state)
        assert not state.agent_id
        assert len(server.requests) == 1
        assert "overwrite" not in server.requests[0]["arguments"]
