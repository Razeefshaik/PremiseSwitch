"""Offline checks for the C07 optional Switch helpers."""
import asyncio

import pytest

from premise_switch.extras import DecisionLedger, LEDGER_MAX_BYTES, LEDGER_NAME
from switch_fakes import FakeGateway


class DocumentGateway(FakeGateway):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.fail = False

    async def upsert_document(self, room_id, name, description, instructions, content):
        if self.fail:
            raise RuntimeError("secret-token and private room text")
        key = (room_id, name)
        self.calls.append(("update" if key in self.documents else "create", key))
        return await super().upsert_document(room_id, name, description, instructions, content)


@pytest.mark.asyncio
async def test_ledger_creates_then_updates_newest_first():
    gateway = DocumentGateway()
    ledger = DecisionLedger(gateway)
    assert await ledger.record("r1", "verdict", {
        "verdict": "BLOCKED", "changes": {"paylane.retry_window_hours": 72}, "thread_id": "t1"})
    assert await ledger.record("r1", "proposal", {"summary": "Retry window proposal", "changes": "24 -> 72", "thread_id": "t2"})
    assert gateway.calls == [("create", ("r1", LEDGER_NAME)), ("update", ("r1", LEDGER_NAME))]
    assert len(gateway.documents) == 1
    text = gateway.documents["r1", LEDGER_NAME]
    assert text.index("Retry window proposal") < text.index("BLOCKED")
    assert '"paylane.retry_window_hours": 72' in text
    assert "t1" in text and "t2" in text and "Time (UTC)" in text
    await ledger.record("r2", "verdict", "separate room")
    assert "separate room" not in gateway.documents["r1", LEDGER_NAME]


@pytest.mark.asyncio
async def test_ledger_row_and_utf8_byte_caps():
    gateway = FakeGateway()
    ledger = DecisionLedger(gateway)
    for n in range(105):
        await ledger.record("r", "proposal", f"change-{n:03}")
    text = gateway.documents["r", LEDGER_NAME]
    assert len(text.splitlines()) == 102
    assert "change-000" not in text and "change-005" in text
    for _ in range(100):
        await ledger.record("r", "proposal", "\U0001f9e0" * 100_000)
    text = gateway.documents["r", LEDGER_NAME]
    assert len(text.encode("utf-8")) <= LEDGER_MAX_BYTES
    assert len(text.splitlines()) > 2


@pytest.mark.asyncio
async def test_ledger_failure_is_type_only_and_recovers(caplog):
    gateway = DocumentGateway()
    ledger = DecisionLedger(gateway)
    gateway.fail = True
    assert not await ledger.record("r", "verdict", {"verdict": "BLOCKED", "thread_id": "thread"})
    assert "RuntimeError" in caplog.text
    assert "secret-token" not in caplog.text and "private room text" not in caplog.text
    gateway.fail = False
    assert await ledger.record("r", "proposal", "next change")
    assert "BLOCKED" in gateway.documents["r", LEDGER_NAME]


@pytest.mark.asyncio
async def test_ledger_concurrency_and_untrusted_cells():
    gateway = DocumentGateway()
    ledger = DecisionLedger(gateway)
    await asyncio.gather(*(ledger.record("r", "proposal", {"summary": f"row-{n}", "changes": "@coder | <b>\ntext"}) for n in range(10)))
    text = gateway.documents["r", LEDGER_NAME]
    assert len(gateway.documents) == 1 and len(text.splitlines()) == 12
    assert "@coder" not in text and "&#124;" in text and "&lt;b&gt;" in text
