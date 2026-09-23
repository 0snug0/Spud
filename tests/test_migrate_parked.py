"""Migration 0003_parked (SPD-096, docs/design/2026-09-16-parked-tickets.md section 4.1).

A v2 database, built here from the module's own DDL_0001 and DDL_0002 and the v2 views as they stood before the
migration, with a member, an event, a handoff and a proposal pointing at its tickets, is migrated by `spud migrate`:
the pre-migration backup, the rebuilt `tickets` with the fifth status and its two qualifying columns, every row and id
kept, the foreign keys intact, the index back, and `v_board` sorting parked after queued.  The design's probe ran this
DDL in four settings before it was written; these are the same assertions on the shipped one.
"""

import sqlite3
import unittest

from helpers import EXIT_ERROR, Home, load_spud_module

spud = load_spud_module()

# The views and triggers of schema v2, verbatim from bin/spudlib/state/schema.py before SPD-096.
V2_VIEWS_AND_TRIGGERS = """
CREATE VIEW v_board AS
SELECT t.key, (SELECT pr.key FROM projects pr WHERE pr.id = t.project_id) AS project,
       t.status, t.priority, t.title, l.name AS lead, t.origin,
       (SELECT t2.team_key || '/' || m.name
          FROM proposals p JOIN members m ON m.id = p.origin_member_id JOIN tickets t2 ON t2.id = m.ticket_id
         WHERE p.id = t.proposal_id) AS proposed_by,
       t.created_at, t.updated_at
  FROM tickets t LEFT JOIN members l ON l.id = t.lead_id
 ORDER BY CASE t.status WHEN 'active' THEN 1 WHEN 'queued' THEN 2 WHEN 'done' THEN 3 ELSE 4 END,
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

AT = "2026-09-15T10:00:00-07:00"

# (number, status, priority, title) — one of every v2 status, so the new ORDER BY is visible
TICKETS = [(1, "active", "P1", "Active one"), (2, "queued", "P2", "Queued one"), (3, "done", "P0", "Done one"),
           (4, "declined", "P3", "Declined one")]


class MigrateParkedTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.cleanup)
        self.home.db.parent.mkdir(parents=True)
        con = sqlite3.connect(self.home.db, autocommit=True)
        try:
            con.execute("PRAGMA journal_mode = WAL")
            con.executescript(spud.DDL_0001)
            con.executescript(spud.DDL_0002)
            con.executescript(V2_VIEWS_AND_TRIGGERS)
            con.execute("PRAGMA user_version = 2")
            con.execute("INSERT INTO projects (id, key, name, root_path, ticket_prefix, team_prefix, created_at) VALUES (1, 'spud', 'Spud', ?, 'SPD', 'SPUD', ?)",
                        (str(self.home.path), AT))
            for name in self.home.config["naming"]["pool"]:
                con.execute("INSERT INTO name_pool (name) VALUES (?)", (name,))
            for number, status, priority, title in TICKETS:
                con.execute("INSERT INTO tickets (project_id, number, key, team_key, title, priority, status, origin, created_at, updated_at)"
                            " VALUES (1, ?, ?, ?, ?, ?, ?, 'eric', ?, ?)",
                            (number, "SPD-%03d" % number, "SPUD-%03d" % number, title, priority, status, AT, AT))
            con.execute("INSERT INTO members (ticket_id, lineage, depth, name, persona, model, status, brief, planned_at)"
                        " VALUES (1, '01', 1, 'Russet', 'scout', 'haiku', 'planned', 'Do it.', ?)", (AT,))
            con.execute("UPDATE tickets SET lead_id = 1 WHERE id = 1")
            con.execute("INSERT INTO events (at, actor, ticket_id, member_id, kind, body) VALUES (?, 'spud', 1, 1, 'member.planned', 'planned Russet')", (AT,))
            con.execute("INSERT INTO handoffs (ticket_id, at, from_member_id, what) VALUES (1, ?, 1, 'the spike')", (AT,))
            con.execute("INSERT INTO proposals (ticket_id, origin_member_id, title, why, evidence, filed_at, status)"
                        " VALUES (1, 1, 'A proposal', 'because', 'the code', ?, 'open')", (AT,))
            con.execute("UPDATE tickets SET origin = 'proposal', proposal_id = 1 WHERE id = 2")
            con.execute("INSERT INTO proposal_decisions (proposal_id, at, decision, reason) VALUES (1, ?, 'escalate', 'Spud decides')", (AT,))
        finally:
            con.close()

    def migrate(self):
        return self.home.json("migrate")

    def park(self, key, reason, until=None):
        args = ["ticket", "move", key, "--status", "parked", "--reason", reason]
        return self.home.json(*args, *(["--until", until] if until else []), actor="spud")["ticket"]

    def test_the_cli_refuses_a_v2_database_until_migrate(self):
        proc = self.home.run("board", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("behind", proc.stderr)
        self.migrate()
        self.assertIn("SPD-001", self.home.run("board").stdout)

    def test_migrate_writes_the_pre_migration_backup_and_keeps_every_row_and_id(self):
        before = self.home.rows("SELECT * FROM tickets ORDER BY id")
        out = self.migrate()
        self.assertEqual((out["applied"], out["user_version"]), (["0003_parked", "0004_ticket_worktree", "0005_pull_requests", "0006_owner_origin", "0007_project_scripts"], 7))
        self.assertEqual(len(out["backups"]), 5)
        self.assertRegex(out["backups"][0], r"/ledger-\d{8}T\d{6}-pre-0003_parked\.db$")
        backup = sqlite3.connect("file:%s?mode=ro" % out["backups"][0], uri=True)
        try:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(backup.execute("SELECT count(*) FROM tickets").fetchone()[0], len(TICKETS))
        finally:
            backup.close()
        after = self.home.rows("SELECT * FROM tickets ORDER BY id")
        self.assertEqual([{k: v for k, v in r.items() if k not in ("parked_until", "parked_reason", "worktree")} for r in after], [dict(r, origin="owner") if r["origin"] == "eric" else r for r in before])
        self.assertTrue(all(r["parked_until"] is None and r["parked_reason"] is None for r in after))
        self.assertEqual(self.home.rows("PRAGMA foreign_key_check"), [])
        self.assertEqual(self.home.scalar("SELECT count(*) FROM handoffs"), 1)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM proposals"), 1)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM proposal_decisions"), 1)
        self.assertEqual(self.home.json("migrate")["applied"], [])

    def test_the_foreign_keys_still_hold_after_the_migration(self):
        self.migrate()
        self.assertEqual({r["table"] for r in self.home.rows("PRAGMA foreign_key_list(members)")}, {"tickets", "members"})
        con = self.home.connect()
        con.execute("PRAGMA foreign_keys = ON")  # as the CLI's connections have it
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                with con:
                    con.execute("INSERT INTO members (ticket_id, lineage, depth, name, persona, model, status, planned_at)"
                                " VALUES (999, '02', 1, 'Yukon', 'scout', 'haiku', 'planned', ?)", (AT,))
            with self.assertRaises(sqlite3.IntegrityError):
                with con:
                    con.execute("INSERT INTO tickets (project_id, number, key, team_key, title, priority, status, origin, created_at, updated_at)"
                                " VALUES (99, 9, 'SPD-009', 'SPUD-009', 'x', 'P2', 'queued', 'eric', ?, ?)", (AT, AT))
        finally:
            con.close()

    def test_the_database_refuses_every_shape_the_checks_forbid(self):
        self.migrate()
        con = self.home.connect()
        try:
            for what, sql in (
                ("parked without a reason", "UPDATE tickets SET status = 'parked' WHERE id = 2"),
                ("a reason while queued", "UPDATE tickets SET parked_reason = 'why' WHERE id = 2"),
                ("an until while queued", "UPDATE tickets SET parked_until = '2026-10-16' WHERE id = 2"),
                ("an until that is not YYYY-MM-DD", "UPDATE tickets SET status = 'parked', parked_reason = 'why', parked_until = 'soon' WHERE id = 2"),
                ("a sixth status", "UPDATE tickets SET status = 'shelved' WHERE id = 2"),
            ):
                with self.subTest(what):
                    with self.assertRaises(sqlite3.IntegrityError) as caught:
                        with con:
                            con.execute(sql)
                    self.assertIn("CHECK", str(caught.exception))
            with con:  # parked with a reason and no date, and an unpark that clears both, are accepted
                con.execute("UPDATE tickets SET status = 'parked', parked_reason = 'why' WHERE id = 2")
            with self.assertRaises(sqlite3.IntegrityError):  # an unpark that keeps its reason
                with con:
                    con.execute("UPDATE tickets SET status = 'queued' WHERE id = 2")
            with con:
                con.execute("UPDATE tickets SET status = 'queued', parked_reason = NULL, parked_until = NULL WHERE id = 2")
        finally:
            con.close()

    def test_the_index_and_the_append_only_triggers_survive_the_rebuild(self):
        self.migrate()
        con = self.home.connect()
        try:
            self.assertIn("tickets_board", {r[1] for r in con.execute("PRAGMA index_list(tickets)").fetchall()})
            self.assertTrue(con.execute("SELECT sql FROM sqlite_master WHERE name = 'tickets'").fetchone()[0].rstrip().endswith("STRICT"))
            for statement in ("UPDATE events SET body = 'changed' WHERE id = 1", "DELETE FROM events WHERE id = 1"):
                with self.subTest(statement):
                    with self.assertRaises(sqlite3.DatabaseError) as caught:
                        with con:
                            con.execute(statement)
                    self.assertIn("append-only", str(caught.exception))
        finally:
            con.close()

    def test_v_board_sorts_parked_after_queued_and_before_done(self):
        self.migrate()
        self.park("SPD-002", "waiting on Eric")
        self.assertEqual([r["key"] for r in self.home.rows("SELECT key, status FROM v_board")],
                         ["SPD-001", "SPD-002", "SPD-003", "SPD-004"])
        self.assertEqual(self.home.rows("SELECT status, parked_until, parked_reason FROM v_board WHERE key = 'SPD-002'"),
                         [{"status": "parked", "parked_until": None, "parked_reason": "waiting on Eric"}])
        # with a queued ticket of its own, the parked one sorts after it whatever the priority
        self.home.json("ticket", "new", "--title", "Fresh", "--priority", "P3", actor="spud")
        self.assertEqual([r["key"] for r in self.home.rows("SELECT key FROM v_board WHERE status IN ('queued','parked')")],
                         ["SPD-005", "SPD-002"])

    def test_a_fresh_init_applies_every_migration(self):
        other = Home()
        self.addCleanup(other.cleanup)
        out = other.init()
        self.assertEqual((out["applied"], out["user_version"], out["backups"]), (["0001_init", "0002_projects", "0003_parked", "0004_ticket_worktree", "0005_pull_requests", "0006_owner_origin", "0007_project_scripts"], 7, []))
        self.assertEqual(other.scalar("SELECT count(*) FROM pragma_table_info('tickets') WHERE name IN ('parked_until','parked_reason')"), 2)


if __name__ == "__main__":
    unittest.main()
