"""state/lookup: Row lookups and row dicts; member and project handles.  Moved from bin/spud_ledger.py (SPD-065)."""

import json
import re

from ..core import kernel


# ----------------------------------------------------------------------------
# Rows as dicts
# ----------------------------------------------------------------------------


def member_ref(con, member_id):
    if member_id is None:
        return None
    row = con.execute("SELECT m.name, t.team_key FROM members m JOIN tickets t ON t.id = m.ticket_id WHERE m.id = ?", (member_id,)).fetchone()
    return None if row is None else "%s/%s" % (row["team_key"], row["name"])


def get_ticket(con, key):
    row = con.execute("SELECT * FROM tickets WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise kernel.SpudError(kernel.EXIT_ERROR, "no ticket %s" % key)
    return row


def get_ticket_by_id(con, ticket_id):
    return con.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()


def get_member_by_id(con, member_id):
    return con.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()


# The two handles the CLI prints (member new's is the three-tuple form; the Agent description
# DESCRIPTION parses, below, is the two-tuple): SPUD-nnn/<Name> (<lineage>, <persona>) and
# SPUD-nnn/<Name> (<lineage>, <persona>, <model>), with the same whitespace DESCRIPTION allows.
HANDLE_RE = re.compile(
    r"^\s*(?P<team>[A-Z][A-Z0-9]*-\d+)/(?P<name>[A-Za-z][\w-]*)\s*\(\s*(?P<lineage>\d+(?:\.\d+)*)\s*,\s*(?P<persona>[a-z]+)\s*(?:,\s*(?P<model>[a-z]+)\s*)?\)\s*$"
)


def get_member(con, ref, *, allow_handle=True):
    """SPUD-nnn/<Name> or SPUD-nnn/<lineage>; with allow_handle (the default), a member may also be
    written as either handle HANDLE_RE parses.  A handle's parenthetical must agree with the row --
    lineage and persona, and model when the handle gives one -- or the lookup is refused naming the
    member's real handle, never a silent pick.  resolve_actor passes allow_handle=False so --as stays
    exactly as strict as it has always been: a handle in --as is refused the way an unknown actor is."""
    handle = HANDLE_RE.match(ref) if allow_handle else None
    if handle:
        team_key, who = handle.group("team"), handle.group("name")
    elif "/" in ref:
        team_key, _, who = ref.partition("/")
    else:
        raise kernel.SpudError(kernel.EXIT_ERROR, "a member is written SPUD-nnn/<Name> or SPUD-nnn/<lineage>, not %r" % ref)
    ticket = con.execute("SELECT * FROM tickets WHERE team_key = ?", (team_key,)).fetchone()
    if ticket is None:
        raise kernel.SpudError(kernel.EXIT_ERROR, "no team %s" % team_key)
    if re.fullmatch(r"\d+(\.\d+)*", who):
        row = con.execute("SELECT * FROM members WHERE ticket_id = ? AND lineage = ?", (ticket["id"], who)).fetchone()
    else:
        row = con.execute("SELECT * FROM members WHERE ticket_id = ? AND name = ?", (ticket["id"], who)).fetchone()
    if row is None:
        raise kernel.SpudError(kernel.EXIT_ERROR, "no member %s" % ref)
    if handle:
        given_model = handle.group("model")
        if handle.group("lineage") != row["lineage"] or handle.group("persona") != row["persona"] or (given_model and given_model != row["model"]):
            real = "%s/%s (%s, %s, %s)" % (team_key, row["name"], row["lineage"], row["persona"], row["model"])
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s does not match the ledger; it is %s" % (ref.strip(), real))
    return row


def ticket_dict(con, t):
    project = con.execute("SELECT key FROM projects WHERE id = ?", (t["project_id"],)).fetchone()
    proposed_by = None
    if t["proposal_id"] is not None:
        p = con.execute("SELECT origin_member_id FROM proposals WHERE id = ?", (t["proposal_id"],)).fetchone()
        if p is not None:
            proposed_by = member_ref(con, p["origin_member_id"])
    return {
        "id": t["id"],
        "key": t["key"],
        "team_key": t["team_key"],
        "project": project["key"] if project else None,
        "number": t["number"],
        "title": t["title"],
        "heading": t["heading"],
        "priority": t["priority"],
        "status": t["status"],
        "parked_until": t["parked_until"],
        "parked_reason": t["parked_reason"],
        "origin": t["origin"],
        "proposed_by": proposed_by,
        "lead": member_ref(con, t["lead_id"]),
        "brief": t["brief"],
        "sizing": t["sizing"],
        "outcome": t["outcome"],
        "tags": json.loads(t["tags"]),
        "created_at": t["created_at"],
        "updated_at": t["updated_at"],
        "closed_at": t["closed_at"],
        "worktree": t["worktree"],  # SPD-098: the bound linked worktree, None while unbound; never rendered into a note
    }


def member_dict(con, m):
    ticket = get_ticket_by_id(con, m["ticket_id"])
    return {
        "id": m["id"],
        "ref": "%s/%s" % (ticket["team_key"], m["name"]),
        "ticket": ticket["key"],
        "team_key": ticket["team_key"],
        "lineage": m["lineage"],
        "depth": m["depth"],
        "name": m["name"],
        "persona": m["persona"],
        "agent_type": m["agent_type"],
        "model": m["model"],
        "tier_reason": m["tier_reason"],
        "status": m["status"],
        "parent": member_ref(con, m["parent_id"]),
        "brief": m["brief"],
        "deliverables": json.loads(m["deliverables"]),
        "result": m["result"],
        "blocked": m["blocked"],
        "outcome": m["outcome"],
        "summary": m["summary"],
        "planned_at": m["planned_at"],
        "spawned_at": m["spawned_at"],
        "stopped_at": m["stopped_at"],
        "finished_at": m["finished_at"],
        "agent_id": m["agent_id"],
        "resolved_model": m["resolved_model"],
        "project": project_key_of(con, ticket),
        "total_tokens": m["total_tokens"],
        "duration_ms": m["duration_ms"],
        "tool_uses": m["tool_uses"],
    }


def proposal_dict(con, p):
    ticket = get_ticket_by_id(con, p["ticket_id"])
    created = get_ticket_by_id(con, p["created_ticket_id"]) if p["created_ticket_id"] else None
    return {
        "id": p["id"],
        "ticket": ticket["key"],
        "origin": member_ref(con, p["origin_member_id"]),
        "holder": member_ref(con, p["holder_member_id"]),
        "title": p["title"],
        "why": p["why"],
        "evidence": p["evidence"],
        "suggested_priority": p["suggested_priority"],
        "filed_at": p["filed_at"],
        "status": p["status"],
        "created_ticket": created["key"] if created else None,
    }


def pull_requests(con, ticket_ids=None, settled_only=False):
    """The recorded landing pull requests (SPD-077), in ticket then record order: of these ticket ids, or every one when
    None; `settled_only` keeps the merged and closed ones, which are the rows that nag."""
    if ticket_ids is not None and not ticket_ids:
        return []  # an empty id list matches nothing, and `IN ()` is a syntax error
    clauses, params = [], []
    if ticket_ids is not None:
        clauses.append("ticket_id IN (%s)" % ",".join("?" * len(ticket_ids)))
        params += list(ticket_ids)
    if settled_only:
        clauses.append("state <> 'open'")
    sql = "SELECT * FROM pull_requests" + ((" WHERE " + " AND ".join(clauses)) if clauses else "") + " ORDER BY ticket_id, id"
    return con.execute(sql, params).fetchall()


def ticket_landing(con, ticket_id):
    """The one pull request a ticket's note describes in its two frontmatter keys (SPD-116): the newest row recorded,
    which is the one `pr record` last said the ticket lands through; None when none is recorded.  The newest row, not
    the unsettled one, because that is the value that changes only when Spud records another pull request -- a
    reconciled state moves `pr_state` and never `pr`.  A ticket carries several rows over its life; every one of them
    renders in ## Landing, and `spud pr list` stays the whole record."""
    return con.execute("SELECT * FROM pull_requests WHERE ticket_id = ? ORDER BY id DESC LIMIT 1", (ticket_id,)).fetchone()


def pr_dict(con, p):
    ticket = get_ticket_by_id(con, p["ticket_id"])
    return {
        "id": p["id"],
        "ticket": ticket["key"],
        "ticket_status": ticket["status"],
        "project": project_key_of(con, ticket),
        "url": p["url"],
        "number": p["number"],
        "branch": p["branch"],
        "worktree": p["worktree"],
        "state": p["state"],
        "merged_at": p["merged_at"],
        "recorded_at": p["recorded_at"],
        "recorded_by": p["recorded_by"],
        "checked_at": p["checked_at"],
        "check_error": p["check_error"],
        "settled_at": p["settled_at"],
    }


def pr_name(d):
    """`#361` when the URL gave a number, else the URL itself."""
    return ("#%d" % d["number"]) if d["number"] else d["url"]


def pr_state_text(d):
    """A recorded pull request's state in words: `open`, `merged <minute>`, `closed unmerged <minute>`.  A merge time is
    GitHub's `mergedAt` as GitHub spells it (UTC), so it never reads as the ledger's local clock."""
    stamp = d["merged_at"] or d["settled_at"] if d["state"] == "merged" else d["settled_at"]
    tail = (" " + kernel.fm_minute(stamp)) if stamp else ""
    if d["state"] == "merged":
        return "merged" + tail
    if d["state"] == "closed":
        return "closed unmerged" + tail
    return "open"


def pr_owed(d):
    """What a merged pull request leaves owed while its ticket is still open (SPD-077): the done move with the URL in the
    Outcome, and the cleanup of the worktree and the local branch the record named.  [] for anything else -- a closed
    unmerged pull request is surfaced and owes nothing, and a ticket already done or declined was settled by hand.  The
    ledger never does any of this itself: the move is a judgment and the cleanup is git's."""
    if d["state"] != "merged" or d["ticket_status"] in ("done", "declined"):
        return []
    owed = ["the done move, with the pull request URL in %s's Outcome" % d["ticket"]]
    if d["worktree"]:
        owed.append("`git worktree remove %s`" % d["worktree"])
    if d["branch"]:
        owed.append("`git branch -D %s`" % d["branch"])
    return owed


def pr_nag_line(d):
    """The line a merged or closed pull request puts on the brief board and in every SessionStart context, built from
    stored state alone: no gh call and no git, because that text is injected at every session start (SPD-077)."""
    owed = pr_owed(d)
    return "pull request %s %s%s" % (pr_name(d), pr_state_text(d), ("; owed: " + "; ".join(owed)) if owed else "")


def event_dict(con, e):
    ticket = get_ticket_by_id(con, e["ticket_id"]) if e["ticket_id"] else None
    return {
        "id": e["id"],
        "at": e["at"],
        "actor": e["actor"],
        "ticket": ticket["key"] if ticket else None,
        "member": member_ref(con, e["member_id"]),
        "agent_id": e["agent_id"],
        "kind": e["kind"],
        "body": e["body"],
        "data": json.loads(e["data"]) if e["data"] else None,
    }


# ----------------------------------------------------------------------------
# Render
# ----------------------------------------------------------------------------


def persona_label(m):
    if m["persona"] == "contractor":
        return "contractor on `%s`" % m["agent_type"]
    return m["persona"]


def project_key_of(con, ticket):
    row = con.execute("SELECT key FROM projects WHERE id = ?", (ticket["project_id"],)).fetchone()
    return row["key"] if row else None


def get_project(con, key):
    row = con.execute("SELECT * FROM projects WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise kernel.SpudError(kernel.EXIT_ERROR, "no project %r (spud project list)" % key)
    return row
