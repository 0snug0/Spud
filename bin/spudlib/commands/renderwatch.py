"""commands/renderwatch: render --watch, the loop LaunchAgent local.spud.render runs (SPD-097).  Whether a watcher is alive is
core/launchagents' since SPD-048."""

import contextlib
import fcntl
import os
import signal
import sqlite3
import stat
import sys
import time

from . import publish
from ..core import kernel, launchagents
from ..state import ledgerdb

WATCH_INTERVAL = 2.0  # seconds between two reads of the event log


def render_entry(ctx, args):
    """The parser's `render`: the watcher with --watch, else one pass."""
    return cmd_render_watch(ctx, args) if args.watch else publish.cmd_render(ctx, args)


def latest_event_id(con):
    """The highest event id whose kind is not `render`: what the vault must have caught up with.  A render event never
    triggers a pass, so the watcher does not chase its own writes."""
    return con.execute("SELECT COALESCE(MAX(id), 0) FROM events WHERE kind != 'render'").fetchone()[0]


def log_line(text):
    """One timestamped line on stdout, flushed: launchd redirects it into <home>/.spud/logs/render.log."""
    sys.stdout.write("%s %s\n" % (kernel.now(), text))
    sys.stdout.flush()


def truncate_log():
    """launchd appends to StandardOutPath; the watcher starts its log afresh when stdout is a regular file, and leaves a
    terminal or a pipe alone."""
    with contextlib.suppress(OSError, ValueError):
        fd = sys.stdout.fileno()
        if stat.S_ISREG(os.fstat(fd).st_mode):
            sys.stdout.flush()
            os.ftruncate(fd, 0)


def cmd_render_watch(ctx, args):
    """render --watch: every `interval` seconds compare the highest non-render event id with the id the last pass rendered
    through; when the database is ahead, run one pass under the render lock.  The watermark lives in this process (the
    renders table's through_event_id at start, then the id read before each pass), so a pass that changes nothing writes
    nothing.  Runs until SIGTERM or SIGINT, or `ticks` reads (tests).  Refused while another watcher holds the lock."""
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
    truncate_log()
    log_line("watching %s every %.2f s" % (ctx.db_path, args.interval))
    passes, ticks, seen = 0, 0, None
    try:
        while not stop and (args.ticks is None or ticks < args.ticks):
            try:
                con = ledgerdb.connect(ctx)
                try:
                    if seen is None:
                        seen = con.execute("SELECT COALESCE(MAX(through_event_id), 0) FROM renders").fetchone()[0]
                    latest = latest_event_id(con)
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
            ticks += 1
            slept = 0.0
            while not stop and slept < args.interval:
                time.sleep(min(0.1, args.interval - slept))
                slept += 0.1
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    why = ("signal %d" % stop[0]) if stop else ("%d ticks" % ticks)
    log_line("stopped after %d pass(es): %s" % (passes, why))
    return kernel.Result({"passes": passes, "ticks": ticks, "stopped_by": why}, "")
