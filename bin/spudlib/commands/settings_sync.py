"""commands/settings_sync: settings sync, and the settings merge project install shares.  Moved from bin/spud_ledger.py (SPD-065)."""

import json
import re
import shlex
import sys
from pathlib import Path

from ..core import homeconf, kernel
from ..projects import sessions
from ..state import ledgerdb


# The hook table of the spike's Enforcement plan (SPD-006), installed by `spud settings sync`.
HOOK_TABLE = (
    ("PreToolUse", "Agent"),
    ("PreToolUse", "Bash"),
    ("PreToolUse", "Write|Edit|MultiEdit|NotebookEdit"),
    ("PostToolUse", "Agent"),
    ("SubagentStart", None),
    ("SubagentStop", None),
    ("SessionStart", "startup|resume|clear|compact"),  # clear since SPD-011: a /clear gets the board too
    ("Stop", None),
    ("UserPromptSubmit", None),  # SPD-057: a prompt naming a claim project's ticket claims the session; the event takes no matcher
)
HOOK_TIMEOUT = 30  # seconds; a hook is one Python start and one short transaction (busy_timeout 5 s)
# What marks an allow rule as the ledger's, whatever home it names and whatever spelling an older sync wrote (the #! rule
# `Bash(<home>/bin/spud *)` until SPD-038, the `:*` form): settings sync drops every such rule and writes cli_allow_rules.
ALLOW_RULE_MARK = re.compile(r"^Bash\(.*bin/spud(?: \*|:\*)\)$")


def hook_command(ctx, event, project_key=None):
    """`SPUD_HOME=<home> <interpreter> -I -S <tool>/bin/spud hook <event>`, absolute, resolved at sync time (SPD-097: the
    launcher is the tool repository's and the home is a plain directory SPUD_HOME alone names); a project's line (SPD-014)
    ends in `--project <key>`, which sets the failure policy of the design's section 6.4."""
    home = str(ctx.home)
    line = "SPUD_HOME=%s %s -I -S %s hook %s" % (shlex.quote(home), shlex.quote(sys.executable), shlex.quote(str(ctx.launcher)), event)
    return line + (" --project %s" % shlex.quote(project_key) if project_key else "")


def cli_allow_rules(ctx):
    """The permission rules for the CLI in the prescribed form, `python3.14 -I -S <tool>/bin/spud ...`: by the documented
    interpreter name and by the absolute interpreter.  None for the script alone: its #! line runs the interpreter with
    neither -I nor -S, so PYTHONPATH and user-site .pth files inherited from the shell load code before the program, and
    that spelling gets the harness's prompt (SPD-038).  merge_allow_rules drops an older sync's rule for it."""
    script = str(ctx.launcher)
    rules = ["Bash(python3.14 -I -S %s *)" % script, "Bash(%s -I -S %s *)" % (sys.executable, script)]
    out = []
    for r in rules:
        if r not in out:
            out.append(r)
    return out


def tool_warning(ctx):
    """The stderr line of a command that writes the tool's path somewhere durable (settings sync, project install, schedule
    install) when that path is a linked worktree (SPD-097): a worktree is deleted when its ticket lands, and a hook line
    naming it would die with it.  None otherwise; a tool with no git at all is deliberate (a copied tree) and says nothing."""
    if homeconf.tool_checkout_kind(ctx.tool) != "worktree":
        return None
    return ("the running bin/spud is in a linked worktree, %s: the lines written name it and will break when the worktree is removed;"
            " rerun this from the main checkout's bin/spud before relying on them" % ctx.tool)


def is_ledger_hook(entry):
    """SPW-003: the mark itself lives in projects/sessions, which reads installed hook lines too and, unlike every
    module here, may be imported by a hook -- so there is one spelling of it."""
    return isinstance(entry, dict) and sessions.HOOK_MARK in str(entry.get("command", ""))


def merge_hooks(ctx, settings, project_key=None):
    """Keep every hook that is not the ledger's, replace the ledger's own entries, one
    group per row of the hook table, appended in table order."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
    for event in dict.fromkeys(e for e, _ in HOOK_TABLE):
        kept = []
        groups = hooks.get(event)
        for group in (groups if isinstance(groups, list) else []):
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue  # malformed in, nothing out (Rooster's LOW-6)
            entries = [h for h in group["hooks"] if isinstance(h, dict) and isinstance(h.get("command", ""), str) and not is_ledger_hook(h)]
            if entries:
                g2 = dict(group)
                g2["hooks"] = entries
                kept.append(g2)
        for ev, matcher in HOOK_TABLE:
            if ev != event:
                continue
            entry = {"type": "command", "command": hook_command(ctx, event, project_key), "timeout": HOOK_TIMEOUT}
            kept.append({"matcher": matcher, "hooks": [entry]} if matcher else {"hooks": [entry]})
        hooks[event] = kept
    return sum(len(v) for e, v in hooks.items() if e in dict(HOOK_TABLE))


def merge_allow_rules(ctx, settings):
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
        settings["permissions"] = permissions
    allow = permissions.get("allow")
    if not isinstance(allow, list):
        allow = []
    allow = [a for a in allow if isinstance(a, str) and not ALLOW_RULE_MARK.match(a)]
    for rule in cli_allow_rules(ctx):
        if rule not in allow:
            allow.append(rule)
    permissions["allow"] = allow
    return allow


# Law 3 in the permission system itself (SPD-016), so it holds when the PreToolUse(Agent) hook is removed or does not
# run.  These are the parameter rules of the Claude Code permissions docs ("Match by input parameter": Tool(param:value),
# deny and ask rules only, `*` a wildcard): any explicit isolation, and the inherit model.  A parameter rule never
# matches a parameter the call leaves out, so a spawn without `model` is still the hook's to refuse.
AGENT_DENY_RULES = ("Agent(isolation:*)", "Agent(model:inherit)")


def merge_deny_rules(settings):
    """Keep every other deny rule where it is, drop non-strings and repeats of ours, append ours when missing."""
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
        settings["permissions"] = permissions
    deny = permissions.get("deny")
    kept = []
    for rule in (deny if isinstance(deny, list) else []):
        if isinstance(rule, str) and not (rule in AGENT_DENY_RULES and rule in kept):
            kept.append(rule)
    for rule in AGENT_DENY_RULES:
        if rule not in kept:
            kept.append(rule)
    permissions["deny"] = kept
    return kept


def merge_additional_dirs(settings, dirs):
    """permissions.additionalDirectories gains each of `dirs` it lacks, every other entry kept; returns those it added."""
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
        settings["permissions"] = permissions
    current = permissions.get("additionalDirectories")
    current = [d for d in current if isinstance(d, str)] if isinstance(current, list) else []
    added = [d for d in dirs if d not in current]
    permissions["additionalDirectories"] = current + added
    return added


def merge_settings(ctx, settings, *, env, deny, additional_dirs=(), project_key=None):
    """One merge for both writers (SPD-014): `settings sync` for the home (env=True, deny=True) and `project install` for
    another repository's local settings (env=False, deny=False, the home as an additional directory, the project's key on
    every hook line).  Keeps every key and entry that is not the ledger's; returns what it set."""
    out = {}
    if env:
        limits = ctx.limits
        block = settings.get("env")
        if not isinstance(block, dict):
            block = {}
            settings["env"] = block
        block["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] = str(limits["max_depth"])
        block["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] = str(limits["max_concurrent_total"])
        out["env"] = block
    out["allow"] = merge_allow_rules(ctx, settings)
    if deny:
        out["deny"] = merge_deny_rules(settings)
    if additional_dirs:
        out["additional_dirs_added"] = merge_additional_dirs(settings, list(additional_dirs))
    out["hooks"] = merge_hooks(ctx, settings, project_key)
    return out


def cmd_settings_sync(ctx, args):
    limits = ctx.limits
    path = Path(args.path).expanduser().resolve() if args.path else (ctx.home / ".claude" / "settings.json")
    if path.is_file():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not valid JSON: %s" % (path, e))
        if not isinstance(settings, dict):
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not a JSON object" % path)
    else:
        settings = {}
    merged = merge_settings(ctx, settings, env=True, deny=True)
    env, allow, deny, hook_count = merged["env"], merged["allow"], merged["deny"], merged["hooks"]
    rendered = json.dumps(settings, indent=2) + "\n"
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    written = False
    if not args.dry_run and rendered != current:
        kernel.write_whole(path, rendered)
        written = True
    if not args.dry_run and ctx.db_path.is_file():
        con = ledgerdb.connect(ctx)
        try:
            at = kernel.now()
            with ledgerdb.write_txn(con):
                ledgerdb.write_event(con, at, "spud", "config.synced", "settings synced to %s (%s)" % (path, "written" if written else "unchanged"),
                            data={"path": str(path), "written": written, "env": dict(env), "hooks": hook_count, "allow": allow, "deny": deny})
        finally:
            con.close()
    text = "%s%s\n%s" % (path, " (dry run)" if args.dry_run else (" written" if written else " unchanged"), rendered.rstrip("\n"))
    return kernel.Result({"path": str(path), "written": written, "dry_run": bool(args.dry_run), "settings": settings, "hooks": hook_count}, text,
                         stderr=tool_warning(ctx) or "")


def settings_hold_hooks(ctx, path, key=None):
    """Whether a settings file carries every ledger hook of HOOK_TABLE for this home (and, for a project, with its key).
    SPW-003 moved the reading itself to projects/sessions.settings_hook_events, because `session show` asks the same
    question of the files its own session loads and cannot import this module (the hook path); this stays the whole-table
    answer, which is what `project install`, `home move`, `project list` and doctor's per-project check want."""
    return sessions.settings_hook_events(ctx, path, key) >= {e for e, _ in HOOK_TABLE}
