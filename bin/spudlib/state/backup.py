"""state/backup: The backup copy, its listing, quick check and daily prune.  Moved from bin/spud_ledger.py (SPD-065)."""

import os
import re
import sqlite3
from datetime import datetime

from ..core import kernel


def backup_stamp():
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def do_backup(ctx, con, label=None, stamp=None):
    """wal_checkpoint(TRUNCATE) then VACUUM INTO .spud/backups/ledger-<stamp>[-<label>].db.  The stamp is
    the clock's, read here unless the caller read it first (backup --daily names its copy before writing)."""
    backups = ctx.home / ".spud" / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    name = "ledger-%s%s.db" % (stamp or backup_stamp(), ("-" + label) if label else "")
    target = backups / name
    if target.exists():
        raise kernel.SpudError(kernel.EXIT_ERROR, "backup %s already exists" % target)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.execute("VACUUM INTO ?", (str(target),))
    return target


# backup --daily (SPD-012): one checked copy a local calendar day, and the newest DAILY_KEEP daily copies kept.
DAILY_LABEL = "daily"
DAILY_KEEP = 14
# A daily copy's whole name.  The stamp is fixed-width, so the names sort chronologically; ASCII digits and
# fullmatch only, so neither other digits nor a trailing newline make a look-alike name a daily copy.
DAILY_NAME_RE = re.compile(r"ledger-[0-9]{8}T[0-9]{6}-daily\.db")
BACKUP_NAME_RE = re.compile(r"ledger-.*\.db", re.DOTALL)  # ledger-*.db: manual, pre-migration and daily copies


def backups_dir(ctx):
    return ctx.home / ".spud" / "backups"


def backup_listing(directory):
    """(daily, other), from the listing of .spud/backups/ alone: the names of the regular files (a symlink or
    a directory is not one) whose whole name is a daily copy's, oldest first, and the count of the other
    regular files named ledger-*.db.  A missing or unreadable directory holds no copies."""
    try:
        with os.scandir(directory) as entries:
            files = [entry.name for entry in entries if entry.is_file(follow_symlinks=False)]
    except OSError:
        return [], 0
    daily = sorted(name for name in files if DAILY_NAME_RE.fullmatch(name))
    other = sum(1 for name in files if BACKUP_NAME_RE.fullmatch(name) and not DAILY_NAME_RE.fullmatch(name))
    return daily, other


def backup_quick_check(path):
    """PRAGMA quick_check on a backup copy: its rows, ['ok'] for a sound copy.  The copy is opened read-only
    and immutable, so the check writes nothing beside it; a copy SQLite cannot read answers with the error."""
    uri = "file:%s?mode=ro&immutable=1" % str(path).replace("%", "%25").replace("?", "%3F").replace("#", "%23")
    try:
        con = sqlite3.connect(uri, uri=True, autocommit=True)
        try:
            return [str(row[0]) for row in con.execute("PRAGMA quick_check").fetchall()] or ["quick_check returned no rows"]
        finally:
            con.close()
    except sqlite3.Error as e:
        return ["%s: %s" % (type(e).__name__, e)]


def prune_daily_backups(directory, keep, spare, pruned):
    """Unlink all but the newest `keep` daily copies in `directory`, and never `spare`, the copy this run wrote
    and checked.  Each path unlinked is directory / a name backup_listing returned: never a glob, a tree, or
    any other entry.  Appends each name unlinked to `pruned` and returns how many daily copies are left; an
    OSError other than a copy already gone stops the prune and propagates."""
    if keep < 1:
        raise kernel.SpudError(kernel.EXIT_USAGE, "backup --daily keeps at least one daily copy (--keep %d)" % keep)
    daily, _ = backup_listing(directory)
    gone = 0
    for name in daily[:-keep]:
        if name == spare:
            continue
        try:
            (directory / name).unlink()
        except FileNotFoundError:
            gone += 1
            continue
        pruned.append(name)
    return len(daily) - len(pruned) - gone
