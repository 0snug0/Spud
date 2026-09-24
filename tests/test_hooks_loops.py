"""PreToolUse(Bash): zsh's short loop and conditional forms and the command positions around them -- a repeat, for,
select or foreach body with no `do`, if and elif with braces, a `}` or `))` in command position, reserved words glued to a
word."""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from helpers import load_spud_module, wall_clock
from hookcase import AGENT_A, AGENT_B, AGENT_C, BashHookCase


# zsh's short loop forms (SPD-042): a header, and the body that may follow it with no `do`.  Each fills its `%s`.
SHORT_HEADERS = ("repeat 2 %s", "repeat 2; %s", "repeat 2\n%s", "repeat 2 ;\n; %s", "n=2; repeat $n %s", "repeat $(echo 2) %s",
                 "repeat 1+1 %s", "for f (a b) %s", "for f (a b); %s", "for f (a b)\n%s", "for f (*.py) %s", "for f () %s",
                 "for f in a b; %s", "for f in a b\n%s", "for f; %s", "select f (a b) %s", "select f in a b; %s",
                 "for (( i=0; i<2; i++ )) %s", "for (( i=0; i<2; i++ )); %s")
SHORT_BODIES = ("%s", "{ %s }", "{ %s; }", "( %s )", "do %s; done", "if true; then %s; fi", "case x in x) %s;; esac",
                "true && %s", "false || %s", "%s | cat")


class ZshShortLoopTest(BashHookCase):
    """SPD-042: zsh's SHORT_LOOPS, on under its default options, run a loop body with no `do` and `done`, and the hook read
    that body as header words and discarded it, so a git write, a spud call, a tee, a cd or a redirection there was never
    checked (Laws 1, 5, 6 and 7).  Probed in zsh 5.9 -f and in zsh -f -o nobareglobqual (this Mac's Bash tool), with bash 3.2
    as the control that rejects every one of these forms, in a scratchpad directory with a fake git first on a scratch PATH:

    - the header is `repeat word` (one word, arithmetic: `repeat 1+1` ran twice), `for name ( word ... )`,
      `for name in word ... TERM`, `for name TERM` (the positional parameters), `select` in both spellings, or `for (( ... ))`;
      TERM is `;` or a newline, and more than one may stand between a header and its body (`repeat 2 ;\\n;\\n git push` pushed
      twice).  `for f in a b do ...` is a parse error, so the `in` form always has a terminator before its body;
    - the body is a `do ... done`, a `{ list }`, a `( list )` subshell, or one sublist: a whole and-or list with its pipelines
      (`repeat 3 true && git push` pushed three times; `repeat 2 echo a | wc -l` printed 1 twice, so the pipeline is inside),
      a compound command (`repeat 2 if true; then git push; fi` pushed twice) and the redirections of each, ending at `;`, a
      newline or `&` (`repeat 2 echo a >> f1; echo b >> f2` left two lines in f1 and one in f2; `repeat 2 git push & wait`
      pushed twice, so `&` ends the body and backgrounds the loop);
    - the loop runs in the shell itself, so a cd in a sublist or a `{ }` body moves it and may repeat (`repeat 1 cd d; pwd`
      ended in d), while a `( )` body's does not (`repeat 1 (cd d); pwd` did not), and a cd after the sublist is outside the
      loop (`repeat 1 true; cd d; pwd` ended in d);
    - `for f (a|b)` is a parse error, so a loop's word list is never read as a glob, and a command follows it and a repeat
      count in command position (`for f (a b) (git push)` and `repeat 1 (git push)` open subshells).

    AGENT_A plans tests/** and bin/spud; AGENT_C plans **; note.txt is refused to AGENT_A at the home."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/zzone", "bin"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")

    # -- payloads the body may hold -----------------------------------------------------
    def member_payloads(self):
        """(command, the reason a member is refused for it): Law 7, Law 6, Law 5's --as, the database, and Law 1 through a
        redirection and through tee."""
        home, spud = self.home.path, self.spud_cli
        return (("git push", "Law 7"),
                ("%s ticket new --title x" % spud, "Law 6"),
                ("%s --as spud member log hi" % spud, "Law 6"),
                ("%s --as %s member log hi" % (spud, AGENT_B), "--as"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "generated"),
                ("echo x | tee ledger/tickets/SPD-001.md", "generated"))

    def spud_payloads(self):
        """Spud is never refused for git; these are the checks that do apply to him."""
        home, spud = self.home.path, self.spud_cli
        return (("%s --as %s member log hi" % (spud, AGENT_A), "--as"),
                ("%s hook PreToolUse" % spud, "hook"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "Law 1"),
                ("echo x | tee ledger/tickets/SPD-001.md", "Law 1"))

    def refused_everywhere(self, line, member_needle, spud_needle):
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(line=line, agent_id=agent_id):
                self.assertRefused(line, member_needle, agent_id)
        with self.subTest(line=line, agent_id="spud"):
            self.assertRefused(line, spud_needle, agent_id=None)

    # -- the hole ------------------------------------------------------------------------
    def test_the_tickets_evidence_commands_are_refused(self):
        """The three lines from the ticket: each wrote ledger/tickets/SPD-001.md in zsh and the hook recorded no redirect."""
        for line in ("repeat 3 echo x > ledger/tickets/SPD-001.md",
                     "repeat 1 (echo x > ledger/tickets/SPD-001.md)",
                     "for f (a b) echo x > ledger/tickets/SPD-001.md"):
            self.refused_everywhere(line, "generated", "Law 1")

    def test_every_header_form_reaches_its_body(self):
        for header in SHORT_HEADERS:
            with self.subTest(header=header):
                self.assertRefused(header % "git push", "Law 7")
                self.assertRefused(header % "git push", "Law 7", AGENT_C)

    def test_every_body_form_reaches_its_commands(self):
        for header in ("repeat 2 %s", "for f (a b) %s", "for f in a b; %s", "select f (a b) %s", "for (( i=0; i<2; i++ )) %s"):
            for body in SHORT_BODIES:
                with self.subTest(header=header, body=body):
                    self.assertRefused(header % (body % "git push"), "Law 7")

    def test_every_payload_in_every_body_for_every_caller(self):
        for header in ("repeat 2 %s", "for f (a b) %s", "for f in a b; %s"):
            for body in ("%s", "{ %s }", "( %s )", "do %s; done"):
                self.assertPayloadsAnswered(header % body, self.member_payloads(), self.spud_payloads())

    def test_a_target_outside_a_narrow_members_deliverables(self):
        """AGENT_A plans tests/** and bin/spud, so note.txt at the home is refused it and allowed the ** member."""
        for header in ("repeat 2 %s", "for f (a b) %s", "for f in a b; %s", "for (( i=0; i<2; i++ )) %s"):
            for body in ("%s", "{ %s }", "( %s )"):
                line = header % (body % "echo x > note.txt")
                with self.subTest(line=line):
                    self.assertRefused(line, "deliverables")
                    self.assertSilent(line, AGENT_C)

    # -- the loop model -------------------------------------------------------------------
    def test_the_loop_model_matches_the_long_form(self):
        """A relative cd in a short body may repeat, so it is unfollowable; an absolute one leaves the union of before and
        after, as pop computes for `do ... done`; a `( )` body's cd does not carry out; a cd after the sublist is outside the
        loop and followed."""
        out, home = self.out, self.home.path
        for header in ("repeat 2 %s", "for f (a b) %s", "for f in a b; %s", "select f (a b) %s", "for (( i=0; i<2; i++ )) %s"):
            for body in ("%s", "{ %s; }", "do %s; done"):
                with self.subTest(header=header, body=body):
                    # a relative cd that may repeat: the hook cannot follow it, so a member's relative target is refused
                    self.assertRefused((header % (body % "cd docs")) + "; echo x > note.txt", "cannot follow")
                    # an absolute cd: both the old directory and the new one are checked
                    self.assertRefused((header % (body % ("cd %s" % out))) + "; echo x > note.txt", "deliverables")
                    self.assertRefused((header % (body % ("cd %s/ledger" % home))) + "; echo x > tickets/SPD-001.md", "generated", AGENT_C)
        for header in ("repeat 2 %s", "for f (a b) %s"):
            with self.subTest("a subshell body does not move the shell: " + header):
                self.assertSilent((header % ("(cd %s)" % out)) + "; echo x > tests/zzone/k.py")
            with self.subTest("after the sublist, the loop is over: " + header):
                self.assertSilent((header % "true") + "; cd %s; echo x > note.txt" % out)
                self.assertRefused((header % "true") + "; cd %s/ledger; echo x > tickets/SPD-001.md" % home, "generated", AGENT_C)

    def test_the_loop_variable_is_doubted(self):
        """`for name (...)` and `for name in ...` assign the name each turn, so a command word built from it is refused."""
        for line in ("for X (git) $X push", "for X in git; $X push", "select X (git) $X push",
                     "X=ls; for X (git) $X push", "for X (git) { $X push }"):
            with self.subTest(line=line):
                self.assertRefused(line, "cannot resolve")
                self.assertRefused(line, "cannot resolve", AGENT_C)

    # -- where a short loop may stand ------------------------------------------------------
    def test_short_loops_nested_and_enclosed(self):
        for line in ("for f in a b; do repeat 1 git push; done",
                     "repeat 2 for f in a; do git push; done",
                     "repeat 2 repeat 2 git push",
                     "for f (a b) for g (c) git push",
                     "repeat 2 { repeat 1 git push }",
                     "{ repeat 1 git push; }",
                     "( repeat 1 git push )",
                     "f() { repeat 1 git push; }; f",
                     "eval 'repeat 1 git push'",
                     "sh -c 'repeat 1 git push'",
                     "zsh -c 'for f (a) git push'",
                     "echo $(repeat 1 git push)",
                     "echo `for f (a) git push`",
                     "coproc repeat 1 git push",
                     "coproc for f (a) git push",
                     "time repeat 1 git push",
                     "! repeat 1 git push",
                     "for f (a b) (git push)",
                     "for f () git push",
                     "repeat 1 true; repeat 1 git push",
                     "repeat 1 git push & wait",
                     "repeat 1 git push | cat",
                     "repeat 1 true && git push",
                     "repeat 1 false || git push",
                     "for f (a b) git push > out 2>&1",
                     "case x in x) repeat 1 git push;; esac",
                     "case x in x) repeat 1 git push;; y) git status;; esac",
                     "if true; then repeat 1 git push; else echo no; fi",
                     "repeat 2 cat <<< 'x'; git push"):
            with self.subTest(line=line):
                self.assertRefused(line, "Law 7")
                self.assertRefused(line, "Law 7", AGENT_C)
        # a short loop closed above a case frame must not swallow the arm's `;;`
        ledger, out = self.home.path / "ledger", self.out
        self.assertRefused("case x in a) repeat 1 cd %s;; b) cd %s;; esac; echo x > tickets/SPD-001.md" % (ledger, out),
                           "generated", AGENT_C)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        """Deep nesting and very long headers stay bounded and still find the write."""
        m = load_spud_module()
        for line in ("repeat 1 " * 2000 + "git push", "repeat 1 { " * 500 + "git push" + " }" * 500,
                     "repeat 1 ( " * 200 + "git push" + " )" * 200, "for f (a) " * 2000 + "git push",
                     "repeat 1 { " * 1000 + "git push", "for f (" + "a " * 5000 + ") git push", "repeat " * 500):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = m.analyse_command(line, m.ShellAnalysis(cwd=str(self.home.path)))
                self.assertLess(time.monotonic() - started, 5.0)
                if "git push" in line:
                    self.assertIn(("git", ("push", "push")), a.findings, line[:40])

    def test_the_sublist_body_ends_where_zsh_ends_it(self):
        """`repeat 2 echo a; echo b` ran echo a twice and echo b once, so what follows the sublist is outside the loop."""
        home = self.home.path
        m = load_spud_module()
        for line, inside, outside in (("repeat 2 echo a > ledger/tickets/SPD-001.md; echo b > tests/zzone/k.py", 1, 1),
                                      ("for f (a b) echo a > ledger/tickets/SPD-001.md; echo b > tests/zzone/k.py", 1, 1)):
            a = m.analyse_command(line, m.ShellAnalysis(cwd=str(home)))
            self.assertEqual(len(a.redirects), inside + outside, (line, a.redirects))
        # both simple commands are checked, and the one outside the loop for its own directory
        self.assertRefused("repeat 2 true; echo x > ledger/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertSilent("repeat 2 true; echo x > tests/zzone/k.py")

    # -- controls ---------------------------------------------------------------------------
    def test_the_long_forms_and_ordinary_words_keep_todays_reading(self):
        home, out = self.home.path, self.out
        for refused in ("for f in a b; do git push; done", "for f in a b\ndo\n  git push\ndone",
                        "for (( i=0; i<3; i++ )); do git push; done", "select f in a b; do git push; done",
                        "while true; do git push; done", "until false; do git push; done", "repeat 3; do git push; done",
                        "foreach f (a b); git push; end", "case x in x) git push;; esac",
                        "f() { git push; }; f", "function f { git push; }",
                        "(git push)", "{ git push; }", "n=0; while (( n++ < 2 )) { git push }"):
            with self.subTest(refused):
                self.assertRefused(refused, "Law 7")
        for ok in ("echo repeat 3 git push", "echo 'repeat 3 git push'", "echo \"for f (a b) git push\"",
                   "git log --grep 'for f (a b) git push'", "grep -n repeat tests/zzone/k.py",
                   "grep -c 'repeat 2 git push' tests/zzone/k.py", "arr=(a b); echo $arr", "arr=(); echo done",
                   "for f in tests/*.py; do echo $f; done", "echo select repeat for", "ls *(.)",
                   "printf 'repeat 1 git push\\n' > tests/zzone/k.py"):
            with self.subTest(ok):
                self.assertSilent(ok)
        # the directory model of the long forms is untouched
        self.assertRefused("for d in a b; do cd ..; done; echo x > note.txt", "cannot follow")
        self.assertSilent("cd %s; echo x > note.txt" % out)
        self.assertSilent("for f in a b; do echo $f; done; cd %s; echo x > note.txt" % out)
        self.assertRefused("for f in a b; do cd %s/ledger; done; echo x > tickets/SPD-001.md" % home, "generated", AGENT_C)

    def test_a_short_loop_is_not_read_into_a_quoted_or_argument_position(self):
        """`repeat`, `for` and `select` outside command position are ordinary words, and a `(` after a loop name is its word
        list, never a glob: neither changes what the hook reads elsewhere."""
        home = self.home.path
        for ok in ("echo for f (a b) done", "echo x | grep -F 'for f (a b) git push'",
                   "%s --as %s member log 'repeat 3 git push'" % (self.spud_cli, AGENT_A),
                   "%s --as %s member log 'for f (a b) git push'" % (self.spud_cli, AGENT_A)):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
        self.assertRefused("echo x > 'ledger/tickets/SPD-001.md'", "generated", AGENT_C)
        self.assertSilent("echo x > %s/tests/zzone/k.py" % home)


# zsh's short conditionals (SPD-061): an if, while or until whose condition ends in `[[ ... ]]`, and the body that may
# follow it with no `then` or `do`.  Each fills its `%s`.
SHORT_CONDITIONS = ("if [[ -n x ]] %s", "n=0; while [[ $((n++)) -lt 2 ]] %s", "n=0; until [[ $((n++)) -ge 2 ]] %s",
                    "if [[ -n x ]] && [[ -n y ]] %s", "if true && [[ -n x ]] %s", "if [[ -n x ]] || [[ -z y ]] %s")
SHORT_CONDITION_BODIES = ("%s", "{ %s }", "{ %s; }", "( %s )", "true && %s", "false || %s", "%s | cat",
                          "if true; then %s; fi", "case x in x) %s;; esac")


class ZshShortConditionalTest(BashHookCase):
    """SPD-061 (Caribe's SPD-042 proposal): zsh's SHORT_LOOPS give `if`, `while` and `until` a body with no `then` or `do`
    too, and with a `[[ ... ]]` condition the hook read the whole line as one simple command whose command word was `[[`, so
    the body's git verb or spud call was never checked (Laws 5, 6 and 7).  SPD-042 fixed `for`, `select` and `repeat` and
    named these out of scope, believing they were read; they were not.  Probed in zsh 5.9 -f and zsh -f -o nobareglobqual
    (this Mac's Bash tool), with bash 3.2 and sh as the controls that reject every one of these forms, in a scratchpad with a
    fake git first on a scratch PATH:

    - the condition is a list that ends in `[[ ... ]]`: `if [[ -n x ]] git push` pushed once, `n=0; while [[ $((n++)) -lt 2
      ]] git push` and `until [[ $((n++)) -ge 2 ]] git push` twice, and only the last `]]` ends it (`if [[ -n x ]] && [[ -n y
      ]] echo both`, `if true && [[ -n x ]] echo both` and the `||` form all ran their body, while `if [[ -n x ]] | cat` is a
      parse error);
    - the body is one sublist, a `{ list }`, a `( list )` or, with no terminator between, `then ... fi` or `do ... done`
      (`if [[ -n x ]] then echo t; fi` and `while [[ $((n++)) -lt 2 ]] do echo t; done` both ran).  The sublist ends where a
      short loop's does: `if [[ -n x ]] echo a; echo b` printed a once and b once, and `echo a > f1; echo b > f2` wrote one
      line each;
    - the body runs in the shell itself, so a cd in a sublist or a `{ }` body moves it and a `( )` body's does not (`cd /;
      if [[ -n x ]] cd /tmp; pwd` printed /tmp, `if [[ -z x ]] cd /tmp; pwd` printed /), which is SPD-030's model: either
      directory follows the conditional;
    - the `{ }` body wants a terminator after it, so `if [[ -n x ]] { git push }` alone is a zsh parse error while `if c { a
      }; echo after`, `if c { a } fi` and `if c { a } else { b }` all run (an `elif` needs a trailing `else`); `( )` takes no
      else.  The hook reads the brace body wherever it stands, fail closed and at no real cost: the line is a shell error
      where it is not one of the forms that run.  It also closes a `{ list }` at a `}` that follows a word with no terminator
      before it, as zsh does and bash does not, so both branches of `if c { a } else { b }` are read;
    - the single-bracket spelling runs nothing and is left alone: `if [ -n x ] git push` is a parse error, `while [ ... ]
      body` never iterates, and `until [ -n x ] body` spins with an empty body.  `if true git push` and `if [[ -n x ]]; git
      push` are parse errors too, and `if (( 1 )) git push` and `while (( n++ < 2 )) git push` were read before this ticket,
      because `(( ))` opens a subshell frame and closes it, leaving what follows in the condition list (still so since
      SPD-088 marked the arithmetic between the parentheses: one frame there now, where it used to be two).

    AGENT_A plans tests/** and bin/spud; AGENT_C plans **; note.txt is refused to AGENT_A at the home."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/zzone", "bin"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")

    def member_payloads(self):
        """(command, the reason a member is refused for it): Law 7, Law 6, Law 5's --as, the database, and Law 1 through a
        redirection and through tee."""
        home, spud = self.home.path, self.spud_cli
        return (("git push", "Law 7"),
                ("%s ticket new --title x" % spud, "Law 6"),
                ("%s --as spud member log hi" % spud, "Law 6"),
                ("%s --as %s member log hi" % (spud, AGENT_B), "--as"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "generated"),
                ("echo x | tee ledger/tickets/SPD-001.md", "generated"))

    def spud_payloads(self):
        """Spud is never refused for git; these are the checks that do apply to him."""
        home, spud = self.home.path, self.spud_cli
        return (("%s --as %s member log hi" % (spud, AGENT_A), "--as"),
                ("%s hook PreToolUse" % spud, "hook"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "Law 1"),
                ("echo x | tee ledger/tickets/SPD-001.md", "Law 1"))

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    # -- the hole ------------------------------------------------------------------------
    def test_the_tickets_evidence_commands_are_refused(self):
        """Caribe's four lines: each ran git push in zsh while the hook recorded no finding."""
        for line in ("if [[ -n x ]] git push",
                     "n=0; while [[ $((n++)) -lt 2 ]] git push",
                     "n=0; while [[ $((n++)) -lt 2 ]] { git push }",
                     "n=0; until [[ $((n++)) -ge 2 ]] git push"):
            with self.subTest(line=line):
                r = self.assertRefused(line, "Law 7")
                self.assertIn("git push", r.reason)
                self.assertRefused(line, "Law 7", AGENT_C)
                self.assertSilent(line, agent_id=None)  # Law 7 refuses members only
        self.assertEqual(self.analysis("if [[ -n x ]] git push").findings, [("git", ("push", "push"))])

    def test_every_condition_reaches_its_body(self):
        for condition in SHORT_CONDITIONS:
            with self.subTest(condition=condition):
                self.assertRefused(condition % "git push", "Law 7")
                self.assertRefused(condition % "git push", "Law 7", AGENT_C)

    def test_every_body_form_reaches_its_commands(self):
        for condition in ("if [[ -n x ]] %s", "n=0; while [[ $((n++)) -lt 2 ]] %s", "n=0; until [[ $((n++)) -ge 2 ]] %s"):
            for body in SHORT_CONDITION_BODIES:
                with self.subTest(condition=condition, body=body):
                    self.assertRefused(condition % (body % "git push"), "Law 7")
        # `then` and `do` with no terminator before them, which zsh takes as the long body
        self.assertRefused("if [[ -n x ]] then git push; fi", "Law 7")
        self.assertRefused("n=0; while [[ $((n++)) -lt 2 ]] do git push; done", "Law 7")

    def test_every_payload_in_every_body_for_every_caller(self):
        for condition in ("if [[ -n x ]] %s", "n=0; while [[ $((n++)) -lt 2 ]] %s", "if true && [[ -n x ]] %s"):
            for body in ("%s", "{ %s }", "( %s )"):
                self.assertPayloadsAnswered(condition % body, self.member_payloads(), self.spud_payloads())

    def test_a_target_outside_a_narrow_members_deliverables(self):
        """AGENT_A plans tests/** and bin/spud, so note.txt at the home is refused it and allowed the ** member."""
        for condition in ("if [[ -n x ]] %s", "n=0; while [[ $((n++)) -lt 2 ]] %s", "if [[ -n x ]] && [[ -n y ]] %s"):
            for body in ("%s", "{ %s }", "( %s )"):
                line = condition % (body % "echo x > note.txt")
                with self.subTest(line=line):
                    self.assertRefused(line, "deliverables")
                    self.assertSilent(line, AGENT_C)

    # -- the directory model ---------------------------------------------------------------
    def test_either_directory_follows_a_short_conditional(self):
        """The condition may be false, so what runs after the body may be in either directory (SPD-030's model, which pop
        already computes for `if ... then ... fi`); a `( )` body's cd does not carry out at all."""
        out, home = self.out, self.home.path
        for condition in ("if [[ -n x ]] %s", "if [[ -n x ]] && [[ -n y ]] %s"):
            for body in ("%s", "{ %s; }", "then %s; fi"):
                with self.subTest(condition=condition, body=body):
                    self.assertRefused((condition % (body % ("cd %s" % out))) + "; echo x > note.txt", "deliverables")
                    self.assertRefused((condition % (body % ("cd %s/ledger" % home))) + "; echo x > tickets/SPD-001.md",
                                       "generated", AGENT_C)
            with self.subTest("a subshell body does not move the shell: " + condition):
                self.assertSilent((condition % ("(cd %s)" % out)) + "; echo x > tests/zzone/k.py")
        # a while or until body may repeat, so a relative cd there is unfollowable, as in the long form
        for condition in ("n=0; while [[ $((n++)) -lt 2 ]] %s", "n=0; until [[ $((n++)) -ge 2 ]] %s"):
            for body in ("%s", "{ %s; }"):
                with self.subTest(condition=condition, body=body):
                    self.assertRefused((condition % (body % "cd docs")) + "; echo x > note.txt", "cannot follow")
                    self.assertRefused((condition % (body % ("cd %s" % out))) + "; echo x > note.txt", "deliverables")
        self.assertEqual(self.analysis("if [[ -n x ]] (cd /tmp)").cwds, frozenset([str(self.home.path)]))
        self.assertEqual(sorted(self.analysis("if [[ -n x ]] cd /tmp").cwds), sorted([str(self.home.path), "/tmp"]))

    def test_the_sublist_body_ends_where_zsh_ends_it(self):
        """`if [[ -n x ]] echo a > f1; echo b > f2` left one line in each, so what follows the sublist is outside the body."""
        home = self.home.path
        line = "if [[ -n x ]] echo a > ledger/tickets/SPD-001.md; echo b > tests/zzone/k.py"
        self.assertEqual(len(self.analysis(line).redirects), 2, self.analysis(line).redirects)
        self.assertRefused("if [[ -n x ]] true; echo x > ledger/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertSilent("if [[ -n x ]] true; echo x > tests/zzone/k.py")
        self.assertRefused("if [[ -n x ]] git push; echo b", "Law 7")
        self.assertSilent("if [[ -n x ]] git status; echo b")
        # the `{ }` body ends at its brace, and an else may follow it
        self.assertRefused("if [[ -n x ]] { git status } else { git push }", "Law 7")
        self.assertRefused("if [[ -n x ]] { cd %s } else { cd %s/ledger }; echo x > tickets/SPD-001.md" % (self.out, home),
                           "generated", AGENT_C)

    # -- where a short conditional may stand ------------------------------------------------
    def test_short_conditionals_nested_and_enclosed(self):
        for line in ("if [[ -n x ]] if [[ -n y ]] git push",
                     "if [[ -n x ]] if true; then git push; fi",
                     "if [[ -n x ]] case x in x) git push;; esac",
                     "if [[ -n x ]] repeat 2 git push",
                     "repeat 1 if [[ -n x ]] git push",
                     "for f (a) if [[ -n x ]] git push",
                     "for f in a b; do if [[ -n x ]] git push; done",
                     "if [[ -n x ]] true && git push",
                     "if [[ -n x ]] false || git push",
                     "if [[ -n x ]] git push | cat",
                     "if [[ -n x ]] git push &",
                     "n=0; while [[ $((n++)) -lt 2 ]] git push & wait",
                     "if [[ -n x ]] git push > out 2>&1",
                     "{ if [[ -n x ]] git push; }",
                     "( if [[ -n x ]] git push )",
                     "f() { if [[ -n x ]] git push; }; f",
                     "eval 'if [[ -n x ]] git push'",
                     "sh -c 'if [[ -n x ]] git push'",
                     "zsh -c 'if [[ -n x ]] git push'",
                     "echo $(if [[ -n x ]] git push)",
                     "echo `while [[ -n x ]] git push`",
                     "coproc if [[ -n x ]] git push",
                     "time if [[ -n x ]] git push",
                     "! if [[ -n x ]] git push",
                     "case x in x) if [[ -n x ]] git push;; esac",
                     "case x in x) if [[ -n x ]] git push;; y) git status;; esac",
                     "if true; then if [[ -n x ]] git push; fi"):
            with self.subTest(line=line):
                self.assertRefused(line, "Law 7")
                self.assertRefused(line, "Law 7", AGENT_C)
        # a short conditional closed above a case frame must not swallow the arm's `;;`
        ledger, out = self.home.path / "ledger", self.out
        self.assertRefused("case x in a) if [[ -n x ]] cd %s;; b) cd %s;; esac; echo x > tickets/SPD-001.md" % (ledger, out),
                           "generated", AGENT_C)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        m = load_spud_module()
        for line in ("if [[ -n x ]] " * 2000 + "git push", "if [[ -n x ]] { " * 500 + "git push" + " }" * 500,
                     "while [[ -n x ]] ( " * 200 + "git push" + " )" * 200, "if [[ -n x ]] && " * 2000 + "[[ -n y ]] git push",
                     "if [[ " * 2000, "while [[ -n x ]] " * 2000):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = m.analyse_command(line, m.ShellAnalysis(cwd=str(self.home.path)))
                self.assertLess(time.monotonic() - started, 5.0)
                if "git push" in line:
                    self.assertIn(("git", ("push", "push")), a.findings, line[:40])

    # -- controls -----------------------------------------------------------------------------
    def test_the_forms_that_were_read_before_are_read_the_same_way(self):
        home, out = self.home.path, self.out
        for refused in ("if (( 1 )) git push", "n=0; while (( n++ < 2 )) git push", "n=0; while (( n++ < 2 )) { git push }",
                        "if [[ -n x ]]; then git push; fi", "while [[ -n x ]]; do git push; done",
                        "until [[ -n x ]]; do git push; done", "if [[ -n x ]]; then true; else git push; fi",
                        "if [[ -n x ]]; then true; elif [[ -n y ]]; then git push; fi",
                        "[[ -n x ]] && git push", "[[ -n x ]]; git push", "[[ -n x ]] || git push",
                        "if true; then [[ -n x ]]; git push; fi", "for f in a; do [[ -n x ]]; git push; done"):
            with self.subTest(refused):
                self.assertRefused(refused, "Law 7")
        # an ordinary `[[ ... ]]` test in command position is untouched, and so is the long form's directory model
        for ok in ("[[ -n x ]] && git status", "[[ -n x ]]", "if [[ -n x ]]; then git status; fi",
                   "if true; then [[ -n x ]] && git status; echo after; fi",
                   "echo '[[ -n x ]] git push'", "echo \"if [[ -n x ]] git push\"",
                   "grep -n 'if [[ -n x ]] git push' tests/zzone/k.py",
                   "%s --as %s member log 'if [[ -n x ]] git push'" % (self.spud_cli, AGENT_A),
                   "if [[ -n x ]]; then echo hi; fi", "if [ -n x ]; then git status; fi"):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
        self.assertSilent("if [[ -n x ]]; then cd %s; fi; echo x > tests/zzone/k.py" % out)
        self.assertRefused("if [[ -n x ]]; then cd %s; fi; echo x > note.txt" % out, "deliverables")
        self.assertRefused("if [[ -n x ]]; then cd %s/ledger; fi; echo x > tickets/SPD-001.md" % home, "generated", AGENT_C)

    def test_the_single_bracket_spellings_run_nothing_and_stay_silent(self):
        """`[ ... ]` is an ordinary command, so zsh's short form never reaches a body through it (probed: `if [ -n x ] git
        push` and `if true git push` are parse errors, `while [ $n -lt 0 ] git push` never iterates, and `until [ -n x ] git
        push` spins with an empty body).  The hook leaves all of them as they were."""
        for ok in ("if [ -n x ] git push", "if [ -n x ] { git push }", "n=0; while [ $n -lt 0 ] git push",
                   "if true git push", "if grep -q ']]' tests/zzone/k.py; then git status; fi",
                   "test -n x && git status"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        self.assertEqual(self.analysis("if [ -n x ] git push").findings, [])


# SPD-136: an `elif` after zsh's short conditional.  Each form fills its `%s` slots with the command the `if`'s body runs
# and the command the `elif`'s body runs; ELIF_EVIDENCE is Spud's probe of 2026-09-19, the spellings zsh 5.9 ran the elif's
# body for -- with an `else`, with a terminator after the body, with `fi`, with a sublist body, and with a short loop or an
# eval inside the body -- and ELIF_CHAINED the same with a second elif (four slots: the if's body, both elifs' bodies, and
# the else's body or the command after the conditional).  ELIF_PARSE_ERRORS are the spellings zsh refused to parse, read as
# the running form all the same (fail closed), the ticket's own headline example among them.
ELIF_EVIDENCE = ("if [[ -z x ]] { %s } elif [[ -n y ]] { %s } else { true }",
                 "if [[ -z x ]] { %s } elif [[ -n y ]] { %s }; true",
                 "if [[ -z x ]] { %s } elif [[ -n y ]] { %s } fi",
                 "if [[ -z x ]] { %s } elif [[ -n y ]] %s",
                 "if [[ -z x ]] { %s } elif [[ -n y ]] %s; true",
                 "if [[ -z x ]] { %s } elif [[ -n y ]] { repeat 1 %s } else { true }",
                 "if [[ -z x ]] { %s } elif [[ -n y ]] { eval '%s' } else { true }")
ELIF_CHAINED = ("if [[ -z x ]] { %s } elif [[ -z y ]] { %s } elif [[ -n z ]] { %s } else { %s }",
                "if [[ -z x ]] { %s } elif [[ -z y ]] { %s } elif [[ -n z ]] { %s }; %s",
                "if [[ -z x ]] { %s } elif [[ -z y ]] { %s } elif [[ -n z ]] %s; %s")
ELIF_PARSE_ERRORS = ("if [[ -z x ]] { %s } elif [[ -n y ]] { %s }",
                     "if [[ -z x ]] { %s } elif [[ -z y ]] { %s } elif [[ -n z ]] { %s }",
                     "if [[ -z x ]] { %s } elif [[ -n y ]] {%s}",
                     "if [[ -z x ]] { %s } elif [[ -n y ]] { %s } else true",
                     "if [[ -z x ]] { %s } elif [[ -n y ]] { %s } always { true }")
# The long forms, read correctly before this ticket and unchanged by it: the `then` after an elif's condition, whether the
# branch before it was the short conditional's body or the long form's, and an elif whose condition is a plain command.
ELIF_LONG_FORMS = ("if [[ -z x ]]; then %s; elif [[ -n y ]]; then %s; fi",
                   "if [[ -z x ]] { %s } elif [[ -n y ]]; then %s; fi",
                   "if [[ -z x ]]; then %s; elif true; then %s; fi",
                   "if [[ -z x ]]; then %s; elif git status; then %s; fi",
                   "if [[ -z x ]]; then %s; elif [[ -n y ]]; then true; else %s; fi")


class ElifShortConditionalTest(BashHookCase):
    """SPD-136: SPD-061 reads zsh's short conditional -- an `if`, `while` or `until` whose condition ends in `[[ ... ]]`
    needs no `then` or `do` -- but its frame's body was left at "sublist" when an `elif` followed, so the elif's own `]]`
    ended no condition (end_header fires only while the body is "cond") and the `{` after it was appended to the elif's
    words as an ordinary argument.  Everything in the elif's condition and body was then read as arguments of one simple
    command whose command word was `[[`: a member's push, write, spud call or cd there was never checked (Laws 1, 5, 6
    and 7).  branch() now reopens the condition at an `elif` on a conditional's frame, so the elif is read exactly as the
    `if`'s own condition and body were.

    No shell is probed here: this worktree session's harness refuses to run one (SPD-094).  The evidence is Spud's probe
    of 2026-09-19, recorded on the ticket -- zsh 5.9 -f, a function `vcs` standing in for the VCS program that logs its
    arguments, the line started in a scratch directory D.  It corrects the ticket's own headline example, `if [[ -z x ]]
    { vcs a } elif [[ -n y ]] { vcs b }` with nothing after it: that spelling is a parse error and runs nothing.  The
    forms that ran are ELIF_EVIDENCE and ELIF_CHAINED -- with an `else`, with a terminator after the elif's body, with
    `fi`, with a sublist body (`elif [[ -n y ]] vcs b`), and with `repeat 1` or `eval` inside the body -- each of which
    was findings=[] for a push in the elif's body on main.  The probe also showed:

    - the directory carries out of an elif's body: `if [[ -z x ]] { vcs a } elif [[ -n y ]] { cd /tmp; vcs in-$PWD } else
      { vcs c }` logged in-/tmp and left the line in /tmp, while main left it in D, missing the cd in both directions.
      The elif's body is a branch, so either directory follows the whole conditional, as `else`'s body already gave;
    - a redirection inside the elif's body was recorded even on main (separate_redirects sees a target wherever the words
      fall), but in the directory the line started in; the commands around it were what went unread;
    - the parse errors ELIF_PARSE_ERRORS, and the headline example above, ran nothing.  Each is over-read as the running
      form, which costs a refusal on a line no shell accepts.  `elif true { vcs b }` is the one parse error left as it
      was: no `]]` ends that condition, so the body is not a short conditional's, exactly as `if true { vcs b }` is not
      one either -- zsh parses neither, and reading them would take a short conditional's body where no condition ended;
    - the long forms ELIF_LONG_FORMS were read correctly before this ticket and must stay that way.

    A `( list )` body after an elif is read as it is after an `if`: since SPD-142 the `}` that closes the if's body gives
    mark_zsh_patterns its command position back, so zsh's reading takes that `( ... )` for the subshell zsh runs and no
    longer for one glob pattern word (BraceCloseCommandPositionTest).  AGENT_A plans tests/** and bin/spud; AGENT_C plans
    **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/zzone"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)  # Law 7 refuses members only
        return r

    def member_payloads(self):
        """(command, the reason a member is refused for it): Law 7, Law 6, Law 5's --as, the database, and Law 1 through a
        redirection and through tee."""
        home, spud = self.home.path, self.spud_cli
        return (("git push", "Law 7"),
                ("%s ticket new --title x" % spud, "Law 6"),
                ("%s --as spud member log hi" % spud, "Law 6"),
                ("%s --as %s member log hi" % (spud, AGENT_B), "--as"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "generated"),
                ("echo x | tee ledger/tickets/SPD-001.md", "generated"))

    def spud_payloads(self):
        """Spud is never refused for git; these are the checks that do apply to him."""
        home, spud = self.home.path, self.spud_cli
        return (("%s --as %s member log hi" % (spud, AGENT_A), "--as"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "Law 1"),
                ("echo x | tee ledger/tickets/SPD-001.md", "Law 1"))

    # -- the hole ---------------------------------------------------------------------------
    def test_the_tickets_headline_example_and_the_probes_running_forms(self):
        """The headline example is a parse error, over-read here; every form the probe ran finds the elif's push, which
        main found in none of them."""
        push, status = ("git", ("push", "push")), ("git", ("status", None))
        self.assertEqual(self.analysis("if [[ -z x ]] { git status } elif [[ -n y ]] { git push }").findings,
                         [status, push])
        for form in ELIF_EVIDENCE:
            line = form % ("git status", "git push")
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).findings, [status, push], line)
                r = self.refused_for_members(line)
                self.assertIn("git push", r.reason)

    def test_every_running_form_reaches_both_bodies(self):
        for form in ELIF_EVIDENCE + ELIF_CHAINED + ELIF_PARSE_ERRORS:
            slots = form.count("%s")
            for k in range(slots):
                line = form % tuple("git push" if j == k else "git status" for j in range(slots))
                with self.subTest(line=line):
                    self.assertIn(("git", ("push", "push")), self.analysis(line).findings, line)
                    self.assertRefused(line, "Law 7")
                    self.assertRefused(line, "Law 7", AGENT_C)
            self.assertSilent(form % tuple("git status" for _ in range(slots)))

    def test_every_payload_in_the_elifs_body_for_every_caller(self):
        for form in ("if [[ -z x ]] { true } elif [[ -n y ]] { %s } else { true }",
                     "if [[ -z x ]] { true } elif [[ -n y ]] { %s }; true",
                     "if [[ -z x ]] { true } elif [[ -n y ]] %s",
                     "if [[ -z x ]] { true } elif [[ -n y ]] { %s } fi",
                     "if [[ -z x ]] { true } elif [[ -z y ]] { true } elif [[ -n z ]] { %s } else { true }"):
            self.assertPayloadsAnswered(form, self.member_payloads(), self.spud_payloads())

    def test_a_target_outside_a_narrow_members_deliverables(self):
        """AGENT_A plans tests/** and bin/spud, so note.txt at the home is refused it and allowed the ** member."""
        for form in ("if [[ -z x ]] { true } elif [[ -n y ]] { %s } else { true }",
                     "if [[ -z x ]] { true } elif [[ -n y ]] %s",
                     "if [[ -z x ]] { true } elif [[ -n y ]] { %s } fi"):
            line = form % "echo x > note.txt"
            with self.subTest(line=line):
                self.assertRefused(line, "deliverables")
                self.assertSilent(line, AGENT_C)
                self.assertSilent(form % "echo x > tests/zzone/k.py")

    def test_the_elifs_condition_is_read_as_the_ifs_is(self):
        """Every command of the elif's condition list runs, and only the last `]]` ends it (SPD-061's rule, now reached
        through the elif as well)."""
        for line in ("if [[ -z x ]] { true } elif git push; then true; fi",
                     "if [[ -z x ]] { true } elif git push && [[ -n y ]] { true } else { true }",
                     "if [[ -z x ]] { true } elif [[ -n y ]] && [[ -n z ]] git push",
                     "if [[ -z x ]] { true } elif [[ -n y ]] || [[ -z z ]] git push",
                     "if [[ -z x ]] { true } elif [[ -n $(git push) ]] { true } else { true }",
                     "if [[ -z x ]] { true } elif [[ -n y ]] { true } elif git push; then true; fi"):
            with self.subTest(line=line):
                self.assertIn(("git", ("push", "push")), self.analysis(line).findings, line)
                self.refused_for_members(line)

    def test_every_body_form_inside_the_elifs_body(self):
        """The elif's body is read as the if's is: a nested conditional, a short loop, a long loop, an eval, a group, a
        subshell, an `sh -c`, an and-or list, a pipeline and a case arm."""
        for form in ("if [[ -z x ]] { true } elif [[ -n y ]] { %s } else { true }",
                     "if [[ -z x ]] { true } elif [[ -n y ]] { %s }; true",
                     "if [[ -z x ]] { true } elif [[ -n y ]] %s"):
            for body in ("%s", "if [[ -n z ]] %s", "repeat 1 %s", "for f in a b; do %s; done", "eval '%s'", "{ %s }",
                         "( %s )", "sh -c '%s'", "true && %s", "false || %s", "%s | cat",
                         "case x in x) %s;; esac", "if true; then %s; fi"):
                line = form % (body % "git push")
                with self.subTest(line=line):
                    self.assertIn(("git", ("push", "push")), self.analysis(line).findings, line)
                    self.assertRefused(line, "Law 7")

    # -- the directory model -------------------------------------------------------------------
    def test_the_directory_carries_out_of_an_elifs_body_in_both_directions(self):
        """The probe's `... elif [[ -n y ]] { cd /tmp; vcs in-$PWD } else { vcs c }` logged in-/tmp and left the line in
        /tmp; the elif's condition may be false, so either directory follows the whole conditional."""
        home, out = str(self.home.path), str(self.out)
        for form in ("if [[ -z x ]] { true } elif [[ -n y ]] { cd %s } else { true }",
                     "if [[ -z x ]] { true } elif [[ -n y ]] { cd %s }; true",
                     "if [[ -z x ]] { true } elif [[ -n y ]] cd %s",
                     "if [[ -z x ]] { true } elif [[ -n y ]] { cd %s } fi",
                     "if [[ -z x ]] { true } elif [[ -z y ]] { true } elif [[ -n z ]] { cd %s } else { true }",
                     "if [[ -z x ]]; then true; elif [[ -n y ]]; then cd %s; fi"):
            line = form % out
            with self.subTest(line=line):
                self.assertEqual(sorted(self.analysis(line).cwds), sorted([home, out]), line)
                self.assertIn(("k.txt", frozenset([home, out])), self.analysis(line + "; echo x > k.txt").redirects, line)
        # the ledger through the elif's cd, and the directory the line started in through the same line
        self.assertRefused("if [[ -z x ]] { true } elif [[ -n y ]] { cd %s/ledger }; echo x > tickets/SPD-001.md" % home,
                           "generated", AGENT_C)
        self.assertRefused("if [[ -z x ]] { true } elif [[ -n y ]] cd %s; echo x > note.txt" % out, "deliverables")
        self.assertSilent("if [[ -z x ]] { true } elif [[ -n y ]] { cd %s }; echo x > tests/zzone/k.py" % out)
        # a `( )` body runs in its own process, so its cd does not carry; a relative cd in the body runs at most once
        self.assertEqual(self.analysis("if [[ -z x ]] { true } elif [[ -n y ]] ( cd %s ) else { true }" % out).cwds,
                         frozenset([home]))
        self.assertEqual(sorted(self.analysis("if [[ -z x ]] { true } elif [[ -n y ]] { cd docs } else { true }").cwds),
                         sorted([home, home + "/docs"]))

    def test_a_redirection_in_the_elifs_body_opens_where_the_body_left_the_shell(self):
        """main recorded the target wherever the words fell (separate_redirects), but always in the directory the line
        started in; the commands around it were what went unread."""
        home, out = str(self.home.path), str(self.out)
        a = self.analysis("if [[ -z x ]] { true } elif [[ -n y ]] { cd %s; echo x > k.txt } else { true }" % out)
        self.assertIn(("k.txt", frozenset([out])), a.redirects)
        self.assertEqual(self.analysis("if [[ -z x ]] { true } elif [[ -n y ]] { echo x > k.txt } else { true }").redirects,
                         [("k.txt", frozenset([home]))])
        self.assertRefused("if [[ -z x ]] { true } elif [[ -n y ]] { cd %s/ledger; echo x > tickets/SPD-001.md }" % home,
                           "generated", AGENT_C)
        self.assertRefused("if [[ -z x ]] { true } elif [[ -n y ]] { echo x > ledger/tickets/SPD-001.md } else { true }",
                           "generated", AGENT_C)

    # -- controls ------------------------------------------------------------------------------
    def test_the_long_forms_are_read_as_before(self):
        home, out = str(self.home.path), str(self.out)
        for form in ELIF_LONG_FORMS:
            for k in range(2):
                line = form % tuple("git push" if j == k else "git status" for j in range(2))
                with self.subTest(line=line):
                    self.assertIn(("git", ("push", "push")), self.analysis(line).findings, line)
                    self.assertRefused(line, "Law 7")
            self.assertSilent(form % ("git status", "git status"))
        self.assertEqual(sorted(self.analysis("if [[ -z x ]]; then true; elif [[ -n y ]]; then cd %s; fi" % out).cwds),
                         sorted([home, out]))
        self.assertRefused("if [[ -z x ]]; then true; elif [[ -n y ]]; then cd %s/ledger; fi; echo x > tickets/SPD-001.md"
                           % home, "generated", AGENT_C)

    def test_an_elif_that_reaches_no_conditional_is_read_as_before(self):
        """A loop's frame, a group's and no frame at all: an `elif` there is not a conditional's branch, and nothing about
        those readings changes (zsh parses none of them)."""
        status = ("git", ("status", None))
        self.assertEqual(self.analysis("while [[ -n x ]] { git status } elif [[ -n y ]] { git push }").findings, [status])
        self.assertEqual(self.analysis("elif [[ -n y ]] { git push }").findings, [])
        self.assertEqual(self.analysis("echo x elif [[ -n y ]] { git push }").findings, [])
        # `elif true { git push }`: no `]]` ends that condition, so no short body follows it -- as after `if true`
        self.assertEqual(self.analysis("if [[ -z x ]] { git status } elif true { git push }").findings, [status])
        self.assertEqual(self.analysis("if true { git push }").findings, [])
        for ok in ("echo 'if [[ -z x ]] { a } elif [[ -n y ]] { git push }'",
                   "echo \"elif [[ -n y ]] { git push }\"",
                   "grep -n 'elif \\[\\[ -n y \\]\\] { git push }' tests/zzone/k.py",
                   "%s --as %s member log 'elif [[ -n y ]] { git push }'" % (self.spud_cli, AGENT_A),
                   "if [[ -z x ]] { git status } elif [[ -n y ]] { git log } else { git status }"):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))

    def test_the_other_short_forms_are_unchanged(self):
        """SPD-042's short loops, SPD-061's short conditionals, SPD-124's try-always and SPD-132's glued braces, on lines
        with no elif: an elif's reading is the only thing this ticket moves."""
        for condition in SHORT_CONDITIONS:
            for body in SHORT_CONDITION_BODIES:
                with self.subTest(condition=condition, body=body):
                    self.assertRefused(condition % (body % "git push"), "Law 7")
        for refused in ("repeat 2 { git push }", "for f (a) git push", "{ git status } always { git push }",
                        "{git push}", "if [[ -n x ]] { echo a } else { git push }",
                        "if [[ -n x ]] { echo a } else { repeat 1 git push }",
                        "if [[ -n x ]]; then echo a; else git push; fi"):
            with self.subTest(refused):
                self.refused_for_members(refused)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        m = load_spud_module()
        for line in ("if [[ -z x ]] { true } " + "elif [[ -n y ]] { true } " * 1000 + "elif [[ -n z ]] { git push }",
                     "if [[ -z x ]] " + "elif " * 5000 + "git push",
                     "if [[ -z x ]] { true } elif " * 1000 + "[[ -n y ]] { git push }",
                     "if [[ -z x ]] { true } elif [[ -n y ]] { " * 500 + "git push" + " }" * 500,
                     "elif [[ " * 2000, "if [[ -z x ]] { true } elif [[ -n y ]] && " * 1000 + "[[ -n z ]] git push"):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = m.analyse_command(line, m.ShellAnalysis(cwd=str(self.home.path)))
                self.assertLess(time.monotonic() - started, 5.0)
                if "git push" in line:
                    self.assertIn(("git", ("push", "push")), a.findings, line[:40])


# SPD-142: the lines in which a closing `}` gives zsh its command position back and a `( list )` read after it runs as a
# subshell; each form's `%s` is that subshell's list.  Probed 2026-09-22 (BraceCloseCommandPositionTest): with `echo
# <label>` in the slot zsh 5.9 printed the label for every form, and so it did with `{echo <label>}` (a group glued to its
# words, SPD-132); with `echo x | tee l/t` there it wrote l/t, and with `cd o` the line ended where it began.
BRACE_CLOSE_SUBSHELLS = (
    # a short conditional's `{ }` body, then an elif whose condition ends in `[[ ... ]]` (SPD-136) and whose body is the
    # subshell
    "if [[ -z x ]] { true } elif [[ -n y ]] ( %s )",
    "if [[ -z x ]] { true } elif [[ -n y ]] ( %s ); true",
    "if [[ -z x ]] { true } elif [[ -n y ]] (%s)",
    "if [[ -z x ]] { true } elif [[ -z y ]] { true } elif [[ -n z ]] ( %s )",
    # ... an else whose body is the subshell, closed by fi
    "if [[ -z x ]] { true } else ( %s ); fi",
    "if [[ -z x ]] { true } else ( %s ) fi",
    # ... an elif's or an else's own `{ }` body holding the subshell, apart from its brace or glued to it
    "if [[ -z x ]] { true } elif [[ -n y ]] { ( %s ) }; true",
    "if [[ -z x ]] { true } elif [[ -n y ]] { ( %s ) } fi",
    "if [[ -z x ]] { true } elif [[ -n y ]] {( %s )}; true",
    "if [[ -z x ]] { true } else { ( %s ) }",
    "if [[ -z x ]] { true } else {( %s )}",
    # ... an elif whose condition is the subshell
    "if [[ -z x ]] { true } elif ( %s ) then true; fi",
    "if [[ -z x ]] { true } elif ( %s ); then true; fi",
    "if [[ -z x ]] { true } elif ( %s ) { true }; true",
    # zsh's try-always form (SPD-124), its always block holding the subshell
    "{ true } always { ( %s ) }",
    "{ true } always {( %s )}",
    "{ { true } always { ( %s ) } }",
    # a group ending a long form's condition or body, before then, do, else or elif
    "if { true } then ( %s ) fi",
    "n=0; while { [[ $((n++)) -lt 1 ]] } do ( %s ) done",
    "if false; then { true } else ( %s ); fi",
    "if false; then { true } elif { true } then ( %s ) fi",
    # the closing brace glued to the word before it, which zsh splits off (SPD-132): a command's last word, a one-word
    # group, a redirection's target, a condition's `]]` and a case's esac
    "if [[ -z x ]] { true} elif [[ -n y ]] ( %s )",
    "if [[ -z x ]] {true} elif [[ -n y ]] ( %s )",
    "if [[ -z x ]] {true} else ( %s ); fi",
    "{true} always {( %s )}",
    "if [[ -z x ]] { true > /tmp/k} elif [[ -n y ]] ( %s )",  # a temp-dir target, open to members in bash's `/tmp/k}` too
    "if [[ -z x ]] { [[ -n y ]]} else ( %s ); fi",
    "if [[ -z x ]] { case y in y) true;; esac} else ( %s ); fi",
)
# ... and an arithmetic command after such a brace, `%s` the command after it: zsh printed the label and made no file.
BRACE_CLOSE_ARITHMETIC = (
    "if [[ -z x ]] { true } elif (( 3 > 2 )) %s",
    "if [[ -z x ]] { true } elif (( 3 > 2 )) then %s; fi",
    "if [[ -z x ]] { true } else (( 3 > 2 )) && %s; fi",
    "if [[ -z x ]] { true } else { (( 3 > 2 )) && %s }",
    "{ true } always { (( 3 > 2 )) && %s }",
    "if false; then { true } elif (( 3 > 2 )) then %s; fi",
)
# A `(` right after the closing `}`: zsh rejects every one of these lines, so nothing on them runs -- a parse error near
# `(` after a group, an always block or a function body, near the list's first word after a short conditional's body, and
# near the whole `( ... )` after a short loop's body, which zsh reads there as a glob pattern word.
BRACE_CLOSE_PARSE_ERRORS = (
    "{ true } ( %s )",
    "{ true } always { true } ( %s )",
    "f() { true } ( %s )",
    "if [[ -n x ]] { true } ( %s )",
    "repeat 1 { true } ( %s )",
    "n=0; while [[ $((n++)) -lt 1 ]] { true } ( %s )",
    "for f in a; { true } ( %s )",
)
# A pattern after a brace that closes no group -- quoted, escaped, a parameter expansion's, a brace list's, one inside a
# word -- is a pattern to zsh, which ran each of these (the first four printed `} b c`, `} b c`, `b c` and `b c b c` with
# files b and c present; the last two failed with "no matches found", a pattern matching nothing).
BRACE_KEPT_PATTERNS = ("echo '}' (b|c)", "echo \\} (b|c)", "echo ${x} (b|c)", "echo {b,c} (b|c)", "echo }(b|c)", "echo a}(b|c)")


class BraceCloseCommandPositionTest(BashHookCase):
    """SPD-142, found while SPD-136 read the elif of zsh's short conditional: mark_zsh_patterns takes a `(` that opens a word
    for a subshell in zsh's command position and for one of zsh's glob patterns outside it, and a closing `}` never gave
    it command position back.  After `{ a }` it was still after the command word `a`, so a reserved word that goes on with
    the compound command -- `else`, `elif`, `then`, `do`, the try-always form's `always` -- was read as one more argument,
    the `[[` of an elif's condition opened nothing, and the `( list )` after them became one pattern word, where zsh runs
    a subshell.

    Probed 2026-09-22 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o
    nobareglobqual and under -f, which printed the same for every line, and in GNU bash 3.2.57, which rejects every line
    here (it has no short forms, and a group's `}` wants a terminator before it); an `echo` stood in for each command:

    - BRACE_CLOSE_SUBSHELLS ran their subshell.  zsh's `}` is significant wherever it stands, alone or split off the end
      of a word (SPD-132): `{ echo a}`, `{true}`, `{ echo x > g}` (which made g, not `g}`), `{ [[ -n x ]]}` and `{ case
      ... esac}` each closed their group, and after it zsh read `else`, `elif`, `fi`, `then`, `do`, `done`, `esac` and
      `always` as the reserved words they are.  A brace that closes no group -- `echo a}`, `for f in a} b` -- is a parse
      error;
    - the hole: the other reading restores a `(` that opens a word, so a plain `( git push )` in these forms was refused
      all the same, but that reading is bash's, which keeps a brace glued to a word, and a subshell glued to its `{` stayed
      a pattern in both readings.  So `( {list} )` in any of these forms, and `{( list )}` after an else, an elif or an
      always, ran their list in zsh while zsh's reading held one glob command word.  One whose words hold no `/` went
      unchecked -- `{ true } always {( git push )}`, `... else {( rm -rf docs )}`, `... elif [[ -n y ]] ( {touch note.txt}
      )`: Law 7, and the path rule (Law 5 for a member, Law 1 for Spud); one holding a `/` could name a file called sqlite
      and was refused as direct access to the database, the right refusal for a tee into the ledger or a spud call, for the
      wrong reason;
    - the friction: the same misreading refused harmless lines to members and Spud alike, the ticket's evidence first
      (`if [[ -z x ]] { true } elif [[ -n y ]] ( cd /tmp/o )`) and a member's write into its own deliverables beside it;
      and an arithmetic command after the brace, read as a subshell holding `> 2`, was a write to a file named 2
      (BRACE_CLOSE_ARITHMETIC, which zsh evaluated without making one);
    - a brace glued to `]]` or `esac` left the scanner inside the condition or the case, where no `(` is a pattern:
      `{ [[ -n x ]]}; echo x > (l|x)/t` and `{ case x in x) echo a;; esac}; echo x > (l|x)/t` wrote l/t in zsh, while the
      hook read the same lines with the ledger in them as a subshell and a target `/tickets/SPD-001.md` (Spud's reading of
      the esac line named `ledger` instead): after `]]}` Spud's Law 1 went unchecked, and every other refusal named a file
      the line never writes;
    - a `(` right after the `}` is a parse error in every form (BRACE_CLOSE_PARSE_ERRORS).  After a short loop's `{ }`
      body zsh reads it as a pattern word -- the one place it reads a pattern right after a closing `}` -- and rejects the
      line, as it rejects a `fi` or an `else` there (`if true; then repeat 1 { echo r } fi`): after that brace zsh is out of
      command position for good.  The hook reads a subshell in all of them, fail closed, since nothing on such a line runs;
    - a `}` that closes nothing leaves the pattern after it a pattern, as zsh reads and runs it (BRACE_KEPT_PATTERNS).

    AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        (self.out / "o").mkdir()
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/zzone"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")
        self.m = load_spud_module()

    def analysis(self, command):
        return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path)))

    def member_payloads(self):
        """(command, the reason a member is refused for it): Law 7, Law 6, Law 5's --as, the database, and Law 1 through a
        redirection and through tee."""
        home, spud = self.home.path, self.spud_cli
        return (("git push", "Law 7"),
                ("%s ticket new --title x" % spud, "Law 6"),
                ("%s --as spud member log hi" % spud, "Law 6"),
                ("%s --as %s member log hi" % (spud, AGENT_B), "--as"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "generated"),
                ("echo x | tee ledger/tickets/SPD-001.md", "generated"))

    def spud_payloads(self):
        """Spud is never refused for git; these are the checks that do apply to him."""
        home, spud = self.home.path, self.spud_cli
        return (("%s --as %s member log hi" % (spud, AGENT_A), "--as"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "Law 1"),
                ("echo x | tee ledger/tickets/SPD-001.md", "Law 1"))

    def every_payload(self, form):
        self.assertPayloadsAnswered(form, self.member_payloads(), self.spud_payloads())

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_is_the_subshell_zsh_runs(self):
        """zsh ran `cd o` there in a subshell and ended where it began; the hook read a glob command word that can name the
        database and refused everyone."""
        home = str(self.home.path)
        line = "if [[ -z x ]] { true } elif [[ -n y ]] ( cd %s/o )" % self.out
        self.assertEqual(self.m.mark_zsh_patterns(line), (line, line))  # no pattern in either reading
        for agent_id in (AGENT_A, AGENT_C, None):
            with self.subTest(agent_id=agent_id):
                self.assertSilent(line, agent_id)
        a = self.analysis(line)
        self.assertEqual((a.findings, a.cwds), ([], frozenset([home])))
        push = self.analysis("if [[ -z x ]] { true } elif [[ -n y ]] ( git push )")
        self.assertEqual(push.findings, [("git", ("push", "push"))])  # one reading, one push: no glob command word beside it

    # -- the hole -----------------------------------------------------------------------------------------------------
    def test_zsh_reads_every_form_as_the_subshell_it_runs(self):
        for form in BRACE_CLOSE_SUBSHELLS:
            for body in ("{git push}", "git push"):
                line = form % body
                with self.subTest(line=line):
                    marked, other = self.m.mark_zsh_patterns(line)
                    self.assertEqual(marked, other)  # no `(` on the line is a pattern in zsh's reading
                    self.assertIn("(", marked)
                    self.assertIn(("git", ("push", "push")), self.analysis(line).findings)

    def test_a_group_in_the_subshell_is_refused(self):
        """The hole: `( {git push} )` runs the push in every form (`{echo <label>}` printed its label in the probe), and
        neither reading found it wherever zsh's reading took that `(` for a pattern."""
        for form in BRACE_CLOSE_SUBSHELLS:
            # Law 7 refuses members only, naming the push
            self.assertAnsweredAs(form % "{git push}", "git push", [(AGENT_A, "Law 7: spudagents never run `git push`"),
                                                                    (AGENT_C, "Law 7"), (None, None)])

    def test_every_payload_in_a_subshell_glued_to_its_brace(self):
        """`{( list )}` stayed one pattern word in both readings, so every check on the list was lost, not Law 7's alone."""
        for form in ("if [[ -z x ]] { true } else {( %s )}", "if [[ -z x ]] { true } elif [[ -n y ]] {( %s )}; true",
                     "{ true } always {( %s )}", "{true} always {( %s )}"):
            self.every_payload(form)

    def test_every_payload_in_a_group_inside_the_subshell(self):
        for form in ("if [[ -z x ]] { true } elif [[ -n y ]] ( {%s} )", "if [[ -z x ]] { true } else ( {%s} ); fi",
                     "{ true } always { ( {%s} ) }", "if false; then { true } else ( {%s} ); fi"):
            self.every_payload(form)

    def test_the_path_rule_inside_the_subshell(self):
        """A write whose words hold no `/` was silent to everyone -- the path rule unchecked -- and one whose words hold a `/`
        was refused as direct access to the database, a member's write into its own deliverables among them."""
        for form in ("{ true } always {( %s )}", "if [[ -z x ]] { true } else {( %s )}",
                     "if [[ -z x ]] { true } elif [[ -n y ]] ( {%s} )", "if [[ -z x ]] { true } else ( {%s} ); fi"):
            for write in ("echo x | tee note.txt", "touch note.txt", "rm -rf docs"):
                line = form % write
                with self.subTest(line=line):
                    self.assertRefused(line, "deliverables")  # AGENT_A plans tests/** and bin/spud
                    self.assertSilent(line, AGENT_C)
                    self.assertRefused(line, "Law 1", agent_id=None)
            line = form % "echo x | tee tests/zzone/k.py"
            with self.subTest(line=line):
                self.assertSilent(line)
                self.assertSilent(line, AGENT_C)

    # -- the friction ---------------------------------------------------------------------------------------------------
    def test_a_harmless_subshell_is_silent_and_moves_nothing(self):
        home = str(self.home.path)
        for form in BRACE_CLOSE_SUBSHELLS:
            line = form % ("cd %s/o" % self.out)
            with self.subTest(line=line):
                self.assertAnsweredAs(line, "true", [(AGENT_A, None), (AGENT_C, None), (None, None)])
                self.assertEqual(self.analysis(line).cwds, frozenset([home]))  # the subshell's cd does not carry out

    def test_an_arithmetic_command_after_the_brace_writes_nothing(self):
        for form in BRACE_CLOSE_ARITHMETIC:
            line = form % "true"
            with self.subTest(line=line):
                self.assertAnsweredAs(line, "true", [(AGENT_A, None), (None, None)])
                self.assertEqual(self.analysis(line).redirects, [])
            self.assertAnsweredAs(form % "git push", "git push", [(AGENT_A, "Law 7"), (None, None)])

    # -- a brace glued to ]] or esac -------------------------------------------------------------------------------------
    def test_a_pattern_after_a_brace_glued_to_a_condition_or_a_case(self):
        """zsh splits the brace off `]]}` and `esac}` and closes the group: each of these lines, with l/t standing in for the
        ledger file, wrote l/t in the probe, and the cd into `(l|x)` wrote it too."""
        for line in ("{ [[ -n x ]]}; echo x > (ledger|x)/tickets/SPD-001.md",
                     "{ [[ -n x ]]} && echo x > (ledger|x)/tickets/SPD-001.md",
                     "{ case x in x) true;; esac}; echo x > (ledger|x)/tickets/SPD-001.md",
                     "{ [[ -n x ]]}; echo x | tee (ledger|x)/tickets/SPD-001.md",
                     "if [[ -z x ]] { [[ -n y ]]} else { echo x > (ledger|x)/tickets/SPD-001.md }"):
            with self.subTest(line=line):
                for agent_id in (AGENT_C, None):
                    r = self.assertRefused(line, "generated", agent_id)
                    self.assertIn("into (ledger|x)/tickets/SPD-001.md", r.reason)  # the target zsh opens, as the line spells it
                self.assertIn("Law 1", self.bash(line, None).reason)
        line = "{ [[ -n x ]]}; cd (ledger|x) && echo x > tickets/SPD-001.md"
        self.assertRefused(line, "cannot follow", AGENT_C)
        self.assertEqual(self.m.mark_zsh_patterns("{ [[ -n x ]]}; echo (b|c)")[0].count("("), 0)

    # -- lines zsh rejects ----------------------------------------------------------------------------------------------
    def test_a_subshell_right_after_the_brace_is_read_fail_closed(self):
        """Nothing on these lines runs; the hook reads the subshell all the same, after a short loop's body too, where
        zsh's own reading of the `(` is a pattern it then rejects."""
        for form in BRACE_CLOSE_PARSE_ERRORS:
            line = form % "{git push}"
            with self.subTest(line=line):
                self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
                self.assertRefused(line, "Law 7")

    # -- the other direction ----------------------------------------------------------------------------------------------
    def test_a_pattern_after_a_brace_that_closes_nothing_stays_a_pattern(self):
        for line in BRACE_KEPT_PATTERNS:
            with self.subTest(line=line):
                marked, other = self.m.mark_zsh_patterns(self.m.neutralize_quoted_globs(line))  # as analyse_command marks it
                self.assertNotIn("(", marked)  # zsh's reading holds the group as one pattern word
                self.assertEqual(self.m.deglob(marked), line)
        for line in ("tee '}' (ledger|x)/tickets/SPD-001.md", "tee \\} (ledger|x)/tickets/SPD-001.md",
                     "tee {tests,docs} (ledger|x)/tickets/SPD-001.md"):
            with self.subTest(line=line):
                r = self.assertRefused(line, "generated", AGENT_C)  # the pattern still expands to the ledger file
                self.assertIn("into (ledger|x)/tickets/SPD-001.md", r.reason)

    # -- the directory model --------------------------------------------------------------------------------------------
    def test_where_each_directory_goes(self):
        """The try block's cd carries into the always block and past it (probed: `{ cd o } always { ( echo in-$PWD ) }`
        printed in-.../o and ended in o), the always block's subshell's does not; a short conditional's body may not run."""
        home, out = str(self.home.path), str(self.out)
        a = self.analysis("{ cd %s } always { ( cd %s/o ) }; echo x > k.txt" % (out, out))
        self.assertIn(("k.txt", frozenset([out])), a.redirects)
        a = self.analysis("if [[ -n x ]] { cd %s } else ( cd %s/o ); fi; echo x > k.txt" % (out, out))
        self.assertIn(("k.txt", frozenset([home, out])), a.redirects)
        self.assertRefused("{ cd %s/ledger } always {( true )}; echo x > tickets/SPD-001.md" % home, "generated", AGENT_C)

    # -- controls ---------------------------------------------------------------------------------------------------------
    def test_a_terminator_or_an_operator_after_the_brace_was_always_read(self):
        """A `;`, a newline, `&&`, `||` and `|` put the scanner in command position before this ticket too (probed: each
        ran the subshell after it)."""
        for form in ("{ true }; ( %s )", "{ true }\n( %s )", "{ true } && ( %s )", "{ false } || ( %s )", "{ true } | ( %s )",
                     "if [[ -n x ]] { true }; ( %s )", "repeat 1 { true } && ( %s )", "f() { true }; ( %s ); f"):
            line = form % "{git push}"
            with self.subTest(line=line):
                self.assertRefused(line, "Law 7")
                self.assertRefused(line, "Law 7", AGENT_C)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        for line in ("if [[ -z x ]] { true } " + "elif [[ -n y ]] { true } " * 2000 + "elif [[ -n z ]] ( {git push} )",
                     "{ " * 2000 + "true" + " }" * 2000 + " always {( git push )}",
                     "{ true" + "}" * 50000 + " always {( git push )}",
                     "{ " + "a}" * 20000 + " always {( git push )}",
                     "{ echo " + "${x}" * 20000 + "} always {( git push )}",
                     "if [[ -z x ]] { true} " + "else ( true ); fi; if [[ -z x ]] { true} " * 1000 + "else ( {git push} ); fi"):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = self.analysis(line)
                self.assertLess(time.monotonic() - started, 5.0)
                self.assertIn(("git", ("push", "push")), a.findings, line[:40])


# SPD-173: the lines in which an arithmetic command's `))` leaves zsh in command position, so that a `( list )` read after
# it runs as a subshell; each form's `%s` is that subshell's list.  Probed 2026-09-22 (ArithmeticCommandPositionTest):
# with `echo <label>` in the slot zsh 5.9 printed the label for every form, and so it did with `{echo <label>}` (a group
# glued to its words, SPD-132); with `echo x | tee l/t` there it wrote l/t, and with `cd o` the line ended where it began.
ARITH_CLOSE_SUBSHELLS = (
    # an if, while or until whose condition ends in an arithmetic command, the subshell its body (zsh's short forms), the
    # ticket's evidence first
    "if (( 1 )) ( %s )",
    "if (( 1 )) (%s)",
    "if (( 1 ))(%s)",
    "if ((1)) ( %s )",
    "if (( 1 )) ( %s ); true",
    "n=0; while (( n++ < 1 )) ( %s )",
    "n=1; until (( n-- < 1 )) ( %s )",
    # ... that condition a list ending in one
    "if (( 1 )) && (( 1 )) ( %s )",
    "if true && (( 1 )) ( %s )",
    "if (( 0 )) || (( 1 )) ( %s )",
    "if ! (( 0 )) ( %s )",
    # an elif whose condition is one, after a short conditional's `{ }` body (SPD-142) or after a long form's then
    "if [[ -z x ]] { true } elif (( 1 )) ( %s )",
    "if (( 0 )) { true } elif (( 1 )) ( %s )",
    "if false; then true; elif (( 1 )) ( %s )",
    # then or do right after the `))`, their body holding the subshell
    "if (( 1 )) then ( %s ) fi",
    "if (( 1 )) then ( %s ); fi",
    "n=0; while (( n++ < 1 )) do ( %s ) done",
    "if [[ -z x ]] { true } elif (( 1 )) then ( %s ) fi",
    "if false; then true; elif (( 1 )) then ( %s ) fi",
    # a `{ }` body holding the subshell, apart from its brace or glued to it
    "if (( 1 )) { ( %s ) }; true",
    "if (( 1 )) { ( %s ) } fi",
    "if (( 1 )) {( %s )}; true",
    "if (( 0 )) { true } elif (( 1 )) { ( %s ) }; true",
    "n=0; while (( n++ < 1 )) { ( %s ) }",
    "n=0; while (( n++ < 1 )) {( %s )}",
    "n=1; until (( n-- < 1 )) { ( %s ) }",
    # a compound command or a prefix word as the body, the subshell after it
    "if (( 1 )) if [[ -n x ]] ( %s )",
    "if (( 1 )) repeat 1 ( %s )",
    "if (( 1 )) for f (a) ( %s )",
    "n=0; if (( 1 )) while (( n++ < 1 )) ( %s )",
    "if (( 1 )) case x in x) ( %s );; esac",
    "if (( 1 )) time ( %s )",
    "if (( 1 )) ! ( %s )",
    # the arithmetic command's own redirection before the body
    "if (( 1 )) > /dev/null ( %s )",
)
# ... and an arithmetic command right after the `))`, `%s` the command after it: zsh printed the label and made no file 2.
ARITH_CLOSE_ARITHMETIC = (
    "if (( 1 )) (( 3 > 2 )) && %s",
    "if (( 1 )) (( 3 > 2 )) && %s; true",
    "n=0; while (( n++ < 1 )) (( 3 > 2 )) && %s",
    "if [[ -z x ]] { true } elif (( 1 )) (( 3 > 2 )) && %s",
    "if (( 1 )) then (( 3 > 2 )) && %s; fi",
    "n=0; while (( n++ < 1 )) do (( 3 > 2 )) && %s; done",
)
# A `( list )` after an arithmetic command that is no condition -- at the start of a line, after `time` or `!`, glued to
# a `{` -- and a short conditional's `{ }` body with nothing after its `}`: zsh rejects every one of these lines, so
# nothing on them runs.
ARITH_CLOSE_PARSE_ERRORS = (
    "(( 1 )) ( %s )",
    "(( 1 )) {( %s )}",
    "time (( 1 )) ( %s )",
    "! (( 0 )) ( %s )",
    "if (( 1 )) { ( %s ) }",
)
# A group after the body's command word stands outside command position, and zsh read it as the pattern it is: each
# printed `b c` with files b and c present (`x b c` for the second).
ARITH_KEPT_PATTERNS = ("if (( 1 )) echo (b|c)", "if (( 1 )) echo x (b|c)", "n=0; while (( n++ < 1 )) echo (b|c)")


class ArithmeticCommandPositionTest(BashHookCase):
    """SPD-173, filed by SPD-142's engineer: mark_zsh_patterns takes a `(` that opens a word for a subshell in zsh's command
    position and for one of zsh's glob patterns outside it, and it ended an arithmetic command `(( ... ))` outside command
    position, as a command word would, where zsh stays in it.  A `( list )` right after the `))` of an arithmetic condition
    was then one pattern word, where zsh runs a subshell: SPD-142's shape after `))` instead of a closing brace.

    Probed 2026-09-22 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o
    nobareglobqual and under -f, which printed the same for every line, and in GNU bash 3.2.57, which rejects every line
    of the tables above (it has no short forms, and wants a terminator before a `then` or a `do`); an `echo` stood in for
    each command:

    - ARITH_CLOSE_SUBSHELLS ran their subshell.  zsh reads `((` as arithmetic only in command position or a for loop's
      header (`echo (( 1 )) (b|c)` failed with "no matches found: (( 1 ))", a pattern), and its `))` leaves zsh in command
      position: after an if's, elif's, while's or until's condition ending in one it read the `( list )` as the short
      form's body, and `then`, `do`, `{`, `if`, `repeat`, `for`, `while`, `case`, `time` and `!` there as the reserved
      words they are, and a second `((` as arithmetic again (ARITH_CLOSE_ARITHMETIC, which made no file 2);
    - the hole: the other reading restores a `(` that opens a word, so a plain `( git push )` in these forms was refused
      all the same, but that reading is bash's, which keeps a brace glued to a word, and a subshell glued to its `{` stayed
      a pattern in both readings.  So `( {list} )` in any of these forms, and `{( list )}` as a `{ }` body, ran their list
      in zsh while zsh's reading held one glob command word: the ticket's `if (( 1 )) ( {git push} )`, `while (( n++ < 1
      )) ( {git push} )` and `if [[ -z x ]] { true } elif (( 1 )) ( {git push} )` were silent for a member (Law 7), and a
      write there whose words hold no `/` went past the path rule (Law 5 for a member, Law 1 for Spud);
    - the friction: one whose words hold a `/` could name a file called sqlite, so the ticket's `if (( 1 )) ( cd /tmp/o )`
      was refused to members and Spud as direct access to the ledger database, and an arithmetic command after the `))`,
      read as a subshell holding `> 2`, was a write to a file named 2;
    - a `(` after an arithmetic command that is no condition -- at the start of a line (a parse error near `(`, while
      `(( 1 )) (( 1 ))` failed near its second ` 1 `, a second arithmetic command), after `time` or `!`, glued to a `{`
      -- is a parse error, and so is a short conditional's `{ }` body with nothing after its `}` (ARITH_CLOSE_PARSE_ERRORS;
      `if (( 1 )) { echo g }` failed near `}`, as `if [[ -n x ]] { git push }` does, SPD-061).  The hook reads a subshell
      in all of them, fail closed, since nothing on such a line runs;
    - a group after the body's command word stands outside command position, and zsh reads it as the pattern it is
      (ARITH_KEPT_PATTERNS).

    AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        (self.out / "o").mkdir()
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/zzone"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")
        self.m = load_spud_module()

    def analysis(self, command):
        return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path)))

    def member_payloads(self):
        """(command, the reason a member is refused for it): Law 7, Law 6, Law 5's --as, the database, and Law 1 through a
        redirection and through tee."""
        home, spud = self.home.path, self.spud_cli
        return (("git push", "Law 7"),
                ("%s ticket new --title x" % spud, "Law 6"),
                ("%s --as spud member log hi" % spud, "Law 6"),
                ("%s --as %s member log hi" % (spud, AGENT_B), "--as"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "generated"),
                ("echo x | tee ledger/tickets/SPD-001.md", "generated"))

    def spud_payloads(self):
        """Spud is never refused for git; these are the checks that do apply to him."""
        home, spud = self.home.path, self.spud_cli
        return (("%s --as %s member log hi" % (spud, AGENT_A), "--as"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "Law 1"),
                ("echo x | tee ledger/tickets/SPD-001.md", "Law 1"))

    def every_payload(self, form):
        self.assertPayloadsAnswered(form, self.member_payloads(), self.spud_payloads())

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_is_the_subshell_zsh_runs(self):
        """zsh ran each push in a subshell (`{echo <label>}` printed its label), and `cd o` there ended where it began; the
        hook read a glob command word in each, silent for the three pushes and refused to everyone for the cd."""
        for line in ("if (( 1 )) ( {git push} )", "while (( n++ < 1 )) ( {git push} )",
                     "if [[ -z x ]] { true } elif (( 1 )) ( {git push} )"):
            with self.subTest(line=line):
                marked, other = self.m.mark_zsh_patterns(line)
                self.assertEqual(marked, other)  # the subshell's `(` is punctuation in zsh's reading too
                r = self.assertRefused(line, "Law 7")
                self.assertIn("git push", r.reason)
                self.assertRefused(line, "Law 7", AGENT_C)
                self.assertSilent(line, agent_id=None)  # Law 7 refuses members only
        home = str(self.home.path)
        line = "if (( 1 )) ( cd %s/o )" % self.out
        for agent_id in (AGENT_A, AGENT_C, None):
            with self.subTest(line=line, agent_id=agent_id):
                self.assertSilent(line, agent_id)
        a = self.analysis(line)
        self.assertEqual((a.findings, a.cwds), ([], frozenset([home])))

    # -- the hole -----------------------------------------------------------------------------------------------------
    def test_zsh_reads_every_form_as_the_subshell_it_runs(self):
        for form in ARITH_CLOSE_SUBSHELLS:
            for body in ("{git push}", "git push"):
                line = form % body
                with self.subTest(line=line):
                    marked, other = self.m.mark_zsh_patterns(line)
                    self.assertEqual(marked, other)  # no `(` on the line is a pattern in zsh's reading
                    self.assertIn(("git", ("push", "push")), self.analysis(line).findings)

    def test_a_group_in_the_subshell_is_refused(self):
        """The hole: `( {git push} )` runs the push in every form, and neither reading found it while zsh's reading took
        that `(` for a pattern."""
        for form in ARITH_CLOSE_SUBSHELLS:
            # Law 7 refuses members only, naming the push
            self.assertAnsweredAs(form % "{git push}", "git push", [(AGENT_A, "Law 7: spudagents never run `git push`"),
                                                                    (AGENT_C, "Law 7"), (None, None)])

    def test_every_payload_in_a_subshell_glued_to_its_brace(self):
        """`{( list )}` stayed one pattern word in both readings, so every check on the list was lost, not Law 7's alone."""
        for form in ("if (( 1 )) {( %s )}; true", "n=0; while (( n++ < 1 )) {( %s )}",
                     "if (( 0 )) { true } elif (( 1 )) {( %s )}; true", "if (( 1 )) then {( %s )}; fi"):
            self.every_payload(form)

    def test_every_payload_in_a_group_inside_the_subshell(self):
        for form in ("if (( 1 )) ( {%s} )", "n=0; while (( n++ < 1 )) ( {%s} )", "n=1; until (( n-- < 1 )) ( {%s} )",
                     "if [[ -z x ]] { true } elif (( 1 )) ( {%s} )", "if (( 1 )) then ( {%s} ) fi"):
            self.every_payload(form)

    def test_the_path_rule_inside_the_subshell(self):
        """A write whose words hold no `/` was silent to everyone -- the path rule unchecked -- save a tee inside `( {list}
        )`, whose target bash's reading spells `note.txt}` and checked; and one whose words hold a `/` was refused as direct
        access to the database, a member's write into its own deliverables among them."""
        for form in ("if (( 1 )) {( %s )}; true", "n=0; while (( n++ < 1 )) {( %s )}",
                     "if (( 1 )) ( {%s} )", "if [[ -z x ]] { true } elif (( 1 )) ( {%s} )"):
            for write in ("echo x | tee note.txt", "touch note.txt", "rm -rf docs"):
                line = form % write
                with self.subTest(line=line):
                    self.assertRefused(line, "deliverables")  # AGENT_A plans tests/** and bin/spud
                    self.assertSilent(line, AGENT_C)
                    self.assertRefused(line, "Law 1", agent_id=None)
            line = form % "echo x | tee tests/zzone/k.py"
            with self.subTest(line=line):
                self.assertSilent(line)
                self.assertSilent(line, AGENT_C)

    # -- the friction ---------------------------------------------------------------------------------------------------
    def test_a_harmless_subshell_is_silent_and_moves_nothing(self):
        home = str(self.home.path)
        for form in ARITH_CLOSE_SUBSHELLS:
            line = form % ("cd %s/o" % self.out)
            with self.subTest(line=line):
                self.assertAnsweredAs(line, "true", [(AGENT_A, None), (AGENT_C, None), (None, None)])
                self.assertEqual(self.analysis(line).cwds, frozenset([home]))  # the subshell's cd does not carry out

    def test_an_arithmetic_command_after_the_close_writes_nothing(self):
        for form in ARITH_CLOSE_ARITHMETIC:
            line = form % "true"
            with self.subTest(line=line):
                self.assertAnsweredAs(line, "true", [(AGENT_A, None), (None, None)])
                self.assertEqual(self.analysis(line).redirects, [])
            self.assertAnsweredAs(form % "git push", "git push", [(AGENT_A, "Law 7"), (None, None)])

    # -- lines zsh rejects ----------------------------------------------------------------------------------------------
    def test_a_subshell_zsh_rejects_is_read_fail_closed(self):
        """Nothing on these lines runs; the hook reads the subshell all the same."""
        for form in ARITH_CLOSE_PARSE_ERRORS:
            line = form % "{git push}"
            with self.subTest(line=line):
                self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
                self.assertRefused(line, "Law 7")

    # -- the other direction ----------------------------------------------------------------------------------------------
    def test_a_pattern_after_the_bodys_command_word_stays_a_pattern(self):
        for line in ARITH_KEPT_PATTERNS:
            with self.subTest(line=line):
                marked, other = self.m.mark_zsh_patterns(self.m.neutralize_quoted_globs(line))  # as analyse_command marks it
                self.assertEqual(marked.count("("), 1)  # the arithmetic command's own; the group is one pattern word
                self.assertEqual(self.m.deglob(marked), line)
        # with l/t standing in for the ledger file, each of these wrote l/t in the probe
        for line in ("if (( 1 )) tee (ledger|x)/tickets/SPD-001.md", "if (( 1 )) echo x > (ledger|x)/tickets/SPD-001.md",
                     "n=0; while (( n++ < 1 )) tee -a (ledger|x)/tickets/SPD-001.md"):
            with self.subTest(line=line):
                r = self.assertRefused(line, "generated", AGENT_C)  # the pattern still expands to the ledger file
                self.assertIn("into (ledger|x)/tickets/SPD-001.md", r.reason)

    # -- the directory model --------------------------------------------------------------------------------------------
    def test_where_each_directory_goes(self):
        """The subshell's cd never carries out (probed: `if (( 1 )) ( cd o ); echo x > k.txt` made k.txt where the line
        began, and `if (( 1 )) ( cd l ); echo x > t` and its while spelling made t there, not l/t)."""
        home, out = str(self.home.path), str(self.out)
        self.assertIn(("k.txt", frozenset([home])), self.analysis("if (( 1 )) ( cd %s ); echo x > k.txt" % out).redirects)
        self.assertSilent("if (( 1 )) ( cd %s/ledger ); echo x > tickets/SPD-001.md" % home, AGENT_C)
        self.assertSilent("n=0; while (( n++ < 1 )) ( cd %s/ledger ); echo x > tickets/SPD-001.md" % home, AGENT_C)

    # -- where the form may stand -------------------------------------------------------------------------------------------
    def test_every_enclosing_text_reads_the_subshell(self):
        """The scanner reads every text the hook analyses, so the subshell is read wherever the form stands (probed: zsh ran
        `{echo <label>}` in each of these, and in `zsh -f -c` for the zsh -c line)."""
        for line in ("echo $(if (( 1 )) ( {git push} ))", "echo `if (( 1 )) ( {git push} )`",
                     "eval 'if (( 1 )) ( {git push} )'", "zsh -c 'if (( 1 )) ( {git push} )'",
                     "{ if (( 1 )) ( {git push} ) }", "( while (( n++ < 1 )) ( {git push} ) )",
                     "f() { if (( 1 )) ( {git push} ) }; f", "case x in x) if (( 1 )) ( {git push} );; esac",
                     "if true; then if (( 1 )) ( {git push} ); fi", "for f in a; do while (( n++ < 1 )) ( {git push} ); done",
                     "time if (( 1 )) ( {git push} )"):
            with self.subTest(line=line):
                self.assertRefused(line, "Law 7")
                self.assertRefused(line, "Law 7", AGENT_C)

    # -- controls ---------------------------------------------------------------------------------------------------------
    def test_a_terminator_an_operator_or_a_for_header_before_the_subshell_was_always_read(self):
        """A `;`, a newline, `&&`, `||` and `|` put the scanner in command position before this ticket too, and so did a for
        loop's arithmetic header (probed: each ran the subshell after it)."""
        for form in ("(( 1 )); ( %s )", "(( 1 ))\n( %s )", "(( 1 )) && ( %s )", "(( 0 )) || ( %s )", "(( 1 )) | ( %s )",
                     "if (( 1 )); then ( %s ); fi", "n=0; while (( n++ < 1 )); do ( %s ); done",
                     "for (( i=0; i<1; i++ )) ( %s )", "for (( i=0; i<1; i++ )) do ( %s ) done"):
            line = form % "{git push}"
            with self.subTest(line=line):
                self.assertRefused(line, "Law 7")
                self.assertRefused(line, "Law 7", AGENT_C)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        for line in ("if (( 1 )) " * 2000 + "( {git push} )",
                     "(( 1 )) " * 5000 + "( {git push} )",
                     "n=0; " + "while (( n++ < 1 )) " * 1000 + "( {git push} )",
                     "if (( 1 )) { " * 1000 + "( {git push} )" + " }" * 1000,
                     "if " + "(( 1 )) && " * 2000 + "(( 1 )) ( {git push} )",
                     "if " + "((" * 2000 + " 1 " + "))" * 2000 + " ( {git push} )"):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = self.analysis(line)
                self.assertLess(time.monotonic() - started, 5.0)
                self.assertIn(("git", ("push", "push")), a.findings, line[:40])


# SPD-174: the reserved words the scanner keeps command position after (zsh.ZSH_COMMAND_POSITION_WORDS) but `{`.  With a
# `(` glued to one, zsh reads one glob word, and with a file of that name present zsh -f ran the code of the word's
# `(e:'echo <label>':)` at the start of a line and in a then-body, for every one of these (GluedReservedWordTest).
GLUED_RESERVED_WORDS = ("if", "then", "else", "elif", "fi", "while", "until", "do", "done", "}", "!", "time", "coproc", "nocorrect")
# The places a command word stands in, `%s` the glued word: `else(e:'echo <label>':)` ran its code in every one.
GLUED_WORD_PLACES = (
    "%s",
    "true; %s",
    "true && %s",
    "false || %s",
    "echo | %s",
    "if %s; then :; fi",
    "if true; then %s; fi",
    "if true; then :; %s; fi",
    "if false; then :; else %s; fi",
    "{ %s; }",
    "( %s )",
    "x=1 %s",
    "! %s",
    "time %s",
    "nocorrect %s",
    "repeat 1 %s",
    "for f (a) %s",
    "if [[ -n x ]] %s",
    "if (( 1 )) %s",
    "> /dev/null %s",
    "case x in x) %s;; esac",
    "f() { %s }; f",
)
# The qualifier spellings whose code ran as `else(<qualifier>)` in a then-body, `%s` the code.
GLUED_QUALIFIERS = ("e:'%s':", "e{%s}", "e[%s]", "oe:'%s':", ".e:'%s':", 'e:"%s":')


class GluedReservedWordTest(BashHookCase):
    """SPD-174, filed by SPD-142's engineer: mark_zsh_patterns' docstring and ZshGlobOperatorTest said zsh runs `else(` as
    a subshell, as it runs `{(`.  It does not.  zsh reads a reserved word with a `(` glued to it as one glob word, its
    group a pattern or, with bareglobqual (zsh's default), glob qualifiers, and the code of an `e` or `+` qualifier runs
    for each file the word matches.  The scanner took every reserved word it keeps command position after, glued to a `(`
    in command position, for that word and a subshell, in both readings, so that code went unread.

    Probed 2026-09-22 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f and under -f -o
    nobareglobqual, and in GNU bash 3.2.57; an `echo <label>` stood in for each command:

    - the proposer's evidence: `if false; then :; else(echo else-glued-ran); fi` printed nothing in zsh and else-glued-ran
      in bash; `if true; then :; else(echo NOT-this); fi` failed in zsh -f -o nobareglobqual with "no matches found:
      else(echo NOT-this)", a pattern, and in zsh -f with "missing end of string", its group read as qualifiers (an `e`
      whose string, delimited by `c`, never ends); `{(echo brace-glued-top)}` ran its subshell in all three;
    - the hole: with a file named after the word present, zsh -f ran the code of `W(e:'echo <label>':)` for every word of
      GLUED_RESERVED_WORDS, at the start of a line and in a then-body, `else(e:'echo <label>':)` in every one of
      GLUED_WORD_PLACES, `else(...)` with every one of GLUED_QUALIFIERS, and `else(+f)`, `then(+f)` and `fi(+f)`,
      which ran the function f.  A write there made its file (`else(e:'echo x > l/t':)` wrote l/t), and a cd there moved
      the shell the line goes on in (`else(e:'cd d':) ; pwd` printed .../d).  zsh -f -o nobareglobqual read each group as a
      pattern and ran nothing, and bash runs the word and a subshell where the word belongs (`!(`, `if(`, `then(`,
      `else(`, `elif(`, `while(`, `until(`, `do(` and `time(` ran their subshell there) and rejects it anywhere else;
    - a second hole beside it: zsh splits every brace off a run of them opening a word in command position, so
      `{{(echo <label>)}}` ran its subshell in its two groups, at the start of a line and after `true;`, `time`, `!` and
      a `then` (and a cd there stayed there), where the scanner took only a lone `{` for a group and read one pattern word
      in both readings; bash rejects the line;
    - zsh rejects the glued word after a closing `}` (`if [[ -z x ]] { : } else(...)`, `{ : } else(...)`), as a group's
      last word (`{ :; }(...)`), in then's and do's own place (`if true; then(...); fi`, `for i in 1; do(...); done`), and
      `{{{( list )}}}` (a parse error near `}}`): nothing on those lines runs, and the hook reads them fail closed.

    The words the scanner never kept command position after -- `esac`, `always`, `case`, `for`, `select`, `repeat`,
    `foreach`, `function` -- ran their code the same way (`esac(e:'echo <label>':)` at the start of a line), and were
    already read as glob words.  zsh's reading now holds such a group in its word, where the walk reads its qualifier
    code (globbing.qualifier_code) and the word as the glob command word it is, and the other reading restores the
    parenthesis, bash's subshell, as before; a run of braces before a `(` is a run of groups and a subshell in both.
    AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        (self.out / "o").mkdir()
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/zzone"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")
        self.m = load_spud_module()

    def analysis(self, command):
        return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path)))

    def member_payloads(self):
        """(command, the reason a member is refused for it, the directory it runs in): Law 7, Law 6, Law 5's --as, and Law 1
        through a redirection and through tee -- none holding a quote, so each fits between a qualifier's quotes.  The
        writes name ledger/tickets/SPD-001.md from the home: in a glued word's group a `/` is a bad pattern to zsh (probed:
        `else(e:'echo x > sub/t':)` failed so under -f -o nobareglobqual, while -f ran the code), and since SPD-179 the
        hook's reading of the glob command word keeps that `/` in its last segment, which names no command, so the code's
        write is refused in the path rule's words.  Before it the word was split inside the group, the pattern after the
        `/` named any command, the database's first, and these writes ran in the tickets directory to hold no `/`."""
        spud = self.spud_cli
        return (("git push", "Law 7", None),
                ("%s ticket new --title x" % spud, "Law 6", None),
                ("%s --as spud member log hi" % spud, "Law 6", None),
                ("%s --as %s member log hi" % (spud, AGENT_B), "--as", None),
                ("echo x > ledger/tickets/SPD-001.md", "generated", None),
                ("echo x | tee ledger/tickets/SPD-001.md", "generated", None))

    def spud_payloads(self):
        """Spud is never refused for git; these are the checks that do apply to him."""
        spud = self.spud_cli
        return (("%s --as %s member log hi" % (spud, AGENT_A), "--as", None),
                ("echo x > ledger/tickets/SPD-001.md", "Law 1", None),
                ("echo x | tee ledger/tickets/SPD-001.md", "Law 1", None))

    def every_payload(self, form):
        for command, needle, cwd in self.member_payloads():
            self.assertAnsweredAs(form % command, command, [(AGENT_C, needle), (AGENT_A, needle)], cwd)
        for command, needle, cwd in self.spud_payloads():
            self.assertAnsweredAs(form % command, command, [(None, needle)], cwd)

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_is_one_glob_word_in_zsh(self):
        """zsh's reading holds `else` and its group as one word, and the other reading is the line as bash reads it, the
        word and a subshell; `{(` is a group and a subshell in both."""
        for line in ("if false; then :; else(echo else-glued-ran); fi", "if true; then :; else(echo NOT-this); fi"):
            with self.subTest(line=line):
                marked, other = self.m.mark_zsh_patterns(line)
                self.assertNotIn("(", marked)  # the group is part of the word
                self.assertEqual(self.m.deglob(marked), line)
                self.assertEqual(other, line)
        line = "{(echo brace-glued-top)}"
        self.assertEqual(self.m.mark_zsh_patterns(line), (line, line))
        self.assertRefused("{(git push)}", "Law 7")

    def test_the_tickets_qualifier_corner(self):
        """With a file named else present, zsh -f ran the code of `else(e:'...':)` and `else(+f)` in a then-body."""
        line = "if true; then :; else(e:'git push':); fi"
        self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
        r = self.assertRefused(line, "Law 7")
        self.assertIn("git push", r.reason)
        self.assertRefused(line, "Law 7", AGENT_C)
        self.assertSilent(line, agent_id=None)  # Law 7 refuses members only
        # ... and a write there is held to the path rule, in its own words (SPD-179: see member_payloads)
        line = "if true; then :; else(e:'echo x > ledger/tickets/SPD-001.md':); fi"
        self.assertNotIn("db", self.analysis(line).kinds)
        self.assertRefused(line, "Law 1", None)
        self.assertRefused(line, "generated", AGENT_C)
        line = "if true; then :; else(e:'echo x > k.txt':); fi"
        self.assertRefused(line, "deliverables", AGENT_A)  # AGENT_A plans tests/** and bin/spud
        self.assertSilent(line, AGENT_C)
        self.assertRefused(line, "Law 1", agent_id=None)  # a file in the home that is not Spud's own

    # -- the hole -----------------------------------------------------------------------------------------------------
    def test_every_glued_word_reads_its_qualifier_code(self):
        # every word the scanner keeps command position after but `{`: a word added there is probed and added here
        self.assertEqual(set(GLUED_RESERVED_WORDS), self.m.ZSH_COMMAND_POSITION_WORDS - {"{"})
        for word in GLUED_RESERVED_WORDS:
            for form in ("%s", "if true; then :; %s; fi"):
                line = form % ("%s(e:'git push':)" % word)
                with self.subTest(line=line):
                    marked, other = self.m.mark_zsh_patterns(line)
                    self.assertNotIn("(", marked)  # zsh's reading: one glob word
                    self.assertEqual(self.m.deglob(marked), line)
                    self.assertEqual(other, line)  # the other reading: bash's, the word and a subshell
                    r = self.assertRefused(line, "Law 7")
                    self.assertIn("git push", r.reason)
                    self.assertRefused(line, "Law 7", AGENT_C)

    def test_every_place_reads_the_qualifier_code(self):
        for place in GLUED_WORD_PLACES:
            line = place % "else(e:'git push':)"
            with self.subTest(line=line):
                r = self.assertRefused(line, "Law 7")
                self.assertIn("git push", r.reason)
                self.assertRefused(line, "Law 7", AGENT_C)

    def test_every_qualifier_spelling_reads_its_code(self):
        for qualifier in GLUED_QUALIFIERS:
            line = "if true; then :; else(%s); fi" % (qualifier % "git push")
            with self.subTest(line=line):
                self.assertRefused(line, "Law 7")
                self.assertRefused(line, "Law 7", AGENT_C)

    def test_every_payload_in_the_code(self):
        """zsh -f ran the code of each form with `echo <label>` in the slot (`x=1 do(` and `true && fi(` too)."""
        for form in ("if true; then :; else(e:'%s':); fi", "then(e:'%s':)", "time(e:'%s':)", "!(e:'%s':)", "x=1 do(e:'%s':)",
                     "{ :; }(e:'%s':); }", "true && fi(e:'%s':)"):
            self.every_payload(form)

    def test_a_cd_in_the_code_is_not_followed(self):
        """zsh runs the code in the shell that expands the word, once for every file it matches, so the directory after it
        is one the hook cannot follow, as after any qualifier's cd (probed in zsh -f: `else(e:'cd d':) ; pwd` printed .../d,
        `then(e:'cd e':); echo x > g` made e/g, `if true; then :; else(e:'cd d':); echo x > h; fi` made d/h, and
        `time(e:'cd d':) > f` made d/f, the word expanded before its redirection opened)."""
        for line in ("else(e:'cd ledger':); echo x > tickets/SPD-001.md",
                     "if true; then :; else(e:'cd ledger':); echo x > tickets/SPD-001.md; fi",
                     "time(e:'cd ledger':) > tickets/SPD-001.md"):
            with self.subTest(line=line):
                self.assertRefused(line, "cannot follow", AGENT_C)

    # -- a run of braces --------------------------------------------------------------------------------------------------
    def test_a_run_of_braces_opens_its_groups_and_the_subshell(self):
        for line in ("{{(git push)}}", "true; {{(git push)}}", "time {{(git push)}}", "! {{(git push)}}",
                     "if true; then {{(git push)}}; fi"):
            with self.subTest(line=line):
                self.assertEqual(self.m.mark_zsh_patterns(line), (line, line))  # no pattern in either reading
                r = self.assertRefused(line, "Law 7")
                self.assertIn("git push", r.reason)
                self.assertRefused(line, "Law 7", AGENT_C)
                self.assertSilent(line, agent_id=None)
        self.every_payload("{{(%s)}}")
        self.every_payload("true; {{(%s)}}")

    def test_a_cd_in_the_braced_subshell_stays_there(self):
        home = str(self.home.path)
        line = "{{(cd %s/o)}}" % self.out
        for agent_id in (AGENT_A, AGENT_C, None):
            with self.subTest(agent_id=agent_id):
                self.assertSilent(line, agent_id)
        self.assertEqual(self.analysis(line).cwds, frozenset([home]))
        self.assertSilent("{{(cd %s/ledger)}}; echo x > tests/zzone/k.py" % home, AGENT_C)

    # -- lines zsh rejects ----------------------------------------------------------------------------------------------
    def test_a_glued_word_zsh_rejects_is_read_fail_closed(self):
        """Nothing on these lines runs in zsh; the hook reads the glued word's code all the same, and the subshell bash runs
        after then and do."""
        for line in ("if [[ -z x ]] { : } else(e:'git push':)", "{ : } else(e:'git push':)", "{ :; }(e:'git push':)",
                     "if true; then(e:'git push':); fi", "for i in 1; do(e:'git push':); done", "{{{(git push)}}}"):
            with self.subTest(line=line):
                self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
                self.assertRefused(line, "Law 7")

    # -- the other reading ------------------------------------------------------------------------------------------------
    def test_bashs_subshell_is_still_read(self):
        """bash ran the word and a subshell for each of these, `echo <label>` for the push, and zsh ran none of them, reading
        one glob word (the if, then, do, while and until lines were parse errors near fi or done): both readings are
        checked."""
        for line in ("if false; then :; else(git push); fi", "if true; then(git push); fi", "for i in 1; do(git push); done",
                     "if(git push) then :; fi", "time(git push)", "!(git push)", "if false; then :; elif(git push) then :; fi",
                     "while(git push) do break; done", "until(git push) do :; done"):
            with self.subTest(line=line):
                marked, other = self.m.mark_zsh_patterns(line)
                self.assertEqual(other, line)
                self.assertNotIn("(", marked)
                self.assertRefused(line, "Law 7")
                self.assertRefused(line, "Law 7", AGENT_C)

    # -- controls -----------------------------------------------------------------------------------------------------------
    def test_a_glued_word_already_read_as_one_still_is(self):
        """Outside command position, and after a reserved word the scanner never kept command position after, a glued group
        was always read as part of its word (probed: `esac(e:'echo <label>':)` ran its code at the start of a line, and
        `always(`, `case(`, `for(`, `select(`, `repeat(`, `foreach(` and `function(` too)."""
        for line in ("echo else(e:'git push':)", "esac(e:'git push':)", "always(e:'git push':)", "case(e:'git push':)",
                     "for(e:'git push':)", "select(e:'git push':)", "repeat(e:'git push':)", "foreach(e:'git push':)",
                     "function(e:'git push':)"):
            with self.subTest(line=line):
                self.assertRefused(line, "Law 7")

    def test_the_line_after_a_glued_word_is_read_as_before(self):
        """A parameter expansion and a group after the glued word are read as they always were."""
        for line in ("else(e:'true':); x=git; ${x} push", "time(e:'true':) && x=git && ${x} push",
                     "!(e:'true':); { git push }", "{{(true)}}; x=git; ${x} push", "if true; then :; fi(N); x=git; ${x} push"):
            with self.subTest(line=line):
                self.assertRefused(line, "Law 7")

    def test_harmless_lines_stay_silent(self):
        for line in ("if false; then :; else(true); fi", "if false; then :; else (true); fi", "time (true)", "! (true)",
                     "{ (true) }", "{(true)}", "{{(true)}}", "if true; then :; else(N); fi", "echo else(e:'true':)"):
            with self.subTest(line=line):
                for agent_id in (AGENT_A, AGENT_C, None):
                    self.assertSilent(line, agent_id)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        for line in ("if true; then :; " + "else(e:'true':); " * 2000 + "else(e:'git push':); fi",
                     "else(" * 20000 + "; then(e:'git push':)",
                     "{" * 2000 + "(git push)" + "}" * 2000,
                     "{" * 2000 + "a" + "(b)" * 20000 + "; time(e:'git push':)",
                     "true; " + "!(e:'true':) " * 1000 + "&& then(e:'git push':)"):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = self.analysis(line)
                self.assertLess(time.monotonic() - started, 5.0)
                self.assertIn(("git", ("push", "push")), a.findings, line[:40])


# SPD-178: a `for (( ... ))` header where it may stand, `%s` its short body.  With l/t present, zsh 5.9 (-f, and -f -o
# nobareglobqual) wrote l/t for `echo <label> | ` and each of these with `tee (l|x)/t` in the slot (ForArithmeticBodyTest).
FOR_ARITH_HEADERS = (
    "for (( i=0; i<1; i++ )) %s",
    "for ((i=0; i<1; i++)) %s",
    "for (( i=0; i<1; i++ )) for (( j=0; j<1; j++ )) %s",
    "repeat 1 for (( i=0; i<1; i++ )) %s",
    "if true; then for (( i=0; i<1; i++ )) %s; fi",
    "f() { for (( i=0; i<1; i++ )) %s }; f",
    "for (( i=0; i<1; i++ )) if (( 1 )) %s",
    "for (( i=0; i<1; i++ )) { %s }",
)
# A short body writing through a group right after its command word, `%s` the target: with l/t present and `(l|x)/t` in
# the slot, zsh wrote, appended to, touched or removed l/t after the first header for each.
FOR_ARITH_WRITERS = ("tee %s", "tee -a %s", "echo x > %s", "touch %s", "rm %s")


class ForArithmeticBodyTest(BashHookCase):
    """SPD-178, filed by SPD-173's engineer: mark_zsh_patterns reads `for` and `select` as a loop whose name comes next and
    whose `for name ( word ... )` list may follow that name, and its arithmetic branch cleared none of it.  After a `for ((
    ... ))` header the short body's first word was taken for the loop's name, and a `(` opening the word after it for the
    word list: the glob pattern zsh expands there was read as a subshell, and the rest of its word as a command.  The target
    of a tee or another writer into the pattern was lost -- Law 1 unchecked for Spud, and members refused for a path the
    line never writes -- and the code of a glob qualifier after the group went unread (Law 7 and the path rule).

    Probed 2026-09-22 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, which printed the same for every line but the qualifier's, and in GNU bash 3.2.57, which rejects every
    short form, near its body's first word:

    - the proposer's evidence: with l/t present, `echo new | for (( i=0; i<1; i++ )) tee (l|x)/t` wrote new to l/t, and `for
      (( i=0; i<1; i++ )) echo (b|c)` printed `b c`, as the long form `do echo (b|c); done` did.  The hook on the SPD-173
      tree read the ticket's two tee lines as a tee into /tickets/SPD-001.md: silent for Spud, and refused to a member as a
      path outside every registered project;
    - every header of FOR_ARITH_HEADERS wrote l/t with `tee (l|x)/t` as its body, and so did the first in eval, in `$( ...
      )` and in `zsh -f -c`; after it each of FOR_ARITH_WRITERS wrote, appended to, touched or removed l/t, and `cat
      (l|x)/t` printed it;
    - the qualifier: with files b and c present, zsh -f ran the code of `for (( i=0; i<1; i++ )) ls (b|c)(e:'echo
      QRAN-$REPLY':)` once for each, and `(e:'echo q > w':)` wrote w; zsh -f -o nobareglobqual read a second pattern and
      found no match;
    - the two other states the arithmetic branch now clears with the loop's name are never pending there on a line zsh runs:
      `((` right after `repeat` opens its count word, not an arithmetic command (`repeat (( 1+1 )) echo rep` printed rep
      twice, and `echo rt | repeat (( 1 )) tee (l|x)/t` wrote l/t, read so before this ticket), and right after a closing
      brace it is a parse error (`{ echo a } (( 1 ))`, `{ echo a } (( 1 )) always { echo b }`, `if [[ -n x ]] { echo a }
      (( 1 ))` and `{ echo a } always { echo b } (( 1 ))` each failed near ` 1 `);
    - `select` has no arithmetic form: `select (( 1 )) tee (l|x)/t` and `select (( 1 )) git push` failed near `(( 1 ))`,
      and `select (( i=0; i<1; i++ )) tee (l|x)/t` near `(( i=0`.  Nothing on those lines runs; the hook reads the body
      after the `))` all the same, fail closed.

    AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    TARGET = "(ledger|x)/tickets/SPD-001.md"

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/zzone"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")
        self.m = load_spud_module()

    def analysis(self, command):
        return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path)))

    def refused_everywhere(self, line):
        """Refused to both members for the ledger file, named as the line spells it, and to Spud on Law 1."""
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(line=line, agent_id=agent_id):
                r = self.assertRefused(line, "generated", agent_id)
                self.assertIn(self.TARGET, r.reason)
        with self.subTest(line=line, agent_id="spud"):
            self.assertRefused(line, "Law 1", agent_id=None)

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_reads_the_pattern_zsh_expands(self):
        for line in ("for (( i=0; i<1; i++ )) tee %s" % self.TARGET, "echo x | for (( i=0; i<1; i++ )) tee %s" % self.TARGET):
            with self.subTest(line=line):
                marked, _other = self.m.mark_zsh_patterns(line)
                self.assertEqual(marked.count("("), 1)  # the header's own: the group is part of the tee's word
                self.assertEqual(self.m.deglob(marked), line)
                self.assertIn(self.TARGET, [self.m.deglob(t) for t, _c in self.analysis(line).redirects])
            self.refused_everywhere(line)
        # the ticket's controls, refused so before it
        for line in ("for (( i=0; i<1; i++ )) echo x > %s" % self.TARGET, "for i in 1; do tee %s; done" % self.TARGET):
            self.refused_everywhere(line)

    # -- the hole -----------------------------------------------------------------------------------------------------
    def test_every_header_reads_its_body(self):
        for header in FOR_ARITH_HEADERS:
            self.refused_everywhere(header % ("tee %s" % self.TARGET))

    def test_every_enclosing_text_reads_its_body(self):
        for line in ("eval 'for (( i=0; i<1; i++ )) tee %s'", "x=$(for (( i=0; i<1; i++ )) tee %s)",
                     "zsh -f -c 'for (( i=0; i<1; i++ )) tee %s'"):
            self.refused_everywhere(line % self.TARGET)

    def test_every_writer_reads_its_target(self):
        for writer in FOR_ARITH_WRITERS:
            self.refused_everywhere("for (( i=0; i<1; i++ )) " + writer % self.TARGET)

    def test_the_qualifier_code_is_read(self):
        line = "for (( i=0; i<1; i++ )) ls (b|c)(e:'git push':)"
        self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
        r = self.assertRefused(line, "Law 7")
        self.assertIn("git push", r.reason)
        self.assertRefused(line, "Law 7", AGENT_C)
        self.assertSilent(line, agent_id=None)  # Law 7 refuses members only
        # ... and a write there is held to the path rule, from the home as GluedReservedWordTest runs its own since SPD-179
        line = "for (( i=0; i<1; i++ )) ls (b|c)(e:'echo x > ledger/tickets/SPD-001.md':)"
        self.assertNotIn("db", self.analysis(line).kinds)
        self.assertRefused(line, "Law 1", None)
        self.assertRefused(line, "generated", AGENT_C)
        self.assertRefused(line, "generated", AGENT_A)

    def test_the_body_reads_as_the_command_does_alone(self):
        """The loop runs its body, and its header changes none of the body's words: every finding, redirection target and
        written operand the body has on its own line, it has after each header."""
        for body in [w % self.TARGET for w in FOR_ARITH_WRITERS] + ["cat %s" % self.TARGET, "echo (b|c)",
                                                                    "ls (b|c)(e:'git push':)"]:
            alone = self.analysis(body)
            for header in FOR_ARITH_HEADERS[:6]:
                line = header % body
                with self.subTest(line=line):
                    looped = self.analysis(line)
                    self.assertEqual(looped.findings, alone.findings)
                    self.assertEqual([t for t, _c in looped.redirects], [t for t, _c in alone.redirects])
                    self.assertEqual([w[1] for w in looped.arg_writes], [w[1] for w in alone.arg_writes])

    # -- lines zsh rejects --------------------------------------------------------------------------------------------
    def test_a_select_with_an_arithmetic_header_is_read_fail_closed(self):
        """zsh rejects each of these lines at its `((`, so nothing on them runs; the hook reads the body after the `))`."""
        for line in ("select (( 1 )) tee %s" % self.TARGET, "select (( i=0; i<1; i++ )) tee %s" % self.TARGET):
            self.refused_everywhere(line)
        self.assertRefused("select (( 1 )) git push", "Law 7")

    # -- controls -----------------------------------------------------------------------------------------------------
    def test_a_loop_name_and_word_list_are_still_read(self):
        """A `for name ( word ... )` list is still a list, never a glob, with a command after it (ZshShortLoopTest), and a
        group after the body's command word is a pattern there too (probed: `for f (a) echo (b|c)` printed `b c`, and `echo
        fa | for f (a) tee (l|x)/t` wrote l/t); `repeat (( 1 ))` counts once, its body a body (`repeat (( 1 )) ( echo
        rsub )` and `repeat (( 1 )) echo rlist` each printed their label once)."""
        for line in ("for f (a b) (git push)", "for f (a b) git push",
                     "for (( i=0; i<1; i++ )) ( git push )", "for (( i=0; i<1; i++ )) do git push; done",
                     "for (( i=0; i<1; i++ )) { git push }", "for (( i=0; i<1; i++ )); git push",
                     "repeat (( 1 )) git push", "repeat (( 1 )) ( git push )"):
            with self.subTest(line=line):
                self.assertRefused(line, "Law 7")
                self.assertRefused(line, "Law 7", AGENT_C)
        for line in ("for f (a) tee %s" % self.TARGET, "repeat (( 1 )) tee %s" % self.TARGET,
                     "for (( i=0; i<1; i++ )) tee -a %s" % self.TARGET):
            self.refused_everywhere(line)
        for line in ("for (( i=0; i<1; i++ )) echo (b|c)", "for f (a) echo (b|c)"):
            with self.subTest(line=line):
                self.assertSilent(line, AGENT_C)
                self.assertSilent(line, agent_id=None)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        for line in ("for (( i=0; i<1; i++ )) " * 2000 + "tee %s" % self.TARGET,
                     "echo x | " + "repeat 1 for (( i=0; i<1; i++ )) " * 1000 + "tee %s" % self.TARGET,
                     "for (( i=0; i<1; i++ )) " + "tee (a|b) " * 5000 + self.TARGET):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = self.analysis(line)
                self.assertLess(time.monotonic() - started, 5.0)
                self.assertIn(self.TARGET, [self.m.deglob(t) for t, _c in a.redirects])


# SPD-180: zsh's foreach, `%s` its body.  FOREACH_HEADERS end their loop with `end`, the body a list of any length that
# starts right after the header -- on its line, after a terminator or after a newline; FOREACH_CLOSED take a `{ list }` or
# a `do ... done` body, which ends the loop with no `end`.  FOREACH_BODIES are body forms, each in `foreach f (a b) %s;
# end`, and FOREACH_ENCLOSED the places a foreach may stand.  With `echo <label> >> ran.log` in the slot, every one of the
# 51 lines logged its label in zsh 5.9 -f and -f -o nobareglobqual (tests/probes/shell_probe.py, 2026-09-23), and bash
# 3.2.57 rejected the first near its `(`.
FOREACH_HEADERS = (
    "foreach f (a b) %s; end",
    "foreach f ( a b ) %s; end",
    "foreach f (a b)%s; end",
    "foreach f (*) %s; end",
    "foreach f () %s; end",
    "foreach a b (1 2) %s; end",
    "foreach f (a b) %s\nend",
    "foreach f (a b); %s; end",
    "foreach f (a b)\n%s\nend",
    "foreach f in a b; %s; end",
    "foreach f\nin a b; %s; end",
    "foreach f; %s; end",
    "foreach f g; %s; end",
)
FOREACH_CLOSED = (
    "foreach f (a b) { %s }",
    "foreach f (a b) {%s}",
    "foreach f (a b); { %s }",
    "foreach f (a b)\n{ %s }",
    "foreach f (a b) do %s; done",
    "foreach f (a b); do %s; done",
    "foreach f in a b; do %s; done",
    "foreach f in a b; { %s }",
    "foreach f do %s; done",
    "foreach f { %s }",
    "foreach f g { %s }",
)
FOREACH_BODIES = ("%s", "( %s )", "( {%s} )", "true; %s", "true && %s", "false || %s", "%s | cat", "true; { %s }",
                  "if true; then %s; fi", "case x in x) %s;; esac", "repeat 1 %s", "for g (c) %s")
FOREACH_ENCLOSED = ("eval 'foreach f (a) %s; end'", "x=$(foreach f (a) %s; end)", "zsh -f -c 'foreach f (a) %s; end'",
                    "fn() { foreach f (a) %s; end }; fn", "if true; then foreach f (a) %s; end; fi",
                    "foreach f (a) foreach g (b) %s; end; end", "foreach f (a) true; foreach g (b) %s; end; true; end",
                    "{ foreach f (a) %s; end }", "{ foreach f (a) %s; end}", "repeat 1 foreach f (a) %s; end",
                    "time foreach f (a) %s; end", "! foreach f (a) %s; end", "coproc foreach f (a) %s; end",
                    "echo x | foreach f (a) %s; end", "case x in x) foreach f (a) %s; end;; esac")


class ForeachBodyTest(BashHookCase):
    """SPD-180, filed by SPD-178's engineer: ShellWalk opened no loop for zsh's `foreach`, and mark_zsh_patterns read its
    `( word ... )` list as a glob word, so a foreach whose body starts right after that list, on its line, was one command
    named foreach and everything up to the next terminator its arguments.  A git write there was silent for a member (Law 7),
    a redirection or a tee into a generated file recorded nothing (Law 1 for Spud, Law 5 for a member), `rm -rf docs` no
    argument write, and a cd there was never followed.  SPD-042's `foreach f (a b); ...; end`, with a terminator after the
    list, read its body as the commands after a `foreach` command, and still does, now as the loop's.

    Probed 2026-09-22 (the proposer) and 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0)
    under -f -o nobareglobqual and under -f, which printed the same for every line but the qualifier's, and in GNU bash
    3.2.57, which has no foreach and rejects each of these lines near its `(`, or runs `foreach` and `end` as programs it
    cannot find:

    - the proposer's evidence: `foreach f (a b) echo fe-ran-$f; end` printed fe-ran-a and fe-ran-b; with l/t present `echo ft
      | foreach f (a) tee (l|x)/t > /dev/null; end` and `foreach f (a) echo fr > (l|x)/t; end` wrote l/t; `foreach f (a) (
      {echo fe-sub} ); end` ran the subshell; `foreach f (a) cd d; end; pwd` ended in d;
    - the body: after the header, zsh's parser (par_for, the csh form) reads a `do ... done` or a `{ list }` as the body, which
      ends the loop with no `end` (`foreach f (a) { echo x }; end` failed near `end`), and anything else as a list of any
      length up to an `end` in command position -- several sublists, pipelines, and-or lists, a `( list )` subshell, compound
      commands with their own closers (`foreach f (a b) echo s1-$f; echo s2-$f; end` printed both for each word).  That `end`
      closes after a `}`, a subshell's `)` and an `esac` (`foreach f (a) echo x3; { echo y3 } end` ran), not after a `fi` or
      a `done` (a parse error), and zsh splits it off a glued `}` (`{ foreach f (a) echo gc-$f; end}` ran);
    - the header: one or more names (`foreach a b (1 2 3 4) echo two-$a$b; end` printed two-12 two-34), then `( word ... )`,
      `in word ... TERM` or neither, the positional parameters (`foreach f; echo pos-$f; end`, `foreach f do ...; done`,
      `foreach f { ... }`).  The list may hold a glob (`foreach f (*.txt)` listed a.txt b.txt) but no zsh group (`foreach f
      (a|b)` failed near `|`); `foreach f in a b echo x; end` ran nothing, its list reaching the `;`; and there is no
      arithmetic form (`foreach (( i=0; i<1; i++ ))` failed near it).  `foreach f () echo empty-$f; end` ran its body once
      for each positional parameter;
    - the loop runs in the shell itself: a cd in the body moved it, a second turn's `cd d` failed from inside d, and a
      subshell body's did not carry out;
    - the qualifier: with a directory l present, zsh -f ran the `e:` code of `foreach f (a) ls (l|x)(e:<code>:); end`
      once, for l (it printed QRAN-l); zsh -f -o nobareglobqual read a second pattern and found no match.

    AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    TARGET = "(ledger|x)/tickets/SPD-001.md"

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/zzone"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")
        self.m = load_spud_module()

    def analysis(self, command):
        return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path)))

    def member_payloads(self):
        """(command, the reason a member is refused for it): Law 7, Law 6, Law 5's --as, the database, and Law 1 through a
        redirection and through tee."""
        home, spud = self.home.path, self.spud_cli
        return (("git push", "Law 7"),
                ("%s ticket new --title x" % spud, "Law 6"),
                ("%s --as spud member log hi" % spud, "Law 6"),
                ("%s --as %s member log hi" % (spud, AGENT_B), "--as"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "generated"),
                ("echo x | tee ledger/tickets/SPD-001.md", "generated"))

    def spud_payloads(self):
        """Spud is never refused for git; these are the checks that do apply to him."""
        home, spud = self.home.path, self.spud_cli
        return (("%s --as %s member log hi" % (spud, AGENT_A), "--as"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "Law 1"),
                ("echo x | tee ledger/tickets/SPD-001.md", "Law 1"))

    def every_payload(self, form):
        self.assertPayloadsAnswered(form, self.member_payloads(), self.spud_payloads())

    def law_7(self, line):
        """A member is refused the push, and the analysis finds it; Spud is never refused git."""
        with self.subTest(line=line):
            self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
            self.assertRefused(line, "Law 7")
            self.assertRefused(line, "Law 7", AGENT_C)
            self.assertSilent(line, agent_id=None)

    def refused_everywhere(self, line, target="ledger/tickets/SPD-001.md"):
        """Refused to both members for the ledger file, named as the line spells it, and to Spud on Law 1."""
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(line=line, agent_id=agent_id):
                r = self.assertRefused(line, "generated", agent_id)
                self.assertIn(target, r.reason)
        with self.subTest(line=line, agent_id="spud"):
            self.assertRefused(line, "Law 1", agent_id=None)

    def path_rule(self, line):
        """`rm -rf docs`, a tee and a touch with no `/` in their words: refused to AGENT_A, whose deliverables are tests/**
        and bin/spud, allowed to AGENT_C, and refused to Spud, whose own files these are not."""
        with self.subTest(line=line):
            self.assertRefused(line, "deliverables")
            self.assertSilent(line, AGENT_C)
            self.assertRefused(line, "Law 1", agent_id=None)

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_is_read_as_the_loop_zsh_runs(self):
        self.law_7("foreach f (a) git push; end")
        self.law_7("foreach f (a) ( {git push} ); end")
        self.refused_everywhere("foreach f (a) tee ledger/tickets/SPD-001.md; end")
        self.path_rule("foreach f (a) rm -rf docs; end")
        self.assertIn("docs", [w[1] for w in self.analysis("foreach f (a) rm -rf docs; end").arg_writes])
        # the proposer's probes, the pattern zsh expands after the body's command word kept in its word and the list's
        # own `(` left as it is
        for line in ("echo x | foreach f (a) tee %s > /dev/null; end" % self.TARGET,
                     "foreach f (a) echo x > %s; end" % self.TARGET):
            with self.subTest(line=line):
                marked, _other = self.m.mark_zsh_patterns(line)
                self.assertEqual(marked.count("("), 1)
                self.assertEqual(self.m.deglob(marked), line)
                self.assertIn(self.TARGET, [self.m.deglob(t) for t, _c in self.analysis(line).redirects])
            self.refused_everywhere(line, self.TARGET)
        # `foreach f (a) cd /tmp; end; echo x > k.txt` checked k.txt only where the line began
        home, out = str(self.home.path), str(self.out)
        self.assertEqual(self.analysis("foreach f (a) cd %s; end" % out).cwds, frozenset([home, out]))
        self.assertRefused("foreach f (a) cd %s/ledger; end; echo x > tickets/SPD-001.md" % home, "generated", AGENT_C)

    # -- the hole -----------------------------------------------------------------------------------------------------
    def test_every_header_reads_its_body(self):
        for header in FOREACH_HEADERS + FOREACH_CLOSED:
            self.law_7(header % "git push")

    def test_every_body_form_reads_its_commands(self):
        for header in ("foreach f (a b) %s; end", "foreach f in a b; %s; end", "foreach a b (1 2) %s; end"):
            for body in FOREACH_BODIES:
                self.law_7(header % (body % "git push"))

    def test_every_enclosing_text_reads_its_body(self):
        for form in FOREACH_ENCLOSED:
            self.law_7(form % "git push")
            self.refused_everywhere(form % ("tee %s" % self.TARGET), self.TARGET)

    def test_a_word_after_the_names_that_is_no_name_opens_the_body(self):
        """zsh reads the word after each of a foreach's names in command position, so a reserved word or a redirection there
        ends the names and opens the body of a loop over the positional parameters, while another identifier is one more
        name (syntax.ZSH_RESERVED_WORDS and ShellWalk.names_end have the probes; with `set -- p`, `foreach f repeat 1
        echo rp-$f; end`, `foreach f nocorrect ...`, `foreach f time ...`, `foreach f ! ...` and `foreach f foreach g (b)
        ...; end; end` ran theirs too).  A `(` after a `{` or a `do` there opens a subshell (`foreach f {(echo gsub-$f)}`,
        `foreach f do (echo dsub-$f); done` and `foreach f g { (echo bsub-$f) }` ran it), and a group after the body's
        command word is a pattern (with l/t present, `echo pd | foreach f do tee (l|x)/t > /dev/null; done` wrote pd to
        l/t, and `foreach f typeset -f > (l|x)/t; end` emptied it)."""
        for line in ("foreach f repeat 1 git push; end", "foreach f time git push; end", "foreach f ! git push; end",
                     "foreach f nocorrect git push; end", "foreach f foreach g (b) git push; end; end",
                     "foreach f g if true; then git push; fi; end", "foreach f [[ -n x ]] && git push; end",
                     "foreach f case x in x) git push;; esac; end", "foreach f export X=1; git push; end",
                     "foreach f {(git push)}", "foreach f do (git push); done", "foreach f g { (git push) }"):
            self.law_7(line)
        self.refused_everywhere("foreach f typeset -f > %s; end" % self.TARGET, self.TARGET)
        self.refused_everywhere("echo x | foreach f do tee %s > /dev/null; done" % self.TARGET, self.TARGET)
        self.path_rule("foreach f repeat 1 rm -rf docs; end")
        # a redirection ends the names too: its null command's target, and the command after it (with `set -- p`,
        # `foreach f 2> o3 echo y3; end` printed y3 and made o3)
        self.refused_everywhere("foreach f > ledger/tickets/SPD-001.md; end")
        self.refused_everywhere("foreach f g > %s echo x; end" % self.TARGET, self.TARGET)
        self.refused_everywhere("foreach f 2> ledger/tickets/SPD-001.md git status; end")
        # ... and an identifier is one more name, nothing that runs
        for line in ("foreach f git push; end", "foreach f g h; end"):
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).findings, [])
                self.assertSilent(line)
                self.assertSilent(line, AGENT_C)

    def test_every_payload_for_every_caller(self):
        for form in ("foreach f (a b) %s; end", "foreach f (a b) ( %s ); end", "foreach f (a b) ( {%s} ); end",
                     "foreach f (a b) true; %s; end", "foreach f (a b) { %s }", "foreach f (a b) do %s; done",
                     "foreach f in a b; %s; end", "foreach a b (1 2) %s; end", "foreach f do %s; done", "foreach f { %s }"):
            self.every_payload(form)

    def test_the_path_rule_in_the_body(self):
        for form in ("foreach f (a b) %s; end", "foreach f (a b) ( {%s} ); end", "foreach f (a b) {%s}",
                     "foreach a b (1 2) true; %s; end"):
            for write in ("echo x | tee note.txt", "touch note.txt", "rm -rf docs"):
                self.path_rule(form % write)
            line = form % "echo x | tee tests/zzone/k.py"
            with self.subTest(line=line):
                self.assertSilent(line)
                self.assertSilent(line, AGENT_C)

    def test_the_qualifier_code_is_read(self):
        line = "foreach f (a) ls (b|c)(e:'git push':); end"
        self.law_7(line)
        tickets = str(self.home.path / "ledger" / "tickets")
        line = "foreach f (a) ls (b|c)(e:'echo x > SPD-001.md':); end"
        self.assertRefused(line, "Law 1", None, tickets)
        self.assertRefused(line, "generated", AGENT_C, tickets)

    def test_the_loop_variable_is_doubted(self):
        """The names take each word of the list in turn, so a command word built from one is refused."""
        for line in ("foreach X (git) $X push; end", "foreach X in git; $X push; end", "X=ls; foreach X (git) $X push; end",
                     "foreach X (git) { $X push }", "foreach Y X (a git) $X push; end", "foreach X; $X push; end"):
            with self.subTest(line=line):
                self.assertRefused(line, "cannot resolve")
                self.assertRefused(line, "cannot resolve", AGENT_C)

    # -- the loop model -----------------------------------------------------------------------------------------------
    def test_a_cd_in_the_body_is_read_as_a_loops(self):
        """A relative cd may repeat, so it is unfollowable; an absolute one leaves the union of before and after; a
        subshell's does not carry out; the list runs to `end`, so a cd after a `;` inside it is the loop's too; and a cd after
        the `end` is outside the loop and followed."""
        home, out = self.home.path, self.out
        for form in ("foreach f (a b) %s; end", "foreach f (a b) true; %s; end", "foreach f (a b) { %s }",
                     "foreach f (a b) do %s; done", "foreach f in a b; %s; end", "foreach a b (1 2) %s; end"):
            with self.subTest(form=form):
                self.assertRefused((form % "cd docs") + "; echo x > note.txt", "cannot follow", AGENT_C)
                self.assertRefused((form % ("cd %s/ledger" % home)) + "; echo x > tickets/SPD-001.md", "generated", AGENT_C)
                self.assertEqual(self.analysis(form % ("cd %s" % out)).cwds, frozenset([str(home), str(out)]))
        self.assertSilent("foreach f (a b) (cd %s/ledger); end; echo x > tickets/SPD-001.md" % home, AGENT_C)
        self.assertEqual(self.analysis("foreach f (a b) (cd %s); end" % out).cwds, frozenset([str(home)]))
        self.assertSilent("foreach f (a b) true; end; cd %s; echo x > note.txt" % out)
        self.assertRefused("foreach f (a b) true; end; cd %s/ledger; echo x > tickets/SPD-001.md" % home, "generated", AGENT_C)

    def test_the_list_runs_to_end(self):
        """Every sublist before the `end` is the loop's body, and the loop's own redirection stands after it."""
        m = self.m
        a = self.analysis("foreach f (a b) echo a > ledger/tickets/SPD-001.md; echo b > tests/zzone/k.py; end")
        self.assertEqual([m.deglob(t) for t, _c in a.redirects], ["ledger/tickets/SPD-001.md", "tests/zzone/k.py"])
        self.refused_everywhere("foreach f (a) echo x; end > ledger/tickets/SPD-001.md")
        self.refused_everywhere("foreach f (a) echo x; end >> ledger/tickets/SPD-001.md; echo y")

    def test_the_body_reads_as_the_command_does_alone(self):
        """The loop runs its body, and its header changes none of the body's words: every finding, redirection target and
        written operand the body has on its own line, it has after each header."""
        for body in [w % self.TARGET for w in FOR_ARITH_WRITERS] + ["cat %s" % self.TARGET, "echo (b|c)",
                                                                    "ls (b|c)(e:'git push':)", "rm -rf docs"]:
            alone = self.analysis(body)
            for header in FOREACH_HEADERS[:7] + ("foreach f (a b) { %s }", "foreach f (a b) do %s; done"):
                line = header % body
                with self.subTest(line=line):
                    looped = self.analysis(line)
                    self.assertEqual(looped.findings, alone.findings)
                    self.assertEqual([t for t, _c in looped.redirects], [t for t, _c in alone.redirects])
                    self.assertEqual([w[1] for w in looped.arg_writes], [w[1] for w in alone.arg_writes])

    # -- controls -----------------------------------------------------------------------------------------------------
    def test_end_closes_only_a_foreach(self):
        """An `end` is the foreach's closer only in command position with a foreach open; elsewhere it is the word it was."""
        for line in ("foreach f (a b); git push; end", "end; git push", "end && git push", "foreach f (a) echo end; git push; end",
                     "foreach f (a) true; end; git push", "foreach f (a) true; end && git push", "{ true; end}; git push",
                     "foreach f (a) true; end; end; git push", "for f (a) true; end; git push",
                     "foreach f (a) if true; then true; fi; end; git push"):
            self.law_7(line)
        for ok in ("echo 'foreach f (a) git push; end'", "echo foreach f end", "grep -n foreach tests/zzone/k.py",
                   "foreach f (a b) echo $f; end", "foreach f (a b) echo x > tests/zzone/k.py; end", "end",
                   "foreach f (a b) echo (b|c); end", "foreach f (a b) { echo $f }; echo done"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, AGENT_C)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        for line in ("foreach f (a) " * 2000 + "git push" + "; end" * 2000, "foreach f (" + "a " * 5000 + ") git push; end",
                     "foreach f (a) { " * 500 + "git push" + " }" * 500, "end; " * 2000 + "git push",
                     "foreach a " + "b " * 3000 + "(c) git push; end", "foreach f (a) true; " * 2000 + "git push",
                     "foreach " * 500):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = self.analysis(line)
                self.assertLess(time.monotonic() - started, 5.0)
                if "git push" in line:
                    self.assertIn(("git", ("push", "push")), a.findings, line[:40])


# SPD-182: the other spellings of a for or select header, `%s` its body.  zsh's parser (par_for) reads a for's header as it
# reads a foreach's (SPD-180): one or more names, then `( word ... )`, `in word ... TERM` or neither -- the positional
# parameters -- with the word after each name in command position, so a `do` or a `{` there opens a body over the positional
# parameters and a word that is no name the first command of one.  The first name may be any identifier, `in` and a reserved
# word among them, and every name a run of digits.  A select takes one name, and the word after it, unless `in` or `(`, is
# its body's first.  FOR_OPENERS are the other words that open such a body: a reserved word, a subshell after a `{` or a
# `do`; FOR_BODIES are body forms, each after `for a b (1 2)`, `for f do`, `for f {` and `select f` (where a `( list )`
# is the word list instead); FOR_ENCLOSED are the places such a loop may stand.  With `set -- p`, a file holding 1 on
# standard input for a select, and `echo <label> >> ran.log` in the slot, each of the 102 lines built from these and from
# ForSelectHeaderTest's other loop forms logged its label in zsh 5.9 (arm64-apple-darwin26.0) -f and -f -o nobareglobqual (tests/probes/shell_probe.py,
# 2026-09-23), and with `tee (l|x)/t<n>` in the slot each of the 39 headers and enclosing forms it writes through wrote its
# l/t<n>; bash 3.2.57 ran only the lines whose loop is spelled `for f do` or `select f do` (and `zsh -f -c`'s, which zsh
# ran), and rejected the rest.
FOR_HEADERS = (
    "for f do %s; done",
    "for f { %s }",
    "for f {%s}",
    "for a b (1 2) %s",
    "for a b (1 2) { %s }",
    "for a b (1 2); %s",
    "for a b do %s; done",
    "for a b { %s }",
    "for f g {%s}",
    "for in (a b) %s",
    "for do (a b) %s",
    "for end (a) %s",
    "for 1 (a b) %s",
    "for f 1 (a b) %s",
    "for f 12 (a b) %s",
    "for if in a b; %s",
)
SELECT_HEADERS = (
    "select f %s",
    "select f do %s; done",
    "select f { %s }",
    "select f {%s}",
    "select in (a) %s",
)
FOR_OPENERS = (
    "for f repeat 1 %s",
    "for f time %s",
    "for f ! %s",
    "for f nocorrect %s",
    "for f if true; then %s; fi",
    "for f [[ -n x ]] && %s",
    "for f case x in x) %s;; esac",
    "for f for g (x) %s",
    "for f foreach g (x) %s; end",
    "for f {(%s)}",
    "for f do (%s); done",
    "for f g { (%s) }",
    "for f function g { %s }; g",
    "select f if true; then %s; fi",
    "select f repeat 1 %s",
    "select f [[ -n x ]] && %s",
    "select f {(%s)}",
    "foreach in (a b) %s; end",
    "foreach end (a) %s; end",
    "foreach f 1 (a b) %s; end",
)
FOR_BODIES = ("%s", "( %s )", "{ %s }", "true && %s", "false || %s", "%s | cat", "if true; then %s; fi",
              "case x in x) %s;; esac", "repeat 1 %s")
FOR_ENCLOSED = ("eval 'for f do %s; done'", "x=$(for a b (1 2) %s)", "zsh -f -c 'for f { %s }' zc p",
                "fn() { for f do %s; done }; fn q", "if true; then for a b (1 2) %s; fi", "{ for f { %s } }",
                "time for f do %s; done", "! for f { %s }", "echo x | for f do %s; done",
                "case x in x) for a b (1 2) %s;; esac", "for f (a) for g { %s }", "repeat 1 for f do %s; done",
                "eval 'select f %s'", "x=$(select f %s)")


class ForSelectHeaderTest(BashHookCase):
    """SPD-182, filed by SPD-180's engineer: ShellWalk read every word of a for or select header up to a terminator, or up to
    a `( ... )` list after exactly one name, as the header's, so the four spellings whose body starts on the header's line
    with no terminator -- `for f do git push; done`, `for f { git push }`, `for a b (1 2) git push` and `select f git push`
    -- were a header with nothing after it.  A git write there was silent for a member (Law 7), a redirection or a tee into a
    generated file recorded nothing (Law 1 for Spud, Law 5 for a member), and mark_zsh_patterns read a `(` after the name, a
    `do` or a `{` as a glob, so `for f do (git push); done` hid its subshell.  SPD-180 gave foreach zsh's reading of its
    header; for and select share it (par_for) and now read so too, and so does foreach where its names were read short:
    `foreach in (a b) ...; end` and `foreach f 1 (a b) ...; end` were silent as well.

    Probed 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, which printed the same for every line but the qualifier's, and in GNU bash 3.2.57:

    - the ticket's evidence, with `set -- p`: `for f do echo fd-$f; done` printed fd-p (bash ran it too, the POSIX form),
      `for f { echo fb-$f }` fb-p, `for a b (1 2 3 4) echo two-$a$b` two-12 and two-34, and `{ select f echo sel-$f; } <
      in`, with 1 in the file, sel-p; bash rejected all but the first near the word after the name;
    - the names: after the first, zsh reads each word in command position, so a reserved word or a redirection ends them and
      opens the body (`for f > o1` made o1, `for f 2> o2 echo x` made o2, `for f &> o8 echo x` o8, and FOR_OPENERS ran
      theirs), an identifier or a run of digits is one more name (`for f 1 (a b) echo n-$f$1` printed n-ab, and so did
      foreach), and any other word is a parse error (`for f x-y`, `for f a[1] (x)`).  The first name is read with command
      position off: `for in (a b)`, `foreach in (a b)`, `select in (a)`, `for do (a b)`, `for end (a)`, `for if in a b;`
      and `for 1 (a b)` each ran their body for each word, and `for x-y (a)` and `for { ... }` failed near their word;
    - select takes one name: `select f x-y` ran a command named x-y, `select a b c` one named b, and `select a b (1 2) echo
      x` failed expanding `(1 2)` as b's glob argument (no matches found; unknown file attribute under -f); `select f (echo
      x)` is its word list, and `select f` then a newline and `(echo x)` a subshell body;
    - the body: a for's is a `do ... done`, a `{ list }` or one sublist (`for a b (1 2) echo s1-$a; echo s2-$a` printed
      s1-1 then s2-1 once); `for f git push` ran nothing, git and push being names, and `for f git push; done` failed near
      `done`; a `((` after a name is an arithmetic command, not a list (`for f ((1)) && echo $f` echoed the positional
      parameter);
    - the loop runs in the shell: `for f do cd d; done`, `for f { cd d }`, `for a b (1 2) cd d` and `{ select f cd d; } <
      in` each ended in d, and `for f do (cd d); done` did not move it;
    - with l/t present, the pattern after the body's command word was expanded: `echo fd | for f do tee (l|x)/t1 >
      /dev/null; done`, `for f { echo fb > (l|x)/t2 }`, `for a b (1 2) echo ab > (l|x)/t3`, `echo abt | for a b (1 2) tee
      (l|x)/t4 > /dev/null`, `{ select f echo sr > (l|x)/t6; } < in` and `{ select f tee (l|x)/t8 < in; } < in` each wrote
      its l/t, and `for f g > (l|x)/t echo gx` wrote gx there; with b and c present, zsh -f ran the `e:` code of `for f do
      ls (b|c)(e:<code>:); done`, of `for a b (1 2) ls (b|c)(e:<code>:)` and of `{ select f ls (b|c)(e:<code>:); } < in`
      for the files it matched, and zsh -f -o nobareglobqual read a second pattern and found no match.

    AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    TARGET = "(ledger|x)/tickets/SPD-001.md"
    EVIDENCE = ("for f do %s; done", "for f { %s }", "for a b (1 2) %s", "select f %s")

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/zzone"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")
        self.m = load_spud_module()

    def analysis(self, command):
        return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path)))

    def every_payload(self, form):
        """Law 7, Law 6, Law 5's --as, the database and Law 1 through a redirection and a tee for both members; for Spud, the
        checks that apply to him."""
        home, spud = self.home.path, self.spud_cli
        self.assertPayloadsAnswered(form, (("git push", "Law 7"), ("%s ticket new --title x" % spud, "Law 6"),
                                           ("%s --as spud member log hi" % spud, "Law 6"),
                                           ("%s --as %s member log hi" % (spud, AGENT_B), "--as"),
                                           ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                                           ("echo x > ledger/tickets/SPD-001.md", "generated"),
                                           ("echo x | tee ledger/tickets/SPD-001.md", "generated")),
                                    (("%s --as %s member log hi" % (spud, AGENT_A), "--as"),
                                     ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                                     ("echo x > ledger/tickets/SPD-001.md", "Law 1"),
                                     ("echo x | tee ledger/tickets/SPD-001.md", "Law 1")))

    def law_7(self, line):
        """A member is refused the push, and the analysis finds it; Spud is never refused git."""
        with self.subTest(line=line):
            self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
            self.assertRefused(line, "Law 7")
            self.assertRefused(line, "Law 7", AGENT_C)
            self.assertSilent(line, agent_id=None)

    def refused_everywhere(self, line, target="ledger/tickets/SPD-001.md"):
        """Refused to both members for the ledger file, named as the line spells it, and to Spud on Law 1."""
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(line=line, agent_id=agent_id):
                r = self.assertRefused(line, "generated", agent_id)
                self.assertIn(target, r.reason)
        with self.subTest(line=line, agent_id="spud"):
            self.assertRefused(line, "Law 1", agent_id=None)

    def path_rule(self, line):
        """A write with no `/` in its words: refused to AGENT_A, whose deliverables are tests/** and bin/spud, allowed to
        AGENT_C, and refused to Spud, whose own files these are not."""
        with self.subTest(line=line):
            self.assertRefused(line, "deliverables")
            self.assertSilent(line, AGENT_C)
            self.assertRefused(line, "Law 1", agent_id=None)

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_is_read_as_the_loop_zsh_runs(self):
        for header in self.EVIDENCE:
            self.law_7(header % "git push")
            self.refused_everywhere(header % "tee ledger/tickets/SPD-001.md")
            self.refused_everywhere(header % "echo x > ledger/tickets/SPD-001.md")
            self.path_rule(header % "rm -rf docs")
            self.assertIn("docs", [w[1] for w in self.analysis(header % "rm -rf docs").arg_writes])
            # the pattern zsh expands after the body's command word is kept in its word, a list's own `(` left as it is
            for line in (header % ("tee %s" % self.TARGET), header % ("echo x > %s" % self.TARGET)):
                with self.subTest(line=line):
                    marked, _other = self.m.mark_zsh_patterns(line)
                    self.assertEqual(marked.count("("), line.count("(") - 1)
                    self.assertEqual(self.m.deglob(marked), line)
                    self.assertIn(self.TARGET, [self.m.deglob(t) for t, _c in self.analysis(line).redirects])
                self.refused_everywhere(line, self.TARGET)

    # -- the hole -----------------------------------------------------------------------------------------------------
    def test_every_header_reads_its_body(self):
        for header in FOR_HEADERS + SELECT_HEADERS:
            self.law_7(header % "git push")
            self.refused_everywhere(header % ("tee %s" % self.TARGET), self.TARGET)

    def test_every_word_that_opens_a_body_reads_it(self):
        """A reserved word, a subshell after a `{` or a `do`, and a redirection after the names (FOR_OPENERS; with `set --
        p`, `for f > o1` made o1, `for f 2> o2 echo x` made o2, `for f g >o3 echo x` wrote x to o3, `for f &> o8 echo x` made
        o8, `for f g > (l|x)/t echo x` wrote x to l/t, `for f typeset -f > o4` made o4, and `{ select f > o5; } < in` and `{
        select f 2> o6 echo x; } < in` made theirs)."""
        for opener in FOR_OPENERS:
            self.law_7(opener % "git push")
        self.path_rule("for f repeat 1 rm -rf docs")
        for line in ("for f > ledger/tickets/SPD-001.md", "for f 2> ledger/tickets/SPD-001.md git status",
                     "for f &> ledger/tickets/SPD-001.md echo x", "select f > ledger/tickets/SPD-001.md",
                     "select f 2> ledger/tickets/SPD-001.md echo x", "for f typeset -f > ledger/tickets/SPD-001.md"):
            self.refused_everywhere(line)
        self.refused_everywhere("for f g > %s echo x" % self.TARGET, self.TARGET)
        self.refused_everywhere("echo x | for f do tee %s > /dev/null; done" % self.TARGET, self.TARGET)
        self.refused_everywhere("select f tee %s < ledger/tickets/SPD-001.md" % self.TARGET, self.TARGET)
        self.refused_everywhere("select f { echo x > %s }" % self.TARGET, self.TARGET)
        self.refused_everywhere("select f echo x | tee %s > /dev/null" % self.TARGET, self.TARGET)
        # a redirection's own descriptor stays the redirection's: `2>&1` duplicates, it writes no file named 1
        self.law_7("for f 2>&1 git push")

    def test_every_body_form_reads_its_commands(self):
        """Each body form after each header, but a `( ... )` right after a select's name, which is its word list
        (test_names_and_lists_run_nothing)."""
        for header in ("for a b (1 2) %s", "for f do %s; done", "for f { %s }", "select f %s"):
            for body in FOR_BODIES:
                if not (header == "select f %s" and body[:1] == "("):
                    self.law_7(header % (body % "git push"))

    def test_every_enclosing_text_reads_its_body(self):
        for form in FOR_ENCLOSED:
            self.law_7(form % "git push")
            self.refused_everywhere(form % ("tee %s" % self.TARGET), self.TARGET)

    def test_every_payload_for_every_caller(self):
        for form in self.EVIDENCE + ("for a b (1 2) ( %s )", "for f do ( {%s} ); done", "for f g { %s }", "for in (a b) %s",
                                     "for f 1 (a b) %s", "select f { %s }", "select f do %s; done", "for f if true; then %s; fi"):
            self.every_payload(form)

    def test_the_path_rule_in_the_body(self):
        for form in self.EVIDENCE + ("for f {%s}", "select f { %s }"):
            for write in ("echo x | tee note.txt", "touch note.txt", "rm -rf docs"):
                self.path_rule(form % write)
            line = form % "echo x | tee tests/zzone/k.py"
            with self.subTest(line=line):
                self.assertSilent(line)
                self.assertSilent(line, AGENT_C)

    def test_the_qualifier_code_is_read(self):
        tickets = str(self.home.path / "ledger" / "tickets")
        for header in ("for f do %s; done", "select f %s", "for a b (1 2) %s"):
            self.law_7(header % "ls (b|c)(e:'git push':)")
            line = header % "ls (b|c)(e:'echo x > SPD-001.md':)"
            self.assertRefused(line, "Law 1", None, tickets)
            self.assertRefused(line, "generated", AGENT_C, tickets)

    def test_the_loop_variable_is_doubted(self):
        """Each name takes the list's words in turn, or the positional parameters, so a command word built from one is
        refused (with `set -- p`, `for X (echo) $X xv` printed xv and `for X do $X xd; done` ran a command named p)."""
        for line in ("for X do $X push; done", "for X { $X push }", "for Y X (a git) $X push", "select X $X push",
                     "select X do $X push; done", "for in (git) $in push", "foreach Y 1 (a git) $1 push; end",
                     "for do (git) { $do push }"):
            with self.subTest(line=line):
                self.assertRefused(line, "cannot resolve")
                self.assertRefused(line, "cannot resolve", AGENT_C)

    # -- SPD-269: a header assigns its names alone ---------------------------------------------------------------------
    # Each spelling runs no command and has a variable x's name among its list's words.
    LIST_SPELLS_X = ("for w in x; do :; done", "for w in x y; do true; done", "for w in x\ndo :; done", "for w (x) :",
                     "for w ( x y ) true", "foreach w (x) true; end", "select w in x; do break; done", "select w (x) true",
                     "for a b in x y; do :; done", "for a 1 (x y) :", "for in (x in) :", "for w in $x; do :; done",
                     "for w in ${x} \"$x\" x.$x; do :; done", "for w in $(echo x); do :; done", "for w in; do :; done")

    def test_a_list_word_leaves_the_variable_it_spells(self):
        """SPD-269: ShellWalk.finish doubted every name any word of a for, select or foreach header spelled, the list's
        words among them, so `x=note.txt; for w in x; do :; done; echo > $x` left `$x` unresolved, refused to every caller
        (SPD-091), where the shells write note.txt: the list is expanded before the loop runs, and the header assigns only
        its names (probed 2026-09-24 through tests/probes/shell_probe.py: `x=note.txt; for w in x; do :; done; echo $x` and
        the same with `select w in x; do break; done` printed note.txt in zsh 5.9 -f, -f -o nobareglobqual and bash
        3.2.57, and `for a b in x y` in both zsh).  The write is then held to the path rule as it is with no loop before it."""
        for form in self.LIST_SPELLS_X:
            line = "x=note.txt; %s; echo hi > $x" % form
            with self.subTest(line=line):
                self.assertEqual([t for t, _c in self.analysis(line).redirects], ["note.txt"])
                self.assertNotIn("x", self.analysis(line).doubt)
            self.path_rule(line)
            line = "x=tests/zzone/k.py; %s; echo hi | tee $x" % form
            with self.subTest(line=line):
                self.assertSilent(line)
                self.assertSilent(line, AGENT_C)

    def test_the_loops_own_names_are_still_doubted(self):
        """What a header does assign: its names, which hold the last word after the loop (`x=note.txt; for x in a b; do :;
        done; echo $x` printed b in zsh 5.9 and bash 3.2.57), so a write naming one after the loop is unresolved, and in
        the body it is read once per word as before (SPD-146)."""
        for form in ("for x in a b; do :; done", "for x (a b) :", "foreach x (a b) true; end", "select x in a; do break; done",
                     "for w x in a b; do :; done", "for x; do :; done", "for x do :; done", "for x in $(cat l); do :; done"):
            line = "x=note.txt; %s; echo hi > $x" % form
            with self.subTest(line=line):
                self.assertIn("x", self.analysis(line).doubt)
                self.assertEqual([t for t, _c in self.analysis(line).redirects], ["$x"])
                for agent_id in (AGENT_A, AGENT_C, None):
                    self.assertRefused(line, "cannot resolve", agent_id)
        line = "for f in note.txt tests/zzone/k.py; do echo hi > $f; done"
        self.assertEqual([t for t, _c in self.analysis(line).redirects], ["note.txt", "tests/zzone/k.py"])
        self.assertRefused(line, "deliverables")
        self.assertSilent(line, AGENT_C)
        self.assertSilent("for f in tests/zzone/a.py tests/zzone/b.py; do echo hi > $f; done")
        # a list word the line does not settle leaves the name unknown in the body, as before
        for line in ("for f in $(cat l); do echo hi > $f; done", "for f in $y a; do echo hi > $f; done"):
            with self.subTest(line=line):
                for agent_id in (AGENT_A, AGENT_C, None):
                    self.assertRefused(line, "cannot resolve", agent_id)

    def test_a_list_words_own_assignment_is_read(self):
        """A list word's expansion runs in the loop's shell before the loop, and what it assigns stands after it (`x=note.txt;
        for f in $((x=5)); do :; done; echo $x` printed 5 in zsh 5.9 and bash 3.2.57): read as a command's words are, with
        the loop's own assignments, which a compound command leaves doubted -- so no longer by the name the word spells,
        and still a value the hook does not settle."""
        for line in ("x=note.txt; for f in $((x=5)); do :; done; echo hi > $x", "x=note.txt; for f ($[x=5]) :; echo hi > $x",
                     "x=note.txt; for f in $((y=x=5)); do :; done; echo hi > $x"):
            with self.subTest(line=line):
                self.assertIn("x", self.analysis(line).doubt)
                self.assertRefused(line, "cannot resolve", AGENT_C)
        line = "x=note.txt; for f in $((y=5)); do :; done; echo hi > $x"
        self.assertEqual([t for t, _c in self.analysis(line).redirects], ["note.txt"])
        # a list word's substitution is read as a command word's is, whatever that reading doubts
        for body in ("$(x=7; echo q)", "$(echo x)", "`x=7`", "*(e:'x=7':)"):
            with self.subTest(body=body):
                self.assertEqual("x" in self.analysis("x=note.txt; for f in %s; do :; done" % body).doubt,
                                 "x" in self.analysis("x=note.txt; echo %s" % body).doubt)
        for line in ("x=note.txt; for f in ${x:=y}; do :; done; echo hi > $x",
                     "x=note.txt; for f in ${x::=y}; do :; done; echo hi > $x"):
            with self.subTest(line=line):
                self.assertIn("x", self.analysis(line).doubt)
                self.assertRefused(line, "cannot resolve", AGENT_C)
        # an arithmetic header's expressions assign as the loop runs: its names stay doubted
        line = "i=note.txt; for (( i=0; i<1; i++ )) true; echo hi > $i"
        self.assertIn("i", self.analysis(line).doubt)
        self.assertRefused(line, "cannot resolve", AGENT_C)

    # -- the loop model -----------------------------------------------------------------------------------------------
    def test_a_cd_in_the_body_is_read_as_a_loops(self):
        """A relative cd may repeat, so it is unfollowable; an absolute one leaves the union of before and after; a
        subshell's does not carry out; and a cd after a for's sublist body is outside the loop and followed."""
        home, out = self.home.path, self.out
        for form in self.EVIDENCE + ("for f g { %s }", "for in (a b) %s", "select f do %s; done"):
            with self.subTest(form=form):
                self.assertRefused((form % "cd docs") + "; echo x > note.txt", "cannot follow", AGENT_C)
                self.assertRefused((form % ("cd %s/ledger" % home)) + "; echo x > tickets/SPD-001.md", "generated", AGENT_C)
                self.assertEqual(self.analysis(form % ("cd %s" % out)).cwds, frozenset([str(home), str(out)]))
        self.assertSilent("for f do (cd %s/ledger); done; echo x > tickets/SPD-001.md" % home, AGENT_C)
        self.assertEqual(self.analysis("for f do (cd %s); done" % out).cwds, frozenset([str(home)]))
        for form in ("for a b (1 2) true; %s", "select f true; %s"):
            with self.subTest(form=form):
                self.assertSilent(form % ("cd %s; echo x > note.txt" % out))
                self.assertRefused(form % ("cd %s/ledger; echo x > tickets/SPD-001.md" % home), "generated", AGENT_C)

    def test_the_body_reads_as_the_command_does_alone(self):
        """The loop runs its body, and its header changes none of the body's words: every finding, redirection target and
        written operand the body has on its own line, it has after each header."""
        for body in [w % self.TARGET for w in FOR_ARITH_WRITERS] + ["cat %s" % self.TARGET, "echo (b|c)",
                                                                    "ls (b|c)(e:'git push':)", "rm -rf docs"]:
            alone = self.analysis(body)
            for header in self.EVIDENCE + ("for in (a b) %s", "for f 1 (a b) %s", "select f do %s; done", "for f g { %s }"):
                line = header % body
                with self.subTest(line=line):
                    looped = self.analysis(line)
                    self.assertEqual(looped.findings, alone.findings)
                    self.assertEqual([t for t, _c in looped.redirects], [t for t, _c in alone.redirects])
                    self.assertEqual([w[1] for w in looped.arg_writes], [w[1] for w in alone.arg_writes])

    # -- controls -----------------------------------------------------------------------------------------------------
    def test_names_and_lists_run_nothing(self):
        """An identifier or a run of digits after a for's names is one more name, a word after `in` or inside `( ... )` a
        word of the list, a `for` out of command position a word, and a group after the body's command word a pattern:
        none of them runs."""
        for line in ("for f git push", "for f g h", "for f git push; done", "foreach f 1 git push; end", "for f 1 2 git",
                     "echo for f do git push", "for f in git push; do echo $f; done", "select f in git push; do true; done",
                     "for f (git push) echo $f", "select f (git push) true", "select f ( git push )", "for a b (git push) true",
                     "for f do echo (b|c); done", "select f echo (b|c)", "for a b (1 2) echo (b|c)"):
            with self.subTest(line=line):
                self.assertNotIn(("git", ("push", "push")), self.analysis(line).findings)
                self.assertSilent(line)
                self.assertSilent(line, AGENT_C)
        # select takes one name: the word after it is the body's command, the group after that its glob argument
        self.assertNotIn(("git", ("push", "push")), self.analysis("select a b (1 2) git push").findings)

    def test_the_other_spellings_keep_their_reading(self):
        for line in ("for f in a b; do git push; done", "for f (a b) git push", "for f; git push", "for f\ndo git push\ndone",
                     "select f in a b; do git push; done", "select f (a b) git push", "for (( i=0; i<1; i++ )) git push",
                     "foreach f (a b) git push; end", "for f\n(git push)", "select f\n(git push)"):
            self.law_7(line)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        for line in ("for f do " * 2000 + "git push" + "; done" * 2000, "for f { " * 500 + "git push" + " }" * 500,
                     "for a " + "b " * 3000 + "(c) git push", "select f " * 2000 + "git push",
                     "for a " + "b " * 3000 + "do git push; done", "for f in " + "a " * 5000 + "; git push",
                     "for " * 500, "select " * 500, "for f 1 do " * 1000 + "git push" + "; done" * 1000):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = self.analysis(line)
                self.assertLess(time.monotonic() - started, 5.0)
                if "git push" in line:
                    self.assertIn(("git", ("push", "push")), a.findings, line[:40])


class LoopOptionWordsTest(BashHookCase):
    """SPD-274, handed on by SPD-269: a for loop over spelled words settled no value that may start with `-`, `~` or `=`
    (loop_bindings.loop_values), so `for o in --tags --prune; do git fetch $o origin; done` was refused a member as an
    unsettled leading expansion, though every value is on the line.  The dispatch reading now takes such a loop's values
    once each, as the option or operand each is where it stands (a program option refused under Law 7 as spelled would
    be); the write channels keep their rule, an option-looking value no path."""

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)
        return r

    def finding(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path))).findings

    def test_the_tickets_line_is_allowed(self):
        for ok in ("for o in --tags --prune; do git fetch $o origin; done", 'for o in --tags; do git fetch "$o" origin; done',
                   "for o in -q --dry-run; do git fetch ${o} origin; done", "for o in --heads --tags; do git ls-remote $o .; done",
                   "for o in origin --tags; do git fetch $o; done"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, AGENT_C)
                self.assertSilent(ok, agent_id=None)

    def test_a_program_option_among_the_values_is_refused_as_spelled(self):
        for cmd in ("for o in --tags --upload-pack=sh; do git fetch $o r; done",
                    "for o in --upload-pack=sh; do git ls-remote $o .; done"):
            with self.subTest(cmd):
                looped = self.refused_for_members(cmd)
                self.assertNotIn("spell the words out", looped.reason)
        # archive's -o takes the next word as the file it writes, spelled or looped alike
        for agent_id in (AGENT_A, AGENT_C, None):
            with self.subTest(agent_id=agent_id):
                looped, spelled = (self.bash(c, agent_id) for c in ("for o in -o; do git archive $o x HEAD; done",
                                                                     "git archive -o x HEAD"))
                self.assertEqual((looped.decision, looped.reason), (spelled.decision, spelled.reason))
        spelled = self.refused_for_members("git fetch --upload-pack=sh r")
        looped = self.refused_for_members("for o in --tags --upload-pack=sh; do git fetch $o r; done")
        self.assertIn("--upload-pack=sh", looped.reason)
        self.assertEqual(self.finding("for o in --upload-pack=sh; do git fetch $o r; done"),
                         self.finding("git fetch --upload-pack=sh r"))
        self.assertTrue(spelled.reason)

    def test_an_unsettled_list_is_still_refused(self):
        for cmd in ("for o in $(cat opts); do git fetch $o r; done", "for o in --tags $OPT; do git fetch $o r; done",
                    "for o in --tags; do :; done; git fetch $o r", "for o in --tags; do f() { git fetch $o r; }; done"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, needle="spell the words out")

    def test_the_write_channels_keep_their_rule(self):
        """A value that may start with `-` is no path: a write naming it reads the raw word and the refusal it earns,
        as before (SPD-146)."""
        m = load_spud_module()
        for body in ("touch $f", "cp README $f", "echo x > $f"):
            unsettled = m.analyse_command("for f in $Q; do %s; done" % body, m.ShellAnalysis(cwd=str(self.home.path)))
            for words in ("-x", "--x a", "~x", "=x"):
                cmd = "for f in %s; do %s; done" % (words, body)
                with self.subTest(cmd):
                    a = m.analyse_command(cmd, m.ShellAnalysis(cwd=str(self.home.path)))
                    self.assertEqual([w[1] for w in a.arg_writes], [w[1] for w in unsettled.arg_writes])
                    self.assertEqual([t for t, _c in a.redirects], [t for t, _c in unsettled.redirects])


if __name__ == "__main__":
    unittest.main()
