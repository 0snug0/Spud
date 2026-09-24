"""shell/analyse: analyse_command and analyse_words.

Past 250 lines as one function and its way in: dispatch_words, reached through analyse_command, analyse_segment and
analyse_words.  The reading of the text the shell holds, an alias's body and a function's, is shell/held_text (SPD-264)."""

import os

from . import arg_writes, assignment_words, directories, downloads, expansions, find_xargs, git_programs, git_verbs, git_writes, globbing, held_text, heredocs, inline_programs, interpreter_words, loop_bindings, prepare, runtime_shells, script_files, script_runners, script_text, spelled_writes, spud_calls, stdin_text, syntax, tree_writes, unread, walk, zsh
from ..hooks import hookio


# The most levels of command substitution, `eval`, `-c` string or here-document the hook reads into: past it the
# innermost text is dropped, so a member is refused it as text the hook never read (SPD-195, unread_reason "depth"), the
# bound staying so the reading stays bounded.  reevaluation.EVALUATION_DEPTH mirrors it for a value zsh's (e) flag reads.
READING_DEPTH = 6


def analyse_command(command, analysis=None, depth=0, stdin=None, fed=False):
    """Walk every simple command the shell would run, recursing into substitutions,
    `sh -c` strings, `eval`, here-documents fed to a shell, and the substitutions an unquoted here-document's
    body expands wherever it is fed (SPD-192).  `stdin` and `fed`: the standard input the text runs on and whether
    anything stands there (as syntax.ShellAnalysis.stdin and stdin_fed say them), which the commands in it read where
    no redirection or pipe of their own replaces it -- a `-c` string's and eval's, their command's (SPD-210)."""
    a = analysis or syntax.ShellAnalysis()
    if depth > READING_DEPTH:
        unread.record_unread(a, "depth", (READING_DEPTH, unread.unread_shown(command)))  # SPD-195: past the bound, refused a member
        # ... and text that runs in the shell reading it (an eval's, a function's) may move its directory where the hook
        # cannot follow; a body in a process of its own puts the directories back after this (isolated, SPD-252)
        a.cwds = None
        a.dir_moves += 1
        return a
    if depth == 0:
        held_text.line_options(a)  # the options the shell's snapshot set before the line (SPD-263)
    if depth == 0 and not command.isascii() and unread.has_marker(command):
        # SPD-199: a private-use marker the member typed into the reading's own alphabet, on the raw line before any pass
        # writes one; recorded and read on, so a refusal the readable words earn keeps its own reason.
        unread.record_unread(a, "placeholder", unread.marker_shown(command))
    if "${" in command and unread.brace_depth_exceeds(command, unread.BRACE_DEPTH):
        # SPD-103: a `${ }` nested past the bound; recorded (a member's refusal) and read on, so Spud's own refusal on an
        # unresolvable target still stands.  The scan stops at the bound, so a run of openings is refused at once.
        unread.record_unread(a, "braces", (unread.BRACE_DEPTH, unread.unread_shown(command)))
    for m in syntax._ASSIGNING_EXPANSION_RE.finditer(command):  # `${X:=git}` assigns X wherever it is expanded (probed)
        a.doubt.add(m.group(1))
        a.sticky.add(m.group(1))
        a.unseen_assigned.add(m.group(1))  # SPD-221: no loop body's basename settles it
        a.line_assigned.update(a.reaching((m.group(1),)))  # ... and it is the line's variable (SPD-205, SPD-254)
        end, depth_left = m.end(), 1  # its default, to the brace that closes it: a variable it reads fills X (SPD-258)
        while end < len(command) and depth_left:
            depth_left += {"{": 1, "}": -1}.get(command[end], 0)
            end += 1
        expansions.fill_from(a, (m.group(1),), command[m.end() : end])
    text, bodies, expanded = heredocs.strip_heredocs(command)
    text, apart = prepare.ansi_c_quotes(text)
    if apart is not None and a.unparseable is None:
        # an ANSI-C string that never closes, or a quote zsh and bash end apart (SPD-202): the words the reading finds are
        # read on, and the line is refused every caller after the refusals they earn, as one shlex cannot split is (SPD-191)
        a.unparseable = apart + ("line" if depth == 0 else "shell" if a.shell_reading else "nested",)
    outer, inner = prepare.split_substitutions(prepare.newlines_as_separators(text))
    for sub in inner:
        if unread.substitution_incomplete(sub):  # SPD-194: a `$( )` whose closing `)` the reader cannot place
            unread.record_unread(a, "subst-end", unread.unread_shown(sub))
    if inner and outer.count(hookio.SUBST) - outer.count(walk.PROCSUB_FILE) > len(inner):
        # SPD-199: more placeholders than lifted bodies (a `<( )`'s file name carries a placeholder that pairs with no
        # body, so it is not counted, as consume does not), so the member typed one; with a real substitution present it
        # steals that body in consume's pairing.  Recorded and read on; the raw line spells the placeholder for the reason.
        unread.record_unread(a, "placeholder", unread.marker_shown(command))
    plain = prepare.neutralize_quoted_globs(outer)
    marked, other = zsh.mark_zsh_patterns(plain)
    tokens = syntax.shell_tokens(marked)  # the readings differ only in unquoted characters, so each tokenizes when zsh's does
    if tokens is None:
        # The text holds a quote that never closes, or ends in a backslash with nothing to escape: nothing of it is read
        # but its substitutions, and the line is refused every caller (bash_rule, SPD-191).  Recorded before the
        # substitutions are read, so the reason names this text's own quote rather than one a body of it holds.
        if a.unparseable is None:
            where = "line" if depth == 0 else "shell" if a.shell_reading else "nested"
            a.unparseable = syntax.untokenized(marked) + (where,)
        for sub in inner:
            analyse_isolated(a, sub, depth + 1, stdin, fed)
        return a
    start = walk.reading_start(a)
    zsh_walk = walk.walk_line(a, tokens, inner, bodies, expanded, depth, start, True, stdin, fed)
    if other == marked and not zsh_walk.split_brace:
        # one reading: the line holds no zsh pattern, or only markings both shells make (arithmetic), and no brace
        # glued to a word that zsh splits off
        return a
    # Two readings of one line: zsh's, its groups and ranges kept whole, then the other shell's, where a range is two
    # redirections (bash) and a group opening a word is read as shlex reads it, a subshell where one runs (mark_zsh_patterns).
    # zsh's reading splits a brace off the word it is glued to (`{git push}` is a group), bash's keeps it in the
    # word (`{git` is a command, `{ cd /tmp}` a cd into `/tmp}`), which is how the hook read every line before.
    # Every command and target either reading finds is checked, zsh's first; the directories and variables after the line are
    # those of both.  The quotes are the same, so both tokenize.
    zsh_cwds, zsh_vars, zsh_aliases, zsh_bodies = a.cwds, a.vars, a.aliases, a.function_bodies
    zsh_locals = None if a.body_locals is None else a.body_locals[-1]
    walk.restore_reading(a, start)
    walk.walk_line(a, tokens if other == marked else syntax.shell_tokens(other) or [], inner, bodies, expanded, depth, start,
                   False, stdin, fed)
    if zsh_locals is not None:  # a name is surely local only where both readings declared it so (SPD-246)
        a.body_locals[-1] = {name: before for name, before in a.body_locals[-1].items() if name in zsh_locals}
    for name, found in zsh_bodies.items():  # a function body either reading defines (SPD-212, walk.read_call)
        a.function_bodies.setdefault(name, set()).update(found)
    a.cwds = directories.union_dirs(zsh_cwds, a.cwds)
    a.doubt.update(set(zsh_vars) ^ set(a.vars))  # a variable only one reading assigns
    for name, value in zsh_vars.items():
        a.vars[name] = value if a.vars.get(name, value) == value else hookio.SUBST  # readings that disagree: a value the hook cannot know
    for name in set(zsh_aliases) ^ set(a.aliases):
        a.doubt.add(syntax.ALIAS_KEY + name)  # an alias only one reading defines
    for name, body in zsh_aliases.items():
        if a.aliases.get(name, body) != body:
            a.doubt.add(syntax.ALIAS_KEY + name)
        a.aliases.setdefault(name, body)
    return a


def isolated(a, run):
    """Run an analysis whose directory changes stay in its own process (a substitution, `sh -c`, a shell fed a body).  Its variables
    do not persist either: certain inside it, doubted after it -- and nothing it declares local is local after it (SPD-246).
    A function body the shell holds is read here too, in a scope of its own (held_text.read_body), which gives the line back the
    directories the body left, since it ran in the line's shell (SPD-252)."""
    before, mark, unsure, scopes = a.cwds, len(a.assigned), a.unsure, a.body_locals
    a.unsure = 0
    if scopes is not None:
        a.body_locals = scopes[:-1] + [dict(scopes[-1])]
    run()
    a.cwds = before
    a.cd_uncertain = False
    a.unsure = unsure
    a.body_locals = scopes
    a.doubt.update(a.assigned[mark:])


def analyse_isolated(a, command, depth, stdin=None, fed=False):
    """analyse_command on a body that runs in its own process, once per body and starting state: both readings of a line walk
    its substitutions, and a nested line must not double its work at every level.  `stdin` and `fed`: the standard input
    the body runs on (analyse_command), part of that state (SPD-210)."""
    key = (command, depth, stdin_text.reading_key(stdin, fed), a.reading_state())
    if key in a.isolated_done:
        return
    a.isolated_done.add(key)
    bodies, a.function_bodies = a.function_bodies, walk.bodies_copy(a.function_bodies)
    isolated(a, lambda: analyse_command(command, a, depth, stdin, fed))
    a.function_bodies = bodies  # a function the body defines stays in its process, and no call after it runs it (SPD-212)


def analyse_new_shell(a, command, depth, stdin=None, fed=False):
    """A body another shell process reads: a `-c` string, a here-document fed to a shell, the words `env -S` or `script -c`
    hand on.  An alias the line defined does not reach it (probed: `alias gp='git push'; eval 'sh -c gp'` ran
    nothing, while `eval 'echo $(gp)'` ran it, the substitution being parsed by the shell that holds the alias).  It runs
    on the standard input `stdin` its command hands it, and `fed` says whether anything stands there (SPD-210)."""
    state = (a.alias_scope, a.aliases, a.alias_unknown)
    a.alias_scope, a.aliases, a.alias_unknown = 0, {}, False
    try:
        analyse_isolated(a, command, depth, stdin, fed)
    finally:
        a.alias_scope, a.aliases, a.alias_unknown = state


def analyse_segment(tokens, bodies, a, depth, redirect_cwds=syntax._CURRENT, stdin=None, piped_fed=False):
    """A simple command: its output targets, then its words.  A target is resolved here, before analyse_words reads
    the command's own prefix assignments, because those reach neither the redirection nor the arguments in either shell
    (probed 2026-09-18: `S=$D/a; S=$D/b echo hi > $S.f` made a.f, and `S=$D/a echo hi > $S.f` with S unset made `.f`).
    `stdin` is the standard input a shell here would run: the text the pipeline element before this one printed and
    the command's own input redirections, as ShellWalk.finish read them (stdin_text.command_input); a.stdin holds it
    while the words are read and is put back after, so a body read in its own process reads its own input and not this
    one.  `piped_fed` says a pipe feeds this element at all, which with the same redirections says whether anything
    stands on that input (stdin_text.input_fed), text the hook can spell or not; a.stdin_fed carries it the same way,
    for an interpreter that runs the program it reads there (shell/inline_programs)."""
    words, targets = directories.separate_redirects(tokens)
    cwds = a.cwds if redirect_cwds is syntax._CURRENT else redirect_cwds
    settled = [arg_writes.resolved(t, a) for t in targets]
    # a target naming a for loop's variable, or a basename substitution, once per reading of it (SPD-146)
    for target in [t for ws in loop_bindings.resolved_words(targets, a) for t in ws] if targets else ():
        a.redirects.append((target, cwds))
    outer_stdin, a.stdin = a.stdin, stdin
    outer_fed, a.stdin_fed = a.stdin_fed, stdin_text.input_fed(tokens, bodies, piped_fed)
    try:
        analyse_words(words, bodies, a, depth, [globbing.GLOB_READING_BUDGET], "shell", False)
    finally:
        a.stdin, a.stdin_fed = outer_stdin, outer_fed
    if targets and words and all(assignment_words.assignment_word(w) for w in words):
        # An assignment-only command's own redirection is where the shells part: zsh opens it with the value the line had
        # before the command, bash with the one the command assigns (probed: `S=$D/a; S=$D/b > $S.f` made a.f in zsh and
        # b.f in bash).  Both readings are checked, as hidden_option checks both of sed's, so neither shell's
        # reading decides alone; where one of them stays raw it keeps the refusal an unresolvable target earns.
        for word, before in zip(targets, settled):
            after = arg_writes.resolved(word, a)
            if after != before:
                a.redirects.append((after, cwds))


def analyse_words(words, bodies, a, depth, budget, effect, prefixed, fresh=0):
    """A simple command's words, its redirections taken, read by dispatch_words.  A wrapper that moves the command
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
    (no spud call behind one is allowed).  A word the dispatch reads by name that the shell expands first (the command
    word, a wrapper's options and command, git's options, verb and the arguments git_refused reads, a shell's options, python's
    options and script, a spud call's arguments) is read as each word it can become (resolve_glob), an expansion in it
    before a glob (resolve_expansion).  `fresh`: the leading words an expansion in the command word gave, none of which
    the shell reads as an assignment or a reserved word, since it finds those before it expands -- and the same for the words a
    wrapper that execs its command word is handed (`nice x=./git push` runs git push, nice having exec'd `x=./git`).
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
    path_names = []  # the wrapper names this command runs, which the shell finds on PATH like the command word
    # `coproc` was read; no word has taken the command position away yet, so an alias the line defined is
    # still expanded here (a reserved word and an assignment keep it, a wrapper other than zsh's `time` does not).
    coproc, command_position = False, True
    # SPD-262: whether the shell still looks the next word up as a function, which outlasts the command position an alias
    # needs: True until a wrapper that resolves its word itself takes it (command, env, nice, ...; nocorrect and time
    # where the command position is gone, a program's name there), kept by zsh's noglob, exec and `-`, and "builtin"
    # after `builtin`, whose word is looked up as a builtin alone, so only a modifier builtin gives the lookup back
    # (syntax.FUNCTION_KEEP_MODIFIERS).  `looked_up`: the words the shell looked up as a function on the way, each
    # wrapper's own name among them (a function named nice runs in nice's place), for shadowed_name.
    seeks_function, looked_up = True, []
    input_appended = False  # an xargs this command runs under appends what it reads to the words (find_xargs)
    # The standard input the line gives this command, which a shell here runs as its commands, and the command
    # string an xargs makes of that input for the shell it runs (`echo 'git push' | xargs -0 sh -c`).  `fed`,
    # whether anything stands on that input at all, for an interpreter that runs the program it reads there.
    stdin, input_string, fed = a.stdin, None, a.stdin_fed
    # (that command string, whether xargs appends its input, the words it appends) for an xargs that runs this
    # command, which a tabled interpreter reads as its own options; None where no xargs runs it (interpreter_words)
    xargs_input = None
    while words:
        w = words[0]
        # `name[subscript]=value` too, read before any glob reading of its brackets
        m = None if fresh else assignment_words.assignment_word(w)
        reserved = not fresh and w in syntax.RESERVED_WORDS
        if not (m or reserved):
            a.doubt.update(prefix_names)  # a prefix assignment is the command's environment; the shell's variable keeps its value
            loop_bindings.forget(a, prefix_names)  # ... which no loop body's basename settles either (SPD-221)
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
                # a forked shell of the shell's own, not an external program: a builtin runs there, and its
                # directory changes still never reach the line
                effect = max(effect, "fork", key=directories.EFFECT_ORDER.get)
                prefixed = coproc = True
            words = words[1:]
        elif coproc and len(words) > 1 and syntax.IDENTIFIER_RE.match(w) and words[1] in syntax.COPROC_COMPOUND_WORDS:
            # bash 4 and later run `coproc NAME compound_command` in the forked shell, and the hook read NAME for the command
            # (probed in bash 5.2): the group after the name is read exactly as the unnamed form's is.  A name the
            # hook cannot resolve reaches this as the expansion it is and refuses a member; `coproc NAME echo x` is a simple
            # command named NAME in every shell, and is left as it was.
            # `fresh` goes back to 0: an expansion that gave the name gave none of the words after it, and the shell read the
            # group's opener as the reserved word it is before it expanded anything (`N=NAME; coproc $N { git push; }`)
            words, coproc, fresh = words[1:], False, 0
        elif m:
            record_assignment(a, m)
            prefix_names.append(m[0])
            if effect != "shell":
                loop_bindings.forget(a, prefix_names)  # behind coproc or zsh's `-`: it may not assign the shell's (SPD-221)
            words = words[1:]
        elif w == "-":
            effect = max(effect, "either", key=directories.EFFECT_ORDER.get)  # zsh's `-` precommand modifier; bash finds no `-`
            prefixed = True
            command_position = False
            seeks_function = seeks_function is not False  # a builtin, and the function lookup goes on after it
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
            # zsh's `time` and `nocorrect` where the command position holds are reserved words, never looked up; every
            # other wrapper's name is looked up as the command word is, an alias where the command position holds and a
            # function where the lookup does, and one the shell holds runs in the wrapper's place (SPD-262)
            reserved = command_position and w in zsh.ZSH_COMMAND_POSITION_WORDS
            if seeks_function is True and not reserved:
                looked_up.append(w)
            if (command_position or seeks_function is True) and held_text.read_shell_name(
                    words, a, depth, stdin, fed, effect, aliased=command_position,
                    function=seeks_function is True and not reserved):
                return  # an alias the shell holds under the wrapper's name took the command
            if seeks_function is not False and (w == "builtin" or w in syntax.FUNCTION_KEEP_MODIFIERS):
                seeks_function = "builtin" if w == "builtin" else True
            elif not (seeks_function is True and reserved):
                seeks_function = False
            if os.path.basename(w).casefold() == "xargs":
                # What xargs reads from its input stands where -J or -I puts it, or after the words it runs
                rest, appended = find_xargs.xargs_input(words, consumed, rest)
                input_appended = input_appended or appended
                # xargs reads that input itself, so the command it runs does not; where the line spells it, it
                # is the command string a shell run with a `-c` and no string is handed
                # ... and xargs gives the command it runs no standard input of its own (never an inline program)
                # An interpreter it runs is handed every word of that input instead of one string
                # ... and an input zsh and bash read apart (SPD-209) is one the line does not spell for either
                whole = stdin_text.single(stdin)
                xargs_input = (stdin_text.xargs_string(words, consumed, appended, whole), appended,
                               stdin_text.xargs_words(words, consumed, whole))
                input_string, stdin, fed = xargs_input[0], None, False
            if chdir is not None:
                # Everything the wrapper runs -- its words, a string it hands a shell, a nested wrapper -- starts in the
                # directory it moved to, once its own words are read (a glob or an expansion there leaves it unknown)
                a.cwds = directories.wrapped_directories(chdir, rest, a)
                moved.append(chdir)
            for aname, avalue in env_assignments:
                script_files.read_assignment(a, aname)  # `env BASH_ENV=x ...`: a file of commands a shell under it runs
                script_runners.read_runner_assignment(a, aname)  # `env npm_config_script_shell=x npm test`: a runner's shell
                if aname.startswith(assignment_words.ENV_FUNCTION_PREFIX):
                    a.findings.append(("env-function", prepare.deglob(aname)))  # a function bash and sh import
                # `env GIT_CONFIG_*/HOME/GIT_PAGER/GIT_SSH_COMMAND/GIT_DIR/PATH/GIT_TRACE=... git ...`
                if syntax.IDENTIFIER_RE.match(aname) and (git_programs.is_git_config_var(aname) or git_programs.is_git_program_var(aname)
                        or git_programs.is_git_repo_var(aname) or git_programs.is_path_var(aname)
                        or git_programs.is_git_write_var(aname)):
                    a.vars[aname] = avalue
                    a.doubt.add(aname)  # the command's environment, not the shell's
            path_names.append(w)
            script_files.read_path_word(a, w)  # `./env git status` runs whatever ./env holds, not env
            prefixed = True
            # only zsh's `time` and `nocorrect`, where the command position holds, keep it for an alias (probed: `eval
            # 'time gp'` ran the alias, `eval 'command gp'` and `eval 'env gp'` ran nothing, and `noglob time gp` ran
            # /usr/bin/time, SPD-262)
            shell_modifier = command_position = reserved
            effect = max(effect, directories.prefix_effect(w, words[1] if len(words) > 1 else None), key=directories.EFFECT_ORDER.get)
            # env and sudo read NAME=value as an assignment of their own, and the shell reads one after its own `time`
            # or `nocorrect`, which keep the command position; every other wrapper -- and `time` anywhere but in the command
            # position, where it is /usr/bin/time (probed) -- execs its first remaining word whatever it looks like, so none of
            # the words it is handed is an assignment or a reserved word, which is what `fresh` says.
            words = rest
            fresh = 0 if (shell_modifier or os.path.basename(w).casefold() in syntax.WRAPPER_TAKES_ASSIGNMENTS) else len(rest)
            for s in strings:
                # a shell reads the string with its own quotes, on the command's standard input (SPD-210)
                analyse_new_shell(a, prepare.deglob(s), depth + 1, stdin, fed)
        else:
            break
    if not words:
        return
    cmd = words[0]
    if syntax.unknown_operand(cmd):
        # The program is what xargs reads from its input (`xargs -J % % x`), or a file find found (`-exec {}`)
        a.kinds.append("other")
        a.findings.append(("var", syntax.shown_operands(prepare.deglob(cmd))))
        return
    # An input xargs appends is operands the line does not spell, read where a command writes by argument
    unspelled = [syntax.INPUT_OPERAND] * 2 if input_appended else []
    if cmd.startswith(assignment_words.ENV_FUNCTION_PREFIX) and "=" in cmd:
        # `BASH_FUNC_<name>%%=...` is no assignment to a shell, but sudo reads it as one, and env reads it so
        # wherever strip_wrapper did not: refused as the environment it spells, whatever takes it
        a.findings.append(("env-function", prepare.deglob(cmd.partition("=")[0])))
    if a.alias_scope and command_position:
        # Inside `eval`, a command word the line aliased runs the alias's body, not a command of its own.  The body
        # is read as the shell text it is, with its own quotes, as eval's rejoined words are, and the words after it as
        # eval's text spells them, quotes and all: the shell parses them after the body (SPD-201, prepare.requoted).
        body, doubtful = expansions.alias_substitution(cmd, a)
        if body is not None or doubtful:
            if body is not None:
                rest = " ".join(prepare.requoted(w) for w in words[1:])
                before = a.cwds
                analyse_command(body + (" " + rest if rest else ""), a, depth + 1, stdin, fed)
                a.cwds = directories.settle(effect, before, a.cwds)  # its cd, where the command runs (SPD-252)
            else:
                a.kinds.append("other")
            if doubtful:  # after the body, so a refusal the body itself earns keeps its own reason
                a.findings.append(("alias", prepare.deglob(cmd)))
            return
    if seeks_function is True:
        looked_up.append(cmd)
    if (command_position or seeks_function is True) and held_text.read_shell_name(
            words, a, depth, stdin, fed, effect, aliased=command_position, function=seeks_function is True):
        # The shell this line runs in already defines the command word as an alias, whose body took the command; a
        # function it defines is read behind noglob, exec and `-` too, where no alias is expanded (SPD-262)
        return
    if cmd in syntax.ASSIGNING_COMMANDS and directories.builtin_runs(effect):
        # `read X`, `printf -v X`, `unset X`, `getopts o X`, `let X=1`: the line's own assignments, each builtin's names read
        # by its grammar (SPD-254, SPD-225); a builtin's program run by its path or behind env assigns nothing here
        expansions.read_assigning_builtin(words, a, effect)
    options = cmd in ("setopt", "unsetopt", "emulate") or cmd == "set" and any(prepare.deglob(w)[:2] in ("-o", "+o") for w in words[1:])
    if options:
        a.arith_opaque = True  # an option may change how arithmetic reads a number (SPD-225, ShellAnalysis.arith_opaque)
    if options or cmd == "shopt":
        a.cdable = True  # ... or turn on CDABLE_VARS (bash's cdable_vars), for a cd after it (SPD-252, ShellAnalysis.cdable)
    if options or cmd == "set" and any(syntax.PHYSICAL_FLAG_RE.match(prepare.deglob(w)) for w in words[1:]):
        a.chase = "either"  # ... or CHASE_LINKS (bash's `set -P`), or turn one off (SPD-263, ShellAnalysis.chase)
    if cmd in ("source", ".", "trap"):
        a.all_doubt = True  # code the hook does not read may assign any variable
    if cmd in ("source", "."):
        # the file's commands run in this shell, whatever runs the builtin (shell/script_files)
        script_files.record_script(a, "source", cmd, words[1] if len(words) > 1 else None)
    base = os.path.basename(cmd).casefold()
    launcher = "/" in cmd and spud_calls.any_spud_launcher(cmd, a.cwds)
    if base != "spud" and launcher:
        base = "spud"  # a symlink to bin/spud run by its path, whatever its own name
    if not launcher:
        script_files.read_path_word(a, cmd)  # a file run by its path, never a program found on PATH
    if base in runtime_shells.RUNNERS:
        # A runtime's or a package manager's subcommand that hands a shell its text (`bun exec`, `npm exec -c`, `deno task
        # --eval`, ...) or runs its words as a command (`npx --package=x -- git push`): read where an `sh -c` string and a
        # wrapper's command are, before the dispatch below reads the run for what it is itself (shell/runtime_shells)
        if not read_points(lambda ws, start: expansions.option_point(runtime_shells.runner_read_index(base, ws, start))):
            return
        for form, body in runtime_shells.runner_readings(base, words + unspelled):
            if form == "words":
                analyse_words(body, bodies, a, depth, budget, "process", True, len(body))
            elif form == "text" and syntax.INPUT_OPERAND in body and input_string is None:
                a.findings.append(("var", runtime_shells.runner_shown(words + unspelled)))  # text an xargs reads, unspelled
            elif form == "text":
                analyse_new_shell(a, body.replace(syntax.INPUT_OPERAND, input_string or ""), depth + 1, stdin, fed)
            else:
                a.findings.append(("var", body))
    if base in script_runners.RUNNER_BASES:
        # A script runner runs commands a project file holds (`npm run <name>`, `deno task <name>`, `make <target>`),
        # refused a member in bash_rule unless its project allows every name it runs (shell/script_runners)
        script_runners.read_runner(base, words + unspelled, a)
    if base == "git":
        if not read_points(expansions.git_read_point):
            return
        a.kinds.append("git")
        # every reading below takes the words an xargs appends as well (SPD-230): its input may be git's verb, an option,
        # a file mailinfo or mailsplit writes, or an operand branch, tag or config reads
        git_words = words + unspelled
        alias = git_programs.git_line_defines_alias(git_words) or git_programs.git_env_defines_alias(a.vars)
        if alias is not None:  # a defined alias/include or GIT_CONFIG_* injection: the verb the hook reads is not what runs
            a.findings.append(("git-config", alias))
        else:
            program = (git_programs.git_line_names_program(git_words) or git_programs.git_env_names_program(a.vars)
                       or git_programs.git_verb_names_program(git_words))
            if program is not None:  # config, environment or a verb option names a program git runs under an allowed verb
                a.findings.append(("git-program", program))
            else:
                verb, args = git_verbs.git_verb(git_words)
                refused = git_verbs.git_refused(verb, args)
                unknown = None if refused else git_verbs.git_unknown_verb(verb, a.home)
                if unknown is not None:  # not one of git's own commands: an alias or an external git-<verb>
                    a.findings.append(("git-verb", unknown))
                else:  # one of git's own: Law 7's table first, then its allowlist, which refuses every other name
                    a.findings.append(("git", (verb, refused or git_verbs.git_not_allowed(verb))))
        if git_verbs.git_unspelled_word(git_words) is not None:
            # a word xargs reads from its input where git reads its verb or an option, or find's {} as the verb: refused
            # a member after every reason the spelled words earn (spud_calls.FINDING_LAST)
            a.findings.append(("git-input", syntax.shown_operands(prepare.deglob(" ".join(git_words)))))
        targets = git_verbs.git_repo_targets(git_words, a.vars)
        for spelled, target in targets:
            # another repository, whose .git/config the hook cannot read: resolved against the checkouts in bash_reason
            a.findings.append(("git-repo", (spelled, target, a.cwds)))
        # ... and the repository this call does read, whose local and worktree scopes bash_reason holds to the allowlist
        a.git_calls.append((tuple(targets), a.cwds))
        work_trees = [target for spelled, target in targets if git_verbs.git_target_kind(spelled) == "worktree"]
        # git is handed the value, not the spelling, so a `$NAME` the line settled is resolved in the option that
        # names the file and in the variable whose value decides whether git writes a file at all (a GIT_TRACE* sibling
        # traces to a path only when its value is absolute; a descriptor or a relative one writes nothing).
        for spelled, target, top in git_writes.git_write_targets(git_words, {n: arg_writes.resolved(v, a) for n, v in a.vars.items()}):
            # A file the call writes through one of its own options or the environment, held to the path rule
            # in bash_reason like a redirection target, for every caller; one git reads from the work tree's top
            # at each place that may be (SPD-227)
            target = arg_writes.resolved(target, a)
            paths = [target] if top is None else git_writes.top_readings(target, arg_writes.resolved(top, a),
                                                                         [arg_writes.resolved(w, a) for w in work_trees], a.cwds)
            a.git_writes.extend((spelled, path, a.cwds) for path in paths)
    elif base in syntax.SHELLS:
        if not read_points(lambda ws, start: expansions.option_point(expansions.shell_read_index(ws, start))):
            return
        a.kinds.append("shell")
        i, dash_c, string = 1, False, None
        while i < len(words):
            w = words[i]
            if w.startswith("-") and "c" in w[1:] and not w.startswith("--"):
                dash_c = True
                string = prepare.deglob(words[i + 1]) if i + 1 < len(words) else None
                if input_string is not None:
                    # An xargs hands the shell the input the line spells, where its -I or -J replstr stands in
                    # the string (`xargs -I% sh -c 'rm %'`) or as the whole string (`xargs -0 sh -c`)
                    string = input_string if string is None else string.replace(syntax.INPUT_OPERAND, input_string)
                if string is not None:
                    if unread.escaped_substitution(string):
                        unread.record_unread(a, "escaped-subst", unread.escaped_shown(string))  # SPD-196
                    # read with its own quotes, `sh -c 'g?t push'`, and on the shell's standard input, which its commands
                    # read where nothing of their own replaces it: `sh -c sh < x.sh` runs x.sh (SPD-210, CompoundInputTest)
                    analyse_new_shell(a, string, depth + 1, stdin, fed)
                break
            if not w.startswith("-"):
                break
            i += 1
        for body in bodies:
            analyse_new_shell(a, body, depth + 1, stdin, fed)
        if not dash_c and stdin_text.reads_commands(words):
            # With no -c string and no script of its own the shell runs what it reads on standard input, and
            # the line spells that text: `echo 'git push' | sh`, `bash -s <<< 'git push'`, `cat <<'EOF' | sh` -- zsh's
            # reading and bash's, where a command's inputs make them differ (stdin_text.MultiosText, SPD-209).  A text
            # that is one body alone was read above.
            fed_bodies = {body + "\n" for body in bodies}
            for text in stdin_text.each_reading(stdin):
                if text not in fed_bodies:
                    analyse_new_shell(a, text, depth + 1, stdin, fed)
        # ... and every shell whose commands come from a file: a script operand, standard input the line does not spell,
        # an xargs string from such input, a HOME of the line's own (shell/script_files, refused a member in bash_rule)
        script_files.read_shell(words, a, dash_c, string, xargs_input, stdin, fed)
    elif base == "eval":
        a.kinds.append("eval")
        before = a.cwds
        a.alias_scope += 1  # an alias the line defined is expanded where eval parses its words again
        eval_text = prepare.deglob(" ".join(words[1:]))
        if unread.escaped_substitution(eval_text):
            unread.record_unread(a, "escaped-subst", unread.escaped_shown(eval_text))  # SPD-196
        try:
            # eval reads its words again, their quotes gone, and runs them on its own standard input (SPD-210)
            analyse_command(eval_text, a, depth + 1, stdin, fed)
        finally:
            a.alias_scope -= 1
        a.cwds = directories.settle(effect, before, a.cwds)
    elif cmd in ("source", ".") and directories.builtin_runs_here(effect):
        a.kinds.append("other")
        a.cwds = None  # the file may change directory anywhere
        a.dir_moves += 1
    elif cmd == "trap" and directories.builtin_runs(effect):  # the builtin, spelled exactly: env trap and /usr/bin/trap set no trap
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
            # After the database and the launcher, which keep their reasons: a program the line spells rather
            # than reads from a file (`-c`, or standard input under `-` or no script) is one the hook cannot read at all
            a.kinds.append("other")
            interpreter_words.read_run(cmd, base, words, a, fed, xargs_input)
    elif base in syntax.JS_RUNTIMES:
        # Its options and its program read by name first, as python's are above (interpreter_words.read_point)
        if not read_points(lambda ws, start: interpreter_words.read_point(base, ws, start)):
            return
        if any("sqlite" in w.lower() for w in words[1:]) or any("sqlite" in b.lower() for b in bodies):
            a.kinds.append("db")
            a.findings.append(("db", cmd))
        else:
            a.kinds.append("other")
            # -e, --eval, -p, --print, or standard input; `deno eval`, and an option out of xargs's input
            interpreter_words.read_run(cmd, base, words, a, fed, xargs_input)
    elif base == "spud":
        if not read_points(lambda ws, start: expansions.option_point(expansions.first_read_index(ws, start))):
            return
        a.kinds.append("spud")
        call = spud_calls.parse_spud_call(words[1:])
        call["vouched"] = False  # its #! line runs python3.14 with neither -I nor -S
        a.findings.append(("spud", call))
    elif base == "tee":
        a.kinds.append("tee")
        # the word as spelled decides whether tee reads it as an option, as this Mac's getopt does; what it names is
        # the value the line settled, which is the file tee opens -- once per reading of a loop's word or a basename (SPD-146);
        # the words an xargs appends are files tee writes too (SPD-248)
        files = [w for w in (words + unspelled)[1:] if not w.startswith("-")]
        for target in [t for ws in loop_bindings.resolved_words(files, a) for t in ws] if files else ():
            a.redirects.append((target, a.cwds))
    elif base in syntax.ARG_WRITE_COMMANDS:
        # A command that writes the files it names as operands, read where tee is, so bash_reason holds each to
        # the path rule as it holds a redirection target.  The words this Mac's getopt reads as options are read by name
        # first, so a glob among them is read as each option it can become (`sed -? '' s/a/b/ f` is `sed -i`).
        if not read_points(lambda ws, start: expansions.option_point(arg_writes.option_read_index(base, ws, start, a))):
            return
        a.kinds.append("other")
        arg_writes.read_writes(prepare.deglob(cmd), base, words + unspelled, a)
        if base in syntax.SCRIPT_COMMANDS:  # sed's own script, beside the files its -i names
            script_text.read_script(prepare.deglob(cmd), base, words + unspelled, a, depth)
    elif base in syntax.SCRIPT_COMMANDS:
        # awk, whose program names the files it writes and the commands it hands /bin/sh; each file is held to
        # the path rule where tee's operand is, and each command read where an `sh -c` string is
        a.kinds.append("other")
        script_text.read_script(prepare.deglob(cmd), base, words + unspelled, a, depth)
    elif base in syntax.TREE_WRITE_COMMANDS:
        # A command whose files the line does not spell -- what find deletes and runs, an archive extracted, a
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
        # A command that writes a file it names past ARG_WRITE_COMMANDS -- dd's of=, sort's -o, mktemp's templates,
        # split's pieces, perl -i -- read where tee is, each held to the path rule as a redirection target
        # perl's own options and program read by name (read_point finds nothing for dd, sort, mktemp and split,
        # which the table does not name)
        if not read_points(lambda ws, start: interpreter_words.read_point(base, ws, start)):
            return
        a.kinds.append("other")
        spelled_writes.read_spelled_writes(prepare.deglob(cmd), base, words + unspelled, a, depth)
        # perl's -e and -E, and the program it reads on standard input; dd, sort, mktemp and split run none
        interpreter_words.read_run(cmd, base, words, a, fed, xargs_input)
    elif inline_programs.interpreter(base) is not None:
        # ruby -e and its standard input, the one tabled interpreter with no reading of its own here.
        # And the families the table gained, none of which the analysis reads anywhere else -- osascript (whose
        # `do shell script` is any shell command at all), php, lua, Rscript, swift, tsx and ts-node.
        if not read_points(lambda ws, start: interpreter_words.read_point(base, ws, start)):
            return
        a.kinds.append("other")
        interpreter_words.read_run(cmd, base, words, a, fed, xargs_input)
    elif cmd in ("alias", "unalias") and directories.builtin_runs(effect):
        # The builtin, spelled exactly, stores text the shell runs wherever it next parses this name in command
        # position -- which on one line means `eval`.  Never a spud call, so an aliasing line is not allowed on its own.
        a.kinds.append("other")
        if cmd == "alias":
            expansions.record_alias_line(words, a)
        else:
            expansions.clear_alias_line(words, a)
    elif cmd == "hash" and directories.builtin_runs(effect):
        # The builtin, spelled exactly, puts a file of the line's own choosing in the shell's command table, so a
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
        if a.body_locals is not None and effect == "shell" and not a.unsure:
            scope = a.body_locals[-1]  # a function body the shell holds, where it surely runs: its names are local (SPD-246)
            for name in assignment_words.local_names(words):
                scope.setdefault(name, a.vars.get(name, syntax.UNSET))
        a.typed.update(assignment_words.typed_names(words))  # `declare -i X`: X's assignments are arithmetic (SPD-225)
        for w in words[1:]:
            m = assignment_words.declaration_word(w)
            if m:
                record_assignment(a, m)
            elif w.startswith(assignment_words.ENV_FUNCTION_PREFIX):  # `export 'BASH_FUNC_git%%=...'`, `export BASH_FUNC_x`
                a.findings.append(("env-function", prepare.deglob(w.partition("=")[0])))
        a.findings.extend(("env-function", name) for name in exported_function_names(words))
        if any(w.startswith(("-", "+")) for w in words[1:]):
            for w in words[1:]:  # an attribute (`declare -n X=Y`, `typeset -i`, `local -a`) changes what the name reads
                names = syntax._NAME_RE.findall(prepare.deglob(w))
                a.doubt.update(names)
                loop_bindings.unbind(a, names)
    else:
        a.kinds.append("other")  # CD, /usr/bin/cd, env cd: /usr/bin/cd in its own process, and the shell stays
    shadowed_name(a, cmd, path_names, looked_up)  # after the dispatch, so a refusal the words as spelled earn keeps its own reason


def record_assignment(a, found):
    """Record a word the shell reads as an assignment, found = (name, subscript or None, whether it appends, value) from
    assignment_words.  The variable is assigned where the shell runs it; a subscripted one changes part of its
    value, which the hook does not compute, so its value is unknown as an appended one's is, and it counts for every rule
    that reads the variable as a plain assignment does -- PATH and zsh's `path` for shadowed_name, CDPATH, GIT_*.
    An element of zsh's `functions`, `commands` or `aliases` binds the name it keys as a definition, a `hash` or an `alias`
    line would, a name the hook cannot read standing for all of them; and a BASH_FUNC_ variable is refused
    outright.

    SPD-225: a subscript is arithmetic, which the shells evaluate before they assign (`arr[X=1]=q` and `declare
    arr2[X=1]=q` assigned X in zsh 5.9 and bash 3.2.57, probed), and so is the value a name with the integer or float
    attribute is assigned (a.typed: `typeset -i X; X='T=12'` set T as well), whose own value the hook does not compute."""
    name, subscript, append, value = found
    if subscript is not None:
        expansions.read_arithmetic(a, prepare.deglob(subscript), doubtful=True)
    script_files.read_assignment(a, name)  # BASH_ENV, ENV, ZDOTDIR: a file of commands a shell started later runs
    script_runners.read_runner_assignment(a, name)  # npm_config_*, MAKEFLAGS ...: a runner's configuration, its shell among it
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
    if name in a.typed:
        expansions.read_arithmetic(a, prepare.deglob(value), doubtful=bool(append or subscript is not None))
        expansions.assign_unknown(a, name)
    else:
        expansions.assign_variable(a, name, value, append or subscript is not None)


def exported_function_names(words):
    """The variables `export -f <name>` and `declare -fx <name>` (typeset, local) put in the environment of every program
    bash starts later, BASH_FUNC_<name>%%, for a name the hook reads: a bash or sh started under any of them
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
    """The command names a `hash` line puts in the shell's own table: every operand's name, `<name>` after bash's
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


def shadowed_name(a, cmd, path_names, looked_up):
    """Record that the shell would not run the program the hook read by name: the line bound the name to
    a shell function, assigned PATH (or zsh's `path`, tied to it) so the shell searches a directory of the line's own
    choosing, or hashed the name to a file of its own.  Only the names the hook reads count (git, spud, python3.14, sqlite3,
    tee, a shell, a wrapper): for any other name the hook grants nothing, so replacing its program takes a member no further
    than running a program of its own.  A command run by a path is not looked for on PATH, and GIT_EXEC_PATH keeps the
    reason git's program check gives it.

    A function shadows each word the shell looks up as one, `looked_up` (dispatch_words): the command word `cmd` unless a
    wrapper that resolves its own word (`command`, `builtin` alone, `env`, `nice`, ...) took the lookup away, and each
    wrapper's own name on the way, up to the first such resolving wrapper, which a function named for it shadows in turn;
    zsh's `noglob`, `exec` and `-`, `builtin` naming one of them, and `nocorrect` and `time` where the command position
    holds keep the lookup for the word after them (probed in zsh 5.9 and bash 3.2, SPD-262: tests/test_hooks_snapshots.py
    ModifierLookupTest).  A PATH or a hash, in contrast, decides the lookup of every one of these names, so both are read
    for the command word and the wrappers alike."""
    for word in looked_up:
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
