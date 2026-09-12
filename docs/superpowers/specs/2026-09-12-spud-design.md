# Spud — design spec (canonical build, v0)

Date: 2026-09-12. Status: approved by Eric, implemented as the bootstrap commit.

## Purpose

Spud is a peer and second brain. Eric is Bob; Spud is the first fork, running on the best model available (`claude-fable-5-1`). Spud never does task work himself: he sizes a ticket, picks a persona and a model, and delegates to **spudagents**, his children and their children, under configurable fan-out and depth limits. He keeps a durable memory of what has been done, what is queued, and what is running, and reports on it. Only Spud creates tickets; spudagents propose, and proposals climb the tree until Spud decides.

Minilla (`~/Personal/Minilla`) is prior art only. Its code is ignored; its lesson stands: a long-lived orchestrator gets compacted and forgets, a file on disk does not. Tickets are the plan, events are the memory, rules are enforced by the tool rather than by prose.

The first real ticket after bootstrap is a research spike on the ledger database, run by a researcher spudagent on Fable 5.1 (Appendix A). This bootstrap makes that possible without pre-deciding the database or the glue language.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Spudagent runtime | Native Claude Code `Agent` tool, in-process subagents | Eric's choice; nothing to reinvent |
| Visual layer | Markdown only, shaped for Obsidian (frontmatter, wikilinks) | Eric's choice; UI waits for the DB |
| Glue language | Decided by the DB spike | Eric's choice; v0 has no code |
| Spudagent identity | Team-scoped lineage ID + a name unique within the team + persona | ID counts and orders; the name is what people and Obsidian link to; the persona says what kind of teammate it was. Names may repeat across teams |
| Ticket authority | Spud only; spudagents propose, parents absorb, decline, or escalate; Spud creates with a priority or declines with a reason | Eric's rule |
| Depth cap | `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` env (hard) | Native |
| Total concurrency cap | `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` env (hard) | Native |
| Per-parent fan-out | Prompt-level rule in `spud.config.json` (soft) in v0 | No native per-parent cap; hook enforcement needs the glue language |
| Native `subagent_type: "fork"` | Not used in v0 | Always inherits the parent model and skips the depth cap |
| Scheduling, memory dir, worktrees | Native | Do not rebuild |
| Persistent ticket store | The one thing to build: the ledger | Claude Code has no cross-session task store |

Native facts relied on: nested subagents are supported; agent frontmatter `model` accepts `sonnet|opus|haiku|fable|inherit` and the Agent tool's per-call `model` overrides it; subagents cannot use `AskUserQuestion`; `SubagentStart/Stop` hooks carry `agent_id`, not Spud names, so hook enforcement is deferred.

## Design

### Identity and precedence

Name, pronouns, and model live in `spud.config.json`; `CLAUDE.md` defers to it so a future user can rename or regender Spud. `ledger/Spud.md` is the identity card and the root node of the Obsidian graph. Inside the repo, `CLAUDE.md` supersedes the oh-my-claudecode block in Eric's global CLAUDE.md.

### Teams and spudagents

A team is every spudagent spawned for one ticket. It lives in `ledger/tickets/SPUD-nnn/`, one file per member, next to the ticket file. Each spudagent has:

- **ID**: team-scoped lineage counter (`01`, `02`; children `01.01`). Next ID = count of files in the team folder with that parent, plus one.
- **Name**: unique within the team, picked at random from `naming.pool` (potato cultivars) among names unused in the folder. Free to repeat on other teams. Alternative considered: Bobiverse self-naming (file named by ID, `aliases:` for links); not chosen because the parent must link the child before it returns.
- **Persona**: from the catalog, with a default tier. researcher, architect, reviewer (fable); engineer, designer (opus); writer (sonnet); scout (haiku). Overrides need a written reason.

Handle: `Russet (01, researcher)`; qualified: `SPUD-002/Russet`. Team-member wikilinks are folder-qualified, `[[SPUD-002/Russet|Russet]]`; ticket links are plain.

### Protocol

Ticket file → persona, tier, ID, name → spudagent file with frontmatter and `## Brief` → add to the ticket's `## Team` → `Agent(subagent_type: "spudagent", model, description: "<Name> (<id>, <persona>)", run_in_background: true, prompt: brief)` → disjoint paths for parallel work, no commits by spudagents → on return: `## Outcome`, proposals decided, ticket status updated, report appended, Spud commits. Nested parents run the same steps without creating tickets.

### Proposals and Blocked

Ticket proposals (out-of-scope work) are written in the spudagent's own file and climb the tree: absorb, decline with a reason, or escalate with `origin`. At Spud: create under `## Queued` with a P0–P3 priority and `origin: proposal` plus `proposed_by: "[[SPUD-nnn/Name]]"`, or decline under `## Proposals received`. Blocked (a human decision needed) climbs the same way; Spud asks Eric.

### Limits

`max_depth: 2` (Spud → 01 → 01.01), `root_fan_out: 3`, `child_fan_out: 2` (soft), `max_concurrent_total: 9` = root × (1 + child), mirrored by hand into `.claude/settings.json` until the spike's glue generates it.

### Ledger v0 (`markdown-v0`)

One writer per file. `ledger/Spud.md`; `ledger/Board.base` (a Bases table over ticket frontmatter; the status on each ticket is the board); `ledger/tickets/SPUD-nnn.md` (Spud; frontmatter id, title, priority, status, origin, lead, created; sections Brief, Size persona and model decision, Team, Handoffs, Proposals received, Outcome); `ledger/tickets/SPUD-nnn/<Name>.md` (frontmatter id, name, persona, model, parent, ticket, status `active|done|blocked|failed`, spawned, finished, all parent-owned with times from `date`; sections Brief (parent), Log, Sub-agents, Ticket proposals, Result or Blocked (spudagent), Outcome (parent)); `reports/YYYY-MM-DD.md` (Spud). No event log in v0. The native memory dir holds Eric's preferences; the ledger holds ticket state.

### Reporting and scope

Status is always read from the ledger. A dated report entry follows every recorded outcome and ticket decision. Spudagents write only inside this repo in v0 and never use `isolation: worktree`. Cross-repo work is a spike question.

## Files

`.gitignore`, `spud.config.json`, `.claude/settings.json`, `.claude/agents/spudagent.md`, `CLAUDE.md`, `ledger/Spud.md`, `ledger/_templates/ticket.md`, `ledger/_templates/spudagent.md`, `ledger/Home.md`, `ledger/Fleet.base`, `ledger/Board.base`, `reports/`, `docs/spikes/`, this spec.

## Verification

1. A fresh session asked "what are you working on?" reads the board and makes no Agent call.
2. Toy ticket SPUD-001: a lead spawns two sub-agents with distinct pool names and disjoint paths; team, outcome, board, report, and one commit by Spud follow. A later ticket reuses a name without conflict.
3. A planted out-of-scope observation becomes a proposal that climbs to Spud, who creates or declines a ticket; no spudagent creates one.
4. A sub-agent at the depth limit is refused by the Agent tool and logs it.
5. "Write it yourself" is refused under law 1 and delegated to a scout.
6. Ownership and commits: only Spud commits; each file's Log names its owner.
7. Model parameters are explicit in every spawn.
8. Obsidian (manual): graph shows Spud, leads, children; team links resolve to the right folder.

## Deferred

`/status` skill (a CLAUDE.md rule covers it); persona-specific agent files; hooks for hard enforcement; scheduled digests; README; `.claude/rules/`; Obsidian Bases fleet view; alternate transports (native fork, Scape sessions, `claude -p`); cross-repo work.

## Roadmap

After the spike: implement the ledger (schema, CLI, migration, generated Obsidian views); hooks that turn laws 1, 4, 5, 6 into tool-enforced rules; Obsidian Bases fleet view; scheduled digests; cross-repo projects; alternate transports.

## Appendix A — DB spike brief (persona researcher, model fable)

Answer with evidence:

1. Which store: SQLite vs alternatives (JSONL/markdown, DuckDB, local Postgres, other). Criteria: single machine, no daemon, survives compaction, human-inspectable, Obsidian-renderable views.
2. Topology: one ledger vs per-project vs per-spudagent databases; define "project" in Spud's world (this repo vs repos Spud works on, such as BadTakes).
3. Schema: tickets (priority, status, origin); teams and spudagents (tree per ticket: id, name unique per team, persona, model, parent, status, timestamps, tokens and duration as returned by the Agent tool); events (append-only); handoffs; proposals and their decisions; projects; the name pool. How `SPUD-002/01.01` maps to rows; how limits are checked from the table.
4. Concurrency: N in-process spudagents writing at once. WAL and busy timeouts vs single-writer through a CLI or MCP tool; recommend one.
5. Migration: schema evolution; how `ledger/` markdown-v0 migrates in; whether the markdown files become generated views so Obsidian keeps working (frontmatter, wikilinks, Bases).
6. Glue language for CLI, hooks, and MCP server: Python 3.14 with stdlib sqlite3, Node 26 with node:sqlite, Bun; hook startup latency; stdlib-only preference; one recommendation.
7. Enforcement: which laws move from prose into the tool (limits, ownership, ticket authority, state moves); how `SubagentStart/Stop` and `PreToolUse` feed it, including the `agent_id` to spudagent-name mapping problem.
8. What the fleet view and reports read from.

Prior art, reference only, not a design to inherit: `/Users/ericlugo/Personal/Minilla/schema.sql`, `/Users/ericlugo/Personal/Minilla/CLAUDE.md`, plus this repo's `ledger/` and this spec.

Output: `docs/spikes/2026-09-12-ledger-database.md` with sections Recommendation, Options considered, Topology, Proposed schema (DDL in a code block is spec, not code), Concurrency model, Migration plan, Language recommendation, Enforcement plan, Ticket proposals, Open questions for Eric. No code files. May spawn up to two sub-agents within limits.
