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
masked words no longer tell `$X` from `"$X"`, so where a settled value holds a blank the printer is read as zsh passes the
word and as bash splits it unquoted, and the text kept only where both print the same (printed_text, _string_text).

What stays unread: standard input the line does not spell -- a file (`sh < f`), another program's output (`cat f | sh`,
`curl ... | sh`), a value the line does not settle or the two readings print apart, or text this module cannot decode.  That is the same class as `sh script.sh`, a script the hook does
not read either.  Eric's call on SPD-145 (fail closed): a member is refused both, a shell reading standard input the
line does not spell and a shell given a script file (script_operand below), by shell/script_files; Spud is not.

Kept whole past 250 lines (the package's look-again point): it answers one question -- what text stands on a
command's standard input and standard output -- and the two halves are the same reading from either end.  `input_fed`
says whether the line puts anything there at all, which is the same reading of the same redirections.  What the
printers decode is the table `analyse` and `walk` reach it for; splitting the escapes off would leave a module no caller
names.
"""

import os
import re

from . import arg_writes, directories, expansions, globbing, prepare, syntax

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


def joined(before, text):
    """The text two commands print one after the other; None, text the hook cannot spell, absorbs."""
    return None if before is None or text is None else before + text


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
    """The words bash makes of this masked word were each of its expansions unquoted: a settled value split at its runs of
    blanks, the default IFS, and an empty field at either end dropped (SPD-148).  [None] where word_text cannot say it."""
    text = word_text(arg_writes.resolved(word, a, _split))
    if text is None:
        return [None]
    return [f for f in text.split(_FIELD) if f] if _FIELD in text else [text]


def _whole(value):
    return value


def _split(value):
    return syntax._IFS_BLANKS_RE.sub(_FIELD, value)


def _readings(words, a):
    """The words a command is passed as zsh reads them and as bash reads them unquoted -- each a list of texts, None for
    a word the hook cannot say.  The masked words no longer tell `$X` from `"$X"`, so where a settled value holds a blank
    the two are the only readings there are, and a caller keeps what they agree on."""
    return [word_text(w, a) for w in words], [f for w in words for f in word_fields(w, a)]


def command_input(tokens, bodies, piped, a=None):
    """The text the simple command `tokens` reads on standard input, or None where the line does not spell it.

    The last thing the line puts there wins: a here-document body, which ShellWalk.consume has already taken out of the
    words (`bodies`); a here-string whose word the hook can resolve; or the text the pipeline element before this one
    printed (`piped`).  A file (`sh < f`), a descriptor (`sh <&3`) and a word holding an expansion, a substitution or a
    glob leave it unknown, and so does a here-string whose settled value the two shells do not read alike
    (_string_text).  A here-document and a `<` on one command are read in the order the redirections stand, which
    the body no longer stands in; the body wins, since the hook reads it anyway.  `a`: the line's analysis, whose settled
    values a here-string's word is read with (SPD-148)."""
    text = bodies[-1] + "\n" if bodies else piped
    i = 0
    while i < len(tokens):
        t, fd = tokens[i], None
        if _FD_RE.fullmatch(t) and i + 1 < len(tokens) and tokens[i + 1] in (syntax.OUT_REDIRECTS | syntax.IN_REDIRECTS):
            fd, i, t = t, i + 1, tokens[i + 1]
        if t in syntax.IN_REDIRECTS:
            if fd in (None, "0") and t not in ("<<", "<<-"):  # a here-document's body is in `bodies`
                spelled = _string_text(tokens[i + 1], a) if t == "<<<" and i + 1 < len(tokens) else None
                text = None if spelled is None else spelled + "\n"
            i += 2
            continue
        i += 2 if t in syntax.OUT_REDIRECTS else 1
    return text


def _string_text(word, a):
    """A here-string's text, its word read with the line's settled values (`a`): whole, as zsh reads it, where bash's
    reading of the word unquoted -- its fields joined by a blank -- is the same text, and None where the two differ."""
    whole = word_text(word, a)
    if a is None or whole is None or "$" not in word:
        return whole
    fields = word_fields(word, a)
    return whole if None not in fields and " ".join(fields) == whole else None


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


def printed_text(tokens, bodies, piped, a=None):
    """The text the simple command `tokens` prints on standard output, or None where the hook cannot spell it.

    Only the commands that print what the line spells are read: echo, zsh's print, printf, a cat of its own input, and
    tee, which passes its input on whatever files it also writes.  A command whose standard output a redirection takes
    prints nothing into the pipe, and every other command prints text this module does not know.

    `a`, the line's analysis before this command runs: a `$NAME` the line settled is read as its value (SPD-148), the
    value the shells expand it to, since the command's own prefix assignments reach none of its words.  The masked words
    no longer say whether the expansion was quoted, and bash splits an unquoted one at its blanks where zsh never does, so
    such a command is read both ways (_readings) and its text kept only where the two agree: `X='git push'; echo $X`
    prints `git push` either way, `X='-n x'; echo $X` and `printf '%s\\n' $X` do not."""
    if _stdout_taken(tokens):
        return None
    words = directories.separate_redirects(tokens)[0]
    if a is None or not any("$" in w for w in words):
        name = word_text(words[0]) if words else None
        if not _printer(name):
            return None
        return _printed([name] + [word_text(w) for w in words[1:]], tokens, bodies, piped, a)
    if not _printer(word_text(words[0], a)):
        return None  # zsh's reading prints nothing this module reads, so bash's cannot agree with a text
    whole, fields = _readings(words, a)
    text = _printed(whole, tokens, bodies, piped, a)
    return text if fields and _printer(fields[0]) and text == _printed(fields, tokens, bodies, piped, a) else None


def _printer(name):
    return bool(name) and os.path.basename(name).casefold() in _PRINTERS


def _printed(texts, tokens, bodies, piped, a):
    """printed_text's reading of one list of words as a shell passes them, `texts`, None for one the hook cannot say;
    the first is a printer's name."""
    base, args = os.path.basename(texts[0]).casefold(), texts[1:]
    if base == "echo":
        return _echo_text(args)
    if base == "print":
        return _print_text(args)
    if base == "printf":
        return _printf_text(args)
    stdin = command_input(tokens, bodies, piped, a)
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


def _stdout_taken(tokens):
    """Whether a redirection on this command takes its standard output, so what it prints never reaches the pipe: an
    output operator with no descriptor or with 1 before it, and `&>`/`&>>`, which take both (`2>&1` does not)."""
    for i, t in enumerate(tokens):
        if t in syntax.OUT_REDIRECTS:
            fd = tokens[i - 1] if i and _FD_RE.fullmatch(tokens[i - 1]) else None
            if fd in (None, "1") or t in ("&>", "&>>"):
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
        if not w.startswith("-") or len(w) == 1:
            break
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
