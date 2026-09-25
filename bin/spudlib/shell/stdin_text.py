"""shell/stdin_text: the commands a line feeds a shell on standard input.

Before this module the analysis read two bodies a shell runs as commands: its `-c` string, and a here-document fed to the
shell itself (`zsh <<EOF ... EOF`).  A shell started with neither runs what it reads on standard input, and every line
that spelled those commands somewhere else -- `echo 'git push' | sh`, `printf ... | bash -s`, `bash -s <<< '...'`,
`cat <<'EOF' | sh`, `echo ... | xargs -0 sh -c` -- was silent for a member: Laws 1, 5, 6 and 7 all read nothing there.

Spud probed the shells for this, a member in a worktree being unable to run one, with an executable `vcs` on
PATH logging its arguments and each line run by /bin/bash -c from a scratch directory:

- a shell with no -c and no script operand reads its commands from standard input and ran them: `echo 'vcs a' | sh`,
  `| bash`, `| zsh -f`, `| dash`, `| ksh`; with options first, `sh -s x` (x is $1), `bash -`, `zsh -f -`, `sh -e`,
  `bash --norc`, `sh -o errexit`, `sh -x`, `sh --` and `bash -i`; and `sh /dev/stdin`, whose script operand is that input.
- `sh -c 'cat >/dev/null; vcs c'` ran c alone: the -c string is what runs, and its own commands may read the input as
  data.  `sh script-that-does-not-exist` ran nothing.
- what the line spells: `printf 'vcs a\\nvcs b' | sh` ran both (printf decodes the escapes in its format),
  `printf '%s\\n' 'vcs a' | sh` ran a, `echo -e 'vcs a\\nvcs b' | bash` ran both, and `echo 'vcs a\\nvcs b' | sh` ran one
  command under bash's echo, which keeps the backslash, and two under zsh's, which decodes it -- the Bash tool's shell
  here.  The escapes are decoded in this reading, the one that finds more (the analysis reads a line as both zsh and
  bash for the same reason).
- here-strings: `bash -s <<< 'vcs a'`, `zsh -f <<< 'vcs a'` and `sh <<< 'vcs a'` ran a.
- through other shapes: `cat <<'EOF' | sh` (a here-document through cat), `{ echo 'vcs a'; echo 'vcs b'; } | sh`,
  `(echo 'vcs a') | sh`, `echo 'vcs a' | tee /dev/null | sh`, `echo 'vcs a' | env sh` and `| nohup sh` each ran what the
  element before the shell printed.
- xargs: `echo 'vcs a' | xargs -0 sh -c` ran `vcs a`, the whole input being the -c string; `echo 'vcs a' | xargs sh -c`
  ran `vcs` with $0 set to a, its first word being the string.

A word the printers are passed, and a here-string's word, is read through the value the line settled
(arg_writes.resolved, SPD-148): `X='git push'; echo $X | sh` feeds the shell what `echo 'git push' | sh` does.  The
masked words tell `"$X"` from `$X` (syntax._QUOTED_NAME, SPD-167): a quoted value is one word in both shells, an unquoted
one is passed whole by zsh and split at its blanks by bash, so the printer is read both ways and the text a shell reading
it runs is what either prints (printed_text, _string_text, _either).

What a command prints reaches a pipe after its own output redirections as each shell sends it (SPD-214,
redirected_text): bash where the last of them points, and zsh, whose MULTIOS joins them to a pipe that follows the
command, into the pipe as well -- `{ echo '...'; } > /dev/null | sh` runs the text in zsh alone.  A compound command's
redirections after its closer are read so too (walk.ShellWalk.finish), where they once stood for a command of their own
that printed nothing the hook could spell.  So are the commands that print nothing at all, a condition's `true` or
`test` and a loop's `break` (SPD-273, printed_text), which once left a compound's whole text unread.  A call of a
function the line defines prints what the call's reading of its body prints, from the state and on the input the call
has (SPD-272, LineCall, line_functions.read_call), and a printer's name the shell runs another body for -- an alias, a
function of the snapshot's or one whose name the hook cannot read -- prints text this module does not follow (_shadowed).

A command's input redirections are read as each shell feeds them (SPD-209, command_input): zsh reads every one of them
in turn, after the pipe into the command, and bash the last alone, so where the two differ the text is a MultiosText
holding both, and a shell fed it reads each.

That input reaches the commands a command runs inside itself wherever nothing of theirs replaces it (SPD-210): a `-c`
string's commands and eval's start from their command's (analyse.analyse_command's `stdin`), the commands in a compound
command from its own input redirections, read as a simple command's are (walk.walk_line), and a command substitution
from the input of the list it stands in (walk.ShellWalk.substitution_input).  So `sh -c sh < f`, `{ sh; } < f` and
`(sh) <<'EOF'` read as `sh < f` and `sh <<'EOF'` do.  A function the line defines runs its body on each call's input
(SPD-212, walk.walk_line): `f() { sh; }; f < x` reads as `sh < x` does.

What stays unread: standard input the line does not spell -- a file (`sh < f`), another program's output (`cat f | sh`,
`curl ... | sh`), a value the line does not settle, a command substitution's output or such a value in an unquoted
here-document's body (heredocs.OutputBody, SPD-207 and SPD-208), or text this module cannot decode in either reading.  That is the same class as `sh script.sh`, a script the hook does
not read either.  Eric's call on SPD-145 (fail closed): a member is refused both, a shell reading standard input the
line does not spell -- in either shell's reading of it -- and a shell given a script file (script_operand below), by
shell/script_files; Spud is not.

Kept whole past 250 lines (the package's look-again point): it answers one question -- what text stands on a
command's standard input and standard output -- and the two halves are the same reading from either end.  `input_fed`
says whether the line puts anything there at all, which is the same reading of the same redirections, and
MultiosText with its helpers is the text both halves carry where zsh and bash read it apart.  What the
printers decode is the table `analyse` and `walk` reach it for; splitting the escapes off would leave a module no caller
names.
"""

import os
import re

from . import (alias_views, arg_writes, directories, expansions, globbing, held_text, heredocs, line_aliases, line_functions,
               prepare, syntax)
from ..hooks import snapshots

# The operands that name the standard input the line gave the shell rather than a script file of its own (probed:
# `bash /dev/stdin <<< 'vcs a'` and `bash - <<< 'vcs a'` both ran it).
STDIN_OPERANDS = frozenset({"-", "/dev/stdin", "/dev/fd/0"})
# bash's options whose value is the next word (bash(1)); every other option of these shells takes none or carries its
# value in the same word, and `-o`, `+o` and `-O` are read with the short options below.
_VALUE_OPTIONS = frozenset({"--rcfile", "--init-file"})
_FD_RE = re.compile(r"\d+\Z")
_ECHO_OPTIONS_RE = re.compile(r"-[neE]+\Z")  # echo(1): -n no newline, -e decode the escapes, -E leave them
_PRINT_OPTIONS = "rRnl"  # zsh's print: -r and -R raw, -n no newline, -l one argument a line
# What echo, zsh's print, and printf in its format and in a `%b` argument decode (echo(1), printf(1), zshbuiltins(1)).
_ESCAPES = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\"}
_ESCAPE_RE = re.compile(r"\\(c|0[0-7]{0,3}|[0-7]{1,3}|x[0-9A-Fa-f]{1,2}|.)", re.S)
# The xargs options that hand the utility whole records rather than blank-separated words, so one input is one operand
# (xargs(1): -0/--null, -d/--delimiter).
_WHOLE_INPUT_OPTIONS = ("--null", "--delimiter")
# Where bash's reading of an unquoted expansion splits its value, in word_fields: no shell variable can hold a NUL.
_FIELD = "\0"
_PRINTERS = frozenset({"echo", "print", "printf", "cat", "tee"})
# The commands that print nothing on standard output whatever their words (SPD-273): the conditions and the loop and
# function controls, by their own names alone (a path is a program, whose options may print).  Probed through
# tests/probes/shell_probe.py in zsh 5.9 -f and -f -o nobareglobqual and bash 3.2.57 (2026-09-24,
# tests/test_hooks_input.py SilentCommandOutputTest): each printed no byte there, alone, with `--help` and with `-v x`.
_SILENT = frozenset({"true", "false", ":", "test", "[", "[[", "break", "continue", "return", "exit"})
# The shell's own among the printers and the silent commands, which `builtin NAME` and `command NAME` run whatever
# function or alias shares the name (SPD-272); cat and tee are programs, which `command` finds on PATH, and `[[` a word
# of the grammar, which neither runs.
_BUILTINS = frozenset({"echo", "print", "printf"}) | (_SILENT - {"[["})
_PROGRAMS = frozenset({"cat", "tee"})
# What the words of an arithmetic command, `(( ... ))`, open with as the walk hands them on
_ARITHMETIC_COMMAND = syntax._ARITH_SENTINELS["("]
# LineCall.own for a call no command of the function's name can answer: a definition surely ran before it (SPD-272)
_CERTAIN = object()


class MultiosText(str):
    """The text on a command's standard input where zsh and bash read it apart (SPD-209, command_input): the str is
    bash's reading -- the last input redirection's text, which sh and bash feed the command alone -- and `zsh` zsh's,
    every input in turn under its MULTIOS option, None where one of them is text the line does not spell.  bash's is
    never None where zsh's is text, zsh's holding bash's with the rest, so where either shell reads text the line spells
    the str is text.  A reader that takes it as a str reads bash's; joined keeps both, and each_reading gives a shell
    each."""

    def __new__(cls, text, zsh_text):
        self = super().__new__(cls, text)
        self.zsh = zsh_text
        return self


def _zsh(text):
    """zsh's reading of a text: a MultiosText's own, and any other text as it stands."""
    return text.zsh if isinstance(text, MultiosText) else text


def _paired(bash, zsh_text):
    """One text for bash's reading and zsh's: None where bash's is None, the text where they agree, a MultiosText where
    they differ."""
    if bash is None or zsh_text == bash:
        return bash
    return MultiosText(bash, zsh_text)


def each_reading(text):
    """The texts a shell fed `text` may read, each a plain str: none for None, and a MultiosText's zsh's, where it is
    text, then bash's."""
    if text is None:
        return []
    if isinstance(text, MultiosText) and text.zsh is not None:
        return [text.zsh, str(text)]
    return [str(text)]


def unspelled(text):
    """Whether either shell's reading of this standard input is text the line does not spell."""
    return text is None or isinstance(text, MultiosText) and text.zsh is None


def reading_key(text, fed):
    """The standard input a body is analysed with, as a key (analyse.analyse_isolated, SPD-210): each shell's reading of
    the text, whether either is text the line does not spell, and whether anything stands there at all."""
    return tuple(each_reading(text)), unspelled(text), fed


def single(text):
    """The one text both shells read, or None where they read apart or the line does not spell it: for a reader that
    takes its input whole, as xargs builds its command string from it."""
    return None if isinstance(text, MultiosText) else text


def joined(before, text):
    """The text two commands print one after the other; None, text the hook cannot spell, absorbs.  Each shell's reading
    joins its own (MultiosText)."""
    if before is None or text is None:
        return None
    if not (isinstance(before, MultiosText) or isinstance(text, MultiosText)):
        return before + text
    zsh_before, zsh_text = _zsh(before), _zsh(text)
    return _paired(before + text, None if zsh_before is None or zsh_text is None else zsh_before + zsh_text)


def word_text(word, a=None):
    """The text this masked word stands for, or None where the hook cannot say it: an expansion, a substitution, a glob
    the shell expands, or an operand the line does not spell.  With the line's analysis `a`, a `$NAME` the line settled
    stands for its value, whole (SPD-148): zsh's reading, which never splits an expansion, and bash's of a quoted one."""
    if a is not None:
        word = arg_writes.resolved(word, a, _whole)
    if expansions.expansion_word(word) or globbing.active_glob_word(word) or syntax.unknown_operand(word):
        return None
    return prepare.deglob(word)


def word_fields(word, a):
    """The words bash makes of this masked word: the settled value of each unquoted expansion split at its runs of
    blanks, the default IFS, and an empty field at either end dropped (SPD-148); a quoted one's (`"$X"`, SPD-167) kept
    whole, as zsh keeps both.  [None] where word_text cannot say it."""
    text = word_text(arg_writes.resolved(word, a, _split))
    if text is None:
        return [None]
    return [f for f in text.split(_FIELD) if f] if _FIELD in text else [text]


def _whole(value, _quoted):
    return value


def _split(value, quoted):
    return value if quoted else syntax._IFS_BLANKS_RE.sub(_FIELD, value)


def _readings(words, a):
    """The words a command is passed as zsh reads them and as bash reads them -- each a list of texts, None for a word the
    hook cannot say.  They differ only where an unquoted expansion's settled value holds a blank."""
    return [word_text(w, a) for w in words], [f for w in words for f in word_fields(w, a)]


def _either(zsh_text, bash_text):
    """The text the two readings print, as one text a shell reading it runs: the one they agree on, or both one after the
    other where they print apart (SPD-167), so every command either shell would run is read -- `X='-n git push'; echo $X`
    is `git push` in bash alone.  None where either cannot be spelled: the fail-closed reading of what the hook cannot say."""
    if zsh_text is None or bash_text is None or zsh_text == bash_text:
        return None if bash_text is None else zsh_text
    return zsh_text + ("" if zsh_text.endswith("\n") else "\n") + bash_text


def command_input(tokens, bodies, piped, pipe_feeds, a=None):
    """The text the simple command `tokens` reads on standard input, or None where the line does not spell it.

    `tokens`: the command's words with its redirections still in them, each here-document's operator and delimiter
    among them, whose bodies ShellWalk.consume took out (`bodies`, in the order the operators stand; an unquoted one
    as its expansion leaves it, heredocs.received_body, SPD-206).  `piped`: what stands on standard input before the
    command's own redirections -- the text the pipeline element before it printed, or the input the compound command
    around it was given -- and `pipe_feeds`, whether a pipe feeds this command itself rather than a compound command
    around it.  `a`: the line's analysis, whose settled values a here-string's word is read with (SPD-148).

    Each redirection on descriptor 0 is one input: a here-document's body, a here-string whose word the hook can
    resolve, and a file (`<`, and `<>`, which opens it for reading and writing) or a descriptor (`<&`), which the line
    does not spell -- nor a word holding an expansion, a substitution or a glob, a here-string whose settled value the
    two shells do not read alike (_string_text), or a body holding a command substitution's output
    (heredocs.OutputBody, SPD-207).  zsh reads every one of them, one after another in the order they stand, after the
    pipe that feeds the command itself (its MULTIOS option, on by default: zshmisc(1) calls a pipe an implicit
    redirection), and bash reads the last alone; with none, both read `piped`.  Where the two differ the text is a
    MultiosText, bash's holding zsh's (SPD-209, probed through tests/probes/shell_probe.py in zsh 5.9 -f and -f -o
    nobareglobqual and bash 3.2.57: tests/test_hooks_input.py MultiosInputTest).  A pipe into a compound command is no input
    of a command inside it that has one of its own (probed: `printf 'touch p1\\n' | { sh <<'EOF' ...; }` ran the body
    alone)."""
    inputs, pending = [], iter(bodies)
    i = 0
    while i < len(tokens):
        t, fd = tokens[i], None
        if _FD_RE.fullmatch(t) and i + 1 < len(tokens) and tokens[i + 1] in (syntax.OUT_REDIRECTS | syntax.IN_REDIRECTS):
            fd, i, t = t, i + 1, tokens[i + 1]
        if t in ("<<", "<<-"):
            body = next(pending, None)  # consume takes a body for each operator while any is left
            if fd in (None, "0") and body is not None:
                inputs.append(None if isinstance(body, heredocs.OutputBody) else body + "\n")
            i += 2
            continue
        if t in syntax.IN_REDIRECTS or t == "<>":
            if fd in (None, "0"):
                spelled = _string_text(tokens[i + 1], a) if t == "<<<" and i + 1 < len(tokens) else None
                inputs.append(None if spelled is None else spelled + "\n")
            i += 2
            continue
        i += 2 if t in syntax.OUT_REDIRECTS else 1
    if not inputs:
        return piped
    zsh_inputs = ([_zsh(piped)] if pipe_feeds else []) + inputs
    return _paired(inputs[-1], None if None in zsh_inputs else "".join(zsh_inputs))


def _string_text(word, a):
    """A here-string's text, its word read with the line's settled values (`a`): whole, as zsh reads it, and as bash
    3.2 reads it -- its fields joined by a blank, an unquoted expansion's value split -- and both where the two differ
    (_either)."""
    whole = word_text(word, a)
    if a is None or whole is None or "$" not in word:
        return whole
    fields = word_fields(word, a)
    return _either(whole, None if None in fields else " ".join(fields))


def input_fed(tokens, bodies, piped):
    """Whether the line puts anything on this simple command's standard input at all, which is not whether
    command_input can say what it is: that answers None both for input the line does not spell and for none at all, and
    an interpreter with no program of its own runs whatever stands there (shell/inline_programs), so the two must be
    told apart -- `cat x | node` and `python3 < f` run a program, `python3` on its own is the REPL.

    Anything: a here-document body (`bodies`, which ShellWalk.consume has taken out of the words), a here-string, a
    `<` file, a `<&` descriptor, a `<>` opened read-write (bash's default descriptor for it is 0), or a pipeline element
    before this one (`piped`).  A redirection with a descriptor of its own before it feeds that descriptor, not
    standard input."""
    if bodies or piped:
        return True
    i = 0
    while i < len(tokens):
        t, fd = tokens[i], None
        if _FD_RE.fullmatch(t) and i + 1 < len(tokens) and tokens[i + 1] in (syntax.OUT_REDIRECTS | syntax.IN_REDIRECTS):
            fd, i, t = t, i + 1, tokens[i + 1]
        if t in syntax.IN_REDIRECTS or t == "<>":
            if fd in (None, "0"):
                return True
            i += 2
            continue
        i += 2 if t in syntax.OUT_REDIRECTS else 1
    return False


def reads_commands(words):
    """Whether this shell command runs the commands it reads on standard input: no `-c` string, and either `-s`, which
    forces it whatever the operands, or no script operand -- or an operand that is that same input, `-`, `/dev/stdin` or
    `/dev/fd/0` (probed).  An operand the hook cannot resolve is read as a script of its own, so a line whose own shell
    reads a file is refused a member as `sh script.sh` is (script_operand, shell/script_files)."""
    i, forced, dash_c, _files = _shell_options(words)
    if dash_c:
        return False  # its commands are the -c string, which the dispatch reads
    if forced or i >= len(words):
        return True
    return prepare.deglob(words[i]) in STDIN_OPERANDS


def script_operand(words):
    """The word this shell command reads as a script file of its own (masked, as the line spells it), or None: no `-c`
    string, no `-s`, and a first operand that is not the standard input the line gives it (reads_commands).  `sh x.sh`,
    `bash -e ./x.sh`, `zsh -- x.sh`; and `bash - x.sh`, whose lone `-` ends the options as `--` does."""
    i, forced, dash_c, _files = _shell_options(words)
    if dash_c or forced or i >= len(words) or prepare.deglob(words[i]) in STDIN_OPERANDS:
        return None
    return words[i]


def startup_files(words):
    """The files bash's `--rcfile` and `--init-file` name among this shell command's options, which an interactive bash
    runs before anything else (bash(1)): each is a file of commands, whatever else the line runs."""
    return _shell_options(words)[3]


def _shell_options(words):
    """(the index of the first operand, or len(words); whether `-s` forces standard input; whether a `-c` string gives the
    commands; the words `--rcfile` and `--init-file` name) for a shell command's words, read as bash(1), sh(1) and
    zsh(1) read their options."""
    i, forced, files = 1, False, []
    while i < len(words):
        w = prepare.deglob(words[i])
        if w in ("--", "-"):  # the end of the options, and on its own bash's standard input
            i += 1
            break
        if not w.startswith(("-", "+")) or len(w) == 1:
            break
        if w.startswith("--"):
            if w in _VALUE_OPTIONS:
                if i + 1 < len(words):
                    files.append(words[i + 1])
                i += 1
        else:
            letters = w[1:]
            if "c" in letters:
                return i, forced, True, files
            forced = forced or "s" in letters
            if letters[-1] in "oO":  # `sh -o errexit`, `bash -O globstar`: the next word is the option's value
                i += 1
        i += 1
    return i, forced, False, files


def printed_text(tokens, stdin, a=None, feeds_pipe=False):
    """The text the simple command `tokens` prints on standard output, or None where the hook cannot spell it.

    Only the commands that print what the line spells are read: echo, zsh's print, printf, a cat of its own input, and
    tee, which passes its input on whatever files it also writes; and the ones that print nothing (_SILENT, `(( ... ))`
    and a pipeline's `!` before any of these, SPD-273), so the text of a compound around them -- `if true; then echo
    ...; fi`, `while ...; do ...; break; done` -- is the text its printers print.  Every other command prints text this
    module does not know, and so does a name the shell runs an alias or another function for (_shadowed, SPD-272), unless
    `builtin` or `command` runs the shell's own (_command).  A call of a function the line defines, whatever its name,
    is a LineCall, whose text walk.ShellWalk.finish takes from the call's own reading of the body once it is read, with
    the command's own text beside it where no definition surely ran first.  A redirection of the command's standard
    output takes that text from where it would go, as redirected_text reads it (`feeds_pipe`: a pipe follows the
    command).  `stdin`: the text the command reads on standard input (command_input), which cat and tee print as it
    stands.

    `a`, the line's analysis before this command runs: a `$NAME` the line settled is read as its value (SPD-148), the
    value the shells expand it to, since the command's own prefix assignments reach none of its words.  bash splits an
    unquoted expansion's value at its blanks where zsh never does, and neither splits a quoted one (SPD-167), so the
    command is read both ways (_readings) and its text is what either prints (_either): `X='git push'; echo $X` prints
    `git push` in both, `printf '%s\\n' "$X"` too, and `X='-n git push'; echo $X` prints it in bash alone."""
    words = directories.separate_redirects(tokens)[0]
    while words and words[0] == "!":
        words = words[1:]  # `! cmd` prints what cmd prints (probed: `! echo hi` printed hi)
    if words and words[0].startswith(_ARITHMETIC_COMMAND):
        return redirected_text("", tokens, feeds_pipe)  # an arithmetic command prints nothing (probed: `(( 1 + 1 ))`)
    name = word_text(words[0]) if words else None
    bodies, certain = _line_bodies(name, a)
    if bodies:
        # a call of a function the line defines: the text its body prints where the call runs it (SPD-272), and, where no
        # definition surely ran before it or a removal may have run since (SPD-281), the text the command of that name
        # prints as well, either being what runs
        return LineCall(name, tokens, feeds_pipe, _CERTAIN if certain else _command_text(words, tokens, stdin, a, feeds_pipe, name))
    return _command_text(words, tokens, stdin, a, feeds_pipe)


def _command_text(words, tokens, stdin, a, feeds_pipe, called=None):
    """printed_text's reading of the command `words`, `tokens` with its redirections: the shell's own printer, a silent
    command, or a program by those names, or None for text it does not know.  `called`: a function the line defines
    under the command's name, which _shadowed leaves aside, since the call's own reading of its body stands beside this."""
    words, own = _command(words, a, called)
    if words is None:
        return None
    if a is None or not any("$" in w for w in words):
        name = word_text(words[0]) if words else None
        if not _reads(name, a, own, called):
            return None
        return redirected_text(_printed([name] + [word_text(w) for w in words[1:]], stdin), tokens, feeds_pipe)
    if not _reads(word_text(words[0], a), a, own, called):
        return None  # zsh's reading prints nothing this module reads, so bash's cannot agree with a text
    whole, fields = _readings(words, a)
    if not (fields and _reads(fields[0], a, own, called)):
        return None
    return redirected_text(_either(_printed(whole, stdin), _printed(fields, stdin)), tokens, feeds_pipe)


class LineCall:
    """printed_text's answer for a call of a function the line defines (SPD-272): the text is the one the call's reading of
    the body prints, which line_functions.read_call leaves in ShellAnalysis.call_printed once ShellWalk.finish has had the
    command read (analyse_segment), where it asks `output` for it.  `name`, the function's; `tokens` and `feeds_pipe`, the call's
    own redirections and whether a pipe follows it, as redirected_text reads them; `own`, _CERTAIN where a definition
    surely ran before the call, else the text the command of that name prints (None: text the hook cannot spell), which
    runs where none did."""

    __slots__ = ("name", "tokens", "feeds_pipe", "own")

    def __init__(self, name, tokens, feeds_pipe, own):
        self.name, self.tokens, self.feeds_pipe, self.own = name, tokens, feeds_pipe, own

    def output(self, called):
        """The text the call prints, from `called`, line_functions.read_call's (the name, the text where the call's output
        goes, the text where a pipe follows the call), or None where no call of this name was read: the body's text where a pipe
        follows the call is the one zsh joins the definition's own output redirections to (ShellWalk.body_piped), and the
        call's own redirections take from either as a command's do."""
        text = None
        if called is not None and called[0] == self.name:
            text = redirected_text(called[2] if self.feeds_pipe else called[1], self.tokens, self.feeds_pipe)
        return text if self.own is _CERTAIN else either_text([text, self.own])


def either_text(texts):
    """The text a shell reading any one of `texts` runs, where the hook cannot say which one is printed (SPD-272: the
    bodies of one name, or a body and the command of its name): the one text where they agree, else each after the other,
    as _either reads two shells' readings, so every command any of them would run is read.  None where one of them is text
    the hook cannot spell, and where they differ and one is a MultiosText, whose two readings a join would lose."""
    if not texts or None in texts:
        return None
    first = texts[0]
    if all(text == first and _zsh(text) == _zsh(first) for text in texts[1:]):
        return first
    if any(isinstance(text, MultiosText) for text in texts):
        return None
    joined_text = first
    for text in texts[1:]:
        if text != joined_text:
            joined_text = _either(joined_text, text)
    return joined_text


def _line_bodies(name, a):
    """(the bodies a call of `name` reads where the line defines it, whether one of them surely runs) -- line_functions.
    line_bodies, which a removal the line makes settles (SPD-281) -- when nothing else the shell could run under the name
    stands beside them (_shadowed's other shadows: a function whose name the hook cannot read, a hashed program, an alias,
    the snapshot's own); else none, and the name reads as a shadowed one does."""
    if a is None or not name:
        return [], False
    bodies, certain = line_functions.line_bodies(a, name)
    if not bodies or _shadowed(name, a, name):
        return [], False
    return bodies, certain


def _command(words, a, called=None):
    """(the words from the command's name on, the precommand that runs it as the shell's own or None) for printed_text,
    or (None, None) where that is not a command it reads.

    `builtin NAME` runs the shell's own NAME and `command NAME` its own or the program on PATH, whatever function or alias
    shares the name (probed through tests/probes/shell_probe.py in zsh 5.9 -f and -f -o nobareglobqual and bash 3.2.57,
    2026-09-24, tests/test_hooks_input.py PrinterShadowTest: with a function echo defined, `builtin echo`, `command echo`
    and `command -p echo` ran the echo); `command -v` and `-V` print what the name is instead, text of their own."""
    first = word_text(words[0]) if words else None
    if first not in ("builtin", "command"):
        return words, None
    if _shadowed(first, a, called):
        return None, None  # a function named builtin or command runs in its place (probed)
    rest = words[1:]
    while first == "command" and rest:
        option = word_text(rest[0])
        if option == "--":
            rest = rest[1:]
            break
        if option is None or not option.startswith("-") or len(option) == 1:
            break
        if option.strip("p") != "-":
            return None, None
        rest = rest[1:]
    return (rest, first) if rest else (None, None)


def _reads(name, a, own, called=None):
    """Whether printed_text reads the command `name` as a printer or as a command that prints nothing: its own name, a
    printer's by a path too, which the shell runs as itself.  `own`: the precommand that runs it, `builtin` (the shell's
    own alone) or `command` (the shell's own or the program), or None, where a name the shell runs a function or an
    alias for is text of its own (_shadowed; `called`, a function of the line's it leaves aside)."""
    if not name or not (name in _SILENT or _printer(name)):
        return False
    if own == "builtin":
        return name in _BUILTINS
    if own == "command":
        return name in _BUILTINS or _printer(name) and not _hashed(name, a)
    return not _shadowed(name, a, called)


def _shadowed(name, a, called=None):
    """Whether the shell runs something other than its own `name` or the program on PATH for it (SPD-272): a function the
    line defines under that name, or one it cannot name (ShellAnalysis.functions: a definition, zsh's `functions` table),
    a program it hashed there (cat and tee alone, which the shell looks up), an alias of the line's where `eval` reads it
    again (line_aliases.alias_substitution), or an alias or a function the shell's snapshot defines (hooks/snapshots), an
    alias not while its own expansion is read.  The shell runs that body in place of the command, whose text this reading
    does not follow: unread, as input the line does not spell (probed through tests/probes/shell_probe.py in zsh 5.9 -f
    and -f -o nobareglobqual and bash 3.2.57, 2026-09-24, tests/test_hooks_input.py PrinterShadowTest: `echo() { printf
    ...; }; echo hi | sh` ran the function's text, and so did a function named printf, print, cat, tee, true, `:`, test,
    builtin or command, one defined inside an `if` too, while one defined in a subshell did not reach a call after it).
    A function the line defines is read for the text its body prints where a call runs it (LineCall, SPD-272), so
    `called` names one this leaves aside: the command's reading beside the call's, or whether anything else shadows it.

    SPD-300: a snapshot alias only where the snapshot's aliases stand (alias_views.held_standing, SPD-290): not in a
    function body the snapshot defines before its aliases, nor in a new shell's text (probed in zsh 5.9 -f through
    tests/probes/shell_probe.py, a file of functions then `alias cat='echo CAT-ALIAS'` sourced: a body's `printf 'echo
    PIPED\\n' | cat | sh` printed PIPED, the real cat, where the same line outside the body ran CAT-ALIAS).

    SPD-303: a snapshot function only where the shell sourced the snapshot (held_text.snapshot_sourced, SPD-298): a new
    shell's text runs its own printer, so under a profile's `echo () { ...; }` `sh -c 'echo git status | sh'` pipes the
    builtin's text, read as the line spells it (tests/test_hooks_groups.py NewShellSnapshotLookupTest).

    SPD-307: a snapshot function only where line_functions.held_function still finds it standing -- "sure" or "maybe" --
    rather than merely present in the snapshot's table: a line's own `unset -f echo` (or `cat`) takes it, `sure` where the
    removal surely ran, so the shell reads its own name's text and this module follows it, `maybe` keeping today's
    cautious reading where the removal may not have (tests/test_hooks_groups.py SnapshotPrinterRemovalTest)."""
    if a is None:
        return False
    if name in a.functions and name != called or syntax.UNKNOWN_NAME in a.functions or _hashed(name, a):
        return True
    if a.alias_scope:
        body, doubtful = line_aliases.alias_substitution(name, a)
        if body is not None or doubtful:
            return True
    table = snapshots.shell_table(a.home)
    return bool(line_functions.held_function(a, name)) \
        or name in table.aliases and name not in a.expanding and alias_views.held_standing(a)


def _hashed(name, a):
    """Whether the line hashed this program's name to a file of its own (ShellAnalysis.hashed), which the shell then runs."""
    return a is not None and name in _PROGRAMS and (name in a.hashed or syntax.UNKNOWN_NAME in a.hashed)


def redirected_text(text, tokens, feeds_pipe=False):
    """What reaches the command's own standard output of the `text` it printed, after its redirections `tokens` -- a simple
    command's, or the words after a compound command's closer (SPD-214) -- or None where the hook cannot spell it.

    Only a redirection of standard output changes it: an output operator with no descriptor or with 1 before it, `&>`
    and `&>>`, and a `1<`, `1<&` or `1<>`; `2>`, `2>&1`, an input and a here-document leave the text where it was, and so
    does `>&1`.  bash sends the text where the last of them points: into a file, which leaves nothing here; nowhere, for
    `>&-`; or to another descriptor (`>&2`, a dup the hook cannot read), whose text it does not follow (None).  zsh's
    MULTIOS option, on by default, joins every such redirection to the pipe that follows the command itself, so there it
    reads the text whole, `>&-` apart; with no pipe after the command its reading is bash's.  Where the two differ the
    text is a MultiosText.

    Probed through tests/probes/shell_probe.py in zsh 5.9 -f and -f -o nobareglobqual and bash 3.2.57 (2026-09-24,
    tests/test_hooks_input.py CompoundOutputTest): `echo 'touch M1' > /dev/null | sh`, `{ ...; } > /dev/null | sh`,
    `&>`, `>>`, `>|`, `> a > b` and `>&2` before the pipe ran the text in zsh and not in bash; `2>/dev/null`, `2>&1`,
    `2>&-`, `<`, `<>`, `<&0`, `4>&1`, `0>&1` and `>&1` ran it in both; `>&-` and `1>&-` in neither; and in neither where
    the redirection stood on a command inside a compound the pipe follows, `{ echo '...' > /dev/null; } | sh`."""
    if text is None:
        return None
    route, i = None, 0
    while i < len(tokens):
        t, fd = tokens[i], None
        if _FD_RE.fullmatch(t) and i + 1 < len(tokens) and tokens[i + 1] in (syntax.OUT_REDIRECTS | syntax.IN_REDIRECTS):
            fd, i, t = t, i + 1, tokens[i + 1]
        if t not in syntax.OUT_REDIRECTS and t not in syntax.IN_REDIRECTS:
            i += 1
            continue
        operand = tokens[i + 1] if i + 1 < len(tokens) else ""
        i += 2
        if not (fd == "1" or fd is None and t in syntax.OUT_REDIRECTS and t != "<>"):
            continue  # another descriptor's, or standard input's
        if directories.redirect_descriptor(t, operand) or t == "<&":
            target = prepare.deglob(operand).lstrip("&")
            if target == "-":
                route = "closed"
            elif target != "1":
                route = "descriptor"
        elif t in syntax.IN_REDIRECTS or t == ">&" and word_text(operand) is None:
            route = "descriptor"  # `1< f` opens it for reading; `>&$fd` may name a descriptor once expanded
        else:
            route = "file"
    if route is None:
        return text
    bash = "" if route in ("file", "closed") else None
    return _paired(bash, _zsh(text) if feeds_pipe and route != "closed" else bash)


def _printer(name):
    return bool(name) and os.path.basename(name).casefold() in _PRINTERS


def _printed(texts, stdin):
    """printed_text's reading of one list of words as a shell passes them, `texts`, None for one the hook cannot say;
    the first is a printer's name.  `stdin`: the text the command reads, a MultiosText kept whole."""
    if texts[0] in _SILENT:
        return ""
    base, args = os.path.basename(texts[0]).casefold(), texts[1:]
    if base == "echo":
        return _echo_text(args)
    if base == "print":
        return _print_text(args)
    if base == "printf":
        return _printf_text(args)
    # tee(1) writes its input to each file and to standard output unchanged, whatever its options; cat prints its
    # input only when every operand it reads is that input (`cat`, `cat -`), never a file the hook does not read
    return stdin if base == "tee" or all(t == "-" for t in args) else None


def xargs_string(words, consumed, appended, text):
    """The command string an xargs call hands the shell it runs, from the standard input `text` the line spells, or None.

    The whole input where xargs passes it as one operand -- with -0 or -d, which read whole records, and where -I or -J
    put it, which find_xargs has already marked in the words -- and its first blank-separated word otherwise (probed:
    `echo 'vcs a' | xargs sh -c` ran `vcs` with $0 set to a).  `consumed` is the index of the utility in `words`
    (directories.strip_wrapper), `appended` whether xargs appends the input after the words the line spells."""
    if text is None:
        return None
    if not appended or _whole_input(words, consumed):
        return text
    first = text.split()
    return first[0] if first else None


def xargs_words(words, consumed, text):
    """The words an xargs call hands the command it runs, from the standard input `text` the line spells, or None where
    the line does not spell it.

    The whole input as one word where xargs reads whole records (-0 or -d), and its blank-separated words otherwise --
    every one of them, where xargs_string above takes the first: a shell's `-c` string is one operand, while an
    interpreter is handed them all as words of its own (`echo '-e code' | xargs node` runs `node -e code`)."""
    if text is None:
        return None
    return [text] if _whole_input(words, consumed) else text.split()


def _whole_input(words, consumed):
    """Whether this xargs call reads its input as whole records rather than blank-separated words (xargs(1): -0, --null,
    -d, --delimiter)."""
    for w in words[1:consumed]:
        t = prepare.deglob(w)
        if t == "--":
            break
        if t.startswith("--"):
            if t.partition("=")[0] in _WHOLE_INPUT_OPTIONS:
                return True
        elif t.startswith("-") and any(c in "0d" for c in t[1:]):
            return True
    return False


def _spelled_words(texts, between=" "):
    """The words' texts as the command prints them, `between` between them, or None when the hook cannot spell one."""
    return None if None in texts else between.join(texts)


def _echo_text(args):
    """What `echo` prints: its words, blank-separated, with a newline unless -n, and its escapes decoded -- zsh's
    reading, and bash's under -e -- unless -E asks for them as they stand.  A leading word that starts with `-` and is
    no option of echo's is text this module does not read: the shells differ over which of those they print.  `args`:
    the texts of its words after the command word, None for one the hook cannot say, here and in the printers below."""
    decode, newline = True, True
    while args:
        w = args[0]
        if w is None:
            return None
        if not w.startswith("-") or len(w) == 1 or syntax._IFS_BLANKS_RE.search(w):
            break  # a blank makes it text in both shells (probed, zsh 5.9 -f and bash 3.2: `echo '-n vcs a'` printed it)
        if not _ECHO_OPTIONS_RE.fullmatch(w):
            return None
        newline = newline and "n" not in w
        decode = "E" not in w
        args = args[1:]
    text = _spelled_words(args)
    if text is None:
        return None
    return (_decode(text) if decode else text) + ("\n" if newline else "")


def _print_text(args):
    """What zsh's `print` prints: its words with a blank between them, or a newline under -l, decoded unless -r or -R,
    with a trailing newline unless -n (probed: `print -r 'vcs a' | zsh -f` ran a).  Any other option -- -z, -s, -v, -P,
    -f and their kin, which print elsewhere or format -- leaves the text unread."""
    decode, newline, between = True, True, " "
    while args:
        w = args[0]
        if w is None:
            return None
        if not w.startswith("-") or len(w) == 1:
            break
        if w == "--":
            args = args[1:]
            break
        if any(c not in _PRINT_OPTIONS for c in w[1:]):
            return None
        decode = decode and not ("r" in w or "R" in w)
        newline = newline and "n" not in w
        between = "\n" if "l" in w else between
        args = args[1:]
    text = _spelled_words(args, between)
    if text is None:
        return None
    return (_decode(text) if decode else text) + ("\n" if newline else "")


def _printf_text(args):
    """What `printf` prints: its format, escapes decoded, applied to its arguments and repeated while any are left
    (printf(1)).  Only `%s`, `%b` and `%%` are applied -- every other directive, a width or a flag among them, and an
    argument the hook cannot spell, leave the text unread -- and so does `-v NAME`, which assigns and prints nothing."""
    if not args or args[0] == "-v" or None in args:
        return None
    fmt, values = args[0], args[1:]
    text = []
    while True:
        chunk, used = _printf_once(fmt, values)
        if chunk is None:
            return None
        text.append(chunk)
        values = values[used:]
        if not values or not used:
            return "".join(text)


def _printf_once(fmt, values):
    """(one pass of the format over `values`, how many of them it consumed), or (None, 0) for a directive not read."""
    out, i, used = [], 0, 0
    while i < len(fmt):
        if fmt[i] != "%":
            j = fmt.find("%", i)
            j = len(fmt) if j == -1 else j
            out.append(_decode(fmt[i:j]))
            i = j
            continue
        directive = fmt[i + 1 : i + 2]
        if directive == "%":
            out.append("%")
        elif directive in ("s", "b"):
            value = values[used] if used < len(values) else ""
            out.append(_decode(value) if directive == "b" else value)
            used += 1
        else:
            return None, 0
        i += 2
    return "".join(out), used


def _decode(text):
    """The text with the escapes echo, print and printf decode: the named ones, `\\0nnn` and `\\nnn` octal, `\\xhh` hex,
    and `\\c`, which ends the output there.  An escape none of them knows keeps its backslash, as the shells print it."""
    out, at = [], 0
    for m in _ESCAPE_RE.finditer(text):
        out.append(text[at : m.start()])
        at = m.end()
        body = m.group(1)
        if body == "c":
            return "".join(out)
        if body in _ESCAPES:
            out.append(_ESCAPES[body])
        elif body[0] == "x":
            out.append(chr(int(body[1:], 16)))
        elif body[0] in "01234567":
            out.append(chr(int(body, 8) & 0xFF))
        else:
            out.append(m.group(0))
    out.append(text[at:])
    return "".join(out)
