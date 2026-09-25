"""commands/doctor: doctor."""

import os
import sqlite3
import sys

from . import ghread, homesync, logread, prcmds, projectboards, publish, settings_sync, vaultlock
from ..core import homeconf, kernel, launchagents, shipped
from ..hooks import gitrepos, hookio, snapshots, worktrees
from ..projects import agentdef, install, sessions
from ..render import prices
from ..state import actors, backup, ledgerdb, lookup, schema

# Both watcher problems name the log (SPD-118): the hooks refuse a shell command naming .spud/, so `spud logs render` is how a
# session reads how the last run ended -- render.log.1 once the next run has started -- or what a stuck one keeps logging.
WATCHER_LOG = "`spud logs render` shows its log"
WATCHER_DOWN = ("the render watcher %s is installed but not running: the vault is stale until `spud --as spud schedule install` reloads it;"
                " %s, the last run's end included" % (launchagents.RENDER_LABEL, WATCHER_LOG))
# The vault behind the ledger by more than a render takes, which a watcher running and stuck leaves behind exactly
# as a watcher that is down does.  The state of the vault, not of a process, so it is a problem either way.
VAULT_BEHIND = "the vault is behind the ledger"
RENDER_BEHIND = VAULT_BEHIND + " by %s, the oldest %s (a render lands within seconds of an event): `spud render` brings it up to date"
# A watcher holding its lock and rendering nothing is stale for the same reason a down one is, and reloads the same way;
# one command settles the vault now, the other the watcher that should have settled it.
RENDER_BEHIND_STUCK = RENDER_BEHIND + ", and `spud --as spud schedule install` reloads the watcher that is running and not rendering; " + WATCHER_LOG
# doctor's render line for each of the four states core/launchagents reports; the lag phrase follows it.
WATCHER_TEXT = {"absent": "not installed", "down": "installed, not running", "current": "running", "behind": "running"}
# A home with an empty registry is a working home -- the schema allows it and `spud init --no-project` makes one
# -- but it can hold no ticket, since tickets.project_id references a project.  So it is a note, not a problem, and the
# note names the command that ends it.  The first project registered gets id 1 and is the project the config names.
NO_PROJECT = ("no project is registered, so this home can hold no ticket:"
              " `spud --as spud project add <path> --key <key> --ticket-prefix %s --team-prefix %s --landing merge` registers project 1")
# A projects table with rows but no id 1: reachable by nothing the CLI does (`project remove` refuses id 1, archiving
# keeps the row), and cheap to report.  `config sync` no longer creates the row, so nothing names a fix.
NO_PROJECT_ONE = "no project 1 among the %d project(s) registered, so spud.config.json's prefixes (%s / %s) name no project"
# A config with no `owner.name`.  A note and never a problem: the home works, every shipped file renders, and
# what is missing is a name -- so the rendered CLAUDE.md, the brief template and the root note all say `the owner`
# where they would say a person's, which is exactly the shape of a config written before the block existed.  A problem
# here would fail `spud init`'s own step 10 on every such home and stop a command that has nothing left to do.
NO_CONFIG_OWNER = ("%s names no owner, so every file the tool generates for this home calls the person it works for %r:"
                   " add an \"owner\" block beside \"identity\" ({\"name\": \"…\", \"pronouns\": {\"subject\": \"…\","
                   " \"object\": \"…\", \"possessive\": \"…\"}}), then `%s --as spud home sync` writes the files again")
# The home's own .claude/settings.json, which `settings sync` writes and `init` writes at its step 6.  Its hook
# lines are the whole of what makes a session launched in the home Spud -- the SessionStart board, the path rule, the
# Agent and Bash guards -- and a home missing them loses every one of them in every home session, silently.
SETTINGS_SYNC = "`spud --as spud settings sync`"
# The consequence, and it is the same in every wrong state below: the lines are not there, so neither is anything they do.
UNHOOKED_HOME = (", so a session launched in the home records and guards nothing -- no SessionStart board, no path rule,"
                 " no Agent guard and no Bash guard")
HOME_HOOKS_ABSENT = "there is no %s, the settings file every session launched in the home reads" + UNHOOKED_HOME + ": run " + SETTINGS_SYNC
HOME_HOOKS_NONE = "%s carries none of this home's ledger hook lines" + UNHOOKED_HOME + ": run " + SETTINGS_SYNC
# A file one event short reads differently from one with nothing in it -- a hand edit, or a table an older sync wrote --
# so the events are named rather than the fact that something is wrong; the command is the same one either way.  One
# event short is the likeliest shape of this and the one whose grammar the plural would get wrong, hence the clause.
HOME_HOOKS_PARTIAL = "%s carries no ledger hook line of this home for %s, so %s nothing in a session launched in the home: run " + SETTINGS_SYNC
HOME_HOOKS_ONE_SHORT, HOME_HOOKS_MANY_SHORT = "that event records and guards", "those events record and guard"
# The fourth state, and the one where naming the command alone would be a lie: `settings sync` refuses a file it cannot
# read (it would otherwise drop a hand's whole settings), so the hand edit comes first and the command after it.
HOME_HOOKS_UNREADABLE = ("%s, so it carries no ledger hook line of this home" + UNHOOKED_HOME + ", and " + SETTINGS_SYNC
                         + " refuses it as it stands: make it a JSON object, or remove it, and then run that")


def cmd_doctor(ctx, args):
    report, problems, lines = doctor_report(ctx)
    if problems:
        raise kernel.SpudError(kernel.EXIT_ERROR, "doctor found %d problem(s): %s" % (len(problems), "; ".join(problems)), data=report)
    return kernel.Result(report, "\n".join(lines))


def doctor_report(ctx):
    """(report, problems, lines): what cmd_doctor prints and raises on; home move reads it too."""
    problems = []
    no_project = False  # an empty registry, reported as a note below, where the notes are made
    report = {
        "interpreter": {"path": sys.executable, "version": "%d.%d.%d" % sys.version_info[:3], "flags": {"isolated": bool(sys.flags.isolated), "no_site": bool(sys.flags.no_site)}},
        "sqlite": {"library": sqlite3.sqlite_version, "module": sqlite3.version if hasattr(sqlite3, "version") else None},
        "spud_home": {"path": str(ctx.home), "resolved_by": ctx.resolved_by, "config": str(ctx.config_path), "config_exists": ctx.config_path.is_file()},
        "tool": {"path": str(ctx.tool), "launcher": str(ctx.launcher), "checkout": homeconf.tool_checkout_kind(ctx.tool)},
        "database": {"path": str(ctx.db_path), "exists": ctx.db_path.is_file()},
        "schema_version": schema.SCHEMA_VERSION,
    }
    if sys.version_info[:2] != (3, 14):
        problems.append("interpreter is Python %s, not 3.14" % report["interpreter"]["version"])
    if not ctx.config_path.is_file():
        problems.append("no spud.config.json in %s" % ctx.home)
        config = None
    else:
        try:
            config = ctx.config
        except kernel.SpudError as e:
            problems.append(e.message)
            config = None
    if config is not None:
        cfg_problems = homeconf.config_problems(config)
        problems.extend("config: " + p for p in cfg_problems)
        report["config"] = {"problems": cfg_problems, "limits": config.get("limits"), "pool": len(config.get("naming", {}).get("pool", []))}
    report["settings"] = doctor_home_settings(ctx, problems)  # the home's own hook lines, beside the config's line
    pricing = prices.price_table(config)[0] if config is not None else None  # the price table, and below what it cannot price
    report["pricing"] = None if pricing is None else {"as_of": pricing["as_of"], "source": pricing["source"], "currency": "USD",
                                                      "models": sorted(pricing["models"]), "not_priced": []}
    db = report["database"]
    if db["exists"]:
        con = ledgerdb.open_connection(ctx.db_path)
        try:
            db["user_version"] = con.execute("PRAGMA user_version").fetchone()[0]
            db["journal_mode"] = con.execute("PRAGMA journal_mode").fetchone()[0]
            db["foreign_keys"] = con.execute("PRAGMA foreign_keys").fetchone()[0]
            db["busy_timeout"] = con.execute("PRAGMA busy_timeout").fetchone()[0]
            db["synchronous"] = con.execute("PRAGMA synchronous").fetchone()[0]
            if db["user_version"] != schema.SCHEMA_VERSION:
                problems.append("database user_version %d, CLI schema %d" % (db["user_version"], schema.SCHEMA_VERSION))
            if db["journal_mode"] != "wal":
                problems.append("journal_mode is %s, not wal" % db["journal_mode"])
            if db["user_version"] >= 1 and config is not None:
                # The comparison is project 1's, and it is made only when the home has a project 1.
                one = con.execute("SELECT key, ticket_prefix, team_prefix FROM projects WHERE id = 1").fetchone()
                config_prefixes = (config.get("tickets", {}).get("prefix"), config.get("teams", {}).get("prefix"))
                if one is not None:
                    if (one["ticket_prefix"], one["team_prefix"]) != config_prefixes:
                        problems.append("project %s is project 1 and its prefixes differ from spud.config.json; run `spud config sync`" % one["key"])
                else:
                    registered = con.execute("SELECT count(*) FROM projects").fetchone()[0]
                    if registered:
                        problems.append(NO_PROJECT_ONE % (registered, *config_prefixes))
                    else:
                        no_project = True
                db["live_members"] = con.execute("SELECT live FROM v_live").fetchone()[0]
                db["tickets"] = con.execute("SELECT count(*) FROM tickets").fetchone()[0]
                db["events"] = con.execute("SELECT count(*) FROM events").fetchone()[0]
                if report["pricing"] is not None:
                    for m in con.execute("SELECT m.* FROM members m JOIN tickets t ON t.id = m.ticket_id WHERE m.usage_json IS NOT NULL ORDER BY t.id, m.lineage").fetchall():
                        cost, reasons = prices.run_cost(m["usage_json"], pricing)
                        if cost is None and reasons:
                            report["pricing"]["not_priced"].append({"ref": lookup.member_ref(con, m["id"]), "reasons": reasons})
        finally:
            con.close()
    else:
        problems.append("no database at %s; run `spud init`" % ctx.db_path)
    # The backup copies, read from the directory listing alone.  No backup state is ever a problem:
    # a Mac switched off for a weekend misses its daily copies and is not at fault.
    daily, other = backup.backup_listing(backup.backups_dir(ctx))
    report["backups"] = {"dir": str(backup.backups_dir(ctx)), "daily": {"count": len(daily), "newest": daily[-1] if daily else None, "oldest": daily[0] if daily else None}, "other": other}
    notes = []
    if report["tool"]["checkout"] == "worktree":
        notes.append("the running bin/spud is in a linked worktree: hook lines written from here name it")
    if no_project:
        notes.append(NO_PROJECT % (config.get("tickets", {}).get("prefix"), config.get("teams", {}).get("prefix")))
    if config is not None and not (config.get("owner") or {}).get("name"):
        notes.append(NO_CONFIG_OWNER % (ctx.config_path, shipped.DEFAULT_OWNER["name"], ctx.launcher))
    report["projects"] = doctor_projects(ctx, problems, notes) if db["exists"] and db.get("user_version") == schema.SCHEMA_VERSION else []
    report["hooks"] = doctor_session_hooks(ctx, notes) if db["exists"] and db.get("user_version") == schema.SCHEMA_VERSION else None
    report["repositories"] = doctor_repositories(ctx, problems) if db["exists"] and db.get("user_version") == schema.SCHEMA_VERSION else None
    report["render"] = doctor_render(ctx, problems, notes) if db["exists"] and db.get("user_version") == schema.SCHEMA_VERSION and config is not None else None
    report["pull_requests"] = doctor_pull_requests(ctx, problems, notes) if db["exists"] and db.get("user_version") == schema.SCHEMA_VERSION else None
    report["shipped"] = doctor_shipped(ctx, notes) if db["exists"] and db.get("user_version") == schema.SCHEMA_VERSION else None
    report["vault"] = doctor_vault(ctx, notes)
    report["notes"] = notes
    report["problems"] = problems
    lines = [
        "python      %s (%s; isolated=%s, no_site=%s)" % (report["interpreter"]["path"], report["interpreter"]["version"], report["interpreter"]["flags"]["isolated"], report["interpreter"]["flags"]["no_site"]),
        "SQLite      %s" % report["sqlite"]["library"],
        "SPUD_HOME   %s (via %s)" % (report["spud_home"]["path"], report["spud_home"]["resolved_by"]),
        "tool        %s (bin/spud; %s)" % (ctx.tool, {"main": "main checkout", "worktree": "linked worktree", "none": "no git checkout"}[report["tool"]["checkout"]]),
        "config      %s%s" % (report["spud_home"]["config"], "" if report["spud_home"]["config_exists"] else " (missing)"),
        "settings    %s" % report["settings"]["line"],
        "database    %s%s" % (db["path"], "" if db["exists"] else " (missing)"),
    ]
    if db["exists"]:
        lines.append("            user_version %s, journal_mode %s, foreign_keys %s, busy_timeout %s, synchronous %s" % (db.get("user_version"), db.get("journal_mode"), db.get("foreign_keys"), db.get("busy_timeout"), db.get("synchronous")))
        if "tickets" in db:
            lines.append("            %d tickets, %d events, %d members alive" % (db["tickets"], db["events"], db["live_members"]))
    if daily:
        lines.append("backups     %d daily (newest %s, oldest %s), %d other" % (len(daily), daily[-1], daily[0], other))
    else:
        lines.append("backups     0 daily, %d other" % other)
    if report["pricing"] is not None:
        p = report["pricing"]
        lines.append("pricing     API list price in USD as of %s from %s: %d model%s" % (
            p["as_of"], p["source"] or "an unnamed source", len(p["models"]), "" if len(p["models"]) == 1 else "s"))
        lines.extend("            not priced: %s (%s)" % (x["ref"], "; ".join(x["reasons"])) for x in p["not_priced"])
    elif config is not None:
        lines.append("pricing     %s" % ("%s: every cost shows —" % prices.NO_TABLE if not prices.price_table(config)[1] else "no usable price table: see problems"))
    for p in report["projects"]:
        lines.append("project     %s at %s: %s" % (p["key"], p["root"], ", ".join(p["checks"]) or "no check passed"))
    if report["hooks"] is not None:  # the installation above can be perfect and this session still load none of it
        lines.extend(("hooks       " if n == 0 else "            ") + line for n, line in enumerate(report["hooks"]["lines"]))
    if report["repositories"] is not None:
        r = report["repositories"]
        lines.append("repos       %d checkout%s read: %s" % (
            len(r["checkouts"]), "" if len(r["checkouts"]) == 1 else "s",
            "%d finding(s), under problems" % len(r["findings"]) if r["findings"] else "nothing planted"))
    if report["render"] is not None:
        r = report["render"]
        lines.append("render      watcher %s; %s%s" % (r["watcher"], launchagents.lag_text(r["lag"]),
                                                       ("; %d hand-edited file(s)" % len(r["conflicts"])) if r["conflicts"] else ""))
    if report["pull_requests"] is not None:
        p = report["pull_requests"]
        lines.append("pull reqs   %d recorded, %d open, %d settled; reader %s" % (p["recorded"], p["open"], p["settled"], p["reader"]))
        lines.extend("            last read failed: %s %s: %s" % (x["ticket"], x["url"], x["check_error"]) for x in p["failed_checks"])
    if report["shipped"] is not None:
        s = report["shipped"]
        lines.append("shipped     %d tool-owned file(s) from %s; %s"
                     % (s["files"], s["share"],
                        "%d not what it ships" % len(s["differences"]) if s["differences"] else "every one is what it ships"))
    v = report["vault"]
    lines.append("vault       %s" % ("no .obsidian/ in the home" if not v["vault"] else
                                     "%d shipped file(s) and %d pinned plugin(s) and theme(s); %s"
                                     % (v["shipped"], v["pinned"],
                                        "%d changed here" % len(v["differences"]) if v["differences"] else "nothing changed here")))
    lines.extend("note        %s" % n for n in notes)
    lines.append("problems    %s" % (("\n            ".join(problems)) if problems else "none"))
    return report, problems, lines


def doctor_home_settings(ctx, problems):
    """doctor's settings line: whether the home's own `.claude/settings.json` -- what `spud settings sync` writes, and
    `init` at its step 6 -- carries a ledger hook line of this home for every event of HOOK_TABLE.  Nothing else in the
    report asks.  The project lines read each installed project's `.claude/settings.local.json`, a different file for a
    different directory; the `hooks` line reads the files the session doctor itself runs in loads, which for a session
    launched anywhere but the home are not this file at all -- so both could pass with this file absent, and did, which
    is how a green doctor came to mean less than `spud init`, whose last step is doctor, takes it to mean.

    A problem in every wrong state, and that is the point of the check rather than an oversight in it.  The `hooks` line
    is deliberately a note because what is wrong there is where the session was launched and nothing in the home is
    broken; here this home's own installation is broken, one command fixes it, and `init`'s step 10 and `home move`'s
    7b -- which refuse on this report's problems -- are right to refuse until it is run.  An event short is an event
    whose hook never runs, so `partial` is a problem too, and it names the events rather than leaving Eric to diff.

    The granularity is the event, not the matcher: one hook line is one event to every reader of such a file.  The table
    has one row per event since SPD-223, so in a file the sync writes now an event is a row; a file an older sync wrote
    carries PreToolUse in three rows, and one of them alone (the Agent row, say) reads as complete here.  That is the
    reading `settings_hold_hooks` has always made for the project lines, not a choice of this line's, and it is a
    proposal of its own; the next `settings sync` writes the one row in place of the three.

    The rendered line is in the report, as the `hooks` lines are, so `--json` says as much as the text does."""
    path = ctx.home / ".claude" / "settings.json"
    exists, unreadable = path.is_file(), None
    if exists:
        try:
            install.read_json_object(path)  # the read `settings sync` itself makes, and the message it would give
        except kernel.SpudError as e:
            unreadable = e.message
    missing = settings_sync.settings_missing_hooks(ctx, path)
    if unreadable is not None:
        problems.append(HOME_HOOKS_UNREADABLE % unreadable)
        line = "%s: not a readable JSON object, so no ledger hook of this home (see problems)" % path
    elif not missing:
        line = "%s: this home's ledger hooks, all %d events" % (path, len(settings_sync.TABLE_EVENTS))
    elif len(missing) == len(settings_sync.TABLE_EVENTS):
        problems.append((HOME_HOOKS_NONE if exists else HOME_HOOKS_ABSENT) % path)
        line = "%s%s: none of this home's ledger hooks (see problems)" % (path, "" if exists else " (missing)")
    else:
        problems.append(HOME_HOOKS_PARTIAL % (path, ", ".join(missing),
                                              HOME_HOOKS_ONE_SHORT if len(missing) == 1 else HOME_HOOKS_MANY_SHORT))
        line = "%s: no ledger hook line for %s (see problems)" % (path, ", ".join(missing))
    return {"path": str(path), "exists": exists, "unreadable": unreadable, "missing": missing,
            "events": [e for e in settings_sync.TABLE_EVENTS if e not in missing], "line": line}


PR_CHECK_FAILED = ("the last read of %s (%s) failed: %s; until it succeeds the ledger cannot see whether that landing happened"
                   " -- retry it with `spud pr reconcile --ticket %s`")


def doctor_pull_requests(ctx, problems, notes):
    """doctor's pull-request section: how many landing pull requests are recorded, open and settled, which
    program answers for GitHub, and every still-open one of an open ticket whose last read failed.  A failed read is a
    problem the way a down render watcher is: nothing is broken in the ledger, but until it succeeds a merge that has
    already happened stays invisible.  A reader turned off is a note, the way a watcher never installed is."""
    con = ledgerdb.connect(ctx)
    try:
        rows = [lookup.pr_dict(con, p) for p in lookup.pull_requests(con)]
        failed = prcmds.failed_checks(con)
    finally:
        con.close()
    reader = ghread.gh_program()
    if rows and not ghread.enabled():
        notes.append("the gh reader is off (SPUD_GH=off): no recorded pull request is read, and a merge stays invisible")
    for d in failed:
        problems.append(PR_CHECK_FAILED % (lookup.pr_name(d), d["ticket"], d["check_error"], d["ticket"]))
    return {"recorded": len(rows), "open": sum(1 for d in rows if d["state"] == "open"),
            "settled": sum(1 for d in rows if d["state"] != "open"),
            "reader": reader if ghread.enabled() else "off", "failed_checks": failed}


SYNC_ALL = "run `spud --as spud project sync --all`"
AGENT_ABSENT = "no spudagent definition at %s; " + SYNC_ALL
AGENTS_ABSENT = "no spudagent definitions %s; " + SYNC_ALL  # several of the base and its effort variants (SPD-222)
# A hand edit of the installed copy and a home whose launcher moved read the same way -- the copy is not what
# install renders from the tool repository's template now -- and one sync settles both.
AGENT_DIFFERS = "%s is not the spudagent definition this home installs from %s; " + SYNC_ALL
AGENTS_DIFFER = "%s are not the spudagent definitions this home installs from %s; " + SYNC_ALL
# Claude Code reads a project-scope agent definition in preference to the user-scope copy install writes, so a
# `.claude/agents/spudagent.md` in a project's own checkout is the definition every session there actually reads -- which
# is how the tool repository's own template, `{{launcher}}` and all, shadowed the installed copy until it moved under
# share/.  Nothing in the installed files shows it, so doctor says it in as many words.
AGENT_SHADOWED = ("%s exists, so a spudagent in that checkout reads it and not %s, the definition this home installs:"
                  " Claude Code prefers a project-scope agent definition to the user-scope one")


def agent_files(paths):
    """Several installed definitions in one phrase: `spudagent.md, spudagent-low.md in <the agents directory>`."""
    return "%s in %s" % (", ".join(path.name for path in paths), paths[0].parent)


def doctor_projects(ctx, problems, notes):
    """doctor's projects section: each active project, its root a main checkout, and when it is installed its local settings
    carrying this home's hooks, the file ignored, the user-scope agent being what this home installs now, the /spud skill
    present, and whether a definition of the project's own shadows the installed one.  The home pointer, the superseded
    worktree cache and that shadow are notes, never problems.

    The shadow is a note because the file is that repository's and not this home's: doctor's problems are what `home init`
    and `home move` refuse on, and neither has anything to do with a file a project's git tracks.  It is read for
    installed projects only -- the ones this home has written a definition for, and so the ones where two definitions can
    disagree.  The root is read, not each worktree: a file a project tracks reaches every worktree of it anyway."""
    out = []
    con = ledgerdb.open_connection(ctx.db_path)
    try:
        rows = con.execute("SELECT * FROM projects WHERE archived_at IS NULL ORDER BY id").fetchall()
    finally:
        con.close()
    # The tool repository's copy is the source, a template under share/ whose launcher is filled in per machine: the
    # installed copy is compared with what this home renders from it now, never with the source's bytes, which name no
    # machine's launcher.
    # Every definition install writes is checked: the base and each effort variant (SPD-222), which the spawn check names
    # as the subagent_type of a member planned at that effort, so a variant missing here is a spawn the harness refuses.
    source_agent = agentdef.agent_source(ctx)
    try:
        expected_agents, agent_gone = dict(agentdef.definitions(ctx)), None
    except kernel.SpudError as e:
        expected_agents, agent_gone = None, e.message
    for p in rows:
        root, checks, bad, project_agent = p["root_path"], [], [], None
        if not os.path.isdir(root):
            bad.append("root %s is not a directory" % root)
        else:
            try:
                proc = homeconf.run_git(root, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir", timeout=10)
                lines = proc.stdout.strip().split("\n") if proc.returncode == 0 else []
                if len(lines) == 2 and worktrees.file_identity(lines[0]) == worktrees.file_identity(root) and worktrees.file_identity(lines[1]) == worktrees.file_identity(os.path.join(root, ".git")):
                    checks.append("main checkout")
                else:
                    bad.append("root %s is not the main checkout of a git repository" % root)
            except kernel.SpudError as e:
                bad.append(e.message)
        if p["installed"]:
            files = install.install_files(ctx, p)
            if settings_sync.settings_hold_hooks(ctx, files["settings"], p["key"]):
                checks.append("hooks")
            else:
                bad.append("%s lacks this home's ledger hooks; run `spud --as spud project sync %s`" % (files["settings"], p["key"]))
            if os.path.isdir(root) and homeconf.run_git(root, "check-ignore", "-q", "--", install.SETTINGS_LOCAL, timeout=10).returncode == 0:
                checks.append("ignored")
            else:
                bad.append("%s is not ignored by git in %s" % (install.SETTINGS_LOCAL, root))
            if agent_gone is not None:
                bad.append(agent_gone)
            else:
                absent = [path for path in files["agents"].values() if not path.is_file()]
                differ = [path for name, path in files["agents"].items()
                          if path.is_file() and path.read_bytes() != expected_agents[name].encode("utf-8")]
                if absent:
                    bad.append(AGENT_ABSENT % absent[0] if len(absent) == 1 else AGENTS_ABSENT % agent_files(absent))
                if differ:
                    bad.append(AGENT_DIFFERS % (differ[0], source_agent) if len(differ) == 1 else AGENTS_DIFFER % (agent_files(differ), source_agent))
                if not absent and not differ:
                    checks.append("agent")
            if files["skill"].is_file():
                checks.append("skill")
            else:
                bad.append("no /spud skill at %s; run `spud --as spud project sync %s`" % (files["skill"], p["key"]))
            if not files["pointer"].is_file():
                notes.append("no home pointer at %s (a launcher copied outside every checkout cannot find the home)" % files["pointer"])
            for name, installed in files["agents"].items():
                own = agentdef.project_scope_agent(root, name)
                if own.is_file():  # it wins over the installed copy, so say so; the report carries the answer either way
                    project_agent = project_agent or str(own)
                    notes.append(AGENT_SHADOWED % (own, installed))
        else:
            checks.append("not installed")
        problems.extend("project %s: %s" % (p["key"], b) for b in bad)
        out.append({"key": p["key"], "root": root, "installed": bool(p["installed"]), "checks": checks, "problems": bad,
                    "project_scope_agent": project_agent})
    if (ctx.home / hookio.STATE_DIR / "worktrees.json").exists():
        notes.append(".spud/worktrees.json is superseded by .spud/worktrees/<key>.json and ignored")
    problem, note = snapshots.table_report(str(ctx.home))  # what the Bash hook reads a command word against
    if problem:
        problems.append(problem)
    if note:
        notes.append(note)
    return out


def doctor_session_hooks(ctx, notes):
    """doctor's hooks section: whether the session doctor itself runs in has this home's ledger hooks loaded
    where that session actually reads them -- the question the project lines above cannot answer, since they check the
    files this home installs and a session launched somewhere else reads none of them.

    A note, never a problem, for the two states that are proven wrong (`absent` and `partial`).  What is wrong then is
    where the session was launched, not anything in this home: doctor's exit code would otherwise call a healthy home
    broken, and `home move`, which refuses on this report's problems, would refuse from such a session -- which is
    exactly the session most likely to be running it.  `unknown` and `no_session` add nothing: the hooks line says what
    was read, and neither proves a gap."""
    con = ledgerdb.open_connection(ctx.db_path)
    try:
        hooks = sessions.session_hooks(ctx, con, actors.planning_session(os.environ))
    finally:
        con.close()
    hooks["lines"] = sessions.session_hooks_lines(ctx, hooks)
    if hooks["state"] in ("absent", "partial"):
        notes.append(hooks["lines"][0])  # the state; the fix is on the hooks line itself, and in the report
    return hooks


def doctor_shipped(ctx, notes):
    """doctor's tool-owned-files section: every file `commands/homesync` says the tool owns in a home --
    CLAUDE.md, the two ledger notes, the templates, the `.base` views and every shipped skill -- that this home either
    does not have or has and has changed, each a note naming `home sync`.

    Notes, not problems, for `doctor_vault`'s own reason and one more of this section's.  These files are Spud's to edit
    between syncs (`hooks/hookio.SPUD_PATHS` keeps a member out of them and lets him in), so a home somebody works in
    drifts from the template as a matter of course; nothing is broken while it does, and one command settles it.  A
    problem would also fail every later `spud init`, which stops on each one (`commands/homeinit.verify`) -- including
    the init that is a rerun over a home whose CLAUDE.md its owner has since edited.

    Read only with a database, because the prose is rendered against project 1's row: with no database there is no row
    to render from, and a home with no database has a problem of its own on the line above.
    """
    findings = homesync.home_findings(ctx)
    notes.extend(sentence for _what, sentence in findings)
    return {"share": str(shipped.share_dir(ctx)), "files": len(homesync.tool_owned(ctx)),
            "differences": [what for what, _sentence in findings]}


def doctor_vault(ctx, notes):
    """doctor's vault section: every shipped settings file, `.base` file, plugin and theme this home has *and*
    has changed since it was captured, each a note naming the command that settles it.

    Notes, not problems, and on purpose: Obsidian rewrites `graph.json` when Eric pans the graph and `Board.base` when
    he drags a column, so drift here is the normal state of a vault someone works in -- nothing is broken, the shipped
    copy is simply behind, and only a ticket can settle it, because `vault capture` writes into a worktree.  A problem
    would also fail every later `spud init`, which stops on each one (`commands/homeinit.verify`).  What the home does
    not have at all is no finding: `vault install` is the command for that, and init already says so when a download was
    refused.  A project's own Kanban board, `ledger/<project name>.base` (SPD-324), is the home's and no shipped view,
    so it is left out of every one of these notes.
    """
    findings = vaultlock.vault_findings(ctx, projectboards.home_board_paths(ctx))
    notes.extend(sentence for _what, sentence in findings)
    try:
        pinned = len(vaultlock.locked(vaultlock.read_lock(ctx)))
    except kernel.SpudError:
        pinned = 0
    return {"vault": (ctx.home / vaultlock.OBSIDIAN).is_dir() and str(ctx.home / vaultlock.OBSIDIAN),
            "shipped": len(vaultlock.shipped_settings(ctx)), "pinned": pinned,
            "differences": [what for what, _sentence in findings]}


def doctor_repositories(ctx, problems):
    """doctor's repositories section: every checkout the ledger knows -- the home, each active project's root and
    its listed worktrees -- read the way the Bash hook reads the repository of a git call (hooks/gitrepos), with every
    finding a problem: a hook that is not a sample, a program key at the local or worktree scope, a repository that is not
    the checkout's own.  The harness and Eric's terminal run git that no hook sees, so this is where Eric learns of one.
    A finding two checkouts share (their common directory's hooks, its config) is one problem, named at the first."""
    con = ledgerdb.connect(ctx)
    try:
        read, found = gitrepos.checkout_findings(ctx, con)
    except hookio.HookError as e:
        problems.append("repositories: %s" % e)
        return {"checkouts": [], "findings": [], "error": str(e)}
    finally:
        con.close()
    findings, named = [], set()
    for project, root, f in found:
        findings.append({"project": project["key"], "checkout": root, "kind": f["kind"], "what": f["what"], "file": f["file"], "text": f["text"]})
        if f["text"] not in named:
            named.add(f["text"])
            problems.append("checkout %s: %s. %s" % (root, f["text"], gitrepos.TO_DO))
    return {"checkouts": read, "findings": findings}


def doctor_render(ctx, problems, notes):
    """doctor's render section: which of core/launchagents' four states the render watcher is in and how
    far behind the ledger the vault is, then every rendered file whose on-disk text is neither the last render's nor the
    current one, each with the two commands that settle it.  A watcher installed and not running is a problem and one never
    installed is a note, as before; a vault behind past launchagents.RENDER_LAG_SECONDS is a problem of its own, raised
    whether or not a watcher holds the lock, because what is stale then is the vault and one `spud render` settles it.
    A watcher down, or running and stuck, names `spud logs render` (SPD-118), and `log` carries render.log and render.log.1."""
    con = ledgerdb.connect(ctx)
    try:
        watcher = launchagents.watcher_report(ctx, con)
        conflicts = publish.render_pass(ctx, con, None, check_only=True)["conflicts"]
    finally:
        con.close()
    lag = watcher["lag"]
    if watcher["state"] == "down":
        problems.append(WATCHER_DOWN)
    elif not watcher["installed"]:
        notes.append("no render watcher installed (%s): `spud --as spud schedule install`" % launchagents.RENDER_LABEL)
    if lag["behind"]:
        shape = RENDER_BEHIND_STUCK if watcher["state"] == "behind" else RENDER_BEHIND  # `behind` is the alive-and-stuck one
        problems.append(shape % (launchagents.behind_text(lag), kernel.ago_text(lag["seconds"])))
    for rel in conflicts:
        problems.append("hand-edited %s: accept it with `spud --as spud import --file %s`, or overwrite it with `spud --as spud render --discard %s`" % (rel, rel, rel))
    return {"watcher": WATCHER_TEXT[watcher["state"]], "state": watcher["state"], "lag": lag, "conflicts": conflicts,
            "log": [str(p) for p in logread.render_files(ctx)]}
