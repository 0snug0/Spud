"""commands/proposalcmds: proposal file, decide, list; handoff add; report add."""

from . import reportentry
from ..core import kernel
from ..render import sectiontext
from ..state import actors, ledgerdb, lookup, ops


def cmd_proposal_file(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_member(con, actor, "filing a proposal")
        ops.check_prose_headings(ops.proposal_brief(args.why or "", args.evidence or ""), kernel.TICKET_SECTIONS,
                             "--why and --evidence, the brief of a ticket created from the proposal,")
        at = kernel.now()
        with ledgerdb.write_txn(con):
            m = lookup.get_member_by_id(con, actor.member["id"])
            cur = con.execute(
                "INSERT INTO proposals (ticket_id, origin_member_id, holder_member_id, title, why, evidence, suggested_priority, filed_at, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')",
                (m["ticket_id"], m["id"], m["parent_id"], args.title, args.why or "", args.evidence or "", args.priority, at),
            )
            proposal_id = cur.lastrowid
            ledgerdb.write_event(con, at, actor.label, "proposal.filed", args.title, ticket_id=m["ticket_id"], member_id=m["id"],
                        data={"proposal_id": proposal_id, "suggested_priority": args.priority})
        d = lookup.proposal_dict(con, con.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone())
    finally:
        con.close()
    return kernel.Result({"proposal": d}, "proposal %d filed: %s (held by %s)" % (d["id"], d["title"], d["holder"] or "Spud"))


def cmd_proposal_decide(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        reportentry.check_next(con, actor, args)
        at = kernel.now()
        created = None
        entry = None
        with ledgerdb.write_txn(con):
            p = con.execute("SELECT * FROM proposals WHERE id = ?", (args.id,)).fetchone()
            if p is None:
                raise kernel.SpudError(kernel.EXIT_ERROR, "no proposal %d" % args.id)
            if p["status"] != "open":
                raise kernel.SpudError(kernel.EXIT_ERROR, "proposal %d is already %s" % (p["id"], p["status"]))
            # A recorded holder that has returned cannot decide anything, so the proposal falls to the nearest
            # ancestor that can and to Spud at the root.  The climb is resolved here, before the ownership check, and
            # written below with the decision, so nothing is ever held by a member that no longer exists.
            recorded = p["holder_member_id"]
            holder = lookup.effective_holder(con, recorded)
            held_by = lookup.holder_words(lookup.member_ref(con, recorded), lookup.member_ref(con, holder))
            if actor.kind == "spud":
                if holder is not None:
                    raise kernel.SpudError(kernel.EXIT_OWNERSHIP, "proposal %d is held by %s, not by Spud yet" % (p["id"], held_by))
                if args.decision == "escalate":
                    raise kernel.SpudError(kernel.EXIT_ERROR, "Spud has nobody to escalate to; create or decline")
                if args.decision == "absorb":
                    raise kernel.SpudError(kernel.EXIT_ERROR, "Spud does not absorb work (Law 1); create a ticket or decline")
            else:
                if holder != actor.member["id"]:
                    raise kernel.SpudError(kernel.EXIT_OWNERSHIP, "proposal %d is held by %s, not by %s" % (p["id"], held_by, actor.ref(con)))
                if args.decision == "create":
                    raise kernel.SpudError(kernel.EXIT_OWNERSHIP, "only Spud creates tickets (Law 6); escalate instead")
            updates = {}
            climb = {}
            if holder != recorded:  # the climb, written where it was read; escalate's own move overwrites it below
                was = lookup.get_member_by_id(con, recorded)
                updates["holder_member_id"] = holder
                climb = {"inherited_from": lookup.member_ref(con, recorded), "inherited_from_status": was["status"] if was else None}
            if args.decision == "escalate":
                updates["holder_member_id"] = actor.member["parent_id"]
            elif args.decision == "absorb":
                updates["status"] = "absorbed"
            elif args.decision == "decline":
                updates["status"] = "declined"
            else:
                priority = args.priority or p["suggested_priority"] or "P2"
                created = ops.create_ticket_for_proposal(con, at, actor, p, args.title, priority)
                updates["status"] = "created"
                updates["created_ticket_id"] = created["id"]
            con.execute("UPDATE proposals SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in updates), (*updates.values(), p["id"]))
            con.execute(
                "INSERT INTO proposal_decisions (proposal_id, at, by_member_id, decision, reason, created_ticket_id) VALUES (?, ?, ?, ?, ?, ?)",
                (p["id"], at, None if actor.kind == "spud" else actor.member["id"], args.decision, args.reason or "", created["id"] if created else None),
            )
            ledgerdb.write_event(con, at, actor.label, "proposal.decided", "%s: %s%s" % (args.decision, p["title"], (" (" + args.reason + ")") if args.reason else ""),
                        ticket_id=p["ticket_id"], member_id=p["origin_member_id"],
                        data={"proposal_id": p["id"], "decision": args.decision, "created_ticket": created["key"] if created else None, **climb})
            if actor.kind == "spud":  # Spud creates or declines, and either decision is one report entry
                proposer = lookup.member_ref(con, p["origin_member_id"])
                if created is not None:
                    entry = reportentry.write_report_entry(con, at, "%s created from a proposal by %s (%s, %s): %s" % (created["key"], proposer, created["status"], created["priority"], created["title"]),
                                               "proposal decide", created["id"], next_line=args.next)
                else:
                    reason = args.reason if args.reason and args.reason.strip() else None
                    entry = reportentry.write_report_entry(con, at, "Proposal declined: %s (from %s)" % (p["title"], proposer),
                                               "proposal decide", p["ticket_id"], lines=["Reason: " + reason] if reason else [], next_line=args.next)
            p = con.execute("SELECT * FROM proposals WHERE id = ?", (p["id"],)).fetchone()
        d = lookup.proposal_dict(con, p)
        data = {"proposal": d}
        if created:
            data["ticket"] = lookup.ticket_dict(con, lookup.get_ticket_by_id(con, created["id"]))
    finally:
        con.close()
    text = "proposal %d %s" % (d["id"], {"escalate": "escalated to %s" % (d["holder"] or "Spud"), "absorb": "absorbed", "decline": "declined", "create": "created as %s" % d["created_ticket"]}[args.decision])
    return reportentry.with_report_entry(data, text, entry)


def cmd_proposal_list(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        sql = "SELECT p.* FROM proposals p"
        params = []
        clauses = []
        if args.ticket:
            clauses.append("p.ticket_id = ?")
            params.append(lookup.get_ticket(con, args.ticket)["id"])
        if args.open:
            clauses.append("p.status = 'open'")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = [lookup.proposal_dict(con, p) for p in con.execute(sql + " ORDER BY p.id", params).fetchall()]
    finally:
        con.close()
    # The holder column says who must decide it now, naming the recorded holder when the climb passed one that has
    # returned -- `Spud (was SPUD-nnn/<Name>)`.  The rows keep both, so --json still reads the recorded holder.
    shown = [dict(r, holder=lookup.holder_words(r["holder"], r["effective_holder"])) for r in rows]
    return kernel.Result({"proposals": rows}, kernel.table(shown, [("id", "id"), ("ticket", "ticket"), ("status", "status"), ("origin", "origin"), ("holder", "holder"), ("P", "suggested_priority"), ("title", "title")]))


def cmd_handoff_add(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        at = kernel.now()
        with ledgerdb.write_txn(con):
            t = lookup.get_ticket(con, args.ticket)
            if actor.kind == "member" and actor.member["ticket_id"] != t["id"]:
                raise kernel.SpudError(kernel.EXIT_OWNERSHIP, "%s is not on %s's team; a member records handoffs only on its own ticket" % (actor.ref(con), t["key"]))
            parties = []
            for label in (args.frm, args.to):
                if label == "spud":
                    parties.append(None)
                else:
                    m = lookup.get_member(con, label)
                    if m["ticket_id"] != t["id"]:
                        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not on %s's team" % (label, t["key"]))
                    parties.append(m["id"])
            if parties == [None, None]:
                raise kernel.SpudError(kernel.EXIT_ERROR, "a handoff needs a member on at least one side")
            if actor.kind == "member" and actor.member["id"] not in parties:
                raise kernel.SpudError(kernel.EXIT_OWNERSHIP, "a handoff is recorded by Spud or by one of its parties; %s is neither" % actor.ref(con))
            cur = con.execute(
                "INSERT INTO handoffs (ticket_id, at, from_member_id, to_member_id, what, path) VALUES (?, ?, ?, ?, ?, ?)",
                (t["id"], at, parties[0], parties[1], args.what, args.path),
            )
            ledgerdb.write_event(con, at, actor.label, "handoff", "%s → %s: %s" % (sectiontext.handoff_party(con, parties[0]), sectiontext.handoff_party(con, parties[1]), args.what),
                        ticket_id=t["id"], member_id=parties[0], data={"handoff_id": cur.lastrowid, "to": parties[1], "path": args.path})
    finally:
        con.close()
    return kernel.Result({"handoff": {"id": cur.lastrowid, "ticket": args.ticket, "from": args.frm, "to": args.to, "what": args.what, "path": args.path}},
                  "handoff recorded on %s" % args.ticket)


def cmd_report_add(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "a report entry")
        at = kernel.now()
        body = "- Next: " + args.next
        with ledgerdb.write_txn(con):
            event_id = ledgerdb.write_event(con, at, actor.label, "report.entry", body, data={"title": args.title})
    finally:
        con.close()
    entry = {"id": event_id, "at": at, "title": args.title, "body": body}
    return kernel.Result({"entry": entry}, reportentry.report_entry_line(entry))
