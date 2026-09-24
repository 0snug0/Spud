"""hooks/pathrule: Laws 1 and 5 over a path: globs, outside roots, git config files and git directories, the state directory, edit_reason."""

import json
import os
import re

from . import hookio, worktrees
from ..core import kernel
from ..state import lookup, ops


# -- paths: the rule of Law 5 (deliverables) and Law 1 (Spud's own set) ---------------


# A bracket is a literal character in a deliverable glob, never a character class.  When `[` opened a class, as a shell
# glob's does, `admin/src/app/accounts/[email]/**` -- a Next.js dynamic route segment, a directory genuinely named
# `[email]` -- compiled to a class over e, m, a, i and l and matched no real path at all, and a member was refused its own
# page.tsx in the words of its own brief.  Deliverable globs are written by Spud and by parents through `member new`,
# never by a shell: of every member ever planned, the only globs holding a bracket were one ticket's three, all three a
# literal segment (one of them the `?email?` workaround this bug forced); no class is documented anywhere, and
# SPUD_PATHS holds none.  Class support was also unsafe on the hook path, where this runs in every Write and Edit: the
# shell's own escape `[[]email[]]` compiled to an unterminated set, so path_matches_glob raised re.PatternError instead of
# answering.  Escaping both ways round (option 1) would have needed `normalize_bare_deliverable` to stop folding `\` into
# `/` as well, and would still leave every parent owing a spelling nobody writes.  With brackets literal every character
# is either a wildcard or re.escape'd, so glob_to_regex is total: no glob it accepts can fail to compile.
def glob_to_regex(glob):
    """Repository-relative globs: `*` and `?` stay inside a path segment, `**` crosses segments, and every other
    character -- `[` and `]` included -- is literal, so a `[segment]` directory is written plainly."""
    i, n, out = 0, len(glob), []
    while i < n:
        c = glob[i]
        if c == "*":
            if glob.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
            elif glob.startswith("**", i):
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return "^" + "".join(out) + "$"


# A character of a name a command picks for itself -- mktemp's X, split's suffix letter, a numbered backup's
# digit -- in a write target the Bash hook records (shell/spelled_writes).  It is no character a glob spells, so every
# wildcard of a deliverable glob matches it and no literal does: a glob matches a target holding it only when it matches
# every name the command may pick there.  NAME_MORE stands for any number more of them (split's suffix grows past its
# initial length, a numbered backup's number has as many digits as it needs).  Private-use characters, as the shell
# modules' own sentinels are; a reason shows them as `?` and `*`.
NAME_CHAR, NAME_MORE = chr(0xE053), chr(0xE054)


def path_matches_glob(rel, glob, fold=False):
    if glob.endswith("/"):
        glob += "**"
    if NAME_MORE in rel:
        # A run of picked characters of any length.  Read one at a time, the glob's matcher settles within the
        # glob's own length -- each state that is not a star's either dies or reaches one, and a star keeps what it has --
        # so a run longer than the glob changes nothing, and the lengths up to it and one more decide every length.
        return all(re.fullmatch(glob_to_regex(glob), rel.replace(NAME_MORE, NAME_CHAR * n), re.IGNORECASE if fold else 0)
                   is not None for n in range(len(glob) + 2))
    return re.fullmatch(glob_to_regex(glob), rel, re.IGNORECASE if fold else 0) is not None


# The directory a glob covers, which no match of it names.  path_matches_glob reads `X/**` as `X/` plus something, so a
# member whose deliverable is `test/fixtures/movecheck/**` may write every file under that directory and was refused the
# `mkdir -p` that makes it (Law 5).  While the Bash hook did not read a mkdir the question never arose; now it is the
# natural first line of a member's work, and a differential over the 3477 commands spudagents had run held five refusals
# of exactly this shape.  Spud's decision is two readings, one per direction, and they are not symmetrical:
# making a directory writes no content, so an ancestor of the directory is as harmless as the directory itself, while
# removing one takes everything under it with it.  The kind of write comes from the command (shell/arg_writes): only
# mkdir's operands, install -d's (with no -m, -o or -g, which chmod or chown one that exists), rmdir's and rm's under
# -r, -R or -d carry one (the "tree" reading, below, takes rm -r and the commands whose files the line does not spell),
# and every other write of the same path --
# touch, cp, mv, ln, tee, sed -i, a redirection, a Write or an Edit -- is a file and keeps the reading it had.
def glob_directory(glob):
    """A glob's literal directory prefix: its leading segments that hold no wildcard, dropping the segment the first
    wildcard is in, and, when the glob holds none, its last segment, which is the file it names.  So
    `test/fixtures/movecheck/**` gives `test/fixtures/movecheck`, `admin/src/*.ts` gives `admin/src`, `bin/spud` gives
    `bin` and `dist/` gives `dist`, while a glob that starts with a wildcard (`**/x`, `*.md`) or names a single segment
    gives ``, which is no directory at all.  A bracket is a literal character, never a class, so `[email]` is
    an ordinary segment."""
    segments = glob.split("/")
    for i, segment in enumerate(segments):
        if "*" in segment or "?" in segment:
            return "/".join(segments[:i])
    return "/".join(segments[:-1])


def glob_covers_directory(rel, glob, how, fold=False):
    """Whether one deliverable glob lets its member make (`how` "make") or remove ("remove") the directory `rel`, which no
    match of that glob names, or write anywhere under it ("tree").  Making it: `rel` is the glob's literal directory prefix
    or an ancestor of it, since an empty directory holds nothing the glob does not already cover.  Removing it: the glob
    covers the whole subtree -- it is `D/**` or `D/` -- and `rel` is exactly D; an ancestor is never inside, so `rm -rf
    test` with `test/fixtures/movecheck/**` removes files no glob covers and stays Law 5.  `fold` folds case where the
    filesystem does, as path_matches_glob's re.IGNORECASE does for a match.  The reading is the glob's alone: nothing here
    stats the path, so a member is answered the same before and after it has made its own directory.

    The "tree" reading: a write that may put a file anywhere under `rel`, or remove everything there -- what find runs and
    deletes, a recursive copy's contents, an archive extracted, a download named by the server -- is inside only when this
    one glob matches every path under `rel`: `**` itself, or a glob ending in `/**` (or `/`) that matches `rel` or whose
    literal root is `rel` (`tests/**` covers `tests` and `tests/tmp`).  A glob that matches `rel` but not everything below
    it (`bin/*`, `docs/*.md`) never covers the subtree, although path_matches_glob matches `rel` itself; nor does one
    whose root holds a wildcard, at that root (`a/*/**` covers `a/x/y`, not `a/x`).  So "tree" never lets in what
    "remove" refuses, and rm -r, once read as "remove", is only ever narrowed."""
    if fold:
        rel = rel.casefold()
        glob = glob.casefold()
    if how == "tree":
        if glob.endswith("/"):
            glob += "**"
        return glob == "**" or (glob.endswith("/**") and (rel == glob[:-3] or path_matches_glob(rel, glob)))
    if how == "remove":
        whole = glob[:-1] if glob.endswith("/") else glob[:-3] if glob.endswith("/**") else None
        return whole is not None and rel == (whole.casefold() if fold else whole)
    prefix = glob_directory(glob)
    if fold:
        prefix = prefix.casefold()
    return bool(prefix) and (rel == prefix or prefix.startswith(rel + "/"))


HARNESS_FILES_RE = re.compile(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/subagents/", re.IGNORECASE)


def harness_file(path):
    """The harness's subagent transcripts and meta files (<project>/<session>/subagents/...):
    the ledger binds identities from them, so nobody but Claude Code writes them."""
    p = path.replace("\\", "/")
    return HARNESS_FILES_RE.search(p) is not None or re.search(r"/agent-[0-9a-f]+\.(?:meta\.json|jsonl)$", p, re.IGNORECASE) is not None


# The path rule holds a member to its deliverable globs inside a registered project; outside every project edit_reason
# once returned no reason at all, so a bound member could Write, Edit or redirect into ~/.gitconfig (which git reads
# with nothing on the line), ~/.claude/settings.json and ~/.claude/agents/ (the user-level hooks,
# permissions and the spudagent definition `project sync --all` writes there), the shell rc files, ~/.ssh, a LaunchAgent,
# or anything else in Eric's home.  Spud's decision is the allowlist: outside every registered project only the harness's
# scratchpad root for this user and the system temp directories stay open, where members run probes and differential
# harnesses; everything else is refused, fail closed, the rule's shape everywhere else.
SCRATCHPAD_ROOT = "/private/tmp/claude-%d"  # the harness's scratchpad root: /private/tmp/claude-<uid>/<project>/<session>/scratchpad
FIXED_TEMP_ROOTS = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")
TEMP_ROOT_VARS = ("TMPDIR", "TMP", "TEMP")  # what tempfile.gettempdir() reads, which the hook path may not import
# The character devices a redirection legitimately opens: /dev/null and the standard streams, which are outside every project
# and are nobody's file (QUIET_TARGETS is the same set for the spud allow; /dev/stdout resolves to /dev/fd/1 on macOS).
DEV_WRITE_ROOTS = ("/dev/null", "/dev/zero", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/fd")
OUTSIDE_PROJECT_REASON = (
    "Law 5: %s is outside every registered project, and a spudagent writes only its deliverables (Law 2, Law 5): outside"
    " every registered project only this session's scratchpad (under %s/) and the system temp directories (%s) are open,"
    " where members run probes and harnesses. Nothing else out there is any member's to write -- not ~/.gitconfig, the"
    " shell rc files, ~/.ssh, ~/.claude/settings.json or ~/.claude/agents/. Write inside your deliverable globs, or in"
    " the scratchpad; ask your parent to extend the globs if the work is really out there")


def outside_roots():
    """The roots outside every registered project a caller with an agent_id may still write under, each in every
    spelling the filesystem honours, since a target is held under one by both its lexical and its real reading (on macOS
    /tmp is /private/tmp and TMPDIR is under /var/folders, which is /private/var/folders)."""
    roots = [SCRATCHPAD_ROOT % os.getuid(), *FIXED_TEMP_ROOTS, *DEV_WRITE_ROOTS]
    for name in TEMP_ROOT_VARS:
        value = os.environ.get(name)
        if value and os.path.isabs(value):
            roots.append(value)
    out = []
    for root in roots:
        for spelling in (os.path.normpath(root), os.path.realpath(root)):
            if spelling not in out and spelling not in ("", os.sep):
                out.append(spelling)
    return out


def under_outside_root(path, roots):
    """True when an absolute, normalized path is one of the allowed outside roots or lies under it."""
    return any(path == root or path.startswith(root + os.sep) for root in roots)


def outside_project_reason(outside):
    """The reason a caller with an agent_id may not write these readings of a target, none of which lands in a registered
    project, or None when every one of them is under an allowed outside root.  Both readings must hold, so a
    symlink planted in the scratchpad that points at ~/.gitconfig is refused, while /tmp and /private/tmp, which resolve
    to each other, stay open."""
    roots = outside_roots()
    for candidate in outside:
        if not under_outside_root(candidate, roots):
            return OUTSIDE_PROJECT_REASON % (candidate, SCRATCHPAD_ROOT % os.getuid(), ", ".join(FIXED_TEMP_ROOTS))
    return None


# The files git reads with nothing on the line.  The outside roots above close a member's writes to ~/.gitconfig and
# $XDG_CONFIG_HOME/git/config, but a repository's own config stays open: a member can craft `.git/config` (or
# `.git/config.worktree`, or a file an `include.path` there names) under its own deliverable globs inside a checkout the
# ledger knows, and `git -C tests/fake status` resolves inside the home, so the refusal of a repository outside every
# known checkout never fires.  Such a file defines aliases git expands into a write verb, and every key that names a
# program git runs (a pager, an editor, a hook path, a helper) under a real verb, so no caller with an agent_id writes
# one, anywhere, its own deliverables included.
GIT_CONFIG_FILE_NAMES = (".gitconfig",)
GIT_CONFIG_FILE_TAILS = ((".git", "config"), (".git", "config.worktree"), ("git", "config"))
GIT_CONFIG_FILE_REASON = (
    "Law 7: %s is a configuration file git reads with nothing on the line (a repository's .git/config and"
    " .git/config.worktree, ~/.gitconfig, $XDG_CONFIG_HOME/git/config). It can define an alias git expands into whatever"
    " command it names before it dispatches, and the keys that name a program git runs -- a pager, editor, ssh or proxy"
    " command, diff or merge driver, hooks path, credential or askpass helper -- under a verb Law 7's table allows, so a"
    " write can run under a verb the table does not list. No spudagent writes one, its own deliverables included; Spud"
    " commits, after the outcome is recorded")


def git_config_file(path):
    """True when `path` (absolute, normalized) names a configuration file git reads by itself: any `.gitconfig`, or a path
    ending in `.git/config`, `.git/config.worktree` or `git/config`.  Matched case-folded, so a case variant or a simple
    fold is refused on every filesystem, as the generated roots are."""
    parts = [p.casefold() for p in path.replace("\\", "/").split("/") if p]
    if parts and parts[-1] in GIT_CONFIG_FILE_NAMES:
        return True
    return any(len(parts) >= len(tail) and parts[-len(tail):] == list(tail) for tail in GIT_CONFIG_FILE_TAILS)


# The rest of a git directory.  The config files are closed above, but git also runs a hook from <gitdir>/hooks with
# nothing on the line and no config key naming it (probed on git 2.54.0: every `git status` runs post-index-change, every
# `git fetch` reference-transaction), reads info/attributes, info/exclude, shallow and the index, and follows a worktree's
# or a submodule's .git gitfile to any git directory it names.  So no caller with an agent_id writes a path with a `.git`
# component, anywhere, its own deliverables included, the way the state directory is refused at any project root.
# A .git/config keeps GIT_CONFIG_FILE_REASON, checked first: it is the more specific reason (an alias as well as a hook).
# A component that merely begins with .git (.gitignore, .github, .gitattributes, .gitmodules) is an ordinary file of the tree.
GIT_DIR_COMPONENT = ".git"
GIT_DIR_PATH_REASON = (
    "Law 7: %s is inside a git directory (a path with a .git component: anything under a .git directory, or the .git"
    " gitfile of a worktree or submodule). git runs a hook from there with nothing on the line -- post-index-change under"
    " `git status`, reference-transaction under `git fetch` -- and reads the attributes, the index and the gitfile with no"
    " config key naming them, so a write there can run a program, or point git at another repository, under a verb Law 7's"
    " table allows. No spudagent writes one, its own deliverables included; Spud commits, after the outcome is recorded")


def git_dir_path(path):
    """True when `path` has a component `.git`: it lies in a git directory, or is a .git gitfile.  Matched case-folded, like
    git_config_file, so a case variant is refused on every filesystem."""
    return any(p.casefold() == GIT_DIR_COMPONENT for p in path.replace("\\", "/").split("/"))


# A write anywhere under a directory (shell/find_xargs, shell/tree_writes) reaches every path below it, and a
# checkout's root below it brings that checkout's .git, and for the home and every project root the ledger's state
# directory, into the write although no reading of the directory's own path names them: `find . -delete` at a root,
# `rm -rf .claude` over the worktrees, `tar -x -C /tmp` above a checkout under /tmp.  So a caller the path rule holds is
# refused such a write whenever a checkout the ledger knows lies at or under it, as writing the .git itself is refused.
TREE_CHECKOUT_REASON = (
    "Law 7: a write anywhere under %s reaches %s, the root of a checkout the ledger knows, and with it that checkout's .git"
    " directory or gitfile (git runs its hooks and reads its config with nothing on the line) and, at a project root, the"
    " ledger's state directory. No spudagent writes a git directory, its own deliverables included; name a directory inside"
    " the checkout, under your deliverables")


def tree_checkout_reason(ctx, con, readings):
    """The reason a write anywhere under one of these readings of a directory is refused, or None: a checkout root
    of the home or a registered project, or one of their worktrees, is that directory or lies under it.  Compared by file
    identity, as map_into_checkouts finds a root, so a case variant, a symlink or the /System/Volumes/Data prefix of the
    directory is the directory; a directory that does not exist yet holds no checkout."""
    idents = [i for i in (worktrees.file_identity(r) for r in readings) if i is not None]
    if not idents:
        return None
    for _project, roots in worktrees.project_checkouts(ctx, con):
        for root in roots:
            cur = str(root)
            while True:
                if worktrees.file_identity(cur) in idents:
                    return TREE_CHECKOUT_REASON % (readings[0], root)
                parent = os.path.dirname(cur)
                if parent == cur:
                    break
                cur = parent
    return None


NOT_SPUD_HOME = ("a session that is not Spud does not write in Spud's home (%s is there); type /spud to make this session Spud,"
                 " or work in a session opened in the home")


OUTSIDE_BOUND_WORKTREE = (
    "Law 5: %(rel)s is in %(checkout)s, a checkout of project %(project)s, and %(ref)s's ticket %(key)s is bound to %(bound)s: a"
    " member of a bound ticket writes its project's paths there alone, never in the main checkout or another worktree. %(tail)s")
OUTSIDE_BOUND_WORKTREE_TAIL = "Write the same path under %s"
OUTSIDE_GONE_WORKTREE_TAIL = ("That worktree is gone, so no checkout is open to you: ask your parent, since Spud rebinds the ticket by"
                              " planning a member from the worktree the work continues in")


def path_reason(rel, member, ref, fold=False, project_key=kernel.HOME_KEY, ticket_project_key=None, home=True, elsewhere=None, directory=None):
    """None when the actor may write the repository path `rel` of project `project_key`, else the reason.  The generated
    roots are the home's alone and are matched whatever the case (a case variant is refused on every filesystem),
    case-folded rather than lower-cased so that the simple folds APFS honours (reportſ is reports) count too; globs fold
    case only where the filesystem does.  In another project Spud has no own files: every path there is a deliverable, and
    a member's bare glob is relative to its ticket's project, a `<key>:<glob>` to that project's, a `home:<glob>` to the
    home.  `home` is worktrees.is_home for the checkout the path was mapped into: the home alone carries the generated
    roots and Spud's own set.  `elsewhere` is (ticket key, bound worktree, checkout) when the member's ticket is bound and
    the path lies in another checkout of the ticket's own project: no glob of the member's matches there, whatever it
    says.  `directory` is "make" or "remove" when the write only makes or removes a directory, which a glob covers without
    matching; it is read inside the glob loop, so a glob's scope and its case folding hold for it exactly as they hold for
    a match, and every refusal before the loop -- the generated roots, a bound ticket's other checkouts -- wins over it as
    it did. "tree" is a write anywhere under `rel`, which only glob_covers_directory's whole-subtree reading lets in: a
    glob that merely matches `rel` does not.  Spud has no globs, so for him every kind is the write of `rel` itself."""
    generated = home and rel.split("/")[0].casefold() in hookio.GENERATED_ROOTS
    if member is None:
        if not home:
            return ("Law 1: Spud never produces a deliverable; %s is in project %s, where every path is a deliverable (Spud keeps no own"
                    " files there), so a spudagent writes it" % (rel, project_key))
        if any(path_matches_glob(rel, g, fold) for g in hookio.SPUD_PATHS):
            return None
        if generated:
            return ("Law 5: %s is generated from the ledger database (ledger/** and reports/** are rendered by `spud render`);"
                    " Spud's hand-written set is %s; use the spud CLI for everything else" % (rel, ", ".join(hookio.SPUD_PATHS)))
        return ("Law 1: Spud never produces a deliverable; %s is inside the repository and outside Spud's own paths (%s),"
                " so a spudagent writes it" % (rel, ", ".join(hookio.SPUD_PATHS)))
    if generated:
        return ("Law 5: %s is outside every member's deliverables; ledger/** and reports/** are generated from the ledger database"
                " (rendered by `spud render`), so record through the CLI: spud member log | result | block, spud proposal file" % rel)
    if elsewhere is not None:
        ticket_key, bound, checkout = elsewhere
        tail = OUTSIDE_BOUND_WORKTREE_TAIL % bound if os.path.isdir(bound) else OUTSIDE_GONE_WORKTREE_TAIL
        return OUTSIDE_BOUND_WORKTREE % {"rel": rel, "checkout": checkout, "project": project_key, "ref": ref, "key": ticket_key, "bound": bound, "tail": tail}
    globs = json.loads(member["deliverables"]) if member["deliverables"] else []
    for g in globs:
        key, bare = ops.glob_scope(g)
        if (key or ticket_project_key) != project_key:
            continue
        if directory == "tree":
            if glob_covers_directory(rel, bare, directory, fold):
                return None
        elif path_matches_glob(rel, bare, fold) or (directory and glob_covers_directory(rel, bare, directory, fold)):
            return None
    shown = rel if project_key == ticket_project_key else "%s:%s" % (project_key, rel)
    return "Law 5: %s is not among %s's deliverables (%s); write only there, or ask your parent to extend them" % (shown, ref, ", ".join(globs) or "none")


def in_state_dir(rel):
    """True when the repository path lies in the ledger state directory at its root (or is that directory).  The first
    component is case-folded, like a generated root's, so a case variant or a simple fold (U+017F for s) is refused on
    every filesystem; map_into_repository has already named a same-file spelling of it canonically.  A state
    directory deeper in the tree is no ledger's (bin/spud keeps its state at the root of its home) and stays under the globs."""
    return rel.split("/", 1)[0].casefold() == hookio.STATE_DIR


def state_dir_reason(rel):
    return hookio.DB_REASON % ("%s is in the ledger state directory at a project root, which holds the database, its WAL and shm files, the worktree"
                        " list cache, the backups and the launcher's cached bytecode; only the CLI writes there, whatever the deliverables" % rel)


def edit_reason(ctx, con, caller_agent_id, caller_member, path, cwd, mode="spud", directory=None):
    """The path rule for a Write/Edit target (and for a shell redirection target), across every
    registered project.  A path in the ledger state directory at any project root, by any reading of it, is refused
    to everyone before the binding, Law 1 and glob checks.  A bound member is held to its globs in any session; a session
    that is not Spud (`mode` plain), and an unbound subagent of one, writes freely in other projects and nowhere in the home.

    A caller with an agent_id in a Spud session is held outside the projects too: a git configuration file
    and any other path in a git directory are refused wherever they lie, and every reading of the target must land either in a registered project, where the
    globs decide, or under an allowed outside root -- the session scratchpad and the system temp directories.  So a symlink
    that reaches out of a project, and a path in no project at all, are both refused instead of passing unchecked.

    `directory` says the write only makes ("make") or only removes ("remove") a directory, which shell/arg_writes
    reads from the command; a member is then held to path_reason's directory reading of its globs as well as to a match.
    Every other caller leaves it None -- the Write/Edit hook, a redirection, tee, a git call's own writes -- and every
    refusal above here is unchanged, so a .git component, the state directory and the outside allowlist still win.
    "tree" is a write anywhere under the path: every refusal above holds for the path itself, a checkout root at
    or under it is refused as the .git it brings (tree_checkout_reason), and the outside allowlist, a prefix reading,
    holds the whole subtree as it holds one file."""
    readings = worktrees.path_readings(path, cwd)
    for candidate in readings:
        if harness_file(candidate):
            return ("%s is one of the harness's subagent files (<project>/<session>/subagents/...): the ledger binds identities from them,"
                    " so nothing but Claude Code writes them" % candidate), None
    # A caller the path rule binds: a bound member in any session, and an agent_id in a Spud session before its binding,
    # which has no globs of its own (a plain session's own subagents are Eric's).
    held = bool(caller_agent_id) and not (mode == "plain" and caller_member is None)
    if held:
        for candidate in readings:
            if git_config_file(candidate):
                return GIT_CONFIG_FILE_REASON % candidate, None
        for candidate in readings:  # the rest of a git directory, after the more specific config reason
            if git_dir_path(candidate):
                return GIT_DIR_PATH_REASON % candidate, None
    # A checkout at or under a directory written whole brings its .git into the write, which the path's own
    # reasons (another checkout, the outside allowlist, the globs) do not see; asked once they have all let it in
    tree = held and directory == "tree"
    inside, outside = worktrees.path_placements(ctx, con, path, cwd)
    if held and outside:
        reason = outside_project_reason(outside)
        if reason:
            return reason, None
    if not inside:
        return (tree_checkout_reason(ctx, con, readings) if tree else None), None
    for _project, _root, rel in inside:
        if in_state_dir(rel):
            return state_dir_reason(rel), rel
    first = inside[0][2]
    if caller_member is not None:
        ref = lookup.member_ref(con, caller_member["id"])
        ticket = lookup.get_ticket_by_id(con, caller_member["ticket_id"])
        ticket_project = lookup.project_key_of(con, ticket)
        bound = ticket["worktree"]  # NULL for a ticket no plan has bound, which keeps the rule it had
        for project, root, rel in inside:
            elsewhere = None
            if bound is not None and project["key"] == ticket_project and not worktrees.same_directory(root, bound):
                elsewhere = (ticket["key"], bound, root)
            reason = path_reason(rel, caller_member, ref, worktrees.folds_case(root), project["key"], ticket_project, worktrees.is_home(project),
                                 elsewhere, directory)
            if reason:
                return reason, rel
        return (tree_checkout_reason(ctx, con, readings) if tree else None), first
    plain = mode == "plain"
    if caller_agent_id:  # the home's generated roots are Law 5's for every caller, bound or not, before the binding matters
        for project, _root, rel in inside:
            if worktrees.is_home(project) and rel.split("/")[0].casefold() in hookio.GENERATED_ROOTS:
                return path_reason(rel, {"deliverables": "[]"}, "agent_id %s" % caller_agent_id), rel
    if caller_agent_id and not plain:
        return ("your agent_id %s is not bound to a member yet (the PostToolUse(Agent) hook binds a background spawn right after launch;"
                " a foreground spawn is bound at its first tool call or at its stop), so %s cannot be checked against your deliverables" % (caller_agent_id, first)), first
    for project, root, rel in inside:
        home = worktrees.is_home(project)
        if plain:
            if not home:
                continue
            generated = rel.split("/")[0].casefold() in hookio.GENERATED_ROOTS
            reason = (path_reason(rel, None, "Spud", worktrees.folds_case(root)) if generated else None) or NOT_SPUD_HOME % rel
        else:
            reason = path_reason(rel, None, "Spud", worktrees.folds_case(root), project["key"], None, home)
        if reason:
            return reason, rel
    return None, first
