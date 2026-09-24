"""SessionStart, Stop and the enforcing hooks' failure policy, late binding, and the CLI's own checks around them."""

import json
import os
import unittest
from datetime import datetime, timedelta

from helpers import EXIT_ERROR, EXIT_USAGE, spawn_type, SpudTestCase
from hookcase import AGENT_A, AGENT_B, AGENT_C, AGENT_D, SESSION, HookCase


SESSION_B = "7d1e6a0c-5b2f-4c8d-9e3a-1f2b3c4d5e6f"  # a second Spud session working in parallel (SPD-018)


# =============================================================================
# SessionStart and Stop
# =============================================================================


class SessionStartTest(HookCase):
    def test_board_brief_is_injected(self):
        m = self.plan(name="Kestrel")
        r = self.home.hook("SessionStart", self.session_start())
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.json["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn(self.t["key"], r.context)
        self.assertIn("Kestrel", r.context)
        self.assertIn(self.home.run("board", "--brief").stdout.strip(), r.context)
        for source in ("resume", "compact"):
            self.assertEqual(self.home.hook("SessionStart", self.session_start(source)).code, 0)
        # SPD-096: a parked ticket that is not due leaves the injection for the count line alone
        shelved = self.new_ticket("Shelved", status="queued")
        self.home.json("ticket", "move", shelved["key"], "--status", "parked", "--reason", "Eric's go", "--until", "2099-01-01", actor="spud")
        r = self.home.hook("SessionStart", self.session_start())
        self.assertNotIn("Shelved", r.context)
        self.assertIn("1 parked (spud board --parked)", r.context)

    def test_clear_injects_the_board_as_the_other_sources_do(self):
        # SPD-011: the matcher takes clear, and a /clear gets the same context as the other three
        self.plan(name="Kestrel")
        board = self.home.run("board", "--brief").stdout.strip()
        for source in ("startup", "resume", "clear", "compact"):
            r = self.home.hook("SessionStart", self.session_start(source))
            self.assertEqual(r.code, 0, r)
            self.assertEqual(r.json["hookSpecificOutput"]["hookEventName"], "SessionStart", source)
            self.assertIn("source %s)" % source, r.context)
            self.assertIn(board, r.context)

    def test_missing_database_is_silent(self):
        os.remove(self.home.db)
        r = self.home.hook("SessionStart", self.session_start())
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""))


class StopTest(HookCase):
    def returned_unrecorded(self, name=None):
        m = self.spawn(self.plan(name=name), AGENT_A if name != "Yukon" else AGENT_B)
        agent = m["agent_id"]
        self.home.json("member", "result", "done", actor=agent)
        r = self.home.hook("SubagentStop", self.sub_stop(agent))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        return m

    def test_nothing_unrecorded_lets_the_turn_end(self):
        self.spawn(self.plan(), AGENT_A)  # still running
        r = self.home.hook("Stop", self.stop())
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""), r)

    def test_law_9_blocks_once_with_the_list(self):
        m = self.returned_unrecorded("Kestrel")
        r = self.home.hook("Stop", self.stop())
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.json["decision"], "block")
        self.assertIn(m["ref"], r.json["reason"])
        self.assertIn("spud --as spud member finish", r.json["reason"])
        self.assertIn("Law 9", r.json["reason"])
        d = self.denied()
        self.assertEqual((d[-1]["actor"], d[-1]["data"]["hook_event_name"]), ("hook:Stop", "Stop"))
        r = self.home.hook("Stop", self.stop(stop_hook_active=True))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.home.json("member", "finish", m["ref"], "--status", "done", "--outcome", "Accepted.", actor="spud")
        r = self.home.hook("Stop", self.stop())
        self.assertEqual((r.code, r.stdout), (0, ""), r)

    def test_a_grandchild_is_its_leads_business_while_the_lead_lives(self):
        lead = self.spawn(self.plan(persona="engineer", model="opus"), AGENT_A)
        child = self.plan(actor=lead["ref"])
        child = self.spawn(child, AGENT_B, caller=AGENT_A)
        self.home.json("member", "result", "done", actor=AGENT_B)
        self.home.hook("SubagentStop", self.sub_stop(AGENT_B))
        r = self.home.hook("Stop", self.stop())
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.home.json("member", "finish", lead["ref"], "--status", "failed", "--outcome", "died", actor="spud")
        r = self.home.hook("Stop", self.stop())
        self.assertEqual(r.json["decision"], "block")
        self.assertIn(child["ref"], r.json["reason"])

    def test_a_subagents_stop_is_a_no_op(self):
        self.returned_unrecorded("Kestrel")
        r = self.home.hook("Stop", self.stop(agent_id=AGENT_B))
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""), r)

    def test_missing_database_is_silent(self):
        os.remove(self.home.db)
        r = self.home.hook("Stop", self.stop())
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""))


class StopSessionTest(HookCase):
    """Spud's Stop hook, one session at a time (SPD-018).  Eric runs tickets in parallel sessions, so a session is
    held only for what it owes: the members of the trees it spawned, and a row with no known session (from before
    sessions were recorded).  Three kinds, in one block: returned and unrecorded, planned and never spawned, and
    still running under a finished parent (told once per session)."""

    # Today's reason when only returned members are listed, kept byte for byte for a root member; since SPD-027 the
    # printed command also carries --next (member finish's own Next line, since a root member's finish is the one
    # that writes a report entry).
    RETURNED_ONLY = ("Law 9: %d returned spudagent(s) are not recorded: %s. Record each with `spud --as spud member finish <SPUD-nnn/Name>"
                     " --status done|blocked|failed --outcome '<verdict>' [--summary '<one paragraph>'] [--next '<what happens next>']`,"
                     " decide its proposals (spud proposal list --open; spud --as spud proposal decide ...), then end the turn.")
    # A nested returned member (its parent finished): the SPD-008 wording, unchanged, since member finish writes no
    # report entry and so no Next line for a child.
    RETURNED_ONLY_NESTED = ("Law 9: %d returned spudagent(s) are not recorded: %s. Record each with `spud --as spud member finish <SPUD-nnn/Name>"
                           " --status done|blocked|failed --outcome '<verdict>' [--summary '<one paragraph>']`, decide its proposals"
                           " (spud proposal list --open; spud --as spud proposal decide ...), then end the turn.")
    SESSION_C = "5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d"

    def in_session(self, session):
        """The session `member new` runs in from here on (None: outside every session)."""
        if session is None:
            self.home.env.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            self.home.env["CLAUDE_CODE_SESSION_ID"] = session

    def returned(self, agent_id, session=SESSION, name=None):
        """A root member planned and spawned in session that recorded its Result and stopped: returned, unrecorded."""
        self.in_session(session)
        m = self.spawn(self.plan(name=name), agent_id, session=session)
        self.home.json("member", "result", "Built it.", actor=agent_id)
        r = self.home.hook("SubagentStop", self.sub_stop(agent_id, session=session))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        return self.home.json("member", "show", m["ref"])["member"]

    def orphan(self, lead_agent, child_agent, session=SESSION):
        """A lead spawned in session and its child, still running after Spud finished the lead: (lead, child)."""
        self.in_session(session)
        lead = self.spawn(self.plan(persona="engineer", model="opus"), lead_agent, session=session)
        child = self.spawn(self.plan(actor=lead["ref"]), child_agent, caller=lead_agent, session=session)
        self.home.json("member", "finish", lead["ref"], "--status", "done", "--outcome", "Accepted.", actor="spud")
        return lead, child

    def stop_in(self, session, stop_hook_active=False):
        return self.home.hook("Stop", self.stop(stop_hook_active=stop_hook_active, session=session))

    def assertSilent(self, r):
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""), r)

    @staticmethod
    def ago(minutes):
        return (datetime.now().astimezone() - timedelta(minutes=minutes)).isoformat(timespec="seconds")

    def session_of(self, m):
        return self.home.scalar("SELECT session_id FROM members WHERE id = ?", m["id"])

    # -- returned and unrecorded ----------------------------------------------------
    def test_a_returned_member_holds_only_the_session_that_spawned_it(self):
        kestrel = self.returned(AGENT_A, SESSION, name="Kestrel")
        yukon = self.returned(AGENT_B, SESSION_B, name="Yukon")
        r = self.stop_in(SESSION)
        self.assertEqual((r.code, r.json["decision"]), (0, "block"), r)
        self.assertEqual(r.json["reason"], self.RETURNED_ONLY % (1, "SPUD-001/Kestrel (01, scout) on SPD-001, stopped %s" % kestrel["stopped_at"][:16]))
        r = self.stop_in(SESSION_B)
        self.assertEqual(r.json["reason"], self.RETURNED_ONLY % (1, "SPUD-001/Yukon (02, scout) on SPD-001, stopped %s" % yukon["stopped_at"][:16]))
        self.assertSilent(self.stop_in(self.SESSION_C))  # a third session owes neither
        self.assertEqual([d["data"] for d in self.denied()], [
            {"hook_event_name": "Stop", "session_id": SESSION, "members": ["SPUD-001/Kestrel"], "returned": ["SPUD-001/Kestrel"], "planned": [], "unbound": [], "running": []},
            {"hook_event_name": "Stop", "session_id": SESSION_B, "members": ["SPUD-001/Yukon"], "returned": ["SPUD-001/Yukon"], "planned": [], "unbound": [], "running": []},
        ])

    def test_the_printed_next_option_actually_runs(self):
        """The --next this reason offers a root member is not just words: filled in and run, it exits 0 and
        writes a report entry whose last line is the Next line (SPD-027)."""
        kestrel = self.returned(AGENT_A, SESSION, name="Kestrel")
        reason = self.stop_in(SESSION).json["reason"]
        self.assertIn("[--next '<what happens next>']", reason)
        before = len(self.events("report.entry"))
        out = self.home.json("member", "finish", kestrel["ref"], "--status", "done", "--outcome", "x", "--next", "y", actor="spud")
        self.assertEqual(out["member"]["status"], "done")
        entries = self.events("report.entry")
        self.assertEqual(len(entries), before + 1)
        self.assertTrue(entries[-1]["body"].endswith("- Next: y"), entries[-1])
        self.assertSilent(self.stop_in(SESSION))

    def test_the_spawn_request_names_the_session_when_the_row_records_none(self):
        m = self.returned(AGENT_A, SESSION_B)
        self.set_member(m["id"], session_id=None)
        self.assertSilent(self.stop_in(SESSION))
        self.assertEqual(self.stop_in(SESSION_B).json["decision"], "block")

    def test_a_member_with_no_known_session_holds_every_session(self):
        """A row from before sessions were recorded: no spawn request and no session on the row."""
        self.in_session(None)
        m = self.plan(name="Kestrel")
        self.home.json("member", "start", m["ref"], actor="spud")
        self.set_member(m["id"], stopped_at="2026-09-12T20:15:48-07:00")
        for session in (SESSION, SESSION_B):
            r = self.stop_in(session)
            self.assertEqual(r.json["reason"], self.RETURNED_ONLY % (1, "SPUD-001/Kestrel (01, scout) on SPD-001, stopped 2026-09-12T20:15"), (session, r))

    def test_a_stop_payload_without_a_session_owes_every_member(self):
        """session_id is a common input field (hooks reference), so a Stop without one is malformed: the hook holds
        as it did before SPD-018, ledger-wide, rather than letting every member go."""
        yukon = self.returned(AGENT_B, SESSION_B, name="Yukon")
        payload = self.stop()
        del payload["session_id"]
        r = self.home.hook("Stop", payload)
        self.assertEqual(r.json["reason"], self.RETURNED_ONLY % (1, "SPUD-001/Yukon (01, scout) on SPD-001, stopped %s" % yukon["stopped_at"][:16]), r)
        self.assertIsNone(self.denied()[-1]["data"]["session_id"])

    # -- planned and never spawned --------------------------------------------------
    def test_a_planned_row_never_spawned_holds_the_session_that_planned_it(self):
        m = self.plan(name="Kestrel")
        self.assertEqual(self.session_of(m), SESSION)  # member new records CLAUDE_CODE_SESSION_ID
        self.assertSilent(self.stop_in(SESSION_B))
        r = self.stop_in(SESSION)
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 1 planned spudagent(s) were never spawned: SPUD-001/Kestrel (01, scout) on SPD-001, planned %s. " % m["planned_at"][:16]), reason)
        self.assertIn("subagent_type `spudagent`, model `haiku`, description `SPUD-001/Kestrel (01, scout)`", reason)
        # a root row (Kestrel's parent_id is NULL): its failed command also offers --next, since SPD-027
        self.assertIn("`spud --as spud member finish SPUD-001/Kestrel --status failed --outcome '<why>' [--next '<what happens next>']`", reason)
        self.assertTrue(reason.endswith(". Then end the turn."), reason)
        self.assertEqual(self.denied()[-1]["data"]["planned"], ["SPUD-001/Kestrel"])
        self.assertEqual(self.stop_in(SESSION).json["decision"], "block")  # every fresh stop, until it is spawned or recorded
        self.home.json("member", "finish", m["ref"], "--status", "failed", "--outcome", "Never spawned.", actor="spud")
        self.assertSilent(self.stop_in(SESSION))

    def test_the_never_spawned_hold_names_the_effort_variant_to_spawn(self):
        # SPD-222: the way out it prints is a spawn the check would allow, so a member planned at an effort is named by its variant
        m = self.plan(name="Kestrel", persona="engineer", model="opus", effort="medium")
        reason = self.stop_in(SESSION).json["reason"]
        self.assertIn("subagent_type `spudagent-medium`, model `opus`, description `SPUD-001/Kestrel (01, engineer)`", reason)
        self.home.json("member", "finish", m["ref"], "--status", "failed", "--outcome", "Never spawned.", actor="spud")

    def test_a_planned_row_with_no_known_session_holds_any_session_after_ten_minutes(self):
        self.in_session(None)
        m = self.plan(name="Kestrel")
        self.assertIsNone(self.session_of(m))
        self.assertSilent(self.stop_in(SESSION))  # planned a moment ago, perhaps by a session about to spawn it
        self.set_member(m["id"], planned_at=self.ago(9))
        self.assertSilent(self.stop_in(SESSION))
        self.set_member(m["id"], planned_at=self.ago(11))
        for session in (SESSION, SESSION_B):
            r = self.stop_in(session)
            self.assertEqual(r.json["decision"], "block", (session, r))
            self.assertIn("SPUD-001/Kestrel (01, scout) on SPD-001, planned ", r.json["reason"])

    def test_a_spawn_waiting_to_bind_and_a_living_leads_child_are_not_held(self):
        reserved = self.plan(name="Kestrel")
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(reserved), tool_use_id="toolu_reserved"))
        self.assertEqual(pre.decision, "allow", pre)
        lead = self.spawn(self.plan(persona="engineer", model="opus", name="Yukon"), AGENT_A)
        self.plan(actor=lead["ref"], name="Russet")
        self.assertSilent(self.stop_in(SESSION))  # a spawn on its way, and a child its living lead answers for
        self.home.json("member", "finish", lead["ref"], "--status", "failed", "--outcome", "Died.", actor="spud")
        r = self.stop_in(SESSION)
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 1 planned spudagent(s) were never spawned: SPUD-001/Russet (02.01, scout) on SPD-001, planned "), reason)
        self.assertIn("`spud --as spud member finish SPUD-001/Russet --status failed --outcome '<why>'`", reason)
        self.assertNotIn("--next", reason)  # Russet is nested (under Yukon): its failed command stays as it was
        self.assertNotIn("description `SPUD-001/Russet", reason)  # nobody can spawn it: its parent is finished
        self.assertNotIn("Kestrel", reason)

    # -- running under a finished parent --------------------------------------------
    def test_a_child_running_under_a_finished_parent_is_held_once_and_then_as_returned(self):
        lead, child = self.orphan(AGENT_A, AGENT_B)
        r = self.stop_in(SESSION)
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 1 spudagent(s) are still running under a finished parent: %s (01.01, scout) on SPD-001, running since %s, under %s (done). "
                                          % (child["ref"], child["spawned_at"][:16], lead["ref"])), reason)
        self.assertIn("`spud --as spud member finish %s --status done|blocked|failed --outcome '<verdict>'`" % child["ref"], reason)
        self.assertNotIn("--next", reason)  # the running clause never offers it (SPD-027)
        self.assertEqual(self.denied()[-1]["data"]["running"], [child["ref"]])
        self.assertSilent(self.stop_in(SESSION))  # told once in this session
        self.assertSilent(self.stop_in(SESSION_B))  # and never another session's to record
        self.home.json("member", "result", "Child done.", actor=AGENT_B)
        self.assertEqual(self.home.hook("SubagentStop", self.sub_stop(AGENT_B)).stdout, "")
        stopped = self.home.json("member", "show", child["ref"])["member"]["stopped_at"]
        r = self.stop_in(SESSION)
        # child is nested (its parent_id is the lead's, not NULL): returned without --next, unlike a root member
        self.assertEqual(r.json["reason"], self.RETURNED_ONLY_NESTED % (1, "%s (01.01, scout) on SPD-001, stopped %s" % (child["ref"], stopped[:16])))

    def test_a_running_child_with_no_known_session_is_held_once_in_each_session(self):
        self.in_session(None)
        lead = self.plan(persona="engineer", model="opus")
        self.home.json("member", "start", lead["ref"], actor="spud")
        child = self.plan(actor=lead["ref"])
        self.home.json("member", "start", child["ref"], actor="spud")
        self.home.json("member", "finish", lead["ref"], "--status", "blocked", "--outcome", "Needs Eric.", actor="spud")
        for session in (SESSION, SESSION_B):
            r = self.stop_in(session)
            self.assertEqual(r.json["decision"], "block", (session, r))
            self.assertIn("%s (01.01, scout) on SPD-001, running since " % child["ref"], r.json["reason"])
            self.assertSilent(self.stop_in(session))

    def test_spuds_own_running_child_never_holds(self):
        self.spawn(self.plan(), AGENT_A)  # a background child at work: ending the turn meanwhile is the design
        for session in (SESSION, SESSION_B):
            self.assertSilent(self.stop_in(session))
        self.assertEqual(self.denied(), [])

    # -- one block --------------------------------------------------------------------
    def three_kinds(self):
        """One member of each kind, all in SESSION: (returned, planned, running)."""
        _, running = self.orphan(AGENT_A, AGENT_B)
        returned = self.returned(AGENT_C)
        planned = self.plan()
        return returned, planned, running

    def test_stop_hook_active_lets_every_kind_through(self):
        self.three_kinds()
        self.assertSilent(self.stop_in(SESSION, stop_hook_active=True))
        self.assertEqual(self.denied(), [])
        r = self.stop_in(SESSION)  # the let-go stop told nobody anything
        self.assertEqual(r.json["decision"], "block", r)
        self.assertEqual(len(self.denied()[-1]["data"]["running"]), 1)

    def test_one_block_names_the_three_kinds_in_order(self):
        returned, planned, running = self.three_kinds()
        reason = self.stop_in(SESSION).json["reason"]
        heads = ["Law 9: 1 returned spudagent(s) are not recorded: %s (" % returned["ref"],
                 "1 planned spudagent(s) were never spawned: %s (" % planned["ref"],
                 "1 spudagent(s) are still running under a finished parent: %s (" % running["ref"]]
        at = [reason.find(h) for h in heads]
        self.assertEqual(at[0], 0, reason)
        self.assertTrue(0 < at[1] < at[2], (at, reason))
        self.assertTrue(reason.endswith(". Then end the turn."), reason)
        self.assertEqual([d["data"] for d in self.denied()], [{
            "hook_event_name": "Stop", "session_id": SESSION, "members": [returned["ref"], planned["ref"], running["ref"]],
            "returned": [returned["ref"]], "planned": [planned["ref"]], "unbound": [], "running": [running["ref"]]}])

    # -- allowed to spawn and never bound (SPD-025) -------------------------------------
    # PreToolUse(Agent) allowed the spawn and reserved the row, and nothing bound it: the harness failed the spawn after the
    # allow, or the binding hooks failed open.  Neither can be produced on demand, so these tests age the spawn request.
    UNBOUND_ONLY = ("Law 9: 1 planned spudagent(s) were allowed to spawn and never bound: %(ref)s (%(lineage)s, %(persona)s) on SPD-001,"
                    " spawn allowed %(at)s (tool_use_id %(tool_use_id)s), never bound. First look for its tool_use_id in"
                    " `spud events --kind hook.error --json`: a gap there means that child may still be running, so wait for its notification."
                    " If the harness failed the spawn, record %(ref)s with `spud --as spud member finish %(ref)s --status failed --outcome '<why>'"
                    " [--next '<what happens next>']` and plan a new member, since the reservation refuses a second spawn of"
                    " `%(ref)s (%(lineage)s, %(persona)s)`. A reserved row holds a slot against the limits until it is bound or recorded."
                    " Then end the turn.")

    def reserve(self, m, tool_use_id, session=SESSION, caller=None):
        """PreToolUse(Agent) allows m's spawn in session, which reserves the row; nothing binds it."""
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(m), model=m["model"], subagent_type=spawn_type(m), agent_id=caller,
                                                          tool_use_id=tool_use_id, session=session))
        self.assertEqual((pre.code, pre.decision), (0, "allow"), pre)

    def age_request(self, tool_use_id, minutes):
        """The spawn request's allow moved minutes into the past; returns its new stamp."""
        con = self.home.connect()
        try:
            with con:
                con.execute("UPDATE spawn_requests SET at = ? WHERE tool_use_id = ?", (self.ago(minutes), tool_use_id))
        finally:
            con.close()
        return self.home.scalar("SELECT at FROM spawn_requests WHERE tool_use_id = ?", tool_use_id)

    def blocked(self, r):
        """The reason of a Stop that blocked."""
        self.assertEqual((r.code, (r.json or {}).get("decision")), (0, "block"), r)
        return r.json["reason"]

    def test_a_spawn_allowed_within_the_grace_holds_nobody(self):
        m = self.plan(name="Kestrel")
        self.set_member(m["id"], planned_at=self.ago(30))  # the grace runs from the allow, not from the plan
        self.reserve(m, "toolu_reserved")
        self.assertSilent(self.stop_in(SESSION))
        self.age_request("toolu_reserved", 9)
        for session in (SESSION, SESSION_B):
            self.assertSilent(self.stop_in(session))  # a spawn in flight
        self.assertEqual(self.denied(), [])

    def test_a_spawn_allowed_past_the_grace_and_never_bound_holds_its_session_with_its_own_clause(self):
        m = self.plan(name="Kestrel")
        self.reserve(m, "toolu_reserved")
        at = self.age_request("toolu_reserved", 11)
        self.assertSilent(self.stop_in(SESSION_B))  # another session's spawn
        reason = self.blocked(self.stop_in(SESSION))
        self.assertEqual(reason, self.UNBOUND_ONLY % {"ref": m["ref"], "lineage": "01", "persona": "scout", "at": at[:16], "tool_use_id": "toolu_reserved"})
        self.assertEqual(self.denied()[-1]["data"], {"hook_event_name": "Stop", "session_id": SESSION, "members": [m["ref"]],
                                                     "returned": [], "planned": [], "unbound": [m["ref"]], "running": []})
        self.blocked(self.stop_in(SESSION))  # every fresh stop, until it is bound or recorded

    def test_the_session_that_asked_for_the_spawn_owes_it_not_the_one_that_planned_it(self):
        m = self.plan(name="Kestrel")  # planned in SESSION
        self.reserve(m, "toolu_reserved", session=SESSION_B)  # spawned from SESSION_B, after a /clear say
        self.age_request("toolu_reserved", 11)
        self.assertSilent(self.stop_in(SESSION))
        self.assertIn(": SPUD-001/Kestrel (01, scout) on SPD-001, spawn allowed ", self.blocked(self.stop_in(SESSION_B)))

    def test_binding_the_spawn_or_recording_the_row_failed_clears_it(self):
        kestrel, yukon = self.plan(name="Kestrel"), self.plan(name="Yukon")
        for m, tool_use_id in ((kestrel, "toolu_kestrel"), (yukon, "toolu_yukon")):
            self.reserve(m, tool_use_id)
            self.age_request(tool_use_id, 11)
        reason = self.blocked(self.stop_in(SESSION))
        self.assertTrue(reason.startswith("Law 9: 2 planned spudagent(s) were allowed to spawn and never bound: SPUD-001/Kestrel (01, scout) on SPD-001, "), reason)
        self.assertIn(" never bound. First look for each tool_use_id in `spud events --kind hook.error --json`: ", reason)
        self.assertIn(" If the harness failed a spawn, record SPUD-001/Kestrel with ", reason)
        self.assertEqual(self.denied()[-1]["data"]["unbound"], ["SPUD-001/Kestrel", "SPUD-001/Yukon"])
        # Kestrel's binding lands after all: Spud's own child at work, which never holds
        post = self.home.hook("PostToolUse", self.post_agent_launched("toolu_kestrel", AGENT_A, self.description(kestrel)))
        self.assertEqual((post.code, post.stdout), (0, ""), post)
        self.assertEqual(self.home.json("member", "show", kestrel["ref"])["member"]["status"], "active")
        self.blocked(self.stop_in(SESSION))
        self.assertEqual(self.denied()[-1]["data"]["unbound"], ["SPUD-001/Yukon"])
        # the harness failed Yukon's spawn: the printed command, --next and all, records a planned row that holds a reservation
        out = self.home.json("member", "finish", yukon["ref"], "--status", "failed", "--outcome", "The harness failed the spawn.",
                             "--next", "Plan a new member for the work.", actor="spud")
        self.assertEqual((out["member"]["status"], bool(out["member"]["finished_at"])), ("failed", True))
        self.assertSilent(self.stop_in(SESSION))
        self.assertEqual(self.home.hook("PreToolUse", self.pre_agent(self.description(yukon), tool_use_id="toolu_again")).decision, "deny")
        self.spawn(self.plan(name="Russet"), AGENT_B)  # the new member takes the work
        self.assertSilent(self.stop_in(SESSION))

    def test_a_nested_spawn_never_bound_under_a_finished_parent_holds_with_the_nested_way_out(self):
        lead = self.spawn(self.plan(persona="engineer", model="opus", name="Yukon"), AGENT_A)
        child = self.plan(actor=lead["ref"], name="Russet")
        self.reserve(child, "toolu_russet", caller=AGENT_A)
        at = self.age_request("toolu_russet", 11)
        self.assertSilent(self.stop_in(SESSION))  # its living lead answers for it (SubagentStop, alive_children)
        self.home.json("member", "finish", lead["ref"], "--status", "failed", "--outcome", "Died.", actor="spud")
        self.assertSilent(self.stop_in(SESSION_B))
        reason = self.blocked(self.stop_in(SESSION))
        self.assertTrue(reason.startswith("Law 9: 1 planned spudagent(s) were allowed to spawn and never bound: SPUD-001/Russet (01.01, scout) on SPD-001,"
                                          " spawn allowed %s (tool_use_id toolu_russet), never bound, under SPUD-001/Yukon (failed). " % at[:16]), reason)
        self.assertIn(" If the harness failed the spawn, record SPUD-001/Russet with `spud --as spud member finish SPUD-001/Russet --status failed"
                      " --outcome '<why>'`: nobody can spawn it again now that SPUD-001/Yukon is failed. ", reason)
        self.assertNotIn("--next", reason)
        self.assertNotIn("plan a new member", reason)
        self.assertEqual(self.denied()[-1]["data"]["unbound"], ["SPUD-001/Russet"])
        refused = self.home.run("member", "finish", child["ref"], "--status", "failed", "--outcome", "x", "--next", "y", actor="spud", check=False)
        self.assertEqual(refused.returncode, EXIT_USAGE, refused.stderr)  # a nested row's finish refuses --next
        out = self.home.json("member", "finish", child["ref"], "--status", "failed", "--outcome", "Its spawn never bound; its lead died.", actor="spud")
        self.assertEqual(out["member"]["status"], "failed")
        self.assertSilent(self.stop_in(SESSION))

    def test_stop_hook_active_lets_an_unbound_spawn_through(self):
        m = self.plan(name="Kestrel")
        self.reserve(m, "toolu_reserved")
        self.age_request("toolu_reserved", 11)
        self.assertSilent(self.stop_in(SESSION, stop_hook_active=True))
        self.assertEqual(self.denied(), [])
        self.assertIn(m["ref"], self.blocked(self.stop_in(SESSION)))

    def test_one_block_names_the_four_kinds_in_order(self):
        _, running = self.orphan(AGENT_A, AGENT_B)
        returned = self.returned(AGENT_C)
        planned = self.plan()
        denied = self.home.hook("PreToolUse", self.pre_agent(self.description(planned), model="opus", tool_use_id="toolu_denied"))
        self.assertEqual(denied.decision, "deny", denied)  # a denied spawn reserves nothing: the row is still never spawned
        unbound = self.plan()
        self.reserve(unbound, "toolu_unbound")
        self.age_request("toolu_unbound", 11)
        reason = self.blocked(self.stop_in(SESSION))
        heads = ["Law 9: 1 returned spudagent(s) are not recorded: %s (" % returned["ref"],
                 "1 planned spudagent(s) were never spawned: %s (" % planned["ref"],
                 "1 planned spudagent(s) were allowed to spawn and never bound: %s (" % unbound["ref"],
                 "1 spudagent(s) are still running under a finished parent: %s (" % running["ref"]]
        at = [reason.find(h) for h in heads]
        self.assertEqual(at[0], 0, reason)
        self.assertTrue(0 < at[1] < at[2] < at[3], (at, reason))
        self.assertTrue(reason.endswith(". Then end the turn."), reason)
        self.assertEqual([d["data"] for d in self.denied()], [{
            "hook_event_name": "Stop", "session_id": SESSION, "members": [returned["ref"], planned["ref"], unbound["ref"], running["ref"]],
            "returned": [returned["ref"]], "planned": [planned["ref"]], "unbound": [unbound["ref"]], "running": [running["ref"]]}])

    def test_the_returned_only_reason_is_unchanged_beside_a_spawn_in_flight_and_another_sessions_unbound_one(self):
        kestrel = self.returned(AGENT_A, SESSION, name="Kestrel")
        self.reserve(self.plan(name="Yukon"), "toolu_in_flight")  # SESSION's own, allowed a moment ago
        self.in_session(SESSION_B)
        self.reserve(self.plan(name="Russet"), "toolu_other", session=SESSION_B)
        self.age_request("toolu_other", 11)
        reason = self.blocked(self.stop_in(SESSION))
        self.assertEqual(reason, self.RETURNED_ONLY % (1, "SPUD-001/Kestrel (01, scout) on SPD-001, stopped %s" % kestrel["stopped_at"][:16]))
        self.assertEqual(self.denied()[-1]["data"], {"hook_event_name": "Stop", "session_id": SESSION, "members": ["SPUD-001/Kestrel"],
                                                     "returned": ["SPUD-001/Kestrel"], "planned": [], "unbound": [], "running": []})
        # once SESSION's own spawn has waited past the grace it joins the block, and the returned clause loses its own ending
        at = self.age_request("toolu_in_flight", 11)
        reason = self.blocked(self.stop_in(SESSION))
        self.assertTrue(reason.startswith("Law 9: 1 returned spudagent(s) are not recorded: SPUD-001/Kestrel (01, scout) on SPD-001, stopped "), reason)
        self.assertIn(". 1 planned spudagent(s) were allowed to spawn and never bound: SPUD-001/Yukon (02, scout) on SPD-001, spawn allowed %s"
                      " (tool_use_id toolu_in_flight), never bound. " % at[:16], reason)
        self.assertTrue(reason.endswith(". Then end the turn."), reason)
        self.assertEqual(self.denied()[-1]["data"]["unbound"], ["SPUD-001/Yukon"])


# =============================================================================
# Failure policy
# =============================================================================


class FailurePolicyTest(HookCase):
    def break_database(self):
        con = self.home.connect()
        con.execute("PRAGMA user_version = 99")
        con.close()

    def test_enforcing_hooks_exit_2_on_an_unexpected_error(self):
        m = self.plan()
        self.break_database()
        for p in (self.pre_agent(self.description(m)), self.pre_bash("ls", agent_id=AGENT_A), self.pre_edit(self.home.path / "x", agent_id=AGENT_A)):
            r = self.home.hook("PreToolUse", p)
            self.assertEqual(r.code, 2, r)
            self.assertEqual(r.stdout, "")
            self.assertIn("ahead of this CLI", r.stderr)
        r = self.home.hook("Stop", self.stop())
        self.assertEqual(r.code, 2, r)
        r = self.home.hook("Stop", self.stop(stop_hook_active=True))
        self.assertEqual(r.code, 0, r)

    def test_recording_hooks_exit_0_and_spool_the_gap(self):
        m1 = self.spawn(self.plan(), AGENT_A)
        m2 = self.plan()
        self.home.hook("PreToolUse", self.pre_agent(self.description(m2), tool_use_id="toolu_dup"))
        # binding a second member to an agent_id that is already bound violates UNIQUE
        r = self.home.hook("PostToolUse", self.post_agent_launched("toolu_dup", AGENT_A, self.description(m2)))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertTrue(self.home.spool.exists())
        lines = [json.loads(l) for l in self.home.spool.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["event"], "PostToolUse")
        self.assertIn("UNIQUE", lines[0]["error"])
        self.assertEqual(self.events("hook.error"), [])
        # the next successful hook drains it
        r = self.home.hook("SubagentStart", self.sub_start(AGENT_C))
        self.assertEqual(r.code, 0)
        errs = self.events("hook.error")
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["actor"], "hook:PostToolUse")
        self.assertIn("UNIQUE", errs[0]["body"])
        self.assertEqual(errs[0]["data"]["tool_use_id"], "toolu_dup")
        self.assertFalse(self.home.spool.exists() and self.home.spool.stat().st_size > 0)
        self.assertEqual(self.home.json("member", "show", m1["ref"])["member"]["agent_id"], AGENT_A)

    def test_recording_hooks_exit_0_on_a_broken_database_and_malformed_input(self):
        for raw in ("", "not json", "[1]"):
            for event in ("PostToolUse", "SubagentStart", "SubagentStop", "SessionStart"):
                r = self.home.hook(event, raw)
                self.assertEqual((r.code, r.stdout), (0, ""), (event, raw, r))
        self.assertTrue(self.home.spool.exists())
        self.break_database()
        for event, p in (("PostToolUse", self.post_agent_launched("toolu_x", AGENT_A, "x")), ("SubagentStart", self.sub_start(AGENT_A)), ("SubagentStop", self.sub_stop(AGENT_A)), ("SessionStart", self.session_start())):
            r = self.home.hook(event, p)
            self.assertEqual((r.code, r.stdout), (0, ""), (event, r))

    def test_the_cli_drains_the_spool_too(self):
        self.home.hook("SubagentStart", "not json")
        self.assertTrue(self.home.spool.exists())
        self.home.json("board")
        errs = self.events("hook.error")
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["actor"], "hook:SubagentStart")
        self.assertEqual(self.home.spool.stat().st_size if self.home.spool.exists() else 0, 0)


# =============================================================================
# spud sql --readonly
# =============================================================================


class SqlTest(SpudTestCase):
    def test_readonly_queries_print_a_table_or_json(self):
        t = self.new_ticket("Query")
        proc = self.home.run("sql", "--readonly", "SELECT key, status FROM tickets")
        self.assertIn(t["key"], proc.stdout)
        self.assertIn("queued", proc.stdout)
        out = self.home.json("sql", "--readonly", "SELECT count(*) AS n FROM tickets")
        self.assertEqual(out["rows"], [{"n": 1}])
        self.assertEqual(out["columns"], ["n"])
        out = self.home.json("sql", "--readonly", "SELECT 1 WHERE 0")
        self.assertEqual(out["rows"], [])

    def test_writes_are_impossible(self):
        for stmt in ("INSERT INTO name_pool (name) VALUES ('X')", "UPDATE tickets SET title = 'x'", "DELETE FROM events", "PRAGMA user_version = 77", "CREATE TABLE x (a)", "DROP TABLE renders", "PRAGMA query_only = OFF; INSERT INTO name_pool (name) VALUES ('X')", "ATTACH '/tmp/x.db' AS x"):
            proc = self.home.run("sql", "--readonly", stmt, check=False)
            self.assertEqual(proc.returncode, EXIT_ERROR, stmt)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM name_pool WHERE name = 'X'"), 0)
        self.assertEqual(self.home.scalar("PRAGMA user_version"), 9)

    def test_one_statement_no_flag_no_actor_needed(self):
        proc = self.home.run("sql", "SELECT 1", check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE)
        proc = self.home.run("sql", "--readonly", "SELECT 1; SELECT 2", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        proc = self.home.run("sql", "--readonly", "SELECT 1", actor="spud")
        self.assertEqual(proc.returncode, 0)
        proc = self.home.run("sql", "--readonly", "SELEC 1", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("syntax", proc.stderr.lower())


class LateBindingTest(HookCase):
    """A foreground spawn is bound at its first tool call from agent-<id>.meta.json beside
    the session's subagent transcripts, so it can write its deliverables before its stop."""

    def write_meta(self, agent_id, tool_use_id, description):
        """What the harness writes beside the subagent transcript (spike fact 6)."""
        meta = self.home.path / "projects" / SESSION / "subagents" / ("agent-%s.meta.json" % agent_id)
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"agentType": "spudagent", "description": description, "toolUseId": tool_use_id, "spawnDepth": 1, "requestShape": "foreground", "requestNonInteractive": True, "model": "haiku"}), encoding="utf-8")
        return str(self.home.path / "projects" / ("%s.jsonl" % SESSION))

    def test_first_tool_call_binds_a_foreground_spawn(self):
        m = self.plan(deliverable=["home:tests/**"])
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_fg", run_in_background=False))
        self.assertEqual(pre.decision, "allow")
        transcript = self.write_meta(AGENT_B, "toolu_fg", self.description(m))
        p = self.pre_edit(self.home.path / "docs" / "x.md", agent_id=AGENT_B)
        p["transcript_path"] = transcript
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.decision), (0, "deny"), r)
        self.assertIn("deliverables", r.reason)
        self.assertNotIn("not bound", r.reason)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual((shown["status"], shown["agent_id"]), ("active", AGENT_B))
        self.assertEqual(self.home.scalar("SELECT agent_id FROM spawn_requests WHERE tool_use_id = 'toolu_fg'"), AGENT_B)
        p = self.pre_edit(self.home.path / "tests" / "x.py", agent_id=AGENT_B)
        p["transcript_path"] = transcript
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        p = self.pre_bash("%s --as %s member log hi" % ("python3.14 -I -S %s/bin/spud" % self.home.path, AGENT_B), agent_id=AGENT_B)
        r = self.home.hook("PreToolUse", p)
        self.assertEqual(r.decision, "allow", r)
        status = [e for e in self.events("member.status") if e["member"] == m["ref"]]
        self.assertEqual(status[0]["actor"], "hook:PreToolUse")

    def test_a_forged_meta_cannot_bind_someone_elses_member(self):
        """Rooster's MEDIUM-4: the meta.json must describe the request it names, and the
        harness's subagent files are not writable by spudagents."""
        victim = self.plan(name="Cara", deliverable=["home:tests/**"])
        self.home.hook("PreToolUse", self.pre_agent(self.description(victim), tool_use_id="toolu_victim", run_in_background=False))
        # a meta naming the victim's request but describing something else: no binding
        meta = self.home.path / "projects" / SESSION / "subagents" / ("agent-%s.meta.json" % AGENT_D)
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"agentType": "spudagent", "description": "SPUD-001/Nobody (01, scout)", "toolUseId": "toolu_victim"}), encoding="utf-8")
        transcript = str(self.home.path / "projects" / ("%s.jsonl" % SESSION))
        p = self.pre_edit(self.home.path / "tests" / "x.py", agent_id=AGENT_D)
        p["transcript_path"] = transcript
        r = self.home.hook("PreToolUse", p)
        self.assertEqual(r.decision, "deny")
        self.assertIn("not bound", r.reason)
        self.assertIsNone(self.home.json("member", "show", victim["ref"])["member"]["agent_id"])
        # a meta with a mismatching agentType: no binding either
        meta.write_text(json.dumps({"agentType": "Explore", "description": self.description(victim), "toolUseId": "toolu_victim"}), encoding="utf-8")
        r = self.home.hook("PreToolUse", p)
        self.assertEqual(r.decision, "deny")
        self.assertIsNone(self.home.json("member", "show", victim["ref"])["member"]["agent_id"])
        # the harness's subagent files cannot be written by an agent, bound or not, through the edit tools or a redirection
        bound = self.spawn(self.plan(name="Kestrel"), AGENT_A)
        for who in (AGENT_A, AGENT_D):
            for path in (meta, meta.with_name("agent-%s.jsonl" % who), self.home.path / "projects" / SESSION / "subagents" / "x.txt"):
                r = self.home.hook("PreToolUse", self.pre_edit(path, agent_id=who))
                self.assertEqual((r.code, r.decision), (0, "deny"), (who, str(path), r))
                self.assertIn("harness", r.reason)
            r = self.home.hook("PreToolUse", self.pre_bash("echo x > %s" % meta, agent_id=who))
            self.assertEqual(r.decision, "deny", r)
        r = self.home.hook("PreToolUse", self.pre_edit(meta, agent_id=None))
        self.assertEqual(r.decision, "deny", r)
        # the memory directory beside the sessions (outside the repository) stays writable
        memory = self.home.path.parent / ("%s-projects" % self.home.path.name) / "memory" / "x.md"
        r = self.home.hook("PreToolUse", self.pre_edit(memory, agent_id=AGENT_A))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertEqual(bound["status"], "active")

    def test_no_meta_file_stays_unbound(self):
        m = self.plan()
        self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_fg", run_in_background=False))
        p = self.pre_edit(self.home.path / "tests" / "x.py", agent_id=AGENT_B)
        p["transcript_path"] = str(self.home.path / "projects" / ("%s.jsonl" % SESSION))
        r = self.home.hook("PreToolUse", p)
        self.assertEqual(r.decision, "deny")
        self.assertIn("not bound", r.reason)
        self.assertIsNone(self.home.json("member", "show", m["ref"])["member"]["agent_id"])

    def test_help_and_version_are_well_formed(self):
        m = self.plan()
        self.spawn(m, AGENT_A)
        cli = "python3.14 -I -S %s/bin/spud" % self.home.path
        for tail in ("--help", "-h", "member --help", "member finish --help", "--version"):
            r = self.home.hook("PreToolUse", self.pre_bash("%s %s" % (cli, tail), agent_id=AGENT_A))
            self.assertEqual((r.code, r.decision), (0, "allow"), (tail, r))


class ArgvAnywhereTest(SpudTestCase):
    def test_as_and_json_are_accepted_after_the_subcommand(self):
        t = self.new_ticket("Argv")
        m = self.new_member(t["key"])
        self.home.json("member", "start", m["ref"], actor="spud")
        proc = self.home.run("member", "log", "--as", m["ref"], "hello")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.home.run("member", "result", "produced x", "--as", m["ref"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.home.run("member", "finish", m["ref"], "--status", "done", "--outcome", "fine", "--as", "spud")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.home.run("board", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["ok"])
        proc = self.home.run("member", "show", m["ref"], "--as=spud", "--json")
        self.assertEqual(json.loads(proc.stdout)["member"]["status"], "done")
        # a text value that merely contains the flags is left alone
        out = self.home.json("member", "log", "--as", m["ref"], "ran with --json and --as before")
        self.assertTrue(out["ok"])
        logged = self.home.json("events", "--member", m["ref"], "--kind", "member.log")["events"]
        self.assertEqual(logged[-1]["body"], "ran with --json and --as before")


class ActorEpilogTest(SpudTestCase):
    def test_help_no_longer_defers_to_spd_008(self):
        text = self.home.run("--help").stdout
        self.assertNotIn("Until SPD-008", text)
        self.assertNotIn("unresolvable until then", text)
        self.assertIn("hook", text)
        proc = self.home.run("member", "log", "x", actor=AGENT_D, check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertNotIn("SPD-008", proc.stderr)
        self.assertIn("not bound", proc.stderr)


if __name__ == "__main__":
    unittest.main()
