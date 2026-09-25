"""PreToolUse(Bash): the laws as the Bash hook reads a line -- git's write verbs (Law 7), spud calls (Laws 5 and 6), the
database and `spud hook`, Spud's own redirections (Law 1) -- the allow a well-formed spud call earns, and the shell model of
wrappers and the working directory."""

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import PROGRAM, SPUD
from hookcase import AGENT_A, AGENT_B, AGENT_C, AGENT_D, HOOK_CACHES, INLINE_WORDING, LEAK_ALLOWED, SCRIPT_WORDING, STATE, BashHookCase
from hookcase import case_insensitive_fs, plant_git_dir, spellings, tables_changed


class PreBashTest(BashHookCase):

    def test_law_7_git_verbs_for_members(self):
        for verb in ("commit -m x", "add .", "stash", "stash pop", "checkout main", "switch -c x", "rebase main", "reset --hard", "push", "merge x", "cherry-pick abc", "worktree add ../x", "branch -D x", "branch new", "pull", "tag v1", "am x.patch", "apply x.patch", "revert HEAD", "restore f", "rm f", "mv a b", "clean -fd"):
            r = self.assertRefused("git " + verb, "Law 7")
            self.assertIn("git " + verb.split()[0], r.reason)
        for ok in ("git status", "git log --oneline -5", "git diff", "git show HEAD", "git blame f", "git worktree list --porcelain", "git branch", "git branch -a", "git branch --show-current", "git rev-parse HEAD", "git ls-files", "git grep x", "git stash list", "git fetch", "git remote -v"):
            self.assertSilent(ok)
        self.assertEqual(len(self.denied()), 23)

    def test_git_hidden_in_shell_constructs(self):
        for cmd in (
            "cd /tmp && git commit -m x",
            "ls; git add .",
            "true || git push",
            "echo $(git commit -m x)",
            "echo `git stash`",
            'echo "$(git commit -m x)"',
            "sh -c 'git commit -m x'",
            'bash -lc "cd /x && git push"',
            "zsh -c \"git rebase main\"",
            "eval 'git checkout main'",
            "gi\"\"t commit",
            "g\\it commit",
            "'git' commit",
            "git -C /tmp commit -m x",
            "git -c user.name=x commit",
            "git --git-dir=/x/.git commit",
            "/usr/bin/git commit",
            "(git commit)",
            "{ git commit; }",
            "if git commit; then echo ok; fi",
            "env GIT_DIR=/x git commit",
            "nohup git push &",
            "xargs git add < list",
            "time git commit",
            "command git commit",
            "bash <<'EOF'\ngit commit -m x\nEOF",
            "python3 -c 'print(1)' && git   commit",
            "git\tcommit",
        ):
            self.assertRefused(cmd, "Law 7")

    def test_law_6_spud_mutations_for_members(self):
        for tail in ("ticket new --title x", "ticket move SPD-001 --status done", "ticket edit SPD-001 --title y", "--as spud member log hi", "init", "migrate", "import", "import --file x.md", "render", "backup", "settings sync", "config sync", "--json --as spud board", "member finish SPUD-001/01 --as spud --status done --outcome x",
                     "member resum --all", "member resum --all --dry-run", "--as %s member resum SPUD-001/01" % AGENT_A,
                     "backup --daily", "backup --daily --keep 3", "--as %s backup --daily" % AGENT_A, "schedule show", "schedule install", "schedule install --at 04:30",
                     "schedule uninstall", "--as %s schedule show" % AGENT_A, "--as spud schedule install"):
            self.assertRefused("%s %s" % (self.spud_cli, tail), "Law 6")
        self.assertIn("member resum", self.assertRefused("%s member resum --all" % self.spud_cli, "Law 6").reason)
        for verb in ("show", "install", "uninstall"):
            reason = self.assertRefused("%s schedule %s" % (self.spud_cli, verb), "Law 6").reason
            self.assertIn("spud schedule", reason)
            self.assertIn("proposal file", reason)
        self.assertRefused("%s ticket new --title x" % self.home.launcher, "Law 6")
        self.assertRefused("cd %s && python3.14 -I -S bin/spud ticket new --title x" % self.home.tool, "Law 6")
        self.assertRefused("spud ticket new --title x", "Law 6")
        self.assertRefused("%s ticket show SPD-001 && %s init" % (self.spud_cli, self.spud_cli), "Law 6")

    def test_spud_runs_backup_daily_and_schedule(self):
        for tail in ("backup --daily", "backup --daily --keep 3", "schedule show", "schedule show --at 04:30", "schedule install", "schedule install --at 04:30", "schedule uninstall"):
            self.assertAllowed("%s --as spud %s" % (self.spud_cli, tail), agent_id=None)
            self.assertAllowed("%s --json --as spud %s" % (self.spud_cli, tail), agent_id=None)

    def test_as_must_resolve_to_the_caller(self):
        lead, other = self.lead, self.other
        for who in (other["ref"], "%s/%s" % (self.team, other["lineage"]), AGENT_B, AGENT_D, "spud", "SPUD-999/Nobody"):
            self.assertRefused("%s --as %s member log hi" % (self.spud_cli, who), "--as")
        for who in (AGENT_A, lead["ref"], "%s/%s" % (self.team, lead["lineage"])):
            self.assertAllowed("%s --as %s member log hi" % (self.spud_cli, who))
            self.assertAllowed("%s --as=%s member log hi" % (self.spud_cli, who))
        # an unbound caller may not use --as at all
        r = self.assertRefused("%s --as %s member log hi" % (self.spud_cli, AGENT_D), "not bound", agent_id=AGENT_D)
        self.assertIn(AGENT_D, r.reason)
        # reads need no --as and are allowed
        for tail in ("board --brief", "events --member %s" % lead["ref"], "member show %s" % lead["ref"], "member list", "member list --ticket SPD-001",
                     "sql --readonly 'select 1'", "doctor", "ticket show SPD-001", "fleet", "card SPD-001", "proposal list"):
            self.assertAllowed("%s %s" % (self.spud_cli, tail))

    def test_hook_and_database_access_are_refused_for_everyone(self):
        for agent_id in (AGENT_A, None):
            self.assertRefused("%s hook PreToolUse" % self.spud_cli, "hook", agent_id=agent_id)
            self.assertRefused("echo '{}' | %s hook Stop" % self.spud_cli, "hook", agent_id=agent_id)
            for cmd in (
                "sqlite3 %s/.spud/ledger.db 'select 1'" % self.home.path,
                "/usr/bin/sqlite3 x.db",
                "python3 -c 'import sqlite3; sqlite3.connect(\"/x/ledger.db\")'",
                "python3.14 -m sqlite3 x.db",
                "cat .spud/ledger.db",
                "ls -la %s/.spud" % self.home.path,
                "cp ledger.db /tmp/x",
                "python3 - <<EOF\nimport sqlite3\nEOF",
                "node -e 'require(\"node:sqlite\")'",
                "rm -rf .spud/",
                "SQLITE3 x.db",
                "cat .SPUD/ledger.db",
                "cat Ledger.DB",
                "python3 -c 'import SQLite3'",
            ):
                self.assertRefused(cmd, "spud sql --readonly", agent_id=agent_id)
        self.assertAllowed("%s sql --readonly 'select count(*) from members'" % self.spud_cli, agent_id=None)

    def test_command_words_from_substitutions_are_refused_for_members(self):
        """Rooster's MEDIUM-3: a command word the hook cannot see is refused like an unresolvable variable."""
        for cmd in (
            "$(echo git) push",
            "git=$(which git); $git push",
            "sh -c \"$(printf 'git push')\"",
            "eval \"$(echo git push)\"",
            "$(printf %s git) push",
            "S=$(echo sqlite3); $S /tmp/copy.db",
            "`which git` push",
            "$(printf %s gi)t push",
        ):
            self.assertRefused(cmd, "spell the command out")
        for cmd in ("$(echo git) push", "`which git` push"):
            self.assertSilent(cmd, agent_id=None)
        self.assertSilent("echo $(git status)")
        self.assertSilent("x=$(date); echo $x")

    def test_law_1_redirections_for_spud(self):
        home = self.home.path
        for cmd in (
            "echo x > %s/bin/spud" % home,
            "echo x >> tests/new.py",
            "cat <<EOF > docs/spikes/x.md\nhi\nEOF",
            "printf x | tee ledger/tickets/SPD-001.md",
            "printf x | tee -a %s/reports/2026-09-12.md" % home,
            "echo x >| bin/x",
            "echo x &> bin/x",
            "ls >& bin/x",
            "cd tests && echo x > new.py",
            "cd %s/docs; echo x > spike.md" % home,
        ):
            self.assertRefused(cmd, "Law 1", agent_id=None)
        for ok in (
            "echo x > CLAUDE.md",
            "echo x >> %s/spud.config.json" % home,
            "echo x > .claude/settings.json",
            "echo x > ledger/Home.md",
            "echo x > ledger/Board.base",
            "echo x > ledger/_templates/ticket.md",
            "echo x > docs/superpowers/specs/x.md",
            "echo x > /tmp/x",
            "echo x > ~/.claude/projects/-Users-x/memory/x.md",
            "make 2>&1",
            "echo hi >&2",
            "ls > /dev/null",
            "cat < bin/spud",
            "T=/tmp; echo x > \"$T/x\"",  # a value the line settles is read (SPD-127)
        ):
            self.assertSilent(ok, agent_id=None)
        # SPD-091: a target the line does not settle, the environment's TMPDIR included, refuses Spud as it does a member
        self.assertRefused("echo x > \"$TMPDIR/x\"", "spell the path out", agent_id=None)
        self.assertEqual(len(self.denied()), 11)
        self.assertEqual(self.denied()[0]["data"]["tool_name"], "Bash")

    def test_member_redirections_follow_the_deliverables(self):
        self.assertSilent("echo x > tests/out.txt")
        self.assertSilent("echo x > /tmp/scratch.txt")
        self.assertRefused("echo x > docs/x.md", "deliverables")
        self.assertRefused("printf x | tee CLAUDE.md", "deliverables")
        self.assertRefused("echo x > ledger/teams/SPUD-001/X.md", "generated")

    def test_well_formed_spud_calls_are_allowed_and_everything_else_is_silent(self):
        self.assertAllowed("%s --as spud ticket new --title x" % self.spud_cli, agent_id=None)
        self.assertAllowed("%s --json board" % self.spud_cli, agent_id=None)
        self.assertAllowed("SPUD_HOME=%s %s --as spud board" % (self.home.path, self.spud_cli), agent_id=None)
        self.assertAllowed("%s --as %s member log 'a; b && c'" % (self.spud_cli, AGENT_A))
        self.assertAllowed("%s --as %s member log @- <<'EOF'\nDid a thing; git status was clean.\nEOF" % (self.spud_cli, AGENT_A))
        for cmd in ("ls -la", "python3.14 -I -S -m unittest discover -s tests -t tests", "%s board | head" % self.spud_cli, "%s bogus" % self.spud_cli, "%s board && ls" % self.spud_cli):
            self.assertSilent(cmd)
        self.assertEqual(self.denied(), [])
        # SPD-191: a line the hook cannot tokenize stood silent here since SPD-008; it passed every law, and is refused now
        # to every caller (UnreadableLineTest)
        self.assertRefused("echo 'unterminated", "the hook cannot read this line")

    def test_missing_command_is_denied(self):
        p = self.pre_bash("ls")
        p["tool_input"] = {}
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.decision), (0, "deny"), r)

    def test_spud_may_not_write_a_members_own_sections(self):
        lead = self.lead
        for who in (AGENT_A, lead["ref"], "%s/%s" % (self.team, lead["lineage"])):
            for tail in ("member log hi", "member result done", "member block why", "proposal file --title x"):
                self.assertRefused("%s --as %s %s" % (self.spud_cli, who, tail), "Law 5", agent_id=None)
            for tail in ("member show %s" % lead["ref"], "member list", "events", "board"):
                self.assertAllowed("%s --as %s %s" % (self.spud_cli, who, tail), agent_id=None)
        self.assertAllowed("%s --as spud member finish %s --status done --outcome x" % (self.spud_cli, lead["ref"]), agent_id=None)
        self.assertAllowed("%s --as spud member log hi" % self.spud_cli, agent_id=None)  # the CLI refuses it (exit 3); not the hook's call

    def test_a_spud_call_is_recognized_however_its_script_is_spelled(self):
        """SPD-029: Law 6's refusals depend on seeing a spud call.  A case variant or a fold of bin/spud's name is the
        same file on macOS, and a symlink by any name runs the launcher (it finds its program from its real path)."""
        home = self.home.tool  # the launcher's checkout
        (home / "bin").mkdir(exist_ok=True)
        (home / "bin" / "spud").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        (home / "tests").mkdir(exist_ok=True)
        (home / "tests" / "tool").symlink_to(home / "bin" / "spud")
        (home / "tests" / "other.py").write_text("print(1)\n", encoding="utf-8")
        for script in (str(home).upper() + "/BIN/SPUD", "%s/bin/Spud" % home, "%s/bin/ſpud" % home, "%s/tests/tool" % home):
            self.assertRefused("python3.14 -I -S %s --as spud board" % script, "Law 6")
            self.assertRefused("python3.14 -I -S %s ticket new --title x" % script, "Law 6")
        self.assertRefused("%s/tests/tool --as spud board" % home, "Law 6")
        self.assertRefused("%s/bin/ſpud --as spud board" % home, "Law 6")
        self.assertRefused("cd %s && python3.14 -I -S tests/tool init" % home, "Law 6")
        self.assertRefused("cd %s/tests && ./tool --as spud board" % home, "Law 6")
        self.assertAllowed("python3.14 -I -S %s/tests/tool --as %s member log hi" % (home, AGENT_A))
        self.assertSilent("python3.14 -I -S %s/tests/other.py --as spud board" % home)

    def test_cd_before_a_spud_call_keeps_the_allow(self):
        self.assertAllowed("cd %s && %s --as %s member log hi" % (self.home.path, self.spud_cli, AGENT_A))
        self.assertAllowed("cd /tmp; %s board" % self.spud_cli)
        self.assertSilent("cd /tmp && ls")


class SpudAllowIdentityTest(BashHookCase):
    """SPD-032: PreToolUse(Bash) allows a spud call only when the hook can vouch for everything that runs: the ledger root's
    own launcher by file identity, from its own directory (the launcher loads spud_ledger.py beside its real path), under the
    interpreter the hook itself runs on, with exactly -I and -S, no wrapper, no variable assigned anywhere earlier in the line
    but SPUD_HOME naming the root, and no redirection into a file.  A call it recognizes by name but cannot vouch for is still
    refused for Laws 5 and 6 and otherwise stays silent, so the harness's permission rules and prompt decide."""

    tool_sessions_always = True  # one spelling runs from a worktree of the tool, as a session of Spud's there without a claim

    def setUp(self):
        super().setUp()
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        self.launcher = self.home.launcher
        self.log = "--as %s member log hi" % AGENT_A
        # SPD-233: a bare `python3.14` is vouched for only when the first python3.14 on the hook's PATH is the file the hook
        # runs on, in its own directory (shell/spud_calls.interpreter_vouched).  The hook reads the home's environment, which
        # was the suite's own PATH, so the allow hung on the order of whoever started the run; the PATH it sees is pinned
        # here, the interpreter's directory first and the rest as it was.
        interpreters = os.path.dirname(sys.executable)
        beside = os.path.join(interpreters, "python3.14")
        if not (os.path.isfile(beside) and os.path.samefile(beside, sys.executable)):
            self.skipTest("needs a python3.14 beside the interpreter running the suite (%s), the one file a bare name is vouched "
                          "for" % sys.executable)
        rest = [p for p in self.home.env.get("PATH", "").split(os.pathsep) if p and p != interpreters]
        self.home.env["PATH"] = os.pathsep.join([interpreters] + rest)

    def assertSilentForBoth(self, spelled, cwd=None):
        """`spelled` is a command line with {tail} where the spud arguments go: silent for the lead and for Spud."""
        self.assertSilent(spelled.format(tail=self.log), AGENT_A, cwd)
        self.assertSilent(spelled.format(tail="--as spud board"), None, cwd)

    def assertAllowedForBoth(self, spelled, cwd=None):
        self.assertAllowed(spelled.format(tail=self.log), AGENT_A, cwd)
        self.assertAllowed(spelled.format(tail="--as spud board"), None, cwd)

    def assertStillRefused(self, spelled, cwd=None):
        """Name recognition is unchanged: Law 6 refuses the lead's `--as spud` and `ticket new` however the call is spelled."""
        self.assertRefused(spelled.format(tail="--as spud board"), "Law 6", AGENT_A, cwd)
        self.assertRefused(spelled.format(tail="ticket new --title x"), "Law 6", AGENT_A, cwd)

    def test_the_tickets_evidence_a_script_merely_named_spud_is_silent(self):
        any_dir = self.out / "any"
        any_dir.mkdir()
        (any_dir / "spud").write_text("print('not the ledger')\n", encoding="utf-8")
        copy = self.out / "copy" / "bin"  # a real copy of the launcher and its program: another ledger program, not the root's
        copy.mkdir(parents=True)
        shutil.copyfile(SPUD, copy / "spud")
        shutil.copyfile(PROGRAM, copy / "spud_ledger.py")
        for script in ("/tmp/any/spud", any_dir / "spud", copy / "spud"):
            with self.subTest(script=str(script)):
                self.assertSilentForBoth("python3.14 %s board" % script)
                self.assertSilentForBoth("python3.14 -I -S %s {tail}" % script)
                self.assertStillRefused("python3.14 -I -S %s {tail}" % script)
        self.assertAllowedForBoth("python3.14 -I -S %s {tail}" % self.launcher)

    def test_a_hard_link_to_the_launcher_elsewhere_runs_another_program(self):
        """The same file in another directory: the launcher loads spud_ledger.py beside its own real path, so identity of the
        file alone does not fix the program.  A symlink resolves into bin/, so it does."""
        (self.home.path / "tests").mkdir(exist_ok=True)
        for where in (self.out / "spud", self.home.path / "tests" / "spud"):
            try:
                os.link(self.launcher, where)
            except OSError as e:
                self.skipTest("no hard link from %s to %s: %s" % (self.launcher, where, e))
            with self.subTest(where=str(where)):
                self.assertSilentForBoth("python3.14 -I -S %s {tail}" % where)
                self.assertStillRefused("python3.14 -I -S %s {tail}" % where)
        (self.out / "link").symlink_to(self.launcher)
        self.assertAllowedForBoth("python3.14 -I -S %s {tail}" % (self.out / "link"))

    def test_a_worktrees_launcher_is_not_the_ledgers(self):
        """Root only: a worktree's bin/spud_ledger.py is a member's deliverable on a code ticket (bin/**), so its launcher runs
        code a member may just have written.  The prescribed spelling names the root's launcher, from any directory."""
        wt = self.home.tool / ".claude" / "worktrees" / "spd-999-x"
        (wt / "bin").mkdir(parents=True)
        shutil.copyfile(SPUD, wt / "bin" / "spud")
        shutil.copyfile(PROGRAM, wt / "bin" / "spud_ledger.py")
        root = "python3.14 -I -S %s {tail}" % self.launcher
        self.assertAllowedForBoth(root, cwd=str(wt))
        self.assertAllowedForBoth("cd %s && %s" % (wt, root))
        for spelled, cwd in (("python3.14 -I -S bin/spud {tail}", str(wt)), ("python3.14 -I -S %s/bin/spud {tail}" % wt, None),
                             ("cd %s && python3.14 -I -S bin/spud {tail}" % wt, None), ("python3.14 -I -S ./bin/spud {tail}", str(wt))):
            with self.subTest(spelled=spelled, cwd=cwd):
                self.assertSilentForBoth(spelled, cwd)
                self.assertStillRefused(spelled, cwd)

    def test_the_interpreter_must_be_the_one_the_hook_runs_on(self):
        """The same file in the same directory as the hook's own interpreter (a symlink elsewhere is a file anyone who can
        write that directory swaps, and its pyvenv.cfg moves sys.prefix).  A bare name is looked up on the hook's PATH, which
        the Bash tool shares, and a PATH the line changes, or a relative PATH entry ahead of the match, cannot be vouched for
        -- and since SPD-062 a PATH the line changes, or a `hash` it sets, refuses a member outright rather than only
        withholding the vouch."""
        exe = sys.executable
        launcher = self.launcher
        self.assertAllowedForBoth("python3.14 -I -S %s {tail}" % launcher)
        self.assertAllowedForBoth("%s -I -S %s {tail}" % (exe, launcher))
        links = self.out / "links"
        links.mkdir()
        (links / "python3.14").symlink_to(exe)
        other = self.out / "other"
        other.mkdir()
        (other / "python3.14").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (other / "python3.14").chmod(0o755)
        for interpreter, cwd in (("%s/python3.14" % links, None), ("./python3.14", str(links)), ("links/python3.14", str(self.out)),
                                 ("%s/python3.14" % other, None), ("cd %s && ./python3.14" % other, None)):
            with self.subTest(interpreter=interpreter, cwd=cwd):
                spelled = "%s -I -S %s {tail}" % (interpreter, launcher)
                # SPD-145: a file in the temp roots run by its path is one a member may have written (`other`'s is a shell
                # script), so the lead is refused it; Spud's unvouched call stays silent
                self.assertRefused(spelled.format(tail=self.log), SCRIPT_WORDING, AGENT_A, cwd)
                self.assertSilent(spelled.format(tail="--as spud board"), None, cwd)
                self.assertStillRefused(spelled, cwd)
        # SPD-062: a PATH the line changes, and a name it hashes, no longer merely withhold the vouch -- the interpreter the
        # hook read by name is not the one the shell would run, so a member is refused outright.  Spud, whom Law 7 does not
        # bind, still sees the unvouched call and stays silent.
        for prefix in ("PATH=%s:$PATH " % other, "PATH=%s; " % other, "export PATH=%s:$PATH; " % other, "path=(%s $path); " % other,
                       "hash -p %s/python3.14 python3.14; " % other):
            with self.subTest(prefix=prefix):
                spelled = "%spython3.14 -I -S %s {tail}" % (prefix, launcher)
                self.assertRefused(spelled.format(tail=self.log), "Law 7", AGENT_A)
                self.assertSilent(spelled.format(tail="--as spud board"), None)
        # an alias reaches no command word outside `eval` (SPD-059), so it withholds the vouch and nothing more
        self.assertSilentForBoth("alias python3.14=%s/python3.14; python3.14 -I -S %s {tail}" % (other, launcher))
        # SPD-084: the line defines a function python3.14, so the later call runs the function, not the interpreter the hook
        # read; the function shadow refuses the lead outright (its body's "$@", words python reads by name, would too --
        # SPD-043).  Spud's call stays silent.  Neither is allowed.
        spelled = "python3.14() {{ %s/python3.14 \"$@\"; }}; python3.14 -I -S %s {tail}" % (other, launcher)
        self.assertRefused(spelled.format(tail=self.log), "shell function", AGENT_A)
        self.assertSilent(spelled.format(tail="--as spud board"), None)
        path = self.home.env["PATH"]
        try:
            for changed in (".:" + path, ":" + path, "%s:%s" % (links, path), "%s:%s" % (other, path)):
                with self.subTest(hook_path=changed.split(":")[0]):
                    self.home.env["PATH"] = changed
                    self.assertSilentForBoth("python3.14 -I -S %s {tail}" % launcher)
                    self.assertAllowedForBoth("%s -I -S %s {tail}" % (exe, launcher))
        finally:
            self.home.env["PATH"] = path

    def test_exactly_dash_I_and_dash_S(self):
        """-I keeps PYTHONPATH and the user site out (probed: a PYTHONPATH json.py runs under -S alone, a PYTHONUSERBASE .pth
        under neither flag); -S keeps site-packages .pth files out; -X pycache_prefix reads bytecode from anywhere (probed: an
        unchecked-hash pyc of json runs under -I -S).  Any other option is not the prescribed call, and -c, -m and - run other code."""
        launcher = self.launcher
        for opts in ("-I -S", "-IS", "-SI", "-S -I"):
            self.assertAllowedForBoth("python3.14 %s %s {tail}" % (opts, launcher))
        for opts in ("", "-I", "-S", "-E -s -S", "-I -S -X pycache_prefix=%s" % self.out, "-I -S -Xpycache_prefix=%s" % self.out,
                     "-I -S -i", "-I -S -u", "-I -S -W error", "-I -S -B", "-I -S -X importtime", "-I -S --"):
            with self.subTest(opts=opts):
                spelled = "python3.14 %s %s {tail}" % (opts, launcher)
                self.assertSilentForBoth(spelled)
                self.assertStillRefused(spelled)
        self.assertSilentForBoth("python3.14 -I -S -m spud_ledger {tail}")
        # -c and - run code of the line's own, which SPD-150 refuses a member where it cannot read that it writes nothing
        # (SPD-175); neither is an allowed spud call for anyone, which is what this test is about.
        for spelled in ("python3.14 -I -S -c 'import runpy; exec(1)' %s {tail}" % launcher,
                        "python3.14 -I -S - %s {tail} < %s" % (launcher, launcher)):
            with self.subTest(spelled=spelled):
                self.assertSilent(spelled.format(tail="--as spud board"), None)
                self.assertRefused(spelled.format(tail=self.log), INLINE_WORDING)

    def test_the_launcher_alone_runs_without_dash_I_or_dash_S(self):
        """The #! line runs /opt/homebrew/bin/python3.14 with no flags, so the environment's PYTHONPATH and the site .pth files
        load code before the program (probed).  Silent, and since SPD-038 settings sync writes no native allow rule for that
        spelling, so the harness prompts."""
        for spelled in ("%s {tail}" % self.launcher, "cd %s && bin/spud {tail}" % self.home.tool, "spud {tail}"):
            with self.subTest(spelled=spelled):
                self.assertSilentForBoth(spelled)
                self.assertStillRefused(spelled)

    def test_no_wrapper_and_no_assignment_before_the_call(self):
        """An assignment reaches the interpreter's environment when it prefixes the call, and when it assigns a variable
        the shell already exports (probed: DYLD_INSERT_LIBRARIES loads a dylib under -I -S).  Wrappers change the
        environment, argv[0] or the user."""
        launcher = self.launcher
        for prefix in ("PYTHONPATH=%s " % self.out, "DYLD_INSERT_LIBRARIES=%s/x.dylib " % self.out, "FOO=1 ", "FOO=1; ", "FOO=1 && ",
                       "export FOO=1; ", "env ", "env -i ", "env PYTHONPATH=%s " % self.out, "ENV ", "command ", "exec ", "exec -a x ",
                       "nohup ", "time ", "nice ", "noglob ", "sudo ", "xargs ", "- "):
            with self.subTest(prefix=prefix):
                spelled = "%spython3.14 -I -S %s {tail}" % (prefix, launcher)
                self.assertSilentForBoth(spelled)
                self.assertStillRefused(spelled)
        self.assertAllowedForBoth("python3.14 -I -S %s {tail}; FOO=1" % launcher)  # an assignment after the call reaches nothing it runs

    def test_spud_home_must_name_the_ledger_root(self):
        """The launcher caches its program's bytecode under <SPUD_HOME>/.spud/pycache when that directory exists, and loads
        it from there (probed: an unchecked-hash pyc in another home's state directory runs under -I -S); the CLI would also
        write another ledger.  Not refused: members probe scratch homes, and the prompt decides."""
        home = self.home.path
        launcher = self.launcher
        self.assertAllowedForBoth("SPUD_HOME=%s python3.14 -I -S %s {tail}" % (home, launcher))
        self.assertAllowedForBoth("SPUD_HOME=%s/ python3.14 -I -S %s {tail}" % (home, launcher))
        for value in (self.out, "%s/elsewhere" % home, "$HOME/x", "", "'%s'x" % home, "~"):
            with self.subTest(value=str(value)):
                spelled = "SPUD_HOME=%s python3.14 -I -S %s {tail}" % (value, launcher)
                self.assertSilentForBoth(spelled)
                self.assertStillRefused(spelled)
                self.assertSilentForBoth("SPUD_HOME=%s; python3.14 -I -S %s {tail}" % (value, launcher))

    # SPD-233: a path in another case names the same directory only where the filesystem folds case, which is the
    # machine's, so these are tests of their own that skip, saying so, on a case-sensitive filesystem -- where an `if` in
    # the tests above once dropped them without a word.
    def needs_a_case_insensitive_filesystem(self, path):
        if not case_insensitive_fs(path):
            self.skipTest("needs a case-insensitive filesystem at %s" % path)

    def test_spud_home_in_another_case_names_the_root_where_the_filesystem_folds_case(self):
        home = self.home.path
        self.needs_a_case_insensitive_filesystem(home)
        self.assertAllowedForBoth("SPUD_HOME=%s python3.14 -I -S %s {tail}" % (str(home).swapcase(), self.launcher))

    def test_the_launcher_in_another_case_is_the_roots_where_the_filesystem_folds_case(self):
        tool = self.home.tool
        self.needs_a_case_insensitive_filesystem(tool)
        self.assertAllowedForBoth("python3.14 -I -S %s/BIN/SPUD {tail}" % str(tool).upper())
        self.assertAllowedForBoth("cd %s && python3.14 -I -S Bin/Spud {tail}" % str(tool).swapcase())

    def test_relative_and_indirect_spellings_by_identity(self):
        """A relative launcher is the root's from every directory the shell may be in (SPD-030's candidates), or silent."""
        home = self.home.tool  # the directory the launcher is in: bin/spud is relative to the tool checkout
        (home / "tests").mkdir(exist_ok=True)
        (home / "tests" / "tool").symlink_to(self.launcher)
        for spelled, cwd in (("python3.14 -I -S bin/spud {tail}", str(home)), ("python3.14 -I -S ./bin/spud {tail}", str(home)),
                             ("cd %s && python3.14 -I -S bin/spud {tail}" % home, None), ("cd %s/tests && python3.14 -I -S ../bin/spud {tail}" % home, None),
                             ("pushd %s && python3.14 -I -S bin/spud {tail}" % home, None), ("python3.14 -I -S tests/tool {tail}", str(home)),
                             ("python3.14 -I -S %s/tests/../bin/spud {tail}" % home, None)):
            with self.subTest(spelled=spelled, cwd=cwd):
                self.assertAllowedForBoth(spelled, cwd)
        (self.out / "bin").mkdir()
        (self.out / "bin" / "spud").write_text("print('not the ledger')\n", encoding="utf-8")
        for spelled, cwd in (("python3.14 -I -S bin/spud {tail}", str(self.out)), ("cd %s && python3.14 -I -S bin/spud {tail}" % self.out, None),
                             ("cd $X && python3.14 -I -S bin/spud {tail}", None), ("cd %s/not-yet; python3.14 -I -S bin/spud {tail}" % home, None),
                             ("true | cd %s; python3.14 -I -S bin/spud {tail}" % self.out, str(home)),
                             ("command cd %s; python3.14 -I -S bin/spud {tail}" % self.out, str(home))):
            with self.subTest(spelled=spelled, cwd=cwd):
                self.assertSilentForBoth(spelled, cwd)
                self.assertStillRefused(spelled, cwd)

    def test_an_allowed_call_redirects_into_no_file(self):
        """The allow skips the prompt a write outside the repository would get; a spud call's output written into a file (a
        shell profile, a .pth file) is not the prescribed call.  Descriptor duplication and /dev/null stay allowed."""
        cli = "python3.14 -I -S %s" % self.launcher
        for tail in (" > /dev/null", " 2>&1", " 2>/dev/null", " >&2", " < %s" % self.launcher, " &>/dev/null"):
            with self.subTest(tail=tail):
                self.assertAllowedForBoth(cli + " {tail}" + tail)
        for tail in (" > %s/board.txt" % self.out, " >> %s/board.txt" % self.out, " 2> %s/err.txt" % self.out, " &> %s/all.txt" % self.out,
                     " >| %s/x" % self.out):
            with self.subTest(tail=tail):
                self.assertSilentForBoth(cli + " {tail}" + tail)
        # A shell profile or .pth file in Eric's home: not allowed, and since SPD-064 refused to a member outright.
        self.assertRefused(cli + " board > ~/x.pth", "outside every registered project")
        self.assertSilent(cli + " board > ~/x.pth", agent_id=None)
        self.assertRefused(cli + " board > ledger/tickets/SPD-001.md", "generated")

    def test_every_prescribed_spelling_stays_allowed(self):
        """CLAUDE.md's `spud` and the brief template's: Spud's and a member's, --json, @- heredocs, @file, a worktree's cwd."""
        wt = self.home.tool / ".claude" / "worktrees" / "spd-999-x"
        wt.mkdir(parents=True)
        cli = "python3.14 -I -S %s" % self.launcher
        lead = self.lead["ref"]
        for cmd, cwd in (
            ("%s --as %s member log 'Read the brief; probed -I'" % (cli, AGENT_A), None),
            ("%s --as %s member show %s" % (cli, AGENT_A, lead), None),
            ("%s --json --as %s member show %s" % (cli, AGENT_A, lead), None),
            ("%s --as %s --json member show %s" % (cli, AGENT_A, lead), None),
            ("%s --as %s member result @- <<'EOF'\nProduced x; the suite is green.\nEOF" % (cli, AGENT_A), None),
            ("%s --as %s member block \"which one?\"" % (cli, AGENT_A), None),
            ("%s --as %s proposal file --title x --why y --evidence z --priority P2" % (cli, AGENT_A), None),
            ("%s --as %s member new --persona scout --model haiku --brief @- --deliverable 'tests/x/**' <<'EOF'\nLook it up.\nEOF" % (cli, AGENT_A), None),
            ("cd %s && %s --as %s member log hi" % (wt, cli, AGENT_A), None),
            ("%s --as %s member log hi" % (cli, AGENT_A), str(wt)),
            ("%s -I -S %s --as %s member log hi" % (sys.executable, self.launcher, AGENT_A), str(wt)),
        ):
            with self.subTest(cmd=cmd, cwd=cwd):
                self.assertAllowed(cmd, AGENT_A, cwd)
        for cmd, cwd in (
            ("%s --as spud ticket new --title x --priority P1 --status active --brief @- --sizing s <<'EOF'\nBrief.\nEOF" % cli, None),
            ("%s --as spud member new --ticket SPD-001 --persona engineer --model opus --brief @brief.md --deliverable 'bin/**'" % cli, None),
            ("%s --as spud member finish %s --status done --outcome x --summary y --next z" % (cli, lead), None),
            ("%s --as spud proposal decide 1 --decision create --priority P2" % cli, None),
            ("%s --as spud render" % cli, None),
            ("%s board --brief" % cli, str(wt)),
            ("%s card SPD-001" % cli, None),
            ("%s events --ticket SPD-001 --limit 50" % cli, None),
            ("cd %s && %s --as spud render" % (self.home.path, cli), str(wt)),
            ("%s -I -S %s --as spud board" % (sys.executable, self.launcher), None),
        ):
            with self.subTest(cmd=cmd, cwd=cwd):
                self.assertAllowed(cmd, None, cwd)


# The wrappers the Bash hook strips before it names a command, as bin/spud_ledger.py WRAPPERS holds them since SPD-030
# (zsh's noglob and nocorrect precommand modifiers added), and the arguments each needs before its command.
SHELL_WRAPPERS = ("builtin", "caffeinate", "chronic", "command", "doas", "env", "exec", "ionice", "nice", "nocorrect", "noglob",
                  "nohup", "script", "setsid", "stdbuf", "sudo", "time", "timeout", "unbuffer", "xargs")
WRAPPER_ARGS = {"timeout": "5 ", "script": "-q /dev/null "}


class ShellModelTest(BashHookCase):
    """SPD-030: PreToolUse(Bash) reads wrappers and the working directory as zsh 5.9 and bash 3.2 do (the Bash tool runs the
    user's shell: zsh on this Mac, bash elsewhere), erring toward refusal where the two could disagree.  macOS PATH lookup is
    case-insensitive, so ENV and NOHUP are env and nohup, and CD, /usr/bin/cd and a cd behind an external wrapper run
    /usr/bin/cd in their own process: the shell's directory stays.  A directory the shell may or may not be in is checked
    too; one the hook cannot know refuses a member's relative redirection and leaves Spud's unchecked.  AGENT_A plans
    tests/** and bin/spud; AGENT_C plans **; note.txt is refused to AGENT_A at the home and silent outside it."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        home = self.home.path
        for d in ("ledger/tickets", "docs/inner", "tests/zzone", "bin"):
            (home / d).mkdir(parents=True, exist_ok=True)

    def test_the_tickets_evidence_commands_are_refused(self):
        home = self.home.path
        redirections = ("CD /tmp && echo x > ledger/tickets/SPD-001.md", "/usr/bin/cd /tmp && echo x > ledger/tickets/SPD-001.md",
                        "pushd %s/ledger && echo x > tickets/SPD-001.md" % home)
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(agent_id=agent_id):
                for cmd in redirections:
                    self.assertRefused(cmd, "generated", agent_id)
                self.assertRefused("ENV git commit -m x", "Law 7", agent_id)
                self.assertRefused("NOHUP git push", "Law 7", agent_id)
        for cmd in redirections:
            self.assertRefused(cmd, "Law 1", agent_id=None)

    def test_every_wrapper_in_any_case_before_a_git_write(self):
        for name in SHELL_WRAPPERS:
            for spelled in spellings(name) + ("/usr/bin/" + name.upper(),):
                with self.subTest(spelled):
                    self.assertRefused("%s %sgit push" % (spelled, WRAPPER_ARGS.get(name, "")), "Law 7")
        self.assertRefused("NOHUP nice -n 5 ENV -u X git commit -m x", "Law 7")
        for ok in ("ENV FOO=1 ls", "NOHUP git status", "TIMEOUT 5 git log", "Env -u X git diff", "COMMAND -v git"):
            self.assertSilent(ok)

    def test_a_wrappers_value_options_do_not_hide_its_command(self):
        for cmd in ("timeout -s KILL 5 git push", "timeout --signal KILL 5 git push", "TIMEOUT -k 1 5 git push", "env -u HOME git push",
                    "env -C /tmp git push", "ENV -S 'git push'", "env --split-string='git push'", "exec -a name git push", "nice -n 5 git push",
                    "NICE -5 git push", "ionice -c 3 git push", "caffeinate -t 5 git push", "Caffeinate -w 1 git push", "xargs -J % git push",
                    "xargs -I {} git push", "XARGS -n 1 git push", "stdbuf -o 0 git push", "stdbuf -oL git push", "sudo -g staff git push",
                    "sudo -Eu root git push", "sudo --user root git push", "doas -u root git push", "script -q /dev/null git push",
                    "script -c 'git push' typescript", "time -o /dev/null git push", "- git push"):
            with self.subTest(cmd):
                self.assertRefused(cmd, "Law 7")

    def test_only_the_builtin_cd_moves_the_directory(self):
        out = self.out
        for what, dir_command in (("builtin", "cd %s"), ("builtin keyword", "builtin cd %s"), ("assignment prefix", "FOO=1 cd %s"),
                                  ("negated", "! cd %s"), ("brace group", "{ cd %s; }"), ("time reserved word", "time cd %s"),
                                  ("--", "cd -- %s"), ("-L", "cd -L %s"), ("-P", "cd -P %s"), ("-LP", "cd -LP %s"),
                                  ("pushd", "pushd %s"), ("pushd --", "pushd -- %s"), ("eval", "eval 'cd %s'")):
            with self.subTest("followed: " + what):
                self.assertSilent((dir_command % out) + " && echo x > note.txt")
        self.assertSilent("cd %s; echo x > note.txt" % out)
        self.assertSilent("arr=(); cd %s; echo x > note.txt" % out)  # an empty array, not a function header
        self.assertSilent("cd %s\necho x > note.txt" % out)
        self.assertSilent("(cd %s && echo x > note.txt)" % out)
        for what, dir_command in (("upper case", "CD %s"), ("mixed case", "Cd %s"), ("cD", "cD %s"), ("/usr/bin/cd", "/usr/bin/cd %s"),
                                  ("/usr/bin/CD", "/usr/bin/CD %s"), ("env", "env cd %s"), ("ENV", "ENV cd %s"), ("nohup", "nohup cd %s"),
                                  ("nice", "nice cd %s"), ("timeout", "timeout 5 cd %s"), ("sudo", "sudo cd %s"), ("xargs", "xargs cd %s"),
                                  ("COMMAND runs /usr/bin/COMMAND", "COMMAND cd %s"), ("BUILTIN is not found", "BUILTIN cd %s"),
                                  ("TIME runs /usr/bin/time", "TIME cd %s"), ("exec", "exec cd %s"), ("coproc", "coproc cd %s"),
                                  ("subshell", "(cd %s)"), ("command substitution", "echo $(cd %s)"), ("backticks", "echo `cd %s`"),
                                  ("process substitution", "cat <(cd %s)"), ("sh -c", "sh -c 'cd %s'"), ("bash -c", "bash -c \"cd %s\""),
                                  ("first pipeline element", "cd %s | cat"), ("group as pipeline element", "{ cd %s; } | cat"),
                                  ("background", "cd %s &")):
            with self.subTest("not moved: " + what):
                sep = " " if what == "background" else "; "
                self.assertRefused((dir_command % out) + sep + "echo x > note.txt", "deliverables")
        self.assertRefused("cd %s > note.txt" % out, "deliverables")
        self.assertRefused("{ cd %s; } > note.txt" % out, "deliverables")
        for what, dir_command in (("command runs the external in zsh", "command cd %s"), ("command -p", "command -p cd %s"),
                                  ("noglob is zsh's", "noglob cd %s"), ("nocorrect is zsh's", "nocorrect cd %s"), ("zsh's - precommand", "- cd %s"),
                                  ("time -p: zsh runs -p", "time -p cd %s"), ("chdir is zsh's", "chdir %s"),
                                  ("last pipeline element: zsh's shell, bash's subshell", "true | cd %s"),
                                  ("builtin command", "builtin command cd %s"), ("command builtin", "command builtin cd %s")):
            with self.subTest("one shell moves: " + what):
                self.assertRefused((dir_command % out) + " && echo x > note.txt", "deliverables")
        self.assertRefused("true | cd %s; echo x > ledger/tickets/SPD-001.md" % out, "Law 1", agent_id=None)
        self.assertSilent("cd %s && echo x > ledger/tickets/SPD-001.md" % out, agent_id=None)
        self.assertSilent("CD %s && echo x > CLAUDE.md" % out, agent_id=None)

    def test_a_cd_that_may_not_run_or_may_fail_leaves_either_directory(self):
        out = self.out
        for cmd in ("true && cd %s; echo x > note.txt", "false || cd %s; echo x > note.txt", "if true; then cd %s; fi; echo x > note.txt",
                    "if false; then true; else cd %s; fi; echo x > note.txt", "case x in x) cd %s;; esac; echo x > note.txt",
                    "case x in\n  x) cd %s ;;\nesac\necho x > note.txt", "while false; do cd %s; done; echo x > note.txt",
                    "function f { cd %s; }; echo x > note.txt", "cd %s/nonexistent; echo x > note.txt",
                    "cd %s/nonexistent || echo x > note.txt", "case x in (x) cd %s;; esac; echo x > note.txt"):
            with self.subTest(cmd):
                self.assertRefused(cmd % out, "deliverables")
        ledger = self.home.path / "ledger"
        for cmd in ("if true; then cd %s; else cd %s; fi; echo x > tickets/SPD-001.md" % (ledger, out),
                    "if false; then cd %s; elif true; then cd %s; else cd %s; fi; echo x > tickets/SPD-001.md" % (out, ledger, out),
                    "case x in a) cd %s;; b) cd %s;; esac; echo x > tickets/SPD-001.md" % (ledger, out)):
            with self.subTest("every branch's directory: " + cmd):
                self.assertRefused(cmd, "generated", AGENT_C)  # ** : only the ledger branch's target is refused
        self.assertRefused("case x in (x) git push;; esac", "Law 7")
        for cmd in ("true && cd %s && echo x > note.txt", "if true; then cd %s; echo x > note.txt; fi", "mkdir -p %s/new && cd %s/new && echo x > note.txt",
                    "cd %s/nonexistent && echo x > note.txt", "cd %s || exit 1; echo x > note.txt",
                    # SPD-277: a call reads the body of a function the line defines where it runs, and its cd carries
                    # into the rest of the line, as `cd %s; echo x > note.txt` does
                    "f() { cd %s; }; f; echo x > note.txt"):
            with self.subTest(cmd):
                self.assertSilent(cmd.replace("%s", str(out)))

    def test_directories_the_hook_cannot_follow(self):
        """Refused for a member's relative redirection or tee target, and since SPD-035 for Spud's too: Laws 1 and 5 held
        for nobody after `popd`, `cd -`, a sourced file or a relative cd in a loop, because the hook could not say which
        directory the target landed in.  Eric's call, made on SPD-035: refuse Spud with the reason members get, his own
        files being few.  An absolute target is checked for both, as it was."""
        out = self.out
        for dir_command in ("popd", "popd -n", "pushd", "pushd +1", "pushd -1", "pushd -q %s", "pushd -n %s", "cd -q %s", "cd -s %s", "cd -e %s",
                            "cd -@ %s", "cd -x %s", "cd +1", "cd -1", "cd -", "cd ~-", "cd ~root", "cd ~nosuchuser-spd-030", "cd $DIR",
                            "cd %s/*", "cd /{tmp,var}", "cd a b c", "source x.sh", ". x.sh", "for d in a b; do cd ..; done",
                            "CDPATH=$HOME; cd sub"):
            command = (dir_command.replace("%s", str(out))) + "; echo x > note.txt"
            with self.subTest(command):
                self.assertRefused(command, "cannot follow")
                self.assertRefused(command.replace("echo x > note.txt", "echo x | tee note.txt"), "cannot follow")
                self.assertRefused(command, "cannot follow", agent_id=None)
                self.assertRefused(command.replace("echo x > note.txt", "echo x | tee note.txt"), "cannot follow", agent_id=None)
                self.assertRefused(command.replace("note.txt", "ledger/tickets/SPD-001.md"), "cannot follow", agent_id=None)
        home = self.home.path
        self.assertRefused("popd; echo x > %s/docs/x.md" % home, "deliverables")
        self.assertRefused("popd; echo x > %s/ledger/tickets/SPD-001.md" % home, "Law 1", agent_id=None)
        self.assertRefused("echo x > ~-/note.txt", "cannot follow")
        self.assertRefused("echo x > ~root/note.txt", "cannot follow")
        self.assertAllowed("pushd %s && %s --as %s member log hi" % (home, self.spud_cli, AGENT_A))
        self.assertSilent("CD %s && %s --as %s member log hi" % (home, self.spud_cli, AGENT_A))

    def test_cd_physical_and_tilde_targets(self):
        home, out = self.home.path, self.out
        (home / "tests" / "link").symlink_to(home / "docs" / "inner")
        self.assertRefused("cd -P tests/link/.. && echo x > note.txt", "deliverables")  # the kernel's parent of docs/inner
        self.assertSilent("cd -L tests/link/.. && echo x > note.txt")  # tests, lexically
        self.assertSilent("cd tests/link/.. && echo x > note.txt")
        self.assertRefused("cd %s/docs && echo x > ~+/note.txt" % home, "deliverables")
        self.assertRefused("cd ~+/docs && echo x > note.txt", "deliverables")
        self.assertSilent("cd %s && cd ~+/tests && echo x > note.txt" % home)
        # ~ is HOME, so this writes no ledger file; since SPD-064 a member is refused there all the same, for being outside
        # every registered project, while Spud writes his own home as before.
        self.assertRefused("cd ~ && echo x > ledger/tickets/SPD-001.md", "outside every registered project")
        self.assertSilent("cd ~ && echo x > ledger/tickets/SPD-001.md", agent_id=None)
        self.assertRefused("cd %s && echo x > ~+/ledger/tickets/SPD-001.md" % home, "Law 1", agent_id=None)

    def test_cdpath_sends_a_relative_cd_elsewhere(self):
        """bash tries CDPATH before the current directory, zsh after it; a target starting with / ./ or ../ skips it."""
        home, out = self.home.path, str(self.out)
        # The non-matching CDPATH entry is under the scratch root: since SPD-064 a member's redirection into a directory
        # outside every registered project is refused, and this control is about which directory the cd lands in.
        for setting in ("CDPATH=%s cd ledger", "CDPATH=%s; cd ledger", "export CDPATH=%s; cd ledger", "cdpath=(%s); cd ledger",
                        "CDPATH=" + out + "/nowhere:%s; cd ledger"):
            with self.subTest(setting):
                self.assertRefused((setting % home) + " && echo x > tickets/SPD-001.md", "generated", AGENT_C, cwd=out)
        self.assertSilent("CDPATH=%s; cd ./ledger && echo x > tickets/SPD-001.md" % home, AGENT_C, cwd=out)
        self.assertRefused("CDPATH=%s; cd ledger && echo x > tickets/SPD-001.md" % home, "Law 1", agent_id=None, cwd=out)
        self.home.env["CDPATH"] = str(home)
        try:
            self.assertRefused("cd ledger && echo x > tickets/SPD-001.md", "generated", AGENT_C, cwd=out)
            self.assertSilent("cd /tmp && echo x > tickets/SPD-001.md", AGENT_C, cwd=out)
        finally:
            del self.home.env["CDPATH"]

    def test_zsh_two_argument_cd(self):
        """zsh replaces the first occurrence of the first argument in the current directory with the second; bash 3.2 takes the first."""
        home = self.home.path
        self.assertRefused("cd %s/tests/zzone && cd tests/zzone docs && echo x > note.txt" % home, "deliverables")
        self.assertRefused("cd %s/tests/zzone && cd zzone ../docs && echo x > note.txt" % home, "deliverables")

    def test_newlines_comments_and_joined_operators(self):
        out, home = self.out, self.home.path
        for cmd in ("ls\ngit push", "ls # don't\ngit push", "ls \\\n&& git push", "cat <(git push)", "tee >(git commit -m x) < /dev/null",
                    "echo ok;\n\ngit push", "for f in a; do\n  git add $f\ndone"):
            with self.subTest(cmd):
                self.assertRefused(cmd, "Law 7")
        self.assertRefused("(true)>ledger/tickets/SPD-001.md", "generated")
        self.assertRefused("(true)>ledger/tickets/SPD-001.md", "Law 1", agent_id=None)
        self.assertRefused("(cd %s)&&echo x > note.txt" % out, "deliverables")
        self.assertRefused("cd %s/docs\necho x > note.txt" % home, "deliverables")
        for ok in ("git status # before git push", "echo 'a\ngit push'", "echo a \\\ngit push", "x=$((1 + 2)); echo $x", "ls *(.)"):
            with self.subTest(ok):
                self.assertSilent(ok)


class ClassHomeTest(BashHookCase):
    """SPD-231: a BashHookCase class builds its home once and each later test starts from it restored (hookcase.ClassHome),
    so a test changes nothing the next one reads: the directory, the ledger, the environment, the config and the
    attributes the build set all come back."""

    def test_a_class_home_is_restored_whole(self):
        home, held = self.home.path, type(self).class_home
        (home / "new.md").write_text("x\n", encoding="utf-8")
        locked = home / "tests" / "locked"
        (locked / "in").mkdir(parents=True)
        locked.chmod(0o000)
        self.home.json("member", "finish", self.other["ref"], "--status", "done", "--outcome", "x", actor="spud")
        self.home.env["PATH"] = "/nowhere"
        self.home.config["limits"]["max_depth"] = 9
        self.lead["name"] = "Changed"
        held.restore(self)
        self.assertFalse((home / "new.md").exists())
        self.assertFalse((home / "tests").exists())
        self.assertEqual(self.home.json("member", "show", self.other["ref"])["member"]["status"], "active")
        self.assertNotEqual(self.home.env["PATH"], "/nowhere")
        self.assertNotEqual(self.home.config["limits"]["max_depth"], 9)
        self.assertNotEqual(self.lead["name"], "Changed")
        self.assertTrue((home / ".spud" / "pycache").is_dir() or not self.warm_cache)  # the bytecode cache stays
        self.assertRefused("git push", "Law 7")  # and the home answers as it was built to


class InProcessParityTest(BashHookCase):
    """SPD-231: every other Bash test runs the hook in this process (hookcase.run_main), so this class holds the two runs to
    one answer.  Each payload goes through `spud hook PreToolUse` as a process of its own and through the in-process run, in
    turn first, and both must print the same bytes and exit alike; the process's answer is also asserted -- the decision and
    words of the reason -- so these are the suite's representative end-to-end cases too: every law the PreToolUse hook
    enforces and every family of reason bash_rule gives, the edit and Agent tools', a malformed payload and one that is not
    JSON, and the hook.denied event a refusal records.  A new per-process cache on the hook path that fresh_process does
    not empty fails the last test here."""

    def setUp(self):
        super().setUp()
        home = self.home.path
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        (self.out / ".git").mkdir()  # a repository outside every checkout the ledger knows
        for d in ("tests", "docs", "ledger/tickets"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "package.json").write_text(json.dumps({"name": "x", "scripts": {"build": "echo build"}}), encoding="utf-8")
        snapshots = Path(self.home.env["SPUD_USER_CLAUDE_DIR"]) / "shell-snapshots"
        snapshots.mkdir(parents=True)
        (snapshots / "snapshot-zsh-1700000000000-aaaaaa.sh").write_text("alias -- gp='git push'\nalias -- broken='git push\n", encoding="utf-8")
        many = home / "tests" / "many"  # a glob past the match budget ("capped", GlobRedirectTest)
        many.mkdir()
        for i in range(importlib.import_module("spudlib.shell.syntax").GLOB_MATCH_CAP + 1):
            (many / ("f%04d.txt" % i)).write_text("x\n", encoding="utf-8")
        locked = home / "vendor" / "plain" / "locked"  # a tree the walk cannot read whole ("unwalked", RecursiveWriteTest)
        locked.mkdir(parents=True)
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)

    def bash_samples(self):
        """(command, caller, cwd, the decision, words of the reason): a member is AGENT_A (tests/** and bin/spud), Spud None."""
        cli, home, out = self.spud_cli, self.home.path, self.out
        return (
            ("git push", AGENT_A, None, "deny", "Law 7: spudagents never run `git push`"),
            ("git -c alias.p=push p", AGENT_A, None, "deny", "config the hook cannot read"),
            ("git frobnicate", AGENT_A, None, "deny", "is not one of git's own commands"),
            ("git -C %s status" % out, AGENT_A, None, "deny", "outside every checkout the ledger knows"),
            ("git -c core.pager=less log", AGENT_A, None, "deny", "runs a program git never checks"),
            ("PATH=%s:$PATH git status" % out, AGENT_A, None, "deny", "assigns PATH"),
            ("hash -p %s/git git; git status" % out, AGENT_A, None, "deny", "hashes `git`"),
            ("git() { :; }; git status", AGENT_A, None, "deny", "defines a shell function `git`"),
            ("env 'BASH_FUNC_git%%=() { true; }' bash -c true", AGENT_A, None, "deny", "BASH_FUNC_"),
            ("$X push", AGENT_A, None, "deny", "comes from a variable or a substitution"),
            ("git $X", AGENT_A, None, "deny", "spell the words out"),
            ("x=$(cat f); echo ${(e)x}", AGENT_A, None, "deny", "zsh's (e) flag"),
            ("echo $(echo $(echo $(echo $(echo $(echo $(echo $(git push)))))))", AGENT_A, None, "deny", "cannot read part of what this line runs"),
            ("X=ls; trap 'X=git' DEBUG; $X push", AGENT_A, None, "deny", "may not hold the value"),
            ("alias g=$Y; eval 'g push'", AGENT_A, None, "deny", "runs an alias this line defines that the hook cannot resolve"),
            # SPD-286: eval parses its text whole, so the alias it defines stands in none of its own commands
            ("eval 'alias git=echo; git push'", AGENT_A, None, "deny", "Law 7: spudagents never run `git push`"),
            ("broken", AGENT_A, None, "deny", "runs an alias your shell already defines"),
            ("gp", AGENT_A, None, "deny", "(The shell this command runs in already defines `gp`"),
            ("[gp][iu][st]* x", AGENT_A, None, "deny", "is a glob the shell expands"),
            ("%s --as spud board" % cli, AGENT_A, None, "deny", "Law 6: `--as spud` is Spud's"),
            ("%s ticket new --title x" % cli, AGENT_A, None, "deny", "Law 6: `spud ticket new` is Spud's"),
            ("%s --as %s member log hi" % (cli, AGENT_B), AGENT_A, None, "deny", "does not resolve to the caller's own member"),
            ("%s --as %s member log hi" % (cli, AGENT_D), AGENT_D, None, "deny", "is not bound to a member yet"),
            ("%s --as %s member log hi" % (cli, AGENT_A), None, None, "deny", "Law 5: `--as %s` from Spud's own session" % AGENT_A),
            ("%s hook PreToolUse" % cli, AGENT_A, None, "deny", "`spud hook` is the harness's"),
            ("sqlite3 %s/x.db" % out, None, None, "deny", "direct access to the ledger database is refused"),
            ("echo x > docs/x.md", AGENT_A, None, "deny", "a redirection or tee into docs/x.md: Law 5"),
            ("echo x > ledger/tickets/SPD-001.md", AGENT_A, None, "deny", "generated"),
            ("echo x > bin/x", None, None, "deny", "Law 1: a redirection or tee into"),
            ('echo x > "$TMPDIR/x"', None, None, "deny", "spell the path out"),
            ("popd; echo x > note.txt", AGENT_A, None, "deny", "cannot follow"),
            ("echo x > tests/*.none", AGENT_A, None, "deny", "matches no file now"),
            ("echo x > tests/many/*.txt", AGENT_A, None, "deny", "the redirection or tee target tests/many/*.txt is a glob whose expansion reaches"),
            ("cp tests/many/*.txt tests/", AGENT_A, None, "deny", "a write by argument (`cp` tests/many/*.txt) names a glob whose expansion reaches"),
            ("cp -R vendor/plain tests/", AGENT_A, None, "deny", "lands a directory tree the hook could not read whole"),
            ("xargs rm < list", AGENT_A, None, "deny", "xargs reads from its input"),
            ("tar -xPf a.tar", AGENT_A, None, "deny", "places files where the line cannot say"),
            ("git diff --output=docs/x.diff", AGENT_A, None, "deny", "a file this git call writes"),
            ("rm docs/x.md", AGENT_A, None, "deny", "a write by argument (`rm` docs/x.md): Law 5"),
            ("sh x.sh", AGENT_A, None, "deny", SCRIPT_WORDING),
            ("npm run build", AGENT_A, None, "deny", "is a script runner"),
            ("python3 -c 'open(\"x\", \"w\")'", AGENT_A, None, "deny", INLINE_WORDING),
            ("cat f | xargs node", AGENT_A, None, "deny", "spell the words out"),
            ("echo 'unterminated", None, None, "deny", "the hook cannot read this line"),
            ("%s --as %s member log hi" % (cli, AGENT_A), AGENT_A, None, "allow", "a well-formed spud call"),
            ("%s board --brief" % cli, None, str(out), "allow", "a well-formed spud call by Spud"),
            ("echo x > tests/out.txt", AGENT_A, None, None, ""),
            ("git push", None, None, None, ""),
            ("ls -la", AGENT_B, str(out), None, ""),
        )

    def assertAlike(self, payload, first):
        """The in-process answer and the process's, `first` deciding which runs first (the second reads the caches on disk
        the first wrote, as a later hook does): the same exit, stdout and stderr.  Returns the process's."""
        runs = (lambda: self.decide(payload), lambda: self.home.hook_process("PreToolUse", payload))
        if first == "process":
            runs = runs[::-1]
        a, b = (run() for run in runs)
        inp, proc = (a, b) if first != "process" else (b, a)
        self.assertEqual((inp.code, inp.stdout, inp.stderr), (proc.code, proc.stdout, proc.stderr), payload.get("tool_input", payload))
        return proc

    def test_every_law_and_every_family_of_reason_answer_alike(self):
        for i, (command, caller, cwd, decision, needle) in enumerate(self.bash_samples()):
            with self.subTest(command=command, caller=caller):
                r = self.assertAlike(self.pre_bash(command, agent_id=caller, cwd=cwd), "process" if i % 2 else "in-process")
                self.assertEqual((r.code, r.decision), (0, decision), r)
                self.assertIn(needle, r.reason)
                if decision is None:
                    self.assertEqual((r.stdout, r.stderr), ("", ""))

    def test_a_repository_the_checkout_holds_answers_alike(self):
        """A program key at the home's own local scope (GitLocalConfigTest): a member's git call refused for it, and Spud's."""
        plant_git_dir(self.home.path / ".git", "[core]\n\trepositoryformatversion = 0\n\tpager = /bin/echo\n")
        for i, (caller, needle) in enumerate(((AGENT_A, "scope (the repository in"), (None, "is Eric's call"), (AGENT_A, "core.pager"))):
            with self.subTest(caller=caller):
                r = self.assertAlike(self.pre_bash("git -C %s status" % self.home.path, agent_id=caller), "process" if i % 2 else "in-process")
                self.assertEqual(r.decision, "deny", r)
                self.assertIn(needle, r.reason)

    def test_the_other_tools_and_payloads_answer_alike(self):
        home = self.home.path
        edit = self.pre_edit(home / "docs" / "x.md", agent_id=AGENT_A)
        no_command = self.pre_bash("ls")
        no_command["tool_input"] = {}
        for i, (payload, code, decision, needle) in enumerate((
                (edit, 0, "deny", "Law 5: home:docs/x.md is not among"),
                (self.pre_edit(home / "tests" / "x.py", agent_id=AGENT_A), 0, None, ""),
                (self.pre_edit(home / "ledger" / "tickets" / "SPD-001.md", agent_id=None), 0, "deny", "Law 5"),
                (self.pre_edit(home / ".spud" / "ledger.db", agent_id=AGENT_A, tool="NotebookEdit"), 0, "deny", "spud sql --readonly"),
                (self.pre_edit("/Users/Nobody/.zshrc", agent_id=AGENT_A, tool="Edit"), 0, "deny", "outside every registered project"),
                (self.pre_agent("SPUD-001/Nobody (01, scout)"), 0, "deny", "Law 2"),
                (self.pre_agent("%s/%s (%s, %s)" % (self.team, self.lead["name"], self.lead["lineage"], self.lead["persona"]), model=None),
                 0, "deny", "is active, not planned"),
                (no_command, 0, "deny", "tool_input.command is missing"),
                (dict(self.pre_bash("ls"), hook_event_name="Stop"), 2, None, "does not match"),
                ("not json", 2, None, "payload is not JSON"))):
            with self.subTest(payload=str(payload)[:80]):
                if isinstance(payload, str):
                    inp, proc = self.home.hook("PreToolUse", payload), self.home.hook_process("PreToolUse", payload)
                    self.assertEqual((inp.code, inp.stdout, inp.stderr), (proc.code, proc.stdout, proc.stderr))
                    r = proc
                else:
                    r = self.assertAlike(payload, "process" if i % 2 else "in-process")
                self.assertEqual((r.code, r.decision), (code, decision), r)
                self.assertIn(needle, r.reason if code == 0 else r.stderr)

    def test_a_refusal_is_recorded_alike(self):
        """Both runs write the same hook.denied event for a refusal -- the reason, the tool, the command, the caller's
        member -- and nothing for a silence."""
        payload = self.pre_bash("git push", agent_id=AGENT_A)
        self.assertEqual(self.denied(), [])
        self.home.hook_process("PreToolUse", payload)
        self.decide(payload)
        self.home.hook_process("PreToolUse", self.pre_bash("ls", agent_id=AGENT_A))
        self.decide(self.pre_bash("ls", agent_id=AGENT_A))
        events = self.denied()
        self.assertEqual(len(events), 2, events)
        process, in_process = ({k: v for k, v in e.items() if k not in ("id", "at")} for e in events)
        self.assertEqual(process, in_process)
        self.assertEqual((process["data"]["tool_name"], process["data"]["command"], process["agent_id"]), ("Bash", "git push", AGENT_A))
        self.assertIn("Law 7", process["body"])

    def test_lines_that_read_alike_are_answered_alike(self):
        """The premise of BashHookCase.assertAnsweredAs, checked on the hook itself: forms from the grammar tests around
        each payload, and wherever the line reads as the payload alone (hook_reading), every caller's whole answer to the
        line -- the exit, the decision and every word of the reason -- is its answer to the payload: a refusal, a silence,
        and an allow where the form's own words are no command at all (`repeat 2 { ... }`)."""
        cli, home = self.spud_cli, self.home.path
        payloads = ("git push", "%s ticket new --title x" % cli, "%s --as spud member log hi" % cli,
                    "%s --as %s member log hi" % (cli, AGENT_B), "%s --as %s member log hi" % (cli, AGENT_A),
                    "%s hook PreToolUse" % cli, "sqlite3 %s/.spud/ledger.db 'select 1'" % home,
                    "echo x > ledger/tickets/SPD-001.md", "echo x | tee ledger/tickets/SPD-001.md", "echo x > docs/x.md",
                    "rm -rf docs", "echo x > tests/k.py", "true")
        forms = ("repeat 2 { %s }", "for f (a b) ( %s )", "for f in a b; do %s; done", "if [[ -n x ]] %s",
                 "n=0; while [[ $((n++)) -lt 2 ]] { %s }", "if [[ -z x ]] { true } elif [[ -n y ]] { %s }; true",
                 "{ true } always {( %s )}", "if (( 1 )) ( {%s} )", "coproc { repeat 1 %s; }", "time { %s; }", "{%s}",
                 "eval '{%s}'", "foreach f (a) %s; end", "case x in x) %s;; esac", "if [[ -z x ]] { true } else ( {%s} ); fi")
        alike = 0
        for form in forms:
            for command in payloads:
                line = form % command
                if self.hook_reading(line) != self.hook_reading(command):
                    continue
                alike += 1
                for caller in (AGENT_A, AGENT_B, None):
                    with self.subTest(line=line, caller=caller):
                        a, b = self.bash(line, caller), self.bash(command, caller)
                        self.assertEqual((a.code, a.stdout, a.stderr), (b.code, b.stdout, b.stderr))
        self.assertGreater(alike, len(forms) * len(payloads) // 2, "too few forms read as their payload to prove anything")

    def test_no_table_on_the_hook_path_outlives_a_run_but_the_ones_fresh_process_empties(self):
        """Every table of the program that an in-process run of the Bash and edit hooks changes is a cache fresh_process
        empties (HOOK_CACHES, HOOK_MEMOS) or one that holds nothing a later run could read differently (PURE_TABLES):
        anything else would carry one test's run into the next, which a hook process never does.  A table is what a module
        holds by name, and what a module's own functions and classes hold: a function's default arguments, its closure
        cells and its attributes, a class's attributes and its methods' defaults, cells and attributes, and a functools
        cache anywhere among them; each is followed into the containers and the objects' __dict__ it holds, four levels
        down (hookcase.program_tables).  The samples run against the program imported anew (hookcase.fresh_program), so a
        memo this class's other tests filled under the same keys cannot hide.  test_hookcase.InProcessLeakTest runs the
        same walk over every other hook event and the CLI commands the in-process test classes call (SPD-241)."""
        home = self.home.path

        def samples():
            for command, caller, cwd, _, _ in self.bash_samples():
                self.bash(command, caller, cwd)
            for path, caller, tool in ((home / "docs" / "x.md", AGENT_A, "Write"), (home / "tests" / "x.py", AGENT_A, "Edit"),
                                       (home / "ledger" / "tickets" / "SPD-001.md", None, "Write"),
                                       (home / STATE / "ledger.db", AGENT_A, "NotebookEdit")):
                self.decide(self.pre_edit(path, agent_id=caller, tool=tool))

        changed = tables_changed(samples)
        self.assertEqual(changed - LEAK_ALLOWED, set())
        self.assertTrue(changed & set(HOOK_CACHES), "the samples fill no cache, so this guard proves nothing")


class SubstitutionShownTest(BashHookCase):
    """SPD-200: a reason names a word the line fills from a substitution as `$(...)` -- a `$( )` or backtick body the
    reader lifted, and the file name a `<( )` hands its command, whose reason reads as the `$( )` line's (SPD-190) -- and
    never by the reader's own placeholder (hookio.SUBST) or process-substitution mark (syntax.PROCSUB_MARK), which a
    member reading the refusal could not tell apart from a word of its own.  A marker the member typed keeps its own
    "placeholder" reason (SPD-199), which test_hooks_snapshots.ReaderFailsClosedTest holds."""

    # Lines whose refusal, for a member or for Spud, names a word holding a substitution or a process substitution's
    # file: every reason bash_rule formats from a word the reader holds.
    LINES = ("echo x | tee $(cat)", "echo x > $(cat)", "echo x > `cat`", 'echo x > "$(cat)"', "echo x > a$(cat)b",
             "echo x >> $(cat)/y", "echo x | tee <(cat)", "echo x > <(cat)", "cp a $(cat)", "sort -o $(cat) f", "rm -rf $(cat)",
             "mkfifo $(cat)", "touch <(cat)", "git -C $(cat) status", "git -C a$(cat) log", "git -C <(cat) status",
             "GIT_DIR=$(cat) git log", "git --git-dir=<(cat) log", "git -c alias.x=$(cat) status", "git -c core.pager=$(cat) log",
             "git config --file $(cat) a b", "GIT_TRACE=$(cat) git status", "git format-patch -o $(cat)",
             "env BASH_FUNC_x$(cat)%%=y bash", "spud --as $(cat) member log hi", "spud --as <(cat) member log hi",
             "$(cat) x", "eval $(cat)", "git --output=$(cat) log", "awk $(cat) f", "sed -i -f $(cat) f", "cat f | xargs git $(cat)",
             "PATH=$(cat) git status", "hash -p $(cat) git", "f$(cat)() { :; }", "read $(cat)", "(( $(cat) = 1 ))")

    def marks(self):
        return (importlib.import_module("spudlib.hooks.hookio").SUBST, importlib.import_module("spudlib.shell.syntax").PROCSUB_MARK)

    def test_a_substitution_target_is_shown_as_one(self):
        for command, shown in (("echo x | tee $(cat)", "the redirection target $(...) holds"),
                               ("echo x > $(cat)", "the redirection target $(...) holds"),
                               ("echo x > a$(cat)b", "the redirection target a$(...)b holds"),
                               ("cp a $(cat)", "(`cp` $(...)) names its file"),
                               ("git format-patch -o $(cat)", "(format-patch -o $(...)) holds"),
                               ("git -C $(cat) status", "(-C $(...))"),
                               ("git -c alias.x=$(cat) status", "(-c alias.x=$(...))"),
                               ("env BASH_FUNC_x$(cat)%%=y bash", "`BASH_FUNC_x$(...)%%`"),
                               ("spud --as $(cat) member log hi", "`--as $(...)`")):
            for agent_id in (AGENT_A, None):
                with self.subTest(command, agent_id=agent_id):
                    if agent_id is None and command.startswith(("git -", "env ")):
                        continue  # Spud's own git calls and environment are not held to these
                    self.assertRefused(command, shown, agent_id)

    def test_a_process_substitution_file_is_shown_as_a_substitution(self):
        # shown as the same line's `$( )` is, which SPD-190 keeps (test_hooks_words.ProcessSubstitutionFileTest)
        for command, shown in (("echo x | tee <(cat)", "the redirection target $(...) holds"),
                               ("echo x > <(cat)", "the redirection target $(...) holds"),
                               ("touch <(cat)", "(`touch` $(...)) names its file"),
                               ("spud --as <(cat) member log hi", "`--as $(...)`")):
            for agent_id in (AGENT_A, None):
                with self.subTest(command, agent_id=agent_id):
                    self.assertEqual(self.assertRefused(command, shown, agent_id).reason,
                                     self.bash(command.replace("<(cat)", "$(cat)"), agent_id).reason)
        self.assertRefused("git -C <(cat) status", "(-C $(...))")

    def test_no_reason_shows_the_readers_own_marks(self):
        subst, procsub = self.marks()
        for command in self.LINES:
            for agent_id in (AGENT_A, None):
                with self.subTest(command, agent_id=agent_id):
                    reason = self.bash(command, agent_id).reason or ""
                    self.assertNotIn(subst, reason)
                    self.assertNotIn(procsub, reason)


class AliasReasonTest(BashHookCase):
    """SPD-289: the "alias", "alias-word" and "shell-alias" reasons were written for `eval` and a command word -- "`eval`
    runs the command word", "`eval` reads ... again", "the command word ... is an alias your shell already defines" -- but
    each now arises elsewhere: the line's aliases in every text zsh parses as the line runs, a `$( )` or `<( )` body as
    well as eval's words (SPD-283, SPD-287), and the shell's own global alias in any word, its suffix alias on a command
    word ending in its suffix (SPD-283).  Each reason now reads true in every such place and says what to respell.  The
    snapshot holds a plain, a global and a suffix alias whose quoting never closes, as test_hooks_snapshots'
    HeldWordAliasTest does.  AGENT_A plans tests/** and bin/spud."""

    def setUp(self):
        super().setUp()
        snapshots = Path(self.home.env["SPUD_USER_CLAUDE_DIR"]) / "shell-snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        (snapshots / "snapshot-zsh-1700000000000-aaaaaa.sh").write_text(
            "alias -- broken='git push\nalias -g -- BROKEN='git push\nalias -s -- cfg='git push\n", encoding="utf-8")

    def test_a_command_word_the_lines_alias_may_run(self):
        for line in ("alias g=$Y; eval 'g push'", "alias g=$Y; echo $(g push)", "alias g=$Y; cat <(g push)",
                     "if true; then alias gp='git status'; fi; cat <(gp)", "(alias gp='git status'); echo $(gp)"):
            with self.subTest(line):
                r = self.assertRefused(line, "runs an alias this line defines that the hook cannot resolve")
                self.assertIn("text zsh parses as the line runs -- eval's words, a `$( )`, backtick or `<( )` body", r.reason)
                self.assertIn("spell out the command the alias stands for in place of its name", r.reason)
                self.assertNotIn("`eval` runs", r.reason)
                self.assertSilent(line, agent_id=None)

    def test_a_word_the_lines_global_or_suffix_alias_may_stand_in(self):
        for line in ("if true; then alias -g gp='; git status'; fi; echo $(echo gp)",
                     "if true; then alias -g gp='; git status'; fi; eval 'echo gp'",
                     "alias -g gp='; git status'; X=gp; eval echo $X"):
            with self.subTest(line):
                r = self.assertRefused(line, "a global or suffix alias the hook cannot resolve")
                self.assertIn("text zsh parses as the line runs", r.reason)
                self.assertIn("spell out what the alias stands for in place of its name", r.reason)
                self.assertNotIn("`eval` reads", r.reason)
                self.assertSilent(line, agent_id=None)

    def test_an_alias_of_the_shell_whose_body_the_hook_cannot_read(self):
        for line, word in (("broken", "broken"), ("echo hi BROKEN", "BROKEN"), ("a.cfg", "a.cfg"), ("x=$(b.cfg)", "b.cfg")):
            with self.subTest(line):
                r = self.assertRefused(line, "the word `%s` runs an alias your shell already defines whose body the hook"
                                             " cannot read" % word)
                self.assertIn("a global alias (`alias -g`) in any word", r.reason)
                self.assertIn("spell out the command the alias stands for in its place, or quote a global alias's name"
                              " where you mean the word as written (`'%s'`)" % word, r.reason)
                self.assertNotIn("the command word", r.reason)
                self.assertSilent(line, agent_id=None)

    def test_a_quoted_global_alias_name_is_no_alias(self):
        # the respelling the shell-alias reason offers for a global alias's name: zsh expands no alias of a word quoted in
        # any way (a quoted command word the hook still reads against the table, so the reason offers it only there)
        for line in ("echo hi 'BROKEN'", "echo hi \\BROKEN", 'echo hi "BROKEN"'):
            with self.subTest(line):
                r = self.bash(line, AGENT_A, None)
                self.assertNotIn("alias your shell already defines", r.reason or "", (line, r))


if __name__ == "__main__":
    unittest.main()
