"""shell/inline_programs: the program an interpreter run spells on the line rather than reads from a file.

Jeremy, a spudagent on another project, wrote `scripts/web-seed.js`, a path outside his deliverable globs, with

    python3.14 - <<'PY'
    import pathlib
    p = pathlib.Path('scripts/web-seed.js')
    p.write_text(p.read_text().replace(...))
    PY

and patched two files inside his globs the same way.  Nothing went through Edit or Write, so the edit hook never saw
them, and the Bash rule read the line as far as `python3.14` and appended kind `other`: the reading of writes by
argument left the interpreters out on purpose, as shell/spelled_writes says, so Law 5's fence stood only where a member
used the tools.

The rule this module decides: for a caller the Bash rule holds (a member), an interpreter run whose program the line
spells rather than reads from a file is refused, because the hook reads no such program and cannot tell what it writes.
Spelled means an option that carries the program -- python's `-c`, node's (bun's, deno's) `-e`/`--eval`/`-p`/`--print`,
perl's `-e`/`-E`, ruby's `-e` -- or standard input, where the line feeds it and the interpreter has no program of its
own (`python3.14 - <<'PY'`, `echo ... | node`, `python3 < x.py`, `cat x | perl`).  A third shape is a subcommand
whose own operand is the program (`deno eval <code>`).

What stays exactly as it was: a program from a file (`python3.14 -I -S tests/suite.py`, the spud launcher,
`node scripts/build-web.js`), python's `-m module`, and an interpreter left to read a terminal, which runs no program of
the line's (`python3` on its own, the REPL).  Whether a member may run a script file it wrote itself is an open question,
which nothing here touches.  Spud keeps his inline programs: the hook cannot read those either, but Law 1
binds him where it cannot see, and his are investigation rather than a way past a fence.

The table below is each interpreter's own option grammar, from its manual: python(1) and `python3.14 --help`, node(1)
with bun's and deno's spellings of its two, perlrun, and ruby(1).  perl's switches are read in shell/spelled_writes too,
for the files `-i` edits; both rows come from perlrun and neither reads the other's, because they answer different
questions -- which switch gave the program, against what `-i` puts beside each file.  The other rows: osascript(1),
php(1) (`php --help`), lua(1), Rscript's own usage in the R manual, `swift --help`, and deno's own help, whose grammar
is its subcommands'.  shell/interpreter_words reads the same table for the two shapes that are about the words rather
than the grammar: a word the line cannot settle where an option may stand, and the words an xargs appends.

What stays open, none of it this module's to close:

- **A family the table does not name.**  A row is what reads an interpreter, so a runner outside these names records
  nothing at all: julia's `-e`, elixir's `-e`, scala's and groovy's `-e`, luajit's `-e`, and whatever a project's
  toolchain brings next.  Each is one row when a project needs it.
- **A program from a file**, which is an open question for every caller, and with it every option that names a
  library the interpreter runs before the program: perl's `-M`, ruby's `-r`, node's `--require`, deno's `--preload`
  and `deno repl --eval-file`.  Each names a file, so each is that same question and not this one.
- **A subcommand that runs a command a file holds**: `npm run <name>`, `deno task <name>`, `bun run <script>`,
  `pnpm run` and `yarn <script>`, whose command comes from package.json or deno.json.  That is SPD-145's class (Eric's
  call: a member's shell whose commands come from a file is refused, with a project allow-list), not this module's.
  The inline half of the same shape -- a subcommand that hands a shell text the line spells, `bun exec`, `npm exec -c`,
  `npx`, `npm explore`, `deno task --eval`, `pnpm exec`, `yarn exec` -- is read since SPD-154, where an `sh -c` string
  is, by shell/runtime_shells; `deno task` is tabled "shell" below so its `--eval` is no program of deno's.
- **An environment variable that carries the interpreter's own switches**, which each manual limits to switches that
  carry no program: PERL5OPT takes only `-[CDIMUdmtwW]` (perlrun), RUBYOPT only `-d -E -I -K -r -T -U -v -w -W` and
  the `--debug`/`--enable`/`--disable` kin (ruby(1)), NODE_OPTIONS no `-e`, `-p` or script at all (node(1)), and
  PYTHONSTARTUP a file read in interactive mode (python(1)).  What is left is `-M` and `-r`, a library from a file.

Past 250 lines (the package's look-again point): it is one body of data read as one -- a row per family, each row its
manual's and its comment the citation -- with the single grammar that walks a run's words against it, which
spelled_program reads for a verdict and interpreter_words.read_index for indices, so the two can never disagree about
where an option ends and a program begins.  The seam that was real came out: the words such a run is read with, one
the line cannot settle and one an xargs appends, is shell/interpreter_words, with its own name and its own users.
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
    analysis already reads for a database call).

    For the families whose grammar is no shape of python's, node's, perl's or ruby's:
    `subcommands`: {the subcommand: what the words after it are} for a family that runs code under a subcommand rather
    than an option -- "code" where the subcommand's first operand is the program (`deno eval <code>`), "stdin" where a
    run with no program of its own reads one there (`deno repl`, `swift repl`), "file" where it reads none at all
    (`deno run`, whose only standard input is the `-` operand), "shell" where what it runs is shell text rather than a
    program, which shell/runtime_shells reads and nothing here does (`deno task`, whose `--eval` is a task's text and
    no program of deno's).
    `stdin_program`: whether this interpreter runs what it reads on standard input when the line gives it no program of
    its own, which is python's shape and not every family's (`Rscript` alone prints its usage).
    `whole_options`: whether its short options are whole words rather than getopt clusters, and so every word is read
    for one (a compiler driver: `swift -e`, beside seven hundred `-name value` options no cluster reading would
    survive)."""

    __slots__ = ("code", "code_long", "value", "attached", "digits", "value_long", "module",
                 "subcommands", "stdin_program", "whole_options")

    def __init__(self, code="", code_long=(), value="", attached="", digits="", value_long=(), module="",
                 subcommands=None, stdin_program=True, whole_options=False):
        self.code, self.code_long, self.value = code, code_long, value
        self.attached, self.digits, self.value_long, self.module = attached, digits, value_long, module
        self.subcommands, self.stdin_program, self.whole_options = subcommands or {}, stdin_program, whole_options


# python(1): -c takes the rest of its word or the next one and ends the options, -m a module, -W, -X and -Q a value; every
# other switch is a flag, and --check-hash-based-pycs is the one long option whose value is the next word.
PYTHON = Interpreter(code="c", value="WXQ", value_long=("--check-hash-based-pycs",), module="m")
# node(1): -e/--eval and -p/--print carry the program (`node -pe 'x'` clusters them), -r/--require a module to load
# first.  bun and deno spell the same two, and node's -c is --check, a flag, which is why it is no code letter here.
JS = Interpreter(code="ep", code_long=("--eval", "--print"), value="r", value_long=("--require",))
# deno's own help: its program comes after a subcommand, and `deno eval <code>` carries none of node's letters at all
# (`deno completions zsh`: `*::code_arg -- Code to evaluate`).  `deno repl --eval <code>`/`--eval=<code>` evaluates code
# when the REPL starts (`deno repl --help`) and the REPL itself reads what it is given; `deno run -` reads the program on
# standard input ("Specifying the filename '-' to read the file from stdin", `deno run --help`), and so may any other
# subcommand that takes a script, which is why each is tabled rather than left to be read as a file's name.  A word the
# table does not name is the file or task deno runs (`deno main.ts`), read as a program from a file exactly as before.
# node's own letters stay on the row: `deno -e 'x'` was refused from the first and stays refused.
DENO = Interpreter(code="ep", code_long=("--eval", "--print"), value="r", value_long=("--require",),
                   subcommands={"eval": "code", "repl": "stdin", "run": "file", "serve": "file", "watch": "file",
                                "task": "shell", "test": "file", "bench": "file", "check": "file", "compile": "file"})
# perlrun: -e and -E carry the program, -I a directory, -C, -D, -F, -M, -m, -V, -x and -i the rest of their own word
# (-d only before a `:` or `=`, read below), -0 and -l an optional number the cluster goes on after.
PERL = Interpreter(code="eE", value="I", attached="CDFMVimx", digits="0l")
# ruby(1): -e carries the program, -C, -E, -F, -I and -r take a value, -0, -K, -T, -W, -i and -x the rest of their word.
RUBY = Interpreter(code="e", value="CEFIr", attached="Kix", digits="0TW")
# osascript(1): `osascript [-l language] [-i] [-s flags] [-e statement | programfile] [argument ...]`.  -e enters one line
# of a script and more than one builds it up, -l and -s take a value, -i is a flag; with no -e and no programfile the
# script is "passed in using standard input", and `-` names that input where arguments follow it.  An AppleScript runs
# `do shell script "..."`, which is any shell command at all, so an osascript the hook cannot read is a shell it cannot
# read: this row is the one that matters most on a Mac.
OSASCRIPT = Interpreter(code="e", value="ls")
# php(1) (`php --help`): -r runs code without script tags, and -B, -R and -E run code before, for and after each input
# line; -c, -d, -z, -f and -F take a value (an ini path, a define, a Zend extension, a script file).  With no -r and no
# file php reads the script on standard input.  The long spellings are php_cli's own names for the same switches.  php is
# not installed on this Mac, so this row rests on its manual alone, as shell/syntax's wget row does.
PHP = Interpreter(code="rBRE", code_long=("--run", "--process-begin", "--process-code", "--process-end"),
                  value="cdfFz", value_long=("--php-ini", "--define", "--zend-extension", "--file", "--process-file"))
# lua(1): `lua [options] [script [args]]`, -e executes a string, -l requires a library (a value), -i, -v, -E and -W are
# flags, `-` executes standard input, and with no options and no arguments lua behaves as `lua -i` at a terminal and as
# `lua -` otherwise -- python's shape exactly.  Not installed here; lua(1) alone.
LUA = Interpreter(code="e", value="l")
# Rscript, from R's own usage (`Rscript [options] [-e expr [-e expr2 ...] | file] [args]`): -e executes an expression and
# may be given more than once; every other option is `--name` or `--name=value`, none of them a program.  Rscript with no
# file and no -e prints its usage and runs nothing, so it is no python: only the `-` operand is standard input.  Not
# installed here; R's manual alone.
RSCRIPT = Interpreter(code="e", stdin_program=False)
# `swift --help`: `-e <value>` "Executes a line of code provided on the command line", and `swift repl` is the
# interactive one.  Probed 2026-09-20: `swift - < s.swift` ran the file's program, while bare `swift` with a program on
# its standard input printed the driver's help and ran nothing -- so standard input is a program only where the line
# names it, `-` or the REPL.  The driver's options are whole `-name value` words, hundreds of them, and a cluster reading
# would find `e` inside `-access-notes-path`: whole_options reads each word as one option instead.
SWIFT = Interpreter(code="e", subcommands={"repl": "stdin"}, whole_options=True)
# The families a base name alone names, ahead of the four regular expressions and syntax.JS_RUNTIMES below: deno, whose
# subcommands are its grammar, and the rows beyond python, node, perl and ruby.  tsx and ts-node run node and pass its options through, so
# they read node's row -- `-e`/`--eval`, `-p`/`--print`, standard input -- and their own long options (`--project`,
# `--compilerOptions`) carry no program.  The names are as the dispatch casefolds them (`Rscript` reaches this as
# `rscript`).
BASES = {"deno": DENO, "osascript": OSASCRIPT, "php": PHP, "lua": LUA, "rscript": RSCRIPT, "swift": SWIFT,
         "tsx": JS, "ts-node": JS}


def interpreter(base):
    """The table entry for a command's base name, or None when it is no interpreter this module reads."""
    kind = BASES.get(base)
    if kind is not None:
        return kind
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
    for a program an option's value holds or a subcommand's operand is (`deno eval <code>`, whose subcommand
    is what the refusal names), ("stdin", the operand that names standard input or None) for one the interpreter reads
    there, or (None, None) for a program from a file, a module, or a command outside the table.

    The words are the simple command's own, its redirections taken out and its prefixes and wrappers read (analyse's
    dispatch), so words[0] is the interpreter and the options follow."""
    kind = interpreter(base)
    if kind is None:
        return None, None
    for _i, role, spelled in read_words(kind, words):
        if role in ("option", "stdin"):
            return role, spelled
        if role in ("program", "module"):
            return None, None  # a program from a file, which the hook reads no more of than it ever did
    return None, None


def read_words(kind, words):
    """Walk an interpreter run's words as its own option grammar reads them, yielding (the index of each word the run
    reads by name, what the word is there, the option as spelled):

    - (i, "word", None) for an option the scan passes over, an option's value, a subcommand, or `--`;
    - (i, "program", the word) for the file the run's program comes from, after which no word is read by name: its own
      arguments are the program's, whatever they look like;
    - (None, "option", the option) for an option whose value is the program, or a subcommand whose operand is
      the program, the option's own word having been yielded before it;
    - (None, "stdin", the operand or None) for a program the interpreter reads on standard input, named by a `-`
      operand or by the run having no program of its own at all;
    - (None, "module", the option) for python's `-m`, whose module the analysis reads elsewhere.

    The walk stops at the first of those four verdicts.  `--` ends the options, and so does the first word that is not
    one: it is the program's file, whatever it holds.  spelled_program reads the verdict; interpreter_words.read_index
    reads the indices, so one grammar answers both."""
    if kind.whole_options:
        yield from _driver_words(kind, words)
        return
    i, options, sub, code_operand, stdin_at_end = 1, True, None, False, kind.stdin_program
    while i < len(words):
        w = prepare.deglob(words[i])
        if code_operand and (not options or not w.startswith("-")):
            yield i, "option", sub  # the subcommand's first operand is the code (`deno eval 'x'`)
            return
        if options and w == "--":
            yield i, "word", None
            options, i = False, i + 1  # the word after it is the program's file, whatever it looks like
            continue
        if w in stdin_text.STDIN_OPERANDS:  # `-`, /dev/stdin, /dev/fd/0: the program is what the line puts there
            yield i, "word", None
            yield None, "stdin", w
            return
        if not options or not w.startswith("-"):
            mode = kind.subcommands.get(w) if sub is None else None
            if mode is None or mode == "shell":
                # a file's program, or a shell's text (`deno task --eval`), which shell/runtime_shells reads
                yield i, "program", w
                return
            # this family runs its program under a subcommand, which says where that program comes from
            sub, code_operand, stdin_at_end = w, mode == "code", mode == "stdin"
            yield i, "word", None
            i += 1
            continue
        role, option, skip = _long(w, kind) if w.startswith("--") else _cluster(w, kind)
        yield i, "word", None
        if role is not None:
            yield None, role, option
            return
        if skip > 1 and i + 1 < len(words):
            yield i + 1, "word", None  # the option's value, a word the scan reads by name and passes over
        i += skip
    if stdin_at_end:
        yield None, "stdin", None  # no program of its own: it runs what it reads on standard input


def _driver_words(kind, words):
    """read_words for a compiler driver, whose short options are whole `-name value` words rather than getopt clusters
    (`swift --help`).  Every word is read for an option, and no word ends the walk: the hook cannot tell an option's
    value from an operand among hundreds of `-name value` options, so it neither reads a value as an option nor stops
    at what may be one -- and a driver's script operand is a file either way."""
    codes = frozenset(["-" + c for c in kind.code] + list(kind.code_long))
    stdin_at_end = False
    for i in range(1, len(words)):
        w = prepare.deglob(words[i])
        yield i, "word", None
        if w in codes:
            yield None, "option", w
            return
        if w in stdin_text.STDIN_OPERANDS:
            yield None, "stdin", w
            return
        stdin_at_end = stdin_at_end or kind.subcommands.get(w) == "stdin"
    if stdin_at_end:
        yield None, "stdin", None


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
