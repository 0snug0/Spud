"""The launcher (SPD-016).

A script is compiled on every run and a module's bytecode can be cached, so `bin/spud` is a few lines that load
the program, `bin/spud_ledger.py`, from the launcher's own real path, with its bytecode cached under the home's
`.spud/pycache/` (gitignored, and refused to edits like the database beside it).  The imports the hook path never
uses are made on first use, and `spud hook <event>` is dispatched without building the argument parser.  Every
run is against a scratch SPUD_HOME, or a scratch copy of the two files; nothing is written into the repository.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import EXIT_USAGE, REPO, SPUD, SpudTestCase

PROGRAM = REPO / "bin" / "spud_ledger.py"
SESSION = "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b"
# What bin/spud imported at the top before SPD-016 and no hook uses.
HOOK_UNUSED = {"argparse", "subprocess", "tempfile", "hashlib", "fractions"}


def cached_programs(root):
    return sorted(p for p in Path(root).rglob("*.pyc"))


class LauncherTest(SpudTestCase):
    def payloads(self):
        common = {"session_id": SESSION, "transcript_path": "/tmp/x.jsonl", "cwd": str(self.home.path), "permission_mode": "default"}
        return (
            ("PreToolUse", dict(common, hook_event_name="PreToolUse", tool_name="Bash", tool_use_id="toolu_1", tool_input={"command": "git status", "description": "s"})),
            ("SessionStart", dict(common, hook_event_name="SessionStart", source="startup", model="claude-opus-5")),
            ("Stop", dict(common, hook_event_name="Stop", stop_hook_active=False, last_assistant_message="Done.")),
            ("UserPromptSubmit", dict(common, hook_event_name="UserPromptSubmit", prompt="Work on SPD-001")),  # SPD-057: silent in the home
        )

    def test_the_launcher_is_small_and_the_program_is_a_module_beside_it(self):
        self.assertTrue(PROGRAM.is_file())
        self.assertLess(len(SPUD.read_text(encoding="utf-8").splitlines()), 80)
        self.assertNotIn("def main(", SPUD.read_text(encoding="utf-8"))

    def test_the_programs_bytecode_is_cached_under_the_homes_spud_directory(self):
        cache = self.home.path / ".spud" / "pycache"
        self.home.run("board")
        pycs = cached_programs(cache)
        self.assertEqual([p.name for p in pycs], ["spud_ledger.cpython-%d%d.pyc" % sys.version_info[:2]], pycs)
        # the cache mirrors the program's own path, so a worktree's program and the main checkout's never share a file
        self.assertEqual(pycs[0].parent, cache / str(PROGRAM.parent).lstrip("/"))
        stamp = pycs[0].stat().st_mtime_ns
        self.assertEqual(self.home.run("board").returncode, 0)
        self.assertEqual(pycs[0].stat().st_mtime_ns, stamp)  # read, not rewritten
        self.assertFalse((REPO / "bin" / "__pycache__" / pycs[0].name).exists())

    def test_the_hook_path_imports_nothing_it_does_not_use(self):
        for event, payload in self.payloads():
            proc = subprocess.run([sys.executable, "-I", "-S", "-X", "importtime", str(SPUD), "hook", event],
                                  input=json.dumps(payload), capture_output=True, text=True, env=self.home.env)
            self.assertEqual(proc.returncode, 0, proc)
            imported = {line.rsplit("|", 1)[-1].strip() for line in proc.stderr.splitlines() if line.startswith("import time:")}
            self.assertIn("json", imported, event)  # the flag took: the log is there
            self.assertEqual(imported & HOOK_UNUSED, set(), event)

    def test_the_hook_answers_the_same_through_the_launcher(self):
        for event, payload in self.payloads():
            r = self.home.hook(event, payload)
            self.assertEqual((r.code, r.stderr), (0, ""), (event, r))
            self.assertEqual(r.json is not None, event == "SessionStart", (event, r))  # the board brief; Bash and Stop stay silent

    def test_running_the_module_directly_refuses(self):
        proc = subprocess.run([sys.executable, "-I", "-S", str(PROGRAM), "--as", "spud", "board"], capture_output=True, text=True, env=self.home.env)
        self.assertEqual(proc.returncode, EXIT_USAGE, proc)
        self.assertIn("bin/spud", proc.stderr)
        self.assertEqual(proc.stdout, "")


class WithoutSpudHomeTest(unittest.TestCase):
    """SPUD_HOME unset: the cache goes under the launcher's own checkout's .spud/, and only when that directory exists.
    A scratch copy of bin/ runs `--version`, which touches no ledger."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="spud-launcher-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "bin").mkdir()
        for name in ("spud", "spud_ledger.py"):
            shutil.copy(REPO / "bin" / name, self.root / "bin" / name)
        self.env = {k: v for k, v in os.environ.items() if k != "SPUD_HOME"}

    def version(self):
        proc = subprocess.run([sys.executable, "-I", "-S", str(self.root / "bin" / "spud"), "--version"], capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc)
        self.assertTrue(proc.stdout.startswith("spud "), proc)

    def test_no_spud_directory_means_no_bytecode_anywhere(self):
        self.version()
        self.assertEqual(cached_programs(self.root), [])
        self.assertFalse((self.root / ".spud").exists())

    def test_the_checkouts_spud_directory_holds_the_cache(self):
        (self.root / ".spud").mkdir()
        self.version()
        self.assertEqual([p.name for p in cached_programs(self.root)], ["spud_ledger.cpython-%d%d.pyc" % sys.version_info[:2]])
        self.assertEqual(cached_programs(self.root / "bin"), [])


if __name__ == "__main__":
    unittest.main()
