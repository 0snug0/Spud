"""A spudagent resumed with SendMessage after it returned (SPD-050).

The harness gives the hooks one signal and only one: SubagentStart fires again for the same agent_id.  Observed
live on 2026-09-15 with the scout SPUD-050/Sarpo (agent_id aca970f6a277bd623), events 3683 to 3691 of the real
ledger: member.started 16:32:44, member.result, member.stopped 16:32:51, then SendMessage, then member.started
again 16:33:18 carrying the same three data fields (agent_type, session_id, cwd) and nothing marking it as a
resume, a second member.result, and member.stopped 16:33:25.  No PreToolUse or PostToolUse names SendMessage, so
the resume is read from the row, not from the payload.

Before SPD-050 nothing cleared the return: for the whole second round the row still read stopped 16:32:51, so
Spud's Stop held with Law 9 and told him to `member finish` a member that was running again, the board read
`returned 16:32, unrecorded`, and the Result of the first round answered for the second return's hold.  The
second stop also recorded `transcript_usage: false`, so the run figures stayed frozen at the first round while
the transcript had grown.

Every run is against a scratch SPUD_HOME (tests/helpers.py); the payload builders and the planned team come from
tests/test_hooks.py.
"""

import unittest

from test_hooks import AGENT_A, AGENT_B, AGENT_D, SESSION, HookCase


class ResumeCase(HookCase):
    """A member taken to its first return, and the second SubagentStart that resumes it."""

    def returned(self, agent_id=AGENT_A, result="Round 1 done.", **kw):
        """A root member spawned and bound, its Result recorded unless result is None, and its return stamped."""
        m = self.spawn(self.plan(**kw), agent_id)
        if result is not None:
            self.home.json("member", "result", result, actor=agent_id)
        r = self.home.hook("SubagentStop", self.sub_stop(agent_id, last="Round 1."))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        return self.row(m)

    def resume(self, agent_id=AGENT_A, agent_type="spudagent", session=SESSION):
        r = self.home.hook("SubagentStart", self.sub_start(agent_id, agent_type, session=session))
        self.assertEqual(r.code, 0, r)
        return r

    def row(self, m):
        return self.home.json("member", "show", m["ref"])["member"]

    def usage_of(self, m):
        return self.home.rows("SELECT total_tokens, duration_ms, tool_uses FROM members WHERE id = ?", m["id"])[0]

    def brief_board(self):
        return self.home.run("board", "--brief").stdout


class ResumeClearsTheReturnTest(ResumeCase):
    def test_a_second_start_clears_the_return_and_records_the_previous_stop(self):
        m = self.returned()
        self.assertIsNotNone(m["stopped_at"])
        self.resume()
        after = self.row(m)
        self.assertIsNone(after["stopped_at"])
        self.assertEqual(after["status"], "active")
        self.assertEqual(after["spawned_at"], m["spawned_at"])  # the run still starts at the first spawn
        e = self.events("member.started")[-1]
        self.assertEqual((e["actor"], e["member"], e["agent_id"]), ("hook:SubagentStart", m["ref"], AGENT_A))
        self.assertEqual(e["data"]["resumed"], True)
        self.assertEqual(e["data"]["was_stopped_at"], m["stopped_at"])
        self.assertEqual(e["data"]["cleared"], True)
        self.assertEqual(e["data"]["agent_type"], "spudagent")
        self.assertEqual(e["data"]["session_id"], SESSION)
        self.assertIn("resumed", e["body"])
        self.assertIn(m["stopped_at"], e["body"])

    def test_a_second_start_for_a_member_that_never_stopped_is_the_plain_start(self):
        m = self.spawn(self.plan(), AGENT_A)
        r = self.resume()
        self.assertIn(m["ref"], r.context)
        e = self.events("member.started")[-1]
        self.assertEqual(e["body"], "subagent %s started (spudagent)" % AGENT_A)
        self.assertNotIn("resumed", e["data"])
        self.assertIsNone(self.row(m)["stopped_at"])

    def test_an_unbound_agent_id_is_the_plain_start(self):
        r = self.home.hook("SubagentStart", self.sub_start(AGENT_D))
        self.assertEqual(r.code, 0, r)
        e = self.events("member.started")[-1]
        self.assertIsNone(e["member"])
        self.assertNotIn("resumed", e["data"])

    def test_a_finished_members_resume_changes_nothing_but_the_event(self):
        m = self.returned()
        self.home.json("member", "finish", m["ref"], "--status", "done", "--outcome", "Good work.", actor="spud")
        before = self.row(m)
        self.assertEqual(before["status"], "done")
        self.resume()
        after = self.row(m)
        self.assertEqual((after["status"], after["stopped_at"], after["finished_at"]),
                         (before["status"], before["stopped_at"], before["finished_at"]))
        e = self.events("member.started")[-1]
        self.assertEqual((e["data"]["resumed"], e["data"]["cleared"]), (True, False))
        self.assertEqual(e["data"]["was_stopped_at"], before["stopped_at"])


class ResumeStopTest(ResumeCase):
    """Spud's Stop (Law 9) and the board: a resumed member is live again, not returned."""

    def test_spuds_stop_holds_for_the_return_and_not_after_the_resume(self):
        m = self.returned()
        r = self.home.hook("Stop", self.stop())
        self.assertEqual(r.json["decision"], "block", r)
        self.assertIn("returned spudagent(s) are not recorded", r.json["reason"])
        self.assertIn(m["ref"], r.json["reason"])
        self.resume()
        r = self.home.hook("Stop", self.stop())
        self.assertEqual((r.code, r.stdout), (0, ""), r)

    def test_the_board_and_member_show_drop_the_returned_label(self):
        m = self.returned()
        self.assertIn("returned %s, unrecorded" % m["stopped_at"][11:16], self.brief_board())
        self.resume()
        board = self.brief_board()
        self.assertNotIn("unrecorded", board)
        self.assertIn("%s (01, scout, haiku) active" % m["name"], board)
        self.assertIsNone(self.row(m)["stopped_at"])


class ResumeSubagentStopTest(ResumeCase):
    """The second return answers to the second round: the Result of the first no longer satisfies the hold."""

    def test_a_second_return_with_no_result_since_the_resume_is_held_once(self):
        m = self.returned()
        self.resume()
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, last="Round 2."))
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.json["decision"], "block", r)
        self.assertIn("spud --as %s member result" % AGENT_A, r.json["reason"])
        self.assertIsNone(self.row(m)["stopped_at"])  # held, so nothing is stamped
        held = self.events("member.stopped")[-1]
        self.assertEqual((held["data"]["held"], held["data"]["unrecorded"]), (True, True))
        self.home.json("member", "result", "Round 2 done.", actor=AGENT_A)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, last="Round 2."))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        after = self.row(m)
        self.assertIsNotNone(after["stopped_at"])
        self.assertGreaterEqual(after["stopped_at"], m["stopped_at"])
        final = self.events("member.stopped")[-1]
        self.assertEqual((final["data"]["held"], final["data"]["unrecorded"]), (False, False))

    def test_a_block_recorded_since_the_resume_answers_for_the_second_return(self):
        self.returned()
        self.resume()
        self.home.json("member", "block", "Need Eric.", actor=AGENT_A)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual((r.code, r.stdout), (0, ""), r)

    def test_stop_hook_active_still_lets_a_second_unrecorded_return_go(self):
        m = self.returned()
        self.resume()
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, stop_hook_active=True))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertIsNotNone(self.row(m)["stopped_at"])

    def test_the_run_figures_span_to_the_second_stop(self):
        m = self.spawn(self.plan(), AGENT_A)
        self.home.json("member", "result", "Round 1.", actor=AGENT_A)
        first = self.write_transcript(AGENT_A, self.two_requests())
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, transcript=str(first)))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertEqual(self.usage_of(m), {"total_tokens": 442, "duration_ms": 4500, "tool_uses": 3})
        stopped_first = self.row(m)["stopped_at"]
        self.resume()
        self.home.json("member", "result", "Round 2.", actor=AGENT_A)
        longer = self.write_transcript(AGENT_A, self.two_requests() + [
            self.assistant("c", {"input_tokens": 1000, "output_tokens": 1000}, "2026-09-12T13:32:00.000Z", tool_uses=5)])
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, transcript=str(longer)))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        after = self.row(m)
        self.assertIsNotNone(after["stopped_at"])
        self.assertGreaterEqual(after["stopped_at"], stopped_first)
        self.assertEqual(self.usage_of(m), {"total_tokens": 2442, "duration_ms": 120000, "tool_uses": 8})
        final = self.events("member.stopped")[-1]
        self.assertEqual(final["data"]["transcript_usage"], True)


class ResumedChildTest(ResumeCase):
    """Law 9 one level down: a lead that resumes its own child is told the child is alive, not that it returned."""

    def lead_and_child(self):
        lead = self.spawn(self.plan(persona="engineer", model="opus"), AGENT_A)
        self.home.json("member", "result", "Lead built it.", actor=AGENT_A)
        child = self.spawn(self.plan(actor=lead["ref"]), AGENT_B, caller=AGENT_A)
        self.home.json("member", "result", "Child done.", actor=AGENT_B)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_B))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        return lead, self.row(child)

    def test_a_lead_is_told_its_resumed_child_is_alive_not_that_it_returned(self):
        lead, child = self.lead_and_child()
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["decision"], "block", r)
        self.assertIn("returned and is not recorded", r.json["reason"])
        self.resume(AGENT_B)
        self.assertIsNone(self.row(child)["stopped_at"])
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertNotIn("returned and is not recorded", reason)
        self.assertIn("1 child you planned is still alive", reason)
        self.assertIn("%s (01.01, scout), running since" % child["ref"], reason)
        self.assertIn("Wait inside this turn", reason)

    def test_spuds_stop_does_not_call_a_resumed_child_returned(self):
        lead, child = self.lead_and_child()
        self.home.json("member", "finish", lead["ref"], "--status", "done", "--outcome", "Done.", actor="spud")
        r = self.home.hook("Stop", self.stop())
        self.assertIn("returned spudagent(s) are not recorded", r.json["reason"])
        self.assertIn(child["ref"], r.json["reason"])
        self.resume(AGENT_B)
        r = self.home.hook("Stop", self.stop())
        self.assertEqual(r.json["decision"], "block", r)  # still owed: a child running under a finished parent
        self.assertNotIn("returned spudagent(s) are not recorded", r.json["reason"])
        self.assertIn("still running under a finished parent", r.json["reason"])


if __name__ == "__main__":
    unittest.main()
