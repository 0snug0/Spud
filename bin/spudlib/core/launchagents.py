"""core/launchagents: The two LaunchAgents' labels and plist paths, and how the render watcher is doing.
Moved below commands/ from commands/schedule and commands/renderwatch so that `spud board --brief`, doctor and
the SessionStart hook ask one check: no hook may import a command module.  That check is how far behind the
vault is, not whether a process is alive: a watcher running and stuck leaves every note stale and answered every liveness
question correctly, so the four states below are what doctor, the board and the SessionStart context report."""

import contextlib
import fcntl
import json
import os
from pathlib import Path

from . import kernel

# commands/schedule writes and loads both agents; `backup` runs `spud backup --daily`, `render` runs `spud render --watch`.
SCHEDULE_LABEL = "local.spud.backup"
RENDER_LABEL = "local.spud.render"
LABELS = {"backup": SCHEDULE_LABEL, "render": RENDER_LABEL}
WATCH_LOCK = "watch.lock"  # <home>/.spud/watch.lock, held for the watcher's life: doctor, board and SessionStart ask it whether a watcher is alive
RENDERED_MARK = "rendered.json"  # <home>/.spud/rendered.json: the event id the last pass rendered through, written by every pass
WATCHER_DOWN_LINE = "render watcher: installed but not running; the vault is stale (spud --as spud schedule install reloads it)"
WATCHER_BEHIND_LINE = "render watcher: %s unrendered, the oldest %s; the vault is stale (spud render brings it up to date)"

# The watcher reads the event log every two seconds and a pass over the whole vault takes well under a second, so a render
# lands within seconds of the event that earns it.  Two minutes of unrendered events is therefore not a slow machine: it is
# a watcher that has stopped doing its work, alive or not.  Small enough that the next session sees a stall that started
# minutes ago, large enough that a long pass, a busy database or a clock a second out never raises it.
RENDER_LAG_SECONDS = 120
# Where launchd reads this Mac's user agents from, and so the one directory commands/schedule writes into for no home but
# the machine's own (SPD-101).  SPUD_LAUNCH_AGENTS_DIR moves it, for tests and probes.
DEFAULT_AGENTS_DIR = "~/Library/LaunchAgents"


def agents_dir():
    """$SPUD_LAUNCH_AGENTS_DIR, default ~/Library/LaunchAgents, made absolute."""
    return Path(os.path.abspath(os.path.expanduser(os.environ.get("SPUD_LAUNCH_AGENTS_DIR") or DEFAULT_AGENTS_DIR)))


def agent_plist_path(agent):
    """<agents_dir()>/<label>.plist for `backup` or `render`."""
    return agents_dir() / (LABELS[agent] + ".plist")


def watch_lock_path(ctx):
    return ctx.home / ".spud" / WATCH_LOCK


def rendered_mark_path(ctx):
    return ctx.home / ".spud" / RENDERED_MARK


def watcher_alive(ctx):
    """True when a watcher holds the watch lock: taken non-blocking and released at once.  False with no lock file (no
    watcher ever ran for this home) and before `spud init`."""
    path = watch_lock_path(ctx)
    if not path.is_file():
        return False
    fd = os.open(str(path), os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def watcher_installed():
    """Whether the render watcher's LaunchAgent plist is installed: `down` means installed and not alive."""
    return agent_plist_path("render").is_file()


def latest_event_id(con):
    """The highest event id whose kind is not `render`: what the vault must have caught up with.  A render event never
    triggers a pass, so the watcher does not chase its own writes.  Moved here from commands/renderwatch: the
    watcher's own comparison is the one the report needs, and no hook may import a command module."""
    return con.execute("SELECT COALESCE(MAX(id), 0) FROM events WHERE kind != 'render'").fetchone()[0]


def record_render(ctx, through, at):
    """What every pass into the home leaves behind: the event id it rendered through, and when.  The renders table records
    only the files a pass wrote (a pass that changes nothing writes nothing), so its through_event_id stands still
    whenever an event changes no note -- an event the renderers never read, a hook denial say.  A lag read from that table
    alone would then call a vault that is perfectly current behind, and no `spud render` could clear it.  This mark advances
    on every pass instead, and writes no database row.  Best effort: a mark that cannot be written leaves the table's
    watermark to answer."""
    with contextlib.suppress(OSError):
        kernel.write_whole(rendered_mark_path(ctx), json.dumps({"through_event_id": through, "at": at}) + "\n")


def rendered_through(ctx, con):
    """The highest event id the vault is known to be rendered through: the last pass's mark, or the renders table's own
    watermark when there is no readable mark (a home last rendered by an older spud, one whose mark could not be written).
    The larger of the two, so neither source can drag the answer backwards."""
    through = con.execute("SELECT COALESCE(MAX(through_event_id), 0) FROM renders").fetchone()[0]
    try:
        marked = json.loads(rendered_mark_path(ctx).read_text(encoding="utf-8")).get("through_event_id")
    except (OSError, ValueError, AttributeError):
        marked = None
    return max(through, marked) if isinstance(marked, int) else through


def render_lag(ctx, con):
    """How far behind the ledger the vault is: the events since the last pass, the oldest one's age in seconds, and whether
    that age is past RENDER_LAG_SECONDS.  One query on a connection the caller already holds and one small file read, so
    `spud board --brief` and the SessionStart hook can ask it; it reads only, never rendering and never writing a row."""
    through = rendered_through(ctx, con)
    row = con.execute("SELECT COUNT(*) AS events, MIN(at) AS oldest FROM events WHERE id > ? AND kind != 'render'", (through,)).fetchone()
    events, oldest = row["events"], row["oldest"]
    seconds = kernel.seconds_since(oldest) if events else None
    seconds = None if seconds is None else int(seconds)
    return {"through": through, "events": events, "oldest": oldest, "seconds": seconds,
            "behind": bool(events) and seconds is not None and seconds >= RENDER_LAG_SECONDS}


def watcher_report(ctx, con):
    """The one answer doctor, `spud board --brief` and the SessionStart context share, in four states rather than two:
    `absent` (no plist installed), `down` (installed, nothing holding the lock), `current` (a watcher holds the lock and the
    vault has caught up) and `behind` (a watcher holds the lock and the vault has not -- the state liveness alone cannot
    see).  The lag comes with it, so no caller asks twice."""
    lag = render_lag(ctx, con)
    installed, alive = watcher_installed(), watcher_alive(ctx)
    state = "absent" if not installed else "down" if not alive else "behind" if lag["behind"] else "current"
    return {"state": state, "installed": installed, "alive": alive, "lag": lag}


def behind_text(lag):
    """`1 event` / `3 events`: how many events the vault has not caught up with."""
    return "%d event%s" % (lag["events"], "" if lag["events"] == 1 else "s")


def lag_text(lag):
    """The vault in one phrase, for doctor's render line: caught up, or how many events behind and how old the oldest is."""
    return "vault current" if not lag["events"] else "vault behind by %s, the oldest %s" % (behind_text(lag), kernel.ago_text(lag["seconds"]))


def watcher_lines(ctx, con):
    """The lines `spud board --brief` adds and the SessionStart context carries above the board: one when the watcher is
    down, one when the vault is behind, both when both, and none when the watcher is doing its work -- so a board with no
    such line means the vault on disk is the ledger.  A behind line stands whether or not a watcher is alive: what is wrong
    then is the vault, and `spud render` settles it either way."""
    report = watcher_report(ctx, con)
    lines = []
    if report["state"] == "down":
        lines.append(WATCHER_DOWN_LINE)
    if report["lag"]["behind"]:
        lines.append(WATCHER_BEHIND_LINE % (behind_text(report["lag"]), kernel.ago_text(report["lag"]["seconds"])))
    return tuple(lines)
