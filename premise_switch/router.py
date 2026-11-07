"""Bounded, deterministic room text routing; text never supplies tool names."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
import math
import re
from typing import Any


@dataclass(frozen=True)
class Route:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    explicit: bool = True


class ParseError(ValueError):
    """An explicit command could not be parsed without guessing."""


ENTITY = re.compile(r"\b(?:ADR-\d{3}|[GA]_[A-Z_]+)\b", re.I)
TARGET = r"[A-Za-z0-9_][A-Za-z0-9_.\-/]*(?:#[A-Za-z0-9_][A-Za-z0-9_.-]*)?"
VALUE = r'''(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|true\b|false\b|[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)'''
ASSIGNMENT = re.compile(rf"({TARGET})\s*=\s*({VALUE})", re.I)


def _changes(text: str) -> list[dict[str, Any]]:
    changes = []
    remaining = text.strip()
    while remaining:
        match = ASSIGNMENT.match(remaining)
        if not match:
            raise ParseError("Use fact.key = a finite number, true/false, or a quoted string.")
        target, literal = match.groups()
        if literal[0] in "\"'":
            try:
                value = ast.literal_eval(literal)
            except (ValueError, SyntaxError) as exc:
                raise ParseError("Invalid quoted string.") from exc
        elif literal.lower() in {"true", "false"}:
            value = literal.lower() == "true"
        else:
            value = float(literal) if any(c in literal.lower() for c in ".e") else int(literal)
            if isinstance(value, float) and not math.isfinite(value):
                raise ParseError("Numbers must be finite.")
        if "#" in target:
            path, key = target.split("#")
            if any(part in {"", ".", ".."} for part in path.split("/")) or len(path) > 240:
                raise ParseError("Use a repository-relative file path.")
            item = {"file": path, "key": key, "value": value}
        else:
            item = {"fact": target, "value": value}
        if len(item.get("fact", item.get("key", ""))) > 160:
            raise ParseError("Fact or key is too long.")
        if any({k: v for k, v in old.items() if k != "value"} ==
               {k: v for k, v in item.items() if k != "value"} for old in changes):
            raise ParseError("Specify each fact only once.")
        changes.append(item)
        if len(changes) > 20:
            raise ParseError("Check at most 20 changes at once.")
        remaining = remaining[match.end():]
        if not remaining.strip():
            break
        separator = re.match(r"(?:[ \t]*,[ \t]*(?:and\b[ \t]*)?|[ \t]+and\b[ \t]*|[ \t]*\r?\n\s*)", remaining, re.I)
        if not separator or not remaining[separator.end():].strip():
            raise ParseError("Separate assignments with commas, and, or newlines.")
        remaining = remaining[separator.end():]
    if not changes:
        raise ParseError("Supply at least one fact.key = value.")
    return changes


def parse(text: str) -> Route:
    if not isinstance(text, str) or len(text) > 2000:
        raise ParseError("Use 1 to 2000 characters.")
    text = re.sub(r"(?<![\w@])@premise\b", "", text, flags=re.I).strip()
    # Agents often wrap commands in Markdown code spans.
    text = text.replace("`", "").strip()
    if not text:
        raise ParseError("Supply a command or question.")
    if text.lower() == "help":
        return Route("help")
    if "=" in text or re.match(r"^check\b", text, re.I):
        return Route("check_change", {"changes": _changes(re.sub(r"^check\b\s*", "", text, flags=re.I))})
    who = re.match(r"^who\s+(?:knows|owns|should\s+review)\s+(.+?)[?]?\s*$", text, re.I)
    if who:
        return Route("ownership", {"query": who[1]})
    if re.match(r"^(?:why|lineage)\b", text, re.I):
        entity = re.search(r"\bADR-\d{3}\b", text, re.I)
        if entity:
            return Route("decision_lineage", {"id": entity[0].upper()})
    impact = re.fullmatch(r"(?:impact\s+(.+?)|what\s+breaks\s+if\s+(.+?)\s+breaks)[?]?", text, re.I)
    if impact:
        target = (impact[1] or impact[2]).strip()
        if re.fullmatch(r"A_[A-Z_]+", target, re.I):
            return Route("impact_if_broken", {"id": target.upper()})
        return Route("impact", {"query": target})
    # The analyst's deterministic fallback, intentionally not an explicit command.
    entity = ENTITY.search(text)
    if entity:
        identity = entity[0].upper()
        name = "decision_lineage" if identity.startswith("ADR-") else "guarantee_health" if identity.startswith("G_") else "impact_if_broken"
        return Route(name, {"id": identity}, explicit=False)
    if any(word in text.lower() for word in ("health", "unverified", "load bearing", "load-bearing", "guarantees")):
        return Route("guarantee_health", explicit=False)
    if "orphan" in text.lower() or "depart" in text.lower():
        return Route("orphaned_decisions", explicit=False)
    return Route("question", {"query": text}, explicit=False)
