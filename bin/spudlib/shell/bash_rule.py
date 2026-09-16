"""shell/bash_rule: bash_reason: the Bash hook's rule over a line.  Moved from bin/spud_ledger.py (SPD-065)."""

import os

from . import analyse, git_config, prepare, redirect_globs, spud_calls, syntax
from ..hooks import hookio, pathrule, worktrees
from ..state import lookup


def git_target_dirs(target, cwds):
    """(the directories a git call's repository target may name, whether the hook cannot resolve it): a path relative to a
    directory it cannot follow, `~-`/`~+`/`~name`, or a value holding an expansion is unresolvable (SPD-047)."""
    path = prepare.deglob(target)
    if spud_calls.unresolvable_word(path):
        return [], True
    if path.startswith("~"):
        if path != "~" and not path.startswith("~/"):
            return [], True  # ~- and ~+ are OLDPWD and the current directory, ~name another user or a zsh named directory
        return [os.path.expanduser(path)], False
    if os.path.isabs(path):
        return [path], False
    if cwds is None:
        return [], True
    return [os.path.join(c, path) for c in sorted(cwds)], False


def git_repo_outside(ctx, con, target, cwds):
    """Where a git call's repository target lands (SPD-047): (the first directory it may name that lies outside every checkout
    the ledger knows, False), (None, True) when the hook cannot resolve it, else (None, None).  Inside is a registered
    project's root or one of the worktrees git names for it, read as the path rule reads a file's path."""
    candidates, unresolved = git_target_dirs(target, cwds)
    if unresolved:
        return None, True
    for candidate in candidates:
        if not worktrees.project_paths(ctx, con, candidate, None):
            return os.path.normpath(candidate), False
    return None, None


def bash_reason(ctx, con, caller_agent_id, caller_member, command, cwd, mode="spud"):
    """(reason or None, analysis) for a Bash command line.  In a session that is not Spud (`mode` plain, SPD-014) a caller
    with no agent_id, or an unbound one (Eric's own subagents), keeps the database, `spud hook`, `--as spud` and member-own
    refusals and the path rule, and gets no Law 7 refusal; a bound member gets every refusal, in any session."""
    db_reason = hookio.DB_REASON
    if hookio.DB_PATH_RE.search(command):
        return db_reason % "the command names ledger.db or .spud/", None
    analysis = analyse.analyse_command(command, syntax.ShellAnalysis(cwd=cwd, home=str(ctx.home)))
    if analysis.unparseable:
        return None, analysis
    plain = mode == "plain"
    strict = bool(caller_agent_id) and not (plain and caller_member is None)
    who = ("%s (agent_id %s)" % (lookup.member_ref(con, caller_member["id"]), caller_agent_id)) if caller_member else ("agent_id %s" % caller_agent_id if caller_agent_id else "Spud")
    # An expansion the hook cannot resolve in a word it reads by name (SPD-043) refuses a member last, and a verb outside git's
    # own commands or a repository it cannot read (SPD-047) second to last, so a refusal the words as spelled already earn
    # (Law 7's verb, a program config key, an ambiguous glob, Law 6, an actor) keeps its own reason.
    for kind, detail in sorted(analysis.findings, key=lambda f: spud_calls.FINDING_LAST.get(f[0], 0)):
        if kind == "db":
            return db_reason % ("`%s`" % detail), analysis
        if kind == "spud" and detail["command"] == "hook":
            return "`spud hook` is the harness's: hooks run from .claude/settings.json (spud settings sync), never from Bash", analysis
        if not strict:
            if kind == "spud" and detail["actor"] not in (None, "spud") and (detail["command"], detail["subcommand"]) in hookio.MEMBER_OWN_COMMANDS:
                return ("Law 5: `--as %s` from Spud's own session would write a member's own sections (log, result, block, proposals) in its name;"
                        " members record themselves (the SubagentStop hold sees to it), Spud records verdicts with `spud --as spud member finish`" % prepare.deglob(detail["actor"])), analysis
            if plain and kind == "spud" and detail["actor"] == "spud" and spud_calls.spud_call_writes(detail) and (detail["command"], detail["subcommand"]) != ("session", "claim"):
                what = " ".join(w for w in (detail["command"], detail["subcommand"]) if w)
                return ("Law 6: this session is not Spud; /spud claims it. `spud --as spud %s` writes the ledger, and outside Spud's home a session"
                        " is Spud only after `spud --as spud session claim` (the /spud skill runs it)" % what), analysis
            continue
        if kind == "git":
            verb, refused = detail
            if refused:
                return ("Law 7: spudagents never run `git %s` (commit, add, stash, checkout, switch, rebase, reset, push, merge, cherry-pick,"
                        " worktree, branch, tag, pull, apply, restore, rm, mv, clean ...); Spud commits, after the outcome is recorded" % verb), analysis
        elif kind == "git-config":
            return ("Law 7: this git call takes config the hook cannot read (%s): an alias or include defined on the line, or a variable"
                    " that injects config or points git at a config file of its own (HOME and XDG_CONFIG_HOME move git's global config to"
                    " <dir>/.gitconfig or <dir>/git/config). git expands an alias into whatever command it names before it dispatches, and"
                    " such a file can also name a program git runs, so a write can run under a verb Law 7's table does not list. Run git"
                    " with no `-c`/`--config-env` alias or include and no GIT_CONFIG_*, HOME or XDG_CONFIG_HOME variable; Spud commits,"
                    " after the outcome is recorded" % detail), analysis
        elif kind == "git-verb":
            how, verb = detail
            if how == "unreadable":
                return ("Law 7: the hook cannot read git's own command list (`git --list-cmds=main`), so it cannot tell whether `git %s` is"
                        " one of git's commands or an alias from a config file it cannot read, which git would expand into whatever command"
                        " it names before it dispatches; the hook fails closed. Spud commits, after the outcome is recorded" % verb), analysis
            return ("Law 7: `git %s` is not one of git's own commands (`git --list-cmds=main`), so it is an alias from a config file the hook"
                    " cannot read (another repository's .git/config, ~/.gitconfig, $XDG_CONFIG_HOME/git/config, the system config, an include)"
                    " or an external `git-%s` program on PATH; git expands an alias into whatever command it names before it dispatches, so a"
                    " write can run under a verb Law 7's table does not list. A member runs only git's own read verbs (status, log, diff, show,"
                    " rev-parse, ls-files ...); Spud commits, after the outcome is recorded" % (verb, verb)), analysis
        elif kind == "git-repo":
            spelled, target, target_cwds = detail
            outside, unresolved = git_repo_outside(ctx, con, target, target_cwds)
            if unresolved:
                return ("Law 7: this git call points git at a repository the hook cannot resolve (%s): a path relative to a directory it"
                        " cannot follow, or a value it cannot read. git reads that repository's .git/config before it dispatches -- aliases"
                        " and the keys that name a program it runs -- so a write can run under a verb Law 7's table does not list. Use an"
                        " absolute path inside the session's own checkout; Spud commits, after the outcome is recorded" % prepare.deglob(spelled)), analysis
            if outside is not None:
                return ("Law 7: this git call points git at %s (%s), outside every checkout the ledger knows (a registered project's root or"
                        " one of its worktrees). git reads that repository's .git/config before it dispatches -- aliases and the keys that"
                        " name a program it runs -- and a member can write such a file under its own deliverables, so a write can run under a"
                        " verb Law 7's table does not list. Run git in the session's own checkout; Spud commits, after the outcome is"
                        " recorded" % (outside, prepare.deglob(spelled))), analysis
        elif kind == "git-program":
            return ("Law 7: this git call runs a program git never checks (%s); git config, the environment and some options can name or"
                    " enable a program git runs -- a pager, editor, ssh or proxy command, diff or merge driver, hooks or exec path, credential"
                    " or askpass helper, the ext:: transport, --exec-path, or a verb option like --upload-pack -- under a verb Law 7's table"
                    " allows. A member may set only inert `-c`/`--config-env` keys (color.*, advice.*, i18n.*, core.quotepath, log.date,"
                    " safe.directory, and core.pager/pager.<cmd>=cat), no program-naming environment variable, and no such option; Spud"
                    " commits, after the outcome is recorded" % detail), analysis
        elif kind == "path":
            var, name = detail
            return ("Law 7: this line assigns %s and then runs `%s` by name, so the shell looks for it in a directory the"
                    " line chose and the program the hook checked is not the one that would run: a `%s` of the member's own"
                    " can do everything the name it borrows is allowed to do (zsh's `path` array is PATH under another"
                    " name). Leave PATH as the session has it, or spell the program's path out (/usr/bin/git, bin/spud),"
                    " which the shell does not search PATH for; Spud commits, after the outcome is recorded" % (var, name, name)), analysis
        elif kind == "hashed":
            return ("Law 7: this line hashes `%s` into the shell's own command table (`hash -p <path> <name>` in bash,"
                    " `hash <name>=<path>` in zsh), so a later bare `%s` runs that file whatever PATH holds and the program"
                    " the hook checked is not the one that would run. Hash none of the names the hook reads (git, spud,"
                    " python3.14, sqlite3, tee, a shell, a wrapper), or spell the program's path out; Spud commits, after"
                    " the outcome is recorded" % (detail, detail)), analysis
        elif kind == "var":
            return "the command word %s comes from a variable or a substitution the hook cannot resolve; spell the command out" % detail, analysis
        elif kind == "var-word":
            return ("the word %s holds a parameter expansion, arithmetic or a substitution the hook cannot resolve, where the command is"
                    " read by name (a wrapper's options, git's options, verb and the arguments it checks, a shell's or python's options and"
                    " script, a spud call's words); spell the words out" % detail), analysis
        elif kind == "var-doubt":
            return ("the variable %s may not hold the value this line assigned it (the assignment may not run or does not persist: a"
                    " condition, a compound command, a loop or function body, a pipeline, a background job, a subshell or substitution,"
                    " a command's prefix, or a builtin that assigns it), so the hook cannot resolve the words it becomes; spell them out"
                    % detail), analysis
        elif kind == "alias":
            return ("`eval` runs the command word %s, which this line defines as an alias the hook cannot resolve: its body holds"
                    " an expansion or a substitution, or the definition or an `unalias` may not have run (a branch, a subshell, a"
                    " pipeline, a background list, a loop or function body, a reading only one shell makes). A shell expands an"
                    " alias when it parses the text, so the command that runs is not the one written; spell the command out, or"
                    " define no alias on the line" % detail), analysis
        elif kind == "glob":
            return ("the word %s is a glob the shell expands before it runs the command, and it can become more than one command,"
                    " option or verb the hook checks at once, or more than the hook reads; spell the words out" % detail), analysis
        elif kind == "spud":
            call = detail
            if call["actor"] == "spud":
                return "Law 6: `--as spud` is Spud's; a spudagent acts as itself (--as %s)" % (caller_agent_id,), analysis
            if call["command"] in hookio.SPUD_ONLY_COMMANDS or (call["command"], call["subcommand"]) in hookio.SPUD_ONLY_SUBCOMMANDS:
                what = call["command"] + ((" " + call["subcommand"]) if call["subcommand"] and (call["command"], call["subcommand"]) in hookio.SPUD_ONLY_SUBCOMMANDS else "")
                return ("Law 6: `spud %s` is Spud's (tickets are created, moved and edited by Spud alone; init, migrate, import, render, backup,"
                        " settings sync, config sync and member resum are ledger-wide, and schedule installs the ledger's daily backup on this Mac);"
                        " file a proposal instead: spud proposal file --as %s" % (what, caller_agent_id)), analysis
            if call["actor"] is not None:
                if caller_member is None:
                    return ("your agent_id %s is not bound to a member yet, so `--as %s` cannot be verified (a background spawn is bound right"
                            " after launch by PostToolUse(Agent); a foreground spawn only at its stop)" % (caller_agent_id, call["actor"])), analysis
                if not spud_calls.actor_is_self(con, call["actor"], caller_member, caller_agent_id):
                    return ("`--as %s` does not resolve to the caller's own member %s; use `--as %s`"
                            % (prepare.deglob(call["actor"]), who, caller_agent_id)), analysis
    if strict:
        # SPD-063: the config the repository each git call reads sets for itself, which git reads with nothing on the line.
        # After the findings, so a refusal the words as spelled already earn (a write verb, a program key, an unknown verb,
        # a repository outside every known checkout) keeps its own reason.
        for targets, target_cwds in analysis.git_calls:
            reason = git_config.git_local_config_reason(ctx, targets, target_cwds)
            if reason:
                return reason, analysis

    def redirect_reason(spelled, path):
        """edit_reason for one concrete file a redirection or tee may open, phrased for the redirect."""
        reason, rel = pathrule.edit_reason(ctx, con, caller_agent_id, caller_member, path, cwd, mode)
        if not reason:
            return None
        # the state directory is refused in the database's words, not Law 1's; a session that is not Spud is not held to Law 1
        law_1 = not caller_agent_id and not plain and not (rel is not None and pathrule.in_state_dir(rel))
        return ("Law 1: a redirection or tee into %s: %s" if law_1 else "a redirection or tee into %s: %s") % (spelled, reason)

    for target, target_cwds in analysis.redirects:
        if "$" in target or "`" in target or hookio.SUBST in target:
            if strict:
                return "the redirection target %s holds a variable or substitution the hook cannot resolve; spell the path out" % prepare.deglob(target), analysis
            continue
        spelled = prepare.deglob(target)
        if target_has_active_glob(target):
            # The shell expands the target before opening it (SPD-034): check every file it opens from every candidate
            # directory, not the literal spelling that maps under no root.
            expansion = redirect_globs.expand_redirect_target(target, target_cwds)
            if expansion is None:  # a directory the hook cannot follow
                if strict:
                    return ("the redirection target %s is relative to a directory the hook cannot follow (a cd into a variable, `cd -`, popd,"
                            " a directory stack entry or ~name, an option or a CDPATH it cannot read, a relative cd in a loop, a sourced file);"
                            " use an absolute path" % spelled), analysis
                continue
            matches, capped = expansion
            for path in matches:
                reason = redirect_reason(spelled, path)
                if reason:
                    return reason, analysis
            if strict:  # a member: the hook cannot know what the glob opens beyond what it matches now
                if capped:
                    return ("the redirection or tee target %s is a glob whose expansion reaches the hook's match budget of %d files;"
                            " write to explicit paths instead" % (spelled, syntax.GLOB_MATCH_CAP)), analysis
                if not matches:
                    return ("the redirection or tee target %s is a glob that matches no file now, so the hook cannot know what the shell"
                            " would open (a matching file may appear before the command runs, or the shell may write the name literally);"
                            " write to an explicit path" % spelled), analysis
            else:  # Spud: also the literal name a shell writes when a glob matches nothing
                for path in redirection_paths(spelled, target_cwds) or []:
                    reason = redirect_reason(spelled, path)
                    if reason:
                        return reason, analysis
            continue
        paths = redirection_paths(spelled, target_cwds)
        if paths is None:  # a member is refused; Spud's target stays unchecked, since the hook cannot know where it lands
            if strict:
                return ("the redirection target %s is relative to a directory the hook cannot follow (a cd into a variable, `cd -`, popd,"
                        " a directory stack entry or ~name, an option or a CDPATH it cannot read, a relative cd in a loop, a sourced file);"
                        " use an absolute path" % spelled), analysis
            continue
        for path in paths:  # every directory the shell may be in (SPD-030)
            reason = redirect_reason(spelled, path)
            if reason:
                return reason, analysis
    return None, analysis


def redirection_paths(target, cwds):
    """The paths a redirection or tee target may name, one per directory the shell may be in; None when it is relative to a
    directory the hook cannot know (`~+` is that directory; `~-` and `~name` are OLDPWD, a user or a zsh named directory)."""
    if target.startswith("~"):
        head, _, tail = target.partition("/")
        if head == "~":
            return [target]
        if head == "~+" and cwds is not None:
            return [os.path.join(c, tail) for c in sorted(cwds)]
        return None
    if os.path.isabs(target):
        return [target]
    if cwds is None:
        return None
    return [os.path.join(c, target) for c in sorted(cwds)]


def target_has_active_glob(target):
    """True when a masked redirection or tee target holds an unquoted glob metacharacter the shell would expand (a quoted
    one is a sentinel, so GLOB_RE, which looks for bare `* ? [` or a brace list, does not see it)."""
    return syntax.GLOB_RE.search(target) is not None
