"""state/lookup: Row lookups and row dicts; member and project handles."""

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
        "worktree": t["worktree"],  # the bound linked worktree, None while unbound; never rendered into a note
    }


def respawn_links(con, ticket_id):
    """{the re-spawn's member id: the id of the member it re-spawns} on one ticket (SPD-321).  The link is data on the
    re-spawn's member.planned event, `respawns_id` beside the handle `respawns`, written by `member respawn` in the plan's
    own transaction: an event field rather than a column, since the link is a fact of the planning, written once and
    never changed, and the append-only log already holds it -- no migration.  A re-spawn is planned on the ticket of the
    member it re-spawns, so one indexed read of the ticket's events finds every link on it."""
    rows = con.execute("SELECT member_id, json_extract(data, '$.respawns_id') AS old FROM events WHERE ticket_id = ?"
                       " AND kind = 'member.planned' AND json_extract(data, '$.respawns_id') IS NOT NULL", (ticket_id,))
    return {r["member_id"]: r["old"] for r in rows}


def respawn_chain(con, member):
    """The ids of `member`'s re-spawn chain in run order (SPD-321): the first run, each re-spawn of it in turn, `member`
    among them.  A chain is linear, since a member is re-spawned once (state/ops.respawn_target), so the walk back is
    one link a step and so is the walk forward; a loop, which the planning cannot make, ends it."""
    links = respawn_links(con, member["ticket_id"])
    forward = {old: new for new, old in links.items()}
    first, seen = member["id"], {member["id"]}
    while links.get(first) is not None and links[first] not in seen:
        first = links[first]
        seen.add(first)
    chain, seen = [first], {first}
    while forward.get(chain[-1]) is not None and forward[chain[-1]] not in seen:
        chain.append(forward[chain[-1]])
        seen.add(chain[-1])
    return chain


def respawn_refs(con, m, links=None):
    """(the handle of the member `m` re-spawns, the handle of the member that re-spawned `m`), each None when there is
    none.  `links` is respawn_links of m's ticket, when the caller already read it for a whole team."""
    links = respawn_links(con, m["ticket_id"]) if links is None else links
    after = next((new for new, old in links.items() if old == m["id"]), None)
    return member_ref(con, links.get(m["id"])), member_ref(con, after)


def member_dict(con, m):
    ticket = get_ticket_by_id(con, m["ticket_id"])
    respawns, respawned_as = respawn_refs(con, m)
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
        "effort": m["effort"],
        "tier_reason": m["tier_reason"],
        "escalates": member_ref(con, m["escalates_id"]),
        "respawns": respawns,  # SPD-321: the member this row re-spawns, and the one that re-spawned it
        "respawned_as": respawned_as,
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


def effective_holder(con, member_id):
    """Who must decide a proposal held by `member_id` now: that member while it can still act, else the nearest
    ancestor that can, and None -- Spud -- at the root.  A member can act while its status is in kernel.ALIVE, planned or
    active; done, failed and blocked have all returned, and a blocked one acts again only after `member start`.  The climb
    is read where a decision is made and written there, never at `member finish`, so a blocked member that is re-briefed
    keeps whatever nobody decided meanwhile.  A holder whose row is gone, or a parent chain that loops, falls to Spud:
    the one thing that must never happen is a proposal nobody can decide."""
    seen = set()
    while member_id is not None and member_id not in seen:
        seen.add(member_id)
        row = get_member_by_id(con, member_id)
        if row is None:
            return None
        if row["status"] in kernel.ALIVE:
            return member_id
        member_id = row["parent_id"]
    return None


def holder_name(ref):
    """A proposal's holder in words: its member ref, or `Spud` for the NULL holder that means Spud."""
    return ref or "Spud"


def holder_words(recorded, effective):
    """Who must decide a proposal now, naming the recorded holder when the climb passed it:
    `Spud (was BADS-110/Garfield)`.  Both are member refs, or None for Spud."""
    if effective == recorded:
        return holder_name(recorded)
    return "%s (was %s)" % (holder_name(effective), holder_name(recorded))


def held_proposals(con, member_id):
    """The open proposals a member must decide: those recorded to it, and those recorded to a member below it
    that has returned, whose climb ends at it.  [{id, title}] in id order.  `member finish` reads it before the status
    change and names what it found, since after the change every one of them climbs one step further; it refuses
    nothing, because recording a returned member's outcome is Law 9 and the finish is what hands them on."""
    rows = con.execute("SELECT id, title, holder_member_id FROM proposals WHERE status = 'open' ORDER BY id").fetchall()
    return [{"id": r["id"], "title": r["title"]} for r in rows if effective_holder(con, r["holder_member_id"]) == member_id]


def proposal_dict(con, p):
    ticket = get_ticket_by_id(con, p["ticket_id"])
    created = get_ticket_by_id(con, p["created_ticket_id"]) if p["created_ticket_id"] else None
    # Who must decide it now, which is the recorded holder unless that member has returned.  A settled proposal
    # is nobody's to decide, so it stays its own holder rather than reading as inherited.
    effective = effective_holder(con, p["holder_member_id"]) if p["status"] == "open" else p["holder_member_id"]
    return {
        "id": p["id"],
        "ticket": ticket["key"],
        "origin": member_ref(con, p["origin_member_id"]),
        "holder": member_ref(con, p["holder_member_id"]),
        "effective_holder": member_ref(con, effective),
        "title": p["title"],
        "why": p["why"],
        "evidence": p["evidence"],
        "suggested_priority": p["suggested_priority"],
        "filed_at": p["filed_at"],
        "status": p["status"],
        "created_ticket": created["key"] if created else None,
    }


def pull_requests(con, ticket_ids=None, settled_only=False):
    """The recorded landing pull requests, in ticket then record order: of these ticket ids, or every one when
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
    """The one pull request a ticket's note describes in its two frontmatter keys: the newest row recorded,
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
    """What a merged pull request leaves owed while its ticket is still open: the done move with the URL in the
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
    stored state alone: no gh call and no git, because that text is injected at every session start."""
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
