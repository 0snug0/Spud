---
id: "01"
name: Desiree
persona: engineer
model: fable
parent: "[[Spud]]"
ticket: "[[SPD-008]]"
status: active
spawned: 2026-09-12T16:25
finished: ""
tags: [spudagent]
---
# Desiree (01, engineer) — SPD-008

## Brief
<!-- written by the parent before spawn -->

### Objective
Turn the laws that today live in prose into refusals: implement `spud hook <event>` in `bin/spud` for every row of the spike's hook table, teach `spud settings sync` to install those hooks (plus the `permissions.allow` rule for the CLI) into a settings file, add `spud sql --readonly` as the inspection path, and prove all of it twice: with stdlib unit tests that feed recorded payloads through the hooks, and with headless `claude -p` probes that show the harness actually refusing.

### Deliverables (only these paths, relative to your working directory, which is the worktree `.claude/worktrees/spd-008-enforcement-hooks`)
- `bin/spud` (edit in place; one file, Python 3.14 stdlib only, run as `python3.14 -I -S bin/spud …`)
- `tests/**` (new `tests/test_hooks.py`, edits to `tests/test_settings.py` and `tests/helpers.py` as needed, and a probe harness such as `tests/probes/` if you script the headless runs)
- Your own file (this one, absolute path): `## Log`, `## Sub-agents`, `## Ticket proposals`, `## Result`.

Nothing else. In particular **not** `/Users/ericlugo/Personal/Spud/.claude/settings.json` (installing the hooks into the live settings is the cutover, Eric's go, after `spud init`), not `CLAUDE.md`, not `.claude/agents/spudagent.md`, not `docs/` (the spike stays as written; corrections go in your Result).

### Two things that would hurt Eric's real ledger
1. `resolve_home` in `bin/spud` falls back to the git common dir, which from the worktree is the **main checkout**. So `spud init`, `spud settings sync` (without `--path`), `spud import` or `spud render` run without `SPUD_HOME` would create `/Users/ericlugo/Personal/Spud/.spud/ledger.db` or rewrite the live `.claude/settings.json` before cutover. **Every `spud` invocation you make, by hand or from a test or a probe, runs with `SPUD_HOME` set to a scratch directory** (the suite's `Home` fixture in `tests/helpers.py` already does this). Before you return, verify `ls /Users/ericlugo/Personal/Spud/.spud` fails and `cmp /Users/ericlugo/Personal/Spud/.claude/settings.json .claude/settings.json` is silent.
2. Headless probes spawn real subagents. Run them one at a time, in a scratch directory with a scratch `SPUD_HOME`, never from the main checkout, with `--settings` pointing at a file `spud settings sync --path …` generated for that scratch home.

### What to build

**1. `spud hook <event>`** — one subcommand, `event` in `PreToolUse | PostToolUse | SubagentStart | SubagentStop | SessionStart | Stop`, reading the harness payload as JSON on stdin (tool dispatch inside from `tool_name`), answering the documented way (JSON on stdout, exit 0; or exit 2 with the reason on stderr). Behaviour per the spike's hook table (`docs/spikes/2026-09-12-ledger-database.md`, "Enforcement plan", the two tables), with these decisions fixed by Spud and Eric:

- `PreToolUse` / `Agent` (enforcing): parse `tool_input.description` as `SPUD-nnn/<Name> (<lineage>, <persona>)` (Eric: descriptions carry the team key). The spawn is allowed only when a `planned` member row matches team key, name, lineage and persona, its `brief` is non-empty, its `agent_type` equals `tool_input.subagent_type`, its parent is the caller (caller `agent_id` bound to a member, or no `agent_id` = Spud = parent NULL), `tool_input.model` equals the row's `model` tier, `subagent_type` is not `fork`, `model` is not missing or `inherit`, `isolation` is not set, and the limits still hold when recomputed inside the transaction (fan-out counts children **alive at once** under the parent, `status IN ('planned','active')`; total alive; depth). Write the `spawn_requests` row either way, `member.spawned` on allow, `member.spawn_denied` (row found) or `hook.denied` (no row) on deny. Allow = `permissionDecision: "allow"`; refusal = `permissionDecision: "deny"` with the reason in `permissionDecisionReason`, exit 0, so the model reads why; any exception = exit 2 with the reason on stderr (fail closed, never exit 1).
- `PostToolUse` / `Agent` (recording, fail open): bind `tool_response.agentId` to the `spawn_requests` row by `tool_use_id` and to its member; `planned → active` through the state machine with `spawned_at`, `session_id`, `tool_use_id`, `resolved_model`; on `status: completed` also `return_text` (joined text content) and `total_tokens`, `duration_ms`, `tool_uses`, `usage_json`. Events a child recorded before binding (by `agent_id`) get their `member_id` attached now.
- `SubagentStart` (recording, fail open): `member.started` event keyed by `agent_id` (attach to the member if already bound); return `additionalContext`: ``Ledger: your agent_id is `<id>`; every `spud` command you run takes `--as <id>`.``
- `SubagentStop` (recording plus the finishing hold): bind if still unbound (`agent-<id>.meta.json` beside `agent_transcript_path` gives `toolUseId`; fallback the oldest unbound `spawn_requests` row of the session, stated as an ordering assumption); set `return_text` from `last_assistant_message`, `transcript_path`; sum the transcript's per-message `usage` when no totals were recorded; `member.stopped` event; never touch `status`. The hold: when the member's `result` and `blocked` are both empty and `stop_hook_active` is false, return `{"decision": "block", "reason": "record your Result with `spud member result --as <agent_id> …` (or Blocked with `spud member block …`) before returning"}`; when `stop_hook_active` is true let it go and record that it returned unrecorded. Decide and write down when `stopped_at` is stamped (the final stop, not the held one, is Spud's suggestion).
- `PreToolUse` / `Bash` (enforcing): for callers with `agent_id`: refuse `git commit|add|stash|checkout|switch|rebase|reset|push|merge|cherry-pick|worktree` (Law 7); refuse `spud ticket new|move|edit`, any `--as spud`, and the Spud-only or actor-less mutations `init|migrate|import|render|settings sync|config sync|backup` (Law 6); and require any `--as <x>` to resolve to the caller's own bound member (its `agent_id`, or that member's `SPUD-nnn/Name` or lineage), which is what stops `--as` being "trusted by prose". For everyone: refuse `spud hook …` typed from Bash (hooks are the harness's), and refuse direct access to the database file (any command naming `ledger.db` or `.spud/`, `sqlite3`, `python … sqlite3` against it): the inspection path is `spud sql --readonly` (correction 1 on the ticket, resolved this way by Spud). For Spud (no `agent_id`): Law 1 heuristic, refuse `>`, `>>` and `tee` whose target is inside the repository and outside Spud's paths (below); say in the Result what the heuristic catches and misses. A well-formed `spud` call that passed every check returns `permissionDecision: "allow"` (background children cannot answer prompts); everything else that passes returns no decision. Every refusal writes `hook.denied`.
- `PreToolUse` / `Write|Edit|MultiEdit|NotebookEdit` (enforcing): resolve `tool_input.file_path`; if it lies under `SPUD_HOME` or under one of its worktrees (`.claude/worktrees/<name>/…`), map it to a repository-relative path; paths outside every project root are allowed (memory, scratchpad). Inside: `ledger/**` and `reports/**` are refused for everyone (generated), except Spud's hand-written set; a member's path must match one of its `deliverables` globs (repository-relative, `**` allowed; make `member new` validate them as such: relative, no `..`); Spud's path must be one of `spud.config.json`, `CLAUDE.md`, `.claude/**`, `docs/superpowers/specs/**`, `ledger/Home.md`, `ledger/Spud.md`, `ledger/*.base`, `ledger/_templates/**` (correction 2 on the ticket, extended by Spud with the templates). Refusals write `hook.denied`.
- `SessionStart` / `startup|resume|compact` (fail open): `additionalContext` = the output of `spud board --brief`.
- `Stop` (main session only; a payload with `agent_id` is a no-op): Law 9: members with a final stop recorded and no `outcome` block the turn once, `{"decision": "block", "reason": "<list> — record each with `spud member finish --as spud …`"}`; `stop_hook_active` true lets it through.
- Missing database (`<SPUD_HOME>/.spud/ledger.db` absent): enforcing hooks deny with "no ledger database at <path>; run `spud init`", recording hooks and `SessionStart` exit 0 with nothing. Spud's decision: the hooks are only installed at cutover, after `init`, and refusing from day one is Eric's call (question 6).
- Failure policy, exactly as the spike: enforcing hooks fail closed (exit 2, reason on stderr); recording hooks fail open (exit 0 whatever happens) and the gap is written later as a `hook.error` event: keep a spool (for example `<SPUD_HOME>/.spud/hook-errors.jsonl`) that any later successful hook or CLI command drains into events. One short transaction per hook; `busy_timeout` is already 5000.

**2. `spud settings sync`** — extend the existing command: besides the two env caps, write the `hooks` block (six events, matchers as in the table, `type: command`, absolute interpreter path resolved at sync time, `SPUD_HOME=<home>` on the command line, a short `timeout`) and the `permissions.allow` entries for the CLI in the forms spudagents actually type (`Bash(python3.14 -I -S <abs>/bin/spud:*)` and `Bash(<abs>/bin/spud:*)`; choose and write down). Merge, never clobber: keep every entry that is not Spud's, replace only hook entries whose command contains `bin/spud hook`, idempotent (second run "unchanged"), `--dry-run` prints what would change. Emit `config.synced`.

**3. `spud sql --readonly '<statement>'`** — opens the database with `mode=ro` and `PRAGMA query_only`, runs one statement, prints a table or `--json`; any actor, no writes possible. Also update the `--as` epilog and any "until SPD-008" prose in `bin/spud`.

### Proof
- Unit tests (`tests/test_hooks.py`, plus `test_settings.py`): feed the verbatim payload shapes from the spike and from `/Users/ericlugo/Personal/Spud/ledger/teams/SPUD-006/Vitelotte.md` `## Log` through `spud hook` on stdin against a scratch home; cover the whole lifecycle for a background and a foreground spawn, every refusal above with its reason, the `--as` spoof, the path matrix (member globs, worktree paths, `..`, symlinks, outside-repo paths, Spud's set), the Bash matrix (each git verb, quoting and chaining tricks such as `;`, `&&`, `$(…)`, `sh -c`), both holds with `stop_hook_active` false and true, the missing-database branch, exit 2 on a forced exception in an enforcing hook and exit 0 plus a spooled `hook.error` in a recording one, the settings merge and idempotence. Red first: write the failing test, then the code (`superpowers:test-driven-development`). The whole suite stays green and leaves no bytecode.
- Headless probes, one at a time, in a scratch directory: `env -u CLAUDECODE claude -p "…" --settings <generated> --agents '<probe definitions>' --strict-mcp-config --output-format stream-json --verbose --allowedTools …` (the spike's method; `permissions.allow` from the settings file is ignored until a folder is trusted, so pass `--allowedTools` explicitly and record whether the hook's own `allow` decision lets a background child run `spud` without it). Demonstrate each of the ticket's "done when" refusals with the harness's own output: a spawn with no planned row, a spawn without a model, a spudagent `git commit`, a write outside the member's deliverables, a child returning without a Result held once and then returning after `spud member result`, and every spawn recorded with its usage (`spud member show`, `spud events`). Quote the command lines, the hook decisions and the resulting `events` rows in `## Log`; the same excerpts, condensed, in `## Result`.
- Before returning: spawn a reviewer (fable, `reviewer` persona, `01.01`) to attack the hooks with its own scratch home: forged descriptions, `--as` spoofing, path escapes, quoting tricks, a fork spawn, `inherit`, `isolation`, a hook fed malformed JSON. Fix what it finds red-first and record the handoff in your Log.

### Read first
- `/Users/ericlugo/Personal/Spud/ledger/tickets/SPD-008.md` (the ticket: brief, the two corrections, Eric's decisions).
- `docs/spikes/2026-09-12-ledger-database.md`: Enforcement plan (payload facts 1 to 9, the two tables, latency and failure, where the hooks live), Concurrency model, Proposed schema (`members`, `spawn_requests`, `events`).
- `/Users/ericlugo/Personal/Spud/ledger/teams/SPUD-006/Vitelotte.md` `## Log` (verbatim probe payloads and the exact `claude -p` command lines) and `/Users/ericlugo/Personal/Spud/ledger/teams/SPUD-006/Elba.md` (documentation quotes).
- `/Users/ericlugo/Personal/Spud/ledger/tickets/SPD-007.md` `## Outcome` (the CLI as built: exit codes, decisions, the "Left" list that names what this ticket may close).
- `bin/spud` end to end, especially `resolve_home`, `Ctx`, `connect`, `resolve_actor`, `write_event`, `cmd_member_new`, `cmd_member_start`, `cmd_settings_sync`, the DDL and the state machines; `tests/helpers.py`, `tests/test_settings.py`, `tests/test_members.py`.
- `CLAUDE.md` (Laws 1 to 10) and `.claude/agents/spudagent.md`.
- The live hooks documentation, https://code.claude.com/docs/en/hooks and https://code.claude.com/docs/en/sub-agents, for field names and decision shapes; the spike quotes the 2026-09-12 text, verify against today's.

### Limits
Up to 2 sub-agents alive at once, 1 level remaining (your children get no `Agent` tool), same protocol, IDs `01.01`, `01.02`, names from the `spud.config.json` pool not already used as a filename in `/Users/ericlugo/Personal/Spud/ledger/teams/SPUD-008/`, files created from `/Users/ericlugo/Personal/Spud/ledger/_templates/spudagent.md` at that absolute path.

### Done when
1. `spud hook` handles all six events with the behaviour above; `settings sync` installs them; `spud sql --readonly` exists.
2. The suite is green (the old 124 plus yours) with the payload, refusal, hold, failure-policy and settings cases listed under Proof.
3. The headless probes show each of the ticket's refusals, the one-time hold, and spawn recording with usage, quoted in your file.
4. The reviewer's pass is recorded and its findings fixed.
5. No `/Users/ericlugo/Personal/Spud/.spud`, the main checkout's `.claude/settings.json` untouched, no bytecode, no commits.
6. `## Result` lists every decision you made where the spike or this brief left room (hook naming, exit and decision conventions, `stopped_at` timing, the allow-rule forms, glob semantics, what the Bash heuristics miss, anything you could not verify), and anything left for SPD-009 or a proposal.

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
