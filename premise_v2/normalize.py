"""Identity links and normalization into Kestrel Interchange Format (KIF) rows.

Identity: people are LINKED, never merged. Phase 1 links only through the explicit ext/identities.yaml
mapping or an exact email match. Anyone else stays ``unresolved:<source>:<id>``: their messages count
as evidence, but they can never become reviewers or accountable people.
"""
from __future__ import annotations

from dataclasses import dataclass

from .envelope import Envelope
from .registry import Ext


@dataclass(frozen=True)
class IdentityLink:
    person: str
    method: str            # explicit | email | unresolved


class IdentityResolver:
    def __init__(self, ext: Ext):
        self.by_account: dict[tuple[str, str], str] = {}
        self.by_email: dict[str, str] = {}
        for person, ids in ext.identities.items():
            for source in ("slack", "jira", "github", "switch"):
                if ids.get(source):
                    self.by_account[(source, str(ids[source]))] = person
            if ids.get("email"):
                self.by_email[ids["email"].lower()] = person

    def resolve(self, env: Envelope) -> IdentityLink:
        a = env.author
        if a.source_user_id and (env.source, a.source_user_id) in self.by_account:
            return IdentityLink(self.by_account[(env.source, a.source_user_id)], "explicit")
        if a.email and a.email.lower() in self.by_email:
            return IdentityLink(self.by_email[a.email.lower()], "email")
        return IdentityLink(f"unresolved:{env.source}:{a.source_user_id or 'unknown'}", "unresolved")


def to_kif(env: Envelope, cls: str, person: str) -> dict:
    """The KIF row this envelope would become (same shapes as chat/chat.jsonl and tickets/tickets.jsonl)."""
    ts = env.time.strftime("%Y-%m-%dT%H:%M:%SZ")
    if cls == "chat":
        return {"id": env.native_id, "channel": (env.container or "").lstrip("#"), "author": person, "ts": ts,
                "text": env.text, "thread": env.thread}
    if cls in {"ticket", "ticket_comment"}:
        return {"id": env.native_id, "title": env.title or "", "body": env.text, "author": person,
                "created_at": ts[:10], "parent": env.thread}
    return {"id": env.native_id, "text": env.text, "author": person, "ts": ts}
