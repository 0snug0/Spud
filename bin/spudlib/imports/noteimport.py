"""imports/noteimport: markdown-v0 ticket and member notes into rows.  Moved from bin/spud_ledger.py (SPD-065)."""

import json
import re

from ..core import kernel, lazy, markdown
from ..render import prices
from ..state import ledgerdb, lookup, ops


# ----------------------------------------------------------------------------
# Import
# ----------------------------------------------------------------------------


def parse_link(value):
    """'[[SPUD-001/Kestrel]]' -> 'SPUD-001/Kestrel'; '' -> None."""
    if not value:
        return None
    m = re.fullmatch(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", value.strip())
    if not m:
        raise kernel.SpudError(kernel.EXIT_ERROR, "not a wikilink: %r" % value)
    return m.group(1)


def store_prose_if_needed(con, entity, entity_id, section, text, rendered_from_rows):
    """Keep the file's prose only where the rows cannot regenerate it."""
    if markdown.normalize_markdown(text) == markdown.normalize_markdown(rendered_from_rows):
        con.execute("DELETE FROM imported_sections WHERE entity = ? AND entity_id = ? AND section = ?", (entity, entity_id, section))
        return False
    table = kernel.SECTION_TABLES.get(section)
    through = con.execute("SELECT COALESCE(MAX(id), 0) FROM %s" % table).fetchone()[0] if table else 0
    con.execute(
        "INSERT INTO imported_sections (entity, entity_id, section, body, through_id) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(entity, entity_id, section) DO UPDATE SET body = excluded.body, through_id = excluded.through_id",
        (entity, entity_id, section, text, through),
    )
    return True


def ticket_layout(fm_keys, section_names, parked=False):
    """The file's own key and section order, stored only where it differs from the template's.  The two parked
    properties are in the template's order only while the ticket is parked (SPD-096), as `## Blocked` is on a member
    note: a note that is not parked has neither, and still needs no layout of its own."""
    default = list(kernel.TICKET_FM_KEYS) if parked else [k for k in kernel.TICKET_FM_KEYS if k not in kernel.PARKED_FM_KEYS]
    layout = {}
    if fm_keys != default:
        layout["fm_keys"] = fm_keys
    if section_names != kernel.TICKET_SECTIONS:
        layout["sections"] = section_names
    return layout or None


def member_layout(section_names, blocked):
    default = [n for n in kernel.MEMBER_SECTIONS if n != "Blocked" or blocked is not None]
    return {"sections": section_names} if section_names != default else None


def require_keys(fm, keys, path):
    for key in keys:
        if key not in fm:
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s: frontmatter lacks %s" % (path, key))


def import_ticket_file(ctx, con, at, path, rel):
    doc = markdown.split_document(path.read_text(encoding="utf-8"), kernel.TICKET_SECTIONS)
    fm = doc["frontmatter"]
    require_keys(fm, ["id", "title", "priority", "status", "origin", "created", "tags"], rel)
    key = fm["id"]
    m = re.fullmatch(r"([A-Z][A-Z0-9]*)-(\d+)", key)
    if not m:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: id %r is not <PREFIX>-<number>" % (rel, key))
    project = con.execute("SELECT * FROM projects WHERE ticket_prefix = ?", (m.group(1),)).fetchone()
    if project is None:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: no project has ticket prefix %s" % (rel, m.group(1)))
    if "project" in fm and fm["project"] != project["key"]:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: project %r, but the prefix %s is project %s's" % (rel, fm["project"], m.group(1), project["key"]))
    if con.execute("SELECT 1 FROM tickets WHERE key = ?", (key,)).fetchone():
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is already in the ledger (imported before); `spud import --file` accepts edits to a rendered file" % key)
    if fm["priority"] not in kernel.PRIORITIES or fm["status"] not in kernel.TICKET_STATUSES or fm["origin"] not in ("eric", "proposal"):
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: priority, status or origin outside the schema's values" % rel)
    heading = None
    if doc["heading"]:
        tail = doc["heading"].split(" — ", 1)
        tail = tail[1] if len(tail) == 2 else doc["heading"]
        if tail != fm["title"]:
            heading = tail
    if doc["preamble"]:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: text between the heading and the first section cannot be imported" % rel)
    names = [n for n, _ in doc["sections"]]
    if len(set(names)) != len(names):
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: a section repeats" % rel)
    sections = dict(doc["sections"])
    tags = fm["tags"] if isinstance(fm["tags"], list) else [fm["tags"]]
    t = ops.insert_ticket(
        con, at, "import", project, fm["title"], fm["priority"], fm["status"], origin=fm["origin"],
        brief=sections.get("Brief", ""), sizing=sections.get("Size, persona and model decision", ""),
        outcome=sections.get("Outcome", ""), tags=tags, heading=heading, created_at=fm["created"],
        number=int(m.group(2)), layout=ticket_layout(list(fm.keys()), names, fm["status"] == "parked"),
        # SPD-096: an export from before the parked status has neither key, so require_keys does not grow; the CHECKs
        # of migration 0003_parked refuse one that says parked with no reason, which is the right refusal
        parked_until=fm.get("parked_until") or None, parked_reason=fm.get("parked_reason") or None,
    )
    # the file carries one date: updated_at takes it (closed_at stays NULL; the file has no close time)
    return {"ticket": t, "fm": fm, "sections": doc["sections"], "rel": rel, "derived": {"updated_at": "created"}}


MEMBER_USAGE_KEYS = ("duration_ms", "tool_uses", "tokens_out", "tokens_in", "tokens_cached")
SQLITE_INTEGER_MAX = 2**63 - 1


def usage_columns(fm, rel):
    """The usage keys a rendered member note carries when known (SPD-010), read back: duration_ms and
    tool_uses into their columns; the three token keys, when all are present, as total_tokens and a
    transcript-shaped usage_json that renders the same keys and Tokens cell again (in stands for
    input plus cache creation, so cache creation is 0).  A present key that is not a non-negative
    integer is refused, naming the file and the key."""
    values = {}
    for key in MEMBER_USAGE_KEYS:
        if key not in fm:
            continue
        value = fm[key]
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,19}", value) or int(value) > SQLITE_INTEGER_MAX:
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s: %s must be a non-negative integer, not %r" % (rel, key, value))
        values[key] = int(value)
    columns = {key: values[key] for key in ("duration_ms", "tool_uses") if key in values}
    if all(key in values for key in ("tokens_out", "tokens_in", "tokens_cached")):
        total = values["tokens_out"] + values["tokens_in"] + values["tokens_cached"]
        if total > SQLITE_INTEGER_MAX:
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s: tokens_out, tokens_in and tokens_cached sum past what an integer column holds" % rel)
        columns["total_tokens"] = total
        columns["usage_json"] = json.dumps({"source": "transcript", "imported": True, "usage": {
            "input_tokens": values["tokens_in"], "output_tokens": values["tokens_out"],
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": values["tokens_cached"]}})
    if "cost_usd" in fm:  # SPD-013: the list-price cost the note showed, kept with the imported sum it was computed from
        value = fm["cost_usd"]
        if not isinstance(value, str) or not prices.COST_USD.fullmatch(value):
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s: cost_usd must be an amount of US dollars with at most two decimals, not %r" % (rel, value))
        if "usage_json" not in columns:
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s: cost_usd needs tokens_out, tokens_in and tokens_cached beside it" % rel)
        columns["usage_json"] = json.dumps(dict(json.loads(columns["usage_json"]), cost_usd=prices.usd_text(lazy.fractions.Fraction(value))))
    return columns


def import_member_file(ctx, con, at, path, rel, team_key):
    doc = markdown.split_document(path.read_text(encoding="utf-8"), markdown.IMPORT_MEMBER_SECTIONS)
    fm = doc["frontmatter"]
    require_keys(fm, ["id", "name", "persona", "model", "parent", "ticket", "status", "spawned", "finished"], rel)
    ticket = con.execute("SELECT * FROM tickets WHERE team_key = ?", (team_key,)).fetchone()
    if ticket is None:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: no ticket has team key %s (import the tickets first)" % (rel, team_key))
    if parse_link(fm["ticket"]) != ticket["key"]:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: ticket %s does not match folder %s" % (rel, fm["ticket"], team_key))
    if "project" in fm and fm["project"] != lookup.project_key_of(con, ticket):
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: project %r, but %s is project %s's" % (rel, fm["project"], ticket["key"], lookup.project_key_of(con, ticket)))
    lineage = fm["id"]
    if not re.fullmatch(r"\d+(\.\d+)*", lineage):
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: id %r is not a lineage" % (rel, lineage))
    if fm["name"] != path.stem:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: name %r does not match the file name" % (rel, fm["name"]))
    if con.execute("SELECT 1 FROM members WHERE ticket_id = ? AND (name = ? OR lineage = ?)", (ticket["id"], fm["name"], lineage)).fetchone():
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s/%s is already in the ledger (imported before)" % (team_key, fm["name"]))
    parent_link = parse_link(fm["parent"])
    parent = None
    if parent_link and parent_link != "Spud":
        parent = lookup.get_member(con, parent_link)
        if parent["ticket_id"] != ticket["id"] or not lineage.startswith(parent["lineage"] + "."):
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s: parent %s does not fit lineage %s" % (rel, parent_link, lineage))
    elif "." in lineage:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: lineage %s needs a parent member" % (rel, lineage))
    if fm["persona"] not in kernel.PERSONAS or fm["model"] not in kernel.MODELS or fm["status"] not in kernel.MEMBER_STATUSES:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: persona, model or status outside the schema's values" % rel)
    names = [n for n, _ in doc["sections"]]
    if len(set(names)) != len(names):
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s: a section repeats" % rel)
    sections = dict(doc["sections"])
    spawned = fm["spawned"] or None
    finished = fm["finished"] or None
    derived = {"planned_at": "spawned" if spawned else "ticket.created"}
    blocked = sections.get("Blocked") if "Blocked" in sections else None
    usage = usage_columns(fm, rel)
    cur = con.execute(
        "INSERT INTO members (ticket_id, lineage, depth, parent_id, name, persona, agent_type, model, status, brief,"
        " result, blocked, outcome, layout, planned_at, spawned_at, finished_at, duration_ms, tool_uses, total_tokens, usage_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ticket["id"], lineage, lineage.count(".") + 1, parent["id"] if parent else None, fm["name"], fm["persona"],
         fm.get("agent_type") or "spudagent", fm["model"], fm["status"], sections.get("Brief", ""),
         sections.get("Result") if "Result" in sections else None, blocked,
         sections.get("Outcome") if "Outcome" in sections else None,
         json.dumps(member_layout(names, blocked)) if member_layout(names, blocked) else None,
         spawned or ticket["created_at"], spawned, finished,
         usage.get("duration_ms"), usage.get("tool_uses"), usage.get("total_tokens"), usage.get("usage_json")),
    )
    member_id = cur.lastrowid
    ledgerdb.write_event(con, at, "import", "import", "imported %s" % rel, ticket_id=ticket["id"], member_id=member_id, data={"source": rel, "derived": derived})
    for entry_at, body in markdown.parse_log_entries(sections.get("Log", "")):
        ledgerdb.write_event(con, entry_at, "import", "member.log", body, ticket_id=ticket["id"], member_id=member_id,
                    data={"source": rel, "date_only": "T" not in entry_at})
    return {"member": lookup.get_member_by_id(con, member_id), "ticket": ticket, "fm": fm, "sections": doc["sections"], "rel": rel}
