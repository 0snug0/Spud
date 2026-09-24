"""Migration 0010_member_resumed (SPD-082).

Since SPD-050 a spudagent resumed after it returned was recorded on the member.started of the SubagentStart that resumed it,
marked by data `resumed`.  From this migration on the resume has a kind of its own, member.resumed, written beside a plain
member.started (hooks/recording), and events is rebuilt so its CHECK takes it.

A v9 database, built here from the module's own DDL_0001 to DDL_0009 and the v9 views and triggers, holds two resumes in the
old shape: Russet's, resumed and still running when the migration runs, its first round's Result recorded before the resume;
and Yukon's, resumed after its parent recorded it.  `spud migrate` writes the pre-migration backup and copies every event as it
was written, those two included -- the log is append-only, so a resume recorded before the migration stays a member.started
with data resumed -- and the SubagentStop hold still keys on that resume (subagent_stop.last_resume reads both shapes).  A
resume after the migration writes a plain member.started and a member.resumed, and `spud events --kind member.resumed` finds it.
"""

import json
import sqlite3
import unittest

from helpers import load_spud_module
from hookcase import AGENT_A, AGENT_B, SESSION, common
from test_migrations import MigrationCase

spud = load_spud_module()

# The views and triggers of schema v9, verbatim from bin/spudlib/state/schema.py before SPD-082 (0009_member_effort named
# effort in v_fleet).
V9_VIEWS_AND_TRIGGERS = """
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
       t.team_key, m.lineage AS id, m.name, m.persona, m.agent_type, m.model, m.effort,
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

AT = "2026-09-24T09:00:00-07:00"
STOPPED = "2026-09-24T09:10:00-07:00"  # each member's first return
RESUMED = "2026-09-24T09:20:00-07:00"  # the SubagentStart that resumed it, recorded in the pre-0010 shape

# (id, lineage, name, persona, model, effort, agent_id, status, stopped_at, result, outcome): Russet resumed and still running
# (the resume cleared its return), Yukon resumed after Spud recorded it (the resume changed nothing but the event).
MEMBERS = ((20, "01", "Russet", "engineer", "opus", "high", AGENT_A, "active", None, "Round 1 done.", None),
           (21, "02", "Yukon", "scout", "haiku", None, AGENT_B, "done", STOPPED, "Found it.", "Good."))


def start_data(agent_type, **resume):
    return json.dumps(dict({"agent_type": agent_type, "session_id": SESSION, "cwd": "/somewhere"}, **resume))


class MigrateMemberResumedTest(MigrationCase):
    MIGRATION, VERSION, VIEWS, AT = "0010_member_resumed", 9, V9_VIEWS_AND_TRIGGERS, AT

    def seed(self, con):
        con.execute("INSERT INTO tickets (id, project_id, number, key, team_key, title, priority, status, origin, created_at, updated_at)"
                    " VALUES (10, 1, 1, 'SPD-001', 'SPUD-001', 'One', 'P1', 'active', 'owner', ?, ?)", (AT, AT))
        for member_id, lineage, name, persona, model, effort, agent_id, status, stopped, result, outcome in MEMBERS:
            con.execute("INSERT INTO members (id, ticket_id, lineage, depth, name, persona, model, effort, status, brief, planned_at, spawned_at,"
                        " stopped_at, finished_at, agent_id, session_id, result, outcome)"
                        " VALUES (?, 10, ?, 1, ?, ?, ?, ?, ?, 'Do it.', ?, ?, ?, ?, ?, ?, ?, ?)",
                        (member_id, lineage, name, persona, model, effort, status, AT, AT, stopped, STOPPED if status == "done" else None,
                         agent_id, SESSION, result, outcome))
        con.execute("UPDATE tickets SET lead_id = 20 WHERE id = 10")
        event = "INSERT INTO events (at, actor, ticket_id, member_id, agent_id, kind, body, data) VALUES (?, ?, 10, ?, ?, ?, ?, ?)"
        con.execute("INSERT INTO events (at, actor, ticket_id, kind, body) VALUES (?, 'spud', 10, 'ticket.created', 'SPD-001 created')", (AT,))
        for member_id, _, name, _, _, effort, agent_id, _, _, result, _ in MEMBERS:
            agent_type = "spudagent-" + effort if effort else "spudagent"
            con.execute(event, (AT, "spud", member_id, None, "member.planned", "planned " + name, None))
            con.execute(event, (AT, "hook:SubagentStart", member_id, agent_id, "member.started", "subagent %s started (%s)" % (agent_id, agent_type),
                                start_data(agent_type)))
            con.execute(event, (AT, "agent:" + agent_id, member_id, agent_id, "member.result", result, None))
            con.execute(event, (STOPPED, "hook:SubagentStop", member_id, agent_id, "member.stopped", "SPUD-001/%s stopped" % name,
                                json.dumps({"agent_type": agent_type, "held": False, "unrecorded": False})))
            cleared = member_id == 20
            con.execute(event, (RESUMED, "hook:SubagentStart", member_id, agent_id, "member.started",
                                "subagent %s resumed (%s) after returning at %s" % (agent_id, agent_type, STOPPED),
                                start_data(agent_type, resumed=True, was_stopped_at=STOPPED, cleared=cleared)))

    def old_resumes(self):
        return self.home.rows("SELECT id, member_id, kind FROM events WHERE json_extract(data, '$.resumed') = 1 ORDER BY id")

    def hook(self, event, agent_id, **fields):
        payload = common(str(self.home.path), session=SESSION)
        payload.update(dict(hook_event_name=event, agent_id=agent_id, **fields))
        return self.home.hook(event, payload)

    def test_migrate_writes_the_backup_and_copies_every_event_as_it_was_written(self):
        members = self.home.rows("SELECT * FROM members ORDER BY id")
        events = self.home.rows("SELECT * FROM events ORDER BY id")
        resumes = self.old_resumes()
        self.assertEqual([(r["member_id"], r["kind"]) for r in resumes], [(20, "member.started"), (21, "member.started")])
        backup = self.assert_migrated(self.migrate())
        self.assertNotIn("member.resumed", backup.execute("SELECT sql FROM sqlite_master WHERE name = 'events'").fetchone()[0])
        self.assertEqual(self.as_before(self.home.rows("SELECT * FROM events ORDER BY id"), events), events)  # ids, kinds, data: as written
        self.assertEqual(self.old_resumes(), resumes)  # the two resumes before it keep their kind and their flag
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events WHERE kind = 'member.resumed'"), 0)
        self.assertEqual(self.as_before(self.home.rows("SELECT * FROM members ORDER BY id"), members), members)
        self.assertEqual(self.home.rows("PRAGMA foreign_key_check"), [])
        self.assertEqual(self.home.json("migrate")["applied"], [])

    def test_the_events_check_takes_the_new_kind_and_nothing_unknown(self):
        self.migrate()
        self.assertIn("member.resumed", spud.EVENT_KINDS)
        con = self.home.connect()
        try:
            self.assertIn("'member.resumed'", con.execute("SELECT sql FROM sqlite_master WHERE name = 'events'").fetchone()[0])
            with con:
                con.execute("INSERT INTO events (at, actor, ticket_id, member_id, kind, body, data) VALUES (?, 'hook:SubagentStart', 10, 21, 'member.resumed', 'x', '{}')", (AT,))
            with self.assertRaises(sqlite3.IntegrityError) as caught:
                with con:
                    con.execute("INSERT INTO events (at, actor, kind, body) VALUES (?, 'spud', 'member.paused', 'x')", (AT,))
            self.assertIn("CHECK", str(caught.exception))
        finally:
            con.close()

    def test_the_indexes_the_append_only_triggers_and_the_views_survive(self):
        self.migrate()
        con = self.home.connect()
        con.execute("PRAGMA foreign_keys = ON")
        try:
            self.assertTrue({"events_ticket", "events_member", "events_agent", "events_kind"} <= {r[1] for r in con.execute("PRAGMA index_list(events)").fetchall()})
            self.assertTrue(con.execute("SELECT sql FROM sqlite_master WHERE name = 'events'").fetchone()[0].rstrip().endswith("STRICT"))
            for statement in ("UPDATE events SET kind = 'member.resumed' WHERE json_extract(data, '$.resumed') = 1", "DELETE FROM events WHERE id = 1"):
                with self.subTest(statement):
                    with self.assertRaises(sqlite3.DatabaseError) as caught:
                        with con:
                            con.execute(statement)
                    self.assertIn("append-only", str(caught.exception))
        finally:
            con.close()
        self.assertEqual([r["key"] for r in self.home.rows("SELECT key FROM v_board")], ["SPD-001"])
        self.assertEqual([(r["name"], r["effort"]) for r in self.home.rows("SELECT name, effort FROM v_fleet")], [("Russet", "high"), ("Yukon", None)])

    def test_the_subagent_stop_hold_still_keys_on_a_resume_recorded_before_the_migration(self):
        self.migrate()
        r = self.hook("SubagentStop", AGENT_A, agent_type="spudagent-high", stop_hook_active=False, last_assistant_message="Round 2.")
        self.assertEqual(r.code, 0, r)
        self.assertEqual(json.loads(r.stdout)["decision"], "block", r)  # Round 1's Result predates the resume: it does not answer for round 2
        self.assertIn("spud --as %s member result" % AGENT_A, json.loads(r.stdout)["reason"])
        self.assertIsNone(self.home.scalar("SELECT stopped_at FROM members WHERE id = 20"))
        self.home.json("member", "result", "Round 2 done.", actor=AGENT_A)
        r = self.hook("SubagentStop", AGENT_A, agent_type="spudagent-high", stop_hook_active=False, last_assistant_message="Round 2.")
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertIsNotNone(self.home.scalar("SELECT stopped_at FROM members WHERE id = 20"))

    def test_a_resume_after_the_migration_has_its_own_kind_and_events_finds_it(self):
        self.migrate()
        self.assertEqual(self.home.json("events", "--kind", "member.resumed")["events"], [])  # the two before it are member.started
        self.assertEqual(len([e for e in self.home.json("events", "--kind", "member.started")["events"] if e["data"].get("resumed")]), 2)
        r = self.hook("SubagentStart", AGENT_B, agent_type="spudagent")
        self.assertEqual(r.code, 0, r)
        kinds = [e["kind"] for e in self.home.json("events", "--member", "SPUD-001/Yukon")["events"]]
        self.assertEqual(kinds[-2:], ["member.started", spud.RESUME_KIND])
        resumed = self.home.json("events", "--kind", "member.resumed")["events"]
        self.assertEqual([(e["member"], e["agent_id"], e["actor"]) for e in resumed], [("SPUD-001/Yukon", AGENT_B, "hook:SubagentStart")])
        self.assertEqual((resumed[0]["data"]["was_stopped_at"], resumed[0]["data"]["cleared"]), (STOPPED, False))
        started = self.home.json("events", "--kind", "member.started", "--member", "SPUD-001/Yukon")["events"][-1]
        self.assertEqual(started["body"], "subagent %s started (spudagent)" % AGENT_B)
        self.assertNotIn("resumed", started["data"])


if __name__ == "__main__":
    unittest.main()
