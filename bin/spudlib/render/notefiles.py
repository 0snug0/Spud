"""render/notefiles: Whole notes: ticket, member, Projects.md, report; the render targets."""

import json
import re

from . import sectiontext, teamcard
from ..core import kernel, markdown
from ..state import ledgerdb, lookup


def place_names(names, group, after, present):
    """`group`'s names in order, each kept where the note already has it and otherwise inserted right after the first
    name of `after` the note carries (at the end when it carries none) -- or all of them gone when `present` is False.
    The shape a conditional part of a ticket note has: the parked pair of keys, and the pull-request pair
    and its ## Landing section, each rendered only while the ledger has something to put in it."""
    if not present:
        return [n for n in names if n not in group]
    at = next((names.index(n) + 1 for n in after if n in names), len(names))
    for name in group:
        if name not in names:
            names.insert(at, name)
        at = names.index(name) + 1
    return names


def render_ticket(con, t, pricing=None):
    layout = json.loads(t["layout"]) if t["layout"] else {}
    keys = list(layout.get("fm_keys") or kernel.TICKET_FM_KEYS)
    if "project" not in keys:  # an imported ticket's stored order, from before projects: the key goes right after origin
        keys.insert(keys.index("origin") + 1 if "origin" in keys else len(keys), "project")
    # The two keys that qualify the status, right after it and only while parked.  Then the two that name
    # the landing pull request, after those, and only while one is recorded.
    landing = lookup.ticket_landing(con, t["id"])
    keys = place_names(keys, kernel.PARKED_FM_KEYS, ("status",), t["status"] == "parked")
    keys = place_names(keys, kernel.PR_FM_KEYS, ("parked_reason", "parked_until", "status"), landing is not None)
    d = lookup.ticket_dict(con, t)
    values = {
        "id": ("plain", t["key"]),
        "title": ("quoted", t["title"]),
        "priority": ("plain", t["priority"]),
        "status": ("plain", t["status"]),
        "parked_until": ("stamp", t["parked_until"] or ""),
        "parked_reason": ("quoted", t["parked_reason"] or ""),
        # plain when known and "" when not, as parked_until and a member's spawned are: the number is the pull request
        # a Bases view filters and links on, and a URL that carried none leaves the key empty rather than holding a URL
        "pr": ("stamp", (landing["number"] or "") if landing else ""),
        "pr_state": ("plain", landing["state"] if landing else ""),
        "origin": ("plain", t["origin"]),
        "project": ("plain", d["project"]),
        "proposed_by": ("quoted", "[[%s]]" % d["proposed_by"] if d["proposed_by"] else ""),
        "lead": ("quoted", "[[%s]]" % d["lead"] if d["lead"] else ""),
        "created": ("plain", kernel.fm_date(t["created_at"])),
        "tags": ("list", d["tags"]),
    }
    pairs = [(k, values[k]) for k in keys if k in values]
    heading = "%s — %s" % (t["key"], t["heading"] or t["title"])
    # ## Landing goes where the template has it, after the sections that precede it there, so a note whose
    # stored layout predates the section -- every imported one -- gains it in the right place the moment one is recorded
    before = kernel.TICKET_SECTIONS[: kernel.TICKET_SECTIONS.index(kernel.LANDING_SECTION)]
    names = place_names(list(layout.get("sections") or kernel.TICKET_SECTIONS), (kernel.LANDING_SECTION,),
                        tuple(reversed(before)), landing is not None)
    sections = [(name, sectiontext.ticket_section_text(con, t, name, pricing)) for name in names]
    return markdown.emit_frontmatter(pairs) + sectiontext.body_with_sections(heading, sections)


def render_member(con, m, pricing=None):
    ticket = lookup.get_ticket_by_id(con, m["ticket_id"])
    layout = json.loads(m["layout"]) if m["layout"] else {}
    parent = "[[Spud]]" if m["parent_id"] is None else "[[%s]]" % lookup.member_ref(con, m["parent_id"])
    contractor = m["persona"] == "contractor" or m["agent_type"] != "spudagent"
    pairs = [("id", ("quoted", m["lineage"])), ("name", ("plain", m["name"])), ("persona", ("plain", m["persona"]))]
    if contractor:
        pairs.append(("agent_type", ("plain", m["agent_type"])))
    pairs += [
        ("model", ("plain", m["model"])),
        ("parent", ("quoted", parent)),
        ("ticket", ("quoted", "[[%s]]" % ticket["key"])),
        ("project", ("plain", lookup.project_key_of(con, ticket))),
        ("status", ("plain", m["status"])),
        ("spawned", ("stamp", kernel.fm_minute(m["spawned_at"]))),
        ("finished", ("stamp", kernel.fm_minute(m["finished_at"]))),
    ]
    pairs += teamcard.usage_pairs(m, pricing)
    pairs.append(("tags", ("list", ["spudagent", "contractor"] if m["persona"] == "contractor" else ["spudagent"])))
    heading = "%s (%s, %s) — %s" % (m["name"], m["lineage"], m["persona"], ticket["key"])
    names = layout.get("sections")
    if not names:
        names = [n for n in kernel.MEMBER_SECTIONS if n != "Blocked" or m["blocked"] is not None]
    sections = [(name, sectiontext.member_section_text(con, m, ticket, name)) for name in names]
    return markdown.emit_frontmatter(pairs) + sectiontext.body_with_sections(heading, sections)


# ledger/Projects.md: the projects table, generated, and the disaster-recovery import source for it.
PROJECTS_NOTE = "ledger/Projects.md"
PROJECTS_COLUMNS = ("Key", "Name", "Ticket prefix", "Team prefix", "Root", "Default branch", "Landing", "Sessions", "Remote", "Archived",
                    "Scripts", "Runners")
# The headers before migration 0008_project_runners added the runner allow-list (SPD-168) and before 0007_project_scripts
# added the script allow-list (SPD-145), which an export from then still carries and imports with nothing allowed.
PROJECTS_COLUMNS_V7 = PROJECTS_COLUMNS[:-1]
PROJECTS_COLUMNS_V6 = PROJECTS_COLUMNS[:-2]


def table_cell(value):
    return ("" if value is None else str(value)).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def table_cells(line):
    """A markdown table row's cells, `\\|` and `\\\\` read back; None for a line that is no row."""
    s = line.strip()
    if not s.startswith("|") or not s.endswith("|") or len(s) < 2:
        return None
    cells, cur, i = [], [], 1
    while i < len(s) - 1:
        c = s[i]
        if c == "\\" and i + 1 < len(s) - 1 and s[i + 1] in "|\\":
            cur.append(s[i + 1])
            i += 2
            continue
        if c == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def render_projects(con):
    rows = con.execute("SELECT * FROM projects ORDER BY id").fetchall()
    lines = [kernel.MARKER, "# Projects", "", "| %s |" % " | ".join(PROJECTS_COLUMNS), "|%s" % ("---|" * len(PROJECTS_COLUMNS))]
    for p in rows:
        cells = (p["key"], p["name"], p["ticket_prefix"], p["team_prefix"], p["root_path"], p["default_branch"], p["landing"], p["sessions"],
                 p["remote"], kernel.fm_date(p["archived_at"]), ", ".join(json.loads(p["scripts"] or "[]")),
                 ", ".join(json.loads(p["runners"] or "[]")))
        lines.append("| %s |" % " | ".join(table_cell(c) for c in cells))
    return markdown.emit_frontmatter([("tags", ("list", ["projects"]))]) + "\n".join(lines) + "\n"


def import_projects_file(con, at, path, rel):
    """Projects.md into the projects table, before any ticket (a ticket finds its project by its prefix): every row but the
    home's, which config sync keeps, and any key already in the ledger.  Returns how many rows it inserted."""
    table_rows = [cells for cells in (table_cells(line) for line in path.read_text(encoding="utf-8").split("\n")) if cells is not None]
    columns = tuple(table_rows[0]) if table_rows else ()
    if columns not in (PROJECTS_COLUMNS, PROJECTS_COLUMNS_V7, PROJECTS_COLUMNS_V6):
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: the table's header is not %s" % (rel, " | ".join(PROJECTS_COLUMNS)))
    inserted = 0
    for cells in table_rows[1:]:
        if all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue
        if len(cells) != len(columns):
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s: a row has %d cells, not %d" % (rel, len(cells), len(columns)))
        row = dict(zip(columns, cells))
        scripts = sorted({s.strip() for s in row.get("Scripts", "").split(",") if s.strip()})
        runners = sorted({s.strip() for s in row.get("Runners", "").split(",") if s.strip()})
        if row["Key"] == "spud" or con.execute("SELECT 1 FROM projects WHERE key = ?", (row["Key"],)).fetchone():
            continue
        if row["Landing"] not in ("merge", "pr") or row["Sessions"] not in ("always", "claim"):
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s: project %s has landing %r and sessions %r, outside the schema's values" % (rel, row["Key"], row["Landing"], row["Sessions"]))
        con.execute(
            "INSERT INTO projects (key, name, root_path, remote, ticket_prefix, team_prefix, created_at, default_branch, landing, sessions, archived_at,"
            " scripts, runners) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["Key"], row["Name"] or row["Key"], row["Root"], row["Remote"] or None, row["Ticket prefix"], row["Team prefix"], at,
             row["Default branch"] or "main", row["Landing"], row["Sessions"], row["Archived"] or None, json.dumps(scripts),
             json.dumps(runners)),
        )
        inserted += 1
    ledgerdb.write_event(con, at, "import", "import", "imported %s" % rel, data={"source": rel, "projects": inserted})
    return inserted


def render_report(day, entries):
    out = [kernel.MARKER, "# " + day, ""]
    for e in entries:
        data = json.loads(e["data"]) if e["data"] else {}
        out.append("## %s — %s" % (e["at"][11:16], data.get("title", "")))
        if e["body"]:
            out.append(e["body"])
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def render_targets(con, pricing=None):
    """[(relative path, content)] for every generated file; costs at the given price table's list prices."""
    targets = []
    for t in con.execute("SELECT * FROM tickets ORDER BY id").fetchall():
        targets.append(("ledger/tickets/%s.md" % t["key"], render_ticket(con, t, pricing)))
    for m in con.execute("SELECT m.* FROM members m JOIN tickets t ON t.id = m.ticket_id ORDER BY t.id, m.lineage").fetchall():
        ticket = lookup.get_ticket_by_id(con, m["ticket_id"])
        targets.append(("ledger/teams/%s/%s.md" % (ticket["team_key"], m["name"]), render_member(con, m, pricing)))
    targets.append((PROJECTS_NOTE, render_projects(con)))
    days = {}
    for e in con.execute("SELECT * FROM events WHERE kind = 'report.entry' ORDER BY id").fetchall():
        days.setdefault(e["at"][:10], []).append(e)
    for day in sorted(days):
        targets.append(("reports/%s.md" % day, render_report(day, days[day])))
    return targets
