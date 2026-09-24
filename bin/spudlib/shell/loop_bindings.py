"""shell/loop_bindings: the values a for loop's words and a `$(basename ...)` give a write target, read once per value.

SPD-121 refused every write target holding an expansion the hook cannot settle, and SPD-127 settled only what the line
assigns plainly (arg_writes.resolved), so `for f in a b; do touch tests/$f; done` and `curl -o "$(basename $p)"` stayed
raw and refused, though the loop's words are on the line.  SPD-126's differential over the 945 Bash commands spudagents
ran that name a download or a spelled write found 16 new refusals, 12 of exactly this shape, all in a session scratchpad.

A `for NAME in WORD ...` loop (bind_loop) whose every word the line settles -- a literal word, a value the line settled,
or a glob matching files now, read as the files it matches -- gives NAME one value per word while its body is read, as a
glob target is read once per match.  A word that may start with `-`, `~` or `=`, holds a blank, a quoted glob character,
a brace list, a zsh pattern or anything unsettled, or a glob that matches nothing, settles no loop, and nor do more than
LOOP_WORDS_CAP words.  The binding ends where the loop closes, and where anything assigns the name or doubts it
(unbind): an assignment, `read`, `unset`, a declaration's attribute, `${NAME:=}`, code the hook does not read; and it is
not read inside a function body opened in the loop, which runs where it is called.

`$(basename WORD [SUFFIX])` -- the body one plain call of the program, with `--` or neither option, no redirection, no
pipe, no nested substitution, and no alias, function, hash or PATH of the line's or the shell's own for the name -- is
settled (evaluate_basename): exactly, where WORD and SUFFIX settle, when the result is one plain name (no `/`, blank or glob
character, not starting with `-`, `~` or `=`); where they do not, as one name, pathrule.NAME_CHAR and NAME_MORE, which
every wildcard of a deliverable glob matches and no literal does, so a glob lets it in only when it lets in every name
in that directory -- basename's result holds no `/`.  That one-name reading is taken only where the substitution stands
in double quotes (syntax._QUOTED_SUBST), so its output is one word, and after something the word spells, so it is no
option either.  The same holds for a name a certain `NAME=$(basename ...)` assigned (`derived`), evaluated where the
assignment runs and read as `"$NAME"` while that value stands.

A write channel resolves its words once per reading (per_reading, resolved_words): the product of the loop values its words
name, at most READINGS_CAP of them, each with the derived names and substitutions evaluated under it.  Every other
reader, and a word that names nothing bound, reads as before: the raw word and the refusal it earns.

Past 250 lines (the look-again point) it stays whole: one reading with one user, arg_writes.resolved, whose two halves --
a loop's words and basename's output -- meet in the one binding each reading carries, and no definition here is long."""

import itertools

from . import arg_writes, globbing, git_programs, prepare, redirect_globs, syntax, zsh
from ..core import lazy
from ..hooks import hookio, pathrule, snapshots

LOOP_WORDS_CAP = 64  # the most values one loop's words settle to before the loop settles nothing
READINGS_CAP = 64  # the most readings one write channel's words are resolved under before none is bound
_LOOP_BRACE_RE = lazy.LazyPattern(r"\{[^}]*(?:,|\.\.)[^}]*\}")  # an unquoted brace list, which expands whatever exists
_BASENAME_OPERATOR_RE = lazy.LazyPattern(r"[;&|<>()]+\Z")  # a token shlex gives an operator
ONE_NAME = pathrule.NAME_CHAR + pathrule.NAME_MORE  # one name of any length, which only a wildcard matches
_NO_READINGS = (None,)
_BASENAME_SPECS = {}  # body text -> (WORD, SUFFIX or None, whether `--` ended basename's options) or None, per process


def bind_loop(words, frame, a):
    """A `for` header's words are complete: bind its variable to the values its words settle to, or unbind the name when
    they settle to none.  `frame`, the loop's, holds the name until it closes (unbind_frame)."""
    if len(words) < 3 or words[1] != "in" or syntax.IDENTIFIER_RE.match(words[0]) is None:
        if words and syntax.IDENTIFIER_RE.match(words[0]) is not None:
            a.loop_words.pop(words[0], None)
        return
    name, values = words[0], []
    for word in words[2:]:
        found = loop_values(word, a)
        if found is None or len(values) + len(found) > LOOP_WORDS_CAP:
            a.loop_words.pop(name, None)
            return
        values += found
    a.loop_words[name] = (tuple(values), a.func_depth)
    frame.bound = name


def unbind_frame(frame, a):
    """The loop closed: after it the name holds its last word, or what it held before when there were none."""
    if frame.bound is not None:
        a.loop_words.pop(frame.bound, None)


def unbind(a, names):
    """Something assigned or doubted these names: a loop's binding of any of them ends."""
    if a.loop_words:
        for name in names:
            a.loop_words.pop(name, None)


def loop_values(word, a):
    """The values one masked word of a for list gives the variable, as masked words, or None when the line does not
    settle them (module docstring)."""
    value = arg_writes.resolved(word, a)
    if arg_writes.unresolved(value) or syntax._ARRAY_VALUE in value or _LOOP_BRACE_RE.search(value) \
            or any(c in value for c in syntax._ZSH_UNSENTINEL):
        return None
    text = prepare.deglob(value)
    if not text or text[0] in "-~=":
        return None
    if globbing.active_glob_word(value):
        expansion = redirect_globs.expand_redirect_target(value, a.cwds)
        if expansion is None or expansion[1] or not expansion[0]:
            return None  # a directory it cannot follow, past the budget, or no match (bash's literal word, zsh's error)
        matches = expansion[0]
        if not text.startswith("/"):
            if not a.cwds or len(a.cwds) != 1:
                return None
            prefix = next(iter(a.cwds)).rstrip("/") + "/"
            if not all(m.startswith(prefix) for m in matches):
                return None
            matches = [m[len(prefix) :] for m in matches]
        if any(not plain_name(m) for m in matches):
            return None
        return [globbing.literalize(m) for m in matches]
    if syntax._IFS_BLANKS_RE.search(text) or syntax.GLOB_RE.search(text):
        return None
    return [value]


def plain_name(text):
    """True when a literal text passes through an unquoted expansion as itself: not empty, no blank or glob character,
    and nothing at its start an option, a tilde or zsh's EQUALS would read."""
    return bool(text) and text[0] not in "-~=" and not syntax._IFS_BLANKS_RE.search(text) and not syntax.GLOB_RE.search(text)


def loop_assigned(a, name, value, append):
    """expansions.assign_variable's record: the name's loop binding ends, and a value that is one lifted basename call
    whose body the command's words hold is evaluated here, where the shell runs it, with the values the line holds now,
    and kept as `derived` (the value, what basename prints) for as long as that value stands (derived_value)."""
    a.loop_words.pop(name, None)
    a.derived.pop(name, None)
    if append or value.replace(syntax._QUOTED_SUBST, "") != hookio.SUBST:
        return
    bodies = a.subst_words.get(name + "=" + value)
    spec = basename_spec(bodies[0]) if bodies is not None and len(bodies) == 1 else None
    found = evaluate_basename(spec, a) if spec is not None else None
    if found is not None:
        a.derived[name] = (value, found)


def per_reading(words, a):
    """Run the caller's loop body once per reading of `words`, with a.binding set to it: once, unbound, for words that
    name no bound loop variable, derived name or settled substitution."""
    outer = a.binding
    for binding in write_readings(words, a):
        a.binding = binding
        try:
            yield binding
        finally:
            a.binding = outer


def resolved_words(words, a):
    """[the words resolved (arg_writes.resolved) under one reading] per reading."""
    return [[arg_writes.resolved(w, a) for w in words] for _ in per_reading(words, a)]


def write_readings(words, a):
    """The bindings `words` are resolved under (module docstring): one per combination of the bound loop values they name,
    each mapping a loop name and a derived name to (value, exact) and a word holding substitutions to one such pair per
    substitution (None for one that does not settle); (None,) when nothing they name is bound."""
    if not (a.loop_words or a.derived or a.subst_words):
        return _NO_READINGS
    loops, derived, substs = {}, {}, {}

    def note(word):
        if "$" not in word:
            return
        for m in arg_writes._EXPANSION_RE.finditer(word):
            name = m.group(1) or m.group(2)
            if name in loops or name in derived:
                continue
            values = bound_loop(name, a)
            if values is not None:
                loops[name] = values
                continue
            found = derived_value(name, a)
            if found is not None:
                derived[name] = found

    for word in words:
        note(word)
        if hookio.SUBST in word:
            bodies = a.subst_words.get(word)
            if bodies:
                specs = [basename_spec(b) for b in bodies]
                substs[word] = specs
                for spec in specs:
                    if spec is not None:
                        note(spec[0])
                        if spec[1] is not None:
                            note(spec[1])
    if not (loops or derived or any(s is not None for specs in substs.values() for s in specs)):
        return _NO_READINGS
    count = 1
    for values in loops.values():
        count *= len(values)
    if count > READINGS_CAP:
        return _NO_READINGS
    out, outer = [], a.binding
    try:
        for combination in itertools.product(*loops.values()):
            binding = dict(derived, **{name: (value, True) for name, value in zip(loops, combination)})
            a.binding = binding
            for word, specs in substs.items():
                binding[("subst", word)] = [None if s is None else evaluate_basename(s, a) for s in specs]
            out.append(binding)
    finally:
        a.binding = outer
    return out


def bound_loop(name, a):
    """The values a loop bound `name` to, where that binding holds here, or None."""
    entry = a.loop_words.get(name)
    if entry is None or entry[1] != a.func_depth or name in a.sticky or a.all_doubt:
        return None
    return entry[0]


def derived_value(name, a):
    """(what basename printed, exact) for a name a certain `name=$(basename ...)` assigned, where that value still
    stands, or None."""
    entry = a.derived.get(name)
    if entry is None or a.vars.get(name) != entry[0] or name in a.doubt or name in a.sticky or a.all_doubt:
        return None
    return entry[1]


def basename_spec(body):
    """(WORD, SUFFIX or None, whether `--` ended the options) when a substitution's body is one plain basename call, else
    None: no redirection, pipe, list, here-document, nested substitution, zsh pattern or option other than `--`."""
    if body in _BASENAME_SPECS:
        return _BASENAME_SPECS[body]
    spec = None
    if "basename" in body and "<<" not in body and "$'" not in body and '$"' not in body:
        outer, inner = prepare.split_substitutions(prepare.newlines_as_separators(body))
        if not inner:
            marked, other = zsh.mark_zsh_patterns(prepare.neutralize_quoted_globs(outer))
            tokens = syntax.shell_tokens(marked) if other == marked else None
            if tokens and tokens[0] == "basename" and not any(_BASENAME_OPERATOR_RE.match(t) for t in tokens):
                rest, ended = tokens[1:], False
                if rest[:1] == ["--"]:
                    rest, ended = rest[1:], True
                if len(rest) in (1, 2) and (ended or not prepare.deglob(rest[0]).startswith("-")):
                    spec = (rest[0], rest[1] if len(rest) == 2 else None, ended)
    if len(_BASENAME_SPECS) < 256:
        _BASENAME_SPECS[body] = spec
    return spec


def basename_runs(a):
    """True when `basename` on this line runs the program: no function, alias or hash of the line's or of the shell's
    snapshot takes the name, the line assigns no PATH, and the snapshot could be read."""
    if {"basename", syntax.UNKNOWN_NAME} & (a.functions | a.hashed) or "basename" in a.aliases or a.alias_unknown:
        return False
    if git_programs.path_in_force(a.vars) is not None:
        return False
    table = snapshots.shell_table(a.home)
    return table.gap is None and "basename" not in table.aliases and "basename" not in table.functions


def evaluate_basename(spec, a):
    """(the masked word basename prints under a.binding, True) where its operands settle to a plain name; (ONE_NAME,
    False) where they do not, basename printing one name all the same; None where the program may not be basename's."""
    if not basename_runs(a):
        return None
    word, suffix, ended = spec
    value = arg_writes.resolved(word, a)
    tail = arg_writes.resolved(suffix, a) if suffix is not None else None
    if not operand_settled(value) or (tail is not None and not operand_settled(tail)):
        return ONE_NAME, False
    text = prepare.deglob(value)
    if not text or (text.startswith("-") and not ended):
        return None
    result = basename_of(text, prepare.deglob(tail) if tail is not None else None)
    if "/" in result or not plain_name(result):
        return None
    return globbing.literalize(result), True


def operand_settled(value):
    return not arg_writes.unresolved(value) and syntax._ARRAY_VALUE not in value and not globbing.active_glob_word(value)


def basename_of(text, suffix=None):
    """What basename(1) prints for a string and a suffix (this Mac's, and POSIX's): trailing slashes dropped, the last
    component, the suffix removed when the name ends in it and is more than it; `/` for a string of slashes alone."""
    stripped = text.rstrip("/")
    if not stripped:
        return "/"
    name = stripped.rpartition("/")[2]
    if suffix and name != suffix and name.endswith(suffix):
        name = name[: -len(suffix)]
    return name


def with_basenames(word, binding):
    """The word with each lifted substitution the reading settled put in its place (module docstring); one it does not
    settle stays as its placeholder, which leaves the word unresolved."""
    results = binding.get(("subst", word))
    if not results:
        return word
    out, at = [], 0
    for found in results:
        k = word.find(hookio.SUBST, at)
        if k < 0:
            break
        end = k + len(hookio.SUBST)
        quoted = word.startswith(syntax._QUOTED_SUBST, end)
        end += quoted
        out.append(word[at:k])
        if found is None or (not found[1] and (not quoted or k == 0)):
            out.append(word[k:end])
        else:
            out.append(found[0])
        at = end
    out.append(word[at:])
    return "".join(out)
