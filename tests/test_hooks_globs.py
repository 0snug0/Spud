"""PreToolUse(Bash): globs and redirections -- a redirection target the shell expands, zsh's glob operators, arithmetic in
command position, a glob as a command word, read-write and descriptor redirections, a directory the hook cannot enter."""

import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from helpers import load_spud_module, wall_clock
from hookcase import AGENT_A, AGENT_C, SCRIPT_WORDING, BashHookCase


class GlobRedirectTest(BashHookCase):
    """SPD-034: a redirection or tee target the shell expands (an unquoted glob character, a brace list) is checked as
    every file it can open from every candidate directory, not as its literal spelling.  bash and zsh both expand a
    glob in a redirection target before opening it, so `echo x > ledg*/tickets/SPD-00?.md` writes ledger/tickets/SPD-001.md
    (probed in zsh 5.9 and bash 3.2).  A quoted or escaped glob character is literal.  A member is refused when the hook
    cannot know what a glob opens (no match now, the cost bound reached); Spud is checked against every match and the
    literal name bash writes when nothing matches.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        home = self.home.path
        for rel in ("ledger/tickets/SPD-001.md", "ledger/tickets/SPD-002.md", "tests/keep.py", "tests/other.py", "docs/x.md"):
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")

    def test_the_tickets_evidence_glob_writes_a_generated_file(self):
        """The hole from SPUD-030/Elba (proposal 30): the hook saw the literal `ledg*/tickets/SPD-00?.md`, under no root."""
        evidence = "echo overwritten > ledg*/tickets/SPD-00?.md"
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(agent_id=agent_id):
                self.assertRefused(evidence, "generated", agent_id)
        self.assertRefused(evidence, "Law 1", agent_id=None)

    def test_each_glob_character_expands(self):
        for target in ("ledger/tickets/SPD-00?.md", "ledger/tickets/SPD-00[12].md", "ledger/tickets/SPD-00[!x].md",
                       "led*/tickets/SPD-001.md", "led?er/tickets/SPD-001.md", "**/SPD-001.md",
                       "ledger/tickets/SPD-00{1,2}.md", "ledger/tickets/SPD-00{1..2}.md", "{ledger,docs}/tickets/SPD-001.md",
                       "ledger/tickets/../tickets/SPD-00?.md"):
            with self.subTest(target):
                self.assertRefused("echo x > %s" % target, "generated", AGENT_C)
                self.assertRefused("echo x > %s" % target, "Law 1", agent_id=None)

    def test_a_quoted_or_escaped_glob_character_is_literal(self):
        # Quoted or escaped: the literal path ledg*/tickets/SPD-001.md is inside the repo but not a generated file, so it is
        # refused for a member as outside the deliverables (not "generated"), never expanded to ledger/tickets/SPD-001.md.
        for target in ("'ledg*'/tickets/SPD-001.md", '"ledg*"/tickets/SPD-001.md', "ledg\\*/tickets/SPD-001.md",
                       "'ledg*/tickets/SPD-001.md'", "ledg\\?r/tickets/SPD-001.md"):
            with self.subTest(target):
                r = self.assertRefused("echo x > %s" % target, "deliverables", AGENT_A)
                self.assertNotIn("generated", r.reason)
        # Partial quoting still expands the unquoted character.
        self.assertRefused("echo x > 'ledg'*/tickets/SPD-001.md", "generated", AGENT_A)

    def test_a_glob_matching_only_deliverables_is_allowed(self):
        self.assertSilent("echo x > tests/*.py")
        self.assertSilent("echo x > tests/keep.p?")
        self.assertSilent("printf x | tee tests/*.py")
        self.assertSilent("echo x > tests/{keep,other}.py")
        # a quoted glob character in a deliverable target is a literal filename under tests/**
        self.assertSilent("echo x > 'tests/star*.py'")

    def test_several_matches_are_all_checked(self):
        # Both SPD-001.md and SPD-002.md match; a member is refused (generated), Spud on Law 1.
        self.assertRefused("echo x > ledger/tickets/SPD-00?.md", "generated", AGENT_C)
        self.assertRefused("echo x >> ledger/tickets/SPD-00?.md", "Law 1", agent_id=None)
        # a glob that matches a deliverable and a generated file is refused on the generated one
        (self.home.path / "tests" / "tickets").mkdir(parents=True, exist_ok=True)
        (self.home.path / "tests" / "tickets" / "SPD-001.md").write_text("x\n", encoding="utf-8")
        self.assertRefused("echo x > */tickets/SPD-001.md", "generated", AGENT_C)

    def test_a_glob_that_matches_nothing_now(self):
        # A member: refused, since the hook cannot know what the shell opens (a match may appear, or bash writes the name).
        for target in ("tests/nomatch-xyzzy*.py", "ledger/tickets/SPD-09?.md", "no-such-dir-*/x"):
            with self.subTest(target):
                self.assertRefused("echo x > %s" % target, "matches no file", AGENT_A)
                self.assertRefused("echo x | tee %s" % target, "matches no file", AGENT_A)
        # Spud: the literal name a shell writes on no match is checked too -- outside the repository it stays unchecked,
        # inside it is refused (bash writes the file named literally, glob characters and all).
        self.assertSilent("echo x > %s/outside-nomatch*.md" % self.out, agent_id=None)
        self.assertRefused("echo x > tests/nomatch-xyzzy*.py", "Law 1", agent_id=None)

    def test_tee_arguments_expand(self):
        self.assertRefused("printf x | tee ledger/tickets/SPD-00?.md", "generated", AGENT_C)
        self.assertRefused("printf x | tee {ledger,docs}/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertRefused("printf x | tee -a ledger/tickets/SPD-00?.md", "Law 1", agent_id=None)
        self.assertRefused("printf x | tee ledger/tickets/SPD-00?.md tests/keep.py", "generated", AGENT_C)
        self.assertSilent("printf x | tee tests/*.py")

    def test_a_variable_in_a_glob_target(self):
        # SPD-127: a value the line itself settled is put in the target's place, and the glob it spells is expanded and
        # checked as a spelled one is -- the refusal is the path rule's, not the unresolvable-target one.  A variable the
        # line does not settle keeps that refusal, for a member and, since SPD-091, for Spud.
        self.assertRefused("X=ledger; echo x > $X/tickets/SPD-00?.md", "generated", AGENT_C)
        self.assertRefused("X=ledger; echo x > $X/tickets/SPD-001.md", "Law 1", agent_id=None)
        self.assertRefused("echo x > $X/tickets/SPD-00?.md", "spell the path out", AGENT_C)
        self.assertRefused("echo x > $X/tickets/SPD-001.md", "spell the path out", agent_id=None)
        self.assertRefused("echo x > $X/tickets/SPD-00?.md", "spell the path out", agent_id=None)

    def test_tilde_before_a_glob(self):
        home = self.home.path
        self.assertRefused("cd %s && echo x > ~+/ledger/tickets/SPD-00?.md" % home, "generated", AGENT_C)
        self.assertRefused("cd %s && echo x > ~+/ledger/tickets/SPD-00?.md" % home, "Law 1", agent_id=None)
        self.assertRefused("echo x > ~-/ledger/tickets/SPD-00?.md", "cannot follow", AGENT_C)

    def test_a_glob_over_a_directory_the_hook_cannot_follow(self):
        # cwds unknown (a cd the hook cannot follow) plus a glob: refused for a member, and since SPD-035 for Spud too.
        self.assertRefused("cd $DIR; echo x > ledger/tickets/SPD-00?.md", "cannot follow", AGENT_C)
        self.assertRefused("cd $DIR; echo x > ledger/tickets/SPD-00?.md", "cannot follow", agent_id=None)

    def test_the_glob_cost_is_bounded(self):
        # A glob whose expansion reaches the hook's match budget is refused for a member rather than walked without limit.
        cap = load_spud_module().GLOB_MATCH_CAP
        many = self.home.path / "tests" / "many"
        many.mkdir(parents=True, exist_ok=True)
        for i in range(cap + 20):
            (many / ("f%04d.txt" % i)).write_text("x\n", encoding="utf-8")
        self.assertRefused("echo x > tests/many/*.txt", "budget", AGENT_A)

    def test_controls_stay_allowed(self):
        self.assertSilent("ls > /dev/null")
        self.assertSilent("make 2>&1", agent_id=None)
        self.assertSilent("echo x > tests/plain.py")
        self.assertRefused("echo x > docs/plain.md", "deliverables")


class ZshGlobOperatorTest(BashHookCase):
    """SPD-039: zsh reads parenthesised alternation `(a|b)` and the numeric range `<n-m>` (`<->`, `<n->`, `<-m>`) as glob
    operators in a word it expands, where shlex reads `(` as a subshell and `<` as an input redirection.  Probed in zsh 5.9
    with its default options and with nobareglobqual (this Mac's Bash tool), and in bash 3.2: `echo x > (ledger|x)/tickets/
    SPD-001.md` and `echo x > tests/<1-1>/../../ledger/tickets/SPD-002.md` write the ledger file in zsh; bash rejects the `(`
    line and reads `<1-1>` as `< 1-1 >`.  The hook checks both readings: zsh's, the pattern kept whole and expanded as
    SPD-034 expands a glob, and the other shell's.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for rel in ("ledger/tickets/SPD-001.md", "ledger/tickets/SPD-002.md", "ledger/tickets/SPD-010.md", "tests/keep.py", "tests/other.py", "docs/x.md"):
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")
        for d in ("tests/1", "tests/007"):
            (home / d).mkdir(parents=True, exist_ok=True)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        for d in ("1", "007"):
            (self.out / d).mkdir()

    def outside(self, pattern, rel):
        """A target that opens <home>/<rel> through `pattern` matching a directory outside the repository, so that the other
        shell's reading (`<out>/`, then `/../<home>/<rel>` from the root) names only paths outside it."""
        return "%s/%s/../%s/%s" % (self.out, pattern, os.path.relpath(self.home.path, self.out), rel)

    def test_the_tickets_evidence_alternation(self):
        """The hole from SPUD-034/Atlantic (proposal 32): the hook read `(` as a subshell and checked `/tickets/SPD-001.md`."""
        evidence = "echo x > (ledger|x)/tickets/SPD-001.md"
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(agent_id=agent_id):
                self.assertRefused(evidence, "generated", agent_id)
        self.assertRefused(evidence, "Law 1", agent_id=None)
        for target in ("led(ger|x)/tickets/SPD-001.md", "(x|ledger)/(tickets|y)/SPD-00(1|9).md", "ledger/tickets/SPD-001.m(d|x)"):
            with self.subTest(target):
                self.assertRefused("echo x > %s" % target, "generated", AGENT_C)
                self.assertRefused("echo x >| %s" % target, "Law 1", agent_id=None)
        self.assertRefused("echo x &>(ledger|x)/tickets/SPD-001.md", "generated", AGENT_C)  # zsh: &>( and >|( open a glob target
        self.assertRefused("cd %s && echo x > ~+/(ledger|x)/tickets/SPD-001.md" % self.home.path, "generated", AGENT_C)
        self.assertRefused("echo x > (nomatch|zz)/x.py", "matches no file", AGENT_C)

    def test_the_tickets_evidence_range(self):
        """`SPD-<1-1>.md`: bash opens `SPD-` beside the ledger file, which was refused before SPD-039 on that reading alone; a
        range in a directory component sends bash's fragments elsewhere, and only zsh's reading finds the ledger file."""
        self.assertRefused("echo x > ledger/tickets/SPD-<1-1>.md", "generated", AGENT_C)
        self.assertRefused("echo x > ledger/tickets/SPD-<1-1>.md", "Law 1", agent_id=None)
        for pattern in ("<1-1>", "<7-7>", "<01-01>"):  # tests/1 and tests/007
            with self.subTest(pattern):
                for agent_id in (AGENT_C, AGENT_A):
                    self.assertRefused("echo x > tests/%s/../../ledger/tickets/SPD-001.md" % pattern, "generated", agent_id)
                self.assertRefused("echo x > %s" % self.outside(pattern, "ledger/tickets/SPD-001.md"), "Law 1", agent_id=None)
        self.assertRefused("echo x > tests/<1-1>/../../docs/x.md", "deliverables", AGENT_A)
        # bash's reading opens /../../docs/x.md, which is /docs/x.md at the filesystem root: no ledger file, and since
        # SPD-064 refused to a member (even a ** one) for being outside every registered project.
        self.assertRefused("echo x > tests/<1-1>/../../docs/x.md", "outside every registered project", AGENT_C)

    def test_the_open_range(self):
        for pattern in ("<->", "<1->", "<-9>", "<0-7>"):
            with self.subTest(pattern):
                self.assertRefused("echo x > ledger/tickets/SPD-%s.md" % pattern, "generated", AGENT_C)
                for agent_id in (AGENT_C, AGENT_A):
                    self.assertRefused("echo x > tests/%s/../../ledger/tickets/SPD-002.md" % pattern, "generated", agent_id)
                self.assertRefused("echo x > %s" % self.outside(pattern, "ledger/tickets/SPD-002.md"), "Law 1", agent_id=None)
        self.assertRefused("echo x > tests/<2->/../../ledger/tickets/SPD-001.md", "generated", AGENT_A)  # 007
        self.assertRefused("echo x > tests/<8->/../../ledger/tickets/SPD-001.md", "matches no file", AGENT_A)

    def test_nested_alternation(self):
        for target in ("((ledger|y)|x)/tickets/SPD-001.md", "led(g(e|x)r|zz)/tickets/SPD-001.md", "(x|(y|(ledger)))/tickets/SPD-00(1|(2|3)).md",
                       "{docs,(ledger|x)}/tickets/SPD-001.md", "ledger/tickets/SPD-(<1-1>|zz).md"):
            with self.subTest(target):
                self.assertRefused("echo x > %s" % target, "generated", AGENT_C)
                self.assertRefused("echo x > %s" % target, "Law 1", agent_id=None)
        self.assertRefused("echo x > %s" % self.outside("(1|zz)", "ledger/tickets/SPD-001.md"), "Law 1", agent_id=None)

    def test_a_group_zsh_cannot_read_opens_nothing(self):
        # `/` inside a group is a bad pattern in zsh (probed): nothing opens, so a member is refused as for a glob matching nothing.
        self.assertRefused("echo x > (ledger/tickets|x)/SPD-001.md", "matches no file", AGENT_C)
        # `;` `&` `<` `>` inside a group are parse errors in zsh and nothing on the line runs: the other shell's reading stands.
        self.assertRefused("echo x > ledger/tickets/SPD-00(1;|3).md", "generated", AGENT_C)

    def test_the_range_matcher_reads_numbers_as_zsh_does(self):
        """Any digit string whose value is in range, leading zeros included (`SPD-<1-1>.md` wrote SPD-001.md); a reversed range,
        which zsh matches to nothing, is read as the ordered one (checking more files is the safe side)."""
        m = load_spud_module()
        for lo, hi in (("", ""), ("1", ""), ("", "9"), ("1", "1"), ("001", "002"), ("5", "150"), ("0", "0"), ("99", "101"), ("2", "1"), ("7", "1000")):
            rx = re.compile(m.numeric_range_regex(lo, hi) + r"\Z")
            a, b = int(lo or 0), (int(hi) if hi else None)
            if b is not None and b < a:
                a, b = b, a
            for v in range(0, 1200):
                for name in (str(v), "0" + str(v), "00" + str(v)):
                    self.assertEqual(bool(rx.match(name)), a <= v and (b is None or v <= b), (lo, hi, name))
            for name in ("", "-1", "1a", "a1", " 1"):
                self.assertIsNone(rx.match(name), (lo, hi, name))

    def test_tee_arguments(self):
        self.assertRefused("printf x | tee (ledger|x)/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertRefused("printf x | tee -a tests/keep.py (ledger|x)/tickets/SPD-002.md", "generated", AGENT_C)
        self.assertRefused("printf x | tee tests/<1-1>/../../ledger/tickets/SPD-00<1-2>.md", "generated", AGENT_A)
        self.assertRefused("printf x | tee (ledger|x)/tickets/SPD-001.md", "Law 1", agent_id=None)
        self.assertRefused("printf x | tee %s" % self.outside("<->", "ledger/tickets/SPD-001.md"), "Law 1", agent_id=None)
        self.assertSilent("printf x | tee tests/keep.(py|zz)")

    def test_a_trailing_qualifier_shaped_group_is_read_both_ways(self):
        """Decision: zsh's default bareglobqual reads a trailing group with no `|` as glob qualifiers (`(.)` plain files, `(N)`
        null glob), nobareglobqual (this Mac's Bash tool) as a group; the hook checks the files of both readings."""
        self.assertRefused("echo x > (ledger|x)/tickets/SPD-001.md(.)", "Law 1", agent_id=None)  # the qualifier reading
        self.assertRefused("echo x > (ledger|x)/tickets/SPD-001.md(N)", "generated", AGENT_C)
        self.assertRefused("echo x > (ledger|x)/tickets/SPD-001(.md)", "generated", AGENT_C)  # the group reading
        self.assertRefused("echo x > (ledger|x)/tickets/SPD-001(.md)", "Law 1", agent_id=None)
        self.assertSilent("echo x > tests/keep.py(.)")
        self.assertSilent("echo x > tests/(keep|other).py(N)")

    def test_code_in_a_glob_qualifier_is_analysed(self):
        """With bareglobqual (zsh's default) the string of an `e` qualifier, `oe` included, runs for every file the glob matches,
        in the shell that expands it (probed: `(e:"touch ran":)`, `(oe:...:)`, `(e{...})`, `(e[...])` and `(+f)` ran, and a cd
        there moved the command's directory); the hook reads each string as a command run once or more."""
        for word in ("tests/*(e:'git push':)", "tests/*(oe:'git push':)", "tests/keep.py(e{git push})", "tests/keep.py(e[git push])",
                     "tests/*(.e:'git push':)", "(tests|x)/keep.py(e:'git push':)"):
            with self.subTest(word):
                self.assertRefused("ls %s" % word, "Law 7")
                self.assertRefused("echo x > %s" % word, "Law 7")
        self.assertRefused("ls tests/*(e:'echo y > ledger/tickets/SPD-001.md':)", "Law 1", agent_id=None)
        self.assertRefused("ls tests/*(e:'echo y > ledger/tickets/SPD-001.md':)", "generated", AGENT_C)
        self.assertRefused("ls tests/*(e:'cd ledger':) > tickets/SPD-001.md", "cannot follow", AGENT_C)
        for ok in ("ls tests/*(om[1])", "ls tests/*(.)", "ls tests/*(Lk+1)"):
            with self.subTest(ok):
                self.assertSilent(ok)

    def test_a_cd_into_a_zsh_pattern_is_unfollowable(self):
        """zsh follows `cd (ledger|y)` and `pushd (ledger|y)` to what they match (probed); a glob cd target is unfollowable
        (SPD-030), so these are too, rather than a cd to the home directory beside a subshell or an input redirection."""
        for cd in ("cd (ledger|x)", "cd led(ger|x)", "pushd (ledger|x)", "cd tests/<1-1>", "cd <1-1>", "cd tests/<->", "cd -P (ledger|x)"):
            with self.subTest(cd):
                self.assertRefused("%s && echo x > tickets/SPD-001.md" % cd, "cannot follow", AGENT_C)
                self.assertRefused("%s; echo x | tee tickets/SPD-001.md" % cd, "cannot follow", AGENT_C)
        self.assertRefused("cd (ledger|x) && echo x > %s/ledger/tickets/SPD-001.md" % self.home.path, "Law 1", agent_id=None)

    def test_bashs_reading_of_a_range_is_still_checked(self):
        """bash reads `<1-2>` as `< 1-2 >`: `cat <1-2> out` writes out in bash and reads a glob in zsh (probed)."""
        self.assertRefused("cat <1-2> ledger/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertRefused("cat <1-2> ledger/tickets/SPD-001.md", "Law 1", agent_id=None)

    def test_a_subshell_either_shell_runs_is_still_read(self):
        """bash runs `!(...)`, `{(...)}`, `if(...)`, `time(...)`, `then(...)`, `do(...)`, `else(...)`, `time -p (...)`,
        `f()(...)` and `g () (...)` as subshells, and zsh `{(...)}`, `f()(...)` and `g () (...)` only: zsh reads every other
        reserved word glued to `(` as one glob word, `else(` included, which in `if false; then :; else(...); fi` stands in
        the then-body (SPD-174, probed 2026-09-22 through tests/probes/shell_probe.py, `echo <label>` for the push: bash 3.2
        printed the label for each of those ten; zsh 5.9, under -f and -f -o nobareglobqual alike, for `{(`, `f()(` and
        `g () (` alone -- `!(` and `time(` failed with "no matches found" under nobareglobqual and "missing end of string"
        under -f, the group read as glob qualifiers, the else line printed nothing, and `if(...) then :; fi`, `then(` and
        `do(` in their places were parse errors, near fi and near done).  Their commands are checked in the other reading,
        whatever zsh's; GluedReservedWordTest reads zsh's."""
        for cmd in ("!(git push)", "{(git push)}", "if(git push) then :; fi", "time(git push)", "if true; then(git push); fi",
                    "for i in 1; do(git push); done", "if false; then :; else(git push); fi", "time -p (git push)", "f()(git push); f",
                    "g () (git push)", "coproc CO (git push)", "echo a; (git push)"):
            with self.subTest(cmd):
                self.assertRefused(cmd, "Law 7")
        m = load_spud_module()
        for cmd in ("{(git push)}", "f()(git push); f", "g () (git push)"):  # a subshell in zsh's reading too
            with self.subTest(cmd):
                self.assertEqual(m.mark_zsh_patterns(cmd), (cmd, cmd))
        for cmd in ("!(git push)", "time(git push)", "if false; then :; else(git push); fi"):  # one glob word in zsh's
            with self.subTest(cmd):
                marked, other = m.mark_zsh_patterns(cmd)
                self.assertEqual((m.deglob(marked), other), (cmd, cmd))
                self.assertNotIn("(", marked)
        self.assertRefused("{(echo x > ledger/tickets/SPD-001.md)}", "generated", AGENT_C)
        self.assertRefused("time -p (echo x > ledger/tickets/SPD-001.md)", "generated", AGENT_C)

    @wall_clock
    def test_pathological_patterns_neither_raise_nor_grow_quadratic(self):
        """A hook that raises refuses everyone, and one that runs past the harness's timeout protects nothing: deep nesting, a line
        of unbalanced openings, huge range bounds and long qualifier lists are read in bounded time (robustness probe)."""
        m = load_spud_module()
        home = str(self.home.path)
        for command in ("echo x > " + "(" * 4000 + "a" + ")" * 4000, "echo x > " + "(" * 40000 + "a", "echo x > " + "$((" * 20000,
                        "echo x > " + "${" * 40000, "echo x > f<%s-%s>" % ("1" * 5000, "9" * 5000), "ls x(" + "+a" * 20000 + ")",
                        "echo " + "a(" * 20000, "echo x > " + "a" * 200000 + "(b|c)"):
            with self.subTest(command[:24]):
                start = datetime.now()
                a = m.analyse_command(command, m.ShellAnalysis(cwd=home))
                for target, cwds in a.redirects:
                    if m.target_has_active_glob(target):
                        m.expand_redirect_target(target, cwds)
                self.assertLess((datetime.now() - start).total_seconds(), 10)

    def test_both_readings_stay_linear_in_nested_substitutions(self):
        m = load_spud_module()
        walks = []
        original = m.ShellWalk.walk

        def counting(walk, tokens):
            walks.append(1)
            return original(walk, tokens)

        m.ShellWalk.walk = counting
        command = "echo (a|b)"
        for _ in range(6):
            command = "echo (a|b) $(%s)" % command
        m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))
        self.assertLessEqual(len(walks), 16)

    def test_controls_stay_as_they_are(self):
        out = self.out
        self.assertRefused("(cd %s) && echo x > note.txt" % out, "deliverables")  # a subshell's cd does not carry out
        self.assertSilent("(cd %s && echo x > note.txt)" % out)
        self.assertSilent("time (cd %s); echo x > tests/keep.py" % out)
        self.assertSilent("cat <tests/keep.py > tests/out.py")
        self.assertSilent("sort < tests/keep.py > tests/out.py")
        self.assertRefused("cat <tests/keep.py > docs/x.md", "deliverables")
        self.assertRefused("cat <(git push)", "Law 7")
        self.assertSilent("diff <(ls tests) <(ls bin)")
        self.assertRefused("echo x >(tee ledger/tickets/SPD-001.md)", "generated", AGENT_C)
        self.assertSilent("echo x > 'tests/(a|b).py'")
        self.assertSilent('echo x > "tests/(keep|other).py"')
        self.assertSilent("echo x > tests/\\(a\\|b\\).py")
        self.assertRefused("echo x > 'ledger/(a|b).md'", "generated")
        self.assertSilent("echo x > tests/(keep|other).py")
        self.assertRefused("echo x > tests/(keep|other).py", "Law 1", agent_id=None)
        self.assertSilent("make 2>&1", agent_id=None)
        self.assertSilent("cc --version > /dev/null 2>&1")
        self.assertSilent("ls > /dev/null")
        for ok in ("f() { echo hi; }; f", "arr=(a b); echo $arr", "typeset -a arr=(a b)", "x=$(( 1<2 )); (( 3 < 2 )) && echo y",
                   "[[ -n x && ( -d tests ) ]] && echo y", "case x in (x|y) echo y;; esac", "for f in tests/(keep|other).py; do echo $f; done",
                   "noglob echo (a|b)", "echo a |(cat)", "! (true)", "{ (true) }"):
            with self.subTest(ok):
                self.assertSilent(ok)
        # The `>` of an arithmetic command was read as an output operator and the word after it as a target (proposal 103,
        # then SPD-088): marked as arithmetic since, and silent here.  ArithmeticCommandTest holds the whole reading.
        self.assertSilent("x=$(( 1<2 )); (( 3 > 2 )) && echo y")
        self.assertSilent("(( a > b ))")
        self.assertRefused("case x in (x) git push;; esac", "Law 7")
        self.assertAllowed("%s --as %s member log 'a (b|c) <1-2>'" % (self.spud_cli, AGENT_A))


class QualifierCodeAliasTest(BashHookCase):
    """SPD-292: zsh parses an `e` or `+` glob qualifier's code when the glob expands, once for every file it matches, as
    eval parses its words, so an alias the line -- or an eval text before the glob -- defined stands there; the walk read
    that code as part of the text around it, where no alias of the line's stands (and since SPD-286 no alias an eval text
    defines stands in the rest of that text), so `alias gq='git push'; echo *(e:gq:)` reached the hook with no finding.
    Probed in zsh 5.9 -f through tests/probes/shell_probe.py with two files: after `alias ls='echo ALIASED'`, `echo
    *(e:'ls -d /':)` printed `ALIASED -d /` twice and `echo *(+ls)` ran the alias; `eval 'alias l2="echo L2"; echo *(e:l2:)'`
    ran L2; the code's own `alias ls="echo INNER"` stood for the second file and not the first, and an alias it defined
    after running a command ran for the second file; an alias an eval text defined after the glob stood in none of it.
    Under -o nobareglobqual (this Mac's Bash tool) each is `bad pattern`; the hook reads the qualifier reading too.
    AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        for rel in ("tests/keep.py", "tests/other.py"):
            p = self.home.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")

    def refused_for_members(self, command, needle="Law 7"):
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertRefused(command, needle, agent_id)

    def silent_for_everyone(self, command):
        for agent_id in (AGENT_C, AGENT_A, None):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertSilent(command, agent_id)

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def test_the_tickets_evidence(self):
        self.refused_for_members("alias gq='git push'; echo *(e:gq:)")
        self.refused_for_members("eval 'alias gq=\"git push\"; echo *(e:gq:)'")
        self.assertEqual(self.analysis("alias gq='git push'; echo *(e:gq:)").findings, [("git", ("push", "push"))])

    def test_every_qualifier_form_reads_the_lines_alias(self):
        for cmd in ("alias gq='git push'; ls tests/*(e:'gq':)", "alias gq='git push'; ls tests/*(oe:gq:)",
                    "alias gq='git push'; ls tests/keep.py(e{gq})", "alias gq='git push'; ls tests/keep.py(e[gq])",
                    "alias gq='git push'; ls tests/*(.e:'true && gq':)", "alias gq='git push'; ls tests/*(+gq)",
                    "alias gq='git push'; echo x > tests/*(e:gq:)", "alias -s txt='git push'; ls tests/*(e:a.txt:)",
                    "aliases[gq]='git push'; ls tests/*(e:gq:)", "alias gq='git push'; eval 'ls tests/*(e:gq:)'",
                    "ls tests/*(e:gq:); alias gq='git push'; ls tests/*(e:gq:)", "alias gq='git push'; echo $(ls *(e:gq:))"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_an_alias_the_code_defines_stands_for_the_next_match(self):
        """The code runs once for every match, in the line's shell, so what it defines is there when the next match's
        code is parsed (probed: `ls` ran INNER for the second file, `q3` ran for the second file only)."""
        self.refused_for_members("ls tests/*(e:'gq; alias gq=\"git push\"':)")
        found = self.analysis("alias gq='git push'; ls tests/*(e:'alias gq=\"git status\"; gq':)").findings
        self.assertIn(("git", ("push", "push")), found)

    def test_where_the_lines_alias_does_not_reach(self):
        for ok in ("ls tests/*(e:gq:); alias gq='git push'", "alias gq='git push'; unalias gq; ls tests/*(e:gq:)",
                   "alias gq='git status'; ls tests/*(e:gq:)", "alias gq='git push'; ls tests/*(e:'echo gq':)",
                   "alias gq='git push'; ls tests/*(e:'sh -c gq':)", "eval 'ls tests/*(e:gq:); alias gq=\"git push\"'",
                   "alias gq='git push'; ls 'tests/*(e:gq:)'", "alias gq='git push'; ls tests/*(om[1])"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)

    def test_a_definition_that_may_not_have_run(self):
        self.refused_for_members("if true; then alias gq='git status'; fi; ls tests/*(e:gq:)", "cannot resolve")

    def test_a_line_with_no_alias_reads_its_code_as_before(self):
        found = self.analysis("ls tests/*(e:'git push':)")
        self.assertEqual(found.findings, [("git", ("push", "push"))])
        self.assertEqual((found.alias_scope, found.alias_view), (0, None))
        after = self.analysis("alias gq='git push'; ls tests/*(e:gq:); echo done")
        self.assertEqual((after.alias_scope, after.alias_view), (0, None))  # the scope closes with the code's reading


class ArithmeticCommandTest(BashHookCase):
    """SPD-088: `(( ... ))` is an arithmetic command and `$(( ... ))` an arithmetic expansion, and both shells evaluate what
    stands between the parentheses -- the `>` of `(( n > 2 ))` is a comparison, the `|` of `(( a | b ))` a bitwise or, the `;`
    of a `for (( ... ))` header separates its three expressions -- so no file is opened and no second command runs.
    mark_zsh_patterns already found the region and copied it into both readings verbatim, so shlex handed separate_redirects
    `n`, `>`, `2`, and a member's `if (( retries > 3 ))` was refused as a write to a file named 2 wherever the shell's
    directory lay outside its deliverables, and Spud's in a protected one.

    Recorded on the ticket, from analyse_command on the working tree when SPD-088 was filed: `(( 3 > 2 ))`,
    `if (( n > 2 )); then echo x; fi` and `x=$(( 1 > 2 ))` each recorded the target ['2'], `(( i > 0 ))` ['0'],
    `(( n >= 3 ))` ['='] and `(( a > b ))` ['b'], while `let "n > 2"` and `[[ 3 -gt 2 ]]` recorded none; SPD-049's
    differential had already found `x=$(( 1<2 )); (( 3 > 2 )) && echo y` among the 32 member lines that went silent -> deny.
    No shell is probed here: this worktree session's harness refuses to run one, and what the fix restores is arithmetic
    evaluation as bash(1) and zsh(1) define it, which the ticket's own evidence measures the hook against.

    The marking (syntax._ARITH_SENTINELS, with the quoted-glob sentinels for `*?[]{},`, which already mean "not expanded
    here", and _LITERAL_DOLLAR for a `$`) goes into both readings unchanged, arithmetic being arithmetic in both shells.  The
    region's outer parenthesis and its match are left as they are, so ShellWalk opens and closes the frame it always opened
    and its `for (( ... ))` header scan still finds the header's end; every character between them is marked, the inner `(`
    of `((` included, so the segment's first word always begins with a sentinel and no word of an arithmetic command is read
    as a command name, a variable, an assignment or a glob.  An arithmetic command keeps its blanks, so a `$( ... )` inside
    it is still analysed and still stays out of the command word; an expansion is marked whole, blanks included, since it is
    part of a word.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    EVIDENCE = ("(( n > 2 ))", "(( i > 0 ))", "(( 3 > 2 ))", "(( n >= 3 ))", "(( a > b ))",
                "if (( retries > 3 )); then echo x; fi", "x=$(( 1 > 2 ))", "x=$(( 1<2 )); (( 3 > 2 )) && echo y")

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for d in ("docs", "tests", "ledger/tickets"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def test_the_tickets_evidence_records_no_redirect_target(self):
        for command in self.EVIDENCE:
            with self.subTest(command):
                a = self.analysis(command)
                self.assertEqual([t for t, _cwds in a.redirects], [])
                self.assertEqual(a.findings, [])

    def test_the_tickets_evidence_is_silent_wherever_the_shell_stands(self):
        for command in self.EVIDENCE:
            with self.subTest(command):
                self.assertSilent("cd docs && " + command)  # a member, its directory outside its deliverables
                self.assertSilent("cd docs && " + command, AGENT_C)
                self.assertSilent("cd ledger/tickets && " + command, agent_id=None)  # Spud, in a generated directory

    def test_the_other_operators_are_read_as_arithmetic_too(self):
        for command in ("(( n < 2 ))", "(( n >> 1 ))", "(( n << 1 ))", "(( a && b ))", "(( a || b ))", "(( a | b ))",
                        "(( a & b ))", "(( a > b ? 1 : 0 ))", "(( a * b ))", "(( a*b ))", "(( a % b ))", "(( a ^ b ))",
                        "(( ! a ))", "(( ~a ))", "(( n++ ))", "(( a[1] + 2 ))", "(( 16#ff ))", "(( (a) > 2 ))",
                        "(( $n > 2 ))", "(( ${n} > 2 ))"):
            with self.subTest(command):
                a = self.analysis(command)
                self.assertEqual([t for t, _cwds in a.redirects], [])  # nothing opened a target
                self.assertEqual(a.kinds, ["other"])  # ... and nothing split the line into more commands
                self.assertEqual(a.findings, [])
                self.assertSilent("cd docs && " + command)

    def test_a_for_arithmetic_header_is_still_a_header(self):
        for header in ("for (( i = 0; i < 10; i++ )); do %s; done", "for (( i = 0; i < 10; i++ )) do %s; done",
                       "for (( i=0; i<3; i++ )) %s"):
            with self.subTest(header):
                self.assertRefused(header % "git push", "Law 7")  # the body still runs in command position
                self.assertRefused(header % "echo x > docs/x.md", "deliverables")
                self.assertSilent(header % "echo x > tests/out.py")  # ... and the header's own `<` opens nothing
                self.assertEqual([t for t, _c in self.analysis(header % "echo x > out.txt").redirects], ["out.txt"])
        # the loop still closes where it closed: what follows `done` is outside the body
        self.assertRefused("for (( i = 0; i < 2; i++ )); do echo x; done; git push", "Law 7")
        self.assertSilent("for (( i = 0; i < 2; i++ )); do echo x; done")

    def test_a_real_redirection_after_an_arithmetic_command_is_still_checked(self):
        for command, targets in (("(( n > 2 )) > out.txt", ["out.txt"]), ("(( n > 2 )) && echo x > out.txt", ["out.txt"]),
                                 ("x=$(( 1 + 1 )) > out.txt", ["out.txt"]), ("(( n > 2 )) 2> err.txt", ["err.txt"]),
                                 ("(( a )) > f1 ; (( b )) >> f2", ["f1", "f2"])):
            with self.subTest(command):
                self.assertEqual([t for t, _cwds in self.analysis(command).redirects], targets)
        for command in ("(( n > 2 )) > docs/x.md", "(( n > 2 )) && echo x > docs/x.md", "x=$(( 1 + 1 )) > docs/x.md"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables")
                self.assertSilent(command, AGENT_C)
        self.assertRefused("(( n > 2 )) > ledger/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertRefused("(( n > 2 )) > ledger/tickets/SPD-001.md", "Law 1", agent_id=None)

    def test_a_redirection_outside_arithmetic_is_unchanged(self):
        for command, targets in (("echo x > 2", ["2"]), ("echo x > b", ["b"]), ("echo x >> -", ["-"]),
                                 ("echo x 2> 12", ["12"]), ("echo x >| 3", ["3"]), ("echo x >&3", []),
                                 ("echo x 2>&1", []), ("echo x >&-", []), ("echo x >& out", ["out"])):
            with self.subTest(command):
                self.assertEqual([t for t, _cwds in self.analysis(command).redirects], targets)
        for command in ("cd docs && echo x > 2", "cd docs && echo x > b", "cd docs && echo x 2> 12"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables")

    def test_an_unbalanced_opener_is_no_crash_and_no_new_silence(self):
        for command in ("(( ", "((", "(( 1 > ", "$(( 1 + ", "x=$(( 1 + ", "for (( i=0; i<3"):
            with self.subTest(command):
                self.assertEqual([t for t, _cwds in self.analysis(command).redirects], [])
                self.assertSilent(command)
        # ... and the reading that stood before SPD-088 still reads what follows an opener it cannot pair
        self.assertRefused("(( 1 ; git push", "Law 7")
        self.assertRefused("x=$(( 1 + ; echo x > docs/x.md", "deliverables")

    def test_a_substitution_inside_arithmetic_is_still_analysed(self):
        for command in ("(( x = $(git push) ))", "(( $(git push) > 2 ))", "x=$(( $(git push) + 1 ))",
                        "(( $(git push) ))", "for (( i=0; i<$(git push); i++ )); do echo x; done"):
            with self.subTest(command):
                self.assertRefused(command, "Law 7")
        self.assertRefused("(( x = $(echo y > docs/x.md) ))", "deliverables")
        self.assertSilent("(( x = $(echo y > tests/out.py) ))")

    def test_an_expansions_own_dollar_still_names_the_command_word(self):
        """SPD-043's reading, deliberately left alone: only the arithmetic between the parentheses is marked, never the `$`
        in front of them, so `$(( ... ))` standing in the command position still names a command the hook cannot read -- an
        executable named by digits, found on a PATH of the line's own choosing -- and still refuses a member."""
        self.assertRefused("$((1)) push", "the command word")
        self.assertRefused("$(( 1 + 1 ))", "the command word")
        self.assertSilent("echo $(( 1 > 2 ))")  # ... where an expansion that is not the command word is read by nobody
        self.assertSilent("x=$(( 1 > 2 ))")
        self.assertSilent("cd docs && echo $(( 1 > 2 ))")

    def test_both_readings_carry_the_same_marking(self):
        m = load_spud_module()
        for command in ("(( n > 2 ))", "x=$(( 1 > 2 ))", "for (( i=0; i<3; i++ )); do echo x; done", "(( a | b ))"):
            with self.subTest(command):
                marked, other = m.mark_zsh_patterns(command)
                self.assertEqual(marked, other)  # an arithmetic command is arithmetic in bash and zsh alike
                self.assertNotEqual(marked, command)  # ... and it is marked in both
                self.assertEqual(m.deglob(marked), command)  # deglob is its inverse, so a reason names what the line spells
        # a line that holds a zsh pattern as well keeps the two readings, with the same arithmetic in each
        line = "(( n > 2 )); echo x > (ledger|x)/tickets/SPD-001.md"
        marked, other = m.mark_zsh_patterns(line)
        self.assertNotEqual(marked, other)
        self.assertEqual(m.deglob(marked), line)
        self.assertEqual(m.deglob(other), line)
        self.assertRefused(line, "generated", AGENT_C)  # the pattern's target is still expanded and checked

    def test_an_arithmetic_assignment_is_read_as_the_lines_assignment(self):
        """The other half of marking the whole region: `(( x=1 ))` reaches analyse_words behind the arithmetic sentinel that
        keeps the region inert, never as the word `x=1`.  SPD-088 left x unrecorded there, so a later `$x` refused a member;
        SPD-225 reads the assignment itself and records the literal result, so `(( x=1 )); $x` reads as `x=1; $x` does.  An
        arithmetic value is a number, never a command word the hook grants anything for, nor a path."""
        self.assertEqual(self.analysis("(( x=1 ))").vars, {"x": "1"})
        self.assertEqual(self.analysis("(( x=1 )); $x").vars, self.analysis("x=1; $x").vars)
        self.assertSilent("(( x=1 )); $x")  # read as the ordinary assignment below
        self.assertSilent("x=1; $x")  # an ordinary assignment is read as it always was


class GlobCommandWordTest(BashHookCase):
    """SPD-041: both shells expand an unquoted glob, a brace list and zsh's `(a|b)` and `<n-m>` in every word before running a
    command, the command word and git's verb included, and zsh replaces a leading `=name` with the command's path (EQUALS).
    Probed in zsh 5.9 (-f, and -o nobareglobqual as this Mac's Bash tool runs it) and bash 3.2 with a fake git on a scratch
    PATH: `$FAKE/bin/g?t push`, `touch push; git p?sh`, `{git,push}` (bash), `=git push` (zsh), `git [-]p push` with a file
    named -p, `sh [-]c 'git push'`, `spud t?cket new` all ran the hidden command.  The hook reads such a word, where it
    dispatches on it, as every checked name it can match (its literal spelling too, which bash runs when nothing matches), so
    the refusal does not wait for the file a line may create; a word that can become two checked names at once (`(env|git)`)
    is refused for a member.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **; Spud is never refused for git."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for rel in ("ledger/tickets/SPD-001.md", "tests/keep.py", "docs/x.md"):
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")
        (home / "tests" / "tool").symlink_to(self.home.launcher)  # a launcher by another name (SPD-029)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)

    def refused_for_members(self, command, needle, cwd=None):
        return_value = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                return_value = self.assertRefused(command, needle, agent_id, cwd)
        return return_value

    def test_the_tickets_evidence_commands(self):
        for cmd in ("/usr/bin/g?t push", "/usr/bin/g(i|x)t push"):
            r = self.refused_for_members(cmd, "Law 7")
            self.assertIn("git push", r.reason)
            self.assertSilent(cmd, agent_id=None)

    def test_a_glob_in_the_command_word_is_every_command_it_can_become(self):
        for cmd in ("/usr/bin/gi[t] commit -m x", "/usr/bin/g*t push", "g?t push", "./g?t push", "~/bin/g?t push", "/usr/b?n/g?t push",
                    "/usr/bin/G?T push", "g(i|x)t push", "gi(t|x) reset --hard", "/usr/bin/git(N) push", "g?t(.) push", "touch git; g?t push",
                    "true && /usr/bin/g[a-z]t add .", "X=g?t; $X push", "timeout 5 g?t push", "- g?t push", "f() { g?t push; }; f"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")
                # SPD-121: `touch git` is itself a write by argument, and a file named git in the home is a deliverable, so
                # Spud earns Law 1 for it there; his reading of the glob is unchanged, which the same line outside shows.
                self.assertSilent(cmd, agent_id=None, cwd=str(self.out) if cmd.startswith("touch ") else None)
        self.assertSilent("X=g?t; echo $X")  # an assignment's value is not expanded, and the variable is only echoed

    def test_a_verb_glob_matching_a_file_the_line_creates(self):
        """Decision: the verb is read as every write verb its pattern can match, whether or not a file matches now (the line may
        create it), and a pattern that can match no write verb is read as spelled.  Since SPD-047 the spelled reading is refused
        too, as a verb outside git's own commands: the shell expands the pattern against files, so a member that creates a file
        named like an alias runs that alias (`touch zz; git z?`), and the pattern itself is no git command either.  The refusal
        of a write verb the pattern can match still comes first, so `touch push; git p?sh` keeps naming `git push`."""
        for cmd in ("touch push; git p?sh", "touch push && git p*", "git p?sh", "git pu[s]h", "git p(u|x)sh", "touch commit; git c?mmit -m x",
                    "git ch?ckout main", "git st?sh", "git re[s]et --hard", "git st(a|x)sh pop", "git worktree a?d ../x", "git branch -[D] x"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")
        self.assertIn("git push", self.assertRefused("touch push; git p?sh", "Law 7").reason)
        # A pattern that can match no write verb was silent before SPD-047 and is now refused as a verb git does not have;
        # Spud is still not bound by any of it.
        for cmd in ("git st?tus", "git l?g --oneline", "git d[i]ff", "git sh(o|x)w HEAD", "touch status; git st*tus"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd, "Law 7")
                self.assertIn("not one of git's own commands", r.reason)
                # SPD-121: `touch status` writes a file in the home, which is Law 1 for Spud; outside every project it is his
                self.assertSilent(cmd, agent_id=None, cwd=str(self.out) if cmd.startswith("touch ") else None)

    def test_brace_lists_in_the_command_word_and_the_verb(self):
        for cmd in ("{git,push}", "{/usr/bin/git,push}", "git {push,status}", "command {git,push}", "{env,git} push", "git {-C,.} push",
                    "{/usr/bin/g?t,push}"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")
                self.assertSilent(cmd, agent_id=None)
        # git status push; git gxt push; git x push; git /usr/bin/x push (probed: the second word is the verb)
        self.assertSilent("git {status,push}")  # status is one of git's own commands and its arguments are not read
        # The other three put a word git does not have in the verb position, which SPD-047 refuses as an alias git would expand.
        for cmd in ("g{i,x}t push", "{git,x} push", "/usr/bin/{git,x} push"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd, "Law 7")
                self.assertIn("not one of git's own commands", r.reason)
                self.assertSilent(cmd, agent_id=None)

    def test_zsh_equals_expansion(self):
        """zsh replaces `=git` with git's path (EQUALS, on by default and in the Bash tool); bash runs a command named =git.
        `=(...)` is zsh's process substitution, which runs its command: read as one again, as before SPD-039."""
        for cmd in ("=git push", "command =git push", "nice =git push", "env =git push", "exec =git commit -m x", "echo =(git push)",
                    "cat =(git commit -m x)", "diff =(git push) tests/keep.py"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")
        self.refused_for_members("python3.14 -I -S =spud ticket new --title x", "Law 6")
        for ok in ("echo a=b c==d", "[ a = b ] && echo y", "test x = y", "echo =git"):
            with self.subTest(ok):
                self.assertSilent(ok)

    def test_a_wrappers_command_word_and_options(self):
        for cmd in ("env g?t push", "command g?t push", "exec g?t push", "nice -n 5 g?t push", "NOHUP g?t push", "sudo -u root g?t push",
                    "xargs g?t push", "time g?t push", "env -u X g?t push", "e?v git push", "n?hup git push", "/usr/bin/e[n]v git push",
                    "nice -[n] 5 git push", "sudo -[u] root git push", "env {-u,X} git push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")

    def test_git_global_options_before_a_glob_verb(self):
        for cmd in ("git -C . p?sh", "git -c k=v c?mmit -m x", "git --no-pager p?sh", "git -C /tmp -c k=v p?sh", "git [-]p push",
                    "git -[C] . push", "git -C [.p]ush status", "git nomatch(N) push", "git -C tests/(keep|x) p?sh"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")

    def test_a_glob_in_a_spud_invocation(self):
        home, tool = self.home.path, self.home.tool  # the launcher is the tool's bin/spud, and tests/tool in the home names it
        for cmd in ("python3.14 -I -S %s/bin/sp?d ticket new --title x" % tool, "%s/bin/sp[u]d --as spud board" % tool,
                    "python3.14 -I -S %s/bin/sp(u|x)d init" % tool, "cd %s && python3.14 -I -S bin/sp?d render" % tool,
                    "python3.14 -I -S %s/t*s/tool --as spud board" % home, "%s t?cket new --title x" % self.spud_cli,
                    "%s --a[s] spud board" % self.spud_cli, "%s ticket {new,show} --title x" % self.spud_cli, "python3.1[4] -I -S %s/bin/spud init" % tool):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 6")
        self.assertRefused("python3.14 -I -S %s/bin/sp?d --as %s member log hi" % (tool, AGENT_A), "Law 5", agent_id=None)
        self.assertRefused("%s/bin/sp?d hook PreToolUse" % tool, "hook", agent_id=None)
        self.assertSilent("python3.14 -I -S %s/bin/sp?d --as %s member log hi" % (tool, AGENT_A))  # recognized, never allowed

    def test_a_tee_or_cd_behind_a_glob(self):
        home, out = self.home.path, self.out
        self.assertRefused("printf x | t?e ledger/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertRefused("printf x | t?e ledger/tickets/SPD-001.md", "Law 1", agent_id=None)
        self.assertRefused("printf x | /usr/bin/t(e|x)e docs/x.md", "deliverables", AGENT_A)
        # `c[d]` matches cd alone; since SPD-121 cp is a checked name too, so `c?` can become two at once and a member is
        # refused for the glob itself, while Spud's reading of the cd is unchanged.
        cmd = "c[d] %s/ledger && echo x > tickets/SPD-001.md" % home
        self.assertRefused(cmd, "generated", AGENT_C, cwd=str(out))
        self.assertRefused(cmd, "Law 1", agent_id=None, cwd=str(out))
        self.assertRefused("c? %s/ledger && echo x > tickets/SPD-001.md" % home, "glob", AGENT_C, cwd=str(out))
        self.assertRefused("c? %s/ledger && echo x > tickets/SPD-001.md" % home, "Law 1", agent_id=None, cwd=str(out))
        self.assertSilent("cd %s && echo x > tickets/SPD-001.md" % out, AGENT_C, cwd=str(out))

    def test_shell_strings_read_their_own_globs(self):
        """A glob quoted for the outer shell is unquoted in the string a shell or eval runs (probed: `sh -c 'g?t push'` and
        `eval 'g?t push'` pushed): the string is read with its quotes, the hook's quoting marks cleared."""
        for cmd in ("sh -c 'g?t push'", "bash -c \"/usr/bin/g?t push\"", "eval 'g?t push'", "zsh -c 'git p?sh'", "sh [-]c 'git push'",
                    "script -c 'g?t push' typescript"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")
        for cmd in ("sh -c 'echo x > ledg*/tickets/SPD-00?.md'", "eval 'echo x > ledg*/tickets/SPD-001.md'"):
            with self.subTest(cmd):
                self.assertRefused(cmd, "generated", AGENT_C)
                self.assertRefused(cmd, "Law 1", agent_id=None)
        self.assertSilent("sh -c \"echo 'g?t push'\"")

    def test_a_glob_that_can_become_two_checked_words_is_refused_for_a_member(self):
        """`* x` ran `git push x` with files git and push, `* push` ran `env git push` with files env and git (probed): one
        glob that can match two checked names can put both on the line, so a member is refused whatever each reading finds."""
        for cmd in ("[gp][iu][st]* x", "/usr/bin/(env|git) status","%s member log *" % self.spud_cli, "git -C * status", "/usr/bin/" + "*" * 3 + "g*t*x*y push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "glob")
                self.assertSilent(cmd, agent_id=None)
        self.refused_for_members("* push", "Law 7")  # one reading is git push

    @wall_clock
    def test_pathological_glob_words_are_read_in_bounded_time(self):
        m = load_spud_module()
        home = str(self.home.path)
        for command in ("*" * 5000 + "x push", "?" * 3000 + " push", "git " + "*a" * 2000, "/usr/bin/" + "*a" * 50 + "b push",
                        "git " + "{a,b}" * 20, "spud " + " ".join("a%d*" % i for i in range(3000)), "(" + "a|" * 3000 + "b)x push",
                        "git " + "[ab]" * 2000 + "*" * 3 + "c"):
            with self.subTest(command[:24]):
                start = datetime.now()
                m.analyse_command(command, m.ShellAnalysis(cwd=home))
                self.assertLess((datetime.now() - start).total_seconds(), 10)

    def test_controls_keep_todays_reading(self):
        out = self.out
        for ok in ("'g?t' push", "g\\?t push", '"g*t" push', "ls *.py", "ls tests/*", "grep 'a*' tests/keep.py", "grep -n x tests/*.py",
                   "git log -- '*.py'", "git diff HEAD -- tests/*", "git log --oneline -- tests/*.py", "git show HEAD:tests/*.py",
                   "[[ x == g?t ]] && echo y", "case g?t in g?t) echo y;; esac", "arr=(g?t push); echo $arr", "echo $((1*2))",
                   "x=$((2*3)); echo $x", "[ -f tests/keep.py ] && echo y", "for f in tests/*.py; do echo $f; done", "echo g?t push",
                   "git branch --list 'feat*'", "git tag -l v1.*", "python3.14 -I -S -m unittest discover -s tests -t tests",
                   "command -v g?t", "find . -name '*.py'", "(cd %s && echo x > note.txt)" % out,
                   "python3 tests/k*.py", "env FOO=1 ls *.py", "git -C tests/* status"):
            with self.subTest(ok):
                self.assertSilent(ok)
        # SPD-145: a shell's script operand and a path run as a command are a member's file of commands; a glob in either may
        # name a file anywhere, so it is refused whatever it matches, and Spud's reading is unchanged
        for cmd in ("bash tests/*.sh", "/usr/bin/nomatch-spd-041* push"):
            with self.subTest(cmd):
                self.assertRefused(cmd, SCRIPT_WORDING)
                self.assertSilent(cmd, agent_id=None)
        self.assertRefused("(cd %s) && echo x > note.txt" % out, "deliverables")
        self.assertAllowed("%s --as %s member log 'a*b (c|d) =e {f,g}'" % (self.spud_cli, AGENT_A))
        self.assertRefused("git push", "Law 7")


class WrapperClusterGlobTest(BashHookCase):
    """SPD-138: a glob inside a wrapper's option cluster is read as every cluster the wrapper's own getopt can take it for,
    a value option included -- the letter that takes the rest of the word or the next word -- as a whole-word option glob
    (`env -? ...`) already was.  env(1) and sudo(8) read a cluster with getopt: the first letter that takes a value ends it
    (`env -iS 'echo hi'` split its string, probed with tests/probes/shell_probe.py).  A cluster whose letters the reader
    cannot settle fails closed for a member (SPD-217); Spud's answers do not change.  AGENT_A plans tests/** and bin/spud."""

    def setUp(self):
        super().setUp()
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        self.planted = Path(tempfile.mkdtemp(prefix="spud-cluster-")).resolve()
        self.addCleanup(shutil.rmtree, self.planted, True)
        (self.planted / "-iS").write_text("", encoding="utf-8")  # the file zsh expands `-i?` to

    def test_the_tickets_lines_are_refused_for_a_member(self):
        lines = ("env -i? 'git push'", "env -v? 'git push'", "env -i? %s touch f" % self.out, "sudo -n? %s touch f" % self.out)
        for cwd in (None, str(self.planted)):
            for cmd in lines:
                with self.subTest(cmd=cmd, cwd=cwd):
                    self.assertRefused(cmd, "", AGENT_A, cwd)

    def test_a_split_string_reached_through_a_cluster_names_the_push(self):
        for cmd in ("env -i? 'git push'", "env -v? 'git push'", "env -i[S] 'git push'"):
            with self.subTest(cmd):
                self.assertIn("git push", self.assertRefused(cmd, "Law 7", AGENT_A).reason)

    def test_spelled_clusters_and_a_whole_word_option_glob_read_as_today(self):
        for ok in ("env -i ls", "env -iv ls", "env -i 'x'", "sudo -n ls", "nice -n 5 ls", "env -u X ls"):
            with self.subTest(ok):
                self.assertSilent(ok)
        self.assertIn("git push", self.assertRefused("env -? 'git push'", "Law 7", AGENT_A).reason)
        self.assertRefused("env -iS 'git push'", "Law 7", AGENT_A)

    def test_spud_is_allowed_the_split_string_and_whole_word_lines(self):
        # The git-push lines and the whole-word option glob read as before: Spud is never bound by a git finding.
        for cmd in ("env -i? 'git push'", "env -v? 'git push'", "env -? 'git push'", "env -iS 'git push'"):
            for cwd in (None, str(self.planted)):
                with self.subTest(cmd=cmd, cwd=cwd):
                    self.assertSilent(cmd, agent_id=None, cwd=cwd)

    def test_arg_write_command_clusters_close_the_same_gap(self):
        # Same kind, in scope: an arg-write command's getopt cluster (syntax.ARG_WRITE_COMMANDS' value letters) had the same
        # gap.  `sed -n? '' f` can become `sed -ni '' f` (i=in-place, probed: BSD sed writes f), so a member is refused the
        # in-place write it hid, while `sed -n '' f` (no in-place) stays silent.  Spud is not bound by an outside write.
        target = "%s/x.txt" % self.out
        self.assertRefused("sed -n? '' %s" % target, "write by argument", AGENT_A)
        self.assertSilent("sed -n '' %s" % target)
        for cwd in (None, str(self.planted)):
            self.assertSilent("sed -n? '' %s" % target, agent_id=None, cwd=cwd)
        self.assertSilent("sed -n? '' tests/keep.py")  # a deliverable in place: allowed

    def test_spud_now_sees_the_write_the_cluster_hid(self):
        # SPD-128's shape: `env -i? <dir> touch f` read <dir> as the command and recorded no write.  The cluster reading now
        # reaches `env -iu <dir> touch f` (u=--unset), whose `touch f` runs in the cwd, so Spud earns Law 1 in the home for
        # it (as `touch push` in the home already did) and is allowed where the cwd is outside every project.
        for cmd in ("env -i? %s touch f" % self.out, "sudo -n? %s touch f" % self.out):
            with self.subTest(cmd=cmd):
                self.assertRefused(cmd, "Law 1", agent_id=None)  # cwd None: the home
                self.assertSilent(cmd, agent_id=None, cwd=str(self.out))


class GlobGroupSegmentTest(BashHookCase):
    """SPD-179, filed by SPD-174's engineer: globbing.glob_readings split a command word at its last `/` even inside a zsh
    group, and redirect_globs._segment_regex read a segment it could not compile -- the group's unbalanced close -- as
    matching every name, the database's first: `time(ls /tmp)` and a write inside a glued word's qualifier code were
    refused to members and Spud alike in the database's words, and Spud's harmless `else(e:'echo x > /tmp/k':)` too.

    Probed 2026-09-24 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, and in GNU bash 3.2.57:

    - a `/` in a group outside a bracket is a bad pattern in both zsh modes: `time(ls /tmp)` (-f: "number expected", its
      group read as qualifiers), `else(e:'echo x > sub/t':)` under nobareglobqual (-f ran the code: sub/t written),
      `echo e(1|/)`, `ech(o|/x) x`, `echo (a|b/c)d` and `/(bin/ec|x)ho x`; bash ran `time` and a subshell for the first;
    - a `/` inside a bracket inside a group is not: `/bin/(e|[/])cho x` and `/bin/ec(h|[/])o x` ran echo, `echo d1/d(2|[/])/f`
      printed d1/d2/f, and `echo x > d1/d2/(f|[/])` wrote d1/d2/f; a `/` inside a bracket outside every group still splits
      the path (`echo d1[/]d2/f` matched nothing with d1/d2/f present); a group before a `/` is read as ever
      (`/(bin|x)/echo x`, `/bin/(ech|x)o x` ran echo);
    - a bracket's POSIX class matches as the class does, in both zsh modes and bash: `/bin/ec[[:alpha:]]o x`,
      `[[:lower:]]`, `[![:digit:]]` and `[[:alpha:]-]` ran echo, and zsh's own `[[:IDENT:]]` and `[[:WORD:]]` too (bash
      ran the word as spelled); a class zsh does not have (`[[:bogus:]]`) and a reversed range (`[z-a]`) match nothing in
      zsh, and bash ran the word as spelled.  The regex read `[[:alpha:]]` as the set `[:alph` and a `]`, so `g[[:alpha:]]t
      push` was read as no command at all, and `[z-a]` as a regex error, every name.

    A command word is now split at its last `/` outside every group, a segment keeping a `/` inside a group matches no
    name as zsh matches no file, a segment zsh calls a bad pattern matches none, a class matches as it does, and a segment
    the reader still cannot compile makes the word a glob the hook cannot read.  AGENT_A plans tests/** and bin/spud;
    AGENT_C plans **; Spud is never refused for git."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for rel in ("ledger/tickets/SPD-001.md", "tests/keep.py", "docs/x.md"):
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        self.m = load_spud_module()

    def kinds(self, command):
        return set(self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path))).kinds)

    def assertNotDatabase(self, command, agent_id, cwd=None):
        r = self.bash(command, agent_id, cwd)
        self.assertNotIn("database", r.reason or "", (command, agent_id))
        return r

    def test_the_tickets_evidence(self):
        for line in ("time(ls /tmp)", "else(e:'echo x > /tmp/k':)", "if true; then :; else(e:'echo x > /tmp/k':); fi"):
            with self.subTest(line=line):
                self.assertNotIn("db", self.kinds(line))
                self.assertSilent(line, agent_id=None)
                for agent_id in (AGENT_A, AGENT_C):
                    self.assertNotDatabase(line, agent_id)
        # zsh runs nothing for `time(ls /tmp)` and bash runs `time (ls /tmp)`: a member is refused for the word as spelled,
        # a path run as a command, the one reading that runs a file
        self.assertRefused("time(ls /tmp)", SCRIPT_WORDING, AGENT_C)
        # a write in the code is the path rule's, in its own words
        self.assertRefused("else(e:'echo x > %s/k':)" % self.out, "outside", AGENT_A)
        self.assertSilent("else(e:'echo x > %s/k':)" % self.out, agent_id=None)
        line = "if true; then :; else(e:'echo x > ledger/tickets/SPD-001.md':); fi"
        self.assertNotIn("db", self.kinds(line))
        self.assertRefused(line, "Law 1", agent_id=None)
        self.assertRefused(line, "generated", AGENT_C)
        self.assertRefused(line, "generated", AGENT_A)

    def test_a_command_word_splits_at_its_last_slash_outside_a_group(self):
        for cmd in ("/usr/bin/(g|[/])it push", "/usr/(bin|x)/g(i|x)t push", "/usr/bin/g(i|[/])t push", "g(i|[/])t push"):
            with self.subTest(cmd):
                self.assertNotIn("db", self.kinds(cmd))
                r = self.assertRefused(cmd, "Law 7", AGENT_C)
                self.assertIn("git push", r.reason)
                self.assertSilent(cmd, agent_id=None)

    def test_a_group_holding_a_slash_matches_no_command(self):
        """zsh calls each a bad pattern and runs nothing, and bash rejects the `(`: no reading is the database or git."""
        for cmd in ("g(i|/)t push", "/usr/bin/g(i|/x)t push", "/usr/(bin/g|x)it push", "/usr/bin/s(qlite3|/x)"):
            with self.subTest(cmd):
                kinds = self.kinds(cmd)
                self.assertNotIn("db", kinds)
                self.assertNotIn("git", kinds)
                self.assertSilent(cmd, agent_id=None)
                self.assertNotDatabase(cmd, AGENT_C)

    def test_a_slash_in_a_bracket_inside_a_group_opens_its_files(self):
        """`echo x > d1/d2/(f|[/])` wrote d1/d2/f in zsh: the bracket's `/` neither splits the path nor spoils the group."""
        for target in ("ledger/tickets/SPD-00(1|[/]).md", "ledger/(tickets|[/])/SPD-001.md"):
            with self.subTest(target):
                self.assertRefused("echo x > %s" % target, "generated", AGENT_C)
                self.assertRefused("echo x > %s" % target, "Law 1", agent_id=None)
        self.assertRefused("echo x > (ledger/tickets|x)/SPD-001.md", "matches no file", AGENT_C)  # a bad pattern still

    def test_a_posix_class_matches_as_the_class_does(self):
        for cmd in ("g[[:alpha:]]t push", "/usr/bin/g[[:lower:]]t push", "gi[![:digit:]] push", "g[[:alnum:]-]t push",
                    "g[[:IDENT:]]t push", "g[[:WORD:]]t push", "[[:alpha:]]it push"):
            with self.subTest(cmd):
                r = self.assertRefused(cmd, "Law 7", AGENT_C)
                self.assertIn("git push", r.reason)
                self.assertSilent(cmd, agent_id=None)
        for target in ("ledger/tickets/SPD-00[[:digit:]].md", "ledger/tickets/SPD-[[:digit:]][[:digit:]][![:alpha:]].md"):
            with self.subTest(target):
                self.assertRefused("echo x > %s" % target, "generated", AGENT_C)
                self.assertRefused("echo x > %s" % target, "Law 1", agent_id=None)
        m = self.m
        for pattern, yes, no in (("[[:alpha:]]", "aZ", "1_-"), ("[![:alpha:]]", "1_-", "aZ"), ("[[:digit:]x]", "0x", "ay"),
                                 ("[[:space:]]", " \t", "a"), ("[[:punct:]]", "-_!", "a1"), ("[[:xdigit:]]", "0fA", "g"),
                                 ("[[:upper:][:digit:]]", "A5", "a"), ("[[:bogus:]]", "", "a1-"), ("[z-a]", "", "amz"),
                                 ("[a-c]", "abc", "d"), ("[]a]", "]a", "b"), ("[!]a]", "b", "]a")):
            rx = m._segment_regex(pattern)
            for name in yes:
                self.assertTrue(rx.match(name), (pattern, name))
            for name in no:
                self.assertFalse(rx.match(name), (pattern, name))

    def test_a_segment_zsh_calls_a_bad_pattern_matches_no_name(self):
        m = self.m
        for seg in ("tmp" + m.ZSH_CLOSE, m.ZSH_OPEN + "a", "a" + m.ZSH_OPEN + "b/c" + m.ZSH_CLOSE, "[z-a]"):
            with self.subTest(seg=seg):
                rx = m._segment_regex(seg)
                self.assertIsNotNone(rx)
                for name in ("tmp", "a", "sqlite3", "git", ""):
                    self.assertFalse(rx.match(name), (seg, name))
                self.assertEqual(m.glob_sample_matches(seg, True), frozenset())

    def test_a_segment_the_reader_cannot_compile_is_an_unreadable_glob(self):
        """Nesting deeper than the regex engine compiles: the command word is a glob the hook cannot read, never the
        database; a redirection's segment is every name, checked, and a member is refused at the budget."""
        m = self.m
        deep = m.ZSH_OPEN * 2000 + "git" + m.ZSH_CLOSE * 2000
        self.assertIsNone(m._segment_regex(deep))
        self.assertTrue(m.bounded_glob(str(self.home.path / "ledger" / "tickets") + "/" + deep)[1])

    def test_controls(self):
        for ok in ("ls /tmp", "time ls /tmp", "time (ls /tmp)", "echo (a|b)/c", "ls tests/(keep|x).py", "ls tests/[[:alpha:]]*"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)


class ReadWriteRedirectTest(BashHookCase):
    """SPD-040: the read-write redirection `<>` opens its target O_RDWR|O_CREAT, so it writes or creates the file, and `1<>`
    lets the command overwrite it from the start without truncating.  Before, the hook read `<>` as an input redirection and
    dropped its operand unchecked.  Probed in zsh 5.9 -f, zsh -f -o nobareglobqual (this Mac's Bash tool), bash 3.2 and sh,
    scratchpad files only: bare `<>f`, spaced `<> f`, `0<>`, `1<>`, `2<>`, `9<>`, `10<>`, a leading `<>f echo x`, a quoted
    operand, `<>f<>g` (both), and the same inside `sh -c`, `bash -c`, `zsh -c`, `eval`, a subshell and `$(...)` each created
    its file; `printf abcd > g; echo x 1<>g` left `x` then `cd`.  An operand that is all digits or `-` is a file name after
    `<>` (`<>3` and `<>-` created files named 3 and -); `1<>&2` is a syntax error in both shells, so `<>` has no dup form.
    `exec {fd}<>f` created f in all four (zsh's named descriptor, bash 3.2's command word).  `<>` goes through every check an
    output target gets: the path rule, glob, brace and zsh-pattern readings, the directories before it, Spud's Law 1 and the
    state directory.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    EVIDENCE = "echo x 1<>ledger/tickets/SPD-001.md"

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for rel in ("ledger/tickets/SPD-001.md", "ledger/tickets/SPD-002.md", "tests/keep.py", "docs/x.md"):
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")

    def refused_for_all(self, command):
        """A generated file: refused for both members (whatever their deliverables) and for Spud under Law 1."""
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertRefused(command, "generated", agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertRefused(command, "Law 1", agent_id=None)

    def test_the_tickets_evidence_overwrites_a_generated_file(self):
        """Proposal 33 (SPUD-039/Bintje): `echo x 1<>ledger/tickets/SPD-001.md` changed the file and the hook recorded no redirect."""
        self.refused_for_all(self.EVIDENCE)
        m = load_spud_module()
        self.assertEqual([t for t, _cwds in m.analyse_command(self.EVIDENCE).redirects], ["ledger/tickets/SPD-001.md"])

    def test_every_spelling_of_the_operator_keeps_its_operand(self):
        target = "ledger/tickets/SPD-001.md"
        for command in ("echo x <>%s", "echo x <> %s", "echo x 0<>%s", "echo x 1<> %s", "echo x 2<>%s", "echo x 9<>%s",
                        "echo x 10<>%s", "<>%s echo x", "1<>%s echo x", "echo x 1<>'%s'", 'echo x 1<>"%s"', "cat <>%s",
                        "echo x <>tests/keep.py<>%s", "echo x 1<>tests/keep.py 2<>%s", "echo x 1<>%s 2>&1", "exec {fd}<>%s",
                        "exec 3<>%s", "echo x >/dev/null 1<>%s"):
            self.refused_for_all(command % target)

    def test_the_operand_is_a_file_name_whatever_its_shape(self):
        # `<>3` and `<>-` created files named 3 and - (probed): neither is a descriptor or a close after `<>`.  The home is
        # the cwd, outside AGENT_A's deliverables.
        for command in ("echo x <>3", "echo x 1<>3", "echo x <> -", "echo x <>docs/x.md", "echo x 1<>docs/new.md"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables", AGENT_A)
        self.assertSilent("echo x 1<>tests/keep.py")
        self.assertSilent("echo x <> tests/new.py")
        self.assertSilent("echo x 1<>3", agent_id=AGENT_C)

    def test_inside_shell_strings_eval_and_subshells(self):
        target = "ledger/tickets/SPD-001.md"
        for command in ("sh -c 'echo x 1<>%s'", "bash -c 'echo x <>%s'", "zsh -c 'echo x 2<> %s'", "eval 'echo x 1<>%s'",
                        "(echo x 1<>%s)", "echo $(echo x 1<>%s)", "echo `echo x 1<>%s`", "{ echo x; } 1<>%s", "(echo x) 1<>%s",
                        "if true; then echo x 1<>%s; fi", "for f in a; do echo x 1<>%s; done", "true && echo x <>%s",
                        "echo x | cat 1<>%s", "env sh -c 'echo x 1<>%s'", "sh -c \"eval 'echo x 1<>%s'\""):
            self.refused_for_all(command % target)

    def test_a_glob_brace_or_zsh_pattern_operand_is_expanded(self):
        # SPD-034, SPD-039 and SPD-041's readings of an output target apply to `<>` alike.
        for target in ("ledger/tickets/SPD-00?.md", "led*/tickets/SPD-001.md", "ledger/tickets/SPD-00[12].md",
                       "{ledger,docs}/tickets/SPD-001.md", "ledger/tickets/SPD-00{1,2}.md", "(ledger|x)/tickets/SPD-001.md",
                       "ledger/tickets/SPD-<1-1>.md"):
            with self.subTest(target):
                self.assertRefused("echo x 1<>%s" % target, "generated", AGENT_C)
                self.assertRefused("echo x <>%s" % target, "Law 1", agent_id=None)
        self.assertRefused("sh -c 'echo x 1<>ledg*/tickets/SPD-001.md'", "generated", AGENT_C)
        self.assertRefused("echo x 1<>tests/nomatch-xyzzy*.py", "matches no file", AGENT_A)
        self.assertSilent("echo x 1<>tests/k*.py")

    def test_the_directories_before_it_are_followed(self):
        home = self.home.path
        self.assertRefused("cd ledger && echo x 1<>tickets/SPD-001.md", "generated", AGENT_C)
        self.assertRefused("cd %s/ledger; echo x <>tickets/SPD-001.md" % home, "Law 1", agent_id=None)
        self.assertRefused("{ cd ledger; true; } 1<>tickets/SPD-001.md", "generated", AGENT_C)  # a compound's redirection (redirect_cwds)
        self.assertRefused("cd $DIR; echo x 1<>tests/keep.py", "cannot follow", AGENT_A)
        self.assertRefused("echo x 1<>~+/ledger/tickets/SPD-001.md", "generated", AGENT_C)

    def test_the_state_directory_and_a_variable_target(self):
        for agent_id in (AGENT_A, None):
            with self.subTest(agent_id=agent_id):
                # `>.spud/` escapes DB_PATH_RE (a `>` before `.spud`), so the target itself is what refuses it
                self.assertRefused("echo x 1<>.spud/pycache/x", "ledger database", agent_id)
                self.assertRefused("cd .spud && echo x <>backups/x", "ledger database", agent_id)
                # SPD-091: a target the line does not settle refuses Spud too, as SPD-035's unfollowable one does
                self.assertRefused("echo x 1<>$T", "spell the path out", agent_id)

    def test_a_spud_call_with_a_read_write_redirection_is_not_allowed(self):
        # The allow needs every redirection quiet (SPD-032): a `<>` into a file is a write the prompt would ask about.
        log = "%s --as %s member log x" % (self.spud_cli, AGENT_A)
        self.assertSilent(log + " 1<>tests/keep.py")
        self.assertRefused(log + " 1<>docs/x.md", "deliverables")
        self.assertAllowed(log + " <>/dev/null")

    def test_input_redirections_are_unchanged(self):
        for ok in ("cat <ledger/tickets/SPD-001.md", "cat < docs/x.md", "cat 0<docs/x.md", "wc -l <ledger/tickets/SPD-001.md",
                   "cat <<<ledger/tickets/SPD-001.md", "cat <<EOF\nledger/tickets/SPD-001.md\nEOF", "cat <<-EOF\n\tx\n\tEOF",
                   "cat <&0", "cat 3<&0", "cat <&-", "diff <(cat docs/x.md) docs/x.md", "cc --version 2>&1", "ls > /dev/null",
                   "exec 3<&0", "echo x >&2", "sh -c 'cat <docs/x.md'"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        self.assertSilent("cat <tests/keep.py >tests/new.py")
        self.assertAllowed("%s --as %s member log x < docs/x.md" % (self.spud_cli, AGENT_A))
        self.assertRefused("cat <tests/keep.py >docs/x.md", "deliverables")
        m = load_spud_module()
        for command in ("cat <f", "cat 0<f", "cat <<<f", "cat <&3", "cat <<EOF\nf\nEOF"):
            with self.subTest(command):
                self.assertEqual(m.analyse_command(command).redirects, [])


class DescriptorRedirectTest(BashHookCase):
    """SPD-045 (proposed by SPUD-040/Bintje): separate_redirects dropped any operand that is all digits or `-` for every
    output operator, though only `>&` reads such a word as a descriptor or a close.  The others open a file of that name,
    so a member wrote outside its deliverables and Spud past Law 1 with nothing checked.

    Probed again in zsh 5.9 -f -o nobareglobqual (this Mac's Bash tool) and bash 3.2, in an empty scratch directory:
    `echo x > 3`, `>3`, `>> 3`, `>| 3`, `&> 3`, `1> 4`, `9> 9`, `2> 12` and `2>12` each created a file named by the
    digits in both shells, `&>> 3` in zsh (a syntax error in bash 3.2), and `> -`, `>> -` and `&> -` created a file named
    `-`.  Only `>& 3`, `>&3` and `1>&3` were a descriptor (both shells: "bad file descriptor") and `>& -`, `>&-` a close,
    while `2>&1` duplicated; `>& out` and `>&out` wrote the file `out` in both.  shlex reads `2>&1` as `2`, `>&`, `1` and
    `2>12` as `2`, `>`, `12`, so the leading descriptor never reaches the operand check.  AGENT_A plans tests/** and
    bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        for d in ("ledger/tickets", "docs", "tests"):
            (self.home.path / d).mkdir(parents=True, exist_ok=True)

    def test_a_digit_or_dash_operand_is_a_file_for_every_operator_but_the_dup(self):
        for operator in (">", ">>", ">|", "&>", "&>>"):
            for operand in ("3", "-", "12", "0"):
                for spacing in ("%s %s", "%s%s"):
                    command = "cd docs && echo x " + (spacing % (operator, operand))
                    with self.subTest(command):
                        self.assertRefused(command, "deliverables", AGENT_A)
                        self.assertSilent(command, AGENT_C)

    def test_a_leading_descriptor_does_not_make_the_operand_one(self):
        for command in ("cd docs && echo x 2> 12", "cd docs && echo x 2>12", "cd docs && echo x 1> 4",
                        "cd docs && echo x 9> 9", "cd docs && echo x 2>> 3"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables", AGENT_A)

    def test_the_dup_operator_keeps_its_descriptor_and_its_close(self):
        for ok in ("echo x >& 3", "echo x >&3", "echo x 1>&3", "echo x 2>&1", "echo x >&-", "echo x >& -",
                   "echo x >&2", "cc --version 2>&1", "exec 3>&1", "cd docs && echo x >& 3", "cd docs && echo x >&-"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_the_dup_operator_with_a_file_name_is_still_a_target(self):
        for command in ("cd docs && echo x >& out", "cd docs && echo x >&out", "cd docs && ls >& 3x"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables", AGENT_A)

    def test_a_generated_file_named_by_digits_is_refused_for_spud_too(self):
        for command in ("cd ledger/tickets && echo x > 3", "cd ledger/tickets && echo x >> -",
                        "cd ledger/tickets && echo x &> 12"):
            with self.subTest(command):
                self.assertRefused(command, "generated", AGENT_C)
                self.assertRefused(command, "Law 1", agent_id=None)

    def test_the_analysis_records_the_operand(self):
        m = load_spud_module()
        for command, targets in (("echo x > 3", ["3"]), ("echo x >> -", ["-"]), ("echo x 2> 12", ["12"]),
                                 ("echo x &> 3", ["3"]), ("echo x >| 3", ["3"]), ("echo x >&3", []),
                                 ("echo x 2>&1", []), ("echo x >&-", []), ("echo x >& out", ["out"])):
            with self.subTest(command):
                self.assertEqual([t for t, _cwds in m.analyse_command(command).redirects], targets)


class UnenterableDirTest(BashHookCase):
    """SPD-037 (proposed by SPUD-036/Huckleberry): SPD-030's directory model marked a cd uncertain only when the target
    was not a directory (os.path.isdir), so a directory that exists but cannot be entered -- no execute bit, /var/root,
    a directory a member chmod 000's -- passed, the hook assumed the cd succeeded, and a relative write after `;` or
    `||` landed in the original directory unchecked.

    Probed in zsh 5.9 -f -o nobareglobqual and bash 3.2 on a scratch directory with mode 000: `cd <dir>` is "permission
    denied" and PWD stays put in both, while `cd <dir> && pwd` runs nothing (exit 1).  os.path.isdir is True there and
    os.access(d, os.X_OK) is False, which is the difference the model was missing.  The `&&` form is unchanged: the write
    only runs where the cd succeeded.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests"):
            (home / d).mkdir(parents=True, exist_ok=True)
        self.locked = home / "locked"
        self.locked.mkdir(exist_ok=True)
        os.chmod(self.locked, 0o000)
        self.addCleanup(os.chmod, self.locked, 0o755)

    def test_a_cd_into_an_unenterable_directory_keeps_both_directories(self):
        for command in ("cd %s; echo x > ledger/tickets/SPD-001.md", "cd %s || echo x > ledger/tickets/SPD-001.md",
                        "cd %s\necho x > ledger/tickets/SPD-001.md", "cd %s; echo x | tee ledger/tickets/SPD-001.md",
                        "pushd %s; echo x > ledger/tickets/SPD-001.md"):
            with self.subTest(command % self.locked):
                self.assertRefused(command % self.locked, "generated", AGENT_C)
                self.assertRefused(command % self.locked, "Law 1", agent_id=None)

    def test_the_and_form_is_unchanged(self):
        # Only the directory the cd may have reached is checked, so `locked/ledger/tickets/SPD-001.md` is no generated
        # root and AGENT_C's `**` covers it.  Spud is refused there as he was before, the locked directory being inside
        # the repository and outside his own paths -- Law 1, not the cd model.
        for ok in ("cd %s && echo x > ledger/tickets/SPD-001.md", "cd %s && echo x > note.txt"):
            with self.subTest(ok % self.locked):
                self.assertSilent(ok % self.locked, AGENT_C)
                self.assertRefused(ok % self.locked, "Law 1", agent_id=None)

    def test_an_enterable_directory_is_unchanged(self):
        home = self.home.path
        self.assertSilent("cd %s/tests; echo x > keep.py" % home)
        self.assertSilent("cd %s/tests || echo x > keep.py" % home)
        self.assertRefused("cd %s/docs; echo x > x.md" % home, "deliverables", AGENT_A)

    def test_a_git_write_after_such_a_cd_is_checked_in_both_directories(self):
        self.assertRefused("cd %s; git archive -o ledger/tickets/SPD-001.md HEAD" % self.locked, "generated", AGENT_C)


if __name__ == "__main__":
    unittest.main()
