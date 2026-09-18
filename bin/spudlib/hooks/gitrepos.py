"""hooks/gitrepos: The repository git reads from a directory, what it sets for itself and what it would run, read without running git.

SPD-063 and SPD-066 built this in shell/git_config for a member's git call; SPD-123 moved it here, below the shell, because
three readers need it and the shell's import cycle must stay off two of them: the Bash hook (shell/git_config, for every
caller but a plain session's), the SessionStart context and `spud board --brief` (one line naming a checkout with a
finding), and `spud doctor` (every finding).  It stays one module past the size rule's look-again point because it is one
reading of one repository -- where git finds it, the config it sets for itself and the hooks it runs, and whether it is a
known checkout's own -- and every reader asks all of it in the same order."""

import contextlib
import json
import os
import re

from . import hookio, worktrees
from ..core import homeconf, lazy


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
_GIT_INCLUDE_PATH = r"^[ \t]*path[ \t]*=[ \t]*(.+?)[ \t]*$"  # read with re.findall, so nothing compiles until a config is read
# SPD-046's allowlist, which shell/git_programs reads for a `-c` key on the line and git_config_key_names_program for a key
# in a repository's own config.  Sections whose every documented key is inert:
GIT_INERT_CONFIG_SECTIONS = {
    "color",    # color.* -- terminal colour of output only (color.pager is a boolean, not a program)
    "advice",   # advice.* -- booleans toggling advisory hint messages
    "i18n",     # i18n.commitEncoding/logOutputEncoding/filesEncoding -- text encodings
    "column",   # column.* -- multi-column output layout
}
# Inert keys inside sections that also hold program-naming keys (so the whole section cannot be allowed):
GIT_INERT_CONFIG_KEYS = {
    "core.quotepath",   # whether to quote non-ASCII bytes in printed paths (output)
    "core.abbrev",      # length of abbreviated object names (output)
    "log.date",         # date format git prints (output); NOT log.showSignature, which runs gpg
    "safe.directory",   # marks a directory trusted; runs no program
}
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
# Each key's answer, once per process (SPD-123): a repository with many branches or remotes has hundreds of keys at its own
# scopes, its checkout and every worktree of it share them, and SessionStart and doctor read every checkout's in one run.
_PROGRAM_KEYS = {}


def git_config_key_inert(key):
    """True when a config key is on SPD-046's allowlist: a section whose every key is inert, or one inert key."""
    section = key.split(".", 1)[0].strip().casefold()
    return section in GIT_INERT_CONFIG_SECTIONS or key.strip().casefold() in GIT_INERT_CONFIG_KEYS


def git_config_key_names_program(key):
    """True when a config key in force in a repository names or enables a program git runs under a verb Law 7's table allows
    (SPD-046's class), so a member's git call in that repository is refused (SPD-063).  The keys SPD-046 proved inert pass
    first, so color.pager (a boolean) and safe.directory stay silent; core.pager and pager.<cmd> never do, since the hook
    reads no value here."""
    named = _PROGRAM_KEYS.get(key)
    if named is None:
        section = key.split(".", 1)[0].strip().casefold()
        last = key.rsplit(".", 1)[-1].strip().casefold()
        named = _PROGRAM_KEYS[key] = not git_config_key_inert(key.partition("=")[0]) and (
            section in GIT_PROGRAM_KEY_SECTIONS or any(word in last for word in GIT_PROGRAM_KEY_WORDS))
    return named


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
        for named in re.findall(_GIT_INCLUDE_PATH, text, re.MULTILINE | re.IGNORECASE):
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


_SCOPES_READ = {}  # the cache file -> (its stat, what it held when read), so one process parses it once while it is unchanged


def git_scopes_cache(cache):
    """What <home>/.spud/git-config-scopes.json holds, {} when it is missing or unreadable.  Parsed once per process while
    its stat stays the same (SPD-123): SessionStart and doctor read every checkout's entry in one run, and a repository with
    many branches keeps hundreds of keys there.  A write replaces the file, which changes its inode, so it is read again.
    The dict is shared: git_own_config_keys builds a new one before it changes anything."""
    try:
        st = os.stat(cache)
    except OSError:
        return {}
    stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
    held = _SCOPES_READ.get(cache)
    if held is not None and held[0] == stamp:
        return held[1]
    stored = {}
    with contextlib.suppress(OSError, ValueError):
        with open(cache, encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            stored = loaded
    _SCOPES_READ[cache] = (stamp, stored)
    return stored


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
        stored = git_scopes_cache(cache)
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


# -- a known checkout's own repository (SPD-066) ------------------------------------------------------------------------


def checkout_identities(checkouts):
    """What git_checkout_repository compares against, read once per call: {checkouts: project_checkouts' rows, roots: the
    identity of every root and listed worktree, commons: the identity of each registered root's own common directory}.
    Only a registered root's .git says which common directory is a checkout's own: a worktree's gitfile or commondir is a
    file a member could rewrite to name any repository (SPD-123)."""
    ident = worktrees.file_identity
    roots, commons = set(), set()
    for _project, paths in checkouts:
        for n, root in enumerate(paths):
            mine = ident(root)
            if mine is not None:
                roots.add(mine)
            own = git_dot_dir(root) if n == 0 else None  # the registered root, never a listed worktree
            common = ident(git_repo_common_dir(own)) if own is not None else None
            if common is not None:
                commons.add(common)
    return {"checkouts": checkouts, "roots": roots, "commons": commons}


def git_checkout_repository(known, where, gitdir, commondir):
    """True when the repository git_repository_dirs found is the own repository of a checkout the ledger knows (`known` is
    checkout_identities'): found through `<where>/.git`, where `where` is a root or listed worktree; or a git directory
    taken as it is (a bare layout, or one a call names), which is such a checkout's own git or common directory.  Either
    way its common directory, the one whose hooks and config git reads, is a registered root's own (SPD-123: a worktree's
    .git gitfile or its commondir rewritten to name another repository is not its checkout's).  Compared by file identity,
    so any spelling of a root the filesystem honours is it."""
    ident = worktrees.file_identity
    if ident(commondir) not in known["commons"]:
        return False
    if gitdir != where:
        mine = ident(where)
        return mine is not None and mine in known["roots"]
    own = set()
    for _project, paths in known["checkouts"]:
        for root in paths:
            dot = git_dot_dir(root)
            if dot is not None:
                own.update(i for i in (ident(dot), ident(git_repo_common_dir(dot))) if i is not None)
    return ident(gitdir) in own


def in_known_checkout(home, known, path):
    """True when `path` lies in a checkout the ledger knows (`known` is checkout_identities'): a root or listed worktree
    itself, or a path below one by its lexical or its real reading (worktrees.map_into_checkouts)."""
    if worktrees.file_identity(path) in known["roots"]:
        return True
    p = os.path.abspath(path)
    return any(worktrees.map_into_checkouts(known["checkouts"], c, str(home)) for c in (os.path.normpath(p), os.path.realpath(p)))


# -- what git would run from it (SPD-123) --------------------------------------------------------------------------------
#
# SPD-066 closes every path a member spells into a git directory and every repository but a known checkout's own, and
# SPD-063 the program keys of that repository; a member that writes through a program the hook cannot read (python -c, a
# script under its globs, node) can still put a hook in a known checkout's common git directory, a program key in its config
# or a worktree's config.worktree, or a repository below the checkout.  git runs a hook with nothing on the line (probed on
# 2.54.0: post-index-change under any `git status`, reference-transaction under `git fetch`; pre-commit, commit-msg,
# post-commit, post-merge and post-checkout under a commit, a merge or `git worktree add`), whoever runs git: a member, Spud,
# the harness, Eric in his terminal.  So these are the findings a repository in a known checkout can hold, and every reader
# asks for them here: the Bash hook before a member's git call and Spud's own, doctor, and the line the board adds.
FINDING_HOOK = "%s, an entry of the repository's hooks directory that is not a *.sample file"
FINDING_HOOKS_UNLISTED = "%s, the repository's hooks directory, which the hook cannot list"
FINDING_KEY = "%s at the %s scope (%s, or a file it includes), a key that names or enables a program git runs"
FINDING_FOREIGN = ("the repository in %s%s, which is not the own repository of a checkout the ledger knows -- a .git or a bare"
                   " layout below a checkout's root, or a .git gitfile or commondir naming another repository; git runs its"
                   " hooks and reads its config")
FINDING_UNREADABLE = ("the config at the local and worktree scopes of the repository in %s, which the hook cannot read"
                      " (`git config --list --show-scope --name-only` failed there)")
TO_DO = "Inspecting and removing it is Eric's call: Spud asks him, does not delete it, and runs no git there until it is gone"


def git_hook_entries(commondir):
    """(every entry of <commondir>/hooks that is not a plain file named *.sample, whether the directory could be listed):
    one scandir.  Whatever its mode, since git runs a hook once it is executable and a program can set the bit; a directory
    or a symlink named like a sample is refused too, which fails closed and costs nothing real.  No hooks directory, or a
    file where it should be, is no hook at all."""
    hooks = os.path.join(commondir, "hooks")
    try:
        with os.scandir(hooks) as entries:
            return sorted(os.path.join(hooks, e.name) for e in entries
                          if not (e.name.endswith(".sample") and e.is_file(follow_symlinks=False))), True
    except (FileNotFoundError, NotADirectoryError):
        return [], True
    except OSError:
        return [hooks], False


def repository_findings(home, known, where, gitdir, commondir, hooks=None):
    """[finding] for one repository git_repository_dirs found, in the order the Bash hook refuses a member (SPD-066's
    repository, then SPD-063's keys, then SPD-123's hooks), each a dict: kind (foreign, unreadable, key, hook), what (the
    repository, the key or the hook's path), file, and text, the phrase every reader shows.  A repository that is not a
    known checkout's own is its one finding: git runs nothing there to read its keys, and removing it is Eric's call.

    `hooks`: a dict a reader of many checkouts passes to list one common directory's hooks once, since a checkout and every
    worktree of it share it (checkout_findings); a hook call reads one repository and passes none."""
    if not git_checkout_repository(known, where, gitdir, commondir):
        named = "" if gitdir == where else " (its git directory %s)" % gitdir
        return [{"kind": "foreign", "what": where, "file": gitdir, "text": FINDING_FOREIGN % (where, named)}]
    out = []
    keys = git_own_config_keys(home, where, gitdir, commondir)
    if keys is None:
        out.append({"kind": "unreadable", "what": where, "file": os.path.join(commondir, "config"), "text": FINDING_UNREADABLE % where})
    else:
        for scope, key in keys:
            if git_config_key_names_program(key):
                source = os.path.join(gitdir, "config.worktree") if scope == "worktree" else os.path.join(commondir, "config")
                out.append({"kind": "key", "what": key, "scope": scope, "file": source, "text": FINDING_KEY % (key, scope, source)})
    if hooks is None or commondir not in hooks:
        entries, listed = git_hook_entries(commondir)
        if hooks is not None:
            hooks[commondir] = (entries, listed)
    else:
        entries, listed = hooks[commondir]
    for path in entries:
        out.append({"kind": "hook", "what": path, "file": path, "text": (FINDING_HOOK if listed else FINDING_HOOKS_UNLISTED) % path})
    return out


def checkout_findings(ctx, con):
    """(the checkouts read, [(project row, checkout, finding)]) for every checkout the ledger knows (project_checkouts: the
    home, each active project's root and its listed worktrees), each repository read once: what doctor lists and the
    board's line names.  A checkout whose directory is gone, or whose repository lies outside every known checkout (the home,
    which is no repository, inside one that is), is not read.  Raises HookError when a worktree list git cannot give."""
    checkouts = worktrees.project_checkouts(ctx, con)
    known = checkout_identities(checkouts)
    read, found, seen, hooks = [], [], set(), {}
    for project, paths in checkouts:
        for root in paths:
            if not os.path.isdir(root):
                continue
            where, gitdir, commondir = git_repository_dirs(root)
            if gitdir is None:
                continue
            mine = worktrees.file_identity(where) or os.path.normpath(where)
            if mine in seen:
                continue
            seen.add(mine)
            if not in_known_checkout(ctx.home, known, where):
                continue
            read.append(root)
            found.extend((project, root, f) for f in repository_findings(ctx.home, known, where, gitdir, commondir, hooks))
    return read, found


PLANTED_LINE = ("repository check: git would run what a member may have planted, with nothing on the line, in %s"
                " (`spud doctor` names it; %s)")
PLANTED_UNREAD_LINE = "repository check: the checkouts could not be read (%s); `spud doctor` says more"


def planted_lines(ctx, con):
    """The line `spud board --brief` adds and a Spud session's SessionStart context carries above the board when a checkout
    the ledger knows holds a finding, naming each such checkout -- none when every one is clean, so a board with no such line
    means git runs nothing planted in any of them.  The harness runs git no hook sees (its session-start `git status`,
    EnterWorktree's `git worktree add`), and so does Eric, so the next session learns of it here."""
    try:
        _read, found = checkout_findings(ctx, con)
    except hookio.HookError as e:
        return (PLANTED_UNREAD_LINE % e,)
    names = []
    for _project, root, _finding in found:
        if root not in names:
            names.append(root)
    return (PLANTED_LINE % (", ".join(names), TO_DO),) if names else ()
