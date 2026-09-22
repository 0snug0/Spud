"""spud session claim|release|show and the session gate on Spud's commands (SPD-014, design sections 2.5, 6.1 and 6.3).
Eric's answer of 2026-09-14: a session in another project is Spud only after /spud claims it; the home stays `always`.
SessionHooksTest is SPW-003: `session show` says when the session it describes loads none of the ledger's hooks where
that session actually reads them -- the state the mode alone never showed."""

import json
import unittest

from helpers import EXIT_ERROR, EXIT_OWNERSHIP, RepoMixin, SpudTestCase

SESSION = "11111111-2222-4333-8444-555555555555"
OTHER_SESSION = "66666666-7777-4888-9999-000000000000"
AGENT = "---\nname: spudagent\ndescription: A spudagent (test fixture).\n---\nRun `python3.14 -I -S {{launcher}}`.\n"
EVENTS = ("PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop", "SessionStart", "Stop", "UserPromptSubmit")


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
        for needle in ("badtakes", "BAD-nnn", "BADS-nnn", "landing pr", str(self.home.path), "Spud's laws govern delegation"):
            self.assertIn(needle, card)
        row = self.home.rows("SELECT s.session_id, p.key, s.released_at, s.cwd FROM sessions s JOIN projects p ON p.id = s.project_id")
        self.assertEqual(row, [{"session_id": SESSION, "key": "badtakes", "released_at": None, "cwd": str(self.other)}])
        e = self.home.json("events", "--kind", "session.claimed")["events"]
        self.assertEqual([(x["data"]["session_id"], x["data"]["project"], x["data"]["how"]) for x in e], [(SESSION, "badtakes", "command")])

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
        # SPD-048: the note counts the board lines it cut, exactly
        board = self.cli("board", "--brief", "--project", "badtakes").stdout.rstrip("\n").split("\n")
        shown = card.split("\nboard (badtakes):\n", 1)[1].split("\n")
        self.assertRegex(shown[-1], r"\A\(\d+ more lines? cut to fit; run `spud board --brief` for the rest\)\Z")
        self.assertEqual(shown[:-1], board[:len(shown) - 1])
        self.assertEqual(shown[-1], "(%d more lines cut to fit; run `spud board --brief` for the rest)" % (len(board) - (len(shown) - 1)))

    def test_the_card_counts_the_parked_tickets_of_its_own_project(self):
        """SPD-096: the count line is the project's, because the card calls board_brief_text with the project's rows."""
        home = self.new_ticket("Home one")
        self.home.json("ticket", "move", home["key"], "--status", "parked", "--reason", "Eric's go", actor="spud")
        for n in range(2):
            bad = self.cli_json("ticket", "new", "--project", "badtakes", "--title", "BadTakes %d" % n, actor="spud")["ticket"]
            self.cli("ticket", "move", bad["key"], "--status", "parked", "--reason", "waiting", actor="spud")
        card = self.cli("session", "claim", actor="spud", cwd=self.other, session=SESSION).stdout
        self.assertIn("2 parked (spud board --parked)", card)
        self.assertNotIn("Home one", card)
        self.assertIn("3 parked (spud board --parked)", self.cli("board", "--brief", cwd=self.home.path).stdout)

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
        self.assertEqual((home["project"], home["checkout"]["kind"], home["mode"]), (None, "home", "spud"))

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


class SessionHooksTest(RepoMixin, SpudTestCase):
    """SPW-003: a session loads the ledger's hooks from the directory it was launched in (CLAUDE_PROJECT_DIR), so one
    launched outside the home and outside every registered checkout runs none of them -- silently, since a recorded claim
    made `session show` print `mode spud` over exactly that.  The hooks line appears only when there is news, and the
    unset variable is its own answer: the CLI claims only what the files it can name prove."""

    def setUp(self):
        super().setUp()
        agents = self.home.path / ".claude" / "agents"
        agents.mkdir(parents=True, exist_ok=True)
        (agents / "spudagent.md").write_text(AGENT, encoding="utf-8")  # what `project install` renders the user copy from
        self.other = self.make_repo("badtakes-")
        self.add_project(self.other)
        self.wt = self.add_worktree(self.other, "bad-001")
        self.user_settings = self.home.path / ".user-claude" / "settings.json"  # SPUD_USER_CLAUDE_DIR: ~/.claude here

    def show(self, launch=None, session=SESSION, cwd=None):
        """`session show` for a session launched in `launch` (None: CLAUDE_PROJECT_DIR unset, as in the session that found
        SPW-003), as JSON and as text."""
        env = {"CLAUDE_PROJECT_DIR": str(launch)} if launch is not None else {}
        kw = {"env": env, "session": session, "cwd": cwd or self.home.path}
        return self.cli_json("session", "show", **kw)["hooks"], self.cli("session", "show", **kw).stdout

    def test_a_session_launched_outside_every_project_is_told_it_loads_no_hook(self):
        nowhere = self.scratch_dir("nowhere-")
        hooks, text = self.show(nowhere)
        self.assertEqual((hooks["state"], hooks["launch"], hooks["events"], hooks["project"]), ("absent", str(nowhere), [], None))
        self.assertEqual(hooks["missing"], list(EVENTS))
        self.assertEqual([f["exists"] for f in hooks["files"]], [False, False, False])
        self.assertIn("hooks     no ledger hook of this home is loaded in this session", text)
        self.assertIn("it reads its hooks from %s" % nowhere, text)
        self.assertIn("a spawned member never binds", text)
        self.assertIn("relaunch the session in %s or in a registered project's checkout" % self.home.path, text)
        self.assertIn("`spud --as spud settings sync --path %s/.claude/settings.json`" % nowhere, text)
        self.assertEqual(len(hooks["lines"]), 2)  # --json says as much as the text: the state, then the fix
        self.assertEqual([line for line in hooks["lines"] if line in text], hooks["lines"])
        # A claim changes nothing about what the session loads, and the mode it does set says nothing about it either.
        self.cli("session", "claim", actor="spud", cwd=self.other, session=SESSION)
        hooks, text = self.show(nowhere, cwd=self.other)
        self.assertIn("mode      outside every project (behaves as Spud)", text)
        self.assertEqual(hooks["state"], "absent")
        self.assertIn("no ledger hook of this home is loaded in this session", text)

    def test_the_home_and_an_installed_checkout_say_nothing_new(self):
        self.home.json("settings", "sync")  # writes <home>/.claude/settings.json, which a session launched here reads
        hooks, text = self.show(self.home.path)
        self.assertEqual(hooks["state"], "loaded")
        self.assertEqual(hooks["missing"], [])
        self.assertEqual([f["path"] for f in hooks["files"] if f["events"]], [str(self.home.path / ".claude" / "settings.json")])
        self.assertNotIn("hooks", text)  # nothing new: the text is what it was before SPW-003
        self.assertEqual(len(hooks["lines"]), 1)  # the report still carries the answer, with no fix to name
        self.assertNotIn(hooks["lines"][0], text)
        self.cli("project", "install", "badtakes", actor="spud")
        hooks, text = self.show(self.other)
        self.assertEqual((hooks["state"], hooks["project"], hooks["root"]), ("loaded", "badtakes", None))
        self.assertEqual([f["path"] for f in hooks["files"] if f["events"]], [str(self.other / ".claude" / "settings.local.json")])
        self.assertNotIn("hooks", text)

    def test_a_worktree_or_a_subdirectory_launched_in_directly_loads_none(self):
        """`project install` writes the main checkout's local settings and `settings sync` the home's own, and neither a
        worktree nor a subdirectory holds a copy: a session launched there reads none of them, though the directory is in
        the project all the same.  Entering the worktree from the checkout is what keeps the hooks (probe P5)."""
        self.cli("project", "install", "badtakes", actor="spud")
        hooks, text = self.show(self.wt)
        self.assertEqual((hooks["state"], hooks["project"], hooks["root"]), ("absent", "badtakes", str(self.other)))
        self.assertIn("%s is in `badtakes` but is not %s, the directory this home installs its hook lines into" % (self.wt, self.other), text)
        self.assertIn("EnterWorktree", text)
        inside = self.home.path / "docs"
        inside.mkdir(exist_ok=True)
        hooks, text = self.show(inside)
        self.assertEqual((hooks["state"], hooks["project"], hooks["root"]), ("absent", "home", str(self.home.path)))
        self.assertIn("%s is in `home` but is not %s" % (inside, self.home.path), text)

    def test_without_claude_project_dir_it_says_only_what_it_can_prove(self):
        """The variable was unset in the session that found SPW-003, so its absence proves nothing: the answer is
        `unknown`, never `absent`.  The one file that can still be named is the user-scope one -- and when that carries
        the lines, every session reads them and `loaded` holds with no CLAUDE_PROJECT_DIR at all."""
        hooks, text = self.show(None)
        self.assertEqual((hooks["state"], hooks["launch"], hooks["project"]), ("unknown", None, None))
        self.assertEqual([f["path"] for f in hooks["files"]], [str(self.user_settings)])
        self.assertIn("CLAUDE_PROJECT_DIR is not set", text)
        self.assertIn("cannot be named", text)
        self.assertNotIn("no ledger hook of this home is loaded", text)  # the overclaim SPW-003 asked us not to make
        self.assertIn("relaunch the session in %s" % self.home.path, text)
        self.assertNotIn("install this home's hook lines where this session reads them", text)  # no directory to name
        # SPW-003 as it was found: the variable unset, a claim recorded from a checkout, and `mode spud` over a session
        # whose hooks nothing had established.  The mode still reads spud -- and now the line beside it says so.
        self.cli("session", "claim", actor="spud", cwd=self.other, session=SESSION)
        hooks, text = self.show(None, cwd=self.other)
        self.assertIn("mode      spud (claimed", text)
        self.assertEqual(hooks["state"], "unknown")
        self.assertIn("CLAUDE_PROJECT_DIR is not set", text)
        self.home.json("settings", "sync", "--path", self.user_settings)
        hooks, text = self.show(None)
        self.assertEqual((hooks["state"], hooks["launch"]), ("loaded", None))
        self.assertNotIn("hooks", text)

    def test_outside_a_claude_code_session_nothing_loads_them_and_nothing_is_said(self):
        hooks, text = self.show(self.scratch_dir("nowhere-"), session=None)
        self.assertEqual(hooks["state"], "no_session")
        self.assertIn("session   none (outside a Claude Code session)", text)
        self.assertNotIn("hooks", text)

    def test_a_settings_file_missing_one_event_is_partly_loaded(self):
        """Some but not all: what is missing is named, because the events that are there hide the ones that are not."""
        launch = self.scratch_dir("half-")
        path = launch / ".claude" / "settings.json"
        self.home.json("settings", "sync", "--path", path)
        data = json.loads(path.read_text(encoding="utf-8"))
        del data["hooks"]["Stop"]
        del data["hooks"]["SubagentStart"]
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        hooks, text = self.show(launch)
        self.assertEqual((hooks["state"], hooks["missing"]), ("partial", ["SubagentStart", "Stop"]))
        self.assertEqual(hooks["files"][1]["events"], ["PostToolUse", "PreToolUse", "SessionStart", "SubagentStop", "UserPromptSubmit"])
        self.assertIn("hooks     some of this home's ledger hooks are not loaded in this session", text)
        self.assertIn("no file it reads carries a line for SubagentStart, Stop", text)

    def test_another_homes_hook_lines_are_not_this_homes(self):
        """The line has to name *this* home: one left behind by another home runs, and writes that home's ledger."""
        launch = self.scratch_dir("other-home-")
        path = launch / ".claude" / "settings.json"
        self.home.json("settings", "sync", "--path", path)
        path.write_text(path.read_text(encoding="utf-8").replace("SPUD_HOME=%s " % self.home.path, "SPUD_HOME=/elsewhere "), encoding="utf-8")
        hooks, _text = self.show(launch)
        self.assertEqual((hooks["state"], hooks["events"]), ("absent", []))


if __name__ == "__main__":
    unittest.main()
