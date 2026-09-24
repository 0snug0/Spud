"""hooks/recording: Binding, and the recording handlers PostToolUse and SubagentStart."""

import json

from . import hookio, worktrees
from ..core import kernel
from ..projects import sessions
from ..state import ledgerdb, lookup, ops, transcripts


# -- the recording handlers ------------------------------------------------------------


def bind_member(con, at, actor_label, member, agent_id, session_id=None, tool_use_id=None, resolved_model=None):
    """agent_id -> member: the columns only hooks write, planned -> active through the state
    machine, and the events the child recorded before the binding attached to it."""
    if member["agent_id"] and member["agent_id"] != agent_id:
        ledgerdb.write_event(con, at, actor_label, "hook.error", "%s is already bound to agent_id %s; not rebinding to %s" % (lookup.member_ref(con, member["id"]), member["agent_id"], agent_id),
                    ticket_id=member["ticket_id"], member_id=member["id"], agent_id=agent_id, data={"tool_use_id": tool_use_id})
        return member
    updates = {"agent_id": agent_id}
    if session_id:
        updates["session_id"] = str(session_id)
    if tool_use_id:
        updates["tool_use_id"] = tool_use_id
    if resolved_model:
        updates["resolved_model"] = str(resolved_model)
    con.execute("UPDATE members SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in updates), (*updates.values(), member["id"]))
    member = lookup.get_member_by_id(con, member["id"])
    if member["status"] == "planned":
        ops.member_status_change(con, at, actor_label, member, "active")
        member = lookup.get_member_by_id(con, member["id"])
    con.execute("UPDATE events SET member_id = ?, ticket_id = COALESCE(ticket_id, ?) WHERE agent_id = ? AND member_id IS NULL", (member["id"], member["ticket_id"], agent_id))
    return member


# A member's run totals.  A foreground spawn fires SubagentStop and then PostToolUse(Agent,
# completed) (spike, Enforcement plan, fact 8); a background spawn's PostToolUse comes at launch with no
# usage fields, so only its SubagentStop records the run.
# total_tokens and the usage key token_counts reads come only from a transcript sum: the completion's
# totalTokens and usage cover its final API request only (hooks reference, Agent tool telemetry).
# What the completion reports is kept under "completion" in usage_json, beside the sum or alone.  Its
# totalDurationMs and totalToolUseCount, which the reference defines over the whole run (the run's
# wall-clock duration, the count of tool calls the subagent made), win over the transcript's
# first-to-last timestamps and tool_use blocks.  Both hooks merge through run_totals, so either order
# ends in the same row.
COMPLETION_KEYS = ("status", "usage", "totalTokens", "totalDurationMs", "totalToolUseCount", "toolStats", "modelsUsed")


def record_completion(con, member, resp):
    """A completed foreground Agent call: the child's final text into return_text, and what the
    completion reports merged into the run totals (run_totals)."""
    content = resp.get("content")
    text = None
    if isinstance(content, list):
        text = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    totals = transcripts.run_totals(member, completion={key: resp[key] for key in COMPLETION_KEYS if key in resp})
    con.execute(
        "UPDATE members SET return_text = COALESCE(?, return_text), total_tokens = ?, duration_ms = ?, tool_uses = ?, usage_json = ? WHERE id = ?",
        (text if text else None, totals["total_tokens"], totals["duration_ms"], totals["tool_uses"], totals["usage_json"], member["id"]),
    )


def hook_post_tool_use(ctx, payload):
    if payload.get("tool_name") != "Agent" or not ctx.db_path.is_file():
        return hookio.SILENT
    con = ledgerdb.connect(ctx)
    try:
        at = kernel.now()
        resp = payload.get("tool_response")
        resp = resp if isinstance(resp, dict) else {}
        tool_use_id = payload.get("tool_use_id")
        agent_id = resp.get("agentId")
        data = {"tool_use_id": tool_use_id, "agent_id": agent_id, "status": resp.get("status")}
        with ledgerdb.write_txn(con):
            req = con.execute("SELECT * FROM spawn_requests WHERE tool_use_id = ?", (tool_use_id,)).fetchone() if isinstance(tool_use_id, str) else None
            gap = None
            if req is None and sessions.session_mode(ctx, con, payload)[0] == "plain":
                return hookio.SILENT  # Eric's own subagent in a session that is not Spud: PreToolUse wrote no row, and that is no gap
            if req is None:
                gap = "PostToolUse(Agent) for tool_use_id %s (agentId %s) has no spawn_requests row: the spawn was not seen by PreToolUse" % (tool_use_id, agent_id)
            elif req["decision"] != "allow":
                gap = "PostToolUse(Agent) for tool_use_id %s: the spawn was denied by PreToolUse yet a subagent ran (agentId %s)" % (tool_use_id, agent_id)
            elif req["member_id"] is None:
                gap = "PostToolUse(Agent) for tool_use_id %s matched no member row" % tool_use_id
            elif not isinstance(agent_id, str) or not agent_id:
                gap = "PostToolUse(Agent) for tool_use_id %s carries no agentId; nothing to bind" % tool_use_id
            if gap:
                ledgerdb.write_event(con, at, "hook:PostToolUse", "hook.error", gap, agent_id=agent_id if isinstance(agent_id, str) else None, data=data)
                return hookio.SILENT
            member = lookup.get_member_by_id(con, req["member_id"])
            con.execute("UPDATE spawn_requests SET agent_id = ? WHERE tool_use_id = ?", (agent_id, tool_use_id))
            if member["status"] not in kernel.ALIVE:
                ledgerdb.write_event(con, at, "hook:PostToolUse", "hook.error", "%s is %s; the binding of agent_id %s was skipped" % (lookup.member_ref(con, member["id"]), member["status"], agent_id),
                            ticket_id=member["ticket_id"], member_id=member["id"], agent_id=agent_id, data=data)
                return hookio.SILENT
            member = bind_member(con, at, "hook:PostToolUse", member, agent_id, session_id=payload.get("session_id"), tool_use_id=tool_use_id, resolved_model=resp.get("resolvedModel"))
            if resp.get("status") == "completed":
                record_completion(con, member, resp)
    finally:
        con.close()
    return hookio.SILENT


# A resume.  Eric resumes a spudagent that has already returned by sending it a message, and the harness
# gives the hooks one signal for it and only one: SubagentStart fires again for the same agent_id.  Probed live on
# 2026-09-15 with a scout (agent_id aca970f6a277bd623): events 3685 (started), 3688 (stopped), then
# SendMessage, then 3689, a second member.started carrying the same three fields, agent_type, session_id and cwd, and
# nothing else; no PreToolUse or PostToolUse names SendMessage.  So the resume is read from the row, not the payload.
# Before this the return stood through the whole second round: Spud's Stop held with Law 9 and named a member that was
# running again, the board read `returned HH:MM, unrecorded`, and the first round's Result answered for the second
# return's hold.  A resume has a kind of its own (SPD-082): the SubagentStart writes the plain member.started every start
# writes and, beside it, a RESUME_KIND event carrying the stop it superseded and whether it cleared it, so member.started
# keeps meaning one SubagentStart and the SubagentStop hold (subagent_stop.last_resume) keys on a kind.  Before migration
# 0010_member_resumed the resume rode on that member.started, marked by data `resumed` (SPD-050: a kind of its own was a
# migration of the append-only table); the migration copies those rows as they were written, so last_resume reads that
# shape too.
RESUME_KIND = "member.resumed"
RESUME_KIND_BEFORE_0010 = "member.started"  # with data resumed: a resume recorded before 0010_member_resumed


def resume_member(con, member):
    """A bound member whose SubagentStart fired again: (the row, the stop the start supersedes or None, whether the
    return was cleared).  A return still open -- active, no outcome -- is cleared, so the member counts as live again
    everywhere stopped_at is read: stop_owed's returned list, the board's returned label, a parent's unrecorded
    children, and the run, which now ends at the second stop.  A member its parent has already recorded (done,
    blocked, failed) keeps every stamp; only the event says it ran again."""
    if member is None or not member["stopped_at"]:
        return member, None, False
    was_stopped = member["stopped_at"]
    if member["status"] != "active" or (member["outcome"] or "").strip():
        return member, was_stopped, False
    con.execute("UPDATE members SET stopped_at = NULL WHERE id = ?", (member["id"],))
    return lookup.get_member_by_id(con, member["id"]), was_stopped, True


def hook_subagent_start(ctx, payload):
    if not ctx.db_path.is_file():
        return hookio.SILENT
    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        raise hookio.HookError("SubagentStart payload carries no agent_id")
    con = ledgerdb.connect(ctx)
    try:
        at = kernel.now()
        with ledgerdb.write_txn(con):
            member = con.execute("SELECT * FROM members WHERE agent_id = ?", (agent_id,)).fetchone()
            if member is None and not sessions.pending_spawn(con, payload.get("session_id")) and sessions.session_mode(ctx, con, payload)[0] == "plain":
                return hookio.SILENT  # Eric's own subagent in a session that is not Spud
            member, was_stopped, cleared = resume_member(con, member)
            ticket_id, member_id = (member["ticket_id"], member["id"]) if member else (None, None)
            ledgerdb.write_event(con, at, "hook:SubagentStart", "member.started", "subagent %s started (%s)" % (agent_id, payload.get("agent_type")),
                        ticket_id=ticket_id, member_id=member_id, agent_id=agent_id,
                        data={"agent_type": payload.get("agent_type"), "session_id": payload.get("session_id"), "cwd": payload.get("cwd")})
            if was_stopped:
                ledgerdb.write_event(con, at, "hook:SubagentStart", RESUME_KIND,
                            "subagent %s resumed (%s) after returning at %s" % (agent_id, payload.get("agent_type"), was_stopped),
                            ticket_id=ticket_id, member_id=member_id, agent_id=agent_id, data={"was_stopped_at": was_stopped, "cleared": cleared})
            context = "Ledger: your agent_id is `%s`; every `spud` command you run takes `--as %s`." % (agent_id, agent_id)
            named = member
            if named is None:
                # A background spawn starts before PostToolUse(Agent) binds it: when the session has exactly one spawn waiting
                # to bind (of this agent type), that is the member starting, and it is named here without being bound.
                params = [payload.get("session_id")]
                narrowing = ""
                if isinstance(payload.get("agent_type"), str) and payload.get("agent_type"):
                    narrowing = " AND (subagent_type IS NULL OR subagent_type = ?)"
                    params.append(payload["agent_type"])
                waiting = con.execute("SELECT member_id FROM spawn_requests WHERE session_id = ? AND decision = 'allow' AND member_id IS NOT NULL"
                                      " AND agent_id IS NULL" + narrowing, params).fetchall()
                if len(waiting) == 1:
                    named = lookup.get_member_by_id(con, waiting[0]["member_id"])
            if named:
                member = named
                ticket = lookup.get_ticket_by_id(con, member["ticket_id"])
                context += " You are %s/%s (%s, %s) on %s; your deliverables: %s." % (
                    ticket["team_key"], member["name"], member["lineage"], member["persona"], ticket["key"], ", ".join(json.loads(member["deliverables"])) or "none")
                project = con.execute("SELECT * FROM projects WHERE id = ?", (ticket["project_id"],)).fetchone()
                if ticket["worktree"]:  # a bound ticket's paths are its worktree's alone
                    context += (" Your ticket's project is `%s`, and your ticket is bound to its worktree `%s`: bare deliverables are relative"
                                " to that worktree alone, the same paths in the main checkout `%s` or in any other worktree are refused, and"
                                " `home:<glob>` names Spud's home (%s); that repository's CLAUDE.md and skills govern how you build and verify."
                                % (project["key"], ticket["worktree"], worktrees.project_root(ctx, project), ctx.home))
                else:
                    context += (" Your ticket's project is `%s`; bare deliverables are relative to `%s` or a worktree of it, `<key>:<glob>` names"
                                " another project's checkout and `home:<glob>` Spud's home (%s); that repository's CLAUDE.md and skills govern how"
                                " you build and verify." % (project["key"], worktrees.project_root(ctx, project), ctx.home))
    finally:
        con.close()
    return hookio.HookOutput({"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": context}})
