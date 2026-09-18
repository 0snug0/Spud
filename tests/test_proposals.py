"""proposal file|decide: the climb from a member to Spud, what create does, and who decides what a returned holder left."""

import unittest

from helpers import EXIT_ERROR, EXIT_OWNERSHIP, SpudTestCase, real_config


class ProposalTest(SpudTestCase):
    def setUp(self):
        super().setUp()
        self.t = self.new_ticket("Origin", status="active")
        self.lead = self.new_member(self.t["key"], persona="writer", model="sonnet", name="Kestrel")
        self.home.json("member", "start", self.lead["ref"], actor="spud")
        self.child = self.new_member(self.t["key"], actor=self.lead["ref"], name="Rosara")
        self.home.json("member", "start", self.child["ref"], actor=self.lead["ref"])

    def file_one(self, actor=None, title="Add an index page for docs/toy/"):
        return self.home.json(
            "proposal", "file", "--title", title, "--why", "No entry point.", "--evidence", "ls docs/toy", "--priority", "P3",
            actor=actor or self.child["ref"],
        )["proposal"]

    def test_file_sets_the_holder_to_the_parent(self):
        p = self.file_one()
        self.assertEqual(p["status"], "open")
        self.assertEqual(p["origin"], self.child["ref"])
        self.assertEqual(p["holder"], self.lead["ref"])
        self.assertEqual(p["ticket"], self.t["key"])
        self.assertEqual(p["suggested_priority"], "P3")
        kinds = [e["kind"] for e in self.home.json("events", "--member", self.child["ref"], "--kind", "proposal.filed")["events"]]
        self.assertEqual(kinds, ["proposal.filed"])
        proc = self.home.run("proposal", "file", "--title", "x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)

    def test_the_climb_escalate_then_create(self):
        p = self.file_one()
        proc = self.home.run("proposal", "decide", str(p["id"]), "--decision", "escalate", actor=self.child["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        proc = self.home.run("proposal", "decide", str(p["id"]), "--decision", "create", actor=self.lead["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)  # Law 6
        up = self.home.json("proposal", "decide", str(p["id"]), "--decision", "escalate", "--reason", "outside my paths", actor=self.lead["ref"])["proposal"]
        self.assertEqual(up["status"], "open")
        self.assertIsNone(up["holder"])
        proc = self.home.run("proposal", "decide", str(p["id"]), "--decision", "decline", actor=self.lead["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        proc = self.home.run("proposal", "decide", str(p["id"]), "--decision", "escalate", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        out = self.home.json("proposal", "decide", str(p["id"]), "--decision", "create", "--priority", "P3", actor="spud")
        created = out["ticket"]
        self.assertEqual(created["key"], "SPD-002")
        self.assertEqual(created["origin"], "proposal")
        self.assertEqual(created["priority"], "P3")
        self.assertEqual(created["title"], p["title"])
        self.assertEqual(created["proposed_by"], self.child["ref"])
        self.assertEqual(out["proposal"]["status"], "created")
        self.assertEqual(out["proposal"]["created_ticket"], "SPD-002")
        decisions = self.home.rows("SELECT by_member_id, decision, reason FROM proposal_decisions ORDER BY id")
        self.assertEqual(decisions[0]["decision"], "escalate")
        self.assertEqual(decisions[0]["by_member_id"], self.lead["id"])
        self.assertEqual(decisions[1]["decision"], "create")
        self.assertIsNone(decisions[1]["by_member_id"])
        shown = self.home.json("ticket", "show", "SPD-002")["ticket"]
        self.assertEqual(shown["proposed_by"], "SPUD-001/Rosara")
        proc = self.home.run("proposal", "decide", str(p["id"]), "--decision", "decline", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)  # already decided

    def test_absorb_and_decline(self):
        a = self.file_one(title="Absorbed")
        out = self.home.json("proposal", "decide", str(a["id"]), "--decision", "absorb", "--reason", "in scope", actor=self.lead["ref"])["proposal"]
        self.assertEqual(out["status"], "absorbed")
        d = self.file_one(title="Declined")
        self.home.json("proposal", "decide", str(d["id"]), "--decision", "escalate", actor=self.lead["ref"])
        out = self.home.json("proposal", "decide", str(d["id"]), "--decision", "decline", "--reason", "not now", actor="spud")["proposal"]
        self.assertEqual(out["status"], "declined")
        proc = self.home.run("proposal", "decide", str(d["id"]), "--decision", "absorb", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        listing = self.home.json("proposal", "list", "--ticket", self.t["key"])["proposals"]
        self.assertEqual([p["status"] for p in listing], ["absorbed", "declined"])
        self.assertEqual(len(self.home.json("proposal", "list", "--open")["proposals"]), 0)

    def test_rendered_sections(self):
        p = self.file_one()
        self.home.json("proposal", "decide", str(p["id"]), "--decision", "escalate", actor=self.lead["ref"])
        self.home.json("proposal", "decide", str(p["id"]), "--decision", "create", actor="spud")
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        member = (out / "ledger" / "teams" / "SPUD-001" / "Rosara.md").read_text(encoding="utf-8")
        section = member.split("## Ticket proposals\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("**Add an index page for docs/toy/**", section)
        self.assertIn("No entry point.", section)
        self.assertIn("ls docs/toy", section)
        self.assertIn("P3", section)
        self.assertIn("[[SPD-002]]", section)
        ticket = (out / "ledger" / "tickets" / "SPD-001.md").read_text(encoding="utf-8")
        received = ticket.split("## Proposals received\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("**Add an index page for docs/toy/**", received)
        self.assertIn("[[SPUD-001/Rosara]]", received)
        self.assertIn("created as [[SPD-002]] at P3", received)
        new_ticket = (out / "ledger" / "tickets" / "SPD-002.md").read_text(encoding="utf-8")
        self.assertIn('proposed_by: "[[SPUD-001/Rosara]]"', new_ticket)
        self.assertIn("origin: proposal", new_ticket)


def deeper_config():
    config = real_config()
    config["limits"]["max_depth"] = 3  # a grandchild, so the climb can pass two returned members in a row
    return config


class DeadHolderTest(SpudTestCase):
    """SPD-120: a proposal whose recorded holder has returned falls to the nearest ancestor that can still act, and to
    Spud at the root, resolved and written when somebody decides it."""

    config = deeper_config()

    def setUp(self):
        super().setUp()
        self.t = self.new_ticket("Origin", status="active")
        self.lead = self.new_member(self.t["key"], persona="writer", model="sonnet", name="Kestrel")
        self.home.json("member", "start", self.lead["ref"], actor="spud")
        self.child = self.new_member(self.t["key"], actor=self.lead["ref"], name="Rosara")
        self.home.json("member", "start", self.child["ref"], actor=self.lead["ref"])
        self.grandchild = self.new_member(self.t["key"], actor=self.child["ref"], name="Ozette")
        self.home.json("member", "start", self.grandchild["ref"], actor=self.child["ref"])

    def file_one(self, actor=None, title="Add an index page for docs/toy/"):
        return self.home.json(
            "proposal", "file", "--title", title, "--why", "No entry point.", "--evidence", "ls docs/toy", "--priority", "P3",
            actor=actor or self.grandchild["ref"],
        )["proposal"]

    def finish(self, ref, actor, status="failed", outcome="Rate limit."):
        return self.home.json("member", "finish", ref, "--status", status, "--outcome", outcome, actor=actor)

    def decided_data(self):
        events = self.home.json("events", "--kind", "proposal.decided")["events"]
        return events[-1]["data"]

    def test_the_nearest_live_ancestor_decides_what_a_dead_holder_left(self):
        p = self.file_one()
        self.assertEqual(p["holder"], self.child["ref"])
        self.finish(self.child["ref"], actor=self.lead["ref"])
        out = self.home.json("proposal", "decide", str(p["id"]), "--decision", "absorb", "--reason", "in scope", actor=self.lead["ref"])["proposal"]
        self.assertEqual(out["status"], "absorbed")
        self.assertEqual(out["holder"], self.lead["ref"])  # the climb is written where it was read
        data = self.decided_data()
        self.assertEqual(data["inherited_from"], self.child["ref"])
        self.assertEqual(data["inherited_from_status"], "failed")
        self.assertEqual(data["decision"], "absorb")
        rows = self.home.rows("SELECT by_member_id, decision FROM proposal_decisions ORDER BY id")
        self.assertEqual(rows, [{"by_member_id": self.lead["id"], "decision": "absorb"}])

    def test_the_inheriting_ancestor_declines_or_escalates_but_never_creates(self):
        p = self.file_one()
        self.finish(self.child["ref"], actor=self.lead["ref"], status="blocked", outcome="Needs Eric.")
        proc = self.home.run("proposal", "decide", str(p["id"]), "--decision", "create", actor=self.lead["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)  # Law 6 still holds for an inherited holder
        up = self.home.json("proposal", "decide", str(p["id"]), "--decision", "escalate", "--reason", "above my brief", actor=self.lead["ref"])["proposal"]
        self.assertEqual(up["status"], "open")
        self.assertIsNone(up["holder"])  # escalate's own move wins over the climb it was resolved with
        self.assertEqual(self.decided_data()["inherited_from"], self.child["ref"])
        out = self.home.json("proposal", "decide", str(p["id"]), "--decision", "decline", "--reason", "not now", actor="spud")["proposal"]
        self.assertEqual(out["status"], "declined")
        d = self.file_one(title="Filed after the holder blocked")
        out = self.home.json("proposal", "decide", str(d["id"]), "--decision", "decline", "--reason", "no", actor=self.lead["ref"])["proposal"]
        self.assertEqual(out["status"], "declined")  # a blocked holder cannot decide either, so this one climbed too
        self.assertEqual(self.decided_data()["inherited_from_status"], "blocked")

    def test_the_climb_passes_several_returned_members_and_ends_at_spud(self):
        p = self.file_one()
        self.finish(self.child["ref"], actor=self.lead["ref"])
        self.finish(self.lead["ref"], actor="spud", status="failed", outcome="Killed with its child.")
        proc = self.home.run("proposal", "decide", str(p["id"]), "--decision", "absorb", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)  # Law 1: Spud does not absorb, even what he inherited
        proc = self.home.run("proposal", "decide", str(p["id"]), "--decision", "decline", actor=self.grandchild["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)  # a live member that is not the effective holder
        self.assertIn("(was %s)" % self.child["ref"], proc.stderr)
        out = self.home.json("proposal", "decide", str(p["id"]), "--decision", "create", "--priority", "P3", actor="spud")
        self.assertEqual(out["proposal"]["status"], "created")
        self.assertIsNone(out["proposal"]["holder"])
        self.assertEqual(out["ticket"]["proposed_by"], self.grandchild["ref"])
        self.assertEqual(out["ticket"]["origin"], "proposal")
        data = self.decided_data()
        self.assertEqual((data["inherited_from"], data["inherited_from_status"]), (self.child["ref"], "failed"))
        self.assertEqual(data["created_ticket"], out["ticket"]["key"])

    def test_spud_creates_what_a_failed_root_member_held(self):
        """The shape of the row SPD-120 was filed for: a child filed it, its root parent held it and then failed."""
        p = self.file_one(actor=self.child["ref"])
        self.assertEqual(p["holder"], self.lead["ref"])
        self.finish(self.lead["ref"], actor="spud", outcome="A rate limit killed it with its child.")
        out = self.home.json("proposal", "decide", str(p["id"]), "--decision", "create", "--priority", "P3", actor="spud")
        self.assertEqual(out["proposal"]["status"], "created")
        self.assertEqual(out["ticket"]["proposed_by"], self.child["ref"])
        self.assertIsNone(self.home.scalar("SELECT holder_member_id FROM proposals WHERE id = ?", p["id"]))
        data = self.decided_data()
        self.assertEqual((data["inherited_from"], data["inherited_from_status"]), (self.lead["ref"], "failed"))

    def test_a_live_holder_is_unchanged(self):
        p = self.file_one()
        proc = self.home.run("proposal", "decide", str(p["id"]), "--decision", "create", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        self.assertIn("held by %s, not by Spud yet" % self.child["ref"], proc.stderr)
        self.assertNotIn("(was", proc.stderr)
        proc = self.home.run("proposal", "decide", str(p["id"]), "--decision", "decline", actor=self.lead["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)  # the holder's own parent waits for the holder
        out = self.home.json("proposal", "decide", str(p["id"]), "--decision", "absorb", actor=self.child["ref"])["proposal"]
        self.assertEqual(out["status"], "absorbed")
        self.assertEqual(out["holder"], self.child["ref"])
        self.assertNotIn("inherited_from", self.decided_data())

    def test_member_finish_names_what_it_still_held_and_refuses_nothing(self):
        first = self.file_one()
        second = self.file_one(title="Delete the dead flag")
        proc = self.home.run("member", "finish", self.child["ref"], "--status", "done", "--outcome", "Done.", actor=self.lead["ref"])
        self.assertIn("%s left 2 open proposals for %s to decide" % (self.child["ref"], self.lead["ref"]), proc.stdout)
        self.assertIn("%d (%s)" % (first["id"], first["title"]), proc.stdout)
        self.assertIn("%d (%s)" % (second["id"], second["title"]), proc.stdout)
        out = self.home.json("member", "finish", self.lead["ref"], "--status", "failed", "--outcome", "Out of budget.", actor="spud")
        self.assertEqual(out["member"]["status"], "failed")  # Law 9: the outcome is recorded whatever it leaves held
        self.assertEqual([h["id"] for h in out["held_proposals"]], [first["id"], second["id"]])  # inherited from Rosara
        self.assertEqual({h["holder"] for h in out["held_proposals"]}, {None})  # they fall to Spud
        out = self.home.json("member", "finish", self.grandchild["ref"], "--status", "done", "--outcome", "Filed them.", actor="spud")
        self.assertNotIn("held_proposals", out)  # it filed them; it never held them

    def test_proposal_list_shows_who_must_decide_now(self):
        p = self.file_one()
        proc = self.home.run("member", "finish", self.child["ref"], "--status", "failed", "--outcome", "Rate limit.", actor=self.lead["ref"])
        self.assertIn("left 1 open proposal for %s to decide: %d (%s)" % (self.lead["ref"], p["id"], p["title"]), proc.stdout)
        self.finish(self.lead["ref"], actor="spud")
        row = self.home.json("proposal", "list", "--open")["proposals"][0]
        self.assertEqual(row["holder"], self.child["ref"])  # the record keeps who held it
        self.assertIsNone(row["effective_holder"])
        text = self.home.run("proposal", "list", "--open").stdout
        self.assertIn("Spud (was %s)" % self.child["ref"], text)
        self.home.json("proposal", "decide", str(p["id"]), "--decision", "decline", "--reason", "no", actor="spud")
        settled = self.home.json("proposal", "list")["proposals"][0]
        self.assertEqual((settled["holder"], settled["effective_holder"]), (None, None))
        self.assertNotIn("(was", self.home.run("proposal", "list").stdout)


if __name__ == "__main__":
    unittest.main()
