"""shell/git_verbs: Law 7's verbs: git's own commands, unknown and refused verbs, words xargs adds, and repository targets."""

import contextlib
import json
import os

from . import git_programs, git_writes, globbing, prepare, spud_calls, syntax
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
    None when git cannot be run or names nothing, which fails the unknown-verb check closed.

    Read once per process, and kept in <home>/.spud/git-commands.json under the fingerprint of the git binary it was read
    from, so a hook runs git only after git itself changes: the Bash hook runs on every command line, and the list costs
    about 8 ms with the subprocess import, which stays off the hook path.  A cache that is missing, unreadable or
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
    command list at all, else None.  git ignores an alias that hides one of its own commands ("aliases that hide
    existing Git commands are ignored", git-config(1); `alias.log` and `alias.status` were ignored, probed), so an alias can
    only introduce a verb git does not have: a verb outside git's own set is the tell for every alias source at once -- a
    repository's .git/config reached by -C/--git-dir/GIT_DIR, ~/.gitconfig, $XDG_CONFIG_HOME/git/config, the system config, an
    include, GIT_CONFIG_* -- and for an external `git-<verb>` program on PATH, which the hook cannot read either.  A verb built
    from an expansion keeps the unresolvable expansion's own reason."""
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
    Each names a repository whose .git/config the hook cannot read.  A path is absolute, or relative to the directory
    the shell is in; a word that starts with `~` is the shell's expansion (chdir_join), and a `~` after an option's `=`
    reaches git as spelled, a path relative like any other (SPD-227)."""
    base, options = None, []
    i = 1
    while i < len(words):
        w = words[i]
        if w == "-C":
            value = words[i + 1] if i + 1 < len(words) else None
            if value:
                base = chdir_join(base, value)
            i += 2
            continue
        key, sep, attached = w.partition("=")
        if key in git_programs.GIT_REPO_OPTIONS:
            value = attached if sep else (words[i + 1] if i + 1 < len(words) else None)
            if value:
                options.append((w if sep else "%s %s" % (key, value), "./" + value if sep and value.startswith("~") else value))
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
        targets.append((spelled, chdir_join(base, value)))
    return targets


# what a git_repo_targets entry names, read back from the spelling that function gives it (`-C <dir>`, `<option>=<v>`
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


def anchored(path):
    """True when a path word names its place whatever directory git runs in: absolute, or starting with `~`, which the
    shell expands (the house reading of a leading tilde, as tree_writes and runner_files read one)."""
    return os.path.isabs(path) or path.startswith("~")


def chdir_join(base, value):
    """`value` read from `base`, the directory the -C chain has reached (None: the one git starts in), as git reads a
    relative -C and every relative path it is given after it; an anchored value starts again (SPD-227: `-C docs -C ~/x`
    is the home's x, not docs/~/x)."""
    return value if base is None or anchored(value) else os.path.join(base, value)


def git_unspelled_word(words):
    """The first word of a git call holding an operand the line does not spell where git reads its verb or an option, or
    None (SPD-230).  What xargs reads from its input (syntax.INPUT_OPERAND, appended by the caller where xargs appends it)
    may be any option before the first `--` -- a file git writes, a program it runs, config, another repository -- and,
    in the verb's place, any verb; so may a word that starts with it, or with a `-` and no `=` before it (`-{input}`,
    `--{input}`), while one whose option the line has settled (`--author={input}`) or that a spaced file option takes as
    its path (`-o {input}`, the write channel's) is read as it stands.  A verb syntax.GIT_OPERAND_VERBS reads by operand
    holds such an operand anywhere, after `--` too; find's `{}` is a path under starting points the line spells, read as
    one except in the verb's place.  The values of git's own global value flags are their own readers' (-C, -c,
    --git-dir ...)."""
    i = 1
    while i < len(words):
        w = words[i]
        if input_option(w):
            return w
        if w in syntax.GIT_GLOBAL_VALUE_FLAGS:
            i += 2
            continue
        if not w.startswith("-"):
            break
        i += 1
    else:
        return None
    verb, args = words[i], words[i + 1 :]
    if syntax.unknown_operand(verb):
        return verb
    if verb in syntax.GIT_OPERAND_VERBS:
        return next((w for w in args if syntax.unknown_operand(w)), None)
    longs, shorts = git_writes.git_file_options(verb)
    k = 0
    while k < len(args):
        w = args[k]
        if w == "--":
            return None
        if input_option(w):
            return w
        k += 2 if (git_writes.file_option_spelling(w, longs, shorts) or (None, False))[1] else 1
    return None


def input_option(word):
    """True when a word holds what xargs reads from its input where git may read it as an option: nothing but a `-`
    run with no `=` stands before it (`{input}`, `-{input}`, `--{input}`, `-o{input}`)."""
    if syntax.INPUT_OPERAND not in word:
        return False
    head = prepare.deglob(word[: word.index(syntax.INPUT_OPERAND)])
    return not head or (head.startswith("-") and "=" not in head)


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
    """The verb when Law 7 allows a member no form of it: every name git answers to outside
    syntax.GIT_MEMBER_VERBS, so a plumbing verb the old denylist never held -- and one a later git adds -- is refused
    rather than silent.

    Read after git_refused, so Law 7's own table and its subcommand cases keep their reason when the hook cannot read
    git's command list, and after git_unknown_verb, so the name here is one of git's own commands: a name git does not
    know is the unknown-verb check's, whose reason names the alias or the external `git-<verb>` program it must be.  A
    verb built from an expansion is the unresolvable expansion's, which doubts the whole word."""
    if verb is None or verb in syntax.GIT_MEMBER_VERBS:
        return None
    name = prepare.deglob(verb)
    return None if (name in syntax.GIT_MEMBER_VERBS or spud_calls.unresolvable_word(name)) else verb


def git_refused(verb, args):
    """The verb when Law 7's own table or one of its subcommand cases refuses it for a spudagent, else None.  Every
    other name git knows is refused by git_not_allowed; these are the ones whose refusal stands whatever git's command
    list says."""
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
        # Two syntaxes (probed on git 2.54.0): the flags, where a key and a value write and one positional reads,
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
