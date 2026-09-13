"""member new|start|log|result|block|finish|edit, the limits, lineage, the
name draw, ownership, the member state machine, card and fleet."""

import copy
import json
import re
import unittest

from helpers import (
    EXIT_ERROR,
    EXIT_LIMIT,
    EXIT_OWNERSHIP,
    EXIT_TRANSITION,
    SpudTestCase,
    real_config,
)

ISO_WITH_OFFSET = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


class MemberBasicsTest(SpudTestCase):
    def test_new_member_is_planned_with_lineage_name_and_lead(self):
        t = self.new_ticket("Team")
        m = self.new_member(t["key"])
        pool = real_config()["naming"]["pool"]
        self.assertEqual(m["lineage"], "01")
        self.assertEqual(m["depth"], 1)
        self.assertEqual(m["status"], "planned")
        self.assertIn(m["name"], pool)
        self.assertEqual(m["ref"], "SPUD-001/" + m["name"])
        self.assertEqual(m["ticket"], "SPD-001")
        self.assertIsNone(m["parent"])
        self.assertRegex(m["planned_at"], ISO_WITH_OFFSET)
        self.assertIsNone(m["spawned_at"])
        self.assertEqual(m["persona"], "scout")
        self.assertEqual(m["model"], "haiku")
        self.assertEqual(m["agent_type"], "spudagent")
        shown = self.home.json("ticket", "show", t["key"])["ticket"]
        self.assertEqual(shown["lead"], m["ref"])
        m2 = self.new_member(t["key"])
        self.assertEqual(m2["lineage"], "02")
        self.assertNotEqual(m2["name"], m["name"])
        kinds = [e["kind"] for e in self.home.json("events", "--member", m["ref"])["events"]]
        self.assertEqual(kinds, ["member.planned"])
        lead_after = self.home.json("ticket", "show", t["key"])["ticket"]["lead"]
        self.assertEqual(lead_after, m["ref"])

    def test_children_get_nested_lineage_and_parent(self):
        t = self.new_ticket("Nested")
        lead = self.new_member(t["key"], persona="engineer", model="opus")
        child = self.new_member(t["key"], actor=lead["ref"])
        self.assertEqual(child["lineage"], "01.01")
        self.assertEqual(child["depth"], 2)
        self.assertEqual(child["parent"], lead["ref"])
        by_lineage = self.home.json("member", "show", "SPUD-001/01.01")["member"]
        self.assertEqual(by_lineage["name"], child["name"])
        # the ticket is implied by the actor; a mismatching --ticket is an error
        t2 = self.new_ticket("Other")
        proc = self.home.run("member", "new", "--ticket", t2["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", actor=lead["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)

    def test_deliverables_brief_and_agent_type(self):
        t = self.new_ticket("Deliverables")
        m = self.new_member(t["key"], brief="Build it.", deliverable=["bin/spud", "tests/**"])
        self.assertEqual(m["deliverables"], ["bin/spud", "tests/**"])
        self.assertEqual(m["brief"], "Build it.")
        c = self.new_member(t["key"], persona="contractor", model="sonnet", agent_type="claude-code-guide")
        self.assertEqual((c["persona"], c["agent_type"]), ("contractor", "claude-code-guide"))
        proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "contractor", "--model", "sonnet", "--brief", "x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("agent-type", proc.stderr)

    def test_tier_reason_required_when_model_is_not_the_persona_default(self):
        t = self.new_ticket("Tiers")
        proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "engineer", "--model", "fable", "--brief", "x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("tier-reason", proc.stderr)
        m = self.new_member(t["key"], persona="engineer", model="fable", tier_reason="data and persistence")
        self.assertEqual(m["tier_reason"], "data and persistence")
        m2 = self.new_member(t["key"], persona="engineer", model="opus")
        self.assertIsNone(m2["tier_reason"])
        proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "wizard", "--model", "opus", "--brief", "x", actor="spud", check=False)
        self.assertNotEqual(proc.returncode, 0)

    def test_member_new_refused_on_a_closed_ticket(self):
        t = self.new_ticket("Closed")
        self.home.json("ticket", "move", t["key"], "--status", "declined", actor="spud")
        proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)

    def test_name_must_come_from_the_pool_and_be_unused_on_the_ticket(self):
        t = self.new_ticket("Names")
        m = self.new_member(t["key"], name="Kestrel")
        self.assertEqual(m["name"], "Kestrel")
        proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--name", "Kestrel", "--brief", "x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--name", "NotAPotato", "--brief", "x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        t2 = self.new_ticket("Again")
        m2 = self.new_member(t2["key"], name="Kestrel")
        self.assertEqual(m2["name"], "Kestrel")

    def test_unknown_actor_and_unbound_agent_id(self):
        t = self.new_ticket("Actors")
        proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", actor="SPUD-001/Nobody", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        proc = self.home.run("member", "log", "hello", actor="0123456789abcdef0", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("not bound", proc.stderr)

    def test_card_and_fleet(self):
        t = self.new_ticket("Card", status="active")
        lead = self.new_member(t["key"], persona="engineer", model="opus", name="Russet")
        child = self.new_member(t["key"], actor=lead["ref"], name="Yukon")
        card = self.home.json("card", t["key"])
        self.assertEqual(card["ticket"]["key"], t["key"])
        self.assertEqual(len(card["team"]), 1)
        self.assertEqual(card["team"][0]["name"], "Russet")
        self.assertEqual(card["team"][0]["children"][0]["name"], "Yukon")
        text = self.home.run("card", t["key"]).stdout
        self.assertIn("Russet", text)
        self.assertIn("01.01", text)
        self.assertIn("Yukon", text)
        self.assertIn("engineer", text)
        self.assertIn("planned", text)
        fleet = self.home.json("fleet")["members"]
        self.assertEqual({(r["name"], r["id"]) for r in fleet}, {("Russet", "01"), ("Yukon", "01.01")})
        self.assertEqual(fleet[0]["ticket"], t["key"])
        self.assertIn("Yukon", self.home.run("fleet").stdout)


class BriefRequiredTest(SpudTestCase):
    def test_member_new_requires_a_non_empty_brief(self):
        t = self.new_ticket("Briefless")
        proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("--brief", proc.stderr)
        proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "  ", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM members"), 0)
        m = self.home.json("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "@-", actor="spud", stdin="From stdin.\n")["member"]
        self.assertEqual(m["brief"], "From stdin.")


class ResumeTest(SpudTestCase):
    def test_blocked_member_resumes_with_member_start(self):
        t = self.new_ticket("Resume")
        m = self.new_member(t["key"])
        self.home.json("member", "start", m["ref"], actor="spud")
        first_spawn = self.home.json("member", "show", m["ref"])["member"]["spawned_at"]
        self.home.json("member", "finish", m["ref"], "--status", "blocked", "--outcome", "asked Eric", actor="spud")
        proc = self.home.run("member", "start", m["ref"], actor=m["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        resumed = self.home.json("member", "start", m["ref"], actor="spud")["member"]
        self.assertEqual(resumed["status"], "active")
        self.assertIsNone(resumed["finished_at"])
        self.assertEqual(resumed["spawned_at"], first_spawn)
        events = self.home.json("events", "--member", m["ref"], "--kind", "member.status")["events"]
        self.assertEqual([e["data"] for e in events], [{"from": "planned", "to": "active"}, {"from": "active", "to": "blocked"}, {"from": "blocked", "to": "active"}])
        done = self.home.json("member", "finish", m["ref"], "--status", "done", "--outcome", "resolved", actor="spud")["member"]
        self.assertEqual(done["status"], "done")
        for terminal in ("done", "failed"):
            t2 = self.new_ticket("Terminal " + terminal)
            m2 = self.new_member(t2["key"])
            if terminal == "done":
                self.home.json("member", "start", m2["ref"], actor="spud")
            self.home.json("member", "finish", m2["ref"], "--status", terminal, "--outcome", "x", actor="spud")
            proc = self.home.run("member", "start", m2["ref"], actor="spud", check=False)
            self.assertEqual(proc.returncode, EXIT_TRANSITION, terminal)


class LeadRaceTest(SpudTestCase):
    def test_concurrent_root_plans_always_make_lineage_01_the_lead(self):
        import subprocess
        import sys

        from helpers import SPUD

        for round_no in range(4):
            t = self.new_ticket("Race %d" % round_no)
            procs = [
                subprocess.Popen(
                    [sys.executable, "-I", "-S", str(SPUD), "--as", "spud", "--json", "member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "race"],
                    env=self.home.env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(3)
            ]
            for p in procs:
                out, err = p.communicate(timeout=60)
                self.assertEqual(p.returncode, 0, err)
            lead = self.home.json("ticket", "show", t["key"])["ticket"]["lead"]
            first = self.home.json("member", "show", "%s/01" % t["team_key"])["member"]
            self.assertEqual(lead, first["ref"], "round %d" % round_no)
            for lineage in ("01", "02", "03"):  # free the slots for the next round
                self.home.json("member", "finish", "%s/%s" % (t["team_key"], lineage), "--status", "failed", "--outcome", "race over", actor="spud")


class LimitsTest(SpudTestCase):
    def test_depth_limit_counts_from_spud(self):
        t = self.new_ticket("Depth")
        lead = self.new_member(t["key"], persona="engineer", model="opus")
        child = self.new_member(t["key"], actor=lead["ref"])
        proc = self.home.run("member", "new", "--persona", "scout", "--model", "haiku", "--brief", "x", actor=child["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_LIMIT)
        self.assertIn("depth", proc.stderr.lower())

    def test_root_fan_out_counts_children_alive_and_frees_a_slot(self):
        t = self.new_ticket("Fan-out")
        kids = [self.new_member(t["key"]) for _ in range(3)]
        proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_LIMIT)
        self.assertIn("fan-out", proc.stderr.lower())
        self.home.json("member", "finish", kids[0]["ref"], "--status", "failed", "--outcome", "never spawned", actor="spud")
        fourth = self.new_member(t["key"])
        self.assertEqual(fourth["lineage"], "04")  # lineage counts every child ever planned

    def test_child_fan_out(self):
        t = self.new_ticket("Child fan-out")
        lead = self.new_member(t["key"], persona="engineer", model="opus")
        self.new_member(t["key"], actor=lead["ref"])
        self.new_member(t["key"], actor=lead["ref"])
        proc = self.home.run("member", "new", "--persona", "scout", "--model", "haiku", "--brief", "x", actor=lead["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_LIMIT)


class ConcurrencyLimitTest(SpudTestCase):
    config = real_config()
    config["limits"]["max_concurrent_total"] = 2

    def test_total_alive_across_tickets(self):
        a = self.new_ticket("A")
        b = self.new_ticket("B")
        m1 = self.new_member(a["key"])
        self.new_member(b["key"])
        proc = self.home.run("member", "new", "--ticket", a["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_LIMIT)
        self.assertIn("concurren", proc.stderr.lower())
        self.home.json("member", "start", m1["ref"], actor="spud")
        self.home.json("member", "finish", m1["ref"], "--status", "done", "--outcome", "ok", actor="spud")
        self.new_member(a["key"])


class TinyPoolTest(SpudTestCase):
    config = real_config()
    config["naming"]["pool"] = ["Russet", "Yukon"]

    def test_names_are_drawn_from_the_pool_and_unique_per_ticket(self):
        t = self.new_ticket("Pool")
        names = {self.new_member(t["key"])["name"], self.new_member(t["key"])["name"]}
        self.assertEqual(names, {"Russet", "Yukon"})
        proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("pool", proc.stderr.lower())
        t2 = self.new_ticket("Other")
        self.assertIn(self.new_member(t2["key"])["name"], {"Russet", "Yukon"})


class MemberStateTest(SpudTestCase):
    def test_state_machine_and_timestamps(self):
        t = self.new_ticket("States")
        m = self.new_member(t["key"])
        proc = self.home.run("member", "finish", m["ref"], "--status", "done", "--outcome", "x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_TRANSITION)
        started = self.home.json("member", "start", m["ref"], actor="spud")["member"]
        self.assertEqual(started["status"], "active")
        self.assertRegex(started["spawned_at"], ISO_WITH_OFFSET)
        proc = self.home.run("member", "start", m["ref"], actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_TRANSITION)
        done = self.home.json("member", "finish", m["ref"], "--status", "done", "--outcome", "Accepted.", "--summary", "Built it.", actor="spud")["member"]
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["outcome"], "Accepted.")
        self.assertEqual(done["summary"], "Built it.")
        self.assertRegex(done["finished_at"], ISO_WITH_OFFSET)
        proc = self.home.run("member", "finish", m["ref"], "--status", "failed", "--outcome", "again", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_TRANSITION)
        kinds = [e["kind"] for e in self.home.json("events", "--member", m["ref"])["events"]]
        self.assertEqual(kinds, ["member.planned", "member.status", "member.outcome", "member.status"])
        m2 = self.new_member(t["key"])
        failed = self.home.json("member", "finish", m2["ref"], "--status", "failed", "--outcome", "spawn failed", actor="spud")["member"]
        self.assertEqual(failed["status"], "failed")
        m3 = self.new_member(t["key"])
        self.home.json("member", "start", m3["ref"], actor="spud")
        blocked = self.home.json("member", "finish", m3["ref"], "--status", "blocked", "--outcome", "asked Eric", actor="spud")["member"]
        self.assertEqual(blocked["status"], "blocked")
        proc = self.home.run("member", "finish", m3["ref"], "--status", "bogus", "--outcome", "x", actor="spud", check=False)
        self.assertNotEqual(proc.returncode, 0)


class OwnershipTest(SpudTestCase):
    def setUp(self):
        super().setUp()
        self.t = self.new_ticket("Own", status="active")
        self.lead = self.new_member(self.t["key"], persona="engineer", model="opus")
        self.home.json("member", "start", self.lead["ref"], actor="spud")
        self.child = self.new_member(self.t["key"], actor=self.lead["ref"])
        self.sibling = self.new_member(self.t["key"], actor=self.lead["ref"])
        self.home.json("member", "start", self.child["ref"], actor=self.lead["ref"])

    def test_log_result_block_belong_to_the_member(self):
        proc = self.home.run("member", "log", "hello", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        out = self.home.json("member", "log", "Read the brief.", actor=self.child["ref"])
        self.assertTrue(out["ok"])
        events = self.home.json("events", "--member", self.child["ref"], "--kind", "member.log")["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["body"], "Read the brief.")
        self.assertEqual(events[0]["actor"], "member:%d" % self.child["id"])
        res = self.home.json("member", "result", "Produced x.", actor=self.child["ref"])["member"]
        self.assertEqual(res["result"], "Produced x.")
        self.assertEqual(res["name"], self.child["name"])
        blk = self.home.json("member", "block", "Need Eric.", actor=self.child["ref"])["member"]
        self.assertEqual(blk["blocked"], "Need Eric.")
        proc = self.home.run("member", "result", "x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        proc = self.home.run("member", "block", "x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        kinds = [e["kind"] for e in self.home.json("events", "--member", self.child["ref"])["events"]]
        self.assertEqual(kinds[-3:], ["member.log", "member.result", "member.blocked"])

    def test_finish_and_start_belong_to_the_parent_or_an_ancestor(self):
        for actor in (self.child["ref"], self.sibling["ref"]):
            proc = self.home.run("member", "finish", self.child["ref"], "--status", "done", "--outcome", "x", actor=actor, check=False)
            self.assertEqual(proc.returncode, EXIT_OWNERSHIP, actor)
        proc = self.home.run("member", "start", self.sibling["ref"], actor=self.sibling["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        proc = self.home.run("member", "start", self.sibling["ref"], actor=self.child["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        self.home.json("member", "start", self.sibling["ref"], actor=self.lead["ref"])
        done = self.home.json("member", "finish", self.child["ref"], "--status", "done", "--outcome", "fine", actor=self.lead["ref"])["member"]
        self.assertEqual(done["status"], "done")
        # Spud is every member's ancestor and may record a grandchild's verdict
        done2 = self.home.json("member", "finish", self.sibling["ref"], "--status", "failed", "--outcome", "lead died", actor="spud")["member"]
        self.assertEqual(done2["status"], "failed")
        proc = self.home.run("member", "finish", self.lead["ref"], "--status", "done", "--outcome", "x", actor=self.lead["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)

    def test_brief_and_summary_are_the_parents(self):
        proc = self.home.run("member", "edit", self.child["ref"], "--brief", "rewritten", actor=self.child["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        out = self.home.json("member", "edit", self.child["ref"], "--brief", "rewritten", "--deliverable", "x.md", actor=self.lead["ref"])["member"]
        self.assertEqual(out["brief"], "rewritten")
        self.assertEqual(out["deliverables"], ["x.md"])
        proc = self.home.run("member", "edit", self.child["ref"], "--model", "fable", actor=self.lead["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)  # tier reason required
        out = self.home.json("member", "edit", self.child["ref"], "--model", "fable", "--tier-reason", "why", actor=self.lead["ref"])["member"]
        self.assertEqual((out["model"], out["tier_reason"]), ("fable", "why"))
        kinds = [e["kind"] for e in self.home.json("events", "--member", self.child["ref"], "--kind", "member.edited")["events"]]
        self.assertEqual(kinds, ["member.edited", "member.edited"])

    def test_a_member_cannot_plan_children_on_another_ticket_or_after_finishing(self):
        other = self.new_ticket("Other")
        proc = self.home.run("member", "new", "--ticket", other["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", actor=self.lead["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        self.home.json("member", "finish", self.child["ref"], "--status", "done", "--outcome", "x", actor=self.lead["ref"])
        proc = self.home.run("member", "new", "--persona", "scout", "--model", "haiku", "--brief", "x", actor=self.child["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)

    def test_handoff_add_by_spud_or_a_party(self):
        out = self.home.json("handoff", "add", "--ticket", self.t["key"], "--from", self.lead["ref"], "--to", self.child["ref"], "--what", "the draft", "--path", "docs/x.md", actor=self.lead["ref"])
        self.assertTrue(out["ok"])
        out = self.home.json("handoff", "add", "--ticket", self.t["key"], "--from", self.lead["ref"], "--to", "spud", "--what", "the merged file", actor="spud")
        self.assertTrue(out["ok"])
        proc = self.home.run("handoff", "add", "--ticket", self.t["key"], "--from", self.lead["ref"], "--to", self.child["ref"], "--what", "x", actor=self.sibling["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        other = self.new_ticket("Elsewhere")
        proc = self.home.run("handoff", "add", "--ticket", other["key"], "--from", self.lead["ref"], "--to", "spud", "--what", "x", actor=self.lead["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        proc = self.home.run("handoff", "add", "--ticket", self.t["key"], "--from", "spud", "--to", "spud", "--what", "x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        rows = self.home.rows("SELECT from_member_id, to_member_id, what, path FROM handoffs ORDER BY id")
        self.assertEqual(rows[0]["what"], "the draft")
        self.assertEqual(rows[0]["path"], "docs/x.md")
        self.assertIsNone(rows[1]["to_member_id"])
        kinds = [e["kind"] for e in self.home.json("events", "--ticket", self.t["key"], "--kind", "handoff")["events"]]
        self.assertEqual(kinds, ["handoff", "handoff"])


if __name__ == "__main__":
    unittest.main()
