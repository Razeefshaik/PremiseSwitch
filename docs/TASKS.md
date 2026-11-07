# premise-switch: task board

**How to use this board:**
- Each card says who owns it, which files it covers, and when it's due.
- Ownership is in `AGENTS.md`, and interfaces are in `docs/CONTRACTS.md`.
- "Needs the owner" marks a step an agent cannot do, such as using a dashboard or recording a video.

**Cut order if time runs short:** see HANDOFF section 8. All times IST.

---

**C01: foundation**
- **Owner:** Claude. **Due:** 13:30.
- **Goal:** new repo prepared, contracts frozen, docs filled, ready for parallel cards.
- **Files:** repo root config, `premise_switch/contracts.py`, `tests/switch_fakes.py`, `docs/`.
- **Status:** active. Needs the owner: create the empty GitHub repo, start the VM.

**C02: Switch client**
- **Owner:** Razeef's Codex. **Due:** 15:00.
- **Goal:** registration, per-room connections, ops, dedupe and persisted state, tested against a fake server.
- **Files:** `premise_switch/client.py`, `premise_switch/state.py`, `tests/fake_switch.py`, `tests/test_switch_client.py`.
- **Status:** todo.

**C03: answer and guard**
- **Owner:** teammate's Codex. **Due:** 15:00.
- **Goal:** a question or change in a room becomes a correct, cited reply with BLOCKED or CLEAR.
- **Files:** `premise_switch/router.py`, `premise_switch/format.py`, `premise_switch/answer.py`, `tests/test_switch_answer.py`.
- **Status:** todo.

**C04: listen**
- **Owner:** Razeef's Codex. **Due:** 17:30.
- **Goal:** room messages flow through the v2 pipeline and produce one proposal notice per drift; injection can't count as evidence.
- **Files:** `premise_switch/listen.py`, `tests/test_switch_listen.py`, source handling in `premise_v2/`, injection handling in `premise_v2/extract/`.
- **Status:** todo.

**C05: repro pack**
- **Owner:** teammate's Codex. **Due:** 17:00.
- **Goal:** a stranger can start Premise, create the rooms and check their setup with one command each.
- **Files:** `scripts/up.sh`, `scripts/prepare_switch_demo.py`, `templates/kestrel.group.yaml`, `premise_switch/doctor.py`, `tests/test_switch_repro.py`.
- **Status:** todo.

**C06: clearance receipts**
- **Owner:** Razeef's Codex. **Due:** 20:00. **After:** C03 merged.
- **Goal:** CLEAR carries a signed receipt, and a git hook refuses a `config/` commit without a matching one.
- **Files:** `premise_switch/clearance.py`, `demo/hooks/`, `tests/test_switch_clearance.py`, small hook-in to `premise_switch/answer.py`.
- **Status:** todo.

**C07: extras**
- **Owner:** teammate's Codex. **Due:** 20:00. **After:** C02 and C03 merged.
- **Goal:** decision ledger document, reviewer pings, room-join intro, linked-room hints, repeatable demo messages.
- **Files:** `premise_switch/extras.py`, `demo/seed_payments.py`, `tests/test_switch_extras.py`.
- **Status:** todo.

**C08: adversarial review**
- **Owner:** whichever Codex is free. **Due:** 20:30. **After:** C09 first pass.
- **Goal:** find real bugs in the adapter through hostile room input and failure cases; fix nothing outside the review report.
- **Files:** read-only, report only.
- **Status:** todo.

**C09: wiring and live milestones**
- **Owner:** Claude. **Due:** 21:00.
- **Goal:** `python -m premise_switch` runs everything on the VM; M1, M2, M3 pass live; README and walkthrough written.
- **Files:** `premise_switch/__main__.py`, `README.md`, `docs/WALKTHROUGH.md`, `docs/PREMISE_ORIGINAL.md`.
- **Status:** todo. Needs the owner: registration key, room invites, Mattermost accounts.

**C10: video and submission**
- **Owner:** Razeef, with Claude guiding. **Due:** 23:00.
- **Goal:** video recorded, repo public, form submitted.
- **Files:** `docs/submission/`.
- **Status:** todo. Needs the owner.
