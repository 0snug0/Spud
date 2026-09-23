"""shell/heredocs: where each here-document's body starts and ends, and the line without the bodies.

A body starts after the newline that ends its operator's command, not after the line the operator stands on (SPD-188): zsh
and bash read here-documents at the next newline their lexer reads as one, and a quoted word, a backslash-newline, a `$( )`,
backticks, a `${ }`, a `<( )`, an arithmetic expression or zsh's glob group spanning lines keeps the command going.  The
scan below reads the line as that lexer does, one frame per nested text: a command list (the line itself, a `$( )`, a `<(
)`), double quotes, a `${ }`, an arithmetic expansion.  A body read at the line's own level, or in a `<( )` on it, is taken
out of the text, in the order its operator stands, since the walk reads the words of both and takes one body per `<<` it
meets (ShellWalk.consume); a body inside a `$( )` or backticks stays in the text, which split_substitutions hands to that
substitution's own analysis.  A `$( )` ends where zsh ends it, past its bodies and quotes; bash 3.2 ends it at a body
line's `)` (probed), and so does split_substitutions, which counts parentheses (proposal 299), so the text between is read
as the outer line's commands, bash's reading.  tests/test_hooks.py HereDocumentBodyTest has the probes.

What the scan cannot tell from characters alone is what an open `(` is: a subshell or an array assignment, whose newline
ends a command, or zsh's glob group or an arithmetic command, whose newline does not (and whose `<<` is a shift there).
A `(` standing alone after a separator or an opening -- the line's start, a newline, `;`, `&`, `|`, `(` or `{` -- with no
case command on the line is a subshell (zsh.mark_zsh_patterns reads it so), and while only such are open the scan needs
nothing more.  Otherwise it asks the analysis's own reading: mark_zsh_patterns on the text read so far, the `(`s closed
after the newline or the `<<`, with and without it.  A line that asks more than _READINGS times reads on as command text,
which the walk reads fail closed.

One scanner whose frames share the characters they stop at, kept whole past the ~250-line mark for that reason (the
spudlib-modules size rule)."""

import re

from . import prepare, syntax, zsh

_READINGS = 32  # how often one line asks mark_zsh_patterns what an open `(` is: the pathological line reads on
# What mark_zsh_patterns writes for a newline zsh reads inside a word: a glob group's (_zsh_group), and an arithmetic
# command's or a case pattern's group's (_ARITH_MARKS, _PATTERN_MARKS).
_WORD_NEWLINES = (syntax._ZSH_SENTINELS["\n"], syntax._ARITH_SENTINELS["\n"])
_COMMAND_RE = re.compile(r"[\\'\"`$<>=()#\n]")  # the characters a command list's scan stops at
_QUOTED_RE = re.compile(r"[\\\"`$]")  # in double quotes
_BRACED_RE = re.compile(r"[\\'\"`${}]")  # in a `${ }`
_ARITH_RE = re.compile(r"[\\$()\[\]]")  # in an arithmetic expansion
_BACKTICK_RE = re.compile(r"[\\`]")
_ANSI_RE = re.compile(r"[\\']")  # in `$'...'`
_WORD_START = " \t\n;&|()<>"  # before a `#` that opens a comment (newlines_as_separators reads the same)
_DELIMITER_END = " \t\n;&|<>()"
_CASE_RE = re.compile(r"(?<![\w-])case(?![\w-])")
_COMMAND, _QUOTED, _BRACED, _ARITH = range(4)


class _Frame:
    """One nested text the scan is in.  A command list's: `strip` (its bodies leave the text), `closer` (`)` for a `$( )` or a
    `<( )`, None for the line), the here-documents whose bodies its next newline reads, whether each bare `(` open in it
    opens a subshell (`opens`, _Scan.subshell), and how many do not (`others`).  A `${ }`'s or an arithmetic expansion's:
    `closer` (`}`, `)` or `]`), and `depth` counts its own brackets."""

    __slots__ = ("kind", "strip", "closer", "pending", "opens", "others", "depth")

    def __init__(self, kind, strip=False, closer=None, depth=0):
        self.kind, self.strip, self.closer, self.pending, self.opens, self.others, self.depth = kind, strip, closer, [], [], 0, depth


def strip_heredocs(command):
    """Remove the here-document bodies read at the line's own level from the command text; return (text, bodies), the
    bodies in the order their operators stand."""
    if "<<" not in command:
        return command, []
    return _Scan(command).run()


class _Scan:
    """One pass over a command line, as zsh's lexer reads it, splitting off the bodies (strip_heredocs)."""

    def __init__(self, text):
        self.text, self.n = text, len(text)
        self.out, self.mark, self.bodies = [], 0, []
        self.readings = _READINGS
        case = _CASE_RE.search(text)
        self.case_at = case.start() if case else self.n  # where the line's first case command may stand

    def run(self):
        text, n = self.text, self.n
        stack = [_Frame(_COMMAND, strip=True)]
        i = 0
        while i < n:
            frame = stack[-1]
            if frame.kind == _COMMAND:
                i = self.command(stack, frame, i)
            elif frame.kind == _QUOTED:
                i = self.quoted(stack, i)
            elif frame.kind == _BRACED:
                i = self.braced(stack, frame, i)
            else:
                i = self.arithmetic(stack, frame, i)
        self.out.append(text[self.mark :])
        return "".join(self.out), self.bodies

    # -- the frames ---------------------------------------------------------------------------------------------------
    def command(self, stack, frame, i):
        """A command list's next stop from text[i]: the index to go on from."""
        text, n = self.text, self.n
        m = _COMMAND_RE.search(text, i)
        if m is None:
            return n
        i, c = m.start(), m.group()
        if c == "\\":
            return i + 2  # a backslash-newline joins the lines: no newline the lexer reads
        if c == "'":
            end = text.find("'", i + 1)
            return n if end < 0 else end + 1
        if c == '"':
            stack.append(_Frame(_QUOTED))
            return i + 1
        if c == "`":
            return self.backticks(i)
        if c == "$":
            return self.dollar(stack, i)
        if c == "#":
            if i == 0 or text[i - 1] in _WORD_START:  # a comment: the rest of the line
                end = text.find("\n", i)
                return n if end < 0 else end
            return i + 1
        if c == "\n":
            if frame.pending and not self.in_word(stack, i):
                return self.read_bodies(frame, i + 1)
            return i + 1
        if c == "(":
            subshell = self.subshell(i)
            frame.opens.append(subshell)
            frame.others += not subshell
            return i + 1
        if c == ")":
            if frame.opens:
                frame.others -= not frame.opens.pop()
            elif frame.closer:
                # a here-document the list opened and no newline of it read gets no body (probed: after `s=$(cat
                # <<EOF)` the next line ran as a command)
                stack.pop()
            return i + 1
        # `<`, `>` or `=`
        if text.startswith("<<", i):
            if text.startswith("<<<", i):
                return i + 3  # a here-string
            return self.operator(stack, frame, i)
        if text.startswith("(", i + 1) and (c == "<" or c == ">" and text[i - 1 : i] != "&"
                                            or c == "=" and (i == 0 or text[i - 1] in " \t\n;&|(")):
            # a process substitution: its own list, whose bodies the walk reads with the line's (`&>(` opens a zsh pattern,
            # zsh.mark_zsh_patterns)
            stack.append(_Frame(_COMMAND, strip=frame.strip, closer=")"))
            return i + 2
        return i + 1

    def quoted(self, stack, i):
        text, n = self.text, self.n
        m = _QUOTED_RE.search(text, i)
        if m is None:
            return n
        i, c = m.start(), m.group()
        if c == "\\":
            return i + 2
        if c == '"':
            stack.pop()
            return i + 1
        if c == "`":
            return self.backticks(i)
        return self.dollar(stack, i)

    def braced(self, stack, frame, i):
        text, n = self.text, self.n
        m = _BRACED_RE.search(text, i)
        if m is None:
            return n
        i, c = m.start(), m.group()
        if c == "\\":
            return i + 2
        if c == "'":
            end = text.find("'", i + 1)
            return n if end < 0 else end + 1
        if c == '"':
            stack.append(_Frame(_QUOTED))
            return i + 1
        if c == "`":
            return self.backticks(i)
        if c == "$":
            return self.dollar(stack, i)
        frame.depth += 1 if c == "{" else -1
        if not frame.depth:
            stack.pop()
        return i + 1

    def arithmetic(self, stack, frame, i):
        text, n = self.text, self.n
        m = _ARITH_RE.search(text, i)
        if m is None:
            return n
        i, c = m.start(), m.group()
        if c == "\\":
            return i + 2
        if c == "$":
            return self.dollar(stack, i)
        if c in "([":
            frame.depth += c == ("(" if frame.closer == ")" else "[")
            return i + 1
        if c == frame.closer:
            frame.depth -= 1
            if not frame.depth:
                stack.pop()
        return i + 1

    def dollar(self, stack, i):
        """A `$` in a command list, double quotes, a `${ }` or an arithmetic expansion: what it opens."""
        text = self.text
        nxt = text[i + 1 : i + 2]
        if text.startswith("$((", i):
            stack.append(_Frame(_ARITH, closer=")", depth=2))
            return i + 3
        if nxt == "(":
            stack.append(_Frame(_COMMAND, closer=")"))  # a command substitution: its bodies stay in its own text
            return i + 2
        if nxt == "{":
            stack.append(_Frame(_BRACED, depth=1))
            return i + 2
        if nxt == "[":
            stack.append(_Frame(_ARITH, closer="]", depth=1))
            return i + 2
        if nxt == "'" and stack[-1].kind == _COMMAND:  # `$'...'`, whose backslash escapes a quote
            j = i + 2
            while True:
                m = _ANSI_RE.search(text, j)
                if m is None:
                    return self.n
                if m.group() == "'":
                    return m.end()
                j = m.end() + 1
        return i + 1

    def backticks(self, i):
        """The index after the backtick that closes the one at text[i]: its text, bodies and all, is the substitution's."""
        text, j = self.text, i + 1
        while True:
            m = _BACKTICK_RE.search(text, j)
            if m is None:
                return self.n
            if m.group() == "`":
                return m.end()
            j = m.end() + 1

    # -- here-documents -----------------------------------------------------------------------------------------------
    def operator(self, stack, frame, i):
        """A `<<` or `<<-` at text[i]: the here-document its delimiter word opens, read at the list's next newline."""
        text, n = self.text, self.n
        k = i + 2
        dash = text.startswith("-", k)
        k += dash
        while k < n and text[k] in " \t":
            k += 1
        word, quoted, end = self.delimiter(k)
        if not (word or quoted) or self.shifts(stack, i):
            return i + 2  # no word, which no shell accepts, or zsh's arithmetic shift
        slot = None
        if frame.strip:
            slot = len(self.bodies)
            self.bodies.append("")
        frame.pending.append((word, dash, quoted, slot))
        return end

    def delimiter(self, k):
        """(the delimiter the word at text[k] spells, whether any of it is quoted, the index after it): quote removal and
        nothing else, as both shells read it (probed: `E"O"F`, `'EOF'` and `\\EOF` are EOF, `<<$Z` ends at a line `$Z`)."""
        text, n = self.text, self.n
        word, quoted = [], False
        while k < n and text[k] not in _DELIMITER_END:
            c = text[k]
            if c == "'":
                end = text.find("'", k + 1)
                end = n if end < 0 else end
                word.append(text[k + 1 : end])
                quoted, k = True, end + 1
            elif c == '"':
                k += 1
                while k < n and text[k] != '"':
                    if text[k] == "\\" and text[k + 1 : k + 2] in ('"', "\\", "$", "`"):
                        k += 1
                    word.append(text[k])
                    k += 1
                quoted, k = True, k + 1
            elif c == "\\":
                word.append(text[k + 1 : k + 2])
                quoted, k = True, k + 2
            else:
                word.append(c)
                k += 1
        return "".join(word), quoted, min(k, n)

    def read_bodies(self, frame, i):
        """The bodies of the list's pending here-documents, one after another from text[i], the line after the newline
        that ends their command: each runs to the line that is its delimiter, whole (leading tabs stripped after `<<-`), or
        to the end of the text.  Where the delimiter is unquoted a line ending in an odd run of backslashes joins the next
        before it is compared (probed: `a\\` then EOF read aEOF, and the body went on).  The index after the last one."""
        text, n = self.text, self.n
        if frame.strip:
            self.out.append(text[self.mark : i])
        for word, dash, quoted, slot in frame.pending:
            start, end = i, None
            while i < n:
                line_end = text.find("\n", i)
                line_end = n if line_end < 0 else line_end
                line = text[i:line_end]
                while not quoted and line_end < n and (len(line) - len(line.rstrip("\\"))) % 2:
                    nxt = text.find("\n", line_end + 1)
                    nxt = n if nxt < 0 else nxt
                    line, line_end = line[:-1] + text[line_end + 1 : nxt], nxt
                if (line.lstrip("\t") if dash else line) == word:
                    end = i
                    i = min(line_end + 1, n)
                    break
                i = line_end + 1
            i = min(i, n)
            if slot is not None:
                self.bodies[slot] = text[start:n] if end is None else text[start : max(start, end - 1)]
        frame.pending = []
        if frame.strip:
            self.mark = i
        return i

    # -- what an open `(` is -------------------------------------------------------------------------------------------
    def in_word(self, stack, i):
        """zsh reads the newline at text[i] inside a word (a glob group's pattern, an arithmetic command's expression), so
        it ends no command and reads no body.  Asked only at the line's own level and in its `<( )`s."""
        tail = self.open_parens(stack, i)
        if not tail:
            return False
        if self.readings <= 0:
            return True
        self.readings -= 1
        prefix = "".join(self.out) + self.text[self.mark : i]
        return _word_newlines(prefix + "\n" + tail) > _word_newlines(prefix + " " + tail)

    def shifts(self, stack, i):
        """zsh reads the `<<` at text[i] inside an arithmetic command (`(( x = 1 <<y ))`, probed), as a shift."""
        tail = self.open_parens(stack, i)
        if not tail:
            return False
        if self.readings <= 0:
            return True
        self.readings -= 1
        prefix = "".join(self.out) + self.text[self.mark : i]
        return _marked(prefix + "<<" + tail).count("<<") == _marked(prefix + tail).count("<<")

    def open_parens(self, stack, i):
        """The `)`s that close what is open at the line's level before text[i], where a bare `(` that may be a group or
        arithmetic is: '' where none is, where every one open is a subshell (subshell), or where the scan is in a `$( )`,
        whose text its own analysis reads."""
        if not stack[-1].strip or not any(f.opens for f in stack):
            return ""
        # a case command's patterns hold groups after the same characters (`;;`, `|`, a newline)
        if self.case_at < i or any(f.others for f in stack):
            return ")" * (sum(len(f.opens) for f in stack) + len(stack) - 1)
        return ""

    def subshell(self, p):
        """The `(` at text[p] opens a subshell as zsh reads it: alone (no `((`), after the line's start, a newline that no
        backslash continues, `;`, `&`, `|` (not `>&` or `>|`), `(`, or a `{` that stands alone itself."""
        text = self.text
        if text[p + 1 : p + 2] == "(" or text[p - 1 : p] == "(":
            return False
        j = p - 1
        while j >= 0 and text[j] in " \t":
            j -= 1
        if j < 0:
            return True
        c = text[j]
        if c not in "\n;&|({" or c in "&|" and text[j - 1 : j] in ("<", ">") \
                or c == "{" and j and text[j - 1] not in " \t\n;&|(":
            return False
        k = j
        while k > 0 and text[k - 1] == "\\":
            k -= 1
        return (j - k) % 2 == 0


def _marked(text):
    """zsh's reading of a line's text (mark_zsh_patterns), prepared as analyse_command prepares it."""
    outer, _inner = prepare.split_substitutions(prepare.newlines_as_separators(text))
    return zsh.mark_zsh_patterns(prepare.neutralize_quoted_globs(outer))[0]


def _word_newlines(text):
    marked = _marked(text)
    return sum(marked.count(mark) for mark in _WORD_NEWLINES)
