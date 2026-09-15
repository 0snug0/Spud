"""Hook-run and command medians for one or more launchers, interleaved (SPD-065).

  python3.14 -I -S tests/probes/hook_timing.py [ROUNDS] LAUNCHER [LAUNCHER ...]

Each launcher gets its own scratch SPUD_HOME (a copy of spud.config.json and `spud init`), so nothing is written into
any ledger.  Three warm-up rounds fill each home's bytecode and git-command caches; then every round runs every case
once per launcher, in turn, so a machine slowing down slows every launcher alike.  Prints, per case, each launcher's
median, min and max in ms, and the difference of each median from the first launcher's.  The pass for a change to the
hook path: every hook case within 1 ms of main's median in the same run.
"""

import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(os.path.dirname(os.path.dirname(HERE)), "spud.config.json")
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


def setup(launcher):
    home = tempfile.mkdtemp(prefix="spud-hook-timing-")
    shutil.copy(CONFIG, home)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR")}
    env.update(SPUD_HOME=home, SPUD_USER_CLAUDE_DIR=os.path.join(home, ".user-claude"), SPUD_CONFIG_DIR=os.path.join(home, ".user-config"))
    subprocess.run([PY, "-I", "-S", launcher, "init"], env=env, check=True, capture_output=True)
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
