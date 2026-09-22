# spud

`spud` is the ledger CLI behind Spud, a second brain built as an Obsidian vault plus a SQLite database. This
repository is its source: `bin/spud` (the launcher), `bin/spud_ledger.py` (the entry), `bin/spudlib/` (the package),
and `share/` (what the tool ships into a home it builds).

## From a fresh clone

One command, run from the repository root in a terminal:

```bash
python3.14 -I -S bin/spud init
```

It asks four questions — the home to build (default `~/SpudHome`), your name, your pronouns, and, if you are
registering your own repository as this home's first project, its ticket and team prefixes — and ends by running
`spud doctor`, which reports `problems    none` once everything is in place. Every value can also be given as a
flag instead of answered at a prompt: `python3.14 -I -S bin/spud init --help` lists all of them.

Inside a Claude Code session opened in this clone, `/spud-init` walks through the same command interactively —
see `.claude/skills/spud-init/SKILL.md`.

Everything else about this repository — how it is built, verified and landed — is in `CLAUDE.md`.
