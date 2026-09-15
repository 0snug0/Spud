"""imports/accept: import --file: accepting a hand edit of a rendered file.  Moved from bin/spud_ledger.py (SPD-065)."""

import json
import re
from pathlib import Path

from . import noteimport
from ..core import kernel, markdown
from ..render import notefiles
from ..state import ledgerdb, lookup, ops


def classify_path(ctx, path):
    """Which generated file a path is: ('ticket', KEY) | ('member', TEAM, NAME) | ('report', DAY), and its home-relative path."""
    path = Path(path).resolve()
    parts = path.parts
    try:
        rel = path.relative_to(ctx.home).as_posix()
    except ValueError:
        rel = None
    if len(parts) >= 2 and parts[-2] == "ledger" and path.name == "Projects.md":
        return ("projects",), rel or notefiles.PROJECTS_NOTE
    if len(parts) >= 3 and parts[-3] == "ledger" and parts[-2] == "tickets" and path.suffix == ".md":
        return ("ticket", path.stem), rel or "ledger/tickets/%s" % path.name
    if len(parts) >= 4 and parts[-4] == "ledger" and parts[-3] == "teams" and path.suffix == ".md":
        return ("member", parts[-2], path.stem), rel or "ledger/teams/%s/%s" % (parts[-2], path.name)
    if len(parts) >= 2 and parts[-2] == "reports" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem):
        return ("report", path.stem), rel or "reports/%s" % path.name
    raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not a generated ledger file (ledger/tickets/*.md, ledger/teams/*/*.md, reports/*.md)" % path)


def heading_tail(heading, title):
    """'SPD-003 — Protocol fixes' -> the tail; None when it equals the title."""
    if not heading:
        return None
    parts = heading.split(" — ", 1)
    tail = parts[1] if len(parts) == 2 else heading
    return None if tail == title else tail


def edited_keys(base, current):
    return [k for k in list(current.keys()) + [k for k in base if k not in current] if base.get(k) != current.get(k)]


def refuse(rel, message):
    raise kernel.SpudError(kernel.EXIT_ERROR, "%s: %s" % (rel, message))


def check_section_layout(rel, base, doc):
    base_names = [n for n, _ in base["sections"]]
    names = [n for n, _ in doc["sections"]]
    if names != base_names:
        missing = [n for n in base_names if n not in names]
        added = [n for n in names if n not in base_names]
        what = "; ".join(
            p for p in (
                ("deleted: " + ", ".join(missing)) if missing else "",
                ("added: " + ", ".join(added)) if added else "",
                "reordered" if not missing and not added else "",
            ) if p
        )
        refuse(rel, "the note's sections are fixed (empty a section rather than deleting it; %s)" % what)


TICKET_SECTION_COMMANDS = {
    "Team": "spud member new|start|finish",
    "Handoffs": "spud handoff add",
    "Proposals received": "spud proposal decide",
}
MEMBER_OWN_SECTIONS = {"Log": "spud member log", "Result": "spud member result", "Blocked": "spud member block"}
MEMBER_SECTION_COMMANDS = {"Sub-agents": "spud member new", "Ticket proposals": "spud proposal file"}


def accept_ticket_edit(ctx, con, at, t, base, doc, rel):
    """Accepted by hand: title, priority, tags, status (through the state machine),
    Brief, Size, Outcome.  Everything else is refused with the way to change it."""
    fm, base_fm = doc["frontmatter"], base["frontmatter"]
    keys = edited_keys(base_fm, fm)
    for key in keys:
        if key not in kernel.TICKET_FM_KEYS:
            refuse(rel, "property %r has no column; the ledger keeps only %s" % (key, ", ".join(kernel.TICKET_FM_KEYS)))
        if key in ("id", "origin", "proposed_by", "project"):
            refuse(rel, "%s is not editable by hand" % key)
        if key == "lead":
            refuse(rel, "lead is not editable by hand; the first member planned with `spud member new` is the lead")
        if key == "created":
            refuse(rel, "created is not editable by hand; timestamps come from the clock")
    if list(fm.keys()) != list(base_fm.keys()):
        refuse(rel, "the order of the properties is generated; put them back as rendered")
    title_changed = "title" in keys
    if doc["heading"] != base["heading"] and not title_changed:
        refuse(rel, "the heading is generated from the title; use `spud ticket edit --heading`")
    check_section_layout(rel, base, doc)
    updates = {}
    if title_changed:
        updates["title"] = fm["title"]
        if doc["heading"] != base["heading"]:
            updates["heading"] = heading_tail(doc["heading"], fm["title"])
    if "priority" in keys:
        if fm.get("priority") not in kernel.PRIORITIES:
            refuse(rel, "priority %r is not one of %s" % (fm.get("priority"), ", ".join(kernel.PRIORITIES)))
        updates["priority"] = fm["priority"]
    if "tags" in keys:
        tags = fm["tags"] if isinstance(fm["tags"], list) else [fm["tags"]]
        updates["tags"] = json.dumps(tags)
    status_change = None
    if "status" in keys:
        if fm.get("status") not in kernel.TICKET_STATUSES:
            refuse(rel, "status %r is not one of %s" % (fm.get("status"), ", ".join(kernel.TICKET_STATUSES)))
        ops.check_transition("tickets", t["status"], fm["status"], t["key"])
        status_change = (t["status"], fm["status"])
    base_sections = dict(base["sections"])
    for name, body in doc["sections"]:
        if base_sections[name] == body:
            continue
        if name in kernel.TICKET_COLUMN_SECTIONS:
            ops.check_prose_headings(body, kernel.TICKET_SECTIONS, "%s: ## %s" % (rel, name), before=base_sections[name])
            updates[kernel.TICKET_COLUMN_SECTIONS[name]] = body
        elif name in TICKET_SECTION_COMMANDS:
            refuse(rel, "## %s is generated from the ledger; it changes through `%s`" % (name, TICKET_SECTION_COMMANDS[name]))
        else:
            refuse(rel, "## %s is kept verbatim from markdown-v0; not editable by hand" % name)
    changed = list(updates)
    if status_change:
        updates["status"] = status_change[1]
        updates["closed_at"] = at if status_change[1] in ("done", "declined") else None
        changed.append("status")
    if updates:
        updates["updated_at"] = at
        con.execute("UPDATE tickets SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in updates), (*updates.values(), t["id"]))
    if "priority" in updates:
        ledgerdb.write_event(con, at, "eric", "ticket.priority", "%s %s -> %s (hand edit)" % (t["key"], t["priority"], updates["priority"]),
                    ticket_id=t["id"], data={"from": t["priority"], "to": updates["priority"]})
    if status_change:
        ledgerdb.write_event(con, at, "eric", "ticket.status", "%s %s -> %s (hand edit)" % (t["key"], status_change[0], status_change[1]),
                    ticket_id=t["id"], data={"from": status_change[0], "to": status_change[1]})
    ledgerdb.write_event(con, at, "eric", "import", "accepted hand edit of %s" % rel, ticket_id=t["id"], data={"source": rel, "accepted": True, "changed": changed})
    return changed


def accept_member_edit(ctx, con, at, m, ticket, base, doc, rel):
    """Accepted by hand: status (through the state machine), Brief, Outcome (parent-owned;
    Spud is every member's ancestor).  The member's own sections and everything
    the CLI derives are refused with the command that changes them."""
    fm, base_fm = doc["frontmatter"], base["frontmatter"]
    keys = edited_keys(base_fm, fm)
    for key in keys:
        if key == "model":
            refuse(rel, "model is not editable by hand; use `spud member edit --model` with `--tier-reason`")
        if key in ("spawned", "finished"):
            refuse(rel, "%s is not editable by hand; timestamps come from the clock" % key)
        if key in ("id", "name", "parent", "ticket", "project", "persona", "agent_type", "tags"):
            refuse(rel, "%s is not editable by hand" % key)
        if key in ("duration_ms", "tool_uses"):
            refuse(rel, "%s is not editable by hand; the hooks record it, and `spud member resum` recomputes it" % key)
        if key in ("tokens_out", "tokens_in", "tokens_cached"):
            refuse(rel, "%s is not editable by hand; it is derived from the transcript sum the hooks record, and "
                        "`spud member resum` recomputes it" % key)
        if key == "cost_usd":
            refuse(rel, "cost_usd is not editable by hand; it is computed when the note renders, from the transcript sum's "
                        "per-model breakdown and the price table in spud.config.json")
        if key != "status":
            refuse(rel, "property %r has no column" % key)
    if list(fm.keys()) != list(base_fm.keys()):
        refuse(rel, "the order of the properties is generated; put them back as rendered")
    if doc["heading"] != base["heading"]:
        refuse(rel, "the heading is generated")
    check_section_layout(rel, base, doc)
    updates = {}
    base_sections = dict(base["sections"])
    for name, body in doc["sections"]:
        if base_sections[name] == body:
            continue
        if name in ("Brief", "Outcome"):
            ops.check_prose_headings(body, markdown.IMPORT_MEMBER_SECTIONS, "%s: ## %s" % (rel, name), before=base_sections[name])
            updates[kernel.MEMBER_COLUMN_SECTIONS[name]] = body
        elif name in MEMBER_OWN_SECTIONS:
            refuse(rel, "## %s is the member's own; the member writes it through the CLI (`%s`)" % (name, MEMBER_OWN_SECTIONS[name]))
        elif name in MEMBER_SECTION_COMMANDS:
            refuse(rel, "## %s is generated from the ledger; it changes through `%s`" % (name, MEMBER_SECTION_COMMANDS[name]))
        else:
            refuse(rel, "## %s is kept verbatim from markdown-v0; not editable by hand" % name)
    changed = list(updates)
    if "status" in keys:
        if fm.get("status") not in kernel.MEMBER_STATUSES:
            refuse(rel, "status %r is not one of %s" % (fm.get("status"), ", ".join(kernel.MEMBER_STATUSES)))
        ops.member_status_change(con, at, "eric", m, fm["status"], updates)
        changed.append("status")
    elif updates:
        con.execute("UPDATE members SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in updates), (*updates.values(), m["id"]))
    ledgerdb.write_event(con, at, "eric", "import", "accepted hand edit of %s" % rel, ticket_id=ticket["id"], member_id=m["id"], data={"source": rel, "accepted": True, "changed": changed})
    return changed


def accept_report_edit(con, at, day, text, rel):
    """An appended entry is accepted; a changed or deleted entry is refused: report
    entries are append-only events."""
    entries = markdown.parse_report_entries(text)
    existing = con.execute("SELECT at, body, data FROM events WHERE kind = 'report.entry' AND at LIKE ? ORDER BY id", (day + "%",)).fetchall()
    have = {}
    for e in existing:
        title = (json.loads(e["data"]) if e["data"] else {}).get("title", "")
        have.setdefault((e["at"][11:16], title), []).append(markdown.normalize_markdown(e["body"]))
    seen = {}
    for time, title, body in entries:
        seen[(time, title)] = seen.get((time, title), 0) + 1
    for key, bodies in have.items():
        if seen.get(key, 0) < len(bodies):
            refuse(rel, "the entry '%s — %s' was deleted; report entries are append-only events (`spud render --discard` restores the file)" % key)
    added = 0
    for time, title, body in entries:
        bodies = have.get((time, title))
        if bodies is None:
            ledgerdb.write_event(con, "%sT%s" % (day, time), "eric", "report.entry", body, data={"title": title, "source": rel})
            added += 1
        elif markdown.normalize_markdown(body) not in bodies:
            refuse(rel, "the entry '%s — %s' changed; report entries are append-only events" % (time, title))
    changed = ["entries:%d" % added]
    ledgerdb.write_event(con, at, "eric", "import", "accepted hand edit of %s" % rel, data={"source": rel, "accepted": True, "changed": changed})
    return changed


def accept_file(ctx, con, actor, path):
    """import --file: accept a hand edit of a rendered file.  The edit is the
    difference between the file and what the renderer last wrote (renders.content;
    the current render when the file was never rendered); fields the file leaves
    as rendered do not overwrite changes the CLI made since."""
    path = Path(path).resolve()
    if not path.is_file():
        raise kernel.SpudError(kernel.EXIT_ERROR, "no such file: %s" % path)
    kind, rel = classify_path(ctx, path)
    if kind[0] == "projects":
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is generated from the projects table and never accepted by hand; it changes with `spud project add|edit|remove`"
                        " (`spud render --discard %s` restores it)" % (rel, rel))
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    at = kernel.now()
    with ledgerdb.write_txn(con):
        record = con.execute("SELECT content FROM renders WHERE path = ?", (rel,)).fetchone()
        baseline = record["content"] if record and record["content"] else None
        if kind[0] == "ticket":
            t = lookup.get_ticket(con, kind[1])
            owned = (json.loads(t["layout"]) if t["layout"] else {}).get("sections") or kernel.TICKET_SECTIONS  # SPD-076
            base = markdown.split_document(baseline or notefiles.render_ticket(con, t, ctx.pricing), owned)
            doc = markdown.split_document(text, owned)
            if doc["frontmatter"].get("id") != t["key"]:
                raise kernel.SpudError(kernel.EXIT_ERROR, "%s: id is not editable by hand" % rel)
            changed = accept_ticket_edit(ctx, con, at, t, base, doc, rel)
        elif kind[0] == "member":
            m = lookup.get_member(con, "%s/%s" % (kind[1], kind[2]))
            ticket = lookup.get_ticket_by_id(con, m["ticket_id"])
            # every member section, Blocked included, since the last render may predate a block or its clearing (SPD-076)
            owned = (json.loads(m["layout"]) if m["layout"] else {}).get("sections") or kernel.MEMBER_SECTIONS
            base = markdown.split_document(baseline or notefiles.render_member(con, m, ctx.pricing), owned)
            doc = markdown.split_document(text, owned)
            fm = doc["frontmatter"]
            expected_parent = "Spud" if m["parent_id"] is None else lookup.member_ref(con, m["parent_id"])
            parent_link = noteimport.parse_link(fm["parent"]) if fm.get("parent") else None
            if fm.get("id") != m["lineage"] or fm.get("name") != m["name"] or parent_link != expected_parent or noteimport.parse_link(fm.get("ticket") or "") != ticket["key"]:
                raise kernel.SpudError(kernel.EXIT_ERROR, "%s: id, name, parent and ticket are not editable by hand" % rel)
            changed = accept_member_edit(ctx, con, at, m, ticket, base, doc, rel)
        else:
            changed = accept_report_edit(con, at, kind[1], text, rel)
        con.execute(
            "INSERT INTO renders (path, sha256, content, rendered_at, through_event_id) VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(id), 0) FROM events))"
            " ON CONFLICT(path) DO UPDATE SET sha256 = excluded.sha256, content = excluded.content, rendered_at = excluded.rendered_at, through_event_id = excluded.through_event_id",
            (rel, kernel.sha256_bytes(raw), text, at),
        )
    return {"path": rel, "changed": changed}
