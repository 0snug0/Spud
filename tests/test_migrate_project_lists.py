"""Migrations 0007_project_scripts (SPD-145) and 0008_project_runners (SPD-168): a project's two allow-lists.

Both are one plain ADD COLUMN of a JSON list on projects, and neither changes a view or a trigger, so one set of tests
runs on each (SPD-233 merged the two modules that repeated them).  A database at the version before the migration, built
here from the module's own DDL and the views and triggers of that version, with two projects, one archived and one
installed -- under 0008 the second already allowing a script -- is migrated by `spud migrate`: the pre-migration backup,
every project row and column kept, the earlier list with them, the new column '[]' on each, a CHECK that refuses anything
but a JSON list, and the events exactly as they were.
"""

import json
import sqlite3
import unittest

from test_migrate_pull_requests import V4_VIEWS_AND_TRIGGERS
from test_migrations import MigrationCase

# 0005 to 0008 changed no view, so the v6 and v7 views and triggers are the v4 ones.
V6_VIEWS_AND_TRIGGERS = V7_VIEWS_AND_TRIGGERS = V4_VIEWS_AND_TRIGGERS


class ProjectListTests:
    """What each list migration is tested for; a class below names the migration, its COLUMN, a VALUE the column takes,
    and project 2's ROOT and EXTRA columns, the lists an earlier migration added, set on the old database."""

    COLUMN = VALUE = ROOT = None
    EXTRA = {}

    def seed(self, con):
        at = self.AT
        columns = "".join(", " + column for column in self.EXTRA)
        con.execute("INSERT INTO projects (id, key, name, root_path, ticket_prefix, team_prefix, created_at, landing, sessions, installed, archived_at%s)"
                    " VALUES (2, 'badtakes', 'BadTakes', ?, 'BAD', 'BADS', ?, 'pr', 'claim', '{\"at\": \"x\"}', ?%s)" % (columns, ", ?" * len(self.EXTRA)),
                    (self.ROOT, at, at, *self.EXTRA.values()))
        con.execute("INSERT INTO tickets (id, project_id, number, key, team_key, title, priority, status, origin, created_at, updated_at)"
                    " VALUES (10, 1, 1, 'SPD-001', 'SPUD-001', 'One', 'P1', 'active', 'owner', ?, ?)", (at, at))
        con.execute("INSERT INTO events (at, actor, ticket_id, kind, body) VALUES (?, 'spud', 10, 'ticket.created', 'SPD-001 created')", (at,))

    def test_migrate_writes_the_backup_and_adds_an_empty_list_to_every_project(self):
        projects = self.home.rows("SELECT * FROM projects ORDER BY id")
        events = self.home.rows("SELECT * FROM events ORDER BY id")
        backup = self.assert_migrated(self.migrate())
        self.assertNotIn(self.COLUMN, [r[1] for r in backup.execute("PRAGMA table_info(projects)")])
        after = self.home.rows("SELECT * FROM projects ORDER BY id")
        self.assertEqual(self.as_before(after, projects), projects)
        self.assertEqual([r[self.COLUMN] for r in after], ["[]", "[]"])
        for column, value in self.EXTRA.items():
            self.assertEqual(after[1][column], value)
        self.assertEqual(self.as_before(self.home.rows("SELECT * FROM events ORDER BY id"), events), events)
        self.assertEqual(self.home.rows("PRAGMA foreign_key_check"), [])
        self.assertEqual(self.home.json("migrate")["applied"], [])

    def test_the_column_takes_a_json_list_and_nothing_else(self):
        self.migrate()
        con = self.home.connect()
        try:
            with con:
                con.execute("UPDATE projects SET %s = ? WHERE id = 1" % self.COLUMN, (json.dumps(self.VALUE),))
            for value in (self.VALUE[0], "{}", "3", "[1"):
                with self.subTest(value):
                    with self.assertRaises(sqlite3.IntegrityError) as caught:
                        with con:
                            con.execute("UPDATE projects SET %s = ? WHERE id = 1" % self.COLUMN, (value,))
                    self.assertIn("CHECK", str(caught.exception))
        finally:
            con.close()
        self.assertEqual(self.home.scalar("SELECT %s FROM projects WHERE id = 1" % self.COLUMN), json.dumps(self.VALUE))
        self.assertEqual(self.home.json("project", "show", "spud")["project"][self.COLUMN], self.VALUE)


class MigrateProjectScriptsTest(ProjectListTests, MigrationCase):
    MIGRATION, VERSION, VIEWS, AT = "0007_project_scripts", 6, V6_VIEWS_AND_TRIGGERS, "2026-09-22T10:00:00-07:00"
    COLUMN, VALUE, ROOT = "scripts", ["scripts/a.sh"], "/tmp/spd-145-badtakes"


class MigrateProjectRunnersTest(ProjectListTests, MigrationCase):
    MIGRATION, VERSION, VIEWS, AT = "0008_project_runners", 7, V7_VIEWS_AND_TRIGGERS, "2026-09-22T21:00:00-07:00"
    COLUMN, VALUE, ROOT = "runners", ["test", "check:functions"], "/tmp/spd-168-badtakes"
    EXTRA = {"scripts": '["scripts/worktree-init.sh"]'}


if __name__ == "__main__":
    unittest.main()
