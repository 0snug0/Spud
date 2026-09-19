"""shell/git_verbs: Law 7's verbs: git's own commands, unknown verbs, repository targets.  Moved from bin/spud_ledger.py (SPD-065)."""

import contextlib
import json
import os

from . import git_programs, globbing, prepare, spud_calls, syntax
from ..core import homeconf, lazy
from ..hooks import hookio


def git_verb(words):
    i = 1
    while i < len(words):
        w = words[i]
        if w in syntax.GIT_GLOBAL_VALUE_FLAGS:
            i += 2
            continue
        if w.startswith("-"):
            i += 1
            continue
        return w, words[i + 1 :]
    return None, []


_GIT_OWN_COMMANDS = None  # git's own command set, read once per process
GIT_COMMANDS_CACHE = "git-commands.json"  # ... and kept in the home's state directory between them


def git_binary_fingerprint():
    """What changes when the git the hook would run changes, read without running it: the path PATH finds for it and its
    stat.  None when there is nothing to stat there (no git: the list is then read the slow way and fails closed)."""
    path = globbing.command_path("git")
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [path, st.st_mtime_ns, st.st_size, st.st_ino]


def git_own_commands(home=None):
    """The names git dispatches itself, from the git the hook runs (`git --list-cmds=main`: 174 on git 2.54.0, a superset of
    --list-cmds=builtins' 147), with git_env()'s sanitised environment -- the hook's own, never the line's HOME or PATH.
    None when git cannot be run or names nothing, which fails the unknown-verb check closed (SPD-047).

    Read once per process, and kept in <home>/.spud/git-commands.json under the fingerprint of the git binary it was read
    from, so a hook runs git only after git itself changes: the Bash hook runs on every command line, and the list costs
    about 8 ms with the subprocess import, which SPD-016 keeps off the hook path.  A cache that is missing, unreadable or
    stale is read anew; one that cannot be written is left unwritten.  It lives in the state directory, which the edit and
    Bash hooks refuse to everyone, so nothing a member writes can widen git's command set."""
    global _GIT_OWN_COMMANDS
    if _GIT_OWN_COMMANDS is not None:
        return _GIT_OWN_COMMANDS or None
    state = os.path.join(str(home), hookio.STATE_DIR) if home else None
    cache = os.path.join(state, GIT_COMMANDS_CACHE) if state else None
    fingerprint = git_binary_fingerprint() if cache else None
    if fingerprint is not None:
        with contextlib.suppress(OSError, ValueError):
            with open(cache, encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict) and stored.get("git") == fingerprint \
                    and isinstance(stored.get("commands"), list) and all(isinstance(c, str) for c in stored["commands"]):
                _GIT_OWN_COMMANDS = frozenset(stored["commands"])
                return _GIT_OWN_COMMANDS or None
    try:
        proc = lazy.subprocess.run(["git", "--list-cmds=main"], capture_output=True, text=True, errors="replace",
                              env=homeconf.git_env(), timeout=10)
        _GIT_OWN_COMMANDS = frozenset(proc.stdout.split()) if proc.returncode == 0 else frozenset()
    except (OSError, lazy.subprocess.TimeoutExpired):
        _GIT_OWN_COMMANDS = frozenset()
    if _GIT_OWN_COMMANDS and fingerprint is not None and os.path.isdir(state):
        tmp = "%s.%d.tmp" % (cache, os.getpid())
        with contextlib.suppress(OSError):
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"git": fingerprint, "commands": sorted(_GIT_OWN_COMMANDS)}, f)
            os.replace(tmp, cache)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
    return _GIT_OWN_COMMANDS or None


def git_unknown_verb(verb, home=None):
    """("verb", name) when the verb is not one of git's own commands, ("unreadable", name) when the hook could not read that
    command list at all, else None (SPD-047).  git ignores an alias that hides one of its own commands ("aliases that hide
    existing Git commands are ignored", git-config(1); `alias.log` and `alias.status` were ignored, probed), so an alias can
    only introduce a verb git does not have: a verb outside git's own set is the tell for every alias source at once -- a
    repository's .git/config reached by -C/--git-dir/GIT_DIR, ~/.gitconfig, $XDG_CONFIG_HOME/git/config, the system config, an
    include, GIT_CONFIG_* -- and for an external `git-<verb>` program on PATH, which the hook cannot read either.  A verb built
    from an expansion keeps SPD-043's own reason."""
    if verb is None:
        return None
    name = prepare.deglob(verb)
    if spud_calls.unresolvable_word(name):
        return None
    commands = git_own_commands(home)
    if commands is None:
        return "unreadable", name
    return None if name in commands else ("verb", name)


def git_repo_targets(words, variables):
    """Every directory a git call points git at, as (spelling, path): the composed `-C` chain (git chdirs there before it reads
    any config, and a repeated -C is relative to the previous one), the values of --git-dir and --work-tree (spaced and `=`
    forms) resolved against that chain wherever they stand on the line, and GIT_DIR, GIT_WORK_TREE and GIT_COMMON_DIR in force.
    Each names a repository whose .git/config the hook cannot read (SPD-047).  A path is absolute, or relative to the directory
    the shell is in."""
    base, options = None, []
    i = 1
    while i < len(words):
        w = words[i]
        if w == "-C":
            value = words[i + 1] if i + 1 < len(words) else None
            if value:
                base = value if os.path.isabs(value) or base is None else os.path.join(base, value)
            i += 2
            continue
        key, sep, attached = w.partition("=")
        if key in git_programs.GIT_REPO_OPTIONS:
            value = attached if sep else (words[i + 1] if i + 1 < len(words) else None)
            if value:
                options.append((w if sep else "%s %s" % (key, value), value))
            i += 1 if sep else 2
            continue
        if w in syntax.GIT_GLOBAL_VALUE_FLAGS:
            i += 2
            continue
        if w.startswith("-"):
            i += 1
            continue
        break  # the verb: git's global options are done
    for name in git_programs.GIT_REPO_ENV_VARS:
        if variables.get(name):
            options.append(("%s=%s" % (name, variables[name]), variables[name]))
    targets = [] if base is None else [("-C %s" % base, base)]
    for spelled, value in options:
        targets.append((spelled, value if os.path.isabs(value) or base is None else os.path.join(base, value)))
    return targets


# SPD-066: what a git_repo_targets entry names, read back from the spelling that function gives it (`-C <dir>`, `<option>=<v>`
# or `<option> <v>`, `<VAR>=<v>`).  The -C chain is where git discovers the repository from; --git-dir and GIT_DIR name the
# git directory itself and GIT_COMMON_DIR the common one, whose config and hooks git reads, each taken as given with no
# discovery; --work-tree and GIT_WORK_TREE name only the work tree: git still discovers the repository from the directory
# it runs in (probed on 2.54.0: from a directory in no repository `git --work-tree=<repo> rev-parse --git-dir` fails, and
# from another repository it reads that one's config).
GIT_TARGET_KINDS = {"-C": "chdir", "--git-dir": "gitdir", "GIT_DIR": "gitdir", "GIT_COMMON_DIR": "common",
                    "--work-tree": "worktree", "GIT_WORK_TREE": "worktree"}


def git_target_kind(spelled):
    """"chdir", "gitdir", "common" or "worktree" for one (spelling, path) git_repo_targets returned."""
    return GIT_TARGET_KINDS.get(spelled.split("=", 1)[0].split(" ", 1)[0], "worktree")


def git_write_option_targets(words):
    """Every file or directory a git call's own options name for git to write (SPD-049), as (the spelling a reason names
    it by, the path word as the line spells it): the diff option syntax.GIT_FILE_OPTIONS on any verb, this verb's entry
    in syntax.GIT_VERB_FILE_OPTIONS, and the positional forms of syntax.GIT_VERB_FILE_POSITIONALS.

    Read in every spelling git takes: spaced, `=`-attached, a short option with its value attached or clustered, and any
    `--`-prefix of a long option, git's parse-options resolving an unambiguous one.  A verb GIT_WRITE_VERBS refuses whole
    carries no target: Law 7's verb is the reason a member gets, and `git init`/`git clone` are Spud's own.  A verb Law 7
    refuses through GIT_MEMBER_VERBS instead (SPD-087: read-tree, checkout-index, index-pack, repack, pack-objects,
    commit-graph, multi-pack-index, credential-store ...) keeps its target and its entry: Law 7 does not bind Spud, and
    the path rule still holds his own call to it."""
    verb, args = git_verb(words)
    if verb is None or verb in syntax.GIT_WRITE_VERBS:
        return []
    longs, shorts = syntax.GIT_VERB_FILE_OPTIONS.get(verb, ((), ""))
    longs = tuple(longs) + syntax.GIT_FILE_OPTIONS
    out, i = [], 0
    while i < len(args):
        w = args[i]
        if w == "--":
            break  # nothing after the end-of-options marker is an option (a path or a revision, not a target)
        key, sep, attached = w.partition("=")
        if key.startswith("--") and len(key) >= 3 and any(opt.startswith(key) for opt in longs):
            value = attached if sep else (args[i + 1] if i + 1 < len(args) else None)
            if value:
                out.append(("%s %s" % (verb, w if sep else "%s %s" % (key, value)), value))
            i += 1 if sep else 2
            continue
        if shorts and w.startswith("-") and not w.startswith("--") and len(w) > 1:
            k = next((j for j in range(1, len(w)) if w[j] in shorts), None)
            if k is not None:
                rest = w[k + 1 :]
                value = rest or (args[i + 1] if i + 1 < len(args) else None)
                if value:
                    out.append(("%s %s" % (verb, w if rest else "%s %s" % (w, value)), value))
                i += 1 if rest else 2
                continue
        i += 1
    return out + git_write_positional_targets(verb, args)


def git_write_positional_targets(verb, args):
    """The positional words of a verb whose writing form names its file that way (SPD-049): `git bundle create <file>`,
    `git mailinfo <msg> <patch>`, `git pack-objects <base-name>`.  An option is skipped as spelled; which of a verb's
    options take a separate value is not known here, so such a value is read as a positional and checked too, which fails
    closed."""
    entry = syntax.GIT_VERB_FILE_POSITIONALS.get(verb)
    if not entry:
        return []
    subcommand, count = entry
    positionals, options = [], True
    for w in args:
        if options and w == "--":
            options = False
            continue
        if options and w.startswith("-") and len(w) > 1:
            continue
        positionals.append(w)
    if subcommand is not None:
        if not positionals or positionals[0] != subcommand:
            return []
        positionals = positionals[1:]
    return [("%s %s" % (" ".join(x for x in (verb, subcommand) if x), p), p) for p in positionals[:count]]


def git_write_env_targets(variables):
    """Every file or directory a git call writes because of a variable in force on the line (SPD-049), as (the spelling a
    reason names it by, the path word): a GIT_TRACE* sibling whose value is a path git appends to -- an absolute one, or
    a `~` the shell expanded before git saw it, a descriptor, an off value and a relative one writing nothing -- or whose
    value the hook cannot read, which fails closed, and GIT_INDEX_FILE and GIT_OBJECT_DIRECTORY, whose value is a path
    whatever its shape.  `variables` holds each value as the shell passes it to git, a `$NAME` the line settled resolved
    (SPD-127), so the shape that decides here is the one git sees.  A fixed order so the reason is deterministic."""
    out = []
    for name in sorted(variables):
        value = variables[name]
        if not value:
            continue
        if name in syntax.GIT_WRITE_PATH_ENV_VARS:
            out.append(("%s=%s" % (name, value), value))
        elif name.startswith(syntax.GIT_TRACE_VAR_PREFIX) and (value.startswith(("/", "~")) or spud_calls.unresolvable_word(value)):
            out.append(("%s=%s" % (name, value), value))
    return out


def git_write_targets(words, variables):
    """Every file or directory a git call writes beside the repository it reads (SPD-049): what its options name, then
    what the environment in force names.  Each is checked with the path rule in bash_reason, like a redirection target."""
    return git_write_option_targets(words) + git_write_env_targets(variables)


def flag_list_refused(verb, args, read_flags, value_flags):
    """`git branch`/`git tag` listing forms are reads; a name or a modifying flag writes."""
    positional_allowed = False
    i = 0
    while i < len(args):
        w = args[i]
        if "=" in w and w.split("=", 1)[0] in value_flags:
            positional_allowed = True
            i += 1
            continue
        if w in value_flags:
            positional_allowed = True
            i += 2
            continue
        if w in read_flags:
            if w in ("--list", "-l"):
                positional_allowed = True
            i += 1
            continue
        if w.startswith("-"):
            return verb
        if not positional_allowed:
            return verb
        i += 1
    return None


def git_not_allowed(verb):
    """The verb when Law 7 allows a member no form of it (SPD-087): every name git answers to outside
    syntax.GIT_MEMBER_VERBS, so a plumbing verb the old denylist never held -- and one a later git adds -- is refused
    rather than silent.

    Read after git_refused, so Law 7's own table and its subcommand cases keep their reason when the hook cannot read
    git's command list, and after git_unknown_verb, so the name here is one of git's own commands: a name git does not
    know is SPD-047's, whose reason names the alias or the external `git-<verb>` program it must be.  A verb built from
    an expansion is SPD-043's, which doubts the whole word."""
    if verb is None or verb in syntax.GIT_MEMBER_VERBS:
        return None
    name = prepare.deglob(verb)
    return None if (name in syntax.GIT_MEMBER_VERBS or spud_calls.unresolvable_word(name)) else verb


def git_refused(verb, args):
    """The verb when Law 7's own table or one of its subcommand cases refuses it for a spudagent, else None.  Every
    other name git knows is refused by git_not_allowed; these are the ones whose refusal stands whatever git's command
    list says (SPD-087)."""
    if verb is None:
        return None
    if verb in syntax.GIT_WRITE_VERBS:
        return verb
    if verb == "stash":
        return None if (args and args[0] in ("list", "show")) else verb
    if verb == "worktree":
        return None if (args and args[0] == "list") else verb
    if verb == "remote":
        return verb if (args and args[0] in ("add", "remove", "rm", "rename", "set-url", "set-head", "set-branches", "prune", "update")) else None
    if verb == "reflog":
        return verb if (args and args[0] in ("expire", "delete")) else None
    if verb == "branch":
        return flag_list_refused(verb, args, syntax.BRANCH_READ_FLAGS, syntax.BRANCH_READ_VALUE_FLAGS)
    if verb == "tag":
        return flag_list_refused(verb, args, syntax.TAG_READ_FLAGS, syntax.TAG_READ_VALUE_FLAGS)
    if verb == "config":
        # Two syntaxes (SPD-063, probed on git 2.54.0): the flags, where a key and a value write and one positional reads,
        # and the 2.46 subcommands, where the verb is the first positional -- `git config edit` opens the file in an editor
        # with a single positional, and `git config get <key>` reads with two.  A read selector (--get, --get-urlmatch,
        # --get-color ...) takes positionals of its own, so they are never a key and a value.
        sub, positionals, selector = None, 0, False
        i = 0
        while i < len(args):
            w = args[i]
            if w in syntax.CONFIG_WRITE_FLAGS:
                return verb
            if w in syntax.CONFIG_VALUE_FLAGS:
                i += 2
                continue
            if w in syntax.CONFIG_READ_SELECTORS:
                selector = True
                i += 1
                continue
            if w.startswith("-"):
                i += 1
                continue
            if positionals == 0 and not selector:
                sub = w
            positionals += 1
            i += 1
        if sub in syntax.CONFIG_WRITE_SUBCOMMANDS:
            return verb
        if sub in syntax.CONFIG_READ_SUBCOMMANDS:
            return None
        return verb if (positionals >= 2 and not selector) else None
    return None
