---
tags: [home]
---
# {{identity_name}}'s ledger

Start here. [[Spud]] is the root of the tree. Every ticket is a note under `ledger/tickets/`, and every spudagent that worked on it is a note under `ledger/teams/{{team_prefix}}-nnn/`, the team keyed to ticket `{{ticket_prefix}}-nnn`.

## Where to look
- [[Board.base|Board]] for every ticket with its status, priority, and lead, plus a Reports tab
- [[Fleet.base|Fleet]] for every spudagent: persona, model, status, parent, ticket
- `reports/` holds one note per day, newest at the bottom of the file
- [[Projects]] for every repository {{identity_name}} works in: its ticket and team prefixes, root, landing and sessions

This note, [[Spud]], the two `.base` files and the templates under `ledger/_templates/` are generated from `{{tool}}/share/`, the tool's own copies: `spud --as spud home sync` writes each of them again, keeping a copy of whatever it replaces, so a change to them is a ticket in the tool repository rather than an edit here. The views in [[Board.base|Board]] and [[Fleet.base|Fleet]] are the tool's for the same reason — a view added inside either file is replaced at the next sync, while a `.base` file of your own is nobody's to regenerate and survives.

## How to read a name
`Kestrel (01, writer, opus)` is name, ID, persona, model tier. A ticket key's prefix names its project: `{{ticket_prefix}}-nnn` (team `{{team_prefix}}-nnn`) is project `{{project_key}}`; every note also carries a `project` property.
- The **name** is unique within one ticket's team and may appear again on another ticket, which is why links are written `[[{{team_prefix}}-001/Kestrel|Kestrel]]`.
- The **ID** is the position in that ticket's tree: `01` is {{identity_name}}'s first child on the ticket, `01.02` is that child's second child.
- The **persona** says what kind of teammate it was: researcher, architect, reviewer, engineer, designer (opus, by default at high effort); writer (sonnet, medium); scout (haiku, which takes no effort). A member planned on another tier says why in its tier reason, fable for a review of the hook path, the ledger's schema or security, and for the one escalation after a failure on opus.
- The **effort** property is the level its parent planned it at, from low to max; it is left out where there is none.

## How to read a ticket note
Brief, then Size, persona and model decision, then Team (a table of who ran as what, on which model, and how it went; the tree, indented, with what each member worked on and built; and the live Team view with totals), Handoffs, Proposals received, Outcome. The properties panel shows priority (P0 now, P1 next, P2 soon, P3 someday), status (queued, active, parked, done, declined; a parked ticket also shows parked_until and parked_reason), origin (owner or proposal), proposed_by for proposals, and lead.

In the Team table, Run is the member's own run, from spawn to return, with its measured length. Tokens are out (what the member wrote), in (input read fresh) and cached (input re-read from the prompt cache), summed from the member's own transcript. Cost (list) is that run at the API list price in USD, from the dated price table in `spud.config.json`: what the same tokens would cost on the API, never your subscription's bill. The Total row sums the ticket, and says partial when a run it covers has no price. A dash means nothing was recorded, or no price for it.

## How to read a spudagent note
Brief is written by its parent before it starts. Log, Sub-agents, Ticket proposals, and Result (or Blocked) are written by the spudagent while it works. Outcome is the parent's verdict afterwards. The status property (active, done, blocked, failed) and the timestamps are the parent's.

## Graph view
Filter on `path:ledger`. The `parent` and `ticket` properties are links, so {{identity_name}}, the leads, and their children draw themselves as a tree.
