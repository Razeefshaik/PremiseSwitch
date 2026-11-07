# premise-switch future task notes

Reusable detail, grouped by task. Read only the section you need. No secrets, environment values or real emails.

## Local verification
See `AGENTS.md`. Set `POLL_ENABLED=false` when running Premise: the default in `config.py` is true, which skips loading the graph. `premise ingest` also loads it.

## Premise internals (2026-11-07)
- **Tools in-process:** `GraphTools(GraphClient.from_settings(settings())).call(name, args)`. `premise/agent/analyst.py` has the deterministic question routing and `row_answer`, which gives generic prose for `search_evidence`, so use the raw rows there.
- **`check_change` result keys** (`premise/reasoning/preflight.py`, `compact()`): `hypothetical, stored, graph_rev, verdict, touched, reasons, changed, assumptions, guarantees, not_holding, decisions, verified_fixes, advice`. Verdicts: `BLOCKED`, `NEEDS_REVIEW`, `CLEAR_WITHIN_SCOPE`, `ERROR`.
- **v2 extraction with no model** needs one sentence with an entity alias, a term within 8 words of a number, a compatible unit, and exactly one matching fact. Aliases are in `sandbox/kestrel-platform/ext/fact_aliases.yaml`.
- **v2 proposals** need 2+ independent origins, a value different from the model, and a claim newer than the YAML line's git blame, so full git history is required. Claims sharing a person, thread or ticket merge into one origin, so demo messages must be top-level and from different people.
- **`list_keys` must be complete**, or missing items get tombstoned (`pipeline.py:127`).
- **Identity:** explicit accounts live in `sandbox/kestrel-platform/ext/identities.yaml`. Unlinked accounts become `unresolved:<account>`.
