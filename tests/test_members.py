"""member new|start|log|result|block|finish|edit, the limits, lineage, the
name draw, ownership, the member state machine, card and fleet."""

import copy
import json
import os
import re
import unittest

import hookcase
from helpers import (
    EXIT_ERROR,
    EXIT_LIMIT,
    EXIT_OK,
    EXIT_OWNERSHIP,
    EXIT_TRANSITION,
    EXIT_USAGE,
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
        m = self.new_member(t["key"], brief="Build it.", deliverable=["home:bin/spud", "home:tests/**"])
        self.assertEqual(m["deliverables"], ["home:bin/spud", "home:tests/**"])
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

    def test_a_lone_dash_brief_is_refused_naming_at_dash(self):
        """SPD-099: `--brief -`, typed for `--brief @-`, stored the brief '-' and lost the whole brief.  It is refused, and
        so is '-' read from stdin through @-, the same mistake one step removed; no row is written."""
        t = self.new_ticket("Dash")
        for value, stdin in (("-", "the real brief\n"), (" - ", None), ("@-", "-\n")):
            proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", value, actor="spud", stdin=stdin, check=False)
            self.assertEqual(proc.returncode, EXIT_USAGE, (value, proc.stderr))
            self.assertIn("--brief", proc.stderr)
            self.assertIn("@-", proc.stderr)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM members"), 0)
        m = self.new_member(t["key"])
        proc = self.home.run("member", "edit", m["ref"], "--brief", "-", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE)
        self.assertIn("@-", proc.stderr)
        self.assertEqual(self.home.json("member", "show", m["ref"])["member"]["brief"], "Do the thing.")

    def test_a_lone_dash_is_refused_for_every_text_argument(self):
        """The refusal lives in text_arg, so it reaches every text a lone '-' would silently replace: a log line, a
        result, an outcome, a summary.  A dash inside a longer text is untouched."""
        t = self.new_ticket("Dash texts")
        m = self.new_member(t["key"])
        self.home.json("member", "start", m["ref"], actor="spud")
        for args in (("member", "log", "-"), ("member", "result", "-")):
            proc = self.home.run(*args, actor=m["ref"], check=False)
            self.assertEqual(proc.returncode, EXIT_USAGE, (args, proc.stderr))
            self.assertIn("@-", proc.stderr)
        proc = self.home.run("member", "finish", m["ref"], "--status", "done", "--outcome", "-", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE)
        proc = self.home.run("member", "finish", m["ref"], "--status", "done", "--outcome", "ok", "--summary", "-", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE)
        self.assertEqual(self.home.json("member", "show", m["ref"])["member"]["status"], "active")
        self.home.json("member", "log", "a - b", actor=m["ref"])
        self.home.json("member", "log", "@-", actor=m["ref"], stdin="--\n")


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
        # Spud's finish of a root member ends with its report entry (SPD-011)
        self.assertEqual(kinds, ["member.planned", "member.status", "member.outcome", "member.status", "report.entry"])
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
        out = self.home.json("member", "edit", self.child["ref"], "--brief", "rewritten", "--deliverable", "home:x.md", actor=self.lead["ref"])["member"]
        self.assertEqual(out["brief"], "rewritten")
        self.assertEqual(out["deliverables"], ["home:x.md"])
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


class MemberHandleTest(SpudTestCase):
    """SPD-017: get_member also accepts the handle the CLI prints, wherever a ref is taken."""

    def setUp(self):
        super().setUp()
        self.t = self.new_ticket("Handles", status="active")
        self.lead = self.new_member(self.t["key"], persona="engineer", model="opus", name="Russet")
        self.child = self.new_member(self.t["key"], actor=self.lead["ref"], persona="scout", model="haiku", name="Yukon")

    @staticmethod
    def handle(m, with_model):
        tail = ", %s)" % m["model"] if with_model else ")"
        return "%s (%s, %s" % (m["ref"], m["lineage"], m["persona"]) + tail

    def test_member_show_accepts_both_handle_forms(self):
        for with_model in (False, True):
            shown = self.home.json("member", "show", self.handle(self.child, with_model))["member"]
            self.assertEqual(shown["id"], self.child["id"], with_model)

    def test_events_member_accepts_both_handle_forms(self):
        for with_model in (False, True):
            events = self.home.json("events", "--member", self.handle(self.child, with_model))["events"]
            self.assertTrue(events, with_model)
            self.assertTrue(all(e["member"] == self.child["ref"] for e in events), with_model)

    def test_member_edit_accepts_both_handle_forms(self):
        out = self.home.json("member", "edit", self.handle(self.child, False), "--summary", "two-tuple", actor=self.lead["ref"])["member"]
        self.assertEqual((out["id"], out["summary"]), (self.child["id"], "two-tuple"))
        out = self.home.json("member", "edit", self.handle(self.child, True), "--summary", "three-tuple", actor=self.lead["ref"])["member"]
        self.assertEqual((out["id"], out["summary"]), (self.child["id"], "three-tuple"))

    def test_member_start_accepts_both_handle_forms(self):
        a = self.new_member(self.t["key"], name="Kestrel")
        b = self.new_member(self.t["key"], name="Rooster")
        started = self.home.json("member", "start", self.handle(a, False), actor="spud")["member"]
        self.assertEqual((started["id"], started["status"]), (a["id"], "active"))
        started = self.home.json("member", "start", self.handle(b, True), actor="spud")["member"]
        self.assertEqual((started["id"], started["status"]), (b["id"], "active"))

    def test_member_finish_accepts_both_handle_forms(self):
        a = self.new_member(self.t["key"], name="Kestrel")
        b = self.new_member(self.t["key"], name="Rooster")
        done = self.home.json("member", "finish", self.handle(a, False), "--status", "failed", "--outcome", "x", actor="spud")["member"]
        self.assertEqual((done["id"], done["status"]), (a["id"], "failed"))
        done = self.home.json("member", "finish", self.handle(b, True), "--status", "failed", "--outcome", "x", actor="spud")["member"]
        self.assertEqual((done["id"], done["status"]), (b["id"], "failed"))

    def test_handoff_add_from_and_to_accept_both_handle_forms(self):
        out = self.home.json("handoff", "add", "--ticket", self.t["key"], "--from", self.handle(self.lead, False),
                              "--to", self.handle(self.child, True), "--what", "the draft", actor=self.lead["ref"])
        self.assertTrue(out["ok"])
        rows = self.home.rows("SELECT from_member_id, to_member_id, what FROM handoffs ORDER BY id")
        self.assertEqual(rows[-1], {"from_member_id": self.lead["id"], "to_member_id": self.child["id"], "what": "the draft"})

    def test_a_disagreeing_handle_is_refused_naming_the_real_handle_and_writes_nothing(self):
        real = self.handle(self.lead, True)
        for bad, field in (
            ("%s (99, engineer, opus)" % self.lead["ref"], "lineage"),
            ("%s (01, scout, opus)" % self.lead["ref"], "persona"),
            ("%s (01, engineer, sonnet)" % self.lead["ref"], "model"),
        ):
            proc = self.home.run("member", "show", bad, check=False)
            self.assertEqual(proc.returncode, EXIT_ERROR, field)
            self.assertIn(real, proc.stderr, field)
        # caught before any write: an edit through the same disagreeing handle changes nothing
        proc = self.home.run("member", "edit", "%s (99, engineer, opus)" % self.lead["ref"], "--summary", "should not land", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIsNone(self.home.json("member", "show", self.lead["ref"])["member"]["summary"])

    def test_as_refuses_a_handle_exactly_as_an_unknown_actor(self):
        handle = self.handle(self.lead, True)
        proc = self.home.run("member", "log", "hi", actor=handle, check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("no member %s" % handle, proc.stderr)
        # the same shape of refusal a plain unknown actor gets today
        unknown = "%s/NotAName" % self.t["team_key"]
        proc2 = self.home.run("member", "log", "hi", actor=unknown, check=False)
        self.assertEqual(proc2.returncode, EXIT_ERROR)
        self.assertIn("no member %s" % unknown, proc2.stderr)


class MemberListTest(SpudTestCase):
    """SPD-017: member list [--ticket], an alias of card's team listing / fleet, read-only."""

    def setUp(self):
        super().setUp()
        self.t = self.new_ticket("Listing", status="active")
        self.lead = self.new_member(self.t["key"], persona="engineer", model="opus", name="Russet")
        self.child = self.new_member(self.t["key"], actor=self.lead["ref"], persona="scout", model="haiku", name="Yukon")
        self.other_t = self.new_ticket("Other listing", status="active")
        self.other = self.new_member(self.other_t["key"], persona="writer", model="sonnet", name="Kestrel")

    def test_list_by_ticket_matches_cards_order_with_handle_and_status(self):
        listed = self.home.json("member", "list", "--ticket", self.t["key"])["members"]
        self.assertEqual([m["id"] for m in listed], [self.lead["id"], self.child["id"]])
        lines = self.home.run("member", "list", "--ticket", self.t["key"]).stdout.splitlines()
        self.assertEqual(lines[0], "%s (01, engineer, opus) planned" % self.lead["ref"])
        self.assertEqual(lines[1], "%s (01.01, scout, haiku) planned" % self.child["ref"])

    def test_list_without_ticket_matches_fleets_order(self):
        fleet_rows = self.home.json("fleet")["members"]
        listed = self.home.json("member", "list")["members"]
        self.assertEqual(len(listed), 3)
        self.assertEqual([(d["team_key"], d["lineage"]) for d in listed], [(r["team_key"], r["id"]) for r in fleet_rows])

    def test_json_shape_matches_member_show(self):
        shown = self.home.json("member", "show", self.lead["ref"])["member"]
        listed = self.home.json("member", "list", "--ticket", self.t["key"])["members"]
        # SPD-013 added tokens/cost_usd/not_priced to every team_tree node -- what card's team, and so
        # member list --ticket, shows -- which member show's plain member_dict never carries.
        extra = set(listed[0]) - set(shown)
        self.assertEqual(extra, {"tokens", "cost_usd", "not_priced"})
        self.assertEqual({k: v for k, v in listed[0].items() if k not in extra}, shown)

    def test_unknown_ticket_is_an_error(self):
        proc = self.home.run("member", "list", "--ticket", "SPD-999", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)



class MemberHelpTest(unittest.TestCase):
    """`member --help`, as the program's main answers it in this process before any home is read (hookcase.run_main):
    no home, where MemberListTest built one and planned three members for it (SPD-233)."""

    def test_member_list_is_documented_in_help(self):
        code, out, err = hookcase.run_main(dict(os.environ, COLUMNS="80"), ["member", "--help"])
        self.assertEqual(code, 0, err)
        text = " ".join(out.split())
        self.assertIn("list", text)
        self.assertIn("card", text)
        self.assertIn("fleet", text)


class OpusFirstTest(SpudTestCase):
    """SPD-222: researcher, architect and reviewer default to opus; every spudagent is planned at the effort its definition
    sets, recorded on the row; and a member that returned failed or blocked on opus is re-planned once on fable."""

    def plan(self, ticket, persona, model, *extra, actor="spud"):
        return self.home.run("member", "new", "--ticket", ticket, "--persona", persona, "--model", model, "--brief", "x", *extra,
                             actor=actor, check=False)

    def test_researcher_architect_and_reviewer_default_to_opus(self):
        for persona in ("researcher", "architect", "reviewer"):
            with self.subTest(persona):
                t = self.new_ticket("Tiers: " + persona)  # a ticket each, so root_fan_out never counts the three
                self.assertIsNone(self.new_member(t["key"], persona=persona, model="opus")["tier_reason"])
                proc = self.plan(t["key"], persona, "fable")
                self.assertEqual(proc.returncode, EXIT_ERROR)
                self.assertIn("%s defaults to opus; model fable needs --tier-reason" % persona, proc.stderr)
        kept = self.new_member(t["key"], persona="reviewer", model="fable", tier_reason="review of the hook path")
        self.assertEqual((kept["model"], kept["tier_reason"]), ("fable", "review of the hook path"))

    def test_the_effort_is_the_personas_on_a_model_that_takes_one(self):
        """SPD-222, as Eric asked it on 2026-09-23: effort is chosen per member at planning.  Without --effort it is the
        persona's `effort` in spud.config.json; haiku takes none and a contractor's own definition sets its own, so both
        record NULL and are spawned as their plain type."""
        cases = [("engineer", "opus", {}, "high", "spudagent-high"),
                 ("reviewer", "fable", {"tier_reason": "review of security"}, "high", "spudagent-high"),
                 ("writer", "sonnet", {}, "medium", "spudagent-medium"), ("scout", "haiku", {}, None, "spudagent"),
                 ("contractor", "sonnet", {"agent_type": "claude-code-guide"}, None, "claude-code-guide")]
        for persona, model, extra, effort, spawn_as in cases:
            with self.subTest(persona=persona, model=model):
                t = self.new_ticket("Effort: " + persona)
                m = self.new_member(t["key"], persona=persona, model=model, **extra)
                self.assertEqual(m["effort"], effort)
                shown = self.home.run("member", "show", m["ref"]).stdout.splitlines()
                self.assertEqual([line for line in shown if line.startswith("effort: ")], ["effort: " + effort] if effort else [])
                self.assertIn("subagent_type: " + spawn_as, shown)
                planned = self.home.json("events", "--member", m["ref"])["events"][0]
                self.assertEqual(planned["data"].get("effort"), effort)
        fleet = [r["effort"] for r in self.home.json("fleet")["members"]]  # a name repeats across teams, so a list
        self.assertEqual(sorted(fleet, key=str), sorted([c[3] for c in cases], key=str))
        self.assertIn("effort", self.home.run("fleet").stdout.splitlines()[0])

    def test_member_new_takes_an_effort_and_the_plan_line_names_the_spawn_type(self):
        t = self.new_ticket("Chosen")
        for effort in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort):
                out = self.home.json("member", "new", "--ticket", t["key"], "--persona", "engineer", "--model", "opus",
                                     "--effort", effort, "--brief", "x", actor="spud")
                self.assertEqual((out["member"]["effort"], out["subagent_type"]), (effort, "spudagent-" + effort))
                self.assertNotIn("note", out)
                self.home.json("member", "finish", out["member"]["ref"], "--status", "failed", "--outcome", "x", actor="spud")
        proc = self.plan(t["key"], "architect", "opus", "--effort", "xhigh")
        self.assertEqual(proc.returncode, EXIT_OK)
        m = self.home.json("member", "list", "--ticket", t["key"])["members"][-1]
        self.assertEqual(proc.stdout.strip(), "planned %s (%s, architect, opus) on %s at xhigh effort: spawn it as subagent_type"
                                              " spudagent-xhigh" % (m["ref"], m["lineage"], t["key"]))
        proc = self.plan(t["key"], "engineer", "opus", "--effort", "extreme")
        self.assertEqual(proc.returncode, EXIT_USAGE)
        # a nested parent chooses its children's effort with the same flag
        lead = self.new_member(t["key"], persona="engineer", model="opus")
        child = self.new_member(t["key"], actor=lead["ref"], persona="researcher", model="opus", effort="low")
        self.assertEqual((child["parent"], child["effort"]), (lead["ref"], "low"))

    def test_haiku_and_a_contractor_record_no_effort_whatever_is_passed_and_say_so(self):
        t = self.new_ticket("None")
        out = self.home.json("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--effort", "max",
                             "--brief", "x", actor="spud")
        self.assertEqual((out["member"]["effort"], out["subagent_type"]), (None, "spudagent"))
        self.assertEqual(out["note"], "haiku takes no effort, so --effort max is not recorded; it is spawned as subagent_type"
                                      " spudagent, which sets none")
        proc = self.plan(t["key"], "scout", "haiku", "--effort", "low")
        self.assertIn("\nnote: haiku takes no effort, so --effort low is not recorded", proc.stdout)
        self.assertIn(": spawn it as subagent_type spudagent\n", proc.stdout)
        out = self.home.json("member", "new", "--ticket", t["key"], "--persona", "contractor", "--model", "sonnet", "--agent-type",
                             "Explore", "--effort", "high", "--brief", "x", actor="spud")
        self.assertEqual((out["member"]["effort"], out["subagent_type"]), (None, "Explore"))
        self.assertIn("a contractor's effort is its own definition's, so --effort high is not recorded", out["note"])

    def test_an_effort_variant_is_not_an_agent_type(self):
        t = self.new_ticket("Variant")
        proc = self.plan(t["key"], "engineer", "opus", "--agent-type", "spudagent-high")
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("spudagent-high is the spawn type of a spudagent planned at an effort, not an agent type to plan: plan it"
                      " as a spudagent with --effort high", proc.stderr)
        m = self.new_member(t["key"], persona="engineer", model="opus")
        proc = self.home.run("member", "edit", m["ref"], "--agent-type", "spudagent-max", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM members WHERE agent_type != 'spudagent'"), 0)

    def test_the_persona_default_falls_back_to_high_when_the_config_names_none(self):
        # A home whose config predates SPD-222's persona `effort` keys (Eric's own, until he adds them), and a scout
        # re-tiered onto a model that takes one: kernel.DEFAULT_EFFORT.
        config = json.loads((self.home.path / "spud.config.json").read_text(encoding="utf-8"))
        for spec in config["personas"].values():
            spec.pop("effort", None)
        (self.home.path / "spud.config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        t = self.new_ticket("Old config")
        self.assertEqual(self.new_member(t["key"], persona="writer", model="sonnet")["effort"], "high")
        self.assertEqual(self.new_member(t["key"], persona="scout", model="sonnet", tier_reason="a long survey")["effort"], "high")

    def test_member_edit_changes_a_planned_members_effort_and_a_model_change_keeps_it(self):
        t = self.new_ticket("Edit")
        m = self.new_member(t["key"], persona="engineer", model="opus")
        edited = self.home.json("member", "edit", m["ref"], "--effort", "xhigh", actor="spud")
        self.assertEqual((edited["member"]["effort"], edited["changed"], edited["subagent_type"]), ("xhigh", ["effort"], "spudagent-xhigh"))
        self.assertIn("edited: effort; spawn it as subagent_type spudagent-low", self.home.run("member", "edit", m["ref"], "--effort", "low", actor="spud").stdout)
        self.home.json("member", "edit", m["ref"], "--effort", "xhigh", actor="spud")
        kept = self.home.json("member", "edit", m["ref"], "--model", "fable", "--tier-reason", "the schema", actor="spud")
        self.assertEqual((kept["member"]["effort"], kept["changed"]), ("xhigh", ["model", "tier_reason"]))
        cleared = self.home.json("member", "edit", m["ref"], "--model", "haiku", "--tier-reason", "a lookup after all", actor="spud")
        self.assertEqual((cleared["member"]["effort"], cleared["changed"]), (None, ["effort", "model", "tier_reason"]))
        back = self.home.json("member", "edit", m["ref"], "--model", "opus", actor="spud")
        self.assertEqual(back["member"]["effort"], "high")  # the persona's again: haiku kept none to return to
        brief = self.home.json("member", "edit", m["ref"], "--brief", "Again.", actor="spud")
        self.assertEqual((brief["member"]["effort"], brief["changed"]), ("high", ["brief"]))
        self.home.json("member", "start", m["ref"], actor="spud")
        proc = self.home.run("member", "edit", m["ref"], "--effort", "max", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_TRANSITION)
        self.assertIn("is active; its effort changes only while it is planned, before it is spawned", proc.stderr)
        self.assertEqual(self.home.scalar("SELECT effort FROM members WHERE id = ?", m["id"]), "high")

    def test_an_escalation_runs_at_the_failed_members_effort_or_higher(self):
        t = self.new_ticket("Escalate at effort")
        failed = self.new_member(t["key"], persona="engineer", model="opus", effort="xhigh")
        self.home.json("member", "finish", failed["ref"], "--status", "failed", "--outcome", "x", actor="spud")
        proc = self.plan(t["key"], "engineer", "fable", "--escalates", failed["ref"], "--effort", "high")
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("an escalation runs at the effort of the member it re-plans or higher, never lower: %s ran at xhigh, and"
                      " --effort high is lower" % failed["ref"], proc.stderr)
        again = self.new_member(t["key"], persona="engineer", model="fable", escalates=failed["ref"])
        self.assertEqual(again["effort"], "xhigh")  # the default: the failed member's own level, not the persona's high
        proc = self.home.run("member", "edit", again["ref"], "--effort", "medium", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("never lower", proc.stderr)
        self.assertEqual(self.home.json("member", "edit", again["ref"], "--effort", "max", actor="spud")["member"]["effort"], "max")
        other = self.new_member(t["key"], persona="engineer", model="opus")
        self.home.json("member", "finish", other["ref"], "--status", "failed", "--outcome", "x", actor="spud")
        higher = self.new_member(t["key"], persona="engineer", model="fable", escalates=other["ref"], effort="max")
        self.assertEqual(higher["effort"], "max")

    def test_a_failed_opus_member_is_escalated_once_on_fable(self):
        t = self.new_ticket("Escalate")
        failed = self.new_member(t["key"], persona="engineer", model="opus", name="Russet")
        self.home.json("member", "finish", failed["ref"], "--status", "failed", "--outcome", "could not build it", actor="spud")
        again = self.new_member(t["key"], persona="engineer", model="fable", escalates=failed["ref"])
        self.assertEqual((again["escalates"], again["tier_reason"], again["effort"]),
                         (failed["ref"], "escalation after %s failed" % failed["ref"], "high"))
        shown = self.home.run("member", "show", again["ref"]).stdout.splitlines()
        self.assertIn("escalates: " + failed["ref"], shown)
        self.assertIn("tier reason: escalation after %s failed" % failed["ref"], shown)
        planned = self.home.json("events", "--member", again["ref"])["events"][0]
        self.assertEqual(planned["data"]["escalates"], failed["ref"])
        proc = self.plan(t["key"], "engineer", "fable", "--escalates", failed["ref"])
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("%s was escalated once already, by %s" % (failed["ref"], again["ref"]), proc.stderr)

    def test_a_blocked_member_keeps_a_tier_reason_given_and_names_its_status(self):
        t = self.new_ticket("Blocked")
        m = self.new_member(t["key"], persona="architect", model="opus")
        self.home.json("member", "start", m["ref"], actor="spud")
        self.home.json("member", "finish", m["ref"], "--status", "blocked", "--outcome", "the design is beyond it", actor="spud")
        filled = self.new_member(t["key"], persona="architect", model="fable", escalates=m["ref"])
        self.assertEqual(filled["tier_reason"], "escalation after %s blocked" % m["ref"])
        other = self.new_member(t["key"], persona="engineer", model="opus")
        self.home.json("member", "finish", other["ref"], "--status", "failed", "--outcome", "x", actor="spud")
        given = self.new_member(t["key"], persona="engineer", model="fable", escalates=other["ref"], tier_reason="escalation: the schema")
        self.assertEqual((given["tier_reason"], given["escalates"]), ("escalation: the schema", other["ref"]))

    def test_what_an_escalation_refuses(self):
        t = self.new_ticket("Refusals")
        live = self.new_member(t["key"], persona="engineer", model="opus", name="Russet")
        proc = self.plan(t["key"], "engineer", "fable", "--escalates", live["ref"])
        self.assertEqual(proc.returncode, EXIT_TRANSITION)
        self.assertIn("is planned; only a member that returned failed or blocked is escalated", proc.stderr)
        self.home.json("member", "finish", live["ref"], "--status", "failed", "--outcome", "x", actor="spud")
        proc = self.plan(t["key"], "engineer", "opus", "--escalates", live["ref"])
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("the one escalation is opus to fable: %s ran on opus and this plan names opus" % live["ref"], proc.stderr)
        on_fable = self.new_member(t["key"], persona="engineer", model="fable", tier_reason="the schema", name="Kestrel")
        self.home.json("member", "finish", on_fable["ref"], "--status", "failed", "--outcome", "x", actor="spud")
        proc = self.plan(t["key"], "engineer", "fable", "--escalates", on_fable["ref"])
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("%s ran on fable" % on_fable["ref"], proc.stderr)
        elsewhere = self.new_ticket("Elsewhere")
        proc = self.plan(elsewhere["key"], "engineer", "fable", "--escalates", live["ref"])
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("is not on %s" % elsewhere["key"], proc.stderr)
        lead = self.new_member(t["key"], persona="engineer", model="opus")
        proc = self.plan(t["key"], "engineer", "fable", "--escalates", live["ref"], actor=lead["ref"])
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        self.assertIn("%s is Spud's child; its own parent re-plans it" % live["ref"], proc.stderr)
        proc = self.plan(t["key"], "engineer", "fable", "--escalates", "SPUD-001/Nobody")
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("no member SPUD-001/Nobody", proc.stderr)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM members WHERE escalates_id IS NOT NULL"), 0)


if __name__ == "__main__":
    unittest.main()
