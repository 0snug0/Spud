"""A scripted session of CLI and hook calls against two launchers, compared step by step (SPD-065).

  python3.14 -I -S tests/probes/session_diff.py LAUNCHER_A LAUNCHER_B

Each launcher gets its own scratch SPUD_HOME (the shipped share/spud.config.json, rendered) and, beside it, a tool
checkout of its own (tests/probes/probe_env.py's build_tool), which the script's `init --project-root` registers as project
1: the shape a real home has (SPD-244), and a linked worktree of that tool, `<tool>/.claude/worktrees/spd-002-probe`,
which the members are planned from, so the ticket binds there and the spawn, subagent and member steps meet a planned
row as a real session's do (SPD-251).  Nothing is written into any real ledger.  The same steps run against each:
commands from init to member finish, every hook event with a real payload, and two malformed payloads.  Each step's exit
code, stdout and stderr are compared after masking the scratch home, its tool (the worktree under it with it),
timestamps, dates, clock times and durations.  Prints how many steps are identical and a diff of
each that is not, and exits 1 when any differs.  A change meant to leave behaviour alone, such as a refactor of the
program, passes with every step identical against main's launcher.
"""

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True  # probe_env comes from this directory, and the checkout keeps no bytecode
sys.path.insert(0, HERE)  # `python3.14 -I -S` puts no script directory on sys.path
import probe_env  # noqa: E402  SPD-101: the isolation every probe's scratch home runs under

PY = sys.executable
AGENT = "a0123456789abcdef"
SESSION = "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b"
WORKTREE = "spd-002-probe"  # the linked worktree of the tool the members are planned from (SPD-251)


def script(home, tool, worktree):
    """The steps, in order: (argv, stdin) run in the home, or (argv, stdin, cwd).  The fourth is init itself, registering
    `tool` as project 1 as a person's first init does (probe_env.init_args, SPD-244), so every step after it runs against
    a home with a row 1: the shape of every real home, which is what this probe is evidence about.

    SPD-251: the first `member new` runs in the home and is refused (exit 5: bin/** is in project 1's checkout); the two
    after it run in `worktree`, a linked worktree of the tool, so SPD-002 binds there and Russet and Yukon are planned.
    Every Agent, PostToolUse, SubagentStart, SubagentStop and member step after them meets a planned row, as a real
    session's do; the `Nobody` spawn keeps the no-row path."""
    base = {"session_id": SESSION, "transcript_path": "/tmp/x.jsonl", "cwd": home, "permission_mode": "default"}
    pre = dict(base, hook_event_name="PreToolUse")
    return [
        (["--version"], None), (["--help"], None), (["no-such-command"], None), (probe_env.init_args(tool), None), (["board"], None),
        (["--as", "spud", "ticket", "new", "--title", "Split", "--priority", "P1", "--status", "active", "--brief", "b", "--sizing", "s"], None),
        (["--as", "spud", "ticket", "move", "SPD-001", "--status", "done"], None),
        (["--as", "spud", "ticket", "new", "--title", "Two", "--priority", "P2", "--status", "active", "--brief", "b", "--sizing", "s"], None),
        (["ticket", "show", "SPD-002"], None),
        (["--as", "spud", "member", "new", "--ticket", "SPD-002", "--persona", "engineer", "--model", "opus", "--brief", "do it", "--deliverable", "bin/**", "--name", "Russet"], None),
        (["--as", "spud", "member", "new", "--ticket", "SPD-002", "--persona", "engineer", "--model", "opus", "--brief", "do it", "--deliverable", "bin/**", "--name", "Russet"], None, worktree),
        (["--as", "spud", "member", "new", "--ticket", "SPD-002", "--persona", "scout", "--model", "opus", "--brief", "x", "--deliverable", "docs/**", "--name", "Yukon", "--tier-reason", "probe"], None, worktree),
        (["--json", "card", "SPD-002"], None), (["render"], None), (["events", "--limit", "5"], None),
        (["sql", "--readonly", "select count(*) from members"], None), (["settings", "sync", "--dry-run"], None),
        (["project", "list"], None), (["session", "show"], None), (["--as", "spud", "schedule", "show"], None), (["doctor"], None),
        (["member", "resum", "--all"], None), (["fleet"], None), (["member", "show", "SPUD-002/Russet"], None),
        (["hook", "SessionStart"], dict(base, hook_event_name="SessionStart", source="startup")),
        (["hook", "PreToolUse"], dict(pre, tool_name="Bash", tool_use_id="t1", tool_input={"command": "git status"})),
        (["hook", "PreToolUse"], dict(pre, tool_name="Bash", tool_use_id="t2", tool_input={"command": "cat .spud/ledger.db"})),
        (["hook", "PreToolUse"], dict(pre, tool_name="Bash", tool_use_id="t3", tool_input={"command": "git push"}, agent_id=AGENT)),
        (["hook", "PreToolUse"], dict(pre, tool_name="Bash", tool_use_id="t4", tool_input={"command": "echo x > ledger/tickets/SPD-001.md"})),
        (["hook", "PreToolUse"], dict(pre, tool_name="Write", tool_use_id="t5", tool_input={"file_path": home + "/ledger/x.md", "content": "x"})),
        (["hook", "PreToolUse"], dict(pre, tool_name="Agent", tool_use_id="t6", tool_input={"description": "SPUD-002/Nobody (09, engineer)", "subagent_type": "spudagent", "model": "opus", "prompt": "p"})),
        (["hook", "PreToolUse"], dict(pre, tool_name="Agent", tool_use_id="t7", tool_input={"description": "SPUD-002/Russet (01, engineer)", "subagent_type": "spudagent", "model": "opus", "prompt": "p", "run_in_background": True})),
        # SPD-251: Russet is planned at the engineer's effort, so the spawn above is refused (Law 3) and this one is allowed
        (["hook", "PreToolUse"], dict(pre, tool_name="Agent", tool_use_id="t9", tool_input={"description": "SPUD-002/Russet (01, engineer)", "subagent_type": "spudagent-high", "model": "opus", "prompt": "p", "run_in_background": True})),
        (["hook", "PostToolUse"], dict(base, hook_event_name="PostToolUse", tool_name="Agent", tool_use_id="t9", tool_input={"description": "SPUD-002/Russet (01, engineer)"}, tool_response={"agentId": AGENT, "status": "async_launched"})),
        (["hook", "SubagentStart"], dict(base, hook_event_name="SubagentStart", agent_id=AGENT, agent_type="spudagent-high")),
        (["hook", "PreToolUse"], dict(pre, tool_name="Edit", tool_use_id="t8", tool_input={"file_path": home + "/bin/x.py", "old_string": "a", "new_string": "b"}, agent_id=AGENT)),
        (["hook", "PreToolUse"], dict(pre, tool_name="Edit", tool_use_id="t10", tool_input={"file_path": worktree + "/bin/x.py", "old_string": "a", "new_string": "b"}, agent_id=AGENT)),
        (["hook", "PreToolUse"], dict(pre, tool_name="Edit", tool_use_id="t11", tool_input={"file_path": tool + "/bin/x.py", "old_string": "a", "new_string": "b"}, agent_id=AGENT)),
        (["hook", "SubagentStop"], dict(base, hook_event_name="SubagentStop", agent_id=AGENT, agent_type="spudagent-high", stop_hook_active=False, last_assistant_message="done")),
        (["hook", "Stop"], dict(base, hook_event_name="Stop", stop_hook_active=False, last_assistant_message="Done.")),
        (["hook", "UserPromptSubmit"], dict(base, hook_event_name="UserPromptSubmit", prompt="Work on SPD-002")),
        (["hook", "PreToolUse"], "not json"), (["hook", "Stop"], "[]"),
        (["--as", AGENT, "member", "log", "progress"], None), (["--as", AGENT, "member", "result", "done"], None),
        (["hook", "SubagentStop"], dict(base, hook_event_name="SubagentStop", agent_id=AGENT, agent_type="spudagent-high", stop_hook_active=False, last_assistant_message="done")),
        (["--as", "spud", "member", "finish", "SPUD-002/Russet", "--status", "done", "--outcome", "ok", "--summary", "x" * 160], None),
        (["board", "--brief"], None), (["events", "--limit", "40"], None),
    ]


def run(launcher):
    root = tempfile.mkdtemp(prefix="spud-session-")
    home = os.path.join(root, "home")
    os.mkdir(home)
    probe_env.write_config(home, name_pool=True)  # SPD-157: the suite's own pool, for the names the script spells
    # Project 1's root (SPD-244): a tool checkout beside the home, one per launcher, built alike from this checkout, so both
    # launchers read a row of the same shape at a path of the same length, masked below.
    tool = str(probe_env.build_tool(root))
    # SPD-251: a linked worktree of it, as EnterWorktree makes one; under the tool, so the tool's mark masks its path too,
    # and its branch, worktree-<WORKTREE>, is the same for both launchers.
    worktree = str(probe_env.add_worktree(tool, WORKTREE))
    # SPD-101: helpers.Home's isolation, from the one helper every probe shares.  SPUD_TOOL_DIR is the scratch tool, a
    # main checkout, since init refuses a bin/spud in a linked worktree and the second launcher here is exactly that.
    # SPUD_LAUNCH_AGENTS_DIR under the scratch is also what lets the two launchers answer alike at all: doctor and
    # `board --brief` read the watcher's plist, and ~/Library/LaunchAgents names main's own launcher -- SPD-101's evidence,
    # 41 of 44 identical without it.  SPUD_VAULT_DOWNLOADS is off (SPD-156), so this probe answers the same offline as
    # online; what the real lock downloads is tests/probes/vault_download.py's.
    env = probe_env.isolated_env(home, tool)
    out = []
    try:
        for argv, stdin, *where in script(home, tool, worktree):
            text = stdin if isinstance(stdin, str) else (json.dumps(stdin) if stdin is not None else None)
            # cwd is the scratch home, or the step's own directory under the scratch, so a command that reads the working
            # directory answers alike wherever this runs
            p = subprocess.run([PY, "-I", "-S", launcher, *argv], input=text, capture_output=True, text=True, env=env, cwd=where[0] if where else home)
            blob = "$ %s\nexit %d\n%s\n--stderr--\n%s" % (" ".join(argv[:4]), p.returncode, p.stdout, p.stderr)
            # The real path first: /private/var/... holds /var/... whole.  The home and its tool before the root holding both.
            for path, mark in ((home, "<HOME>"), (tool, "<TOOL>"), (root, "<ROOT>")):
                blob = blob.replace(os.path.realpath(path), mark).replace(path, mark)
            blob = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[-+]\d{2}:\d{2}", "<T>", blob)
            blob = re.sub(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?", "<D>", blob)
            blob = re.sub(r"\b\d{2}:\d{2}\b", "<HM>", blob)
            blob = re.sub(r"\d+(\.\d+)? ?ms\b", "<MS>", blob)
            out.append(blob)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return out


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    a, b = (run(os.path.abspath(launcher)) for launcher in sys.argv[1:3])
    same = sum(1 for x, y in zip(a, b) if x == y)
    print("steps %d, identical after masking %d" % (len(a), same))
    for x, y in zip(a, b):
        if x != y:
            print("".join(list(difflib.unified_diff(x.splitlines(True), y.splitlines(True), "A", "B", n=1))[:40]))
    return 0 if same == len(a) == len(b) else 1


if __name__ == "__main__":
    sys.exit(main())
