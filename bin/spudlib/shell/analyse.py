"""shell/analyse: analyse_command and analyse_words.  Moved from bin/spud_ledger.py (SPD-065)."""

import os

from . import arg_writes, assignment_words, directories, downloads, expansions, find_xargs, git_programs, git_verbs, globbing, inline_programs, prepare, script_text, spelled_writes, spud_calls, stdin_text, syntax, tree_writes, walk, zsh
from ..hooks import hookio


def analyse_command(command, analysis=None, depth=0):
    """Walk every simple command the shell would run, recursing into substitutions,
    `sh -c` strings, `eval` and here-documents fed to a shell."""
    a = analysis or syntax.ShellAnalysis()
    if depth > 6:
        return a
    for m in syntax._ASSIGNING_EXPANSION_RE.finditer(command):  # `${X:=git}` assigns X wherever it is expanded (SPD-043, probed)
        a.doubt.add(m.group(1))
        a.sticky.add(m.group(1))
    text, bodies = prepare.strip_heredocs(command)
    outer, inner = prepare.split_substitutions(prepare.newlines_as_separators(text))
    plain = prepare.neutralize_quoted_globs(outer)
    marked, other = zsh.mark_zsh_patterns(plain)
    tokens = syntax.shell_tokens(marked)  # the readings differ only in unquoted characters, so each tokenizes when zsh's does
    if tokens is None:
        for sub in inner:
            analyse_isolated(a, sub, depth + 1)
        a.unparseable = True
        return a
    cwds, variables, loop_depth, aliases = a.cwds, dict(a.vars), a.loop_depth, dict(a.aliases)
    zsh_walk = walk.ShellWalk(a, inner, bodies, depth)
    zsh_walk.walk(tokens)
    if other == marked and not zsh_walk.split_brace:
        # one reading: the line holds no zsh pattern, or only markings both shells make (SPD-088: arithmetic), and no brace
        # glued to a word that zsh splits off (SPD-132)
        return a
    # Two readings of one line (SPD-039): zsh's, its groups and ranges kept whole, then the other shell's, where a range is two
    # redirections (bash) and a group opening a word is read as shlex reads it, a subshell where one runs (mark_zsh_patterns).
    # SPD-132: zsh's reading splits a brace off the word it is glued to (`{git push}` is a group), bash's keeps it in the
    # word (`{git` is a command, `{ cd /tmp}` a cd into `/tmp}`), which is how the hook read every line before.
    # Every command and target either reading finds is checked, zsh's first; the directories and variables after the line are
    # those of both.  The quotes are the same, so both tokenize.
    zsh_cwds, zsh_vars, zsh_aliases = a.cwds, a.vars, a.aliases
    a.cwds, a.vars, a.loop_depth, a.cd_uncertain, a.aliases = cwds, variables, loop_depth, False, aliases
    walk.ShellWalk(a, inner, bodies, depth, glued=False).walk(tokens if other == marked else syntax.shell_tokens(other) or [])
    a.cwds = directories.union_dirs(zsh_cwds, a.cwds)
    a.doubt.update(set(zsh_vars) ^ set(a.vars))  # a variable only one reading assigns (SPD-043)
    for name, value in zsh_vars.items():
        a.vars[name] = value if a.vars.get(name, value) == value else hookio.SUBST  # readings that disagree: a value the hook cannot know
    for name in set(zsh_aliases) ^ set(a.aliases):
        a.doubt.add(syntax.ALIAS_KEY + name)  # an alias only one reading defines (SPD-059)
    for name, body in zsh_aliases.items():
        if a.aliases.get(name, body) != body:
            a.doubt.add(syntax.ALIAS_KEY + name)
        a.aliases.setdefault(name, body)
    return a


def isolated(a, run):
    """Run an analysis whose directory changes stay in its own process (a substitution, `sh -c`, a shell fed a body).  Its variables
    do not persist either: certain inside it, doubted after it (SPD-043)."""
    before, mark, unsure = a.cwds, len(a.assigned), a.unsure
    a.unsure = 0
    run()
    a.cwds = before
    a.cd_uncertain = False
    a.unsure = unsure
    a.doubt.update(a.assigned[mark:])


def analyse_isolated(a, command, depth):
    """analyse_command on a body that runs in its own process, once per body and starting state: both readings of a line walk
    its substitutions (SPD-039), and a nested line must not double its work at every level."""
    key = (command, depth, a.cwds, a.loop_depth, tuple(sorted(a.vars.items())), frozenset(a.doubt), frozenset(a.sticky),
           a.all_doubt, a.alias_scope)
    if key in a.isolated_done:
        return
    a.isolated_done.add(key)
    isolated(a, lambda: analyse_command(command, a, depth))


def analyse_new_shell(a, command, depth):
    """A body another shell process reads: a `-c` string, a here-document fed to a shell, the words `env -S` or `script -c`
    hand on.  An alias the line defined does not reach it (SPD-059, probed: `alias gp='git push'; eval 'sh -c gp'` ran
    nothing, while `eval 'echo $(gp)'` ran it, the substitution being parsed by the shell that holds the alias)."""
    state = (a.alias_scope, a.aliases, a.alias_unknown)
    a.alias_scope, a.aliases, a.alias_unknown = 0, {}, False
    try:
        analyse_isolated(a, command, depth)
    finally:
        a.alias_scope, a.aliases, a.alias_unknown = state


def analyse_segment(tokens, bodies, a, depth, redirect_cwds=syntax._CURRENT, piped=None, piped_fed=False):
    """A simple command: its output targets, then its words.  SPD-127: a target is resolved here, before analyse_words reads
    the command's own prefix assignments, because those reach neither the redirection nor the arguments in either shell
    (probed 2026-09-18: `S=$D/a; S=$D/b echo hi > $S.f` made a.f, and `S=$D/a echo hi > $S.f` with S unset made `.f`).
    SPD-143: `piped` is the text the pipeline element before this one printed, which with the command's own redirections
    makes the standard input a shell here would run (stdin_text.command_input); a.stdin holds it while the words are read
    and is put back after, so a body read in its own process reads its own input and not this one.
    SPD-150: `piped_fed` says a pipe feeds this element at all, which with the same redirections says whether anything
    stands on that input (stdin_text.input_fed), text the hook can spell or not; a.stdin_fed carries it the same way,
    for an interpreter that runs the program it reads there (shell/inline_programs)."""
    words, targets = directories.separate_redirects(tokens)
    cwds = a.cwds if redirect_cwds is syntax._CURRENT else redirect_cwds
    settled = [arg_writes.resolved(t, a) for t in targets]
    for target in settled:
        a.redirects.append((target, cwds))
    outer_stdin, a.stdin = a.stdin, stdin_text.command_input(tokens, bodies, piped)
    outer_fed, a.stdin_fed = a.stdin_fed, stdin_text.input_fed(tokens, bodies, piped_fed)
    try:
        analyse_words(words, bodies, a, depth, [globbing.GLOB_READING_BUDGET], "shell", False)
    finally:
        a.stdin, a.stdin_fed = outer_stdin, outer_fed
    if targets and words and all(assignment_words.assignment_word(w) for w in words):
        # An assignment-only command's own redirection is where the shells part: zsh opens it with the value the line had
        # before the command, bash with the one the command assigns (probed: `S=$D/a; S=$D/b > $S.f` made a.f in zsh and
        # b.f in bash).  Both readings are checked, as hidden_option checks both of sed's (SPD-121), so neither shell's
        # reading decides alone; where one of them stays raw it keeps the refusal an unresolvable target earns.
        for word, before in zip(targets, settled):
            after = arg_writes.resolved(word, a)
            if after != before:
                a.redirects.append((after, cwds))


def analyse_words(words, bodies, a, depth, budget, effect, prefixed, fresh=0):
    """A simple command's words, its redirections taken, read by dispatch_words.  SPD-128: a wrapper that moves the command
    it runs (`env -C <dir>`, `sudo -D <dir>`) moves that command alone, so the directories the shell may be in after it are
    the ones it had before, wherever the wrapper took the command and whatever that command would change."""
    before, moved = a.cwds, []
    try:
        dispatch_words(words, bodies, a, depth, budget, effect, prefixed, fresh, moved)
    finally:
        if moved:
            a.cwds = before


def dispatch_words(words, bodies, a, depth, budget, effect, prefixed, fresh, moved):
    """A simple command's words, its redirections taken: the prefixes, then what the command word dispatches on.  `effect` is
    where a builtin behind the prefixes runs (prefix_effect); `prefixed`, a wrapper, zsh's `-` or coproc runs the command
    (SPD-032: no spud call behind one is allowed).  A word the dispatch reads by name that the shell expands first (the command
    word, a wrapper's options and command, git's options, verb and the arguments git_refused reads, a shell's options, python's
    options and script, a spud call's arguments) is read as each word it can become (SPD-041, resolve_glob), an expansion in it
    before a glob (SPD-043, resolve_expansion).  `fresh`: the leading words an expansion in the command word gave, none of which
    the shell reads as an assignment or a reserved word, since it finds those before it expands -- and the same for the words a
    wrapper that execs its command word is handed (SPD-055: `nice x=./git push` runs git push, nice having exec'd `x=./git`).
    `moved`: set once a wrapper has moved the command into directories of its own, which analyse_words then gives back."""

    def read(i, wrapper_command=False, **kind):
        nonlocal fresh
        if expansions.expansion_word(words[i], command=i == 0):
            outcome = expansions.resolve_expansion(words, i, bodies, a, depth, budget, effect, prefixed, fresh, wrapper_command)
            if outcome == expansions._AGAIN and i == 0:
                fresh = len(words)  # past the command position, no word is an assignment
            return outcome
        return expansions._STOP if globbing.resolve_glob(words, i, kind, bodies, a, depth, budget, effect, prefixed, fresh) else expansions._AGAIN

    def read_points(index_of):
        """Read each word index_of(words, start) finds, in order; False when a reading analysed the command whole."""
        start = 1
        while (found := index_of(words, start)) is not None:
            k, kind = found
            outcome = read(k, **kind)
            if outcome == expansions._STOP:
                return False
            start = k + 1 if outcome == expansions._FLAGGED else k
        return True

    prefix_names, wrapper_from, spelled_command = [], 1, False
    path_names = []  # the wrapper names this command runs, which the shell finds on PATH like the command word (SPD-062)
    # SPD-060: `coproc` was read; SPD-059: no word has taken the command position away yet, so an alias the line defined is
    # still expanded here (a reserved word and an assignment keep it, a wrapper other than zsh's `time` does not).
    coproc, command_position = False, True
    input_appended = False  # SPD-126: an xargs this command runs under appends what it reads to the words (find_xargs)
    # SPD-143: the standard input the line gives this command, which a shell here runs as its commands, and the command
    # string an xargs makes of that input for the shell it runs (`echo 'git push' | xargs -0 sh -c`).  SPD-150: `fed`,
    # whether anything stands on that input at all, for an interpreter that runs the program it reads there.
    stdin, input_string, fed = a.stdin, None, a.stdin_fed
    while words:
        w = words[0]
        # SPD-085: `name[subscript]=value` too, read before any glob reading of its brackets
        m = None if fresh else assignment_words.assignment_word(w)
        reserved = not fresh and w in syntax.RESERVED_WORDS
        if not (m or reserved):
            a.doubt.update(prefix_names)  # a prefix assignment is the command's environment; the shell's variable keeps its value (SPD-043)
            prefix_names = []
            # an assignment's value is not expanded (probed: X=g?t kept g?t); a command word left as spelled is read once
            if not spelled_command and (expansions.expansion_word(w, command=True) or globbing.active_glob_word(w)):
                outcome = read(0, command=True)
                if outcome == expansions._STOP:
                    return
                spelled_command = outcome == expansions._FLAGGED
                continue
        spelled_command = False
        if reserved:
            if w == "coproc":
                # a forked shell of the shell's own, not an external program: a builtin runs there (SPD-054), and its
                # directory changes still never reach the line
                effect = max(effect, "fork", key=directories.EFFECT_ORDER.get)
                prefixed = coproc = True
            words = words[1:]
        elif coproc and len(words) > 1 and syntax.IDENTIFIER_RE.match(w) and words[1] in syntax.COPROC_COMPOUND_WORDS:
            # bash 4 and later run `coproc NAME compound_command` in the forked shell, and the hook read NAME for the command
            # (SPD-060, probed in bash 5.2): the group after the name is read exactly as the unnamed form's is.  A name the
            # hook cannot resolve reaches this as the expansion it is and refuses a member; `coproc NAME echo x` is a simple
            # command named NAME in every shell, and is left as it was.
            # `fresh` goes back to 0: an expansion that gave the name gave none of the words after it, and the shell read the
            # group's opener as the reserved word it is before it expanded anything (`N=NAME; coproc $N { git push; }`)
            words, coproc, fresh = words[1:], False, 0
        elif m:
            record_assignment(a, m)
            prefix_names.append(m[0])
            words = words[1:]
        elif w == "-":
            effect = max(effect, "either", key=directories.EFFECT_ORDER.get)  # zsh's `-` precommand modifier; bash finds no `-`
            prefixed = True
            command_position = False
            words = words[1:]
            fresh = max(fresh - 1, 0)
        elif os.path.basename(w).casefold() in syntax.WRAPPERS and w not in a.vars:
            rest, strings, consumed, env_assignments, chdir = directories.strip_wrapper(words)
            k = expansions.first_read_index(words[: consumed + 1], wrapper_from)  # its options, their values, and the command word it runs
            if k is not None:
                outcome = read(k, wrapper_command=k == consumed, command=k == consumed, dash=True, shift=k < consumed)
                if outcome == expansions._STOP:
                    return
                wrapper_from = k + 1 if outcome == expansions._FLAGGED else k
                continue
            wrapper_from = 1
            if os.path.basename(w).casefold() == "xargs":
                # SPD-126: what xargs reads from its input stands where -J or -I puts it, or after the words it runs
                rest, appended = find_xargs.xargs_input(words, consumed, rest)
                input_appended = input_appended or appended
                # SPD-143: xargs reads that input itself, so the command it runs does not; where the line spells it, it
                # is the command string a shell run with a `-c` and no string is handed
                # ... and xargs gives the command it runs no standard input of its own (SPD-150: never an inline program)
                input_string, stdin, fed = stdin_text.xargs_string(words, consumed, appended, stdin), None, False
            if chdir is not None:
                # SPD-128: everything the wrapper runs -- its words, a string it hands a shell, a nested wrapper -- starts in the
                # directory it moved to, once its own words are read (a glob or an expansion there leaves it unknown)
                a.cwds = directories.wrapped_directories(chdir, rest, a)
                moved.append(chdir)
            for aname, avalue in env_assignments:
                if aname.startswith(assignment_words.ENV_FUNCTION_PREFIX):
                    a.findings.append(("env-function", prepare.deglob(aname)))  # SPD-106: a function bash and sh import
                # `env GIT_CONFIG_*/HOME/GIT_PAGER/GIT_SSH_COMMAND/GIT_DIR/PATH/GIT_TRACE=... git ...` (SPD-044, SPD-046,
                # SPD-047, SPD-062, SPD-049)
                if syntax.IDENTIFIER_RE.match(aname) and (git_programs.is_git_config_var(aname) or git_programs.is_git_program_var(aname)
                        or git_programs.is_git_repo_var(aname) or git_programs.is_path_var(aname)
                        or git_programs.is_git_write_var(aname)):
                    a.vars[aname] = avalue
                    a.doubt.add(aname)  # the command's environment, not the shell's (SPD-043)
            path_names.append(w)
            prefixed = True
            # only zsh's `time` keeps the command position an alias is expanded in (SPD-059, probed: `eval 'time gp'` ran the
            # alias, `eval 'command gp'` and `eval 'env gp'` ran nothing)
            shell_modifier = command_position and w in zsh.ZSH_COMMAND_POSITION_WORDS
            command_position = w in zsh.ZSH_COMMAND_POSITION_WORDS
            effect = max(effect, directories.prefix_effect(w, words[1] if len(words) > 1 else None), key=directories.EFFECT_ORDER.get)
            # SPD-055: env and sudo read NAME=value as an assignment of their own, and the shell reads one after its own `time`
            # or `nocorrect`, which keep the command position; every other wrapper -- and `time` anywhere but in the command
            # position, where it is /usr/bin/time (probed) -- execs its first remaining word whatever it looks like, so none of
            # the words it is handed is an assignment or a reserved word, which is what `fresh` says.
            words = rest
            fresh = 0 if (shell_modifier or os.path.basename(w).casefold() in syntax.WRAPPER_TAKES_ASSIGNMENTS) else len(rest)
            for s in strings:
                analyse_new_shell(a, prepare.deglob(s), depth + 1)  # a shell reads the string with its own quotes (SPD-041)
        else:
            break
    if not words:
        return
    cmd = words[0]
    if syntax.unknown_operand(cmd):
        # SPD-126: the program is what xargs reads from its input (`xargs -J % % x`), or a file find found (`-exec {}`)
        a.kinds.append("other")
        a.findings.append(("var", syntax.shown_operands(prepare.deglob(cmd))))
        return
    # SPD-126: an input xargs appends is operands the line does not spell, read where a command writes by argument
    unspelled = [syntax.INPUT_OPERAND] * 2 if input_appended else []
    if cmd.startswith(assignment_words.ENV_FUNCTION_PREFIX) and "=" in cmd:
        # SPD-106: `BASH_FUNC_<name>%%=...` is no assignment to a shell, but sudo reads it as one, and env reads it so
        # wherever strip_wrapper did not: refused as the environment it spells, whatever takes it
        a.findings.append(("env-function", prepare.deglob(cmd.partition("=")[0])))
    if a.alias_scope and command_position:
        # SPD-059: inside `eval`, a command word the line aliased runs the alias's body, not a command of its own.  The body
        # is read as the shell text it is, with its own quotes and the words after it, as eval's rejoined words are.
        body, doubtful = expansions.alias_substitution(cmd, a)
        if body is not None or doubtful:
            if body is not None:
                rest = " ".join(prepare.deglob(w) for w in words[1:])
                analyse_command(body + (" " + rest if rest else ""), a, depth + 1)
            else:
                a.kinds.append("other")
            if doubtful:  # after the body, so a refusal the body itself earns keeps its own reason
                a.findings.append(("alias", prepare.deglob(cmd)))
            return
    if command_position and read_shell_name(words, a, depth):
        # SPD-133: the shell this line runs in already defines the command word as an alias, whose body took the command
        return
    if cmd in syntax.ASSIGNING_COMMANDS:
        for x in words[1:]:  # `read X`, `printf -v X`, `unset X`, `getopts o X`: X may now hold anything (SPD-043, probed)
            a.doubt.update(syntax._NAME_RE.findall(prepare.deglob(x)))
    if cmd in ("source", ".", "trap"):
        a.all_doubt = True  # code the hook does not read may assign any variable (SPD-043)
    base = os.path.basename(cmd).casefold()
    if base != "spud" and "/" in cmd and spud_calls.any_spud_launcher(cmd, a.cwds):
        base = "spud"  # a symlink to bin/spud run by its path, whatever its own name (SPD-029)
    if base == "git":
        if not read_points(lambda ws, start: expansions.option_point(expansions.git_read_index(ws, start))):
            return
        a.kinds.append("git")
        alias = git_programs.git_line_defines_alias(words) or git_programs.git_env_defines_alias(a.vars)
        if alias is not None:  # a defined alias/include or GIT_CONFIG_* injection: the verb the hook reads is not what runs (SPD-044)
            a.findings.append(("git-config", alias))
        else:
            program = git_programs.git_line_names_program(words) or git_programs.git_env_names_program(a.vars) or git_programs.git_verb_names_program(words)
            if program is not None:  # config, environment or a verb option names a program git runs under an allowed verb (SPD-046)
                a.findings.append(("git-program", program))
            else:
                verb, args = git_verbs.git_verb(words)
                refused = git_verbs.git_refused(verb, args)
                unknown = None if refused else git_verbs.git_unknown_verb(verb, a.home)
                if unknown is not None:  # not one of git's own commands: an alias or an external git-<verb> (SPD-047)
                    a.findings.append(("git-verb", unknown))
                else:  # one of git's own: Law 7's table first, then its allowlist, which refuses every other name (SPD-087)
                    a.findings.append(("git", (verb, refused or git_verbs.git_not_allowed(verb))))
        targets = git_verbs.git_repo_targets(words, a.vars)
        for spelled, target in targets:
            # another repository, whose .git/config the hook cannot read: resolved against the checkouts in bash_reason (SPD-047)
            a.findings.append(("git-repo", (spelled, target, a.cwds)))
        # ... and the repository this call does read, whose local and worktree scopes bash_reason holds to the allowlist (SPD-063)
        a.git_calls.append((tuple(targets), a.cwds))
        # SPD-127: git is handed the value, not the spelling, so a `$NAME` the line settled is resolved in the option that
        # names the file and in the variable whose value decides whether git writes a file at all (a GIT_TRACE* sibling
        # traces to a path only when its value is absolute; a descriptor or a relative one writes nothing).
        for spelled, target in git_verbs.git_write_targets(words, {n: arg_writes.resolved(v, a) for n, v in a.vars.items()}):
            # SPD-049: a file the call writes through one of its own options or the environment, held to the path rule
            # in bash_reason like a redirection target, for every caller
            a.git_writes.append((spelled, arg_writes.resolved(target, a), a.cwds))
    elif base in syntax.SHELLS:
        if not read_points(lambda ws, start: expansions.option_point(expansions.shell_read_index(ws, start))):
            return
        a.kinds.append("shell")
        i, dash_c = 1, False
        while i < len(words):
            w = words[i]
            if w.startswith("-") and "c" in w[1:] and not w.startswith("--"):
                dash_c = True
                string = prepare.deglob(words[i + 1]) if i + 1 < len(words) else None
                if input_string is not None:
                    # SPD-143: an xargs hands the shell the input the line spells, where its -I or -J replstr stands in
                    # the string (`xargs -I% sh -c 'rm %'`) or as the whole string (`xargs -0 sh -c`)
                    string = input_string if string is None else string.replace(syntax.INPUT_OPERAND, input_string)
                if string is not None:
                    analyse_new_shell(a, string, depth + 1)  # read with its own quotes: `sh -c 'g?t push'` (SPD-041)
                break
            if not w.startswith("-"):
                break
            i += 1
        for body in bodies:
            analyse_new_shell(a, body, depth + 1)
        if not bodies and not dash_c and stdin is not None and stdin_text.reads_commands(words):
            # SPD-143: with no -c string and no script of its own the shell runs what it reads on standard input, and
            # the line spells that text: `echo 'git push' | sh`, `bash -s <<< 'git push'`, `cat <<'EOF' | sh`
            analyse_new_shell(a, stdin, depth + 1)
    elif base == "eval":
        a.kinds.append("eval")
        before = a.cwds
        a.alias_scope += 1  # an alias the line defined is expanded where eval parses its words again (SPD-059)
        try:
            analyse_command(prepare.deglob(" ".join(words[1:])), a, depth + 1)  # eval reads its words again, their quotes gone (SPD-041)
        finally:
            a.alias_scope -= 1
        a.cwds = directories.settle(effect, before, a.cwds)
    elif cmd in ("source", ".") and directories.builtin_runs_here(effect):
        a.kinds.append("other")
        a.cwds = None  # the file may change directory anywhere
    elif cmd == "trap" and directories.builtin_runs(effect):  # the builtin, spelled exactly: env trap and /usr/bin/trap set no trap (SPD-054)
        a.kinds.append("other")  # never a spud call, so a line that sets a trap is not allowed on its own
        if not read_points(lambda ws, start: expansions.option_point(expansions.trap_read_index(ws, start))):
            return
        expansions.analyse_trap(words, a, depth)
    elif base in ("sqlite3", "sqlite"):
        a.kinds.append("db")
        a.findings.append(("db", cmd))
    elif syntax.PYTHON_RE.match(base):
        if not read_points(lambda ws, start: expansions.script_point(expansions.python_read_index(ws, a, start))):
            return
        code, module, stdin_script, script, script_args = spud_calls.python_interpreter_args(words[1:])
        if (code and "sqlite" in code.lower()) or (module and "sqlite" in module.lower()) or (stdin_script and any("sqlite" in b.lower() for b in bodies)):
            a.kinds.append("db")
            a.findings.append(("db", cmd))
        elif script and spud_calls.any_spud_launcher(script, a.cwds):
            a.kinds.append("spud")
            call = spud_calls.parse_spud_call(script_args)
            call["vouched"] = spud_calls.vouched_spud_call(a, cmd, words[1 : len(words) - len(script_args) - 1], script, prefixed)
            a.findings.append(("spud", call))
        else:
            # SPD-150: after the database and the launcher, which keep their reasons: a program the line spells rather
            # than reads from a file (`-c`, or standard input under `-` or no script) is one the hook cannot read at all
            a.kinds.append("other")
            inline_programs.read_inline(cmd, base, words, a, fed)
    elif base in syntax.JS_RUNTIMES:
        if any("sqlite" in w.lower() for w in words[1:]) or any("sqlite" in b.lower() for b in bodies):
            a.kinds.append("db")
            a.findings.append(("db", cmd))
        else:
            a.kinds.append("other")
            inline_programs.read_inline(cmd, base, words, a, fed)  # SPD-150: -e, --eval, -p, --print, or standard input
    elif base == "spud":
        if not read_points(lambda ws, start: expansions.option_point(expansions.first_read_index(ws, start))):
            return
        a.kinds.append("spud")
        call = spud_calls.parse_spud_call(words[1:])
        call["vouched"] = False  # its #! line runs python3.14 with neither -I nor -S (SPD-032)
        a.findings.append(("spud", call))
    elif base == "tee":
        a.kinds.append("tee")
        for w in words[1:]:
            # the word as spelled decides whether tee reads it as an option, as this Mac's getopt does; what it names is
            # the value the line settled (SPD-127), which is the file tee opens
            if not w.startswith("-"):
                a.redirects.append((arg_writes.resolved(w, a), a.cwds))
    elif base in syntax.ARG_WRITE_COMMANDS:
        # SPD-121: a command that writes the files it names as operands, read where tee is, so bash_reason holds each to
        # the path rule as it holds a redirection target.  The words this Mac's getopt reads as options are read by name
        # first, so a glob among them is read as each option it can become (`sed -? '' s/a/b/ f` is `sed -i`).
        if not read_points(lambda ws, start: expansions.option_point(arg_writes.option_read_index(base, ws, start, a))):
            return
        a.kinds.append("other")
        arg_writes.read_writes(prepare.deglob(cmd), base, words + unspelled, a)
        if base in syntax.SCRIPT_COMMANDS:  # SPD-139: sed's own script, beside the files its -i names
            script_text.read_script(prepare.deglob(cmd), base, words + unspelled, a, depth)
    elif base in syntax.SCRIPT_COMMANDS:
        # SPD-139: awk, whose program names the files it writes and the commands it hands /bin/sh; each file is held to
        # the path rule where tee's operand is, and each command read where an `sh -c` string is
        a.kinds.append("other")
        script_text.read_script(prepare.deglob(cmd), base, words + unspelled, a, depth)
    elif base in syntax.TREE_WRITE_COMMANDS:
        # SPD-126: a command whose files the line does not spell -- what find deletes and runs, an archive extracted, a
        # patch applied, a download the server names, a tree synced -- read where tee is, a whole-subtree write each, with
        # the files the same command names (a download's in shell/downloads)
        a.kinds.append("other")
        if base == "find":
            find_xargs.read_find(prepare.deglob(cmd), words + unspelled, a, depth)
        elif base in syntax.DOWNLOAD_COMMANDS:
            downloads.read_download(prepare.deglob(cmd), base, words + unspelled, a, depth)
        else:
            tree_writes.read_tree_writes(prepare.deglob(cmd), base, words + unspelled, a, depth)
    elif base in syntax.SPELLED_WRITE_COMMANDS or syntax.PERL_RE.match(base):
        # SPD-126: a command that writes a file it names past SPD-121's table -- dd's of=, sort's -o, mktemp's templates,
        # split's pieces, perl -i -- read where tee is, each held to the path rule as a redirection target
        a.kinds.append("other")
        spelled_writes.read_spelled_writes(prepare.deglob(cmd), base, words + unspelled, a, depth)
        # SPD-150: perl's -e and -E, and the program it reads on standard input; dd, sort, mktemp and split run none
        inline_programs.read_inline(cmd, base, words, a, fed)
    elif inline_programs.RUBY_RE.match(base):
        # SPD-150: the one interpreter of the table with no reading of its own here -- ruby -e, and its standard input
        a.kinds.append("other")
        inline_programs.read_inline(cmd, base, words, a, fed)
    elif cmd in ("alias", "unalias") and directories.builtin_runs(effect):
        # SPD-059: the builtin, spelled exactly, stores text the shell runs wherever it next parses this name in command
        # position -- which on one line means `eval`.  Never a spud call, so an aliasing line is not allowed on its own.
        a.kinds.append("other")
        if cmd == "alias":
            expansions.record_alias_line(words, a)
        else:
            expansions.clear_alias_line(words, a)
    elif cmd == "hash" and directories.builtin_runs(effect):
        # SPD-062: the builtin, spelled exactly, puts a file of the line's own choosing in the shell's command table, so a
        # later bare call of that name runs it whatever PATH holds.  Probed in bash 3.2 and sh (`hash -p <dir>/<name> <name>`)
        # and zsh 5.9 -f and -o nobareglobqual (`hash <name>=<dir>/<name>`): both ran the scratch copy, and `hash`, `hash -r`
        # and `hash -l` list or clear and name nothing.  Never a spud call, so a hashing line is not allowed on its own.
        a.kinds.append("other")
        a.hashed.update(hashed_names(words))
    elif cmd in syntax.DIRECTORY_COMMANDS and directories.builtin_runs_here(effect):
        a.kinds.append("cd")
        directories.directory_change(words, a, effect)
    elif cmd in syntax.SHELL_DECLARATIONS:
        a.kinds.append("other")
        for w in words[1:]:
            m = assignment_words.declaration_word(w)
            if m:
                record_assignment(a, m)
            elif w.startswith(assignment_words.ENV_FUNCTION_PREFIX):  # `export 'BASH_FUNC_git%%=...'`, `export BASH_FUNC_x` (SPD-106)
                a.findings.append(("env-function", prepare.deglob(w.partition("=")[0])))
        a.findings.extend(("env-function", name) for name in exported_function_names(words))
        if any(w.startswith(("-", "+")) for w in words[1:]):
            for w in words[1:]:  # an attribute (`declare -n X=Y`, `typeset -i`, `local -a`) changes what the name reads (SPD-043)
                a.doubt.update(syntax._NAME_RE.findall(prepare.deglob(w)))
    else:
        a.kinds.append("other")  # CD, /usr/bin/cd, env cd: /usr/bin/cd in its own process, and the shell stays
    shadowed_name(a, cmd, path_names)  # after the dispatch, so a refusal the words as spelled earn keeps its own reason


def read_shell_name(words, a, depth):
    """A command word the shell the Bash tool starts already defines (SPD-133), read for what it actually runs: an alias,
    whose body and the words after it are analysed as the text the shell put there -- and True, since that text is the
    command now -- or a function, whose body is read as an `eval` string is while the call's own words go on to be
    dispatched for what they name.  An alias shadows a function of the same name, as the shell resolves them."""
    cmd = prepare.deglob(words[0])
    text, own_words, expanded, unreadable = expansions.shell_aliased(words, a)
    a.shell_expanded.extend(expanded)
    if unreadable is not None:  # the chain reached a body whose quoting the hook cannot take off: it runs the line, unread
        a.kinds.append("other")
        a.findings.append(("shell-alias", unreadable))
        return True
    if expanded:
        a.expanding.extend(name for name, _ in expanded)
        try:
            analyse_shell_text(a, text, depth + 1, own_words)
        finally:
            del a.expanding[len(a.expanding) - len(expanded):]
        return True
    body = expansions.shell_function(cmd, a)
    if body is not None:
        a.shell_expanded.append((cmd, "a shell function"))
        a.bodies_read.add(cmd)
        # the call's own words reach the body as its positional parameters, which member_supplied reads there
        analyse_shell_text(a, body, depth + 1, [prepare.deglob(w) for w in words[1:]], own_process=True)
    return False


def analyse_shell_text(a, text, depth, own_words, own_process=False):
    """Read text the shell itself holds: an alias's body, which is the line's own text once the shell has parsed it, or a
    function's, whose directory changes and assignments stay its own (own_process).  `own_words` are the member's own
    words of the line that reach this text -- the words after the alias the shell expanded, or the call's arguments, which
    a function receives as its positional parameters.

    The findings are the findings the text would earn on the line, minus two kinds that would fall on a member for text it
    did not write and cannot change: the ones that say only that the hook cannot read a word (syntax.SHELL_TEXT_TOLERATED)
    and a write whose target the hook cannot resolve.  Claude Code shadows find, grep, rg and pkill with functions that
    dispatch through `"$_cc_bin"` and write through `$data`-shaped names of their own; without both prunes a member would
    be refused every `grep` it runs.

    A target the member supplied is never pruned (member_supplied): an alias's expansion is its body followed by the
    member's own words, and a function's `$@` is them, so pruning on the target's spelling alone let an alias launder
    exactly what the unresolvable-target rule exists to refuse -- `md $HOME/planted` recorded no write while
    `mkdir -p $HOME/planted` was refused.  A concrete file the text writes is held to the path rule for the caller, as an
    alias's redirection is anywhere else.  Only the outermost of these readings prunes, so a nested one never drops what
    the reading closest to the member's words keeps (SPD-133).

    A target the text's own line settles is resolved before this sees it (SPD-127), so it is a concrete file and the prune
    does not reach it: a body that writes `$data` after assigning it goes to the path rule like any spelled path, while the
    harness's `"$_cc_bin"` and the environment it reads, which no line settles, stay as unresolvable as they were."""
    outermost = a.shell_reading == 0
    marks = (len(a.findings), len(a.redirects), len(a.git_writes), len(a.arg_writes))
    a.shell_reading += 1
    try:
        if own_process:
            analyse_isolated(a, text, depth)
        else:
            analyse_command(text, a, depth)
    finally:
        a.shell_reading -= 1
    if not outermost:
        return
    supplied = frozenset(own_words)
    a.findings[marks[0]:] = [f for f in a.findings[marks[0]:] if f[0] not in syntax.SHELL_TEXT_TOLERATED]
    a.redirects[marks[1]:] = [e for e in a.redirects[marks[1]:] if keeps_write(e[0], supplied)]
    a.git_writes[marks[2]:] = [e for e in a.git_writes[marks[2]:] if keeps_write(e[1], supplied)]
    a.arg_writes[marks[3]:] = [e for e in a.arg_writes[marks[3]:] if keeps_write(e[1], supplied)]


def keeps_write(target, supplied):
    """Whether a write this text would make is read as it stands: every target the hook can resolve, and every one the
    member's own words supplied, whatever the hook can make of it."""
    return not unresolvable_write(target) or member_supplied(target, supplied)


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


def record_assignment(a, found):
    """Record a word the shell reads as an assignment, found = (name, subscript or None, whether it appends, value) from
    assignment_words.  The variable is assigned where the shell runs it (SPD-043); a subscripted one changes part of its
    value, which the hook does not compute, so its value is unknown as an appended one's is, and it counts for every rule
    that reads the variable as a plain assignment does -- PATH and zsh's `path` for shadowed_name, CDPATH, GIT_* (SPD-085).
    An element of zsh's `functions`, `commands` or `aliases` binds the name it keys as a definition, a `hash` or an `alias`
    line would (SPD-105), a name the hook cannot read standing for all of them; and a BASH_FUNC_ variable is refused
    outright (SPD-106)."""
    name, subscript, append, value = found
    if name.startswith(assignment_words.ENV_FUNCTION_PREFIX):
        a.findings.append(("env-function", name))
    special = assignment_words.special_bindings(name, subscript, append, value)
    if special is not None:
        table, pairs = special
        if table == "alias":
            if pairs is None:
                a.alias_unknown = True
            for key, body in pairs or ():
                expansions.record_alias_definition(a, key, body)
        else:
            names = a.functions if table == "function" else a.hashed
            names.update([syntax.UNKNOWN_NAME] if pairs is None else [prepare.deglob(key) for key, _ in pairs])
    expansions.assign_variable(a, name, value, append or subscript is not None)


def exported_function_names(words):
    """The variables `export -f <name>` and `declare -fx <name>` (typeset, local) put in the environment of every program
    bash starts later, BASH_FUNC_<name>%%, for a name the hook reads (SPD-106): a bash or sh started under any of them
    imports the function, which shadows the name there (probed: `bash -c 'foo() { echo SH; }; export -f foo; sh -c foo'`
    ran it).  zsh's `export -f` lists functions and exports nothing (probed), so on the Bash tool's own line this is a
    refusal on doubt, the line being read for every shell.  A name the hook grants nothing for is left alone: its body is
    on the line and was read."""
    options = "".join(w[1:] for w in words[1:] if w.startswith("-") and w != "--")
    if "f" not in options or (words[0] != "export" and "x" not in options):
        return []
    return [assignment_words.ENV_FUNCTION_PREFIX + prepare.deglob(w) + "%%" for w in words[1:]
            if not w.startswith("-") and git_programs.path_dispatched(w)]


def hashed_names(words):
    """The command names a `hash` line puts in the shell's own table (SPD-062): every operand's name, `<name>` after bash's
    `-p <pathname>` or `<name>=<path>` in zsh.  Read loosely -- a name after any option, `-d` and `-t` included -- so a line
    that only prints or forgets an entry is refused with it: a member has no reason to hash a name the hook reads."""
    names, i, options = [], 1, True
    while i < len(words):
        w = words[i]
        if options and w.startswith("-") and len(w) > 1:
            if w == "--":
                options = False
            elif not w.startswith("--") and "p" in w[1:]:
                i += 1  # bash's `-p <pathname>`, whose value is the file, not a name
            i += 1
            continue
        options = False
        names.append(prepare.deglob(w).split("=", 1)[0])
        i += 1
    return [n for n in names if n]


def shadowed_name(a, cmd, path_names):
    """Record that the shell would not run the program the hook read by name (SPD-062, SPD-084): the line bound the name to
    a shell function, assigned PATH (or zsh's `path`, tied to it) so the shell searches a directory of the line's own
    choosing, or hashed the name to a file of its own.  Only the names the hook reads count (git, spud, python3.14, sqlite3,
    tee, a shell, a wrapper): for any other name the hook grants nothing, so replacing its program takes a member no further
    than running a program of its own.  A command run by a path is not looked for on PATH, and GIT_EXEC_PATH keeps SPD-046's
    reason.

    A function is looked up in command position, so it shadows the command word `cmd` unless a wrapper that resolves its own
    word (`command`, `builtin`, `env`, `nice`, ...) took the position; the wrappers zsh keeps looking a function up after
    (`exec`, `noglob`, `nocorrect`, `time`) and its `-` modifier leave `cmd` in command position, and a function named for
    the first such resolving wrapper shadows it in turn (SPD-084, probed in the four shells).  A PATH or a hash, in contrast,
    decides the lookup of every one of these names, so both are read for the command word and the wrappers alike."""
    bypass = [w for w in path_names if os.path.basename(w).casefold() not in syntax.FUNCTION_KEEP_WRAPPERS]
    for word in bypass[:1] if bypass else [cmd]:
        if git_programs.path_dispatched(word) and (prepare.deglob(word) in a.functions or syntax.UNKNOWN_NAME in a.functions):
            a.findings.append(("function", prepare.deglob(word)))
            return
    for word in [cmd] + path_names:
        if not git_programs.path_dispatched(word):
            continue
        name = prepare.deglob(word)
        if name in a.hashed or syntax.UNKNOWN_NAME in a.hashed:
            a.findings.append(("hashed", name))
            return
        var = git_programs.path_in_force(a.vars)
        if var is not None:
            a.findings.append(("path", (var, name)))
            return
