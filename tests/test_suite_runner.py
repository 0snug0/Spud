"""tests/suite.py's own guarantees (SPD-232): one run per machine, --no-wait, --background, and cleanup after a killed run.

Most of these start a real nested tests/suite.py -- the snapshot's, over the snapshot this run is running in -- as a tiny
named run of `Target` below (`--cold -j 1`), in a directory of the test's own: TMPDIR names its `tmp/`, where the nested
run makes and sweeps its scratch, and SPUD_SUITE_LOCK names a lock file there too.  That is how a test inside a run
holding the machine's lock starts another run without waiting on its own parent: a nested run's lock is the one its
environment names, and a run passes SPUD_SUITE_LOCK to none of its workers while pointing their TMPDIR into its own
scratch directory, so even a nested run that names no lock locks inside its parent's run (`Target` checks both).

The classes from `MapTest` on cover `--changed [BASE]` (SPD-234): the selection by tests/suite_map.json, driven directly
over trees built here, the final line's wording, the changed paths git lists in a scratch repository, and one real
nested run over such a repository with a copy of this runner in it.
"""

import contextlib
import fnmatch
import io
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import suite
from helpers import git, isolated_git_env, wall_clock

TESTS = Path(__file__).resolve().parent
SUITE = TESTS / "suite.py"
MAP = TESTS / "suite_map.json"
TARGET_ENV = "SPUD_SUITE_TARGET"  # the directory a nested run's Target writes its reports into
DARWIN_BG = hasattr(os, "PRIO_DARWIN_BG") and hasattr(os, "PRIO_DARWIN_PROCESS")
PRIORITY = ("import os; print(os.getpriority(os.PRIO_DARWIN_PROCESS, 0) if hasattr(os, 'PRIO_DARWIN_PROCESS') else -1)")


def priority():
    """This process's darwin background state: 1 at background priority, 0 at normal, -1 where there is none."""
    return os.getpriority(os.PRIO_DARWIN_PROCESS, 0) if DARWIN_BG else -1


def dead_pid():
    """The pid of a process that has exited and been reaped."""
    proc = subprocess.Popen([sys.executable, "-I", "-S", "-c", "pass"])
    proc.wait()
    return proc.pid


def scratch_dirs(directory):
    return sorted(p.name for p in Path(directory).iterdir() if p.name.startswith(suite.SCRATCH_PREFIX) and p.is_dir())


class Target(unittest.TestCase):
    """What the nested runs below run.  With $SPUD_SUITE_TARGET set, each test writes <that dir>/<its name>.json: its pid,
    its priority and its child's, the temp directory it sees and a directory it made there; with <that dir>/sleep present
    it then sleeps until its parent is gone, <that dir>/go appears or two minutes pass.  In any other run of tests/suite.py it checks what every
    worker is given, which is why a nested run cannot wait on its parent's lock: a temp directory inside the run's own
    scratch directory, and no SPUD_SUITE_LOCK.  Under the serial command there is no run around it to check."""

    def report(self):
        out = os.environ.get(TARGET_ENV)
        if out is None:
            return False
        made = tempfile.mkdtemp(prefix="spud-test-")
        child = subprocess.run([sys.executable, "-I", "-S", "-c", PRIORITY], capture_output=True, text=True, check=True)
        Path(out, self._testMethodName + ".json").write_text(json.dumps({
            "pid": os.getpid(), "priority": priority(), "child_priority": int(child.stdout), "tmp": tempfile.gettempdir(),
            "made": made}), encoding="utf-8")
        if Path(out, "sleep").exists():
            parent, deadline = os.getppid(), time.monotonic() + 120
            while os.getppid() == parent and not Path(out, "go").exists() and time.monotonic() < deadline:
                time.sleep(0.1)
        return True

    def check_run(self):
        if os.environ.get(suite.PYCACHE_ENV) is None:
            return  # the serial command
        tmp = tempfile.gettempdir()
        scratch = os.path.dirname(tmp)
        self.assertTrue(os.path.basename(scratch).startswith(suite.SCRATCH_PREFIX), tmp)
        self.assertEqual(Path(scratch, suite.OWNER).read_text(encoding="utf-8").strip(), str(os.getppid()))
        self.assertNotIn(suite.LOCK_ENV, os.environ)
        self.assertEqual(suite.lock_path(), os.path.join(tmp, suite.LOCK_NAME))

    def test_target(self):
        if not self.report():
            self.check_run()

    @wall_clock
    def test_timed_target(self):
        if not self.report():
            self.check_run()


class NestedRun(unittest.TestCase):
    """A directory of the test's own for nested runs of Target: tmp/ (their TMPDIR), out/ (Target's reports), a lock."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="spud-nested-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.tmp, self.out, self.lock = self.dir / "tmp", self.dir / "out", self.dir / "suite.lock"
        self.tmp.mkdir()
        self.out.mkdir()

    def env(self, lock=True):
        env = dict(os.environ)
        env.pop(suite.PYCACHE_ENV, None)
        env["TMPDIR"] = str(self.tmp)
        env[TARGET_ENV] = str(self.out)
        if lock:
            env[suite.LOCK_ENV] = str(self.lock)
        else:
            env.pop(suite.LOCK_ENV, None)
        return env

    def start(self, *args, names=("test_suite_runner.Target.test_target",), env=None):
        proc = subprocess.Popen([sys.executable, "-I", "-S", str(SUITE), "--cold", "-j", "1", *args, *names],
                                env=env or self.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def stop():
            if proc.poll() is None:
                proc.kill()
            proc.communicate()

        self.addCleanup(stop)
        return proc

    def run_nested(self, *args, **kw):
        proc = self.start(*args, **kw)
        out, err = proc.communicate(timeout=120)
        return proc.returncode, out, err

    def first_line(self, stream, timeout=30):
        """The first line a pipe carries, read without blocking past `timeout`."""
        sel = selectors.DefaultSelector()
        sel.register(stream, selectors.EVENT_READ)
        data, deadline = b"", time.monotonic() + timeout
        fd = stream.fileno()
        while b"\n" not in data and time.monotonic() < deadline:
            if sel.select(timeout=max(0, deadline - time.monotonic())):
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                data += chunk
        sel.close()
        return data.decode("utf-8", "replace").split("\n")[0]

    def report(self, name="test_target", timeout=60):
        path, deadline = self.out / (name + ".json"), time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(0.05)
        self.fail("%s never reported" % name)


class LockTest(unittest.TestCase):
    """The lock itself, driven directly: flock locks belong to an open file, so two opens in one process contend."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="spud-lock-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = str(self.dir / "suite.lock")

    def test_a_free_lock_is_taken_and_names_its_run_until_released(self):
        held = suite.acquire(self.path, say=self.fail)
        holder = suite.read_holder(self.path)
        self.assertEqual((holder["pid"], holder["checkout"]), (os.getpid(), str(suite.CHECKOUT)))
        self.assertTrue(holder["started"])
        suite.release(held)
        self.assertIsNone(suite.read_holder(self.path))
        suite.release(suite.acquire(self.path, wait=False, say=self.fail))  # free again

    def test_no_wait_refuses_at_once_and_says_whose_run_holds_it(self):
        held = suite.acquire(self.path)
        self.addCleanup(suite.release, held)
        said = []
        started = time.monotonic()
        self.assertIsNone(suite.acquire(self.path, wait=False, say=said.append))
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(len(said), 1, said)
        self.assertIn("--no-wait", said[0])
        self.assertIn("pid %d in %s" % (os.getpid(), suite.CHECKOUT), said[0])

    def test_a_second_run_waits_says_for_whom_and_starts_when_the_first_ends(self):
        held = suite.acquire(self.path)
        said, got = [], []
        waiter = threading.Thread(target=lambda: got.append(suite.acquire(self.path, say=said.append, poll=0.02)))
        waiter.start()
        time.sleep(0.3)
        self.assertTrue(waiter.is_alive())
        self.assertEqual(got, [])
        self.assertEqual(len(said), 1, said)
        self.assertTrue(said[0].startswith("suite: waiting for the run of pid %d in %s, started " % (os.getpid(), suite.CHECKOUT)), said)
        suite.release(held)
        waiter.join(10)
        self.assertFalse(waiter.is_alive())
        self.assertIsNotNone(got[0])
        suite.release(got[0])

    def test_the_default_lock_is_in_the_temp_directory_and_the_environment_can_name_another(self):
        env = os.environ.pop(suite.LOCK_ENV, None)
        try:
            self.assertEqual(suite.lock_path(), os.path.join(tempfile.gettempdir(), "spud-suite.lock"))
            os.environ[suite.LOCK_ENV] = self.path
            self.assertEqual(suite.lock_path(), self.path)
        finally:
            os.environ.pop(suite.LOCK_ENV, None)
            if env is not None:
                os.environ[suite.LOCK_ENV] = env


class NestedLockTest(NestedRun):
    """A real second run: it queues behind the lock's holder, or with --no-wait refuses; a run inside a run does neither."""

    def test_no_wait_exits_75_with_one_line_while_another_run_holds_the_lock(self):
        held = suite.acquire(str(self.lock))
        self.addCleanup(suite.release, held)
        code, out, err = self.run_nested("--no-wait")
        self.assertEqual(code, suite.EXIT_BUSY, err)
        self.assertEqual(out, "")
        self.assertEqual(len(err.splitlines()), 1, err)
        self.assertIn("pid %d" % os.getpid(), err)
        self.assertEqual(scratch_dirs(self.tmp), [])
        self.assertFalse((self.out / "test_target.json").exists())

    def test_a_run_waits_for_the_holder_says_whose_run_and_then_runs(self):
        held = suite.acquire(str(self.lock))
        proc = self.start()
        line = self.first_line(proc.stderr)
        self.assertTrue(line.startswith("suite: waiting for the run of pid %d in %s, started " % (os.getpid(), suite.CHECKOUT)), line)
        time.sleep(0.5)
        self.assertIsNone(proc.poll())
        self.assertFalse((self.out / "test_target.json").exists())
        suite.release(held)
        out, err = proc.communicate(timeout=120)
        self.assertEqual(proc.returncode, 0, err)
        self.assertTrue(out.startswith("OK: 1 tests (partial: test_suite_runner.Target.test_target)"), out)
        self.assertTrue((self.out / "test_target.json").exists())

    def test_a_run_inside_a_run_locks_inside_its_parents_temp_directory(self):
        if os.environ.get(suite.PYCACHE_ENV) is None:
            self.skipTest("the serial command holds no lock to wait on")
        env = self.env(lock=False)
        env["TMPDIR"] = os.environ["TMPDIR"]  # this worker's own, inside the run's scratch directory
        code, out, err = self.run_nested("--no-wait", env=env)  # --no-wait: were it the parent's lock, 75 at once
        self.assertEqual(code, 0, err)
        self.assertTrue(self.report()["tmp"].startswith(os.environ["TMPDIR"] + os.sep), self.report())


@unittest.skipUnless(DARWIN_BG, "no os.PRIO_DARWIN_BG on this platform")
class BackgroundTest(NestedRun):
    """--background: the workers and what they start run at background priority, the wall_clock tests at normal."""

    def test_background_lowers_the_workers_and_their_children_but_not_the_wall_clock_tests(self):
        code, out, err = self.run_nested("--background", names=("test_suite_runner.Target",))
        self.assertEqual(code, 0, err)
        plain, timed = self.report("test_target"), self.report("test_timed_target")
        self.assertEqual((plain["priority"], plain["child_priority"]), (1, 1))
        self.assertEqual((timed["priority"], timed["child_priority"]), (0, 0))
        self.assertNotEqual(plain["pid"], timed["pid"])  # the timed test's worker started after the priority came back

    def test_without_the_flag_a_run_keeps_its_callers_priority(self):
        code, out, err = self.run_nested()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.report()["priority"], priority())


class NoBackgroundTest(unittest.TestCase):
    def test_where_the_platform_has_no_background_priority_the_flag_says_so(self):
        before = priority()
        saved = {n: getattr(os, n) for n in ("PRIO_DARWIN_BG",) if hasattr(os, n)}
        for n in saved:
            delattr(os, n)
        try:
            why = suite.background()
        finally:
            for n, v in saved.items():
                setattr(os, n, v)
        self.assertIn("PRIO_DARWIN_BG", why)
        self.assertEqual(priority(), before)  # nothing was set


class SignalTest(NestedRun):
    """SIGTERM and SIGHUP end a run as Ctrl-C does: workers killed and reaped, the scratch directory gone, the lock free."""

    def test_a_signalled_run_kills_its_workers_and_leaves_no_scratch(self):
        for signum in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum.name):
                for p in self.out.iterdir():
                    p.unlink()
                (self.out / "sleep").touch()
                proc = self.start()
                seen = self.report()
                self.assertEqual(len(scratch_dirs(self.tmp)), 1)
                self.assertTrue(os.path.isdir(seen["made"]))
                self.assertTrue(seen["made"].startswith(str(self.tmp) + os.sep))  # the worker's temp dir is the run's
                proc.send_signal(signum)
                out, err = proc.communicate(timeout=60)
                self.assertEqual(proc.returncode, 128 + signum, err)
                self.assertIn("suite: interrupted (%s)" % signum.name, err)
                self.assertEqual(scratch_dirs(self.tmp), [])
                self.assertFalse(os.path.exists(seen["made"]))
                self.assertFalse(suite.alive(seen["pid"]))
                suite.release(suite.acquire(str(self.lock), wait=False, say=self.fail))

    def test_a_killed_runs_workers_hold_the_lock_until_they_go_and_the_next_run_removes_its_scratch(self):
        (self.out / "sleep").touch()
        proc = self.start()
        self.addCleanup((self.out / "go").touch)  # before the cleanup that reads the orphan's stderr to its end
        seen = self.report()
        proc.kill()  # SIGKILL: nothing of the run's own cleanup runs
        proc.wait()
        self.assertTrue(suite.alive(seen["pid"]))  # its worker, orphaned mid-test
        self.assertIsNone(suite.acquire(str(self.lock), wait=False, say=lambda text: None))
        self.assertEqual(len(scratch_dirs(self.tmp)), 1)
        (self.out / "go").touch()  # the test ends, its report meets a closed pipe, and the worker exits
        deadline, free = time.monotonic() + 30, None
        while free is None and time.monotonic() < deadline:
            free = suite.acquire(str(self.lock), wait=False, say=lambda text: None)
            time.sleep(0.05)
        self.assertIsNotNone(free, "the orphaned worker never let the lock go")
        suite.release(free)
        for p in self.out.iterdir():
            p.unlink()
        code, out, err = self.run_nested()
        self.assertEqual(code, 0, err)
        self.assertIn("suite: removed 1 scratch directory of runs that are gone", err)
        self.assertEqual(scratch_dirs(self.tmp), [])
        self.assertFalse(os.path.exists(seen["made"]))


class SweepTest(unittest.TestCase):
    """A run's scratch directory names its owner; the next run removes the ones whose owner is gone."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="spud-sweep-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def scratch(self, name, owner=None, age=0):
        path = self.dir / name
        (path / "tree").mkdir(parents=True)
        if owner is not None:
            (path / suite.OWNER).write_text("%d\n" % owner, encoding="utf-8")
        if age:
            then = time.time() - age
            os.utime(path, (then, then))
        return path

    def test_it_removes_a_dead_runs_scratch_and_keeps_a_live_ones_and_everything_not_its_own(self):
        dead = dead_pid()
        self.assertFalse(suite.alive(dead))
        self.scratch("spud-suite-dead", owner=dead)
        self.scratch("spud-suite-live", owner=os.getpid())
        self.scratch("spud-suite-legacy-old", age=suite.LEGACY_AGE + 60)  # a run from before owner files, long gone
        self.scratch("spud-suite-legacy-new")  # no owner yet: a run between mkdtemp and its owner file, or an older runner's
        self.scratch("spud-test-abandoned", owner=dead, age=suite.LEGACY_AGE + 60)  # not the runner's name
        (self.dir / "spud-suite.lock").write_text("", encoding="utf-8")
        self.assertEqual(suite.sweep(self.dir), ["spud-suite-dead", "spud-suite-legacy-old"])
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         ["spud-suite-legacy-new", "spud-suite-live", "spud-suite.lock", "spud-test-abandoned"])


class StartupSweepTest(NestedRun):
    def test_a_run_removes_a_dead_runs_scratch_at_startup_and_its_own_at_the_end(self):
        dead = dead_pid()
        self.assertFalse(suite.alive(dead))
        for name, owner in (("spud-suite-dead", dead), ("spud-suite-live", os.getpid())):
            (self.tmp / name / "tree").mkdir(parents=True)
            (self.tmp / name / suite.OWNER).write_text("%d\n" % owner, encoding="utf-8")
        code, out, err = self.run_nested()
        self.assertEqual(code, 0, err)
        self.assertIn("suite: removed 1 scratch directory of runs that are gone: spud-suite-dead", err)
        self.assertEqual(scratch_dirs(self.tmp), ["spud-suite-live"])
        self.assertFalse(os.path.exists(self.report()["made"]))  # the scratch homes of the run went with it


# -- --changed [BASE] (SPD-234) ---------------------------------------------------------------------------------------


def entry(path, text=""):
    """One file of a tree as suite.tree lists it."""
    return (path.encode(), "f", 0o644, text.encode())


CHECKOUT = TESTS.parent
DEPS = TESTS / "suite_deps.json"


def this_tree():
    """This checkout's tests/*.py, its map and its dependency table, and every file under bin/ and share/, as suite.tree
    would list them: what a selection expands against."""
    files = [entry("tests/" + p.name, p.read_text(encoding="utf-8")) for p in sorted(TESTS.glob("*.py"))]
    files += [entry("tests/suite_map.json", MAP.read_text(encoding="utf-8")), entry("tests/suite_deps.json", DEPS.read_text(encoding="utf-8"))]
    for top in ("bin", "share"):
        for p in sorted((CHECKOUT / top).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                files.append(entry(str(p.relative_to(CHECKOUT)), p.read_text(encoding="utf-8") if p.suffix == ".py" or p.name == "spud" else ""))
    return files


def modules_of(files):
    return sorted(p.decode()[len("tests/"):-3] for p, *_ in files if fnmatch.fnmatchcase(p.decode(), "tests/test*.py"))


PROBES = ["test_module_sizes", "test_probe_env", "test_shell_probe"]
# What every test depends on: each of these runs the full suite whatever else changed, by the map's own `full` or, for a
# module of bin/spudlib, because the fixture's `spud init` ran it (SPD-233).
CORE = ["bin/spud", "bin/spud_ledger.py", "bin/spudlib/core/kernel.py", "bin/spudlib/state/ledgerdb.py", "bin/spudlib/cli/cliparser.py",
        "tests/helpers.py", "tests/suite.py", "tests/suite_map.json", "tests/suite_deps.json", "share/spud.config.json"]


class MapTest(unittest.TestCase):
    """The map itself: data, well formed, and naming only modules and tables this tree has."""

    def test_the_map_is_a_data_file_with_full_patterns_and_named_rules(self):
        table = json.loads(MAP.read_text(encoding="utf-8"))
        self.assertIsInstance(table["full"], list)
        for rule in table["rules"]:
            self.assertEqual(set(rule) - {"name", "paths", "modules", "importers", "table"}, set(), rule)
            self.assertTrue(rule["name"] and rule["paths"], rule)
            self.assertEqual(len({"modules", "table"} & set(rule)), 1, "a rule names its modules or a table, not both: %r" % rule)
        self.assertEqual(len({r["name"] for r in table["rules"]}), len(table["rules"]))
        self.assertEqual(suite.load_map(this_tree()), table)

    def test_every_module_the_map_names_exists_and_every_pattern_matches_one(self):
        present = modules_of(this_tree())
        for rule in json.loads(MAP.read_text(encoding="utf-8"))["rules"]:
            for name in rule.get("modules", []):
                with self.subTest(rule=rule["name"], module=name):
                    self.assertTrue(fnmatch.filter(present, name), "the map names %s, which no tests/*.py module is" % name)
            if "table" in rule:
                self.assertTrue((CHECKOUT / rule["table"]).is_file(), rule)

    def test_a_tree_with_no_map_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            suite.load_map([entry("tests/test_a.py")])
        self.assertIn("tests/suite_map.json", str(cm.exception))


class SelectTest(unittest.TestCase):
    """suite.select over this checkout's own tests, map and table, and over small trees built to show one property each."""

    @classmethod
    def setUpClass(cls):
        cls.files = this_tree()
        cls.present = modules_of(cls.files)
        cls.deps = json.loads(DEPS.read_text(encoding="utf-8"))
        cls.readers = suite.data_readers(cls.files)

    def select(self, *paths, files=None):
        return suite.select(list(paths), self.files if files is None else files, readers=None if files else self.readers)

    def by_the_table(self, path):
        """What the table says a change to `path` runs, spelled out from the table itself: every test module whose entry
        meets the file's reach, and every module the table does not name."""
        reached = suite.reach(path, self.readers)
        return sorted(t for t in self.present if t not in self.deps["modules"] or reached & set(self.deps["modules"][t]))

    def test_a_change_to_a_command_the_fixture_never_runs_selects_the_modules_that_run_it(self):
        """SPD-233's own example: `spud init` builds the parser, which names every command, but runs none of member's, so a
        change there runs what the table says ran it -- the member tests, and every hook test whose class plans members --
        and none of the tests that plan none."""
        for path, runs, never in (("bin/spudlib/commands/membercmds.py", "test_members", "test_markdown"),
                                  ("bin/spudlib/render/teamcard.py", "test_team_card", "test_hooks_globs")):
            with self.subTest(path=path):
                sel = self.select(path)
                self.assertFalse(sel.full, sel.why)
                self.assertEqual((sel.rules, sel.why), (["deps"], [(path, "deps")]))
                self.assertEqual(sel.modules, self.by_the_table(path))
                self.assertIn(runs, sel.modules)
                self.assertNotIn(never, sel.modules)
                self.assertLess(len(sel.modules), len(self.present))

    def test_a_shell_only_change_runs_the_modules_that_ran_the_shell_and_is_no_full_run(self):
        sel = self.select("bin/spudlib/shell/walk.py", "bin/spudlib/shell/zsh.py")
        self.assertFalse(sel.full, sel.why)
        self.assertEqual(sel.rules, ["deps"])
        self.assertEqual(sel.modules, sorted(set(self.by_the_table("bin/spudlib/shell/walk.py")) | set(self.by_the_table("bin/spudlib/shell/zsh.py"))))
        self.assertIn("test_hooks_bash", sel.modules)
        self.assertNotIn("test_markdown", sel.modules)
        # test_hookcase reads shell/bash_rule.py's source as text, and the table records a read as it records a run
        self.assertIn("test_hookcase", self.select("bin/spudlib/shell/bash_rule.py").modules)

    def test_what_the_fixtures_init_runs_or_reads_runs_the_full_suite(self):
        fixture = [p for p in self.deps["fixture"] if p.startswith(("bin/spudlib/", "share/"))]
        self.assertTrue(fixture)
        for path in fixture:
            with self.subTest(path=path):
                sel = self.select(path)
                self.assertTrue(sel.full)
                self.assertEqual(sel.why, [(path, "full" if path == "share/spud.config.json" else "fixture")])

    def test_a_module_whose_data_the_fixture_reads_runs_the_full_suite_though_init_ran_none_of_it(self):
        """state/schema holds the DDL and runs no function of its own when init applies it: the data edge reaches it."""
        schema = "bin/spudlib/state/schema.py"
        self.assertNotIn(schema, self.deps["fixture"])
        self.assertTrue(suite.reach(schema, self.readers) & set(self.deps["fixture"]))
        self.assertEqual(self.select(schema).why, [(schema, "fixture")])

    def test_nothing_under_bin_or_share_escapes_to_fewer_than_the_table_says(self):
        """Every file under bin/ and share/ is `full`, reached by the fixture, or selects exactly what the table says; a file
        the table never saw runs the full suite."""
        for rel, *_ in self.files:
            path = rel.decode()
            if not path.startswith(("bin/", "share/")):
                continue
            with self.subTest(path=path):
                sel = self.select(path)
                if not sel.full:
                    self.assertEqual(sel.modules, self.by_the_table(path))
                elif path not in self.deps["files"]:
                    self.assertEqual(sel.why, [(path, None)])
        for new in ("bin/spudlib/commands/brand_new.py", "share/skills/brand-new/SKILL.md", "bin/spud_helper.py"):
            with self.subTest(new=new):
                sel = self.select(new)
                self.assertTrue(sel.full)
                self.assertIn(sel.why, ([(new, None)], [(new, "full")]))

    def test_a_test_module_the_table_does_not_name_runs_for_every_change_the_table_decides(self):
        files = self.files + [entry("tests/test_brand_new.py", "import unittest\n")]
        self.assertNotIn("test_brand_new", self.deps["modules"])
        self.assertIn("test_brand_new", suite.select(["bin/spudlib/commands/membercmds.py"], files).modules)

    def test_the_data_edge_is_a_read_of_anything_but_a_top_level_function(self):
        files = [entry("bin/spudlib/state/tables.py", "ROWS = (1, 2)\n\n\nclass Shape:\n    pass\n\n\ndef count():\n    return len(ROWS)\n"),
                 entry("bin/spudlib/state/reads.py", "from . import tables\n\n\ndef total():\n    return sum(tables.ROWS)\n"),
                 entry("bin/spudlib/commands/shapes.py", "from ..state import tables\n\n\ndef make():\n    return tables.Shape()\n"),
                 entry("bin/spudlib/commands/calls.py", "from ..state import reads, tables\n\n\ndef run():\n    return tables.count() + reads.total()\n"),
                 entry("bin/spudlib/commands/broken.py", "def (\n")]
        readers = suite.data_readers(files)
        self.assertEqual(readers["bin/spudlib/state/tables.py"], {"bin/spudlib/state/reads.py", "bin/spudlib/commands/shapes.py"})
        self.assertNotIn("bin/spudlib/state/reads.py", readers)  # calls.py only calls reads.total
        self.assertEqual(suite.reach("bin/spudlib/state/tables.py", readers),
                         {"bin/spudlib/state/tables.py", "bin/spudlib/state/reads.py", "bin/spudlib/commands/shapes.py"})

    def test_a_table_measured_on_another_program_is_named_in_a_warning(self):
        """SPD-233 review F3: the table records the digest of the bin/ and share/ it was measured on (`code`), and a
        selection that reads the table says when the tree's differ, and how to measure again; one that matches, or a
        selection that never reads the table, says nothing."""
        program = [entry("bin/spudlib/a.py", "def f():\n    return 1\n"), entry("share/x.md", "x\n")]
        rest = [entry("tests/suite_map.json", MAP.read_text(encoding="utf-8")), entry("tests/test_a.py", "import unittest\n")]

        def tree(code, *changed):
            table = {"files": ["bin/spudlib/a.py", "share/x.md"], "fixture": [], "modules": {"test_a": ["bin/spudlib/a.py"]}}
            if code is not None:
                table["code"] = code
            return [f for f in program if f[0].decode() not in changed] + [entry(p, "changed\n") for p in changed] + rest + [
                entry("tests/suite_deps.json", json.dumps(table))]

        measured = suite.code_digest(program)
        current = self.select("bin/spudlib/a.py", files=tree(measured))
        self.assertEqual((current.modules, current.stale), (["test_a"], None))
        self.assertFalse([line for line in suite.explain(current, "main", "0" * 40) if "warning" in line])
        for changed in ("bin/spudlib/a.py", "share/x.md"):
            with self.subTest(changed=changed):
                files = tree(measured, changed)
                sel = self.select("bin/spudlib/a.py", files=files)
                self.assertEqual(sel.modules, ["test_a"])  # a warning, never a different selection
                self.assertEqual(suite.explain(sel, "main", "0" * 40)[-1], sel.stale)
                self.assertIn("tests/suite_deps.json was measured on bin/ and share/ at %s" % measured, sel.stale)
                self.assertIn("are %s" % suite.code_digest(files), sel.stale)
                self.assertIn("python3.14 -I -S tests/suite_deps.py", sel.stale)
        self.assertIn("before it recorded what it was measured on", self.select("bin/spudlib/a.py", files=tree(None)).stale)
        self.assertIsNone(self.select("tests/probes/hook_timing.py", files=tree(None)).stale)  # no rule read the table

    def test_a_probes_only_change_runs_the_probe_tests(self):
        sel = self.select("tests/probes/hook_timing.py", "tests/probes/a_new_probe.py")
        self.assertEqual((sel.full, sel.rules, sel.modules), (False, ["probes"], PROBES))

    def test_an_unmapped_path_runs_the_full_suite(self):
        for path in ("README.md", "a/new/file.txt", "docs/x.md"):
            with self.subTest(path=path):
                sel = self.select("tests/probes/hook_timing.py", path)
                self.assertTrue(sel.full)
                self.assertEqual(sel.why, [("tests/probes/hook_timing.py", "probes"), (path, None)])

    def test_a_core_path_runs_the_full_suite(self):
        for path in CORE:
            with self.subTest(path=path):
                sel = self.select("tests/probes/hook_timing.py", path)
                self.assertTrue(sel.full)
                self.assertTrue((path, "full") in sel.why or (path, "fixture") in sel.why, sel.why)

    def test_a_changed_test_module_runs_itself_and_every_module_importing_it(self):
        files = [entry("tests/suite_map.json", MAP.read_text(encoding="utf-8")),
                 entry("tests/test_base.py", "import unittest\n"),
                 entry("tests/test_mid.py", "from test_base import Case\n"),
                 entry("tests/test_top.py", "import os\nfrom test_mid import (\n    Case,\n)\n"),
                 entry("tests/test_alone.py", "from helpers import REPO\n")]
        self.assertEqual(self.select("tests/test_base.py", files=files).modules, ["test_base", "test_mid", "test_top"])
        self.assertEqual(self.select("tests/test_top.py", files=files).modules, ["test_top"])
        self.assertEqual(self.select("tests/test_gone.py", files=files).modules, [])  # deleted, and nothing imports it
        self.assertEqual(self.select("tests/test_alone.py", files=files).rules, ["tests"])

    def test_this_trees_test_modules_bring_their_importers(self):
        sel = self.select("tests/test_team_card.py")
        self.assertEqual(sel.modules, ["test_cost", "test_team_card"])  # test_cost imports test_team_card

    def test_hookcase_runs_every_hook_module_and_everything_importing_it(self):
        files = [entry("tests/suite_map.json", MAP.read_text(encoding="utf-8")), entry("tests/hookcase.py", "from helpers import X\n"),
                 entry("tests/test_hooks_bash.py", "from hookcase import HookCase\n"), entry("tests/test_hooks_git.py", "import unittest\n"),
                 entry("tests/test_cost.py", "from hookcase import HookCase\n"), entry("tests/test_members.py", "import helpers\n"),
                 entry("tests/test_hookcase.py", "import unittest\n")]
        sel = self.select("tests/hookcase.py", files=files)
        self.assertEqual((sel.full, sel.rules, sel.modules),
                         (False, ["hookcase"], ["test_cost", "test_hookcase", "test_hooks_bash", "test_hooks_git"]))

    def test_this_trees_hookcase_change_runs_every_module_importing_it(self):
        sel = self.select("tests/hookcase.py")
        importers = {"test_cost", "test_team_card", "test_resum", "test_auto_claim", "test_session_context", "test_pull_requests",
                     "test_ticket_worktree", "test_hookcase"}
        self.assertTrue(importers | {m for m in self.present if m.startswith("test_hooks")} <= set(sel.modules))

    def test_the_tracing_tools_run_their_own_tests(self):
        sel = self.select("tests/suite_trace.py", "tests/suite_deps.py")
        self.assertEqual((sel.full, sel.rules, sel.modules), (False, ["tracing"], ["test_suite_deps"]))

    def test_rules_add_up_and_nothing_changed_selects_nothing(self):
        sel = self.select("tests/probes/probe_env.py", "bin/spudlib/shell/zsh.py", "tests/test_members.py")
        self.assertEqual(sel.rules, ["probes", "deps", "tests"])
        self.assertTrue(set(PROBES) | set(self.by_the_table("bin/spudlib/shell/zsh.py")) | {"test_members"} <= set(sel.modules))
        empty = self.select()
        self.assertEqual((empty.full, empty.rules, empty.modules, empty.why), (False, [], [], []))


class ChangedLineTest(unittest.TestCase):
    """The final line of a --changed run says `affected` and the rule, or `full` and why, and always the tree's digest."""

    def line(self, scope, count=3):
        run = suite.Run(["test_a.T.test_%d" % i for i in range(count)], Path("/nonexistent"), {}, 4, count)
        run.run = count
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            ok = suite.report(run, 1.52, scope, "0123456789abcdef", 4)
        self.assertTrue(ok)
        return out.getvalue()

    def test_a_selection_by_the_map_says_affected_its_rules_its_base_and_the_digest(self):
        sel = suite.Selection(["bin/spudlib/shell/walk.py", "tests/test_x.py"], [("bin/spudlib/shell/walk.py", "shell"), ("tests/test_x.py", "tests")],
                              False, ["shell", "tests"], ["test_hooks_bash", "test_x"])
        self.assertEqual(self.line(suite.scope(sel, "main")),
                         "OK: 3 tests (affected: shell+tests against main, 2 modules) in 1.5 s on 4 workers; tree 0123456789abcdef\n")

    def test_a_fallback_says_full_and_the_path_that_forced_it(self):
        sel = suite.Selection(["bin/spudlib/core/kernel.py", "README.md", "tests/test_x.py"],
                              [("bin/spudlib/core/kernel.py", "full"), ("README.md", None), ("tests/test_x.py", "tests")], True, ["tests"], ["test_x"])
        self.assertEqual(self.line(suite.scope(sel, "origin/main")),
                         "OK: 3 tests (full, --changed against origin/main: bin/spudlib/core/kernel.py by rule full, and 1 more)"
                         " in 1.5 s on 4 workers; tree 0123456789abcdef\n")
        only = suite.Selection(["README.md"], [("README.md", None)], True, [], [])
        self.assertEqual(suite.scope(only, "main"), " (full, --changed against main: README.md unmapped)")

    def test_a_path_the_fixture_reaches_says_so(self):
        sel = suite.Selection(["bin/spudlib/state/schema.py"], [("bin/spudlib/state/schema.py", "fixture")], True, [], [])
        self.assertEqual(suite.scope(sel, "main"), " (full, --changed against main: bin/spudlib/state/schema.py reached by the fixture's init)")
        self.assertIn("  bin/spudlib/state/schema.py: full: the fixture's init reaches it", suite.explain(sel, "main", "0" * 40))

    def test_nothing_changed_says_so(self):
        self.assertEqual(suite.scope(suite.Selection([], [], False, [], []), "main"), " (affected: nothing changed against main)")

    def test_a_named_run_still_says_partial_and_a_full_run_nothing(self):
        self.assertEqual(self.line(" (partial: test_a)"), "OK: 3 tests (partial: test_a) in 1.5 s on 4 workers; tree 0123456789abcdef\n")
        self.assertEqual(self.line(""), "OK: 3 tests in 1.5 s on 4 workers; tree 0123456789abcdef\n")


class ScratchRepo:
    """A git repository of the test's own: main with a first commit, and a branch cut from it."""

    def make_repo(self, root, files):
        root.mkdir(parents=True)
        git(root, "init", "-q", "-b", "main")
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "first")
        git(root, "checkout", "-q", "-b", "feature")
        return root


class ChangedPathsTest(ScratchRepo, unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="spud-changed-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_it_lists_every_change_against_the_merge_base_tracked_or_not_and_nothing_ignored(self):
        repo = self.make_repo(self.dir / "repo", {".gitignore": "*.log\n", "a.txt": "a\n", "b.txt": "b\n", "gone.txt": "g\n", "old.txt": "o\n", "same.txt": "s\n"})
        (repo / "a.txt").write_text("a2\n", encoding="utf-8")
        git(repo, "commit", "-q", "-am", "committed on the branch")
        git(repo, "checkout", "-q", "main")
        (repo / "main_only.txt").write_text("m\n", encoding="utf-8")
        git(repo, "add", "main_only.txt")
        git(repo, "commit", "-q", "-m", "main moved after the branch was cut")
        git(repo, "checkout", "-q", "feature")
        (repo / "staged.txt").write_text("s\n", encoding="utf-8")
        git(repo, "add", "staged.txt")
        (repo / "b.txt").write_text("b2\n", encoding="utf-8")  # unstaged
        git(repo, "rm", "-q", "gone.txt")
        git(repo, "mv", "old.txt", "new.txt")
        (repo / "sub").mkdir()
        (repo / "sub" / "untracked.txt").write_text("u\n", encoding="utf-8")
        (repo / "noise.log").write_text("ignored\n", encoding="utf-8")
        base = git(repo, "merge-base", "main", "HEAD").strip()
        with mock_environ(isolated_git_env()):
            self.assertEqual(suite.changed(repo, "main"),
                             (base, ["a.txt", "b.txt", "gone.txt", "new.txt", "old.txt", "staged.txt", "sub/untracked.txt"]))
            self.assertEqual(suite.changed(repo, "HEAD")[1], ["b.txt", "gone.txt", "new.txt", "old.txt", "staged.txt", "sub/untracked.txt"])

    def test_an_unknown_base_is_refused(self):
        repo = self.make_repo(self.dir / "repo", {"a.txt": "a\n"})
        with mock_environ(isolated_git_env()), self.assertRaises(SystemExit) as cm:
            suite.changed(repo, "no-such-branch")
        self.assertIn("merge-base", str(cm.exception))


@contextlib.contextmanager
def mock_environ(env):
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


class ChangedRunTest(ScratchRepo, NestedRun):
    """A real `--changed` run: a copy of this runner and its map in a scratch repository whose branch changed one module."""

    def setUp(self):
        super().setUp()
        passing = "import unittest\n\nclass T(unittest.TestCase):\n    def test_it(self):\n        pass\n"
        self.repo = self.make_repo(self.dir / "repo", {
            "tests/suite.py": SUITE.read_text(encoding="utf-8"), "tests/suite_map.json": MAP.read_text(encoding="utf-8"),
            "tests/test_one.py": passing, "tests/test_two.py": passing, "tests/test_three.py": passing.replace("pass", "self.fail('never run')")})

    def suite_run(self, *args):
        env = isolated_git_env(self.env())
        proc = subprocess.run([sys.executable, "-I", "-S", str(self.repo / "tests" / "suite.py"), *args], cwd=self.repo, env=env,
                              capture_output=True, text=True, timeout=120)
        return proc.returncode, proc.stdout, proc.stderr

    def test_it_runs_the_changed_module_alone_and_says_affected_with_the_digest(self):
        (self.repo / "tests" / "test_one.py").write_text((self.repo / "tests" / "test_one.py").read_text() + "\n# changed\n", encoding="utf-8")
        _, digest, _ = self.suite_run("--digest")
        code, out, err = self.suite_run("--changed", "--cold", "-j", "1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines()[-1].split(" in ")[0], "OK: 1 tests (affected: tests against main, 1 module)")
        self.assertTrue(out.rstrip().endswith("; tree " + digest.strip()), out)
        self.assertIn("tests/test_one.py: tests", err)

    def test_dry_run_prints_the_selection_and_runs_nothing(self):
        (self.repo / "tests" / "test_two.py").write_text("# changed\n" + (self.repo / "tests" / "test_two.py").read_text(), encoding="utf-8")
        (self.repo / "README.md").write_text("new\n", encoding="utf-8")
        code, out, err = self.suite_run("--changed", "main", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("README.md: unmapped", out)
        self.assertIn("tests/test_two.py: tests", out)
        self.assertIn("the full suite", out)
        self.assertEqual(scratch_dirs(self.tmp), [])
        self.assertFalse(self.lock.exists())

    def test_nothing_changed_runs_nothing_and_says_so(self):
        code, out, err = self.suite_run("--changed", "--cold")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("OK: 0 tests (affected: nothing changed against main) in "), out)

    def test_changed_takes_no_names_and_dry_run_needs_changed(self):
        for args in (("--changed", "main", "test_one"), ("--dry-run",)):
            with self.subTest(args=args):
                code, out, err = self.suite_run(*args)
                self.assertEqual(code, 2, err)


if __name__ == "__main__":
    unittest.main()
