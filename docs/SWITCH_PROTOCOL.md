# Switch agent protocol: facts for this project

Read on 2026-11-07 from `docs.switchagents.ai` and `core/switch_core/bridges/agent/` in `sandbox-quantum/switch` at HEAD (0.41 canary). **The running server is 0.40.1, so live `GET /agents/{id}/ops` is the final word.** The repo's `agent-protocol.md` still describes SSE, but `handlers.py` says the live connection is the WebSocket.

## Registration (once only)
`POST /agents` with `Authorization: Bearer <registration key>`:
```json
{"name": "premise", "description": "...", "connector_type": "premise",
 "integration_profile": {"connection_model": "always_on", "message_exchange": true,
   "pre_invocation_mediation": [], "post_invocation_mediation": [], "event_reporting": []}}
```
Returns `{id, api_key}`; the key is shown once. A repeat gets 409, or with `overwrite: true` a new key that invalidates the old one. Never use `overwrite` automatically.

Addressing, sent once after registering: `POST /agents/{id}/ops/update_agent_detail` with `{"agent_id": "...", "addressing": "owner_and_owner_agents"}`.

## Connection: one per room
`GET /agents/{id}/connection/ws?connection_id=<uuid>&rooms=<room_id>&filter=all&start_from=<head|sequence>&expected_generation=<n>`, upgraded to a WebSocket, with `Authorization: Bearer <agent key>`.

**Use one connection per room, each with its own stable UUID.** `post_message` and `send_targeted_message` have no room argument. They use the caller's single connected room and raise "This connection covers several rooms" otherwise. `POST /agents/{id}/message` takes `{room_id, content}` but has no thread support. The limit is 32 connections per agent. Discover rooms by polling `list_rooms`.

## Frames
Each frame is JSON: `{"event": "...", "data": {...}, "id": n}`.

- **`connection_state`** always comes first: `connection_id, agent_id, generation, scope, filter, spawn_capable, rooms, cursor, protocol, heartbeat_interval_seconds, server, client`. Keep `generation`.
- **`ping`** arrives every 2 s. Answer with `{"type": "pong", "cursor": n}` **before** processing events. The pong is the only frame a client sends. After 6 s of silence you get `evicted` with `heartbeat_lapsed`.
- **`message`**: `data` has `type, room_id, bridge_id, channel_type, payload, sequence`. `payload` has `addressed, sender, sender_name, sender_kind, on_behalf_of, message_id, body, timestamp (ms), thread_id, attachments`.
- **`command`**: `command, args, user_id, user_name, thread_id`.
- **`room_join`**: `member, member_name, timestamp, listening`.
- **`gap`**: `from_sequence, resumed_at, resumed_position, rooms, all_rooms, reason`. Take `resumed_at` as the cursor and re-read history.
- **`evicted`**: `taken_over` is final, don't reconnect. `heartbeat_lapsed`, `no_stream`, `unfenced` and `connection_error` mean reconnect. `closed` means read `reason` and don't auto-retry.
- **`refused`**: `status, detail`, then close code 4000 + status. Auth failure closes with 4401 and no frame.
- Also `subscription_changed` and `room_released`. Close codes 1000, 1011 and 1012 mean reconnect.

**Reconnect:** same `connection_id`, `start_from=<last sequence>`, `expected_generation=<last generation>`. Cursors only move forward, but events can replay, so dedupe on `message_id` and persist the recent set.

## Operations
`POST /agents/{id}/ops/{operation}` with `Authorization: Bearer <agent key>` and `X-Switch-Connection-Id: <connection id for that room>`. The body is the bare arguments object, and the result comes back as `{"result": ...}`. Arguments are checked by Python signature, so **unexpected keys are rejected**. Errors: 400, 403, 404, 409 (dead connection).

| Operation | Arguments | Returns |
| --- | --- | --- |
| `post_message` | `body`, `thread_id?` | `{event_id, trust?}` |
| `send_targeted_message` | `body`, `target_names?`, `target_roles?`, `thread_id?` (don't pre-add `@`) | `{event_id, target_statuses}` |
| `read_context` | `limit=50`, `since?`, `before?`, `room_id?` | `{threads, truncated, oldest_timestamp}` |
| `list_rooms` | `include_archived=false` | `[{room_id, name, description, connected, archived}]` |
| `list_participants` | none | `[{id, name, type, status, alias}]` |
| `list_linked_rooms` | none | `[{target_room_id, target_room_name, target_room_description, label, access, access_note?}]` |
| `create_room_document` | `name, description, instructions, content` | document id fields, check live |
| `update_room_document` | `document_id, name?, description?, instructions?, content?` | check live |
| `connect_to_room` | `room_id`, `include_general_instructions=true` | instructions, participants, references, roles, links |

A blocked message fails the call (422). `trust` in a result means Switch Trust changed or flagged the text.

## Not usable
`pre_invocation_mediation` only checks whether a tool or model is attached (`mediation.py`). An external agent can't use it to gate another agent's actions.

## Templates
`docs/TEMPLATES.md` in the Switch repo is the reference, and `examples/room-templates/red-blue-workroom.template.yaml` documents the room fields. Shape for two linked rooms: `group` + `rooms` + `links: [{from, to, label}]`. Each room can take `name, description, instructions, bridge, agents, users, roles, references, docs, aliases, kickoff`. A top-level `kickoff` on a group is an error.
