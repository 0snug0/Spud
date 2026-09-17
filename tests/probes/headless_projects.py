"""Headless scenarios for cross-repository projects (SPD-014, docs/design/2026-09-14-cross-repository-projects.md section 7.4).

Run one scenario at a time, from any directory:

    python3.14 -I -S tests/probes/headless_projects.py project-plain | project-spud | project-worktree | discovery | skill

(The hook probes of SPD-008, SPD-015 and SPD-018 stay in tests/probes/headless.py; the design named that file for these
scenarios, but it was taken, so they live beside it.)

Each scenario builds a scratch directory under the system temp directory: a scratch home (a git repository holding a copy of
this checkout's bin/spud, bin/spud_ledger.py and .claude/agents/spudagent.md and of the suite's
tests/fixtures/spud.config.json, with a bare origin, `spud init`) and a scratch "other" repository standing in for BadTakes
(key badtakes, prefixes BAD / BADS, Eric 2026-09-14), with a commit and a bare origin.  It runs `project add` and `project install` with SPUD_USER_CLAUDE_DIR and SPUD_CONFIG_DIR in the
scratch directory, then launches `claude -p --model haiku --setting-sources project,local` in the other repository, so the
installed local settings are the settings that load.  `spudagent` is passed with --agents, since a headless run cannot
redirect user scope.  Nothing touches Spud's or BadTakes' repositories.  The stream of each run, the scratch ledger's
events and a JSON summary of what was observed are left in the scratch directory, whose path is printed; the summary is
printed too.  Not collected by the unit test suite (the file name matches no test pattern).
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "tests" / "fixtures" / "spud.config.json"  # the suite's fixture: the tool keeps no home config (SPD-097)
PY = sys.executable
MODEL = "haiku"
IDENTITY = {"GIT_AUTHOR_NAME": "Spud probe", "GIT_AUTHOR_EMAIL": "probe@example.invalid", "GIT_COMMITTER_NAME": "Spud probe", "GIT_COMMITTER_EMAIL": "probe@example.invalid"}


def git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="core.excludesFile", GIT_CONFIG_VALUE_0="/dev/null",
               GIT_CONFIG_KEY_1="commit.gpgsign", GIT_CONFIG_VALUE_1="false", **IDENTITY)
    return env


def git(repo, *args, check=True):
    proc = subprocess.run(["git", "-C", str(repo), *map(str, args)], capture_output=True, text=True, env=git_env())
    if check and proc.returncode != 0:
        raise SystemExit("git %s in %s failed: %s" % (" ".join(map(str, args)), repo, proc.stderr))
    return proc.stdout.strip()


class Scratch:
    def __init__(self, name):
        self.root = Path(tempfile.mkdtemp(prefix="spud-headless-%s-" % name)).resolve()
        self.home = self.root / "home"
        self.other = self.root / "badtakes"
        self.user = self.root / "user-claude"
        self.config = self.root / "user-config"
        self.log = []
        self.env = dict(git_env(), SPUD_HOME=str(self.home), SPUD_USER_CLAUDE_DIR=str(self.user), SPUD_CONFIG_DIR=str(self.config))
        for key in [k for k in self.env if k.startswith("CLAUDE") or k == "CLAUDECODE"]:
            self.env.pop(key)

    def spud(self, *args, cwd=None, session=None, check=True):
        env = dict(self.env)
        if session:
            env["CLAUDE_CODE_SESSION_ID"] = session
        proc = subprocess.run([PY, "-I", "-S", str(self.home / "bin" / "spud"), *map(str, args)], capture_output=True, text=True, env=env, cwd=str(cwd or self.root))
        if check and proc.returncode != 0:
            raise SystemExit("spud %s failed (%d): %s %s" % (" ".join(map(str, args)), proc.returncode, proc.stdout, proc.stderr))
        return proc

    def build(self):
        self.home.mkdir()
        (self.home / "bin").mkdir()
        for rel in ("bin/spud", "bin/spud_ledger.py"):
            shutil.copyfile(REPO / rel, self.home / rel)
        shutil.copyfile(CONFIG, self.home / "spud.config.json")
        shutil.copytree(REPO / "bin" / "spudlib", self.home / "bin" / "spudlib", ignore=shutil.ignore_patterns("__pycache__"))
        os.chmod(self.home / "bin" / "spud", 0o755)
        (self.home / ".claude" / "agents").mkdir(parents=True)
        shutil.copyfile(REPO / ".claude" / "agents" / "spudagent.md", self.home / ".claude" / "agents" / "spudagent.md")
        (self.home / "CLAUDE.md").write_text("# Spud (scratch home for a headless probe)\n\nYou are Spud in this probe. Follow the prompt's steps exactly.\n", encoding="utf-8")
        (self.home / ".gitignore").write_text(".spud/\n", encoding="utf-8")
        git(self.home, "init", "-q", "-b", "main")
        git(self.home, "add", ".")
        git(self.home, "commit", "-q", "-m", "scratch home")
        self.home_origin = self.root / "home-origin.git"
        git(self.root, "init", "-q", "--bare", "-b", "main", self.home_origin)
        git(self.home, "remote", "add", "origin", self.home_origin)
        git(self.home, "push", "-q", "-u", "origin", "main")
        self.spud("init")
        self.other.mkdir()
        (self.other / "README.md").write_text("# BadTakes stand-in\n", encoding="utf-8")
        (self.other / "src").mkdir()
        (self.other / "src" / ".keep").write_text("", encoding="utf-8")
        git(self.other, "init", "-q", "-b", "main")
        git(self.other, "add", ".")
        git(self.other, "commit", "-q", "-m", "stand-in")
        self.other_origin = self.root / "badtakes-origin.git"
        git(self.root, "init", "-q", "--bare", "-b", "main", self.other_origin)
        git(self.other, "remote", "add", "origin", self.other_origin)
        git(self.other, "push", "-q", "-u", "origin", "main")
        self.spud("--as", "spud", "project", "add", self.other, "--key", "badtakes", "--ticket-prefix", "BAD", "--team-prefix", "BADS", "--landing", "pr")
        self.install_out = self.spud("--as", "spud", "project", "install", "badtakes").stdout
        return self

    def agents_json(self):
        text = (self.home / ".claude" / "agents" / "spudagent.md").read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n(.*)\Z", text, re.S)
        front, body = (m.group(1), m.group(2)) if m else ("", text)
        desc = re.search(r"^description:\s*(.*)$", front, re.M)
        return json.dumps({"spudagent": {"description": desc.group(1).strip().strip('"') if desc else "A spudagent.", "prompt": body}})

    def claude(self, label, prompt, cwd=None, extra=(), allowed=None, max_turns=40):
        """One `claude -p` run: (session_id, the parsed stream, the result text)."""
        allowed = allowed or ["Bash(python3.14 -I -S *)", "Bash(git *)", "Bash(pwd)", "Bash(ls *)", "Write", "Read", "Edit", "Agent", "EnterWorktree", "ExitWorktree",
                              "ToolSearch", "Skill"]
        cmd = ["claude", "-p", prompt, "--model", MODEL, "--setting-sources", "project,local", "--output-format", "stream-json", "--verbose",
               "--strict-mcp-config", "--max-turns", str(max_turns), "--agents", self.agents_json(), "--allowedTools", ",".join(allowed), *extra]
        started = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, env=self.env, cwd=str(cwd or self.other), timeout=1800)
        stream_path = self.root / ("%s.stream.jsonl" % label)
        stream_path.write_text(proc.stdout, encoding="utf-8")
        (self.root / ("%s.stderr.txt" % label)).write_text(proc.stderr, encoding="utf-8")
        messages = []
        for line in proc.stdout.splitlines():
            try:
                messages.append(json.loads(line))
            except ValueError:
                continue
        session = next((m.get("session_id") for m in messages if m.get("session_id")), None)
        result = next((m for m in messages if m.get("type") == "result"), {})
        self.log.append({"run": label, "exit": proc.returncode, "seconds": round(time.time() - started), "session_id": session,
                         "cost_usd": result.get("total_cost_usd"), "stream": str(stream_path)})
        return session, messages, result.get("result")

    def db(self):
        con = sqlite3.connect("file:%s?mode=ro" % (self.home / ".spud" / "ledger.db"), uri=True)
        con.row_factory = sqlite3.Row
        return con

    def rows(self, sql, *params):
        con = self.db()
        try:
            return [dict(r) for r in con.execute(sql, params).fetchall()]
        finally:
            con.close()


def tool_calls(messages):
    """[(name, input, result text, is_error)] in order, subagent calls included (their parent_tool_use_id set)."""
    calls, by_id = [], {}
    for m in messages:
        content = (m.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                entry = {"name": block.get("name"), "input": block.get("input"), "result": None, "is_error": None, "parent": m.get("parent_tool_use_id")}
                by_id[block.get("id")] = entry
                calls.append(entry)
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                raw = block.get("content")
                text = raw if isinstance(raw, str) else "\n".join(c.get("text", "") for c in raw or [] if isinstance(c, dict))
                by_id[block["tool_use_id"]].update(result=text[:1200], is_error=bool(block.get("is_error")))
    return calls


def brief(calls):
    return [{"tool": c["name"], "input": {k: (v if len(str(v)) < 300 else str(v)[:300] + "...") for k, v in (c["input"] or {}).items()},
             "error": c["is_error"], "result": (c["result"] or "")[:500], "in_subagent": bool(c["parent"])} for c in calls]


def cli(s):
    return "python3.14 -I -S %s/bin/spud" % s.home


def sessionless_planned_member(s):
    """A planned member with no session, planned eleven minutes ago: it holds every Spud session's Stop (SPD-018)."""
    s.spud("--as", "spud", "ticket", "new", "--title", "Home probe ticket", "--status", "active", cwd=s.home)
    s.spud("--as", "spud", "member", "new", "--ticket", "SPD-001", "--persona", "scout", "--model", "haiku", "--brief", "Never spawned.", "--name", "Kestrel", cwd=s.home)
    con = sqlite3.connect(s.home / ".spud" / "ledger.db")
    try:
        with con:
            con.execute("UPDATE members SET planned_at = ?, session_id = NULL WHERE name = 'Kestrel'",
                        ((datetime.now().astimezone() - timedelta(minutes=11)).isoformat(timespec="seconds"),))
    finally:
        con.close()


def scenario_plain(s):
    sessionless_planned_member(s)
    prompt = """This is a scripted probe of the tools in this repository. Do these steps in order, one tool call each, and do not improvise or retry.
Step 0. Before any tool call, quote verbatim any line of session-start context that mentions "Spud project", or say NONE.
Step 1. Use the Write tool to create the file %(other)s/src/a.txt with the content: plain probe
Step 2. Use the Agent tool with subagent_type "general-purpose", description "Echo probe", model "haiku", isolation "worktree", and prompt "Reply with the single word ok. Use no tools."
Step 3. Use the Bash tool to run exactly: %(cli)s --as spud ticket new --title "Plain probe" --project badtakes
Step 4. Reply with one line per step: the step number, allowed or refused, and the first sentence of any refusal.""" % {"other": s.other, "cli": cli(s)}
    session, messages, result = s.claude("project-plain", prompt)
    calls = tool_calls(messages)
    return {
        "session_id": session,
        "model_reply": result,
        "calls": brief(calls),
        "src_a_written": (s.other / "src" / "a.txt").is_file(),
        "hook_errors": s.rows("SELECT body, data FROM events WHERE kind = 'hook.error'"),
        "spawn_requests_of_the_session": s.rows("SELECT tool_use_id, description, decision FROM spawn_requests WHERE session_id = ?", session),
        "stop_denials_of_the_session": s.rows("SELECT body FROM events WHERE kind = 'hook.denied' AND actor = 'hook:Stop' AND json_extract(data, '$.session_id') = ?", session),
        "bash_denials": s.rows("SELECT body FROM events WHERE kind = 'hook.denied' AND actor = 'hook:PreToolUse'"),
        "bad_tickets": s.rows("SELECT key FROM tickets WHERE key LIKE 'BAD-%'"),
        "session_start_notice_in_stream": [m for m in messages if "Spud project" in json.dumps(m)][:3],
    }


CHILD_SPUD = """You are Russet (01), a scout spudagent on BAD-001. Your SubagentStart context names your agent_id: every spud command takes --as <that agent_id>.
Do these steps in order, one tool call each; a refusal is expected for two of them, so continue after it:
a. Write tool: create %(other)s/src/hello.txt with the content: hello
b. Write tool: overwrite %(other)s/README.md with the content: changed
c. Write tool: create %(home)s/docs/x.md with the content: x
d. Write tool: create %(home)s/bin/x with the content: x
e. Bash tool: %(cli)s --as <your agent_id> member result "a to d done as the hooks allowed"
Then reply with one line per step: the letter, allowed or refused, and the first sentence of any refusal."""


def scenario_spud(s):
    child = CHILD_SPUD % {"other": s.other, "home": s.home, "cli": cli(s)}
    prompt = """This is a scripted probe. Do these steps in order, one tool call each, without improvising; quote each tool's first output line as you go.
Step 1. Bash: %(cli)s --as spud session claim
Step 2. Bash: %(cli)s --as spud ticket new --title "Probe ticket" --status active --brief "A headless probe."
Step 3. Bash: %(cli)s --as spud member new --ticket BAD-001 --persona scout --model haiku --name Russet --brief "Write the four probe files and record a result." --deliverable 'src/**' --deliverable 'home:docs/x.md'
Step 4. Agent tool with subagent_type "spudagent", model "haiku", description "BADS-001/Russet (01, scout)", and this exact prompt:
<<<
%(child)s
>>>
Wait for it to finish.
Step 5. Bash: %(cli)s --as spud member finish BADS-001/Russet --status done --outcome "Probe run." --summary "Wrote the probe files in the stand-in repository and in the home as its globs allowed, and recorded its result through the ledger CLI from the child session."
Step 6. Bash: %(cli)s --as spud render
Step 7. Reply with one line per step: the step number and its first output line or refusal.""" % {"cli": cli(s), "child": child}
    session, messages, result = s.claude("project-spud", prompt, max_turns=60)
    calls = tool_calls(messages)
    ticket_note = s.home / "ledger" / "tickets" / "BAD-001.md"
    return {
        "session_id": session,
        "model_reply": result,
        "calls": brief(calls),
        "claims": s.rows("SELECT session_id, claimed_at, released_at FROM sessions"),
        "member": s.rows("SELECT name, status, agent_id, result, session_id FROM members WHERE name = 'Russet'"),
        "files": {"other/src/hello.txt": (s.other / "src" / "hello.txt").is_file(), "other/README.md changed": (s.other / "README.md").read_text(encoding="utf-8") != "# BadTakes stand-in\n",
                  "home/docs/x.md": (s.home / "docs" / "x.md").is_file(), "home/bin/x": (s.home / "bin" / "x").exists()},
        "denials": s.rows("SELECT agent_id, body FROM events WHERE kind = 'hook.denied'"),
        "hook_errors": s.rows("SELECT body FROM events WHERE kind = 'hook.error'"),
        "home_log_1": git(s.home, "log", "-1", "--format=%s"),
        "origin_has_it": git(s.home_origin, "log", "-1", "--format=%s", "main"),
        "ticket_note_project_line": [l for l in ticket_note.read_text(encoding="utf-8").splitlines() if l.startswith("project:")] if ticket_note.is_file() else None,
    }


CHILD_WORKTREE = """You are Yukon (01), a scout spudagent on BAD-001. Your SubagentStart context names your agent_id: every spud command takes --as <that agent_id>.
a. Bash tool: pwd
b. Write tool: create the file src/w.txt, as an absolute path under the directory pwd printed, with the content: worktree
c. Bash tool: %(cli)s --as <your agent_id> member result "wrote src/w.txt in the worktree"
Then reply with one line per step: the letter and allowed or refused."""


def scenario_worktree(s):
    (s.other / ".claude" / "agents").mkdir(parents=True, exist_ok=True)
    (s.other / ".claude" / "agents" / "probeagent.md").write_text("---\nname: probeagent\ndescription: A probe agent; reply ok.\n---\nReply with the single word ok. Use no tools.\n", encoding="utf-8")
    child = CHILD_WORKTREE % {"cli": cli(s)}
    prompt = """This is a scripted probe. Do these steps in order, one tool call each, without improvising; quote each tool's first output line as you go. The EnterWorktree and ExitWorktree tools may need ToolSearch first.
Step 1. Bash: %(cli)s --as spud session claim
Step 2. Bash: %(cli)s --as spud ticket new --title "Worktree probe" --status active --brief "A headless probe in a worktree."
Step 3. EnterWorktree with name "bad-001-probe".
Step 4. Bash: pwd
Step 5. Bash: %(cli)s --as spud member new --ticket BAD-001 --persona scout --model haiku --name Yukon --brief "Write src/w.txt in the worktree and record a result." --deliverable 'src/**'
Step 6. Agent tool with subagent_type "spudagent", model "haiku", description "BADS-001/Yukon (01, scout)", and this exact prompt:
<<<
%(child)s
>>>
Wait for it to finish.
Step 7. Agent tool with subagent_type "probeagent", model "haiku", description "Probe agent", prompt "Reply ok."
Step 8. Bash: git -C %(home)s log --oneline -1
Step 9. Bash: %(cli)s --as spud project show badtakes
Step 10. Bash: %(cli)s --as spud render
Step 11. ExitWorktree with action "keep".
Step 12. Bash: %(cli)s --as spud member finish BADS-001/Yukon --status done --outcome "Probe run." --summary "Wrote src/w.txt in a worktree of the stand-in repository, bound from the worktree's working directory, and recorded its result through the ledger CLI."
Step 13. Bash: %(cli)s --as spud render
Step 14. Reply with one line per step: the step number and its first output line or refusal.""" % {"cli": cli(s), "child": child, "home": s.home}
    session, messages, result = s.claude("project-worktree", prompt, max_turns=70)
    calls = tool_calls(messages)
    worktree = s.other / ".claude" / "worktrees" / "bad-001-probe"
    out = {
        "session_id": session,
        "model_reply": result,
        "calls": brief(calls),
        "worktree_exists": worktree.is_dir(),
        "w_txt": [str(p) for p in s.other.parent.rglob("w.txt")],
        "member": s.rows("SELECT name, status, agent_id, result FROM members WHERE name = 'Yukon'"),
        "member_started_cwd": s.rows("SELECT json_extract(data, '$.cwd') AS cwd FROM events WHERE kind = 'member.started'"),
        "spawn_requests": s.rows("SELECT description, decision, reason FROM spawn_requests WHERE session_id = ?", session),
        "denials": s.rows("SELECT body FROM events WHERE kind = 'hook.denied'"),
        "hook_errors": s.rows("SELECT body FROM events WHERE kind = 'hook.error'"),
        "commits": s.rows("SELECT body, json_extract(data, '$.pushed') AS pushed FROM events WHERE kind = 'commit'"),
        "home_log_1": git(s.home, "log", "-1", "--format=%s"),
    }
    if session:
        resumed_session, resumed, reply = s.claude("project-worktree-resume", "Run this Bash command and reply with its output: %s session show" % cli(s), extra=["--resume", session], max_turns=6)
        out["resume"] = {"session_id": resumed_session, "same_id": resumed_session == session, "reply": reply, "calls": brief(tool_calls(resumed))}
        forked_session, forked, reply = s.claude("project-worktree-fork", "Run this Bash command and reply with its output: %s session show" % cli(s),
                                                 extra=["--resume", session, "--fork-session"], max_turns=6)
        out["fork"] = {"session_id": forked_session, "same_id": forked_session == session, "reply": reply, "calls": brief(tool_calls(forked))}
    return out


def scenario_discovery(s):
    """A session started inside a worktree of the other repository: does it find the project agent of the main checkout (the
    design's [assumed] worktree-root agent discovery), and do the main checkout's local settings load there (G6)?"""
    (s.other / ".claude" / "agents").mkdir(parents=True, exist_ok=True)
    (s.other / ".claude" / "agents" / "probeagent.md").write_text("---\nname: probeagent\ndescription: A probe agent; reply ok.\n---\nReply with the single word ok. Use no tools.\n", encoding="utf-8")
    worktree = s.other / ".claude" / "worktrees" / "desk"
    git(s.other, "worktree", "add", "-q", "-b", "worktree-desk", worktree)
    prompt = """This is a scripted probe. Step 1: list the subagent types available to your Agent tool, by name only, one per line. Step 2: use the Agent tool with subagent_type "probeagent", model "haiku", description "Probe agent", prompt "Reply ok." Step 3: Bash: %s --as spud ticket new --title "From a worktree session" --project badtakes. Step 4: reply with what each step returned.""" % cli(s)
    session, messages, result = s.claude("discovery", prompt, cwd=worktree, max_turns=12)
    return {"session_id": session, "model_reply": result, "calls": brief(tool_calls(messages)),
            "local_settings_in_worktree": (worktree / ".claude" / "settings.local.json").exists(),
            "denials": s.rows("SELECT body FROM events WHERE kind = 'hook.denied'"), "bad_tickets": s.rows("SELECT key FROM tickets WHERE key LIKE 'BAD-%'")}


def scenario_skill(s):
    """disable-model-invocation (design section 2.5): the /spud skill at project scope in the stand-in repository (a user-scope
    skill cannot be redirected in a headless run, and the key's meaning does not depend on the scope)."""
    skill = s.other / ".claude" / "skills" / "spud"
    skill.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(s.user / "skills" / "spud" / "SKILL.md", skill / "SKILL.md")
    out = {}
    session, messages, result = s.claude("skill-model", "Become Spud in this session, using a skill if you have one for that; if you have no such skill, reply NO SKILL. Do not run any other command.", max_turns=6)
    out["asked_without_slash"] = {"session_id": session, "reply": result, "calls": brief(tool_calls(messages)), "claims": s.rows("SELECT session_id FROM sessions")}
    session, messages, result = s.claude("skill-slash", "/spud", max_turns=12)
    out["typed_slash_spud"] = {"session_id": session, "reply": result, "calls": brief(tool_calls(messages)), "claims": s.rows("SELECT session_id, claimed_at FROM sessions")}
    return out


SCENARIOS = {"project-plain": scenario_plain, "project-spud": scenario_spud, "project-worktree": scenario_worktree, "discovery": scenario_discovery, "skill": scenario_skill}


def main(argv):
    if len(argv) != 1 or argv[0] not in SCENARIOS:
        print("usage: headless.py %s" % " | ".join(SCENARIOS), file=sys.stderr)
        return 2
    s = Scratch(argv[0]).build()
    try:
        observed = SCENARIOS[argv[0]](s)
    finally:
        events = s.rows("SELECT id, at, actor, kind, agent_id, body, data FROM events ORDER BY id")
        (s.root / "events.json").write_text(json.dumps(events, indent=2), encoding="utf-8")
    summary = {"scenario": argv[0], "scratch": str(s.root), "claude": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip(),
               "runs": s.log, "install": s.install_out, "observed": observed}
    (s.root / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
