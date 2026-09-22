---
name: spud-reference
description: The detail behind the home's CLAUDE.md - the ledger's schema and history, the hook catalogue, render conflicts and import, worktree binding, the pull-request landing rules, and the reasoning behind each rule. Read it when a spud command or a hook refuses for a reason CLAUDE.md does not explain, before touching the ledger's files, the hooks or a landing, or when a rule's origin matters.
---

# Spud reference

Everything here was once in the home's `CLAUDE.md` and was moved out so that file stays under Claude Code's memory limit (one file over about 40,000 characters is skipped). Nothing here overrides `CLAUDE.md`; it explains it. Section names follow that file.

## The ledger CLI

- `bin/spud` is a launcher for `bin/spud_ledger.py`, the entry of the package `bin/spudlib/`. It caches every module's bytecode under the home's `.spud/pycache/`, and the entry run directly refuses.
- The CLI finds the home through `SPUD_HOME`, then the pointer `~/.config/spud/home`, and never through the checkout it runs from. The tests point `SPUD_HOME` at scratch homes; nothing else ever should.
- The actor of every writing command is checked by the hooks installed by `spud settings sync` and `spud project install` against the harness's own `agent_id`, so `--as` cannot be spoofed. Exit codes: 3 the actor does not own what it writes, 4 a limit would be exceeded, 5 an illegal status move or a worktree binding refusal, 6 a manual `spud render` that met a hand edit.
- `spud member resum --all` adds the per-model token breakdown to a sum stored before cost was priced at render, so an old run can be priced.

## The hooks

`spud settings sync` writes the hooks into the home's `.claude/settings.json`, and `spud project install <key>` into each project's untracked `.claude/settings.local.json`, the tool repository's included. `settings sync` also writes the home's native deny rules `Agent(isolation:*)` and `Agent(model:inherit)`, so Law 3 holds there without the hook; a spawn that omits `model` matches no rule and is refused by the hook alone. A project gets no native deny rules (a project's own skills may need `isolation` for their own work), so Law 3 holds there by the hook alone.

- **Enforcing, fail closed:** `PreToolUse` on Agent, Bash and the edit tools, and `Stop`.
- **Recording, fail open:** `PostToolUse(Agent)`, `SubagentStart`, `SubagentStop`, `SessionStart`, `UserPromptSubmit`. A gap is spooled into a `hook.error` event.
- Every hook first decides the session's mode: `spud` (the home, or a claimed session), `plain` (an unclaimed session in a `claim` project, left silent), or `outside` (no project, treated as `spud`). A project's hook lines carry `--project <key>`, so a failure there with no `agent_id` fails open and never stalls {{owner_name}}'s own sessions; a member's call still fails closed.
- The path rule covers the home — one row of its own, carrying {{identity_name}}'s own files and the rendered roots — and every registered project with every worktree `git worktree list` names, wherever it lives, mapped to repository-relative paths.
- Hook denials are `hook.denied` events: `spud events --kind hook.denied` shows who tried what. The hooks refuse most of the laws with the law's number in the reason.
- `SessionStart` injects `spud board --brief` on `startup`, `resume`, `compact` and `clear`. The injection drops parked tickets to a count line plus any whose date has arrived, and adds a line when the render watcher is down.
- `UserPromptSubmit` claims a plain session whose prompt names an existing ticket of its launch project; see Projects below.

### What `Stop` holds

`Stop` holds only the stopping session's members: a member's session is its spawn request's, else its row's (recorded at `member new` from `CLAUDE_CODE_SESSION_ID`), else its root's; a member with no known session holds every session, a planned one only after ten minutes. It holds once for:

- a spudagent this session spawned that returned unrecorded;
- a row this session planned and never spawned;
- a planned row whose spawn was allowed and never bound, once the allow is ten minutes old;
- said once, a child still running under a finished parent.

Its `hook.denied` data lists `returned`, `planned`, `unbound`, `running` and `session_id`. Another session's members never hold you; `spud board --brief` shows them as `returned HH:MM, unrecorded`, and they are the spawning session's to record.

`SubagentStop` holds a spudagent the same way: one that returns without `member result` or `member block`, or while a child of its own is unrecorded or still alive, is held once with the reason and the exact commands.

### A spawn allowed and never bound

`PreToolUse(Agent)` reserves the planned row; `PostToolUse(Agent)` binds the child's `agent_id` and moves it to `active`. If the harness fails the spawn, `member finish <ref> --status failed --outcome "…"` and plan a new row; never re-issue the same description while the first is pending. After ten minutes {{identity_name}}'s `Stop` names such a row as `spawn allowed …, never bound`; look for its tool_use_id in `spud events --kind hook.error --json` first, since a binding that failed open can leave the child running. A parent's own hold names such a child at once, and a refused second spawn names the `member finish` command under the caller's own actor.

## The laws: history and coverage

1. **Own files.** The edit hook refuses {{identity_name}} every path in the home other than `spud.config.json`, `CLAUDE.md`, `.claude/`, `docs/superpowers/specs/`, `ledger/Home.md`, `ledger/Spud.md`, the `.base` files and `ledger/_templates/`, and every path in every registered project. The ledger is written only through `spud`. The Bash hook refuses a member an interpreter run whose program the line spells rather than reads from a file — python's `-c`, node's (bun's, deno's) `-e`/`--eval`/`-p`/`--print`, perl's `-e`/`-E`, ruby's `-e`, or standard input under `-` or no script (a here-document, a pipe, `<`, `<<<`) — since the hook reads no such program, and a member once used one to write a file outside its globs; a deliverable is edited with the Edit or Write tool, which the edit hook checks, and a program from a file or a module is unchanged. The same refusal reaches the shapes that table would otherwise leave open: the interpreter families outside it (osascript, whose `do shell script` is any shell command, php, lua, Rscript, swift, tsx and ts-node), a program a subcommand carries rather than an option (`deno eval <code>`, `deno repl --eval`, `deno run -`), a word the line cannot settle where an option may stand (`node $FLAG code`, refused the same way, while a `$NAME` the line settled is read as its value), and an option an xargs reads out of its input (`xargs node <<< '-e code'`, and an input the line does not spell at all). {{identity_name}} keeps {{pronoun_possessive}} own: the law binds {{pronoun_object}} where the hook cannot see.
2. **Planned before spawned.** The CLI refuses a plan with no brief; the `PreToolUse(Agent)` hook refuses a spawn whose description matches no planned member of the caller's.
3. **Explicit model.** The native `subagent_type: "fork"` inherits the caller's model and skips the depth cap, which is why it is refused. Contractors: `claude-code-guide` and `Explore` are the usual ones.
4. **Limits.** `member new` refuses with exit 4, and the hook recomputes the count before every spawn.
5. **Rendered files.** Ticket fields, a member's Brief and Outcome, status and finished are {{identity_name}}'s; Log, Sub-agents, proposals, Result and Blocked are the member's. The edit hook refuses `ledger/tickets/`, `ledger/teams/` and `reports/` for everyone.
6. **Tickets.** The CLI refuses `ticket new` to members; the Bash hook refuses `--as spud` inside a subagent.
7. **Git.** For any caller with an `agent_id` the Bash hook allows `status`, `log`, `diff`, `show`, `blame`, `grep`, `fetch`, `stash list`, `worktree list`, a `branch` or `tag` listing, and `config get`, and refuses every other name git answers to: the verbs that write the repository (`commit`, `add`, `checkout`, `switch`, `rebase`, `reset`, `push`, `merge`, `cherry-pick`, `worktree` ...), git's own spellings of them (`stage` is `add`, `init-db` is `init`), and the plumbing that writes the index, the object database or the working tree (`read-tree`, `checkout-index`, `update-index`, `write-tree`, `hash-object`, `repack` ...). A verb a later git adds is refused until someone reads it.
8. **Answering from memory.** No hook enforces this: `spud board` at answer time is the only source of truth.
9. **Recording.** See What `Stop` holds above.
10. **Worktrees.** The standing call to land without asking, PR-only projects included, dates to 2026-09-14. Enforced by the hooks; see Worktrees below.

**Module size** (2026-09-15): ~250 lines is a look-again point, never a cap; application code only, never tests or stylesheets. The rules the tool's `bin/spudlib/` was built on are its project skill `spudlib-modules` (`.claude/skills/spudlib-modules/SKILL.md` in the tool repository); `tests/probes/module_sizes.py` reports the sizes and is advisory, never a gate.

## The protocol: detail

- `member new` records the session it runs in (`CLAUDE_CODE_SESSION_ID`), which is why a plan must be made in the session that spawns: a row left planned holds that session's `Stop`.
- Report entries write themselves: `member finish` of {{identity_name}}'s own child, `proposal decide`, `ticket new`, `ticket move` and a priority change in `ticket edit` each add one, titled from the record, with the `--next` line {{identity_name}} types. `report add "<title>" --next "…"` is for what no command records, such as a merge or an install.
- Landing without asking dates to 2026-09-14. The suite rule (2026-09-16): the full suite must be green on the branch, a member's recorded green run counts, and it is rerun only when the branch changed after that run or the default branch moved since the branch was cut. In the tool, `python3.14 -I -S tests/suite.py --digest` says whether the tree changed.
- A tool ticket that adds a migration merges and runs `spud migrate` in the same shell call, undoing the merge if the migration fails, since every hook runs the main checkout's code and refuses a database behind it.
- After a merge into the tool: `spud --as spud settings sync` after a hook change; `spud --as spud project sync --all` after `spudagent.md`, the `/spud` skill or the installed hook wiring changes.
- `ExitWorktree` stays `keep`, because the merge runs at the main checkout after the exit and `remove` would delete the branch before it lands.

## Teams and identity: history

- Names are drawn by `member new` from `naming.pool` in `spud.config.json`: characters from fiction, in canon spelling, and beside every name with a natural feminine form that form too (Charles and Charlotte, Oliver and Olivia, Pete and Petra), so a fork may come back as either. The character {{owner_name}} is a fork of is not in the pool: that name is {{owner_possessive}}. A home that wants other names replaces the pool, which is its own to choose, and members named before a replacement keep the names they were given. `spud --as spud config sync` mirrors the pool into `name_pool`, which is what `member new` draws from.
- The team key's prefix comes from `teams.prefix` in `spud.config.json`, the ticket key's from `tickets.prefix`.

## Proposals: inheritance

A holder that has returned decides nothing more, so its open proposals fall to the nearest ancestor that can still act, and to {{identity_name}} at the root: the climb is computed when somebody decides and written then, `proposal list` reads `{{identity_name}} (was {{team_prefix}}-nnn/Name)`, the `proposal.decided` event names the member it was inherited from, and `member finish` says what a returning member leaves held and who must decide it. So a member that dies mid-flight strands nothing.

## Ledger v1

Format `sqlite-v1`: the database is `.spud/ledger.db` in the home (WAL); the markdown you read is rendered from it by `spud render`, with a generated-file marker right after the frontmatter. One writer, the CLI; every write is one transaction with the ownership, limit and state-machine checks inside it; the `events` table is the append-only log of everything (`spud events`).

**The render watcher**: the LaunchAgent `local.spud.render` (RunAtLoad, KeepAlive) runs `spud render --watch`, which every two seconds compares the newest non-render event with the last render and renders when the database is ahead, logging to `.spud/logs/render.log` (truncated at each start). A render that changes nothing writes nothing; one render runs at a time under `.spud/render.lock`, and a manual `spud render` waits its turn and stays valid. When the watcher is down, `spud doctor` reports it and `spud board --brief` adds a line. {{owner_name}} watches `ledger/teams/{{team_prefix}}-nnn/` live in Obsidian; never render by hand after a spawn, and check `doctor` if a note is missing.

**Backups** are copies in `.spud/backups/`: `spud migrate` writes one before each migration, and `spud backup --daily`, run by the LaunchAgent `local.spud.backup` at load and daily at 03:00, writes one checked copy a day and keeps the newest 14. `spud --as spud schedule install|show` handles both agents, and `spud doctor` lists the backups, since the hooks refuse shell commands that name `.spud/`. Nothing copies the home off its disk; a home that wants an off-machine copy needs its own backup policy beyond `.spud/backups/`.

**Disaster recovery**: the rendered markdown stays the import source, with one exception: `pull_requests` is not rebuilt from it. A note carries a pull request's number, its state and its URL, but not the head branch, the worktree, who recorded it or when it was last read, and a merge on a ticket already done renders no owed line at all, so `spud import` reads past the two keys and the Landing section, names them as dropped in its `import` event, and {{identity_name}} re-records the pull request with one `spud pr record`, the URL in the note in front of {{pronoun_object}}. `ledger/Projects.md` is the import source for the `projects` table, read before any ticket.

**Pricing**: `pricing` in `spud.config.json` is a dated price table (the API list price in USD, read from Anthropic's pricing page on `as_of`); cost is computed from it at render and never stored, so a price change needs only a render, and `spud doctor` names each run it cannot price.

**Schema history**: `user_version` 6, at migration `0006_owner_origin`, after `0005_pull_requests`, `0004_ticket_worktree` and `0003_parked`. `pull_requests` holds a row per landing pull request {{identity_name}} records, which the reconciler settles and the board surfaces, with the event kinds `pr.recorded` and `pr.state`. Projects carry a default branch, a landing policy (`merge|pr`) and a sessions mode (`always|claim`), and session claims live in `sessions`. The home is no project: the key `home` is reserved, so a project may not register under that key. Every ticket and member note carries a `project` property, and every project's notes render flat into the home's `ledger/`.

### The files in the ledger, and where each comes from

Three kinds, and the difference decides who may change one. **Rendered** files come from the database at every render and are settled only through a `spud` command. **Generated** files come from `{{tool}}/share/`: `CLAUDE.md`, `ledger/Home.md`, `ledger/Spud.md`, the two `.base` files, `ledger/_templates/` and `.claude/skills/spud-reference/SKILL.md` — this file — written by `spud init` into a new home and by `spud --as spud home sync` into one that exists, which replaces whatever a hand has done to them and keeps a copy of each replaced file under `.spud/backups/home-sync/<when>/<path in the home>` first. `spud home sync --check` reports what would change and writes nothing; `spud doctor`'s shipped section notes each file that differs, as a note and never a problem, because drift is the normal state of a home somebody works in and only a sync settles it. A change to a generated file that should outlive the next sync is a ticket in the tool repository, landed by merge, and reaches every home from there. Everything else is **the home's own**, and no sync touches it: `reports/`, which the render writes, and `docs/`, the database and `spud.config.json`, which nothing regenerates at all.

- `ledger/Home.md`: the entry point, with the legend for names and notes. Generated; its links are the tool's to keep valid when files move.
- `ledger/Projects.md`: rendered, one table of the registered projects.
- `ledger/Spud.md`: {{identity_name}}'s identity card, the root node of the graph. Generated, and named `Spud.md` whatever {{identity_name}} is called: `render/notefiles` writes `parent: "[[Spud]]"` on every root member.
- `ledger/Board.base` and `ledger/Fleet.base`: Obsidian Bases views over the rendered frontmatter, generated like the rest — a view a ticket specifies (the Team view, embedded in every ticket note as `![[Fleet.base#Team]]` and filtered `ticket == this`) is added in the tool, not here, and the render keeps the frontmatter keys stable because the views depend on them. Their `sort`, `order`, `columnSize` and view settings are display preferences: priority is the `priority` property, never the row order.
- `ledger/tickets/{{ticket_prefix}}-nnn.md`: rendered. Frontmatter: id, title, priority, status (`queued|active|parked|done|declined`), `parked_until` and `parked_reason` after `status` while parked, `pr` and `pr_state` (the newest recorded landing pull request's number and its state, `open|merged|closed`, only while `pull_requests` has a row for the ticket; the number and never the URL, which Obsidian would read as a URL scheme), origin (`owner` for a ticket the home's owner filed directly, or `proposal`), proposed_by, lead, created, tags. Sections: Brief; Size, persona and model decision; Team; Handoffs; Proposals received; Landing; Outcome. **Team** is generated from `members` alone: a table (member, ID, persona, model, status, run, tokens, cost (list), tools, ending in a Total row; the lead's subtree first, then lineage order; cost is the API list price in USD, computed at render), the member tree with each member's worked-on sentence (its `--summary`, else the first paragraph of its Result that reads as work, else its final message), and the embedded `Fleet.base` Team view. **Landing** is generated from `pull_requests` alone, and only while there is a row: one sentence per pull request recorded against the ticket, in record order, the number as a markdown link on its URL, the state in the words `spud board` uses, and under a merge the line of what it leaves owed, the done move and the worktree and branch cleanup. It says nothing about when the row was last read; `spud board` and `spud doctor` carry that, live, so a reconcile pass that found nothing new rewrites no note. The two keys name the newest row recorded; `spud pr list` stays the whole record. A parked ticket leaves `parked` to `queued`, `active` or `declined`, or to `parked` again to renew the date; `spud board` sorts parked after queued.
- `ledger/teams/{{team_prefix}}-nnn/<Name>.md`: rendered, one per member. Frontmatter: id, name, persona, model, parent, ticket, status (`planned|active|done|blocked|failed`), spawned, finished; once the hooks record them, duration_ms, tool_uses, and tokens_out, tokens_in and tokens_cached (from the member's transcript sum only), and cost_usd (that sum's per-model breakdown at the API list price, computed at render); tags. Sections by owner: Brief (parent, at `member new`); Log, Sub-agents, Ticket proposals, Result or Blocked (the member, through `member log|result|block` and `proposal file`); Outcome (parent, at `member finish`). Status moves happen at spawn (the hooks), at `member finish`, and at `member start` after a re-brief.
- `reports/YYYY-MM-DD.md`: rendered from `report.entry` events, one per recorded outcome and per ticket decision; the full story of a day is `spud events`.
- Templates live in `ledger/_templates/` (`ticket.md`, `spudagent.md`): the shape of a rendered note, with the command that fills each section. Nothing else in the ledger starts with `_`. The templates folder is hidden from Obsidian's file explorer by a local setting, set once per vault.

### Render conflicts

A hand edit of a rendered file is detected at the next render (exit 6 for a manual one; the render logs its conflict event once per path and on-disk hash, and `spud doctor` lists open conflicts with the commands that settle them). A file matching neither its last render nor the new one byte for byte is compared with both as a note, frontmatter as parsed values and the rest byte for byte. A difference of YAML style alone (quoting, block lists, key order, bare empty values: what Obsidian writes over a note it has open) is no hand edit; the render goes over it and its `render` event keeps the replaced text with `style_only: true`. A changed value or body, or frontmatter the parser cannot read, is one: accept it, when it is one of the fields `spud import --file` allows, or overwrite it with `spud render --discard <path>`, which keeps the discarded text in the event. A report has no frontmatter and keeps the byte check. A hand edit of `pr`, `pr_state` or the Landing section is refused by name by `spud import --file` and settled with `spud render --discard <path>`.

## Worktrees: enforcement

The first `member new` (or `member edit --deliverable`) whose deliverables land in a project checkout binds the ticket to the caller's linked worktree, recorded in `tickets.worktree` with a `ticket.worktree` event. It refuses with exit 5 from the project's main checkout (naming the `EnterWorktree` step), from outside the project (naming its root), from another worktree while the bound one is still listed, and for a glob naming another project, since a ticket binds one worktree of its own project. A worktree may carry several tickets sharing one batch of work. A binding whose worktree is gone is stale: {{identity_name}}'s next plan from a worktree rebinds it, and a member never does. The edit hook and the Bash hook's write targets refuse a bound ticket's members the same paths anywhere but its worktree; a ticket still unbound keeps the old rule. `spud card` and `spud board` show the worktree and its branch.

Switching worktrees while a spudagent works in the current one: after the switch the harness refuses every Bash and Edit call it still makes there, its own `spud member result` included. Wait for its return first, or leave the second code ticket to another session.

A landed worktree is deleted once its branch is merged and nothing needs it: no spudagent working in it, no session inside it, nothing uncommitted. Never `--force`: `worktree remove` refuses modified or untracked files and `branch -d` an unmerged branch, and either refusal means the work is still needed. The pushed branch stays on `origin`. A worktree whose code never merged (a blocked, failed or declined ticket) stays until {{owner_name}} says to drop it.

## Projects: claims and landing

**Auto-claim.** A plain session whose prompt names an existing ticket of its launch project (`Work on {{ticket_prefix}}-098` in the tool repository, or the equivalent in another registered project) is claimed by the `UserPromptSubmit` hook exactly as `session claim` would claim it, with `how: hook` and the ticket on the `session.claimed` event, and the hook hands the model the `/spud` skill's steps (the same source as `/spud`'s own SKILL.md) and the claim card, including the step that sets the title to `<KEY>-nnn - <what this session does>`. It matches only the project's own prefix in the ledger's spelling (three digits), so a declined ticket, another project's key, a prompt in the home or an `always` project, a subagent, or `/spud` itself never claim; a done ticket does claim. A session released with `session release` stays released: the hook never claims it again, and only `/spud` does.

**Install.** `spud project install <key>` writes the ledger hooks into the project's untracked `.claude/settings.local.json`, and `spudagent` and the `/spud` skill into `~/.claude/`; `project sync --all` refreshes the user copies after the tool's `share/agents/spudagent.md` — where the definition lives, `~/.claude/agents/spudagent.md` being where it is installed — or the skill's text changes. It leaves {{owner_name}}'s own allow rules alone.

**The pull-request merge in auto mode.** The auto-mode classifier refuses a PR merge as `Merge Without Review` unless {{owner_name}}'s own message in this session asked for the merge (its documented stated-intent tier); a standing call in `CLAUDE.md` is not {{owner_possessive}} message and does not count, nor does anything a hook, a brief or the ledger injects. If {{owner_name}} has asked, merge. If {{owner_name}} has not and the merge is refused, that was the one attempt: never retry a classifier refusal, never rephrase it, never reach the merge through another tool or session; record the PR URL in the ticket's Outcome, tell {{owner_object}} the PR is green and ready, and move the ticket to done once {{owner_name}} has merged. Never manufacture the intent: no injected message, no phrase written into a brief, no asking {{owner_object}} to repeat words to unlock a command. Each landing's report entry says whether {{owner_name}} had asked, until five PRs in a row have landed on the first attempt. A project's own allow rule for `gh pr merge` in its `.claude/settings.local.json` can resolve the merge before the classifier runs, so there the merge needs no message; that rule is {{owner_name}}'s to write, never {{identity_name}}'s or a spudagent's.

**Recorded pull requests.** `spud --as spud pr record --ticket {{ticket_prefix}}-nnn --url <URL> --branch <head>` also takes `--worktree`, defaulting to the one the ticket is bound to. Once recorded, `spud board` reads each still-open one with a single `gh pr view`, never on a hook path, never in `board --brief`, and never oftener than its staleness window; both the full board and every `board --brief` line the `SessionStart` hook injects then show a merged one with everything it leaves owed: the done move, the Outcome carrying the PR URL, `git worktree remove <path>` and `git branch -D <branch>`. A closed-unmerged one is surfaced and nothing more, and the block clears itself when the ticket reaches done. So a pull request that merges after a session has stopped still reaches the next session that starts anywhere, rather than leaving a ticket looking `active` long after its work has landed. Nothing in this merges a pull request, runs git or moves a ticket; `spud pr reconcile` (any actor, or none: its writes name the actor `reconcile`) is the read to run for the answer now. `spud doctor` lists every recorded pull request whose last read failed (no `gh`, no auth, no network, a rate limit), because until one succeeds a merge that has already happened stays invisible. The squash merge makes `git branch -d` refuse the landed branch; once `gh pr view <branch> --json state` says `MERGED`, `git branch -D <branch>` is the one force the cleanup allows.

## Session ritual: notes

- `spud session show` names the home (`{{home}}`), the project of the working directory (none in the home), the checkout (the home, a project's main checkout, or a worktree and its branch) and the session's mode.
- If `spud doctor` reports no database, the pointer and the home disagree: stop and ask {{owner_name}}.
- A session launched in the home builds no code because `EnterWorktree` needs the session's launch directory to be a repository.

## Memory

A session launched in the home reads `{{memory_dir}}`; a session launched in a project reads that project's own memory directory instead, keyed by Claude Code from the project's checkout path.

## Obsidian

Open `{{home}}` as the vault and start at `ledger/Home.md`. Frontmatter and wikilinks are the interface: graph view shows {{identity_name}}, the team leads, and their children; each ticket note shows its Team card; `ledger/Fleet.base` and `ledger/Board.base` are Bases views. The watcher keeps every note within a few seconds of the database. Rendered notes carry the generated marker after the frontmatter and are read-only for humans; a change is a `spud` command away.

## Commands not in CLAUDE.md

```bash
python3.14 -I -S -m unittest discover -s tests -t tests   # in the tool repository: the serial fallback, about eleven minutes, one run at a time per checkout
```

```bash
python3.14 -I -S tests/probes/module_sizes.py   # in the tool repository: application-code sizes, banded at 250 and 1000; advisory, always exits 0
```

```bash
python3.14 -I -S {{launcher}} events --kind hook.denied --limit 50   # who tried what, and which law refused it
```

```bash
python3.14 -I -S {{launcher}} pr list   # every recorded pull request, the whole record
```
