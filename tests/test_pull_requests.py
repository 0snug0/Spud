"""Landing pull requests (SPD-077): `pr record`, `pr reconcile`, what board, card and doctor say, and since SPD-116 what
the rendered ticket note carries.

BAD-058 showed as `active` for 44 minutes after its work had landed: its session opened the pull request, recorded the
outcome and stopped, the pull request squash-merged later, and no session was left to move the ticket, write the Outcome
or delete the worktree and its branch.  Nothing in the ledger knew a pull request existed.  So Spud records the one a
ticket lands through, the reconciler reads each recorded, still-open one with a single `gh pr view` off every hook path,
and the board -- the full one and the `--brief` line SessionStart injects -- says what a merge leaves owed.

None of it reached the vault, which is the one place Eric reads: SPD-116 puts the two frontmatter keys `pr` and
`pr_state` and a generated `## Landing` section on the ticket note, from `pull_requests` alone.

Every `gh` call here goes to tests/helpers.py's stand-in (GhMixin), and a Home that does not set one up has SPUD_GH=off
and reads nothing: no test in this suite can reach the network.
"""

import json
import unittest

from helpers import EXIT_CONFLICT, EXIT_ERROR, EXIT_OWNERSHIP, EXIT_TRANSITION, EXIT_USAGE, GhMixin, Home, SpudTestCase
from test_hooks import HookCase

URL = "https://github.com/0snug0/BadTakes/pull/361"
URL2 = "https://github.com/0snug0/BadTakes/pull/362"
BRANCH = "feat/bad-058-landing"
MERGED_AT = "2026-09-16T17:12:34Z"


def frontmatter(text):
    lines = text.split("\n")
    return lines[1:lines.index("---", 1)]


def section(text, name):
    return text.split("## %s\n" % name, 1)[1].split("\n## ", 1)[0].strip()


def headings(text):
    return [line for line in text.split("\n") if line.startswith("## ")]


class PullRequestCase(SpudTestCase):
    """An active ticket and a helper that records a pull request against it."""

    def setUp(self):
        super().setUp()
        self.t = self.new_ticket("A ticket that lands by pull request", status="active")

    def record(self, url=URL, ticket=None, branch=BRANCH, worktree="/tmp/wt-bad-058", check=True, actor="spud", **extra):
        args = ["pr", "record", "--ticket", ticket or self.t["key"], "--url", url, "--branch", branch]
        if worktree is not None:
            args += ["--worktree", worktree]
        for k, v in extra.items():
            args += ["--" + k.replace("_", "-"), v]
        return self.home.json(*args, actor=actor, check=check)

    def bind_worktree(self, path):
        """Stand in for SPD-098's binding, which test_ticket_worktree covers: the path `pr record` defaults to."""
        con = self.home.connect()
        try:
            with con:
                con.execute("UPDATE tickets SET worktree = ? WHERE key = ?", (path, self.t["key"]))
        finally:
            con.close()

    def prs(self):
        return self.home.rows("SELECT * FROM pull_requests ORDER BY id")


# =============================================================================
# pr record
# =============================================================================


class RecordTest(PullRequestCase):
    """One writing command records the pull request a ticket lands through, with the branch and the worktree whose
    cleanup the landing will owe.  It is Spud's: opening a pull request is part of landing (Law 10), and a member
    neither commits nor pushes (Law 7), so a member never has one to record."""

    def test_recording_writes_the_row_and_the_event(self):
        out = self.record()
        d = out["pull_request"]
        self.assertEqual((d["ticket"], d["url"], d["number"], d["branch"], d["worktree"]), (self.t["key"], URL, 361, BRANCH, "/tmp/wt-bad-058"))
        self.assertEqual((d["state"], d["settled_at"], d["checked_at"], d["check_error"], d["merged_at"]), ("open", None, None, None, None))
        self.assertEqual(d["recorded_by"], "spud")
        self.assertFalse(out["again"])
        events = self.home.json("events", "--kind", "pr.recorded")["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["actor"], "spud")
        self.assertEqual(events[0]["ticket"], self.t["key"])
        self.assertEqual(events[0]["data"], {"url": URL, "number": 361, "branch": BRANCH, "worktree": "/tmp/wt-bad-058", "again": False})
        self.assertIn("#361", self.home.run("pr", "record", "--ticket", self.t["key"], "--url", URL, "--branch", BRANCH, actor="spud").stdout)

    def test_the_worktree_defaults_to_the_one_the_ticket_is_bound_to(self):
        self.bind_worktree("/tmp/worktrees/bad-058")
        self.assertEqual(self.record(worktree=None)["pull_request"]["worktree"], "/tmp/worktrees/bad-058")
        self.assertEqual(self.record(url=URL2, worktree="/tmp/elsewhere")["pull_request"]["worktree"], "/tmp/elsewhere")

    def test_a_ticket_with_no_worktree_records_none(self):
        self.assertIsNone(self.record(worktree=None)["pull_request"]["worktree"])

    def test_only_spud_records_one(self):
        m = self.new_member(self.t["key"])
        proc = self.home.run("pr", "record", "--ticket", self.t["key"], "--url", URL, "--branch", BRANCH,
                             actor="%s/%s" % (self.t["team_key"], m["name"]), check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP, proc)
        self.assertIn("recording a landing pull request is Spud's", proc.stderr)
        self.assertEqual(self.prs(), [])

    def test_the_url_must_be_an_absolute_http_url(self):
        for bad in ("361", "github.com/o/r/pull/1", "/pull/1", "ssh://git@github.com/o/r", ""):
            with self.subTest(bad):
                proc = self.home.run("pr", "record", "--ticket", self.t["key"], "--url", bad, "--branch", BRANCH, actor="spud", check=False)
                self.assertEqual(proc.returncode, EXIT_ERROR, proc)
                self.assertIn("absolute http(s) URL", proc.stderr)
        self.assertEqual(self.prs(), [])

    def test_a_url_without_a_number_is_recorded_whole(self):
        d = self.record(url="https://example.invalid/o/r/changes/9")["pull_request"]
        self.assertIsNone(d["number"])
        self.assertIn("https://example.invalid/o/r/changes/9", self.home.run("pr", "list").stdout)

    def test_the_branch_is_required_and_not_blank(self):
        proc = self.home.run("pr", "record", "--ticket", self.t["key"], "--url", URL, "--branch", "  ", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE, proc)
        self.assertIn("head branch", proc.stderr)
        proc = self.home.run("pr", "record", "--ticket", self.t["key"], "--url", URL, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE, proc)

    def test_a_closed_ticket_records_none(self):
        self.home.json("ticket", "move", self.t["key"], "--status", "done", actor="spud")
        proc = self.home.run("pr", "record", "--ticket", self.t["key"], "--url", URL, "--branch", BRANCH, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_TRANSITION, proc)
        self.assertIn("still open", proc.stderr)
        self.assertEqual(self.prs(), [])

    def test_recording_the_same_url_again_refreshes_the_branch_and_the_worktree(self):
        self.record()
        out = self.record(branch="fix/renamed", worktree="/tmp/moved")
        self.assertTrue(out["again"])
        self.assertEqual([(p["branch"], p["worktree"]) for p in self.prs()], [("fix/renamed", "/tmp/moved")])
        self.assertIn("again", self.home.run("pr", "record", "--ticket", self.t["key"], "--url", URL, "--branch", "fix/renamed", actor="spud").stdout)

    def test_one_pull_request_lands_one_ticket(self):
        self.record()
        other = self.new_ticket("Another", status="active")
        proc = self.home.run("pr", "record", "--ticket", other["key"], "--url", URL, "--branch", BRANCH, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        self.assertIn("already recorded against %s" % self.t["key"], proc.stderr)

    def test_no_such_ticket(self):
        proc = self.home.run("pr", "record", "--ticket", "SPD-999", "--url", URL, "--branch", BRANCH, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        self.assertIn("no ticket SPD-999", proc.stderr)


# =============================================================================
# pr reconcile
# =============================================================================


class ReconcileTest(GhMixin, PullRequestCase):
    """One `gh pr view` per recorded, still-open pull request, run in its ticket's project checkout, with the answer and
    the time it was read stored.  A read that fails is a stored failed check and never stops the run; a settled pull
    request is never read again; and nothing here merges anything, runs git or moves a ticket."""

    def setUp(self):
        super().setUp()
        self.setup_gh()
        self.record()

    def test_a_merge_reaches_the_ledger_with_no_session_and_no_move(self):
        self.answer_pr(URL, state="MERGED", merged_at=MERGED_AT, number=361)
        out = self.home.json("pr", "reconcile")
        self.assertEqual(out["reconcile"]["settled"], [URL])
        p = self.prs()[0]
        self.assertEqual((p["state"], p["merged_at"]), ("merged", MERGED_AT))
        self.assertTrue(p["settled_at"] and p["checked_at"])
        self.assertIsNone(p["check_error"])
        # the one read, in the ticket's project checkout, of the recorded URL and nothing else
        self.assertEqual([c["args"] for c in self.gh_calls()], [["pr", "view", URL, "--json", "state,mergedAt,url,number"]])
        self.assertEqual(self.gh_calls()[0]["cwd"], self.home.scalar("SELECT root_path FROM projects WHERE id = 1"))
        # the state change is the reconciler's, under no person's name, and the ticket has not moved
        events = self.home.json("events", "--kind", "pr.state")["events"]
        self.assertEqual([(e["actor"], e["ticket"], e["data"]["to"]) for e in events], [("reconcile", self.t["key"], "merged")])
        self.assertEqual(self.home.json("ticket", "show", self.t["key"])["ticket"]["status"], "active")

    def test_a_closed_unmerged_pull_request_is_recorded_and_nothing_is_moved(self):
        self.answer_pr(URL, state="CLOSED", number=361)
        self.home.json("pr", "reconcile")
        p = self.prs()[0]
        self.assertEqual((p["state"], p["merged_at"]), ("closed", None))
        self.assertTrue(p["settled_at"])
        self.assertEqual(self.home.json("ticket", "show", self.t["key"])["ticket"]["status"], "active")
        self.assertEqual([e["data"]["to"] for e in self.home.json("events", "--kind", "pr.state")["events"]], ["closed"])

    def test_an_open_pull_request_stays_open_and_writes_no_event(self):
        self.answer_pr(URL, state="OPEN", number=361)
        self.home.json("pr", "reconcile")
        p = self.prs()[0]
        self.assertEqual((p["state"], p["settled_at"]), ("open", None))
        self.assertTrue(p["checked_at"])
        self.assertEqual(self.home.json("events", "--kind", "pr.state")["events"], [])

    def test_a_settled_pull_request_is_never_read_again(self):
        self.answer_pr(URL, state="MERGED", merged_at=MERGED_AT, number=361)
        self.home.json("pr", "reconcile")
        self.home.json("pr", "reconcile")
        self.assertEqual(len(self.gh_calls()), 1)

    def test_a_failed_read_is_stored_and_does_not_stop_the_run(self):
        self.record(url=URL2, branch="feat/second")
        self.answer_pr(URL, error="gh: HTTP 403: API rate limit exceeded")
        self.answer_pr(URL2, state="MERGED", merged_at=MERGED_AT, number=362)
        out = self.home.json("pr", "reconcile")
        self.assertEqual((out["reconcile"]["failed"], out["reconcile"]["settled"]), (1, [URL2]))
        first, second = self.prs()
        self.assertEqual((first["state"], first["check_error"]), ("open", "gh: HTTP 403: API rate limit exceeded"))
        self.assertTrue(first["checked_at"])
        self.assertEqual(second["state"], "merged")

    def test_every_way_a_read_can_fail_is_a_stored_failed_check(self):
        cases = {"a url github does not know": (dict(), "could not resolve"),
                 "output that is not json": (dict(unparseable=True), "printed no JSON"),
                 "a state nobody knows": (dict(state="DRAFT"), "not OPEN, MERGED or CLOSED")}
        for label, (answer, expected) in cases.items():
            with self.subTest(label):
                if answer:
                    self.answer_pr(URL, **answer)
                else:
                    self.forget_pr(URL)
                self.home.json("pr", "reconcile")
                p = self.prs()[0]
                self.assertEqual(p["state"], "open")
                self.assertIn(expected, p["check_error"])

    def test_a_missing_gh_is_a_stored_failed_check_not_a_crash(self):
        self.home.env["SPUD_GH"] = str(self.home.path / "no-such-gh")
        out = self.home.json("pr", "reconcile")
        self.assertEqual(out["reconcile"]["failed"], 1)
        self.assertIn("cannot run", self.prs()[0]["check_error"])

    def test_a_successful_read_clears_an_earlier_failure(self):
        self.answer_pr(URL, error="gh: could not connect")
        self.home.json("pr", "reconcile")
        self.assertTrue(self.prs()[0]["check_error"])
        self.answer_pr(URL, state="OPEN", number=361)
        self.home.json("pr", "reconcile")
        self.assertIsNone(self.prs()[0]["check_error"])

    def test_the_reader_turned_off_reads_nothing_and_says_so(self):
        self.home.env["SPUD_GH"] = "off"
        proc = self.home.run("pr", "reconcile")
        self.assertIn("SPUD_GH=off", proc.stdout)
        self.assertEqual(self.gh_calls(), [])
        self.assertTrue(self.home.json("pr", "reconcile")["reconcile"]["off"])
        self.assertIsNone(self.prs()[0]["checked_at"])

    def test_stale_skips_a_pull_request_read_recently(self):
        self.answer_pr(URL, state="OPEN", number=361)
        self.home.json("pr", "reconcile")
        out = self.home.json("pr", "reconcile", "--stale", "600")
        self.assertEqual((out["reconcile"]["skipped"], len(self.gh_calls())), (1, 1))
        out = self.home.json("pr", "reconcile")  # --stale defaults to 0: read it now
        self.assertEqual((out["reconcile"]["skipped"], len(self.gh_calls())), (0, 2))

    def test_one_ticket_at_a_time(self):
        other = self.new_ticket("Another", status="active")
        self.home.json("pr", "record", "--ticket", other["key"], "--url", URL2, "--branch", "feat/other", actor="spud")
        self.answer_pr(URL, state="OPEN", number=361)
        self.answer_pr(URL2, state="OPEN", number=362)
        self.home.json("pr", "reconcile", "--ticket", other["key"])
        self.assertEqual([c["args"][2] for c in self.gh_calls()], [URL2])
        proc = self.home.run("pr", "reconcile", "--ticket", "SPD-999", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)

    def test_a_spent_budget_stops_the_run_and_says_so(self):
        self.record(url=URL2, branch="feat/second")
        self.answer_pr(URL, state="OPEN", number=361)
        self.answer_pr(URL2, state="OPEN", number=362)
        out = self.home.json("pr", "reconcile", "--budget", "0")
        self.assertTrue(out["reconcile"]["out_of_budget"])
        self.assertEqual(self.gh_calls(), [])

    def test_reconcile_needs_no_actor_and_ignores_one(self):
        self.answer_pr(URL, state="MERGED", merged_at=MERGED_AT, number=361)
        self.home.json("pr", "reconcile", actor="spud")
        self.assertEqual([e["actor"] for e in self.home.json("events", "--kind", "pr.state")["events"]], ["reconcile"])


# =============================================================================
# the board, the brief board and the card
# =============================================================================


class BoardTest(GhMixin, PullRequestCase):
    """The full board reconciles and surfaces; `board --brief`, which SessionStart injects, reads stored state alone."""

    def setUp(self):
        super().setUp()
        self.setup_gh()
        self.record()

    def merge(self):
        self.answer_pr(URL, state="MERGED", merged_at=MERGED_AT, number=361)
        self.home.json("pr", "reconcile")

    def test_the_board_reconciles_and_shows_a_merge_with_what_it_owes(self):
        self.answer_pr(URL, state="MERGED", merged_at=MERGED_AT, number=361)
        text = self.home.run("board").stdout
        self.assertEqual(len(self.gh_calls()), 1)
        self.assertIn("pull requests:", text)
        self.assertIn("#361 merged 2026-09-16T17:12", text)
        self.assertIn(URL, text)
        self.assertIn("owed: the done move, with the pull request URL in %s's Outcome" % self.t["key"], text)
        self.assertIn("`git worktree remove /tmp/wt-bad-058`", text)
        self.assertIn("`git branch -D %s`" % BRANCH, text)
        d = self.home.json("board")["pull_requests"][0]
        self.assertEqual((d["state"], d["branch"], d["worktree"]), ("merged", BRANCH, "/tmp/wt-bad-058"))

    def test_a_closed_unmerged_pull_request_is_surfaced_and_owes_nothing(self):
        self.answer_pr(URL, state="CLOSED", number=361)
        text = self.home.run("board").stdout
        self.assertIn("#361 closed unmerged", text)
        self.assertNotIn("owed:", text)

    def test_an_open_pull_request_shows_when_it_was_read(self):
        self.answer_pr(URL, state="OPEN", number=361)
        self.assertRegex(self.home.run("board").stdout, r"#361 open, read (just now|\d+s ago)")
        self.assertNotIn("owed:", self.home.run("board", "--no-reconcile").stdout)

    def test_a_failed_read_is_named_on_the_board(self):
        self.answer_pr(URL, error="gh: HTTP 403: API rate limit exceeded")
        self.assertIn("open, last read failed", self.home.run("board").stdout)
        self.assertIn("API rate limit exceeded", self.home.run("board", "--no-reconcile").stdout)

    def test_no_reconcile_and_the_reader_off_read_nothing(self):
        self.answer_pr(URL, state="MERGED", merged_at=MERGED_AT, number=361)
        self.assertIn("#361 open, never read", self.home.run("board", "--no-reconcile").stdout)
        self.home.env["SPUD_GH"] = "off"
        self.assertIn("#361 open, never read", self.home.run("board").stdout)
        self.assertEqual(self.gh_calls(), [])

    def test_the_board_leaves_a_fresh_check_alone(self):
        self.answer_pr(URL, state="OPEN", number=361)
        self.home.run("board")
        self.home.run("board")
        self.assertEqual(len(self.gh_calls()), 1)

    def test_a_ticket_moved_to_done_owes_nothing_and_leaves_the_block(self):
        self.merge()
        self.assertIn("owed:", self.home.run("board").stdout)
        self.home.json("ticket", "move", self.t["key"], "--status", "done", actor="spud")
        text = self.home.run("board").stdout
        self.assertNotIn("pull requests:", text)
        self.assertNotIn("owed:", text)
        self.assertEqual(self.home.json("pr", "list")["pull_requests"][0]["state"], "merged")  # kept as history

    def test_the_brief_board_nags_without_reading_anything(self):
        self.merge()
        calls = len(self.gh_calls())
        text = self.home.run("board", "--brief").stdout
        self.assertEqual(len(self.gh_calls()), calls)
        self.assertIn("  pull request #361 merged 2026-09-16T17:12; owed: the done move", text)
        self.assertNotIn("pull_requests", json.dumps(self.home.json("board", "--brief")))

    def test_the_brief_board_says_nothing_about_an_open_pull_request(self):
        self.answer_pr(URL, state="OPEN", number=361)
        self.home.json("pr", "reconcile")
        self.assertNotIn("  pull request", self.home.run("board", "--brief").stdout)

    def test_a_queued_ticket_nags_too(self):
        other = self.new_ticket("Queued with a merge")
        self.home.json("pr", "record", "--ticket", other["key"], "--url", URL2, "--branch", "feat/other", actor="spud")
        self.answer_pr(URL2, state="MERGED", merged_at=MERGED_AT, number=362)
        self.home.json("pr", "reconcile")
        self.assertIn("  pull request #362 merged", self.home.run("board", "--brief").stdout)

    def test_the_card_shows_the_pull_request_and_what_it_owes(self):
        self.merge()
        text = self.home.run("card", self.t["key"]).stdout
        self.assertIn("pull request #361 merged 2026-09-16T17:12  %s" % URL, text)
        self.assertIn("owed: the done move", text)
        self.assertEqual(self.home.json("card", self.t["key"])["pull_requests"][0]["url"], URL)

    def test_pr_list_shows_everything_recorded(self):
        self.merge()
        self.home.json("pr", "record", "--ticket", self.t["key"], "--url", URL2, "--branch", "feat/second", actor="spud")
        self.assertEqual(len(self.home.json("pr", "list")["pull_requests"]), 2)
        self.assertEqual([d["url"] for d in self.home.json("pr", "list", "--open")["pull_requests"]], [URL2])
        self.assertEqual(len(self.home.json("pr", "list", "--ticket", self.t["key"])["pull_requests"]), 2)
        self.assertEqual(len(self.home.json("pr", "list", "--project", "spud")["pull_requests"]), 2)
        bare = self.new_ticket("Bare")
        self.assertIn("(no pull request recorded)", self.home.run("pr", "list", "--ticket", bare["key"]).stdout)


class SessionStartTest(GhMixin, HookCase):
    """The nag reaches the model: SessionStart injects `board --brief`, so a merge is in front of the next session that
    starts anywhere, which is exactly what BAD-058 lacked."""

    def setUp(self):
        super().setUp()
        self.setup_gh()
        self.home.json("pr", "record", "--ticket", self.t["key"], "--url", URL, "--branch", BRANCH, "--worktree", "/tmp/wt", actor="spud")
        self.answer_pr(URL, state="MERGED", merged_at=MERGED_AT, number=361)
        self.home.json("pr", "reconcile")

    def test_the_injection_carries_the_merged_pull_request(self):
        calls = len(self.gh_calls())
        r = self.home.hook("SessionStart", self.session_start())
        self.assertEqual(r.code, 0, r)
        self.assertIn("pull request #361 merged", r.context)
        self.assertIn("owed: the done move", r.context)
        self.assertEqual(len(self.gh_calls()), calls)  # no hook ever reads the network


# =============================================================================
# doctor
# =============================================================================


class DoctorTest(GhMixin, PullRequestCase):
    """doctor reports the recorded pull requests and every failed read, the way it reports a down render watcher: nothing
    in the ledger is broken, but until the read succeeds a merge that already happened stays invisible."""

    def setUp(self):
        super().setUp()
        self.setup_gh()

    def test_a_clean_home_reports_none_and_stays_green(self):
        proc = self.home.run("doctor")
        self.assertIn("pull reqs   0 recorded, 0 open, 0 settled", proc.stdout)
        self.assertEqual(self.home.json("doctor")["pull_requests"]["failed_checks"], [])

    def test_a_failed_read_is_a_problem_naming_the_retry(self):
        self.record()
        self.answer_pr(URL, error="gh: HTTP 403: API rate limit exceeded")
        self.home.json("pr", "reconcile")
        proc = self.home.run("doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        self.assertIn("the last read of #361 (%s) failed" % self.t["key"], proc.stderr)
        self.assertIn("spud pr reconcile --ticket %s" % self.t["key"], proc.stderr)
        report = self.home.json("doctor", check=False)
        self.assertEqual([d["url"] for d in report["pull_requests"]["failed_checks"]], [URL])
        self.assertEqual((report["pull_requests"]["recorded"], report["pull_requests"]["open"]), (1, 1))

    def test_a_failed_read_on_a_closed_ticket_is_no_problem(self):
        self.record()
        self.answer_pr(URL, error="gh: could not connect")
        self.home.json("pr", "reconcile")
        self.home.json("ticket", "move", self.t["key"], "--status", "done", actor="spud")
        self.assertEqual(self.home.run("doctor").returncode, 0)

    def test_a_settled_pull_request_is_counted_and_the_reader_named(self):
        self.record()
        self.answer_pr(URL, state="MERGED", merged_at=MERGED_AT, number=361)
        self.home.json("pr", "reconcile")
        report = self.home.json("doctor")
        self.assertEqual((report["pull_requests"]["recorded"], report["pull_requests"]["settled"]), (1, 1))
        self.assertEqual(report["pull_requests"]["reader"], str(self.gh))

    def test_the_reader_off_with_rows_recorded_is_a_note(self):
        self.record()
        self.home.env["SPUD_GH"] = "off"
        report = self.home.json("doctor")
        self.assertEqual(report["pull_requests"]["reader"], "off")
        self.assertTrue(any("SPUD_GH=off" in n for n in report["notes"]))


# =============================================================================
# the surface every command promises
# =============================================================================


class HelpTest(PullRequestCase):
    def test_help_answers_for_the_family_and_each_subcommand(self):
        top = self.home.run("--help").stdout
        self.assertIn("pr ", top)
        self.assertIn("landing pull requests", top)
        family = self.home.run("pr", "--help").stdout
        for word in ("record", "reconcile", "list", "never merges a pull request", "SPUD_GH", "## Landing"):
            self.assertIn(word, family)
        for sub in ("record", "reconcile", "list"):
            with self.subTest(sub):
                self.assertIn("usage: spud pr " + sub, self.home.run("pr", sub, "--help").stdout)
        self.assertIn("--no-reconcile", self.home.run("board", "--help").stdout)

    def test_json_answers_on_each_of_them(self):
        self.assertIn("pull_requests", self.home.json("pr", "list"))
        self.assertIn("pull_request", self.record())
        self.assertIn("reconcile", self.home.json("pr", "reconcile"))
        self.assertIn("pull_requests", self.home.json("board"))
        self.assertIn("pull_requests", self.home.json("card", self.t["key"]))
        self.assertIn("pull_requests", self.home.json("doctor"))

    def test_the_events_command_knows_the_two_new_kinds(self):
        self.record()
        self.assertEqual(len(self.home.json("events", "--kind", "pr.recorded")["events"]), 1)
        self.assertEqual(self.home.json("events", "--kind", "pr.state")["events"], [])


# =============================================================================
# the rendered note: the two keys and ## Landing (SPD-116)
# =============================================================================


class LandingCase(GhMixin, PullRequestCase):
    """A ticket, a recorded pull request, and the note the render writes for it."""

    def setUp(self):
        super().setUp()
        self.setup_gh()
        self.note = self.home.path / "ledger" / "tickets" / ("%s.md" % self.t["key"])

    def settle(self, url=URL, state="MERGED", number=361, merged_at=MERGED_AT):
        self.answer_pr(url, state=state, merged_at=merged_at if state == "MERGED" else None, number=number)
        self.home.json("pr", "reconcile")

    def rendered(self):
        self.home.json("render")
        return self.note.read_text(encoding="utf-8")


class LandingNoteTest(LandingCase):
    """What Eric reads in Obsidian.  A ticket with a recorded pull request carries the number and the state as
    properties a Bases view can filter and sort on, and one paragraph per pull request in ## Landing with the URL as a
    link -- never as a property value, which Obsidian would read as a URL scheme (Ledger v1's `word:text` rule)."""

    def test_an_open_pull_request_renders_the_two_keys_and_one_sentence(self):
        self.record()
        text = self.rendered()
        self.assertEqual(frontmatter(text), [
            "id: " + self.t["key"],
            'title: "A ticket that lands by pull request"',
            "priority: P2",
            "status: active",
            "pr: 361",
            "pr_state: open",
            "origin: owner",
            "project: spud",
            'proposed_by: ""',
            'lead: ""',
            "created: " + self.t["created_at"][:10],
            "tags: [ticket]",
        ])
        self.assertEqual(section(text, "Landing"), "Pull request [#361](%s) — open." % URL)
        self.assertEqual(headings(text), ["## Brief", "## Size, persona and model decision", "## Team", "## Handoffs",
                                          "## Proposals received", "## Landing", "## Outcome"])
        self.assertNotIn("http", "\n".join(frontmatter(text)))  # the URL stays out of the frontmatter

    def test_a_merge_renders_its_state_its_minute_and_what_it_owes(self):
        self.record()
        self.settle()
        text = self.rendered()
        self.assertIn("pr: 361", frontmatter(text))
        self.assertIn("pr_state: merged", frontmatter(text))
        self.assertEqual(section(text, "Landing").split("\n\n"), [
            "Pull request [#361](%s) — merged 2026-09-16T17:12." % URL,
            "Owed: the done move, with the pull request URL in %s's Outcome; `git worktree remove /tmp/wt-bad-058`;"
            " `git branch -D %s`." % (self.t["key"], BRANCH),
        ])

    def test_a_closed_unmerged_pull_request_renders_and_owes_nothing(self):
        self.record()
        self.settle(state="CLOSED")
        text = self.rendered()
        self.assertIn("pr_state: closed", frontmatter(text))
        landing = section(text, "Landing")
        self.assertTrue(landing.startswith("Pull request [#361](%s) — closed unmerged " % URL), landing)
        self.assertTrue(landing.endswith("."), landing)
        self.assertNotIn("Owed:", text)

    def test_a_done_ticket_keeps_the_landing_and_owes_nothing(self):
        self.record()
        self.settle()
        self.home.json("ticket", "move", self.t["key"], "--status", "done", actor="spud")
        text = self.rendered()
        self.assertIn("pr_state: merged", frontmatter(text))
        self.assertIn("merged 2026-09-16T17:12.", section(text, "Landing"))
        self.assertNotIn("Owed:", text)

    def test_the_note_carries_no_relative_time_and_no_read(self):
        """The trap named in the brief: the watcher renders within seconds of every write, so a stamp that is never
        identical twice would churn every ticket note forever.  The read behind the state is `spud board`'s and `spud
        doctor`'s, live; the note carries the landing alone."""
        self.record()
        self.settle()
        self.home.run("board")
        text = self.rendered()
        checked = self.home.json("pr", "list")["pull_requests"][0]["checked_at"]
        for word in (" ago", "just now", "never read", "last read", checked):
            self.assertNotIn(word, text)

    def test_a_second_render_writes_nothing_and_a_re_read_changes_no_note(self):
        self.record()
        self.home.json("render")
        snapshot = lambda: (self.home.scalar("SELECT max(id) FROM events"),
                            self.home.rows("SELECT path, sha256, rendered_at FROM renders ORDER BY path"))
        before = snapshot()
        again = self.home.json("render")
        self.assertEqual((again["written"], again["conflicts"], again["restyled"]), ([], [], []))
        self.assertEqual(snapshot(), before)
        # a reconcile read that found the pull request still open writes the row it read, and no note changes by a byte
        self.answer_pr(URL, state="OPEN", number=361)
        text = self.note.read_text(encoding="utf-8")
        for _ in range(2):
            self.home.json("pr", "reconcile", "--stale", "0")
            self.assertEqual(self.home.json("render")["written"], [])
        self.assertEqual(self.note.read_text(encoding="utf-8"), text)

    def test_a_ticket_with_no_pull_request_renders_exactly_as_it_did(self):
        other = self.new_ticket("No landing of its own")
        self.home.json("render")
        elsewhere = self.home.path / "ledger" / "tickets" / ("%s.md" % other["key"])
        untouched, before = elsewhere.read_bytes(), self.note.read_text(encoding="utf-8")
        for word in ("pr:", "pr_state:", "## Landing"):
            self.assertNotIn(word, before)
        self.assertNotIn("pr:", elsewhere.read_text(encoding="utf-8"))
        self.record()
        out = self.home.json("render")
        self.assertEqual(out["written"], ["ledger/tickets/%s.md" % self.t["key"]])  # the other note is not even rewritten
        self.assertEqual(elsewhere.read_bytes(), untouched)
        self.assertNotIn("## Landing", elsewhere.read_text(encoding="utf-8"))

    def test_the_keys_name_the_newest_pull_request_and_the_section_lists_every_one(self):
        """A ticket carries several rows over its life (one closed unmerged, a second opened), and two scalar keys
        cannot hold several: they name the newest recorded row, the one `pr record` last said the ticket lands
        through.  ## Landing lists them all in record order, and `pr list` stays the whole record."""
        self.record()
        self.settle(state="CLOSED")
        self.record(url=URL2, branch="feat/second-try")
        text = self.rendered()
        self.assertIn("pr: 362", frontmatter(text))
        self.assertIn("pr_state: open", frontmatter(text))
        paragraphs = section(text, "Landing").split("\n\n")
        self.assertEqual(len(paragraphs), 2)
        self.assertIn("[#361](%s) — closed unmerged" % URL, paragraphs[0])
        self.assertEqual(paragraphs[1], "Pull request [#362](%s) — open." % URL2)
        self.settle(url=URL2, number=362)
        self.assertIn("pr_state: merged", frontmatter(self.rendered()))

    def test_a_url_that_carried_no_number_leaves_the_key_empty(self):
        self.record(url="https://git.example.com/badtakes/merge_requests")
        text = self.rendered()
        self.assertIn('pr: ""', frontmatter(text))
        self.assertIn("pr_state: open", frontmatter(text))
        self.assertEqual(section(text, "Landing"),
                         "Pull request [https://git.example.com/badtakes/merge_requests]"
                         "(https://git.example.com/badtakes/merge_requests) — open.")

    def test_a_parked_ticket_carries_both_pairs_in_the_templates_order(self):
        self.record()
        self.home.json("ticket", "move", self.t["key"], "--status", "parked", "--reason", "Eric's review",
                       "--until", "2026-10-16", actor="spud")
        fm = frontmatter(self.rendered())
        self.assertEqual(fm[3:8], ["status: parked", "parked_until: 2026-10-16", 'parked_reason: "Eric\'s review"',
                                   "pr: 361", "pr_state: open"])

    def test_the_section_lands_before_the_outcome_of_a_note_whose_layout_predates_it(self):
        """An imported note's stored layout has no ## Landing (the import never rebuilds pull_requests), so the render
        puts the section where the template has it the moment a pull request is recorded."""
        con = self.home.connect()
        try:
            with con:
                con.execute("UPDATE tickets SET layout = ? WHERE key = ?", (json.dumps(
                    {"sections": ["Brief", "Size, persona and model decision", "Team", "Handoffs", "Proposals received", "Outcome"]}),
                    self.t["key"]))
        finally:
            con.close()
        self.assertNotIn("## Landing", self.rendered())
        self.record()
        self.assertEqual(headings(self.rendered())[-3:], ["## Proposals received", "## Landing", "## Outcome"])


class LandingHandEditTest(LandingCase):
    """The new section and the new keys are inside the hand-edit machinery like everything else the render owns: the
    render refuses to overwrite the edit and logs the conflict once, `doctor` lists it with the two commands that
    settle it, `render --discard` puts the render back, and `import --file` refuses each of them by name."""

    def setUp(self):
        super().setUp()
        self.record()
        self.settle()
        self.home.json("render")

    def hand_edit(self, old, new):
        text = self.note.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, old)
        self.note.write_text(text.replace(old, new), encoding="utf-8")
        return text

    def test_an_edit_of_the_section_is_a_conflict_doctor_lists_and_discard_settles(self):
        rendered = self.hand_edit("— merged 2026-09-16T17:12.", "— merged, I think.")
        for _ in range(3):
            self.assertEqual(self.home.run("render", check=False).returncode, EXIT_CONFLICT)
        conflicts = self.home.rows("SELECT data FROM events WHERE kind = 'render' AND json_extract(data, '$.conflict') = 1")
        self.assertEqual(len(conflicts), 1)  # once per path and on-disk hash
        self.assertEqual(json.loads(conflicts[0]["data"])["path"], "ledger/tickets/%s.md" % self.t["key"])
        proc = self.home.run("doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        self.assertIn("hand-edited ledger/tickets/%s.md" % self.t["key"], proc.stderr)
        self.assertIn("render --discard ledger/tickets/%s.md" % self.t["key"], proc.stderr)
        out = self.home.json("render", "--discard", self.note, actor="spud")
        self.assertEqual((out["discarded"], out["conflicts"]), (["ledger/tickets/%s.md" % self.t["key"]], []))
        self.assertEqual(self.note.read_text(encoding="utf-8"), rendered)
        discarded = self.home.rows("SELECT data FROM events WHERE kind = 'render' AND json_extract(data, '$.discarded') = 1")
        self.assertIn("— merged, I think.", json.loads(discarded[0]["data"])["text"])
        self.assertEqual(self.home.json("render")["written"], [])

    def test_import_file_refuses_each_of_them_by_name(self):
        cases = [
            ("pr: 361", "pr: 362", "pr and pr_state are the landing pull request"),
            ("pr_state: merged", "pr_state: open", "pr and pr_state are the landing pull request"),
            ("Pull request [#361]", "Pull request, maybe, [#361]", "## Landing is generated from the ledger"),
            ("Owed: the done move", "Owed: nothing at all", "spud pr record|reconcile"),
        ]
        rendered = self.note.read_text(encoding="utf-8")
        for old, new, message in cases:
            with self.subTest(old):
                self.hand_edit(old, new)
                self.assertEqual(self.home.run("render", check=False).returncode, EXIT_CONFLICT)  # each one is seen
                proc = self.home.run("import", "--file", self.note, actor="spud", check=False)
                self.assertEqual(proc.returncode, EXIT_ERROR, proc)
                self.assertIn(message, proc.stderr)
                self.note.write_text(rendered, encoding="utf-8")
        self.assertEqual(self.home.json("render")["written"], [])  # nothing was accepted, so nothing changed

    def test_an_edit_of_a_section_eric_owns_is_still_accepted_beside_it(self):
        """The new section refuses its own edits without standing in the way of the ones `import --file` allows: the
        Brief of a note that carries a ## Landing is accepted, and the landing renders on untouched."""
        self.hand_edit("## Brief\n", "## Brief\nEric wrote this by hand.\n")
        accepted = self.home.json("import", "--file", self.note, actor="spud")
        self.assertEqual(accepted["changed"], ["brief"])
        self.assertEqual(self.home.json("ticket", "show", self.t["key"])["ticket"]["brief"], "Eric wrote this by hand.")
        text = self.rendered()
        self.assertIn("merged 2026-09-16T17:12.", section(text, "Landing"))
        self.assertIn("pr: 361", frontmatter(text))

    def test_deleting_the_section_by_hand_is_refused(self):
        text = self.note.read_text(encoding="utf-8")
        before, rest = text.split("## Landing\n", 1)
        self.note.write_text(before + "## Outcome" + rest.split("## Outcome", 1)[1], encoding="utf-8")
        proc = self.home.run("import", "--file", self.note, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        self.assertIn("the note's sections are fixed", proc.stderr)
        self.assertIn("deleted: Landing", proc.stderr)


class LandingImportTest(LandingCase):
    """The rendered markdown is the ledger's disaster-recovery import source, and `pull_requests` is the one table it
    cannot rebuild: the note carries the number, the state and the URL, but not the branch, the worktree, who recorded
    the pull request or when it was last read -- and a merged row whose ticket is done shows no owed line at all, so
    the columns that make a row actionable are exactly the ones the note drops.  A rebuilt row would claim a `pr
    record` and a `gh pr view` that never happened.  So the import reads past the two keys and the section, names them
    in its event, and Spud re-records the pull request with one `spud pr record` -- its URL is in the note."""

    def test_the_tree_import_reads_past_the_landing_and_the_note_renders_without_it(self):
        self.record()
        self.settle()
        first = self.home.path / "first"
        self.home.json("render", "--out", first)
        source = (first / "ledger" / "tickets" / ("%s.md" % self.t["key"])).read_text(encoding="utf-8")
        self.assertIn("pr: 361", source)
        other = Home()
        self.addCleanup(other.cleanup)
        other.init()
        counts = other.json("import", first / "ledger")
        self.assertEqual((counts["tickets"], counts["prose_sections"]), (1, 0))  # no ## Landing prose is ever stored
        self.assertEqual(other.scalar("SELECT count(*) FROM pull_requests"), 0)
        self.assertEqual(other.rows("SELECT layout FROM tickets"), [{"layout": None}])  # nor is it part of the layout
        data = [json.loads(r["data"]) for r in other.rows("SELECT data FROM events WHERE kind = 'import' AND ticket_id IS NOT NULL")]
        self.assertEqual([d.get("dropped") for d in data], [["pr", "pr_state", "Landing"]])
        second = other.path / "second"
        other.json("render", "--out", second)
        before, rest = source.replace("pr: 361\n", "").replace("pr_state: merged\n", "").split("## Landing\n", 1)
        want = before + "## Outcome" + rest.split("## Outcome", 1)[1]
        self.assertEqual((second / "ledger" / "tickets" / ("%s.md" % self.t["key"])).read_text(encoding="utf-8"), want)
        # and the pull request is one command away, its URL in the note Eric is looking at
        other.json("pr", "record", "--ticket", self.t["key"], "--url", URL, "--branch", BRANCH, actor="spud")
        other.json("render")
        self.assertIn("## Landing", (other.path / "ledger" / "tickets" / ("%s.md" % self.t["key"])).read_text(encoding="utf-8"))

    def test_a_ticket_note_with_no_landing_imports_and_renders_byte_for_byte(self):
        first = self.home.path / "first"
        self.home.json("render", "--out", first)
        other = Home()
        self.addCleanup(other.cleanup)
        other.init()
        other.json("import", first / "ledger")
        second = other.path / "second"
        other.json("render", "--out", second)
        rel = "ledger/tickets/%s.md" % self.t["key"]
        self.assertEqual((second / rel).read_bytes(), (first / rel).read_bytes())
        self.assertEqual(other.rows("SELECT layout FROM tickets"), [{"layout": None}])
        data = [json.loads(r["data"]) for r in other.rows("SELECT data FROM events WHERE kind = 'import' AND ticket_id IS NOT NULL")]
        self.assertEqual([d.get("dropped") for d in data], [None])


if __name__ == "__main__":
    unittest.main()
