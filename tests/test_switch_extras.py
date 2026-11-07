"""Offline checks for the C07 optional Switch helpers."""
import asyncio

import pytest

from premise_switch.extras import DecisionLedger, LEDGER_MAX_BYTES, LEDGER_NAME
from premise_switch.extras import intro_message, related_rooms_line, reviewer_mentions, room_join_intro
from premise_switch.contracts import LinkedRoom
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


def test_reviewers_only_mention_allowlisted_humans(monkeypatch):
    monkeypatch.setenv("SWITCH_REVIEWER_HANDLES", "p_jun=jun.park,p_anika=anika.rao")
    assert reviewer_mentions(["p_jun", "p_anika"]) == "Reviewers: @jun.park, @anika.rao"
    text = reviewer_mentions({"p_jun": "Jun Park", "p_unknown": "Unknown Human", "agent": "@coder"})
    assert text == "Reviewers: @jun.park, Unknown Human, @\u200bcoder"
    assert "@coder" not in text
    assert reviewer_mentions([], "") == ""
    assert reviewer_mentions(["p_jun", "p_jun"], {"p_jun": "jun.park"}) == "Reviewers: @jun.park"


@pytest.mark.parametrize("handle", ["@coder", "coder\n@everyone", "channel", "all", "here", "premise", "<@coder>"])
def test_reviewers_reject_malformed_and_broadcast_handles(handle):
    assert reviewer_mentions({"p": "Person"}, {"p": handle}) == "Reviewers: Person"


def test_intro_once_per_room_and_both_self_identifiers():
    seen = set()
    assert room_join_intro("r1", {"member": "other", "member_name": "coder"},
                           agent_id="premise-id", room_name="dev", seen=seen) is None
    assert seen == set()
    first = room_join_intro("r1", {"member": "premise-id", "member_name": "different"},
                            agent_id="premise-id", room_name="dev", seen=seen)
    assert first.body == intro_message("dev") and first.mention == () and first.thread_id is None
    assert room_join_intro("r1", {"member_name": "premise"}, agent_id="premise-id", room_name="dev", seen=seen) is None
    assert room_join_intro("r2", {"member": "transport-id", "member_name": "premise"},
                           agent_id="premise-id", room_name="payments", seen=seen) is not None
    assert seen == {"r1", "r2"}
    body = intro_message("dev @coder")
    assert "@coder" not in body
    for command in ("help", "check fact.key", "who knows", "why ADR-012", "lineage", "impact"):
        assert command in body


@pytest.mark.asyncio
async def test_related_rooms_members_only_and_failures(caplog):
    gateway = FakeGateway()
    assert await related_rooms_line(gateway, "dev") == ""
    gateway.links["dev"] = [LinkedRoom("p", "payments", "payments decisions", True),
                             LinkedRoom("secret", "private-finance", "secret label", False)]
    assert await related_rooms_line(gateway, "dev") == "Related: #payments (payments decisions)"
    gateway.links["dev"] = [LinkedRoom("p", "@coder", "@other", True)]
    text = await related_rooms_line(gateway, "dev")
    assert "@coder" not in text and "@other" not in text

    async def fail(room_id):
        raise ValueError("secret-token and private room text")
    gateway.linked_rooms = fail
    assert await related_rooms_line(gateway, "dev") == ""
    assert "ValueError" in caplog.text and "secret-token" not in caplog.text
