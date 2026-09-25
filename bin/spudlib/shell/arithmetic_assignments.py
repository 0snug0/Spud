"""shell/arithmetic_assignments: The names an arithmetic evaluation assigns, and the values it assigns them (SPD-225).

A module of its own since SPD-259, taken out of shell/assignment_words: shell/expansions records what arithmetic_names
reads in an arithmetic command, a `let`, a `for (( ... ))` header, a `[[ ]]` operand or a typed variable's value, and what
word_arithmetic reads in a word's `$(( ... ))`, `$[ ... ]` and `${ ... }` -- and, among those names, each function the
expression calls (MathCall, SPD-282), which shell/line_functions reads where zsh's `functions -M` names one.  It reads
text only and imports nothing of the shell reading's import cycle.  Past 250 lines as one reading: arithmetic_names and
word_arithmetic share one recursive reader (_read_arithmetic, _expanded_arithmetic and _braced call one another, with one
budget), which no cut would leave whole."""

from . import syntax
from ..core import lazy
from ..hooks import hookio


# Both shells evaluate arithmetic in `(( ... ))`, `$(( ... ))`, `$[ ... ]`, `let`'s words, a `for (( ... ))` header, the
# operands of `[[ ... -eq ... ]]` and its kin, an array's subscript and bash's `${name:offset:length}`, and in what a name
# with the integer or float attribute is assigned; each assigns a name an assignment operator (`=`, `+=`, `<<=` ...) or an
# increment stands beside, and a name it reads is evaluated as arithmetic in turn.  Probed in zsh 5.9 -f, zsh -f -o
# nobareglobqual and bash 3.2.57 (tests/probes/shell_probe.py, 2026-09-24; tests/test_hooks_words.py
# ArithmeticAssignmentTest has the lines and what each printed).

ARITH_OPAQUE = "\x00"  # a number the hook does not know, standing in an expression where an expansion stood
ARITH_DEPTH = 8  # how far a value read as arithmetic again, or an expansion inside another, is followed
_BRACE_NEST = 32  # how deep `${ ... }` nesting is read for arithmetic: unread.BRACE_DEPTH, past which the line is refused
_ARITH_BUDGET = 64  # the most expressions one reading follows, so a pathological line stays bounded
_ARITH_TOKEN_RE = lazy.LazyPattern(
    r"\s+|(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<number>[0-9][0-9A-Za-z_#.@]*)"
    r"|(?P<op><<=|>>=|\*\*=|&&=|\|\|=|\^\^=|\+\+|--|[-+*/%&^|<>!=]=|&&|\|\||\^\^|\*\*|<<|>>|\S)")
ASSIGN_OPERATORS = frozenset(("=", "+=", "-=", "*=", "/=", "%=", "&=", "^=", "|=", "<<=", ">>=", "**=", "&&=", "||=", "^^="))
_STEPS = frozenset(("++", "--"))
_BRANCHING = frozenset(("?", "&&", "||"))  # past one of these an assignment may not run
_DECIMAL_RE = lazy.LazyPattern(r"(?:0|[1-9][0-9]{0,17})\Z")  # a number both shells read alike, below 2**63
_NAME_START_RE = lazy.LazyPattern(r"[A-Za-z_][A-Za-z0-9_]*")
_NAME_LETTER_RE = lazy.LazyPattern(r"[A-Za-z_]")
_BRACED_PREFIX_RE = lazy.LazyPattern(r"[#!^=~+]*(?:\([^)]*\))?[#!^=~+]*(?:[A-Za-z_][A-Za-z0-9_]*)?")
_PAIRS = {"(": ")", "[": "]", "{": "}"}
_CALLED = ("name", "opaque")  # what a `(` glued to it calls: a name, or one an expansion gives that the hook cannot read
# A masked word's text with the hook's marks restored, but for the two that say a `$` expands nothing -- single-quoted,
# escaped, `$'...'` -- which word_arithmetic reads (reevaluation._WORD_TEXT's table)
_DOLLAR_MARKS = (syntax._LITERAL_DOLLAR, syntax._QUOTED_DOLLAR)
_KEEP_DOLLAR_MARKS = str.maketrans({k: v for k, v in syntax._SENTINEL_TEXT.items() if k not in _DOLLAR_MARKS})


class MathCall(tuple):
    """SPD-282: a call of a function in an arithmetic expression, `name(...)`, as `found` holds it among the names the
    expression assigns: `(name, None)`, in the order the shell makes it -- after its arguments, which it evaluates first
    (probed: `$(( a1(b1()) ))` ran b1, then a1) -- with syntax.UNKNOWN_NAME for a name an expansion gives that the hook
    cannot read, and `arguments`, how many the call passes.  zsh calls such a name only where `functions -M` registered
    it, with no blank before its `(` (`$(( mf (1) ))` was a bad math expression; `$(( mf ))` read a variable), and bash
    calls none (a syntax error in the expression), so shell/line_functions.read_math_calls decides which of them run a
    function's body.  A pair, so a reader of `found` that knows no calls reads one as a name assigned a value it does not
    know."""

    def __new__(cls, name, arguments):
        call = super().__new__(cls, (name, None))
        call.arguments = arguments
        return call


def arithmetic_names(text, settle, doubtful=False):
    """[(name, value or None)] for the names an arithmetic expression assigns, in the order the shells assign them: the
    decimal literal a name is assigned where the hook can know it, None where it cannot -- a compound assignment or an
    increment, any other expression, a number zsh and bash read apart (a leading 0 is octal to bash alone, `012` printed 12
    in zsh and 10 in bash; a float is zsh's alone; past 18 digits each wraps its own way), and an assignment past `?`,
    `&&` or `||`, in a subscript, or with `doubtful` set, which may not run (`(( 0 && (X = 30) ))` left X).  None when the
    expression assigns a name the hook cannot read: an expansion the line does not settle, or a substitution, stands where
    the name goes (`(( $N = 5 ))`).

    `text` is the expression as the shells read it: the hook's marks taken off, and every `$` in it one that expands, as
    each does inside arithmetic.  `settle(name)` is the text the line settles for a `$name` there, or None.  A name the
    expression reads is evaluated as arithmetic in turn (`Y='X=18'; (( Y ))` assigned X in both shells), followed
    ARITH_DEPTH deep; past that bound, or past _ARITH_BUDGET expressions, the text is one the hook did not read, and the
    answer is None.  A value the line does not spell -- the environment's, a substitution's output -- is not read so: a
    member's number, not text it wrote.  Each function the expression calls stands among the names as a MathCall, where
    the shell calls it (SPD-282)."""
    found = []
    return found if _read_arithmetic(text, settle, 0, found, doubtful, [_ARITH_BUDGET, set()]) else None


def _read_arithmetic(text, settle, depth, found, doubtful, budget):
    """arithmetic_names over one expression at `depth`, adding to `found`; False when it cannot be read.  `budget`: the
    expressions left to read, and the names whose value this reading has read already, each once."""
    budget[0] -= 1
    if depth > ARITH_DEPTH or budget[0] < 0:
        return False
    expanded = _expanded_arithmetic(text, settle, depth, found, doubtful, budget)
    if expanded is None:
        return False
    tokens, spans, calls = [], [], set()
    for m in _ARITH_TOKEN_RE.finditer(expanded):
        if not m.lastgroup:
            continue
        if m.group() == "(" and spans and m.start() == spans[-1][1] and tokens[-1][0] in _CALLED:
            calls.add(len(tokens) - 1)  # a name glued to its `(`: a call (MathCall)
        tokens.append((m.lastgroup if m.group() != ARITH_OPAQUE else "opaque", m.group()))
        spans.append(m.span())

    def between(opener, close):
        """The text between the pair at tokens[opener] and tokens[close], as spelled, so a call inside stays glued."""
        return expanded[spans[opener][1] : spans[close][0] if close < len(spans) else len(expanded)]

    branching = doubtful or any(kind == "op" and t in _BRANCHING for kind, t in tokens)
    i, n = 0, len(tokens)
    while i < n:
        kind, t = tokens[i]
        i += 1
        if i - 1 in calls:
            # its arguments, evaluated before the call, then the call (SPD-282); never the value of a variable of its name
            close = _token_close(tokens, i)
            inner = tokens[i + 1 : close]
            if inner and not _read_arithmetic(between(i, close), settle, depth + 1, found, branching, budget):
                return False
            found.append(MathCall(t if kind == "name" else syntax.UNKNOWN_NAME, _arguments(inner)))
            i = close + 1
            continue
        if kind == "op" and t in _STEPS and i < n and tokens[i][0] in ("name", "opaque"):
            if tokens[i][0] == "opaque":  # `++$N`
                return False
            found.append((tokens[i][1], None))
            continue
        if kind not in ("name", "opaque"):
            continue
        subscripted = i < n and tokens[i][1] == "["
        if subscripted:  # an element: its subscript is arithmetic, which an associative array's is not
            close = _token_close(tokens, i)
            if not _read_arithmetic(between(i, close), settle, depth + 1, found, True, budget):
                return False
            i = close + 1
        op = tokens[i][1] if i < n else None
        if op in ASSIGN_OPERATORS or op in _STEPS:
            if kind == "opaque":  # `$N = 5`, `$(echo X)++`: the hook cannot read which name
                return False
            if op != "=" and not _read_value(t, settle, depth, found, branching, budget):
                return False  # a compound assignment and an increment read the old value first
            found.append((t, None if op != "=" or subscripted or branching else _decimal_operand(tokens, i + 1)))
        elif kind == "name" and not _read_value(t, settle, depth, found, branching, budget):
            return False
    return True


def _read_value(name, settle, depth, found, doubtful, budget):
    """A name an expression reads: the value the line settles for it is evaluated as arithmetic too -- once per reading,
    which assigns what it assigns the first time (a value naming itself assigns nothing: `X=X; (( X ))` exited 2 in zsh
    and 1 in bash, probed)."""
    value = settle(name)
    if value is None or not _NAME_LETTER_RE.search(value) or name in budget[1]:
        return True
    budget[1].add(name)
    return _read_arithmetic(value, settle, depth + 1, found, doubtful, budget)


def _decimal_operand(tokens, start):
    """The decimal literal the operand from tokens[start] is -- up to the `,` or the unmatched `)` or `]` that ends it --
    with its sign, or None when it is anything else."""
    operand, level = [], 0
    for _, t in tokens[start:]:
        if t in ("(", "["):
            level += 1
        elif t in (")", "]"):
            if level == 0:
                break
            level -= 1
        elif t == "," and level == 0:
            break
        operand.append(t)
    sign = ""
    if len(operand) == 2 and operand[0] in ("-", "+"):
        sign, operand = ("-" if operand[0] == "-" else ""), operand[1:]
    if len(operand) != 1 or not _DECIMAL_RE.match(operand[0]):
        return None
    return operand[0] if operand[0] == "0" else sign + operand[0]


def _token_close(tokens, start):
    """The index of the `]` or `)` that closes the `[` or `(` at tokens[start], or the tokens' end when none does."""
    opener, level = tokens[start][1], 0
    closer = _PAIRS[opener]
    for k in range(start, len(tokens)):
        if tokens[k][1] == opener:
            level += 1
        elif tokens[k][1] == closer:
            level -= 1
            if level == 0:
                return k
    return len(tokens)


def _arguments(tokens):
    """How many arguments a call's tokens between its parentheses pass: none for none, else one more than the commas
    outside any pair inside them."""
    level, commas = 0, 0
    for _, t in tokens:
        if t in _PAIRS:
            level += 1
        elif t in (")", "]", "}"):
            level -= 1
        elif t == "," and level == 0:
            commas += 1
    return commas + 1 if tokens else 0


def _close(text, start):
    """The index of the character that closes the `(`, `[` or `{` at text[start], counting that pair alone, or None."""
    opener, level = text[start], 0
    closer = _PAIRS[opener]
    for k in range(start, len(text)):
        c = text[k]
        if c == opener:
            level += 1
        elif c == closer:
            level -= 1
            if level == 0:
                return k
    return None


def _expanded_arithmetic(text, settle, depth, found, doubtful, budget, nest=0):
    """The expression the shells evaluate once they have expanded `text`, which they do first: an arithmetic expansion
    inside it is read for what it assigns, before the expression's own (`$(( (X = 6) + $(( X = 5 )) ))` left 6 in both
    shells), and stands for a number the hook does not know, as a substitution, a special parameter and a `$name` the
    line does not settle do; a `$name` or `${name}` the line settles is put in place as the text it is (`N='X = 5'; ((
    $N ))` assigned X); any other `${ ... }` is read by _braced, `nest` of them deep already.  None when a text inside
    cannot be read."""
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith(hookio.SUBST, i):
            out.append(ARITH_OPAQUE)
            i += len(hookio.SUBST)
            continue
        c = text[i]
        if c == "`":
            end = text.find("`", i + 1)
            out.append(ARITH_OPAQUE)
            i = n if end < 0 else end + 1
            continue
        if c != "$" or i + 1 == n:
            out.append(c)
            i += 1
            continue
        nxt = text[i + 1]
        if nxt in _PAIRS:
            close = _close(text, i + 1)
            inner = text[i + 2 : n if close is None else close]
            i = n if close is None else close + 1
            if nxt == "{" and syntax.IDENTIFIER_RE.match(inner):
                value = settle(inner)
                out.append(ARITH_OPAQUE if value is None else value)
                continue
            if nxt == "{":
                readable = _braced(inner, settle, depth, found, budget, nest + 1)
            elif nxt == "[" or inner.startswith("("):  # `$[ ... ]`, `$(( ... ))`
                readable = _read_arithmetic(inner, settle, depth + 1, found, doubtful, budget)
            else:  # a `$( ... )` no lifting took: a substitution
                readable = True
            if not readable:
                return None
            out.append(ARITH_OPAQUE)
            continue
        m = _NAME_START_RE.match(text, i + 1)
        if m is None:  # `$1`, `$#`, `$?` and the other special parameters
            out.append(ARITH_OPAQUE)
            i += 2
            continue
        value = settle(m.group())
        out.append(ARITH_OPAQUE if value is None else value)
        i = m.end()
    return "".join(out)


def _braced(inner, settle, depth, found, budget, nest):
    """Read the arithmetic a `${ ... }` other than a plain `${name}` may evaluate, every assignment in it one that may not
    run (an operator's word runs only for some values, an associative array's subscript is no arithmetic): its subscript
    (`${arr[X=2]}` and `${#arr[X=1]}` assigned X in both shells), bash's `${name:offset:length}` (`${s:X=1:2}` assigned X
    in bash; zsh read the `:X` as a modifier and stopped), and whatever its word expands.  False when it cannot be read:
    nested past _BRACE_NEST, which the line's own reading refuses first (unread.BRACE_DEPTH, SPD-103)."""
    if nest > _BRACE_NEST:
        return False
    rest = inner[_BRACED_PREFIX_RE.match(inner).end() :]
    if rest.startswith("["):
        close = _close(rest, 0)
        if not _read_arithmetic(rest[1 : len(rest) if close is None else close], settle, depth + 1, found, True, budget):
            return False
        rest = "" if close is None else rest[close + 1 :]
    if rest.startswith(":") and rest[1:2] not in ("-", "=", "+", "?", ":"):
        return all(_read_arithmetic(part, settle, depth + 1, found, True, budget) for part in rest[1:].split(":", 1))
    return _expanded_arithmetic(rest, settle, depth, found, True, budget, nest) is not None


def word_arithmetic(word, settle, raw=False):
    """[(name, value or None)] for the names the shells assign expanding this word -- its arithmetic expansions, `$((
    ... ))` and `$[ ... ]` (probed: `echo $((X=5))`, `/bin/echo $((X=6))`, `echo "$((X=16))"` and `echo $[X=12]` assigned
    X in all three shells), and the arithmetic a `${ ... }` may evaluate (_braced) -- in order, as arithmetic_names gives
    them, each call a MathCall among them; None when one of them cannot be read.  `word` is a masked word, where a `$` the
    hook marked literal (single-quoted, escaped) expands nothing, or, with `raw`, an unquoted here-document's body, where a
    backslash escapes what follows it."""
    if "$" not in word:
        return []
    text = word if raw else word.translate(_KEEP_DOLLAR_MARKS)
    found, budget, i, n = [], [_ARITH_BUDGET, set()], 0, len(text)
    while True:
        i = text.find("$", i)
        if i < 0 or i + 1 >= n:
            return found
        nxt = text[i + 1]
        if nxt not in _PAIRS or (raw and _escaped(text, i)):
            i += 1
            continue
        close = _close(text, i + 1)
        inner = text[i + 2 : n if close is None else close]
        for mark in _DOLLAR_MARKS:  # every `$` inside arithmetic expands (zsh.py marks them literal for the reading)
            inner = inner.replace(mark, "")
        if nxt == "{":
            readable = syntax.IDENTIFIER_RE.match(inner) is not None or _braced(inner, settle, 0, found, budget, 1)
        elif nxt == "[" or inner.startswith("("):
            readable = _read_arithmetic(inner, settle, 0, found, False, budget)
        else:
            readable = True
        if not readable:
            return None
        i = n if close is None else close + 1


def _escaped(text, i):
    """Whether the character at text[i] follows an odd run of backslashes."""
    k = i
    while k > 0 and text[k - 1] == "\\":
        k -= 1
    return (i - k) % 2 == 1
