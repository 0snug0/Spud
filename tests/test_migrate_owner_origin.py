"""Migration 0006_owner_origin (SPD-160).

A v5 database, built here from the module's own DDL_0001 to DDL_0005 and the views as they stood before the migration,
with tickets of origin 'eric' and 'proposal', a parked one, a bound one, and a member, events, a handoff, a proposal and a
pull request pointing at them, is migrated by `spud migrate`: the pre-migration backup, tickets rebuilt with every row, id
and column kept and 'eric' written as 'owner', a CHECK that now refuses 'eric', the foreign keys, the board index and the
views intact, and the events -- append-only -- exactly as they were.  A new ticket and a new accepted hand edit say 'owner'.
"""

import sqlite3
import unittest

from test_migrate_pull_requests import V4_VIEWS_AND_TRIGGERS
from test_migrations import MigrationCase

# 0005_pull_requests changed no view, so the v5 views and triggers are the v4 ones.
V5_VIEWS_AND_TRIGGERS = V4_VIEWS_AND_TRIGGERS

AT = "2026-09-22T10:00:00-07:00"

# (number, status, priority, title, origin)
TICKETS = [(1, "active", "P1", "Active one", "eric"), (2, "queued", "P2", "Proposed one", "eric"),
           (3, "done", "P0", "Done one", "eric"), (4, "parked", "P3", "Parked one", "eric")]


class MigrateOwnerOriginTest(MigrationCase):
    MIGRATION, VERSION, VIEWS, AT = "0006_owner_origin", 5, V5_VIEWS_AND_TRIGGERS, AT

    def seed(self, con):
        for number, status, priority, title, origin in TICKETS:
            con.execute("INSERT INTO tickets (id, project_id, number, key, team_key, title, priority, status, origin, created_at, updated_at,"
                        " parked_until, parked_reason) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (number * 10, number, "SPD-%03d" % number, "SPUD-%03d" % number, title, priority, status, origin, AT, AT,
                         "2026-10-01" if status == "parked" else None, "waiting" if status == "parked" else None))
        con.execute("UPDATE tickets SET worktree = '/tmp/wt' WHERE id = 10")
        con.execute("INSERT INTO members (ticket_id, lineage, depth, name, persona, model, status, brief, deliverables, planned_at)"
                    " VALUES (10, '01', 1, 'Russet', 'engineer', 'opus', 'active', 'Build it.', '[\"bin/**\"]', ?)", (AT,))
        con.execute("UPDATE tickets SET lead_id = 1 WHERE id = 10")
        con.execute("INSERT INTO events (at, actor, ticket_id, member_id, kind, body) VALUES (?, 'spud', 10, 1, 'member.planned', 'planned Russet')", (AT,))
        con.execute("INSERT INTO events (at, actor, ticket_id, kind, body, data) VALUES (?, 'eric', 10, 'ticket.priority', 'SPD-001 P2 -> P1 (hand edit)', '{}')", (AT,))
        con.execute("INSERT INTO handoffs (ticket_id, at, from_member_id, what) VALUES (10, ?, 1, 'the spike')", (AT,))
        con.execute("INSERT INTO proposals (ticket_id, origin_member_id, title, why, evidence, filed_at, status)"
                    " VALUES (10, 1, 'A proposal', 'because', 'the code', ?, 'open')", (AT,))
        con.execute("UPDATE tickets SET origin = 'proposal', proposal_id = 1 WHERE id = 20")
        con.execute("INSERT INTO pull_requests (ticket_id, url, number, branch, recorded_at, recorded_by) VALUES (10, 'https://x/pull/7', 7, 'b', ?, 'spud')", (AT,))

    def test_migrate_writes_the_backup_and_renames_the_origin_in_every_row(self):
        tickets = self.home.rows("SELECT * FROM tickets ORDER BY id")
        events = self.home.rows("SELECT * FROM events ORDER BY id")
        backup = self.assert_migrated(self.migrate())
        self.assertEqual(backup.execute("SELECT count(*) FROM tickets WHERE origin = 'eric'").fetchone()[0], 3)
        after = self.home.rows("SELECT * FROM tickets ORDER BY id")
        self.assertEqual(self.as_before(after, tickets), [dict(r, origin="owner") if r["origin"] == "eric" else r for r in tickets])
        self.assertEqual([(r["id"], r["origin"]) for r in after], [(10, "owner"), (20, "proposal"), (30, "owner"), (40, "owner")])
        # the rebuild kept every column in its place; a column a later migration adds comes after them
        self.assertEqual(list(after[0].keys())[:len(tickets[0])], list(tickets[0].keys()))
        self.assertEqual(self.as_before(self.home.rows("SELECT * FROM events ORDER BY id"), events), events)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events WHERE actor = 'eric'"), 1)
        self.assertEqual(self.home.rows("PRAGMA foreign_key_check"), [])
        self.assertEqual(self.home.json("migrate")["applied"], [])

    def test_the_check_accepts_owner_and_proposal_and_refuses_eric(self):
        self.migrate()
        con = self.home.connect()
        con.execute("PRAGMA foreign_keys = ON")
        try:
            self.assertTrue(con.execute("SELECT sql FROM sqlite_master WHERE name = 'tickets'").fetchone()[0].rstrip().endswith("STRICT"))
            insert = ("INSERT INTO tickets (project_id, number, key, team_key, title, priority, status, origin, created_at, updated_at)"
                      " VALUES (1, ?, ?, ?, 'x', 'P2', 'queued', ?, ?, ?)")
            with con:
                con.execute(insert, (5, "SPD-005", "SPUD-005", "owner", AT, AT))
            for number, origin in ((6, "eric"), (7, "human")):
                with self.subTest(origin):
                    with self.assertRaises(sqlite3.IntegrityError) as caught:
                        with con:
                            con.execute(insert, (number, "SPD-%03d" % number, "SPUD-%03d" % number, origin, AT, AT))
                    self.assertIn("CHECK", str(caught.exception))
            for label, statement in (
                ("a parked ticket without its reason", "UPDATE tickets SET parked_reason = NULL WHERE id = 40"),
                ("a lead who does not exist", "UPDATE tickets SET lead_id = 99 WHERE id = 30"),
                ("a second ticket with the same number", "INSERT INTO tickets (project_id, number, key, team_key, title, priority, status, origin, created_at, updated_at)"
                                                         " VALUES (1, 1, 'SPD-X', 'SPUD-X', 'x', 'P2', 'queued', 'owner', 'a', 'a')"),
            ):
                with self.subTest(label):
                    with self.assertRaises(sqlite3.IntegrityError):
                        with con:
                            con.execute(statement)
        finally:
            con.close()

    def test_the_foreign_keys_the_index_the_triggers_and_the_views_survive(self):
        self.migrate()
        con = self.home.connect()
        con.execute("PRAGMA foreign_keys = ON")
        try:
            self.assertIn("tickets_board", {r[1] for r in con.execute("PRAGMA index_list(tickets)").fetchall()})
            referring = {r[0] for r in con.execute("SELECT m.name FROM sqlite_master m, pragma_foreign_key_list(m.name) f"
                                                   " WHERE m.type = 'table' AND f.\"table\" = 'tickets'").fetchall()}
            self.assertTrue({"members", "events", "handoffs", "proposals", "pull_requests"} <= referring)
            for statement in ("UPDATE events SET actor = 'owner' WHERE actor = 'eric'", "DELETE FROM events WHERE id = 1"):
                with self.subTest(statement):
                    with self.assertRaises(sqlite3.DatabaseError) as caught:
                        with con:
                            con.execute(statement)
                    self.assertIn("append-only", str(caught.exception))
            with self.assertRaises(sqlite3.IntegrityError):
                with con:
                    con.execute("DELETE FROM tickets WHERE id = 10")
        finally:
            con.close()
        self.assertEqual(self.home.rows("SELECT key, origin, worktree FROM v_board WHERE key = 'SPD-001'"),
                         [{"key": "SPD-001", "origin": "owner", "worktree": "/tmp/wt"}])
        self.assertEqual(self.home.rows("SELECT key, origin, proposed_by FROM v_board WHERE key = 'SPD-002'"),
                         [{"key": "SPD-002", "origin": "proposal", "proposed_by": "SPUD-001/Russet"}])
        self.assertEqual([r["name"] for r in self.home.rows("SELECT name FROM v_fleet")], ["Russet"])

    def test_a_new_ticket_says_owner(self):
        self.migrate()
        t = self.home.json("ticket", "new", "--title", "Fresh", actor="spud")["ticket"]
        self.assertEqual(t["origin"], "owner")
        created = self.home.json("events", "--ticket", t["key"], "--kind", "ticket.created")["events"]
        self.assertEqual(created[0]["data"]["origin"], "owner")


if __name__ == "__main__":
    unittest.main()
