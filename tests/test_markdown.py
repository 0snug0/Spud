"""Unit tests for the markdown-v0 parser and emitter inside bin/spud, imported
as a module (bin/spud has no .py suffix), and for SPUD_HOME resolution."""

import os
import tempfile
import unittest
from pathlib import Path

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


class HomeResolutionTest(unittest.TestCase):
    def test_env_var_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, how = spud.resolve_home({"SPUD_HOME": tmp}, Path(spud.__file__))
            self.assertEqual(home, Path(tmp).resolve())
            self.assertEqual(how, "SPUD_HOME")

    def test_git_common_dir_of_the_script_is_the_fallback(self):
        # From the main checkout the home is the repo root; from a worktree it is
        # still the main checkout (the one with a real .git directory), never the
        # worktree, which only has a .git file.
        home, how = spud.resolve_home({}, Path(spud.__file__))
        self.assertEqual(how, "git common dir")
        self.assertTrue((home / ".git").is_dir(), home)
        self.assertTrue((home / "spud.config.json").is_file(), home)


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
