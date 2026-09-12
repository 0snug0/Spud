"""Concurrency smoke test: a handful of spud processes writing at once with zero
`database is locked` errors, every write landing."""

import subprocess
import sys
import unittest

from helpers import SPUD, SpudTestCase


class ConcurrencyTest(SpudTestCase):
    def test_parallel_writers_never_see_database_is_locked(self):
        t = self.new_ticket("Busy", status="active")
        members = [self.new_member(t["key"]) for _ in range(3)]
        for m in members:
            self.home.json("member", "start", m["ref"], actor="spud")
        rounds, width = 5, 8
        procs = []
        for r in range(rounds):
            batch = []
            for i in range(width):
                if i < 3:
                    cmd = ["--as", members[i]["ref"], "member", "log", "round %d writer %d" % (r, i)]
                elif i < 6:
                    cmd = ["--as", "spud", "report", "add", "round %d writer %d" % (r, i), "--next", "keep going"]
                else:
                    cmd = ["--as", "spud", "ticket", "edit", t["key"], "--brief", "round %d writer %d" % (r, i)]
                batch.append(
                    subprocess.Popen(
                        [sys.executable, "-I", "-S", str(SPUD)] + cmd,
                        env=self.home.env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                )
            for p in batch:
                out, err = p.communicate(timeout=60)
                procs.append((p.returncode, out, err))
        failures = [(code, err) for code, _, err in procs if code != 0]
        self.assertEqual(failures, [])
        self.assertFalse(any("locked" in err.lower() for _, _, err in procs))
        logs = self.home.scalar("SELECT count(*) FROM events WHERE kind = 'member.log'")
        reports = self.home.scalar("SELECT count(*) FROM events WHERE kind = 'report.entry'")
        edits = self.home.scalar("SELECT count(*) FROM events WHERE kind = 'ticket.edited'")
        self.assertEqual((logs, reports, edits), (3 * rounds, 3 * rounds, 2 * rounds))


if __name__ == "__main__":
    unittest.main()
