"""shell/assignment_words: The words a shell reads as an assignment, subscripted ones included, and what zsh's special
associations and bash's environment bind through them; and the names the shells assign by other grammars -- an arithmetic
evaluation (SPD-225) and an assigning builtin's own operands (SPD-254).

A module of its own because three readers share it and one of them cannot reach the rest of the shell reading: zsh.py
(which imports syntax alone) asks whether a word before `(` is an array assignment's head and whether a word keeps zsh's
command position, walk.py joins `name[subscript]=( ... )` into one word, and analyse.py reads a prefix or a declaration
operand as the assignment it is.  It reads words only -- nothing here touches a ShellAnalysis, which shell/expansions
records into -- so it stays out of the shell reading's import cycle.

Past 250 lines (the package's look-again point) with SPD-225 and SPD-254, and the question is answered in two ways.  Every
reader here answers one question -- which names a word or a text assigns, and to what -- for the recorders in analyse,
expansions and walk, and none of them touches a ShellAnalysis.  But the arithmetic reader (arithmetic_names,
word_arithmetic) and the builtins' grammars (builtin_names) are seams with their own users, and a module each is their
better home; that move waits on its own ticket, since a new module on the hook path is an edit of tests/test_package.py's
HOOK_PATH, which SPD-225's deliverables did not hold."""

import re

from . import prepare, syntax
from ..core import lazy
from ..hooks import hookio


# `name[subscript]=value` and `name[subscript]+=value` are assignments, not command words, in zsh and bash (probed
# in zsh 5.9 -f, zsh -f -o nobareglobqual, bash 3.2 and sh with a fake program first on a scratch PATH): zsh's
# `path[1]=<dir>; foo`, `path[1,0]=(<dir>); foo`, `path[1]+=/../fakebin; foo` and `PATH[1]=<dir>:/; foo` (a character
# slice of the scalar) each ran the scratch copy, as a prefix too (`path[1]=<dir> foo`), and in bash `PATH[0]=<dir>`
# replaced the scalar (`PATH[0]+=x` appended to it).  The subscript runs to the `]` that balances its `[` (`a[b[1]]=x` is
# one subscript in both shells), and the `=` must follow it at once: `path[1]x=y` and `path[1][1]=x` are globs ("no
# matches found"), `path[ 1 ]=x` splits into words ("bad pattern"), and a quoted bracket is no subscript (`'path[1]'=x`
# ran a command of that name), which the masked word shows as a sentinel rather than `[`.
_SUBSCRIPTED_HEAD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\[")
_BRACKETS_PLAIN = str.maketrans({syntax._GLOB_SENTINELS["["]: "[", syntax._GLOB_SENTINELS["]"]: "]"})
# A blank inside one element of `name=( ... )` (a quoted `'echo SH'`) is kept as zsh's blank sentinel when walk.py joins
# the elements with plain blanks, so the elements can be told apart again (special_bindings); variable_readings restores
# it before splitting, and deglob restores it everywhere else.
_ELEMENT_BLANKS = str.maketrans({" ": syntax._ZSH_SENTINELS[" "], "\t": syntax._ZSH_SENTINELS["\t"]})
# zsh's special associations whose elements the shell runs by name (the zsh/parameter module, loaded under -f):
# each key of `functions` is a shell function, of `commands` a hashed command, of `aliases`, `galiases` and `saliases` an
# alias, a global alias and a suffix alias (as `alias`, `alias -g` and `alias -s` define them).  Probed in zsh 5.9 -f and
# -o nobareglobqual: `functions[foo]='echo SH'; foo`, `functions+=(foo 'echo SH'); foo`, `functions=(foo 'echo SH'); foo`
# (it adds its pairs and removes no other function), `typeset`/`declare`/`export 'functions[foo]=echo SH'`, a prefix
# (`functions[foo]='echo SH' true; foo`) and `functions[foo]+=x` each ran the function; `commands[foo]=<path>; foo` and
# `command foo` ran the file; `aliases[gp]='echo SH'; eval gp` and `aliases=(gp 'echo SH'); eval gp` ran the alias
# (`aliases+=( ... )` defined nothing in 5.9 -- recorded all the same, refusing on doubt); `dis_functions` and
# `dis_aliases` bind disabled entries, which ran nothing.  bash and sh have none of these: there they are plain arrays.
SPECIAL_TABLES = {"functions": "function", "commands": "hashed", "aliases": "alias", "galiases": "alias", "saliases": "alias"}
# The variables bash and sh (bash in POSIX mode) import a shell function from: `BASH_FUNC_<name>%%=() { body; }`
# on macOS's bash 3.2, the spelling `export -f` writes (probed; `BASH_FUNC_<name>()=` and `<name>=() {` imported nothing).
ENV_FUNCTION_PREFIX = "BASH_FUNC_"


def subscript_end(word, start):
    """The index of the `]` that closes the subscript opened by the `[` at word[start], nested brackets counted, or None."""
    depth = 0
    for k in range(start, len(word)):
        c = word[k]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return k
    return None


def assignment_word(word):
    """(name, subscript or None, whether it appends, value) when the shell reads this masked word as an assignment --
    `name=value`, `name+=value`, `name[subscript]=value`, `name[subscript]+=value` -- else None."""
    m = syntax.ASSIGNMENT_WORD_RE.match(word)
    if m is not None:
        return m.group(1), None, bool(m.group(2)), m.group(3)
    if "[" not in word:
        return None
    head = _SUBSCRIPTED_HEAD_RE.match(word)
    if head is None:
        return None
    end = subscript_end(word, head.end() - 1)
    if end is None:
        return None
    append = word.startswith("+=", end + 1)
    if not (append or word.startswith("=", end + 1)):
        return None
    return word[: head.end() - 1], word[head.end() : end], append, word[end + (3 if append else 2) :]


def declaration_word(word):
    """assignment_word for an operand of export, typeset, declare, local or readonly, which the builtin parses from its own
    text: a quoted subscript counts there (probed: `typeset 'path[1]=<dir>'` and `export 'functions[foo]=echo SH'` ran)."""
    found = assignment_word(word)
    if found is None and syntax._GLOB_SENTINELS["["] in word:
        found = assignment_word(word.translate(_BRACKETS_PLAIN))
    return found


_LOCAL_ATTRIBUTES = frozenset("airx")  # array, integer, readonly, export: attributes both shells give a local (local_names)


def local_names(words):
    """The names a declaration makes local to the function body it runs in (SPD-246): `local`, or `typeset` or `declare`
    with no -g, each operand a plain name or `name=value`.  Probed 2026-09-24 (shell_probe: zsh 5.9 -f and -f -o
    nobareglobqual, bash 3.2.57): `x=0; f () { local x=1; }; f; echo $x` printed 0 in all three, and so did typeset,
    declare, `local -a`, `-i`, `-x` and `-r`; `typeset -g` printed 1 in zsh (bash 3.2 has no -g), export 1 in all three,
    readonly 0 in zsh and 1 in bash.  So nothing is local where the hook cannot say so for both shells: export, readonly,
    an option other than those four attributes (-g global, -f/-F functions, -p print, -m a pattern, `+` switching one off,
    `-A` or a bare `-`, which bash 3.2 refuses, leaving a later `x=1` global), and an operand with a subscript, one that
    appends, or one the hook cannot read.  Such a name counts as the line's.  The caller holds a declaration to the body's
    own shell, where it surely runs: the same probe's `(local x)`, `false && local x` and `if false; then local x; fi`
    each left a later `x=1` in the function global."""
    if words[0] not in ("local", "typeset", "declare"):
        return ()
    names = []
    for w in words[1:]:
        text = prepare.deglob(w)
        if text.startswith(("-", "+")):
            if text[0] == "+" or len(text) < 2 or not set(text[1:]) <= _LOCAL_ATTRIBUTES:
                return ()
            continue
        found = declaration_word(w)
        name = text if found is None else found[0] if found[1] is None and not found[2] else None
        if name is None or not syntax.IDENTIFIER_RE.match(name):
            return ()
        names.append(name)
    return names


_TYPED_ATTRIBUTES = frozenset("iEF")  # integer, and zsh's two floats: an assignment to the name is arithmetic (typed_names)


def typed_names(words):
    """The names a declaration gives the integer or float attribute -- typeset, declare, local, export or readonly with
    -i, zsh's -E or -F (bash's -F lists functions: read as zsh's, the side that doubts), and zsh's `integer` and `float`
    -- whose later assignments the shells evaluate as arithmetic (probed in zsh 5.9 and bash 3.2.57: `declare -i X;
    X=3+4` left 7, `X=tests` 0, `typeset -i X; X='T=12'` set T to 12 as well, and zsh's `integer X=3+4` left 7)."""
    options = "".join(t[1:] for t in (prepare.deglob(w) for w in words[1:]) if t[:1] == "-" and len(t) > 1)
    if prepare.deglob(words[0]) not in ("integer", "float") and not _TYPED_ATTRIBUTES.intersection(options):
        return []
    names = []
    for w in words[1:]:
        found, text = declaration_word(w), prepare.deglob(w)
        name = found[0] if found is not None else text if syntax.IDENTIFIER_RE.match(text) else None
        if name is not None:
            names.append(name)
    return names


def array_head(word):
    """True for the word before an array's `(`: `name=`, `name+=`, `name[subscript]=` or `name[subscript]+=` (probed:
    `path[1,0]=(<dir>)` and `typeset path[1]=(<dir>)` ran in zsh)."""
    if not word.endswith("="):
        return False
    found = assignment_word(word)
    return found is not None and found[3] == ""


def array_value(elements):
    """The value walk.py joins for `name=( ... )`: the array mark, then the elements, one blank between each."""
    return syntax._ARRAY_VALUE + " ".join(e.translate(_ELEMENT_BLANKS) for e in elements)


def _unreadable(text, element=False):
    """The hook cannot say what this subscript or array element becomes: it holds an expansion or a substitution (an
    element may then become any number of words); a subscript starts with zsh's subscript flags (`functions[(e)foo]=` ran
    foo, probed); an element, which zsh expands as a word, holds a glob, a brace list or starts with `=name`.  A subscript
    is not globbed (probed: `functions[f?o]=` defined `f?o`, not foo)."""
    if hookio.SUBST in text or syntax._EXPANDING_DOLLAR_RE.search(text):
        return True
    if element:
        return syntax.GLOB_RE.search(text) is not None or text.startswith("=")
    return prepare.deglob(text).startswith("(")


def special_bindings(name, subscript, append, value):
    """(table, [(key, value or None)] or None) when this assignment binds elements of one of zsh's SPECIAL_TABLES, keys
    and values the masked words they are; the list is None when the hook cannot read which keys it binds, and a value is
    None when the hook cannot know it.  None when the name is no such table.

    `name[key]=value` binds key (appended, to a value the hook does not know); `name=( k v ... )` and `name+=( k v ... )`
    bind every other element, and cannot be read when an element holds an expansion or a glob (it may become any number of
    words), when the elements are odd in number (zsh: "bad set of key/value pairs", nothing bound -- refused on doubt), or
    when the value is no array at all."""
    table = SPECIAL_TABLES.get(name)
    if table is None:
        return None
    if subscript is not None:
        if _unreadable(subscript) or not prepare.deglob(subscript):
            return table, None
        return table, [(subscript, None if append else value)]
    if not value.startswith(syntax._ARRAY_VALUE):
        return table, None
    elements = value[len(syntax._ARRAY_VALUE) :].split(" ") if value != syntax._ARRAY_VALUE else []
    if len(elements) % 2 or any(_unreadable(e, element=True) for e in elements):
        return table, None
    return table, [(elements[k], elements[k + 1]) for k in range(0, len(elements), 2)]


# -- SPD-225: the names an arithmetic evaluation assigns ----------------------------------------------------------------
#
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
# A masked word's text with the hook's marks restored, but for the two that say a `$` expands nothing -- single-quoted,
# escaped, `$'...'` -- which word_arithmetic reads (reevaluation._WORD_TEXT's table)
_DOLLAR_MARKS = (syntax._LITERAL_DOLLAR, syntax._QUOTED_DOLLAR)
_KEEP_DOLLAR_MARKS = str.maketrans({k: v for k, v in syntax._SENTINEL_TEXT.items() if k not in _DOLLAR_MARKS})


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
    member's number, not text it wrote."""
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
    tokens = [(m.lastgroup if m.group() != ARITH_OPAQUE else "opaque", m.group())
              for m in _ARITH_TOKEN_RE.finditer(expanded) if m.lastgroup]
    branching = doubtful or any(kind == "op" and t in _BRANCHING for kind, t in tokens)
    i, n = 0, len(tokens)
    while i < n:
        kind, t = tokens[i]
        i += 1
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
            if not _read_arithmetic(" ".join(t for _, t in tokens[i + 1 : close]), settle, depth + 1, found, True, budget):
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
    """The index of the `]` that closes the `[` at tokens[start], or the tokens' end when none does."""
    level = 0
    for k in range(start, len(tokens)):
        if tokens[k][1] == "[":
            level += 1
        elif tokens[k][1] == "]":
            level -= 1
            if level == 0:
                return k
    return len(tokens)


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
    them; None when one of them cannot be read.  `word` is a masked word, where a `$` the hook marked literal (single-quoted,
    escaped) expands nothing, or, with `raw`, an unquoted here-document's body, where a backslash escapes what follows it."""
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


# -- SPD-254: the names an assigning builtin's own words assign ---------------------------------------------------------
#
# Each builtin's grammar, as the bash 5 and zsh 5.9 manuals give it and the probes of tests/test_hooks_words.py
# AssigningBuiltinTest confirm where this Mac's zsh 5.9 and bash 3.2.57 have the builtin: (the option letters that take a
# value, the letters whose value is a name the builtin assigns, the letters whose value zsh takes only glued or as the next
# word, both read).  A builtin with one grammar per shell is read with each, and a name either reading finds counts.
_READ_GRAMMARS = (("adinNptu", "a", ""), ("du", "", "tk"))  # bash's read, then zsh's (its -p is the coprocess, no prompt)
_OPTION_GRAMMARS = {
    "printf": ("v", "v"), "print": ("uCfvxX", "v"), "wait": ("p", "p"), "strftime": ("s", "s"), "zstat": ("fFAH", "AH"),
    "stat": ("fFAH", "AH"), "zselect": ("taA", "aA"),
}
# The builtins whose operand names what they assign, at this index past their options, and the name each assigns with none
_POSITIONAL_GRAMMARS = {
    "mapfile": ("dnOsuCc", 0, "MAPFILE"), "readarray": ("dnOsuCc", 0, "MAPFILE"), "vared": ("prMmift", 0, None),
    "sysread": ("cisot", 0, "REPLY"),
}
_DEFAULT_NAMES = {"zselect": "reply"}
# The builtins that assign no variable where a Bash call runs them: zle defines and runs widgets, and the completion
# builtins fail outside completion (probed in zsh 5.9 -f, non-interactive: `zle -N X` left X, `zle -M hi`, `compadd -A X a
# b` and `compset -p 1` exited 1 and left X)
_ASSIGNING_NOTHING = frozenset(("zle", "compadd", "compset"))
_ONE_WORD_RE = lazy.LazyPattern(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})" + syntax._QUOTED_NAME
                                + "|" + re.escape(hookio.SUBST) + syntax._QUOTED_SUBST)


def _word_reach(word):
    """How far a masked word may reach once the shell expands it: "plain", no expansion and no glob, the text it spells;
    "one", double-quoted expansions alone, one word whose text the hook does not know; "any", an unquoted expansion or a
    glob, which may become no word, several, or any text at all."""
    if hookio.SUBST not in word and not syntax._EXPANDING_DOLLAR_RE.search(word):
        return "any" if syntax.GLOB_RE.search(word) else "plain"
    rest = _ONE_WORD_RE.sub("", word)
    if hookio.SUBST in rest or syntax._EXPANDING_DOLLAR_RE.search(rest) or syntax.GLOB_RE.search(rest):
        return "any"
    return "one"


_BARE_EXPANSION_RE = lazy.LazyPattern(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})")


def _may_open(word, settle=None):
    """Whether a word the hook cannot read wholly is one the builtin reads as options: spelled with a `-` or `+` (`-v$N`),
    or a `$name` the line settles (`settle`, as arithmetic_names takes it) to a value that begins with one (`X='-v Y';
    printf $X hi` assigns Y).  An expansion the line does not settle -- the environment's, a loop's file name, a
    substitution's output -- is read as an operand (`printf "$f\\n"`, `wait $pid`): what it holds is data the line does not
    spell, which a member has no plausible reason to shape into an option (the Bash rule's threat model: a cooperative
    member), and a name the builtin reads from it is still refused (_Assigned.name)."""
    text = prepare.deglob(word)
    m = _BARE_EXPANSION_RE.match(text) if settle is not None else None
    value = settle(m.group(1) or m.group(2)) if m is not None else None
    if value is not None:
        text = value.lstrip() + text[m.end() :]
    return text[:1] in ("-", "+")


_NOT_NAME_RE = lazy.LazyPattern(r"[^A-Za-z0-9_]")


def _first_expansion(text):
    """Where the first expansion or substitution stands in a word's spelled text (deglob's), or its length."""
    found = [k for k in (text.find("$"), text.find(hookio.SUBST)) if k >= 0]
    return min(found) if found else len(text)


def _scan(args, values, optional="", plus=False, settle=None):
    """[(options, operands, stuck)]: each way a builtin's option scan may read `args`, its words after its name --
    options as [(letter, value)], operands the words after them, and stuck the first word the scan cannot read (one that
    may become an option, or an option word holding an expansion) or None.  Options end at `--`, `-` or the first word
    that is none.  A letter of `values` takes the rest of its word, else the next word (a masked word, whatever it holds);
    one of `optional` the rest of its word, or else none and the next word both, each a reading of its own; with `plus`
    a `+` opens options too (zsh's `set +A`).  `settle`: _may_open's."""
    readings, pending = [], [(0, [])]
    while pending:
        i, options = pending.pop()
        stuck = None
        while i < len(args):
            word, text = args[i], prepare.deglob(args[i])
            if _word_reach(word) != "plain":
                # an option word whose expansion stands in an option's value (`print -u$(( 2 - help ))`) is read as spelled;
                # one whose expansion may be a letter (`-$X`, `-r$X`) or may itself be an option (_may_open) is not read
                opens = text[:1] == "-" or plus and text[:1] == "+"
                if not (opens and any(c in values or c in optional for c in text[1 : _first_expansion(text)])):
                    if _may_open(word, settle):
                        stuck = word
                    break
            if text in ("--", "-"):
                i += 1
                break
            if len(text) < 2 or not (text[0] == "-" or plus and text[0] == "+"):
                break
            i += 1
            for k in range(1, len(text)):
                c = text[k]
                if c not in values and c not in optional:
                    options.append((c, None))
                    continue
                if text[k + 1 :]:
                    options.append((c, text[k + 1 :]))
                elif c in optional:
                    pending.append((i + 1, options + [(c, args[i] if i < len(args) else None)]))
                    options.append((c, None))
                else:
                    options.append((c, args[i] if i < len(args) else None))
                    i += 1
                break
        readings.append((options, [] if stuck is not None else args[i:], stuck))
    return readings


class _Assigned:
    """What builtin_names gathers from a builtin's words: the names it assigns, the subscripts of the elements among them,
    the first word it reads a name from that the hook cannot read, and the code it is handed to run."""

    __slots__ = ("names", "subscripts", "stuck", "evaluated")

    def __init__(self):
        self.names, self.subscripts, self.stuck, self.evaluated = [], [], None, None

    def refuse(self, word):
        if self.stuck is None:
            self.stuck = word

    def name(self, word, prompt=False):
        """A word the builtin reads a name from: a name, an element `name[subscript]` (`read 'a[1]'` assigned one in both
        shells), or, zsh's read's first operand, `name?prompt`.  A word that names no variable names none -- the builtin
        fails on it, or assigns a positional parameter (zsh's `read -n 1 X` put `a` in $1) -- and one the hook cannot read
        may name any, unless it stays one word whose text before its first expansion already names an element (`"a[$i]"`)
        or can be no name (`"Enter $what: "`)."""
        if word is None:
            return
        reach, text = _word_reach(word), prepare.deglob(word)
        known = text if reach == "plain" else text[: _first_expansion(text)]
        if prompt and "?" in known:
            text = known = known[: known.index("?")]
            reach = "plain"
        head = _SUBSCRIPTED_HEAD_RE.match(known)
        if reach == "plain" and syntax.IDENTIFIER_RE.match(text):
            self.names.append(text)
        elif reach == "plain" and head is not None and subscript_end(text, head.end() - 1) == len(text) - 1:
            self.names.append(text[: head.end() - 1])
            self.subscripts.append(text[head.end() : -1])
        elif reach != "plain" and head is not None:  # `a[$i]`: an element of a, whatever its subscript holds
            self.names.append(known[: head.end() - 1])
            self.subscripts.append(text[head.end() :].removesuffix("]"))
        elif reach != "plain" and (reach == "any" or not _NOT_NAME_RE.search(known)):
            self.refuse(word)

    def at(self, operands, k):
        """The operand at index k names what the builtin assigns: each word up to it must stay one word, or the name
        moves (an unquoted empty expansion is no word at all)."""
        for word in operands[: k + 1]:
            if _word_reach(word) == "any":
                self.refuse(word)
                return
        if k < len(operands):
            self.name(operands[k])

    def read(self, readings, names, every=False, prompt=False):
        """Each reading _scan made: the word it stuck on, the names its options' values give, and, `every`, its operands."""
        for options, operands, stuck in readings:
            if stuck is not None:
                self.refuse(stuck)
            for letter, value in options:
                if letter in names:
                    self.name(value)
            if every:
                for k, word in enumerate(operands):
                    self.name(word, prompt and k == 0)


def builtin_names(words, settle=None):
    """(names, subscripts, stuck, evaluated) for an assigning builtin (syntax.ASSIGNING_COMMANDS but for `let`, `integer`,
    `float` and `private`, whose operands are arithmetic or declarations), read by the builtin's own grammar where
    ASSIGNING_COMMANDS once read every identifier in every word (SPD-254): the names it assigns in zsh's reading or bash's,
    the subscripts of the elements among them (each arithmetic, SPD-225), the first word the hook cannot read where the
    builtin reads a name or an option (refused a member, SPD-217), and the word holding code the builtin is handed to run
    (`mapfile -C`'s callback, `zstyle -e`'s value), or None.  `settle`, as arithmetic_names takes it, says what a `$name`
    where an option may stand begins with (_may_open).

    Probed in zsh 5.9 -f, zsh -f -o nobareglobqual and bash 3.2.57, each fed `a b`: `read -t 1 X Y` gave X=a Y=b in all
    three; `read -tX Y` took X for the timeout in both; `read -p P X` failed in zsh and gave X in bash; `read 'X?prompt'`
    gave X in zsh; `read -a A` gave bash's array, `read -A A` zsh's; `printf -vX %s hi` assigned X in bash alone (zsh
    printed `-vX`) and `printf -- -v X` in neither; `print -rv X hi` assigned X in zsh; `getopts a: Y -a val` set Y, OPTARG
    and OPTIND; `unset -f X` kept the variable and zsh's `unset -m 'X*'` unset X by a pattern; `set -sA X b a` and `set +A
    X c` assigned X in zsh; `zparseopts -a A h=H -help=H2` set A, H and H2.  The builtins bash 4 and 5 added (mapfile,
    readarray, `read -i`/`-N`, `wait -p`, `unset -n`) and zsh's module builtins are read from their manuals, the side that
    records a name where the shell may not."""
    cmd, args = prepare.deglob(words[0]), list(words[1:])
    got = _Assigned()
    if cmd == "read":
        for (values, names, optional), prompt in zip(_READ_GRAMMARS, (False, True)):
            got.read(_scan(args, values, optional, settle=settle), names, every=True, prompt=prompt)
        if not got.names and got.stuck is None:
            got.names.append("REPLY")
    elif cmd in _OPTION_GRAMMARS:
        values, names = _OPTION_GRAMMARS[cmd]
        got.read(_scan(args, values, settle=settle), names)
        if not got.names and cmd in _DEFAULT_NAMES:
            got.names.append(_DEFAULT_NAMES[cmd])
    elif cmd in _POSITIONAL_GRAMMARS:
        values, k, default = _POSITIONAL_GRAMMARS[cmd]
        for options, operands, stuck in _scan(args, values, settle=settle):
            got.read([(options, operands, stuck)], "c" if cmd == "sysread" else "")
            callback = next((v for c, v in options if c == "C" and cmd != "sysread"), None)
            if callback is not None:
                got.evaluated = callback
            got.at(operands, k)
        if not got.names and default is not None and got.stuck is None:
            got.names.append(default)
    elif cmd == "getopts":
        got.at(args, 1)
        got.names.extend(("OPTARG", "OPTIND"))
    elif cmd == "getln":
        got.read(_scan(args, "", settle=settle), "", every=True)
    elif cmd == "unset":
        for options, operands, stuck in _scan(args, "", settle=settle):
            letters = {c for c, _ in options}
            if "m" in letters:  # zsh's pattern: whichever names match it
                got.refuse(operands[0] if operands else "-m")
            elif "f" not in letters or "v" in letters:
                got.read([(options, operands, stuck)], "", every=True)
            elif stuck is not None:
                got.refuse(stuck)
    elif cmd == "set":
        got.read(_scan(args, "Ao", plus=True, settle=settle), "A")
    elif cmd in ("zstyle", "zformat", "zsystem", "zparseopts"):
        _module_names(cmd, args, got, settle)
    elif cmd in ("zregexparse", "zpty"):
        # zregexparse's two parameters before its regexes (probed: `zregexparse p q a` set p and q to 0), and `zpty -r
        # [-mt] name [param [pattern]]`'s param
        for options, operands, stuck in _scan(args, "", settle=settle):
            got.read([(options, [], stuck)], "")
            if cmd == "zregexparse":
                got.at(operands, 0)
                got.at(operands, 1)
            elif "r" in {c for c, _ in options}:
                got.at(operands, 1)
    elif cmd in ("zsocket", "ztcp"):
        got.names.append("REPLY")  # the descriptor they open
    elif cmd not in _ASSIGNING_NOTHING:  # zcurses, and a loop keyword read as a command: every name any word spells
        for word in args:
            if _word_reach(word) != "plain":
                got.refuse(word)
                break
            got.names.extend(syntax._NAME_RE.findall(prepare.deglob(word)))
        got.names.extend(("REPLY", "reply"))
    return got.names, got.subscripts, got.stuck, got.evaluated


def _module_names(cmd, args, got, settle=None):
    """builtin_names for zsh's zstyle, zformat, zsystem and zparseopts, whose first word decides where a name stands:
    `zstyle -s|-b|-a context style name` and `zstyle -g name` (probed: `zstyle -s ':x' y V` set V), `zformat -f|-F|-a name`
    (`zformat -f V '%a' a:1` set V), `zsystem flock -f var file`, and zparseopts's `-a`, `-A` and `-v` arrays and each
    spec's `=array`.  `zstyle -e` defines a style whose value zsh evaluates when it is looked up: code, refused unread.
    A first word the hook cannot read is refused where it may be an option (_may_open), zsystem's subcommand or a
    zparseopts spec, and read as the operand it is otherwise (`zstyle $context style value` defines a style)."""
    if not args:
        return
    if _word_reach(args[0]) != "plain":
        if _may_open(args[0], settle) or cmd in ("zparseopts", "zsystem"):
            got.refuse(args[0])
        return
    first = prepare.deglob(args[0])
    if cmd == "zstyle":
        if first == "-e":
            got.evaluated = " ".join(args)
        elif first in ("-s", "-b", "-a", "-g"):
            got.at(args[1:], 0 if first == "-g" else 2)
    elif cmd == "zformat":
        if first in ("-f", "-F", "-a"):
            got.at(args[1:], 0)
    elif cmd == "zsystem":
        if first == "flock":
            got.read(_scan(args[1:], "tf"), "f")
    else:
        _zparseopts_names(args, got)


def _zparseopts_names(args, got):
    """zparseopts's own options (-D -E -F -K -M flags, -a, -A and -v arrays) up to `-`, `--` or the first spec, then each
    spec's array, the name after its last `=` (probed: `zparseopts -a A h=H -help=H2` set A, H=-h and H2=--help)."""
    i = 0
    while i < len(args):
        word = args[i]
        if _word_reach(word) != "plain":
            got.refuse(word)
            return
        text = prepare.deglob(word)
        if text in ("-", "--"):
            i += 1
            break
        if text[:2] in ("-a", "-A", "-v"):
            if len(text) > 2:
                got.name(text[2:])
                i += 1
            else:
                got.name(args[i + 1] if i + 1 < len(args) else None)
                i += 2
            continue
        if len(text) > 1 and text[0] == "-" and set(text[1:]) <= set("DEFKM"):
            i += 1
            continue
        break
    for word in args[i:]:
        if _word_reach(word) != "plain":
            got.refuse(word)
            return
        spec = prepare.deglob(word)
        if "=" in spec:
            got.name(spec.rsplit("=", 1)[1])
