# premise-switch: plan

Deadline: 7 Nov 2026, 23:59 IST (aim for 23:00). All times IST.

## Milestones

| Time | Done means |
| --- | --- |
| 13:30 | M0: new repo pushed; Premise answers the canonical check on the VM; live protocol checked |
| 15:00 | M1: a mention in Mattermost echoes in the thread; restart reconnects without re-registering; a replay gives no second reply |
| 17:00 | M2: BLOCKED and CLEAR work in a room, and the coder-to-Premise handoff wakes both sides with no ping-pong |
| 19:00 | M3, hard cut: `payments` messages produce the proposal, or Listen is reverted and listed as "next" |
| 21:00 | Feature freeze. Clearance and Extras merged only if they pass live. README and walkthrough done, setup steps followed literally |
| 22:15 | Rehearsed and recorded |
| 23:00 | History scanned for secrets, repo public, submitted |

## Work split

| Who | Takes |
| --- | --- |
| Razeef | Azure, Console, GitHub repo, Mattermost accounts, assigning cards, video, form |
| Claude | C01 foundation, VM and live checks, reviewing and merging every branch, C09 wiring, README, walkthrough |
| Razeef's Codex | C02 client, C04 listen, then C06 |
| Teammate's Codex | C03 answer and guard, C05 repro pack, then C07 |
| Whichever is free after merge | C08 adversarial review |

## Order

```
C01 (foundation) ─┬─ C02 client ─────────┐
                  ├─ C03 answer + guard ─┼─ C09 wiring ─ live M1, M2, M3 ─ README ─ video
                  ├─ C04 listen ─────────┤
                  └─ C05 repro pack ─────┘
 after C03: C06 clearance    after C02 + C03: C07 extras    before freeze: C08 review
```

## Design notes
See `docs/HANDOFF.md`, `docs/CONTRACTS.md` and `docs/SWITCH_PROTOCOL.md`.
