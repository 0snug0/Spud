"""What effort a subagent spawned through the Agent tool runs at (SPD-222).  A real `claude -p` session, real API usage.

  python3.14 -I -S tests/probes/subagent_effort.py SCENARIO [--model haiku] [--opus-model ID] [--claude PATH] [--root DIR]

Builds a scratch directory (no git, no ledger) holding project-scope agent definitions, written as files the way
`~/.claude/agents/spudagent.md` is: `probe-plain`, whose frontmatter sets no effort (the spudagent before SPD-222), and
`probe-high`, whose frontmatter says `effort: high` -- or, for the `variants` scenario, the base `spudagent` and its five
effort variants.  The main session (`--model`, default haiku) is told to call the Agent tool once per
row of the scenario, in the foreground, each child asked for one word.  Claude Code stamps every assistant entry of a
transcript with the effort it ran that turn at (`effort`, `perTurnEffort`; seen in 2.1.276), so the probe reads each
child's transcript under ~/.claude/projects/<cwd>/<session>/subagents/ and prints its agent type, the model it asked for
and the model that answered, the effort recorded, whether any turn was an API error, and each model's cost from the
result line.

Scenarios:
  frontmatter  the session's effort set to `low` by settings (`effortLevel`, the way ~/.claude/settings.json sets it);
               probe-plain and probe-high each spawned on haiku, sonnet and opus.  Does a definition's `effort:` beat
               the session's level, does a plain definition inherit it, and what does `effort: high` do on haiku?
  unset        no effort anywhere (user settings are not read); probe-plain and probe-high spawned on opus, probe-plain
               on sonnet.  What an Opus 5.5 child runs at when nothing sets one: its own default (medium) or the
               session's?  Run it with `--model sonnet` too: a session model that supports effort (default high), as
               Fable does, against haiku, which supports none.
  variants     the definitions `project install` writes since SPD-222 made effort a per-member choice: `spudagent`, the
               base, and `spudagent-low` ... `spudagent-max`, rendered by the program's own projects/agentdef.variant_markdown
               from a base whose frontmatter is share/agents/spudagent.md's (its body swapped for the probe's one line, so
               a child does not start the ledger protocol); the session's effort `low` by settings.  spudagent-high and
               spudagent-max on opus, spudagent-medium on sonnet, the base on opus, spudagent-high on haiku, each by
               subagent_type: does each variant run at its own level, whatever the session's, and the base at the session's?

`--claude` names the Claude Code binary (default `claude` on PATH).  Which model the `opus` alias reaches depends on it:
2.1.276 resolves it to Opus 5 (claude-opus-5) and answers a request for Opus 5.5 with "API Error: 400 Claude Code 2.1.276
does not support this model; version 2.1.280"; 2.1.280, the version the desktop app bundles, resolves it to Opus 5.5, the
only Opus that defaults to medium.  `--opus-model` sets ANTHROPIC_DEFAULT_OPUS_MODEL for the session, pinning the model
the `opus` children run on.

User settings (~/.claude/settings.json, where an `effortLevel` may sit) are left out with `--setting-sources
project,local`; CLAUDE_CODE_EFFORT_LEVEL, which beats every other source, is removed from the session's environment.
Documented behaviour (code.claude.com/docs/en/sub-agents, model-config): frontmatter `effort` "overrides the session
effort level", default "inherits from session"; levels low, medium, high, xhigh, max; "Models not listed do not support
effort" (Haiku 4.5 is not listed).  Run one scenario at a time; each costs a few cents on haiku.
"""

import argparse
import glob
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.dont_write_bytecode = True  # the variants scenario loads the program, which must leave no bytecode in the checkout

REPO = Path(__file__).resolve().parents[2]
CHILD_PROMPT = "Reply with the single word OK and nothing else."
AGENT_BODY = "You are a probe subagent. Do exactly what the prompt says and nothing more."
AGENTS = {"probe-plain": None, "probe-high": "high"}
SCENARIOS = {
    "frontmatter": {"effortLevel": "low", "spawns": [("probe-plain", "haiku"), ("probe-plain", "sonnet"), ("probe-plain", "opus"),
                                                     ("probe-high", "haiku"), ("probe-high", "sonnet"), ("probe-high", "opus")]},
    "unset": {"effortLevel": None, "spawns": [("probe-plain", "opus"), ("probe-high", "opus"), ("probe-plain", "sonnet")]},
    "variants": {"effortLevel": "low", "agents": "variants",
                 "spawns": [("spudagent-high", "opus"), ("spudagent-max", "opus"), ("spudagent-medium", "sonnet"),
                            ("spudagent", "opus"), ("spudagent-high", "haiku")]},
}


def load_program():
    """bin/spud_ledger.py, loaded the way tests/helpers.load_spud_module loads it: its names are the package's."""
    loader = importlib.machinery.SourceFileLoader("spud_ledger", str(REPO / "bin" / "spud_ledger.py"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("spud_ledger", loader))
    loader.exec_module(module)
    return module


def variant_files():
    """{agent type: text} of the base and its five effort variants as projects/agentdef renders them (SPD-222), from
    share/agents/spudagent.md's own frontmatter with the probe's one-line body."""
    spud = load_program()
    shipped = (REPO / "share" / "agents" / "spudagent.md").read_text(encoding="utf-8")
    end = shipped.index("\n---\n", 4) + len("\n---\n")
    base = shipped[:end] + "\n" + AGENT_BODY + "\n"
    files = {spud.SPUDAGENT: base}
    files.update((name, spud.variant_markdown(base, level)) for name, level in zip(spud.SPUDAGENT_VARIANTS, spud.EFFORTS))
    return files
DROPPED = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EFFORT_LEVEL",
           "CLAUDE_CODE_SUBAGENT_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL")


def agent_file(name, effort):
    front = ["---", "name: %s" % name, "description: SPD-222 effort probe agent; spawn only when told to by name.",
             "model: inherit"]
    if effort:
        front.append("effort: %s" % effort)
    return "\n".join(front + ["---", "", AGENT_BODY, ""])


def prompt_for(spawns):
    rows = "\n".join("%d. subagent_type \"%s\", model \"%s\", description \"%s on %s\"" % (i, a, m, a, m)
                     for i, (a, m) in enumerate(spawns, 1))
    return ("Make exactly these Agent tool calls, one at a time, in this order, each in the foreground (run_in_background "
            "false) and each with the prompt \"%s\". Do not skip, repeat or change any of them.\n%s\n"
            "When every call has returned, reply with the single word DONE." % (CHILD_PROMPT, rows))


def transcript_for(session_id):
    found = glob.glob(os.path.expanduser("~/.claude/projects/*/%s.jsonl" % session_id))
    return found[0] if found else None


def read_child(path):
    """(agent type, requested model, [(answering model, effort, perTurnEffort)], errors, text) of one child transcript."""
    meta_path = path[:-len(".jsonl")] + ".meta.json"
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    turns, errors, text = [], [], []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if entry.get("type") != "assistant":
                continue
            message = entry.get("message") or {}
            turns.append((message.get("model"), entry.get("effort", "<absent>"), entry.get("perTurnEffort", "<absent>")))
            if entry.get("isApiErrorMessage") or entry.get("error"):
                errors.append(str(entry.get("error") or message.get("content"))[:300])
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    text.append(block.get("text", ""))
    return meta.get("agentType"), meta.get("model"), turns, errors, " ".join(text).strip()[:80]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", choices=sorted(SCENARIOS))
    ap.add_argument("--model", default="haiku", help="the main session's model (default haiku)")
    ap.add_argument("--opus-model", help="ANTHROPIC_DEFAULT_OPUS_MODEL for the session: the model the `opus` children run on")
    ap.add_argument("--claude", default="claude", help="the Claude Code binary to run (default `claude` on PATH)")
    ap.add_argument("--root", help="scratch directory (default $TMPDIR/spud-probes)")
    args = ap.parse_args(argv)
    if shutil.which(args.claude) is None:
        raise SystemExit("%s is not an executable Claude Code" % args.claude)
    scenario = SCENARIOS[args.scenario]
    base = os.path.realpath(args.root or os.path.join(os.environ.get("TMPDIR", "/tmp"), "spud-probes"))
    root = os.path.join(base, "subagent-effort-%s-%s-%s" % (args.scenario, args.model, time.strftime("%Y%m%dT%H%M%S")))
    session_dir = os.path.join(root, "session")
    os.makedirs(os.path.join(session_dir, ".claude", "agents"))
    files = variant_files() if scenario.get("agents") == "variants" else {name: agent_file(name, effort) for name, effort in AGENTS.items()}
    for name, text in files.items():
        with open(os.path.join(session_dir, ".claude", "agents", name + ".md"), "w", encoding="utf-8") as f:
            f.write(text)
    settings = {} if scenario["effortLevel"] is None else {"effortLevel": scenario["effortLevel"]}
    settings_path = os.path.join(root, "settings.json")
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    env = {k: v for k, v in os.environ.items() if k not in DROPPED}
    if args.opus_model:
        env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = args.opus_model
    version = subprocess.run([args.claude, "--version"], capture_output=True, text=True, env=env).stdout.strip()
    session_id = str(uuid.uuid4())
    cmd = [args.claude, "-p", prompt_for(scenario["spawns"]), "--settings", settings_path, "--setting-sources", "project,local",
           "--strict-mcp-config", "--tools", "Agent", "--allowedTools", "Agent", "--model", args.model,
           "--session-id", session_id, "--output-format", "json"]
    started = time.time()
    proc = subprocess.run(cmd, env=env, cwd=session_dir, capture_output=True, text=True)
    elapsed = time.time() - started
    with open(os.path.join(root, "stdout.json"), "w", encoding="utf-8") as f:
        f.write(proc.stdout)
    lines = ["subagent-effort probe %s in %s (%.0f s, exit %d), %s" % (args.scenario, root, elapsed, proc.returncode, version),
             "session model %s; settings %s; user settings not read; ANTHROPIC_DEFAULT_OPUS_MODEL %s" % (
                 args.model, json.dumps(settings), args.opus_model or "unset")]
    if proc.stderr.strip():
        lines.append("stderr: %s" % proc.stderr.strip()[:1000])
    try:
        messages = json.loads(proc.stdout)
    except ValueError:
        messages = []
        lines.append("stdout (not JSON): %s" % proc.stdout[:1000])
    messages = messages if isinstance(messages, list) else [messages]
    result = next((m for m in reversed(messages) if isinstance(m, dict) and m.get("type") == "result"), {})
    lines.append("result: %r; cost_usd %s" % (str(result.get("result", ""))[:80], result.get("total_cost_usd")))
    for model, usage in sorted((result.get("modelUsage") or {}).items()):
        lines.append("  %s cost_usd %s, output tokens %s" % (model, usage.get("costUSD"), usage.get("outputTokens")))
    transcript = transcript_for(session_id)
    lines.append("--- main transcript %s ---" % transcript)
    if transcript:
        with open(transcript, encoding="utf-8") as f:
            main_turns = [json.loads(raw) for raw in f if raw.strip()]
        efforts = sorted({(e.get("message", {}).get("model"), str(e.get("effort", "<absent>"))) for e in main_turns if e.get("type") == "assistant"})
        lines.append("main session turns (model, effort): %s" % efforts)
        children = sorted(glob.glob(os.path.join(transcript[:-len(".jsonl")], "subagents", "agent-*.jsonl")), key=os.path.getmtime)
        lines.append("--- %d child transcripts, in the order they were written ---" % len(children))
        for path in children:
            agent_type, requested, turns, errors, text = read_child(path)
            recorded = sorted({"%s effort=%s perTurn=%s" % t for t in turns})
            lines.append("%s asked %s: %s; errors %s; said %r" % (agent_type, requested, "; ".join(recorded) or "no assistant turn",
                                                              errors or "none", text))
    summary = "\n".join(lines)
    with open(os.path.join(root, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(summary + "\n")
    print(summary)
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
