"""C02 protocol tests against an actual local HTTP/WebSocket server."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
from contextlib import asynccontextmanager

import pytest

from premise_switch.client import SwitchClient, register_once
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
    state.room("r")["recent_message_ids"] = [str(n) for n in range(1999)]
    for n in range(1999, 2002):
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


async def eventually(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@asynccontextmanager
async def running_client(server, tmp_path, callback, **kwargs):
    state = StateStore(tmp_path)
    if not state.agent_id:
        state.set_agent("agent-1", "fake-agent-secret")
    client = SwitchClient(server.url, state, callback, room_poll_seconds=0.05, **kwargs)
    task = asyncio.create_task(client.run())
    try:
        await eventually(lambda: bool(server.sockets) and all(state.room(r)["generation"] for r in server.sockets))
        yield client
    finally:
        await client.stop()
        await asyncio.wait_for(task, 3)


@pytest.mark.asyncio
async def test_two_rooms_have_distinct_stable_connections_and_poll_changes(tmp_path):
    with FakeSwitch() as server:
        await server.add_room("room-2")
        async with running_client(server, tmp_path, lambda m: None) as client:
            await eventually(lambda: len(server.sockets) == 2)
            connections = {q["rooms"]: q["connection_id"] for q in server.connections}
            assert len(set(connections.values())) == 2
            await asyncio.sleep(0.15)
            assert len(server.connections) == 2
            await server.add_room("room-3")
            await eventually(lambda: "room-3" in server.sockets)
            await server.remove_room("room-2")
            await eventually(lambda: "room-2" not in server.sockets)
            await server.add_room("room-2")
            await eventually(lambda: "room-2" in server.sockets)
            assert server.connections[-1]["connection_id"] == connections["room-2"]
            assert not client._done.is_set()


@pytest.mark.asyncio
async def test_slow_async_handler_does_not_delay_pongs_or_reorder_messages(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def handler(message):
        seen.append(message.message_id)
        entered.set()
        await release.wait()

    with FakeSwitch() as server:
        async with running_client(server, tmp_path, handler) as client:
            await server.send_frame("room-1", server.message())
            await entered.wait()
            await server.send_frame("room-1", server.message("m-2"))
            await asyncio.sleep(server.heartbeat_timeout * 2)
            assert seen == ["m-1"]
            assert len(server.connections) == 1
            assert len(server.pongs) >= 3
            assert all(p["cursor"] == 0 for p in server.pongs)
            times = [p["time"] for p in server.pongs]
            assert max(b - a for a, b in zip(times, times[1:])) < server.heartbeat_timeout
            release.set()
            await eventually(lambda: seen == ["m-1", "m-2"])
            await eventually(lambda: client.state.room("room-1")["cursor"] == 2)


@pytest.mark.asyncio
async def test_synchronous_handler_runs_off_event_loop(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def handler(message):
        entered.set()
        release.wait(timeout=3)

    with FakeSwitch() as server:
        async with running_client(server, tmp_path, handler):
            try:
                await server.send_frame("room-1", server.message())
                await eventually(entered.is_set)
                await asyncio.sleep(server.heartbeat_timeout * 2)
                assert len(server.pongs) >= 3 and len(server.connections) == 1
            finally:
                release.set()


@pytest.mark.asyncio
async def test_replay_dedupes_across_restart_and_preserves_connection(tmp_path):
    seen = []
    with FakeSwitch() as server:
        frame = server.message()
        async with running_client(server, tmp_path, seen.append) as client:
            await server.send_frame("room-1", frame)
            await server.replay("room-1", frame)
            await eventually(lambda: client.state.room("room-1")["cursor"] == 1)
            connection = client.state.room("room-1")["connection_id"]
            generation = client.state.room("room-1")["generation"]
        async with running_client(server, tmp_path, seen.append):
            assert server.connections[-1]["connection_id"] == connection
            assert server.connections[-1]["expected_generation"] == str(generation)
            assert server.connections[-1]["start_from"] == "1"
            await server.replay("room-1", frame)
            await server.send_frame("room-1", server.message("m-2"))
            await eventually(lambda: len(seen) == 2)
            assert [m.message_id for m in seen] == ["m-1", "m-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("eviction", [None, "heartbeat_lapsed", "no_stream", "unfenced", "connection_error"])
async def test_reconnect_uses_saved_identity_cursor_and_generation(tmp_path, eviction):
    with FakeSwitch() as server:
        async with running_client(server, tmp_path, lambda m: None) as client:
            frame = server.message()
            await server.send_frame("room-1", frame)
            await eventually(lambda: client.state.room("room-1")["cursor"] == 1)
            generation = client.state.room("room-1")["generation"]
            if eviction:
                await server.evict("room-1", eviction)
            else:
                await server.drop_socket("room-1")
            await eventually(lambda: len(server.connections) == 2)
            assert server.connections[1]["connection_id"] == server.connections[0]["connection_id"]
            assert server.connections[1]["start_from"] == "1"
            assert server.connections[1]["expected_generation"] == str(generation)


@pytest.mark.asyncio
@pytest.mark.parametrize("eviction", ["taken_over", "closed"])
async def test_final_eviction_does_not_reconnect_even_after_restart(tmp_path, eviction):
    with FakeSwitch() as server:
        await server.add_room("room-2")
        async with running_client(server, tmp_path, lambda m: None) as client:
            await eventually(lambda: len(server.sockets) == 2)
            await server.evict("room-1", eviction)
            await eventually(lambda: client.state.room("room-1").get("halted") == eviction)
            await asyncio.sleep(2.1)
            assert len([q for q in server.connections if q["rooms"] == "room-1"]) == 1
            assert "room-2" in server.sockets
        async with running_client(server, tmp_path, lambda m: None):
            await asyncio.sleep(0.15)
            assert len([q for q in server.connections if q["rooms"] == "room-1"]) == 1


@pytest.mark.asyncio
async def test_sender_cache_refresh_self_filter_and_join_callback(tmp_path):
    seen, joins = [], []
    with FakeSwitch() as server:
        async with running_client(server, tmp_path, seen.append,
                                  on_room_join=lambda room, data: joins.append((room, data))):
            for sender in ["agent-1", "premise", "user-1", "razeef", "coder-1", "unknown"]:
                await server.send_frame("room-1", server.message(sender, sender=sender))
            await eventually(lambda: len(seen) == 4)
            assert [(m.sender_handle, m.sender_is_agent) for m in seen] == [
                ("razeef", False), ("razeef", False), ("coder.razeef", True), ("unknown", True)]
            assert all(m.room_name == "payments" for m in seen)
            initial_calls = len([r for r in server.requests if r["operation"] == "list_participants"])
            server.participants["room-1"].append({"id": "new-user", "name": "anika", "type": "user"})
            await server.send_frame("room-1", server.message("new", sender="new-user"))
            await eventually(lambda: len(seen) == 5)
            assert seen[-1].sender_handle == "anika" and not seen[-1].sender_is_agent
            assert len([r for r in server.requests if r["operation"] == "list_participants"]) == initial_calls + 1
            join = {"member": "new-user", "member_name": "Anika", "timestamp": 1, "listening": True}
            await server.send_frame("room-1", {"event": "room_join", "data": join})
            await eventually(lambda: bool(joins))
            assert joins == [("room-1", join)]


@pytest.mark.asyncio
async def test_handler_failure_logs_only_type_and_keeps_processing(tmp_path, caplog):
    seen = []

    async def handler(message):
        seen.append(message.message_id)
        if message.message_id == "m-1":
            raise ValueError("private room text fake-agent-secret fake-registration-secret")

    with FakeSwitch() as server:
        async with running_client(server, tmp_path, handler):
            await server.send_frame("room-1", server.message())
            await server.send_frame("room-1", server.message("m-2"))
            await eventually(lambda: len(seen) == 2)
            assert len(server.connections) == 1
    assert "ValueError" in caplog.text
    for secret in ["private room text", "fake-agent-secret", "fake-registration-secret"]:
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_refused_connection_stops_retrying(tmp_path):
    with FakeSwitch() as server:
        server.refusal = 409
        state = StateStore(tmp_path)
        state.set_agent("agent-1", "fake-agent-secret")
        client = SwitchClient(server.url, state, lambda m: None, room_poll_seconds=0.05)
        task = asyncio.create_task(client.run())
        try:
            await eventually(lambda: state.room("room-1").get("halted") == "refused")
            await asyncio.sleep(2.1)
            assert len(server.connections) == 1
        finally:
            await client.stop()
            await task


def test_backoff_is_jittered_and_bounded():
    first = {SwitchClient._retry_delay(0) for _ in range(10)}
    assert len(first) > 1 and all(1 <= d <= 2 for d in first)
    assert all(1 <= SwitchClient._retry_delay(n) <= 30 for n in [1, 4, 9, 100])


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
