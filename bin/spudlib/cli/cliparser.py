"""cli/cliparser: build_parser, normalize_argv, text_arg, date_arg.  Moved from bin/spud_ledger.py (SPD-065)."""

import sys
from datetime import date
from pathlib import Path

from . import helptexts
from ..commands import (
    admincmds,
    doctor,
    ghread,
    homeinit,
    homemove,
    membercmds,
    prcmds,
    proposalcmds,
    publish,
    renderwatch,
    resumcmd,
    schedule,
    settings_sync,
    ticketcmds,
    vaultcapture,
    vaultinstall,
    views,
)
from ..core import kernel, launchagents, lazy
from ..hooks import dispatch, hookio
from ..projects import install, registry, sessions
from ..state import backup, schema


def text_arg(value):
    """@path reads a file, @- reads stdin; trailing newlines are dropped."""
    if value == "@-":
        return sys.stdin.read().rstrip("\n")
    if value.startswith("@") and len(value) > 1:
        path = Path(value[1:]).expanduser()
        if not path.is_file():
            raise lazy.argparse.ArgumentTypeError("no such file: %s" % path)
        return path.read_text(encoding="utf-8").rstrip("\n")
    return value


def date_arg(value):
    """--until (SPD-096): a plain YYYY-MM-DD, the spelling every date in the ledger's frontmatter has.  A past date is
    accepted: the ticket is due back at once."""
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        parsed = None
    if parsed is None or value != parsed.isoformat():
        raise lazy.argparse.ArgumentTypeError("--until is YYYY-MM-DD, not %r" % value)
    return value


def build_parser():
    parser = lazy.argparse.ArgumentParser(prog="spud", description="The one program that writes Spud's ledger.", epilog=helptexts.EPILOG, formatter_class=lazy.argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--as", dest="actor", metavar="ACTOR", help="who is writing: spud | SPUD-nnn/<Name> | <agent_id>")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--version", action="version", version="spud %s (schema %d)" % (kernel.VERSION, schema.SCHEMA_VERSION))
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # SPW-001: init builds a home from nothing -- the config, the database, the first project, the vault scaffolding and
    # the pointer -- so it is the one command that may run with no home to resolve (bin/spud_ledger.main's own branch),
    # and every value it needs is a flag, prompted for only when stdin is a tty.
    p = sub.add_parser("init", help="build a home from nothing: the config, the database (created, or migrated if behind), the first project, the vault scaffolding, the ~/.config/spud/home pointer (Spud's)")
    p.add_argument("--home", help="the home to build: a directory that does not exist or is empty, outside every git work tree (default: SPUD_HOME, else ~/.config/spud/home, else ~/SpudHome offered at the prompt)")
    p.add_argument("--name", help="identity.name in the config written for a home that has none (default Spud)")
    p.add_argument("--pronouns", help="identity.pronouns, subject/object/possessive (default he/him/his)")
    p.add_argument("--project-root", help="the first project's repository: its main checkout, registered as project 1, whose name and prefixes the config carries")
    p.add_argument("--no-project", action="store_true", help="register no project: the home holds an empty registry, and no ticket until `project add`")
    p.add_argument("--project-key", help="lower-case key, [a-z][a-z0-9-]{0,31}; home is reserved (default: the root's directory name)")
    p.add_argument("--project-name", help="display name (default: the root's directory name)")
    p.add_argument("--ticket-prefix", help="upper-case ticket prefix (SPD gives SPD-001): no default, and permanent once a ticket carries it")
    p.add_argument("--team-prefix", help="upper-case team prefix (SPUD gives SPUD-001): no default")
    p.add_argument("--landing", choices=("merge", "pr"), default="merge", help="how the first project's verified branch lands (default merge)")
    p.add_argument("--sessions", choices=("claim", "always"), default="claim", help="claim (default): a session in the project is Spud only after /spud claims it")
    p.add_argument("--default-branch", help="the first project's default branch (default: origin/HEAD's branch, else main)")
    p.add_argument("--no-schedule", action="store_true",
                   help="install neither LaunchAgent (%s, the daily backup, and %s, the render watcher): `spud schedule install` does it later, and doctor reports a watcher never installed as a note, not a problem"
                        % (launchagents.SCHEDULE_LABEL, launchagents.RENDER_LABEL))
    p.add_argument("--no-vault", action="store_true",
                   help="install no Obsidian vault: step 4 writes the scaffolding and stops, and `spud --as spud vault install` sets one up later (SPD-156)")
    p.add_argument("--repoint", action="store_true", help="~/.config/spud/home names another home: point this machine here instead (that home is left untouched)")
    p.add_argument("--yes", action="store_true", help="never prompt: every value not given takes its default, and one with no default is refused")
    p.add_argument("--dry-run", action="store_true", help="check the preconditions and print the steps; write nothing")
    p.add_argument("--next", type=text_arg, help="Spud's Next line, last in the report entry init writes")
    p.set_defaults(func=homeinit.cmd_init)
    p = sub.add_parser("migrate", help="apply pending migrations (backup first)")
    p.set_defaults(func=admincmds.cmd_migrate)
    p = sub.add_parser("backup", help="wal_checkpoint(TRUNCATE) then VACUUM INTO .spud/backups/; --daily keeps one checked copy a day, the newest 14 (Spud's)",
                       description=helptexts.BACKUP_DESCRIPTION, epilog=helptexts.BACKUP_EPILOG, formatter_class=lazy.argparse.RawDescriptionHelpFormatter)
    p.add_argument("--daily", action="store_true", help="write ledger-<stamp>-daily.db unless today's is there, quick_check it, then keep the newest --keep daily copies")
    p.add_argument("--keep", type=int, metavar="N", help="daily copies --daily keeps (default %d, at least 1)" % backup.DAILY_KEEP)
    p.set_defaults(func=schedule.cmd_backup)
    p = sub.add_parser("schedule", help="the macOS LaunchAgents %s (the daily backup) and %s (the render watcher) (Spud's)" % (launchagents.SCHEDULE_LABEL, launchagents.RENDER_LABEL),
                       description=helptexts.SCHEDULE_DESCRIPTION, formatter_class=lazy.argparse.RawDescriptionHelpFormatter)
    ps = p.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ps.required = True
    q = ps.add_parser("show", help="print the plist install would write, where it goes, whether it is installed and matches, whether the job is loaded; writes nothing")
    q.add_argument("--at", type=schedule.at_arg, default=schedule.SCHEDULE_AT, metavar="HH:MM", help="the daily time of the plist to show and compare (default %s)" % schedule.SCHEDULE_AT)
    q.set_defaults(func=schedule.cmd_schedule_show)
    q = ps.add_parser("install", help="write the plist atomically, then launchctl bootout and bootstrap it in gui/<uid>; a second install replaces and reloads")
    q.add_argument("--at", type=schedule.at_arg, default=schedule.SCHEDULE_AT, metavar="HH:MM", help="the daily run, local time (default %s)" % schedule.SCHEDULE_AT)
    q.set_defaults(func=schedule.cmd_schedule_install)
    q = ps.add_parser("uninstall", help="launchctl bootout, then remove the plist")
    q.set_defaults(func=schedule.cmd_schedule_uninstall)
    p = sub.add_parser("doctor", help="interpreter, SQLite, SPUD_HOME, database pragmas, config sanity")
    p.set_defaults(func=doctor.cmd_doctor)

    p = sub.add_parser("config", help="spud.config.json mirrors")
    ps = p.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ps.required = True
    q = ps.add_parser("sync", help="mirror naming.pool into name_pool and the home project's prefixes")
    q.set_defaults(func=admincmds.cmd_config_sync)

    p = sub.add_parser("settings", help=".claude/settings.json generation")
    ps = p.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ps.required = True
    q = ps.add_parser("sync", help="write the two env caps, the seven ledger hooks, the CLI allow rules and the Agent deny rules into a settings file, keeping every other key")
    q.add_argument("--path", help="settings file (default <SPUD_HOME>/.claude/settings.json)")
    q.add_argument("--dry-run", action="store_true", help="print the result, write nothing")
    q.set_defaults(func=settings_sync.cmd_settings_sync)

    p = sub.add_parser("hook", help="run one harness hook event: the payload on stdin, the answer on stdout (installed by settings sync)")
    p.add_argument("event", choices=hookio.HOOK_EVENTS)
    p.add_argument("--project", help="the project whose local settings carry this line (project install): a failure with no agent_id fails open")
    p.set_defaults(func=dispatch.cmd_hook)

    p = sub.add_parser("project", help="repositories Spud works in besides his home (SPD-014)")
    ps = p.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ps.required = True
    q = ps.add_parser("add", help="register a repository's main checkout as a project; installs nothing (Spud's)")
    q.add_argument("path", help="the repository's main checkout")
    q.add_argument("--key", required=True, help="lower-case key, [a-z][a-z0-9-]{0,31}; home is reserved")
    q.add_argument("--ticket-prefix", required=True, help="upper-case ticket prefix (BAD gives BAD-001)")
    q.add_argument("--team-prefix", required=True, help="upper-case team prefix (BADS gives BADS-001)")
    q.add_argument("--landing", required=True, choices=("merge", "pr"), help="how a verified branch lands: merge into the default branch, or a pull request")
    q.add_argument("--name", help="display name (default: the root's directory name)")
    q.add_argument("--sessions", choices=("claim", "always"), default="claim", help="claim (default): a session there is Spud only after /spud claims it; always: every session is")
    q.add_argument("--default-branch", help="default: origin/HEAD's branch, else main")
    q.add_argument("--next", type=text_arg, help="Spud's Next line, last in the report entry this writes")
    q.set_defaults(func=registry.cmd_project_add)
    q = ps.add_parser("list", help="every project, with its install state computed now")
    q.set_defaults(func=registry.cmd_project_list)
    q = ps.add_parser("show", help="one project")
    q.add_argument("key")
    q.set_defaults(func=registry.cmd_project_show)
    q = ps.add_parser("edit", help="change a project's name, landing, sessions, default branch, root, or its prefixes before its first ticket (Spud's)")
    q.add_argument("key")
    q.add_argument("--name")
    q.add_argument("--landing", choices=("merge", "pr"))
    q.add_argument("--sessions", choices=("claim", "always"))
    q.add_argument("--default-branch")
    q.add_argument("--root", help="the repository moved on disk: validated like add")
    q.add_argument("--ticket-prefix")
    q.add_argument("--team-prefix")
    q.set_defaults(func=registry.cmd_project_edit)
    q = ps.add_parser("install", help="write the ledger hooks into the project's untracked .claude/settings.local.json, spudagent and the /spud skill at user scope (Spud's)")
    q.add_argument("key")
    q.add_argument("--next", type=text_arg, help="Spud's Next line, last in the report entry this writes")
    q.set_defaults(func=install.cmd_project_install)
    q = ps.add_parser("uninstall", help="take back what install wrote (Spud's)")
    q.add_argument("key")
    q.add_argument("--next", type=text_arg, help="Spud's Next line, last in the report entry this writes")
    q.set_defaults(func=install.cmd_project_uninstall)
    q = ps.add_parser("sync", help="rewrite an installed project's files from the home's current ones, e.g. after spudagent.md changes (Spud's)")
    q.add_argument("key", nargs="?")
    q.add_argument("--all", action="store_true", help="every installed project")
    q.set_defaults(func=install.cmd_project_sync)
    q = ps.add_parser("remove", help="uninstall, then delete a project without tickets or archive one with them; refused with an open ticket (Spud's)")
    q.add_argument("key")
    q.add_argument("--next", type=text_arg, help="Spud's Next line, last in the report entry this writes")
    q.set_defaults(func=install.cmd_project_remove)

    p = sub.add_parser("session", help="this Claude Code session: claim it for Spud in another project, release it, show it (SPD-014)")
    ps = p.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ps.required = True
    q = ps.add_parser("claim", help="make this session Spud in its project (what /spud runs); needs CLAUDE_CODE_SESSION_ID")
    q.add_argument("--project", help="project key (default: the project of the working directory)")
    q.set_defaults(func=sessions.cmd_session_claim)
    q = ps.add_parser("release", help="end this session's claim")
    q.set_defaults(func=sessions.cmd_session_release)
    q = ps.add_parser("show", help="the home, the working directory's project and checkout, the session and its mode (any actor)")
    q.set_defaults(func=sessions.cmd_session_show)

    p = sub.add_parser("home", help="Spud's home: the directory holding the database, the config and the vault (SPD-097)")
    ps = p.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ps.required = True
    q = ps.add_parser("move", help="move the home to an empty directory outside every git work tree: backup, copy, re-point, re-sync hooks and agents, verify (Spud's)",
                      description=helptexts.HOME_MOVE_DESCRIPTION, formatter_class=lazy.argparse.RawDescriptionHelpFormatter)
    q.add_argument("--to", required=True, help="the new home: a directory that does not exist or is empty, not inside a git work tree")
    q.add_argument("--dry-run", action="store_true", help="check the preconditions and print the steps; move nothing")
    q.add_argument("--next", type=text_arg, help="Spud's Next line, last in the report entry the move writes")
    q.set_defaults(func=homemove.cmd_home_move)

    p = sub.add_parser("vault", help="the home's Obsidian vault: install it from the tool's share/, or capture this one back into a ticket's worktree (SPD-156)",
                       description=helptexts.VAULT_DESCRIPTION, formatter_class=lazy.argparse.RawDescriptionHelpFormatter)
    ps = p.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ps.required = True
    q = ps.add_parser("install", help="write the home's .obsidian/ from share/obsidian/ and download every plugin and theme the lock pins, checking each SHA-256 (Spud's)")
    q.add_argument("--force", action="store_true", help="rewrite a tool-owned file that already holds the shipped text, and download a pinned file the home already has")
    q.set_defaults(func=vaultinstall.cmd_vault_install)
    q = ps.add_parser("capture", help="write share/obsidian/, share/obsidian.lock.json and share/ledger/*.base in a linked worktree from this home's vault (a member's, on a ticket)")
    q.add_argument("--into", required=True, metavar="WORKTREE", help="a linked worktree of this repository; the main checkout and every other repository are refused")
    q.set_defaults(func=vaultcapture.cmd_vault_capture)

    p = sub.add_parser("sql", help="run one read-only statement against the database (any actor; the inspection path)")
    p.add_argument("statement", help="SELECT, WITH, VALUES, EXPLAIN or a read-only PRAGMA")
    p.add_argument("--readonly", action="store_true", help="required: the database is opened mode=ro with query_only on")
    p.set_defaults(func=admincmds.cmd_sql)

    p = sub.add_parser("import", help="import markdown-v0 trees, or accept one hand-edited rendered file with --file (Spud only)")
    p.add_argument("paths", nargs="*", help="roots, ledger/ dirs or reports/ dirs (default: SPUD_HOME)")
    p.add_argument("--file", help="accept a hand edit of this rendered file: title, priority, tags, status through the state machine, Brief/Size/Outcome; a member's Brief, Outcome and status; an appended report entry")
    p.set_defaults(func=admincmds.cmd_import)

    p = sub.add_parser("render", help="regenerate ledger/ and reports/ from the database; a hand-edited file is left alone (exit 6), a note whose frontmatter only changed YAML style (Obsidian's rewrite) is rendered over and kept in the event; --watch keeps doing it (Spud's)")
    p.add_argument("--out", help="render into this directory instead of SPUD_HOME (no hash checks)")
    p.add_argument("--discard", metavar="PATH", help="overwrite this hand-edited file with the current render, keeping the discarded text in the event (Spud only)")
    p.add_argument("--watch", action="store_true", help="run until SIGTERM, rendering whenever the event log moves: the LaunchAgent local.spud.render's run (SPD-097)")
    p.add_argument("--interval", type=float, default=renderwatch.WATCH_INTERVAL, metavar="SECONDS", help="with --watch: seconds between reads of the event log (default %.0f)" % renderwatch.WATCH_INTERVAL)
    p.add_argument("--ticks", type=int, metavar="N", help="with --watch: stop after N reads (tests)")
    p.set_defaults(func=renderwatch.render_entry)

    p = sub.add_parser("ticket", help="tickets (Spud's)")
    ps = p.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ps.required = True
    q = ps.add_parser("new", help="create a ticket in the home project (or --project)")
    q.add_argument("--title", required=True)
    q.add_argument("--priority", choices=kernel.PRIORITIES, default="P2")
    q.add_argument("--status", choices=("queued", "active"), default="queued")
    q.add_argument("--brief", type=text_arg)
    q.add_argument("--sizing", type=text_arg, help="the Size, persona and model decision")
    q.add_argument("--outcome", type=text_arg)
    q.add_argument("--heading", help="a shorter H1 than the title")
    q.add_argument("--tag", action="append", help="extra tag (ticket is always first)")
    q.add_argument("--project", help="project key (default: the project of the working directory, else spud; the home is no project)")
    q.add_argument("--next", type=text_arg, help="Spud's Next line, last in the report entry this writes: SPD-nnn created (<status>, <priority>): <title>")
    q.set_defaults(func=ticketcmds.cmd_ticket_new)
    q = ps.add_parser("move", help="change a ticket's status along the state machine")
    q.add_argument("key")
    q.add_argument("--status", required=True, choices=kernel.TICKET_STATUSES)
    q.add_argument("--reason", type=text_arg, help="why; required for --status parked, where it is stored and shown wherever the ticket is")
    q.add_argument("--until", type=date_arg, metavar="YYYY-MM-DD",
                   help="with --status parked: the date from which `spud board --brief` shows the ticket as due back;"
                        " without it the ticket stays parked until moved")
    q.add_argument("--next", type=text_arg, help="Spud's Next line, last in the report entry this writes: SPD-nnn started|done|queued|parked|declined: <title>")
    q.set_defaults(func=ticketcmds.cmd_ticket_move)
    q = ps.add_parser("edit", help="edit title, heading, priority, brief, sizing, outcome, tags")
    q.add_argument("key")
    q.add_argument("--title")
    q.add_argument("--heading")
    q.add_argument("--priority", choices=kernel.PRIORITIES)
    q.add_argument("--brief", type=text_arg)
    q.add_argument("--sizing", type=text_arg)
    q.add_argument("--outcome", type=text_arg)
    q.add_argument("--tag", action="append")
    q.add_argument("--next", type=text_arg, help="Spud's Next line, last in the report entry a priority change writes: SPD-nnn priority <old> to <new>: <title>; refused when the edit changes no priority")
    q.set_defaults(func=ticketcmds.cmd_ticket_edit)
    q = ps.add_parser("show", help="print a ticket")
    q.add_argument("key")
    q.set_defaults(func=ticketcmds.cmd_ticket_show)

    p = sub.add_parser("member", help="team members")
    ps = p.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ps.required = True
    member_new_help = ("plan a member (the actor is its parent): limits, lineage, name draw; a code ticket binds the linked worktree it runs"
                       " in, and it refuses from the main checkout, outside the project or another worktree (SPD-098)")
    q = ps.add_parser("new", help=member_new_help, description=member_new_help)
    q.add_argument("--ticket", help="ticket key (required for Spud; implied for a member)")
    q.add_argument("--persona", required=True)
    q.add_argument("--model", required=True, choices=kernel.MODELS)
    q.add_argument("--name", help="a pool name instead of a random draw")
    q.add_argument("--tier-reason", type=text_arg, help="required when the model is not the persona's default tier")
    q.add_argument("--agent-type", help="native agent type (required for a contractor)")
    q.add_argument("--brief", type=text_arg, help="the brief, required and non-empty (no brief, no spudagent); @file or @- accepted")
    q.add_argument("--deliverable", action="append", help="a path glob the member may write (repeatable): bare for the ticket's project, in the worktree the ticket"
                                                          " is bound to; home:<glob> for the home; <key>:<glob> naming another project is refused")
    q.set_defaults(func=membercmds.cmd_member_new)
    q = ps.add_parser("start", help="planned -> active (stamping spawned) or blocked -> active after a re-brief; the parent's")
    q.add_argument("ref")
    q.set_defaults(func=membercmds.cmd_member_start)
    q = ps.add_parser("log", help="append a Log line (the member's own)")
    q.add_argument("text", type=text_arg)
    q.set_defaults(func=lambda ctx, a: membercmds.cmd_member_own(ctx, a, "log"))
    q = ps.add_parser("result", help="record the Result (the member's own)")
    q.add_argument("text", type=text_arg)
    q.set_defaults(func=lambda ctx, a: membercmds.cmd_member_own(ctx, a, "result"))
    q = ps.add_parser("block", help="record the Blocked question (the member's own)")
    q.add_argument("text", type=text_arg)
    q.set_defaults(func=lambda ctx, a: membercmds.cmd_member_own(ctx, a, "block"))
    q = ps.add_parser("finish", help="the parent's verdict: status, outcome, summary, finished")
    q.add_argument("ref")
    q.add_argument("--status", required=True, choices=("done", "blocked", "failed"))
    q.add_argument("--outcome", required=True, type=text_arg)
    q.add_argument("--summary", type=text_arg, help="one paragraph for the card")
    q.add_argument("--next", type=text_arg, help="Spud's Next line, last in the report entry a root member's finish writes: SPD-nnn: <Name> (<lineage>, <persona>, <model>) <status>, then its summary; refused for a nested member")
    q.set_defaults(func=membercmds.cmd_member_finish)
    q = ps.add_parser("edit", help="edit brief, deliverables, model, summary (the parent's); new deliverables bind and refuse as member new's do")
    q.add_argument("ref")
    q.add_argument("--brief", type=text_arg)
    q.add_argument("--deliverable", action="append")
    q.add_argument("--summary", type=text_arg)
    q.add_argument("--model", choices=kernel.MODELS)
    q.add_argument("--tier-reason", type=text_arg)
    q.add_argument("--agent-type")
    q.set_defaults(func=membercmds.cmd_member_edit)
    q = ps.add_parser("show", help="print a member")
    q.add_argument("ref")
    q.set_defaults(func=membercmds.cmd_member_show)
    q = ps.add_parser("list", help="list members: what card shows for --ticket, else what fleet shows for every member (read-only)")
    q.add_argument("--ticket", help="a ticket key; without it, every member")
    q.set_defaults(func=views.cmd_member_list)
    q = ps.add_parser("resum", help="re-sum stored transcript sums that added every entry (before SPD-023) or lack the per-model breakdown (before SPD-013), once per API request, from the transcripts (Spud's)",
                      description=helptexts.RESUM_DESCRIPTION, epilog=helptexts.RESUM_EPILOG, formatter_class=lazy.argparse.RawDescriptionHelpFormatter)
    q.add_argument("refs", nargs="*", metavar="ref", help="a member, SPUD-nnn/<Name> or SPUD-nnn/<lineage>; several may be named")
    q.add_argument("--all", action="store_true", help="every member whose usage_json holds a transcript sum")
    q.add_argument("--dry-run", action="store_true", help="print the table and exit as the run would, and write nothing")
    q.set_defaults(func=resumcmd.cmd_member_resum)

    p = sub.add_parser("proposal", help="ticket proposals and their climb")
    ps = p.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ps.required = True
    q = ps.add_parser("file", help="file a proposal (the member's own); its parent holds it")
    q.add_argument("--title", required=True)
    q.add_argument("--why", type=text_arg)
    q.add_argument("--evidence", type=text_arg)
    q.add_argument("--priority", choices=kernel.PRIORITIES, help="suggested priority")
    q.set_defaults(func=proposalcmds.cmd_proposal_file)
    decide_help = ("absorb | decline | escalate (the holder) or create (Spud); a holder that has returned holds nothing, so the"
                   " proposal falls to the nearest ancestor that can still act, and to Spud at the root")
    q = ps.add_parser("decide", help=decide_help, description=decide_help)
    q.add_argument("id", type=int)
    q.add_argument("--decision", required=True, choices=("absorb", "decline", "escalate", "create"))
    q.add_argument("--reason", type=text_arg)
    q.add_argument("--priority", choices=kernel.PRIORITIES, help="priority of the created ticket")
    q.add_argument("--title", help="title of the created ticket (default: the proposal's)")
    q.add_argument("--next", type=text_arg, help="Spud's Next line, last in the report entry his create or decline writes")
    q.set_defaults(func=proposalcmds.cmd_proposal_decide)
    q = ps.add_parser("list", help="list proposals")
    q.add_argument("--ticket")
    q.add_argument("--open", action="store_true")
    q.set_defaults(func=proposalcmds.cmd_proposal_list)

    p = sub.add_parser("handoff", help="handoffs")
    ps = p.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ps.required = True
    q = ps.add_parser("add", help="record a handoff (Spud or a party)")
    q.add_argument("--ticket", required=True)
    q.add_argument("--from", dest="frm", required=True, help="SPUD-nnn/<Name> or spud")
    q.add_argument("--to", required=True, help="SPUD-nnn/<Name> or spud")
    q.add_argument("--what", required=True, type=text_arg)
    q.add_argument("--path")
    q.set_defaults(func=proposalcmds.cmd_handoff_add)

    p = sub.add_parser("report", help="daily reports")
    ps = p.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ps.required = True
    report_add = ("append an entry with the clock time, a title and the Next line, for what no command records, such as a merge or an install;"
                  " ticket new, move and edit, member finish and proposal decide write their own entries and take --next (Spud's)")
    q = ps.add_parser("add", help=report_add, description=report_add)
    q.add_argument("title")
    q.add_argument("--next", required=True, type=text_arg, help="Spud's Next line")
    q.set_defaults(func=proposalcmds.cmd_report_add)

    pr_help = ("landing pull requests: the one a ticket lands through, read back with `gh pr view` and surfaced on the board, so a"
               " merge reaches the ledger without the session that opened it (SPD-077)")
    p = sub.add_parser("pr", help=pr_help, description=helptexts.PR_DESCRIPTION, formatter_class=lazy.argparse.RawDescriptionHelpFormatter)
    ps = p.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ps.required = True
    pr_record = ("record the pull request a ticket lands through, right after `gh pr create` (Spud's): its URL, its head branch"
                 " -- also the local branch the cleanup will owe -- and the worktree the cleanup will owe, which defaults to the"
                 " one the ticket is bound to. Recording the same URL on the same ticket again refreshes the branch and the"
                 " worktree; on another ticket it is refused")
    q = ps.add_parser("record", help=pr_record, description=pr_record)
    q.add_argument("--ticket", required=True, help="the ticket this pull request lands; it must still be open")
    q.add_argument("--url", required=True, help="the pull request's absolute http(s) URL, as `gh pr create` printed it")
    q.add_argument("--branch", required=True, help="the head branch, which is also the local branch the landing owes a `git branch -D`")
    q.add_argument("--worktree", help="the linked worktree the landing owes a `git worktree remove` (default: the one the ticket is bound to)")
    q.set_defaults(func=prcmds.cmd_pr_record)
    pr_reconcile = ("read each recorded, still-open pull request once with `gh pr view` and store what it said (any actor, or none:"
                    " a pull request's state is nobody's judgment, so the writes name the actor `%s` and `spud board` runs this"
                    " itself). A read that fails is stored as a failed check and never stops the run; `spud doctor` lists them."
                    " This never merges anything, never runs git, and never moves a ticket" % kernel.RECONCILE_ACTOR)
    q = ps.add_parser("reconcile", help=pr_reconcile, description=pr_reconcile)
    q.add_argument("--ticket", help="only this ticket's pull requests")
    q.add_argument("--stale", type=float, default=0.0, metavar="SECONDS",
                   help="skip a pull request read more recently than this (default 0: read every open one now; `spud board` uses %d)" % prcmds.STALE)
    q.add_argument("--budget", type=float, default=None, metavar="SECONDS", help="stop once the run has spent this long (default: no cap)")
    q.add_argument("--timeout", type=float, default=ghread.CALL_TIMEOUT, metavar="SECONDS", help="seconds for one `gh pr view` (default %d)" % ghread.CALL_TIMEOUT)
    q.set_defaults(func=prcmds.cmd_pr_reconcile)
    q = ps.add_parser("list", help="every recorded pull request with its state, the read behind it and what a merge still owes (read-only)")
    q.add_argument("--ticket", help="only this ticket's")
    q.add_argument("--project", help="only this project's tickets'")
    q.add_argument("--open", action="store_true", help="only the ones still open")
    q.set_defaults(func=prcmds.cmd_pr_list)

    board_help = ("the board (v_board): every ticket, active first, then queued, parked, done and declined, by priority inside each;"
                  " then each open ticket's bound worktree and its branch, read from git, and its recorded landing pull requests,"
                  " reconciled with one `gh pr view` each when their stored check has gone stale")
    p = sub.add_parser("board", help=board_help, description=board_help)
    p.add_argument("--brief", action="store_true",
                   help="open tickets and live members, one line each, as SessionStart injects it: active tickets with their live"
                        " members and any merged or closed pull request, then parked tickets that are due back, then queued, then"
                        " one count line for the parked; reads stored state only and never runs gh")
    p.add_argument("--no-reconcile", dest="reconcile", action="store_false",
                   help="do not read any pull request: show the stored state as it is (SPUD_GH=off does the same everywhere)")
    p.add_argument("--parked", action="store_true",
                   help="parked tickets only, with why and until (with --brief, one line each); without it the board sorts them"
                        " after queued and --brief counts them on its last line")
    p.add_argument("--project", help="only this project's tickets")
    p.set_defaults(func=views.cmd_board)
    p = sub.add_parser("fleet", help="every member (v_fleet)")
    p.set_defaults(func=views.cmd_fleet)
    p = sub.add_parser("card", help="a ticket's team tree, and the worktree it is bound to with that worktree's branch, read from git")
    p.add_argument("key")
    p.add_argument("--project", help="refuse unless the ticket is this project's")
    p.set_defaults(func=views.cmd_card)
    p = sub.add_parser("events", help="the event log")
    p.add_argument("--ticket")
    p.add_argument("--member")
    p.add_argument("--kind", choices=kernel.EVENT_KINDS)
    p.add_argument("--limit", type=int)
    p.add_argument("--project", help="only this project's tickets' events and the project and session events naming it")
    p.set_defaults(func=views.cmd_events)
    return parser


def normalize_argv(argv):
    """`--as ACTOR`, `--as=ACTOR` and `--json` are accepted anywhere on the line (a subagent
    types `spud member result --as <id> ...` as naturally as the other order); they are
    hoisted in front of the command for argparse.  Tokens after a literal `--` stay put."""
    front, rest = [], []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            rest.extend(argv[i:])
            break
        if tok == "--as" and i + 1 < len(argv):
            front += ["--as", argv[i + 1]]
            i += 2
            continue
        if tok.startswith("--as=") or tok == "--json":
            front.append(tok)
            i += 1
            continue
        rest.append(tok)
        i += 1
    return front + rest
