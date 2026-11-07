# premise-switch: contracts

Version 1.1.0. The interfaces between owners. Changes need owner approval and a version bump. The code is `premise_switch/contracts.py`, and test doubles are in `tests/switch_fakes.py`.

## InboundMessage, Reply, RoomGateway
- **Owner:** C02 (client).
- **Used by:** C03, C04, C07, C09.
- **Shape:** the client turns each `message` frame into `InboundMessage` and exposes `RoomGateway`: `post`, `history`, `upsert_document`, `linked_rooms`. A `Reply` with a non-empty `mention` goes through `send_targeted_message`, otherwise through `post_message`. The client fills `sender_handle` and `sender_is_agent` from `list_participants`, and drops the agent's own messages and replayed `message_id`s before anything else sees them.

## Responder
- **Owner:** C03.
- **Used by:** C09.
- **Shape:** `premise_switch.answer.Answerer(tools: ToolBackend)`, with `.respond(message) -> Reply | None`. Synchronous; the wiring calls it with `asyncio.to_thread`. `None` means stay silent. It returns `None` when `addressed` is false. It never raises on graph failure: it returns the "unchecked" reply.

## Listener
- **Owner:** C04.
- **Used by:** C09.
- **Shape:** `premise_switch.listen.SwitchListener(...)`, with `.record(message)` and `.run_once() -> list[ProposalNotice]`. Synchronous. The wiring calls `record` on every message and `run_once` on a debounce after new messages.

## ToolBackend
- **Owner:** existing Premise code.
- **Used by:** C03, C06.
- **Shape:** `GraphTools(GraphClient.from_settings(settings())).call(name, arguments)` from `premise/graph/tools.py`. Tool names and argument models are in its `TOOLS` table. `check_change` takes `{"changes": [{"fact": "...", "value": ...}]}`, or `file` + `key` in place of `fact`, or `delete: true` in place of `value`.

## Clearance receipt
- **Owner:** C06.
- **Used by:** C03 (optional hook), the demo repo's git hook.
- **Issuer:** `Clearance(key, *, facts_path)` in `premise_switch.clearance`. `load_or_create_key(state_dir)` loads/creates an Ed25519 key at `state_dir/clearance.key`; `facts_path` is the model's `facts.yaml`, not the config file or a v2 alias registry. An unmapped fact means no receipt.
- **Issue:** `.issue(result, *, room, thread, now) -> str | None` accepts conservative `CLEAR_WITHIN_SCOPE` compact results only. `changed` fact IDs map to exact file/key targets and `new` supplies the value. An empty changed list can sign formatting-only config edits. `now` is UTC Unix seconds (or an aware datetime), separate from Answerer's monotonic rate-limit clock.
- **Wire format:** `pc1.<unpadded base64url canonical JSON>.<unpadded base64url Ed25519 signature>`. JSON has exactly `changes`, `graph_rev`, `verdict`, `issued_at`, `room`, `thread`. `changes` is sorted by `(file, key)` with exact `{file, key, value}` rows. Canonical JSON uses sorted keys, no whitespace, ASCII escapes and finite JSON values. Deletion is `value: {"$delete": true}`, distinct from YAML null. Tokens contain no `@`.
- **Verify:** `verify(receipt, public_key, changes, now, max_age_hours=24) -> Verdict`; `.ok` (also boolean conversion), `.reason`, and `.payload` report the outcome. Public keys may be Ed25519 objects or PEM bytes/text. Verification rejects invalid signatures/schema, future/expired receipts and any typed change-set mismatch. Room, thread and graph revision are signed provenance; this offline hook does not query the live graph.
- **Public key:** `public_key_pem(key)` or `clearance.public_key_pem()` returns only PEM public bytes. Install those at the demo repo's `.premise/clearance.pub`; never copy the private key to the coder's repo. POSIX private-key permissions are 0600; Windows chmod is not an ACL policy, so Windows operators must protect their state directory separately.
- **Answer hook-in:** `Answerer(tools, clock=time.monotonic, *, clearance=None)` appends `Premise-Clearance: <receipt>` as the final line, after escaping a valid CLEAR answer. Default behavior is unchanged; rejected/unavailable checks never receive a receipt. Do not truncate or re-escape the token when copying it into a commit trailer.
- **Construction for C09 (no wiring changed in C06):**

```python
from premise_switch.answer import Answerer
from premise_switch.clearance import Clearance, load_or_create_key

clearance = Clearance(load_or_create_key(state_dir), facts_path=model_root / "facts.yaml")
answerer = Answerer(tools, clearance=clearance)
public_pem = clearance.public_key_pem()  # Only this public material goes to the demo repo.
```

- **Install text for README:** activate an environment with `cryptography` and `pyyaml`; copy `demo/hooks/commit-msg` from PremiseSwitch into the demo repo's `.git/hooks/commit-msg`, and make it executable (`chmod +x .git/hooks/commit-msg` on POSIX). The standalone hook's `python` interpreter must use that environment. The hook reads HEAD/index blobs, not unstaged worktree edits, and refuses ambiguous/unsupported YAML or any validation failure. Commits outside `config/` need no receipt.
- **Limit:** a trusted local hook/public-key installation is required; `--no-verify` bypasses it. A server-side check is the next enforcement step.
