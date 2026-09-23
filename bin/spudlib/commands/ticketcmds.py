"""commands/ticketcmds: ticket new, move, edit, show."""

import json
import os

from . import reportentry
from ..core import kernel
from ..hooks import worktrees
from ..state import actors, ledgerdb, lookup, ops


# Report entries.  Spud's recording commands write their own report.entry in the transaction of
# the record they make: ticket new, ticket move, ticket edit when --priority changes the priority, member
# finish of a root member, and proposal decide.  The title is generated and the body is `- ` lines; Spud
# types only the Next line, with --next.  `report add` stays for what no command records (a merge, an install).
TICKET_MOVE_VERBS = {"active": "started", "done": "done", "queued": "queued", "parked": "parked", "declined": "declined"}


def parked_tail(until, reason):
    """What follows the word `parked` wherever one line carries it: ` until 2026-10-16: <reason>`, else `: <reason>`."""
    return ((" until " + until) if until else "") + ": " + (reason or "")


def cmd_ticket_new(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "creating a ticket (Law 6)")
        reportentry.check_next(con, actor, args)
        if args.project:
            project = con.execute("SELECT * FROM projects WHERE key = ?", (args.project,)).fetchone()
            if project is None:
                raise kernel.SpudError(kernel.EXIT_ERROR, "no project %r" % args.project)
        else:  # the project of the working directory, else project 1 (the home is no project)
            try:
                mapped = worktrees.cli_project_of(ctx, con, os.getcwd())
            except OSError:
                mapped = None
            project = mapped[0] if mapped and not worktrees.is_home(mapped[0]) else con.execute("SELECT * FROM projects WHERE id = 1").fetchone()
            if project is None:  # a home may hold no project, and then it can hold no ticket either
                raise kernel.SpudError(kernel.EXIT_ERROR, "no project is registered; `spud --as spud project add <path> --key <key>"
                                " --ticket-prefix %s --team-prefix %s --landing merge` registers one, or give --project"
                                % (ctx.config.get("tickets", {}).get("prefix", "SPD"), ctx.config.get("teams", {}).get("prefix", "SPUD")))
        if project["archived_at"]:
            raise kernel.SpudError(kernel.EXIT_ERROR, "project %s is archived (%s); no ticket is created in it" % (project["key"], kernel.fm_date(project["archived_at"])))
        for field in ("brief", "sizing", "outcome"):
            ops.check_prose_headings(getattr(args, field), kernel.TICKET_SECTIONS, "--" + field)
        tags = ["ticket"] + [t for t in (args.tag or []) if t != "ticket"]
        at = kernel.now()
        with ledgerdb.write_txn(con):
            t = ops.insert_ticket(con, at, actor.label, project, args.title, args.priority, args.status,
                              brief=args.brief or "", sizing=args.sizing or "", outcome=args.outcome or "", tags=tags, heading=args.heading)
            ledgerdb.write_event(con, at, actor.label, "ticket.created", "%s created: %s" % (t["key"], t["title"]), ticket_id=t["id"],
                        data={"origin": "owner", "priority": t["priority"], "status": t["status"]})
            entry = reportentry.write_report_entry(con, at, "%s created (%s, %s): %s" % (t["key"], t["status"], t["priority"], t["title"]),
                                       "ticket new", t["id"], next_line=args.next)
        d = lookup.ticket_dict(con, t)
    finally:
        con.close()
    # Project 1 is the project every unqualified `ticket new` lands in, so its key adds nothing to the line.
    keys = d["team_key"] if project["id"] == 1 else "%s, %s" % (d["team_key"], d["project"])
    return reportentry.with_report_entry({"ticket": d}, "%s (%s) created: %s [%s]" % (d["key"], keys, d["title"], d["status"]), entry)


def cmd_ticket_move(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "moving a ticket")
        reportentry.check_next(con, actor, args)
        # The reason qualifies the status, so it is required to park and refused to anything else, both before
        # a row is read.  --reason on any other move keeps its old meaning: a note in the event body, stored nowhere.
        parked = args.status == "parked"
        if parked and not (args.reason or "").strip():
            raise kernel.SpudError(kernel.EXIT_USAGE, "`--status parked` needs `--reason`: why it is parked"
                            " (and `--until YYYY-MM-DD` when an outside event should bring it back)")
        if args.until and not parked:
            raise kernel.SpudError(kernel.EXIT_USAGE, "`--until` goes with `--status parked`")
        at = kernel.now()
        with ledgerdb.write_txn(con):
            t = lookup.get_ticket(con, args.key)
            ops.check_transition("tickets", t["status"], args.status, t["key"])
            if parked:  # the brief board shows live members under active tickets alone, so a parked ticket has none
                alive = [r["name"] for r in con.execute(
                    "SELECT name FROM members WHERE ticket_id = ? AND status IN ('planned','active') ORDER BY lineage", (t["id"],)).fetchall()]
                if alive:
                    raise kernel.SpudError(kernel.EXIT_ERROR, "%s has %d member%s alive (%s); finish or fail them before parking it"
                                    % (t["key"], len(alive), "" if len(alive) == 1 else "s", ", ".join(alive)))
            closed = at if args.status in ("done", "declined") else None
            # one statement, because the CHECKs of migration 0003_parked read status, parked_until and parked_reason as one row
            con.execute("UPDATE tickets SET status = ?, parked_until = ?, parked_reason = ?, updated_at = ?, closed_at = ? WHERE id = ?",
                        (args.status, args.until if parked else None, args.reason if parked else None, at, closed, t["id"]))
            data = {"from": t["status"], "to": args.status}
            if parked:
                data.update(until=args.until, reason=args.reason)
            tail = parked_tail(args.until, args.reason) if parked else ((": " + args.reason) if args.reason else "")
            ledgerdb.write_event(con, at, actor.label, "ticket.status", "%s %s -> %s%s" % (t["key"], t["status"], args.status, tail),
                        ticket_id=t["id"], data=data)
            entry = reportentry.write_report_entry(con, at, "%s %s: %s" % (t["key"], TICKET_MOVE_VERBS[args.status], t["title"]),
                                       "ticket move", t["id"], lines=["parked" + parked_tail(args.until, args.reason)] if parked else (),
                                       next_line=args.next)
            t = lookup.get_ticket_by_id(con, t["id"])
        d = lookup.ticket_dict(con, t)
    finally:
        con.close()
    text = "%s is now %s" % (d["key"], d["status"]) + (parked_tail(d["parked_until"], d["parked_reason"]) if parked else "")
    return reportentry.with_report_entry({"ticket": d}, text, entry)


def cmd_ticket_edit(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "editing a ticket")
        reportentry.check_next(con, actor, args)
        for field in ("brief", "sizing", "outcome"):
            ops.check_prose_headings(getattr(args, field), kernel.TICKET_SECTIONS, "--" + field)
        at = kernel.now()
        entry = None
        with ledgerdb.write_txn(con):
            t = lookup.get_ticket(con, args.key)
            updates = {}
            for field in ("title", "heading", "brief", "sizing", "outcome"):
                value = getattr(args, field)
                if value is not None and value != (t[field] or ""):
                    updates[field] = value
            if args.tag is not None:
                tags = ["ticket"] + [x for x in args.tag if x != "ticket"]
                if json.dumps(tags) != t["tags"]:
                    updates["tags"] = json.dumps(tags)
            old_priority = t["priority"]
            priority_change = args.priority is not None and args.priority != old_priority
            if args.next is not None and not priority_change:
                raise reportentry.no_entry_for_next("ticket edit writes one only when --priority changes the priority, and %s stays %s" % (t["key"], old_priority))
            if priority_change:
                con.execute("UPDATE tickets SET priority = ?, updated_at = ? WHERE id = ?", (args.priority, at, t["id"]))
                ledgerdb.write_event(con, at, actor.label, "ticket.priority", "%s %s -> %s" % (t["key"], t["priority"], args.priority),
                            ticket_id=t["id"], data={"from": t["priority"], "to": args.priority})
            if updates:
                con.execute("UPDATE tickets SET %s, updated_at = ? WHERE id = ?" % ", ".join("%s = ?" % k for k in updates), (*updates.values(), at, t["id"]))
                ledgerdb.write_event(con, at, actor.label, "ticket.edited", "%s edited: %s" % (t["key"], ", ".join(sorted(updates))),
                            ticket_id=t["id"], data={"fields": sorted(updates)})
            t = lookup.get_ticket_by_id(con, t["id"])
            if priority_change:  # the entry names the ticket as the edit leaves it
                entry = reportentry.write_report_entry(con, at, "%s priority %s to %s: %s" % (t["key"], old_priority, t["priority"], t["title"]),
                                           "ticket edit", t["id"], next_line=args.next)
        d = lookup.ticket_dict(con, t)
        changed = sorted(updates) + (["priority"] if priority_change else [])
    finally:
        con.close()
    return reportentry.with_report_entry({"ticket": d, "changed": changed}, "%s edited: %s" % (d["key"], ", ".join(changed) or "nothing to change"), entry)


def format_ticket(d):
    lines = ["%s (%s) %s %s: %s" % (d["key"], d["team_key"], d["status"], d["priority"], d["title"])]
    lines.append("origin: %s%s   lead: %s   created: %s" % (d["origin"], (" by " + d["proposed_by"]) if d["proposed_by"] else "", d["lead"] or "-", d["created_at"]))
    if d["status"] == "parked":
        lines.append("parked" + parked_tail(d["parked_until"], d["parked_reason"]))
    for name, key in (("Brief", "brief"), ("Size, persona and model decision", "sizing"), ("Outcome", "outcome")):
        if d[key]:
            lines.append("")
            lines.append("## " + name)
            lines.append(d[key])
    return "\n".join(lines)


def cmd_ticket_show(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        d = lookup.ticket_dict(con, lookup.get_ticket(con, args.key))
    finally:
        con.close()
    return kernel.Result({"ticket": d}, format_ticket(d))
