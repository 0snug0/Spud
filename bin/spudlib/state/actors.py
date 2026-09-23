"""state/actors: Actors, ownership, and the session a command runs in."""

import os
import re

from . import lookup
from ..core import kernel
from ..hooks import worktrees


# ----------------------------------------------------------------------------
# Actors and ownership
# ----------------------------------------------------------------------------


class Actor:
    def __init__(self, kind, member=None):
        self.kind = kind  # 'spud' | 'member'
        self.member = member

    @property
    def label(self):
        return "spud" if self.kind == "spud" else "member:%d" % self.member["id"]

    def ref(self, con):
        return "spud" if self.kind == "spud" else lookup.member_ref(con, self.member["id"])


def resolve_actor(con, text):
    if text is None:
        raise kernel.SpudError(kernel.EXIT_ERROR, "this command writes the ledger and needs --as spud | SPUD-nnn/<Name> | <agent_id>")
    if text == "spud":
        return Actor("spud")
    if re.fullmatch(r"[0-9a-f]{17}", text):
        row = con.execute("SELECT * FROM members WHERE agent_id = ?", (text,)).fetchone()
        if row is None:
            raise kernel.SpudError(
                kernel.EXIT_ERROR,
                "agent_id %s is not bound to a member: the PostToolUse(Agent) hook binds it right after a background spawn,"
                " a foreground spawn is bound at its SubagentStop; a spawn the hooks never saw has no binding" % text,
            )
        return Actor("member", row)
    if "/" in text:
        return Actor("member", lookup.get_member(con, text, allow_handle=False))
    raise kernel.SpudError(kernel.EXIT_ERROR, "unknown actor %r: use spud, SPUD-nnn/<Name>, or an agent_id" % text)


ACTIVE_CTX = []  # the Ctx main() runs a command with: require_spud's session check needs the home and its projects


def require_spud(con, actor, what):
    if actor.kind != "spud":
        raise kernel.SpudError(kernel.EXIT_OWNERSHIP, "%s is Spud's; %s may not (use --as spud)" % (what, actor.ref(con)))
    unclaimed = unclaimed_session_project(ACTIVE_CTX[0] if ACTIVE_CTX else None, con)
    if unclaimed is not None:
        raise kernel.SpudError(kernel.EXIT_OWNERSHIP, "%s is Spud's, and this session is not Spud: it runs in project %s, where a session is Spud only after it"
                        " claims (type /spud, or run `spud --as spud session claim`)" % (what, unclaimed["key"]))


def claim_of(con, session_id):
    """The unreleased claim of a Claude Code session, or None."""
    if not session_id:
        return None
    return con.execute("SELECT * FROM sessions WHERE session_id = ? AND released_at IS NULL", (session_id,)).fetchone()


def unclaimed_session_project(ctx, con):
    """The project a CLI command runs in when that makes its session not Spud: CLAUDE_CODE_SESSION_ID
    is set, the working directory is in a `claim` project (the home is none), and the session holds no claim.  None
    otherwise, and always outside a Claude Code session (tests, a terminal)."""
    session = planning_session(os.environ)
    if ctx is None or session is None:
        return None
    if con.execute("SELECT 1 FROM projects WHERE archived_at IS NULL AND sessions = 'claim' LIMIT 1").fetchone() is None:
        return None
    try:
        cwd = os.getcwd()
    except OSError:
        return None
    mapped = worktrees.cli_project_of(ctx, con, cwd)
    if mapped is None or worktrees.is_home(mapped[0]) or mapped[0]["sessions"] != "claim" or claim_of(con, session) is not None:
        return None
    return mapped[0]


def require_member(con, actor, what):
    if actor.kind != "member":
        raise kernel.SpudError(kernel.EXIT_OWNERSHIP, "%s is the member's own; Spud may not write it (use --as SPUD-nnn/<Name>)" % what)


def is_ancestor(actor, member):
    """Spud is every member's ancestor; a member is an ancestor of the members
    below it on the same ticket."""
    if actor.kind == "spud":
        return True
    a = actor.member
    return a["ticket_id"] == member["ticket_id"] and member["lineage"].startswith(a["lineage"] + ".")


def require_ancestor(con, actor, member, what):
    if not is_ancestor(actor, member):
        raise kernel.SpudError(
            kernel.EXIT_OWNERSHIP,
            "%s belongs to the parent of %s; %s is not its parent or an ancestor" % (what, lookup.member_ref(con, member["id"]), actor.ref(con)),
        )


def planning_session(env):
    """The Claude Code session a command runs in: CLAUDE_CODE_SESSION_ID, which the harness sets in every Bash and
    hook subprocess to the session_id its hook payloads carry, a subagent's Bash included (hooks and env-vars
    references; observed 2026-09-13).  None outside a session."""
    value = env.get("CLAUDE_CODE_SESSION_ID")
    return value.strip() if isinstance(value, str) and value.strip() else None
