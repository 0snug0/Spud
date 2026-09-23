"""commands/membercmds: member new, start, finish, edit, log, result, block, show."""

import contextlib
import json
import os

from . import reportentry, worktreebind
from ..core import kernel, markdown
from ..state import actors, ledgerdb, lookup, ops


def cmd_member_new(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        ops.check_prose_headings(args.brief, markdown.IMPORT_MEMBER_SECTIONS, "--brief")
        binder = worktreebind.Binder(ctx, worktreebind.working_directory())  # a code ticket binds a worktree
        binder.prepare(con, actor, worktreebind.planned_ticket(con, actor, args.ticket), args.deliverable)
        m = ops.plan_member(ctx, con, actor, args.ticket, args.persona, args.model, name=args.name, tier_reason=args.tier_reason,
                        agent_type=args.agent_type, brief=args.brief or "", deliverables=args.deliverable,
                        session_id=actors.planning_session(os.environ), binder=binder)
        d = lookup.member_dict(con, m)
    finally:
        con.close()
    return kernel.Result({"member": d}, "planned %s (%s, %s, %s) on %s" % (d["ref"], d["lineage"], d["persona"], d["model"], d["ticket"]))


def cmd_member_start(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        at = kernel.now()
        with ledgerdb.write_txn(con):
            m = lookup.get_member(con, args.ref)
            actors.require_ancestor(con, actor, m, "starting a member (its status)")
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
                updates["agent_type"] = args.agent_type
            if args.model is not None and args.model != m["model"]:
                default_tier = ctx.persona_tier(m["persona"])
                if default_tier and args.model != default_tier and not args.tier_reason:
                    raise kernel.SpudError(kernel.EXIT_ERROR, "%s defaults to %s; model %s needs --tier-reason" % (m["persona"], default_tier, args.model))
                updates["model"] = args.model
            if args.tier_reason is not None and args.tier_reason != m["tier_reason"]:
                updates["tier_reason"] = args.tier_reason
            if updates:
                con.execute("UPDATE members SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in updates), (*updates.values(), m["id"]))
                ledgerdb.write_event(con, at, actor.label, "member.edited", "%s edited: %s" % (lookup.member_ref(con, m["id"]), ", ".join(sorted(updates))),
                            ticket_id=m["ticket_id"], member_id=m["id"], data={"fields": sorted(updates)})
            m = lookup.get_member_by_id(con, m["id"])
        d = lookup.member_dict(con, m)
        changed = sorted(updates)
    finally:
        con.close()
    return kernel.Result({"member": d, "changed": changed}, "%s edited: %s" % (d["ref"], ", ".join(changed) or "nothing to change"))


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
    if d["tier_reason"]:
        lines.append("tier reason: " + d["tier_reason"])
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
