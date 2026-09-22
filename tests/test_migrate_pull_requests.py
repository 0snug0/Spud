"""Migration 0005_pull_requests (SPD-077).

A v4 database, built here from the module's own DDL_0001 to DDL_0004 and the v4 views as they stood before the migration,
with a member, events, a handoff and a proposal pointing at its tickets, is migrated by `spud migrate`: the pre-migration
backup, the empty `pull_requests` table with its two indexes and its settled_at CHECK, `events` rebuilt with the two kinds
`pr.recorded` and `pr.state` its CHECK now accepts, every row and id kept, the foreign keys, the indexes and the
append-only triggers intact.  Forward only: a v4 database carries no pull request, because before this nothing recorded one.
"""

import sqlite3
import unittest

from helpers import EXIT_ERROR, Home, load_spud_module

spud = load_spud_module()

# The views and triggers of schema v4, verbatim from bin/spudlib/state/schema.py before SPD-077.
V4_VIEWS_AND_TRIGGERS = """
CREATE VIEW v_board AS
SELECT t.key, (SELECT pr.key FROM projects pr WHERE pr.id = t.project_id) AS project,
       t.status, t.parked_until, t.parked_reason, t.priority, t.title, l.name AS lead, t.origin, t.worktree,
       (SELECT t2.team_key || '/' || m.name
          FROM proposals p JOIN members m ON m.id = p.origin_member_id JOIN tickets t2 ON t2.id = m.ticket_id
         WHERE p.id = t.proposal_id) AS proposed_by,
       t.created_at, t.updated_at
  FROM tickets t LEFT JOIN members l ON l.id = t.lead_id
 ORDER BY CASE t.status WHEN 'active' THEN 1 WHEN 'queued' THEN 2 WHEN 'parked' THEN 3 WHEN 'done' THEN 4 ELSE 5 END,
          t.priority, t.id DESC;

CREATE VIEW v_fleet AS
SELECT t.key AS ticket, (SELECT pr.key FROM projects pr WHERE pr.id = t.project_id) AS project,
       t.team_key, m.lineage AS id, m.name, m.persona, m.agent_type, m.model,
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

AT = "2026-09-17T10:00:00-07:00"

TICKETS = [(1, "active", "P1", "Active one"), (2, "queued", "P2", "Queued one"), (3, "done", "P0", "Done one")]


class MigratePullRequestsTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.cleanup)
        self.home.db.parent.mkdir(parents=True)
        con = sqlite3.connect(self.home.db, autocommit=True)
        try:
            con.execute("PRAGMA journal_mode = WAL")
            for ddl in (spud.DDL_0001, spud.DDL_0002, spud.DDL_0003, spud.DDL_0004):
                con.executescript(ddl)
            con.executescript(V4_VIEWS_AND_TRIGGERS)
            con.execute("PRAGMA user_version = 4")
            con.execute("INSERT INTO projects (id, key, name, root_path, ticket_prefix, team_prefix, created_at) VALUES (1, 'spud', 'Spud', ?, 'SPD', 'SPUD', ?)",
                        (str(self.home.path), AT))
            for name in self.home.config["naming"]["pool"]:
                con.execute("INSERT INTO name_pool (name) VALUES (?)", (name,))
            for number, status, priority, title in TICKETS:
                con.execute("INSERT INTO tickets (project_id, number, key, team_key, title, priority, status, origin, created_at, updated_at)"
                            " VALUES (1, ?, ?, ?, ?, ?, ?, 'eric', ?, ?)",
                            (number, "SPD-%03d" % number, "SPUD-%03d" % number, title, priority, status, AT, AT))
            con.execute("INSERT INTO members (ticket_id, lineage, depth, name, persona, model, status, brief, deliverables, planned_at)"
                        " VALUES (1, '01', 1, 'Russet', 'engineer', 'opus', 'active', 'Build it.', '[\"bin/**\"]', ?)", (AT,))
            con.execute("UPDATE tickets SET lead_id = 1 WHERE id = 1")
            con.execute("INSERT INTO events (at, actor, ticket_id, member_id, kind, body) VALUES (?, 'spud', 1, 1, 'member.planned', 'planned Russet')", (AT,))
            con.execute("INSERT INTO events (at, actor, ticket_id, kind, body, data) VALUES (?, 'spud', 1, 'ticket.worktree', 'bound', '{}')", (AT,))
            con.execute("INSERT INTO handoffs (ticket_id, at, from_member_id, what) VALUES (1, ?, 1, 'the spike')", (AT,))
            con.execute("INSERT INTO proposals (ticket_id, origin_member_id, title, why, evidence, filed_at, status)"
                        " VALUES (1, 1, 'A proposal', 'because', 'the code', ?, 'open')", (AT,))
        finally:
            con.close()

    def migrate(self):
        return self.home.json("migrate")

    def test_the_cli_refuses_a_v4_database_until_migrate(self):
        proc = self.home.run("board", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("behind", proc.stderr)
        self.migrate()
        self.assertIn("SPD-001", self.home.run("board").stdout)

    def test_migrate_writes_the_backup_and_the_empty_table(self):
        tickets = self.home.rows("SELECT * FROM tickets ORDER BY id")
        events = self.home.rows("SELECT * FROM events ORDER BY id")
        out = self.migrate()
        self.assertEqual((out["applied"], out["user_version"]), (["0005_pull_requests", "0006_owner_origin"], 6))
        self.assertEqual(len(out["backups"]), 2)
        self.assertRegex(out["backups"][0], r"/ledger-\d{8}T\d{6}-pre-0005_pull_requests\.db$")
        backup = sqlite3.connect("file:%s?mode=ro" % out["backups"][0], uri=True)
        try:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 4)
        finally:
            backup.close()
        self.assertEqual(self.home.rows("SELECT * FROM tickets ORDER BY id"), [dict(r, origin="owner") for r in tickets])
        self.assertEqual(self.home.rows("SELECT * FROM events ORDER BY id"), events)
        self.assertEqual(self.home.rows("SELECT * FROM pull_requests"), [])
        self.assertEqual(self.home.rows("PRAGMA foreign_key_check"), [])
        self.assertEqual(self.home.json("migrate")["applied"], [])

    def test_the_table_is_strict_with_its_indexes_its_unique_url_and_its_settled_check(self):
        self.migrate()
        con = self.home.connect()
        con.execute("PRAGMA foreign_keys = ON")
        try:
            self.assertTrue(con.execute("SELECT sql FROM sqlite_master WHERE name = 'pull_requests'").fetchone()[0].rstrip().endswith("STRICT"))
            self.assertTrue({"pull_requests_ticket", "pull_requests_open"} <= {r[1] for r in con.execute("PRAGMA index_list(pull_requests)").fetchall()})
            with con:
                con.execute("INSERT INTO pull_requests (ticket_id, url, branch, recorded_at, recorded_by) VALUES (1, 'u', 'b', ?, 'spud')", (AT,))
            for label, statement, params in (
                ("a second row for the same url", "INSERT INTO pull_requests (ticket_id, url, branch, recorded_at, recorded_by) VALUES (2, 'u', 'b', ?, 'spud')", (AT,)),
                ("a ticket that does not exist", "INSERT INTO pull_requests (ticket_id, url, branch, recorded_at, recorded_by) VALUES (99, 'v', 'b', ?, 'spud')", (AT,)),
                ("a state nobody knows", "INSERT INTO pull_requests (ticket_id, url, branch, state, recorded_at, recorded_by) VALUES (1, 'w', 'b', 'draft', ?, 'spud')", (AT,)),
                ("open with a settled time", "INSERT INTO pull_requests (ticket_id, url, branch, state, settled_at, recorded_at, recorded_by) VALUES (1, 'x', 'b', 'open', ?, ?, 'spud')", (AT, AT)),
                ("merged without one", "INSERT INTO pull_requests (ticket_id, url, branch, state, recorded_at, recorded_by) VALUES (1, 'y', 'b', 'merged', ?, 'spud')", (AT,)),
            ):
                with self.subTest(label):
                    with self.assertRaises(sqlite3.IntegrityError):
                        with con:
                            con.execute(statement, params)
        finally:
            con.close()

    def test_the_events_check_accepts_the_two_new_kinds_and_nothing_unknown(self):
        self.migrate()
        con = self.home.connect()
        try:
            for kind in ("pr.recorded", "pr.state"):
                with self.subTest(kind):
                    with con:
                        con.execute("INSERT INTO events (at, actor, ticket_id, kind, body, data) VALUES (?, 'reconcile', 1, ?, 'x', '{}')", (AT, kind))
            with self.assertRaises(sqlite3.IntegrityError) as caught:
                with con:
                    con.execute("INSERT INTO events (at, actor, kind, body) VALUES (?, 'spud', 'pr.nonsense', 'x')", (AT,))
            self.assertIn("CHECK", str(caught.exception))
        finally:
            con.close()
        for kind in ("pr.recorded", "pr.state"):
            self.assertIn(kind, spud.EVENT_KINDS)

    def test_the_indexes_the_foreign_keys_and_the_append_only_triggers_survive(self):
        self.migrate()
        con = self.home.connect()
        con.execute("PRAGMA foreign_keys = ON")
        try:
            self.assertTrue({"events_ticket", "events_member", "events_agent", "events_kind"} <= {r[1] for r in con.execute("PRAGMA index_list(events)").fetchall()})
            self.assertIn("tickets_board", {r[1] for r in con.execute("PRAGMA index_list(tickets)").fetchall()})
            self.assertTrue(con.execute("SELECT sql FROM sqlite_master WHERE name = 'events'").fetchone()[0].rstrip().endswith("STRICT"))
            for statement in ("UPDATE events SET body = 'changed' WHERE id = 1", "DELETE FROM events WHERE id = 1"):
                with self.subTest(statement):
                    with self.assertRaises(sqlite3.DatabaseError) as caught:
                        with con:
                            con.execute(statement)
                    self.assertIn("append-only", str(caught.exception))
        finally:
            con.close()

    def test_the_two_views_still_resolve_after_the_rebuild(self):
        self.migrate()
        self.assertEqual(self.home.rows("SELECT key, worktree FROM v_board WHERE key = 'SPD-001'"), [{"key": "SPD-001", "worktree": None}])
        self.assertEqual([r["name"] for r in self.home.rows("SELECT name FROM v_fleet")], ["Russet"])

    def test_a_fresh_init_applies_them_all(self):
        other = Home()
        self.addCleanup(other.cleanup)
        out = other.init()
        self.assertEqual((out["applied"], out["user_version"], out["backups"]),
                         (["0001_init", "0002_projects", "0003_parked", "0004_ticket_worktree", "0005_pull_requests", "0006_owner_origin"], 6, []))
        self.assertEqual(other.scalar("SELECT count(*) FROM pull_requests"), 0)


if __name__ == "__main__":
    unittest.main()
