"""shell/positional: the words a call hands a function of the shell's, set where its body reads them (SPD-203).

A function receives its call's words as its positional parameters, so a body that hands them on -- this Mac's oh-my-zsh
`__git_prompt_git () { GIT_OPTIONAL_LOCKS=0 command git "$@" }` -- runs whatever the member wrote after its name:
`__git_prompt_git push` pushes.  Read as it stands, that body is `git $@`, a verb the hook cannot resolve, and the finding
that says so is dropped from text the shell holds (analyse.analyse_shell_text), so the push passed Law 7.  The body is
read instead as the shell runs it for this call: each reference to the parameters replaced by the call's own words,
quoted again as the line spelled them (prepare.requoted), so `__git_prompt_git push` reads as `git push` and
`__git_prompt_git status` as `git status`.

Probed 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual and
-f, which printed the same, and in GNU bash 3.2.57, with `show () { printf '%s:' "$#"; printf '<%s>' "$@"; }`:

- `"$@"` is each word whole, an empty one kept (`<a b><><c>` for `'a b' '' c`) and none at all for no words; glued text
  joins the first and the last (`"x$@y"` gave `<xa><by>`, and `<xy>` for no words); inside a longer string the first word
  takes what stands before it and the last what stands after (`"a $@ b"` gave `<a x><y b>`, `<a x><><y b>` for `x '' y`,
  and `<a  b>` for no words or one empty one);
  `"$*"` is one word, the words joined by a blank (`<a b  c>`), and one empty word for none.  All three shells agree.
- unquoted, `$@`, `$*` and `$1` drop an empty word and zsh splits none (`<a b><c>`), where bash splits each at its blanks
  and globs it (`<a><b><c>`); a word glued to text keeps an empty neighbour (`x$@` gave `<x><b>` for `'' b`).
- `$#` counts the words; `${1+"$@"}` is `"$@"` (`<a b><><c>`, nothing for no words); `${2:+z}`, `${2-w}` and
  `${1:-v}` choose as their operators say in both shells; `${@:$#}`, `${@:2}` and `"${@:2:1}"` are the last word, the
  words from the second, the second alone.
- where the shells part, the body is not substituted: zsh reads `$1:t` as a modifier (`<e>` for `d/e`; bash `<d/e:t>`),
  `$10` as the tenth word (`<j>`; bash `$1` then `0`), `$@[1]` as a subscript, `$#name` as a length (`<3>` for abc; bash
  `<2name>`), `${@:0}` with `$0`, `${@:-.}` for an empty word (zsh gave nothing, bash `<.>`; both the words for `a b`
  and `<.>` for none), and `${@+x}` and `${@-y}` for no words at all (zsh `<x>`, bash `<y>`).
- the parameters move under `shift` (`<b><c>` for `a b c`) and `set --` (`<q><r>`); `for a; do` walks them with no list
  (`<a>`, `<b c>`); a function defined in the body, zsh's anonymous `() { ... } z` among them, reads its own (`<x>`,
  `<z>`); a here-document's `[$1]` expands only where its delimiter is unquoted; a backtick body takes one backslash off
  the text it holds (`` `printf '<%s>' h\\i` `` gave `<hi>`, `$( )` `<h\\i>`); a single-quoted string is another
  reading's (`sh -c '... "$1"' _ "$@"` printed `<m n>`).  A body with any of these is not substituted.

`substituted` answers (the text, True) where every reference is one it sets soundly, and (the body as it stands, False)
where one is not: analyse.analyse_shell_text then keeps every finding the body earns for a call that has words, since
the member's words reach it in a way the hook does not follow.  `substitution` answers the same and, beside it, the text
of every `$( )` it set words in (SPD-258): once set, `V=$(echo "$1")` is `V=$(echo push)`, a value holding no positional,
so the reading could not tell the member's substitution from the body's own `V=$(echo status)` without it.

A module of its own, off the analyse cycle (it reads prepare and syntax alone), and kept whole past the ~250-line mark
(the spudlib-modules size rule): one scanner of a body's quoting, whose parts each read the others' spans."""

import re

from . import prepare, syntax
from ..core import lazy


# A reference to the positional parameters: `$1`..`$9`, `$@`, `$*`, `$#`, every braced form of them (`${1}`, `${10}`,
# `${@:2}`, `${1+"$@"}`, `${#}`, `${#1}`, `${(q)@}`, `${=1}`), and zsh's `argv`, which is them by name.  `$0` is not one:
# zsh sets it to the function's own name.  `${#name}` is a length, and not `${#}` followed by a name.
REFERENCE_RE = lazy.LazyPattern(r"\$(?:[1-9@*#]|\{(?:[=~^]|\([^)]*\))*[#!]?(?:[1-9][0-9]*|[@*#])(?![A-Za-z0-9_]))|\bargv\b")
# What reads the parameters with no reference to them: a loop that walks them with no list of its own (`for f; do`),
# `getopts` and zsh's `zparseopts`, which parse them, and zsh's `argv`, which is them by name.
READS_RE = lazy.LazyPattern(r"\b(?:for|select|foreach)\s+(?!in\b)[A-Za-z_]\w*+(?:\s+(?!in\b)[A-Za-z_]\w*+)*\s*(?:;|\{|\bdo\b)"
                      r"|(?:^|[\s;&|({!])(?:getopts|zparseopts)(?=[\s;&|)}]|\Z)|\bargv\b")
# What moves them before a reference reads them, or holds a reference the substitution does not follow: `shift` and
# `set`, a function defined inside the body (whose own call's they are there), and a here-document.
MOVES_RE = lazy.LazyPattern(r"(?:^|[\s;&|({!])(?:shift|set)(?=[\s;&|)}]|\Z)"
                      r"|\bfunction\s|(?:^|[\s;&|({])[^\s;&|(){}<>'\"=$`]*\(\s*\)|<<(?!<)")
# The most bodies of one function name a line reads with the words set, one per call's words (analyse.read_shell_name):
# a body that calls functions with words of its own reads each of those once per words, so a profile could multiply the
# readings through every level of the analysis's depth.  Past it the body is read once more as it stands.  The same
# bound holds the walks an analysis makes to read the bodies of the functions a line defines on their calls' inputs
# (SPD-212, walk.walk_line): past it such a line's bodies are read once more on input the line does not spell.
READINGS_PER_NAME = 8
_SEPARATORS = " \t\n;&|()<>"  # what ends a word unquoted, and before a `#` opens a comment (prepare._COMMENT_AFTER)
_NUMBER_RE = lazy.LazyPattern(r"[1-9][0-9]*\Z")
_TEST_RE = lazy.LazyPattern(r"([1-9][0-9]*)(:?[-+])(.*)\Z", re.S)  # `${1-w}`, `${1:-w}`, `${1+w}`, `${1:+w}`
_ALL_OR_RE = lazy.LazyPattern(r"([@*]):-(.*)\Z", re.S)  # `${@:-.}`: the words, or the alternative for none
_SLICE_RE = lazy.LazyPattern(r"([@*]):([1-9][0-9]*|\$#)(?::([0-9]+|\$#))?\Z")  # `${@:2}`, `${@:$#}`, `${*:2:1}`
_COUNT_RE = lazy.LazyPattern(r"\$\{#\}|\$#(?![A-Za-z0-9_{\[@*#?!$-])")
_AFTER_COUNT = lazy.LazyPattern(r"[A-Za-z0-9_{\[@*#?!$-]")  # after `$#`: zsh's `$#name`, a length, and not the count
_MODIFIER_RE = lazy.LazyPattern(r"\[|:[A-Za-z&]")  # after a bare `$1`, `$@` or `$*`: zsh's subscript or modifier
# In a word bash splits or globs where it stands unquoted, as zsh does not: a blank, a `"$NAME"` the line quoted (its
# value), or a glob character the line quoted.
_SPLIT_CHARS = frozenset(" \t\n" + syntax._QUOTED_NAME + "".join(syntax._GLOB_SENTINELS[c] for c in "*?["))


class Unreadable(Exception):
    """A reference the substitution cannot set as the shells would."""


def substituted(body, words):
    """(the body with `words` -- the call's own, masked as the line's reading tokenized them -- set where it reads its
    positional parameters, True), or (the body as it stands, False) where a reference is one the substitution does not
    read or the body moves the parameters before it reads them."""
    return substitution(body, words)[:2]


def substitution(body, words):
    """substituted's (text, sound), and the text of each `$( )` in that text that held a reference the words were set in,
    as prepare.split_substitutions lifts it (SPD-258): a value such a substitution fills is the member's.  A `$( )` inside
    another yields both texts, the outer one holding the inner as set."""
    if READS_RE.search(body) is not None:
        return body, False, ()
    if REFERENCE_RE.search(body) is None:
        return body, True, ()
    if MOVES_RE.search(body) is not None:
        return body, False, ()
    call = _Call(list(words))
    try:
        return call.text(body), True, tuple(call.filled)
    except Unreadable:
        return body, False, ()


def _splits(word):
    return not _SPLIT_CHARS.isdisjoint(word)


def _globs(word):
    return syntax.GLOB_RE.search(word) is not None


def _glued(t, start, end):
    """Whether the text at t[start:end] is glued to a word character on either side."""
    return (start > 0 and t[start - 1] not in _SEPARATORS) or (end < len(t) and t[end] not in _SEPARATORS)


class _Call:
    """One call's words, set into the text its function runs."""

    def __init__(self, words):
        self.words = words
        self.filled = []  # the text of each `$( )` a reference in it was set in (substitution)

    def text(self, t):
        """Shell text read unquoted, with every reference in it set."""
        out, i, n = [], 0, len(t)
        while i < n:
            c = t[i]
            if c == "\\":
                out.append(t[i : i + 2])
                i += 2
            elif c == "'":
                j = t.find("'", i + 1)
                if j < 0:
                    raise Unreadable(t[i:])
                out.append(t[i : j + 1])
                i = j + 1
            elif c == '"':
                piece, i = self.quoted(t, i)
                out.append(piece)
            elif c == "#" and (i == 0 or t[i - 1] in _SEPARATORS):
                j = t.find("\n", i)
                j = n if j < 0 else j
                out.append(t[i:j])
                i = j
            elif c == "`":
                j = self.backticks(t, i)
                out.append(t[i : j + 1])
                i = j + 1
            elif c == "$":
                kind, value, j = self.dollar(t, i, quoted=False)
                out.append(value if kind == "raw" else self.unquoted(t, i, j, kind, value))
                i = j
            else:
                out.append(c)
                i += 1
        return "".join(out)

    def unquoted(self, t, start, end, kind, value):
        """A reference that stands unquoted: each word whole, an empty one dropped, as zsh reads it."""
        words = [value] if kind == "word" else value
        glued = _glued(t, start, end)
        if any(_splits(w) for w in words) or (glued and any(_globs(w) for w in words)) \
                or (glued and len(words) > 1 and not all(words)):
            raise Unreadable(t[start:end])
        return " ".join(prepare.requoted(w) for w in words if w)

    def quoted(self, t, i):
        """(the double-quoted string at t[i] with its references set, the index after it)."""
        pieces, j, n = [], i + 1, len(t)
        while True:
            if j >= n:
                raise Unreadable(t[i:])
            c = t[j]
            if c == '"':
                break
            if c == "\\":
                pieces.append(("raw", t[j : j + 2]))
                j += 2
            elif c == "`":
                k = self.backticks(t, j)
                pieces.append(("raw", t[j : k + 1]))
                j = k + 1
            elif c == "$":
                kind, value, j = self.dollar(t, j, quoted=True)
                pieces.append((kind, " ".join(value) if kind == "joined" else value))
            else:
                pieces.append(("raw", c))
                j += 1
        end = j + 1
        if len(pieces) == 1 and pieces[0][0] != "raw":
            # the string is one reference: its words stand as words of their own, glued to what stands beside the quotes
            kind, value = pieces[0]
            words = value if kind == "list" else [value]
            if _glued(t, i, end) and any(_globs(w) for w in words):
                raise Unreadable(t[i:end])
            return " ".join(prepare.requoted(w) for w in words), end
        out = ['"']
        for kind, value in pieces:
            if kind == "raw":
                out.append(value)
                continue
            words = value if kind == "list" else [value]
            if any(_globs(w) for w in words):
                raise Unreadable(t[i:end])
            # the quotes close before the words and open again after them, so each keeps the quoting the line gave it and
            # an empty one of `$@` stays a word of its own (`"a $@ b"` is `a x`, ``, `y b` for `x '' y`)
            if kind == "list":
                out.append('"' + " ".join(prepare.requoted(w) for w in words) + '"')
            else:
                out.append('"' + (prepare.requoted(value) if value else "") + '"')
        out.append('"')
        return "".join(out), end

    def backticks(self, t, i):
        """The index of the backtick closing the one at t[i]: its body takes a backslash off the text in it, which
        would take one off each word set there, so a reference inside it is not read."""
        j = prepare.backtick_end(t, i)
        if j >= len(t) or REFERENCE_RE.search(t, i, j) is not None:
            raise Unreadable(t[i:j])
        return j

    def dollar(self, t, i, quoted):
        """(kind, value, the index after it) for the `$` at t[i]: ("raw", text) for text set or copied as it stands,
        ("list", words) for `$@`, ("joined", words) for `$*`, ("word", the word or "") for `$1`."""
        n, nxt = len(t), t[i + 1 : i + 2]
        if nxt in ("@", "*"):
            self.after_bare(t, i + 2)
            return ("list" if nxt == "@" else "joined"), self.words, i + 2
        if nxt.isdigit() and nxt != "0":
            if t[i + 2 : i + 3].isdigit():
                raise Unreadable(t[i : i + 3])  # zsh's `$10`, bash's `$1` and `0`
            self.after_bare(t, i + 2)
            return "word", self.word(int(nxt)), i + 2
        if nxt == "#":
            if _AFTER_COUNT.match(t, i + 2):
                raise Unreadable(t[i : i + 3])
            return "raw", str(len(self.words)), i + 2
        if nxt == "{":
            return self.braced(t, i, quoted)
        if nxt == "(":
            j = prepare.substitution_end(t, i)
            if j >= n:
                raise Unreadable(t[i:])
            if t.startswith("$((", i):
                # arithmetic: the count is a number there, and a word set into it would be evaluated as an expression
                span = t[i : j + 1]
                if any(m.group() not in ("$#", "${#}") for m in REFERENCE_RE.finditer(span)):
                    raise Unreadable(span)
                return "raw", _COUNT_RE.sub(str(len(self.words)), span), j + 1
            inner = self.text(t[i + 2 : j])  # its own quoting, as split_substitutions lifts it
            if REFERENCE_RE.search(t, i + 2, j) is not None:
                self.filled.append(inner)
            return "raw", "$(" + inner + ")", j + 1
        if nxt == "'" and not quoted:
            j = prepare.ansi_c_end(t, i + 2)
            if j >= n:
                raise Unreadable(t[i:])
            return "raw", t[i : j + 1], j + 1
        if nxt == "$":
            return "raw", "$$", i + 2
        return "raw", "$", i + 1

    def after_bare(self, t, j):
        if _MODIFIER_RE.match(t, j):
            raise Unreadable(t[j : j + 2])

    def word(self, number):
        return self.words[number - 1] if number <= len(self.words) else ""

    def braced(self, t, i, quoted):
        """A `${ ... }` at t[i]: a reference the substitution reads, set; any other, copied as it stands when it holds
        none."""
        j = _brace_end(t, i)
        if j < 0:
            raise Unreadable(t[i:])
        content, end = t[i + 2 : j], j + 1
        if content in ("@", "*"):
            return ("list" if content == "@" else "joined"), self.words, end
        if content == "#":
            return "raw", str(len(self.words)), end
        if _NUMBER_RE.match(content):
            return "word", self.word(int(content)), end
        m = _SLICE_RE.match(content)
        if m is not None:
            count = len(self.words)
            start = count if m.group(2) == "$#" else int(m.group(2))
            if start < 1:
                raise Unreadable(t[i:end])  # `${@:0}` holds zsh's `$0`
            length = None if m.group(3) is None else (count if m.group(3) == "$#" else int(m.group(3)))
            chosen = self.words[start - 1 :] if length is None else self.words[start - 1 : start - 1 + length]
            return ("list" if m.group(1) == "@" else "joined"), chosen, end
        m = _TEST_RE.match(content)
        if m is not None:
            return self.tested(t, i, end, int(m.group(1)), m.group(2), m.group(3), quoted)
        m = _ALL_OR_RE.match(content)
        if m is not None:
            if "" in self.words:
                raise Unreadable(t[i:end])  # zsh drops an empty word there, where bash takes the words for none
            if self.words:
                return ("list" if m.group(1) == "@" else "joined"), self.words, end
            return self.alternative(t, i, end, m.group(2), quoted)
        if REFERENCE_RE.search(t, i, end) is not None:
            raise Unreadable(t[i:end])
        return "raw", t[i:end], end

    def tested(self, t, i, end, number, operator, alternative, quoted):
        """`${1-w}`, `${1:-w}`, `${1+w}` and `${1:+w}`: the word or the alternative, as the operator chooses."""
        present = number <= len(self.words)
        if operator.startswith(":"):
            present = present and self.words[number - 1] != ""
        if operator.endswith("-") and present:
            return "word", self.words[number - 1], end
        if operator.endswith("+") and not present:
            return "raw", "", end
        return self.alternative(t, i, end, alternative, quoted)

    def alternative(self, t, i, end, alternative, quoted):
        """The text a `${ }` at t[i:end] chose instead of the parameter: quoted, as plain text alone; unquoted, as shell text
        with its own references set."""
        if quoted:
            if re.search(r"[\"'`\\]", alternative) or REFERENCE_RE.search(alternative):
                raise Unreadable(t[i:end])
            return "raw", alternative, end
        if re.search(r"\s", alternative) or (REFERENCE_RE.search(alternative) and _glued(t, i, end)
                                             and any(_globs(w) for w in self.words)):
            raise Unreadable(t[i:end])  # zsh keeps a blank in it, where the text set here would split there
        return "raw", self.text(alternative), end


def _brace_end(t, i):
    """The index of the `}` closing the `${` at t[i], its quotes, escapes, substitutions and braces read, or -1."""
    depth, j, n = 1, i + 2, len(t)
    while j < n:
        c = t[j]
        if c == "\\":
            j += 2
            continue
        if c == "'":
            k = t.find("'", j + 1)
            if k < 0:
                return -1
            j = k + 1
            continue
        if c == '"':
            k = j + 1
            while k < n and t[k] != '"':
                k += 2 if t[k] == "\\" else 1
            if k >= n:
                return -1
            j = k + 1
            continue
        if t.startswith("$(", j):
            j = prepare.substitution_end(t, j) + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1
