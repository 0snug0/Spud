"""SPD-097: the home is not a project.  It enters the path rule under the reserved key `home`; project spud is the tool
repository; `home:<glob>` names the home in a deliverable; `session show` names a session launched in the home."""

import json
import unittest

from helpers import EXIT_ERROR, RepoMixin, SpudTestCase, load_spud_module

spud = load_spud_module()
SESSION = "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b"
AGENT = "a0123456789abcdef"


class SplitCase(RepoMixin, SpudTestCase):
    """A scratch home whose tool is a separate scratch main checkout, which `spud init` registered as project spud: every
    SpudTestCase's home since SPD-233 (tests/helpers.fixture)."""

    def setUp(self):
        super().setUp()
        self.tool = self.home.tool

    def ctx(self):
        return spud.Ctx(self.home.path, "SPUD_HOME", False, tool=self.tool)


class HomeRowTest(SplitCase):
    def test_project_spud_is_rooted_at_the_tool_and_the_home_is_no_project(self):
        p = self.home.json("project", "show", "spud")["project"]
        self.assertEqual((p["root"], p["sessions"], p["landing"]), (str(self.tool), "claim", "merge"))
        self.assertEqual(spud.home_row(self.ctx())["key"], "home")
        self.assertTrue(spud.is_home(spud.home_row(self.ctx())))
        con = spud.connect(self.ctx())
        try:
            checkouts = spud.project_checkouts(self.ctx(), con)
        finally:
            con.close()
        self.assertEqual([(p["key"], roots[0]) for p, roots in checkouts], [("home", str(self.home.path)), ("spud", str(self.tool))])

    def test_the_path_rule_for_spud_and_for_a_member(self):
        t = self.new_ticket("Split", status="active")
        wt = self.add_worktree(self.tool, "spd-001-split", inside=True)
        m = self.new_member(t["key"], name="Russet", deliverable=["bin/**", "home:docs/x.md"], cwd=wt)  # SPD-098: binds wt
        ctx = self.ctx()
        con = spud.connect(ctx)
        try:
            row = con.execute("SELECT * FROM members WHERE id = ?", (m["id"],)).fetchone()
            home, tool = self.home.path, self.tool

            def reason(path, agent_id=None, member=None):
                return spud.edit_reason(ctx, con, agent_id, member, str(path), str(home))[0]

            # Spud: his own set in the home, nothing else in the home, nothing in project spud
            self.assertIsNone(reason(home / "CLAUDE.md"))
            self.assertIsNone(reason(home / "docs" / "superpowers" / "specs" / "x.md"))
            self.assertIn("Law 1", reason(home / "docs" / "x.md"))
            self.assertIn("Law 5", reason(home / "ledger" / "tickets" / "SPD-001.md"))
            self.assertIn("project spud, where every path is a deliverable", reason(tool / "CLAUDE.md"))
            # the member: bare globs in the tool's worktree its ticket is bound to (SPD-098), home: globs in the home,
            # generated roots nowhere
            self.assertIsNone(reason(wt / "bin" / "x.py", AGENT, row))
            self.assertIn("bound to %s" % wt, reason(tool / "bin" / "x.py", AGENT, row))
            self.assertIsNone(reason(home / "docs" / "x.md", AGENT, row))
            self.assertIn("Law 5", reason(home / "bin" / "x.py", AGENT, row))
            self.assertIn("Law 5", reason(wt / "docs" / "x.md", AGENT, row))
            self.assertIn("Law 5", reason(home / "ledger" / "teams" / "SPUD-001" / "Russet.md", AGENT, row))
        finally:
            con.close()

    def test_a_deliverable_may_name_the_home_and_must_name_an_active_project_otherwise(self):
        t = self.new_ticket("Globs")
        m = self.new_member(t["key"], deliverable=["home:docs/", "spud:bin/x.py", "tests/**"], cwd=self.add_worktree(self.tool, "spd-001-globs"))
        self.assertEqual(m["deliverables"], ["home:docs/**", "spud:bin/x.py", "tests/**"])
        proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", "--deliverable", "nope:x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("no active project", proc.stderr)


class SessionInTheHomeTest(SplitCase):
    def test_session_show_names_the_home_and_the_tool(self):
        shown = self.cli_json("session", "show", cwd=self.home.path, session=SESSION)
        self.assertEqual((shown["project"], shown["checkout"], shown["mode"]), (None, {"path": str(self.home.path), "kind": "home", "branch": None}, "spud"))
        text = self.cli("session", "show", cwd=self.home.path, session=SESSION).stdout
        self.assertIn("checkout  %s (home)" % self.home.path, text)
        self.assertIn("Spud's home, not a project", text)
        # project spud claims (init's default), so a session in the tool is plain until it claims; claimed, it is Spud's
        shown = self.cli_json("session", "show", cwd=self.tool, session=SESSION)
        self.assertEqual((shown["project"]["key"], shown["checkout"]["kind"], shown["checkout"]["branch"], shown["mode"]), ("spud", "root", "main", "plain"))
        self.cli("session", "claim", actor="spud", cwd=self.tool, session=SESSION)
        self.assertEqual(self.cli_json("session", "show", cwd=self.tool, session=SESSION)["mode"], "spud")

    def test_the_session_start_hook_gives_the_board_in_the_home_and_the_project_context_in_the_tool_once_it_claims(self):
        self.new_ticket("Board me", status="active")
        r = self.home.hook("SessionStart", {"hook_event_name": "SessionStart", "session_id": SESSION, "cwd": str(self.home.path), "source": "startup"})
        self.assertTrue(r.context.startswith("Ledger board ("), r.context)
        self.assertIn("SPD-001 active", r.context)
        r = self.home.hook("SessionStart", {"hook_event_name": "SessionStart", "session_id": SESSION, "cwd": str(self.tool), "source": "startup"})
        self.assertIn("is Spud project `spud`", r.context)
        self.assertIn("This session is not Spud", r.context)
        proc = self.cli("ticket", "new", "--title", "Too early", actor="spud", cwd=self.tool, session=SESSION, check=False)
        self.assertEqual(proc.returncode, 3, proc)
        self.assertIn("claims", proc.stderr)
        self.assertEqual(self.cli_json("session", "claim", actor="spud", cwd=self.tool, session=SESSION)["claimed"], True)
        card = self.cli("session", "claim", actor="spud", cwd=self.tool, session=SESSION).stdout
        self.assertNotIn("ledger commit", card)
        self.assertEqual(self.cli_json("session", "show", cwd=self.tool, session=SESSION)["mode"], "spud")
        self.assertEqual(self.cli_json("session", "claim", actor="spud", cwd=self.home.path, session=SESSION)["claimed"], False)

    def test_a_ticket_created_in_the_home_is_project_spuds(self):
        t = self.cli_json("ticket", "new", "--title", "From the home", actor="spud", cwd=self.home.path)["ticket"]
        self.assertEqual((t["key"], t["project"]), ("SPD-001", "spud"))

    def test_the_subagent_start_context_names_the_tool_and_the_home_for_a_spud_ticket(self):
        self.home.env["CLAUDE_CODE_SESSION_ID"] = SESSION  # the row records the session that plans it, and the spawn is checked against it
        t = self.new_ticket("Context", status="active")
        m = self.new_member(t["key"], name="Russet", deliverable=["home:docs/x.md"])  # an unbound ticket; the bound one is test_ticket_worktree's
        common = {"session_id": SESSION, "transcript_path": "/tmp/x.jsonl", "cwd": str(self.home.path), "permission_mode": "default"}
        allow = self.home.hook("PreToolUse", dict(common, hook_event_name="PreToolUse", tool_name="Agent", tool_use_id="toolu_01",
                                                 tool_input={"description": "SPUD-001/Russet (01, scout)", "subagent_type": "spudagent", "model": "haiku", "prompt": "Do the thing."}))
        self.assertEqual(allow.decision, "allow", allow)
        r = self.home.hook("SubagentStart", dict(common, hook_event_name="SubagentStart", agent_id=AGENT, agent_type="spudagent"))
        self.assertIn("Your ticket's project is `spud`; bare deliverables are relative to `%s`" % self.tool, r.context)
        self.assertIn("`home:<glob>` Spud's home (%s)" % self.home.path, r.context)


class ProjectSpudTest(SplitCase):
    def test_the_key_home_is_reserved(self):
        other = self.make_repo("other-")
        proc = self.add_project(other, key="home", ticket_prefix="HOM", team_prefix="HOMS", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("reserved", proc.stderr)
        proc = self.add_project(other, key="spud", ticket_prefix="HOM", team_prefix="HOMS", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("exists already", proc.stderr)
        proc = self.add_project(self.home.path, key="vault", ticket_prefix="HOM", team_prefix="HOMS", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("Spud's home, which is not a project", proc.stderr)

    def test_project_spud_changes_sessions_and_root_but_keeps_its_name_and_prefixes(self):
        self.assertEqual(self.cli_json("project", "edit", "spud", "--sessions", "always", actor="spud")["changed"], ["sessions"])
        moved = self.make_repo("moved-")
        self.assertEqual(self.cli_json("project", "edit", "spud", "--root", moved, actor="spud")["project"]["root"], str(moved))
        for args, needle in ((["--name", "X"], "spud.config.json"), (["--ticket-prefix", "ZZ"], "spud.config.json")):
            with self.subTest(args=args):
                proc = self.cli("project", "edit", "spud", *args, actor="spud", check=False)
                self.assertEqual(proc.returncode, EXIT_ERROR, proc)
                self.assertIn(needle, proc.stderr)
        proc = self.cli("project", "remove", "spud", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("never removed", proc.stderr)

    def test_project_spud_installs_like_any_project(self):
        """Init installed project 1 (its step 7); uninstalled, it installs again as any project does."""
        local = self.tool / ".claude" / "settings.local.json"
        self.assertEqual(self.home.json("project", "show", "spud")["project"]["settings_file"], str(local))
        self.assertTrue(self.cli_json("project", "show", "spud")["project"]["installed"])
        self.assertEqual(self.cli_json("project", "uninstall", "spud", actor="spud")["changed"][:1], ["removed %s" % local])
        self.assertFalse(local.exists())
        out = self.cli_json("project", "install", "spud", actor="spud")
        self.assertIn(str(local), out["written"])
        data = json.loads(local.read_text(encoding="utf-8"))
        commands = [h["command"] for groups in data["hooks"].values() for g in groups for h in g["hooks"]]
        self.assertTrue(all(c.startswith("SPUD_HOME=%s " % self.home.path) and c.endswith(" --project spud") for c in commands), commands)
        self.assertIn(str(self.home.path), data["permissions"]["additionalDirectories"])
        self.assertTrue(self.cli_json("project", "show", "spud")["project"]["installed"])
        self.assertEqual(self.cli_json("project", "sync", "--all", actor="spud")["projects"], [{"project": "spud", "written": [], "restart": False}])
        report = self.cli_json("doctor")
        self.assertEqual(report["tool"], {"path": str(self.tool), "launcher": str(self.tool / "bin" / "spud"), "checkout": "main"})
        self.assertEqual([p["checks"] for p in report["projects"]], [["main checkout", "hooks", "ignored", "agent", "skill"]])


class WorktreeRootsTest(SplitCase):
    """SPD-097, who carries the generated roots.  Once the home is a directory of its own, `ledger/` in a worktree of the
    tool repository is an ordinary path: the vault is not checked out there any more, and only the home's own `ledger/`
    is generated.  The deliverable globs never follow the generated roots: a worktree of the tool is project spud's
    checkout, so a bare glob binds in it and a `home:` glob does not."""

    def reasons(self, tool, deliverable, cwd=None):
        """edit_reason for a member of a ticket in project spud, planned from `cwd`, as a function of the path it writes."""
        t = self.new_ticket("Worktrees", status="active")
        m = self.new_member(t["key"], name="Russet", deliverable=deliverable, cwd=cwd)
        ctx = spud.Ctx(self.home.path, "SPUD_HOME", False, tool=tool)
        con = spud.connect(ctx)
        self.addCleanup(con.close)
        row = con.execute("SELECT * FROM members WHERE id = ?", (m["id"],)).fetchone()
        return lambda path: spud.edit_reason(ctx, con, AGENT, row, str(path), str(tool))[0]

    def test_a_worktree_of_the_tool_holds_no_generated_roots_when_the_home_is_elsewhere(self):
        wt = self.add_worktree(self.tool, "spd-097")
        reason = self.reasons(self.tool, ["**", "home:docs/**"], cwd=wt)
        self.assertIsNone(reason(wt / "ledger" / "tickets" / "SPD-001.md"))  # ordinary code in project spud's checkout
        self.assertIsNone(reason(wt / "reports" / "2026-09-16.md"))
        self.assertIsNone(reason(wt / "bin" / "x.py"))
        self.assertIn("generated", reason(self.home.path / "ledger" / "tickets" / "SPD-001.md"))
        self.assertIn("generated", reason(self.home.path / "reports" / "2026-09-16.md"))

    def test_a_home_glob_does_not_bind_in_a_worktree_of_the_tool(self):
        wt = self.add_worktree(self.tool, "spd-097")
        reason = self.reasons(self.tool, ["home:**"])
        self.assertIn("Law 5", reason(wt / "bin" / "x.py"))
        self.assertIn("bin/x.py is not among", reason(wt / "bin" / "x.py"))  # the ticket's own project: the path is named bare
        self.assertIsNone(reason(self.home.path / "docs" / "x.md"))


if __name__ == "__main__":
    unittest.main()
