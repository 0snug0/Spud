"""How large a SessionStart additionalContext reaches the model whole (SPD-048).  A real `claude -p` session, real API usage.

  python3.14 -I -S tests/probes/context_limit.py SIZE [--filler ascii|latin|emoji] [--model haiku] [--root DIR]

Builds a scratch directory (no git, no ledger) whose only hook is a SessionStart command that prints one JSON object: an
additionalContext of exactly SIZE code points, a start marker on its first line, numbered filler lines, and an end marker on
its last line, each marker carrying its own random token so the model cannot guess one from the other.  `--filler` picks the
filler's character: `ascii` (one UTF-8 byte, one UTF-16 unit), `latin` (é: two bytes, one unit) or `emoji` (four bytes, two
units), so two runs at one SIZE tell whether the harness counts code points, UTF-16 units or bytes.  The session runs with
every tool disabled (it cannot open a file the harness saved the context to), no user settings, no MCP servers, and a
prompt asking it to quote both markers and the last filler line it sees.

Prints the text's size in code points, UTF-16 units and UTF-8 bytes, `claude --version`, the model's answer, and what the
session's own transcript recorded: whether the SessionStart attachment holds the end marker, its length, and its head and
tail.  The documented cap (code.claude.com/docs/en/hooks, JSON output) is 10,000 characters, past which the text is saved
to a file and the model gets a preview and the path.  Run one at a time; each run costs a few cents on haiku.
"""

import argparse
import glob
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import time
import uuid

FILLERS = {"ascii": "x", "latin": "é", "emoji": "\U0001F954"}

PROMPT = (
    "A SessionStart hook gave you additional context whose first line is a start marker (CTX-START-...) and whose last line, "
    "if you received it, is an end marker (CTX-END-...). Between them are numbered filler lines. Do not guess. Answer in exactly "
    "four lines and nothing else:\n"
    "START: <the start marker exactly as you see it, or NONE>\n"
    "END: <the end marker exactly as you see it, or NONE>\n"
    "LAST FILLER: <the number of the last filler line you can see, or NONE>\n"
    "FILE: <the file path you were given instead of the full text, or NONE>"
)


def utf16_units(text):
    return len(text.encode("utf-16-le")) // 2


def build_text(size, filler):
    """Exactly `size` code points: the start marker, filler lines `NNNNN ` + filler characters, the end marker."""
    start = "CTX-START-%s" % secrets.token_hex(6)
    end = "CTX-END-%s" % secrets.token_hex(6)
    budget = size - len(start) - len(end) - 2  # the two newlines around the body
    if budget < 0:
        raise SystemExit("SIZE %d is too small for the two markers (%d)" % (size, len(start) + len(end) + 2))
    lines, used, n = [], 0, 1
    while True:
        line = "%05d " % n + FILLERS[filler] * 58
        cost = len(line) + (1 if lines else 0)
        if used + cost > budget:
            break
        lines.append(line)
        used += cost
        n += 1
    body = "\n".join(lines)
    body += FILLERS[filler] * (budget - len(body))  # pad the last line to the exact size
    text = start + "\n" + body + "\n" + end
    assert len(text) == size, (len(text), size)
    return text, start, end, len(lines)


def transcript_for(session_id):
    found = glob.glob(os.path.expanduser("~/.claude/projects/*/%s.jsonl" % session_id))
    return found[0] if found else None


def session_start_attachments(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            attachment = entry.get("attachment") if isinstance(entry, dict) else None
            if isinstance(attachment, dict) and attachment.get("hookEvent") == "SessionStart":
                out.append(attachment)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("size", type=int, help="the additionalContext's length in code points")
    ap.add_argument("--filler", choices=sorted(FILLERS), default="ascii")
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--root", help="scratch directory (default $TMPDIR/spud-probes)")
    args = ap.parse_args(argv)
    if shutil.which("claude") is None:
        raise SystemExit("claude is not on PATH")
    base = os.path.realpath(args.root or os.path.join(os.environ.get("TMPDIR", "/tmp"), "spud-probes"))
    root = os.path.join(base, "context-limit-%d-%s-%s" % (args.size, args.filler, time.strftime("%Y%m%dT%H%M%S")))
    os.makedirs(os.path.join(root, "session"))
    text, start, end, filler_lines = build_text(args.size, args.filler)
    output = os.path.join(root, "hook-output.json")
    with open(output, "w", encoding="utf-8") as f:
        json.dump({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}, f)
    settings = os.path.join(root, "settings.json")
    with open(settings, "w", encoding="utf-8") as f:
        json.dump({"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "cat %s" % shlex.quote(output)}]}]}}, f, indent=2)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_ENTRYPOINT")}
    version = subprocess.run(["claude", "--version"], capture_output=True, text=True, env=env).stdout.strip()
    session_id = str(uuid.uuid4())
    cmd = ["claude", "-p", PROMPT, "--settings", settings, "--setting-sources", "project,local", "--strict-mcp-config", "--tools", "",
           "--model", args.model, "--session-id", session_id, "--output-format", "json"]
    started = time.time()
    proc = subprocess.run(cmd, env=env, cwd=os.path.join(root, "session"), capture_output=True, text=True)
    elapsed = time.time() - started
    with open(os.path.join(root, "stdout.json"), "w", encoding="utf-8") as f:
        f.write(proc.stdout)
    lines = ["context-limit probe in %s (%.0f s, exit %d), %s" % (root, elapsed, proc.returncode, version),
             "additionalContext: %d code points, %d UTF-16 units, %d UTF-8 bytes; filler %s, %d numbered lines" % (
                 len(text), utf16_units(text), len(text.encode("utf-8")), args.filler, filler_lines),
             "markers: %s ... %s" % (start, end)]
    try:
        messages = json.loads(proc.stdout)
    except ValueError:
        messages = []
        lines.append("stdout (not JSON): %s" % proc.stdout[:1000])
    messages = messages if isinstance(messages, list) else [messages]  # 2.1.274 prints the session's messages as one array
    result = next((m for m in reversed(messages) if isinstance(m, dict) and m.get("type") == "result"), {})
    if proc.stderr.strip():
        lines.append("stderr: %s" % proc.stderr.strip()[:1000])
    answer = str(result.get("result", ""))
    lines.append("cost_usd %s" % result.get("total_cost_usd"))
    lines.append("--- the model's answer ---")
    lines.append(answer)
    lines.append("model quoted the end marker: %s" % (end in answer))
    transcript = transcript_for(session_id)
    lines.append("--- transcript %s ---" % transcript)
    if transcript:
        for attachment in session_start_attachments(transcript):
            content = attachment.get("content")
            delivered = "\n".join(str(c) for c in content) if isinstance(content, list) else str(content)
            lines.append("attachment type %s, %d code points; start marker %s, end marker %s, identical to the text %s" % (
                attachment.get("type"), len(delivered), start in delivered, end in delivered, delivered == text))
            lines.append("head: %r" % delivered[:300])
            lines.append("tail: %r" % delivered[-300:])
    summary = "\n".join(lines)
    with open(os.path.join(root, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(summary + "\n")
    print(summary)
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
