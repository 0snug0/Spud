"""cli/helptexts: The long help texts the parser shows.  Moved from bin/spud_ledger.py (SPD-065)."""

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

deliverable globs (--deliverable): repository-relative, no leading slash and no `..`;
       `*` and `?` match inside one path segment, `**` crosses segments, a trailing `/`
       means everything under that directory, `<key>:` in front names a project's
       checkout by its key and `home:` Spud's home (SPD-097).  Every other character is
       literal, brackets included: write a Next.js segment plainly,
       admin/src/app/accounts/[email]/** (SPD-086).

ticket worktrees (SPD-098): a bare glob, or one naming the ticket's own project, binds
       the ticket to the linked worktree of that project `member new` runs in, and the
       edit and Bash hooks then hold its members to that worktree.  `member new` and
       `member edit --deliverable` refuse (exit 5) from the main checkout (enter a
       worktree first: EnterWorktree name: <ticket key>-<slug>), from outside the
       project, from another worktree while the bound one exists, and for a glob naming
       another project, whose work is a ticket there.  A binding whose worktree is gone
       is rebound by Spud's next plan from a worktree; a member never rebinds.

text values: an option value of @path reads the file, @- reads stdin.
exit codes: 0 ok, 1 error, 2 usage, 3 ownership refused, 4 limit refused,
            5 transition refused, 6 render conflict (hand-edited file).
"""

RESUM_DESCRIPTION = """\
Re-sum members' stored transcript sums from their transcripts, once per API request (SPD-023), with the
per-model breakdown a list-price cost is computed from (SPD-013).

The harness writes an API response as one transcript entry per content block, each repeating the
request's message.id, requestId and usage.  A sum stored before SPD-023 added every entry, so its
tokens run four to five times too high.  A sum made since counts each request once, by its last
entry, and carries "counting": "request"; one stored before SPD-013 keeps no per-model breakdown, so
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
Two macOS LaunchAgents (SPD-012, SPD-097).  local.spud.backup runs `spud --as spud backup --daily` at load and daily at
the --at time; launchd fires a run missed during sleep at wake, and backup --daily writes one copy a day however often
it runs.  local.spud.render runs `spud --as spud render --watch` at load and again whenever it exits (KeepAlive), so
the vault follows the database within seconds; its log is <home>/.spud/logs/render.log, started afresh at each start.
The plists are $SPUD_LAUNCH_AGENTS_DIR/<label>.plist (default ~/Library/LaunchAgents), launchctl is $SPUD_LAUNCHCTL
(default /bin/launchctl), and the backup's output goes to ~/Library/Logs/spud-backup.log.  Every verb is Spud's
(--as spud) and handles both agents.
"""

HOME_MOVE_DESCRIPTION = """\
Move Spud's home to a plain directory (SPD-097, design section 5).  Refused while any member is planned or active, when
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
