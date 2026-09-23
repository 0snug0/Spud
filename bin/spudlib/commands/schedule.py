"""commands/schedule: spud backup, and the two LaunchAgents that run the daily backup and the render watcher."""

import contextlib
import os
import re
import shlex
import sqlite3
import sys
import time

from . import settings_sync
from ..core import kernel, launchagents, lazy
from ..state import backup, ledgerdb


def cmd_backup(ctx, args):
    if args.keep is not None and not args.daily:
        raise kernel.SpudError(kernel.EXIT_USAGE, "--keep is how many daily copies `spud backup --daily` keeps; plain `spud backup` prunes nothing")
    keep = backup.DAILY_KEEP if args.keep is None else args.keep
    if args.daily and keep < 1:
        raise kernel.SpudError(kernel.EXIT_USAGE, "backup --daily keeps at least one daily copy (--keep %d)" % keep)
    con = ledgerdb.connect(ctx)
    try:
        if args.daily:
            return daily_backup(ctx, con, keep)
        path = backup.do_backup(ctx, con)
    finally:
        con.close()
    return kernel.Result({"path": str(path)}, "backup written to %s" % path)


def daily_backup(ctx, con, keep):
    """backup --daily: today's copy unless one exists, quick_check-ed, then the newest `keep` daily copies kept.
    Nothing is pruned unless this run wrote a copy that checked ok."""
    directory = backup.backups_dir(ctx)
    stamp = backup.backup_stamp()
    daily, _ = backup.backup_listing(directory)
    today = [name for name in daily if name.startswith("ledger-%sT" % stamp[:8])]
    if today:
        path = directory / today[-1]
        return kernel.Result({"path": str(path), "written": False, "pruned": [], "kept": len(daily)},
                      "today's daily backup already exists: %s\nnothing written, nothing pruned; %d daily %s kept"
                      % (path, len(daily), "copy" if len(daily) == 1 else "copies"))
    target = directory / ("ledger-%s-%s.db" % (stamp, backup.DAILY_LABEL))
    not_written = {"path": str(target), "written": False, "pruned": [], "kept": len(daily)}
    in_the_way = os.path.lexists(target)
    try:
        path = backup.do_backup(ctx, con, backup.DAILY_LABEL, stamp=stamp)
    except BaseException as e:
        # A write that failed part way can leave a partial copy behind: remove it, when this run made it.
        if not in_the_way and target.is_file() and not target.is_symlink():
            with contextlib.suppress(OSError):
                target.unlink()
        if isinstance(e, (kernel.SpudError, sqlite3.Error, OSError)):
            raise kernel.SpudError(kernel.EXIT_ERROR, "daily backup %s not written, nothing pruned: %s" % (target, e.message if isinstance(e, kernel.SpudError) else e),
                            data=not_written)
        raise
    check = backup.backup_quick_check(path)
    if check != ["ok"]:
        try:
            path.unlink()
            removed = "the copy was removed"
        except FileNotFoundError:
            removed = "the copy is gone"
        except OSError as e:
            removed = "the copy could not be removed (%s)" % e
        raise kernel.SpudError(kernel.EXIT_ERROR, "quick_check of the daily backup %s did not return ok (%s); %s, nothing pruned" % (path, "; ".join(check)[:1000], removed),
                        data=dict(not_written, quick_check=check))
    pruned = []
    try:
        kept = backup.prune_daily_backups(directory, keep, path.name, pruned)
    except OSError as e:
        raise kernel.SpudError(kernel.EXIT_ERROR, "daily backup written to %s (quick_check ok), but the prune stopped: %s" % (path, e),
                        data={"path": str(path), "written": True, "pruned": pruned, "kept": len(backup.backup_listing(directory)[0])})
    text = "daily backup written to %s (quick_check ok)\npruned %d daily %s%s; %d kept" % (
        path, len(pruned), "copy" if len(pruned) == 1 else "copies", (": " + ", ".join(pruned)) if pruned else "", kept)
    return kernel.Result({"path": str(path), "written": True, "pruned": pruned, "kept": kept}, text)


# schedule: the macOS LaunchAgents.  Eric chose launchd over a Claude scheduled task: the CLI runs alone, with
# the app closed, and a run missed during sleep fires at wake.  Two agents: the daily backup, and the
# render watcher that keeps the vault current (RunAtLoad and KeepAlive, so launchd restarts it whenever it exits).  Their
# labels and plist paths are core/launchagents'.
SCHEDULE_AT = "03:00"
SCHEDULE_LOG = "~/Library/Logs/spud-backup.log"
RENDER_LOG = ".spud/logs/render.log"  # under the home; the watcher truncates it at each start
# Seconds slept before each bootstrap retry: launchd can refuse a bootstrap while the job it has just booted
# out is still going away, so five attempts over about two seconds.
BOOTSTRAP_RETRY_DELAYS = (0.25, 0.5, 0.5, 0.75)


def at_arg(value):
    """--at HH:MM, local time from 00:00 to 23:59: (hour, minute)."""
    m = re.fullmatch(r"([01]?[0-9]|2[0-3]):([0-5][0-9])", value)
    if not m:
        raise lazy.argparse.ArgumentTypeError("expected HH:MM from 00:00 to 23:59, got %r" % value)
    return int(m.group(1)), int(m.group(2))


def require_spud_flag(args, what):
    """A Spud-only command that reads and writes no ledger row: --as must name Spud himself."""
    if args.actor is None:
        raise kernel.SpudError(kernel.EXIT_ERROR, "`%s` is Spud's and needs --as spud" % what)
    if args.actor != "spud":
        raise kernel.SpudError(kernel.EXIT_OWNERSHIP, "`%s` is Spud's; %s may not (use --as spud)" % (what, args.actor))


def schedule_plist_path():
    """The backup agent's plist: the name every caller from before the render watcher knows."""
    return launchagents.agent_plist_path("backup")


def schedule_plist(ctx, at):
    """The LaunchAgent as a dict: the tool's bin/spud run by this interpreter as
    given, symlinks unresolved so a Homebrew upgrade keeps the path valid; SPUD_HOME set, because launchd's
    environment is minimal; at load and daily at `at` (hour, minute)."""
    if not sys.executable:
        raise kernel.SpudError(kernel.EXIT_ERROR, "cannot tell which Python runs spud: sys.executable is empty")
    log = os.path.abspath(os.path.expanduser(SCHEDULE_LOG))
    return {
        "Label": launchagents.SCHEDULE_LABEL,
        "ProgramArguments": [sys.executable, "-I", "-S", str(ctx.launcher), "--as", "spud", "backup", "--daily"],
        "EnvironmentVariables": {"SPUD_HOME": str(ctx.home)},
        "RunAtLoad": True,
        "StartCalendarInterval": {"Hour": at[0], "Minute": at[1]},
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "ProcessType": "Background",
    }


def render_plist(ctx):
    """The watcher's LaunchAgent:`spud --as spud render --watch` under the tool's bin/spud with SPUD_HOME set,
    started at load and restarted by launchd whenever it exits (KeepAlive), its output in <home>/.spud/logs/render.log."""
    if not sys.executable:
        raise kernel.SpudError(kernel.EXIT_ERROR, "cannot tell which Python runs spud: sys.executable is empty")
    log = str(ctx.home / RENDER_LOG)
    return {
        "Label": launchagents.RENDER_LABEL,
        "ProgramArguments": [sys.executable, "-I", "-S", str(ctx.launcher), "--as", "spud", "render", "--watch"],
        "EnvironmentVariables": {"SPUD_HOME": str(ctx.home)},
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "ProcessType": "Background",
    }


def agent_plists(ctx, at):
    """Both agents' plists, backup first, as (agent, plist)."""
    return (("backup", schedule_plist(ctx, at)), ("render", render_plist(ctx)))


def launchctl(*args):
    """Run launchctl ($SPUD_LAUNCHCTL, default /bin/launchctl): (exit code, stdout, stderr)."""
    exe = os.environ.get("SPUD_LAUNCHCTL") or "/bin/launchctl"
    try:
        proc = lazy.subprocess.run([exe, *args], capture_output=True, text=True, errors="replace", timeout=60)
    except (OSError, lazy.subprocess.TimeoutExpired) as e:
        raise kernel.SpudError(kernel.EXIT_ERROR, "cannot run %s %s: %s" % (exe, " ".join(args), e))
    return proc.returncode, proc.stdout, proc.stderr


def write_plist(path, data):
    """Write through a temporary file in the same directory, then os.replace; mode 0644."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = lazy.tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def plist_state(path, plist):
    """(exists, matches, loaded) for one agent's plist on disk and its job in gui/<uid>."""
    import plistlib  # see cmd_schedule_show

    exists = path.is_file()
    matches = False
    if exists:
        try:
            matches = plistlib.loads(path.read_bytes()) == plist
        except Exception:  # unreadable, or not a plist: it does not match
            matches = False
    loaded = launchctl("print", "gui/%d/%s" % (os.getuid(), plist["Label"]))[0] == 0
    return exists, matches, loaded


def bootstrap_agent(path, label):
    """launchctl bootout (a job that was not loaded is fine), then bootstrap with retries:
    (booted_out, attempts, code, stdout, stderr)."""
    domain = "gui/%d" % os.getuid()
    booted_out = launchctl("bootout", "%s/%s" % (domain, label))[0] == 0
    attempts, code, stdout, stderr = 0, None, "", ""
    for delay in (0,) + BOOTSTRAP_RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        attempts += 1
        code, stdout, stderr = launchctl("bootstrap", domain, str(path))
        if code == 0:
            break
    return booted_out, attempts, code, stdout, stderr


def install_agents(ctx, at):
    """Write both plists atomically and (re)load both jobs, backup first; home move runs this too.  Returns one
    record per agent: label, path, replaced, booted_out, attempts."""
    import plistlib  # see cmd_schedule_show

    if (ctx.home / ".spud").is_dir():
        (ctx.home / RENDER_LOG).parent.mkdir(parents=True, exist_ok=True)  # launchd opens the log itself; its directory must exist
    out = []
    for agent, plist in agent_plists(ctx, at):
        path = launchagents.agent_plist_path(agent)
        replaced = os.path.lexists(path)
        try:
            write_plist(path, plistlib.dumps(plist))
        except OSError as e:
            raise kernel.SpudError(kernel.EXIT_ERROR, "cannot write %s: %s" % (path, e))
        booted_out, attempts, code, stdout, stderr = bootstrap_agent(path, plist["Label"])
        record = {"label": plist["Label"], "path": str(path), "replaced": replaced, "booted_out": booted_out, "attempts": attempts}
        if code != 0:
            said = (stderr or stdout).strip() or "no output"
            raise kernel.SpudError(kernel.EXIT_ERROR, "launchctl bootstrap gui/%d %s failed %d times, the last with exit %d: %s; the plist stays at %s (spud schedule uninstall removes it)"
                                   % (os.getuid(), path, attempts, code, said, path), data=dict(record, at="%02d:%02d" % at, stderr=stderr))
        out.append(record)
    return out


def cmd_schedule_show(ctx, args):
    require_spud_flag(args, "spud schedule show")
    import plistlib  # imported here, not at the top: the hooks run on every tool call and never need it

    at = "%02d:%02d" % args.at
    blocks, lines = {}, []
    for agent, plist in agent_plists(ctx, args.at):
        path = launchagents.agent_plist_path(agent)
        exists, matches, loaded = plist_state(path, plist)
        xml = plistlib.dumps(plist).decode("utf-8")
        blocks[agent] = {"label": plist["Label"], "path": str(path), "exists": exists, "matches": matches, "loaded": loaded, "plist": xml}
        service = "gui/%d/%s" % (os.getuid(), plist["Label"])
        state = ("installed, matches" if matches else "installed, differs from the plist below") if exists else "not installed"
        lines += [
            "label       %s" % plist["Label"],
            "plist       %s (%s)" % (path, state),
            "loaded      %s (launchctl print %s)" % ("yes" if loaded else "no", service),
            "runs        %s" % shlex.join(plist["ProgramArguments"]),
            ("when        at load, and daily at %s (a run missed during sleep fires at wake)" % at) if agent == "backup"
            else "when        at load, and again whenever it exits (KeepAlive)",
            "log         %s" % plist["StandardOutPath"],
            "",
            xml.rstrip("\n"),
            "",
        ]
    data = dict(blocks["backup"], at=at, render=blocks["render"])
    return kernel.Result(data, "\n".join(lines).rstrip("\n"))


def cmd_schedule_install(ctx, args):
    require_spud_flag(args, "spud schedule install")
    at = "%02d:%02d" % args.at
    records = install_agents(ctx, args.at)
    data = dict(records[0], at=at, render=records[1])
    domain = "gui/%d" % os.getuid()
    lines = []
    for record in records:
        lines += [
            "wrote %s%s" % (record["path"], " (replacing the plist that was there)" if record["replaced"] else ""),
            "launchctl bootout %s/%s: %s" % (domain, record["label"], "booted out the loaded job" if record["booted_out"] else "no job was loaded"),
            "launchctl bootstrap %s %s: loaded%s" % (domain, record["path"], "" if record["attempts"] == 1 else " on attempt %d" % record["attempts"]),
        ]
    lines.append("%s runs `spud backup --daily` at load and daily at %s; its output goes to %s" % (launchagents.SCHEDULE_LABEL, at, os.path.abspath(os.path.expanduser(SCHEDULE_LOG))))
    lines.append("%s runs `spud render --watch` at load and again whenever it exits; its output goes to %s" % (launchagents.RENDER_LABEL, ctx.home / RENDER_LOG))
    return kernel.Result(data, "\n".join(lines), stderr=settings_sync.tool_warning(ctx) or "")


def cmd_schedule_uninstall(ctx, args):
    require_spud_flag(args, "spud schedule uninstall")
    records, lines = {}, []
    for agent, label in launchagents.LABELS.items():
        path = launchagents.agent_plist_path(agent)
        service = "gui/%d/%s" % (os.getuid(), label)
        booted_out = launchctl("bootout", service)[0] == 0  # a job that was not loaded is fine
        removed = False
        if os.path.lexists(path):
            try:
                path.unlink()
            except OSError as e:
                raise kernel.SpudError(kernel.EXIT_ERROR, "cannot remove %s: %s" % (path, e), data={"label": label, "path": str(path), "booted_out": booted_out, "removed": False})
            removed = True
        records[agent] = {"label": label, "path": str(path), "booted_out": booted_out, "removed": removed}
        if not booted_out and not removed:
            lines.append("%s is not installed: no plist at %s and no job loaded (launchctl bootout %s); nothing to do" % (label, path, service))
        else:
            lines.append("launchctl bootout %s: %s\n%s" % (service, "booted out the loaded job" if booted_out else "no job was loaded", ("removed %s" % path) if removed else ("no plist at %s" % path)))
    return kernel.Result(dict(records["backup"], render=records["render"]), "\n".join(lines))
