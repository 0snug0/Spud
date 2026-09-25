"""spud hook PreToolUse(SendMessage): a spudagent is never resumed with SendMessage (SPD-318).

The evidence (Eric, 2026-09-24, ching CHI-014 and CHI-002): a member returned blocked, Spud ran `member start` and
resumed it with SendMessage to its agentId.  The resumed run lasted more than thirty minutes while the desktop app's
tasks pane showed only the SendMessage result ('Resuming agent ...'): no live progress and no task row, and the resume
never passed PreToolUse(Agent)'s member check (tests/test_hooks_resume.py records the one signal it does give, a second
SubagentStart).  The hook now refuses a SendMessage whose `to` names a member the ledger knows -- by agent id, by
name with or without a listing's ` [ref]`, by `SPUD-nnn/<Name>` or the spawn description -- and names the way back:
`member respawn`, which plans a new row carrying the old one's record and prints the Agent call.  Every other target
passes silently.  The name forms are matched only where a spudagent can be the caller's -- a session that is not plain,
or a caller with an agent_id; a plain session is refused only a member's raw agent id (Ralph's review of SPD-318).

Every run is against a scratch SPUD_HOME (tests/helpers.py); the payload builders and the planned team come from
tests/hookcase.py.
"""

import unittest

from helpers import load_spud_module
from hookcase import AGENT_A, AGENT_B, AGENT_C, HookCase, common

spud = load_spud_module()

PLAIN_SESSION = "7c1d2e3f-0000-4000-8000-00000000c1a1"  # a session that never claims: plain in the tool, a `claim` project


class SendMessageHookTest(HookCase):
    in_process = True  # SPD-233: the hooks and the CLI in this process, one home per class (hookcase.ClassHome)

    def pre_send(self, to, agent_id=None, message="Carry on: the answer is B."):
        p = common(self.cwd, agent_id)
        p.update({"hook_event_name": "PreToolUse", "tool_name": "SendMessage", "tool_use_id": "toolu_01SEND",
                  "tool_input": {"to": to, "message": message, "summary": "resume"}})
        return p

    def returned_blocked(self, agent_id=AGENT_A, actor="spud", caller=None, **kw):
        """A member spawned and bound, returned blocked, and recorded blocked by its parent."""
        m = self.spawn(self.plan(actor=actor, **kw), agent_id, caller=caller)
        self.home.json("member", "block", "A or B?", actor=agent_id)
        self.home.json("member", "finish", m["ref"], "--status", "blocked", "--outcome", "asked Eric", actor=actor)
        return self.home.json("member", "show", m["ref"])["member"]

    def assertRefused(self, r, m, what=None):
        self.assertEqual((r.code, r.decision), (0, "deny"), (what, r))
        self.assertIn("SendMessage", r.reason)
        self.assertIn(m["ref"], r.reason)
        self.assertIn("member respawn %s" % m["ref"], r.reason)
        self.assertIn("run_in_background: true", r.reason)
        self.assertIn("SPD-318", r.reason)
        self.assertIn("run from the ticket's bound worktree when its deliverables are bare globs", r.reason)  # exit 5 elsewhere
        self.assertIn("exit 5", r.reason)
        return r.reason

    def pre_send_plain(self, to, agent_id=None):
        """A SendMessage from a plain session: launched in the tool, a project whose sessions are `claim`, unclaimed."""
        p = self.pre_send(to, agent_id)
        p.update(common(str(self.home.tool), agent_id, session=PLAIN_SESSION))
        return p

    def test_a_plain_session_sends_to_names_freely_and_is_refused_only_a_raw_agent_id(self):
        """Ralph's review of SPD-318: a plain session's teammate or local session may bear a pool name a past member bore
        (Sam, Tom), so the name forms are matched only where a spudagent can be the caller's.  The raw agentId -- how the
        harness addresses an Agent-tool subagent -- is refused in every session."""
        m = self.returned_blocked()
        for to in (m["name"], m["name"].lower(), "%s [3fa9c1]" % m["name"], m["ref"],
                   "%s (%s, %s)" % (m["ref"], m["lineage"], m["persona"])):
            with self.subTest(to=to):
                r = self.decide(self.pre_send_plain(to))
                self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertEqual(self.denied(), [])
        for to in (AGENT_A, " %s " % AGENT_A, "%s [a1b2c3]" % AGENT_A):
            with self.subTest(to=to):
                self.assertRefused(self.decide(self.pre_send_plain(to)), m, to)

    def test_a_spudagent_caller_in_a_plain_session_is_refused_a_childs_name(self):
        """A caller with an agent_id can own a spudagent, whatever the session's mode."""
        lead = self.spawn(self.plan(persona="engineer", model="opus"), AGENT_C)
        child = self.returned_blocked(agent_id=AGENT_A, actor=lead["ref"], caller=AGENT_C)
        for to in (child["name"], child["ref"], AGENT_A):
            with self.subTest(to=to):
                self.assertRefused(self.decide(self.pre_send_plain(to, agent_id=AGENT_C)), child, to)

    def test_a_send_to_a_spudagents_agent_id_is_refused_with_the_way_back(self):
        m = self.returned_blocked()
        r = self.decide(self.pre_send(AGENT_A))
        reason = self.assertRefused(r, m)
        self.assertIn("Law 2", reason)
        self.assertIn("spud --as spud member respawn %s" % m["ref"], reason)
        denied = self.denied()
        self.assertEqual(len(denied), 1, denied)
        self.assertEqual(denied[0]["data"]["tool_name"], "SendMessage")
        self.assertEqual(denied[0]["data"]["to"], AGENT_A)
        self.assertEqual(denied[0]["data"]["member"], m["ref"])
        self.assertEqual(denied[0]["ticket"], self.t["key"])
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual(shown["status"], "blocked")  # the refusal moves nothing

    def test_a_send_by_name_is_refused_in_every_spelling_the_ledger_knows(self):
        m = self.returned_blocked()
        for to in (m["name"], m["name"].lower(), m["name"].upper(), "%s [3fa9c1]" % m["name"], " %s " % m["name"],
                   m["ref"], "%s (%s, %s)" % (m["ref"], m["lineage"], m["persona"]),
                   "%s (%s, %s, %s)" % (m["ref"], m["lineage"], m["persona"], m["model"]), "%s [a1b2c3]" % AGENT_A):
            with self.subTest(to=to):
                self.assertRefused(self.decide(self.pre_send(to)), m, to)

    def test_a_live_or_never_spawned_member_is_refused_too(self):
        """Not only a returned one: a running member is messaged by nobody, and a planned one is spawned with Agent."""
        running = self.spawn(self.plan(), AGENT_B)
        planned = self.plan()
        for m, to in ((running, AGENT_B), (running, running["name"]), (planned, planned["name"])):
            with self.subTest(member=m["ref"], to=to):
                self.assertRefused(self.decide(self.pre_send(to)), m, to)

    def test_a_parent_spudagent_is_refused_its_child_and_told_its_own_actor(self):
        lead = self.spawn(self.plan(persona="engineer", model="opus"), AGENT_C)
        child = self.returned_blocked(agent_id=AGENT_A, actor=lead["ref"], caller=AGENT_C)
        r = self.decide(self.pre_send(AGENT_A, agent_id=AGENT_C))
        reason = self.assertRefused(r, child)
        self.assertIn("spud --as %s member respawn %s" % (AGENT_C, child["ref"]), reason)
        self.assertEqual(self.denied()[-1]["member"], lead["ref"])  # the caller's row

    def test_every_other_target_passes_silently(self):
        m = self.returned_blocked()
        for to in ("main", "researcher", "worker [3fa9c1]", "a0000000000000000", "SPUD-999/%s" % m["name"],
                   "%s-helper" % m["name"], "%s/Nobody" % self.team):
            with self.subTest(to=to):
                r = self.decide(self.pre_send(to))
                self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertEqual(self.denied(), [])

    def test_a_send_with_no_target_is_malformed(self):
        p = self.pre_send("x")
        del p["tool_input"]["to"]
        r = self.decide(p)
        self.assertEqual(r.decision, "deny", r)
        self.assertIn("tool_input.to", r.reason)

    def test_the_hook_enforces_sendmessage_and_settings_install_it(self):
        self.assertIn("SendMessage", spud.ENFORCED_TOOLS)
        self.assertIn("SendMessage", dict(spud.HOOK_TABLE)["PreToolUse"].split("|"))


if __name__ == "__main__":
    unittest.main()
