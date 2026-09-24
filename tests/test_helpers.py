"""The harness's own guarantees: what tests/helpers.py puts between the suite and this machine.

SPD-097's and SPD-133's guards -- the home, the config directory, the tool and `~/.claude` that the test process itself
names -- are pinned by test_home.RealHomeGuardTest, beside the home resolution they protect.  This module is SPW-011's:
the refusing launchctl, which was the last machine-wide thing helpers guarded nowhere.

`spud schedule show|install|uninstall`, `spud init`'s step 8 and `spud home move` run $SPUD_LAUNCHCTL
(commands/schedule.launchctl, default /bin/launchctl) against gui/<uid> and the labels local.spud.backup and
local.spud.render -- this Mac's user domain and this Mac's two running jobs.  SPUD_LAUNCH_AGENTS_DIR moves the plist a
test writes and nothing moves the job a `bootout` removes, so a fixture that reached `schedule install` without
LaunchdMixin unloaded the real daily backup and the real render watcher, whatever else it had overridden.  The guard is
a program that refuses and says which fixture forgot; `fake_launchctl` (LaunchdMixin here, MachineMixin in test_init)
replaces it with the recording fake, and those two are pinned below and in test_init.ScheduleStepTest.

SPD-233's are the last two classes: the home every SpudTestCase starts from, in today's shape at one path per process,
and the Snapshot restore that puts it back before each test and forgets what the process cached about it.
"""

import importlib
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import helpers
from helpers import (EXIT_ERROR, EXIT_OK, GUARD_LAUNCHCTL, GUARD_LAUNCHCTL_EXIT, GUARD_LAUNCHCTL_REFUSAL, Home,
                     LaunchdMixin, SpudTestCase)

LABEL = "local.spud.backup"
RENDER_LABEL = "local.spud.render"
# Everything the refusal must carry, so a reader of a failing fixture's stderr is told what to do rather than only that
# something said no: what refused, what it refused, why it is not launchctl's business, and the two ways out.
WAY_OUT = ("SPUD_LAUNCHCTL", "LaunchdMixin", "fake_launchctl", "--no-schedule", LABEL, RENDER_LABEL,
           "SPUD_LAUNCH_AGENTS_DIR")


class GuardLaunchctlProgramTest(unittest.TestCase):
    """The stub itself, and the environment this test process hands a CLI run that sets up no home of its own."""

    def test_the_test_process_names_the_refusing_stub(self):
        self.assertEqual(os.environ["SPUD_LAUNCHCTL"], str(GUARD_LAUNCHCTL))
        self.assertTrue(GUARD_LAUNCHCTL.is_file(), GUARD_LAUNCHCTL)
        self.assertTrue(os.access(GUARD_LAUNCHCTL, os.X_OK), GUARD_LAUNCHCTL)
        self.assertNotEqual(str(GUARD_LAUNCHCTL), "/bin/launchctl")

    def test_it_refuses_every_verb_the_program_runs_and_says_what_to_do(self):
        """schedule's three verbs between them run `print`, `bootout` and `bootstrap`; the stub reads none of them."""
        domain = "gui/%d" % os.getuid()
        for args in ([], ["print", "%s/%s" % (domain, LABEL)], ["bootout", "%s/%s" % (domain, RENDER_LABEL)],
                     ["bootstrap", domain, "/tmp/local.spud.backup.plist"]):
            with self.subTest(args=args):
                proc = subprocess.run([str(GUARD_LAUNCHCTL), *args], capture_output=True, text=True)
                self.assertEqual(proc.returncode, GUARD_LAUNCHCTL_EXIT, proc)
                self.assertNotEqual(proc.returncode, EXIT_OK)
                self.assertEqual(proc.stdout, "")  # nothing a caller could read as launchd's answer
                self.assertTrue(proc.stderr.startswith(GUARD_LAUNCHCTL_REFUSAL), proc.stderr)
                for word in args:  # what it refused, so the failing call names itself
                    self.assertIn(word, proc.stderr)
                for word in WAY_OUT:
                    self.assertIn(word, proc.stderr, word)
                self.assertIn(domain, proc.stderr)

    def test_a_home_that_sets_up_no_launchctl_still_names_the_stub(self):
        home = Home()
        self.addCleanup(home.cleanup)
        self.assertEqual(home.env["SPUD_LAUNCHCTL"], str(GUARD_LAUNCHCTL))


class PlainHomeTest(SpudTestCase):
    """A Home with no LaunchdMixin: the two schedule verbs that reach launchctl answer from this home, not this Mac."""

    def test_show_reads_the_stub_and_reports_this_homes_own_state(self):
        """`launchctl print` through the stub exits non-zero, which is this scratch home's truth -- no job of its own is
        loaded anywhere.  /bin/launchctl would have answered for this Mac's two jobs, which are usually loaded."""
        out = self.home.json("schedule", "show", actor="spud")
        self.assertEqual((out["label"], out["exists"], out["loaded"]), (LABEL, False, False))
        self.assertEqual((out["render"]["label"], out["render"]["exists"], out["render"]["loaded"]),
                         (RENDER_LABEL, False, False))
        for block in (out, out["render"]):
            self.assertTrue(block["path"].startswith(str(self.home.path)), block["path"])

    def test_install_refuses_out_loud_instead_of_booting_out_this_macs_jobs(self):
        proc = self.home.run("schedule", "install", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        self.assertIn(GUARD_LAUNCHCTL_REFUSAL, proc.stderr)
        for word in WAY_OUT:
            self.assertIn(word, proc.stderr, word)
        # it failed at the bootstrap of its own plist, under this home's LaunchAgents and never ~/Library's
        plist = self.home.path / "LaunchAgents" / (LABEL + ".plist")
        self.assertTrue(plist.is_file())
        self.assertIn("launchctl bootstrap gui/%d %s failed" % (os.getuid(), plist), proc.stderr)
        self.assertNotIn(os.path.expanduser("~/Library/LaunchAgents"), proc.stderr)


class LaunchdMixinTest(LaunchdMixin, SpudTestCase):
    """LaunchdMixin unchanged by SPW-011: the recording fake replaces the guard, and the guard is what it replaces."""

    def test_setup_launchd_replaces_the_guard_with_the_recording_fake(self):
        self.assertEqual(self.home.env["SPUD_LAUNCHCTL"], str(GUARD_LAUNCHCTL))  # before
        self.setup_launchd()
        self.assertEqual(self.home.env["SPUD_LAUNCHCTL"], str(self.launchctl))  # after
        self.assertNotEqual(str(self.launchctl), str(GUARD_LAUNCHCTL))
        self.assertEqual(self.home.env["FAKE_LAUNCHCTL_STATE"], str(self.state))

    def test_the_fake_records_the_calls_the_guard_would_have_refused(self):
        self.setup_launchd()
        self.assertEqual(self.home.run("schedule", "show", actor="spud", check=False).returncode, EXIT_OK)
        self.assertEqual([c[0] for c in self.launchctl_calls()], ["print", "print"])
        self.assertEqual(self.home.json("schedule", "install", actor="spud")["label"], LABEL)
        self.assertEqual([c[0] for c in self.launchctl_calls()],
                         ["print", "print", "bootout", "bootstrap", "bootout", "bootstrap"])


class FixtureShapeTest(SpudTestCase):
    """SPD-233: the home every SpudTestCase starts from is today's shape -- a home beside the tool checkout, project 1 the
    tool, registered by `spud init` itself -- at the one path its process keeps for this config."""

    def test_the_home_and_the_tool_are_siblings_and_project_1_is_the_tool(self):
        home, tool = self.home.path, self.home.tool
        self.assertEqual((home.parent, tool.parent.parent), (self.home.root, self.home.root))
        self.assertFalse(tool.is_relative_to(home) or home.is_relative_to(tool))
        self.assertTrue((tool / ".git").is_dir())
        self.assertFalse((home / ".git").exists())
        self.assertEqual(self.home.env["SPUD_TOOL_DIR"], str(tool))
        self.assertEqual(self.home.launcher, tool / "bin" / "spud")
        [row] = self.home.rows("SELECT id, key, root_path, sessions FROM projects")
        self.assertEqual(row, {"id": 1, "key": "spud", "root_path": str(tool), "sessions": "claim"})  # init's default
        added = self.home.rows("SELECT actor, json_extract(data, '$.root') AS root FROM events WHERE kind = 'project.added'")
        self.assertEqual(added, [{"actor": "spud", "root": str(tool)}])  # init's step 3, not a row written behind its back

    def test_every_test_of_the_process_starts_at_the_same_path(self):
        again = helpers.fixture().restore()
        self.assertEqual((again.path, again.tool), (self.home.path, self.home.tool))

    def test_a_change_that_keeps_the_size_and_the_time_does_not_reach_the_next_test(self):
        """SPD-233 review F2, at the fixture: a same-size write whose modification time is put back, in this process and by
        a program the test runs, is gone when the next test's restore (fixture().restore(), as setUp calls it) is done."""
        config, claude = self.home.path / "spud.config.json", self.home.path / "CLAUDE.md"
        before = {p: p.read_bytes() for p in (config, claude)}
        was = config.stat()
        config.write_bytes(before[config].swapcase())
        os.utime(config, ns=(was.st_atime_ns, was.st_mtime_ns))
        scratch = Path(tempfile.mkdtemp(prefix="spud-stamp-")).resolve()
        self.addCleanup(shutil.rmtree, scratch, True)
        subprocess.run(["/bin/sh", "-c", 'touch -r "$1" "$2/ref" && tr a-z A-Z < "$1" > "$2/new" && cat "$2/new" > "$1" && touch -r "$2/ref" "$1"',
                        "sh", str(claude), str(scratch)], check=True)
        for path in (config, claude):
            self.assertNotEqual(path.read_bytes(), before[path])
            self.assertEqual(len(path.read_bytes()), len(before[path]))
        self.assertEqual(config.stat().st_mtime_ns, was.st_mtime_ns)
        helpers.fixture().restore()
        self.assertEqual({p: p.read_bytes() for p in (config, claude)}, before)


class SnapshotRestoreTest(unittest.TestCase):
    """helpers.Snapshot puts a root back as it was taken, whatever a test did to it, and copies again only what was touched."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="spud-snapshot-test-")).resolve()
        self.addCleanup(helpers.remove_path, self.root)
        (self.root / "home" / ".spud" / "pycache").mkdir(parents=True)
        (self.root / "home" / ".spud" / "pycache" / "x.pyc").write_bytes(b"cache")
        (self.root / "home" / ".spud" / "ledger.db").write_bytes(b"db one")
        (self.root / "home" / "notes").mkdir()
        (self.root / "home" / "notes" / "a.md").write_text("a\n", encoding="utf-8")
        (self.root / "home" / "link").symlink_to("notes/a.md")
        (self.root / "tool").mkdir()
        (self.root / "tool" / "f.txt").write_text("tool\n", encoding="utf-8")
        self.keep = (os.path.join("home", ".spud", "pycache"),)
        self.copied = Path(tempfile.mkdtemp(prefix="spud-snapshot-copy-")).resolve() / "root"
        self.addCleanup(shutil.rmtree, self.copied.parent, True)
        helpers.copy_root(self.root, self.copied, self.keep)
        self.stamps = helpers.stamp_root(self.root, self.keep)

    def restore(self):
        helpers.restore_root(self.copied, self.root, self.keep, self.stamps)

    def tree(self):
        out = {}
        for d, subdirs, names in os.walk(self.root):
            for n in names + [s for s in subdirs if os.path.islink(os.path.join(d, s))]:
                p = os.path.join(d, n)
                rel = os.path.relpath(p, self.root)
                out[rel] = ("link", os.readlink(p)) if os.path.islink(p) else ("file", Path(p).read_bytes(), os.stat(p).st_mode)
            for s in subdirs:
                out[os.path.relpath(os.path.join(d, s), self.root) + "/"] = ("dir", os.stat(os.path.join(d, s)).st_mode)
        return out

    def test_everything_a_test_did_is_undone_and_the_cache_is_kept_in_place(self):
        before = self.tree()
        cache = self.root / "home" / ".spud" / "pycache" / "x.pyc"
        inode = cache.stat().st_ino
        (self.root / "home" / ".spud" / "ledger.db").write_bytes(b"db two")  # the same size, written after the copy
        (self.root / "home" / "notes" / "a.md").chmod(0o600)  # a mode alone
        (self.root / "home" / "new.md").write_text("new\n", encoding="utf-8")
        (self.root / "tool" / "f.txt").unlink()
        (self.root / "home" / "link").unlink()
        (self.root / "home" / "link").symlink_to("elsewhere")
        (self.root / "sibling").mkdir()  # beside the home and the tool, as a test's worktree elsewhere is
        locked = self.root / "home" / "notes" / "locked"
        (locked / "in").mkdir(parents=True)
        locked.chmod(0o000)
        (self.root / "home" / "notes").chmod(0o500)
        self.restore()
        self.assertEqual(self.tree(), before)
        self.assertEqual(cache.stat().st_ino, inode)  # never copied, never removed

    def test_a_file_written_through_a_hard_link_is_copied_again_and_the_link_is_not_written_through(self):
        target = self.root / "home" / "notes" / "a.md"
        elsewhere = self.root.parent / (self.root.name + "-hardlink")
        os.link(target, elsewhere)
        self.addCleanup(os.unlink, elsewhere)
        time.sleep(0.001)
        with open(elsewhere, "w", encoding="utf-8") as f:
            f.write("b\n")  # the same size as "a\n", and the same inode as the home's file
        self.restore()
        self.assertEqual(target.read_text(encoding="utf-8"), "a\n")
        self.assertEqual(elsewhere.read_text(encoding="utf-8"), "b\n")

    def test_an_untouched_file_is_left_where_it_is(self):
        tool_file = self.root / "tool" / "f.txt"
        inode = tool_file.stat().st_ino
        self.restore()
        self.restore()
        self.assertEqual(tool_file.stat().st_ino, inode)

    def test_a_same_size_write_with_its_time_put_back_is_copied_again(self):
        """SPD-233 review F2: size, mode and modification time can all be put back after a write, in this process
        (os.utime) or by a program a test runs (`touch -r`); the status-change time cannot, so the restore still sees it."""
        db = self.root / "home" / ".spud" / "ledger.db"
        note = self.root / "tool" / "f.txt"
        was = db.stat()
        db.write_bytes(b"db two")  # the same size as b"db one"
        os.utime(db, ns=(was.st_atime_ns, was.st_mtime_ns))
        subprocess.run(["/bin/sh", "-c", 'printf "TOOL\\n" > "$1" && touch -r "$2" "$1"', "sh", str(note), str(self.copied / "tool" / "f.txt")],
                       check=True)
        for path, changed in ((db, b"db two"), (note, b"TOOL\n")):
            self.assertEqual(path.read_bytes(), changed)
            copy = self.copied / path.relative_to(self.root)
            self.assertEqual((path.stat().st_size, path.stat().st_mtime_ns), (copy.stat().st_size, copy.stat().st_mtime_ns))
        self.restore()
        self.assertEqual((db.read_bytes(), note.read_bytes()), (b"db one", b"tool\n"))
        stamps = dict(self.stamps)
        self.restore()  # and, copied back, each is left in place from then on
        self.assertEqual(self.stamps, stamps)
        self.assertEqual({str(p): (p.stat().st_ino, p.stat().st_ctime_ns) for p in (db, note)}, {str(p): stamps[str(p)] for p in (db, note)})

    def test_a_restore_forgets_what_the_process_cached_about_the_root(self):
        spud = helpers.load_spud_module()
        worktrees = importlib.import_module("spudlib.hooks.worktrees")  # through the finder the program installs
        worktrees._WORKTREES[str(self.root)] = ["stale"]
        snapshot = helpers.Snapshot.__new__(helpers.Snapshot)
        snapshot.home = Home.__new__(Home)
        snapshot.home.__dict__.update(root=self.root, path=self.root / "home", tool=self.root / "tool", env={}, config={}, _tmp=None)
        snapshot.env, snapshot.config, snapshot.keep, snapshot.copied, snapshot.stamps = {}, {}, self.keep, self.copied, self.stamps
        restored = snapshot.restore()
        self.assertEqual(worktrees._WORKTREES, {})
        self.assertIsNone(restored._tmp)  # its cleanup removes nothing: the root is the snapshot owner's
        self.assertIsNotNone(spud)
