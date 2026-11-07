"""Canonical guard/answer scenarios, using the real kestrel FalkorDB fixture."""
from premise.graph.tools import GraphTools
from premise_switch.answer import Answerer
from premise_switch.router import parse
from conftest import NOW
from switch_fakes import message


def test_retry_window_blocked_then_verified_fix_clear(kestrel):
    answerer = Answerer(GraphTools(kestrel, at_ms=NOW))
    blocked = answerer.respond(message("check paylane.retry_window_hours = 72"))
    assert blocked.body.startswith("BLOCKED\n")
    assert "G_CHARGE_ONCE → G_DEDUPE → A_RETRY_WINDOW" in blocked.body
    assert "Jun Park" in blocked.body and "Anika Rao" in blocked.body
    assert all(decision in blocked.body for decision in ("ADR-007", "ADR-012", "ADR-015", "ADR-018"))
    fix = blocked.body.split("```text\n")[1].split("\n```")[0]
    assert {item["fact"]: item["value"] for item in parse(fix).arguments["changes"]} == {
        "webhook.dedupe_ttl_hours": 72, "redis.max_ttl_hours": 72}
    clear = answerer.respond(message("check paylane.retry_window_hours = 72\n" + fix))
    assert clear.body.startswith("CLEAR_WITHIN_SCOPE\n")
    assert len(blocked.body) <= 1500 and len(clear.body) <= 1500
    print("\nBLOCKED REPLY:\n" + blocked.body + "\nCLEAR REPLY:\n" + clear.body)


def test_adr_012_departed_owners_and_sources(kestrel):
    reply = Answerer(GraphTools(kestrel, at_ms=NOW)).respond(message("why ADR-012, and who owns it now?"))
    assert "ADR-012" in reply.body
    assert "No active accountable owner" in reply.body and "Departed" in reply.body
    assert "Jun Park" in reply.body and "Anika Rao" in reply.body
    assert "Sources:" in reply.body and "https://" not in reply.body
