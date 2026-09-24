"""Migration 0009_member_effort (SPD-222).

A v8 database, built here from the module's own DDL_0001 to DDL_0008 and the v8 views and triggers, with three members on
three models -- one of them a failed opus engineer -- is migrated by `spud migrate`: the pre-migration backup, every member
row and column kept, the two new columns NULL on each (nobody recorded the effort a member ran at before, nor an
escalation), a CHECK that takes only Claude Code's effort levels, a foreign key that takes only a member, `v_fleet` naming
the effort, and the events exactly as they were.  A member planned after the migration carries both.
"""

import sqlite3
import unittest

from test_migrate_pull_requests import V4_VIEWS_AND_TRIGGERS
from test_migrations import MigrationCase

# 0005 to 0008 changed no view, so the v8 views and triggers are the v4 ones: v_fleet without effort.
V8_VIEWS_AND_TRIGGERS = V4_VIEWS_AND_TRIGGERS

AT = "2026-09-23T18:00:00-07:00"
MEMBERS = ((20, "01", "Russet", "engineer", "opus", "failed"), (21, "02", "Yukon", "scout", "haiku", "done"),
           (22, "03", "Kestrel", "reviewer", "fable", "done"))


class MigrateMemberEffortTest(MigrationCase):
    MIGRATION, VERSION, VIEWS, AT = "0009_member_effort", 8, V8_VIEWS_AND_TRIGGERS, AT

    def seed(self, con):
        con.execute("INSERT INTO tickets (id, project_id, number, key, team_key, title, priority, status, origin, created_at, updated_at)"
                    " VALUES (10, 1, 1, 'SPD-001', 'SPUD-001', 'One', 'P1', 'active', 'owner', ?, ?)", (AT, AT))
        for member_id, lineage, name, persona, model, status in MEMBERS:
            con.execute("INSERT INTO members (id, ticket_id, lineage, depth, name, persona, model, status, brief, planned_at, finished_at, result)"
                        " VALUES (?, 10, ?, 1, ?, ?, ?, ?, 'Do it.', ?, ?, ?)",
                        (member_id, lineage, name, persona, model, status, AT, AT, "It did not build." if status == "failed" else None))
        con.execute("UPDATE tickets SET lead_id = 20 WHERE id = 10")
        con.execute("INSERT INTO events (at, actor, ticket_id, kind, body) VALUES (?, 'spud', 10, 'ticket.created', 'SPD-001 created')", (AT,))
        con.execute("INSERT INTO events (at, actor, ticket_id, member_id, kind, body) VALUES (?, 'spud', 10, 20, 'member.planned', 'planned Russet')", (AT,))

    def test_migrate_writes_the_backup_and_leaves_every_member_without_an_effort(self):
        members = self.home.rows("SELECT * FROM members ORDER BY id")
        events = self.home.rows("SELECT * FROM events ORDER BY id")
        backup = self.assert_migrated(self.migrate())
        self.assertNotIn("effort", [r[1] for r in backup.execute("PRAGMA table_info(members)")])
        after = self.home.rows("SELECT * FROM members ORDER BY id")
        self.assertEqual(self.as_before(after, members), members)
        self.assertEqual([(r["effort"], r["escalates_id"]) for r in after], [(None, None)] * len(MEMBERS))
        self.assertEqual(self.as_before(self.home.rows("SELECT * FROM events ORDER BY id"), events), events)
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
