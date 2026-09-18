"""shell/git_config: The repository a member's git call reads: a known checkout's own (SPD-066), and the config it sets for itself (SPD-063).  Moved from bin/spud_ledger.py (SPD-065)."""

import contextlib
import json
import os
import re

from . import bash_rule, git_programs, git_verbs, prepare
from ..core import homeconf, lazy
from ..hooks import hookio, worktrees


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


def git_dir_layout(path):
    """True when `path` is laid out as a git directory: a HEAD, and objects/ and refs/ of its own or a `commondir` file naming
    the directory that holds them (a linked worktree's <common>/worktrees/<id>), which is what git accepts as one."""
    if not os.path.lexists(os.path.join(path, "HEAD")):
        return False
    if os.path.isfile(os.path.join(path, "commondir")):
        return True
    return os.path.isdir(os.path.join(path, "objects")) and os.path.isdir(os.path.join(path, "refs"))


def git_dot_dir(root):
    """The git directory a `<root>/.git` names: the directory itself, or the one a gitfile's `gitdir:` line points at, relative
    spellings resolved against the root; None when there is no .git there or it names nothing."""
    dot = os.path.join(root, ".git")
    if os.path.isdir(dot):
        return dot
    try:
        with open(dot, encoding="utf-8", errors="replace") as f:
            named = f.read(GIT_CONFIG_READ_LIMIT).strip()
    except OSError:
        return None
    if not named.startswith("gitdir:"):
        return None
    gitdir = named[len("gitdir:"):].strip()
    return os.path.normpath(gitdir if os.path.isabs(gitdir) else os.path.join(root, gitdir))


def git_repository_dirs(directory, as_git_dir=False):
    """(the directory to run git in, the repository's git directory, its common directory) for the repository `directory`
    lies in, or (None, None, None) when it lies in none -- read without running git, the way git discovers one: the nearest
    ancestor holding a `.git` directory or a `.git` file naming one (a linked worktree or a submodule), or a directory that
    is a git directory itself (a bare repository, or the `--git-dir=<repo>/.git` a git call names).  The git directory is
    the directory itself exactly when it was found as a bare layout.

    `as_git_dir`: the directory is one a git call names as its git or common directory (--git-dir, GIT_DIR, GIT_COMMON_DIR),
    which git takes as given, so it is read as a git directory before its own .git is (SPD-066: a bare layout planted at a
    checkout's root beside that root's .git is what `git --git-dir=<root>` reads); one that is not laid out as a git
    directory is walked as before, where git would refuse to run at all."""
    cur = os.path.abspath(directory)
    if as_git_dir and git_dir_layout(cur):
        return cur, cur, git_repo_common_dir(cur)
    for _ in range(GIT_WALK_LIMIT):
        dot = os.path.join(cur, ".git")
        if os.path.isdir(dot) or os.path.isfile(dot):
            gitdir = git_dot_dir(cur)
            if gitdir is None:
                return None, None, None
            return cur, gitdir, git_repo_common_dir(gitdir)
        if git_dir_layout(cur):
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


# SPD-066: the repository itself.  SPD-047 refuses a repository a line names outside every checkout the ledger knows, and
# SPD-063 reads the keys a repository sets for itself; neither sees a repository nested in a checkout -- a .git built below
# its root by an interpreter, cp or mkdir, which the path rule cannot read, or a bare layout (HEAD, objects/, refs/, hooks/)
# written under a member's globs, which no path names -- and git runs such a repository's hooks with nothing on the line
# (probed on 2.54.0: `git status` runs post-index-change, `git fetch` reference-transaction; pre-auto-gc was not reached,
# fetch's auto maintenance running gc without it) and reads its config, attributes and index.  So the repository a member's
# git call reads must be a known checkout's own: discovered, its work tree is a registered project's root or a listed
# worktree, reached through that root's own .git; named as the git or common directory, or found as a bare layout, it is
# the git or common directory such a root's .git names.  This is SPD-047's rule carried one level down, and it closes hooks,
# config, attributes and the index at once, with the one walk SPD-063 already makes, no git run and no listing of a hooks
# directory.  It lives here, beside SPD-063's keys, and not in a module of its own: both are answers about the repository
# that one walk finds, in one loop, and neither has another caller.
GIT_FOREIGN_REPOSITORY_REASON = (
    "Law 7: this git call reads the repository at %s (%s), which is not the own repository of a checkout the ledger knows"
    " -- a registered project's root or one of its listed worktrees, read through that root's own .git -- and %s. git runs"
    " such a repository's hooks with nothing on the line (post-index-change under `git status`, reference-transaction under"
    " `git fetch`) and reads its config, attributes and index, and a repository nested in a checkout -- a .git built below"
    " its root, a gitfile, or a bare layout of HEAD, objects/ and refs/ -- is one a member can build under its own"
    " deliverables, so a program can run under a verb Law 7's table allows. Run git in the checkout's own repository; Spud"
    " commits, after the outcome is recorded")


def git_call_directories(targets, cwds):
    """([(directory, named as a git or common directory, the spelling that named it or None)], whether the hook cannot
    resolve one) for one git call: every target git_repo_targets found, and the directories the shell may be in whenever
    neither -C nor --git-dir/GIT_DIR is on the line, since git then discovers the repository there -- --work-tree,
    GIT_WORK_TREE and GIT_COMMON_DIR do not stop that (SPD-066; SPD-063 read only the targets when there were any)."""
    out, unresolved, kinds = [], False, set()
    for spelled, target in targets:
        kind = git_verbs.git_target_kind(spelled)
        kinds.add(kind)
        resolved, cannot = bash_rule.git_target_dirs(target, cwds)
        unresolved = unresolved or cannot
        out.extend((d, kind in ("gitdir", "common"), spelled) for d in resolved)
    if not kinds & {"chdir", "gitdir"}:
        if cwds is None:
            unresolved = True
        else:
            out.extend((d, False, None) for d in sorted(cwds))
    return out, unresolved


def git_checkout_repository(roots, where, gitdir, commondir):
    """True when the repository git_repository_dirs found is the own repository of one of `roots` (every checkout the ledger
    knows, project_checkouts' roots and listed worktrees): found through `<where>/.git`, where `where` is such a root; or a
    git directory taken as it is (a bare layout, or one a call names), which is one such root's own git directory, with its
    common directory one of theirs too.  Compared by file identity, so any spelling of a root the filesystem honours is it."""
    ident = worktrees.file_identity
    if gitdir != where:
        mine = ident(where)
        return mine is not None and any(ident(root) == mine for root in roots)
    known = set()
    for root in roots:
        own = git_dot_dir(root)
        if own is not None:
            known.update(i for i in (ident(own), ident(git_repo_common_dir(own))) if i is not None)
    return ident(gitdir) in known and ident(commondir) in known


def git_foreign_repository_reason(ctx, con, directory, spelled, where):
    """GIT_FOREIGN_REPOSITORY_REASON for the repository at `where` (its work tree, or the git directory itself when it was
    taken as one) found from `directory`: naming it, how the call reached it, and the checkout it lies in, if any."""
    how = prepare.deglob(spelled) if spelled else "discovered from %s" % directory
    placed = worktrees.project_paths(ctx, con, where, None)
    lies = ("it lies in the checkout %s" % placed[0][1]) if placed else "it lies in no checkout the ledger knows"
    return GIT_FOREIGN_REPOSITORY_REASON % (where, how, lies)


def git_repository_reason(ctx, con, targets, cwds):
    """The reason a member's git call is refused for the repository it reads, or None: a repository that is not a known
    checkout's own (SPD-066), and then the config it sets for itself (SPD-063).  The repository is the one `-C`,
    `--git-dir`, GIT_DIR and friends name (SPD-047's git_repo_targets) and, when git discovers it, the one containing each
    directory the shell may be in.  A target outside every checkout the ledger knows already has SPD-047's own refusal,
    which is read first."""
    directories, unresolved = git_call_directories(targets, cwds)
    if unresolved:
        return GIT_SCOPE_UNRESOLVED_REASON
    roots = None
    for directory, as_git_dir, spelled in directories:
        where, gitdir, commondir = git_repository_dirs(directory, as_git_dir)
        if gitdir is None:
            continue  # no repository there: git reads no config of its own, runs no hook, and the hook runs nothing
        if roots is None:
            roots = [root for _project, checkouts in worktrees.project_checkouts(ctx, con) for root in checkouts]
        if not git_checkout_repository(roots, where, gitdir, commondir):
            return git_foreign_repository_reason(ctx, con, directory, spelled, where)
        keys = git_own_config_keys(ctx.home, where, gitdir, commondir)
        if keys is None:
            return GIT_SCOPE_UNREADABLE_REASON % where
        for scope, key in keys:
            if git_config_key_names_program(key):
                return GIT_SCOPE_REASON % (key, scope, where)
    return None
