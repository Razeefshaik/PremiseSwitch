# premise-switch: contracts

Version 1.0.0. The interfaces between owners. Changes need owner approval and a version bump. The code is `premise_switch/contracts.py`, and test doubles are in `tests/switch_fakes.py`.

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
- **Shape:** defined by C06 in `clearance.py` and recorded here when merged.
