"""Bulk import of a small synthetic markdown-v0 tree: the derived-timestamp rule
for a member whose file carries no spawned time, and the import events."""

import json
import unittest

from helpers import EXIT_ERROR, SpudTestCase

TICKET = """---
id: SPD-001
title: "Synthetic"
priority: P2
status: active
origin: owner
project: spud
lead: "[[SPUD-001/Russet]]"
created: 2026-09-01
tags: [ticket]
---
# SPD-001 — Synthetic

## Brief
A brief.

## Size, persona and model decision
Small.

## Team
- [[SPUD-001/Russet|Russet]] (01, scout, haiku)

## Handoffs

## Proposals received

## Outcome
"""

MEMBER = """---
id: "01"
name: Russet
persona: scout
model: haiku
parent: "[[Spud]]"
ticket: "[[SPD-001]]"
project: spud
status: active
spawned: ""
finished: ""
tags: [spudagent]
---
# Russet (01, scout) — SPD-001

## Brief
Do it.

## Log

## Sub-agents

## Ticket proposals

## Result

## Outcome
"""


# SPD-096: a ticket exported while parked carries the two properties that qualify the status, right after it.
PARKED_TICKET = """---
id: SPD-002
title: "Publish the label"
priority: P2
status: parked
parked_until: 2026-10-16
parked_reason: "App Store approval of iOS 1.0"
origin: owner
project: spud
proposed_by: ""
lead: ""
created: 2026-09-14
tags: [ticket]
---
# SPD-002 — Publish the label

## Brief
Waiting on the store.

## Size, persona and model decision

## Team

## Handoffs

## Proposals received

## Outcome
"""


class SyntheticImportTest(SpudTestCase):
    def write_tree(self):
        root = self.home.path / "corpus"
        (root / "ledger" / "tickets").mkdir(parents=True)
        (root / "ledger" / "teams" / "SPUD-001").mkdir(parents=True)
        (root / "ledger" / "tickets" / "SPD-001.md").write_text(TICKET, encoding="utf-8")
        (root / "ledger" / "teams" / "SPUD-001" / "Russet.md").write_text(MEMBER, encoding="utf-8")
        return root

    def test_member_without_spawned_takes_the_tickets_created_and_says_so(self):
        root = self.write_tree()
        out = self.home.json("import", root)
        self.assertEqual((out["tickets"], out["members"], out["prose_sections"]), (1, 1, 0))
        row = self.home.rows("SELECT planned_at, spawned_at, finished_at, status FROM members WHERE name = 'Russet'")[0]
        self.assertEqual(row, {"planned_at": "2026-09-01", "spawned_at": None, "finished_at": None, "status": "active"})
        data = json.loads(self.home.scalar("SELECT data FROM events WHERE kind = 'import' AND member_id IS NOT NULL"))
        self.assertEqual(data["source"], "ledger/teams/SPUD-001/Russet.md")
        self.assertEqual(data["derived"], {"planned_at": "ticket.created"})
        self.assertEqual(self.home.json("ticket", "show", "SPD-001")["ticket"]["lead"], "SPUD-001/Russet")

    def test_round_trip_of_the_synthetic_tree(self):
        root = self.write_tree()
        self.home.json("import", root)
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        from helpers import normalize_markdown, split_team_section, team_section_problems

        for rel in ("ledger/tickets/SPD-001.md", "ledger/teams/SPUD-001/Russet.md"):
            want, want_team = split_team_section((root / rel).read_text(encoding="utf-8"))
            got, got_team = split_team_section((out / rel).read_text(encoding="utf-8"))
            self.assertEqual(normalize_markdown(got), normalize_markdown(want), rel)
            self.assertEqual(got_team is None, want_team is None, rel)
            if want_team is not None:
                # ## Team is generated from the members table (SPD-010): compared by the spec's rule
                self.assertEqual(team_section_problems(want_team, got_team), [], rel)


    def test_a_parked_ticket_round_trips_and_one_without_a_reason_is_refused(self):
        from helpers import normalize_markdown

        root = self.write_tree()
        (root / "ledger" / "tickets" / "SPD-002.md").write_text(PARKED_TICKET, encoding="utf-8")
        self.assertEqual(self.home.json("import", root)["tickets"], 2)
        d = self.home.json("ticket", "show", "SPD-002")["ticket"]
        self.assertEqual((d["status"], d["parked_until"], d["parked_reason"]),
                         ("parked", "2026-10-16", "App Store approval of iOS 1.0"))
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        self.assertEqual(normalize_markdown((out / "ledger" / "tickets" / "SPD-002.md").read_text(encoding="utf-8")),
                         normalize_markdown(PARKED_TICKET))
        # the two keys are the template's order while parked, so the file needs no layout of its own
        self.assertIsNone(self.home.scalar("SELECT layout FROM tickets WHERE key = 'SPD-002'"))
        # an export that says parked with no reason is refused by the CHECK of migration 0003_parked
        bad = self.home.path / "corpus-bad"
        (bad / "ledger" / "tickets").mkdir(parents=True)
        (bad / "ledger" / "tickets" / "SPD-003.md").write_text(
            PARKED_TICKET.replace("SPD-002", "SPD-003").replace('parked_reason: "App Store approval of iOS 1.0"\n', ""), encoding="utf-8")
        proc = self.home.run("import", bad, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CHECK", proc.stderr)
        self.assertIsNone(self.home.scalar("SELECT id FROM tickets WHERE key = 'SPD-003'"))

    def test_a_members_effort_round_trips_and_one_outside_the_levels_is_refused(self):
        # SPD-222: the note carries effort after model when the row has one; an import reads it back
        from helpers import normalize_markdown

        root = self.write_tree()
        opus = MEMBER.replace("persona: scout\nmodel: haiku\n", "persona: engineer\nmodel: opus\neffort: high\n").replace("(01, scout)", "(01, engineer)")
        (root / "ledger" / "teams" / "SPUD-001" / "Russet.md").write_text(opus, encoding="utf-8")
        self.home.json("import", root)
        self.assertEqual(self.home.json("member", "show", "SPUD-001/Russet")["member"]["effort"], "high")
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        self.assertEqual(normalize_markdown((out / "ledger" / "teams" / "SPUD-001" / "Russet.md").read_text(encoding="utf-8")),
                         normalize_markdown(opus))
        bad = self.home.path / "corpus-bad"
        (bad / "ledger" / "tickets").mkdir(parents=True)
        (bad / "ledger" / "teams" / "SPUD-002").mkdir(parents=True)
        (bad / "ledger" / "tickets" / "SPD-002.md").write_text(TICKET.replace("SPD-001", "SPD-002").replace("SPUD-001", "SPUD-002"), encoding="utf-8")
        (bad / "ledger" / "teams" / "SPUD-002" / "Russet.md").write_text(
            opus.replace("SPD-001", "SPD-002").replace("effort: high", "effort: extreme"), encoding="utf-8")
        proc = self.home.run("import", bad, check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("effort 'extreme' is not one of low, medium, high, xhigh, max", proc.stderr)

    def test_a_note_rendered_before_0006_imports_its_origin_as_owner(self):
        # SPD-160: an export from before migration 0006_owner_origin says origin eric; it lands as owner and renders so
        root = self.write_tree()
        (root / "ledger" / "tickets" / "SPD-001.md").write_text(TICKET.replace("origin: owner", "origin: eric"), encoding="utf-8")
        self.home.json("import", root)
        self.assertEqual(self.home.scalar("SELECT origin FROM tickets WHERE key = 'SPD-001'"), "owner")
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        self.assertIn("\norigin: owner\n", (out / "ledger" / "tickets" / "SPD-001.md").read_text(encoding="utf-8"))
        bad = self.home.path / "corpus-bad"
        (bad / "ledger" / "tickets").mkdir(parents=True)
        (bad / "ledger" / "tickets" / "SPD-003.md").write_text(TICKET.replace("SPD-001", "SPD-003").replace("origin: owner", "origin: human"), encoding="utf-8")
        proc = self.home.run("import", bad, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("origin outside the schema's values", proc.stderr)


if __name__ == "__main__":
    unittest.main()
