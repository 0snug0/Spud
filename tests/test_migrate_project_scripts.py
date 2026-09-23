"""Migration 0007_project_scripts (SPD-145).

A v6 database, built here from the module's own DDL_0001 to DDL_0006 and the views and triggers (0007 changes none of
them), with two projects, one archived and one installed, is migrated by `spud migrate`: the pre-migration backup, every
project row and column kept, the new `scripts` column '[]' on each, a CHECK that refuses anything but a JSON list, and the
events exactly as they were.
"""

import json
import sqlite3
import unittest

from helpers import EXIT_ERROR, Home, load_spud_module

spud = load_spud_module()

AT = "2026-09-22T10:00:00-07:00"


class MigrateProjectScriptsTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.cleanup)
        self.home.db.parent.mkdir(parents=True)
        con = sqlite3.connect(self.home.db, autocommit=True)
        try:
            con.execute("PRAGMA journal_mode = WAL")
            for ddl in (spud.DDL_0001, spud.DDL_0002, spud.DDL_0003, spud.DDL_0004, spud.DDL_0005, spud.DDL_0006):
                con.executescript(ddl)
            con.executescript(spud.VIEWS_AND_TRIGGERS)
            con.execute("PRAGMA user_version = 6")
            con.execute("INSERT INTO projects (id, key, name, root_path, ticket_prefix, team_prefix, created_at) VALUES (1, 'spud', 'Spud', ?, 'SPD', 'SPUD', ?)",
                        (str(self.home.path), AT))
            con.execute("INSERT INTO projects (id, key, name, root_path, ticket_prefix, team_prefix, created_at, landing, sessions, installed, archived_at)"
                        " VALUES (2, 'badtakes', 'BadTakes', '/tmp/spd-145-badtakes', 'BAD', 'BADS', ?, 'pr', 'claim', '{\"at\": \"x\"}', ?)", (AT, AT))
            for name in self.home.config["naming"]["pool"]:
                con.execute("INSERT INTO name_pool (name) VALUES (?)", (name,))
            con.execute("INSERT INTO tickets (id, project_id, number, key, team_key, title, priority, status, origin, created_at, updated_at)"
                        " VALUES (10, 1, 1, 'SPD-001', 'SPUD-001', 'One', 'P1', 'active', 'owner', ?, ?)", (AT, AT))
            con.execute("INSERT INTO events (at, actor, ticket_id, kind, body) VALUES (?, 'spud', 10, 'ticket.created', 'SPD-001 created')", (AT,))
        finally:
            con.close()

    def test_the_cli_refuses_a_v6_database_until_migrate(self):
        proc = self.home.run("board", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("behind", proc.stderr)
        self.home.json("migrate")
        self.assertIn("SPD-001", self.home.run("board").stdout)

    def test_migrate_writes_the_backup_and_adds_an_empty_list_to_every_project(self):
        projects = self.home.rows("SELECT * FROM projects ORDER BY id")
        events = self.home.rows("SELECT * FROM events ORDER BY id")
        out = self.home.json("migrate")
        self.assertEqual((out["applied"], out["user_version"]), (["0007_project_scripts"], 7))
        self.assertEqual(len(out["backups"]), 1)
        self.assertRegex(out["backups"][0], r"/ledger-\d{8}T\d{6}-pre-0007_project_scripts\.db$")
        backup = sqlite3.connect("file:%s?mode=ro" % out["backups"][0], uri=True)
        try:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 6)
            self.assertNotIn("scripts", [r[1] for r in backup.execute("PRAGMA table_info(projects)")])
        finally:
            backup.close()
        after = self.home.rows("SELECT * FROM projects ORDER BY id")
        self.assertEqual(after, [dict(r, scripts="[]") for r in projects])
        self.assertEqual(self.home.rows("SELECT * FROM events ORDER BY id"), events)
        self.assertEqual(self.home.rows("PRAGMA foreign_key_check"), [])
        self.assertEqual(self.home.json("migrate")["applied"], [])

    def test_the_column_takes_a_json_list_and_nothing_else(self):
        self.home.json("migrate")
        con = self.home.connect()
        try:
            with con:
                con.execute("UPDATE projects SET scripts = ? WHERE id = 1", (json.dumps(["scripts/a.sh"]),))
            for value in ("scripts/a.sh", "{}", "3", "[1"):
                with self.subTest(value):
                    with self.assertRaises(sqlite3.IntegrityError) as caught:
                        with con:
                            con.execute("UPDATE projects SET scripts = ? WHERE id = 1", (value,))
                    self.assertIn("CHECK", str(caught.exception))
        finally:
            con.close()
        self.assertEqual(self.home.scalar("SELECT scripts FROM projects WHERE id = 1"), '["scripts/a.sh"]')
        self.assertEqual(self.home.json("project", "show", "spud")["project"]["scripts"], ["scripts/a.sh"])


if __name__ == "__main__":
    unittest.main()
