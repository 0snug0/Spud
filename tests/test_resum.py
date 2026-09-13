"""spud member resum (SPD-023).

A transcript sum stored before SPD-023 added every entry of the member's transcript, and the harness
writes an API response as one entry per content block with the request's usage repeated on each, so
its tokens run four to five times too high.  `spud --as spud member resum <ref>... | --all [--dry-run]`
re-sums such a sum from the transcript, once per API request: the transcript at the recorded path, or
else the one file with the same session directory and file name under a project directory beside it
(the harness moves a worktree session's transcripts when the session leaves the worktree).  The new
sum is merged through run_totals, so a completion keeps its whole-run duration and tool count, and a
member.edited event keeps the old figures.  A sum already counted by request, an imported sum and a
member without a transcript sum are listed and left; a sum whose transcript is not found, ambiguous,
unreadable or without usage is listed and left, and makes the exit 1.  Every case runs in a scratch
SPUD_HOME over hand-built transcripts (HookCase.per_block).
"""

import json
import os
import unittest

from helpers import EXIT_ERROR, EXIT_OK, EXIT_OWNERSHIP, EXIT_USAGE
from test_hooks import AGENT_A, AGENT_B, AGENT_C, COMPLETION, PER_ENTRY_SUM, SESSION, TWO_REQUESTS_SUM, HookCase

MAIN = "-Users-eric-Personal-Spud"  # the main checkout's project directory
WORKTREE = "-Users-eric-Personal-Spud--claude-worktrees-spd-001-tokens"  # a worktree session's, emptied when the session left it
OTHER_WORKTREE = "-Users-eric-Personal-Spud--claude-worktrees-spd-002-other"
OTHER_SESSION = "11111111-2222-4333-8444-555555555555"
# per_block as a SubagentStop stored it before SPD-023 (5 entries added), and as one stores it since (2 requests).
OLD_SUM = {"source": "transcript", "messages": 5, "usage": PER_ENTRY_SUM}
REQUEST_SUM = {"source": "transcript", "counting": "request", "messages": 2, "usage": TWO_REQUESTS_SUM}


class MemberResumTest(HookCase):
    # -- builders -------------------------------------------------------------------
    def transcript(self, project, agent_id, entries=None, session=SESSION):
        """A subagent transcript's path, <home>/projects/<project>/<session>/subagents/agent-<id>.jsonl;
        the file is written when entries are given."""
        path = self.home.path / "projects" / project / session / "subagents" / ("agent-%s.jsonl" % agent_id)
        if entries is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")
        return path

    def old_sum(self, agent_id, recorded, completion=None):
        """A member whose run a SubagentStop recorded before SPD-023: per_block with every entry added (994
        tokens), 3 tool uses over 4500 ms, at the recorded transcript path; with a completion kept beside
        the sum (SPD-021), the completion's whole-run duration and tool count in the columns."""
        m = self.plan()
        self.spawn(m, agent_id)
        self.home.json("member", "result", "Built it.", actor=agent_id)
        usage, duration, tools = dict(OLD_SUM), 4500, 3
        if completion is not None:
            usage["completion"] = completion
            duration, tools = completion["totalDurationMs"], completion["totalToolUseCount"]
        self.set_member(m["id"], stopped_at="2026-09-12T06:30:05-07:00", transcript_path=str(recorded),
                        total_tokens=994, duration_ms=duration, tool_uses=tools, usage_json=json.dumps(usage))
        return m

    def resum(self, *args, actor="spud"):
        """(exit code, the --json answer, the process) of `spud member resum`."""
        proc = self.home.run("--json", "member", "resum", *args, actor=actor, check=False)
        return proc.returncode, (json.loads(proc.stdout) if proc.stdout.strip() else None), proc

    def row(self, m):
        return self.home.rows("SELECT * FROM members WHERE id = ?", m["id"])[0]

    def ledger(self):
        """Every members row and every event: what a run that writes nothing leaves as it found it."""
        return self.home.rows("SELECT * FROM members ORDER BY id"), self.home.rows("SELECT * FROM events ORDER BY id")

    # -- re-summing -----------------------------------------------------------------
    def test_an_old_sum_is_re_summed_from_its_recorded_transcript(self):
        path = self.transcript(MAIN, AGENT_A, self.per_block())
        m = self.old_sum(AGENT_A, path)
        code, out, proc = self.resum(m["ref"])
        self.assertEqual(code, EXIT_OK, proc)
        row = self.row(m)
        self.assertEqual((row["total_tokens"], row["duration_ms"], row["tool_uses"], row["transcript_path"]), (442, 4500, 3, str(path)))
        self.assertEqual(json.loads(row["usage_json"]), REQUEST_SUM)
        self.assertEqual(out["members"], [{
            "ref": m["ref"], "action": "re-sum", "found": "recorded", "transcript": str(path), "note": None, "requests": 2,
            "old": {"total_tokens": 994, "tool_uses": 3, "duration_ms": 4500}, "new": {"total_tokens": 442, "tool_uses": 3, "duration_ms": 4500}}])
        self.assertEqual((out["ok"], out["dry_run"], out["resummed"], out["left"]), (True, False, [m["ref"]], []))

    def test_a_moved_transcript_is_found_by_session_and_file_name(self):
        recorded = self.transcript(WORKTREE, AGENT_B)  # never written: the session left the worktree and its transcripts moved
        moved = self.transcript(MAIN, AGENT_B, self.per_block())
        self.transcript(MAIN, AGENT_B, self.two_requests(), session=OTHER_SESSION)  # the same file name in another session
        self.transcript(MAIN, AGENT_C, self.two_requests())  # another agent's transcript in the same session
        m = self.old_sum(AGENT_B, recorded)
        code, out, proc = self.resum(m["ref"])
        self.assertEqual(code, EXIT_OK, proc)
        row = self.row(m)
        self.assertEqual((row["total_tokens"], row["tool_uses"], row["transcript_path"], json.loads(row["usage_json"])), (442, 3, str(moved), REQUEST_SUM))
        self.assertEqual({k: out["members"][0][k] for k in ("action", "found", "transcript")}, {"action": "re-sum", "found": "moved", "transcript": str(moved)})

    def test_a_missing_or_ambiguous_transcript_is_reported_and_left(self):
        missing = self.old_sum(AGENT_A, self.transcript(WORKTREE, AGENT_A))
        ambiguous = self.old_sum(AGENT_B, self.transcript(WORKTREE, AGENT_B))
        copies = [str(self.transcript(project, AGENT_B, self.per_block())) for project in (MAIN, OTHER_WORKTREE)]
        found = self.old_sum(AGENT_C, self.transcript(MAIN, AGENT_C, self.per_block()))
        before = {m["ref"]: self.row(m) for m in (missing, ambiguous)}
        events = len(self.home.rows("SELECT id FROM events"))
        code, out, proc = self.resum("--all")
        self.assertEqual(code, EXIT_ERROR, proc)
        self.assertEqual([(r["ref"], r["action"]) for r in out["members"]],
                         [(missing["ref"], "not found"), (ambiguous["ref"], "ambiguous"), (found["ref"], "re-sum")])
        self.assertEqual((out["ok"], out["exit"], out["resummed"], out["left"]), (False, EXIT_ERROR, [found["ref"]], [missing["ref"], ambiguous["ref"]]))
        for copy in copies:
            self.assertIn(copy, out["members"][1]["note"])
        for m in (missing, ambiguous):
            self.assertEqual(self.row(m), before[m["ref"]])
            self.assertIn(m["ref"], proc.stderr)
        self.assertEqual(self.row(found)["total_tokens"], 442)  # a member that can be re-summed still is
        self.assertEqual(len(self.home.rows("SELECT id FROM events")) - events, 1)

    def test_a_transcript_without_usage_is_reported_and_left(self):
        path = self.transcript(MAIN, AGENT_A, [{"type": "user", "timestamp": "2026-09-12T13:30:00.000Z", "message": {"role": "user", "content": "hi"}}])
        m = self.old_sum(AGENT_A, path)
        before = self.row(m)
        code, out, proc = self.resum(m["ref"])
        self.assertEqual(code, EXIT_ERROR, proc)
        self.assertEqual((out["members"][0]["action"], out["members"][0]["transcript"], out["left"]), ("no usage", str(path), [m["ref"]]))
        self.assertEqual(self.row(m), before)

    def test_an_unreadable_transcript_is_reported_and_left(self):
        path = self.transcript(MAIN, AGENT_A, self.per_block())
        m = self.old_sum(AGENT_A, path)
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o600)
        if os.access(path, os.R_OK):
            self.skipTest("this user reads a file without read permission")
        before = self.row(m)
        code, out, proc = self.resum(m["ref"])
        self.assertEqual(code, EXIT_ERROR, proc)
        self.assertEqual((out["members"][0]["action"], out["left"]), ("unreadable", [m["ref"]]))
        self.assertEqual(self.row(m), before)

    def test_an_imported_sum_and_a_member_without_a_sum_are_listed_and_left(self):
        imported = self.plan()
        self.set_member(imported["id"], total_tokens=47394682, duration_ms=2127776, tool_uses=105, usage_json=json.dumps({
            "source": "transcript", "imported": True,
            "usage": {"input_tokens": 2888805, "output_tokens": 160812, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 44345065}}))
        alone = self.plan()
        self.set_member(alone["id"], duration_ms=4791, tool_uses=1, usage_json=json.dumps({"source": "PostToolUse", "completion": COMPLETION}))
        planned = self.plan()
        before = self.ledger()
        code, out, proc = self.resum(imported["ref"], alone["ref"], planned["ref"])
        self.assertEqual(code, EXIT_OK, proc)
        self.assertEqual([(r["ref"], r["action"], r["new"]) for r in out["members"]],
                         [(imported["ref"], "imported", None), (alone["ref"], "no sum", None), (planned["ref"], "no sum", None)])
        self.assertEqual((out["resummed"], out["left"]), ([], []))
        self.assertEqual(self.ledger(), before)
        # --all takes every stored transcript sum: the imported one, and no member without a sum
        code, out, proc = self.resum("--all")
        self.assertEqual((code, [(r["ref"], r["action"]) for r in out["members"]]), (EXIT_OK, [(imported["ref"], "imported")]), proc)
        self.assertEqual(self.ledger(), before)

    def test_a_completion_keeps_its_whole_run_duration_and_tool_count(self):
        path = self.transcript(MAIN, AGENT_B, self.per_block())
        m = self.old_sum(AGENT_B, path, completion=COMPLETION)
        code, out, proc = self.resum(m["ref"])
        self.assertEqual(code, EXIT_OK, proc)
        row = self.row(m)
        self.assertEqual((row["total_tokens"], row["duration_ms"], row["tool_uses"]), (442, 4791, 1))
        self.assertEqual(json.loads(row["usage_json"]), dict(REQUEST_SUM, completion=COMPLETION))
        self.assertEqual((out["members"][0]["old"], out["members"][0]["new"]),
                         ({"total_tokens": 994, "tool_uses": 1, "duration_ms": 4791}, {"total_tokens": 442, "tool_uses": 1, "duration_ms": 4791}))

    def test_a_dry_run_writes_nothing_and_prints_the_table_of_the_real_run(self):
        recorded = self.transcript(MAIN, AGENT_A, self.per_block())
        good = self.old_sum(AGENT_A, recorded)
        moved = self.transcript(MAIN, AGENT_B, self.per_block())
        self.old_sum(AGENT_B, self.transcript(WORKTREE, AGENT_B))
        missing = self.old_sum(AGENT_C, self.transcript(WORKTREE, AGENT_C))
        before = self.ledger()
        dry = self.home.run("member", "resum", "--all", "--dry-run", actor="spud", check=False)
        code, out, proc = self.resum("--all", "--dry-run")
        self.assertEqual((dry.returncode, code), (EXIT_ERROR, EXIT_ERROR), (dry, proc))  # the real run's exit: one transcript is missing
        self.assertEqual((out["dry_run"], out["resummed"], out["left"]), (True, [], [missing["ref"]]))
        self.assertEqual([r["action"] for r in out["members"]], ["re-sum", "re-sum", "not found"])
        self.assertEqual(self.ledger(), before)
        real = self.home.run("member", "resum", "--all", actor="spud", check=False)
        self.assertEqual(real.returncode, EXIT_ERROR, real)
        table = dry.stdout.rstrip("\n").split("\n")
        self.assertEqual(table[:-1], real.stdout.rstrip("\n").split("\n")[:-1])
        self.assertEqual(table[0].split(), ["member", "action", "requests", "total_tokens", "tool_uses", "duration_ms", "transcript"])
        self.assertEqual(table[1].split()[:12], [good["ref"], "re-sum", "2", "994", "->", "442", "3", "->", "3", "4500", "->", "4500"])
        self.assertTrue(table[1].endswith("recorded: %s" % recorded), table[1])
        self.assertTrue(table[2].endswith("moved: %s" % moved), table[2])
        self.assertEqual(table[3].split()[:3], [missing["ref"], "not", "found"])
        self.assertIn("dry run", table[-1])
        self.assertNotIn("dry run", real.stdout)
        self.assertEqual(self.row(good)["total_tokens"], 442)

    def test_a_second_run_changes_nothing(self):
        m = self.old_sum(AGENT_A, self.transcript(MAIN, AGENT_A, self.per_block()))
        self.assertEqual(self.resum("--all")[0], EXIT_OK)
        after = self.ledger()
        for args in (("--all",), (m["ref"],), ("--all", "--dry-run")):
            with self.subTest(args=args):
                code, out, proc = self.resum(*args)
                self.assertEqual(code, EXIT_OK, proc)
                self.assertEqual([(r["ref"], r["action"], r["new"]) for r in out["members"]], [(m["ref"], "counted", None)])
                self.assertEqual((out["resummed"], out["left"]), ([], []))
                self.assertEqual(self.ledger(), after)

    def test_the_event_keeps_the_old_and_the_new_figures(self):
        path = self.transcript(MAIN, AGENT_B, self.per_block())
        m = self.old_sum(AGENT_B, path, completion=COMPLETION)
        old_text = self.row(m)["usage_json"]
        self.assertEqual(self.resum(m["ref"])[0], EXIT_OK)
        new_text = self.row(m)["usage_json"]
        events = self.events("member.edited", member=m["ref"])
        self.assertEqual(len(events), 1, events)
        e = events[0]
        self.assertEqual((e["actor"], e["ticket"]), ("spud", self.t["key"]))
        self.assertEqual(e["data"], {
            "fields": ["total_tokens", "usage_json"], "counting": "request", "found": "recorded", "transcript": str(path), "requests": 2,
            "old": {"total_tokens": 994, "tool_uses": 1, "duration_ms": 4791, "transcript_path": str(path), "usage_json": old_text},
            "new": {"total_tokens": 442, "tool_uses": 1, "duration_ms": 4791, "transcript_path": str(path), "usage_json": new_text}})
        self.assertIn("994 -> 442", e["body"])

    # -- who and what -----------------------------------------------------------------
    def test_only_spud_may_re_sum(self):
        m = self.old_sum(AGENT_A, self.transcript(MAIN, AGENT_A, self.per_block()))
        before = self.ledger()
        for actor in (m["ref"], AGENT_A):
            for args in (("--all",), ("--all", "--dry-run"), (m["ref"],)):
                with self.subTest(actor=actor, args=args):
                    proc = self.home.run("member", "resum", *args, actor=actor, check=False)
                    self.assertEqual(proc.returncode, EXIT_OWNERSHIP, proc)
                    self.assertIn("--as spud", proc.stderr)
        self.assertEqual(self.ledger(), before)

    def test_the_members_are_named_or_all_of_them_not_both(self):
        m = self.plan()
        for args in ((), ("--all", m["ref"])):
            with self.subTest(args=args):
                proc = self.home.run("member", "resum", *args, actor="spud", check=False)
                self.assertEqual(proc.returncode, EXIT_USAGE, proc)

    def test_help_names_the_counting_and_the_exit_codes(self):
        text = " ".join(self.home.run("member", "resum", "--help").stdout.split())
        for needle in ("once per API request", '"counting": "request"', "--dry-run", "exit codes: 0", "1 ", "3 "):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
