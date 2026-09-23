"""hooks/hookio: Hook constants, HookError and HookOutput, the spool."""

import contextlib
import fcntl
import json
import os
import re

from ..core import kernel
from ..state import ledgerdb


# ----------------------------------------------------------------------------
# Hooks: the harness's events, the laws as refusals
# ----------------------------------------------------------------------------
#
# `spud hook <event>` reads the harness payload on stdin and answers the documented way:
# JSON on stdout with exit 0, or exit 2 with the reason on stderr.  Enforcing hooks
# (PreToolUse for Agent, Bash and the edit tools; Stop) fail closed: a planned refusal is
# `permissionDecision: deny` with the reason (so the model reads why), anything unexpected
# is exit 2.  Recording hooks (PostToolUse for Agent, SubagentStart, SubagentStop,
# SessionStart, UserPromptSubmit) fail open: exit 0 whatever happens, the gap written to the spool
# <SPUD_HOME>/.spud/hook-errors.jsonl and drained into `hook.error` events by the next
# successful hook or CLI command.  One short BEGIN IMMEDIATE transaction per hook.

HOOK_EVENTS = ("PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop", "SessionStart", "Stop", "UserPromptSubmit")
EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
ENFORCED_TOOLS = ("Agent", "Bash") + EDIT_TOOLS
# Spud's hand-written set (Law 1, as corrected when the hooks were designed); everything else in the
# repository is a deliverable, and ledger/** and reports/** are generated.
SPUD_PATHS = ("spud.config.json", "CLAUDE.md", ".claude/**", "docs/superpowers/specs/**",
              "ledger/Home.md", "ledger/Spud.md", "ledger/*.base", "ledger/_templates/**")
GENERATED_ROOTS = ("ledger", "reports")
DESCRIPTION = re.compile(r"^\s*(?P<team>[A-Z][A-Z0-9]*-\d+)/(?P<name>[A-Za-z][\w-]*)\s*\(\s*(?P<lineage>\d+(?:\.\d+)*)\s*,\s*(?P<persona>[a-z]+)\s*\)\s*$")
AGENT_ID_RE = re.compile(r"^[0-9a-f]{17}$")
SPUD_COMMANDS = ("init", "migrate", "backup", "schedule", "doctor", "config", "settings", "import", "render", "ticket", "member",
                 "proposal", "handoff", "report", "board", "fleet", "card", "events", "sql", "hook", "project", "session", "home", "pr",
                 "vault")  # the home's Obsidian vault, installed from the tool's share/ and captured back into it
SPUD_ONLY_COMMANDS = ("init", "migrate", "import", "render", "backup", "schedule")
SPUD_ONLY_SUBCOMMANDS = (("settings", "sync"), ("config", "sync"), ("ticket", "new"), ("ticket", "move"), ("ticket", "edit"), ("member", "resum"),
                         ("project", "add"), ("project", "edit"), ("project", "install"), ("project", "uninstall"), ("project", "sync"),
                         ("project", "remove"), ("session", "claim"), ("session", "release"), ("home", "move"),
                         # `home sync` rewrites the home's own CLAUDE.md, notes, templates and views, every one
                         # of them a SPUD_PATHS file the edit hook already refuses a member.
                         ("home", "sync"),
                         ("pr", "record"),  # opening a pull request is part of landing, which is Spud's (Law 10)
                         # Installing the vault writes the home's own .obsidian/, which is Spud's directory; the
                         # other half, `vault capture`, is a member's and writes only into that member's deliverables.
                         ("vault", "install"))
# The spud calls a plain session in another project may run with `--as spud`: the ones that write nothing an actor
# owns.  `board` stays here although the full board reconciles recorded pull requests: that write
# names the actor `reconcile` and no judgment, and board takes no `--as` of its own, so Law 6 has nothing to refuse.
READ_ONLY_COMMANDS = ("board", "fleet", "card", "events", "sql", "doctor")
READ_ONLY_SUBCOMMANDS = (("ticket", "show"), ("member", "show"), ("member", "list"), ("proposal", "list"), ("project", "list"), ("project", "show"),
                         ("session", "show"), ("schedule", "show"), ("pr", "list"))
MEMBER_OWN_COMMANDS = (("member", "log"), ("member", "result"), ("member", "block"), ("proposal", "file"))
DB_PATH_RE = re.compile(r"ledger\.db|(?:^|[\s/'\"=])\.spud(?:/|$|[\s'\"])", re.IGNORECASE)
# The ledger state directory at a project root: the database, its WAL and shm files, the worktree list cache, the backups
# and the launcher's cached bytecode (bin/spud).  Only the CLI writes there; the Bash hook and the edit hook refuse it to
# everyone in the same words.
STATE_DIR = ".spud"
DB_REASON = ("direct access to the ledger database is refused (%s); the inspection path is `spud sql --readonly '<statement>'`,"
             " and every write goes through the spud CLI")
SUBST = "__SPUD_SUBST__"  # what a lifted `$(...)` or backtick body leaves behind in the outer line


class HookError(Exception):
    """A hook could not do its job (malformed payload, mismatched event)."""


class HookOutput:
    def __init__(self, obj=None, exit_code=kernel.EXIT_OK):
        self.obj = obj
        self.exit_code = exit_code


SILENT = HookOutput()


def pre_decision(decision, reason):
    return HookOutput({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}})


# -- the spool: a recording hook's gap, written later as a hook.error event ----------


def spool_path(ctx):
    return ctx.home / ".spud" / "hook-errors.jsonl"


@contextlib.contextmanager
def spool_lock(ctx):
    fd = os.open(str(ctx.home / ".spud" / "hook-errors.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def spool_write(ctx, record):
    """Append one gap; only when .spud/ exists (before `spud init` there is nothing to drain into)."""
    if not (ctx.home / ".spud").is_dir():
        return False
    with spool_lock(ctx):
        with open(spool_path(ctx), "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    return True


def spool_drain(ctx):
    """Move every spooled gap into a hook.error event.  Returns the count."""
    path = spool_path(ctx)
    if not ctx.db_path.is_file() or not path.is_file() or path.stat().st_size == 0:
        return 0
    with spool_lock(ctx):
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        with open(path, "w", encoding="utf-8"):
            pass
    if not lines:
        return 0
    records = []
    for line in lines:
        try:
            record = json.loads(line)
            records.append(record if isinstance(record, dict) else {"error": "spool record is not an object: %r" % line[:200]})
        except ValueError:
            records.append({"error": "unreadable spool line: %r" % line[:200]})
    try:
        con = ledgerdb.connect(ctx)
        try:
            with ledgerdb.write_txn(con):
                for r in records:
                    agent_id = r.get("agent_id") if isinstance(r.get("agent_id"), str) and r.get("agent_id") else None
                    member = con.execute("SELECT * FROM members WHERE agent_id = ?", (agent_id,)).fetchone() if agent_id else None
                    ledgerdb.write_event(con, r.get("at") or kernel.now(), "hook:%s" % (r.get("event") or "unknown"), "hook.error", str(r.get("error", "")),
                                ticket_id=member["ticket_id"] if member else None, member_id=member["id"] if member else None,
                                agent_id=agent_id, data=r)
        finally:
            con.close()
    except Exception:
        with spool_lock(ctx):
            with open(path, "a", encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")
        raise
    return len(records)
