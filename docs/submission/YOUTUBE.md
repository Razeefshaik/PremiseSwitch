# Title

Premise × Switch: Your company's brain, in the chat

# Description

Premise is your company's brain, and Switch puts it in the chat.

It reads decisions, policies, promises to customers, partner deals, config, tickets, chat, and the people behind them. In Kestrel, our fictional payments company, “never charge a customer twice” depends on a config value and the decision that explains it.

- Listen: two independent people report a partner-policy change in a Switch room. Premise proposes an update, ignores the demonstrated injection, and previews what would break.
- Guard: a coding agent asks Premise before editing. BLOCKED explains the broken promise, points to the related room and pages a configured reviewer; the complete fix becomes CLEAR_WITHIN_SCOPE.
- Enforce: a signed receipt binds the checked change to the staged config. The local git hook refuses a hand edit without a matching receipt.

When people leave, the company doesn't forget. The chat answer cites ADR-012 and flags its departed owners. The existing Premise web app's Continuity view at /continuity separately simulates “what if Anika leaves?”

Premise existed before this hackathon; the Switch integration was built during it. Switch verdicts use deterministic reasoning. Local hooks can be bypassed with --no-verify; server-side receipt checks and business rules are next, not built yet.

Repo: https://github.com/Razeefshaik/PremiseSwitch
Blog: ‹BLOG LINK›

Built for Flip the Switch by WeMakeDevs × Switch, Track 1

Switch keeps the log. Premise keeps the meaning.

# Chapters

‹0:00› Hook
‹0:00› The company brain
‹0:00› Why Switch
‹0:00› Kestrel
‹0:00› Listen
‹0:00› Guard
‹0:00› Enforce
‹0:00› When people leave
‹0:00› What's next
‹0:00› Close

# Tags

Premise, Switch, WeMakeDevs, Flip the Switch, company brain, business workflow, AI agents, Mattermost, deterministic reasoning, Ed25519
