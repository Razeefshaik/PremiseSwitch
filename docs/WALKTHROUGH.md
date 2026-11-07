# Walkthrough: how Premise × Switch works

For anyone who needs to explain this project: judges, reviewers, or us next week. Read it alongside the code. Every claim names the file to check.

## The path of one message

1. **Switch delivers it.** `premise_switch/client.py` keeps one WebSocket per room (`_serve_room`). Frames arrive in `_read`. A `ping` is answered with a pong straight away, and everything else is queued for that room's worker (`_consume`). That split is why slow graph work never makes Switch drop the connection.
2. **It becomes an `InboundMessage`.** `_normalise` turns the frame into the frozen type from `premise_switch/contracts.py`:
   - The sender is matched to a room participant, by id, then name, then `sender_name`, to decide person or agent.
   - Premise's own messages are dropped.
   - A `message_id` already seen is dropped (`state.remember`), so replays never cause a second reply.
3. **The adapter routes it.** `premise_switch/adapter.py`, `on_message`:
   - A message **not addressed** to Premise goes to Listen (`listener.record`), and a pipeline run is scheduled a few seconds later.
   - An **addressed** message goes to Answer (`responder.respond`), off the event loop. Commands to Premise are never recorded as evidence.
4. **Answer decides.** `premise_switch/answer.py`:
   - It splits stacked `@premise` lines and parses each with `premise_switch/router.py`. The text only chooses a tool and its arguments; it never becomes code or a query.
   - It calls Premise's existing read-only graph tools (`premise/graph/tools.py`), and formats the result with `premise_switch/format.py`.
   - Agents get a reply only to a real command, and at most 6 per thread every 10 minutes.
5. **The reply goes back.** `client.post` uses `send_targeted_message` when it should mention the asking agent, otherwise `post_message`, always in the asker's thread. On a BLOCKED reply, the adapter adds "Related: #payments" from the room's links, then records the verdict in the room's ledger document.

## Guard: where the verdict comes from

`check paylane.retry_window_hours = 72` becomes `check_change` on Premise's graph. The graph holds:

| Node | Example |
| --- | --- |
| Decisions | ADR-012 |
| Guarantees | `G_CHARGE_ONCE` |
| Assumptions | `A_RETRY_WINDOW`: "retries end before dedupe records expire" |
| Facts | config values |

The change makes `A_RETRY_WINDOW` false, which breaks `G_DEDUPE`, which breaks `G_CHARGE_ONCE`. That's the chain in the reply. Premise also searches for a set of extra changes that make everything hold again: the verified fix. All of this is pre-existing Premise code in `premise/reasoning/`. Our part is getting it safely into and out of a Switch room.

**Never a false pass.** `format_check` in `premise_switch/format.py` only prints CLEAR when the result is internally consistent: no broken guarantees, and only "all hold" reasons. Anything else, including an untracked fact anywhere in the change, is shown as unchecked.

## Enforce: how a receipt works

`premise_switch/clearance.py`:
- **Issuing:** on CLEAR, Premise maps each fact to its file and key (`sandbox/kestrel-platform/facts.yaml`). It signs `{changes, graph_rev, verdict, issued_at, room, thread}` as canonical JSON with an Ed25519 private key that never leaves the server (`.runtime/switch/clearance.key`). The receipt is `pc1.<payload>.<signature>`.
- **Checking:** `demo/hooks/commit-msg` runs on every commit in the coder's repo. If `config/` changed, it computes the exact staged changes, from `HEAD` to the index, leaf by leaf in the YAML. It then requires one `Premise-Clearance:` trailer whose signature verifies against `.premise/clearance.pub`, isn't older than 24 hours, and lists exactly those changes. Anything else refuses the commit.
- **Why it can't be forged:** the repo holds only the public key. An agent can read and edit the whole repo and still can't make a valid signature.
- **What it doesn't stop:** `git commit --no-verify`, or editing the hook. Server-side checking is the next step, and the README says so.

## Listen: how a proposal happens

`premise_switch/listen.py`:
1. **Journal:** each human message is redacted with the existing v2 gateway rules, then appended to `.runtime/switch/messages.jsonl`. It's idempotent on `message_id`.
2. **Pipeline run:** after a quiet gap, `run_once` feeds the journal through Premise v2's existing pipeline (`premise_v2/pipeline.py`), with one `SwitchWorker` per room:
   - **Extract:** pattern-based, with no model. "Paylane … retries … 72 hours" becomes a claim that `paylane.retry_window_hours = 72`.
   - **Ground:** the claim must quote the source.
   - **Reconcile:** claims are grouped into independent origins. The same person, thread or ticket counts once.
   - **Propose:** a proposal needs at least 2 origins and a value different from the model.
3. **Notice:** a new proposal becomes one notice per room, quoting only that room's evidence, and counting evidence from other rooms without quoting it.
4. **Injection:** "Ignore previous instructions…" marks every claim in that message as `instruction`, which never supports a change (`premise_v2/extract/__init__.py`, our fix).

## Setup and safety

| File | What it does |
| --- | --- |
| `premise_switch/state.py` | The agent id, API key, per-room connection ids, cursors and recent message ids, written atomically and readable only by the owner |
| `premise_switch/__main__.py` | Starts everything. It registers only if there's no state and a registration key is set |
| `premise_switch/doctor.py` | Read-only checks of a setup, including that every operation and argument we use exists on the running server |
| `templates/kestrel.group.yaml` | Creates the two linked rooms, with agents and instructions |
| `scripts/up.sh`, `scripts/prepare_switch_demo.py`, `scripts/reset_listen.sh` | Start Premise, make the coder's repo, and replay Listen |

## Questions judges may ask

| Question | Answer |
| --- | --- |
| **Would it work without Switch?** | No. The rooms, the people, the coder's handoff, threads, addressing, linked rooms, room documents and the template are all Switch. Premise's reasoning existed before, but nothing could ask it in a team workflow. |
| **Is an LLM making the safety call?** | No. Answers are deterministic graph reasoning. The coding agent is an LLM, and it's the thing being guarded. |
| **What if the graph is down?** | The reply says the change is unchecked, never that it's fine. There's no receipt, so git refuses the commit. |
| **Could the coder fake a receipt?** | Not without Premise's private key, which stays on the server. |
| **What stops agents looping?** | Premise answers agents only for explicit commands, mentions only the asker, and caps replies per thread. |
| **What was built during the hackathon?** | Everything from commit `9769606` onwards. The README lists it by directory. |
| **How do we know it works?** | 286 Switch tests, including a fake Switch server and real git commits; the full suite of 671; the gold evaluation; an adversarial review whose findings are fixed (`docs/reviews/C08-2026-11-07.md`); and live runs against Switch 0.31.0 and Mattermost. |
