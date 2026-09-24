"""shell/script_text: the files a sed script or an awk program writes and the commands it runs, read for
bash_reason to hold each to the path rule as a redirection target and to Law 7 as an `sh -c` string.

The Bash hook reads a command's words, and a script is one quoted word, so before this module what the script itself named
was never read.  On main (08c344e), with cwd /Users/X/repo, each of these recorded findings=[] and no write:
`awk 'BEGIN{system("git push")}'` and `awk 'BEGIN{print "x" | "git push"}'`, a VCS write past Law 7;
`awk '{print > "ledger/tickets/SPD-001.md"}' f` and `sed -n 'w ledger/tickets/SPD-001.md' f`, a write to a rendered note
past Law 5.  A file the script names is now recorded where spelled_writes records dd's `of=`, held to the path rule as a
redirection target with the same whole-line masking -- a `$var` the shell would expand inside a double-quoted script is a
hidden part of the name, and the target is unresolvable -- and a command it hands /bin/sh is read where
analyse.analyse_new_shell reads a `-c` string.

Probed on this Mac, whose sed is BSD's (/usr/bin/sed) and whose awk is the one true awk, version 20200816; neither gsed
nor gawk is installed; reading the GNU tools under their g-prefixed names is left for later.

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

What stays unread, for every caller, is a script the line spells only in part or reads from what it does not own: `sed -n
"$S" f` and `awk "$P" f` with a variable of the environment's, which the line never touches, a -f file that is missing, too
large or not text, and a word the line cannot settle where an option may stand, after which the hook cannot say which
operand is the program.  That is the class of `sh script.sh` (a question left open for Eric), and it keeps the
write-by-argument reading of a member's `sed -n "${n},$((n+3))p" f`, which writes nothing, silent.

What refuses a member instead (SPD-260, under SPD-217's rule: where the reader cannot read what a shell will run, it
refuses the member and names a respelling) is a script word the line does not settle at all -- the program operand, a sed
-e, a -f file's name: what xargs reads from its input (appended where the program stands, or a -I or -J replstr in the
program's place or inside it), a path find hands its command, a word that is nothing but a substitution or variables the
line fills and does not settle (from a substitution, a file, a loop it cannot read, a positional), or a -f file on
standard input.  Probed through tests/probes/shell_probe.py (zsh 5.9 -f -o nobareglobqual and -f, bash 3.2.57; this
awk, BSD sed, xargs and find): `echo "'BEGIN{system(\\"touch o\\")}'" | xargs awk`, `xargs awk < l` and `xargs -J% awk
%` ran the program the input held, `echo "'w o' f" | xargs sed -n` and `echo 'w o' | xargs -I{} sed -n {} f` wrote o,
`P=$(cat p); awk "$P"`, `awk -f -`, `sed -n -f -` and `-f /dev/stdin` ran what they were fed, and BSD find replaced a
`{}` inside awk's program.  It is the "script-word" finding, which bash_rule reads for a member last of all, as an
interpreter's unreadable option position (shell/interpreter_words): a write the same input makes keeps its own reason
(SPD-248's `xargs sed -n < l`, whose input may be -i).  Text Claude Code's shell snapshot holds -- a profile function's
body, an alias's -- drops a script word that is its own variables or substitutions (`awk "$1"`, `awk "$prog"`), as it
drops a var-word, and keeps one the member fills: the call's words, a variable the line assigned (shell/held_text,
SPD-265).  Input the command reads -- xargs's, find's {}, standard input as the -f file -- is recorded apart
("script-input"), since it is the caller's wherever the text stands, and is kept there too.

A -f file the line names is read whatever operands follow it.  Where what xargs appends, or puts where -I or -J says,
stands where awk still reads its options after a -f, it may be one more `-f other.awk`, a program the hook never reads:
that is recorded too ("script-option", SPD-266), and bash_rule names the `--` that ends awk's options.  Probed (zsh 5.9
-f -o nobareglobqual and -f, bash 3.2.57, this awk): `echo '-f e.awk' | xargs awk -f c.awk`, `echo '-fe.awk' | xargs
-I{} awk -f c.awk {}` and `echo '-f e.awk' | xargs -J% awk -f c.awk %` ran e.awk; after a `--`, or after an operand, awk
took the input as a file it could not open.  sed's getopt reads one more -f or -e there the same way (`echo '-f e.sed' |
xargs sed -n -f c.sed` wrote the file e.sed names), and SPD-248 already refuses that input, which may be -i.  A word
the line fills and does not settle, standing there (`P=$(cat l); awk -f c.awk "$P" f`), stays in the class left unread
above for every caller: the -f file is read, and the word is not.

One module for both, past 250 lines (the package's look-again point) and past 500: the two grammars are scanned apart, in
two runs of short functions the `sed_` and `awk_` prefixes keep apart, but everything around them is one reading with one
caller -- the option scan that finds a script's fragments, the -f file, the masked slice a span names, the three ways
a span is recorded (a write, a command, a target the hook cannot name), and the script word the line does not settle
(SPD-260), which only that option scan finds.  Splitting it would make three modules, two of them with one user each, and
put two more imports on the hook path that every Bash, Edit and Agent call in every session pays for; the evidence Eric's
rule asks for is the largest definition's share, and here it is sed_spans at 63 lines, 10% of the file, with nothing else
above 45: no long region, one short function per shape of the two grammars, which is the shape shell/arg_writes and
shell/spelled_writes already have."""

import os

from . import analyse, arg_writes, bash_rule, expansions, globbing, prepare, spelled_writes, syntax
from ..hooks import hookio


SCRIPT_FILE_CAP = 64 * 1024  # the most of a -f script file the hook reads; a larger one is a script it does not read
STDIN_FILES = ("-", "/dev/stdin")  # a -f name sed and awk read standard input through (probed), as is every /dev/fd/N
SPECIAL_PARAMETERS = "@*#?$!-"  # the one-character parameters a shell sets itself, never the line
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
    word as spelled.  The line's own values are put in its words first (arg_writes.resolved), as every write target is
    read."""
    args = [arg_writes.resolved(w, a) for w in words[1:]]
    for group in sed_fragments(cmd, args, a) if base == "sed" else awk_fragments(cmd, args, a):
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


def unsettled(word, a):
    """True when a script word -- a program operand, a sed -e, a -f file's name -- is one the line does not settle at all
    (SPD-260, module docstring): it holds an operand the line does not spell (xargs's input, find's path), or it is nothing
    but expansions (expansion_names) of which one is a substitution or a variable the line fills without settling it --
    one it assigns (a value it settled is already in the word, arg_writes.resolved), loops over or doubts, or one the
    shell sets itself.  A variable the line never touches is the environment's, which a member's own line cannot set."""
    if syntax.unknown_operand(word):
        return True
    names = expansion_names(word)
    return names is not None and any(
        name is None or not (name[0].isalpha() or name[0] == "_") or a.all_doubt or name in syntax.DYNAMIC_VARIABLES
        or name in a.assigned or name in a.doubt or name in a.sticky for name in names)


def expansion_names(word):
    """The names a masked word expands, None standing for a `$( )` or backtick, where the word is nothing but expansions --
    `$NAME`, `${...}`, `$1`, `$@` and their kin, a substitution's placeholder -- double-quoted or not; None for any other
    word, which spells some of its text itself.  A `$` the line quoted is a literal one (its sentinel follows it), which
    no expansion begins with."""
    text = word.replace(syntax._QUOTED_NAME, "").replace(syntax._QUOTED_SUBST, "")
    names, i, n = [], 0, len(text)
    while i < n:
        if text.startswith(hookio.SUBST, i):
            names.append(None)
            i += len(hookio.SUBST)
            i += text[i : i + 1] == syntax.PROCSUB_MARK  # a process substitution's file name: a substitution's word too
            continue
        c = text[i + 1 : i + 2] if text[i] == "$" else ""
        if c == "{":
            end = text.find("}", i + 2)
            if end == -1:
                return None
            inner = text[i + 2 : end].lstrip("#!")
            j = 0
            while j < len(inner) and (inner[j].isalnum() or inner[j] == "_"):
                j += 1
            names.append(inner[:j] or inner[:1] or "{")  # `${#N}`, `${N:-x}` name N; a zsh flag `${(P)N}` names `(`
            i = end + 1
        elif c.isalpha() or c == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            names.append(text[i + 1 : j])
            i = j
        elif c and (c.isdigit() or c in SPECIAL_PARAMETERS):
            names.append(c)
            i += 2
        else:
            return None
    return names or None


def stdin_file(word):
    """True when a -f name is standard input, which sed and awk read the script from (probed: `awk -f -`, `sed -n -f -`
    and `-f /dev/stdin`), and which the hook never opens: in the hook's own process it is the hook's input."""
    text = prepare.deglob(word)
    return text in STDIN_FILES or text.startswith("/dev/fd/")


def script_word(cmd, word, a, kind=None):
    """Record a script word the line does not settle (unsettled, stdin_file), which bash_rule refuses a member last of all
    (module docstring), as (kind, "<the command word> <the word as the line spells it>"): one string, which
    held_text's prune reads as it reads a var-word's.  "script-word" for a word that is expansions of variables or
    substitutions, which text the shell holds may spell of its own (syntax.SHELL_TEXT_TOLERATED, SPD-265); "script-input"
    for input the command reads -- what xargs appends or puts where -I or -J says, find's {}, standard input as the -f file
    (`kind`) -- which is its caller's wherever that text stands; "script-option" (`kind`, SPD-266) for what xargs appends
    where awk still reads its options."""
    shown = syntax.shown_operands(prepare.deglob(word)).replace(hookio.SUBST, "$(...)")
    if kind is None:
        kind = "script-input" if syntax.unknown_operand(word) else "script-word"
    a.findings.append((kind, "%s %s" % (cmd, shown)))


def sed_fragments(cmd, args, a):
    """The groups of fragments sed reads as its script: each -e word and each -f file in the order they stand, else the
    first operand (sed(1): with either option given, every operand is a file).  A word the line cannot settle where an
    option may stand leaves the hook unable to place the script, and it reads none of them (arg_writes.hidden_option);
    reading each operand as a script instead found `w eb/app.js` in the file operand of a member's
    `sed -n "$(grep -n x app.js | cut -d: -f1),+12p" web/app.js`, which is the differential's own answer.  A script word
    the line does not settle at all is recorded first (script_word), and the script is read no further."""
    values, longs = syntax.ARG_WRITE_COMMANDS["sed"][1:]
    options, operands = arg_writes.scan(args, values, longs)
    scripts = [(name in ("-f", "--file"), value) for name, value, _ in options
               if value is not None and name in ("-e", "--expression", "-f", "--file")]
    for from_file, value in scripts:
        if from_file and stdin_file(value):
            script_word(cmd, value, a, "script-input")
            return []
        if unsettled(value, a):
            script_word(cmd, value, a)
            return []
    if not scripts and operands and unsettled(operands[0], a):
        script_word(cmd, operands[0], a)
        return []
    if arg_writes.hidden_option(args, values, longs):
        return []  # the hook cannot say which operand is the script, so it reads none of them
    given = []
    for from_file, value in scripts:
        if not from_file:
            given.append((value, False))
            continue
        fragment = script_file(value, a)
        if fragment is None:
            return []  # a script file the hook does not read: the fragments around it are read no further
        given.append(fragment)
    if given:
        return [given]
    return [[(operands[0], False)]] if operands else []


def awk_fragments(cmd, args, a):
    """The groups of fragments awk reads as its program: its -f files, which concatenate, else the first operand.  Only the
    first letter after a dash is the option (main.c), `--` ends them, and an unknown one is ignored.  A word the line
    cannot settle where an option may stand leaves the hook unable to say which operand is the program, and it reads
    none of them (module docstring); -f files are read whatever follows them, the words xargs appends among them.  A
    program word the line does not settle at all is recorded (script_word), and the program is read no further; so is
    what xargs puts where awk still reads its options after a -f, which may be one more -f (SPD-266)."""
    files, hidden, i = [], False, 0
    while i < len(args):
        word = args[i]
        text = prepare.deglob(word)
        if text == "--":
            i += 1
            break
        if not text.startswith("-") or len(text) == 1:
            if files and text.startswith(syntax.INPUT_OPERAND):
                script_word(cmd, word, a, "script-option")
            hidden = hidden or spelled_writes.operand_hidden(word, a)
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
    given = []
    for value in files:
        if stdin_file(value):
            script_word(cmd, value, a, "script-input")
            return []
        if unsettled(value, a):
            script_word(cmd, value, a)
            return []
        fragment = script_file(value, a)
        if fragment is None:
            return []
        given.append(fragment)
    if given:
        return [given]
    operands = args[i:]
    if not files and operands and unsettled(operands[0], a):
        script_word(cmd, operands[0], a)
        return []
    if hidden:
        return []
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
