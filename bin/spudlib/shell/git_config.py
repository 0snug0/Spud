"""shell/git_config: The repository a git call reads, as the Bash rule answers for it: a member's must be a known checkout's own and set no program for itself, and nobody's may hold what a member planted.  The reading itself is hooks/gitrepos."""

from . import bash_rule, git_verbs, prepare
from ..hooks import gitrepos, worktrees


# the config a repository sets for itself, which git reads with nothing on the line (hooks/gitrepos reads it).
GIT_SCOPE_REASON = (
    "Law 7: this git call reads a repository that sets %s at its own %s scope (the repository in %s), a config key that"
    " names or enables a program git runs -- a pager, editor, ssh or proxy command, diff or merge driver, hooks path,"
    " credential or askpass helper, the ext:: transport -- under a verb Law 7's table allows. git reads that file with"
    " nothing on the line, and a member can write such a file under its own deliverables, so a write can run under a verb"
    " the table does not list. Run git in a checkout that does not set it; Spud commits, after the outcome is recorded")
GIT_SCOPE_UNRESOLVED_REASON = (
    "Law 7: this git call runs in a directory the hook cannot follow (a cd into a variable the line does not settle, `cd"
    " -`, popd, a directory stack entry or ~name, an option or a CDPATH it cannot read, a relative cd in a loop, a sourced"
    " file, a cd a DEBUG, ERR or RETURN trap makes, one into a name after setopt or shopt, text nested past the hook's depth"
    " -- on the line, or in a shell function it calls), so it cannot read the config in force at the local and worktree scopes of the repository git would read there -- the keys that name a"
    " program git runs under a verb Law 7's table allows. The hook fails closed: run git from an absolute path inside the"
    " session's own checkout; Spud commits, after the outcome is recorded")
GIT_SCOPE_UNREADABLE_REASON = (
    "Law 7: the hook cannot read the config in force in the repository this git call reads (`git config --list --show-scope"
    " --name-only` in %s), so it cannot tell whether that repository sets a key naming a program git runs under a verb"
    " Law 7's table allows; the hook fails closed. Spud commits, after the outcome is recorded")


# the repository itself.  One check refuses a repository a line names outside every checkout the ledger knows, and
# another reads the keys a repository sets itself; neither sees a repository nested in a checkout -- a .git built below
# its root by an interpreter, cp or mkdir, which the path rule cannot read, or a bare layout (HEAD, objects/, refs/, hooks/)
# written under a member's globs, which no path names -- and git runs such a repository's hooks with nothing on the line
# (probed on 2.54.0: `git status` runs post-index-change, `git fetch` reference-transaction; pre-auto-gc was not reached,
# fetch's auto maintenance running gc without it) and reads its config, attributes and index.  So the repository a member's
# git call reads must be a known checkout's own: discovered, its work tree is a registered project's root or a listed
# worktree, reached through that root's own .git; named as the git or common directory, or found as a bare layout, it is
# the git or common directory such a root's .git names.  This is the outside-repository rule carried one level down, and
# it closes hooks, config, attributes and the index at once, with the one walk the config check already makes and no
# git run.
GIT_FOREIGN_REPOSITORY_REASON = (
    "Law 7: this git call reads the repository at %s (%s), which is not the own repository of a checkout the ledger knows"
    " -- a registered project's root or one of its listed worktrees, read through that root's own .git -- and %s. git runs"
    " such a repository's hooks with nothing on the line (post-index-change under `git status`, reference-transaction under"
    " `git fetch`) and reads its config, attributes and index, and a repository nested in a checkout -- a .git built below"
    " its root, a gitfile, or a bare layout of HEAD, objects/ and refs/ -- is one a member can build under its own"
    " deliverables, so a program can run under a verb Law 7's table allows. Run git in the checkout's own repository; Spud"
    " commits, after the outcome is recorded")


# what a member can plant in a known checkout's own repository through an interpreter the hook cannot read (python -c, a
# script under its globs, node) -- a hook in the common directory, a program key in its config or in a worktree's
# config.worktree, a repository below the checkout -- which git runs with nothing on the line under anyone's git call.  A
# member keeps every refusal it had, in the same order, and is refused a planted hook last; Spud, whose own git runs as
# Eric's, is refused all three for a repository in a known checkout, and nothing else: a repository outside every known
# checkout is his own business (the outside-repository rule is a member's) and a directory the hook cannot follow is his
# own spelling.
GIT_PLANTED_HOOK_REASON = (
    "Law 7: this git call reads the repository in %s, which holds %s. git runs a hook from there with"
    " nothing on the line (post-index-change under `git status`, reference-transaction under `git fetch`), and a member can"
    " write one through an interpreter the hook cannot read (python -c, a script, node). Inspecting and removing it is Eric's"
    " call: Spud asks him, and nobody runs git in that repository until it is gone. Spud commits, after the outcome is recorded")
GIT_SPUD_PLANTED_REASON = (
    "this git call reaches the repository in %s, in a checkout the ledger knows, and git would run what it holds under this"
    " call with nothing on the line: %s. A member may have written it through an interpreter no hook can read (python -c, a"
    " script, node). %s; `spud doctor` lists every such finding")


def git_call_directories(targets, cwds):
    """([(directory, named as a git or common directory, the spelling that named it or None)], whether the hook cannot
    resolve one) for one git call: every target git_repo_targets found, and the directories the shell may be in whenever
    neither -C nor --git-dir/GIT_DIR is on the line, since git then discovers the repository there -- --work-tree,
    GIT_WORK_TREE and GIT_COMMON_DIR do not stop that (the config check read only the targets when there were any)."""
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


def git_foreign_repository_reason(ctx, con, directory, spelled, where):
    """GIT_FOREIGN_REPOSITORY_REASON for the repository at `where` (its work tree, or the git directory itself when it was
    taken as one) found from `directory`: naming it, how the call reached it, and the checkout it lies in, if any."""
    how = prepare.deglob(spelled) if spelled else "discovered from %s" % directory
    placed = worktrees.project_paths(ctx, con, where, None)
    lies = ("it lies in the checkout %s" % placed[0][1]) if placed else "it lies in no checkout the ledger knows"
    return GIT_FOREIGN_REPOSITORY_REASON % (where, how, lies)


def git_repository_reason(ctx, con, targets, cwds):
    """The reason a member's git call is refused for the repository it reads, or None: a repository that is not a known
    checkout's own, then the config it sets for itself, then a hook planted in it.  The
    repository is the one `-C`, `--git-dir`, GIT_DIR and friends name (git_repo_targets) and, when git discovers
    it, the one containing each directory the shell may be in.  A target outside every checkout the ledger knows already
    has the outside-repository refusal, which is read first."""
    call_dirs, unresolved = git_call_directories(targets, cwds)
    if unresolved:
        return GIT_SCOPE_UNRESOLVED_REASON
    known = None
    for directory, as_git_dir, spelled in call_dirs:
        where, gitdir, commondir = gitrepos.git_repository_dirs(directory, as_git_dir)
        if gitdir is None:
            continue  # no repository there: git reads no config of its own, runs no hook, and the hook runs nothing
        if known is None:
            known = gitrepos.checkout_identities(worktrees.project_checkouts(ctx, con))
        for finding in gitrepos.repository_findings(ctx.home, known, where, gitdir, commondir):
            kind = finding["kind"]
            if kind == "foreign":
                return git_foreign_repository_reason(ctx, con, directory, spelled, where)
            if kind == "unreadable":
                return GIT_SCOPE_UNREADABLE_REASON % where
            if kind == "key":
                return GIT_SCOPE_REASON % (finding["what"], finding["scope"], where)
            return GIT_PLANTED_HOOK_REASON % (where, finding["text"])
    return None


def git_spud_repository_reason(ctx, con, targets, cwds):
    """The reason Spud's own git call is refused, or None: each repository it reaches that lies in a checkout the
    ledger knows is read for what a member could have planted there -- a repository that is not the checkout's own, a
    program key at its local or worktree scope, a hook that is not a sample -- and every finding of the first such
    repository is named.  A repository outside every known checkout and a directory the hook cannot resolve stay silent:
    the line is Spud's own, and the check is about files a member planted.  The same reading as a member's, so it costs
    what that one costs: project_checkouts only once a repository is found, one scandir, and git only when a config file
    changed."""
    call_dirs, _unresolved = git_call_directories(targets, cwds)
    known = None
    for directory, as_git_dir, _spelled in call_dirs:
        where, gitdir, commondir = gitrepos.git_repository_dirs(directory, as_git_dir)
        if gitdir is None:
            continue
        if known is None:
            known = gitrepos.checkout_identities(worktrees.project_checkouts(ctx, con))
        if not gitrepos.in_known_checkout(known, where):
            continue  # a scratch clone, a repository Eric keeps elsewhere: Spud's own business
        findings = gitrepos.repository_findings(ctx.home, known, where, gitdir, commondir)
        if findings:
            what = "; ".join(f["text"] for f in findings)
            return GIT_SPUD_PLANTED_REASON % (where, what, gitrepos.TO_DO)
    return None
