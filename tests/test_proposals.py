"""proposal file|decide: the climb from a member to Spud, and what create does."""

import unittest

from helpers import EXIT_ERROR, EXIT_OWNERSHIP, SpudTestCase


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


if __name__ == "__main__":
    unittest.main()
