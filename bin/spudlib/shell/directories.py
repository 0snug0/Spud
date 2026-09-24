"""shell/directories: Redirects, wrappers, prefixes, and the directories the shell may be in."""

import os
import re

from . import arg_writes, globbing, prepare, syntax
from ..hooks import hookio


# The most directories the shell may be in that the hook tracks before it gives up and reads the rest with the
# directory unknown (SPD-193): a line of relative conditional `cd`s doubles the set with each step (`cd a && ...; cd b
# && ...` may leave the shell in the original directory, a/, b/, a/b/, ...), so without a bound the reading is 2^n; no
# real line puts the shell in this many places, and past it a write target relative to the directory is refused (the
# path rule's unfollowable reading), which is the fail-closed answer a member spells around with an absolute path.
CWDS_CAP = 256


def union_dirs(a, b):
    """The directories the shell may be in when it may be in either set; None (not known) absorbs everything, and a
    union past CWDS_CAP becomes None too, so a line of many conditional relative cds is read in bounded time (SPD-193)."""
    if a is None or b is None:
        return None
    both = a | b
    return both if len(both) <= CWDS_CAP else None


def redirect_descriptor(operator, operand):
    """Whether an output operator's operand is a descriptor or a close rather than the name of a file it opens.
    Only `>&` reads a word of digits or `-` that way: probed in zsh 5.9 -f -o nobareglobqual (this Mac's Bash tool) and
    bash 3.2 in an empty directory, `echo x > 3`, `>3`, `>> 3`, `>| 3`, `&> 3`, `1> 4`, `9> 9`, `2> 12` and `2>12` each
    created a file named by the digits in both shells and `&>> 3` in zsh (a syntax error in bash 3.2), and `> -`, `>> -`
    and `&> -` created a file named `-`, while `>& 3`, `>&3` and `1>&3` were a descriptor ("bad file descriptor" in both)
    and `>& -`, `>&-` a close; `>& out` wrote the file.  An operand the tokenizer leaves with a leading `&` is a
    descriptor after every operator but `<>`, which has no dup form (`1<>&2` is a syntax error in both)."""
    if operand.startswith("&"):
        return operator != "<>"
    return operator == ">&" and re.fullmatch(r"-|\d+", operand) is not None


def separate_redirects(tokens):
    """Drop redirection operators and their operands; return (words, output targets).  A `<>` operand is always a file name
    (probed: `<>3` and `<>-` created files named 3 and -, and `1<>&2` is a syntax error), never a descriptor."""
    words, targets = [], []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in syntax.OUT_REDIRECTS:
            operand = tokens[i + 1] if i + 1 < len(tokens) else None
            if operand is not None and not redirect_descriptor(t, operand):
                targets.append(operand)
            i += 2
            continue
        if t in syntax.IN_REDIRECTS:
            i += 2
            continue
        if re.fullmatch(r"\d+", t) and i + 1 < len(tokens) and tokens[i + 1] in (syntax.OUT_REDIRECTS | syntax.IN_REDIRECTS):
            i += 1
            continue
        words.append(t)
        i += 1
    return words, targets


def strip_wrapper(words):
    """`env`, `nohup`, `xargs`, `timeout 10`, `sudo -u x`, ...: drop the wrapper, its options and their values; return (the
    words it runs, the command strings it hands a shell, the index in `words` of the first word it did not consume, the
    (name, value) assignments `env` sets in the command's environment, the directory it runs the command in or None).  The
    name is matched case-folded (ENV runs /usr/bin/env on macOS); `env -S` splits its string into the words it runs
    (no shell: none of them is expanded), GNU `script -c` hands its string to a shell; `env NAME=value` puts NAME in the
    environment of the command it runs.  The directory is (the value of the last `env -C` or `sudo -D`,
    whether the shell gave it as a word of its own), since only the last one counts (probed: `env -C /usr -C bin pwd`
    printed /bin); a value glued to its option or split out of `env -S` is one whose leading `~` no shell expanded."""
    name = os.path.basename(words[0]).casefold()
    values = syntax.WRAPPER_VALUE_OPTIONS.get(name, set())
    chdirs = syntax.WRAPPER_CHDIR_OPTIONS.get(name, ())
    rest, strings, assignments = words[1:], [], []  # `assignments`: the (name, value) pairs `env` sets in the command's environment
    chdir = None
    originals = len(rest)  # the words of `words` still in rest, at its end (env -S puts its words before them)

    def take(opt, value, own_word):
        nonlocal rest, chdir
        if opt in chdirs:
            chdir = (value, own_word)
        elif name == "env" and opt in ("-S", "--split-string"):
            rest = [globbing.literalize(t) for t in (syntax.shell_tokens(value) or [])] + rest
        elif name == "script" and opt in ("-c", "--command"):
            strings.append(value)

    while rest:
        w = rest[0]
        if w == "--":
            rest = rest[1:]
            break
        own_word = len(rest) - 1 <= originals  # rest[1], when an option takes it, is a word the shell gave the wrapper
        if w.startswith("--"):
            opt, eq, value = w.partition("=")
            if opt in values and not eq:
                value = rest[1] if len(rest) > 1 else ""
                rest = rest[2:]
            else:
                rest, own_word = rest[1:], False
            originals = min(originals, len(rest))
            take(opt, value, own_word)
            continue
        if w.startswith("-") and len(w) > 1:
            consumed, opt, value = 1, None, None
            for k in range(1, len(w)):  # a cluster such as -Eu root: the first letter that takes a value ends it
                if "-" + w[k] in values:
                    opt = "-" + w[k]
                    if k + 1 < len(w):
                        value = w[k + 1 :]
                    else:
                        value = rest[1] if len(rest) > 1 else ""
                        consumed = 2
                    break
            rest = rest[consumed:]
            originals = min(originals, len(rest))
            if opt:
                take(opt, value, own_word and consumed == 2)
            continue
        break
    if name == "env":
        # env puts every operand holding `=` past its first character in the environment, whatever the name
        # (probed: `a b=c`, `x[1]=y`, `a%b=c` and `BASH_FUNC_foo%%=() { ...; }` each reached the program; `=x` is an
        # error), so none of them is the command it runs
        while rest and "=" in rest[0][1:]:
            aname, _, avalue = rest[0].partition("=")
            assignments.append((aname, avalue))  # env's environment reaches the command it runs
            rest = rest[1:]
    elif name == "timeout" and rest and syntax.DURATION_RE.fullmatch(rest[0]):
        rest = rest[1:]
    elif name == "script" and rest:
        rest = rest[1:]  # the typescript file; what follows it is the command
    return rest, strings, len(words) - min(originals, len(rest)), assignments, chdir


# What a glob leaves in a word once analyse_words has read it as spelled (globbing.literalize): its glob characters
# quoted, a leading `=` marked, and zsh's pattern characters plain -- which no word the shell gives unquoted holds.
_READ_GLOB_RE = re.compile("[" + re.escape("".join(syntax._GLOB_UNSENTINEL) + syntax._LITERAL_EQUALS + "(|)<>") + "]")
# A filename glob the shell expands against its own directory (syntax.GLOB_RE without the brace list, which names
# the same words wherever the shell is)
_FILENAME_GLOB_RE = re.compile("[*?\\[" + syntax.ZSH_OPEN + syntax.ZSH_RANGE_OPEN + "]")


def wrapped_directories(chdir, command, a):
    """The directories the command `env -C <dir>` or `sudo -D <dir>` runs may run in, or None when the hook cannot
    know.  `chdir` is strip_wrapper's (value, whether the shell gave it as a word of its own); `command`, the words the
    wrapper runs.  Probed by Spud on this Mac's env (a member in a worktree may not run a shell): an absolute, a
    relative, a glued and a clustered value, a `~` the shell expands, and a string a shell runs all start there; a nested
    env moves from where the outer one left it.  The program calls chdir(2) itself, so there is no CDPATH and no
    directory stack; a symlink resolves as the kernel resolves it, before a `..` after it; and a relative value is read
    once against every directory the shell may be in, never compounding as a cd in a loop does, since the shell's own
    directory does not move.

    Unknown: a value holding an expansion the analysis left in it, or a glob, which has been read as spelled by the time it
    gets here, so a quoted glob character reads as one (_READ_GLOB_RE); `~-`, `~name`, and a leading `~` no shell expanded
    (env would enter a directory named `~`; what sudo makes of one is not probed).  Unknown too when a word of the command
    is a filename glob or `~+`: the shell expands those in its own directory and the command opens what they name in the
    one it moved to, which no single set of directories reads.  An empty value leaves the directories as they are: chdir(2)
    fails on it, and env runs nothing after a directory it cannot enter (probed: exit 125)."""
    value, own_word = chdir
    value = settled(value, a)  # a value the line settled, read as a cd target's is (SPD-147)
    if any(w.startswith("~+") or _FILENAME_GLOB_RE.search(w) for w in command[1:]):
        return None
    if value == "":
        return a.cwds
    if "$" in value or "`" in value or hookio.SUBST in value or syntax.GLOB_RE.search(value) or _READ_GLOB_RE.search(value):
        return None
    if syntax.unknown_operand(value):  # unspelled: `find . -exec env -C {} ...`, `xargs -I% env -C % ...`
        return None
    value = prepare.deglob(value)
    if value.startswith("~"):
        head, _, tail = value.partition("/")
        if head == "~" and own_word:
            paths = [os.path.expanduser(value)]
        elif head == "~+" and own_word and a.cwds is not None:
            paths = [os.path.join(c, tail) for c in sorted(a.cwds)]
        else:
            return None
    elif os.path.isabs(value):
        paths = [value]
    elif a.cwds is None:
        return None
    else:
        paths = [os.path.join(c, value) for c in sorted(a.cwds)]
    return frozenset(os.path.realpath(p) for p in paths)


def prefix_effect(word, following):
    """Whether a builtin behind this prefix still runs in the shell that reads the line (probed in zsh 5.9 and bash
    3.2): "shell" for `builtin` and the `time` reserved word; "either" where one shell runs the builtin and the other an
    external or nothing (`command` is the builtin in bash and the external in zsh, noglob and nocorrect are zsh's, zsh
    takes the option in `time -p` for the command); "process" for anything else, spelled in any other case or with a path
    included: an external program, or a name no shell finds (BUILTIN)."""
    if word == "builtin":
        return "shell"
    if word == "time":
        return "either" if following and following.startswith("-") else "shell"
    if word in ("command", "noglob", "nocorrect"):
        return "either"
    return "process"


# Where a builtin behind the prefixes runs: in the shell that reads the line ("shell"), in one of the two shells
# ("either"), in a forked shell of its own ("fork": zsh's coproc, whose exit fires an EXIT trap set there -- probed in zsh
# 5.9 -f and zsh -f -o nobareglobqual, `coproc { trap 'git push' EXIT; }` pushed), or not at all ("process": an
# external wrapper execs a program and finds no builtin).  A builtin runs for the first three and here for the first two.
EFFECT_ORDER = {"shell": 0, "either": 1, "fork": 2, "process": 3}


def builtin_runs(effect):
    """A builtin behind the prefixes runs somewhere, so what it stores runs too."""
    return effect != "process"


def builtin_runs_here(effect):
    """... and in the shell that reads the line, so a directory it changes is the line's own."""
    return effect in ("shell", "either")


def settle(effect, before, after):
    """The directories after a command that changed them from `before` to `after` when it runs in the shell, run with `effect`.
    A forked shell's directory never comes back, so "fork" settles as an external program's does."""
    if effect == "shell":
        return after
    if effect == "either":
        return union_dirs(before, after)
    return before


def cdpath_entries(a):
    """CDPATH's entries as the shell reads them: assigned in the line (CDPATH, or zsh's cdpath array), else inherited from
    the environment the hook runs in; None when a value holds something the hook cannot read."""
    raw = []
    if "CDPATH" in a.vars:
        raw += a.vars["CDPATH"].replace(syntax._ARRAY_VALUE, "").split(":")
    if "cdpath" in a.vars:
        raw += a.vars["cdpath"].replace(syntax._ARRAY_VALUE, "").split()
    if "CDPATH" not in a.vars and "cdpath" not in a.vars and os.environ.get("CDPATH"):
        raw = os.environ["CDPATH"].split(":")
    if any("$" in e or "`" in e or hookio.SUBST in e for e in raw):
        return None
    return [prepare.deglob(e) for e in raw]


def settled(word, a):
    """The word with each variable the line settled put in its place, as SPD-127 reads a write target (arg_writes.resolved),
    so a directory a write is relative to holds the same reading as the write itself (SPD-147): after `S=<dir>; cd
    "$S/x"` the shell is in <dir>/x.  A value the line cannot settle stays spelled, and the `$` left in the word keeps the
    directory unknown.  So does a value that puts a `~` at the word's start: no shell expands a tilde an expansion
    produced, so `S='~'; cd "$S"` enters a directory named `~`, which the word as resolved would read as the home."""
    value = arg_writes.resolved(word, a)
    if value.startswith("~") and not word.startswith("~"):
        return word
    return value


def cd_target(word, a, physical=False):
    """The directories one cd argument may lead to from the directories in force, or None when the hook cannot know:
    `-` and `~-` (OLDPWD), a stack entry (+N, -N, ~N), `~name` (a user, or a zsh named directory), a variable the line did
    not settle (a settled one is in place by now: settled), a glob or a brace expansion, a CDPATH it cannot read, a
    relative target in a loop or a function body.  A bare relative target
    may also land under a CDPATH entry (bash tries those first, zsh after the current directory)."""
    if word == "":
        return a.cwds  # both shells stay
    if word == "-" or "$" in word or "`" in word or hookio.SUBST in word or syntax.GLOB_RE.search(word) or re.fullmatch(r"[+-]\d+", word):
        return None
    if syntax.unknown_operand(word):  # a path find hands its command, or what xargs reads from its input
        return None
    word = prepare.deglob(word)  # a quoted or escaped metacharacter (the GLOB_RE above sees only unquoted ones) is a literal path char
    if word.startswith("~"):
        head, _, tail = word.partition("/")
        if head == "~":
            paths = [os.path.expanduser(word)]
        elif head == "~+" and a.cwds is not None and not (tail and a.loop_depth):
            paths = [os.path.join(c, tail) for c in sorted(a.cwds)]
        else:
            return None
    elif os.path.isabs(word):
        paths = [word]
    else:
        if a.cwds is None or a.loop_depth:
            return None
        bases = sorted(a.cwds)
        if not (word in (".", "..") or word.startswith(("./", "../"))):
            entries = cdpath_entries(a)
            if entries is None:
                return None
            for e in entries:
                e = os.path.expanduser(e) if e.startswith("~") else e
                bases += [e] if os.path.isabs(e) else [os.path.join(c, e) for c in sorted(a.cwds)]
        paths = [os.path.join(b, word) for b in bases]
    if physical == "either":  # CHASE_LINKS may or may not be on (ShellAnalysis.chase): both readings
        return frozenset(os.path.normpath(p) for p in paths) | frozenset(os.path.realpath(p) for p in paths)
    resolve = os.path.realpath if physical else os.path.normpath
    return frozenset(resolve(p) for p in paths)


def cd_destinations(name, args, a):
    """Where cd, chdir, pushd or popd with these arguments may leave the shell; None when the hook cannot follow.  Options:
    -L and -P (and --) are read alike by zsh and bash for cd, -P resolving symlinks first; every other option (-q, -s, -e,
    -@, -N) is refused as unfollowable, since zsh reads an option it does not know as the first string of `cd old new`
    and bash 3.2 rejects it, and pushd takes none but -- in both.  popd, and pushd with no directory, go where the stack
    says.  Two arguments: zsh replaces the first occurrence of the first in the current directory with the second, bash 3.2
    changes to the first, a later bash stays.  With neither -L nor -P the shell's CHASE_LINKS (bash's physical) decides,
    as ShellAnalysis.chase holds it (SPD-263): zsh's -L keeps the path as spelled whatever it says."""
    if name == "popd":
        return None
    args = [settled(w, a) for w in args]  # a value the line settled is the word the builtin gets, option or target (SPD-147)
    physical = a.chase
    while args and args[0].startswith("-") and args[0] != "-":
        if args[0] == "--":
            args = args[1:]
            break
        if name != "pushd" and re.fullmatch(r"-[LP]+", args[0]):
            physical = args[0].endswith("P")
            args = args[1:]
            continue
        return None
    if not args:
        return None if name == "pushd" else frozenset([os.path.expanduser("~")])
    if len(args) == 1:
        return cd_target(args[0], a, physical)
    if len(args) == 2 and a.cwds is not None:
        old, new = args
        first = cd_target(old, a, physical)
        if first is None or not old or "$" in new or "`" in new or hookio.SUBST in new:
            return None
        old, new = prepare.deglob(old), prepare.deglob(new)  # the replacement is on literal path text
        substituted = {os.path.normpath(c.replace(old, new, 1)) for c in a.cwds if old in c}
        return first | a.cwds | frozenset(substituted)
    return None


def directory_change(words, a, effect):
    """Apply cd, chdir, pushd or popd, spelled exactly (the shell's builtins), to the directories the shell may be in."""
    if words[0] == "chdir":
        effect = max(effect, "either", key=EFFECT_ORDER.get)  # zsh's synonym for cd; bash has no chdir
    new = cd_destinations(words[0], words[1:], a)
    # A directory that exists but the process cannot enter (no execute bit, /var/root, one a member chmod 000's)
    # fails the cd exactly as a missing one does -- probed in zsh 5.9 and bash 3.2: "permission denied", and PWD stays
    # put -- so the hook keeps the old directory beside the new one rather than assume the cd ran.
    if new is not None and not all(os.path.isdir(d) and os.access(d, os.X_OK) for d in new):
        a.cd_uncertain = True
        if a.cdable and any(not prepare.deglob(w).startswith(("/", "~")) for w in words[1:] if not w.startswith("-")):
            # CDABLE_VARS (cdable_vars) may be on: a relative name that is no directory is a variable's, or a named
            # directory's, whose value the hook does not read here (probed: `cd dest` went to $dest in zsh 5.9 and bash 3.2)
            new = None
    a.cwds = settle(effect, a.cwds, new)
    a.dir_moves += 1
