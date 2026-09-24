"""Bulk import of a small synthetic markdown-v0 tree: the derived-timestamp rule
for a member whose file carries no spawned time, and the import events."""

import json
import unittest

from helpers import EXIT_ERROR, Home, SpudTestCase

TICKET = """---
id: SPD-001
title: "Synthetic"
priority: P2
status: active
origin: owner
project: spud
lead: "[[SPUD-001/Russet]]"
created: 2026-09-01
tags: [ticket]
---
# SPD-001 — Synthetic

## Brief
A brief.

## Size, persona and model decision
Small.

## Team
- [[SPUD-001/Russet|Russet]] (01, scout, haiku)

## Handoffs

## Proposals received

## Outcome
"""

MEMBER = """---
id: "01"
name: Russet
persona: scout
model: haiku
parent: "[[Spud]]"
ticket: "[[SPD-001]]"
project: spud
status: active
spawned: ""
finished: ""
tags: [spudagent]
---
# Russet (01, scout) — SPD-001

## Brief
Do it.

## Log

## Sub-agents

## Ticket proposals

## Result

## Outcome
"""


# SPD-096: a ticket exported while parked carries the two properties that qualify the status, right after it.
PARKED_TICKET = """---
id: SPD-002
title: "Publish the label"
priority: P2
status: parked
parked_until: 2026-10-16
parked_reason: "App Store approval of iOS 1.0"
origin: owner
project: spud
proposed_by: ""
lead: ""
created: 2026-09-14
tags: [ticket]
---
# SPD-002 — Publish the label

## Brief
Waiting on the store.

## Size, persona and model decision

## Team

## Handoffs

## Proposals received

## Outcome
"""


class SyntheticImportTest(SpudTestCase):
    def write_tree(self):
        root = self.home.path / "corpus"
        (root / "ledger" / "tickets").mkdir(parents=True)
        (root / "ledger" / "teams" / "SPUD-001").mkdir(parents=True)
        (root / "ledger" / "tickets" / "SPD-001.md").write_text(TICKET, encoding="utf-8")
        (root / "ledger" / "teams" / "SPUD-001" / "Russet.md").write_text(MEMBER, encoding="utf-8")
        return root

    def test_member_without_spawned_takes_the_tickets_created_and_says_so(self):
        root = self.write_tree()
        out = self.home.json("import", root)
        self.assertEqual((out["tickets"], out["members"], out["prose_sections"]), (1, 1, 0))
        row = self.home.rows("SELECT planned_at, spawned_at, finished_at, status FROM members WHERE name = 'Russet'")[0]
        self.assertEqual(row, {"planned_at": "2026-09-01", "spawned_at": None, "finished_at": None, "status": "active"})
        data = json.loads(self.home.scalar("SELECT data FROM events WHERE kind = 'import' AND member_id IS NOT NULL"))
        self.assertEqual(data["source"], "ledger/teams/SPUD-001/Russet.md")
        self.assertEqual(data["derived"], {"planned_at": "ticket.created"})
        self.assertEqual(self.home.json("ticket", "show", "SPD-001")["ticket"]["lead"], "SPUD-001/Russet")

    def test_round_trip_of_the_synthetic_tree(self):
        root = self.write_tree()
        self.home.json("import", root)
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        from helpers import normalize_markdown, split_team_section, team_section_problems

        for rel in ("ledger/tickets/SPD-001.md", "ledger/teams/SPUD-001/Russet.md"):
            want, want_team = split_team_section((root / rel).read_text(encoding="utf-8"))
            got, got_team = split_team_section((out / rel).read_text(encoding="utf-8"))
            self.assertEqual(normalize_markdown(got), normalize_markdown(want), rel)
            self.assertEqual(got_team is None, want_team is None, rel)
            if want_team is not None:
                # ## Team is generated from the members table (SPD-010): compared by the spec's rule
                self.assertEqual(team_section_problems(want_team, got_team), [], rel)


    def test_a_parked_ticket_round_trips_and_one_without_a_reason_is_refused(self):
        from helpers import normalize_markdown

        root = self.write_tree()
        (root / "ledger" / "tickets" / "SPD-002.md").write_text(PARKED_TICKET, encoding="utf-8")
        self.assertEqual(self.home.json("import", root)["tickets"], 2)
        d = self.home.json("ticket", "show", "SPD-002")["ticket"]
        self.assertEqual((d["status"], d["parked_until"], d["parked_reason"]),
                         ("parked", "2026-10-16", "App Store approval of iOS 1.0"))
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        self.assertEqual(normalize_markdown((out / "ledger" / "tickets" / "SPD-002.md").read_text(encoding="utf-8")),
                         normalize_markdown(PARKED_TICKET))
        # the two keys are the template's order while parked, so the file needs no layout of its own
        self.assertIsNone(self.home.scalar("SELECT layout FROM tickets WHERE key = 'SPD-002'"))
        # an export that says parked with no reason is refused by the CHECK of migration 0003_parked
        bad = self.home.path / "corpus-bad"
        (bad / "ledger" / "tickets").mkdir(parents=True)
        (bad / "ledger" / "tickets" / "SPD-003.md").write_text(
            PARKED_TICKET.replace("SPD-002", "SPD-003").replace('parked_reason: "App Store approval of iOS 1.0"\n', ""), encoding="utf-8")
        proc = self.home.run("import", bad, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CHECK", proc.stderr)
        self.assertIsNone(self.home.scalar("SELECT id FROM tickets WHERE key = 'SPD-003'"))

    def test_a_members_effort_round_trips_and_one_outside_the_levels_is_refused(self):
        # SPD-222: the note carries effort after model when the row has one; an import reads it back
        from helpers import normalize_markdown

        root = self.write_tree()
        opus = MEMBER.replace("persona: scout\nmodel: haiku\n", "persona: engineer\nmodel: opus\neffort: high\n").replace("(01, scout)", "(01, engineer)")
        (root / "ledger" / "teams" / "SPUD-001" / "Russet.md").write_text(opus, encoding="utf-8")
        self.home.json("import", root)
        self.assertEqual(self.home.json("member", "show", "SPUD-001/Russet")["member"]["effort"], "high")
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        self.assertEqual(normalize_markdown((out / "ledger" / "teams" / "SPUD-001" / "Russet.md").read_text(encoding="utf-8")),
                         normalize_markdown(opus))
        bad = self.home.path / "corpus-bad"
        (bad / "ledger" / "tickets").mkdir(parents=True)
        (bad / "ledger" / "teams" / "SPUD-002").mkdir(parents=True)
        (bad / "ledger" / "tickets" / "SPD-002.md").write_text(TICKET.replace("SPD-001", "SPD-002").replace("SPUD-001", "SPUD-002"), encoding="utf-8")
        (bad / "ledger" / "teams" / "SPUD-002" / "Russet.md").write_text(
            opus.replace("SPD-001", "SPD-002").replace("effort: high", "effort: extreme"), encoding="utf-8")
        proc = self.home.run("import", bad, check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("effort 'extreme' is not one of low, medium, high, xhigh, max", proc.stderr)

    def test_a_note_rendered_before_0006_imports_its_origin_as_owner(self):
        # SPD-160: an export from before migration 0006_owner_origin says origin eric; it lands as owner and renders so
        root = self.write_tree()
        (root / "ledger" / "tickets" / "SPD-001.md").write_text(TICKET.replace("origin: owner", "origin: eric"), encoding="utf-8")
        self.home.json("import", root)
        self.assertEqual(self.home.scalar("SELECT origin FROM tickets WHERE key = 'SPD-001'"), "owner")
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        self.assertIn("\norigin: owner\n", (out / "ledger" / "tickets" / "SPD-001.md").read_text(encoding="utf-8"))
        bad = self.home.path / "corpus-bad"
        (bad / "ledger" / "tickets").mkdir(parents=True)
        (bad / "ledger" / "tickets" / "SPD-003.md").write_text(TICKET.replace("SPD-001", "SPD-003").replace("origin: owner", "origin: human"), encoding="utf-8")
        proc = self.home.run("import", bad, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("origin outside the schema's values", proc.stderr)


# SPD-078: SPD-076's review case G1 (docs/spikes/spd-076/review.md) is a handoff whose what is `x`, a blank line, then two
# more lines; the others are a line of text after a path-less multi-line row, a what that ends in a newline, and one
# with a path, which the render writes after the what's last line.
HANDOFF_G1 = "x\n\nsecond line\nthird line"
HANDOFF_TAIL = "ends with a newline\n"
HANDOFF_PATH = "drafted\n  indented already"


class MultiLineRowRoundTripTest(SpudTestCase):
    """SPD-078: a handoff whose what holds newlines renders under ## Handoffs as one row whose later lines carry the
    two-space continuation indent a Log entry and a proposal's Why and Evidence carry, and a render imported into a
    fresh home comes back as the same rows (no section kept as prose) and renders again byte for byte.  SPD-236: a
    multi-line member Log entry, by the same rule."""

    def setUp(self):
        super().setUp()
        self.t = self.new_ticket("Rows")
        self.lead = self.new_member(self.t["key"], name="Russet", persona="engineer", model="opus")
        self.child = self.new_member(self.t["key"], actor=self.lead["ref"], name="Yukon")

    def handoff(self, frm, to, what, path=None):
        args = ["handoff", "add", "--ticket", self.t["key"], "--from", frm, "--to", to, "--what", what]
        self.home.json(*(args + (["--path", path] if path else [])), actor="spud")

    def round_trip(self):
        """Render, import the render into a fresh home, render again: (the first render's root, the other home), with
        every ticket and member note asserted byte-identical across the two renders."""
        first = self.home.path / "first"
        self.home.json("render", "--out", first)
        other = Home()
        self.addCleanup(other.cleanup)
        other.init()
        other.json("import", first / "ledger")
        second = other.path / "second"
        other.json("render", "--out", second)
        notes = sorted(p.relative_to(first) for p in list(first.glob("ledger/tickets/*.md")) + list(first.glob("ledger/teams/*/*.md")))
        self.assertTrue(notes)
        for rel in notes:
            self.assertEqual((second / rel).read_text(encoding="utf-8"), (first / rel).read_text(encoding="utf-8"), rel)
        return first, other

    @staticmethod
    def handoff_rows(home):
        return home.rows(
            "SELECT f.name AS frm, t.name AS too, h.what || COALESCE(' (`' || h.path || '`)', '') AS what FROM handoffs h"
            " LEFT JOIN members f ON f.id = h.from_member_id LEFT JOIN members t ON t.id = h.to_member_id ORDER BY h.id")

    def test_a_multi_line_handoff_renders_as_one_row_and_imports_back_as_it(self):
        self.handoff(self.lead["ref"], "spud", HANDOFF_G1)
        self.handoff(self.child["ref"], self.lead["ref"], "one line, after it.")
        self.handoff(self.lead["ref"], self.child["ref"], HANDOFF_TAIL)
        self.handoff("spud", self.lead["ref"], HANDOFF_PATH, path="docs/x.md")
        first, other = self.round_trip()
        text = (first / "ledger" / "tickets" / "SPD-001.md").read_text(encoding="utf-8")
        body = text.split("## Handoffs\n", 1)[1].split("\n\n## ", 1)[0]
        lines = body.split("\n")
        self.assertEqual(len(lines), 9, body)
        self.assertRegex(lines[0], r"^- \d{4}-\d{2}-\d{2} — Russet \(01\) → Spud: x$")
        self.assertEqual(lines[1:4], ["  ", "  second line", "  third line"])
        self.assertRegex(lines[4], r"^- \d{4}-\d{2}-\d{2} — Yukon \(01\.01\) → Russet \(01\): one line, after it\.$")
        self.assertRegex(lines[5], r"^- \d{4}-\d{2}-\d{2} — Russet \(01\) → Yukon \(01\.01\): ends with a newline$")
        self.assertEqual(lines[6], "  ")
        self.assertRegex(lines[7], r"^- \d{4}-\d{2}-\d{2} — Spud → Russet \(01\): drafted$")
        self.assertEqual(lines[8], "    indented already (`docs/x.md`)")
        # the rows, not a section of prose: the import reads each row back whole
        self.assertEqual(other.rows("SELECT entity, section FROM imported_sections"), [])
        self.assertEqual(self.handoff_rows(other), self.handoff_rows(self.home))
        self.assertEqual([r["what"] for r in self.handoff_rows(other)],
                         [HANDOFF_G1, "one line, after it.", HANDOFF_TAIL, HANDOFF_PATH + " (`docs/x.md`)"])

    def test_a_blank_line_an_editor_emptied_still_belongs_to_its_handoff(self):
        # an editor that trims trailing whitespace turns the render's `  ` into an empty line; the row still reads whole
        self.handoff(self.lead["ref"], "spud", HANDOFF_G1)
        self.handoff(self.child["ref"], self.lead["ref"], "one line, after it.")
        first = self.home.path / "first"
        self.home.json("render", "--out", first)
        path = first / "ledger" / "tickets" / "SPD-001.md"
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(": x\n  \n  second line\n"), 1)
        path.write_text(text.replace(": x\n  \n  second line\n", ": x\n\n  second line\n"), encoding="utf-8")
        other = Home()
        self.addCleanup(other.cleanup)
        other.init()
        other.json("import", first / "ledger")
        self.assertEqual(other.rows("SELECT entity, section FROM imported_sections"), [])
        self.assertEqual(self.handoff_rows(other), self.handoff_rows(self.home))

    def test_an_indented_line_under_an_unknown_party_stays_with_its_line(self):
        # a handoff line naming no member of the ticket is kept whole as an unparsed row, its continuation with it
        root = self.home.path / "corpus"
        (root / "ledger" / "tickets").mkdir(parents=True)
        section = "- 2026-09-01 — Kestrel (01) → Spud: merged\n  and a second line\nA line of prose.\n  indented prose"
        (root / "ledger" / "tickets" / "SPD-001.md").write_text(TICKET.replace("## Handoffs\n", "## Handoffs\n" + section + "\n"), encoding="utf-8")
        (root / "ledger" / "teams" / "SPUD-001").mkdir(parents=True)
        (root / "ledger" / "teams" / "SPUD-001" / "Russet.md").write_text(MEMBER, encoding="utf-8")
        other = Home()
        self.addCleanup(other.cleanup)
        other.init()
        other.json("import", root)
        self.assertEqual(other.rows("SELECT from_member_id, to_member_id, what FROM handoffs ORDER BY id"), [
            {"from_member_id": None, "to_member_id": None, "what": "- 2026-09-01 — Kestrel (01) → Spud: merged\n  and a second line"},
            {"from_member_id": None, "to_member_id": None, "what": "A line of prose."},
            {"from_member_id": None, "to_member_id": None, "what": "  indented prose"},
        ])
        self.assertEqual(other.rows("SELECT entity, section FROM imported_sections"), [])
        out = other.path / "out"
        other.json("render", "--out", out)
        self.assertIn("## Handoffs\n" + section + "\n\n## ", (out / "ledger" / "tickets" / "SPD-001.md").read_text(encoding="utf-8"))

    def test_multi_line_proposal_titles_why_evidence_and_reasons_round_trip(self):
        # Ticket proposals (the member's note) and Proposals received (the ticket's): a title, why, evidence and a
        # decision's reason holding newlines, one proposal of each fate
        ref = self.lead["ref"]
        self.home.json("member", "start", ref, actor="spud")

        def file(title):
            return self.home.json("proposal", "file", "--title", title, "--why", "why one\n\nwhy two",
                                  "--evidence", "ev one\nev two", "--priority", "P2", actor=ref)["proposal"]["id"]

        declined, created, still_open = file("Declined\ntitle"), file("Created title"), file("Open\n\ntitle")
        self.home.json("proposal", "decide", str(declined), "--decision", "decline", "--reason", "not now\n\nmaybe later", actor="spud")
        self.home.json("proposal", "decide", str(created), "--decision", "create", "--priority", "P3", actor="spud")
        first, other = self.round_trip()
        member = (first / "ledger" / "teams" / "SPUD-001" / "Russet.md").read_text(encoding="utf-8")
        ticket = (first / "ledger" / "tickets" / "SPD-001.md").read_text(encoding="utf-8")
        self.assertIn("- **Declined\ntitle** — suggested P2; declined by Spud: not now\n\nmaybe later.\n  Why: why one\n  \n  why two\n", member)
        self.assertIn("- **Declined\ntitle** — origin [[SPUD-001/Russet]] — decision: declined: not now\n\nmaybe later.", ticket)
        # the import rebuilds a proposal only from the ticket it created (link_proposal); the rest come back as prose
        self.assertEqual(other.rows("SELECT title, status FROM proposals"), [{"title": "Created title", "status": "created"}])
        self.assertEqual(sorted((r["entity"], r["section"]) for r in other.rows("SELECT entity, section FROM imported_sections")),
                         [("member", "Ticket proposals"), ("ticket", "Proposals received")])

    # SPD-236: the member's Log reads back by the same rule, core/markdown.continued_rows.

    @staticmethod
    def log_rows(home):
        return home.rows("SELECT m.name, e.body FROM events e JOIN members m ON m.id = e.member_id"
                         " WHERE e.kind = 'member.log' ORDER BY m.name, e.id")

    def test_a_multi_line_log_entry_imports_back_as_its_row(self):
        # the ticket's probe ('a\n\nb\n  c'), an entry after it, one that ends in a newline, and a member whose Log is
        # single-line entries alone, which re-renders byte for byte as it always has
        lead = self.lead["ref"]
        for text in ["started", "a\n\nb\n  c", "one line, after it.", "ends with a newline\n", "last"]:
            self.home.json("member", "log", text, actor=lead)
        for text in ["one", "two"]:
            self.home.json("member", "log", text, actor=self.child["ref"])
        first, other = self.round_trip()
        note = (first / "ledger" / "teams" / "SPUD-001" / "Russet.md").read_text(encoding="utf-8")
        lines = note.split("## Log\n", 1)[1].split("\n\n## ", 1)[0].split("\n")
        self.assertEqual(len(lines), 9, lines)
        self.assertRegex(lines[0], r"^- \d{4}-\d{2}-\d{2}T\d{2}:\d{2} started$")
        self.assertRegex(lines[1], r"^- \d{4}-\d{2}-\d{2}T\d{2}:\d{2} a$")
        self.assertEqual(lines[2:5], ["  ", "  b", "    c"])
        self.assertRegex(lines[6], r"^- \d{4}-\d{2}-\d{2}T\d{2}:\d{2} ends with a newline$")
        self.assertEqual(lines[7], "  ")
        self.assertEqual(other.rows("SELECT entity, section FROM imported_sections"), [])
        self.assertEqual(self.log_rows(other), self.log_rows(self.home))
        self.assertEqual([r["body"] for r in self.log_rows(other) if r["name"] == "Russet"],
                         ["started", "a\n\nb\n  c", "one line, after it.", "ends with a newline\n", "last"])

    def test_a_blank_line_an_editor_emptied_still_belongs_to_its_log_entry(self):
        self.home.json("member", "log", "a\n\nb\n  c", actor=self.lead["ref"])
        self.home.json("member", "log", "after", actor=self.lead["ref"])
        first = self.home.path / "first"
        self.home.json("render", "--out", first)
        path = first / "ledger" / "teams" / "SPUD-001" / "Russet.md"
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(" a\n  \n  b\n"), 1)
        path.write_text(text.replace(" a\n  \n  b\n", " a\n\n  b\n"), encoding="utf-8")
        other = Home()
        self.addCleanup(other.cleanup)
        other.init()
        other.json("import", first / "ledger")
        self.assertEqual(other.rows("SELECT entity, section FROM imported_sections"), [])
        self.assertEqual(self.log_rows(other), self.log_rows(self.home))


if __name__ == "__main__":
    unittest.main()
