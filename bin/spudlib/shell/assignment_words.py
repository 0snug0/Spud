"""shell/assignment_words: The words a shell reads as an assignment, subscripted ones included, and what zsh's special
associations and bash's environment bind through them (SPD-085, SPD-105, SPD-106).

A module of its own because three readers share it and one of them cannot reach the rest of the shell reading: zsh.py
(which imports syntax alone) asks whether a word before `(` is an array assignment's head and whether a word keeps zsh's
command position, walk.py joins `name[subscript]=( ... )` into one word, and analyse.py reads a prefix or a declaration
operand as the assignment it is.  It reads words only -- nothing here touches a ShellAnalysis, which analyse.py records
into -- so it stays out of the shell reading's import cycle."""

import re

from . import prepare, syntax
from ..hooks import hookio


# SPD-085: `name[subscript]=value` and `name[subscript]+=value` are assignments, not command words, in zsh and bash (probed
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
# SPD-105: zsh's special associations whose elements the shell runs by name (the zsh/parameter module, loaded under -f):
# each key of `functions` is a shell function, of `commands` a hashed command, of `aliases`, `galiases` and `saliases` an
# alias, a global alias and a suffix alias (as `alias`, `alias -g` and `alias -s` define them).  Probed in zsh 5.9 -f and
# -o nobareglobqual: `functions[foo]='echo SH'; foo`, `functions+=(foo 'echo SH'); foo`, `functions=(foo 'echo SH'); foo`
# (it adds its pairs and removes no other function), `typeset`/`declare`/`export 'functions[foo]=echo SH'`, a prefix
# (`functions[foo]='echo SH' true; foo`) and `functions[foo]+=x` each ran the function; `commands[foo]=<path>; foo` and
# `command foo` ran the file; `aliases[gp]='echo SH'; eval gp` and `aliases=(gp 'echo SH'); eval gp` ran the alias
# (`aliases+=( ... )` defined nothing in 5.9 -- recorded all the same, refusing on doubt); `dis_functions` and
# `dis_aliases` bind disabled entries, which ran nothing.  bash and sh have none of these: there they are plain arrays.
SPECIAL_TABLES = {"functions": "function", "commands": "hashed", "aliases": "alias", "galiases": "alias", "saliases": "alias"}
# SPD-106: the variables bash and sh (bash in POSIX mode) import a shell function from: `BASH_FUNC_<name>%%=() { body; }`
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


def array_head(word):
    """True for the word before an array's `(`: `name=`, `name+=`, `name[subscript]=` or `name[subscript]+=` (SPD-085:
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
