"""hooks/worktrees: Case folding, worktrees and project checkouts, file identity, path readings.  Moved from bin/spud_ledger.py (SPD-065)."""

import contextlib
import json
import os

from . import hookio
from ..core import homeconf, kernel, lazy


_CASE_CACHE = {}


def case_insensitive_fs(path):
    """True when the filesystem holding `path` ignores case (macOS by default): then
    `Ledger/x` is `ledger/x` and the path rule must fold case (Rooster's HIGH-2)."""
    key = str(path)
    if key not in _CASE_CACHE:
        swapped = key.swapcase()
        try:
            _CASE_CACHE[key] = swapped != key and os.path.exists(swapped) and os.path.samefile(key, swapped)
        except OSError:
            _CASE_CACHE[key] = False
    return _CASE_CACHE[key]


def folds_case(root):
    """case_insensitive_fs for a project root, asked per root (a worktree elsewhere may sit on a case-sensitive volume)
    of the root itself or, when it does not exist yet, of its nearest existing ancestor, whose filesystem it will be on."""
    cur = str(root)
    while not os.path.isdir(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return case_insensitive_fs(cur)
_WORKTREES = {}  # home -> its worktree list, once per process


def worktrees_fingerprint(home):
    """What changes when a worktree of the home is added, moved, repaired or removed, read without running git: the
    stat of <home>/.git/worktrees and of each worktrees/<id>/gitdir, the file gitrepository-layout(5) documents as
    the path back to that worktree.  None when <home>/.git is not a directory (a gitfile), where only git can say."""
    git_dir = os.path.join(str(home), ".git")
    if not os.path.isdir(git_dir):
        return None
    admin = os.path.join(git_dir, "worktrees")
    try:
        names = sorted(os.listdir(admin))
        stamp = os.stat(admin).st_mtime_ns
    except FileNotFoundError:
        return ["no linked worktrees"]
    out = [stamp]
    for name in names:
        try:
            st = os.stat(os.path.join(admin, name, "gitdir"))
            out.append([name, st.st_mtime_ns, st.st_size])
        except FileNotFoundError:
            out.append([name, None, None])
    return out


def git_worktree_list(home):
    """The paths `git worktree list --porcelain` names for the home, the main checkout first.  Raises HookError when
    git cannot give them, which fails an enforcing hook closed."""
    env = {k: v for k, v in os.environ.items() if k not in homeconf.GIT_REDIRECTS}
    try:
        proc = lazy.subprocess.run(["git", "-C", str(home), "worktree", "list", "--porcelain", "-z"], capture_output=True, env=env, timeout=10)
    except (OSError, lazy.subprocess.TimeoutExpired) as e:
        raise hookio.HookError("cannot list the worktrees of %s, so no path can be checked against them: %s" % (home, e))
    if proc.returncode != 0:
        raise hookio.HookError("cannot list the worktrees of %s, so no path can be checked against them: git worktree list exited %d: %s"
                        % (home, proc.returncode, os.fsdecode(proc.stderr).strip()))
    return [os.fsdecode(field[len(b"worktree "):]) for field in proc.stdout.split(b"\0") if field.startswith(b"worktree ")]


def project_root(ctx, project):
    """A project's main checkout: the home for project 1, whatever path its row recorded, else the row's root_path."""
    return str(ctx.home) if project["id"] == 1 else project["root_path"]


def checkout_worktrees(ctx, project):
    """Every worktree git names for a project's root ([] when the root has no .git of its own, or is gone), kept in
    <home>/.spud/worktrees/<key>.json under the fingerprint it was listed at, so a hook runs git only after the
    worktrees change (about 8 ms with the subprocess import).  A cache that is missing, unreadable, stale or listed for
    another root is listed anew; one that cannot be written is left unwritten (SPD-016, per project since SPD-014)."""
    root = project_root(ctx, project)
    if root in _WORKTREES:
        return _WORKTREES[root]
    if not os.path.lexists(os.path.join(root, ".git")):
        _WORKTREES[root] = []
        return []
    fingerprint = worktrees_fingerprint(root)
    state = os.path.join(str(ctx.home), hookio.STATE_DIR)
    cache = os.path.join(state, "worktrees", "%s.json" % project["key"])
    listed = None
    if fingerprint is not None:
        with contextlib.suppress(OSError, ValueError):
            with open(cache, encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict) and stored.get("root") == root and stored.get("fingerprint") == fingerprint \
                    and isinstance(stored.get("worktrees"), list) and all(isinstance(w, str) for w in stored["worktrees"]):
                listed = stored["worktrees"]
    if listed is None:
        listed = git_worktree_list(root)
        if fingerprint is not None and os.path.isdir(state):
            tmp = "%s.%d.tmp" % (cache, os.getpid())
            with contextlib.suppress(OSError):
                os.makedirs(os.path.dirname(cache), exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"root": root, "fingerprint": fingerprint, "worktrees": listed}, f)
                os.replace(tmp, cache)
            with contextlib.suppress(OSError):
                os.unlink(tmp)
    _WORKTREES[root] = listed
    return listed


def project_checkouts(ctx, con):
    """[(project row, [root, *worktrees])] for every active project, the home first."""
    return [(p, [project_root(ctx, p), *checkout_worktrees(ctx, p)])
            for p in con.execute("SELECT * FROM projects WHERE archived_at IS NULL ORDER BY id").fetchall()]


def project_of_path(ctx, con, path, roots=None):
    """(project row, checkout root, repository-relative path) for the project a path is in, by its lexical and then its
    real reading; None outside every active project."""
    roots = project_checkouts(ctx, con) if roots is None else roots
    p = os.path.abspath(os.path.expanduser(path))
    for candidate in (os.path.normpath(p), os.path.realpath(p)):
        mapped = map_into_checkouts(roots, candidate)
        if mapped:
            return mapped
    return None


def cli_project_of(ctx, con, path):
    """project_of_path for a command: a worktree list git cannot give is the command's error, not a hook's."""
    try:
        return project_of_path(ctx, con, path)
    except hookio.HookError as e:
        raise kernel.SpudError(kernel.EXIT_ERROR, str(e))


def file_identity(path):
    """(st_dev, st_ino) of what `path` names, symlinks followed; None when there is nothing to stat.  Every spelling the
    filesystem resolves to one directory has its identity: a case variant or an NFD spelling on APFS, a simple case
    fold (U+017F for s, U+212A for k), the /System/Volumes/Data firmlink prefix on macOS, a symlink (SPD-029)."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return st.st_dev, st.st_ino


def same_entry(base, spelled, canonical):
    """True when the components `spelled` under `base` name the directory the components `canonical` do: the same
    strings, the same file, or, where that directory does not exist yet on a case-insensitive filesystem, the same
    strings case-folded."""
    if spelled == canonical:
        return True
    ident = file_identity(os.path.join(base, *canonical))
    if ident is not None:
        return file_identity(os.path.join(base, *spelled)) == ident
    return case_insensitive_fs(base) and [s.casefold() for s in spelled] == [c.casefold() for c in canonical]


def map_into_checkouts(roots, path):
    """(project row, checkout root, repository-relative path) when `path` (absolute, normalized) lies in a checkout of an
    active project: its root or a worktree git names for it (SPD-016), wherever it is, or a directory under
    .claude/worktrees/<name>/ of one; None when outside.  `roots` is project_checkouts().  The root nearest the path wins,
    so a worktree inside a root maps to itself, not to that root (SPD-014: every project's roots in one search).

    A root is found by file identity, not by spelling (SPD-029): the nearest existing ancestor of the path whose
    (st_dev, st_ino) is a root's, the components below it being the repository-relative path, so any spelling of the
    root the filesystem honours is the root.  A root with nothing to stat (a worktree git still lists after its
    directory went) is matched by spelling as before.  A generated root of the home spelled another way (Ledger,
    reportſ) is named ledger or reports when it is the same directory; the state directory is named so under every root."""
    idents, spelled = {}, []
    for project, checkouts in roots:
        for root in checkouts:
            ident = file_identity(root)
            if ident is not None:
                idents.setdefault(ident, (project, root))
            else:
                for b in (root, os.path.realpath(root)):
                    if all(b != s for _, s in spelled):
                        spelled.append((project, b))
    best = None
    below, cur = [], path
    while True:
        ident = file_identity(cur)
        if ident is not None and ident in idents:
            best = idents[ident] + (below[::-1],)
            break
        parent, name = os.path.split(cur)
        if parent == cur:
            break
        below.append(name)
        cur = parent
    for project, base in spelled:
        rel = os.path.relpath(path, base)
        if rel == ".." or rel.startswith(".." + os.sep):
            continue
        parts = [] if rel == "." else rel.split(os.sep)
        if best is None or len(parts) < len(best[2]):
            best = (project, base, parts)
    if best is None:
        return None
    project, base, parts = best
    if len(parts) > 3 and same_entry(base, parts[:2], [".claude", "worktrees"]):
        base, parts = os.path.join(base, ".claude", "worktrees", parts[2]), parts[3:]
    named = (hookio.GENERATED_ROOTS + (hookio.STATE_DIR,)) if project["id"] == 1 else (hookio.STATE_DIR,)
    if parts and parts[0] not in named:
        for canonical in named:
            if same_entry(base, parts[:1], [canonical]):
                parts = [canonical] + parts[1:]
                break
    return project, base, "/".join(parts)


def path_readings(path, cwd):
    """Every reading of `path` the kernel could give: the lexical path and, when a symlink changes it, the real one.
    Relative paths resolve against the payload's cwd.  The real path is taken of the path as given too, since the kernel
    resolves a symlink before a `..` after it (tests/link/.. is the link target's parent) where normpath drops the pair
    (SPD-029)."""
    p = os.path.expanduser(path) if path.startswith("~") else path
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    lexical = os.path.normpath(p)
    candidates = [lexical]
    for real in (os.path.realpath(lexical), os.path.realpath(p)):
        if real not in candidates:
            candidates.append(real)
    return candidates


def path_placements(ctx, con, path, cwd):
    """(inside, outside) for every reading of `path`: the ones that land in a project's checkout, as (project row, root,
    repository-relative path), and the ones that land in no registered project at all, as absolute paths (SPD-064)."""
    roots = project_checkouts(ctx, con)
    inside, outside = [], []
    for c in path_readings(path, cwd):
        mapped = map_into_checkouts(roots, c)
        if mapped is None:
            if c not in outside:
                outside.append(c)
        elif all((mapped[0]["id"], mapped[1], mapped[2]) != (o[0]["id"], o[1], o[2]) for o in inside):
            inside.append(mapped)
    return inside, outside


def project_paths(ctx, con, path, cwd):
    """Every reading of `path` that lands inside a project's checkout, as (project row, root, repository-relative path)."""
    return path_placements(ctx, con, path, cwd)[0]
