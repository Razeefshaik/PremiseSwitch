"""Answer/guard regression tests; no database or Switch required."""
import pytest

from premise_switch.router import ParseError, parse


@pytest.mark.parametrize("text,name,args,explicit", [
    ("@premise help", "help", {}, True),
    ("check a.b = 72, c.d = true and e.f = 'x, and y'\ng.h = -1.5e2", "check_change",
     {"changes": [{"fact": "a.b", "value": 72}, {"fact": "c.d", "value": True},
                  {"fact": "e.f", "value": "x, and y"}, {"fact": "g.h", "value": -150.0}]}, True),
    ("config/x.yaml#retry.window_hours = 72", "check_change", {"changes": [
        {"file": "config/x.yaml", "key": "retry.window_hours", "value": 72}]}, True),
    ("who should review webhook?", "ownership", {"query": "webhook"}, True),
    ("who knows ADR-012", "ownership", {"query": "ADR-012"}, True),
    ("who owns webhook", "ownership", {"query": "webhook"}, True),
    ("why ADR-012, and who owns it now?", "decision_lineage", {"id": "ADR-012"}, True),
    ("lineage ADR-007", "decision_lineage", {"id": "ADR-007"}, True),
    ("impact A_RETRY_WINDOW", "impact_if_broken", {"id": "A_RETRY_WINDOW"}, True),
    ("what breaks if retries breaks?", "impact", {"query": "retries"}, True),
    ("tell me about ADR-012", "decision_lineage", {"id": "ADR-012"}, False),
    ("G_DEDUPE", "guarantee_health", {"id": "G_DEDUPE"}, False),
    ("A_RETRY_WINDOW", "impact_if_broken", {"id": "A_RETRY_WINDOW"}, False),
    ("health please", "guarantee_health", {}, False),
    ("departed owners", "orphaned_decisions", {}, False),
    ("something else", "question", {"query": "something else"}, False),
])
def test_routes(text, name, args, explicit):
    route = parse(text)
    assert (route.name, route.arguments, route.explicit) == (name, args, explicit)


@pytest.mark.parametrize("text", ["", "@premise", "check", "a.b = nan", "a.b = inf", "a.b = 1e999",
    "a.b = null", "a.b = word", "a.b = [1]", "a.b = 1 trailing", "a.b = 1,", "a.b = 1 and",
    "a.b = 'unfinished", "../x.yaml#key = 2", "a.b = 1, a.b = 2", "x" * 2001,
    "a.b = truefalse", "a.b = 2; tool = 3", "a.b = 1 " + "a.c = 2",
    ",".join(f"x.k{i} = 1" for i in range(21))])
def test_parse_errors(text):
    with pytest.raises(ParseError):
        parse(text)


from premise_switch.format import format_check, format_result


def blocked_result():
    return {"verdict": "BLOCKED", "graph_rev": 1,
            "not_holding": [{"id": "G_CHARGE_ONCE", "status": "broken",
                             "chain": ["G_CHARGE_ONCE", "G_DEDUPE", "A_RETRY_WINDOW"]}],
            "decisions": [{"id": "ADR-012", "reviewers": ["p_jun", "p_anika"]}],
            "verified_fixes": [{"changes": {"webhook.dedupe_ttl_hours": 72, "redis.max_ttl_hours": 72}}]}


def test_blocked_format_and_pasteable_fix():
    body = format_check(blocked_result(), {"p_jun": "Jun Park", "p_anika": "Anika Rao"})
    assert body.startswith("BLOCKED\n")
    assert "G_CHARGE_ONCE → G_DEDUPE → A_RETRY_WINDOW" in body
    assert "ADR-012" in body and "Jun Park" in body and "Anika Rao" in body
    fix = body.split("```text\n")[1].split("\n```")[0]
    assert parse(fix).arguments == {"changes": [
        {"fact": "webhook.dedupe_ttl_hours", "value": 72}, {"fact": "redis.max_ttl_hours", "value": 72}]}
    assert body.endswith("deterministic · graph rev 1")


def test_format_four_chains_and_overflow_count():
    result = blocked_result()
    result["not_holding"] *= 6
    body = format_check(result)
    assert body.count("- G_CHARGE_ONCE:") == 4
    assert "and 2 more" in body
    assert "p_jun" in body  # Unknown names remain honest person ids.


def test_length_limit_preserves_verdict_footer_and_whole_fix():
    result = blocked_result()
    result["not_holding"] = [{"id": "G_LONG", "status": "broken", "chain": ["A_" + "X" * 70] * 15}] * 10
    body = format_check(result)
    assert len(body) <= 1500 and body.splitlines()[0] == "BLOCKED"
    assert body.endswith("deterministic · graph rev 1")
    assert "webhook.dedupe_ttl_hours = 72" in body and "redis.max_ttl_hours = 72" in body


def test_unknown_and_untracked_never_format_as_clear():
    assert "unchecked" in format_check({"verdict": "ERROR"})
    body = format_check({"verdict": "CLEAR_WITHIN_SCOPE", "reasons": [{"code": "R_UNTRACKED"}]})
    assert body.startswith("NEEDS_REVIEW\n") and "isn't tracked" in body
    assert "CLEAR_WITHIN_SCOPE" not in body


def test_citations_and_departed_owners_and_escaped_graph_names():
    body = format_result("decision_lineage", {
        "decision": {"id": "ADR-012", "title": "@coder.razeef"}, "active_accountable": [],
        "unavailable": ["p_old"], "reviewers": ["p_jun"], "names": {"p_jun": "Jun @coder.razeef"},
        "sources": [{"path": "docs/adr.md", "line": 12, "url": "https://example.com/adr"}]})
    assert "No active accountable owner" in body and "Departed" in body
    assert "docs/adr.md:12" in body and "https://" not in body
    assert "@coder.razeef" not in body and "@\u200bcoder.razeef" in body


def test_fix_alternatives_are_not_combined_or_partially_truncated():
    result = blocked_result()
    result["verified_fixes"].append({"changes": {"other.key": 8}})
    assert "other.key" not in format_check(result)
    result["verified_fixes"] = [{"changes": {"big.key": "x" * 2000}}]
    body = format_check(result)
    assert len(body) <= 1500 and "no partial fix" in body and "big.key =" not in body
