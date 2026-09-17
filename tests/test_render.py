"""render: generated file shapes, the marker, the renders table, the hand-edit
conflict and import --file."""

import hashlib
import json
import re
import unittest
from pathlib import Path

from helpers import EXIT_CONFLICT, EXIT_ERROR, EXIT_OK, EXIT_OWNERSHIP, EXIT_TRANSITION, MARKER, SpudTestCase


def frontmatter(text):
    lines = text.split("\n")
    end = lines.index("---", 1)
    return lines[1:end], lines[end + 1 :]


def section(text, name):
    return text.split("## %s\n" % name, 1)[1].split("\n## ", 1)[0]


class RenderShapeTest(SpudTestCase):
    def test_ticket_file_shape(self):
        t = self.new_ticket("Shape", brief="The brief.", sizing="Small.", tag=["toy"])
        lead = self.new_member(t["key"], persona="engineer", model="opus", name="Russet")
        child = self.new_member(t["key"], actor=lead["ref"], name="Yukon")
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        text = (out / "ledger" / "tickets" / "SPD-001.md").read_text(encoding="utf-8")
        fm, body = frontmatter(text)
        self.assertEqual(
            fm,
            [
                "id: SPD-001",
                'title: "Shape"',
                "priority: P2",
                "status: queued",
                "origin: eric",
                "project: spud",  # SPD-014
                'proposed_by: ""',
                'lead: "[[SPUD-001/Russet]]"',
                "created: " + t["created_at"][:10],
                "tags: [ticket, toy]",
            ],
        )
        self.assertEqual(body[0], MARKER)
        self.assertEqual(body[1], "# SPD-001 — Shape")
        self.assertNotIn("parked", "\n".join(fm))  # SPD-096: a note that is not parked carries neither key
        headings = [l for l in body if l.startswith("## ")]
        self.assertEqual(
            headings,
            ["## Brief", "## Size, persona and model decision", "## Team", "## Handoffs", "## Proposals received", "## Outcome"],
        )
        self.assertEqual(section(text, "Brief").strip(), "The brief.")
        self.assertEqual(section(text, "Size, persona and model decision").strip(), "Small.")
        self.assertEqual(
            section(text, "Team").strip(),
            "\n".join(
                [
                    "| Member | ID | Persona | Model | Status | Run | Tokens | Cost (list) | Tools |",
                    "|---|---|---|---|---|---|---|---|---|",
                    "| [[SPUD-001/Russet\\|Russet]] | 01 | engineer | opus | planned | — | — | — | — |",
                    "| ↳ [[SPUD-001/Yukon\\|Yukon]] | 01.01 | scout | haiku | planned | — | — | — | — |",
                    "| **Total** |  |  |  |  |  | — | — | — |",
                    "",
                    "- [[SPUD-001/Russet|Russet]] (01, engineer, opus)",
                    "  - [[SPUD-001/Yukon|Yukon]] (01.01, scout, haiku)",
                    "",
                    "![[Fleet.base#Team]]",
                ]
            ),
        )
        self.assertTrue(text.endswith("\n"))

    def test_a_parked_ticket_note_carries_the_two_keys_and_no_other_note_changes(self):
        """SPD-096 section 4.3: parked_until and parked_reason render after status while the ticket is parked, and only
        then; the migration and the render after it change no other note."""
        t = self.new_ticket("Publish the label", priority="P2")
        other = self.new_ticket("Untouched", priority="P3")
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        note, elsewhere = out / "ledger" / "tickets" / "SPD-001.md", out / "ledger" / "tickets" / "SPD-002.md"
        before = elsewhere.read_text(encoding="utf-8")
        queued = note.read_text(encoding="utf-8")
        self.home.json("ticket", "move", t["key"], "--status", "parked", "--reason", "App Store approval of iOS 1.0",
                       "--until", "2026-10-16", actor="spud")
        self.home.json("render", "--out", out)
        fm, _ = frontmatter(note.read_text(encoding="utf-8"))
        self.assertEqual(fm, [
            "id: SPD-001",
            'title: "Publish the label"',
            "priority: P2",
            "status: parked",
            "parked_until: 2026-10-16",
            'parked_reason: "App Store approval of iOS 1.0"',
            "origin: eric",
            "project: spud",
            'proposed_by: ""',
            'lead: ""',
            "created: " + t["created_at"][:10],
            "tags: [ticket]",
        ])
        self.assertEqual(elsewhere.read_text(encoding="utf-8"), before)  # no other note changes a byte
        # parked with no date: the key stays, empty, as spawned and finished do on a member note
        self.home.json("ticket", "move", t["key"], "--status", "parked", "--reason", "Eric's go", actor="spud")
        self.home.json("render", "--out", out)
        self.assertIn('\nparked_until: ""\nparked_reason: "Eric\'s go"\n', note.read_text(encoding="utf-8"))
        # unparked, the note loses both keys and is what it was before it was ever parked
        self.home.json("ticket", "move", t["key"], "--status", "queued", actor="spud")
        self.home.json("render", "--out", out)
        self.assertEqual(note.read_text(encoding="utf-8"), queued)
        self.assertEqual(elsewhere.read_text(encoding="utf-8"), before)

    def test_member_file_shape_through_its_life(self):
        t = self.new_ticket("Life")
        m = self.new_member(t["key"], name="Russet", brief="Do it.")
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        path = out / "ledger" / "teams" / "SPUD-001" / "Russet.md"
        text = path.read_text(encoding="utf-8")
        fm, body = frontmatter(text)
        self.assertEqual(
            fm,
            [
                'id: "01"',
                "name: Russet",
                "persona: scout",
                "model: haiku",
                'parent: "[[Spud]]"',
                'ticket: "[[SPD-001]]"',
                "project: spud",  # SPD-014
                "status: planned",
                'spawned: ""',
                'finished: ""',
                "tags: [spudagent]",
            ],
        )
        self.assertEqual(body[0], MARKER)
        self.assertEqual(body[1], "# Russet (01, scout) — SPD-001")
        headings = [l for l in body if l.startswith("## ")]
        self.assertEqual(headings, ["## Brief", "## Log", "## Sub-agents", "## Ticket proposals", "## Result", "## Outcome"])
        self.assertEqual(section(text, "Brief").strip(), "Do it.")
        self.home.json("member", "start", m["ref"], actor="spud")
        self.home.json("member", "log", "Read the brief.", actor=m["ref"])
        self.home.json("member", "log", "Wrote the file.", actor=m["ref"])
        self.home.json("member", "result", "Produced x.", actor=m["ref"])
        self.home.json("member", "block", "Which colour?", actor=m["ref"])
        self.home.json("member", "finish", m["ref"], "--status", "blocked", "--outcome", "Asked Eric.", actor="spud")
        self.home.json("render", "--out", out)
        text = path.read_text(encoding="utf-8")
        fm, body = frontmatter(text)
        self.assertIn("status: blocked", fm)
        spawned = [l for l in fm if l.startswith("spawned: ")][0]
        self.assertRegex(spawned, r"^spawned: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
        finished = [l for l in fm if l.startswith("finished: ")][0]
        self.assertRegex(finished, r"^finished: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
        headings = [l for l in body if l.startswith("## ")]
        self.assertEqual(headings, ["## Brief", "## Log", "## Sub-agents", "## Ticket proposals", "## Result", "## Blocked", "## Outcome"])
        log_lines = section(text, "Log").strip().split("\n")
        self.assertEqual(len(log_lines), 2)
        self.assertRegex(log_lines[0], r"^- \d{4}-\d{2}-\d{2}T\d{2}:\d{2} Read the brief\.$")
        self.assertRegex(log_lines[1], r"^- \d{4}-\d{2}-\d{2}T\d{2}:\d{2} Wrote the file\.$")
        self.assertEqual(section(text, "Result").strip(), "Produced x.")
        self.assertEqual(section(text, "Blocked").strip(), "Which colour?")
        self.assertEqual(section(text, "Outcome").strip(), "Asked Eric.")

    def test_child_member_and_sub_agents(self):
        t = self.new_ticket("Tree")
        lead = self.new_member(t["key"], persona="engineer", model="opus", name="Russet")
        self.new_member(t["key"], actor=lead["ref"], name="Yukon", persona="contractor", model="sonnet", agent_type="claude-code-guide")
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        lead_text = (out / "ledger" / "teams" / "SPUD-001" / "Russet.md").read_text(encoding="utf-8")
        self.assertEqual(section(lead_text, "Sub-agents").strip(), "- [[SPUD-001/Yukon|Yukon]] (01.01, contractor on `claude-code-guide`, sonnet)")
        child_text = (out / "ledger" / "teams" / "SPUD-001" / "Yukon.md").read_text(encoding="utf-8")
        fm, body = frontmatter(child_text)
        self.assertEqual(fm[:5], ['id: "01.01"', "name: Yukon", "persona: contractor", "agent_type: claude-code-guide", "model: sonnet"])
        self.assertEqual(fm[5], 'parent: "[[SPUD-001/Russet]]"')
        self.assertEqual(fm[-1], "tags: [spudagent, contractor]")
        self.assertEqual(body[1], "# Yukon (01.01, contractor) — SPD-001")
        ticket_text = (out / "ledger" / "tickets" / "SPD-001.md").read_text(encoding="utf-8")
        self.assertIn("  - [[SPUD-001/Yukon|Yukon]] (01.01, contractor on `claude-code-guide`, sonnet)", ticket_text)

    def test_handoffs_render(self):
        t = self.new_ticket("Hand")
        lead = self.new_member(t["key"], name="Russet")
        self.home.json("handoff", "add", "--ticket", t["key"], "--from", lead["ref"], "--to", "spud", "--what", "the file, complete.", actor="spud")
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        text = (out / "ledger" / "tickets" / "SPD-001.md").read_text(encoding="utf-8")
        line = section(text, "Handoffs").strip()
        self.assertRegex(line, r"^- \d{4}-\d{2}-\d{2} — Russet \(01\) → Spud: the file, complete\.$")

    def test_render_out_never_generates_the_hand_written_notes(self):
        self.new_ticket("Only")
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        self.assertFalse((out / "ledger" / "Home.md").exists())
        self.assertFalse((out / "ledger" / "Spud.md").exists())
        self.assertFalse((out / "ledger" / "Board.base").exists())
        self.assertEqual(self.home.scalar("SELECT count(*) FROM renders"), 0)


class RenderConflictTest(SpudTestCase):
    def test_render_records_hashes_and_is_idempotent(self):
        t = self.new_ticket("Real")
        m = self.new_member(t["key"], name="Russet")
        out = self.home.json("render")
        ticket_path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        member_path = self.home.path / "ledger" / "teams" / "SPUD-001" / "Russet.md"
        # and the day file of the report entry ticket new wrote (SPD-011), stamped with the ticket's clock read
        # ledger/Projects.md since SPD-014
        self.assertEqual(sorted(out["written"]), ["ledger/Projects.md", "ledger/teams/SPUD-001/Russet.md", "ledger/tickets/SPD-001.md", "reports/%s.md" % t["created_at"][:10]])
        self.assertTrue(ticket_path.exists() and member_path.exists())
        rows = {r["path"]: r for r in self.home.rows("SELECT path, sha256, through_event_id FROM renders")}
        self.assertEqual(rows["ledger/tickets/SPD-001.md"]["sha256"], hashlib.sha256(ticket_path.read_bytes()).hexdigest())
        self.assertGreater(rows["ledger/tickets/SPD-001.md"]["through_event_id"], 0)
        again = self.home.json("render")
        self.assertEqual(again["written"], [])
        self.assertEqual(sorted(again["unchanged"]), sorted(out["written"]))
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events WHERE kind = 'render'"), 1)  # SPD-097: the second pass changed nothing and wrote nothing

    def test_a_no_change_render_writes_nothing(self):
        self.new_ticket("Quiet")
        self.home.json("render")
        snapshot = lambda: (self.home.scalar("SELECT max(id) FROM events"), self.home.rows("SELECT path, sha256, rendered_at, through_event_id FROM renders ORDER BY path"))
        before = snapshot()
        again = self.home.json("render")
        self.assertEqual((again["written"], again["conflicts"], again["restyled"]), ([], [], []))
        self.assertEqual(snapshot(), before)

    def test_a_conflict_is_logged_once_per_path_and_on_disk_hash(self):
        t = self.new_ticket("Edited", brief="Original brief.")
        self.home.json("render")
        path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        path.write_text(path.read_text(encoding="utf-8").replace("Original brief.", "Edited once."), encoding="utf-8")
        self.home.json("ticket", "edit", t["key"], "--title", "Renamed", actor="spud")
        for _ in range(3):
            self.assertEqual(self.home.run("render", check=False).returncode, EXIT_CONFLICT)
        conflicts = lambda: [json.loads(r["data"]) for r in self.home.rows("SELECT data FROM events WHERE kind = 'render' AND json_extract(data, '$.conflict') = 1 ORDER BY id")]
        self.assertEqual([c["path"] for c in conflicts()], ["ledger/tickets/SPD-001.md"])
        self.assertEqual(conflicts()[0]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        path.write_text(path.read_text(encoding="utf-8").replace("Edited once.", "Edited twice."), encoding="utf-8")
        self.assertEqual(self.home.run("render", check=False).returncode, EXIT_CONFLICT)
        self.assertEqual([c["sha256"] for c in conflicts()][1], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(len(conflicts()), 2)

    def test_hand_edit_is_refused_until_import_file_accepts_it(self):
        t = self.new_ticket("Edited", brief="Original brief.")
        self.home.json("render")
        path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        text = path.read_text(encoding="utf-8")
        edited = text.replace("priority: P2", "priority: P0").replace("Original brief.", "Edited brief.")
        path.write_text(edited, encoding="utf-8")
        self.home.json("ticket", "edit", t["key"], "--title", "Renamed by CLI", actor="spud")
        proc = self.home.run("render", check=False)
        self.assertEqual(proc.returncode, EXIT_CONFLICT)
        self.assertIn("ledger/tickets/SPD-001.md", proc.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), edited)
        conflicts = self.home.rows("SELECT data FROM events WHERE kind = 'render' AND json_extract(data, '$.conflict') = 1")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(json.loads(conflicts[0]["data"])["path"], "ledger/tickets/SPD-001.md")
        proc = self.home.run("--json", "render", check=False)
        self.assertEqual(json.loads(proc.stdout)["conflicts"], ["ledger/tickets/SPD-001.md"])
        accepted = self.home.json("import", "--file", path, actor="spud")
        self.assertTrue(accepted["ok"])
        self.assertIn("priority", accepted["changed"])
        self.assertIn("brief", accepted["changed"])
        shown = self.home.json("ticket", "show", t["key"])["ticket"]
        self.assertEqual((shown["priority"], shown["brief"], shown["title"]), ("P0", "Edited brief.", "Renamed by CLI"))
        out = self.home.json("render")
        self.assertEqual(out["conflicts"], [])
        self.assertEqual(out["written"], ["ledger/tickets/SPD-001.md"])
        rendered = path.read_text(encoding="utf-8")
        self.assertIn("priority: P0", rendered)
        self.assertIn('title: "Renamed by CLI"', rendered)
        self.assertIn("Edited brief.", rendered)
        events = [e["kind"] for e in self.home.json("events", "--ticket", t["key"])["events"]]
        self.assertIn("import", events)

    def test_import_file_refuses_a_member_frontmatter_that_names_another_member(self):
        t = self.new_ticket("Guard")
        m = self.new_member(t["key"], name="Russet")
        self.home.json("render")
        path = self.home.path / "ledger" / "teams" / "SPUD-001" / "Russet.md"
        text = path.read_text(encoding="utf-8").replace('id: "01"', 'id: "02"')
        path.write_text(text, encoding="utf-8")
        proc = self.home.run("import", "--file", path, check=False)
        self.assertNotEqual(proc.returncode, 0)

    def test_files_written_whole(self):
        # The renderer writes through a temp file and rename: no half-written note is
        # left behind, and no stray temp files remain next to the notes.
        self.new_ticket("Whole")
        self.home.json("render")
        names = [p.name for p in (self.home.path / "ledger" / "tickets").iterdir()]
        self.assertEqual(names, ["SPD-001.md"])


class StyleOnlyRewriteTest(SpudTestCase):
    """Obsidian rewrites the frontmatter of a note it has open in its own YAML style: event 192
    shows ledger/tickets/SPD-015.md with `title` unquoted and `tags` a block list, nothing else
    changed.  A note that differs from a render only in how its frontmatter is written is written
    over and named in a render event; a changed value or body, or a frontmatter the parser cannot
    read, is the conflict it always was."""

    # What Obsidian did to SPD-015's frontmatter, applied to this test's ticket.
    OBSIDIAN = (
        ('title: "Obsidian rewrote me"', "title: Obsidian rewrote me"),
        ("tags: [ticket, ledger-v1]", "tags:\n  - ticket\n  - ledger-v1"),
    )
    TICKET = "ledger/tickets/SPD-001.md"
    MEMBER = "ledger/teams/SPUD-001/Russet.md"

    def setUp(self):
        super().setUp()
        self.t = self.new_ticket("Obsidian rewrote me", brief="The brief.", tag=["ledger-v1"])
        self.new_member(self.t["key"], name="Russet")
        self.home.json("render")
        self.ticket_path = self.home.path / self.TICKET
        self.member_path = self.home.path / self.MEMBER

    def rewrite(self, path, *edits):
        """Apply each (old, new), old found exactly once, and write the file; returns the text written."""
        text = path.read_text(encoding="utf-8")
        for old, new in edits:
            self.assertEqual(text.count(old), 1, old)
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
        return text

    def render_events(self, flag):
        rows = self.home.rows("SELECT body, data FROM events WHERE kind = 'render' AND json_extract(data, '$.%s') = 1 ORDER BY id" % flag)
        return [dict(json.loads(r["data"]), body=r["body"]) for r in rows]

    def blank_recorded_content(self):
        con = self.home.connect()
        try:
            with con:
                con.execute("UPDATE renders SET content = ''")
        finally:
            con.close()

    def test_a_ticket_restyled_by_obsidian_is_rendered_over_and_named(self):
        rendered = self.ticket_path.read_text(encoding="utf-8")
        restyled = self.rewrite(self.ticket_path, *self.OBSIDIAN)
        out = self.home.json("render")
        self.assertEqual((out["written"], out["restyled"], out["conflicts"]), ([self.TICKET], [self.TICKET], []))
        self.assertEqual(self.ticket_path.read_text(encoding="utf-8"), rendered)
        self.assertEqual(
            [(e["path"], e["text"], e["body"]) for e in self.render_events("style_only")],
            [(self.TICKET, restyled, "re-rendered %s over a style-only frontmatter rewrite" % self.TICKET)],
        )
        summary = json.loads(self.home.scalar("SELECT data FROM events WHERE kind = 'render' ORDER BY id DESC LIMIT 1"))
        self.assertEqual((summary["written"], summary["restyled"], summary["conflicts"]), ([self.TICKET], [self.TICKET], []))
        self.assertEqual(self.render_events("conflict"), [])
        again = self.home.json("render")
        self.assertEqual((again["written"], again["restyled"], again["conflicts"]), ([], [], []))

    def test_a_member_restyled_by_obsidian_is_rendered_over_and_named(self):
        rendered = self.member_path.read_text(encoding="utf-8")
        restyled = self.rewrite(self.member_path, ('finished: ""', "finished:"), ("tags: [spudagent]", "tags:\n  - spudagent"))
        proc = self.home.run("render")
        # unchanged: the ticket and the day file of its report entry (SPD-011)
        self.assertIn(": 1 written, 3 unchanged, 1 style-only rewrite re-rendered\n", proc.stdout)  # ledger/Projects.md among the unchanged
        self.assertIn("  re-rendered %s over a style-only frontmatter rewrite\n" % self.MEMBER, proc.stdout)
        self.assertEqual(self.member_path.read_text(encoding="utf-8"), rendered)
        self.assertEqual([(e["path"], e["text"]) for e in self.render_events("style_only")], [(self.MEMBER, restyled)])

    def test_quoting_and_property_order_are_style(self):
        # 'x' reads as "x" does and 01 as "01" does (values compare as the strings the ledger
        # reads); the order of the properties is the render's to put back, not an edit
        self.home.json("ticket", "edit", self.t["key"], "--title", "Eric's ticket", actor="spud")
        self.home.json("render")
        ticket_rendered = self.ticket_path.read_text(encoding="utf-8")
        member_rendered = self.member_path.read_text(encoding="utf-8")
        self.rewrite(self.ticket_path, ('title: "Eric\'s ticket"', "title: 'Eric''s ticket'"), ("priority: P2\nstatus: queued\n", "status: queued\npriority: P2\n"))
        self.rewrite(self.member_path, ('id: "01"', "id: 01"), ('parent: "[[Spud]]"', "parent: '[[Spud]]'"), ('spawned: ""', "spawned: ''"))
        out = self.home.json("render")
        self.assertEqual((out["restyled"], out["conflicts"]), ([self.TICKET, self.MEMBER], []))
        self.assertEqual(self.ticket_path.read_text(encoding="utf-8"), ticket_rendered)
        self.assertEqual(self.member_path.read_text(encoding="utf-8"), member_rendered)

    def test_a_value_edit_in_obsidian_style_is_still_a_conflict(self):
        cases = {
            "priority": self.OBSIDIAN + (("priority: P2", "priority: P1"),),
            "a list item": (self.OBSIDIAN[0], ("tags: [ticket, ledger-v1]", "tags:\n  - ticket\n  - ledger-v2")),
        }
        for name, edits in cases.items():
            with self.subTest(name):
                edited = self.rewrite(self.ticket_path, *edits)
                proc = self.home.run("--json", "render", check=False)
                self.assertEqual(proc.returncode, EXIT_CONFLICT)
                self.assertIn(self.TICKET, proc.stderr)
                self.assertEqual(json.loads(proc.stdout)["conflicts"], [self.TICKET])
                self.assertEqual(self.ticket_path.read_text(encoding="utf-8"), edited)
                self.assertEqual(
                    self.render_events("conflict")[-1],
                    {"conflict": True, "path": self.TICKET, "sha256": hashlib.sha256(edited.encode("utf-8")).hexdigest(),  # SPD-097: the hash it refused
                     "body": "refused to overwrite hand-edited %s" % self.TICKET},
                )
                self.home.json("render", "--discard", self.ticket_path, actor="spud")
        self.assertEqual(self.render_events("style_only"), [])
        self.assertEqual(self.home.json("ticket", "show", self.t["key"])["ticket"]["priority"], "P2")

    def test_a_body_edit_under_a_restyled_frontmatter_is_still_a_conflict(self):
        cases = {
            "the brief": ("The brief.", "The brief, edited in Obsidian."),
            "one more blank line at the end": ("## Outcome\n", "## Outcome\n\n"),
        }
        for name, edit in cases.items():
            with self.subTest(name):
                edited = self.rewrite(self.ticket_path, *self.OBSIDIAN, edit)
                proc = self.home.run("render", check=False)
                self.assertEqual(proc.returncode, EXIT_CONFLICT)
                self.assertEqual(self.ticket_path.read_text(encoding="utf-8"), edited)
                self.home.json("render", "--discard", self.ticket_path, actor="spud")
        self.assertEqual(self.render_events("style_only"), [])

    def test_an_unreadable_frontmatter_is_a_conflict_not_a_traceback(self):
        rendered = self.ticket_path.read_bytes()
        title = b'title: "Obsidian rewrote me"'
        marker = MARKER.encode("utf-8")
        shapes = {
            "a folded title": rendered.replace(title, b"title: >-\n  Obsidian rewrote me"),
            "an unterminated quote": rendered.replace(title, b'title: "Obsidian rewrote me'),
            "a repeated property hiding an edit": rendered.replace(b"priority: P2\n", b"priority: P1\npriority: P2\n"),
            "no closing ---": rendered.replace(b"---\n" + marker, marker),
            "bytes that are not UTF-8": rendered.replace(title, b'title: "Obsidian rewrote \xff"'),
        }
        for name, data in shapes.items():
            with self.subTest(name):
                self.assertNotEqual(data, rendered)
                self.ticket_path.write_bytes(data)
                proc = self.home.run("render", check=False)
                self.assertEqual(proc.returncode, EXIT_CONFLICT, proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertEqual(self.ticket_path.read_bytes(), data)
                self.assertIn("unreadable", self.render_events("conflict")[-1])
        self.assertEqual(self.render_events("style_only"), [])

    def test_import_file_accepts_a_restyled_note_with_only_its_real_change(self):
        self.rewrite(self.ticket_path, *self.OBSIDIAN, ("priority: P2", "priority: P1"))
        out = self.home.json("import", "--file", self.ticket_path, actor="spud")
        self.assertEqual(out["changed"], ["priority"])
        shown = self.home.json("ticket", "show", self.t["key"])["ticket"]
        self.assertEqual((shown["priority"], shown["title"], shown["tags"]), ("P1", "Obsidian rewrote me", ["ticket", "ledger-v1"]))
        events = self.home.json("events", "--ticket", self.t["key"], "--kind", "ticket.priority")["events"]
        self.assertEqual([e["data"] for e in events], [{"from": "P2", "to": "P1"}])
        # the next render puts the ledger's own style back, with the accepted priority
        again = self.home.json("render")
        self.assertEqual((again["written"], again["restyled"], again["conflicts"]), ([self.TICKET], [], []))
        text = self.ticket_path.read_text(encoding="utf-8")
        for line in ('title: "Obsidian rewrote me"', "priority: P1", "tags: [ticket, ledger-v1]"):
            self.assertIn(line, text)
        # a member note too: its tags as a block list and a bare stamp are no edit of the tags or the stamp
        self.rewrite(self.member_path, ('finished: ""', "finished:"), ("tags: [spudagent]", "tags:\n  - spudagent"), ("## Outcome\n", "## Outcome\nAccepted in Obsidian.\n"))
        out = self.home.json("import", "--file", self.member_path, actor="spud")
        self.assertEqual(out["changed"], ["outcome"])

    def test_a_restyled_note_takes_the_changes_the_cli_made_since(self):
        # compared with the last render, not the new one: Spud's edit after Obsidian's rewrite lands
        restyled = self.rewrite(self.ticket_path, *self.OBSIDIAN)
        self.home.json("ticket", "edit", self.t["key"], "--priority", "P0", actor="spud")
        out = self.home.json("render")
        self.assertEqual((out["restyled"], out["conflicts"]), ([self.TICKET], []))
        text = self.ticket_path.read_text(encoding="utf-8")
        self.assertIn("priority: P0", text)
        self.assertIn('title: "Obsidian rewrote me"', text)
        self.assertEqual([e["text"] for e in self.render_events("style_only")], [restyled])

    def test_without_the_last_render_the_new_render_is_the_baseline(self):
        # a renders row written before the content column existed carries ''
        rendered = self.ticket_path.read_text(encoding="utf-8")
        self.blank_recorded_content()
        self.rewrite(self.ticket_path, *self.OBSIDIAN)
        out = self.home.json("render")
        self.assertEqual((out["restyled"], out["conflicts"]), ([self.TICKET], []))
        self.assertEqual(self.ticket_path.read_text(encoding="utf-8"), rendered)
        # with a CLI change since, the new render is no match and there is no last one: a conflict
        self.blank_recorded_content()
        edited = self.rewrite(self.ticket_path, *self.OBSIDIAN)
        self.home.json("ticket", "edit", self.t["key"], "--priority", "P0", actor="spud")
        proc = self.home.run("render", check=False)
        self.assertEqual(proc.returncode, EXIT_CONFLICT)
        self.assertEqual(self.ticket_path.read_text(encoding="utf-8"), edited)

    def test_a_report_keeps_the_byte_check(self):
        # a report has no frontmatter to restyle: any change is the conflict, with today's event
        self.home.json("report", "add", "First", "--next", "a", actor="spud")
        day = [p for p in self.home.json("render")["written"] if p.startswith("reports/")][0]
        edited = self.rewrite(self.home.path / day, ("- Next: a", "- Next: b"))
        proc = self.home.run("render", check=False)
        self.assertEqual(proc.returncode, EXIT_CONFLICT)
        self.assertEqual((self.home.path / day).read_text(encoding="utf-8"), edited)
        self.assertEqual(self.render_events("conflict"), [{"conflict": True, "path": day, "sha256": hashlib.sha256(edited.encode("utf-8")).hexdigest(),
                                                           "body": "refused to overwrite hand-edited %s" % day}])


class HandEditAllowlistTest(SpudTestCase):
    """import --file accepts only what Spud allowed: ticket priority, tags, title,
    status through the state machine, the column prose sections (ticket Brief,
    Size, Outcome; member Brief, Outcome), an appended report entry.  Everything
    else is refused with a reason and the whole file is left alone."""

    def setUp(self):
        super().setUp()
        self.t = self.new_ticket("Allowed", brief="Brief.", sizing="Size.")
        self.lead = self.new_member(self.t["key"], name="Russet", persona="engineer", model="opus")
        self.child = self.new_member(self.t["key"], actor=self.lead["ref"], name="Yukon")
        self.home.json("render")
        self.ticket_path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        self.member_path = self.home.path / "ledger" / "teams" / "SPUD-001" / "Yukon.md"

    def edit(self, path, old, new):
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def restore(self, path):
        # a refused file stays in conflict; put the render back so the next case starts clean
        self.home.json("render", "--discard", path, actor="spud")

    def test_import_file_needs_as_spud(self):
        self.edit(self.ticket_path, "priority: P2", "priority: P1")
        proc = self.home.run("import", "--file", self.ticket_path, check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("--as", proc.stderr)
        proc = self.home.run("import", "--file", self.ticket_path, actor=self.lead["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        self.assertEqual(self.home.json("ticket", "show", self.t["key"])["ticket"]["priority"], "P2")
        out = self.home.json("import", "--file", self.ticket_path, actor="spud")
        self.assertEqual(out["changed"], ["priority"])
        # the same event the CLI writes, so the log never misses a priority change
        events = self.home.json("events", "--ticket", self.t["key"], "--kind", "ticket.priority")["events"]
        self.assertEqual([(e["actor"], e["data"]) for e in events], [("eric", {"from": "P2", "to": "P1"})])

    def test_ticket_status_goes_through_the_state_machine(self):
        # queued -> done is illegal: the whole file is refused, the priority edit in it too
        self.edit(self.ticket_path, "status: queued", "status: done")
        self.edit(self.ticket_path, "priority: P2", "priority: P0")
        proc = self.home.run("import", "--file", self.ticket_path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_TRANSITION)
        self.assertIn("queued", proc.stderr)
        shown = self.home.json("ticket", "show", self.t["key"])["ticket"]
        self.assertEqual((shown["status"], shown["priority"]), ("queued", "P2"))
        self.assertEqual(self.home.json("events", "--ticket", self.t["key"], "--kind", "ticket.status")["events"], [])
        # queued -> active is legal: accepted with the event, no closed_at
        self.edit(self.ticket_path, "status: done", "status: active")
        out = self.home.json("import", "--file", self.ticket_path, actor="spud")
        self.assertIn("status", out["changed"])
        shown = self.home.json("ticket", "show", self.t["key"])["ticket"]
        self.assertEqual((shown["status"], shown["priority"], shown["closed_at"]), ("active", "P0", None))
        events = self.home.json("events", "--ticket", self.t["key"], "--kind", "ticket.status")["events"]
        self.assertEqual([e["data"] for e in events], [{"from": "queued", "to": "active"}])
        # active -> done stamps closed_at
        self.home.json("render")
        self.edit(self.ticket_path, "status: active", "status: done")
        self.home.json("import", "--file", self.ticket_path, actor="spud")
        shown = self.home.json("ticket", "show", self.t["key"])["ticket"]
        self.assertEqual(shown["status"], "done")
        self.assertRegex(shown["closed_at"], r"^\d{4}-\d{2}-\d{2}T")

    def test_parked_is_never_reached_or_left_by_hand(self):
        """SPD-096 section 4.3: the status and the two properties that qualify it move only through `ticket move`."""
        self.edit(self.ticket_path, "status: queued", "status: parked")
        proc = self.home.run("import", "--file", self.ticket_path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("parked is set and cleared by `spud ticket move --status parked --reason", proc.stderr)
        self.assertEqual(self.home.json("ticket", "show", self.t["key"])["ticket"]["status"], "queued")
        self.restore(self.ticket_path)
        # from a parked note: unparking by hand, and editing either property, are refused the same way
        self.home.json("member", "start", self.lead["ref"], actor="spud")
        self.home.json("member", "finish", self.lead["ref"], "--status", "done", "--outcome", "ok", actor="spud")
        self.home.json("member", "finish", self.child["ref"], "--status", "failed", "--outcome", "never spawned", actor=self.lead["ref"])
        self.home.json("ticket", "move", self.t["key"], "--status", "parked", "--reason", "Eric's go", "--until", "2026-10-16", actor="spud")
        self.home.json("render")
        for old, new in (("status: parked", "status: queued"), ("parked_until: 2026-10-16", "parked_until: 2026-11-16"),
                         ('parked_reason: "Eric\'s go"', 'parked_reason: "my own words"')):
            with self.subTest(old):
                self.edit(self.ticket_path, old, new)
                proc = self.home.run("import", "--file", self.ticket_path, actor="spud", check=False)
                self.assertEqual(proc.returncode, EXIT_ERROR, proc.stderr)
                self.assertIn("a hand edit cannot carry the reason", proc.stderr)
                self.restore(self.ticket_path)
        shown = self.home.json("ticket", "show", self.t["key"])["ticket"]
        self.assertEqual((shown["status"], shown["parked_until"], shown["parked_reason"]), ("parked", "2026-10-16", "Eric's go"))

    def test_member_status_goes_through_the_state_machine(self):
        self.edit(self.member_path, "status: planned", "status: done")
        proc = self.home.run("import", "--file", self.member_path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_TRANSITION)
        self.assertEqual(self.home.json("member", "show", self.child["ref"])["member"]["status"], "planned")
        self.edit(self.member_path, "status: done", "status: active")
        self.home.json("import", "--file", self.member_path, actor="spud")
        shown = self.home.json("member", "show", self.child["ref"])["member"]
        self.assertEqual(shown["status"], "active")
        self.assertRegex(shown["spawned_at"], r"^\d{4}-\d{2}-\d{2}T")
        events = self.home.json("events", "--member", self.child["ref"], "--kind", "member.status")["events"]
        self.assertEqual([e["data"] for e in events], [{"from": "planned", "to": "active"}])
        self.home.json("render")
        self.edit(self.member_path, "status: active", "status: done")
        self.home.json("import", "--file", self.member_path, actor="spud")
        shown = self.home.json("member", "show", self.child["ref"])["member"]
        self.assertEqual(shown["status"], "done")
        self.assertRegex(shown["finished_at"], r"^\d{4}-\d{2}-\d{2}T")

    def test_refused_ticket_properties(self):
        cases = [
            ("id: SPD-001", "id: SPD-901", "id"),
            ("origin: eric", "origin: proposal", "origin"),
            ('proposed_by: ""', 'proposed_by: "[[SPUD-001/Russet]]"', "proposed_by"),
            ('lead: "[[SPUD-001/Russet]]"', 'lead: "[[SPUD-001/Yukon]]"', "lead"),
            ("created: ", "created: 1999-01-01\ncreated_was: ", "created"),
        ]
        for old, new, name in cases:
            text = self.ticket_path.read_text(encoding="utf-8")
            if name == "created":
                import re

                edited = re.sub(r"^created: .*$", "created: 1999-01-01", text, count=1, flags=re.M)
            else:
                self.assertIn(old, text, name)
                edited = text.replace(old, new, 1)
            self.ticket_path.write_text(edited, encoding="utf-8")
            proc = self.home.run("import", "--file", self.ticket_path, actor="spud", check=False)
            self.assertEqual(proc.returncode, EXIT_ERROR, name)
            self.assertIn(name, proc.stderr)
            self.restore(self.ticket_path)
        # an unknown property
        self.edit(self.ticket_path, "tags: [ticket]", "tags: [ticket]\ndue: tomorrow")
        proc = self.home.run("import", "--file", self.ticket_path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("due", proc.stderr)

    def test_accepted_ticket_fields(self):
        self.edit(self.ticket_path, 'title: "Allowed"', 'title: "Allowed, renamed"')
        self.edit(self.ticket_path, "tags: [ticket]", "tags: [ticket, toy]")
        self.edit(self.ticket_path, "Brief.", "Brief, edited.")
        self.edit(self.ticket_path, "Size.", "Size, edited.")
        self.edit(self.ticket_path, "## Outcome\n", "## Outcome\nShipped.\n")
        out = self.home.json("import", "--file", self.ticket_path, actor="spud")
        self.assertEqual(sorted(out["changed"]), ["brief", "outcome", "sizing", "tags", "title"])
        shown = self.home.json("ticket", "show", self.t["key"])["ticket"]
        self.assertEqual(shown["title"], "Allowed, renamed")
        self.assertEqual(shown["tags"], ["ticket", "toy"])
        self.assertEqual((shown["brief"], shown["sizing"], shown["outcome"]), ("Brief, edited.", "Size, edited.", "Shipped."))
        self.assertEqual(self.home.json("render")["written"], ["ledger/tickets/SPD-001.md"])
        self.assertIn("# SPD-001 — Allowed, renamed", self.ticket_path.read_text(encoding="utf-8"))

    def test_derived_sections_cannot_be_edited_or_deleted(self):
        text = self.ticket_path.read_text(encoding="utf-8")
        team = section(text, "Team")
        self.ticket_path.write_text(text.replace(team, "\n- [[SPUD-001/Russet|Russet]] (01, engineer, opus) — the lead\n", 1), encoding="utf-8")
        proc = self.home.run("import", "--file", self.ticket_path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("Team", proc.stderr)
        self.assertIn("spud member", proc.stderr)
        self.restore(self.ticket_path)
        text = self.ticket_path.read_text(encoding="utf-8")
        self.ticket_path.write_text(text.replace("## Team\n" + section(text, "Team") + "\n", "", 1), encoding="utf-8")
        proc = self.home.run("import", "--file", self.ticket_path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("Team", proc.stderr)
        self.restore(self.ticket_path)
        self.edit(self.ticket_path, "## Handoffs\n", "## Handoffs\n- 2026-09-12 — Russet (01) → Spud: the file.\n")
        proc = self.home.run("import", "--file", self.ticket_path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("spud handoff add", proc.stderr)
        shown = self.home.json("ticket", "show", self.t["key"])["ticket"]
        self.assertEqual(shown["title"], "Allowed")

    def test_member_file_allowlist(self):
        # accepted: Brief and Outcome (parent-owned; Spud is an ancestor)
        self.edit(self.member_path, "Do the thing.", "Do the thing, precisely.")
        self.edit(self.member_path, "## Outcome\n", "## Outcome\nAccepted.\n")
        out = self.home.json("import", "--file", self.member_path, actor="spud")
        self.assertEqual(sorted(out["changed"]), ["brief", "outcome"])
        shown = self.home.json("member", "show", self.child["ref"])["member"]
        self.assertEqual((shown["brief"], shown["outcome"]), ("Do the thing, precisely.", "Accepted."))
        self.home.json("render")
        # refused: the member's own sections
        for name, old, new in (
            ("Log", "## Log\n", "## Log\n- 2026-09-12T12:00 typed by hand\n"),
            ("Result", "## Result\n", "## Result\nTyped by hand.\n"),
        ):
            self.edit(self.member_path, old, new)
            proc = self.home.run("import", "--file", self.member_path, actor="spud", check=False)
            self.assertEqual(proc.returncode, EXIT_ERROR, name)
            self.assertIn(name, proc.stderr)
            self.assertIn("through the CLI", proc.stderr)
            self.restore(self.member_path)
        # refused properties
        for old, new, name, hint in (
            ("model: haiku", "model: fable", "model", "--tier-reason"),
            ('spawned: ""', "spawned: 2026-09-12T12:00", "spawned", ""),
            ('finished: ""', "finished: 2031-01-01T00:00", "finished", ""),
            ("name: Yukon", "name: Yukons", "name", ""),
            ("persona: scout", "persona: writer", "persona", ""),
            ('parent: "[[SPUD-001/Russet]]"', 'parent: "[[Spud]]"', "parent", ""),
        ):
            self.edit(self.member_path, old, new)
            proc = self.home.run("import", "--file", self.member_path, actor="spud", check=False)
            self.assertEqual(proc.returncode, EXIT_ERROR, name)
            self.assertIn(name, proc.stderr)
            if hint:
                self.assertIn(hint, proc.stderr)
            self.restore(self.member_path)
        shown = self.home.json("member", "show", self.child["ref"])["member"]
        self.assertEqual((shown["model"], shown["spawned_at"], shown["finished_at"], shown["result"]), ("haiku", None, None, None))

    def test_report_entries_may_be_appended_but_not_changed_or_deleted(self):
        self.home.json("report", "add", "First", "--next", "a", actor="spud")
        self.home.json("report", "add", "Second", "--next", "b", actor="spud")
        out = self.home.json("render")
        day = [p for p in out["written"] if p.startswith("reports/")][0]
        path = self.home.path / day
        text = path.read_text(encoding="utf-8")
        path.write_text(text + "\n## 23:59 — Appended by hand\n- Next: by hand\n", encoding="utf-8")
        accepted = self.home.json("import", "--file", path, actor="spud")
        self.assertEqual(accepted["changed"], ["entries:1"])
        # the three, and the entry setUp's ticket new wrote (SPD-011)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events WHERE kind = 'report.entry'"), 4)
        self.home.json("render")
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("- Next: a", "- Next: changed"), encoding="utf-8")
        proc = self.home.run("import", "--file", path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("append-only", proc.stderr)
        self.restore(path)
        text = path.read_text(encoding="utf-8")
        second = text.rindex("\n## ", 0, text.index(" — Second\n")) + 1  # the Second entry's heading
        third = text.index("\n## ", second) + 1  # the heading after it
        path.write_text(text[:second] + text[third:], encoding="utf-8")  # delete the Second entry
        proc = self.home.run("import", "--file", path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("Second", proc.stderr)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events WHERE kind = 'report.entry'"), 4)


class DiscardTest(SpudTestCase):
    def test_render_discard_overwrites_a_hand_edit_and_records_it(self):
        t = self.new_ticket("Discarded")
        self.home.json("render")
        path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        rendered = path.read_text(encoding="utf-8")
        path.write_text(rendered.replace("priority: P2", "priority: P0"), encoding="utf-8")
        proc = self.home.run("render", check=False)
        self.assertEqual(proc.returncode, EXIT_CONFLICT)
        proc = self.home.run("render", "--discard", path, check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)  # Spud-only: needs --as spud
        proc = self.home.run("render", "--discard", path, actor="SPUD-001/Nobody", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        out = self.home.json("render", "--discard", path, actor="spud")
        self.assertEqual(out["discarded"], ["ledger/tickets/SPD-001.md"])
        self.assertEqual(out["conflicts"], [])
        self.assertEqual(path.read_text(encoding="utf-8"), rendered)
        events = self.home.rows("SELECT data FROM events WHERE kind = 'render' AND json_extract(data, '$.discarded') = 1")
        self.assertEqual(len(events), 1)
        data = json.loads(events[0]["data"])
        self.assertEqual(data["path"], "ledger/tickets/SPD-001.md")
        self.assertIn("priority: P0", data["text"])
        self.assertEqual(self.home.json("ticket", "show", t["key"])["ticket"]["priority"], "P2")
        again = self.home.json("render")
        self.assertEqual((again["written"], again["conflicts"]), ([], []))
        proc = self.home.run("render", "--discard", self.home.path / "ledger" / "tickets" / "SPD-404.md", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)


if __name__ == "__main__":
    unittest.main()
