"""shell/loop_bindings: the values a for loop's words and a `$(basename ...)` give a write target, read once per value.

SPD-121 refused every write target holding an expansion the hook cannot settle, and SPD-127 settled only what the line
assigns plainly (arg_writes.resolved), so `for f in a b; do touch tests/$f; done` and `curl -o "$(basename $p)"` stayed
raw and refused, though the loop's words are on the line.  SPD-126's differential over the 945 Bash commands spudagents
ran that name a download or a spelled write found 16 new refusals, 12 of exactly this shape, all in a session scratchpad.

A `for NAME in WORD ...` loop (bind_loop) whose every word the line settles -- a literal word, a value the line settled,
or a glob matching files now, read as the files it matches -- gives NAME one value per word while its body is read, as a
glob target is read once per match.  A word that holds a blank, a quoted glob character, a brace list, a zsh pattern or
anything unsettled, a glob that may start with `-`, `~` or `=` or matches nothing, settles no loop, and nor do more than
LOOP_WORDS_CAP words.  A spelled word that may start with `-`, `~` or `=` settles the loop for the dispatch alone
(dispatch_loop, SPD-274: `for o in --tags --prune; do git fetch $o origin; done` is two fetches, each option read where it
stands, `--upload-pack=sh` refused as spelled), never for a write channel (bound_loop), where an option is no path.  The binding ends where the loop closes, and where anything assigns the name or doubts it
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

SPD-221: the same assignment in a loop's body (`for f in a b; do out=$(basename "$f"); curl -o "$out" ...; done`) is
doubted as every loop body's is, so SPD-146 left it refused while the substitution written in the operand was read.  Where
the assignment is certain in every pass -- a command standing directly in a settled for loop's body (certain_loop), not
after `&&` or `||`, not in a pipeline, the background, a group, a condition, a subshell, a function body or behind
coproc, and the name nothing assigns unseen (`${NAME:=}`, a function body) -- it is evaluated there once per value of
each loop it reads (loop_table) and kept in `loop_derived`, and a write naming `$NAME` later in that body takes, in each
reading, the value the pass of that reading printed.  It ends where the name or a loop it read may hold something else
(forget): any assignment of either, `read`, `unset`, an attribute, a prefix, another loop over either, the background, the
loop's close; it holds only where the function bodies open are those it ran in.  A write before it in the body, after the
loop, or anywhere the entry ended reads the raw word, and the refusal it earns.

A write channel resolves its words once per reading (per_reading, resolved_words): the product of the loop values its words
name, at most READINGS_CAP of them, each with the derived names and substitutions evaluated under it.  Every other
reader, and a word that names nothing bound, reads as before: the raw word and the refusal it earns.

Past 250 lines (the look-again point) it stays whole: one reading with one user, arg_writes.resolved, whose two halves --
a loop's words and basename's output -- meet in the one binding each reading carries, and a loop body's basename is both
at once; no definition here is long."""

import itertools

from . import arg_writes, globbing, git_programs, held_text, line_functions, prepare, redirect_globs, syntax, zsh
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
            forget(a, words[:1])
        return
    name, values = words[0], []
    forget(a, (name,))  # a basename a loop body read with an earlier binding of the name no longer stands (SPD-221)
    for word in words[2:]:
        found = loop_values(word, a)
        if found is None or len(values) + len(found) > LOOP_WORDS_CAP:
            a.loop_words.pop(name, None)
            return
        values += found
    plain = all(prepare.deglob(v)[0] not in "-~=" for v in values)
    a.loop_words[name] = (tuple(values), a.func_depth, plain)
    # a loop the write channels do not read (a value may start with `-`, `~` or `=`) is held as (name,), so certain_loop
    # reads no basename in its body per pass, as before SPD-274; unbind_frame ends either at the close
    frame.bound = name if plain else (name,)


def unbind_frame(frame, a):
    """The loop closed: after it the name holds its last word, or what it held before when there were none."""
    if frame.bound is not None:
        name = frame.bound if isinstance(frame.bound, str) else frame.bound[0]
        a.loop_words.pop(name, None)
        if a.loop_derived:
            forget(a, (name,))
            for name in [n for n, entry in a.loop_derived.items() if entry[1] is frame]:
                del a.loop_derived[name]  # its body's basenames: after the loop a name holds its last pass's


def unbind(a, names):
    """Something assigned or doubted these names: a loop's binding of any of them ends, and a loop body's basename."""
    if a.loop_words:
        for name in names:
            a.loop_words.pop(name, None)
    forget(a, names)


def forget(a, names):
    """These names may hold something else now: a loop body's basename (SPD-221) assigned to any of them, or read with a
    loop word any of them held, no longer stands."""
    if a.loop_derived and names:
        names = set(names)
        for name in [n for n, entry in a.loop_derived.items() if n in names or names.intersection(entry[3])]:
            del a.loop_derived[name]


def loop_values(word, a):
    """The values one masked word of a for list gives the variable, as masked words, or None when the line does not
    settle them (module docstring)."""
    value = arg_writes.resolved(word, a)
    if arg_writes.unresolved(value) or syntax._ARRAY_VALUE in value or _LOOP_BRACE_RE.search(value) \
            or any(c in value for c in syntax._ZSH_UNSENTINEL):
        return None
    text = prepare.deglob(value)
    if not text:
        return None
    if globbing.active_glob_word(value):
        if text[0] in "-~=":
            return None
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
    and kept as `derived` (the value, what basename prints) for as long as that value stands (derived_value) -- or, where
    it stands directly in a settled for loop's body and runs in every pass, once per pass as `loop_derived` (SPD-221)."""
    a.loop_words.pop(name, None)
    a.derived.pop(name, None)
    forget(a, (name,))
    unseen = name in a.unseen_assigned
    if a.func_depth:
        a.unseen_assigned.add(name)  # a function body assigns it again wherever it is called
    if append or value.replace(syntax._QUOTED_SUBST, "") != hookio.SUBST:
        return
    bodies = a.subst_words.get(name + "=" + value)
    spec = basename_spec(bodies[0]) if bodies is not None and len(bodies) == 1 else None
    if spec is None:
        return
    frame = a.body_loop
    if frame is not None:
        if not (a.unsure or unseen or a.all_doubt):
            table = loop_table(spec, a)
            if table is not None:
                a.loop_derived[name] = (value, frame, a.func_depth) + table
        return
    found = evaluate_basename(spec, a)
    if found is not None:
        a.derived[name] = (value, found)


def certain_loop(stack):
    """The for loop frame a simple command stands in directly, in its body, where the loop's words settled (bind_loop):
    the command runs once in every pass, unless what is around it within the body makes it conditional (ShellWalk.finish
    passes None then).  None for a command in any other compound command, a group or a condition among them."""
    top = stack[-1] if stack else None
    if top is not None and top.kind == "loop" and isinstance(top.bound, str) and top.body in ("long", "sublist"):
        return top
    return None


def loop_table(spec, a):
    """(the loop names a loop body's `$(basename ...)` reads, ((their values, what it printed) per pass)) as the shell
    runs it in each pass of the loops around it (module docstring), or None where a pass's result is not basename's."""
    words = [spec[0]] if spec[1] is None else [spec[0], spec[1]]
    names, rows, outer = None, [], a.binding
    try:
        for binding in write_readings(words, a):
            a.binding = binding
            found = evaluate_basename(spec, a)
            if found is None:
                return None
            if names is None:
                names = tuple(n for n in (binding or ()) if isinstance(n, str) and bound_loop(n, a) is not None)
            rows.append((tuple(binding[n][0] for n in names), found))
    finally:
        a.binding = outer
    return names, tuple(rows)


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
    if not (a.loop_words or a.derived or a.subst_words or a.loop_derived):
        return _NO_READINGS
    loops, derived, substs, passes = {}, {}, {}, {}

    def note(word):
        if "$" not in word:
            return
        for m in arg_writes._EXPANSION_RE.finditer(word):
            name = m.group(1) or m.group(2)
            if name in loops or name in derived or name in passes:
                continue
            values = bound_loop(name, a)
            if values is not None:
                loops[name] = values
                continue
            found = derived_value(name, a)
            if found is not None:
                derived[name] = found
                continue
            entry = loop_derived_value(name, a)
            if entry is not None:
                passes[name] = entry
                for key in entry[0]:
                    loops.setdefault(key, bound_loop(key, a))

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
    if not (loops or derived or passes or any(s is not None for specs in substs.values() for s in specs)):
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
            for name, (keys, rows) in passes.items():  # what the loop body's basename printed in this pass (SPD-221)
                key = tuple(binding[k][0] for k in keys)
                found = next((f for values, f in rows if values == key), None)
                if found is not None:
                    binding[name] = found
            a.binding = binding
            for word, specs in substs.items():
                binding[("subst", word)] = [None if s is None else evaluate_basename(s, a) for s in specs]
            out.append(binding)
    finally:
        a.binding = outer
    return out


def bound_loop(name, a):
    """The values a loop bound `name` to, where that binding holds here and no value may start with `-`, `~` or `=` (a
    write channel's rule: an option is no path), or None."""
    entry = a.loop_words.get(name)
    if entry is None or not entry[2] or entry[1] != a.func_depth or name in a.sticky or a.all_doubt:
        return None
    return entry[0]


def dispatch_loop(name, a):
    """The values a loop bound `name` to, where that binding holds here, whatever they start with, or None: the
    dispatch reads each where it stands as though spelled, an option among them as that option (SPD-274)."""
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


def loop_derived_value(name, a):
    """(the loop names, the rows) of a loop body's basename (loop_table) where its value still stands here: the name holds
    what that assignment gave it, in the function body it ran in, and every loop it read still holds the binding it read
    (forget drops the entry wherever any of that may have changed), or None."""
    entry = a.loop_derived.get(name)
    if entry is None or a.vars.get(name) != entry[0] or entry[2] != a.func_depth or a.all_doubt \
            or name in syntax.DYNAMIC_VARIABLES or any(bound_loop(k, a) is None for k in entry[3]):
        return None
    return entry[3], entry[4]


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
    snapshot takes the name, the line assigns no PATH, and the snapshot could be read.

    SPD-303: the snapshot only where the shell sourced it (held_text.snapshot_sourced, SPD-298): a new shell's text runs
    the program, so under a profile's `basename () { ...; }` `sh -c 'echo hi > "tests/$(basename a/x.txt)"'` writes
    tests/x.txt, and a snapshot it never sources, or could not read, hides nothing there (tests/test_hooks_groups.py
    NewShellSnapshotLookupTest).  Its aliases too, which a snapshot body's substitution, parsed as the body runs, does
    expand, so line_aliases.held_standing, whose `early` mark such a body carries, is not the test here.

    SPD-307: the snapshot's function only where line_functions.held_function still finds it standing there, so a line's
    own `unset -f basename` (sure or maybe -- a removal that may not have run keeps today's cautious reading) takes the
    program back, or leaves it, in place of a bare snapshot lookup (tests/test_hooks_groups.py
    SnapshotPrinterRemovalTest)."""
    if {"basename", syntax.UNKNOWN_NAME} & (a.functions | a.hashed) or "basename" in a.aliases or a.alias_unknown:
        return False
    if git_programs.path_in_force(a.vars) is not None:
        return False
    if not held_text.snapshot_sourced(a):
        return True
    table = snapshots.shell_table(a.home)
    return table.gap is None and "basename" not in table.aliases and not line_functions.held_function(a, "basename")


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
