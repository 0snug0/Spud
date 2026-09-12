"""ticket new|move|edit|show, per-project counters, the ticket state machine,
append-only events, board."""

import re
import sqlite3
import unittest

from helpers import EXIT_ERROR, EXIT_OWNERSHIP, EXIT_TRANSITION, SpudTestCase

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

    def test_every_mutation_writes_an_event(self):
        t = self.new_ticket("Evented")
        self.home.json("ticket", "move", t["key"], "--status", "active", actor="spud")
        self.home.json("ticket", "edit", t["key"], "--priority", "P0", actor="spud")
        self.home.json("ticket", "edit", t["key"], "--title", "Renamed", "--brief", "b", actor="spud")
        events = self.home.json("events", "--ticket", t["key"])["events"]
        self.assertEqual(
            [e["kind"] for e in events], ["ticket.created", "ticket.status", "ticket.priority", "ticket.edited"]
        )
        self.assertEqual(events[1]["data"], {"from": "queued", "to": "active"})
        self.assertEqual(events[2]["data"], {"from": "P2", "to": "P0"})
        self.assertEqual(sorted(events[3]["data"]["fields"]), ["brief", "title"])
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
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events"), 1)

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
