"""SPD-097: `spud --as spud home move --to <dir>`: --dry-run, each precondition, a full move between scratch directories with
the zero-write render, and the rollback.  The scratch home is shaped like the transition window: the tool repository's main
checkout, with tracked settings written by settings sync, project spud rooted there, BadTakes installed."""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import EXIT_ERROR, REPO, SPUD, Home, LaunchdMixin, RepoMixin, SpudTestCase, git, load_spud_module

spud = load_spud_module()
TODAY_REPORT = "reports/%s.md" % spud.now()[:10]


def tree(home, *dirs, skip=()):
    """{path relative to home: bytes} of every file under each of `dirs` in `home`, the skipped relative paths left out."""
    out = {}
    for name in dirs:
        for p in sorted((Path(home) / name).rglob("*")):
            rel = str(p.relative_to(home))
            if p.is_file() and rel not in skip:
                out[rel] = p.read_bytes()
    return out


class HomeMoveCase(LaunchdMixin, RepoMixin, SpudTestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.cleanup)
        home = self.home.path
        shutil.copytree(REPO / "bin", home / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        # No spudagent source is copied: since SPW-004 it is share/agents/spudagent.md, and Home already gives this
        # home-as-tool the repository's share/.
        (home / ".gitignore").write_text(".spud/\n.user-claude/\n.user-config/\n.claude/settings.local.json\n", encoding="utf-8")
        (home / "docs").mkdir()
        (home / "docs" / "note.md").write_text("a doc\n", encoding="utf-8")
        (home / ".obsidian").mkdir()
        (home / ".obsidian" / "app.json").write_text("{}\n", encoding="utf-8")
        (home / "CLAUDE.md").write_text("You are Spud.\n", encoding="utf-8")
        self.home.write_settings({"model": "claude-fable-5-1", "permissions": {"allow": ["Bash(date:*)"]}})
        self.setup_launchd()
        self.home.init()
        self.cli("settings", "sync", actor="spud")
        git(home, "init", "-q", "-b", "main")
        git(home, "add", "-A")
        git(home, "commit", "-q", "-m", "home and tool")
        self.bad = self.make_repo("badtakes-")
        self.add_project(self.bad)
        self.cli("project", "install", "badtakes", actor="spud")
        t = self.new_ticket("Before the move", status="active")
        m = self.new_member(t["key"], name="Russet", deliverable=["bin/**"], cwd=self.add_worktree(home, "spd-001-before"))  # SPD-098: binds it
        self.cli("member", "start", m["ref"], actor="spud")  # planned -> active -> done: a member is finished from active
        self.cli("member", "finish", m["ref"], "--status", "done", "--outcome", "fine", "--summary", "Did the thing before the move.", actor="spud")
        self.cli("render", actor="spud")
        self.cli("schedule", "install", actor="spud")
        self.target = self.scratch_dir("new-home-")
        self.pointer = home / ".user-config" / "home"
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), str(home))  # project install wrote it, as it did on Eric's Mac

    def move(self, *extra, check=False, actor="spud"):
        return self.cli("home", "move", "--to", self.target, *extra, actor=actor, check=check)

    def new_env(self):
        """For RepoMixin.cli's `env=`: what makes a run address the new home."""
        return {"SPUD_HOME": str(self.target)}

    def board_through_the_pointer(self):
        """`spud board` with no SPUD_HOME in the environment: only the pointer under SPUD_CONFIG_DIR says where the home is."""
        env = dict(self.home.env)
        env.pop("SPUD_HOME")
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, "-I", "-S", str(SPUD), "board"], capture_output=True, text=True, env=env, cwd=str(self.home.path))

    def hook_commands(self, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return [h["command"] for groups in data.get("hooks", {}).values() for g in groups for h in g["hooks"] if "bin/spud hook" in h["command"]]


class PreconditionsTest(HomeMoveCase):
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
        self.assertIn("rename %s" % (self.home.path / ".spud"), out["steps"][7])
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertTrue((self.home.path / ".spud" / "ledger.db").is_file())

    def test_a_live_member_refuses(self):
        t = self.new_ticket("Live", status="active")
        self.new_member(t["key"], name="Yukon")
        self.assertRefused("planned or active (SPUD-002/Yukon)")

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
        path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        path.write_text(path.read_text(encoding="utf-8").replace("## Brief", "## Brief\nBy hand."), encoding="utf-8")
        self.assertRefused("hand-edited rendered file(s): ledger/tickets/SPD-001.md")

    def test_a_worktree_launcher_and_an_earlier_move_refuse(self):
        wt = self.add_worktree(self.home.path, "spd-999-x")
        self.home.env["SPUD_TOOL_DIR"] = str(wt)
        self.assertRefused("linked worktree")
        self.home.env["SPUD_TOOL_DIR"] = str(self.home.path)
        (self.home.path / ".spud-moved").mkdir()
        proc = self.move()
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("earlier move", proc.stderr)

    def test_only_spud_moves_the_home(self):
        t = self.new_ticket("Actor", status="active")
        m = self.new_member(t["key"], name="Kennebec")
        self.assertEqual(self.move(actor=m["ref"]).returncode, 3)


class FullMoveTest(HomeMoveCase):
    def test_the_move_and_what_it_leaves(self):
        old = self.home.path
        before = tree(old, "ledger", "reports")
        events_before = self.home.scalar("SELECT count(*) FROM events")
        proc = self.move(check=True)
        out = json.loads(self.cli("--json", "events", "--kind", "report.entry", env=self.new_env()).stdout)
        # the old home: state renamed, vault untouched
        self.assertFalse((old / ".spud").exists())
        self.assertTrue((old / ".spud-moved" / "ledger.db").is_file())
        self.assertEqual(tree(old, "ledger", "reports"), before)
        self.assertIn("end this session", proc.stdout)
        self.assertIn("removal commit", proc.stdout)
        # the new home: the vault, the docs, the config, CLAUDE.md, the backups, the database
        new = self.target
        self.assertEqual(tree(new, "ledger", "reports", skip=("ledger/Projects.md", TODAY_REPORT)),
                         {k: v for k, v in before.items() if k not in ("ledger/Projects.md", TODAY_REPORT)})
        self.assertEqual((new / "docs" / "note.md").read_text(encoding="utf-8"), "a doc\n")
        self.assertTrue((new / ".obsidian" / "app.json").is_file() and (new / "CLAUDE.md").is_file() and (new / "spud.config.json").is_file())
        self.assertTrue(any(p.name.endswith("-pre-move.db") for p in (new / ".spud" / "backups").iterdir()))
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), str(new))
        # the database: every row, plus the move's own events; project spud claims now
        counts = json.loads(self.cli("--json", "sql", "--readonly", "select count(*) as n from events", env=self.new_env()).stdout)["rows"][0]["n"]
        self.assertGreaterEqual(counts, events_before + 5)  # project.edited, report.entry, config.synced, project.installed x2
        self.assertEqual([e["data"]["title"] for e in out["events"]][-1], "Home moved from %s to %s" % (old, new))
        project = json.loads(self.cli("--json", "project", "show", "spud", env=self.new_env()).stdout)["project"]
        self.assertEqual((project["sessions"], project["root"], project["installed"]), ("claim", str(old), True))
        # the settings: the new home's own, the tool's stripped tracked file, the tool's and BadTakes' local files
        commands = self.hook_commands(new / ".claude" / "settings.json")
        self.assertEqual(len(commands), 7)
        self.assertTrue(all(c.startswith("SPUD_HOME=%s " % new) and " %s/bin/spud hook " % old in c for c in commands), commands)
        self.assertEqual(json.loads((new / ".claude" / "settings.json").read_text(encoding="utf-8"))["model"], "claude-fable-5-1")
        tracked = json.loads((old / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(tracked, {"model": "claude-fable-5-1", "permissions": {"allow": ["Bash(date:*)"]}})
        self.assertIn(" M .claude/settings.json", git(old, "status", "--porcelain"))
        local = self.hook_commands(old / ".claude" / "settings.local.json")
        self.assertEqual(len(local), 7)
        self.assertTrue(all(c.startswith("SPUD_HOME=%s " % new) and c.endswith(" --project spud") for c in local), local)
        self.assertTrue(all(c.startswith("SPUD_HOME=%s " % new) for c in self.hook_commands(self.bad / ".claude" / "settings.local.json")))
        skill = (old / ".user-claude" / "skills" / "spud" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Read %s/CLAUDE.md in full" % new, skill)
        # the agents name the new home; the calls: two pairs before the move, two pairs by it
        for label in ("local.spud.backup", "local.spud.render"):
            self.assertIn("<key>SPUD_HOME</key>", (self.agents / (label + ".plist")).read_text(encoding="utf-8"))
            self.assertIn("<string>%s</string>" % new, (self.agents / (label + ".plist")).read_text(encoding="utf-8"))
        self.assertEqual([c[0] for c in self.launchctl_calls()], ["bootout", "bootstrap"] * 4)
        # the new home answers through the pointer alone; the old one has no ledger any more
        proc = self.board_through_the_pointer()
        self.assertEqual(proc.returncode, 0, proc)
        self.assertIn("SPD-001", proc.stdout)
        proc = self.cli("board", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("no ledger at", proc.stderr)
        # a render in the new home has nothing left to write
        self.assertEqual(json.loads(self.cli("--json", "render", actor="spud", env=self.new_env()).stdout)["written"], [])

    def test_the_rollback(self):
        old = self.home.path
        self.move(check=True)
        self.pointer.write_text(str(old) + "\n", encoding="utf-8")
        os.rename(old / ".spud-moved", old / ".spud")
        self.cli("settings", "sync", actor="spud")
        self.cli("project", "install", "badtakes", actor="spud")
        self.cli("schedule", "install", actor="spud")
        self.assertIn("SPD-001", self.board_through_the_pointer().stdout)
        self.assertTrue(all(c.startswith("SPUD_HOME=%s " % old) for c in self.hook_commands(old / ".claude" / "settings.json")))
        self.assertTrue(all(c.startswith("SPUD_HOME=%s " % old) for c in self.hook_commands(self.bad / ".claude" / "settings.local.json")))
        self.assertIn("<string>%s</string>" % old, (self.agents / "local.spud.render.plist").read_text(encoding="utf-8"))
        self.assertEqual(self.home.json("project", "show", "spud")["project"]["sessions"], "always")  # the old database never changed


class ProjectlessMoveTest(LaunchdMixin, RepoMixin, SpudTestCase):
    """SPW-007: `home move` on a home with no project registered at all -- SPW-001 phase 2's legal, project-less home, the
    one `spud init --no-project` will leave.  Before the fix, step 5 (move_resync) subscripted the `None` row 1 and died
    with a TypeError, mid-procedure, after the backup and the database copy.  The fix chosen: the move proceeds, since a
    project-less home is legal and moving it is reasonable, and step 5a records that there was nothing to claim."""

    seed_project = False  # the whole point: no project at all, not even one

    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.cleanup)
        home = self.home.path
        shutil.copytree(REPO / "bin", home / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        (home / ".gitignore").write_text(".spud/\n.user-claude/\n.user-config/\n.claude/settings.local.json\n", encoding="utf-8")
        (home / "docs").mkdir()
        (home / "docs" / "note.md").write_text("a doc\n", encoding="utf-8")
        (home / ".obsidian").mkdir()
        (home / ".obsidian" / "app.json").write_text("{}\n", encoding="utf-8")
        (home / "CLAUDE.md").write_text("You are Spud.\n", encoding="utf-8")
        self.home.write_settings({"model": "claude-fable-5-1"})
        self.setup_launchd()
        self.home.init(project=False)
        self.cli("settings", "sync", actor="spud")
        git(home, "init", "-q", "-b", "main")
        git(home, "add", "-A")
        git(home, "commit", "-q", "-m", "home and tool")
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
        # the new home: still no project registered, and it answers through SPUD_HOME with the move's own report entry
        self.assertEqual(json.loads(self.cli("--json", "project", "list", env=self.new_env()).stdout)["projects"], [])
        out = json.loads(self.cli("--json", "events", "--kind", "report.entry", env=self.new_env()).stdout)
        entry = out["events"][-1]
        self.assertEqual(entry["data"]["title"], "Home moved from %s to %s" % (old, self.target))
        self.assertIn("no project registered", entry["body"])


if __name__ == "__main__":
    unittest.main()
