"""SPD-097: the vault follows the database.  The render lock, the watcher (`render --watch`), a conflict logged once, and
what doctor and board say about a watcher that is down.

SPD-117 adds the state liveness could not see: a watcher running and stuck, which reported `watcher running` and no problem
while every note went stale.  The report compares the vault with the ledger instead -- the events since the last pass and
how old the oldest of them is -- so doctor, `spud board --brief` and the SessionStart context tell four states apart: no
watcher installed, one installed and not running, one running with the vault caught up, and one running with the vault
behind.  VaultLagTest needs no watcher process for any of them.
"""

import importlib
import json
import os
import shutil
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


class LogRotationTest(WatchCase):
    """SPD-170: a watcher start used to truncate render.log, and since SPD-119 ends the watcher on every deploy, each
    restart erased the line saying why the last run ended.  A start now keeps the previous run's log, its tail at most
    RENDER_LOG_KEEP bytes, in render.log.1, and truncates render.log in place: launchd holds render.log open as the
    watcher's stdout and stderr, so the file itself must stay where it is."""

    def start(self, append=True):
        """One watcher run of two ticks whose stdout and stderr are the home's render.log, opened as launchd opens it."""
        log = self.home.path / ".spud" / "logs" / "render.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "ab" if append else "r+b") as out:
            if not append:
                out.seek(0, os.SEEK_END)  # a non-append fd whose offset is the old end: the seek must bring it back
            proc = subprocess.run([sys.executable, "-I", "-S", str(SPUD), "--as", "spud", "render", "--watch", "--interval", "0.05", "--ticks", "2"],
                                  env=self.home.env, stdout=out, stderr=out, timeout=30)
        self.assertEqual(proc.returncode, EXIT_OK)
        return log, log.with_name("render.log.1")

    def test_a_start_keeps_the_previous_run_and_repeated_starts_stay_bounded(self):
        self.new_ticket("Logged")
        log = self.home.path / ".spud" / "logs" / "render.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        previous = "2026-09-22T10:00:00-07:00 stopped after 3 pass(es): the program changed on disk (x.py)\n"
        log.write_text(previous, encoding="utf-8")
        log, kept = self.start()
        self.assertEqual(kept.read_text(encoding="utf-8"), previous)
        now = log.read_text(encoding="utf-8")
        self.assertIn(" watching ", now.split("\n", 1)[0], "render.log starts with this run")
        self.assertIn("stopped after", now)
        self.assertNotIn(previous, now)
        first_run = now
        log, kept = self.start(append=False)
        self.assertEqual(kept.read_text(encoding="utf-8"), first_run, "the second start keeps the first run, not the one before")
        self.assertNotIn("\0", log.read_text(encoding="utf-8"), "a non-append fd must be sought back, not leave a hole")
        self.assertIn(" watching ", log.read_text(encoding="utf-8").split("\n", 1)[0])
        keep = spud_module_keep()
        with open(log, "a", encoding="utf-8") as f:
            for i in range(keep // 40 + 100):
                f.write("%08d a line of a long run that went on and on\n" % i)
        for _ in range(3):
            log, kept = self.start()
            size = kept.stat().st_size + log.stat().st_size
            self.assertLessEqual(kept.stat().st_size, keep)
            self.assertLess(size, keep + 4096)
        self.assertFalse(kept.with_name("render.log.1.tmp").exists())

    def test_the_kept_tail_is_bounded_and_starts_at_a_line(self):
        keep = spud_module_keep()
        log = self.home.path / ".spud" / "logs" / "render.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        lines = ["%08d a line of a long run that went on and on\n" % i for i in range(keep // 40 + 100)]
        log.write_text("".join(lines), encoding="utf-8")
        self.new_ticket("Long")
        log, kept = self.start()
        text = kept.read_text(encoding="utf-8")
        self.assertLessEqual(len(text.encode("utf-8")), keep)
        self.assertTrue(text.endswith(lines[-1]))
        self.assertIn(text.split("\n", 1)[0] + "\n", lines, "the kept tail starts at a whole line")

    def test_a_stdout_that_is_not_the_render_log_is_left_alone(self):
        self.new_ticket("Elsewhere")
        other = self.home.path / "elsewhere.log"
        other.write_text("kept as it was\n", encoding="utf-8")
        with open(other, "ab") as out:
            proc = subprocess.run([sys.executable, "-I", "-S", str(SPUD), "--as", "spud", "render", "--watch", "--interval", "0.05", "--ticks", "1"],
                                  env=self.home.env, stdout=out, stderr=out, timeout=30)
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertTrue(other.read_text(encoding="utf-8").startswith("kept as it was\n"))
        self.assertFalse((self.home.path / ".spud" / "logs" / "render.log.1").exists())


def spud_module_keep():
    """The bound on render.log.1, read from the program the suite runs (loaded with `spud` above)."""
    return importlib.import_module("spudlib.commands.schedule").RENDER_LOG_KEEP


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


class DeployTest(WatchCase):
    """SPD-119: a merge into the tool's main is a deploy, and the watcher is the one process that does not restart with it.
    On 2026-09-17 SPD-077's merge added migration 0005; the database was migrated past the schema the running watcher had
    loaded, `ledgerdb.connect` refused it on every tick, the loop logged the refusal and went round again, and for an hour the
    watcher held its lock and rendered nothing.  A watcher now ends when the program it loaded changed on disk, or when the
    database is ahead of it, so that launchd's KeepAlive starts it again on the code the database was migrated by."""

    def finished(self, proc):
        """The watcher's exit code and output once it has ended on its own; None while it is still running."""
        if not self.wait_for(lambda: proc.poll() is not None):
            return None
        out, err = proc.communicate(timeout=10)
        return proc.returncode, out, err

    def test_a_database_ahead_of_the_watcher_ends_it_rather_than_stalling_it(self):
        self.new_ticket("Before")
        base = self.render_events()
        proc = self.watcher()
        self.assertTrue(self.wait_for(lambda: self.render_events() == base + 1), "the first pass renders")
        con = self.home.connect()
        try:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            con.execute("PRAGMA user_version = %d" % (version + 1))  # what a newer spud's migration leaves behind
        finally:
            con.close()
        ended = self.finished(proc)
        self.assertIsNotNone(ended, "a watcher whose schema is behind the database must end, not log the refusal every tick")
        code, out, err = ended
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(out.count("error: the database is ahead of this CLI"), 1, out)
        self.assertIn("stopped after 1 pass(es): the database is ahead of this program", out)
        self.assertFalse(spud.watcher_alive(self.ctx()))

    def test_a_watcher_whose_program_changed_on_disk_ends(self):
        tool = self.home.path / "deployed"
        shutil.copytree(SPUD.parent, tool / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        self.new_ticket("Before")
        base = self.render_events()
        proc = subprocess.Popen([sys.executable, "-I", "-S", str(tool / "bin" / "spud"), "--as", "spud", "render", "--watch", "--interval", "0.05"],
                                env=self.home.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, proc)
        self.assertTrue(self.wait_for(lambda: self.render_events() == base + 1), "the first pass renders")
        time.sleep(0.3)
        self.assertIsNone(proc.poll(), "an unchanged program keeps watching")
        changed = tool / "bin" / "spudlib" / "render" / "notefiles.py"  # what SPD-116's merge changed under the watcher
        changed.write_text(changed.read_text(encoding="utf-8") + "\n# deployed\n", encoding="utf-8")
        ended = self.finished(proc)
        self.assertIsNotNone(ended, "a watcher running code that is no longer on disk must end so launchd starts the new code")
        code, out, err = ended
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("stopped after 1 pass(es): the program changed on disk (%s)" % changed, out)
        self.assertFalse(spud.watcher_alive(self.ctx()))


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
