"""Registered entity mentions, resolved without merging people or guessing ids."""
from collections import Counter
import re

from .registry import Ext


class EntityCatalog:
    def __init__(self, decision, model_root):
        self.entities = {}
        rows = decision.read("MATCH (n) WHERE n.id IS NOT NULL AND n.retired_rev IS NULL "
                             "RETURN labels(n) AS labels, n.id AS id, properties(n) AS p")
        for row in rows:
            label = next((l for l in row["labels"] if l in {"Person", "Service", "Partner", "Team"}), None)
            if label:
                props = row["p"]
                self.entities[row["id"]] = {"id": row["id"], "label": label, "name": props.get("name", row["id"]),
                                             "aliases": list(props.get("aliases") or []) + [row["id"], props.get("name", row["id"])]}
        first_names = Counter(e["name"].split()[0].casefold() for e in self.entities.values() if e["label"] == "Person")
        for e in self.entities.values():
            if e["label"] == "Person" and first_names[e["name"].split()[0].casefold()] == 1:
                e["aliases"].append(e["name"].split()[0])
        ext = Ext.load(model_root)
        for partner in ext.partners:
            if not isinstance(partner.get("id"), str) or not isinstance(partner.get("name"), str) or partner["id"] in self.entities:
                raise ValueError("partner registry needs unique ids and names")
            self.entities[partner["id"]] = {"id": partner["id"], "label": "Partner", "name": partner["name"],
                                           "aliases": [partner["id"], partner["name"], *(partner.get("aliases") or [])]}
        for person, ids in ext.identities.items():
            if person in self.entities:
                for source in ("slack", "jira", "github", "pages", "switch"):
                    values = ids.get(source) or []
                    for native in values if isinstance(values, list) else [values]:
                        self.entities[person]["aliases"] += [str(native), f"{source}:{native}"]
        for link in ext.identity_links:
            if link.get("status", "confirmed") == "confirmed" and link["person"] in self.entities:
                self.entities[link["person"]]["aliases"] += [link["account"], link["account"].split(":", 1)[-1]]

    def mentions(self, text):
        found = []
        for e in self.entities.values():
            matches = []
            for alias in sorted(set(e["aliases"]), key=len, reverse=True):
                for m in re.finditer(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", text, re.I):
                    matches.append({"id": e["id"], "label": e["label"], "mention": m.group(), "start": m.start(), "end": m.end()})
            if matches:
                found.append(min(matches, key=lambda x: (x["start"], -(x["end"] - x["start"]))))
        return sorted(found, key=lambda m: m["start"])

    def resolves(self, mention, id_):
        return id_ in self.entities and mention.casefold() in {a.casefold() for a in self.entities[id_]["aliases"]}
