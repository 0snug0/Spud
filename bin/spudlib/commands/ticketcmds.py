"""commands/ticketcmds: ticket new, move, edit, show.  Moved from bin/spud_ledger.py (SPD-065)."""

import json
import os

from . import reportentry
from ..core import kernel
from ..hooks import worktrees
from ..state import actors, ledgerdb, lookup, ops


# Report entries (SPD-011).  Spud's recording commands write their own report.entry in the transaction of
# the record they make: ticket new, ticket move, ticket edit when --priority changes the priority, member
# finish of a root member, and proposal decide.  The title is generated and the body is `- ` lines; Spud
# types only the Next line, with --next.  `report add` stays for what no command records (a merge, an install).
TICKET_MOVE_VERBS = {"active": "started", "done": "done", "queued": "queued", "declined": "declined"}


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
        else:  # SPD-014: the project of the working directory, else the home
            try:
                mapped = worktrees.cli_project_of(ctx, con, os.getcwd())
            except OSError:
                mapped = None
            project = mapped[0] if mapped else con.execute("SELECT * FROM projects WHERE id = 1").fetchone()
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
                        data={"origin": "eric", "priority": t["priority"], "status": t["status"]})
            entry = reportentry.write_report_entry(con, at, "%s created (%s, %s): %s" % (t["key"], t["status"], t["priority"], t["title"]),
                                       "ticket new", t["id"], next_line=args.next)
        d = lookup.ticket_dict(con, t)
    finally:
        con.close()
    keys = "%s, %s" % (d["team_key"], d["project"]) if d["project"] != "spud" else d["team_key"]
    return reportentry.with_report_entry({"ticket": d}, "%s (%s) created: %s [%s]" % (d["key"], keys, d["title"], d["status"]), entry)


def cmd_ticket_move(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "moving a ticket")
        reportentry.check_next(con, actor, args)
        at = kernel.now()
        with ledgerdb.write_txn(con):
            t = lookup.get_ticket(con, args.key)
            ops.check_transition("tickets", t["status"], args.status, t["key"])
            closed = at if args.status in ("done", "declined") else None
            con.execute("UPDATE tickets SET status = ?, updated_at = ?, closed_at = ? WHERE id = ?", (args.status, at, closed, t["id"]))
            ledgerdb.write_event(con, at, actor.label, "ticket.status", "%s %s -> %s%s" % (t["key"], t["status"], args.status, (": " + args.reason) if args.reason else ""),
                        ticket_id=t["id"], data={"from": t["status"], "to": args.status})
            entry = reportentry.write_report_entry(con, at, "%s %s: %s" % (t["key"], TICKET_MOVE_VERBS[args.status], t["title"]),
                                       "ticket move", t["id"], next_line=args.next)
            t = lookup.get_ticket_by_id(con, t["id"])
        d = lookup.ticket_dict(con, t)
    finally:
        con.close()
    return reportentry.with_report_entry({"ticket": d}, "%s is now %s" % (d["key"], d["status"]), entry)


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
