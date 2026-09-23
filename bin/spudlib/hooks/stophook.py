"""hooks/stophook: What a stopping session owes, and Stop."""

from datetime import datetime

from . import hookio
from ..core import kernel
from ..projects import sessions
from ..state import ledgerdb, lookup


# Spud's Stop hook, one session at a time.  Eric runs tickets in parallel sessions; before the hook read sessions every
# Stop was held, ledger-wide, for any member returned unrecorded, another session's included.  A member's session is the
# session that spawned its tree.  Every hook payload fired inside a subagent carries the main session's session_id (the
# probe captures of nested spawns: a lead's PreToolUse(Agent) for its child and the SubagentStops of lead and child all carry
# it), and so does CLAUDE_CODE_SESSION_ID in any Bash the harness runs, a subagent's included, which `member new`
# records on the row it plans (planning_session).
PLANNED_GRACE_SECONDS = 600  # a planned row no session can claim holds every session only once it has waited this long; a spawn allowed and never bound holds only once its allow is this old


def member_session(con, member):
    """The session a member belongs to: its latest allowed spawn request's, else the one its row records (bound at
    spawn, or at `member new`), else its parent's, up to the root; None when nothing on the way records one (a row
    from before sessions were recorded)."""
    seen = set()
    while member is not None and member["id"] not in seen:
        seen.add(member["id"])
        req = con.execute("SELECT session_id FROM spawn_requests WHERE member_id = ? AND decision = 'allow' AND session_id != ''"
                          " ORDER BY at DESC, rowid DESC LIMIT 1", (member["id"],)).fetchone()
        if req is not None:
            return req["session_id"]
        if member["session_id"]:
            return member["session_id"]
        member = lookup.get_member_by_id(con, member["parent_id"]) if member["parent_id"] is not None else None
    return None


def planned_long_ago(stamp, at):
    """Whether stamp (a row's planned_at, or a spawn request's allow) is more than PLANNED_GRACE_SECONDS
    before at; a stamp that cannot be read counts as old."""
    try:
        planned, current = datetime.fromisoformat(stamp), datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return True
    planned = planned if planned.tzinfo else planned.astimezone()
    current = current if current.tzinfo else current.astimezone()
    return (current - planned).total_seconds() > PLANNED_GRACE_SECONDS


def told_running(con, session, ref):
    """Whether an earlier Stop block in this session already named ref as still running: the events are the memory."""
    return con.execute(
        "SELECT 1 FROM events e, json_each(e.data, '$.running') j"
        " WHERE e.kind = 'hook.denied' AND e.actor = 'hook:Stop' AND json_extract(e.data, '$.session_id') IS ? AND j.value = ? LIMIT 1",
        (session, ref),
    ).fetchone() is not None


def reservations(con, member):
    """A member's allowed spawn requests still waiting to bind, oldest first: the reservation hook_agent_spawn keeps
    against a second spawn of the member until one binds."""
    return con.execute("SELECT tool_use_id, at FROM spawn_requests WHERE member_id = ? AND decision = 'allow' AND agent_id IS NULL"
                       " ORDER BY at, rowid", (member["id"],)).fetchall()


def stop_owed(con, session, at):
    """What the stopping session owes, as (returned, planned, unbound, running) lists of member rows.  A member of another
    session is never owed; one with no known session is every session's, as is everything when the payload names
    no session.
    - returned: a final stop, no outcome, status active, at the root or under a finished parent;
    - planned: never spawned and no allowed spawn waiting to bind, at the root or under a finished parent; a row no
      session can claim only once it has waited PLANNED_GRACE_SECONDS, so a row planned a moment ago never holds;
    - unbound: planned the same way, but its spawn was allowed and never bound: the harness failed the spawn,
      or the binding failed open.  Owed only once every such allow is more than PLANNED_GRACE_SECONDS old; until then
      it is a spawn in flight (PostToolUse(Agent) binds a background spawn at launch) and never holds;
    - running: active with no final stop under a finished parent, named once per session (Spud's own children at
      work never hold: ending a turn while they run is the design)."""
    def owes(own):
        return own is None or session is None or own == session

    returned = [r for r in con.execute(
        "SELECT m.*, t.team_key, t.key AS ticket_key FROM members m JOIN tickets t ON t.id = m.ticket_id"
        " LEFT JOIN members p ON p.id = m.parent_id"
        " WHERE m.stopped_at IS NOT NULL AND (m.outcome IS NULL OR trim(m.outcome) = '') AND m.status = 'active'"
        "   AND (m.parent_id IS NULL OR p.status NOT IN ('planned','active'))"
        " ORDER BY m.stopped_at, m.id"
    ).fetchall() if owes(member_session(con, r))]
    planned, unbound = [], []
    for r in con.execute(
        "SELECT m.*, t.team_key, t.key AS ticket_key, p.status AS parent_status FROM members m JOIN tickets t ON t.id = m.ticket_id"
        " LEFT JOIN members p ON p.id = m.parent_id"
        " WHERE m.status = 'planned' AND (m.parent_id IS NULL OR p.status NOT IN ('planned','active'))"
        " ORDER BY m.planned_at, m.id"
    ).fetchall():
        waiting = reservations(con, r)
        if not all(planned_long_ago(w["at"], at) for w in waiting):
            continue  # a spawn in flight
        own = member_session(con, r)
        if (session is not None and own == session) or ((own is None or session is None) and planned_long_ago(r["planned_at"], at)):
            (unbound if waiting else planned).append(r)
    running = [r for r in con.execute(
        "SELECT m.*, t.team_key, t.key AS ticket_key, p.status AS parent_status FROM members m JOIN tickets t ON t.id = m.ticket_id"
        " JOIN members p ON p.id = m.parent_id"
        " WHERE m.status = 'active' AND m.stopped_at IS NULL AND p.status NOT IN ('planned','active')"
        " ORDER BY COALESCE(m.spawned_at, m.planned_at), m.id"
    ).fetchall() if owes(member_session(con, r)) and not told_running(con, session, "%s/%s" % (r["team_key"], r["name"]))]
    return returned, planned, unbound, running


def stop_item(r, what):
    return "%s/%s (%s, %s) on %s, %s" % (r["team_key"], r["name"], r["lineage"], r["persona"], r["ticket_key"], what)


def returned_clause(rows):
    """The returned members, in the words of Spud's Stop, without the ending; when every one is a
    root member the printed command also carries --next (a nested one's does not: `member
    finish` writes no report entry, and no Next line, for a child)."""
    next_opt = " [--next '<what happens next>']" if rows and all(r["parent_id"] is None for r in rows) else ""
    return ("%d returned spudagent(s) are not recorded: %s. Record each with `spud --as spud member finish <SPUD-nnn/Name> --status done|blocked|failed"
            " --outcome '<verdict>' [--summary '<one paragraph>']%s`, decide its proposals (spud proposal list --open; spud --as spud proposal decide ...)"
            % (len(rows), "; ".join(stop_item(r, "stopped " + kernel.fm_minute(r["stopped_at"])) for r in rows), next_opt))


def planned_clause(con, rows):
    """Planned rows never spawned: Spud spawns its own or records them failed; one under a finished parent nobody
    can spawn.  A root row's failed command also carries --next; a nested row's does not, the same
    as its unchanged member finish (no report entry for a child)."""
    items, steps = [], []
    for r in rows:
        ref = "%s/%s" % (r["team_key"], r["name"])
        if r["parent_id"] is None:
            fail = "`spud --as spud member finish %s --status failed --outcome '<why>' [--next '<what happens next>']`" % ref
            items.append(stop_item(r, "planned " + kernel.fm_minute(r["planned_at"])))
            steps.append("spawn %s now (the Agent tool with subagent_type `%s`, model `%s`, description `%s (%s, %s)`) or record it with %s"
                         % (ref, r["agent_type"], r["model"], ref, r["lineage"], r["persona"], fail))
        else:
            fail = "`spud --as spud member finish %s --status failed --outcome '<why>'`" % ref
            parent = lookup.member_ref(con, r["parent_id"])
            items.append(stop_item(r, "planned %s, under %s (%s)" % (kernel.fm_minute(r["planned_at"]), parent, r["parent_status"])))
            steps.append("nobody can spawn %s now that %s is %s: record it with %s" % (ref, parent, r["parent_status"], fail))
    steps_text = "; ".join(steps)
    return ("%d planned spudagent(s) were never spawned: %s. %s. A planned row holds a slot against the limits until it is spawned or recorded"
            % (len(rows), "; ".join(items), steps_text[:1].upper() + steps_text[1:]))


def unbound_clause(con, rows):
    """Planned rows whose spawn PreToolUse(Agent) allowed, and so reserved, and which never bound.  They are not
    'never spawned': the harness failed the spawn, or the binding failed open and the child may be running, so the
    binding gap comes first.  A root row that failed is planned anew, because the reservation refuses a second spawn of
    its description; one under a finished parent nobody can spawn again."""
    items, steps = [], []
    for r in rows:
        ref = "%s/%s" % (r["team_key"], r["name"])
        first = reservations(con, r)[0]  # the one hook_agent_spawn's refusal names
        allowed = "spawn allowed %s (tool_use_id %s), never bound" % (kernel.fm_minute(first["at"]), first["tool_use_id"])
        if r["parent_id"] is None:
            items.append(stop_item(r, allowed))
            steps.append("record %s with `spud --as spud member finish %s --status failed --outcome '<why>' [--next '<what happens next>']` and plan a new"
                         " member, since the reservation refuses a second spawn of `%s (%s, %s)`" % (ref, ref, ref, r["lineage"], r["persona"]))
        else:
            parent = lookup.member_ref(con, r["parent_id"])
            items.append(stop_item(r, "%s, under %s (%s)" % (allowed, parent, r["parent_status"])))
            steps.append("record %s with `spud --as spud member finish %s --status failed --outcome '<why>'`: nobody can spawn it again now that %s is %s"
                         % (ref, ref, parent, r["parent_status"]))
    one = len(rows) == 1
    return ("%d planned spudagent(s) were allowed to spawn and never bound: %s. First look for %s in `spud events --kind hook.error --json`:"
            " a gap there means that child may still be running, so wait for its notification. If the harness failed %s, %s."
            " A reserved row holds a slot against the limits until it is bound or recorded"
            % (len(rows), "; ".join(items), "its tool_use_id" if one else "each tool_use_id", "the spawn" if one else "a spawn", "; ".join(steps)))


def running_clause(con, rows):
    """Children still at work under a finished parent: nobody else will record them, and this is said once per session."""
    items = "; ".join(stop_item(r, "running since %s, under %s (%s)" % (kernel.fm_minute(r["spawned_at"] or r["planned_at"]), lookup.member_ref(con, r["parent_id"]), r["parent_status"]))
                      for r in rows)
    commands = "; ".join("`spud --as spud member finish %s/%s --status done|blocked|failed --outcome '<verdict>'`" % (r["team_key"], r["name"]) for r in rows)
    return ("%d spudagent(s) are still running under a finished parent: %s. No parent is left to record them: each completion notification"
            " comes to the session that spawned the tree, and when it arrives, record each with %s. This is said once; one that returns"
            " unrecorded is named again as returned" % (len(rows), items, commands))


def stop_reason(con, returned, planned, unbound, running):
    """One block: returned first, then planned, then unbound, then running.  With returned members alone it is the reason Spud's Stop
    has always given, with --next added to the printed command when every one is a root member."""
    if returned and not planned and not unbound and not running:
        return "Law 9: " + returned_clause(returned) + ", then end the turn."
    clauses = ([returned_clause(returned)] if returned else []) + ([planned_clause(con, planned)] if planned else []) + ([unbound_clause(con, unbound)] if unbound else []) + ([running_clause(con, running)] if running else [])
    return "Law 9: " + ". ".join(clauses) + ". Then end the turn."


def hook_stop(ctx, payload):
    if payload.get("agent_id") or payload.get("stop_hook_active") or not ctx.db_path.is_file():
        return hookio.SILENT
    session = payload.get("session_id") if isinstance(payload.get("session_id"), str) and payload.get("session_id") else None
    con = ledgerdb.connect(ctx)
    try:
        at = kernel.now()
        with ledgerdb.write_txn(con):
            if sessions.session_mode(ctx, con, payload)[0] == "plain":
                return hookio.SILENT  # a session that is not Spud owes the ledger nothing
            returned, planned, unbound, running = stop_owed(con, session, at)
            if not (returned or planned or unbound or running):
                return hookio.SILENT
            refs = {kind: ["%s/%s" % (r["team_key"], r["name"]) for r in rows] for kind, rows in (("returned", returned), ("planned", planned), ("unbound", unbound), ("running", running))}
            reason = stop_reason(con, returned, planned, unbound, running)
            ledgerdb.write_event(con, at, "hook:Stop", "hook.denied", reason,
                        data=dict({"hook_event_name": "Stop", "session_id": session, "members": refs["returned"] + refs["planned"] + refs["unbound"] + refs["running"]}, **refs))
    finally:
        con.close()
    return hookio.HookOutput({"decision": "block", "reason": reason})
