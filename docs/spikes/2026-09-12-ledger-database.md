# Ledger database spike: what replaces markdown-v0

Ticket [[SPD-006]]. Written by Vitelotte (01, researcher, fable) for Spud, with Elba (01.01, contractor on `claude-code-guide`, sonnet) for the official Claude Code citations and Dakota (01.02, scout, haiku) for the Obsidian Bases citations. Date: 2026-09-12. This spike decides; it does not implement. DDL below is specification.

Every number in this document was measured on this machine on 2026-09-12: Apple M5 Pro, 64 GB, macOS 26.6.2, load average 3.3 to 3.9 during the runs (Eric's desktop was busy, so absolute latencies are conservative). Runtimes present: Python 3.14.7 (Homebrew, `python3`), Python 3.12.13, Node v26.8.2, Bun 1.3.14, Deno 2.9.5, `/usr/bin/sqlite3` 3.51.0, Claude Code 2.1.269, Obsidian 1.12.4. Not present: DuckDB, PostgreSQL, hyperfine. Scratch scripts and scratch databases lived under the session scratchpad, never in the repo; the method is stated next to each table. Four headless `claude -p` sessions (about $0.10 each, on haiku, in scratch directories with no repo) captured real hook payloads; their JSON is quoted below.

## Recommendation

One SQLite file is the ledger, and markdown becomes what it renders.

- **Store:** SQLite in WAL mode, one file, `.spud/ledger.db` in Spud's home, gitignored. SQLite is already inside every candidate runtime on this machine (Python 3.14 stdlib, `node:sqlite`, `bun:sqlite`), needs no daemon, survives compaction because it is a file, is inspectable with `/usr/bin/sqlite3` and through the generated markdown, and took 30 concurrent writer processes with zero errors in the probe below.
- **Topology:** one database for everything Spud does, with a `projects` table. A project is a repository Spud works in, identified by the absolute path of its main checkout; Spud's home is project 1. The file is found through `SPUD_HOME` (set by hooks and by Spud's session) or, failing that, from the git common dir of the checkout the CLI runs in, so worktrees like the one this session runs in resolve to the one real database.
- **Schema:** `projects`, `tickets`, `members` (the team tree, one row per spudagent or contractor, lineage as a materialized path), `spawn_requests` (the join between hook events), append-only `events`, `handoffs`, `proposals` plus `proposal_decisions`, `name_pool`, `renders`. `SPUD-002/01.01` is the `members` row with `ticket.team_key = 'SPUD-002'` and `lineage = '01.01'`; every limit in `spud.config.json` is one `count(*)` inside the transaction that inserts the row.
- **Concurrency:** every writer opens the file directly, `PRAGMA busy_timeout = 5000`, every write transaction starts with `BEGIN IMMEDIATE`, transactions stay short. No single-writer daemon and no MCP-server writer: measured, WAL with those two rules produced 0 busy errors under 30 concurrent processes and under a mix of Python, Node and Bun writers; the two ways to get `database is locked` (timeout 0, deferred read-then-write) are both avoidable by rule.
- **Migration:** `spud import` parses the 18 markdown-v0 files that predate this ticket once (they follow fixed templates); `spud render` regenerates `ledger/` and `reports/` from the database after every write, with the same frontmatter keys, wikilinks and `.base` files, so Obsidian keeps working unchanged; the rendered markdown stays committed, the database does not. Schema evolution is `PRAGMA user_version` plus numbered migrations embedded in the CLI. The one-writer-per-file rule becomes one-writer-per-generated-file (the renderer) plus column ownership in the database.
- **Language:** Python 3.14 with the stdlib `sqlite3` module, one file, `bin/spud`, invoked as `python3.14 -I -S`. Cold start of a hook-shaped script (read JSON from stdin, open the database, insert, exit) is 17 to 18 ms median here; Node 26 is 26 ms, Bun 13 ms. Bun's 5 ms lead does not buy a Bun-only API and an older bundled SQLite; Node's `node:sqlite` is still "Stability: 1.2 - Release candidate". Python is stdlib-only, was the Minilla precedent, and is the best of the three at the text work (frontmatter, sections, YAML for `.base` files).
- **Enforcement:** Laws 3, 4, 6 and 7 move into `PreToolUse` hooks that refuse; Law 2 and the "only these paths" line of every brief move into the CLI plus a `PreToolUse` hook on `Write`/`Edit`; the ledger's timestamps and status moves become CLI state machines. Identity comes from the harness, not from prose: every hook payload for a tool call made inside a subagent carries `agent_id` and `agent_type`, Spud's own calls carry neither, and `PostToolUse(Agent)` returns `agentId` next to the `description` string, which is the mapping the spec called a problem.
- **Fleet view and reports:** generated markdown, read by Obsidian. Each ticket note gets a generated Team table (lead first, every member with persona, model, resolved model, status, run time, tokens, and a one-paragraph "worked on / built" summary) plus an embedded `![[Fleet.base#Team]]` view filtered with `ticket == this`. Reports are rendered from the event log. A UI is a later, optional layer over the same tables.

| Appendix A question | Recommendation | Decisive evidence |
|---|---|---|
| 1. Which store | SQLite, WAL, one file | Present in all three runtimes (SQLite 3.53.4 / 3.53.4 / 3.51.0); JSON1, FTS5, `RETURNING`, `STRICT` all available; 30 writers, 0 errors; DuckDB and Postgres absent here and either single-writer-process or a daemon |
| 2. Topology | One database, `projects` table, `.spud/ledger.db` in Spud's home, gitignored, `SPUD_HOME` resolution | This session runs in `.claude/worktrees/ledger-database-spike-56aff0`, a second checkout; a path "beside the script" would find a copy, which is the Minilla bug |
| 3. Schema | Tables below; lineage is a materialized path; limits are `count(*)` in the insert transaction | SPD-001's three-level tree, failed spawn, handoffs and two-level proposal climb map to rows without loss |
| 4. Concurrency | WAL + `busy_timeout` + `BEGIN IMMEDIATE`, direct file access from every process | 9 writers × 200 txns: 0 errors with the rules, 995 errors with timeout 0, 1056 errors with deferred `BEGIN` |
| 5. Migration | Import once, render always, markdown committed, database ignored, `user_version` migrations | 18 files before this ticket, all from fixed templates; Bases read only vault notes and their properties (Obsidian help) |
| 6. Glue language | Python 3.14 stdlib, `bin/spud`, `python3.14 -I -S` | Hook-shaped cold start 17.2 ms (`-S`) vs Node 26.0 ms vs Bun 12.9 ms; node:sqlite release candidate; Bun bundles SQLite 3.51.0 |
| 7. Enforcement | Hooks refuse Laws 3, 4, 6, 7; CLI owns state moves and ownership; hooks bind `agent_id`; `SubagentStop` holds a child that returns without a Result | Probe payloads: `agent_id` present inside subagents, absent for Spud; `PostToolUse(Agent).tool_response.agentId` + `description`; all of it documented on the hooks page |
| 8. Fleet view, reports | Generated Team table per ticket + `Fleet.base#Team` embed; reports from events | Bases read only vault notes and their properties; `this` in an embedded base is the embedding note |

## Options considered

Criteria from Appendix A: single machine, no daemon, survives compaction, human-inspectable, Obsidian-renderable views. Two more the ledger imposes in practice: many concurrent writers (Spud, in-process spudagents, and hooks all write, from separate processes, at the same time) and rules the tool can enforce (constraints, transactions, a state machine).

| Option | No daemon | Survives compaction | Human-inspectable | Obsidian views | Concurrent writers | Rules in the tool | Verdict |
|---|---|---|---|---|---|---|---|
| Markdown as-is (`markdown-v0`) | yes | yes | best | native | one writer per file by convention only; SPD-001 broke it on two fields | none: IDs by counting files, limits by prose | keep as the *view*, not the store |
| JSONL event log | yes | yes | with `jq` | no (needs a renderer anyway) | appends only; concurrent appends from many processes are safe only for small writes and give no read-your-writes state | none; every reader replays | rejected: the renderer and the replay are the database, badly |
| **SQLite, WAL** | yes | yes | `sqlite3` CLI + rendered markdown | via rendered markdown | measured: 30 processes, 0 errors with two rules | constraints, triggers, transactions, views | **chosen** |
| DuckDB | yes | yes | CLI | via rendering | "one process can both read and write to the database" or "multiple processes can read from the database, but no processes can write" ([duckdb.org/docs/current/connect/concurrency](https://duckdb.org/docs/current/connect/concurrency.html)); multi-process writes need DuckLake plus Postgres | yes | rejected: hooks are separate processes; not installed here; analytical engine for a 22-file ledger |
| Local PostgreSQL | **no** | yes | `psql` | via rendering | excellent | excellent | rejected: a daemon to keep alive for one user; not installed here |
| git as the database (commits as events) | yes | yes | yes | native for files | serialized by the index lock; every write is a commit | none | rejected as the store, kept as history: the rendered markdown is committed |
| Obsidian-only (Bases/Dataview over notes) | yes | yes | yes | native | same as markdown | none | rejected: Bases read only "your local Markdown files and their properties" ([obsidian.md/help/bases](https://obsidian.md/help/bases)); no writes, no constraints |

**Why SQLite wins on the evidence, not on habit.** It is already in the standard library of each candidate glue runtime on this machine (Python 3.14.7 links SQLite 3.53.4; Node 26.8.2 `node:sqlite` 3.53.4; Bun 1.3.14 `bun:sqlite` 3.51.0; Deno 2.9.5 `node:sqlite` 3.53.4), and in all three the features the schema uses work: `json_extract`, `jsonb()`, FTS5 virtual tables, `RETURNING`, `STRICT` tables (checked by creating each in a `:memory:` database in each runtime). It is one file plus two WAL sidecars, so "survives compaction" is trivially true and backups are `VACUUM INTO`. It is inspectable at two levels: `/usr/bin/sqlite3 .spud/ledger.db 'select * from v_board'` for Spud and Eric, and rendered markdown for Obsidian. The concurrency measurements are in the Concurrency model section. What SQLite does not give is Obsidian rendering; nothing but markdown does, which is why the markdown stays as generated output.

**Prior art, used only as evidence.** Minilla's `schema.sql` (SQLite, WAL, `events` append-only, `runs` per worker session, `settings`, views) ran a real ticket end to end and taught two things this spike keeps: the rules live in the CLI, not in prose (`TRANSITIONS`, `gate_refusal`), and a database path resolved "beside the script" breaks in worktrees (Minilla's CLAUDE.md documents the bug and the `MINILLA_DB` workaround). Its MCP server shells out to the CLI so the rules live once; the same shape is recommended here. Nothing else is inherited: Minilla has no teams, no lineage, no proposals, and a different status ladder.

### Cold-start measurements

Method: a Python harness ran each command 24 times with `subprocess.run`, discarded 3 warm-ups, timed the remaining 21 with `perf_counter_ns` around the whole process (spawn to exit), and reports min / median / p90 / max in milliseconds. "open" scripts open an on-disk WAL database and run `select 1`. "hook-shaped" scripts read a JSON hook payload from stdin, parse it, open the database with a 5 s busy timeout, insert one row, commit, and exit; all 96 rows landed.

| Case | min | median | p90 | max |
|---|---|---|---|---|
| `/usr/bin/sqlite3` CLI, `select 1` (floor) | 2.9 | 3.4 | 3.8 | 4.4 |
| `python3.14 -c pass` (bare interpreter) | 14.9 | 15.9 | 16.6 | 24.4 |
| `python3.14 open.py` (stdlib `sqlite3`) | 16.6 | 17.3 | 17.8 | 18.0 |
| `python3.14 -S open.py` (no `site`) | 14.2 | 15.0 | 15.5 | 15.7 |
| `python3.14 -I -S open.py` (isolated) | 14.0 | 14.5 | 14.7 | 14.8 |
| `python3.12 open.py` | 15.2 | 15.5 | 16.1 | 16.4 |
| `node -e 0` (bare) | 20.2 | 21.5 | 22.5 | 23.0 |
| `node open.mjs` (`node:sqlite`) | 22.4 | 23.4 | 24.2 | 24.9 |
| `bun -e 0` (bare) | 8.1 | 8.5 | 8.8 | 9.0 |
| `bun open.mjs` (`bun:sqlite`) | 10.6 | 11.1 | 11.4 | 11.5 |
| `deno eval 0` (bare) | 12.4 | 12.7 | 13.1 | 13.2 |
| **hook-shaped** `python3.14 hook.py` | 17.9 | 18.3 | 18.8 | 23.7 |
| hook-shaped `python3.14 -S hook.py` | 16.8 | 17.2 | 17.5 | 17.7 |
| hook-shaped `node hook.mjs` | 25.4 | 26.0 | 27.0 | 27.7 |
| hook-shaped `bun hook.bun.mjs` | 12.2 | 12.9 | 13.3 | 13.5 |

For scale, Claude Code recorded the durations of the hooks that already run on this machine from the oh-my-claudecode plugin (Node scripts) in the probe transcripts: `pre-tool-enforcer.mjs` 88 to 143 ms per tool call, `subagent-tracker.mjs start` 331 to 345 ms per spawn, the three `PostToolUse` scripts 48 to 88 ms each. A Python ledger hook at 17 to 18 ms is a fraction of what each tool call here already pays.

### Sources for this section

- Obsidian Bases: [obsidian.md/help/bases](https://obsidian.md/help/bases), [obsidian.md/help/bases/syntax](https://obsidian.md/help/bases/syntax), [obsidian.md/help/bases/views](https://obsidian.md/help/bases/views), [obsidian.md/help/bases/functions](https://obsidian.md/help/bases/functions) (Dakota, 01.02; `help.obsidian.md` redirects there).
- DuckDB concurrency: [duckdb.org/docs/current/connect/concurrency.html](https://duckdb.org/docs/current/connect/concurrency.html).
- `node:sqlite`: [nodejs.org/api/sqlite.html](https://nodejs.org/api/sqlite.html): "Stability: 1.2 - Release candidate"; added v22.5.0; unflagged in v23.4.0 / v22.13.0; release candidate since v25.7.0; `DatabaseSync` `timeout` option ("The busy timeout in milliseconds ... Default: 0") added in v24.0.0.
- SQLite transactions and the busy handler: [sqlite.org/lang_transaction.html](https://www.sqlite.org/lang_transaction.html), [sqlite.org/c3ref/busy_handler.html](https://www.sqlite.org/c3ref/busy_handler.html).
- Minilla: `/Users/ericlugo/Personal/Minilla/schema.sql` and `CLAUDE.md`.

## Topology

**What a project is.** A project is a repository Spud does work in, recorded once in `projects` by the absolute path of its main checkout (`root_path`) with a short key (`spud`, `badtakes`) and, informationally, its git remote. Spud's home (`/Users/ericlugo/Personal/Spud`) is project 1 and is special only in that the database, the CLI, the hooks and the rendered ledger live there. A ticket belongs to exactly one project; a team belongs to its ticket; nothing else is per project. Ticket numbers stay one sequence across projects (`SPD-nnn`, `SPUD-nnn`), because that is what CLAUDE.md, the templates and the Bases views already assume; a per-project prefix would be a `projects.key` column away if Eric wants it later (open question 2).

**One database, not per project, not per spudagent.** The limits are global counts (`max_concurrent_total` is "9 alive across everything"), the board is one board, the fleet is one fleet, and a name pool draw is per ticket. Per-project databases would need a cross-database count for the concurrency cap and a second board; per-spudagent databases would make every question in Appendix A a merge problem. Minilla's one-database-many-projects shape ran for real and is the right one here.

**Where the file lives and what git sees.** `.spud/ledger.db` in Spud's home, next to `.claude/`, with `.spud/` in `.gitignore` (it also holds `ledger.db-wal`, `ledger.db-shm`, and `backups/`). Reasons: a dot-folder is invisible to Obsidian's file explorer and to Bases' `file.inFolder`, so no stray entries appear in the vault; a binary database in git would produce unreviewable diffs, WAL sidecars in the tree, and merge conflicts between worktrees; and the rendered markdown *is* committed, so git history stays the human-readable audit trail it is today. Durability outside git: `spud backup` runs `VACUUM INTO .spud/backups/ledger-<stamp>.db` (native scheduled task, daily, and before every migration), and the committed markdown is a full re-import source for disaster recovery (the importer is the same parser the migration uses).

**How a spudagent or a hook finds it.** Evidence for why this needs a rule: this very session runs in `/Users/ericlugo/Personal/Spud/.claude/worktrees/ledger-database-spike-56aff0`, a second checkout on branch `claude/ledger-database-spike-56aff0` (`git worktree list` shows both), and its ledger writes land in the worktree's copy of `ledger/`, which Spud must merge. A database path relative to the checkout would silently pick the worktree's copy, the exact failure Minilla's CLAUDE.md documents ("not relative to cwd ... a worktree holds its own copy"). The rule, in order:

1. `SPUD_HOME` environment variable, if set. Hooks set it explicitly in their command line; Spud's own session exports it; `spud` itself refuses to guess when it is set to a path without `.spud/`.
2. Otherwise, from the CLI's own real path: `git -C <dir of bin/spud> rev-parse --path-format=absolute --git-common-dir` gives the main checkout's `.git` for any worktree; its parent is Spud's home. One git subprocess (about 5 ms) only on the fallback path.
3. Otherwise `~/.config/spud/home`, a one-line pointer written by `spud init`, for sessions that start in other repositories (BadTakes) where neither applies.

Spudagents call the CLI by absolute path (`/Users/ericlugo/Personal/Spud/bin/spud`, or a `~/.local/bin/spud` symlink; `Path(__file__).resolve()` follows it), never by relative path, which the brief template will state. Subagents are in-process, so they inherit Spud's environment, including `SPUD_HOME`. Sessions Spud runs inside another repository need that repository added with `spud project add` and the ledger hooks installed there too; that is a later ticket (proposal 8) and open question 8, not a v1 requirement.

## Proposed schema

DDL is specification. Times are ISO 8601 strings with seconds and offset, always taken from the clock by the CLI (never typed by a model), which retires the `date` ritual. `STRICT` tables and `CHECK` constraints are available in every runtime here.

```sql
-- Ledger schema v1. Applied by `spud init`; stamped with PRAGMA user_version = 1.
PRAGMA journal_mode = WAL;                    -- set once at creation; persists in the file
-- Per connection, set by the CLI on every open:
--   PRAGMA foreign_keys = ON;  PRAGMA busy_timeout = 5000;  PRAGMA synchronous = NORMAL;

CREATE TABLE projects (
  id          INTEGER PRIMARY KEY,
  key         TEXT    NOT NULL UNIQUE,        -- 'spud', 'badtakes'
  name        TEXT    NOT NULL,
  root_path   TEXT    NOT NULL UNIQUE,        -- absolute path of the main checkout
  remote      TEXT,                           -- git remote URL, informational
  created_at  TEXT    NOT NULL
) STRICT;

CREATE TABLE tickets (
  id          INTEGER PRIMARY KEY,            -- the number in SPD-nnn; one sequence for all projects
  key         TEXT    NOT NULL UNIQUE,        -- 'SPD-006'  (tickets.prefix from spud.config.json)
  team_key    TEXT    NOT NULL UNIQUE,        -- 'SPUD-006' (teams.prefix)
  project_id  INTEGER NOT NULL REFERENCES projects(id),
  title       TEXT    NOT NULL,
  priority    TEXT    NOT NULL CHECK (priority IN ('P0','P1','P2','P3')),
  status      TEXT    NOT NULL CHECK (status IN ('queued','active','done','declined')),
  origin      TEXT    NOT NULL CHECK (origin IN ('eric','proposal')),
  proposal_id INTEGER REFERENCES proposals(id),   -- set when origin = 'proposal'; renders as proposed_by
  lead_id     INTEGER REFERENCES members(id),     -- the first member; NULL until one is planned
  brief       TEXT    NOT NULL DEFAULT '',
  sizing      TEXT    NOT NULL DEFAULT '',        -- "Size, persona and model decision"
  outcome     TEXT    NOT NULL DEFAULT '',
  tags        TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(tags)),
  created_at  TEXT    NOT NULL,
  updated_at  TEXT    NOT NULL,
  closed_at   TEXT
) STRICT;
CREATE INDEX tickets_board ON tickets(status, priority);

CREATE TABLE members (                         -- one row per spudagent or contractor; the team tree
  id            INTEGER PRIMARY KEY,
  ticket_id     INTEGER NOT NULL REFERENCES tickets(id),
  lineage       TEXT    NOT NULL,              -- '01', '01.02', '01.01.01' (materialized path)
  depth         INTEGER NOT NULL,              -- segments in lineage; Spud is depth 0 and has no row
  parent_id     INTEGER REFERENCES members(id),  -- NULL = Spud
  name          TEXT    NOT NULL,              -- from the pool; unique within the ticket only
  persona       TEXT    NOT NULL CHECK (persona IN ('researcher','architect','reviewer',
                                                    'engineer','designer','writer','scout','contractor')),
  agent_type    TEXT    NOT NULL DEFAULT 'spudagent',   -- 'claude-code-guide', 'Explore', ... for contractors
  model         TEXT    NOT NULL CHECK (model IN ('fable','opus','sonnet','haiku')),   -- tier requested
  tier_reason   TEXT,                          -- the CLI requires it when model <> the persona's default tier
  status        TEXT    NOT NULL CHECK (status IN ('planned','active','done','blocked','failed')),
  brief         TEXT    NOT NULL DEFAULT '',   -- parent-owned
  deliverables  TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(deliverables)),  -- path globs the member may write
  result        TEXT,                          -- member-owned  (## Result)
  blocked       TEXT,                          -- member-owned  (## Blocked)
  outcome       TEXT,                          -- parent-owned  (## Outcome)
  summary       TEXT,                          -- parent-owned, one paragraph: worked on / built (the card)
  planned_at    TEXT    NOT NULL,              -- the row exists before the spawn (Law 2)
  spawned_at    TEXT,                          -- hooks: SubagentStart / PostToolUse(Agent)
  stopped_at    TEXT,                          -- hooks: SubagentStop
  finished_at   TEXT,                          -- the parent's verdict was recorded
  agent_id      TEXT    UNIQUE,                -- native Claude Code id, bound by hooks (17 hex chars)
  session_id    TEXT,
  tool_use_id   TEXT,
  resolved_model TEXT,                         -- e.g. 'claude-haiku-4-5-20251001', from PostToolUse(Agent)
  transcript_path TEXT,                        -- agent_transcript_path from SubagentStop
  return_text   TEXT,                          -- the child's final message (last_assistant_message)
  total_tokens  INTEGER,                       -- as returned by the Agent tool (totalTokens / subagent_tokens)
  duration_ms   INTEGER,                       -- totalDurationMs / duration_ms
  tool_uses     INTEGER,                       -- totalToolUseCount / tool_uses
  usage_json    TEXT    CHECK (usage_json IS NULL OR json_valid(usage_json)),  -- usage + toolStats verbatim
  UNIQUE (ticket_id, name),
  UNIQUE (ticket_id, lineage),
  CHECK  (depth = length(lineage) - length(replace(lineage, '.', '')) + 1)
) STRICT;
CREATE INDEX members_tree ON members(ticket_id, parent_id);
CREATE INDEX members_live ON members(status) WHERE status IN ('planned','active');

CREATE TABLE spawn_requests (                  -- one row per PreToolUse(Agent); joins the hook events
  tool_use_id       TEXT PRIMARY KEY,
  session_id        TEXT NOT NULL,
  at                TEXT NOT NULL,
  caller_agent_id   TEXT,                      -- NULL = Spud's own session
  description       TEXT NOT NULL,             -- 'Elba (01.01, contractor)' or 'SPUD-006/Elba (01.01, contractor)'
  subagent_type     TEXT,
  model             TEXT,
  run_in_background INTEGER,
  member_id         INTEGER REFERENCES members(id),   -- resolved from the description
  agent_id          TEXT,                      -- filled by PostToolUse(Agent) or from meta.json at SubagentStop
  decision          TEXT NOT NULL CHECK (decision IN ('allow','deny')),
  reason            TEXT
) STRICT;

CREATE TABLE events (                          -- append-only; the memory that outlives every session
  id        INTEGER PRIMARY KEY,
  at        TEXT    NOT NULL,
  actor     TEXT    NOT NULL,                  -- 'spud' | 'member:<id>' | 'agent:<agent_id>' | 'hook:<event>' | 'eric' | 'import'
  ticket_id INTEGER REFERENCES tickets(id),
  member_id INTEGER REFERENCES members(id),
  agent_id  TEXT,                              -- lets an event land before its member row is bound
  kind      TEXT    NOT NULL,                  -- see the kind list after the DDL
  body      TEXT    NOT NULL DEFAULT '',       -- the human line: a Log entry, a reason, a report entry
  data      TEXT    CHECK (data IS NULL OR json_valid(data))   -- structured payload: hook JSON, old/new status
) STRICT;
CREATE INDEX events_ticket ON events(ticket_id, id);
CREATE INDEX events_member ON events(member_id, id);
CREATE INDEX events_agent  ON events(agent_id, id);
CREATE INDEX events_kind   ON events(kind, id);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;

CREATE TABLE handoffs (
  id             INTEGER PRIMARY KEY,
  ticket_id      INTEGER NOT NULL REFERENCES tickets(id),
  at             TEXT    NOT NULL,
  from_member_id INTEGER REFERENCES members(id),   -- NULL = Spud
  to_member_id   INTEGER REFERENCES members(id),   -- NULL = Spud
  what           TEXT    NOT NULL,
  path           TEXT
) STRICT;

CREATE TABLE proposals (
  id                 INTEGER PRIMARY KEY,
  ticket_id          INTEGER NOT NULL REFERENCES tickets(id),   -- the ticket it arose on
  origin_member_id   INTEGER NOT NULL REFERENCES members(id),   -- who first wrote it
  holder_member_id   INTEGER REFERENCES members(id),            -- who must decide next; NULL = Spud
  title              TEXT    NOT NULL,
  why                TEXT    NOT NULL DEFAULT '',
  evidence           TEXT    NOT NULL DEFAULT '',
  suggested_priority TEXT    CHECK (suggested_priority IN ('P0','P1','P2','P3')),
  filed_at           TEXT    NOT NULL,
  status             TEXT    NOT NULL CHECK (status IN ('open','absorbed','declined','created')),
  created_ticket_id  INTEGER REFERENCES tickets(id)
) STRICT;

CREATE TABLE proposal_decisions (              -- the climb, one row per parent that touched it
  id                INTEGER PRIMARY KEY,
  proposal_id       INTEGER NOT NULL REFERENCES proposals(id),
  at                TEXT    NOT NULL,
  by_member_id      INTEGER REFERENCES members(id),   -- NULL = Spud
  decision          TEXT    NOT NULL CHECK (decision IN ('absorb','decline','escalate','create')),
  reason            TEXT    NOT NULL DEFAULT '',
  created_ticket_id INTEGER REFERENCES tickets(id)
) STRICT;

CREATE TABLE name_pool (                       -- mirror of naming.pool; `spud config sync` refreshes it
  name   TEXT    PRIMARY KEY,
  active INTEGER NOT NULL DEFAULT 1
) STRICT;

CREATE TABLE renders (                         -- what the renderer last wrote, to detect hand edits
  path             TEXT PRIMARY KEY,           -- relative to Spud's home, e.g. 'ledger/tickets/SPD-006.md'
  sha256           TEXT NOT NULL,
  rendered_at      TEXT NOT NULL,
  through_event_id INTEGER NOT NULL
) STRICT;

CREATE VIEW v_board AS                         -- what Board.base shows, for the CLI and for rendering
SELECT t.key, t.status, t.priority, t.title, l.name AS lead, t.origin,
       (SELECT t2.team_key || '/' || m.name
          FROM proposals p JOIN members m ON m.id = p.origin_member_id JOIN tickets t2 ON t2.id = m.ticket_id
         WHERE p.id = t.proposal_id) AS proposed_by,
       t.created_at, t.updated_at
  FROM tickets t LEFT JOIN members l ON l.id = t.lead_id
 ORDER BY CASE t.status WHEN 'active' THEN 1 WHEN 'queued' THEN 2 WHEN 'done' THEN 3 ELSE 4 END,
          t.priority, t.id DESC;

CREATE VIEW v_fleet AS                         -- what Fleet.base shows
SELECT t.key AS ticket, t.team_key, m.lineage AS id, m.name, m.persona, m.agent_type, m.model,
       m.resolved_model, m.status, COALESCE(p.name, 'Spud') AS parent,
       m.spawned_at, m.finished_at, m.total_tokens, m.duration_ms, m.tool_uses
  FROM members m JOIN tickets t ON t.id = m.ticket_id LEFT JOIN members p ON p.id = m.parent_id
 ORDER BY t.id DESC, m.lineage;                 -- zero-padded lineage sorts as a tree

CREATE VIEW v_live AS SELECT count(*) AS live FROM members WHERE status IN ('planned','active');
```

Event kinds (a `CHECK` in v1, extended by migration): `ticket.created`, `ticket.status`, `ticket.priority`, `ticket.edited`, `member.planned`, `member.spawn_denied`, `member.spawned`, `member.started`, `member.stopped`, `member.log`, `member.result`, `member.blocked`, `member.outcome`, `member.status`, `handoff`, `proposal.filed`, `proposal.decided`, `hook.denied`, `hook.error`, `render`, `report.entry`, `commit`, `import`. A spudagent's `## Log` is its `member.log` events; a day's report is that day's events.

**How `SPUD-002/01.01` maps to rows.** `tickets` has the row with `team_key = 'SPUD-002'` (and `key = 'SPD-002'`, `id = 2`). `members` has the row with `ticket_id = 2` and `lineage = '01.01'`; its `parent_id` is the row with `lineage = '01'` on the same ticket, and its `depth` is 2. The wikilink form `SPUD-002/Russet` is the same table with `name = 'Russet'` instead of the lineage; both are `UNIQUE` per ticket, which is the "name unique within the team, free to repeat elsewhere" rule as a constraint. Children are `WHERE parent_id = :id ORDER BY lineage`; the whole tree is `WHERE ticket_id = 2 ORDER BY lineage`, which needs no recursive query because the lineage is a zero-padded materialized path.

**SPD-001 as rows, the richest record so far.** One `tickets` row (`SPD-001`, done, lead Kestrel). Four `members` rows: Kestrel `01` (writer, opus, `tier_reason` "protocol fidelity on the first run", depth 1, parent NULL), Huckleberry `01.01` and Rosara `01.02` (writer, sonnet, depth 2, parent Kestrel), Ozette `01.01.01` (scout, haiku, depth 3, parent Huckleberry, `status = 'failed'`, `spawned_at` NULL: the row that never became a spawn, with its `member.spawn_denied` event carrying the verbatim `No such tool available: Agent`). Four `handoffs` rows (Kestrel to Huckleberry, Kestrel to Rosara, Huckleberry to Ozette "attempted only", Kestrel to Spud). Three `proposals` rows: Rosara's index page (`origin_member_id` Rosara) with two `proposal_decisions` (Kestrel `escalate`, Spud `create` → `created_ticket_id = 2`), and Kestrel's two (`create` → 3). Tickets `SPD-002` and `SPD-003` carry `origin = 'proposal'` and `proposal_id`, which renders back as `proposed_by: "[[SPUD-001/Rosara]]"` and `"[[SPUD-001/Kestrel]]"`. The guessed timestamps SPD-001's team wrote import as-is with an `import` event noting they were guessed.

**How each limit in `spud.config.json` is checked from the tables.** All four run inside the same `BEGIN IMMEDIATE` transaction that inserts the `planned` row, so two parents planning at once cannot both pass; the config file stays the source of the numbers (the CLI reads it on every invocation; CLAUDE.md says the config wins).

```sql
-- inputs: :ticket, :parent (NULL when Spud is the parent), and the four limits
-- max_depth (2): the new row's depth is the parent's depth + 1; Spud is depth 0
SELECT COALESCE((SELECT depth FROM members WHERE id = :parent), 0) + 1 > :max_depth   AS depth_exceeded;
-- root_fan_out (3) / child_fan_out (2): children ever planned under this parent on this ticket
SELECT count(*) >= CASE WHEN :parent IS NULL THEN :root_fan_out ELSE :child_fan_out END AS fan_out_exceeded
  FROM members WHERE ticket_id = :ticket AND parent_id IS :parent;
-- max_concurrent_total (9): rows alive anywhere, across every ticket and project
SELECT count(*) >= :max_concurrent_total AS concurrency_exceeded
  FROM members WHERE status IN ('planned','active');
-- and, when all three pass, the next lineage and a free name, in the same transaction
SELECT COALESCE((SELECT lineage || '.' FROM members WHERE id = :parent), '') || printf('%02d', count(*) + 1)
  FROM members WHERE ticket_id = :ticket AND parent_id IS :parent;
SELECT name FROM name_pool
 WHERE active = 1 AND name NOT IN (SELECT name FROM members WHERE ticket_id = :ticket)
 ORDER BY random() LIMIT 1;
```

The fan-out count is "children ever planned", which is how CLAUDE.md phrases it ("your children on this ticket are 01, 02, …") and how IDs are numbered; a finished child does not free its slot. Open question 3 asks Eric whether that is the intended meaning. `max_depth` is also enforced natively by `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` (documented default three layers below the main conversation; SPD-001 proved the tool disappears at the cap) and `max_concurrent_total` by `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` (documented default 20, failing with `Concurrent subagent limit reached`); the table check is what makes the per-parent fan-out, which has no native cap, hard instead of soft, and what gives a refusal with a reason instead of a missing tool.

**Ownership, column by column.** The CLI takes `--as spud` (only when no `agent_id` is present, see Enforcement) or `--as <agent_id>` and refuses writes outside the actor's columns: `tickets.*`, `proposal_decisions` with `by_member_id NULL`, and `report.entry` events are Spud's; `members.brief`, `deliverables`, `model`, `tier_reason`, `outcome`, `summary`, `status`, `finished_at` and `handoffs` are the parent's (Spud or the parent member); `members.result`, `blocked`, `member.log` events and `proposals` filed are the member's own; `agent_id`, `session_id`, `tool_use_id`, `resolved_model`, `transcript_path`, `spawned_at`, `stopped_at`, `return_text` and the usage columns are written only by hooks. That is the section-ownership table from CLAUDE.md's Ledger v0, moved from prose into refusals.

## Concurrency model

**Recommendation: WAL, `busy_timeout = 5000`, `BEGIN IMMEDIATE` for every write, short transactions, and every process opens the file itself.** No daemon, no lock file, no single-writer MCP process. The only "single writer" is the single code path: `bin/spud` is the one program that writes the database, invoked by many processes at once.

**Measurements.** Method: a Python driver started N worker processes at once against a fresh WAL database with an `events` table; each worker ran its transactions back to back (one 200-byte insert per transaction) and counted `database is locked` errors, retrying after 0.5 ms; the driver checked the final row count. Python workers used the stdlib `sqlite3` module with its `timeout` argument (which is `sqlite3_busy_timeout`); Node used `new DatabaseSync(path, { timeout })`; Bun used `PRAGMA busy_timeout`.

| Scenario | Wall time | `SQLITE_BUSY` errors | Worst transaction | Rows |
|---|---|---|---|---|
| 9 Python writers × 200 txns, `BEGIN IMMEDIATE`, timeout 5000 ms | 262 ms | **0** | 210 ms | 1800/1800 |
| 9 Python writers × 200 txns, `BEGIN IMMEDIATE`, timeout 0 | 172 ms | **995** (retried) | 125 ms | 1800/1800 |
| 9 Python writers × 200 txns, deferred `BEGIN`, read then write, timeout 5000 ms | 226 ms | **1056** (retried) | 153 ms | 1800/1800 |
| 30 Python writers × 100 txns, `BEGIN IMMEDIATE`, timeout 5000 ms | 340 ms | **0** | 263 ms | 3000/3000 |
| 3 Python + 3 Node + 3 Bun writers × 200 txns, `BEGIN IMMEDIATE`, 5000 ms | 164 ms | **0** | 120 ms | 1800/1800 |
| 1 Python writer × 1000 txns alone (baseline) | 87 ms | 0 | 2.5 ms | 1000/1000 |

Three things the table settles. First, contention is real: with no timeout, more than half of 1800 writes from nine processes hit the lock; nine is exactly `max_concurrent_total`. Second, the busy timeout removes every error at nine and at thirty writers, and the worst case a writer waits is a quarter of a second, an order of magnitude below what any hook or CLI call would notice. Third, the deferred-transaction trap is not theoretical: a transaction that starts with a read and then writes gets `SQLITE_BUSY` even with a 5 s timeout, because SQLite will not wait when a read lock tries to become a write lock while another connection wrote first. The documentation is explicit: "The presence of a busy handler does not guarantee that it will be invoked when there is lock contention", and in the read-to-write promotion case "SQLite returns SQLITE_BUSY for the first process" instead of invoking it ([busy_handler](https://www.sqlite.org/c3ref/busy_handler.html)); "Subsequent write statements will upgrade the transaction to a write transaction if possible, or return SQLITE_BUSY" ([lang_transaction](https://www.sqlite.org/lang_transaction.html)). `BEGIN IMMEDIATE` takes the write lock up front, where the busy handler does apply, and "EXCLUSIVE and IMMEDIATE are the same in WAL mode" (same page). The mixed-runtime row shows the file format is shared safely across three SQLite builds (3.53.4, 3.53.4, 3.51.0), which matters only if a future front end is not Python.

**Rules the CLI follows, derived from the table.** Open, set the three per-connection pragmas, do the work, close; one connection per process, never shared across threads (the stdlib module reports `threadsafety = 3` here, but the CLI has no reason to thread). Every write is `BEGIN IMMEDIATE … COMMIT` and holds no lock while waiting on anything else (no network, no editor, no model). Reads use plain `SELECT` outside a transaction or a deferred transaction that never writes. `synchronous = NORMAL` under WAL survives a process crash and loses at most the last transactions on power loss, which a ledger of a single desktop can accept; `FULL` costs an fsync per commit and can be switched on by a pragma if Eric disagrees. A long-lived reader (an MCP server holding a read transaction open) would block the WAL checkpoint and let the `-wal` file grow; the renderer and the CLI never hold one, and `spud backup` runs `PRAGMA wal_checkpoint(TRUNCATE)` first.

**Why not a single writer behind a CLI daemon or an MCP tool.** An MCP server is a per-session process: Claude Code starts a stdio server for each session that lists it, so two Spud sessions, or Spud plus a headless verification run, would have two "single writers", and hooks cannot call MCP tools at all, so the hooks would need a second write path anyway. Minilla ended up with exactly that shape (an MCP server that shells out to the CLI, plus a queue so its own writes never overlap) and the queue did nothing the file lock does not already do. A daemon would add liveness (is it running? did it die with the terminal?) to solve a problem the measurements show SQLite already solves at this scale. The single-code-path CLI gives the real benefit, one place where the rules live, without a resident process.

**What replaces "one writer per file".** In the database it becomes column ownership checked by the CLI on `--as`; on disk it becomes "one writer per generated file", the renderer, which writes each file whole via a temporary file and `rename`, so Obsidian never sees a half-written note. No human and no agent edits a generated file (Migration plan says what happens if one does).

## Migration plan

**Schema evolution.** `PRAGMA user_version` holds the schema number. Migrations are numbered SQL strings embedded in `bin/spud` (`0001_init`, `0002_…`), applied by `spud migrate` inside one transaction each, after a `VACUUM INTO` backup, and only forward. Adding a column is `ALTER TABLE … ADD COLUMN` with a default; renaming or narrowing is a new table plus copy, never an in-place edit; views and triggers are dropped and recreated by every migration so they always match the tables; the event `kind` list lives in a `CHECK` that a migration widens. `spud init` is idempotent: create if absent, migrate if behind, refuse if ahead (a newer CLI wrote it). Minilla's `ADDED_COLUMNS` pattern is what this generalizes.

**Importing markdown-v0, once.** The 18 files under `ledger/` and `reports/` before this ticket's team files (6 tickets, 5 team files across two teams, `Home`, `Spud`, two `.base` files, two templates, one report) follow fixed templates, so the parser is small: a frontmatter block (a YAML subset: scalars, quoted strings, `[a, b]` lists, `"[[link]]"` values) and `## ` sections with known names. `spud import ledger/ reports/` maps ticket frontmatter to `tickets` (`proposed_by` links resolve to `proposals` rows created from the `## Proposals received` lines), team-file frontmatter to `members` (`parent: "[[SPUD-001/Kestrel]]"` resolves by team key and name; `id` becomes `lineage`; `agent_type` when present), `## Brief`/`## Result`/`## Outcome` to their columns verbatim, `## Log` lines to `member.log` events (the SPD-001 lines carry a date but no time; they import with the date and an `import` note), `## Sub-agents` to a consistency check against the tree, `## Handoffs` to `handoffs` (best effort: the four SPD-001 lines parse; anything that does not parse is kept whole in `what`), and each `reports/YYYY-MM-DD.md` entry to a `report.entry` event with its heading time. Known dirt is imported, not cleaned: the future-dated and `00:00` timestamps of SPUD-001 (recorded in SPD-003), `lead: ""` on SPD-003 and SPD-005, tickets with no team. Every imported row gets an `import` event pointing at the source path, so the ledger says where it came from. The importer runs against a scratch database first; `spud render` from that database into a scratch directory, diffed against the committed files, is the acceptance test: frontmatter keys identical, every wikilink resolving, every section present.

**Markdown becomes generated views, and Obsidian does not notice.** After every write, `spud render` regenerates the affected files: `ledger/tickets/SPD-nnn.md`, `ledger/teams/SPUD-nnn/<Name>.md`, `reports/YYYY-MM-DD.md`; `ledger/Home.md`, `ledger/Spud.md` and the `.base` files are not generated. Frontmatter keys are kept exactly as they are today because `Board.base` and `Fleet.base` depend on them (`id`, `title`, `priority`, `status`, `origin`, `proposed_by`, `lead`, `created`, `tags`; `id`, `name`, `persona`, `model`, `parent`, `ticket`, `status`, `spawned`, `finished`, `tags`); new keys are additive (`agent_type`, `agent_id`, `tokens`, `duration_ms`, `resolved_model`, `spawned_by_tool_use`) and unknown to the existing views, which ignore them. Wikilinks are rendered folder-qualified as today. The `.base` files are not generated in v1 (they are hand-written YAML that already works); the one change is a new `Team` view in `Fleet.base` with the filter `ticket == this` for embedding, per the Bases syntax page ("Links can be compared to files such as `file` or `this`. They will equate if the link resolves to the file"). Obsidian's own docs do not say whether it picks up files changed by another program while the vault is open (Dakota, answer 6); it is what every git-pull and sync workflow relies on, and the first `spud render` while Eric has the vault open is the check; the fallback is Obsidian's "Reload app without saving" command.

**Hand edits and the one-writer rule.** Generated files start with a comment line (`<!-- generated by spud from .spud/ledger.db; edit with the spud CLI -->`). The `renders` table keeps the SHA-256 of every file the renderer wrote; before overwriting, the renderer hashes the file on disk, and if it differs from the recorded hash the file was edited by hand: the renderer refuses that file, records a `render` event with `data.conflict = true`, and prints the path. `spud import --file <path>` accepts the edit into the database (for the case where Eric changes a priority in Obsidian's properties panel), after which rendering resumes. Two-way sync of arbitrary fields is not proposed: it is the many-writers problem again, in a worse costume. Open question 4 asks Eric which he wants: read-only generated notes (recommended) or a short allowlist of properties that `spud import --file` accepts from Obsidian.

**Cutover sequence.** (1) Ship `bin/spud` with `init`, `migrate`, `import`, `render`, and the ticket/member/proposal/handoff/log/report commands (proposal 1). (2) Run the import against a scratch database, render to a scratch directory, diff; fix the parser until the diff is formatting only. (3) `spud init` for real, `spud import`, `spud render`, one commit: "Ledger v1: imported markdown-v0". (4) Spud and the agent definition switch from editing files to calling the CLI (CLAUDE.md and `.claude/agents/spudagent.md` are Spud's files; proposal 3 lists the edits; Eric approves). (5) Install the hooks (proposal 2). (6) Keep `.gitignore` entries for `.spud/`; keep committing rendered markdown. Rollback at any step before (4) is `rm -rf .spud`; after (4) it is "render is a no-op and files are edited by hand again", which loses nothing because the markdown is complete.

### What the fleet view and reports read from (question 8)

**Read from the database, seen through generated markdown.** Bases cannot read the database (Dakota, answer 1), so the store renders and Obsidian reads. Eric's need of 2026-09-12 is met by two generated pieces in every ticket note:

1. A `## Team` section rendered as a table, lead first, then every member in lineage order, indented by depth: name (as a folder-qualified wikilink), ID, persona (with `agent_type` for contractors), model tier and resolved model, status, run (`spawned → finished`, and the duration), tokens and tool uses, and a "worked on / built" column holding `members.summary`, one paragraph the parent writes at Outcome time (`spud member finish … --summary`), falling back to the first paragraph of `result` and then to `return_text` when the parent wrote none. The tree list that the graph view needs stays as today underneath it.
2. `![[Fleet.base#Team]]`, a new view in `Fleet.base` filtered with `ticket == this`, which Obsidian evaluates against the embedding ticket note (Bases syntax page: "When the base is embedded in another file, `this` points to properties of the embedding file"). It shows the same members live from their notes' frontmatter, with the properties Fleet.base already lists plus `tokens` and `duration_ms`.

The member note keeps its sections and gains the usage line in frontmatter. `ledger/Home.md` is unchanged. **Reports** are rendered from `events`: each recorded outcome and ticket decision is a `report.entry` event written by `spud` when Spud records it (`spud member finish`, `spud ticket move`, `spud proposal decide` each append one automatically with a generated line, and `spud report add` takes Spud's prose "Next:" line), and `reports/YYYY-MM-DD.md` is the day's entries in order, so the Reports tab of `Board.base` keeps working and Spud stops hand-writing a file that the ledger already knows. A UI (an MCP App or an HTML card like Minilla's dossier) is a later layer over `v_fleet`, `v_board` and `events`; nothing in v1 depends on it.

## Language recommendation

**Python 3.14 with the stdlib `sqlite3` module, one file, `bin/spud`.** Hooks and spudagents invoke it as `python3.14 -I -S /Users/ericlugo/Personal/Spud/bin/spud …`: `-I` ignores `PYTHON*` environment variables and user site-packages so a hook behaves the same in every session, `-S` skips `site` import, and together they take the cold start from 17.3 ms to 14.5 ms (open-and-select) or 18.3 to 17.2 ms (hook-shaped) in the table above. The interpreter must be named explicitly: `python3` on this machine resolves to Homebrew's 3.14.7 under the current `PATH`, but `/usr/bin/python3` is Apple's 3.9.6, and a hook that inherits a different `PATH` would otherwise run a different Python.

**Why not the faster one.** Bun is the fastest cold start here (12.9 ms hook-shaped), but `bun:sqlite` is an API that exists nowhere else, Bun 1.3.14 bundles SQLite 3.51.0 while the other runtimes have 3.53.4, and Eric's stated preference is stdlib-only. Five milliseconds per hook is not worth a runtime lock-in on a ledger whose hooks fire tens of times per session, not thousands.

**Why not Node.** `node:sqlite` is in core and loads without a warning on v26.8.2, but its documentation still says "Stability: 1.2 - Release candidate", it is the slowest cold start of the three (26.0 ms hook-shaped, and Node's bare interpreter alone is 21.5 ms), and the text work the ledger needs (frontmatter parsing, section splitting, YAML for `.base` files, Markdown tables) is more verbose in Node without packages, which the stdlib-only preference rules out.

**Why Python.** It is stdlib-only for everything in this design: `sqlite3`, `json`, `argparse`, `hashlib`, `pathlib`, `subprocess` (for `git rev-parse` and `git commit`), `datetime` and `zoneinfo`, `re` for the templates, `tomllib` if config ever moves to TOML. `sqlite3` in 3.12+ has the `autocommit` attribute (present here), which makes `BEGIN IMMEDIATE` explicit and unambiguous. Minilla is the precedent that a one-file stdlib Python CLI of this kind runs a real ticket end to end. Its latency is comfortably under everything around it: the plugin hooks already running per tool call on this machine cost Claude Code 48 to 345 ms each.

**MCP server: later, thin, and not the writer.** If a Claude Code tool surface is wanted (for `spud board` inside a session without a Bash call), it should be a stdio MCP server that shells out to `bin/spud … --json` and holds no connection, so the rules stay in one place (Minilla's `mcp/` is exactly this and it worked). In Python that means the `mcp` package via `uv`, which is present here, or a ~200-line stdlib JSON-RPC loop; in Node it means the official SDK. Not part of v1: Spud already reads the ledger through Bash, and hooks cannot call MCP tools.

**The one Python-specific cost.** Homebrew upgrades `python3.14` in place; a hook must not break when 3.15 lands. `bin/spud` states `#!/opt/homebrew/bin/python3.14` and the hook lines name the same binary; `spud doctor` checks the interpreter and the SQLite version at hand and prints both.

## Enforcement plan

**Identity, measured.** Four headless sessions on Claude Code 2.1.269 captured real payloads (method: `claude -p` in a scratch directory with `--settings` pointing at a settings file whose hooks append their stdin JSON to a log, `--agents` defining a one-word probe subagent on haiku, `--strict-mcp-config`; `--output-format stream-json --verbose` recorded the parent's view). The facts the design rests on:

1. `PreToolUse` for an `Agent` call carries `tool_input` with `description`, `prompt`, `subagent_type`, `model` and `run_in_background` (when given), plus `tool_use_id`, `session_id`, `transcript_path`, `cwd`, `permission_mode`, `prompt_id`. Verbatim: `"tool_input": {"description": "Elba (01.01, contractor)", "prompt": "…", "subagent_type": "probe", "model": "haiku"}`.
2. `SubagentStart` carries `agent_id` (17 hex characters), `agent_type`, `session_id`, the parent's `transcript_path`, `cwd`, `prompt_id`. It carries no description, no model, no `tool_use_id`. Its `additionalContext` is delivered to the child: this spike's author received `SubagentStart hook additional context: Agent spudagent started (a0cfc2d597e041e6b)` as its first system reminder (from oh-my-claudecode's tracker hook, which returns `{"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": "…"}}`), and the probe's subagent transcript shows the same line rendered as a `<system-reminder>`.
3. `PostToolUse` for an `Agent` call returns the mapping. For a background spawn it fires at launch: `"tool_response": {"isAsync": true, "status": "async_launched", "agentId": "ac8c90dafa6697045", "description": "Elba (01.01, contractor)", "resolvedModel": "claude-haiku-4-5-20251001", …}`. For a foreground spawn it fires at completion: `"tool_response": {"status": "completed", "agentId": "adb9ecf5d69362ddd", "agentType": "probe2", "content": [{"type": "text", "text": "potato"}], "resolvedModel": "claude-haiku-4-5-20251001", "totalDurationMs": 4791, "totalTokens": 41831, "totalToolUseCount": 1, "usage": {…}, "toolStats": {"readCount": 0, "searchCount": 0, "bashCount": 1, "editFileCount": 0, "linesAdded": 0, "linesRemoved": 0, "otherToolCount": 0}}`. Either way `tool_use_id` matches the `PreToolUse` row, and the completed shape is the documented one (hooks page, `Agent` tool entry: "your PostToolUse hook receives the subagent's final text and run telemetry in `tool_response`").
4. Every hook payload for a tool call made *inside* a subagent carries `agent_id` and `agent_type` (`PreToolUse` and `PostToolUse` for the probe's `Bash` call: `"agent_id": "adb9ecf5d69362ddd", "agent_type": "probe2"`); Spud's own tool calls carry neither. Absence of `agent_id` therefore identifies Spud, and no prose or `--as spud` flag has to be trusted.
5. `SubagentStop` carries `agent_id`, `agent_type`, `agent_transcript_path` (`<project>/<session>/subagents/agent-<agent_id>.jsonl`), `last_assistant_message` (the child's final text), `background_tasks` (for background children: `{"id": "<agent_id>", "type": "subagent", "status", "description", "agent_type"}`), `session_crons`, `stop_hook_active`. No usage.
6. Beside every subagent transcript the harness writes `agent-<agent_id>.meta.json`: `{"agentType", "description", "toolUseId", "spawnDepth", "requestShape": "foreground"|"background", "requestNonInteractive", "model"}`. It does not exist yet when `SubagentStart` fires (checked in the hook: `exists: false`) and does exist at `SubagentStop` (`exists: true`, contents as above). It is an internal file, not a documented interface, so the design uses it only as a fallback.
7. What the parent sees when a child returns: a foreground call's tool result ends with `agentId: <id> … <usage>subagent_tokens: 41831 tool_uses: 1 duration_ms: 4791</usage>`; a background child's completion notification carries `<usage><subagent_tokens>67198</subagent_tokens><tool_uses>24</tool_uses><duration_ms>93143</duration_ms></usage>` (that one is Dakota's, quoted from this spike's own session). The `stream-json` output additionally shows `task_started` with `spawn_depth` and `is_backgrounded`, and the final `result` carries `subagent_stats` with `refused: {depth_limit, concurrency_limit, budget}` counters. The subagent transcript carries `usage` per assistant message (input, output, cache creation, cache read tokens), which the stop hook can sum when no completion totals reached a hook (the background case).
8. Order of events, background spawn: `PreToolUse(Agent)` → `SubagentStart` → `PostToolUse(Agent, async_launched)` → … → `SubagentStop`. Foreground: `PreToolUse(Agent)` → `SubagentStart` → the child's own `PreToolUse`/`PostToolUse` → `SubagentStop` → `PostToolUse(Agent, completed)`.
9. All of the above matches the documentation Elba verified against the raw page source (Sources below): hooks fire for a subagent's tool calls with `agent_id` and `agent_type`; `SubagentStop` carries `agent_transcript_path` and `last_assistant_message`; the `Agent` tool's `PostToolUse` `tool_response` is documented with `totalTokens`, `totalDurationMs`, `totalToolUseCount`, `usage` and `modelsUsed`; and reaching the parent after a child returns is, in the docs' own words, a `PostToolUse` hook on the `Agent` tool.

**The `agent_id`-to-name mapping, solved.** The join key is `tool_use_id`. `PreToolUse(Agent)` writes a `spawn_requests` row with the description (which carries the name, the lineage and the persona, so the `planned` member row is found by name and lineage) and, for a caller inside a subagent, the caller's `agent_id` (which must be the planned row's parent). `PostToolUse(Agent)` binds `agentId` to that row: immediately for background spawns, at completion for foreground ones. For a foreground spawn the binding is completed earlier at `SubagentStop` from `meta.json`'s `toolUseId`, and if that file were ever absent, from the oldest unbound `spawn_requests` row of the same session (an ordering assumption, stated as such). Events a child records before its row is bound carry `agent_id` and attach to the row when the binding lands. Two protocol changes make this exact rather than merely reliable, both in Spud's files and therefore proposals for Eric (proposal 3): the description gains the team key (`SPUD-006/Elba (01.01, contractor)`), so two tickets running a lead of the same name at the same time cannot collide; and spudagent spawns default to `run_in_background: true`, which CLAUDE.md already prescribes for Spud, so the binding lands before the child's first tool call (in the probe, `PostToolUse` fired 60 ms after `SubagentStart`; a child's first tool call needs an API round trip).

**Which laws move into the tool.**

| Law or rule | Today | Enforced by | Mechanism |
|---|---|---|---|
| 2: no brief, no spudagent | prose | CLI + `PreToolUse(Agent)` | the hook refuses a spawn whose description matches no `planned` row whose parent is the caller and whose `brief` is non-empty |
| 3: explicit model, never `fork` | prose | `PreToolUse(Agent)` | refuse when `tool_input.model` is missing, when the call is a fork (documented as a subagent that "sees the same system prompt, tools, model, and message history as the main session"), or when `isolation` is set (documented `worktree` option); `model` values the docs accept are `sonnet`, `opus`, `haiku`, `fable`, a full model ID, or `inherit`, and `inherit` is refused too |
| 4: limits from `spud.config.json` | prose + two native env caps | CLI (`spud member new`) + `PreToolUse(Agent)` as defence in depth | the four `count(*)` checks in the insert transaction; the hook recomputes them before allowing the spawn, so a spawn attempted without a planned row is refused with a reason instead of a missing tool |
| 5: never write a ledger file you do not own; "only these paths" | prose | `PreToolUse(Write\|Edit\|MultiEdit\|NotebookEdit)` + CLI column ownership | for a subagent caller, the path must match its `deliverables` globs; for Spud (no `agent_id`), the path must be `spud.config.json`, `CLAUDE.md`, `.claude/**` or `docs/superpowers/specs/**`; `ledger/**` and `reports/**` are refused for everyone because they are generated |
| 6: only Spud creates tickets | prose | `PreToolUse(Bash)` + CLI | `spud ticket …` mutations are refused when `agent_id` is present; the CLI additionally requires `--as spud`, which the hook only lets through when the payload has no `agent_id` |
| 7: no commits by spudagents | prose | `PreToolUse(Bash)` | `git commit`, `git add`, `git stash`, `git checkout`, `git switch`, `git rebase`, `git reset`, `git push` refused when `agent_id` is present; `spud commit` (Spud's) runs git itself |
| 1: Spud never produces a deliverable | prose | `PreToolUse(Write\|Edit)` for the main session, partially | the path rule above catches direct writes; shell redirection in `Bash` is refused heuristically for the main session (`>`/`>>`/`tee` outside Spud's paths); the judgment of what counts as work stays prose |
| 9: never leave a returned spudagent unrecorded | prose | `Stop` hook for the main session | a member with `stopped_at` set and no `outcome` blocks Spud's turn from ending once, with the list |
| 8: never answer status from memory | prose | `SessionStart` (startup, resume, compact) | `spud board --brief` injected as `additionalContext`, so the session ritual and the post-compaction re-read happen without being remembered |
| Finishing: `## Result` or `## Blocked` before returning | prose | `SubagentStop` | if the member's `result` and `blocked` are both empty and `stop_hook_active` is false, return `decision: "block"` with the reason "record your Result with `spud member result --as <id>` before returning"; documented as "keeps the subagent running and delivers `reason` to the subagent as its next instruction"; `stop_hook_active` prevents a second block |
| status moves, timestamps, IDs, names | prose + `date` | CLI | state machines for `tickets.status` and `members.status`, clock timestamps, lineage and name draws inside the insert transaction |

What stays prose: sizing, persona and tier judgment; the quality of a brief and an outcome; whether a proposal deserves a ticket; Law 1's definition of work beyond paths. Hooks raise the ordinary paths from "the model remembered" to "the harness refused"; a determined agent could still reach the database through an unusual shell command, which the Bash hook's heuristics narrow and the append-only `events` log makes visible after the fact.

**The hook table.** Each command is `python3.14 -I -S $SPUD_HOME/bin/spud hook <name>`, reads the payload from stdin, and answers the documented way: exit 0 with JSON on stdout to allow or to add context (`PreToolUse` returns `hookSpecificOutput.permissionDecision` of `allow`, `deny`, `ask` or `defer` with a `permissionDecisionReason`; `SubagentStart` returns `additionalContext`; `SubagentStop` may return `decision: "block"` with a `reason`), or exit 2 with the reason on stderr, which "means a blocking error" and blocks regardless of stdout; other non-zero codes do not block. `SubagentStart` cannot block a spawn, so refusals live in `PreToolUse(Agent)`. Matching hooks run in parallel, so each event has exactly one ledger hook. The hooks write to the same database with the same rules as every other writer; probe 3 ran precisely this shape, `python3.14 -I -S hook.py` on all four events, and all six payloads of a spawn-with-one-tool-call landed in a WAL database while the plugin's Node hooks ran beside it.

| Event | Matcher | Reads from the payload | Writes and returns |
|---|---|---|---|
| `PreToolUse` | `Agent` | `tool_input`, `tool_use_id`, `session_id`, `agent_id` (caller) | `spawn_requests` row; refuses per Laws 2, 3, 4; on allow, `member.spawned` event |
| `PostToolUse` | `Agent` | `tool_response.agentId`, `description`, `resolvedModel`, `status`, `content`, `totalTokens`, `totalDurationMs`, `totalToolUseCount`, `usage`, `toolStats`; `tool_use_id` | binds `agent_id`; `planned` → `active`; `spawned_at`; on `completed`, `return_text` and the usage columns |
| `SubagentStart` | (all) | `agent_id`, `agent_type`, `session_id` | `member.started` event by `agent_id`; returns `additionalContext`: "Ledger: your agent_id is `<id>`; every `spud` command you run takes `--as <id>`" |
| `SubagentStop` | (all) | `agent_id`, `agent_transcript_path`, `last_assistant_message`, `meta.json` beside the transcript | binds if still unbound (foreground); `stopped_at`, `return_text`, `transcript_path`; sums transcript usage when no totals were recorded; `member.stopped` event; never touches `status` (the parent's verdict) |
| `PreToolUse` | `Bash` | `tool_input.command`, `agent_id` | Laws 6 and 7 for subagent callers; direct database access (`sqlite3 …ledger.db`, `python … sqlite3`) refused for everyone; well-formed `spud` calls answered with `permissionDecision: "allow"` so background children never hit a prompt; `hook.denied` events |
| `PreToolUse` | `Write\|Edit\|MultiEdit\|NotebookEdit` | `tool_input.file_path`, `agent_id` | Law 5 path rule; Law 1 path rule for Spud; `hook.denied` events |
| `SessionStart` | `startup\|resume\|compact` | `session_id`, `cwd` | `additionalContext` = `spud board --brief` |
| `Stop` | (main session only: no `agent_id`) | `session_id` | Law 9 check; blocks once with the list of unrecorded returns |

**Latency and failure.** Each hook costs one Python cold start plus one short transaction: 17 to 18 ms measured here, against the 48 to 345 ms the existing plugin hooks already spend per event. Hooks that enforce a law must fail closed: any exception ends in exit 2 with the reason, never exit 1 (a non-blocking error the harness only reports). Hooks that only record (`SubagentStart`, `SubagentStop`, `PostToolUse`) fail open: they exit 0 whatever happens, and the next successful call writes a `hook.error` event describing the gap, so a ledger hiccup never kills a running team. `busy_timeout` covers the case where nine spudagents' hooks fire in the same 100 ms; the WAL table shows the wait is bounded at a quarter of a second at that concurrency.

**Where the hooks live.** In Spud's home `.claude/settings.json`, with absolute command paths and `SPUD_HOME` in the command line, generated and checked by `spud settings sync` together with the two env caps that the spec already says the glue should generate (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`, whose documented default is three layers, and `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`, whose documented default is 20, both set from `limits`). Settings-file hooks are among the things the docs list as used before a folder is trusted and they ran in the untrusted `claude -p` probes; a project subagent's own frontmatter hooks are the exception ("Not used, and no dialog is offered"), so no hook goes into `.claude/agents/spudagent.md`. Two permission consequences: `permissions.allow` rules are ignored until Eric accepts the trust dialog once (the bootstrap memory note), and a background subagent cannot answer a permission prompt, "if no hook returns a decision, it denies the call", so `spud settings sync` also writes an allow rule for the CLI (`Bash(python3.14 -I -S /Users/ericlugo/Personal/Spud/bin/spud:*)`) and the `PreToolUse(Bash)` hook returns `permissionDecision: "allow"` for well-formed `spud` calls it has checked, which is what lets background spudagents log, propose and record results without a prompt. Sessions in other repositories get the same hooks and rules through `spud project add`, later (proposal 8).

### Sources for this section

Official documentation, verified by Elba (01.01) against the raw page source after the fetch summarizer misnamed a field; quotes are verbatim, full answers in `ledger/teams/SPUD-006/Elba.md`:

- Hooks fire inside subagents with identity: "When a subagent calls a tool, tool events such as `PreToolUse` and `PostToolUse` fire the same configured hooks as in the main conversation" and "the input carries the `agent_id` and `agent_type` common input fields that identify the subagent" ([hooks](https://code.claude.com/docs/en/hooks)).
- `SubagentStart`: "SubagentStart hooks receive `agent_id` with the unique identifier for the subagent and `agent_type` with the agent name"; "SubagentStart hooks can't block subagent creation, but they can inject context into the subagent"; `additionalContext` is a "String added to the subagent's context at the start of its conversation, before its first prompt" ([hooks#subagentstart](https://code.claude.com/docs/en/hooks#subagentstart)).
- `SubagentStop`: "SubagentStop hooks receive `stop_hook_active`, `agent_id`, `agent_type`, `agent_transcript_path`, and `last_assistant_message`"; "Returning `decision: \"block\"` with a `reason` keeps the subagent running and delivers `reason` to the subagent as its next instruction"; "To inject context into the parent session after a subagent returns, use a `PostToolUse` hook on the `Agent` tool instead"; "The `transcript_path` is the main session's transcript, while `agent_transcript_path` is the subagent's own transcript stored in a nested `subagents/` folder" ([hooks#subagentstop](https://code.claude.com/docs/en/hooks#subagentstop)).
- `PreToolUse` and `PostToolUse`: "The `tool_name`, `tool_input`, and `tool_use_id` fields are event-specific"; "The input includes both `tool_input`, the arguments sent to the tool, and `tool_response`, the result it returned"; decision control is `hookSpecificOutput.permissionDecision` with `permissionDecisionReason`, `updatedInput`, `additionalContext`: "\"deny\" prevents the tool call. \"ask\" prompts the user to confirm. \"defer\" exits gracefully so the tool can be resumed later" ([hooks#pretooluse](https://code.claude.com/docs/en/hooks#pretooluse), [#pretooluse-decision-control](https://code.claude.com/docs/en/hooks#pretooluse-decision-control), [#posttooluse](https://code.claude.com/docs/en/hooks#posttooluse)).
- Exit codes: "Exit 2 means a blocking error." and "Any other exit code doesn't block on its own for most hook events." ([hooks#exit-code-2](https://code.claude.com/docs/en/hooks#exit-code-2)). Matchers: "`Bash` matches only the Bash tool; `Edit|Write` and `Edit, Write` each match either tool exactly" ([hooks#matcher-patterns](https://code.claude.com/docs/en/hooks#matcher-patterns)); `Agent` is the documented tool that "Spawns a subagent with its own context window to handle a task" ([tools-reference](https://code.claude.com/docs/en/tools-reference)), so `"Agent"` as a matcher is an inference from those two facts, not a quoted example.
- Agent tool telemetry, documented under the `Agent` tool entry on the hooks page: "your PostToolUse hook receives the subagent's final text and run telemetry in `tool_response`", with `status`, `agentId`, `content`, `resolvedModel`, `modelsUsed`, `totalTokens`, `totalDurationMs` ("Wall-clock duration of the subagent run"), `totalToolUseCount` ("Count of tool calls the subagent made"), `usage` ([hooks#agent](https://code.claude.com/docs/en/hooks#agent)). The sub-agents page adds "When a subagent completes, Claude receives its agent ID." and resumption through `SendMessage` ([sub-agents#resume-subagents](https://code.claude.com/docs/en/sub-agents#resume-subagents)), and names the transcript path: "Each transcript is stored as `agent-{agentId}.jsonl`".
- Sub-agent configuration: `model` is "`sonnet`, `opus`, `haiku`, `fable`, a full model ID such as `claude-opus-5`, or `inherit`"; `isolation: worktree` runs "the subagent in a temporary git worktree, giving it an isolated copy of the repository"; "A fork is a subagent that inherits the entire conversation so far instead of starting fresh" and "sees the same system prompt, tools, model, and message history as the main session"; depth: "set `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` to the number of subagent layers you want below your main conversation" (default "up to three layers below the main conversation"); concurrency: "when 20 subagents are running in a session, spawning another with the Agent tool fails with `Concurrent subagent limit reached`" ([sub-agents](https://code.claude.com/docs/en/sub-agents)).
- Background subagents and permissions: "Claude Code still runs the hooks for their tool calls, and if no hook returns a decision, it denies the call." ([hooks-guide](https://code.claude.com/docs/en/hooks-guide)). Also from the guide: "Claude Code runs all matching hooks in parallel"; default timeout for `command` hooks is ten minutes.
- Hook locations and trust: seven documented locations, "Where you define a hook determines its scope" ([hooks#configure-hook-location](https://code.claude.com/docs/en/hooks#configure-hook-location)); `--settings` "can set any key your user settings file can set" ([settings](https://code.claude.com/docs/en/settings#change-a-setting-for-one-session)); before a folder is trusted, "Hooks in settings files" are listed as used, while a project subagent's own frontmatter hooks are "Not used, and no dialog is offered" ([permissions#what-runs-before-you-trust-a-folder](https://code.claude.com/docs/en/permissions#what-runs-before-you-trust-a-folder)).
- First-hand payloads: scratchpad sessions `hookprobe` through `hookprobe4` on 2026-09-12 (Claude Code 2.1.269), quoted in `ledger/teams/SPUD-006/Vitelotte.md` `## Log`. oh-my-claudecode 5.4.0 `src/hooks/subagent-tracker/index.ts` (installed plugin) as a working example of a `SubagentStart` hook returning `additionalContext`.

## Ticket proposals

1. **Build the ledger CLI v1 (`bin/spud`).** Why: everything else in this spike depends on it. Evidence: this document's DDL, ownership matrix, limit queries, cold-start and WAL measurements. Scope: `init`, `migrate`, `backup`, `config sync`, `import`, `render`, `ticket new|move|edit|show`, `member new|log|result|block|finish`, `proposal file|decide`, `handoff add`, `report add`, `board`, `fleet`, `card`, `doctor`, `settings sync`, all with `--json`; scratch-database acceptance test (import → render → diff) before the real cutover. Persona: engineer; tier override to **fable** because it is data and persistence, expensive if wrong. Suggested priority: **P1**.
2. **Hooks that enforce Laws 3, 4, 6, 7, the path rule, and record spawns.** Why: turns prose into refusals; binds `agent_id` to members. Evidence: probe payloads in the Enforcement plan. Depends on 1. Persona: engineer on **fable** (enforcement is security-adjacent). Suggested priority: **P1**.
3. **Protocol edits in Spud's own files for the CLI era.** Why: CLAUDE.md, the brief template and `.claude/agents/spudagent.md` describe file edits, `date`, and counting files; they must describe `spud` commands, the qualified description `SPUD-nnn/Name (id, persona)`, `run_in_background: true` as the spawn default, deliverable globs recorded at `spud member new`, the absolute CLI path, the `permissions.allow` rule for the CLI (background subagents cannot answer prompts), and the finishing rule that `SubagentStop` will hold a child for: record Result or Blocked through the CLI before returning. Evidence: the mapping section; CLAUDE.md protocol steps 1 to 7. Done by Spud with Eric, no spudagent (Law 1 territory). Suggested priority: **P1**, landing with 1 and 2.
4. **Generated ticket card and `Fleet.base#Team` view.** Why: Eric's stated need; the render format for `## Team` and the `summary` column need a designer's pass so the table reads well in Obsidian. Evidence: question 8 answer; Bases `this` filter. Persona: designer (opus) for the layout, writer for the summary rules. Suggested priority: **P2**.
5. **Reports rendered from events and the `SessionStart` board injection.** Why: removes the hand-written report and the session ritual's grep. Evidence: `report.entry` design; `SessionStart` `additionalContext`. Persona: engineer (opus). Suggested priority: **P2**.
6. **Backups, checkpointing and `.gitignore`.** Why: the database is not in git. Evidence: Topology. `spud backup` (`VACUUM INTO`, `wal_checkpoint(TRUNCATE)`) on a native scheduled task, `.spud/` ignored, retention of 14 daily copies. Persona: engineer (sonnet; mechanical). Suggested priority: **P2**.
7. **Token and cost accounting per member and per ticket.** Why: the Agent tool returns `totalTokens`, `totalDurationMs` and `toolStats`, and the transcripts carry per-message usage; the card should show cost. Evidence: probe 2's `tool_response`, the transcript `usage` fields. Persona: engineer (opus). Suggested priority: **P2**.
8. **Cross-repository projects (BadTakes).** Why: the topology supports it, v1 does not exercise it: `spud project add`, hook installation in the other repository, `SPUD_HOME` in those sessions. Evidence: Topology, open question 8. Persona: architect (fable) then engineer. Suggested priority: **P3**, after Eric answers.
9. **Generate `.claude/settings.json` env caps from `spud.config.json`.** Why: the spec deferred it to the glue; `spud settings sync` covers it once 1 exists. Evidence: spec Limits section. Persona: scout or engineer (haiku/sonnet). Suggested priority: **P3**, folded into 1 if cheap.

## Open questions for Eric

1. **Database location.** `.spud/ledger.db` inside Spud's home, gitignored (recommended: hidden from Obsidian, next to `.claude/`, one `SPUD_HOME` to resolve), or `~/.spud/ledger.db` outside every repository?
2. **Ticket numbering across projects.** One `SPD-nnn` sequence for every project (recommended; nothing changes), or per-project prefixes (`BAD-nnn`) when BadTakes arrives?
3. **Fan-out semantics.** `root_fan_out` and `child_fan_out` count children ever planned under a parent on a ticket (recommended; matches how IDs are numbered and how briefs say "up to 2 sub-agents"), or children currently alive, so a finished child frees a slot?
4. **Hand edits in Obsidian.** Generated notes are read-only and the renderer refuses to overwrite a hand-edited file until `spud import --file` accepts it (recommended), or a short allowlist of properties (`priority`, `status`) may be edited in Obsidian's panel and imported automatically?
5. **Spawn convention.** Approve the two protocol changes in proposal 3: descriptions qualified with the team key, and `run_in_background: true` as the default for every spudagent spawn (recommended; it makes the `agent_id` binding land before the child acts)?
6. **Enforcement strictness at launch.** Refuse from day one for Laws 3, 4, 6, 7, refuse-with-reason for the path rule, and let `SubagentStop` hold a child once when it returns without a Result (all recommended), or run the path rule and the finishing check as warn-only for the first week while briefs learn to list deliverable globs?
7. **Reports.** Rendered from events with Spud adding only the "Next:" prose (recommended), or keep `reports/` hand-written by Spud?
8. **Other repositories.** Should v1 install the hooks and `SPUD_HOME` for sessions started inside BadTakes, or is cross-repository work a later ticket (recommended: later, proposal 8)?
9. **Durability setting.** `synchronous = NORMAL` (recommended: survives process crashes, may lose the last commits on power loss, no fsync per write) or `FULL`?
