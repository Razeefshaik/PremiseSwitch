# Project name

Premise × Switch

# Tagline

Your company's brain, in the chat.

# Track

Track 1: Best Business Workflow

# What it does

Premise is your company's brain, and Switch puts it in the chat. It reads and organises decisions, policies, promises to customers, partner deals, config, tickets, chat, and the people behind them. It connects what the company promised to the reasons, people and values that keep those promises true.

Our demo uses Kestrel, a fictional payments company. “Never charge a customer twice” depends on keeping deduplication records longer than a partner can retry a payment notification. Raising Paylane's retry window from 24 to 72 hours looks like one harmless config edit. On its own, it breaks four tracked guarantees.

Premise joins the team's Switch rooms and does four jobs:

- **Listen:** two independent human reports that Paylane now retries for 72 hours produce a proposal, with two origins, an ignored injection-like message and a BLOCKED verdict preview. Proposals invite review; they do not rewrite the model. Agent messages and addressed commands are excluded from evidence.
- **Guard:** the coding agent asks `@premise check paylane.retry_window_hours = 72` before editing. Premise returns BLOCKED, explains the dependency chain, suggests reviewers, pages configured human handles and links the related payments room. Checking the complete fix, including both required TTL increases, returns CLEAR_WITHIN_SCOPE.
- **Enforce:** with clearance enabled, a successful check carries an Ed25519-signed receipt for the exact file, key and value changes. A local `commit-msg` hook compares it with HEAD-to-index changes and refuses missing, mismatched, expired or tampered receipts. The repo has only the public key; trusted hook installation is required and `--no-verify` bypasses it.
- **Remember:** asking why ADR-012 exists and who owns it now returns cited lineage, flags that Lena Fischer and Marco Ruiz have left, and lists reviewers without appointing new owners. The original Premise web app's Continuity view at `/continuity` separately simulates “what if Anika leaves?” and shows which promises lose verification and who reviews next. That simulation is not a chat command.

No language model decides a Premise Switch verdict. Failed, incomplete and untracked checks remain explicitly unchecked.

# How Switch is used

Switch is where this workflow runs: people report changed reality, agents ask before acting, and the checked answer stays with the conversation.

- **Custom registration:** the `premise` agent registers explicitly once, saves its identity and private credentials locally, and reuses them on restart. It never automatically re-registers or widens its own addressing.
- **One WebSocket per room:** the client discovers membership through `list_rooms` and keeps a stable connection for each room it joins, within Switch's 32-connection limit. It handles heartbeats before work, persists cursors and generations, deduplicates replays, and backfills gaps through `read_context`.
- **Room replies:** `post_message` sends ordinary threaded replies and proposals. `send_targeted_message` wakes only the agent that asked an explicit command. Agent chatter stays silent, and replies are capped per thread. Configured human reviewer mentions route attention to the people who should review.
- **Linked rooms:** `list_linked_rooms` lets a blocked answer in `kestrel-dev` point to `payments`. The listener can count evidence across its rooms, but a proposal quotes only evidence from the receiving room.
- **Room documents:** `create_room_document` and `update_room_document` keep a shared “Premise decision ledger” in each room. Updates are batched, and command summaries are redacted before retention.
- **Group template:** `templates/kestrel.group.yaml` creates `payments` and `kestrel-dev`, their descriptions and instructions, memberships referencing existing agents, and the link between the rooms. The owner selects the coding agent.
- **Mattermost bridge:** our live demonstration uses Switch rooms bridged to Mattermost. Transport senders are resolved against room participants, including `sender_name`, so bridged humans can contribute evidence.
- **Trust boundaries:** Switch returns 403 when an agent tries to change its own settings or addressing; the owner configures access in Console. Switch mediation cannot let an external room agent gate another agent's actions, so the receipt check happens where config changes land: git.

Implementation: `premise_switch/client.py`, `state.py`, `answer.py`, `listen.py`, `extras.py` and `adapter.py`. Protocol evidence is recorded in `docs/SWITCH_PROTOCOL.md`.

# How we built it

Premise existed before the hackathon. Its company memory, deterministic reasoning, source ingestion, fictional Kestrel data and web app were already built. We added the Switch agent, transport client, routing and answer formatting, room listening, signed clearance receipts, room extras, setup tools and integration tests during this event. The dividing line is visible in Git: `f11f34e` is the last pre-hackathon commit; the integration starts at `9769606`.

We directed several Codex agents in parallel, each with a card naming its owned files and frozen interfaces in `premise_switch/contracts.py`. Branches were reviewed, tested on the server and merged with `Merge Cxx` commits. Deterministic Python tools query the FalkorDB decision graph; the Switch listener runs with `llm_provider="none"`. Receipts use Ed25519 signatures over canonical JSON, and the standalone hook inspects staged YAML leaves rather than trusting the working tree.

# Challenges

- **Connections determine the reply room.** `post_message` takes no room argument and fails on a connection covering several rooms. We keep one WebSocket per room so threaded replies reach the intended conversation.
- **Bridged identity was misleading.** Matrix-style Mattermost transport identifiers did not equal participant IDs. Matching by `sender_name` fixed human-versus-agent classification; the resolved participant's stable ID now prevents one person counting as two origins.
- **An agent cannot grant itself access.** Self-addressing updates returned 403. We left access configuration with the owner in Console.
- **Mediation could not gate the coder.** Switch's pre-invocation mediation checks attached tools. Signed receipts and a git hook provide a separate checkpoint for the staged config change.
- **Injection crossed sentences and Unicode forms.** An instruction in one sentence originally left a numeric claim in the next sentence looking clean. Whole-message taint fixes that case. The adversarial review also found zero-width and non-breaking-space variants; the marker check now uses an NFKC-normalised, invisible-character-free view while preserving original source text for grounding and citations.
- **A plausible CLEAR can still be wrong.** Review found stacked replies attributed to the wrong command and incomplete results rendered as CLEAR. Command/reply pairing and complete result validation now keep failed checks explicitly unchecked.

The detailed design record is `docs/DECISIONS.md`; review findings and their final status are in `docs/reviews/C08-2026-11-07.md`.

# Accomplishments

The live coder read its repo instructions, asked Premise on its own, received BLOCKED, and explained that dedupe records would expire while retries remained possible. It checked the complete fix, received CLEAR, changed exactly three files and committed with a matching receipt. The hook accepted it; missing, mismatched and tampered receipts are refused.

The recorded verification checkpoint is **671 tests**, including **286 Switch tests**, with **0 gold floor failures**. The Switch tests include a fake HTTP/WebSocket server that rejects undocumented arguments; hook tests execute real Git commits. The README setup was followed literally in a fresh clone.

An adversarial review found nine issues: **8 of 9 review findings are fixed** with regression coverage. The remaining delivery issue is partly fixed with one transient retry, and the at-most-once limitation across restarts is documented. These results are repository evidence, not a claim that the system is free of defects.

# What we learned

Useful company memory needs to be present when a decision is made. Shared rooms, linked conversations, targeted replies and room documents let the team and its coding agents use the same reasons before changing anything.

Protocol details became product behavior: a connection chooses the reply room, a mention can wake the asking agent, and a transport identity affects whether evidence is independent. Uncertainty must stay visible: an absent source, failed check or missing owner cannot become permission to proceed.

Switch keeps the log. Premise keeps the meaning.

# What's next

**Business rules are next, not built yet.** A decision is a reason, an owner and a promise, so the same format fits business rules next: refund windows, pricing, partner terms. Those examples are roadmap ideas; they are not implemented or tested business rules.

We also want server-side receipt checks because local hooks can be bypassed, revision-aware receipt expiry beyond today's offline 24-hour window, and an acknowledged outbox for replies lost across restarts. Mattermost is the messaging app tested so far; other Switch apps and more evidence sources need validation.

# Built with

Python, FalkorDB, Switch agent protocol, HTTP and WebSockets, Mattermost bridge, Ed25519 through `cryptography`, PyYAML, Git hooks, pytest and Docker Compose for the documented server setup. Codex agents assisted development. The guard's verdicts and the deployed Switch listener use deterministic tools.

# Links

Repo: https://github.com/Razeefshaik/PremiseSwitch

Video: https://youtu.be/h4dNmmn163g

Blog: ‹BLOG LINK›
