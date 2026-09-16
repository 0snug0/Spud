"""The module-sizes probe (SPD-080).

tests/probes/module_sizes.py is advisory: it reports the size of the application code and exits 0 whatever it finds.
Two things a reader trusts are pinned here — which files it reads (application code only, and never a test, whatever
path it is pointed at) and where the two bands fall — and so is the promise that makes it advisory: a file over the
refactor trigger still exits 0.  unittest does not collect the probe itself.
"""

import importlib.machinery
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO

PROBE = REPO / "tests" / "probes" / "module_sizes.py"


def load_probe():
    """Import the probe for unit tests, the way helpers.load_spud_module imports the program."""
    loader = importlib.machinery.SourceFileLoader("module_sizes", str(PROBE))
    spec = importlib.util.spec_from_loader("module_sizes", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


probe = load_probe()


def tree(root, files):
    """Write {relative path: text} under root, making directories as needed."""
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


class SelectionTest(unittest.TestCase):
    """Application code only, by Eric's scope (SPD-065): Python, never a test, never a stylesheet or a page."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="spud-module-sizes-")
        self.addCleanup(self.tmp.cleanup)
        self.root = tree(Path(self.tmp.name).resolve(), {
            "app.py": "a = 1\n",
            "pkg/deep.py": "b = 2\n",
            "runner": "#!/usr/bin/env python3\nc = 3\n",
            "data": "not python\n",
            "styles.css": "body { color: red }\n",
            "page.html": "<p>hi</p>\n",
            "README.md": "# hi\n",
            "test_app.py": "d = 4\n",
            "pkg/app_test.py": "e = 5\n",
            "tests/helper.py": "f = 6\n",
            "tests/probes/probe.py": "g = 7\n",
            "__pycache__/app.py": "h = 8\n",
            ".hidden/app.py": "i = 9\n",
        })

    def found(self, root):
        return sorted(str(Path(p).relative_to(self.root)) for p in probe.sources(root))

    def test_a_walk_reads_python_source_and_nothing_else(self):
        self.assertEqual(self.found(self.root), ["app.py", "pkg/deep.py", "runner"])

    def test_no_file_under_a_directory_named_tests_is_ever_read(self):
        self.assertEqual(self.found(self.root / "tests"), [])
        self.assertFalse(probe.is_application_code(self.root / "tests" / "probes" / "probe.py"))

    def test_a_file_named_for_a_test_is_not_application_code_wherever_it_sits(self):
        for name in ("test_app.py", "pkg/app_test.py"):
            self.assertFalse(probe.is_application_code(self.root / name), name)

    def test_a_file_named_directly_is_read_as_given(self):
        self.assertEqual(self.found(self.root / "app.py"), ["app.py"])
        self.assertEqual(self.found(self.root / "styles.css"), [])

    def test_a_python_file_is_its_extension_or_its_shebang(self):
        self.assertTrue(probe.is_python(str(self.root / "app.py")))
        self.assertTrue(probe.is_python(self.root / "runner"))  # a path object too; bin/spud has no extension
        self.assertFalse(probe.is_python(str(self.root / "data")))
        self.assertFalse(probe.is_python(str(self.root / "gone.py") + ".missing"))  # unreadable is not Python

    def test_the_package_is_what_the_probe_reports_on_by_default(self):
        found = {Path(p).name for p in probe.sources(probe.DEFAULT_ROOTS[0])}
        self.assertEqual(probe.DEFAULT_ROOTS, [str(REPO / "bin")])
        self.assertLessEqual({"spud", "spud_ledger.py", "kernel.py", "walk.py", "cliparser.py"}, found)

    def test_a_dot_directory_above_the_root_does_not_hide_a_checkout_from_itself(self):
        # A worktree lives at .claude/worktrees/<name>: the walk skips dot directories below the root, never above it.
        worktree = tree(Path(self.tmp.name).resolve() / ".claude" / "worktrees" / "w", {"bin/app.py": "a = 1\n"})
        self.assertEqual([Path(p).name for p in probe.sources(worktree)], ["app.py"])


class BandTest(unittest.TestCase):
    """~250 is the look-again point, 1000 the size that started SPD-065 and BAD-036; neither is a pass or a fail."""

    def test_the_two_bands_are_the_look_again_point_and_the_refactor_trigger(self):
        self.assertEqual((probe.LOOK_AGAIN, probe.TICKET), (250, 1000))
        self.assertEqual([floor for floor, _ in probe.BANDS], [1000, 250])

    def test_a_file_falls_in_the_band_it_reaches(self):
        for lines, floor in ((0, 0), (249, 0), (250, 250), (999, 250), (1000, 1000), (10663, 1000)):
            self.assertEqual(probe.band(lines), floor, lines)


class MeasureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="spud-module-sizes-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def measure(self, text):
        path = self.root / "m.py"
        path.write_text(text, encoding="utf-8")
        return probe.measure(str(path))

    def test_a_file_is_its_line_count_and_its_definitions_largest_first(self):
        lines, defs = self.measure("import os\n\n\nclass Big:\n" + "    x = 1\n" * 10 + "\n\ndef small():\n    return 1\n")
        self.assertEqual(lines, 18)
        self.assertEqual(defs, [("Big", 11), ("small", 2)])

    def test_a_constant_counts_as_a_definition_because_a_move_cannot_divide_one_either(self):
        _, defs = self.measure("DDL = '''\n" + "x\n" * 20 + "'''\n")
        self.assertEqual(defs, [("DDL", 22)])

    def test_a_decorator_counts_with_the_function_it_decorates(self):
        _, defs = self.measure("import functools\n\n\n@functools.lru_cache\ndef f():\n    return 1\n")
        self.assertEqual(defs, [("f", 3)])

    def test_a_file_that_does_not_parse_is_still_counted_and_said_so(self):
        lines, defs = self.measure("def broken(:\n    pass\n")
        self.assertEqual((lines, defs), (2, None))
        self.assertEqual(probe.largest(None, 2), "does not parse")

    def test_the_largest_definition_is_reported_with_its_share_of_the_file(self):
        self.assertIn("largest: f, 50 lines, 50% of the file; 2 definitions", probe.largest([("f", 50), ("g", 3)], 100))
        self.assertEqual(probe.largest([], 10), "no top-level definition")


class ReportTest(unittest.TestCase):
    """The probe is a report, never a gate: whatever it finds, it exits 0."""

    def run_probe(self, *args):
        proc = subprocess.run([sys.executable, "-I", "-S", str(PROBE), *args], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc)
        return proc.stdout

    def test_a_file_over_the_refactor_trigger_is_reported_and_still_exits_0(self):
        with tempfile.TemporaryDirectory(prefix="spud-module-sizes-") as tmp:
            root = tree(Path(tmp).resolve(), {"huge.py": "X = [\n" + "    1,\n" * 1200 + "]\n",
                                              "tests/test_big.py": "y = 1\n" * 2000,
                                              "small.py": "z = 1\n"})
            out = self.run_probe(str(root))
        self.assertIn("1000 and over — worth a ticket, not a quiet edit: 1 file", out)
        self.assertIn("huge.py", out)
        self.assertNotIn("test_big.py", out)  # a test is not application code, however large
        self.assertIn("2 files, 1203 lines.", out)
        self.assertIn("Nothing above is a failure.", out)

    def test_a_path_that_does_not_exist_is_said_and_is_not_an_error(self):
        out = self.run_probe("/nowhere/at/all")
        self.assertIn("skipped, no such path: /nowhere/at/all", out)

    def test_the_default_report_lists_the_package_and_only_files_in_a_band(self):
        out = self.run_probe()
        self.assertIn("advisory: a report, never a gate", out)
        self.assertIn("bin/spudlib/", out)
        listed = [line for line in out.splitlines() if line.startswith("    ") and line.split()[0].isdigit()]
        self.assertTrue(listed)
        for line in listed:
            self.assertGreaterEqual(int(line.split()[0]), probe.LOOK_AGAIN, line)


if __name__ == "__main__":
    unittest.main()
