"""shell/analyse: analyse_command and analyse_words.  Moved from bin/spud_ledger.py (SPD-065)."""

import os

from . import directories, expansions, git_programs, git_verbs, globbing, prepare, spud_calls, syntax, walk, zsh
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
    if marked == plain:
        walk.ShellWalk(a, inner, bodies, depth).walk(tokens)
        return a
    # Two readings of one line (SPD-039): zsh's, its groups and ranges kept whole, then the other shell's, where a range is two
    # redirections (bash) and a group opening a word is read as shlex reads it, a subshell where one runs (mark_zsh_patterns).
    # Every command and target either reading finds is checked, zsh's first; the directories and variables after the line are
    # those of both.  The quotes are the same, so both tokenize.
    cwds, variables, loop_depth = a.cwds, dict(a.vars), a.loop_depth
    walk.ShellWalk(a, inner, bodies, depth).walk(tokens)
    if other == marked:
        return a
    zsh_cwds, zsh_vars = a.cwds, a.vars
    a.cwds, a.vars, a.loop_depth, a.cd_uncertain = cwds, variables, loop_depth, False
    walk.ShellWalk(a, inner, bodies, depth).walk(syntax.shell_tokens(other) or [])
    a.cwds = directories.union_dirs(zsh_cwds, a.cwds)
    a.doubt.update(set(zsh_vars) ^ set(a.vars))  # a variable only one reading assigns (SPD-043)
    for name, value in zsh_vars.items():
        a.vars[name] = value if a.vars.get(name, value) == value else hookio.SUBST  # readings that disagree: a value the hook cannot know
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
    key = (command, depth, a.cwds, a.loop_depth, tuple(sorted(a.vars.items())), frozenset(a.doubt), frozenset(a.sticky), a.all_doubt)
    if key in a.isolated_done:
        return
    a.isolated_done.add(key)
    isolated(a, lambda: analyse_command(command, a, depth))


def analyse_segment(tokens, bodies, a, depth, redirect_cwds=syntax._CURRENT):
    words, targets = directories.separate_redirects(tokens)
    for t in targets:
        a.redirects.append((t, a.cwds if redirect_cwds is syntax._CURRENT else redirect_cwds))
    analyse_words(words, bodies, a, depth, [globbing.GLOB_READING_BUDGET], "shell", False)


def analyse_words(words, bodies, a, depth, budget, effect, prefixed, fresh=0):
    """A simple command's words, its redirections taken: the prefixes, then what the command word dispatches on.  `effect` is
    where a builtin behind the prefixes runs (prefix_effect); `prefixed`, a wrapper, zsh's `-` or coproc runs the command
    (SPD-032: no spud call behind one is allowed).  A word the dispatch reads by name that the shell expands first (the command
    word, a wrapper's options and command, git's options, verb and the arguments git_refused reads, a shell's options, python's
    options and script, a spud call's arguments) is read as each word it can become (SPD-041, resolve_glob), an expansion in it
    before a glob (SPD-043, resolve_expansion).  `fresh`: the leading words an expansion in the command word gave, none of which
    the shell reads as an assignment or a reserved word, since it finds those before it expands."""

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
    while words:
        w = words[0]
        m = None if fresh else syntax.ASSIGNMENT_WORD_RE.match(w)
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
                prefixed = True
            words = words[1:]
        elif m:
            name, append, value = m.groups()
            expansions.assign_variable(a, name, value, bool(append))
            prefix_names.append(name)
            words = words[1:]
        elif w == "-":
            effect = max(effect, "either", key=directories.EFFECT_ORDER.get)  # zsh's `-` precommand modifier; bash finds no `-`
            prefixed = True
            words = words[1:]
            fresh = max(fresh - 1, 0)
        elif os.path.basename(w).casefold() in syntax.WRAPPERS and w not in a.vars:
            rest, strings, consumed, env_assignments = directories.strip_wrapper(words)
            k = expansions.first_read_index(words[: consumed + 1], wrapper_from)  # its options, their values, and the command word it runs
            if k is not None:
                outcome = read(k, wrapper_command=k == consumed, command=k == consumed, dash=True, shift=k < consumed)
                if outcome == expansions._STOP:
                    return
                wrapper_from = k + 1 if outcome == expansions._FLAGGED else k
                continue
            wrapper_from = 1
            for aname, avalue in env_assignments:
                # `env GIT_CONFIG_*/HOME/GIT_PAGER/GIT_SSH_COMMAND/GIT_DIR=... git ...` (SPD-044, SPD-046, SPD-047)
                if git_programs.is_git_config_var(aname) or git_programs.is_git_program_var(aname) or git_programs.is_git_repo_var(aname):
                    a.vars[aname] = avalue
                    a.doubt.add(aname)  # the command's environment, not the shell's (SPD-043)
            prefixed = True
            effect = max(effect, directories.prefix_effect(w, words[1] if len(words) > 1 else None), key=directories.EFFECT_ORDER.get)
            words, fresh = rest, 0
            for s in strings:
                analyse_isolated(a, prepare.deglob(s), depth + 1)  # a shell reads the string with its own quotes (SPD-041)
        else:
            break
    if not words:
        return
    cmd = words[0]
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
                else:
                    a.findings.append(("git", (verb, refused)))
        targets = git_verbs.git_repo_targets(words, a.vars)
        for spelled, target in targets:
            # another repository, whose .git/config the hook cannot read: resolved against the checkouts in bash_reason (SPD-047)
            a.findings.append(("git-repo", (spelled, target, a.cwds)))
        # ... and the repository this call does read, whose local and worktree scopes bash_reason holds to the allowlist (SPD-063)
        a.git_calls.append((tuple(targets), a.cwds))
    elif base in syntax.SHELLS:
        if not read_points(lambda ws, start: expansions.option_point(expansions.shell_read_index(ws, start))):
            return
        a.kinds.append("shell")
        i = 1
        while i < len(words):
            w = words[i]
            if w.startswith("-") and "c" in w[1:] and not w.startswith("--"):
                if i + 1 < len(words):
                    analyse_isolated(a, prepare.deglob(words[i + 1]), depth + 1)  # read with its own quotes: `sh -c 'g?t push'` (SPD-041)
                break
            if not w.startswith("-"):
                break
            i += 1
        for body in bodies:
            analyse_isolated(a, body, depth + 1)
    elif base == "eval":
        a.kinds.append("eval")
        before = a.cwds
        analyse_command(prepare.deglob(" ".join(words[1:])), a, depth + 1)  # eval reads its words again, their quotes gone (SPD-041)
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
            a.kinds.append("other")
    elif base in syntax.JS_RUNTIMES:
        if any("sqlite" in w.lower() for w in words[1:]) or any("sqlite" in b.lower() for b in bodies):
            a.kinds.append("db")
            a.findings.append(("db", cmd))
        else:
            a.kinds.append("other")
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
            if not w.startswith("-"):
                a.redirects.append((w, a.cwds))
    elif cmd in syntax.DIRECTORY_COMMANDS and directories.builtin_runs_here(effect):
        a.kinds.append("cd")
        directories.directory_change(words, a, effect)
    elif cmd in syntax.SHELL_DECLARATIONS:
        a.kinds.append("other")
        for w in words[1:]:
            m = syntax.ASSIGNMENT_WORD_RE.match(w)
            if m:
                expansions.assign_variable(a, m.group(1), m.group(3), bool(m.group(2)))
        if any(w.startswith(("-", "+")) for w in words[1:]):
            for w in words[1:]:  # an attribute (`declare -n X=Y`, `typeset -i`, `local -a`) changes what the name reads (SPD-043)
                a.doubt.update(syntax._NAME_RE.findall(prepare.deglob(w)))
    else:
        a.kinds.append("other")  # CD, /usr/bin/cd, env cd: /usr/bin/cd in its own process, and the shell stays
