"""Bulk import of a small synthetic markdown-v0 tree: the derived-timestamp rule
for a member whose file carries no spawned time, and the import events."""

import json
import unittest

from helpers import SpudTestCase

TICKET = """---
id: SPD-001
title: "Synthetic"
priority: P2
status: active
origin: eric
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
        from helpers import normalize_markdown

        for rel in ("ledger/tickets/SPD-001.md", "ledger/teams/SPUD-001/Russet.md"):
            self.assertEqual(normalize_markdown((out / rel).read_text(encoding="utf-8")), normalize_markdown((root / rel).read_text(encoding="utf-8")), rel)


if __name__ == "__main__":
    unittest.main()
