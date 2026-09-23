"""Hook-run and command medians for one or more launchers, interleaved (SPD-065).

  python3.14 -I -S tests/probes/hook_timing.py [ROUNDS] LAUNCHER [LAUNCHER ...]

Each launcher gets its own scratch SPUD_HOME (the shipped share/spud.config.json, rendered, and `spud init --no-schedule`)
under tests/probes/probe_env.py's isolation, so nothing is written into any ledger, ~/.claude, ~/.config/spud or
~/Library/LaunchAgents, and launchctl is never run.  Three warm-up rounds fill each home's bytecode and git-command caches; then every round runs every case
once per launcher, in turn, so a machine slowing down slows every launcher alike.  Prints, per case, each launcher's
median, min and max in ms, and the difference of each median from the first launcher's.  The pass for a change to the
hook path: every hook case within 1 ms of main's median in the same run.
"""

import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True  # probe_env comes from this directory, and the checkout keeps no bytecode
sys.path.insert(0, HERE)  # `python3.14 -I -S` puts no script directory on sys.path
import probe_env  # noqa: E402  SPD-101: the isolation every probe's scratch home runs under

PY = sys.executable
SESSION = "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b"


def cases(home):
    base = {"session_id": SESSION, "transcript_path": "/tmp/x.jsonl", "cwd": home, "permission_mode": "default"}
    pre = dict(base, hook_event_name="PreToolUse")
    return [
        ("PreToolUse(Bash)", ["hook", "PreToolUse"], dict(pre, tool_name="Bash", tool_use_id="t1", tool_input={"command": "git status"})),
        ("PreToolUse(Write)", ["hook", "PreToolUse"], dict(pre, tool_name="Write", tool_use_id="t2", tool_input={"file_path": home + "/n.md", "content": "x"})),
        ("SessionStart", ["hook", "SessionStart"], dict(base, hook_event_name="SessionStart", source="startup")),
        ("Stop", ["hook", "Stop"], dict(base, hook_event_name="Stop", stop_hook_active=False)),
        ("board (a command)", ["board"], None),
        ("python -I -S -c pass", None, None),
    ]


def checkout_of(launcher):
    """The checkout a launcher belongs to, given `<checkout>/bin/spud`.

    Each scratch home plays the tool for its own launcher, so it ships *that* launcher's `share/` (SPD-157).  One
    share/ for every launcher -- this probe's own checkout's, as it was -- breaks the moment a branch adds a
    `{{mark}}`: the branch's shipped files carry a mark main's `core/shipped.MARKS` does not name, main's `spud init`
    refuses to write a file it cannot render, and the run ends in a traceback instead of a comparison.  Nothing on the
    hook path reads share/ at all, so this changes what `setup` builds and nothing that is measured.
    """
    return os.path.dirname(os.path.dirname(launcher))


def seed_project_one(home, config, checkout):
    """Project 1 as `spud init` inserted it before SPW-001: key `spud`, rooted at `checkout`, the
    config's prefixes.  Init registers no project now (docs/design/2026-09-21-spud-init.md section 1.4), and a home with
    no project is a home whose hooks read one row fewer -- so every launcher's home is seeded here, and each measures the
    same work as the one before it."""
    con = sqlite3.connect(os.path.join(home, ".spud", "ledger.db"), timeout=5)
    try:
        with con:
            con.execute(
                "INSERT INTO projects (id, key, name, root_path, ticket_prefix, team_prefix, created_at)"
                " VALUES (1, 'spud', ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (config["identity"]["name"], checkout, config["tickets"]["prefix"], config["teams"]["prefix"],
                 datetime.now().astimezone().isoformat(timespec="seconds")),
            )
    finally:
        con.close()


def setup(launcher):
    home = tempfile.mkdtemp(prefix="spud-hook-timing-")
    # The config template and the rest of share/ are each launcher's own (`checkout_of`); only the marks are this probe's.
    config = probe_env.write_config(home, os.path.join(checkout_of(launcher), "share", "spud.config.json"))
    # SPD-101: helpers.Home's isolation, from the one helper every probe shares.  The home plays the tool (SPUD_TOOL_DIR),
    # as SPW-001 made it, because init refuses a bin/spud in a linked worktree -- the second launcher here is exactly that
    # -- and writes the vault scaffolding from the tool's share/; project 1 below names the main checkout of the launcher's
    # repository, the same for every launcher, so each hook reads the same row and lists the same worktrees.  Its
    # LaunchAgents directory and launchctl are the scratch's, and init skips step 8: before this, this very line
    # installed a scratch render watcher over this Mac's.
    env = probe_env.isolated_env(home)
    probe_env.link_share(home, os.path.join(checkout_of(launcher), "share"))
    subprocess.run([PY, "-I", "-S", launcher, "init", "--no-schedule"], env=env, check=True, capture_output=True)
    seed_project_one(home, config, probe_env.main_checkout(launcher))
    return home, env


def once(argv, stdin, env):
    started = time.perf_counter()
    proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, env=env)
    elapsed = (time.perf_counter() - started) * 1000
    if proc.returncode:
        sys.exit("%s: exit %d %s" % (" ".join(argv), proc.returncode, proc.stderr))
    return elapsed


def main():
    args = sys.argv[1:]
    rounds = int(args.pop(0)) if args and args[0].isdigit() else 30
    launchers = [os.path.abspath(a) for a in args]
    if not launchers:
        sys.exit(__doc__)
    homes = [setup(launcher) for launcher in launchers]
    times = {}
    try:
        for rnd in range(3 + rounds):
            for launcher, (home, env) in zip(launchers, homes):
                for name, argv, payload in cases(home):
                    command = [PY, "-I", "-S", "-c", "pass"] if argv is None else [PY, "-I", "-S", launcher, *argv]
                    ms = once(command, json.dumps(payload) if payload else None, env)
                    if rnd >= 3:
                        times.setdefault((name, launcher), []).append(ms)
    finally:
        for home, _ in homes:
            shutil.rmtree(home, ignore_errors=True)
    print("%d rounds, launchers interleaved; ms" % rounds)
    for name, _, _ in cases("/tmp"):
        first = statistics.median(times[(name, launchers[0])])
        for launcher in launchers:
            v = times[(name, launcher)]
            med = statistics.median(v)
            print("%-22s %-60s median %6.2f  min %6.2f  max %6.2f  vs first %+6.2f" % (name, launcher[-60:], med, min(v), max(v), med - first))


if __name__ == "__main__":
    main()
