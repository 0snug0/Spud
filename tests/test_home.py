"""SPD-097: the home is a plain directory and the tool is a repository.  Home resolution without git, the tool root and the
launcher every durable reference names, and the guard that keeps the suite off the real home."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import EXIT_ERROR, GUARD_HOME, REPO, SPUD, RepoMixin, SpudTestCase, load_spud_module

spud = load_spud_module()
SESSION = "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b"


class RealHomeGuardTest(unittest.TestCase):
    def test_the_test_process_names_a_home_that_does_not_exist(self):
        self.assertEqual(os.environ["SPUD_HOME"], GUARD_HOME)
        self.assertFalse(os.path.exists(GUARD_HOME))
        proc = subprocess.run([sys.executable, "-I", "-S", str(SPUD), "board"], capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        self.assertIn(GUARD_HOME, proc.stderr)


class HomeResolutionTest(unittest.TestCase):
    def test_spud_home_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, how = spud.resolve_home({"SPUD_HOME": tmp, "SPUD_CONFIG_DIR": tmp})
            self.assertEqual((home, how), (Path(tmp).resolve(), "SPUD_HOME"))

    def test_the_pointer_is_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config"
            config.mkdir()
            (config / "home").write_text("%s/the-home\n" % tmp, encoding="utf-8")
            home, how = spud.resolve_home({"SPUD_CONFIG_DIR": str(config)})
            self.assertEqual((home, how), ((Path(tmp) / "the-home").resolve(), "~/.config/spud/home"))

    def test_neither_names_both_and_git_is_never_asked(self):
        # this test runs inside a git checkout: before SPD-097 the fallback would have answered with its common dir
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(spud.SpudError) as caught:
                spud.resolve_home({"SPUD_CONFIG_DIR": tmp})
            self.assertIn("SPUD_HOME", caught.exception.message)
            self.assertIn(os.path.join(tmp, "home"), caught.exception.message)


class ToolRootTest(RepoMixin, unittest.TestCase):
    def test_the_tool_is_the_checkout_holding_the_package_unless_overridden(self):
        self.assertEqual(spud.tool_root({}), REPO)
        self.assertEqual(spud.tool_root({"SPUD_TOOL_DIR": "/tmp/../tmp/tool"}), Path("/tmp/tool"))

    def test_the_launcher_is_the_tools_bin_spud(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = spud.Ctx(Path(tmp), "SPUD_HOME", False, tool=Path(tmp) / "tool")
            self.assertEqual((ctx.tool, ctx.launcher), (Path(tmp) / "tool", Path(tmp) / "tool" / "bin" / "spud"))
            self.assertEqual(spud.Ctx(Path(tmp), "SPUD_HOME", False).tool, Path(os.environ["SPUD_TOOL_DIR"]))

    def test_checkout_kind(self):
        repo = self.make_repo("tool-")
        self.assertEqual(spud.tool_checkout_kind(repo), "main")
        self.assertEqual(spud.tool_checkout_kind(self.add_worktree(repo, "wt")), "worktree")
        self.assertEqual(spud.tool_checkout_kind(self.scratch_dir("plain-")), "none")


class SeparateToolTest(RepoMixin, SpudTestCase):
    """With SPUD_TOOL_DIR naming a checkout other than the home, every durable reference names the tool's bin/spud."""

    def setUp(self):
        super().setUp()
        self.tool = self.make_tool()
        self.home.env["SPUD_TOOL_DIR"] = str(self.tool)
        self.home.env["SPUD_LAUNCHCTL"] = "/usr/bin/false"
        self.home.env["SPUD_LAUNCH_AGENTS_DIR"] = str(self.scratch_dir("agents-"))
        self.launcher = str(self.tool / "bin" / "spud")

    def test_settings_sync_names_the_tools_launcher_and_the_home(self):
        out = self.home.json("settings", "sync", "--dry-run", actor="spud")
        commands = [h["command"] for groups in out["settings"]["hooks"].values() for g in groups for h in g["hooks"]]
        self.assertTrue(commands)
        for command in commands:
            self.assertTrue(command.startswith("SPUD_HOME=%s %s -I -S %s hook " % (self.home.path, sys.executable, self.launcher)), command)
        self.assertIn("Bash(python3.14 -I -S %s *)" % self.launcher, out["settings"]["permissions"]["allow"])
        self.assertNotIn("Bash(python3.14 -I -S %s/bin/spud *)" % self.home.path, out["settings"]["permissions"]["allow"])

    def test_settings_sync_warns_when_the_launcher_is_in_a_linked_worktree(self):
        wt = self.add_worktree(self.tool, "spd-999-x")
        self.home.env["SPUD_TOOL_DIR"] = str(wt)
        proc = self.home.run("settings", "sync", "--dry-run", actor="spud")
        self.assertIn("linked worktree", proc.stderr)
        self.home.env["SPUD_TOOL_DIR"] = str(self.tool)
        self.assertEqual(self.home.run("settings", "sync", "--dry-run", actor="spud").stderr, "")

    def test_the_plist_the_skill_and_the_agent_come_from_the_tool(self):
        plist = self.home.json("schedule", "show", actor="spud")["plist"]
        self.assertIn("<string>%s</string>" % self.launcher, plist)
        other = self.make_repo("badtakes-")
        self.add_project(other)
        self.cli("project", "install", "badtakes", actor="spud")
        skill = (self.home.path / ".user-claude" / "skills" / "spud" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("python3.14 -I -S %s --as spud session claim" % self.launcher, skill)
        self.assertIn("Read %s/CLAUDE.md in full" % self.home.path, skill)
        agent = (self.home.path / ".user-claude" / "agents" / "spudagent.md").read_text(encoding="utf-8")
        self.assertEqual(agent, (self.tool / ".claude" / "agents" / "spudagent.md").read_text(encoding="utf-8"))
        self.assertEqual(self.home.run("doctor").returncode, 0)

    def test_the_bash_hook_vouches_for_the_tools_launcher_not_the_homes(self):
        (self.home.path / "bin").mkdir(exist_ok=True)
        shutil.copyfile(SPUD, self.home.path / "bin" / "spud")

        def decision(script):
            payload = {"hook_event_name": "PreToolUse", "session_id": SESSION, "transcript_path": "/tmp/x.jsonl", "cwd": str(self.home.path),
                       "permission_mode": "default", "tool_name": "Bash", "tool_use_id": "t1",
                       "tool_input": {"command": "python3.14 -I -S %s --as spud board" % script}}
            return self.home.hook("PreToolUse", payload).decision

        self.assertEqual(decision(self.launcher), "allow")
        self.assertIsNone(decision(self.home.path / "bin" / "spud"))


if __name__ == "__main__":
    unittest.main()
