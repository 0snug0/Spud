---
tags: [home]
---
# Spud's ledger

Start here. [[Spud]] is the root of the tree. Every ticket is a note under `ledger/tickets/`, and every spudagent that worked on a ticket is a note inside that ticket's folder.

## Where to look
- [[BOARD]] for what is active, queued, and done
- [[Fleet.base|Fleet]] for every spudagent: persona, model, status, parent, ticket
- [[Tickets.base|Tickets]] for every ticket by priority and status, plus the daily reports
- `reports/` holds one note per day, newest at the bottom of the file

## How to read a name
`Kestrel (01, writer, opus)` is name, ID, persona, model tier.
- The **name** is unique within one ticket's team and may appear again on another ticket, which is why links are written `[[SPUD-001/Kestrel|Kestrel]]`.
- The **ID** is the position in that ticket's tree: `01` is Spud's first child on the ticket, `01.02` is that child's second child.
- The **persona** says what kind of teammate it was: researcher, architect, reviewer (fable); engineer, designer (opus); writer (sonnet); scout (haiku).

## How to read a ticket note
Brief, then Size, persona and model decision, then Team (the tree, indented), Handoffs, Proposals received, Outcome. The properties panel shows priority (P0 now, P1 next, P2 soon, P3 someday), status (queued, active, done, declined), origin (Eric, or a proposal from a spudagent), and lead.

## How to read a spudagent note
Brief is written by its parent before it starts. Log, Sub-agents, Ticket proposals, and Result (or Blocked) are written by the spudagent while it works. Outcome is the parent's verdict afterwards. The status property (active, done, blocked, failed) and the timestamps are the parent's.

## Graph view
Filter on `path:ledger`. The `parent` and `ticket` properties are links, so Spud, the leads, and their children draw themselves as a tree.
