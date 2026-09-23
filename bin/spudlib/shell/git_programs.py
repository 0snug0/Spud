"""shell/git_programs: git config, environment and options that name a program."""

import re

from . import git_verbs, syntax
from ..hooks import gitrepos


# A git alias is a config key `alias.NAME` whose value git expands into a whole command before it dispatches, so a write can run
# under a name Law 7's verb table does not list (`git -c alias.p=push p` pushes; probed).  `include`/`includeIf` load a config
# file that can define one.  git config sections are case-insensitive.
GIT_ALIAS_SECTIONS = {"alias", "include", "includeif"}
# GIT_CONFIG_* variables that inject config git reads before it dispatches: a file it points at (which can hold aliases the hook
# cannot read), or config set inline (GIT_CONFIG_COUNT with GIT_CONFIG_KEY_n/GIT_CONFIG_VALUE_n, and GIT_CONFIG_PARAMETERS).
# The harness's worktree guard refuses these when they redirect writes; Law 7's hook runs in every session, so it closes them too.
GIT_CONFIG_FILE_VARS = ("GIT_CONFIG", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")
GIT_CONFIG_INLINE_VARS = ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS")
GIT_CONFIG_INDEXED_RE = re.compile(r"GIT_CONFIG_(?:KEY|VALUE)_\d+\Z")
# the variables that move git's global config to a file the hook cannot read -- HOME to <HOME>/.gitconfig and
# XDG_CONFIG_HOME to <XDG_CONFIG_HOME>/git/config (probed on git 2.54.0: `HOME=<dir> git v` and `XDG_CONFIG_HOME=<dir> git vv`
# each expanded an alias defined there).  In force on the line they are GIT_CONFIG_GLOBAL by another name: the file can define
# an alias (which the unknown-verb check closes) and every program-naming key (a pager, editor, driver, helper) under a
# real, allowed verb.
GIT_CONFIG_HOME_VARS = ("HOME", "XDG_CONFIG_HOME")
# The settings that point git at another repository, whose .git/config the hook cannot read either and which a member may have
# crafted under its own deliverables (a .git directory is just files).  Probed: `git -C <repo> w`, `git --git-dir=<repo>/.git w`
# (spaced and `=`), `git --work-tree`, and GIT_DIR/GIT_WORK_TREE/GIT_COMMON_DIR in force each read that repository's config.
# git rejects an attached `-C<dir>`/`-C=<dir>` and every abbreviation of these global options (`--git-di=`, `--gitdir=`:
# "unknown option", probed), so only these spellings parse; `-C` is repeatable and relative to the previous one, and it moves
# what a relative --git-dir or --work-tree means whichever order they appear in (git(1) documents the equivalence).
# A probe (2.54.0) narrows that for the work tree: --work-tree and GIT_WORK_TREE never choose the repository, git reads
# the one it discovers in the directory it runs in (from a directory in no repository `git --work-tree=<repo>` fails), which is
# why git_config reads that directory too.  They stay here, refused outside every checkout, which costs a member nothing.
GIT_REPO_ENV_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR")
GIT_REPO_OPTIONS = ("--git-dir", "--work-tree")
# git config and environment can name a program git runs (a pager, editor, ssh/proxy command, diff or merge driver,
# hooks or exec path, credential or askpass helper, and more) under a verb Law 7's table allows, so a member's `-c core.pager=cmd
# fetch` runs cmd while the hook reads only the allowed verb.  `git help --config` lists ~950 keys and the program-naming ones are
# scattered across many sections (core.pager/editor/sshCommand/hooksPath/gitProxy/fsmonitor/alternateRefsCommand/askPass,
# sequence.editor, diff.external and diff.<d>.command/textconv, filter.<d>.clean/smudge, {diff,merge,gui}tool.<t>.cmd, gpg.program
# and gpg.<f>.program and gpg.ssh.defaultKeyCommand, credential.helper, pager.<cmd>, log.showSignature, remote.<n>.uploadpack/
# receivepack, uploadpack.packObjectsHook, protocol.ext, url.<b>.insteadOf, browser.<t>.cmd, instaweb.httpd, notes.rewrite.<c> ...),
# so a denylist would miss keys git adds.  We allowlist instead: only keys proven to change git's output or behaviour without
# naming or enabling a program pass, everything else is refused (default-deny).  The allowlist is hooks/gitrepos'
# GIT_INERT_CONFIG_SECTIONS and GIT_INERT_CONFIG_KEYS, one layer down, where the repository check that reads
# a key in a repository's own config and the SessionStart line both reach it without the shell.
# core.pager and pager.<cmd> name the pager program; allowed only with an inert value (empty or `cat`), handled in code.
# Environment variables in force on the line (prefix assignment, export, env -- like GIT_CONFIG_* in git_env_defines_alias) that
# name or enable a program git runs.  GIT_PAGER/PAGER are inert with an empty or `cat` value; the rest name a program outright,
# except GIT_ALLOW_PROTOCOL, which enables the ext:: transport whose URL is a command git runs (probed via the harness).
GIT_PAGER_ENV_VARS = ("GIT_PAGER", "PAGER")
# GIT_MAN_VIEWER is git-help(1)'s: "If everything fails, or if no viewer is configured, the viewer specified in the
# GIT_MAN_VIEWER environment variable will be tried" -- a program git runs under the allowed verb `git help`.
GIT_PROGRAM_ENV_VARS = ("GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "EDITOR", "VISUAL", "GIT_SSH", "GIT_SSH_COMMAND",
                        "GIT_EXTERNAL_DIFF", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_PROXY_COMMAND", "GIT_EXEC_PATH",
                        "GIT_TEMPLATE_DIR", "GIT_ALLOW_PROTOCOL", "GIT_MAN_VIEWER")
# The global option `--exec-path=<dir>` is the command-line form of GIT_EXEC_PATH: git runs <dir>/git-* for its subprograms
# (probed: `git --exec-path=<dir> ls-remote https://x` ran <dir>/git-remote-https).  Bare `--exec-path` only prints the path and
# stays silent.  git accepts any unambiguous prefix (`--exec`, `--exec-p`), so a `=`-form prefix of this option is refused.
GIT_EXEC_PATH_OPTION = "--exec-path"
# The options that name a program on an otherwise allowed verb are syntax.GIT_VERB_PROGRAM_OPTIONS, beside the other word
# tables, because GLOB_SAMPLES reads them too; git_verb_names_program below is what the git branch calls.

# PATH, and zsh's `path`, which is tied to it.  Every name the hook reads a command by -- git, spud, python3.14,
# sqlite3, tee, a shell, a wrapper -- the shell then looks for on PATH, so a member that puts a directory of its own first
# runs its own program under a name the hook cleared, one level above the git config that names a program.  Probed in
# zsh 5.9 -f, zsh -f -o nobareglobqual as the Bash tool runs it, bash 3.2 and sh with a fake program in a scratch
# directory: a prefix assignment, a plain assignment, `export`, `typeset -x`, `declare -x`, `readonly`, `local -x` in a
# function and `env PATH=... cmd` each ran the scratch copy, as did zsh's `path=(<dir> $path)` and `path+=(<dir>)`.
PATH_VARS = ("PATH", "path")
# The command names the shell finds on PATH and the hook reads by name: a name outside this set (`ls`, `make`) is one the
# hook grants nothing for, so replacing its program takes a member no further than running any program of its own would.
PATH_DISPATCH_NAMES = frozenset({"git", "spud", "sqlite3", "sqlite", "tee"} | syntax.SHELLS | syntax.JS_RUNTIMES | syntax.WRAPPERS)


def is_path_var(name):
    """A variable that decides which file a bare command name finds, so `env PATH=... cmd` must record it into a.vars."""
    return name in PATH_VARS


def path_in_force(variables):
    """The name of a PATH variable the line assigns, or None.  A fixed order so the reason is deterministic."""
    for name in PATH_VARS:
        if name in variables:
            return name
    return None


def path_dispatched(word):
    """True when the shell looks this command word up on PATH and the hook reads it by name.  A word holding a
    slash is a path the shell opens as spelled, so PATH does not decide it; the name is matched case-folded, as the
    dispatch matches it (macOS finds GIT for git)."""
    if "/" in word:
        return False
    base = word.casefold()
    return base in PATH_DISPATCH_NAMES or syntax.PYTHON_RE.match(base) is not None


def git_config_section(operand):
    """The section of a `-c name=value` or `--config-env name=envvar` operand (everything before the first `.` of the key),
    case-folded, since git config sections are case-insensitive.  `-c alias.p=push` -> `alias`; `includeIf.gitdir:/x/.path=f`
    -> `includeif`; `user.name=x` -> `user`."""
    key = operand.split("=", 1)[0]
    return key.split(".", 1)[0].strip().casefold()


def git_line_defines_alias(words):
    """The spelling of the first `-c`/`--config-env` option on a git line that defines an alias or an include (which git
    expands or loads before it dispatches, so the verb the hook reads is not what runs), or None.  Non-alias config
    (`-c user.name=x`, `-c color.ui=never`, `-c core.pager=cat`) is a control git honours without changing the verb, so it is
    left silent.  The joined `-calias.x=y` form is not read: git rejects it (`unknown option`, probed)."""
    i = 1
    while i < len(words):
        w = words[i]
        if w == "-c" or w == "--config-env":
            operand = words[i + 1] if i + 1 < len(words) else ""
            if git_config_section(operand) in GIT_ALIAS_SECTIONS:
                return "%s %s" % (w, operand)
            i += 2
            continue
        if w.startswith("--config-env="):
            operand = w[len("--config-env=") :]
            if git_config_section(operand) in GIT_ALIAS_SECTIONS:
                return w
            i += 1
            continue
        if w in syntax.GIT_GLOBAL_VALUE_FLAGS:
            i += 2
            continue
        if w.startswith("-"):
            i += 1
            continue
        return None  # the verb: git's global options are done
    return None


def is_git_config_var(name):
    """A GIT_CONFIG_* variable that points git at a config file or injects config inline, or one of the home
    variables that moves git's global config to a file the hook cannot read."""
    return (name in GIT_CONFIG_FILE_VARS or name in GIT_CONFIG_INLINE_VARS or name in GIT_CONFIG_HOME_VARS
            or GIT_CONFIG_INDEXED_RE.match(name) is not None)


def is_git_repo_var(name):
    """A variable that points git at another repository, whose .git/config the hook cannot read."""
    return name in GIT_REPO_ENV_VARS


def is_git_write_var(name):
    """A variable that names a file or directory git writes -- a GIT_TRACE* sibling, GIT_INDEX_FILE,
    GIT_OBJECT_DIRECTORY -- so `env GIT_TRACE=... git ...` must record it into a.vars."""
    return name.startswith(syntax.GIT_TRACE_VAR_PREFIX) or name in syntax.GIT_WRITE_PATH_ENV_VARS


def git_env_defines_alias(variables):
    """The name of a variable in force that gives git config the hook cannot resolve (a file GIT_CONFIG, GIT_CONFIG_GLOBAL or
    GIT_CONFIG_SYSTEM points at; GIT_CONFIG_COUNT/GIT_CONFIG_KEY_n/GIT_CONFIG_VALUE_n and GIT_CONFIG_PARAMETERS set inline;
    HOME or XDG_CONFIG_HOME, which move git's global config to <dir>/.gitconfig or <dir>/git/config), any of which can
    define an alias git expands into a write verb, or None.  A fixed order so the reason is deterministic, the home variables
    last so a line that sets both keeps the alias reason."""
    for name in GIT_CONFIG_FILE_VARS + GIT_CONFIG_INLINE_VARS + GIT_CONFIG_HOME_VARS:
        if name in variables:
            return name
    for name in sorted(variables):
        if GIT_CONFIG_INDEXED_RE.match(name):
            return name
    return None


def git_inert_pager_value(value):
    """A pager value that runs nothing of interest: only an empty value or `cat`.  A missing value (`-c core.pager`
    with no `=`) is git's boolean true, which uses the default pager (a real program), so it is not inert."""
    return value is not None and value.strip() in ("", "cat")


def git_config_key_allowed(flag, operand):
    """True when a `-c name=value` / `--config-env name=envvar` operand sets a config key that cannot name or enable a program
    git runs, so a member may set it (an allowlist: everything not proven inert is refused).  core.pager and pager.<cmd>
    name the pager program and are allowed only with an inert value (empty or `cat`), and only in the `-c` form whose value the
    hook can read -- a `--config-env` value lives in an environment variable the hook cannot see, so it is never inert here."""
    key_part, sep, raw_value = operand.partition("=")
    section = key_part.split(".", 1)[0].strip().casefold()
    last = key_part.rsplit(".", 1)[-1].strip().casefold()
    if gitrepos.git_config_key_inert(key_part):
        return True
    if section == "pager" or (section == "core" and last == "pager"):
        return flag == "-c" and git_inert_pager_value(raw_value if sep else None)
    return False


def git_line_names_program(words):
    """The spelling of the first `-c`/`--config-env` option on a git line whose key is outside the inert allowlist,
    or None.  git config can name a program git runs (a pager, editor, ssh or proxy command, diff/merge driver, hooks or exec
    path, credential or askpass helper, and more) under a verb Law 7 allows, so only keys proven inert (git_config_key_allowed)
    pass.  Alias/include keys are caught first by git_line_defines_alias, which keeps its own alias reason."""
    i = 1
    while i < len(words):
        w = words[i]
        if w == "-c" or w == "--config-env":
            operand = words[i + 1] if i + 1 < len(words) else ""
            if not git_config_key_allowed(w, operand):
                return "%s %s" % (w, operand)
            i += 2
            continue
        if w.startswith("--config-env="):
            operand = w[len("--config-env=") :]
            if not git_config_key_allowed("--config-env", operand):
                return w
            i += 1
            continue
        key, sep, _ = w.partition("=")
        if sep and key.startswith("--") and len(key) >= 3 and GIT_EXEC_PATH_OPTION.startswith(key):
            return w  # `--exec-path=<dir>` (and abbreviations): the command-line form of GIT_EXEC_PATH, git runs <dir>/git-*
        if w in syntax.GIT_GLOBAL_VALUE_FLAGS:
            i += 2
            continue
        if w.startswith("-"):
            i += 1
            continue
        return None  # the verb: git's global options are done
    return None


def is_git_program_var(name):
    """An environment variable that names a program git runs, so `env NAME=... git ...` must record it into a.vars."""
    return name in GIT_PROGRAM_ENV_VARS or name in GIT_PAGER_ENV_VARS


def git_env_names_program(variables):
    """The name of an environment variable in force on the line that names a program git would run (an editor, ssh or proxy
    command, diff driver, askpass or exec/template dir), or None.  GIT_PAGER/PAGER are inert with an empty or `cat` value.  A
    fixed order so the reason is deterministic."""
    for name in GIT_PROGRAM_ENV_VARS:
        if name in variables:
            return name
    for name in GIT_PAGER_ENV_VARS:
        if name in variables and not git_inert_pager_value(variables[name]):
            return name
    return None


def git_verb_names_program(words):
    """`verb option` when an allowed git verb carries an option that names a program git runs:
    ls-remote/fetch --upload-pack, grep -O/--open-files-in-pager, difftool -x/--extcmd, archive --exec, send-email
    --sendmail-cmd/--smtp-server/--to-cmd/--cc-cmd, instaweb --httpd/-d and --browser/-b, web--browse --browser/-b,
    --tool/-t and --config/-c (syntax.GIT_VERB_PROGRAM_OPTIONS).  Write verbs (submodule, bisect ...) are
    already refused whole by git_refused, so they are absent.  git accepts any unambiguous prefix of a long option and lets short
    options cluster with the value attached, so a `--`-prefix of one of the verb's long options (`--upload`, `--ext`, with or
    without `=value`) and any short cluster containing one of its letters (`-nO`, `-x`) are refused, value inspected or not."""
    verb, args = git_verbs.git_verb(words)
    entry = syntax.GIT_VERB_PROGRAM_OPTIONS.get(verb)
    if not entry:
        return None
    longs, shorts = entry
    for w in args:
        if w == "--":
            break  # nothing after the end-of-options marker is an option (a pattern or path, not a program)
        key = w.split("=", 1)[0]
        if key.startswith("--") and len(key) >= 3 and any(opt.startswith(key) for opt in longs):
            return "%s %s" % (verb, w)  # a full or abbreviated long option
        if shorts and w.startswith("-") and not w.startswith("--") and any(c in shorts for c in w[1:]):
            return "%s %s" % (verb, w)  # a short cluster carrying the program letter (fail closed)
    return None
