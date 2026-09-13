---
id: SPD-nnn
title: ""
priority: P2
status: queued
origin: eric
proposed_by: ""
lead: ""
created: YYYY-MM-DD
tags: [ticket]
---
<!-- the shape of a rendered ticket note: `spud render` writes ledger/tickets/SPD-nnn.md from the database; nobody edits the rendered file -->
# SPD-nnn — title

## Brief
<!-- `spud --as spud ticket new --title … --priority … --status … --brief @-`; later `ticket edit --brief` -->
What Eric asked for, in Spud's words. Done when: …

## Size, persona and model decision
<!-- `--sizing` on ticket new or ticket edit -->
Small (one spudagent) or large (a lead with a team). Persona and tier per member, with a reason for any tier override.

## Team
<!-- rendered from the members `spud member new` planned; the first root member is the lead -->
- [[SPUD-nnn/Name|Name]] (01, persona, tier)
  - [[SPUD-nnn/Child|Child]] (01.01, persona, tier)

## Handoffs
<!-- `spud handoff add --ticket SPD-nnn --from … --to … --what … [--path …]` -->
Dated lines: who handed what to whom, and where it lives.

## Proposals received
<!-- rendered from `spud proposal file` and `spud proposal decide` -->
Per proposal: title, origin, decision (created as SPD-mmm at P? | declined: reason).

## Outcome
<!-- `ticket edit --outcome …`, then `ticket move --status done` -->
What shipped, where, what was verified, what is left.
