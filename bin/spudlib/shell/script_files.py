"""shell/script_files: a shell whose commands come from a file, and the project allow-list of repository scripts.

SPD-145.  The Bash rule reads the commands a line spells -- a `-c` string, a here-document or here-string fed to a shell,
the text a printer pipes into one (shell/stdin_text) -- and read none of a shell's commands that come from a file:
`sh x.sh`, `bash ./x.sh`, `source x.sh`, `. x.sh`, `sh < x.sh`, `cat x.sh | sh`, `curl ... | sh`, and `./x.sh` run by its
path.  So a member that wrote `git push` into a script in its scratchpad (the Write tool allows it there) and ran
`bash x.sh` pushed past Law 7, and the same for a spud call (Law 6) or a write outside its deliverables (Law 5).

Eric's call (2026-09-22, option 3 of the ticket): fail closed.  For a caller the Bash rule holds (a member), each of these
is recorded here and refused in bash_rule, last of all, with the readable forms named: `sh -c '...'` and a here-document.
Spud keeps every one of them, Law 1 binding him where the hook cannot see.  The shapes, each a "script" finding
(form, the command word as spelled, the file as spelled or None, the file as the line settles it or None, the directories
the shell may be in):

- "operand": a shell given a script file of its own (stdin_text.script_operand), through every wrapper the dispatch
  already unwraps (env, nice, xargs, ...), and bash's `--rcfile`/`--init-file`, a file an interactive bash runs first.
- "source": the `source` and `.` builtins, whatever runs them.
- "exec": a command word that is a path rather than a name the shell looks up on PATH -- `./x.sh`, `scripts/foo.sh`,
  `/tmp/x` -- and a wrapper's own word spelled that way, whatever the dispatch reads it as (`./git status` runs
  whatever `./git` holds).  The spud launcher is read as the spud call it is, as before.
- "stdin": a shell that runs what it reads on standard input where the line feeds it text it does not spell (a `<` file,
  another program's output, and since SPD-207 an unquoted here-document whose body holds a command substitution's
  output, fed to the shell or printed into it: heredocs.OutputBody, and since SPD-208 one holding a variable's value
  the line does not settle); spelled text is read as SPD-143 and SPD-148 read it.
- "xargs": a shell's `-c` string an xargs reads from input the line does not spell (`cat f | xargs -0 sh -c`).
- "startup": a variable that names a file of commands a shell runs when it starts -- BASH_ENV (any non-interactive
  bash, a script's `#!/bin/bash` included), ENV and ZDOTDIR -- assigned anywhere on the line, and HOME assigned on a line
  that starts a shell, whose ~/.zshenv (and an interactive shell's rc files) then come from the line's own directory.

Whether a shebang must be read to tell a script from a program run by its path: no.  What matters is whether the file is
one a member could have put there, not what is in it -- a binary a member compiled (`cc -o x x.c; ./x`) is exactly as
unreadable as a script.  So a path is let through on where it lies: outside every checkout the ledger knows and outside
the scratchpad and temp roots a member may write (hooks/pathrule.outside_roots), which is the machine's installed software
the hook already treats as a program on PATH (`/usr/bin/env`, `/bin/sh`, Claude Code's own `$_cc_bin`, a virtualenv's
python resolved to its interpreter), or an allow-listed repository script.

The allow-list: `projects.scripts`, repository paths Spud names with `spud --as spud project edit --allow-script`
(migration 0007_project_scripts).  A file a "script" finding names is let through only when every reading of it (the
lexical path and the real one, from every directory the shell may be in) is either such an installed program or lies in
the calling member's ticket's project -- its main checkout or the ticket's bound worktree -- at a path the list names, and
then only when the member cannot write it (the path rule, hooks/pathrule.edit_reason, refuses it the write: it is outside
the member's deliverable globs, and not in a checkout the ticket's binding closes) and the line writes none of it first
(a redirection, a write by argument or a git call's own write naming the file or a directory above it).  `source` needs a
slash in its operand for that, since bash's `source name` looks on PATH first; a shell's operand must exist where the line
names it, since `sh name` looks on PATH when it does not.  Standard input, an xargs string and a startup variable are never
let through: the readable forms are the way.

A script runner -- `npm run`, `npm test`, `deno task <name>`, `bun run`, `pnpm run`, `yarn <script>`, `make` -- runs a
command a file of the project's holds: SPD-168's, the same shape with an allow-list of names (shell/script_runners).
What stays open: an interpreter's program from a file (`python3 x.py`, `node x.js`) is SPD-150's rule and unchanged.
"""

import json
import os

from . import heredocs, prepare, stdin_text, syntax
from ..hooks import pathrule, worktrees
from ..state import lookup

# The variables that name a file of commands a shell runs as it starts (bash(1) INVOCATION, zsh(1) STARTUP/SHUTDOWN
# FILES): refused a member wherever the line assigns one.  HOME is read only where the line starts a shell (read_shell).
STARTUP_VARIABLES = frozenset({"BASH_ENV", "ENV", "ZDOTDIR"})

SCRIPT_REASON = (
    "Law 7: `%s` runs commands %s, and the hook reads no file's commands, so a git write verb (Law 7), a spud call (Law 6)"
    " or a write outside your deliverables (Law 5) there would pass every fence the line itself is held to. Spell the"
    " commands on the line, where the hook reads them: `sh -c '...'`, or a here-document fed to the shell"
    " (`sh <<'EOF'` ... `EOF`). A repository script your project allows (`spud project show <key>` lists them) runs from its"
    " checkout or your ticket's bound worktree, spelled as a path, while it is outside your deliverables and the line does"
    " not write it; ask your parent if the work needs another")
HOW = {
    "operand": "from the script file %s",
    "source": "from the file %s, which it reads into the shell",
    "exec": "from %s, a file run by its path rather than a program the shell finds on PATH",
    "stdin": "it reads on standard input that the line does not spell (a `<` file, another program's output through a pipe,"
             " a command substitution's output in an unquoted here-document, or a variable's value there that the line"
             " does not settle)",
    "xargs": "from a `-c` string xargs reads from input the line does not spell",
    "startup": "from a file of commands a shell runs as it starts (BASH_ENV, ENV and ZDOTDIR name one, and HOME holds"
               " a shell's own startup files)",
}


# ----------------------------------------------------------------------------
# The analysis: what analyse's dispatch records
# ----------------------------------------------------------------------------

def record_script(a, form, cmd, word=None):
    """Record one "script" finding: `cmd` the command word as the line spells it, `word` the file (masked), read through
    the values the line settled (stdin_text.word_text) and None where the hook cannot say which file it is."""
    shown = None if word is None else syntax.shown_operands(prepare.deglob(word))
    target = None if word is None else stdin_text.word_text(word, a)
    a.findings.append(("script", (form, syntax.shown_operands(prepare.deglob(cmd)), shown, target, a.cwds)))


def read_shell(words, a, dash_c, string, xargs_input, stdin, fed, bodies):
    """A shell command's words as analyse's dispatch read them: the script file it runs, the files bash runs first, the
    standard input the line feeds it without spelling it, the `-c` string an xargs reads from such input, and a HOME the
    line set for it.  `string` is the -c string as the dispatch settled it (None where there is none to read)."""
    cmd = words[0]
    operand = stdin_text.script_operand(words)
    if operand is not None:
        record_script(a, "operand", cmd, operand)
    for f in stdin_text.startup_files(words):
        record_script(a, "operand", cmd, f)
    if dash_c and string is None and xargs_input is not None:
        record_script(a, "xargs", cmd)
    elif not dash_c and operand is None and xargs_input is not None and xargs_input[1]:
        # the words xargs appends are the shell's operands, the first of them its script (`echo x.sh | xargs sh`)
        record_script(a, "operand", cmd, syntax.INPUT_OPERAND)
    if not dash_c and operand is None and stdin_text.reads_commands(words) \
            and (not bodies and stdin is None and fed or heredocs.holds_output(bodies)):
        # standard input the line does not spell: a file, another program's output, or a here-document body holding a
        # command substitution's output, which the shell runs as its commands (SPD-207)
        record_script(a, "stdin", cmd)
    if "HOME" in a.vars:
        record_script(a, "startup", "HOME=...")


def read_path_word(a, word):
    """A command word, or a wrapper's, spelled as a path: the shell runs that file, never a program it looks up."""
    if "/" in prepare.deglob(word):
        record_script(a, "exec", word, word)


def read_assignment(a, name):
    """An assignment the line makes, in the shell or a command's environment: a startup variable is recorded."""
    if name in STARTUP_VARIABLES:
        record_script(a, "startup", name + "=...")


# ----------------------------------------------------------------------------
# The rule: what bash_rule asks for a member
# ----------------------------------------------------------------------------

def script_reason(ctx, con, caller_agent_id, caller_member, cwd, mode, detail, written, allow=None):
    """The refusal a "script" finding earns a member, or None where the file is one it may run (module docstring).
    `written`: the absolute paths the line writes (bash_rule.written_targets), each a file or a directory above one.
    `allow`: a one-element list caching (the roots, the allow-listed paths) across a line's findings."""
    form, cmd, shown, target, cwds = detail
    if form in ("operand", "source", "exec") and target is not None:
        if allow is None:
            allow = []
        if not allow:
            allow.append(allow_list(ctx, con, caller_member))
        roots, scripts = allow[0]
        if runnable(ctx, con, caller_agent_id, caller_member, cwd, mode, form, target, cwds, written, roots, scripts):
            return None
    how = HOW[form]
    if "%s" in how:
        how = how % ("`%s`" % (shown if shown is not None else cmd))
    return SCRIPT_REASON % (cmd, how)


def allow_list(ctx, con, caller_member):
    """(the checkouts an allow-listed script may run from, the paths the list names) for the member's ticket's project:
    its main checkout and the ticket's bound worktree.  Nothing for a caller with no member."""
    if caller_member is None:
        return [], frozenset()
    ticket = lookup.get_ticket_by_id(con, caller_member["ticket_id"])
    project = con.execute("SELECT * FROM projects WHERE id = ?", (ticket["project_id"],)).fetchone()
    roots = [worktrees.project_root(ctx, project)] + ([ticket["worktree"]] if ticket["worktree"] else [])
    return roots, frozenset(json.loads(project["scripts"] or "[]"))


def runnable(ctx, con, caller_agent_id, caller_member, cwd, mode, form, target, cwds, written, roots, scripts):
    """True when every file `target` may name, from every directory the shell may be in, is one a member may run."""
    if form == "source" and "/" not in target:
        return False  # bash's `source name` searches PATH before the directory it is in
    if any(c in target for c in "*?["):
        return False  # a glob the dispatch left as spelled: `/*/x.sh` may match a file anywhere, the scratchpad's included
    if target.startswith("~"):
        if not target.startswith("~/"):
            return False  # ~user or a zsh named directory
        target = os.path.expanduser(target)
    if os.path.isabs(target):
        paths = [target]
    elif cwds is None:
        return False  # relative to a directory the hook cannot follow
    else:
        paths = [os.path.join(c, target) for c in sorted(cwds)]
    out_roots = pathrule.outside_roots()
    for path in paths:
        if form != "exec" and not os.path.isfile(path):
            return False  # a shell's operand it does not find there is looked for on PATH, and so is a sourced one
        # (a path run as a command that is not there runs nothing; one the line could create is refused below, where it lies)
        readings = worktrees.path_readings(path, cwd)
        if any(written_over(r, written) for r in readings):
            return False
        inside, outside = worktrees.path_placements(ctx, con, path, cwd)
        if any(pathrule.under_outside_root(c, out_roots) for c in outside):
            return False  # the scratchpad and the temp roots, a member's to write
        for _project, root, rel in inside:
            if rel not in scripts or not any(worktrees.same_directory(root, r) for r in roots):
                return False
        if inside and pathrule.edit_reason(ctx, con, caller_agent_id, caller_member, path, cwd, mode)[0] is None:
            return False  # under the member's own deliverables: an allowed name, but its text is the member's
    return True


def written_over(path, written):
    """True when the line writes `path` itself or a directory above it."""
    return any(path == w or path.startswith(w.rstrip(os.sep) + os.sep) for w in written)
