# Setting up Spud on a project

These instructions are for a Claude Code session that a person opened in **their own project** and asked to "Setup
Spud on this project". The session's working directory is that project, not this repository. Follow the steps in
order and run each command as written. Ask the person wherever a step says to, and stop and quote the output if a
command fails; do not guess a fix.

Setup writes nothing tracked in the person's repository. Spud lives in a separate directory, its *home* (default
`~/SpudHome`). The project gets an untracked `.claude/settings.local.json`, which `spud` excludes through the
repository's own `.git/info/exclude`.

## 1. Check the preconditions

```bash
uname -s; command -v claude git python3.14; python3.14 --version
```

- **Python 3.14**, on `PATH` under exactly the name `python3.14`. On macOS, if it is missing: `brew install python@3.14`.
  The CLI uses only the standard library and needs no packages.
- **git** and **Claude Code** (this session already proves Claude Code is installed).
- **macOS** (`Darwin`) is the supported platform.
- **Linux, including WSL2**: nothing in the code is macOS-only except the two LaunchAgents, but Linux is untested.
  `spud init` skips the LaunchAgents by itself when the platform is not darwin. If `python3.14` is missing on
  Ubuntu, the person can install it from the deadsnakes PPA (`sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt
  install python3.14`). Ask before running `sudo`.
- **Native Windows is not supported.** The CLI imports `fcntl`, which Windows does not have, and the hooks are POSIX
  shell lines. Tell the person to install WSL2 (Ubuntu), clone the project and run Claude Code inside WSL, and then
  follow these steps there. Add `--no-schedule --no-vault` to the init command in step 5.

On any platform other than macOS, tell the person what they lose:
- **No daily backup.** There is no `local.spud.backup`. Run `python3.14 -I -S ~/spud/bin/spud --as spud backup` by
  hand, or from cron.
- **No render watcher.** There is no `local.spud.render`, so the Obsidian notes go stale. `spud --as spud render`
  brings them up to date.
- **On WSL2, no Obsidian vault install**, because step 5 passes `--no-vault`. Obsidian runs on the Windows side, not
  inside WSL.

The ledger, the hooks and `/spud` should work on Linux the same way they do on macOS, but this has not been tested.

## 2. Get the tool

The tool goes to `~/spud`. Its path is written into every hook, so it must stay there.

```bash
git -C ~/spud remote get-url origin 2>/dev/null || git clone https://github.com/0snug0/Spud ~/spud
```

If that printed a URL, `~/spud` was already there. If the URL is `github.com/0snug0/Spud`, update it with
`git -C ~/spud pull --ff-only`. If it is any other URL, stop and ask the person where the tool should go, and use their
path wherever these steps say `~/spud`. If `~/spud` exists but is not a git checkout, the clone fails; stop and ask
the same question.

## 3. Resolve the project

Run this from the session's working directory. It works from a linked worktree too, because it finds the main
checkout:

```bash
dirname "$(git rev-parse --path-format=absolute --git-common-dir)"
git rev-parse --verify --quiet --abbrev-ref origin/HEAD
git config user.name
```

Write down the values. Shell variables do not carry over between commands, so later steps spell them out literally.

- **root**: the first line, the main checkout's absolute path. If `git` says this is not a repository, stop: Spud
  needs one.
- **name**: the root's directory name.
- **key**: the name in lower case, with every run of other characters replaced by `-`. It must match
  `[a-z][a-z0-9-]{0,31}` and must not be `home`.
- **default branch**: the second line without its `origin/`. If the line is empty, use `main`, or the branch the
  person says is the default.

## 4. Ask the person

Propose values and ask the person to confirm or change each one. Never set a value they have not seen. A prefix
appears in every ticket name and note link from then on and cannot be changed once a ticket uses it.

- **Ticket prefix**: the project name's first three letters or digits in upper case, starting with a letter, e.g.
  `Acme` gives `ACM` (tickets `ACM-001`).
- **Team prefix**: the first four, e.g. `ACME` (teams `ACME-001`). For a shorter name, add `S` to the ticket prefix.
  The two prefixes must differ.
- **Owner name**: the person the home is for. Offer `git config user.name`.
- **Landing**: `merge` (the default; Spud merges the verified branch into the default branch) or `pr` (Spud opens a
  pull request).
- **Home**: `~/SpudHome` unless they want another directory. The home must be empty or absent, and must not be inside
  any git work tree.

## 5. Build the home

First check whether this machine already has Spud:

```bash
cat ~/.config/spud/home 2>/dev/null; echo "SPUD_HOME=${SPUD_HOME-}"
```

**If neither names a home** (the usual case), do a dry run, show the steps to the person, and then run init:

```bash
python3.14 -I -S ~/spud/bin/spud init --home <home> --project-root <root> --project-key <key> \
  --default-branch <branch> --ticket-prefix <TKT> --team-prefix <TEAM> --owner-name '<Owner Name>' \
  --landing <merge|pr> --yes --dry-run
```

Then run the same command without `--dry-run`. On WSL2, add `--no-schedule --no-vault`. On other Linux systems, init
skips the LaunchAgents by itself. Init prints ten numbered steps and ends with `10. doctor ok`. If it stops, its
message names the problem; quote it to the person and stop.

**If either one names a home**, this machine already has Spud, and init cannot register a second project into it.
Init keeps the prefixes of the existing config and refuses different ones. Ask the person which they want:
- **Add this project to the existing home** (usually right). Run `spud project list` first and choose prefixes no
  listed project uses. Run both commands from the home directory. Once the project is registered, a `claim` project
  refuses Spud's commands from an unclaimed session whose working directory is inside it, and the home directory is
  never inside a project:
  ```bash
  (cd "$(cat ~/.config/spud/home)" && python3.14 -I -S ~/spud/bin/spud --as spud project add <root> --key <key> --ticket-prefix <TKT> --team-prefix <TEAM> --landing <merge|pr> --default-branch <branch>)
  (cd "$(cat ~/.config/spud/home)" && python3.14 -I -S ~/spud/bin/spud --as spud project install <key>)
  ```
- **Build a separate new home and point this machine at it.** Run the init command above with a new, empty `<home>`
  and `--repoint`, but only after the person says yes. The old home is left untouched, and this machine stops using it.

If `SPUD_HOME` is set and names a directory other than `<home>`, init refuses. Ask the person to unset it, or use that
directory as the home.

**Already initialized.** Running init again on a home that already exists loses nothing. Every step is idempotent by
content: a file already there is kept, a project already registered is reported and left alone, and nothing is
rewritten. The run still ends by reporting the no-op and naming `spud doctor`. To find out whether Spud is already set
up without repeating any step, run `spud doctor` (step 6).

## 6. Verify

```bash
python3.14 -I -S ~/spud/bin/spud doctor
python3.14 -I -S ~/spud/bin/spud project list
git -C <root> status --porcelain
```

- `doctor` must end with `problems    none`. A `notes` line about LaunchAgents skipped by `--no-schedule` or off macOS is
  expected. Anything listed under `problems` gets quoted to the person.
- `project list` must show the project as installed. Init installs the project in its own step 7, so no separate
  `project install` is needed after init. The `project add` route above runs `project install` itself.
- `git status --porcelain` must not list anything that setup wrote.

## 7. Tell the person

Tell the person what to do next:

1. **Open a new Claude Code session in the project and type `/spud`.** The session that ran this setup started before
   the hooks and skills existed, so it cannot load them. With the default `--sessions claim`, a session in the project
   stays a plain session until `/spud` makes it Spud.
2. Optionally, open the home as a vault in Obsidian and choose *Trust author and enable plugins*. A session started in
   the home itself is always Spud.

Give them this list of what setup created and where:

- **The home** (`<home>`, `~/SpudHome` by default): `spud.config.json`, the ledger database under `.spud/`, `CLAUDE.md` (Spud's laws), the
  rendered notes under `ledger/` and `reports/`, and the home's own `.claude/settings.json`.
- **`~/.config/spud/home`**: one line naming the home.
- **User scope (`~/.claude/`)**: `agents/spudagent.md` and its effort variants `spudagent-<effort>.md`, and the
  skills `skills/spud/`, `skills/spud-cleanup/` and `skills/spud-autopilot/`.
- **In the project**: an untracked `.claude/settings.local.json` holding the ledger hooks, and its line in
  `.git/info/exclude`. Nothing tracked.
- **On macOS without `--no-schedule`**: `~/Library/LaunchAgents/local.spud.backup.plist` and `local.spud.render.plist`.

To update Spud later, run
`git -C ~/spud pull --ff-only && (cd "$(cat ~/.config/spud/home)" && python3.14 -I -S ~/spud/bin/spud --as spud project sync --all)`.
