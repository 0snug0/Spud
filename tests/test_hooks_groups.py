"""PreToolUse(Bash): case patterns and case substitutions, groups across newlines, coprocesses, prefixed groups,
try-always, and braces glued to a word."""

import importlib
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import load_spud_module, wall_clock
from hookcase import AGENT_A, AGENT_B, AGENT_C, BashHookCase, fresh_process


# SPD-181: case patterns that hold a glob group, `%s` the arm's body.  With `( {echo <label>} )`, `{echo <label>}` and `{(
# echo <label> )}` in the slot every one printed its label in zsh 5.9 -f and -f -o nobareglobqual, and with l/t present
# `echo <label> | <the line with tee (l|x)/t > /dev/null in the slot>` wrote the label to l/t for every one but the two
# extendedglob lines, whose pipe fed setopt (tests/probes/shell_probe.py, 2026-09-23); bash 3.2.57 rejected every one, near
# its group, its `;&` or its `;|`.
CASE_GROUP_PATTERNS = (
    # a group opening the pattern's first word: `((x))` (the ticket's first line), the optional parenthesis around a group;
    # `(x|y))` (its third), a group that is the pattern itself
    "case x in ((x)) %s;; esac",
    "case x in (x|y)) %s;; esac",
    "case x in (x)) %s;; esac",
    "case x in (((x))) %s;; esac",
    "case x in ((x|y)) %s;; esac",
    "case x in (a|(x|y))) %s;; esac",
    "case x in ((x) | y) %s;; esac",
    "case x in ( (x) ) %s;; esac",
    "case ab in (a)(b)) %s;; esac",
    "case xy in (x)y) %s;; esac",
    "case 5 in (<1-9>)) %s;; esac",
    "setopt extendedglob; case X in (#i)x) %s;; esac",
    "setopt extendedglob; case X in ((#i)x)) %s;; esac",
    # a group after a bar, or glued inside a word, an assignment's spelling among them
    "case x in x|(y)) %s;; esac",
    "case x in (x)|y) %s;; esac",
    "case x in (x) | (y)) %s;; esac",
    "case ab in a(b|c)) %s;; esac",
    "case mode=x in mode=(a|x)) %s;; esac",
    # a pattern after `;;` (the ticket's second line), `;&` and `;|`, glued to its terminator, or on a line of its own
    "case x in a) true;; ((x)) %s;; esac",
    "case x in a) true;; (x|y)) %s;; esac",
    "case x in x) true;& ((x)) %s;; esac",
    "case x in x) true;& (x|y)) %s;; esac",
    "case x in x) true;| ((x)) %s;; esac",
    "case x in x) true;| (x|y)) %s;; esac",
    "case x in y) ;;((x)) %s;; esac",
    "case x in\n((x)) %s;; esac",
    "case x in\n  (x|y))\n    %s\n  ;;\nesac",
    "case x in ((x))\n%s;; esac",
    # a newline inside the group, which zsh reads as part of the pattern
    "case x in ((x|\ny)) %s;; esac",
    "case x in (x|\ny)) %s;; esac",
)
# ... and the places such a case may stand, `%s` its body: each printed its label too.
CASE_GROUP_ENCLOSED = (
    "eval 'case x in ((x)) %s;; esac'",
    "x=$(case x in ((x)) %s;; esac)",
    "echo `case x in (x|y)) %s;; esac`",
    "zsh -f -c 'case x in ((x)) %s;; esac'",
    "f() { case x in (x|y)) %s;; esac }; f",
    "if true; then case x in ((x)) %s;; esac; fi",
    "case x in x) case y in ((y)) %s;; esac;; esac",
    "{ case x in (x|y)) %s;; esac }",
    "for f (a) case x in ((x)) %s;; esac",
    "time case x in (x|y)) %s;; esac",
)


class CasePatternGroupTest(BashHookCase):
    """SPD-181, filed by SPD-178's engineer: mark_zsh_patterns ended a case pattern at the first `)` it met, so after a
    pattern holding a glob group, `((x))` or `(x|y))`, the arm's body started outside command position: a `( {list} )`
    subshell there was one glob word in zsh's reading and a command named `{git` in bash's, its commands unread.  After `;;`
    its arithmetic branch took `((x))` for an arithmetic command and left the pattern open to the next `)`, so a tee into a
    group in the body lost its target.  The proposer's evidence, on the SPD-178 tree: `case x in ((x)) ( {git push} );;
    esac` and `case x in (x|y)) ( {git push} );; esac` recorded no finding (Law 7 for members), and `case x in a) true;;
    ((x)) tee (ledger|x)/tickets/SPD-001.md;; esac` recorded only /tickets/SPD-001.md (silent for Spud, Law 1).

    Probed 2026-09-22 (the proposer) and 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0)
    under -f -o nobareglobqual and under -f, which printed the same for every line, and in GNU bash 3.2.57:

    - zsh reads a pattern word whole, its groups with the blanks, bars and newlines in them, and the pattern ends at the
      `)` after it (`(x|y)) echo C`, `(x)) echo I`, `x|(y)) echo E` and `(x)y) echo S16` each ran their body).  A first word
      that starts and ends with a group and is followed by anything but a `)` or a `|` is the pattern in its optional
      parentheses (`((x)) echo B`, `( (x) ) echo T`, `(x) echo A`), and its body starts after it in command position:
      `((x)) { echo Z13 }`, `((x)) if true; then echo Z14; fi`, a `((` there an arithmetic command (`((x)) (( 3 > 2 ))
      && echo Z28` made no file 2).  Anything else there is a parse error (`(x)|(y) ( echo P21 )`, `z|(x) echo Z7`);
    - after `;;`, `;&` and `;|` a pattern is read, never an arithmetic command: `case y in a) true;; ((x)) echo wrong;;
      esac` printed nothing and `case y in ((x)) echo wrong2;; ((y)) echo W;; esac` printed W.  `;|` is a terminator of
      zsh's own (`echo a;| cat` failed near `;|`), which the hook read as `;` and `|`;
    - a group in a pattern runs nothing: `case x in ((echo P2)) true;; esac`, `(echo P3|x))`, `x) true;| (echo P1))` and
      `x) ;& (echo P4) )` printed nothing, and zsh generates no file names from a case's word or its patterns, so no glob
      qualifier there runs code (`(x)(e:"echo QUAL":))`, `((x)(e:"echo QUAL2":))`, `x(e:"echo QUAL3":))`,
      `(x|(e:"echo QUAL4":)))` with a file x present, `(x)(e:"echo QUAL":)|y)`, and the word `f*(e:"echo SUBJ":)` with a
      file f1 present printed no QUAL or SUBJ under -f);
    - `mode=(a|x)` is a group there, no array (`case mode=x in mode=(a|x)) echo m4;; esac` printed m4);
    - bash rejects a group in a pattern, `;&` and `;|`, and reads the POSIX spellings as zsh does.

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

    def refused_everywhere(self, line):
        """Refused to both members for the ledger file, named as the line spells it, and to Spud on Law 1."""
        with self.subTest(line=line):
            self.assertIn(self.TARGET, [self.m.deglob(t) for t, _c in self.analysis(line).redirects])
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(line=line, agent_id=agent_id):
                r = self.assertRefused(line, "generated", agent_id)
                self.assertIn(self.TARGET, r.reason)
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
    def test_the_tickets_evidence_is_read_as_zsh_runs_it(self):
        # zsh ran each subshell (`{echo case-sub}` and its kin printed their labels), and the hook found no push in the first
        # two; the third was refused before, and checking the arithmetic branch alone would have made it silent
        for line in ("case x in ((x)) ( {git push} );; esac", "case x in (x|y)) ( {git push} );; esac",
                     "case x in a) true;; ((x)) ( {git push} );; esac"):
            self.law_7(line)
        # the pattern stays one word and the subshell's parentheses stay the shell's
        marked, _other = self.m.mark_zsh_patterns("case x in (x|y)) ( {git push} );; esac")
        self.assertEqual((marked.count("("), marked.count(")")), (1, 2))
        # `((x))` after `;;` is a pattern (`case y in a) true;; ((x)) echo wrong;; esac` printed nothing): its optional
        # parentheses are the shell's, the tee's group a pattern zsh expands (with l/u present, `echo cu | case x in a) ;;
        # ((x)) tee (l|x)/u;; esac` wrote cu to l/u)
        line = "case x in a) true;; ((x)) tee %s;; esac" % self.TARGET
        marked, _other = self.m.mark_zsh_patterns(line)
        self.assertEqual(marked.count("("), 1)
        self.assertEqual(self.m.deglob(marked), line)
        self.refused_everywhere(line)

    # -- the hole -----------------------------------------------------------------------------------------------------
    def test_every_pattern_reads_its_body(self):
        for form in CASE_GROUP_PATTERNS:
            for body in ("( {%s} )", "{( %s )}", "{%s}", "( %s )", "%s"):
                self.law_7(form % (body % "git push"))
            self.refused_everywhere(form % ("tee " + self.TARGET))

    def test_every_enclosing_text_reads_its_body(self):
        for form in CASE_GROUP_ENCLOSED:
            self.law_7(form % "( {git push} )")
            self.law_7(form % "{( git push )}")
            self.refused_everywhere(form % ("tee " + self.TARGET))

    def test_every_payload_for_every_caller(self):
        for form in ("case x in ((x)) ( {%s} );; esac", "case x in (x|y)) ( {%s} );; esac",
                     "case x in a) true;; ((x)) ( {%s} );; esac", "case x in x) true;| (x|y)) {( %s )};; esac"):
            self.every_payload(form)

    def test_the_path_rule_in_the_body(self):
        for form in ("case x in ((x)) ( {%s} );; esac", "case x in (x|y)) ( {%s} );; esac",
                     "case x in a) true;; ((x)) ( {%s} );; esac", "case x in ((x|\ny)) {( %s )};; esac"):
            for write in ("echo x | tee note.txt", "touch note.txt", "rm -rf docs"):
                self.path_rule(form % write)
            line = form % "echo x | tee tests/zzone/k.py"
            with self.subTest(line=line):
                self.assertSilent(line)
                self.assertSilent(line, AGENT_C)

    def test_an_arithmetic_command_opens_the_body(self):
        """After the pattern `((` is an arithmetic command, whose `>` opens no file (probed: `((x)) (( 3 > 2 )) && echo Z28`,
        `(x|y)) (( 3 > 2 )) && echo A1` and `(x) (( 3 > 2 )) && echo A2` ran their echo and made no file 2)."""
        for line in ("case x in ((x)) (( 3 > 2 )) && git push;; esac", "case x in (x|y)) (( 3 > 2 )) && git push;; esac",
                     "case x in a) true;; ((x)) (( 3 > 2 )) && git push;; esac", "case x in (x) (( 3 > 2 )) && git push;; esac"):
            self.law_7(line)
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).redirects, [])

    # -- controls -----------------------------------------------------------------------------------------------------
    def test_a_pattern_runs_nothing(self):
        """A group in a pattern is matched against the case's word, never run, and no glob qualifier in a pattern runs code.
        The line after `;|` was refused before this ticket, which read `;|` as `;` then `|` and the group after it as a
        subshell; the glued qualifier was refused, its code read as a glob's."""
        for line in ("case x in ((git push)) true;; esac", "case x in (git push|x)) true;; esac",
                     "case x in x) true;| (git push)) true;; esac", "case x in x) ;& (git push) ) true;; esac",
                     "case x in x(e:'git push':)) true;; esac", "case x in ((x)(e:'git push':)) true;; esac",
                     "case x in (x)(e:'git push':)|y) true;; esac", "case x in (x|(e:'git push':))) true;; esac"):
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).findings, [])
                self.assertSilent(line)
                self.assertSilent(line, AGENT_C)

    def test_the_posix_spellings_keep_their_reading(self):
        """bash reads these too, and they are marked as they are spelled, or read as they were."""
        for line in ("case x in (x) git push;; esac", "case x in x) git push;; esac", "case x in (x|y) git push;; esac",
                     "case x in ( x | y ) git push;; esac", "case x in x|y) git push;; esac",
                     "case x in (x) ( {git push} );; esac", "case x in y) true;; (x) git push;; esac",
                     "case x in x)\n(git push);; esac", "case x in x) true;; y) git push;; esac"):
            self.law_7(line)
        for line in ("case x in (x) git push;; esac", "case x in y) true;; (x) git push;; esac", "case x in x|y) true;; esac"):
            with self.subTest(line=line):
                self.assertEqual(self.m.mark_zsh_patterns(line), (line, line))

    @wall_clock
    def test_bounded_on_pathological_input(self):
        for line in ("case x in " + "((x)) true;; " * 2000 + "(x|y)) ( {git push} );; esac",
                     "case x in " + "(" * 3000 + "x" + ")" * 3000 + " ( {git push} );; esac",
                     "case x in " + "x) true;| " * 2000 + "(x|y)) ( {git push} );; esac",
                     "case x in (" + "a|" * 5000 + "x)) ( {git push} );; esac",
                     "case x in " + "(x) | " * 2000 + "(y)) ( {git push} );; esac",
                     "case x in " + "(" * 5000 + "x ( {git push} );; esac",
                     "case x in " + "((x| ; " * 2000 + "y" + "))" * 2000 + " ( {git push} );; esac"):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = self.analysis(line)
                self.assertLess(time.monotonic() - started, 5.0)
                if ")) ( {" in line:
                    self.assertIn(("git", ("push", "push")), a.findings, line[:40])


# SPD-183: a newline inside a zsh glob group, in a word zsh expands.  Each writes ledger/tickets/SPD-001.md in zsh (probed:
# see GroupNewlineTest), the first the ticket's evidence.
GROUP_NEWLINE_TARGETS = (
    "(ledger|\nx)/tickets/SPD-001.md",
    "(ledger|\n\nx)/tickets/SPD-001.md",
    "(ledger|\t\n  x)/tickets/SPD-001.md",
    "(x|\n|ledger)/tickets/SPD-001.md",
    "(\nx|ledger)/tickets/SPD-001.md",
    "led(ger|\nx)/tickets/SPD-001.md",
    "(ledger|x)(|\n)/tickets/SPD-001.md",
    "ledger/(tickets|\n)/SPD-001.md",
    "ledger/tickets/SPD-00(1|\n2).md",
    "(ledger|(x|\ny))/tickets/SPD-001.md",
    "(ledger|\n# x\ny)/tickets/SPD-001.md",
)


class GroupNewlineTest(BashHookCase):
    """SPD-183, filed by SPD-181's engineer: zsh reads a newline inside a glob group as part of the pattern in any word, not
    only in a case pattern, but newlines_as_separators wrote every unquoted newline as ` ; `, and _zsh_group rejects a group
    of a word holding a `;`, so the hook read a subshell there and a write into the group lost its target.  The proposer's
    evidence, on the SPD-181 tree: `echo x > (ledger|<newline>x)/tickets/SPD-001.md` and `echo x | tee
    (ledger|<newline>x)/tickets/SPD-001.md` recorded only /tickets/SPD-001.md (silent for Spud, Law 1).

    Probed 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, which printed the same for every line but the qualifier's, and in GNU bash 3.2.57:

    - a newline in a group is one more character of the pattern, wherever it stands in the group: each of
      GROUP_NEWLINE_TARGETS wrote the ledger file through `>`, and the first through `>>`, `>|`, `&>`, `2>`, `tee -a`, `cp`,
      `mv`, `rm` (which removed it), `touch -t` and `truncate -s 0` (the probe runs no git; `git diff --output=` opens the
      file its word expands to as the others do), while `(ledger<newline>|x)` found no match, its first alternative being
      `ledger` and a newline, and `echo (a|<newline>b)` failed with "no matches found: (a|\\nb)".  With a file named push
      present, `echo (push|<newline>x)` and `echo p(u|<newline>x)sh` printed push, so a git verb spelled so is `git push`.
      eval, `$( )` and `zsh -f -c` wrote l/t through `(l|<newline>x)/t`;
    - under -f a glob qualifier's code with a newline in it ran (`ls tests/*(e{true<newline>echo QUAL-$REPLY})` printed
      QUAL-); under nobareglobqual it is a group, and no file matched;
    - a `#` after a newline in a group opens no comment there: `(ledger|<newline># x<newline>y)` is a pattern whose second
      alternative holds it (the write above), as a `#` in the middle of any word is;
    - a `;` spelled in a word's group ends the word there, the group left open: `x=a(l ; echo RAN7` with its `)` on the next
      line printed RAN7 and then failed near that `)`, and `echo x > (l ; echo RAN3` failed with "bad pattern: (l ", so it
      stays the plain reading's separator;
    - `(` in command position opens a subshell whatever the newlines in it (`(echo A|<newline>cat)` printed A);
    - bash rejected the first such line of every probe (`syntax error near unexpected token`), as it rejects any group
      (ZshGlobOperatorTest).

    A here-document whose `<<` line goes on into a group is not read here: the body starts after the line the command
    ends on (proposal 292, SPD-188's HereDocumentBodyTest).  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    TARGET = "ledger/tickets/SPD-001.md"

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for rel in (self.TARGET, "docs/x.md", "tests/keep.py"):
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")
        self.m = load_spud_module()

    def analysis(self, command):
        return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path)))

    def refused_everywhere(self, line):
        """Refused to both members for the ledger file, and to Spud on Law 1."""
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(line=line, agent_id=agent_id):
                self.assertRefused(line, "generated", agent_id)
        with self.subTest(line=line, agent_id="spud"):
            self.assertRefused(line, "Law 1", agent_id=None)

    def law_7(self, line):
        """A member is refused the push, and the analysis finds it; Spud is never refused git."""
        with self.subTest(line=line):
            self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
            self.assertRefused(line, "Law 7")
            self.assertRefused(line, "Law 7", AGENT_C)
            self.assertSilent(line, agent_id=None)

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_is_read_as_zsh_expands_it(self):
        for line in ("echo x > (ledger|\nx)/tickets/SPD-001.md", "echo x | tee (ledger|\nx)/tickets/SPD-001.md"):
            with self.subTest(line=line):
                # zsh's reading, first; the other shell's, which bash rejects, still reads a subshell there
                targets = [self.m.deglob(t) for t, _c in self.analysis(line).redirects]
                self.assertEqual(targets, ["(ledger|\nx)/tickets/SPD-001.md", "/tickets/SPD-001.md"])
            self.refused_everywhere(line)

    # -- the hole -----------------------------------------------------------------------------------------------------
    def test_every_spelling_of_the_group(self):
        for target in GROUP_NEWLINE_TARGETS:
            for form in ("echo x > %s", "echo x | tee %s"):
                self.refused_everywhere(form % target)
            with self.subTest(target=target):
                self.assertIn(target, [self.m.deglob(t) for t, _c in self.analysis("echo x > %s" % target).redirects])

    def test_every_write_channel(self):
        target = GROUP_NEWLINE_TARGETS[0]
        for form in ("echo x >> %s", "echo x >| %s", "echo x &> %s", "echo x 2> %s", "echo x | tee -a tests/keep.py %s",
                     "rm %s", "rm -f %s", "touch %s", "cp docs/x.md %s", "mv docs/x.md %s", "truncate -s 0 %s",
                     "git diff --output=%s"):
            with self.subTest(form=form):
                self.assertRefused(form % target, "generated", AGENT_C)
                self.assertRefused(form % target, "Law 1", agent_id=None)

    def test_a_git_verb_spelled_with_a_newline_in_a_group(self):
        for line in ("git (push|\nx)", "git p(u|\nx)sh", "git -C . (push|\nx)", "git (push|\nx) origin main",
                     "git -C (ledger|\nx) push"):
            with self.subTest(line=line):
                self.assertRefused(line, "Law 7")
                self.assertRefused(line, "Law 7", AGENT_C)

    def test_glob_qualifier_code_holding_a_newline(self):
        """zsh -f runs the code for each file the glob matches, newline and all; the hook reads it as a command."""
        for line in ("ls tests/*(e{true\ngit push})", "cat tests/keep.py(e{true\ngit push})",
                     "ls (tests|\nx)/*(e{git push})"):
            self.law_7(line)

    def test_every_enclosing_text(self):
        for form in ("eval 'echo x > %s'", "x=$(echo x > %s)", "echo `echo x | tee %s`", "zsh -f -c 'echo x > %s'",
                     "sh -c 'echo x | tee %s'", "if true; then echo x > %s; fi", "{ echo x | tee %s; }",
                     "f() { echo x > %s; }; f", "echo a; echo x > %s", "echo a &&\necho x > %s"):
            self.refused_everywhere(form % GROUP_NEWLINE_TARGETS[0])
        line = "zsh <<'EOF'\necho x > %s\nEOF" % GROUP_NEWLINE_TARGETS[0]  # a body a shell reads
        self.refused_everywhere(line)

    def test_the_path_rule_reads_the_group(self):
        """A member's own file through a group is its own, and a file outside its deliverables is not.  The groups are glued
        inside their words, which the other reading keeps whole too: a group opening a word is a subshell there, whose
        target (`/keep.py`) is outside every project, with a newline in it or not."""
        for line in ("echo x | tee tests/(keep|\nx).py", "echo x > tests/keep.(py|\nzz)", "touch tests/(keep|\nx).py",
                     "echo x | tee t(ests|\n)/keep.py"):
            with self.subTest(line=line):
                self.assertSilent(line)
                self.assertSilent(line, AGENT_C)
        for line in ("echo x | tee docs/(x|\ny).md", "rm docs/(x|\ny).md", "echo x > d(ocs|\n)/x.md"):
            with self.subTest(line=line):
                self.assertRefused(line, "deliverables")
                self.assertSilent(line, AGENT_C)
        self.assertRefused("echo x > (nomatch|\nzz)/x.py", "matches no file", AGENT_C)

    # -- controls -----------------------------------------------------------------------------------------------------
    def test_a_newline_is_still_a_separator_outside_a_group(self):
        """In command position `(` opens a subshell, its newlines separators; after a group, a newline ends the command."""
        for line in ("(ledger|\ngit push)", "(\ngit push)", "( echo a\ngit push )", "echo (a|b)\ngit push",
                     "echo (tests|x)/keep.py\ngit push", "echo a\n(git push)", "x=$(echo a\ngit push)"):
            self.law_7(line)
        prepared = self.m.newlines_as_separators("echo a\necho (b|\nc)\n(echo d)")
        marked, other = self.m.mark_zsh_patterns(prepared)
        self.assertEqual([t for t in self.m.shell_tokens(marked) if t == ";"], [";", ";"])
        self.assertEqual(self.m.deglob(marked), "echo a ; echo (b|\nc) ; (echo d)")
        self.assertEqual(other, "echo a ; echo (b| ; c) ; (echo d)")
        for text in ("echo a\ngit push", "a\n\nb"):  # no group: the text as the walk always read it
            prepared = self.m.newlines_as_separators(text)
            with self.subTest(text=text):
                self.assertEqual(self.m.mark_zsh_patterns(prepared), (prepared.replace(self.m.LINE_BREAK, ";"),) * 2)
        # ... but a comment, written as its `#` alone since SPD-186 (CaseCommentTest), its newline the `;` it was
        self.assertEqual(self.m.mark_zsh_patterns(self.m.newlines_as_separators("a # c\nb")), ("a #; b",) * 2)

    def test_a_spelled_semicolon_still_ends_the_word(self):
        """zsh's lexer ends a word at a spelled `;` even inside a group, so the text after it is a command, run when the
        group's `)` stands on a later line (probed: `x=a(l ; echo RAN7` then `)` printed RAN7) and a parse error when it
        stands on the same one (`echo (a ; echo RAN1 )` failed near `)`): the plain reading stands either way."""
        for line in ("x=a(l ; git push\n)", "echo x > (tests ; git push\n)/keep.py", "echo (a ; git push )",
                     "echo (a|\nb ; git push\n)"):
            self.law_7(line)
        self.assertRefused("echo x > ledger/tickets/SPD-00(1;|3).md", "generated", AGENT_C)

    def test_a_here_document_body_is_never_a_word(self):
        """A body cat reads is data, whatever text it holds; the line writes only its own redirection target."""
        for line in ("cat <<'EOF'\necho x > (ledger|\nx)/tickets/SPD-001.md\nEOF",
                     "cat > /dev/null <<EOF\n(ledger|\nx)/tickets/SPD-001.md\nEOF"):
            for agent_id in (AGENT_A, AGENT_C, None):
                with self.subTest(line=line, agent_id=agent_id):
                    self.assertSilent(line, agent_id)
        line = "cat <<EOF > tests/keep.py\n(ledger|\nx)/tickets/SPD-001.md\nEOF"
        for agent_id in (AGENT_A, AGENT_C):
            with self.subTest(line=line, agent_id=agent_id):
                self.assertSilent(line, agent_id)

    def test_a_case_pattern_reads_its_newline_as_the_newline(self):
        """SPD-181's case pattern holds its newline as zsh reads it, and its body still follows the pattern."""
        line = "case x in ((x|\ny)) tee (ledger|\nx)/tickets/SPD-001.md;; esac"
        self.refused_everywhere(line)
        self.law_7("case x in ((x|\ny)) {( git push )};; esac")
        self.law_7("case x in (x|\ny)) git push;; esac")

    @wall_clock
    def test_bounded_on_pathological_input(self):
        for line in ("echo x > (" + "a|\n" * 5000 + "ledger)/tickets/SPD-001.md",
                     "echo x > " + "(ledger|\nx)" * 2000,
                     "echo " + "(" * 3000 + "\n" * 3000 + ")" * 3000 + "\ngit push",
                     "echo " + "(a\n" * 3000 + "\ngit push"):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = self.analysis(line)
                self.assertLess(time.monotonic() - started, 5.0)
                if line.endswith("git push"):
                    self.assertIn(("git", ("push", "push")), a.findings, line[:40])


# SPD-184: the process substitutions zsh runs in a case's word and its patterns, `%s` standing for the list.  With a file
# touched in place of %s, zsh 5.9 made it for each (CaseSubstitutionTest has the probes).
CASE_EQUALS_FORMS = (
    # `=( ... )` opening the case's word, or a top-level alternative of a pattern -- after `in`, a bar with or without blanks
    # around it, `;;`, `;&`, `;|` or a newline: zsh parses its list as commands (the ticket's two lines first)
    "case x in =(%s)) true;; esac",
    "case =(%s) in x) true;; esac",
    "case =(%s)x in x) true;; esac",
    "case x in y|=(%s)) true;; esac",
    "case x in y| =(%s)) true;; esac",
    "case x in y |=(%s)) true;; esac",
    "case x in (y)|=(%s)) true;; esac",
    "case x in (y) | =(%s)) true;; esac",
    "case x in =(true)|=(%s)) true;; esac",
    "case x in =(%s)|y) true;; esac",
    "case x in y) ;; =(%s)) true;; esac",
    "case x in y) ;& =(%s)) true;; esac",
    "case x in y) ;| =(%s)) true;; esac",
    "case x in\n=(%s)) true;; esac",
    "case x in =(true\n%s)) true;; esac",
    "case x in =( (%s) )) true;; esac",
    "case x in =(case y in y) %s;; esac)) true;; esac",
)
CASE_ANGLE_FORMS = (
    # `<( ... )` and `>( ... )` anywhere in the case's word or a pattern, inside a group too: its list parsed as commands
    "case x in <(%s)) true;; esac",
    "case <(%s) in x) true;; esac",
    "case x in >(%s)) true;; esac",
    "case >(%s) in x) true;; esac",
    "case x in a<(%s)) true;; esac",
    "case x in a>(%s)) true;; esac",
    "case x in y|<(%s)) true;; esac",
    "case x in (<(%s))) true;; esac",
    "case x in (y|<(%s))) true;; esac",
    "case x in (y|>(%s))) true;; esac",
    "case x in ((y)|<(%s))) true;; esac",
    "case x in ( <(%s) ) true;; esac",
    "case x in (y|<(%s)) true;; esac",
    "case x in (y|<(%s))|z) true;; esac",
    "case <(%s)q in *) true;; esac",
    "case (y|<(%s)) in *) true;; esac",
)
# `=( ... )` opening the content of a pattern's optional parentheses: zsh lexed that content as the pattern's text, and
# runs the list up to its first `)` with the pattern's `(` and `|` taken out, so the list holds no pipe and no redirection
CASE_OPTIONAL_FORMS = (
    "case x in ( =(%s) ) true;; esac",
    "case x in (=(%s) ) true;; esac",
    "case x in ( =(%s)) true;; esac",
    "case x in (=(%s)|y) true;; esac",
    "case x in ( =(%s)|y ) true;; esac",
    "case x in (=(%s) | y) true;; esac",
    "case x in y) ;; ( =(%s) ) true;; esac",
    "case x in ( =(%s) <(true) ) true;; esac",
)
# a body after a pattern or a word holding a substitution
CASE_SUBSTITUTION_BODIES = (
    "case x in =(true)) %s;; esac",
    "case =(true) in x) %s;; esac",
    "case x in <(true)) %s;; esac",
    "case <(true) in x) %s;; esac",
    "case x in >(true)) %s;; esac",
    "case x in y|=(true)) %s;; esac",
    "case x in =(true)|x) %s;; esac",
    "case x in =(true)x|x) %s;; esac",
    "case x in (y|<(true))) %s;; esac",
    "case x in (y|<(true)) %s;; esac",
    "case x in ( =(true) ) %s;; esac",
    "case x in ( =(true) <(true) ) %s;; esac",
    "case x in =(case y in y) true;; esac)) %s;; esac",
    "case x in y) ;; =(true)) %s;; esac",
    "case (y|<(true)) in x) %s;; esac",
)


class CaseSubstitutionTest(BashHookCase):
    """SPD-184, filed by SPD-181's engineer: mark_zsh_patterns read zsh's `=(` as a plain parenthesis, and ShellWalk took a
    `(` in a case's word or pattern for the pattern's optional parenthesis and discarded every word before its `)` as
    pattern text, so the list of a `=( ... )` there was never read.  A `<( ... )` or `>( ... )` there was read, but the
    marking took its `)` for the pattern's own and read the arm's body outside command position.  The proposer's evidence:
    `case x in =(git push)) true;; esac` and `case =(git push) in x) true;; esac` recorded no finding (Law 7 for members).

    Probed 2026-09-23 through tests/probes/shell_probe.py, TMPPREFIX in the probe's directory, in zsh 5.9
    (arm64-apple-darwin26.0) under -f -o nobareglobqual and under -f, which printed the same for every line, and in GNU bash
    3.2.57, each line an eval with a file touched in the list (`case x in =(touch r01)) echo m01;; esac`):

    - zsh runs a `=( ... )` opening the case's word (`case =(touch r02) in x)`, `case =(touch r63)x in *)`) or a top-level
      alternative of a pattern: after `in`, `;;`, `;|` or a newline, and after a bar with or without blanks (`y|=(...)`,
      `x| =(...)`, `x |=(...)`, `(x)|=(...)`, `(x) | =(...)`, `=(touch r80)|=(touch r81)` made both).  Its list is parsed as
      commands: `x|=(tou|ch r100))` ran `tou` and `ch`, `=(true|touch r101))` made r101, and `=(case y in y) touch r17;;
      esac))` and `=( (touch r18) ))` made theirs.  A pattern tests its alternatives in turn and stops at the first match
      (`case x in x| =(touch r48))` made nothing, with the word q it made r48), and after `;&` the next body runs untested;
      the hook reads every alternative;
    - zsh runs a `=( ... )` opening the content of the pattern's optional parentheses (`( =(touch r09) )`, `(=(touch r45) )`,
      `( =(touch r46))`, `(=(touch r61)|x)`, `(=(touch r65) | x)`, `( =(touch r40)|x )`, after `;;` too), and in a group
      that holds a `<( )` (`( =(touch r82) <(true) )`).  That content is the pattern's text: the list ends at its first `)`
      and loses the pattern's `(` and `|` (`( =(tou|ch r90) )` made r90, `( =(touch (r97|r98)) )` made r97r98, `( =(git
      (push|)) )` ran git), a newline before it is no blank (`(<newline>=(touch r83) )` made nothing), and only the first
      element counts (`( =(touch r105)|=(touch r106) )` made r105 alone);
    - zsh leaves `=` elsewhere as pattern text: inside a group (`(x|=(touch r11)))`, `((x)|=(touch r24)))`, `((=(touch
      r60)))`, `( ( =(touch r59) ) )`), in a group that is the pattern itself (`(=(touch r08)))`, `( =(touch r89) )|x)`),
      after the first element of the optional parentheses (`( x|=(touch r43) )`, `( x | =(touch r52) )`, `(x|=(touch r62))
      echo`), glued after other text (`a=(touch r10))`, `x|a=(touch r25))`, `case a=(touch r31) in`), in a later
      alternative's group (`y|(=(touch r67)))`, `y|( =(touch r68) ))`), and in the case word's group (`case (=(touch r33))
      in`);
    - zsh runs `<( )` and `>( )` anywhere in the word or a pattern, groups included (`(<(touch r26)))`, `(x|<(touch r27)))`,
      `(x|>(touch r28)))`, `a>(touch r29))`, `( <(touch r51) )`, `(x|<(touch r70))`, `((x)|<(touch r73)))`, `case <(touch
      r78)q in`, `case (y|<(touch r112)) in`), its list parsed as commands (`(x|<(tou|ch r102)))` ran `tou` and `ch`);
    - the pattern goes on after the substitution and the body after it runs in command position: `=(true)|x) echo m15`,
      `=(true)x|x) echo m19`, `x|=(true)) (echo m20)`, `case =(true) in *) (echo m110)`, `(q|<(true)) (echo m72)`, `(q|<(true)))
      (echo m77)`, `( =(true) ) ;; q) (echo m111)` and `=(case y in y) true;; esac)) ;; *) (echo m115)` each printed theirs;
    - `$( )`, backticks and a `$( )` in `${z:-$( )}` or in a pattern's group ran, as they always were read; zsh 5.9 has no
      `${ list; }` (bad substitution);
    - bash rejects a `=( )` and a group in a case, and runs `<( )` and `>( )` in the word and outside a group in a pattern.

    AGENT_A plans tests/** and bin/spud; AGENT_C plans home:**."""

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

    def law_7(self, line):
        """A member is refused the push, and the analysis finds it; Spud is never refused git."""
        with self.subTest(line=line):
            self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
            self.assertRefused(line, "Law 7")
            self.assertRefused(line, "Law 7", AGENT_C)
            self.assertSilent(line, agent_id=None)

    def ledger_write(self, line):
        """Refused to both members for the generated ledger file, and to Spud on Law 1."""
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(line=line, agent_id=agent_id):
                self.assertRefused(line, "generated", agent_id)
        with self.subTest(line=line, agent_id="spud"):
            self.assertRefused(line, "Law 1", agent_id=None)

    def every_payload(self, form, lists):
        """Law 7, Law 6, Law 5's --as, the database and Law 1 for both members; for Spud, the checks that apply to him."""
        home, spud = self.home.path, self.spud_cli
        self.assertPayloadsAnswered(form, (("git push", "Law 7"), ("%s ticket new --title x" % spud, "Law 6"),
                                           ("%s --as spud member log hi" % spud, "Law 6"),
                                           ("%s --as %s member log hi" % (spud, AGENT_B), "--as"),
                                           ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly")) + lists,
                                    (("%s --as %s member log hi" % (spud, AGENT_A), "--as"),
                                     ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"))
                                    + tuple((command, "Law 1") for command, _needle in lists))

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_is_read_as_zsh_runs_it(self):
        for line in ("case x in =(git push)) true;; esac", "case =(git push) in x) true;; esac"):
            self.law_7(line)
            # both readings take the `=( ... )` for the process substitution it is, as a `<( ... )` hands its command a
            # file name: bash rejects the line
            with self.subTest(line=line):
                self.assertEqual(self.m.mark_zsh_patterns(line), (line.replace("=(", "<("),) * 2)

    # -- the hole -----------------------------------------------------------------------------------------------------
    def test_every_spelling_that_runs_its_list(self):
        for form in CASE_EQUALS_FORMS + CASE_ANGLE_FORMS + CASE_OPTIONAL_FORMS:
            self.law_7(form % "git push")

    def test_every_payload_in_a_list_parsed_as_commands(self):
        writes = (("echo x > ledger/tickets/SPD-001.md", "generated"), ("echo x | tee ledger/tickets/SPD-001.md", "generated"))
        for form in ("case x in =(%s)) true;; esac", "case =(%s) in x) true;; esac", "case x in y| =(%s)) true;; esac",
                     "case x in y) ;; =(%s)) true;; esac", "case x in (y|<(%s))) true;; esac", "case <(%s) in x) true;; esac"):
            self.every_payload(form, writes)

    def test_every_payload_in_the_optional_parentheses(self):
        """No pipe and no redirection reaches a list there, so the writes are by argument."""
        writes = (("touch ledger/tickets/SPD-001.md", "generated"), ("rm ledger/tickets/SPD-001.md", "generated"))
        for form in ("case x in ( =(%s) ) true;; esac", "case x in (=(%s)|y) true;; esac",
                     "case x in ( =(%s) <(true) ) true;; esac"):
            self.every_payload(form, writes)

    def test_the_optional_parentheses_list_as_zsh_takes_it(self):
        """The list ends at its first `)` and loses the pattern's `(` and `|`: `gi|t push` runs git push (probed: `( =(tou|ch
        r90) )` made r90), and `echo x|tee FILE` runs one echo, which writes nothing."""
        for line in ("case x in ( =(gi|t push) ) true;; esac", "case x in ( =(git (push|)) ) true;; esac",
                     "case x in (=(g|it p|ush)|y) true;; esac"):
            self.law_7(line)
        line = "case x in ( =(echo x|tee ledger/tickets/SPD-001.md) ) true;; esac"
        for agent_id in (AGENT_A, AGENT_C, None):
            with self.subTest(line=line, agent_id=agent_id):
                self.assertSilent(line, agent_id)

    def test_every_body_after_a_substitution(self):
        for form in CASE_SUBSTITUTION_BODIES:
            for body in ("( {git push} )", "{( git push )}", "{git push}", "git push"):
                self.law_7(form % body)
            line = form % ("tee " + self.TARGET)
            with self.subTest(line=line):
                self.assertIn(self.TARGET, [self.m.deglob(t) for t, _c in self.analysis(line).redirects])
            self.ledger_write(line)

    def test_every_enclosing_text(self):
        for form in ("eval 'case x in =(%s)) true;; esac'", "x=$(case =(%s) in (x) true;; esac)",
                     "echo `case x in y|=(%s)) true;; esac`", "zsh -f -c 'case x in ( =(%s) ) true;; esac'",
                     "f() { case x in =(%s)) true;; esac }; f", "if true; then case =(%s) in x) true;; esac; fi",
                     "case y in y) case x in =(%s)) true;; esac;; esac", "{ case x in (y|<(%s))) true;; esac }",
                     "for f (a) case x in y|=(%s)) true;; esac", "time case x in =(%s)) true;; esac"):
            self.law_7(form % "git push")

    def test_the_path_rule_in_the_list(self):
        for form in ("case x in =(%s)) true;; esac", "case =(%s) in x) true;; esac", "case x in (y|<(%s))) true;; esac",
                     "case x in ( =(%s) ) true;; esac"):
            for write in ("touch note.txt", "rm -rf docs"):
                line = form % write
                with self.subTest(line=line):
                    self.assertRefused(line, "deliverables")
                    self.assertSilent(line, AGENT_C)
                    self.assertRefused(line, "Law 1", agent_id=None)
            line = form % "touch tests/zzone/k.py"
            with self.subTest(line=line):
                self.assertSilent(line)
                self.assertSilent(line, AGENT_C)

    def test_a_substitution_leaves_the_body_its_own_substitutions(self):
        """The file name a `<( )` hands its command stood among the pattern's words, which the walk discards at the
        pattern's `)`: it took the first `$( )` of the body there, read before the body's cd, and after a bar the walk ran
        the pattern's words as a command named by that file name."""
        body = "cd ledger && echo $(echo x > tickets/SPD-001.md)"
        for pattern in ("<(true))", ">(true))", "=(true))", "( =(true) )", "(y|<(true))"):
            self.ledger_write("case x in %s %s;; esac" % (pattern, body))
        for line in ("case x in y) ;; <(true)|x) true;; esac", "case x in y) ;; >(true)|x) true;; esac",
                     "case x in y) ;; =(true)|x) true;; esac", "case x in y) ;; ( =(true) |x) true;; esac"):
            for agent_id in (AGENT_A, AGENT_C, None):
                with self.subTest(line=line, agent_id=agent_id):
                    self.assertSilent(line, agent_id)

    # -- controls -----------------------------------------------------------------------------------------------------
    def test_a_pattern_s_plain_text_runs_nothing(self):
        """zsh reads these `=(` as pattern text and runs nothing (probed; see the class).  `((y)|=(git push)))` is one
        too, but the other reading restores a group that opens a pattern's word, and a `(git push)` after the `(y)` it
        closes is a subshell there, with `=` or without (SPD-181): bash rejects the line."""
        for line in ("case x in a=(git push)) true;; esac", "case x in (y|=(git push))) true;; esac",
                     "case x in (=(git push))) true;; esac",
                     "case x in ((=(git push))) true;; esac", "case x in ( ( =(git push) ) ) true;; esac",
                     "case x in ( y|=(git push) ) true;; esac", "case x in ( y | =(git push) ) true;; esac",
                     "case x in (y | =(git push)) true;; esac", "case x in (y|=(git push)) true;; esac",
                     "case x in y|a=(git push)) true;; esac", "case x in y|(=(git push))) true;; esac",
                     "case x in y|( =(git push) )) true;; esac", "case x in ( =(git push) )|y) true;; esac",
                     "case x in ( =(true)|=(git push) ) true;; esac", "case a=(git push) in *) true;; esac",
                     "case (=(git push)) in *) true;; esac", "case x in (\n=(git push) ) true;; esac"):
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).findings, [])
                self.assertSilent(line)
                self.assertSilent(line, AGENT_C)

    def test_the_substitutions_already_read(self):
        """`$( )` and backticks were lifted out of every word before the walk, a case's word and patterns included."""
        for form in ("case x in $(%s)) true;; esac", "case $(%s) in x) true;; esac", "case x in `%s`) true;; esac",
                     "case ${z:-$(%s)} in *) true;; esac", "case x in (y|$(%s))) true;; esac",
                     "case x in ${z:-`%s`}) true;; esac"):
            self.law_7(form % "git push")

    @wall_clock
    def test_bounded_on_pathological_input(self):
        for line in ("case x in " + "=(true)) true;; " * 2000 + "=(true)) ( {git push} );; esac",
                     "case x in " + "y|" * 5000 + "=(git push)) true;; esac",
                     "case x in " + "=(" * 3000 + "git push" + ")" * 3000 + ") true;; esac",
                     "case x in (" + "y|<(true)" * 2000 + ")) ( {git push} );; esac",
                     "case " + "<(true)" * 2000 + " in x) ( {git push} );; esac",
                     "case x in ( =(git push) " + "|y" * 5000 + " ) true;; esac",
                     "case x in " + "( =(true) ) true;; " * 2000 + "( =(git push) ) true;; esac",
                     "case x in ( =(" + "(" * 3000 + "git push" + " " * 3000 + ") ) true;; esac",
                     "case x in " + "=(" * 3000 + "git push"):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = self.analysis(line)
                self.assertLess(time.monotonic() - started, 5.0)
                self.assertIn(("git", ("push", "push")), a.findings, line[:40])


class CasePatternAlternativeTest(BashHookCase):
    """SPD-187, filed by SPD-181's engineer: ShellWalk read a `|` in a case pattern as a pipeline operator and analysed the
    words before it as a command -- for the first pattern the case's word and `in` among them -- so `case $1 in a|b) echo;;
    esac` was refused to a member as a command word from a variable, and `case x in a) ;; git|b) echo;; esac` recorded a git
    command.  A newline after `case word in` did the same with the word and `in`.

    Probed 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 -f -o nobareglobqual, zsh 5.9 -f and bash 3.2.57,
    with `touch` a function that names itself on stderr: `case q in touch|b) echo m1;; esac` and `case q in a) ;; touch|b)
    echo m2;; esac` ran nothing; `case q in a|$(touch s3))` and ``a|`touch s4` `` ran their substitutions; `case q in a|q)
    echo m5;; esac | cat; echo after5` printed both; `case q in<newline>  a|b) ... ;;<newline>  *) echo star6 ;;<newline>esac`,
    `a | q )`, `(a|q)` and `case q<newline>in a|q)` matched in all three shells.

    zsh's brace form, `case word { ... }`, places its patterns since SPD-185 (CaseBraceFormTest): a `|` in one is an
    alternative there too, and the case closes at its `}`."""

    def setUp(self):
        super().setUp()
        self.m = load_spud_module()

    def findings(self, line):
        return self.m.analyse_command(line, self.m.ShellAnalysis(cwd=str(self.home.path))).findings

    def law_7(self, line):
        with self.subTest(line=line):
            self.assertIn(("git", ("push", "push")), self.findings(line))
            self.assertRefused(line, "Law 7")
            self.assertSilent(line, agent_id=None)

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_lines_record_no_command(self):
        self.assertNotIn(("var", "$1"), self.findings("case $1 in a|b) echo;; esac"))
        self.assertEqual([f for f in self.findings("case x in a) ;; git|b) echo;; esac") if f[0] == "git"], [])
        for line in ("case $1 in a|b) echo;; esac", "case x in a) ;; git|b) echo;; esac"):
            for agent_id in (AGENT_A, None):
                with self.subTest(line=line, agent_id=agent_id):
                    self.assertSilent(line, agent_id)

    def test_no_pattern_word_is_a_command(self):
        for line in ("case $1 in a|b|c) echo;; esac", "case $1 in a | b ) echo;; esac", "case $1 in (a|b) echo;; esac",
                     "case \"$1\" in git|push) echo;; esac", "case $1 in a) ;; $2|b) echo;; esac",
                     "case $1 in a) true;& git|b) echo;; esac", "case $1 in a) true;| git|b) echo;; esac",
                     "case $1 in\n  a|b) echo ;;\n  git|c) echo ;;\nesac", "case $1 in\n\n  a|b) echo ;;\nesac",
                     "case $1\nin a|b) echo;; esac", "case $1\n\nin a|git) echo;; esac",
                     "case x in (x|y)|git) echo;; esac", "case $1 in a|b) echo;; esac; case $2 in c|d) echo;; esac",
                     "if true; then case $1 in a|b) echo;; esac; fi", "f() { case $1 in a|b) echo;; esac }; f x"):
            with self.subTest(line=line):
                found = self.findings(line)
                self.assertNotIn(("var", "$1"), found)
                self.assertNotIn(("var", "$2"), found)
                self.assertEqual([f for f in found if f[0] == "git"], [])
                self.assertSilent(line)

    # -- what still runs ----------------------------------------------------------------------------------------------
    def test_a_substitution_in_an_alternative_is_read(self):
        for line in ("case x in a|$(git push)) echo;; esac", "case x in a|`git push`) echo;; esac",
                     "case x in $(git push)|a) echo;; esac", "case x in a) ;; b|$(git push)|c) echo;; esac",
                     "case x in (a|$(git push)) echo;; esac", "case x in a|<(git push)) echo;; esac",
                     "case x in y) ;; b|=(git push)) true;; esac", "case $(git push) in a|b) echo;; esac",
                     "case x in\n  a|$(git push)) echo ;;\nesac"):
            self.law_7(line)

    def test_the_body_and_what_follows_esac_are_read(self):
        for line in ("case $1 in a|b) git push;; esac", "case $1 in a|b) echo;; c|d) git push;; esac",
                     "case $1 in (a|b) git push;; esac", "case $1 in a | b ) git push;; esac",
                     "case $1 in a|b) echo;; esac; git push", "case $1 in a|b) echo;; esac | git push",
                     "case $1 in a|b) echo;; esac && git push", "case $1 in a|b) echo;; esac\ngit push",
                     "case $1 in\n  a|b) echo ;;\nesac\ngit push", "echo x | case $1 in a|b) git push;; esac",
                     "case $1 in a|b) echo;; esac | cat | git push", "{ case $1 in a|b) echo;; esac } | git push",
                     "case $1 in a|b) case $2 in c|d) echo;; esac;; esac; echo | git push"):
            self.law_7(line)

    def test_a_case_with_no_pattern_closes_at_its_esac(self):
        # `case q in esac | echo e1`, `case q in esac; echo e2`, `case q in<newline>esac | echo e3` and `case q<newline>in
        # esac && echo e4` printed theirs in zsh 5.9 -f, -f -o nobareglobqual and bash 3.2
        for line in ("case x in esac | git push", "case x in esac; echo in | git push", "case x in\nesac | git push",
                     "case x\nin esac && echo in | git push", "case x in esac; echo in|git push",
                     "(case x in esac) | git push", "case $1 in esac; case $2 in a|b) echo;; esac | git push"):
            self.law_7(line)

    def test_the_brace_form_places_its_patterns(self):
        # SPD-185: `case word { ... }` closes at its `}`, and what follows it is read as commands; a `|` in its patterns
        # is an alternative, no pipe, the case's word no command
        for line in ("case x { x) ;; }; echo a | git push", "case x { x|y) ;; }; echo a | git push",
                     "case x { in) ;; }; echo a | git push", "case <(true) { in|y) ;; }; echo a | git push"):
            self.law_7(line)
        for line in ("case $1 { a|b) echo;; }", "case x { a) ;; git|b) echo;; }", "case <(true) { git|b) echo;; }"):
            with self.subTest(line=line):
                found = self.findings(line)
                self.assertNotIn(("var", "$1"), found)
                self.assertEqual([f for f in found if f[0] == "git"], [])
                self.assertSilent(line)

    def test_a_pattern_the_reader_cannot_place_stays_refused(self):
        # a case pattern's `)` inside a `$( )` ends the substitution for split_substitutions: unread, refused to a member
        for line in ("x=$(case $1 in a|b) echo;; esac)", "echo $(case x in a|b) echo;; esac)"):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line).decision, "deny")


# SPD-185: zsh's brace form of case, `%s` an arm's body.  With `( echo <label> )` or `echo <label>` in the slot each
# printed its label in zsh 5.9 -f and -f -o nobareglobqual (tests/probes/shell_probe.py, 2026-09-24), and bash 3.2.57
# rejected every one near its `{`.  The `{` is the case's `in` -- glued to the first pattern too (`{x)`, `{(x)`) and after
# a newline -- and the `}` its esac: a body may end at it with no `;;` (`x) echo B3 }`, and glued, `x) echo A24}`), and at a
# pattern's place either closer ends either form (`case x { x) echo A13 ;; esac` and `case x in x) echo A14;; }` ran).
CASE_BRACE_FORMS = (
    "case x { x) %s;; }",
    "case x { ((x)) %s;; }",
    "case x { x) %s }",
    "case x { y) true;; x) %s }",
    "case x { x) %s;; y) true;; }",
    "case x {\n  x) %s ;;\n}",
    "case x\n{ x) %s;; }",
    "case x {\n  x) %s\n}",
    "case x { x|y) %s;; }",
    "case x { (x) %s;; }",
    "case x { x) true;& y) %s;; }",
    "case x { x) true;| x) %s;; }",
    "case x {x) %s;; }",
    "case x {(x) %s;; }",
    "case x {x|y) %s;; }",
    "case x { x) %s;;}",
    "case x { x) %s; }",
    "case {x} { {x}) %s;; }",
    "case x { in) true;; x) %s;; }",
    "case x { x) %s;; esac",
    "case x in x) %s;; }",
    "{ case x { x) %s;; } }",
    "case x { x) case y { y) %s;; };; }",
    "echo in | case x { x) %s;; }",
    "case x { x) %s;; } | cat",
    "case x { x) %s } && true",
)
# ... and what follows such a case, `%s` the command after it: each ran (`case x { x) ;; }; ( echo SUB3 )`, `case x { y)
# echo NO;; } ; ( echo SUB61 )`, `case x { x) echo A64 ;;<newline>}; (echo SUB64)`, `case x { x) echo A26 } && echo
# after26`, `case x { x) echo A56 } | cat; echo after56`).
CASE_BRACE_AFTER = (
    "case x { x) ;; }; %s",
    "case x { y) true;; } ; %s",
    "case x { x) true ;;\n}; %s",
    "case x { x) true ;;\n}\n%s",
    "case x { x) true } && %s",
    "case x { x) true } | cat; %s",
    "case x { }; %s",
    "case x {}; %s",
    "case x { x) }; %s",
    "case x in x) true;; }; %s",
)


class CaseBraceFormTest(BashHookCase):
    """SPD-185, filed by SPD-181's engineer: zsh takes `{ ... }` in place of `in ... esac`, and mark_zsh_patterns waited for
    an `in` that never came, so it read no pattern and no body, while ShellWalk's case frame waited for an esac.  A
    subshell body there was one glob word in zsh's reading, and after such a case the walk stayed in a pattern, taking a
    later `( ... )` for a pattern's parentheses.  The proposer's evidence, on main and the SPD-181 tree: `case x { x) (
    {git push} );; }` and `case x { x) ;; }; (git push)` recorded no finding (Law 7 for members).

    Probed 2026-09-24 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, which printed the same for every line, and in GNU bash 3.2.57, which rejected every brace form near its
    `{` (CASE_BRACE_FORMS and CASE_BRACE_AFTER have the lines that ran).  Beside those: `case x { x) echo A28 } echo
    after28`, `case x { x) ;; } ( echo SUB62 )` and `case x { x) ( echo B36 );; } always { echo AL36 }` are parse errors,
    so the case ends at its `}`; in a body only the form's own closer ends it (`case x { x) echo B70; esac` and `case x in
    x) echo B71; }` failed near the closer, `case x { x) echo A41 esac }` printed `A41 esac`, and `case x in x) echo A40 }`
    failed), while at a pattern's place either does (`case x { x) echo B73;; esac }` failed near its `}`, `{ case x in x)
    echo A45;; } }` ran and `{ case x in x) echo A46;; }` failed); a comment's `}` closes nothing (`case x {<newline># (x)
    comment }<newline>x) ( echo B85 ) ;;<newline>}` ran B85)."""

    TARGET = "(ledger|x)/tickets/SPD-001.md"

    def setUp(self):
        super().setUp()
        home = self.home.path
        (home / "ledger" / "tickets").mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")
        self.m = load_spud_module()

    def analysis(self, command):
        return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path)))

    def law_7(self, line):
        """A member is refused the push, and the analysis finds it; Spud is never refused git."""
        with self.subTest(line=line):
            self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
            self.assertRefused(line, "Law 7")
            self.assertSilent(line, agent_id=None)

    def refused_everywhere(self, line):
        """Refused to a member for the ledger file, named as the line spells it, and to Spud on Law 1."""
        with self.subTest(line=line):
            self.assertIn(self.TARGET, [self.m.deglob(t) for t, _c in self.analysis(line).redirects])
            r = self.assertRefused(line, "generated")
            self.assertIn(self.TARGET, r.reason)
            self.assertRefused(line, "Law 1", agent_id=None)

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_is_read_as_zsh_runs_it(self):
        for line in ("case x { x) ( {git push} );; }", "case x { x) ;; }; (git push)", "case x { ((x)) ( {git push} );; }",
                     "case x { x) git push }"):
            self.law_7(line)
        # the subshell's parentheses stay the shell's, and the case's braces stand apart
        marked, _other = self.m.mark_zsh_patterns("case x { x) ( {git push} );; }")
        self.assertEqual((marked.count("("), marked.count(")")), (1, 2))
        self.assertEqual(self.m.mark_zsh_patterns("case x {x) ( {git push} );; }")[0].split()[:4], ["case", "x", "{", "x)"])

    # -- the hole -----------------------------------------------------------------------------------------------------
    def test_every_form_reads_its_body(self):
        for form in CASE_BRACE_FORMS:
            for body in ("( {%s} )", "{( %s )}", "( %s )", "%s"):
                self.law_7(form % (body % "git push"))
            self.refused_everywhere(form % ("echo x | tee " + self.TARGET))

    def test_what_follows_the_case_is_read(self):
        for form in CASE_BRACE_AFTER:
            for command in ("( {git push} )", "(git push)", "{( git push )}", "git push"):
                self.law_7(form % command)
            self.refused_everywhere(form % ("echo x | tee " + self.TARGET))

    def test_a_group_in_a_body_closes_before_the_case(self):
        """A `{ list }` in an arm's body closes at its own `}`, and the case at the next (probed: `case x { x) { echo A15 }
        ;; }` and `case x { x) { echo A37 };; }` ran)."""
        for line in ("case y { x) { true } ;; y) ( {git push} );; }", "case x { x) { true }; ( {git push} ) }",
                     "case x { x) {true} } ; ( {git push} )", "case x { x) f() { true }; ( {git push} ) }",
                     "case x { x) if true; then { true }; fi; ( {git push} ) }", "case x { x) { { true } } ;; }; (git push)"):
            self.law_7(line)

    # -- controls -----------------------------------------------------------------------------------------------------
    def test_a_pattern_runs_nothing(self):
        for line in ("case x { (git push)) true;; }", "case x { git|push) true;; }", "case x { x) ;; (git push)) true;; }",
                     "case x {(git push)) true;; }", "case x { x(e:'git push':)) true;; }"):
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).findings, [])
                self.assertSilent(line)

    def test_the_posix_form_keeps_its_reading(self):
        for line in ("case x in x) echo a;; esac", "case x in (x) echo a;; esac", "case x in x|y) true;; esac"):
            with self.subTest(line=line):
                self.assertEqual(self.m.mark_zsh_patterns(line), (line, line))
                self.assertEqual(self.analysis(line).findings, [])
                self.assertSilent(line)
        for line in ("case x in x) git push;; esac", "case x in {x) git push;; esac", "case x in x) { git push } ;; esac"):
            self.law_7(line)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        for line in ("case x { " + "x) true;; " * 2000 + "x) ( {git push} );; }",
                     "case x { x) " + "{ " * 2000 + "true" + " }" * 2000 + ";; }; ( {git push} )",
                     "case x { x) " * 1000 + "( {git push} )" + " }" * 1000,
                     "case x {\n" + "x) true;;\n" * 2000 + "}; ( {git push} )",
                     "case x {" + "{" * 3000 + "x) ( {git push} );; }"):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = self.analysis(line)
                self.assertLess(time.monotonic() - started, 5.0)
                self.assertIn(("git", ("push", "push")), a.findings, line[:40])


# SPD-186: a comment in a case statement, `%s` an arm's body.  Each ran its body (`( echo <label> )` or `echo <label>` in
# the slot) in zsh 5.9 -f, -f -o nobareglobqual and bash 3.2.57 (tests/probes/shell_probe.py, 2026-09-24), the brace forms
# in zsh alone: a word that starts with `#` after a blank, a newline, `;`, `|`, `&`, `(`, `)` or `>` is a comment to the end
# of its line, and its words are no pattern, no closer and no command.
CASE_COMMENTS = (
    "case x in\n  a) true ;;  # (see below)\n  x) %s ;;\nesac",
    "case x in\n  a) true ;;\n  # (a) or (b)\n  x) %s ;;\nesac",
    "case x in # (y)\n x) %s;; esac",
    "case x # (y)\nin x) %s;; esac",
    "case x in x) %s # )\n;; esac",
    "case x in\n#c (x)\nx) %s;; esac",
    "case x in x)#c (y)\n %s;; esac",
    "case x in y) true;;#c (y)\nx) %s;; esac",
    "case x in # esac\nx) %s;; esac",
    "case x in y) true ;; # esac\nx) %s;; esac",
    "case x in x) %s # ;;\n;; esac",
    "case x in x) %s # esac\n;; esac",
    "case x { x) %s ;; # }\n}",
    "case x {\n# (x) comment }\nx) %s ;;\n}",
)
# ... and a comment that spells a case, a closer or a group outside one, `%s` the command on the next line: each ran it.
COMMENT_LINES = (
    "echo a # ; case x in\n%s",
    "echo a # ; case x {\n%s",
    "{ echo a # }\n%s; }",
    "if true; then %s # ; fi\nfi",
    "echo a;#b (\n%s",
    "(#c (\n%s)",
    "echo a|#b (\n%s",
    "echo a&#b (\n%s",
    "x=1 # (\n%s",
    "case x in x) true;; esac # (\n%s",
    "echo a # )\n%s",
)


class CaseCommentTest(BashHookCase):
    """SPD-186, filed by SPD-181's engineer: newlines_as_separators keeps a comment's words, and mark_zsh_patterns and
    ShellWalk read them as the line's own: in a case statement a comment holding a parenthesised word ended the pattern
    early, the real pattern after it was read as a body, and the arm's own body stood outside command position, so a
    subshell there was one glob word in zsh's reading.  The proposer's evidence, on main and the SPD-181 tree: with `a)
    true ;;  # (see below)`, or `# (a) or (b)`, on the line before `b) ( {git push} ) ;;` no finding.  The same reading
    reached past case statements: `echo a # ; case x in` opened a case on a comment's words, so a `( {git push} )` on the
    next line was a pattern, and `case x in x) git push # )` ended the arm's pattern at the comment's `)`, discarding
    the push.

    Probed 2026-09-24 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, which printed the same, and in GNU bash 3.2.57: CASE_COMMENTS and COMMENT_LINES ran their bodies;
    `echo a #b ; echo NO12` printed a alone, `echo a#b; echo C13` printed `a#b` and C13; `case x in x|#y) echo C7;;
    esac` and `echo a >#b` failed in both at the end of the line, the `#` opening a comment there too; and zsh read `case x
    in (#c<newline>x) echo C19;; esac` as a pattern, with no error and no C19, where bash failed near the newline."""

    def setUp(self):
        super().setUp()
        self.m = load_spud_module()

    def analysis(self, command):
        return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path)))

    def law_7(self, line):
        with self.subTest(line=line):
            self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
            self.assertRefused(line, "Law 7")
            self.assertSilent(line, agent_id=None)

    def silent(self, line):
        with self.subTest(line=line):
            self.assertNotIn("git", [kind for kind, _d in self.analysis(line).findings])
            self.assertSilent(line)
            self.assertSilent(line, agent_id=None)

    def test_the_tickets_evidence_is_read_as_zsh_runs_it(self):
        for line in ("case b in\n  a) true ;;  # (see below)\n  b) ( {git push} ) ;;\nesac",
                     "case b in\n  a) true ;;\n  # (a) or (b)\n  b) ( {git push} ) ;;\nesac"):
            self.law_7(line)

    def test_every_comment_in_a_case(self):
        for form in CASE_COMMENTS:
            for body in ("( {%s} )", "{( %s )}", "%s"):
                self.law_7(form % (body % "git push"))

    def test_a_comment_outside_a_case(self):
        for form in COMMENT_LINES:
            for command in ("( {git push} )", "{( git push )}", "git push"):
                self.law_7(form % command)

    def test_a_comment_runs_nothing(self):
        """zsh and bash ignore what a comment holds, operators and all (`echo a #b ; echo NO12` printed a alone)."""
        for line in ("echo a # ; git push", "echo a #b ; git push", "true;# git push", "# ( {git push} )",
                     "case x in x) true;; # ( {git push} )\nesac", "case x { x) true;; # ; git push\n}",
                     "echo a # | sh", "echo 'git push' # | sh"):
            self.silent(line)

    def test_what_is_no_comment_is_read(self):
        """A `#` inside a word, and zsh's `(#` in a case pattern, open no comment (probed: `echo a#b; echo C13` and
        `case x in (#c<newline>x) echo C19;; esac`, which zsh read as a pattern); a substitution in a comment is read as
        before, which runs nothing and fails closed."""
        for line in ("echo a#b; git push", "echo ${x#y}; git push", "case x in (#c\nx) git push;; esac",
                     "setopt extendedglob; case X in (#i)x) ( {git push} );; esac", "echo a # $(git push)"):
            self.law_7(line)
        # zsh's `(#i)` in `[[ ... ]]` is a pattern's (`setopt extendedglob; [[ x == (#i)X ]] && echo L6` printed L6); a
        # group zsh's lexer reads on in, which the reading cannot place, keeps what follows read as before (both lines a
        # parse error to zsh's eval, which ran nothing); and `{#c` is read as the words it spells (zsh ran `{#c<newline>echo
        # L10 }` as a group, bash ran a command named `{#c`)
        for line in ("[[ x == (#i)X ]] && git push", "setopt extendedglob; [[ x == (#i)X ]] # ; git push\ngit push",
                     "echo (a #b ; git push\n)", "echo (a|#b ; git push\n)", "echo a(#b ; git push\n)", "{#c\ngit push }"):
            self.law_7(line)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        for line in ("case x in " + "# (x)\n" * 3000 + "x) ( {git push} );; esac",
                     "echo a # " + "(" * 5000 + "\n( {git push} )",
                     "case x in x) true;; " + "#c (\n" * 3000 + "x) ( {git push} );; esac"):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = self.analysis(line)
                self.assertLess(time.monotonic() - started, 5.0)
                self.assertIn(("git", ("push", "push")), a.findings, line[:40])


class NamedCoprocTest(BashHookCase):
    """SPD-060: bash 4 and later accept a name before a coproc's compound command, `coproc NAME compound_command`, and run
    the group in a forked shell; the hook read NAME for the command word, so `coproc NAME { git push; }` was kind other with
    no finding, while `coproc { git push; }`, `coproc git push`, `coproc ( git push )` and `coproc sh -c 'git push'` each
    found it.  No bash 4+ is on this Mac (bash 3.2 has no coproc and zsh's takes a command only), so the forms were probed in
    bash 5.2 in the ubuntu:24.04 podman image, with a file side effect, since the group's own stdout goes to the pipe:

    - `coproc NAME { echo ran >> /tmp/m; }` wrote the file, and so did the `( ... )`, `while`, `for`, `if`, `case` and
      `[[ ... ]]` forms after a name, a group nested in the group, the form with a redirection after it, and one inside a
      pipeline.  A name holding an expansion ran the group too (`N=NAME; coproc $N { ... }`, `${N}`, `$(echo NAME)`);
    - a name that is not a valid identifier ran nothing (`coproc 1bad { ... }` and `coproc na-me { ... }` printed "not a
      valid identifier"), and `coproc NAME{` with no blank is a syntax error;
    - `coproc NAME echo ran` and `coproc NAME sh -c '...'` printed "NAME: command not found": a simple command named NAME,
      not a named coproc.  That reading is left exactly as it was;
    - a cd inside the group never reaches the line (`coproc NAME { cd /tmp; pwd; }; wait; pwd` printed /), as SPD-054 already
      had it for the unnamed form;
    - zsh 5.9 is a parse error for every named form, so nothing runs there.

    The group after a valid identifier is now read exactly as the unnamed form's is, and a name the hook cannot resolve
    refuses a member as SPD-043's command word does.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        for rel in ("tests/keep.py", "ledger/tickets/SPD-001.md"):
            p = self.home.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)
        return r

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def test_the_tickets_evidence_command(self):
        self.refused_for_members("coproc NAME { git push; }")
        self.assertEqual(self.analysis("coproc NAME { git push; }").findings, [("git", ("push", "push"))])
        self.assertEqual(self.analysis("coproc NAME { git status; }").findings, [("git", ("status", None))])

    def test_every_compound_command_after_a_name(self):
        for cmd in ("coproc NAME { git push; }", "coproc NAME { git push }", "coproc NAME ( git push )",
                    "coproc NAME while true; do git push; done", "coproc NAME until false; do git push; done",
                    "coproc NAME for i in 1; do git push; done", "coproc NAME select f in a; do git push; done",
                    "coproc NAME if true; then git push; fi", "coproc NAME case x in x) git push;; esac",
                    "coproc NAME { { git push; } }", "coproc NAME { git push; } 2>/dev/null",
                    "coproc _n1 { git push; }", "coproc NAMe { git push; }", "coproc N2 { git push; }",
                    "coproc NAME { sh -c 'git push'; }", "coproc NAME { eval 'git push'; }",
                    "coproc NAME { trap 'git push' EXIT; }", "coproc NAME { for f in a; do git push; done; }"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_a_name_the_hook_cannot_resolve_is_refused(self):
        for cmd in ("coproc $N { git push; }", "coproc ${N} { git push; }", "coproc $(echo NAME) { git push; }",
                    "coproc `echo NAME` { git push; }"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "cannot resolve")
        # a name the line assigned for certain resolves, and the group behind it is read
        self.refused_for_members("N=NAME; coproc $N { git push; }")

    def test_the_payloads_inside_a_named_group(self):
        spud, home = self.spud_cli, self.home.path
        for agent_id in (AGENT_C, AGENT_A):
            self.assertRefused("coproc NAME { %s ticket new --title x; }" % spud, "Law 6", agent_id)
        # Spud's own call inside the group is answered as it is inside the unnamed form's (SPD-125)
        self.assertEqual(self.bash("coproc NAME { %s ticket new --title x; }" % spud, agent_id=None).decision,
                         self.bash("coproc { %s ticket new --title x; }" % spud, agent_id=None).decision)
        self.assertRefused("coproc NAME { %s --as %s member log hi; }" % (spud, AGENT_B), "--as", AGENT_A)
        self.assertRefused("coproc NAME { %s --as %s member log hi; }" % (spud, AGENT_A), "Law 5", agent_id=None)
        for agent_id in (AGENT_C, AGENT_A, None):  # the database is refused to everyone, Spud included
            self.assertRefused("coproc NAME { sqlite3 %s/.spud/ledger.db 'select 1'; }" % home, "spud sql --readonly", agent_id)
        self.assertRefused("coproc NAME { echo x > ledger/tickets/SPD-001.md; }", "generated")
        self.assertRefused("coproc NAME { echo x > ledger/tickets/SPD-001.md; }", "Law 1", agent_id=None)
        self.assertRefused("coproc NAME { echo x > note.txt; }", "deliverables")
        self.assertSilent("coproc NAME { echo x > note.txt; }", AGENT_C)

    def test_a_cd_in_the_group_never_reaches_the_line(self):
        """The forked shell's directory is not the line's, as SPD-054 has it for the unnamed form."""
        for cmd in ("coproc NAME { cd /tmp; }", "coproc NAME { cd /tmp }", "coproc NAME ( cd /tmp )",
                    "coproc NAME { cd /tmp; git status; }"):
            with self.subTest(cmd):
                self.assertEqual(self.analysis(cmd).cwds, frozenset([str(self.home.path)]))
        self.assertSilent("coproc NAME { cd /tmp; }; echo x > tests/keep.py")

    def test_the_named_group_is_walked_as_the_unnamed_one(self):
        """SPD-125: the group after a valid name opens SPD-081's frame, so a cd inside it moves the commands after it in the
        group, and a redirection target is placed where bash 4+ opens it, as the unnamed form's already was."""
        home = str(self.home.path)
        for line in ("coproc { cd /tmp; echo x > out.txt; }", "coproc NAME { cd /tmp; echo x > out.txt; }",
                     "coproc _n1 { cd /tmp; echo x > out.txt }"):
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).redirects, [("out.txt", frozenset(["/tmp"]))], line)
                self.assertEqual(self.analysis(line).cwds, frozenset([home]), line)
        self.assertEqual(self.analysis("time coproc NAME { cd /tmp; echo x > out.txt; }").redirects,
                         [("out.txt", frozenset(["/tmp"]))])
        for form in ("coproc { %s; }", "coproc NAME { %s; }"):
            with self.subTest(form=form):
                self.assertSilent(form % "cd tests; echo x > keep.py")  # tests/keep.py, inside AGENT_A's tests/**
                self.assertRefused(form % "cd docs; echo x > keep.py", "deliverables")  # docs/keep.py, outside
                self.assertSilent(form % "cd docs; echo x > keep.py", AGENT_C)

    def test_a_simple_command_named_by_the_word_is_read_as_before(self):
        """`coproc word args` is a command named `word` in every shell, and an invalid identifier runs nothing anywhere."""
        for ok in ("coproc NAME git push", "coproc NAME sh -c 'git push'", "coproc NAME echo x",
                   "coproc 1bad { git push; }", "coproc na-me { git push; }", "coproc NAME.x { git push; }",
                   "echo coproc NAME { git push; }", "grep -n 'coproc NAME' tests/keep.py",
                   "%s --as %s member log 'coproc NAME { git push; }'" % (self.spud_cli, AGENT_A)):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
        self.assertEqual(self.analysis("coproc NAME git push").findings, [])

    def test_the_unnamed_forms_are_unchanged(self):
        for refused in ("coproc { git push; }", "coproc ( git push )", "coproc git push", "coproc sh -c 'git push'",
                        "coproc repeat 1 git push", "coproc for f (a) git push", "coproc { trap 'git push' EXIT; }",
                        "coproc while true; do git push; done"):
            with self.subTest(refused):
                self.refused_for_members(refused)
        for cmd in ("coproc { cd /tmp; }", "coproc cd /tmp"):
            with self.subTest(cmd):
                self.assertEqual(self.analysis(cmd).cwds, frozenset([str(self.home.path)]))
        for ok in ("coproc { echo hi; }", "coproc echo hi", "coproc git status"):
            with self.subTest(ok):
                self.assertSilent(ok)


# zsh's prefixed groups (SPD-081): a `{ list }` whose `{` follows only `coproc`, `time` and `!`.  Each fills its `%s` with
# the body the group runs, and PREFIXED_GROUP_EVIDENCE is Spud's probe, line for line.
PREFIXED_GROUP_EVIDENCE = ("coproc { repeat 1 %s }", "coproc { repeat 1 %s; }", "coproc { if [[ -n x ]] %s }",
                           "coproc { for f (a) %s }", "time { repeat 1 %s; }", "! { repeat 1 %s; }",
                           "time { for f (a) %s }", "! { while [[ -n x ]] %s }", "time ! { repeat 1 %s }",
                           "! time { repeat 1 %s }", "time coproc { repeat 1 %s; }", "coproc time { repeat 1 %s; }",
                           "time { { repeat 1 %s } }")
# The forms a shell parses but does not run: read all the same, which costs a line no shell accepts.
PREFIXED_GROUP_PARSE_ERRORS = ("! coproc { repeat 1 %s }", "! ! { repeat 1 %s }")
# The prefixes whose group runs in a forked shell of its own, and those whose group runs in the shell that reads the line.
FORKED_GROUPS = ("coproc { %s }", "coproc { %s; }", "time coproc { %s; }", "coproc time { %s; }")
CURRENT_SHELL_GROUPS = ("time { %s; }", "! { %s; }", "time ! { %s; }", "! time { %s; }", "time { { %s; } }")


class PrefixedGroupTest(BashHookCase):
    """SPD-081 (from proposal 85): zsh runs a `{ list }` after `coproc`, `time` and `!`, and ShellWalk opened a group only
    where no word stood before the `{`; after one of those words the brace was appended as an ordinary word, analyse_words
    stripped it as the reserved word it is, and the rest of the line was read as one simple command.  A zsh short loop or
    short conditional inside such a group was flattened into that command's words and never checked, so `coproc { repeat 1
    git push }`, `time { repeat 1 git push; }` and `! { repeat 1 git push; }` were each kind other with no finding, while
    `{ repeat 1 git push }` found the push (Laws 1, 5, 6 and 7).  SPD-042 and SPD-061 had already opened a frame for `for`,
    `select`, `repeat`, `if`, `while` and `until` after those words; `{` was the one left.

    No shell is probed here: this worktree session's harness refuses to run one (SPD-094).  The evidence is Spud's probe of
    2026-09-17, recorded on the ticket -- zsh 5.9 -f and zsh -f -o nobareglobqual (this Mac's Bash tool), identical in both,
    with a function standing in for the VCS program that appends its arguments to a log, since a coproc's stdout goes to its
    pipe and only a file shows that the body ran:

    - the body ran for every form in PREFIXED_GROUP_EVIDENCE above, `coproc { repeat 1 vcs push }` and
      `time { { repeat 1 vcs push } }` included;
    - `! coproc { ... }` and `! ! { ... }` are parse errors and ran nothing.  The hook reads them as groups all the same,
      fail closed and at no real cost: the line is a shell error where it is not one of the forms that run;
    - the line's directory and variables: `time { x=1; cd /tmp; }`, `! { x=1; cd /tmp; }`, `time ! { x=1; cd /tmp; }` and
      `! time { x=1; cd /tmp; }` each left the line in /tmp with x=1 (the current shell), while `coproc { x=1; cd /tmp; }`,
      `time coproc { x=1; cd /tmp; }` and `coproc time { x=1; cd /tmp; }` left the line where it was with x unset (a fork,
      as SPD-054 has it, and as `coproc ( ... )` was already read).  In a pipeline, `time { x=1; cd /tmp; } | cat` and
      `! { x=1; cd /tmp; } | cat` left the line where it was, as `{ cd /tmp; } | cat` does -- the reading main had wrong,
      since it moved the line there.  (A zsh quirk the hook does not chase: `time { x=1; }` alone left x unset, while
      `time { x=1; cd /tmp; }` set it; a variable behind `time` is read as the current shell's, which is what `time x=1`
      already did.)

    So a `{` whose preceding words are all LOOP_PREFIX_WORDS opens a group frame, and the group is read exactly as an
    unprefixed one in the same position -- a short loop, a short conditional, a nested group, a subshell, a spud call, a
    redirection, an eval and an `sh -c` inside it alike.  With `coproc` among those words the frame is a forked shell's:
    its cd and its assignments never reach the line, and a trap set there still fires (SPD-054).  `echo coproc { git push; }`
    and any `{` after an ordinary word stay arguments, SPD-060's `coproc NAME { ... }` keeps its own path, and zsh's
    `{ ... } always { ... }` is SPD-124, not this ticket.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/zzone", "bin"):
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
                ("%s hook PreToolUse" % spud, "hook"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "Law 1"),
                ("echo x | tee ledger/tickets/SPD-001.md", "Law 1"))

    # -- the hole --------------------------------------------------------------------------
    def test_the_probes_evidence_forms_reach_the_body(self):
        """Every form Spud's probe ran: the push inside the prefixed group is found and the member refused."""
        for form in PREFIXED_GROUP_EVIDENCE:
            line = form % "git push"
            with self.subTest(line=line):
                r = self.refused_for_members(line)
                self.assertIn("git push", r.reason)
                self.assertIn(("git", ("push", "push")), self.analysis(line).findings, line)
        self.assertEqual(self.analysis("coproc { repeat 1 git push }").findings, [("git", ("push", "push"))])
        self.assertEqual(self.analysis("time { repeat 1 git status; }").findings, [("git", ("status", None))])

    def test_the_forms_no_shell_parses_are_over_read(self):
        """`! coproc { ... }` and `! ! { ... }` run nothing anywhere; reading them as groups refuses a line no shell accepts."""
        for form in PREFIXED_GROUP_PARSE_ERRORS:
            with self.subTest(form=form):
                self.refused_for_members(form % "git push")

    def test_every_body_form_inside_a_prefixed_group(self):
        """A short loop, a short conditional, a nested group, a subshell, an eval and an `sh -c` inside the group are read
        as they are inside an unprefixed one."""
        for form in ("coproc { %s; }", "time { %s; }", "! { %s; }"):
            for body in ("%s", "repeat 1 %s", "for f (a) %s", "if [[ -n x ]] %s", "{ %s }", "( %s )",
                         "if true; then %s; fi", "eval '%s'", "sh -c '%s'", "true && %s", "%s | cat",
                         "for f in a b; do %s; done"):
                line = form % (body % "git push")
                with self.subTest(line=line):
                    self.assertRefused(line, "Law 7")
                    self.assertRefused(line, "Law 7", AGENT_C)

    def test_every_payload_in_every_prefixed_group_for_every_caller(self):
        for form in ("coproc { %s; }", "time coproc { %s; }", "time { %s; }", "! { %s; }"):
            for body in ("%s", "repeat 1 %s", "if [[ -n x ]] %s"):
                self.assertPayloadsAnswered(form % body, self.member_payloads(), self.spud_payloads())

    def test_a_target_outside_a_narrow_members_deliverables(self):
        """AGENT_A plans tests/** and bin/spud, so note.txt at the home is refused it and allowed the ** member."""
        for form in ("coproc { %s; }", "coproc time { %s; }", "time { %s; }", "! { %s; }"):
            for body in ("%s", "repeat 1 %s"):
                line = form % (body % "echo x > note.txt")
                with self.subTest(line=line):
                    self.assertRefused(line, "deliverables")
                    self.assertSilent(line, AGENT_C)
                    self.assertSilent(form % (body % "echo x > tests/zzone/k.py"))

    # -- the coproc group's fork (SPD-054) ---------------------------------------------------
    def test_a_coproc_groups_directory_and_assignments_never_reach_the_line(self):
        home, out = str(self.home.path), self.out
        for form in FORKED_GROUPS:
            for body in ("x=1; cd %s" % out, "cd %s" % out, "cd %s; git status" % out, "repeat 1 cd %s" % out):
                line = form % body
                with self.subTest(line=line):
                    self.assertEqual(self.analysis(line).cwds, frozenset([home]), line)
            # ... so what follows the group is in the line's own directory, checked there: the ledger file the same line
            # names through the group's cd is never reached
            self.assertSilent((form % ("cd %s" % out)) + "; echo x > tests/zzone/k.py")
            self.assertSilent((form % ("cd %s/ledger" % home)) + "; echo x > tickets/SPD-001.md", AGENT_C)
        # a variable the forked shell assigns does not hold after it, so a later word that reads it is doubted (SPD-043),
        # exactly as after an unprefixed group
        for line, plain in (("coproc { X=git; }; $X push", "{ X=git; }; $X push"),
                            ("X=git; coproc { X=ls; }; $X push", "X=git; { X=ls; }; $X push")):
            with self.subTest(line=line):
                self.assertIn(("var-doubt", "$X"), self.analysis(line).findings, line)
                self.assertEqual(self.analysis(line).findings, self.analysis(plain).findings, line)
        self.assertRefused("coproc { X=git; }; $X push", "Law 7")
        self.assertRefused("X=git; coproc { X=ls; }; $X push", "may not hold")

    def test_a_trap_set_in_a_coproc_group_still_fires(self):
        """SPD-054: the forked shell's exit fires an EXIT trap set there, so the action is read as it is anywhere else."""
        for line in ("coproc { trap 'git push' EXIT; }", "coproc { repeat 1 trap 'git push' EXIT }",
                     "time coproc { trap 'git push' EXIT; }", "coproc time { trap 'git push' EXIT; }"):
            with self.subTest(line=line):
                self.refused_for_members(line)
        for agent_id in (AGENT_C, AGENT_A, None):  # a target the hook cannot place refuses Spud too (SPD-035)
            self.assertRefused("coproc { trap 'echo x > out.txt' EXIT; }", "cannot follow", agent_id)
        self.assertSilent("coproc { trap 'echo done' EXIT; }")
        self.assertSilent("coproc { trap 'echo done' EXIT; }", agent_id=None)

    # -- the current shell's groups -------------------------------------------------------------
    def test_a_time_or_bang_group_is_read_as_an_unprefixed_group_in_its_place(self):
        home, out = str(self.home.path), self.out
        for form in CURRENT_SHELL_GROUPS:
            for body in ("x=1; cd %s" % out, "cd %s" % out, "git status; cd %s" % out):
                line, plain = form % body, "{ %s; }" % body
                with self.subTest(line=line):
                    a, b = self.analysis(line), self.analysis(plain)
                    self.assertEqual((a.cwds, a.findings, sorted(a.doubt)), (b.cwds, b.findings, sorted(b.doubt)), line)
                    self.assertEqual(a.cwds, frozenset([str(out)]), line)  # the cd moved the line itself
            # ... and what follows the group is checked in the directory the group left it in, not in the line's own
            self.assertRefused((form % ("cd %s/ledger" % home)) + "; echo x > tickets/SPD-001.md", "generated", AGENT_C)
            self.assertSilent((form % ("cd %s" % out)) + "; echo x > note.txt")
            self.assertRefused((form % ("cd %s" % out)) + "; echo x > %s/note.txt" % home, "deliverables")

    def test_a_prefixed_group_in_a_pipeline_leaves_the_line_where_it_was(self):
        """`time { x=1; cd /tmp; } | cat` and `! { x=1; cd /tmp; } | cat` left the line where it was, as `{ cd /tmp; } | cat`
        does; main moved it to /tmp."""
        home, out = str(self.home.path), self.out
        for form in ("time { %s; } | cat", "! { %s; } | cat", "{ %s; } | cat", "time ! { %s; } | cat", "coproc { %s; } | cat"):
            line = form % ("x=1; cd %s" % out)
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).cwds, frozenset([home]), line)
        self.assertSilent(("time { cd %s; } | cat" % out) + "; echo x > tests/zzone/k.py")
        self.assertRefused(("! { cd %s; } | cat" % out) + "; echo x > note.txt", "deliverables")
        # the commands inside a piped group are still checked
        self.assertRefused("time { git push; } | cat", "Law 7")
        self.assertRefused("! { repeat 1 git push; } | cat", "Law 7")

    def test_the_group_ends_where_its_brace_ends_it(self):
        """What follows the group is outside it: a `}` with no terminator before it closes the group, as zsh closes one."""
        home = str(self.home.path)
        for form in ("time { %s }", "coproc { %s }", "! { %s }"):
            line = form % "echo a > ledger/tickets/SPD-001.md" + "; echo b > tests/zzone/k.py"
            with self.subTest(line=line):
                self.assertEqual(len(self.analysis(line).redirects), 2, self.analysis(line).redirects)
                self.assertRefused(line, "generated")
        self.assertSilent("time { echo a > tests/zzone/k.py }; echo b > tests/zzone/k.py")
        self.assertRefused("coproc { true }; echo x > ledger/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertEqual(self.analysis("time { cd /nowhere-at-all }; git status").kinds[-1], "git")
        self.assertEqual(self.analysis("coproc { git push }").cwds, frozenset([home]))

    # -- controls ----------------------------------------------------------------------------------
    def test_a_brace_after_an_ordinary_word_stays_an_argument(self):
        for ok in ("echo coproc { git push; }", "echo time { git push; }", "echo x coproc { git push; }",
                   "echo '{ git push; }'", "echo \"coproc { git push; }\"",
                   "grep -n 'coproc { git push; }' tests/zzone/k.py",
                   "%s --as %s member log 'coproc { repeat 1 git push }'" % (self.spud_cli, AGENT_A),
                   "printf 'time { git push; }\\n' > tests/zzone/k.py"):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
        self.assertEqual(self.analysis("echo coproc { git push; }").findings, [])

    def test_the_named_form_and_the_simple_commands_are_unchanged(self):
        for refused in ("coproc NAME { git push; }", "coproc NAME { git push }", "coproc git push", "time git push",
                        "! git push", "coproc { git push; }", "coproc ( git push )", "{ git push; }",
                        "coproc repeat 1 git push", "time repeat 1 git push", "! repeat 1 git push",
                        "coproc if [[ -n x ]] git push", "time if [[ -n x ]] git push"):
            with self.subTest(refused):
                self.refused_for_members(refused)
        for ok in ("coproc NAME git push", "coproc { git status; }", "time { git status; }", "! { echo hi; }",
                   "coproc { echo hi; }", "time echo hi", "coproc 1bad { git push; }"):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
        # the named form's group still runs in its own fork, and the unnamed one's cd is still not the line's
        for cmd in ("coproc NAME { cd /tmp; }", "coproc { cd /tmp; }", "coproc cd /tmp"):
            with self.subTest(cmd):
                self.assertEqual(self.analysis(cmd).cwds, frozenset([str(self.home.path)]))

    @wall_clock
    def test_bounded_on_pathological_input(self):
        m = load_spud_module()
        for line in ("coproc { " * 1000 + "git push" + " }" * 1000, "time { " * 1000 + "git push",
                     "! { " * 500 + "repeat 1 git push" + " }" * 500, "coproc time ! { " * 300 + "git push",
                     "time { " * 2000, "coproc { repeat 1 { " * 300 + "git push"):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = m.analyse_command(line, m.ShellAnalysis(cwd=str(self.home.path)))
                self.assertLess(time.monotonic() - started, 5.0)
                if "git push" in line:
                    self.assertIn(("git", ("push", "push")), a.findings, line[:40])


# zsh's try-always form (SPD-124), `{ try-list } always { always-list }`.  Each form fills its first `%s` with the try
# block's body and its second with the always block's.  TRY_ALWAYS_EVIDENCE is Spud's probe of the forms that ran both
# blocks, line for line, but for the redirections: out.txt there is /dev/null here, a member's out.txt at the home being
# refused as outside its deliverables whatever the always block holds.
TRY_ALWAYS_EVIDENCE = ("{ %s } always { %s }", "{ %s; } always { %s; }",
                       "{ %s } always\n{ %s }", "{ %s } always\n\n{ %s }", "{ %s } always;{ %s }",
                       "time { %s } always { %s }", "! { %s } always { %s }", "coproc { %s } always { %s }; wait",
                       "time coproc { %s } always { %s }", "coproc time { %s } always { %s }",
                       "{ %s } always { %s } && echo after", "{ %s } always { %s } || echo orelse",
                       "echo pre && { %s } always { %s }", "{ %s } always { %s } | cat", "echo first | { %s } always { %s }",
                       "{ %s } always { %s } &; wait", "{ %s } always { %s } ; echo next",
                       "( { %s } always { %s } )", "x=$( { %s } always { %s } )", "case a in a) { %s } always { %s } ;; esac",
                       "{ %s } always { %s } > /dev/null", "{ %s } always { %s } 2>&1", "{ %s } always { %s } < /dev/null",
                       "{ false; %s } always { %s }")
# The probe's forms with more than two blocks: nested, and two in a row.  Every `%s` is a block of its own.
TRY_ALWAYS_NESTED = ("{ { %s } always { %s } } always { %s }", "{ %s } always { { %s } always { %s } }",
                     "{ %s } always { %s }; { %s } always { %s }")
# The positions zsh refuses to parse, so nothing runs there.  Each is over-read: the block after `always` is read as a
# group, a subshell or a command of its own, never as the arguments of a command named `always`.
TRY_ALWAYS_PARSE_ERRORS = ("{ %s }\nalways { %s }", "{ %s }; always { %s }", "( %s ) always { %s }",
                           "{ %s } always ( %s )", "{ %s } always %s", "{ %s } always { true } always { %s }",
                           "{ %s } 'always' { %s }", "{ %s } \\always { %s }",
                           "f() { %s } always { %s }", "function g { %s } always { %s }",
                           "repeat 1 { %s } always { %s }", "for f (a) { %s } always { %s }",
                           "while [[ -n x ]] { %s } always { %s }", "if [[ -n x ]] { %s } always { %s }",
                           "coproc NAME { %s; } always { %s; }")


class TryAlwaysTest(BashHookCase):
    """SPD-124: zsh's try-always form, `{ try-list } always { always-list }`, runs the always block after the try block,
    and ShellWalk read it as a group and then one simple command named `always`: the group's `}` popped its frame, `always`
    became a command word, and the `{` after it was appended to that command's words (a `{` opens a group only where no
    word, or only `coproc`, `time` and `!`, stands before it -- SPD-081), so the whole always block was flattened into
    arguments.  `{ git status } always { git push }`, its `coproc`, `time` and `!` forms and the rest were kind other with
    no finding for the push, and `{ cd /tmp } always { git push }` had no finding at all (Laws 1, 5, 6 and 7).  Only
    `always` followed by a newline or `;` before the `{` found the push, reading the second group as unrelated to the first.

    No shell is probed here: this worktree session's harness refuses to run one (SPD-094).  The evidence is Spud's probe of
    2026-09-18, recorded on the ticket -- zsh 5.9 -f (identical with -o nobareglobqual, spot-checked), a function `vcs`
    standing in for the VCS program that appends its arguments to a log file, the line started in a scratch directory D:

    - both blocks ran, try then always, for every form in TRY_ALWAYS_EVIDENCE and TRY_ALWAYS_NESTED: with or without the
      terminators, with newlines or a `;` between `always` and the second `{`, after `time`, `!` and `coproc`, in an and-or
      list, a pipeline and the background, in a subshell, a command substitution and a case arm, before a redirection, and
      whatever the try block's status (`{ false } always { vcs alw }` ran the always block);
    - the always block's body was read as any group's: `repeat 1 vcs alw`, `if [[ -n x ]] vcs alw`, `eval 'vcs alw'` and
      `( vcs alw )` each ran;
    - the positions in TRY_ALWAYS_PARSE_ERRORS, and `echo { vcs try } always { vcs alw }` and a second `always` chained,
      are parse errors that ran nothing.  A sole `}` is significant anywhere in zsh, so a `}` that closes no group is one;
    - `always` elsewhere is an ordinary command (`always() { vcs fn }; always` ran the function, `print -r -- always`
      printed the word), and `}always` glued is no `}`: `{ vcs try } always { vcs alw }always { vcs two }` ran
      `vcs alw }always { vcs two`, one command;
    - the line's directory and variables: `{ x=1; cd /tmp } always { y=2; cd /usr }` left the line in /usr with x=1 and
      y=2, both blocks in the current shell, and so did its `time` and `!` forms; `{ cd /tmp } always { vcs alw-in-$PWD }`
      logged alw-in-/tmp, the always block starting where the try block ended, inside a coproc's fork too; the `coproc`
      and `time coproc` forms left the line in D with x and y unset, both blocks being the one fork's; `| cat` and `&`
      left it in D, the last element of a pipeline moved it; `false && { cd /tmp } always { cd /usr }` left it in D; a
      redirection after the always block opened where the try block started (D/out.txt); a trap set in either block fired.

    So a `}` that closes a `{ list }` and is followed by an unquoted `always`, any number of `;` and newlines, then `{`,
    does not close its frame: the always block is one more list of the same compound command, read exactly as a list
    after a `;` in that group would be, and the frame closes at the always block's `}`.  The reading is therefore the
    group's own, `{ try-list; always-list; }`, prefixes, pipelines, redirections and all, which is what the tests compare
    it with.  Each parse-error position is over-read, fail closed: a `{` after a lone `always` opens a group, and an
    `always` directly after a closing `}` with no `{` to follow is read as the keyword and dropped, so what follows it is
    read as commands.  `echo { vcs try } always { vcs alw }` stays echo's arguments, as `echo coproc { ... }` does.

    Left to other tickets, and not pinned here: zsh runs `{vcs alw}` with no blank after the `{` as a group, which the hook
    reads since SPD-132, `{ vcs try } always {vcs alw}` included (GluedBraceTest); and SPD-125's named coproc group, so
    `coproc NAME { a } always { b }` with no terminator inside is read only once that group is a frame (its `;` forms
    are read already, through the lone `always`).  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.out2 = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        self.addCleanup(shutil.rmtree, self.out2, True)
        home = self.home.path
        for d in ("ledger/tickets", "tests/zzone"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def reading(self, command):
        """What the walk makes of a line: the findings, the directories after it, what it assigned and doubts, and every
        redirection target with the directories it opens in."""
        a = self.analysis(command)
        return a.findings, a.cwds, a.vars, sorted(a.doubt), a.redirects, a.kinds

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)  # Law 7 refuses members only
        return r

    def member_payloads(self):
        """(command, the reason a member is refused for it): Law 7, Law 6's ticket new and `--as spud`, Law 5's --as, and
        Law 1 through a redirection and through tee."""
        spud = self.spud_cli
        return (("git push", "Law 7"),
                ("%s ticket new --title x" % spud, "Law 6"),
                ("%s --as spud member log hi" % spud, "Law 6"),
                ("%s --as %s member log hi" % (spud, AGENT_B), "--as"),
                ("echo x > ledger/tickets/SPD-001.md", "generated"),
                ("echo x | tee ledger/tickets/SPD-001.md", "generated"))

    def spud_payloads(self):
        """Spud is never refused for git; these are the checks that do apply to him."""
        spud = self.spud_cli
        return (("%s --as %s member log hi" % (spud, AGENT_A), "--as"),
                ("echo x > ledger/tickets/SPD-001.md", "Law 1"),
                ("echo x | tee ledger/tickets/SPD-001.md", "Law 1"))

    # -- the hole --------------------------------------------------------------------------
    def test_the_tickets_evidence_commands(self):
        """What main read on the ticket: the always block's push was never found, and a cd in the try block hid it too."""
        push, status = ("git", ("push", "push")), ("git", ("status", None))
        for line in ("{ git status } always { git push }", "coproc { git status } always { git push }",
                     "time { git status } always { git push }", "! { git status } always { git push }",
                     "{ git status } always { repeat 1 git push }", "{ git status } always { git push } > out.txt"):
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).findings, [status, push], line)
        self.assertEqual(self.analysis("{ git status } always { { git push } always { git commit } }").findings,
                         [status, push, ("git", ("commit", "commit"))])
        a = self.analysis("{ cd /tmp } always { git push }")
        self.assertEqual((a.findings, a.cwds), ([push], frozenset(["/tmp"])))
        self.refused_for_members("{ git status } always { git push }")
        self.refused_for_members("{ cd /tmp } always { git push }")

    def test_every_form_the_probe_ran_reaches_the_always_block(self):
        for form in TRY_ALWAYS_EVIDENCE:
            line = form % ("git status", "git push")
            with self.subTest(line=line):
                r = self.refused_for_members(line)
                self.assertIn("git push", r.reason)
                self.assertIn(("git", ("push", "push")), self.analysis(line).findings, line)
            # ... and the try block is read as before, in every form
            self.assertIn(("git", ("push", "push")), self.analysis(form % ("git push", "git status")).findings, form)

    def test_every_block_of_a_nested_or_repeated_form(self):
        for form in TRY_ALWAYS_NESTED:
            slots = form.count("%s")
            for k in range(slots):
                line = form % tuple("git push" if j == k else "git status" for j in range(slots))
                with self.subTest(line=line):
                    self.assertIn(("git", ("push", "push")), self.analysis(line).findings, line)
                    self.assertRefused(line, "Law 7")
            self.assertSilent(form % tuple("git status" for _ in range(slots)))

    def test_every_body_form_inside_the_always_block(self):
        """A short loop, a short conditional, a nested group or always form, a subshell, an eval and an `sh -c` in the
        always block are read as they are inside a group."""
        for form in ("{ git status } always { %s }", "{ git status; } always { %s; }", "coproc { git status } always { %s }",
                     "time { git status } always { %s }"):
            for body in ("repeat 1 %s", "if [[ -n x ]] %s", "eval '%s'", "( %s )", "for f (a) %s", "while [[ -n x ]] %s",
                         "{ %s }", "{ true } always { %s }", "sh -c '%s'", "if true; then %s; fi", "true && %s",
                         "%s | cat", "for f in a b; do %s; done"):
                line = form % (body % "git push")
                with self.subTest(line=line):
                    self.assertRefused(line, "Law 7")
                    self.assertIn(("git", ("push", "push")), self.analysis(line).findings, line)

    def test_a_short_loop_ending_the_try_block(self):
        """zsh's `}` ends a short loop's or a short conditional's sublist and closes the group behind it (a sole `}` is
        significant anywhere in zsh, and SPD-081's probe ran `coproc { repeat 1 vcs push }`), so what follows is read as the
        always block and not as more words of the loop's command."""
        for try_block in ("repeat 1 git status", "if [[ -n x ]] git status", "for f (a) git status",
                          "while [[ -n x ]] git status"):
            line = "{ %s } always { git push }" % try_block
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).findings, [("git", ("status", None)), ("git", ("push", "push"))])
                self.refused_for_members(line)
        # the brace closed the loop and the group, so the loop's command is `git status` and not `git status }`, and what
        # follows the group is outside both, exactly as after the same group closed by a terminator
        for tail in ("; git log", " > out.txt", " | cat", " && git log"):
            line, plain = "{ repeat 1 git status }" + tail, "{ repeat 1 git status; }" + tail
            with self.subTest(line=line):
                self.assertEqual(self.reading(line), self.reading(plain), line)

    def test_every_payload_in_the_always_block_for_every_caller(self):
        for form in ("{ git status } always { %s }", "coproc { git status } always { %s }",
                     "time { git status; } always { %s; }", "{ git status } always { repeat 1 %s }"):
            self.assertPayloadsAnswered(form, self.member_payloads(), self.spud_payloads())
        for agent_id in (AGENT_C, AGENT_A, None):  # the database is refused to everyone, Spud included
            self.assertRefused("{ true } always { sqlite3 %s/.spud/ledger.db 'select 1' }" % self.home.path,
                               "spud sql --readonly", agent_id)

    def test_a_target_outside_a_narrow_members_deliverables(self):
        """AGENT_A plans tests/** and bin/spud, so note.txt at the home is refused it and allowed the ** member."""
        for form in ("{ true } always { %s }", "coproc { true } always { %s }", "! { true; } always { %s; }"):
            line = form % "echo x > note.txt"
            with self.subTest(line=line):
                self.assertRefused(line, "deliverables")
                self.assertSilent(line, AGENT_C)
                self.assertSilent(form % "echo x > tests/zzone/k.py")

    # -- the one compound command -----------------------------------------------------------
    def test_the_form_reads_as_one_group_holding_both_lists(self):
        """`{ A } always { B }` is read as `{ A; B; }`, wherever it stands and whatever stands before and after it."""
        out, out2 = self.out, self.out2
        bodies = (("x=1; cd %s" % out, "y=2; cd %s" % out2), ("x=1", "cd %s; y=2" % out2), ("cd %s" % out, "y=2"),
                  ("git status; cd %s" % out, "cd lib"), ("cd %s" % out, "echo x > k.txt"),
                  ("X=git", "$X push"), ("cd %s" % out, "trap 'git push' EXIT"))
        for wrap in ("%s", "time %s", "! %s", "coproc %s", "time coproc %s", "coproc time %s", "%s | cat", "%s &",
                     "echo first | %s", "false && %s", "true || %s", "%s > out.txt", "( %s )", "%s; echo x > k.txt"):
            for try_body, always_body in bodies:
                line = wrap % ("{ %s } always { %s }" % (try_body, always_body))
                plain = wrap % ("{ %s; %s; }" % (try_body, always_body))
                with self.subTest(line=line):
                    self.assertEqual(self.reading(line), self.reading(plain), line)

    def test_the_line_runs_on_where_the_probe_left_it(self):
        home, out, out2 = str(self.home.path), str(self.out), str(self.out2)
        both = "{ x=1; cd %s } always { y=2; cd %s }" % (out, out2)
        for line, where in ((both, out2), ("time " + both, out2), ("! " + both, out2),
                            ("{ x=1 } always { cd %s; y=2 }" % out2, out2), ("{ cd %s } always { y=2 }" % out, out),
                            ("coproc " + both, home), ("time coproc " + both, home), ("coproc time " + both, home),
                            (both + " | cat", home), (both + " &", home)):
            with self.subTest(line=line):
                a = self.analysis(line)
                self.assertEqual(a.cwds, frozenset([where]), line)
                if where != home:  # the current shell's variables, both blocks'
                    self.assertEqual((a.vars.get("x"), a.vars.get("y")), ("1" if "x=1" in line else None, "2"), line)
        # the hook keeps each directory the shell may be in: a list that may not run, a cd that fails
        self.assertIn(home, self.analysis("false && { cd %s } always { cd %s }" % (out, out2)).cwds)
        self.assertIn(out, self.analysis("{ cd %s } always { cd lib }" % out).cwds)
        self.assertIn(out2, self.analysis("echo first | " + both).cwds)

    def test_the_always_block_starts_where_the_try_block_ended(self):
        home, out = self.home.path, self.out
        for form in ("{ cd %s } always { echo x > k.txt }", "coproc { cd %s } always { echo x > k.txt }",
                     "{ cd %s; } always\n{ echo x > k.txt; }"):
            line = form % out
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).redirects, [("k.txt", frozenset([str(out)]))], line)
        for form in ("{ cd %s } always { echo x > tickets/SPD-001.md }", "coproc { cd %s } always { echo x > tickets/SPD-001.md }"):
            line = form % (home / "ledger")
            with self.subTest(line=line):
                self.assertRefused(line, "generated", AGENT_C)
                self.assertRefused(line, "Law 1", agent_id=None)
        self.assertSilent("{ cd %s } always { echo x > note.txt }" % out)
        self.assertSilent("coproc { cd %s } always { echo x > note.txt }" % out)

    def test_the_compound_ends_at_the_always_blocks_brace(self):
        home, out = str(self.home.path), self.out
        # a redirection after it opens where the try block started, as a group's does
        a = self.analysis("{ cd %s } always { git status } > out.txt" % out)
        self.assertIn(home, a.redirects[-1][1])
        self.assertRefused("{ cd %s } always { true } > ledger/tickets/SPD-001.md" % out, "generated", AGENT_C)
        self.assertRefused("{ cd %s } always { true } > ledger/tickets/SPD-001.md" % out, "Law 1", agent_id=None)
        # what follows it is outside it, in the directory it left
        line = "{ true } always { echo a > tests/zzone/k.py }; echo b > ledger/tickets/SPD-001.md"
        self.assertEqual(len(self.analysis(line).redirects), 2)
        self.assertRefused(line, "generated")
        self.assertSilent("{ true } always { cd %s }; echo x > note.txt" % out)
        self.assertSilent("coproc { true } always { cd %s }; echo x > tests/zzone/k.py" % out)
        self.assertRefused("coproc { true } always { cd %s }; echo x > note.txt" % out, "deliverables")

    def test_a_trap_set_in_either_block_is_read(self):
        """The probe's traps fired at the line's exit and at the coproc's fork's (SPD-054)."""
        for line in ("{ trap 'git push' EXIT } always { git status }", "{ git status } always { trap 'git push' EXIT }",
                     "coproc { true } always { trap 'git push' EXIT }", "time { true } always { trap 'git push' EXIT; }"):
            with self.subTest(line=line):
                self.refused_for_members(line)

    # -- the positions no shell parses ------------------------------------------------------------
    def test_the_positions_zsh_refuses_to_parse_are_over_read(self):
        """Nothing runs at these positions; the block after `always` is read all the same, which refuses a line no shell
        accepts rather than leave a silent always block."""
        for form in TRY_ALWAYS_PARSE_ERRORS:
            line = form % ("git status", "git push")
            with self.subTest(line=line):
                self.refused_for_members(line)
                self.assertIn(("git", ("push", "push")), self.analysis(line).findings, line)
        # Not in the probe: a redirection between the group's `}` and `always`.  Whatever zsh makes of it, the block after
        # `always` is read as a group, and the redirection before it is checked where the group left it.
        for line in ("{ git status } 2>&1 always { git push }", "{ git status } > /dev/null always { git push }",
                     "time { git status; } 2>/dev/null always\n{ git push; }"):
            with self.subTest(line=line):
                self.refused_for_members(line)
        self.assertRefused("{ true } > ledger/tickets/SPD-001.md always { true }", "generated", AGENT_C)
        self.assertRefused("{ true } > ledger/tickets/SPD-001.md always { true }", "Law 1", agent_id=None)

    def test_always_elsewhere_is_an_ordinary_word(self):
        for ok in ("always() { echo fn; }; always", "print -r -- always", "echo always", "always",
                   "echo { git push } always { git push }", "echo always { git push }", "echo x always { git push; }",
                   "grep -n 'always { git push }' tests/zzone/k.py",
                   "%s --as %s member log '{ a } always { git push }'" % (self.spud_cli, AGENT_A),
                   "{ git status } always { echo alw }always { git push }", "{ git status } always"):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
        self.assertEqual(self.analysis("echo { git push } always { git push }").findings, [])
        # `}always` glued is a word, not the always block's brace: the probe ran `vcs alw }always { vcs two`, one command
        self.assertEqual(self.analysis("{ git status } always { echo alw }always { git push }").findings,
                         [("git", ("status", None))])
        self.assertEqual(self.analysis("{ git status } always { git log }always { git push }").findings,
                         [("git", ("status", None)), ("git", ("log", None))])

    def test_every_other_group_reading_is_unchanged(self):
        for refused in ("{ git push }", "{ git push; }", "{ git status; }; git push", "time { git push; }", "! { git push }",
                        "coproc { git push }", "coproc NAME { git push; }", "{ repeat 1 git push }",
                        "repeat 2 { git push }", "if [[ -n x ]] { echo a } else { git push }", "f() { git push }; f",
                        "function g { git push }", "{ git status }; always=1; git push"):
            with self.subTest(refused):
                self.refused_for_members(refused)
        for ok in ("{ git status }", "{ echo hi; } > /dev/null", "coproc { echo hi; }", "time { git status; }"):
            with self.subTest(ok):
                self.assertSilent(ok)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        m = load_spud_module()
        for line in ("{ git status } always " * 1000 + "{ git push }", "{ " * 500 + "git push" + " } always { true }" * 500,
                     "{ true } always" + " ;" * 5000 + " { git push }", "{ a } always ; " * 2000 + "git push",
                     "coproc { " * 300 + "git push" + " } always { git status }" * 300, "} always { " * 2000,
                     "{ repeat 1 true } always { " * 500 + "git push"):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = m.analyse_command(line, m.ShellAnalysis(cwd=str(self.home.path)))
                self.assertLess(time.monotonic() - started, 5.0)
                if "git push" in line:
                    self.assertIn(("git", ("push", "push")), a.findings, line[:40])


# zsh's braces glued to a word (SPD-132).  Each form fills its `%s` with the command the group runs, and
# GLUED_BRACE_EVIDENCE is Spud's probe of the forms zsh ran as a group, line for line but for that command: the opening
# brace glued, the closing one, or both; what may follow the group; and every command position the hook already reads a
# group in, after a prefix, as a short loop's or conditional's body, as a function's, in the always block, after an
# operator, inside `$(...)` and eval.
GLUED_BRACE_EVIDENCE = ("{%s}", "{%s;}", "{%s }", "{%s\n}", "{ %s}", "{true;%s}", "{true&&%s}", "{%s|cat}", "{true\n%s}",
                        "{repeat 1 %s}", "{(%s)}", "{ {%s} }", "{ true }; {%s}; { true}",
                        "{%s}; true", "{%s}&&true", "{%s}||true", "{%s}|cat", "{%s}&\nwait", "{%s};", "{%s} # c",
                        "{%s}>/dev/null", "{%s} 2>/dev/null", "{%s}<<<in",
                        "time {%s}", "! {%s}", "coproc {%s}", "repeat 1 {%s}", "for f (a) {%s}", "if [[ -n x ]] {%s}",
                        "if [[ -n x ]] {true} else {%s}", "f() {%s}; f", "(){%s}", "() {%s}", "true; {%s}",
                        "true && {%s}", "false || {%s}", "true | {%s}", "true & {%s}", "true\n{%s}", "({%s})",
                        "x=$({%s})", "eval '{%s}'", "case a in a) {%s};; esac", "if true; then {%s}; fi",
                        "if false; then true; else {%s}; fi", "for f in a; do {%s}; done", "{%s} always {true}",
                        "{true} always {%s}", "{ true } always {%s}", "select x in a; {%s; break} <<< 1")
# The words whose `}` zsh split off and closed the group with, each after `{ echo ` (the probe logged the word before the
# `}`): after an expansion, a brace expansion, a word holding a `}` of its own, a quoted `}` and a redirection's target.
GLUED_BRACE_CLOSERS = ("a", "${HOME}", "${x-q}", "${#x}", "$((1+1))", "$(true)", "{a}", "{a,b}", "x{a,b}", "a}b", "a}",
                       '"a}"', "'a}'", '"}"', "a >/dev/null", "a 2>&1")
# The words that keep their `}`, so the group stays open: no `}` at the end, one a `{` or `${` in the word matches, a
# quoted or escaped one, and an assignment's value.
GLUED_BRACE_WORDS = ("a}x", "{a}", "${x-q}", "${HOME}", "a\\}", '"a}"', "'a}'")
# The positions zsh reads no group at, parse errors that ran nothing: a quoted or escaped brace, a brace after a word that
# is not a prefix, and a named coproc's (SPD-125).  Each stays the word it is, as main read it.
GLUED_BRACE_NOT_GROUPS = ('"{git" push}', "\\{git push}", "echo {git push}", "builtin {git push}", "nocorrect {git push}",
                          "x=1 {git push}", "x={git push}", "coproc NAME {git push}", "{git status} always{git push}")
# The parse errors the hook over-reads, fail closed: a second group glued to the first, `function name` before a glued
# brace (`f() {vcs a}` ran, `function g {vcs a}` did not), a stray `}` after the group, words after it.
GLUED_BRACE_PARSE_ERRORS = ("{git status} {git push}", "{git push} {git status}", "function g {git push}",
                            "function {git push}", "{git push}; }", "{ git push} b }", "{git push}()")


class GluedBraceTest(BashHookCase):
    """SPD-132: zsh reads a brace glued to the words of a group -- `{git push}`, `{ git push}`, `{git push }` -- as the group
    it runs, and ShellWalk opened a group only at a token that is exactly `{` and closed one only at a token that is exactly
    `}`.  So `{git push}` was a command named `{git` with an argument `push}`, and `time {git push}`, `coproc {git push}`,
    `{repeat 1 git push}`, `if [[ -n x ]] {git push}`, `f() {git push}; f` and the rest had no finding; `{ git push}` found
    a verb `push}`, no push; `{x=1; cd /tmp}` left the line in `/tmp}`; and `{echo x > ledger/tickets/SPD-001.md}` wrote
    `SPD-001.md}`, a file the path rule never matches, so a member's write to a rendered note was allowed (Laws 1, 5, 6, 7).

    No shell is probed here: this worktree session's harness refuses to run one (SPD-094).  The evidence is Spud's probe of
    2026-09-18, recorded on the ticket -- zsh 5.9 -f (the ticket's own probe found -o nobareglobqual identical), a
    function `vcs` standing in for the VCS program that appends its arguments and $PWD to a log, each line started in a
    scratch directory D:

    - an unquoted `{` at the start of a word in command position opened a group, the rest of the word read as the next
      word: every form in GLUED_BRACE_EVIDENCE ran as a group, and so did `{"vcs" try}`, `{\\vcs try}`, `{vcs}` and `{}`.
      Commas and `..` make no brace expansion there: `{vcs,x}` ran a function named `vcs,x`, `{1..2}` was "command not
      found: 1..2".  `{fd}>out.txt` in command position is a group too (only `exec {fd}>` allocates a descriptor);
    - an unquoted, unescaped `}` ending a word closed the group when no `{` or `${` in the same word matched it, and only
      the last one: GLUED_BRACE_CLOSERS, each logged as the word before its `}` (`{vcs try}}` logged `try}`), a redirection
      too (`{vcs a >out.txt}` wrote out.txt, `{vcs a}>out.txt` redirected the group).  `{vcs a}x`, `{vcs {a}`,
      `{vcs ${x-q}`, `{vcs a\\}`, `{vcs "a}"` stayed words and left the group open (a parse error at the end);
    - an assignment keeps its `}`: `{x=1}`, `{ x=1}` and `while [[ -z $x ]] {vcs a; x=1}` never closed, while `{x=1 }` set
      x and `{vcs x=1}` logged `x=1`;
    - GLUED_BRACE_NOT_GROUPS and GLUED_BRACE_PARSE_ERRORS are parse errors that ran nothing, and so is a word-ending `}`
      outside any group (`vcs a}`, `print -r -- a}`, `[[ -n a} ]]`, `for i in a}; do ...`);
    - the line's directory and variables, as after a spaced group: `{cd /tmp}` left it in /tmp, `{cd /tmp} && y=2` and
      `time {cd /tmp}` too, `{cd /tmp} | cat` left it in D, `{x=1; cd /tmp}` left x=1 in /tmp.

    bash 3.2 reads neither brace: `{echo a}` is "{echo: command not found", `{ echo a}; }` printed `a}`, and
    `{ cd /tmp}; pwd; }` failed the cd and stayed.  So where the line holds a glued brace zsh splits off, the hook reads it
    twice, as it reads zsh's glob groups (SPD-039): zsh's reading, the group exactly as a spaced group in its place, and
    main's, which is bash's, and every finding either makes is checked, the directories after the line are both
    readings', and a variable only one of them assigns is doubted (SPD-030: keep both rather than guess).  So `{cd /tmp}`
    leaves the hook with both D and /tmp, `{git,push}` is still bash's brace expansion, and nothing main found is lost.

    The decisions this class pins: every glued `{` opens where a lone `{` would -- a group, a function body after `f()`,
    `()` and `function name` (zsh rejects the last glued; over-read), a prefixed group, a coproc's fork, the always
    block -- and never in a case pattern; a glued `}` closes only where a lone `}` would (after `fi`, `done` or `esac` in
    the same word too), so a word-ending `}` outside any group stays a word, as today; GLUED_BRACE_PARSE_ERRORS are read as
    the groups they spell; GLUED_BRACE_NOT_GROUPS stay words, since neither shell runs a command there.  The reading
    assumes zsh's IGNORE_BRACES and IGNORE_CLOSE_BRACES off, as they are by default.  zsh's `elif [[ ... ]] {vcs b}` ran
    too, but the hook reads no elif short body even spaced (proposal 185).  AGENT_A plans tests/** and bin/spud; AGENT_C
    plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        home = self.home.path
        for d in ("ledger/tickets", "tests/zzone"):
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
        """(command, the reason a member is refused for it): Law 7, Law 6's ticket new and `--as spud`, and Law 1 through a
        redirection and through tee."""
        spud = self.spud_cli
        return (("git push", "Law 7"),
                ("%s ticket new --title x" % spud, "Law 6"),
                ("%s --as spud member log hi" % spud, "Law 6"),
                ("echo x > ledger/tickets/SPD-001.md", "generated"),
                ("echo x | tee ledger/tickets/SPD-001.md", "generated"))

    def spud_payloads(self):
        """Spud is never refused for git; these are the checks that do apply to him."""
        return (("%s --as %s member log hi" % (self.spud_cli, AGENT_A), "--as"),
                ("echo x > ledger/tickets/SPD-001.md", "Law 1"),
                ("echo x | tee ledger/tickets/SPD-001.md", "Law 1"))

    # -- the hole --------------------------------------------------------------------------
    def test_the_tickets_evidence_commands(self):
        """What main read on the ticket: no finding, a verb `push}`, a cd into `/tmp}`, a write to `SPD-001.md}`."""
        push = ("git", ("push", "push"))
        for line in ("{git push}", "{git push }", "{git push; }", "time {git push}", "coproc {git push}",
                     "{repeat 1 git push}", "{git push} | cat", "echo pre && {git push}", "if [[ -n x ]] {git push}",
                     "for f (a) {git push}", "repeat 1 {git push}", "f() {git push}; f", "(){git push}",
                     "{git push} always { git status }", "{ git status } always {git push}", "{ git push}",
                     "{cd /tmp; git push}"):
            with self.subTest(line=line):
                self.assertIn(push, self.analysis(line).findings, line)
                self.refused_for_members(line)
        self.assertEqual(self.analysis("{git push}").findings, [push])
        self.assertEqual(self.analysis("{repeat 1 git status}").findings, [("git", ("status", None))])
        # `{ git push}`: zsh's push, and main's verb `push}` still read beside it (bash's reading, see the docstring)
        self.assertEqual(self.analysis("{ git push}").findings, [push, ("git-verb", ("verb", "push}"))])
        self.assertRefused("{ git status}", "not one of git's own commands")
        self.assertIn("/tmp", self.analysis("{x=1; cd /tmp}").cwds)
        self.assertRefused("{%s --as spud ticket new}" % self.spud_cli, "Law 6")
        self.assertRefused("{echo x > ledger/tickets/SPD-001.md}", "generated", AGENT_C)
        self.assertRefused("{echo x > ledger/tickets/SPD-001.md}", "Law 1", agent_id=None)

    def test_every_form_the_probe_ran_reaches_the_group(self):
        for form in GLUED_BRACE_EVIDENCE:
            line = form % "git push"
            with self.subTest(line=line):
                r = self.refused_for_members(line)
                self.assertIn("git push", r.reason)
                self.assertIn(("git", ("push", "push")), self.analysis(line).findings, line)
            with self.subTest(line=form % "git status"):
                self.assertSilent(form % "git status", agent_id=None)
                # zsh's reading adds no refusal to a harmless line; a member is refused only where bash's, main's, reads
                # a verb `status}` after a separator (`{true;git status}`: `{true` is a command there, then `git status}`)
                r = self.bash(form % "git status")
                self.assertTrue(r.decision != "deny" or "`git status}` is not one of git's own commands" in r.reason, r)
        for line in ('{"git" push}', "{\\git push}", "{git push;git status}", "{git status\ngit push}",
                     "{git status&&git push}", "{git push|cat}", "{ {git push} }", "{git push }; {git status}; { git status}",
                     "{git status} always {git push}", "{git push} always {git status}", "sh -c '{git push}'",
                     "echo $({git push})", "if [[ -n x ]] {git status} else {git push}"):
            with self.subTest(line=line):
                self.refused_for_members(line)

    def test_every_payload_in_a_glued_group_for_every_caller(self):
        for form in ("{%s}", "{ %s}", "{%s }", "time {%s}", "coproc {%s}", "{repeat 1 %s}", "if [[ -n x ]] {%s}",
                     "f() {%s}; f", "{true} always {%s}", "eval '{%s}'"):
            self.assertPayloadsAnswered(form, self.member_payloads(), self.spud_payloads())
        for agent_id in (AGENT_C, AGENT_A, None):  # the database is refused to everyone, Spud included
            self.assertRefused("{sqlite3 %s/.spud/ledger.db 'select 1'}" % self.home.path, "spud sql --readonly", agent_id)

    def test_a_target_outside_a_narrow_members_deliverables(self):
        """AGENT_A plans tests/** and bin/spud, so note.txt at the home is refused it and allowed the ** member."""
        for form in ("{%s}", "{ %s}", "time {%s}", "{true} always {%s}"):
            line = form % "echo x > note.txt"
            with self.subTest(line=line):
                self.assertRefused(line, "deliverables")
                self.assertSilent(line, AGENT_C)
                self.assertSilent(form % "echo x > tests/zzone/k.py")

    # -- the closing brace ------------------------------------------------------------------------
    def test_a_word_ending_brace_closes_the_group_after_the_word(self):
        """The always block after the group is read only where the `}` closed it: otherwise it is more words of the
        command before it, as it is in both shells."""
        for word in GLUED_BRACE_CLOSERS:
            line = "{ echo %s} always { git push }" % word
            with self.subTest(line=line):
                self.assertIn(("git", ("push", "push")), self.analysis(line).findings, line)
                for agent_id in (AGENT_C, AGENT_A):
                    self.assertRefused(line, "Law 7", agent_id)
        for word in GLUED_BRACE_WORDS:
            line = "{ echo %s always { git push }" % word
            with self.subTest(line=line):
                self.assertNotIn(("git", ("push", "push")), self.analysis(line).findings, line)
                self.assertSilent(line)
        # the word before the `}` is the command's last, redirection targets included, and a descriptor stays one
        a = self.analysis("{echo x >%s/k.txt} && {echo y 2>&1}" % self.out)
        self.assertIn(("%s/k.txt" % self.out, frozenset([str(self.home.path)])), a.redirects)
        self.assertNotIn("1", [t for t, _ in a.redirects])
        # ... while bash's reading, main's, still opens `1}`: bash runs `{echo` there, and `>&1}` names a file
        self.assertIn("1}", [t for t, _ in a.redirects])
        self.assertRefused("{echo y 2>&1}", "Law 1", agent_id=None)
        # only the last `}` is split off: `{git push}}` is a verb `push}`, no push
        self.assertEqual(self.analysis("{git push}}").findings, [("git-verb", ("verb", "push}"))])

    def test_an_assignment_keeps_its_brace(self):
        """`{x=1}` and `{ x=1}` never closed in zsh: the value is `1}`, and what follows is read inside the group."""
        for line in ("{x=1}", "{ x=1}", "{ y=2 x=1}"):
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).vars.get("x"), "1}", line)
        self.assertEqual(self.analysis("{x=1 }").vars.get("x"), "1")
        self.refused_for_members("{echo x=1} always { git push }")  # an argument's `}` closes
        self.assertNotIn(("git", ("push", "push")), self.analysis("{ x=1} always { git push }").findings)
        self.refused_for_members("{x=1}; git push")
        self.refused_for_members("while [[ -z $x ]] {git status; x=1}; git push")

    def test_a_closer_word_glued_to_the_brace(self):
        """`fi}`, `done}` and `esac}` close their compound command, then the group, as `fi }` would."""
        for line in ("{ if true; then git status; fi} always { git push }",
                     "{ for f in a; do git status; done} always { git push }",
                     "{ case a in a) git status;; esac} always { git push }"):
            with self.subTest(line=line):
                self.assertIn(("git", ("push", "push")), self.analysis(line).findings, line)
                self.refused_for_members(line)

    # -- the directory and the variables ---------------------------------------------------------
    def test_the_line_runs_on_where_either_shell_leaves_it(self):
        """zsh's directory is a spaced group's, and bash's -- the line where it was, `{cd` a command it cannot find -- is
        kept beside it."""
        home, out = str(self.home.path), str(self.out)
        for wrap in ("%s", "time %s", "! %s", "coproc %s", "%s | cat", "%s &", "false && %s", "true || %s",
                     "repeat 1 %s", "if [[ -n x ]] %s", "f() %s", "%s && y=2", "( %s )"):
            for body in ("cd %s" % out, "cd %s; git status" % out, "cd %s && true" % out):
                line, spaced = wrap % ("{%s}" % body), wrap % ("{ %s; }" % body)
                with self.subTest(line=line):
                    self.assertEqual(self.analysis(line).cwds, self.analysis(spaced).cwds | {home}, line)
        for line, where in (("{cd %s}" % out, {home, out}), ("{cd %s} | cat" % out, {home}), ("coproc {cd %s}" % out, {home}),
                            ("time {cd %s}" % out, {home, out}), ("{cd %s} && y=2" % out, {home, out})):
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).cwds, frozenset(where), line)
        # the variables zsh's group assigns, doubted where bash's reading does not assign them (`{x=1` is a command there)
        a = self.analysis("{x=1; cd %s}" % out)
        self.assertEqual((a.vars.get("x"), "x" in a.doubt, out in a.cwds, home in a.cwds), ("1", True, True, True))
        a = self.analysis("{cd %s} && y=2" % out)
        self.assertEqual(a.vars.get("y"), "2")
        # main read `ls push` for both, `{X=git` being a command to it
        for line in ("X=ls; {X=git; true}; $X push", "X=ls; {X=git }; $X push"):
            with self.subTest(line=line):
                self.assertIn(("var-doubt", "$X"), self.analysis(line).findings)
                self.assertRefused(line, "git push")

    def test_what_follows_the_group_is_checked_where_either_shell_left_it(self):
        home, out = self.home.path, self.out
        # zsh's: the group's cd moved the line into the ledger
        for form in ("{cd %s}", "{ cd %s}", "time {cd %s}", "{true; cd %s}", "{true} always {cd %s}"):
            line = (form % (home / "ledger")) + "; echo x > tickets/SPD-001.md"
            with self.subTest(line=line):
                self.assertRefused(line, "generated", AGENT_C)
                self.assertRefused(line, "Law 1", agent_id=None)
        # bash's, kept beside it: `{cd` is a command there and the line stays at the home, outside AGENT_A's deliverables
        for line in ("{cd %s}; echo x > note.txt" % out, "{ cd %s}; echo x > note.txt; }" % out,
                     "{cd %s} | cat; echo x > note.txt" % out, "coproc {cd %s}; echo x > note.txt" % out):
            with self.subTest(line=line):
                self.assertRefused(line, "deliverables")
                self.assertSilent(line, AGENT_C)
        self.assertSilent("{cd %s}; echo x > tests/zzone/k.py" % out)
        # the brief's own: bash runs the push in the line's own directory, and the hook keeps it
        a = self.analysis("{ cd /tmp}; git push; }")
        self.assertIn(("git", ("push", "push")), a.findings)
        self.assertIn(str(home), a.cwds)

    def test_the_always_block_starts_where_the_try_block_ended(self):
        out = str(self.out)
        for line in ("{ cd %s } always {echo x > k.txt}" % out, "{cd %s} always { echo x > k.txt }" % out,
                     "{cd %s} always {echo x > k.txt}" % out):
            with self.subTest(line=line):
                self.assertIn(("k.txt", frozenset([out])), self.analysis(line).redirects, line)

    # -- the positions zsh reads no group at --------------------------------------------------------
    def test_the_parse_errors_are_over_read(self):
        for line in GLUED_BRACE_PARSE_ERRORS:
            with self.subTest(line=line):
                self.refused_for_members(line)
                self.assertIn(("git", ("push", "push")), self.analysis(line).findings, line)
        # `{{git push}}`: two groups, the last `}` of `push}}` alone split off, so the verb is `push}` -- git runs no such
        # verb, which a member is refused anyway (SPD-047)
        self.assertEqual(self.analysis("{{git push}}").findings, [("git-verb", ("verb", "push}"))])
        self.assertRefused("{{git push}}", "not one of git's own commands")

    def test_a_brace_neither_shell_reads_as_a_group_stays_a_word(self):
        for line in GLUED_BRACE_NOT_GROUPS:
            with self.subTest(line=line):
                self.assertNotIn(("git", ("push", "push")), self.analysis(line).findings, line)
                self.assertSilent(line)
        # a case pattern is no command position: `{git}` is a pattern, not a group running git
        self.assertEqual(self.analysis("case a in b) true;; {git}) git status;; esac").findings, [("git", ("status", None))])

    def test_a_word_ending_brace_outside_any_group_stays_a_word(self):
        """zsh rejects each of these; bash runs them, and the hook reads them as before."""
        self.assertEqual(self.analysis("git push}").findings, [("git-verb", ("verb", "push}"))])
        self.assertEqual(self.analysis("echo x > ledger/tickets/SPD-001.md}").redirects,
                         [("ledger/tickets/SPD-001.md}", frozenset([str(self.home.path)]))])
        for ok in ("echo a}", "print -r -- a}", "[[ -n a} ]]", "for i in a}; do echo $i; done", "echo ${x:-a}}",
                   "echo x > tests/zzone/k.py}", "git status}"):
            with self.subTest(ok):
                self.assertEqual(self.analysis(ok).cwds, frozenset([str(self.home.path)]))
        self.assertSilent("echo a} b")
        self.assertRefused("echo a} > ledger/tickets/SPD-001.md", "generated", AGENT_C)

    # -- controls ----------------------------------------------------------------------------------
    def test_bashs_brace_expansion_and_every_other_reading_are_unchanged(self):
        # bash expands `{git,push}` into `git push`; zsh runs a command named `git,push`.  The push is still found.
        self.refused_for_members("{git,push}")
        self.refused_for_members("echo {a,b}; {git,push}")
        for refused in ("{ git push }", "{ git push; }", "time { git push; }", "coproc { git push }", "f() { git push }; f",
                        "{ git status } always { git push }", "repeat 1 { git push }", "if [[ -n x ]] { git push }"):
            with self.subTest(refused):
                self.refused_for_members(refused)
        for ok in ("echo {a,b}", "echo ${HOME}", "echo x{a,b}y", "exec {fd}>/dev/null", "echo {}", "find . -exec echo {} \\;",
                   "git status", "{ git status }", "%s --as %s member log '{git push}'" % (self.spud_cli, AGENT_A),
                   "grep -n '{git push}' tests/zzone/k.py", "printf '{git push}\\n' > tests/zzone/k.py",
                   "{git status}", "time {git status}", "{echo hi} > /dev/null"):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
        for line in ("echo {a,b}", "echo ${HOME}", "exec {fd}>/dev/null", "echo {git push}", "{ echo hi; }", "a=${b}"):
            with self.subTest(line):
                self.assertEqual(self.analysis(line).findings, [], line)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        m = load_spud_module()
        for line in ("{" * 5000 + "git push", "{" * 3000 + "git push" + "}" * 3000, "{git status} " * 2000 + "{git push}",
                     "{ " * 1000 + "git push" + "}" * 1000, "{a} always " * 2000 + "{git push}", "}" * 20000,
                     "{true} always {" * 1000 + "git push", "{repeat 1 {" * 500 + "git push", "{x=1}" * 3000,
                     "time {" * 1000 + "git push}", "echo " + "a}" * 20000):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = m.analyse_command(line, m.ShellAnalysis(cwd=str(self.home.path)))
                self.assertLess(time.monotonic() - started, 5.0)
                if line.endswith("git push") or line.endswith("git push}"):
                    self.assertIn(("git", ("push", "push")), a.findings, line[:40])
        # two readings at every level of nested substitutions, each starting the next level in a directory of its own
        command = "git push"
        for _ in range(8):
            command = "{cd /tmp; echo $(%s)}" % command
        started = time.monotonic()
        a = m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))
        self.assertLess(time.monotonic() - started, 5.0)


# SPD-303: a profile whose functions shadow a printer and basename, which only the Bash tool's own shell sources.
NEW_SHELL_SHADOWS = """\
# Snapshot file
# Unset all aliases to avoid conflicts with functions
unalias -a 2>/dev/null || true
# Functions
echo () {
\tprintf '%s\\n' "$@"
}
basename () {
\tcommand basename "$@"
}
gs () {
\tgit status
}
# Shell Options
setopt autocd
# Aliases
alias -- ls='ls -G'
"""


class NewShellSnapshotLookupTest(BashHookCase):
    """SPD-303: SPD-298 made held_text.read_shell_name read no snapshot function in a new shell's text (`sh -c`, `zsh -c`,
    `bash -c`, a shell fed its text on standard input, and every text parsed inside one), which never sources the
    snapshot.  Two other lookups still asked the snapshot at every depth: stdin_text._shadowed, so a profile's `echo`
    function made `sh -c 'echo git status | sh'` read the piped text as unread, though the new shell runs its builtin
    echo; and loop_bindings.basename_runs, so a profile's `basename` left `sh -c 'echo hi > "tests/$(basename a/x.txt)"'`
    unsettled, though the new shell runs the program.  A third lookup outside read_shell_name, line_functions.copy_function's
    `profile` check, took the same shortcut: `functions -c` names a zsh builtin a new shell's `-c` text cannot run, but the
    hook read gs as the profile's function all the same, no matter whose text spelled the copy.  All three now follow
    held_text.snapshot_sourced.  In the Bash tool's own line, where the snapshot's functions stand, every one stays as it was.

    held_shadows.read_shadow needs no guard: it runs only past read_shell_name's own snapshot_sourced gate, in a body
    the sourcing shell runs, where the snapshot's functions do stand."""

    def setUp(self):
        super().setUp()
        snapshots = Path(self.home.env["SPUD_USER_CLAUDE_DIR"]) / "shell-snapshots"
        snapshots.mkdir(parents=True)
        (snapshots / "snapshot-zsh-1700000000000-aaaaaa.sh").write_text(NEW_SHELL_SHADOWS, encoding="utf-8")
        (self.home.path / "tests").mkdir(exist_ok=True)

    def test_a_new_shells_printer_is_its_own(self):
        for line in ("sh -c 'echo git status | sh'", "zsh -c 'echo git status | sh'", "bash -c 'echo git status | sh'",
                     "sh <<< 'echo git status | sh'", "sh -c 'eval \"echo git status | sh\"'"):
            for agent_id in (AGENT_A, AGENT_B):
                with self.subTest(line, agent_id=agent_id):
                    self.assertSilent(line, agent_id)
        for line in ("sh -c 'echo git push | sh'", "zsh -c 'echo git push | sh'"):
            with self.subTest(line):
                self.assertRefused(line, "Law 7")
        # the Bash tool's own shell runs the profile's echo, whose text the hook does not follow
        for line in ("echo git status | sh", "eval 'echo git status | sh'"):
            with self.subTest(line):
                self.assertRefused(line, "")

    def test_a_new_shells_basename_is_the_program(self):
        for line in ("sh -c 'echo hi > \"tests/$(basename a/x.txt)\"'",
                     "zsh -c 'for f in a/x.txt b/y.txt; do echo hi > \"tests/$(basename $f)\"; done'",
                     "bash -c 'n=$(basename a/x.txt); echo hi > \"tests/$n\"'"):
            for agent_id in (AGENT_A, AGENT_B):
                with self.subTest(line, agent_id=agent_id):
                    self.assertSilent(line, agent_id)
        self.assertRefused("sh -c 'echo hi > \"$(basename a/x.txt)\"'", "Law 5")  # the name lands outside tests/
        # the Bash tool's own shell runs the profile's basename, whose output the hook does not settle
        for line in ("echo hi > \"tests/$(basename a/x.txt)\"", "eval 'echo hi > \"tests/$(basename a/x.txt)\"'"):
            with self.subTest(line):
                self.assertRefused(line, "")

    def test_a_new_shells_function_copy_reads_no_profile_body(self):
        for line in ("sh -c 'functions -c gs g; cd tests; g'", "zsh -c 'functions -c gs g; cd tests; g'",
                     "bash -c 'functions -c gs g; cd tests; g'", "sh <<< 'functions -c gs g; cd tests; g'"):
            for agent_id in (AGENT_A, AGENT_B):
                with self.subTest(line, agent_id=agent_id):
                    self.assertSilent(line, agent_id)
        # the Bash tool's own shell sources the profile: gs is still a copy the hook cannot follow
        self.assertRefused("functions -c gs g; cd tests; g", "define the new name")


class SnapshotFunctionRemovalTest(BashHookCase):
    """SPD-305: SPD-281 marked a removal (`unset -f`, `unfunction`, `unhash -f`, `disable -f`, bash's plain `unset`) only
    for bodies the line defines, so a function the shell's snapshot defines was read at every call after the line removed
    it, where the shell runs the command of that name.  For most names that read a body that no longer runs (a refusal
    at worst), but a snapshot cd, pushd, popd or chdir (SPD-304) was read as its body's move where the shell makes the
    builtin's: under a snapshot `cd () { builtin cd u }`, `unset -f cd; cd /tmp; git status` was read as git in ./u, where
    the shell runs it in /tmp.

    Probed 2026-09-24 through tests/probes/shell_probe.py in zsh 5.9 -f -o nobareglobqual and -f and bash 3.2.57: after
    `cd() { builtin cd u; }; unset -f cd; cd /tmp`, `pwd` printed /tmp in all three; so did the same line with the
    function sourced from a file, and the removal reached a later `eval 'cd /tmp'` and `$(cd /tmp; pwd)` alike, while one
    in a subshell, `( unset -f cd ); cd /tmp`, left the body running (./u).  A removal that may not have run -- zsh's own
    `unfunction`, one after `&&`, one of a name the hook cannot read -- leaves the function read beside the command, and a
    cd there unknown (line_functions.held_function)."""

    SNAPSHOT = "# Snapshot file\n# Functions\n%s# Aliases\n"

    def setUp(self):
        super().setUp()
        for name in ("u", "tests", "bin"):
            (self.home.path / name).mkdir(exist_ok=True)
        functions = (("cd", "builtin cd u"), ("pushd", "builtin cd u"), ("gp", "git push"))
        snapshots = Path(self.home.env["SPUD_USER_CLAUDE_DIR"]) / "shell-snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        text = self.SNAPSHOT % "".join("%s () {\n\t%s\n}\n" % f for f in functions)
        (snapshots / "snapshot-zsh-1700000000305-rmrmrm.sh").write_text(text, encoding="utf-8")

    def git_dirs(self, command):
        """The directories each git call of `command` may run in, as the hook's analysis reads them."""
        fresh_process()
        analyse = importlib.import_module("spudlib.shell.analyse")
        syntax = importlib.import_module("spudlib.shell.syntax")
        home = str(self.home.path)
        with mock.patch.dict(os.environ, self.home.env, clear=True):
            a = analyse.analyse_command(command, syntax.ShellAnalysis(cwd=home, home=home))
        return [cwds for _, cwds in a.git_calls]

    def test_the_tickets_evidence(self):
        home = str(self.home.path)
        self.assertEqual(self.git_dirs("cd /tmp; git status"), [frozenset([home + "/u"])])  # the function runs
        self.assertEqual(self.git_dirs("unset -f cd; cd /tmp; git status"), [frozenset(["/tmp"])])

    def test_a_removal_stands_for_every_later_call(self):
        home, u = str(self.home.path), str(self.home.path / "u")
        for command, expected in (
                ("unset -f pushd; pushd /tmp; git status", [{"/tmp"}]),
                ("unset -f -- cd; cd /tmp; git status", [{"/tmp"}]),
                ("builtin unset -f cd; cd /tmp; git status", [{"/tmp"}]),
                ("{ unset -f cd; }; cd /tmp; git status", [{"/tmp"}]),
                ("unset -f cd; eval 'cd /tmp; git status'", [{"/tmp"}]),
                ("unset -f cd; echo $(cd /tmp; git status)", [{"/tmp"}]),
                ("unset -f cd; cd() { builtin cd u; }; cd /tmp; git status", [{u}]),  # a definition after it runs
                # a substitution or an eval read before the removal is read again after it (SPD-296's reading state)
                ("echo $(cd /tmp; git status); unset -f cd; echo $(cd /tmp; git status)", [{u}, {"/tmp"}]),
                ("eval 'cd /tmp; git status'; unset -f cd; eval 'cd /tmp; git status'", [{u}, {"/tmp"}]),
                # a removal in a process of its own leaves the function standing, as before
                ("( unset -f cd ); cd /tmp; git status", [{u}]),
                ("echo $(unset -f cd); cd /tmp; git status", [{u}]),
                ("sh -c 'unset -f cd'; cd /tmp; git status", [{u}]),
                # ... and so does a removal of another name, or of a variable
                ("unset -f gp; cd /tmp; git status", [{u}]),
                ("unset -v cd; cd /tmp; git status", [{u}])):
            with self.subTest(command=command):
                self.assertEqual(self.git_dirs(command), [frozenset(each) for each in expected])
        self.assertEqual(self.git_dirs("cd /tmp; git status"), [frozenset([u])])
        self.assertNotEqual(home, u)

    def test_a_removal_that_may_not_have_run_leaves_the_directory_unknown(self):
        for command in ("true && unset -f cd; cd /tmp; git status",
                        "unfunction cd; cd /tmp; git status",  # zsh's alone
                        "unhash -f cd; cd /tmp; git status",
                        "unset cd; cd /tmp; git status",  # bash's alone, where no variable cd is set
                        "unset -f \"$x\"; cd /tmp; git status",  # a name the hook cannot read
                        "unfunction -m 'c*'; cd /tmp; git status",
                        "if true; then unset -f cd; fi; cd /tmp; git status"):
            with self.subTest(command=command):
                self.assertEqual(self.git_dirs(command), [None])

    def test_a_relative_write_after_the_call_is_held_where_it_lands(self):
        self.write_cd_into_tests()
        self.assertSilent("cd bin; echo x > out.txt")  # the function moves to tests/
        self.assertRefused("unset -f cd; cd bin; echo x > out.txt", "deliverables")  # ... and after the removal cd does not

    def write_cd_into_tests(self):
        snapshots = Path(self.home.env["SPUD_USER_CLAUDE_DIR"]) / "shell-snapshots"
        (snapshots / "snapshot-zsh-1700000000305-rmrmrm.sh").write_text(
            self.SNAPSHOT % "cd () {\n\tbuiltin cd tests\n}\n", encoding="utf-8")

    def test_a_removed_function_is_the_program_of_its_name(self):
        self.assertRefused("gp", "Law 7")  # the snapshot's gp pushes
        for line in ("unset -f gp; gp", "unset -f gp; eval gp", "unset -f gp; echo $(gp)", "unset -f cd gp; gp"):
            with self.subTest(line):
                self.assertSilent(line)
        for line in ("true && unset -f gp; gp", "unfunction gp; gp", "( unset -f gp ); gp", "unset -f gp & gp",
                     "gp; unset -f gp", "unset -f \"$x\"; gp"):
            with self.subTest(line):
                self.assertRefused(line, "Law 7")

    def test_the_two_readings_merge_a_removal(self):
        """line_functions.merge_readings, where _settled drops a removal that surely ran with the bodies it took: the
        snapshot's function stays gone where both readings removed it, and may stand where one did."""
        fresh_process()
        line_functions = importlib.import_module("spudlib.shell.line_functions")
        syntax = importlib.import_module("spudlib.shell.syntax")
        home = str(self.home.path)

        def removal(a, certain):
            mark = line_functions.LineBody(("cd",), None, a)
            mark.removes, mark.certain = True, certain
            return {"cd": {mark}}

        with mock.patch.dict(os.environ, self.home.env, clear=True):
            for zsh_removes, other_removes, expected in ((True, True, None), (True, False, "maybe"),
                                                         (False, True, "maybe"), (None, None, "sure")):
                with self.subTest(zsh=zsh_removes, other=other_removes):
                    a = syntax.ShellAnalysis(cwd=home, home=home)
                    zsh_bodies = {} if zsh_removes is None else removal(a, zsh_removes)
                    a.function_bodies = {} if other_removes is None else removal(a, other_removes)
                    self.assertEqual(line_functions.held_function(a, "cd"), "sure" if other_removes is None else
                                     None if other_removes else "maybe")
                    line_functions.merge_readings(a, zsh_bodies)
                    self.assertEqual(line_functions.held_function(a, "cd"), expected)

    def test_a_copy_of_a_removed_function_copies_nothing(self):
        # zsh's functions -c of a function it no longer holds copies nothing (`no such function`)
        self.assertRefused("functions -c gp g; g", "define the new name")
        self.assertSilent("unset -f gp; functions -c gp g; g")


# SPD-307: a profile whose functions shadow the printers echo and cat, and basename.
PRINTER_SHADOWS = """\
# Snapshot file
# Functions
echo () {
\tprintf '%s\\n' "$@"
}
cat () {
\tcommand cat "$@"
}
basename () {
\tcommand basename "$@"
}
# Aliases
"""


class SnapshotPrinterRemovalTest(BashHookCase):
    """SPD-307: stdin_text._shadowed and loop_bindings.basename_runs asked the snapshot's tables directly (`name in
    table.functions`) rather than line_functions.held_function, so a line's own `unset -f echo` (or `cat`, `basename`)
    left the snapshot's function read as still standing -- an over-refusal, not a hole: the piped text a removed echo
    or cat prints, and the write target a removed basename settles, both stayed unread after the line took the
    function back.  Both now ask held_function, whose "sure" and "maybe" still leave the shadow standing (a removal
    that may not have run keeps today's cautious reading) and whose None -- the removal surely ran -- lets the shell's
    own printer or program through."""

    def setUp(self):
        super().setUp()
        (self.home.path / "tests").mkdir(exist_ok=True)
        snapshots = Path(self.home.env["SPUD_USER_CLAUDE_DIR"]) / "shell-snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        (snapshots / "snapshot-zsh-1700000000307-ppppp.sh").write_text(PRINTER_SHADOWS, encoding="utf-8")

    def test_a_removed_echo_pipes_its_own_text(self):
        # the control: echo's function shadows it, so the piped text is unread
        self.assertRefused("echo git status | sh", "")
        # a removal that surely ran leaves the builtin's own text read
        self.assertSilent("unset -f echo; echo git status | sh")
        self.assertRefused("unset -f echo; echo git push | sh", "Law 7")
        # a removal that may not have run keeps today's cautious reading (unread)
        self.assertRefused("true && unset -f echo; echo git status | sh", "")

    def test_a_removed_cat_pipes_its_own_text(self):
        self.assertRefused("printf 'git status' | cat | sh", "")
        self.assertSilent("unset -f cat; printf 'git status' | cat | sh")
        self.assertRefused("unset -f cat; printf 'git push' | cat | sh", "Law 7")

    def test_a_removed_basename_settles_the_write_target(self):
        # the control: basename's function shadows it, so the substitution stays unsettled
        self.assertRefused('echo hi > "tests/$(basename a/x.txt)"', "hook cannot resolve")
        # a removal that surely ran lets the program's own output settle the write
        self.assertSilent('unset -f basename; echo hi > "tests/$(basename a/x.txt)"')
        # a removal that may not have run keeps today's cautious reading (unsettled)
        self.assertRefused('true && unset -f basename; echo hi > "tests/$(basename a/x.txt)"', "hook cannot resolve")


if __name__ == "__main__":
    unittest.main()
