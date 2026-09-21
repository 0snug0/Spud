"""shell/inline_programs: the program an interpreter run spells on the line rather than reads from a file (SPD-150).

BADS-140/Jeremy wrote `scripts/web-seed.js`, a path outside his deliverable globs, with

    python3.14 - <<'PY'
    import pathlib
    p = pathlib.Path('scripts/web-seed.js')
    p.write_text(p.read_text().replace(...))
    PY

and patched two files inside his globs the same way.  Nothing went through Edit or Write, so the edit hook never saw
them, and the Bash rule read the line as far as `python3.14` and appended kind `other`: SPD-126's brief left the
interpreters out on purpose, as shell/spelled_writes says, so Law 5's fence stood only where a member used the tools.

The rule this module decides: for a caller the Bash rule holds (a member), an interpreter run whose program the line
spells rather than reads from a file is refused, because the hook reads no such program and cannot tell what it writes.
Spelled means an option that carries the program -- python's `-c`, node's (bun's, deno's) `-e`/`--eval`/`-p`/`--print`,
perl's `-e`/`-E`, ruby's `-e` -- or standard input, where the line feeds it and the interpreter has no program of its
own (`python3.14 - <<'PY'`, `echo ... | node`, `python3 < x.py`, `cat x | perl`).

What stays exactly as it was: a program from a file (`python3.14 -I -S tests/suite.py`, the spud launcher,
`node scripts/build-web.js`), python's `-m module`, and an interpreter left to read a terminal, which runs no program of
the line's (`python3` on its own, the REPL).  Whether a member may run a script file it wrote itself is SPD-145's open
question, which nothing here touches.  Spud keeps his inline programs: the hook cannot read those either, but Law 1
binds him where it cannot see, and his are investigation rather than a way past a fence.

The table below is each interpreter's own option grammar, from its manual: python(1) and `python3.14 --help`, node(1)
with bun's and deno's spellings of its two, perlrun, and ruby(1).  perl's switches are read in shell/spelled_writes too,
for the files `-i` edits; both rows come from perlrun and neither reads the other's, because they answer different
questions -- which switch gave the program, against what `-i` puts beside each file.
"""

import re

from . import prepare, stdin_text, syntax

RUBY_RE = re.compile(r"^ruby(?:\d+(?:\.\d+)*)?$")  # ruby, ruby3.4: versioned names as syntax.PERL_RE takes perl's


class Interpreter:
    """One interpreter's options as its own manual reads them.

    `code`: the short letters whose value is the program the line spells (attached to the letter, or the next word).
    `code_long`: the long options that carry it (`--eval CODE`, `--eval=CODE`).
    `value`: the other short letters whose value is the rest of their word or, where there is none, the next word, so a
    letter of `code` standing in that value is text and not an option (`perl -I dir -e ...`).
    `attached`: the short letters whose value is the rest of their own word alone and may be empty, after which the word
    is over (perl's `-i[extension]`: `-pie` is -p and -i with extension `e`, and no -e at all).
    `digits`: the short letters an optional number follows, after which the cluster goes on (perl's `-0`, `-l`).
    `value_long`: the long options whose value is the next word unless an `=` attaches it.
    `module`: the short letters that name a program the hook reads somewhere else (python's `-m`, whose module name the
    analysis already reads for a database call)."""

    __slots__ = ("code", "code_long", "value", "attached", "digits", "value_long", "module")

    def __init__(self, code="", code_long=(), value="", attached="", digits="", value_long=(), module=""):
        self.code, self.code_long, self.value = code, code_long, value
        self.attached, self.digits, self.value_long, self.module = attached, digits, value_long, module


# python(1): -c takes the rest of its word or the next one and ends the options, -m a module, -W, -X and -Q a value; every
# other switch is a flag, and --check-hash-based-pycs is the one long option whose value is the next word.
PYTHON = Interpreter(code="c", value="WXQ", value_long=("--check-hash-based-pycs",), module="m")
# node(1): -e/--eval and -p/--print carry the program (`node -pe 'x'` clusters them), -r/--require a module to load
# first.  bun and deno spell the same two, and node's -c is --check, a flag, which is why it is no code letter here.
JS = Interpreter(code="ep", code_long=("--eval", "--print"), value="r", value_long=("--require",))
# perlrun: -e and -E carry the program, -I a directory, -C, -D, -F, -M, -m, -V, -x and -i the rest of their own word
# (-d only before a `:` or `=`, read below), -0 and -l an optional number the cluster goes on after.
PERL = Interpreter(code="eE", value="I", attached="CDFMVimx", digits="0l")
# ruby(1): -e carries the program, -C, -E, -F, -I and -r take a value, -0, -K, -T, -W, -i and -x the rest of their word.
RUBY = Interpreter(code="e", value="CEFIr", attached="Kix", digits="0TW")


def interpreter(base):
    """The table entry for a command's base name, or None when it is no interpreter this module reads."""
    if syntax.PYTHON_RE.match(base):
        return PYTHON
    if base in syntax.JS_RUNTIMES:
        return JS
    if syntax.PERL_RE.match(base):
        return PERL
    if RUBY_RE.match(base):
        return RUBY
    return None


def read_inline(cmd, base, words, a, fed):
    """Record what an interpreter run spells as its own program (module docstring), which bash_rule turns into Law 1's
    refusal for a member: (the command word as spelled, the option that carries the program, or None where the
    interpreter reads it on standard input).

    `fed` is whether the line puts anything on that standard input at all (shell/stdin_text.input_fed), which is not
    whether the hook can say what it is: `cat x | node` and `python3 < f` feed a program the hook cannot read, while
    `python3` on its own is the REPL and runs none.  A command outside the table, a program from a file and a module
    record nothing."""
    how, option = spelled_program(base, words)
    if how == "option" or (how == "stdin" and fed):
        a.findings.append(("inline", (prepare.deglob(cmd), option if how == "option" else None)))


def spelled_program(base, words):
    """(how this interpreter run reaches its program, the option that carries it): ("option", the option as spelled)
    for a program an option's value holds, ("stdin", the operand that names standard input or None) for one the
    interpreter reads there, or (None, None) for a program from a file, a module, or a command outside the table.

    The words are the simple command's own, its redirections taken out and its prefixes and wrappers read (analyse's
    dispatch), so words[0] is the interpreter and the options follow.  `--` ends the options, and so does the first
    word that is not one: it is the program's file, whatever it holds."""
    kind = interpreter(base)
    if kind is None:
        return None, None
    i, options = 1, True
    while i < len(words):
        w = prepare.deglob(words[i])
        if options and w == "--":
            options, i = False, i + 1  # the word after it is the program's file, whatever it looks like
            continue
        if w in stdin_text.STDIN_OPERANDS:  # `-`, /dev/stdin, /dev/fd/0: the program is what the line puts there
            return "stdin", w
        if not options or not w.startswith("-"):
            return None, None  # a program from a file, which the hook reads no more of than it ever did (SPD-145)
        how, option, skip = _long(w, kind) if w.startswith("--") else _cluster(w, kind)
        if how == "option":
            return how, option
        if how == "module":
            return None, None
        i += skip
    return "stdin", None  # no program of its own: it runs what it reads on standard input


def _cluster(word, kind):
    """One short-option cluster as the interpreter's own getopt reads it: (what it says about the program -- "option"
    for one whose value is the program, "module" for one the hook reads elsewhere, None for neither -- the option as
    spelled, and how many words the cluster takes)."""
    t = prepare.deglob(word)
    k = 1
    while k < len(t):
        c = t[k]
        if c in kind.code:
            return "option", "-" + c, 1
        if c in kind.module:
            return "module", "-" + c, 1
        if c in kind.attached or (c == "d" and t[k + 1 : k + 2] in (":", "=")):
            return None, None, 1  # its value is the rest of its own word, never the next (perl's -i, ruby's -x)
        if c in kind.value:
            return None, None, 1 if k + 1 < len(t) else 2
        k += 1
        if c in kind.digits:
            while k < len(t) and t[k].isdigit():
                k += 1
    return None, None, 1


def _long(word, kind):
    """One long option: ("option", it, 1) where it carries the program (`--eval CODE`, `--eval=CODE`), else
    (None, None, the words it takes)."""
    name, sep, _value = prepare.deglob(word).partition("=")
    if name in kind.code_long:
        return "option", name, 1
    return None, None, 2 if (not sep and name in kind.value_long) else 1
