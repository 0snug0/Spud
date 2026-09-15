"""hooks/pretool: The PreToolUse handlers.  Moved from bin/spud_ledger.py (SPD-065)."""

import json
from pathlib import Path

from . import hookio, pathrule, recording
from ..core import kernel
from ..projects import sessions
from ..shell import bash_rule, spud_calls
from ..state import ledgerdb, lookup


# -- the PreToolUse handlers ---------------------------------------------------------


def deny_and_record(con, at, payload, reason, caller_agent_id, caller_member, ticket_id=None, extra=None):
    data = {"hook_event_name": "PreToolUse", "tool_name": payload.get("tool_name"), "tool_use_id": payload.get("tool_use_id"), "reason": reason}
    data.update(extra or {})
    ledgerdb.write_event(con, at, "hook:PreToolUse", "hook.denied", reason,
                ticket_id=ticket_id if ticket_id is not None else (caller_member["ticket_id"] if caller_member else None),
                member_id=caller_member["id"] if caller_member else None, agent_id=caller_agent_id, data=data)
    return hookio.pre_decision("deny", reason)


def hook_agent_spawn(ctx, con, at, payload, tool_input, caller_agent_id, caller_member, mode="spud"):
    tool_use_id = payload.get("tool_use_id")
    description = tool_input.get("description") if isinstance(tool_input.get("description"), str) else ""
    model = tool_input.get("model")
    subagent_type = tool_input.get("subagent_type")
    background = tool_input.get("run_in_background")
    extra = {"description": description, "subagent_type": subagent_type, "model": model}
    if not isinstance(tool_use_id, str) or not tool_use_id:
        return deny_and_record(con, at, payload, "malformed PreToolUse(Agent) payload: tool_use_id is missing, so the spawn cannot be recorded", caller_agent_id, caller_member, extra=extra)
    ticket = member = None
    reason = None
    m = hookio.DESCRIPTION.match(description)
    if not m:
        reason = "the description must be `SPUD-nnn/<Name> (<lineage>, <persona>)`: the team key, the pool name, the lineage and the persona of a planned member (got %r)" % description
    else:
        team, name, lineage, persona = m.group("team"), m.group("name"), m.group("lineage"), m.group("persona")
        ticket = con.execute("SELECT * FROM tickets WHERE team_key = ?", (team,)).fetchone()
        if ticket is None:
            reason = "no team %s in the ledger (the ticket must exist before a member is planned on it)" % team
        else:
            member = con.execute("SELECT * FROM members WHERE ticket_id = ? AND name = ?", (ticket["id"], name)).fetchone()
            if member is None:
                reason = "no planned member named %s on %s; plan it first with `spud member new` (Law 2: no brief, no spudagent)" % (name, team)
    if reason is None:
        ref = "%s/%s" % (ticket["team_key"], member["name"])
        limits = ctx.limits
        pending = con.execute(
            "SELECT tool_use_id, at FROM spawn_requests WHERE member_id = ? AND decision = 'allow' AND agent_id IS NULL AND tool_use_id != ? ORDER BY at, rowid LIMIT 1",
            (member["id"], tool_use_id),
        ).fetchone()
        if mode == "plain" and caller_member is None:  # SPD-014: the full check refuses a spudagent-shaped spawn in a session that is not Spud
            reason = ("this session is not Spud: a spudagent is spawned by the Spud session that planned it, and %s was not planned here"
                      " (type /spud to claim this session first)" % ref)
        elif member["status"] != "planned":
            reason = "%s is %s, not planned; a spawned member is never spawned twice, a re-spawn is a new member row (spud member new)" % (ref, member["status"])
        elif pending is not None:
            # The first allow reserves the row (Rooster's HIGH-1): until that spawn binds, a second
            # Agent call for the same member would run a copy the ledger cannot see or count.  The
            # way out names the caller's own actor (SPD-028): a spudagent cannot run `--as spud`
            # (Law 6 refuses it inside a subagent), so only Spud's own root row offers --next.
            fail = ("`spud --as %s member finish %s --status failed --outcome '<why>'`" % (caller_agent_id, ref)) if caller_agent_id else (
                    "`spud --as spud member finish %s --status failed --outcome '<why>' [--next '<what happens next>']`" % ref)
            reason = ("%s was already allowed to spawn at %s (tool_use_id %s) and is not yet bound; a planned member is spawned once."
                      " If that spawn failed in the harness, record it with %s"
                      " and plan a new member: a re-spawn is a new row" % (ref, pending["at"], pending["tool_use_id"], fail))
        elif member["lineage"] != lineage:
            reason = "lineage %s in the description, but %s is planned as %s" % (lineage, ref, member["lineage"])
        elif member["persona"] != persona:
            reason = "persona %s in the description, but %s is planned as a %s" % (persona, ref, member["persona"])
        elif not (member["brief"] or "").strip():
            reason = "Law 2: %s has no brief; no brief, no spudagent" % ref
        elif subagent_type == "fork" or tool_input.get("fork"):
            reason = "Law 3: a fork is refused (it inherits Spud's model and skips the depth cap); spawn subagent_type %s with an explicit model" % member["agent_type"]
        elif tool_input.get("isolation"):
            reason = "Law 3: isolation %r is refused; spudagents share the session's working tree so their ledger writes land in the one ledger" % tool_input.get("isolation")
        elif not model:
            reason = "Law 3: model is missing; every spawn names its model tier explicitly (%s is planned on %s)" % (ref, member["model"])
        elif model == "inherit":
            reason = "Law 3: model inherit is refused; name the tier (%s is planned on %s)" % (ref, member["model"])
        elif model != member["model"]:
            reason = "Law 3: model must equal the planned tier %s, not %s (change the plan with `spud member edit --model` first)" % (member["model"], model)
        elif subagent_type != member["agent_type"]:
            reason = "agent_type: %s is planned as %s, the call asks for subagent_type %s" % (ref, member["agent_type"], subagent_type)
        elif caller_agent_id and caller_member is None:
            reason = "caller agent_id %s is not bound to a member; only a bound member (or Spud, with no agent_id) spawns" % caller_agent_id
        elif (caller_member["id"] if caller_member else None) != member["parent_id"]:
            reason = "parent: %s's parent is %s, the caller is %s; a member is spawned by the parent that planned it" % (
                ref, lookup.member_ref(con, member["parent_id"]) or "Spud", lookup.member_ref(con, caller_member["id"]) if caller_member else "Spud")
        else:
            depth = member["lineage"].count(".") + 1
            parent_id = member["parent_id"]
            alive = con.execute("SELECT count(*) FROM members WHERE ticket_id = ? AND parent_id IS ? AND status IN ('planned','active')", (member["ticket_id"], parent_id)).fetchone()[0]
            cap_name = "root_fan_out" if parent_id is None else "child_fan_out"
            cap = limits[cap_name]
            total = con.execute("SELECT count(*) FROM members WHERE status IN ('planned','active')").fetchone()[0]
            if depth > limits["max_depth"]:
                reason = "Law 4: depth %d exceeds limits.max_depth %d" % (depth, limits["max_depth"])
            elif alive > cap:
                reason = "Law 4: fan-out: %d children alive under %s (this one included), limits.%s is %d; a child frees its slot when it finishes" % (
                    alive, lookup.member_ref(con, parent_id) or "Spud", cap_name, cap)
            elif total > limits["max_concurrent_total"]:
                reason = "Law 4: concurrency: %d members alive across every ticket (this one included), limits.max_concurrent_total is %d" % (total, limits["max_concurrent_total"])
    decision = "deny" if reason else "allow"
    con.execute(
        "INSERT INTO spawn_requests (tool_use_id, session_id, at, caller_agent_id, description, subagent_type, model, run_in_background, member_id, decision, reason)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(tool_use_id) DO UPDATE SET session_id = excluded.session_id, at = excluded.at, caller_agent_id = excluded.caller_agent_id,"
        " description = excluded.description, subagent_type = excluded.subagent_type, model = excluded.model, run_in_background = excluded.run_in_background,"
        " member_id = excluded.member_id, decision = excluded.decision, reason = excluded.reason",
        (tool_use_id, str(payload.get("session_id") or ""), at, caller_agent_id, description, subagent_type if isinstance(subagent_type, str) else None,
         model if isinstance(model, str) else None, None if background is None else int(bool(background)), member["id"] if member else None, decision, reason),
    )
    data = {"tool_use_id": tool_use_id, "subagent_type": subagent_type, "model": model, "run_in_background": background,
            "caller": lookup.member_ref(con, caller_member["id"]) if caller_member else "spud", "caller_agent_id": caller_agent_id}
    if decision == "allow":
        ledgerdb.write_event(con, at, "hook:PreToolUse", "member.spawned", "spawn allowed: %s" % description, ticket_id=member["ticket_id"], member_id=member["id"], data=data)
        return hookio.pre_decision("allow", "spawn of %s recorded (%s)" % (description, tool_use_id))
    data["reason"] = reason
    if member is not None:
        ledgerdb.write_event(con, at, "hook:PreToolUse", "member.spawn_denied", reason, ticket_id=member["ticket_id"], member_id=member["id"], data=data)
        return hookio.pre_decision("deny", reason)
    return deny_and_record(con, at, payload, reason, caller_agent_id, caller_member, ticket_id=ticket["id"] if ticket else None, extra={"description": description})


def hook_bash(ctx, con, at, payload, tool_input, caller_agent_id, caller_member, mode="spud"):
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return deny_and_record(con, at, payload, "malformed PreToolUse(Bash) payload: tool_input.command is missing", caller_agent_id, caller_member)
    reason, analysis = bash_rule.bash_reason(ctx, con, caller_agent_id, caller_member, command, payload.get("cwd") or None, mode)
    if reason:
        return deny_and_record(con, at, payload, reason, caller_agent_id, caller_member, extra={"command": command[:2000]})
    # The allow skips the harness's prompt, so it needs more than recognition (SPD-032): every spud call runs the ledger root's
    # launcher through an interpreter, options and environment the hook vouches for (vouched_spud_call), and the line writes
    # no file by redirection, which the prompt would otherwise ask about.  Anything else recognized stays silent.
    if analysis is not None and analysis.all_spud \
            and all((d["command"] in hookio.SPUD_COMMANDS or (d["command"] is None and d["help"])) and d.get("vouched") for k, d in analysis.findings if k == "spud") \
            and all(target in spud_calls.QUIET_TARGETS for target, _cwds in analysis.redirects):
        who = lookup.member_ref(con, caller_member["id"]) if caller_member else ("agent_id %s" % caller_agent_id if caller_agent_id else "Spud")
        return hookio.pre_decision("allow", "a well-formed spud call by %s through the ledger root's own launcher, checked by the ledger hook" % who)
    return hookio.SILENT


def hook_edit(ctx, con, at, payload, tool_input, caller_agent_id, caller_member, mode="spud"):
    tool = payload.get("tool_name")
    field = "notebook_path" if tool == "NotebookEdit" else "file_path"
    path = tool_input.get(field)
    if not isinstance(path, str) or not path:
        return deny_and_record(con, at, payload, "malformed PreToolUse(%s) payload: tool_input.%s is missing" % (tool, field), caller_agent_id, caller_member)
    reason, rel = pathrule.edit_reason(ctx, con, caller_agent_id, caller_member, path, payload.get("cwd") or None, mode)
    if reason:
        return deny_and_record(con, at, payload, reason, caller_agent_id, caller_member, extra={"path": rel if rel is not None else path})
    return hookio.SILENT


def request_from_meta(con, meta_path, agent_id):
    """The allowed, still unbound spawn_requests row named by agent-<id>.meta.json's toolUseId
    (the harness writes the file about a second after the spawn; it is an internal file, so
    it is only ever a fallback)."""
    if meta_path is None or not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tid = meta.get("toolUseId") if isinstance(meta, dict) else None
    if not isinstance(tid, str) or not tid:
        return None
    req = con.execute(
        "SELECT * FROM spawn_requests WHERE tool_use_id = ? AND decision = 'allow' AND member_id IS NOT NULL AND (agent_id IS NULL OR agent_id = ?)",
        (tid, agent_id),
    ).fetchone()
    if req is None:
        return None
    # The meta must describe the request it names (Rooster's MEDIUM-4): a forged file pointing at
    # someone else's request carries the wrong description or agent type.
    if "description" in meta and meta.get("description") != req["description"]:
        return None
    if "agentType" in meta and req["subagent_type"] is not None and meta.get("agentType") != req["subagent_type"]:
        return None
    return req


def bind_request(con, at, actor_label, req, agent_id, session_id):
    """Bind the member of an allowed request to agent_id when it is alive and unbound."""
    candidate = lookup.get_member_by_id(con, req["member_id"])
    if candidate is None or candidate["status"] not in kernel.ALIVE or candidate["agent_id"]:
        return None
    con.execute("UPDATE spawn_requests SET agent_id = ? WHERE tool_use_id = ?", (agent_id, req["tool_use_id"]))
    return recording.bind_member(con, at, actor_label, candidate, agent_id, session_id=session_id, tool_use_id=req["tool_use_id"])


def late_bind(con, at, payload, agent_id):
    """A foreground spawn's first tool call: bind it from the meta.json beside the session's
    subagent transcripts (<project>/<session_id>/subagents/agent-<id>.meta.json)."""
    transcript = payload.get("transcript_path")
    session = payload.get("session_id")
    if not (isinstance(transcript, str) and transcript and isinstance(session, str) and session):
        return None
    meta_path = Path(transcript).parent / session / "subagents" / ("agent-%s.meta.json" % agent_id)
    req = request_from_meta(con, meta_path, agent_id)
    if req is None:
        return None
    return bind_request(con, at, "hook:PreToolUse", req, agent_id, session)


def hook_pre_tool_use(ctx, payload):
    tool = payload.get("tool_name")
    if tool not in hookio.ENFORCED_TOOLS:
        return hookio.SILENT
    if not ctx.db_path.is_file():
        if ctx.hook_project and not payload.get("agent_id"):
            return hookio.SILENT  # a project's hook with no ledger to read fails open for a caller with no agent_id (design section 6.4)
        return hookio.pre_decision("deny", "no ledger database at %s; run `spud init` (the enforcing hooks refuse until the ledger exists)" % ctx.db_path)
    con = ledgerdb.connect(ctx)
    try:
        at = kernel.now()
        caller_agent_id = payload.get("agent_id") if isinstance(payload.get("agent_id"), str) and payload.get("agent_id") else None
        tool_input = payload.get("tool_input")
        with ledgerdb.write_txn(con):
            caller_member = con.execute("SELECT * FROM members WHERE agent_id = ?", (caller_agent_id,)).fetchone() if caller_agent_id else None
            if caller_agent_id and caller_member is None:
                caller_member = late_bind(con, at, payload, caller_agent_id)
            if not isinstance(tool_input, dict):
                return deny_and_record(con, at, payload, "malformed PreToolUse(%s) payload: tool_input is not an object" % tool, caller_agent_id, caller_member)
            mode = sessions.session_mode(ctx, con, payload)[0]
            if tool == "Agent":
                if mode == "plain" and caller_member is None and not sessions.spudagent_shaped(tool_input):
                    return hookio.SILENT  # Eric's own subagent in a session that is not Spud: no check, no spawn_requests row (SPD-014)
                return hook_agent_spawn(ctx, con, at, payload, tool_input, caller_agent_id, caller_member, mode)
            if tool == "Bash":
                return hook_bash(ctx, con, at, payload, tool_input, caller_agent_id, caller_member, mode)
            return hook_edit(ctx, con, at, payload, tool_input, caller_agent_id, caller_member, mode)
    finally:
        con.close()
