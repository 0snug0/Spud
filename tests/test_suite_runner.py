"""tests/suite.py's own guarantees (SPD-232): one run per machine, --no-wait, --background, and cleanup after a killed run.

Most of these start a real nested tests/suite.py -- the snapshot's, over the snapshot this run is running in -- as a tiny
named run of `Target` below (`--cold -j 1`), in a directory of the test's own: TMPDIR names its `tmp/`, where the nested
run makes and sweeps its scratch, and SPUD_SUITE_LOCK names a lock file there too.  That is how a test inside a run
holding the machine's lock starts another run without waiting on its own parent: a nested run's lock is the one its
environment names, and a run passes SPUD_SUITE_LOCK to none of its workers while pointing their TMPDIR into its own
scratch directory, so even a nested run that names no lock locks inside its parent's run (`Target` checks both).
"""

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
from helpers import wall_clock

SUITE = Path(__file__).resolve().parent / "suite.py"
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


if __name__ == "__main__":
    unittest.main()
