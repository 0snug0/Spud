"""commands/renderwatch: render --watch, the loop LaunchAgent local.spud.render runs.  Whether a watcher is alive is
core/launchagents' question, and so is the comparison this loop makes: the same watermark and the same highest
non-render event id answer doctor, the board and the SessionStart context."""

import contextlib
import fcntl
import os
import signal
import sqlite3
import stat
import sys
import time

from . import publish, schedule
from ..core import kernel, launchagents
from ..state import ledgerdb, schema

WATCH_INTERVAL = 2.0  # seconds between two reads of the event log


def render_entry(ctx, args):
    """The parser's `render`: the watcher with --watch, else one pass."""
    return cmd_render_watch(ctx, args) if args.watch else publish.cmd_render(ctx, args)


def log_line(text):
    """One timestamped line on stdout, flushed: launchd redirects it into <home>/.spud/logs/render.log
    (the previous run's is render.log.1, rotate_log)."""
    sys.stdout.write("%s %s\n" % (kernel.now(), text))
    sys.stdout.flush()


def kept_tail(path, limit):
    """The last `limit` bytes of the file at most, read from there alone, and cut at a line start when the cut falls
    inside a line."""
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - limit))
        tail = f.read(limit)
    if size <= limit:
        return tail
    start = tail.find(b"\n") + 1
    return tail[start:] if 0 < start < len(tail) else tail


def rotate_log(ctx):
    """Start the watcher's log afresh, keeping the previous run's in render.log.1 (SPD-170).  Since SPD-119 a watcher ends
    on every deploy and KeepAlive starts the next, and the old truncation erased the line saying why the last run ended.

    launchd opens <home>/.spud/logs/render.log as StandardOutPath and StandardErrorPath and hands the process those fds
    before any Python runs, so the file is never renamed: a rename would leave both fds writing into render.log.1.  The old
    content's tail (at most RENDER_LOG_KEEP bytes, so the pair stays bounded however often the watcher restarts) is copied
    to render.log.1 through a temporary file and a rename, then render.log is truncated in place and each of stdout and
    stderr that is this file is sought back to its start, which lands the next write at offset 0 whether or not launchd
    opened it O_APPEND.  A stdout that is not the home's render.log -- a terminal, a pipe, a file a person redirected it
    to -- is left alone."""
    log = ctx.home / schedule.RENDER_LOG
    with contextlib.suppress(OSError, ValueError):
        fd = sys.stdout.fileno()
        st = os.fstat(fd)
        disk = os.stat(log)
        if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != (disk.st_dev, disk.st_ino):
            return
        sys.stdout.flush()
        sys.stderr.flush()
        if st.st_size:
            kept = log.with_name(log.name + ".1")
            partial = log.with_name(log.name + ".1.tmp")
            partial.write_bytes(kept_tail(log, schedule.RENDER_LOG_KEEP))
            os.replace(partial, kept)
        os.ftruncate(fd, 0)
        for each in (fd, sys.stderr.fileno()):
            same = os.fstat(each)
            if (same.st_dev, same.st_ino) == (st.st_dev, st.st_ino):
                os.lseek(each, 0, os.SEEK_SET)


def file_stamp(path):
    """(mtime_ns, size) of a file, or None when it is gone: what changes when a merge rewrites it."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def program_stamps():
    """{path: stamp} for every file of the program this process loaded: the entry and each module of spudlib.  A merge into
    the tool's main rewrites some of them under a watcher that keeps running what it loaded (SPD-119)."""
    stamps = {}
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if path and (name == "spud_ledger" or name.startswith("spudlib.")):
            stamps[path] = file_stamp(path)
    return stamps


def changed_program_file(stamps):
    """The first loaded file whose stamp is not the one it had at start, or None: the code on disk is not the code running."""
    return next((path for path, stamp in sorted(stamps.items()) if file_stamp(path) != stamp), None)


def database_ahead(ctx):
    """Whether the database's user_version is past the schema this process loaded: a newer spud migrated it, so no tick of
    this process can ever connect again.  False when the version cannot be read (the next tick says why)."""
    try:
        con = ledgerdb.open_connection(ctx.db_path)
        try:
            return con.execute("PRAGMA user_version").fetchone()[0] > schema.SCHEMA_VERSION
        finally:
            con.close()
    except sqlite3.Error:
        return False


def cmd_render_watch(ctx, args):
    """render --watch: every `interval` seconds compare the highest non-render event id with the id the last pass rendered
    through; when the database is ahead, run one pass under the render lock.  The watermark lives in this process (the last
    pass's mark at start, then the id read before each pass), so a pass that changes nothing writes no row.  Runs until
    SIGTERM or SIGINT, or `ticks` reads (tests).  Refused while another watcher holds the lock.

    It also ends, logging why, when this process can no longer be the right one to render (SPD-119): when a file of the
    program it loaded changed on disk -- a merge into the tool's main is a deploy, and this is the one process that outlives
    it -- or when the database is ahead of the schema it loaded, which `ledgerdb.connect` refuses on every tick forever.
    Before, that refusal was logged and the loop went round again: the lock stayed held and nothing rendered.  Ending is
    the fix, because the LaunchAgent's KeepAlive starts the watcher again on the code now on disk."""
    if args.out or args.discard:
        raise kernel.SpudError(kernel.EXIT_USAGE, "--watch renders into the home on its own: no --out, no --discard")
    if not ctx.db_path.is_file():
        raise kernel.SpudError(kernel.EXIT_ERROR, "no ledger at %s; run `spud init`" % ctx.db_path)
    fd = os.open(str(launchagents.watch_lock_path(ctx)), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise kernel.SpudError(kernel.EXIT_ERROR, "a render watcher is already running for %s (it holds %s)" % (ctx.home, launchagents.watch_lock_path(ctx)))
    stop = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda signum, frame: stop.append(signum))
    rotate_log(ctx)
    log_line("watching %s every %.2f s" % (ctx.db_path, args.interval))
    passes, ticks, seen, ended = 0, 0, None, None
    loaded = program_stamps()
    try:
        while not stop and (args.ticks is None or ticks < args.ticks):
            changed = changed_program_file(loaded)
            if changed:
                ended = "the program changed on disk (%s)" % changed
                break
            try:
                con = ledgerdb.connect(ctx)
                try:
                    if seen is None:
                        seen = launchagents.rendered_through(ctx, con)
                    latest = launchagents.latest_event_id(con)
                    if latest > seen:
                        with publish.render_lock(ctx):
                            result = publish.render_pass(ctx, con, None)
                        passes += 1
                        seen = latest
                        if result["written"] or result["restyled"] or result["new_conflicts"]:
                            log_line("rendered %d, unchanged %d, restyled %d, conflicts %d (through event %d)" % (
                                len(result["written"]), len(result["unchanged"]), len(result["restyled"]), len(result["conflicts"]), latest))
                finally:
                    con.close()
            except (kernel.SpudError, sqlite3.Error) as e:
                log_line("error: %s" % (e.message if isinstance(e, kernel.SpudError) else e))
                if database_ahead(ctx):
                    ended = "the database is ahead of this program's schema %d" % schema.SCHEMA_VERSION
                    break
            ticks += 1
            slept = 0.0
            while not stop and slept < args.interval:
                time.sleep(min(0.1, args.interval - slept))
                slept += 0.1
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    why = ("signal %d" % stop[0]) if stop else ended or ("%d ticks" % ticks)
    log_line("stopped after %d pass(es): %s" % (passes, why))
    return kernel.Result({"passes": passes, "ticks": ticks, "stopped_by": why}, "")
