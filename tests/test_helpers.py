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
"""

import os
import subprocess
import unittest

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
