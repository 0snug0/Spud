"""shell/find_xargs: the operands a line does not spell (SPD-126): what find deletes, the files its -fprint and kin name,
the command its -exec, -execdir, -ok and -okdir run with `{}` a path under its starting points; and where xargs puts what
it reads from its input.

Until SPD-126 neither was read.  `find . -exec git push \\;` was silent for a member, past Law 7, because find's utility
was never a command the analysis read; `find tests/tmp -name '*.pyc' -delete` and `find . -exec rm {} \\;` recorded no
write at all; and `xargs rm < list` read as an `rm` with no operand.

find.  In the Bash tool `find` is Claude Code's shadow function, which runs the claude binary as bfs (the shell snapshot
~/.claude/shell-snapshots/ defines it, SPD-133), and this Mac's BSD find where that binary is missing.  bfs takes BSD's and
GNU's primaries and lets "Flags (-H/-L/-P etc.), paths, and expressions ... be freely mixed in any order" (its --help), so
every word that is neither a flag, a primary, an operator nor a primary's argument is a starting point, and none means `.`;
a primary this reading does not know may have taken the next word, so `.` is then a starting point too.
- -delete (bfs's -rm too) removes everything under each starting point: arg_writes' "rm-tree", a whole-subtree removal,
  or the removal of the starting point alone when it is not a directory now (find(1): "Delete found files and/or
  directories"; a starting point that is a file is the one file found).
- the utility of -exec, -execdir, -ok and -okdir and its arguments are analysed as the command they are (analyse_words:
  Law 7, a spud call, a nested wrapper, a `sh -c` string), with `{}` read as syntax.FIND_PATH wherever it stands (BSD
  find(1): "If the string "{}" appears anywhere in the utility name or the arguments it is replaced by the pathname of the
  current file").  Every write that command records whose file holds FIND_PATH becomes a write anywhere under each
  starting point ("find-tree", or "rm-tree" for a removal), and each starting point is walked for a git directory
  (arg_writes.written_paths), since what find hands its command is already on disk there.  A write the line spells stays
  SPD-121's.  -execdir and -okdir run in the found file's directory, which the hook cannot know, so their command is read
  with its directories unknown, as after an unresolved cd.  `{}` in the utility's own name is a program find found.
- -fls, -fprint, -fprint0 and -fprintf name a file find writes: SPD-121's write of that file.
A word where a primary may stand that holds a value the member controls (tree_writes.hidden_word: a substitution, a
positional, a variable the line assigns but cannot settle, an operand xargs or an outer find hands it) may be -delete or
several words, and is refused as SPD-043 refuses a word the dispatch reads by name.

xargs.  xargs runs its utility with what it reads from its input appended after the words the line spells, or put where
-J's replstr stands as an argument of its own, or into every argument (not the utility) holding -I's replstr (xargs(1);
GNU's -i and --replace read as -I).  The hook cannot know any of it: syntax.INPUT_OPERAND stands there, and when appended it
stands twice, since one input may be several operands and the first may be read as an option (`xargs chmod`, whose input
is the mode and the files).  A write whose file holds it is refused to a member as an unresolvable target is, a
destination named on the line that only such operands land in (`xargs -J % cp % dest/`) is a whole-subtree write of it,
and a command word holding it is a program the input names."""

import os
import re

from . import analyse, arg_writes, globbing, prepare, syntax, tree_writes


# find's grammar: BSD find(1) and bfs's --help (the claude binary's), which is a superset of GNU's.
EXEC_PRIMARIES = {"-exec": False, "-ok": False, "-execdir": True, "-okdir": True}  # whether it runs in the found file's directory
DELETE_PRIMARIES = frozenset({"-delete", "-rm"})
FILE_PRIMARIES = {"-fls": 1, "-fprint": 1, "-fprint0": 1, "-fprintf": 2}  # the first word after it is a file find writes
ONE_ARGUMENT = frozenset({
    "-amin", "-anewer", "-asince", "-atime", "-Bmin", "-Bnewer", "-Bsince", "-Btime", "-cmin", "-cnewer", "-csince", "-ctime",
    "-mmin", "-mnewer", "-msince", "-mtime", "-since", "-newer", "-maxdepth", "-mindepth", "-regextype", "-fstype", "-gid",
    "-uid", "-group", "-user", "-ilname", "-iname", "-ipath", "-iregex", "-iwholename", "-lname", "-name", "-path",
    "-wholename", "-regex", "-inum", "-links", "-perm", "-samefile", "-size", "-type", "-xtype", "-used", "-xattrname",
    "-printf", "-limit", "-flags", "-context",
    "-D", "-S",  # bfs's flags taking the next word: a debug flag, a search strategy
})
OPTIONAL_NUMBER = frozenset({"-depth", "-exit"})  # `-depth 2` is a test, `-depth` alone an option; `-exit [STATUS]`
KNOWN = frozenset({
    "-print", "-print0", "-printx", "-ls", "-prune", "-quit", "-true", "-false", "-empty", "-executable", "-readable",
    "-writable", "-hidden", "-nohidden", "-noerror", "-nouser", "-nogroup", "-acl", "-xattr", "-sparse", "-mount", "-xdev",
    "-noleaf", "-daystart", "-ignore_readdir_race", "-noignore_readdir_race", "-status", "-unique", "-warn", "-nowarn",
    "-color", "-nocolor", "-help", "--help", "-version", "--version", "-not", "-a", "-and", "-o", "-or", "-exclude",
    "-capable", "(", ")", "!", ",", "--",
})
FOLLOW = frozenset({"-L", "-H", "-follow"})
NUMBER_RE = re.compile(r"[-+]?\d+\Z")
NEWER_XY_RE = re.compile(r"-newer[aBcm][aBcmt]\Z")
FLAGS_RE = re.compile(r"-(?:[HLPEXdsx]+|O\d*|j\d+)\Z")  # the flags, clustered as BSD's getopt reads them: -dsx, -O3, -j8
REMOVERS = ("rm", "unlink", "rmdir")


def read_find(cmd, words, a, depth):
    """Record what the find call `words` (its command word spelled `cmd`) writes, and read the commands it runs (the module
    docstring)."""
    line_cwds = a.cwds
    ws = [arg_writes.resolved(w, a) for w in words[1:]]
    starts, execs, files, removes, follow, unsure = [], [], [], False, False, False
    i, n = 0, len(ws)
    while i < n:
        w = ws[i]
        t = prepare.deglob(w)
        if t in EXEC_PRIMARIES:
            j = i + 1
            while j < n and not exec_end(ws, i + 1, j):
                j += 1
            execs.append((ws[i + 1 : j], EXEC_PRIMARIES[t]))
            i = j + 1
            continue
        if t in FILE_PRIMARIES:
            files += ws[i + 1 : i + 2]
            i += 1 + FILE_PRIMARIES[t]
            continue
        if t in ("-f", "-files0-from"):  # a starting point, or a file of them the hook does not read
            starts += ws[i + 1 : i + 2] if t == "-f" else [syntax.INPUT_OPERAND]
            i += 2
            continue
        if t in ONE_ARGUMENT or NEWER_XY_RE.match(t):
            i += 2
            continue
        if t in OPTIONAL_NUMBER:
            i += 2 if i + 1 < n and NUMBER_RE.match(prepare.deglob(ws[i + 1])) else 1
            continue
        if t in DELETE_PRIMARIES:
            removes = True
        elif t in FOLLOW or (FLAGS_RE.match(t) and t[1] not in "Oj" and ("L" in t or "H" in t)):
            follow = True
        elif t in KNOWN or FLAGS_RE.match(t):
            pass
        elif tree_writes.hidden_word(w, a) or (globbing.active_glob_word(w) and globbing.may_start_with_dash(w)):
            # may be -delete, or in bash several words: refused as a word read by name is (SPD-043, SPD-041), and read as
            # the starting point it may also be
            a.findings.append(("glob" if globbing.active_glob_word(w) else "var-word", syntax.shown_operands(t)))
            starts.append(w)
        elif t.startswith("-") and len(t) > 1:
            unsure = True
        else:
            starts.append(w)
        i += 1
    if not starts or unsure:
        starts.append(".")
    if removes:
        a.arg_writes += [(cmd, s, line_cwds, (), "path", None, "tree" if follow else "rm-tree") for s in starts]
    a.arg_writes += [(cmd, f, line_cwds, (), "path", None, None) for f in files]
    for command, elsewhere in execs:
        run_exec(cmd, command, elsewhere, starts, line_cwds, follow, a, depth)


def exec_end(ws, first, j):
    """True when ws[j] ends the command an -exec begun at ws[first] runs: `;`, or `+` right after `{}` (find(1))."""
    t = prepare.deglob(ws[j])
    return t == ";" or (t == "+" and j > first and prepare.deglob(ws[j - 1]) == "{}")


def run_exec(cmd, command, elsewhere, starts, line_cwds, follow, a, depth):
    """Analyse the command an -exec runs (module docstring), then read each write it recorded under find's starting points."""
    if not command:
        return
    words = [mark_text(w, "{}", syntax.FIND_PATH) for w in command]
    if syntax.FIND_PATH in words[0]:
        a.kinds.append("other")
        a.findings.append(("var", syntax.shown_operands(prepare.deglob(words[0]))))  # the program is a file find found
        return
    marks = (len(a.arg_writes), len(a.redirects), len(a.git_writes))
    before = a.cwds
    if elsewhere:
        a.cwds = None  # -execdir, -okdir: the directory of each file found
    try:
        analyse.analyse_words(words, [], a, depth + 1, [globbing.GLOB_READING_BUDGET], "process", True, len(words))
    finally:
        a.cwds = before
    under_starts(cmd, a, marks, starts, line_cwds, follow)


def under_starts(cmd, a, marks, starts, line_cwds, follow):
    """Replace each write recorded since `marks` whose file holds FIND_PATH -- by argument, by redirection, a git call's --
    with a write anywhere under each starting point, and add a walk of the starting points wherever such a path is the
    source a command copies or moves."""

    def spread(removal):
        if follow:  # a starting point that is a symlink is traversed: never read as the link alone
            return [(cmd, s, line_cwds, (), "path", None, "tree") for s in starts] + walks()
        return [(cmd, s, line_cwds, (), "path", None, "rm-tree" if removal else "find-tree") for s in starts]

    def walks():
        return [(cmd, s, line_cwds, (), "walk", None, None) for s in starts]

    kept = []
    for entry in a.arg_writes[marks[0] :]:
        spelled, word, _, sources, how, _, kind = entry
        base = os.path.basename(prepare.deglob(spelled)).casefold()
        if syntax.FIND_PATH in word:
            kept += spread(kind in ("remove", "rm-tree") or base in REMOVERS or (base == "mv" and how == "path"))
        else:
            kept.append(entry)
        if any(syntax.FIND_PATH in s for s in sources):
            kept += walks()
    moved = [t for t, _ in a.redirects[marks[1] :] if syntax.FIND_PATH in t]
    moved += [t for _, t, _ in a.git_writes[marks[2] :] if syntax.FIND_PATH in t]
    a.redirects[marks[1] :] = [r for r in a.redirects[marks[1] :] if syntax.FIND_PATH not in r[0]]
    a.git_writes[marks[2] :] = [g for g in a.git_writes[marks[2] :] if syntax.FIND_PATH not in g[1]]
    a.arg_writes[marks[0] :] = kept + (spread(False) if moved else [])


def mark_text(word, text, marker):
    """The masked word with every occurrence of `text` in what it spells -- its quotes' sentinels read as the characters
    they stand for -- replaced by `marker`."""
    if not text or text not in prepare.deglob(word):
        return word
    spelled = [prepare.deglob(c) for c in word]
    offsets, at = [], 0
    for piece in spelled:
        offsets.append(at)
        at += len(piece)
    literal = "".join(spelled)
    out, k, s = [], 0, literal.find(text)
    while s != -1:
        end = s + len(text)
        while k < len(word) and offsets[k] < s:
            out.append(word[k])
            k += 1
        while k < len(word) and offsets[k] < end:
            k += 1
        out.append(marker)
        s = literal.find(text, end)
    return "".join(out) + word[k:]


def xargs_input(words, consumed, rest):
    """(the words the xargs call `words` runs, with what it reads from its input marked INPUT_OPERAND where -J or -I puts
    it; whether that input is appended after them instead).  `consumed` is the index of its utility in `words`
    (directories.strip_wrapper), `rest` the words from there on."""
    values = syntax.WRAPPER_VALUE_OPTIONS["xargs"]
    replace = first = None
    i = 1
    while i < consumed:
        w = prepare.deglob(words[i])
        if w == "--":
            break
        if w.startswith("--"):
            name, eq, value = w.partition("=")
            if name == "--replace":
                replace = value if eq else "{}"
            elif name in values and not eq:
                i += 1
        elif w.startswith("-"):
            for k in range(1, len(w)):
                if w[k] == "i":  # GNU's -i[replstr]
                    replace = w[k + 1 :] or "{}"
                    break
                if "-" + w[k] in values:
                    value = w[k + 1 :]
                    if not value:
                        i += 1
                        value = prepare.deglob(words[i]) if i < len(words) else ""
                    if w[k] == "I":
                        replace = value
                    elif w[k] == "J":
                        first = value
                    break
        i += 1
    if not rest:
        return rest, False  # the utility is echo
    if replace:
        return rest[:1] + [mark_text(w, replace, syntax.INPUT_OPERAND) for w in rest[1:]], False
    if first:
        k = next((k for k, w in enumerate(rest) if prepare.deglob(w) == first), None)
        if k is not None:
            return rest[:k] + [syntax.INPUT_OPERAND] + rest[k + 1 :], False
    return rest, True
