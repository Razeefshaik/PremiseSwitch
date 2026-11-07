"""Gateway: everything that happens to an envelope before anyone reads its meaning.

Order matters and every step is deterministic in Phase 1:
  1. version check  (newer than the latest stored version of this item? a no-op? a tombstone?)
  2. secret redaction (before anything is stored or shown to a model)
  3. classification by rules (a model classifier is a later phase)
  4. scope (does it mention anything Premise tracks? if not, keep it as evidence and skip extraction)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .envelope import Envelope
from .registry import Ext

SECRET_PATTERNS = [
    ("github_token", re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,})")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")),
    ("credential_pair", re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S{6,}")),
]

CLASS_RULES = {
    ("slack", "message"): "chat",
    ("switch", "message"): "chat",
    ("jira", "ticket"): "ticket",
    ("jira", "comment"): "ticket_comment",
    ("github", "issue"): "ticket",
    ("github", "pull_request"): "ticket",
    ("github", "comment"): "ticket_comment",
    ("github", "page"): "doc",
    ("github", "file"): "ownership",
    ("pages", "page"): "doc",
    ("config", "file"): "config_file",
}
ENTITY_ID = re.compile(r"\b(?:ADR-\d{3}|[GA]_[A-Z_]{3,})\b")


def redact(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for name, pattern in SECRET_PATTERNS:
        text, n = pattern.subn(f"[REDACTED:{name}]", text)
        if n:
            counts[name] = counts.get(name, 0) + n
    return text, counts


@dataclass
class GatewayResult:
    action: str                       # stored | duplicate | stale | deleted
    envelope: Envelope
    cls: str = "unclassified"
    classifier: str = "rules/1"
    in_scope: bool = False
    redactions: dict[str, int] = field(default_factory=dict)


class Gateway:
    def __init__(self, ext: Ext, staging, extra_scope_terms: set[str] | None = None):
        self.ext, self.staging = ext, staging
        terms = set(extra_scope_terms or set())
        for spec in ext.aliases.values():
            terms.update(t.lower() for t in spec.get("entity", []))
        self.scope_terms = terms

    def in_scope(self, env: Envelope) -> bool:
        text = f"{env.title or ''} {env.text}".lower()
        words = set(re.findall(r"[a-z0-9][a-z0-9\-]*", text))
        return bool(words & self.scope_terms) or bool(ENTITY_ID.search(f"{env.title or ''} {env.text}"))

    def process(self, env: Envelope) -> GatewayResult:
        latest = self.staging.latest_version(env.entity_key)
        if latest:
            if env.sequence < latest["sequence"]:
                return GatewayResult("stale", env)
            same = env.op == "upsert" and env.content_hash == latest["hash"] and not latest["deleted"]
            if same and latest["processed"]:
                if env.sequence > latest["sequence"]:
                    self.staging.advance_version(env.entity_key, env.sequence)
                return GatewayResult("duplicate", env)       # replay, or touched but unchanged
        if env.op == "delete":
            return GatewayResult("deleted", env)
        title, t_counts = redact(env.title or "")
        text, counts = redact(env.text)
        for k, v in t_counts.items():
            counts[k] = counts.get(k, 0) + v
        clean = env.model_copy(update={"text": text, "title": title or None})
        cls = CLASS_RULES.get((env.source, env.kind), "unclassified")
        return GatewayResult("stored", clean, cls=cls, in_scope=cls != "unclassified" and self.in_scope(clean),
                             redactions=counts)
