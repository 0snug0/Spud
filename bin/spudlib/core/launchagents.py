"""core/launchagents: The two LaunchAgents' labels and plist paths, and whether the render watcher is down (SPD-097).
Moved below commands/ from commands/schedule and commands/renderwatch on SPD-048, so that `spud board --brief`, doctor and
the SessionStart hook ask one check: no hook may import a command module."""

import fcntl
import os
from pathlib import Path

# commands/schedule writes and loads both agents; `backup` runs `spud backup --daily`, `render` runs `spud render --watch`.
SCHEDULE_LABEL = "local.spud.backup"
RENDER_LABEL = "local.spud.render"
LABELS = {"backup": SCHEDULE_LABEL, "render": RENDER_LABEL}
WATCH_LOCK = "watch.lock"  # <home>/.spud/watch.lock, held for the watcher's life: doctor, board and SessionStart ask it whether a watcher is alive
WATCHER_DOWN_LINE = "render watcher: installed but not running; the vault is stale (spud --as spud schedule install reloads it)"


def agent_plist_path(agent):
    """$SPUD_LAUNCH_AGENTS_DIR/<label>.plist for `backup` or `render`, default ~/Library/LaunchAgents."""
    agents = os.environ.get("SPUD_LAUNCH_AGENTS_DIR") or "~/Library/LaunchAgents"
    return Path(os.path.abspath(os.path.expanduser(agents))) / (LABELS[agent] + ".plist")


def watch_lock_path(ctx):
    return ctx.home / ".spud" / WATCH_LOCK


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


def watcher_down_line(ctx):
    """The line `spud board --brief` adds and the SessionStart context carries above the board when the watcher is down
    (its plist installed, no watcher holding the lock), else None.  A stat when no watcher is installed, one flock when one
    is: cheap enough for the hook."""
    return WATCHER_DOWN_LINE if watcher_installed() and not watcher_alive(ctx) else None
