"""Migration 0004_ticket_worktree (SPD-098, the home and tool split design's section 6).

A v3 database, built here from the module's own DDL_0001 to DDL_0003 and the v3 views as they stood before the migration,
with a member, events, a handoff and a proposal pointing at its tickets, is migrated by `spud migrate`: the pre-migration
backup, `tickets.worktree` added and NULL on every existing ticket (unbound: a ticket planned before SPD-098 keeps
today's path rule), `events` rebuilt with the kind `ticket.worktree` its CHECK now accepts, every row and id kept, the
foreign keys, the indexes and the append-only triggers intact.
"""

import sqlite3
import unittest

from helpers import load_spud_module
from test_migrations import MigrationCase

spud = load_spud_module()

# The views and triggers of schema v3, verbatim from bin/spudlib/state/schema.py before SPD-098.
V3_VIEWS_AND_TRIGGERS = """
CREATE VIEW v_board AS
SELECT t.key, (SELECT pr.key FROM projects pr WHERE pr.id = t.project_id) AS project,
       t.status, t.parked_until, t.parked_reason, t.priority, t.title, l.name AS lead, t.origin,
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

AT = "2026-09-16T10:00:00-07:00"

TICKETS = [(1, "active", "P1", "Active one"), (2, "queued", "P2", "Queued one"), (3, "done", "P0", "Done one")]


class MigrateTicketWorktreeTest(MigrationCase):
    MIGRATION, VERSION, VIEWS, AT = "0004_ticket_worktree", 3, V3_VIEWS_AND_TRIGGERS, AT

    def seed(self, con):
        for number, status, priority, title in TICKETS:
            con.execute("INSERT INTO tickets (project_id, number, key, team_key, title, priority, status, origin, created_at, updated_at)"
                        " VALUES (1, ?, ?, ?, ?, ?, ?, 'eric', ?, ?)",
                        (number, "SPD-%03d" % number, "SPUD-%03d" % number, title, priority, status, AT, AT))
        con.execute("INSERT INTO members (ticket_id, lineage, depth, name, persona, model, status, brief, deliverables, planned_at)"
                    " VALUES (1, '01', 1, 'Russet', 'engineer', 'opus', 'active', 'Build it.', '[\"bin/**\"]', ?)", (AT,))
        con.execute("UPDATE tickets SET lead_id = 1 WHERE id = 1")
        con.execute("INSERT INTO events (at, actor, ticket_id, member_id, kind, body) VALUES (?, 'spud', 1, 1, 'member.planned', 'planned Russet')", (AT,))
        con.execute("INSERT INTO events (at, actor, kind, body, data) VALUES (?, 'spud', 'session.claimed', 'claimed', '{\"session_id\": \"s\"}')", (AT,))
        con.execute("INSERT INTO handoffs (ticket_id, at, from_member_id, what) VALUES (1, ?, 1, 'the spike')", (AT,))
        con.execute("INSERT INTO proposals (ticket_id, origin_member_id, title, why, evidence, filed_at, status)"
                    " VALUES (1, 1, 'A proposal', 'because', 'the code', ?, 'open')", (AT,))

    def test_migrate_writes_the_backup_and_leaves_every_ticket_unbound(self):
        tickets = self.home.rows("SELECT * FROM tickets ORDER BY id")
        events = self.home.rows("SELECT * FROM events ORDER BY id")
        self.assert_migrated(self.migrate())
        after = self.home.rows("SELECT * FROM tickets ORDER BY id")
        # 0006_owner_origin, later in the chain, writes 'eric' as 'owner'
        self.assertEqual(self.as_before(after, tickets), [dict(r, origin="owner") if r["origin"] == "eric" else r for r in tickets])
        self.assertEqual([r["worktree"] for r in after], [None, None, None])
        self.assertEqual(self.as_before(self.home.rows("SELECT * FROM events ORDER BY id"), events), events)
        self.assertEqual(self.home.rows("PRAGMA foreign_key_check"), [])
        self.assertEqual(self.home.json("migrate")["applied"], [])

    def test_the_events_check_accepts_ticket_worktree_and_nothing_unknown(self):
        self.migrate()
        con = self.home.connect()
        try:
            with con:
                con.execute("INSERT INTO events (at, actor, ticket_id, kind, body, data) VALUES (?, 'spud', 1, 'ticket.worktree', 'bound', '{}')", (AT,))
            with self.assertRaises(sqlite3.IntegrityError) as caught:
                with con:
                    con.execute("INSERT INTO events (at, actor, kind, body) VALUES (?, 'spud', 'ticket.nonsense', 'x')", (AT,))
            self.assertIn("CHECK", str(caught.exception))
        finally:
            con.close()
        self.assertIn("ticket.worktree", spud.EVENT_KINDS)

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
            with self.assertRaises(sqlite3.IntegrityError):
                with con:
                    con.execute("INSERT INTO events (at, actor, ticket_id, kind) VALUES (?, 'spud', 999, 'ticket.edited')", (AT,))
        finally:
            con.close()

    def test_v_board_names_the_worktree(self):
        self.migrate()
        self.assertEqual(self.home.rows("SELECT key, worktree FROM v_board WHERE key = 'SPD-001'"), [{"key": "SPD-001", "worktree": None}])


if __name__ == "__main__":
    unittest.main()
