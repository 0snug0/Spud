"""Migration 0006_owner_origin (SPD-160).

A v5 database, built here from the module's own DDL_0001 to DDL_0005 and the views as they stood before the migration,
with tickets of origin 'eric' and 'proposal', a parked one, a bound one, and a member, events, a handoff, a proposal and a
pull request pointing at them, is migrated by `spud migrate`: the pre-migration backup, tickets rebuilt with every row, id
and column kept and 'eric' written as 'owner', a CHECK that now refuses 'eric', the foreign keys, the board index and the
views intact, and the events -- append-only -- exactly as they were.  A new ticket and a new accepted hand edit say 'owner'.
"""

import sqlite3
import unittest

from helpers import EXIT_ERROR, Home, load_spud_module
from test_migrate_pull_requests import V4_VIEWS_AND_TRIGGERS

spud = load_spud_module()

# 0005_pull_requests changed no view, so the v5 views and triggers are the v4 ones.
V5_VIEWS_AND_TRIGGERS = V4_VIEWS_AND_TRIGGERS

AT = "2026-09-22T10:00:00-07:00"

# (number, status, priority, title, origin)
TICKETS = [(1, "active", "P1", "Active one", "eric"), (2, "queued", "P2", "Proposed one", "eric"),
           (3, "done", "P0", "Done one", "eric"), (4, "parked", "P3", "Parked one", "eric")]


class MigrateOwnerOriginTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.cleanup)
        self.home.db.parent.mkdir(parents=True)
        con = sqlite3.connect(self.home.db, autocommit=True)
        try:
            con.execute("PRAGMA journal_mode = WAL")
            for ddl in (spud.DDL_0001, spud.DDL_0002, spud.DDL_0003, spud.DDL_0004, spud.DDL_0005):
                con.executescript(ddl)
            con.executescript(V5_VIEWS_AND_TRIGGERS)
            con.execute("PRAGMA user_version = 5")
            con.execute("INSERT INTO projects (id, key, name, root_path, ticket_prefix, team_prefix, created_at) VALUES (1, 'spud', 'Spud', ?, 'SPD', 'SPUD', ?)",
                        (str(self.home.path), AT))
            for name in self.home.config["naming"]["pool"]:
                con.execute("INSERT INTO name_pool (name) VALUES (?)", (name,))
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
        finally:
            con.close()

    def migrate(self):
        return self.home.json("migrate")

    def test_the_cli_refuses_a_v5_database_until_migrate(self):
        proc = self.home.run("board", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("behind", proc.stderr)
        self.migrate()
        self.assertIn("SPD-001", self.home.run("board").stdout)

    def test_migrate_writes_the_backup_and_renames_the_origin_in_every_row(self):
        tickets = self.home.rows("SELECT * FROM tickets ORDER BY id")
        events = self.home.rows("SELECT * FROM events ORDER BY id")
        out = self.migrate()
        self.assertEqual((out["applied"], out["user_version"]), (["0006_owner_origin", "0007_project_scripts", "0008_project_runners"], 8))
        self.assertEqual(len(out["backups"]), 3)
        self.assertRegex(out["backups"][0], r"/ledger-\d{8}T\d{6}-pre-0006_owner_origin\.db$")
        backup = sqlite3.connect("file:%s?mode=ro" % out["backups"][0], uri=True)
        try:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual(backup.execute("SELECT count(*) FROM tickets WHERE origin = 'eric'").fetchone()[0], 3)
        finally:
            backup.close()
        after = self.home.rows("SELECT * FROM tickets ORDER BY id")
        self.assertEqual(after, [dict(r, origin="owner") if r["origin"] == "eric" else r for r in tickets])
        self.assertEqual([(r["id"], r["origin"]) for r in after], [(10, "owner"), (20, "proposal"), (30, "owner"), (40, "owner")])
        self.assertEqual(list(after[0].keys()), list(tickets[0].keys()))
        self.assertEqual(self.home.rows("SELECT * FROM events ORDER BY id"), events)
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

    def test_a_fresh_init_applies_them_all(self):
        other = Home()
        self.addCleanup(other.cleanup)
        out = other.init()
        self.assertEqual((out["applied"], out["user_version"], out["backups"]),
                         (["0001_init", "0002_projects", "0003_parked", "0004_ticket_worktree", "0005_pull_requests", "0006_owner_origin", "0007_project_scripts", "0008_project_runners"], 8, []))
        self.assertIn("CHECK (origin IN ('owner','proposal'))", other.scalar("SELECT sql FROM sqlite_master WHERE name = 'tickets'"))


if __name__ == "__main__":
    unittest.main()
