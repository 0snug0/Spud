"""SPD-157: doctor's tool-owned-files check -- the `shipped` section and its notes.

Doctor already had two checks of this shape: SPW-006's settings line, whose every wrong state is a problem because the
home's own installation is broken and one command fixes it, and SPD-156's vault notes, which are notes because a vault
somebody works in drifts from the template as a matter of course.  This one is the second kind, and these cases say so
in the only way that counts: a home whose CLAUDE.md Spud has edited still exits 0.

The rest of doctor's report is tested where its commands are (`tests/test_home.py`, `test_install.py`, `test_vault.py`,
`test_settings.py`, `test_watch.py`); what is here is what `spud home sync` added.
"""

import json
import os
import unittest

from helpers import SpudTestCase, load_spud_module

spud = load_spud_module()


class ShippedCheckTest(SpudTestCase):
    """`spud doctor`'s `shipped` line and the note it writes for each tool-owned file that is not what the tool ships."""

    def setUp(self):
        super().setUp()
        self.home.run("home", "sync", actor="spud")  # the home holds exactly what the tool ships now

    def report(self):
        proc = self.home.run("doctor", "--json", actor="spud", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)  # a note is never a problem: doctor still exits 0
        return self.home.json("doctor", actor="spud")

    def notes(self):
        return self.report()["notes"]

    def line(self):
        return [l for l in self.home.run("doctor", actor="spud").stdout.split("\n") if l.startswith("shipped ")][0]

    def test_it_is_quiet_when_every_tool_owned_file_is_what_the_tool_ships(self):
        report = self.report()
        self.assertEqual(report["shipped"]["differences"], [])
        self.assertEqual(report["shipped"]["files"], len(spud.tool_owned(
            spud.Ctx(self.home.path, "SPUD_HOME", False, tool=self.home.path))))
        self.assertIn("every one is what it ships", self.line())
        for note in report["notes"]:
            self.assertNotIn("home sync", note)

    def test_a_file_the_home_has_changed_is_a_note_naming_home_sync(self):
        path = self.home.path / "CLAUDE.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nMine now.\n", encoding="utf-8")
        report = self.report()
        self.assertEqual(report["shipped"]["differences"], ["CLAUDE.md"])
        note = [n for n in report["notes"] if n.startswith("the home's CLAUDE.md")][0]
        self.assertIn("differs from the one the tool ships", note)
        self.assertIn("home sync", note)
        self.assertIn("--check", note)
        self.assertIn("1 not what it ships", self.line())

    def test_a_file_the_tool_ships_and_the_home_does_not_have_is_a_note_too(self):
        os.remove(self.home.path / "ledger" / "Spud.md")
        report = self.report()
        self.assertEqual(report["shipped"]["differences"], ["ledger/Spud.md"])
        note = [n for n in report["notes"] if "ledger/Spud.md" in n][0]
        self.assertIn("the home has no ledger/Spud.md", note)
        self.assertIn("home sync", note)

    def test_every_file_that_differs_is_named_and_the_count_is_the_home_s(self):
        for rel in ("ledger/Home.md", "ledger/_templates/ticket.md"):
            (self.home.path / rel).write_text("mine now\n", encoding="utf-8")
        os.remove(self.home.path / "ledger" / "Fleet.base")
        report = self.report()
        self.assertEqual(sorted(report["shipped"]["differences"]),
                         ["ledger/Fleet.base", "ledger/Home.md", "ledger/_templates/ticket.md"])
        self.assertIn("3 not what it ships", self.line())
        self.assertEqual(sum(1 for n in report["notes"] if "home sync" in n), 3)

    def test_a_sync_settles_every_note_the_check_wrote(self):
        (self.home.path / "CLAUDE.md").write_text("mine now\n", encoding="utf-8")
        os.remove(self.home.path / "ledger" / "Board.base")
        self.assertEqual(len(self.report()["shipped"]["differences"]), 2)
        self.home.run("home", "sync", actor="spud")
        self.assertEqual(self.report()["shipped"]["differences"], [])

    def test_a_shipped_file_that_cannot_be_rendered_is_one_note_and_not_a_traceback(self):
        """doctor reports; it never refuses.  A `{{mark}}` with no value is what `home sync` itself raises on, and here
        it has to come back as a sentence."""
        self.home.shipped("ledger/Home.md", "{{nobody_fills_this}}\n")
        report = self.report()
        note = [n for n in report["notes"] if "cannot be rendered" in n][0]
        self.assertIn("{{nobody_fills_this}}", note)
        self.assertEqual(report["shipped"]["differences"], [str(self.home.path / "share")])

    def test_the_shipped_skill_is_one_of_the_files_the_check_reads(self):
        self.home.shipped_skill("spud-reference", "---\nname: spud-reference\ndescription: d\n---\n\nIn {{home}}.\n")
        report = self.report()
        self.assertEqual(report["shipped"]["differences"], [".claude/skills/spud-reference/SKILL.md"])
        self.home.run("home", "sync", actor="spud")
        self.assertEqual(self.report()["shipped"]["differences"], [])


class OwnerCheckTest(SpudTestCase):
    """SPD-157: the `owner` block, which every file the tool generates renders the person's name and pronouns from.

    A note and never a problem, for the reason every check in this module is one: a home whose config predates the
    block works -- `core/shipped.DEFAULT_OWNER` renders `the owner` rather than a hole -- and what is missing is a name
    somebody has to type.  A problem here would fail `spud init`'s own last step on every such home."""

    def report(self, check=True):
        return self.home.json("doctor", actor="spud", check=check)

    def owner_notes(self, report):
        return [n for n in report["notes"] if "names no owner" in n]

    def config_without_owner(self):
        path = self.home.path / "spud.config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        del config["owner"]
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    def test_a_config_that_names_an_owner_says_nothing(self):
        self.assertEqual(self.owner_notes(self.report()), [])

    def test_a_config_with_no_owner_block_is_a_note_naming_the_block_and_home_sync(self):
        self.config_without_owner()
        report = self.report(check=False)  # whatever else this home says, the owner is never among its problems
        note = self.owner_notes(report)[0]
        self.assertIn("'the owner'", note)
        self.assertIn('"owner"', note)
        self.assertIn('"pronouns"', note)
        self.assertIn("home sync", note)
        self.assertEqual([p for p in report["problems"] if "owner" in p], [])

    def test_an_owner_block_with_an_empty_name_reads_as_none_at_all(self):
        path = self.home.path / "spud.config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["owner"] = {"name": "", "pronouns": {}}
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        self.assertEqual(len(self.owner_notes(self.report(check=False))), 1)


class ShippedSectionWithoutADatabaseTest(SpudTestCase):
    """The section is read from project 1's row, so a home with no database has none to report -- and a problem of its
    own on the line above.  Doctor must still print its report rather than fall over."""

    warm_cache = False

    def test_a_home_with_no_database_reports_no_shipped_section_and_still_prints(self):
        os.remove(self.home.db)
        proc = self.home.run("doctor", "--json", actor="spud", check=False)
        self.assertNotEqual(proc.returncode, 0)  # the missing database is the problem, not the shipped files
        report = self.home.json("doctor", actor="spud", check=False)
        self.assertIsNone(report["shipped"])
        self.assertNotIn("shipped ", self.home.run("doctor", actor="spud", check=False).stdout)


if __name__ == "__main__":
    unittest.main()
