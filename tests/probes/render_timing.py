"""A full pass and a no-change pass of `spud render` timed over a synthetic ledger (SPD-097).

  python3.14 -I -S tests/probes/render_timing.py [TICKETS] [MEMBERS_PER_TICKET] [LAUNCHER]

Builds a scratch SPUD_HOME with TICKETS tickets (default 60) of MEMBERS_PER_TICKET members each (default 3), a log line
and a result per member, renders it once (the full pass: every file written) and again (the no-change pass: nothing
written, nothing recorded), and prints both times in milliseconds with the file count.  Nothing outside the scratch home
is touched.  The real ledger's numbers are Spud's to take: two runs of `spud render --out <scratch>`, the second a
no-change pass, recorded on SPD-097.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONFIG = os.path.join(ROOT, "tests", "fixtures", "spud.config.json")  # the suite's fixture: the tool keeps no home config
PY = sys.executable


def run(launcher, env, *args):
    proc = subprocess.run([PY, "-I", "-S", launcher, "--json", *args], env=env, capture_output=True, text=True)
    if proc.returncode:
        sys.exit("%s: exit %d %s" % (" ".join(args), proc.returncode, proc.stderr))
    return json.loads(proc.stdout)


def main():
    args = sys.argv[1:]
    tickets = int(args.pop(0)) if args and args[0].isdigit() else 60
    per_ticket = int(args.pop(0)) if args and args[0].isdigit() else 3
    launcher = os.path.abspath(args[0]) if args else os.path.join(ROOT, "bin", "spud")
    home = tempfile.mkdtemp(prefix="spud-render-timing-")
    shutil.copy(CONFIG, home)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR")}
    env.update(SPUD_HOME=home, SPUD_TOOL_DIR=home, SPUD_USER_CLAUDE_DIR=os.path.join(home, ".user-claude"), SPUD_CONFIG_DIR=os.path.join(home, ".user-config"))
    try:
        run(launcher, env, "init")
        for n in range(tickets):
            t = run(launcher, env, "--as", "spud", "ticket", "new", "--title", "Ticket %d" % n, "--status", "active", "--brief", "b", "--sizing", "s")["ticket"]
            for _ in range(per_ticket):
                m = run(launcher, env, "--as", "spud", "member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", "--deliverable", "docs/**")["member"]
                run(launcher, env, "--as", "spud", "member", "start", m["ref"])  # no harness here to bind and activate it
                run(launcher, env, "--as", m["ref"], "member", "log", "started")
                run(launcher, env, "--as", m["ref"], "member", "result", "done")
                # Finished, not left alive: members alive at once are capped by limits.max_concurrent_total, and a
                # synthetic ledger of any size would stop at the ninth.
                run(launcher, env, "--as", "spud", "member", "finish", m["ref"], "--status", "done", "--outcome", "done")
        started = time.perf_counter()
        full = run(launcher, env, "--as", "spud", "render")
        full_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        again = run(launcher, env, "--as", "spud", "render")
        again_ms = (time.perf_counter() - started) * 1000
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("%d files: full pass %.0f ms (%d written), no-change pass %.0f ms (%d written, %d unchanged)"
          % (len(full["written"]), full_ms, len(full["written"]), again_ms, len(again["written"]), len(again["unchanged"])))


if __name__ == "__main__":
    main()
