# CLAUDE.md

This file guides Claude Code when developing `spud`, the ledger CLI, in this repository. Spud's identity, his laws, the spudagent protocol, and the ledger itself live in his home's own `CLAUDE.md` — the home is the directory `spud session show` prints as `home`, and it is this machine's, named in no file of this repository. That file is the reference for all of it and nothing here restates it.

## What this repository is

The source of `spud`: `bin/spud` (the launcher), `bin/spud_ledger.py` (the entry), `bin/spudlib/` (the package), its tests and probes under `tests/`, `share/` (every file the tool ships — the config, `CLAUDE.md` and the vault scaffolding a home starts from, each a template whose `{{marks}}` `bin/spudlib/core/shipped.py` fills from `Ctx`, SPW-001; and `share/agents/spudagent.md`, the one that goes to no home but to `~/.claude/agents/spudagent.md`, which `project install` and `project sync --all` render there with `{{launcher}}` filled in by `bin/spudlib/projects/agentdef.py` — the launcher that runs on the machine they install on, SPW-002. It sits under `share/` and no longer at this repository's own `.claude/agents/`, because Claude Code prefers a project-scope definition and every spudagent in this checkout was reading the unrendered template instead of the installed copy — SPW-004), and the two project skills `spudlib-modules` and `spud-init` (`/spud-init`, SPW-001: one command from a fresh clone to a working Spud home; `README.md` has the command by hand). It is registered as project `spud` — tickets `SPD-nnn`, teams `SPUD-nnn`, sessions `claim`, landing `merge` on `main` — and it is not Spud's home: it holds no `ledger/`, `reports/`, `docs/` or `spud.config.json` of its own (removed on SPD-097; history keeps them). The test suite keeps one fixture of its own, `tests/fixtures/name_pool.json`: every scratch home a test or a probe builds renders `share/spud.config.json`, the file a real home starts from, with the suite's own marks from `tests/fixtures/config_marks.json` and, in place of the shipped owner's own canon, that fixture's `naming.pool` — the one block the suite does not take from the shipped file (SPW-001; SPD-157).

`share/CLAUDE.md` has a budget, kept by `tests/test_claude_md_budget.py` (SPD-169): rendered, at most 24,000 characters, and at most 29,000 with the rendered `share/spud.config.json`, so both fit one read under the Bash tool's 30,000-character output cut; a change that would exceed it moves detail into `share/skills/spud-reference/SKILL.md` instead.

## Sessions here

A session launched in this checkout is plain until claimed — by `/spud`, or by a prompt naming an existing `SPD-nnn` ticket. Once claimed, the home's `CLAUDE.md` binds for delegation, the ledger, and who writes what; this file governs how the tool itself is built, verified, committed and landed.

## The main checkout is the running copy

Every ledger hook line, in every registered project, and both LaunchAgents (`local.spud.backup`, `local.spud.render`) run the main checkout's `bin/spud`. That absolute path is this machine's, so no file here names it (SPW-002): `spud doctor` prints it as `tool`, the home's own `CLAUDE.md` spells it out, and the derivation below finds it from any checkout of this repository. A merge into this repository's `main` is a deploy: it changes the CLI and every hook for every session at once, so the full suite passes on the branch before every merge, without exception. An edit of `bin/` on `main` itself deploys the same way, at once, whatever session makes it — a plain one included — so `bin/` is never edited there. Code is built in a worktree, `.claude/worktrees/spd-nnn-<slug>` on branch `worktree-spd-nnn-<slug>`, and `spud` is always run from the main checkout's launcher, never a worktree's own copy.

## Before writing code under bin/

Read `.claude/skills/spudlib-modules/SKILL.md` first: the import rule (a module imports modules, never names, so the suite's patching reaches the call site), where a new module goes among the package's nine directories, what must stay off the hook path and how to time a change to it, the launcher's loader and its bytecode cache, the one guarded import cycle in `shell/`, module naming, and the size rule — ~250 lines is a look-again point for a module, never a cap, and the scope is application code only, never tests.

## Verification

The full suite, from the checkout or a worktree root:

```bash
python3.14 -I -S tests/suite.py   # the full suite on every core; the last line names the tree it ran
```

`tests/suite.py` (SPD-102) reads every file `git ls-files -c -o --exclude-standard` lists (tracked, or untracked and not ignored) into a scratch copy, runs the suite there in one worker interpreter per core, and removes the copy; it writes nothing into the checkout, bytecode included. A file edited during a run reaches no worker, so a run tests exactly the tree it started from, and two runs in one checkout share no file, only the cores (SPD-083). Failures print as unittest prints them, and the run ends with one line on stdout, `OK: <count> tests in <seconds> s on <workers> workers; tree <digest>`: the result and the digest of the tree it covered. `python3.14 -I -S tests/suite.py --digest` prints the checkout's digest now, running nothing. It changes when any listed file's content, mode or presence changes, and never for bytecode, scratch files or anything else git ignores.

While iterating, run only the modules, classes or tests you touched, `python3.14 -I -S tests/suite.py test_members test_hooks_session.StopTest`, and verify with `--changed` once at the end. A named run's final line says `partial`, and it proves nothing at landing.

A member verifies with `python3.14 -I -S tests/suite.py --changed [BASE]` (SPD-234; BASE defaults to `main`) and records its final line in its `member result`; it runs the full suite only when its brief says so, because the one full run that proves what deploys is Spud's, at landing, on the tree that merges. `--changed` takes every file changed against the merge base of BASE and HEAD, tracked or untracked, and runs the test modules the path map `tests/suite_map.json` names for them; `--dry-run` prints each path, the rule that took it and the modules, and runs nothing. The map is data, its `about` says how a path matches, and it is widened only by a dependency table on record (SPD-233's Result), never by guess. A path in its `full` list — the launcher, the entry, `core/`, `state/`, `cli/`, `tests/helpers.py`, the runner and its map, the fixtures, `share/spud.config.json` — or in no rule runs the full suite; a changed test module runs itself and every test module importing it. The final line says `(affected: <rules> against <BASE>, <n> modules)`, or `(full, --changed against <BASE>: <path> …)` when it fell back, and ends with the digest, so a selection is never taken for a full run. `-j N` overrides the worker count; `--cold` gives every scratch home an empty bytecode cache, as before SPD-102. Two rules keep the parallel run honest: a test that asserts an upper bound on wall time carries `helpers.wall_clock` and runs after every other test is done, and a `SpudTestCase` that asserts what the launcher caches or what `init` or a backup leaves in `.spud/` sets `warm_cache = False`. The hook tests are `tests/test_hooks_<subject>.py`, over what `tests/hookcase.py` shares; the Bash and edit tools' tests call the hook in the test's own process, against one home per class (SPD-231), so a cache on the hook path belongs at module level, where it is emptied between their runs only if `hookcase.HOOK_CACHES` names it, and `InProcessParityTest` fails on any table a run changes that is not named, whether a module holds it or one of its functions or classes does (a default argument, a closure cell, a class attribute, an object's `__dict__`).

One run at a time per machine (SPD-232): every run, named or full, takes an flock on `spud-suite.lock` in the user's temp directory, and a second run waits, saying on stderr whose run (pid, checkout, start time) it waits for, then starts when that one ends; `--digest` never waits. `--no-wait` exits 75 at once with one line instead of queueing. `--background` runs the suite and every process it starts at macOS's background priority, quieter and slower, and puts the `wall_clock` tests back at normal priority. A run ended by Ctrl-C, SIGTERM or SIGHUP kills its workers and removes its scratch directory; a run killed outright leaves it for the next run, which removes every `spud-suite-*` directory whose run is gone. Workers' temp directory is inside the run's scratch directory, so every scratch home goes with the run, and a test that starts a nested `tests/suite.py` names its own lock in `SPUD_SUITE_LOCK` (a run passes that variable to no worker).

The serial command is the fallback: the same tests, one at a time, in the checkout itself, many times slower. It reads the live files, removes at exit the bytecode its first two modules wrote, and prints no digest, so run it only while nothing else writes to or runs in that checkout, and never as a landing's evidence:

```bash
python3.14 -I -S -m unittest discover -s tests -t tests   # the serial fallback, leaves no bytecode
```

Either way the suite runs entirely against scratch homes built from the rendered `share/spud.config.json` (`tests/helpers.CONFIG` and `config_text()`, and the same pair in every probe); `tests/helpers.py` points `SPUD_HOME` at a home that cannot exist before each test sets its own, so a run can never open the real ledger. `tests/probes/` covers what the suite doesn't — read a probe's own docstring for its exact arguments before running it:

- `hook_timing.py [ROUNDS] LAUNCHER [LAUNCHER ...]` — hook-run medians, interleaved across launchers; run it against main's launcher after any change to the hook path, and pass only when every hook case's median lands within 1 ms of main's in the same run.
- `module_sizes.py [PATH ...]` — application-code line counts and each file's largest definition, banded at 250 and 1000 lines; advisory, always exits 0.
- `session_diff.py LAUNCHER_A LAUNCHER_B` — scripted CLI and hook calls compared step by step after masking; run after a refactor meant to leave behavior alone.
- `render_timing.py [TICKETS] [MEMBERS_PER_TICKET] [LAUNCHER]` — a full pass and a no-change pass of `spud render` over a synthetic ledger, timed.
- `headless.py <scenario> [--root DIR] [--model haiku]` and `headless_projects.py <scenario>` — real `claude -p` sessions against a scratch home, real API usage (roughly $0.10 to $0.50 a scenario on haiku); run one scenario at a time.
- `shell_probe.py [FILE | -]` — runs a snippet under `zsh -f -o nobareglobqual`, `zsh -f` and `/bin/bash` in a throwaway directory, inside a `sandbox-exec` profile that lets it run no git, xcrun, python or launcher, write nothing outside that directory, read nothing under your home, and reach no network, and prints each shell's version, status, stdout and stderr; the way a member in a worktree, refused every command that runs zsh or bash, gets live shell evidence (SPD-094). Exits 3 without running anything where the sandbox cannot apply.
- `context_limit.py SIZE [--filler ascii|latin|emoji] [--model haiku] [--root DIR]` — a real `claude -p` session measuring how large a SessionStart hook's `additionalContext` reaches the model whole, and whether the harness counts code points, UTF-16 units or bytes; real API usage, a few cents a run on haiku; run one size at a time.
- `subagent_effort.py SCENARIO [--model haiku] [--claude PATH] [--root DIR]` — real `claude -p` sessions reading, from each child's transcript, the effort a subagent spawned through the Agent tool runs at: a definition's `effort:` against the session's `effortLevel`, a definition with none, and (`variants`) the `spudagent-<effort>` definitions `project install` renders, spawned by `subagent_type`; real API usage, a few cents a scenario on haiku; `--claude` names a Claude Code that serves Opus 5.5 when the one on `PATH` does not.

## Landing

`git merge-tree --write-tree main <branch>` as a dry run; merge `main` into the branch if it moved since the branch was cut; then the full suite, once, on that tree, the one that merges. A member's `--changed` line is never landing evidence. A green full run's line (Spud's, or a member's whose brief asked for one) counts for as long as `python3.14 -I -S tests/suite.py --digest` in the worktree prints the digest on it, and the suite is never rerun for an unchanged digest; a fix after the run or `main` merged in changes it. Committing the tree on the branch does not. `ExitWorktree` (`keep`); `git merge --no-ff <branch>` at the main checkout and push. Then the sync the change needs: `spud --as spud settings sync` refreshes the home's own `.claude/settings.json` after a change to what the hooks or allow rules generate; `spud --as spud project sync spud` (or `--all`) refreshes this and every other project's installed `.claude/settings.local.json`, `~/.claude/agents/spudagent.md` with its effort variants `spudagent-<effort>.md`, and the `/spud` skill after a change to `share/agents/spudagent.md`, the skill text, or the installed hook wiring. Finally `git worktree remove .claude/worktrees/<name>` and `git branch -d <branch>` — never `--force`. Every commit names its ticket.

## Interpreter

Always `python3.14 -I -S`: isolated, no `site`, nothing on `sys.path` but the interpreter's own. Standard library only — no third-party dependency, ever.

## Commands

```bash
python3.14 -I -S tests/suite.py   # the full suite on every core, once at landing on the tree that merges; ends with the result and the tree's digest
```

```bash
python3.14 -I -S tests/suite.py --changed   # a member's verification: the modules tests/suite_map.json names for what changed against main
```

```bash
python3.14 -I -S tests/suite.py --changed main --dry-run   # each changed path, the rule that took it, and the modules; runs nothing
```

```bash
python3.14 -I -S tests/suite.py --digest   # the digest of the checkout's tree now; runs nothing
```

```bash
python3.14 -I -S tests/suite.py test_package test_hooks_session.StopTest   # named modules, classes or tests only, while iterating
```

```bash
python3.14 -I -S tests/suite.py --no-wait   # exit 75 at once if another run holds the machine's suite lock, rather than queue
```

```bash
python3.14 -I -S tests/suite.py --background   # at macOS's background priority, workers included: quieter, slower
```

```bash
python3.14 -I -S -m unittest discover -s tests -t tests   # the serial fallback, many times slower; one run at a time per checkout
```

```bash
python3.14 -I -S tests/probes/hook_timing.py 30 bin/spud <other checkout>/bin/spud   # hook-run medians, interleaved
```

```bash
python3.14 -I -S tests/probes/module_sizes.py   # application-code sizes, banded at 250 and 1000; advisory
```

```bash
main=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")   # the main checkout, from it or from any of its worktrees
python3.14 -I -S "$main/bin/spud" session show   # the home, this checkout's project, the checkout, and whether this session is claimed
```

```bash
git log --oneline -20   # what has landed in the tool, by ticket
```
