"""shell/spud_calls: Recognizing and vouching for a spud call.  Moved from bin/spud_ledger.py (SPD-065)."""

import os
import re
import sys

from ..hooks import hookio, worktrees
from ..state import lookup


def parse_spud_call(words):
    """The global options and the command words of a `spud` invocation."""
    call = {"actor": None, "command": None, "subcommand": None, "rest": [], "help": False}
    i = 0
    while i < len(words):
        w = words[i]
        if w == "--as":
            call["actor"] = words[i + 1] if i + 1 < len(words) else ""
            i += 2
            continue
        if w.startswith("--as="):
            call["actor"] = w[5:]
            i += 1
            continue
        if w in ("--version", "-h", "--help"):
            call["help"] = True
            i += 1
            continue
        if w == "--json":
            i += 1
            continue
        if call["command"] is None:
            call["command"] = w
        elif call["subcommand"] is None and call["command"] in ("config", "settings", "ticket", "member", "proposal", "handoff", "report",
                                                                  "project", "session", "ledger", "schedule"):
            call["subcommand"] = w
        else:
            call["rest"].append(w)
        i += 1
    return call


def python_interpreter_args(args):
    """(code, module, stdin_script, script, script_args) for a python command line."""
    i = 0
    while i < len(args):
        w = args[i]
        if w == "-c":
            return args[i + 1] if i + 1 < len(args) else "", None, False, None, []
        if w == "-m":
            return None, args[i + 1] if i + 1 < len(args) else "", False, None, []
        if w == "-":
            return None, None, True, None, []
        if w.startswith("-"):
            i += 2 if w in ("-X", "-W", "-Q") else 1
            continue
        return None, None, False, w, args[i + 1 :]
    return None, None, False, None, []


def spud_launcher(script, cwd):
    """True when running `script` runs a spud launcher however its path is spelled (SPD-029): Law 6's refusals and
    Law 5's `--as` check depend on seeing the call.  Its name case-folded is spud (bin/SPUD and bin/ſpud are bin/spud
    on macOS), or the name its symlinks resolve to is (the launcher finds its program from its own real path, so a
    link by any name runs it).  A relative path resolves against the directory the shell would be in."""
    if os.path.basename(script).casefold() == "spud":
        return True
    if "$" in script or "`" in script or hookio.SUBST in script:
        return False
    p = os.path.expanduser(script) if script.startswith("~") else script
    if not os.path.isabs(p):
        if not cwd:
            return False
        p = os.path.join(cwd, p)
    return os.path.basename(os.path.realpath(p)).casefold() == "spud"


def any_spud_launcher(script, cwds):
    """spud_launcher against each directory the shell may be in (a relative script is a launcher from any of them)."""
    return any(spud_launcher(script, c) for c in (sorted(cwds) if cwds else [None]))


# What the Bash hook's allow needs beyond recognizing a spud call (SPD-032).  Recognition by name stays wide, since Laws 5 and 6
# refuse every spelling; the allow skips the harness's prompt, so it is given only to a call whose every moving part the hook
# can vouch for, each pinned by a probe in the scratchpad (python3.14 3.14.7, framework build):
#   the launcher: the running tool's bin/spud by file identity (SPD-097), run from its own directory, since it loads spud_ledger.py beside
#     its real path (a hard link elsewhere is the same file running another program; __file__ keeps the path as given, and its
#     realpath is the kernel's reading, symlinks before `..` included).  Not a worktree's: bin/** there is a member's deliverable.
#   the interpreter: the file the hook itself runs on, in the same directory (a symlink elsewhere is a file anyone who can write
#     that directory swaps, and a pyvenv.cfg beside it moves sys.prefix); a bare name as the hook's PATH finds it, with no
#     relative entry ahead of it (the Bash tool inherits the same environment; a PATH the line changes is an assignment).
#   the options: exactly -I and -S.  Without -I a PYTHONPATH json.py runs before the program, without -I or -S a user-site .pth;
#     -X pycache_prefix reads an unchecked-hash pyc of a stdlib module from anywhere; -c, -m and - run other code.
#   the environment: no wrapper (env, exec -a, nohup, sudo ... change the environment, argv[0] or the user) and no variable
#     assigned earlier in the line or as a prefix (DYLD_INSERT_LIBRARIES loads a dylib under -I -S; assigning a variable the shell
#     already exports changes what the interpreter inherits), except SPUD_HOME naming the root: the launcher caches its bytecode
#     under <SPUD_HOME>/.spud/pycache and loads an unchecked-hash pyc it finds there.
PYTHON_FLAGS_RE = re.compile(r"-[IS]+")


def python_options_vouched(options):
    """The options before the script are -I and -S, separately or clustered, both of them and nothing else."""
    letters = set()
    for o in options:
        if not PYTHON_FLAGS_RE.fullmatch(o):
            return False
        letters.update(o[1:])
    return letters == {"I", "S"}


def unresolvable_word(word):
    return "$" in word or "`" in word or hookio.SUBST in word


def interpreter_vouched(word, cwds):
    """The interpreter word runs the file the hook runs on (sys.executable), found in the same directory: an absolute path,
    a relative path from every directory the shell may be in, or a bare name as PATH finds it."""
    if unresolvable_word(word) or word.startswith("~"):
        return False
    exe = sys.executable
    want = (worktrees.file_identity(exe), worktrees.file_identity(os.path.dirname(exe)))
    if None in want:
        return False

    def same(path):
        return (worktrees.file_identity(path), worktrees.file_identity(os.path.dirname(path))) == want

    if "/" not in word:
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if not os.path.isabs(entry):
                return False  # an empty or relative entry is the shell's working directory, not the hook's
            found = os.path.join(entry, word)
            if os.path.isfile(found) and os.access(found, os.X_OK):
                return same(found)
        return False
    if os.path.isabs(word):
        return same(word)
    return bool(cwds) and all(same(os.path.join(c, word)) for c in cwds)


def launcher_vouched(script, cwds, launcher):
    """The script is the running tool's own bin/spud (SPD-097: the tool repository's, which the hook itself runs; never the
    home's, which has none), the same file in the same directory, from every directory the shell may be in."""
    if unresolvable_word(script) or (script.startswith("~") and not script.startswith("~/")):
        return False
    real = os.path.realpath(launcher)
    want = (worktrees.file_identity(real), worktrees.file_identity(os.path.dirname(real)))
    if None in want:
        return False
    p = os.path.expanduser(script) if script.startswith("~/") else script
    if os.path.isabs(p):
        paths = [p]
    elif cwds:
        paths = [os.path.join(c, p) for c in cwds]
    else:
        return False
    return all((worktrees.file_identity(x), worktrees.file_identity(os.path.dirname(os.path.realpath(x)))) == want for x in paths)


def spud_home_vouched(value, home):
    """A SPUD_HOME assignment names the ledger root itself: an absolute path to the same directory."""
    if not value or unresolvable_word(value) or not os.path.isabs(value):
        return False
    ident = worktrees.file_identity(value)
    return ident is not None and ident == worktrees.file_identity(home)


def vouched_spud_call(a, interpreter, options, script, prefixed):
    """True when the hook may allow this python spud call without the harness's prompt (SPD-032)."""
    if prefixed or a.home is None or a.launcher is None or not python_options_vouched(options):
        return False
    for name, value in a.vars.items():
        if name != "SPUD_HOME" or not spud_home_vouched(value, a.home):
            return False
    return interpreter_vouched(interpreter, a.cwds) and launcher_vouched(script, a.cwds, a.launcher)


QUIET_TARGETS = ("/dev/null", "/dev/stdout", "/dev/stderr")
# The order bash_reason reads a line's findings in: a refusal the words as spelled already earn first, then the ones that are
# the hook's last resort -- a verb it cannot place among git's own commands and a repository it cannot read (SPD-047), a
# program name the shell would not find where the hook looked (SPD-062: a PATH the line assigns, a hashed name; SPD-049 gave
# them their entry, which wave 2 could not) -- then a word it cannot resolve at all (SPD-043).  So `touch push; git p?sh` still
# names `git push`, `git -C /tmp commit` the verb, and `PATH=<dir> git status; git push` the push.  All of them refuse; the
# entry decides only which reason a line of several commands answers with.
FINDING_LAST = {"git-verb": 1, "git-repo": 1, "path": 1, "hashed": 1, "var-word": 2, "var-doubt": 2}


def actor_is_self(con, actor, caller_member, caller_agent_id):
    if actor == caller_agent_id:
        return True
    ticket = lookup.get_ticket_by_id(con, caller_member["ticket_id"])
    return actor in ("%s/%s" % (ticket["team_key"], caller_member["name"]), "%s/%s" % (ticket["team_key"], caller_member["lineage"]))


def spud_call_writes(call):
    """Whether a recognized spud call writes the ledger: everything but the read commands and --help (SPD-014)."""
    if call["help"] or call["command"] is None:
        return False
    return call["command"] not in hookio.READ_ONLY_COMMANDS and (call["command"], call["subcommand"]) not in hookio.READ_ONLY_SUBCOMMANDS
