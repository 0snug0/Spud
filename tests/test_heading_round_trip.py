"""SPD-076: a note's sections open only at the headings its layout owns, so a ticket's brief, sizing and outcome and a
member's brief, result, blocked question and outcome that hold `## ` lines render, import and render again byte for
byte, and a hand edit of such a note is still seen for what it is: a render conflict (exit 6), then `import --file`
taking the edit as a change to the section it sits in.

A line naming a section the note owns is the one the split cannot always place (Cherie's review, docs/spikes/spd-076/
review.md): the CLI refuses it in every value it stores, and `import --file` refuses a hand edit that adds one.  The
rows below that hold such lines stand for legacy or hand-written text, stored as the CLI no longer would."""

import json
import unittest

from helpers import EXIT_CONFLICT, EXIT_ERROR, Home, SpudTestCase, load_spud_module

spud = load_spud_module()

# The level-2 lines the split must leave in the prose: a name no layout owns, after a line of text as the BAD-036
# briefs have it and after a blank line; a later section's name; the next section's name after a blank line; an
# earlier section's name; and a section's own name right under its heading.
TICKET_BRIEF = "\n".join([
    "The brief.",
    "## Shared context (a heading of the brief's own)",
    "Prose under it.",
    "",
    "## Outcome",
    "Not the ticket's Outcome: a line of the brief.",
])
TICKET_SIZING = "One engineer.\n\n## Team\nNot the Team card: a line of the sizing."
TICKET_OUTCOME = "Done.\n\n## Brief\nNot the Brief: a line of the outcome."
MEMBER_BRIEF = "\n".join([
    "Objective: the thing.",
    "## Shared context (already established; do not redo)",
    "Facts.",
    "",
    "## Log",
    "Not the Log: a line of the brief.",
    "",
    "## Outcome",
    "Not the Outcome either.",
])
MEMBER_RESULT = "## Result\nBuilt it.\n\n## Outcome\nWhat the member expects, not its parent's Outcome."
MEMBER_BLOCKED = "Which option?\n\n## Options\n- a\n- b"
MEMBER_OUTCOME = "Accepted.\n## Brief\nNot the Brief."


class HeadingRoundTripTest(SpudTestCase):
    def setUp(self):
        super().setUp()
        self.t = self.new_ticket("Headings")
        self.lead = self.new_member(self.t["key"], name="Russet", persona="engineer", model="opus")
        self.child = self.new_member(self.t["key"], actor=self.lead["ref"], name="Yukon")
        # the CLI refuses a line naming one of the note's own sections (HeadingRefusalTest), and these values hold
        # such lines: the render and the import read only the rows, so the rows are stored as legacy text has them
        con = self.home.connect()
        try:
            with con:
                con.execute("UPDATE tickets SET brief = ?, sizing = ?, outcome = ?", (TICKET_BRIEF, TICKET_SIZING, TICKET_OUTCOME))
                con.execute("UPDATE members SET brief = ?", (MEMBER_BRIEF,))
                con.execute("UPDATE members SET result = ?, outcome = ? WHERE name = 'Russet'", (MEMBER_RESULT, MEMBER_OUTCOME))
                con.execute("UPDATE members SET blocked = ? WHERE name = 'Yukon'", (MEMBER_BLOCKED,))
        finally:
            con.close()
        self.ticket_path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        self.lead_path = self.home.path / "ledger" / "teams" / "SPUD-001" / "Russet.md"

    def edit(self, path, old, new):
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, old)
        path.write_text(text.replace(old, new), encoding="utf-8")
        return text.replace(old, new)

    def test_the_rendered_notes_import_and_render_again_byte_for_byte(self):
        first = self.home.path / "first"
        self.home.json("render", "--out", first)
        text = (first / "ledger" / "teams" / "SPUD-001" / "Russet.md").read_text(encoding="utf-8")
        self.assertIn("\n## Brief\n" + MEMBER_BRIEF + "\n\n## Log\n", text)  # the prose's headings are in the note
        other = Home()
        self.addCleanup(other.cleanup)
        other.init()
        counts = other.json("import", first / "ledger")
        self.assertEqual((counts["tickets"], counts["members"], counts["prose_sections"]), (1, 2, 0))
        second = other.path / "second"
        other.json("render", "--out", second)
        notes = sorted(p.relative_to(first) for p in list(first.glob("ledger/tickets/*.md")) + list(first.glob("ledger/teams/*/*.md")))
        self.assertEqual(len(notes), 3)
        for rel in notes:
            self.assertEqual((second / rel).read_bytes(), (first / rel).read_bytes(), rel)
        self.assertEqual(
            other.rows("SELECT brief, sizing, outcome, layout FROM tickets"),
            [{"brief": TICKET_BRIEF, "sizing": TICKET_SIZING, "outcome": TICKET_OUTCOME, "layout": None}],
        )
        self.assertEqual(
            other.rows("SELECT name, brief, result, blocked, outcome, layout FROM members ORDER BY lineage"),
            [
                {"name": "Russet", "brief": MEMBER_BRIEF, "result": MEMBER_RESULT, "blocked": None, "outcome": MEMBER_OUTCOME, "layout": None},
                # an empty section imports as an empty value, which renders as the NULL did
                {"name": "Yukon", "brief": MEMBER_BRIEF, "result": "", "blocked": MEMBER_BLOCKED, "outcome": "", "layout": None},
            ],
        )

    def test_a_hand_edit_is_a_conflict_and_import_file_takes_it_as_the_section_it_sits_in(self):
        self.home.json("render")
        # below the brief's own `## Outcome` line: an edit of the Brief, not of the Outcome
        edited = self.edit(self.lead_path, "Not the Outcome either.", "Not the Outcome either, edited.")
        proc = self.home.run("render", check=False)
        self.assertEqual(proc.returncode, EXIT_CONFLICT)
        self.assertIn("ledger/teams/SPUD-001/Russet.md", proc.stderr)
        self.assertEqual(self.lead_path.read_text(encoding="utf-8"), edited)
        out = self.home.json("import", "--file", self.lead_path, actor="spud")
        self.assertEqual(out["changed"], ["brief"])
        shown = self.home.json("member", "show", self.lead["ref"])["member"]
        want_brief = MEMBER_BRIEF.replace("Not the Outcome either.", "Not the Outcome either, edited.")
        self.assertEqual((shown["brief"], shown["result"], shown["outcome"]), (want_brief, MEMBER_RESULT, MEMBER_OUTCOME))
        self.assertNotIn("ledger/teams/SPUD-001/Russet.md", self.home.json("render")["written"])
        self.assertEqual(self.lead_path.read_text(encoding="utf-8"), edited)  # the render of the accepted rows is the file

    def test_a_heading_added_inside_the_prose_is_an_edit_of_that_section(self):
        # headings the note does not own; an owned one added by hand is refused (the next test)
        self.home.json("render")
        self.edit(self.ticket_path, "Not the Brief: a line of the outcome.", "Not the Brief: a line of the outcome.\n\n## Aside\nStill the outcome.")
        self.edit(self.ticket_path, "Prose under it.", "Prose under it.\n## Notes\nStill the brief.")
        out = self.home.json("import", "--file", self.ticket_path, actor="spud")
        self.assertEqual(sorted(out["changed"]), ["brief", "outcome"])
        shown = self.home.json("ticket", "show", self.t["key"])["ticket"]
        self.assertEqual(shown["brief"], TICKET_BRIEF.replace("Prose under it.", "Prose under it.\n## Notes\nStill the brief."))
        self.assertEqual(shown["sizing"], TICKET_SIZING)
        self.assertEqual(shown["outcome"], TICKET_OUTCOME + "\n\n## Aside\nStill the outcome.")
        self.assertEqual(self.home.json("render")["conflicts"], [])

    def test_a_hand_edit_that_adds_a_line_naming_one_of_the_notes_own_sections_is_refused(self):
        self.home.json("render")
        edits = [
            (self.ticket_path, "Prose under it.", "Prose under it.\n## Handoffs\nStill the brief.", "## Brief", "## Handoffs"),
            (self.ticket_path, "Not the Brief: a line of the outcome.", "Not the Brief: a line of the outcome.\n\n## Proposals received\nStill the outcome.",
             "## Outcome", "## Proposals received"),
            # E3 by hand: Sources is a member section at import, though this note's layout does not carry it
            (self.lead_path, "Not the Outcome either.", "Not the Outcome either.\n\n## Sources\n- a link", "## Brief", "## Sources"),
        ]
        for path, old, new, section, line in edits:
            self.edit(path, old, new)
            proc = self.home.run("import", "--file", path, actor="spud", check=False)
            self.assertEqual(proc.returncode, EXIT_ERROR, (line, proc.stderr))
            self.assertIn("%s: %s holds the line `%s`" % (path.relative_to(self.home.path), section, line), proc.stderr)
            self.assertIn("write `### %s`" % line[3:], proc.stderr)
            self.home.json("render", "--discard", path, actor="spud")
        shown = self.home.json("ticket", "show", self.t["key"])["ticket"]
        self.assertEqual((shown["brief"], shown["sizing"], shown["outcome"]), (TICKET_BRIEF, TICKET_SIZING, TICKET_OUTCOME))
        shown = self.home.json("member", "show", self.lead["ref"])["member"]
        self.assertEqual((shown["brief"], shown["result"], shown["outcome"]), (MEMBER_BRIEF, MEMBER_RESULT, MEMBER_OUTCOME))
        events = self.home.rows("SELECT data FROM events WHERE kind = 'import'")
        self.assertFalse([e for e in events if json.loads(e["data"]).get("accepted")])

    def test_an_edit_below_a_heading_inside_a_members_own_section_is_still_refused(self):
        self.home.json("render")
        self.edit(self.lead_path, "What the member expects", "What the member now expects")
        proc = self.home.run("import", "--file", self.lead_path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("## Result is the member's own", proc.stderr)
        self.home.json("render", "--discard", self.lead_path, actor="spud")
        # a section's real heading deleted is still a deleted section, whatever headings its neighbours' prose holds
        self.edit(self.lead_path, "\n## Sub-agents\n", "\n")
        proc = self.home.run("import", "--file", self.lead_path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("deleted: Sub-agents", proc.stderr)
        shown = self.home.json("member", "show", self.lead["ref"])["member"]
        self.assertEqual((shown["brief"], shown["result"]), (MEMBER_BRIEF, MEMBER_RESULT))
        events = self.home.rows("SELECT data FROM events WHERE kind = 'import'")
        self.assertFalse([e for e in events if json.loads(e["data"]).get("accepted")])


class HeadingRefusalTest(SpudTestCase):
    """SPD-076: every command that stores a ticket's brief, sizing or outcome, or a member's brief, result, blocked
    question or outcome, refuses a line `## <name>` for a section of that note's kind, fenced or not, and stores
    nothing; the cases are Cherie's review's (docs/spikes/spd-076/review.md)."""

    def setUp(self):
        super().setUp()
        self.t = self.new_ticket("Refusals")
        self.m = self.new_member(self.t["key"], name="Russet", persona="engineer", model="opus")
        self.home.json("member", "start", self.m["ref"], actor="spud")
        self.p = self.home.json("proposal", "file", "--title", "Later", "--why", "Found it.", actor=self.m["ref"])["proposal"]

    def stored(self):
        return (
            self.home.rows("SELECT key, brief, sizing, outcome FROM tickets ORDER BY id"),
            self.home.rows("SELECT name, status, brief, result, blocked, outcome FROM members ORDER BY id"),
            self.home.rows("SELECT id, why, evidence, status FROM proposals ORDER BY id"),
            self.home.scalar("SELECT count(*) FROM events"),
        )

    def assert_refused(self, args, actor, line):
        before = self.stored()
        proc = self.home.run(*args, actor=actor, check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR, (args, proc.stderr))
        self.assertIn("holds the line `%s`, which an import reads as the note's own %s heading" % (line, line), proc.stderr)
        self.assertIn("write `### %s` or reword it" % line[3:], proc.stderr)
        self.assertEqual(self.stored(), before, args)

    def test_member_prose_naming_a_member_section_is_refused(self):
        ref, key = self.m["ref"], self.t["key"]
        template = "Template:\n```\n## Brief\nThe brief.\n\n## Log\n\n## Result\n\n## Blocked\nThe question.\n\n## Outcome\n```"
        cases = [
            # C1: a result that repeats its own section's name after a blank line
            (("member", "result", "Built.\n\n## Result\nmore"), ref, "## Result"),
            # B5: the same inside a fence
            (("member", "result", "Built.\n```\n# Note\n\n## Result\ntext\n```"), ref, "## Result"),
            # E3: a section a member note carries only from markdown-v0
            (("member", "result", "Built.\n\n## Sources\n- a link"), ref, "## Sources"),
            # E4
            (("member", "block", "Which?\n\n## Sources\n- a link"), ref, "## Sources"),
            # F2: an outcome that repeats its own section's name after a blank line
            (("member", "finish", ref, "--status", "done", "--outcome", "Done.\n\n## Outcome\nmore"), "spud", "## Outcome"),
            # B3: a brief quoting a fenced member-note template, planned by Spud and by a member
            (("member", "new", "--ticket", key, "--persona", "scout", "--model", "haiku", "--brief", template), "spud", "## Brief"),
            (("member", "new", "--ticket", key, "--persona", "scout", "--model", "haiku", "--brief", template), ref, "## Brief"),
            # E5: a section the note lacks until the member blocks
            (("member", "edit", ref, "--brief", "Do it.\n\n## Blocked\nif stuck"), "spud", "## Blocked"),
        ]
        for args, actor, line in cases:
            self.assert_refused(args, actor, line)
        # the demoted heading, a name no member section has, a ticket section's name, and a heading not at the line's
        # start are all stored
        result = "Built.\n\n### Result\nmore\n## Results\n ## Result\n## Team"
        self.home.json("member", "result", result, actor=ref)
        self.home.json("member", "finish", ref, "--status", "done", "--outcome", "Done.\n\n### Outcome\nmore", actor="spud")
        shown = self.home.json("member", "show", ref)["member"]
        self.assertEqual((shown["result"], shown["outcome"]), (result, "Done.\n\n### Outcome\nmore"))

    def test_ticket_prose_naming_a_ticket_section_is_refused(self):
        key, ref = self.t["key"], self.m["ref"]
        cases = [
            # A3, C5 and C4 through the commands that store them
            (("ticket", "new", "--title", "T", "--brief", "B.\n\n## Outcome\nprose"), "spud", "## Outcome"),
            (("ticket", "new", "--title", "T", "--sizing", "S.\n\n## Size, persona and model decision\nx"), "spud", "## Size, persona and model decision"),
            (("ticket", "new", "--title", "T", "--outcome", "Done.\n\n## Outcome\nmore"), "spud", "## Outcome"),
            (("ticket", "edit", key, "--brief", "B.\n```\n## Team\n```"), "spud", "## Team"),
            (("ticket", "edit", key, "--sizing", "S.\n\n## Size, persona and model decision\nx"), "spud", "## Size, persona and model decision"),
            (("ticket", "edit", key, "--outcome", "Done.\n\n## Outcome\nmore"), "spud", "## Outcome"),
            # SPD-116: ## Landing is a ticket section like the rest, generated from pull_requests
            (("ticket", "edit", key, "--brief", "B.\n\n## Landing\nnot the generated section"), "spud", "## Landing"),
            # a proposal's why and evidence become a ticket's brief when Spud creates it
            (("proposal", "file", "--title", "P", "--why", "x\n\n## Handoffs\ny"), ref, "## Handoffs"),
            (("proposal", "file", "--title", "P", "--why", "x", "--evidence", "a\n## Proposals received"), ref, "## Proposals received"),
        ]
        for args, actor, line in cases:
            self.assert_refused(args, actor, line)
        # a proposal filed before the refusal: `proposal decide --decision create` refuses to store its brief
        con = self.home.connect()
        try:
            with con:
                con.execute("UPDATE proposals SET why = ?", ("Found it.\n\n## Outcome\nmore",))
        finally:
            con.close()
        self.assert_refused(("proposal", "decide", str(self.p["id"]), "--decision", "create", "--priority", "P3"), "spud", "## Outcome")
        # a member section's name is no ticket section, and a demoted heading is stored
        self.home.json("ticket", "edit", key, "--brief", "B.\n\n## Log\nx\n\n### Outcome\ny", actor="spud")
        self.assertEqual(self.home.json("ticket", "show", key)["ticket"]["brief"], "B.\n\n## Log\nx\n\n### Outcome\ny")


class HeadingSplitTest(unittest.TestCase):
    """The refusal's reading of a line, against the program's own split: functions of the text alone, no home."""

    def test_the_line_is_read_as_the_split_reads_a_heading(self):
        with self.assertRaises(spud.SpudError):
            spud.check_prose_headings("x\n##  Result  ", spud.IMPORT_MEMBER_SECTIONS, "the Result")
        doc = spud.split_document("---\nid: \"01\"\n---\n# H\n\n## Brief\nx\n\n##  Result  \ny", spud.IMPORT_MEMBER_SECTIONS)
        self.assertEqual([name for name, _ in doc["sections"]], ["Brief", "Result"])
        spud.check_prose_headings("x\n## Result\ny", spud.IMPORT_MEMBER_SECTIONS, "a hand edit", before="## Result")  # not added
        spud.check_prose_headings(None, spud.TICKET_SECTIONS, "--outcome")


if __name__ == "__main__":
    unittest.main()
