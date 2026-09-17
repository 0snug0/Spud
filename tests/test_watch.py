"""SPD-097: the vault follows the database.  The render lock, the watcher (`render --watch`), a conflict logged once, and
what doctor and board say about a watcher that is down."""

import subprocess
import sys
import time
import unittest

from helpers import EXIT_ERROR, EXIT_OK, SPUD, LaunchdMixin, SpudTestCase, load_spud_module

spud = load_spud_module()


class WatchCase(SpudTestCase):
    def ctx(self):
        return spud.Ctx(self.home.path, "SPUD_HOME", False, tool=self.home.path)

    def watcher(self, *extra):
        """`spud --as spud render --watch` as a subprocess with a fast tick, stopped and reaped by the test."""
        proc = subprocess.Popen([sys.executable, "-I", "-S", str(SPUD), "--as", "spud", "render", "--watch", "--interval", "0.05", *extra],
                                env=self.home.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, proc)
        return proc

    def stop(self, proc):
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def render_events(self):
        return self.home.scalar("SELECT count(*) FROM events WHERE kind = 'render'")

    def conflict_events(self):
        return self.home.rows("SELECT data FROM events WHERE kind = 'render' AND json_extract(data, '$.conflict') = 1 ORDER BY id")


class RenderLockTest(WatchCase):
    def test_a_manual_render_waits_for_the_lock(self):
        self.new_ticket("Locked")
        with spud.render_lock(self.ctx()):
            proc = subprocess.Popen([sys.executable, "-I", "-S", str(SPUD), "--as", "spud", "render"], env=self.home.env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(0.5)
            self.assertIsNone(proc.poll(), "the render must wait while the lock is held")
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, EXIT_OK, err)
        self.assertIn("written", out)
        self.assertTrue((self.home.path / ".spud" / "render.lock").is_file())


class WatcherTest(WatchCase):
    def test_the_watcher_renders_once_after_an_event_and_not_after_its_own(self):
        t = self.new_ticket("Watched")
        path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        proc = self.watcher()
        self.assertTrue(self.wait_for(path.is_file), "the first pass renders the ticket")
        self.assertTrue(self.wait_for(lambda: self.render_events() == 1))
        time.sleep(0.4)  # eight ticks with nothing new
        self.assertEqual(self.render_events(), 1)
        self.home.json("ticket", "edit", t["key"], "--title", "Renamed", actor="spud")
        self.assertTrue(self.wait_for(lambda: "Renamed" in path.read_text(encoding="utf-8")))
        self.assertTrue(self.wait_for(lambda: self.render_events() == 2))
        time.sleep(0.4)
        self.assertEqual(self.render_events(), 2)
        proc.terminate()
        out, err = proc.communicate(timeout=10)
        self.assertEqual((proc.returncode, err), (0, ""), (out, err))
        self.assertIn("stopped after 2 pass(es): signal 15", out)
        self.assertEqual(out.count(" rendered "), 2)

    def test_ticks_ends_the_watcher_and_a_second_watcher_is_refused(self):
        self.new_ticket("Ticks")
        first = self.watcher()
        self.assertTrue(self.wait_for(lambda: spud.watcher_alive(self.ctx())))
        proc = self.home.run("render", "--watch", "--interval", "0.05", "--ticks", "1", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("already running", proc.stderr)
        first.terminate()
        first.communicate(timeout=10)
        self.assertFalse(spud.watcher_alive(self.ctx()))
        proc = self.home.run("render", "--watch", "--interval", "0.05", "--ticks", "2", actor="spud")
        self.assertIn("stopped after 0 pass(es): 2 ticks", proc.stdout)  # the first watcher rendered the ticket already
        self.assertFalse(spud.watcher_alive(self.ctx()))
        self.assertFalse(spud.watcher_alive(spud.Ctx(self.home.path / "nowhere", "SPUD_HOME", False, tool=self.home.path)))

    def test_watch_takes_no_out_and_no_discard(self):
        for extra in (("--out", str(self.home.path / "x")), ("--discard", "ledger/tickets/SPD-001.md")):
            with self.subTest(extra=extra):
                proc = self.home.run("render", "--watch", *extra, actor="spud", check=False)
                self.assertEqual(proc.returncode, 2, proc)


class ConflictOnceTest(WatchCase):
    def test_the_watcher_logs_a_conflict_once_and_keeps_rendering_the_rest(self):
        self.new_ticket("Edited", brief="Original brief.")
        self.home.json("render")
        path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        path.write_text(path.read_text(encoding="utf-8").replace("Original brief.", "By hand."), encoding="utf-8")
        proc = self.watcher()
        other = self.new_ticket("Second")
        second = self.home.path / "ledger" / "tickets" / "SPD-002.md"
        self.assertTrue(self.wait_for(second.is_file))
        self.home.json("ticket", "edit", other["key"], "--title", "Renamed", actor="spud")
        self.assertTrue(self.wait_for(lambda: "Renamed" in second.read_text(encoding="utf-8")))
        time.sleep(0.3)
        self.assertEqual(len(self.conflict_events()), 1)
        self.assertIn("By hand.", path.read_text(encoding="utf-8"))
        proc.terminate()
        out, _ = proc.communicate(timeout=10)
        self.assertEqual(out.count("conflicts 1"), 2)


class DoctorAndBoardTest(LaunchdMixin, WatchCase):
    def setUp(self):
        super().setUp()
        self.setup_launchd()

    def test_doctor_and_board_say_when_the_watcher_is_down(self):
        self.new_ticket("Board")
        self.assertNotIn("render watcher", self.home.run("board", "--brief").stdout)
        report = self.home.json("doctor")
        self.assertEqual(report["render"], {"watcher": "not installed", "conflicts": []})
        self.assertTrue(any("no render watcher installed" in n for n in report["notes"]), report["notes"])
        self.home.json("schedule", "install", actor="spud")  # the fake launchctl loads nothing
        self.assertIn("render watcher: installed but not running", self.home.run("board", "--brief").stdout)
        proc = self.home.run("doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("installed but not running", proc.stderr)
        self.watcher()
        self.assertTrue(self.wait_for(lambda: spud.watcher_alive(self.ctx())))
        self.assertNotIn("render watcher", self.home.run("board", "--brief").stdout)
        self.assertEqual(self.home.json("doctor")["render"]["watcher"], "running")

    def test_doctor_lists_a_hand_edited_file_with_its_two_commands(self):
        self.new_ticket("Edited", brief="Original brief.")
        self.home.json("render")
        path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        path.write_text(path.read_text(encoding="utf-8").replace("Original brief.", "By hand."), encoding="utf-8")
        proc = self.home.run("--json", "doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("import --file ledger/tickets/SPD-001.md", proc.stderr)
        self.assertIn("render --discard ledger/tickets/SPD-001.md", proc.stderr)
        self.assertIn("ledger/tickets/SPD-001.md", proc.stdout)


if __name__ == "__main__":
    unittest.main()
