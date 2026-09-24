"""tests/probes/probe_env.py: the shape and the isolation every probe that builds a scratch SPUD_HOME shares (SPD-101, SPD-244).

On 2026-09-22 hook_timing.py's scratch home ran `spud init` against ~/Library/LaunchAgents and /bin/launchctl and replaced
this Mac's render watcher with its own.  These tests hold the probes' half of the fix: the helper sets what helpers.Home
sets, its launchctl refuses, it stops a probe whose environment still reaches the machine, and every probe that builds a
home uses it and skips init's step 8.  Nothing here reads or writes the real ~/Library/LaunchAgents or runs launchctl.

And since SPD-244 the shape: a probe's home has a tool checkout beside it, a main checkout that `spud init --project-root`
registers as project 1, as helpers.Home's has (SPD-233) -- never the home playing the tool, and never a row put in by SQL.
"""

import importlib.machinery
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO, SPUD

PROBES = REPO / "tests" / "probes"
# Every probe that builds a scratch SPUD_HOME and runs a `spud` against it.
HOME_BUILDERS = ("hook_timing.py", "session_diff.py", "render_timing.py", "headless.py", "headless_projects.py")


def load_probe_env():
    loader = importlib.machinery.SourceFileLoader("probe_env", str(PROBES / "probe_env.py"))
    spec = importlib.util.spec_from_loader("probe_env", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def git_lines(repo, *args):
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError("git %s in %s: %s" % (" ".join(args), repo, proc.stderr))
    return proc.stdout.splitlines()


class ProbeEnvCase(unittest.TestCase):
    def setUp(self):
        self.probe_env = load_probe_env()
        scratch = tempfile.TemporaryDirectory(prefix="spud-probe-env-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.tool = self.probe_env.tool_path(self.root)

    def spud(self, env, *args, check=True):
        proc = subprocess.run([sys.executable, "-I", "-S", str(SPUD), *[str(a) for a in args]], env=env, capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise AssertionError("spud %s exited %d: %s %s" % (" ".join(str(a) for a in args), proc.returncode, proc.stdout, proc.stderr))
        return proc

    def rows(self, sql):
        """The scratch ledger read directly, opened read-only: a `spud sql` run would reach modules the dependency table
        (tests/suite_deps.json) does not list for this test module."""
        con = sqlite3.connect("file:%s?mode=ro" % (self.home / ".spud" / "ledger.db"), uri=True)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql).fetchall()]
        finally:
            con.close()


class IsolatedEnvTest(ProbeEnvCase):
    def test_it_sets_what_helpers_home_sets_and_drops_the_session(self):
        base = {"PATH": "/usr/bin:/bin", "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_PROJECT_DIR": "/p", "CLAUDECODE": "1",
                "SPUD_HOME": "/Users/Nobody/SpudHome", "SPUD_TOOL_DIR": "/Users/Nobody/Spud"}
        env = self.probe_env.isolated_env(self.home, self.tool, base=base)
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        for name in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR", "CLAUDECODE"):
            self.assertNotIn(name, env)
        self.assertEqual(env["SPUD_HOME"], str(self.home))
        self.assertEqual(env["SPUD_TOOL_DIR"], str(self.tool))
        self.assertEqual(env["SPUD_CONFIG_DIR"], str(self.home / ".user-config"))
        self.assertEqual(env["SPUD_USER_CLAUDE_DIR"], str(self.home / ".user-claude"))
        self.assertEqual(env["SPUD_LAUNCH_AGENTS_DIR"], str(self.home / "LaunchAgents"))
        self.assertTrue(env["SPUD_LAUNCHCTL"].startswith(str(self.home) + os.sep))
        self.assertEqual((env["SPUD_GH"], env["SPUD_VAULT_DOWNLOADS"]), ("off", "off"))
        self.assertEqual(self.probe_env.machine_reach(env), [])

    def test_the_tool_is_named_never_defaulted_to_the_home(self):
        """SPD-244: the home is never the tool; a probe names the checkout beside it."""
        with self.assertRaises(TypeError):
            self.probe_env.isolated_env(self.home, base={})

    def test_scratch_moves_what_it_names(self):
        env = self.probe_env.isolated_env(self.home, self.tool, scratch=self.root, base={})
        self.assertEqual(env["SPUD_TOOL_DIR"], str(self.tool))
        self.assertEqual(env["SPUD_LAUNCH_AGENTS_DIR"], str(self.root / "LaunchAgents"))
        self.assertTrue(env["SPUD_LAUNCHCTL"].startswith(str(self.root) + os.sep))

    def test_its_launchctl_refuses_and_runs_nothing(self):
        env = self.probe_env.isolated_env(self.home, self.tool, base={})
        proc = subprocess.run([env["SPUD_LAUNCHCTL"], "bootout", "gui/%d/local.spud.render" % os.getuid()], capture_output=True, text=True)
        self.assertEqual(proc.returncode, self.probe_env.REFUSAL_EXIT)
        self.assertIn(self.probe_env.REFUSAL + " bootout gui/", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_an_environment_that_reaches_the_machine_stops_the_probe(self):
        reach = self.probe_env.machine_reach({})
        self.assertEqual(len(reach), 4)
        with self.assertRaises(SystemExit) as caught:
            self.probe_env.assert_isolated({})
        self.assertIn("nothing run", str(caught.exception.code))
        env = self.probe_env.isolated_env(self.home, self.tool, base={})
        for name, value in (("SPUD_LAUNCH_AGENTS_DIR", "~/Library/LaunchAgents"), ("SPUD_LAUNCHCTL", "/bin/launchctl"), ("SPUD_LAUNCH_AGENTS_DIR", "")):
            self.assertEqual(len(self.probe_env.machine_reach(dict(env, **{name: value}))), 1, (name, value))

    def test_init_under_it_writes_the_plists_into_the_scratch_and_is_refused_at_launchctl(self):
        """A probe that forgot --no-schedule: step 8 writes into the scratch's LaunchAgents and stops at the stub."""
        self.probe_env.write_config(self.home)
        self.probe_env.build_tool(self.root)
        env = self.probe_env.isolated_env(self.home, self.tool)
        args = [a for a in self.probe_env.init_args(self.tool) if a != "--no-schedule"]
        proc = self.spud(env, "--json", *args, check=False)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn(self.probe_env.REFUSAL, proc.stderr)
        self.assertTrue((self.home / "LaunchAgents" / "local.spud.backup.plist").is_file())
        self.assertEqual((self.home / ".user-config" / "home").read_text(encoding="utf-8").strip(), str(self.home))

    def test_write_config_renders_the_marks_and_the_suites_pool(self):
        config = self.probe_env.write_config(self.home, name_pool=True)
        text = (self.home / "spud.config.json").read_text(encoding="utf-8")
        self.assertNotIn("{{", text)
        self.assertEqual(json.loads(text), config)
        self.assertEqual(config["naming"]["pool"], json.loads((REPO / "tests" / "fixtures" / "name_pool.json").read_text(encoding="utf-8")))


class ProbeShapeTest(ProbeEnvCase):
    """SPD-244: the tool beside the home, and project 1 that tool as init registers it."""

    def test_build_tool_makes_a_main_checkout_beside_the_home(self):
        tool = self.probe_env.build_tool(self.root)
        self.assertEqual(tool, self.root / "tool" / "Spud")
        self.assertEqual(tool.parent.parent, self.home.parent)  # siblings under the scratch, neither inside the other
        git_dir, common = git_lines(tool, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir")
        self.assertEqual(os.path.realpath(git_dir), os.path.realpath(common))  # the main checkout, not a linked worktree
        self.assertEqual(len(git_lines(tool, "log", "--oneline", "main")), 1)
        self.assertEqual(git_lines(tool, "status", "--porcelain"), [])
        self.assertFalse((tool / "bin" / "spud").is_symlink())
        self.assertEqual((tool / "bin" / "spud").read_bytes(), (REPO / "bin" / "spud").read_bytes())
        self.assertTrue((tool / "bin" / "spudlib").is_dir())
        self.assertEqual(list(tool.joinpath("bin").rglob("__pycache__")), [])
        self.assertEqual(os.path.realpath(tool / "share"), os.path.realpath(REPO / "share"))
        self.assertEqual(git_lines(tool, "check-ignore", ".claude/settings.local.json"), [".claude/settings.local.json"])

    def test_build_tool_takes_another_checkouts_bin_and_share(self):
        other = self.root / "other"
        (other / "bin").mkdir(parents=True)
        (other / "bin" / "spud").write_text("# another checkout's launcher\n", encoding="utf-8")
        (other / "share").mkdir()
        tool = self.probe_env.build_tool(self.root, other)
        self.assertEqual((tool / "bin" / "spud").read_text(encoding="utf-8"), "# another checkout's launcher\n")
        self.assertEqual(os.path.realpath(tool / "share"), os.path.realpath(other / "share"))

    def test_a_probe_homes_project_1_is_the_tool_registered_by_init(self):
        """What every probe that builds a home does, end to end: project 1 is the tool checkout, rooted at it, and the row
        is init's own -- its project.added event from step 3, with the row's own creation time -- not one put in by SQL."""
        self.probe_env.write_config(self.home)
        tool = self.probe_env.build_tool(self.root)
        env = self.probe_env.isolated_env(self.home, tool)
        report = json.loads(self.spud(env, "--json", *self.probe_env.init_args(tool)).stdout)
        self.assertEqual(report["tool"], str(tool))
        projects = self.rows("SELECT id, key, name, root_path, created_at FROM projects")
        self.assertEqual(len(projects), 1, projects)
        project = projects[0]
        self.assertEqual((project["id"], project["key"], project["name"]), (1, "spud", "Spud"))
        self.assertEqual(os.path.realpath(project["root_path"]), os.path.realpath(tool))
        added = self.rows("SELECT actor, at, json_extract(data, '$.root') AS root FROM events WHERE kind = 'project.added'")
        self.assertEqual(added, [{"actor": "spud", "at": project["created_at"], "root": project["root_path"]}])

    def test_init_args_register_the_tool_and_skip_step_8(self):
        args = self.probe_env.init_args(self.tool)
        self.assertEqual(args[0], "init")
        self.assertIn("--no-schedule", args)
        self.assertEqual(args[args.index("--project-root") + 1], str(self.tool))
        self.assertEqual(args[args.index("--project-key") + 1], "spud")


class ProbesUseItTest(unittest.TestCase):
    def test_every_probe_that_builds_a_home_takes_the_shared_isolation(self):
        for name in HOME_BUILDERS:
            text = (PROBES / name).read_text(encoding="utf-8")
            self.assertIn("import probe_env", text, name)
            self.assertIn("sys.dont_write_bytecode = True", text, name)
            self.assertIn("probe_env.isolated_env(", text, name)

    def test_every_probe_that_builds_a_home_registers_its_tool_through_init(self):
        """SPD-244: the tool beside the home, and init's own step 3 -- never the home as the tool, never a row by SQL."""
        for name in HOME_BUILDERS:
            text = (PROBES / name).read_text(encoding="utf-8")
            self.assertIn("probe_env.build_tool(", text, name)
            self.assertIn("probe_env.init_args(", text, name)
        for path in sorted(PROBES.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("seed_project_one", text, path.name)
            self.assertIsNone(re.search(r"INSERT\s+INTO\s+projects", text, re.I), path.name)
            self.assertNotIn("link_share", text, path.name)

    def test_no_probe_runs_init_with_step_8(self):
        """A bare `init` would reach launchctl; every probe's own init skips step 8."""
        for path in sorted(PROBES.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"""["']init["']\s*[\)\]]""", text.replace('"git", "init"', "")), path.name)

    def test_any_probe_that_sets_spud_home_is_listed(self):
        """A new probe that builds a home joins HOME_BUILDERS, and with it the tests above."""
        for path in sorted(PROBES.glob("*.py")):
            if path.name == "probe_env.py":
                continue
            if re.search(r"SPUD_HOME\s*=|isolated_env\(", path.read_text(encoding="utf-8")):
                self.assertIn(path.name, HOME_BUILDERS)


if __name__ == "__main__":
    unittest.main()
