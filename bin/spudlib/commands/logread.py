"""commands/logread: logs, the read of the LaunchAgents' log files a session cannot reach otherwise (SPD-118).

Every hook refuses a shell command naming the home's .spud/ (it guards the database), and a log is a file, so neither
`tail` nor `spud sql --readonly` reads <home>/.spud/logs/render.log.  This command does, and only reads: it takes no actor,
like `spud board`.  Each log is a name in LOGS -- its current file and, when its writer keeps one, the previous run's --
so another log joins as one more entry.

--tail N reads across the rotation.  The render watcher starts render.log afresh at each start and keeps the last run's
in render.log.1 (SPD-170), and since SPD-119 a watcher ends on every deploy, so the line that says why the last run
ended is the first thing a reader wants and is in render.log.1 the moment the next run starts.  So when the current file
holds fewer than N lines the rest are the previous file's last ones, printed first, and each file's lines are headed
`==> <path> <==` as tail(1) heads several files -- only when both give lines, so a plain read stays plain."""

import os
from datetime import datetime

from . import schedule
from ..core import kernel, lazy

DEFAULT_TAIL = 40
TAIL_BLOCK = 64 * 1024  # bytes read per step backwards from a file's end


def render_files(ctx):
    """The render watcher's log: render.log under the home, and render.log.1, the previous run's."""
    current = ctx.home / schedule.RENDER_LOG
    return current, current.with_name(current.name + ".1")


def backup_files(ctx):
    """The daily backup's log: one file, appended to by every run, which nothing rotates."""
    return os.path.abspath(os.path.expanduser(schedule.SCHEDULE_LOG)), None


# name -> (what it is, the function giving (current, previous or None)).  The order is `spud logs`'s listing.
LOGS = {
    "render": ("the render watcher, local.spud.render (`spud render --watch`)", render_files),
    "backup": ("the daily backup, local.spud.backup (`spud backup --daily`)", backup_files),
}


def tail_arg(value):
    """--tail N, the parser's type: a whole number of lines, at least 1."""
    try:
        n = int(value)
    except ValueError:
        n = 0
    if n < 1:
        raise lazy.argparse.ArgumentTypeError("a whole number of lines, at least 1, not %r" % value)
    return n


def tail_lines(path, n):
    """The last `n` lines of the file at `path` as text (a line's newline dropped; bytes that are not UTF-8 replaced),
    read backwards from the end in TAIL_BLOCK steps, so a long log costs what its tail does.  [] for a missing file."""
    try:
        f = open(path, "rb")
    except FileNotFoundError:
        return []
    with f:
        end = f.seek(0, os.SEEK_END)
        pos, data = end, b""
        while pos > 0 and data.count(b"\n") <= n:
            step = min(TAIL_BLOCK, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
    if data.endswith(b"\n"):
        data = data[:-1]
    if not data:
        return []
    return data.decode("utf-8", errors="replace").split("\n")[-n:]


def file_state(path):
    """{path, exists, bytes, modified} of one log file; modified is the local ISO time of its last write."""
    try:
        st = os.stat(path)
    except OSError:
        return {"path": str(path), "exists": False, "bytes": None, "modified": None}
    return {"path": str(path), "exists": True, "bytes": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")}


def cmd_logs(ctx, args):
    """`spud logs` lists the logs; `spud logs <name> [--tail N]` prints the last N lines of one, across its rotation."""
    if args.name is None:
        if args.tail is not None:
            raise kernel.SpudError(kernel.EXIT_USAGE, "--tail reads one log: name it (%s)" % ", ".join(LOGS))
        return list_logs(ctx)
    n = DEFAULT_TAIL if args.tail is None else args.tail
    what, files = LOGS[args.name]
    current, previous = files(ctx)
    now = tail_lines(current, n)
    before = tail_lines(previous, n - len(now)) if previous is not None and len(now) < n else []
    parts = [dict(file_state(previous), previous=True, lines=before)] if before else []
    parts.append(dict(file_state(current), previous=False, lines=now))
    data = {"log": args.name, "what": what, "tail": n, "lines": len(before) + len(now), "files": parts}
    if not before and not now:
        exists = [p for p in (current, previous) if p is not None and os.path.exists(p)]
        text = ("%s is empty" % " and ".join(str(p) for p in exists)) if exists else (
            "no %s log yet at %s: %s has not written one in this home" % (args.name, current, what))
        return kernel.Result(data, text)
    if before:
        text = "\n".join(["==> %s <==" % previous, *before, "", "==> %s <==" % current, *now])
    else:
        text = "\n".join(now)
    return kernel.Result(data, text)


def list_logs(ctx):
    """Every log LOGS names: what writes it, and each of its files with its size and last write."""
    out, lines = [], []
    for name, (what, files) in LOGS.items():
        states = [file_state(p) for p in files(ctx) if p is not None]
        out.append({"log": name, "what": what, "files": states})
        lines.append("%-8s %s" % (name, what))
        for s in states:
            lines.append("         %s: %s" % (s["path"], ("%d bytes, last written %s" % (s["bytes"], s["modified"])) if s["exists"] else "not there"))
    lines.append("read one with `spud logs <name> --tail N` (default %d lines)" % DEFAULT_TAIL)
    return kernel.Result({"logs": out}, "\n".join(lines))
