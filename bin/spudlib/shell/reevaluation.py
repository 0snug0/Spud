"""shell/reevaluation: zsh's (e) parameter flag, which runs the substitutions in the value it expands (SPD-189).

`${(e)x}` performs parameter expansion, command substitution and arithmetic expansion on the value of x, so a line that
assigns shell text and expands it with (e) runs that text: `x='$(git push)'; echo ${(e)x}` pushed, and the hook read
nothing of it.  ShellWalk.consume hands this every word of a simple command -- its arguments, its redirections' targets,
its assignments, a case's word and patterns, a loop's header -- with the variables the line holds where the word is
expanded, and each (e) expansion in the word is read:

- a value the line settles (arg_writes.resolved's reading, bar its rules on blanks and glob characters, which are about
  the words an expansion splits into and not the text (e) reads) is read as the text (e) evaluates, read_evaluated_text;
- a value the line spells but may not hold there -- one it doubts, one a loop or function body reads, one an earlier word
  of the same command assigns, an array -- or one another flag, a modifier or a subscript changes before (e) reads it, is
  read as it is spelled, and refuses a member besides ("eval-flag"): the text zsh evaluates may be another;
- a value the line does not spell (a substitution's output, a variable it did not assign, a `$'...'` whose escapes the shells
  decode apart; SPD-202 reads every other one as its value) refuses a member, and
  Spud reads on, as he does past an eval of a word the hook cannot resolve.

Probed in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py (tests/test_hooks.py EvalFlagTest has
the lines): (e) alone, repeated or beside `@` evaluates the value as it is; (P) first takes it for the name whose value is
evaluated; every other flag may change the text first -- `(Le)` lowercased `$(TOUCH R9)` before it ran, `(l(10)(x)e)` cut
its `$` off and ran nothing, `${(e)x#\\\\}` took a backslash away and ran what the value only spelled -- and so may a
modifier or a subscript.  zsh.flag_group reads the group, whose delimited arguments are no flags (`(j:e:)` runs nothing).
bash fails every one of these with "bad substitution".

zsh and bash expand an unquoted here-document's body before its command reads it as (e) evaluates a value, so
ShellWalk.consume hands such a body to read_expanded_body, which reads it with read_evaluated_text (SPD-192) and says
whether it ran a substitution, whose output the command then reads (SPD-207), and reads each parameter's value in it
with body_values, which heredocs.received_body puts in place where the line settles it (SPD-208).

Kept whole past 250 lines (the package's look-again point): (e)'s evaluation and a body's expansion are one reading of
text as the shells expand it, and split apart they would each need the other's scan; a body's parameters are read with
(e)'s settled values (_variable_texts) and its cache (_Reading)."""

import re

from . import analyse, assignment_words, prepare, syntax, zsh
from ..hooks import hookio

# analyse_command's own bound: past it a body is dropped unread, so a value (e) would evaluate there refuses a member
EVALUATION_DEPTH = 6
_TEXT_KEEPING_FLAGS = frozenset("e@")  # the flags that leave the value's text as it is before (e) evaluates it
_MODIFIERS = "^=~#+"  # what may stand between the flags and the name: `${(e)~x}`, `${(e)=x}`, `${(e)#x}`
# A masked word's characters as the text they spell, but for the marks that say a `$` expands nothing -- single-quoted,
# escaped, or inside arithmetic -- or opens text the hook does not decode (`$'...'`)
_WORD_TEXT = str.maketrans({k: v for k, v in syntax._SENTINEL_TEXT.items()
                            if k not in (syntax._LITERAL_DOLLAR, syntax._QUOTED_DOLLAR)})
# `${(` as mark_zsh_patterns writes it inside an arithmetic expansion or command, where it marks every `$` literal:
# zsh expands it there all the same (probed: `$(( ${(e)x} + 1 ))` and `(( ${(e)x} ))` ran x's substitution)
_ARITHMETIC_OPEN = "$" + syntax._LITERAL_DOLLAR + syntax._GLOB_SENTINELS["{"] + syntax._ARITH_SENTINELS["("]
_ESCAPED_DOLLAR = "\\$" + syntax._LITERAL_DOLLAR  # a backslash before a literal `$` in a masked value (_value_readings)
# What body_values reads in an unquoted here-document's body (SPD-208):
_PLACED_RE = re.compile(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{(\(e\))?([A-Za-z_][A-Za-z0-9_]*)\})\Z")  # handed on as its value
# the characters a shell fed the body parses as more than a word's plain text: blanks and separators, redirections,
# quotes and escapes, expansions, braces, globs, and `=`, which makes a command's first word an assignment
_BODY_SYNTAX_RE = re.compile(r"[\s;&|<>()'\"\\$`{}*?\[\]=]")
_NUMBER_PARAMETERS = frozenset("?$!#-")  # an exit status, a process id, a count, the option letters
_BRACED_UNREAD_RE = re.compile(r"[$`\\'\"(){}]")  # a nested expansion, quoting, zsh's flags, braces
_BRACED_HEAD_RE = re.compile(r"([#!^=~+]*)([A-Za-z_][A-Za-z0-9_]*|[0-9]+|[@*?$!#-])")  # modifiers, then the parameter
_OPERATOR_RE = re.compile(r":?[-=+?]|##?|%%?|//?|\^\^?|,,?|:")
_PATTERN_OPERATORS = frozenset(("#", "##", "%", "%%", ":?", "?", "^", "^^", ",", ",,"))  # their word leaves no text


class _Reading:
    """One command's reading of its (e) expansions: the analysis, the texts being read now (a value met again inside
    its own reading expands itself, which never returns in zsh: probed with `x='${(e)x}'`), the (text, depth) pairs
    read whole already, so a value named many times is read once, each masked value's readings (_value_readings),
    so a long one named many times is not scanned again, and whether any `$( )` or backtick substitution was read
    (`ran`, which read_expanded_body answers with)."""

    __slots__ = ("a", "open", "done", "texts", "ran")

    def __init__(self, a):
        self.a, self.open, self.done, self.texts, self.ran = a, set(), set(), {}, False

    def readings(self, value):
        found = self.texts.get(value)
        if found is None:
            found = self.texts[value] = tuple(_value_readings(value))
        return list(found)


def read_eval_words(words, a, depth):
    """Read every (e) expansion in a simple command's masked words, at the analysis depth `depth` of the text they stand
    in.  An assignment word gives a later word of the same command its value -- zsh assigns an assignment-only command's
    words in turn (probed: `x=a; x='$(touch w1)' y=${(e)x}` made w1) -- where a command's prefix assignment does not
    reach its own words (`x=a; x='$(touch w2)' echo "${(e)x}"` printed a); the hook reads both values and refuses a
    member, since which one zsh uses depends on the words after it."""
    reading, assigned = None, {}
    for w in words:
        if "$" in w:
            text = w.replace(_ARITHMETIC_OPEN, "${(").translate(_WORD_TEXT)
            if "${(" in text:
                reading = reading or _Reading(a)
                read_eval_text(text, reading, depth, assigned)
        found = assignment_words.assignment_word(w) if "=" in w else None
        if found is not None:
            name, subscript, append, value = found
            assigned[name] = None if (subscript is not None or append) else value


def read_eval_text(text, reading, depth, assigned=None):
    """Read each `${(` expansion of `text` whose flags hold (e): a word's text with its literal dollars marked, or a value
    (e) evaluates, whose every `$` expands."""
    closes, p = _brace_closes(text), text.find("${(")
    while p >= 0:
        read_eval_expansion(text, p, closes, reading, depth, assigned or {})
        p = text.find("${(", p + 3)


def read_eval_expansion(text, p, closes, reading, depth, assigned):
    """The (e) expansion at text[p], if its flags hold (e): each text it evaluates is read (read_evaluated_text), and a
    member is refused unless the hook reads exactly that text.  A group zsh would reject is refused too, fail closed."""
    end, head = closes.get(p + 1), zsh.flag_group(text, p + 3)
    if head is None:
        exact = False
    else:
        letters, k = head
        if "e" not in letters:
            return
        texts, exact = _expansion_texts(text, letters, k, end, closes, reading, assigned, outer=True)
        for value in texts:
            if value in reading.open or depth + 1 > EVALUATION_DEPTH:
                exact = False  # a value that expands itself, or one read past the analysis's bound
            elif (value, depth + 1) not in reading.done:
                read_evaluated_text(value, reading, depth + 1)
    if not exact:
        reading.a.findings.append(("eval-flag", prepare.deglob(text[p : len(text) if end is None else end + 1])))


def read_evaluated_text(text, reading, depth):
    """Read `text` as (e) evaluates it, at analysis depth `depth`: the inside of double quotes, where quotes are text
    and a backslash escapes the next character (probed: `'$(touch re3)'` and `"$(touch re5)"` ran, their quotes printed;
    `\\$(touch re4)` printed `$(touch re4)`, `\\\\$(touch q2)` ran), each `$( )` and backtick body analysed as the commands
    it runs in its own process, a default word's and one inside arithmetic included (`${zz:-$(touch q5)}`, `$((1+$(touch
    q6)))`), and each (e) expansion in it read in turn (`x='${(e)y}'` ran y's) -- a plain `$y` is expanded once and never
    evaluated again (`x='$y'` printed y's `$(touch re9)`).  `${y:=...}` there assigns y as it does on the line, which
    doubts it as analyse_command does.  An unquoted here-document body is expanded the same way."""
    a = reading.a
    reading.open.add(text)
    try:
        for m in syntax._ASSIGNING_EXPANSION_RE.finditer(text):
            a.doubt.add(m.group(1))
            a.sticky.add(m.group(1))
        i, n, closes = 0, len(text), None
        while i < n:
            c = text[i]
            if c == "\\":
                i += 2
                continue
            if c == "$" and text.startswith("$(", i) and not text.startswith("$((", i):
                j = prepare.substitution_end(text, i)
                analyse.analyse_isolated(a, text[i + 2 : j], depth)
                reading.ran = True
                i = j + 1
                continue
            if c == "`":
                j = prepare.backtick_end(text, i)
                analyse.analyse_isolated(a, text[i + 1 : j], depth)
                reading.ran = True
                i = j + 1
                continue
            if c == "$" and text.startswith("${(", i):
                closes = _brace_closes(text) if closes is None else closes
                read_eval_expansion(text, i, closes, reading, depth, {})
            i += 1
    finally:
        reading.open.discard(text)
    reading.done.add((text, depth))


def read_expanded_body(body, a, depth):
    """Read an unquoted here-document's body as zsh and bash expand it before its command reads it, at analysis depth
    `depth` (SPD-192): as read_evaluated_text reads a value, since the body is expanded as that text is -- its quotes
    are text, a backslash escapes `$`, a backtick, a backslash or a newline, and each `$( )`, backtick, default word,
    arithmetic expansion and zsh (e) expansion in it runs (probed through tests/probes/shell_probe.py in zsh 5.9 -f,
    -f -o nobareglobqual and bash 3.2.57: tests/test_hooks.py HereDocumentExpansionTest).  A body with neither a `$`
    nor a backtick expands nothing, and is not scanned.

    Whether the expansion runs a command substitution -- a default word's, one in arithmetic and one an (e) expansion
    evaluates among them -- whose output then stands in the text the command reads, text the line does not spell
    (SPD-207, heredocs.OutputBody)."""
    if "$" not in body and "`" not in body:
        return False
    reading = _Reading(a)
    read_evaluated_text(body, reading, depth)
    return reading.ran


def body_values(a, words):
    """The line's reading of each parameter expansion in an unquoted here-document's body, for heredocs.received_body
    (SPD-208): the shell expanding the body puts each value in the text, and a shell fed it parses that text again, so
    the value's separators, redirections, newlines and substitutions are commands it runs (probed through
    tests/probes/shell_probe.py in zsh 5.9 -f, -f -o nobareglobqual and bash 3.2.57: tests/test_hooks.py
    HereDocumentValueTest).  `words` are the command's, whose prefix assignments bash expands the body with and zsh
    does not (probed: `x=a; x=b cat <<EOF` printed `[$x]` as b in bash and a in zsh).

    Called with an expansion's text, it answers (the text to put in its place or None, whether the line settles every
    text it may leave).  A `$NAME`, `${NAME}` or zsh's `${(e)NAME}` whose value the line settles -- arg_writes.resolved's
    reading, as _variable_texts reads it, bar the rules on blanks and glob characters, since nothing splits or globs a
    value in a body -- is that value's text, and for (e) only a value with no expansion in it, which (e) leaves as it is.
    Where the two shells' values differ the one holding shell syntax is put in place.  Every text an expansion may leave
    that is not put in place must be plain for the line to settle it: an environment variable the line never touches, a
    special parameter's number or option letters, a length, or a settled value or spelled word holding no shell syntax
    (_BODY_SYNTAX_RE).  A value the line does not spell, a positional parameter, a variable the shells set themselves,
    one a loop or function body reads, and a nested expansion or quoting inside a `${ }` settle nothing."""
    reading, assigned = _Reading(a), {}
    for w in words:
        found = assignment_words.assignment_word(w) if "=" in w else None
        if found is None:
            break  # the command's prefix assignments end at its first other word
        name, subscript, append, value = found
        assigned[name] = None if (subscript is not None or append) else value

    def value(text):
        m = _PLACED_RE.match(text)
        if m is None:
            possible, placed = _operator_texts(text, reading, assigned), None
        else:
            possible = _parameter_texts(m.group(1) or m.group(3), reading, assigned)
            if m.group(2):  # (e) evaluates a value holding an expansion into text the line does not spell
                possible = [None if isinstance(t, str) and ("$" in t or "`" in t) else t for t in possible]
            known = [t for t in possible if isinstance(t, str)]
            placed = next((t for t in reversed(known) if _BODY_SYNTAX_RE.search(t)), known[-1] if known else None)
        return placed, all(t is True or isinstance(t, str) and (t == placed or not _BODY_SYNTAX_RE.search(t))
                           for t in possible)

    return value


def _parameter_texts(name, reading, assigned):
    """The texts the parameter `name` may leave in a body: the value the line holds before the command (zsh's reading)
    and the one the command's own prefix assigns (bash's).  A str is a value's text, None text the line does not spell,
    True plain text that is not the line's -- an environment variable it never touches, a special parameter's number or
    option letters."""
    a = reading.a
    if name in _NUMBER_PARAMETERS:
        return [True]
    if not syntax.IDENTIFIER_RE.match(name) or name in syntax.DYNAMIC_VARIABLES or a.all_doubt or a.loop_depth:
        return [None]  # a positional parameter or $0, one the shells set, or a name a loop's next pass may assign
    if name in a.vars or name in a.doubt or name in a.sticky:
        texts, settled = _variable_texts(name, reading, {})
        found = texts if settled else [None]
    else:
        found = [True]
    if name in assigned:
        found += reading.readings(assigned[name]) or [None]
    return found


def _operator_texts(text, reading, assigned):
    """The texts an expansion other than a plain name may leave in a body, which is kept as spelled: its parameter's
    values -- a piece of one or the whole -- and the word an operator may put in their place (`${u:-word}`,
    `${x/pattern/word}`), where a pattern the operator only removes is left out.  A number for a length or zsh's `$+`,
    and None for what the hook does not read here: bash's `${!name}`, zsh's flags, a subscript, a nested expansion."""
    braced = text.startswith("${")
    inner = text[2:-1] if braced and text.endswith("}") else None if braced else text[1:]
    if inner is None or braced and inner not in _NUMBER_PARAMETERS and _BRACED_UNREAD_RE.search(inner):
        return [None]
    head = _BRACED_HEAD_RE.match(inner)
    if head is None or "!" in head.group(1):
        return [None]
    if "#" in head.group(1) or "+" in head.group(1):
        return [True]  # zsh's and bash's length, zsh's set-or-not: a number
    possible = _parameter_texts(head.group(2), reading, assigned)
    rest = inner[head.end() :]
    op = _OPERATOR_RE.match(rest)
    if op is None or op.group() not in _PATTERN_OPERATORS:
        possible.append(rest[op.end() if op else 0 :])
    return possible


def _expansion_texts(text, letters, k, end, closes, reading, assigned, outer):
    """([the texts the expansion whose flags `letters` end at text[k] and whose `}` is text[end] yields before (e) reads
    them], whether that is exactly the text zsh evaluates).  Its operand is a name, a nested `${...}` (only in the
    outermost expansion, whose own (e) makes the inner's value unknown), or none, with a default word spelled after
    `:-`; a special parameter, a substitution or a deeper nesting yields nothing the hook can read."""
    if not outer and "e" in letters:
        return [], False  # an evaluation's output, which the hook cannot know; the inner one is read on its own
    exact = end is not None and set(letters) <= _TEXT_KEEPING_FLAGS | {"P"} and letters.count("P") <= 1
    n, j = len(text), k
    while j < n and text[j] in _MODIFIERS:
        j += 1
    exact = exact and j == k
    quoted = outer and text.startswith("\"${", j)  # `${(e)"${x}"}` in a value, whose quotes zsh takes off
    j += quoted
    if text.startswith("${", j):
        inner_end = closes.get(j + 1)
        inner = zsh.flag_group(text, j + 3) if text.startswith("${(", j) else ("", j + 2)
        if not outer or inner is None or inner_end is None:
            return [], False
        texts, inner_exact = _expansion_texts(text, inner[0], inner[1], inner_end, closes, reading, assigned, outer=False)
        exact, j = exact and inner_exact, inner_end + 1
        j += quoted and text[j : j + 1] == '"'
    elif text.startswith(hookio.SUBST, j):
        return [], False
    elif (m := syntax._NAME_RE.match(text, j)) is not None:
        texts, settled = _variable_texts(m.group(), reading, assigned)
        exact, j = exact and settled, m.end()
    elif text.startswith(":-", j) and end is not None:
        texts = reading.readings(text[j + 2 : end])
        return texts, exact and bool(texts)
    else:
        return [], False
    if "P" in letters:
        texts, exact = _indirect(texts, exact, reading, assigned)
    return texts, exact and j == end


def _indirect(names, exact, reading, assigned):
    """(P): each text is the name whose value is expanded.  A text that is no plain name -- a subscript (whose
    substitutions (P) runs, probed: `n='x[$(touch p10)]'; echo ${(P)n}` made p10), anything else -- is read itself."""
    texts = []
    for name in names:
        if syntax.IDENTIFIER_RE.match(name):
            found, settled = _variable_texts(name, reading, assigned)
            texts += found
            exact = exact and settled
        else:
            texts.append(name)
            exact = False
    return texts, exact and bool(names)


def _variable_texts(name, reading, assigned):
    """([the texts `name` may hold where it is expanded], whether the line settles it): the value it assigned, when that
    is text the line spells, and the one an earlier word of this command assigns.  Settled as arg_writes.resolved settles
    a value -- not doubted, not a loop's or a function body's, not one the shells set themselves -- and not an array, whose
    elements (e) evaluates one by one."""
    a = reading.a
    value = a.vars.get(name)
    texts = reading.readings(value)
    settled = (bool(texts) and syntax._ARRAY_VALUE not in value and name not in a.doubt and name not in a.sticky
               and not a.all_doubt and name not in syntax.DYNAMIC_VARIABLES and a.loop_depth == 0 and name not in assigned)
    if name in assigned:
        texts += [t for t in reading.readings(assigned[name]) if t not in texts]
    return texts, settled


def _value_readings(value):
    """The texts a masked value may spell, [] where the line does not spell it: it holds a substitution's output, an
    expansion (a `$` not marked literal), or `$'...'` text the hook does not decode (escapes the shells decode apart).

    Two where it holds a backslash before a literal `$` or a backtick: shlex leaves the backslash of `"\\$"` and `"\\``"`
    in the word, where the shell takes it off, so `x="\\$(touch h4)"` and `x='\\$(touch h4)'` reach the hook alike,
    and zsh's (e) runs the first and not the second (probed: h4 made, re4 not).  Both are read, the second fail closed
    (proposal 301, filed with SPD-189: the masked word should hold what the shell passes)."""
    if value is None or hookio.SUBST in value or syntax._QUOTED_DOLLAR in value or syntax._EXPANDING_DOLLAR_RE.search(value):
        return []
    texts = [prepare.deglob(value)]
    if _ESCAPED_DOLLAR in value or "\\`" in value:
        texts.append(prepare.deglob(value.replace(_ESCAPED_DOLLAR, _ESCAPED_DOLLAR[1:]).replace("\\`", "`")))
    return texts


def _brace_closes(text):
    """{the index of each `{` in text: the index of the `}` that closes it}, in one pass."""
    closes, opened = {}, []
    for i, c in enumerate(text):
        if c == "{":
            opened.append(i)
        elif c == "}" and opened:
            closes[opened.pop()] = i
    return closes
