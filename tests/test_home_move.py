"""SPD-097: `spud --as spud home move --to <dir>`: --dry-run, each precondition, and a full move between scratch directories
with the zero-write render.  SPD-233: the homes here are the suite's own, today's shape -- a home beside the tool checkout
that init registered as project spud -- and the transition window's scenarios (a home that was also the tool repository,
its tracked settings stripped by the move, and the rollback to it) went with the cutover they served."""

import json
import unittest

from helpers import EXIT_ERROR, LaunchdMixin, RepoMixin, SpudTestCase, load_spud_module

spud = load_spud_module()


def state_deny_rules(home):
    """SPD-033's Edit rules for a home's state directory, as test_install spells them (no scratch path needs escaping)."""
    return ["Edit(/%s/.spud)" % home, "Edit(/%s/.spud/**)" % home]


class PreconditionsTest(RepoMixin, SpudTestCase):
    """Every refusal comes before anything is written: no state renamed, nothing in the target, the pointer unchanged."""

    def setUp(self):
        super().setUp()
        self.target = self.scratch_dir("new-home-")
        self.pointer = self.home.path / ".user-config" / "home"
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), str(self.home.path))  # init wrote it (step 5)

    def move(self, *extra, check=False, actor="spud"):
        return self.cli("home", "move", "--to", self.target, *extra, actor=actor, check=check)

    def assertRefused(self, needle, *extra):
        proc = self.move(*extra)
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        self.assertIn(needle, proc.stderr)
        self.assertFalse((self.home.path / ".spud-moved").exists())
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), str(self.home.path))

    def test_dry_run_checks_and_prints_the_steps_and_moves_nothing(self):
        out = self.cli_json("home", "move", "--to", self.target, "--dry-run", actor="spud")
        self.assertEqual((out["dry_run"], out["to"], len(out["steps"])), (True, str(self.target), 9))
        self.assertIn(str(self.target / ".spud"), out["steps"][1])
        self.assertIn("set project spud's sessions to claim", out["steps"][4])
        self.assertIn("rename %s" % (self.home.path / ".spud"), out["steps"][7])
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertTrue((self.home.path / ".spud" / "ledger.db").is_file())

    def test_a_live_member_refuses(self):
        t = self.new_ticket("Live", status="active")
        self.new_member(t["key"], name="Yukon")
        self.assertRefused("planned or active (SPUD-001/Yukon)")

    def test_a_target_that_is_not_empty_or_is_inside_a_work_tree_or_the_home_refuses(self):
        (self.target / "x").write_text("x", encoding="utf-8")
        proc = self.move()
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        self.assertIn("is not an empty directory", proc.stderr)
        self.assertFalse((self.home.path / ".spud-moved").exists())
        (self.target / "x").unlink()
        proc = self.cli("home", "move", "--to", self.make_repo("repo-") / "sub", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("inside a git work tree", proc.stderr)
        proc = self.cli("home", "move", "--to", self.home.path / "vault", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("current home or inside it", proc.stderr)

    def test_a_hand_edit_refuses(self):
        self.new_ticket("Rendered", status="active")
        self.cli("render", actor="spud")
        path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        path.write_text(path.read_text(encoding="utf-8").replace("## Brief", "## Brief\nBy hand."), encoding="utf-8")
        self.assertRefused("hand-edited rendered file(s): ledger/tickets/SPD-001.md")

    def test_a_worktree_launcher_and_an_earlier_move_refuse(self):
        wt = self.add_worktree(self.home.tool, "spd-999-x")
        self.home.env["SPUD_TOOL_DIR"] = str(wt)
        self.assertRefused("linked worktree")
        self.home.env["SPUD_TOOL_DIR"] = str(self.home.tool)
        (self.home.path / ".spud-moved").mkdir()
        proc = self.move()
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("earlier move", proc.stderr)

    def test_only_spud_moves_the_home(self):
        t = self.new_ticket("Actor", status="active")
        m = self.new_member(t["key"], name="Kennebec")
        self.assertEqual(self.move(actor=m["ref"]).returncode, 3)


class ProjectlessMoveTest(LaunchdMixin, RepoMixin, SpudTestCase):
    """SPW-007: `home move` on a home with no project registered at all -- SPW-001 phase 2's legal, project-less home, the
    one `spud init --no-project` leaves.  Before the fix, step 5 (move_resync) subscripted the `None` row 1 and died
    with a TypeError, mid-procedure, after the backup and the database copy.  The fix chosen: the move proceeds, since a
    project-less home is legal and moving it is reasonable, and step 5a records that there was nothing to claim.  The
    general move -- the backup, the copy, the zero-write render, the pointer, the agents, the renamed state -- is this
    class's since the transition window's own full move went with it (SPD-233)."""

    seed_project = False  # the whole point: no project at all, not even one

    def setUp(self):
        super().setUp()
        home = self.home.path
        (home / "docs").mkdir(exist_ok=True)
        (home / "docs" / "note.md").write_text("a doc\n", encoding="utf-8")
        self.setup_launchd()
        self.cli("render", actor="spud")
        self.cli("schedule", "install", actor="spud")
        self.target = self.scratch_dir("new-home-")

    def move(self, *extra, check=False, actor="spud"):
        return self.cli("home", "move", "--to", self.target, *extra, actor=actor, check=check)

    def new_env(self):
        return {"SPUD_HOME": str(self.target)}

    def test_dry_run_says_nothing_to_claim(self):
        self.assertEqual(self.home.rows("SELECT * FROM projects"), [])
        out = self.cli_json("home", "move", "--to", self.target, "--dry-run", actor="spud")
        self.assertEqual((out["dry_run"], len(out["steps"])), (True, 9))
        self.assertIn("no project registered, so nothing to claim", out["steps"][4])
        self.assertNotIn("project spud", out["steps"][4])
        self.assertEqual(list(self.target.iterdir()), [])

    def test_the_move_completes_with_a_truthful_step_5a(self):
        old = self.home.path
        proc = self.move(check=True)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("5a. no project registered; nothing to claim", proc.stdout)
        # the old home: state renamed, as a completed move leaves it -- nothing crashed mid-procedure
        self.assertFalse((old / ".spud").exists())
        self.assertTrue((old / ".spud-moved" / "ledger.db").is_file())
        # the new home: the docs, the config, CLAUDE.md, the backups, the database, and the pointer naming it
        new = self.target
        self.assertEqual((new / "docs" / "note.md").read_text(encoding="utf-8"), "a doc\n")
        self.assertTrue((new / "CLAUDE.md").is_file() and (new / "spud.config.json").is_file())
        self.assertTrue(any(p.name.endswith("-pre-move.db") for p in (new / ".spud" / "backups").iterdir()))
        self.assertEqual((old / ".user-config" / "home").read_text(encoding="utf-8").strip(), str(new))
        for label in ("local.spud.backup", "local.spud.render"):
            self.assertIn("<string>%s</string>" % new, (self.agents / (label + ".plist")).read_text(encoding="utf-8"))
        # still no project registered, and it answers through SPUD_HOME with the move's own report entry
        self.assertEqual(json.loads(self.cli("--json", "project", "list", env=self.new_env()).stdout)["projects"], [])
        out = json.loads(self.cli("--json", "events", "--kind", "report.entry", env=self.new_env()).stdout)
        entry = out["events"][-1]
        self.assertEqual(entry["data"]["title"], "Home moved from %s to %s" % (old, self.target))
        self.assertIn("no project registered", entry["body"])
        # a render in the new home has nothing left to write
        self.assertEqual(json.loads(self.cli("--json", "render", actor="spud", env=self.new_env()).stdout)["written"], [])


class InstalledProjectsMoveTest(LaunchdMixin, RepoMixin, SpudTestCase):
    """The move of a home with projects installed (SPD-233 review F1): step 5 re-points every installed project's hook lines
    and permissions at the new home (move_resync, 5d: project 1 always, every other project that is installed) and makes
    project spud claim (5a).  Project 1 is the fixture's own, which init registered and installed; a second project shaped
    like BadTakes (`landing pr`) is added and installed beside it, and a third is added and never installed, which the move
    must leave alone.  Project spud starts at `always` so that 5a's change is one the move makes."""

    def setUp(self):
        super().setUp()
        self.setup_launchd()
        self.bad = self.make_repo("badtakes-")
        self.add_project(self.bad)
        self.cli("project", "install", "badtakes", actor="spud")
        self.bare = self.make_repo("bare-")
        self.add_project(self.bare, "bare", "BAR", "BARS")
        self.cli("project", "edit", "spud", "--sessions", "always", actor="spud")
        self.cli("render", actor="spud")
        self.cli("schedule", "install", actor="spud")
        self.target = self.scratch_dir("new-home-")
        self.locals = {"spud": self.home.tool / ".claude" / "settings.local.json", "badtakes": self.bad / ".claude" / "settings.local.json"}

    def new_env(self):
        return {"SPUD_HOME": str(self.target)}

    def local_settings(self, key):
        return json.loads(self.locals[key].read_text(encoding="utf-8"))

    def hook_commands(self, data):
        return [h["command"] for groups in data.get("hooks", {}).values() for g in groups for h in g["hooks"]]

    def test_the_move_points_every_installed_project_at_the_new_home_and_claims_project_spud(self):
        old, new = self.home.path, self.target
        launcher = self.home.launcher
        for key in self.locals:  # before: each names the old home, as install wrote it
            self.assertEqual(self.local_settings(key)["permissions"]["additionalDirectories"], [str(old)])
            self.assertTrue(all(c.startswith("SPUD_HOME=%s " % old) for c in self.hook_commands(self.local_settings(key))))
        proc = self.cli("home", "move", "--to", new, actor="spud")
        self.assertIn("5a. project spud: sessions claim", proc.stdout)
        self.assertIn("5d. project spud: ", proc.stdout)
        self.assertIn("5d. project badtakes: ", proc.stdout)
        self.assertNotIn("5d. project bare", proc.stdout)
        # 5a: project spud claims now, and the move says it changed it
        project = json.loads(self.cli("--json", "project", "show", "spud", env=self.new_env()).stdout)["project"]
        self.assertEqual((project["sessions"], project["root"], project["installed"]), ("claim", str(self.home.tool), True))
        edited = json.loads(self.cli("--json", "events", "--kind", "project.edited", env=self.new_env()).stdout)["events"][-1]
        self.assertEqual((edited["data"]["project"], edited["data"]["from"], edited["data"]["to"]), ("spud", {"sessions": "always"}, {"sessions": "claim"}))
        # 5d: each installed project's hook lines and permissions name the new home, and nothing names the old one
        for key in self.locals:
            with self.subTest(project=key):
                data = self.local_settings(key)
                commands = self.hook_commands(data)
                self.assertEqual(len(commands), 7)
                for command in commands:
                    self.assertTrue(command.startswith("SPUD_HOME=%s " % new), command)
                    self.assertTrue(command.endswith(" --project %s" % key), command)
                    self.assertIn(" -I -S %s hook " % launcher, command)
                # SPD-245: the home install added is replaced, not joined -- the old one would keep every session here
                # able to read and write it -- and the state directory's deny rules name the new home alone.
                self.assertEqual(data["permissions"]["additionalDirectories"], [str(new)])
                self.assertEqual([d for d in data["permissions"]["deny"] if ".spud" in d], state_deny_rules(new))
                self.assertIn("Bash(python3.14 -I -S %s *)" % launcher, data["permissions"]["allow"])
                self.assertFalse([line for line in commands + data["permissions"]["allow"] if str(old) in line])
        self.assertFalse((self.bare / ".claude" / "settings.local.json").exists())
        installed = json.loads(self.cli("--json", "events", "--kind", "project.installed", env=self.new_env()).stdout)["events"]
        self.assertEqual([e["data"]["project"] for e in installed if e["data"].get("sync")], ["spud", "badtakes"])
        # the user-scope skill and the new home's own settings follow it too
        skill = (old / ".user-claude" / "skills" / "spud" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("%s/CLAUDE.md" % new, skill)
        self.assertNotIn(str(old) + "/CLAUDE.md", skill)
        home_hooks = self.hook_commands(json.loads((new / ".claude" / "settings.json").read_text(encoding="utf-8")))
        self.assertTrue(home_hooks and all(c.startswith("SPUD_HOME=%s " % new) for c in home_hooks), home_hooks)
        # doctor in the new home finds every installation whole (the fake launchctl runs no watcher, as step 7b allows)
        doctor = json.loads(self.cli("--json", "doctor", env=self.new_env(), check=False).stdout)
        self.assertEqual([p for p in doctor["problems"] if not p.startswith("the render watcher ")], [])
        self.assertEqual([(p["key"], p["installed"], p["problems"]) for p in doctor["projects"]],
                         [("spud", True, []), ("badtakes", True, []), ("bare", False, [])])

    def test_the_move_keeps_the_users_own_directories_and_uninstall_after_it_leaves_neither_home(self):
        """SPD-245: badtakes' file holds directories of the user's own around install's entry -- one added after install,
        and one the file held before install first wrote it, in the order the user keeps them.  The move replaces install's
        entry where it stands and touches neither of the user's; an uninstall in the new home then takes the new home out
        and leaves no trace of the old one, the user's two still there."""
        old, new = self.home.path, self.target
        self.cli("project", "uninstall", "badtakes", actor="spud")
        before = str(self.scratch_dir("before-"))  # the user's, in the file before install ever wrote it
        path = self.locals["badtakes"]
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"permissions": {"additionalDirectories": [before]}}, indent=2) + "\n", encoding="utf-8")
        self.cli("project", "install", "badtakes", actor="spud")
        data = self.local_settings("badtakes")
        after = str(self.scratch_dir("after-"))  # the user's, added once install had written the file
        data["permissions"]["additionalDirectories"].append(after)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(self.local_settings("badtakes")["permissions"]["additionalDirectories"], [before, str(old), after])
        self.cli("render", actor="spud")  # the uninstall's and install's report entries, so the copied vault matches (3b)
        self.cli("home", "move", "--to", new, actor="spud")
        data = self.local_settings("badtakes")
        self.assertEqual(data["permissions"]["additionalDirectories"], [before, str(new), after])
        self.assertEqual([d for d in data["permissions"]["deny"] if ".spud" in d], state_deny_rules(new))
        self.assertEqual(json.loads(self.cli("--json", "project", "sync", "badtakes", actor="spud", env=self.new_env()).stdout)["projects"][0]["written"], [])
        self.cli("project", "uninstall", "badtakes", actor="spud", env=self.new_env())
        left = self.local_settings("badtakes")
        self.assertEqual(left, {"permissions": {"additionalDirectories": [before, after]}})
        self.cli("project", "uninstall", "spud", actor="spud", env=self.new_env())
        left = json.loads(self.locals["spud"].read_text(encoding="utf-8")) if self.locals["spud"].exists() else {}
        self.assertNotIn("additionalDirectories", left.get("permissions", {}))
        self.assertFalse([d for d in left.get("permissions", {}).get("deny", []) if ".spud" in d])


if __name__ == "__main__":
    unittest.main()
