"""commands/views: events, board, fleet, card, member list."""

from . import prcmds, worktreebind
from ..core import kernel, launchagents
from ..hooks import gitrepos
from ..projects import sessions
from ..render import prices, teamcard
from ..state import ledgerdb, lookup


def cmd_events(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        clauses, params = [], []
        if args.ticket:
            clauses.append("ticket_id = ?")
            params.append(lookup.get_ticket(con, args.ticket)["id"])
        if args.member:
            clauses.append("member_id = ?")
            params.append(lookup.get_member(con, args.member)["id"])
        if args.kind:
            clauses.append("kind = ?")
            params.append(args.kind)
        if args.project:  # its tickets' events, and the project and session events that name it
            lookup.get_project(con, args.project)
            clauses.append("(ticket_id IN (SELECT t.id FROM tickets t JOIN projects p ON p.id = t.project_id WHERE p.key = ?)"
                           " OR json_extract(data, '$.project') = ?)")
            params += [args.project, args.project]
        sql = "SELECT * FROM events" + ((" WHERE " + " AND ".join(clauses)) if clauses else "") + " ORDER BY id"
        rows = con.execute(sql, params).fetchall()
        if args.limit:
            rows = rows[-args.limit :]
        events = [lookup.event_dict(con, e) for e in rows]
    finally:
        con.close()
    lines = ["%d  %s  %s  %s  %s" % (e["id"], e["at"], e["actor"], e["kind"], e["body"].split("\n")[0]) for e in events]
    return kernel.Result({"events": events}, "\n".join(lines) or "(no events)")


def cmd_board(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM v_board").fetchall()]
        if args.project:
            lookup.get_project(con, args.project)
            rows = [r for r in rows if r["project"] == args.project]
        if args.parked:  # the bucket on its own, with the two columns the default table does not carry
            rows = [r for r in rows if r["status"] == "parked"]
        # The full board is where the one `gh pr view` per unsettled pull request happens -- never in `--brief`,
        # which SessionStart injects, and never on any hook path.  A stored check younger than STALE is left alone, the
        # whole run is capped, `--no-reconcile` skips it, and SPUD_GH=off turns it off (the suite's default).
        prs, pr_block = [], []
        if not args.brief:
            open_ids = prcmds.open_ticket_ids(con, rows)
            if args.reconcile:
                prcmds.reconcile(ctx, con, open_ids, stale=prcmds.STALE)
            prs, pr_block = prcmds.board_block(con, open_ids)
        for r in rows:  # the bound worktree and its branch, read from git now, never stored
            r["worktree"] = worktreebind.worktree_state(r["worktree"])
        if args.brief:
            text = sessions.board_brief_text(con, rows, parked=args.parked)
            # The render watcher's lines -- down, the vault behind, or both.  The SessionStart context carries the
            # same ones, so a session reads the state of the vault it is about to trust.
            # And one line naming each checkout that holds what a member may have planted for git to run, which the
            # harness's own git and Eric's terminal would run with no hook to see it.
            # And one naming each model a run used that the price table does not price, and where its price goes.
            for line in launchagents.watcher_lines(ctx, con) + gitrepos.planted_lines(ctx, con) + prices.unpriced_lines(ctx, con):
                text += "\n" + line
        else:
            for r in rows:
                r["created"] = kernel.fm_date(r["created_at"])
            if args.parked:  # every row is parked, so the status column says nothing and until and reason say it all
                columns = [("ticket", "key"), ("P", "priority"), ("title", "title"), ("until", "parked_until"), ("reason", "parked_reason"), ("lead", "lead"), ("created", "created")]
            else:
                columns = [("ticket", "key"), ("status", "status"), ("P", "priority"), ("title", "title"), ("lead", "lead"), ("origin", "origin"), ("proposed by", "proposed_by"), ("created", "created")]
            if con.execute("SELECT count(*) FROM projects").fetchone()[0] > 1:  # the project column once there is more than one
                columns.insert(1, ("project", "project"))
            text = kernel.table(rows, columns)
            bound = [r for r in rows if r["worktree"] is not None and r["status"] not in ("done", "declined")]
            if bound:  # an open ticket's worktree, in the board's order; a closed one keeps its path as history only
                text += "\n\nworktrees:\n" + "\n".join("  %s  %s" % (r["key"], worktreebind.worktree_line(r["worktree"])) for r in bound)
            if pr_block:  # an open ticket's recorded pull requests, merged ones with what the landing still owes
                text += "\n" + "\n".join(pr_block)
    finally:
        con.close()
    data = {"tickets": rows}
    if not args.brief:
        data["pull_requests"] = prs
    return kernel.Result(data, text)


def cmd_fleet(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM v_fleet").fetchall()]
    finally:
        con.close()
    for r in rows:
        r["spawned"] = kernel.fm_minute(r["spawned_at"])
        r["finished"] = kernel.fm_minute(r["finished_at"])
    return kernel.Result({"members": rows}, kernel.table(rows, [("ticket", "ticket"), ("id", "id"), ("name", "name"), ("persona", "persona"), ("model", "model"), ("effort", "effort"), ("status", "status"), ("parent", "parent"), ("spawned", "spawned"), ("finished", "finished")]))


def team_tree(con, ticket, pricing=None):
    rows = con.execute("SELECT * FROM members WHERE ticket_id = ? ORDER BY lineage", (ticket["id"],)).fetchall()
    nodes = {}
    roots = []
    for m in rows:
        node = lookup.member_dict(con, m)
        cost, reasons = prices.run_cost(m["usage_json"], pricing)  # the run's tokens and its cost at the API list price
        node.update(tokens=prices.token_counts(m["usage_json"]), cost_usd=prices.usd_text(cost) if cost is not None else None, not_priced=reasons)
        node["children"] = []
        nodes[m["id"]] = node
        if m["parent_id"] in nodes:
            nodes[m["parent_id"]]["children"].append(node)
        else:
            roots.append(node)
    return roots


def flatten_team(nodes):
    """team_tree()'s roots flattened in the depth-first order `card` prints (format_tree's order:
    each root, then its children recursively), each member dict without its "children" key."""
    flat = []
    for n in nodes:
        flat.append({k: v for k, v in n.items() if k != "children"})
        flat.extend(flatten_team(n["children"]))
    return flat


def format_tree(nodes, depth=0):
    lines = []
    for n in nodes:
        run = "spawned %s, finished %s" % (kernel.fm_minute(n["spawned_at"]) or "-", kernel.fm_minute(n["finished_at"]) or "-")
        chain = ", ".join(w + " " + ref.split("/", 1)[-1] for w, ref in (("re-spawns", n.get("respawns")), ("re-spawned as", n.get("respawned_as"))) if ref)
        line = "%s- %s (%s, %s, %s%s) %s; %s" % ("  " * depth, n["name"], n["lineage"], n["persona"] if n["persona"] != "contractor" else "contractor on %s" % n["agent_type"],
                                           n["model"], "; " + chain if chain else "", n["status"], run)  # SPD-321: the re-spawn chain, as on the Team card
        if n.get("tokens"):
            line += "; %s · %s" % (teamcard.tokens_text(n["tokens"]), "$" + n["cost_usd"] if n.get("cost_usd") else "— (%s)" % "; ".join(n.get("not_priced") or ["not priced"]))
        lines.append(line)
        if n["summary"]:
            lines.append("%s  %s" % ("  " * depth, n["summary"]))
        lines.extend(format_tree(n["children"], depth + 1))
    return lines


def card_total_line(totals, not_priced, pricing):
    """The card's last line:the ticket's tokens and its cost at the API list price, with the table's date,
    partial when a member's transcript sum has no cost, naming each such member and why."""
    if totals["tokens"] is None:
        return "total: no tokens recorded"
    tokens = teamcard.tokens_text(totals["tokens"])
    if pricing is None:
        return "total: %s · — (%s)" % (tokens, prices.NO_TABLE)
    cost = "—" if totals["cost"] is None else prices.money(totals["cost"]) + (" (partial)" if not_priced else "")
    line = "total: %s · %s at API list price (USD, prices as of %s)" % (tokens, cost, pricing["as_of"])
    if not_priced:
        line += "; not priced: " + ", ".join("%s (%s)" % (p["ref"], "; ".join(p["reasons"])) for p in not_priced)
    return line


def cmd_card(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        t = lookup.get_ticket(con, args.key)
        d = lookup.ticket_dict(con, t)
        if args.project and d["project"] != args.project:
            lookup.get_project(con, args.project)
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s is project %s's, not %s's" % (d["key"], d["project"], args.project))
        pricing = ctx.pricing
        tree = team_tree(con, t, pricing)
        totals = teamcard.team_totals(con.execute("SELECT * FROM members WHERE ticket_id = ? ORDER BY lineage", (t["id"],)).fetchall(), pricing)
        not_priced = [{"ref": lookup.member_ref(con, m["id"]), "reasons": reasons} for m, reasons in totals["not_priced"]]
        prs = [lookup.pr_dict(con, p) for p in lookup.pull_requests(con, [t["id"]])]  # stored state; `card` reads no gh
    finally:
        con.close()
    total = {"tokens": totals["tokens"], "cost_usd": prices.usd_text(totals["cost"]) if totals["cost"] is not None else None,
             "partial": totals["cost"] is not None and bool(not_priced), "not_priced": not_priced, "tool_uses": totals["tools"],
             "pricing": {k: pricing[k] for k in ("as_of", "source", "currency")} if pricing else None}
    worktree = worktreebind.worktree_state(d["worktree"])  # read from git now, never stored
    lines = ["%s — %s  [%s, %s]  lead: %s" % (d["key"], d["title"], d["status"], d["priority"], d["lead"] or "-")]
    if worktree is not None:
        lines.append("worktree: " + worktreebind.worktree_line(worktree))
    for p in prs:  # every recorded pull request, and what a merge nobody has acted on still owes
        lines.append("pull request %s %s  %s" % (lookup.pr_name(p), prcmds.pr_read_text(p), p["url"]))
        owed = lookup.pr_owed(p)
        if owed:
            lines.append("  owed: " + "; ".join(owed))
    lines.extend(format_tree(tree) or ["(no team yet)"])
    if tree:
        lines.append(card_total_line(totals, not_priced, pricing))
    return kernel.Result({"ticket": d, "worktree": worktree, "pull_requests": prs, "team": tree, "total": total}, "\n".join(lines))


def cmd_member_list(ctx, args):
    """member list [--ticket]: what `card`'s team shows for one ticket, in its order, or what
    `fleet` shows for every member, in its order; read-only, for any actor or none."""
    con = ledgerdb.connect(ctx)
    try:
        if args.ticket:
            t = lookup.get_ticket(con, args.ticket)
            members = flatten_team(team_tree(con, t, ctx.pricing))  # match card's team, cost included
        else:
            rows = con.execute("SELECT m.* FROM members m JOIN tickets t ON t.id = m.ticket_id ORDER BY t.id DESC, m.lineage").fetchall()
            members = [lookup.member_dict(con, m) for m in rows]
    finally:
        con.close()
    lines = ["%s (%s, %s, %s) %s" % (d["ref"], d["lineage"], d["persona"], d["model"], d["status"]) for d in members]
    return kernel.Result({"members": members}, "\n".join(lines) or "(no members)")
