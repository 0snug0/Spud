# spud

`spud` is the ledger CLI behind Spud: a Claude Code orchestration layer that gives an AI assistant a
persistent second brain. It has three parts. A ledger CLI (this repository) drives a single SQLite
database of tickets, teams and members. A set of Claude Code hooks enforces Spud's laws on every
session and every sub-agent it spawns — who may write what, when a sub-agent may be spawned, when a
worktree may be entered, what a session must record before it ends. And an Obsidian vault is rendered
from that database, so the ledger is also a set of readable, linked notes: a board of tickets, one
page per ticket and per team member, and a daily report.

The design lets Spud delegate real work to short-lived sub-agents ("spudagents"), each spawned for one
ticket with a name, a brief, and a narrow set of files it is allowed to touch, while the ledger itself
stays the single source of truth for what was planned, what was done, and what is still open. Spud's
own identity, laws and protocol live in the `CLAUDE.md` that `spud init` writes into the home it
builds; this repository is only the program that reads and writes the ledger.

## Requirements

- Python 3.14, invoked as `python3.14 -I -S` (isolated, no `site`): the CLI depends on nothing outside
  the standard library.
- macOS, if you want the two LaunchAgents that back up the ledger daily and keep the Obsidian vault
  rendered as it changes. The CLI itself has no platform dependency.
- [Claude Code](https://claude.com/claude-code), to run sessions and sub-agents against the ledger.
- [Obsidian](https://obsidian.md), optional, to browse the rendered vault as linked notes and Bases
  views instead of through the CLI.

## From a fresh clone

One command, run from the repository root in a terminal:

```bash
python3.14 -I -S bin/spud init
```

It asks four questions — the home to build (default `~/SpudHome`), your name, your pronouns, and, if
you are registering your own repository as this home's first project, its ticket and team prefixes —
and ends by running `spud doctor`, which reports `problems    none` once everything is in place. Every
value can also be given as a flag instead of answered at a prompt: `python3.14 -I -S bin/spud init
--help` lists all of them, including `--owner-name` (who the home is for), `--project-root`,
`--no-project`, `--landing` (`merge` or `pr`), `--sessions` (`claim` or `always`), `--no-schedule` (skip
the two LaunchAgents), `--no-vault` (skip the Obsidian scaffolding), `--yes` (never prompt) and
`--dry-run` (check preconditions and print the steps without writing anything).

Inside a Claude Code session opened in this clone, `/spud-init` walks through the same command
interactively — see `.claude/skills/spud-init/SKILL.md`.

## Layout

- `bin/spud` — the launcher; every invocation of the CLI runs through it.
- `bin/spud_ledger.py` — the entry point the launcher loads.
- `bin/spudlib/` — the package: the CLI parser and commands, the ledger schema and migrations, the
  hook implementations, and the code that renders the database into the Obsidian vault.
- `share/` — everything the tool ships into a home it builds or updates: the starting config, the
  generated `CLAUDE.md` and vault scaffolding, the Claude Code skills, and the sub-agent definition
  (installed as a base and one variant per effort level, so each sub-agent runs at the effort it was
  planned at), each a template filled in with values specific to the home being built.
- `tests/` — the test suite (`suite.py`, run in parallel against scratch homes) and a set of
  standalone probes for timing, hook behavior and other properties the suite doesn't cover.
- `.claude/skills/` — this repository's own Claude Code skills: `spud-init` (the interactive walk
  through `spud init`) and `spudlib-modules` (the rules `bin/spudlib/` is built on, for anyone changing
  the code under `bin/`).

## Tests

Run the full suite from the repository root:

```bash
python3.14 -I -S tests/suite.py
```

It reads every tracked or untracked-and-not-ignored file into a scratch copy, runs the suite there
across one worker interpreter per core, and removes the copy afterward — it writes nothing into the
checkout. The run ends with one line naming the result and a digest of the tree it covered. While
iterating on a smaller area, name the modules, classes or tests to run instead of the whole suite:

```bash
python3.14 -I -S tests/suite.py test_package test_hooks_session.StopTest
```

To run only the tests a change reaches, name the branch it is compared against (default `main`):

```bash
python3.14 -I -S tests/suite.py --changed main
```

It takes every file changed since the branch left `main`, committed or not, and runs the test modules
that `tests/suite_map.json`, a plain data file, names for them. For the program's own code and the
shipped files, the map defers to a measured dependency table, `tests/suite_deps.json`: which test
modules ran or read each file. A file the map does not name, or one that every test depends on, runs
the full suite instead. The last line says `affected` and which rules chose the modules, so it is never
mistaken for a full run; `--dry-run` shows the choice without running anything. A contributor verifies
a change this way, and the full suite runs once, at landing, on the tree that merges. After a change
that moves what the tests exercise, such as a new test module or a new module of the program, measure
the table again; `--changed` warns when the program's files differ from the ones the table was
measured on:

```bash
python3.14 -I -S tests/suite_deps.py
```

Only one run goes at a time on a machine, since two side by side would each take every core. A
second run waits for the first and says whose run it is waiting for; `--no-wait` makes it exit at once
with a non-zero status instead. `--background` runs the suite at macOS's background priority, so it is
quieter and slower. A run stopped by Ctrl-C, SIGTERM or SIGHUP removes its scratch copy; a run killed
outright leaves it for the next run to remove.

A serial fallback runs the same tests one at a time directly against the checkout, with no parallelism
and no digest:

```bash
python3.14 -I -S -m unittest discover -s tests -t tests
```

Either way, every test runs against a scratch home built from this repository's own shipped config, so
a test run never touches a real Spud home. Each worker builds that home once, the way `spud init`
builds a real one beside a checkout of the tool, and puts a copy of it back before every test. See
`CLAUDE.md` for the full verification and landing process, including the standalone probes under
`tests/probes/`.

## License

MIT. See [LICENSE](LICENSE).
