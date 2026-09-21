---
id: {{ticket_prefix}}-nnn
title: ""
priority: P2
status: queued
origin: eric
proposed_by: ""
lead: ""
created: YYYY-MM-DD
tags: [ticket]
---
<!-- the shape of a rendered ticket note: `spud render` writes ledger/tickets/{{ticket_prefix}}-nnn.md from the database; nobody edits the rendered file -->
<!-- while parked (`ticket move --status parked --reason … [--until YYYY-MM-DD]`) the frontmatter carries `parked_until` and `parked_reason` right after `status`; every other note has neither -->
# {{ticket_prefix}}-nnn — title

## Brief
<!-- `spud --as spud ticket new --title … --priority … --status … --brief @-`; later `ticket edit --brief` -->
What Eric asked for, in {{identity_name}}'s words. Done when: …

## Size, persona and model decision
<!-- `--sizing` on ticket new or ticket edit -->
Small (one spudagent) or large (a lead with a team). Persona and tier per member, with a reason for any tier override.

## Team
<!-- rendered from the members `spud member new` planned: the table (the lead's subtree first, then lineage order; Cost (list) is a run at the API list price from spud.config.json `pricing`, and a Total row ends the table), the tree with what each member worked on and built (the parent's `member finish --summary`, else the first paragraph of its Result that reads as work, else its final message), and the Fleet.base Team view -->
| Member | ID | Persona | Model | Status | Run | Tokens | Cost (list) | Tools |
|---|---|---|---|---|---|---|---|---|
| [[{{team_prefix}}-nnn/Name\|Name]] | 01 | persona | tier (resolved model) | done | HH:MM → HH:MM · N min | Nk out · N.NM in · N.NM cached | $N.NN | N |
| ↳ [[{{team_prefix}}-nnn/Child\|Child]] | 01.01 | persona | tier | active | HH:MM → | — | — | — |
| **Total** |  |  |  |  |  | Nk out · N.NM in · N.NM cached | $N.NN | N |

- [[{{team_prefix}}-nnn/Name|Name]] (01, persona, tier) — what it built or changed and where, in one or two sentences
  - [[{{team_prefix}}-nnn/Child|Child]] (01.01, persona, tier)

![[Fleet.base#Team]]

## Handoffs
<!-- `spud handoff add --ticket {{ticket_prefix}}-nnn --from … --to … --what … [--path …]` -->
Dated lines: who handed what to whom, and where it lives.

## Proposals received
<!-- rendered from `spud proposal file` and `spud proposal decide` -->
Per proposal: title, origin, decision (created as {{ticket_prefix}}-mmm at P? | declined: reason).

## Outcome
<!-- `ticket edit --outcome …`, then `ticket move --status done` -->
What shipped, where, what was verified, what is left.
