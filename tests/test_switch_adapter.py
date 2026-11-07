"""The wiring between room messages, Answer and Listen. No FalkorDB or Switch needed."""
import asyncio

from premise_switch.adapter import UNAVAILABLE, Adapter
from premise_switch.contracts import ProposalNotice, Reply
from switch_fakes import FakeGateway, message


class Echo:
    def __init__(self, error=None):
        self.error = error

    def respond(self, m):
        if self.error:
            raise self.error
        return Reply("ok", m.thread_id or m.message_id) if m.addressed else None


class FakeListener:
    def __init__(self, notices=(), hold=None, record_error=None, run_error=None):
        self.records, self.runs = [], 0
        self.notices, self.hold = list(notices), hold
        self.record_error, self.run_error = record_error, run_error

    def record(self, m):
        if self.record_error:
            raise self.record_error
        self.records.append(m.message_id)

    def run_once(self):
        self.runs += 1
        if self.hold:
            self.hold.wait(timeout=5)
        if self.run_error:
            raise self.run_error
        out, self.notices = self.notices, []
        return out


def run(coro):
    return asyncio.run(coro)


def test_addressed_message_is_answered_in_its_thread():
    async def go():
        gw = FakeGateway()
        await Adapter(Echo(), gateway=gw).on_message(message("q", message_id="m-9"))
        return gw.posts
    assert run(go()) == [("room-1", Reply("ok", "m-9"))]


def test_unaddressed_message_is_recorded_but_not_answered():
    async def go():
        gw, li = FakeGateway(), FakeListener()
        a = Adapter(Echo(), li, gw, debounce_seconds=0)
        await a.on_message(message("chat", addressed=False))
        await asyncio.sleep(0.05)
        await a.aclose()
        return gw.posts, li.records
    posts, records = run(go())
    assert posts == [] and records == ["m-1"]


def test_responder_crash_says_unchecked_and_never_stays_silent():
    async def go():
        gw = FakeGateway()
        await Adapter(Echo(RuntimeError("boom")), gateway=gw).on_message(message("check x = 1"))
        return gw.posts
    (_, reply), = run(go())
    assert reply.body == UNAVAILABLE and "unchecked" in reply.body


def test_responder_crash_mentions_only_the_asking_agent():
    async def go():
        gw = FakeGateway()
        await Adapter(Echo(RuntimeError("boom")), gateway=gw).on_message(
            message("check x = 1", sender_is_agent=True, sender_handle="coder.razeef"))
        return gw.posts
    (_, reply), = run(go())
    assert reply.mention == ("coder.razeef",)


def test_responder_crash_on_unaddressed_message_stays_silent():
    async def go():
        gw = FakeGateway()
        await Adapter(Echo(RuntimeError("boom")), gateway=gw).on_message(message("chat", addressed=False))
        return gw.posts
    assert run(go()) == []


def test_post_failure_does_not_raise():
    class Broken(FakeGateway):
        async def post(self, room_id, reply):
            raise OSError("down")

    async def go():
        await Adapter(Echo(), gateway=Broken()).on_message(message("q"))
    run(go())


def test_listen_failures_never_block_the_answer():
    async def go():
        gw = FakeGateway()
        await Adapter(Echo(), FakeListener(record_error=ValueError("bad")), gw).on_message(message("q"))
        return gw.posts
    assert len(run(go())) == 1


def test_burst_of_messages_runs_the_pipeline_once_and_posts_the_notice():
    async def go():
        gw = FakeGateway()
        li = FakeListener([ProposalNotice("room-1", "fact=72", "proposal")])
        a = Adapter(Echo(), li, gw, debounce_seconds=0.05)
        for i in range(3):
            await a.on_message(message("chat", message_id=f"m-{i}", addressed=False))
        await asyncio.sleep(0.2)
        await a.aclose()
        return gw.posts, li
    posts, li = run(go())
    assert li.runs == 1 and li.records == ["m-0", "m-1", "m-2"]
    assert posts == [("room-1", Reply("proposal"))]


def test_pipeline_failure_is_contained():
    async def go():
        gw = FakeGateway()
        a = Adapter(Echo(), FakeListener(run_error=RuntimeError("graph")), gw, debounce_seconds=0)
        await a.on_message(message("chat", addressed=False))
        await asyncio.sleep(0.05)
        await a.aclose()
        return gw.posts
    assert run(go()) == []


def test_new_message_during_a_run_gets_another_run_and_no_overlap():
    import threading
    hold = threading.Event()

    async def go():
        li = FakeListener(hold=hold)
        a = Adapter(Echo(), li, FakeGateway(), debounce_seconds=0.01)
        await a.on_message(message("one", message_id="a", addressed=False))
        await asyncio.sleep(0.05)
        assert li.runs == 1                 # first run is in progress
        await a.on_message(message("two", message_id="b", addressed=False))
        await asyncio.sleep(0.05)
        assert li.runs == 1                 # no overlap while it holds
        hold.set()
        await asyncio.sleep(0.1)
        await a.aclose()
        return li.runs
    assert run(go()) == 2


def test_addressed_commands_are_never_recorded_as_evidence():
    async def go():
        li = FakeListener()
        a = Adapter(Echo(), li, FakeGateway(), debounce_seconds=0)
        await a.on_message(message("@premise Paylane now retries for 72 hours"))
        await asyncio.sleep(0.02)
        await a.aclose()
        return li
    li = run(go())
    assert li.records == [] and li.runs == 0


class Verdict:
    def __init__(self, first):
        self.first = first

    def respond(self, m):
        return Reply(f"{self.first}\n- detail\ndeterministic · graph rev 1", m.message_id)


class LinkedGateway(FakeGateway):
    def __init__(self):
        super().__init__()
        from premise_switch.contracts import LinkedRoom
        self.links["room-1"] = [LinkedRoom("room-2", "payments", "payments decisions", True)]


def test_blocked_reply_points_at_the_linked_room_and_is_ledgered():
    async def go():
        gw = LinkedGateway()
        a = Adapter(Verdict("BLOCKED"), gateway=gw, ledger=True, ledger_flush_seconds=0)
        await a.on_message(message("@premise check a.b = 72"))
        await asyncio.sleep(0.05)
        return gw
    gw = run(go())
    (_, reply), = gw.posts
    assert reply.body.endswith("Related: #payments (payments decisions)")
    ledger = gw.documents[("room-1", "Premise decision ledger")]
    assert "BLOCKED" in ledger and "check a.b = 72" in ledger and "@premise" not in ledger


def test_clear_reply_has_no_related_line_and_ledger_is_off_by_default():
    async def go():
        gw = LinkedGateway()
        await Adapter(Verdict("CLEAR_WITHIN_SCOPE"), gateway=gw).on_message(message("check a.b = 1"))
        return gw
    gw = run(go())
    assert "Related" not in gw.posts[0][1].body and gw.documents == {}


def test_proposal_notice_is_ledgered_after_posting():
    async def go():
        gw = FakeGateway()
        li = FakeListener([ProposalNotice("room-1", "fact=72", "**Proposal:** x 24 → 72.\nmore")])
        a = Adapter(Echo(), li, gw, debounce_seconds=0, ledger=True, ledger_flush_seconds=0)
        await a.on_message(message("chat", addressed=False))
        await asyncio.sleep(0.05)
        await a.aclose()
        return gw
    ledger = run(go()).documents[("room-1", "Premise decision ledger")]
    assert "proposal" in ledger and "fact=72" in ledger


def test_ledger_writes_are_batched_into_one_document_update():
    class Counting(FakeGateway):
        writes = 0

        async def upsert_document(self, *args):
            Counting.writes += 1
            return await super().upsert_document(*args)

    async def go():
        gw = Counting()
        a = Adapter(Verdict("BLOCKED"), gateway=gw, ledger=True, ledger_flush_seconds=0.05)
        for i in range(3):
            await a.on_message(message(f"@premise check a.b = {i}", message_id=f"m-{i}"))
        await asyncio.sleep(0.15)
        return gw
    gw = run(go())
    assert Counting.writes == 1 and gw.documents[("room-1", "Premise decision ledger")].count("BLOCKED") == 3


class Flaky(FakeGateway):
    def __init__(self, failures, status):
        super().__init__()
        self.failures, self.status, self.attempts = failures, status, 0

    async def post(self, room_id, reply):
        import httpx
        self.attempts += 1
        if self.attempts <= self.failures:
            request = httpx.Request("POST", "http://switch/ops/post_message")
            raise httpx.HTTPStatusError("x", request=request, response=httpx.Response(self.status, request=request))
        return await super().post(room_id, reply)


def test_transient_post_failure_is_retried_once():
    async def go():
        gw = Flaky(1, 409)
        a = Adapter(Echo(), gateway=gw)
        a.retry_seconds = 0
        await a.on_message(message("q"))
        return gw
    gw = run(go())
    assert gw.attempts == 2 and len(gw.posts) == 1


def test_refused_post_is_not_retried():
    async def go():
        gw = Flaky(5, 422)
        a = Adapter(Echo(), gateway=gw)
        a.retry_seconds = 0
        await a.on_message(message("q"))
        return gw
    gw = run(go())
    assert gw.attempts == 1 and gw.posts == []


def test_ledger_never_keeps_a_secret_from_a_command():
    async def go():
        gw = FakeGateway()
        a = Adapter(Verdict("NEEDS_REVIEW"), gateway=gw, ledger=True, ledger_flush_seconds=0)
        await a.on_message(message("@premise check service.password = 'synthetic-password-123'"))
        await asyncio.sleep(0.05)
        return gw
    ledger = run(go()).documents[("room-1", "Premise decision ledger")]
    assert "NEEDS_REVIEW" in ledger and "synthetic-password-123" not in ledger
