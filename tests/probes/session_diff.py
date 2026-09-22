"""A scripted session of CLI and hook calls against two launchers, compared step by step (SPD-065).

  python3.14 -I -S tests/probes/session_diff.py LAUNCHER_A LAUNCHER_B

Each launcher gets its own scratch SPUD_HOME (the shipped share/spud.config.json, rendered), so nothing is
written into any ledger.  The same 44 steps run against each: commands from init to member finish, every hook event with a real payload, and two
malformed payloads.  Each step's exit code, stdout and stderr are compared after masking the scratch home, the
launcher's checkout, timestamps, dates, clock times and durations.  Prints how many steps are identical and a diff of
each that is not, and exits 1 when any differs.  A change meant to leave behaviour alone, such as a refactor of the
program, passes with every step identical against main's launcher.
"""

import difflib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
# The config a scratch home starts from: the template the tool ships for a real home, rendered with the suite's marks --
# the pair tests/helpers.py builds every Home from (SPW-001, which deleted the suite's own copy of the config).
CONFIG = os.path.join(os.path.dirname(os.path.dirname(HERE)), "share", "spud.config.json")
CONFIG_MARKS = os.path.join(os.path.dirname(HERE), "fixtures", "config_marks.json")
PY = sys.executable
AGENT = "a0123456789abcdef"
SESSION = "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b"


def write_config(home):
    """Render the shipped config template into `home`/spud.config.json; returns it parsed."""
    with open(CONFIG, encoding="utf-8") as f:
        text = f.read()
    with open(CONFIG_MARKS, encoding="utf-8") as f:
        for mark, value in json.load(f).items():
            text = text.replace(mark, value)
    with open(os.path.join(home, "spud.config.json"), "w", encoding="utf-8") as f:
        f.write(text)
    return json.loads(text)


def seed_project_one(home, config, checkout):
    """Project 1 as `spud init` inserted it before SPW-001: key `spud`, the identity's name, rooted at the launcher's own
    checkout, the config's two prefixes, `remote` from that checkout's origin.

    Run right after the script's `init` step, because init registers no project now (docs/design/2026-09-21-spud-init.md
    section 1.4) and every step below it needs one -- and because a home *with* a row 1 is the shape of the two homes
    that exist, which is what this probe is evidence about: their `config sync`, `doctor`, `project edit` and
    `project remove` must answer exactly as main's launcher answers."""
    proc = subprocess.run(["git", "-C", checkout, "remote", "get-url", "origin"], capture_output=True, text=True)
    con = sqlite3.connect(os.path.join(home, ".spud", "ledger.db"), timeout=5)
    try:
        with con:
            con.execute(
                "INSERT INTO projects (id, key, name, root_path, remote, ticket_prefix, team_prefix, created_at)"
                " VALUES (1, 'spud', ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (config["identity"]["name"], checkout, (proc.stdout.strip() or None) if proc.returncode == 0 else None,
                 config["tickets"]["prefix"], config["teams"]["prefix"], datetime.now().astimezone().isoformat(timespec="seconds")),
            )
    finally:
        con.close()


def script(home):
    base = {"session_id": SESSION, "transcript_path": "/tmp/x.jsonl", "cwd": home, "permission_mode": "default"}
    pre = dict(base, hook_event_name="PreToolUse")
    return [
        (["--version"], None), (["--help"], None), (["no-such-command"], None), (["init"], None), (["board"], None),
        (["--as", "spud", "ticket", "new", "--title", "Split", "--priority", "P1", "--status", "active", "--brief", "b", "--sizing", "s"], None),
        (["--as", "spud", "ticket", "move", "SPD-001", "--status", "done"], None),
        (["--as", "spud", "ticket", "new", "--title", "Two", "--priority", "P2", "--status", "active", "--brief", "b", "--sizing", "s"], None),
        (["ticket", "show", "SPD-002"], None),
        (["--as", "spud", "member", "new", "--ticket", "SPD-002", "--persona", "engineer", "--model", "opus", "--brief", "do it", "--deliverable", "bin/**", "--name", "Russet"], None),
        (["--as", "spud", "member", "new", "--ticket", "SPD-002", "--persona", "scout", "--model", "opus", "--brief", "x", "--deliverable", "docs/**", "--name", "Yukon"], None),
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
        (["hook", "PostToolUse"], dict(base, hook_event_name="PostToolUse", tool_name="Agent", tool_use_id="t7", tool_input={"description": "SPUD-002/Russet (01, engineer)"}, tool_response={"agentId": AGENT, "status": "async_launched"})),
        (["hook", "SubagentStart"], dict(base, hook_event_name="SubagentStart", agent_id=AGENT, agent_type="spudagent")),
        (["hook", "PreToolUse"], dict(pre, tool_name="Edit", tool_use_id="t8", tool_input={"file_path": home + "/bin/x.py", "old_string": "a", "new_string": "b"}, agent_id=AGENT)),
        (["hook", "SubagentStop"], dict(base, hook_event_name="SubagentStop", agent_id=AGENT, agent_type="spudagent", stop_hook_active=False, last_assistant_message="done")),
        (["hook", "Stop"], dict(base, hook_event_name="Stop", stop_hook_active=False, last_assistant_message="Done.")),
        (["hook", "UserPromptSubmit"], dict(base, hook_event_name="UserPromptSubmit", prompt="Work on SPD-002")),
        (["hook", "PreToolUse"], "not json"), (["hook", "Stop"], "[]"),
        (["--as", AGENT, "member", "log", "progress"], None), (["--as", AGENT, "member", "result", "done"], None),
        (["--as", "spud", "member", "finish", "SPUD-002/Russet", "--status", "done", "--outcome", "ok", "--summary", "x" * 160], None),
        (["board", "--brief"], None), (["events", "--limit", "40"], None),
    ]


def run(launcher):
    home = tempfile.mkdtemp(prefix="spud-session-")
    config = write_config(home)
    checkout = os.path.dirname(os.path.dirname(launcher))
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR", "SPUD_HOME")}
    env.update(SPUD_HOME=home, SPUD_USER_CLAUDE_DIR=home + "/.user-claude", SPUD_CONFIG_DIR=home + "/.user-config")
    # SPW-001: this home plays the tool, as it does for tests/helpers.py and tests/probes/hook_timing.py, for the two
    # reasons the `init` step below now has: init refuses to build a home when the running bin/spud is in a linked
    # worktree (design section 6, refusal 6), and the second launcher this probe is run with is exactly that; and it
    # writes the vault scaffolding from the tool's share/, so the scratch tool must ship it.  Both launchers alike.
    env["SPUD_TOOL_DIR"] = home
    os.symlink(os.path.join(os.path.dirname(os.path.dirname(HERE)), "share"), os.path.join(home, "share"), target_is_directory=True)
    out = []
    try:
        for argv, stdin in script(home):
            text = stdin if isinstance(stdin, str) else (json.dumps(stdin) if stdin is not None else None)
            # cwd is the scratch home, so a command that reads the working directory answers alike wherever this runs
            p = subprocess.run([PY, "-I", "-S", launcher, *argv], input=text, capture_output=True, text=True, env=env, cwd=home)
            if argv == ["init"]:
                seed_project_one(home, config, checkout)
            blob = "$ %s\nexit %d\n%s\n--stderr--\n%s" % (" ".join(argv[:4]), p.returncode, p.stdout, p.stderr)
            blob = blob.replace(home, "<HOME>").replace(os.path.realpath(home), "<HOME>")
            blob = blob.replace(checkout, "<CHECKOUT>")
            blob = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[-+]\d{2}:\d{2}", "<T>", blob)
            blob = re.sub(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?", "<D>", blob)
            blob = re.sub(r"\b\d{2}:\d{2}\b", "<HM>", blob)
            blob = re.sub(r"\d+(\.\d+)? ?ms\b", "<MS>", blob)
            out.append(blob)
    finally:
        shutil.rmtree(home, ignore_errors=True)
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
