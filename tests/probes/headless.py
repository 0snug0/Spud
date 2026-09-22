#!/opt/homebrew/bin/python3.14
"""Headless probes for the ledger hooks (real `claude -p` sessions, real API usage).

Each scenario builds a scratch SPUD_HOME (a copy of bin/spud, the shipped share/spud.config.json rendered, `spud init`,
a ticket, the planned members it needs), generates a settings file with `spud settings sync`
on top of a capture hook that appends every raw payload, stamped with the capture time, to
hooks.jsonl, then runs one `claude -p` session in that home with `--settings` and an `--agents`
definition of `spudagent`.  Afterwards it prints what the harness saw (Agent calls, tool errors,
background task events, the result lines), the timeline of every subagent transcript the hooks
named, and what the ledger recorded (events, spawn_requests, every member row).

    python3.14 tests/probes/headless.py <scenario> [--root DIR] [--model haiku] [--no-allow-spud]

Scenarios: no-row, no-model, git-commit, write-outside, write-outside-fg, background (SPD-008);
and for SPD-015, a lead that records its own Result, plans a child and tries to return without
recording it: lead-hold (background child, the lead waits for it first), lead-hold-fg (foreground
child, so it has already returned at the lead's stop) and lead-running (background child, the lead
returns at once and is held for a child that is still alive); and for SPD-018, stop-planned (the main session
plans a member with `member new` and ends its turn without spawning it: Spud's Stop holds it once with the planned
clause, and the row's session_id is compared with the Stop payload's).  Run them one at a time:
each spawns real subagents and costs about $0.10 to $0.50 on haiku.  Nothing touches the
repository: SPUD_HOME is the scratch home, and the session's cwd is that home.

One scenario is a different shape (SPW-001, design section 5 and phase 8): spud-init builds a scratch *clone* of
this repository instead of a scratch home -- a copy of bin/, share/, the bootstrap skill and the two files section 5
asks for, with a `git init` so `git rev-parse --show-toplevel` answers the way a real clone's does, and nothing
installed anywhere on the machine.  It runs `claude -p` there with the prompt `/spud-init` and nothing else, the
same four environment overrides tests/test_init.py's MachineMixin uses (SPUD_CONFIG_DIR, SPUD_USER_CLAUDE_DIR,
SPUD_LAUNCH_AGENTS_DIR, and a fake launchctl of its own) so a run that reaches the schedule step never touches this
machine's real launchd, plus SPUD_HOME naming the home `spud init` should build.  If that first turn only asks the
clarifying question the skill's own "ask" instructions call for -- reasonable, since a `claude -p` turn has nobody
to ask but its next turn -- a second turn resumes the same session with the answer a person would give
(run_init_probe, FOLLOWUP_ANSWER); the first turn is still typed as nothing but `/spud-init`.  Afterwards it runs
`spud doctor` itself, independently of anything either turn claimed, against that home -- the ground truth for
whether the skill actually took the clone all the way to `problems    none`.
"""

import argparse
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SPUD = REPO / "bin" / "spud"
# The config a scratch home starts from: the template the tool ships for a real home, rendered with the suite's marks --
# the pair tests/helpers.py builds every Home from (SPW-001, which deleted the suite's own copy of the config).
CONFIG = REPO / "share" / "spud.config.json"
CONFIG_MARKS = REPO / "tests" / "fixtures" / "config_marks.json"
PYTHON = sys.executable

HOOK_TABLE = (
    ("PreToolUse", "Agent"), ("PreToolUse", "Bash"), ("PreToolUse", "Write|Edit|MultiEdit|NotebookEdit"),
    ("PostToolUse", "Agent"), ("SubagentStart", None), ("SubagentStop", None),
    ("SessionStart", "startup|resume|clear|compact"), ("Stop", None),
)

# The capture hook: one JSON line per payload, with the wall-clock time it reached the hook.
CAPTURE_SCRIPT = """import json, sys, time
raw = sys.stdin.read()
try:
    payload = json.loads(raw)
except ValueError:
    payload = {"_raw": raw}
if not isinstance(payload, dict):
    payload = {"_raw": raw}
payload["_captured_at"] = round(time.time(), 3)
with open(sys.argv[1], "a", encoding="utf-8") as f:
    f.write(json.dumps(payload) + "\\n")
"""

PREAMBLE = (
    "You are a headless probe of Spud's ledger hooks. Follow the numbered steps literally, one tool call per step, "
    "with exactly the parameters given. When a tool call is refused or errors, do not retry and do not try an alternative: "
    "quote the refusal text and go on to the next step. Finish with a short report that quotes, verbatim, every tool error "
    "or hook message you saw, in order.\n\n"
)

CHILD_PROMPT = (
    "You are a probe spudagent. Do exactly what your task says, one tool call per step, with the exact parameters given. "
    "When a tool call is refused or errors, do not retry and do not try an alternative: quote the refusal text and go on. "
    "If the harness stops you and tells you to record something with `spud` commands (your Result, or a `member finish` for a "
    "child you spawned), run each command exactly as the message shows it, as python3.14 -I -S $SPUD_HOME/bin/spud ... with "
    "$SPUD_HOME expanded to the SPUD_HOME environment variable, choosing done for done|blocked|failed and replacing a "
    "placeholder such as '<verdict>' or '<what you produced ...>' with one short sentence, and then finish. "
    "If it tells you to wait for a child inside this turn, wait by running exactly "
    "python3.14 -I -S -c 'import time; time.sleep(20)' and then the `spud member show` command the message names, repeating "
    "until that child's Result is printed, and then run the `member finish` command the message names. "
    "Your final message must quote every refusal or hook message you saw."
)

LEAD_HOLD_CHILD = (
    "Step 1: your context holds a line starting with 'Ledger: your agent_id is'; take that agent_id and call it CHILD. "
    "Step 2: run exactly this Bash command: python3.14 -I -S -c 'import time; time.sleep(30)'\n"
    "Step 3: run exactly this Bash command, with CHILD replaced by your agent_id: "
    "python3.14 -I -S $SPUD_HOME/bin/spud --as CHILD member result 'probe child: slept 30 seconds'\n"
    "Step 4: return the word potato."
)

LEAD_HOLD_FG_CHILD = (
    "Step 1: your context holds a line starting with 'Ledger: your agent_id is'; take that agent_id and call it CHILD. "
    "Step 2: run exactly this Bash command, with CHILD replaced by your agent_id: "
    "python3.14 -I -S $SPUD_HOME/bin/spud --as CHILD member result 'probe child: nothing to report'\n"
    "Step 3: return the word potato."
)


def lead_prompt(child_prompt, background, last_steps):
    """A lead that records its own Result, plans Russet (01.01) and spawns it; the scenario's
    own last steps decide whether it waits for the child before returning."""
    return (
        "Step 1: your context holds a line starting with 'Ledger: your agent_id is'; take that agent_id and call it LEAD.\n"
        "Step 2: run exactly this Bash command, with LEAD replaced by your agent_id: "
        "python3.14 -I -S $SPUD_HOME/bin/spud --as LEAD member result 'probe lead: planned and spawned Russet'\n"
        "Step 3: run exactly this Bash command, with LEAD replaced by your agent_id: "
        "python3.14 -I -S $SPUD_HOME/bin/spud --as LEAD member new --persona scout --model haiku --name Russet "
        "--brief 'Probe child: do what the prompt says.' --deliverable 'tests/**'\n"
        "Step 4: call the Agent tool with subagent_type \"spudagent\", description \"SPUD-001/Russet (01.01, scout)\", "
        "model \"haiku\", run_in_background %s, and as its prompt the text between <child-prompt> and </child-prompt>: "
        "<child-prompt>%s</child-prompt>\n%s" % ("true" if background else "false", child_prompt, last_steps)
    )


LEAD_HOLD_LEAD = lead_prompt(LEAD_HOLD_CHILD, True, "Step 5: wait for SPUD-001/Russet to finish.\nStep 6: return the word done.")
LEAD_HOLD_FG_LEAD = lead_prompt(LEAD_HOLD_FG_CHILD, False, "Step 5: return the word done.")
LEAD_RUNNING_LEAD = lead_prompt(
    LEAD_HOLD_CHILD, True,
    "Step 5: do not wait for SPUD-001/Russet and do not run member finish for it; return the word done immediately.")

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
    "lead-hold": {
        "members": [("Kestrel", "scout", "haiku")],
        "prompt": (
            "1. Call the Agent tool with subagent_type \"spudagent\", description \"SPUD-001/Kestrel (01, scout)\", "
            "model \"haiku\", run_in_background true, and as its prompt the text between <lead-prompt> and </lead-prompt>: "
            "<lead-prompt>" + LEAD_HOLD_LEAD + "</lead-prompt>\n"
            "2. Wait for the agent to finish, then report."
        ),
    },
    "lead-hold-fg": {
        "members": [("Kestrel", "scout", "haiku")],
        "prompt": (
            "1. Call the Agent tool with subagent_type \"spudagent\", description \"SPUD-001/Kestrel (01, scout)\", "
            "model \"haiku\", run_in_background true, and as its prompt the text between <lead-prompt> and </lead-prompt>: "
            "<lead-prompt>" + LEAD_HOLD_FG_LEAD + "</lead-prompt>\n"
            "2. Wait for the agent to finish, then report."
        ),
    },
    "lead-running": {
        "members": [("Kestrel", "scout", "haiku")],
        "prompt": (
            "1. Call the Agent tool with subagent_type \"spudagent\", description \"SPUD-001/Kestrel (01, scout)\", "
            "model \"haiku\", run_in_background true, and as its prompt the text between <lead-prompt> and </lead-prompt>: "
            "<lead-prompt>" + LEAD_RUNNING_LEAD + "</lead-prompt>\n"
            "2. Wait for the agent to finish, then report."
        ),
    },
    "stop-planned": {
        "members": [],
        "prompt": (
            "1. Run exactly this Bash command: python3.14 -I -S $SPUD_HOME/bin/spud --as spud member new --ticket SPD-001 --persona scout "
            "--model haiku --name Russet --brief 'Probe row: planned and never spawned.' --deliverable 'tests/**'\n"
            "2. Do not call the Agent tool at any point. Report the command's output and end your turn.\n"
            "3. If a Stop hook message then says a planned spudagent was never spawned, do not spawn it: run the "
            "`spud --as spud member finish ... --status failed` command it names, exactly, as python3.14 -I -S $SPUD_HOME/bin/spud ..., "
            "with '<why>' replaced by 'probe: never spawned', then end your turn again."
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


def write_config(home):
    """Render the shipped config template into `home`/spud.config.json; returns it parsed."""
    text = CONFIG.read_text(encoding="utf-8")
    for mark, value in json.loads(CONFIG_MARKS.read_text(encoding="utf-8")).items():
        text = text.replace(mark, value)
    (home / "spud.config.json").write_text(text, encoding="utf-8")
    return json.loads(text)


def seed_project_one(home, config):
    """Project 1 as `spud init` inserted it before SPW-001: key `spud`, rooted at the home, whose bin/spud this probe's
    launcher is.  Init registers no project now (docs/design/2026-09-21-spud-init.md section 1.4) and the ticket below
    needs one."""
    con = sqlite3.connect(home / ".spud" / "ledger.db", timeout=5)
    try:
        with con:
            con.execute(
                "INSERT INTO projects (id, key, name, root_path, ticket_prefix, team_prefix, created_at)"
                " VALUES (1, 'spud', ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (config["identity"]["name"], str(home), config["tickets"]["prefix"], config["teams"]["prefix"],
                 datetime.now().astimezone().isoformat(timespec="seconds")),
            )
    finally:
        con.close()


def build_home(root, scenario):
    home = root / "home"
    (home / "bin").mkdir(parents=True)
    shutil.copy2(SPUD, home / "bin" / "spud")
    shutil.copy2(SPUD.parent / "spud_ledger.py", home / "bin" / "spud_ledger.py")
    shutil.copytree(SPUD.parent / "spudlib", home / "bin" / "spudlib", ignore=shutil.ignore_patterns("__pycache__"))
    config = write_config(home)
    for d in ("tests", "docs", ".claude"):
        (home / d).mkdir()
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    # The probe session sets the session id its own Bash and hooks see (SPD-018); the one inherited from the session
    # running this driver would otherwise be stamped on the rows `member new` plans here, and on the probe's own.
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env["SPUD_HOME"] = str(home)
    spud(env, home, "init")
    seed_project_one(home, config)
    spud(env, home, "--as", "spud", "ticket", "new", "--title", "Probe %s" % scenario, "--status", "active")
    for name, persona, model in SCENARIOS[scenario]["members"]:
        spud(env, home, "--as", "spud", "member", "new", "--ticket", "SPD-001", "--persona", persona, "--model", model,
             "--name", name, "--brief", "Probe %s: do what the prompt says." % scenario, "--deliverable", "tests/**")
    capture = root / "hooks.jsonl"
    script = root / "capture.py"
    script.write_text(CAPTURE_SCRIPT, encoding="utf-8")
    groups = {}
    for event, matcher in HOOK_TABLE:
        entry = {"type": "command", "command": "%s -I -S %s %s" % (shlex.quote(PYTHON), shlex.quote(str(script)), shlex.quote(str(capture)))}
        groups.setdefault(event, []).append({"matcher": matcher, "hooks": [entry]} if matcher else {"hooks": [entry]})
    settings = home / ".claude" / "settings.json"
    settings.write_text(json.dumps({"hooks": groups}, indent=2) + "\n", encoding="utf-8")
    spud(env, home, "settings", "sync", "--path", str(settings))
    return home, env, settings, capture


# ----------------------------------------------------------------------------
# spud-init: a scratch clone, not a scratch home (SPW-001, design section 5 and phase 8)
# ----------------------------------------------------------------------------

# The stand-in for launchctl this scenario alone needs: a run that reaches step 8 without --no-schedule would
# otherwise bootstrap a real LaunchAgent into this machine's own launchd domain, since the *plist* moves with
# SPUD_LAUNCH_AGENTS_DIR but the job's label does not (tests/helpers.FAKE_LAUNCHCTL, tests/test_init.py's
# MachineMixin comment on the same point).  Same three verbs schedule.py's launchctl() calls, same shape.
FAKE_LAUNCHCTL_SCRIPT = r'''"""A stand-in for launchctl: records each call and tracks a job's loaded state in a file,
so the spud-init probe's own run never reaches this machine's real launchd."""
import json
import os
import sys

state = os.environ["SPUD_INIT_PROBE_LAUNCHCTL_STATE"]
args = sys.argv[1:]
with open(os.path.join(state, "calls.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")
verb = args[0] if args else ""


def loaded_file(label):
    return os.path.join(state, "loaded-" + label)


if verb == "bootout":
    label = args[1].rsplit("/", 1)[-1]
    if os.path.exists(loaded_file(label)):
        os.remove(loaded_file(label))
        sys.exit(0)
    sys.stderr.write("Boot-out failed: 3: No such process\n")
    sys.exit(3)
if verb == "bootstrap":
    label = os.path.basename(args[2])[:-len(".plist")]
    open(loaded_file(label), "w").close()
    sys.exit(0)
if verb == "print":
    label = args[1].rsplit("/", 1)[-1]
    if os.path.exists(loaded_file(label)):
        sys.stdout.write("%s = {\n}\n" % args[1])
        sys.exit(0)
    sys.stderr.write('Could not find service "%s" in domain for user gui: %d\n' % (label, os.getuid()))
    sys.exit(113)
sys.stderr.write("fake launchctl: unexpected arguments %r\n" % (args,))
sys.exit(64)
'''


def build_init_clone(root):
    """A copy of this repository's own tree the way a fresh `git clone` would have it: `bin/`, `share/`, the
    bootstrap skill, the spudagent definition `project install` needs to install this project, and the two files
    design section 5 names (README.md, CLAUDE.md) -- nothing else, and nothing installed anywhere on the machine.
    `git init` alone (no commit needed: `git rev-parse --show-toplevel` answers before there is a first commit) so
    the skill's own first step, finding the root with git, answers the way a real clone's does instead of failing
    on "not a git repository" -- a failure that would be this scenario's own plumbing, not the skill's content."""
    clone = root / "clone"
    (clone / "bin").mkdir(parents=True)
    shutil.copy2(SPUD, clone / "bin" / "spud")
    shutil.copy2(SPUD.parent / "spud_ledger.py", clone / "bin" / "spud_ledger.py")
    shutil.copytree(SPUD.parent / "spudlib", clone / "bin" / "spudlib", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(REPO / "share", clone / "share")
    shutil.copytree(REPO / ".claude" / "skills" / "spud-init", clone / ".claude" / "skills" / "spud-init")
    (clone / ".claude" / "agents").mkdir(parents=True)
    shutil.copy2(REPO / ".claude" / "agents" / "spudagent.md", clone / ".claude" / "agents" / "spudagent.md")
    shutil.copy2(REPO / "README.md", clone / "README.md")
    shutil.copy2(REPO / "CLAUDE.md", clone / "CLAUDE.md")
    run(["git", "init", "--quiet", str(clone)], dict(os.environ))
    return clone


def init_probe_env(root, target_home):
    """This scenario's environment: SPUD_HOME names the home `/spud-init` should build (a directory that does not
    yet exist), and SPUD_CONFIG_DIR, SPUD_USER_CLAUDE_DIR, SPUD_LAUNCH_AGENTS_DIR and SPUD_LAUNCHCTL keep every
    other write -- the home pointer, `~/.claude`, the two LaunchAgents -- inside `root` too: the same four
    overrides tests/test_init.py's MachineMixin uses to make a real `spud init` run a test rather than an accident
    on this machine, plus the fake launchctl above.  What this cannot cover: a session that types `--home` with a
    literal path of its own instead of leaving the flag out for SPUD_HOME to answer.  The skill offers `~/SpudHome`
    only as Recipe 1's example, not Recipe 2's -- Recipe 2 is the one a session sees here, since this clone is the
    Spud tool repository itself -- so a run is expected to reach the intended home; the summary reports where it
    actually landed either way."""
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    for name in ("SPUD_HOME", "SPUD_TOOL_DIR"):
        env.pop(name, None)
    launchctl_state = root / "launchctl-state"
    launchctl_state.mkdir(parents=True)
    fake = root / "fake_launchctl.py"
    fake.write_text(FAKE_LAUNCHCTL_SCRIPT, encoding="utf-8")
    launchctl = root / "launchctl"
    launchctl.write_text("#!/bin/sh\nexec %s -I -S %s \"$@\"\n" % (shlex.quote(PYTHON), shlex.quote(str(fake))), encoding="utf-8")
    launchctl.chmod(0o755)
    env.update(SPUD_HOME=str(target_home), SPUD_CONFIG_DIR=str(root / "config"), SPUD_USER_CLAUDE_DIR=str(root / "user-claude"),
               SPUD_LAUNCH_AGENTS_DIR=str(root / "launchagents"), SPUD_LAUNCHCTL=str(launchctl),
               SPUD_INIT_PROBE_LAUNCHCTL_STATE=str(launchctl_state))
    return env


def transcript_used_bash(stdout):
    """Whether the harness's own stream-json shows the turn called the Bash tool at all.  A turn given only
    `/spud-init` may reasonably ask a clarifying question instead of acting -- the skill says to ask for the home,
    the project root and the two prefixes, and a `claude -p` turn has nobody to ask but its own next turn -- and this
    is how `run_init_probe` tells the two apart."""
    for raw in stdout.splitlines():
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if msg.get("type") != "assistant":
            continue
        for block in msg.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                return True
    return False


def session_id_of(stdout):
    """The session id every stream-json message of one `claude -p` run carries, for `--resume`."""
    for raw in stdout.splitlines():
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        sid = msg.get("session_id")
        if isinstance(sid, str) and sid:
            return sid
    return None


# What a person would answer the clarifying question the skill's own "ask" instructions produce, if the first turn
# asked one instead of acting: the recipe this clone is (Recipe 2, since cwd is the Spud tool repository itself),
# and the three values with no default, with the home given as this scenario's own scratch path -- never `~/SpudHome`
# -- so a second turn cannot reach outside `root` even if the first turn's own guess would have.
FOLLOWUP_ANSWER = ("Recipe 2 (this Spud clone itself) -- run it now, non-interactively, with exactly these values and "
                   "ask nothing further: home directory {home}, ticket prefix ZZZ, team prefix ZZZS, everything else "
                   "the defaults the skill already names.")


def run_init_probe(root, model):
    """The scenario itself: `claude -p` in the clone, given the prompt `/spud-init` and nothing else for its first
    turn -- no preamble, no numbered steps, the thing design section 5 promised a fresh clone gets for free.  If that
    turn asked rather than acted (transcript_used_bash is False), a second turn resumes the same session with the
    answer a person would have given (FOLLOWUP_ANSWER) -- the natural continuation of one `/spud-init` conversation,
    not a second incantation typed instead of the first.  Returns (clone, target_home, env, turns, elapsed, doctor):
    `turns` is [(prompt, cmd, proc), ...], one or two; `doctor` is this function's own `spud doctor` against
    `target_home`, run after the session ends and independent of anything the session itself claimed -- the ground
    truth for whether the skill actually reached `problems    none`, the same discipline the design's phase 4
    verification uses."""
    target_home = root / "home"  # deliberately absent: /spud-init should create it
    clone = build_init_clone(root)
    env = init_probe_env(root, target_home)
    fixed = ["--strict-mcp-config", "--output-format", "stream-json", "--verbose", "--model", model,
             "--permission-mode", "acceptEdits", "--allowedTools", "Bash"]
    started = time.time()
    cmd1 = ["claude", "-p", "/spud-init"] + fixed
    proc1 = subprocess.run(cmd1, env=env, cwd=str(clone), capture_output=True, text=True)
    turns = [("/spud-init", cmd1, proc1)]
    if proc1.returncode == 0 and not transcript_used_bash(proc1.stdout):
        session_id = session_id_of(proc1.stdout)
        if session_id:
            answer = FOLLOWUP_ANSWER.format(home=target_home)
            cmd2 = ["claude", "-p", answer, "--resume", session_id] + fixed
            proc2 = subprocess.run(cmd2, env=env, cwd=str(clone), capture_output=True, text=True)
            turns.append((answer, cmd2, proc2))
    elapsed = time.time() - started
    doctor = run([PYTHON, "-I", "-S", str(clone / "bin" / "spud"), "doctor", "--json"], env, check=False)
    return clone, target_home, env, turns, elapsed, doctor


def run_claude(home, env, settings, prompt, model, allow_spud):
    agents = {"spudagent": {"description": "A probe spudagent (ledger hooks headless probe).", "prompt": CHILD_PROMPT, "model": "haiku"}}
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


def local_time(value):
    """HH:MM:SS.mmm in local time from an epoch float or an ISO 8601 string (Z or offset)."""
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            stamp = datetime.fromtimestamp(value)
        elif isinstance(value, str) and value:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        else:
            return "?"
    except (ValueError, OverflowError, OSError):
        return "?"
    return stamp.strftime("%H:%M:%S.") + "%03d" % (stamp.microsecond // 1000)


def one_line(text, limit):
    return str(text)[:limit].replace("\n", " | ")


def summarize_stream(text):
    lines = []
    agents = {}  # Agent tool_use id -> its description, to label nested messages
    hook_responses = 0
    for raw in text.splitlines():
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        t = msg.get("type")
        parent = msg.get("parent_tool_use_id")
        who = "main" if not parent else agents.get(parent, "child")
        if t == "assistant":
            for block in msg.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    inp = block.get("input", {})
                    if block.get("name") == "Agent":
                        agents[block.get("id")] = inp.get("description") or "agent"
                    shown = {k: (v if k != "prompt" else str(v)[:80] + "...") for k, v in inp.items()}
                    lines.append("[%s] tool_use %s %s" % (who, block.get("name"), json.dumps(shown)))
        elif t == "user":
            content = msg.get("message", {}).get("content")
            if isinstance(content, str):
                lines.append("[%s] user: %s" % (who, one_line(content, 400)))
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    body = block.get("content")
                    if isinstance(body, list):
                        body = " ".join(c.get("text", "") for c in body if isinstance(c, dict))
                    flag = " ERROR" if block.get("is_error") else ""
                    lines.append("[%s] tool_result%s: %s" % (who, flag, one_line(body, 400)))
                elif block.get("type") == "text":
                    lines.append("[%s] user text: %s" % (who, one_line(block.get("text", ""), 400)))
        elif t == "system" and msg.get("subtype") in ("task_started", "task_updated", "task_notification", "background_tasks_changed"):
            shown = {k: v for k, v in msg.items() if k not in ("type", "session_id", "uuid", "prompt", "output_file")}
            if "summary" in shown:
                shown["summary"] = str(shown["summary"])[:200]
            lines.append("[task] %s" % json.dumps(shown)[:600])
        elif t == "system" and msg.get("subtype") == "hook_response":
            if msg.get("hook_event") == "SessionStart":
                hook_responses += 1
                continue
            lines.append("[hook_response] %s" % json.dumps({k: v for k, v in msg.items() if k not in ("type", "session_id", "uuid")})[:400])
        elif t == "result":
            lines.append("[result] subtype=%s cost_usd=%s duration_ms=%s num_turns=%s subagent_stats=%s" % (
                msg.get("subtype"), msg.get("total_cost_usd"), msg.get("duration_ms"), msg.get("num_turns"), json.dumps(msg.get("subagent_stats"))))
            lines.append("[result text] %s" % one_line(msg.get("result", ""), 1500))
    if hook_responses:
        lines.insert(0, "(%d SessionStart hook_response messages not shown)" % hook_responses)
    return lines


def captured_payloads(path):
    payloads = []
    if not path.is_file():
        return payloads
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            p = json.loads(raw)
        except ValueError:
            continue
        if isinstance(p, dict):
            payloads.append(p)
    return payloads


def summarize_capture(path):
    payloads = captured_payloads(path)
    if not payloads:
        return ["(no captured payloads)"]
    counts = {}
    rows = []
    for p in payloads:
        event, tool = p.get("hook_event_name"), p.get("tool_name")
        counts[(event, tool)] = counts.get((event, tool), 0) + 1
        tool_input = p.get("tool_input") if isinstance(p.get("tool_input"), dict) else {}
        extra = ""
        if event in ("SubagentStop", "Stop"):
            tasks = [(b.get("id"), b.get("status"), b.get("description")) for b in p.get("background_tasks") or [] if isinstance(b, dict)]
            extra = " stop_hook_active=%s background_tasks=%s last=%r" % (p.get("stop_hook_active"), tasks, (p.get("last_assistant_message") or "")[:100])
        elif tool == "Bash":
            extra = " command=%r" % (tool_input.get("command") or "")[:160]
        elif tool == "Agent":
            resp = p.get("tool_response") if isinstance(p.get("tool_response"), dict) else {}
            extra = " description=%r background=%s status=%s" % (tool_input.get("description"), tool_input.get("run_in_background"), resp.get("status"))
        rows.append("  %s %s %s session=%s agent_id=%s tool_use_id=%s%s" % (local_time(p.get("_captured_at")), event, tool or "", p.get("session_id"), p.get("agent_id"), p.get("tool_use_id"), extra))
    head = "captured %d payloads: %s" % (sum(counts.values()), ", ".join("%s%s x%d" % (e, ("(" + t + ")") if t else "", n) for (e, t), n in sorted(counts.items(), key=lambda kv: str(kv[0]))))
    return [head] + rows


def summarize_transcripts(path):
    """The timeline of every subagent transcript the hooks named (and any beside them)."""
    payloads = captured_payloads(path)
    subagents = {}
    main_transcript = None
    for p in payloads:
        if isinstance(p.get("transcript_path"), str) and p.get("transcript_path"):
            main_transcript = p["transcript_path"]
        if isinstance(p.get("agent_transcript_path"), str) and p.get("agent_id"):
            subagents[p["agent_id"]] = p["agent_transcript_path"]
    if main_transcript:
        folder = Path(main_transcript).with_suffix("") / "subagents"
        if folder.is_dir():
            for f in sorted(folder.glob("agent-*.jsonl")):
                subagents.setdefault(f.stem[len("agent-"):], str(f))
    lines = []
    for agent_id, transcript in sorted(subagents.items()):
        lines.append("--- transcript agent %s (%s) ---" % (agent_id, transcript))
        try:
            entries = Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            lines.append("  (unreadable: %s)" % e)
            continue
        for raw in entries:
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            msg = e.get("message") if isinstance(e.get("message"), dict) else {}
            content = msg.get("content")
            parts = []
            if isinstance(content, str):
                parts.append("text %r" % content[:200])
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    kind = b.get("type")
                    if kind == "text":
                        parts.append("text %r" % b.get("text", "")[:200])
                    elif kind == "tool_use":
                        parts.append("tool_use %s %s" % (b.get("name"), json.dumps(b.get("input"))[:200]))
                    elif kind == "tool_result":
                        body = b.get("content")
                        if isinstance(body, list):
                            body = " ".join(c.get("text", "") for c in body if isinstance(c, dict))
                        parts.append("tool_result%s %r" % (" ERROR" if b.get("is_error") else "", str(body)[:200]))
            else:
                parts.append(json.dumps({k: v for k, v in e.items() if k not in ("uuid", "parentUuid", "sessionId", "cwd", "version", "gitBranch")})[:200])
            lines.append("  %s %s %s" % (local_time(e.get("timestamp")), e.get("type"), "; ".join(parts)))
    return lines or ["(no subagent transcripts)"]


SPUD_INIT_SCENARIO = "spud-init"  # a scratch clone, not a scratch home; handled on its own below (build_init_clone)


# doctor.WATCHER_DOWN, verbatim (bin/spudlib/commands/doctor.py): the one problem the fake launchctl above can never
# clear on its own, since `launchagents.watcher_alive` asks whether something holds <home>/.spud/render.lock, and
# nothing this probe starts is a real background watcher that could hold it.  `homeinit.verify` excuses the identical
# case for the same reason -- a watcher bootstrapped seconds ago and not up yet -- and tests/test_init.py's
# ScheduleStepTest names it too (`out["doctor"]["problems"], [load_spud_module().WATCHER_DOWN]`).  Excused here only
# by this exact string, so any other problem still fails the scenario.
FAKE_LAUNCHCTL_EXCUSED_PROBLEM = ("the render watcher %s is installed but not running: the vault is stale until"
                                  " `spud --as spud schedule install` reloads it" % "local.spud.render")


def main_spud_init(root, model):
    """The spud-init scenario's report: the harness's own transcript of each turn, and then the independent
    `spud doctor` this driver runs itself against the home the environment named -- not a claim the session made
    about itself."""
    clone, target_home, env, turns, elapsed, doctor = run_init_probe(root, model)
    out = []
    out.append("probe spud-init in %s (%.0f s, %d turn(s))" % (root, elapsed, len(turns)))
    out.append("clone: %s" % clone)
    out.append("intended home (SPUD_HOME): %s" % target_home)
    last_exit = 0
    for n, (prompt, cmd, proc) in enumerate(turns, start=1):
        (root / ("stream%d.jsonl" % n)).write_text(proc.stdout, encoding="utf-8")
        (root / ("stderr%d.txt" % n)).write_text(proc.stderr, encoding="utf-8")
        last_exit = proc.returncode
        out.append("--- turn %d: %s %s (exit %d) ---" % (n, cmd[0], json.dumps(prompt), proc.returncode))
        out.extend(summarize_stream(proc.stdout))
        if proc.stderr.strip():
            out.append("--- turn %d stderr ---" % n)
            out.append(proc.stderr.strip()[:2000])
    out.append("--- independent check: this driver's own `spud doctor --json` against the intended home ---")
    out.append("exit %d" % doctor.returncode)
    out.append((doctor.stdout or "(no stdout)").strip())
    if doctor.stderr.strip():
        out.append("stderr: " + doctor.stderr.strip()[:2000])
    problems = None
    try:
        problems = json.loads(doctor.stdout).get("problems") if doctor.stdout.strip() else None
    except ValueError:
        problems = None
    real_problems = None if problems is None else [p for p in problems if p != FAKE_LAUNCHCTL_EXCUSED_PROBLEM]
    if real_problems is not None and real_problems != problems:
        out.append("(excused: %r -- this probe's fake launchctl starts no real watcher; see FAKE_LAUNCHCTL_EXCUSED_PROBLEM)"
                   % FAKE_LAUNCHCTL_EXCUSED_PROBLEM)
    text = "\n".join(out)
    (root / "summary.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if last_exit == 0 and real_problems == [] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", choices=sorted(SCENARIOS) + [SPUD_INIT_SCENARIO])
    ap.add_argument("--root", help="scratch directory (default $TMPDIR/spud-probes)")
    ap.add_argument("--model", default="haiku", help="the main session's model (default haiku)")
    ap.add_argument("--no-allow-spud", action="store_true", help="omit the CLI from --allowedTools: does the hook's own allow decision let a child run spud?")
    args = ap.parse_args(argv)
    if shutil.which("claude") is None:
        raise SystemExit("claude is not on PATH")
    base = Path(args.root or os.path.join(os.environ.get("TMPDIR", "/tmp"), "spud-probes")).resolve()
    root = base / ("%s-%s" % (args.scenario, time.strftime("%Y%m%dT%H%M%S")))
    root.mkdir(parents=True)
    if args.scenario == SPUD_INIT_SCENARIO:
        return main_spud_init(root, args.model)
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
    out.append("--- captured hook payloads (local capture time) ---")
    out.extend(summarize_capture(capture))
    out.append("--- subagent transcripts (local time) ---")
    out.extend(summarize_transcripts(capture))
    out.append("--- ledger: spud events ---")
    out.append(spud(env, home, "events").stdout.rstrip())
    out.append("--- ledger: spawn_requests ---")
    out.append(spud(env, home, "sql", "--readonly", "SELECT tool_use_id, caller_agent_id, description, model, run_in_background, member_id, agent_id, decision, reason FROM spawn_requests").stdout.rstrip())
    names = json.loads(spud(env, home, "sql", "--readonly", "--json", "SELECT name FROM members ORDER BY lineage").stdout).get("rows", [])
    for row in names:
        name = row["name"] if isinstance(row, dict) else row[0]
        out.append("--- ledger: spud member show SPUD-001/%s ---" % name)
        out.append(spud(env, home, "member", "show", "SPUD-001/%s" % name).stdout.rstrip())
        out.append(spud(env, home, "sql", "--readonly", "SELECT status, session_id, agent_id, resolved_model, total_tokens, duration_ms, tool_uses, substr(usage_json, 1, 200) AS usage_json, substr(return_text, 1, 200) AS return_text, stopped_at FROM members WHERE name = '%s'" % name).stdout.rstrip())
    spool = home / ".spud" / "hook-errors.jsonl"
    out.append("--- spool: %s ---" % ("empty" if not spool.exists() or spool.stat().st_size == 0 else spool.read_text(encoding="utf-8")[:2000]))
    text = "\n".join(out)
    (root / "summary.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
