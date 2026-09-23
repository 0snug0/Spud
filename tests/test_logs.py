"""SPD-118: `spud logs`, the read of the LaunchAgents' logs.  Every hook refuses a shell command naming the home's .spud/,
so before this a session had no way to read <home>/.spud/logs/render.log -- which is where a watcher that went down says
why.  The command only reads and takes no actor; `--tail N` reads across the rotation SPD-170 added (render.log.1, the
previous run's), since the line saying why the last run stopped is in render.log.1 once the next run has started.

Doctor's pointer to it is tested in tests/test_doctor.py.
"""

import os
import tempfile
import unittest

from helpers import EXIT_OK, SpudTestCase, load_spud_module

spud = load_spud_module()

EXIT_USAGE = 2


def numbered(prefix, count):
    return "".join("%s %d\n" % (prefix, i) for i in range(1, count + 1))


class LogsCase(SpudTestCase):
    def setUp(self):
        super().setUp()
        self.logs = self.home.path / ".spud" / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.current = self.logs / "render.log"
        self.previous = self.logs / "render.log.1"

    def lines(self, *args):
        """`spud logs` with no actor, as a member would run it; its stdout's lines."""
        proc = self.home.run("logs", *args)
        return proc.stdout.rstrip("\n").split("\n")


class RenderLogTest(LogsCase):
    def test_it_prints_the_last_lines_of_the_current_log_with_no_actor(self):
        self.current.write_text(numbered("now", 50), encoding="utf-8")
        self.previous.write_text(numbered("before", 5), encoding="utf-8")
        self.assertEqual(self.lines("render", "--tail", "3"), ["now 48", "now 49", "now 50"])
        self.assertEqual(self.lines("render"), ["now %d" % i for i in range(11, 51)])  # 40 by default, all from render.log

    def test_tail_reads_across_the_rotation_into_the_previous_run(self):
        self.current.write_text(numbered("now", 2), encoding="utf-8")
        self.previous.write_text(numbered("before", 10) + "stopped after 3 pass(es): the program changed on disk\n", encoding="utf-8")
        self.assertEqual(self.lines("render", "--tail", "4"), [
            "==> %s <==" % self.previous, "before 10", "stopped after 3 pass(es): the program changed on disk",
            "", "==> %s <==" % self.current, "now 1", "now 2"])
        data = self.home.json("logs", "render", "--tail", "4")
        self.assertEqual((data["log"], data["tail"], data["lines"]), ("render", 4, 4))
        self.assertEqual([(f["path"], f["previous"], f["lines"]) for f in data["files"]], [
            (str(self.previous), True, ["before 10", "stopped after 3 pass(es): the program changed on disk"]),
            (str(self.current), False, ["now 1", "now 2"])])

    def test_a_just_started_watcher_shows_the_last_run_whole_when_asked(self):
        self.current.write_text("", encoding="utf-8")  # rotate_log truncated it a moment ago
        self.previous.write_text(numbered("before", 3), encoding="utf-8")
        self.assertEqual(self.lines("render", "--tail", "10"), [
            "==> %s <==" % self.previous, "before 1", "before 2", "before 3", "", "==> %s <==" % self.current])

    def test_no_previous_log_and_a_short_current_one_prints_it_plain(self):
        self.current.write_text("only line\n", encoding="utf-8")
        self.assertEqual(self.lines("render", "--tail", "5"), ["only line"])

    def test_a_long_log_is_read_from_its_end(self):
        body = numbered("x" * 200, 2000)  # about 400 KiB: several of the reader's backward steps
        self.current.write_text(body + "last line without a newline", encoding="utf-8")
        self.assertEqual(self.lines("render", "--tail", "2"), ["%s 2000" % ("x" * 200), "last line without a newline"])

    def test_bytes_that_are_not_utf8_are_replaced_not_fatal(self):
        self.current.write_bytes(b"good\n\xff\xfe bad\n")
        self.assertEqual(self.lines("render", "--tail", "2"), ["good", "�� bad"])

    def test_no_log_yet_says_so_and_exits_0(self):
        proc = self.home.run("logs", "render")
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertIn("no render log yet at %s" % self.current, proc.stdout)
        data = self.home.json("logs", "render")
        self.assertEqual((data["lines"], data["files"][0]["exists"]), (0, False))

    def test_tail_takes_a_positive_whole_number_and_needs_a_name(self):
        for bad in ("0", "-3", "many"):
            proc = self.home.run("logs", "render", "--tail", bad, check=False)
            self.assertEqual(proc.returncode, EXIT_USAGE, bad)
            self.assertIn("at least 1", proc.stderr)
        proc = self.home.run("logs", "--tail", "5", check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE)
        self.assertIn("name it", proc.stderr)
        self.assertEqual(self.home.run("logs", "nosuch", check=False).returncode, EXIT_USAGE)

    def test_it_writes_nothing(self):
        self.current.write_text(numbered("now", 3), encoding="utf-8")
        before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in self.logs.iterdir()}
        events = self.home.scalar("SELECT count(*) FROM events")
        self.home.run("logs", "render", "--tail", "100")
        self.home.run("logs")
        self.assertEqual({p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in self.logs.iterdir()}, before)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events"), events)


class ListAndBackupTest(LogsCase):
    def setUp(self):
        super().setUp()
        self.user = self.home.path / "user-home"  # the backup's log is under ~/Library/Logs: never this Mac's in a test
        self.home.env["HOME"] = str(self.user)
        self.backup = self.user / "Library" / "Logs" / "spud-backup.log"

    def test_no_name_lists_every_log_and_its_files(self):
        self.current.write_text(numbered("now", 2), encoding="utf-8")
        data = self.home.json("logs")
        self.assertEqual([l["log"] for l in data["logs"]], ["render", "backup"])
        render = data["logs"][0]["files"]
        self.assertEqual([(f["path"], f["exists"]) for f in render], [(str(self.current), True), (str(self.previous), False)])
        self.assertEqual(render[0]["bytes"], len(numbered("now", 2)))
        self.assertEqual([(f["path"], f["exists"]) for f in data["logs"][1]["files"]], [(str(self.backup), False)])
        text = self.home.run("logs").stdout
        self.assertIn("render ", text)
        self.assertIn("%s: not there" % self.previous, text)
        self.assertIn("spud logs <name> --tail N", text)

    def test_the_backup_log_reads_the_same_way_and_has_no_previous(self):
        self.backup.parent.mkdir(parents=True)
        self.backup.write_text(numbered("daily", 3), encoding="utf-8")
        self.assertEqual(self.lines("backup", "--tail", "10"), ["daily 1", "daily 2", "daily 3"])
        self.assertEqual(len(self.home.json("logs", "backup")["files"]), 1)


class TailLinesTest(unittest.TestCase):
    """The reader itself, on the edges a log file has: empty, one line, no final newline, a blank line at the end."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "log")

    def tail(self, content, n):
        with open(self.path, "wb") as f:
            f.write(content)
        return spud.tail_lines(self.path, n)

    def test_edges(self):
        self.assertEqual(spud.tail_lines(os.path.join(self.dir.name, "missing"), 3), [])
        self.assertEqual(self.tail(b"", 3), [])
        self.assertEqual(self.tail(b"one", 3), ["one"])
        self.assertEqual(self.tail(b"one\n", 3), ["one"])
        self.assertEqual(self.tail(b"one\ntwo\nthree\n", 2), ["two", "three"])
        self.assertEqual(self.tail(b"one\n\n", 2), ["one", ""])

    def test_every_block_boundary_gives_the_same_answer(self):
        lines = ["line %05d %s" % (i, "y" * (i % 97)) for i in range(3000)]
        body = ("\n".join(lines) + "\n").encode("utf-8")
        for n in (1, 7, 500, 2999, 3000, 4000):
            self.assertEqual(self.tail(body, n), lines[-n:], n)


if __name__ == "__main__":
    unittest.main()
