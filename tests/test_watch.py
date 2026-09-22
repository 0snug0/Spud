"""SPD-097: the vault follows the database.  The render lock, the watcher (`render --watch`), a conflict logged once, and
what doctor and board say about a watcher that is down.

SPD-117 adds the state liveness could not see: a watcher running and stuck, which reported `watcher running` and no problem
while every note went stale.  The report compares the vault with the ledger instead -- the events since the last pass and
how old the oldest of them is -- so doctor, `spud board --brief` and the SessionStart context tell four states apart: no
watcher installed, one installed and not running, one running with the vault caught up, and one running with the vault
behind.  VaultLagTest needs no watcher process for any of them.
"""

import json
import os
import subprocess
import sys
import time
import unittest

from helpers import (EXIT_ERROR, EXIT_OK, SPUD, LaunchdMixin, SpudTestCase, aged_event, hold_watch_lock,
                     install_watcher_plist, load_spud_module)

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
        base = self.render_events()  # SPW-001 phase 4: init rendered the home it built, so the watcher's is not the first
        proc = self.watcher()
        self.assertTrue(self.wait_for(path.is_file), "the first pass renders the ticket")
        self.assertTrue(self.wait_for(lambda: self.render_events() == base + 1))
        time.sleep(0.4)  # eight ticks with nothing new
        self.assertEqual(self.render_events(), base + 1)
        self.home.json("ticket", "edit", t["key"], "--title", "Renamed", actor="spud")
        self.assertTrue(self.wait_for(lambda: "Renamed" in path.read_text(encoding="utf-8")))
        self.assertTrue(self.wait_for(lambda: self.render_events() == base + 2))
        time.sleep(0.4)
        self.assertEqual(self.render_events(), base + 2)
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
        self.assertEqual((report["render"]["watcher"], report["render"]["state"], report["render"]["conflicts"]), ("not installed", "absent", []))
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


BEHIND_LINE = "render watcher: %s unrendered, the oldest %s; the vault is stale (spud render brings it up to date)"
DOWN_LINE = "render watcher: installed but not running; the vault is stale (spud --as spud schedule install reloads it)"


class VaultLagTest(WatchCase):
    """SPD-117: the four states doctor and `spud board --brief` report, with no watcher process in any of them.  The plist
    is a file, the lock is one this test holds, and how far behind the vault is comes from the event log's own stamps."""

    def report(self):
        """doctor's JSON report, whatever doctor exits: a problem is the point of half of these states."""
        return json.loads(self.home.run("--json", "doctor", check=False).stdout)

    def render_line(self):
        """doctor's render line.  doctor prints its lines only when it found nothing wrong; a problem goes to stderr."""
        return next((line for line in self.home.run("doctor", check=False).stdout.split("\n") if line.startswith("render ")), "")

    def brief(self):
        return self.home.run("board", "--brief").stdout.rstrip("\n").split("\n")

    def run_watcher_in_place(self):
        """Installed and holding the lock: a watcher that answers every liveness question and renders nothing."""
        install_watcher_plist(self.home)
        self.addCleanup(os.close, hold_watch_lock(self.home))

    def test_a_vault_that_has_caught_up_says_so_whether_or_not_a_watcher_is_there(self):
        self.new_ticket("Vault")
        self.home.json("render")
        r = self.report()["render"]
        self.assertEqual((r["state"], r["watcher"], r["lag"]["events"], r["lag"]["behind"]), ("absent", "not installed", 0, False))
        self.assertEqual(self.render_line(), "render      watcher not installed; vault current")
        self.assertNotIn("render watcher", "\n".join(self.brief()))
        self.run_watcher_in_place()
        r = self.report()["render"]
        self.assertEqual((r["state"], r["watcher"], r["lag"]["events"]), ("current", "running", 0))
        self.assertEqual(self.render_line(), "render      watcher running; vault current")
        self.assertNotIn("render watcher", "\n".join(self.brief()))  # a quiet board means the vault on disk is the ledger

    def test_a_watcher_running_and_behind_is_a_problem_that_names_spud_render(self):
        self.new_ticket("Rendered")
        self.home.json("render")
        self.run_watcher_in_place()
        fresh = self.new_ticket("Unrendered")  # seconds old: reported, and not yet a problem
        r = self.report()["render"]
        events = self.home.scalar("SELECT count(*) FROM events WHERE ticket_id = (SELECT id FROM tickets WHERE key = ?)", fresh["key"])
        self.assertEqual((r["state"], r["lag"]["events"], r["lag"]["behind"]), ("current", events, False))
        self.assertTrue(self.render_line().startswith("render      watcher running; vault behind by %d events, the oldest " % events), self.render_line())
        self.assertNotIn("render watcher", "\n".join(self.brief()))
        self.home.json("render")  # caught up again, and then the same watcher stops rendering for ten minutes
        aged_event(self.home, 10 * 60)
        proc = self.home.run("--json", "doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        r = json.loads(proc.stdout)["render"]
        self.assertEqual((r["state"], r["watcher"], r["lag"]["events"], r["lag"]["behind"], r["lag"]["seconds"] // 60), ("behind", "running", 1, True, 10))
        self.assertIn("the vault is behind the ledger by 1 event, the oldest 10m ago", proc.stderr)
        self.assertIn("`spud render` brings it up to date", proc.stderr)
        self.assertIn("reloads the watcher that is running and not rendering", proc.stderr)  # the other half of the recovery
        self.assertNotIn("installed but not running", proc.stderr)  # alive, and stale: the state liveness cannot see
        self.assertEqual(self.brief()[-1], BEHIND_LINE % ("1 event", "10m ago"))

    def test_a_watcher_down_and_a_vault_behind_are_two_problems_and_two_lines(self):
        self.new_ticket("Rendered")
        self.home.json("render")
        install_watcher_plist(self.home)  # installed, nothing holding the lock
        aged_event(self.home, 3 * 3600)
        aged_event(self.home, 3600)
        proc = self.home.run("--json", "doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        r = json.loads(proc.stdout)
        self.assertEqual((r["render"]["state"], r["render"]["watcher"]), ("down", "installed, not running"))
        self.assertEqual((r["render"]["lag"]["events"], r["render"]["lag"]["behind"]), (2, True))
        self.assertEqual(len([p for p in r["problems"] if "watcher" in p or "vault is behind" in p]), 2)
        self.assertNotIn("running and not rendering", " ".join(r["problems"]))  # nothing is running: the down problem says so
        self.assertEqual(self.brief()[-2:], [DOWN_LINE, BEHIND_LINE % ("2 events", "3h ago")])

    def test_no_watcher_installed_and_a_vault_behind_still_names_the_command(self):
        self.new_ticket("Rendered")
        self.home.json("render")
        aged_event(self.home, 2 * 86400)
        r = self.report()["render"]
        self.assertEqual((r["state"], r["watcher"], r["lag"]["behind"]), ("absent", "not installed", True))
        self.assertEqual(self.brief()[-1], BEHIND_LINE % ("1 event", "2d ago"))
        self.assertTrue(any("no render watcher installed" in n for n in self.report()["notes"]))

    def test_one_render_clears_a_lag_no_note_shows(self):
        """The renders table records only the files a pass wrote (SPD-097), so an event no note shows -- a hook denial --
        leaves its through_event_id where it was, however often the vault is rendered.  Every pass writes its own mark
        instead, so the one command the report names does clear the report."""
        self.new_ticket("Vault")
        self.home.json("render")
        recorded = self.home.scalar("SELECT MAX(through_event_id) FROM renders")
        aged_event(self.home, 30 * 60, kind="hook.denied")
        r = self.report()["render"]
        self.assertEqual((r["lag"]["events"], r["lag"]["behind"]), (1, True))
        result = self.home.json("render")
        self.assertEqual(result["written"], [])  # the event changed no note, so the table's watermark stands still
        self.assertEqual(self.home.scalar("SELECT MAX(through_event_id) FROM renders"), recorded)
        r = self.report()["render"]
        self.assertEqual((r["state"], r["lag"]["events"], r["lag"]["behind"]), ("absent", 0, False))
        self.assertNotIn("render watcher", self.home.run("board", "--brief").stdout)

    def test_every_pass_marks_what_it_rendered_and_the_table_answers_without_a_mark(self):
        self.new_ticket("Vault")
        self.home.json("render")
        mark = self.home.path / ".spud" / "rendered.json"
        marked = json.loads(mark.read_text(encoding="utf-8"))
        self.assertEqual(marked["through_event_id"], self.home.scalar("SELECT MAX(id) FROM events WHERE kind != 'render'"))
        recorded = self.home.scalar("SELECT MAX(through_event_id) FROM renders")
        con = self.home.connect()
        try:
            self.assertEqual(spud.rendered_through(self.ctx(), con), marked["through_event_id"])
            mark.unlink()  # a home last rendered before SPD-117: the renders table still answers
            self.assertEqual(spud.rendered_through(self.ctx(), con), recorded)
            mark.write_text("not json at all\n", encoding="utf-8")  # and a mark that cannot be read never answers instead
            self.assertEqual(spud.rendered_through(self.ctx(), con), recorded)
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
