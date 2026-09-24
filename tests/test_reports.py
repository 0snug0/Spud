"""report add is Spud's.  What it writes, and how a day file renders its entries, is test_report_entries.ReportDayTest's
(SPD-233: the two tests that were here -- an entry added and rendered, two entries in order -- are covered by its
test_a_day_of_report_add_entries_alone_is_byte_identical_to_before, which also asserts report add's output and each
entry's body and title)."""

import unittest

from helpers import EXIT_OWNERSHIP, SpudTestCase


class ReportTest(SpudTestCase):
    def test_report_add_is_spuds(self):
        t = self.new_ticket("Reported")
        m = self.new_member(t["key"])
        proc = self.home.run("report", "add", "x", "--next", "y", actor=m["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)


if __name__ == "__main__":
    unittest.main()
