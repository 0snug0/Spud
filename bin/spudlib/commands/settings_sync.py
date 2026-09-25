"""commands/settings_sync: settings sync, and the settings merge project install shares."""

import json
import re
import shlex
import sys
from pathlib import Path

from ..core import homeconf, kernel
from ..projects import sessions
from ..state import ledgerdb


# The hook table of the spike's Enforcement plan, installed by `spud settings sync`: one row per event.  PreToolUse's
# matcher names every tool the hook enforces, hooks/hookio.ENFORCED_TOOLS, and the hook reads the payload's tool_name and
# dispatches on it, so one row does what the three rows every sync wrote before SPD-223 did (Agent; Bash;
# Write|Edit|MultiEdit|NotebookEdit, each carrying the same command): a call to any of those tools matches one row and runs
# one process.  A matcher of letters and `|` alone is a list of exact tool names to the harness, not a regular expression
# (Claude Code's hooks reference, "Matcher patterns"), so it matches those seven tools and no other.  SendMessage joined
# them on SPD-318, so a message that would resume a spudagent outside PreToolUse(Agent)'s member check is refused.
HOOK_TABLE = (
    ("PreToolUse", "Agent|Bash|Write|Edit|MultiEdit|NotebookEdit|SendMessage"),
    ("PostToolUse", "Agent"),
    ("SubagentStart", None),
    ("SubagentStop", None),
    ("SessionStart", "startup|resume|clear|compact"),  # clear too: a /clear gets the board as well
    ("Stop", None),
    ("UserPromptSubmit", None),  # a prompt naming a claim project's ticket claims the session; the event takes no matcher
)
# The events those rows install, in the table's order.  One hook line is one event however many matchers it has -- which
# is what merge_hooks writes, what settings_hook_events reads back, and what settings_missing_hooks and doctor's `settings`
# line count -- so a file an older sync wrote, PreToolUse in three rows, reads as carrying every event, and the next sync
# leaves the one row in their place.
TABLE_EVENTS = tuple(dict.fromkeys(e for e, _ in HOOK_TABLE))
HOOK_TIMEOUT = 30  # seconds; a hook is one Python start and one short transaction (busy_timeout 5 s)
# What marks an allow rule as the ledger's, whatever home it names and whatever spelling an older sync wrote (the #! rule
# `Bash(<home>/bin/spud *)` from before the `-I -S` spelling, the `:*` form): settings sync drops every such rule and
# writes cli_allow_rules.
ALLOW_RULE_MARK = re.compile(r"^Bash\(.*bin/spud(?: \*|:\*)\)$")


def hook_command(ctx, event, project_key=None):
    """`SPUD_HOME=<home> <interpreter> -I -S <tool>/bin/spud hook <event>`, absolute, resolved at sync time (the launcher is
    the tool repository's and the home is a plain directory SPUD_HOME alone names); a project's line ends in
    `--project <key>`, which sets a project hook's failure policy (fail open for a caller with no agent_id)."""
    home = str(ctx.home)
    line = "SPUD_HOME=%s %s -I -S %s hook %s" % (shlex.quote(home), shlex.quote(sys.executable), shlex.quote(str(ctx.launcher)), event)
    return line + (" --project %s" % shlex.quote(project_key) if project_key else "")


def cli_allow_rules(ctx):
    """The permission rules for the CLI in the prescribed form, `python3.14 -I -S <tool>/bin/spud ...`: by the documented
    interpreter name and by the absolute interpreter.  None for the script alone: its #! line runs the interpreter with
    neither -I nor -S, so PYTHONPATH and user-site .pth files inherited from the shell load code before the program, and
    that spelling gets the harness's prompt.  merge_allow_rules drops an older sync's rule for it."""
    script = str(ctx.launcher)
    rules = ["Bash(python3.14 -I -S %s *)" % script, "Bash(%s -I -S %s *)" % (sys.executable, script)]
    out = []
    for r in rules:
        if r not in out:
            out.append(r)
    return out


def tool_warning(ctx):
    """The stderr line of a command that writes the tool's path somewhere durable (settings sync, project install, schedule
    install) when that path is a linked worktree: a worktree is deleted when its ticket lands, and a hook line
    naming it would die with it.  None otherwise; a tool with no git at all is deliberate (a copied tree) and says nothing."""
    if homeconf.tool_checkout_kind(ctx.tool) != "worktree":
        return None
    return ("the running bin/spud is in a linked worktree, %s: the lines written name it and will break when the worktree is removed;"
            " rerun this from the main checkout's bin/spud before relying on them" % ctx.tool)


def is_ledger_hook(entry):
    """Whether a hook entry is the ledger's, by projects/sessions.is_ledger_command: the rule lives there, beside the
    mark, because that module reads installed hook lines too and, unlike every module here, may be imported by a hook --
    so there is one spelling of it, and a line whose launcher shlex.quote quoted is the ledger's here as well (SPD-226)."""
    return isinstance(entry, dict) and sessions.is_ledger_command(str(entry.get("command", "")))


def merge_hooks(ctx, settings, project_key=None):
    """Keep every hook that is not the ledger's, replace the ledger's own entries, one
    group per row of the hook table, appended in table order.  An entry is the ledger's by its command, whatever group or
    matcher it sits under, so the three PreToolUse rows an older sync wrote go with the rest and one row takes their place."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
    for event in TABLE_EVENTS:
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


# Law 3 in the permission system itself, so it holds when the PreToolUse(Agent) hook is removed or does not
# run.  These are the parameter rules of the Claude Code permissions docs ("Match by input parameter": Tool(param:value),
# deny and ask rules only, `*` a wildcard): any explicit isolation, and the inherit model.  A parameter rule never
# matches a parameter the call leaves out, so a spawn without `model` is still the hook's to refuse.
AGENT_DENY_RULES = ("Agent(isolation:*)", "Agent(model:inherit)")

# SPD-033: SPD-031's edit-hook rule for the ledger state directory (hooks/pathrule.in_state_dir: the database, its WAL and
# shm, the caches, the backups, the launcher's bytecode; only the CLI writes there) held by the permission system too, so
# it stands where the hook does not run.  The spelling follows Claude Code's permissions docs, "Read and Edit"
# (code.claude.com/docs/en/permissions):
#   - "`Edit` rules apply to all built-in tools that edit files", Write and NotebookEdit included, while a path rule named
#     for Write, NotebookEdit or the legacy MultiEdit is "accepted but never consulted" -- so Edit is the one tool named;
#   - the path is gitignore syntax, and `//path` is the "Absolute path from filesystem root" (a single `/path` anchors at
#     the settings source instead: the home's own directory for its .claude/settings.json, the session's working
#     directory for a project's local settings, which is why neither is used);
#   - `*` stays within one segment and `**` crosses directories, so `<dir>/**` is everything under the directory at any
#     depth, and the bare `<dir>` is the directory's own path;
#   - rules "you write yourself aren't escaped", so each gitignore character of the home's path (\ * ? [ ]) gets a
#     backslash, and the rule matches that path alone; parentheses need none, and a rule is not a shell word, so a path
#     with a space or a non-ASCII letter goes in as it is spelled and JSON carries it;
#   - a deny rule applies when either a symlink or its target matches, and the home is resolved already
#     (core/homeconf.resolve_home), so the one spelling covers both.
# The docs' own caveat stands: these rules reach the file tools and the file commands Claude Code recognizes in Bash, not
# a program that opens files itself -- the Bash hook's DB_PATH_RE stays the rule there.
GITIGNORE_SPECIAL = "\\*?[]"
# A state-directory rule of ours, whatever home it names: Edit(//<a literal path>/.spud) or its `/**`, every gitignore
# character in the path escaped, as state_dir_deny_rules writes it.  A moved home's rules are stale spellings of ours and
# go; a rule with a live wildcard, a relative anchor, another tool, or a file inside the directory is the user's.
STATE_DENY_MARK = re.compile(r"Edit\(//(?:[^*?\[\]\\]|\\.)+/\.spud(?:/\*\*)?\)")


def gitignore_literal(text):
    """`text` as a gitignore pattern that matches exactly itself: a backslash before each of \\ * ? [ ]."""
    return "".join("\\" + c if c in GITIGNORE_SPECIAL else c for c in text)


def state_dir_deny_rules(ctx):
    """The two Edit deny rules for the home's state directory: the directory's own path, and everything under it."""
    state = gitignore_literal(str(ctx.db_path.parent).lstrip("/"))
    return ["Edit(//%s)" % state, "Edit(//%s/**)" % state]


def merge_deny_rules(ctx, settings, agent=True, keep=()):
    """Keep every other deny rule where it is, drop non-strings, repeats of ours and a state-directory rule of ours for
    another home (unless `keep` names it: project install's file had it before install), append ours when missing.  Ours
    are the state directory's rules, and with `agent` Law 3's Agent rules before them."""
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
        settings["permissions"] = permissions
    ours = (list(AGENT_DENY_RULES) if agent else []) + state_dir_deny_rules(ctx)
    deny = permissions.get("deny")
    kept = []
    for rule in (deny if isinstance(deny, list) else []):
        if not isinstance(rule, str) or (rule in ours and rule in kept):
            continue
        if rule not in ours and rule not in keep and STATE_DENY_MARK.fullmatch(rule):
            continue
        kept.append(rule)
    for rule in ours:
        if rule not in kept:
            kept.append(rule)
    permissions["deny"] = kept
    return kept


def merge_additional_dirs(settings, dirs, drop=()):
    """permissions.additionalDirectories gains each of `dirs` it lacks, every other entry kept, except an entry `drop`
    names: a directory an earlier install wrote for another home (SPD-245), whose place the first of `dirs` missing takes,
    so a reinstall for a moved home replaces its entry where it stands rather than adding one beside it.  Returns the
    directories of `dirs` it added."""
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
        settings["permissions"] = permissions
    current = permissions.get("additionalDirectories")
    current = [d for d in current if isinstance(d, str)] if isinstance(current, list) else []
    added = [d for d in dirs if d not in current]
    missing = list(added)
    out = []
    for d in current:
        if d in drop and d not in dirs:
            if missing:
                out.append(missing.pop(0))
            continue
        out.append(d)
    permissions["additionalDirectories"] = out + missing
    return added


def merge_settings(ctx, settings, *, env, agent_deny, keep_deny=(), additional_dirs=(), drop_dirs=(), project_key=None):
    """One merge for both writers:`settings sync` for the home (env=True, agent_deny=True) and `project install` for
    another repository's local settings (env=False, agent_deny=False, the home as an additional directory in place of
    the one `drop_dirs` names when an earlier install wrote that for another home, the project's key on every hook line).
    Both write the state directory's deny rules (SPD-033).  Keeps every key and entry that is not the ledger's; returns
    what it set."""
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
    out["deny"] = merge_deny_rules(ctx, settings, agent=agent_deny, keep=keep_deny)
    if additional_dirs:
        out["additional_dirs_added"] = merge_additional_dirs(settings, list(additional_dirs), drop=tuple(drop_dirs))
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
    merged = merge_settings(ctx, settings, env=True, agent_deny=True)
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


def settings_exact_hooks(ctx, path, key=None):
    """The events `path` carries the hook line this home writes for them, byte for byte as hook_command writes it now.

    The strongest evidence a file can carry, first read because projects/sessions.HOOK_MARK cannot see a line whose
    launcher word got quoted.  shlex.quote quotes any path outside ASCII `[\\w@%+=:,./-]`, so in a home whose path holds
    a space or a non-ASCII character (a home named `Spüd`) every installed line reads `... '<home>/bin/spud' hook Stop`,
    in which `bin/spud hook` is not a substring: the mark then found nothing, and a home `settings sync` had just written
    read as a home with no ledger hook at all.  sessions.is_ledger_command reads such a line by its shape since SPD-226;
    answering with the generated string instead of parsing one stays, since it cannot have that class of bug at all.  The
    marked reading answers beside this, because a line an older sync wrote -- another interpreter path, an older
    spelling -- is this home's line and does run."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return set()
    found = set()
    for event in TABLE_EVENTS:
        groups = hooks.get(event)
        want = hook_command(ctx, event, key)
        for group in (groups if isinstance(groups, list) else []):
            for h in (group.get("hooks") if isinstance(group, dict) and isinstance(group.get("hooks"), list) else []):
                if isinstance(h, dict) and h.get("command") == want:
                    found.add(event)
    return found


def settings_missing_hooks(ctx, path, key=None):
    """The events of HOOK_TABLE a settings file carries no ledger hook line of this home for (and, for a project, none
    with its key), in the table's own order; the empty list for a file that carries them all.

    Doctor's `settings` line names these rather than saying only that something is wrong, because a file one
    event short and a file with nothing in it are fixed by the same `settings sync` and read completely differently --
    the first is a hand edit or an older table, the second an installation that never happened -- and a report that
    says neither leaves Eric to diff the file himself.  One line is one event however many matchers it has, as
    merge_hooks writes it and both readings below take it: the table has one row per event, and the three PreToolUse
    rows a sync before SPD-223 wrote answer for their one event, so such a file is missing nothing."""
    found = settings_exact_hooks(ctx, path, key) | sessions.settings_hook_events(ctx, path, key)
    return [e for e in TABLE_EVENTS if e not in found]


def settings_hold_hooks(ctx, path, key=None):
    """Whether a settings file carries every ledger hook of HOOK_TABLE for this home (and, for a project, with its key).
    The marked reading lives in projects/sessions.settings_hook_events, because `session show` asks the same
    question of the files its own session loads and cannot import this module (the hook path); this stays the whole-table
    answer, which is what `project install`, `home move`, `project list` and doctor's two checks want."""
    return not settings_missing_hooks(ctx, path, key)
