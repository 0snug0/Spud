"""shell/git_config: The config a repository sets for itself (SPD-063).  Moved from bin/spud_ledger.py (SPD-065)."""

import contextlib
import json
import os
import re

from . import bash_rule, git_programs
from ..core import homeconf, lazy
from ..hooks import hookio


# -- the config a repository sets for itself (SPD-063) ------------------------------------------------------------------
#
# git reads the target repository's own config with nothing on the line, and a member can craft one under its deliverable
# globs inside a checkout the ledger knows (`git -C tests/fake status` resolves inside the home, so SPD-047's git-repo
# refusal, which only fires outside every known checkout, stays silent).  SPD-064 closes a member's writes to ~/.gitconfig
# and $XDG_CONFIG_HOME/git/config, and the edit hook now refuses every .git/config, so what is left is a repository the
# member did not write: before a member's git call the hook reads the keys in force at that repository's `local` and
# `worktree` scopes and refuses the ones that name or enable a program git runs (SPD-046's class).
#
# The system and global scopes are Eric's own and stay out of it: credential.helper is in force at the system scope on this
# Mac.  And the check is on the program-naming keys, not on every key outside SPD-046's inert allowlist: Spud's own checkout
# carries core.filemode, core.bare, core.logallrefupdates, core.ignorecase, core.precomposeunicode, extensions.worktreeConfig,
# remote.origin.url, remote.origin.fetch and branch.main.remote/merge/vscode-merge-base at its local scope (probed), none of
# them in that allowlist, so a literal default-deny would refuse every member git call in every real repository.
GIT_CONFIG_SCOPES_CACHE = "git-config-scopes.json"
GIT_OWN_SCOPES = ("local", "worktree")  # the scopes a repository sets for itself; `system`, `global` and `unknown` are Eric's
GIT_CONFIG_SCOPES_KEPT = 64  # repositories kept in the cache file
GIT_CONFIG_INCLUDE_FILES = 8  # config files followed through include.path/includeIf.<c>.path when fingerprinting
GIT_CONFIG_READ_LIMIT = 1 << 18  # bytes read from one config file when looking for its includes
GIT_WALK_LIMIT = 64  # directories walked up from a candidate looking for a repository
_GIT_INCLUDE_PATH_RE = re.compile(r"^[ \t]*path[ \t]*=[ \t]*(.+?)[ \t]*$", re.MULTILINE | re.IGNORECASE)
# The sections whose every documented key names or drives a program git runs, and the words the last component of such a key
# carries (SPD-046's enumeration generalised: core.pager/editor/sshCommand/hooksPath/gitProxy/fsmonitor/alternateRefsCommand/
# askPass, sequence.editor, diff.external and diff.<d>.command/textconv, filter.<d>.clean/smudge/process, {diff,merge,gui}
# tool.<t>.cmd, gpg.program and gpg.<f>.program and gpg.ssh.defaultKeyCommand, credential.helper, pager.<cmd>,
# log.showSignature, remote.<n>.uploadpack/receivepack, uploadpack.packObjectsHook, protocol.ext.allow, url.<b>.insteadOf,
# browser.<t>.cmd, instaweb.httpd).  Matching the word anywhere in the last component catches the keys git adds later under
# the same naming (anything ...Command, ...Cmd, ...Program, ...Helper, ...Hook) and over-refuses an occasional inert one
# (merge.tool, difftool.prompt), which fails closed and costs a member nothing it needs.
GIT_PROGRAM_KEY_SECTIONS = {"filter", "difftool", "mergetool", "guitool", "instaweb", "browser", "protocol", "gpg",
                            "credential", "pager", "sequence"}
GIT_PROGRAM_KEY_WORDS = ("pager", "editor", "command", "cmd", "program", "helper", "hook", "external", "textconv", "clean",
                         "smudge", "process", "askpass", "proxy", "exec", "uploadpack", "receivepack", "insteadof", "httpd",
                         "showsignature", "fsmonitor", "driver", "tool", "shell", "script", "wrapper", "alternaterefs")


def git_config_key_names_program(key):
    """True when a config key in force in a repository names or enables a program git runs under a verb Law 7's table allows
    (SPD-046's class), so a member's git call in that repository is refused (SPD-063).  The keys SPD-046 proved inert pass
    first, so color.pager (a boolean) and safe.directory stay silent."""
    if git_programs.git_config_key_allowed("--config-env", key):  # never the `-c` form: the hook reads no value here, so a pager is not inert
        return False
    section = key.split(".", 1)[0].strip().casefold()
    last = key.rsplit(".", 1)[-1].strip().casefold()
    return section in GIT_PROGRAM_KEY_SECTIONS or any(word in last for word in GIT_PROGRAM_KEY_WORDS)


def git_repo_common_dir(gitdir):
    """The repository's common directory: what `<gitdir>/commondir` names for a linked worktree (gitrepository-layout(5)),
    else the git directory itself."""
    try:
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8", errors="replace") as f:
            named = f.read().strip()
    except OSError:
        return gitdir
    if not named:
        return gitdir
    return os.path.normpath(named if os.path.isabs(named) else os.path.join(gitdir, named))


def git_repository_dirs(directory):
    """(the directory to run git in, the repository's git directory, its common directory) for the repository `directory`
    lies in, or (None, None, None) when it lies in none -- read without running git, the way git discovers one: the nearest
    ancestor holding a `.git` directory or a `.git` file naming one (a linked worktree or a submodule), or a directory that
    is a git directory itself (a bare repository, or the `--git-dir=<repo>/.git` a git call names)."""
    cur = os.path.abspath(directory)
    for _ in range(GIT_WALK_LIMIT):
        dot = os.path.join(cur, ".git")
        if os.path.isdir(dot):
            return cur, dot, git_repo_common_dir(dot)
        if os.path.isfile(dot):
            try:
                with open(dot, encoding="utf-8", errors="replace") as f:
                    named = f.read(GIT_CONFIG_READ_LIMIT).strip()
            except OSError:
                return None, None, None
            if not named.startswith("gitdir:"):
                return None, None, None
            gitdir = named[len("gitdir:"):].strip()
            gitdir = os.path.normpath(gitdir if os.path.isabs(gitdir) else os.path.join(cur, gitdir))
            return cur, gitdir, git_repo_common_dir(gitdir)
        if os.path.isdir(os.path.join(cur, "objects")) and os.path.isdir(os.path.join(cur, "refs")) \
                and os.path.lexists(os.path.join(cur, "HEAD")):
            return cur, cur, git_repo_common_dir(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None, None, None


def git_scope_config_files(gitdir, commondir):
    """Every file git reads at the local and worktree scopes of one repository: <commondir>/config (the local scope),
    <gitdir>/config.worktree (the worktree scope, with extensions.worktreeConfig), the git directory's own config where it
    is the common one, and the files any `include.path`/`includeIf.<c>.path` in them names.  An included file's keys are
    reported at the including file's scope, so the listing already covers them; they are here so that editing one changes
    the fingerprint the answer is cached under."""
    files, queue = [], [os.path.join(commondir, "config"), os.path.join(gitdir, "config"),
                        os.path.join(gitdir, "config.worktree"), os.path.join(commondir, "config.worktree")]
    while queue and len(files) < GIT_CONFIG_INCLUDE_FILES:
        path = os.path.normpath(queue.pop(0))
        if path in files:
            continue
        files.append(path)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read(GIT_CONFIG_READ_LIMIT)
        except OSError:
            continue
        for named in _GIT_INCLUDE_PATH_RE.findall(text):
            named = named.strip().strip('"')
            if named:
                queue.append(os.path.expanduser(named) if named.startswith("~") else
                             (named if os.path.isabs(named) else os.path.join(os.path.dirname(path), named)))
    return files


def git_config_fingerprint(files):
    """What changes when the config a repository sets for itself changes, read without running git: the stat of each file
    git reads at its local and worktree scopes; a file that is not there is recorded as absent, so one that appears later
    is a change too."""
    out = []
    for path in files:
        try:
            st = os.stat(path)
            out.append([path, st.st_mtime_ns, st.st_size, st.st_ino])
        except OSError:
            out.append([path, None, None, None])
    return out


def git_run_config_scopes(where):
    """[[scope, key]] as `git config --list --show-scope --name-only` prints them in `where`, with git_env()'s sanitised
    environment -- the hook's own, never the line's HOME or PATH -- or None when git cannot be run or fails there.  git
    reports an included file's keys at the including file's scope, and prints one `scope<TAB>key` per line (a config key
    can hold neither a tab nor a newline: a section header is one line and a subsection name escapes only \\" and \\\\)."""
    try:
        proc = lazy.subprocess.run(["git", "-C", where, "config", "--list", "--show-scope", "--name-only"],
                              capture_output=True, text=True, errors="replace", env=homeconf.git_env(), timeout=10)
    except (OSError, lazy.subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out = []
    for line in proc.stdout.splitlines():
        scope, sep, key = line.partition("\t")
        if sep and key:
            out.append([scope, key])
    return out


def git_own_config_keys(home, where, gitdir, commondir):
    """[[scope, key]] in force at one repository's own scopes (`local` and `worktree`), [] when it sets nothing there, or
    None when the hook could not read them, which fails closed.

    Kept in <home>/.spud/git-config-scopes.json under the stat fingerprint of the files git reads at those scopes, so a hook
    runs git only after one of them changes: the Bash hook runs on every command line and SPD-016 keeps subprocess off its
    path.  A repository that sets nothing for itself costs no git run at all.  The cache lives in the state directory, which
    the edit and Bash hooks refuse to everyone, so nothing a member writes can widen what passes."""
    files = git_scope_config_files(gitdir, commondir)
    if not any(os.path.lexists(f) for f in files):
        return []  # no local or worktree scope: nothing for the repository to say, and nothing to run git for
    fingerprint = git_config_fingerprint(files)
    state = os.path.join(str(home), hookio.STATE_DIR) if home else None
    cache = os.path.join(state, GIT_CONFIG_SCOPES_CACHE) if state else None
    stored = {}
    if cache:
        with contextlib.suppress(OSError, ValueError):
            with open(cache, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                stored = loaded
        entry = stored.get(gitdir)
        if isinstance(entry, dict) and entry.get("fingerprint") == fingerprint and isinstance(entry.get("keys"), list):
            return [k for k in entry["keys"] if isinstance(k, list) and len(k) == 2]
    listed = git_run_config_scopes(where)
    if listed is None:
        return None
    keys = [[scope, key] for scope, key in listed if scope in GIT_OWN_SCOPES]
    if cache and os.path.isdir(state):
        stored = {k: v for k, v in stored.items() if k != gitdir}
        for extra in sorted(stored)[:max(0, len(stored) - GIT_CONFIG_SCOPES_KEPT + 1)]:
            del stored[extra]
        stored[gitdir] = {"fingerprint": fingerprint, "keys": keys}
        tmp = "%s.%d.tmp" % (cache, os.getpid())
        with contextlib.suppress(OSError):
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(stored, f)
            os.replace(tmp, cache)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
    return keys


GIT_SCOPE_REASON = (
    "Law 7: this git call reads a repository that sets %s at its own %s scope (the repository in %s), a config key that"
    " names or enables a program git runs -- a pager, editor, ssh or proxy command, diff or merge driver, hooks path,"
    " credential or askpass helper, the ext:: transport -- under a verb Law 7's table allows. git reads that file with"
    " nothing on the line, and a member can write such a file under its own deliverables, so a write can run under a verb"
    " the table does not list. Run git in a checkout that does not set it; Spud commits, after the outcome is recorded")
GIT_SCOPE_UNRESOLVED_REASON = (
    "Law 7: this git call runs in a directory the hook cannot follow (a cd into a variable, `cd -`, popd, a directory stack"
    " entry or ~name, an option or a CDPATH it cannot read, a relative cd in a loop, a sourced file), so it cannot read the"
    " config in force at the local and worktree scopes of the repository git would read there -- the keys that name a"
    " program git runs under a verb Law 7's table allows. The hook fails closed: run git from an absolute path inside the"
    " session's own checkout; Spud commits, after the outcome is recorded")
GIT_SCOPE_UNREADABLE_REASON = (
    "Law 7: the hook cannot read the config in force in the repository this git call reads (`git config --list --show-scope"
    " --name-only` in %s), so it cannot tell whether that repository sets a key naming a program git runs under a verb"
    " Law 7's table allows; the hook fails closed. Spud commits, after the outcome is recorded")


def git_local_config_reason(ctx, targets, cwds):
    """The reason a member's git call is refused for the config the repository it reads sets for itself (SPD-063), or None.
    The repository is the one `-C`, `--git-dir`, `--work-tree`, GIT_DIR and friends name (SPD-047's git_repo_targets) or,
    with none of them on the line, the one containing each directory the shell may be in.  A target outside every checkout
    the ledger knows already has SPD-047's own refusal, which is read first."""
    dirs, unresolved = [], False
    if targets:
        for _spelled, target in targets:
            resolved, cannot = bash_rule.git_target_dirs(target, cwds)
            unresolved = unresolved or cannot
            dirs.extend(resolved)
    elif cwds is None:
        unresolved = True
    else:
        dirs.extend(sorted(cwds))
    if unresolved:
        return GIT_SCOPE_UNRESOLVED_REASON
    for directory in dirs:
        where, gitdir, commondir = git_repository_dirs(directory)
        if gitdir is None:
            continue  # no repository there: git reads no config of its own, and the hook runs nothing
        keys = git_own_config_keys(ctx.home, where, gitdir, commondir)
        if keys is None:
            return GIT_SCOPE_UNREADABLE_REASON % where
        for scope, key in keys:
            if git_config_key_names_program(key):
                return GIT_SCOPE_REASON % (key, scope, where)
    return None
