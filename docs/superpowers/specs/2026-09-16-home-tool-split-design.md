# Spud's home and tool split: design

Date: 2026-09-16. Tickets: [[SPD-097]] (split the home from the tool), [[SPD-098]] (bind every code ticket to a worktree). Eric approved the design section by section in conversation on 2026-09-16; this document is the written form for his review.

## Problem

One repository, `~/Personal/Spud`, is three things at once: the source of the `spud` CLI, Spud's home (database, config, backups), and the Obsidian vault of rendered notes for every project. What that costs:

- **The tool's history is mostly ledger.** Since 2026-09-10, 314 of 444 commits touch `ledger/` or `reports/`, and 56 touch code. Every BadTakes ticket move is a commit in the tool's history.
- **The vault lags the database.** A note changes only when `spud render` runs, and it reaches git only at `spud ledger commit` on `main`.
- **Spud tickets fight the worktree guard.** A session inside a worktree may not run git against the main checkout, so every SPD code ticket goes worktree, `ExitWorktree`, commit the ledger on `main`. BadTakes keeps its ledger in another repository and has none of this.
- **Nothing checks that code is built in a worktree.** It depends on Spud remembering `EnterWorktree`.

## Decisions (Eric, 2026-09-16)

- The home is `~/Personal/SpudHome`: a plain directory, not a git repository, and the Obsidian vault. Eric reads the vault only in Obsidian on this Mac. The vault may go back into git later, not now.
- `~/Personal/Spud` stays the tool repository, registered as project `spud` with sessions `claim` and landing `merge`.
- `docs/` moves into the vault.
- The vault is kept current by a background renderer.
- A code ticket's worktree is enforced: the CLI and the hooks refuse, they do not warn.

## Goals and non-goals

Goals: no ledger write needs git; the vault follows the database within seconds; the tool repository's history is code; a code ticket is built in a worktree whether or not the session was launched in one.

Non-goals: putting the vault in git, reading it on other devices, pull-request landing for the tool, renaming the tool repository or its GitHub remote, the CLI creating worktrees by itself, any schema change beyond one column.

## 1. What lives where

**`~/Personal/SpudHome/`** (the home and the vault; not a git repository)

- `CLAUDE.md`: Spud's identity, laws and protocol, rewritten for this layout (section 7). A session launched here is always Spud: the board, triage, filing and deciding tickets for any project.
- `spud.config.json`.
- `.spud/`: `ledger.db`, `backups/`, `pycache/`, and new `logs/` and `render.lock`.
- `ledger/`, `reports/`, `docs/`, `.obsidian/`, with the same relative layout as today, so wikilinks, embeds and the `.base` views keep working.
- `.claude/settings.json`, written by `spud settings sync`.

**`~/Personal/Spud/`** (the tool; a git repository like any project)

- `bin/`, `tests/`, `.claude/agents/spudagent.md` and the `/spud` skill as sources, `.claude/skills/spudlib-modules/`, and a `CLAUDE.md` about developing the tool.
- `ledger/`, `reports/`, `docs/`, `spud.config.json` and the ledger hooks in the tracked `.claude/settings.json` are removed in one commit after the switch-over; history keeps them.
- As in any project, every path here is a deliverable: Spud's own files are the home's.

## 2. Finding the home

- `resolve_home` (`bin/spudlib/core/homeconf.py`) tries `SPUD_HOME`, then the `~/.config/spud/home` pointer, and otherwise fails with a message naming both. The fallback to the git common directory of the running script is removed.
- Hook lines keep an explicit `SPUD_HOME=<home>` and run `~/Personal/Spud/bin/spud`, the tool repository's main checkout. That checkout is the running copy. It changes only when a ticket merges, so a merge is a deploy: the full suite runs on the branch first, as it does today.
- Every code path that assumes the home is a git checkout works with a plain directory: `session show` (it prints `checkout home` and mode `spud` for a session launched in the home), the path rule, `settings sync`, `schedule install`, `doctor`. `spud ledger commit` is removed, along with every message, hint and template line that names it.
- The home is not a project. Project `spud` is re-registered with root `~/Personal/Spud`, sessions `claim`, landing `merge`. The key `home` is reserved, and `project add` refuses it.

## 3. Paths

- A deliverable glob is bare (the ticket's project checkout: its bound worktree once section 6 lands), `<key>:<glob>` (another project's), or `home:<glob>` (the home). What briefs wrote as `spud:docs/...` becomes `home:docs/...`.
- Law 5 holds in the home: nobody hand-edits `ledger/tickets/`, `ledger/teams/` or `reports/`; the CLI writes them.
- Spud's own files are the home's: `CLAUDE.md`, `spud.config.json`, `ledger/Home.md`, `ledger/Spud.md`, the `.base` files, `ledger/_templates/`, `docs/superpowers/specs/`.

## 4. Keeping the vault current

- **`spud render --watch`** runs until it receives SIGTERM. Every 2 seconds it reads the highest event id whose kind is not `render` and compares it with the `through_event_id` of the last render. When the database is ahead, it renders.
- **LaunchAgent `local.spud.render`** (RunAtLoad, KeepAlive) runs the watcher, logging to `<home>/.spud/logs/render.log`, truncated at each start. `spud --as spud schedule install|show` handles it alongside `local.spud.backup`.
- **A render that changes nothing writes nothing.** A render that writes no file, restyles nothing and finds no new conflict makes no database write. Today every render writes a summary event (`bin/spudlib/commands/publish.py`, the final `write_event`).
- **Hand edits.** The render leaves a hand-edited note alone, as today, but writes its conflict event once per path and on-disk sha256, not on every pass. `spud doctor` lists open conflicts with the `render --discard` and `import --file` commands that settle them.
- **One render at a time.** Renders take an exclusive lock on `<home>/.spud/render.lock`; a manual `spud render` waits for the watcher's pass.
- **When the watcher is down,** `spud doctor` reports it and `spud board --brief` adds one line saying so.
- **Hooks are unchanged**, so their timing budget is untouched. A manual `spud render` remains valid.
- **Measure** a no-change pass and a full pass over the current ledger (404 files) and record both on SPD-097.

## 5. The switch-over command

`spud --as spud home move --to <dir> [--dry-run]`.

**Preconditions.** Each one refuses with a non-zero exit that names what is in the way:
- no member is `planned` or `active` in any project;
- `<dir>` does not exist, or is an empty directory, and is not inside a git work tree;
- no rendered file in the current home has an open conflict.

**Steps**, in order, each reported:

1. Write a checked backup in the current home.
2. Create `<dir>/.spud/`, copy the database with SQLite's online backup API, run `integrity_check`, and compare row counts table by table.
3. Copy `spud.config.json`, `ledger/`, `reports/`, `docs/`, `.obsidian/` and `.spud/backups/`.
4. Write `~/.config/spud/home`.
5. Set project `spud`'s sessions to `claim`, then re-sync hooks: the new home's `.claude/settings.json`; every installed project with `project install` (the tool repository gets `.claude/settings.local.json`, like BadTakes); the ledger hooks removed from the tool repository's tracked `.claude/settings.json`, leaving that change in the working tree for the removal commit; the `~/.claude/` copies with `project sync --all`.
6. Reinstall `local.spud.backup` and `local.spud.render` with the new paths.
7. Render into the new home and require zero files written; run `spud doctor`.
8. Rename the old `.spud/` to `.spud-moved/`. Deleting it is Eric's call once he is satisfied.
9. Print what is left by hand: end the session that ran the move, whose hooks were loaded against the old home, and continue in a new session launched in `~/Personal/SpudHome`; open that directory as the vault in Obsidian; make the removal commit in the tool repository.

`--dry-run` checks the preconditions and prints the steps.

**Rollback**, until the removal commit: point `~/.config/spud/home` at the old path, rename `.spud-moved/` back, and rerun `settings sync`, `project install` and `schedule install`. Ledger writes made after step 4 exist only in the new home, and a rollback loses them.

## 6. Ticket-bound worktrees (SPD-098)

- **Migration `0004_ticket_worktree`** adds `tickets.worktree TEXT`: the absolute path of a linked worktree, NULL while unbound. `user_version` becomes 4.
- **Which tickets need one.** A ticket needs a worktree when any of its members' deliverables resolves into a project checkout (a bare or `<key>:` glob). A ticket whose deliverables are all `home:` never needs one.
- **Binding.** The first `member new` that needs a worktree binds the ticket to the caller's checkout (`git rev-parse --show-toplevel`). That checkout must be a linked worktree of the ticket's project (same `--git-common-dir`), whether the app created it when Eric launched the session or Spud entered it with `EnterWorktree`. The binding writes a `ticket.worktree` event.
- **Refusals** (exit 5):
  - from the project's main checkout: names the step, `EnterWorktree name: <key in lowercase>-<slug>`, then rerun;
  - from outside the project, for instance a session in the home: names the project root to start the ticket from;
  - when the ticket is bound to another worktree that still exists (`git worktree list`): names it. A bound path that no longer exists counts as unbound, and the ticket rebinds to the caller's worktree, with the old path in the event.
- **Batches.** A worktree may carry several open tickets, as SPD-049's hook batch did.
- **Children** inherit the ticket's binding; a member's `member new` never rebinds.
- **Enforcement.** The edit hook and the Bash hook's write-target check resolve a member's bare and `<key>:` globs only inside its ticket's worktree. The same path in the main checkout or in another worktree is refused, with the bound worktree named. Spud's own writes are unaffected.
- **Display.** `spud card` and `spud board` show the worktree and its current branch, which is read from git at display time, never stored. Spud renames the branch by the project's rules, as today.
- **After landing**, Spud removes the worktree as today, and `tickets.worktree` stays as history.
- **Timing.** The hooks' extra lookup must keep `tests/probes/hook_timing.py` within 1 ms of `main`.

## 7. CLAUDE.md, agent, skill and memory

- **The home's `CLAUDE.md`** is Spud's, rewritten from today's:
  - no `render` or `ledger commit` steps in the protocol;
  - Law 10 becomes "code is built in the ticket's bound worktree; the ledger needs no git";
  - Main and worktrees is replaced by a short section on the home and ticket worktrees;
  - the Ledger v1 paths are updated;
  - In another project covers the tool repository too.
- **The tool repository's `CLAUDE.md`** covers developing `spud`: the suite, the probes, the `spudlib-modules` skill, and that the main checkout is the running copy, so a merge is a deploy.
- **`spudagent.md` and the `/spud` skill** lose every mention of `ledger commit` and of the ledger root as a checkout; `project sync --all` installs them.
- **Memory.** A session launched in the home reads `~/.claude/projects/-Users-ericlugo-Personal-SpudHome/memory/`. Spud copies the current memories there and rewrites `worktree-session-git-guard` and `render-after-spawn` for the new flow.

## 8. Build order

1. **SPD-097**, built in a worktree of the tool repository under today's flow, for the last time: sections 2 to 5, the tool-side text of section 7, and the tests. Merged.
2. **The switch-over**, in a quiet window: Spud runs `spud home move --to ~/Personal/SpudHome`, Eric opens the vault, Spud writes the home's `CLAUDE.md` and copies the memories, then the removal commit lands in the tool repository and is pushed.
3. **SPD-098**, built under the new flow, which makes it the flow's first real test: section 6. Merged.

## 9. Testing

- **The real home is off limits.** Every test builds its home in a temporary directory through `SPUD_HOME`, and the suite fails if the real home's database is opened (the cause SPD-092 could not name).
- **SPD-097:**
  - the resolution order;
  - a plain-directory home under `session show`, `settings sync`, `schedule install` and `doctor`;
  - `home:` globs, and the refusal of the reserved key;
  - a no-change render writes nothing;
  - the watcher renders once after an event and not after its own render event;
  - a conflict is logged once;
  - the render lock;
  - `home move`: `--dry-run`, each precondition, a full move between temporary directories with the zero-write render, and a rollback.
- **SPD-098:**
  - migration 0004;
  - binding: from a main checkout (refused), a launched or entered worktree (bound), a home session (refused), a stale binding (rebound), a batch;
  - a member's write into the main checkout is refused by both hooks;
  - the hook timing probe.

## 10. Risks and related tickets

- **The running CLI is the tool repository's main checkout.** A plain, unclaimed session editing `bin/` on `main` changes the hooks for every session. Accepted: claimed sessions are refused by section 6, and plain sessions are Eric's own.
- **Backups stay on this disk.** Nothing copies the database or the vault off the disk (14 daily checked backups on the same disk). Accepted until the vault goes back into git.
- **Related tickets:**
  - SPD-092 (a suite run against the real ledger) should close with SPD-097's test guard;
  - SPD-083 (suite runs sharing `bin/__pycache__`) is to be rechecked after the split;
  - SPD-033 (native deny rules for the state directory) must follow the new path.
