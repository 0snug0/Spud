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
- a value the line does not spell (a substitution's output, a variable it did not assign, `$'...'`) refuses a member, and
  Spud reads on, as he does past an eval of a word the hook cannot resolve.

Probed in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py (tests/test_hooks.py EvalFlagTest has
the lines): (e) alone, repeated or beside `@` evaluates the value as it is; (P) first takes it for the name whose value is
evaluated; every other flag may change the text first -- `(Le)` lowercased `$(TOUCH R9)` before it ran, `(l(10)(x)e)` cut
its `$` off and ran nothing, `${(e)x#\\\\}` took a backslash away and ran what the value only spelled -- and so may a
modifier or a subscript.  zsh.flag_group reads the group, whose delimited arguments are no flags (`(j:e:)` runs nothing).
bash fails every one of these with "bad substitution"."""

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


class _Reading:
    """One command's reading of its (e) expansions: the analysis, the texts being read now (a value met again inside
    its own reading expands itself, which never returns in zsh: probed with `x='${(e)x}'`), the (text, depth) pairs
    read whole already, so a value named many times is read once, and each masked value's readings (_value_readings),
    so a long one named many times is not scanned again."""

    __slots__ = ("a", "open", "done", "texts")

    def __init__(self, a):
        self.a, self.open, self.done, self.texts = a, set(), set(), {}

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
                i = j + 1
                continue
            if c == "`":
                j = prepare.backtick_end(text, i)
                analyse.analyse_isolated(a, text[i + 1 : j], depth)
                i = j + 1
                continue
            if c == "$" and text.startswith("${(", i):
                closes = _brace_closes(text) if closes is None else closes
                read_eval_expansion(text, i, closes, reading, depth, {})
            i += 1
    finally:
        reading.open.discard(text)
    reading.done.add((text, depth))


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
    expansion (a `$` not marked literal), or `$'...'` text the hook does not decode.

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
