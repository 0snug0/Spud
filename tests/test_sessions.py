"""spud session claim|release|show and the session gate on Spud's commands (SPD-014, design sections 2.5, 6.1 and 6.3).
Eric's answer of 2026-09-14: a session in another project is Spud only after /spud claims it; the home stays `always`."""

import unittest

from helpers import EXIT_ERROR, EXIT_OWNERSHIP, RepoMixin, SpudTestCase

SESSION = "11111111-2222-4333-8444-555555555555"
OTHER_SESSION = "66666666-7777-4888-9999-000000000000"


class SessionTest(RepoMixin, SpudTestCase):
    def setUp(self):
        super().setUp()
        self.other = self.make_repo("badtakes-")
        self.add_project(self.other)
        self.wt = self.add_worktree(self.other, "bad-001")

    def test_claim_needs_a_claude_code_session(self):
        proc = self.cli("session", "claim", actor="spud", cwd=self.other, check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("nothing to claim", proc.stderr)

    def test_claim_takes_the_working_directorys_project_and_prints_a_short_card(self):
        proc = self.cli("session", "claim", actor="spud", cwd=self.other, session=SESSION)
        card = proc.stdout
        self.assertLessEqual(len(card.encode("utf-8")), 1536 + 1, card)
        for needle in ("badtakes", "BAD-nnn", "BADS-nnn", "landing pr", str(self.home.path), "ledger commit", "Spud's laws govern delegation"):
            self.assertIn(needle, card)
        row = self.home.rows("SELECT s.session_id, p.key, s.released_at, s.cwd FROM sessions s JOIN projects p ON p.id = s.project_id")
        self.assertEqual(row, [{"session_id": SESSION, "key": "badtakes", "released_at": None, "cwd": str(self.other)}])
        e = self.home.json("events", "--kind", "session.claimed")["events"]
        self.assertEqual([(x["data"]["session_id"], x["data"]["project"]) for x in e], [(SESSION, "badtakes")])

    def test_claim_from_a_worktree_and_with_an_explicit_project(self):
        self.assertEqual(self.cli_json("session", "claim", actor="spud", cwd=self.wt, session=SESSION)["project"], "badtakes")
        self.assertEqual(self.cli_json("session", "claim", "--project", "badtakes", actor="spud", cwd=self.scratch_dir("elsewhere-"), session=OTHER_SESSION)["project"], "badtakes")
        self.assertEqual(self.home.scalar("SELECT count(*) FROM sessions"), 2)

    def test_claim_outside_every_project_is_refused_and_the_home_needs_none(self):
        proc = self.cli("session", "claim", actor="spud", cwd=self.scratch_dir("nowhere-"), session=SESSION, check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("not a registered project", proc.stderr)
        out = self.cli_json("session", "claim", actor="spud", cwd=self.home.path, session=SESSION)
        self.assertFalse(out["claimed"])
        self.assertIn("every session in the home is Spud", self.cli("session", "claim", actor="spud", cwd=self.home.path, session=SESSION).stdout)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM sessions"), 0)
        t = self.new_ticket("Home", status="active")
        m = self.new_member(t["key"])
        proc = self.cli("session", "claim", actor=m["ref"], cwd=self.other, session=SESSION, check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)

    def test_the_card_stays_within_its_cap_with_a_long_board(self):
        for n in range(40):
            self.cli("ticket", "new", "--project", "badtakes", "--status", "active", "--title", "A long BadTakes ticket title number %02d, for the card" % n, actor="spud")
        card = self.cli("session", "claim", actor="spud", cwd=self.other, session=SESSION).stdout.rstrip("\n")
        self.assertLessEqual(len(card.encode("utf-8")), 1536)
        self.assertIn("BAD-040", card)  # the board lists the newest first
        self.assertIn("run `spud board --brief`", card)

    def test_show_release_and_the_mode(self):
        out = self.cli_json("session", "show", cwd=self.wt, session=SESSION)
        self.assertEqual((out["home"], out["project"]["key"], out["checkout"]["kind"], out["checkout"]["branch"], out["session_id"], out["mode"], out["claimed_at"]),
                         (str(self.home.path), "badtakes", "worktree", "bad-001", SESSION, "plain", None))
        self.cli("session", "claim", actor="spud", cwd=self.wt, session=SESSION)
        out = self.cli_json("session", "show", cwd=self.other, session=SESSION)
        self.assertEqual((out["checkout"]["kind"], out["checkout"]["branch"], out["mode"]), ("root", "main", "spud"))
        self.assertIsNotNone(out["claimed_at"])
        self.assertIn("mode      spud (claimed", self.cli("session", "show", cwd=self.other, session=SESSION).stdout)
        self.assertEqual(self.cli_json("session", "show", cwd=self.other, session=OTHER_SESSION)["mode"], "plain")
        self.assertTrue(self.cli_json("session", "release", actor="spud", cwd=self.other, session=SESSION)["released"])
        self.assertEqual(self.cli_json("session", "show", cwd=self.other, session=SESSION)["mode"], "plain")
        self.assertIsNotNone(self.home.scalar("SELECT released_at FROM sessions WHERE session_id = ?", SESSION))
        self.assertFalse(self.cli_json("session", "release", actor="spud", cwd=self.other, session=SESSION)["released"])
        self.assertEqual(len(self.home.json("events", "--kind", "session.released")["events"]), 1)
        home = self.cli_json("session", "show", cwd=self.home.path, session=SESSION)
        self.assertEqual((home["project"]["key"], home["mode"]), ("spud", "spud"))

    def test_spuds_commands_refuse_an_unclaimed_session_in_a_claim_project(self):
        proc = self.cli("ticket", "new", "--title", "Too early", actor="spud", cwd=self.other, session=SESSION, check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        self.assertIn("this session is not Spud", proc.stderr)
        self.assertIn("/spud", proc.stderr)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM tickets"), 0)
        self.cli("session", "claim", actor="spud", cwd=self.other, session=SESSION)
        self.assertEqual(self.cli_json("ticket", "new", "--title", "Claimed", actor="spud", cwd=self.wt, session=SESSION)["ticket"]["key"], "BAD-001")
        # another session there is still not Spud; the home and a terminal outside any session are unaffected
        self.assertEqual(self.cli("ticket", "new", "--title", "x", actor="spud", cwd=self.other, session=OTHER_SESSION, check=False).returncode, EXIT_OWNERSHIP)
        self.assertEqual(self.cli_json("ticket", "new", "--title", "Home", actor="spud", cwd=self.home.path, session=OTHER_SESSION)["ticket"]["key"], "SPD-001")
        self.assertEqual(self.cli_json("ticket", "new", "--title", "Terminal", actor="spud", cwd=self.other)["ticket"]["key"], "BAD-002")
        # reads need no claim
        self.assertEqual(self.cli("board", cwd=self.other, session=OTHER_SESSION).returncode, 0)

    def test_an_always_project_needs_no_claim(self):
        always = self.make_repo("always-")
        self.add_project(always, "always", "ALW", "ALWS", "merge", "--sessions", "always")
        self.assertEqual(self.cli_json("ticket", "new", "--title", "No claim", actor="spud", cwd=always, session=SESSION)["ticket"]["key"], "ALW-001")
        self.assertEqual(self.cli_json("session", "show", cwd=always, session=SESSION)["mode"], "spud")


if __name__ == "__main__":
    unittest.main()
