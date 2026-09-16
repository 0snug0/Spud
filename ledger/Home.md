---
tags: [home]
---
# Spud's ledger

Start here. [[Spud]] is the root of the tree. Every ticket is a note under `ledger/tickets/`, and every spudagent that worked on it is a note under `ledger/teams/SPUD-nnn/`, the team keyed to ticket `SPD-nnn`.

## Where to look
- [[Board.base|Board]] for every ticket with its status, priority, and lead, plus a Reports tab
- [[Fleet.base|Fleet]] for every spudagent: persona, model, status, parent, ticket
- `reports/` holds one note per day, newest at the bottom of the file
- [[Projects]] for every repository Spud works in: its ticket and team prefixes, root, landing and sessions

## How to read a name
`Kestrel (01, writer, opus)` is name, ID, persona, model tier. A ticket key's prefix names its project: `SPD-nnn` (team `SPUD-nnn`) is Spud's own repository, `BAD-nnn` (team `BADS-nnn`) is BadTakes; every note also carries a `project` property.
- The **name** is unique within one ticket's team and may appear again on another ticket, which is why links are written `[[SPUD-001/Kestrel|Kestrel]]`.
- The **ID** is the position in that ticket's tree: `01` is Spud's first child on the ticket, `01.02` is that child's second child.
- The **persona** says what kind of teammate it was: researcher, architect, reviewer (fable); engineer, designer (opus); writer (sonnet); scout (haiku).

## How to read a ticket note
Brief, then Size, persona and model decision, then Team (a table of who ran as what, on which model, and how it went; the tree, indented, with what each member worked on and built; and the live Team view with totals), Handoffs, Proposals received, Outcome. The properties panel shows priority (P0 now, P1 next, P2 soon, P3 someday), status (queued, active, parked, done, declined; a parked ticket also shows parked_until and parked_reason), origin (eric or proposal), proposed_by for proposals, and lead.

In the Team table, Run is the member's own run, from spawn to return, with its measured length. Tokens are out (what the member wrote), in (input read fresh) and cached (input re-read from the prompt cache), summed from the member's own transcript. Cost (list) is that run at the API list price in USD, from the dated price table in `spud.config.json`: what the same tokens would cost on the API, never your subscription's bill. The Total row sums the ticket, and says partial when a run it covers has no price. A dash means nothing was recorded, as for the tickets imported from markdown, or no price for it.

## How to read a spudagent note
Brief is written by its parent before it starts. Log, Sub-agents, Ticket proposals, and Result (or Blocked) are written by the spudagent while it works. Outcome is the parent's verdict afterwards. The status property (active, done, blocked, failed) and the timestamps are the parent's.

## Graph view
Filter on `path:ledger`. The `parent` and `ticket` properties are links, so Spud, the leads, and their children draw themselves as a tree.
