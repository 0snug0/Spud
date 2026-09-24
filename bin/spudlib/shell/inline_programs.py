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
spells rather than reads from a file is refused where that program's text visibly writes (SPD-175).  Spelled means an
option that carries the program -- python's `-c`, node's (bun's, deno's) `-e`/`--eval`/`-p`/`--print`, perl's
`-e`/`-E`, ruby's `-e` -- or standard input, where the line feeds it and the interpreter has no program of its own
(`python3.14 - <<'PY'`, `echo ... | node`, `python3 < x.py`, `cat x | perl`).  A third shape is a subcommand whose own
operand is the program (`deno eval <code>`).  SPD-150 refused every such run, because the hook read none of them; since
SPD-175 the text the line spells -- the option's value and every later option that carries more, or each shell's
reading of what stands on standard input -- is scanned for the write markers shell/program_writes tables per family,
and perl's and ruby's `-i` among the options is one.  A marker found: refused.  None: the program runs.  Text the line
does not spell (a pipe from a file or a program, a `<` file, a word the line cannot settle, including one perl or ruby
reads as a switch past the program) stays refused, since there is nothing to scan, and so does every program of a
family no marker is tabled for (osascript, php, lua, Rscript, swift: the `markers` column below).

What stays exactly as it was: a program from a file (`python3.14 -I -S tests/suite.py`, the spud launcher,
`node scripts/build-web.js`), python's `-m module`, and an interpreter left to read a terminal, which runs no program of
the line's (`python3` on its own, the REPL).  Whether a member may run a script file it wrote itself is an open question,
which nothing here touches.  Spud keeps his inline programs: the hook cannot read those either, but his laws bind him
where it cannot see, and his are investigation rather than a way past a fence.

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
  `pnpm run`, `yarn <script>` and `make <target>`, whose command comes from package.json, deno.json or a makefile.  That
  is SPD-145's class (Eric's call: a member's shell whose commands come from a file is refused, with a project allow-list,
  shell/script_files), not this module's; since SPD-168 shell/script_runners refuses a member such a run unless its
  project allows every name it runs (`project edit --allow-runner`), from a file outside the member's reach.
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
where an option ends and a program begins; since SPD-175 the same walk, taken again past each value, also says which
words are the program's text, which is why that reading lives here and only the families' write markers, a body of
data with no grammar in it, went to shell/program_writes.  The seam that was real came out: the words such a run is read with, one
the line cannot settle and one an xargs appends, is shell/interpreter_words, with its own name and its own users.
"""

import re

from . import prepare, program_writes, stdin_text, syntax

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
    survive).

    For the text of the program itself (SPD-175):
    `markers`: the family shell/program_writes scans the program's text with, or None for a family no write marker is
    tabled for, whose every spelled program stays refused.
    `code_ends`: whether the option that carries the program ends the options, so the words after its value are the
    program's arguments and never more of its text (python's `-c`); elsewhere a later option may carry more text
    (`perl -e a -e b`) and every one is read.
    `inplace`: the short letters that edit the files the run names in place (perl's and ruby's `-i`), a write marker
    wherever they stand among the options."""

    __slots__ = ("code", "code_long", "value", "attached", "digits", "value_long", "module",
                 "subcommands", "stdin_program", "whole_options", "markers", "code_ends", "inplace")

    def __init__(self, code="", code_long=(), value="", attached="", digits="", value_long=(), module="",
                 subcommands=None, stdin_program=True, whole_options=False, markers=None, code_ends=False, inplace=""):
        self.code, self.code_long, self.value = code, code_long, value
        self.attached, self.digits, self.value_long, self.module = attached, digits, value_long, module
        self.subcommands, self.stdin_program, self.whole_options = subcommands or {}, stdin_program, whole_options
        self.markers, self.code_ends, self.inplace = markers, code_ends, inplace


# python(1): -c takes the rest of its word or the next one and ends the options, -m a module, -W, -X and -Q a value; every
# other switch is a flag, and --check-hash-based-pycs is the one long option whose value is the next word.
PYTHON = Interpreter(code="c", value="WXQ", value_long=("--check-hash-based-pycs",), module="m", markers="python",
                     code_ends=True)
# node(1): -e/--eval and -p/--print carry the program (`node -pe 'x'` clusters them), -r/--require a module to load
# first.  bun and deno spell the same two, and node's -c is --check, a flag, which is why it is no code letter here.
JS = Interpreter(code="ep", code_long=("--eval", "--print"), value="r", value_long=("--require",), markers="js")
# deno's own help: its program comes after a subcommand, and `deno eval <code>` carries none of node's letters at all
# (`deno completions zsh`: `*::code_arg -- Code to evaluate`).  `deno repl --eval <code>`/`--eval=<code>` evaluates code
# when the REPL starts (`deno repl --help`) and the REPL itself reads what it is given; `deno run -` reads the program on
# standard input ("Specifying the filename '-' to read the file from stdin", `deno run --help`), and so may any other
# subcommand that takes a script, which is why each is tabled rather than left to be read as a file's name.  A word the
# table does not name is the file or task deno runs (`deno main.ts`), read as a program from a file exactly as before.
# node's own letters stay on the row: `deno -e 'x'` was refused from the first and stays refused.
DENO = Interpreter(code="ep", code_long=("--eval", "--print"), value="r", value_long=("--require",),
                   subcommands={"eval": "code", "repl": "stdin", "run": "file", "serve": "file", "watch": "file",
                                "task": "shell", "test": "file", "bench": "file", "check": "file", "compile": "file"},
                   markers="js")
# perlrun: -e and -E carry the program, -I a directory, -C, -D, -F, -M, -m, -V, -x and -i the rest of their own word
# (-d only before a `:` or `=`, read below), -0 and -l an optional number the cluster goes on after.
PERL = Interpreter(code="eE", value="I", attached="CDFMVimx", digits="0l", markers="perl", inplace="i")
# ruby(1): -e carries the program, -C, -E, -F, -I and -r take a value, -0, -K, -T, -W, -i and -x the rest of their word.
RUBY = Interpreter(code="e", value="CEFIr", attached="Kix", digits="0TW", markers="ruby", inplace="i")
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
    """Record an interpreter run whose program the line spells (module docstring) where a member is refused it, which
    bash_rule turns into the refusal: (the command word as spelled, the option that carries the program or None where
    the interpreter reads it on standard input, why, the write marker or None).  Why is "writes" for text that shows a
    write marker (shell/program_writes, SPD-175), "unspelled" for text the line does not spell, and "untabled" for a
    family no marker is tabled for.  A program whose spelled text shows no marker records nothing, and runs.

    `fed` is whether the line puts anything on that standard input at all (shell/stdin_text.input_fed), which is not
    whether the hook can say what it is: `cat x | node` and `python3 < f` feed a program the hook cannot read, while
    `python3` on its own is the REPL and runs none.  What stands there is a.stdin, the text the line spells or None
    (stdin_text.command_input).  A command outside the table, a program from a file and a module record nothing."""
    kind = interpreter(base)
    if kind is None:
        return
    how, option, at, seen = _reach(kind, words)
    if how != "option" and not (how == "stdin" and fed):
        return
    why, marker = _verdict(kind, words, how, option, at, seen, a)
    if why is not None:
        a.findings.append(("inline", (prepare.deglob(cmd), option if how == "option" else None, why, marker)))


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
    how, option, _at, _seen = _reach(kind, words)
    return how, option


def _reach(kind, words):
    """spelled_program's walk, with where it ended: (how, the option, the index of the word that carries the program --
    the option's own word, or the subcommand's operand -- or None, and the indices of every word the walk read by
    name on the way).  how is None for a program from a file or a module, which the hook reads no more of than it ever
    did."""
    last, seen = None, []
    for i, role, spelled in read_words(kind, words):
        if role == "option":
            return role, spelled, (i if i is not None else last), seen
        if role == "stdin":
            return role, spelled, None, seen
        if role in ("program", "module"):
            return None, None, None, seen + [i]
        last = i
        seen.append(i)
    return None, None, None, seen


def _verdict(kind, words, how, option, at, seen, a):
    """(why a member is refused this run, the marker) or (None, None) where the text the line spells shows no write.

    The text: for an option, the word that carries the program and its value, and -- where that option does not end
    the options (code_ends) -- every later option that carries more, the walk read again past each value; for standard
    input, each shell's reading of what the line puts there (stdin_text.each_reading).  A word the walk reads by name
    that may stand for an option the line does not spell earns "unspelled", as the text itself does."""
    if kind.markers is None:
        return "untabled", None
    if how == "stdin":
        texts = None if stdin_text.unspelled(a.stdin) else stdin_text.each_reading(a.stdin)
    else:
        texts, more = _option_texts(kind, words, option, at, a)
        seen = seen + more
    for i in seen:
        if i is not None and _inplace(prepare.deglob(words[i]), kind):
            return "writes", "-" + kind.inplace
    if texts is None:
        return "unspelled", None
    for text in texts:
        marker = program_writes.first_write(kind.markers, text)
        if marker is not None:
            return "writes", marker
    return None, None


def _option_texts(kind, words, option, at, a):
    """(the program's text as one reading, or None where a word of it is one the line does not spell; the indices of the
    words the walks after the first read by name).  `option` and `at` are where the first walk stopped."""
    texts, seen = [], []
    while True:
        operand = not option.startswith("-")  # a subcommand's operand is the program (`deno eval <code>`)
        attached = operand or _attached(prepare.deglob(words[at]), option, kind)
        taken = words[at:at + (1 if attached else 2)]
        texts.extend(stdin_text.word_text(w, a) for w in taken)
        after = at + len(taken)
        if operand or kind.code_ends:
            break
        rest = [words[0]] + words[after:]
        how, option, j, more = _reach(kind, rest)
        seen.extend(after + k - 1 for k in more if k is not None)
        if any(k is not None and not _readable(rest[k], a) for k in more):
            return None, seen
        if how != "option":
            break
        at = after + j - 1
    return (None if None in texts else ["\n".join(texts)]), seen


def _attached(word, option, kind):
    """Whether the option in this word carries its value in the word itself (`-c'x'`, `-ecode`, `--eval=x`), rather
    than in the next one.  A rest of the cluster made only of code letters is more options, as node reads `-pe`: -p
    and -e, the program in the next word."""
    if word.startswith("--") or kind.whole_options:
        return "=" in word
    rest = word[word.find(option[1:], 1) + 1:]
    return bool(rest) and not all(c in kind.code for c in rest)


def _readable(word, a):
    """Whether a word the walk reads by name is one it can read: text the line spells, or a word whose every expansion
    starts with the character the line spells at its front -- a letter, a digit or a path's -- and so is never an option
    (`tests/*.txt`, `~/x`, `docs/$NAME`)."""
    if stdin_text.word_text(word, a) is not None:
        return True
    text = prepare.deglob(word)
    return bool(text) and (text[0].isalnum() or text[0] in "./~+,:@%")


def _inplace(word, kind):
    """Whether this option word, as the family's getopt reads it, holds a letter that edits files in place (-i)."""
    if not kind.inplace or not word.startswith("-") or word.startswith("--"):
        return False
    k = 1
    while k < len(word):
        c = word[k]
        if c in kind.inplace:
            return True
        if c in kind.code or c in kind.module or c in kind.attached or c in kind.value:
            return False
        k += 1
        if c in kind.digits:
            while k < len(word) and word[k].isdigit():
                k += 1
    return False


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
