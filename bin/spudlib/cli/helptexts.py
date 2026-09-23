"""cli/helptexts: The long help texts the parser shows."""

# ----------------------------------------------------------------------------
# Argument parsing and main
# ----------------------------------------------------------------------------

EPILOG = """\
actors (--as):
  spud               Spud himself: tickets, report entries, the parent of every lead.
                     Inside a subagent the PreToolUse(Bash) hook refuses this form.
  SPUD-nnn/<Name>    a team member (also SPUD-nnn/<lineage>, e.g. SPUD-007/01.01).
                     Inside a subagent the hook accepts it only for the caller's own member.
  <agent_id>         the 17-hex-character id the harness gives a subagent, bound to its
                     member by the hooks: PostToolUse(Agent) right after a background
                     spawn, SubagentStop for a foreground one.

hooks: `spud hook <event>` is the harness's entry point (payload on stdin, answer on
       stdout); `spud settings sync` installs the seven events into .claude/settings.json.
       Enforcing hooks fail closed (exit 2), recording hooks fail open (spool, then
       hook.error events).  Inspect the database with `spud sql --readonly '<statement>'`.
       A session loads them from the directory it was launched in (CLAUDE_PROJECT_DIR),
       so one launched outside the home and outside every registered project's checkout
       runs none of them and nothing it does is recorded or guarded:
       `spud session show` and `spud doctor` say so, and name the fix -- as a note, since
       what is wrong there is where the session was launched.  The home's own
       .claude/settings.json is this home's own installation, and the whole of what makes
       a session launched in the home Spud, so doctor's `settings` line reports it absent,
       unreadable or an event short as a problem: `spud --as spud settings sync`.

deliverable globs (--deliverable): repository-relative, no leading slash and no `..`;
       `*` and `?` match inside one path segment, `**` crosses segments, a trailing `/`
       means everything under that directory, `<key>:` in front names a project's
       checkout by its key and `home:` Spud's home.  Every other character is
       literal, brackets included: write a Next.js segment plainly,
       admin/src/app/accounts/[email]/**.

ticket worktrees: a bare glob, or one naming the ticket's own project, binds
       the ticket to the linked worktree of that project `member new` runs in, and the
       edit and Bash hooks then hold its members to that worktree.  `member new` and
       `member edit --deliverable` refuse (exit 5) from the main checkout (enter a
       worktree first: EnterWorktree name: <ticket key>-<slug>), from outside the
       project, from another worktree while the bound one exists, and for a glob naming
       another project, whose work is a ticket there.  A binding whose worktree is gone
       is rebound by Spud's next plan from a worktree; a member never rebinds.

landing pull requests: under a project whose landing is `pr`, Spud records
       the pull request with `spud --as spud pr record` right after `gh pr create`.
       `spud pr reconcile`, which the full board runs itself, reads each recorded,
       still-open one once with `gh pr view` and stores what it said; `spud board` and
       every `board --brief` then show a merged one with the done move, the Outcome and
       the worktree and local-branch cleanup it owes, and a closed-unmerged one plainly.
       Nothing here merges a pull request, runs git or moves a ticket, and no hook and
       no `--brief` ever reads the network.  SPUD_GH names the program (default `gh`);
       SPUD_GH=off turns every read off.

text values: an option value of @path reads the file, @- reads stdin.
exit codes: 0 ok, 1 error, 2 usage, 3 ownership refused, 4 limit refused,
            5 transition refused, 6 render conflict (hand-edited file).
"""

PR_DESCRIPTION = """\
Landing pull requests.  Under `landing: pr` a ticket's done move used to depend on the session that
opened the pull request still being alive when it merged: one ticket showed as `active` for 44 minutes after its
work had landed, because its session had stopped and nothing in the ledger knew a pull request existed.

  record     Spud's, right after `gh pr create`: the URL, the head branch (also the local branch the cleanup
             will owe) and the worktree the cleanup will owe.  Opening a pull request is part of landing, and a
             member neither commits nor pushes, so a member never has one to record.
  reconcile  one `gh pr view` per recorded, still-open pull request, in its ticket's project checkout, storing
             the state and the time it was read.  Any actor, or none: the writes name the actor `reconcile`,
             because a pull request's state is nobody's judgment, and that is what lets `spud board` run it.
             A read that fails -- no gh, no auth, no network, a rate limit -- is stored as a failed check, does
             not stop the run, and is listed by `spud doctor`.  A settled pull request is never read again.
  list       what is recorded, read-only.

The rendered ticket note carries it as well: the properties `pr` (the number the URL gave) and
`pr_state` right after the status, which the Obsidian views filter on, and a generated `## Landing` section of
one sentence per recorded pull request -- the number as a link on its URL, the state in these same words, and
what a merge leaves owed.  It renders from the table alone, never says when the row was last read, and so a
read that found nothing new rewrites no note.

Four things none of this does: it never merges a pull request; it never acts on one the ledger did not record;
it runs no git, so the worktree and local-branch cleanup is named as owed and performed by Spud; and it never
moves a ticket to done, because that is a judgment -- the job here is to make sure the judgment gets asked for.

No hook and no `spud board --brief` ever reads the network: both read stored state alone, and the full board is
the only command that reconciles on its own, skipping a check younger than its staleness window and capping the
whole run.  SPUD_GH names the program (default `gh`), and the literal SPUD_GH=off turns every read off.
"""

RESUM_DESCRIPTION = """\
Re-sum members' stored transcript sums from their transcripts, once per API request, with the
per-model breakdown a list-price cost is computed from.

The harness writes an API response as one transcript entry per content block, each repeating the
request's message.id, requestId and usage.  A sum stored by an older spud added every entry, so its
tokens run four to five times too high.  A sum made since counts each request once, by its last
entry, and carries "counting": "request"; one stored before costs were priced keeps no per-model breakdown, so
no cost can be priced from it.  Both are re-summed.  A sum with its breakdown is left as it is, so a
second run changes nothing.

The transcript read is the recorded transcript_path when that is a file; else the one file with the
same session directory and file name (<session>/subagents/agent-<id>.jsonl) under a project directory
beside the recorded one, where the harness moves a worktree session's transcripts when the session
leaves the worktree.  The new sum is merged through the run totals, so a foreground completion keeps
its whole-run duration and tool count; transcript_path becomes the file read; and each member
re-summed gets a member.edited event with its old and new figures and its old usage_json whole.
An imported sum (no transcript behind it) and a member without a transcript sum are listed and left.
"""

RESUM_EPILOG = """\
exit codes: 0 every member listed is re-summed, already counted with its breakdown, imported, or holds no
            transcript sum; 1 a sum without its breakdown was left as it is, because its transcript
            was not found, is ambiguous, cannot be read or holds no usage (the other members are
            still written, and --dry-run exits the same); 2 usage; 3 an actor other than Spud.
"""

BACKUP_DESCRIPTION = """\
Back up the ledger: PRAGMA wal_checkpoint(TRUNCATE), then VACUUM INTO .spud/backups/ledger-<stamp>.db.

--daily is the LaunchAgent's run (spud schedule).  It writes ledger-<stamp>-daily.db, unless a daily copy
for today's local date is already there: then it writes nothing and exits 0.  It runs PRAGMA quick_check
on the new copy; anything but ok removes that copy, prunes nothing and exits 1.  Then it keeps the newest
--keep daily copies (default 14): of the regular files in .spud/backups/ whose whole name is
ledger-<YYYYMMDD>T<HHMMSS>-daily.db, it unlinks the older ones, never the copy it has just written.
Nothing else is ever deleted: manual and pre-migration copies, look-alike names, directories, the live
database and its -wal and -shm all stay.  `spud doctor` reports the copies.

--json with --daily: path, written, pruned (the names unlinked) and kept (the daily copies left).
"""

BACKUP_EPILOG = """\
exit codes: 0 a copy written, or today's daily copy already there; 1 the write or its quick_check failed
            (nothing pruned); 2 usage (--keep below 1, or --keep without --daily).
"""

SCHEDULE_DESCRIPTION = """\
Two macOS LaunchAgents.  local.spud.backup runs `spud --as spud backup --daily` at load and daily at
the --at time; launchd fires a run missed during sleep at wake, and backup --daily writes one copy a day however often
it runs.  local.spud.render runs `spud --as spud render --watch` at load and again whenever it exits (KeepAlive), so
the vault follows the database within seconds; its log is <home>/.spud/logs/render.log, started afresh at each start with the previous run's kept in render.log.1.
The plists are $SPUD_LAUNCH_AGENTS_DIR/<label>.plist (default ~/Library/LaunchAgents), launchctl is $SPUD_LAUNCHCTL
(default /bin/launchctl), and the backup's output goes to ~/Library/Logs/spud-backup.log.  Every verb is Spud's
(--as spud) and handles both agents.
"""

HOME_MOVE_DESCRIPTION = """\
Move Spud's home to a plain directory.  Refused while any member is planned or active, when
--to exists and is not empty, lies inside a git work tree or inside the current home, when a rendered file is hand-edited
(spud doctor lists them with the commands that settle each), when an earlier move left .spud-moved behind, and when the
running bin/spud sits in a linked worktree.  Then, each step reported: a checked backup; the database copied with
SQLite's online backup, integrity-checked and compared row by row; ledger/, reports/, docs/, .obsidian/, the config,
CLAUDE.md, the two .claude settings files and the backups copied, and the copied vault checked against the copied
database (zero files to render); ~/.config/spud/home re-pointed; project spud set to sessions claim, the new home's
.claude/settings.json synced, the ledger's entries stripped from the tool's tracked .claude/settings.json (left
uncommitted for the removal commit), project spud installed and every installed project re-synced; both LaunchAgents
reinstalled; a render and spud doctor in the new home; the old .spud renamed .spud-moved.  Ends by printing what is
left by hand and how to roll back until the removal commit.  --dry-run checks the preconditions and prints the steps.
"""

HOME_SYNC_DESCRIPTION = """\
Write every file the tool owns in this home again from <tool>/share/, so a
home's own text is generated rather than hand-kept in two places: CLAUDE.md, ledger/Home.md, ledger/Spud.md,
ledger/_templates/, the .base views, every skill under share/skills/ (which a home reads from .claude/skills/), and the
Obsidian settings and pinned plugins through the same install `spud vault install` runs.  The ledger's rendered notes,
the reports, docs/, the database and spud.config.json are never touched: they are the home's, not the tool's.

A file that already holds the shipped text is left alone.  One that holds something else is replaced, and a copy of
what it held is kept first, under a home-sync folder of the home's backups directory named by the time of the run, at
the file's own path inside it; every copy is named in the output.  Nothing is written until everything can be: a
{{mark}} with no value refuses the whole sync naming the mark, as `spud init` refuses, and so does a lock this tool
will not install from.  A refused download is a line in the output, never a failed command.

--check prints what would change, file by file -- what would be written, what already holds the shipped text, and where
the copy of each replaced file would be kept -- and writes nothing, downloads nothing and keeps no copy.  Run it first.
`spud doctor` names each tool-owned file this home does not have, or has and has changed, as a note.
"""

VAULT_DESCRIPTION = """\
The home's Obsidian vault, which the tool ships and the tool captures.
`vault install` writes <home>/.obsidian/ from <tool>/share/obsidian/ and downloads every plugin and theme
<tool>/share/obsidian.lock.json pins, checking each file's SHA-256 before it is renamed into place: a release replaced
upstream refuses that one plugin by name and the rest still install, and a refused download is a line in the output,
never a failed command.  A tool-owned file the home has changed is replaced, with a copy kept under a vault-install
folder of the home's backups directory and named in the output; --force rewrites what matched anyway.  A file the home
has that the tool does not ship is never touched.  The shipped .base views are the tool's, but install leaves them
alone -- `spud home sync` refreshes them, keeping a copy of what it replaces, while a .base file of your own survives.
`spud init` runs this as its step 4b, and `spud init --no-vault` leaves the vault out.

`vault capture --into <worktree>` is the other direction, and a member's: it writes share/obsidian/,
share/obsidian.lock.json and share/ledger/*.base in a linked worktree of this repository from what this home's vault
holds now.  Only what is turned on is captured -- the plugins community-plugins.json enables, the theme and the snippets
appearance.json names -- and never a note, never workspace.json.  Each plugin's and theme's GitHub repository comes from
Obsidian's own community lists, and a file is pinned only when the download is byte for byte what is installed, so a
plugin changed by hand or installed from outside the store is refused by name.  The main checkout, another repository,
and any path outside the member's deliverables are all refused before anything is written.

`spud doctor` names, as notes, each shipped settings file, .base file, or plugin and theme version the home has and has
changed since the last capture.  SPUD_VAULT_DOWNLOADS=off refuses every download (the suite sets it on every home).
"""
