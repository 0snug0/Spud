"""shell/prepare: Heredocs, substitutions, newlines and quoted globs before tokenizing."""

from . import syntax
from ..hooks import hookio


# What a quoted or escaped character becomes: a glob metacharacter's sentinel, or a shell operator character's, so a
# quoted `;` or `(` stays in its word instead of reaching the walk as the operator.
_QUOTED_SENTINELS = dict(syntax._GLOB_SENTINELS, **syntax._PUNCT_SENTINELS)


def strip_heredocs(command):
    """Remove here-document bodies from the command text; return (text, bodies) in order."""
    lines = command.split("\n")
    out, bodies = [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        words = [(m.group(1) if m.group(1) is not None else (m.group(2) if m.group(2) is not None else m.group(3).lstrip("\\"))) for m in syntax.HEREDOC_RE.finditer(line)]
        i += 1
        for word in words:
            body = []
            while i < len(lines) and lines[i].lstrip("\t") != word:
                body.append(lines[i])
                i += 1
            bodies.append("\n".join(body))
            i += 1
    return "\n".join(out), bodies


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
            depth, j = 0, i + 1
            while j < n:
                if command[j] == "(":
                    depth += 1
                elif command[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            inner.append(command[i + 2 : j])
            out.append(hookio.SUBST)
            i = j + 1
            continue
        if c == "`":
            j = command.find("`", i + 1)
            if j == -1:
                j = n
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
    blanked, so an apostrophe in it cannot unbalance shlex, which gets no commenters."""
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
    `$'...'` and `$"..."` (text the hook does not decode), and _NAME_END where a quote or an escape continues a word right after a
    bare `$name` (`$X"t"` and `$X\\t` read $X, then t).  After the name of a `$name` in double quotes it leaves
    _QUOTED_NAME (SPD-167): bash splits an unquoted expansion's value at its blanks and never a quoted one's, and zsh splits
    neither, so a reader of the words the shell passes (shell/stdin_text) knows which shell reads a word as one."""
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
