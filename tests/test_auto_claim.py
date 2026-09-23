"""The UserPromptSubmit hook's auto-claim (SPD-057).

Eric's call of 2026-09-14: in a project whose sessions are `claim` (BadTakes), a session whose prompt names one of that
project's tickets becomes Spud without /spud.  Three BadTakes sessions started "Work on BAD-006" and one "Start on BAD-020";
each stayed plain and did its work outside the ledger.

The hook claims only a `plain` session (unclaimed, launched in a claim project), only on a key of that launch project's
existing, not declined tickets, spelled as the ledger spells a key: never the older tracker's two-digit numbers BadTakes'
pull requests and TODO.md cite (BAD-23 to BAD-36), BAD-0060 or XBAD-006, another project's key, a prompt in the home or
an always project, a claimed session, a subagent's call, /spud itself, or a session `session release` released.  A done
ticket claims: Eric may be reopening it.  The context it returns is the /spud skill's steps from the same source as the
installed SKILL.md, the title step naming the key, and the claim card, within SessionStart's inline limit.

The unit tests load the program; the rest run the hook as a project's installed hook line runs it (test_hooks_projects).
"""

import json
import re
import unittest
from pathlib import Path

from helpers import load_spud_module
from test_hooks import AGENT_A, SESSION, common
from test_hooks_projects import KEY, LAW_1, NOT_SPUD, SESSION_CLAIMED, SESSION_PLAIN, ProjectHookCase, Session

spud = load_spud_module()

SESSION_OTHER = "d9e3f4a5-6b7c-4d8e-8f9a-1b2c3d4e5f6a"
CAP = 8000  # SessionStart's cap, which the claim context shares: 80% of the harness's measured inline limit (SPD-048)
SPUD_EXPANDED = "<command-message>spud</command-message>\n<command-name>/spud</command-name>\n<command-args>%s</command-args>"  # as a transcript records /spud


def numbered(text):
    return [line for line in text.split("\n") if line[:1].isdigit() and line[1:3] == ". "]


class PromptMatchTest(unittest.TestCase):
    def test_the_projects_own_keys_in_the_ledgers_spelling(self):
        cases = {
            "Work on BAD-006": ["BAD-006"],
            "Start on BAD-020 ticket": ["BAD-020"],
            "BAD-020 and BAD-006, then BAD-020 again": ["BAD-020", "BAD-006"],
            "(BAD-006)": ["BAD-006"],
            "`BAD-006`": ["BAD-006"],
            "fix/BAD-006-feed": ["BAD-006"],
            "done with BAD-006.": ["BAD-006"],
            "BAD-1000": ["BAD-1000"],
        }
        for prompt, keys in cases.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(spud.prompt_ticket_keys(prompt, "BAD"), keys)

    def test_old_tracker_numbers_and_look_alikes_never_match(self):
        for prompt in ("Rebase PRs 327 and 328 (BAD-23)", "BAD-36", "BAD-06", "BAD-6", "BAD-0060", "BAD-0006", "BAD-01000", "XBAD-006", "_BAD-006",
                       "BAD-006x", "bad-006", "Bad-006", "BAD_006", "BAD 006", "BAD-", "BADS-006", "SPD-006", "BAD-" + "9" * 40):
            with self.subTest(prompt=prompt):
                self.assertEqual(spud.prompt_ticket_keys(prompt, "BAD"), [])

    def test_the_spud_command_typed_or_expanded(self):
        for prompt in ("/spud", "/spud BAD-020 move the ticket as needed", "  /spud\nWork on BAD-006", SPUD_EXPANDED % "BAD-020 move the ticket as needed"):
            with self.subTest(prompt=prompt):
                self.assertTrue(spud.prompt_is_spud_command(prompt))
        for prompt in ("Work on BAD-006", "/spudnik BAD-006", "/review BAD-006", "run /spud later for BAD-006", "<command-name>/review</command-name> BAD-006"):
            with self.subTest(prompt=prompt):
                self.assertFalse(spud.prompt_is_spud_command(prompt))

    def test_the_skill_and_the_hooks_steps_have_one_source(self):
        # SPD-097: the steps read the home's CLAUDE.md and run the tool's launcher, two directories now
        ctx = spud.Ctx(Path("/Users/Someone/SpudHome"), "SPUD_HOME", False, tool=Path("/Users/Someone/Spud"))
        home, launcher = ctx.home, ctx.launcher
        skill = spud.skill_markdown(ctx)
        self.assertTrue(skill.startswith("---\nname: spud\n"), skill)
        self.assertIn("You are becoming Spud in this session.\n\n1. Read /Users/Someone/SpudHome/CLAUDE.md in full", skill)
        self.assertIn("/Users/Someone/Spud/bin/spud --as spud session claim", skill)
        hook = numbered(spud.skill_steps(home, spud.HOOK_CLAIM.format(launcher=launcher), "BAD-006 - <what this session does>"))
        steps = numbered(skill)
        self.assertEqual((len(steps), len(hook)), (4, 4))
        self.assertEqual((hook[0], hook[2]), (steps[0], steps[2]))
        self.assertEqual(hook[3].replace("BAD-006 - ", "<KEY> - "), steps[3])
        self.assertIn("--as spud session claim`", steps[1])
        self.assertIn("do not run `session claim`", hook[1])
        self.assertIn("set the session title to `<KEY> - <what this session does>`", steps[3])


class AutoClaimTest(ProjectHookCase):
    # -- payloads and reads ---------------------------------------------------------------
    def prompt_p(self, s, prompt, agent_id=None):
        p = self.place(s, common(str(s.cwd), agent_id, session=s.session))
        p.update({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
        return p

    def submit(self, s, prompt, agent_id=None):
        return self.hook_in(s, "UserPromptSubmit", self.prompt_p(s, prompt, agent_id))

    def claims(self, session, released=False):
        return self.home.rows("SELECT session_id, cwd FROM sessions WHERE session_id = ? AND released_at IS %s" % ("NOT NULL" if released else "NULL"), session)

    def claim_events(self, session):
        rows = self.home.rows("SELECT actor, ticket_id, body, data FROM events WHERE kind = 'session.claimed' ORDER BY id")
        return [dict(r, data=json.loads(r["data"])) for r in rows if json.loads(r["data"])["session_id"] == session]

    def ticket_id(self, key):
        return self.home.scalar("SELECT id FROM tickets WHERE key = ?", key)

    def new_bad(self, title, status="active", project=KEY):
        return self.cli_json("ticket", "new", "--project", project, "--title", title, "--status", status, actor="spud")["ticket"]

    def assertClaims(self, s, prompt, key, project=KEY):
        r = self.submit(s, prompt)
        self.assertEqual((r.code, r.stderr), (0, ""), r)
        self.assertEqual(r.json["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit", r)
        self.assertIn("`%s - <what this session does>`" % key, r.context)
        self.assertIn("Session %s is Spud in project %s" % (s.session, project), r.context)
        self.assertLessEqual(len(r.context.encode("utf-8")), CAP, r.context)
        self.assertEqual(len(self.claims(s.session)), 1, s)
        event = self.claim_events(s.session)[-1]
        self.assertEqual((event["actor"], event["ticket_id"], event["data"]["how"], event["data"]["ticket"], event["data"]["project"]),
                         ("hook:UserPromptSubmit", self.ticket_id(key), "hook", key, project))
        return r

    def assertNoClaim(self, s, prompt, agent_id=None):
        before = len(self.claim_events(s.session))
        r = self.submit(s, prompt, agent_id)
        self.assertHookSilent(r, (s.label, prompt))
        self.assertEqual(len(self.claim_events(s.session)), before, (s.label, prompt))
        return r

    def assertSpudNow(self, s, root):
        self.assertDenied(self.write(s, root / "src" / "a.txt"), LAW_1, s.label)
        self.assertHookSilent(self.write(s, self.home.path / "CLAUDE.md"), s.label)

    def assertPlainStill(self, s, root):
        self.assertHookSilent(self.write(s, root / "src" / "a.txt"), s.label)
        self.assertDenied(self.write(s, self.home.path / "CLAUDE.md"), NOT_SPUD, s.label)

    # -- claiming ---------------------------------------------------------------------------
    def test_a_plain_session_that_names_its_projects_ticket_becomes_spud(self):
        self.assertPlainStill(self.PLAIN, self.bad)
        r = self.assertClaims(self.PLAIN, "Work on BAD-001", "BAD-001")
        home = str(self.home.path)
        for needle in ("the prompt names BAD-001", "%s/CLAUDE.md in full, then %s/spud.config.json" % (home, home), "the project wins; about the second, Spud's laws win",
                       "Run the session ritual of CLAUDE.md from step 2", "do not run `session claim`", "--as spud session release",
                       "rule: this repository's CLAUDE.md", "board (badtakes):"):
            self.assertIn(needle, r.context)
        self.assertEqual(self.claims(SESSION_PLAIN), [{"session_id": SESSION_PLAIN, "cwd": str(self.bad)}])
        self.assertIn("by the UserPromptSubmit hook: the prompt names BAD-001", self.claim_events(SESSION_PLAIN)[0]["body"])
        self.assertSpudNow(self.PLAIN, self.bad)
        self.assertEqual(self.cli_json("session", "show", cwd=self.bad, session=SESSION_PLAIN)["mode"], "spud")
        self.assertNoClaim(self.PLAIN, "BAD-001 again")  # claimed now: the next prompt is Spud's and the hook is silent
        self.assertEqual(len(self.claim_events(SESSION_PLAIN)), 1)

    def test_a_session_in_a_worktree_of_the_project_claims(self):
        cwd_there = self.PLAIN._replace(label="plain, cwd in the worktree", cwd=self.bad_wt)
        self.assertClaims(cwd_there, "Start on BAD-001", "BAD-001")
        self.assertEqual(self.claims(SESSION_PLAIN)[0]["cwd"], str(self.bad_wt))
        launched_there = Session("plain, launched in the worktree", self.bad_wt, SESSION_OTHER, self.bad_wt, KEY)
        self.assertClaims(launched_there, "Start on BAD-001", "BAD-001")

    def test_a_done_ticket_claims_and_a_declined_one_does_not(self):
        declined = self.new_bad("Declined", status="queued")
        self.cli("ticket", "move", declined["key"], "--status", "declined", actor="spud")
        self.cli("ticket", "move", "BAD-001", "--status", "done", actor="spud")
        self.assertNoClaim(self.PLAIN, "Work on %s" % declined["key"])
        self.assertEqual(self.claims(SESSION_PLAIN), [])
        self.assertClaims(self.PLAIN, "%s is declined; reopen BAD-001" % declined["key"], "BAD-001")

    def test_old_tracker_numbers_look_alikes_and_missing_tickets_do_not_claim(self):
        for n in range(2, 7):
            self.new_bad("BadTakes ticket %d" % n)
        for prompt in ("Rebase PRs 327 and 328 (BAD-23, BAD-26 and BAD-36)", "BAD-0060", "BAD-06", "XBAD-006", "bad-006", "BAD-006x", "BADS-006",
                       "BAD-007", "Look at %s" % self.t["key"], "Fix the feed"):
            with self.subTest(prompt=prompt):
                self.assertNoClaim(self.PLAIN, prompt)
        self.assertEqual((self.claims(SESSION_PLAIN), self.claim_events(SESSION_PLAIN)), ([], []))
        self.assertClaims(self.PLAIN, "Work on BAD-006", "BAD-006")

    def test_another_projects_key_does_not_claim(self):
        other = self.make_repo("otherrepo-")
        self.register(other, "otherrepo", "OTH", "OTHS", "merge")
        self.new_bad("Other", project="otherrepo")
        self.assertNoClaim(self.PLAIN, "Work on OTH-001")
        in_other = Session("plain in otherrepo", other, SESSION_OTHER, other, "otherrepo")
        self.assertNoClaim(in_other, "Work on BAD-001")
        self.assertClaims(in_other, "Work on OTH-001", "OTH-001", project="otherrepo")
        self.assertEqual(self.claims(SESSION_PLAIN), [])

    # -- sessions that are Spud already, and callers that are not Eric -------------------------
    def test_the_home_an_always_project_outside_and_a_claimed_session_stay_silent(self):
        always = self.make_repo("alwaysrepo-")
        self.register(always, "alwaysrepo", "ALW", "ALWS", "merge", sessions="always")
        self.new_bad("Always", project="alwaysrepo")
        for s, prompt in ((self.HOME, "Work on BAD-001 and %s" % self.t["key"]),
                          (self.CLAIMED, "Work on BAD-001"),
                          (Session("always, unclaimed", always, SESSION_PLAIN, always, "alwaysrepo"), "Work on ALW-001"),
                          (Session("outside", self.outside, SESSION_OTHER, self.outside, None), "Work on BAD-001")):
            with self.subTest(session=s.label):
                self.assertNoClaim(s, prompt)
        self.assertEqual(len(self.claim_events(SESSION_CLAIMED)), 1)  # setUp's /spud claim alone
        self.assertEqual(self.home.scalar("SELECT count(*) FROM sessions WHERE session_id IN (?, ?, ?)", SESSION, SESSION_PLAIN, SESSION_OTHER), 0)

    def test_a_subagents_prompt_and_the_spud_command_do_not_claim(self):
        self.assertNoClaim(self.PLAIN, "Work on BAD-001", agent_id=AGENT_A)
        self.assertNoClaim(self.PLAIN, "/spud BAD-001 move the ticket as needed")
        self.assertNoClaim(self.PLAIN, SPUD_EXPANDED % "BAD-001 move the ticket as needed")
        self.assertEqual(self.claims(SESSION_PLAIN), [])
        self.claim(self.PLAIN)  # what the skill runs
        self.assertEqual([e["data"]["how"] for e in self.claim_events(SESSION_PLAIN)], ["command"])

    def test_a_release_sticks_for_its_session(self):
        self.release(self.CLAIMED)
        self.assertNoClaim(self.CLAIMED, "Work on BAD-001")
        self.assertEqual(len(self.claims(SESSION_CLAIMED, released=True)), 1)
        self.assertPlainStill(self.CLAIMED, self.bad)
        # a session the hook claimed and Eric released stays released too
        self.assertClaims(self.PLAIN, "Work on BAD-001", "BAD-001")
        self.release(self.PLAIN)
        self.assertNoClaim(self.PLAIN, "BAD-001, please")
        self.assertPlainStill(self.PLAIN, self.bad)
        # /spud still claims a released session
        self.claim(self.PLAIN)
        self.assertSpudNow(self.PLAIN, self.bad)

    def test_a_claim_released_without_session_release_can_claim_again(self):
        """`project uninstall` and `project remove` release every claim of a project without a session.released event; that
        is not Eric releasing the session, so a later prompt naming a ticket claims it again."""
        s = Session("claimed, then released by uninstall", self.bad, SESSION_OTHER, self.bad, KEY)
        self.claim(s)
        con = self.home.connect()
        try:
            with con:
                con.execute("UPDATE sessions SET released_at = claimed_at WHERE session_id = ?", (SESSION_OTHER,))
        finally:
            con.close()
        self.assertEqual(self.claims(SESSION_OTHER), [])
        self.assertClaims(s, "Work on BAD-001", "BAD-001")

    # -- silence, failure and size ----------------------------------------------------------------
    def test_a_prompt_that_names_no_ticket_and_malformed_input_stay_silent(self):
        for prompt in ("Fix the feed", "", "PR 327 and BAD-", "   "):
            with self.subTest(prompt=prompt):
                self.assertNoClaim(self.PLAIN, prompt)
        payload = self.prompt_p(self.PLAIN, "Work on BAD-001")
        del payload["prompt"]
        self.assertHookSilent(self.hook_in(self.PLAIN, "UserPromptSubmit", payload))
        for raw in ("", "not json", "[1]"):
            with self.subTest(raw=raw):
                r = self.hook_in(self.PLAIN, "UserPromptSubmit", raw)
                self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertEqual(self.claims(SESSION_PLAIN), [])

    def test_a_broken_ledger_fails_open_on_a_project_line(self):
        con = self.home.connect()
        con.execute("PRAGMA user_version = 99")
        con.close()
        r = self.submit(self.PLAIN, "Work on BAD-001")
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertTrue(self.home.spool.exists())

    def test_the_context_fits_the_inline_limit_with_a_long_board(self):
        for n in range(20):
            self.new_bad("BadTakes ticket %02d with a long title that makes the board brief grow well past eight kilobytes %s" % (n, "and on " * 45))
        r = self.assertClaims(self.PLAIN, "Work on BAD-001", "BAD-001")
        self.assertGreater(len(r.context.encode("utf-8")), CAP - 1000, len(r.context.encode("utf-8")))
        self.assertIn("4. Run the session ritual", r.context)
        # SPD-048: the note counts what it cut, exactly: the card's board is `board --brief` of the project, from the top
        board = self.cli("board", "--brief", "--project", KEY).stdout.rstrip("\n").split("\n")
        shown = r.context.split("\nboard (%s):\n" % KEY, 1)[1].split("\n")
        count = int(re.fullmatch(r"\((\d+) more lines? cut to fit; run `spud board --brief` for the rest\)", shown[-1]).group(1))
        self.assertEqual(shown[:-1], board[:len(shown) - 1])
        self.assertEqual(count, len(board) - (len(shown) - 1))


if __name__ == "__main__":
    unittest.main()
