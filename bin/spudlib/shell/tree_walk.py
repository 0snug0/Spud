"""shell/tree_walk: the walk that holds a copied tree's git directory to SPD-066's rule (SPD-126).

A recursive copy or move -- cp -R, mv, rsync, ditto -- lands its source's files wherever it lands, and find hands its command
the files already under its starting points, so a `.git` directory or gitfile, a `.gitconfig` or a `git/config` in that
tree is written where the tree lands, and SPD-066 refuses a caller the path rule holds any such write: git runs a hook from
there, and reads its config, with nothing on the line.  arg_writes.written_paths asks this module which directories a
source word names now and what git reads under each, and turns each find into a target of its own where it lands.

first_git_entry looks for one by name, following no symlink below the root, and fails closed -- the walk stops short -- at
syntax.GLOB_SCAN_CAP entries or at a directory it cannot list: the names a copy lands are the source's, and the source is on
disk to read.  A plain rsync --exclude of a name is honoured, as rsync(1) matches a pattern with no `/` against each name, so
`rsync -a --exclude .git checkout/ scratch/` lands no git directory; any other filter leaves the walk reading everything.

A module of its own, apart from shell/tree_writes' command grammars, because its one caller is written_paths and its one
concern the git directory a copied tree carries, whichever command copies it."""

import os
import re

from . import arg_writes, bash_rule, globbing, prepare, redirect_globs, syntax


def directory_sources(source, cwds):
    """[(the name a source takes inside a destination directory, as a masked word; its path)] for each path the source word
    names that is a directory now, followed if a symlink, as the tools that copy a directory named on their line follow it:
    the word's own last segment (`.` for `src/.`, whose contents land in the destination itself), or for a glob each
    directory it matches, by its name.  A word the hook cannot resolve names none."""
    if arg_writes.unresolved(source):
        return []
    text = source.rstrip("/") or source
    if bash_rule.target_has_active_glob(text):
        expansion = redirect_globs.expand_redirect_target(text, cwds)
        paths = expansion[0] if expansion is not None else []
        named = [(globbing.literalize(os.path.basename(os.path.normpath(p))), p) for p in paths]
    else:
        last = text.rpartition("/")[2]
        if last.startswith("~") and last == text:
            last = globbing.literalize(os.path.basename(os.path.expanduser(prepare.deglob(last))))
        named = [(last, p) for p in bash_rule.redirection_paths(prepare.deglob(text), cwds) or []]
    return [(name, os.path.expanduser(path)) for name, path in named if os.path.isdir(os.path.expanduser(path))]


def excluded(name, is_dir, excludes):
    """True when an rsync --exclude pattern matches this name as rsync(1) matches a pattern with no `/` but a trailing one
    (which asks for a directory): against the final component, `*` and `?` over one name.  A pattern holding `/`, `**` or a
    bracket class is not honoured -- the walk reads what it would exclude -- so no reading of a class can skip a `.git` the
    copy lands."""
    for pattern in excludes:
        dirs_only = pattern.endswith("/")
        pattern = pattern.rstrip("/")
        if not pattern or "/" in pattern or "**" in pattern or "[" in pattern or (dirs_only and not is_dir):
            continue
        if re.fullmatch("".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in pattern), name):
            return True
    return False


def git_name(name, parent):
    """True when an entry named `name` in a directory named `parent` is one git reads with nothing on the line wherever it
    lands (hooks/pathrule's GIT_DIR_COMPONENT and git config files, case-folded as they are there)."""
    folded = name.casefold()
    return folded in (".git", ".gitconfig") or (folded in ("config", "config.worktree") and parent.casefold() == "git")


def first_git_entry(root, excludes=None):
    """(the path, relative to the directory `root`, of the first entry under it that git reads with nothing on the line --
    a `.git` directory or gitfile, a `.gitconfig`, a `config` in a directory named `git` -- or None; whether the walk
    stopped short, at syntax.GLOB_SCAN_CAP entries or at a directory it cannot list).  Depth first, following no symlink
    below the root (a link named `.git` is found by its name), skipping what `excludes` (rsync's) excludes."""
    stack, seen = [("", root)], 0
    while stack:
        rel, path = stack.pop()
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError:
            return None, True
        parent = os.path.basename(path)
        for entry in entries:
            seen += 1
            if seen > syntax.GLOB_SCAN_CAP:
                return None, True
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            if excludes and excluded(entry.name, is_dir, excludes):
                continue
            sub = rel + "/" + entry.name if rel else entry.name
            if git_name(entry.name, parent):
                return sub, False
            if is_dir:
                stack.append((sub, entry.path))
    return None, False
