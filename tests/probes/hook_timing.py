"""Hook-run and command medians for one or more launchers, interleaved (SPD-065).

  python3.14 -I -S tests/probes/hook_timing.py [ROUNDS] [--members N] LAUNCHER [LAUNCHER ...]

Each launcher gets its own scratch SPUD_HOME (the shipped share/spud.config.json, rendered) and, beside it, a tool checkout
of its own (tests/probes/probe_env.py's build_tool), which `spud init --no-schedule --project-root` registers as project 1:
the shape a real home has (SPD-244).  Under probe_env's isolation, so nothing is written into any ledger, ~/.claude,
~/.config/spud or ~/Library/LaunchAgents, and launchctl is never run.  Three warm-up rounds fill each home's bytecode and git-command caches; then every round runs every case
once per launcher, in turn, so a machine slowing down slows every launcher alike.  Prints, per case, each launcher's
median, min and max in ms, and the difference of each median from the first launcher's.  The pass for a change to the
hook path: every hook case within 1 ms of main's median in the same run.

`--members N` (SPD-332) seeds each home, after init, with N finished runs of one ticket in the real ledger's row shape: a
brief, a result and a return text of its average sizes, which a scan of usage_json walks past, and a transcript sum whose
breakdown names only models the shipped price table prices.  An empty ledger hides what a hook pays per member.
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

    Each launcher's tool is built from *that* launcher's checkout, its `share/` included (SPD-157).  One share/ for every
    launcher -- this probe's own checkout's, as it was -- breaks the moment a branch adds a `{{mark}}`: the branch's
    shipped files carry a mark main's `core/shipped.MARKS` does not name, main's `spud init` refuses to write a file it
    cannot render, and the run ends in a traceback instead of a comparison.  Nothing on the hook path reads share/ at
    all, so this changes what `setup` builds and nothing that is measured.
    """
    return os.path.dirname(os.path.dirname(launcher))


SEED_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5", "claude-fable-5-1", "claude-haiku-4-5-20251001")


def seed_usage(model):
    """A transcript sum in the shape the SubagentStop hook stores, two breakdown entries on one model."""
    entry = {"model": model, "service_tier": "standard", "inference_geo": "not_available", "requests": 8, "input_tokens": 16,
             "output_tokens": 107, "cache_read_input_tokens": 634321, "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 105188}}
    return json.dumps({"source": "transcript", "counting": "request", "messages": 9, "usage": {
        "input_tokens": 18, "output_tokens": 295, "cache_creation_input_tokens": 106379, "cache_read_input_tokens": 739509},
        "breakdown": [entry, dict(entry, speed="standard", server_tool_use={"web_search_requests": 0, "web_fetch_requests": 0})]})


def seed(launcher, home, env, members):
    """`members` finished runs of one ticket, written straight into this scratch home's ledger (the CLI would take minutes)."""
    proc = subprocess.run([PY, "-I", "-S", launcher, "--json", "--as", "spud", "ticket", "new", "--title", "Seeded", "--status", "active",
                           "--brief", "b", "--sizing", "s"], env=env, check=True, capture_output=True, text=True)
    key = json.loads(proc.stdout)["ticket"]["key"]
    con = sqlite3.connect(os.path.join(home, ".spud", "ledger.db"))
    try:
        with con:
            ticket = con.execute("SELECT id FROM tickets WHERE key = ?", (key,)).fetchone()[0]
            con.executemany(
                "INSERT INTO members (ticket_id, lineage, depth, name, persona, model, status, brief, result, outcome, summary, planned_at,"
                " finished_at, return_text, total_tokens, usage_json) VALUES (?, ?, 1, ?, 'engineer', 'opus', 'done', ?, ?, ?, ?,"
                " '2026-09-28T09:00:00-07:00', '2026-09-28T10:00:00-07:00', ?, 846201, ?)",
                [(ticket, "%04d" % n, "Seed%04d" % n, "b" * 3700, "r" * 2700, "o" * 500, "s" * 270, "t" * 1800, seed_usage(SEED_MODELS[n % len(SEED_MODELS)]))
                 for n in range(1, members + 1)])
    finally:
        con.close()


def setup(launcher, members=0):
    """A scratch directory holding the home and, beside it, the tool (probe_env.build_tool), with project 1 that tool as
    init registers it (SPD-244): every launcher's home the same shape, a real home's, so each measures the same work.
    Returns (the scratch directory, the home, the environment)."""
    root = tempfile.mkdtemp(prefix="spud-hook-timing-")
    home = os.path.join(root, "home")
    os.mkdir(home)
    # The config template, bin/ and share/ are each launcher's own (`checkout_of`); only the marks are this probe's.
    probe_env.write_config(home, os.path.join(checkout_of(launcher), "share", "spud.config.json"))
    tool = probe_env.build_tool(root, checkout_of(launcher))
    # SPD-101: helpers.Home's isolation, from the one helper every probe shares.  SPUD_TOOL_DIR is the scratch tool, a main
    # checkout, since init refuses a bin/spud in a linked worktree -- the second launcher here is exactly that.  Its
    # LaunchAgents directory and launchctl are the scratch's, and init skips step 8: before this, this very line
    # installed a scratch render watcher over this Mac's.
    env = probe_env.isolated_env(home, tool)
    subprocess.run([PY, "-I", "-S", launcher, *probe_env.init_args(tool)], env=env, check=True, capture_output=True)
    if members:
        seed(launcher, home, env, members)
    return root, home, env


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
    members = 0
    if "--members" in args:
        at = args.index("--members")
        members = int(args[at + 1])
        del args[at:at + 2]
    launchers = [os.path.abspath(a) for a in args]
    if not launchers:
        sys.exit(__doc__)
    homes = [setup(launcher, members) for launcher in launchers]
    times = {}
    try:
        for rnd in range(3 + rounds):
            for launcher, (_, home, env) in zip(launchers, homes):
                for name, argv, payload in cases(home):
                    command = [PY, "-I", "-S", "-c", "pass"] if argv is None else [PY, "-I", "-S", launcher, *argv]
                    ms = once(command, json.dumps(payload) if payload else None, env)
                    if rnd >= 3:
                        times.setdefault((name, launcher), []).append(ms)
    finally:
        for root, _, _ in homes:
            shutil.rmtree(root, ignore_errors=True)
    print("%d rounds, launchers interleaved; ms" % rounds)
    for name, _, _ in cases("/tmp"):
        first = statistics.median(times[(name, launchers[0])])
        for launcher in launchers:
            v = times[(name, launcher)]
            med = statistics.median(v)
            print("%-22s %-60s median %6.2f  min %6.2f  max %6.2f  vs first %+6.2f" % (name, launcher[-60:], med, min(v), max(v), med - first))


if __name__ == "__main__":
    main()
