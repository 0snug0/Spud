"""shell/git_verbs: Law 7's verbs: git's own commands, unknown and refused verbs, words xargs adds, repository targets, and a member's scratch clone."""

import contextlib
import json
import os

from . import arg_writes, bash_rule, expansions, git_programs, git_writes, globbing, prepare, spud_calls, syntax
from ..core import homeconf, lazy
from ..hooks import hookio, pathrule, worktrees


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


# SPD-095 (Eric, 2026-09-24: "allow clone into scratch"): `git clone` stays in syntax.GIT_WRITE_VERBS, and a member's
# clone is read apart from it (clone_finding, clone_reason): allowed only when every directory it writes lies under the
# outside allowlist (pathrule.outside_roots: the session scratchpad and the system temp directories) and outside every
# registered project's checkouts and the home, and it carries no option that runs or plants a program or config.  It
# creates a repository nobody else reads from a source it only reads -- closer to `git archive` than to checkout -- and
# reproduces what a member cannot otherwise, a CI checkout's shallow clone.  It sits beside git_refused, whose verb table
# it narrows for a member; clone_finding and clone_reason are its whole surface, so it is a seam of its own (a
# shell/git_clone) the day this module wants to shed it.
#
# Its options, from git 2.54.0 (Apple Git-157): git-clone(1) and builtin/clone.c's option table as the git binary holds its
# strings, in order (verbosity, progress, reject-shallow, no-checkout, bare, naked, mirror, local, no-hardlinks, shared,
# recurse-submodules, recursive, jobs, template, reference, reference-if-able, dissociate, origin, branch, revision,
# upload-pack, depth, shallow-since, shallow-exclude, single-branch, tags, shallow-submodules, separate-git-dir,
# ref-format, config, server-option, ipv4, ipv6, filter, also-filter-submodules, remote-submodules, sparse, bundle-uri);
# `git clone -h` itself is a clone, which the hook refuses the member that would read it.  "value" takes the next word
# or an `=` one, "optional" only an `=` one (--recurse-submodules[=<pathspec>]), and every other a flag.  `--naked` is a
# hidden --bare and `--recursive` an alias of --recurse-submodules.  parse-options takes any unambiguous prefix of a long
# option and its `--no-` form (and `--checkout`, `--hardlinks` for the two named `no-...`), and a short cluster up to a
# letter that takes a value, which takes the rest of the cluster or the next word.
CLONE_LONG_OPTIONS = {
    "verbose": "flag", "quiet": "flag", "progress": "flag", "reject-shallow": "flag", "no-checkout": "flag", "bare": "flag",
    "naked": "flag", "mirror": "flag", "local": "flag", "no-hardlinks": "flag", "shared": "flag",
    "recurse-submodules": "optional", "recursive": "optional", "jobs": "value", "template": "value", "reference": "value",
    "reference-if-able": "value", "dissociate": "flag", "origin": "value", "branch": "value", "revision": "value",
    "upload-pack": "value", "depth": "value", "shallow-since": "value", "shallow-exclude": "value", "single-branch": "flag",
    "tags": "flag", "shallow-submodules": "flag", "separate-git-dir": "value", "ref-format": "value", "config": "value",
    "server-option": "value", "ipv4": "flag", "ipv6": "flag", "filter": "value", "also-filter-submodules": "flag",
    "remote-submodules": "flag", "sparse": "flag", "bundle-uri": "value",
}
CLONE_ALIASES = {"recursive": "recurse-submodules"}
CLONE_SHORT_FLAGS = "vqnls46"
CLONE_SHORT_VALUES = {"j": "jobs", "o": "origin", "b": "branch", "u": "upload-pack", "c": "config"}
# The options that run or plant a program or config, refused a member in any spelling (their `--no-` forms plant nothing):
# -c/--config writes config into the new repository before the fetch and the checkout (core.hooksPath, a filter driver,
# an alias ...), --template copies a directory of the line's choosing into its .git (hooks among it), -u/--upload-pack
# names the program run for the source, and the submodule options clone further repositories from URLs the source's
# .gitmodules names, into paths inside the new one, with their own transport and update rules.
CLONE_PLANTING = frozenset({"config", "template", "upload-pack", "recurse-submodules", "shallow-submodules",
                            "remote-submodules", "also-filter-submodules"})
# The bare forms: the target is then the git directory itself (<name>.git when git names it), not <dir>/.git.
CLONE_BARE = frozenset({"bare", "naked", "mirror"})
# An environment variable that turns off the protection git added against a clone writing its own hooks
# (CVE-2024-32002): a clone carrying it plants as much as --template does.
CLONE_PLANTING_VARS = ("GIT_CLONE_PROTECTION_ACTIVE",)
CLONE_REASON = (
    "Law 7: a spudagent runs `git clone` only into a scratch directory, and this clone is not one: %s. A member's clone is"
    " allowed when every directory it writes -- its directory operand, else the name git makes of the source in the"
    " directory git runs in, and --separate-git-dir's, GIT_WORK_TREE's or --work-tree's -- is one the line spells out, lies"
    " under this session's scratchpad (under %s/) or a system temp directory (%s), and is outside every registered"
    " project's checkouts and Spud's home, and when it carries no option that runs or plants a program or config (-c or"
    " --config, --template, -u or --upload-pack, --recurse-submodules or --recursive, --shallow-submodules,"
    " --remote-submodules, --also-filter-submodules, GIT_CLONE_PROTECTION_ACTIVE). Everything else about clone is Spud's;"
    " Spud commits, after the outcome is recorded")


def clone_long_option(key):
    """(the option, whether it is its `--no-` form) that `--<key>` names as git's parse-options reads it -- exact, else
    the one option an unambiguous prefix names -- or None when it names none or more than one (git: "unknown option",
    "ambiguous option")."""
    spellings = []
    for name in CLONE_LONG_OPTIONS:
        option = CLONE_ALIASES.get(name, name)
        spellings += [(name, option, False), ("no-" + name, option, True)]
        if name.startswith("no-"):
            spellings.append((name[3:], option, True))
    exact = [(option, negated) for spelled, option, negated in spellings if spelled == key]
    if exact:
        return exact[0]
    found = {(option, negated) for spelled, option, negated in spellings if key and spelled.startswith(key)}
    return found.pop() if len(found) == 1 else None


def clone_humanish(source, bare):
    """The names `git clone <source>` may make for its directory with none given, as git's git_url_basename makes one
    (dir.c, git 2.54.0): past a `scheme://` and the credentials before an `@`, trailing slashes and a `/.git` dropped, a
    port dropped from a bare host, the last component after a `/` or a `:`, less `.git` -- or `.bundle` when the source is
    a bundle file, which the hook does not read, so both -- and `.git` added for a bare clone.  [] when git guesses no
    name (it dies), or when the name holds a blank or a control character, which git rewrites."""
    start = source.find("://")
    text = source if start < 0 else source[start + 3 :]
    head = text.split("/", 1)[0]
    if "@" in head:
        text = text[text.rindex("@", 0, len(head)) + 1 :]
    text = text.rstrip("/ \t\n")
    if len(text) > 5 and text.endswith("/.git"):
        text = text[:-5].rstrip("/")
    if "/" not in text and ":" in text:
        port = text.rstrip("0123456789")
        if port.endswith(":"):
            text = port[:-1]
    name = text[max(text.rfind("/"), text.rfind(":")) + 1 :]
    out = []
    for suffix in (".git", ".bundle"):
        stem = name[: -len(suffix)] if name.endswith(suffix) else name
        if not stem or stem == "/" or any(c.isspace() or not c.isprintable() for c in stem):
            continue
        guessed = stem + ".git" if bare else stem
        if guessed not in out:
            out.append(guessed)
    return out


def clone_reading(words):
    """(what refuses the clone outright, as the text a reason names it by, or None; [(the spelling a reason names a
    directory by, the word git reads it from)]) for a `git clone` call's arguments, read as parse-options reads them: an
    option that plants a program or config, a word the hook cannot read (an expansion the line does not settle, a glob,
    an operand it does not spell, an option git does not have or cannot tell apart), and a count of operands git does not
    take are each the first; else the directories it writes -- its directory operand, or the names clone_humanish makes of
    the source, and --separate-git-dir's.

    A word the line does not settle refuses the clone wherever it stands, a value's included: an unquoted expansion may
    vanish or split in bash, and a glob expand to several words, which moves every operand after it (`-b $B /tmp/r
    /tmp/ok` with B empty clones /tmp/ok into ./ok), and one may become a planting option.  A `$NAME` the line settled
    reaches here resolved (clone_finding)."""
    _verb, args = git_verb(words)
    positionals, dirs, bare, options = [], [], False, True
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        if clone_unsettled(word):
            return "`%s` is a word the line does not settle (an expansion, a glob, an operand it does not spell)" % prepare.deglob(word), []
        w = prepare.deglob(word)
        if not options or w == "-" or not w.startswith("-"):
            positionals.append(w)
            continue
        if w == "--":
            options = False
            continue
        if w.startswith("--"):
            key, sep, attached = w[2:].partition("=")
            found = clone_long_option(key)
            if found is None:
                return "`%s` is an option git clone does not have, or one it cannot tell apart" % w, []
            option, negated = found
            if option in CLONE_PLANTING and not negated:
                return "`%s` runs or plants a program or config" % w, []
            kind = CLONE_LONG_OPTIONS[option]
            if option in CLONE_BARE:
                bare = not negated
            if negated or kind != "value":
                if sep and (negated or kind == "flag"):
                    return "`%s` gives a value to an option that takes none" % w, []
                continue
            value = attached if sep else (prepare.deglob(args[i]) if i < len(args) else None)
            if not sep:
                if i < len(args) and clone_unsettled(args[i]):
                    return "`%s %s` is a word the line does not settle" % (w, value), []
                i += 1
            if value is None:
                return "`%s` takes a value the line does not give" % w, []
            if option == "separate-git-dir":
                dirs.append(("--separate-git-dir %s" % value, "./" + value if sep and value.startswith("~") else value))
            continue
        for k in range(1, len(w)):
            letter = w[k]
            if letter in CLONE_SHORT_FLAGS:
                continue
            option = CLONE_SHORT_VALUES.get(letter)
            if option is None:
                return "`%s` holds an option git clone does not have" % w, []
            if option in CLONE_PLANTING:
                return "`%s` runs or plants a program or config" % w, []
            if not w[k + 1 :]:
                if i >= len(args):
                    return "`%s` takes a value the line does not give" % w, []
                if clone_unsettled(args[i]):
                    return "`%s %s` is a word the line does not settle" % (w, prepare.deglob(args[i])), []
                i += 1  # jobs, origin or branch: a value that names no directory
            break
    if len(positionals) == 2:
        return None, [("the directory %s" % positionals[1], positionals[1])] + dirs
    if len(positionals) != 1:
        return "git clone takes a repository and at most one directory, and this line gives %d" % len(positionals), []
    source = positionals[0]
    names = [] if source.startswith("~") and "/" not in source else clone_humanish(source, bare)
    if not names:
        return "the hook cannot tell which directory git would make of `%s`; name the directory" % source, []
    return None, [("the directory %s, which git makes of %s" % (name, source), name) for name in names] + dirs


def clone_unsettled(word):
    """True when the shell may make of a masked word something the line does not spell: an expansion it does not
    settle, a glob, an operand xargs or find hands the command."""
    return spud_calls.unresolvable_word(word) or syntax.GLOB_RE.search(word) is not None


def clone_finding(words, a):
    """The "git-clone" finding's detail for a `git clone` call on the line: (what refuses it outright or None, ((the
    spelling a reason names a directory by, its path from the directory the -C chain reaches), ...), the directories the
    shell may be in).  Every word and variable is read as the shell passes it to git, a `$NAME` the line settled put in
    its place (arg_writes.resolved), so a member clones into a scratchpad it named in a variable.  --work-tree,
    GIT_WORK_TREE, --git-dir, GIT_DIR and GIT_COMMON_DIR are directories the clone writes too (clone takes GIT_WORK_TREE
    as its work tree and the target as the bare git directory then; the others are read as writes, fail closed), and
    GIT_CLONE_PROTECTION_ACTIVE refuses it outright.  bash_rule holds a member to clone_reason on it and leaves Spud's
    clone alone, as Law 7 leaves him."""
    words = [arg_writes.resolved(w, a) for w in words]
    variables = {n: arg_writes.resolved(v, a) for n, v in a.vars.items()}
    for name in CLONE_PLANTING_VARS:
        if name in variables:
            return ("%s=%s turns off git's protection against a clone that writes its own hooks" % (name, variables[name]), (), a.cwds)
    problem, dirs = clone_reading(words)
    if problem is not None:
        return problem, (), a.cwds
    base, _ = git_writes.git_chdir_and_config(words, ())
    out = [(shown, chdir_join(base, path)) for shown, path in dirs]
    out += [(spelled, target) for spelled, target in git_repo_targets(words, variables) if git_target_kind(spelled) != "chdir"]
    return None, tuple(dict.fromkeys(out)), a.cwds


def clone_reason(ctx, con, detail):
    """The reason a member's `git clone` is refused (CLONE_REASON), or None, for a clone_finding detail: a problem it
    names outright; a directory the hook cannot resolve; one any reading of which (lexical or real, pathrule's) lies in a
    registered project's checkout or the home, holds a checkout at or under it, has a .git component, or lies outside the
    outside allowlist (the scratchpad and the temp directories, where a member writes outside every project)."""
    problem, dirs, cwds = detail
    if isinstance(cwds, expansions.TrapDirs):
        cwds = cwds.dirs()
    if problem is None:
        roots = pathrule.outside_roots()
        for spelled, target in dirs:
            candidates, unresolved = bash_rule.git_target_dirs(target, cwds)
            if unresolved:
                problem = "%s is a path the hook cannot resolve (relative to a directory it cannot follow, or ~name)" % spelled
                break
            for candidate in candidates:
                readings = worktrees.path_readings(candidate, None)
                placed = worktrees.project_paths(ctx, con, candidate, None)
                if placed:
                    problem = "%s is %s, in the checkout %s" % (spelled, os.path.normpath(candidate), placed[0][1])
                elif any(pathrule.git_dir_path(r) for r in readings):
                    problem = "%s is %s, inside a git directory" % (spelled, os.path.normpath(candidate))
                elif not all(pathrule.under_outside_root(r, roots) for r in readings):
                    problem = "%s is %s, outside the scratchpad and the temp directories" % (spelled, os.path.normpath(candidate))
                elif pathrule.tree_checkout_reason(ctx, con, readings):
                    problem = "%s is %s, which holds a checkout the ledger knows" % (spelled, os.path.normpath(candidate))
                if problem:
                    break
            if problem:
                break
    if problem is None:
        return None
    # shown as every reason shows a word: the sentinels restored, xargs's input and find's {} as the line spells them
    return bash_rule.shown_word(CLONE_REASON % (problem, pathrule.SCRATCHPAD_ROOT % os.getuid(), ", ".join(pathrule.FIXED_TEMP_ROOTS)))
