"""Acceptance tests for SPD-007: the committed markdown-v0 ledger survives a
round trip through the database.

git archive <ref> ledger reports  ->  spud init  ->  spud import  ->
spud render --out <tmp>/out  ->  every generated file equals its source up to
the marker line, trailing whitespace and runs of blank lines.  One section is
compared by its own rule: a ticket's ## Team, generated from the members table
alone since SPD-010 (a table, the member tree with each member's worked-on
sentence, the embedded Team view), is checked by team_section_problems in
tests/helpers.py, the rule of docs/design/2026-09-12-team-card.md section 8.3.

Two corpora, two purposes:

* PinnedLedgerTest archives PINNED_REF, the ledger as it stood when the importer
  was written, and pins exactly which sections the rows cannot regenerate (the
  31 pairs below).  A regression that pushes more of the ledger into verbatim
  prose, or parses less of it into rows, fails here on purpose.
* CutoverReadinessTest archives live HEAD: the ledger as it stands now, which
  grows with every commit on main.  It asserts the round trip file by file and
  that every prose section is of a known kind, with no fixed count, so it keeps
  passing as the ledger grows and tells Spud before cutover that the importer
  copes with the ledger as it then stands.
"""

import difflib
import io
import json
import re
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from helpers import REPO, MARKER, Home, normalize_markdown, split_team_section, team_section_problems

# The last ledger commit on main before bin/spud existed (Law 10 and the Main and
# worktrees section landed in it).  It is on main, in every worktree and on origin,
# so the pinned corpus is the same everywhere the suite runs.
PINNED_REF = "2f11506"

# The sections the importer keeps as verbatim prose when the rows cannot
# regenerate them, at PINNED_REF.  Exact, on purpose: a change here is a change
# to what the database models.
PINNED_PROSE = {
    "ledger/teams/SPUD-001/Huckleberry.md": {"Log", "Sub-agents", "Ticket proposals"},
    "ledger/teams/SPUD-001/Kestrel.md": {"Log", "Sub-agents", "Ticket proposals"},
    "ledger/teams/SPUD-001/Ozette.md": {"Log", "Sub-agents", "Ticket proposals"},
    "ledger/teams/SPUD-001/Rosara.md": {"Log", "Sub-agents", "Ticket proposals"},
    "ledger/teams/SPUD-004/Kestrel.md": {"Log", "Sub-agents", "Ticket proposals"},
    "ledger/teams/SPUD-006/Dakota.md": {"Log", "Sources", "Sub-agents", "Ticket proposals"},
    "ledger/teams/SPUD-006/Elba.md": {"Log", "Sub-agents", "Ticket proposals"},
    "ledger/teams/SPUD-006/Vitelotte.md": {"Log", "Sub-agents", "Ticket proposals"},
    "ledger/teams/SPUD-007/Atlantic.md": {"Log", "Sub-agents", "Ticket proposals"},
    "ledger/tickets/SPD-001.md": {"Proposals received"},
    "ledger/tickets/SPD-004.md": {"Proposals received"},
    "ledger/tickets/SPD-006.md": {"Proposals received"},
}

# The kinds of section the importer may keep as prose from any markdown-v0 file:
# the sections it derives from rows (when the file carries more than the rows
# hold: annotations, comment lines, preambles) and the one extra section a
# member note carries today.  ## Team is none of them: its prose is never stored,
# its tree lines' suffixes become summaries (SPD-010).  A new kind must be added
# here deliberately.
KNOWN_PROSE_KINDS = {"Log", "Sub-agents", "Ticket proposals", "Handoffs", "Proposals received", "Sources"}

WIKILINK = re.compile(r"\[\[[^\]]*\]\]")
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def extract_corpus(ref, dest):
    """git archive <ref> ledger reports into dest; a missing ref is a failure, not a skip."""
    proc = subprocess.run(["git", "-C", str(REPO), "archive", ref, "ledger", "reports"], capture_output=True)
    if proc.returncode != 0:
        raise AssertionError(
            "git archive %s failed (%s); the pinned commit must exist on main, in every worktree and on origin"
            % (ref, proc.stderr.decode("utf-8", "replace").strip())
        )
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(dest, filter="data")


def split_frontmatter(text):
    lines = text.split("\n")
    assert lines[0] == "---", "no frontmatter"
    end = lines.index("---", 1)
    return lines[1:end], "\n".join(lines[end + 1 :])


def split_sections(body):
    """[(heading or None, text)] in order; the marker line is dropped."""
    sections = []
    heading = None
    buf = []
    for line in body.split("\n"):
        if line.strip() == MARKER:
            continue
        if line.startswith("## "):
            sections.append((heading, "\n".join(buf)))
            heading, buf = line, []
        else:
            buf.append(line)
    sections.append((heading, "\n".join(buf)))
    return sections


def is_ticket(rel):
    return tuple(rel.parts[:2]) == ("ledger", "tickets")


class RoundTripMixin:
    """import -> render --out of the corpus at cls.REF, then the file-by-file checks."""

    REF = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="spud-acceptance-")
        root = Path(cls.tmp.name)
        cls.src = root / "src"
        cls.out = root / "out"
        cls.src.mkdir()
        extract_corpus(cls.REF, cls.src)
        cls.home = Home()
        cls.home.init()
        cls.imported = cls.home.json("import", cls.src / "ledger", cls.src / "reports")
        cls.rendered = cls.home.json("render", "--out", cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.home.cleanup()
        cls.tmp.cleanup()

    def rendered_text(self, rel):
        """The rendered file, less its `project` property when the committed note predates it: SPD-014 names the project in
        every note, and a corpus committed before the first render with it carries no such line."""
        got = (self.out / rel).read_text(encoding="utf-8")
        want = (self.src / rel).read_text(encoding="utf-8")
        if rel.parts[0] == "ledger" and want.startswith("---\n") and not re.search(r"^project: ", want.split("\n---\n", 1)[0], re.M):
            got = re.sub(r"\A(---\n(?:[^\n]*\n)*?)project: [^\n]*\n", r"\1", got, count=1)
        return got

    def sources(self):
        # every project's notes render flat into the home's ledger/ (SPD-014): BAD-nnn and BADS-nnn beside SPD and SPUD
        files = sorted(self.src.glob("ledger/tickets/*.md"))
        files += sorted(self.src.glob("ledger/teams/*/*.md"))
        files += sorted(p for p in self.src.glob("reports/*.md"))
        return [p.relative_to(self.src) for p in files]

    def prose_pairs(self):
        rows = self.home.rows(
            "SELECT 'ledger/tickets/' || t.key || '.md' AS path, s.section FROM imported_sections s"
            " JOIN tickets t ON t.id = s.entity_id WHERE s.entity = 'ticket'"
            " UNION ALL"
            " SELECT 'ledger/teams/' || t.team_key || '/' || m.name || '.md', s.section FROM imported_sections s"
            " JOIN members m ON m.id = s.entity_id JOIN tickets t ON t.id = m.ticket_id WHERE s.entity = 'member'"
        )
        pairs = {}
        for r in rows:
            pairs.setdefault(r["path"], set()).add(r["section"])
        return pairs

    def test_import_counts_match_the_corpus(self):
        rels = self.sources()
        tickets = [r for r in rels if r.parts[1] == "tickets"]
        members = [r for r in rels if r.parts[1] == "teams"]
        reports = [r for r in rels if r.parts[0] == "reports"]
        self.assertEqual(self.imported["tickets"], len(tickets))
        self.assertEqual(self.imported["members"], len(members))
        self.assertEqual(self.imported["reports"], len(reports))
        self.assertGreater(self.imported["report_entries"], 0)

    def test_every_source_file_is_generated_and_nothing_else(self):
        expected = set(self.sources()) | {Path("ledger/Projects.md")}  # generated whatever the corpus holds (SPD-014)
        generated = {p.relative_to(self.out) for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual(generated, expected)
        for never in ["ledger/Home.md", "ledger/Spud.md", "ledger/Board.base", "ledger/Fleet.base"]:
            self.assertFalse((self.out / never).exists(), never)
        self.assertFalse((self.out / "ledger" / "_templates").exists())

    def test_marker_sits_immediately_after_the_frontmatter(self):
        for rel in self.sources():
            text = (self.out / rel).read_text(encoding="utf-8")
            lines = text.split("\n")
            if lines[0] == "---":
                end = lines.index("---", 1)
                self.assertEqual(lines[end + 1], MARKER, rel)
                self.assertNotIn(MARKER, lines[:end], rel)
            else:  # reports have no frontmatter; the marker leads
                self.assertEqual(lines[0], MARKER, rel)

    def test_round_trip_differs_only_in_formatting(self):
        failures = []
        for rel in self.sources():
            want_text = (self.src / rel).read_text(encoding="utf-8")
            got_text = self.rendered_text(rel)
            if is_ticket(rel):
                # ## Team is generated from the members table: its own rule; the rest of the note byte for byte
                want_text, want_team = split_team_section(want_text)
                got_text, got_team = split_team_section(got_text)
                if (want_team is None) != (got_team is None):
                    failures.append("%s: ## Team is in only one of the committed and the rendered note" % rel)
                elif want_team is not None:
                    failures += ["%s ## Team: %s" % (rel, problem) for problem in team_section_problems(want_team, got_team)]
            want = normalize_markdown(want_text)
            got = normalize_markdown(got_text)
            if want != got:
                diff = difflib.unified_diff(
                    want.splitlines(keepends=True),
                    got.splitlines(keepends=True),
                    fromfile="committed/" + str(rel),
                    tofile="rendered/" + str(rel),
                )
                failures.append("".join(diff))
        self.assertFalse(failures, "\n".join(failures))

    def test_frontmatter_keys_and_values_identical(self):
        for rel in self.sources():
            if rel.parts[0] == "reports":
                continue
            want, _ = split_frontmatter((self.src / rel).read_text(encoding="utf-8"))
            got, _ = split_frontmatter(self.rendered_text(rel))
            self.assertEqual([l.rstrip() for l in want], [l.rstrip() for l in got], rel)

    def test_sections_identical_in_order(self):
        for rel in self.sources():
            if rel.parts[0] == "reports":
                continue
            _, want_body = split_frontmatter((self.src / rel).read_text(encoding="utf-8"))
            _, got_body = split_frontmatter((self.out / rel).read_text(encoding="utf-8"))
            want = split_sections(want_body)
            got = split_sections(got_body)
            self.assertEqual([h for h, _ in want], [h for h, _ in got], rel)
            for (h, wt), (_, gt) in zip(want, got):
                if h == "## Team" and is_ticket(rel):
                    self.assertEqual(team_section_problems(wt, gt), [], "%s section %s" % (rel, h))
                else:
                    self.assertEqual(normalize_markdown(wt), normalize_markdown(gt), "%s section %s" % (rel, h))

    def test_wikilinks_identical(self):
        for rel in self.sources():
            want = (self.src / rel).read_text(encoding="utf-8")
            got = (self.out / rel).read_text(encoding="utf-8")
            if is_ticket(rel):
                # ## Team links each member from the table and the tree: compared outside it
                want, got = split_team_section(want)[0], split_team_section(got)[0]
            self.assertEqual(WIKILINK.findall(want), WIKILINK.findall(got), rel)

    def test_import_events_point_at_the_source_paths(self):
        rows = self.home.rows("SELECT body, data FROM events WHERE kind = 'import'")
        paths = set()
        for r in rows:
            self.assertIsNotNone(r["data"])
            paths.add(json.loads(r["data"])["source"])
        projects = {"ledger/Projects.md"} if (self.src / "ledger" / "Projects.md").is_file() else set()  # SPD-014: imported first, when committed
        self.assertEqual(paths, {str(rel) for rel in self.sources()} | projects)

    def test_second_import_is_refused(self):
        proc = self.home.run("import", self.src / "ledger", self.src / "reports", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("already", proc.stderr.lower())


class PinnedLedgerTest(RoundTripMixin, unittest.TestCase):
    """The regression guard: the ledger at PINNED_REF, with the exact prose pairs
    and the row-level facts of that corpus."""

    REF = PINNED_REF

    def test_prose_sections_are_exactly_the_pinned_ones(self):
        actual = self.prose_pairs()
        self.assertEqual(self.imported["prose_sections"], sum(len(v) for v in PINNED_PROSE.values()))
        self.assertEqual(actual, PINNED_PROSE)

    def test_imported_timestamps_keep_what_the_file_said(self):
        self.assertEqual(self.home.scalar("SELECT created_at FROM tickets WHERE key = 'SPD-001'"), "2026-09-12")
        self.assertEqual(self.home.scalar("SELECT spawned_at FROM members WHERE name = 'Ozette'"), "2026-09-12T00:00")
        self.assertIsNone(
            self.home.scalar("SELECT finished_at FROM members WHERE name = 'Atlantic' AND ticket_id = (SELECT id FROM tickets WHERE key = 'SPD-007')")
        )

    def test_import_invents_no_timestamp(self):
        # the files carry no close time: closed_at stays NULL on done tickets
        self.assertIsNone(self.home.scalar("SELECT closed_at FROM tickets WHERE key = 'SPD-001'"))
        # NOT NULL columns take the source's nearest value and the import event says which
        self.assertEqual(self.home.scalar("SELECT updated_at FROM tickets WHERE key = 'SPD-001'"), "2026-09-12")
        data = json.loads(self.home.scalar("SELECT e.data FROM events e JOIN tickets t ON t.id = e.ticket_id WHERE e.kind = 'import' AND e.member_id IS NULL AND t.key = 'SPD-001'"))
        # Ozette's tree line carries a suffix, so the event also names the summary it gave (SPD-010)
        self.assertEqual(data["derived"], {"updated_at": "created", "members.summary": "Team section"})
        data = json.loads(self.home.scalar("SELECT e.data FROM events e JOIN tickets t ON t.id = e.ticket_id WHERE e.kind = 'import' AND e.member_id IS NULL AND t.key = 'SPD-002'"))
        self.assertEqual(data["derived"], {"updated_at": "created", "proposal.filed_at": "created", "proposal_decisions.at": "created"})
        self.assertEqual(self.home.scalar("SELECT planned_at FROM members WHERE name = 'Ozette'"), "2026-09-12T00:00")
        data = json.loads(self.home.scalar("SELECT e.data FROM events e JOIN members m ON m.id = e.member_id WHERE e.kind = 'import' AND m.name = 'Ozette'"))
        self.assertEqual(data["derived"], {"planned_at": "spawned"})

    def test_team_line_suffix_became_the_members_summary(self):
        # the one fact SPD-001's markdown-v0 Team prose held beyond the rows (SPD-010)
        self.assertEqual(
            self.home.scalar("SELECT m.summary FROM members m JOIN tickets t ON t.id = m.ticket_id WHERE t.key = 'SPD-001' AND m.name = 'Ozette'"),
            "spawn attempted at the depth cap, never ran",
        )
        self.assertEqual(self.home.scalar("SELECT count(*) FROM members WHERE summary IS NOT NULL"), 1)

    def test_team_cards_match_the_spec_mocks(self):
        # the spec's raw mocks, byte for byte, rendered from the rows this corpus imports: SPD-001 as it
        # reads after the backfill (the corpus carries Ozette's annotation), SPD-006 as it reads before it
        fixture = json.loads((FIXTURES / "team_card.json").read_text(encoding="utf-8"))
        ozette = "    - [[SPUD-001/Ozette|Ozette]] (01.01.01, scout, haiku)\n"
        self.assertEqual(fixture["mocks"]["SPD-001"].count(ozette), 1)
        want = {
            "SPD-001": fixture["mocks"]["SPD-001"].replace(ozette, fixture["ozette_after_backfill"] + "\n"),
            "SPD-006": fixture["mocks"]["SPD-006"],
        }
        for key, section in want.items():
            text = (self.out / "ledger" / "tickets" / ("%s.md" % key)).read_text(encoding="utf-8")
            self.assertEqual("## Team\n" + text.split("\n## Team\n", 1)[1].split("\n\n## Handoffs\n", 1)[0], section, key)

    def test_proposal_links_resolve_to_members(self):
        row = self.home.rows(
            "SELECT t.key, m.name, t2.key AS arose_on FROM tickets t"
            " JOIN proposals p ON p.id = t.proposal_id"
            " JOIN members m ON m.id = p.origin_member_id"
            " JOIN tickets t2 ON t2.id = p.ticket_id WHERE t.key = 'SPD-002'"
        )
        self.assertEqual(row, [{"key": "SPD-002", "name": "Rosara", "arose_on": "SPD-001"}])

    def test_tree_imported_with_lineage_and_parents(self):
        rows = self.home.rows(
            "SELECT m.lineage, m.name, m.depth, p.name AS parent, m.status FROM members m"
            " JOIN tickets t ON t.id = m.ticket_id LEFT JOIN members p ON p.id = m.parent_id"
            " WHERE t.key = 'SPD-001' ORDER BY m.lineage"
        )
        self.assertEqual(
            rows,
            [
                {"lineage": "01", "name": "Kestrel", "depth": 1, "parent": None, "status": "done"},
                {"lineage": "01.01", "name": "Huckleberry", "depth": 2, "parent": "Kestrel", "status": "done"},
                {"lineage": "01.01.01", "name": "Ozette", "depth": 3, "parent": "Huckleberry", "status": "done"},
                {"lineage": "01.02", "name": "Rosara", "depth": 2, "parent": "Kestrel", "status": "done"},
            ],
        )
        self.assertEqual(self.home.scalar("SELECT agent_type FROM members WHERE name = 'Elba'"), "claude-code-guide")
        self.assertEqual(self.home.scalar("SELECT persona FROM members WHERE name = 'Elba'"), "contractor")

    def test_log_lines_became_member_log_events(self):
        text = (self.src / "ledger/teams/SPUD-006/Vitelotte.md").read_text(encoding="utf-8")
        log = text.split("## Log\n", 1)[1].split("\n## ", 1)[0]
        expected = len([l for l in log.split("\n") if re.match(r"^- \d{4}-\d{2}-\d{2}", l)])
        self.assertEqual(expected, 20)
        n = self.home.scalar(
            "SELECT count(*) FROM events e JOIN members m ON m.id = e.member_id"
            " WHERE e.kind = 'member.log' AND m.name = 'Vitelotte'"
        )
        self.assertEqual(n, expected)
        first = self.home.rows(
            "SELECT e.at, e.body FROM events e JOIN members m ON m.id = e.member_id"
            " WHERE e.kind = 'member.log' AND m.name = 'Vitelotte' ORDER BY e.id LIMIT 1"
        )[0]
        self.assertEqual(first["at"], "2026-09-12T13:20")
        self.assertTrue(first["body"].startswith("Started. Read spud.config.json"))

    def test_handoffs_parsed(self):
        rows = self.home.rows(
            "SELECT f.name AS f, t.name AS t, h.what FROM handoffs h"
            " JOIN tickets k ON k.id = h.ticket_id LEFT JOIN members f ON f.id = h.from_member_id"
            " LEFT JOIN members t ON t.id = h.to_member_id WHERE k.key = 'SPD-001' ORDER BY h.id"
        )
        self.assertEqual([(r["f"], r["t"]) for r in rows], [("Kestrel", "Huckleberry"), ("Kestrel", "Rosara"), ("Huckleberry", "Ozette"), ("Kestrel", None)])

    def test_report_entries_are_events(self):
        rows = self.home.rows("SELECT at, body, data FROM events WHERE kind = 'report.entry' ORDER BY id")
        self.assertEqual(len(rows), 22)
        self.assertEqual(rows[0]["at"], "2026-09-12T12:25")
        self.assertTrue(rows[0]["body"].startswith("- Ticket: [[SPD-001]]"))


class CutoverReadinessTest(RoundTripMixin, unittest.TestCase):
    """The ledger as it stands at HEAD: the round trip holds file by file and every
    prose section is of a known kind, whatever the count."""

    REF = "HEAD"

    def test_prose_sections_are_of_known_kinds(self):
        unknown = [
            "%s: ## %s" % (path, section)
            for path, sections in sorted(self.prose_pairs().items())
            for section in sorted(sections)
            if section not in KNOWN_PROSE_KINDS
        ]
        self.assertEqual(unknown, [], "sections kept as prose of a kind the importer does not know; add the kind to KNOWN_PROSE_KINDS deliberately")

    def test_report_entries_match_the_corpus(self):
        expected = 0
        for path in self.src.glob("reports/*.md"):
            expected += len([l for l in path.read_text(encoding="utf-8").split("\n") if re.match(r"^## \d{2}:\d{2} — ", l)])
        self.assertGreater(expected, 0)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events WHERE kind = 'report.entry'"), expected)


if __name__ == "__main__":
    unittest.main()
