"""state/ops: Domain operations: ticket numbers, transitions, names, deliverables, planning, member status changes, and the prose checks and proposal brief the commands share.

Past 250 lines and kept whole (SPD-222): the effort rules -- check_agent_type, planned_effort, escalation_target -- are
planning's own, read inside plan_member's transaction, and `member edit` reuses planned_effort for the same decision.
Taking them into a module of their own would put one more module on the hook path, where state/ops already sits, for a
seam no hook uses."""

import json
import re

from . import ledgerdb, lookup
from ..core import kernel


def check_prose_headings(value, names, what, before=None):
    """Refuse stored prose holding a line an import reads as its note's own section heading: `## <name>`, read
    as split_document reads a heading, for a name in `names` (TICKET_SECTIONS for ticket prose, IMPORT_MEMBER_SECTIONS
    for member prose), fenced or not, since owned_headings sees no fences.  Given `before`, only the lines added to it."""
    lines = [line for line in (value or "").split("\n") if line.startswith("## ") and line[3:].strip() in names]
    for line in (before or "").split("\n"):
        if line in lines:
            lines.remove(line)
    if lines:
        name = lines[0][3:].strip()
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s holds the line `%s`, which an import reads as the note's own ## %s heading and so moves"
                        " text into another section; write `### %s` or reword it" % (what, lines[0].rstrip(), name, name))


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


def insert_ticket(con, at, actor_label, project, title, priority, status, origin="owner", proposal_id=None,
                  brief="", sizing="", outcome="", tags=None, heading=None, created_at=None, number=None, layout=None,
                  parked_until=None, parked_reason=None):
    if number is None:
        number = next_ticket_number(con, project["id"])
    key = ticket_key(project["ticket_prefix"], number)
    team_key = ticket_key(project["team_prefix"], number)
    stamp = created_at or at
    tags = tags if tags is not None else ["ticket"]
    # parked_until and parked_reason are the importer's: `ticket new` has no --status parked, so a ticket born
    # parked is `ticket new` then `ticket move`, and the CHECKs of 0003_parked refuse anything else here.
    cur = con.execute(
        "INSERT INTO tickets (project_id, number, key, team_key, title, heading, priority, status, origin, proposal_id,"
        " brief, sizing, outcome, tags, layout, created_at, updated_at, closed_at, parked_until, parked_reason)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
        (project["id"], number, key, team_key, title, heading, priority, status, origin, proposal_id,
         brief, sizing, outcome, json.dumps(tags), json.dumps(layout) if layout else None, stamp, stamp,
         parked_until, parked_reason),
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
    """(project key or None, the glob): `<key>:<glob>` names another project's checkout, a bare glob the ticket's."""
    m = QUALIFIED_GLOB.match(glob)
    return (m.group(1), m.group(2)) if m else (None, glob)


def check_deliverable_projects(con, globs):
    """A qualified deliverable must name an active project, or the home by its reserved key."""
    keys = {r["key"] for r in con.execute("SELECT key FROM projects WHERE archived_at IS NULL").fetchall()} | {kernel.HOME_KEY}
    for g in globs:
        key = glob_scope(g)[0]
        if key is not None and key not in keys:
            raise kernel.SpudError(kernel.EXIT_ERROR, "deliverable %r names project %r, which is no active project and not the home (spud project list)" % (g, key))


def normalize_deliverable(glob):
    """A deliverable is a repository-relative path glob: no leading slash, no `..`,
    `**` allowed; a trailing slash means everything under that directory.  An optional
    `<key>:` in front names the project whose checkout it is relative to, or
    `home:` Spud's home.
    `*` and `?` stay inside one path segment, `**` crosses segments, and every other
    character is literal, brackets included: a Next.js `[email]` segment is written
    plainly, and nothing in a glob has to be escaped (pathrule.glob_to_regex)."""
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


VARIANT_AS_TYPE = ("%s is the spawn type of a spudagent planned at an effort, not an agent type to plan: plan it as a"
                   " spudagent with --effort %s, and the spawn check holds its Agent call to %s")
NO_EFFORT = "%s takes no effort, so --effort %s is not recorded; it is spawned as subagent_type spudagent, which sets none"
CONTRACTOR_EFFORT = "a contractor's effort is its own definition's, so --effort %s is not recorded"
BELOW_FLOOR = ("an escalation runs at the effort of the member it re-plans or higher, never lower: %s ran at %s, and"
               " --effort %s is lower")


def check_agent_type(agent_type):
    """Refuse an --agent-type naming an effort variant (SPD-222): the row's agent type is `spudagent` and its effort is
    a column, from which kernel.spawn_type makes `spudagent-<effort>`; a variant as the agent type would record no effort
    and spawn at one nobody chose."""
    if agent_type in kernel.SPUDAGENT_VARIANTS:
        level = agent_type[len(kernel.SPUDAGENT) + 1:]
        raise kernel.SpudError(kernel.EXIT_ERROR, VARIANT_AS_TYPE % (agent_type, level, agent_type))


def planned_effort(ctx, persona, model, agent_type, effort=None, kept=None, floor=None):
    """(the effort a member is planned at, a note when an --effort given is not recorded) (SPD-222).

    None for a contractor, whose own definition sets its effort, and on a model that takes none (haiku): whatever
    --effort says, and the note says so.  Otherwise the --effort given; else `kept`, the level the row already has (a
    `member edit --model` keeps it); else the floor's; else the persona's `effort` in spud.config.json; else
    kernel.DEFAULT_EFFORT.  `floor` is (the handle, the effort) of the member an escalation re-plans, whose level this
    one may not go below -- and whose level is also its default (escalation_target says why)."""
    if agent_type != kernel.SPUDAGENT:
        return None, (CONTRACTOR_EFFORT % effort if effort else None)
    if model not in kernel.EFFORT_MODELS:
        return None, (NO_EFFORT % (model, effort) if effort else None)
    ref, least = floor if floor else (None, None)
    level = effort or kept or least or ctx.persona_effort(persona) or kernel.DEFAULT_EFFORT
    if least in kernel.EFFORTS and kernel.EFFORTS.index(level) < kernel.EFFORTS.index(least):
        raise kernel.SpudError(kernel.EXIT_ERROR, BELOW_FLOOR % (ref, least, level))
    return level, None


def escalation_target(con, ref, ticket, parent_id, model):
    """The member `member new --escalates <ref>` re-plans (SPD-222), checked inside the plan's transaction: on the same
    ticket under the same parent, returned failed or blocked, run on the first model of kernel.ESCALATION and re-planned on
    the second, and escalated by no other member yet -- once, never twice.

    Its effort is the re-plan's floor and, when --effort names none, its default: the same level, not one higher.  The
    escalation changes one thing, the model, so that when the re-plan succeeds the ledger says what fixed it; raising
    the effort as well would confound the two and compound the cost of the more expensive model.  A parent that judges
    the failure one of effort too passes a higher --effort; a lower one is refused (planned_effort)."""
    target = lookup.get_member(con, ref)
    handle = lookup.member_ref(con, target["id"])
    low, high = kernel.ESCALATION
    if target["ticket_id"] != ticket["id"]:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not on %s; an escalation is planned on the ticket of the member it re-plans" % (handle, ticket["key"]))
    if target["parent_id"] != parent_id:
        raise kernel.SpudError(kernel.EXIT_OWNERSHIP, "%s is %s's child; its own parent re-plans it" % (handle, lookup.member_ref(con, target["parent_id"]) or "Spud"))
    if target["status"] not in ("failed", "blocked"):
        raise kernel.SpudError(kernel.EXIT_TRANSITION, "%s is %s; only a member that returned failed or blocked is escalated" % (handle, target["status"]))
    if target["model"] != low or model != high:
        raise kernel.SpudError(kernel.EXIT_ERROR, "the one escalation is %s to %s: %s ran on %s and this plan names %s" % (low, high, handle, target["model"], model))
    again = con.execute("SELECT id FROM members WHERE escalates_id = ?", (target["id"],)).fetchone()
    if again is not None:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s was escalated once already, by %s; a %s failure is not retried" % (handle, lookup.member_ref(con, again["id"]), high))
    return target


def plan_member(ctx, con, actor, ticket_key, persona, model, name=None, tier_reason=None, agent_type=None, brief="", deliverables=None, session_id=None,
                binder=None, escalates=None, effort=None):
    """member new: the four limit checks, the lineage and the name draw, all inside
    one BEGIN IMMEDIATE, reading the ticket and the parent inside it too.  session_id is
    the Claude Code session planning it, None outside one.  `binder` is the
    command's commands/worktreebind.Binder, prepared before this call: its decision runs
    last, inside the transaction, so a plan refused for its worktree writes nothing and a
    binding is written only with the member it binds for.  `escalates` names the failed
    opus member this plan re-runs on fable (escalation_target), and fills the tier reason
    when none is given.  `effort` is --effort, settled by planned_effort.  Returns (the
    row, planned_effort's note or None)."""
    limits = ctx.limits
    deliverables = normalize_deliverables(deliverables)
    if persona not in ctx.personas():
        raise kernel.SpudError(kernel.EXIT_ERROR, "unknown persona %r; spud.config.json knows %s" % (persona, ", ".join(ctx.personas())))
    if persona not in kernel.PERSONAS:
        raise kernel.SpudError(kernel.EXIT_ERROR, "persona %r is not in the schema's list (%s)" % (persona, ", ".join(kernel.PERSONAS)))
    if model not in kernel.MODELS:
        raise kernel.SpudError(kernel.EXIT_ERROR, "model must be one of %s" % ", ".join(kernel.MODELS))
    check_agent_type(agent_type)
    if effort is not None and effort not in kernel.EFFORTS:
        raise kernel.SpudError(kernel.EXIT_ERROR, "effort must be one of %s" % ", ".join(kernel.EFFORTS))
    if persona == "contractor":
        if not agent_type:
            raise kernel.SpudError(kernel.EXIT_ERROR, "a contractor needs --agent-type (the native agent type it runs as)")
    else:
        agent_type = agent_type or kernel.SPUDAGENT
        default_tier = ctx.persona_tier(persona)
        if default_tier and model != default_tier and not tier_reason and not escalates:
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
        target = escalation_target(con, escalates, ticket, parent_id, model) if escalates else None
        if target is not None and not tier_reason:
            tier_reason = "escalation after %s %s" % (lookup.member_ref(con, target["id"]), target["status"])
        floor = (lookup.member_ref(con, target["id"]), target["effort"]) if target is not None else None
        effort, note = planned_effort(ctx, persona, model, agent_type, effort=effort, floor=floor)
        if binder is not None:
            binder.decide(con, at, actor, ticket, deliverables)
        cur = con.execute(
            "INSERT INTO members (ticket_id, lineage, depth, parent_id, name, persona, agent_type, model, effort, tier_reason,"
            " escalates_id, status, brief, deliverables, planned_at, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?)",
            (ticket["id"], lineage, depth, parent_id, chosen, persona, agent_type, model, effort, tier_reason,
             target["id"] if target is not None else None, brief, json.dumps(list(deliverables or [])), at, session_id),
        )
        member_id = cur.lastrowid
        if parent is None:
            con.execute("UPDATE tickets SET lead_id = ?, updated_at = ? WHERE id = ? AND lead_id IS NULL", (member_id, at, ticket["id"]))
        data = {"lineage": lineage, "name": chosen, "persona": persona, "model": model, "agent_type": agent_type}
        if effort:
            data["effort"] = effort
        if target is not None:
            data["escalates"] = lookup.member_ref(con, target["id"])
        if session_id:
            data["session_id"] = session_id
        ledgerdb.write_event(con, at, actor.label, "member.planned", "planned %s (%s, %s, %s)" % (chosen, lineage, persona, model),
                    ticket_id=ticket["id"], member_id=member_id, data=data)
    return lookup.get_member_by_id(con, member_id), note


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
