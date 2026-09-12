---
id: "01"
name: Vitelotte
persona: researcher
model: fable
parent: "[[Spud]]"
ticket: "[[SPD-006]]"
status: active
spawned: 2026-09-12T13:19
finished: ""
tags: [spudagent]
---
# Vitelotte (01, researcher) — SPD-006

## Brief
<!-- written by the parent before spawn -->
**Objective.** Run the ledger database spike. Answer the eight questions below with evidence and write the spike document that decides what replaces the ledger's `markdown-v0` format. The canonical brief is Appendix A of `docs/superpowers/specs/2026-09-12-spud-design.md`; the questions are restated here so this file stands on its own if you are compacted. The spike decides; it does not implement.

**Deliverables (only these paths).**
- `docs/spikes/2026-09-12-ledger-database.md`, with these sections in this order: Recommendation, Options considered, Topology, Proposed schema (DDL in a code block is spec, not code), Concurrency model, Migration plan, Language recommendation, Enforcement plan, Ticket proposals, Open questions for Eric.
- `ledger/teams/SPUD-006/<ChildName>.md` for any child you spawn (its Brief and Outcome are yours).
- Your own file's Log, Sub-agents, Ticket proposals, Result.
- No code files in the repo. Throwaway experiments (timing scripts, scratch databases, concurrency probes) are encouraged, but run them outside the repo, under `$TMPDIR` or a scratch directory of your own, and quote their results in the spike as evidence.

**Read first.** `spud.config.json`; `CLAUDE.md` (Laws, protocol, Ledger v0, brief template); `.claude/agents/spudagent.md`; `.claude/settings.json` (the env caps and the allowlist); `docs/superpowers/specs/2026-09-12-spud-design.md` in full (Decisions, Design, Appendix A); `ledger/Home.md`, `ledger/Spud.md`, `ledger/Board.base`, `ledger/Fleet.base`, `ledger/_templates/ticket.md`, `ledger/_templates/spudagent.md`; `ledger/tickets/SPD-001.md` with every file in `ledger/teams/SPUD-001/` (the richest record the store must hold: a three-level tree, a spawn that never ran, handoffs, proposals climbing two levels, a tier override with a reason); `ledger/tickets/SPD-003.md` and `SPD-005.md` (ownership and Obsidian decisions already made); `reports/2026-09-12.md`. Prior art, reference only and not a design to inherit: `/Users/ericlugo/Personal/Minilla/schema.sql` and `/Users/ericlugo/Personal/Minilla/CLAUDE.md`. The spec's one-line lesson from Minilla stands: a long-lived orchestrator gets compacted and forgets, a file on disk does not; tickets are the plan, events are the memory, rules are enforced by the tool rather than by prose.

**The eight questions.** Each needs an evidenced answer and one recommendation, not a menu.

1. **Which store.** SQLite versus alternatives: JSONL or markdown as-is, DuckDB, local Postgres, anything else worth naming. Criteria: single machine, no daemon, survives compaction, human-inspectable, Obsidian-renderable views. Obsidian is a hard requirement: Eric reads the ledger there, through frontmatter, wikilinks, and Bases views.
2. **Topology.** One ledger versus per-project versus per-spudagent databases. Define "project" in Spud's world: this repo (Spud's home) versus repos Spud works on, such as BadTakes. Cover where the database file lives, whether it is committed or ignored in git, and how a spudagent working in another repo or a worktree reaches it.
3. **Schema.** Tickets (priority, status, origin, proposed_by, lead); teams and spudagents as a tree per ticket (id, name unique per team, persona, model, parent, status, timestamps, tokens and duration as returned by the Agent tool); contractors (`persona: contractor`, `agent_type`); events (append-only); handoffs; proposals and their decisions; projects; the name pool. Show how `SPUD-002/01.01` maps to rows, and how each limit in `spud.config.json` (max_depth, root_fan_out, child_fan_out, max_concurrent_total) is checked from the tables.
4. **Concurrency.** N in-process spudagents writing at once. WAL plus busy timeouts versus a single writer behind a CLI or MCP tool. Recommend one.
5. **Migration.** Schema evolution over time; how the existing `ledger/` markdown-v0 files migrate in; whether the markdown files become generated views so Obsidian keeps working (frontmatter, wikilinks, Bases), and what that does to the one-writer-per-file rule.
6. **Glue language** for the CLI, hooks, and MCP server: Python 3.14 with stdlib `sqlite3`, Node 26 with `node:sqlite`, Bun with `bun:sqlite`. Hook startup latency matters and must be measured on this machine, not quoted. Stdlib-only preference. One recommendation.
7. **Enforcement.** Which laws move from prose into the tool: limits (Law 4), ownership (Law 5), ticket authority (Law 6), no commits by spudagents (Law 7), explicit model and no `fork` (Law 3), and state moves. How `SubagentStart`, `SubagentStop`, and `PreToolUse` feed it, including the `agent_id`-to-spudagent-name mapping problem (hooks carry `agent_id`, the ledger carries names and lineage IDs).
8. **Fleet view and reports.** What they read from. Eric's stated need, 2026-09-12: click a ticket and see the lead, every spudagent under it with persona, model, and status, and a summary of what they worked on and built. Today the ticket note holds this as a working log. Propose what generates a readable card per ticket from the chosen store: Obsidian Bases embeds, generated markdown, or a UI.

**Evidence standards.** Measure, do not assume: which runtimes are present and their versions; cold-start latency of a minimal script per candidate runtime that opens a SQLite database and exits (several runs, report the median, state the method); WAL behaviour under concurrent writers (busy_timeout, how often SQLITE_BUSY surfaces). Verify native Claude Code facts (hook event payloads, what `SubagentStart` and `SubagentStop` carry, whether `agent_id` can be joined to anything a parent knows, what the Agent tool returns about tokens, duration, and tool uses) against official documentation, and cite the page. Spawning at least one child has a side benefit: you observe first-hand what the Agent tool returns, which question 3 needs; quote that metadata verbatim in your Log. Where evidence is missing, say so rather than fill the gap with a plausible sentence.

**Limits.** Up to 2 sub-agents, 1 level remaining below you (your children get 0, so their Limits line says "no sub-agents"), same protocol, IDs `01.01` and `01.02`, names from the `spud.config.json` pool not already used as a filename in `ledger/teams/SPUD-006/`. Whether to spawn at all is your call; suggested uses are a scout on haiku for the Minilla survey or the measurements, or a `claude-code-guide` contractor for native facts, recorded exactly like a spudagent with `persona: contractor`, `agent_type: claude-code-guide`, and an explicit model. Every spawn: file first, `subagent_type: "spudagent"` (or the contractor's type), explicit `model`, `description: "<Name> (<id>, <persona>)"`, never `isolation: worktree`, never `subagent_type: "fork"`. When a child returns, write its `## Outcome` and set its `status` and `finished` from `date`.

**Proposals and questions.** Follow-up work the spike implies (implementation tickets, hook work, migration) goes under your `## Ticket proposals` in full (title, why, evidence, suggested priority); the spike's own Ticket proposals section carries the same entries, a copy is fine. Questions only Eric can answer go in the spike's Open questions for Eric. Block only if a question prevents you from making the recommendation at all; otherwise recommend under stated assumptions and list the questions.

**Done when.** The spike file exists with all ten sections in the order above; each of the eight questions has an evidenced answer and one recommendation; measurements state they were taken on this machine and how; sources are cited; any child's file has Outcome, status, and finished set by you; your `## Result` says what you produced, what you verified, and what is open; your return is at most 10 lines.

**Rules.** No `git commit`, `git add`, stash, or branch changes; Spud commits. No tickets, no edits to any ticket file, `spud.config.json`, `CLAUDE.md`, `.claude/`, the spec, or any `ledger/` file outside `ledger/teams/SPUD-006/`. The frontmatter of your file is Spud's. Every timestamp from `date "+%Y-%m-%dT%H:%M"`, never guessed. Out-of-scope work goes under `## Ticket proposals`. A decision only a human can make goes under `## Blocked`, then return. Log progress in `## Log` as dated lines.

## Log
<!-- the spudagent: dated lines -->

## Sub-agents
<!-- the spudagent: one line per child, folder-qualified link, id, persona, tier -->

## Ticket proposals
<!-- the spudagent: title / why / evidence / suggested priority -->

## Result
<!-- the spudagent: what you produced, where, what you verified, what is left. Or "## Blocked" with the question and options. Leave the frontmatter to your parent. -->

## Outcome
<!-- written by the parent after return; the parent also sets status (done | blocked | failed) and finished, both from date -->
