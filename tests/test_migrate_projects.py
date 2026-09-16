"""Migration 0002_projects (SPD-014, docs/design/2026-09-14-cross-repository-projects.md section 8).

A v1 database, built here from the module's own DDL_0001 and the v1 views as they stood before the migration, with rows in
it, is migrated by `spud migrate`: the pre-migration backup, project 1's defaults, the sessions table, the widened event
kinds on the rebuilt append-only events table, and the views that carry `project`.  The design marks `ADD COLUMN` with a
CHECK on a STRICT table as assumed for SQLite 3.53.4; the CHECK tests below settle it.
"""

import json
import sqlite3
import unittest

from helpers import EXIT_ERROR, Home, load_spud_module

spud = load_spud_module()

# The views and triggers of schema v1, verbatim from bin/spud before SPD-014.
V1_VIEWS_AND_TRIGGERS = """
CREATE VIEW v_board AS
SELECT t.key, t.status, t.priority, t.title, l.name AS lead, t.origin,
       (SELECT t2.team_key || '/' || m.name
          FROM proposals p JOIN members m ON m.id = p.origin_member_id JOIN tickets t2 ON t2.id = m.ticket_id
         WHERE p.id = t.proposal_id) AS proposed_by,
       t.created_at, t.updated_at
  FROM tickets t LEFT JOIN members l ON l.id = t.lead_id
 ORDER BY CASE t.status WHEN 'active' THEN 1 WHEN 'queued' THEN 2 WHEN 'done' THEN 3 ELSE 4 END,
          t.priority, t.id DESC;
CREATE VIEW v_fleet AS
SELECT t.key AS ticket, t.team_key, m.lineage AS id, m.name, m.persona, m.agent_type, m.model,
       m.resolved_model, m.status, COALESCE(p.name, 'Spud') AS parent,
       m.spawned_at, m.finished_at, m.total_tokens, m.duration_ms, m.tool_uses
  FROM members m JOIN tickets t ON t.id = m.ticket_id LEFT JOIN members p ON p.id = m.parent_id
 ORDER BY t.id DESC, m.lineage;
CREATE VIEW v_live AS SELECT count(*) AS live FROM members WHERE status IN ('planned','active');
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
  WHEN NOT (OLD.member_id IS NULL AND NEW.member_id IS NOT NULL
            AND NEW.id = OLD.id AND NEW.at = OLD.at AND NEW.actor = OLD.actor
            AND (OLD.ticket_id IS NULL OR NEW.ticket_id IS OLD.ticket_id)
            AND NEW.agent_id IS OLD.agent_id AND NEW.kind = OLD.kind
            AND NEW.body = OLD.body AND NEW.data IS OLD.data)
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
"""

AT = "2026-09-12T10:00:00-07:00"


class MigrateProjectsTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.cleanup)
        self.home.db.parent.mkdir(parents=True)
        con = sqlite3.connect(self.home.db, autocommit=True)
        try:
            con.execute("PRAGMA journal_mode = WAL")
            con.executescript(spud.DDL_0001)
            con.executescript(V1_VIEWS_AND_TRIGGERS)
            con.execute("PRAGMA user_version = 1")
            con.execute("INSERT INTO projects (id, key, name, root_path, ticket_prefix, team_prefix, created_at) VALUES (1, 'spud', 'Spud', ?, 'SPD', 'SPUD', ?)",
                        (str(self.home.path), AT))
            for name in self.home.config["naming"]["pool"]:
                con.execute("INSERT INTO name_pool (name) VALUES (?)", (name,))
            con.execute("INSERT INTO tickets (project_id, number, key, team_key, title, priority, status, origin, created_at, updated_at)"
                        " VALUES (1, 1, 'SPD-001', 'SPUD-001', 'Old ticket', 'P1', 'active', 'eric', ?, ?)", (AT, AT))
            con.execute("INSERT INTO members (ticket_id, lineage, depth, name, persona, model, status, brief, planned_at)"
                        " VALUES (1, '01', 1, 'Russet', 'scout', 'haiku', 'planned', 'Do it.', ?)", (AT,))
            for kind, member in (("ticket.created", None), ("member.planned", 1), ("commit", None), ("report.entry", None)):
                con.execute("INSERT INTO events (at, actor, ticket_id, member_id, kind, body, data) VALUES (?, 'spud', 1, ?, ?, ?, ?)",
                            (AT, member, kind, "old " + kind, json.dumps({"title": "Old"}) if kind == "report.entry" else None))
        finally:
            con.close()

    def migrate(self):
        return self.home.json("migrate")

    def test_the_cli_refuses_a_v1_database_until_migrate(self):
        proc = self.home.run("board", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("behind", proc.stderr)
        self.migrate()
        self.assertIn("SPD-001", self.home.run("board").stdout)

    def test_migrate_writes_the_pre_migration_backup_and_keeps_every_row(self):
        events_before = self.home.rows("SELECT * FROM events ORDER BY id")
        out = self.migrate()
        self.assertEqual((out["applied"], out["user_version"]), (["0002_projects", "0003_parked"], 3))
        self.assertEqual(len(out["backups"]), 2)
        self.assertRegex(out["backups"][0], r"/ledger-\d{8}T\d{6}-pre-0002_projects\.db$")
        backup = sqlite3.connect("file:%s?mode=ro" % out["backups"][0], uri=True)
        try:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(backup.execute("SELECT count(*) FROM events").fetchone()[0], len(events_before))
        finally:
            backup.close()
        self.assertEqual(self.home.rows("SELECT * FROM events ORDER BY id"), events_before)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM tickets WHERE project_id = 1"), 1)
        self.assertEqual(self.home.rows("SELECT default_branch, landing, sessions, installed, archived_at FROM projects WHERE id = 1"),
                         [{"default_branch": "main", "landing": "merge", "sessions": "always", "installed": None, "archived_at": None}])
        self.assertEqual(self.home.json("migrate")["applied"], [])

    def test_the_sessions_table_and_the_new_event_kinds(self):
        self.migrate()
        con = self.home.connect()
        con.execute("PRAGMA foreign_keys = ON")  # as the CLI's connections have it
        try:
            sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'sessions'").fetchone()[0]
            self.assertTrue(sql.rstrip().endswith("STRICT"), sql)
            with con:
                con.execute("INSERT INTO sessions (session_id, project_id, claimed_at, cwd) VALUES ('s1', 1, ?, '/x')", (AT,))
                for kind in ("project.added", "project.edited", "project.installed", "project.uninstalled", "project.removed", "session.claimed", "session.released"):
                    con.execute("INSERT INTO events (at, actor, kind, body) VALUES (?, 'spud', ?, '')", (AT, kind))
            with self.assertRaises(sqlite3.IntegrityError):
                with con:
                    con.execute("INSERT INTO events (at, actor, kind, body) VALUES (?, 'spud', 'no.such.kind', '')", (AT,))
            with self.assertRaises(sqlite3.IntegrityError):
                with con:
                    con.execute("INSERT INTO sessions (session_id, project_id, claimed_at) VALUES ('s2', 99, ?)", (AT,))
        finally:
            con.close()
        self.assertEqual(sorted(spud.EVENT_KINDS), sorted(set(spud.EVENT_KINDS)))

    def test_the_rebuilt_events_table_is_still_append_only_and_indexed(self):
        self.migrate()
        con = self.home.connect()
        try:
            for statement in ("UPDATE events SET body = 'changed' WHERE id = 1", "DELETE FROM events WHERE id = 1"):
                with self.subTest(statement):
                    with self.assertRaises(sqlite3.DatabaseError) as caught:
                        with con:
                            con.execute(statement)
                    self.assertIn("append-only", str(caught.exception))
            indexes = {r[1] for r in con.execute("PRAGMA index_list(events)").fetchall()}
            self.assertLessEqual({"events_ticket", "events_member", "events_agent", "events_kind"}, indexes)
            self.assertTrue(con.execute("SELECT sql FROM sqlite_master WHERE name = 'events'").fetchone()[0].rstrip().endswith("STRICT"))
        finally:
            con.close()

    def test_the_views_carry_project(self):
        self.migrate()
        self.assertEqual(self.home.scalar("SELECT project FROM v_board WHERE key = 'SPD-001'"), "spud")
        self.assertEqual(self.home.scalar("SELECT project FROM v_fleet WHERE name = 'Russet'"), "spud")
        self.assertEqual(self.home.json("board")["tickets"][0]["project"], "spud")

    def test_add_column_with_check_on_a_strict_table_enforces_the_check(self):
        """The design's [assumed] claim: SQLite accepts ADD COLUMN with a CHECK on a STRICT table and enforces it."""
        self.migrate()
        con = self.home.connect()
        try:
            for column, value in (("landing", "squash"), ("sessions", "sometimes"), ("installed", "not json")):
                with self.subTest(column=column):
                    with self.assertRaises(sqlite3.IntegrityError) as caught:
                        with con:
                            con.execute("UPDATE projects SET %s = ? WHERE id = 1" % column, (value,))
                    self.assertIn("CHECK", str(caught.exception))
            with con:
                con.execute("UPDATE projects SET landing = 'pr', sessions = 'claim', installed = '{}' WHERE id = 1")
        finally:
            con.close()

    def test_render_after_migrate_names_the_project_in_every_note(self):
        self.migrate()
        out = self.home.json("render", actor="spud")
        self.assertIn("ledger/Projects.md", out["written"])
        ticket = (self.home.path / "ledger" / "tickets" / "SPD-001.md").read_text(encoding="utf-8")
        self.assertIn("\norigin: eric\nproject: spud\n", ticket)
        member = (self.home.path / "ledger" / "teams" / "SPUD-001" / "Russet.md").read_text(encoding="utf-8")
        self.assertIn('\nticket: "[[SPD-001]]"\nproject: spud\n', member)

    def test_a_fresh_init_applies_both_migrations(self):
        other = Home()
        self.addCleanup(other.cleanup)
        out = other.init()
        self.assertEqual((out["applied"], out["user_version"], out["backups"]), (["0001_init", "0002_projects", "0003_parked"], 3, []))
        self.assertEqual(other.rows("SELECT key, landing, sessions FROM projects"), [{"key": "spud", "landing": "merge", "sessions": "always"}])


if __name__ == "__main__":
    unittest.main()
