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
                'proposed_by: ""',
                'lead: "[[SPUD-001/Russet]]"',
                "created: " + t["created_at"][:10],
                "tags: [ticket, toy]",
            ],
        )
        self.assertEqual(body[0], MARKER)
        self.assertEqual(body[1], "# SPD-001 — Shape")
        headings = [l for l in body if l.startswith("## ")]
        self.assertEqual(
            headings,
            ["## Brief", "## Size, persona and model decision", "## Team", "## Handoffs", "## Proposals received", "## Outcome"],
        )
        self.assertEqual(section(text, "Brief").strip(), "The brief.")
        self.assertEqual(section(text, "Size, persona and model decision").strip(), "Small.")
        self.assertEqual(
            section(text, "Team").strip(),
            "- [[SPUD-001/Russet|Russet]] (01, engineer, opus)\n  - [[SPUD-001/Yukon|Yukon]] (01.01, scout, haiku)",
        )
        self.assertTrue(text.endswith("\n"))

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
        self.assertEqual(sorted(out["written"]), ["ledger/teams/SPUD-001/Russet.md", "ledger/tickets/SPD-001.md"])
        self.assertTrue(ticket_path.exists() and member_path.exists())
        rows = {r["path"]: r for r in self.home.rows("SELECT path, sha256, through_event_id FROM renders")}
        self.assertEqual(rows["ledger/tickets/SPD-001.md"]["sha256"], hashlib.sha256(ticket_path.read_bytes()).hexdigest())
        self.assertGreater(rows["ledger/tickets/SPD-001.md"]["through_event_id"], 0)
        again = self.home.json("render")
        self.assertEqual(again["written"], [])
        self.assertEqual(sorted(again["unchanged"]), sorted(out["written"]))
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events WHERE kind = 'render'"), 2)

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
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events WHERE kind = 'report.entry'"), 3)
        self.home.json("render")
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("- Next: a", "- Next: changed"), encoding="utf-8")
        proc = self.home.run("import", "--file", path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("append-only", proc.stderr)
        self.restore(path)
        text = path.read_text(encoding="utf-8")
        start = text.index("## ")
        second = text.index("## ", start + 1)
        third = text.index("## ", second + 1)
        path.write_text(text[:second] + text[third:], encoding="utf-8")  # delete the second entry
        proc = self.home.run("import", "--file", path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("Second", proc.stderr)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events WHERE kind = 'report.entry'"), 3)


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
