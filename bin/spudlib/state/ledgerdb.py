"""state/ledgerdb: Connections, transactions, migrations applied, config rows mirrored, events written.  Moved from bin/spud_ledger.py (SPD-065)."""

import contextlib
import json
import sqlite3

from . import backup, schema
from ..core import homeconf, kernel


def open_connection(path):
    con = sqlite3.connect(path, timeout=5.0, autocommit=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def connect(ctx):
    """One connection per process, the three pragmas set, the schema version checked."""
    if not ctx.db_path.is_file():
        raise kernel.SpudError(kernel.EXIT_ERROR, "no ledger at %s; run `spud init`" % ctx.db_path)
    con = open_connection(ctx.db_path)
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version > schema.SCHEMA_VERSION:
        raise kernel.SpudError(kernel.EXIT_ERROR, "the database is ahead of this CLI (user_version %d > %d); a newer spud wrote it" % (version, schema.SCHEMA_VERSION))
    if version < schema.SCHEMA_VERSION:
        raise kernel.SpudError(kernel.EXIT_ERROR, "the database is behind (user_version %d < %d); run `spud init` or `spud migrate`" % (version, schema.SCHEMA_VERSION))
    return con


@contextlib.contextmanager
def write_txn(con):
    """Every write: BEGIN IMMEDIATE ... COMMIT, short, holding nothing else."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


def apply_migrations(ctx, con, created):
    """Forward only; a VACUUM INTO backup before each migration of an existing database."""
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version > schema.SCHEMA_VERSION:
        raise kernel.SpudError(kernel.EXIT_ERROR, "the database is ahead of this CLI (user_version %d > %d); a newer spud wrote it" % (version, schema.SCHEMA_VERSION))
    applied = []
    backups = []
    try:
        for number, (name, sql) in enumerate(schema.MIGRATIONS, start=1):
            if number <= version:
                continue
            if not created and version > 0:
                backups.append(str(backup.do_backup(ctx, con, "pre-" + name)))
            # SQLite's table-rebuild recipe (lang_altertable.html section 7), which 0003_parked needs and the others do
            # not mind: foreign keys off before BEGIN, since the pragma is a no-op inside a transaction, and the check
            # inside it, so a copy that lost a reference rolls back and leaves the database as it was.
            con.execute("PRAGMA foreign_keys = OFF")
            with write_txn(con):
                con.executescript(sql)
                con.executescript(schema.VIEWS_AND_TRIGGERS)
                broken = con.execute("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise kernel.SpudError(kernel.EXIT_ERROR, "migration %s broke %d foreign key reference(s); rolled back" % (name, len(broken)))
                con.execute("PRAGMA user_version = %d" % number)
            applied.append(name)
            version = number
    finally:
        con.execute("PRAGMA foreign_keys = ON")
    return applied, backups


def sync_config_rows(ctx, con, at):
    """Mirror naming.pool into name_pool and project spud's prefixes; project spud is inserted at the tool's root (SPD-097:
    the home is no project)."""
    config = ctx.config
    pool = config.get("naming", {}).get("pool", [])
    ticket_prefix = config.get("tickets", {}).get("prefix", "SPD")
    team_prefix = config.get("teams", {}).get("prefix", "SPUD")
    con.execute("UPDATE name_pool SET active = 0")
    for name in pool:
        con.execute(
            "INSERT INTO name_pool (name, active) VALUES (?, 1) ON CONFLICT(name) DO UPDATE SET active = 1",
            (name,),
        )
    tool_key = "spud"
    row = con.execute("SELECT id FROM projects WHERE id = 1").fetchone()
    if row is None:
        con.execute(
            "INSERT INTO projects (id, key, name, root_path, ticket_prefix, team_prefix, created_at)"
            " VALUES (1, ?, ?, ?, ?, ?, ?)",
            (tool_key, config.get("identity", {}).get("name", "Spud"), str(ctx.tool), ticket_prefix, team_prefix, at),
        )
    else:
        con.execute(
            "UPDATE projects SET ticket_prefix = ?, team_prefix = ? WHERE id = 1",
            (ticket_prefix, team_prefix),
        )
    if con.execute("SELECT remote FROM projects WHERE id = 1").fetchone()["remote"] is None:  # SPD-014: informational
        remote = homeconf.git_remote_url(ctx.tool)
        if remote:
            con.execute("UPDATE projects SET remote = ? WHERE id = 1 AND remote IS NULL", (remote,))
    return {"pool": len(pool), "ticket_prefix": ticket_prefix, "team_prefix": team_prefix}


def write_event(con, at, actor, kind, body="", ticket_id=None, member_id=None, agent_id=None, data=None):
    cur = con.execute(
        "INSERT INTO events (at, actor, ticket_id, member_id, agent_id, kind, body, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (at, actor, ticket_id, member_id, agent_id, kind, body, json.dumps(data) if data is not None else None),
    )
    return cur.lastrowid
