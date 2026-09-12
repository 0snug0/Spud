"""report add and the day file rendered from report.entry events."""

import datetime
import re
import unittest

from helpers import EXIT_OWNERSHIP, MARKER, SpudTestCase


class ReportTest(SpudTestCase):
    def test_report_add_appends_an_entry_and_renders_the_day_file(self):
        t = self.new_ticket("Reported")
        out = self.home.json("report", "add", "SPD-001 done", "--next", "commit; Eric decides SPD-002", actor="spud")
        self.assertTrue(out["ok"])
        entry = out["entry"]
        self.assertEqual(entry["title"], "SPD-001 done")
        self.assertRegex(entry["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
        events = self.home.json("events", "--kind", "report.entry")["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["body"], "- Next: commit; Eric decides SPD-002")
        self.assertEqual(events[0]["data"]["title"], "SPD-001 done")
        outdir = self.home.path / "out"
        self.home.json("render", "--out", outdir)
        day = entry["at"][:10]
        text = (outdir / "reports" / (day + ".md")).read_text(encoding="utf-8")
        lines = text.split("\n")
        self.assertEqual(lines[0], MARKER)
        self.assertEqual(lines[1], "# " + day)
        self.assertEqual(lines[3], "## %s — SPD-001 done" % entry["at"][11:16])
        self.assertEqual(lines[4], "- Next: commit; Eric decides SPD-002")
        self.assertTrue(text.endswith("\n"))

    def test_report_add_is_spuds(self):
        t = self.new_ticket("Reported")
        m = self.new_member(t["key"])
        proc = self.home.run("report", "add", "x", "--next", "y", actor=m["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)

    def test_entries_render_in_order_within_a_day(self):
        self.home.json("report", "add", "First", "--next", "a", actor="spud")
        self.home.json("report", "add", "Second", "--next", "b", actor="spud")
        outdir = self.home.path / "out"
        self.home.json("render", "--out", outdir)
        files = list((outdir / "reports").glob("*.md"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding="utf-8")
        self.assertLess(text.index("— First"), text.index("— Second"))


if __name__ == "__main__":
    unittest.main()
