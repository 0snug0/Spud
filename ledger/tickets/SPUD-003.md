---
id: SPUD-003
title: "Protocol fixes from the first end-to-end run: depth-cap wording, status/finished ownership, real timestamps"
priority: P2
status: done
origin: proposal
proposed_by: "[[SPUD-001/Kestrel]]"
lead: ""
created: 2026-09-12
tags: [ticket, protocol, meta]
---
# SPUD-003 — Protocol fixes from the first end-to-end run

## Brief
Three defects in the protocol documents, all surfaced by [[SPUD-001]]. Items 1 and 2 were proposed by [[SPUD-001/Kestrel|Kestrel]]; item 3 is Spud's own finding while recording Kestrel's outcome. All three change Spud-owned files (`CLAUDE.md`, `.claude/agents/spudagent.md`, the ledger templates), so per Law 1 Spud makes the edits himself, and only once Eric has decided items 2 and 3 and said go.

1. **Depth-cap wording.** `.claude/agents/spudagent.md` says "If the Agent tool refuses because the depth limit is reached, log the refusal". In practice the tool is removed from the toolset at the cap; the call fails with `Error: No such tool available: Agent. Agent is disabled for this session, in subagents as well as here.` (misleading: level 1 could spawn in the same session). Fix: state that a missing `Agent` tool *is* the depth limit, log it, do not retry or hunt via `ToolSearch`, do the part yourself. Evidence: [[SPUD-001/Huckleberry]] Log, [[SPUD-001/Ozette]] Outcome. No decision needed; wording only.

2. **Who owns `status` and `finished`.** CLAUDE.md step 7 and the Ledger v0 ownership list give them to the parent; `.claude/agents/spudagent.md` "Finishing" tells the spudagent to set them itself. In SPUD-001 every member self-marked `done` before its parent judged the work. Failure mode: a child returning partial work marked `done`, and the one-writer-per-file rule silently broken on two fields. Options: (a) parent owns both, delete the line from the agent definition; (b) child owns `finished`, parent owns `status` (Kestrel's view). **Spud's recommendation: (a)**, because of item 3: the child's `finished` is a guess anyway, and the parent records the return time from the notification. Eric decides.

3. **Timestamps are guessed.** Nobody in the team ran `date`. All frontmatter times were fabricated and in the future (`spawned: 12:41`, `finished: 13:05`, `13:00`, `13:12`, and one `00:00` placeholder) against a real window of 12:25 to 12:31. Fix: the brief template and the agent definition require every timestamp to come from `date "+%Y-%m-%dT%H:%M"`, never from memory; and, if (a) above is chosen, only parents write `spawned` and `finished`. Evidence: [[SPUD-001/Kestrel]] Outcome, file mtimes.

Done when: the three documents agree, the brief template carries the `date` rule, and a future ticket's team files show real clock times and parent-written status.

## Size, persona and model decision
No spudagent: the deliverables are Spud's own files, which Law 1 reserves to Spud (edited only when Eric asks or the protocol requires). Eric's go-ahead on item 2 is the trigger. If Eric would rather a spudagent draft the wording for review, an architect on fable is the right persona, since this is a ledger-integrity rule.

## Team

## Handoffs

## Proposals received

## Outcome
Resolved by Eric's bootstrap session at 2026-09-12T12:36, not by a spudagent: the three defects were contradictions in files written during the bootstrap, so fixing them completed the bootstrap rather than starting new work.

Decisions: item 2 → option (a), the parent owns `status`, `spawned`, and `finished`; a spudagent signals only through its `## Result` or `## Blocked` sections. A fourth status, `failed`, was added for a child that never ran or whose work the parent rejects. Item 1 → the agent definition now says the `Agent` tool is absent at the cap, quotes the real error, forbids retrying or writing a child's file when the brief says no sub-agents, and marks a child whose spawn failed as `failed`. Item 3 → CLAUDE.md, the agent definition, the brief template, and the member template all require timestamps from `date "+%Y-%m-%dT%H:%M"`; `.claude/settings.json` now allowlists `date`, `ls`, and read-only `git` so sessions do not prompt for them.

Files changed: `CLAUDE.md`, `.claude/agents/spudagent.md`, `.claude/settings.json`, `ledger/tickets/_TEMPLATE/_TEMPLATE.md`, `docs/superpowers/specs/2026-09-12-spud-design.md`, `.gitignore` (`.obsidian/` ignored wholesale). Left: nothing; the next ticket's team files are the proof.
