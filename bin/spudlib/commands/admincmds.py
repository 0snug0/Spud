"""commands/admincmds: migrate, config sync, sql, import.  Moved from bin/spud_ledger.py (SPD-065).

SPW-001: `cmd_init` left here for `commands/homeinit.create_database`, which is its body -- `spud init` stopped being a
command about a database and became the command that builds a home, and the database is step 2 of five."""

import re
import sqlite3

from ..core import kernel
from ..imports import accept, bulkimport
from ..state import actors, ledgerdb


def cmd_migrate(ctx, args):
    if not ctx.db_path.is_file():
        raise kernel.SpudError(kernel.EXIT_ERROR, "no ledger at %s; run `spud init`" % ctx.db_path)
    con = ledgerdb.open_connection(ctx.db_path)
    try:
        applied, backups = ledgerdb.apply_migrations(ctx, con, False)
        version = con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()
    text = ("migrated to user_version %d (%s)" % (version, ", ".join(applied))) if applied else ("up to date (user_version %d)" % version)
    return kernel.Result({"database": str(ctx.db_path), "user_version": version, "applied": applied, "backups": backups}, text)


NO_PROJECT_SYNCED = ("name_pool: %d names; no project is registered, so the config's prefixes (%s / %s) name none:"
                     " `spud --as spud project add <path> --key <key> --ticket-prefix %s --team-prefix %s` registers project 1")


def cmd_config_sync(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        at = kernel.now()
        with ledgerdb.write_txn(con):
            synced = ledgerdb.sync_config_rows(ctx, con)
            ledgerdb.write_event(con, at, "spud", "config.synced", "config synced", data=synced)
    finally:
        con.close()
    prefixes = (synced["ticket_prefix"], synced["team_prefix"])
    # SPW-001: the prefixes are project 1's, whichever project that is; with no row 1 they are nobody's, and the line
    # says so and names what registers one.  (`home project` was a leftover from before SPD-097 either way.)
    if synced["project"] is None:
        project, text = None, NO_PROJECT_SYNCED % (synced["pool"], *prefixes, *prefixes)
    else:
        project = {"key": synced["project"], "ticket_prefix": synced["ticket_prefix"], "team_prefix": synced["team_prefix"]}
        text = "name_pool: %d names; project %s's prefixes %s / %s" % (synced["pool"], synced["project"], *prefixes)
    return kernel.Result({"project": project, "pool": synced["pool"]}, text)


READ_ONLY_FIRST_WORDS = ("SELECT", "WITH", "VALUES", "EXPLAIN", "PRAGMA")


def cmd_sql(ctx, args):
    """spud sql --readonly '<statement>': one read statement against the database opened
    read-only (mode=ro, query_only); any actor, no writes possible."""
    if not args.readonly:
        raise kernel.SpudError(kernel.EXIT_USAGE, "spud sql runs read-only statements only; the syntax is: spud sql --readonly '<statement>'")
    if not ctx.db_path.is_file():
        raise kernel.SpudError(kernel.EXIT_ERROR, "no ledger at %s; run `spud init`" % ctx.db_path)
    statement = args.statement.strip().rstrip(";").strip()
    first = (re.match(r"[A-Za-z]+", statement) or re.match(r"", statement)).group(0).upper()
    if first not in READ_ONLY_FIRST_WORDS or (first == "PRAGMA" and "=" in statement):
        raise kernel.SpudError(kernel.EXIT_ERROR, "sql: only SELECT, WITH, VALUES, EXPLAIN and read-only PRAGMA statements run here; the syntax is: spud sql --readonly '<statement>' (got %r)" % statement[:60])
    uri = "file:%s?mode=ro" % str(ctx.db_path).replace("%", "%25").replace("?", "%3F").replace("#", "%23")
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5.0, autocommit=True)
    except sqlite3.Error as e:
        raise kernel.SpudError(kernel.EXIT_ERROR, "sql: cannot open %s read-only: %s" % (ctx.db_path, e))
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")
        con.execute("PRAGMA busy_timeout = 5000")
        try:
            cur = con.execute(statement)
            rows = [dict(r) for r in cur.fetchall()]
            columns = [d[0] for d in cur.description] if cur.description else []
        except (sqlite3.Error, sqlite3.Warning) as e:
            raise kernel.SpudError(kernel.EXIT_ERROR, "sql: %s" % e)
    finally:
        con.close()
    for r in rows:
        for k, v in r.items():
            if isinstance(v, bytes):
                r[k] = v.hex()
    text = kernel.table(rows, [(c, c) for c in columns]) if columns else "(no columns)"
    return kernel.Result({"columns": columns, "rows": rows, "count": len(rows)}, text)


def cmd_import(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        if args.file:
            if args.paths:
                raise kernel.SpudError(kernel.EXIT_USAGE, "--file takes one file; directories are the bulk import")
            actor = actors.resolve_actor(con, args.actor)
            actors.require_spud(con, actor, "accepting a hand edit")
            out = accept.accept_file(ctx, con, actor, args.file)
            return kernel.Result(out, "accepted %s (%s)" % (out["path"], ", ".join(out["changed"]) or "no changes"))
        paths = args.paths or [str(ctx.home)]
        counts = bulkimport.bulk_import(ctx, con, paths)
    finally:
        con.close()
    return kernel.Result(counts, "imported %d tickets, %d members, %d report files (%d entries); %d sections kept as prose"
                  % (counts["tickets"], counts["members"], counts["reports"], counts["report_entries"], counts["prose_sections"]))
