# premise-switch

Premise as a custom Switch agent: it answers from its decision graph, guards config changes, and listens to rooms. Deadline: 7 Nov 2026, 23:59 IST (aim for 23:00).

Before any design or build work, read docs/HANDOFF.md and docs/DECISIONS.md fully.
Also follow AGENTS.md.
HANDOFF.md is the source of truth for the design. docs/PLAN.md holds the plan.

Project rules: see HANDOFF section 4. In short: no language model in answers, an empty result is never safety, no secrets anywhere, never touch the original Premise project or its deployments.

Everyone who works on this project commits as they build, one commit per working step, so the history gives a clear view. No squashing.

Always confirm `git remote get-url origin` ends in `Razeefshaik/PremiseSwitch.git` before any git command. On Razeef's machine the folders above this repo belong to a different repo. Notes and prompts live outside the repo, in the folder above it.
