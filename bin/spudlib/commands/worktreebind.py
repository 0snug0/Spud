"""commands/worktreebind: Ticket-bound worktrees at `member new` and `member edit`: which deliverables need one, the caller's checkout, the binding, its refusals, and the worktree `card` and `board` show."""

import os

from ..core import homeconf, kernel
from ..hooks import hookio, worktrees
from ..state import ledgerdb, lookup, ops


# Ticket-bound worktrees, a refusal and not a warning.  A ticket needs a
# worktree when a deliverable of the member being planned resolves into a checkout of its project: a bare glob, or one
# qualified with the project's own key; `home:` never does, and a glob naming another project is refused outright, since
# one ticket binds one worktree of its own project (Spud's decision 3).  The first plan that needs one binds the ticket to
# the caller's checkout, which must be a linked worktree of the project (the same git common directory as its root, and
# not the main checkout); after that every plan must come from that worktree while git still lists it.  A binding whose
# worktree is gone is stale: Spud's next plan from a worktree rebinds it, with the old path in the event, and a member's
# never does (decision 1).  A project whose root holds no `.git` has no linked worktree to bind -- `project add` registers
# only a repository, so this is one whose .git went away after -- and its tickets bind nothing.
#
# Every git call runs before the transaction opens: Binder.prepare makes the whole decision once against the ticket as it
# reads then, writing nothing and keeping every refusal to itself, and decide, inside member new's BEGIN IMMEDIATE, reads
# its cached answers.  Only a binding that another command changed in between costs a `git worktree list` under the lock.

MAIN_CHECKOUT = (
    "%(key)s needs a worktree: %(glob)s is in project %(project)s's checkout, and this command runs in its main checkout"
    " %(root)s, where code is never built. Enter a linked worktree of it first (EnterWorktree name: %(name)s), then"
    " rerun this command from there: the first plan from a worktree binds the ticket to it")
OUTSIDE_PROJECT = (
    "%(key)s needs a worktree of project %(project)s: %(glob)s is in its checkout, and this command runs in %(cwd)s, in"
    " %(where)s, outside that project. Start the ticket from the project root %(root)s: a session there enters a"
    " linked worktree (EnterWorktree name: %(name)s) and reruns this command from it, which binds the ticket to that worktree")
BOUND_ELSEWHERE = (
    "%(key)s is bound to the worktree %(bound)s (%(state)s), which git still lists, and this command runs in %(here)s:"
    " plan from that worktree. %(tail)s")
BOUND_ELSEWHERE_SPUD = ("A ticket moves to another worktree only once its bound one is gone (removed after landing); then a"
                        " plan from the new worktree rebinds it")
BOUND_ELSEWHERE_MEMBER = ("A member never rebinds a ticket: Spud rebinds it, by planning from the worktree the work moves to"
                          " once the bound one is gone")
STALE_FOR_MEMBER = (
    "%(key)s was bound to the worktree %(bound)s, which no longer exists, and a member never rebinds a ticket:"
    " Spud rebinds it by planning a member from the worktree the work continues in (this command runs in %(here)s). Ask"
    " your parent, or record the question with `member block`")
OTHER_PROJECT = (
    "deliverable %(glob)r names project %(other)s, and %(key)s is project %(project)s's: one ticket binds one worktree of its"
    " own project, so the work in project %(other)s is a ticket there (spud --as spud ticket new --project %(other)s)")


def working_directory():
    """The directory the command runs in, None when it is gone."""
    try:
        return os.getcwd()
    except OSError:
        return None


def checkout_of(path):
    """(top level, git directory, common directory), each a real path, of the repository `path` is in; None outside every
    repository.  The common directory is the same for a main checkout and each of its linked worktrees, and equals the git
    directory only in the main checkout."""
    if not path:
        return None
    proc = homeconf.run_git(path, "rev-parse", "--path-format=absolute", "--show-toplevel", "--absolute-git-dir", "--git-common-dir", timeout=10)
    lines = proc.stdout.splitlines()
    if proc.returncode != 0 or len(lines) != 3:
        return None
    return tuple(os.path.realpath(line) for line in lines)


def worktree_state(path):
    """What `card` and `board` show for a binding, read from git at display time and never stored: {path, present, branch,
    detached}; None for an unbound ticket.  A worktree that is gone is shown as gone, never an error."""
    if not path:
        return None
    state = {"path": path, "present": os.path.isdir(path), "branch": None, "detached": None}
    if not state["present"]:
        return state
    try:
        proc = homeconf.run_git(path, "symbolic-ref", "--quiet", "--short", "HEAD", timeout=10)
        if proc.returncode == 0:
            state["branch"] = proc.stdout.strip() or None
        else:
            proc = homeconf.run_git(path, "rev-parse", "--short", "HEAD", timeout=10)
            state["detached"] = proc.stdout.strip() or None if proc.returncode == 0 else None
    except kernel.SpudError:
        pass
    return state


def state_text(state):
    """`branch <name>`, `detached at <commit>`, `gone`, or what git could not say, for a worktree_state."""
    if not state["present"]:
        return "gone"
    if state["branch"]:
        return "branch %s" % state["branch"]
    if state["detached"]:
        return "detached at %s" % state["detached"]
    return "no branch git can read"


def worktree_line(state):
    return "%s (%s)" % (state["path"], state_text(state))


class Binder:
    """One command's binding decision (member new, member edit): the caller's working directory, and the git answers the
    decision reads, cached so the transaction runs none it already has."""

    def __init__(self, ctx, cwd):
        self.ctx = ctx
        self.cwd = cwd
        self.cache = {}

    def cached(self, key, compute):
        if key not in self.cache:
            self.cache[key] = compute()
        return self.cache[key]

    def caller(self):
        return self.cached(("checkout", self.cwd), lambda: checkout_of(self.cwd))

    def project_common(self, root):
        facts = self.cached(("checkout", root), lambda: checkout_of(root))
        return facts[2] if facts else None

    def listed(self, root):
        def compute():
            try:
                return [os.path.realpath(p) for p in worktrees.git_worktree_list(root)]
            except hookio.HookError as e:
                raise kernel.SpudError(kernel.EXIT_ERROR, str(e))
        return self.cached(("listed", root), compute)

    def prepare(self, con, actor, ticket, deliverables):
        """decide() once before the transaction, writing nothing and raising nothing, so its git calls are made and cached;
        `ticket` is the row as it reads now, or None when it cannot be read yet (the command reports that itself)."""
        if ticket is None:
            return
        try:
            self.decide(con, None, actor, ticket, ops.normalize_deliverables(deliverables), write=False)
        except kernel.SpudError:
            pass

    def decide(self, con, at, actor, ticket, deliverables, write=True):
        """Bind `ticket` to the caller's worktree when these deliverables need one and it is not bound there yet, or refuse
        with exit 5; the bound path when this call bound it, else None.  `deliverables` are normalized globs."""
        project = con.execute("SELECT * FROM projects WHERE id = ?", (ticket["project_id"],)).fetchone()
        facts = {"key": ticket["key"], "project": project["key"], "name": "%s-<slug>" % ticket["key"].lower()}
        needing = None
        for glob in deliverables:
            key = ops.glob_scope(glob)[0]
            if key == kernel.HOME_KEY:
                continue
            if key is not None and key != project["key"]:
                raise kernel.SpudError(kernel.EXIT_TRANSITION, OTHER_PROJECT % dict(facts, glob=glob, other=key))
            needing = needing or glob
        root = worktrees.project_root(self.ctx, project)
        if needing is None or not os.path.lexists(os.path.join(root, ".git")):
            return None
        facts.update(glob=needing, root=root)
        common = self.project_common(root)
        if common is None:
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s needs a worktree of project %s, and git cannot read the repository at its root %s" % (ticket["key"], project["key"], root))
        caller = self.caller()
        if caller is None or caller[2] != common:
            raise kernel.SpudError(kernel.EXIT_TRANSITION, OUTSIDE_PROJECT % dict(facts, cwd=self.cwd or "a directory that is gone", where=self.where(caller)))
        here, git_dir, _common = caller
        if git_dir == common:
            raise kernel.SpudError(kernel.EXIT_TRANSITION, MAIN_CHECKOUT % facts)
        bound = ticket["worktree"]
        if bound is not None and worktrees.same_directory(bound, here):
            return None
        if bound is not None:
            if os.path.isdir(bound) and os.path.realpath(bound) in self.listed(root):
                tail = BOUND_ELSEWHERE_SPUD if actor.kind == "spud" else BOUND_ELSEWHERE_MEMBER
                state = self.cached(("state", bound), lambda: worktree_state(bound))
                raise kernel.SpudError(kernel.EXIT_TRANSITION, BOUND_ELSEWHERE % dict(facts, bound=bound, here=here, tail=tail, state=state_text(state)))
            if actor.kind != "spud":
                raise kernel.SpudError(kernel.EXIT_TRANSITION, STALE_FOR_MEMBER % dict(facts, bound=bound, here=here))
        if not write:
            return None
        con.execute("UPDATE tickets SET worktree = ?, updated_at = ? WHERE id = ?", (here, at, ticket["id"]))
        body = ("%s bound to the worktree %s (project %s)" % (ticket["key"], here, project["key"]) if bound is None else
                "%s rebound to the worktree %s (project %s): %s is gone" % (ticket["key"], here, project["key"], bound))
        ledgerdb.write_event(con, at, actor.label, "ticket.worktree", body, ticket_id=ticket["id"],
                             member_id=actor.member["id"] if actor.kind == "member" else None,
                             data={"worktree": here, "previous": bound, "project": project["key"]})
        return here

    def where(self, caller):
        """Where a command outside the ticket's project runs: Spud's home, another repository, or no repository at all."""
        home = os.path.realpath(str(self.ctx.home))
        cwd = os.path.realpath(self.cwd) if self.cwd else None
        if cwd is not None and (cwd == home or cwd.startswith(home + os.sep)):
            return "Spud's home"
        if caller is None:
            return "no git repository"
        return "another repository" if caller[0] == cwd else "another repository, %s" % caller[0]


def planned_ticket(con, actor, ticket_key):
    """The ticket member new plans on, read before its transaction: the parent's own for a member, else --ticket; None when
    it cannot be read, which plan_member reports in its own words."""
    try:
        if actor.kind == "member":
            return lookup.get_ticket_by_id(con, actor.member["ticket_id"])
        return lookup.get_ticket(con, ticket_key) if ticket_key else None
    except kernel.SpudError:
        return None
