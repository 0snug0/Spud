"""PreToolUse(Bash): here-documents and standard input -- a body, its expansions, what a shell or a command reads on its
input, multios, and a document fed to a compound command or a function."""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from helpers import load_spud_module, wall_clock
from hookcase import AGENT_A, AGENT_C, SCRIPT_WORDING, BashHookCase


# SPD-188: what keeps a command going past the line its `<<` operator stands on, each standing in a word of the command
# (HereDocumentBodyTest has the probes); the body starts after the line on which the command's last word ends.
HEREDOC_SPANNING = (
    "'a\nb'",  # a single-quoted word
    '"a\nb"',  # a double-quoted word
    "a\\\nb",  # a backslash-newline
    "$(echo a\n)",  # a command substitution
    "`echo a\n`",  # backticks
    "${X:-a\nb}",  # a parameter expansion
    "<(cat\n)",  # a process substitution
    "$((1 +\n2))",  # an arithmetic expansion
    "(ledger|\nx)/tickets/SPD-001.md",  # zsh's glob group, which bash rejects
)


class HereDocumentBodyTest(BashHookCase):
    """SPD-188, filed by SPD-183's engineer: strip_heredocs took the lines right after the line holding a `<<` operator as its
    body, where zsh and bash start it after the newline that ends the operator's command.  A quoted word, a `$( )` or a zsh
    glob group spanning lines keeps the command going, so the hook read the rest of the command as body text and lost its
    targets.  The ticket's evidence, on the SPD-183 tree: `cat <<EOF | tee ledger/tickets/SPD-001.md 'a<newline>b' >
    /dev/null` was unparseable (allowed to every caller), the group lines `cat <<EOF > (ledger|<newline>x)/...` and `| tee
    (ledger|<newline>x)/...` recorded no target, and `cat <<EOF > $(echo ...<newline>)` read the substitution's first line
    alone.  The operator itself was read from the line's text, so a `<<` in quotes, a comment, an arithmetic shift or a `<<<`
    hid the lines after it (`echo '<<EOF'<newline>git push` was silent for a member), and a body inside a `$( )` was taken out
    of the outer text, where no substitution read it (`x=$(sh <<EOF<newline>git push<newline>EOF<newline>)` too).

    Probed 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, which printed the same for every line, and in GNU bash 3.2.57, with TMPPREFIX in the probe's directory so
    zsh could write a here-document's file:

    - the body starts after the newline that ends the operator's command: `cat <<EOF | tee l/t 'a<newline>b' > /dev/null`,
      the same with "a<newline>b" and with a backslash-newline before its `>`, `cat <<EOF > $(echo l/t<newline>)`, `cat
      <<EOF > \\`echo l/t<newline>\\``, `cat <<EOF | tee l/t <(cat<newline>) > /dev/null` and `cat <<EOF > ${X:-l/t<newline>}`
      each read the line after the command as the body (into l/t, or a file named l/t and a newline), in all three;
      zsh's `cat <<EOF > (l|<newline>x)/t`, `| tee (l|<newline>x)/t`, `> (l|<newline>x|<newline>y)/t` and `>
      (l|<newline>x)/(t|<newline>z)` wrote it into l/t, and so did one whose pattern held a quoted newline; bash rejects
      each of those lines;
    - an arithmetic command's newline (`cat <<EOF > l/t; (( n = 1 +<newline>2 ))`, n=3) and a for header's (`for (( i =
      0;<newline>i < 1; i++ ))`) end no command either, in all three, and zsh's case pattern group (`case a in
      (a|<newline>b)) echo RAN;; esac`) none in zsh: the body came after that line and the case ran its arm; a subshell's
      newline does (`(cat <<EOF > l/t<newline>body<newline>EOF<newline>)` wrote body), and so does an array assignment's
      (`cat <<EOF > l/t; arr=(a<newline>b)`: the body started there and the assignment went on past it, to a parse error);
    - the bodies of two here-documents follow in the order their operators stand: `cat <<A <<B` read bodyA then bodyB (zsh's
      multios catted both, bash bodyB alone), and `cat <<A - <(cat <<B<newline>bodyB<newline>B<newline>) > l/t<newline>bodyA
      <newline>A` wrote bodyA and bodyB, the body of the one in the `<( )` inside it;
    - a body inside a `$( )` is read inside it: `s=$(cat <<EOF<newline>in<newline>EOF<newline>)` held in, and so did the
      same in backticks and in double quotes; `s=$(cat <<EOF)` read no body, and the next line ran as a command (in8, EOF:
      command not found).  bash 3.2 ends the substitution at a body line's `)` (`a)b`, after which it ran the delimiter as a
      command), zsh does not;
    - `<<` is no operator in single or double quotes, escaped (`echo \\<<EOF` read a file named EOF), in a comment, in a
      parameter expansion, in `(( x = 1 <<y ))` (x=2, with its `))` on the next line too) or in `$(( 1 <<y ))`, and `<<<`
      opens a here-string, quoted or not: the line after each ran;
    - a here-document inside a `sh -c`, `zsh -f -c` or eval string is that string's (`sh -c 'cat <<EOF > l/t<newline>body
      <newline>EOF'` wrote body);
    - the delimiter line matches whole: after `<<`, `EOF ` with a trailing blank and a tab-indented EOF end nothing, and after
      `<<-` only leading tabs are stripped (`EOF<tab>` ends nothing).  In a body whose delimiter is unquoted a line ending in
      one backslash joins the next (`a\\` then EOF read aEOF, the body going on to the next EOF), one ending in two does not
      (the body was `a\\`), and a quoted delimiter's body keeps its backslash and ends at EOF; `E"O"F`, `'EOF'` and `\\EOF`
      are the delimiter EOF, `<< 'E F'` ends at a line `E F`, `<<$Z` at a line `$Z`, and a body with no delimiter runs to
      the end of the text;
    - `sh -s <<EOF 'a<newline>b'` ran the body after that line as its script.

    AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

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

    def data(self, line):
        """A body the line only reads as data: silent for every caller, and no push found."""
        with self.subTest(line=line):
            self.assertNotIn(("git", ("push", "push")), self.analysis(line).findings)
            for agent_id in (AGENT_A, AGENT_C, None):
                self.assertSilent(line, agent_id)

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_is_read_with_its_targets(self):
        tee = "cat <<EOF | tee ledger/tickets/SPD-001.md 'a\nb' > /dev/null\nbody11\nEOF"
        self.assertFalse(self.analysis(tee).unparseable)
        self.assertIn(self.TARGET, [self.m.deglob(t) for t, _c in self.analysis(tee).redirects])
        self.refused_everywhere(tee)
        for line in ("cat <<EOF > (ledger|\nx)/tickets/SPD-001.md\nbody2\nEOF",
                     "cat <<EOF | tee (ledger|\nx)/tickets/SPD-001.md\nbody3\nEOF"):
            with self.subTest(line=line):
                # zsh's reading first, as GroupNewlineTest reads the group without a here-document
                targets = [self.m.deglob(t) for t, _c in self.analysis(line).redirects]
                self.assertEqual(targets[0], "(ledger|\nx)/tickets/SPD-001.md")
            self.refused_everywhere(line)
        # the substitution's command, every line of it (a target the line spells only through it is Spud's refusal too)
        line = "cat <<EOF > $(echo ledger/tickets/SPD-001.md\ngit push\n)\nbody12\nEOF"
        self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
        self.assertRefused(line, "Law 7")
        self.assertRefused(line, "Law 7", AGENT_C)
        self.law_7("cat <<EOF $(echo ledger/tickets/SPD-001.md\ngit push\n) > /dev/null\nbody12\nEOF")
        self.refused_everywhere("cat <<EOF > $(echo a\ntee ledger/tickets/SPD-001.md < /dev/null\n)\nbody12\nEOF")
        self.assertEqual(self.m.strip_heredocs("cat <<EOF > $(echo ledger/tickets/SPD-001.md\n)\nbody12\nEOF"),
                         ("cat <<EOF > $(echo ledger/tickets/SPD-001.md\n)\n", ["body12"], [True]))

    # -- the hole -----------------------------------------------------------------------------------------------------
    def test_every_spanning_word_keeps_the_command_going(self):
        for spanning in HEREDOC_SPANNING:
            self.refused_everywhere("cat <<EOF %s | tee ledger/tickets/SPD-001.md > /dev/null\nbody\nEOF" % spanning)
            self.law_7("cat <<EOF %s; git push\nbody\nEOF" % spanning)
            self.law_7("sh -s <<EOF %s\ngit push\nEOF" % spanning)  # the body is still the shell's input
            self.data("cat <<EOF %s > /dev/null\ngit push\nEOF" % spanning)

    def test_an_arithmetic_command_or_header_keeps_it_going(self):
        for line in ("cat <<EOF; (( n = 1 +\n2 )); echo x > ledger/tickets/SPD-001.md\nbody\nEOF",
                     "cat <<EOF; for (( i = 0;\ni < 1; i++ )) do echo x > ledger/tickets/SPD-001.md; done\nbody\nEOF",
                     "cat <<EOF; case a in (a|\nb)) echo x > ledger/tickets/SPD-001.md;; esac\nbody\nEOF"):
            self.refused_everywhere(line)
        for line in ("cat <<EOF; (( n = 1 +\n2 )); git push\nbody\nEOF", "cat <<EOF; for (( i = 0;\ni < 1; i++ )) git push\nbody\nEOF",
                     "cat <<EOF; case a in (a|\nb)) git push;; esac\nbody\nEOF"):
            self.law_7(line)

    def test_a_newline_that_ends_a_command_starts_the_body(self):
        """A subshell's newline and an array assignment's end a command line: the body starts after them."""
        for line in ("(cat <<EOF > /dev/null\ngit push\nEOF\n)", "( cd docs && cat <<EOF > /dev/null\ngit push\nEOF\n)",
                     "cat <<EOF > /dev/null; arr=(a\ngit push\nEOF\n)", "{ cat <<EOF > /dev/null\ngit push\nEOF\n}"):
            self.data(line)
        self.law_7("(cat <<EOF > /dev/null\nbody\nEOF\ngit push)")

    def test_what_is_no_operator_hides_no_line(self):
        for line in ("echo '<<EOF'\ngit push", 'echo "<<EOF"\ngit push', "echo \\<<EOF\ngit push", "true # <<EOF\ngit push",
                     "# <<EOF\ngit push", "echo ${X:-<<EOF}\ngit push", "y=1; (( x = 1 <<y ))\ngit push",
                     "y=1; (( x = 1 <<y\n))\ngit push", "echo $(( 1 <<y ))\ngit push", "cat <<<EOF\ngit push",
                     "cat <<< EOF\ngit push", "cat <<<'EOF'\ngit push"):
            self.law_7(line)
        self.refused_everywhere("echo '<<EOF'\necho x > ledger/tickets/SPD-001.md")

    def test_a_here_document_in_a_string_a_shell_runs(self):
        for line in ("bash -c 'sh <<EOF\ngit push\nEOF'", "zsh -f -c 'sh <<EOF\ngit push\nEOF'", "eval 'sh <<EOF\ngit push\nEOF'",
                     "sh -c \"sh <<EOF\ngit push\nEOF\""):
            self.law_7(line)
        self.data("bash -c 'cat <<EOF > /dev/null\ngit push\nEOF'")

    def test_a_body_inside_a_substitution_is_read_there(self):
        for line in ("x=$(sh <<EOF\ngit push\nEOF\n)", "echo $(sh <<EOF\ngit push\nEOF\n)", 'x="$(sh <<EOF\ngit push\nEOF\n)"',
                     "echo `sh <<EOF\ngit push\nEOF\n`", "cat <<A > /dev/null $(sh <<B\ngit push\nB\n)\nbodyA\nA",
                     "x=$(cat <<EOF)\ngit push\nEOF"):  # the substitution closed on the operator's line: no body, a command
            self.law_7(line)
        for line in ("x=$(cat <<EOF\ngit push\nEOF\n)", "x=$(cat <<'EOF'\nit's git push\nEOF\n)",
                     'x="$(cat <<\'EOF\'\nit\'s git push\nEOF\n)"', "echo `cat <<EOF\ngit push\nEOF\n`",
                     "cat <<A > /dev/null $(cat <<B\ngit push\nB\n)\ngit push\nA"):
            self.data(line)

    def test_bodies_follow_in_operator_order(self):
        self.assertEqual(self.m.strip_heredocs("cat <<A <<-B\nbodyA\nA\n\tbodyB\n\tB\nnext"),
                         ("cat <<A <<-B\nnext", ["bodyA", "\tbodyB"], [True, True]))
        self.assertEqual(self.m.strip_heredocs("cat <<A - <(cat <<B\nbodyB\nB\n) > f\nbodyA\nA"),
                         ("cat <<A - <(cat <<B\n) > f\n", ["bodyA", "bodyB"], [True, True]))
        self.assertEqual(self.m.strip_heredocs("cat <<'A' \"a\nb\" <<B\nbodyA\nA\nbodyB\nB"),
                         ("cat <<'A' \"a\nb\" <<B\n", ["bodyA", "bodyB"], [False, True]))
        self.law_7("cat <<A <<B | sh\necho a\nA\ngit push\nB")

    def test_the_delimiter_line(self):
        for line in ("cat <<EOF > /dev/null\n\tEOF\ngit push\nEOF", "cat <<EOF > /dev/null\nEOF \ngit push\nEOF",
                     "cat <<-EOF > /dev/null\n\tEOF\t\ngit push\nEOF", "cat <<EOF > /dev/null\na\\\nEOF\ngit push\nEOF",
                     "cat <<E\"O\"F > /dev/null\ngit push\nEOF", "cat <<$Z > /dev/null\ngit push\n$Z",
                     "cat <<EOF > /dev/null\ngit push"):
            self.data(line)
        for line in ("cat <<E\"O\"F > /dev/null\nx\nEOF\ngit push", "cat <<'EOF' > /dev/null\na\\\nEOF\ngit push",
                     "cat <<EOF > /dev/null\na\\\\\nEOF\ngit push", "cat <<\\EOF > /dev/null\nx\nEOF\ngit push",
                     "cat <<-EOF > /dev/null\n\t\tx\n\t\tEOF\ngit push", "cat <<$Z > /dev/null\nx\n$Z\ngit push",
                     "cat << 'E F' > /dev/null\nx\nE F\ngit push"):
            self.law_7(line)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        for line in ("cat <<EOF " + "(a|\n" * 3000 + ")" * 3000 + "\ngit push\nEOF",
                     "cat <<EOF " + "(" * 3000 + "\n" * 3000 + "git push",
                     "cat " + "<<EOF (\n" * 2000 + "git push",
                     "(" * 2000 + "cat <<EOF\n" * 2000 + "EOF\n" * 2000 + ")" * 2000 + "\ngit push",
                     "( " * 3000 + "cat <<EOF\nx\nEOF\n" * 3000 + ")" * 3000 + "\ngit push",
                     "x=(a " * 1000 + "cat <<EOF\nx\nEOF\n" * 1000 + "\ngit push",
                     "(( " + "x <<y\n" * 2000 + "))\ngit push",
                     "cat " + "$(" * 1500 + "<<EOF\n" + ")" * 1500 + "\nbody\nEOF\ngit push",
                     "cat " + "<(" * 1500 + "<<EOF\n" + ")" * 1500 + "\nbody\nEOF\ngit push"):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                self.analysis(line)
                self.assertLess(time.monotonic() - started, 5.0)


# SPD-192: the places an unquoted here-document's body is fed to a command, `%s` standing for one line of the body.  zsh
# and bash expand the body wherever it is fed, before the command reads it: with `$(echo RAN > <file>)` for %s each made
# its file (HereDocumentExpansionTest has the probes).
HEREDOC_EXPANDED_FEEDS = (
    "cat <<EOF > /dev/null\n%s\nEOF",
    "cat <<-EOF > /dev/null\n\t%s\n\tEOF",
    "cat <<$Z > /dev/null\n%s\n$Z",  # an unquoted `$` in the delimiter is no quoting: the delimiter is the line `$Z`
    ": <<EOF\n%s\nEOF",
    "true <<EOF\n%s\nEOF",
    "nosuchcmd <<EOF\n%s\nEOF",
    "<<EOF\n%s\nEOF",
    "exec 3<<EOF\n%s\nEOF",
    "cat <<EOF | wc -l > /dev/null\n%s\nEOF",
    "cat <(cat <<EOF\n%s\nEOF\n) > /dev/null",
    "x=$(cat <<EOF\n%s\nEOF\n)",
    "{ cat <<EOF > /dev/null\n%s\nEOF\n}",
    "if true; then cat <<EOF > /dev/null\n%s\nEOF\nfi",
    "sh -c 'cat <<EOF > /dev/null\n%s\nEOF'",
    "eval 'cat <<EOF > /dev/null\n%s\nEOF'",
    "cat <<A > /dev/null\n$(cat <<B\n%s\nB\n)\nA",  # a body in a substitution of a body
)
# the spellings of one body line whose substitution runs, `%s` standing for its command: each made its file
HEREDOC_EXPANDED_SPELLINGS = (
    "$(%s)",
    "`%s`",
    "'$(%s)'",  # quotes are text in a body
    '"$(%s)"',
    "\\\\$(%s)",  # an escaped backslash, then the substitution
    "${u:-$(%s)}",  # a default word
    "$((1 + $(%s)))",  # arithmetic
    "$[1 + $(%s)]",
    "a\\\n$(%s)",  # a backslash-newline joins the lines first
    "$(%s\n)",  # a substitution spanning the body's lines
    "$(%s)\\\nEOF",  # a line joined to the next is no delimiter: the body goes on to the next EOF
)
# a delimiter any character of which is quoted: the body is text, never expanded, and ran nothing
HEREDOC_QUOTED_OPERATORS = ("<<'EOF'", '<<"EOF"', "<<\\EOF", '<<E"O"F', "<<E\\OF", "<<$'EOF'", "<<-'EOF'")


class HereDocumentExpansionTest(BashHookCase):
    """SPD-192, filed by SPD-188's engineer: zsh and bash expand an unquoted here-document's body before its command reads
    it, running every `$( )` and backtick substitution in it, and the hook read a body only where a shell is fed it, as
    that shell's commands.  The ticket's evidence, on the SPD-188 tree and on main before it: `cat <<EOF<newline>$(git
    push)<newline>EOF` and the same in backticks recorded no finding, so a member was allowed the push.

    Probed 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, which printed the same for every line, and in GNU bash 3.2.57, with TMPPREFIX in the probe's directory,
    each substitution an `echo RAN > <file>`:

    - an unquoted body's substitution ran wherever the body was fed (HEREDOC_EXPANDED_FEEDS: cat, `<<-` with its tabs,
      `<<$Z`, `:`, true, a command that does not exist, a bare redirection, `exec 3<<`, a pipeline element, a `<( )`, a
      `$( )`, a group, an if, `sh -c` and eval strings, a body in a body's own substitution), in every spelling of
      HEREDOC_EXPANDED_SPELLINGS: quotes are text there, `\\\\` is one backslash, a default word's and an arithmetic
      expansion's substitutions run, and a backslash-newline joins two lines first;
    - zsh's `${(e)x}` in a body ran x's substitution (bash: bad substitution);
    - a body whose delimiter has any character quoted (HEREDOC_QUOTED_OPERATORS) ran nothing, and neither did `\\$( )` or
      an escaped backtick in an unquoted one, a `\\$( )` in an unquoted body inside a body's `$( )`, nor a `$( )` in a quoted
      one there;
    - the body is expanded when its command runs, in its directory and with the values the line holds then: `cd d; cat
      <<EOF` and `cd d && cat <<EOF` wrote into d, `cat <<EOF > /dev/null; cd d` where the line stood before the cd, `x=a;
      cat <<EOF > /dev/null; x=b` into a, `false && cat <<EOF` ran nothing, and a function's body ran it once called; a
      command's prefix assignment does not reach its body (`x=a; x=b cat <<EOF` and `x=a; x=b : <<EOF` wrote into a);
    - `${u:=v}` in a body fed to cat left u unset, fed to `:` set it: the hook doubts u either way, as on the line;
    - a here-string's word is expanded as any word is (`cat <<< "$(...)"` and `cat <<< $(...)` ran), which the hook read
      already.

    AGENT_A plans tests/** and bin/spud; AGENT_C plans home:**."""

    TARGET = "ledger/tickets/SPD-001.md"

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for rel in (self.TARGET, "docs/x.md"):
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

    def data(self, line):
        """A body the line only reads as text: silent for every caller, and no push found."""
        with self.subTest(line=line):
            self.assertNotIn(("git", ("push", "push")), self.analysis(line).findings)
            for agent_id in (AGENT_A, AGENT_C, None):
                self.assertSilent(line, agent_id)

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_is_read_as_the_shells_run_it(self):
        self.law_7("cat <<EOF\n$(git push)\nEOF")
        self.law_7("cat <<EOF\n`git push`\nEOF")

    def test_a_write_in_a_body_substitution_is_checked(self):
        for spelling in ("$(%s)", "`%s`"):
            self.refused_everywhere("cat <<EOF > /dev/null\n%s\nEOF" % (spelling % "echo x > ledger/tickets/SPD-001.md"))
            line = "cat <<EOF > /dev/null\n%s\nEOF" % (spelling % "echo x > docs/x.md")
            with self.subTest(line=line):
                self.assertRefused(line, "deliverables")
                self.assertSilent(line, AGENT_C)

    # -- the hole -----------------------------------------------------------------------------------------------------
    def test_every_place_a_body_is_fed(self):
        for feed in HEREDOC_EXPANDED_FEEDS:
            self.law_7(feed % "$(git push)")
            self.law_7(feed % "`git push`")
            self.refused_everywhere(feed % "$(echo x > ledger/tickets/SPD-001.md)")

    def test_every_spelling_that_runs_a_substitution(self):
        for spelling in HEREDOC_EXPANDED_SPELLINGS:
            self.law_7("cat <<EOF > /dev/null\n%s\nEOF" % (spelling % "git push"))
        self.law_7("x='$(git push)'; cat <<EOF > /dev/null\n${(e)x}\nEOF")  # zsh's (e) evaluates x's value there too

    def test_a_quoted_delimiter_keeps_the_body_text(self):
        body = "$(git push)\n`git push`\n${u:-$(git push)}\n$((1 + $(git push)))\n${(e)x}\n$(echo x > ledger/tickets/SPD-001.md)"
        for operator in HEREDOC_QUOTED_OPERATORS:
            self.data("x='$(git push)'; cat %s > /dev/null\n%s\nEOF" % (operator, body))
            self.assertEqual(self.m.strip_heredocs("cat %s\nbody\nEOF" % operator)[2], [False])
        for operator in ("<<EOF", "<<-EOF", "<< EOF"):
            self.assertEqual(self.m.strip_heredocs("cat %s\nbody\nEOF" % operator)[2], [True])
        self.assertEqual(self.m.strip_heredocs("cat <<'A' <<B <<\\C\nbodyA\nA\nbodyB\nB\nbodyC\nC"),
                         ("cat <<'A' <<B <<\\C\n", ["bodyA", "bodyB", "bodyC"], [False, True, False]))

    def test_an_escaped_substitution_runs_nothing(self):
        for line in ("cat <<EOF > /dev/null\n\\$(git push)\n\\`git push\\`\nEOF",
                     "cat <<A > /dev/null\n$(cat <<B\n\\$(git push)\nB\n)\nA",
                     "cat <<A > /dev/null\n$(cat <<'B'\n$(git push)\nB\n)\nA"):
            self.data(line)

    def test_the_body_is_expanded_where_and_when_its_command_runs(self):
        # the command's directory: a cd before it on the line, not one after its operator
        self.refused_everywhere("cd ledger && cat <<EOF > /dev/null\n$(echo x > tickets/SPD-001.md)\nEOF")
        self.refused_everywhere("cd ledger; cat <<EOF > /dev/null; cd ..\n$(echo x > tickets/SPD-001.md)\nEOF")
        line = "cat <<EOF > /dev/null; cd ledger\n$(echo x > tickets/SPD-001.md)\nEOF"
        with self.subTest(line=line):
            self.assertSilent(line, AGENT_C)
            self.assertRefused(line, "deliverables")
        # the values the line holds then; a command's prefix assignment does not reach its body
        for line in ("x=docs/x.md; cat <<EOF > /dev/null; x=%s\n$(echo y > $x)\nEOF",
                     "x=docs/x.md; x=%s cat <<EOF > /dev/null\n$(echo y > $x)\nEOF",
                     "x=docs/x.md; x=%s : <<EOF\n$(echo y > $x)\nEOF"):
            with self.subTest(line=line):
                self.assertSilent(line % self.TARGET, AGENT_C)
                self.assertRefused(line.replace("docs/x.md", self.TARGET) % "docs/x.md", "generated", AGENT_C)

    def test_what_stays_as_it_was(self):
        """A body whose substitutions write nothing and run no verb is allowed to every caller; a shell fed an unquoted body
        still reads it as its commands, and a body with no substitution is text."""
        line = "cat <<EOF > /dev/null\nDate: $(date)\nUser: `whoami`\nHome: ${HOME:-x}\nEOF"
        for agent_id in (AGENT_A, AGENT_C, None):
            with self.subTest(line=line, agent_id=agent_id):
                self.assertSilent(line, agent_id)
        self.law_7("sh <<EOF\n$(git push)\nEOF")
        self.law_7("sh <<EOF\ngit push\nEOF")
        self.data("cat <<EOF > /dev/null\ngit push\nEOF")

    @wall_clock
    def test_bounded_on_pathological_input(self):
        for line in ("cat <<EOF\n" + "$(" * 3000 + ")" * 3000 + "\nEOF\ngit push",
                     "cat <<EOF\n" + "`x`" * 3000 + "\nEOF\ngit push",
                     "cat <<EOF\n" + "${(e)x}" * 3000 + "\nEOF\ngit push",
                     "cat <<EOF\n" + "a" * 200000 + "$\nEOF\ngit push",
                     "cat " + "<<EOF " * 500 + "\n" + "$(true)\nEOF\n" * 500 + "git push"):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                findings = self.analysis(line).findings
                self.assertLess(time.monotonic() - started, 5.0)
                self.assertIn(("git", ("push", "push")), findings)


class HereDocumentInputTest(BashHookCase):
    """SPD-206, filed by SPD-192's engineer: the shell that expands an unquoted here-document's body hands its command the
    text it expanded, and the hook handed a shell fed such a body the body as spelled, so its reading of that shell saw
    other commands than the shell runs.  The ticket's evidence, on the SPD-192 tree: `sh <<EOF` fed each of the first
    four lines below, a push in place of the echo, recorded no finding, and the joined line aimed at the ledger recorded
    the target `ledger/tickets/SPD-00` and a backslash.

    Probed 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f and in GNU bash 3.2.57, which printed the same, with TMPPREFIX in the probe's directory:

    - each of the three outer shells feeding /bin/sh an unquoted body ran `\\$(echo RAN > l/h1)`, `echo \\$(echo RAN >
      l/h2)`, `\\`echo RAN > l/h3\\``, `echo hi > l/h\\\\` then a line `5` (sh joined them: h5), and `ec\\\\` then a line
      `ho RAN > l/h6`: all five files were written; the first line ran too fed through `cat <<EOF | sh`, to `bash` and
      to `zsh -f`, and to sh after `<<-` with its tabs;
    - with the delimiter quoted, sh stopped at the first line with a syntax error and wrote nothing;
    - `cat` fed `[\\a] [\\"] [\\'] [\\$] [\\`] [\\\\] [\\x] [a\\<newline>b] [\\\\\\\\]` printed `[\\a] [\\"] [\\'] [$] [`]
      [\\] [\\x] [ab] [\\\\]`: a backslash escapes a `$`, a backtick, a backslash and a newline, and stays before anything
      else; a default word's text is read the same (`${u:-\\$(echo X)}` printed `$(echo X)`), while a `$( )`'s and
      backticks' text is the substitution's own (`$(echo '\\$x')` printed `\\$x`)."""

    TARGET = "ledger/tickets/SPD-001.md"
    JOINED = "echo hi > ledger/tickets/SPD-00\\\\\n1.md"  # an escaped backslash, then a newline

    def setUp(self):
        super().setUp()
        p = self.home.path / self.TARGET
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("orig\n", encoding="utf-8")
        self.m = load_spud_module()

    def analysis(self, command):
        return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path)))

    def law_7(self, line):
        """A member is refused the push, and the analysis finds it; Spud is never refused git."""
        with self.subTest(line=line):
            self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
            self.assertRefused(line, "Law 7")
            self.assertSilent(line, agent_id=None)

    def test_the_tickets_evidence_is_read_as_the_shell_receives_it(self):
        for body in ("\\$(git push)", "echo \\$(git push)", "\\`git push\\`", "git pu\\\\\nsh"):
            self.law_7("sh <<EOF\n%s\nEOF" % body)

    def test_the_joined_line_names_the_ledger_file_whole(self):
        line = "sh <<EOF\n%s\nEOF" % self.JOINED
        self.assertEqual([t for t, _cwds in self.analysis(line).redirects], [self.TARGET])
        self.assertRefused(line, "Law 1", agent_id=None)
        self.assertRefused(line, "generated")

    def test_every_reader_of_the_body_is_handed_the_same_text(self):
        # a shell reading it on standard input through a printer (shell/stdin_text), each shell, and `<<-` with its tabs
        for line in ("cat <<EOF | sh\n\\$(git push)\nEOF", "bash <<EOF\n\\$(git push)\nEOF",
                     "zsh -f <<EOF\n\\$(git push)\nEOF", "sh <<-EOF\n\t\\$(git push)\n\tEOF"):
            self.law_7(line)

    def test_a_quoted_delimiter_hands_the_body_over_as_spelled(self):
        for operator in HEREDOC_QUOTED_OPERATORS:
            line = "sh %s\n%s\nEOF" % (operator, self.JOINED)
            with self.subTest(line=line):
                self.assertEqual([t for t, _cwds in self.analysis(line).redirects], ["ledger/tickets/SPD-00\\"])
            line = "sh %s\n\\$(git push)\necho \\$(git push)\n\\`git push\\`\nEOF" % operator
            with self.subTest(line=line):
                self.assertNotIn(("git", ("push", "push")), self.analysis(line).findings)

    def test_the_text_a_command_receives(self):
        received = self.m.received_body
        self.assertEqual(received("[\\a] [\\\"] [\\'] [\\$] [\\`] [\\\\] [\\x] [a\\\nb] [\\\\\\\\]"),
                         "[\\a] [\\\"] [\\'] [$] [`] [\\] [\\x] [ab] [\\\\]")
        self.assertEqual(received("${u:-\\$(echo X)}"), "${u:-$(echo X)}")
        for kept in ("$(echo '\\$x')", "`echo '\\\\$y'`", "$(echo a\\\nb)", "no escape", "trailing\\"):
            with self.subTest(kept=kept):
                self.assertEqual(received(kept), kept)


class HereDocumentOutputTest(BashHookCase):
    """SPD-207, filed by SPD-206's engineer: a shell fed an unquoted here-document reads the body as its expansion leaves
    it, and each command substitution there leaves its output in the text, which the shell then runs as commands: a
    newline in the output starts another.  The hook reads the substitution where the outer shell runs it (SPD-192) and
    hands the inner shell the body with the substitution as spelled (SPD-206), so the output was never read, and a member
    could write a script into its scratchpad and run it through such a body -- what SPD-145 refuses as `sh x.sh` and as
    `cat x.sh | sh`.  The ticket's evidence, on the SPD-206 tree: analyse_command recorded no finding for `sh <<EOF`
    fed `echo a $(cat x.sh)` (LINE), nor for the same body with `git push` in a printf's output, while `echo "echo a
    $(cat x.sh)" | sh` recorded a script "stdin" finding.

    The rule: the text a command reads from such a body is text the line does not spell (shell/stdin_text), so a shell
    that runs its standard input as commands is refused a member with SPD-145's reason for standard input the line does
    not spell, whether the body is fed to it or printed into it through a pipe (shell/script_files); Spud keeps it.
    A quoted delimiter, an unquoted body with no substitution, an escaped `\\$( )`, which the inner shell runs itself and
    whose output is one of its words (SPD-206), and the same body fed to a command that only prints it read as before.

    Probed 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f and in GNU bash 3.2.57, which printed the same but where noted, with TMPPREFIX in the probe's directory:

    - `sh` fed an unquoted body `echo a $(printf 'b\\necho RAN > l/p1')` printed `a b` and wrote l/p1, and so did the
      same in backticks, in a default word (`${u:-$( )}`), after `<<-` with its tabs, fed to `sh -s`, `bash` and `zsh
      -f`, and printed into `sh` and `bash` by `cat <<EOF |`; `echo a $(cat x.sh)`, x.sh holding `echo RAN > l/x1`,
      wrote l/x1; zsh's `${(e)x}`, x holding such a substitution, wrote its file too (bash: bad substitution);
    - `cat` fed the same body printed its two lines and wrote nothing; with the delimiter quoted (`<<'EOF'`, `<<"EOF"`,
      `<<\\EOF`, `<<E"O"F`, `<<-'EOF'`) sh printed `a b echo RAN > l/q1` and wrote nothing, and so did `\\$(printf
      ...)` in an unquoted body; a body holding `$((1 + 2))` and `${u:-b}` printed `a 3 b`."""

    LINE = "sh <<EOF\necho a $(cat x.sh)\nEOF"  # the proposer's line
    BODY = "echo a $(cat x.sh)"

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def forms(self, command):
        """The forms of the script findings this line records."""
        return [detail[0] for kind, detail in self.analysis(command).findings if kind == "script"]

    def refused(self, line):
        """Refused a member with SPD-145's reason for standard input the line does not spell; silent for Spud."""
        with self.subTest(line=line):
            r = self.assertRefused(line, SCRIPT_WORDING)
            self.assertIn("standard input that the line does not spell", r.reason)
            self.assertSilent(line, agent_id=None)

    def test_the_tickets_evidence_is_refused_a_member(self):
        self.refused(self.LINE)
        self.refused("sh <<EOF\necho a $(printf 'b\\ngit push')\nEOF")
        self.assertEqual(self.forms(self.LINE), ["stdin"])

    def test_the_same_body_printed_into_a_shell(self):
        for line in ("cat <<EOF | sh\n%s\nEOF", "cat <<EOF | bash\n%s\nEOF", "cat - <<EOF | sh -s\n%s\nEOF"):
            self.refused(line % self.BODY)
            self.assertEqual(self.forms(line % self.BODY), ["stdin"])

    def test_every_shell_and_spelling(self):
        for spelling in HEREDOC_EXPANDED_SPELLINGS:
            self.refused("sh <<EOF\necho a %s\nEOF" % (spelling % "cat x.sh"))
        for line in ("bash <<EOF\n%s\nEOF", "zsh -f <<EOF\n%s\nEOF", "sh -s <<EOF\n%s\nEOF", "env sh <<EOF\n%s\nEOF",
                     "sh <<-EOF\n\t%s\n\tEOF", "sh - <<EOF\n%s\nEOF", "nice bash <<EOF\n%s\nEOF"):
            self.refused(line % self.BODY)
        self.refused("x='$(cat x.sh)'; sh <<EOF\necho a ${(e)x}\nEOF")  # zsh's (e) runs x's substitution in the body

    def test_the_controls_read_as_before(self):
        """The same body printed and nothing more, a quoted delimiter, an escaped substitution, and an unquoted body with
        no substitution: no script finding, and silent for every caller."""
        lines = ["cat <<EOF\n%s\nEOF" % self.BODY, "cat <<EOF > /dev/null\n%s\nEOF" % self.BODY,
                 "cat <<'EOF' | sh\n%s\nEOF" % self.BODY, "sh <<EOF\necho a \\$(cat x.sh)\nEOF",
                 "sh <<EOF\necho a $((1 + 2)) ${u:-b}\nEOF", "sh <<EOF\necho a\nEOF"]
        lines += ["sh %s\n%s\nEOF" % (operator, self.BODY) for operator in HEREDOC_QUOTED_OPERATORS]
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(self.forms(line), [])
                self.assertSilent(line)
                self.assertSilent(line, agent_id=None)

    def test_the_reason_names_the_readable_form_and_an_earlier_reason_is_kept(self):
        r = self.assertRefused(self.LINE, SCRIPT_WORDING)
        for needle in ("Law 7: `sh` runs commands it reads on standard input that the line does not spell",
                       "a command substitution's output in an unquoted here-document", "`sh <<'EOF'`"):
            self.assertIn(needle, r.reason)
        # read last, as SPD-145's refusals are: a git verb or a write the path rule refuses keeps its own reason
        self.assertNotIn(SCRIPT_WORDING, self.assertRefused("sh <<EOF\ngit push\n%s\nEOF" % self.BODY, "Law 7").reason)
        line = "sh <<EOF\necho x > docs/y.md\n%s\nEOF" % self.BODY
        self.assertNotIn(SCRIPT_WORDING, self.assertRefused(line, "deliverables").reason)


class HereDocumentValueTest(BashHookCase):
    """SPD-208, filed by SPD-207's engineer: the shell that expands an unquoted here-document's body puts each parameter
    expansion's value in the text, and a shell fed that text parses it again, so a separator, a redirection, a newline
    or a substitution the value holds is a command the inner shell runs.  The hook handed that shell the body with `$x`
    as spelled, which it read as one word.  The ticket's evidence, on the SPD-207 tree: analyse_command recorded no
    finding for LINE, nor for the same line with a ledger file as the redirection target, nor for `Y=$(printf ...)`
    and a body `echo a $Y`.

    The rule (reevaluation.body_values, heredocs.received_body): a `$NAME` or `${NAME}` whose value the line settles --
    arg_writes.resolved's reading -- is handed on as that value's text, and so is zsh's `${(e)NAME}` of a value holding
    no expansion.  Any other parameter expansion is kept as spelled, as before, where every text it may leave is plain:
    an environment variable the line never touches, a special parameter's number, a settled value or a spelled word
    holding no shell syntax.  Otherwise the body holds text the line does not spell (heredocs.OutputBody), and a shell
    reading it is refused a member with SPD-145's reason for standard input the line does not spell, as SPD-207 refuses
    one fed a substitution's output; Spud reads on.

    Probed 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, which printed the same, and in GNU bash 3.2.57, with TMPPREFIX in the probe's directory, each file an
    `echo RAN > l/<name>`:

    - sh fed an unquoted body `echo $x`, x holding `a; echo RAN > l/v1`, printed a and wrote l/v1, and so did `${x}`, a
      value holding a newline (`x=$'a\\necho RAN > l/v4'`) and one holding a substitution (`x='$(echo RAN > l/v5)'`,
      which the inner shell ran, where `cat` fed the same body printed `$(echo RAN > l/v5)`); `Y=$(printf 'b\\necho RAN >
      l/m4')` and a body `echo a $Y` wrote l/m4; zsh's `${(e)y}` wrote its file (bash: bad substitution); `echo
      ${HOME:+a;echo RAN > l/v8}` wrote l/v8; `$1` in a function's body wrote its file once the function was called with
      such a word, `$_` after `: 'a; echo RAN > l/v9'` wrote l/v9 in bash (zsh printed sh), and `for i in 1 2` fed sh
      `echo pass $i $v` and then assigned v such a value, which its second pass wrote;
    - with the delimiter quoted sh printed its words and wrote nothing; `echo "$x" '$x'` in an unquoted body printed the
      value twice and wrote nothing, its quotes being the inner shell's; `echo d ${u:-b} $((1 + 2))` printed `d b 3`, and
      `x=b` then `echo e $x` printed `e b`;
    - `x='a; echo RAN > l/v13'; x=q sh <<EOF` with `echo pre $x` printed `pre a` and wrote l/v13 in zsh, and printed
      `pre q` in bash, which expands a body with the command's own prefix assignments: `x=l/a3; x=l/b3 cat <<EOF` with
      `[$x]` printed `[l/b3]` there and `[l/a3]` in zsh, and `u='c; echo RAN > l/w1' sh <<EOF` with `echo sh [$u]`
      wrote `l/w1]` in bash and nothing in zsh."""

    LINE = "x='a; git push'; sh <<EOF\necho $x\nEOF"  # the proposer's line
    TARGET = "ledger/tickets/SPD-001.md"

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        p = self.home.path / self.TARGET
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("orig\n", encoding="utf-8")
        self.m = load_spud_module()

    def analysis(self, command):
        return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path)))

    def law_7(self, line):
        """A member is refused the push, and the analysis finds it; Spud is never refused git."""
        with self.subTest(line=line):
            self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
            self.assertRefused(line, "Law 7")
            self.assertSilent(line, agent_id=None)

    def unsettled(self, line):
        """A "stdin" script finding, refused a member with SPD-145's reason for standard input the line does not spell;
        silent for Spud."""
        with self.subTest(line=line):
            self.assertIn("stdin", [detail[0] for kind, detail in self.analysis(line).findings if kind == "script"])
            r = self.assertRefused(line, SCRIPT_WORDING)
            self.assertIn("standard input that the line does not spell", r.reason)
            self.assertSilent(line, agent_id=None)

    def data(self, line):
        """Read as before: no push, no script finding, and silent for every caller."""
        with self.subTest(line=line):
            findings = self.analysis(line).findings
            self.assertNotIn(("git", ("push", "push")), findings)
            self.assertEqual([detail for kind, detail in findings if kind == "script"], [])
            for agent_id in (AGENT_A, AGENT_C, None):
                self.assertSilent(line, agent_id)

    def test_the_tickets_evidence_is_refused(self):
        self.law_7(self.LINE)
        line = "x='a; echo x > %s'; sh <<EOF\necho $x\nEOF" % self.TARGET
        self.assertEqual([t for t, _cwds in self.analysis(line).redirects], [self.TARGET])
        for agent_id in (AGENT_A, AGENT_C):
            self.assertRefused(line, "generated", agent_id)
        self.assertRefused(line, "Law 1", agent_id=None)
        self.unsettled("Y=$(printf 'b\\ngit push'); sh <<EOF\necho a $Y\nEOF")
        self.law_7("y='a; git push'; sh <<EOF\necho ${(e)y}\nEOF")  # zsh's (e) of a value holding no expansion

    def test_a_settled_value_is_read_as_the_inner_shell_parses_it(self):
        for line in ("x='a; git push'; sh <<EOF\necho ${x}\nEOF",
                     "x=$'a\\ngit push'; sh <<EOF\necho $x\nEOF",  # a newline in the value starts another command
                     "x='$(git push)'; sh <<EOF\necho $x\nEOF",  # the inner shell runs the substitution the value spells
                     "x='a; git push'; cat <<EOF | sh\necho $x\nEOF",
                     "x='a; git push'; bash <<EOF\necho $x\nEOF",
                     "x='a; git push'; zsh -f <<EOF\necho $x\nEOF",
                     "x='a; git push'; sh <<-EOF\n\techo $x\n\tEOF",
                     "x='a; git push'; x=b sh <<EOF\necho $x\nEOF",  # zsh's reading: the line's value
                     "x=b; x='a; git push' sh <<EOF\necho $x\nEOF",  # bash's: the command's own prefix assignment
                     "x=b; y='a; git push'; x=q sh <<EOF\necho $x $y\nEOF"):
            self.law_7(line)
        # the inner shell's own quotes keep the value one word, and a body only printed runs nothing
        self.data("x='a; git push'; sh <<EOF\necho \"$x\" '$x'\nEOF")
        self.data("x='$(git push)'; cat <<EOF\necho $x\nEOF")

    def test_a_value_the_line_does_not_settle_is_refused_a_member(self):
        for line in ("Y=$(cat x.sh); sh <<EOF\necho a $Y\nEOF",  # a substitution's output
                     "read -r y < f; sh <<EOF\necho a $y\nEOF",  # text the line does not spell
                     ": 'a; echo x'; sh <<EOF\necho $_\nEOF",  # bash's last word of the command before
                     "sh <<EOF\necho $1 $@\nEOF",  # the positional parameters, which a function's call sets
                     "x='a; echo x'; x='b; echo y' sh <<EOF\necho $x\nEOF",  # zsh's value and bash's, both commands
                     "x='a; echo x'; sh <<EOF\necho ${x:-b}\nEOF",  # a value behind an operator
                     "sh <<EOF\necho ${HOME:+a;echo x}\nEOF",  # a spelled word holding a separator
                     # a loop may assign a variable after its body reads it, for the next pass
                     "for i in 1 2; do sh <<EOF\necho $v\nEOF\nv='a; echo x'; done"):
            self.unsettled(line)

    def test_the_controls_read_as_before(self):
        """A quoted delimiter, values holding no shell syntax, the environment's variables and the special parameters'
        numbers, an escaped `\\$x`, and a body a command only prints."""
        for operator in HEREDOC_QUOTED_OPERATORS:
            self.data("x='a; git push'; sh %s\necho $x ${x} ${x:-b}\nEOF" % operator)
        for line in ("x=hello; sh <<EOF\necho $x ${x} ${x:-b} ${#x} ${x%l*}\nEOF",
                     "x=hello; x=world sh <<EOF\necho $x\nEOF",
                     "sh <<EOF\necho $HOME ${HOME} ${u:-b} $((1 + 2)) $? $$ $# ${#}\nEOF",
                     "x='a; git push'; sh <<EOF\necho \\$x\nEOF",
                     "x='a; git push'; cat <<EOF\necho $x ${x:-b}\nEOF",
                     "x='a; git push'; cat <<EOF > /dev/null\necho $x\nEOF"):
            self.data(line)

    def test_the_text_a_shell_receives(self):
        values = {"$x": ("a; b", True), "${y}": (None, False)}.get
        received = self.m.received_body("echo $x \\$x ${y} $(echo $x) `echo $x` $((1 + $x))", values)
        self.assertEqual(received, "echo a; b $x ${y} $(echo $x) `echo $x` $((1 + $x))")
        self.assertIsInstance(received, self.m.OutputBody)  # ${y}, which the line does not settle
        received = self.m.received_body("echo $x ${u:-\\$(echo X)}", lambda text: ("v", True) if text == "$x" else (None, True))
        self.assertEqual(received, "echo v ${u:-$(echo X)}")  # a kept expansion's escapes are taken off as before
        self.assertNotIsInstance(received, self.m.OutputBody)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        for body in ("${" * 3000, "${x:-" * 3000 + "}" * 3000, "echo $x" * 3000, "$[" * 3000, "${x}" * 3000,
                     "${u:-b}" * 3000):
            line = "x=a; sh <<EOF\n%s\nEOF\ngit push" % body
            with self.subTest(body=body[:20]):
                started = time.monotonic()
                findings = self.analysis(line).findings
                self.assertLess(time.monotonic() - started, 5.0)
                self.assertIn(("git", ("push", "push")), findings)


class MultiosInputTest(BashHookCase):
    """SPD-209, filed by SPD-207's engineer: zsh feeds a command every input its redirections name, one after
    another -- its MULTIOS option, on by default -- and a pipe into the command is one of them, read first; bash feeds
    it the last alone.  The hook read bash's way, and took a here-document's body over a `<` or a here-string wherever
    they stood, so on the SPD-208 tree analyse_command recorded no finding for either of the proposer's lines: `cat
    <<'A' <<'B' | sh` with `git push` in body A, where the Bash tool's zsh runs both bodies' lines, and `sh <<'EOF' <
    x.sh`, where it runs the body and then x.sh, which SPD-145 refuses a member as `sh < x.sh`.

    The rule (stdin_text.command_input, stdin_text.MultiosText): a command's standard input is read both ways, zsh's
    -- the pipe that feeds the command itself, then each input redirection on descriptor 0 in the order it stands, `<>`
    among them -- and bash's, the last of them.  A shell fed it reads each as its commands, and where either holds text
    the line does not spell a member is refused it with SPD-145's reason; Spud reads on.  Both readings are taken
    wherever the line stands, failing closed: the Bash tool's shell is zsh, but a line in a body bash or sh reads is
    read bash's way, and zsh's text can hide in a here-document what bash's runs.  An xargs whose two readings differ
    reads input the line does not spell.

    Probed 2026-09-23 through tests/probes/shell_probe.py in zsh 5.9 (arm64-apple-darwin26.0) under -f -o
    nobareglobqual and under -f, which printed the same, and in GNU bash 3.2.57, with TMPPREFIX in the probe's
    directory, each command a `touch`, x.sh holding `touch f1`:

    - zsh ran every input of `printf 'touch p1\\n' | sh <<'EOF'` (p1, then the body's h1), `sh <<'A' <<'B'`, `sh <<<
      'touch s1' <<'EOF'`, `sh <<'EOF' <<< ...`, `sh <<'EOF' < x.sh`, `printf ... | sh < x.sh`, `sh < x.sh <<< ...`,
      `cat <<'A' <<'B' | sh`, `cat <<'A' <<'B' | tee /dev/null | sh`, `printf ... | cat <<'EOF' | sh`, `sh <<< ...
      <> x.sh`, `printf ... | sh <> x.sh` and `xargs -0 sh -c <<'A' <<'B'`; bash ran the last input alone in each,
      and bodies `touch j\\` and `oined` made `joined` in zsh where bash ran `oined`;
    - `sh <<< 'touch s1' 3< x.sh` ran s1 alone in all three, and `printf ... | { sh <<'EOF' ...; }` ran the body
      alone: the pipe feeds the group, not the command in it;
    - `cat <<'A' <<'B' | sh` with A `cat <<X` and B `touch h2` made nothing in zsh, B standing in cat's
      here-document, and made h2 in bash; the same line in a body `bash <<'OUTER'` reads made h2 under all three."""

    FIRST = "cat <<'A' <<'B' | sh\ngit push\nA\ntrue\nB"  # the proposer's lines: the push in body A
    FILE = "sh <<'EOF' < x.sh\ntrue\nEOF"  # ... and the body, then x.sh
    # zsh's text holds the push in cat's here-document; bash's runs it
    HIDDEN = "cat <<'A' <<'B' | sh\ncat <<X\nA\ngit push\nB"

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def verbs(self, command):
        return [detail[0] for kind, detail in self.analysis(command).findings if kind == "git"]

    def forms(self, command):
        return [detail[0] for kind, detail in self.analysis(command).findings if kind == "script"]

    def law_7(self, line):
        """The analysis finds the push and nothing unread; a member is refused it, and Spud never is."""
        with self.subTest(line=line):
            self.assertIn("push", self.verbs(line))
            self.assertEqual(self.forms(line), [])
            self.assertRefused(line, "Law 7")
            self.assertSilent(line, agent_id=None)

    def unspelled(self, line, spud=True):
        """A "stdin" script finding, refused a member with SPD-145's reason; silent for Spud where `spud` says so."""
        with self.subTest(line=line):
            self.assertIn("stdin", self.forms(line))
            r = self.assertRefused(line, SCRIPT_WORDING)
            self.assertIn("standard input that the line does not spell", r.reason)
            if spud:
                self.assertSilent(line, agent_id=None)

    def data(self, line):
        """No push, no script finding, and silent for every caller."""
        with self.subTest(line=line):
            self.assertNotIn("push", self.verbs(line))
            self.assertEqual(self.forms(line), [])
            self.assertSilent(line)
            self.assertSilent(line, agent_id=None)

    def test_the_tickets_evidence_is_refused_a_member(self):
        self.law_7(self.FIRST)
        self.assertEqual(self.verbs(self.FIRST), ["push"])
        self.unspelled(self.FILE)
        self.assertEqual(self.forms(self.FILE), ["stdin"])

    def test_zsh_reads_every_input_in_turn(self):
        """Each line hands a shell `git push` through an input bash's reading drops, or through two inputs zsh joins."""
        for line in ("echo 'git push' | sh <<'EOF'\ntrue\nEOF",  # the pipe, then the body
                     "sh <<< 'git push' <<'EOF'\ntrue\nEOF",
                     "echo 'git push' | cat <<'EOF' | sh\ntrue\nEOF",
                     "cat <<< 'git push' <<'EOF' | bash\ntrue\nEOF",
                     "cat <<'A' <<'B' | tee /dev/null | sh\ngit push\nA\ntrue\nB",
                     "{ cat <<'A' <<'B'; } | sh\ngit push\nA\ntrue\nB",
                     "cat - <<'A' <<'B' | sh -s\ngit push\nA\ntrue\nB"):
            self.law_7(line)
        # zsh joins the two bodies' lines, as it made `joined`: the push neither body holds alone (body A, read alone as
        # well, ends in a backslash that escapes nothing, which SPD-191 refuses every caller)
        line = "sh <<'A' <<'B'\ngit \\\nA\npush\nB"
        self.assertIn("push", self.verbs(line))
        self.assertRefused(line, "Law 7")

    def test_a_file_beside_another_input_is_refused_a_member(self):
        """zsh reads the file too, after or before the text the line spells, so the shell reads text the line does not
        spell -- and so does a shell fed a pipe from a file ahead of its own here-document."""
        for line in (self.FILE, "sh < x.sh <<< 'git status'", "sh <<'EOF' 0< x.sh\ngit status\nEOF",
                     "cat x.sh | sh <<'EOF'\ngit status\nEOF", "cat <<'EOF' < x.sh | sh\ngit status\nEOF",
                     "cat x.sh | cat <<'EOF' | sh\ngit status\nEOF"):
            self.unspelled(line)
        # `<>` opens its file on standard input for reading and writing: Spud's own write of it is Law 1's, as it was
        for line in ("sh <<< 'git status' <> tests/x.sh", "echo 'git status' | sh <> tests/x.sh"):
            self.unspelled(line, spud=False)

    def test_bash_reads_the_last_input_alone(self):
        """Both readings are read wherever the line stands: HIDDEN's zsh text holds the push in cat's here-document,
        which bash's runs, and a body bash reads is bash's reading; the here-string after a body is the input bash
        reads."""
        self.law_7(self.HIDDEN)
        self.law_7("bash <<'OUTER'\n%s\nOUTER" % self.HIDDEN)
        self.law_7("sh <<'EOF' <<< 'git push'\ntrue\nEOF")

    def test_an_xargs_whose_readings_differ_reads_input_the_line_does_not_spell(self):
        line = "cat <<'A' <<'B' | xargs -0 sh -c\ngit push\nA\ntrue\nB"
        self.assertEqual(self.forms(line), ["xargs"])
        self.assertRefused(line, "xargs reads from input the line does not spell")
        self.assertSilent(line, agent_id=None)
        self.law_7("echo 'git push' | xargs -0 sh -c")  # one input: one reading, as before

    def test_a_single_input_reads_as_before(self):
        for line in ("sh <<'EOF'\ngit push\nEOF", "cat <<'EOF' | sh\ngit push\nEOF", "echo 'git push' | sh",
                     "sh <<< 'git push'", "echo 'git push' | { sh; }", "sh <<'A' <<'B'\ngit push\nA\ntrue\nB"):
            self.law_7(line)
        for line in ("sh < x.sh", "cat x.sh | sh", "echo 'git status' | sh < x.sh"):
            self.unspelled(line)
        for line in ("sh <<'EOF'\ngit status\nEOF", "cat <<'A' <<'B'\ngit push\nA\ntrue\nB",
                     "sh <<'A' <<'B'\ngit status\nA\ntrue\nB", "sh <<< 'git status' 3< x.sh",
                     "echo 'git push' | { sh <<'EOF'\ntrue\nEOF\n}"):  # the group's pipe is no input of sh's
            self.data(line)


class CompoundInputTest(BashHookCase):
    """SPD-210, filed by SPD-207's engineer and widened by SPD-209's: a shell inside a `-c` string, a `{ }` group, a `( )`
    subshell, a loop or a conditional runs on the standard input the command around it is given, and SPD-145 read that
    input only on a shell's own simple command.  On the SPD-209 tree analyse_command recorded no finding for the
    proposer's `sh -c sh < x.sh`, `{ sh; } < x.sh` and `(sh) < x.sh`, while `sh < x.sh` records a script "stdin"
    finding; nor for `{ sh; } <<'EOF'` with `git push` in the body, nor for `{ sh; } <<'EOF' < x.sh`.

    The rule: a `-c` string's commands, and `eval`'s, start from the input of the command that runs them
    (analyse.analyse_command's `stdin` and `fed`), and so do the substitutions in a command's words, which read the input
    of the list they stand in; and a compound command's own input redirections, which the walk reads after its closer,
    stand where it opens (walk.walk_line: where any compound on the line has one, the line is walked again with each
    compound's input known when it opens), read as SPD-209 reads a command's: zsh's reading, the pipe that feeds the
    compound and then each input in turn, and bash's, the last.  A shell there reading text the line spells reads it as
    its commands; one reading text the line does not spell is refused a member with SPD-145's reason, and Spud reads on.

    Probed 2026-09-23 through tests/probes/shell_probe.py in zsh 5.9 (arm64-apple-darwin26.0) under -f -o
    nobareglobqual and under -f, which printed the same, and in GNU bash 3.2.57, with TMPPREFIX in the probe's
    directory, each file and body a `touch`:

    - all three ran the file's line for `sh -c sh < i1.sh`, `{ sh; } < i2.sh`, `(sh) < i3.sh`, `cat i4.sh | { sh; }`,
      `cat i5.sh | (sh)`, `for f in a; do sh; done < i6.sh`, `if true; then sh; fi < i7.sh`, `case x in x) sh;; esac <
      i8.sh`, `while true; do sh; break; done < i9.sh`, `until false; do sh; break; done < i10.sh`, `eval sh < i11.sh`,
      `sh -c '{ sh; }' < i12.sh`, `sh -c 'sh -c sh' < i13.sh`, `{ { sh; }; } < x.sh` and `{ sh; } 2> /dev/null < x.sh |
      cat`; and the body's or the string's line for the group, the subshell, the `-c` string, for, if, case, while and
      until fed a here-document or a here-string, for `{ { sh; }; } <<< ...`, `{ sh; } <<< ... | cat`, `fn() { sh; } <<<
      ...` once fn was called, `eval sh <<< ...`, `sh -c '{ sh; }' <<< ...`, `sh -c 'sh -c sh' <<< ...`, `{ echo $(sh) >
      /dev/null; } <<< ...`, the same in backticks, `(echo $(sh) > /dev/null) <<< ...` and `sh -c 'echo $(sh) >
      /dev/null' <<< ...`; zsh ran `repeat 1 do sh; done <<< ...` (bash: a syntax error), and bash ran the line after `1`
      in a body fed to `select f in a; do sh; break; done` (zsh's select took no choice there and ran nothing);
    - zsh ran both inputs of `{ sh; } <<'EOF' < x.sh` (the body, then x.sh), of `(sh) <<'EOF' < x.sh`, of `printf
      'touch p1\\n' | { sh; } < x.sh`, of `printf 'touch p2\\n' | { sh; } <<'EOF'` and of `{ sh; } <<< 'touch a1'
      <<'EOF'`, and bash the last alone in each; in `{ printf 'touch r1\\n' | echo $(sh) > /dev/null; } <<< 'touch r2'`
      zsh's substitution read r2, the group's input, and bash's r1, the pipe;
    - none ran anything for `{ sh; } 3< x.sh`, `echo $(sh) <<< ...` (the substitution runs before the command's own
      redirection), or `{ :; } <<< ...; sh`; `while read -r l; do echo "got $l"; done < x.sh` and `cat x.sh | while
      ...` printed the line, and `{ cat; } <<'EOF'` its body."""

    EVIDENCE = ("sh -c sh < x.sh", "{ sh; } < x.sh", "(sh) < x.sh")  # the proposer's lines
    FOLDED = "{ sh; } <<'EOF'\ngit push\nEOF"  # SPD-209's engineer's, with `{ sh; } < x.sh`

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def verbs(self, command):
        return [detail[0] for kind, detail in self.analysis(command).findings if kind == "git"]

    def forms(self, command):
        return [detail[0] for kind, detail in self.analysis(command).findings if kind == "script"]

    def law_7(self, line):
        """The analysis finds the push and nothing unread; a member is refused it, and Spud never is."""
        with self.subTest(line=line):
            self.assertIn("push", self.verbs(line))
            self.assertEqual(self.forms(line), [])
            self.assertRefused(line, "Law 7")
            self.assertSilent(line, agent_id=None)

    def unspelled(self, line):
        """A "stdin" script finding, refused a member with SPD-145's reason; silent for Spud."""
        with self.subTest(line=line):
            self.assertIn("stdin", self.forms(line))
            r = self.assertRefused(line, SCRIPT_WORDING)
            self.assertIn("standard input that the line does not spell", r.reason)
            self.assertSilent(line, agent_id=None)

    def data(self, line):
        """No push, no script finding, and silent for every caller."""
        with self.subTest(line=line):
            self.assertNotIn("push", self.verbs(line))
            self.assertEqual(self.forms(line), [])
            self.assertSilent(line)
            self.assertSilent(line, agent_id=None)

    def test_the_tickets_evidence_is_refused_a_member(self):
        for line in self.EVIDENCE:
            self.unspelled(line)
            self.assertEqual(self.forms(line), ["stdin"])
        self.law_7(self.FOLDED)
        self.unspelled("{ sh; } <<'EOF' < x.sh\ntrue\nEOF")

    def test_a_spelled_input_is_the_inner_shells_program(self):
        """The shapes of the evidence, and every compound command, fed a here-document or a here-string."""
        for line in ("(sh) <<'EOF'\ngit push\nEOF", "sh -c sh <<'EOF'\ngit push\nEOF", "{ sh; } <<< 'git push'",
                     "(sh) <<< 'git push'", "sh -c sh <<< 'git push'",
                     "for f in a; do sh; done <<'EOF'\ngit push\nEOF", "if true; then sh; fi <<< 'git push'",
                     "case x in x) sh;; esac <<'EOF'\ngit push\nEOF", "while true; do sh; break; done <<< 'git push'",
                     "until false; do sh; break; done <<< 'git push'", "repeat 1 do sh; done <<< 'git push'",
                     "select f in a; do sh; break; done <<'EOF'\n1\ngit push\nEOF",
                     "{ { sh; }; } <<< 'git push'", "{ sh; } <<< 'git push' | cat", "fn() { sh; } <<< 'git push'",
                     "eval sh <<< 'git push'", "sh -c '{ sh; }' <<< 'git push'", "sh -c 'sh -c sh' <<< 'git push'"):
            self.law_7(line)

    def test_a_file_on_any_compound_is_refused_a_member(self):
        for line in ("cat x.sh | (sh)", "for f in a; do sh; done < x.sh", "if true; then sh; fi < x.sh",
                     "case x in x) sh;; esac < x.sh", "while true; do sh; break; done < x.sh",
                     "until false; do sh; break; done < x.sh", "eval sh < x.sh", "sh -c '{ sh; }' < x.sh",
                     "sh -c 'sh -c sh' < x.sh", "{ { sh; }; } < x.sh", "{ sh; } 2> /dev/null < x.sh | cat"):
            self.unspelled(line)

    def test_a_substitution_reads_the_input_of_its_list(self):
        for line in ("{ echo $(sh) > /dev/null; } <<< 'git push'", "{ echo `sh` > /dev/null; } <<< 'git push'",
                     "(echo $(sh) > /dev/null) <<< 'git push'", "sh -c 'echo $(sh) > /dev/null' <<< 'git push'",
                     "{ printf x | echo $(sh) > /dev/null; } <<< 'git push'"):  # zsh's reading: the group's input
            self.law_7(line)
        self.unspelled("{ echo $(sh) > /dev/null; } < x.sh")

    def test_zsh_reads_a_compounds_inputs_in_turn(self):
        """The pipe into the compound, then each of its own input redirections: zsh reads them all, bash the last."""
        for line in ("{ sh; } <<'EOF' < x.sh\ntrue\nEOF", "(sh) <<'EOF' < x.sh\ntrue\nEOF",
                     "echo 'git status' | { sh; } < x.sh"):
            self.unspelled(line)
        for line in ("echo 'git push' | { sh; } <<'EOF'\ntrue\nEOF", "{ sh; } <<< 'git push' <<'EOF'\ntrue\nEOF"):
            self.law_7(line)

    def test_the_controls_read_as_before(self):
        """Another descriptor, a loop that only reads its input, input that stops at the compound's end, and a
        substitution in the words of a command whose own redirection comes after it."""
        for line in ("{ sh; } 3< x.sh", "while read -r l; do echo \"got $l\"; done < x.sh",
                     "cat x.sh | while read -r l; do echo \"got $l\"; done", "{ :; } <<< 'git push'; sh",
                     "echo $(sh) <<< 'git push'", "{ cat; } <<'EOF'\ngit push\nEOF"):
            self.data(line)


class FunctionInputTest(BashHookCase):
    """SPD-212, filed by SPD-210's engineer: a function the line defines was read once, where it is defined, on the input
    that place stands on, and never with the input a call of it is given, so a shell in its body reading that input ran
    unread.  On the SPD-210 tree analyse_command recorded no finding for the proposer's `f() { sh; }; f < x.sh`, while
    `{ sh; } < x.sh` records a script "stdin" finding, and read nothing of the here-string in `g() { sh; }; g <<< 'touch
    g1'`.  SPD-210 covered a redirection on the definition itself (`fn() { sh; } < x.sh`), not on the call.

    The rule (walk.walk_line): a call of a function the line defines, in command position, hands the function's body the
    standard input the call is given (its pipe and its own input redirections, as SPD-209 reads a command's), and the line
    is walked again with that input standing where the body opens, once per distinct input, as SPD-210 walks it again for
    a compound command's own input.  A call the second walk finds (in a body, in a group given input) is read the same
    way.  These walks are bounded as SPD-203's per-call readings are (READINGS_PER_NAME), across the whole analysis: past
    the bound a line's bodies are read once more on input the line does not spell, refused a member on doubt.  A
    function an `eval` string defines is defined in the shell that runs the line, but its text's reading is over before
    the call: a call of it given input is refused a member as such input is (script_files, "function"); a function a
    substitution or a `-c` string defines stays in its own process.

    Probed 2026-09-23 through tests/probes/shell_probe.py in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, which printed the same, and in GNU bash 3.2.57, with TMPPREFIX in the probe's directory, each file and
    body a `touch`:

    - all three made the file for `g() { sh; }; g <<< 'touch g1'`, `f() { sh; }; f < x.sh`, a here-document fed to the call,
      `printf 'touch p1\\n' | p` (p's body `sh`), `k() if true; then sh; fi; k <<< ...`, `s() ( sh ); s <<< ...`, `function
      fk { sh; }` and `function fp () { sh; }` called with a here-string, `a() { sh; }; b() { a; }; b <<< ...`, a chain of
      four such functions, `c() { sh; }; { c; } <<< ...`, `m <<< 'touch m1'; m <<< 'touch m2'` (both), `echo $(q <<<
      ...)`, `eval "e <<< ..."`, `eval e < x.sh`, `r <<< ... > /dev/null`, `time t <<< ...`, `u() { v <<< 'touch
      inner1'; }` called with other input (v's body `sh`), `f() { sh -c sh; }` and `h() { { sh; }; }` called with a
      here-string, a definition and its call inside one subshell and inside one `sh -c` string, and a body that calls
      itself with a here-string;
    - zsh made d1 for `d() { sh; } <<< 'true'; d <<< 'touch d1'`, and `d() { cat; } < def.txt; d < call.txt` printed
      call then def: zsh reads the call's input and then each of the definition's own in turn; bash read the
      definition's alone (def, and no d1);
    - zsh made z1 for its `z() sh; z <<< 'touch z1'` (bash: a syntax error), and `z() cat < def.txt; z < call.txt`
      printed def alone: a simple command's own input replaces the call's, as it does in a `{ }` body (`f() { sh <<<
      'true'; }; f <<< 'touch own1'` made nothing in any of the three);
    - all three made ev2 for `eval 'ev() { sh; }'; ev <<< 'touch ev2'`;
    - none made anything for `command cw <<< ...` (command runs no function), for `(o() { sh; }); o <<< ...` or `x=$(xf()
      { sh; }; echo); xf <<< ...` (the definition stays in its subshell), or for a call with no input."""

    EVIDENCE = ("g() { sh; }; g <<< 'touch g1'", "f() { sh; }; f < x.sh")  # the proposer's lines
    SPELLED = "f() { sh; }; f <<'EOF'\ngit push\nEOF"  # a here-document body the inner shell runs as its program

    # CompoundInputTest's readings of the analysis and its asserts, which this class makes of the same findings
    analysis, verbs, forms = CompoundInputTest.analysis, CompoundInputTest.verbs, CompoundInputTest.forms
    law_7, unspelled, data = CompoundInputTest.law_7, CompoundInputTest.unspelled, CompoundInputTest.data

    def writes(self, command):
        return sorted(str(e[1]) for e in self.analysis(command).arg_writes)

    def test_the_tickets_evidence_is_refused_a_member(self):
        touched, unspelled = self.EVIDENCE
        # the here-string is the inner shell's program: the call reads as `sh <<< 'touch g1'` does
        self.assertEqual(self.writes(touched), self.writes("sh <<< 'touch g1'"))
        self.assertTrue(self.writes(touched))
        self.assertEqual(self.bash(touched).reason, self.bash("sh <<< 'touch g1'").reason)
        self.assertRefused(touched, "deliverables")
        self.unspelled(unspelled)
        self.assertEqual(self.forms(unspelled), ["stdin"])
        self.law_7(self.SPELLED)

    def test_every_input_a_call_is_given_reaches_the_body(self):
        for line in ("f() { sh; }; f <<< 'git push'", "p() { sh; }; echo 'git push' | p",
                     "k() if true; then sh; fi; k <<< 'git push'", "s() ( sh ); s <<< 'git push'",
                     "function fk { sh; }; fk <<< 'git push'", "function fp () { sh; }; fp <<< 'git push'",
                     "z() sh; z <<< 'git push'", "a() { sh; }; b() { a; }; b <<< 'git push'",
                     "f1() { sh; }; f2() { f1; }; f3() { f2; }; f4() { f3; }; f4 <<< 'git push'",
                     "c() { sh; }; { c; } <<< 'git push'", "m() { sh; }; m <<< 'git status'; m <<< 'git push'",
                     "q() { sh; }; echo $(q <<< 'git push')", "e() { sh; }; eval \"e <<< 'git push'\"",
                     "r() { sh; }; r <<< 'git push' > /dev/null", "t() { sh; }; time t <<< 'git push'",
                     "v() { sh; }; u() { v <<< 'git push'; }; u <<< 'true'", "f() { sh -c sh; }; f <<< 'git push'",
                     "f() { { sh; }; }; f <<< 'git push'", "(f() { sh; }; f <<< 'git push')",
                     "f() { sh; f <<< 'git push'; }; f", "sh -c 'f() { sh; }; f <<< \"git push\"'"):
            self.law_7(line)
        for line in ("f() { sh; }; cat x.sh | f", "s() ( sh ); s < x.sh", "a() { sh; }; b() { a; }; b < x.sh",
                     "c() { sh; }; { c; } < x.sh", "e() { sh; }; eval e < x.sh", "sh -c 'f() { sh; }; f < x.sh'",
                     "z() sh; z < x.sh"):
            self.unspelled(line)

    def test_zsh_reads_the_calls_input_then_the_definitions(self):
        """A definition's own input redirections (SPD-210) with a call's: zsh reads the call's and then each of them,
        bash the definition's last alone, which SPD-210 already read."""
        self.law_7("d() { sh; } <<< 'true'; d <<< 'git push'")
        self.unspelled("d() { sh; } <<< 'true'; d < x.sh")
        self.law_7("d() { sh; } <<< 'git push'; d <<< 'true'")

    def test_the_readings_of_one_body_have_a_bound(self):
        """READINGS_PER_NAME distinct inputs are each read; one more, or a chain of calls longer than that, reads every
        body on the line once more on input the line does not spell: refused a member on doubt, Spud reading on."""
        cap = load_spud_module().READINGS_PER_NAME
        inputs = "m() { sh; }; " + "; ".join("m <<< 'true %d'" % k for k in range(1, cap + 1))
        self.data(inputs)
        self.unspelled(inputs + "; m <<< 'git push'")
        chain = "f1() { sh; }; " + "; ".join("f%d() { f%d; }" % (k + 1, k) for k in range(1, cap + 2))
        self.unspelled(chain + "; f%d <<< 'git push'" % (cap + 2))
        # the bound holds across the analysis, so a line nested in a body read on each input cannot multiply the walks
        nested = "f() { echo $(g() { sh; }; %s); }; " % "; ".join("g <<< 'true %d'" % k for k in range(1, cap + 1))
        nested += "; ".join("f <<< 'true %d'" % k for k in range(1, cap + 1))
        self.assertEqual(self.analysis(nested).body_walks, cap)
        self.unspelled(nested)

    def test_a_function_an_eval_defines_is_refused_on_doubt(self):
        """Its body stands in text whose reading is over before the call: a call given input is refused a member."""
        for line in ("eval 'f() { sh; }'; f <<< 'git push'", "eval 'f() { sh; }'; f < x.sh"):
            with self.subTest(line=line):
                self.assertEqual(self.forms(line), ["function"])
                r = self.assertRefused(line, SCRIPT_WORDING)
                self.assertIn("inside an `eval` string", r.reason)
                self.assertSilent(line, agent_id=None)
        self.law_7("eval 'f() { sh; }; f <<< \"git push\"'")  # the call inside the same string is read
        self.data("eval 'f() { sh; }'; f")

    def test_the_controls_read_as_before(self):
        """A wrapper that runs no function, a definition kept in its subshell, a call before the definition or with no
        input, a body that only prints its input, a body whose shell has input of its own, and a line zsh and bash read
        apart, whose definitions each reading binds for itself."""
        for line in ("cw() { sh; }; command cw <<< 'git push'", "(o() { sh; }); o <<< 'git push'",
                     "x=$(f() { sh; }; echo); f <<< 'git push'", "f <<< 'git push'; f() { sh; }", "f() { sh; }; f",
                     "f() { cat; }; f <<< 'git push'", "f() { sh <<< 'true'; }; f <<< 'git push'",
                     "z() sh <<< 'true'; z <<< 'git push'", "f() { sh; }; f <<< 'git status'; {true}"):
            self.data(line)
        self.law_7("f() { sh; }; f <<< 'git push'; {true}")


class ShellStandardInputTest(BashHookCase):
    """SPD-143: a shell started with no `-c` string and no script of its own runs the commands it reads on standard
    input, and the analysis read none of them.  Main (44803a6) found the push in `zsh <<EOF ... EOF`, a here-document
    fed to the shell itself, and nothing at all in `echo 'git push' | sh`, `printf 'git push' | bash -s`,
    `bash -s <<< 'git push'`, `bash /dev/stdin <<< 'git push'`, `echo 'git push' | sh -i`, `| env sh`, `| sudo sh`,
    `| xargs -0 sh -c` or `cat <<'EOF' | sh`: Laws 1, 5, 6 and 7 all stopped at the pipe.

    Spud probed the shells for this, a member in a worktree being unable to run one (SPD-094), with an executable `vcs`
    on PATH logging its arguments and each line run by /bin/bash -c from a scratch directory:
    - a shell with no -c and no script operand ran what it read: `echo 'vcs a' | sh`, `| bash`, `| zsh -f`, `| dash`,
      `| ksh`; with options first, `sh -s x` (x is $1), `bash -`, `zsh -f -`, `sh -e`, `bash --norc`, `sh -o errexit`,
      `sh -x`, `sh --` and `bash -i`; and `sh /dev/stdin`, whose script operand is that input.
    - `sh -c 'cat >/dev/null; vcs c'` ran c alone: the -c string is what runs, whatever its own commands read.
    - what the line spells: `printf 'vcs a\\nvcs b' | sh` ran both, `printf '%s\\n' 'vcs a' | sh` ran a,
      `echo -e 'vcs a\\nvcs b' | bash` ran both, and `echo 'vcs a\\nvcs b' | sh` ran one command under bash's echo and
      two under zsh's, which decodes the escapes -- the reading taken here, the one that finds more (SPD-039).
    - here-strings: `bash -s <<< 'vcs a'`, `zsh -f <<< 'vcs a'` and `sh <<< 'vcs a'` ran a.
    - through other shapes: `cat <<'EOF' | sh`, `{ echo 'vcs a'; echo 'vcs b'; } | sh`, `(echo 'vcs a') | sh`,
      `echo 'vcs a' | tee /dev/null | sh`, `echo 'vcs a' | env sh` and `| nohup sh` each ran what was printed.
    - xargs: `echo 'vcs a' | xargs -0 sh -c` ran `vcs a`, the whole input being the string; `| xargs sh -c` ran `vcs`
      with $0 set to a, its first word being the string.

    Standard input the line does not spell keeps main's reading: a file (`sh < f`), another program's output
    (`cat f | sh`, `curl ... | sh`), or text this reading cannot decode.  That is the same class as `sh script.sh`, a
    script the hook does not read either -- a hole in Law 7 for every caller, which Spud filed as a question for Eric
    rather than have a member refused for it here.  Eric's call on SPD-145 was to fail closed: the text is still not
    read, and a member is refused the shell that runs it (ScriptFileTest)."""

    # Each line feeds a shell one `git push` through a shape the shells probed above run.
    FED = ("echo 'git push' | sh", "echo 'git push' | bash", "echo 'git push' | zsh -f", "echo 'git push' | dash",
           "echo 'git push' | ksh", "printf 'git push\\n' | bash -s", "printf '%s\\n' 'git push' | sh",
           "print -r 'git push' | zsh -f", "bash -s <<< 'git push'", "zsh <<< 'git push'", "sh <<< 'git push'",
           "bash /dev/stdin <<< 'git push'", "echo 'git push' | sh /dev/stdin", "echo 'git push' | sh -",
           "echo 'git push' | sh -i", "echo 'git push' | sh -e", "echo 'git push' | sh -o errexit",
           "echo 'git push' | bash --norc", "echo 'git push' | sh --", "echo 'git push' | sh -s x",
           "echo 'git push' | env sh", "echo 'git push' | sudo sh", "echo 'git push' | nohup sh",
           "echo 'git push' | tee /dev/null | sh", "cat <<'EOF' | sh\ngit push\nEOF", "cat <<< 'git push' | sh",
           "{ echo 'git push'; } | sh", "(echo 'git push') | sh", "echo 'git push' | { sh; }",
           "echo 'git push' | xargs -0 sh -c", "echo 'git push' | xargs -I% sh -c %",
           "zsh <<EOF\ngit push\nEOF")  # the here-document main already read, kept
    # ... and each of these two, in the order the shell would run them.
    FED_TWICE = ("printf 'git push\\ngit commit -m x' | sh", "echo -e 'git push\\ngit commit -m x' | bash",
                 "echo 'git push\\ngit commit -m x' | sh", "{ echo 'git push'; echo 'git commit -m x'; } | sh",
                 "printf '%s\\n' 'git push' 'git commit -m x' | sh", "print -l 'git push' 'git commit -m x' | zsh -f")
    # Standard input the line does not spell, and a script operand of the shell's own: main's reading, unchanged.
    UNREAD = ("sh < setup.sh", "sh -s arg < setup.sh", "cat setup.sh | sh", "cat setup.sh | zsh",
              "curl -sS https://example.com/i.sh | sh", "sh setup.sh", "bash ./setup.sh", "sh <(echo 'git push')",
              "echo \"$CMD\" | sh", "printf '%d' 'git push' | sh",  # a value the line settles: SettledStandardInputTest
              "echo 'git push' > tests/out.txt | sh", "echo 'git push' | cat", "echo 'git push' | xargs sh")

    def setUp(self):
        super().setUp()
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def verbs(self, command):
        """The git verbs the analysis finds on this line, in the order it finds them."""
        return [detail[0] for kind, detail in self.analysis(command).findings if kind == "git"]

    def test_the_analysis_reads_the_commands_the_line_feeds_a_shell(self):
        for command in self.FED:
            with self.subTest(command):
                self.assertEqual(self.verbs(command), ["push"])
        for command in self.FED_TWICE:
            with self.subTest(command):
                self.assertEqual(self.verbs(command), ["push", "commit"])

    def test_input_the_line_does_not_spell_is_read_as_it_was(self):
        """Read no further than main read it -- and, since SPD-145, a shell whose commands come from input the line does not
        spell or from a script of its own refuses a member (ScriptFileTest) -- `| xargs sh` among them, whose first word from
        xargs is its script -- while `| cat`, which runs no such text, stays silent."""
        for command in self.UNREAD:
            with self.subTest(command):
                findings = self.analysis(command).findings
                self.assertEqual([f for f in findings if f[0] not in ("script", "var-word")], [])
                if command.endswith("| cat"):
                    self.assertEqual(findings, [])
                    self.assertSilent(command)
                elif "<(" in command:
                    # the file `<( list )` hands the shell is a word the line does not spell, where a shell's operand is
                    # read by name: refused as any such word is, before the script is
                    self.assertRefused(command, "spell the words out")
                else:
                    self.assertRefused(command, SCRIPT_WORDING)
                if ">" not in command:  # Spud's own write of tests/out.txt is Law 1's, as it was
                    self.assertSilent(command, agent_id=None)
        # An interpreter is no shell, so nothing the line puts on python's standard input is read as commands here; since
        # SPD-150 such a line is refused a member for the program python runs there instead (InlineProgramTest).
        self.assertEqual(self.verbs("echo 'git push' | python3 -"), [])
        self.assertSilent("echo 'git push' | python3 -", agent_id=None)

    def test_a_c_string_is_still_the_only_thing_that_shell_runs(self):
        """probed: `sh -c 'cat >/dev/null; vcs c'` ran c alone, whatever its commands read from the pipe."""
        self.assertEqual(self.verbs("echo 'git commit -m x' | sh -c 'git status'"), ["status"])
        self.assertEqual(self.verbs("echo 'git commit -m x' | bash -lc 'git status'"), ["status"])
        for command in ("echo x | sh -c 'cat'", "echo 'git push' | sh -c 'true'"):
            with self.subTest(command):
                self.assertEqual(self.verbs(command), [])
                self.assertSilent(command)

    def test_law_7_reaches_the_commands_a_shell_reads(self):
        for command in self.FED:
            with self.subTest(command):
                self.assertRefused(command, "Law 7")
                self.assertSilent(command, agent_id=None)  # Spud pushes; Law 7 binds members
        self.assertRefused("echo 'git commit -m x' | sh", "Law 7")
        self.assertSilent("echo 'git status' | sh")

    def test_a_spud_call_and_a_write_are_read_there_too(self):
        home, cli = self.home.path, self.spud_cli
        self.assertRefused("echo '%s ticket new --title x' | sh" % cli, "Law 6")
        self.assertRefused("echo '%s --as %s member log hi' | sh" % (cli, AGENT_A), "Law 5", agent_id=None)
        self.assertRefused("printf '%%s\\n' '%s init' | bash -s" % cli, "Law 6")
        self.assertRefused("echo 'echo x > ledger/tickets/SPD-001.md' | sh", "generated")
        self.assertRefused("echo 'echo x > ledger/tickets/SPD-001.md' | sh", "Law 1", agent_id=None)
        self.assertRefused("echo 'touch docs/x.md' | sh", "deliverables")
        self.assertRefused("cat <<'EOF' | sh\necho x > %s/bin/spud\nEOF" % home, "Law 1", agent_id=None)
        self.assertSilent("echo 'echo x > tests/out.txt' | sh")

    def test_the_text_runs_in_a_process_of_its_own(self):
        """The shell it feeds is another process: a cd there does not move the line, as a here-document's body does not."""
        out = str(self.out)
        self.assertRefused("echo 'cd %s' | sh; echo x > note.txt" % out, "deliverables")
        self.assertSilent("cd %s && echo 'git status' | sh && echo x > note.txt" % out)


class SettledStandardInputTest(BashHookCase):
    """SPD-148: SPD-143 read the text a line prints into a shell only where every word of the printing command was
    spelled literally, so `X='git push'; echo $X | sh`, `echo "$X" | sh` and `X=push; echo "git $X" | sh` were silent for
    a member while `echo 'git push' | sh` was refused -- the same Law 7 evasion one assignment away.  A word the printer
    is passed, and a here-string's word, is now read through the value the line settled (shell/arg_writes.resolved, the
    reading SPD-127 gave every write target), before the command runs, so its own prefix assignments reach none of them.

    bash splits an unquoted expansion at its blanks where zsh never does, and neither splits a quoted one, which the masked
    words mark since SPD-167, so a command whose settled value holds a blank is read both ways and its text is what either
    prints: `echo $X` prints `git push` in both, and where they differ -- an echo option the split makes of the value, a
    run of blanks, a printf format applied to each field -- both texts are read.  A reading the hook cannot spell leaves
    the text unread, as does every value the line does not settle: one assigned in a branch, a loop, a function body or a
    subshell, one a substitution computes, one `read` or `unset` changes, and one holding a glob character, which bash
    expands."""

    # Each line feeds a shell one `git push` through a value it settles itself.
    SETTLED = ("X='git push'; echo $X | sh", "X='git push'; echo \"$X\" | sh", "X=push; echo \"git $X\" | sh",
               "X=push; echo git ${X} | sh", "X=git; Y=push; echo $X $Y | sh", "X=push\necho \"git $X\" | bash",
               "X=push; printf 'git %s\\n' \"$X\" | sh", "X=push; print -r git $X | zsh -f",
               "X='git push'; echo $X | tee /dev/null | sh", "X='git push'; { echo $X; } | sh",
               "X=push; sh <<< \"git $X\"", "X='git push'; bash -s <<< $X", "X='git push'; cat <<< \"$X\" | sh",
               "X=push; cat <<EOF | sh\ngit $X\nEOF", "X=echo; $X 'git push' | sh",
               "X='git push'; echo $X | xargs -0 sh -c")
    # A value the line does not settle, or one bash's split and zsh's whole reading do not print alike: unread.
    UNSETTLED = ("true && X='git push'; echo $X | sh", "if true; then X='git push'; fi; echo $X | sh",
                 "for X in 'git push'; do echo $X | sh; done", "f() { X='git push'; }; f; echo $X | sh",
                 "(X='git push'); echo $X | sh", "X='git push' | true; echo $X | sh", "X='git push' echo $X | sh",
                 "X='git push'; X=$(date); echo $X | sh", "X='git push'; unset X; echo $X | sh",
                 "X='git p*'; echo $X | sh", "echo \"$CMD\" | sh", "X=push; echo \"git $Y\" | sh")
    # SPD-167: a value bash's split of an unquoted expansion prints otherwise than zsh does -- each reading is read.
    SPLIT = ("X='-n git push'; echo $X | sh", "X='git  push'; echo $X | sh", "X='git push'; printf '%s\\n' $X | sh",
             "X='git  push'; bash -s <<< $X", "X='-n git push'; echo $X | xargs -0 sh -c")
    # ... and a quoted one, which neither shell splits, is one word in both readings.
    QUOTED = ("X='git push'; printf '%s\\n' \"$X\" | sh", "X='git  push'; printf '%s\\n' \"$X\" | sh",
              "X='push'; Y='git  '; printf '%s\\n' \"$Y$X\" | sh", "X='git  push'; bash -s <<< \"$X\"",
              "X='git push'; printf '%s\\n' \"a; $X\" | sh")
    # One reading the hook cannot spell leaves the text unread, whatever the other prints: zsh passes printf the format
    # `git %d`, a directive this module does not apply, where bash's `printf git %d` prints `git`.
    HALF = ("X='git %d'; printf $X | sh", "X='%d git push'; printf $X | sh")

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def verbs(self, command):
        return [detail[0] for kind, detail in self.analysis(command).findings if kind == "git"]

    def test_a_settled_value_is_read_where_the_shell_prints_it(self):
        for command in self.SETTLED:
            with self.subTest(command):
                self.assertEqual(self.verbs(command), ["push"])
                self.assertRefused(command, "Law 7")
                self.assertSilent(command, agent_id=None)  # Spud pushes; Law 7 binds members
        self.assertEqual(self.verbs("X='git push'; Y='git commit -m x'; { echo $X; echo \"$Y\"; } | sh"), ["push", "commit"])
        self.assertSilent("X='git status'; echo $X | sh")

    def test_a_value_the_line_does_not_settle_stays_unread(self):
        # ... and so is text the line does not spell, which since SPD-145 refuses a member (fail closed) and leaves Spud be
        for command in self.UNSETTLED:
            with self.subTest(command):
                self.assertNotIn("push", self.verbs(command))
                self.assertRefused(command, SCRIPT_WORDING)
                self.assertSilent(command, agent_id=None)

    def test_a_value_the_two_shells_print_apart_is_read_both_ways(self):
        """SPD-167: SPD-148 read `$X` and `"$X"` alike, so a value bash's split of an unquoted expansion printed otherwise
        than zsh did was text the hook could not say -- refused a member only as unread input (SPD-145), and `printf '%s\\n'
        "$X" | sh`, one word in both shells, with it.  The masked words now mark a quoted `$NAME`: a quoted value is read
        whole, an unquoted one as each shell passes it, and a shell fed what either prints is read on both texts, so the
        bash-only `X='-n git push'; echo $X | sh` earns the Law 7 refusal `echo 'git push' | sh` earns."""
        for command in self.SPLIT + self.QUOTED:
            with self.subTest(command):
                findings = self.analysis(command).findings
                self.assertIn("push", [d[0] for k, d in findings if k == "git"])
                self.assertEqual([d[0] for k, d in findings if k == "script"], [])
                r = self.assertRefused(command, "Law 7")
                self.assertNotIn(SCRIPT_WORDING, r.reason)
                self.assertSilent(command, agent_id=None)
        # the quoted value is one word: printf applies its format once, where bash's split of `$X` applies it per field
        self.assertEqual(self.verbs("X='git push'; printf '%s\\n' \"$X\" | sh"), ["push"])
        self.assertSilent("X='git status'; printf '%s\\n' \"$X\" | sh")
        self.assertSilent("X='-n git status'; echo $X | sh")

    def test_a_reading_the_hook_cannot_spell_leaves_the_text_unread(self):
        for command in self.HALF:
            with self.subTest(command):
                findings = self.analysis(command).findings
                self.assertEqual([f for f in findings if f[0] != "script"], [])
                self.assertEqual([d[0] for k, d in findings if k == "script"], ["stdin"])
                self.assertRefused(command, SCRIPT_WORDING)
                self.assertSilent(command, agent_id=None)

    def test_the_masked_words_mark_a_quoted_name(self):
        """Only a `$NAME` inside double quotes carries the mark, and every reader that takes the word whole -- a write
        target, the command word -- reads it as before."""
        m = load_spud_module()
        mark = ""
        self.assertEqual(m.neutralize_quoted_globs('echo "$X" $Y "a $Z/b" \'$W\''),
                         'echo "$X%s" $Y "a $Z%s/b" \'$W\'' % (mark, mark))
        self.assertNotIn(mark, m.neutralize_quoted_globs('echo "$1" "$@" "cost $" "${X:-y}" "${#X}"'))
        # SPD-141: a plain `"${X}"` is `"$X"`, its braces left plain
        self.assertEqual(m.neutralize_quoted_globs('echo "${X}" "a ${Y}/b"'), 'echo "${X}%s" "a ${Y}%s/b"' % (mark, mark))
        self.assertEqual(m.deglob("$X" + mark), "$X")
        self.assertRefused("X=push; \"$X\"; git \"$X\"", "Law 7")
        self.assertRefused("S=docs; echo x > \"$S/y.md\"", "deliverables")
        self.assertSilent("S=tests; echo x > \"$S/y.md\"")
        self.assertRefused("echo x > \"$HOME/planted\"", "$HOME/planted")  # a reason names the spelling, unmarked

    def test_a_spud_call_and_a_write_are_read_through_it_too(self):
        cli = self.spud_cli
        self.assertRefused("C='%s'; echo \"$C ticket new --title x\" | sh" % cli, "Law 6")
        self.assertRefused("T=ledger/tickets/SPD-001.md; echo \"echo x > $T\" | sh", "generated")
        self.assertRefused("D=docs/x.md; echo touch $D | sh", "deliverables")
        self.assertSilent("T=tests/out.txt; echo \"echo x > $T\" | sh")


if __name__ == "__main__":
    unittest.main()
