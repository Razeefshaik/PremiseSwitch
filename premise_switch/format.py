"""Small, conservative Markdown summaries of exact tool rows."""
from __future__ import annotations

import json
import re
from typing import Any

LIMIT = 1500
UNAVAILABLE = "Premise is unavailable right now, so this change is unchecked"
NOT_FOUND = "This topic wasn't found in the graph. This doesn't establish that nothing is affected."
HELP = ("Commands: help; check fact.key = value (commas, and, or newlines); "
        "check config/x.yaml#key = value; who knows/owns/should review X; "
        "why ADR-012; lineage ADR-012; impact A_RETRY_WINDOW; what breaks if X breaks. "
        "Values: finite numbers, true/false, or quoted strings.")


def escape(text: Any) -> str:
    return str(text).replace("@", "@\u200b")


def _text(value: Any, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    text = re.sub(r"(?:https?|ftp)://\S+|www\.\S+", "[link omitted]", text, flags=re.I)
    text = text.replace("`", "'").replace("<", "&lt;").replace(">", "&gt;")
    return escape(text[:limit] + ("…" if len(text) > limit else ""))


def _finish(lines: list[str], revision: Any = None, tail: str = "") -> str:
    footer = f"deterministic · graph rev {_text(revision if revision is not None else 'unknown', 24)}"
    reserved = "\n" + (tail + "\n" if tail else "") + footer
    budget = LIMIT - len(reserved)
    body = "\n".join(lines)
    if len(body) > budget:
        # Keep whole Markdown lines and the verdict; never cut a chain or scalar.
        kept = []
        for line in lines:
            if len("\n".join(kept + [line])) > budget - 24:
                break
            kept.append(line)
        body = "\n".join(kept + ["… additional rows omitted"])
    return body + reserved


def _citations(rows: Any) -> list[str]:
    sources = list(rows.get("sources", [])) if isinstance(rows, dict) else []
    if isinstance(rows, list):
        for row in rows:
            sources.extend(row.get("citations", []))
            if row.get("path"):
                sources.append(row)
    refs = []
    for source in sources:
        path, line = source.get("path"), source.get("line")
        if path and isinstance(line, int) and line > 0 and not re.search(r"://|www\.", path, re.I):
            ref = f"{_text(path, 180)}:{line}"
            if ref not in refs:
                refs.append(ref)
    return ["Sources: " + "; ".join(refs[:4])] if refs else []


def _people(ids: list[str], names: dict[str, str]) -> str:
    return ", ".join(_text(names.get(person, person), 70) for person in dict.fromkeys(ids)) or "No active reviewer found"


def format_check(result: dict[str, Any], names: dict[str, str] | None = None) -> str:
    revision = result.get("graph_rev")
    verdict = result.get("verdict")
    if verdict not in {"BLOCKED", "NEEDS_REVIEW", "CLEAR_WITHIN_SCOPE"}:
        return _finish([UNAVAILABLE], revision)
    if any(reason.get("code") == "R_UNTRACKED" for reason in result.get("reasons", [])):
        # A mixed tracked/untracked check cannot acquire an apparent clear verdict.
        verdict = "BLOCKED" if verdict == "BLOCKED" else "NEEDS_REVIEW"
        return _finish([verdict, "At least one fact isn't tracked, so this change is unchecked.",
                        "Tracked results do not establish the impact of the entire change."], revision)
    lines = [verdict]
    bad = result.get("not_holding", [])
    for guarantee in bad[:4]:
        chain = " → ".join(_text(node, 70) for node in guarantee.get("chain", []))
        lines.append(f"- {_text(guarantee['id'], 70)}: {_text(guarantee['status'], 30)}" + (f" — {chain}" if chain else ""))
    if len(bad) > 4:
        lines.append(f"and {len(bad) - 4} more")
    if verdict == "CLEAR_WITHIN_SCOPE":
        lines.append("Every affected guarantee holds within the tracked scope. Untracked behavior is not assessed.")
    elif verdict == "NEEDS_REVIEW":
        lines.append("Resolve missing or unverified evidence before applying this change.")
    decisions = result.get("decisions", [])
    lines.append("Affected decisions: " + (", ".join(_text(d['id'], 70) for d in decisions) or "none returned"))
    reviewers = [person for decision in decisions for person in decision.get("reviewers", [])]
    lines.append("Reviewers: " + _people(reviewers, names or result.get("names", {})))
    tail = ""
    fixes = result.get("verified_fixes", [])
    if fixes:
        # Offer one whole alternative. Never mix separate verified alternatives.
        fix = fixes[0]["changes"]
        assignments = [f"{key} = {json.dumps(value, ensure_ascii=False, allow_nan=False)}" for key, value in fix.items()]
        fix_text = escape("Verified fix (include the original proposed changes too):\n```text\n" + "\n".join(assignments) + "\n```")
        tail = fix_text if len(fix_text) <= 650 else "Verified fix exceeds reply space; no partial fix displayed."
    lines.extend(_citations(result))
    return _finish(lines, revision, tail)


def format_result(name: str, rows: Any) -> str:
    if not rows or isinstance(rows, dict) and rows.get("outcome") == "unknown_entity":
        return _finish([NOT_FOUND])
    if name == "check_change":
        return format_check(rows)
    lines = []
    if name == "decision_lineage":
        decision = rows.get("decision")
        if not decision:
            return _finish([NOT_FOUND])
        lines.append(f"{_text(decision['id'])}: {_text(decision.get('title', ''))}")
        lines.append(f"Lifecycle: {_text(decision.get('status', 'unknown'))}")
        if not rows.get("active_accountable"):
            lines.append("No active accountable owner. Departed or unavailable owners: " +
                         _people(rows.get("unavailable", []), rows.get("names", {})))
        else:
            lines.append("Active accountable: " + _people(rows["active_accountable"], rows.get("names", {})))
        lines.append("Reviewers: " + _people(rows.get("reviewers", []), rows.get("names", {})))
        for path in rows.get("paths", [])[:4]:
            chain = path.get("chain") or [path["guarantee"]]
            lines.append(f"- {_text(path['role'])}: " + " → ".join(_text(node, 70) for node in chain))
    elif name == "who_knows":
        for row in rows[:4]:
            lines.append(f"{_text(row['team_name'])} ({_text(row['team'])}) owns {_text(row['service'])}.")
            if row.get("person"):
                lines.append(f"Expert: {_text(row.get('name') or row['person'])} — {_text(row['evidence'])} recent authored sources")
    elif name == "guarantee_health":
        for guarantee in rows.get("guarantees", [])[:6]:
            lines.append(f"{_text(guarantee['id'])}: {_text(guarantee['status'])}")
        for assumption in rows.get("assumptions", [])[:4]:
            lines.append(f"{_text(assumption['id'])}: {_text(assumption['state'])}")
    elif name == "impact_if_broken":
        if rows.get("guarantees"):
            lines.append(f"Hypothetical impact if {_text(rows['assumption'])} breaks:")
            for guarantee in rows["guarantees"][:4]:
                lines.append(f"- {_text(guarantee['guarantee'])}; decisions: " +
                             ", ".join(_text(d) for d in guarantee.get("promised_by", []) + guarantee.get("relied_on_by", [])))
    elif name == "orphaned_decisions":
        lines.append("Accepted decisions without an active accountable owner:")
        lines.extend(f"- {_text(row['decision'])}: {_text(row['title'])}" for row in rows[:6])
    elif name == "search_evidence":
        lines.extend(f"- {_text(row.get('text', ''), 260)}" for row in rows[:4])
    if not lines:
        lines = [NOT_FOUND]
    lines.extend(_citations(rows))
    return _finish(lines, rows.get("graph_rev") if isinstance(rows, dict) else None)
