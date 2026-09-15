"""commands/doctor: doctor.  Moved from bin/spud_ledger.py (SPD-065)."""

import os
import sqlite3
import sys

from . import settings_sync
from ..core import homeconf, kernel
from ..hooks import hookio, worktrees
from ..projects import install
from ..render import prices
from ..state import backup, ledgerdb, lookup, schema


def cmd_doctor(ctx, args):
    problems = []
    report = {
        "interpreter": {"path": sys.executable, "version": "%d.%d.%d" % sys.version_info[:3], "flags": {"isolated": bool(sys.flags.isolated), "no_site": bool(sys.flags.no_site)}},
        "sqlite": {"library": sqlite3.sqlite_version, "module": sqlite3.version if hasattr(sqlite3, "version") else None},
        "spud_home": {"path": str(ctx.home), "resolved_by": ctx.resolved_by, "config": str(ctx.config_path), "config_exists": ctx.config_path.is_file()},
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
    pricing = prices.price_table(config)[0] if config is not None else None  # SPD-013: the price table, and below what it cannot price
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
                home = con.execute("SELECT ticket_prefix, team_prefix FROM projects WHERE id = 1").fetchone()
                if home is None:
                    problems.append("no home project row; run `spud config sync`")
                elif (home["ticket_prefix"], home["team_prefix"]) != (config.get("tickets", {}).get("prefix"), config.get("teams", {}).get("prefix")):
                    problems.append("home project prefixes differ from spud.config.json; run `spud config sync`")
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
    # The backup copies, read from the directory listing alone (SPD-012).  No backup state is ever a problem:
    # a Mac switched off for a weekend misses its daily copies and is not at fault.
    daily, other = backup.backup_listing(backup.backups_dir(ctx))
    report["backups"] = {"dir": str(backup.backups_dir(ctx)), "daily": {"count": len(daily), "newest": daily[-1] if daily else None, "oldest": daily[0] if daily else None}, "other": other}
    notes = []
    report["projects"] = doctor_projects(ctx, problems, notes) if db["exists"] and db.get("user_version") == schema.SCHEMA_VERSION else []
    report["notes"] = notes
    report["problems"] = problems
    lines = [
        "python      %s (%s; isolated=%s, no_site=%s)" % (report["interpreter"]["path"], report["interpreter"]["version"], report["interpreter"]["flags"]["isolated"], report["interpreter"]["flags"]["no_site"]),
        "SQLite      %s" % report["sqlite"]["library"],
        "SPUD_HOME   %s (via %s)" % (report["spud_home"]["path"], report["spud_home"]["resolved_by"]),
        "config      %s%s" % (report["spud_home"]["config"], "" if report["spud_home"]["config_exists"] else " (missing)"),
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
    lines.extend("note        %s" % n for n in notes)
    lines.append("problems    %s" % (("\n            ".join(problems)) if problems else "none"))
    result = kernel.Result(report, "\n".join(lines))
    if problems:
        raise kernel.SpudError(kernel.EXIT_ERROR, "doctor found %d problem(s): %s" % (len(problems), "; ".join(problems)), data=report)
    return result


def doctor_projects(ctx, problems, notes):
    """doctor's projects section (SPD-014): each active project but the home, its root a main checkout, and when it is
    installed its local settings carrying this home's hooks, the file ignored, the user-scope agent matching the home's
    and the /spud skill present.  The home pointer and the superseded worktree cache are notes, never problems."""
    out = []
    con = ledgerdb.open_connection(ctx.db_path)
    try:
        rows = con.execute("SELECT * FROM projects WHERE id != 1 AND archived_at IS NULL ORDER BY id").fetchall()
    finally:
        con.close()
    home_agent = ctx.home / ".claude" / "agents" / "spudagent.md"
    for p in rows:
        root, checks, bad = p["root_path"], [], []
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
            if files["agent"].is_file() and home_agent.is_file() and kernel.sha256_bytes(files["agent"].read_bytes()) == kernel.sha256_bytes(home_agent.read_bytes()):
                checks.append("agent")
            else:
                bad.append("%s differs from %s; run `spud --as spud project sync --all`" % (files["agent"], home_agent))
            if files["skill"].is_file():
                checks.append("skill")
            else:
                bad.append("no /spud skill at %s; run `spud --as spud project sync %s`" % (files["skill"], p["key"]))
            if not files["pointer"].is_file():
                notes.append("no home pointer at %s (a launcher copied outside every checkout cannot find the home)" % files["pointer"])
        else:
            checks.append("not installed")
        problems.extend("project %s: %s" % (p["key"], b) for b in bad)
        out.append({"key": p["key"], "root": root, "installed": bool(p["installed"]), "checks": checks, "problems": bad})
    if (ctx.home / hookio.STATE_DIR / "worktrees.json").exists():
        notes.append(".spud/worktrees.json is superseded by .spud/worktrees/<key>.json and ignored")
    return out
