# CLAUDE.md

This file guides Claude Code when developing `spud`, the ledger CLI, in this repository. Spud's identity, his laws, the spudagent protocol, and the ledger itself live in his home's own `CLAUDE.md`, `/Users/ericlugo/Personal/SpudHome/CLAUDE.md`; that file is the reference for all of it and nothing here restates it.

## What this repository is

The source of `spud`: `bin/spud` (the launcher), `bin/spud_ledger.py` (the entry), `bin/spudlib/` (the package), its tests and probes under `tests/`, `.claude/agents/spudagent.md` (the source `project sync --all` copies to `~/.claude/`), and the project skill `spudlib-modules`. It is registered as project `spud` — tickets `SPD-nnn`, teams `SPUD-nnn`, sessions `claim`, landing `merge` on `main` — and it is not Spud's home: it holds no `ledger/`, `reports/`, `docs/` or `spud.config.json` (removed on SPD-097; history keeps them). The test suite keeps its own config fixture, `tests/fixtures/spud.config.json`, byte for byte what the repository's config held before the split; every scratch home a test or a probe builds starts from that copy, never from the real one.

## Sessions here

A session launched in this checkout is plain until claimed — by `/spud`, or by a prompt naming an existing `SPD-nnn` ticket. Once claimed, the home's `CLAUDE.md` binds for delegation, the ledger, and who writes what; this file governs how the tool itself is built, verified, committed and landed.

## The main checkout is the running copy

Every ledger hook line, in every registered project, and both LaunchAgents (`local.spud.backup`, `local.spud.render`) run `/Users/ericlugo/Personal/Spud/bin/spud`. A merge into this repository's `main` is a deploy: it changes the CLI and every hook for every session at once, so the full suite passes on the branch before every merge, without exception. An edit of `bin/` on `main` itself deploys the same way, at once, whatever session makes it — a plain one included — so `bin/` is never edited there. Code is built in a worktree, `.claude/worktrees/spd-nnn-<slug>` on branch `worktree-spd-nnn-<slug>`, and `spud` is always run from the main checkout's launcher, never a worktree's own copy.

## Before writing code under bin/

Read `.claude/skills/spudlib-modules/SKILL.md` first: the import rule (a module imports modules, never names, so the suite's patching reaches the call site), where a new module goes among the package's nine directories, what must stay off the hook path and how to time a change to it, the launcher's loader and its bytecode cache, the one guarded import cycle in `shell/`, module naming, and the size rule — ~250 lines is a look-again point for a module, never a cap, and the scope is application code only, never tests.

## Verification

The full suite, from the checkout or a worktree root:

```bash
python3.14 -I -S -m unittest discover -s tests -t tests   # about eleven minutes, leaves no bytecode
```

It runs entirely against scratch homes built from `tests/fixtures/spud.config.json`; `tests/helpers.py` points `SPUD_HOME` at a home that cannot exist before each test sets its own, so a run can never open the real ledger. `tests/probes/` covers what the suite doesn't — read a probe's own docstring for its exact arguments before running it:

- `hook_timing.py [ROUNDS] LAUNCHER [LAUNCHER ...]` — hook-run medians, interleaved across launchers; run it against main's launcher after any change to the hook path, and pass only when every hook case's median lands within 1 ms of main's in the same run.
- `module_sizes.py [PATH ...]` — application-code line counts and each file's largest definition, banded at 250 and 1000 lines; advisory, always exits 0.
- `session_diff.py LAUNCHER_A LAUNCHER_B` — 44 scripted CLI and hook calls compared step by step after masking; run after a refactor meant to leave behavior alone.
- `render_timing.py [TICKETS] [MEMBERS_PER_TICKET] [LAUNCHER]` — a full pass and a no-change pass of `spud render` over a synthetic ledger, timed.
- `headless.py <scenario> [--root DIR] [--model haiku]` and `headless_projects.py <scenario>` — real `claude -p` sessions against a scratch home, real API usage (roughly $0.10 to $0.50 a scenario on haiku); run one scenario at a time.

## Landing

The full suite green on the branch; `git merge-tree --write-tree main <branch>` as a dry run; merge `main` into the branch and rerun the suite if `main` moved since the branch was cut; `ExitWorktree` (`keep`); `git merge --no-ff <branch>` at the main checkout and push. Then the sync the change needs: `spud --as spud settings sync` refreshes the home's own `.claude/settings.json` after a change to what the hooks or allow rules generate; `spud --as spud project sync spud` (or `--all`) refreshes this and every other project's installed `.claude/settings.local.json`, `~/.claude/agents/spudagent.md` and the `/spud` skill after a change to `spudagent.md`, the skill text, or the installed hook wiring. Finally `git worktree remove .claude/worktrees/<name>` and `git branch -d <branch>` — never `--force`. Every commit names its ticket.

## Interpreter

Always `python3.14 -I -S`: isolated, no `site`, nothing on `sys.path` but the interpreter's own. Standard library only — no third-party dependency, ever.

## Commands

```bash
python3.14 -I -S -m unittest discover -s tests -t tests   # the full suite, about eleven minutes
```

```bash
python3.14 -I -S -m unittest discover -s tests -t tests -p test_package.py   # the package-shape guard alone, seconds
```

```bash
python3.14 -I -S tests/probes/hook_timing.py 30 bin/spud <other checkout>/bin/spud   # hook-run medians, interleaved
```

```bash
python3.14 -I -S tests/probes/module_sizes.py   # application-code sizes, banded at 250 and 1000; advisory
```

```bash
python3.14 -I -S /Users/ericlugo/Personal/Spud/bin/spud session show   # the home, this checkout's project, the checkout, and whether this session is claimed
```

```bash
git log --oneline -20   # what has landed in the tool, by ticket
```
