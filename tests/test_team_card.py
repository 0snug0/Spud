"""The Team card of a ticket note (SPD-010).  The contract is docs/design/2026-09-12-team-card.md,
sections 1 to 9: the generated ## Team section (the stats table, the member tree carrying each
member's worked-on sentence, the embedded Fleet.base Team view), the five member usage keys, and
the importer reading summaries and usage back.  Expected values are the spec's own vectors and
mocks, or derived by hand from its rules.  Every case runs in a scratch SPUD_HOME; rows the CLI
cannot produce directly are set with a direct UPDATE or INSERT there, as section 9 allows."""

import json
import re
import unittest
from pathlib import Path

from helpers import (
    EXIT_CONFLICT,
    EXIT_ERROR,
    TEAM_TABLE_DELIMITER as DELIMITER,
    TEAM_TABLE_HEADER as HEADER,
    TEAM_VIEW_EMBED as EMBED,
    load_spud_module,
    real_config,
)
from hookcase import AGENT_B, COMPLETION, PER_ENTRY_SUM, HookCase, InProcessCase

spud = load_spud_module()
FIXTURE = json.loads((Path(__file__).resolve().parent / "fixtures" / "team_card.json").read_text(encoding="utf-8"))
USAGE_KEYS = ("duration_ms", "tool_uses", "tokens_out", "tokens_in", "tokens_cached", "cost_usd")


def usage_json_of(input_tokens, output_tokens, cache_creation, cache_read, messages=40):
    """A usage_json as transcript_usage stores it at SubagentStop."""
    return json.dumps({"source": "transcript", "messages": messages, "usage": {
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation, "cache_read_input_tokens": cache_read}})


# Pompadour's run on SPD-015, as the hooks recorded it.
POMPADOUR = dict(status="done", spawned_at="2026-09-12T20:01:23-07:00", stopped_at="2026-09-12T20:36:51-07:00",
                 finished_at="2026-09-12T20:41:50-07:00", duration_ms=2127776, tool_uses=105, total_tokens=47394682,
                 resolved_model="claude-opus-5", usage_json=usage_json_of(4416, 160812, 2884389, 44345065))


def team_section(text):
    """The body of a rendered ticket note's ## Team section, byte for byte."""
    rest = text.split("\n## Team\n", 1)[1]
    return "" if rest.startswith("\n## Handoffs\n") else rest.split("\n\n## Handoffs\n", 1)[0]


def card_blocks(section):
    """The card's three blocks, split on its blank lines: the table, the tree, the embed."""
    blocks = section.split("\n\n")
    if len(blocks) != 3:
        raise AssertionError("not a Team card (a table, a tree and the embed): %r" % section)
    return blocks


TOTAL = "| **Total** |"  # the table's last row since SPD-013: the ticket's tokens, list-price cost and tools


def table_rows(section):
    """The member rows of the card's table: the rows after the delimiter, without the Total row that ends it."""
    rows = card_blocks(section)[0].split("\n")[2:]
    if not rows or not rows[-1].startswith(TOTAL) or any(row.startswith(TOTAL) for row in rows[:-1]):
        raise AssertionError("the table does not end with its one Total row: %r" % rows)
    return rows[:-1]


def total_row(section):
    """The Total row that ends the card's table."""
    rows = card_blocks(section)[0].split("\n")
    if not rows[-1].startswith(TOTAL):
        raise AssertionError("the table does not end with its Total row: %r" % rows)
    return rows[-1]


def tree_lines(section):
    return card_blocks(section)[1].split("\n")


def cells(row):
    """A table row's cells, split on the pipes that are not escaped."""
    parts = re.split(r"(?<!\\)\|", row)
    if len(parts) < 3 or parts[0] != "" or parts[-1] != "":
        raise AssertionError("not a table row: %r" % row)
    return [part.strip() for part in parts[1:-1]]


def member_of(row):
    """The member name in a row's Member cell."""
    return cells(row)[0].split("/", 1)[1].split("\\|", 1)[0]


def frontmatter(text):
    lines = text.split("\n")
    return lines[1 : lines.index("---", 1)]


def usage_lines(text):
    return [line for line in frontmatter(text) if line.split(":", 1)[0] in USAGE_KEYS]


class TeamCardCase(InProcessCase):
    """Builders over the scratch home: direct row writes and a render into <home>/out.  SPD-242: the CLI in this process,
    and a subclass's rows made once per class in build_home (hookcase.InProcessCase)."""

    def sql(self, statement, *params):
        con = self.home.connect()
        try:
            with con:
                return con.execute(statement, params).lastrowid
        finally:
            con.close()

    def set(self, table, row_id, **columns):
        self.sql("UPDATE %s SET %s WHERE id = ?" % (table, ", ".join("%s = ?" % k for k in columns)), *columns.values(), row_id)

    def insert_member(self, ticket_id, lineage, name, **columns):
        parent = None
        if "." in lineage:
            parent = self.home.scalar("SELECT id FROM members WHERE ticket_id = ? AND lineage = ?", ticket_id, lineage.rsplit(".", 1)[0])
        row = {"ticket_id": ticket_id, "lineage": lineage, "depth": lineage.count(".") + 1, "parent_id": parent, "name": name,
               "persona": "scout", "model": "haiku", "status": "done", "planned_at": "2026-09-12T19:00:00-07:00"}
        row.update(columns)
        return self.sql("INSERT INTO members (%s) VALUES (%s)" % (", ".join(row), ", ".join("?" * len(row))), *row.values())

    def render_out(self):
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        return out

    def team(self, key="SPD-001"):
        return team_section((self.render_out() / "ledger" / "tickets" / ("%s.md" % key)).read_text(encoding="utf-8"))

    def member_note(self, name, team="SPUD-001"):
        return (self.render_out() / "ledger" / "teams" / team / ("%s.md" % name)).read_text(encoding="utf-8")


# =============================================================================
# Section 1: the section, its cells, its order
# =============================================================================


class TeamSectionTest(TeamCardCase):
    def test_no_team_renders_an_empty_section(self):  # case 1
        self.new_ticket("Nobody yet")
        self.assertEqual(self.team(), "")

    def test_a_planned_member_never_spawned(self):  # case 6
        t = self.new_ticket("Planned")
        self.new_member(t["key"], name="Russet")
        self.assertEqual(
            self.team(),
            "\n".join([
                HEADER,
                DELIMITER,
                "| [[SPUD-001/Russet\\|Russet]] | 01 | scout | haiku | planned | — | — | — | — |",
                "| **Total** |  |  |  |  |  | — | — | — |",
                "",
                "- [[SPUD-001/Russet|Russet]] (01, scout, haiku)",
                "",
                EMBED,
            ]),
        )
        self.assertEqual(usage_lines(self.member_note("Russet")), [])

    def test_stored_team_prose_is_not_rendered_and_stays_stored(self):  # case 2
        t = self.new_ticket("Prose")
        self.new_member(t["key"], name="Russet")
        self.sql("INSERT INTO imported_sections (entity, entity_id, section, body, through_id) VALUES ('ticket', ?, 'Team', '- custom prose', 0)", t["id"])
        self.assertEqual(
            self.team().split("\n"),
            [HEADER, DELIMITER, "| [[SPUD-001/Russet\\|Russet]] | 01 | scout | haiku | planned | — | — | — | — |", "| **Total** |  |  |  |  |  | — | — | — |", "",
             "- [[SPUD-001/Russet|Russet]] (01, scout, haiku)", "", EMBED],
        )
        self.assertEqual(self.home.rows("SELECT entity, section, body FROM imported_sections"), [{"entity": "ticket", "section": "Team", "body": "- custom prose"}])

    def test_failed_before_spawning(self):  # case 7
        t = self.new_ticket("Refused")
        m = self.new_member(t["key"], name="Russet")
        self.home.json("member", "finish", m["ref"], "--status", "failed", "--outcome", "The spawn was refused.", actor="spud")
        self.assertEqual([cells(r) for r in table_rows(self.team())], [["[[SPUD-001/Russet\\|Russet]]", "01", "scout", "haiku", "**failed**", "—", "—", "—", "—"]])

    def test_a_contractor(self):  # case 4
        t = self.new_ticket("Contractor")
        lead = self.new_member(t["key"], name="Russet", persona="engineer", model="opus")
        self.new_member(t["key"], actor=lead["ref"], name="Yukon", persona="contractor", model="sonnet", agent_type="claude-code-guide")
        section = self.team()
        self.assertEqual(table_rows(section)[1], "| ↳ [[SPUD-001/Yukon\\|Yukon]] | 01.01 | contractor on `claude-code-guide` | sonnet | planned | — | — | — | — |")
        self.assertEqual(tree_lines(section)[1], "  - [[SPUD-001/Yukon|Yukon]] (01.01, contractor on `claude-code-guide`, sonnet)")
        # the Team view's Persona formula reads these two keys: `contractor on claude-code-guide`
        fm = frontmatter(self.member_note("Yukon"))
        self.assertIn("persona: contractor", fm)
        self.assertIn("agent_type: claude-code-guide", fm)

    def test_pipes_in_data_are_escaped_in_cells_and_kept_on_the_tree(self):  # case 9, the table half
        t = self.new_ticket("Pipes")
        lead = self.new_member(t["key"], name="Russet", persona="engineer", model="opus")
        self.new_member(t["key"], actor=lead["ref"], name="Yukon", persona="contractor", model="sonnet", agent_type="odd|type")
        self.set("members", lead["id"], resolved_model="claude|opus")
        section = self.team()
        rows = table_rows(section)
        self.assertEqual([len(cells(r)) for r in rows], [9, 9])
        self.assertEqual(cells(rows[0])[3], "opus (claude\\|opus)")
        self.assertEqual(cells(rows[1])[2], "contractor on `odd\\|type`")
        self.assertEqual(tree_lines(section)[1], "  - [[SPUD-001/Yukon|Yukon]] (01.01, contractor on `odd|type`, sonnet)")

    def test_order_puts_the_leads_subtree_first(self):  # case 17
        t = self.new_ticket("Order")
        first = self.new_member(t["key"], name="Russet")
        self.new_member(t["key"], actor=first["ref"], name="Yukon")
        second = self.new_member(t["key"], name="Kennebec")
        self.new_member(t["key"], actor=second["ref"], name="Fingerling")

        def order():
            section = self.team()
            return [cells(r)[1] for r in table_rows(section)], [re.search(r"\((\d+(?:\.\d+)*),", line).group(1) for line in tree_lines(section)]

        self.assertEqual(order(), (["01", "01.01", "02", "02.01"], ["01", "01.01", "02", "02.01"]))
        self.set("tickets", t["id"], lead_id=second["id"])
        self.assertEqual(order(), (["02", "02.01", "01", "01.01"], ["02", "02.01", "01", "01.01"]))
        self.set("tickets", t["id"], lead_id=None)
        self.assertEqual(order(), (["01", "01.01", "02", "02.01"], ["01", "01.01", "02", "02.01"]))


def deeper_config():
    config = real_config()
    config["limits"]["max_depth"] = 3
    return config


class ThreeDeepTest(TeamCardCase):
    config = deeper_config()  # only SPD-001's imported history is three deep; max_depth is 2

    def test_a_member_three_deep(self):  # case 5
        t = self.new_ticket("Deep")
        kestrel = self.new_member(t["key"], name="Kestrel", persona="writer", model="opus", tier_reason="the lead")
        huckleberry = self.new_member(t["key"], actor=kestrel["ref"], name="Huckleberry", persona="writer", model="sonnet")
        self.new_member(t["key"], actor=huckleberry["ref"], name="Ozette")
        section = self.team()
        self.assertEqual(
            [cells(r)[0] for r in table_rows(section)],
            ["[[SPUD-001/Kestrel\\|Kestrel]]", "↳ [[SPUD-001/Huckleberry\\|Huckleberry]]", "↳↳ [[SPUD-001/Ozette\\|Ozette]]"],
        )
        self.assertEqual(tree_lines(section)[2], "    - [[SPUD-001/Ozette|Ozette]] (01.01.01, scout, haiku)")


# =============================================================================
# Sections 1.3 and 2: Run, Tokens, Tools
# =============================================================================


class RunAndTokensTest(TeamCardCase):
    CREATED = "2026-09-12T19:00:00-07:00"

    def build_home(self):
        self.t = self.new_ticket("Runs")
        self.set("tickets", self.t["id"], created_at=self.CREATED)

    def columns(self, *indexes):
        return {member_of(r): tuple(cells(r)[i] for i in indexes) for r in table_rows(self.team())}

    def test_the_run_table(self):  # cases 13, 14, 15 and 20: section 1.3's table
        tid = self.t["id"]
        self.insert_member(tid, "01", "Pompadour", spawned_at="2026-09-12T20:01:23-07:00", stopped_at="2026-09-12T20:36:51-07:00",
                           finished_at="2026-09-12T20:41:50-07:00", duration_ms=2127776)
        self.insert_member(tid, "02", "Kestrel", spawned_at="2026-09-12T12:25", finished_at="2026-09-12T12:31")
        self.insert_member(tid, "03", "Ozette", spawned_at="2026-09-12T00:00", finished_at="2026-09-12T13:00")
        self.insert_member(tid, "04", "Yukon", status="active", spawned_at="2026-09-12T20:01:23-07:00")
        self.insert_member(tid, "05", "Russet", status="active", spawned_at="2026-09-12T20:01:23-07:00",
                           stopped_at="2026-09-12T20:36:51-07:00", duration_ms=2127776)
        self.insert_member(tid, "06", "Fingerling", status="planned")
        self.insert_member(tid, "07", "Desiree", status="failed", finished_at="2026-09-12T20:05:00-07:00")
        self.insert_member(tid, "08", "Rooster", spawned_at="2026-09-13T09:05:00-07:00", stopped_at="2026-09-13T09:50:12-07:00",
                           finished_at="2026-09-13T09:55:00-07:00", duration_ms=2700000)
        self.insert_member(tid, "09", "Sarpo", spawned_at="2026-09-12T23:50:00-07:00", stopped_at="2026-09-13T00:20:40-07:00",
                           finished_at="2026-09-13T00:25:00-07:00", duration_ms=1840000)
        self.insert_member(tid, "10", "Linda", status="blocked", spawned_at="2026-09-12T20:10:00-07:00", finished_at="2026-09-12T20:30:00-07:00")
        want = {
            "Pompadour": ("done", "20:01 → 20:36 · 35 min"),
            "Kestrel": ("done", "12:25 → 12:31"),  # imported: no stop, no duration
            "Ozette": ("done", "—"),  # case 13: the imported 00:00 spawn
            "Yukon": ("active", "20:01 →"),  # case 14: still running
            "Russet": ("active", "20:01 → 20:36 · 35 min"),  # returned, verdict not yet recorded
            "Fingerling": ("planned", "—"),
            "Desiree": ("**failed**", "—"),
            "Rooster": ("done", "2026-09-13 09:05 → 09:50 · 45 min"),  # case 15: a later day than the ticket
            "Sarpo": ("done", "23:50 → 2026-09-13 00:20 · 31 min"),  # case 15: across midnight
            "Linda": ("**blocked**", "20:10 → 20:30"),
        }
        self.assertEqual(self.columns(4, 5), want)

    def test_tokens_and_tools(self):  # case 20: section 2's figures and number format
        tid = self.t["id"]
        members = [
            ("Pompadour", {"usage_json": usage_json_of(4416, 160812, 2884389, 44345065), "tool_uses": 105}),
            ("Sarpo", {"usage_json": usage_json_of(5000, 121994, 2608656, 22208820), "tool_uses": 70}),
            ("Yukon", {"usage_json": usage_json_of(999, 0, 0, 1000), "tool_uses": 0}),
            ("Russet", {"usage_json": usage_json_of(9950, 9949, 0, 999499)}),
            ("Kennebec", {"usage_json": usage_json_of(999949999, 999500, 0, 999950000)}),
            ("Fingerling", {"usage_json": usage_json_of(4416, 1234567890, 0, 2888805)}),
            ("Desiree", {"usage_json": json.dumps({"source": "transcript", "usage": {"output_tokens": 5}})}),
            ("Rooster", {"usage_json": json.dumps({"source": "transcript", "usage": {"input_tokens": -1, "output_tokens": 5}})}),
            ("Agria", {"usage_json": json.dumps({"source": "transcript", "usage": {"output_tokens": True}})}),
            ("Bintje", {"usage_json": json.dumps({"source": "transcript", "usage": {"output_tokens": 1.5}})}),
            ("Nicola", {"usage_json": json.dumps({"source": "transcript", "usage": [1, 2]})}),
            ("Estima", {"usage_json": json.dumps({"source": "PostToolUse", "usage": {"output_tokens": 40}})}),
            ("Marfona", {"usage_json": json.dumps([1])}),
            ("Cara", {}),
        ]
        for n, (name, columns) in enumerate(members, start=1):
            self.insert_member(tid, "%02d" % n, name, **columns)
        self.assertEqual(self.columns(6, 8), {
            "Pompadour": ("161k out · 2.9M in · 44.3M cached", "105"),
            "Sarpo": ("122k out · 2.6M in · 22.2M cached", "70"),
            "Yukon": ("0 out · 999 in · 1.0k cached", "0"),
            "Russet": ("9.9k out · 10k in · 999k cached", "—"),
            "Kennebec": ("1.0M out · 999.9M in · 1.0B cached", "—"),
            "Fingerling": ("1.2B out · 4.4k in · 2.9M cached", "—"),
            "Desiree": ("5 out · 0 in · 0 cached", "—"),
            "Rooster": ("—", "—"),
            "Agria": ("—", "—"),
            "Bintje": ("—", "—"),
            "Nicola": ("—", "—"),
            "Estima": ("—", "—"),
            "Marfona": ("—", "—"),
            "Cara": ("—", "—"),
        })

    def test_a_completion_without_a_transcript_sum_shows_no_tokens(self):  # case 16, since SPD-021
        m = self.new_member(self.t["key"], name="Pompadour", persona="engineer", model="opus")
        # what record_completion stores for a completed foreground Agent call before any transcript sum:
        # the completion's figures under "completion" and total_tokens empty (tests/hookcase.py's payload)
        completion = {"source": "PostToolUse", "completion": COMPLETION}
        self.set("members", m["id"], status="active", spawned_at="2026-09-12T13:30:00-07:00", stopped_at="2026-09-12T13:30:05-07:00",
                 total_tokens=None, duration_ms=4791, tool_uses=1, usage_json=json.dumps(completion))
        self.assertEqual(cells(table_rows(self.team())[0])[5:], ["13:30 → 13:30 · <1 min", "—", "—", "1"])
        self.assertEqual(usage_lines(self.member_note("Pompadour")), ["duration_ms: 4791", "tool_uses: 1"])


# =============================================================================
# Section 3: worked on / built, through the tree line's suffix
# =============================================================================


class WorkedOnTest(TeamCardCase):
    LINE = "- [[SPUD-001/Russet|Russet]] (01, scout, haiku)"

    def build_home(self):
        self.t = self.new_ticket("Worked on")
        self.m = self.new_member(self.t["key"], name="Russet")
        self.home.json("member", "start", self.m["ref"], actor="spud")

    def suffix(self):
        line = tree_lines(self.team())[0]
        if line == self.LINE:
            return None
        self.assertTrue(line.startswith(self.LINE + " — "), line)
        return line[len(self.LINE + " — "):]

    def test_blocked_reads_summary_then_result_then_return_text_never_blocked(self):  # case 8
        self.home.json("member", "block", "Which colour: red or blue?", actor=self.m["ref"])
        self.home.json("member", "finish", self.m["ref"], "--status", "blocked", "--outcome", "Asked Eric.", actor="spud")
        self.assertEqual(cells(table_rows(self.team())[0])[4], "**blocked**")
        self.assertIsNone(self.suffix())
        self.set("members", self.m["id"], return_text="Stopped at the colour question after drafting both palettes.")
        self.assertEqual(self.suffix(), "Stopped at the colour question after drafting both palettes.")
        self.set("members", self.m["id"], result="Drafted the red and the blue palette in `docs/colours.md`.")
        self.assertEqual(self.suffix(), "Drafted the red and the blue palette in `docs/colours.md`.")
        self.set("members", self.m["id"], summary="Drafted two palettes in docs/colours.md and asked Eric which one ships.")
        self.assertEqual(self.suffix(), "Drafted two palettes in docs/colours.md and asked Eric which one ships.")

    def test_a_summary_with_a_pipe_and_a_newline_is_one_line(self):  # case 9, the tree half
        self.home.json("member", "finish", self.m["ref"], "--status", "done", "--outcome", "Accepted.",
                       "--summary", "Split `a|b` parsing and the a | b case\nacross two lines.", actor="spud")
        self.assertEqual(self.suffix(), "Split `a|b` parsing and the a | b case across two lines.")
        self.assertEqual([len(cells(r)) for r in table_rows(self.team())], [9])

    def test_a_summary_that_is_a_bullet_list_falls_through_to_the_result(self):  # case 10
        self.set("members", self.m["id"], summary="- first\n- second", result="Built the Team card renderer. Tests green.")
        self.assertEqual(self.suffix(), "Built the Team card renderer. Tests green.")

    def test_a_result_with_a_comment_a_status_line_a_list_and_a_stop_label_gives_its_prose(self):  # case 11
        self.set("members", self.m["id"], result="<!-- template -->\nProduced on the worktree branch x, uncommitted.\n- item one\n\n**Verified.** 12 tests.\n\nThe parser now reads block lists.")
        self.assertEqual(self.suffix(), "The parser now reads block lists.")

    def test_only_status_lines_and_a_handle_give_no_suffix(self):  # case 12
        self.set("members", self.m["id"], return_text="Nothing further is needed.\n\n**SPUD-015/Pompadour (01, engineer) — done.**\n\n1. an item")
        self.assertIsNone(self.suffix())

    def test_the_cut(self):  # case 19: section 3.6's four cut vectors
        def clauses(last):
            return ", ".join("the `section_%02d` renderer" % n for n in range(1, last + 1))

        vectors = [
            ("29 clauses, no sentence end fits", "Renders " + clauses(29) + ".", "Renders " + clauses(14) + ", the…", 390),
            ("a first sentence over 400, then a short one", "Built a " + "renderers " * 45 + "for the card. Second sentence.",
             "Built a " + "renderers " * 38 + "renderers…", 398),
            ("two short sentences", "Built A. Built B.", "Built A. Built B.", 17),
            ("e.g. is no sentence end", "Wrote the history, e.g. the 1570 arrival, and " + "more detail " * 35 + "at the end. Second sentence here.",
             "Wrote the history, e.g. the 1570 arrival, and " + "more detail " * 29 + "more…", 399),
        ]
        for name, summary, want, length in vectors:
            with self.subTest(name):
                self.set("members", self.m["id"], summary=summary)
                got = self.suffix()
                self.assertEqual((got, len(got or "")), (want, length))


# =============================================================================
# Section 6.3: the member usage keys
# =============================================================================


class UsageKeysTest(TeamCardCase):
    def build_home(self):
        self.t = self.new_ticket("Usage")
        self.m = self.new_member(self.t["key"], name="Pompadour", persona="engineer", model="opus")
        self.set("members", self.m["id"], **POMPADOUR)

    def test_pompadours_frontmatter(self):
        self.assertEqual(
            frontmatter(self.member_note("Pompadour")),
            ['id: "01"', "name: Pompadour", "persona: engineer", "model: opus", "effort: high", 'parent: "[[Spud]]"', 'ticket: "[[SPD-001]]"', "project: spud",
             "status: done", "spawned: 2026-09-12T20:01", "finished: 2026-09-12T20:41", "duration_ms: 2127776", "tool_uses: 105",
             "tokens_out: 160812", "tokens_in: 2888805", "tokens_cached: 44345065", "tags: [spudagent]"],
        )

    def test_each_key_only_when_known(self):
        cases = [
            ("a duration alone", dict(tool_uses=None, total_tokens=None, usage_json=None), ["duration_ms: 2127776"]),
            ("zeros are known", dict(duration_ms=None, tool_uses=0, usage_json=usage_json_of(0, 0, 0, 0)),
             ["tool_uses: 0", "tokens_out: 0", "tokens_in: 0", "tokens_cached: 0"]),
            ("a completion alone", dict(duration_ms=None, tool_uses=None, total_tokens=None, usage_json=json.dumps({"source": "PostToolUse", "completion": COMPLETION})), []),
            ("a figure that is no count", dict(duration_ms=None, tool_uses=None, usage_json=json.dumps({"source": "transcript", "usage": {"output_tokens": -1}})), []),
        ]
        for name, columns, want in cases:
            with self.subTest(name):
                self.set("members", self.m["id"], **dict(POMPADOUR, **columns))
                self.assertEqual(usage_lines(self.member_note("Pompadour")), want)

    def test_a_restyled_note_with_the_usage_keys_is_style_only(self):  # case 18
        self.home.json("render")
        rel = "ledger/teams/SPUD-001/Pompadour.md"
        path = self.home.path / rel
        rendered = path.read_text(encoding="utf-8")
        block = "finished: 2026-09-12T20:41\nduration_ms: 2127776\ntool_uses: 105\ntokens_out: 160812\ntokens_in: 2888805\ntokens_cached: 44345065\ntags: [spudagent]\n"
        self.assertEqual(rendered.count(block), 1)
        # what Obsidian writes over a note it has open: its own key order, a block list, the numbers bare
        restyled = rendered.replace(block, "tokens_cached: 44345065\nfinished: 2026-09-12T20:41\ntags:\n  - spudagent\ntool_uses: 105\ntokens_in: 2888805\nduration_ms: 2127776\ntokens_out: 160812\n")
        path.write_text(restyled, encoding="utf-8")
        out = self.home.json("render")
        self.assertEqual((out["written"], out["restyled"], out["conflicts"]), ([rel], [rel], []))
        self.assertEqual(path.read_text(encoding="utf-8"), rendered)
        events = [json.loads(r["data"]) for r in self.home.rows("SELECT data FROM events WHERE kind = 'render' AND json_extract(data, '$.style_only') = 1")]
        self.assertEqual([(e["path"], e["text"]) for e in events], [(rel, restyled)])
        # a changed number is a value, not a style
        path.write_text(rendered.replace("tokens_out: 160812\n", "tokens_out: 160813\n"), encoding="utf-8")
        self.assertEqual(self.home.run("render", check=False).returncode, EXIT_CONFLICT)

    # section 8.2, import --file unchanged: an edited tool_uses is the second case below (SPD-233 retired its own test,
    # whose every assertion that case makes too)
    def test_import_file_refuses_every_edited_usage_key(self):  # SPD-022: the fallthrough lied about the column
        self.home.json("render")
        path = self.home.path / "ledger" / "teams" / "SPUD-001" / "Pompadour.md"
        # (key, its rendered value, the real column its figure lives in, whether it is derived from usage_json)
        cases = (
            ("duration_ms", "2127776", "duration_ms", False),
            ("tool_uses", "105", "tool_uses", False),
            ("tokens_out", "160812", "usage_json", True),
            ("tokens_in", "2888805", "usage_json", True),
            ("tokens_cached", "44345065", "usage_json", True),
        )
        for key, original, column, derived in cases:
            with self.subTest(key):
                before = self.home.scalar("SELECT %s FROM members WHERE id = ?" % column, self.m["id"])
                text = path.read_text(encoding="utf-8")
                line = "%s: %s\n" % (key, original)
                self.assertEqual(text.count(line), 1, key)
                path.write_text(text.replace(line, "%s: 1\n" % key), encoding="utf-8")
                proc = self.home.run("import", "--file", path, actor="spud", check=False)
                self.assertEqual(proc.returncode, EXIT_ERROR, key)
                self.assertIn(key, proc.stderr, key)
                self.assertIn("not editable by hand", proc.stderr, key)
                self.assertIn("spud member resum", proc.stderr, key)
                self.assertNotIn("has no column", proc.stderr, key)
                if derived:
                    self.assertIn("derived from the transcript sum", proc.stderr, key)
                else:
                    self.assertIn("the hooks record it", proc.stderr, key)
                self.assertEqual(self.home.scalar("SELECT %s FROM members WHERE id = ?" % column, self.m["id"]), before, key)
                self.home.json("render", "--discard", path, actor="spud")

    def test_import_file_refuses_an_edited_effort(self):  # SPD-222: member new sets it, member edit --model resets it
        self.home.json("render")
        path = self.home.path / "ledger" / "teams" / "SPUD-001" / "Pompadour.md"
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("\neffort: high\n"), 1)
        path.write_text(text.replace("\neffort: high\n", "\neffort: max\n"), encoding="utf-8")
        proc = self.home.run("import", "--file", path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("effort is not editable by hand", proc.stderr)
        self.assertNotIn("has no column", proc.stderr)
        self.assertEqual(self.home.scalar("SELECT effort FROM members WHERE id = ?", self.m["id"]), "high")

    def test_import_file_still_refuses_an_unknown_member_property(self):
        self.home.json("render")
        path = self.home.path / "ledger" / "teams" / "SPUD-001" / "Pompadour.md"
        text = path.read_text(encoding="utf-8")
        self.assertIn("tags: [spudagent]", text)
        path.write_text(text.replace("tags: [spudagent]", "tags: [spudagent]\ndue: tomorrow"), encoding="utf-8")
        proc = self.home.run("import", "--file", path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("due", proc.stderr)
        self.assertIn("has no column", proc.stderr)


class ForegroundUsageTest(HookCase):
    """A foreground member recorded by the hooks in the harness's order, SubagentStop and then
    PostToolUse(Agent, completed) (SPD-021): the card and the note read its transcript sum, beside
    its completion's whole-run duration and tool count."""

    in_process = True  # SPD-233: the hooks and the CLI in this process (hookcase.InProcessHome)

    def test_the_tokens_cell_and_the_token_keys_show_the_transcript_sum(self):
        m = self.plan(persona="engineer", model="opus")
        self.foreground(m, AGENT_B)
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        ticket = (out / "ledger" / "tickets" / ("%s.md" % self.t["key"])).read_text(encoding="utf-8")
        note = (out / "ledger" / "teams" / self.team / ("%s.md" % m["name"])).read_text(encoding="utf-8")
        self.assertEqual(cells(table_rows(team_section(ticket))[0])[6:], ["12 out · 130 in · 300 cached", "—", "1"])  # two_requests names no model: no cost
        self.assertEqual(usage_lines(note), ["duration_ms: 4791", "tool_uses: 1", "tokens_out: 12", "tokens_in: 130", "tokens_cached: 300"])


class ResummedUsageTest(HookCase):
    """SPD-023: a member whose stored sum added every entry of its transcript (the hooks before SPD-023)
    shows those figures until Spud runs `member resum`, and the figures counted once per request after."""

    in_process = True  # SPD-233

    def card_and_note(self, m):
        """The Tokens and Tools cells of m's row on the rendered card, and its note's usage keys."""
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        ticket = (out / "ledger" / "tickets" / ("%s.md" % self.t["key"])).read_text(encoding="utf-8")
        note = (out / "ledger" / "teams" / self.team / ("%s.md" % m["name"])).read_text(encoding="utf-8")
        return cells(table_rows(team_section(ticket))[0])[6:], usage_lines(note)

    def test_the_tokens_cell_and_the_token_keys_show_the_re_summed_figures(self):  # proof 6
        m = self.plan(persona="engineer", model="opus")
        self.spawn(m, AGENT_B)
        self.home.json("member", "result", "Built it.", actor=AGENT_B)
        path = self.write_transcript(AGENT_B, self.per_block())
        self.set_member(m["id"], stopped_at="2026-09-12T06:30:05-07:00", transcript_path=str(path), total_tokens=994, duration_ms=4500, tool_uses=3,
                        usage_json=json.dumps({"source": "transcript", "messages": 5, "usage": PER_ENTRY_SUM}))
        self.assertEqual(self.card_and_note(m), (["24 out · 370 in · 600 cached", "—", "3"],
                                                 ["duration_ms: 4500", "tool_uses: 3", "tokens_out: 24", "tokens_in: 370", "tokens_cached: 600"]))
        self.home.json("member", "resum", m["ref"], actor="spud")
        self.assertEqual(self.card_and_note(m), (["12 out · 130 in · 300 cached", "—", "3"],
                                                 ["duration_ms: 4500", "tool_uses: 3", "tokens_out: 12", "tokens_in: 130", "tokens_cached: 300"]))


class StableRenderTest(TeamCardCase):
    def test_a_second_render_writes_nothing(self):  # case 21
        t = self.new_ticket("Stable")
        lead = self.new_member(t["key"], name="Pompadour", persona="engineer", model="opus")
        child = self.new_member(t["key"], actor=lead["ref"], name="Elba", persona="contractor", model="sonnet", agent_type="claude-code-guide")
        self.set("members", lead["id"], summary="Extended the hold below Spud, with 13 red-first tests.", **POMPADOUR)
        self.set("members", child["id"], status="active", spawned_at="2026-09-12T20:05:00-07:00", result="Answered the seven questions with a quote each.")
        first = self.home.json("render")
        self.assertIn("ledger/tickets/SPD-001.md", first["written"])
        self.assertIn("\n" + EMBED + "\n", (self.home.path / "ledger" / "tickets" / "SPD-001.md").read_text(encoding="utf-8"))
        again = self.home.json("render")
        self.assertEqual((again["written"], again["restyled"], again["conflicts"]), ([], [], []))


# =============================================================================
# The spec's mocks, from the rows they were rendered from
# =============================================================================


class SpecMockTest(TeamCardCase):
    """tests/fixtures/team_card.json: the raw mocks, byte for byte, and the SPD-006 and SPD-015 rows."""

    def seed(self, key):
        team = FIXTURE["teams"][key]
        number = int(key.split("-")[1])
        t = self.new_ticket("The mock of %s" % key)
        self.set("tickets", t["id"], number=number, key=key, team_key="SPUD-%03d" % number, created_at=team["created_at"])
        ids = {m["lineage"]: self.insert_member(t["id"], **m) for m in team["members"]}
        self.set("tickets", t["id"], lead_id=ids[team["lead"]])

    def test_spd_006_a_claude_code_guide_contractor(self):
        self.seed("SPD-006")
        self.assertEqual("## Team\n" + self.team("SPD-006"), FIXTURE["mocks"]["SPD-006"])

    def test_spd_015_usage_recorded(self):
        self.seed("SPD-015")
        self.assertEqual("## Team\n" + self.team("SPD-015"), FIXTURE["mocks"]["SPD-015"])


# =============================================================================
# Section 8.2: the importer
# =============================================================================


TICKET_ONE = """---
id: SPD-001
title: "Imported card"
priority: P2
status: done
origin: owner
lead: "[[SPUD-001/Russet]]"
created: 2026-09-01
tags: [ticket]
---
# SPD-001 — Imported card

## Brief
A brief.

## Size, persona and model decision
Small.

## Team
| Member | ID | Persona | Model | Status | Run | Tokens | Tools |
|---|---|---|---|---|---|---|---|
| [[SPUD-001/Russet\\|Russet]] | 01 | scout | haiku | done | 10:00 → 11:00 | — | — |
| ↳ [[SPUD-001/Yukon\\|Yukon]] | 01.01 | scout | haiku | done | 10:00 → 11:00 · 35 min | 161k out · 2.9M in · 44.3M cached | 105 |
| ↳ [[SPUD-001/Kennebec\\|Kennebec]] | 01.02 | scout | haiku | done | 10:00 → 11:00 · <1 min | — | 3 |

- [[SPUD-001/Russet|Russet]] (01, scout, haiku) — Built the importer's `TEAM_LINE` reader.
  - [[SPUD-001/Yukon|Yukon]] (01.01, scout, haiku) — Wrote the fixture | with a pipe.
  - [[SPUD-001/Yukon|Yukon]] (01.01, scout, haiku) — A second line for Yukon, ignored.
  - [[SPUD-001/Kennebec|Kennebec]] (01.02, scout, haiku)
- [[SPUD-002/Kennebec|Kennebec]] (01, scout, haiku) — Another team's Kennebec, ignored.

![[Fleet.base#Team]]

## Handoffs

## Proposals received

## Outcome
"""

TICKET_TWO = """---
id: SPD-002
title: "Another team"
priority: P3
status: done
origin: owner
lead: "[[SPUD-002/Kennebec]]"
created: 2026-09-01
tags: [ticket]
---
# SPD-002 — Another team

## Brief
Another brief.

## Size, persona and model decision
Small.

## Team
- [[SPUD-002/Kennebec|Kennebec]] (01, scout, haiku)

## Handoffs

## Proposals received

## Outcome
"""

MEMBER = """---
id: "{lineage}"
name: {name}
persona: scout
model: haiku
parent: "{parent}"
ticket: "[[{ticket}]]"
project: spud
status: done
spawned: 2026-09-01T10:00
finished: 2026-09-01T11:00
{usage}tags: [spudagent]
---
# {name} ({lineage}, scout) — {ticket}

## Brief
Do it.

## Log

## Sub-agents
{subagents}
## Ticket proposals

## Result

## Outcome
"""

YUKON_USAGE = "duration_ms: 2127776\ntool_uses: 105\ntokens_out: 160812\ntokens_in: 2888805\ntokens_cached: 44345065\n"


class TeamImportTest(InProcessCase):  # SPD-242: the CLI in this process, from the fixture's home (hookcase.InProcessCase)
    def write_tree(self, yukon_usage=YUKON_USAGE):
        root = self.home.path / "corpus"
        tickets = root / "ledger" / "tickets"
        tickets.mkdir(parents=True, exist_ok=True)
        (tickets / "SPD-001.md").write_text(TICKET_ONE, encoding="utf-8")
        (tickets / "SPD-002.md").write_text(TICKET_TWO, encoding="utf-8")
        members = [
            ("SPD-001", "01", "Russet", "[[Spud]]", "", "- [[SPUD-001/Yukon|Yukon]] (01.01, scout, haiku)\n- [[SPUD-001/Kennebec|Kennebec]] (01.02, scout, haiku)\n"),
            ("SPD-001", "01.01", "Yukon", "[[SPUD-001/Russet]]", yukon_usage, ""),
            ("SPD-001", "01.02", "Kennebec", "[[SPUD-001/Russet]]", "duration_ms: 4791\ntool_uses: 3\n", ""),
            ("SPD-002", "01", "Kennebec", "[[Spud]]", "", ""),
        ]
        for ticket, lineage, name, parent, usage, subagents in members:
            folder = root / "ledger" / "teams" / ticket.replace("SPD", "SPUD")
            folder.mkdir(parents=True, exist_ok=True)
            note = MEMBER.format(ticket=ticket, lineage=lineage, name=name, parent=parent, usage=usage, subagents=subagents)
            (folder / ("%s.md" % name)).write_text(note, encoding="utf-8")
        return root

    def test_tree_line_suffixes_become_summaries_and_no_team_prose_is_stored(self):  # case 3
        out = self.home.json("import", self.write_tree())
        self.assertEqual(out["prose_sections"], 0)
        rows = self.home.rows("SELECT t.key, m.name, m.summary FROM members m JOIN tickets t ON t.id = m.ticket_id ORDER BY t.key, m.lineage")
        self.assertEqual(
            [(r["key"], r["name"], r["summary"]) for r in rows],
            [
                ("SPD-001", "Russet", "Built the importer's `TEAM_LINE` reader."),
                ("SPD-001", "Yukon", "Wrote the fixture | with a pipe."),  # the second line leaves the set summary alone
                ("SPD-001", "Kennebec", None),  # a line without the suffix; SPUD-002/Kennebec's line is another team's
                ("SPD-002", "Kennebec", None),  # another ticket's member named on SPD-001's card
            ],
        )
        self.assertEqual(self.home.rows("SELECT * FROM imported_sections"), [])
        derived = {r["key"]: json.loads(r["data"])["derived"] for r in self.home.rows(
            "SELECT t.key, e.data FROM events e JOIN tickets t ON t.id = e.ticket_id WHERE e.kind = 'import' AND e.member_id IS NULL")}
        self.assertEqual(derived, {"SPD-001": {"updated_at": "created", "members.summary": "Team section"}, "SPD-002": {"updated_at": "created"}})

    def test_usage_keys_are_read_back_and_render_the_same(self):  # section 8.2, step 3
        root = self.write_tree()
        self.home.json("import", root)
        rows = {r["name"]: r for r in self.home.rows(
            "SELECT m.name, m.duration_ms, m.tool_uses, m.total_tokens, m.usage_json FROM members m JOIN tickets t ON t.id = m.ticket_id WHERE t.key = 'SPD-001'")}
        yukon = rows["Yukon"]
        self.assertEqual((yukon["duration_ms"], yukon["tool_uses"], yukon["total_tokens"]), (2127776, 105, 47394682))
        self.assertEqual(json.loads(yukon["usage_json"]), {"source": "transcript", "imported": True, "usage": {
            "input_tokens": 2888805, "output_tokens": 160812, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 44345065}})
        self.assertEqual(tuple(rows["Kennebec"][k] for k in ("duration_ms", "tool_uses", "total_tokens", "usage_json")), (4791, 3, None, None))
        self.assertEqual(tuple(rows["Russet"][k] for k in ("duration_ms", "tool_uses", "total_tokens", "usage_json")), (None, None, None, None))
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        for name in ("Russet", "Yukon", "Kennebec"):
            rel = "ledger/teams/SPUD-001/%s.md" % name
            self.assertEqual(frontmatter((out / rel).read_text(encoding="utf-8")), frontmatter((root / rel).read_text(encoding="utf-8")), rel)
        rows = {cells(r)[1]: cells(r) for r in table_rows(team_section((out / "ledger" / "tickets" / "SPD-001.md").read_text(encoding="utf-8")))}
        self.assertEqual(rows["01.01"][6:], ["161k out · 2.9M in · 44.3M cached", "—", "105"])  # an imported sum without cost_usd has no cost
        self.assertEqual(rows["01.02"][5:], ["10:00 → 11:00 · <1 min", "—", "—", "3"])

    def test_a_usage_key_that_is_not_a_non_negative_integer_is_refused(self):  # section 8.2, step 3
        cases = [("tokens_in", value) for value in ("-1", "1.5", '""', "", "12abc", "[1, 2]", "１２", "9223372036854775808")]
        # past a 64-bit INTEGER column: a duration has no token sum to catch it later
        cases += [("duration_ms", "9223372036854775808"), ("tool_uses", "1.5")]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                usage = re.sub(r"^%s: .*$" % key, lambda _: "%s: %s" % (key, value), YUKON_USAGE, flags=re.M)
                self.assertNotEqual(usage, YUKON_USAGE)
                proc = self.home.run("import", self.write_tree(yukon_usage=usage), check=False)
                self.assertEqual(proc.returncode, EXIT_ERROR, proc.stdout)
                self.assertIn("ledger/teams/SPUD-001/Yukon.md: %s " % key, proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertEqual(self.home.scalar("SELECT count(*) FROM tickets"), 0)


# =============================================================================
# The vectors, against bin/spud's functions
# =============================================================================


def worked_on_row(summary=None, result=None, return_text=None):
    return {"summary": summary, "result": result, "return_text": return_text}


def section_clauses(last):
    return ", ".join("the `section_%02d` renderer" % n for n in range(1, last + 1))


WORKED_ON_VECTORS = [
    # section 3.6
    ("pipe and newline", worked_on_row(summary="Split `a|b` parsing and the a | b case\nacross two lines."), "Split `a|b` parsing and the a | b case across two lines."),
    ("a bullet-list summary", worked_on_row(summary="- first\n- second", result="Built the Team card renderer. Tests green."), "Built the Team card renderer. Tests green."),
    ("comment, status line, list, stop label, prose", worked_on_row(result="<!-- template -->\nProduced on the worktree branch x, uncommitted.\n- item one\n\n**Verified.** 12 tests.\n\nThe parser now reads block lists."), "The parser now reads block lists."),
    ("only status lines and a handle", worked_on_row(return_text="Nothing further is needed.\n\n**SPUD-015/Pompadour (01, engineer) — done.**\n\n1. an item"), None),
    ("a label with a status parenthetical", worked_on_row(result="**Produced** (branch `worktree-x`, uncommitted): `bin/spud` now holds a member once."), "`bin/spud` now holds a member once."),
    ("plain stop labels, a label introducing a list", worked_on_row(result="Verified: 234 tests.\n\nLeft: nothing.\n\nDecisions the brief left open:\n\n- one\n\nMoved the card rules into one function."), "Moved the card rules into one function."),
    ("a setext heading", worked_on_row(result="Summary\n-------\nBuilt the card renderer and its tests."), "Built the card renderer and its tests."),
    ("a setext heading of words, underlined with =", worked_on_row(result="The Team card, in short\n=======================\nBuilt the card renderer and its tests."), "Built the card renderer and its tests."),
    ("a status word first, even in a summary", worked_on_row(summary="Done. Built X for the card.", return_text="Built the card renderer."), "Built the card renderer."),
    ("a trailing backslash", worked_on_row(summary="Ends with a hard break\\"), "Ends with a hard break"),
    ("29 clauses", worked_on_row(summary="Renders " + section_clauses(29) + "."), "Renders " + section_clauses(14) + ", the…"),
    ("a first sentence over 400", worked_on_row(summary="Built a " + "renderers " * 45 + "for the card. Second sentence."), "Built a " + "renderers " * 38 + "renderers…"),
    ("two short sentences", worked_on_row(summary="Built A. Built B."), "Built A. Built B."),
    ("e.g.", worked_on_row(summary="Wrote the history, e.g. the 1570 arrival, and " + "more detail " * 35 + "at the end. Second sentence here."),
     "Wrote the history, e.g. the 1570 arrival, and " + "more detail " * 29 + "more…"),
    # one per block kind and rule, derived from sections 3.2 and 3.3
    ("a fence", worked_on_row(result="```\ncode here is long enough\n```\n\nWrote the fence parser."), "Wrote the fence parser."),
    ("an ATX heading", worked_on_row(result="# Result\n\nWrote the heading parser."), "Wrote the heading parser."),
    ("a quote", worked_on_row(result="> quoted words here now\n\nWrote the quote parser."), "Wrote the quote parser."),
    ("a table", worked_on_row(result="| a | b |\n|---|---|\n\nWrote the table parser."), "Wrote the table parser."),
    ("a thematic break", worked_on_row(result="* * *\nWrote the rule parser."), "Wrote the rule parser."),
    ("bold without punctuation is emphasis", worked_on_row(summary="**Important** work landed in the renderer."), "**Important** work landed in the renderer."),
    ("a stop label followed by more words", worked_on_row(summary="**Open questions for Spud:** none.", result="Wrote the card."), "Wrote the card."),
    ("a list item ends a paragraph", worked_on_row(summary="Wrote the parser\n- a list item"), "Wrote the parser"),
    ("a quote ends a paragraph", worked_on_row(summary="Wrote the parser\n> a quote line"), "Wrote the parser"),
    ("a comment across lines", worked_on_row(result="<!-- a\nmulti-line comment -->\nWrote it all down."), "Wrote it all down."),
    ("CRLF", worked_on_row(result="Wrote the parser\r\nfor Windows files."), "Wrote the parser for Windows files."),
    ("rule b: where the work sits", worked_on_row(summary="Committed in the branch `x` with tests green.", result="Wrote X for the card."), "Wrote X for the card."),
    ("rule c: a member handle", worked_on_row(summary="SPUD-008/Rooster (01.01, reviewer) reviewed the hooks.", result="Reviewed the hooks."), "Reviewed the hooks."),
    ("rule e: links are not prose", worked_on_row(summary="See [[SPD-010]] and [the spec](docs/x.md).", result="Wrote the spec."), "Wrote the spec."),
    ("a status word needs a word boundary", worked_on_row(summary="Readying the renderer for the card."), "Readying the renderer for the card."),
    # index 399 falls inside the wikilink, after the space in its alias: a cut at the last space would end inside it
    ("the cut never ends inside a wikilink", worked_on_row(summary="Linked " + "word " * 75 + "[[SPD-010|the ticket]] and more words."), "Linked " + "word " * 74 + "word…"),
    ("a sentence end inside a code span is none", worked_on_row(summary="Wrote `x. Y` for " + "the card " * 45 + "renderer. Next one."), "Wrote `x. Y` for " + "the card " * 42 + "the…"),
    ("nothing recorded", worked_on_row(), None),
]


class VectorTest(unittest.TestCase):
    def test_humanize(self):  # section 2
        table = [(0, "0"), (999, "999"), (1000, "1.0k"), (4416, "4.4k"), (9949, "9.9k"), (9950, "10k"), (160812, "161k"),
                 (999499, "999k"), (999500, "1.0M"), (2888805, "2.9M"), (44345065, "44.3M"), (999949999, "999.9M"),
                 (999950000, "1.0B"), (1234567890, "1.2B")]
        self.assertEqual([(n, spud.humanize(n)) for n, _ in table], table)

    def test_run_duration(self):  # section 1.3
        table = [(None, None), (0, "<1 min"), (29999, "<1 min"), (30000, "1 min"), (2127776, "35 min"), (1840000, "31 min"),
                 (2700000, "45 min"), (3569999, "59 min"), (3570000, "1 h"), (3630000, "1 h 1 min"), (7170000, "2 h"), (8130000, "2 h 16 min")]
        self.assertEqual([(ms, spud.run_duration(ms)) for ms, _ in table], table)

    def test_run_cell(self):  # section 1.3
        def row(spawned=None, stopped=None, finished=None, duration=None):
            return {"spawned_at": spawned, "stopped_at": stopped, "finished_at": finished, "duration_ms": duration}

        created = "2026-09-12T19:00:00-07:00"
        table = [
            (row("2026-09-12T20:01:23-07:00", "2026-09-12T20:36:51-07:00", "2026-09-12T20:41:50-07:00", 2127776), created, "20:01 → 20:36 · 35 min"),
            (row("2026-09-12T12:25", None, "2026-09-12T12:31"), "2026-09-12", "12:25 → 12:31"),
            (row("2026-09-12T00:00", None, "2026-09-12T13:00"), "2026-09-12", "—"),
            (row("2026-09-12T20:01:23-07:00"), created, "20:01 →"),
            (row("2026-09-12T20:01:23-07:00", "2026-09-12T20:36:51-07:00", None, 2127776), created, "20:01 → 20:36 · 35 min"),
            (row(), created, "—"),
            (row(None, None, "2026-09-12T20:05:00-07:00"), created, "—"),
            (row("2026-09-13T09:05:00-07:00", "2026-09-13T09:50:12-07:00", "2026-09-13T09:55:00-07:00", 2700000), created, "2026-09-13 09:05 → 09:50 · 45 min"),
            (row("2026-09-12T23:50:00-07:00", "2026-09-13T00:20:40-07:00", "2026-09-13T00:25:00-07:00", 1840000), created, "23:50 → 2026-09-13 00:20 · 31 min"),
            (row(""), created, "—"),
            (row("2026-09-12"), created, "—"),
            (row("2026-09-12T12:25", "", "2026-09-12"), "2026-09-12", "12:25 →"),
            (row("2026-09-12T12:25", "2026-09-12T00:00", "2026-09-12T12:31"), "2026-09-12", "12:25 → 12:31"),
            (row("2026-09-12T12:25", None, None, 0), "2026-09-12", "12:25 → · <1 min"),
        ]
        self.assertEqual([spud.run_cell(m, c) for m, c, _ in table], [want for _, _, want in table])

    def test_token_counts(self):  # section 2
        def usage(**figures):
            return json.dumps({"source": "transcript", "messages": 3, "usage": figures})

        table = [
            (usage(input_tokens=4416, output_tokens=160812, cache_creation_input_tokens=2884389, cache_read_input_tokens=44345065), {"out": 160812, "in": 2888805, "cached": 44345065}),
            (usage(output_tokens=5), {"out": 5, "in": 0, "cached": 0}),
            (usage(output_tokens=True), None),
            (usage(output_tokens=1.5), None),
            (usage(input_tokens=-1), None),
            (usage(output_tokens="5"), None),
            (json.dumps({"source": "transcript", "usage": [5]}), None),
            (json.dumps({"source": "transcript"}), None),
            (json.dumps({"source": "PostToolUse", "usage": {"output_tokens": 40}}), None),
            (json.dumps({"source": "PostToolUse", "completion": {"usage": {"output_tokens": 40}, "totalTokens": 40}}), None),  # a completion alone (SPD-021)
            (json.dumps({"source": "transcript", "messages": 2, "usage": {"output_tokens": 5}, "completion": {"usage": {"output_tokens": 40}, "totalTokens": 40}}),
             {"out": 5, "in": 0, "cached": 0}),  # the sum beside a completion: never the completion's figures
            (json.dumps([1]), None),
            ("not json", None),
            ("", None),
            (None, None),
        ]
        self.assertEqual([spud.token_counts(u) for u, _ in table], [want for _, want in table])

    def test_worked_on(self):  # section 3
        for name, row, want in WORKED_ON_VECTORS:
            with self.subTest(name):
                self.assertEqual(spud.worked_on(row), want)


if __name__ == "__main__":
    unittest.main()
