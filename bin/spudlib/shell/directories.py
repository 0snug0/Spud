"""shell/directories: Redirects, wrappers, prefixes, and the directories the shell may be in.  Moved from bin/spud_ledger.py (SPD-065)."""

import os
import re

from . import globbing, prepare, syntax
from ..hooks import hookio


def union_dirs(a, b):
    """The directories the shell may be in when it may be in either set; None (not known) absorbs everything."""
    if a is None or b is None:
        return None
    return a | b


def redirect_descriptor(operator, operand):
    """Whether an output operator's operand is a descriptor or a close rather than the name of a file it opens (SPD-045).
    Only `>&` reads a word of digits or `-` that way: probed in zsh 5.9 -f -o nobareglobqual (this Mac's Bash tool) and
    bash 3.2 in an empty directory, `echo x > 3`, `>3`, `>> 3`, `>| 3`, `&> 3`, `1> 4`, `9> 9`, `2> 12` and `2>12` each
    created a file named by the digits in both shells and `&>> 3` in zsh (a syntax error in bash 3.2), and `> -`, `>> -`
    and `&> -` created a file named `-`, while `>& 3`, `>&3` and `1>&3` were a descriptor ("bad file descriptor" in both)
    and `>& -`, `>&-` a close; `>& out` wrote the file.  An operand the tokenizer leaves with a leading `&` is a
    descriptor after every operator but `<>`, which has no dup form (`1<>&2` is a syntax error in both, SPD-040)."""
    if operand.startswith("&"):
        return operator != "<>"
    return operator == ">&" and re.fullmatch(r"-|\d+", operand) is not None


def separate_redirects(tokens):
    """Drop redirection operators and their operands; return (words, output targets).  A `<>` operand is always a file name
    (SPD-040, probed: `<>3` and `<>-` created files named 3 and -, and `1<>&2` is a syntax error), never a descriptor."""
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
    (name, value) assignments `env` sets in the command's environment).  The name is matched case-folded (SPD-030: ENV runs
    /usr/bin/env on macOS); `env -S` splits its string into the words it runs (no shell: none of them is expanded), GNU
    `script -c` hands its string to a shell; `env NAME=value` puts NAME in the environment of the command it runs (SPD-044)."""
    name = os.path.basename(words[0]).casefold()
    values = syntax.WRAPPER_VALUE_OPTIONS.get(name, set())
    rest, strings, assignments = words[1:], [], []  # `assignments`: the (name, value) pairs `env` sets in the command's environment
    originals = len(rest)  # the words of `words` still in rest, at its end (env -S puts its words before them)

    def take(opt, value):
        nonlocal rest
        if name == "env" and opt in ("-S", "--split-string"):
            rest = [globbing.literalize(t) for t in (syntax.shell_tokens(value) or [])] + rest
        elif name == "script" and opt in ("-c", "--command"):
            strings.append(value)

    while rest:
        w = rest[0]
        if w == "--":
            rest = rest[1:]
            break
        if w.startswith("--"):
            opt, eq, value = w.partition("=")
            if opt in values and not eq:
                value = rest[1] if len(rest) > 1 else ""
                rest = rest[2:]
            else:
                rest = rest[1:]
            originals = min(originals, len(rest))
            take(opt, value)
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
                take(opt, value)
            continue
        break
    if name == "env":
        # env puts every operand holding `=` past its first character in the environment, whatever the name (SPD-106,
        # probed: `a b=c`, `x[1]=y`, `a%b=c` and `BASH_FUNC_foo%%=() { ...; }` each reached the program; `=x` is an
        # error), so none of them is the command it runs
        while rest and "=" in rest[0][1:]:
            aname, _, avalue = rest[0].partition("=")
            assignments.append((aname, avalue))  # env's environment reaches the command it runs (SPD-044)
            rest = rest[1:]
    elif name == "timeout" and rest and syntax.DURATION_RE.fullmatch(rest[0]):
        rest = rest[1:]
    elif name == "script" and rest:
        rest = rest[1:]  # the typescript file; what follows it is the command
    return rest, strings, len(words) - min(originals, len(rest)), assignments


def prefix_effect(word, following):
    """Whether a builtin behind this prefix still runs in the shell that reads the line (SPD-030, probed in zsh 5.9 and bash
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
# 5.9 -f and zsh -f -o nobareglobqual, `coproc { trap 'git push' EXIT; }` pushed, SPD-054), or not at all ("process": an
# external wrapper execs a program and finds no builtin).  A builtin runs for the first three and here for the first two.
EFFECT_ORDER = {"shell": 0, "either": 1, "fork": 2, "process": 3}


def builtin_runs(effect):
    """A builtin behind the prefixes runs somewhere, so what it stores runs too (SPD-054)."""
    return effect != "process"


def builtin_runs_here(effect):
    """... and in the shell that reads the line, so a directory it changes is the line's own (SPD-030)."""
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


def cd_target(word, a, physical=False):
    """The directories one cd argument may lead to from the directories in force, or None when the hook cannot know:
    `-` and `~-` (OLDPWD), a stack entry (+N, -N, ~N), `~name` (a user, or a zsh named directory), a variable, a glob or a
    brace expansion, a CDPATH it cannot read, a relative target in a loop or a function body.  A bare relative target
    may also land under a CDPATH entry (bash tries those first, zsh after the current directory)."""
    if word == "":
        return a.cwds  # both shells stay
    if word == "-" or "$" in word or "`" in word or hookio.SUBST in word or syntax.GLOB_RE.search(word) or re.fullmatch(r"[+-]\d+", word):
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
    resolve = os.path.realpath if physical else os.path.normpath
    return frozenset(resolve(p) for p in paths)


def cd_destinations(name, args, a):
    """Where cd, chdir, pushd or popd with these arguments may leave the shell; None when the hook cannot follow.  Options:
    -L and -P (and --) are read alike by zsh and bash for cd, -P resolving symlinks first; every other option (-q, -s, -e,
    -@, -N) is refused as unfollowable, since zsh reads an option it does not know as the first string of `cd old new`
    and bash 3.2 rejects it, and pushd takes none but -- in both.  popd, and pushd with no directory, go where the stack
    says.  Two arguments: zsh replaces the first occurrence of the first in the current directory with the second, bash 3.2
    changes to the first, a later bash stays."""
    if name == "popd":
        return None
    physical = False
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
    # SPD-037: a directory that exists but the process cannot enter (no execute bit, /var/root, one a member chmod 000's)
    # fails the cd exactly as a missing one does -- probed in zsh 5.9 and bash 3.2: "permission denied", and PWD stays
    # put -- so the hook keeps the old directory beside the new one rather than assume the cd ran.
    if new is not None and not all(os.path.isdir(d) and os.access(d, os.X_OK) for d in new):
        a.cd_uncertain = True
    a.cwds = settle(effect, a.cwds, new)
