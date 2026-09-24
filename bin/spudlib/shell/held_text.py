"""shell/held_text: The text the shell holds, an alias's body and a function's, read and pruned of what is not the member's.

A module of its own since SPD-264, taken out of shell/analyse: analyse.dispatch_words asks read_shell_name for a command
word the Bash tool's shell already defines, and analyse_shell_text reads the body through analyse.analyse_command, then
drops what would fall on a member for text it did not write (names_member_var through unresolvable_write).  It joins the
shell reading's import cycle, every read of analyse inside a function body.  Past 250 lines as one reading:
analyse_shell_text's prune and the helpers it keeps by are one rule, and read_shell_name and read_body are its only way
in."""

from . import analyse, directories, expansions, globbing, loop_bindings, positional, prepare, stdin_text, syntax, walk
from ..hooks import hookio


def read_body(a, text, depth, stdin, fed):
    """Read a function's body the shell holds, isolated, in a scope of its own (SPD-246): a name it surely declares local
    (assignment_words.local_names) is not the line's while it runs, and when it returns the shell drops the local, so the
    name is what it was before -- its value in `vars`, no loop binding or basename of the body's, and its doubt as it
    stood before the call, unless the body assigned the name before declaring it (`V=x; local V`), which changed the
    line's.  An assignment to any other name, the body's own `NAME=value` or a loop's variable, reaches the line's shell
    and stays, doubted after the call as isolated doubts it.

    Its directory changes stay too (SPD-252): a cd, pushd or popd in the body, or in a function it calls, is where the
    line's shell is when the call returns, as the same cd on the line leaves it -- either directory after one that may
    not run or may fail, and one the hook cannot follow where the body cds into a value it cannot settle.  What the body
    runs in a process of its own (a subshell, a substitution, a pipeline element) its walk already puts back."""
    values, doubted, own, left = dict(a.vars), frozenset(a.doubt), {}, []

    def run():
        a.body_locals = (a.body_locals or []) + [{}]
        analyse.analyse_command(text, a, depth, stdin, fed)
        own.update(a.body_locals[-1])
        left.append(a.cwds)

    analyse.isolated(a, run)
    a.cwds = left[0]
    for name, before in own.items():
        if before is syntax.UNSET:
            a.vars.pop(name, None)
        else:
            a.vars[name] = before
        a.derived.pop(name, None)
        if values.get(name, syntax.UNSET) == before and name not in doubted:
            a.doubt.discard(name)
    loop_bindings.unbind(a, own)


def read_shell_name(words, a, depth, stdin=None, fed=False, effect="shell"):
    """A command word the shell the Bash tool starts already defines, read for what it actually runs: an alias,
    whose body and the words after it are analysed as the text the shell put there -- and True, since that text is the
    command now -- or a function, whose body is read as an `eval` string is while the call's own words go on to be
    dispatched for what they name.  An alias shadows a function of the same name, as the shell resolves them.

    Either runs in the line's shell, so the directory it leaves is the line's after it (SPD-252), settled by `effect`,
    where the command runs (directories.prefix_effect): `coproc takedir x` moves a forked shell and leaves the line where
    it was, as a coproc's cd does.  Probed in zsh 5.9 -f -o nobareglobqual and -f and bash 3.2.57: after `takedir x1`,
    `mkcd p q`, `take x2/y`, a pushd, a popd or a cd through a second function in a body, `pwd` printed where the body
    went; after a subshell body `( cd "$1" )`, which the walk of the body reads as the subshell it is, where the call
    started (tests/test_hooks_snapshots.py FunctionDirectoryTest).

    A function the line itself defines under that name reads the call's standard input, `stdin` (`fed`: whether anything
    stands there): its body is read with it where the line defines it, the line being walked again (walk.read_call and
    walk_line, SPD-212).  An alias's body and a snapshot function's -- which no walk of the line defines -- read that same
    input here, so `xs < x.sh` (alias xs=sh) and `shfn < x.sh` (a snapshot `shfn(){ sh }`) read the file's program as
    `sh < x.sh` does, past SPD-145 and SPD-150 (SPD-215); a body is read once per call's words and standard input both.

    The call's words reach a function's body as its positional parameters, so the body is read with them set where it
    reads those (shell/positional, SPD-203): `gitfn push`, whose body is `command git "$@"`, is `git push`.  It is read
    once per call's words, standard input and the state the call starts in (ShellAnalysis.reading_state: the
    directories, the line's variables, SPD-252), not once per name, so the second call of `gitfn status; gitfn push` is
    read too, and so are `noteit; cd ..; noteit` (its relative write lands again, elsewhere) and `V=status; vgit;
    V=push; vgit` (`git $V` pushes); a body that calls itself from where it started reads it no further.  A call that
    reads exactly as one read before -- after a subshell put the directory back, or in the line's second walk -- is
    given the directories that reading left (ShellAnalysis.body_dirs), and one inside its own reading leaves them
    unknown if the body moves them, since the shell would move them again from where the inner call returns.  Past
    positional.READINGS_PER_NAME readings of one name on a line, a call with other words reads the body as it stands,
    once per starting state, whose findings the member's words keep: a profile whose functions call one another with
    ever other words cannot multiply one line's readings without bound."""
    cmd = prepare.deglob(words[0])
    text, own_words, expanded, unreadable = expansions.shell_aliased(words, a)
    a.shell_expanded.extend(expanded)
    if unreadable is not None:  # the chain reached a body whose quoting the hook cannot take off: it runs the line, unread
        a.kinds.append("other")
        a.findings.append(("shell-alias", unreadable))
        return True
    before = a.cwds
    if expanded:
        a.expanding.extend(name for name, _ in expanded)
        try:
            analyse_shell_text(a, text, depth + 1, own_words, stdin=stdin, fed=fed)
        finally:
            del a.expanding[len(a.expanding) - len(expanded):]
        a.cwds = directories.settle(effect, before, a.cwds)
        return True
    walk.read_call(a, cmd, stdin, fed)
    body = expansions.shell_function(cmd, a)
    if body is not None:
        text, sound, filled = positional.substitution(body, words[1:])
        # a body is read once per call's words, standard input (SPD-215) and starting state (SPD-252)
        key = (stdin_text.reading_key(stdin, fed), a.reading_state())
        read, reads = (text, tuple(words[1:]), key), a.bodies_read.setdefault(cmd, set())
        if read not in reads and len(reads) >= positional.READINGS_PER_NAME:
            text, sound, filled = body, False, ()
            read = (body, bool(words[1:]) and (a.shell_reading == 0 or bool(a.shell_words)), key)
        if read not in reads:
            a.shell_expanded.append((cmd, "a shell function"))
            reads.add(read)
            # the substitutions the call's words were set in (SPD-258): the member's where the words are, the outermost
            # call's and a nested call's that passes one of them on; a nested call's own literals are its body's
            if a.shell_reading and not any(w in a.shell_words for w in words[1:]):
                filled = ()
            a.body_dirs[cmd, read] = _READING
            analyse_shell_text(a, text, depth + 1, words[1:], own_process=True, substituted=sound, stdin=stdin, fed=fed,
                               filled=filled)
            after = None if a.body_dirs[cmd, read] is _REENTERED and a.cwds != before else a.cwds
            a.body_dirs[cmd, read] = after
        else:
            after = a.body_dirs.get((cmd, read), before)
            if after is _READING or after is _REENTERED:  # a call inside its own reading, from where that reading started
                a.body_dirs[cmd, read], after = _REENTERED, before
            a.dir_moves += after != before  # the move given again, as the reading counted it
        a.cwds = directories.settle(effect, before, after)
    return False


# ShellAnalysis.body_dirs' marks for a function body whose reading is under way, and for one a call inside that reading
# reached again from the same state (read_shell_name)
_READING, _REENTERED = object(), object()


def analyse_shell_text(a, text, depth, own_words, own_process=False, substituted=None, stdin=None, fed=False, filled=()):
    """Read text the shell itself holds: an alias's body, which is the line's own text once the shell has parsed it, or a
    function's, which runs in the line's shell in a scope of its own (own_process, read_body).  `own_words` are the member's own
    words of the line that reach this text, as the line's reading tokenized them -- the words after the alias the shell
    expanded, or the call's arguments, which a function receives as its positional parameters.  `substituted`, for a
    function's body: whether shell/positional set those words where the body reads them (True), or left it as it stands
    because it cannot follow them there (False).

    The findings are the findings the text would earn on the line, minus two kinds that would fall on a member for text it
    did not write and cannot change: the ones that say only that the hook cannot read a word (syntax.SHELL_TEXT_TOLERATED)
    and a write whose target the hook cannot resolve.  Claude Code shadows find, grep, rg and pkill with functions that
    dispatch through `"$_cc_bin"` and write through `$data`-shaped names of their own; without both prunes a member would
    be refused every `grep` it runs.

    What the member's own words earn is never pruned, since the member wrote those and can spell them out (SPD-203):

    - a target the member supplied (member_supplied): an alias's expansion is its body followed by the member's own words,
      and a function's `$@` is them, so pruning on the target's spelling alone let an alias launder exactly what the
      unresolvable-target rule exists to refuse -- `md $HOME/planted` recorded no write while `mkdir -p $HOME/planted`
      was refused;
    - a finding that spells one of the member's words the hook cannot read (spells_member_word): `_ $(echo git) push`
      (`_='sudo '`) is `sudo $(echo git) push`, whose command word refuses a member as it does spelled out;
    - for a call with words, every finding of a function's body the substitution could not set them into
      (substituted=False), and one that spells a reference to them the substitution left where another reading takes
      it (`sh -c 'git "$@"' _ "$@"`): the member's words reach those where the hook does not follow them;
    - every finding, of every kind, and every write that names a variable the member filled (names_member_var): one the
      line assigned before the text (SPD-205), one a value naming such a variable fills in the text (SPD-253: the
      harness's `_cc_bin="${CLAUDE_CODE_EXECPATH:-}"`), and, where the call has words or standard input the member gave
      it, one they fill -- a loop over them, a substitution shell/positional set them in (`filled`, SPD-258), a builtin
      that reads them, another such variable (expansions.fill_from).  The command word `"$_cc_bin"` of the harness's
      shadows is kept too when the line fills `_cc_bin` (SPD-248: `for f in "$@"; do $f push; done` ran `git push`
      for `loopcmd git`), and stays the body's own where only the environment and the body's literals do.

    A concrete file the text writes is held to the path rule for the caller, as an alias's redirection is anywhere else.
    Only the outermost of these readings prunes, so a nested one never drops what the reading closest to the member's
    words keeps; a function's body nested in it marks its own findings to keep while the outermost reading has words.

    A target the text's own line settles is resolved before this sees it, so it is a concrete file and the prune
    does not reach it: a body that writes `$data` after assigning it goes to the path rule like any spelled path, while the
    harness's `"$_cc_bin"` and the environment it reads, which no line settles, stay as unresolvable as they were."""
    outermost = a.shell_reading == 0
    # what the line's shell holds from the line before this text (SPD-205): what its own text assigned and what a function
    # body it called assigned to a name the body did not declare local -- never a body's local, which is gone (SPD-246)
    line_vars = frozenset(a.line_assigned) if outermost else frozenset()
    if outermost:
        a.shell_words = list(own_words)
        a.member_vars = set(a.line_members)  # ... and an earlier body's local that its call's words filled is gone too
        a.shell_line_vars, a.line_filled, a.filled_texts = line_vars, set(), set()  # what fills a body's variable (SPD-258)
    a.filled_texts.update(filled)
    marks = (len(a.findings), len(a.redirects), len(a.git_writes), len(a.arg_writes))
    a.shell_reading += 1
    try:
        if own_process:
            # not analyse_isolated's cache: a body read before without the member's words had its findings pruned, and
            # read_shell_name reads each call's once.  On the call's standard input, so a body running a shell or an
            # interpreter reads the file the call feeds it (SPD-215); in a scope of its own, for its locals (SPD-246)
            read_body(a, text, depth, stdin, fed)
        else:
            analyse.analyse_command(text, a, depth, stdin, fed)
    finally:
        a.shell_reading -= 1
    if substituted is not None and own_words and a.shell_words:
        a.shell_kept.update(i for i in range(marks[0], len(a.findings)) if a.findings[i][0] in syntax.SHELL_TEXT_TOLERATED
                            and (not substituted or positional.REFERENCE_RE.search(a.findings[i][1])))
    if not outermost:
        return
    unread_words = [member_spelling(w) for w in own_words if unreadable_word(w)]
    # the variables the line's shell held from the line before the text (always the member's: line_vars) and those a value
    # naming one of them filled in the text (line_filled, SPD-253), and, where the call passed words or standard input,
    # those they filled inside the body (SPD-205, SPD-258) or an earlier body's global they filled (line_members, SPD-246);
    # with neither there is nothing of the member's to fill one
    member = line_vars | a.line_filled | (a.member_vars if own_words or fed else frozenset())
    kept, a.shell_kept, a.shell_words = a.shell_kept, set(), []
    a.shell_line_vars = frozenset()
    a.findings[marks[0]:] = [f for i, f in enumerate(a.findings[marks[0]:], marks[0])
                             if f[0] not in syntax.SHELL_TEXT_TOLERATED or i in kept or names_member_var(f[1], member)
                             or any(spells_member_word(f[1], spelled) for spelled in unread_words)]
    supplied = frozenset(prepare.deglob(w) for w in own_words)
    a.redirects[marks[1]:] = [e for e in a.redirects[marks[1]:] if keeps_write(e[0], supplied, member)]
    a.git_writes[marks[2]:] = [e for e in a.git_writes[marks[2]:] if keeps_write(e[1], supplied, member)]
    a.arg_writes[marks[3]:] = [e for e in a.arg_writes[marks[3]:] if keeps_write(e[1], supplied, member)]


def names_member_var(detail, member):
    """Whether a finding or a write target names a variable the member filled (SPD-205): its detail -- a string, or a
    tuple's strings -- holds a `$NAME` (syntax.READ_NAME_RE, read with its glob sentinels taken off) whose NAME is in
    `member`, analyse_shell_text's set.  Such a variable is the member's own, so the finding is kept, not dropped as the
    body's, and refuses the member on doubt as the same reference does on a plain line -- whatever its kind (SPD-258),
    the command word a variable gives among them, which the harness's own shadows spell `"$_cc_bin"` and which stays
    theirs wherever nothing of the member's fills `_cc_bin`."""
    if not member:
        return False
    texts = (detail,) if isinstance(detail, str) else detail if isinstance(detail, tuple) else ()
    return any(name in member for text in texts if isinstance(text, str)
               for name in syntax.READ_NAME_RE.findall(prepare.deglob(text)))


def unreadable_word(word):
    """Whether the hook cannot read this word of the member's as a fixed string: it holds an expansion, a substitution or
    a glob the shell expands."""
    return expansions.expansion_word(word) or globbing.active_glob_word(word)


def member_spelling(word):
    """How a finding spells a word of the member's that the hook cannot read: a substitution as `$(...)`, as
    resolve_expansion shows every word that holds one, and anything else as the line spells it."""
    return "$(...)" if hookio.SUBST in word else prepare.deglob(word)


def spells_member_word(detail, spelled):
    """Whether a finding's detail is the member's word the hook cannot read: the word itself, or -- for an expansion,
    which a body may glue to text of its own (`--author=$1`) -- a longer word that holds it, not followed by a character
    that would lengthen its name.  A glob of the member's is its whole word: `*` is not the body's own `-*-config*`."""
    if detail == spelled:
        return True
    if "$" not in spelled:
        return False
    at = detail.find(spelled)
    while at >= 0:
        after = detail[at + len(spelled) : at + len(spelled) + 1]
        if not (after and (after.isalnum() or after == "_") and (spelled[-1].isalnum() or spelled[-1] == "_")):
            return True
        at = detail.find(spelled, at + 1)
    return False


def keeps_write(target, supplied, member=frozenset()):
    """Whether a write this text would make is read as it stands: every target the hook can resolve, every one the
    member's own words supplied, and every one naming a variable the member filled (`member`, names_member_var, SPD-258),
    whatever the hook can make of it."""
    return not unresolvable_write(target) or member_supplied(target, supplied) or names_member_var(target, member)


def member_supplied(target, supplied):
    """True when this target is one the member's own line gave the text: one of the words that followed the alias the
    shell expanded, or a positional parameter, which is how a function receives them.  An own word that is itself
    unresolvable is also read where it stands inside a longer target, since that is the spelling the refusal is for."""
    spelled = prepare.deglob(target)
    if syntax.POSITIONAL_RE.search(spelled) is not None or spelled in supplied:
        return True
    return any(word in spelled for word in supplied if unresolvable_write(word))


def unresolvable_write(target):
    """True when the hook cannot tell which file this target names: it holds a variable or a substitution, the test
    bash_reason makes of a redirection target before it refuses a member for one."""
    return "$" in target or "`" in target or hookio.SUBST in target or syntax.unknown_operand(target)
