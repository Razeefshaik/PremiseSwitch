"""Reversible account links. Fuzzy proposals never participate in origin counting."""
from __future__ import annotations

import hashlib
from collections import defaultdict

import yaml
from rapidfuzz.fuzz import token_set_ratio
from premise.agent.render import safe

from .gateway import redact
from .registry import Ext
from .staging import now_ms


def account_of(env):
    native = env.author.source_user_id
    return f"{env.source}:{native}" if native else f"{env.source}:unknown:{env.entity_key}"


class IdentityDirectory:
    def __init__(self, staging, decision, model_root):
        self.staging, self.decision, self.root = staging, decision, model_root

    def capture(self, env):
        key = account_of(env)
        display = redact(env.author.display_name or "")[0]
        email = (env.author.email or "").strip().lower()
        self.staging.c.write(
            "MERGE (a:Account {key:$k}) SET a.source=$s, a.display=$d, a.email=$e, "
            "a.email_verified=$verified, a.visibility=$v",
            {"k": key, "s": env.source, "d": display, "e": email,
             "verified": env.author.email_verified, "v": env.visibility})
        return key

    def sync(self):
        ext = Ext.load(self.root)
        people = {r["id"]: r["name"] for r in self.decision.read(
            "MATCH (p:Person) WHERE p.retired_rev IS NULL RETURN p.id AS id, p.name AS name")}
        accounts = [r["a"] for r in self.staging.c.read("MATCH (a:Account) RETURN properties(a) AS a")]
        explicit, emails = {}, {}
        for person, ids in ext.identities.items():
            if person not in people:
                raise ValueError(f"identity mapping references unknown person {person}")
            for source in ("slack", "jira", "github", "pages", "switch"):
                values = ids.get(source) or []
                for native in values if isinstance(values, list) else [values]:
                    key = f"{source}:{native}"
                    if key in explicit and explicit[key] != person:
                        raise ValueError("one account cannot link to multiple people")
                    explicit[key] = person
            if ids.get("email"):
                email = ids["email"].lower()
                if email in emails and emails[email] != person:
                    raise ValueError("verified email mapping is ambiguous")
                emails[email] = person
        confirmed = {}
        for row in ext.identity_links:
            account, person = row["account"], row["person"]
            if person not in people or row.get("status", "confirmed") not in {"confirmed", "rejected"}:
                raise ValueError("invalid reviewed identity link")
            if account in confirmed or account in explicit and explicit[account] != person:
                raise ValueError("one account cannot link to multiple people")
            confirmed[account] = (person, row.get("status", "confirmed"))
        desired = []
        for a in accounts:
            key = a["key"]
            if key in confirmed:
                person, status = confirmed[key]
                method = "merged"
            elif key in explicit:
                person, status, method = explicit[key], "auto", "explicit"
            elif a.get("email_verified") and a.get("email") in emails:
                person, status, method = emails[a["email"]], "auto", "verified_email"
            else:
                continue
            desired.append({"account": key, "person": person, "status": status, "method": method})
        # Validate all mappings first; only the staging graph is ever changed.
        self.staging.c.write("MATCH (:Account)-[l:LINKED_TO]->(:PersonRef) "
                             "WHERE l.status IN ['auto','confirmed'] SET l.status='rejected', l.at_ms=$now", {"now": now_ms()})
        if desired:
            self.staging.c.write(
                "UNWIND $rows AS row MATCH (a:Account {key:row.account}) MERGE (p:PersonRef {id:row.person}) "
                "MERGE (a)-[l:LINKED_TO]->(p) SET l.method=row.method, l.status=row.status, "
                "l.score=100.0, l.decided_by='model mapping', l.at_ms=$now, l.bridge=false",
                {"rows": desired, "now": now_ms()})
        active = self.person_map()
        linked_by_source = {(key.split(":", 1)[0], person) for key, person in active.items()}
        # Authoritative mappings count even before that account's first message.
        linked_by_source.update((key.split(":", 1)[0], person) for key, person in explicit.items())
        linked_by_source.update((key.split(":", 1)[0], person) for key, (person, status) in confirmed.items()
                                if status == "confirmed")
        existing = {(r["account"], r["person"]): r["status"] for r in self.links()}
        for a in accounts:
            if a["key"] in active or not a.get("display"):
                continue
            for person, name in sorted(people.items()):
                score = token_set_ratio(a["display"].casefold(), name.casefold())
                if score < 92 or existing.get((a["key"], person)) == "rejected":
                    continue
                bridge = (a["source"], person) in linked_by_source
                self.staging.c.write(
                    "MATCH (a:Account {key:$a}) MERGE (p:PersonRef {id:$p}) MERGE (a)-[l:LINKED_TO]->(p) "
                    "SET l.status='proposed', l.method='fuzzy', l.score=$score, l.bridge=$bridge, l.at_ms=$now",
                    {"a": a["key"], "p": person, "score": score, "bridge": bridge, "now": now_ms()})

    def links(self):
        return self.staging.c.read("MATCH (a:Account)-[l:LINKED_TO]->(p:PersonRef) RETURN a.key AS account, "
            "a.display AS display, a.visibility AS visibility, p.id AS person, l.status AS status, "
            "l.method AS method, l.score AS score, l.bridge AS bridge ORDER BY account, person")

    def person_map(self):
        rows = self.staging.c.read("MATCH (a:Account)-[l:LINKED_TO]->(p:PersonRef) "
                                  "WHERE l.status IN ['auto','confirmed'] RETURN a.key AS account, p.id AS person")
        grouped = defaultdict(set)
        for row in rows:
            grouped[row["account"]].add(row["person"])
        return {key: next(iter(values)) for key, values in grouped.items() if len(values) == 1}

    def resolve_claims(self, claims):
        self.sync()
        mapping = self.person_map()
        for c in claims:
            account = c.get("account")
            # Historical Phase 1 rows lack accounts; preserve their historical person
            # until the original source is replayed. New claims always carry accounts.
            c["person"] = mapping.get(account, f"unresolved:{account}") if account else c.get("person", "unresolved:legacy")
        return claims

    def propose(self, writer):
        active = self.person_map()
        candidates = [r for r in self.links() if r["status"] == "proposed" and r["account"] not in active]
        counts = defaultdict(int)
        for c in candidates:
            counts[c["account"]] += 1
        # Ambiguous accounts and bridges require individual PRs, never a batch.
        regular = [c for c in candidates if not c["bridge"] and counts[c["account"]] == 1]
        individual = [c for c in candidates if c not in regular]
        groups = ([regular[:10]] if regular else []) + [[c] for c in individual[:10]]
        output = []
        for group in groups:
            pairs = sorted((c["account"], c["person"]) for c in group)
            key = "identity:" + hashlib.sha256(repr(pairs).encode()).hexdigest()[:12]
            path = writer.root / "ext/identities.yaml"
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {"people": {}}
            links = doc.setdefault("links", [])
            for account, person in pairs:
                links[:] = [r for r in links if r["account"] != account]
                links.append({"account": account, "person": person, "status": "confirmed"})
            body = "Identity links proposed for human review. These links do not affect origin counts until approved in the model.\n\n"
            if any(c["bridge"] for c in group):
                body += "**Bridge warning:** this person already has a linked account in this source. Review individually.\n\n"
            body += "\n".join(f"- `{safe(a)}` -> `{safe(p)}`" for a, p in pairs)
            p = writer.write_document("identity", key, "ext/identities.yaml", yaml.safe_dump(doc, sort_keys=False), body,
                                      f"Premise v2: review {len(group)} identity link(s)")
            output.append({"key": key, "kind": "identity", "state": p.state, "bridge": any(c["bridge"] for c in group), "pr_url": p.pr_url})
        return output
