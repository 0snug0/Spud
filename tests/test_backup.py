"""spud backup --daily, doctor's backups line and spud schedule (SPD-012).

`spud backup --daily [--keep N]` writes one ledger-<stamp>-daily.db a local calendar day through the
same VACUUM INTO as `spud backup`, runs PRAGMA quick_check on it, and then keeps the newest N daily
copies (14 by default): it unlinks regular files in .spud/backups/ whose whole name is a daily copy's
and nothing else.  `spud doctor` reports the copies from the directory listing and never counts them
as problems.  `spud schedule show|install|uninstall` manages both LaunchAgents: local.spud.backup,
which runs the daily backup, and local.spud.render, the render watcher (SPD-097).  Every case runs in
a scratch SPUD_HOME whose SPUD_LAUNCH_AGENTS_DIR is a directory of its own, and every schedule case
points that at its scratch and SPUD_LAUNCHCTL at a fake that records its arguments, so no test reaches
this Mac's LaunchAgents or launchctl.
"""

import contextlib
import io
import json
import os
import plistlib
import re
import sqlite3
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from helpers import EXIT_ERROR, EXIT_OK, EXIT_OWNERSHIP, EXIT_USAGE, REPO, SPUD, Home, LaunchdMixin, SpudTestCase, load_spud_module

DAILY_NAME = re.compile(r"ledger-[0-9]{8}T[0-9]{6}-daily\.db")
LABEL = "local.spud.backup"
RENDER_LABEL = "local.spud.render"
PLIST_KEYS = {"Label", "ProgramArguments", "EnvironmentVariables", "RunAtLoad", "StartCalendarInterval",
              "StandardOutPath", "StandardErrorPath", "ProcessType"}

# Sixteen daily copies from December 2029, for the runs whose clock is set to 2030-01-01.
DECEMBER = ["ledger-202912%02dT030000-daily.db" % day for day in range(1, 17)]


def daily_name(day, clock="030000"):
    """The name of a daily copy stamped on `day` (a date) at `clock` (HHMMSS)."""
    return "ledger-%sT%s-daily.db" % (day.strftime("%Y%m%d"), clock)


def earlier_days(n):
    """Daily copy names for the n local days before today, oldest first."""
    today = datetime.now().date()
    return [daily_name(today - timedelta(days=i)) for i in range(n, 0, -1)]


def user_version(path):
    """PRAGMA user_version of a copy, opened read-only and immutable so nothing appears beside it."""
    con = sqlite3.connect("file:%s?mode=ro&immutable=1" % path, uri=True)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


class BackupCase(SpudTestCase):
    warm_cache = False  # SPD-102: a backup's .spud/ holds only what init and the backups put there

    def setUp(self):
        super().setUp()
        self.backups = self.home.path / ".spud" / "backups"

    def seed(self, *names, content=b"a daily copy stand-in"):
        """Files in .spud/backups/; the prune reads names only, so their bytes do not matter."""
        self.backups.mkdir(parents=True, exist_ok=True)
        for name in names:
            (self.backups / name).write_bytes(content)

    def listing(self):
        """Every entry name in .spud/backups/, sorted."""
        return sorted(os.listdir(self.backups)) if self.backups.exists() else []

    def daily(self):
        return [n for n in self.listing() if DAILY_NAME.fullmatch(n) and (self.backups / n).is_file() and not (self.backups / n).is_symlink()]


# =============================================================================
# backup --daily, as the LaunchAgent runs it
# =============================================================================


class DailyBackupTest(BackupCase):
    def backup_daily(self, *args):
        """(exit code, the --json answer, the process) of `spud --as spud backup --daily ...`."""
        proc = self.home.run("--json", "backup", "--daily", *args, actor="spud", check=False)
        return proc.returncode, (json.loads(proc.stdout) if proc.stdout.strip() else None), proc

    def test_daily_writes_one_checked_copy_a_day(self):
        code, out, proc = self.backup_daily()
        self.assertEqual(code, EXIT_OK, proc)
        path = Path(out["path"])
        self.assertEqual(path.parent, self.backups)
        self.assertRegex(path.name, r"^ledger-\d{8}T\d{6}-daily\.db$")
        self.assertEqual(out, {"ok": True, "path": str(path), "written": True, "pruned": [], "kept": 1})
        self.assertEqual(self.listing(), [path.name])  # the check leaves no -wal, -shm or -journal beside the copy
        self.assertEqual(user_version(path), 4)  # schema v4 since SPD-098
        code, again, proc = self.backup_daily()
        text = self.home.run("backup", "--daily", actor="spud").stdout
        if datetime.now().strftime("%Y%m%d") != path.name[7:15]:
            self.skipTest("the local date changed between the runs")
        self.assertEqual(code, EXIT_OK, proc)
        self.assertEqual(again, {"ok": True, "path": str(path), "written": False, "pruned": [], "kept": 1})
        self.assertIn("already exists", text)
        self.assertIn(str(path), text)
        self.assertEqual(self.listing(), [path.name])

    def test_the_fifteenth_day_prunes_the_oldest_copy(self):
        seeds = earlier_days(14)
        self.seed(*seeds)
        code, out, proc = self.backup_daily()
        self.assertEqual(code, EXIT_OK, proc)
        new = Path(out["path"]).name
        self.assertEqual((out["written"], out["pruned"], out["kept"]), (True, [seeds[0]], 14))
        self.assertEqual(self.listing(), seeds[1:] + [new])

    def test_the_text_names_the_copy_what_was_pruned_and_how_many_are_kept(self):
        seeds = earlier_days(15)  # fifteen and the new copy: the two oldest go
        self.seed(*seeds)
        proc = self.home.run("backup", "--daily", actor="spud")
        new = [n for n in self.listing() if n not in seeds]
        self.assertEqual(len(new), 1, self.listing())
        self.assertIn(str(self.backups / new[0]), proc.stdout)
        self.assertIn("quick_check ok", proc.stdout)
        self.assertIn("pruned 2 daily copies", proc.stdout)
        self.assertIn("14 kept", proc.stdout)
        for name in seeds[:2]:
            self.assertIn(name, proc.stdout)
        self.assertEqual(self.listing(), seeds[2:] + new)

    def test_keep_keeps_the_newest_n_daily_copies(self):
        seeds = earlier_days(5)
        self.seed(*seeds)
        code, out, proc = self.backup_daily("--keep", "3")
        self.assertEqual(code, EXIT_OK, proc)
        new = Path(out["path"]).name
        self.assertEqual((out["written"], out["pruned"], out["kept"]), (True, seeds[:3], 3))
        self.assertEqual(self.listing(), seeds[3:] + [new])

    def test_keep_below_one_or_without_daily_is_a_usage_error(self):
        seeds = earlier_days(3)
        self.seed(*seeds)
        for args in (("--daily", "--keep", "0"), ("--daily", "--keep", "-2"), ("--keep", "3"), ("--keep", "0"), ("--daily", "--keep", "three")):
            with self.subTest(args=args):
                proc = self.home.run("backup", *args, actor="spud", check=False)
                self.assertEqual(proc.returncode, EXIT_USAGE, proc)
                self.assertEqual(self.listing(), seeds)  # nothing written, nothing pruned

    def test_the_prune_unlinks_old_daily_copies_and_nothing_else(self):
        old = earlier_days(3)
        self.seed(*old)
        # The look-alikes carry the oldest stamps: a matcher that took any of them for a daily copy would prune it first.
        spared = [
            "ledger-20200101T000000.db",  # a manual copy, from spud backup
            "ledger-20200101T000001-pre-0001_init.db",  # a pre-migration copy
            "ledger-2020bad-daily.db",
            "ledger-20200101T000000-daily.db.bak",
            "ledger-20200102T000000-daily.DB",
            "Ledger-20200103T000000-daily.db",
            "xledger-20200104T000000-daily.db",
            " ledger-20200105T000000-daily.db",
            "ledger-20200106T0000000-daily.db",
            "ledger-2020010T000000-daily.db",
            "ledger-20200107T000000-daily.db-wal",
            "ledger-20200108T000000-daily.db-shm",
            "ledger-20200109T000000-daily.db-journal",
            "ledger-20200110T000000-daily.db\n",
            "ledger-٢٠٢٠٠١١١T000000-daily.db",  # digits, but not ASCII ones
            "notes.txt",
        ]
        self.seed(*spared)
        directory = self.backups / "ledger-20190101T000000-daily.db"  # a directory named like a daily copy
        directory.mkdir()
        (directory / "inside.db").write_bytes(b"x")
        target = self.home.path / "elsewhere.db"
        target.write_bytes(b"x")
        link = self.backups / "ledger-20180101T000000-daily.db"  # a symlink named like one is not a regular file
        link.symlink_to(target)
        outside = [self.home.path / ".spud" / "ledger-20170101T000000-daily.db", self.home.path / "ledger-20170101T000000-daily.db"]
        for path in outside:
            path.write_bytes(b"x")
        # A reader holds the live database open, so its -wal and -shm are on disk while the run prunes.
        con = sqlite3.connect(self.home.db)
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT count(*) FROM projects").fetchone()[0], 1)
        live = [self.home.db, Path("%s-wal" % self.home.db), Path("%s-shm" % self.home.db)]
        for path in live:
            self.assertTrue(path.is_file(), path)
        code, out, proc = self.backup_daily("--keep", "1")
        self.assertEqual(code, EXIT_OK, proc)
        new = Path(out["path"]).name
        self.assertEqual((out["written"], out["pruned"], out["kept"]), (True, old, 1))
        self.assertEqual(self.listing(), sorted(spared + [directory.name, link.name, new]))
        self.assertTrue((directory / "inside.db").is_file())
        self.assertTrue(link.is_symlink())
        self.assertTrue(target.is_file())
        for path in outside + live:
            self.assertTrue(path.is_file(), path)
        self.assertEqual(con.execute("SELECT count(*) FROM projects").fetchone()[0], 1)
        self.assertEqual(self.home.scalar("PRAGMA user_version"), 4)

    def test_plain_backup_writes_a_manual_copy_and_prunes_nothing(self):
        seeds = earlier_days(20)
        self.seed(*seeds)
        out = self.home.json("backup")
        self.assertEqual(set(out), {"ok", "path"})
        name = Path(out["path"]).name
        self.assertRegex(name, r"^ledger-\d{8}T\d{6}\.db$")
        self.assertEqual(self.listing(), sorted(seeds + [name]))


class DailyBackupInProcessTest(BackupCase):
    """The clock and the faults, injected into bin/spud loaded as a module."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.spud = load_spud_module()

    def main(self, *argv, stamp):
        """(exit code, the --json answer) of `spud --json --as spud backup --daily ...` run in this process
        against the scratch home, with backup_stamp() answering `stamp`."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"SPUD_HOME": str(self.home.path)}))
            stack.enter_context(mock.patch("spudlib.state.backup.backup_stamp", lambda: stamp))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = self.spud.main(["--json", "--as", "spud", "backup", "--daily", *argv])
        return code, json.loads(out.getvalue())

    def test_the_day_is_the_local_date_of_the_stamp(self):
        code, out = self.main(stamp="20300101T030000")
        self.assertEqual((code, out["written"], Path(out["path"]).name), (EXIT_OK, True, "ledger-20300101T030000-daily.db"))
        code, out = self.main(stamp="20300101T235959")
        self.assertEqual((code, out["written"], Path(out["path"]).name, out["pruned"], out["kept"]),
                         (EXIT_OK, False, "ledger-20300101T030000-daily.db", [], 1))
        code, out = self.main("--keep", "1", stamp="20300102T000001")
        self.assertEqual((code, out["written"], out["pruned"], out["kept"]), (EXIT_OK, True, ["ledger-20300101T030000-daily.db"], 1))
        self.assertEqual(self.listing(), ["ledger-20300102T000001-daily.db"])

    def test_the_copy_just_written_is_never_pruned_even_beside_a_later_stamp(self):
        self.seed("ledger-20991231T030000-daily.db")  # left by a clock that once ran ahead
        code, out = self.main("--keep", "1", stamp="20300101T030000")
        self.assertEqual((code, out["written"], out["pruned"], out["kept"]), (EXIT_OK, True, [], 2))
        self.assertEqual(self.listing(), ["ledger-20300101T030000-daily.db", "ledger-20991231T030000-daily.db"])

    def test_a_failed_write_prunes_nothing_and_leaves_no_copy(self):
        self.seed(*DECEMBER)
        real = self.spud.do_backup

        def partial_copy_then_disk_full(ctx, con, label=None, stamp=None):
            path = real(ctx, con, label, stamp=stamp)
            with open(path, "r+b") as f:
                f.truncate(512)
            raise sqlite3.OperationalError("database or disk is full")

        def nothing_written(ctx, con, label=None, stamp=None):
            raise OSError(28, "No space left on device")

        for fault in (partial_copy_then_disk_full, nothing_written):
            with self.subTest(fault=fault.__name__), mock.patch("spudlib.state.backup.do_backup", fault):
                code, out = self.main("--keep", "1", stamp="20300101T030000")
                self.assertEqual(code, EXIT_ERROR)
                self.assertEqual((out["ok"], out["written"], out["pruned"], out["kept"]), (False, False, [], 16))
                self.assertIn("nothing pruned", out["error"])
                self.assertEqual(self.listing(), DECEMBER)

    def test_an_entry_in_the_way_of_todays_name_is_left_alone(self):
        self.seed(*DECEMBER)
        blocker = self.backups / "ledger-20300101T030000-daily.db"
        blocker.mkdir()
        (blocker / "keep.me").write_bytes(b"x")
        code, out = self.main("--keep", "1", stamp="20300101T030000")
        self.assertEqual((code, out["written"], out["pruned"], out["kept"]), (EXIT_ERROR, False, [], 16))
        self.assertIn("already exists", out["error"])
        self.assertTrue((blocker / "keep.me").is_file())
        self.assertEqual(self.listing(), sorted(DECEMBER + [blocker.name]))

    def test_a_failing_quick_check_removes_the_new_copy_and_prunes_nothing(self):
        self.seed(*DECEMBER)
        real = self.spud.do_backup

        def damage_page_two(path):
            data = bytearray(path.read_bytes())
            size = int.from_bytes(data[16:18], "big")
            size = 65536 if size == 1 else size
            self.assertGreater(len(data), 2 * size)
            data[size:2 * size] = b"\xff" * size  # a b-tree page with no valid page type
            path.write_bytes(bytes(data))

        def not_a_database(path):
            path.write_bytes(b"this is not an SQLite database\n" * 100)

        for damage in (damage_page_two, not_a_database):
            def damaging(ctx, con, label=None, stamp=None, damage=damage):
                path = real(ctx, con, label, stamp=stamp)
                damage(path)
                return path

            with self.subTest(damage=damage.__name__), mock.patch("spudlib.state.backup.do_backup", damaging):
                code, out = self.main("--keep", "1", stamp="20300101T030000")
                self.assertEqual(code, EXIT_ERROR)
                self.assertEqual((out["written"], out["pruned"], out["kept"]), (False, [], 16))
                self.assertTrue(out["quick_check"])
                self.assertNotEqual(out["quick_check"], ["ok"])
                self.assertIn("quick_check", out["error"])
                self.assertEqual(self.listing(), DECEMBER)  # the damaged copy is gone, and nothing was left beside it

    def test_quick_check_reads_a_sound_copy_as_ok(self):
        code, out = self.main(stamp="20300101T030000")
        self.assertEqual(code, EXIT_OK)
        path = Path(out["path"])
        self.assertEqual(self.spud.backup_quick_check(path), ["ok"])
        self.assertEqual(self.listing(), [path.name])


# =============================================================================
# doctor
# =============================================================================


class DoctorBackupsTest(BackupCase):
    def test_without_a_backups_directory_doctor_reports_no_copies(self):
        self.assertFalse(self.backups.exists())
        out = self.home.json("doctor")
        self.assertEqual(out["backups"], {"dir": str(self.backups), "daily": {"count": 0, "newest": None, "oldest": None}, "other": 0})
        self.assertEqual(out["problems"], [])
        self.assertIn("backups     0 daily, 0 other", self.home.run("doctor").stdout.splitlines())
        self.assertFalse(self.backups.exists())

    def test_doctor_counts_daily_and_other_copies_and_finds_no_problem_in_them(self):
        daily = ["ledger-20200105T030000-daily.db", "ledger-20200103T030000-daily.db", "ledger-20200104T030000-daily.db"]
        other = ["ledger-20200101T120000.db", "ledger-20200101T120001-pre-0001_init.db", "ledger-2020bad-daily.db"]
        neither = ["notes.txt", "ledger-20200106T030000-daily.db-wal", "ledger-20200107T030000-daily.db.bak"]
        self.seed(*daily, *other, *neither)
        (self.backups / "ledger-20200108T030000-daily.db").mkdir()
        before = self.listing()
        out = self.home.json("doctor")  # copies that stopped in 2020 are no problem: a Mac can be switched off
        self.assertEqual(out["backups"], {
            "dir": str(self.backups),
            "daily": {"count": 3, "newest": "ledger-20200105T030000-daily.db", "oldest": "ledger-20200103T030000-daily.db"},
            "other": 3,
        })
        self.assertEqual(out["problems"], [])
        self.assertIn("backups     3 daily (newest ledger-20200105T030000-daily.db, oldest ledger-20200103T030000-daily.db), 3 other",
                      self.home.run("doctor").stdout.splitlines())
        self.assertEqual(self.listing(), before)

    def test_doctor_counts_what_the_backup_commands_write(self):
        self.home.run("backup", "--daily", actor="spud")
        self.home.run("backup")
        backups = self.home.json("doctor")["backups"]
        self.assertEqual((backups["daily"]["count"], backups["other"]), (1, 1))
        self.assertEqual((backups["daily"]["newest"], backups["daily"]["oldest"]), (self.daily()[0], self.daily()[0]))

    def test_doctor_without_a_database_still_reports_the_backups(self):
        home = Home()
        self.addCleanup(home.cleanup)
        proc = home.run("--json", "doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        out = json.loads(proc.stdout)
        self.assertEqual(out["backups"]["daily"], {"count": 0, "newest": None, "oldest": None})
        self.assertEqual(out["backups"]["other"], 0)
        self.assertFalse(any("backup" in problem for problem in out["problems"]), out["problems"])


# =============================================================================
# schedule: the LaunchAgent, against a scratch directory and a fake launchctl
# =============================================================================

class ScheduleTest(LaunchdMixin, SpudTestCase):
    def setUp(self):
        super().setUp()
        self.setup_launchd()
        self.plist_path = self.agents / (LABEL + ".plist")
        self.uid = os.getuid()
        self.service = "gui/%d/%s" % (self.uid, LABEL)
        self.render_plist_path = self.agents / (RENDER_LABEL + ".plist")
        self.render_service = "gui/%d/%s" % (self.uid, RENDER_LABEL)
        self.interpreter = self.home.json("doctor")["interpreter"]["path"]  # the CLI's own sys.executable

    def schedule(self, *args, actor="spud", json_mode=True):
        """`spud schedule ...` as a process; it reaches only the scratch LaunchAgents directory and the fake launchctl."""
        self.assertEqual(self.home.env["SPUD_LAUNCHCTL"], str(self.launchctl))
        self.assertEqual(self.home.env["SPUD_LAUNCH_AGENTS_DIR"], str(self.agents))
        return self.home.run(*((["--json"] if json_mode else []) + ["schedule", *args]), actor=actor, check=False)

    def answer(self, *args, actor="spud"):
        proc = self.schedule(*args, actor=actor)
        return proc.returncode, (json.loads(proc.stdout) if proc.stdout.strip() else None), proc

    def calls(self):
        path = self.state / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def expected_plist(self, hour=3, minute=0, interpreter=None):
        log = os.path.join(os.path.expanduser("~"), "Library", "Logs", "spud-backup.log")
        return {
            "Label": LABEL,
            "ProgramArguments": [interpreter or self.interpreter, "-I", "-S", str(self.home.path / "bin" / "spud"), "--as", "spud", "backup", "--daily"],
            "EnvironmentVariables": {"SPUD_HOME": str(self.home.path)},
            "RunAtLoad": True,
            "StartCalendarInterval": {"Hour": hour, "Minute": minute},
            "StandardOutPath": log,
            "StandardErrorPath": log,
            "ProcessType": "Background",
        }

    def test_show_prints_the_plist_install_would_write_and_writes_nothing(self):
        code, out, proc = self.answer("show")
        self.assertEqual(code, EXIT_OK, proc)
        plist = plistlib.loads(out["plist"].encode("utf-8"))
        self.assertEqual(set(plist), PLIST_KEYS)
        self.assertEqual(plist, self.expected_plist())
        self.assertEqual({k: out[k] for k in ("label", "path", "at", "exists", "matches", "loaded")},
                         {"label": LABEL, "path": str(self.plist_path), "at": "03:00", "exists": False, "matches": False, "loaded": False})
        self.assertFalse(self.agents.exists())
        self.assertEqual(self.calls(), [["print", self.service], ["print", self.render_service]])
        text = self.schedule("show", json_mode=False).stdout
        xml = text[text.index("<?xml"):text.index("</plist>") + len("</plist>")]
        self.assertEqual(plistlib.loads(xml.encode("utf-8")), self.expected_plist())
        self.assertIn(str(self.plist_path), text)
        self.assertIn("not installed", text)
        self.assertFalse(self.agents.exists())

    def test_the_plist_names_the_interpreter_as_given_not_resolved(self):
        link = self.scratch / "bin" / "python3.14"
        link.parent.mkdir()
        link.symlink_to(os.path.realpath(sys.executable))
        proc = subprocess.run([str(link), "-I", "-S", str(SPUD), "--json", "--as", "spud", "schedule", "show"],
                              capture_output=True, text=True, env=self.home.env)
        self.assertEqual(proc.returncode, EXIT_OK, proc)
        plist = plistlib.loads(json.loads(proc.stdout)["plist"].encode("utf-8"))
        self.assertEqual(plist["ProgramArguments"][0], str(link))
        self.assertEqual(plist, self.expected_plist(interpreter=str(link)))

    def test_the_plists_program_runs_the_daily_backup_in_launchds_minimal_environment(self):
        code, out, proc = self.answer("show")
        self.assertEqual(code, EXIT_OK, proc)
        plist = plistlib.loads(out["plist"].encode("utf-8"))
        script = Path(plist["ProgramArguments"][3])
        self.assertEqual(script, self.home.path / "bin" / "spud")
        script.parent.mkdir()
        script.symlink_to(SPUD)  # the scratch home's bin/spud is this checkout's
        # What launchd gives an agent: the plist's EnvironmentVariables, a minimal PATH, and / as the working directory.
        env = dict(plist["EnvironmentVariables"], PATH="/usr/bin:/bin:/usr/sbin:/sbin")
        first = subprocess.run(plist["ProgramArguments"], capture_output=True, text=True, env=env, cwd="/")
        self.assertEqual(first.returncode, EXIT_OK, first)
        self.assertIn("daily backup written to %s" % (self.home.path / ".spud" / "backups"), first.stdout)
        second = subprocess.run(plist["ProgramArguments"], capture_output=True, text=True, env=env, cwd="/")
        written = [n for n in os.listdir(self.home.path / ".spud" / "backups") if DAILY_NAME.fullmatch(n)]
        if datetime.now().strftime("%Y%m%d") != written[0][7:15]:
            self.skipTest("the local date changed between the runs")
        self.assertEqual(second.returncode, EXIT_OK, second)
        self.assertIn("already exists", second.stdout)
        self.assertEqual(len(written), 1)

    def test_install_writes_the_plist_then_boots_out_and_bootstraps(self):
        code, out, proc = self.answer("install")
        self.assertEqual(code, EXIT_OK, proc)
        self.assertEqual(plistlib.loads(self.plist_path.read_bytes()), self.expected_plist())
        self.assertEqual(sorted(os.listdir(self.agents)), [LABEL + ".plist", RENDER_LABEL + ".plist"])  # the temporary file was renamed over it
        self.assertEqual(self.plist_path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.calls(), [["bootout", self.service], ["bootstrap", "gui/%d" % self.uid, str(self.plist_path)],
                                        ["bootout", self.render_service], ["bootstrap", "gui/%d" % self.uid, str(self.render_plist_path)]])
        self.assertEqual({k: out[k] for k in ("label", "path", "at", "replaced", "booted_out", "attempts")},
                         {"label": LABEL, "path": str(self.plist_path), "at": "03:00", "replaced": False, "booted_out": False, "attempts": 1})
        code, shown, proc = self.answer("show")
        self.assertEqual((shown["exists"], shown["matches"], shown["loaded"]), (True, True, True))
        self.assertEqual(shown["plist"].encode("utf-8"), self.plist_path.read_bytes())

    def test_install_at_sets_the_hour_and_minute(self):
        code, out, proc = self.answer("install", "--at", "04:30")
        self.assertEqual(code, EXIT_OK, proc)
        plist = plistlib.loads(self.plist_path.read_bytes())
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 4, "Minute": 30})
        self.assertEqual(plist, self.expected_plist(4, 30))
        self.assertEqual(out["at"], "04:30")
        code, shown, proc = self.answer("show")
        self.assertEqual((shown["exists"], shown["matches"], shown["loaded"]), (True, False, True))  # show compares with 03:00
        code, shown, proc = self.answer("show", "--at", "04:30")
        self.assertEqual((shown["at"], shown["exists"], shown["matches"], shown["loaded"]), ("04:30", True, True, True))

    def test_a_bad_at_is_a_usage_error(self):
        for bad in ("25:00", "24:00", "03:60", "3pm", "0300", "", "03:00:00", "-1:00", " 03:00", "03:0", "٠٣:٠٠"):
            for verb in ("install", "show"):
                with self.subTest(verb=verb, at=bad):
                    proc = self.schedule(verb, "--at", bad)
                    self.assertEqual(proc.returncode, EXIT_USAGE, proc)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.agents.exists())

    def test_a_second_install_replaces_the_plist_and_reloads(self):
        self.assertEqual(self.schedule("install").returncode, EXIT_OK)
        code, out, proc = self.answer("install", "--at", "05:15")
        self.assertEqual(code, EXIT_OK, proc)
        self.assertEqual((out["replaced"], out["booted_out"], out["attempts"]), (True, True, 1))
        self.assertEqual(plistlib.loads(self.plist_path.read_bytes()), self.expected_plist(5, 15))
        self.assertEqual(sorted(os.listdir(self.agents)), [LABEL + ".plist", RENDER_LABEL + ".plist"])
        self.assertEqual([c[0] for c in self.calls()], ["bootout", "bootstrap"] * 4)  # both agents, twice

    def test_uninstall_boots_out_and_removes_the_plist(self):
        self.assertEqual(self.schedule("install").returncode, EXIT_OK)
        code, out, proc = self.answer("uninstall")
        self.assertEqual(code, EXIT_OK, proc)
        self.assertEqual({k: out[k] for k in ("label", "path", "booted_out", "removed")},
                         {"label": LABEL, "path": str(self.plist_path), "booted_out": True, "removed": True})
        self.assertFalse(self.plist_path.exists())
        self.assertEqual(self.calls()[-2:], [["bootout", self.service], ["bootout", self.render_service]])
        code, shown, proc = self.answer("show")
        self.assertEqual((shown["exists"], shown["matches"], shown["loaded"]), (False, False, False))
        code, out, proc = self.answer("uninstall")
        self.assertEqual((code, out["booted_out"], out["removed"]), (EXIT_OK, False, False))
        self.assertIn("not installed", self.schedule("uninstall", json_mode=False).stdout)

    def test_a_failing_bootstrap_is_retried_then_exits_1_with_launchctls_stderr(self):
        self.home.env["FAKE_LAUNCHCTL_BOOTSTRAP_FAILURES"] = "all"
        started = time.monotonic()
        proc = self.schedule("install")
        elapsed = time.monotonic() - started
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        self.assertIn("Bootstrap failed: 5: Input/output error", proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual((out["ok"], out["exit"], out["attempts"]), (False, EXIT_ERROR, 5))
        self.assertIn("Bootstrap failed: 5: Input/output error", out["error"])
        self.assertEqual([c[0] for c in self.calls()], ["bootout"] + ["bootstrap"] * 5)
        self.assertGreaterEqual(elapsed, 1.5)

    def test_a_bootstrap_that_fails_right_after_the_bootout_is_retried(self):
        self.home.env["FAKE_LAUNCHCTL_BOOTSTRAP_FAILURES"] = "2"
        code, out, proc = self.answer("install")
        self.assertEqual(code, EXIT_OK, proc)
        self.assertEqual(out["attempts"], 3)
        self.assertEqual([c[0] for c in self.calls()], ["bootout"] + ["bootstrap"] * 3 + ["bootout"] + ["bootstrap"] * 3)
        code, shown, proc = self.answer("show")
        self.assertTrue(shown["loaded"])

    def test_install_writes_and_loads_the_render_watcher_too(self):
        code, out, proc = self.answer("install")
        self.assertEqual(code, EXIT_OK, proc)
        render_path = self.agents / (RENDER_LABEL + ".plist")
        self.assertEqual((out["render"]["label"], out["render"]["path"], out["render"]["replaced"]), (RENDER_LABEL, str(render_path), False))
        plist = plistlib.loads(render_path.read_bytes())
        self.assertEqual(plist["ProgramArguments"], [self.interpreter, "-I", "-S", str(self.home.path / "bin" / "spud"), "--as", "spud", "render", "--watch"])
        self.assertEqual((plist["RunAtLoad"], plist["KeepAlive"], plist["EnvironmentVariables"], plist["StandardOutPath"], plist["StandardErrorPath"]),
                         (True, True, {"SPUD_HOME": str(self.home.path)}, str(self.home.path / ".spud" / "logs" / "render.log"), str(self.home.path / ".spud" / "logs" / "render.log")))
        self.assertTrue((self.home.path / ".spud" / "logs").is_dir())
        calls = [c[:2] for c in self.calls()]
        self.assertEqual(calls, [["bootout", self.service], ["bootstrap", "gui/%d" % self.uid], ["bootout", "gui/%d/%s" % (self.uid, RENDER_LABEL)], ["bootstrap", "gui/%d" % self.uid]])
        shown = self.answer("show")[1]
        self.assertEqual((shown["render"]["exists"], shown["render"]["matches"], shown["render"]["loaded"]), (True, True, True))
        self.assertIn("KeepAlive", shown["render"]["plist"])
        text = self.schedule("show", json_mode=False).stdout
        self.assertIn("label       %s" % RENDER_LABEL, text)
        self.assertIn("whenever it exits (KeepAlive)", text)
        gone = self.answer("uninstall")[1]
        self.assertFalse(render_path.exists())
        self.assertFalse(self.plist_path.exists())
        self.assertEqual((gone["removed"], gone["render"]["removed"]), (True, True))

    def test_schedule_is_spuds_in_the_cli(self):
        t = self.new_ticket("Backups", status="active")
        m = self.new_member(t["key"])
        for args in (("show",), ("install",), ("install", "--at", "04:30"), ("uninstall",)):
            with self.subTest(args=args):
                proc = self.schedule(*args, actor=None)
                self.assertEqual(proc.returncode, EXIT_ERROR, proc)
                self.assertIn("--as spud", proc.stderr)
                for actor in (m["ref"], "0123456789abcdef0"):
                    proc = self.schedule(*args, actor=actor)
                    self.assertEqual(proc.returncode, EXIT_OWNERSHIP, proc)
                    self.assertIn("Spud's", proc.stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.agents.exists())


class HelpTest(unittest.TestCase):
    def test_backup_and_schedule_help_render(self):
        home = Home()
        self.addCleanup(home.cleanup)
        text = home.run("backup", "--help").stdout
        for needle in ("--daily", "--keep N", "quick_check", "ledger-<YYYYMMDD>T<HHMMSS>-daily.db", "exit codes"):
            self.assertIn(needle, text)
        text = home.run("schedule", "--help").stdout
        for needle in ("show", "install", "uninstall", "SPUD_LAUNCH_AGENTS_DIR", "SPUD_LAUNCHCTL", LABEL, "--as spud"):
            self.assertIn(needle, text)
        for verb in ("show", "install"):
            self.assertIn("--at HH:MM", home.run("schedule", verb, "--help").stdout)
        self.assertIn("schedule", home.run("--help").stdout)


class GitignoreTest(unittest.TestCase):
    def test_gitignore_lists_the_spud_directory(self):
        lines = [line.strip() for line in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()]
        self.assertIn(".spud/", lines)


if __name__ == "__main__":
    unittest.main()
