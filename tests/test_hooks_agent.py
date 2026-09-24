"""spud hook PreToolUse(Agent), PostToolUse(Agent), SubagentStart and SubagentStop: the spawn check, the binding, run
totals and the lead hold."""

import json
import os
import unittest

from helpers import EXIT_USAGE, load_spud_module, real_config, spawn_type
from hookcase import AGENT_A, AGENT_B, AGENT_C, AGENT_D, COMPLETION, PER_BLOCK_BREAKDOWN, PER_ENTRY_SUM, SESSION
from hookcase import TWO_REQUESTS_SUM, HookCase


# The per-model breakdown a sum keeps beside its usage since SPD-013 (tests/test_cost.py).  two_requests' entries name no
# model and split no cache write, so their 100 writes count as unsplit; per_block's carry a service tier and a TTL split.
TWO_REQUESTS_BREAKDOWN = [{"requests": 2, "input_tokens": 30, "output_tokens": 12, "cache_read_input_tokens": 300, "cache_creation_unsplit_input_tokens": 100}]


# =============================================================================
# PreToolUse / Agent
# =============================================================================


class PreAgentTest(HookCase):
    def test_allow_matches_the_planned_row_and_records_the_request(self):
        m = self.plan()
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(m)))
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.decision, "allow")
        self.assertEqual(r.json["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(r.stderr, "")
        row = self.home.rows("SELECT * FROM spawn_requests")[0]
        self.assertEqual(row["tool_use_id"], "toolu_01AGENT")
        self.assertEqual(row["session_id"], SESSION)
        self.assertIsNone(row["caller_agent_id"])
        self.assertEqual(row["description"], self.description(m))
        self.assertEqual((row["subagent_type"], row["model"], row["decision"]), ("spudagent", "haiku", "allow"))
        self.assertEqual(row["member_id"], m["id"])
        self.assertIsNone(row["agent_id"])
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual(shown["status"], "planned")  # PostToolUse makes it active
        kinds = [e["kind"] for e in self.events(member=m["ref"])]
        self.assertEqual(kinds, ["member.planned", "member.spawned"])
        spawned = self.events("member.spawned")[0]
        self.assertEqual(spawned["actor"], "hook:PreToolUse")
        self.assertEqual(spawned["data"]["tool_use_id"], "toolu_01AGENT")

    def test_run_in_background_is_recorded_and_not_required(self):
        m = self.plan()
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(m), run_in_background=False))
        self.assertEqual(r.decision, "allow", r)
        self.assertEqual(self.home.scalar("SELECT run_in_background FROM spawn_requests"), 0)

    def test_no_planned_row_is_denied_with_a_reason_and_hook_denied(self):
        r = self.home.hook("PreToolUse", self.pre_agent("%s/Nobody (01, scout)" % self.team))
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.decision, "deny")
        self.assertIn("no planned member", r.reason)
        self.assertIn("Nobody", r.reason)
        row = self.home.rows("SELECT * FROM spawn_requests")[0]
        self.assertEqual(row["decision"], "deny")
        self.assertIsNone(row["member_id"])
        self.assertEqual(row["reason"], r.reason)
        d = self.denied()
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["actor"], "hook:PreToolUse")
        self.assertEqual(d[0]["ticket"], self.t["key"])  # the team key in the description resolves the ticket
        self.assertEqual(d[0]["data"]["tool_name"], "Agent")

    def test_description_must_carry_the_team_key(self):
        m = self.plan()
        for bad in ("%s (%s, %s)" % (m["name"], m["lineage"], m["persona"]), "Do a thing", "%s/%s" % (self.team, m["name"]), ""):
            r = self.home.hook("PreToolUse", self.pre_agent(bad))
            self.assertEqual((r.code, r.decision), (0, "deny"), bad)
            self.assertIn("SPUD-nnn/<Name> (<lineage>, <persona>)", r.reason)
        self.assertEqual(len(self.denied()), 4)

    def test_lineage_persona_and_name_must_match_the_row(self):
        m = self.plan(persona="scout", model="haiku", name="Yukon")  # fixed name: the third case's literal Kestrel must never be what the draw picked (SPD-026)
        for desc, what in (
            ("%s/%s (02, scout)" % (self.team, m["name"]), "lineage"),
            ("%s/%s (01, engineer)" % (self.team, m["name"]), "persona"),
            ("%s/Kestrel (01, scout)" % self.team, "no planned member"),
            ("SPUD-999/%s (01, scout)" % m["name"], "no team"),
        ):
            r = self.home.hook("PreToolUse", self.pre_agent(desc))
            self.assertEqual((r.code, r.decision), (0, "deny"), desc)
            self.assertIn(what, r.reason, desc)
        # a found row that mismatches records member.spawn_denied on that row
        kinds = [e["kind"] for e in self.events(member=m["ref"])]
        self.assertEqual(kinds, ["member.planned", "member.spawn_denied", "member.spawn_denied"])

    def test_law_3_model_and_fork_and_isolation(self):
        m = self.plan(persona="scout", model="haiku")
        cases = [
            (dict(model=None), "model"),
            (dict(model="inherit"), "inherit"),
            (dict(model="opus"), "haiku"),
            (dict(model="claude-haiku-4-5-20251001"), "haiku"),
            (dict(subagent_type="fork"), "fork"),
            (dict(isolation="worktree"), "isolation"),
            (dict(fork=True), "fork"),
            (dict(subagent_type="general-purpose"), "agent_type"),
        ]
        for n, (kw, needle) in enumerate(cases):
            r = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_%02d" % n, **kw))
            self.assertEqual((r.code, r.decision), (0, "deny"), (kw, r))
            self.assertIn(needle, r.reason, (kw, r.reason))
            if needle in ("model", "inherit", "fork", "isolation"):
                self.assertIn("Law 3", r.reason)
        # one request is one row: the same tool_use_id twice updates rather than duplicates
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_00", model=None))
        self.assertEqual(r.decision, "deny")
        self.assertEqual(self.home.scalar("SELECT count(*) FROM spawn_requests WHERE decision = 'deny'"), len(cases))
        self.assertEqual(len(self.events("member.spawn_denied")), len(cases) + 1)  # every attempt is an event

    def test_contractor_spawns_as_its_agent_type(self):
        c = self.plan(persona="contractor", model="sonnet", agent_type="claude-code-guide")
        desc = "%s/%s (01, contractor)" % (self.team, c["name"])
        r = self.home.hook("PreToolUse", self.pre_agent(desc, model="sonnet", subagent_type="spudagent"))
        self.assertEqual(r.decision, "deny")
        self.assertIn("claude-code-guide", r.reason)
        r = self.home.hook("PreToolUse", self.pre_agent(desc, model="sonnet", subagent_type="claude-code-guide"))
        self.assertEqual(r.decision, "allow", r)

    def test_a_member_planned_at_an_effort_spawns_as_that_effort_s_definition(self):
        """SPD-222: the Agent tool takes no effort, so the effort rides on the definition, `spudagent-<effort>`, and the check
        holds the call to the planned one as it holds the model: any other definition would run the member at a level
        nobody planned, the base at the spawning session's own."""
        m = self.plan(persona="engineer", model="opus", effort="xhigh")
        desc = self.description(m)
        for n, (asked, needle) in enumerate((("spudagent", "not spudagent"), ("spudagent-high", "not spudagent-high"),
                                              ("spudagent-XHIGH", "not spudagent-XHIGH"), (None, "not None"))):
            r = self.home.hook("PreToolUse", self.pre_agent(desc, model="opus", subagent_type=asked, tool_use_id="toolu_e%d" % n))
            self.assertEqual(r.decision, "deny", (asked, r))
            self.assertIn("Law 3: %s is planned at xhigh effort, so it is spawned as subagent_type spudagent-xhigh, %s" % (m["ref"], needle), r.reason)
            self.assertIn("`spud member edit --effort`", r.reason)
        r = self.home.hook("PreToolUse", self.pre_agent(desc, model="opus", subagent_type="spudagent-xhigh", tool_use_id="toolu_eok"))
        self.assertEqual(r.decision, "allow", r)
        row = self.home.rows("SELECT * FROM spawn_requests WHERE tool_use_id = 'toolu_eok'")[0]
        self.assertEqual((row["subagent_type"], row["decision"]), ("spudagent-xhigh", "allow"))

    def test_a_member_at_no_effort_spawns_as_the_base_definition(self):
        scout = self.plan(persona="scout", model="haiku")
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(scout), subagent_type="spudagent-high", tool_use_id="toolu_h1"))
        self.assertEqual(r.decision, "deny")
        self.assertIn("Law 3: %s is planned at no effort (haiku takes none), so it is spawned as subagent_type spudagent, the base"
                      " definition, not spudagent-high" % scout["ref"], r.reason)
        # A row planned before migration 0009 recorded no effort: it ran at the session's level, and still spawns as the base.
        old = self.plan(persona="engineer", model="opus")
        con = self.home.connect()
        con.execute("UPDATE members SET effort = NULL WHERE id = ?", (old["id"],))
        con.commit()
        con.close()
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(old), model="opus", subagent_type="spudagent-high", tool_use_id="toolu_h2"))
        self.assertEqual(r.decision, "deny")
        self.assertIn("planned at no effort (it was planned before effort was recorded)", r.reason)
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(old), model="opus", subagent_type="spudagent", tool_use_id="toolu_h3"))
        self.assertEqual(r.decision, "allow", r)
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(scout), subagent_type="spudagent", tool_use_id="toolu_h4"))
        self.assertEqual(r.decision, "allow", r)

    def test_a_variant_spawn_is_named_and_bound_at_subagent_start(self):
        """SubagentStart names a background spawn waiting to bind by the agent type the harness reports, which for a member
        planned at an effort is the variant its spawn named -- the exact type, never a prefix of it."""
        m = self.plan(persona="engineer", model="opus")
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(m), model="opus", subagent_type="spudagent-high", tool_use_id="toolu_v1"))
        self.assertEqual(r.decision, "allow", r)
        other = self.home.hook("SubagentStart", self.sub_start(AGENT_B, "spudagent"))
        self.assertNotIn(m["name"], other.context or "")  # the base is another definition: it names no waiting variant
        start = self.home.hook("SubagentStart", self.sub_start(AGENT_A, "spudagent-high"))
        self.assertIn("You are %s (01, engineer)" % m["ref"], start.context)
        self.assertEqual(self.home.hook("PostToolUse", self.post_agent_launched("toolu_v1", AGENT_A, self.description(m))).code, 0)
        self.assertEqual(self.home.json("member", "show", m["ref"])["member"]["agent_id"], AGENT_A)

    def test_brief_must_be_non_empty(self):
        m = self.plan()
        con = self.home.connect()
        con.execute("UPDATE members SET brief = '  ' WHERE id = ?", (m["id"],))
        con.commit()
        con.close()
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(m)))
        self.assertEqual(r.decision, "deny")
        self.assertIn("Law 2", r.reason)

    def test_parent_must_be_the_caller(self):
        lead = self.plan(persona="engineer", model="opus")
        lead = self.spawn(lead, AGENT_A)
        child = self.plan(actor=lead["ref"])
        # Spud (no agent_id) may not spawn the lead's child
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(child)))
        self.assertEqual(r.decision, "deny")
        self.assertIn("parent", r.reason)
        # another bound agent may not either
        other = self.plan(persona="engineer", model="opus")
        other = self.spawn(other, AGENT_B)
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(child), agent_id=AGENT_B))
        self.assertEqual(r.decision, "deny")
        self.assertIn("parent", r.reason)
        # an unbound agent_id is refused outright
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(child), agent_id=AGENT_D))
        self.assertEqual(r.decision, "deny")
        self.assertIn("not bound", r.reason)
        # the lead itself may
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(child), agent_id=AGENT_A))
        self.assertEqual(r.decision, "allow", r)
        row = self.home.rows("SELECT * FROM spawn_requests WHERE decision = 'allow' AND caller_agent_id = ?", AGENT_A)[0]
        self.assertEqual(row["member_id"], child["id"])
        # and a lead may not spawn Spud's members
        second = self.plan(persona="engineer", model="opus")
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(second), model="opus", subagent_type=spawn_type(second), agent_id=AGENT_A))
        self.assertEqual(r.decision, "deny")
        self.assertIn("parent", r.reason)

    def test_a_spawned_member_cannot_be_spawned_again(self):
        m = self.plan()
        self.spawn(m, AGENT_A)
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_02"))
        self.assertEqual(r.decision, "deny")
        self.assertIn("active", r.reason)
        self.assertIn("planned", r.reason)

    def test_the_first_allow_reserves_the_planned_row(self):
        """Rooster's HIGH-1: before the binding lands, a second Agent call for the same planned
        member must not be allowed (N running copies of one row would count as one)."""
        m = self.plan()
        r1 = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_a"))
        self.assertEqual(r1.decision, "allow")
        r2 = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_b"))
        self.assertEqual(r2.decision, "deny", r2)
        self.assertIn("toolu_a", r2.reason)
        self.assertIn("not yet bound", r2.reason)
        self.assertIn("member finish", r2.reason)
        # SPD-028: Spud is the caller here (no agent_id), so the way out is --as spud with [--next]
        self.assertIn("`spud --as spud member finish %s --status failed --outcome '<why>' [--next '<what happens next>']`" % m["ref"], r2.reason)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM spawn_requests WHERE decision = 'allow'"), 1)
        # the same tool_use_id again (a deferred call resumed) is the same request, not a second spawn
        r3 = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_a"))
        self.assertEqual(r3.decision, "allow", r3)
        # a spawn that failed in the harness is recorded as failed and re-planned as a new row
        self.home.json("member", "finish", m["ref"], "--status", "failed", "--outcome", "Concurrent subagent limit reached", actor="spud")
        m2 = self.plan()
        r4 = self.home.hook("PreToolUse", self.pre_agent(self.description(m2), tool_use_id="toolu_c"))
        self.assertEqual(r4.decision, "allow", r4)

    def test_the_pending_reservation_refusal_names_a_spudagent_callers_own_actor(self):
        """SPD-028: a lead cannot run `spud --as spud` (Law 6 refuses it inside a subagent), so its
        own second spawn of a reserved child must be refused under its own agent_id, with no --next
        (a nested `member finish` refuses that option)."""
        lead = self.plan(persona="engineer", model="opus")
        lead = self.spawn(lead, AGENT_A)
        child = self.plan(actor=lead["ref"])
        r1 = self.home.hook("PreToolUse", self.pre_agent(self.description(child), agent_id=AGENT_A, tool_use_id="toolu_x"))
        self.assertEqual(r1.decision, "allow", r1)
        r2 = self.home.hook("PreToolUse", self.pre_agent(self.description(child), agent_id=AGENT_A, tool_use_id="toolu_y"))
        self.assertEqual(r2.decision, "deny", r2)
        self.assertIn("toolu_x", r2.reason)
        self.assertIn("not yet bound", r2.reason)
        self.assertIn("`spud --as %s member finish %s --status failed --outcome '<why>'`" % (AGENT_A, child["ref"]), r2.reason)
        self.assertNotIn("--next", r2.reason)
        self.assertNotIn("--as spud", r2.reason)

    def test_limits_are_recomputed_inside_the_hook(self):
        kids = [self.plan() for _ in range(3)]
        config = real_config()
        config["limits"]["root_fan_out"] = 2
        self.home.write_config(config)
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(kids[0])))
        self.assertEqual(r.decision, "deny")
        self.assertIn("fan-out", r.reason)
        self.assertIn("Law 4", r.reason)
        config["limits"]["root_fan_out"] = 3
        config["limits"]["max_concurrent_total"] = 2
        self.home.write_config(config)
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(kids[0])))
        self.assertEqual(r.decision, "deny")
        self.assertIn("concurren", r.reason)
        config["limits"]["max_concurrent_total"] = 9
        self.home.write_config(config)
        lead = self.spawn(kids[0], AGENT_A)
        child = self.plan(actor=lead["ref"])
        config["limits"]["max_depth"] = 1
        self.home.write_config(config)
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(child), agent_id=AGENT_A))
        self.assertEqual(r.decision, "deny")
        self.assertIn("depth", r.reason)
        # a freed slot makes room again
        config["limits"]["max_depth"] = 2
        config["limits"]["root_fan_out"] = 3
        self.home.write_config(config)
        self.home.json("member", "finish", kids[1]["ref"], "--status", "failed", "--outcome", "never spawned", actor="spud")
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(kids[2])))
        self.assertEqual(r.decision, "allow", r)

    def test_other_tools_get_no_decision(self):
        p = self.pre_bash("ls")
        p["tool_name"] = "Read"
        p["tool_input"] = {"file_path": "/etc/hosts"}
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""))

    def test_missing_database_denies_with_run_init(self):
        os.remove(self.home.db)
        for p in (self.pre_agent("x"), self.pre_bash("ls"), self.pre_edit(self.home.path / "x")):
            r = self.home.hook("PreToolUse", p)
            self.assertEqual((r.code, r.decision), (0, "deny"), r)
            self.assertIn("no ledger database at", r.reason)
            self.assertIn("spud init", r.reason)
        self.assertFalse(self.home.spool.exists())

    def test_malformed_payloads_fail_closed(self):
        m = self.plan()
        for raw in ("", "not json", "[1, 2]", '"str"'):
            r = self.home.hook("PreToolUse", raw)
            self.assertEqual(r.code, 2, (raw, r))
            self.assertIn("spud hook PreToolUse", r.stderr)
            self.assertEqual(r.stdout, "")
        p = self.pre_agent(self.description(m))
        del p["tool_use_id"]
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.decision), (0, "deny"), r)
        self.assertIn("tool_use_id", r.reason)
        p = self.pre_agent(self.description(m))
        p["hook_event_name"] = "PostToolUse"
        r = self.home.hook("PreToolUse", p)
        self.assertEqual(r.code, 2, r)
        self.assertIn("hook_event_name", r.stderr)
        p = self.pre_agent(self.description(m))
        p["tool_input"] = "nope"
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.decision), (0, "deny"), r)

    def test_unknown_event_is_a_usage_error(self):
        r = self.home.hook("Bogus", {})
        self.assertEqual(r.code, EXIT_USAGE)


# =============================================================================
# PostToolUse / Agent
# =============================================================================


class PostAgentTest(HookCase):
    def test_background_launch_binds_and_activates(self):
        m = self.plan()
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_bg"))
        self.assertEqual(pre.decision, "allow")
        st = self.home.hook("SubagentStart", self.sub_start(AGENT_A))
        self.assertEqual(st.code, 0)
        started = self.events("member.started")[0]
        self.assertIsNone(started["member"])
        self.assertEqual(started["agent_id"], AGENT_A)
        r = self.home.hook("PostToolUse", self.post_agent_launched("toolu_bg", AGENT_A, self.description(m)))
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""), r)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual(shown["status"], "active")
        self.assertEqual(shown["agent_id"], AGENT_A)
        self.assertEqual(shown["resolved_model"], "claude-haiku-4-5-20251001")
        self.assertIsNotNone(shown["spawned_at"])
        self.assertIsNone(shown["total_tokens"])
        row = self.home.rows("SELECT * FROM members WHERE id = ?", m["id"])[0]
        self.assertEqual((row["session_id"], row["tool_use_id"]), (SESSION, "toolu_bg"))
        self.assertEqual(self.home.scalar("SELECT agent_id FROM spawn_requests WHERE tool_use_id = 'toolu_bg'"), AGENT_A)
        # the child's earlier event is attached now
        started = self.events("member.started")[0]
        self.assertEqual(started["member"], m["ref"])
        self.assertEqual(started["ticket"], self.t["key"])
        kinds = [e["kind"] for e in self.events(member=m["ref"])]
        self.assertEqual(kinds, ["member.planned", "member.spawned", "member.started", "member.status"])
        # and the agent_id is now an actor for the CLI
        out = self.home.json("member", "log", "first line", actor=AGENT_A)
        self.assertTrue(out["ok"])
        self.assertEqual(out["member"]["ref"], m["ref"])

    def test_a_completion_alone_keeps_its_figures_and_leaves_total_tokens_empty(self):
        """SPD-021: totalTokens and usage cover the final request only, so with no transcript sum yet
        total_tokens stays empty and the figures are kept under "completion", away from the usage key
        token_counts reads; the whole-run duration and tool count fill their columns."""
        m = self.plan()
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_fg", run_in_background=False))
        self.assertEqual(pre.decision, "allow")
        r = self.home.hook("PostToolUse", self.post_agent_completed("toolu_fg", AGENT_B, self.description(m)))
        self.assertEqual(r.code, 0, r)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual(shown["status"], "active")
        self.assertEqual(shown["agent_id"], AGENT_B)
        self.assertEqual((shown["total_tokens"], shown["duration_ms"], shown["tool_uses"]), (None, 4791, 1))
        row = self.home.rows("SELECT return_text, usage_json FROM members WHERE id = ?", m["id"])[0]
        self.assertEqual(row["return_text"], "potato\n(done)")
        self.assertEqual(json.loads(row["usage_json"]), {"source": "PostToolUse", "completion": COMPLETION})

    def test_unknown_tool_use_id_is_a_gap_not_a_failure(self):
        r = self.home.hook("PostToolUse", self.post_agent_launched("toolu_unknown", AGENT_A, "whatever"))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        errs = self.events("hook.error")
        self.assertEqual(len(errs), 1)
        self.assertIn("toolu_unknown", errs[0]["body"])

    def test_binding_never_touches_a_finished_member(self):
        m = self.plan()
        self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_x"))
        self.home.json("member", "finish", m["ref"], "--status", "failed", "--outcome", "spawn failed", actor="spud")
        r = self.home.hook("PostToolUse", self.post_agent_launched("toolu_x", AGENT_A, self.description(m)))
        self.assertEqual(r.code, 0, r)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual(shown["status"], "failed")
        self.assertEqual(len(self.events("hook.error")), 1)

    def test_other_tools_are_ignored(self):
        p = self.post_agent_launched("toolu_bg", AGENT_A, "x")
        p["tool_name"] = "Bash"
        p["tool_response"] = {"stdout": "hi", "stderr": "", "interrupted": False, "isImage": False}
        r = self.home.hook("PostToolUse", p)
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""))
        self.assertEqual(self.events(), [e for e in self.events() if e["kind"] != "hook.error"])


# =============================================================================
# SubagentStart
# =============================================================================


class SubagentStartTest(HookCase):
    def test_context_names_the_agent_id_and_the_as_flag(self):
        r = self.home.hook("SubagentStart", self.sub_start(AGENT_A))
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.json["hookSpecificOutput"]["hookEventName"], "SubagentStart")
        self.assertEqual(r.context, "Ledger: your agent_id is `%s`; every `spud` command you run takes `--as %s`." % (AGENT_A, AGENT_A))
        e = self.events("member.started")[0]
        self.assertEqual((e["actor"], e["agent_id"], e["member"]), ("hook:SubagentStart", AGENT_A, None))
        self.assertEqual(e["data"]["agent_type"], "spudagent")

    def test_a_bound_agent_is_attached_and_named(self):
        m = self.plan()
        self.spawn(m, AGENT_A)
        r = self.home.hook("SubagentStart", self.sub_start(AGENT_A))  # a resume fires SubagentStart again
        self.assertIn(m["ref"], r.context)
        started = self.events("member.started")
        self.assertEqual([e["member"] for e in started], [m["ref"], m["ref"]])

    def test_missing_database_is_silent(self):
        os.remove(self.home.db)
        r = self.home.hook("SubagentStart", self.sub_start(AGENT_A))
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""))


# =============================================================================
# SubagentStop
# =============================================================================


class SubagentStopTest(HookCase):
    def test_hold_once_then_let_go_after_result(self):
        m = self.plan()
        self.spawn(m, AGENT_A)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, last="I am done."))
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.json["decision"], "block")
        self.assertIn("spud --as %s member result" % AGENT_A, r.json["reason"])
        self.assertIn("member block", r.json["reason"])
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertIsNone(shown["stopped_at"])
        self.assertEqual(shown["status"], "active")
        held = self.events("member.stopped")[-1]
        self.assertEqual((held["member"], held["data"]["held"]), (m["ref"], True))
        self.home.json("member", "result", "Produced x.", actor=AGENT_A)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, last="I am done."))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertIsNotNone(shown["stopped_at"])
        self.assertEqual(shown["status"], "active")  # the parent's verdict, never the hook's
        row = self.home.rows("SELECT return_text, transcript_path FROM members WHERE id = ?", m["id"])[0]
        self.assertEqual(row["return_text"], "I am done.")
        self.assertIn("agent-%s.jsonl" % AGENT_A, row["transcript_path"])
        final = self.events("member.stopped")[-1]
        self.assertEqual((final["data"]["held"], final["data"]["unrecorded"]), (False, False))

    def test_blocked_counts_as_recorded(self):
        m = self.plan()
        self.spawn(m, AGENT_A)
        self.home.json("member", "block", "Need Eric.", actor=AGENT_A)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual((r.code, r.stdout), (0, ""), r)

    def test_stop_hook_active_lets_an_unrecorded_return_go(self):
        m = self.plan()
        self.spawn(m, AGENT_A)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, stop_hook_active=True))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertIsNotNone(shown["stopped_at"])
        final = self.events("member.stopped")[-1]
        self.assertEqual((final["data"]["held"], final["data"]["unrecorded"], final["data"]["stop_hook_active"]), (False, True, True))

    def test_transcript_usage_is_summed_when_no_totals_were_recorded(self):
        m = self.plan()
        self.spawn(m, AGENT_A)
        self.home.json("member", "result", "ok", actor=AGENT_A)
        path = self.write_transcript(AGENT_A, self.two_requests())
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, transcript=str(path)))
        self.assertEqual(r.code, 0, r)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual(shown["total_tokens"], 442)
        self.assertEqual(shown["tool_uses"], 3)
        self.assertEqual(shown["duration_ms"], 4500)
        usage = json.loads(self.home.scalar("SELECT usage_json FROM members WHERE id = ?", m["id"]))
        self.assertEqual(usage, {"source": "transcript", "counting": "request", "messages": 2, "usage": TWO_REQUESTS_SUM, "breakdown": TWO_REQUESTS_BREAKDOWN})  # a background run: no completion

    def test_a_stop_counts_a_transcript_written_per_block_once_per_request(self):  # SPD-023, proof 4
        m = self.plan()
        self.spawn(m, AGENT_A)
        self.home.json("member", "result", "ok", actor=AGENT_A)
        path = self.write_transcript(AGENT_A, self.per_block())
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, transcript=str(path)))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        row = self.home.rows("SELECT total_tokens, duration_ms, tool_uses, usage_json FROM members WHERE id = ?", m["id"])[0]
        self.assertEqual((row["total_tokens"], row["duration_ms"], row["tool_uses"]), (442, 4500, 3))
        self.assertEqual(json.loads(row["usage_json"]), {"source": "transcript", "counting": "request", "messages": 2, "usage": TWO_REQUESTS_SUM, "breakdown": PER_BLOCK_BREAKDOWN})

    def test_a_transcript_sum_after_the_completion_fills_total_tokens_and_keeps_the_completion(self):
        """SPD-021, the reverse of the harness's foreground order: the completion alone leaves
        total_tokens empty and the later stop's transcript sum fills it; the completion's figures stay
        beside the sum, and its whole-run duration and tool count keep their columns."""
        m = self.plan()
        self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_fg"))
        self.home.hook("PostToolUse", self.post_agent_completed("toolu_fg", AGENT_B, self.description(m)))
        self.assertIsNone(self.home.json("member", "show", m["ref"])["member"]["total_tokens"])
        self.home.json("member", "result", "ok", actor=AGENT_B)
        path = self.write_transcript(AGENT_B, [self.assistant("a", {"input_tokens": 1, "output_tokens": 1}, "2026-09-12T13:30:01.000Z")])
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_B, transcript=str(path)))
        self.assertEqual(r.code, 0, r)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual((shown["total_tokens"], shown["duration_ms"], shown["tool_uses"]), (2, 4791, 1))
        usage = json.loads(self.home.scalar("SELECT usage_json FROM members WHERE id = ?", m["id"]))
        self.assertEqual(usage, {"source": "transcript", "counting": "request", "messages": 1, "completion": COMPLETION,
                                 "usage": {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                                 "breakdown": [{"requests": 1, "input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0}]})

    def test_foreground_binding_through_meta_json(self):
        m = self.plan()
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_meta", run_in_background=False))
        self.assertEqual(pre.decision, "allow")
        path = self.write_transcript(AGENT_B, [])
        meta = path.with_name("agent-%s.meta.json" % AGENT_B)
        meta.write_text(json.dumps({"agentType": "spudagent", "description": self.description(m), "toolUseId": "toolu_meta", "spawnDepth": 1, "requestShape": "foreground", "requestNonInteractive": False, "model": "haiku"}), encoding="utf-8")
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_B, transcript=str(path)))
        self.assertEqual(r.json["decision"], "block", r)  # bound, so the hold applies
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual((shown["status"], shown["agent_id"]), ("active", AGENT_B))
        stopped = self.events("member.stopped")[-1]
        self.assertEqual(stopped["data"]["bound_by"], "meta.json")
        self.assertEqual(self.home.scalar("SELECT agent_id FROM spawn_requests WHERE tool_use_id = 'toolu_meta'"), AGENT_B)

    def test_foreground_binding_falls_back_only_when_one_request_is_unbound(self):
        """Rooster's MEDIUM-5: with two unbound requests and no meta.json the hook does not guess."""
        m1 = self.plan()
        m2 = self.plan()
        self.home.hook("PreToolUse", self.pre_agent(self.description(m1), tool_use_id="toolu_first"))
        self.home.hook("PreToolUse", self.pre_agent(self.description(m2), tool_use_id="toolu_second"))
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_B, stop_hook_active=True))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertIsNone(self.home.json("member", "show", m1["ref"])["member"]["agent_id"])
        self.assertIsNone(self.home.json("member", "show", m2["ref"])["member"]["agent_id"])
        self.assertIsNone(self.events("member.stopped")[-1]["data"]["bound_by"])
        errs = self.events("hook.error")
        self.assertEqual(len(errs), 1)
        self.assertIn("2 unbound", errs[0]["body"])
        # bind one of them through PostToolUse; the other is now the only candidate
        self.home.hook("PostToolUse", self.post_agent_launched("toolu_first", AGENT_A, self.description(m1)))
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_B, stop_hook_active=True))
        self.assertEqual(r.code, 0, r)
        self.assertEqual(self.home.json("member", "show", m2["ref"])["member"]["agent_id"], AGENT_B)
        self.assertEqual(self.events("member.stopped")[-1]["data"]["bound_by"], "oldest-unbound")

    def test_an_unbound_stop_is_recorded_and_not_held(self):
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_C, agent_type="Explore"))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        e = self.events("member.stopped")[-1]
        self.assertEqual((e["member"], e["agent_id"], e["data"]["bound_by"]), (None, AGENT_C, None))

    def test_missing_database_is_silent(self):
        os.remove(self.home.db)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""))


# =============================================================================
# SubagentStop and PostToolUse(Agent): a member's run totals (SPD-021)
# =============================================================================


class RunTotalsTest(HookCase):
    """A foreground spawn fires SubagentStop, then PostToolUse(Agent, completed) (spike, Enforcement
    plan, fact 8).  In either order total_tokens and the usage key token_counts reads are the
    transcript sum; the completion, whose totalTokens and usage cover its final request only (hooks
    reference, Agent tool telemetry), is kept beside the sum; its totalDurationMs and
    totalToolUseCount, whole-run figures, fill duration_ms and tool_uses."""

    USAGE_COLUMNS = ("total_tokens", "duration_ms", "tool_uses", "usage_json")

    def usage_of(self, m):
        return self.home.rows("SELECT total_tokens, duration_ms, tool_uses, usage_json FROM members WHERE id = ?", m["id"])[0]

    def test_the_harness_order_keeps_the_transcript_sum_with_the_completion_beside_it(self):
        row = self.foreground(self.plan(), AGENT_B)
        self.assertEqual((row["total_tokens"], row["duration_ms"], row["tool_uses"]), (442, 4791, 1))
        self.assertEqual(json.loads(row["usage_json"]), {"source": "transcript", "counting": "request", "messages": 2, "usage": TWO_REQUESTS_SUM,
                                                          "breakdown": TWO_REQUESTS_BREAKDOWN, "completion": COMPLETION})

    def test_the_reverse_order_ends_in_the_same_row(self):
        harness_order = self.foreground(self.plan(), AGENT_B)
        reverse = self.foreground(self.plan(), AGENT_C, order="completion-first")
        self.assertEqual(json.loads(reverse["usage_json"])["source"], "transcript")
        self.assertEqual({k: reverse[k] for k in self.USAGE_COLUMNS}, {k: harness_order[k] for k in self.USAGE_COLUMNS})

    def test_a_stop_without_a_readable_transcript_erases_nothing(self):
        summed = self.plan()
        self.foreground(summed, AGENT_B)
        alone = self.plan()
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(alone), tool_use_id="toolu_alone", run_in_background=False))
        self.assertEqual(pre.decision, "allow", pre)
        self.home.hook("PostToolUse", self.post_agent_completed("toolu_alone", AGENT_C, self.description(alone)))
        self.home.json("member", "result", "Built it.", actor=AGENT_C)
        no_usage = self.write_transcript(AGENT_D, [
            {"type": "user", "timestamp": "2026-09-12T13:30:00.000Z", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "timestamp": "2026-09-12T13:30:01.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "a"}]}},
        ])
        kept = [
            (summed, AGENT_B, (442, 4791, 1, {"source": "transcript", "counting": "request", "messages": 2, "usage": TWO_REQUESTS_SUM,
                                              "breakdown": TWO_REQUESTS_BREAKDOWN, "completion": COMPLETION})),
            (alone, AGENT_C, (None, 4791, 1, {"source": "PostToolUse", "completion": COMPLETION})),
        ]
        for m, agent_id, want in kept:
            for label, path in (("a missing transcript", self.home.path / "transcripts" / "missing.jsonl"), ("no assistant usage", no_usage)):
                with self.subTest(member=m["name"], transcript=label):
                    r = self.home.hook("SubagentStop", self.sub_stop(agent_id, transcript=str(path)))
                    self.assertEqual((r.code, r.stdout), (0, ""), r)
                    row = self.usage_of(m)
                    self.assertEqual((row["total_tokens"], row["duration_ms"], row["tool_uses"], json.loads(row["usage_json"])), want)

    def test_a_stored_transcript_sum_is_not_summed_again(self):
        m = self.plan()
        before = self.foreground(m, AGENT_B)
        self.assertEqual(before["total_tokens"], 442)
        longer = self.write_transcript(AGENT_D, self.two_requests() + [
            self.assistant("c", {"input_tokens": 1000, "output_tokens": 1000}, "2026-09-12T13:31:00.000Z", tool_uses=5)])
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_B, transcript=str(longer)))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertEqual(self.usage_of(m), {k: before[k] for k in self.USAGE_COLUMNS})

    def test_both_orders_count_a_transcript_written_per_block_once_per_request(self):  # SPD-023, proof 4
        for order, agent_id in (("stop-first", AGENT_B), ("completion-first", AGENT_C)):
            with self.subTest(order=order):
                row = self.foreground(self.plan(), agent_id, order=order, entries=self.per_block())
                self.assertEqual((row["total_tokens"], row["duration_ms"], row["tool_uses"]), (442, 4791, 1))
                self.assertEqual(json.loads(row["usage_json"]),
                                 {"source": "transcript", "counting": "request", "messages": 2, "usage": TWO_REQUESTS_SUM, "breakdown": PER_BLOCK_BREAKDOWN, "completion": COMPLETION})

    def test_a_stop_leaves_a_sum_counted_per_entry_to_member_resum(self):  # SPD-023: a stored sum is not summed again at a stop
        m = self.plan()
        self.spawn(m, AGENT_A)
        self.home.json("member", "result", "ok", actor=AGENT_A)
        path = self.write_transcript(AGENT_A, self.per_block())
        old = json.dumps({"source": "transcript", "messages": 5, "usage": PER_ENTRY_SUM})
        self.set_member(m["id"], total_tokens=994, duration_ms=4500, tool_uses=3, usage_json=old)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, transcript=str(path)))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertEqual(self.usage_of(m), {"total_tokens": 994, "duration_ms": 4500, "tool_uses": 3, "usage_json": old})


# =============================================================================
# transcript_usage: each API request counted once (SPD-023)
# =============================================================================


class RequestCountingTest(HookCase):
    """The harness writes an API response as one transcript entry per content block, each repeating the
    request's message id, requestId and usage, with only output_tokens growing to its final count.  A sum
    groups the entries by request and counts each once, by its last entry; tool uses count distinct
    tool_use blocks; the sum is marked "counting": "request".  Before SPD-023 every entry was added."""

    in_process = True  # SPD-231: no hook runs here; the class's home is built once, in this process

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.spud = load_spud_module()

    def sum_of(self, entries):
        return self.spud.transcript_usage(self.write_transcript(AGENT_A, entries))

    def test_a_request_written_as_several_entries_counts_once_by_its_last_entry(self):  # proof 1
        shared = {"input_tokens": 2, "cache_creation_input_tokens": 75049, "cache_read_input_tokens": 0}
        got = self.sum_of([
            {"type": "user", "timestamp": "2026-09-13T09:00:00.000Z", "message": {"role": "user", "content": "go"}},
            self.block("msg_A", "req_A", {"type": "thinking", "thinking": "", "signature": "s"}, dict(shared, output_tokens=5), "2026-09-13T09:00:01.000Z"),
            self.block("msg_A", "req_A", {"type": "text", "text": "a"}, dict(shared, output_tokens=120), "2026-09-13T09:00:02.000Z"),
            self.block("msg_A", "req_A", self.tool_use("toolu_A1"), dict(shared, output_tokens=234), "2026-09-13T09:00:03.000Z"),
            self.block("msg_B", "req_B", {"type": "text", "text": "b"},
                       {"input_tokens": 3, "output_tokens": 40, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 75049}, "2026-09-13T09:00:05.000Z"),
        ])
        self.assertEqual(got, {"total_tokens": 150377, "duration_ms": 5000, "tool_uses": 1, "usage_json": {
            "source": "transcript", "counting": "request", "messages": 2,
            "usage": {"input_tokens": 5, "output_tokens": 274, "cache_creation_input_tokens": 75049, "cache_read_input_tokens": 75049},
            "breakdown": [{"requests": 2, "input_tokens": 5, "output_tokens": 274, "cache_read_input_tokens": 75049, "cache_creation_unsplit_input_tokens": 75049}]}})

    def test_the_request_is_the_message_id_with_its_request_id(self):  # the grouping key
        usage = {"input_tokens": 1, "output_tokens": 2, "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4}
        cases = [
            ("a message id without a requestId", [self.block("msg_A", None, {"type": "text", "text": "a"}, usage, "2026-09-13T09:00:01.000Z"),
                                                  self.block("msg_A", None, self.tool_use("toolu_A1"), usage, "2026-09-13T09:00:02.000Z")], 1),
            ("one message id with two requestIds", [self.block("msg_A", "req_A", {"type": "text", "text": "a"}, usage, "2026-09-13T09:00:01.000Z"),
                                                    self.block("msg_A", "req_B", {"type": "text", "text": "b"}, usage, "2026-09-13T09:00:02.000Z")], 2),
        ]
        for label, entries, requests in cases:
            with self.subTest(label):
                got = self.sum_of(entries)
                self.assertEqual((got["usage_json"]["messages"], got["total_tokens"], got["usage_json"]["counting"]), (requests, 10 * requests, "request"))

    def test_tool_uses_count_distinct_tool_use_blocks(self):  # proof 2
        first = {"input_tokens": 1, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 100}
        second = {"input_tokens": 2, "output_tokens": 3, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 110}
        thinking = {"type": "thinking", "thinking": "", "signature": "s"}
        one_block_each = [
            self.block("msg_A", "req_A", thinking, dict(first, output_tokens=1), "2026-09-13T09:00:01.000Z"),
            self.block("msg_A", "req_A", self.tool_use("toolu_A1"), dict(first, output_tokens=2), "2026-09-13T09:00:02.000Z"),
            self.block("msg_A", "req_A", self.tool_use("toolu_A2"), dict(first, output_tokens=3), "2026-09-13T09:00:03.000Z"),
            self.block("msg_B", "req_B", self.tool_use("toolu_B1"), second, "2026-09-13T09:00:04.000Z"),
        ]
        cases = [
            ("one block per entry", one_block_each),
            ("a block id written twice", one_block_each + [self.block("msg_B", "req_B", self.tool_use("toolu_B1"), second, "2026-09-13T09:00:04.000Z")]),
            ("the same blocks in one entry each", [
                self.block("msg_A", "req_A", [thinking, self.tool_use("toolu_A1"), self.tool_use("toolu_A2")], dict(first, output_tokens=3), "2026-09-13T09:00:03.000Z"),
                self.block("msg_B", "req_B", [self.tool_use("toolu_B1")], second, "2026-09-13T09:00:04.000Z"),
            ]),
        ]
        for label, entries in cases:
            with self.subTest(label):
                got = self.sum_of(entries)
                self.assertEqual((got["tool_uses"], got["usage_json"]["messages"], got["total_tokens"]), (3, 2, 114 + 115))
        with self.subTest("blocks without an id count where they appear"):
            got = self.sum_of([
                self.block("msg_A", "req_A", [self.tool_use(None), self.tool_use(None)], dict(first, output_tokens=2), "2026-09-13T09:00:01.000Z"),
                self.block("msg_A", "req_A", self.tool_use(None), dict(first, output_tokens=3), "2026-09-13T09:00:02.000Z"),
            ])
            self.assertEqual((got["tool_uses"], got["usage_json"]["messages"], got["total_tokens"]), (3, 1, 114))

    def test_entries_without_a_message_id_count_once_each(self):  # proof 3
        usage = {"input_tokens": 1, "output_tokens": 2, "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4}
        got = self.sum_of([
            self.assistant("a", usage, "2026-09-13T09:00:01.000Z"),  # neither a message id nor a requestId
            self.assistant("b", usage, "2026-09-13T09:00:02.000Z"),
            self.block(None, "req_C", {"type": "text", "text": "c"}, usage, "2026-09-13T09:00:03.000Z"),  # a requestId alone
            self.block(None, "req_C", {"type": "text", "text": "d"}, usage, "2026-09-13T09:00:04.000Z"),
        ])
        self.assertEqual(got, {"total_tokens": 40, "duration_ms": 3000, "tool_uses": 0, "usage_json": {
            "source": "transcript", "counting": "request", "messages": 4,
            "usage": {"input_tokens": 4, "output_tokens": 8, "cache_creation_input_tokens": 12, "cache_read_input_tokens": 16},
            "breakdown": [{"requests": 4, "input_tokens": 4, "output_tokens": 8, "cache_read_input_tokens": 16, "cache_creation_unsplit_input_tokens": 12}]}})


# =============================================================================
# SubagentStop / the lead hold (SPD-015)
# =============================================================================


class LeadHoldTest(HookCase):
    """Law 9 below Spud: a member that returns while a child it spawned has returned
    unrecorded (a final stop, no outcome) is held once, in the same block as the Result hold."""

    RESULT_ONLY = ("record your Result with `spud --as %s member result '<what you produced, where, what you verified, what is left>'`"
                   " (or Blocked with `spud --as %s member block '<the question and the options>'`) before returning; then return your summary")

    def lead(self, agent_id=AGENT_A, result="Built the thing."):
        """A root member, spawned and bound, with its own Result recorded unless result is None."""
        m = self.spawn(self.plan(persona="engineer", model="opus"), agent_id)
        if result is not None:
            self.home.json("member", "result", result, actor=agent_id)
        return m

    def child_of(self, lead, agent_id, returned=True):
        """A child planned and spawned by the lead; by default it records its Result and stops."""
        child = self.spawn(self.plan(actor=lead["ref"]), agent_id, caller=lead["agent_id"])
        if returned:
            self.home.json("member", "result", "Child done.", actor=agent_id)
            r = self.home.hook("SubagentStop", self.sub_stop(agent_id))
            self.assertEqual((r.code, r.stdout), (0, ""), r)
        return self.home.json("member", "show", child["ref"])["member"]

    def finish_command(self, agent_id, ref):
        return "`spud --as %s member finish %s --status done|blocked|failed --outcome '<verdict>'`" % (agent_id, ref)

    def stopped_events(self, ref):
        return self.events("member.stopped", member=ref)

    def test_a_lead_with_its_result_recorded_is_held_once_for_a_returned_unrecorded_child(self):
        lead = self.lead()
        child = self.child_of(lead, AGENT_B)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, last="All done."))
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 1 child you spawned returned and is not recorded: "), reason)
        self.assertIn("%s (01.01, scout), stopped %s" % (child["ref"], child["stopped_at"][11:16]), reason)
        self.assertIn(self.finish_command(AGENT_A, child["ref"]), reason)
        self.assertIn("`spud proposal list --open`", reason)
        self.assertIn("`spud --as %s proposal decide " % AGENT_A, reason)
        self.assertNotIn("member result", reason)  # its own Result is recorded
        self.assertNotIn("--next", reason)  # Law 9 one level down never offers it (SPD-027 touched Spud's Stop only)
        self.assertTrue(reason.endswith(" Then return your summary."), reason)
        # held, so nothing is stamped: stopped_at belongs to the final stop
        shown = self.home.json("member", "show", lead["ref"])["member"]
        self.assertIsNone(shown["stopped_at"])
        self.assertEqual(shown["status"], "active")
        held = self.stopped_events(lead["ref"])[-1]
        self.assertEqual((held["data"]["held"], held["data"]["unrecorded"], held["data"]["unrecorded_children"]), (True, False, [child["ref"]]))

    def test_stop_hook_active_lets_the_lead_go_and_records_the_unrecorded_children(self):
        lead = self.lead()
        child = self.child_of(lead, AGENT_B)
        self.assertEqual(self.home.hook("SubagentStop", self.sub_stop(AGENT_A)).json["decision"], "block")
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, stop_hook_active=True))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        shown = self.home.json("member", "show", lead["ref"])["member"]
        self.assertIsNotNone(shown["stopped_at"])
        self.assertEqual(shown["status"], "active")  # the parent's verdict, never the hook's
        final = self.stopped_events(lead["ref"])[-1]
        self.assertEqual((final["data"]["held"], final["data"]["unrecorded"], final["data"]["unrecorded_children"]), (False, False, [child["ref"]]))
        self.assertIn(child["ref"], final["body"])

    def test_after_the_lead_finishes_the_child_it_is_not_held(self):
        lead = self.lead()
        child = self.child_of(lead, AGENT_B)
        self.assertEqual(self.home.hook("SubagentStop", self.sub_stop(AGENT_A)).json["decision"], "block")
        out = self.home.json("member", "finish", child["ref"], "--status", "done", "--outcome", "Accepted.", actor=AGENT_A)
        self.assertEqual(out["member"]["status"], "done")
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))  # a fresh stop, not the let-go continuation
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        final = self.stopped_events(lead["ref"])[-1]
        self.assertEqual((final["data"]["held"], final["data"]["unrecorded"]), (False, False))
        self.assertNotIn("unrecorded_children", final["data"])

    def test_one_block_names_the_child_first_and_then_the_missing_result(self):
        lead = self.lead(result=None)
        child = self.child_of(lead, AGENT_B)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 1 child you spawned returned and is not recorded: "), reason)
        self.assertLess(reason.index(self.finish_command(AGENT_A, child["ref"])), reason.index("member result"), reason)
        self.assertTrue(reason.endswith(" Then " + self.RESULT_ONLY % (AGENT_A, AGENT_A)), reason)
        held = [e for e in self.stopped_events(lead["ref"]) if e["data"]["held"]]
        self.assertEqual(len(held), 1)
        self.assertEqual((held[0]["data"]["unrecorded"], held[0]["data"]["unrecorded_children"]), (True, [child["ref"]]))
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, stop_hook_active=True))
        self.assertEqual((r.code, r.stdout), (0, ""), r)

    def test_a_lead_that_recorded_blocked_is_still_held_for_its_child(self):
        lead = self.lead(result=None)
        self.home.json("member", "block", "Need Eric.", actor=AGENT_A)
        child = self.child_of(lead, AGENT_B)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["decision"], "block", r)
        self.assertIn(self.finish_command(AGENT_A, child["ref"]), r.json["reason"])
        self.assertNotIn("member result", r.json["reason"])

    def test_every_unrecorded_child_is_listed_with_its_own_command(self):
        lead = self.lead()
        first = self.child_of(lead, AGENT_B)
        second = self.child_of(lead, AGENT_C)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 2 children you spawned returned and are not recorded: "), reason)
        self.assertLess(reason.index(first["ref"]), reason.index(second["ref"]), reason)  # oldest stop first
        for m in (first, second):
            self.assertIn("%s (%s, scout), stopped %s" % (m["ref"], m["lineage"], m["stopped_at"][11:16]), reason)
            self.assertIn(self.finish_command(AGENT_A, m["ref"]), reason)
        held = self.stopped_events(lead["ref"])[-1]
        self.assertEqual(held["data"]["unrecorded_children"], [first["ref"], second["ref"]])

    def test_finished_children_and_a_siblings_children_are_not_listed(self):
        lead = self.lead()
        sibling = self.lead(agent_id=AGENT_C)
        recorded = self.child_of(lead, AGENT_B)
        self.home.json("member", "finish", recorded["ref"], "--status", "done", "--outcome", "Accepted.", actor=AGENT_A)
        nephew = self.child_of(sibling, AGENT_D)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual((r.code, r.stdout), (0, ""), r)  # nothing of the lead's is unrecorded
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_C))
        self.assertEqual(r.json["decision"], "block", r)
        self.assertIn(nephew["ref"], r.json["reason"])
        self.assertNotIn(recorded["ref"], r.json["reason"])

    def test_the_result_only_reason_is_unchanged(self):
        self.lead(result=None)  # no children at all
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["reason"], self.RESULT_ONLY % (AGENT_A, AGENT_A))
        other = self.lead(agent_id=AGENT_C, result=None)  # every child recorded
        child = self.child_of(other, AGENT_D)
        self.home.json("member", "finish", child["ref"], "--status", "done", "--outcome", "Accepted.", actor=AGENT_C)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_C))
        self.assertEqual(r.json["reason"], self.RESULT_ONLY % (AGENT_C, AGENT_C))

    def test_a_lead_is_held_once_for_a_child_that_is_still_running(self):
        """The probe of 2026-09-12: a background child is not killed when its lead returns, its
        completion notification goes to the main session, and its row is orphaned.  The block is
        the only thing that keeps the lead alive long enough to collect it."""
        lead = self.lead()
        child = self.child_of(lead, AGENT_B, returned=False)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 1 child you planned is still alive: "), reason)
        self.assertIn("%s (01.01, scout), running since %s" % (child["ref"], child["spawned_at"][11:16]), reason)
        self.assertIn(self.finish_command(AGENT_A, child["ref"]), reason)
        self.assertIn("Wait inside this turn", reason)
        self.assertIn("`spud member show %s`" % child["ref"], reason)
        self.assertNotIn("--next", reason)  # Law 9 one level down never offers it (SPD-027 touched Spud's Stop only)
        held = self.stopped_events(lead["ref"])[-1]
        self.assertEqual(held["data"]["alive_children"], [child["ref"]])
        self.assertNotIn("unrecorded_children", held["data"])
        self.assertIsNone(self.home.json("member", "show", lead["ref"])["member"]["stopped_at"])
        # it waited; the child returns, and the let-go stop records it as unrecorded instead
        self.home.json("member", "result", "Child done.", actor=AGENT_B)
        self.home.hook("SubagentStop", self.sub_stop(AGENT_B))
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, stop_hook_active=True))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        final = self.stopped_events(lead["ref"])[-1]
        self.assertEqual(final["data"]["unrecorded_children"], [child["ref"]])
        self.assertNotIn("alive_children", final["data"])

    def test_a_planned_child_that_was_never_spawned_holds_the_lead_once(self):
        lead = self.lead()
        child = self.plan(actor=lead["ref"])
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertIn("%s (01.01, scout), planned %s and never spawned" % (child["ref"], child["planned_at"][11:16]), reason)
        self.assertIn("`spud --as %s member finish %s --status failed --outcome '<why>'`" % (AGENT_A, child["ref"]), reason)
        self.assertNotIn("Wait inside this turn", reason)  # nothing to wait for: it never started
        # SPD-028: a child with no reservation keeps today's words byte for byte
        expected = ("Law 9: 1 child you planned is still alive: %s (01.01, scout), planned %s and never spawned."
                    " The planned child never started: spawn it now, or record it with `spud --as %s member finish %s --status failed --outcome '<why>'`"
                    " so the row stops counting against your limits. Then return your summary."
                    % (child["ref"], child["planned_at"][11:16], AGENT_A, child["ref"]))
        self.assertEqual(reason, expected)
        self.assertEqual(self.stopped_events(lead["ref"])[-1]["data"]["alive_children"], [child["ref"]])
        out = self.home.json("member", "finish", child["ref"], "--status", "failed", "--outcome", "Never spawned.", actor=AGENT_A)
        self.assertEqual(out["member"]["status"], "failed")
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual((r.code, r.stdout), (0, ""), r)

    def test_a_reserved_spawn_that_never_bound_is_still_alive(self):
        lead = self.lead()
        child = self.plan(actor=lead["ref"])
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(child), agent_id=AGENT_A, tool_use_id="toolu_reserved"))
        self.assertEqual(pre.decision, "allow", pre)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertIn(child["ref"], reason)
        self.assertEqual(self.stopped_events(lead["ref"])[-1]["data"]["alive_children"], [child["ref"]])
        # SPD-028: a reserved child is not "never spawned" and is never offered "spawn it now" (the
        # reservation would refuse it); its item names the reservation, and its way out is the
        # hook.error check, polling inside this turn, and the lead's own actor, no grace period.
        req_at = self.home.scalar("SELECT at FROM spawn_requests WHERE tool_use_id = 'toolu_reserved'")
        self.assertIn("%s (01.01, scout), spawn allowed %s (tool_use_id toolu_reserved), never bound" % (child["ref"], req_at[11:16]), reason)
        self.assertNotIn("spawn it now", reason)
        self.assertNotIn("and never spawned", reason)
        self.assertIn("`spud events --kind hook.error --json`", reason)
        self.assertIn("wait inside this turn", reason)
        self.assertIn("`spud member show %s`" % child["ref"], reason)
        self.assertIn("`spud --as %s member finish %s --status failed --outcome '<why>'`" % (AGENT_A, child["ref"]), reason)
        self.assertIn("plan a new member", reason)
        self.assertIn("reservation refuses a second spawn", reason)
        self.assertNotIn("--next", reason)  # a nested finish refuses it
        self.assertNotIn("--as spud", reason)  # the lead cannot run this
        # the way out works: record it failed under the lead's own actor, then the hold clears
        out = self.home.json("member", "finish", child["ref"], "--status", "failed", "--outcome", "Harness failed the spawn.", actor=AGENT_A)
        self.assertEqual(out["member"]["status"], "failed")
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual((r.code, r.stdout), (0, ""), r)

    def test_one_block_names_a_plain_planned_child_and_a_reserved_one_in_their_own_words(self):
        lead = self.lead()
        plain = self.plan(actor=lead["ref"])
        reserved = self.plan(actor=lead["ref"])
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(reserved), agent_id=AGENT_A, tool_use_id="toolu_reserved2"))
        self.assertEqual(pre.decision, "allow", pre)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 2 children you planned are still alive: "), reason)
        self.assertLess(reason.index(plain["ref"]), reason.index(reserved["ref"]), reason)  # planned first
        self.assertIn("%s (01.01, scout), planned %s and never spawned" % (plain["ref"], plain["planned_at"][11:16]), reason)
        req_at = self.home.scalar("SELECT at FROM spawn_requests WHERE tool_use_id = 'toolu_reserved2'")
        self.assertIn("%s (01.02, scout), spawn allowed %s (tool_use_id toolu_reserved2), never bound" % (reserved["ref"], req_at[11:16]), reason)
        self.assertIn("The planned child never started: spawn it now, or record it with `spud --as %s member finish %s --status failed --outcome '<why>'`"
                      % (AGENT_A, plain["ref"]), reason)
        self.assertIn("The planned child was allowed to spawn and never bound: first look for its tool_use_id in `spud events --kind hook.error --json`", reason)
        self.assertIn("`spud --as %s member finish %s --status failed --outcome '<why>'` and plan a new member" % (AGENT_A, reserved["ref"]), reason)
        self.assertNotIn("spawn %s now" % reserved["ref"], reason)
        held = self.stopped_events(lead["ref"])[-1]
        self.assertEqual(held["data"]["alive_children"], [plain["ref"], reserved["ref"]])

    def test_one_block_covers_a_returned_child_and_a_running_one(self):
        lead = self.lead()
        returned = self.child_of(lead, AGENT_B)
        running = self.child_of(lead, AGENT_C, returned=False)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 1 child you spawned returned and is not recorded: "), reason)
        self.assertIn("1 child you planned is still alive: ", reason)
        self.assertLess(reason.index(returned["ref"]), reason.index("is still alive"), reason)
        self.assertIn(self.finish_command(AGENT_A, running["ref"]), reason)
        held = self.stopped_events(lead["ref"])[-1]
        self.assertEqual((held["data"]["unrecorded_children"], held["data"]["alive_children"]), ([returned["ref"]], [running["ref"]]))

    def test_spud_records_an_orphaned_grandchild_once_its_lead_is_finished(self):
        lead = self.lead()
        child = self.child_of(lead, AGENT_B)
        self.assertEqual(self.home.hook("SubagentStop", self.sub_stop(AGENT_A)).json["decision"], "block")
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, stop_hook_active=True))  # ignored the hold
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        r = self.home.hook("Stop", self.stop())
        self.assertEqual(r.json["decision"], "block", r)
        self.assertIn(lead["ref"], r.json["reason"])
        self.assertNotIn(child["ref"], r.json["reason"])  # a grandchild is its living lead's business
        self.home.json("member", "finish", lead["ref"], "--status", "done", "--outcome", "Accepted; its child is not recorded.", actor="spud")
        r = self.home.hook("Stop", self.stop())
        self.assertEqual(r.json["decision"], "block", r)
        self.assertIn(child["ref"], r.json["reason"])
        out = self.home.json("member", "finish", child["ref"], "--status", "done", "--outcome", "Recorded by Spud: orphaned.", actor="spud")
        self.assertEqual((out["member"]["status"], out["member"]["outcome"]), ("done", "Recorded by Spud: orphaned."))
        r = self.home.hook("Stop", self.stop())
        self.assertEqual((r.code, r.stdout), (0, ""), r)


if __name__ == "__main__":
    unittest.main()
