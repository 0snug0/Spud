"""shell/alias_spellings: how the text being read writes each word, quoted or not, which decides the aliases it may run.

Taken out of shell/line_aliases past its 1000-line band (SPD-314), the part of it that reads the text's own words and
never the alias table: how the text being read spells a word -- unquoted, quoted, or quoted after its last dot
(spellings, quoted_name, quoted_too, quoted_words: SPD-295, SPD-308), read by line_aliases, alias_chains, analyse,
bash_rule, line_functions and walk; the words zsh reads unquoted, and which of them are plain, the only ones a global
alias's name can be (alias_words, plain_words: SPD-109, SPD-293), read by line_aliases.global_aliased, alias_chains and
walk; and whether a global alias's name is one a word can spell at all (alias_word_name), read where a line or the
snapshot defines one.  It imports nothing of the shell's reading but prepare and syntax, so the import cycle's modules
reach it and it reaches none of them."""

from . import prepare, syntax


# The characters that end a word where zsh reads text unquoted, and those that keep a word from being a global alias's
# name: a quote, an escape, an expansion or a substitution anywhere in it (`'gp'`, `g\\p`, `g'p'` and `${gp}` were each
# left as written, probed).
_WORD_ENDS = frozenset(" \t\n;&|<>()")


def alias_word_name(name):
    """Whether a global alias's name is one a word can spell unquoted (`...`, `G`), which zsh then expands; one holding a
    blank, an operator or a quote never matches a word (probed: `alias -g 'a b'=x; eval 'echo a b'` printed `a b`)."""
    return bool(name) and not any(c in _WORD_ENDS or c in "'\"\\$`=" for c in name)


# SPD-295: zsh expands no plain alias of a command word quoted in any way -- a quote or a backslash anywhere in it -- and
# no suffix alias of one quoted or escaped anywhere after its last dot, the line's own aliases as the snapshot's (probed
# in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py, the snapshot sourced and the line eval'd
# as the Bash tool's shell runs it: `\ls`, `l''s`, `"ls"`, `'ls'`, `ls''` and `$'ls'` ran the real ls, in eval and a
# `$( )` too, and `eval "'ls'"` did under the line's own `alias ls=...`; `'a.cfg'`, `a.'cfg'`, `a.cf\g` and `a.cfg''` ran
# no `alias -s cfg`, `a\.cfg`, `"a".cfg`, `\a.cfg` and `'a'.cfg` ran it).  The tokens the walk reads have their quotes
# taken (`'ls'`, `\ls` and `ls` are one token), so the mark is kept on the text instead: how the text being read spells
# each word, found by a scan of it (spellings).  The text is the one analyse_command tokenizes
# (ShellAnalysis.quoted_text), its comments' quote characters blanked, its newlines LINE_BREAK and its `$( )` and
# backtick bodies lifted out, each read as a text of its own; a `<( )` body's words are the line's own tokens, and read
# here with the rest.  A brace ends a word here, where zsh may split one off (`{ls}`), so a word glued to one counts
# unquoted too.
#
# SPD-308: zsh reads each word's own quotes where it parses it, so a text that spells a name both ways runs the alias
# where it is unquoted and the command the name spells where it is quoted (probed as above: under `alias ls='echo
# ALIASED'`, `'ls' -d /; ls -d /` printed `/`, then `ALIASED -d /`, and the other order the other way; with `function
# a.txt` and `alias -s txt=...`, `'a.txt' q1; a.txt q2` ran the function, then the suffix alias).  SPD-295 read every
# token of such a name as the alias, which let `'git' push; git status` through under a profile's `alias git=hub`
# while zsh pushed.  A token does not say which spelling it came from, so where the text has more than one for its word
# the word is read as each of them runs: the alias an unquoted one runs, then on as a quoted one runs, its suffix alias
# where one written with that suffix unquoted may run it, and the command it names (analyse.dispatch_words,
# held_text.read_shell_name).  That reads more than runs, never less.  A token carrying its own mark instead -- a
# sentinel written into each quoted word before shlex splits the text, or a str subclass -- was weighed and left: a
# sentinel inside the word reaches every reading by name that does not pass through prepare.deglob (the wrapper and
# reserved-word tables, an assignment word, a redirection operator), and a subclass's mark is gone from every token the
# walk rebuilds (a brace split off, `{'git' push}`; a closer glued on), where the reading of both ways is needed still.
UNQUOTED, QUOTED_STEM, QUOTED_SUFFIX = 1, 2, 4  # a word as written: unquoted; quoted, but not after its last dot; after it
_ANY_SPELLING = UNQUOTED | QUOTED_STEM | QUOTED_SUFFIX
_SCAN_ENDS = _WORD_ENDS | {"{", "}", syntax.LINE_BREAK}


def spellings(a, name):
    """How the text being read writes the word `name` wherever it stands, the ways zsh then reads it (SPD-295, SPD-308):
    UNQUOTED, QUOTED_STEM (quoted, but not after its last dot, or it has none) and QUOTED_SUFFIX (quoted after its last
    dot), or'd -- UNQUOTED alone for a word it never writes quoted, and every one where the hook cannot see the quotes
    of the words it reads (quoted_words)."""
    written = quoted_words(a)
    return _ANY_SPELLING if written is None else written.get(prepare.deglob(name), UNQUOTED)


def quoted_name(a, name):
    """Whether the text being read spells the command word `name` only quoted (spellings), so no plain alias of it stands
    there (SPD-295)."""
    return not spellings(a, name) & UNQUOTED


def quoted_too(a, name):
    """Whether the text being read spells the command word `name` quoted as well as unquoted (spellings), so the word the
    reading has may be either, and is read as each runs: the alias, and on as a quoted word (SPD-308)."""
    written = spellings(a, name)
    return written != UNQUOTED and bool(written & UNQUOTED)


def quoted_words(a):
    """{each word the text being read writes quoted at least once: how it writes it (spellings)} -- SPD-295, SPD-308,
    above -- found once per text, at the first lookup that asks (ShellAnalysis.quoted_sets); none where no word of it is
    quoted.  None -- every word may be written any way -- where the reading has no text (a function body the line
    defines, where the walk that read it is not reading its text still: analyse.analyse_command), and in a `<( )` or `>(
    )` body where a global alias set words the text does not spell (under `alias -g QP="'git' push"`, `cat <(QP)` runs
    git's push), which the walk reads with no text until the body closes (ShellWalk.open_process_substitution, SPD-315:
    every other body is read by the text's quotes, as a `$( )` body is by its own)."""
    text = a.quoted_text
    if text is None:
        return None
    if a.quoted_sets is None:
        a.quoted_sets = _quoted_words(text) if any(q in text for q in "'\"\\") else {}
    return a.quoted_sets


def _quoted_words(text):
    """quoted_words' scan of `text`: each word as the shell splits it, taken apart into its value and whether any of it was
    quoted, and, for one that was, whether the text after its last dot was."""
    written = {}
    i, n = 0, len(text)
    while i < n:
        if text[i] in _SCAN_ENDS:
            i += 1
            continue
        start, value, quoted = i, [], False
        while i < n and text[i] not in _SCAN_ENDS:
            c = text[i]
            if c == "'":
                end = text.find("'", i + 1)
                end = n if end < 0 else end
                value.append(text[i + 1 : end])
                i, quoted = end + 1, True
            elif c == '"':
                i, quoted = _double_quoted(text, i + 1, value), True
            elif c == "\\":
                value.append(text[i + 1 : i + 2])
                i, quoted = i + 2, True
            else:
                value.append(c)
                i += 1
        word, spelled = "".join(value), UNQUOTED
        if quoted:
            raw = text[start:i]
            dot = raw.rfind(".")  # the same dot is the last of the word's value, a quote adding or taking none
            spelled = QUOTED_SUFFIX if dot >= 0 and any(q in raw[dot + 1 :] for q in "'\"\\") else QUOTED_STEM
        written[word] = written.get(word, 0) | spelled
    return {word: spelled for word, spelled in written.items() if spelled != UNQUOTED}


def _double_quoted(text, i, value):
    """The index past the `"` that closes a double-quoted string whose text starts at text[i], its value appended to
    `value`: a backslash escapes only `"`, `\\`, `$` and a backtick there."""
    n = len(text)
    while i < n and text[i] != '"':
        if text[i] == "\\" and text[i + 1 : i + 2] in ('"', "\\", "$", "`"):
            value.append(text[i + 1])
            i += 2
        else:
            value.append(text[i])
            i += 1
    return i + 1


def alias_words(text, bodies=None):
    """[(start, end, plain)] for each word of `text` where zsh reads it unquoted, `plain` when it holds no quote, escape,
    expansion or substitution, which a global alias's name alone can be.  A comment is no word, and neither is arithmetic
    `(( ))` or the text of a `$( )`, backticks, `${ }` or `$'...'`, which a word holding it is not plain for; a substitution's
    body is read again on its own, where its words are expanded (probed: `echo $(echo gp)` and `` echo `echo gp` `` ran the
    alias, `(( gp == 5 ))` read the variable).  A case pattern is read as a word, which zsh does not expand there: a
    refusal of more than runs, never less.

    SPD-293: nor is the body of a `<( )` or `>( )`, which zsh parses when it runs it, with the table as it stands then
    (probed: with `alias -g X=snapshot` held, `alias -g X=line; cat <(echo p1 X); echo top X` printed `p1 line`, then `top
    snapshot`), so the walk expands its words where it opens it (shell/walk ShellWalk.open_process_substitution) and the
    text around it leaves them be.  Its closing `)` is found as a `$( )`'s is (prepare.substitution_end).  `bodies`, a list:
    the words inside such bodies are read and not skipped, and each body's (start, end) is appended to it (plain_words)."""
    spans, i, n, start, plain = [], 0, len(text), None, True
    while i < n:
        c = text[i]
        if c in _WORD_ENDS:
            if start is not None:
                spans.append((start, i, plain))
                start = None
            if c in "<>" and text.startswith("(", i + 1):
                end = prepare.substitution_end(text, i)
                if bodies is None:
                    i = end + 1
                else:
                    bodies.append((i + 2, end))
                    i += 2
            elif text.startswith("((", i):
                end = text.find("))", i + 2)
                i = n if end < 0 else end + 2
            else:
                i += 1
            continue
        if start is None:
            if c == "#":
                end = text.find("\n", i)
                i = n if end < 0 else end
                continue
            start, plain = i, True
        if c == "'":
            end = text.find("'", i + 1)
            i, plain = (n if end < 0 else end + 1), False
        elif c == '"':
            i, plain = _double_quote_end(text, i + 1), False
        elif c == "\\":
            i, plain = i + 2, False
        elif c == "`":
            i, plain = prepare.backtick_end(text, i) + 1, False
        elif c == "$":
            i, plain = _dollar_end(text, i), False
        else:
            i += 1
    if start is not None:
        spans.append((start, n, plain))
    return spans


def _double_quote_end(text, i):
    """The index past the `"` that closes a double-quoted string whose text starts at text[i]."""
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            return i + 1
        if c == "\\":
            i += 2
        elif c == "`":
            i = prepare.backtick_end(text, i) + 1
        elif text.startswith("$(", i):
            i = prepare.substitution_end(text, i) + 1
        else:
            i += 1
    return n


def _dollar_end(text, i):
    """The index past what the `$` at text[i] opens: a `$( )` or `$(( ))`, a `${ }` (its braces counted), a `$'...'`."""
    if text.startswith("$(", i):
        return prepare.substitution_end(text, i) + 1
    if text.startswith("${", i):
        depth, j, n = 0, i + 1, len(text)
        while j < n:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
            if depth == 0:
                break
        return j
    if text.startswith("$'", i):
        return prepare.ansi_c_end(text, i + 2) + 1
    return i + 1


def plain_words(text):
    """The words `text` writes unquoted (alias_words), those inside a `<( )` or `>( )` body among them: the ones the walk
    may take for a global alias's name in such a body, since its tokens no longer show which word the line quoted (`'X'`
    and `X` are one token).  Every such word of the text, not only a body's, since a case pattern's `)` in a body ends it
    where a count of parentheses would place it (alias_words): a word written quoted in a body and unquoted elsewhere is
    expanded in the body too, more than zsh runs, never less."""
    return {text[s:e] for s, e, plain in alias_words(text, []) if plain}
