"""Every SessionStart context fits the harness's inline limit, and says when the vault is not to be trusted (SPD-048).

The harness keeps a hook's additionalContext inline up to 10,000 UTF-16 code units; past that the model gets a preview of
about 2 KB and a file path, with no sign of what was left out (code.claude.com/docs/en/hooks, measured on Claude Code
2.1.274 with tests/probes/context_limit.py).  Before SPD-048 the home's context had no cap at all.  Now every SessionStart
context, the home's, outside's and a claimed project's, is cut on whole lines from the top to 8,000 bytes of UTF-8 with a
note that counts the lines it cut, and the render watcher's lines, which `spud board --brief` has carried since SPD-097,
sit in the head above the board, where no cut reaches them.  Since SPD-117 there are two: the watcher down, and the vault
behind the ledger, which is the one a session cannot otherwise detect.  The unit tests load the program; the rest run the
hook as its installed line does.
"""

import os
import re
import unittest

from helpers import aged_event, hold_watch_lock, install_watcher_plist, load_spud_module
from hookcase import HookCase
from test_hooks_projects import KEY, ProjectHookCase

spud = load_spud_module()

CAP = 8000
LIMIT_UTF16_UNITS = 10000  # measured on SPD-048: 10,000 inline, 10,001 persisted
WATCHER_DOWN = "render watcher: installed but not running; the vault is stale (spud --as spud schedule install reloads it)"
WATCHER_BEHIND = "render watcher: %s unrendered, the oldest %s; the vault is stale (spud render brings it up to date)"
NOTE = re.compile(r"\((\d+) more lines? cut to fit; run `spud board --brief` for the rest\)")
HEADER = re.compile(r"Ledger board \(`spud board --brief` at [^,]+, source startup\):")
FILLER = "and so on " * 25  # a board line of about 300 bytes: 27 of them pass the cap


def expected_fit(head, body, cap):
    """fit_bytes written the slow way: try every number of kept lines and take the largest that fits with its note."""
    text = head + ("\n" + body if body else "")
    if len(text.encode("utf-8")) <= cap:
        return text
    lines = body.split("\n") if body else []
    best = None
    for kept in range(len(lines)):
        candidate = head + "".join("\n" + line for line in lines[:kept]) + "\n" + spud.cut_note(len(lines) - kept)
        if len(candidate.encode("utf-8")) <= cap:
            best = candidate
    return best if best is not None else head.encode("utf-8")[:cap].decode("utf-8", "ignore")


class CapTest(unittest.TestCase):
    def test_one_cap_for_every_session_start_context_at_most_80_percent_of_the_measured_limit(self):
        self.assertEqual(spud.SESSION_CONTEXT_CAP, CAP)
        self.assertLessEqual(spud.SESSION_CONTEXT_CAP, LIMIT_UTF16_UNITS * 8 // 10)
        self.assertEqual(spud.PROMPT_CLAIM_CAP, spud.SESSION_CONTEXT_CAP)
        self.assertEqual(spud.CLAIM_CARD_CAP, 1536)  # a Bash result, not hook context: untouched

    def test_a_byte_cap_holds_whatever_the_harness_counts(self):
        # The harness counts UTF-16 code units; UTF-8 never has fewer bytes than that, one to four characters wide.
        for text in ("x" * 100, "é" * 100, "じゃが" * 40, "\U0001F954" * 50, "aéじ\U0001F954" * 30):
            self.assertGreaterEqual(len(text.encode("utf-8")), len(text.encode("utf-16-le")) // 2, text[:4])


class FitBytesTest(unittest.TestCase):
    def test_text_that_fits_comes_back_whole(self):
        self.assertEqual(spud.fit_bytes("head", "a\nb", 100), "head\na\nb")
        self.assertEqual(spud.fit_bytes("head", "a\nb", len("head\na\nb")), "head\na\nb")
        self.assertEqual(spud.fit_bytes("head", "", 4), "head")

    def test_the_note_counts_the_cut_lines(self):
        self.assertEqual(spud.cut_note(1), "(1 more line cut to fit; run `spud board --brief` for the rest)")
        self.assertEqual(spud.cut_note(12), "(12 more lines cut to fit; run `spud board --brief` for the rest)")
        body = "\n".join("line %02d %s" % (n, "x" * 40) for n in range(12))
        text = spud.fit_bytes("head", body, 400)
        shown = text.split("\n")
        self.assertEqual(shown[0], "head")
        self.assertEqual(shown[1:-1], body.split("\n")[:len(shown) - 2])
        self.assertEqual(int(NOTE.fullmatch(shown[-1]).group(1)), 12 - (len(shown) - 2))
        self.assertLessEqual(len(text.encode("utf-8")), 400)

    def test_every_cap_keeps_the_most_whole_lines_that_fit_with_an_exact_count(self):
        # Counts that cross 1 -> 2 (line, lines) and 9 -> 10 (a digit), empty lines, and characters of one to four bytes.
        bodies = [
            "\n".join("x" * (n % 7) for n in range(14)),
            "\n".join(("éじ\U0001F954" * (n % 4)) + "t%d" % n for n in range(12)),
            "one line only, and a long one at that",
            "\n\n\n",
        ]
        for body in bodies:
            for head in ("h", "Ledger board (じ\U0001F954):"):
                whole = len((head + "\n" + body).encode("utf-8"))
                for cap in range(0, whole + 2):
                    with self.subTest(body=body[:12], head=head, cap=cap):
                        got = spud.fit_bytes(head, body, cap)
                        self.assertEqual(got, expected_fit(head, body, cap))
                        self.assertLessEqual(len(got.encode("utf-8")), max(cap, 0))

    def test_a_head_past_the_cap_is_cut_on_a_character_boundary(self):
        head = "\U0001F954" * 10  # 40 bytes
        for cap in range(0, 44):
            with self.subTest(cap=cap):
                got = spud.fit_bytes(head, "a\nb", cap)
                self.assertTrue(head.startswith(got), got)
                self.assertLessEqual(len(got.encode("utf-8")), cap)
                self.assertGreater(len(got.encode("utf-8")), cap - 4)


class ContextCase(HookCase):
    in_process = True  # SPD-233: the hooks and the CLI in this process, one home per class (hookcase.ClassHome)

    def board_lines(self, *extra):
        """`spud board --brief` as the CLI prints it, one entry per line, without the render watcher's own lines."""
        lines = self.home.run("board", "--brief", *extra).stdout.rstrip("\n").split("\n")
        return [line for line in lines if not line.startswith("render watcher: ")]

    def install_watcher_plist(self):
        """The watcher's plist where this home's SPUD_LAUNCH_AGENTS_DIR looks: installed, and no watcher holds the lock."""
        install_watcher_plist(self.home)

    def hold_watch_lock(self):
        """What a live watcher does: hold the home's watch lock for as long as the test runs."""
        self.addCleanup(os.close, hold_watch_lock(self.home))

    def assertCut(self, context, head_lines, board):
        """context is head_lines, then the board from the top on whole lines, then a note counting exactly the lines left out,
        within the cap, and one more line would not have fit."""
        size = len(context.encode("utf-8"))
        self.assertLessEqual(size, CAP)
        shown = context.split("\n")
        self.assertEqual(shown[:len(head_lines)], head_lines)
        kept = shown[len(head_lines):-1]
        count = int(NOTE.fullmatch(shown[-1]).group(1))
        self.assertEqual(kept, board[:len(kept)])
        self.assertEqual(count, len(board) - len(kept))
        self.assertGreaterEqual(count, 1)
        one_more = "\n".join(shown[:-1] + [board[len(kept)], spud.cut_note(count - 1)]) if count > 1 else "\n".join(shown[:-1] + board[len(kept):])
        self.assertGreater(len(one_more.encode("utf-8")), CAP)
        return kept, count


class HomeSessionStartTest(ContextCase):
    def test_a_board_under_the_cap_is_injected_as_before_byte_for_byte(self):
        self.plan(name="Kestrel")
        self.new_ticket("Queued été \U0001F954")
        r = self.home.hook("SessionStart", self.session_start())
        self.assertEqual(r.code, 0, r)
        board = self.home.run("board", "--brief").stdout.rstrip("\n")
        at = re.fullmatch(r"Ledger board \(`spud board --brief` at ([^,]+), source startup\):\n.*", r.context, re.S).group(1)
        self.assertEqual(r.context, "Ledger board (`spud board --brief` at %s, source %s):\n%s" % (at, "startup", board))  # SPD-011's text

    def test_a_board_over_the_cap_is_cut_on_whole_lines_and_the_due_back_line_survives(self):
        due = self.new_ticket("Waiting on Eric")
        self.home.json("ticket", "move", due["key"], "--status", "parked", "--reason", "App Store approval", "--until", "2020-01-01", actor="spud")
        for n in range(30):
            self.new_ticket("Queued %02d éじ\U0001F954 %s" % (n, FILLER))
        board = self.board_lines()
        self.assertGreater(len("\n".join(board).encode("utf-8")), CAP)
        r = self.home.hook("SessionStart", self.session_start())
        self.assertEqual(r.code, 0, r)
        self.assertTrue(HEADER.fullmatch(r.context.split("\n")[0]), r.context[:200])
        kept, count = self.assertCut(r.context, r.context.split("\n")[:1], board)
        self.assertIn("SPD-002 parked P2 Waiting on Eric (due back 2020-01-01: App Store approval)", kept)
        self.assertNotIn("1 parked, 1 due back (spud board --parked)", kept)  # the count line is last, and a cut takes it first
        self.assertGreater(count, 2)

    def test_the_watcher_line_sits_above_the_board_and_survives_a_cut(self):
        self.install_watcher_plist()
        r = self.home.hook("SessionStart", self.session_start())
        shown = r.context.split("\n")
        self.assertEqual(shown[0], WATCHER_DOWN)
        self.assertTrue(HEADER.fullmatch(shown[1]), shown[1])
        self.assertEqual(shown[2:], self.board_lines())
        self.assertEqual(self.home.run("board", "--brief").stdout.rstrip("\n").split("\n")[-1], WATCHER_DOWN)  # one line, one check
        for n in range(30):
            self.new_ticket("Queued %02d %s" % (n, FILLER))
        r = self.home.hook("SessionStart", self.session_start())
        self.assertEqual(r.context.split("\n")[0], WATCHER_DOWN)
        self.assertCut(r.context, r.context.split("\n")[:2], self.board_lines())
        self.hold_watch_lock()  # a watcher is alive: no line, in the hook or in the board
        r = self.home.hook("SessionStart", self.session_start())
        self.assertNotIn("render watcher", r.context)
        self.assertTrue(HEADER.fullmatch(r.context.split("\n")[0]), r.context[:200])
        self.assertNotIn("render watcher", self.home.run("board", "--brief").stdout)

    def test_no_watcher_installed_says_nothing(self):
        r = self.home.hook("SessionStart", self.session_start())
        self.assertNotIn("render watcher", r.context)

    def test_a_vault_behind_the_ledger_puts_its_own_line_above_the_board(self):
        """SPD-117: the state a session cannot otherwise detect.  A watcher holds its lock and renders nothing, so nothing
        about it is down; the line says how far behind the vault is and the one command that brings it up to date."""
        self.install_watcher_plist()
        self.hold_watch_lock()
        self.new_ticket("Queued")
        self.home.run("render")  # the vault has caught up, and then one event goes unrendered for 25 minutes
        aged_event(self.home, 25 * 60)
        r = self.home.hook("SessionStart", self.session_start())
        shown = r.context.split("\n")
        self.assertEqual(shown[0], WATCHER_BEHIND % ("1 event", "25m ago"))
        self.assertTrue(HEADER.fullmatch(shown[1]), shown[1])
        self.assertEqual(shown[2:], self.board_lines())
        self.assertEqual(self.home.run("board", "--brief").stdout.rstrip("\n").split("\n")[-1], WATCHER_BEHIND % ("1 event", "25m ago"))
        for n in range(30):  # and a cut never reaches it: it is in the head
            self.new_ticket("Queued %02d %s" % (n, FILLER))
        r = self.home.hook("SessionStart", self.session_start())
        self.assertTrue(r.context.split("\n")[0].endswith("(spud render brings it up to date)"), r.context[:200])
        self.assertCut(r.context, r.context.split("\n")[:2], self.board_lines())

    def test_a_watcher_down_and_a_vault_behind_are_two_lines_in_the_head(self):
        self.install_watcher_plist()
        self.new_ticket("Queued")
        self.home.run("render")
        aged_event(self.home, 3 * 3600)
        r = self.home.hook("SessionStart", self.session_start())
        shown = r.context.split("\n")
        self.assertEqual(shown[:2], [WATCHER_DOWN, WATCHER_BEHIND % ("1 event", "3h ago")])
        self.assertTrue(HEADER.fullmatch(shown[2]), shown[2])
        self.assertEqual(shown[3:], self.board_lines())
        for n in range(30):
            self.new_ticket("Queued %02d %s" % (n, FILLER))
        r = self.home.hook("SessionStart", self.session_start())
        shown = r.context.split("\n")
        self.assertEqual(shown[0], WATCHER_DOWN)
        self.assertTrue(shown[1].startswith("render watcher: "), shown[1])
        self.assertCut(r.context, shown[:3], self.board_lines())


class ProjectSessionStartTest(ContextCase, ProjectHookCase):
    def test_a_claimed_sessions_context_carries_the_watcher_line_under_its_header_and_counts_its_cut(self):
        self.install_watcher_plist()
        r = self.hook_in(self.CLAIMED, "SessionStart", self.session_start_p(self.CLAIMED, "resume"))
        shown = r.context.split("\n")
        self.assertTrue(shown[0].startswith("Ledger: this session is Spud in project `%s`" % KEY), shown[0])
        self.assertEqual(shown[1], WATCHER_DOWN)
        self.assertRegex(shown[2], r"\ALedger board \(`spud board --brief` at [^,]+, source resume\):\Z")
        self.assertEqual(shown[3:], self.board_lines())
        for n in range(30):
            self.cli("ticket", "new", "--project", KEY, "--title", "BadTakes %02d %s" % (n, FILLER), actor="spud")
        r = self.hook_in(self.CLAIMED, "SessionStart", self.session_start_p(self.CLAIMED, "resume"))
        shown = r.context.split("\n")
        self.assertEqual(shown[1], WATCHER_DOWN)
        self.assertCut(r.context, shown[:2], [shown[2]] + self.board_lines())  # the board's header is the body's first line

    def test_a_claimed_sessions_context_carries_the_behind_line_under_its_header(self):
        self.install_watcher_plist()
        self.hold_watch_lock()  # a watcher alive and rendering nothing: the project's session hears about the vault, not the process
        aged_event(self.home, 45 * 60)
        r = self.hook_in(self.CLAIMED, "SessionStart", self.session_start_p(self.CLAIMED, "resume"))
        shown = r.context.split("\n")
        self.assertTrue(shown[0].startswith("Ledger: this session is Spud in project `%s`" % KEY), shown[0])
        self.assertTrue(shown[1].startswith("render watcher: ") and shown[1].endswith("(spud render brings it up to date)"), shown[1])
        self.assertIn("the oldest 45m ago", shown[1])
        self.assertRegex(shown[2], r"\ALedger board \(`spud board --brief` at [^,]+, source resume\):\Z")
        self.assertEqual(shown[3:], self.board_lines())

    def test_a_plain_session_gets_its_notice_and_no_watcher_line(self):
        self.install_watcher_plist()
        r = self.hook_in(self.PLAIN, "SessionStart", self.session_start_p(self.PLAIN))
        self.assertNotIn("render watcher", r.context)
        self.assertLessEqual(len(r.context.encode("utf-8")), 300)


if __name__ == "__main__":
    unittest.main()
