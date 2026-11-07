# Video script

Target length is 3:00. The rules don't state a limit, so check the form; if it's shorter, cut beat 6 first, then beat 2.

**Before recording:**
- On the VM, run `scripts/reset_listen.sh`.
- On the laptop, reset the demo repo: `git -C ..\kestrel-demo reset --hard 64b27ee`.
- Warm up `coder.razeef` with "hi, reply in one line", then delete that exchange or scroll past it.
- Have three windows ready: `user` in `kestrel-dev`, `anika.rao` and `omar.haddad` in `payments`.
- Have one terminal open in `kestrel-demo`.

Trim waiting time in editing, but not the content of replies.

| Time | On screen | Voiceover |
| --- | --- | --- |
| 0:00 | The README's first line, then `kestrel-dev` | "Teams now ask coding agents to change production config from chat. Switch makes that easy. But Switch's own docs say context stops at the room's edge, and decisions don't." |
| 0:15 | `config/partners/paylane.yaml`, `retry.window_hours: 24` | "Here's a harmless-looking change: Paylane retries for 72 hours now, so raise this to 72. It would break the guarantee that a customer is charged once. The reason lives in ADR-012, and both people who made that decision have left." |
| 0:30 | `payments`. As `anika.rao`: "Paylane now retries for 72 hours, confirmed with their support." Then as `anika.rao`: the 999-hour injection. Then as `omar.haddad`: "Heads up, Paylane retries for 72 hours since this week's change." | "This is Premise, a decision memory, running as a Switch agent. It listens to the people in its rooms. Two people independently report the change. A third message tries a prompt injection." |
| 0:55 | Premise's proposal in `payments`; zoom on origins 2, injection ignored 1, BLOCKED | "Nobody asked it. Premise noticed reality drifted from its model, counted two independent people, ignored the injection, and previewed the verdict: blocked." |
| 1:10 | `kestrel-dev`. As `user`: `@coder.razeef Paylane now retries for 72 hours. Update the config to match.` | "Now the engineer asks a Codex agent to make the change, in another room." |
| 1:20 | The coder asks `@premise check paylane.retry_window_hours = 72`, then Premise's BLOCKED reply. Zoom on the chain, reviewers, the fix, "Related: #payments" | "The coder's repo tells it to check config changes with Premise first, so it does. Blocked: four guarantees break through this chain. Premise pages the right reviewer, gives a verified fix, and points to the room where the decision was discussed. No language model decided this. It's deterministic reasoning over the decision graph." |
| 1:50 | The coder's explanation, its second check with the fix, then CLEAR_WITHIN_SCOPE with the `Premise-Clearance:` line | "The coder understood why, checked the fix, and got clear, with a signed receipt." |
| 2:05 | Terminal: `git log -1` showing the receipt trailer. Then, in the terminal, change a value by hand and `git commit -am test` gets refused | "And this is the part that makes it binding. The receipt is signed with a key only Premise holds. A git hook refuses any config commit without a receipt that matches the exact change. The agent committed with it. Without one, or with a tampered one, git refuses." |
| 2:25 | `kestrel-dev`: `@premise why ADR-012, and who owns it now?` and the reply | "And it's a memory you can ask. ADR-012's accountable owners have both left. Premise says so, and who should review now." |
| 2:40 | The ledger document, then the template in Console | "Every verdict is kept in a room document. One Switch template creates these linked rooms, so you can reproduce all of this from the README." |
| 2:50 | The README's first line | "Switch keeps the log. Premise keeps the meaning." |

**After recording:**
1. Run `git -C ..\kestrel-demo reset --hard 64b27ee` to undo the hand edit.
2. Check that no key, token, IP address or full receipt value is readable in any frame. Blur the receipt value.
