"""shell/spelled_writes: the files a command names on the line past syntax.ARG_WRITE_COMMANDS -- dd's of=, sort's -o, the
templates mktemp fills in, split's pieces and perl's in-place edits -- read for bash_reason to hold to the path rule as a
redirection target.

None of them used to be read: `dd if=/dev/zero of=out/f`, `sort -o out/f in`, `mktemp out/tmp.XXXX`, `split big
out/part_` and `perl -i -pe 's/a/b/' out/f` recorded no write at all, so a member's `sort -o ledger/tickets/SPD-001.md x` or
`dd of=tests/fake/.git/hooks/pre-commit` never met Law 5 or the git-directory rule.  None of their grammars is a shape
of syntax.ARG_WRITE_COMMANDS (an operand spelled `of=`, getopt_long's permuted options, a name the command picks, perl's
switch clusters), so they have a table of their own beside it, syntax.SPELLED_WRITE_COMMANDS, and record into the same
a.arg_writes.  Each is read on this Mac's man page:
- dd(1): `of=file` writes file; BSD dd refuses an operand given twice and GNU's keeps the last, so the last is the one.
- sort(1), FreeBSD's bsdsort: -o/--output writes its file (which may also be an input), -T/--temporary-directory holds the
  temporary files sort names itself there (a whole-subtree write, as tree_writes reads a directory a tool fills), and
  --compress-program names a program sort runs, read as a command.  getopt_long permutes and takes an abbreviation.
- mktemp(1): each template's trailing Xs become characters mktemp picks from [0-9A-Za-z]; -p (--tmpdir) is the directory a
  relative template is read under, and -t's prefix is filled in under the temporary directory: the environment's (a temp
  root every caller may write) unless the line sets TMPDIR or gives -p.  -d makes a directory, -u nothing.
- split(1): the pieces are the prefix (`x` when there is none) and a suffix of -a's length (2 by default) from a-z, or
  0-9 under -d; without -a and -d the suffix grows past its initial length when it runs out (text_cmds' split.c, autosfx).
- perlrun: -i[extension] edits every file the <> construct reads in place -- the operands after the program, which is -e's
  (or -E's) or the first operand -- with `<file><extension>` as the backup, or, when the extension holds `*`, the extension
  with each `*` replaced by the file; `-i` and `-i*` keep none.  The files are read as written whether or not -p or -n
  wraps the program, since the program may read <> itself; what perl's program writes by itself stays unread (the
  interpreters in general are shell/inline_programs' reading).

A name the command picks is held with hooks/pathrule.NAME_CHAR (and NAME_MORE for a run that may grow) in the place of each
picked character, which every wildcard of a deliverable glob matches and no literal does: so a glob lets the write in only
when it lets in every name the command may pick there, the way a member's globs hold a redirection glob, and each name the
path rule refuses by its spelling (.git, .gitconfig, a git config file, the ledger's generated roots and state directory)
that the command could pick is checked as itself.  A word the line cannot settle where an option may stand -- a
substitution, a positional, a variable the line assigns but cannot settle, xargs's input (tree_writes.hidden_word) -- may be
-o or -i with any file, and is syntax.ANY_PATH, refused to a member as a target the hook cannot place.  A glob word there is
read as spelled, as shell/tree_writes reads one.

Past 250 lines (the package's look-again point) it stays whole: a list of five short grammars with one caller, the analysis,
and the two readings they share -- getopt_long's, which shell/downloads reads wget with too, and the picked names."""

import os
import re

from . import analyse, arg_writes, globbing, prepare, syntax, tree_writes
from ..hooks import hookio, pathrule


# sort (bsdsort's sort.c): the short options taking a value, and every long option with whether it requires one (True),
# takes one only after `=` ("="), or takes none (False).  An option this Mac's sort may not take a value for is read as
# taking none, which reads its next word as an option or a file: never looser.
SORT_VALUE_LETTERS = frozenset("koStT")
SORT_LONGS = {
    "batch-size": True, "buffer-size": True, "check": "=", "compress-program": True, "debug": False,
    "dictionary-order": False, "field-separator": True, "files0-from": True, "general-numeric-sort": False, "heapsort": False,
    "help": False, "human-numeric-sort": False, "ignore-case": False, "ignore-leading-blanks": False, "ignore-nonprinting": False,
    "key": True, "merge": False, "mergesort": False, "mmap": False, "month-sort": False, "numeric-sort": False, "output": True,
    "parallel": False, "qsort": False, "radixsort": False, "random-sort": False, "random-source": True, "reverse": False,
    "sort": True, "stable": False, "temporary-directory": True, "unique": False, "version": False, "version-sort": False,
    "zero-terminated": False,
}
# mktemp (mktemp(1)): -p and -t take a value; --tmpdir takes one only after `=`.
MKTEMP_VALUE_LETTERS = frozenset("pt")
MKTEMP_LONGS = {"directory": False, "tmpdir": "=", "quiet": False, "dry-run": False}
MKTEMP_ALPHABET = "0-9A-Za-z"  # the characters an X becomes (FreeBSD's mktemp, 62 of them: mktemp(1))
MKTEMP_T_XS = 8  # -t's template: <tmpdir>/<prefix>.XXXXXXXX
# split (split(1), getopt "0::...9::a:b:cdl:n:p:"): the letters taking a value, and the digits of the obsolete line count,
# each taking the rest of its word.
SPLIT_VALUE_LETTERS = frozenset("ablnp")
# perl (perlrun): the switches whose argument is the rest of their word (and -e, -E and -I the next word when the rest is
# empty), and those followed by optional digits before the cluster goes on.
PERL_REST = frozenset("CDFMVmx")
PERL_NEXT = frozenset("eEI")
# The characters that begin what a word does not spell until the shell runs: an expansion, a substitution, or an operand
# find or xargs hands the command.
_UNSETTLED = ("$", "`", hookio.SUBST, syntax.FIND_PATH, syntax.INPUT_OPERAND, syntax.ANY_PATH)


def read_spelled_writes(cmd, base, words, a, depth):
    """Record what `words`, a command of syntax.SPELLED_WRITE_COMMANDS or a perl, writes by the names it spells (module
    docstring); `cmd` is its command word as spelled, `base` what it dispatches on.  The line's own values are put in its
    words first (arg_writes.resolved), so a settled `$NAME` is read as the option or the file it holds."""
    args = [arg_writes.resolved(w, a) for w in words[1:]]
    if syntax.PERL_RE.match(base):
        read_perl(cmd, args, a)
    else:
        {"dd": read_dd, "sort": read_sort, "mktemp": read_mktemp, "split": read_split}[base](cmd, args, a, depth)


def spelled_write(a, cmd, word, kind=None, suffix=None):
    """One write by argument of the file `word` names, as arg_writes records it (a backup `suffix` beside it)."""
    a.arg_writes.append((cmd, word, a.cwds, (), "path", suffix, kind))


def after_spelled(word, n):
    """The masked word from its (n+1)th spelled character on: a sentinel counts as the character it stands for, and a
    marker deglob removes as none."""
    at = 0
    for k, c in enumerate(word):
        if at >= n:
            return word[k:]
        at += len(prepare.deglob(c))
    return ""


def spelled_head(word, n):
    """The masked word's first n spelled characters, the part after_spelled leaves out."""
    return word[: len(word) - len(after_spelled(word, n))]


def may_spell(word, prefix, a):
    """True when a word holds something the line does not settle (tree_writes.hidden_word) and what it spells before that
    is a start of `prefix`, so the shell may hand the command a word that begins with it (`dd $ARGS` may be `dd of=x`)."""
    cuts = [k for k in (word.find(c) for c in _UNSETTLED) if k != -1]
    return bool(cuts) and prefix.startswith(prepare.deglob(word[: min(cuts)])) and tree_writes.hidden_word(word, a)


def option_hidden(word, a):
    """True when the part of a word spelled with `-` that the command reads as option names holds what the line does not
    settle (tree_writes.hidden_word), so it may name any option."""
    return tree_writes.hidden_word(word, a)


def operand_hidden(word, a):
    """True when a word not spelled with `-` may still become an option: it begins with what the line does not settle.  A
    word that begins with a character the line spells is an operand whatever follows it, and so is a path find hands its
    command, which begins with a starting point (find reads one spelled with `-` as an option)."""
    return may_spell(word, "", a) and not word.startswith(syntax.FIND_PATH)


def long_match(name, longs):
    """The long option `name` (without its dashes) names as getopt_long reads it: itself, or the one option it is a unique
    start of; None when it names none or several (getopt_long refuses it, and the command writes nothing)."""
    if name in longs:
        return name
    found = [n for n in longs if n.startswith(name)]
    return found[0] if len(found) == 1 else None


def read_options(args, letters, longs, a, permute=True):
    """(options [(name, value word or None)], operands, hidden) of a command's arguments as getopt_long reads them: `--`
    ends the options and `-` alone is an operand; a short cluster's first letter in `letters` takes the rest of its word,
    or the next word; a long option is itself or the unique start of one in `longs`, `--name=value` gives its value, and
    the next word does when `longs` says it requires one.  `permute`: options may follow operands (getopt_long's default),
    else the first operand ends them (getopt's).  `hidden`: the part of a word getopt reads as option names holds what the
    line does not settle, or a word where an option may stand begins with it (option_hidden, operand_hidden), so it may be
    any option."""
    options, operands, hidden, i, ended = [], [], False, 0, False
    while i < len(args):
        w = args[i]
        t = prepare.deglob(w)
        if ended or t == "-" or not t.startswith("-"):
            if not ended and operand_hidden(w, a):
                hidden = True
            operands.append(w)
            ended = ended or not permute
        elif t == "--":
            ended = True
        elif t.startswith("--"):
            name, eq, _ = t.partition("=")
            hidden = hidden or option_hidden(spelled_head(w, len(name)), a)
            full = long_match(name[2:], longs)
            value = None
            if eq:
                value = after_spelled(w, len(name) + 1)
            elif full is not None and longs[full] is True:
                value, i = (args[i + 1] if i + 1 < len(args) else ""), i + 1
            options.append(("--" + (full if full is not None else name[2:]), value))
        else:
            for k in range(1, len(t)):
                if t[k] in letters:
                    hidden = hidden or option_hidden(spelled_head(w, k + 1), a)
                    if k + 1 < len(t):
                        value = after_spelled(w, k + 1)
                    else:
                        value, i = (args[i + 1] if i + 1 < len(args) else ""), i + 1
                    options.append(("-" + t[k], value))
                    break
                options.append(("-" + t[k], None))
            else:
                hidden = hidden or option_hidden(w, a)
        i += 1
    return options, operands, hidden


def values_of(options, names):
    """The values the options `names` were given, in order, an empty one left out (the command refuses it)."""
    return [v for n, v in options if n in names and v is not None and prepare.deglob(v) != ""]


def picked_write(a, cmd, word, picked, alphabet, more=False, kind=None):
    """Record the write of a file named by `word` with its last `picked` spelled characters chosen by the command from the
    regex class `alphabet` (and, with `more`, any number more of them): the name with each chosen character
    pathrule.NAME_CHAR, and each name the path rule refuses by its spelling that the command could choose, as itself."""
    stem = word[: len(word) - picked]
    spelled_write(a, cmd, stem + pathrule.NAME_CHAR * picked + (pathrule.NAME_MORE if more else ""), kind)
    folder = stem[: stem.rfind("/") + 1]
    last = prepare.deglob(stem[len(folder) :])
    shape = re.compile(re.escape(last) + "[%s]{%d%s}" % (alphabet, picked, "," if more else ""), re.IGNORECASE)
    for name in special_names():
        if shape.fullmatch(name):
            spelled_write(a, cmd, folder + name, kind)


def special_names():
    """The names the path rule refuses by their spelling, wherever they stand or at a checkout's root: a git directory, a
    git config file's last component, the ledger's generated roots and its state directory."""
    return ((pathrule.GIT_DIR_COMPONENT,) + pathrule.GIT_CONFIG_FILE_NAMES + tuple(t[-1] for t in pathrule.GIT_CONFIG_FILE_TAILS)
            + hookio.GENERATED_ROOTS + (hookio.STATE_DIR,))


def trailing_xs(word):
    """How many Xs end the template `word` spells, which mktemp fills in: none in a word the hook cannot resolve, whose
    reading refuses a member anyway."""
    return 0 if arg_writes.unresolved(word) else len(word) - len(word.rstrip("X"))


def read_dd(cmd, args, a, depth):
    """dd: the last `of=` operand is the file it writes; a word the line does not settle that may still begin `of=` is a
    target of its own, which a member is refused as one the hook cannot resolve."""
    target = None
    for w in args:
        if prepare.deglob(w).startswith("of="):
            target = after_spelled(w, 3)
        elif may_spell(w, "of=", a):
            spelled_write(a, cmd, w)
    if target:
        spelled_write(a, cmd, target)


def read_sort(cmd, args, a, depth):
    """sort: -o/--output's file, a whole-subtree write of -T's directory, --compress-program's program read as a command;
    anything, when a word where an option may stand is one the line does not settle."""
    options, _, hidden = read_options(args, SORT_VALUE_LETTERS, SORT_LONGS, a)
    if hidden:
        spelled_write(a, cmd, syntax.ANY_PATH, "tree")
        return
    for value in values_of(options, ("--compress-program",)):
        analyse.analyse_new_shell(a, prepare.deglob(value), depth + 1)
    for value in values_of(options, ("-o", "--output")):
        if prepare.deglob(value) != "-":
            spelled_write(a, cmd, value)
    for value in values_of(options, ("-T", "--temporary-directory")):
        spelled_write(a, cmd, value, "tree")


def read_mktemp(cmd, args, a, depth):
    """mktemp: each template, read under -p's directory when relative, with its trailing Xs picked; -t's prefix (or `tmp`
    when no template is given) filled in under the temporary directory, read only where the line names it; -d makes a
    directory, -u nothing."""
    options, templates, hidden = read_options(args, MKTEMP_VALUE_LETTERS, MKTEMP_LONGS, a)
    if hidden:
        spelled_write(a, cmd, syntax.ANY_PATH, "tree")
        return
    names = {n for n, _ in options}
    if names.intersection(("-u", "--dry-run")):
        return
    kind = "make" if names.intersection(("-d", "--directory")) else None
    line_tmpdir = "$TMPDIR" if "TMPDIR" in a.vars or "TMPDIR" in a.assigned else None
    tmpdir = None  # the directory a relative template is read under
    for name, value in options:
        if name == "-p" or (name == "--tmpdir" and value):
            tmpdir = value
        elif name == "--tmpdir":  # empty or omitted: TMPDIR, the environment's when the line does not set it
            tmpdir = line_tmpdir or os.environ.get("TMPDIR") or "/tmp"
    if tmpdir is not None:
        tmpdir = arg_writes.resolved(tmpdir, a)
    for template in templates:
        text = prepare.deglob(template)
        word = template if tmpdir is None or text.startswith(("/", "~")) else tmpdir.rstrip("/") + "/" + template
        picked_write(a, cmd, word, trailing_xs(template), MKTEMP_ALPHABET, kind=kind)
    if "-t" in names or not templates:
        prefix = (values_of(options, ("-t",)) or ["tmp"])[-1]
        for folder in dict.fromkeys(d for d in (line_tmpdir and arg_writes.resolved(line_tmpdir, a), tmpdir) if d):
            word = folder.rstrip("/") + "/" + prefix + "." + "X" * MKTEMP_T_XS
            picked_write(a, cmd, word, MKTEMP_T_XS, MKTEMP_ALPHABET, kind=kind)


def read_split(cmd, args, a, depth):
    """split: its pieces, the prefix (the second operand, `x` by default) and a suffix split picks, of -a's length or, with
    no -a, of two letters that grow when they run out (never under -d, whose suffix is digits)."""
    options, operands, hidden = read_options(args, SPLIT_VALUE_LETTERS, {}, a, permute=False)  # -NNN: flags, never a file
    if hidden:
        spelled_write(a, cmd, syntax.ANY_PATH, "tree")
        return
    numeric = any(n == "-d" for n, _ in options)
    lengths = values_of(options, ("-a",))
    length = prepare.deglob(lengths[-1]) if lengths else "2"
    fixed = length.isdigit() and int(length) > 0
    prefix = operands[1] if len(operands) > 1 else "x"
    picked = int(length) if fixed else 1
    picked_write(a, cmd, prefix + "X" * picked, picked, "0-9" if numeric else "a-z", more=not fixed or not (lengths or numeric))


def perl_switches(args, a):
    """(the -i extension or None, whether -e or -E gave the program, the words after the switches, the words that may be
    files once a word the line does not settle is read as switches, or None) as perl reads its command line (perlrun,
    perl.c): a cluster's switches in order, -i taking the rest of its word as the extension, -e, -E and -I the rest or the
    next word, -C, -D, -F, -M, -m, -V and -x the rest, -d a `:module` or `=...` rest, -l and -0 their digits; `--` ends the
    switches, as does `-` or the first word not spelled with `-`, which perl still reads as switches when it expands to
    one.  A switch word the line does not settle may be -i with any extension, or -e, so every word after the switches may
    be a file; a first word after them that may begin with `-` may be both, so every word after it may."""
    extension, program, unsure, ended, i = None, False, False, False, 0
    while i < len(args):
        w = args[i]
        t = prepare.deglob(w)
        if t == "--":
            i, ended = i + 1, True
            break
        if not t.startswith("-") or t == "-":
            break
        k = 1
        while k < len(t):
            c = t[k]
            if c == "i":
                extension = after_spelled(w, k + 1)
                unsure = unsure or option_hidden(spelled_head(w, k + 1), a)
                break
            if c in PERL_NEXT:
                program = program or c in "eE"
                unsure = unsure or option_hidden(spelled_head(w, k + 1), a)
                i += k + 1 == len(t)
                break
            if c in PERL_REST or (c == "d" and t[k + 1 : k + 2] in (":", "=")):
                unsure = unsure or option_hidden(spelled_head(w, k + 1), a)
                break
            k += 1
            if c in "l0":
                while k < len(t) and (t[k].isdigit() or (c == "0" and t[k] in "xXabcdefABCDEF")):
                    k += 1
        else:
            unsure = unsure or option_hidden(w, a)
        i += 1
    rest = args[i:]
    candidates = rest if unsure else None
    if candidates is None and not ended and rest and operand_hidden(rest[0], a):
        candidates = rest[1:]
    return extension, program, rest, candidates


def read_perl(cmd, args, a):
    """perl -i: every file operand, and its backup (module docstring); nothing without -i.  When a word the line does not
    settle may be -i, the files it may edit, whose backup the extension it may give can put anywhere, are syntax.ANY_PATH;
    when no word may be such a file, nothing is written."""
    extension, program, rest, candidates = perl_switches(args, a)
    if candidates:
        spelled_write(a, cmd, syntax.ANY_PATH, "tree")
        return
    if extension is None:
        return
    files = [f for f in (rest if program else rest[1:]) if prepare.deglob(f) != "-"]
    text = prepare.deglob(extension)
    for f in files:
        if text in ("", "*"):
            spelled_write(a, cmd, f)
        elif "*" not in text:
            spelled_write(a, cmd, f, suffix=extension)
        else:
            spelled_write(a, cmd, f)
            spelled_write(a, cmd, f.join(globbing.literalize(part) for part in text.split("*")))
