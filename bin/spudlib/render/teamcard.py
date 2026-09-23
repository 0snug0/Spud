"""render/teamcard: The Team table and its cells."""

from . import prices
from ..core import lazy
from ..state import lookup


def team_line(team_key, m):
    return "- [[%s/%s|%s]] (%s, %s, %s)" % (team_key, m["name"], m["name"], m["lineage"], lookup.persona_label(m), m["model"])


def prose_for(con, entity, entity_id, section):
    row = con.execute(
        "SELECT body, through_id FROM imported_sections WHERE entity = ? AND entity_id = ? AND section = ?",
        (entity, entity_id, section),
    ).fetchone()
    return (row["body"], row["through_id"]) if row else (None, 0)


def join_prose(prose, rows_text):
    parts = [p for p in (prose, rows_text) if p]
    return "\n".join(parts)


# The Team card.  A ticket
# note's ## Team is generated from `members` alone: a table of who ran as what, on which model and
# how it went; the member tree, each line carrying what that member worked on and built; and the
# embedded Fleet.base Team view.  The spec's reference implementation is the contract.

# Cost (list): a run at the API list price in USD, never Eric's subscription bill; a Total row ends the table.
TEAM_TABLE_HEAD = ("| Member | ID | Persona | Model | Status | Run | Tokens | Cost (list) | Tools |", "|---|---|---|---|---|---|---|---|---|")
TEAM_TOTAL_LABEL = "**Total**"
TEAM_VIEW_EMBED = "![[Fleet.base#Team]]"
NOT_RECORDED = "—"  # alone in a cell: planned and imported rows, runs without usage
WORKED_ON_CAP = 400  # characters (code points), markdown included


def humanize(n):
    """A token count in integer arithmetic: 999, 4.4k, 161k, 2.9M, 44.3M, 1.2B."""
    if n < 1000:
        return str(n)
    tenths = (n * 10 + 500) // 1000
    if tenths < 100:
        return "%d.%dk" % (tenths // 10, tenths % 10)
    k = (n + 500) // 1000
    if k < 1000:
        return "%dk" % k
    tenths = (n * 10 + 500_000) // 1_000_000
    if tenths < 10_000:
        return "%d.%dM" % (tenths // 10, tenths % 10)
    tenths = (n * 10 + 500_000_000) // 1_000_000_000
    return "%d.%dB" % (tenths // 10, tenths % 10)


def run_duration(ms):
    """duration_ms to the nearest minute: <1 min, N min, H h, H h M min; None when not recorded."""
    if ms is None:
        return None
    minutes = (ms + 30_000) // 60_000
    if minutes == 0:
        return "<1 min"
    if minutes < 60:
        return "%d min" % minutes
    hours, minutes = divmod(minutes, 60)
    return "%d h" % hours if minutes == 0 else "%d h %d min" % (hours, minutes)


def known_stamp(stamp):
    """The stamp, or None when it is NULL, empty, has no time, or is an imported minute-only stamp at
    00:00 (the markdown-v0 placeholder for a spawn that never ran; the hooks write seconds and an offset)."""
    if not stamp or len(stamp) < 16 or (len(stamp) == 16 and stamp.endswith("T00:00")):
        return None
    return stamp


def run_cell(m, ticket_created):
    """The member's own run, from spawn to return (stopped_at, else finished_at: the verdict adds the
    parent's review time), in the clock it was written in; the start is dated when its day is not the
    ticket's, the end when its day is not the start's; then the measured duration."""
    start = known_stamp(m["spawned_at"])
    if start is None:
        return NOT_RECORDED
    text = start[11:16] if start[:10] == ticket_created[:10] else start[:10] + " " + start[11:16]
    text += " →"
    end = known_stamp(m["stopped_at"]) or known_stamp(m["finished_at"])
    if end:
        text += " " + (end[11:16] if end[:10] == start[:10] else end[:10] + " " + end[11:16])
    duration = run_duration(m["duration_ms"])
    if duration:
        text += " · " + duration
    return text


def card_cell(text):
    """A table cell built from data: a pipe would split the row."""
    return str(text).replace("|", "\\|")


def team_table_row(team_key, ticket_created, m, pricing=None):
    marker = ("↳" * (m["depth"] - 1) + " ") if m["depth"] > 1 else ""
    counts = prices.token_counts(m["usage_json"])
    cost, _ = prices.run_cost(m["usage_json"], pricing)
    cells = [
        "%s[[%s/%s\\|%s]]" % (marker, team_key, m["name"], m["name"]),
        m["lineage"],
        card_cell(lookup.persona_label(m)),
        card_cell(m["model"] + (" (%s)" % m["resolved_model"] if m["resolved_model"] else "")),
        "**%s**" % m["status"] if m["status"] in ("blocked", "failed") else m["status"],
        run_cell(m, ticket_created),
        tokens_text(counts) if counts else NOT_RECORDED,
        prices.money(cost) if cost is not None else NOT_RECORDED,
        str(m["tool_uses"]) if m["tool_uses"] is not None else NOT_RECORDED,
    ]
    return "| " + " | ".join(cells) + " |"


def team_order(rows, lead_id):
    """Lineage order, which sorts as a tree; the lead's subtree first when the lead is not 01."""
    rows = sorted(rows, key=lambda m: m["lineage"])
    lead = next((m for m in rows if m["id"] == lead_id), None)
    if lead is None:
        return rows
    root = lead["lineage"].split(".")[0]
    return [m for m in rows if m["lineage"].split(".")[0] == root] + [m for m in rows if m["lineage"].split(".")[0] != root]


def usage_pairs(m, pricing=None):
    """A member note's usage keys, each only when known: after finished, before tags.  cost_usd is the run at
    the API list price, from the price table the render is given; a run without a cost has no cost_usd."""
    pairs = []
    if m["duration_ms"] is not None:
        pairs.append(("duration_ms", ("plain", m["duration_ms"])))
    if m["tool_uses"] is not None:
        pairs.append(("tool_uses", ("plain", m["tool_uses"])))
    counts = prices.token_counts(m["usage_json"])
    if counts:
        pairs += [("tokens_out", ("plain", counts["out"])), ("tokens_in", ("plain", counts["in"])), ("tokens_cached", ("plain", counts["cached"]))]
    cost, _ = prices.run_cost(m["usage_json"], pricing)
    if cost is not None:
        pairs.append(("cost_usd", ("plain", prices.cost_number(cost))))
    return pairs


def tokens_text(counts):
    """{out, in, cached} as the card and the Team table show them."""
    return "%s out · %s in · %s cached" % (humanize(counts["out"]), humanize(counts["in"]), humanize(counts["cached"]))


def team_totals(rows, pricing):
    """A ticket's totals over its members' rows: tokens (out, in, cached) over every member with a transcript sum; the
    list-price cost of those priced, kept exact until it is shown, and each member whose sum has no cost, with the
    reasons (a completion alone is no sum and never makes the total partial); tool uses over the members that have them."""
    totals = {"tokens": None, "cost": None, "not_priced": [], "tools": None}
    for m in rows:
        if m["tool_uses"] is not None:
            totals["tools"] = (totals["tools"] or 0) + m["tool_uses"]
        counts = prices.token_counts(m["usage_json"])
        if counts is None:
            continue
        totals["tokens"] = {k: (totals["tokens"] or {}).get(k, 0) + counts[k] for k in counts}
        cost, reasons = prices.run_cost(m["usage_json"], pricing)
        if cost is None:
            totals["not_priced"].append((m, reasons))
        else:
            totals["cost"] = (totals["cost"] or lazy.fractions.Fraction(0)) + cost
    return totals


def team_total_row(totals):
    """The Team table's last row: the ticket's tokens, its list-price cost (partial when a member's sum has none), its tools."""
    cost = NOT_RECORDED if totals["cost"] is None else prices.money(totals["cost"]) + (" (partial)" if totals["not_priced"] else "")
    cells = [TEAM_TOTAL_LABEL, "", "", "", "", "", tokens_text(totals["tokens"]) if totals["tokens"] else NOT_RECORDED, cost,
             NOT_RECORDED if totals["tools"] is None else str(totals["tools"])]
    return "| " + " | ".join(cells) + " |"
