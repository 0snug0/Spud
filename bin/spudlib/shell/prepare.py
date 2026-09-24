"""shell/prepare: ANSI-C strings, substitutions, newlines and quoted globs before tokenizing (shell/heredocs goes first).

One pass per step analyse_command takes before shlex, each reading the quotes the one before it wrote and the same
substitution spans, and shell/heredocs prepares the text it asks about with the same steps: kept whole past the ~250-line
mark (the spudlib-modules size rule), where a module of its own for the ANSI-C pass (SPD-202) would put one more file on
every hook run's import path for two functions that read the others' spans.  The decoder of a `$'...'` string's value is
hooks/snapshots.ansi_c_value, beside the alias reading that needs it too, so a command loading snapshots loads no shell/
module (SPD-216)."""

import re

from . import syntax
from ..hooks import hookio, snapshots


# What a quoted or escaped character becomes: a glob metacharacter's sentinel, or a shell operator character's, so a
# quoted `;` or `(` stays in its word instead of reaching the walk as the operator.
_QUOTED_SENTINELS = dict(syntax._GLOB_SENTINELS, **syntax._PUNCT_SENTINELS)

_ANSI_C_PAIR_RE = re.compile(r"\\(.)", re.S)
_COMMENT_AFTER = " \t\n;&|()<>"  # before a `#` that opens a comment, as newlines_as_separators reads one


def substitution_end(command, i):
    """The index of the `)` that closes the `$(` at command[i], its parentheses counted, or len(command) when none does."""
    depth, j, n = 0, i + 1, len(command)
    while j < n:
        if command[j] == "(":
            depth += 1
        elif command[j] == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return j


def backtick_end(command, i):
    """The index of the backtick that closes the one at command[i], or len(command) when none does."""
    j = command.find("`", i + 1)
    return len(command) if j == -1 else j


def ansi_c_end(text, i):
    """The index of the `'` that closes the ANSI-C string whose text starts at text[i], just after its `$'`, or len(text)
    when none does: a backslash escapes the next character, so `\\'` ends nothing (SPD-202)."""
    n = len(text)
    while i < n:
        c = text[i]
        if c == "'":
            return i
        i += 2 if c == "\\" else 1
    return n


def _hex_apostrophe(m):
    return "\\x27" if m.group(1) == "'" else m.group()


def ansi_c_quotes(text):
    """(the text with each ANSI-C string, `$'...'`, spelled as the steps after this one read it, what makes the text one
    the hook cannot read or None) -- SPD-202.  zsh and bash read `$'...'` wherever a `$` opens it outside quotes, in an
    unquoted `${ }` too; a backslash escapes the next character there, so `\\'` does not end it, and shlex, which reads it as
    plain single quotes, ended it there: `echo $'\\'' ; git push ; echo \\'` read as one echo while both shells pushed.  So:

    - a string both shells decode alike (hooks/snapshots.ansi_c_value) becomes its value in single quotes, a literal, which every reading
      after this one reads as the word the shells pass (a command word, a git verb, a write target, eval's text);
    - one whose escapes they decode apart keeps its `$'`, the text the hook does not decode that neutralize_quoted_globs
      marks, with each escaped apostrophe spelled `\\x27`, the same character, so that it holds no `'` but its closer;
    - one that never closes stays open, and the text is one the hook cannot read (("$'", the text from it));
    - after `$$` a quote opens ANSI-C quoting in zsh and single quotes in bash (probed: `echo $$'\\'' > /dev/null ; echo RAN >
      l/z ; echo '\\''` wrote l/z in zsh alone), which end it apart once a backslash stands before its next `'`: the text
      is then one the hook cannot read (("$$'", the text from the `$$`)), and it is read on as bash reads it.

    Not in single or double quotes (`"$'a'"` is text in both shells), not after an escaped `$`, not in a comment; a `$( )`
    or backticks is copied as it stands, over the span split_substitutions lifts out, since its body's own analysis reads
    its own.  Here-document bodies are out of the text by now (shell/heredocs, which decodes a delimiter itself)."""
    if "$'" not in text:
        return text, None
    out, unreadable = [], None
    i, n, in_double = 0, len(text), False
    while i < n:
        c = text[i]
        if c == "'" and not in_double:
            j = text.find("'", i + 1)
            j = n if j < 0 else j + 1
        elif c == "\\":
            j = i + 2
        elif c == '"':
            in_double = not in_double
            j = i + 1
        elif c == "#" and not in_double and (i == 0 or text[i - 1] in _COMMENT_AFTER):
            j = text.find("\n", i)
            j = n if j < 0 else j
        elif c == "`" or text.startswith("$(", i) and not text.startswith("$((", i):
            j = (backtick_end(text, i) if c == "`" else substitution_end(text, i)) + 1
        elif text.startswith("$'", i) and not in_double:
            end = ansi_c_end(text, i + 2)
            body = text[i + 2 : end]
            value = snapshots.ansi_c_value(body) if end < n else None
            if value is not None:
                out.append("'" + value.replace("'", "'\\''") + "'")
            else:
                out.append("$'" + _ANSI_C_PAIR_RE.sub(_hex_apostrophe, body) + ("'" if end < n else ""))
                if end == n and unreadable is None:
                    unreadable = ("$'", text[i : i + syntax.UNTOKENIZED_KEPT])
            i = end + 1
            continue
        elif text.startswith("$$", i):
            j = i + 2
            if text.startswith("'", j) and not in_double and unreadable is None:
                close = text.find("'", j + 1)
                if "\\" in text[j + 1 : n if close < 0 else close]:
                    unreadable = ("$$'", text[i : i + syntax.UNTOKENIZED_KEPT])
        else:
            j = i + 1
        out.append(text[i:j])
        i = j
    return "".join(out), unreadable


def split_substitutions(command):
    """Lift `$(...)` and backtick bodies (outside single quotes) out of the command; each
    becomes its own command to analyse and a placeholder word in the outer text."""
    out, inner = [], []
    i, n = 0, len(command)
    state = None
    while i < n:
        c = command[i]
        if state == "'":
            out.append(c)
            if c == "'":
                state = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append(command[i : i + 2])
            i += 2
            continue
        if state is None and c == "'":
            state = "'"
            out.append(c)
            i += 1
            continue
        if c == '"':
            state = None if state == '"' else '"'
            out.append(c)
            i += 1
            continue
        if c == "$" and command.startswith("$(", i) and not command.startswith("$((", i):
            j = substitution_end(command, i)
            inner.append(command[i + 2 : j])
            out.append(hookio.SUBST)
            i = j + 1
            continue
        if c == "`":
            j = backtick_end(command, i)
            inner.append(command[i + 1 : j])
            out.append(hookio.SUBST)
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out), inner


def newlines_as_separators(text):
    """An unquoted newline ends a command as `;` does, but shlex reads it as a blank (`ls<newline>git push` once hid the
    push).  It is written as syntax.LINE_BREAK between blanks, which mark_zsh_patterns reads as `;`, except inside a zsh
    glob group, where zsh reads a newline as part of the pattern and a spelled `;` ends the word (SPD-183): the two could not
    be told apart when a newline was written as ` ; `.  A backslash-newline outside single quotes joins the lines.  A
    comment keeps its words (a word the shell ignores is at worst read as one more command) with its quote characters
    blanked, so an apostrophe in it cannot unbalance shlex, which gets no commenters.

    An unquoted `$( ... )` or backticks is copied as it stands, over the span split_substitutions lifts out (SPD-188): its
    text is the substitution's own analysis's, which reads its newlines, quotes and here-document bodies itself (in double
    quotes it always was, the quotes keeping its newlines).  Read here, a body's lines were separators and its apostrophe
    opened a quote, and the analysis of `x=$(cat <<EOF ... EOF<newline>)` could not find the body at all."""
    out = []
    i, n = 0, len(text)
    state = None  # None, "'", '"' or "#"
    line_break = " " + syntax.LINE_BREAK + " "
    while i < n:
        c = text[i]
        if state == "#":
            if c == "\n":
                state = None
                out.append(line_break)
            else:
                out.append(" " if c in "'\"`\\" else c)
            i += 1
            continue
        if state == "'":
            out.append(c)
            if c == "'":
                state = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            if text[i + 1] != "\n":
                out.append(text[i : i + 2])
            i += 2
            continue
        if state == '"':
            out.append(c)
            if c == '"':
                state = None
        elif c in "'\"":
            state = c
            out.append(c)
        elif c == "#" and (i == 0 or text[i - 1] in " \t\n;&|()<>"):
            state = "#"
            out.append(c)
        elif c == "$" and text.startswith("$(", i) and not text.startswith("$((", i):
            end = substitution_end(text, i) + 1
            out.append(text[i:end])
            i = end
            continue
        elif c == "`":
            end = backtick_end(text, i) + 1
            out.append(text[i:end])
            i = end
            continue
        elif c == "\n":
            out.append(line_break)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def neutralize_quoted_globs(text):
    """Replace a glob metacharacter (`* ? [ ] { } ,`) that is single-quoted, double-quoted or backslash-escaped with a
    sentinel, so filename generation and brace expansion are read only from the unquoted metacharacters (zsh 5.9
    and bash 3.2 both expand an unquoted glob in a redirection target, and both leave a quoted one literal).  The quotes and
    backslashes are kept for shlex to strip; deglob restores the literal character.  Word boundaries are untouched, so other
    words are read exactly as before.  A quoted or escaped shell operator character (`; & | < > ( )`) gets a sentinel
    too, so `find . \\( -name a \\) -exec rm {} \\; -delete` reaches the walk as one command whose words hold them.

    Beside a `$` it leaves the marks the expansion check reads once shlex has removed the quotes: _LITERAL_DOLLAR after a
    `$` that is single-quoted, escaped, or last in double quotes (no expansion in either shell), _QUOTED_DOLLAR after the `$` of
    `$'...'` and `$"..."` (text the hook does not decode: ansi_c_quotes has written each `$'...'` it decodes as a literal),
    and _NAME_END where a quote or an escape continues a word right after a
    bare `$name` (`$X"t"` and `$X\\t` read $X, then t).  After the name of a `$name` in double quotes it leaves
    _QUOTED_NAME (SPD-167): bash splits an unquoted expansion's value at its blanks and never a quoted one's, and zsh splits
    neither, so a reader of the words the shell passes (shell/stdin_text) knows which shell reads a word as one; a plain
    `"${name}"` is marked the same way with its braces left plain, since they only delimit the name, where they once
    reached every reader as quoted glob sentinels and arg_writes.resolved never settled the name (SPD-141).  After a
    lifted substitution's placeholder in double quotes it leaves _QUOTED_SUBST, for the same reason (SPD-146)."""
    out = []
    i, n = 0, len(text)
    state = None  # None, "'" or '"'

    def end_name(k):
        # the word goes on after the quotes from text[k] with a name character: a `$name` just written ends here
        while k < n and text[k] in "'\"":
            k += 1
        if k < n and syntax._NAME_CHAR_RE.match(text[k]) and syntax._BARE_NAME_TAIL_RE.search("".join(out[-64:])):
            out.append(syntax._NAME_END)

    while i < n:
        c = text[i]
        if state == "'":
            out.append(_QUOTED_SENTINELS.get(c, c))
            if c == "'":
                state = None
            elif c == "$":
                out.append(syntax._LITERAL_DOLLAR)
            i += 1
        elif c == "\\" and i + 1 < n and state != "'":
            nxt = text[i + 1]
            if syntax._NAME_CHAR_RE.match(nxt) and syntax._BARE_NAME_TAIL_RE.search("".join(out[-64:])):
                out.append(syntax._NAME_END)
            if state == '"' and nxt not in '$`"\\\n':
                out.append(c)  # inside "" a backslash before an ordinary character stays literal
                out.append(_QUOTED_SENTINELS.get(nxt, nxt))
            else:
                out.append(c)
                out.append(_QUOTED_SENTINELS.get(nxt, nxt))
            if nxt == "$":
                out.append(syntax._LITERAL_DOLLAR)
            i += 2
        elif state == '"' and c == "$" and i + 1 < n and syntax._NAME_RE.match(text, i + 1):
            name = syntax._NAME_RE.match(text, i + 1).group()
            out.append("$" + name + syntax._QUOTED_NAME)  # `"$X"`: one word in both shells, whatever X holds
            i += 1 + len(name)
        elif state == '"' and c == "$" and i + 1 < n and syntax._BRACED_NAME_RE.match(text, i + 1):
            braced = syntax._BRACED_NAME_RE.match(text, i + 1).group()
            out.append("$" + braced + syntax._QUOTED_NAME)  # `"${X}"` is `"$X"`: its braces only delimit the name (SPD-141)
            i += 1 + len(braced)
        elif state == '"' and c == "_" and text.startswith(hookio.SUBST, i):
            out.append(hookio.SUBST + syntax._QUOTED_SUBST)  # `"$(x)"`: its output one word in both shells (SPD-146)
            i += len(hookio.SUBST)
        elif state == '"':
            if c == '"':
                if out and out[-1] == "$":
                    out.append(syntax._LITERAL_DOLLAR)  # `"cost $"`: a dollar last in double quotes is literal
                end_name(i + 1)
                state = None
            out.append(_QUOTED_SENTINELS.get(c, c))
            i += 1
        elif c in "'\"":
            end_name(i)
            state = c
            out.append(c)
            i += 1
        elif c == "$" and i + 1 < n and text[i + 1] in "'\"":
            out.append(c)
            out.append(syntax._QUOTED_DOLLAR)
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def deglob(text):
    """Restore the characters neutralize_quoted_globs and mark_zsh_patterns replaced with sentinels (a no-op for text that has none)."""
    if not text:
        return text
    return syntax._GLOB_SENTINEL_RE.sub(lambda m: syntax._SENTINEL_TEXT[m.group()], text)


# What requoted escapes: each character that ends a word or changes the reading around it wherever it stands unquoted and
# that a word can hold with no sentinel of its own -- shlex's blanks, both quotes and the backslash, a backtick (which
# split_substitutions would lift as a body) and `#` (which opens a comment at a word's start in newlines_as_separators).  A
# newline is single-quoted instead, since newlines_as_separators joins a backslash-newline's lines.
_REQUOTE = str.maketrans({**{c: "\\" + c for c in " \t\r'\"\\`#"}, "\n": "'\n'"})


def requoted(word):
    """Shell text the reading turns back into exactly this masked word, for text the hook reads again with words it has
    already tokenized set into it: the body of an alias followed by the words the line spelled after the alias's name,
    whether the Bash tool's shell holds the alias (expansions.shell_aliased) or the line defined it for eval
    (analyse.dispatch_words) -- SPD-201.  The shell expands an alias textually, before it parses the words after it, so
    they keep their quotes: `gc -m "don't"` runs `git commit --verbose -m "don't"`, and a quoted word holding blanks, `;`,
    `>`, a newline, `$( )` or backticks stays one word (probed through tests/probes/shell_probe.py, AliasWordsTest).
    Joined raw, with their quotes gone, the words read as a line the hook could not tokenize, or as more words and more
    commands than the shell runs.  A reading that hands tokenized words to text a program joins them into itself (eval's
    words, `npm explore`'s) joins them raw, as that program does.

    The sentinels stay as they are, so a quoted glob or operator character stays quoted and a literal dollar literal, and
    what the line left active -- an unquoted glob, a `$NAME`, a lifted substitution -- stays active; only the characters
    that carry no sentinel are escaped.  An empty word is `''`, which shlex keeps as a word."""
    return word.translate(_REQUOTE) if word else "''"
