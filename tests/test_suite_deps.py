"""tests/suite_trace.py and tests/suite_deps.py (SPD-233): what a traced process records, and the table its records make.

tests/suite.py's use of the table -- the `deps` rule of tests/suite_map.json -- is test_suite_runner.SelectTest's.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import suite
import suite_deps
import suite_trace
from helpers import LAUNCHER, REPO

TESTS = Path(__file__).resolve().parent
USED = "TABLE = (1, 2)\n\n\ndef run():\n    return TABLE\n\n\nclass Shape:\n    SIDES = 4\n"
LOADED = "def never():\n    return 1\n\n\nclass Idle:\n    pass\n"


def entry(path, text=""):
    return (path.encode(), "f", 0o644, text.encode())


def record(role, ran=(), opened=()):
    return {"role": role, "pid": 1, "argv": [], "ran": list(ran), "opened": list(opened)}


class TraceTest(unittest.TestCase):
    """A process traced into a directory records, at exit, the functions it ran under <repo>/bin and the files it opened
    under <repo>/bin and <repo>/share -- never a module or class body run at import, and never an import's own read."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="spud-trace-")).resolve()
        self.addCleanup(shutil.rmtree, self.repo, True)
        (self.repo / "bin" / "pkg").mkdir(parents=True)
        (self.repo / "bin" / "pkg" / "used.py").write_text(USED, encoding="utf-8")
        (self.repo / "bin" / "pkg" / "loaded.py").write_text(LOADED, encoding="utf-8")
        (self.repo / "share").mkdir()
        (self.repo / "share" / "note.txt").write_text("hi\n", encoding="utf-8")
        self.out = self.repo / "records"

    def traced(self, program, role="test"):
        """`program`, Python source, run in an interpreter of its own that starts the trace first; the one record it wrote."""
        head = ("import sys\nsys.dont_write_bytecode = True\nsys.path.insert(0, %r)\nimport suite_trace\nsuite_trace.start(%r, %r, %r)\n"
                % (str(TESTS), str(self.repo), str(self.out), role))
        proc = subprocess.run([sys.executable, "-I", "-S", "-c", head + program], capture_output=True, text=True, cwd=str(self.repo))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        records = sorted(self.out.glob("*.json"))
        self.assertEqual(len(records), 1, records)
        with open(records[0], encoding="utf-8") as f:
            return records[0].name, json.load(f)

    def load(self, name):
        return ("import importlib.util\nspec = importlib.util.spec_from_file_location(%r, %r)\n%s = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(%s)\n" % (name, str(self.repo / "bin" / "pkg" / (name + ".py")), name, name))

    def test_a_function_run_is_recorded_and_a_module_or_class_body_is_not(self):
        _, rec = self.traced(self.load("used") + self.load("loaded") + "used.run()\nused.Shape()\nloaded.Idle()\n")
        self.assertEqual(rec["ran"], ["bin/pkg/used.py"])
        self.assertEqual(rec["opened"], [])  # both sources were read to import them, and an import is not a read

    def test_a_file_opened_under_bin_or_share_is_recorded_and_one_elsewhere_is_not(self):
        other = self.repo / "elsewhere.txt"
        _, rec = self.traced("open(%r).read()\nopen('bin/pkg/loaded.py').read()\nopen(%r, 'w').close()\n"
                             % (str(self.repo / "share" / "note.txt"), str(other)))
        self.assertEqual((rec["opened"], rec["ran"]), (["bin/pkg/loaded.py", "share/note.txt"], []))

    def test_the_role_names_the_record(self):
        name, rec = self.traced("pass\n", role="fixture")
        self.assertEqual(rec["role"], "fixture")
        self.assertTrue(name.startswith("fixture-"), name)

    def test_the_shim_runs_the_launcher_traced_under_the_launchers_own_name(self):
        """argparse names the program after sys.argv[0], so the shim is a file called spud; what it records is this
        checkout's program: the entry and the parser, which `--help` builds before any home is read."""
        shim = suite_trace.write_shim(self.repo / "shim" / "bin" / "spud", REPO, self.out, LAUNCHER)
        self.assertEqual(shim.name, "spud")
        proc = subprocess.run([sys.executable, "-I", "-S", str(shim), "--help"], capture_output=True, text=True, env=dict(os.environ, COLUMNS="80"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.startswith("usage: spud"), proc.stdout[:80])
        [path] = self.out.glob("test-*.json")
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        self.assertIn("bin/spud_ledger.py", rec["ran"])
        self.assertIn("bin/spudlib/cli/cliparser.py", rec["ran"])
        self.assertNotIn("bin/spudlib/commands/membercmds.py", rec["ran"])  # the parser names its commands and runs none


class TableTest(unittest.TestCase):
    """suite_deps.table_of: the records of each module's traced run made into the table tests/suite.py reads."""

    FILES = [entry("bin/spud"), entry("bin/spudlib/a.py"), entry("bin/spudlib/b.py"), entry("share/x.md"),
             entry("tests/test_one.py"), entry("tests/test_two.py"), entry("tests/suite_deps.json", "{}"), entry("README.md")]

    def test_the_fixtures_files_leave_every_modules_entry_and_only_bin_and_share_count(self):
        results = [("test_one", [record("fixture", ran=["bin/spud", "bin/spudlib/a.py"]),
                                 record("test", ran=["bin/spudlib/a.py", "bin/spudlib/b.py"], opened=["share/x.md", "README.md"])], "OK", 0),
                   ("test_two", [record("fixture", ran=["bin/spudlib/a.py"]), record("test")], "FAILED (errors=1)", 1)]
        table = suite_deps.table_of(self.FILES, results)
        self.assertEqual(table["files"], ["bin/spud", "bin/spudlib/a.py", "bin/spudlib/b.py", "share/x.md"])
        self.assertEqual(table["fixture"], ["bin/spud", "bin/spudlib/a.py"])
        self.assertEqual(table["modules"], {"test_one": ["bin/spudlib/b.py", "share/x.md"], "test_two": []})
        self.assertEqual(table["failed"], {"test_two": "FAILED (errors=1)"})
        self.assertEqual(table["about"], suite_deps.ABOUT)

    def test_the_digest_leaves_the_table_itself_out_so_a_rerun_on_the_same_tree_names_it_alike(self):
        once = suite_deps.table_of(self.FILES, [])
        again = suite_deps.table_of([f if f[0] != b"tests/suite_deps.json" else entry("tests/suite_deps.json", json.dumps(once)) for f in self.FILES], [])
        self.assertEqual(once["tree"], again["tree"])
        self.assertEqual(once["tree"], suite.digest([f for f in self.FILES if f[0] != b"tests/suite_deps.json"]))

    def test_the_code_digest_covers_bin_and_share_alone(self):
        """What --changed compares to say the table is older than the program (SPD-233 review F3): a test module or a doc
        changed since leaves it alike, a file under bin/ or share/ does not."""
        table = suite_deps.table_of(self.FILES, [])
        self.assertEqual(table["code"], suite.digest([f for f in self.FILES if f[0].startswith((b"bin/", b"share/"))]))
        tests_moved = [f if f[0] not in (b"tests/test_one.py", b"README.md") else entry(f[0].decode(), "changed\n") for f in self.FILES]
        self.assertEqual(suite_deps.table_of(tests_moved, [])["code"], table["code"])
        for path in ("bin/spudlib/b.py", "share/x.md"):
            with self.subTest(path=path):
                moved = [f if f[0] != path.encode() else entry(path, "changed\n") for f in self.FILES]
                self.assertNotEqual(suite_deps.table_of(moved, [])["code"], table["code"])

    def test_a_measurement_of_named_modules_keeps_the_rest_and_drops_what_the_tree_lost(self):
        previous = {"fixture": ["bin/spudlib/a.py"], "modules": {"test_one": ["bin/spudlib/b.py"], "test_two": ["share/x.md"],
                                                                  "test_gone": ["bin/spudlib/b.py"]}, "failed": {"test_one": "FAILED"}}
        table = suite_deps.table_of(self.FILES, [("test_one", [record("test", ran=["bin/spudlib/a.py"])], "OK", 0)], previous)
        self.assertEqual(table["modules"], {"test_one": [], "test_two": ["share/x.md"]})
        self.assertNotIn("failed", table)


class ThisTreesTableTest(unittest.TestCase):
    """The table in this tree: data tests/suite.py can read, naming files and modules this tree has."""

    def test_it_is_well_formed_and_names_only_this_trees_files_and_modules(self):
        table = json.loads((TESTS / "suite_deps.json").read_text(encoding="utf-8"))
        self.assertEqual(set(table), {"about", "tree", "code", "files", "fixture", "modules"} | ({"failed"} if "failed" in table else set()))
        self.assertEqual(table["about"], suite_deps.ABOUT)
        checkout = TESTS.parent
        for path in table["files"]:
            self.assertTrue(path.startswith(suite_deps.ROOTS), path)
        self.assertLessEqual(set(table["fixture"]), set(table["files"]))
        for module, paths in table["modules"].items():
            with self.subTest(module=module):
                self.assertTrue((checkout / "tests" / (module + ".py")).is_file(), module)
                self.assertLessEqual(set(paths), set(table["files"]))
                self.assertFalse(set(paths) & set(table["fixture"]))
        self.assertIn("bin/spudlib/core/kernel.py", table["fixture"])  # what init runs is every test's


if __name__ == "__main__":
    unittest.main()
