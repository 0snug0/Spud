"""shell/expansions: Parameter expansions and a command's read points.  Moved from bin/spud_ledger.py (SPD-065)."""

from . import analyse, globbing, prepare, spud_calls, syntax
from ..hooks import hookio


# What reading one word leaves its caller to do (SPD-043): read on from where the word was (its readings replaced it in place),
# stop (its readings were each analysed whole, or the command word cannot be read), or read on past it (it stays as spelled).
_AGAIN, _STOP, _FLAGGED = "again", "stop", "flagged"
_ACTIVATE_GLOBS = str.maketrans({syntax._GLOB_SENTINELS[c]: c for c in "*?[]"})  # bash globs an unquoted expansion, however it was assigned
_ZSH_PLAIN = str.maketrans(syntax._ZSH_UNSENTINEL)
_BRACES_PLAIN = str.maketrans({syntax._GLOB_SENTINELS["{"]: "{", syntax._GLOB_SENTINELS["}"]: "}"})


def expansion_word(word, command=False):
    """True when the shell expands a parameter, arithmetic or a substitution in this word (SPD-043): a `$` neutralize_quoted_globs
    did not mark literal, or a lifted `$(...)` or backtick body.  In the command word a bare `$X` is read as one whatever its
    quoting, as it was before SPD-043 (`'$X' push` stays refused)."""
    if hookio.SUBST in word or syntax._EXPANDING_DOLLAR_RE.search(word):
        return True
    return command and variable_reference(word, command) is not None


def variable_reference(word, command=False):
    """The name of a bare `$X` or `${X}` the word is, quoted or not (`"${X}"`'s braces reach it as quoted glob sentinels); in the
    command word a literal-marked dollar counts too, as before SPD-043; else None."""
    text = word.translate(_BRACES_PLAIN)
    if command:
        text = text.replace(syntax._LITERAL_DOLLAR, "")
    ref = syntax.VARREF_RE.match(text)
    return ref and (ref.group(1) or ref.group(2))


def active_read_word(word):
    """A word the dispatch reads by name that must be resolved before it is read: an expansion (SPD-043) or a glob (SPD-041)."""
    return expansion_word(word) or globbing.active_glob_word(word)


def assign_variable(a, name, value, append=False):
    """Record `name=value` (or `name+=value`) where the shell runs it (SPD-043).  An appended value is not known (CDPATH's reads as
    `$`, which cd_target does not follow; any other as a substitution).  The value is certain unless the assignment may not run
    or persist here (a.unsure) or runs in a loop or function body, which may assign again later (sticky); a certain assignment
    settles an earlier doubt."""
    a.vars[name] = ("$" if name in ("CDPATH", "cdpath") else hookio.SUBST) if append else value
    a.assigned.append(name)
    if a.unsure or a.loop_depth:
        a.doubt.add(name)
        if a.loop_depth:
            a.sticky.add(name)
    elif name not in a.sticky:
        a.doubt.discard(name)


def variable_readings(a, name):
    """(readings, doubtful) for a bare `$name` whose value the line assigned (SPD-043, probed in zsh 5.9 -f and bash 3.2 with a fake
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
    """Read words[i], a word the dispatch reads by name that holds an expansion (SPD-043).  A bare `$X` or `${X}` whose value the
    line assigned is read as the words the shells give it (variable_readings): one reading replaces it in place (_AGAIN), several
    are each analysed from the start (_STOP), and a doubtful value adds a "var-doubt" finding.  Anything else is not resolved: an
    operator form (`${X:-git}`), zsh's flags and modifiers (`${(L)X}`, `$~X`, `$X:t`), a subscript, a concatenation (`$X$Y`,
    `g$X`), arithmetic, `$'...'`, a substitution, a variable the line did not assign, or an empty value outside the command word.
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
    spelled = "$(...)" if hookio.SUBST in w else prepare.deglob(w)
    if readings is None:
        if wrapper_command:
            return _FLAGGED
        a.findings.append(("var" if i == 0 else "var-word", spelled))
        if i > 0:
            return _FLAGGED
        a.kinds.append("var")
        # a bare variable or a substitution names nothing; a partial expansion (`${HOME}/bin/spud`, `$D/git`) is dispatched as
        # spelled, as it was before SPD-043, so Spud's checks still read it
        return _STOP if name is not None or hookio.SUBST in w else _FLAGGED
    if doubtful:
        a.findings.append(("var-doubt", spelled))
    budget[0] -= len(readings)
    if len(readings) == 1:
        words[i : i + 1] = readings[0]
        return _AGAIN
    globbing.analyse_readings(words, i, readings, bodies, a, depth, budget, effect, prefixed, fresh, expanded=True)
    return _STOP


def first_read_index(words, start):
    return next((j for j in range(start, len(words)) if active_read_word(words[j])), None)


def git_read_index(words, start=1):
    """The index, from `start`, of the first word git's option scan, its verb or the arguments git_refused reads that holds a glob or
    an expansion, or None."""
    i = 1
    while i < len(words):
        w = words[i]
        if i >= start and active_read_word(w):
            return i
        if w in syntax.GIT_GLOBAL_VALUE_FLAGS:
            if i + 1 < len(words) and i + 1 >= start and active_read_word(words[i + 1]):
                return i + 1
            i += 2
            continue
        if w.startswith("-"):
            i += 1
            continue
        if w in ("stash", "worktree", "remote", "reflog"):
            return i + 1 if i + 1 < len(words) and i + 1 >= start and active_read_word(words[i + 1]) else None
        if w in ("branch", "tag", "config"):
            return first_read_index(words, max(i + 1, start))
        return None
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
# name or number (SPD-054, Agria's SPD-043 proposal).  Probed in bash 3.2, zsh 5.9 -f, zsh -f -o nobareglobqual and sh with a
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
    shell's `-c` string are (SPD-054): a finding inside it is the finding it would be on the line.  The action runs later,
    at a directory the hook cannot know (probed: `trap 'echo trapped >> rel.txt' EXIT; cd /tmp` wrote /tmp/rel.txt, and an
    EXIT action's `pwd` is the last directory of the line), so it is read with the directories unknown, as a sourced file
    is, and a relative redirection or tee inside it refuses a member.  The line's own directories and variables are
    restored afterwards: defining a trap changes nothing on the line, and the action's assignments run later, where
    SPD-043's `a.all_doubt` after `trap` already doubts every variable."""
    cwds, variables = a.cwds, dict(a.vars)
    for k in trap_action_indices(words):
        a.cwds = None
        calls = len(a.git_calls)
        analyse.analyse_isolated(a, prepare.deglob(words[k]), depth + 1)
        # SPD-063: the action's git calls are not scope-checked.  The hook reads the action with the directories unknown
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
