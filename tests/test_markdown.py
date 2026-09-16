"""Unit tests for the markdown-v0 parser and emitter inside bin/spud, imported
as a module (bin/spud has no .py suffix)."""

import os
import unittest

from helpers import load_spud_module

spud = load_spud_module()


class FrontmatterTest(unittest.TestCase):
    def test_parses_the_yaml_subset_the_ledger_uses(self):
        fm = spud.parse_frontmatter(
            [
                "id: SPD-004",
                'title: "Toy doc docs/toy/one-liner.md: one line, \'Potatoes are tubers.\'"',
                'lead: "[[SPUD-004/Kestrel]]"',
                'proposed_by: ""',
                "created: 2026-09-12",
                "tags: [ticket, toy]",
                'quoted: "a \\"b\\" c"',
                "spawned: 2026-09-12T12:37",
            ]
        )
        self.assertEqual(list(fm.keys()), ["id", "title", "lead", "proposed_by", "created", "tags", "quoted", "spawned"])
        self.assertEqual(fm["id"], "SPD-004")
        self.assertEqual(fm["title"], "Toy doc docs/toy/one-liner.md: one line, 'Potatoes are tubers.'")
        self.assertEqual(fm["lead"], "[[SPUD-004/Kestrel]]")
        self.assertEqual(fm["proposed_by"], "")
        self.assertEqual(fm["tags"], ["ticket", "toy"])
        self.assertEqual(fm["quoted"], 'a "b" c')
        self.assertEqual(fm["spawned"], "2026-09-12T12:37")

    def test_emit_round_trips(self):
        pairs = [
            ("id", ("quoted", "01.01")),
            ("name", ("plain", "Elba")),
            ("parent", ("quoted", "[[SPUD-006/Vitelotte]]")),
            ("finished", ("quoted", "")),
            ("tags", ("list", ["spudagent", "contractor"])),
            ("title", ("quoted", 'He said "no"')),
        ]
        text = spud.emit_frontmatter(pairs)
        self.assertEqual(
            text.split("\n"),
            [
                "---",
                'id: "01.01"',
                "name: Elba",
                'parent: "[[SPUD-006/Vitelotte]]"',
                'finished: ""',
                "tags: [spudagent, contractor]",
                'title: "He said \\"no\\""',
                "---",
                "",
            ],
        )
        parsed = spud.parse_frontmatter(text.split("\n")[1:-2])
        self.assertEqual(parsed["title"], 'He said "no"')
        self.assertEqual(parsed["tags"], ["spudagent", "contractor"])

    def test_block_lists_read_like_flow_lists_at_any_indent(self):
        fm = spud.parse_frontmatter(
            [
                "tags:",
                "  - ticket",
                "  - ledger-v1",
                "aliases:",
                "- one",
                '- "two, quoted"',
                "",
                "- 'three'",
                "cssclasses: [wide, dark]",
            ]
        )
        self.assertEqual(fm, {"tags": ["ticket", "ledger-v1"], "aliases": ["one", "two, quoted", "three"], "cssclasses": ["wide", "dark"]})

    def test_an_empty_value_and_both_empty_quotes_read_the_same(self):
        fm = spud.parse_frontmatter(["spawned:", 'finished: ""', "proposed_by: ''", 'id: "01"', "lineage: 01"])
        self.assertEqual((fm["spawned"], fm["finished"], fm["proposed_by"]), ("", "", ""))
        self.assertEqual(fm["id"], fm["lineage"])

    def test_obsidians_rewrite_of_spd_015_reads_as_the_render(self):
        # Event 192: the frontmatter Obsidian wrote over ledger/tickets/SPD-015.md, and the one
        # render_ticket had written there.
        obsidian = [
            "id: SPD-015",
            "title: Hold a lead that returns with its own children unrecorded",
            "priority: P2",
            "status: active",
            "origin: proposal",
            'proposed_by: "[[SPUD-008/Desiree]]"',
            'lead: "[[SPUD-015/Pompadour]]"',
            "created: 2026-09-12",
            "tags:",
            "  - ticket",
            "  - ledger-v1",
        ]
        rendered = [
            "id: SPD-015",
            'title: "Hold a lead that returns with its own children unrecorded"',
            "priority: P2",
            "status: active",
            "origin: proposal",
            'proposed_by: "[[SPUD-008/Desiree]]"',
            'lead: "[[SPUD-015/Pompadour]]"',
            "created: 2026-09-12",
            "tags: [ticket, ledger-v1]",
        ]
        self.assertEqual(spud.parse_frontmatter(obsidian), spud.parse_frontmatter(rendered))

    def test_refuses_what_it_cannot_read(self):
        cases = {
            "a folded value": ["title: >-", "  Hold a lead"],
            "a continuation line": ["title: Hold a lead", "  that returns"],
            "an indented property": ["title: T", "  priority: P2"],
            "an item with no property above it": ["title: T", "- ticket"],
            "items at two indents": ["tags:", "  - ticket", "- ledger-v1"],
            "a list inside a list": ["tags:", "  - [ticket, toy]"],
            "a repeated property": ["priority: P1", "priority: P2"],
            "an unterminated quote": ['title: "Hold a lead'],
            "a line without a colon": ["title"],
        }
        for name, lines in cases.items():
            with self.subTest(name):
                with self.assertRaises(spud.SpudError):
                    spud.parse_frontmatter(lines)


class DocumentTest(unittest.TestCase):
    def test_split_document_keeps_section_order_and_ignores_fenced_headings(self):
        text = "\n".join(
            [
                "---",
                "id: SPD-001",
                "---",
                "# SPD-001 — Title",
                "",
                "## Brief",
                "Text with ### sub heading below",
                "### Sub",
                "```",
                "## not a section",
                "```",
                "",
                "## Team",
                "",
                "## Outcome",
                "Done.",
                "",
            ]
        )
        doc = spud.split_document(text)
        self.assertEqual(doc["frontmatter"]["id"], "SPD-001")
        self.assertEqual(doc["heading"], "SPD-001 — Title")
        self.assertEqual([s[0] for s in doc["sections"]], ["Brief", "Team", "Outcome"])
        self.assertEqual(doc["sections"][0][1], "Text with ### sub heading below\n### Sub\n```\n## not a section\n```")
        self.assertEqual(doc["sections"][1][1], "")
        self.assertEqual(doc["sections"][2][1], "Done.")

    def test_owned_sections_leave_every_other_level_2_line_in_the_prose(self):
        # SPD-076: given the names its layout owns, a note opens a section only at those headings
        brief = ["Brief.", "## Shared context", "Facts.", "", "## Outcome", "A line of the brief.", "```", "## Team", "```"]
        sizing = ["Small.", "", "## Team", "A line of the sizing."]
        team = ["- [[SPUD-001/Russet|Russet]] (01, scout, haiku)", "```"]  # an unbalanced fence hides no heading
        outcome = ["## Outcome", "Done.", "## Brief", "A line of the outcome."]
        lines = ["---", "id: SPD-001", "---", spud.MARKER, "# SPD-001 — T", "", "## Brief"] + brief
        lines += ["", "## Size, persona and model decision"] + sizing + ["", "## Team"] + team
        lines += ["", "## Handoffs", "", "## Proposals received", "", "## Outcome"] + outcome + [""]
        doc = spud.split_document("\n".join(lines), spud.TICKET_SECTIONS)
        self.assertEqual(doc["preamble"], "")
        self.assertEqual(
            doc["sections"],
            [
                ("Brief", "\n".join(brief)),
                ("Size, persona and model decision", "\n".join(sizing)),
                ("Team", "\n".join(team)),
                ("Handoffs", ""),
                ("Proposals received", ""),
                ("Outcome", "\n".join(outcome)),
            ],
        )
        # without the names, markdown-v0 as written by hand: every level-2 line outside a fence opens a section
        legacy = [name for name, _ in spud.split_document("\n".join(lines))["sections"]]
        self.assertEqual(legacy[:4], ["Brief", "Shared context", "Outcome", "Size, persona and model decision"])

    def test_owned_sections_absent_from_the_note_are_skipped_in_layout_order(self):
        body = "\n".join(["## Brief", "Do it.", "## Blocked", "A line of the brief.", "", "## Log", "", "## Result", "## Log", "", "## Outcome", ""])
        doc = spud.split_document("---\nid: \"01\"\n---\n# Russet (01, scout) — SPD-001\n\n" + body, spud.MEMBER_SECTIONS)
        self.assertEqual(
            doc["sections"],
            [("Brief", "Do it.\n## Blocked\nA line of the brief."), ("Log", ""), ("Result", "## Log"), ("Outcome", "")],
        )

    def test_marker_line_after_frontmatter_is_dropped(self):
        text = "---\nid: SPD-001\n---\n%s\n# SPD-001 — T\n\n## Brief\nx\n" % spud.MARKER
        doc = spud.split_document(text)
        self.assertEqual(doc["heading"], "SPD-001 — T")
        self.assertEqual(doc["sections"], [("Brief", "x")])


class LogEntriesTest(unittest.TestCase):
    def test_four_shapes_and_continuations(self):
        text = "\n".join(
            [
                "<!-- the spudagent: dated lines -->",
                "- 2026-09-12 — Read the config.",
                "- 2026-09-12: Called `Agent`. Verbatim tool response:",
                "  > Error: No such tool available: Agent.",
                "2026-09-12T12:38 created the file",
                "",
                "- 2026-09-12T13:27 Started.",
            ]
        )
        entries = spud.parse_log_entries(text)
        self.assertEqual(
            entries,
            [
                ("2026-09-12", "Read the config."),
                ("2026-09-12", "Called `Agent`. Verbatim tool response:\n  > Error: No such tool available: Agent."),
                ("2026-09-12T12:38", "created the file"),
                ("2026-09-12T13:27", "Started."),
            ],
        )

    def test_no_entries_in_a_comment_only_log(self):
        self.assertEqual(spud.parse_log_entries("<!-- the spudagent: dated lines -->"), [])


class HandoffLineTest(unittest.TestCase):
    def test_parses_the_committed_form(self):
        line = "- 2026-09-12 — Kestrel (01) → Huckleberry (01.01): `## History`, drafted to `docs/toy/drafts/history.md` (270 words)."
        self.assertEqual(
            spud.parse_handoff_line(line),
            ("2026-09-12", "Kestrel (01)", "Huckleberry (01.01)", "`## History`, drafted to `docs/toy/drafts/history.md` (270 words)."),
        )
        self.assertEqual(spud.parse_handoff_line("- 2026-09-12 — Kestrel (01) → Spud: merged file.")[2], "Spud")
        self.assertIsNone(spud.parse_handoff_line("Dated lines: who handed what to whom."))


class ReportEntriesTest(unittest.TestCase):
    def test_entries_by_heading_time(self):
        text = "# 2026-09-12\n\n## 12:25 — SPD-001 created and started\n- Ticket: x\n- Next: y\n\n## 12:32 — SPD-001 done\n- Done.\n"
        entries = spud.parse_report_entries(text)
        self.assertEqual(entries, [("12:25", "SPD-001 created and started", "- Ticket: x\n- Next: y"), ("12:32", "SPD-001 done", "- Done.")])


class TimestampTest(unittest.TestCase):
    def test_now_has_seconds_and_a_local_offset(self):
        stamp = spud.now()
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")

    def test_render_forms(self):
        self.assertEqual(spud.fm_date("2026-09-12T14:39:05-07:00"), "2026-09-12")
        self.assertEqual(spud.fm_minute("2026-09-12T14:39:05-07:00"), "2026-09-12T14:39")
        self.assertEqual(spud.fm_minute("2026-09-12T12:41"), "2026-09-12T12:41")
        self.assertEqual(spud.fm_minute(None), "")


if __name__ == "__main__":
    unittest.main()
