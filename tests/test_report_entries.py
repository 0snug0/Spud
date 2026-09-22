"""Report entries written by the commands that make the records they report (SPD-011).

As Spud, ticket new, ticket move, ticket edit (when --priority changes the priority), member finish of
a root member and proposal decide each write one report.entry in the transaction of their record: a
generated title, a body of `- ` lines, and with --next Spud's Next line last.  A refused command writes
none; a member passing --next is refused (exit 3), an empty --next or one with no entry to go on is a
usage error (exit 2), and neither writes anything.  report add stays for what no command records.  A
day renders each entry as its heading and then its body lines, in event order, and the days rendered
before SPD-011 render byte for byte as they are committed.
"""

import io
import re
import subprocess
import tarfile
import unittest
from pathlib import Path

from helpers import (
    EXIT_ERROR,
    EXIT_LIMIT,
    EXIT_OWNERSHIP,
    EXIT_TRANSITION,
    EXIT_USAGE,
    MARKER,
    REPO,
    SpudTestCase,
    init_report_day,
)

ISO_WITH_OFFSET = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")

# The commit SPD-011 started from.  Every day in its reports/ was rendered by `spud render` before this
# ticket; the commit is on main, in every worktree and on origin, like the acceptance tests' pinned ref.
PINNED_DAYS_REF = "e0443b5"
PINNED_DAYS = ["2026-09-12.md", "2026-09-13.md"]


def entry_line(event):
    """The line `report add` has printed since SPD-007, and every command that writes an entry prints."""
    return "report entry %s — %s added to reports/%s.md" % (event["at"][11:16], event["data"]["title"], event["at"][:10])


def rendered_days(events):
    """{reports/<day>.md: text} as a day file renders report.entry events: the marker, the date and a blank
    line, then per entry its heading, its body when it has one, and a blank line; one newline at the end."""
    days = {}
    for e in events:
        days.setdefault(e["at"][:10], []).append(e)
    out = {}
    for day, entries in days.items():
        text = "%s\n# %s\n" % (MARKER, day)
        for e in entries:
            text += "\n## %s — %s\n" % (e["at"][11:16], e["data"]["title"])
            if e["body"]:
                text += e["body"] + "\n"
        out["reports/%s.md" % day] = text
    return out


class EntryCase(SpudTestCase):
    """What every test here reads: the report entries, the events, a command and the entry it wrote."""

    def entries(self):
        return self.home.json("events", "--kind", "report.entry")["events"]

    def event_count(self):
        return self.home.scalar("SELECT count(*) FROM events")

    def one_entry(self, *args, actor="spud"):
        """Run a command with --json that must write exactly one report entry: (its output, the entry's event)."""
        before = len(self.entries())
        out = self.home.json(*args, actor=actor)
        new = self.entries()[before:]
        self.assertEqual(len(new), 1, (args, new))
        return out, new[0]

    def no_entry(self, *args, actor="spud"):
        """Run a command with --json that succeeds and writes no report entry; its output."""
        before = len(self.entries())
        out = self.home.json(*args, actor=actor)
        self.assertNotIn("report_entry", out)
        self.assertEqual(len(self.entries()), before, args)
        return out

    def refused(self, code, *args, actor="spud"):
        """Run a command that must exit with `code` and write nothing at all; the process."""
        before = self.event_count()
        proc = self.home.run(*args, actor=actor, check=False)
        self.assertEqual(proc.returncode, code, (args, proc.stdout, proc.stderr))
        self.assertEqual(self.event_count(), before, args)
        return proc

    def assert_entry(self, event, title, generated, ticket, body=(), member=None):
        self.assertEqual(event["kind"], "report.entry")
        self.assertEqual(event["actor"], "spud")
        self.assertEqual(event["data"], {"title": title, "generated": generated})
        self.assertEqual(event["ticket"], ticket)
        self.assertEqual(event["member"], member)
        self.assertEqual(event["body"], "\n".join(body))
        self.assertRegex(event["at"], ISO_WITH_OFFSET)

    def assert_written_with(self, entry, kind):
        """The entry follows the event of its record, stamped by the same clock read: one transaction."""
        self.assertEqual(self.home.rows("SELECT kind, at FROM events WHERE id = ?", entry["id"] - 1), [{"kind": kind, "at": entry["at"]}])


class TicketEntryTest(EntryCase):
    def test_ticket_new_writes_its_entry(self):
        _, e = self.one_entry("ticket", "new", "--title", "First")
        self.assert_entry(e, "SPD-001 created (queued, P2): First", "ticket new", "SPD-001")
        self.assert_written_with(e, "ticket.created")
        _, e = self.one_entry("ticket", "new", "--title", "Second", "--status", "active", "--priority", "P1", "--next", "An engineer builds it.")
        self.assert_entry(e, "SPD-002 created (active, P1): Second", "ticket new", "SPD-002", ["- Next: An engineer builds it."])
        self.assert_written_with(e, "ticket.created")

    def test_ticket_move_writes_an_entry_named_by_the_new_status(self):
        t = self.new_ticket("Flow")
        for status, verb, extra, body in (
            ("active", "started", ["--next", "An engineer builds it."], ["- Next: An engineer builds it."]),
            ("queued", "queued", ["--reason", "Eric shelved it."], []),
            # SPD-096: a park's entry carries the reason, so the day's report says why the ticket left the queue
            ("parked", "parked", ["--reason", "Eric's go", "--until", "2026-10-16"], ["- parked until 2026-10-16: Eric's go"]),
            ("active", "started", [], []),
            ("done", "done", ["--next", "Nothing left."], ["- Next: Nothing left."]),
        ):
            _, e = self.one_entry("ticket", "move", t["key"], "--status", status, *extra)
            self.assert_entry(e, "SPD-001 %s: Flow" % verb, "ticket move", "SPD-001", body)
            self.assert_written_with(e, "ticket.status")
        d = self.new_ticket("Never")
        _, e = self.one_entry("ticket", "move", d["key"], "--status", "declined", "--next", "Nothing.")
        self.assert_entry(e, "SPD-002 declined: Never", "ticket move", "SPD-002", ["- Next: Nothing."])
        self.assert_written_with(e, "ticket.status")

    def test_ticket_edit_writes_an_entry_when_the_priority_changes(self):
        t = self.new_ticket("Ranked")
        _, e = self.one_entry("ticket", "edit", t["key"], "--priority", "P0")
        self.assert_entry(e, "SPD-001 priority P2 to P0: Ranked", "ticket edit", "SPD-001")
        self.assert_written_with(e, "ticket.priority")
        # the title is the ticket's after the edit
        _, e = self.one_entry("ticket", "edit", t["key"], "--priority", "P3", "--title", "Reranked", "--next", "Someday.")
        self.assert_entry(e, "SPD-001 priority P0 to P3: Reranked", "ticket edit", "SPD-001", ["- Next: Someday."])
        for args in (["--priority", "P3"], ["--title", "x"], ["--brief", "b", "--tag", "docs"]):
            self.no_entry("ticket", "edit", t["key"], *args)

    def test_next_on_a_ticket_edit_that_writes_no_entry_is_a_usage_error(self):
        t = self.new_ticket("Kept")
        for args in (["--title", "x", "--next", "y"], ["--title", "x", "--next", ""], ["--priority", "P2", "--next", "y"], ["--next", "y"]):
            proc = self.refused(EXIT_USAGE, "ticket", "edit", t["key"], *args)
            self.assertIn("--next", proc.stderr)
            self.assertNotIn("unrecognized arguments", proc.stderr)  # the CLI's refusal, not argparse's
        shown = self.home.json("ticket", "show", t["key"])["ticket"]
        self.assertEqual((shown["title"], shown["priority"]), ("Kept", "P2"))


class MemberFinishEntryTest(EntryCase):
    def setUp(self):
        super().setUp()
        self.t = self.new_ticket("Team", status="active")

    def test_a_root_members_finish_writes_its_entry_for_each_status(self):
        russet = self.new_member(self.t["key"], persona="engineer", model="opus", name="Russet")
        self.home.json("member", "start", russet["ref"], actor="spud")
        _, e = self.one_entry("member", "finish", russet["ref"], "--status", "done", "--outcome", "Accepted.",
                              "--summary", "Built generated report entries in bin/spud.", "--next", "Spud commits the branch.")
        self.assert_entry(e, "SPD-001: Russet (01, engineer, opus) done", "member finish", "SPD-001",
                          ["- Built generated report entries in bin/spud.", "- Next: Spud commits the branch."], member=russet["ref"])
        self.assert_written_with(e, "member.status")
        yukon = self.new_member(self.t["key"], name="Yukon")
        self.home.json("member", "start", yukon["ref"], actor="spud")
        _, e = self.one_entry("member", "finish", yukon["ref"], "--status", "blocked", "--outcome", "Asked Eric.")
        self.assert_entry(e, "SPD-001: Yukon (02, scout, haiku) blocked", "member finish", "SPD-001", member=yukon["ref"])
        kennebec = self.new_member(self.t["key"], persona="writer", model="sonnet", name="Kennebec")
        _, e = self.one_entry("member", "finish", kennebec["ref"], "--status", "failed", "--outcome", "The spawn failed.", "--next", "Spud plans a new writer.")
        self.assert_entry(e, "SPD-001: Kennebec (03, writer, sonnet) failed", "member finish", "SPD-001", ["- Next: Spud plans a new writer."], member=kennebec["ref"])

    def test_the_summary_line_is_the_members_summary_after_the_command(self):
        russet = self.new_member(self.t["key"], name="Russet")
        yukon = self.new_member(self.t["key"], name="Yukon")
        for m in (russet, yukon):
            self.home.json("member", "start", m["ref"], actor="spud")
            self.home.json("member", "edit", m["ref"], "--summary", "Surveyed the tests directory.", actor="spud")
        _, e = self.one_entry("member", "finish", russet["ref"], "--status", "done", "--outcome", "Fine.")
        self.assertEqual(e["body"], "- Surveyed the tests directory.")
        _, e = self.one_entry("member", "finish", yukon["ref"], "--status", "done", "--outcome", "Fine.", "--summary", "Listed the fixtures.")
        self.assertEqual(e["body"], "- Listed the fixtures.")

    def test_a_nested_parents_finish_writes_no_entry(self):
        lead = self.new_member(self.t["key"], persona="engineer", model="opus", name="Russet")
        self.home.json("member", "start", lead["ref"], actor="spud")
        child = self.new_member(self.t["key"], actor=lead["ref"], name="Yukon")
        other = self.new_member(self.t["key"], actor=lead["ref"], name="Kennebec")
        for m in (child, other):
            self.home.json("member", "start", m["ref"], actor=lead["ref"])
        proc = self.refused(EXIT_OWNERSHIP, "member", "finish", child["ref"], "--status", "done", "--outcome", "Fine.", "--next", "y", actor=lead["ref"])
        self.assertIn("--as spud", proc.stderr)
        self.no_entry("member", "finish", child["ref"], "--status", "done", "--outcome", "Fine.", "--summary", "Wrote the section.", actor=lead["ref"])
        # Spud recording a nested member's verdict is no root member's finish either: no entry, so no --next
        proc = self.refused(EXIT_USAGE, "member", "finish", other["ref"], "--status", "failed", "--outcome", "Its lead died.", "--next", "y")
        self.assertIn("root member", proc.stderr)
        self.no_entry("member", "finish", other["ref"], "--status", "failed", "--outcome", "Its lead died.")


class ProposalEntryTest(EntryCase):
    def setUp(self):
        super().setUp()
        self.t = self.new_ticket("Origin", status="active")
        self.lead = self.new_member(self.t["key"], persona="writer", model="sonnet", name="Kestrel")
        self.home.json("member", "start", self.lead["ref"], actor="spud")
        self.child = self.new_member(self.t["key"], actor=self.lead["ref"], name="Rosara")
        self.home.json("member", "start", self.child["ref"], actor=self.lead["ref"])

    def escalated(self, title):
        """A proposal Rosara filed and Kestrel escalated, so Spud holds it; neither step writes an entry."""
        before = len(self.entries())
        p = self.home.json("proposal", "file", "--title", title, "--why", "No entry point.", "--priority", "P3", actor=self.child["ref"])["proposal"]
        self.no_entry("proposal", "decide", str(p["id"]), "--decision", "escalate", actor=self.lead["ref"])
        self.assertEqual(len(self.entries()), before)
        return p

    def test_create_writes_one_entry_for_the_ticket_it_creates(self):
        p = self.escalated("Add an index page for docs/toy/")
        out, e = self.one_entry("proposal", "decide", str(p["id"]), "--decision", "create", "--priority", "P1", "--next", "Picked up when Eric names it.")
        self.assertEqual(out["ticket"]["key"], "SPD-002")
        self.assert_entry(e, "SPD-002 created from a proposal by SPUD-001/Rosara (queued, P1): Add an index page for docs/toy/",
                          "proposal decide", "SPD-002", ["- Next: Picked up when Eric names it."])
        self.assert_written_with(e, "proposal.decided")
        # one entry, not a second one for the ticket it creates
        self.assertEqual([x["kind"] for x in self.home.json("events", "--ticket", "SPD-002")["events"]], ["ticket.created", "report.entry"])
        q = self.escalated("Another page")
        _, e = self.one_entry("proposal", "decide", str(q["id"]), "--decision", "create", "--title", "A shorter title")
        self.assert_entry(e, "SPD-003 created from a proposal by SPUD-001/Rosara (queued, P3): A shorter title", "proposal decide", "SPD-003")

    def test_decline_writes_its_entry_with_the_reason(self):
        p = self.escalated("Rewrite the renderer")
        _, e = self.one_entry("proposal", "decide", str(p["id"]), "--decision", "decline", "--reason", "Nothing depends on it.", "--next", "Nothing.")
        self.assert_entry(e, "Proposal declined: Rewrite the renderer (from SPUD-001/Rosara)", "proposal decide", "SPD-001",
                          ["- Reason: Nothing depends on it.", "- Next: Nothing."])
        self.assert_written_with(e, "proposal.decided")
        q = self.escalated("No reason given")
        _, e = self.one_entry("proposal", "decide", str(q["id"]), "--decision", "decline")
        self.assert_entry(e, "Proposal declined: No reason given (from SPUD-001/Rosara)", "proposal decide", "SPD-001")

    def test_a_holders_decisions_write_no_entry(self):
        p = self.home.json("proposal", "file", "--title", "In scope", actor=self.child["ref"])["proposal"]
        for decision in ("absorb", "decline", "escalate"):
            self.refused(EXIT_OWNERSHIP, "proposal", "decide", str(p["id"]), "--decision", decision, "--next", "y", actor=self.lead["ref"])
        self.no_entry("proposal", "decide", str(p["id"]), "--decision", "absorb", "--reason", "in scope", actor=self.lead["ref"])
        q = self.home.json("proposal", "file", "--title", "Not now", actor=self.child["ref"])["proposal"]
        self.no_entry("proposal", "decide", str(q["id"]), "--decision", "decline", "--reason", "not now", actor=self.lead["ref"])


class NextFlagTest(EntryCase):
    def setUp(self):
        super().setUp()
        self.t = self.new_ticket("Flags", status="active")
        self.lead = self.new_member(self.t["key"], persona="engineer", model="opus", name="Russet")
        self.home.json("member", "start", self.lead["ref"], actor="spud")
        self.child = self.new_member(self.t["key"], actor=self.lead["ref"], name="Yukon")
        self.home.json("member", "start", self.child["ref"], actor=self.lead["ref"])
        self.held_by_lead = self.home.json("proposal", "file", "--title", "For the lead", actor=self.child["ref"])["proposal"]
        self.held_by_spud = self.home.json("proposal", "file", "--title", "For Spud", actor=self.lead["ref"])["proposal"]

    def five(self, member, proposal, next_value):
        """One call of each of the five commands, every one with --next and otherwise one that would succeed."""
        return [
            ("ticket", "new", "--title", "Another", "--next", next_value),
            ("ticket", "move", self.t["key"], "--status", "done", "--next", next_value),
            ("ticket", "edit", self.t["key"], "--priority", "P0", "--next", next_value),
            ("member", "finish", member["ref"], "--status", "done", "--outcome", "Fine.", "--next", next_value),
            ("proposal", "decide", str(proposal["id"]), "--decision", "decline", "--reason", "No.", "--next", next_value),
        ]

    def assert_untouched(self):
        self.assertEqual([t["key"] for t in self.home.json("board")["tickets"]], [self.t["key"]])
        shown = self.home.json("ticket", "show", self.t["key"])["ticket"]
        self.assertEqual((shown["status"], shown["priority"]), ("active", "P2"))
        for m in (self.lead, self.child):
            self.assertEqual(self.home.json("member", "show", m["ref"])["member"]["status"], "active")
        self.assertEqual([p["status"] for p in self.home.json("proposal", "list")["proposals"]], ["open", "open"])

    def test_a_member_passing_next_to_any_of_the_five_is_refused_and_writes_nothing(self):
        for args in self.five(self.child, self.held_by_lead, "Spud commits it."):
            proc = self.refused(EXIT_OWNERSHIP, *args, actor=self.lead["ref"])
            self.assertIn("Spud's", proc.stderr)
        self.assert_untouched()

    def test_an_empty_next_is_a_usage_error_and_writes_nothing(self):
        for value in ("", "   "):
            for args in self.five(self.lead, self.held_by_spud, value):
                proc = self.refused(EXIT_USAGE, *args)
                self.assertIn("--next", proc.stderr)
                self.assertNotIn("unrecognized arguments", proc.stderr)  # the CLI's refusal, not argparse's
        self.assert_untouched()


class RefusedCommandTest(EntryCase):
    def test_a_refused_command_writes_no_entry(self):
        t = self.new_ticket("Refusals")
        russet = self.new_member(t["key"], name="Russet")
        # the state machines: a queued ticket is not done yet, nor is a planned member
        self.refused(EXIT_TRANSITION, "ticket", "move", t["key"], "--status", "done", "--next", "y")
        self.refused(EXIT_TRANSITION, "member", "finish", russet["ref"], "--status", "done", "--outcome", "x", "--next", "y")
        # ownership: a member moving a ticket or finishing its parent; Spud deciding a proposal a member holds
        self.home.json("member", "start", russet["ref"], actor="spud")
        yukon = self.new_member(t["key"], actor=russet["ref"], name="Yukon")
        self.home.json("member", "start", yukon["ref"], actor=russet["ref"])
        p = self.home.json("proposal", "file", "--title", "Held", actor=yukon["ref"])["proposal"]
        self.refused(EXIT_OWNERSHIP, "ticket", "move", t["key"], "--status", "active", actor=russet["ref"])
        self.refused(EXIT_OWNERSHIP, "member", "finish", russet["ref"], "--status", "done", "--outcome", "x", actor=yukon["ref"])
        self.refused(EXIT_OWNERSHIP, "proposal", "decide", str(p["id"]), "--decision", "create", "--next", "y")
        # the limits refuse none of the five; member new at the root fan-out is the limit refusal, and it writes no entry either
        for name in ("Kennebec", "Agria"):
            self.new_member(t["key"], name=name)
        self.refused(EXIT_LIMIT, "member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "x")
        # errors: Spud escalating, a ticket that does not exist
        self.home.json("proposal", "decide", str(p["id"]), "--decision", "escalate", actor=russet["ref"])
        self.refused(EXIT_ERROR, "proposal", "decide", str(p["id"]), "--decision", "escalate", "--next", "y")
        self.refused(EXIT_ERROR, "ticket", "move", "SPD-404", "--status", "active", "--next", "y")
        self.assertEqual([e["data"]["title"] for e in self.entries()][1:],  # [0] is init's own (SPW-001)
                         ["SPD-001 created (queued, P2): Refusals"])


class OutputTest(EntryCase):
    def test_json_gains_report_entry(self):
        out = self.home.json("ticket", "new", "--title", "Shown", "--next", "Look at it.", actor="spud")
        event = self.entries()[-1]
        self.assertEqual(out["report_entry"], {"id": event["id"], "at": event["at"], "title": "SPD-001 created (queued, P2): Shown", "body": "- Next: Look at it."})
        self.assertEqual(out["ticket"]["key"], "SPD-001")
        for args in (["ticket", "move", "SPD-001", "--status", "active"], ["ticket", "edit", "SPD-001", "--priority", "P1"]):
            out = self.home.json(*args, actor="spud")
            event = self.entries()[-1]
            self.assertEqual(out["report_entry"], {"id": event["id"], "at": event["at"], "title": event["data"]["title"], "body": event["body"]})

    def test_text_gains_the_entry_line(self):
        def lines_and_entry(*args):
            proc = self.home.run(*args, actor="spud")
            return proc.stdout.rstrip("\n").split("\n"), self.entries()[-1]

        lines, e = lines_and_entry("ticket", "new", "--title", "Shown", "--status", "active")
        self.assertEqual(lines, ["SPD-001 (SPUD-001) created: Shown [active]", entry_line(e)])
        m = self.new_member("SPD-001", name="Russet")
        self.home.json("member", "start", m["ref"], actor="spud")
        p = self.home.json("proposal", "file", "--title", "Idea", actor=m["ref"])["proposal"]
        lines, e = lines_and_entry("proposal", "decide", str(p["id"]), "--decision", "decline", "--reason", "No.", "--next", "Nothing.")
        self.assertEqual(lines, ["proposal %d declined" % p["id"], entry_line(e)])
        lines, e = lines_and_entry("member", "finish", m["ref"], "--status", "done", "--outcome", "Fine.")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("SPUD-001/Russet is done (finished "), lines[0])
        self.assertEqual(lines[1], entry_line(e))
        lines, e = lines_and_entry("ticket", "edit", "SPD-001", "--priority", "P0")
        self.assertEqual(lines, ["SPD-001 edited: priority", entry_line(e)])
        lines, e = lines_and_entry("ticket", "move", "SPD-001", "--status", "done")
        self.assertEqual(lines, ["SPD-001 is now done", "report entry %s — SPD-001 done: Shown added to reports/%s.md" % (e["at"][11:16], e["at"][:10])])
        # the wording report add prints, unchanged; a command that writes no entry prints no such line
        lines, e = lines_and_entry("report", "add", "Merged into main", "--next", "Nothing.")
        self.assertEqual(lines, [entry_line(e)])
        self.assertEqual(self.home.run("ticket", "edit", "SPD-001", "--brief", "b", actor="spud").stdout, "SPD-001 edited: brief\n")


class ReportDayTest(EntryCase):
    def a_day(self):
        """report add entries around generated ones, with and without bodies; the report.entry events in order."""
        self.home.json("report", "add", "SPD-000 merged into main", "--next", "Spud installs it.", actor="spud")
        t = self.new_ticket("Rendered", status="active")
        m = self.new_member(t["key"], persona="engineer", model="opus", name="Russet")
        self.home.json("member", "start", m["ref"], actor="spud")
        self.home.json("member", "finish", m["ref"], "--status", "done", "--outcome", "Accepted.", "--summary", "Built the day file.", "--next", "Spud commits it.", actor="spud")
        self.home.json("ticket", "move", t["key"], "--status", "done", "--next", "Nothing left.", actor="spud")
        self.home.json("report", "add", "Installed", "--next", "Nothing.", actor="spud")
        events = self.entries()
        # SPW-001: init's own entry is every home's first, and this day file is the day it was built
        self.assertEqual([e["data"].get("generated") for e in events], ["init", None, "ticket new", "member finish", "ticket move", None])
        return events

    def test_a_day_renders_each_entry_as_its_heading_then_its_body_lines_in_order(self):
        events = self.a_day()
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        got = {p.relative_to(out).as_posix(): p.read_text(encoding="utf-8") for p in (out / "reports").glob("*.md")}
        self.assertEqual(got, rendered_days(events))
        text = "".join(got.values())
        self.assertIn("## %s — SPD-001 created (active, P2): Rendered\n\n## " % events[1]["at"][11:16], text)
        self.assertIn("## %s — SPD-001: Russet (01, engineer, opus) done\n- Built the day file.\n- Next: Spud commits it.\n\n## " % events[2]["at"][11:16], text)

    def test_import_file_of_a_day_with_generated_entries_adds_and_refuses_nothing(self):
        events = self.a_day()
        days = sorted(p for p in self.home.json("render")["written"] if p.startswith("reports/"))
        self.assertEqual(days, sorted(rendered_days(events)))
        for rel in days:
            accepted = self.home.json("import", "--file", self.home.path / rel, actor="spud")
            self.assertEqual(accepted["changed"], ["entries:0"])
        self.assertEqual(self.entries(), events)
        again = self.home.json("render")
        self.assertEqual(again["conflicts"], [])
        self.assertEqual([p for p in again["written"] if p.startswith("reports/")], [])

    def test_a_day_of_report_add_entries_alone_is_byte_identical_to_before(self):
        for title, next_line in (("First", "a"), ("Second", "b; Eric decides SPD-002"), ("Third", "c")):
            self.home.json("report", "add", title, "--next", next_line, actor="spud")
        events = self.entries()
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        for rel, text in rendered_days(events).items():
            self.assertEqual((out / rel).read_bytes(), text.encode("utf-8"), rel)


class HelpTest(SpudTestCase):
    def test_the_five_take_next_and_report_add_says_what_it_is_for(self):
        for args in (["ticket", "new"], ["ticket", "move"], ["ticket", "edit"], ["member", "finish"], ["proposal", "decide"]):
            self.assertIn("--next", self.home.run(*args, "--help").stdout, args)
        for args in (["report"], ["report", "add"]):
            text = " ".join(self.home.run(*args, "--help").stdout.split())  # the help is wrapped to the terminal's width
            self.assertIn("for what no command records, such as a merge or an install", text, args)
            self.assertIn("write their own entries and take --next", text, args)


class RenderedDaysPinTest(SpudTestCase):
    """Beside the acceptance tests: the days rendered before SPD-011, imported as committed, render byte for byte."""

    def test_the_days_rendered_before_render_byte_for_byte(self):
        proc = subprocess.run(["git", "-C", str(REPO), "archive", PINNED_DAYS_REF, "reports"], capture_output=True)
        if proc.returncode != 0:
            raise AssertionError("git archive %s reports failed (%s); the pinned commit must exist on main, in every worktree and on origin"
                                 % (PINNED_DAYS_REF, proc.stderr.decode("utf-8", "replace").strip()))
        src = self.home.path / "corpus"
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
            tar.extractall(src, filter="data")
        days = sorted((src / "reports").glob("*.md"))
        self.assertEqual([p.name for p in days], PINNED_DAYS)
        self.assertEqual(self.home.json("import", src / "reports")["reports"], len(days))
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        # the pinned days, and today's, which holds the report entry `spud init` wrote when this home was built (SPW-001)
        self.assertEqual(sorted(p.name for p in (out / "reports").glob("*.md")), sorted(PINNED_DAYS + [Path(init_report_day(self.home)).name]))
        for path in days:
            self.assertEqual((out / "reports" / path.name).read_bytes(), path.read_bytes(), path.name)


if __name__ == "__main__":
    unittest.main()
