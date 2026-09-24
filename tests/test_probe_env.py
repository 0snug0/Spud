"""tests/probes/probe_env.py: the isolation every probe that builds a scratch SPUD_HOME shares (SPD-101).

On 2026-09-22 hook_timing.py's scratch home ran `spud init` against ~/Library/LaunchAgents and /bin/launchctl and replaced
this Mac's render watcher with its own.  These tests hold the probes' half of the fix: the helper sets what helpers.Home
sets, its launchctl refuses, it stops a probe whose environment still reaches the machine, and every probe that builds a
home uses it and skips init's step 8.  Nothing here reads or writes the real ~/Library/LaunchAgents or runs launchctl.
"""

import importlib.machinery
import importlib.util
import json
import os
import re
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


class IsolatedEnvTest(unittest.TestCase):
    def setUp(self):
        self.probe_env = load_probe_env()
        scratch = tempfile.TemporaryDirectory(prefix="spud-probe-env-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()

    def test_it_sets_what_helpers_home_sets_and_drops_the_session(self):
        base = {"PATH": "/usr/bin:/bin", "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_PROJECT_DIR": "/p", "CLAUDECODE": "1",
                "SPUD_HOME": "/Users/Nobody/SpudHome"}
        env = self.probe_env.isolated_env(self.home, base=base)
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        for name in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR", "CLAUDECODE"):
            self.assertNotIn(name, env)
        self.assertEqual(env["SPUD_HOME"], str(self.home))
        self.assertEqual(env["SPUD_TOOL_DIR"], str(self.home))
        self.assertEqual(env["SPUD_CONFIG_DIR"], str(self.home / ".user-config"))
        self.assertEqual(env["SPUD_USER_CLAUDE_DIR"], str(self.home / ".user-claude"))
        self.assertEqual(env["SPUD_LAUNCH_AGENTS_DIR"], str(self.home / "LaunchAgents"))
        self.assertTrue(env["SPUD_LAUNCHCTL"].startswith(str(self.home) + os.sep))
        self.assertEqual((env["SPUD_GH"], env["SPUD_VAULT_DOWNLOADS"]), ("off", "off"))
        self.assertEqual(self.probe_env.machine_reach(env), [])

    def test_scratch_and_tool_move_what_they_name(self):
        env = self.probe_env.isolated_env(self.home, scratch=self.root, tool=self.root / "tool", base={})
        self.assertEqual(env["SPUD_TOOL_DIR"], str(self.root / "tool"))
        self.assertEqual(env["SPUD_LAUNCH_AGENTS_DIR"], str(self.root / "LaunchAgents"))
        self.assertTrue(env["SPUD_LAUNCHCTL"].startswith(str(self.root) + os.sep))

    def test_its_launchctl_refuses_and_runs_nothing(self):
        env = self.probe_env.isolated_env(self.home, base={})
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
        env = self.probe_env.isolated_env(self.home, base={})
        for name, value in (("SPUD_LAUNCH_AGENTS_DIR", "~/Library/LaunchAgents"), ("SPUD_LAUNCHCTL", "/bin/launchctl"), ("SPUD_LAUNCH_AGENTS_DIR", "")):
            self.assertEqual(len(self.probe_env.machine_reach(dict(env, **{name: value}))), 1, (name, value))

    def test_init_under_it_writes_the_plists_into_the_scratch_and_is_refused_at_launchctl(self):
        """A probe that forgot --no-schedule: step 8 writes into the scratch's LaunchAgents and stops at the stub."""
        self.probe_env.write_config(self.home)
        self.probe_env.link_share(self.home)
        env = self.probe_env.isolated_env(self.home)
        proc = subprocess.run([sys.executable, "-I", "-S", str(SPUD), "--json", "init"], env=env, capture_output=True, text=True)
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


class ProbesUseItTest(unittest.TestCase):
    def test_every_probe_that_builds_a_home_takes_the_shared_isolation(self):
        for name in HOME_BUILDERS:
            text = (PROBES / name).read_text(encoding="utf-8")
            self.assertIn("import probe_env", text, name)
            self.assertIn("sys.dont_write_bytecode = True", text, name)
            self.assertIn("probe_env.isolated_env(", text, name)

    def test_no_probe_runs_init_with_step_8(self):
        """A bare `init` would reach launchctl; every probe's own init skips step 8."""
        for path in sorted(PROBES.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"""["']init["']\s*[\)\]]""", text.replace('"git", "init"', "")), path.name)

    def test_any_probe_that_sets_spud_home_is_listed(self):
        """A new probe that builds a home joins HOME_BUILDERS, and with it the two tests above."""
        for path in sorted(PROBES.glob("*.py")):
            if path.name == "probe_env.py":
                continue
            if re.search(r"SPUD_HOME\s*=|isolated_env\(", path.read_text(encoding="utf-8")):
                self.assertIn(path.name, HOME_BUILDERS)


if __name__ == "__main__":
    unittest.main()
