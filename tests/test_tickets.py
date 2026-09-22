"""ticket new|move|edit|show, per-project counters, the ticket state machine,
append-only events, board."""

import re
import sqlite3
import unittest
from datetime import datetime

from helpers import EXIT_ERROR, EXIT_OWNERSHIP, EXIT_TRANSITION, EXIT_USAGE, SpudTestCase

ISO_WITH_OFFSET = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


class TicketTest(SpudTestCase):
    def test_new_ticket_gets_the_next_key_in_the_home_project(self):
        t1 = self.new_ticket("First")
        t2 = self.new_ticket("Second", priority="P1", status="active")
        self.assertEqual((t1["key"], t1["team_key"], t1["number"]), ("SPD-001", "SPUD-001", 1))
        self.assertEqual((t2["key"], t2["team_key"], t2["number"]), ("SPD-002", "SPUD-002", 2))
        self.assertEqual(t1["status"], "queued")
        self.assertEqual(t1["priority"], "P2")
        self.assertEqual(t1["origin"], "eric")
        self.assertEqual(t1["tags"], ["ticket"])
        self.assertEqual(t1["lead"], None)
        self.assertRegex(t1["created_at"], ISO_WITH_OFFSET)
        self.assertEqual(t2["status"], "active")
        self.assertEqual(t2["priority"], "P1")

    def test_ticket_tags_and_sections(self):
        t = self.new_ticket("Tagged", tag=["toy", "docs"], brief="Do the thing.", sizing="Small: one scout.")
        self.assertEqual(t["tags"], ["ticket", "toy", "docs"])
        shown = self.home.json("ticket", "show", "SPD-001")["ticket"]
        self.assertEqual(shown["brief"], "Do the thing.")
        self.assertEqual(shown["sizing"], "Small: one scout.")

    def test_counters_and_keys_are_per_project(self):
        con = sqlite3.connect(self.home.db)
        con.execute(
            "INSERT INTO projects (key, name, root_path, ticket_prefix, team_prefix, created_at)"
            " VALUES ('badtakes', 'BadTakes', '/tmp/badtakes', 'BAD', 'BADT', '2026-09-12T00:00:00-07:00')"
        )
        con.commit()
        con.close()
        a = self.new_ticket("Home one")
        b = self.new_ticket("Bad one", project="badtakes")
        c = self.new_ticket("Home two")
        d = self.new_ticket("Bad two", project="badtakes")
        self.assertEqual([a["key"], b["key"], c["key"], d["key"]], ["SPD-001", "BAD-001", "SPD-002", "BAD-002"])
        self.assertEqual([b["team_key"], d["team_key"]], ["BADT-001", "BADT-002"])
        self.assertEqual([a["number"], b["number"], c["number"], d["number"]], [1, 1, 2, 2])

    def test_unknown_project_is_an_error(self):
        proc = self.home.run("ticket", "new", "--title", "x", "--project", "nope", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)

    def test_tickets_are_spuds_alone(self):
        t = self.new_ticket("Mine")
        m = self.new_member(t["key"])
        proc = self.home.run("ticket", "new", "--title", "theirs", actor=m["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        self.assertIn("spud", proc.stderr.lower())
        proc = self.home.run("ticket", "move", t["key"], "--status", "active", actor=m["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        proc = self.home.run("ticket", "edit", t["key"], "--priority", "P0", actor=m["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        proc = self.home.run("ticket", "new", "--title", "no actor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("--as", proc.stderr)

    def test_state_machine(self):
        t = self.new_ticket("Flow")
        key = t["key"]
        proc = self.home.run("ticket", "move", key, "--status", "done", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_TRANSITION)
        self.assertIn("queued", proc.stderr)
        self.assertIn("done", proc.stderr)
        moved = self.home.json("ticket", "move", key, "--status", "active", actor="spud")["ticket"]
        self.assertEqual(moved["status"], "active")
        self.assertIsNone(moved["closed_at"])
        back = self.home.json("ticket", "move", key, "--status", "queued", actor="spud")["ticket"]
        self.assertEqual(back["status"], "queued")
        self.home.json("ticket", "move", key, "--status", "active", actor="spud")
        done = self.home.json("ticket", "move", key, "--status", "done", actor="spud")["ticket"]
        self.assertEqual(done["status"], "done")
        self.assertRegex(done["closed_at"], ISO_WITH_OFFSET)
        proc = self.home.run("ticket", "move", key, "--status", "active", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_TRANSITION)
        t2 = self.new_ticket("Declined")
        declined = self.home.json("ticket", "move", t2["key"], "--status", "declined", actor="spud")["ticket"]
        self.assertEqual(declined["status"], "declined")
        proc = self.home.run("ticket", "move", t2["key"], "--status", "bogus", actor="spud", check=False)
        self.assertNotEqual(proc.returncode, 0)

    def park(self, key, reason="Eric's go", until=None, check=True):
        args = ["ticket", "move", key, "--status", "parked", "--reason", reason] + (["--until", until] if until else [])
        return self.home.run(*args, actor="spud", check=check)

    def test_the_parked_state_machine(self):
        """SPD-096: parked is reached from queued and active, left for queued, active and declined, and renewed."""
        t = self.new_ticket("Split it")
        key = t["key"]
        proc = self.home.run("ticket", "move", key, "--status", "parked", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE)
        self.assertIn("--reason", proc.stderr)
        parked = self.home.json("ticket", "move", key, "--status", "parked", "--reason", "Eric's go", actor="spud")["ticket"]
        self.assertEqual((parked["status"], parked["parked_reason"], parked["parked_until"]), ("parked", "Eric's go", None))
        self.assertIsNone(parked["closed_at"])
        proc = self.home.run("ticket", "move", key, "--status", "done", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_TRANSITION)  # nothing was done while parked
        self.assertIn("parked -> done is not a transition", proc.stderr)
        renewed = self.home.json("ticket", "move", key, "--status", "parked", "--reason", "still Eric's go", "--until", "2026-10-16", actor="spud")["ticket"]
        self.assertEqual((renewed["parked_reason"], renewed["parked_until"]), ("still Eric's go", "2026-10-16"))
        back = self.home.json("ticket", "move", key, "--status", "active", actor="spud")["ticket"]
        self.assertEqual((back["status"], back["parked_reason"], back["parked_until"]), ("active", None, None))
        self.park(key, "on hold again")  # active -> parked
        queued = self.home.json("ticket", "move", key, "--status", "queued", actor="spud")["ticket"]
        self.assertEqual((queued["status"], queued["parked_reason"]), ("queued", None))
        self.park(key, "moot")
        gone = self.home.json("ticket", "move", key, "--status", "declined", actor="spud")["ticket"]
        self.assertEqual((gone["status"], gone["parked_reason"]), ("declined", None))
        self.assertRegex(gone["closed_at"], ISO_WITH_OFFSET)

    def test_until_is_a_date_and_goes_only_with_parked(self):
        t = self.new_ticket("Waiting")
        for extra, needle in ((["--status", "queued", "--until", "2026-10-16"], "--until` goes with"),
                              (["--status", "active", "--until", "2026-10-16"], "--until` goes with")):
            proc = self.home.run("ticket", "move", t["key"], *extra, actor="spud", check=False)
            self.assertEqual(proc.returncode, EXIT_USAGE, proc.stderr)
            self.assertIn(needle, proc.stderr)
        for bad in ("soon", "2026-10-32", "16/10/2026", "2026-9-16"):
            with self.subTest(bad):
                proc = self.park(t["key"], until=bad, check=False)
                self.assertEqual(proc.returncode, EXIT_USAGE, proc.stderr)
                self.assertIn("YYYY-MM-DD", proc.stderr)
        self.assertEqual(self.home.json("ticket", "show", t["key"])["ticket"]["status"], "queued")  # nothing was written

    def test_a_parked_ticket_carries_its_reason_through_json_show_and_events(self):
        t = self.new_ticket("Waiting", priority="P2")
        proc = self.park(t["key"], "App Store approval of iOS 1.0", "2026-10-16")
        self.assertIn("SPD-001 is now parked until 2026-10-16: App Store approval of iOS 1.0", proc.stdout)
        d = self.home.json("ticket", "show", t["key"])["ticket"]
        self.assertEqual((d["parked_until"], d["parked_reason"]), ("2026-10-16", "App Store approval of iOS 1.0"))
        text = self.home.run("ticket", "show", t["key"]).stdout
        self.assertIn("parked until 2026-10-16: App Store approval of iOS 1.0", text)
        status = [e for e in self.home.json("events", "--ticket", t["key"])["events"] if e["kind"] == "ticket.status"][-1]
        self.assertEqual(status["body"], "SPD-001 queued -> parked until 2026-10-16: App Store approval of iOS 1.0")
        self.assertEqual(status["data"], {"from": "queued", "to": "parked", "until": "2026-10-16", "reason": "App Store approval of iOS 1.0"})
        renewal = self.park(t["key"], "renewed")  # parked -> parked drops the date it does not repeat
        self.assertIn("SPD-001 is now parked: renewed", renewal.stdout)
        self.assertIsNone(self.home.json("ticket", "show", t["key"])["ticket"]["parked_until"])
        d = self.home.json("ticket", "move", t["key"], "--status", "queued", actor="spud")["ticket"]
        self.assertEqual((d["parked_until"], d["parked_reason"]), (None, None))
        self.assertNotIn("parked", self.home.run("ticket", "show", t["key"]).stdout)

    def test_parking_waits_for_the_live_members_and_no_member_is_planned_on_a_parked_ticket(self):
        t = self.new_ticket("Busy", status="active")
        kestrel = self.new_member(t["key"], name="Kestrel")
        yukon = self.new_member(t["key"], name="Yukon")
        proc = self.park(t["key"], check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("2 members alive (Kestrel, Yukon)", proc.stderr)
        self.assertEqual(self.home.json("ticket", "show", t["key"])["ticket"]["status"], "active")
        for m in (kestrel, yukon):
            self.home.json("member", "start", m["ref"], actor="spud")
            self.home.json("member", "finish", m["ref"], "--status", "done", "--outcome", "ok", actor="spud")
        self.park(t["key"], "the rest waits for Eric")
        proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku",
                             "--brief", "Do it.", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("is parked; no member can be planned on it", proc.stderr)

    def test_every_mutation_writes_an_event(self):
        t = self.new_ticket("Evented")
        self.home.json("ticket", "move", t["key"], "--status", "active", actor="spud")
        self.home.json("ticket", "edit", t["key"], "--priority", "P0", actor="spud")
        self.home.json("ticket", "edit", t["key"], "--title", "Renamed", "--brief", "b", actor="spud")
        events = self.home.json("events", "--ticket", t["key"])["events"]
        # since SPD-011 a record Spud makes is followed by its report entry; an edit that changes no priority writes none
        self.assertEqual(
            [e["kind"] for e in events],
            ["ticket.created", "report.entry", "ticket.status", "report.entry", "ticket.priority", "report.entry", "ticket.edited"],
        )
        self.assertEqual(events[2]["data"], {"from": "queued", "to": "active"})
        self.assertEqual(events[4]["data"], {"from": "P2", "to": "P0"})
        self.assertEqual(sorted(events[6]["data"]["fields"]), ["brief", "title"])
        self.assertTrue(all(e["actor"] == "spud" for e in events))
        self.assertTrue(all(ISO_WITH_OFFSET.match(e["at"]) for e in events))
        shown = self.home.json("ticket", "show", t["key"])["ticket"]
        self.assertEqual((shown["title"], shown["priority"], shown["brief"]), ("Renamed", "P0", "b"))

    def test_events_are_append_only_at_the_database(self):
        self.new_ticket("Immutable")
        con = sqlite3.connect(self.home.db)
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            con.execute("UPDATE events SET body = 'x'")
        self.assertIn("append-only", str(ctx.exception))
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("DELETE FROM events")
        con.close()
        # What an init and one `ticket new` leave: init's report entry, the config.synced of its settings sync and the
        # render event of its own pass (SPW-001 steps 3, 6 and 9), then ticket.created and the entry it writes (SPD-011).
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events"), 5)

    def test_show_unknown_ticket(self):
        proc = self.home.run("ticket", "show", "SPD-404", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("SPD-404", proc.stderr)

    def test_board_lists_tickets_from_v_board(self):
        a = self.new_ticket("Active one", status="active", priority="P1")
        b = self.new_ticket("Queued one", priority="P3")
        rows = self.home.json("board")["tickets"]
        self.assertEqual([r["key"] for r in rows], [a["key"], b["key"]])
        self.assertEqual(rows[0]["status"], "active")
        text = self.home.run("board").stdout
        self.assertIn("SPD-001", text)
        self.assertIn("Active one", text)
        brief = self.home.run("board", "--brief").stdout
        self.assertIn("SPD-001", brief)
        self.assertIn("active", brief)
        self.assertIn("SPD-002", brief)

    def test_a_parked_ticket_sorts_after_queued_and_leaves_the_brief_for_a_count_line(self):
        a = self.new_ticket("Active one", status="active", priority="P1")
        q = self.new_ticket("Queued one", priority="P3")
        p = self.new_ticket("Parked one", priority="P1")
        self.park(p["key"], "the remaining splits wait for Eric's go")
        self.assertEqual([r["key"] for r in self.home.json("board")["tickets"]], [a["key"], q["key"], p["key"]])
        self.assertIn("parked", self.home.run("board").stdout)
        lines = self.home.run("board", "--brief").stdout.strip().split("\n")
        self.assertEqual(lines, ["SPD-001 active P1 Active one", "SPD-002 queued P3 Queued one", "1 parked (spud board --parked)"])

    def test_a_parked_ticket_that_is_due_back_prints_above_the_queue(self):
        self.new_ticket("Active one", status="active", priority="P1")
        self.new_ticket("Queued one", priority="P2")
        due = self.new_ticket("Waiting one", priority="P2")
        deferred = self.new_ticket("Deferred one", priority="P1")
        self.park(due["key"], "App Store approval of iOS 1.0", "2020-01-01")  # a past date: due back at once
        self.park(deferred["key"], "Eric's go")
        self.assertEqual(self.home.run("board", "--brief").stdout.strip().split("\n"), [
            "SPD-001 active P1 Active one",
            "SPD-003 parked P2 Waiting one (due back 2020-01-01: App Store approval of iOS 1.0)",
            "SPD-002 queued P2 Queued one",
            "2 parked, 1 due back (spud board --parked)",
        ])
        # a date still ahead is the count line and nothing else
        self.home.json("ticket", "move", due["key"], "--status", "parked", "--reason", "App Store approval", "--until", "2099-01-01", actor="spud")
        self.assertEqual(self.home.run("board", "--brief").stdout.strip().split("\n")[-1], "2 parked (spud board --parked)")

    def test_board_parked_lists_them_with_why_and_until(self):
        self.new_ticket("Active one", status="active", priority="P1")
        due = self.new_ticket("Waiting one", priority="P2")
        deferred = self.new_ticket("Deferred one", priority="P1")
        self.park(due["key"], "App Store approval of iOS 1.0", "2020-01-01")
        self.park(deferred["key"], "Eric's go")
        out = self.home.json("board", "--parked")["tickets"]
        self.assertEqual([r["key"] for r in out], ["SPD-003", "SPD-002"])  # P1 before P2, both after the queue
        text = self.home.run("board", "--parked").stdout
        self.assertEqual(text.split("\n")[0].split(), ["ticket", "P", "title", "until", "reason", "lead", "created"])
        self.assertIn("2020-01-01", text)
        self.assertIn("App Store approval of iOS 1.0", text)
        self.assertNotIn("Active one", text)
        self.assertEqual(self.home.run("board", "--parked", "--brief").stdout.strip().split("\n"), [
            "SPD-003 parked P1 Deferred one (Eric's go)",
            "SPD-002 parked P2 Waiting one (due back 2020-01-01: App Store approval of iOS 1.0)",
        ])
        self.assertIn("--parked", self.home.run("board", "--help").stdout)

    def test_board_parked_on_a_board_with_none(self):
        self.new_ticket("Queued one")
        self.assertEqual(self.home.run("board", "--parked").stdout.strip(), "(none)")
        self.assertEqual(self.home.run("board", "--parked", "--brief").stdout.strip(), "(no parked ticket)")
        self.assertNotIn("parked", self.home.run("board", "--brief").stdout)

    def set_stopped(self, member, stamp):
        """A member's final stop, as the SubagentStop hook stamps it."""
        con = sqlite3.connect(self.home.db)
        try:
            with con:
                con.execute("UPDATE members SET stopped_at = ? WHERE id = ?", (stamp, member["id"]))
        finally:
            con.close()

    def test_board_brief_marks_a_member_that_returned_and_is_not_recorded(self):
        """SessionStart injects the brief, so a later or parallel session sees a member another session has not
        recorded yet without being held for it (SPD-018)."""
        t = self.new_ticket("Active one", status="active")
        kestrel = self.new_member(t["key"], name="Kestrel")
        yukon = self.new_member(t["key"], name="Yukon")
        for m in (kestrel, yukon):
            self.home.json("member", "start", m["ref"], actor="spud")
        today = datetime.now().astimezone().isoformat(timespec="seconds")
        self.set_stopped(kestrel, today)
        lines = self.home.run("board", "--brief").stdout.splitlines()
        self.assertIn("  Kestrel (01, scout, haiku) returned %s, unrecorded" % today[11:16], lines)
        self.assertIn("  Yukon (02, scout, haiku) active", lines)
        self.set_stopped(kestrel, "2026-01-02T20:15:48-08:00")  # another day: the clock alone would mislead
        self.assertIn("  Kestrel (01, scout, haiku) returned 2026-01-02T20:15, unrecorded", self.home.run("board", "--brief").stdout.splitlines())
        self.home.json("member", "finish", kestrel["ref"], "--status", "done", "--outcome", "Accepted.", actor="spud")
        lines = self.home.run("board", "--brief").stdout.splitlines()
        self.assertEqual([line for line in lines if line.startswith("  Kestrel")], [])
        self.assertIn("  Yukon (02, scout, haiku) active", lines)

    def test_text_arguments_can_come_from_files_and_stdin(self):
        p = self.home.path / "brief.md"
        p.write_text("A brief\nwith two lines.\n", encoding="utf-8")
        t = self.new_ticket("From file", brief="@" + str(p))
        shown = self.home.json("ticket", "show", t["key"])["ticket"]
        self.assertEqual(shown["brief"], "A brief\nwith two lines.")
        out = self.home.json("ticket", "edit", t["key"], "--outcome", "@-", actor="spud", stdin="From stdin.\n")
        self.assertEqual(out["ticket"]["outcome"], "From stdin.")


if __name__ == "__main__":
    unittest.main()
