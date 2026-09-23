"""The shell probe and its guard (SPD-094).

tests/probes/shell_probe.py runs a member's snippet in `zsh -f -o nobareglobqual`, `zsh -f` and /bin/bash under
sandbox-exec.  The guard is the point, so each way out is pinned here as failing in every shell: git by absolute path,
through xcrun, env, exec and a PATH the snippet sets, a copy of git; python and the spud launcher; a write to the
caller's home, to any other directory, and through a hard link; reading the caller's home; and the network, proved
against a socket this test listens on rather than the internet.  One ordinary snippet shows what each shell prints.
Everything skips where sandbox-exec is absent or cannot apply the profile (inside another sandbox), since the probe
itself refuses to run a snippet unguarded there.  unittest does not collect the probe.
"""

import importlib.machinery
import importlib.util
import os
import pwd
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

import helpers
from helpers import REPO

PROBE = REPO / "tests" / "probes" / "shell_probe.py"
LABELS = ["zsh -f -o nobareglobqual", "zsh -f", "bash"]


def load_probe():
    """Import the probe for unit tests, the way test_module_sizes imports its own."""
    loader = importlib.machinery.SourceFileLoader("shell_probe", str(PROBE))
    spec = importlib.util.spec_from_loader("shell_probe", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


probe = load_probe()


def unavailable():
    """Why the sandbox cannot be used here, or None."""
    if sys.platform != "darwin":
        return "sandbox-exec is macOS only"
    why, _ = probe.probe(b"true\n")
    return why


UNAVAILABLE = unavailable()


def run(snippet, timeout=probe.DEFAULT_TIMEOUT):
    """{label: (status, stdout, stderr)} for the snippet in every shell."""
    why, results = probe.probe(snippet.encode(), timeout)
    if why:
        raise AssertionError("the sandbox stopped working mid-run: %s" % why)
    return {label: (status, out, err) for label, _version, status, out, err in results}


@unittest.skipIf(UNAVAILABLE, "no usable sandbox: %s" % UNAVAILABLE)
class GuardTest(unittest.TestCase):
    """Each snippet tries one way out and prints RAN only if it got there; every shell must refuse it."""

    def assertRefused(self, snippet, said="not permitted"):
        """RAN never printed, and every shell's standard error says the kernel refused (`said`, when given)."""
        results = run(snippet)
        self.assertEqual(list(results), LABELS)
        for label, (status, out, err) in results.items():
            self.assertNotIn("RAN", out, "%s: %r %r" % (label, out, err))
            if said:
                self.assertIn(said, err.lower(), "%s: %r %r" % (label, out, err))
        return results

    def test_git_by_absolute_path_cannot_run(self):
        self.assertRefused("/usr/bin/git init repo && echo RAN\ntest -e repo/.git && echo RAN-MADE\n")

    def test_git_through_xcrun_env_exec_or_a_path_the_snippet_sets_cannot_run(self):
        for snippet in ("xcrun git --version && echo RAN\n",
                        "/usr/bin/xcrun --find git && echo RAN\n",
                        "env git --version && echo RAN\n",
                        "(exec git --version) && echo RAN\n",
                        "PATH=/Library/Developer/CommandLineTools/usr/bin:/Applications/Xcode.app/Contents/Developer"
                        "/usr/bin:/opt/homebrew/bin:/usr/local/bin:$PATH\ngit --version && echo RAN\n",
                        "/Library/Developer/CommandLineTools/usr/bin/git --version && echo RAN\n"):
            with self.subTest(snippet=snippet):
                self.assertRefused(snippet)

    def test_a_copy_of_git_or_of_any_program_in_the_directory_cannot_run(self):
        self.assertRefused("cp /usr/bin/git ./g && ./g --version && echo RAN\n"
                           "cp /bin/echo ./e && ./e RAN\n")

    def test_launchctl_at_its_real_path_cannot_run(self):
        # launchctl ships at /bin/launchctl, not /usr/bin/launchctl; DENIED_EXEC must deny the real path.
        self.assertRefused("/bin/launchctl list && echo RAN\n")

    def test_python_and_the_spud_launcher_cannot_run(self):
        for snippet in ("python3 -c 'print(\"RAN\")'\n",
                        "%s -I -S -c 'print(\"RAN\")'\n" % sys.executable,
                        "%s --help && echo RAN\n" % (REPO / "bin" / "spud")):
            with self.subTest(snippet=snippet):
                self.assertRefused(snippet)

    def test_a_write_to_the_callers_home_fails(self):
        target = Path(probe.user_home()) / (".spud-shell-probe-%s" % uuid.uuid4().hex)
        self.addCleanup(lambda: target.unlink(missing_ok=True))
        self.assertRefused("echo pwn > '%s' && echo RAN\n" % target)
        self.assertFalse(target.exists())

    def test_a_write_outside_the_directory_fails_whatever_the_route(self):
        with tempfile.TemporaryDirectory(prefix="spud-shell-probe-outside-") as tmp:
            outside = Path(tmp).resolve()
            victim = outside / "victim"
            victim.write_text("original\n")
            self.assertRefused("echo pwn > '%s/new' && echo RAN\n" % outside)
            self.assertRefused("echo pwn >> '%s' && echo RAN\n" % victim)
            self.assertRefused("ln '%s' hard && echo RAN\n" % victim)
            self.assertRefused("ln -s '%s' soft; echo pwn >> soft && echo RAN\n" % victim)
            self.assertRefused("mv '%s' here && echo RAN\n" % victim)
            self.assertEqual(sorted(p.name for p in outside.iterdir()), ["victim"])
            self.assertEqual(victim.read_text(), "original\n")

    def test_the_callers_home_cannot_be_read(self):
        self.assertRefused("ls '%s' && echo RAN\n" % probe.user_home())

    def test_the_network_cannot_be_reached(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            # The socket is refused before it connects, so curl reports it as a failed connection (7).
            self.assertRefused("curl -sS -m 3 http://127.0.0.1:%d/ && echo RAN\n" % port, "failed to connect")
            self.assertRefused("/usr/bin/nc -z -w 3 127.0.0.1 %d && echo RAN\n" % port, None)
            server.setblocking(False)
            with self.assertRaises(BlockingIOError):  # and nothing ever connected
                server.accept()
        # By name: resolution is refused too, so curl never gets as far as a socket.
        self.assertRefused("curl -sS -m 3 https://example.com/ -o /dev/null && echo RAN\n", "could not resolve")

    def test_the_environment_is_replaced_so_nothing_of_the_callers_reaches_the_snippet(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": "/nowhere", "BASH_ENV": "/nowhere/env", "SPUD_HOME": "/x"}):
            results = run("/usr/bin/env\n")
        for label, (status, out, err) in results.items():
            names = {line.split("=", 1)[0] for line in out.splitlines()}
            self.assertEqual(status, 0, (label, err))
            self.assertFalse({n for n in names if n.startswith("GIT_")}, label)
            self.assertNotIn("BASH_ENV", names, label)
            self.assertNotIn("SPUD_HOME", names, label)
            self.assertIn("PATH=/usr/bin:/bin", out.splitlines(), label)


class UserHomeTest(unittest.TestCase):
    def test_user_home_comes_from_the_password_database_not_HOME(self):
        real_home = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
        with mock.patch.dict(os.environ, {"HOME": "/nonexistent-spud-shell-probe-home"}):
            self.assertEqual(probe.user_home(), real_home)
        self.assertNotEqual(probe.user_home(), "/nonexistent-spud-shell-probe-home")


@unittest.skipIf(UNAVAILABLE, "no usable sandbox: %s" % UNAVAILABLE)
class ShellsTest(unittest.TestCase):
    def test_an_ordinary_snippet_prints_in_each_shell(self):
        results = run("touch a b\necho *(N)\nx=(p q r); echo ${#x[@]} $(( 6 * 7 ))\necho \"$HOME\" \"$PWD\"\n")
        nobare, zsh, bash = (results[label] for label in LABELS)
        # nobareglobqual: *(N) is a pattern, not a qualifier, and matches nothing, which is an error in zsh.
        self.assertEqual(nobare[0], 1)
        self.assertIn("no matches found: *(N)", nobare[2])
        # zsh's default: (N) is the null-glob qualifier, so the glob lists the directory.
        self.assertEqual(zsh, (0, "a b\n3 42\n<probe-dir>/shell2 <probe-dir>/shell2\n", ""))
        # bash 3.2: a parenthesis there is a syntax error, so the script stops before it runs a line.
        self.assertEqual(bash[0], 2)
        self.assertIn("syntax error near unexpected token `('", bash[2])

    def test_each_shell_prints_its_version_and_runs_in_its_own_directory(self):
        why, results = probe.probe(b"echo \"$PWD\"\n")
        self.assertIsNone(why)
        versions = [r[1] for r in results]
        self.assertTrue(versions[0].startswith("zsh 5."), versions)
        self.assertEqual(versions[0], versions[1])
        self.assertTrue(versions[2].startswith("GNU bash, version 3.2"), versions)
        self.assertEqual([r[3] for r in results], ["<probe-dir>/shell%d\n" % n for n in (1, 2, 3)])

    def test_the_directory_is_removed_whatever_the_snippet_did_to_it(self):
        made = []
        real = tempfile.mkdtemp

        def recording(*args, **kwargs):
            made.append(real(*args, **kwargs))
            return made[-1]

        with mock.patch.object(probe.tempfile, "mkdtemp", recording):
            run("mkdir -p d/e && touch d/e/f && chmod 000 d/e d .\n")
        self.assertEqual(len(made), 1)
        self.assertFalse(os.path.exists(made[0]))

    def test_one_shell_cannot_change_the_script_the_next_one_reads(self):
        results = run("echo 'echo CHANGED' > \"$0\"; echo ran\n")
        for label, (status, out, err) in results.items():
            self.assertEqual(out, "ran\n", label)

    @helpers.wall_clock
    def test_a_shell_past_its_timeout_is_killed_and_a_background_job_does_not_hold_it(self):
        start = time.monotonic()
        results = run("sleep 30\necho RAN\n", timeout=0.5)
        for label, (status, out, err) in results.items():
            self.assertEqual((status, out), ("timeout after 0.5 s", ""), label)
        results = run("sleep 30 &\necho started\n")
        for label, (status, out, err) in results.items():
            self.assertEqual((status, out), (0, "started\n"), label)
        self.assertLess(time.monotonic() - start, 8)


class CommandTest(unittest.TestCase):
    def run_probe(self, *args, stdin=b""):
        return subprocess.run([sys.executable, "-I", "-S", str(PROBE), *args], input=stdin, capture_output=True)

    def test_the_profile_denies_by_default_and_names_what_it_refuses(self):
        proc = self.run_probe("--profile")
        self.assertEqual(proc.returncode, 0)
        text = proc.stdout.decode()
        self.assertIn("(deny default)", text)
        self.assertIn('(allow process-exec (subpath "/bin") (subpath "/usr/bin"))', text)
        self.assertIn('(regex #"^/usr/bin/git")', text)
        self.assertIn("(deny network*)", text)
        self.assertIn("(deny file-link)", text)

    def test_usage_errors_exit_2(self):
        self.assertEqual(self.run_probe("--bogus").returncode, 2)
        self.assertEqual(self.run_probe("/nowhere/at/all.sh").returncode, 2)
        self.assertEqual(self.run_probe("--timeout", "soon").returncode, 2)

    def test_no_sandbox_means_no_shell_runs(self):
        with mock.patch.object(probe, "SANDBOX_EXEC", "/nowhere/sandbox-exec"):
            why, results = probe.probe(b"echo RAN\n")
        self.assertIn("/nowhere/sandbox-exec does not exist", why)
        self.assertEqual(results, [])

    @unittest.skipIf(UNAVAILABLE, "no usable sandbox: %s" % UNAVAILABLE)
    def test_a_snippet_on_standard_input_or_in_a_file_prints_every_shell_labelled(self):
        with tempfile.NamedTemporaryFile("wb", suffix=".sh", delete=False) as f:
            f.write(b"echo hello\n")
        self.addCleanup(os.unlink, f.name)
        for args, stdin in (((), b"echo hello\n"), (("-",), b"echo hello\n"), ((f.name,), b"")):
            with self.subTest(args=args):
                proc = self.run_probe(*args, stdin=stdin)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                out = proc.stdout.decode()
                self.assertTrue(out.startswith("shell probe (SPD-094): "), out)
                for label in LABELS:
                    self.assertIn("\n== %s  (" % label, out)
                self.assertEqual(out.count("status: 0\n-- stdout:\nhello\n-- stderr: (empty)\n"), 3, out)


if __name__ == "__main__":
    unittest.main()
