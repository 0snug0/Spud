"""imports/bulkimport: The markdown-v0 tree import."""

import re
from pathlib import Path

from . import noteimport
from ..core import kernel, markdown
from ..render import notefiles, sectiontext
from ..state import ledgerdb, lookup


def handoff_rows(con, ticket, text):
    """A ## Handoffs body -> [{at, from_id, to_id, what, whole}], read as sectiontext.render_handoff_rows writes it.

    A dated handoff line naming members of this ticket (or Spud) is a row; its `at` is the line's date.  A line
    indented two spaces under a handoff line continues that row, as a Log entry's lines and a proposal's Why continue
    theirs (SPD-078): the indent comes off, so a `what` holding newlines reads back whole.  A handoff line naming a
    party that is no member here is kept whole (`whole`, no ids), its continuation lines verbatim with it, so it
    renders as it was.  An empty line between a handoff line and its next continuation line is a blank line of the
    `what` (an editor may have trimmed the render's `  `); any other blank line, and a comment, only separates rows.
    Any other line is a row of its own, kept whole, with no date (`at` None).  The rule is core/markdown.continued_rows,
    which the Log reads by too (SPD-236)."""
    rows = []
    for line, more in markdown.continued_rows(text, markdown.parse_handoff_line):
        if line.strip().startswith("<!--"):
            continue
        parsed = markdown.parse_handoff_line(line)
        if not parsed:
            rows.append({"at": None, "from_id": None, "to_id": None, "what": line, "whole": True})
            continue
        date, frm, to, what = parsed
        from_id = handoff_party_id(con, ticket, frm)
        to_id = handoff_party_id(con, ticket, to)
        if from_id == "?" or to_id == "?":
            rows.append({"at": date, "from_id": None, "to_id": None, "what": "\n".join([line] + more), "whole": True})
        else:
            rows.append({"at": date, "from_id": from_id, "to_id": to_id, "what": markdown.joined_row(what, more), "whole": False})
    return rows


def import_handoffs(con, at, ticket, text):
    """Returns the columns whose value had to be derived (an unparsed line has no date of its own)."""
    derived = {}
    for row in handoff_rows(con, ticket, text):
        if row["at"] is None:
            con.execute("INSERT INTO handoffs (ticket_id, at, what) VALUES (?, ?, ?)", (ticket["id"], ticket["created_at"], row["what"]))
            derived["handoffs.at"] = "created"
        elif row["whole"]:
            con.execute("INSERT INTO handoffs (ticket_id, at, what) VALUES (?, ?, ?)", (ticket["id"], row["at"], row["what"]))
        else:
            con.execute(
                "INSERT INTO handoffs (ticket_id, at, from_member_id, to_member_id, what) VALUES (?, ?, ?, ?, ?)",
                (ticket["id"], row["at"], row["from_id"], row["to_id"], row["what"]),
            )
    return derived


TEAM_LINE = re.compile(r"^\s*- \[\[(?P<ref>[^\]|\\]+)(?:\\?\|[^\]]*)?\]\] \([^()]*(?:\([^()]*\)[^()]*)*\) — (?P<text>.+)$")


def import_team_summaries(con, ticket, text):
    """A tree line's ` — text` in a ticket's ## Team becomes that member's summary, when the member is
    on this ticket and has none (the section is generated, so its prose is never stored).
    Table rows, the embed, lines without the suffix and other teams' members are ignored.  Returns
    the columns whose value was derived."""
    derived = {}
    for line in text.split("\n"):
        m = TEAM_LINE.match(line)
        if not m or not m.group("text").strip():
            continue
        team_key, _, name = m.group("ref").partition("/")
        if team_key != ticket["team_key"]:
            continue
        cur = con.execute("UPDATE members SET summary = ? WHERE ticket_id = ? AND name = ? AND summary IS NULL",
                          (m.group("text").strip(), ticket["id"], name))
        if cur.rowcount:
            derived["members.summary"] = "Team section"
    return derived


def handoff_party_id(con, ticket, label):
    """'Kestrel (01)' -> member id; 'Spud' -> None; anything else -> '?'."""
    if label.strip() == "Spud":
        return None
    m = re.fullmatch(r"(\S+) \((\d+(?:\.\d+)*)\)", label.strip())
    if not m:
        return "?"
    row = con.execute("SELECT id FROM members WHERE ticket_id = ? AND name = ? AND lineage = ?", (ticket["id"], m.group(1), m.group(2))).fetchone()
    return row["id"] if row else "?"


def link_proposal(con, at, t, fm):
    """origin: proposal + proposed_by -> a proposals row on the ticket it arose on, decided
    `create` by Spud.  The file carries one date, so filed_at and the decision's at take it."""
    link = noteimport.parse_link(fm.get("proposed_by", "")) if fm.get("proposed_by") else None
    if t["origin"] != "proposal":
        return {}
    if not link:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s has origin proposal but no proposed_by link" % t["key"])
    origin_member = lookup.get_member(con, link)
    cur = con.execute(
        "INSERT INTO proposals (ticket_id, origin_member_id, holder_member_id, title, filed_at, status, created_ticket_id)"
        " VALUES (?, ?, NULL, ?, ?, 'created', ?)",
        (origin_member["ticket_id"], origin_member["id"], t["title"], t["created_at"], t["id"]),
    )
    proposal_id = cur.lastrowid
    con.execute(
        "INSERT INTO proposal_decisions (proposal_id, at, by_member_id, decision, reason, created_ticket_id)"
        " VALUES (?, ?, NULL, 'create', '', ?)",
        (proposal_id, t["created_at"], t["id"]),
    )
    con.execute("UPDATE tickets SET proposal_id = ? WHERE id = ?", (proposal_id, t["id"]))
    return {"proposal.filed_at": "created", "proposal_decisions.at": "created"}


def bulk_import(ctx, con, paths):
    """Import markdown-v0 trees: each path is a root with ledger/ and reports/, a
    ledger/ dir (tickets/ and teams/), or a reports/ dir."""
    ledger_dirs = []
    report_dirs = []
    for p in paths:
        p = Path(p).resolve()
        if not p.is_dir():
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not a directory" % p)
        if (p / "tickets").is_dir() or (p / "teams").is_dir():
            ledger_dirs.append((p, p.parent))
        elif p.name == "reports":
            report_dirs.append((p, p.parent))
        elif (p / "ledger").is_dir() or (p / "reports").is_dir():
            if (p / "ledger").is_dir():
                ledger_dirs.append((p / "ledger", p))
            if (p / "reports").is_dir():
                report_dirs.append((p / "reports", p))
        else:
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s holds no ledger/ or reports/ tree" % p)
    counts = {"tickets": 0, "members": 0, "reports": 0, "report_entries": 0, "prose_sections": 0, "projects": 0}
    at = kernel.now()
    with ledgerdb.write_txn(con):
        imported_tickets = []
        imported_members = []
        for ledger, base in ledger_dirs:  # the projects first, since a ticket finds its project by its prefix
            if (ledger / "Projects.md").is_file():
                path = ledger / "Projects.md"
                counts["projects"] += notefiles.import_projects_file(con, at, path, path.relative_to(base).as_posix())
        for ledger, base in ledger_dirs:
            for path in sorted((ledger / "tickets").glob("*.md")) if (ledger / "tickets").is_dir() else []:
                rel = path.relative_to(base).as_posix()
                imported_tickets.append(noteimport.import_ticket_file(ctx, con, at, path, rel))
                counts["tickets"] += 1
        for ledger, base in ledger_dirs:
            files = []
            if (ledger / "teams").is_dir():
                for folder in sorted(p for p in (ledger / "teams").iterdir() if p.is_dir() and not p.name.startswith("_")):
                    for path in folder.glob("*.md"):
                        doc_fm = markdown.split_document(path.read_text(encoding="utf-8"))["frontmatter"]
                        files.append((folder.name, str(doc_fm.get("id", "")).count("."), path))
            for team_key, _, path in sorted(files, key=lambda f: (f[0], f[1], f[2].name)):
                rel = path.relative_to(base).as_posix()
                imported_members.append(noteimport.import_member_file(ctx, con, at, path, rel, team_key))
                counts["members"] += 1
        # second pass: leads, proposals, handoffs, then the prose the rows cannot regenerate
        for item in imported_tickets:
            t, fm = item["ticket"], item["fm"]
            lead = noteimport.parse_link(fm.get("lead", "")) if fm.get("lead") else None
            if lead:
                lead_row = lookup.get_member(con, lead)
                if lead_row["ticket_id"] != t["id"]:
                    raise kernel.SpudError(kernel.EXIT_ERROR, "%s: lead %s is on another ticket" % (item["rel"], lead))
                con.execute("UPDATE tickets SET lead_id = ? WHERE id = ?", (lead_row["id"], t["id"]))
        for item in imported_tickets:
            item["derived"].update(link_proposal(con, at, item["ticket"], item["fm"]))
        for item in imported_tickets:
            sections = dict(item["sections"])
            if "Handoffs" in sections:
                item["derived"].update(import_handoffs(con, at, item["ticket"], sections["Handoffs"]))
            if "Team" in sections:
                item["derived"].update(import_team_summaries(con, item["ticket"], sections["Team"]))
        for item in imported_tickets:
            data = {"source": item["rel"], "derived": item["derived"]}
            if item["dropped"]:  # what the note said about its landing pull request and the import read past
                data["dropped"] = item["dropped"]
            ledgerdb.write_event(con, at, "import", "import", "imported %s" % item["rel"], ticket_id=item["ticket"]["id"], data=data)
        for item in imported_tickets:
            t = lookup.get_ticket_by_id(con, item["ticket"]["id"])
            for name, text in item["sections"]:
                # ## Team is generated from members (its tree lines' suffixes gave the summaries above) and ## Landing
                # from pull_requests, which no import rebuilds: neither one's prose is ever stored
                if name in kernel.TICKET_COLUMN_SECTIONS or name in kernel.TICKET_GENERATED_SECTIONS:
                    continue
                rendered = sectiontext.ticket_section_text(con, t, name)
                if noteimport.store_prose_if_needed(con, "ticket", t["id"], name, text, rendered):
                    counts["prose_sections"] += 1
        for item in imported_members:
            m = lookup.get_member_by_id(con, item["member"]["id"])
            ticket = item["ticket"]
            for name, text in item["sections"]:
                if name in kernel.MEMBER_COLUMN_SECTIONS:
                    continue
                rendered = sectiontext.member_section_text(con, m, ticket, name)
                if noteimport.store_prose_if_needed(con, "member", m["id"], name, text, rendered):
                    counts["prose_sections"] += 1
        for reports, base in report_dirs:
            for path in sorted(reports.glob("*.md")):
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem):
                    continue
                rel = path.relative_to(base).as_posix()
                day = path.stem
                if con.execute("SELECT 1 FROM events WHERE kind = 'report.entry' AND at LIKE ?", (day + "%",)).fetchone():
                    raise kernel.SpudError(kernel.EXIT_ERROR, "%s already has entries in the ledger (imported before)" % rel)
                entries = markdown.parse_report_entries(path.read_text(encoding="utf-8"))
                for time, title, body in entries:
                    ledgerdb.write_event(con, "%sT%s" % (day, time), "import", "report.entry", body, data={"title": title, "source": rel})
                    counts["report_entries"] += 1
                ledgerdb.write_event(con, at, "import", "import", "imported %s" % rel, data={"source": rel, "entries": len(entries)})
                counts["reports"] += 1
    return counts
