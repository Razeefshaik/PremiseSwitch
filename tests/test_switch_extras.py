"""Offline checks for the C07 optional Switch helpers."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from premise_switch.extras import DecisionLedger, LEDGER_MAX_BYTES, LEDGER_NAME
from premise_switch.extras import intro_message, related_rooms_line, reviewer_mentions, room_join_intro
from premise_switch.contracts import LinkedRoom
from switch_fakes import FakeGateway
from demo import seed_payments as seed

ROOT = Path(__file__).resolve().parents[1]


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


def test_seed_cli_dry_run_never_constructs_http_client(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("dry-run attempted network access")
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setenv("MM_TOKEN_1", "secret-token-one")
    monkeypatch.setenv("MM_TOKEN_2", "secret-token-two")
    assert seed.main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    for _, message in seed.MESSAGES:
        assert message in output
    assert "secret-token" not in output
    result = subprocess.run([sys.executable, "demo/seed_payments.py", "--dry-run"],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    assert "DRY RUN person 2" in result.stdout and "secret-token" not in result.stdout


def test_seed_posts_top_level_messages_with_correct_tokens_and_delays(capsys):
    calls, pauses = [], []
    env = {"MM_URL": "http://mattermost.test/", "MM_CHANNEL_ID": "payments-id",
           "MM_TOKEN_1": "secret-token-one", "MM_TOKEN_2": "secret-token-two"}

    def handle(request):
        calls.append(request)
        return httpx.Response(201, json={"id": "post-id"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert seed.seed_payments(environ=env, http_client=client, sleep=pauses.append) == 3
    assert pauses == [3, 3]
    for request, (person, message) in zip(calls, seed.MESSAGES, strict=True):
        assert request.method == "POST" and str(request.url) == "http://mattermost.test/api/v4/posts"
        assert request.headers["Authorization"] == "Bearer " + env[f"MM_TOKEN_{person}"]
        assert json.loads(request.content) == {"channel_id": "payments-id", "message": message}
        assert "root_id" not in json.loads(request.content)
    assert "secret-token" not in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["http", "transport"])
def test_seed_failure_output_never_exposes_tokens(monkeypatch, capsys, mode):
    def handle(request):
        if mode == "transport":
            raise httpx.ConnectError("secret-token-one secret-token-two", request=request)
        return httpx.Response(403, text="secret-token-one secret-token-two")
    env = {"MM_URL": "http://mattermost.test", "MM_CHANNEL_ID": "payments-id",
           "MM_TOKEN_1": "secret-token-one", "MM_TOKEN_2": "secret-token-two"}
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    client_class = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(handle)))
    assert seed.main([]) == 1
    output = capsys.readouterr()
    assert "Seed failed" in output.err and "secret-token" not in output.out + output.err


def test_seed_missing_environment_does_not_post(monkeypatch, capsys):
    for name in seed.NEEDED_NAMES:
        monkeypatch.delenv(name, raising=False)
    def forbidden(**kwargs):
        pytest.fail("invalid environment constructed a client")
    monkeypatch.setattr(httpx, "Client", forbidden)
    assert seed.main([]) == 1
    assert "ValueError" in capsys.readouterr().err


def test_seed_sentences_extract_grounded_claims_with_bundled_aliases():
    from premise_v2.envelope import Envelope
    from premise_v2.extract import FactClaimExtractor, ground
    from premise_v2.registry import Ext

    ext = Ext.load(ROOT / "sandbox/kestrel-platform")
    extractor = FactClaimExtractor(ext)
    for n, (person, text) in enumerate(seed.MESSAGES):
        env = Envelope(entity_key=f"switch:demo-{n}", source="switch", native_id=f"demo-{n}",
                       sequence=n, kind="message", text=text, time=datetime.now(timezone.utc))
        claims, skipped = extractor.extract(env, f"demo-person-{person}")
        assert not skipped and len(claims) == 1
        claim = claims[0]
        assert claim.fact == "paylane.retry_window_hours"
        assert claim.value == (72 if n < 2 else 999)
        assert claim.status == ("asserted" if n < 2 else "instruction")
        if n < 2:
            assert ground(claim, text, ext)[0] is True
