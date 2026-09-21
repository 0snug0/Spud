"""shell/script_text: the files a sed script or an awk program writes and the commands it runs (SPD-139), read for
bash_reason to hold each to the path rule as a redirection target and to Law 7 as an `sh -c` string.

The Bash hook reads a command's words, and a script is one quoted word, so until this ticket what the script itself named
was never read.  On main (08c344e), with cwd /Users/x/repo, each of these recorded findings=[] and no write:
`awk 'BEGIN{system("git push")}'` and `awk 'BEGIN{print "x" | "git push"}'`, a VCS write past Law 7;
`awk '{print > "ledger/tickets/SPD-001.md"}' f` and `sed -n 'w ledger/tickets/SPD-001.md' f`, a write to a rendered note
past Law 5.  A file the script names is now recorded where spelled_writes records dd's `of=`, held to the path rule as a
redirection target with the same whole-line masking -- a `$var` the shell would expand inside a double-quoted script is a
hidden part of the name, and the target is unresolvable -- and a command it hands /bin/sh is read where
analyse.analyse_new_shell reads a `-c` string.

Probed on this Mac, whose sed is BSD's (/usr/bin/sed) and whose awk is the one true awk, version 20200816; neither gsed
nor gawk is installed, and SPD-140 owns the g-names.

- sed(1): the `w file` command and the `w file` flag of `s///` take the rest of the line as the name -- `w out.txt;p` made
  a file called `out.txt;p`, and `w out.txt   ` one with the blanks (sed warns and keeps them) -- with the blanks after
  the letter skipped and `wout.txt` needing none.  Each -e is its own line, so a name ends where its fragment does, and a
  brace may open in one fragment and close in another.  `b`, `t` and `:` take the rest of the line as a label (`b;w f`
  failed with "undefined label ;w f"), `a`, `i` and `c` need a backslash-newline and their text lines are not commands,
  `#` comments to the end of the line, `r` only reads, and this sed has no `W`, `R`, `T`, `z`, `F`, `Q`, `e` or `v`: a
  letter outside its own list is a script it refuses, and so one the hook cannot read either.
- awk(1) and its main.c: only the first letter after a dash is the option (`switch (argv[1][1])`), so there is no getopt
  cluster and `-safe` is `s`; -f, -F and -v take the rest of their word or the next word; an unknown option is ignored
  with a warning and the program is still the next operand; `--` ends the options; -f may repeat and its files
  concatenate; the program is the first operand only where no -f was given, and an operand `var=value` is an assignment.
  `print` and `printf` write with `>` and `>>` and run a command with `|`, `system(...)` runs one, and `"cmd" | getline`
  runs one and reads it.  `>` is also the comparison: `print (1 > 2)` printed 0, so only a `>` standing at the statement's
  own bracket depth after `print` or `printf` redirects.

What is readable there, and what is not.  A target or command that is one string literal standing alone is read as the
name it spells.  Anything else -- a variable (`print > f`), a parenthesised expression (`> ("out/f")`), a bare
concatenation (`> "out" "6.txt"`, which made out6.txt) -- is a file or a command the hook cannot name, and is recorded as
syntax.ANY_PATH, which refuses a member as an unresolvable redirection target does and leaves Spud's own reading silent.
A script this Mac's sed or awk would itself refuse is recorded the same way: the hook fails closed on what it cannot
read.

What stays unread, for every caller, is a script the line does not spell: `sed -n "$S" f`, `awk "$P" f`, a -f file that is
missing, too large or not text, and a word the line cannot settle where an option may stand, after which the hook cannot
say which operand is the program.  That is the class of `sh script.sh` (SPD-145's question for Eric), and it keeps
SPD-121's reading of a member's `sed -n "${n},$((n+3))p" f`, which writes nothing, silent.

One module for both, past 250 lines (SPD-065's look-again point) and past 500: the two grammars are scanned apart, in
two runs of short functions the `sed_` and `awk_` prefixes keep apart, but everything around them is one reading with one
caller -- the option scan that finds a script's fragments, the -f file, the masked slice a span names, and the three ways
a span is recorded (a write, a command, a target the hook cannot name).  Splitting it would make three modules, two of
them with one user each, and put two more imports on the hook path that every Bash, Edit and Agent call in every session
pays for; the evidence Eric's rule asks for is the largest definition's share, and here it is sed_spans at 63 lines, 12%
of the file, with nothing else above 45: no long region, one short function per shape of the two grammars, which is the
shape shell/arg_writes and shell/spelled_writes already have."""

import os

from . import analyse, arg_writes, bash_rule, expansions, globbing, prepare, spelled_writes, syntax


SCRIPT_FILE_CAP = 64 * 1024  # the most of a -f script file the hook reads; a larger one is a script it does not read
AWK_VALUE_LETTERS = "fFv"  # awk's options that take the rest of their word or the next word (main.c)
# sed(1)'s own function letters on this Mac: the ones that take the rest of the line as a label, as a file name (`w`
# writes it, `r` only reads), and the ones that carry nothing more than an optional number.  Every other letter is one
# this sed refuses.
SED_LABEL_COMMANDS = "bt:"
SED_FILE_COMMANDS = "rw"
SED_PLAIN_COMMANDS = "=dDgGhHnNpPxlq"
# The awk names after which a `/` opens a regular expression rather than dividing (the usual lexer rule: after a value it
# divides), and the tokens that end a value.
AWK_KEYWORDS = frozenset({"BEGIN", "END", "break", "continue", "delete", "do", "else", "exit", "for", "function",
                          "getline", "if", "in", "next", "nextfile", "print", "printf", "return", "while"})
AWK_OPERATORS = ("**=", ">>", "<=", ">=", "==", "!=", "&&", "||", "++", "--", "+=", "-=", "*=", "/=", "%=", "^=", "!~", "**")
AWK_VALUE_END = (")", "]", "++", "--", "$")
AWK_STATEMENT_END = (";", "\n", "}", ")")


def read_script(cmd, base, words, a, depth):
    """Record what the sed or awk command `words` writes and runs by its script (module docstring); `cmd` is its command
    word as spelled.  The line's own values are put in its words first (arg_writes.resolved), as SPD-127 reads every write
    target."""
    args = [arg_writes.resolved(w, a) for w in words[1:]]
    for group in sed_fragments(args, a) if base == "sed" else awk_fragments(args, a):
        text, offsets = script_lines(group)
        spans = sed_spans(text) if base == "sed" else awk_spans(text)
        record_spans(cmd, a, depth, text, group, offsets, spans, not hidden_script(group))


def script_lines(group):
    """(the text a group of fragments makes, the index each fragment starts at in it).  sed reads each -e and each -f file
    as its own line, and awk concatenates its -f files, so a newline between them is both grammars' reading."""
    text, offsets, at = [], [], 0
    for masked, _ in group:
        offsets.append(at)
        spelled = prepare.deglob(masked)
        text.append(spelled)
        at += len(spelled) + 1
    return "\n".join(text), offsets


def record_spans(cmd, a, depth, text, group, offsets, spans, closed):
    """Record each span the scan found: a file the script writes, a command it hands /bin/sh, and a target or command the
    hook cannot name, which is syntax.ANY_PATH -- refused a member as an unresolvable redirection target, silent for
    Spud.  `closed` is False for a script the line does not spell whole, where a scan that could not read it has read a
    script the line never gave, and only what it positively found is recorded."""
    for kind, start, end in spans:
        word = None if kind == "unknown" else span_word(text, group, offsets, start, end)
        if word is None:
            if closed:
                spelled_writes.spelled_write(a, cmd, syntax.ANY_PATH, "tree")
        elif kind == "write":
            spelled_writes.spelled_write(a, cmd, word)
        else:
            analyse.analyse_new_shell(a, prepare.deglob(word), depth + 1)


def span_word(text, group, offsets, start, end):
    """The masked word for the text from `start` to `end`, or None when it crosses two fragments (a name sed reads to the
    end of its line and a string literal awk reads to its closing quote never do).  A fragment whose word is as long as
    the text it spells holds no marker deglob removes, so the two line up and the slice is the word's own; every other
    one is walked (spelled_writes.after_spelled), which a script of thousands of writes would pay for once each.  A
    fragment the hook read from a -f file is text, not a word the shell passed, so its glob characters are literal
    (globbing.literalize)."""
    for i in reversed(range(len(offsets))):
        if offsets[i] <= start:
            masked, from_file = group[i]
            stop = offsets[i + 1] - 1 if i + 1 < len(offsets) else len(text)  # where this fragment's own text ends
            if end > stop:
                return None  # the newline script_lines put between two fragments stands inside the span
            if len(masked) == stop - offsets[i]:
                word = masked[start - offsets[i] : end - offsets[i]]
            else:
                word = spelled_writes.spelled_head(spelled_writes.after_spelled(masked, start - offsets[i]), end - start)
            return globbing.literalize(word) if from_file else word
    return None


def hidden_script(group):
    """True when a fragment of this script holds what the line does not spell -- an expansion, a substitution, an operand
    the line does not spell -- so a scan that cannot read it has read a script the line never gave, and records nothing."""
    return any(expansions.expansion_word(masked) or syntax.unknown_operand(masked) for masked, _ in group)


def script_file(word, a):
    """The text of a script file the line names, as a fragment, or None where the hook does not read it: a name it cannot
    resolve or follow, a glob, a file it cannot open in every directory the shell may be in, one larger than
    SCRIPT_FILE_CAP, one that is not UTF-8 text, or two different files behind one name."""
    if arg_writes.unresolved(word) or bash_rule.target_has_active_glob(word):
        return None
    text = prepare.deglob(word)
    if not text or text == "-":
        return None
    found = None
    for path in bash_rule.redirection_paths(text, a.cwds) or []:
        try:
            with open(os.path.expanduser(path), "rb") as fh:
                data = fh.read(SCRIPT_FILE_CAP + 1)
        except OSError:
            return None
        if len(data) > SCRIPT_FILE_CAP:
            return None
        try:
            one = data.decode("utf-8")
        except ValueError:
            return None
        if found is not None and one != found:
            return None
        found = one
    return None if found is None else (found, True)


def sed_fragments(args, a):
    """The groups of fragments sed reads as its script: each -e word and each -f file in the order they stand, else the
    first operand (sed(1): with either option given, every operand is a file).  A word the line cannot settle where an
    option may stand leaves the hook unable to place the script, and it reads none of them (arg_writes.hidden_option);
    reading each operand as a script instead found `w eb/app.js` in the file operand of a member's
    `sed -n "$(grep -n x app.js | cut -d: -f1),+12p" web/app.js`, which is the differential's own answer."""
    values, longs = syntax.ARG_WRITE_COMMANDS["sed"][1:]
    options, operands = arg_writes.scan(args, values, longs)
    if arg_writes.hidden_option(args, values, longs):
        return []  # the hook cannot say which operand is the script, so it reads none of them
    given = []
    for name, value, _ in options:
        if name in ("-e", "--expression") and value is not None:
            given.append((value, False))
        elif name in ("-f", "--file") and value is not None:
            fragment = script_file(value, a)
            if fragment is None:
                return []  # a script file the hook does not read: the fragments around it are read no further
            given.append(fragment)
    if given:
        return [given]
    return [[(operands[0], False)]] if operands else []


def awk_fragments(args, a):
    """The groups of fragments awk reads as its program: its -f files, which concatenate, else the first operand.  Only the
    first letter after a dash is the option (main.c), `--` ends them, and an unknown one is ignored.  A word the line
    cannot settle where an option may stand leaves the hook unable to say which operand is the program, and it reads
    none of them (module docstring)."""
    files, hidden, i = [], False, 0
    while i < len(args):
        word = args[i]
        text = prepare.deglob(word)
        if text == "--":
            i += 1
            break
        if not text.startswith("-") or len(text) == 1:
            hidden = spelled_writes.operand_hidden(word, a)
            break
        hidden = hidden or spelled_writes.option_hidden(word, a)
        if text[1] in AWK_VALUE_LETTERS:
            if len(text) > 2:
                value = spelled_writes.after_spelled(word, 2)
            else:
                value, i = (args[i + 1] if i + 1 < len(args) else ""), i + 1
            if text[1] == "f":
                files.append(value)
        i += 1
    if hidden:
        return []
    given = []
    for value in files:
        fragment = script_file(value, a)
        if fragment is None:
            return []
        given.append(fragment)
    operands = args[i:]
    if given:
        return [given]
    return [[(operands[0], False)]] if operands else []


def line_end(text, i):
    """The index of the newline that ends the line `i` stands in, or the end of the text."""
    found = text.find("\n", i)
    return len(text) if found == -1 else found


def delimited(text, i, delim, brackets=True):
    """The index after the next `delim` a backslash does not escape, or None where the line ends first.  `brackets`: a
    bracket expression is skipped whole, the delimiter inside it and all (sed's compile_delimited, probed: `/[/]/w f` and
    `s/.*github.com[:/]//` both run).  A replacement and `y`'s two strings are read without it, which is what this sed
    does: `s/a/[x/]/` ended the replacement at the third `/` and failed on `]` as a flag."""
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "\n":
            return None
        if c == delim:
            return i + 1
        if c == "[" and brackets:
            i = bracket_end(text, i)
            if i is None:
                return None
            continue
        i += 1
    return None


def bracket_end(text, i):
    """The index after the bracket expression starting at `text[i]`, or None where it does not close (sed's compile_ccl):
    a `^` may follow the bracket, a `]` right after either stands for itself, `[:class:]`, `[.coll.]` and `[=eq=]` end
    with their own delimiter, and a backslash escapes nothing inside."""
    n, i = len(text), i + 1
    if i < n and text[i] == "^":
        i += 1
    if i < n and text[i] == "]":
        i += 1
    while i < n and text[i] != "]":
        if text[i] == "[" and text[i + 1 : i + 2] in (".", ":", "="):
            found = text.find(text[i + 1] + "]", i + 2)
            if found == -1:
                return None
            i = found + 2
        else:
            i += 1
    return i + 1 if i < n else None


def sed_address(text, i):
    """(the index after an address, or `i` where there is none; False where the hook cannot read one): a line number, `$`,
    `/re/`, `\\cREc`, `+N` after a comma, each with an optional `I` (sed(1)).  This sed has no `first~step`, so a script
    that uses GNU's is one it refuses."""
    n = len(text)
    if i >= n:
        return i, True
    c = text[i]
    if c.isdigit() or (c == "+" and i + 1 < n and text[i + 1].isdigit()):
        i += 0 if c.isdigit() else 1
        while i < n and text[i].isdigit():
            i += 1
    elif c == "$":
        i += 1
    elif c == "/":
        i = delimited(text, i + 1, "/")
    elif c == "\\" and i + 1 < n:
        i = delimited(text, i + 2, text[i + 1])
    else:
        return i, True
    if i is None:
        return 0, False
    while i < n and text[i] == "I":
        i += 1
    return i, True


def sed_text_end(text, i):
    """The index after the text of an `a`, `i` or `c`, or None: this sed wants a backslash-newline and then lines while
    each ends with a backslash (probed: `1a w f` failed with "command a expects \\ followed by text")."""
    n = len(text)
    while i < n and text[i] in " \t":
        i += 1
    if i >= n or text[i] != "\\":
        return None
    i += 1
    if i < n and text[i] == "\n":  # the newline the backslash escapes: the text begins on the line after it
        i += 1
    while True:
        end = line_end(text, i)
        if end == n:
            return n
        slashes = 0  # a text line that ends with a backslash the line before it does not escape carries on
        while end - 1 - slashes >= i and text[end - 1 - slashes] == "\\":
            slashes += 1
        i = end + 1
        if slashes % 2 == 0:
            return i


def sed_spans(text):
    """[(what, start, end)] for a sed script: ("write", the file a `w` command or a `s///w` flag names) and ("unknown", 0,
    0) where the script runs past what this Mac's sed reads, which is a script it refuses and one the hook cannot read."""
    spans, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\n;}":
            i += 1
            continue
        if c == "#":
            i = line_end(text, i)
            continue
        i, ok = sed_address(text, i)
        if not ok:
            return spans + [("unknown", 0, 0)]
        while i < n and text[i] in " \t":
            i += 1
        if i < n and text[i] == ",":
            i, ok = sed_address(text, i + 1)
            if not ok:
                return spans + [("unknown", 0, 0)]
        while i < n and text[i] in " \t!":
            i += 1
        if i >= n:
            return spans + [("unknown", 0, 0)]  # an address with no command: a script this sed refuses
        c, i = text[i], i + 1
        if c == "{":
            continue
        if c in SED_LABEL_COMMANDS:
            i = line_end(text, i)
        elif c in SED_FILE_COMMANDS:
            while i < n and text[i] in " \t":
                i += 1
            end = line_end(text, i)
            if end == i:
                return spans + [("unknown", 0, 0)]  # `w` with no name: a script this sed refuses
            if c == "w":
                spans.append(("write", i, end))
            i = end
        elif c in "aic":
            end = sed_text_end(text, i)
            if end is None:
                return spans + [("unknown", 0, 0)]
            i = end
        elif c == "s":
            i, span = sed_substitute(text, i)
            if i is None:
                return spans + [("unknown", 0, 0)]
            spans += span
        elif c == "y":
            if i >= n or text[i] in "\\\n":
                return spans + [("unknown", 0, 0)]
            delim = text[i]  # `y`'s two strings, which this sed reads without bracket expressions (is_tr)
            i = delimited(text, i + 1, delim, brackets=False)
            i = None if i is None else delimited(text, i, delim, brackets=False)
            if i is None:
                return spans + [("unknown", 0, 0)]
        elif c in SED_PLAIN_COMMANDS:
            while i < n and text[i].isdigit():  # `l` takes a line length and `q` an exit status where sed allows one
                i += 1
        else:
            return spans + [("unknown", 0, 0)]
    return spans


def sed_substitute(text, i):
    """(the index after an `s` command, its spans) for the text from just past the letter, or (None, []): the delimiter,
    the regular expression, the replacement, then the flags, of which `w` takes the rest of the line as a file."""
    n = len(text)
    if i >= n or text[i] in "\\\n":
        return None, []
    delim = text[i]
    i = delimited(text, i + 1, delim)  # the regular expression, its bracket expressions whole
    i = None if i is None else delimited(text, i, delim, brackets=False)  # the replacement, which has none
    if i is None:
        return None, []
    while i < n:
        c = text[i]
        if c.isdigit() or c in "gpiI":  # this sed's own flags: an occurrence, g, p, i or I, and w (sed(1))
            i += 1
            continue
        if c == "w":
            i += 1
            while i < n and text[i] in " \t":
                i += 1
            end = line_end(text, i)
            return (end, [("write", i, end)]) if end > i else (None, [])
        break
    return i, []


def awk_tokens(text):
    """[(what, its text, start, end)] for an awk program -- "str" (a string literal, without its quotes), "re", "name",
    "num" and "op" -- or None where it does not close, which is a program this awk refuses."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r":
            i += 1
        elif c == "\\" and i + 1 < n and text[i + 1] == "\n":
            i += 2
        elif c == "#":
            i = line_end(text, i)
        elif c == '"':
            j = i + 1
            while j < n and text[j] not in ('"', "\n"):
                j += 2 if text[j] == "\\" else 1
            if j >= n or text[j] == "\n":
                return None
            out.append(("str", text[i + 1 : j], i + 1, j))
            i = j + 1
        elif c == "/" and awk_regex_here(out):
            j = i + 1
            while j < n and text[j] not in ("/", "\n"):
                j += 2 if text[j] == "\\" else 1
            if j >= n or text[j] == "\n":
                return None
            out.append(("re", text[i : j + 1], i, j + 1))
            i = j + 1
        elif c.isdigit():
            j = i
            while j < n and (text[j].isdigit() or text[j] == "."):
                j += 1
            out.append(("num", text[i:j], i, j))
            i = j
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            out.append(("name", text[i:j], i, j))
            i = j
        else:
            op = next((o for o in AWK_OPERATORS if text.startswith(o, i)), c)
            out.append(("op", op, i, i + len(op)))
            i += len(op)
    return out


def awk_regex_here(out):
    """Whether a `/` here opens a regular expression rather than dividing: after a value it divides, and after anything
    else -- the start of the program, an operator, a keyword -- it opens one."""
    if not out:
        return True
    what, text = out[-1][0], out[-1][1]
    if what == "name":
        return text in AWK_KEYWORDS
    if what in ("num", "str", "re"):
        return False
    return text not in AWK_VALUE_END


def awk_spans(text):
    """[(what, start, end)] for an awk program: ("write", the file a `print` or `printf` redirection names), ("run", the
    command one pipes to, `system` runs, or `getline` reads), and ("unknown", 0, 0) for one the hook cannot name."""
    tokens = awk_tokens(text)
    if tokens is None:
        return [("unknown", 0, 0)]
    spans, depth, printing = [], 0, None
    for k, (what, token, start, end) in enumerate(tokens):
        if what == "name":
            if token in ("print", "printf"):
                printing = depth
            elif token == "system":
                spans.append(awk_call(tokens, k))
            continue
        if what != "op":
            continue
        if token in ("(", "["):
            depth += 1
        elif token in (")", "]"):
            depth -= 1
        elif token in ("{", "}", ";", "\n"):
            printing = None
        elif token in (">", ">>") and printing == depth:
            spans.append(awk_after(tokens, k, "write"))
            printing = None
        elif token == "|":
            if k + 1 < len(tokens) and tokens[k + 1][:2] == ("name", "getline"):
                spans.append(awk_before(tokens, k))
            elif printing == depth:
                spans.append(awk_after(tokens, k, "run"))
                printing = None
    return spans


def awk_after(tokens, k, what):
    """The span of the string literal standing alone after `tokens[k]` -- a redirection's file, a pipe's command -- or an
    unknown one: a variable, a parenthesised expression or a concatenation, none of which the hook can name."""
    given = tokens[k + 1] if k + 1 < len(tokens) else None
    after = tokens[k + 2] if k + 2 < len(tokens) else None
    if given is None or given[0] != "str":
        return ("unknown", 0, 0)
    if after is not None and not (after[0] == "op" and after[1] in AWK_STATEMENT_END):
        return ("unknown", 0, 0)  # the literal is the first part of a concatenation awk builds the name from
    return awk_literal(given, what)


def awk_before(tokens, k):
    """The span of the string literal standing alone before `tokens[k]`, which `"cmd" | getline` runs, or an unknown one.
    The token before it must be one no value ends, which awk_regex_here answers for the tokens up to it."""
    given = tokens[k - 1] if k else None
    if given is None or given[0] != "str" or not awk_regex_here(tokens[: k - 1]):
        return ("unknown", 0, 0)
    return awk_literal(given, "run")


def awk_literal(token, what):
    """The span a string literal names, or an unknown one where an escape makes the name awk builds other than the
    characters the program spells (awk(1)'s C escapes), which no span of the line's own words can carry."""
    _, body, start, end = token
    return (what, start, end) if "\\" not in body else ("unknown", 0, 0)


def awk_call(tokens, k):
    """The span of `system("...")`'s command, or an unknown one where its argument is any other expression."""
    rest = tokens[k + 1 : k + 4]
    if len(rest) == 3 and rest[0][:2] == ("op", "(") and rest[1][0] == "str" and rest[2][:2] == ("op", ")"):
        return awk_literal(rest[1], "run")
    return ("unknown", 0, 0)
