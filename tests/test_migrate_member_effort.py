"""Migration 0009_member_effort (SPD-222).

A v8 database, built here from the module's own DDL_0001 to DDL_0008 and the views and triggers, with three members on
three models -- one of them a failed opus engineer -- is migrated by `spud migrate`: the pre-migration backup, every member
row and column kept, the two new columns NULL on each (nobody recorded the effort a member ran at before, nor an
escalation), a CHECK that takes only Claude Code's effort levels, a foreign key that takes only a member, `v_fleet` naming
the effort, and the events exactly as they were.  A member planned after the migration carries both.
"""

import sqlite3
import unittest

from helpers import EXIT_ERROR, Home, load_spud_module

spud = load_spud_module()

AT = "2026-09-23T18:00:00-07:00"
MEMBERS = ((20, "01", "Russet", "engineer", "opus", "failed"), (21, "02", "Yukon", "scout", "haiku", "done"),
           (22, "03", "Kestrel", "reviewer", "fable", "done"))


class MigrateMemberEffortTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.cleanup)
        self.home.db.parent.mkdir(parents=True)
        con = sqlite3.connect(self.home.db, autocommit=True)
        try:
            con.execute("PRAGMA journal_mode = WAL")
            for ddl in (spud.DDL_0001, spud.DDL_0002, spud.DDL_0003, spud.DDL_0004, spud.DDL_0005, spud.DDL_0006, spud.DDL_0007,
                        spud.DDL_0008):
                con.executescript(ddl)
            con.executescript(spud.VIEWS_AND_TRIGGERS)
            con.execute("PRAGMA user_version = 8")
            con.execute("INSERT INTO projects (id, key, name, root_path, ticket_prefix, team_prefix, created_at) VALUES (1, 'spud', 'Spud', ?, 'SPD', 'SPUD', ?)",
                        (str(self.home.path), AT))
            for name in self.home.config["naming"]["pool"]:
                con.execute("INSERT INTO name_pool (name) VALUES (?)", (name,))
            con.execute("INSERT INTO tickets (id, project_id, number, key, team_key, title, priority, status, origin, created_at, updated_at)"
                        " VALUES (10, 1, 1, 'SPD-001', 'SPUD-001', 'One', 'P1', 'active', 'owner', ?, ?)", (AT, AT))
            for member_id, lineage, name, persona, model, status in MEMBERS:
                con.execute("INSERT INTO members (id, ticket_id, lineage, depth, name, persona, model, status, brief, planned_at, finished_at, result)"
                            " VALUES (?, 10, ?, 1, ?, ?, ?, ?, 'Do it.', ?, ?, ?)",
                            (member_id, lineage, name, persona, model, status, AT, AT, "It did not build." if status == "failed" else None))
            con.execute("UPDATE tickets SET lead_id = 20 WHERE id = 10")
            con.execute("INSERT INTO events (at, actor, ticket_id, kind, body) VALUES (?, 'spud', 10, 'ticket.created', 'SPD-001 created')", (AT,))
            con.execute("INSERT INTO events (at, actor, ticket_id, member_id, kind, body) VALUES (?, 'spud', 10, 20, 'member.planned', 'planned Russet')", (AT,))
        finally:
            con.close()

    def test_the_cli_refuses_a_v8_database_until_migrate(self):
        proc = self.home.run("board", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("behind", proc.stderr)
        self.home.json("migrate")
        self.assertIn("SPD-001", self.home.run("board").stdout)

    def test_migrate_writes_the_backup_and_leaves_every_member_without_an_effort(self):
        members = self.home.rows("SELECT * FROM members ORDER BY id")
        events = self.home.rows("SELECT * FROM events ORDER BY id")
        out = self.home.json("migrate")
        self.assertEqual((out["applied"], out["user_version"]), (["0009_member_effort"], 9))
        self.assertEqual(len(out["backups"]), 1)
        self.assertRegex(out["backups"][0], r"/ledger-\d{8}T\d{6}-pre-0009_member_effort\.db$")
        backup = sqlite3.connect("file:%s?mode=ro" % out["backups"][0], uri=True)
        try:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 8)
            self.assertNotIn("effort", [r[1] for r in backup.execute("PRAGMA table_info(members)")])
        finally:
            backup.close()
        self.assertEqual(self.home.rows("SELECT * FROM members ORDER BY id"), [dict(m, effort=None, escalates_id=None) for m in members])
        self.assertEqual(self.home.rows("SELECT * FROM events ORDER BY id"), events)
        self.assertEqual(self.home.rows("PRAGMA foreign_key_check"), [])
        self.assertEqual([(r["name"], r["model"], r["effort"]) for r in self.home.rows("SELECT * FROM v_fleet ORDER BY id")],
                         [("Russet", "opus", None), ("Yukon", "haiku", None), ("Kestrel", "fable", None)])
        shown = self.home.json("member", "show", "SPUD-001/Russet")["member"]
        self.assertEqual((shown["effort"], shown["escalates"]), (None, None))
        self.assertEqual(self.home.json("migrate")["applied"], [])

    def test_the_columns_take_an_effort_level_and_a_member(self):
        self.home.json("migrate")
        con = self.home.connect()
        con.execute("PRAGMA foreign_keys = ON")  # as ledgerdb.open_connection sets it
        try:
            with con:
                con.execute("UPDATE members SET effort = 'xhigh', escalates_id = 20 WHERE id = 22")
            for column, value, error in (("effort", "extreme", "CHECK"), ("effort", "HIGH", "CHECK"), ("escalates_id", 99, "FOREIGN KEY")):
                with self.subTest(column=column, value=value):
                    with self.assertRaises(sqlite3.IntegrityError) as caught:
                        with con:
                            con.execute("UPDATE members SET %s = ? WHERE id = 21" % column, (value,))
                    self.assertIn(error, str(caught.exception))
        finally:
            con.close()
        self.assertEqual(self.home.scalar("SELECT effort FROM members WHERE id = 22"), "xhigh")

    def test_a_member_planned_after_it_carries_its_effort_and_its_escalation(self):
        self.home.json("migrate")
        m = self.home.json("member", "new", "--ticket", "SPD-001", "--persona", "engineer", "--model", "fable", "--escalates", "SPUD-001/Russet",
                           "--brief", "Read first: Russet's Result.", actor="spud")["member"]
        self.assertEqual((m["effort"], m["escalates"], m["tier_reason"]), ("high", "SPUD-001/Russet", "escalation after SPUD-001/Russet failed"))
        self.assertEqual(self.home.scalar("SELECT escalates_id FROM members WHERE name = ?", m["name"]), 20)


if __name__ == "__main__":
    unittest.main()
