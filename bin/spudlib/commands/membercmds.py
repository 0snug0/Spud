"""commands/membercmds: member new, respawn, start, finish, edit, log, result, block, show.

Past 250 lines since SPD-318 added respawn, and kept whole: it is the one family of member commands, each a thin shell
over state/ops, and respawn shares plan_line and spawn_data with new and edit."""

import contextlib
import json
import os

from . import reportentry, worktreebind
from ..core import kernel, markdown
from ..render import sectiontext
from ..state import actors, ledgerdb, lookup, ops


def cmd_member_new(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        ops.check_prose_headings(args.brief, markdown.IMPORT_MEMBER_SECTIONS, "--brief")
        binder = worktreebind.Binder(ctx, worktreebind.working_directory())  # a code ticket binds a worktree
        binder.prepare(con, actor, worktreebind.planned_ticket(con, actor, args.ticket), args.deliverable)
        m, note = ops.plan_member(ctx, con, actor, args.ticket, args.persona, args.model, name=args.name, tier_reason=args.tier_reason,
                              agent_type=args.agent_type, brief=args.brief or "", deliverables=args.deliverable,
                              session_id=actors.planning_session(os.environ), binder=binder, escalates=args.escalates,
                              effort=args.effort)
        d = lookup.member_dict(con, m)
    finally:
        con.close()
    return kernel.Result(dict({"member": d}, **spawn_data(d, note)), plan_line(d, note))


def spawn_data(d, note):
    """What member new and member edit add to --json beside the member (SPD-222): the subagent_type it is spawned as,
    and the note when an --effort given was not recorded."""
    data = {"subagent_type": kernel.spawn_type(d["agent_type"], d["effort"])}
    if note:
        data["note"] = note
    return data


def plan_line(d, note):
    """`planned SPUD-nnn/<Name> (<lineage>, <persona>, <model>) on SPD-nnn at <effort> effort: spawn it as subagent_type
    <type>` -- the handle, and the two things the Agent call must carry that the description does not (SPD-222)."""
    at = " at %s effort" % d["effort"] if d["effort"] else ""
    line = "planned %s (%s, %s, %s) on %s%s: spawn it as subagent_type %s" % (
        d["ref"], d["lineage"], d["persona"], d["model"], d["ticket"], at, kernel.spawn_type(d["agent_type"], d["effort"]))
    return line + ("\nnote: " + note if note else "")


def agent_call(d):
    """The Agent call that spawns planned member `d`, as the PreToolUse(Agent) check holds it (SPD-318): the prompt is the
    brief template, which `member show` fills with the brief."""
    return {"subagent_type": kernel.spawn_type(d["agent_type"], d["effort"]), "model": d["model"],
            "description": "%s (%s, %s)" % (d["ref"], d["lineage"], d["persona"]), "run_in_background": True}


def agent_call_lines(d):
    call = agent_call(d)
    return (["Agent call:"] + ["  %s: %s" % (k, "true" if v is True else v) for k, v in call.items()]
            + ["  prompt: the brief template, naming %s (`spud member show %s` prints its brief)" % (d["ref"], d["ref"])])


def quoted(text):
    """`text` as a markdown block quote, so no line of it reads as one of a member note's own headings."""
    return "\n".join("> " + line if line else ">" for line in text.split("\n"))


def respawn_brief(con, old, ref, brief, answer):
    """The re-spawned member's brief (SPD-318): `brief`, or the old member's, then its record under Read first -- the
    answer to its Blocked question when there is one, and its Blocked, Result and Log, each quoted -- so the new run starts
    from what the old one found and left in the tree rather than from nothing."""
    parts = [(brief if brief is not None else old["brief"] or "").rstrip(),
             "Read first, the prior run: this member re-spawns %s (%s, %s), which returned %s. A spudagent is never resumed"
             " (SPD-318), so its record is here: continue from its work in the tree rather than starting over."
             % (ref, old["lineage"], old["persona"], old["status"])]
    if answer:
        parts.append("The answer to its Blocked question:\n" + quoted(answer))
    for label, text in (("Its Blocked question", old["blocked"]), ("Its Result", old["result"]),
                        ("Its Log", sectiontext.render_log_rows(con, old))):
        if text and text.strip():
            parts.append("%s:\n%s" % (label, quoted(text.strip())))
    return "\n\n".join(p for p in parts if p)


def cmd_member_respawn(ctx, args):
    """member respawn: plan a returned member again as a new row, since a spudagent is never resumed with SendMessage
    (SPD-318): the same persona, model, effort, agent type and deliverables under the same parent, its brief carrying the
    old row's record (respawn_brief), and the exact Agent call printed."""
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        old = lookup.get_member(con, args.ref)
        ref = lookup.member_ref(con, old["id"])
        caller_id = actor.member["id"] if actor.kind == "member" else None
        if old["parent_id"] != caller_id:
            raise kernel.SpudError(kernel.EXIT_OWNERSHIP, "%s's parent is %s; a member is re-spawned by the parent that planned it, not by %s"
                                   % (ref, lookup.member_ref(con, old["parent_id"]) or "Spud", actor.ref(con)))
        if old["status"] == "planned":
            raise kernel.SpudError(kernel.EXIT_TRANSITION, "%s is planned and was never spawned: spawn it with Agent as planned (`spud member show %s`"
                                   " prints its subagent_type)" % (ref, ref))
        if old["status"] == "active":
            raise kernel.SpudError(kernel.EXIT_TRANSITION, "%s is active: record its return first (`member finish %s --status blocked|failed|done"
                                   " --outcome '…'`), then re-spawn it" % (ref, ref))
        brief = respawn_brief(con, old, ref, args.brief, args.answer)
        ops.check_prose_headings(brief, markdown.IMPORT_MEMBER_SECTIONS, "the re-spawned member's brief")
        ticket = lookup.get_ticket_by_id(con, old["ticket_id"])
        deliverables = json.loads(old["deliverables"])
        binder = worktreebind.Binder(ctx, worktreebind.working_directory())
        binder.prepare(con, actor, worktreebind.planned_ticket(con, actor, ticket["key"]), deliverables)
        m, note = ops.plan_member(ctx, con, actor, ticket["key"], old["persona"], old["model"], tier_reason=old["tier_reason"] or (
                                      None if old["model"] == ctx.persona_tier(old["persona"]) else "re-spawn of %s" % ref),
                                  agent_type=old["agent_type"], brief=brief, deliverables=deliverables,
                                  session_id=actors.planning_session(os.environ), binder=binder, effort=old["effort"], respawns=ref)
        d = lookup.member_dict(con, m)
    finally:
        con.close()
    lines = [plan_line(d, note), "re-spawns %s (%s); its record is under Read first in the new brief" % (ref, old["status"])] + agent_call_lines(d)
    return kernel.Result(dict({"member": d, "respawns": ref, "agent_call": agent_call(d)}, **spawn_data(d, note)), "\n".join(lines))


def cmd_member_start(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        at = kernel.now()
        with ledgerdb.write_txn(con):
            m = lookup.get_member(con, args.ref)
            actors.require_ancestor(con, actor, m, "starting a member (its status)")
            if m["status"] == "blocked" and m["agent_id"]:
                # A spawned member that returned blocked has no way to run again as this row: SendMessage is refused
                # (SPD-318) and Agent spawns only a planned one.  Its re-spawn is a new row.
                ref = lookup.member_ref(con, m["id"])
                raise kernel.SpudError(kernel.EXIT_TRANSITION, "%s returned blocked and a spudagent is never resumed (SPD-318): re-spawn it with"
                                       " `spud --as %s member respawn %s [--answer '…'] [--brief @-]` and issue the Agent call it prints"
                                       % (ref, args.actor, ref))
            ops.member_status_change(con, at, actor.label, m, "active")
            m = lookup.get_member_by_id(con, m["id"])
        d = lookup.member_dict(con, m)
    finally:
        con.close()
    return kernel.Result({"member": d}, "%s is active (spawned %s)" % (d["ref"], kernel.fm_minute(d["spawned_at"])))


def cmd_member_finish(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        reportentry.check_next(con, actor, args)
        at = kernel.now()
        entry = None
        with ledgerdb.write_txn(con):
            m = lookup.get_member(con, args.ref)
            actors.require_ancestor(con, actor, m, "finishing a member (status, outcome, finished)")
            ops.check_prose_headings(args.outcome, markdown.IMPORT_MEMBER_SECTIONS, "--outcome")
            if args.next is not None and m["parent_id"] is not None:
                raise reportentry.no_entry_for_next("member finish writes one only for a root member, and %s is %s's child" % (lookup.member_ref(con, m["id"]), lookup.member_ref(con, m["parent_id"])))
            extra = {"outcome": args.outcome}
            if args.summary is not None:
                extra["summary"] = args.summary
            # A finished member decides nothing more, so say what it was holding and who it now falls to.  This
            # refuses nothing and moves nothing: recording a returned member's outcome is Law 9, and the climb is
            # resolved and written at `proposal decide`, so a blocked member that is re-briefed keeps what nobody decided.
            held = lookup.held_proposals(con, m["id"])
            ops.member_status_change(con, at, actor.label, m, args.status, extra, outcome_event=args.outcome)
            m = lookup.get_member_by_id(con, m["id"])
            falls_to = lookup.member_ref(con, lookup.effective_holder(con, m["id"])) if held else None
            held = [dict(h, holder=falls_to) for h in held]
            if actor.kind == "spud" and m["parent_id"] is None:  # report entries are Spud's: his own children's verdicts
                ticket = lookup.get_ticket_by_id(con, m["ticket_id"])
                summary = m["summary"] if m["summary"] and m["summary"].strip() else None
                entry = reportentry.write_report_entry(con, at, "%s: %s (%s, %s, %s) %s" % (ticket["key"], m["name"], m["lineage"], m["persona"], m["model"], m["status"]),
                                           "member finish", ticket["id"], member_id=m["id"], lines=[summary] if summary else [], next_line=args.next)
        d = lookup.member_dict(con, m)
    finally:
        con.close()
    data = {"member": d}
    text = "%s is %s (finished %s)" % (d["ref"], d["status"], d["finished_at"])
    if held:
        data["held_proposals"] = held
        text += "\n%s left %d open proposal%s for %s to decide: %s" % (
            d["ref"], len(held), "" if len(held) == 1 else "s", lookup.holder_name(held[0]["holder"]),
            ", ".join("%d (%s)" % (h["id"], h["title"]) for h in held))
    return reportentry.with_report_entry(data, text, entry)


def cmd_member_edit(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        binder = worktreebind.Binder(ctx, worktreebind.working_directory())  # new deliverables bind as member new's do
        if args.deliverable is not None:
            with contextlib.suppress(kernel.SpudError):  # the transaction reports a member it cannot read
                binder.prepare(con, actor, lookup.get_ticket_by_id(con, lookup.get_member(con, args.ref)["ticket_id"]), args.deliverable)
        at = kernel.now()
        with ledgerdb.write_txn(con):
            m = lookup.get_member(con, args.ref)
            actors.require_ancestor(con, actor, m, "editing a member's brief, deliverables, model or summary")
            ops.check_prose_headings(args.brief, markdown.IMPORT_MEMBER_SECTIONS, "--brief")
            updates = {}
            if args.brief is not None and args.brief != m["brief"]:
                updates["brief"] = args.brief
            if args.deliverable is not None and json.dumps(ops.normalize_deliverables(args.deliverable)) != m["deliverables"]:
                ops.check_deliverable_projects(con, ops.normalize_deliverables(args.deliverable))
                binder.decide(con, at, actor, lookup.get_ticket_by_id(con, m["ticket_id"]), ops.normalize_deliverables(args.deliverable))
                updates["deliverables"] = json.dumps(ops.normalize_deliverables(args.deliverable))
            if args.summary is not None and args.summary != m["summary"]:
                updates["summary"] = args.summary
            if args.agent_type is not None and args.agent_type != m["agent_type"]:
                ops.check_agent_type(args.agent_type)
                updates["agent_type"] = args.agent_type
            if args.model is not None and args.model != m["model"]:
                default_tier = ctx.persona_tier(m["persona"])
                if default_tier and args.model != default_tier and not args.tier_reason:
                    raise kernel.SpudError(kernel.EXIT_ERROR, "%s defaults to %s; model %s needs --tier-reason" % (m["persona"], default_tier, args.model))
                updates["model"] = args.model
            if args.tier_reason is not None and args.tier_reason != m["tier_reason"]:
                updates["tier_reason"] = args.tier_reason
            note = None
            if args.effort is not None and m["status"] != "planned":
                raise kernel.SpudError(kernel.EXIT_TRANSITION, "%s is %s; its effort changes only while it is planned, before it is spawned"
                                       " (a re-run at another effort is a new member)" % (lookup.member_ref(con, m["id"]), m["status"]))
            if "model" in updates or "agent_type" in updates or args.effort is not None:
                # the effort follows the model and the definition it runs as, and --effort, above an escalation's floor
                target = lookup.get_member_by_id(con, m["escalates_id"]) if m["escalates_id"] else None
                floor = (lookup.member_ref(con, target["id"]), target["effort"]) if target is not None else None
                effort, note = ops.planned_effort(ctx, m["persona"], updates.get("model", m["model"]), updates.get("agent_type", m["agent_type"]),
                                                  effort=args.effort, kept=m["effort"], floor=floor)
                if effort != m["effort"]:
                    updates["effort"] = effort
            if updates:
                con.execute("UPDATE members SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in updates), (*updates.values(), m["id"]))
                ledgerdb.write_event(con, at, actor.label, "member.edited", "%s edited: %s" % (lookup.member_ref(con, m["id"]), ", ".join(sorted(updates))),
                            ticket_id=m["ticket_id"], member_id=m["id"], data={"fields": sorted(updates)})
            m = lookup.get_member_by_id(con, m["id"])
        d = lookup.member_dict(con, m)
        changed = sorted(updates)
    finally:
        con.close()
    text = "%s edited: %s" % (d["ref"], ", ".join(changed) or "nothing to change")
    if "effort" in changed or "model" in changed or "agent_type" in changed:
        text += "; spawn it as subagent_type %s" % kernel.spawn_type(d["agent_type"], d["effort"])
    return kernel.Result(dict({"member": d, "changed": changed}, **spawn_data(d, note)), text + ("\nnote: " + note if note else ""))


def cmd_member_own(ctx, args, kind):
    """member log|result|block: the member's own sections."""
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        what = {"log": "the Log", "result": "the Result", "block": "the Blocked section"}[kind]
        actors.require_member(con, actor, what)
        if kind != "log":  # a Log renders each entry's later lines indented, so no log line reads as a heading
            ops.check_prose_headings(args.text, markdown.IMPORT_MEMBER_SECTIONS, what)
        at = kernel.now()
        with ledgerdb.write_txn(con):
            m = lookup.get_member_by_id(con, actor.member["id"])
            if kind == "log":
                ledgerdb.write_event(con, at, actor.label, "member.log", args.text, ticket_id=m["ticket_id"], member_id=m["id"])
            elif kind == "result":
                con.execute("UPDATE members SET result = ? WHERE id = ?", (args.text, m["id"]))
                ledgerdb.write_event(con, at, actor.label, "member.result", args.text, ticket_id=m["ticket_id"], member_id=m["id"])
            else:
                con.execute("UPDATE members SET blocked = ? WHERE id = ?", (args.text, m["id"]))
                ledgerdb.write_event(con, at, actor.label, "member.blocked", args.text, ticket_id=m["ticket_id"], member_id=m["id"])
            m = lookup.get_member_by_id(con, m["id"])
        d = lookup.member_dict(con, m)
    finally:
        con.close()
    verb = {"log": "logged", "result": "recorded the Result of", "block": "recorded the Blocked section of"}[kind]
    return kernel.Result({"member": d}, "%s %s" % (verb, d["ref"]))


def format_member(d):
    lines = ["%s (%s, %s, %s) %s on %s" % (d["ref"], d["lineage"], d["persona"], d["model"], d["status"], d["ticket"])]
    lines.append("parent: %s   planned: %s   spawned: %s   finished: %s" % (d["parent"] or "Spud", d["planned_at"], d["spawned_at"] or "-", d["finished_at"] or "-"))
    if d["effort"]:
        lines.append("effort: " + d["effort"])
    lines.append("subagent_type: " + kernel.spawn_type(d["agent_type"], d["effort"]))  # what the Agent call carries (SPD-222)
    if d["tier_reason"]:
        lines.append("tier reason: " + d["tier_reason"])
    if d["escalates"]:
        lines.append("escalates: " + d["escalates"])
    if d["respawns"]:  # SPD-321: the re-spawn chain, both ways
        lines.append("re-spawns: " + d["respawns"])
    if d["respawned_as"]:
        lines.append("re-spawned as: " + d["respawned_as"])
    if d["deliverables"]:
        lines.append("deliverables: " + ", ".join(d["deliverables"]))
    for name, key in (("Brief", "brief"), ("Result", "result"), ("Blocked", "blocked"), ("Outcome", "outcome"), ("Summary", "summary")):
        if d[key]:
            lines.append("")
            lines.append("## " + name)
            lines.append(d[key])
    return "\n".join(lines)


def cmd_member_show(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        d = lookup.member_dict(con, lookup.get_member(con, args.ref))
    finally:
        con.close()
    return kernel.Result({"member": d}, format_member(d))
