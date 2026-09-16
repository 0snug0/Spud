"""state/ops: Domain operations: ticket numbers, transitions, names, deliverables, planning, member status changes, and the prose checks and proposal brief the commands share.  Moved from bin/spud_ledger.py (SPD-065)."""

import json
import re

from . import ledgerdb, lookup
from ..core import kernel


def check_prose_headings(value, names, what, before=None):
    """Refuse stored prose holding a line an import reads as its note's own section heading (SPD-076): `## <name>`, read
    as split_document reads a heading, for a name in `names` (TICKET_SECTIONS for ticket prose, IMPORT_MEMBER_SECTIONS
    for member prose), fenced or not, since owned_headings sees no fences.  Given `before`, only the lines added to it."""
    lines = [line for line in (value or "").split("\n") if line.startswith("## ") and line[3:].strip() in names]
    for line in (before or "").split("\n"):
        if line in lines:
            lines.remove(line)
    if lines:
        name = lines[0][3:].strip()
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s holds the line `%s`, which an import reads as the note's own ## %s heading and so moves"
                        " text into another section (SPD-076); write `### %s` or reword it" % (what, lines[0].rstrip(), name, name))


def proposal_brief(why, evidence):
    """The brief of a ticket created from a proposal: its why, then its evidence."""
    return ((why + "\n\n" if why else "") + "Evidence: " + evidence) if evidence else why


# ----------------------------------------------------------------------------
# Domain operations
# ----------------------------------------------------------------------------


def next_ticket_number(con, project_id):
    return (con.execute("SELECT COALESCE(MAX(number), 0) FROM tickets WHERE project_id = ?", (project_id,)).fetchone()[0]) + 1


def ticket_key(prefix, number):
    """The ledger's spelling of a ticket or team key: the prefix, a dash, the number padded to three digits."""
    return "%s-%03d" % (prefix, number)


def insert_ticket(con, at, actor_label, project, title, priority, status, origin="eric", proposal_id=None,
                  brief="", sizing="", outcome="", tags=None, heading=None, created_at=None, number=None, layout=None):
    if number is None:
        number = next_ticket_number(con, project["id"])
    key = ticket_key(project["ticket_prefix"], number)
    team_key = ticket_key(project["team_prefix"], number)
    stamp = created_at or at
    tags = tags if tags is not None else ["ticket"]
    cur = con.execute(
        "INSERT INTO tickets (project_id, number, key, team_key, title, heading, priority, status, origin, proposal_id,"
        " brief, sizing, outcome, tags, layout, created_at, updated_at, closed_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (project["id"], number, key, team_key, title, heading, priority, status, origin, proposal_id,
         brief, sizing, outcome, json.dumps(tags), json.dumps(layout) if layout else None, stamp, stamp),
    )
    return con.execute("SELECT * FROM tickets WHERE id = ?", (cur.lastrowid,)).fetchone()


def check_transition(table, old, new, key):
    transitions = kernel.TICKET_TRANSITIONS if table == "tickets" else kernel.MEMBER_TRANSITIONS
    if (old, new) not in transitions:
        allowed = sorted(t for o, t in transitions if o == old)
        raise kernel.SpudError(
            kernel.EXIT_TRANSITION,
            "%s is %s; %s -> %s is not a transition (from %s: %s)" % (key, old, old, new, old, ", ".join(allowed) or "nothing"),
        )


def draw_name(con, ticket_id, wanted=None):
    if wanted:
        row = con.execute("SELECT active FROM name_pool WHERE name = ?", (wanted,)).fetchone()
        if row is None or row["active"] != 1:
            raise kernel.SpudError(kernel.EXIT_ERROR, "%r is not an active name in naming.pool (spud.config.json); run `spud config sync` after adding it" % wanted)
        used = con.execute("SELECT 1 FROM members WHERE ticket_id = ? AND name = ?", (ticket_id, wanted)).fetchone()
        if used:
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s is already used on this ticket's team" % wanted)
        return wanted
    row = con.execute(
        "SELECT name FROM name_pool WHERE active = 1 AND name NOT IN (SELECT name FROM members WHERE ticket_id = ?)"
        " ORDER BY random() LIMIT 1",
        (ticket_id,),
    ).fetchone()
    if row is None:
        raise kernel.SpudError(kernel.EXIT_ERROR, "the name pool is exhausted for this ticket; extend naming.pool in spud.config.json and run `spud config sync`")
    return row["name"]


QUALIFIED_GLOB = re.compile(r"([a-z][a-z0-9-]*):(.*)\Z", re.S)


def glob_scope(glob):
    """(project key or None, the glob): `<key>:<glob>` names another project's checkout (SPD-014), a bare glob the ticket's."""
    m = QUALIFIED_GLOB.match(glob)
    return (m.group(1), m.group(2)) if m else (None, glob)


def check_deliverable_projects(con, globs):
    """A qualified deliverable must name an active project."""
    keys = {r["key"] for r in con.execute("SELECT key FROM projects WHERE archived_at IS NULL").fetchall()}
    for g in globs:
        key = glob_scope(g)[0]
        if key is not None and key not in keys:
            raise kernel.SpudError(kernel.EXIT_ERROR, "deliverable %r names project %r, which is no active project (spud project list)" % (g, key))


def normalize_deliverable(glob):
    """A deliverable is a repository-relative path glob: no leading slash, no `..`,
    `**` allowed; a trailing slash means everything under that directory.  An optional
    `<key>:` in front names the project whose checkout it is relative to (SPD-014).
    `*` and `?` stay inside one path segment, `**` crosses segments, and every other
    character is literal, brackets included: a Next.js `[email]` segment is written
    plainly, and nothing in a glob has to be escaped (SPD-086, pathrule.glob_to_regex)."""
    key, rest = glob_scope((glob or "").strip())
    g = normalize_bare_deliverable(glob, rest)
    return "%s:%s" % (key, g) if key else g


def normalize_bare_deliverable(glob, rest):
    g = (rest or "").strip().replace("\\", "/")
    while g.startswith("./"):
        g = g[2:]
    if not g:
        raise kernel.SpudError(kernel.EXIT_ERROR, "a deliverable must be a non-empty repository-relative path glob (bin/spud, tests/**, docs/x/*.md)")
    if g.startswith("/") or re.match(r"^[A-Za-z]:", g) or g.startswith("~"):
        raise kernel.SpudError(kernel.EXIT_ERROR, "deliverable %r must be relative to the repository, not absolute" % glob)
    parts = g.split("/")
    if any(p == ".." for p in parts) or any(p in ("", ".") for p in parts[:-1]):
        raise kernel.SpudError(kernel.EXIT_ERROR, "deliverable %r may not contain `..` or empty segments" % glob)
    if g.endswith("/"):
        g += "**"
    return g


def normalize_deliverables(globs):
    return [normalize_deliverable(g) for g in (globs or [])]


def plan_member(ctx, con, actor, ticket_key, persona, model, name=None, tier_reason=None, agent_type=None, brief="", deliverables=None, session_id=None):
    """member new: the four limit checks, the lineage and the name draw, all inside
    one BEGIN IMMEDIATE, reading the ticket and the parent inside it too.  session_id is
    the Claude Code session planning it (SPD-018), None outside one."""
    limits = ctx.limits
    deliverables = normalize_deliverables(deliverables)
    if persona not in ctx.personas():
        raise kernel.SpudError(kernel.EXIT_ERROR, "unknown persona %r; spud.config.json knows %s" % (persona, ", ".join(ctx.personas())))
    if persona not in kernel.PERSONAS:
        raise kernel.SpudError(kernel.EXIT_ERROR, "persona %r is not in the schema's list (%s)" % (persona, ", ".join(kernel.PERSONAS)))
    if model not in kernel.MODELS:
        raise kernel.SpudError(kernel.EXIT_ERROR, "model must be one of %s" % ", ".join(kernel.MODELS))
    if persona == "contractor":
        if not agent_type:
            raise kernel.SpudError(kernel.EXIT_ERROR, "a contractor needs --agent-type (the native agent type it runs as)")
    else:
        agent_type = agent_type or "spudagent"
        default_tier = ctx.persona_tier(persona)
        if default_tier and model != default_tier and not tier_reason:
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s defaults to %s; model %s needs --tier-reason" % (persona, default_tier, model))
    if not (brief or "").strip():
        raise kernel.SpudError(kernel.EXIT_ERROR, "no brief, no spudagent (Law 2): member new needs --brief (a non-empty brief; @file and @- are accepted)")
    if actor.kind == "spud" and not ticket_key:
        raise kernel.SpudError(kernel.EXIT_ERROR, "--ticket is required when Spud plans a member")
    at = kernel.now()
    with ledgerdb.write_txn(con):
        parent = lookup.get_member_by_id(con, actor.member["id"]) if actor.kind == "member" else None
        if parent is not None:
            ticket = lookup.get_ticket_by_id(con, parent["ticket_id"])
            if ticket_key and ticket_key != ticket["key"]:
                raise kernel.SpudError(kernel.EXIT_OWNERSHIP, "%s works %s; a member plans children only on its own ticket, not on %s" % (lookup.member_ref(con, parent["id"]), ticket["key"], ticket_key))
            if parent["status"] not in kernel.ALIVE:
                raise kernel.SpudError(kernel.EXIT_ERROR, "%s is %s and cannot plan children" % (lookup.member_ref(con, parent["id"]), parent["status"]))
        else:
            ticket = lookup.get_ticket(con, ticket_key)
        check_deliverable_projects(con, deliverables)
        if ticket["status"] not in ("queued", "active"):
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s is %s; no member can be planned on it" % (ticket["key"], ticket["status"]))
        depth = (parent["depth"] if parent else 0) + 1
        if depth > limits["max_depth"]:
            raise kernel.SpudError(kernel.EXIT_LIMIT, "depth %d exceeds limits.max_depth %d" % (depth, limits["max_depth"]))
        parent_id = parent["id"] if parent else None
        alive = con.execute(
            "SELECT count(*) FROM members WHERE ticket_id = ? AND parent_id IS ? AND status IN ('planned','active')",
            (ticket["id"], parent_id),
        ).fetchone()[0]
        cap = limits["root_fan_out"] if parent is None else limits["child_fan_out"]
        cap_name = "root_fan_out" if parent is None else "child_fan_out"
        if alive >= cap:
            raise kernel.SpudError(kernel.EXIT_LIMIT, "fan-out: %d children alive under %s already, limits.%s is %d; a child frees its slot when it finishes" % (alive, "Spud" if parent is None else lookup.member_ref(con, parent_id), cap_name, cap))
        total = con.execute("SELECT count(*) FROM members WHERE status IN ('planned','active')").fetchone()[0]
        if total >= limits["max_concurrent_total"]:
            raise kernel.SpudError(kernel.EXIT_LIMIT, "concurrency: %d members alive across every ticket, limits.max_concurrent_total is %d" % (total, limits["max_concurrent_total"]))
        ever = con.execute("SELECT count(*) FROM members WHERE ticket_id = ? AND parent_id IS ?", (ticket["id"], parent_id)).fetchone()[0]
        pad = ctx.id_pad()
        lineage = ((parent["lineage"] + ".") if parent else "") + str(ever + 1).zfill(pad)
        chosen = draw_name(con, ticket["id"], name)
        cur = con.execute(
            "INSERT INTO members (ticket_id, lineage, depth, parent_id, name, persona, agent_type, model, tier_reason,"
            " status, brief, deliverables, planned_at, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?)",
            (ticket["id"], lineage, depth, parent_id, chosen, persona, agent_type, model, tier_reason, brief,
             json.dumps(list(deliverables or [])), at, session_id),
        )
        member_id = cur.lastrowid
        if parent is None:
            con.execute("UPDATE tickets SET lead_id = ?, updated_at = ? WHERE id = ? AND lead_id IS NULL", (member_id, at, ticket["id"]))
        data = {"lineage": lineage, "name": chosen, "persona": persona, "model": model, "agent_type": agent_type}
        if session_id:
            data["session_id"] = session_id
        ledgerdb.write_event(con, at, actor.label, "member.planned", "planned %s (%s, %s, %s)" % (chosen, lineage, persona, model),
                    ticket_id=ticket["id"], member_id=member_id, data=data)
    return lookup.get_member_by_id(con, member_id)


def create_ticket_for_proposal(con, at, actor, proposal, title, priority):
    origin_ticket = lookup.get_ticket_by_id(con, proposal["ticket_id"])
    project = con.execute("SELECT * FROM projects WHERE id = ?", (origin_ticket["project_id"],)).fetchone()
    brief = proposal_brief(proposal["why"], proposal["evidence"])
    check_prose_headings(brief, kernel.TICKET_SECTIONS, "proposal %d's why and evidence, the new ticket's brief," % proposal["id"])
    t = insert_ticket(con, at, actor.label, project, title or proposal["title"], priority, "queued",
                      origin="proposal", proposal_id=proposal["id"], brief=brief)
    ledgerdb.write_event(con, at, actor.label, "ticket.created", "%s created from proposal %d: %s" % (t["key"], proposal["id"], t["title"]),
                ticket_id=t["id"], data={"origin": "proposal", "proposal_id": proposal["id"], "priority": priority})
    return t


def status_stamps(m, new_status, at):
    """The timestamps a member transition carries: spawned on the first start,
    finished cleared on a resume from blocked, finished stamped on a terminal state."""
    extra = {}
    if new_status == "active":
        if m["spawned_at"] is None:
            extra["spawned_at"] = at
        if m["status"] == "blocked":
            extra["finished_at"] = None
    elif new_status in ("done", "blocked", "failed"):
        extra["finished_at"] = at
    return extra


def member_status_change(con, at, actor_label, m, new_status, extra_updates=None, outcome_event=None):
    check_transition("members", m["status"], new_status, lookup.member_ref(con, m["id"]))
    updates = status_stamps(m, new_status, at)
    updates.update(extra_updates or {})
    updates["status"] = new_status
    con.execute("UPDATE members SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in updates), (*updates.values(), m["id"]))
    if outcome_event:
        ledgerdb.write_event(con, at, actor_label, "member.outcome", outcome_event, ticket_id=m["ticket_id"], member_id=m["id"],
                    data={"status": new_status, "summary": updates.get("summary")})
    ledgerdb.write_event(con, at, actor_label, "member.status", "%s %s -> %s" % (lookup.member_ref(con, m["id"]), m["status"], new_status),
                ticket_id=m["ticket_id"], member_id=m["id"], data={"from": m["status"], "to": new_status})
