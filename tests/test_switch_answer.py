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
