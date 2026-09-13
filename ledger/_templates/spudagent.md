---
id: "01"
name: Name
persona: engineer
model: opus
parent: "[[Spud]]"
ticket: "[[SPD-nnn]]"
status: planned
spawned: ""
finished: ""
tags: [spudagent]
---
<!-- the shape of a rendered member note: `spud render` writes ledger/teams/SPUD-nnn/<Name>.md from the database; nobody edits the rendered file. The frontmatter is the parent's: id and name from `spud member new`, status and the timestamps from the hooks and `member finish` -->
# Name (01, engineer) — SPD-nnn

## Brief
<!-- the parent, at `spud member new --brief @- --deliverable …` (or `member edit --brief` before a re-spawn) -->
Objective, deliverables (globs), read first, limits, done when.

## Log
<!-- the member: `spud --as <agent_id> member log "…"`, one line per call, stamped by the CLI -->

## Sub-agents
<!-- rendered from the children the member planned with `member new`: folder-qualified link, id, persona, tier -->

## Ticket proposals
<!-- the member: `spud --as <agent_id> proposal file --title … --why … --evidence … --priority P? -->

## Result
<!-- the member: `spud --as <agent_id> member result "…"` (what you produced, where, what you verified, what is left), or `member block "…"` which renders as "## Blocked" with the question and options. The SubagentStop hook holds a member once if neither is recorded. -->

## Outcome
<!-- the parent: `spud --as <parent> member finish SPUD-nnn/<Name> --status done|blocked|failed --outcome "…" [--summary "…"]`, which also sets status and finished -->
