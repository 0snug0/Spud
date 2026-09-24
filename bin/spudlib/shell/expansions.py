"""shell/expansions: Parameter expansions and a command's read points."""

import functools

from . import analyse, arg_writes, arithmetic_assignments, assigning_builtins, assignment_words, git_programs, git_writes, globbing, loop_bindings, prepare, spud_calls, syntax, unread
from ..core import lazy
from ..hooks import hookio, snapshots


# What reading one word leaves its caller to do: read on from where the word was (its readings replaced it in place),
# stop (its readings were each analysed whole, or the command word cannot be read), or read on past it (it stays as spelled).
_AGAIN, _STOP, _FLAGGED = "again", "stop", "flagged"
_ACTIVATE_GLOBS = str.maketrans({syntax._GLOB_SENTINELS[c]: c for c in "*?[]"})  # bash globs an unquoted expansion, however it was assigned
_ZSH_PLAIN = str.maketrans(syntax._ZSH_UNSENTINEL)
_BRACES_PLAIN = str.maketrans({syntax._GLOB_SENTINELS["{"]: "{", syntax._GLOB_SENTINELS["}"]: "}"})


def expansion_word(word, command=False):
    """True when the shell expands a parameter, arithmetic or a substitution in this word: a `$` neutralize_quoted_globs
    did not mark literal, or a lifted `$(...)` or backtick body.  In the command word a bare `$X` is read as one whatever its
    quoting (`'$X' push` stays refused)."""
    if hookio.SUBST in word or syntax._EXPANDING_DOLLAR_RE.search(word):
        return True
    return command and variable_reference(word, command) is not None


def variable_reference(word, command=False):
    """The name of a bare `$X` or `${X}` the word is, quoted or not (`"${X}"`'s braces reach it as quoted glob sentinels); in the
    command word a literal-marked dollar counts too; else None."""
    text = word.translate(_BRACES_PLAIN)
    if command:
        text = text.replace(syntax._LITERAL_DOLLAR, "")
    ref = syntax.VARREF_RE.match(text)
    return ref and (ref.group(1) or ref.group(2))


def active_read_word(word):
    """A word the dispatch reads by name that must be resolved before it is read: an expansion or a glob."""
    return expansion_word(word) or globbing.active_glob_word(word)


def assign_variable(a, name, value, append=False, spelled=True):
    """Record `name=value` (or `name+=value`) where the shell runs it.  An appended value is not known (CDPATH's reads as
    `$`, which cd_target does not follow; any other as a substitution).  The value is certain unless the assignment may not run
    or persist here (a.unsure) or runs in a loop or function body, which may assign again later (sticky); a certain assignment
    settles an earlier doubt.  `spelled`: the value is the text the line assigns, which may carry what the member supplies
    (fill_from); assign_unknown's stands for a value the hook does not read, whose caller reads its source itself."""
    if spelled:
        fill_from(a, (name,), value, value_bodies(a, name, value))  # before the value changes: `V=$(echo $V)` reads the old
    a.vars[name] = ("$" if name in ("CDPATH", "cdpath") else hookio.SUBST) if append else value
    loop_bindings.loop_assigned(a, name, value, append)  # a loop's binding of the name ends; `name=$(basename ...)` kept (SPD-146)
    a.assigned.append(name)
    a.line_assigned.update(a.reaching((name,)))  # the line's variable, unless a function body's local (SPD-246)
    if a.unsure or a.loop_depth:
        a.doubt.add(name)
        if a.loop_depth:
            a.sticky.add(name)
    elif name not in a.sticky:
        a.doubt.discard(name)


# SPD-225 and SPD-254: the assignments a line makes by other grammars than `name=value` -- an arithmetic evaluation, an
# assigning builtin's name operands -- recorded as the line's, where the shell makes them, as assign_variable records one.
# The operators of `[[ ... ]]` whose operands both shells evaluate as arithmetic (probed: `[[ X=22 -eq 22 ]]` and `[[ 1 -lt
# X=60 ]]` assigned X in zsh 5.9 and bash 3.2.57; `[[ X=61 == 61 ]]` and `[ 1 -eq 1 ]` evaluate none)
_ARITHMETIC_TESTS = frozenset(("-eq", "-ne", "-lt", "-le", "-gt", "-ge"))


def assign_unknown(a, name):
    """Record an assignment of a value the hook does not read -- a builtin's (`read X`, `printf -v X`, `unset X`), an
    arithmetic evaluation's that is no literal -- as an appended value is recorded: unknown, the name's loop binding and
    basename gone, doubted where the assignment may not run or persist."""
    assign_variable(a, name, hookio.SUBST, append=True, spelled=False)


def fill_from(a, names, text, bodies=(), arithmetic=False):
    """Record `names`, just assigned from `text` -- a value, a for or select list's word, an arithmetic expression, an
    assigning builtin's words -- as filled by what the member supplies wherever that text can carry it, so a finding naming
    one of them is not pruned as a function body's own (held_text.analyse_shell_text).  `bodies`: the substitutions the text
    holds, as the command lifted them (value_bodies); None for one the hook cannot pair with its text.

    The call's words (a.member_vars, SPD-205): a positional in the text or a substitution's; a substitution shell/positional
    set the words in (a.filled_texts, SPD-258: `V=$(echo "$1")` read as `V=$(echo push)`); a variable they fill; a variable
    holding one of the words as it stands (`V="$1"` read as `V=push`, then `W=$(echo $V)`); and in `arithmetic`, which reads
    a name bare, a word of the call's among its names and numbers (`(( N = $1 ))` read as `(( N = push ))`, which reads the
    variable push).  The line's own variables (a.line_filled, SPD-253): a variable the line assigned before the shell's text,
    or one such a variable fills (`_cc_bin="${CLAUDE_CODE_EXECPATH:-}"`).  A substitution the hook cannot pair counts as
    both, fail closed.  Outside the shell's text only the positional counts: the line's own text is the member's already."""
    plain = prepare.deglob(text)
    known = [b for b in bodies if b is not None]
    if syntax.POSITIONAL_RE.search(plain) is not None or any(b in a.filled_texts or syntax.POSITIONAL_RE.search(b) for b in known):
        a.fill_members(names)
    if not a.shell_reading:
        return
    unpaired = len(known) < len(bodies)
    read = {name for source in [plain] + known for name in syntax.READ_NAME_RE.findall(source)}
    if arithmetic:
        read.update(_ARITHMETIC_NAME_RE.findall(plain))
    if unpaired or not read.isdisjoint(a.shell_line_vars) or not read.isdisjoint(a.line_filled):
        a.line_filled.update(names)
    if unpaired or not read.isdisjoint(a.member_vars):
        a.fill_members(names)
        return
    if not (read or arithmetic) or not a.shell_words:
        return  # a literal list word or value reads no variable: a long loop list stays linear
    words = {prepare.deglob(w) for w in a.shell_words if w}
    if any(held_word(a, name, words) for name in read) \
            or (arithmetic and not words.isdisjoint(_ARITHMETIC_TOKEN_RE.findall(plain))):
        a.fill_members(names)


_ARITHMETIC_NAME_RE = lazy.LazyPattern(r"(?<![\w$#])[A-Za-z_][A-Za-z0-9_]*")  # a name arithmetic reads with no `$`
_ARITHMETIC_TOKEN_RE = lazy.LazyPattern(r"[A-Za-z0-9_]+")  # the names and numbers an arithmetic expression spells


def held_word(a, name, words):
    """Whether the variable `name` holds, as the line reads it here, one of the call's own words (`words`, as fill_from
    reads them): the value shell/positional set there, which no reference marks any more."""
    value = a.vars.get(name)
    return value is not None and hookio.SUBST not in value and prepare.deglob(value) in words


def value_bodies(a, name, value):
    """The bodies of the substitutions an assignment's value holds, as the command being read lifted them (a.subst_words,
    ShellWalk.consume): its own word's, `name=value` or `name+=value`; else every substitution of the command, one it cannot
    tell apart among them; and None for each the hook cannot pair at all."""
    count = value.count(hookio.SUBST) - value.count(hookio.SUBST + syntax.PROCSUB_MARK)  # a `<( )`'s file lifted no body
    if count <= 0:
        return ()
    own = a.subst_words.get(name + "=" + value) or a.subst_words.get(name + "+=" + value)
    if own is not None:
        return own
    found = [b for bodies in a.subst_words.values() for b in (bodies or (None,))]
    return tuple(found) if found else (None,) * count


def settled_text(a, name):
    """The text the line settles for a `$name` arithmetic reads, or None: the value arg_writes.resolved puts in place, but
    as text, so a blank or a glob character in it stands (arithmetic reads the text again and splits and globs nothing)."""
    value = a.vars.get(name)
    if value is None or name in a.doubt or name in a.sticky or a.all_doubt or name in syntax.DYNAMIC_VARIABLES:
        return None
    if hookio.SUBST in value or syntax._ARRAY_VALUE in value:
        return None
    text = prepare.deglob(value)
    return None if "$" in text or "`" in text else text


def read_arithmetic(a, text, doubtful=False):
    """Record what an arithmetic expression, spelled as the shells read it, assigns (SPD-225): each name with the literal
    arithmetic_assignments.arithmetic_names reads for it where the evaluation surely runs and persists -- not `doubtful`
    (a command's prefix, a here-document's body, a `[[ ]]` operand, a subscript), not after an option that may change how
    the shells read a number (a.arith_opaque), and not a name with the integer or float attribute (a.typed), whose value
    the shells format their own way (`typeset -i 16 X; X=255` printed 16#FF in zsh) -- else a value the hook does not
    know.  A name the hook cannot read refuses a member unread (SPD-217)."""
    record_arithmetic(a, arithmetic_assignments.arithmetic_names(text, functools.partial(settled_text, a), doubtful), text)


def record_arithmetic(a, found, shown, doubtful=False):
    """read_arithmetic's record, for the names arithmetic_names or word_arithmetic found (None: one it cannot read)."""
    if found is None:
        unread.record_unread(a, "assigned", ("an arithmetic evaluation", unread.unread_shown(shown)))
        return
    if found:  # arithmetic over what the member supplies fills the names it assigns (SPD-258); a substitution in it, unpaired
        fill_from(a, [name for name, _ in found], shown, (None,) if hookio.SUBST in shown else (), arithmetic=True)
    for name, value in found:
        if value is None or doubtful or a.arith_opaque or name in a.typed:
            assign_unknown(a, name)
        else:
            assign_variable(a, name, value)


def read_word_arithmetic(words, a, bodies=()):
    """Record what the arithmetic a simple command's words expand assigns (SPD-225): each `$(( ... ))` and `$[ ... ]` in
    its words, where the shell expands them, in order (`echo $((X=5)) $((X=6))` left 6), the operands of a `[[ ... -eq
    ... ]]`, and each unquoted here-document's body among `bodies`.  Probed in zsh 5.9 -f, -f -o nobareglobqual and bash
    3.2.57, from `X=a`: an external command's words are expanded in the shell (`/usr/bin/true $((X=9))` left 9) but its
    redirections in its own process (`/bin/echo hi > f$((X=5))` and `cat < f$((X=8))` left a, a builtin's `echo hi >
    g$((X=6))` 6); a prefix's arithmetic before a command word stays in the command's process in zsh (`Y=$((X=7))
    /usr/bin/true` assigned X in bash alone), and bash expands the prefix after the command's own words (`Y=$((X=11))
    /usr/bin/true $((X=12))` left 11 in bash and 12 in zsh), while an assignment-only command's is the shell's own
    (`Y=$((X=8))` assigned in all three); a body is expanded where the shell and the command decide (bash's `cat <<EOF` fed
    `$((X=26))` left X, its `: <<EOF` did not); and a `[[ ]]` operand may not be evaluated at all (`[[ -n y || X=1 -eq 1
    ]]`).  A redirection's target, a prefix, a body and a `[[ ]]` operand are read as ones that may not persist, a
    prefix after the command's own words."""
    settle = functools.partial(settled_text, a)
    expanding = [k for k, w in enumerate(words) if "$" in w]
    if expanding:
        prefix, targets = _word_roles(words)
        for k in [k for k in expanding if k not in prefix] + [k for k in expanding if k in prefix]:
            found = arithmetic_assignments.word_arithmetic(words[k], settle)
            record_arithmetic(a, found, words[k], doubtful=k in prefix or k in targets)
    if "[[" in words:
        for k, w in enumerate(words):
            if w in _ARITHMETIC_TESTS and 0 < k < len(words) - 1:
                for operand in (words[k - 1], words[k + 1]):
                    read_arithmetic(a, prepare.deglob(operand), doubtful=True)
    for body in bodies:
        if "$" in body:
            record_arithmetic(a, arithmetic_assignments.word_arithmetic(body, settle, raw=True), body, doubtful=True)


def _word_roles(words):
    """({the indexes of a simple command's prefix assignments}, {the indexes of its redirections' targets}): the prefix
    being the assignment words before its command word, none for an assignment-only command, which assigns in the shell."""
    prefix, targets, command, target = set(), set(), False, False
    for k, w in enumerate(words):
        if target:
            targets.add(k)
            target = False
        elif w in syntax.OUT_REDIRECTS or w in syntax.IN_REDIRECTS:
            target = True
        elif not command and assignment_words.assignment_word(w) is not None:
            prefix.add(k)
        else:
            command = True
    return (prefix if command else set()), targets


def read_assigning_builtin(words, a, effect):
    """Record what an assigning builtin assigns (SPD-254; syntax.ASSIGNING_COMMANDS), read by its own grammar: `let`'s
    words as arithmetic (SPD-225), zsh's `integer`, `float` and `private` as declarations -- the first two giving the
    integer or float attribute, so what they and later assignments give the name is arithmetic -- and every other
    builtin's name operands as assigning_builtins.builtin_names reads them, each assigned a value the hook does not know.
    A name the line settles is the name it spells (`N=X; read $N` reads X); one the hook cannot read refuses a member
    unread (SPD-217), and so does code the builtin is handed to run.  `effect`: where the builtin runs -- in the shell, in
    either (`command read`: zsh's external, bash's builtin) or in a fork, where what it assigns may not reach the line.
    Inside a function body the shell holds, with the call's words, a name a builtin assigns may hold them: the member's
    own (SPD-205's member_vars); and where its words name the line's own variable, as spelled before the line's values
    are put in them, what it assigns is the line's (fill_from, SPD-253)."""
    cmd = prepare.deglob(words[0])
    spelled = " ".join(words[1:])
    words = [words[0]] + [arg_writes.resolved(w, a) for w in words[1:]]
    maybe = effect != "shell"
    a.unsure += maybe
    if cmd == "let":
        for w in words[2:] if len(words) > 1 and prepare.deglob(words[1]) == "--" else words[1:]:
            read_arithmetic(a, prepare.deglob(w))
    elif cmd in ("integer", "float", "private"):
        _read_zsh_declaration(words, a)
    else:
        names, subscripts, unreadable, evaluated = assigning_builtins.builtin_names(words, functools.partial(settled_text, a))
        if evaluated is not None:
            unread.record_unread(a, "evaluated", "`%s` %s" % (cmd, unread.unread_shown(evaluated)))
        if unreadable is not None:
            unread.record_unread(a, "assigned", ("`%s`" % cmd, unread.unread_shown(" ".join(words))))
        for subscript in subscripts:
            read_arithmetic(a, prepare.deglob(subscript), doubtful=True)
        fill_from(a, names, spelled)
        for name in dict.fromkeys(names):
            assign_unknown(a, name)
        if a.shell_reading:
            a.fill_members(names)
    a.unsure -= maybe


def _read_zsh_declaration(words, a):
    """zsh's `integer` and `float` (a typeset with the integer or float attribute: `integer X=3+4` left 7, probed) and
    `private` (zsh/param/private's local): each `name=value` recorded as a declaration's is, the rest assigned a value the
    hook does not know; an operand the hook cannot read refuses a member."""
    a.typed.update(assignment_words.typed_names(words))
    for w in words[1:]:
        text = prepare.deglob(w)
        found = assignment_words.declaration_word(w)
        if found is not None:
            analyse.record_assignment(a, found)
        elif syntax.IDENTIFIER_RE.match(text):
            assign_unknown(a, text)
        elif expansion_word(w):
            unread.record_unread(a, "assigned", ("`%s`" % prepare.deglob(words[0]), unread.unread_shown(" ".join(words))))


# `alias NAME=body` stores shell text the shell runs wherever it next reads NAME in command position.  A shell
# expands an alias when it parses the text, before the line runs, so an alias defined on the line
# reaches only code the line parses again: `eval`'s words, and a substitution inside them.  Probed in bash 3.2, zsh 5.9 -f,
# zsh -f -o nobareglobqual and sh with a fake git first on a scratch PATH: `alias gp='git push'; eval gp` pushed in zsh,
# zsh-nbgq and sh (bash expands no alias non-interactively without `shopt -s expand_aliases`, so it pushed nothing -- noted,
# never relied on), and so did `alias -g GP=...; eval GP`, two definitions on one `alias` line, `eval 'gp; gp'`, `eval 'X=1
# gp'`, `eval '{ gp; }'`, `eval 'if true; then gp; fi'`, `eval 'coproc gp'`, `eval 'time gp'`, `eval '! gp'`, `eval 'eval
# gp'`, `eval 'echo $(gp)'`, an alias reached from another alias, and one that shadows a function of the same name.  `alias
# g=git; g push` ran nothing anywhere (the alias does not exist when the line is parsed), nor did `eval 'command gp'`, `eval
# 'env gp'` or `eval 'sh -c gp'`, and `unalias` cleared.
def record_alias(a, name, body, doubtful=False):
    """Record `alias NAME=body`, or an `unalias` (whose body is None), where the shell reads it.  The name goes into
    `assigned` under a key no variable can have, so every rule that doubts a variable the line assigned -- a branch that may
    not run, a subshell, a pipeline element, a background list, a loop or function body, a reading only one shell makes --
    doubts the alias too, and a certain definition settles an earlier doubt as an assignment does.  The body itself stays out
    of `vars`, which holds the shell's variables alone (vouched_spud_call reads every name there)."""
    key = syntax.ALIAS_KEY + name
    a.aliases[name] = body
    a.assigned.append(key)
    if doubtful or a.unsure or a.loop_depth:
        a.doubt.add(key)
        if a.loop_depth:
            a.sticky.add(key)
    elif key not in a.sticky:
        a.doubt.discard(key)


def alias_arguments(words):
    """An `alias` or `unalias` line's words past its options (`alias -g X=y`, `alias -- a=b c=d`, `unalias -a`)."""
    i = 1
    while i < len(words) and words[i].startswith("-") and len(words[i]) > 1:
        i += 1
        if words[i - 1] == "--":
            break
    return words[i:]


def record_alias_line(words, a):
    """Read an `alias` line's definitions into the analysis's table.  A word with no `=` is a query and defines
    nothing.  A body the hook cannot read (it holds an expansion or a substitution, whose value is not on the line) is
    recorded with no body and doubted, so the name refuses a member where `eval` dispatches it; a name it cannot read leaves
    every name of the line's in doubt, since the hook cannot tell which one this defines."""
    for w in alias_arguments(words):
        m = syntax.ALIAS_WORD_RE.match(w)
        if m is not None:
            record_alias_definition(a, m.group(1), m.group(2))


def record_alias_definition(a, name, value):
    """One definition as the line spells it, `name` and `value` masked words: an `alias` line's `name=body`, or an element
    of zsh's `aliases` parameter, whose value is None where the hook cannot know it (`aliases[gp]+=...`)."""
    if expansion_word(name) or not syntax.IDENTIFIER_RE.match(prepare.deglob(name)):
        a.alias_unknown = True
        return
    readable = value is not None and not expansion_word(value)
    record_alias(a, prepare.deglob(name), prepare.deglob(value) if readable else None, doubtful=not readable)


def clear_alias_line(words, a):
    """`unalias NAME ...` and `unalias -a` clear what the line aliased; an argument the hook cannot read (an expansion, or a
    pattern for zsh's `-m`) clears nothing and doubts every name instead."""
    rest = alias_arguments(words)
    options = words[1 : len(words) - len(rest)]
    if "-a" in options:
        names, doubtful = list(a.aliases), False
    elif "-m" in options or any(active_read_word(w) for w in rest):
        names, doubtful = list(a.aliases), True
    else:
        names, doubtful = [prepare.deglob(w) for w in rest], False
    for name in names:
        record_alias(a, name, None, doubtful=doubtful)


def alias_substitution(name, a):
    """(the text an alias of this line's runs where `eval` dispatches its name, whether the hook cannot be sure of it), for a
    command word inside an `eval`.  (None, False) when the name is no alias of the line's and the line defined none
    the hook could not read; (None, True) when it may be one, or may have been cleared, and the hook cannot say what it runs."""
    if name not in a.aliases:
        return None, a.alias_unknown
    key = syntax.ALIAS_KEY + name
    return a.aliases[name], key in a.doubt or key in a.sticky or a.all_doubt


# The aliases and functions the shell already holds.  Claude Code starts a shell for every Bash call and sources
# its snapshot of the user's interactive shell in it (~/.claude/shell-snapshots/snapshot-zsh-*.sh), so a member's command
# word is expanded by that profile's aliases and run by its functions before any program does: on this Mac `gp` pushed,
# `gc -m x` committed, `g commit -m x` committed and `ggp` pushed, each reaching the hook as an unknown command with no
# finding at all.  Confirmed from a member's own Bash call: `type gp` printed "gp is an alias for git push",
# `type ggp` "ggp is a shell function from <that snapshot>", and `gst --short --branch` ran git and printed the worktree's
# status.  hooks/snapshots holds the table; these two read a command word against it.  An alias the line itself defines is
# a different thing and read by the alias table: it reaches only text the line parses again, which is `eval`.
def shell_aliased(words, a):
    """(the text the shell's own aliases put in place of `words`, the member's own words that follow that expansion, as
    the line's reading tokenized them, [(the name, what it runs)] for each one expanded, the first name whose body the hook
    cannot read), or (None, [], [], None) when the command word is none of them.  The own words are kept apart because a
    write a body makes into one of them, and a finding that spells one the hook cannot read, is the member's, which
    analyse_shell_text never prunes.

    A shell expands an alias where it parses the command word, textually and before any rule reads it, so the body and
    the words after it are analysed as the text the shell would have parsed -- `gc -m x` is `git commit --verbose -m x`
    and earns Law 7's own refusal -- each of those words quoted again as the line spelled it (prepare.requoted, SPD-201):
    `gc -m "don't"` is one message, not a quote that never closes.  When the body ends in a blank the next word is
    expanded too (zsh's chaining rule, `_='sudo '`), and a name is not expanded again while its own expansion is in
    flight, which is what stops `alias ls='ls -G'`.  A word the line quoted or escaped (`\\gp`, `'gp'`) reaches this
    with its quotes already taken and is expanded all the same: that is fail-closed -- the name it spells is no program --
    and telling the two apart would need a mark inside the command word that every reading by name would then have to
    strip (the expansion check makes the same choice for `'$X' push`)."""
    found = snapshots.shell_table(a.home)
    if not found.aliases:  # a machine with no snapshot, and every scratch home the suite builds: nothing to read
        return None, [], [], None
    out, expanded, i, at_command = [], [], 0, True
    while i < len(words) and at_command:
        name = prepare.deglob(words[i])
        if name in a.expanding or name not in found.aliases:
            break
        body = found.aliases[name]
        if body is None:
            return None, [], expanded, name
        spelled = body.strip()
        expanded.append((name, "an alias for `%s`" % (spelled if len(spelled) <= 120 else spelled[:117] + "...")))
        out.append(body)
        at_command = body.endswith((" ", "\t"))
        i += 1
    if not expanded:
        return None, [], [], None
    text = " ".join(out + [prepare.requoted(w) for w in words[i:]])
    return text, list(words[i:]), expanded, None


def shell_function(name, a):
    """The shell text a function the shell already defines runs for this command word, or None; an alias of the same name
    is expanded first, as the shell does it (shell_aliased runs before this).  held_text.read_shell_name reads it once per
    call's words and starting state, so a body that calls itself with them from where it started reads it no further."""
    found = snapshots.shell_table(a.home)
    return found.body(name) if name in found.functions else None


def variable_readings(a, name):
    """(readings, doubtful) for a bare `$name` whose value the line assigned (probed in zsh 5.9 -f and bash 3.2 with a fake
    git): the words bash gives (the value split on blanks, each field's glob characters active even if quoted in the assignment,
    `X='g?t'; $X push` pushed; an array's first element) and the words zsh gives (the value as one word, never globbed, when it
    holds a blank, `X='/a b/git'; $X push` ran that git; every element of an array).  (None, False) when the value is not known
    exactly: it holds an expansion or a substitution, was appended to, or IFS was assigned (`IFS=_; X=git_push; $X` pushed in
    bash).  Doubtful when the shell may not hold that value here (ShellAnalysis.doubt), inside a loop or function body, for a
    variable the shells set themselves, and for an array (the shells disagree)."""
    value = a.vars[name]
    if hookio.SUBST in value or "$" in value or "IFS" in a.vars or "IFS" in a.doubt:
        return None, False
    doubtful = name in a.doubt or name in a.sticky or name in syntax.DYNAMIC_VARIABLES or a.all_doubt or a.loop_depth > 0
    plain = value.translate(_ZSH_PLAIN)
    fields = [f.translate(_ACTIVATE_GLOBS) for f in syntax._IFS_BLANKS_RE.split(plain.lstrip(syntax._ARRAY_VALUE)) if f]
    if plain.startswith(syntax._ARRAY_VALUE):
        readings, doubtful = [fields, fields[:1]], True
    else:
        readings = [fields]
        if syntax._IFS_BLANKS_RE.search(plain):
            readings.append([globbing.literalize(plain)])
    unique = []
    for r in readings:
        if r not in unique:
            unique.append(r)
    return unique, doubtful


def resolve_expansion(words, i, bodies, a, depth, budget, effect, prefixed, fresh, wrapper_command=False):
    """Read words[i], a word the dispatch reads by name that holds an expansion.  A bare `$X` or `${X}` whose value the
    line assigned is read as the words the shells give it (variable_readings): one reading replaces it in place (_AGAIN), several
    are each analysed from the start (_STOP), and a doubtful value adds a "var-doubt" finding.  Anything else is not resolved: an
    operator form (`${X:-git}`), zsh's flags and modifiers (`${(L)X}`, `$~X`, `$X:t`), a subscript, a concatenation (`$X$Y`,
    `g$X`), arithmetic, a `$'...'` whose escapes the shells decode apart (prepare.ansi_c_quotes), a substitution, a variable the
    line did not assign, or an empty value outside the command word.
    The command word is then a "var" finding, which ends the analysis for a bare variable or a substitution (_STOP) and leaves a
    partial expansion to be dispatched as spelled (_FLAGGED); another word is a "var-word" finding left as spelled (_FLAGGED); a
    wrapper's command word is left for the loop to read once the wrapper is stripped (_FLAGGED, no finding).  Each finding refuses
    a member; Spud reads on."""
    w = words[i]
    name = variable_reference(w, command=i == 0)
    readings = doubtful = None
    if name and name in a.vars and budget[0] > 0:
        readings, doubtful = variable_readings(a, name)
        if readings is not None and i > 0 and not all(readings):
            readings = None  # an empty value drops the word: read as spelled (a member is refused, Spud's reading is kept)
    if readings is None and name is None and i > 0 and not wrapper_command:
        settled = glued_word(w, a)
        if settled is not None:
            words[i] = settled
            return _AGAIN
    spelled = "$(...)" if hookio.SUBST in w else prepare.deglob(w)
    if readings is None:
        if wrapper_command:
            return _FLAGGED
        a.findings.append(("var" if i == 0 else "var-word", spelled))
        if i > 0:
            return _FLAGGED
        a.kinds.append("var")
        # a bare variable or a substitution names nothing; a partial expansion (`${HOME}/bin/spud`, `$D/git`) is dispatched as
        # spelled, so Spud's checks still read it
        return _STOP if name is not None or hookio.SUBST in w else _FLAGGED
    if doubtful:
        a.findings.append(("var-doubt", spelled))
    budget[0] -= len(readings)
    if len(readings) == 1:
        words[i : i + 1] = readings[0]
        return _AGAIN
    globbing.analyse_readings(words, i, readings, bodies, a, depth, budget, effect, prefixed, fresh, expanded=True)
    return _STOP


def glued_word(word, a):
    """The word a by-name word holding an expansion glued to literal text becomes (`--git-dir=$D/.git`,
    `core.pager="$P"`), or None when the line does not settle it (SPD-141).  It is SPD-127's one reading of a write target,
    arg_writes.resolved: every `$NAME` and `${NAME}` put in place when the line settled its value as one plain word both
    shells pass (not doubted, no blank, no glob character, nothing left to expand), so the word is then read as though
    spelled, and its own reading -- the repository it names, the key and value -c sets -- decides.  One expansion the
    line cannot settle leaves the whole word as spelled, and so does a substitution, a `~` the value brings (the
    shell does not expand a tilde an expansion gives), a line that assigns IFS (bash then splits the value at other
    characters, as variable_readings holds) and a reading under a loop's binding, which the dispatch is not run once
    per value of."""
    if hookio.SUBST in word or a.binding is not None or "IFS" in a.vars or "IFS" in a.doubt:
        return None
    settled = arg_writes.resolved(word, a)
    if settled == word or expansion_word(settled) or settled.count("~") != word.count("~"):
        return None
    return settled


def first_read_index(words, start):
    return next((j for j in range(start, len(words)) if active_read_word(words[j])), None)


def git_read_point(words, start=1):
    """The read point, from `start`, of the first word git's option scan, its verb, the arguments git_refused reads, or an
    option of its verb (verb_option_read_index) reads that holds a glob or an expansion, or None.  A verb's option is read
    with the options that name a path git writes under that verb, so glob_readings reads a glob that may become one as
    it (`--outp?t=<path>`) and holds it unsettled."""
    i = 1
    while i < len(words):
        w = words[i]
        if i >= start and active_read_word(w):
            return option_point(i)
        if w in syntax.GIT_GLOBAL_VALUE_FLAGS:
            if i + 1 < len(words) and i + 1 >= start and active_read_word(words[i + 1]):
                return option_point(i + 1)
            i += 2
            continue
        if w.startswith("-"):
            i += 1
            continue
        if w in ("stash", "worktree", "remote", "reflog"):
            return option_point(i + 1 if i + 1 < len(words) and i + 1 >= start and active_read_word(words[i + 1]) else None)
        if w in ("branch", "tag", "config"):
            return option_point(first_read_index(words, max(i + 1, start)))
        k = verb_option_read_index(words, i, start)
        return None if k is None else (k, {"dash": True, "shift": True, "options": git_writes.git_read_options(w)})
    return None


def verb_option_read_index(words, verb_at, start):
    """The index, from `start`, of the first argument of the verb at `verb_at` that holds a glob or an expansion and may be
    one of the verb's options that name a program or a path git writes, or None.  Only the words before `--` are read,
    and of them: on a verb of syntax.GIT_VERB_PROGRAM_OPTIONS, a word spelled with a leading `-`, so `git ls-remote
    --upload-pac? cmd .` is read as --upload-pack while a pattern or a path a member greps for (`git grep '*.py'`) is
    left as the argument it is; on any verb, such a word whose literal head may still become an option that names a path
    git writes (git_writes.may_become_file_option: `git log --outp?t=<path>`, never `--grep=$P`); and on a verb of
    syntax.GIT_VERB_FILE_OPTIONS also a word that starts with its expansion (`git archive $OPT HEAD`), which may become
    any option at all.  The value a literal file option takes as the next word is a path, not an option, and is left
    to the path rule (`git archive -o $T HEAD`).
    On a verb of syntax.GIT_VERB_PROGRAM_OPTIONS (git_programs.reads_leading_expansion) a word that starts with its
    expansion is read too, where git still reads options (SPD-171): not as the value a spelled option takes as the next
    word (`git fetch --depth $N r`, `git grep -e $P`), nor past the first word ls-remote or grep reads as no option
    (`git ls-remote origin $REF`, `git grep foo $R`: git_programs.GIT_OPTIONS_STOP_VERBS)."""
    verb = words[verb_at]
    longs, shorts = git_writes.git_read_options(verb)
    program, table = verb in syntax.GIT_VERB_PROGRAM_OPTIONS, verb in syntax.GIT_VERB_FILE_OPTIONS
    leading = git_programs.reads_leading_expansion(verb)
    stops = leading and verb in git_programs.GIT_OPTIONS_STOP_VERBS
    value = past = False  # the word is a spelled option's value; git reads no more options (a stopping verb)
    k = verb_at + 1
    while k < len(words):
        w = words[k]
        if w == "--":
            return None
        if active_read_word(w):
            dash = w.startswith("-")
            opens = leading and not (dash or value or past) and expansion_word(w) and git_writes.literal_head(w) == ""
            if k >= start and ((program and (dash or opens)) or ((dash or table) and git_writes.may_become_file_option(w, longs, shorts))):
                return k
            value = False
        elif (git_writes.file_option_spelling(w, longs, shorts) or (None, False))[1]:
            k += 1  # the path a spaced file option names
            value = False
        elif leading:
            past = past or (stops and not value and not w.startswith("-"))
            value = not value and w.startswith("-") and git_programs.takes_value(verb, w)
        k += 1
    return None


def shell_read_index(words, start=1):
    """The index, from `start`, of the first option a shell's `-c` scan reads that holds a glob or an expansion, or None (the string
    itself is read as code)."""
    for i in range(1, len(words)):
        w = words[i]
        if i >= start and active_read_word(w):
            return i
        if not w.startswith("-") or (not w.startswith("--") and "c" in w[1:]):
            return None
    return None


# `trap` stores shell code the shell runs later: at exit, before every command under DEBUG, on ERR, and on every signal by
# name or number.  Probed in bash 3.2, zsh 5.9 -f, zsh -f -o nobareglobqual and sh with a
# fake git first on a scratch PATH: all four ran the action of `trap 'git push' EXIT`, and the two shells disagree about
# where that action is.  bash reads it after its options and `--` (`trap -- 'git push' EXIT` pushed; with `-p` or `-l` it
# prints and runs nothing); zsh has no options there and takes the word right after `trap` whatever it is (`trap -P EXIT`
# ran `-P` at exit, `trap -p EXIT` ran `-p`).  Both positions are read.  A word list that sets no action runs nothing:
# `trap`, `trap -p`, `trap -l`, `trap -lp`, `trap -`, `trap - EXIT`, `trap '' EXIT` and `trap "" INT TERM` list, print,
# reset or ignore, and each of those words reads as a command that does nothing.  The single-argument form runs nothing
# either (`trap 'git push'` and `trap git` print bash's and sh's usage and set nothing in zsh), but its word names code,
# so it is read all the same: fail closed, at no real cost, since the line is a usage error where it is not a no-op.
def trap_action_indices(words):
    """The indices of a `trap` line's words a shell may run as code later: zsh's action, the word right after `trap`, and
    bash's, the first word after its options and `--`."""
    if len(words) < 2:
        return []
    found, i = [1], 1
    while i < len(words) and words[i].startswith("-") and len(words[i]) > 1:
        i += 1
        if words[i - 1] == "--":
            break
    return found if i in (1, len(words)) else found + [i]


def trap_read_index(words, start=1):
    """The index, from `start`, of the first word a `trap` line may run as code that holds a glob or an expansion, or None."""
    return next((k for k in trap_action_indices(words) if k >= start and active_read_word(words[k])), None)


def analyse_trap(words, a, depth):
    """Read each action a `trap` line may set as the shell text it is, with its own quotes, as eval's rejoined words and a
    shell's `-c` string are: a finding inside it is the finding it would be on the line.  The action runs later,
    at a directory the hook cannot know (probed: `trap 'echo trapped >> rel.txt' EXIT; cd /tmp` wrote /tmp/rel.txt, and an
    EXIT action's `pwd` is the last directory of the line), so it is read with the directories unknown, as a sourced file
    is, and a relative redirection or tee inside it refuses a member.  The line's own directories and variables are
    restored afterwards: defining a trap changes nothing on the line, and the action's assignments run later, where
    the expansion check's `a.all_doubt` after `trap` already doubts every variable.

    Unless the action runs inside the line (trap_runs_in_line) and changes the directory of the shell it runs in -- a cd,
    a sourced file, a function's move, text the reading drops, whatever ShellAnalysis.dir_moves counts, a cd in a
    substitution of the action's among them, read on the safe side -- which leaves the line's directory unknown from the
    trap on, since the action may run before any later command (SPD-252).  An action that moves nothing leaves it."""
    cwds, variables, moved = a.cwds, dict(a.vars), False
    for k in trap_action_indices(words):
        text = prepare.deglob(words[k])
        a.cwds = None
        calls, moves = len(a.git_calls), a.dir_moves
        analyse.analyse_isolated(a, text, depth + 1)
        if a.dir_moves != moves:
            a.moving_traps.add(text)  # read once per starting state (analyse_isolated): a later reading of it knows too
        moved = moved or text in a.moving_traps
        # The action's git calls are not scope-checked.  The hook reads the action with the directories unknown
        # because it runs later, not because the line lost them, and `trap 'git status' EXIT` names no repository, so the
        # unresolvable-directory refusal would fall on every trap that mentions git.  Its own findings still stand.
        del a.git_calls[calls:]
        a.cwds, a.vars = cwds, dict(variables)
    if moved and trap_runs_in_line(words, a):
        a.cwds = None
        a.dir_moves += 1


# The traps whose action the shell reading the line runs before the line is over (SPD-252), probed in zsh 5.9 -f -o
# nobareglobqual and -f and bash 3.2.57 (tests/test_hooks_writes.py MovedBetweenCommandsTest): DEBUG before each
# command, ERR and zsh's ZERR after a failing one, bash's RETURN when the function that set it returns -- and EXIT or 0
# set inside a function, which zsh runs as that function returns.  EXIT at the line's own level runs after the line,
# and a signal the line does not raise never.  Read as either shell names them: bash took `debug`, zsh `SIGDEBUG`.
LINE_TRAPS = frozenset({"DEBUG", "ERR", "ZERR", "RETURN"})
FUNCTION_TRAPS = frozenset({"EXIT", "0"})


def trap_runs_in_line(words, a):
    """Whether a `trap` line may set an action its own shell runs before the line is over: a signal word, read
    case-folded with any SIG prefix taken off, in LINE_TRAPS, or in FUNCTION_TRAPS inside a function body (the line's own,
    or one the shell holds), or a signal word the hook does not read (an expansion, a glob).  The action words
    (trap_action_indices) and options are not signals."""
    actions = trap_action_indices(words)
    in_function = a.func_depth > 0 or a.body_locals is not None
    for k, word in enumerate(words[1:], 1):
        if k in actions or word.startswith("-"):
            continue
        if expansion_word(word) or globbing.active_glob_word(word):
            return True
        name = prepare.deglob(word).upper()
        name = name[3:] if name.startswith("SIG") else name
        if name in LINE_TRAPS or (in_function and name in FUNCTION_TRAPS):
            return True
    return False


def python_read_index(words, a, start=1):
    """(index, is the script), from `start`, of the first word python_interpreter_args reads, or a spud launcher's arguments, that
    holds a glob or an expansion; or None."""
    i = 1
    while i < len(words):
        w = words[i]
        if i >= start and active_read_word(w):
            return i, not w.startswith("-")
        if w in ("-c", "-m", "-"):
            j = i + 1
            return (j, False) if w == "-m" and j < len(words) and j >= start and active_read_word(words[j]) else None
        if w.startswith("-"):
            if w in ("-X", "-W", "-Q"):
                if i + 1 < len(words) and i + 1 >= start and active_read_word(words[i + 1]):
                    return i + 1, False
                i += 1
            i += 1
            continue
        if spud_calls.any_spud_launcher(w, a.cwds):
            k = first_read_index(words, max(i + 1, start))
            return None if k is None else (k, False)
        return None
    return None


def option_point(k):
    """A read point for an option, a verb or an argument: a glob there may start with `-` or be skipped as an option's value."""
    return None if k is None else (k, {"dash": True, "shift": True})


def script_point(found):
    """A read point python_read_index found: a script is matched as a path, an option like any other."""
    if found is None:
        return None
    k, is_script = found
    return k, {"script": is_script, "dash": True, "shift": not is_script}
