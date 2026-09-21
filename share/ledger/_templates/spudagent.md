---
id: "01"
name: Name
persona: engineer
model: opus
parent: "[[Spud]]"
ticket: "[[{{ticket_prefix}}-nnn]]"
status: planned
spawned: ""
finished: ""
duration_ms: 0
tool_uses: 0
tokens_out: 0
tokens_in: 0
tokens_cached: 0
cost_usd: 0
tags: [spudagent]
---
<!-- the shape of a rendered member note: `spud render` writes ledger/teams/{{team_prefix}}-nnn/<Name>.md from the database; nobody edits the rendered file. The frontmatter is the parent's: id and name from `spud member new`, status and the timestamps from the hooks and `member finish`; duration_ms, tool_uses and tokens_out, tokens_in, tokens_cached come from the hooks' record of the run (the tokens only from the member's transcript sum), and cost_usd is that sum at the API list price, computed at render from spud.config.json `pricing`; each is left out until known -->
# Name (01, engineer) — {{ticket_prefix}}-nnn

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
<!-- the parent: `spud --as <parent> member finish {{team_prefix}}-nnn/<Name> --status done|blocked|failed --outcome "…" --summary "…"`, which also sets status and finished; the summary is this member's line on the ticket's Team card: one or two sentences, what it built or changed and where -->
