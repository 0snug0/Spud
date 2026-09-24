"""shell/expansions: Parameter expansions and a command's read points."""

from . import analyse, arg_writes, git_verbs, globbing, loop_bindings, prepare, spud_calls, syntax
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


def assign_variable(a, name, value, append=False):
    """Record `name=value` (or `name+=value`) where the shell runs it.  An appended value is not known (CDPATH's reads as
    `$`, which cd_target does not follow; any other as a substitution).  The value is certain unless the assignment may not run
    or persist here (a.unsure) or runs in a loop or function body, which may assign again later (sticky); a certain assignment
    settles an earlier doubt."""
    a.vars[name] = ("$" if name in ("CDPATH", "cdpath") else hookio.SUBST) if append else value
    loop_bindings.loop_assigned(a, name, value, append)  # a loop's binding of the name ends; `name=$(basename ...)` kept (SPD-146)
    if not append and syntax.POSITIONAL_RE.search(prepare.deglob(value)) is not None:
        a.member_vars.add(name)  # a value holding a positional fills the name with the call's words (SPD-205)
    a.assigned.append(name)
    if a.unsure or a.loop_depth:
        a.doubt.add(name)
        if a.loop_depth:
            a.sticky.add(name)
    elif name not in a.sticky:
        a.doubt.discard(name)


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
    is expanded first, as the shell does it (shell_aliased runs before this).  analyse.read_shell_name reads it once per
    call's words, so a body that calls itself with them reads it no further."""
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
        return None if k is None else (k, {"dash": True, "shift": True, "options": git_verbs.git_file_options(w)})
    return None


def verb_option_read_index(words, verb_at, start):
    """The index, from `start`, of the first argument of the verb at `verb_at` that holds a glob or an expansion and may be
    one of the verb's options that name a program or a path git writes, or None.  Only the words before `--` are read,
    and of them: on a verb of syntax.GIT_VERB_PROGRAM_OPTIONS, a word spelled with a leading `-`, so `git ls-remote
    --upload-pac? cmd .` is read as --upload-pack while a pattern or a path a member greps for (`git grep '*.py'`) is
    left as the argument it is; on any verb, such a word whose literal head may still become an option that names a path
    git writes (git_verbs.may_become_file_option: `git log --outp?t=<path>`, never `--grep=$P`); and on a verb of
    syntax.GIT_VERB_FILE_OPTIONS also a word that starts with its expansion (`git archive $OPT HEAD`), which may become
    any option at all.  The value a literal file option takes as the next word is a path, not an option, and is left
    to the path rule (`git archive -o $T HEAD`)."""
    verb = words[verb_at]
    longs, shorts = git_verbs.git_file_options(verb)
    program, table = verb in syntax.GIT_VERB_PROGRAM_OPTIONS, verb in syntax.GIT_VERB_FILE_OPTIONS
    k = verb_at + 1
    while k < len(words):
        w = words[k]
        if w == "--":
            return None
        if active_read_word(w):
            dash = w.startswith("-")
            if k >= start and ((program and dash) or ((dash or table) and git_verbs.may_become_file_option(w, longs, shorts))):
                return k
        elif (git_verbs.file_option_spelling(w, longs, shorts) or (None, False))[1]:
            k += 1  # the path a spaced file option names
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
    the expansion check's `a.all_doubt` after `trap` already doubts every variable."""
    cwds, variables = a.cwds, dict(a.vars)
    for k in trap_action_indices(words):
        a.cwds = None
        calls = len(a.git_calls)
        analyse.analyse_isolated(a, prepare.deglob(words[k]), depth + 1)
        # The action's git calls are not scope-checked.  The hook reads the action with the directories unknown
        # because it runs later, not because the line lost them, and `trap 'git status' EXIT` names no repository, so the
        # unresolvable-directory refusal would fall on every trap that mentions git.  Its own findings still stand.
        del a.git_calls[calls:]
        a.cwds, a.vars = cwds, dict(variables)


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
