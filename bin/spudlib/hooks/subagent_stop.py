"""hooks/subagent_stop: A parent's finishing rule, and SubagentStop.  Moved from bin/spud_ledger.py (SPD-065)."""

import os
from pathlib import Path

from . import hookio, pretool, recording, stophook
from ..core import kernel
from ..projects import sessions
from ..state import ledgerdb, lookup, transcripts


RESULT_HOLD = ("record your Result with `spud --as %s member result '<what you produced, where, what you verified, what is left>'`"
               " (or Blocked with `spud --as %s member block '<the question and the options>'`) before returning; then return your summary")


def last_resume(con, member):
    """The event id of the member's last resume (SPD-050), None when it was never resumed: the start that
    superseded a return, which hook_subagent_start marks."""
    row = con.execute("SELECT MAX(id) AS id FROM events WHERE member_id = ? AND kind = ?"
                      " AND json_extract(data, '$.resumed') = 1", (member["id"], recording.RESUME_KIND)).fetchone()
    return None if row is None else row["id"]


def recorded_since(con, member, resumed):
    """Whether the Result or Blocked the member holds answers for the round that is ending (SPD-050).  With no
    resume this is the column check the Result hold has made since SPD-015.  After one it is not enough: what the
    member recorded before it was resumed was the verdict of the round before, and letting it stand would send a
    second round of work back with a first round's Result.  So the hold asks for a `member result` or `member
    block` recorded since the resume, and the events are the memory of when that was."""
    if not ((member["result"] or "").strip() or (member["blocked"] or "").strip()):
        return False
    if resumed is None:
        return True
    return con.execute("SELECT 1 FROM events WHERE member_id = ? AND kind IN ('member.result','member.blocked') AND id > ? LIMIT 1",
                       (member["id"], resumed)).fetchone() is not None


def unrecorded_children(con, member):
    """A member's own direct children that have returned (a final stop) and still have no
    verdict: the shape `hook_stop` uses for Spud's children, one level down (SPD-015)."""
    return con.execute(
        "SELECT m.*, t.team_key FROM members m JOIN tickets t ON t.id = m.ticket_id"
        " WHERE m.parent_id = ? AND m.stopped_at IS NOT NULL AND (m.outcome IS NULL OR trim(m.outcome) = '')"
        "   AND m.status = 'active'"
        " ORDER BY m.stopped_at, m.id",
        (member["id"],),
    ).fetchall()


def alive_children(con, member):
    """A member's own direct children that are still alive at its stop: spawned and running (no
    final stop), or planned and never spawned.  Probed 2026-09-12: a background child is not
    killed when its parent returns, its completion notification goes to the main session and its
    row is orphaned, so the block is the only thing that keeps the parent alive to collect it."""
    return con.execute(
        "SELECT m.*, t.team_key FROM members m JOIN tickets t ON t.id = m.ticket_id"
        " WHERE m.parent_id = ? AND m.status IN ('planned','active') AND m.stopped_at IS NULL"
        " ORDER BY COALESCE(m.spawned_at, m.planned_at), m.id",
        (member["id"],),
    ).fetchall()


def finish_commands(agent_id, children):
    return "; ".join("`spud --as %s member finish %s/%s --status done|blocked|failed --outcome '<verdict>'`" % (agent_id, c["team_key"], c["name"])
                     for c in children)


def children_hold_reason(agent_id, children):
    """Law 9 one level down: the children that returned, and the exact command for each."""
    n = len(children)
    items = "; ".join("%s/%s (%s, %s), stopped %s" % (c["team_key"], c["name"], c["lineage"], c["persona"], (c["stopped_at"] or "")[11:16])
                      for c in children)
    return ("%d child%s you spawned returned and %s not recorded: %s. Record %s with %s, and decide the proposals %s filed, if any"
            " (`spud proposal list --open`; `spud --as %s proposal decide <id> --decision absorb|decline|escalate --reason '<why>'`)."
            % (n, "" if n == 1 else "ren", "is" if n == 1 else "are", items, "it" if n == 1 else "each",
               finish_commands(agent_id, children), "it" if n == 1 else "they", agent_id))


def alive_children_reason(con, agent_id, children):
    """The other half of the parent's job: a child still running, planned and never spawned, or
    planned with its spawn allowed and never bound.  The last is not "never spawned": the
    reservation refuses a second spawn of it (hook_agent_spawn), so telling the lead to spawn it
    now would be wrong; its way out is the hook.error check and the lead's own actor, with no
    grace period, since a lead's background child binds at launch and a foreground one cannot
    outlive the lead's own turn (SPD-028; SPD-025 gives the session-level kind, ten minutes on)."""
    reserved = {}
    for c in children:
        if c["status"] != "active":
            waiting = stophook.reservations(con, c)
            if waiting:
                reserved[c["id"]] = waiting[0]

    def item(c):
        if c["status"] == "active":
            return "running since " + (c["spawned_at"] or "")[11:16]
        if c["id"] in reserved:
            first = reserved[c["id"]]
            return "spawn allowed %s (tool_use_id %s), never bound" % ((first["at"] or "")[11:16], first["tool_use_id"])
        return "planned " + (c["planned_at"] or "")[11:16] + " and never spawned"

    n = len(children)
    items = "; ".join("%s/%s (%s, %s), %s" % (c["team_key"], c["name"], c["lineage"], c["persona"], item(c)) for c in children)
    text = "%d child%s you planned %s still alive: %s." % (n, "" if n == 1 else "ren", "is" if n == 1 else "are", items)
    running = [c for c in children if c["status"] == "active"]
    unspawned = [c for c in children if c["status"] != "active" and c["id"] not in reserved]
    unbound = [c for c in children if c["id"] in reserved]
    if running:
        one = len(running) == 1
        text += (" Ending your turn ends your run: a background child is not killed with you, its completion notification goes to the main"
                 " session and its row is orphaned. Wait inside this turn (%s until the child's Result is there), then record %s with %s."
                 " If you cannot wait, say so in your summary and return: your parent records %s."
                 % ("; ".join("`spud member show %s/%s`" % (c["team_key"], c["name"]) for c in running),
                    "it" if one else "each", finish_commands(agent_id, running), "it" if one else "them"))
    if unspawned:
        one = len(unspawned) == 1
        text += (" The planned child%s never started: spawn %s now, or record %s with %s so the %s counting against your limits."
                 % ("" if one else "ren", "it" if one else "them", "it" if one else "each",
                    "; ".join("`spud --as %s member finish %s/%s --status failed --outcome '<why>'`" % (agent_id, c["team_key"], c["name"]) for c in unspawned),
                    "row stops" if one else "rows stop"))
    if unbound:
        one = len(unbound) == 1
        refs = ["%s/%s" % (c["team_key"], c["name"]) for c in unbound]
        steps = ["record %s with `spud --as %s member finish %s --status failed --outcome '<why>'` and plan a new member, since the reservation refuses a second spawn of `%s (%s, %s)`"
                 % (ref, agent_id, ref, ref, c["lineage"], c["persona"]) for c, ref in zip(unbound, refs)]
        text += (" The planned %s allowed to spawn and never bound: first look for %s in `spud events --kind hook.error --json`: a gap there means that child may"
                 " still be running, so wait inside this turn (%s until the child's Result is there), then record %s. If the harness failed %s, %s."
                 % ("child was" if one else "children were", "its tool_use_id" if one else "each tool_use_id",
                    "; ".join("`spud member show %s`" % ref for ref in refs),
                    "it" if one else "each",
                    "the spawn" if one else "a spawn", "; ".join(steps)))
    return text


def stop_hold_reason(con, agent_id, recorded, returned, still_alive):
    """One block for the whole finishing rule, children first; with no children of its own it is
    the Result hold, word for word."""
    clauses = []
    if returned:
        clauses.append(children_hold_reason(agent_id, returned))
    if still_alive:
        clauses.append(alive_children_reason(con, agent_id, still_alive))
    if not clauses:
        return RESULT_HOLD % (agent_id, agent_id)
    return "Law 9: " + " ".join(clauses) + (" Then return your summary." if recorded else " Then " + RESULT_HOLD % (agent_id, agent_id))


def hook_subagent_stop(ctx, payload):
    if not ctx.db_path.is_file():
        return hookio.SILENT
    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        raise hookio.HookError("SubagentStop payload carries no agent_id")
    stop_hook_active = bool(payload.get("stop_hook_active"))
    transcript = payload.get("agent_transcript_path") if isinstance(payload.get("agent_transcript_path"), str) else None
    last = payload.get("last_assistant_message")
    agent_type = payload.get("agent_type")
    con = ledgerdb.connect(ctx)
    try:
        at = kernel.now()
        with ledgerdb.write_txn(con):
            member = con.execute("SELECT * FROM members WHERE agent_id = ?", (agent_id,)).fetchone()
            bound_by = None
            if member is None:
                req = None
                if transcript:
                    req = pretool.request_from_meta(con, Path(transcript).with_name("agent-%s.meta.json" % agent_id), agent_id)
                    bound_by = "meta.json" if req else None
                if req is None:
                    # The only allowed, still unbound request of this session; with two or more the
                    # hook does not guess (Rooster's MEDIUM-5) and records the gap instead.
                    params = [payload.get("session_id")]
                    narrowing = ""
                    if isinstance(agent_type, str) and agent_type:
                        narrowing = " AND (subagent_type IS NULL OR subagent_type = ?)"
                        params.append(agent_type)
                    candidates = con.execute("SELECT * FROM spawn_requests WHERE session_id = ? AND decision = 'allow' AND member_id IS NOT NULL AND agent_id IS NULL" + narrowing + " ORDER BY at, rowid", params).fetchall()
                    if len(candidates) == 1:
                        req = candidates[0]
                        bound_by = "oldest-unbound"
                    elif len(candidates) > 1:
                        ledgerdb.write_event(con, at, "hook:SubagentStop", "hook.error",
                                    "SubagentStop for agent_id %s: no meta.json and %d unbound allowed requests in session %s (%s); not guessing which member it is"
                                    % (agent_id, len(candidates), payload.get("session_id"), ", ".join(c["tool_use_id"] for c in candidates)),
                                    agent_id=agent_id, data={"candidates": [c["tool_use_id"] for c in candidates]})
                if req is not None:
                    member = pretool.bind_request(con, at, "hook:SubagentStop", req, agent_id, payload.get("session_id"))
                    if member is None:
                        bound_by = None
            base = {"agent_type": agent_type, "stop_hook_active": stop_hook_active, "bound_by": bound_by}
            if member is None:
                if not sessions.pending_spawn(con, payload.get("session_id")) and sessions.session_mode(ctx, con, payload)[0] == "plain":
                    return hookio.SILENT  # Eric's own subagent in a session that is not Spud, with no spawn to bind: no event (SPD-014)
                ledgerdb.write_event(con, at, "hook:SubagentStop", "member.stopped", "subagent %s stopped unbound (%s)" % (agent_id, agent_type), agent_id=agent_id, data=base)
                return hookio.SILENT
            ref = lookup.member_ref(con, member["id"])
            resumed = last_resume(con, member)
            recorded = recorded_since(con, member, resumed)
            live = member["status"] in kernel.ALIVE
            returned = unrecorded_children(con, member) if live else []
            still_alive = alive_children(con, member) if live else []
            returned_refs = ["%s/%s" % (c["team_key"], c["name"]) for c in returned]
            alive_refs = ["%s/%s" % (c["team_key"], c["name"]) for c in still_alive]
            if live and (not recorded or returned or still_alive) and not stop_hook_active:
                # The finishing rule has two halves: the member's own Result, and the verdict of
                # every child it spawned (Law 9 binds every parent, not only Spud).  One block
                # covers both, children first; `stop_hook_active` lets the second stop through.
                what = []
                if not recorded:
                    what.append("without a Result")
                if returned_refs:
                    what.append("with %d unrecorded child%s (%s)" % (len(returned_refs), "" if len(returned_refs) == 1 else "ren", ", ".join(returned_refs)))
                if alive_refs:
                    what.append("with %d child%s still alive (%s)" % (len(alive_refs), "" if len(alive_refs) == 1 else "ren", ", ".join(alive_refs)))
                data = dict(base, held=True, unrecorded=not recorded)
                if returned_refs:
                    data["unrecorded_children"] = returned_refs
                if alive_refs:
                    data["alive_children"] = alive_refs
                ledgerdb.write_event(con, at, "hook:SubagentStop", "member.stopped", "%s returned %s; held once" % (ref, " and ".join(what)),
                            ticket_id=member["ticket_id"], member_id=member["id"], agent_id=agent_id, data=data)
                return hookio.HookOutput({"decision": "block", "reason": stop_hold_reason(con, agent_id, recorded, returned, still_alive)})
            updates = {"stopped_at": at}
            if isinstance(last, str) and last.strip():
                updates["return_text"] = last
            if transcript:
                updates["transcript_path"] = transcript
            summed = None
            if (transcripts.usage_parts(member["usage_json"])[0] is None or resumed is not None) and transcript and os.path.isfile(transcript):
                # a stored transcript sum stays, however it was counted (`member resum` re-sums one that added every
                # entry, SPD-023); a completion recorded before this stop stays beside the new sum (SPD-021).
                # A resumed member is the exception (SPD-050): its stored sum covers the round before, while the
                # transcript has gone on growing through the round now ending, so the run figures are counted again
                # from the whole file -- which is what a sum always is, so counting twice changes nothing else.
                # Live proof of the gap: the probe of 2026-09-15 stopped a second time with `transcript_usage: false`
                # and kept 60,994 tokens over a run the harness billed at 64,357.
                summed = transcripts.transcript_usage(transcript)
                if summed:
                    updates.update(transcripts.run_totals(member, summed=summed))
            con.execute("UPDATE members SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in updates), (*updates.values(), member["id"]))
            suffix = "" if recorded else " (returned unrecorded)"
            if returned_refs:
                suffix += " (%d unrecorded child%s: %s)" % (len(returned_refs), "" if len(returned_refs) == 1 else "ren", ", ".join(returned_refs))
            if alive_refs:
                suffix += " (%d child%s still alive: %s)" % (len(alive_refs), "" if len(alive_refs) == 1 else "ren", ", ".join(alive_refs))
            data = dict(base, held=False, unrecorded=not recorded, transcript_usage=bool(summed))
            if returned_refs:
                data["unrecorded_children"] = returned_refs
            if alive_refs:
                data["alive_children"] = alive_refs
            ledgerdb.write_event(con, at, "hook:SubagentStop", "member.stopped", "%s stopped%s" % (ref, suffix),
                        ticket_id=member["ticket_id"], member_id=member["id"], agent_id=agent_id, data=data)
    finally:
        con.close()
    return hookio.SILENT
