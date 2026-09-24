"""shell/runner_files: the files a script runner reads, and the rule bash_rule asks of a member's run.

SPD-168.  shell/script_runners reads a runner's words into plans -- the names a run asks for, where it starts, the
configuration it names -- and this module holds each plan against the files the runner would read there: every one the
member may write (the path rule, the scratchpad and the temp roots) or the line writes is refused, the file that defines
the names must lie in the member's ticket's project, and every name the run runs -- the ones asked for, and the `pre`/`post`
scripts, deno dependencies and lifecycle scripts the file defines for them -- must be on that project's allow-list,
`projects.runners`.  script_runners' docstring is the whole rule; this is its second half, the half bash_rule calls, with
its own users: analyse reads the words and never a file, and bash_rule reads the files and never the words.
"""

import json
import os
import re

from . import script_files
from ..hooks import pathrule, worktrees
from ..state import lookup

# The files each runner reads in every directory from where it starts up to the nearest one holding its primary file:
# the scripts, and the configuration that may set the shell they run in (npm's `script-shell` in .npmrc, pnpm's in
# pnpm-workspace.yaml, yarn 1's in .yarnrc, bun's `[run] shell` in bunfig.toml).  make reads only the directory it runs in.
FILES = {
    "npm": ("package.json", ".npmrc"),
    "pnpm": ("package.json", ".npmrc", "pnpm-workspace.yaml", ".pnpmfile.cjs"),
    "yarn": ("package.json", ".npmrc", ".yarnrc", ".yarnrc.yml"),
    "bun": ("package.json", ".npmrc", "bunfig.toml"),
    "node": ("package.json",),
    "deno": ("deno.json", "deno.jsonc", "package.json"),
    "make": ("GNUmakefile", "makefile", "Makefile"),  # GNU make's order (make(1)): the first that exists is read
}
PRIMARY = {"deno": ("deno.json", "deno.jsonc")}

INCLUDE_RE = re.compile(r"^(?:-include|sinclude|include)\s+(.*)$")
MAX_INCLUDES = 32

RUNNER_REASON = (
    "Law 7: `%s` is a script runner, which runs commands a project file holds (%s), and the hook reads no file's commands,"
    " so a git write verb (Law 7), a spud call (Law 6) or a write outside your deliverables (Law 5) there would pass every"
    " fence the line itself is held to. %s. A member runs a script, task or target only by a name its project allows"
    " (`spud project show %s` lists them), while every file that defines it or configures the runner lies outside the"
    " member's deliverables and the line writes none of them, and with no option or variable of the line's own that sets"
    " the runner's shell; Spud allows a name with `spud --as spud project edit %s --allow-runner <name>`. Spell the"
    " commands on the line instead, or ask your parent if the work needs another name")
HOLDS = {"make": "the makefile's recipes", "deno": "deno.json's tasks and package.json's scripts"}

# ----------------------------------------------------------------------------
# The rule: what bash_rule asks for a member
# ----------------------------------------------------------------------------

def runner_reason(ctx, con, caller_agent_id, caller_member, cwd, mode, detail, written, cache):
    """The refusal a "runner" finding earns a member, or None where every reading runs only names its project allows
    from files it cannot write (module docstring).  `written`: the absolute paths the line writes (bash_rule.written_targets).
    `cache`: a dict kept across a line's findings, for the member's project."""
    if "project" not in cache:
        cache["project"] = _project(ctx, con, caller_member)
    key, roots, allowed = cache["project"]
    plans, cwds = detail
    for plan in plans:
        if plan[0] == "refuse":
            _r, family, shown, why = plan
            return _reason(family, shown, why, key)
        why = _plan_why(ctx, con, caller_agent_id, caller_member, cwd, mode, plan, cwds, written, roots, allowed, key)
        if why is not None:
            return _reason(plan[1], plan[2], why, key)
    return None


def _reason(family, shown, why, key):
    holds = HOLDS.get(family, "package.json's scripts")
    return RUNNER_REASON % (shown, holds, why[0].upper() + why[1:], key or "<key>", key or "<key>")


def _project(ctx, con, caller_member):
    """(the member's ticket's project key, its checkouts -- the main one and the ticket's bound worktree -- and the names
    its allow-list holds); nothing for a caller with no member."""
    if caller_member is None:
        return None, [], frozenset()
    ticket = lookup.get_ticket_by_id(con, caller_member["ticket_id"])
    project = con.execute("SELECT * FROM projects WHERE id = ?", (ticket["project_id"],)).fetchone()
    roots = [worktrees.project_root(ctx, project)] + ([ticket["worktree"]] if ticket["worktree"] else [])
    return project["key"], roots, frozenset(json.loads(project["runners"] or "[]"))


def _plan_why(ctx, con, caller_agent_id, caller_member, cwd, mode, plan, cwds, written, roots, allowed, key):
    """Why one reading is refused, or None."""
    _c, family, shown, requests, dirs, configs = plan
    if caller_member is None:
        return "a caller with no ticket has no project whose allow-list it may run from"
    starts = []
    for base in (sorted(cwds) if cwds is not None else [None]):
        d = base
        for text in dirs:
            text = os.path.expanduser(text) if text.startswith("~/") or text == "~" else text
            d = text if os.path.isabs(text) else (None if d is None or text.startswith("~") else os.path.join(d, text))
        if d is None:
            return "it runs in a directory the hook cannot follow"
        starts.append(os.path.normpath(d))
    out_roots = pathrule.outside_roots()

    def reach(path):
        """Why the member could decide what this file holds, or None."""
        readings = worktrees.path_readings(path, cwd)
        if any(script_files.written_over(r, written) for r in readings):
            return "the line writes %s, which it reads" % path
        _inside, outside = worktrees.path_placements(ctx, con, path, cwd)
        if (any(pathrule.under_outside_root(c, out_roots) for c in outside)
                or pathrule.edit_reason(ctx, con, caller_agent_id, caller_member, path, cwd, mode)[0] is None):
            return "it reads %s, a file you may write" % path
        return None

    def ours(path):
        inside, outside = worktrees.path_placements(ctx, con, path, cwd)
        if outside or not inside or not all(any(worktrees.same_directory(root, r) for r in roots) for _p, root, _rel in inside):
            return "the file it runs from, %s, is not in project %s's checkout or your ticket's worktree" % (path, key)
        return None

    for start in starts:
        if family == "make":
            files = [os.path.join(start, f) for f in configs] if configs else [os.path.join(start, f) for f in FILES["make"]]
            read = files if configs else [f for f in files if os.path.isfile(f)][:1]
            checked = list(files)
            why = _make_includes(read, start, checked)
            if why is not None:
                return why
            defining = [f for f in checked if os.path.isfile(f)]
        else:
            checked, found = _walk(family, start, configs)
            defining = [found[n] for n in ("package.json", "deno.json", "deno.jsonc") if n in found]
        for path in checked:
            why = reach(path)
            if why is not None:
                return why
        for path in defining:
            why = ours(path)
            if why is not None:
                return why
        if family == "make":
            defined, deps = None, {}
        else:
            defined, deps = _defined(family, found)
            if defined is None:
                return deps  # the file the hook could not read
        missing = [n for n in _required(requests, defined, deps, bool(defining)) if n not in allowed]
        if missing:
            return "%s %s not on project %s's runner allow-list" % (
                ", ".join("`%s`" % n for n in missing), "is" if len(missing) == 1 else "are", key)
    return None


def _walk(family, start, configs):
    """(every file the runner may read, from `start` up to the nearest directory holding its primary file, whether it
    exists or not; {name: the nearest existing file of that name}).  A configuration file the line names is read too."""
    primary = PRIMARY.get(family, ("package.json",))
    checked, found = [], {}
    for text in configs:
        path = os.path.normpath(os.path.join(start, os.path.expanduser(text)))
        checked.append(path)
        found.setdefault("deno.jsonc" if path.endswith(".jsonc") else "deno.json", path)
    d = start
    while True:
        for name in FILES[family]:
            path = os.path.join(d, name)
            checked.append(path)
            if name not in found and os.path.isfile(path):
                found[name] = path
        if any(n in found for n in primary):
            return checked, found
        parent = os.path.dirname(d)
        if parent == d:
            return checked, found
        d = parent


def _make_includes(makefiles, start, checked):
    """Add to `checked` every file an `include` line names, through the makefiles it reads, up to MAX_INCLUDES; why a
    member is refused where one is a name the hook cannot settle, or None."""
    pending = list(makefiles)
    while pending:
        path = pending.pop()
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().split("\n")
        except OSError:
            continue
        for line in lines:
            if line.startswith("\t"):
                continue  # a recipe line
            m = INCLUDE_RE.match(line.split("#", 1)[0].strip())
            if m is None:
                continue
            for name in m.group(1).split():
                if any(c in name for c in "$*?[(`"):
                    return "%s includes %s, a name the hook does not expand" % (path, name)
                included = os.path.normpath(os.path.join(start, os.path.expanduser(name)))
                if included not in checked:
                    if len(checked) >= MAX_INCLUDES:
                        return "%s includes more files than the hook reads" % path
                    checked.append(included)
                    pending.append(included)
    return None


def _defined(family, found):
    """(the names the files define, {a deno task: the names it depends on}), or (None, why) for a file the hook cannot
    read as its runner would."""
    defined, deps = set(), {}
    if "package.json" in found:
        data = _load(found["package.json"], False)
        scripts = data.get("scripts", {}) if isinstance(data, dict) else None
        if not isinstance(scripts, dict):
            return None, "the hook cannot read the scripts of %s" % found["package.json"]
        defined.update(scripts)
    if family == "deno":
        config = found.get("deno.json") or found.get("deno.jsonc")
        if config is not None:
            data = _load(config, True)
            tasks = data.get("tasks", {}) if isinstance(data, dict) else None
            if not isinstance(tasks, dict):
                return None, "the hook cannot read the tasks of %s" % config
            for name, task in tasks.items():
                defined.add(name)
                if isinstance(task, dict):
                    deps[name] = [d for d in task.get("dependencies") or () if isinstance(d, str)]
    return defined, deps


def _required(requests, defined, deps, readable):
    """The names a run runs: each it asks for (a make target, `npm run <name>`), and each one the file defines among a
    name's pre and post scripts, its dependencies, and a verb's lifecycle scripts.  `defined` None: make's, whose
    makefile the hook does not parse, so every target asked for is one it runs."""
    out = []

    def visit(n, hooks):
        if n in out:
            return
        if hooks and "pre" + n in defined:
            out.append("pre" + n)
        out.append(n)
        for d in deps.get(n, ()):
            visit(d, True)  # a dependency runs as a task of its own, its hooks with it
        if hooks and "post" + n in defined:
            out.append("post" + n)

    for name, prepost, always in requests:
        if defined is None:
            if readable:  # with no makefile at all, make runs no recipe of the project's
                out.append(name)
        elif always or name in defined:
            visit(name, prepost)
    return list(dict.fromkeys(out))


def _load(path, jsonc):
    """A JSON file's value, or None; deno.jsonc (and deno.json, which deno reads the same way) with its comments and
    trailing commas taken out first."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        return json.loads(_strip_jsonc(text) if jsonc else text)
    except (OSError, ValueError):
        return None


def _strip_jsonc(text):
    """JSON with comments (`//` and `/* */`) and trailing commas, as deno reads a configuration file, made plain JSON:
    the comments out first, then each comma that only blanks separate from a closing bracket."""
    return "".join(_jsonc_pieces("".join(_jsonc_pieces(text, True)), False))


def _jsonc_pieces(text, comments):
    """The text in pieces, strings whole, without its comments (`comments`) or its trailing commas (not `comments`)."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            yield text[i : j + 1]
            i = j + 1
        elif comments and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif comments and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif not comments and c == "," and text[i + 1 :].lstrip(" \t\r\n")[:1] in ("}", "]"):
            i += 1  # a trailing comma
        else:
            yield c
            i += 1
