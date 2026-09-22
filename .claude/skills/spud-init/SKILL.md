---
name: spud-init
description: One command from a fresh clone of this repository to a working Spud home — the database, the vault scaffolding, the home's own hooks, and the two LaunchAgents. Only when a person types /spud-init.
disable-model-invocation: true
---
# Bootstrapping Spud from this clone

`~/.claude/skills/spud/SKILL.md` is written by `project install`, which needs a database, which needs a home — which
is what `spud init` creates. A user-scope skill cannot bootstrap anything, so this one ships here instead, at project
scope, and reaches a session in this clone with nothing installed at all.

Find the repository root first, and run every command below from there. Never spell a path of your own — only the
ones the person gives you.

```bash
root=$(git rev-parse --show-toplevel)
cd "$root"
```

`bin/spud init` takes a fresh clone — or an existing one with no home yet — to a home whose database validates,
whose vault is scaffolded, whose own `.claude/settings.json` carries the ledger hooks, whose first project (if any)
is installed, and whose two LaunchAgents are loaded (unless skipped). It ends by running `spud doctor` and refuses
on anything that comes back but `problems    none`. It takes far more flags than the two recipes below use; read
`python3.14 -I -S bin/spud init --help` and `bin/spudlib/commands/homeinit.py` before trusting any paraphrase of them,
this one included. Two of them have no default and are never guessed: `--ticket-prefix` and `--team-prefix`. A prefix
sits in every rendered file name and every wikilink forever, so ask the person for both rather than inventing either.

Always add `--yes` when running this from inside a session: without it, a value not given falls back to an
interactive prompt, and this skill has no tty to answer one on. With `--yes`, every value not given takes its
default, and a value with no default (a prefix, or a home with neither `--home`, nor `SPUD_HOME`, nor an existing
pointer) is refused by name instead.

## Recipe 1: your own repository

The common case — a home for someone whose work lives in a repository other than this one:

```bash
python3.14 -I -S bin/spud init --home <the home to build, e.g. ~/SpudHome> \
  --project-root <path to their repository> --ticket-prefix <XXX> --team-prefix <XXXX> --yes
```

Ask for the home directory (offer `~/SpudHome` if they have no preference), the path to their repository, and the
two prefixes (upper-case letters and digits, starting with a letter, and the two must differ from each other).
Everything else — `--landing` (`merge` or `pr`, default `merge`), `--sessions` (`claim` or `always`, default
`claim`), `--default-branch`, `--no-schedule` — takes its default unless they ask for something else. `--no-project`
is the shape for a home with no first project at all: an empty registry that can hold no ticket until `project add`.

## Recipe 2: the Spud clone itself

Developing `spud` needs a home the same way any other project does — this tool repository registers as project 1
of its own home, with its own prefixes:

```bash
python3.14 -I -S bin/spud init --home <the home to build> --project-root "$root" \
  --ticket-prefix <XXX> --team-prefix <XXXX> --yes
```

**Prefix collision warning.** If this repository is already developed from another machine with a home of its own,
that machine's prefixes are already in use forever — a ticket key and a team key never change once written. Choose
prefixes that do not collide with the other machine's before running this. That is why there is no flag that bakes
in a fixed prefix pair for this recipe: two machines developing the same repository need two *different* pairs on
purpose, and a flag that assumed one pair would assume wrong the moment a second machine ran it. (Two machines
already do this in practice, with prefixes chosen for exactly this reason — ask rather than reuse a pair you have
seen elsewhere.)

## Running it

1. Ask which recipe applies, and the values it needs: the home directory, the project's repository path (recipe 2's
   is the root found above), and the two prefixes. Everything else defaults.
2. Run the command with `--yes`, from the repository root, spelling no absolute path but the ones just given.
3. Read the numbered output. Each line is one step of ten; a run that changes nothing still ends on `10. doctor ok`.
   If it stops instead, the refusal names exactly what is in the way (a home elsewhere, a missing prefix, a database
   behind) — quote it back and stop. Do not guess a fix or retry with different flags on your own.
4. Report the home's path and that `spud doctor` reports `problems    none` (or the note it left, such as a schedule
   step skipped by `--no-schedule`).

## Already initialized

Running this again on a home that already exists loses nothing: every step is idempotent by content — a file
already there is kept, a project already registered is reported and left alone, nothing is rewritten — and the run
still ends by reporting the no-op and naming `spud doctor`. If someone only wants to know whether Spud is already set
up here, running `python3.14 -I -S bin/spud doctor` directly from the repository root gives the same answer without
repeating any step above.
