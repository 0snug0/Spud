"""commands/admincmds: init, migrate, config sync, sql, import.  Moved from bin/spud_ledger.py (SPD-065)."""

import re
import sqlite3

from ..core import kernel
from ..imports import accept, bulkimport
from ..state import actors, ledgerdb


def cmd_init(ctx, args):
    created = not ctx.db_path.exists()
    ctx.config  # the config must exist before a ledger is created
    ctx.db_path.parent.mkdir(parents=True, exist_ok=True)
    con = ledgerdb.open_connection(ctx.db_path)
    try:
        if created:
            con.execute("PRAGMA journal_mode = WAL")
        applied, backups = ledgerdb.apply_migrations(ctx, con, created)
        at = kernel.now()
        with ledgerdb.write_txn(con):
            ledgerdb.sync_config_rows(ctx, con, at)
        version = con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()
    data = {"database": str(ctx.db_path), "created": created, "user_version": version, "applied": applied, "backups": backups}
    if created:
        text = "created %s (user_version %d)" % (ctx.db_path, version)
    elif applied:
        text = "migrated %s to user_version %d (%s)" % (ctx.db_path, version, ", ".join(applied))
    else:
        text = "%s is up to date (user_version %d)" % (ctx.db_path, version)
    return kernel.Result(data, text)


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


def cmd_config_sync(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        at = kernel.now()
        with ledgerdb.write_txn(con):
            synced = ledgerdb.sync_config_rows(ctx, con, at)
            ledgerdb.write_event(con, at, "spud", "config.synced", "config synced", data=synced)
    finally:
        con.close()
    return kernel.Result({"project": {"ticket_prefix": synced["ticket_prefix"], "team_prefix": synced["team_prefix"]}, "pool": synced["pool"]},
                  "name_pool: %d names; home project prefixes %s / %s" % (synced["pool"], synced["ticket_prefix"], synced["team_prefix"]))


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
