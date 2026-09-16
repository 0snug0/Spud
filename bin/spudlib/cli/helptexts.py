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
       means everything under that directory, and `<key>:` in front names another
       project's checkout.  Every other character is literal, brackets included: write
       a Next.js segment plainly, admin/src/app/accounts/[email]/** (SPD-086).

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
The macOS LaunchAgent local.spud.backup runs `spud --as spud backup --daily` at load and daily at the
--at time; launchd fires a run missed during sleep at wake, and backup --daily writes one copy a day
however often it runs.  The plist is $SPUD_LAUNCH_AGENTS_DIR/local.spud.backup.plist (default
~/Library/LaunchAgents), launchctl is $SPUD_LAUNCHCTL (default /bin/launchctl), and the run's output
goes to ~/Library/Logs/spud-backup.log.  Every verb is Spud's (--as spud).
"""
