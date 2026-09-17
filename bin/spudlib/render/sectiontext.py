"""render/sectiontext: Section bodies rendered from rows.  Moved from bin/spud_ledger.py (SPD-065)."""

from . import teamcard, workedon
from ..core import kernel
from ..state import lookup


def render_team_section(con, t, pricing=None):
    """## Team: the table ending in its Total row (SPD-013), the tree with each member's worked-on suffix, the embed;
    empty without members.  Costs come from the price table given, at render time."""
    rows = teamcard.team_order(con.execute("SELECT * FROM members WHERE ticket_id = ? ORDER BY lineage", (t["id"],)).fetchall(), t["lead_id"])
    if not rows:
        return ""
    lines = list(teamcard.TEAM_TABLE_HEAD) + [teamcard.team_table_row(t["team_key"], t["created_at"], m, pricing) for m in rows]
    lines += [teamcard.team_total_row(teamcard.team_totals(rows, pricing)), ""]
    for m in rows:
        text = workedon.worked_on(m)
        lines.append("  " * (m["depth"] - 1) + teamcard.team_line(t["team_key"], m) + (" — " + text if text else ""))
    lines += ["", teamcard.TEAM_VIEW_EMBED]
    return "\n".join(lines)


def render_landing_section(con, t):
    """## Landing (SPD-116): every pull request recorded against the ticket, in record order, each as one sentence --
    the number as a link on its URL, and the state in the words the board uses -- with what a merge leaves owed under
    it, from `lookup.pr_owed`, so the wording of an owed landing has one definition.  Empty without a pull request, so
    a ticket that never had one renders as it did before this section existed.

    Stored columns only.  No `gh` call: the render reads `pull_requests`, and reading GitHub is `pr reconcile`'s alone.
    And nothing here says when the row was last read, or that the last read failed -- `spud board` and `spud doctor`
    carry that, live, where it belongs.  A relative stamp would make every render a change (the watcher renders within
    seconds of every write, and "4 min ago" is never identical twice); an absolute read stamp would rewrite the note
    on every reconcile pass that found nothing new.  What renders here moves only when the landing itself does."""
    parts = []
    for p in lookup.pull_requests(con, [t["id"]]):
        d = lookup.pr_dict(con, p)
        parts.append("Pull request [%s](%s) — %s." % (lookup.pr_name(d), d["url"], lookup.pr_state_text(d)))
        owed = lookup.pr_owed(d)
        if owed:
            parts.append("Owed: %s." % "; ".join(owed))
    return "\n\n".join(parts)


def handoff_party(con, member_id):
    if member_id is None:
        return "Spud"
    m = lookup.get_member_by_id(con, member_id)
    return "%s (%s)" % (m["name"], m["lineage"])


def render_handoff_rows(con, ticket, after=0):
    rows = con.execute("SELECT * FROM handoffs WHERE ticket_id = ? AND id > ? ORDER BY id", (ticket["id"], after)).fetchall()
    lines = []
    for h in rows:
        if h["from_member_id"] is None and h["to_member_id"] is None:
            lines.append(h["what"])  # an imported line that did not parse; kept whole (`handoff add` refuses Spud -> Spud)
            continue
        what = h["what"]
        if h["path"]:
            what = "%s (`%s`)" % (what, h["path"])
        lines.append("- %s — %s → %s: %s" % (kernel.fm_date(h["at"]), handoff_party(con, h["from_member_id"]), handoff_party(con, h["to_member_id"]), what))
    return "\n".join(lines)


def decision_trail(con, proposal_id):
    rows = con.execute("SELECT * FROM proposal_decisions WHERE proposal_id = ? ORDER BY id", (proposal_id,)).fetchall()
    parts = []
    for d in rows:
        who = "Spud" if d["by_member_id"] is None else lookup.get_member_by_id(con, d["by_member_id"])["name"]
        if d["decision"] == "create":
            created = lookup.get_ticket_by_id(con, d["created_ticket_id"])
            parts.append("created as [[%s]] at %s by %s" % (created["key"], created["priority"], who))
        elif d["decision"] == "escalate":
            parts.append("escalated by %s" % who + (" (%s)" % d["reason"] if d["reason"] else ""))
        else:
            parts.append("%s by %s" % ("absorbed" if d["decision"] == "absorb" else "declined", who) + (": %s" % d["reason"] if d["reason"] else ""))
    return parts


def render_received_rows(con, ticket, after=0):
    """Spud's decisions on proposals that arose on this ticket."""
    rows = con.execute(
        "SELECT d.*, p.title, p.origin_member_id FROM proposal_decisions d JOIN proposals p ON p.id = d.proposal_id"
        " WHERE p.ticket_id = ? AND d.by_member_id IS NULL AND d.id > ? ORDER BY d.id",
        (ticket["id"], after),
    ).fetchall()
    lines = []
    for d in rows:
        origin = lookup.member_ref(con, d["origin_member_id"])
        escalations = [t for t in decision_trail(con, d["proposal_id"]) if t.startswith("escalated by")]
        via = (", " + "; ".join(escalations)) if escalations else ""
        if d["decision"] == "create":
            created = lookup.get_ticket_by_id(con, d["created_ticket_id"])
            verdict = "created as [[%s]] at %s" % (created["key"], created["priority"])
        else:
            verdict = "declined" + (": %s" % d["reason"] if d["reason"] else "")
        lines.append("- **%s** — origin [[%s]]%s — decision: %s." % (d["title"], origin, via, verdict))
    return "\n".join(lines)


def render_log_rows(con, member, after=0):
    rows = con.execute(
        "SELECT * FROM events WHERE member_id = ? AND kind = 'member.log' AND id > ? ORDER BY id",
        (member["id"], after),
    ).fetchall()
    lines = []
    for e in rows:
        body = e["body"].split("\n")
        lines.append("- %s %s" % (kernel.fm_minute(e["at"]), body[0]))
        lines.extend("  " + more for more in body[1:])
    return "\n".join(lines)


def render_subagent_rows(con, member, team_key, after=0):
    rows = con.execute("SELECT * FROM members WHERE parent_id = ? AND id > ? ORDER BY lineage", (member["id"], after)).fetchall()
    return "\n".join(teamcard.team_line(team_key, m) for m in rows)


def render_proposal_rows(con, member, after=0):
    rows = con.execute("SELECT * FROM proposals WHERE origin_member_id = ? AND id > ? ORDER BY id", (member["id"], after)).fetchall()
    lines = []
    for p in rows:
        trail = decision_trail(con, p["id"])
        if p["status"] == "open":
            holder = "Spud" if p["holder_member_id"] is None else lookup.get_member_by_id(con, p["holder_member_id"])["name"]
            trail.append("open, with %s" % holder)
        head = "- **%s** — suggested %s; %s." % (p["title"], p["suggested_priority"] or "no priority", "; ".join(trail))
        lines.append(head)
        if p["why"]:
            lines.append("  Why: %s" % p["why"].replace("\n", "\n  "))
        if p["evidence"]:
            lines.append("  Evidence: %s" % p["evidence"].replace("\n", "\n  "))
    return "\n".join(lines)


def ticket_section_text(con, t, name, pricing=None):
    if name in kernel.TICKET_COLUMN_SECTIONS:
        return t[kernel.TICKET_COLUMN_SECTIONS[name]] or ""
    if name == "Team":
        return render_team_section(con, t, pricing)  # from members alone: stored Team prose is not rendered
    if name == kernel.LANDING_SECTION:
        return render_landing_section(con, t)  # from pull_requests alone, the same way (kernel.TICKET_GENERATED_SECTIONS)
    prose, after = teamcard.prose_for(con, "ticket", t["id"], name)
    if name == "Handoffs":
        return teamcard.join_prose(prose, render_handoff_rows(con, t, after))
    if name == "Proposals received":
        return teamcard.join_prose(prose, render_received_rows(con, t, after))
    return prose or ""


def member_section_text(con, m, ticket, name):
    if name in kernel.MEMBER_COLUMN_SECTIONS:
        return m[kernel.MEMBER_COLUMN_SECTIONS[name]] or ""
    prose, after = teamcard.prose_for(con, "member", m["id"], name)
    if name == "Log":
        return teamcard.join_prose(prose, render_log_rows(con, m, after))
    if name == "Sub-agents":
        return teamcard.join_prose(prose, render_subagent_rows(con, m, ticket["team_key"], after))
    if name == "Ticket proposals":
        return teamcard.join_prose(prose, render_proposal_rows(con, m, after))
    return prose or ""


def body_with_sections(heading, sections):
    out = [kernel.MARKER, "# " + heading, ""]
    for name, text in sections:
        out.append("## " + name)
        if text:
            out.append(text)
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"
