#!/opt/homebrew/bin/python3.14
"""Headless probes for the SPD-008 hooks (real `claude -p` sessions, real API usage).

Each scenario builds a scratch SPUD_HOME (a copy of bin/spud and spud.config.json, `spud init`,
a ticket, the planned members it needs), generates a settings file with `spud settings sync`
on top of a capture hook that appends every raw payload to hooks.jsonl, then runs one
`claude -p` session in that home with `--settings` and an `--agents` definition of
`spudagent`.  Afterwards it prints what the harness saw (Agent calls, tool errors, the final
result line) and what the ledger recorded (events, spawn_requests, member rows).

    python3.14 tests/probes/headless.py <scenario> [--root DIR] [--model haiku] [--no-allow-spud]

Scenarios: no-row, no-model, git-commit, write-outside, background.  Run them one at a time:
each spawns a real subagent and costs about $0.10 on haiku.  Nothing touches the repository:
SPUD_HOME is the scratch home, and the session's cwd is that home.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SPUD = REPO / "bin" / "spud"
CONFIG = REPO / "spud.config.json"
PYTHON = sys.executable

HOOK_TABLE = (
    ("PreToolUse", "Agent"), ("PreToolUse", "Bash"), ("PreToolUse", "Write|Edit|MultiEdit|NotebookEdit"),
    ("PostToolUse", "Agent"), ("SubagentStart", None), ("SubagentStop", None),
    ("SessionStart", "startup|resume|compact"), ("Stop", None),
)

PREAMBLE = (
    "You are a headless probe of Spud's ledger hooks. Follow the numbered steps literally, one tool call per step, "
    "with exactly the parameters given. When a tool call is refused or errors, do not retry and do not try an alternative: "
    "quote the refusal text and go on to the next step. Finish with a short report that quotes, verbatim, every tool error "
    "or hook message you saw, in order.\n\n"
)

CHILD_PROMPT = (
    "You are a probe spudagent. Do exactly what your task says, one tool call per step, with the exact parameters given. "
    "When a tool call is refused or errors, do not retry and do not try an alternative: quote the refusal text and go on. "
    "If the harness stops you and tells you to record a Result with a `spud member result` command, run that command exactly "
    "as the message shows it (python3.14 -I -S $SPUD_HOME/bin/spud ... with $SPUD_HOME expanded to the SPUD_HOME environment "
    "variable) and then finish. Your final message must quote every refusal or hook message you saw."
)

SCENARIOS = {
    "no-row": {
        "members": [],
        "prompt": (
            "1. Call the Agent tool with subagent_type \"spudagent\", description \"SPUD-001/Nobody (01, scout)\", "
            "model \"haiku\", run_in_background true, and prompt \"Say potato.\"\n"
            "2. Report."
        ),
    },
    "no-model": {
        "members": [("Kestrel", "scout", "haiku")],
        "prompt": (
            "1. Call the Agent tool with subagent_type \"spudagent\", description \"SPUD-001/Kestrel (01, scout)\", "
            "run_in_background false, prompt \"Return the single word potato and nothing else.\", and NO model parameter at all "
            "(leave model out of the call entirely).\n"
            "2. Call the Agent tool again with the same subagent_type, description, run_in_background false and prompt, "
            "this time with model \"haiku\".\n"
            "3. Report."
        ),
    },
    "git-commit": {
        "members": [("Kestrel", "scout", "haiku")],
        "prompt": (
            "1. Call the Agent tool with subagent_type \"spudagent\", description \"SPUD-001/Kestrel (01, scout)\", "
            "model \"haiku\", run_in_background false, and this prompt: \"Step 1: run exactly this Bash command: "
            "git commit --allow-empty -m probe . Step 2: run exactly this Bash command: git status . Step 3: return the word done.\"\n"
            "2. Report."
        ),
    },
    "write-outside": {
        "members": [("Kestrel", "scout", "haiku")],
        "prompt": (
            "1. Call the Agent tool with subagent_type \"spudagent\", description \"SPUD-001/Kestrel (01, scout)\", "
            "model \"haiku\", run_in_background true, and this prompt: \"Step 1: use the Write tool to create the file "
            "{home}/docs/probe.md with the content x. Step 2: use the Write tool to create the file {home}/tests/probe.txt "
            "with the content ok. Step 3: return the word done.\"\n"
            "2. Wait for the agent to finish, then report."
        ),
    },
    "write-outside-fg": {
        "members": [("Kestrel", "scout", "haiku")],
        "prompt": (
            "1. Call the Agent tool with subagent_type \"spudagent\", description \"SPUD-001/Kestrel (01, scout)\", "
            "model \"haiku\", run_in_background false, and this prompt: \"Step 1: use the Write tool to create the file "
            "{home}/docs/probe.md with the content x. Step 2: use the Write tool to create the file {home}/tests/probe.txt "
            "with the content ok. Step 3: return the word done.\"\n"
            "2. Report."
        ),
    },
    "background": {
        "members": [("Kestrel", "scout", "haiku")],
        "prompt": (
            "1. Call the Agent tool with subagent_type \"spudagent\", description \"SPUD-001/Kestrel (01, scout)\", "
            "model \"haiku\", run_in_background true, and this prompt: \"Step 1: your context holds a line starting with "
            "'Ledger: your agent_id is'; take that agent_id. Step 2: run exactly this Bash command, with AGENT replaced by that "
            "agent_id: python3.14 -I -S $SPUD_HOME/bin/spud --as AGENT member log 'probe log line' . Step 3: return the word potato.\"\n"
            "2. Wait for the agent to finish, then report."
        ),
    },
}


def run(cmd, env, cwd=None, stdin=None, check=True):
    proc = subprocess.run(cmd, env=env, cwd=cwd, input=stdin, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise SystemExit("%s failed (%d):\n%s\n%s" % (" ".join(str(c) for c in cmd), proc.returncode, proc.stdout, proc.stderr))
    return proc


def spud(env, home, *args):
    return run([PYTHON, "-I", "-S", str(home / "bin" / "spud"), *args], env)


def build_home(root, scenario):
    home = root / "home"
    (home / "bin").mkdir(parents=True)
    shutil.copy2(SPUD, home / "bin" / "spud")
    shutil.copy2(CONFIG, home / "spud.config.json")
    for d in ("tests", "docs", ".claude"):
        (home / d).mkdir()
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env["SPUD_HOME"] = str(home)
    spud(env, home, "init")
    spud(env, home, "--as", "spud", "ticket", "new", "--title", "Probe %s" % scenario, "--status", "active")
    for name, persona, model in SCENARIOS[scenario]["members"]:
        spud(env, home, "--as", "spud", "member", "new", "--ticket", "SPD-001", "--persona", persona, "--model", model,
             "--name", name, "--brief", "Probe %s: do what the prompt says." % scenario, "--deliverable", "tests/**")
    capture = root / "hooks.jsonl"
    groups = {}
    for event, matcher in HOOK_TABLE:
        entry = {"type": "command", "command": "cat >> %s; printf '\\n' >> %s" % (capture, capture)}
        groups.setdefault(event, []).append({"matcher": matcher, "hooks": [entry]} if matcher else {"hooks": [entry]})
    settings = home / ".claude" / "settings.json"
    settings.write_text(json.dumps({"hooks": groups}, indent=2) + "\n", encoding="utf-8")
    spud(env, home, "settings", "sync", "--path", str(settings))
    return home, env, settings, capture


def run_claude(home, env, settings, prompt, model, allow_spud):
    agents = {"spudagent": {"description": "A probe spudagent (SPD-008 headless probe).", "prompt": CHILD_PROMPT, "model": "haiku"}}
    allowed = ["Agent", "Write", "Edit", "Bash(git *)", "Bash(ls *)", "Bash(echo *)"]
    if allow_spud:
        allowed.append("Bash(python3.14 -I -S *)")
    cmd = [
        "claude", "-p", PREAMBLE + prompt, "--settings", str(settings), "--agents", json.dumps(agents), "--strict-mcp-config",
        "--output-format", "stream-json", "--verbose", "--model", model, "--permission-mode", "acceptEdits",
        "--allowedTools", ",".join(allowed),
    ]
    started = time.time()
    proc = subprocess.run(cmd, env=env, cwd=str(home), capture_output=True, text=True)
    return cmd, proc, time.time() - started


def summarize_stream(text):
    lines = []
    for raw in text.splitlines():
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        t = msg.get("type")
        if t == "assistant":
            for block in msg.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    inp = block.get("input", {})
                    shown = {k: (v if k != "prompt" else v[:80] + "...") for k, v in inp.items()}
                    lines.append("[%s] tool_use %s %s" % (msg.get("parent_tool_use_id") and "child" or "main", block.get("name"), json.dumps(shown)))
        elif t == "user":
            for block in msg.get("message", {}).get("content", []) if isinstance(msg.get("message", {}).get("content"), list) else []:
                if block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, list):
                        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                    content = str(content)
                    flag = " ERROR" if block.get("is_error") else ""
                    lines.append("[%s] tool_result%s: %s" % (msg.get("parent_tool_use_id") and "child" or "main", flag, content[:400].replace("\n", " | ")))
        elif t == "system" and msg.get("subtype") == "hook_response":
            lines.append("[hook_response] %s" % json.dumps({k: v for k, v in msg.items() if k not in ("type", "session_id", "uuid")})[:400])
        elif t == "result":
            lines.append("[result] subtype=%s cost_usd=%s duration_ms=%s num_turns=%s subagent_stats=%s" % (
                msg.get("subtype"), msg.get("total_cost_usd"), msg.get("duration_ms"), msg.get("num_turns"), json.dumps(msg.get("subagent_stats"))))
            lines.append("[result text] %s" % str(msg.get("result", ""))[:1500].replace("\n", " | "))
    return lines


def summarize_capture(path):
    counts = {}
    rows = []
    if not path.is_file():
        return ["(no captured payloads)"]
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            p = json.loads(raw)
        except ValueError:
            continue
        key = (p.get("hook_event_name"), p.get("tool_name"))
        counts[key] = counts.get(key, 0) + 1
        rows.append("  %s %s agent_id=%s tool_use_id=%s" % (p.get("hook_event_name"), p.get("tool_name") or "", p.get("agent_id"), p.get("tool_use_id")))
    return ["captured %d payloads: %s" % (sum(counts.values()), ", ".join("%s%s x%d" % (e, ("(" + t + ")") if t else "", n) for (e, t), n in sorted(counts.items(), key=lambda kv: str(kv[0]))))] + rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", choices=sorted(SCENARIOS))
    ap.add_argument("--root", help="scratch directory (default $TMPDIR/spud-probes)")
    ap.add_argument("--model", default="haiku", help="the main session's model (default haiku)")
    ap.add_argument("--no-allow-spud", action="store_true", help="omit the CLI from --allowedTools: does the hook's own allow decision let a child run spud?")
    args = ap.parse_args(argv)
    if shutil.which("claude") is None:
        raise SystemExit("claude is not on PATH")
    base = Path(args.root or os.path.join(os.environ.get("TMPDIR", "/tmp"), "spud-probes")).resolve()
    root = base / ("%s-%s" % (args.scenario, time.strftime("%Y%m%dT%H%M%S")))
    root.mkdir(parents=True)
    home, env, settings, capture = build_home(root, args.scenario)
    prompt = SCENARIOS[args.scenario]["prompt"].replace("{home}", str(home))
    cmd, proc, elapsed = run_claude(home, env, settings, prompt, args.model, not args.no_allow_spud)
    (root / "stream.jsonl").write_text(proc.stdout, encoding="utf-8")
    (root / "stderr.txt").write_text(proc.stderr, encoding="utf-8")
    out = []
    out.append("probe %s in %s (%.0f s, exit %d)" % (args.scenario, root, elapsed, proc.returncode))
    out.append("command: " + " ".join(("'%s'" % c) if " " in c or "{" in c else c for c in cmd[:2]) + " ... --settings %s --agents '<spudagent>' --strict-mcp-config --output-format stream-json --verbose --model %s --permission-mode acceptEdits --allowedTools %s" % (settings, args.model, cmd[-1]))
    out.append("--- harness (stream-json) ---")
    out.extend(summarize_stream(proc.stdout))
    if proc.stderr.strip():
        out.append("--- stderr ---")
        out.append(proc.stderr.strip()[:2000])
    out.append("--- captured hook payloads ---")
    out.extend(summarize_capture(capture))
    out.append("--- ledger: spud events ---")
    out.append(spud(env, home, "events").stdout.rstrip())
    out.append("--- ledger: spawn_requests ---")
    out.append(spud(env, home, "sql", "--readonly", "SELECT tool_use_id, caller_agent_id, description, model, run_in_background, member_id, agent_id, decision, reason FROM spawn_requests").stdout.rstrip())
    for name, _p, _m in SCENARIOS[args.scenario]["members"]:
        out.append("--- ledger: spud member show SPUD-001/%s ---" % name)
        out.append(spud(env, home, "member", "show", "SPUD-001/%s" % name).stdout.rstrip())
        out.append(spud(env, home, "sql", "--readonly", "SELECT status, agent_id, resolved_model, total_tokens, duration_ms, tool_uses, substr(usage_json, 1, 200) AS usage_json, substr(return_text, 1, 200) AS return_text, stopped_at FROM members WHERE name = '%s'" % name).stdout.rstrip())
    spool = home / ".spud" / "hook-errors.jsonl"
    out.append("--- spool: %s ---" % ("empty" if not spool.exists() or spool.stat().st_size == 0 else spool.read_text(encoding="utf-8")[:2000]))
    text = "\n".join(out)
    (root / "summary.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
