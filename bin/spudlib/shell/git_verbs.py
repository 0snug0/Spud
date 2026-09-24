"""shell/git_verbs: Law 7's verbs: git's own commands, unknown verbs, repository targets, and the files a git call writes."""

import contextlib
import json
import os

from . import git_programs, globbing, prepare, spud_calls, syntax
from ..core import homeconf, lazy
from ..hooks import hookio, pathrule


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


def git_write_option_targets(words):
    """Every file a git call's own options and positional words name for git to write, as (the spelling a reason names
    it by, the path, None or the -C base a work-tree-top path is read from): the diff option syntax.GIT_FILE_OPTIONS on
    any verb, this verb's entry in syntax.GIT_VERB_FILE_OPTIONS, the positional forms of
    syntax.GIT_VERB_FILE_POSITIONALS and mailsplit's older one, each read in the shape and from the base
    syntax.GIT_FILE_FORMS gives it (placed_paths).

    Read in every spelling git takes: spaced, `=`-attached, a short option with its value attached or clustered, and any
    `--`-prefix of a long option, git's parse-options resolving an unambiguous one.  A verb GIT_WRITE_VERBS refuses whole
    carries no target: Law 7's verb is the reason a member gets, and `git init`/`git clone` are Spud's own.  A verb Law 7
    refuses through GIT_MEMBER_VERBS instead (read-tree, checkout-index, index-pack, repack, pack-objects,
    commit-graph, multi-pack-index, credential-store ...) keeps its target and its entry: Law 7 does not bind Spud, and
    the path rule still holds his own call to it."""
    verb, args = git_verb(words)
    if verb is None or verb in syntax.GIT_WRITE_VERBS:
        return []
    base, _ = git_chdir_and_config(words, ())
    longs, shorts = git_file_options(verb)
    if verb == "config" and git_refused(verb, args) is None:
        longs, shorts = syntax.GIT_FILE_OPTIONS, ""  # a read form: its --file names the file git reads, not one it writes
    out, i = [], 0
    while i < len(args):
        w = args[i]
        if w == "--":
            break  # nothing after the end-of-options marker is an option (a path or a revision, not a target)
        spelled = file_option_spelling(w, longs, shorts)
        if spelled is None:
            i += 1
            continue
        value, spaced = spelled
        if spaced:
            value = args[i + 1] if i + 1 < len(args) else None
        if value:
            shown = "%s %s" % (verb, "%s %s" % (w, value) if spaced else w)
            for shape, where in file_option_forms(verb, w, shorts):
                out += placed_paths(shown, value, shape, where, base, not spaced)
        i += 2 if spaced else 1
    shape, where = syntax.GIT_FILE_FORMS.get((verb, ""), ("file", "cwd"))
    for shown, value in git_write_positional_targets(verb, args):
        out += placed_paths(shown, value, shape, where, base, False)
    for shown, value in mailsplit_directory(verb, args):
        out += placed_paths(shown, value, "dir", "cwd", base, False)
    return list(dict.fromkeys(out))


def file_option_forms(verb, word, shorts):
    """The (shape, base) of each option a word file_option_spelling matched may be, from syntax.GIT_FILE_FORMS: an exact
    long option alone, else every one a `--` prefix names -- among the verb's own only where its own options take a prefix
    and it has no diff options (bugreport, diagnose: `--output` is -o there, probed as `--out=D` by SPD-093) -- or the
    short letter the cluster holds."""
    key = word.partition("=")[0]
    if key.startswith("--"):
        own = tuple(syntax.GIT_VERB_FILE_OPTIONS.get(verb, ((), ""))[0])
        longs = own if syntax.GIT_VERB_CWD_WRITES.get(verb, (False,))[0] else own + syntax.GIT_FILE_OPTIONS
        names = [o for o in longs if o == key] or [o for o in longs if o.startswith(key)] or [key]
    else:
        names = [next((c for c in word[1:] if c in shorts), "")]
    return list(dict.fromkeys(syntax.GIT_FILE_FORMS.get((verb, name), ("file", "cwd")) for name in names))


PICK = pathrule.NAME_CHAR + pathrule.NAME_MORE  # a name git picks, of any length, in one path component


def shaped_paths(value, shape):
    """The paths git writes through a value of this shape (syntax.GIT_FILE_FORMS), a name git picks read as PICK."""
    if shape == "dir":
        return [os.path.join(value, PICK)]
    if shape == "tree":
        return [os.path.join(value, *(PICK,) * depth) for depth in (1, 2, 3)]
    if shape == "base":
        return [value + "-" + PICK]
    if shape == "prefix":
        return [value + PICK, value + PICK + "/" + PICK]
    if shape == "idx" and value.endswith(".idx"):
        return [value, value[: -len("idx")] + "rev"]
    return [value]


def placed_paths(shown, value, shape, where, base, attached):
    """(shown, path, top) for each path a value of this shape and base names: a "cwd" path joined onto the -C base,
    top None; a "top" path left as it is, with the -C base ("" for none) that top_readings reads it from.  A value
    attached to its option keeps a leading `~` as spelled, since no shell expands one inside a word, and git reads it
    relative like any other (probed: `-o~/t.tar` and `--output=~/u.tar` wrote ./~/t.tar and ./~/u.tar)."""
    if attached and value.startswith("~"):
        value = "./" + value
    paths = shaped_paths(value, shape)
    if where == "top":
        return [(shown, p, base or "") for p in paths]
    return [(shown, chdir_join(base, p), None) for p in paths]


def mailsplit_directory(verb, args):
    """(shown, the directory) where `git mailsplit` writes in its older form, with no -o: its last word, of one or two
    (SPD-228).  Read as mailsplit's own parser reads its words (git 2.54.0, probed): options end at the first word not
    starting with `-`, or after `--`, and an -o among them (only ever attached, `-o<dir>`: a spaced -o died "unknown
    option") makes every later word a mailbox; with none, one word is the directory and the mailbox comes on standard
    input, two are the mailbox and the directory, and any other count prints usage.  An -o after the first word is a
    word like any other (`mailsplit box -oo4` wrote into ./-oo4), which the option scan still reads as -o too."""
    if verb != "mailsplit":
        return []
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 1
        if args[i - 1] == "--":
            break
        if args[i - 1].startswith("-o") and len(args[i - 1]) > 2:
            return []
    words = args[i:]
    return [("mailsplit %s" % " ".join(words), words[-1])] if len(words) in (1, 2) else []


def work_tree_top(directory):
    """The top of the work tree git discovers from `directory`: the nearest directory, itself or an ancestor, holding a
    .git (a directory, or a gitfile), read in its physical spelling, as git's own getcwd reads it; None where there is
    none (git then runs in no repository, and a verb that needs one writes nothing)."""
    d = os.path.realpath(directory)
    while True:
        if os.path.lexists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def top_readings(path, top, work_trees, cwds):
    """Every path a value git reads from the top of the work tree may land at (syntax.GIT_FILE_FORMS' "top"): in the
    directory git runs in -- relative to the -C base `top`, as the line reads any path -- and, from each directory the
    shell may be in, at the top of the work tree git discovers from there, and at each --work-tree or GIT_WORK_TREE the
    line names (`work_trees`), since which of them git takes turns on its discovery and on config the hook does not read.
    A path that is anchored, or that the hook cannot resolve, and every path when the hook cannot follow the directory
    the shell is in, is read as the one path it spells, whose own refusal follows."""
    here = chdir_join(top or None, path)
    if anchored(path) or spud_calls.unresolvable_word(path) or spud_calls.unresolvable_word(top) or cwds is None:
        return [here]
    out = [here]
    for cwd in sorted(cwds):
        found = work_tree_top(os.path.join(cwd, os.path.expanduser(top)))
        if found is not None:
            out.append(os.path.join(found, path))
    out += [os.path.join(w, path) for w in work_trees if not spud_calls.unresolvable_word(w)]
    return list(dict.fromkeys(out))


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
    longs, shorts = git_file_options(verb)
    k = 0
    while k < len(args):
        w = args[k]
        if w == "--":
            return None
        if input_option(w):
            return w
        k += 2 if (file_option_spelling(w, longs, shorts) or (None, False))[1] else 1
    return None


def input_option(word):
    """True when a word holds what xargs reads from its input where git may read it as an option: nothing but a `-`
    run with no `=` stands before it (`{input}`, `-{input}`, `--{input}`, `-o{input}`)."""
    if syntax.INPUT_OPERAND not in word:
        return False
    head = prepare.deglob(word[: word.index(syntax.INPUT_OPERAND)])
    return not head or (head.startswith("-") and "=" not in head)


def git_file_options(verb):
    """(long options, short-option letters) that name a path git writes under `verb`: its entry in
    syntax.GIT_VERB_FILE_OPTIONS, and syntax.GIT_FILE_OPTIONS, which every verb is read for."""
    longs, shorts = syntax.GIT_VERB_FILE_OPTIONS.get(verb, ((), ""))
    return tuple(longs) + syntax.GIT_FILE_OPTIONS, shorts


def file_option_spelling(word, longs, shorts):
    """How a word as git gets it spells one of these options: None when it spells none, else (the value it carries
    attached, whether its value is the next word instead).  Any `--`-prefix of a long option counts, git's parse-options
    resolving an unambiguous one, and a short cluster holding one of the letters takes the rest of the cluster or the
    next word (`-so D`)."""
    key, sep, attached = word.partition("=")
    if key.startswith("--") and len(key) >= 3 and any(opt.startswith(key) for opt in longs):
        return (attached, False) if sep else (None, True)
    if shorts and word.startswith("-") and not word.startswith("--") and len(word) > 1:
        k = next((j for j in range(1, len(word)) if word[j] in shorts), None)
        if k is not None:
            rest = word[k + 1 :]
            return (rest, False) if rest else (None, True)
    return None


def literal_head(word):
    """The text a masked word starts with before anything the shell expands in it, or xargs puts there: up to its first
    glob character, parameter expansion, substitution or syntax.INPUT_OPERAND (SPD-230)."""
    ends = [len(word)]
    for m in (syntax.GLOB_RE.search(word), syntax._EXPANDING_DOLLAR_RE.search(word)):
        if m:
            ends.append(m.start())
    for mark in (hookio.SUBST, syntax.INPUT_OPERAND):
        if mark in word:
            ends.append(word.index(mark))
    return prepare.deglob(word[: min(ends)])


def may_become_file_option(word, longs, shorts):
    """True when a word the shell expands may reach git as one of these options, whatever its expansion leaves git to
    read: its literal head is empty or `-` alone, or `--` and a prefix of a long option with no `=` yet (git takes any
    unambiguous prefix, and a glob's `?` or `*` can itself become the `=`: `--outp?t=y` matched a file `--outp=t=y`,
    probed), or a single `-` when the verb has a short letter any later character may be.  A head that has settled its
    option already (`--grep=`, `-S`, `--oneline`) is read as spelled."""
    head = literal_head(word)
    if head in ("", "-"):
        return True
    if head.startswith("--"):
        return "=" not in head and any(opt.startswith(head) for opt in longs)
    return head.startswith("-") and bool(shorts)


def git_write_positional_targets(verb, args):
    """The positional words of a verb whose writing form names its file that way: `git bundle create <file>`,
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


def git_write_env_targets(variables, base=None):
    """Every file a git call writes because of a variable in force on the line, as git_write_option_targets gives
    them: a GIT_TRACE* sibling whose value is a path git appends to -- an absolute one, or a `~` the shell expanded before
    git saw it, a descriptor, an off value and a relative one writing nothing -- or whose value the hook cannot read,
    which fails closed, and GIT_INDEX_FILE and GIT_OBJECT_DIRECTORY, whose value is a path whatever its shape, read in the
    shape and from the base syntax.GIT_WRITE_PATH_ENV_VARS gives it, from the -C `base`.  `variables` holds each value as
    the shell passes it to git, a `$NAME` the line settled resolved, so the shape that decides here is the one git sees.
    A fixed order so the reason is deterministic."""
    out = []
    for name in sorted(variables):
        value = variables[name]
        if not value:
            continue
        if name in syntax.GIT_WRITE_PATH_ENV_VARS:
            shape, where = syntax.GIT_WRITE_PATH_ENV_VARS[name]
            out += placed_paths("%s=%s" % (name, value), value, shape, where, base, False)
        elif name.startswith(syntax.GIT_TRACE_VAR_PREFIX) and (value.startswith(("/", "~")) or spud_calls.unresolvable_word(value)):
            out.append(("%s=%s" % (name, value), value, None))
    return out


def git_read_options(verb):
    """git_file_options, and the suffix options of the verb's syntax.GIT_VERB_CWD_WRITES entry (format-patch's --suffix,
    bugreport's and diagnose's -s): the options a glob or an expansion is read as it may become
    (expansions.git_read_point), since a suffix's value can move a file git writes as far as a path option's can."""
    longs, shorts = git_file_options(verb)
    entry = syntax.GIT_VERB_CWD_WRITES.get(verb)
    if entry is None:
        return longs, shorts
    suffixes = [(long, short) for long, short, kind in entry[1] if kind == "suffix"]
    return (longs + tuple(long for long, _ in suffixes if long not in longs),
            shorts + "".join(short for _, short in suffixes if short not in shorts))


def cwd_own_options(word, options, abbreviates):
    """(the options of a syntax.GIT_VERB_CWD_WRITES entry one word spells, in order, each (option, the value it carries
    attached or None, whether it is the `--no-` form), what of the word git leaves to its later parse or None), as git's
    parse-options reads the word.  A long option exact, or, where the verb abbreviates, the one option an unambiguous
    `--` prefix names (an ambiguous one git refuses); a short cluster letter by letter, until a letter that takes a
    value -- which takes the rest of the cluster, or the next word when nothing is left -- or one the verb does not have,
    where parse-options leaves the rest of the cluster to the later parse."""
    if word.startswith("--"):
        key, sep, attached = word.partition("=")
        name, value, found = key[2:], (attached if sep else None), []
        for option in options:
            for spelled, negated in ((option[0][2:], False), ("no-" + option[0][2:], True)):
                if spelled == name:
                    return [(option, value, negated)], None
                if abbreviates and name and spelled.startswith(name):
                    found.append((option, value, negated))
        return (found, None) if len(found) == 1 else ([], word)
    if not word.startswith("-") or len(word) < 2:
        return [], word
    out = []
    for k in range(1, len(word)):
        option = next((o for o in options if o[1] == word[k]), None)
        if option is None:
            return out, "-" + word[k:]
        if option[2] not in ("flag", "stdout"):
            return out + [(option, word[k + 1 :] or None, False)], None
        out.append((option, None, False))
    return out, None


def cwd_unread_word(word):
    """True when the shell may turn a masked word into an option the hook does not see -- a reset, or one that takes the
    next word as its value: a glob or an expansion whose literal head is empty, or starts with `-` and has not reached
    the `=` that settles which option it is (`--subject-prefix=$P` has)."""
    head = literal_head(word)
    return head != prepare.deglob(word) and (not head or (head.startswith("-") and "=" not in head))


def cwd_later_takes_value(word):
    """Whether a word format-patch leaves to its revision and diff options may take the next word as its value: any
    option but a count (`-3`), a `--no-` form or one that carries its value after `=`.  Which of those options take one
    is not read, so any other may, fail closed."""
    if not word.startswith("-") or word == "-":
        return False
    if word.startswith("--"):
        return "=" not in word and not word.startswith("--no-")
    return not word[1:].isdigit()


def cwd_write_scan(args, entry):
    """What the arguments of a verb of syntax.GIT_VERB_CWD_WRITES leave git to write, or None when it writes nothing
    (`-h` or `--help` no option takes as its value): (whether the files go to standard output or to one file, the
    directory a "dir" option names -- "" for the current one -- or None when none does, every suffix the line gives,
    whether a word the hook cannot read stands before `--`).  The verb's own options first, as parse-options reads
    them, a "value" option taking the next word whatever it is; then the words that parse leaves, in order, where an
    option of the later parse sends the files to one file unless the word before it may take it as its value."""
    abbreviates, options, later = entry[0], entry[1], entry[2]
    stdout, directory, suffixes, unread, kept = False, None, [], False, []
    i = 0
    while i < len(args):
        w = args[i]
        i += 1
        if w == "--":
            break
        if w in ("-h", "--help") and not unread:
            return None  # usage, or the manual: nothing written, wherever it stood (exit 129, probed)
        unread = unread or cwd_unread_word(w)
        spelled, rest = cwd_own_options(w, options, abbreviates)
        if rest is not None:
            kept.append(rest)
        for (_long, _short, kind), value, negated in spelled:
            if negated:  # `--no-stdout`, bugreport's `--no-output-directory`: back to the current directory
                stdout = stdout and kind != "stdout"
                directory = None if kind == "dir" else directory
                continue
            attached = value is not None
            if value is None and kind not in ("flag", "stdout"):
                value = args[i] if i < len(args) else None
                i += 1
                unread = unread or (kind == "value" and value is not None and cwd_unread_word(value))
            if kind == "stdout":
                stdout = True
            elif kind == "dir" and value is not None:
                directory = "./" + value if attached and value.startswith("~") else value  # as placed_paths reads one
            elif kind == "suffix" and value is not None:
                suffixes.append(value)
    sent, taker, k = stdout, False, 0
    while k < len(kept):
        w = kept[k]
        k += 1
        key, sep, attached = w.partition("=")
        if key in later and not taker:
            value = attached if sep else (kept[k] if k < len(kept) else None)
            k += 0 if sep else 1
            sent, taker = sent or bool(value), False
            continue
        taker = cwd_later_takes_value(w)
    return sent, directory, suffixes, unread


def git_chdir_and_config(words, keys):
    """(the directory the composed -C chain names or None, {key: the value `-c` or `--config-env` gives it last} for the
    config keys of `keys`, compared case-folded), read from git's global options.  A `--config-env` value lives in a
    variable the hook cannot read, so it comes back as that variable's `$NAME`, which the path rule refuses as
    unresolvable."""
    base, values, i = None, {}, 1
    while i < len(words):
        w = words[i]
        if w == "-C":
            named = words[i + 1] if i + 1 < len(words) else None
            if named:  # `-C ''` leaves the directory as it is
                base = chdir_join(base, named)
            i += 2
            continue
        operand = None
        if w in ("-c", "--config-env"):
            operand, i = (words[i + 1] if i + 1 < len(words) else ""), i + 2
        elif w.startswith("--config-env="):
            operand, i = w[len("--config-env=") :], i + 1
        if operand is not None:
            key, _, given = operand.partition("=")
            if key.strip().casefold() in keys:
                values[key.strip().casefold()] = given if w == "-c" else "$" + given
            continue
        if w in syntax.GIT_GLOBAL_VALUE_FLAGS:
            i += 2
            continue
        if w.startswith("-"):
            i += 1
            continue
        break
    return base, values


def strftime_shape(value):
    """A strftime format as the name git makes of it: its literal text as it is, `%%` a `%`, a conversion
    syntax.STRFTIME_FLAT names a run git picks, and every other conversion a run two slashes deep (%D's mm/dd/yy), so a
    name git writes through directories is read that deep."""
    pick, out, i = pathrule.NAME_CHAR + pathrule.NAME_MORE, [], 0
    while i < len(value):
        if value[i] != "%" or i + 1 == len(value):
            out.append(value[i])
            i += 1
            continue
        c = value[i + 1]
        out.append("%" if c == "%" else pick if c in syntax.STRFTIME_FLAT else "/".join((pick,) * 3))
        i += 2
    return "".join(out)


def cwd_write_target(what, base, place, name):
    """(the spelling a reason names a default-form write by, the path word): `name` in the directory `place`, relative
    to -C's; an anchored directory drops -C's, as git's does (chdir_join)."""
    where = chdir_join(base, place) if place else base
    path = os.path.join(where, name) if where else name
    shown = path if os.path.isabs(path) or path.startswith(("./", "~")) else "./" + path
    return "%s, into %s" % (what, shown), path


def git_cwd_write_targets(words):
    """What a verb of syntax.GIT_VERB_CWD_WRITES writes with no word the line spells naming it, as (the spelling a reason
    names it by, the path): a name git picks directly in the directory it writes into when the line sends the files
    nowhere else -- the shell's, -C's, format.outputDirectory's when `-c` sets it -- and, for each suffix the line gives
    (the option, or format.suffix), the name it shapes there and in -o's directory, which may lie in another directory.
    Nothing when `-h` or `--help` prints usage, or when --stdout or --output takes every file, or -o does and no suffix
    shapes a name (git_write_option_targets reads -o's and --output's own path)."""
    verb, args = git_verb(words)
    entry = syntax.GIT_VERB_CWD_WRITES.get(verb)
    scan = None if entry is None else cwd_write_scan(args, entry)
    if scan is None:
        return []
    sent, directory, suffixes, unread = scan
    config, names, strftime = entry[3], entry[4], entry[5]
    base, configured = git_chdir_and_config(words, config or ())
    default = configured.get(config[0]) if config else None  # format.outputDirectory: where no -o is given
    if default is not None and default.startswith("~"):
        default = "./" + default  # a config value, which no shell expanded
    if config and configured.get(config[1]) is not None:
        suffixes = [configured[config[1]]] + suffixes  # format.suffix, which --suffix overrides
    if unread:  # a word the hook cannot read may be a reset or take --stdout as its value
        here = [""] + ([default] if default is not None else [])
    elif sent:
        return []
    elif directory is None:
        here = [default if default is not None else ""]
    else:
        here = [""] if directory == "" else []
    pick = pathrule.NAME_CHAR + pathrule.NAME_MORE
    out = [cwd_write_target("%s's default form" % verb, base, place, pick) for place in here]
    for place in here + ([directory] if directory else []):
        for value in suffixes:
            shaped = strftime_shape(value) if strftime else value
            for name in names:
                out.append(cwd_write_target("%s's suffix %s" % (verb, value), base, place, name.format(pick=pick, value=shaped)))
    return list(dict.fromkeys(out))


def git_write_targets(words, variables):
    """Every file a git call writes beside the repository it reads, as (the spelling a reason names it by, the path, None
    or the -C base top_readings reads a work-tree-top path from): what its options and positional words name, the file a
    default form writes into the directory git runs in, then what the environment in force names.  Each is checked with
    the path rule in bash_reason, like a redirection target."""
    base, _ = git_chdir_and_config(words, ())
    cwd_writes = [(shown, path, None) for shown, path in git_cwd_write_targets(words)]
    return list(dict.fromkeys(git_write_option_targets(words) + cwd_writes + git_write_env_targets(variables, base)))


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
