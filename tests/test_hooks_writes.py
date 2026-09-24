"""PreToolUse(Bash): writes by argument -- the files and directories a command names and writes, the trees find, xargs, a
recursive copy or an extraction write, spelled and scripted writes, and how a target resolves."""

import os
import unittest
from unittest import mock

from helpers import load_spud_module
from hookcase import AGENT_A, AGENT_B, AGENT_C, AGENT_D, DB_WORDING, GIT_DIR_WORDING, GIT_FILE_WORDING, INLINE_WORDING, OUTSIDE
from hookcase import SESSION, VARIABLE_WORDING, WORD_WORDING, BashHookCase, plant_git_dir


ARG_WORDING = "a write by argument"  # SPD-121: a file a command names as an operand and writes, checked as a redirection's target

# SPD-121: one line per command the Bash hook reads writes by argument from, `{}` the file it writes.
ARG_WRITERS = (
    ("cp", "cp tests/src.txt {}"),
    ("mv", "mv /tmp/spd-121-src {}"),  # a source every caller may remove: mv's sources are written too
    ("ln -s", "ln -s /tmp/x {}"),
    ("link", "link tests/src.txt {}"),
    ("install", "install -m 644 tests/src.txt {}"),
    ("install -d", "install -d {}"),
    ("mkdir", "mkdir -p {}"),
    ("touch", "touch {}"),
    ("rm", "rm -f {}"),
    ("unlink", "unlink {}"),
    ("rmdir", "rmdir {}"),
    ("truncate", "truncate -s 0 {}"),
    ("chmod", "chmod +x {}"),
    ("chown", "chown nobody {}"),
    ("chgrp", "chgrp staff {}"),
    ("chflags", "chflags nohidden {}"),
    ("sed -i ''", "sed -i '' s/a/b/ {}"),
    ("sed -i.bak", "sed -i.bak s/a/b/ {}"),
    ("sed -I", "sed -I '' -e s/a/b/ {}"),
)


class ArgumentWriteTest(BashHookCase):
    """SPD-121: the Bash hook held a write to the path rule only when it was a redirection, a tee operand or one of SPD-049's
    file-writing git options, so a command that writes the files it names as operands was never read: a bound member's
    `cp tests/src.txt tests/fake/.git/hooks/post-index-change`, `ln -s /tmp/x tests/fake/.git/hooks/x` and
    `cp tests/src.txt /Users/Nobody/.zshrc` were silent, past Law 5, SPD-064's outside allowlist and SPD-066's .git rule.

    Spud's design decision: a write by argument is a write by redirection.  The files cp, mv, ln (and link), install, mkdir,
    touch, rm (and unlink), rmdir, truncate, chmod, chown, chgrp, chflags and sed in place name as operands go through the
    check a redirection target goes through, for every caller: a member held to its globs, the outside allowlist and the .git
    rule; Spud held to Law 1 in a project and free in his own files and outside every project.  An operand the hook cannot
    resolve, and a glob, are read as a redirection target is.  Each command is read on this Mac's BSD grammar, probed in a
    scratch directory (getopt stops at the first operand, so `mkdir new -p` made ./-p; `sed -i` always takes the next word as
    its suffix, so `sed -i -e X f` backed f up to f-e; `install -M log` wrote log), and GNU's -t/--target-directory and -T
    are read too.  AGENT_A plans home:tests/** and home:bin/spud; AGENT_C plans home:bin/**."""

    def setUp(self):
        super().setUp()
        self.narrow = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:bin/**"]), AGENT_C)
        home = self.home.path
        self.home.env["HOME"] = "/Users/Nobody"  # a HOME outside every project and every temp root, as OutsideProjectTest's
        for d in ("tests/fake/.git/hooks", "tests/out", "tests/copy", "docs", "ledger/tickets", "reports", "bin"):
            (home / d).mkdir(parents=True, exist_ok=True)
        for f in ("tests/src.txt", "tests/b.txt", "docs/x.md", "ledger/tickets/SPD-001.md", "bin/spud"):
            (home / f).write_text("a\n", encoding="utf-8")
        self.scratchpad = "/private/tmp/claude-%d/-Users-Someone-Personal-Spud/%s/scratchpad" % (os.getuid(), SESSION)

    def refused_targets(self):
        """(target, the needle its refusal carries) for AGENT_A: a .git component, outside the deliverables, the home's
        generated roots, and outside every project."""
        return (("tests/fake/.git/hooks/post-index-change", GIT_DIR_WORDING), ("docs/x.md", "deliverables"),
                ("ledger/tickets/SPD-001.md", "generated"), ("reports/2026-09-17.md", "generated"),
                ("/Users/Nobody/.zshrc", OUTSIDE), ("~/.zshrc", OUTSIDE))

    def silent_targets(self):
        return ("tests/out/new.txt", self.scratchpad + "/probe.txt", "/tmp/spd-121-x")

    def test_the_tickets_lines_are_refused_with_the_path_in_the_reason(self):
        for command, needle, path in (
            ("cp tests/src.txt tests/fake/.git/hooks/post-index-change", GIT_DIR_WORDING, "tests/fake/.git/hooks/post-index-change"),
            ("ln -s /tmp/x tests/fake/.git/hooks/x", GIT_DIR_WORDING, "tests/fake/.git/hooks/x"),
            ("cp tests/src.txt /Users/Nobody/.zshrc", OUTSIDE, "/Users/Nobody/.zshrc"),
        ):
            with self.subTest(command):
                r = self.assertRefused(command, needle)
                self.assertIn(path, r.reason)
                self.assertIn(ARG_WORDING, r.reason)
        # The fourth line lays out a bare repository's objects/ and refs/ with mkdir.  Both lie inside a tests/** member's
        # globs and no .git component is spelled, so the path rule allows it; SPD-066 already refuses any git call a member
        # points at a repository that is not a known checkout's own, which is what such a layout is for.  A member whose
        # globs do not cover tests/b is refused it, with the path named.
        self.assertSilent("mkdir -p tests/b/objects tests/b/refs")
        r = self.assertRefused("mkdir -p tests/b/objects tests/b/refs", "deliverables", agent_id=AGENT_C)
        self.assertIn("tests/b/objects", r.reason)

    def test_every_command_is_held_to_the_path_rule(self):
        for label, form in ARG_WRITERS:
            for target, needle in self.refused_targets():
                command = form.format(target)
                with self.subTest(command=command):
                    r = self.assertRefused(command, needle)
                    self.assertIn(target if target[0] != "~" else ".zshrc", r.reason)  # the reason names the path

    def test_every_command_is_silent_inside_the_deliverables_the_scratchpad_and_tmp(self):
        for label, form in ARG_WRITERS:
            for target in self.silent_targets():
                command = form.format(target)
                with self.subTest(command=command):
                    self.assertSilent(command)

    def test_a_command_that_writes_nothing_by_argument_stays_silent(self):
        for command in ("sed -n p docs/x.md", "sed s/a/b/ docs/x.md", "sed -e s/a/b/ -e s/c/d/ docs/x.md", "sed -E 's/(a)/b/' docs/x.md",
                        "cat docs/x.md", "ls -la docs", "grep -r x docs", "cp docs/x.md tests/out/", "ln -s docs/x.md tests/x-link",
                        "chmod -R u+w tests/out", "sed s/a/b/ -i '' docs/x.md"):  # BSD: after the script, -i and '' are files sed reads
            with self.subTest(command):
                self.assertSilent(command)

    def test_a_directory_destination_writes_each_source_inside_it(self):
        refused = (
            ("cp tests/src.txt tests/b.txt docs/", "docs/src.txt"),  # several sources: a directory
            ("cp tests/src.txt tests/b.txt docs", "docs/src.txt"),
            ("cp tests/src.txt docs", "docs/src.txt"),  # an existing directory, no slash
            ("cp tests/src.txt tests/fake/.git", "tests/fake/.git/src.txt"),
            ("cp -r tests/fake/.git tests/copy", "tests/copy/.git"),  # the source's own name, joined
            ("cp -R tests/fake/.git tests/copy/", "tests/copy/.git"),
            ("cp -t docs tests/src.txt", "docs/src.txt"),  # GNU's -t, in every spelling
            ("cp -tdocs tests/src.txt", "docs/src.txt"),
            ("cp -rt docs tests/src.txt", "docs/src.txt"),
            ("cp --target-directory=docs tests/src.txt", "docs/src.txt"),
            ("cp --target-directory docs tests/src.txt", "docs/src.txt"),
            ("cp --target docs tests/src.txt", "docs/src.txt"),
            ("cp -t tests/fake/.git/hooks tests/src.txt", "tests/fake/.git/hooks/src.txt"),
            ("cp -T tests/src.txt tests/fake/.git", "tests/fake/.git"),  # -T: the destination itself, never inside it
            ("cp tests/*.txt docs/", "docs/b.txt"),  # a source glob: each file it matches, in order
            ("mv tests/src.txt docs/", "docs/src.txt"),
            ("ln -s tests/src.txt docs/", "docs/src.txt"),
            ("install tests/src.txt docs", "docs/src.txt"),
        )
        for command, path in refused:
            with self.subTest(command):
                r = self.assertRefused(command, GIT_DIR_WORDING if ".git" in path else "deliverables")
                self.assertIn(path, r.reason)
        for command in ("cp tests/src.txt tests/out/", "cp tests/src.txt tests/b.txt tests/out", "cp -r tests/fake/.git tests/newcopy",
                        "cp -t tests/out tests/src.txt", "cp -T tests/src.txt tests/out", "cp tests/*.txt tests/out/",
                        "cp -R tests/out tests/copy/", "cp tests/src.txt tests/newdir/", "install tests/src.txt tests/out"):
            with self.subTest(command):
                self.assertSilent(command)

    def test_a_destination_reached_through_a_symlink_is_read_as_the_link_resolves(self):
        home = self.home.path
        (home / "tests" / "link").symlink_to(home / "tests" / "fake" / ".git")
        for command in ("cp tests/src.txt tests/link", "cp tests/src.txt tests/link/hooks/x", "touch tests/link/hooks/x",
                        "sed -i '' s/a/b/ tests/link/HEAD", "ln -s /tmp/x tests/link/hooks/x"):
            with self.subTest(command):
                r = self.assertRefused(command, GIT_DIR_WORDING)
                self.assertIn(str(home / "tests" / "fake" / ".git"), r.reason)  # the reading that has the component

    def test_a_case_variant_of_the_target_or_the_command_is_refused(self):
        for command in ("cp tests/src.txt tests/fake/.GIT/hooks/x", "touch tests/fake/.Git/index", "CP tests/src.txt docs/x.md",
                        "/bin/cp tests/src.txt docs/x.md", "/BIN/Cp tests/src.txt docs/x.md", "Rm docs/x.md", "SED -i '' s/a/b/ docs/x.md",
                        "/usr/bin/install tests/src.txt docs/x.md", "Mkdir docs/new"):
            with self.subTest(command):
                self.assertRefused(command, GIT_DIR_WORDING if "git" in command.lower() else "deliverables")

    def test_mv_removes_its_sources(self):
        for command, needle, path in (("mv docs/x.md tests/out/", "deliverables", "docs/x.md"),
                                      ("mv ledger/tickets/SPD-001.md /tmp/spd-121-x", "generated", "ledger/tickets/SPD-001.md"),
                                      ("mv tests/fake/.git tests/g2", GIT_DIR_WORDING, "tests/fake/.git"),
                                      ("mv -f -- docs/x.md tests/b.txt tests/out", "deliverables", "docs/x.md")):
            with self.subTest(command):
                r = self.assertRefused(command, needle)
                self.assertIn(path, r.reason)
        self.assertSilent("mv tests/src.txt tests/renamed.txt")
        self.assertSilent("mv tests/src.txt /tmp/spd-121-x")

    def test_ln_writes_its_link_name_never_its_target(self):
        for command in ("ln -s tests/fake/.git/hooks tests/hooks-link", "ln -s docs/x.md tests/x-link", "ln -s /Users/Nobody/.zshrc tests/rc",
                        "ln tests/src.txt tests/hard", "ln -sf /tmp/x tests/out/x", "cd tests && ln -s ../docs/x.md"):
            with self.subTest(command):
                self.assertSilent(command)
        for command, needle, path in (("ln -s tests/src.txt docs/link", "deliverables", "docs/link"),
                                      ("ln -s /tmp/x", "deliverables", "x"),  # one operand: ./x, here the home's root
                                      ("ln -s tests/fake/.git/hooks/post-index-change", "deliverables", "post-index-change"),
                                      ("ln -s /tmp/x tests/fake/.git/hooks/pre-commit", GIT_DIR_WORDING, "pre-commit"),
                                      ("link tests/src.txt docs/hard", "deliverables", "docs/hard")):
            with self.subTest(command):
                r = self.assertRefused(command, needle)
                self.assertIn(path, r.reason)

    def test_sed_in_place_and_its_backup(self):
        for command, path in (("sed -i '' s/a/b/ docs/x.md", "docs/x.md"),
                              ("sed -i.bak s/a/b/ docs/x.md", "docs/x.md"),
                              ("sed -i.bak s/a/b/ bin/spud", "bin/spud.bak"),  # bin/spud is AGENT_A's, its backup is not
                              ("sed -i -e s/a/b/ bin/spud", "bin/spud-e"),  # BSD: -i took -e as its suffix (probed)
                              ("sed -e s/a/b/ -i '' docs/x.md", "docs/x.md"),
                              ("sed -Ei '' s/a/b/ docs/x.md", "docs/x.md"),
                              ("sed -n -i '' -e s/a/b/ tests/b.txt docs/x.md", "docs/x.md"),
                              ("sed -I '' s/a/b/ tests/b.txt docs/x.md", "docs/x.md"),
                              ("sed -f tests/s.sed -i '' docs/x.md", "docs/x.md"),
                              ("sed -i '' -- s/a/b/ docs/x.md", "docs/x.md"),
                              ("sed --in-place s/a/b/ docs/x.md", "docs/x.md"),  # GNU's, read too
                              ("sed --in-place=.bak -e s/a/b/ docs/x.md", "docs/x.md"),
                              # a `/` in the suffix BSD would append to the file name fails there (rename: Not a directory,
                              # probed), so the word is also read as GNU reads it: the script, and the files after it
                              ("sed -i 's/a/b/' docs/x.md", "docs/x.md")):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables")
                self.assertIn(path, r.reason)
        for command in ("sed -i '' s/a/b/ bin/spud", "sed -i.bak s/a/b/ tests/b.txt", "sed -i '' s/a/b/ tests/b.txt",
                        "sed -i bak s/a/b/ tests/b.txt"):
            with self.subTest(command):
                self.assertSilent(command)

    def test_chmod_and_its_kin_skip_the_mode_or_owner_and_nothing_else(self):
        for command in ("chmod 755 docs/x.md", "chmod -R u+x docs", "chmod -x docs/x.md", "chmod -v -w docs/x.md", "chmod u+x,g-w tests/b.txt docs/x.md",
                        "chmod +a 'admin allow write' docs/x.md", "chmod =a# 1 'admin allow write' docs/x.md", "chmod -a# 1 docs/x.md",
                        "chmod -N docs/x.md", "chown -R nobody:staff docs", "chown :staff docs/x.md", "chgrp -h staff docs/x.md",
                        "chflags -R nouchg docs", "chmod --reference=tests/b.txt docs/x.md"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables")
        for command in ("chmod 755 tests/b.txt", "chmod +a 'admin allow write' tests/b.txt", "chmod -a# 1 tests/b.txt", "chown nobody tests/b.txt"):
            with self.subTest(command):
                self.assertSilent(command)

    def test_the_options_end_where_this_macs_getopt_ends_them(self):
        for command, path in (("rm -- docs/x.md", "docs/x.md"), ("cp -- tests/src.txt docs/x.md", "docs/x.md"),
                              ("mkdir -- docs/new", "docs/new"), ("touch -- -x", "-x"),  # -x at the home's root
                              ("touch tests/b.txt -c", "-c"),  # BSD: an option after an operand is a file (probed: ./-c)
                              ("install -d tests/out/d -m 700", "-m"),  # probed: made ./-m and ./700
                              ("rm -f -- tests/b.txt docs/x.md", "docs/x.md")):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables")
                self.assertIn(path, r.reason)
        for command in ("rm -rf -- tests/out", "mkdir -m 700 -p tests/out/d", "touch -r docs/x.md tests/b.txt", "touch -t 202609170000 tests/b.txt",
                        "truncate -r docs/x.md tests/b.txt", "install -m 755 -o nobody tests/src.txt tests/out/x"):
            with self.subTest(command):
                self.assertSilent(command)

    def test_install_writes_its_metalog_and_backup_and_install_d_every_operand(self):
        for command, path in (("install -M docs/log tests/src.txt tests/out/x", "docs/log"),
                              ("install -d tests/out/a docs/b", "docs/b"),
                              ("install -b tests/src.txt bin/spud", "bin/spud.old"),
                              ("install -b -B .orig tests/src.txt bin/spud", "bin/spud.orig")):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables")
                self.assertIn(path, r.reason)
        self.assertSilent("install -b tests/src.txt tests/out/x")

    def test_behind_every_wrapper_and_wherever_tee_is_read(self):
        for command in ("env cp tests/src.txt docs/x.md", "command cp tests/src.txt docs/x.md", "sudo cp tests/src.txt docs/x.md",
                        "sudo -u root rm docs/x.md", "env A=1 touch docs/x.md", "ENV touch docs/x.md", "nohup rm docs/x.md &",
                        "time mkdir docs/new", "nice -n 5 chmod +x docs/x.md", "xargs rm docs/x.md < /dev/null", "exec touch docs/x.md",
                        "timeout 5 sed -i '' s/a/b/ docs/x.md", "command -p mv docs/x.md tests/out/", "noglob rm docs/x.md",
                        "echo x | sed -i '' s/a/b/ docs/x.md", "true && ln -s x docs/y", "false || touch docs/x.md",
                        "(cd tests && cp src.txt ../docs/x.md)", "{ touch docs/x.md; }", "eval 'rm docs/x.md'", "sh -c 'mv tests/src.txt docs/'",
                        "bash -lc 'touch docs/x.md'", "f() { touch docs/x.md; }; f", "echo $(touch docs/x.md)", "bash <<'EOF'\nrm docs/x.md\nEOF",
                        "if true; then mkdir docs/new; fi", "while false; do rm docs/x.md; done", "cd docs && touch x.md", "cd docs; rm -rf ."):
            with self.subTest(command):
                self.assertRefused(command, "deliverables")

    def test_a_glob_command_word_is_read_as_each_command_it_can_be(self):
        (self.home.path / "cp").write_text("", encoding="utf-8")  # so `c?` matches a file here, as zsh would expand it
        for command in ("c? tests/src.txt docs/x.md", "/bin/c? tests/src.txt docs/x.md", "/bin/[c]p tests/src.txt docs/x.md"):
            with self.subTest(command):
                r = self.bash(command)
                self.assertEqual((r.code, r.decision), (0, "deny"), (command, r))

    def test_a_target_the_hook_cannot_resolve_is_refused(self):
        for command in ("cp tests/src.txt $D", "rm \"$F\"", "touch $(date).log", "mkdir -p \"$D\"/x", "sed -i '' s/a/b/ $F",
                        "for f in $X; do touch tests/$f; done", "ln -s /tmp/x `pwd`/x", "cp -t \"$D\" tests/src.txt",
                        "cp \"$SRC\" tests/out/", "mv \"$SRC\" tests/out/"):  # the name a source takes inside a directory
            with self.subTest(command):
                self.assertRefused(command, VARIABLE_WORDING)
                self.assertRefused(command, VARIABLE_WORDING, agent_id=None)  # Spud too, as for a redirection (SPD-091)
        # Spud's own settled value is read, as a member's is below: outside every project it passes, in one Law 1 holds
        self.assertSilent("S=/tmp/spd-121-x; mkdir -p $S/y && rm -rf $S", agent_id=None)
        self.assertRefused("S=docs; touch $S/x.md", "Law 1", agent_id=None)
        for command in ("cp \"$SRC\" tests/out.txt", "cp \"$D\"/src.txt tests/out/", "sed -n \"$N\"p docs/x.md", "cat \"$F\""):
            with self.subTest(command):
                self.assertSilent(command)
        # The line's own assignment is resolved, as it is for every other word the hook reads by name: a member writes into
        # its scratchpad through a variable it set on the line, and the hook reads the path it set.
        self.assertSilent("F=tests/b.txt; rm \"$F\"")
        self.assertSilent("S=/tmp/spd-121-x; mkdir -p $S/y && rm -rf $S")
        r = self.assertRefused("S=docs; touch $S/x.md", "deliverables")
        self.assertIn("docs/x.md", r.reason)
        self.assertRefused("D=tests/fake/.git/hooks; cp tests/src.txt $D/post-index-change", GIT_DIR_WORDING)
        # A word where this Mac's getopt still reads an option, holding an expansion, is read by name (SPD-043): for sed and
        # install, whose options change what they write, `sed $X` may be `sed -i`.
        self.assertRefused("X=-i; sed $X '' s/a/b/ docs/x.md", "deliverables")  # resolved, and then in place
        for command in ("sed -$X '' s/a/b/ docs/x.md", "cp -$X tests/src.txt docs/"):
            with self.subTest(command):
                self.assertRefused(command, WORD_WORDING)  # a word spelled with `-` is an option the hook must read
        # A first operand the line cannot settle is read both ways instead, and each reading's files are checked: what a
        # `sed -n "${n},$((n+3))p" f` only prints stays silent, while the files an in-place reading would write do not.
        for command in ("sed $(printf -- -i) '' s/a/b/ docs/x.md", "sed \"$X\" -e p tests/b.txt docs/x.md",
                        "install $(echo -d) tests/out/a docs/b"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables")
        for command in ("sed -n \"$N\"p docs/x.md", "sed -n \"${A},$((A+3))p\" docs/x.md", "for n in 1 2; do sed -n \"${n}p\" docs/x.md; done"):
            with self.subTest(command):
                self.assertSilent(command)

    def test_a_glob_target_is_read_as_a_redirections(self):
        (self.home.path / "tests" / "fake" / ".git" / "hooks" / "pre-commit").write_text("", encoding="utf-8")
        self.assertRefused("rm tests/fake/.git/hooks/*", GIT_DIR_WORDING)
        self.assertRefused("rm tests/fake/.gi?/hooks/pre-commit", GIT_DIR_WORDING)
        self.assertRefused("rm docs/*.md", "deliverables")
        self.assertRefused("rm tests/*.nomatch", "matches no file now")  # a member: what the shell would open is unknown
        self.assertSilent("rm /tmp/spd-121-none/*.nomatch", agent_id=None)  # Spud: the literal name is checked, outside every project
        self.assertSilent("rm tests/*.txt")
        self.assertSilent("rm -f tests/{src,b}.txt")

    def test_spuds_answers(self):
        """Spud is held to Law 1 in a project and is free in his own files and outside every project, as for a redirection."""
        for label, form in ARG_WRITERS:
            with self.subTest(label):
                r = self.assertRefused(form.format("docs/x.md"), "Law 1", agent_id=None)
                self.assertIn(ARG_WORDING, r.reason)
                self.assertRefused(form.format("ledger/tickets/SPD-001.md"), "generated", agent_id=None)
                self.assertSilent(form.format("/Users/Nobody/.zshrc"), agent_id=None)
                self.assertSilent(form.format("/tmp/spd-121-x"), agent_id=None)
                self.assertSilent(form.format(".claude/settings.json"), agent_id=None)  # his own, the backup under .claude/** too
        r = self.assertRefused("cp tests/src.txt tests/fake/.git/hooks/post-index-change", "Law 1", agent_id=None)
        self.assertNotIn(GIT_DIR_WORDING, r.reason)  # SPD-066's rule is a caller's with an agent_id

    def test_an_unbound_agent_id_is_held_as_for_a_redirection(self):
        self.assertRefused("cp tests/src.txt /Users/Nobody/.zshrc", OUTSIDE, agent_id=AGENT_D)
        self.assertRefused("touch tests/fake/.git/hooks/x", GIT_DIR_WORDING, agent_id=AGENT_D)
        self.assertRefused("touch tests/x.py", "not bound", agent_id=AGENT_D)
        self.assertSilent("touch %s/probe.txt" % self.scratchpad, agent_id=AGENT_D)


AGENT_E = "b1c2d3e4f5a6b7c8d"  # SPD-129: the member holding the differential's four mkdir globs
AGENT_F = "c9d8e7f6a5b4c3d2e"  # SPD-129: the member holding dist/**, the differential's `rm -rf dist`
AGENT_FIXTURES = "a7b8c9d0e1f2a3b4c"  # SPD-137: the member holding tests/fixtures/**, the ticket's `install -d -m 000 tests`


class DirectoryWriteTest(BashHookCase):
    """SPD-129: a member is refused the directory its own deliverable glob covers.

    path_matches_glob reads `X/**` as `X/` plus something, so a member whose glob is `test/fixtures/movecheck/**` may write
    every file under that directory and was refused the `mkdir -p` that makes it (Law 5).  Before SPD-121 a mkdir was
    unread and the question never arose; now it is the natural first line of a member's work, and SPD-121's differential
    over the 3477 commands spudagents ran holds five refusals of exactly this shape, each pinned below:
    `mkdir -p test/fixtures/movecheck` with `test/fixtures/movecheck/**`, `mkdir -p admin/src/lib/actions`,
    `mkdir -p test/helpers`, `mkdir -p docs/superpowers/plans` (the three with the obvious `<dir>/**`), and `rm -rf dist`
    with `dist/**`.

    Spud's decision is two readings, one per direction, and they are not symmetrical.  A write that only MAKES a directory
    -- mkdir's operands, install -d's, and nothing else -- is inside when the path is a glob's literal directory prefix or
    an ancestor of it, since an empty directory writes no content.  A write that REMOVES one -- rmdir's operands, and rm's
    when -r, -R or -d is among its options -- is inside only when one glob covers the whole subtree (`D/**` or `D/`) and the
    path is exactly D; an ancestor is never inside, so `rm -rf test` with `test/fixtures/movecheck/**` stays Law 5.  Every
    other write of the same path -- touch, cp, mv, ln, tee, sed -i, a redirection, a Write or an Edit -- keeps today's
    reading, and the refusals that run before the globs (a .git component, the generated roots, the state directory, the
    outside allowlist, SPD-098's bound worktree) win as they did."""

    GLOBS = ("home:test/fixtures/movecheck/**", "home:admin/src/lib/actions/**", "home:test/helpers/**",
             "home:docs/superpowers/plans/**", "home:admin/src/*.ts")

    def setUp(self):
        super().setUp()
        # Children of the lead: the ticket's root fan-out is spent on BashHookCase's two engineers.
        self.dirs = self.spawn(self.plan(actor=self.lead["ref"], persona="engineer", model="opus", deliverable=list(self.GLOBS)),
                               AGENT_E, caller=AGENT_A)
        self.dist = self.spawn(self.plan(actor=self.lead["ref"], persona="engineer", model="opus", deliverable=["home:dist/**"]),
                               AGENT_F, caller=AGENT_A)
        home = self.home.path
        for d in ("tests", "docs", "ledger/tickets", "test/fixtures/movecheck"):  # `dist` is left unmade on purpose
            (home / d).mkdir(parents=True, exist_ok=True)
        for f in ("docs/x.md", "ledger/tickets/SPD-001.md"):
            (home / f).write_text("a\n", encoding="utf-8")

    def edit(self, path, agent_id, tool="Write"):
        return self.home.hook("PreToolUse", self.pre_edit(path, agent_id=agent_id, tool=tool))

    # -- the differential's five lines -------------------------------------------------
    def test_the_differentials_five_lines_are_silent_for_the_member_that_owns_them(self):
        for command in ("mkdir -p test/fixtures/movecheck", "mkdir -p admin/src/lib/actions", "mkdir -p test/helpers",
                        "mkdir -p docs/superpowers/plans"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_E)
        self.assertSilent("rm -rf dist", agent_id=AGENT_F)

    def test_the_same_lines_stay_refused_for_a_member_without_the_glob(self):
        """The reading is the member's own globs, not a licence: AGENT_A holds tests/** and bin/spud."""
        for command, path in (("mkdir -p test/fixtures/movecheck", "test/fixtures/movecheck"),
                              ("mkdir -p admin/src/lib/actions", "admin/src/lib/actions"),
                              ("mkdir -p test/helpers", "test/helpers"), ("mkdir -p docs/superpowers/plans", "docs/superpowers/plans"),
                              ("rm -rf dist", "dist"), ("rmdir dist", "dist")):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables")
                self.assertIn(path, r.reason)

    # -- making a directory ------------------------------------------------------------
    def test_every_ancestor_of_a_globs_literal_prefix_may_be_made(self):
        for command in ("mkdir -p test/fixtures/movecheck", "mkdir -p test/fixtures", "mkdir test", "mkdir -p admin/src/lib/actions",
                        "mkdir -p admin/src/lib", "mkdir -p admin/src", "mkdir admin", "mkdir -p docs/superpowers",
                        "install -d test/helpers", "install -d test/fixtures admin/src", "mkdir -m 700 -p test/helpers"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_E)
        self.assertSilent("mkdir bin", agent_id=AGENT_A)  # the directory of the file glob `home:bin/spud`
        self.assertSilent("mkdir tests", agent_id=AGENT_A)

    def test_a_directory_no_glob_prefixes_is_still_refused(self):
        for command, path in (("mkdir -p test/fixtures/other", "test/fixtures/other"), ("mkdir -p admin/src/lib/other", "admin/src/lib/other"),
                              ("mkdir -p admin/other", "admin/other"), ("install -d docs/x", "docs/x"),
                              ("mkdir -p test/helpers admin/other", "admin/other")):  # the first operand is the member's, the second is not
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables", agent_id=AGENT_E)
                self.assertIn(path, r.reason)
        r = self.assertRefused("mkdir -p bin/other", "deliverables", agent_id=AGENT_A)  # `home:bin/spud` gives bin, and nothing under it
        self.assertIn("bin/other", r.reason)

    def test_install_d_setting_a_mode_owner_or_group_is_a_file_write(self):
        """SPD-137: this Mac's BSD install -d applies -m, -o and -g to a directory that already exists (probed: a 755
        directory left 700 by `install -d -m 700`, where `mkdir -m 700` says File exists), so with any of them among its
        options install -d is a metadata write of each operand, read as a file, and never a make at a glob's prefix."""
        for command in ("install -d test", "install -dv test/fixtures", "install -d -v -p admin/src", "install -d -- test",
                        "install -dpv test/fixtures/movecheck"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_E)
        for command, path in (("install -d -m 000 test", "test"), ("install -d -m000 test", "test"),
                              ("install -dm 000 test", "test"), ("install -dm000 test", "test"),
                              ("install -dvm 000 test", "test"), ("install -m 000 -d test", "test"),
                              ("install -m 000 -dv test/fixtures", "test/fixtures"),
                              ("install -d -o nobody test", "test"), ("install -d -onobody test", "test"),
                              ("install -do nobody test/fixtures", "test/fixtures"),
                              ("install -d -g staff admin", "admin"), ("install -dg staff admin/src", "admin/src"),
                              ("install -d -gstaff admin/src/lib", "admin/src/lib"),
                              ("install -d --mode=000 test", "test"), ("install -d --mode 000 test", "test"),
                              ("install -d --owner=nobody test", "test"), ("install -d --owner nobody test", "test"),
                              ("install -d --group=staff test", "test"), ("install -d --group staff test", "test"),
                              ("install -d --mo=000 test", "test"), ("install -d --own=nobody test", "test"),
                              ("install -d --gr=staff test", "test"),
                              ("install -d -m 700 test/helpers test", "test"),
                              # the glob's own directory too: its mode is no file `test/fixtures/movecheck/**` matches
                              ("install -d -m 700 test/fixtures/movecheck", "test/fixtures/movecheck")):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables", agent_id=AGENT_E)
                self.assertIn(path, r.reason)
        # A first operand the line cannot settle may be `-dm000` as much as `-d`, so what follows it is read as a file.
        r = self.assertRefused("install $(printf -- -dm000) test", "deliverables", agent_id=AGENT_E)
        self.assertIn("test", r.reason)
        # The ticket's own line: a member holding tests/fixtures/** may make tests, and may not chmod it.  The dist member
        # is done here, which frees the lead's second slot for it.
        self.home.json("member", "finish", self.dist["ref"], "--status", "done", "--outcome", "Accepted.", actor=AGENT_A)
        fixtures = self.spawn(self.plan(actor=self.lead["ref"], persona="engineer", model="opus",
                                        deliverable=["home:tests/fixtures/**"]), AGENT_FIXTURES, caller=AGENT_A)
        self.assertTrue(fixtures)
        self.assertSilent("install -d tests", agent_id=AGENT_FIXTURES)
        for command in ("install -d -m 000 tests", "install -d -o nobody tests", "install -d -g staff tests"):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables", agent_id=AGENT_FIXTURES)
                self.assertIn("tests", r.reason)

    # -- removing a directory ----------------------------------------------------------
    def test_a_wholly_covered_directory_may_be_removed_in_every_spelling(self):
        for command in ("rm -rf dist", "rm -r dist", "rm -R dist/", "rm -d dist", "rmdir dist", "rm -fr -- dist",
                        "rm -rf dist/sub", "rmdir dist/sub"):  # under it is content one glob already matched
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_F)

    def test_an_ancestor_is_never_removed_and_a_partial_glob_covers_nothing(self):
        for command, path in (("rm -rf test", "test"), ("rm -rf test/fixtures", "test/fixtures"),
                              ("rm -rf admin/src", "admin/src"),  # admin/src/*.ts covers part of it, never the subtree
                              ("rm -rf admin", "admin"), ("rmdir test/fixtures", "test/fixtures"),
                              ("rm -rf docs/superpowers", "docs/superpowers")):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables", agent_id=AGENT_E)
                self.assertIn(path, r.reason)
        self.assertRefused("rm -rf .", "deliverables", agent_id=AGENT_F)  # the checkout's root is no glob's directory

    def test_rm_without_r_R_or_d_is_a_file_and_keeps_todays_answer(self):
        for command in ("rm dist", "rm -f dist", "rm -v dist", "unlink dist"):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables", agent_id=AGENT_F)
                self.assertIn("dist", r.reason)

    def test_every_other_write_of_the_same_path_keeps_todays_reading(self):
        """The same path written any other way is a file: `dist` does not exist here, so nothing but the reading decides."""
        for command in ("touch dist", "cp docs/x.md dist", "mv dist /tmp/spd-129-x", "ln -s /tmp/x dist", "echo x > dist",
                        "echo x | tee dist", "sed -i '' s/a/b/ dist", "truncate -s 0 dist", "chmod 755 dist",
                        "install docs/x.md dist", "chflags nohidden dist"):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables", agent_id=AGENT_F)
                self.assertIn("dist", r.reason)
        for tool in ("Write", "Edit", "MultiEdit"):
            with self.subTest(tool=tool):
                r = self.edit(self.home.path / "dist", AGENT_F, tool=tool)
                self.assertEqual((r.code, r.decision), (0, "deny"), r)
        r = self.edit(self.home.path / "test" / "fixtures" / "movecheck", AGENT_E)
        self.assertEqual((r.code, r.decision), (0, "deny"), r)  # the Write hook keeps its own reading of the directory

    def test_the_removal_reading_never_asks_the_filesystem(self):
        """`dist` does not exist and is removed all the same: the reading is the glob's, as path_matches_glob's is, so a
        member that has not made its directory yet is answered the same as one that has (and the hook stats nothing)."""
        self.assertFalse((self.home.path / "dist").exists())
        self.assertSilent("rm -rf dist", agent_id=AGENT_F)
        (self.home.path / "dist").mkdir()
        self.assertSilent("rm -rf dist", agent_id=AGENT_F)

    # -- what runs before the globs ----------------------------------------------------
    def test_the_refusals_before_the_globs_still_win(self):
        self.spawn(self.plan(actor=self.other["ref"], persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C, caller=AGENT_B)
        for command, needle in (("mkdir -p .git/hooks", GIT_DIR_WORDING), ("mkdir -p tests/fake/.git/hooks", GIT_DIR_WORDING),
                                ("rm -rf .git", GIT_DIR_WORDING),
                                ("mkdir -p .spud/x", "ledger database"),  # the line names .spud: refused before the words are read
                                ("rm -rf .spud", "ledger database"), ("mkdir -p ledger/x", "generated"),
                                ("rm -rf reports", "generated"), ("mkdir -p /Users/Nobody/x", OUTSIDE)):
            with self.subTest(command):
                self.assertRefused(command, needle, agent_id=AGENT_C)
        self.assertRefused("mkdir -p ledger/x", "generated", agent_id=AGENT_E)

    def test_a_target_the_hook_cannot_resolve_is_still_refused(self):
        for command in ("mkdir -p $D", "rm -rf \"$D\"", "rmdir $(pwd)/x"):
            with self.subTest(command):
                self.assertRefused(command, VARIABLE_WORDING, agent_id=AGENT_E)
        self.assertSilent("D=test/helpers; mkdir -p $D", agent_id=AGENT_E)

    def test_spud_and_an_unbound_agent_id_are_unchanged(self):
        for command in ("mkdir -p test/helpers", "rm -rf dist", "rmdir dist"):
            with self.subTest(command):
                self.assertRefused(command, "Law 1", agent_id=None)  # Spud's own reading: no member, no globs
                self.assertRefused(command, "not bound", agent_id=AGENT_D)


AGENT_G = "d1e2f3a4b5c6d7e8f"  # SPD-126: out/**, a partial bin/* and a partial docs/*.md
AGENT_H = "e2f3a4b5c6d7e8f9a"  # SPD-126: bin/** and vendor/**, whole subtrees
INPUT_WORDING = "xargs reads from its input"  # SPD-126: a write whose file the line does not spell
ANYWHERE_WORDING = "places files where the line cannot say"  # SPD-126: a command that may write anywhere
CHECKOUT_WORDING = "the root of a checkout the ledger knows"  # SPD-126: a whole-subtree write at or above a checkout
UNWALKED_WORDING = "could not read whole"  # SPD-126: a copied tree the walk for a git directory could not finish


class TreeWriteCase(BashHookCase):
    """SPD-126's scratch world: AGENT_A plans home:tests/** and home:bin/spud (BashHookCase's), AGENT_G home:out/**, home:bin/*
    and home:docs/*.md, AGENT_H home:bin/** and home:vendor/**.  out/, bin/sub/, vendor/plain/ and vendor/repo/ (which holds a
    git directory, as a copied checkout does) exist, tests/fake/.git is a nested repository, and bin/link is a symlink to
    bin/sub.  The home is the checkout root the Bash hook runs in."""

    def setUp(self):
        super().setUp()
        # Children of the lead: the ticket's root fan-out is spent on BashHookCase's two engineers.
        self.partial = self.spawn(self.plan(actor=self.lead["ref"], persona="engineer", model="opus",
                                            deliverable=["home:out/**", "home:bin/*", "home:docs/*.md"]), AGENT_G, caller=AGENT_A)
        self.whole = self.spawn(self.plan(actor=self.lead["ref"], persona="engineer", model="opus",
                                          deliverable=["home:bin/**", "home:vendor/**"]), AGENT_H, caller=AGENT_A)
        home = self.home.path
        for d in ("out/tmp", "bin/sub", "vendor/plain/sub", "vendor/repo/src", "tests/out", "docs", "ledger/tickets"):
            (home / d).mkdir(parents=True, exist_ok=True)
        for f in ("out/tmp/a.pyc", "out/keep.txt", "bin/sub/x.py", "bin/x.py", "vendor/plain/a.txt", "vendor/plain/sub/b.txt",
                  "vendor/repo/src/c.py", "docs/x.md", "ledger/tickets/SPD-001.md", "a.tar", "a.zip", "x.patch", "list"):
            (home / f).write_text("a\n", encoding="utf-8")
        plant_git_dir(home / "vendor" / "repo" / ".git")
        plant_git_dir(home / "tests" / "fake" / ".git")
        (home / "bin" / "link").symlink_to(home / "bin" / "sub")
        self.module = load_spud_module()

    def analysis(self, command, cwd=None):
        m = self.module
        return m.analyse_command(command, m.ShellAnalysis(cwd=cwd or self.cwd, home=str(self.home.path)))

    def writes(self, command, cwd=None):
        """(the word, the kind) of each write by argument the line records, in order, the walks find adds left out."""
        return [(w[1], w[6]) for w in self.analysis(command, cwd).arg_writes if w[4] != "walk"]

    def wide(self):
        """A member holding home:**, whose globs let in every whole subtree: only the refusals before the globs stop it."""
        return self.spawn(self.plan(actor=self.other["ref"], persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C,
                          caller=AGENT_B)


class FindWriteTest(TreeWriteCase):
    """SPD-126: find's writes were never read.  With cwd /Users/X/repo, main (08c344e) recorded no finding for `find .
    -exec git push \\;` -- a member's push past Law 7, since find's -exec utility was never a command the analysis read --
    and no write for `find tests/tmp -name '*.pyc' -delete` or `find . -name x -exec rm {} \\;`.

    Read now on find(1) (this Mac's BSD find) and bfs, which Claude Code's shadow of `find` runs in the Bash tool (its
    --help: flags, paths and expression "may be freely mixed in any order"): every word that is no flag, primary, operator
    or primary argument is a starting point, `.` when there is none; -delete (and bfs's -rm) removes every starting point's
    subtree; the utility of -exec, -execdir, -ok and -okdir is analysed as a command with `{}` standing for a path under
    the starting points, and a write of such a path is a write anywhere under each starting point, whose tree is walked for
    a git directory; -execdir runs where the hook cannot know; -fprint and its kin write the file they name.  A whole-subtree
    write is inside a member's deliverables only where one glob covers everything under the directory (`D/**`), never at
    or above a checkout's root.  The differential over the 1167 commands spudagents ran that name find, xargs, rm, cp, mv,
    rsync, ditto, tar, unzip, patch or curl is in the ticket's result."""

    def test_the_analysis_records_what_find_writes(self):
        cwd = frozenset([self.cwd])
        for command, writes in (
            ("find out/tmp -name '*.pyc' -delete", [("out/tmp", "rm-tree")]),
            ("find . -name x -exec rm {} \\;", [(".", "rm-tree")]),
            ("find -name x -delete", [(".", "rm-tree")]),  # no starting point: `.`, as bfs reads it
            ("find -name x out -delete", [("out", "rm-tree")]),  # bfs: a path anywhere
            ("find out -type f -exec sed -i '' s/a/b/ {} +", [("out", "find-tree")]),
            ("find out -execdir rm {} \\;", [("out", "rm-tree")]),
            ("find out -exec mv {} {}.bak \\;", [("out", "rm-tree"), ("out", "find-tree")]),
            ("find out -fprint out/list.txt", [("out/list.txt", None)]),
            ("find out -fprintf out/list.txt '%p\\n'", [("out/list.txt", None)]),
            ("find -L out -delete", [("out", "tree")]),  # a starting point that is a link is traversed
            ("find out -name x -print", []),
            ("find out -exec grep -l x {} +", []),
        ):
            with self.subTest(command):
                self.assertEqual(self.writes(command), writes)
                self.assertTrue(all(w[2] == cwd for w in self.analysis(command).arg_writes))

    def test_find_past_an_escaped_operator_is_still_find(self):
        """A quoted or escaped `;`, `(` or `)` is a character of its word, not the operator: until SPD-126 the walk ended
        find at `\\;` and read `-delete` as a command of its own, and `\\(` opened a subshell."""
        for command, writes in (("find out -exec true \\; -delete", [("out", "rm-tree")]),
                                ("find out \\( -name a -o -name b \\) -delete", [("out", "rm-tree")]),
                                ("find out -exec true ';' -exec rm {} ';'", [("out", "rm-tree")]),
                                ("find out -name '(' -delete", [("out", "rm-tree")])):
            with self.subTest(command):
                self.assertEqual(self.writes(command), writes)
        a = self.analysis("find out -exec true \\; -exec git push \\;")
        self.assertIn(("git", ("push", "push")), a.findings)
        # the same holds outside find: an escaped operator is an argument, as the shell passes it
        self.assertEqual(self.analysis("echo foo \\; git push").findings, [])
        self.assertEqual(self.analysis("echo \\> docs/x.md").redirects, [])

    def test_the_exec_utility_is_read_as_a_command(self):
        for command, needle in (("find . -exec git push \\;", "Law 7"), ("find out -exec git commit -m x {} +", "Law 7"),
                                ("find out -ok git push \\;", "Law 7"), ("find out -execdir git push \\;", "Law 7"),
                                ("find out -exec sh -c 'git push' \\;", "Law 7"), ("find out -exec env git push \\;", "Law 7"),
                                ("find out -exec {} \\;", "command word"),  # the program is a file find found
                                ("find out -exec find . -exec git push \\; \\;", "Law 7")):
            with self.subTest(command):
                for agent in (AGENT_A, AGENT_G):
                    self.assertRefused(command, needle, agent_id=agent)

    def test_a_member_removes_and_writes_under_its_own_whole_subtree(self):
        for command in ("find out/tmp -name '*.pyc' -delete", "find out -exec rm {} \\;", "find out -name '*.pyc' -exec rm -f {} +",
                        "find out -type f -exec sed -i '' s/a/b/ {} +", "find out -exec touch {} \\;", "find out -rm",
                        "find -name x out -delete", "find out -exec sh -c 'rm {}' \\;", "find out -fprint out/list.txt",
                        "cd out && find . -delete", "find out -exec cp {} out/copy/ \\;", "find out -name x -print",
                        "find /tmp/spd-126-x -delete"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_G)

    def test_a_partial_glob_the_checkout_root_and_other_paths_are_refused(self):
        for command, needle, path in (("find bin -delete", "deliverables", "bin"),  # bin/* covers bin's files, not its tree
                                      ("find bin/sub -exec rm {} \\;", "deliverables", "bin/sub"),
                                      ("find docs -name '*.md' -exec sed -i '' s/a/b/ {} +", "deliverables", "docs"),
                                      ("find . -name '*.pyc' -delete", "deliverables", "home:"),  # the root: no glob of its covers it
                                      ("find out ledger -delete", "generated", "ledger"),
                                      ("find out -exec cp {} docs/ \\;", "deliverables", "docs"),
                                      ("find out -fprint docs/list.txt", "deliverables", "docs/list.txt"),
                                      ("find %s -delete" % os.path.dirname(self.home.path), CHECKOUT_WORDING, str(self.home.path))):
            with self.subTest(command):
                r = self.assertRefused(command, needle, agent_id=AGENT_G)
                self.assertIn(path, r.reason)

    def test_a_checkout_root_is_refused_whatever_the_globs(self):
        """home:** covers every whole subtree of the home, but the home's root holds its state directory and rendered roots,
        and a checkout's root its .git: SPD-066's rule refuses the write before the globs are asked."""
        self.wide()
        for command in ("find . -name '*.pyc' -delete", "find -name '*.pyc' -delete", "find . -exec touch {} +", "rm -rf .",
                        "cd out && find .. -delete", "find %s -delete" % os.path.dirname(self.home.path)):
            with self.subTest(command):
                r = self.assertRefused(command, CHECKOUT_WORDING, agent_id=AGENT_C)
                self.assertIn(str(self.home.path), r.reason)
        for command in ("find out -delete", "find ledger -delete"):
            with self.subTest(command):
                r = self.bash(command, AGENT_C)
                self.assertNotIn(CHECKOUT_WORDING, r.reason or "")
        self.assertSilent("find out bin -delete", agent_id=AGENT_C)

    def test_what_find_hands_its_command_is_walked_for_a_git_directory(self):
        """tests/fake holds a nested repository: a command that writes what find finds under tests could write its hooks."""
        for command in ("find tests -name post-index-change -exec cp /tmp/x {} \\;", "find tests -exec touch {} \\;",
                        "find tests -exec cp -R {} tests/out/ \\;"):
            with self.subTest(command):
                self.assertRefused(command, GIT_DIR_WORDING, agent_id=AGENT_A)
        self.assertSilent("find tests -name '*.pyc' -delete", agent_id=AGENT_A)  # a removal under the member's own tree
        self.assertSilent("find tests/out -exec touch {} \\;", agent_id=AGENT_A)

    def test_what_the_hook_cannot_place_is_refused(self):
        for command, needle in (("find out -execdir touch x \\;", "cannot follow"),  # -execdir: the found file's directory
                                ("find out -exec sh -c 'rm \"$1\"' _ {} \\;", VARIABLE_WORDING),
                                ("find out $(printf -- -delete)", WORD_WORDING),  # a substitution where a primary stands
                                ("for p in -delete; do find out $p; done", WORD_WORDING),
                                ("find -files0-from list -delete", INPUT_WORDING)):
            with self.subTest(command):
                self.assertRefused(command, needle, agent_id=AGENT_G)
        self.assertSilent("find $HOME -name x -print", agent_id=AGENT_G)  # the environment's value, and nothing written
        # a loop's variable is the member's, unless every word of its list starts with something other than `-` (the
        # differential's `for d in <scratchpads>/*/; do find "$d" -maxdepth 3 ...; done`)
        self.assertSilent("for d in /tmp/*/ out; do find \"$d\" -maxdepth 3 -name x; done", agent_id=AGENT_G)
        for command in ("for d in /tmp/x -delete; do find out $d; done", "for d in $X; do find out $d; done",
                        "for d in /tmp/x; do d=-delete; find out $d; done"):
            with self.subTest(command):
                self.assertRefused(command, WORD_WORDING, agent_id=AGENT_G)

    def test_spuds_answers(self):
        self.assertRefused("find out -delete", "Law 1", agent_id=None)
        self.assertRefused("find ledger -delete", "generated", agent_id=None)
        self.assertRefused("find . -exec git push \\;", "Law 7", agent_id=AGENT_D)  # an unbound agent_id is held too
        for command in ("find /tmp/spd-126-x -delete", "find . -exec git push \\;", "find out -name x -print"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=None)


class XargsInputTest(TreeWriteCase):
    """SPD-126: xargs was read as a wrapper and its utility dispatched on the words the line spells, so `xargs rm < list`
    read as an `rm` with no operand and wrote nothing (main 08c344e: arg_writes=[]).  xargs(1) appends what it reads after
    those words, or puts it where -J's replstr stands, or into every argument (not the utility) holding -I's; the hook
    cannot know it.  A write whose file holds it is refused to a member as an unresolvable target is; a destination the
    line names that only such operands land in (`xargs -J % cp % out/`) is a whole-subtree write of it; a command word
    holding it is a program the input names.  Spud is not held to a target the hook cannot resolve."""

    def test_the_analysis_marks_what_xargs_reads(self):
        m = self.module
        # appended twice: one input may be several operands, and the first may be -r, so rm is read as rm -r
        self.assertEqual(self.writes("xargs rm < list"), [(m.INPUT_OPERAND, "rm-tree")] * 2)
        self.assertEqual(self.writes("xargs -J % cp % out/"), [("out/", ("recursive", None, True))])
        self.assertEqual(self.writes("xargs -I{} cp {} out/{}.bak"), [("out/" + m.INPUT_OPERAND + ".bak", ("recursive", None, True))])
        self.assertEqual(self.writes("xargs grep -l x"), [])

    def test_a_write_xargs_names_from_its_input_is_refused(self):
        for command in ("xargs rm < list", "cat list | xargs rm -f", "ls | grep -v '^keep' | xargs rm -f", "xargs -0 rm -rf < list",
                        "xargs touch < list", "xargs chmod < list", "xargs -n1 sed -i '' s/a/b/ < list", "xargs mv -t out < list",
                        "xargs -I{} cp {} out/{}.bak < list", "xargs -I % sh -c 'rm %' < list", "xargs -J % mv % out/ < list",
                        "xargs sudo rm < list", "xargs -J % rm -f % out/x < list", "xargs find -delete < list"):
            with self.subTest(command):
                self.assertRefused(command, INPUT_WORDING if "find" not in command else WORD_WORDING, agent_id=AGENT_G)

    def test_a_destination_the_line_names_is_a_whole_subtree(self):
        for command in ("xargs -J % cp % out/ < list", "xargs -J % cp -R % out/copy < list", "xargs -J % ln -s % out/ < list",
                        "xargs -J % cp % out/%.bak < list"):  # -J replaces a word that is the replstr alone (xargs(1))
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_G)
        for command, path in (("xargs -J % cp % docs/ < list", "docs"), ("xargs -J % cp % bin/ < list", "bin")):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables", agent_id=AGENT_G)
                self.assertIn(path, r.reason)
        self.assertSilent("xargs -J % cp % bin/ < list", agent_id=AGENT_H)

    def test_every_other_xargs_is_unchanged(self):
        for command in ("xargs grep -l x < list", "find out -print0 | xargs -0 wc -l", "xargs cat < list", "xargs < list",
                        "xargs -I{} grep x {} < list", "xargs -n1 basename < list"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_G)
        self.assertRefused("xargs git push < list", "Law 7", agent_id=AGENT_G)
        self.assertRefused("xargs -J % % x < list", "command word", agent_id=AGENT_G)  # the program is the input
        # SPD-230: git reads what xargs appends as options until a `--` the line spells (test_hooks_git.GitXargsTest)
        self.assertRefused("xargs git archive HEAD < list", "end git's own words with `--`", agent_id=AGENT_G)
        self.assertSilent("xargs git archive HEAD -- < list", agent_id=AGENT_G)

    def test_what_xargs_hands_a_tree_writer_may_move_where_it_writes(self):
        """An input word may be an option: `-C /elsewhere` to tar, `-d /elsewhere` to unzip, `--output-dir` to curl."""
        for command in ("xargs -n1 tar -xf < list", "xargs unzip -o < list", "xargs curl -O < list", "xargs patch -p1 < list",
                        "cd out && xargs curl -sS < list", "xargs -J % tar -xf a.tar % -C out < list"):
            with self.subTest(command):
                self.assertRefused(command, ANYWHERE_WORDING, agent_id=AGENT_G)

    def test_spuds_answers(self):
        for command in ("xargs rm < list", "xargs -J % cp % docs/ < list"):
            with self.subTest(command):
                r = self.bash(command, None)
                self.assertNotIn(INPUT_WORDING, r.reason or "")
        self.assertRefused("xargs -J % cp % docs/ < list", "Law 1", agent_id=None)

    def test_248_what_xargs_hands_sed_tar_or_tee_may_be_their_options_and_files(self):
        """SPD-248 (2) (proposal by SPUD-134/Billie): the words xargs appends may be sed's -i with its script and files,
        tar's mode with -P or -f, or the files tee writes, and the hook read them as none of these: `echo '-i s/a/b/ f' |
        xargs sed` and `echo '-xPf a.tar' | xargs tar` recorded no write, where `echo f | xargs sed -i s/a/b/` did.  Read now
        as SPD-230 read git's: where the input may stand for sed's options every operand may be a file it edits in place,
        where it may stand for tar's mode the archive may land anywhere, and each word it hands tee is a file tee writes.
        Probed 2026-09-24 through tests/probes/shell_probe.py (zsh 5.9 -f -o nobareglobqual and -f, bash 3.2.57, this Mac's
        BSD sed, bsdtar and xargs): `echo "-i '' s/a/b/ f" | xargs sed` edited f, `echo '-i.bak s/a/b/ g' | xargs sed`
        edited g and left g.bak, `echo '-xf a.tar -C out' | xargs tar` extracted into out, and `echo teefile | xargs tee`
        made teefile."""
        m = self.module
        self.assertEqual(self.writes("echo '-i s/a/b/ f' | xargs sed"), [(m.INPUT_OPERAND, None)] * 2)
        self.assertEqual(self.writes("echo '-xPf a.tar' | xargs tar"), [(m.ANY_PATH, "tree")])
        for command in ("echo '-i s/a/b/ f' | xargs sed", "xargs sed < list", "xargs sed -n < list", "xargs sed -e s/a/b/ < list",
                        "xargs -J % sed % out/x < list", "xargs tee < list", "xargs tee -a < list", "xargs -I% tee out/% < list"):
            with self.subTest(command):
                self.assertRefused(command, INPUT_WORDING, agent_id=AGENT_G)
        for command in ("echo '-xPf a.tar' | xargs tar", "xargs tar < list", "xargs bsdtar < list", "xargs -J % tar % < list",
                        "xargs tar -C out < list", "X=$(echo -xPf); tar $X a.tar", "X=$(echo xPf); tar $X a.tar"):
            with self.subTest(command):
                self.assertRefused(command, ANYWHERE_WORDING, agent_id=AGENT_G)
        # a script the line spells first, or options that leave the input no place to be one, read as before
        for command in ("xargs sed s/a/b/ < list", "xargs sed -n 1p < list", "xargs tar -tf < list", "xargs tar -tvf a.tar < list",
                        "tar -tf a.tar", "tar $TAPE_OPTS -tf a.tar", "sed -n p list"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_G)
        for command in ("xargs sed < list", "xargs tar < list", "xargs tee < list"):
            with self.subTest(command):
                r = self.bash(command, None)
                self.assertNotIn(INPUT_WORDING, r.reason or "")
                self.assertNotIn(ANYWHERE_WORDING, r.reason or "")


class RecursiveWriteTest(TreeWriteCase):
    """SPD-126: what a recursive removal, copy or move carries under the directory it names.  Main (08c344e) read `rm -rf
    bin/sub` as SPD-129's "remove", which `bin/*` matches, so the removal of bin/sub/<anything> was allowed; `cp -R src dest`
    checked dest and dest/src and nothing under them, so a source tree holding a `.git` planted one wherever it landed
    (SPD-066: no caller with an agent_id writes a git directory, its own deliverables included); and `rsync -a src/ dest/`
    and `ditto src dst` recorded nothing.

    Read now: rm -r/-R of a directory, or of a path that does not exist, is a whole-subtree removal, and of a file or a
    symlink the removal of that path alone, as before (rm(1): "removes symbolic links, not the files referenced"); chmod,
    chown, chgrp and chflags under -R, and mv of a directory, are that file and a whole subtree too; cp -R (-r, -a) and mv
    land each source that is a directory now as a whole tree, `src/` as its contents (cp(1)), and walk it for a git
    directory, failing closed when the walk reaches syntax.GLOB_SCAN_CAP entries or a directory it cannot list; openrsync
    (rsync(1)) writes its destination whole and lands each source there, honouring a plain `--exclude NAME`; ditto(1)
    merges each source's contents into its destination."""

    def test_the_analysis_records_the_kinds(self):
        for command, writes in (("rm -rf bin/sub", [("bin/sub", "rm-tree")]), ("rm -d bin/sub", [("bin/sub", "remove")]),
                                ("chmod -R u+w bin/sub", [("bin/sub", "file-tree")]), ("chmod u+w bin/sub", [("bin/sub", None)]),
                                ("mv bin/sub vendor/", [("bin/sub", "file-tree"), ("vendor/", ("recursive", None, False))]),
                                ("cp -R vendor/plain bin/", [("bin/", ("recursive", None, True))]),
                                ("cp vendor/plain/a.txt bin/", [("bin/", None)]),
                                ("rsync -a --exclude .git vendor/repo/ bin/r/",
                                 [("bin/r/", "tree"), ("bin/r/", ("recursive", (".git",), True))]),
                                ("ditto vendor/plain bin/d", [("bin/d", "tree"), ("bin/d", ("recursive", (), True))])):
            with self.subTest(command):
                self.assertEqual(self.writes(command), writes)

    def test_a_recursive_removal_needs_a_whole_subtree(self):
        for command in ("rm -rf bin/sub", "rm -r bin/sub", "rm -R bin/sub/", "chmod -R u+w bin/sub", "chown -R nobody bin/sub",
                        "mv bin/sub /tmp/spd-126-x"):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables", agent_id=AGENT_G)  # bin/* matches bin/sub, not what is in it
                self.assertIn("bin/sub", r.reason)
                self.assertSilent(command, agent_id=AGENT_H)
        for command in ("rm -rf bin/x.py", "rm -r bin/link", "chmod -R u+w bin/x.py", "rm -rf out/tmp", "rm -d bin/sub"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_G)  # a file, a symlink rm removes as itself, a tree it covers

    def test_a_copied_git_directory_is_refused_wherever_it_lands(self):
        for command, landing in (("cp -R vendor/repo bin/", "bin/repo/.git"), ("cp -a vendor/repo bin/copy", "bin/copy/.git"),
                                 ("cp -R vendor/repo/ bin/copy", "bin/copy/.git"), ("cp -R vendor bin/", "bin/vendor/repo/.git"),
                                 ("mv vendor/repo bin/moved", "bin/moved/.git"), ("rsync -a vendor/repo/ bin/r/", "bin/r/.git"),
                                 ("rsync -a vendor/repo bin/r", "bin/r/repo/.git"), ("ditto vendor/repo bin/d", "bin/d/.git"),
                                 ("cp -R vendor/repo /tmp/spd-126-x", "/tmp/spd-126-x/.git"), ("cp -R vendor/* bin/", "bin/repo/.git"),
                                 ("rsync -a --exclude .git --include .git vendor/repo/ bin/r/", "bin/r/.git")):
            with self.subTest(command):
                r = self.assertRefused(command, GIT_DIR_WORDING, agent_id=AGENT_H)
                self.assertIn(landing, r.reason)
        for command in ("cp -R vendor/plain bin/", "cp -R vendor/plain/ bin/copy", "rsync -a --exclude .git vendor/repo/ bin/r/",
                        "rsync -a --exclude=.git vendor/repo/ bin/r/", "ditto vendor/plain bin/d", "mv vendor/plain bin/moved",
                        "cp vendor/repo/src/c.py bin/", "rsync -a vendor/plain/ /tmp/spd-126-x/"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_H)

    def test_a_tree_the_walk_cannot_read_whole_is_refused(self):
        locked = self.home.path / "vendor" / "plain" / "locked"
        locked.mkdir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        r = self.assertRefused("cp -R vendor/plain bin/", UNWALKED_WORDING, agent_id=AGENT_H)
        self.assertIn("vendor/plain", r.reason)
        self.assertSilent("cp -R vendor/plain /tmp/spd-126-x", agent_id=None)  # Spud: no walk, and outside every project

    def test_the_walk_stops_at_the_scan_budget(self):
        m = self.module
        root = self.home.path / "vendor" / "plain"
        self.assertEqual(m.first_git_entry(str(root)), (None, False))
        self.assertEqual(m.first_git_entry(str(self.home.path / "vendor")), ("repo/.git", False))
        with mock.patch("spudlib.shell.syntax.GLOB_SCAN_CAP", 2):
            self.assertEqual(m.first_git_entry(str(root)), (None, True))
        self.assertEqual(m.first_git_entry(str(self.home.path / "vendor"), (".git",)), (None, False))  # rsync's --exclude .git
        self.assertEqual(m.first_git_entry(str(self.home.path / "vendor"), ("re*",)), (None, False))
        self.assertEqual(m.first_git_entry(str(self.home.path / "vendor"), (".git/",)), (None, False))  # a directory pattern
        self.assertEqual(m.first_git_entry(str(self.home.path / "vendor"), ("repo/.git",)), ("repo/.git", False))  # not honoured

    def test_rsync_and_ditto_write_their_destination_whole(self):
        for command, path in (("rsync -a vendor/plain/ docs/", "docs"), ("rsync -a vendor/plain bin/", "bin"),
                              ("ditto vendor/plain docs/d", "docs/d"), ("ditto -x -k a.zip docs", "docs")):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables", agent_id=AGENT_G)
                self.assertIn(path, r.reason)
        for command in ("rsync -a vendor/plain/ out/", "rsync -a --delete vendor/plain/ out/p/", "ditto vendor/plain out/d",
                        "ditto -x -k a.zip out", "ditto -c -k vendor/plain out/p.zip", "rsync -a out/ remote:backup/",
                        "rsync -av host:src/ out/"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_G)
        self.assertRefused("rsync -e 'git push #' -a remote:x out/", "Law 7", agent_id=AGENT_G)  # a program rsync runs
        self.assertRefused("rsync --daemon", ANYWHERE_WORDING, agent_id=AGENT_G)
        self.assertRefused("rsync -a --remove-source-files docs/ out/", "deliverables", agent_id=AGENT_G)

    def test_spuds_answers(self):
        self.assertRefused("rm -rf bin/sub", "Law 1", agent_id=None)
        for command in ("cp -R vendor/repo /tmp/spd-126-x", "rsync -a vendor/repo/ /tmp/spd-126-x/", "rm -rf /tmp/spd-126-x"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=None)  # SPD-066's rule and the walk are a caller's with an agent_id


class ExtractionWriteTest(TreeWriteCase):
    """SPD-126: an archive extracted, a patch applied and a download named by the URL or the server write files the line
    does not spell, and main (08c344e) recorded nothing for `tar -xf a.tar -C out`, `unzip a.zip -d out`, `patch -p1 <
    x.patch` or `curl -O url`.  Read now as a whole-subtree write of the directory the files land in: bsdtar's (tar(1))
    x mode, under the directory each -C changes to or the line's own, -O writing none; Info-ZIP unzip's -d exdir, wherever
    it stands on the line, or the line's directory, -l, -t, -p and kin writing none; BSD patch(1) without -o and without a
    file operand (the second SPD-126 engineer's), under -d's directory or the line's; curl's -O, --remote-name-all and -J
    under --output-dir or the line's directory.  What the line cannot place is refused to a member: bsdtar's -P (it keeps
    absolute paths and `..`) and a -T list; unzip's `-:` (it keeps `../`); a config file curl reads (-K, or CURL_HOME,
    XDG_CONFIG_HOME or HOME set on the line); an option a substitution may hold.  wget is not installed on this Mac and is
    left to the second engineer."""

    def test_the_analysis_records_the_directory(self):
        m = self.module
        for command, writes in (("tar -xf a.tar -C out", [("out", "tree")]), ("tar xzf a.tar -C out", [("out", "tree")]),
                                ("tar -x -C out -C sub -f a.tar", [("out/sub", "tree")]), ("tar -xf a.tar -C /tmp/x", [("/tmp/x", "tree")]),
                                ("tar -xf a.tar", [(".", "tree")]), ("tar -tf a.tar", []), ("tar -xOf a.tar", []),
                                ("tar -xPf a.tar -C out", [(m.ANY_PATH, "tree")]),
                                ("tar -cf out/a.tar docs", [("out/a.tar", None)]),  # SPD-126's second engineer: the archive
                                ("unzip -q a.zip -d out", [("out", "tree")]), ("unzip -dout a.zip", [("out", "tree")]),
                                ("unzip a.zip", [(".", "tree")]), ("unzip -l a.zip", []), ("unzip -: a.zip -d out", [(m.ANY_PATH, "tree")]),
                                ("patch -p1 < x.patch", [(".", "tree")]), ("patch -d out -p1 -i x.patch", [("out", "tree")]),
                                ("patch --dry-run -p1 < x.patch", []),
                                # SPD-126's second engineer: a later patch in the file names its own file, so the tree
                                # stays; the named file, its backups and its reject file beside it
                                ("patch -o out/y x.patch", [(".", "tree"), ("out/y", None), ("out/y.orig", None),
                                                            ("out/y.~" + m.NAME_CHAR + m.NAME_MORE + "~", None), ("out/y.rej", None)]),
                                ("patch docs/x.md x.patch", [(".", "tree"), ("docs/x.md", None), ("docs/x.md.orig", None),
                                                             ("docs/x.md.~" + m.NAME_CHAR + m.NAME_MORE + "~", None),
                                                             ("docs/x.md.rej", None)]),
                                ("curl -O https://example.com/x", [(".", "tree")]), ("curl -sSLO https://example.com/x", [(".", "tree")]),
                                ("curl -O --output-dir out https://example.com/x", [("out", "tree")]),
                                ("curl --remote-name-all https://example.com/x", [(".", "tree")]),
                                ("curl -s https://example.com/x", []), ("curl -K cfg https://example.com/x", [(m.ANY_PATH, "tree")])):
            with self.subTest(command):
                self.assertEqual(self.writes(command), writes)

    def test_into_the_members_own_subtree_is_silent(self):
        for command in ("tar -xf a.tar -C out", "tar xzf a.tar -C out", "tar -x -C out -f a.tar", "tar -xf a.tar --directory=out/x",
                        "unzip -q a.zip -d out", "unzip -o a.zip -d out/z", "patch -d out -p1 < x.patch", "patch -p1 -i x.patch -d out",
                        "curl -O --output-dir out https://example.com/x", "cd out && curl -sSLO https://example.com/x",
                        "cd out && tar -xf ../a.tar", "tar -xf a.tar -C /tmp/spd-126-x", "tar -tf a.tar", "unzip -l a.zip",
                        "patch --dry-run -p1 < x.patch", "curl -s https://example.com/x"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_G)

    def test_into_the_checkout_root_a_rendered_root_or_a_partial_glob_is_refused(self):
        self.wide()
        for command in ("tar -xf a.tar", "tar -xf a.tar -C .", "unzip a.zip", "patch -p1 < x.patch", "curl -O https://example.com/x"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables", agent_id=AGENT_G)
                self.assertRefused(command, CHECKOUT_WORDING, agent_id=AGENT_C)  # home:** too: the root holds the home's own
        for command, needle in (("tar -xf a.tar -C ledger", "generated"),
                                ("unzip a.zip -d reports", "generated"), ("patch -d ledger/tickets -p1 < x.patch", "generated"),
                                ("curl -O --output-dir ledger https://example.com/x", "generated"), ("tar -xf a.tar -C docs", "deliverables"),
                                ("unzip a.zip -d bin", "deliverables"), ("tar -xf a.tar -C tests/fake/.git", GIT_DIR_WORDING),
                                ("tar -xf a.tar -C /Users/Nobody", OUTSIDE)):
            with self.subTest(command):
                self.assertRefused(command, needle, agent_id=AGENT_G)

    def test_what_the_line_cannot_place_is_refused(self):
        for command in ("tar -xPf a.tar -C out", "tar -x --absolute-paths -f a.tar -C out", "tar -x -T list -f a.tar -C out",
                        "unzip -: a.zip -d out", "UNZIP=-d/x unzip a.zip -d out", "curl -K cfg https://example.com/x",
                        "CURL_HOME=out curl https://example.com/x", "tar -xf a.tar $(echo -C /)", "unzip a.zip $(echo -d /)"):
            with self.subTest(command):
                self.assertRefused(command, ANYWHERE_WORDING, agent_id=AGENT_G)
        self.assertSilent("curl -q -s https://example.com/x", agent_id=AGENT_G)
        self.assertRefused("tar -x --use-compress-program 'git push' -f a.tar -C out", "Law 7", agent_id=AGENT_G)

    def test_spuds_answers(self):
        self.assertRefused("tar -xf a.tar -C out", "Law 1", agent_id=None)
        self.assertRefused("unzip a.zip -d ledger", "generated", agent_id=None)
        for command in ("tar -xf a.tar -C /tmp/spd-126-x", "tar -xPf a.tar -C /tmp/spd-126-x", "curl -K cfg https://example.com/x"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=None)


AGENT_I = "f3a4b5c6d7e8f9a0b"  # SPD-126: a member whose one glob names split's pieces exactly, home:out/x??

# SPD-126's second engineer: one line per writer the Bash hook now reads by the name it spells, `{}` the file it writes.
SPELLED_WRITERS = (
    ("dd of=", "dd if=/dev/zero of={} count=1"),
    ("sort -o", "sort -o {} a.tar"),
    ("sort --output=", "sort a.tar --output={}"),  # getopt_long permutes: an option after the operand is an option
    ("curl -o", "curl -sS -o {} https://example.com/x"),
    ("curl -D", "curl -D {} https://example.com/x"),
    ("curl -c", "curl -c {} https://example.com/x"),
    ("curl --trace", "curl --trace {} https://example.com/x"),
    ("curl --trace-ascii", "curl --trace-ascii {} https://example.com/x"),
    ("curl --stderr", "curl --stderr {} https://example.com/x"),
    ("curl --libcurl", "curl --libcurl {} https://example.com/x"),
    ("curl --etag-save", "curl --etag-save {} https://example.com/x"),
    ("curl --hsts", "curl --hsts {} https://example.com/x"),
    ("curl --alt-svc", "curl --alt-svc {} https://example.com/x"),
    ("curl -w %output", "curl -o /dev/null -w '%output{{}}%{http_code}' https://example.com/x"),
    ("mkfifo", "mkfifo -m 600 {}"),
    # SPD-150 refuses a member perl's -e as the inline program it is, before ever reaching its -i, so these two name
    # perl's other program, a file (InlineProgramTest); `perl -i script.pl f` writes f exactly as `-i -pe` does.
    ("perl -i", "perl -i script.pl {}"),
    ("perl -0pi", "perl -0pi script.pl {}"),
    ("tar -c", "tar -czf {} docs"),
    ("tar c bundle", "tar cf {} docs"),
    ("tar -r", "tar -rf {} docs"),
    ("wget -O", "wget --no-hsts -O {} https://example.com/x"),
    ("wget -o", "wget --no-hsts -O - -o {} https://example.com/x"),
    ("wget --save-cookies", "wget --no-hsts -O - --save-cookies {} https://example.com/x"),
    ("wget --hsts-file", "wget -O - --hsts-file={} https://example.com/x"),
)


class SpelledWriteTest(TreeWriteCase):
    """SPD-126 (its second engineer): the writers whose file the line spells but SPD-121 never read.  Main (08c344e) recorded
    nothing -- no redirect, no write by argument -- for `dd if=/dev/zero of=out/f count=1`, `sort -o out/f in`, `curl -o
    out/f https://x`, `perl -i -pe 's/a/b/' out/f` or `mktemp out/tmp.XXXX`, so a member's `sort -o
    ledger/tickets/SPD-001.md x` or `dd of=tests/fake/.git/hooks/pre-commit` met neither Law 5 nor SPD-066's rule.

    Each is read on this Mac's man page and held to the path rule as a redirection target is (shell/spelled_writes,
    shell/downloads, shell/tree_writes): dd(1)'s last `of=`; sort(1)'s -o/--output (getopt_long: permuted, abbreviated) and
    -T's directory, whole; curl(1) 8.7.1's -o under --output-dir, -D, -c, --trace, --trace-ascii, --stderr, --libcurl,
    --etag-save, --hsts, --alt-svc and -w's %output{}, `-` being standard output; mkfifo(1)'s operands; mktemp(1)'s
    templates, read under -p, their trailing Xs picked from [0-9A-Za-z]; split(1)'s pieces, the prefix and a suffix of -a's
    length, two letters that grow when -a and -d are absent (text_cmds' split.c); perlrun's -i[extension] over every file
    after the program, `<file><ext>` or the extension with `*` for the file as the backup; bsdtar's (tar(1)) archive under
    -c, -r and -u, relative to the line's directory whatever -C comes first ("In c and r mode, this changes the directory
    before adding the following files"); BSD patch(1)'s file operand or -o, with its backup (.orig, -z, -B, -Y, numbered)
    and its reject file (-r, or <file>.rej), and a whole-subtree write of -d's directory or the line's, since each later
    patch in the file names its own (FreeBSD patch.c, reinitialize_almost_everything); and wget, from GNU wget's manual
    alone (it is not installed here): -O's file, -o, -a, --save-cookies, --hsts-file (~/.wget-hsts unless --no-hsts),
    and without -O the -P directory, whole.  A name a command picks (mktemp's X, split's suffix) is held by
    hooks/pathrule.NAME_CHAR, which a glob's wildcards match and its literals do not, so a glob lets it in only where it
    lets in every name the command may pick; each name the path rule refuses by spelling (.git, a generated root) that it
    may pick is checked as itself.  A word the line cannot settle where an option may stand is a write anywhere.

    The differential over the 945 Bash commands spudagents ran that name dd, sort, curl, mkfifo, mktemp, split, perl, tar,
    patch or wget is in the ticket's result: every newly refused one is a target the hook cannot resolve (a loop's or a
    substitution's `-o "$n"`), a cd the hook cannot follow, or patch -o at a checkout's root.  TreeWriteCase's members:
    AGENT_G out/**, bin/* and docs/*.md; AGENT_H bin/** and vendor/**; AGENT_A tests/** and bin/spud; AGENT_I out/x??."""

    def setUp(self):
        super().setUp()
        self.home.env["HOME"] = "/Users/Nobody"  # a HOME outside every project and every temp root, as OutsideProjectTest's
        for d in ("tests/fake/.git/hooks", "out/sub", ".claude"):
            (self.home.path / d).mkdir(parents=True, exist_ok=True)
        self.scratchpad = "/private/tmp/claude-%d/-Users-Someone-Personal-Spud/%s/scratchpad" % (os.getuid(), SESSION)

    def full(self, command, cwd=None):
        """(the word, the backup suffix, the kind) of each write by argument the line records."""
        return [(w[1], w[5], w[6]) for w in self.analysis(command, cwd).arg_writes if w[4] != "walk"]

    def picked(self, text):
        """`text` with each `?` a character the command picks and `*` a run of them, as the analysis records them."""
        return text.replace("?", self.module.NAME_CHAR).replace("*", self.module.NAME_MORE)

    def test_the_analysis_records_what_each_writer_names(self):
        m, p = self.module, self.picked
        for command, writes in (
            ("dd if=/dev/zero of=out/f count=1", [("out/f", None)]),
            ("dd of=out/a of=out/f", [("out/f", None)]),  # BSD dd refuses a second of=, GNU's keeps the last
            ("dd if=out/f of=/dev/null", [("/dev/null", None)]),
            ("dd if=out/f", []),
            ("sort -o out/f in", [("out/f", None)]), ("sort in -o out/f", [("out/f", None)]), ("sort -uo out/f in", [("out/f", None)]),
            ("sort --out out/f in", [("out/f", None)]),  # getopt_long takes an abbreviation
            ("sort --output=out/f in", [("out/f", None)]), ("sort -o - in", []), ("sort in", []),
            ("sort -T out/tmp in", [("out/tmp", "tree")]),
            ("curl -o out/f https://x", [("out/f", None)]), ("curl -sSLo out/f https://x", [("out/f", None)]),
            ("curl --output-dir out -o f https://x", [("out/f", None)]),
            ("curl --output-dir out -O https://x --next -o g https://y", [("out", "tree"), ("g", None)]),  # per -: section
            ("curl -o - https://x", []), ("curl -D - -o /dev/null https://x", [("/dev/null", None)]), ("curl --hsts '' https://x", []),
            ("curl -w '%output{out/w}%{http_code}' https://x", [("out/w", None)]),
            ("curl -g -o 'out/#1' 'https://x/[1-3]'", [("out/#1", None)]),  # -g: no URL globbing, the name as spelled
            ("curl --no-clobber -o out/f https://x", [("out/f", None), (p("out/f.?*"), None)]),
            ("curl -o 'out/#1' 'https://x/[1-3]'", [(m.ANY_PATH, "tree")]),  # #1 is the text the URL's set puts there
            ("curl -w @fmt https://x", [(m.ANY_PATH, "tree")]), ("curl --expand-output '{{f}}' https://x", [(m.ANY_PATH, "tree")]),
            ("mkfifo -m 600 out/p out/q", [("out/p", None), ("out/q", None)]),
            ("mktemp out/tmp.XXXX", [(p("out/tmp.????"), None)]), ("mktemp -d out/d.XXXXXX", [(p("out/d.??????"), "make")]),
            ("mktemp -p out x.XXXX", [(p("out/x.????"), None)]), ("mktemp -p out /tmp/x.XX", [(p("/tmp/x.??"), None)]),
            ("mktemp -p out -t foo", [(p("out/foo.????????"), None)]), ("mktemp out/plain", [("out/plain", None)]),
            ("mktemp -u out/x.XXXX", []), ("mktemp -d", []), ("mktemp -t foo", []),  # -u makes nothing; the temp root is open
            ("mktemp -d .giX", [(p(".gi?"), "make"), (".git", "make")]),  # the one name the path rule refuses by spelling
            ("split -l 10 big out/part_", [(p("out/part_??*"), None)]), ("split -a 3 big out/p", [(p("out/p???"), None)]),
            ("split -d big out/p", [(p("out/p??"), None)]), ("split big", [(p("x??*"), None)]),
            ("split -a 1 big .gi", [(p(".gi?"), None), (".git", None)]),
            ("perl -i -pe s/a/b/ out/f", [("out/f", None)]), ("perl -0pi -e s/a/b/ out/f out/g", [("out/f", None), ("out/g", None)]),
            ("perl -i script.pl out/f", [("out/f", None)]),  # no -e: the first operand is the program
            ("perl -pie s/a/b/ out/f", [("out/f", None)]),  # -i takes the rest of its word: extension "e", no -e
            ("perl -pi'old/*.orig' -e s/a/b/ out/f", [("out/f", None), ("old/out/f.orig", None)]),
            ("perl -pe s/a/b/ out/f", []), ("perl -e 'print 1' -- -i", []),
            ("tar -czf out/a.tgz src", [("out/a.tgz", None)]), ("tar czf out/a.tgz src", [("out/a.tgz", None)]),
            ("tar -C src -cf out/a.tar .", [("out/a.tar", None)]), ("tar -uf out/a.tar src", [("out/a.tar", None)]),
            ("tar -cf - src", []), ("export TAPE=out/t; tar -c src", [("out/t", None)]),
            ("patch -d out -r rej f x.patch", [("out", "tree"), ("out/f", None), ("out/f.orig", None), (p("out/f.~?*~"), None),
                                              ("out/rej", None)]),
            ("patch -d out -V none f x.patch", [("out", "tree"), ("out/f", None), ("out/f.rej", None)]),
            ("patch -d out --posix f x.patch", [("out", "tree"), ("out/f", None), ("out/f.rej", None)]),
            ("patch -d out -V simple -z .bak f x.patch", [("out", "tree"), ("out/f", None), ("out/f.bak", None), ("out/f.rej", None)]),
            ("patch -d out -B bak/ f x.patch", [("out", "tree"), ("out/bak", "tree"), ("out/f", None), ("out/bak/f", None),
                                                ("out/f.rej", None)]),
            ("patch -d out -d sub -p1 < x.patch", [("out/sub", "tree")]),  # each -d from the one before it
            ("wget -O out/f https://x", [("out/f", None), ("~/.wget-hsts", None)]),
            ("wget --no-hsts -P out https://x", [("out", "tree")]), ("wget --no-hsts https://x", [(".", "tree")]),
            ("wget --no-hsts -O - https://x", []), ("wget --no-hsts -b -P out https://x", [(p("wget-log*"), None), ("out", "tree")]),
            ("wget -e robots=off https://x", [(m.ANY_PATH, "tree")]),
        ):
            with self.subTest(command):
                self.assertEqual(self.writes(command), writes)

    def test_backups_and_suffixes_are_recorded_beside_the_file(self):
        for command, writes in (("perl -pi.bak -e s/a/b/ out/f", [("out/f", ".bak", None)]),
                                ("perl -pi~ -e s/a/b/ out/f", [("out/f", "~", None)]),
                                ("perl -pi -e s/a/b/ out/f", [("out/f", None, None)]),
                                ("perl -pi'*' -e s/a/b/ out/f", [("out/f", None, None)])):  # `*`: overwrite, no backup (perlrun)
            with self.subTest(command):
                self.assertEqual(self.full(command), writes)

    def test_every_writer_is_held_to_the_path_rule(self):
        for label, form in SPELLED_WRITERS:
            for target, needle in (("ledger/tickets/SPD-001.md", "generated"), ("tests/fake/.git/hooks/pre-commit", GIT_DIR_WORDING),
                                   ("/Users/Nobody/x", OUTSIDE), ("docs/new.txt", "deliverables")):
                command = form.replace("{}", target)
                with self.subTest(command=command):
                    r = self.assertRefused(command, needle, agent_id=AGENT_G)
                    self.assertIn(ARG_WORDING, r.reason)
                    self.assertIn(target, r.reason)

    def test_every_writer_is_silent_into_the_members_own_files_the_scratchpad_and_tmp(self):
        for label, form in SPELLED_WRITERS:
            for target in ("out/new", "docs/new.md", self.scratchpad + "/probe.txt", "/tmp/spd-126-y"):
                command = form.replace("{}", target)
                with self.subTest(command=command):
                    self.assertSilent(command, agent_id=AGENT_G)

    def test_the_tickets_lines(self):
        for command, needle in (("sort -o ledger/tickets/SPD-001.md out/keep.txt", "generated"),
                                ("dd if=/dev/zero of=tests/fake/.git/hooks/pre-commit count=1", GIT_DIR_WORDING),
                                ("curl -c tests/fake/.git/config https://example.com/x", GIT_FILE_WORDING)):
            with self.subTest(command):
                self.assertRefused(command, needle, agent_id=AGENT_A)  # tests/** is AGENT_A's, .git no member's
        self.assertSilent("sort -o tests/sorted.txt out/keep.txt", agent_id=AGENT_A)

    def test_mktemp_is_held_by_every_name_it_may_pick(self):
        for command in ("mktemp out/tmp.XXXX", "mktemp -d out/d.XXXXXX", "mktemp bin/tmp.XXXX",  # bin/*: any name in bin
                        "mktemp -p out x.XXXX", "mktemp docs/x.XXXX.md",  # no trailing X: the name as spelled, docs/*.md's
                        "mktemp -d /tmp/spd-126-x.XXXX", "mktemp -d", "mktemp -t foo", "mktemp -d %s/h.XXXX" % self.scratchpad,
                        "T=$(mktemp -d); echo $T", "mktemp -u docs/x.XXXX"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_G)
        for command, needle, shown in (("mktemp docs/x.XXXX", "deliverables", "docs/x.????"),  # docs/*.md: not every name
                                       ("mktemp -p docs x.XXXX", "deliverables", "docs/x.????"),
                                       ("mktemp tmp.XXXX", "deliverables", "tmp.????"),  # the checkout root
                                       ("mktemp ledger/tickets/x.XXXX", "generated", "ledger/tickets/x.????"),
                                       ("mktemp -d /Users/Nobody/x.XXXX", OUTSIDE, "/Users/Nobody/x.????")):
            with self.subTest(command):
                r = self.assertRefused(command, needle, agent_id=AGENT_G)
                self.assertIn(shown, r.reason)  # the picked characters shown as a glob shows them
        # a name the path rule refuses by spelling, which the template may become, is checked as itself
        r = self.assertRefused("mktemp -d tests/fake/.giX", GIT_DIR_WORDING, agent_id=AGENT_A)
        self.assertIn("tests/fake/.git", r.reason)
        self.assertSilent("mktemp -d tests/fake/.gX", agent_id=AGENT_A)  # three characters: never .git
        self.assertSilent("mktemp -d tests/x.XXXX", agent_id=AGENT_A)
        self.wide()
        self.assertRefused("mktemp -d ledgXX", "generated", agent_id=AGENT_C)  # home:** lets in ledg??, but not ledger
        self.assertSilent("mktemp -d out/ledgXX", agent_id=AGENT_C)

    def test_split_is_held_by_every_piece_it_may_write(self):
        for command in ("split -l 1 a.tar out/p_", "split -l 1 a.tar bin/p_", "split -b 1k -a 3 a.tar out/sub/x",
                        "split -l 1 a.tar /tmp/spd-126-x/p"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_G)
        for command, needle, shown in (("split -l 1 a.tar docs/p_", "deliverables", "docs/p_??*"),
                                       ("split -l 1 a.tar", "deliverables", "x??*"),  # prefix x, at the checkout root
                                       ("split -a 2 a.tar ledger/x", "generated", "ledger/x??")):
            with self.subTest(command):
                r = self.assertRefused(command, needle, agent_id=AGENT_G)
                self.assertIn(shown, r.reason)
        self.assertRefused("split -a 1 a.tar tests/fake/.gi", GIT_DIR_WORDING, agent_id=AGENT_A)  # .git among its pieces
        # out/x?? names every piece of two letters, and no longer one: split's suffix grows past two letters unless -a
        # fixes its length (or -d makes it digits), so only then are the pieces all the member's
        self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:out/x??"]), AGENT_I)
        for command in ("cd out && split -a 2 ../a.tar", "split -a 2 a.tar out/x", "split -d a.tar out/x"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_I)
        r = self.assertRefused("split a.tar out/x", "deliverables", agent_id=AGENT_I)
        self.assertIn("out/x??*", r.reason)
        self.assertRefused("split -a 3 a.tar out/x", "deliverables", agent_id=AGENT_I)

    def test_perl_in_place_and_its_backup(self):
        # SPD-150: perl's -e and -E are inline programs, refused a member where their text writes (SPD-175) -- and -i is
        # a write marker, so an inline -i edit is refused before its files are reached -- so the silent lines with -i here
        # name perl's other program, a file; each of them was silent for the same reason before that ticket.
        for command in ("perl -pi script.pl bin/spud", "perl -0pi script.pl tests/x.txt", "perl -pi.bak script.pl tests/x.txt",
                        "perl script.pl docs/x.md", "perl -p script.pl docs/x.md", "perl -Ilib script.pl docs/x.md",
                        "find tests/out -exec perl -pi script.pl {} +",
                        # a program the line spells whose text writes nothing, which SPD-175 lets run
                        "perl -pe s/a/b/ docs/x.md", "perl -ne 'print if /x/' docs/x.md", "perl -e 'select(undef,undef,undef,0.5)'"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_A)
        # ... and perl reads its switches past -e's program, so a word it reads there that the line does not settle may
        # be a -i or another -e: the differential's `"$1"`, read in a body the line defines, stays refused
        for command in ("perl -pi -e s/a/b/ bin/spud", "perl -0pi -e s/a/b/ tests/x.txt",
                        "sleep_ms() { perl -e 'select undef, undef, undef, $ARGV[0]' \"$1\"; }; sleep_ms 20"):
            with self.subTest(command):
                self.assertRefused(command, INLINE_WORDING, agent_id=AGENT_A)  # -i, whatever the files it edits
        for command in ("perl -pe s/a/b/ docs/x.md", "perl -e 'select(undef,undef,undef,0.5)'"):  # Spud keeps his
            with self.subTest(command):
                self.assertSilent(command, agent_id=None)
        for command, needle, shown in (("perl -pi.bak -e s/a/b/ bin/spud", "deliverables", "bin/spud.bak"),
                                       ("perl -pi'orig_*' -e s/a/b/ bin/spud", "deliverables", "orig_bin/spud"),
                                       ("perl -pie s/a/b/ bin/spud", "deliverables", "bin/spude"),  # -pie: extension "e"
                                       ("perl -i -pe s/a/b/ docs/x.md", "deliverables", "docs/x.md"),
                                       ("perl -pi -e s/a/b/ tests/fake/.git/config", GIT_FILE_WORDING, "tests/fake/.git/config"),
                                       ("find docs -exec perl -pi -e s/a/b/ {} +", "deliverables", "docs")):
            with self.subTest(command):
                r = self.assertRefused(command, needle, agent_id=AGENT_A)
                self.assertIn(shown, r.reason)
        for command in ("xargs perl -pi -e s/a/b/ < list", "perl -e s/a/b/ $(echo -i) tests/x.txt", "f() { perl -pi -e s/a/b/ \"$1\" tests/x; }; f x"):
            with self.subTest(command):
                self.assertRefused(command, ANYWHERE_WORDING, agent_id=AGENT_A)  # a word that may be -i<anything>

    def test_tar_writes_its_archive_where_the_line_is(self):
        for command in ("tar -czf out/a.tgz docs", "tar -C docs -czf out/a.tgz .", "cd out && tar -cf a.tar ../docs",
                        "tar -cf - docs | wc -c", "tar -tf a.tar", "tar -xf a.tar -C out"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_G)
        for command, needle, shown in (("tar -C out -cf docs/a.tar .", "deliverables", "docs/a.tar"),  # -C moves no archive
                                       ("tar -cf ledger/tickets/x.tar out", "generated", "ledger/tickets/x.tar"),
                                       ("tar -rf tests/fake/.git/hooks/pre-commit out", GIT_DIR_WORDING, "pre-commit"),
                                       ("export TAPE=docs/t; tar -c out", "deliverables", "docs/t")):
            with self.subTest(command):
                r = self.assertRefused(command, needle, agent_id=AGENT_G)
                self.assertIn(shown, r.reason)
        self.assertRefused("tar -cf out/a.tar $(ls)", ANYWHERE_WORDING, agent_id=AGENT_G)  # may be -f elsewhere

    def test_patch_writes_its_file_its_backup_and_its_reject(self):
        for command in ("patch -d out f ../x.patch", "patch -d out -o g f ../x.patch", "patch -d out -r f.rej f < x.patch",
                        "patch -d bin -z .bak x.py ../x.patch", "patch --dry-run docs/x.md x.patch"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_H if "bin" in command else AGENT_G)
        for command, needle, shown, agent in (
            ("patch -d out -r ../docs/x.rej f ../x.patch", "deliverables", "docs/x.rej", AGENT_G),
            ("patch -d out -o ../ledger/tickets/SPD-001.md f ../x.patch", "generated", "ledger/tickets/SPD-001.md", AGENT_G),
            ("patch -d bin -Y ../docs/ x.py ../x.patch", "deliverables", "docs/x.py.orig", AGENT_H),  # -Y: before the basename
            ("patch -d bin /Users/Nobody/f ../x.patch", OUTSIDE, "/Users/Nobody/f", AGENT_H),
            ("patch -d tests fake/.git/hooks/pre-commit ../x.patch", GIT_DIR_WORDING, "pre-commit", AGENT_A),
            # a later patch in the file names its own file under the line's directory, whatever -o or the operand say
            ("patch -o out/y out/keep.txt x.patch", "deliverables", "home:", AGENT_G),
        ):
            with self.subTest(command):
                r = self.assertRefused(command, needle, agent_id=agent)
                self.assertIn(shown, r.reason)

    def test_downloads_write_their_named_files(self):
        for command in ("curl -sS -o out/f https://example.com/x", "curl -o /dev/null -w '%{http_code}' https://example.com/x",
                        "curl --output-dir out -o f https://example.com/x", "curl -D - -s https://example.com/x",
                        "curl -g -o 'out/#1' 'https://example.com/[1-2]'", "wget --no-hsts -P out https://example.com/x",
                        "wget --no-hsts -O out/f https://example.com/x", "a=$(echo x); curl -s -D - \"https://example.com/$a\"",
                        "wget --no-hsts -b -P out -o out/log https://example.com/x"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_G)
        for command, needle in (("curl --output-dir docs -o f https://example.com/x", "deliverables"),
                                ("curl -w '%output{docs/w}' https://example.com/x", "deliverables"),
                                ("wget --no-hsts https://example.com/x", "deliverables"),  # the checkout root, whole
                                ("wget --no-hsts -o ledger/x.log -P out https://example.com/x", "generated"),
                                ("wget -P out https://example.com/x", OUTSIDE),  # the HSTS database, ~/.wget-hsts
                                ("wget --no-hsts -b -P out https://example.com/x", "deliverables"),  # ./wget-log, the root's
                                ("curl -o 'out/#1' 'https://example.com/[1-2]'", ANYWHERE_WORDING),
                                ("curl -w @fmt https://example.com/x", ANYWHERE_WORDING),
                                ("curl -o out/f \"$(cat list)\"", ANYWHERE_WORDING),  # may be -o anything
                                ("wget -e robots=off --no-hsts -P out https://example.com/x", ANYWHERE_WORDING),
                                ("for n in a b; do curl -o \"$n.html\" https://example.com/$n; done", "deliverables")):  # a.html (SPD-146)
            with self.subTest(command):
                self.assertRefused(command, needle, agent_id=AGENT_G)
        self.assertRefused("sort --compress-program='git push' -o out/s a.tar", "Law 7", agent_id=AGENT_G)
        self.assertRefused("wget --use-askpass='git push' --no-hsts -P out https://example.com/x", "Law 7", agent_id=AGENT_G)

    def test_a_word_the_line_cannot_settle_where_an_option_may_stand(self):
        for command in ("sort -u \"$(cat list)\"", "sort $(echo -o) docs/x a.tar", "X=$(echo -o); sort $X docs/y a.tar",
                        "mktemp \"$(echo -p)\" /x.XXXX"):
            with self.subTest(command):
                self.assertRefused(command, ANYWHERE_WORDING, agent_id=AGENT_G)
        r = self.assertRefused("dd $(echo of=docs/x)", VARIABLE_WORDING, agent_id=AGENT_G)  # may begin with of=
        self.assertIn(ARG_WORDING, r.reason)
        for command in ("sort -u \"out/$(cat list)\"", "X=-o; sort $X out/s a.tar", "dd if=\"$(ls | head -1)\" of=out/f",
                        "sort \"$HOME/x\""):  # the environment's HOME, which the line does not set
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_G)
        r = self.assertRefused("X=-o; sort $X docs/s a.tar", "deliverables", agent_id=AGENT_G)  # the line's own value, read
        self.assertIn("docs/s", r.reason)

    def test_spuds_answers(self):
        """Spud is held to Law 1 in a project and is free in his own files and outside every project, as for a redirection."""
        for label, form in SPELLED_WRITERS:
            with self.subTest(label):
                r = self.assertRefused(form.replace("{}", "docs/x.md"), "Law 1", agent_id=None)
                self.assertIn(ARG_WORDING, r.reason)
                self.assertRefused(form.replace("{}", "ledger/tickets/SPD-001.md"), "generated", agent_id=None)
                self.assertSilent(form.replace("{}", "/Users/Nobody/x"), agent_id=None)
                self.assertSilent(form.replace("{}", ".claude/x"), agent_id=None)  # his own .claude/**
        self.assertSilent("mktemp .claude/x.XXXX", agent_id=None)
        self.assertRefused("mktemp docs/x.XXXX", "Law 1", agent_id=None)
        self.assertRefused("split -l 1 a.tar ledger/x", "generated", agent_id=None)
        for command in ("wget https://example.com/x -P /tmp/spd-126-x", "sort -u \"$(cat list)\""):
            with self.subTest(command):
                self.assertSilent(command, agent_id=None)  # outside every project, or a target Spud's reading leaves unread
        # a file the line names through an expansion it does not settle refuses Spud too (SPD-091)
        self.assertRefused("curl -o \"$n\" https://example.com/x", "spell the path out", agent_id=None)
        r = self.assertRefused("dd if=/dev/zero of=tests/fake/.git/hooks/pre-commit", "Law 1", agent_id=None)
        self.assertNotIn(GIT_DIR_WORDING, r.reason)  # SPD-066's rule is a caller's with an agent_id


# SPD-139: one line per shape a sed script or an awk program writes a file the line itself never spells, `{}` that file.
SCRIPT_WRITERS = (
    ("sed w", "sed -n 'w {}' a.tar"),
    ("sed address w", "sed -n '/a/w {}' a.tar"),
    ("sed s///w", "sed -n 's/a/b/w {}' a.tar"),
    ("sed s///gw", "sed -n 's/a/b/gw {}' a.tar"),
    ("sed -e w", "sed -n -e 'w {}' -e p a.tar"),
    ("sed -e { w }", "sed -n -e '/a/{' -e 'w {}' -e '}' a.tar"),
    ("awk print >", "awk '{print > \"{}\"}' a.tar"),
    ("awk print >>", "awk '{print >> \"{}\"}' a.tar"),
    ("awk printf >", "awk '{printf \"%s\", $0 > \"{}\"}' a.tar"),
    ("awk BEGIN print >", "awk 'BEGIN{print \"x\" > \"{}\"}'"),
    ("awk print > in a function", "awk 'function w(){print \"x\" > \"{}\"} BEGIN{w()}'"),
)


class ScriptTextTest(TreeWriteCase):
    """SPD-139: the Bash hook reads a command's words, and a sed script or an awk program is one quoted word, so what the
    script itself names was never read.  On main (08c344e), with cwd /Users/X/repo, each of these gave findings=[] and no
    write: `awk 'BEGIN{system(\"git push\")}'` and `awk 'BEGIN{print \"x\" | \"git push\"}'` (a VCS write past Law 7),
    `awk '{print > \"ledger/tickets/SPD-001.md\"}' f` and `sed -n 'w ledger/tickets/SPD-001.md' f` (a write to a rendered
    note past Law 5).

    Spud's decision: what a script names is read as the line's own words are.  A file it writes is held to the path rule
    as a redirection target is (shell/script_text records it where shell/spelled_writes records dd's `of=`), and a command
    it runs is read as an `sh -c` string is (analyse_new_shell), so its git verbs meet Law 7 and its `spud --as spud`
    Law 6.  What the hook cannot resolve there is refused a member as an unresolvable target is, never silently allowed.

    Probed on this Mac, whose sed is BSD's and whose awk is the one true awk, version 20200816 (no gsed or gawk is
    installed; SPD-140 owns the g-names):
    - sed's `w file` and the `w file` flag of `s///` take the rest of the line as the name: `w out2.txt;p` made a file
      called `out2.txt;p`, and `w out.txt   ` one with the blanks (sed warns and keeps them).  Blanks after the letter are
      skipped, and `wout.txt` needs none.  Each -e is its own line, so a name ends where its fragment does, and a brace
      may open in one and close in another.  `b`, `t` and `:` take the rest of the line as a label (`b;w f` failed with
      "undefined label ;w f"), `a`, `i` and `c` need a backslash-newline and their text is not commands, `#` comments to
      the end of the line, `r` only reads, and BSD has no `W`.  -f reads the script from a file, and its `w` writes.
    - awk reads only the first letter after a dash (main.c: `switch (argv[1][1])`), so there is no getopt cluster and
      `-safe` is `s`; -f, -F and -v take the rest of their word or the next word; an unknown option is ignored with a
      warning and the program is still the next operand; `--` ends the options; -f may repeat, and the files concatenate.
      `print`, `printf` and their `>`, `>>` and `|` write and run; `system(...)` and `"cmd" | getline` run.  A `>` inside
      parentheses is the comparison (`print (1 > 2)` printed 0), and a target may also be a variable, a parenthesised
      expression or a bare concatenation (`> \"out\" \"6.txt\"` made out6.txt), none of which the hook can name.

    TreeWriteCase's members: AGENT_G out/**, bin/* and docs/*.md; AGENT_H bin/** and vendor/**; AGENT_A tests/** and
    bin/spud."""

    def setUp(self):
        super().setUp()
        self.home.env["HOME"] = "/Users/Nobody"  # a HOME outside every project and every temp root, as OutsideProjectTest's
        (self.home.path / "out" / "p.sed").write_text("w docs/from-sed.txt\n", encoding="utf-8")
        (self.home.path / "out" / "p.awk").write_text('BEGIN{print "x" > "docs/from-awk.txt"}\n', encoding="utf-8")
        (self.home.path / "out" / "clean.awk").write_text('{n++} END{print n}\n', encoding="utf-8")

    def writes(self, command, cwd=None):
        """(the file, the kind) of each write by argument the line records, the masking taken off the word."""
        return [(self.module.deglob(w[1]), w[6]) for w in self.analysis(command, cwd).arg_writes if w[4] != "walk"]

    def verbs(self, command):
        """The git verbs the analysis finds on this line, in the order it finds them."""
        return [detail[0] for kind, detail in self.analysis(command).findings if kind == "git"]

    def test_the_analysis_records_what_a_sed_script_writes(self):
        m = self.module
        for command, writes in (
            ("sed -n 'w out/f' a.tar", [("out/f", None)]),
            ("sed -n 'wout/f' a.tar", [("out/f", None)]),  # the blank after the letter is optional
            ("sed -n 'w   out/f' a.tar", [("out/f", None)]),  # and any number of them are skipped
            ("sed -n 'w out/f;p' a.tar", [("out/f;p", None)]),  # the rest of the line is the name, `;` and all
            ("sed -n '/a/w out/f' a.tar", [("out/f", None)]),
            ("sed -n '$w out/f' a.tar", [("out/f", None)]),
            ("sed -n '1,$w out/f' a.tar", [("out/f", None)]),
            ("sed -n '1,+1w out/f' a.tar", [("out/f", None)]),
            ("sed -n '/a/,/b/w out/f' a.tar", [("out/f", None)]),
            ("sed -n '/a/Iw out/f' a.tar", [("out/f", None)]),
            ("sed -n '/a/!w out/f' a.tar", [("out/f", None)]),
            ("sed -n '\\%a%w out/f' a.tar", [("out/f", None)]),  # a delimiter of the script's own choosing
            ("sed -n 's/a/b/w out/f' a.tar", [("out/f", None)]),
            ("sed -n 's/a/b/gw out/f' a.tar", [("out/f", None)]),
            ("sed -n 's/a/b/2w out/f' a.tar", [("out/f", None)]),
            ("sed -n 's/a/b/Iw out/f' a.tar", [("out/f", None)]),
            ("sed -n 's|a|b|w out/f' a.tar", [("out/f", None)]),
            ("sed -n 's/a/b\\/c/w out/f' a.tar", [("out/f", None)]),  # the delimiter escaped inside the replacement
            ("sed -n 's/a/b/wout/f' a.tar", [("out/f", None)]),
            ("sed -n 's/a/b/;w out/f' a.tar", [("out/f", None)]),
            # a bracket expression holds the delimiter whole in an address and in the regular expression, and in neither
            # the replacement nor y's strings (sed's compile_delimited and compile_ccl, probed)
            ("sed -n '/[/]/w out/f' a.tar", [("out/f", None)]),
            ("sed -n 's/[a/b]/X/w out/f' a.tar", [("out/f", None)]),
            ("sed -n 's/[]/]/X/w out/f' a.tar", [("out/f", None)]),
            ("sed -n 's/[^/]*/X/w out/f' a.tar", [("out/f", None)]),
            ("sed -n 's/[[:alpha:]/]/X/w out/f' a.tar", [("out/f", None)]),
            ("sed -n 'y/a[/b]/;w out/f' a.tar", [("out/f", None)]),
            ("sed 's/.*github.com[:/]//;s/\\.git$//' a.tar", []),  # the differential's own line
            ("sed -n -e 'w out/f' -e p a.tar", [("out/f", None)]),  # each -e is its own line: the name ends with it
            ("sed -n -e '/a/{' -e 'w out/f' -e '}' a.tar", [("out/f", None)]),
            ("sed -n '/a/{\nw out/f\n}' a.tar", [("out/f", None)]),
            ("sed -n 'w out/f\np' a.tar", [("out/f", None)]),
            ("sed -n 'w out/f\nw out/g' a.tar", [("out/f", None), ("out/g", None)]),
            ("sed -i '' -e 'w out/f' a.tar", [("a.tar", None), ("out/f", None)]),  # in place, and what the script writes
            ("sed -i.bak 'w out/f' a.tar", [("a.tar", None), ("out/f", None)]),
            # what writes nothing
            ("sed -n p a.tar", []), ("sed -n '1,50p' a.tar", []), ("sed 's/a/b/' a.tar", []),
            ("sed -n 'r out/f' a.tar", []),  # r reads its file
            ("sed -n 's/w out\\/f/X/p' a.tar", []),  # a `w` inside the regular expression
            ("sed -n '/w out\\/f/p' a.tar", []),  # and inside an address
            ("sed -n 'y/ab/AB/' a.tar", []), ("sed -n '#w out/f' a.tar", []),
            ("sed -n 'b end;w out/f\n:end' a.tar", []),  # b takes the rest of the line: the label is `end;w out/f`
            ("sed -n '1a\\\nw out/f' a.tar", []),  # a's text is text, not commands
            ("sed -n -e p 'w out/f' a.tar", []),  # with -e given, the first operand is a file sed reads
            # a script this Mac's sed refuses, which the hook cannot read either: fail closed.  Each shape below is the
            # differential's, from the 19 lines of 12059 it newly refuses, and sed refuses every one of them too
            ("sed -n 'W out/f' a.tar", [(m.ANY_PATH, "tree")]),  # BSD sed has no W
            ("sed -n 'Z' a.tar", [(m.ANY_PATH, "tree")]),
            ("sed -n '700,900' a.tar", [(m.ANY_PATH, "tree")]),  # an address with no command
            ("sed -n '790,960]' a.tar", [(m.ANY_PATH, "tree")]),
            ("sed -n '200,290z' a.tar", [(m.ANY_PATH, "tree")]),
            ("sed -n '3490,3520,3600,3745p' a.tar", [(m.ANY_PATH, "tree")]),  # three addresses
            ("sed -i '' 's#a#b (PR #367)#' a.tar", [("a.tar", None), (m.ANY_PATH, "tree")]),  # the delimiter in the replacement
            ("sed -i '' \"s|a|b || c|\" a.tar", [("a.tar", None), (m.ANY_PATH, "tree")]),
        ):
            with self.subTest(command):
                self.assertEqual(self.writes(command), writes)

    def test_the_analysis_records_what_an_awk_program_writes(self):
        m = self.module
        for command, writes in (
            ("awk '{print > \"out/f\"}' a.tar", [("out/f", None)]),
            ("awk '{print >> \"out/f\"}' a.tar", [("out/f", None)]),
            ("awk '{print>\"out/f\"}' a.tar", [("out/f", None)]),
            ("awk '{printf \"%s\", $0 > \"out/f\"}' a.tar", [("out/f", None)]),
            ("awk '{printf(\"%s\", $0) > \"out/f\"}' a.tar", [("out/f", None)]),
            ("awk 'BEGIN{print \"x\" > \"out/f\"; print \"y\" > \"out/g\"}'", [("out/f", None), ("out/g", None)]),
            ("awk '$1 > 2 {print > \"out/f\"}' a.tar", [("out/f", None)]),  # the pattern's `>` is the comparison
            ("awk -F: '{print > \"out/f\"}' a.tar", [("out/f", None)]), ("awk -F : '{print > \"out/f\"}' a.tar", [("out/f", None)]),
            ("awk -v x=1 '{print > \"out/f\"}' a.tar", [("out/f", None)]),
            ("awk -vx=1 '{print > \"out/f\"}' a.tar", [("out/f", None)]),
            ("awk -q '{print > \"out/f\"}' a.tar", [("out/f", None)]),  # an unknown option awk ignores
            ("awk -safe '{print > \"out/f\"}' a.tar", [("out/f", None)]),  # only the first letter after the dash is read
            ("awk -- '{print > \"out/f\"}' a.tar", [("out/f", None)]),
            ("awk 'function w(){print \"x\" > \"out/f\"} BEGIN{w()}'", [("out/f", None)]),
            # what writes nothing
            ("awk '{n++} END{print n}' a.tar", []), ("awk '$1 > 2' a.tar", []), ("awk 'BEGIN{print (1 > 2)}'", []),
            ("awk 'BEGIN{if (2 > 1) print \"x\"}'", []), ("awk '{print $1, $2}' a.tar", []),
            ("awk '/a>b/{n++}' a.tar", []),  # a regular expression holding the operator
            ("awk 'BEGIN{s = \"a > b\"; print s}'", []),  # and a string holding it
            ("awk 'BEGIN{print \"x\"} # print > \"out/f\"'", []),  # a comment
            ("awk 'BEGIN{while ((getline l < \"out/f\") > 0) n++; print n}'", []),  # getline reads
            ("awk '{print}' a.tar", []), ("awk 'NR==1' a.tar", []),
            # the target the hook cannot name: a member is refused as for an unresolvable redirection target
            ("awk '{print > f}' a.tar", [(m.ANY_PATH, "tree")]),
            ("awk '{print > \"out\" \"/f\"}' a.tar", [(m.ANY_PATH, "tree")]),  # a bare concatenation
            ("awk '{print > (\"out/f\")}' a.tar", [(m.ANY_PATH, "tree")]),  # a parenthesised expression
            ("awk '{print > FILENAME}' a.tar", [(m.ANY_PATH, "tree")]),
            ("awk '{print \"x\" | c}' a.tar", [(m.ANY_PATH, "tree")]),  # and the command it cannot read
            ("awk 'BEGIN{system(c)}'", [(m.ANY_PATH, "tree")]),
            ("awk 'BEGIN{system(\"echo \" x)}'", [(m.ANY_PATH, "tree")]),
            ("awk 'BEGIN{c | getline v}'", [(m.ANY_PATH, "tree")]),
            # a program this awk refuses, which the hook cannot read either (the differential's own line: a `\\\"` inside
            # single quotes is a backslash and a quote awk has no use for, and the string never closes)
            ("ls -la | awk '{printf \\\"%s\\\", $9}'", [(m.ANY_PATH, "tree")]),
            ("awk 'BEGIN{print \"x}'", [(m.ANY_PATH, "tree")]),
        ):
            with self.subTest(command):
                self.assertEqual(self.writes(command), writes)

    def test_a_command_an_awk_program_runs_is_read_as_a_shell_string(self):
        for command, verbs in (("awk 'BEGIN{system(\"git push\")}'", ["push"]),
                               ("awk 'BEGIN{print \"x\" | \"git push\"}'", ["push"]),
                               ("awk 'BEGIN{\"git push\" | getline v}'", ["push"]),
                               ("awk '{print | \"git commit -m x\"}' a.tar", ["commit"]),
                               ("awk 'BEGIN{system(\"git status\")}'", ["status"]),
                               ("awk 'BEGIN{system(\"echo hi\")}'", []),
                               ("awk 'BEGIN{print \"x\" > \"out/f\"}'", [])):
            with self.subTest(command):
                self.assertEqual(self.verbs(command), verbs)
        self.assertEqual(self.writes("awk 'BEGIN{system(\"echo x > docs/y.txt\")}'"), [])  # its redirection, not a write by argument
        self.assertRefused("awk 'BEGIN{system(\"echo x > docs/y.txt\")}'", "deliverables", agent_id=AGENT_G)

    def test_the_tickets_four_lines(self):
        """The evidence of the ticket: each was read with findings=[] and no write on main."""
        self.assertRefused("awk 'BEGIN{system(\"git push\")}'", "Law 7", agent_id=AGENT_G)
        self.assertRefused("awk 'BEGIN{print \"x\" | \"git push\"}'", "Law 7", agent_id=AGENT_G)
        r = self.assertRefused("awk '{print > \"ledger/tickets/SPD-001.md\"}' a.tar", "generated", agent_id=AGENT_G)
        self.assertIn(ARG_WORDING, r.reason)
        r = self.assertRefused("sed -n 'w ledger/tickets/SPD-001.md' a.tar", "generated", agent_id=AGENT_G)
        self.assertIn(ARG_WORDING, r.reason)

    def test_every_script_write_is_held_to_the_path_rule(self):
        for label, form in SCRIPT_WRITERS:
            for target, needle in (("ledger/tickets/SPD-001.md", "generated"), ("tests/fake/.git/hooks/pre-commit", GIT_DIR_WORDING),
                                   ("/Users/Nobody/x", OUTSIDE), ("docs/new.txt", "deliverables")):
                command = form.replace("{}", target)
                with self.subTest(command=command):
                    r = self.assertRefused(command, needle, agent_id=AGENT_G)
                    self.assertIn(ARG_WORDING, r.reason)
                    self.assertIn(target, r.reason)

    def test_every_script_write_is_silent_into_the_members_own_files(self):
        for label, form in SCRIPT_WRITERS:
            for target in ("out/new", "docs/new.md", "/tmp/spd-139-y"):
                command = form.replace("{}", target)
                with self.subTest(command=command):
                    self.assertSilent(command, agent_id=AGENT_G)

    def test_a_target_the_script_does_not_spell_whole(self):
        """A `$var` the shell expands inside a double-quoted script is a hidden part of the name, and the line's own
        settled value is put in it where there is one (SPD-127), exactly as for a redirection target."""
        for command, writes in (('D=out; sed -n "w $D/f" a.tar', [("out/f", None)]),
                                ('D=out; awk "{print > \\"$D/f\\"}" a.tar', [("out/f", None)]),
                                ('sed -n "w $D/f" a.tar', [("$D/f", None)]),
                                ('awk "{print > \\"$D/f\\"}" a.tar', [("$D/f", None)])):
            with self.subTest(command):
                self.assertEqual(self.writes(command), writes)
        for command in ('sed -n "w $D/f" a.tar', 'awk "{print > \\"$D/f\\"}" a.tar',
                        'sed -n "w $(cat list)" a.tar'):
            with self.subTest(command):
                self.assertRefused(command, VARIABLE_WORDING, agent_id=AGENT_G)
                self.assertRefused(command, VARIABLE_WORDING, agent_id=None)  # Spud too, as for a redirection (SPD-091)
        r = self.assertRefused('D=docs; sed -n "w $D/f" a.tar', "deliverables", agent_id=AGENT_G)
        self.assertIn("docs/f", r.reason)

    def test_a_script_the_hook_cannot_spell_stays_unread(self):
        """A script word the hook cannot spell is the class of `sh script.sh`: unread for every caller, as on main.  A
        member's `sed -n \"${n},$((n+3))p\" f`, which writes nothing, is silent as SPD-121 left it."""
        for command in ('sed -n "$SCRIPT" a.tar', 'awk "$PROG" a.tar', 'sed -n "${n},$((n+3))p" a.tar',
                        'sed -n "$(cat out/p.sed)" a.tar', 'awk -f "$PROG" a.tar', 'sed -n -f "$S" a.tar'):
            with self.subTest(command):
                self.assertEqual(self.writes(command), [])
                self.assertSilent(command, agent_id=AGENT_G)

    def test_a_script_file_the_line_spells_is_read(self):
        for command, writes in (("sed -n -f out/p.sed a.tar", [("docs/from-sed.txt", None)]),
                                ("awk -f out/p.awk a.tar", [("docs/from-awk.txt", None)]),
                                ("awk -fout/p.awk a.tar", [("docs/from-awk.txt", None)]),
                                ("awk -f out/clean.awk a.tar", []),
                                ("sed -n -f out/missing.sed a.tar", []),  # a file the hook cannot read stays unread
                                ("awk -f out/p.awk -f out/clean.awk a.tar", [("docs/from-awk.txt", None)])):
            with self.subTest(command):
                self.assertEqual(self.writes(command), writes)
        r = self.assertRefused("sed -n -f out/p.sed a.tar", "deliverables", agent_id=AGENT_G)
        self.assertIn("docs/from-sed.txt", r.reason)
        self.assertRefused("awk -f out/p.awk a.tar", "deliverables", agent_id=AGENT_G)

    def test_a_word_the_line_cannot_settle_where_an_option_may_stand(self):
        """The hook cannot then say which operand is the script, and reads none of them: the same silence a script the
        line does not spell keeps.  Reading each operand as a script instead put a `w eb/app.js` in the file operand of a
        member's `sed -n "$(grep -n x app.js | cut -d: -f1),+12p" web/app.js`, one of 27 the differential found."""
        for command in ("awk $(echo -f) out/p.awk a.tar", "X=$(echo -v); awk $X '{print > \"docs/y.txt\"}' a.tar",
                        "sed -n \"$(grep -n x a.tar | cut -d: -f1),+12p\" docs/x.md",
                        "for n in 1 2; do sed -n \"${n}p\" docs/x.md; done"):
            with self.subTest(command):
                self.assertEqual(self.writes(command), [])
                self.assertSilent(command, agent_id=AGENT_G)

    def test_spuds_answers(self):
        """Spud is held to Law 1 in a project and is free in his own files, as for a redirection; Law 7 binds members."""
        for label, form in SCRIPT_WRITERS:
            with self.subTest(label):
                r = self.assertRefused(form.replace("{}", "docs/x.md"), "Law 1", agent_id=None)
                self.assertIn(ARG_WORDING, r.reason)
                self.assertRefused(form.replace("{}", "ledger/tickets/SPD-001.md"), "generated", agent_id=None)
                self.assertSilent(form.replace("{}", "/Users/Nobody/x"), agent_id=None)
                self.assertSilent(form.replace("{}", ".claude/x"), agent_id=None)  # his own .claude/**
        for command in ("awk 'BEGIN{system(\"git push\")}'", "awk 'BEGIN{print \"x\" | \"git push\"}'",
                        "awk '{print > f}' a.tar", "sed -n 'W out/f' a.tar"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=None)
        self.assertRefused("awk 'BEGIN{system(\"%s --as %s member log hi\")}'" % (self.spud_cli, AGENT_A), "Law 5", agent_id=None)


PROBE = "/tmp/spd-127-probe"  # the scratch directory D of Spud's probe of zsh 5.9 -f and bash 3.2, 2026-09-18
NOBODY = "/Users/Nobody"  # a directory outside every registered project and every temp root, as OutsideProjectTest's


class TargetResolutionTest(BashHookCase):
    """SPD-127: SPD-121 put the line's own value in a file a command names as an operand (arg_writes.resolved), because the
    differential over the 3477 commands spudagents ran showed that is how a member writes in its scratchpad: with the raw
    word 75 of those commands were refused, 70 of them a variable the line assigns pointing at the session scratchpad.
    The same line's redirection (`S=<scratchpad>; echo hi > $S/f`), its tee operand and a git call's own write option kept
    the raw word and the unresolvable-target refusal, so one line's halves answered differently.  Spud's decision: resolve,
    everywhere, with the one function, at the point of the walk that holds the value the shell uses there.

    A member in a worktree cannot run a shell (SPD-094), so Spud probed the two readings himself, 2026-09-18 in zsh 5.9 -f
    and bash 3.2, recording which file each line made in a scratch directory D; every line asserted here is one of them.

    Both shells make a.f for `S=$D/a; echo hi > $S.f` and its `>>`, `2>`, `export`, `&&` and `cd /tmp &&` forms -- the
    value decides, not the directory -- and a.t for `... | tee $S.t`.  A prefix assignment on the command itself reaches
    neither its redirection nor its arguments (`S=$D/a; S=$D/b echo hi > $S.f` made a.f, `S=$D/a; S=$D/b tee $S.t` made
    a.t, `S=$D/a; S=$D/b mkdir $S.m` made a.m), so the target is read before the command's own words and the prefix's value
    never counts.  An assignment-only command's own redirection is the one place the shells part (`S=$D/a; S=$D/b > $S.f`
    made a.f in zsh and b.f in bash), so both readings are recorded and neither shell's decides alone.  A later assignment
    wins in both (`S=$D/a; S=$D/b; echo hi > $S.f` made b.f); a loop's variable and a value the hook doubts settle nothing
    and keep the refusal the raw word earns, which refuses rather than name one of the two values.

    AGENT_A plans home:tests/** and home:bin/spud, and the home is the cwd."""

    def setUp(self):
        super().setUp()
        home = self.home.path
        for d in ("tests/out", "docs", "ledger/tickets", "reports", "tests/fake/.git/hooks"):
            (home / d).mkdir(parents=True, exist_ok=True)
        for f in ("tests/keep.py", "docs/x.md", "ledger/tickets/SPD-001.md"):
            (home / f).write_text("orig\n", encoding="utf-8")
        self.scratchpad = "/private/tmp/claude-%d/-Users-Someone-Personal-Spud/%s/scratchpad" % (os.getuid(), SESSION)

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def targets(self, command):
        """The redirection and tee targets the analysis recorded, in order: the words bash_reason holds to the path rule."""
        return [t for t, _cwds in self.analysis(command).redirects]

    # -- what the analysis records (Spud's probe, line by line) ------------------------

    def test_the_value_the_line_settled_is_in_the_targets_place(self):
        for command, recorded in (("S=%s/a; echo hi > $S.f" % PROBE, ["%s/a.f" % PROBE]),
                                  ("S=%s/a; echo hi >> ${S}.f" % PROBE, ["%s/a.f" % PROBE]),
                                  ("S=%s/a; echo hi 2> $S.f" % PROBE, ["%s/a.f" % PROBE]),
                                  ("S=%s/a; cd /tmp && echo hi > $S.f" % PROBE, ["%s/a.f" % PROBE]),
                                  ("S=%s/a; export S; echo hi > $S.f" % PROBE, ["%s/a.f" % PROBE]),
                                  ("S=%s/a && echo hi > $S.f" % PROBE, ["%s/a.f" % PROBE]),
                                  ("S=%s/a; echo hi | tee $S.t" % PROBE, ["%s/a.t" % PROBE])):
            with self.subTest(command):
                self.assertEqual(self.targets(command), recorded)

    def test_a_prefix_assignment_never_reaches_the_targets_of_its_own_command(self):
        """Both shells opened the value the line had before the command, so the target is read before the command's words;
        where that value is not the line's own the word stays raw, which refuses rather than name the prefix's."""
        both = "S=%s/a; S=%s/b %%s" % (PROBE, PROBE)
        self.assertEqual(self.targets(both % "echo hi > $S.f"), ["%s/a.f" % PROBE])
        self.assertEqual(self.targets(both % "tee $S.t"), ["$S.t"])  # the tee operand is read after the prefix doubts it
        self.assertEqual(self.targets("S=%s/a echo hi > $S.f" % PROBE), ["$S.f"])  # S unset before: the shells made `.f`
        a = self.analysis(both % "mkdir $S.m")  # SPD-121's own reading, unchanged
        self.assertEqual(([w[1] for w in a.arg_writes], self.targets(both % "mkdir $S.m")), (["$S.m"], []))

    def test_an_assignment_only_commands_redirection_records_both_readings(self):
        """The one place the shells differ: zsh opens the redirection with the value before the command, bash with the one
        the command assigns.  Both are recorded, as hidden_option reads both of sed's (SPD-121); a reading the hook cannot
        settle stays raw and keeps the refusal it earns, so neither shell's reading decides alone."""
        self.assertEqual(self.targets("S=%s/a; S=%s/b > $S.f" % (PROBE, PROBE)), ["%s/a.f" % PROBE, "%s/b.f" % PROBE])
        self.assertEqual(self.targets("S=%s/b > $S.f" % PROBE), ["$S.f", "%s/b.f" % PROBE])  # zsh's `.f` is unresolvable

    def test_a_value_that_refuses_to_settle_leaves_the_target_raw(self):
        """resolved's own conditions, now read for a redirection and a tee as well as for a write by argument: a value
        holding a blank (bash would split it), an array, an expansion, or a name the shells set themselves.  A glob
        character counts quoted as well as bare (SPD-121 settled a quoted one, which is zsh's reading of `S='docs*';
        rm $S/f` and not bash's, where the unquoted expansion's `*` expands): the value settles in neither shell's
        reading now, and the raw word keeps the refusal it earns."""
        for command in ("S='docs x'; echo hi > $S/f", "S=(docs tests); echo hi > $S/f", "S=$T; echo hi > $S/f",
                        "PWD=docs; echo hi > $PWD/f", "S=docs*; echo hi > $S/f", "S='docs*'; echo hi > $S/f",
                        'S="docs?"; echo hi > $S/f', "S='doc[s]'; echo hi | tee $S/f", "S='docs{1,2}'; echo hi > $S/f"):
            with self.subTest(command):
                self.assertEqual(self.targets(command), [command.rsplit(" ", 1)[1]])
                self.assertRefused(command, VARIABLE_WORDING)
        a = self.analysis("S='docs*'; rm $S/f")  # the same reading for a write by argument, which SPD-121 settled
        self.assertEqual([w[1] for w in a.arg_writes], ["$S/f"])

    def test_a_later_assignment_a_compound_and_a_loop(self):
        """A later assignment wins in both shells; an assignment the hook doubts (a compound command's) settles nothing,
        and the raw word keeps the refusal it had.  A loop's own variable over words the line settles is read once per
        word (SPD-146), and over words it does not settle stays raw."""
        self.assertEqual(self.targets("S=%s/a; S=%s/b; echo hi > $S.f" % (PROBE, PROBE)), ["%s/b.f" % PROBE])
        self.assertEqual(self.targets("S=%s/a; { S=%s/b; }; echo hi > $S.f" % (PROBE, PROBE)), ["$S.f"])
        self.assertEqual(self.targets("for S in %s/a; do echo hi > $S.f; done" % PROBE), ["%s/a.f" % PROBE])
        self.assertEqual(self.targets("for S in $X; do echo hi > $S.f; done"), ["$S.f"])

    # -- what the hook answers ---------------------------------------------------------

    def test_the_differentials_own_shape_is_silent_for_a_member(self):
        """The 70: a member writing into its session scratchpad through a variable it set on the line.  The `mkdir` half
        was already silent (SPD-121); the redirection and the tee beside it were refused."""
        for command in ("S=%s; echo hi > $S/f" % self.scratchpad,
                        "S=%s; echo hi | tee $S/f" % self.scratchpad,
                        "S=%s; mkdir -p $S/base && echo hi > $S/base/f" % self.scratchpad,
                        "S=%s; printf x | tee -a ${S}/f" % self.scratchpad,
                        "S=%s; echo hi > $S/f 2> $S/err" % self.scratchpad):
            with self.subTest(command):
                self.assertSilent(command)

    def test_a_resolved_target_is_checked_by_the_path_rule_as_a_spelled_one_is(self):
        for command, needle, path in (("S=docs; echo x > $S/x.md", "deliverables", "docs/x.md"),
                                      ("S=docs; printf x | tee $S/x.md", "deliverables", "docs/x.md"),
                                      ("S=ledger/tickets; echo x > $S/SPD-001.md", "generated", "ledger/tickets/SPD-001.md"),
                                      ("S=%s; echo x > $S/.zshrc" % NOBODY, OUTSIDE, ".zshrc"),
                                      ("S=%s; echo x >> ${S}/notes.txt" % NOBODY, OUTSIDE, "notes.txt"),
                                      ("S=tests/fake/.git/hooks; echo x > $S/post-index-change", GIT_DIR_WORDING,
                                       "tests/fake/.git/hooks/post-index-change")):
            with self.subTest(command):
                r = self.assertRefused(command, needle)
                self.assertIn(path, r.reason)
                self.assertNotIn(VARIABLE_WORDING, r.reason)  # the path rule's reason, not the unresolvable-target one
        # The state directory, which the raw-text regex does not see here (`.spud` is followed by `;`, not `/`), is
        # refused in the database's words for every caller, with no Law 1 before them.
        for agent_id in (AGENT_A, None):
            with self.subTest(agent_id=agent_id):
                r = self.assertRefused("S=.spud; echo x > $S/pycache/x", DB_WORDING, agent_id)
                self.assertNotIn("Law", r.reason)

    def test_a_target_the_line_does_not_settle_keeps_its_refusal(self):
        for command in ("echo x > $S/x.md", "printf x | tee $S/x.md", "echo x > $(pwd)/x.md",
                        "for S in $X docs; do echo x > $S/x.md; done", "S=$OTHER; echo x > $S/x.md"):
            with self.subTest(command):
                self.assertRefused(command, VARIABLE_WORDING)
                self.assertRefused(command, VARIABLE_WORDING, agent_id=None)  # Spud too, since SPD-091

    def test_spud_is_refused_an_unsettled_target_and_reads_a_settled_one(self):
        """SPD-091 (Eric's call): a redirection, a tee or a git call's own file whose word holds an expansion the line does
        not settle refuses Spud as it refuses a member -- the hook cannot tell whether it lands on a rendered ledger note or
        another project's file, and Laws 1 and 5 hold for him -- while a value the line settles (SPD-127) is read, so it
        passes where the path it names is his to write.  Probed with tests/probes/shell_probe.py (zsh 5.9 -f -o
        nobareglobqual, zsh -f, bash 3.2): `T=d; echo x > $T/f` and `echo y | tee $T/g` wrote under the value the line
        assigned, `tee $(cat list)` and `` tee `cat list`2 `` wrote the paths the file named, which no reading of the line
        can know, and `S=d > $S/k` wrote d/k in bash and tried /k in both zshes.  git runs in no probe sandbox, so its own
        write options rest on SPD-049's probes."""
        for command in ("echo x > $T", "echo x >> \"$T\"/f", "echo x | tee $(cat f)", "printf x | tee -a `cat f`",
                        "echo x | tee ${T}", "git archive -o $T HEAD", "git diff --output=$(cat f)"):
            with self.subTest(command):
                self.assertRefused(command, VARIABLE_WORDING)
                r = self.assertRefused(command, VARIABLE_WORDING, agent_id=None)
                self.assertNotIn("Law 1", r.reason)  # not a Law 1 path: a path the hook cannot read at all
        for command in ("S=%s; echo hi > $S/f" % self.scratchpad, "S=%s; echo hi | tee $S/f" % self.scratchpad,
                        "S=%s; git archive -o $S/a.tar HEAD" % self.scratchpad, "S=.claude; echo x > $S/x"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=None)
        for command in ("S=docs; echo x > $S/x.md", "S=docs; printf x | tee $S/x.md"):
            with self.subTest(command):
                self.assertRefused(command, "Law 1", agent_id=None)

    def test_a_prefix_assignment_moves_no_write(self):
        """The value before the command is the one both shells open: a prefix assignment neither launders a write into the
        scratchpad nor moves one out of it, and a tee operand under one stays raw and refused."""
        self.assertRefused("S=docs; S=%s echo x > $S/x.md" % self.scratchpad, "deliverables")
        self.assertSilent("S=%s; S=docs echo x > $S/x.md" % self.scratchpad)
        self.assertRefused("S=%s; S=docs tee $S/x.md" % self.scratchpad, VARIABLE_WORDING)

    def test_both_readings_of_an_assignment_only_command_are_checked(self):
        self.assertRefused("S=%s; S=docs > $S/x.md" % self.scratchpad, "deliverables")  # bash's reading
        self.assertRefused("S=docs; S=%s > $S/x.md" % self.scratchpad, "deliverables")  # zsh's reading
        self.assertSilent("S=%s; S=%s/b > $S/f" % (self.scratchpad, self.scratchpad))   # both inside the scratchpad
        self.assertRefused("S=docs > $S/x.md", VARIABLE_WORDING)                        # zsh's reading is unresolvable
        self.assertRefused("S=docs > $S/x.md", VARIABLE_WORDING, agent_id=None)         # Spud too, since SPD-091
        self.assertSilent("S=%s; S=%s/b > $S/f" % (self.scratchpad, self.scratchpad), agent_id=None)  # both settled

    def test_a_git_calls_own_write_option_and_trace_variable(self):
        home = self.home.path
        r = self.assertRefused("S=%s/docs; git diff --output $S/d.txt" % home, "deliverables")
        self.assertIn("docs/d.txt", r.reason)
        self.assertSilent("S=%s/tests/out; git diff --output $S/d.txt" % home)
        r = self.assertRefused("S=%s/docs; GIT_TRACE=$S/trace.log git status" % home, "deliverables")
        self.assertIn("docs/trace.log", r.reason)
        self.assertSilent("S=%s/tests/out; GIT_TRACE2_EVENT=$S/trace.log git status" % home)
        # The settled value decides the shape git reads as well as the path git writes: a relative trace value and a
        # descriptor write nothing (probed on SPD-049), where the raw word refused a member.
        for command in ("T=docs/trace.log; GIT_TRACE=$T git status", "T=1; GIT_TRACE=$T git status"):
            with self.subTest(command):
                self.assertSilent(command)
        self.assertRefused("GIT_TRACE=$T git status", "cannot resolve")  # nothing settled: fail closed, as before

    def test_spuds_own_targets_follow_the_same_reading(self):
        self.assertRefused("S=docs; echo x > $S/x.md", "Law 1", agent_id=None)
        self.assertRefused("S=ledger/tickets; printf x | tee $S/SPD-001.md", "generated", agent_id=None)
        self.assertRefused("S=%s/docs; GIT_TRACE=$S/trace.log git status" % self.home.path, "Law 1", agent_id=None)
        self.assertSilent("S=%s; echo hi > $S/f" % self.scratchpad, agent_id=None)
        self.assertSilent("S=%s; echo x > $S/.zshrc" % NOBODY, agent_id=None)

    def test_the_allow_is_unchanged_and_the_resolved_target_still_decides(self):
        """A spud call is allowed past the harness's prompt only when the line sets nothing but a SPUD_HOME the hook
        checks (SPD-032, vouched_spud_call), so a line that assigns the variable its redirection names is silent whatever
        that variable holds -- and the file it resolves to is still held to the path rule beside it."""
        log = "%s --as %s member log x" % (self.spud_cli, AGENT_A)
        self.assertAllowed(log + " > /dev/null")
        self.assertSilent("N=/dev/null; %s > $N" % log)
        self.assertSilent("N=tests/out/log.txt; %s > $N" % log)
        self.assertRefused("N=docs; %s > $N/log.txt" % log, "deliverables")


class LoopWordTargetTest(BashHookCase):
    """SPD-146: SPD-121 refused every write target holding an expansion the hook cannot settle, and SPD-127 settled only
    what the line assigns plainly, so a for loop's variable and a name built with a substitution stayed raw and refused,
    though a loop's words are on the line.  SPD-126's differential over the 945 Bash commands spudagents ran that name
    dd, sort, curl, mkfifo, mktemp, split, perl, tar, patch or wget found 16 new refusals, 12 of exactly this shape, every
    one writing into the session's own scratchpad.

    A `for NAME in WORD ...` loop over words the line settles -- literal words, values the line settled, or a glob that
    matches files now -- gives NAME one value per word, and a write target naming NAME is read once per value, as a glob
    target is read once per match.  A loop the line cannot settle (`$@`, an unsettled `$x`, a substitution, a word that may
    start with `-`, a brace list, a glob matching nothing) stays refused.  `$(basename WORD [SUFFIX])` is settled: exactly,
    where WORD settles; where it does not, as one name inside the directory it is written relative to (the result holds no
    `/`), while the substitution stands in double quotes after something the word spells, so it is one word and no option.
    AGENT_A plans home:tests/** and home:bin/spud, and the home is the cwd."""

    def setUp(self):
        super().setUp()
        home = self.home.path
        for d in ("tests/out", "tests/glob", "docs"):
            (home / d).mkdir(parents=True, exist_ok=True)
        for f in ("tests/glob/g1.txt", "tests/glob/g2.txt", "docs/x.md"):
            (home / f).write_text("orig\n", encoding="utf-8")
        self.scratchpad = "/private/tmp/claude-%d/-Users-Someone-Personal-Spud/%s/scratchpad" % (os.getuid(), SESSION)

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def writes(self, command):
        """Every write target the analysis recorded, in order: the redirections' and tee's, then the writes by argument."""
        a = self.analysis(command)
        return [t for t, _cwds in a.redirects] + [w[1] for w in a.arg_writes]

    # -- a for loop's words ------------------------------------------------------------

    def test_the_tickets_own_line_reads_each_word(self):
        self.assertEqual(self.writes("for f in a b; do touch tests/$f; done"), ["tests/a", "tests/b"])
        self.assertSilent("for f in a b; do touch tests/$f; done")

    def test_every_write_channel_reads_the_loop_once_per_word(self):
        for command, recorded in (
            ("for f in a b; do echo x > tests/$f.txt; done", ["tests/a.txt", "tests/b.txt"]),
            ("for f in a b; do echo x | tee tests/$f; done", ["tests/a", "tests/b"]),
            ("for f in a b\ndo\n  mkdir -p tests/$f\ndone", ["tests/a", "tests/b"]),
            ("S=tests; for f in a b; do touch $S/$f; done", ["tests/a", "tests/b"]),
            ("S=tests/out; for f in $S/x $S/y; do touch $f; done", ["tests/out/x", "tests/out/y"]),
            ("for a in x y; do for b in 1 2; do touch tests/$a$b; done; done", ["tests/x1", "tests/x2", "tests/y1", "tests/y2"]),
            ("for f in a b; do curl -sS -o tests/$f.html https://example.com/$f; done", ["tests/a.html", "tests/b.html"]),
        ):
            with self.subTest(command):
                self.assertEqual(self.writes(command), recorded)
                self.assertSilent(command)

    def test_one_value_stands_for_the_name_in_every_word_of_a_command(self):
        """Each reading is one pass of the body: a source and a destination naming the variable take the same value."""
        a = self.analysis("for f in a b; do cp tests/$f.in tests/out/$f; done")
        self.assertEqual([(w[1], w[3]) for w in a.arg_writes], [("tests/out/a", ("tests/a.in",)), ("tests/out/b", ("tests/b.in",))])

    def test_a_glob_list_is_read_once_per_file_it_matches(self):
        self.assertEqual(self.writes("for f in tests/glob/*.txt; do cp $f $f.bak; done"),
                         ["tests/glob/g1.txt.bak", "tests/glob/g2.txt.bak"])
        self.assertSilent("for f in tests/glob/*.txt; do cp $f $f.bak; done")
        r = self.assertRefused("for f in tests/glob/*.txt; do cp $f docs/; done", "deliverables")
        self.assertIn("docs/g1.txt", r.reason)

    def test_each_word_is_held_to_the_path_rule(self):
        r = self.assertRefused("for d in tests docs; do echo x > $d/x.md; done", "deliverables")
        self.assertIn("docs/x.md", r.reason)
        self.assertNotIn(VARIABLE_WORDING, r.reason)
        self.assertRefused("for f in a .git; do mkdir -p tests/$f/hooks; done", GIT_DIR_WORDING)
        self.assertRefused("for f in a b; do touch docs/$f.md; done", "deliverables")

    def test_a_loop_the_line_does_not_settle_stays_refused(self):
        for command in ('for f in "$@"; do touch tests/$f; done', "for f in $X; do touch tests/$f; done",
                        "for f in $(ls); do touch tests/$f; done", "for f in `ls`; do touch tests/$f; done",
                        "for f; do touch tests/$f; done", "for f in 'a b'; do touch tests/$f; done",
                        "for f in -rf x; do touch tests/$f; done", "for f in a{1,2}; do touch tests/$f; done",
                        "for f in tests/none*.txt; do touch $f.x; done", "for f in 'a*'; do touch tests/$f; done",
                        "for f in ~/x; do touch tests/$f; done", "for a b in 1 2; do touch tests/$a; done",
                        "while read f; do touch tests/$f; done", "select f in a b; do touch tests/$f; done",
                        "for f in a b; do read f; touch tests/$f; done", "for f in a b; do f=$X; touch tests/$f; done",
                        "for f in a b; do unset f; touch tests/$f; done",
                        "for f in a b; do g() { touch tests/$f; }; g; done",
                        "for f in a b; do :; done; touch tests/$f",
                        "for f in a; do for f in b; do :; done; touch tests/$f; done"):
            with self.subTest(command):
                self.assertRefused(command, VARIABLE_WORDING)

    # -- basename -----------------------------------------------------------------------

    def test_a_basename_of_a_settled_word_is_its_result(self):
        for command, recorded in (
            ('touch "tests/$(basename /x/y/z.txt)"', ["tests/z.txt"]),
            ("touch tests/$(basename /x/y/z.txt)", ["tests/z.txt"]),
            ('touch "tests/$(basename /x/y/z.txt .txt).md"', ["tests/z.md"]),
            ('touch "tests/$(basename -- /x/y/z.txt)"', ["tests/z.txt"]),
            ('touch "tests/$(basename /x/y/)"', ["tests/y"]),
            ('F=/x/y/z.txt; touch "tests/$(basename "$F")"', ["tests/z.txt"]),
            ('F=/x/y/z.txt; out=$(basename "$F"); touch "tests/$out"', ["tests/z.txt"]),
            ('F=/x/y/z.txt; out="$(basename $F)"; touch tests/$out', ["tests/z.txt"]),
        ):
            with self.subTest(command):
                self.assertEqual(self.writes(command), recorded)
                self.assertSilent(command)
        r = self.assertRefused('touch "docs/$(basename /x/y/z.md)"', "deliverables")
        self.assertIn("docs/z.md", r.reason)

    def test_a_basename_of_an_unsettled_word_is_one_name_in_its_directory(self):
        """basename's result holds no `/`: quoted after a spelled prefix it is one entry of that directory, which a glob
        covering every name there allows and no narrower glob does."""
        for command in ('touch "tests/$(basename $P)"', 'touch "tests/$(basename "$P" .tar).txt"',
                        'out=$(basename "$P"); touch "tests/$out"', 'echo x > "tests/out/$(basename $P)"'):
            with self.subTest(command):
                self.assertSilent(command)
        for command in ('touch "docs/$(basename $P)"', 'out=$(basename "$P"); touch "docs/$out"'):
            with self.subTest(command):
                self.assertRefused(command, "deliverables")

    def test_a_substitution_basename_does_not_settle_stays_refused(self):
        for command in ("touch tests/$(basename $P)", 'touch "$(basename $P)"', 'out=$(basename "$P"); touch tests/$out',
                        'out=$(basename "$P"); touch "$out"', 'touch "tests/$(basename -a $P)"', 'touch "tests/$(dirname $P)"',
                        'touch "tests/$(basename $P | tr a b)"', 'touch "tests/$(basename $(pwd))"', 'touch "tests/$(cat f)"',
                        'touch "tests/$(basename /)"', "touch \"tests/$(basename '/x/a b')\"",
                        'basename() { echo ../../docs/x.md; }; touch "tests/$(basename a)"',
                        'PATH=/tmp/p:$PATH; touch "tests/$(basename $P)"',
                        'if true; then out=$(basename /x/z); fi; touch "tests/$out"',
                        'out=$(basename /x/z); read out; touch "tests/$out"', 'out=$(basename /x/z) touch "tests/$out"'):
            with self.subTest(command):
                self.assertRefused(command, VARIABLE_WORDING)

    def test_an_assigned_basename_is_what_it_printed_where_it_ran(self):
        """The substitution runs where the assignment does, so a later assignment of its operand changes nothing."""
        self.assertEqual(self.writes('F=/x/.git; out=$(basename "$F"); F=/x/ok; touch "tests/$out"'), ["tests/.git"])
        self.assertRefused('F=/x/.git; out=$(basename "$F"); F=/x/ok; touch "tests/$out"', GIT_DIR_WORDING)
        self.assertEqual(self.writes('out=$(basename /x/z); out=tests/y; touch "$out"'), ["tests/y"])

    # -- an assignment in a loop body (SPD-221) -------------------------------------------

    def test_a_basename_assigned_in_a_loop_body_is_read_once_per_word(self):
        """SPD-221: `out=$(basename "$f")` straight in a settled for loop's body, certain in every pass, gives `$out` the
        value it printed for each of the loop's words, read with that word wherever the write names both."""
        for command, recorded in (
            ('for f in a b; do out=$(basename "$f"); touch "tests/$out"; done', ["tests/a", "tests/b"]),
            ("for f in /x/a /y/b; do out=$(basename $f); touch tests/$out; done", ["tests/a", "tests/b"]),
            ('for f in /x/a.txt /y/b.txt; do out=$(basename "$f" .txt); echo x > "tests/$out.md"; done',
             ["tests/a.md", "tests/b.md"]),
            ('for f in tests/glob/*.txt; do out=$(basename "$f" .txt); cp "$f" "tests/out/$out.bak"; done',
             ["tests/out/g1.bak", "tests/out/g2.bak"]),
            ('for a in x y; do for b in 1 2; do out=$(basename "/p/$a$b"); touch "tests/$out"; done; done',
             ["tests/x1", "tests/x2", "tests/y1", "tests/y2"]),
            ('for f in a b; do out=$(basename "$f")\n  touch "tests/$out"\ndone', ["tests/a", "tests/b"]),
            ('for f in a b; do out=$(basename "$f") && touch "tests/$out"; done', ["tests/a", "tests/b"]),
            ('for f in a b; do out=$(basename /x/z); touch "tests/$out$f"; done', ["tests/za", "tests/zb"]),
            ('for f in /x/a /y/b; do out=$(basename "$f"); o2=$(basename "/q/$out.x"); touch "tests/$o2"; done',
             ["tests/a.x", "tests/b.x"]),
        ):
            with self.subTest(command):
                self.assertEqual(self.writes(command), recorded)
                self.assertSilent(command)

    def test_a_loop_body_basename_keeps_its_own_word(self):
        """One pass sets both names: a write naming the loop's word and the basename takes them from the same pass."""
        a = self.analysis('for f in a b; do out=$(basename "/x/$f.in"); cp tests/$f.in "tests/out/$out"; done')
        self.assertEqual([(w[1], w[3]) for w in a.arg_writes],
                         [("tests/out/a.in", ("tests/a.in",)), ("tests/out/b.in", ("tests/b.in",))])

    def test_each_loop_body_basename_is_held_to_the_path_rule(self):
        r = self.assertRefused('for d in /x/tests /x/docs; do out=$(basename "$d"); echo x > "$out/x.md"; done', "deliverables")
        self.assertIn("docs/x.md", r.reason)
        self.assertRefused('for f in /x/a /x/.git; do out=$(basename "$f"); mkdir -p "tests/$out/hooks"; done', GIT_DIR_WORDING)
        self.assertSilent('for f in a b; do out=$(basename "$P$f"); touch "tests/$out"; done')  # one name in tests/
        self.assertRefused('for f in a b; do out=$(basename "$P$f"); touch "docs/$out"; done', "deliverables")
        r = self.assertRefused('for f in tests/a /x/b; do out=$(basename "$f"); touch "$out"; done', "deliverables")
        self.assertIn("home:a ", r.reason)

    def test_a_loop_body_basename_holds_wherever_its_loop_is_read(self):
        """The same reading in a function body's loop, a shell's `-c` text, a loop after another that assigned the same
        name, and a line zsh and bash read apart."""
        for command, recorded in (
            ('g() { for f in a b; do out=$(basename "$f"); touch "tests/$out"; done; }', ["tests/a", "tests/b"]),
            ("""sh -c 'for f in a b; do out=$(basename "$f"); touch "tests/$out"; done'""", ["tests/a", "tests/b"]),
            ('for f in a; do out=$(basename "$f"); touch "tests/$out"; done; for g in b; do out=$(basename "$g"); touch "tests/$out"; done',
             ["tests/a", "tests/b"]),
            ('for f in a b; do out=$(basename "$f"); touch "tests/$out"; done; ls (a|b)', ["tests/a", "tests/b"]),
        ):
            with self.subTest(command):
                self.assertEqual(sorted(set(self.writes(command))), recorded)
                self.assertSilent(command)

    def test_a_loop_body_basename_that_may_not_hold_stays_refused(self):
        """Refused wherever the assignment may not have run in this pass, may not persist, or something may have
        changed the name or the loop's word since: a condition, a pipe, the background, a subshell, a group, a function
        body, a command's prefix, a later assignment, read, unset, an attribute, another loop over either name, eval,
        code the hook does not read, and a write before the assignment or after the loop."""
        for command in (
            'for f in a b; do if true; then out=$(basename "$f"); fi; touch "tests/$out"; done',
            'for f in a b; do true && out=$(basename "$f"); touch "tests/$out"; done',
            'for f in a b; do false || out=$(basename "$f"); touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f") | cat; touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f") & touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f") && true & touch "tests/$out"; done',
            'for f in a b; do (out=$(basename "$f")); touch "tests/$out"; done',
            'for f in a b; do { out=$(basename "$f"); }; touch "tests/$out"; done',
            'for f in a b; do g() { out=$(basename "$f"); }; g; touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f") true; touch "tests/$out"; done',
            'for f in a b; do touch "tests/$out"; out=$(basename "$f"); done',
            'for f in a b; do out=$(basename "$f"); done; touch "tests/$out"',
            'for f in a b; do out=$(basename "$f"); out=$X; touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f"); out+=x; touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f"); read out; touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f"); unset out; touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f"); declare -n out=X; touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f"); for out in $X; do :; done; touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f"); f=../docs; touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f"); for f in b a; do touch "tests/$f/$out"; done; done',
            "for f in a b; do out=$(basename \"$f\"); eval 'out=../docs/x'; touch \"tests/$out\"; done",
            'for f in a b; do out=$(basename "$f"); source ./x.sh; touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f"); touch "tests/$out"; done; echo ${out:=q}',
            'g() { out=../docs; }; for f in a b; do out=$(basename "$f"); g; touch "tests/$out"; done',
            'basename() { echo ../../docs/x.md; }; for f in a b; do out=$(basename "$f"); touch "tests/$out"; done',
            'PATH=/tmp/p:$PATH; for f in a b; do out=$(basename "$f"); touch "tests/$out"; done',
            'for f in a b; do out=$(basename -a "$f"); touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f" | tr a b); touch "tests/$out"; done',
            'for f in a b; do out=$(dirname "$f"); touch "tests/$out"; done',
            'for f in "$@"; do out=$(basename "$f"); touch tests/$out; done',
            'while read f; do out=$(basename "$f"); touch tests/$out; done',
            'for f in a b; do out=$(basename "$P"); touch tests/$out; done',
            'for f in a b; do out=$(basename "$P"); touch "$out"; done',
            'for f in a b; do coproc out=$(basename "$f"); touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f"); let out=3; touch "tests/$out"; done',
            'for f in a b; do out=$(basename "$f"); x=$(out=zz); : "${out:=q}"; touch "tests/$out"; done',
            """sh -c 'for f in a b; do out=$(basename "$f")'; touch "tests/$out\"""",
        ):
            with self.subTest(command):
                self.assertRefused(command, VARIABLE_WORDING)

    # -- the differential's own lines ---------------------------------------------------

    def test_the_differentials_lines_are_silent_in_the_scratchpad(self):
        s = self.scratchpad
        for command, recorded in (
            ('S=%s; for who in juno hana gil; do curl -sS -b jar.txt -o "$S/$who.html" "https://example.com/u/$who"; done' % s,
             ["%s/%s.html" % (s, who) for who in ("juno", "hana", "gil")]),
            ('S=%s; for p in /data/x/a.tar.gz /data/y/b.zip; do curl -sSf -o "$S/$(basename $p)" "https://example.com$p"; done' % s,
             ["%s/a.tar.gz" % s, "%s/b.zip" % s]),
            ('S=%s; f=/data/x/c.tar; out=$(basename "$f"); curl -sSfL https://example.com/c -o "$S/$out"' % s, ["%s/c.tar" % s]),
            # SPD-221: the assignment in the loop's body, which SPD-146 left refused
            ('S=%s; for f in /data/x/a.tar.gz /data/y/b.zip; do out=$(basename "$f"); curl -sSfL -o "$S/$out" "https://example.com$f"; done' % s,
             ["%s/a.tar.gz" % s, "%s/b.zip" % s]),
        ):
            with self.subTest(command):
                self.assertEqual(self.writes(command), recorded)
                self.assertSilent(command)
        self.assertSilent('S=%s; out=$(basename "$F"); curl -sSfL https://example.com/c -o "$S/$out"' % s)
        self.assertRefused('for who in juno hana; do curl -sS -o "docs/$who.html" https://example.com/u/$who; done',
                           "deliverables")


if __name__ == "__main__":
    unittest.main()
