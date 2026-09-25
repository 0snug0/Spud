"""PreToolUse(Bash): Claude Code's shell snapshot (the aliases and functions the shell already defines), and the reader
failing closed on text it cannot read."""

import contextlib
import importlib
import json
import os
import shutil
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from helpers import forget_process_caches, load_spud_module, wall_clock
from hookcase import AGENT_A, AGENT_B, AGENT_C, INLINE_WORDING, SCRIPT_WORDING, VARIABLE_WORDING, BashHookCase


# A snapshot of the shape Claude Code writes (SPD-133), with a name for each reading the hook makes of one.  The real
# files on this Mac are 4,100 lines and 124 KB; nothing here reads them unless RealShellSnapshotTest is asked to
# (REAL_SNAPSHOTS), and a test never touches ~/.claude.
SHELL_SNAPSHOT = """\
# Snapshot file
# Unset all aliases to avoid conflicts with functions
unalias -a 2>/dev/null || true
# Functions
ggp () {
\tgit push origin "${*}"
}
noteit () {
\techo hi > note.txt
}
keepit () {
\techo hi > tests/kept.txt
}
shadowed () {
\tlocal _cc_bin="${CLAUDE_CODE_EXECPATH:-}"
\tARGV0=ugrep "$_cc_bin" ${1+"$@"}
}
opendb () {
\tsqlite3 ledger.db 'select 1'
}
take () {
\tmkdir -p "$@"
}
# Shell Options
setopt autocd
setopt nohashdirs
# Aliases
alias -- g=git
alias -- gp='git push'
alias -- gpf='git push --force-with-lease --force-if-includes'
alias -- gc='git commit --verbose'
alias -- gcp='git cherry-pick'
alias -- grhh='git reset --hard'
alias -- gam='git am'
alias -- gst='git status'
alias -- gd='git diff'
alias -- glog='git log --oneline --decorate --graph'
alias -- ls='ls -G'
alias -- ll='ls -lh'
alias -- python=python3
alias -- grep='grep --color=auto'
alias -- awky='awk '\\''{print $1}'\\'' f'
alias -- _='sudo '
alias -- into='echo hi > note.txt'
alias -- intok='echo hi > tests/kept.txt'
alias -- toledger='echo hi > ledger/Home.md'
alias -- writes='cp a.txt note.txt'
alias -- md='mkdir -p'
alias -- broken='git push
# Shadow grep with the harness's own
unalias grep 2>/dev/null || true
function grep {
  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"
  ARGV0=ugrep "$_cc_bin" ${1+"$@"}
}
"""


class ShellSnapshotCase(BashHookCase):
    """A scratch ~/.claude/shell-snapshots of the test's own making, under the home's SPUD_USER_CLAUDE_DIR."""

    def setUp(self):
        super().setUp()
        self.snapshots = Path(self.home.env["SPUD_USER_CLAUDE_DIR"]) / "shell-snapshots"
        self.snapshots.mkdir(parents=True)
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", SHELL_SNAPSHOT)
        (self.home.path / "tests").mkdir(exist_ok=True)

    def write_snapshot(self, name, text, mtime=None):
        path = self.snapshots / name
        path.write_text(text, encoding="utf-8")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def refused_for_members(self, command, needle="Law 7", cwd=None):
        r = None
        for agent_id in (AGENT_A, AGENT_B):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id, cwd)
        return r

    def silent_for_everyone(self, command, cwd=None):
        for agent_id in (AGENT_A, AGENT_B, None):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertSilent(command, agent_id, cwd)


class ShellSnapshotTest(ShellSnapshotCase):
    """SPD-133: Claude Code starts a shell for every Bash call and sources its snapshot of the user's interactive shell in
    it (~/.claude/shell-snapshots/snapshot-zsh-<stamp>-<id>.sh), so a member's command word is expanded by that profile's
    aliases and run by its functions before any program does.  The hook read the word as written, so on this Mac `gp`
    pushed, `gc -m x` committed, `g commit -m x` committed and `ggp` pushed, each reaching the rule as an unknown command
    with no finding at all -- a hole straight through Law 7.

    Confirmed from a member's own Bash call before anything was written: `type gp` printed "gp is an alias for git push",
    `type gc` "gc is an alias for git commit --verbose", `type ggp` "ggp is a shell function from
    /Users/Someone/.claude/shell-snapshots/snapshot-zsh-1789793561771-kk8ad0.sh", and `gst --short --branch` ran git and
    printed the worktree's status with no finding.  The six snapshots there define 497 aliases and 223 functions between
    them; 190 aliases expand to git and 111 of those to a write verb.

    The table is parsed out of the snapshots themselves, never by running a shell, and cached under the home's .spud/ by
    each file's size and mtime.  A snapshot is read in line order, since the harness appends its own shadows after the
    alias block -- `unalias grep`, then `function grep { ... }` -- so on this Mac grep is a function and not the alias the
    same file defines earlier.  An alias is then expanded as zsh expands it: at command position only, textually, before
    any rule reads the word, chaining into the next word when the body ends in a blank, and never into its own name
    again.  A function's body is read as an `eval` string is, once per name per line.  The findings are the body's own,
    minus the ones that say only that the hook cannot read a word of it: the harness's own grep, find, rg and pkill each
    dispatch through `"$_cc_bin"`, and reading that as a refusal would refuse a member every grep it runs.

    A member plans home:tests/** and home:bin/spud (AGENT_A and AGENT_B), and the home is the cwd."""

    def test_the_tickets_evidence_commands(self):
        for cmd, verb in (("gp", "git push"), ("gpf", "git push"), ("gc -m x", "git commit"), ("g commit -m x", "git commit"),
                          ("gcp abc", "git cherry-pick"), ("grhh", "git reset"), ("gam x.patch", "git am"), ("ggp", "git push")):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn(verb, r.reason)

    def test_the_read_verbs_stay_silent(self):
        for cmd in ("gst", "gst --short --branch", "gd", "glog", "git status", "git log --oneline -5"):
            self.silent_for_everyone(cmd)

    def test_an_alias_that_shadows_a_program_stays_silent(self):
        for cmd in ("ls", "ls -la", "ll", "python --version", "grep -rn x .", "awky", "echo hi", "cat f"):
            self.silent_for_everyone(cmd)
        # SPD-150: the alias (python=python3) still shadows the program; the words after it are the member's own, so an
        # inline program behind one is refused exactly as it is spelled out, and stays Spud's.
        self.refused_for_members("python -c 'import os; os.remove(1)'", INLINE_WORDING)
        self.assertSilent("python -c 'import os; os.remove(1)'", agent_id=None)
        self.silent_for_everyone("python -c pass")  # SPD-175: a program whose text writes nothing runs

    def test_a_recursive_alias_terminates(self):
        """`alias ls='ls -G'` and `ll='ls -lh'`: zsh does not expand a name again inside its own expansion."""
        self.silent_for_everyone("ll -a")
        self.assertEqual(self.expansion("ll -a"), [("ll", "an alias for `ls -lh`"), ("ls", "an alias for `ls -G`")])
        self.assertEqual(self.expansion("ls"), [("ls", "an alias for `ls -G`")])

    def test_a_body_ending_in_a_blank_chains_into_the_next_word(self):
        """zsh expands the word after an alias whose body ends in a blank, which is what `_='sudo '` is for."""
        self.refused_for_members("_ gp")
        self.assertEqual([n for n, _ in self.expansion("_ gp")], ["_", "gp"])
        self.silent_for_everyone("_ ls")
        self.silent_for_everyone("sudo gp")  # a wrapper takes the command position: no chaining through it

    def test_the_command_position_alone(self):
        for cmd in ("command gp", "echo gp", "builtin gp", "env gp", "x=gp", "git log --grep gp"):
            self.silent_for_everyone(cmd)
        # `./gp` is no alias either, but a file run by its path: a member's file of commands since SPD-145
        r = self.refused_for_members("./gp", SCRIPT_WORDING)
        self.assertNotIn("snapshot", r.reason)
        self.assertSilent("./gp", None)
        self.assertEqual(self.expansion("command gp"), [])

    def test_a_function_that_runs_a_git_write(self):
        r = self.refused_for_members("ggp")
        self.assertIn("git push", r.reason)
        self.assertEqual(self.expansion("ggp"), [("ggp", "a shell function")])
        self.refused_for_members("ggp origin main")

    def test_a_function_that_dispatches_through_a_variable_stays_silent(self):
        """The harness's own shadows for grep, find, rg and pkill run `ARGV0=ugrep "$_cc_bin" ${1+"$@"}`: a command word
        the hook cannot resolve.  Reading that as a refusal would refuse a member every grep it runs, and the member
        neither wrote the text nor can change the file, so the findings that say only "the hook cannot read this" are
        dropped from a body the shell holds."""
        self.silent_for_everyone("shadowed x")
        self.silent_for_everyone("grep -rn x .")
        self.assertEqual(self.expansion("grep x"), [("grep", "a shell function")])  # the function, not the alias

    def test_what_the_hook_can_read_in_a_body_still_refuses(self):
        self.refused_for_members("opendb", "ledger database")

    def test_a_redirection_in_a_body_is_held_to_the_path_rule(self):
        """A file an alias's body or a function's writes is the caller's write, read by the path rule for whoever runs the
        line: refused where it is nobody's deliverable, refused for Spud wherever it is a member's, and allowed a member
        inside its own."""
        for cmd in ("into", "noteit", "writes"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "note.txt")
                self.assertRefused(cmd, "Law 1", agent_id=None)
        for cmd in ("intok", "keepit"):  # tests/** is what these members plan
            with self.subTest(cmd):
                self.assertSilent(cmd, AGENT_A)
                self.assertSilent(cmd, AGENT_B)
                self.assertRefused(cmd, "Law 1", agent_id=None)

    def test_spud_keeps_his_own_answers(self):
        """Law 7 does not bind Spud, so every alias and function that refuses a member here is silent for him; Law 1 and
        the path rule still read what one of them writes, and ledger/Home.md is one of his own paths."""
        for cmd in ("gp", "gc -m x", "ggp", "gst", "ls", "grep x", "broken", "toledger"):
            with self.subTest(cmd):
                self.assertSilent(cmd, agent_id=None)
        self.refused_for_members("toledger", "ledger/Home.md")

    def test_an_alias_body_the_hook_cannot_read(self):
        r = self.refused_for_members("broken", "cannot read")
        self.assertIn("broken", r.reason)
        self.assertSilent("broken", agent_id=None)

    def test_the_reason_names_what_the_shell_defined(self):
        r = self.assertRefused("gc -m x", "Law 7")
        self.assertIn("`gc` as an alias for `git commit --verbose`", r.reason)
        self.assertIn("~/.claude/shell-snapshots/", r.reason)
        self.assertNotIn("shell-snapshots", self.assertRefused("git commit -m x", "Law 7").reason)

    def test_the_harness_shadow_shape_is_read_in_order(self):
        """The snapshot defines `grep` as an alias and then unaliases it and defines a function of the same name; the
        shell ends with the function, and so does the table."""
        m = load_spud_module()
        built = m.build_table([str(self.snapshots / "snapshot-zsh-1700000000000-aaaaaa.sh")])
        self.assertNotIn("grep", built.aliases)
        self.assertIn("grep", built.functions)
        self.assertEqual(built.aliases["gp"], "git push")
        self.assertEqual(built.aliases["_"], "sudo ")  # the trailing blank survives
        self.assertEqual(built.aliases["awky"], "awk '{print $1}' f")  # one level of quoting off, the awk quotes kept
        self.assertIsNone(built.aliases["broken"])
        self.assertIn("git push origin", built.body("ggp"))

    def test_the_newest_snapshot_that_names_a_name_decides_it(self):
        older = "alias -- gp='git status'\nalias -- only='git push'\n"
        self.write_snapshot("snapshot-zsh-1600000000000-bbbbbb.sh", older, mtime=1600000000)
        os.utime(self.snapshots / "snapshot-zsh-1700000000000-aaaaaa.sh", (1700000000, 1700000000))
        self.refused_for_members("gp")            # the newer file's `git push`
        self.refused_for_members("only")          # named only by the older file
        self.silent_for_everyone("gst")

    def test_the_table_is_cached_under_the_state_directory_and_rebuilt_when_a_snapshot_changes(self):
        cache = self.home.path / ".spud" / "shell-snapshot.json"
        self.assertFalse(cache.exists())
        self.refused_for_members("gp")
        self.assertTrue(cache.is_file())
        stored = json.loads(cache.read_text(encoding="utf-8"))
        self.assertEqual(stored["aliases"]["gp"], "git push")
        self.assertEqual(len(stored["fingerprint"]), 1)
        before = cache.stat().st_mtime_ns
        self.silent_for_everyone("echo hi")  # a second run reads the cache and leaves it alone
        self.assertEqual(cache.stat().st_mtime_ns, before)
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", "alias -- gp='git status'\n")
        self.silent_for_everyone("gp")
        self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["aliases"]["gp"], "git status")
        for garbage in ("not json", "[]", '{"fingerprint": 1}'):  # a cache it cannot use is built again, never trusted
            with self.subTest(garbage=garbage):
                cache.write_text(garbage, encoding="utf-8")
                self.silent_for_everyone("gp")
                self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["aliases"]["gp"], "git status")

    def test_an_alias_never_launders_a_target_the_member_supplied(self):
        """An alias's expansion is its body followed by the member's OWN words, and a function's `$@` is them, so the
        prune that keeps `grep` and `find` silent must never reach a target the member wrote.  Before this narrowing
        `md $HOME/planted` (md='mkdir -p') recorded no write at all and was silent, while `mkdir -p $HOME/planted`
        spelled out was refused; the same for the chaining alias and for a function that writes its `$@`."""
        for cmd in ("md $HOME/planted", "mkdir -p $HOME/planted", "_ cp a $HOME/.gitconfig", "cp a $HOME/.gitconfig",
                    "take $HOME/planted", "md `echo x`", "md $(echo x)"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "spell the path out")
        # Spud meets the same refusal (SPD-091), behind an alias exactly as spelled out
        for cmd in ("md $HOME/planted", "mkdir -p $HOME/planted", "take $HOME/planted"):
            with self.subTest(cmd):
                self.assertRefused(cmd, "spell the path out", agent_id=None)

    def test_the_narrowing_keeps_a_resolvable_write_and_the_shadowed_commands(self):
        """What the prune is for is untouched: the `$_cc_bin` and `$data` shaped targets a body of its own writes.  A
        target the hook can resolve was never pruned, so the path rule reads it for the caller as it always did."""
        for cmd in ("md tests/scratch", "md tests/a/b"):
            with self.subTest(cmd):
                self.assertSilent(cmd, AGENT_A)
                self.assertSilent(cmd, AGENT_B)
                self.assertRefused(cmd, "Law 1", agent_id=None)
        self.refused_for_members("md scratchdir", "scratchdir")  # outside every deliverable of theirs
        for cmd in ("grep -rn x .", "find . -name x", "shadowed x", "ls", "python --version"):
            self.silent_for_everyone(cmd)

    def test_a_member_cannot_plant_a_snapshot(self):
        """Reading the shell's table is only safe because a member cannot write one: an alias of its own making would make
        a line the hook refuses silent (`alias git=echo` reaches the dispatch as `echo push`).  SPD-064 already refuses a
        caller with an agent_id every path outside a registered project, and ~/.claude is one -- asserted here because it
        is this reading's premise, not an accident of it.  Nothing is written: the hook is asked about the path.  SPD-233:
        the ~/.claude is the payloads' own user's (TRANSCRIPT's /Users/Someone), which no machine has, where it was this
        Mac's real one: the hook's answer is the path's, and the test reads nothing of the machine's."""
        planted = "/Users/Someone/.claude/shell-snapshots/snapshot-zsh-1900000000000-planted.sh"
        self.assertFalse(os.path.exists(planted))
        for agent_id in (AGENT_A, AGENT_B):
            with self.subTest(agent_id=agent_id):
                self.assertRefused("echo x > %s" % planted, "outside every registered project", agent_id)
                self.assertRefused("cp %s %s" % (self.home.path / "spud.config.json", planted), "outside every registered project", agent_id)
                r = self.home.hook("PreToolUse", self.pre_edit(planted, agent_id=agent_id))
                self.assertEqual(r.decision, "deny", r)
                self.assertIn("outside every registered project", r.reason)
        self.assertFalse(os.path.exists(planted))

    def test_no_snapshot_directory_keeps_the_reading_the_line_had(self):
        shutil.rmtree(self.snapshots)
        for cmd in ("gp", "gc -m x", "ggp", "ls", "grep x"):
            self.silent_for_everyone(cmd)
        self.refused_for_members("git push")  # the words as spelled are read as they always were
        self.assertFalse((self.home.path / ".spud" / "shell-snapshot.json").exists())
        self.assertFalse(self.home.spool.exists())  # a machine with no snapshots is no gap

    def test_an_unreadable_snapshot_fails_open_and_is_spooled(self):
        (self.snapshots / "snapshot-zsh-1700000000000-aaaaaa.sh").chmod(0o000)
        self.addCleanup((self.snapshots / "snapshot-zsh-1700000000000-aaaaaa.sh").chmod, 0o644)
        self.silent_for_everyone("gp")  # a member is never refused every command because the table is cold
        self.refused_for_members("git " + "push")  # and the words as spelled keep every reading they had
        gaps = self.events("hook.error")  # the spool the first run wrote is drained by the run after it
        self.assertTrue(gaps, self.home.spool.read_text(encoding="utf-8") if self.home.spool.exists() else "no spool")
        self.assertIn("the shell alias table is missing", gaps[-1]["body"])
        self.assertIn("the hook cannot read this Mac's shell snapshots", self.home.run("doctor", check=False).stderr)

    def test_doctor_reports_the_table(self):
        out = self.home.json("doctor")
        self.assertTrue(any("shell snapshot: " in n and "aliases" in n for n in out["notes"]), out["notes"])
        self.assertTrue(any("the hook cannot read" in n for n in out["notes"]), out["notes"])
        shutil.rmtree(self.snapshots)
        self.assertTrue(any("no shell snapshot in" in n for n in self.home.json("doctor")["notes"]))

    def expansion(self, command):
        """[(the name, what the shell runs for it)] the analysis recorded for this line."""
        m = load_spud_module()
        env = dict(os.environ)
        env["SPUD_USER_CLAUDE_DIR"] = self.home.env["SPUD_USER_CLAUDE_DIR"]
        with mock.patch.dict(os.environ, env, clear=True):
            a = m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))
        return a.shell_expanded


REAL_SNAPSHOTS = "SPUD_TEST_REAL_SHELL_SNAPSHOTS"  # set to 1 to run RealShellSnapshotTest against this machine's profile


class RealShellSnapshotTest(BashHookCase):
    """The same rule against this Mac's own ~/.claude/shell-snapshots: the ticket's evidence was Eric's oh-my-zsh profile,
    and nothing here is asserted about a name that profile does not define.  It checks that a name the shell really does
    define as a git write is refused, and that the names members run all day are not.

    SPD-233: what it reads is the machine's, not the tree's, so it is an explicit skip unless REAL_SNAPSHOTS is set to 1
    and the directory holds a snapshot: a run then says what it needs rather than passing on one Mac and skipping on
    another.  Every rule it checks is also asserted on a snapshot of the suite's own making -- a git write an alias names
    (ShellSnapshotTest), `__git_prompt_git`'s own body (FunctionWordsTest's `gitfn`), the commands members run all day
    (ShellSnapshotTest, test_hooks_programs.InlineProgramTest) -- so this is a check of this Mac's profile, run on purpose, and never the
    only test of a rule."""

    def setUp(self):
        directory = Path(os.path.expanduser("~/.claude")) / "shell-snapshots"
        if os.environ.get(REAL_SNAPSHOTS) != "1":
            self.skipTest("reads this machine's own %s: set %s=1 to run it" % (directory, REAL_SNAPSHOTS))
        if not directory.is_dir() or not list(directory.glob("snapshot-*.sh")):
            self.skipTest("needs a shell snapshot in %s, which Claude Code writes once a session has run Bash" % directory)
        super().setUp()
        m = load_spud_module()
        self.table = m.build_table(sorted((str(p) for p in directory.glob("snapshot-*.sh")), reverse=True))
        self.env = dict(self.home.env)
        self.env.pop("SPUD_USER_CLAUDE_DIR")  # this run reads the real ~/.claude

    def real_bash(self, command, agent_id=AGENT_A):
        env, self.home.env = self.home.env, self.env
        try:
            return self.bash(command, agent_id)
        finally:
            self.home.env = env

    def test_the_table_reads_this_macs_profile(self):
        self.assertIsNone(self.table.gap)
        self.assertTrue(self.table.aliases or self.table.functions)
        for name, body in sorted(self.table.aliases.items()):
            self.assertTrue(body is None or isinstance(body, str), name)

    def test_a_git_write_this_shell_really_defines_is_refused(self):
        found = [n for n, b in sorted(self.table.aliases.items()) if b and b.split()[:2] in (["git", "push"], ["git", "commit"])]
        if not found:
            self.skipTest("this profile aliases no git push or commit")
        r = self.real_bash(found[0])
        self.assertEqual(r.decision, "deny", (found[0], r))
        self.assertIn("Law 7", r.reason)

    def test_the_commands_members_run_all_day_stay_silent(self):
        for cmd in ("ls", "ls -la", "grep -rn spud .", "python3 --version", "cat /etc/hosts", "echo hi", "git status",
                    "find . -name x", "diff /etc/hosts /etc/hosts",
                    # SPD-246: two of the harness's shadows on one line (HarnessShadowTest)
                    "find . -name x | grep y", "grep a f | grep -v b", "grep -c a f; grep -c b f"):
            with self.subTest(cmd):
                r = self.real_bash(cmd)
                self.assertNotEqual(r.decision, "deny", (cmd, r.reason))
        # ... and one a member ran all day, refused by SPD-150 and allowed again by SPD-175 since its text writes nothing,
        # while one whose text writes is refused whatever this Mac's profile says (InlineProgramTest)
        self.assertNotEqual(self.real_bash("python3 -c pass").decision, "deny")
        self.assertIn(INLINE_WORDING, self.real_bash("python3 -c 'import os; os.remove(1)'").reason)

    def test_a_function_that_hands_its_words_to_git_reads_them(self):
        """SPD-203: oh-my-zsh defines `__git_prompt_git () { GIT_OPTIONAL_LOCKS=0 command git "$@" }`, which ran a
        member's push past Law 7 until the body was read with the call's words (FunctionWordsTest)."""
        name = "__git_prompt_git"
        if name not in self.table.functions:
            self.skipTest("this profile defines no %s" % name)
        r = self.real_bash(name + " push")
        self.assertEqual(r.decision, "deny", r)
        self.assertIn("Law 7", r.reason)
        r = self.real_bash(name + " status")
        self.assertNotEqual(r.decision, "deny", r.reason)

    def test_252_this_profile_s_takedir_and_mkcd_move_the_line(self):
        """SPD-252's evidence on this Mac's own profile (FunctionDirectoryTest holds the same definitions): a relative
        write after `takedir <dir>` or `mkcd <dir>` is read where `mkdir -p <dir> && cd <dir>` on the line leaves it."""
        names = [n for n in ("takedir", "mkcd") if n in self.table.functions]
        if not names:
            self.skipTest("this profile defines neither takedir nor mkcd")
        m, home, away = load_spud_module(), str(self.home.path), str(self.home.path / "away")
        for name in names:
            with self.subTest(name=name), mock.patch.dict(os.environ, self.env, clear=True):
                a = m.analyse_command("%s %s; echo hi > f" % (name, away), m.ShellAnalysis(cwd=home, home=home))
                self.assertEqual([c for t, c in a.redirects if t == "f"], [frozenset([home, away])])

    def test_262_a_function_behind_noglob_or_exec_is_read(self):
        """SPD-262's evidence on this Mac's own profile (ModifierLookupTest holds the same definition)."""
        if "ggp" not in self.table.functions:
            self.skipTest("this profile defines no ggp")
        for line in ("noglob ggp", "exec ggp"):
            with self.subTest(line=line):
                r = self.real_bash(line)
                self.assertEqual(r.decision, "deny", r)
                self.assertIn("Law 7", r.reason)

    def test_263_this_profile_s_options_change_nothing_the_hook_reads(self):
        """SPD-263: every option line this Mac's snapshots run is one the hook reads as changing nothing (SnapshotOptionsTest
        holds the same lines); a profile that sets another would fail here first, naming it."""
        m = load_spud_module()
        effects = [(line, m.option_effect(kind, name, on)) for kind, name, on, line, _ in self.table.options]
        self.assertTrue(effects)
        self.assertEqual([e for e in effects if e[1] is not None], [])

    def test_247_this_macs_shadows_are_the_harness_s_text(self):
        """SPD-247: every shadow the harness wrote into this Mac's snapshots is the text hooks/snapshots.HARNESS_SHADOWS
        recognizes, so its calls take the fast reading, which records what the full reading does (HarnessShadowReadingTest).
        A harness that writes other text fails here first, naming the shadow, and its calls are read in full meanwhile."""
        m, home = load_spud_module(), str(self.home.path)
        found = [name for name in ("find", "grep", "rg", "pkill") if name in self.table.functions]
        if not found:
            self.skipTest("this profile's snapshots hold none of the harness's shadows")
        self.assertEqual([n for n in found if m.harness_shadow(n, self.table.body(n)) is None], [])

        def reading(line):
            marks = {}
            with mock.patch.dict(os.environ, self.env, clear=True):
                a = m.analyse_command(line, m.ShellAnalysis(cwd=home, home=home))
            return {name: canonical(value, marks) for name, value in vars(a).items()}
        for line in ("grep -rn x .", "find . -name '*.py' | grep -v y", "rg -n foo src", "pkill -f 'node server'",
                     "CLAUDE_CODE_EXECPATH=/tmp/x.sh grep a f", "x=status; grep -rn x .; git $x"):
            with self.subTest(line=line):
                with mock.patch("spudlib.shell.held_shadows.read_shadow", return_value=False):  # read in full (SPD-290)
                    full = reading(line)
                self.assertEqual(reading(line), full)


SETTLED_SNAPSHOT = """\
# Functions
keepvar () {
\tlocal out=tests/kept.txt
\techo hi > $out
}
ledgervar () {
\tlocal out=ledger/Home.md
\techo hi > $out
}
datavar () {
\techo hi > "${CLAUDE_CODE_DATA:-}"/log
}
"""


class ResolvedTargetInShellTextTest(ShellSnapshotCase):
    """SPD-127 under SPD-133's prune: a body the shell holds keeps its writes whose target the hook can resolve and drops
    the ones it cannot, so that the harness's own shadows -- which write through `$_cc_bin` and `$data`-shaped names of
    their own -- do not refuse a member every grep it runs.  Resolution moves a target the body's own line settles from
    the second group into the first: it is a concrete file now, and the path rule reads it for the caller exactly as it
    reads the file an alias spells out.  A target nothing settles is as unresolvable as it was and stays pruned."""

    def setUp(self):
        super().setUp()
        self.write_snapshot("snapshot-zsh-1700000000002-cccccc.sh", SETTLED_SNAPSHOT)

    def test_a_body_that_settles_its_own_target_reaches_the_path_rule(self):
        for agent_id in (AGENT_A, AGENT_B):
            with self.subTest(agent_id=agent_id):
                self.assertSilent("keepvar", agent_id)  # tests/** is what these members plan
        self.assertRefused("keepvar", "Law 1", agent_id=None)
        self.refused_for_members("ledgervar", "ledger/Home.md")
        self.assertSilent("ledgervar", agent_id=None)  # ledger/Home.md is Spud's own, as `toledger` is

    def test_a_target_the_body_does_not_settle_stays_pruned(self):
        self.silent_for_everyone("datavar")


UNREADABLE = "the hook cannot read this line"  # SPD-191: the reason a line it cannot tokenize earns, every caller
UNREAD = "the hook cannot read part of what this line runs"  # SPD-217: text the reader did not read, refused a member


class UnreadableLineTest(ShellSnapshotCase):
    """SPD-191, filed by SPD-188's engineer: bash_refusal returned no reason for a line the hook could not tokenize
    (analysis.unparseable), so any line shlex cannot split -- a quote that never closes, or a backslash ending it with
    nothing to escape -- passed every law, for Spud and members alike.  A body the line hands another reading (a `$( )`,
    eval's words, a `-c` string, a here-document a shell reads) marked the whole analysis the same way, so `git push; eval
    "echo 'x"` dropped the push the outer words had already earned.  The ticket's evidence, on the SPD-188 tree: `git
    push<newline>echo 'x` was allowed a member, and `echo x > $(echo ')' >/dev/null; echo ledger/tickets/SPD-001.md)`,
    which split_substitutions ends at the quoted paren (SPD-194), was allowed Spud under Law 1.

    The rule now: a line any part of which the hook cannot tokenize is refused to every caller -- a bound member, Spud, a
    plain session and its subagents -- after every refusal the words it did read already earn, with a reason that names the
    quote (or the backslash), the text from it, where it stands, and how to spell the line.

    Probed 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, which printed the same, and in GNU bash 3.2.57; and the Bash tool's own shell, read with `ps` from a
    member's Bash call: `/bin/zsh -c 'source <snapshot> ... && eval '<line>' < /dev/null && pwd -P >| <file>'`:

    - zsh's eval parses the whole text before it runs any of it: `eval 'echo RAN1 > r1<newline>echo '\\''unbalanced'`
      wrote nothing in zsh (`(eval):2: unmatched '`), and neither did the same line on one row after `;`, the same inside
      `zsh -f -c "true && eval '...'"` (the Bash tool's shape), `zsh -f -c` of the text itself, or a `$( )` holding the
      stray quote; bash's eval wrote r1, and `sh -c`, `bash -c` and `bash -c 'eval ...'` of `echo RAN > r<newline>echo
      "unbalanced` each wrote r before they failed (`unexpected EOF while looking for matching`).  One row with `;` wrote
      nothing in bash either.  A script read from a here-document or a file ran its first line in both shells: `sh
      <<'EOF'`, `zsh -f <<'EOF'` (TMPPREFIX in the probe's directory), `bash -s <<< "..."`, `bash s.sh` and `zsh -f s.sh`
      of `echo RAN > r<newline>echo 'unbalanced` each wrote r.  So an unbalanced quote at the Bash tool's top level runs
      nothing, while the same text in bash's hands, or read as a script by either shell, runs every complete line before it.
    - three lines both shells read whole and the hook could not tokenize, each writing in all three: `echo $'\\'' > l/r1`
      (ANSI-C quoting, whose `\\'` shlex takes for a closing quote), `echo x > $(echo ')' >/dev/null; echo l/r2)`, and
      `echo RAN3 > l/r3 \\` (a lone backslash at the end of eval's text).

    AGENT_A plans home:tests/** and home:bin/spud; AGENT_C plans home:**."""

    TARGET = "ledger/tickets/SPD-001.md"

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        target = self.home.path / self.TARGET
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("orig\n", encoding="utf-8")
        (self.home.path / "docs").mkdir(exist_ok=True)
        self.m = load_spud_module()

    def analysis(self, command):
        return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def unreadable_for_everyone(self, line):
        """Refused to both members and to Spud, in the words of a line the hook cannot read."""
        for agent_id in (AGENT_A, AGENT_C, None):
            with self.subTest(line=line, agent_id=agent_id):
                self.assertRefused(line, UNREADABLE, agent_id)

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_is_refused(self):
        push = "git push\necho 'x"
        self.assertTrue(self.analysis(push).unparseable)
        self.assertRefused(push, UNREADABLE)  # the push, for a member
        self.assertRefused(push, UNREADABLE, AGENT_C)
        ledger = "echo x > $(echo ')' >/dev/null; echo %s)" % self.TARGET
        self.assertTrue(self.analysis(ledger).unparseable)  # SPD-194 reads the substitution; this ticket refuses the line
        self.assertRefused(ledger, UNREADABLE, agent_id=None)  # the ledger write, for Spud
        self.unreadable_for_everyone(push)
        self.unreadable_for_everyone(ledger)

    def test_lines_both_shells_read_whole_are_refused(self):
        """The reading gaps the class's probes found, each a write both shells made while the hook read nothing."""
        for line in ("git push \\", "echo x > %s \\" % self.TARGET):
            with self.subTest(line=line):
                self.assertTrue(self.analysis(line).unparseable)
            self.unreadable_for_everyone(line)
        # SPD-202 reads `$'\\''` as the shells do (AnsiCQuotingTest): the write is Law 1's for Spud, the push Law 7's
        self.assertRefused("echo $'\\'' > %s" % self.TARGET, "Law 1", agent_id=None)
        self.assertRefused("git push; echo $'\\''", "Law 7")

    # -- text another reading takes -----------------------------------------------------------------------------------
    def test_text_the_line_hands_another_reading_is_held_the_same(self):
        """A shell runs the complete lines before an unbalanced one (probed): bash in `sh -c` and `bash -c` text and its
        eval, either shell in a script it reads from a here-document or a here-string.  zsh's eval and `zsh -c` run none of
        it, and the line is refused all the same: the hook cannot tell a stray quote from one of its own reading gaps."""
        for line in ('sh -c "git push\necho \'x"', 'bash -c "echo x > %s\necho \'x"' % self.TARGET, "zsh -c \"echo 'x\"",
                     "sh <<'EOF'\ngit push\necho 'x\nEOF", "bash -s <<< \"git push\necho 'x\"", 'eval "git push\necho \'x"',
                     "x=$(echo 'x)", "echo `echo 'x`", "echo \"$(git push\necho 'x)\"", "echo $(eval \"echo 'x\")"):
            with self.subTest(line=line):
                self.assertTrue(self.analysis(line).unparseable)
            self.unreadable_for_everyone(line)

    def test_a_refusal_the_read_words_earn_keeps_its_reason(self):
        """A body the hook cannot read no longer takes the outer words' refusals with it: the one they earn stands, and a
        line that earns none is refused as one the hook cannot read."""
        for line in ('git push; eval "echo \'x"', "git push; sh -c \"echo 'x\"", "git push $(echo 'x)"):
            with self.subTest(line=line):
                self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
                self.assertRefused(line, "Law 7")
                self.assertRefused(line, "Law 7", AGENT_C)
                self.assertRefused(line, UNREADABLE, agent_id=None)  # Spud is never refused git
        line = "echo x > %s; eval \"echo 'x\"" % self.TARGET
        self.assertRefused(line, "Law 1", agent_id=None)
        self.assertRefused(line, "generated", AGENT_C)
        self.assertRefused("echo x > docs/x.md; eval \"echo 'x\"", "deliverables")

    # -- the reason ---------------------------------------------------------------------------------------------------
    def test_the_reason_names_what_stops_the_reading_and_where(self):
        reason = self.assertRefused("echo ok; echo 'unbalanced", UNREADABLE).reason
        self.assertIn("the `'` that opens `'unbalanced` never closes", reason)
        self.assertIn("'\\''", reason)  # ... and how to spell an apostrophe in single quotes
        reason = self.assertRefused("echo \"it's", UNREADABLE).reason
        self.assertIn("the `\"` that opens `\"it's` never closes", reason)
        reason = self.assertRefused("echo x \\", UNREADABLE).reason
        self.assertIn("the backslash that ends `echo x \\` has nothing to escape", reason)
        reason = self.assertRefused("echo 'x" + "y" * 200, UNREADABLE).reason
        self.assertIn("the `'` that opens `'x%s...` never closes" % ("y" * 38), reason)  # a long text is cut
        reason = self.assertRefused("echo 'a\n\tb", UNREADABLE).reason
        self.assertIn("the `'` that opens `'a b` never closes", reason)  # its blanks and newlines read as one space
        reason = self.assertRefused("echo $(echo 'x)", UNREADABLE).reason
        self.assertIn("the `'` that opens `'x` never closes in text the line hands another reading", reason)
        reason = self.assertRefused("echo x > 'a*b", UNREADABLE).reason
        self.assertIn("`'a*b`", reason)  # the text as the line spells it, the hook's own marks taken off
        reason = self.assertRefused("echo 'a $(b) c", UNREADABLE).reason
        self.assertIn("`'a $(b) c`", reason)

    def test_an_alias_expansion_reads_the_words_after_it_with_their_quotes(self):
        """The words after an alias of the shell's were read again after its body without their quotes, so `gc -m
        "don't"` (the snapshot's `git commit --verbose`) read as unparseable: a member's commit passed before this ticket,
        and was refused to every caller, Spud included, as a line the hook cannot read.  SPD-201 reads each word as the shell
        passes it (AliasWordsTest): the commit is Law 7's for a member and nothing for Spud.  Text the shell itself holds
        that the hook cannot read -- here a function whose `$$'\\''` zsh and bash quote apart (AnsiCQuotingTest; its
        `$'it\\'s'` read as an open quote before SPD-202) -- still earns the reason, which says the text is the shell's."""
        line = "gc -m \"don't\""
        self.assertIn("git commit", self.refused_for_members(line, "Law 7").reason)
        self.assertSilent(line, agent_id=None)
        self.write_snapshot("snapshot-zsh-1700000000003-dddddd.sh", "ansi () {\n\techo $$'\\''\n}\n")
        r = self.refused_for_members("ansi", UNREADABLE)
        self.assertIn("in the text an alias or function of your shell runs", r.reason)
        self.assertRefused("ansi", UNREADABLE, agent_id=None)

    # -- what stays as it was -----------------------------------------------------------------------------------------
    def test_a_line_the_hook_reads_is_answered_as_before(self):
        """An apostrophe the line quotes, escapes or holds in a comment or a here-document body is no stray quote."""
        for line in ("echo \"it's\"", "echo 'it'\\''s'", "echo it\\'s", "# it's a note\necho x", "cat <<'EOF'\nit's\nEOF",
                     "x=\"$(cat <<'EOF'\nit's\nEOF\n)\"", "echo \"$(echo \"it's\")\"", "echo \"a\\\"b\"", "echo x \\\n  y",
                     "sh -c \"echo it\\'s\"", "eval \"echo 'it'\"", "echo \"it's\" > /dev/null"):
            with self.subTest(line=line):
                self.assertFalse(self.analysis(line).unparseable)
                self.assertSilent(line)
                self.assertSilent(line, agent_id=None)

    def test_a_spud_call_the_hook_cannot_read_is_no_longer_silent(self):
        """The allow was never given (all_spud holds no unreadable line), but the call stood silent and the harness's
        rules decided it; it is refused now, and a readable one keeps its allow."""
        self.assertAllowed("%s --as %s member log \"it's done\"" % (self.spud_cli, AGENT_A))
        self.assertAllowed("%s --as spud board" % self.spud_cli, agent_id=None)
        self.assertRefused("%s --as %s member log \"it's done" % (self.spud_cli, AGENT_A), UNREADABLE)
        self.assertRefused("%s --as spud board 'x" % self.spud_cli, UNREADABLE, agent_id=None)


def single_quoted(text):
    """The text as one single-quoted shell word, an apostrophe in it spelled '\\''."""
    return "'" + text.replace("'", "'\\''") + "'"


class AliasWordsTest(ShellSnapshotCase):
    """SPD-201, filed by SPD-191's engineer: read_shell_name read an alias of the Bash tool's shell as its body followed by
    the words the line spelled after it, joined again after their quotes were taken off, so a quoted word read as other
    words.  `gc -m "don't"` (the snapshot's `git commit --verbose`) read as a line the hook cannot tokenize: a member's
    commit passed before SPD-191, and the line was refused to every caller after it, Spud included.  A quoted word holding
    blanks, an operator, a newline, a `$( )` or backticks read as more words or more commands than the shell runs: `ll "x;
    git push"` was Law 7's, `ll "x; echo hi > tests/kept.txt"` refused Spud under Law 1 for a write no shell makes, and `md
    "tests/a b"` wrote `b`.  The line aliases eval expands (SPD-059, SPD-105) joined their words the same way.

    The rule now: each word after the name reaches the body's reading as the shell passes it (prepare.requoted), its
    quoted characters still quoted and what the line left active -- a glob, a `$NAME`, a substitution -- still active, so
    the reading behind an alias is the reading of the same words spelled after its body.

    Probed 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, and in GNU bash 3.2.57 with `shopt -s expand_aliases`, which printed the same: with `alias show='printf
    "<%s>\\n"'` defined on a line before, `show "a b" 'c;d' "don't" '$(touch r1)' 'x`touch r2`' '#h' '' "e<newline>f" 'g >
    r3' "h\\\\i" x\\ y` printed each word whole on a row of its own (`<a b>`, `<c;d>`, `<don't>`, `<$(touch r1)>`,
    `<x`touch r2`>`, `<#h>`, `<>`, `<e<newline>f>`, `<g > r3>`, `<h\\i>`, `<x y>`) and wrote none of r1, r2 and r3; `alias
    chain='show '; chain show 'j;touch r4'` passed `j;touch r4` as one word; and `alias show2='printf "[%s]\\n"'; eval
    'show2 "a b" "c;touch r5" "don'\\''t" "" "#k" '\\''$(touch r6)'\\'''` printed `[a b]`, `[c;touch r5]`, `[don't]`, `[]`,
    `[#k]` and `[$(touch r6)]` and wrote nothing.

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud, and the home is the cwd."""

    # Words a line may spell after an alias, each read as the same words spelled after the alias's body
    WORDS = ("\"don't\"", "'a b'", "'a;b'", "\"a > b\"", "'a\nb'", "'$(x)'", "'`x`'", "'#h' 'a b'", "''", "\"h\\\\i\"",
             "x\\ y", "'*'", "'$HOME/x'", "\"$HOME/x\"", "$HOME/x", "$'a b'", "\"a'b\\\"c\"", "'{a,b}'", "\\#x 'a b'",
             "'a\tb'", "'a\rb'", "$(echo x)", "\"$(echo 'x y')\"", "\\; git\\ push", "a*b", "tests/{a,b}", "(a|b)/x",
             "x(a|b)", "<1-3>/x", "$((1+2))", "<(echo x) y", "\"${(e)X}\"")

    def setUp(self):
        super().setUp()
        (self.home.path / "tests" / "kept.txt").write_text("orig\n", encoding="utf-8")
        self.m = load_spud_module()

    def reading(self, command):
        """What the analysis of this line reads that a refusal rests on: whether it could tokenize it, the findings that are
        more than "the hook cannot read a word" (which the text an alias runs does not keep), and the files it writes."""
        env = dict(os.environ)
        env["SPUD_USER_CLAUDE_DIR"] = self.home.env["SPUD_USER_CLAUDE_DIR"]
        with mock.patch.dict(os.environ, env, clear=True):
            a = self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))
        return (a.unparseable, [f for f in a.findings if f[0] not in self.m.SHELL_TEXT_TOLERATED],
                [(e[0], e[1]) for e in a.arg_writes], [r[0] for r in a.redirects], [(g[0], g[1]) for g in a.git_writes])

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_is_a_commit(self):
        for line in ("gc -m \"don't\"", "gc -m 'it'\\''s'", "gc -m it\\'s", "gp origin \"don't\"", "_ gc -m \"don't\""):
            with self.subTest(line=line):
                self.assertIsNone(self.reading(line)[0])
                self.assertIn("git ", self.refused_for_members(line, "Law 7").reason)
                self.assertSilent(line, agent_id=None)
        self.assertEqual(self.reading("gc -m \"don't\"")[1], [("git", ("commit", "commit"))])

    def test_a_quoted_word_with_an_operator_stays_one_word(self):
        """`;`, `&&`, `|`, `>`, a newline, `$( )` and backticks inside quotes, and an escaped `;`, are characters of the
        word, which `ls` is handed; before, each ran a command or opened a file of its own."""
        for line in ("ll \"x; git push\"", "ll 'x && git push'", "ll 'x | git push'", "ll \"x\ngit push\"", "ll '$(git push)'",
                     "ll 'x`git push`'", "ll \\; git\\ push", "ll \"x; echo hi > note.txt\"", "ll 'x > note.txt'",
                     "ll \"x; echo hi > tests/kept.txt\"", "gp origin 'x; echo hi > tests/kept.txt'"):
            with self.subTest(line=line):
                if line.startswith("gp"):
                    self.refused_for_members(line, "Law 7")
                    self.assertSilent(line, agent_id=None)
                else:
                    self.silent_for_everyone(line)
        self.assertEqual((self.home.path / "tests" / "kept.txt").read_text(encoding="utf-8"), "orig\n")

    def test_a_quoted_word_with_blanks_stays_one_word(self):
        for line in ("md \"tests/a b\"", "md 'tests/a b'", "md tests/a\\ b", "md 'tests/a\tb'", "_ md \"tests/a b\""):
            with self.subTest(line=line):
                self.assertSilent(line, AGENT_A)
                self.assertSilent(line, AGENT_B)
                self.assertRefused(line, "Law 1", agent_id=None)  # a member's deliverable
        self.assertEqual(self.reading("md \"tests/a b\"")[2], [("mkdir", "tests/a b")])
        self.refused_for_members("md \"tests/a b\" b", "deliverables")  # a word of its own is still one

    # -- every word reads as it does after the body spelled out ---------------------------------------------------------
    def test_the_words_read_as_they_do_after_the_body_spelled_out(self):
        for alias, body in (("md", "mkdir -p"), ("gc -m", "git commit --verbose -m"), ("_ md", "sudo mkdir -p")):
            for words in self.WORDS:
                with self.subTest(alias=alias, words=words):
                    self.assertEqual(self.reading("%s %s" % (alias, words)), self.reading("%s %s" % (body, words)))

    def test_a_line_alias_eval_expands_reads_its_words_the_same_way(self):
        """SPD-059's alias table, read where eval parses its words again: the words after the name are the eval text's
        own, which the shell parses after the body with their quotes, as the probe's `show2` shows."""
        for words in self.WORDS:
            with self.subTest(words=words):
                aliased = "alias mk='mkdir -p'; eval %s" % single_quoted("mk " + words)
                spelled = "alias mk='mkdir -p'; eval %s" % single_quoted("mkdir -p " + words)
                self.assertEqual(self.reading(aliased), self.reading(spelled))
        line = "alias gp='git push'; eval %s" % single_quoted("gp origin \"don't\"")
        self.assertIsNone(self.reading(line)[0])
        self.refused_for_members(line, "Law 7")
        self.assertSilent(line, agent_id=None)
        for words in ("\"x; git push\"", "'x > note.txt'", "\"x\ngit push\""):
            with self.subTest(words=words):
                self.silent_for_everyone("alias e=echo; eval %s" % single_quoted("e " + words))
        line = "alias mk='mkdir -p'; eval %s" % single_quoted("mk \"tests/a b\"")
        self.assertSilent(line, AGENT_A)
        self.assertRefused(line, "Law 1", agent_id=None)


class AnsiCQuotingTest(ShellSnapshotCase):
    """SPD-202, filed by SPD-191's engineer: shlex read ANSI-C quoting, `$'...'`, as plain single quotes, so an escaped
    apostrophe inside one ended the quote for the hook and not for the shells.  `echo $'\\'' ; git push ; echo \\'` read
    as one echo of one quoted word -- kinds ['other'], no finding, not unparseable -- while both shells ran the push, and
    SPD-191's refusal of a line the hook cannot tokenize never reached it, since the misreading stays balanced.  A here-document
    delimiter spelled that way (`<<$'EOF'`) read as `$EOF`, so the body ran to the end of the text and every line after the
    real `EOF` was read as body.

    The rule now (prepare.ansi_c_quotes, after the here-documents are taken out): a backslash escapes the next character
    inside `$'...'`, and the string is read as its value where the shells decode it alike, a single-quoted literal: a
    command word, a git verb, a write target, eval's and `sh -c`'s text, an alias body, a value zsh's (e) evaluates.  A
    string holding an escape the shells decode apart keeps the old reading, an expansion the hook cannot resolve, now with its
    quote read where the shells end it; one that never closes, and a `$$` a quote follows, which zsh and bash quote apart,
    make the line one the hook cannot read.  A here-document delimiter is decoded the same way, and one the hook cannot know
    (an escape read apart, bash's `$"..."`, `$$'...'`) gets no body: the lines after it are read as commands.

    Probed 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, which printed the same, and in GNU bash 3.2.57(1)-release; LANG=C:

    - eval of `echo $'\\'' ; echo RAN > l/r1 ; echo \\'` printed an apostrophe twice and wrote l/r1 in all three.
    - Both shells decode \\a \\b \\e \\E \\f \\n \\r \\t \\v \\\\ \\' \\" \\?, one to three octal digits (`\\101` A, `\\0101`
      a backspace then 1, `\\18` \\001 then 8) and one or two hex digits (`\\x41g` Ag, `\\x414` A4) alike (od -c of each value).
      They part on the rest: `\\u0041` and `\\U...` (zsh A, bash 3.2 the text itself), `\\cA` (zsh cA, bash \\001), an
      unknown escape (`\\z`: zsh z, bash \\z), a bare `\\x` (zsh NUL, bash \\x), a backslash-newline (zsh drops the
      backslash, bash keeps it), and NUL (`a\\0b`: zsh keeps a NUL b, bash ends the value at a).  A value past 0x7f
      (`\\xff`) is one byte, which a command line held as text cannot spell, and the hook leaves it undecoded too.
    - The value is literal: `echo $'\\x24(touch s1)' $'\\x60touch s2\\x60'` printed both texts and made neither file,
      `echo $'g*' $'{a,b}' $'~'` printed them as spelled, `echo x > $'f\\x2eo'` wrote f.o and `echo y > $'\\x24HOME'` a
      file named $HOME; `$'\\x65cho' hi` ran echo, `eval $'echo a;echo b'` ran both, `sh -c $'echo RAN > l/shc\\necho two'`
      wrote l/shc, `echo x > $'l/a\\x2eb'` wrote l/a.b, `( trap $'echo RAN > l/trap' EXIT; true )` wrote l/trap, and zsh's
      `x=$'\\x24(touch l/e1)'; echo ${(e)x}` made l/e1 while `echo "$y"` of the same value printed it.
    - Where it is ANSI-C quoting: not inside double quotes (`"$'a'"` printed $'a', and `echo "$'\\''" > l/dq ; echo RAN >
      l/dq2` wrote both), not after an escaped `$` (`\\$'a'` printed $a), not in a here-document body (`$'a\\tb'` printed as
      is), but inside a `$( )` in double quotes (`"$(printf '%s' $'a\\tb')"` held a tab) and in an unquoted `${u:-$'a\\tb'}`.
      The escaped apostrophe ends nothing in any of them: `echo ${u:-$'\\''} ; echo RAN > l/b1 ; echo \\'`, the same line in
      backticks and in `x=$( )`, wrote in all three, and so did `echo ${u:-$'\\''} '<<EOF'`, a line `echo RAN > l/hb` and
      `EOF`, where no here-document opens.
      After `$$` the shells part: zsh reads `$$'...'` as the pid then ANSI-C quoting and bash as the pid then single quotes,
      so `echo $$'\\'' > /dev/null ; echo RAN > l/z ; echo '\\''` wrote l/z in zsh alone, as did `$$$$'...'` and `x$$'...'`;
      `$$$'...'` wrote in all three, and `${$}'...'` and `"$$"'...'` in none.
    - An unclosed one runs nothing: eval of `echo RAN > u1; echo $'a\\' ; echo RAN > u2` wrote neither file anywhere.
    - A here-document delimiter: `<<$'EOF'`, `<<$'E\\x4fF'`, `<<x$'y'"z"` and `<<$'E\\'F'` ended at EOF, EOF, xyz and E'F in
      both shells, `<<-$'EOF'` at a tab and EOF, and `<<\\$'E'` at $E; `<<$'A\\nB'` never ended.  They part on `<<$"EOF"` (zsh at $EOF, bash at EOF) and
      `<<$$'E'` (zsh at $E, bash at $$E).
    - `$"..."`: bash translates it (`$"a b"` printed a b), zsh reads `$` then double quotes (`$a b`); inside it both read
      double-quote rules (`$"a\\"b"`).  The hook keeps reading it as text it does not decode.

    AGENT_A plans home:tests/** and home:bin/spud, AGENT_C home:**, and the home is the cwd."""

    TARGET = "ledger/tickets/SPD-001.md"

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        target = self.home.path / self.TARGET
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("orig\n", encoding="utf-8")
        self.m = load_spud_module()

    def analysis(self, command):
        env = dict(os.environ)
        env["SPUD_USER_CLAUDE_DIR"] = self.home.env["SPUD_USER_CLAUDE_DIR"]
        with mock.patch.dict(os.environ, env, clear=True):
            return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def law_7(self, line):
        """Law 7 for both members, and nothing for Spud, who is never refused git."""
        for agent_id in (AGENT_A, AGENT_C):
            with self.subTest(line=line, agent_id=agent_id):
                self.assertRefused(line, "Law 7", agent_id)
        with self.subTest(line=line, agent_id="spud"):
            self.assertSilent(line, agent_id=None)

    def unresolved(self, line):
        """A member refused a word the hook does not decode; Spud reads on."""
        for agent_id in (AGENT_A, AGENT_C):
            with self.subTest(line=line, agent_id=agent_id):
                self.assertRefused(line, "cannot resolve", agent_id)
        with self.subTest(line=line, agent_id="spud"):
            self.assertSilent(line, agent_id=None)

    def unreadable(self, line):
        for agent_id in (AGENT_A, AGENT_C, None):
            with self.subTest(line=line, agent_id=agent_id):
                self.assertRefused(line, UNREADABLE, agent_id)

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence(self):
        push = "echo $'\\'' ; git push ; echo \\'"
        a = self.analysis(push)
        self.assertIsNone(a.unparseable)
        self.assertIn(("git", ("push", "push")), a.findings)
        self.law_7(push)
        ledger = "echo $'\\'' ; echo x > %s ; echo \\'" % self.TARGET
        self.assertRefused(ledger, "Law 1", agent_id=None)
        self.assertRefused(ledger, "generated", AGENT_C)
        self.assertRefused("echo $'\\'' ; echo x > note.txt ; echo \\'", "deliverables")
        self.assertRefused("echo $'\\'' > %s" % self.TARGET, "Law 1", agent_id=None)  # SPD-191 refused it as unreadable

    def test_an_escaped_apostrophe_ends_no_ansi_c_string_wherever_it_stands(self):
        evidence = "echo $'\\'' ; git push ; echo \\'"
        for line in ("x=$'\\'' ; git push ; y=\\'", "echo $'it\\'s' ; git push", "echo $'\\\\\\'' ; git push ; echo \\'",
                     "echo a$'\\''b ; git push ; echo \\'", "echo $'\\'' $'\\'' ; git push", "git push; echo $'\\''",
                     "eval " + single_quoted(evidence), "sh -c " + single_quoted(evidence), "bash -c " + single_quoted(evidence),
                     "x=$(%s)" % evidence, "echo \"$(%s)\"" % evidence, "echo `%s`" % evidence,
                     "sh <<'EOF'\n%s\nEOF" % evidence, "# it's a note\n" + evidence, "cat <<EOF\nit's\nEOF\n" + evidence,
                     "echo ${u:-$'\\''} ; git push ; echo \\'", "echo $'\\u00e9\\'' ; git push ; echo \\'",
                     "echo $$$'\\'' ; git push ; echo \\'", "echo ${u:-$'\\''} '<<EOF'\ngit push\nEOF"):
            with self.subTest(line=line):
                self.assertIsNone(self.analysis(line).unparseable)
            self.law_7(line)
        # an unquoted `${ }`'s ANSI-C string ends where the shells end it before a here-document operator too
        for agent_id in (AGENT_A, AGENT_C, None):
            self.assertSilent("echo ${u:-$'\\''} ; cat <<EOF\ngit push\nEOF", agent_id)

    # -- a value both shells decode alike is read as that value ---------------------------------------------------------
    def test_a_decoded_command_word_is_the_command_it_spells(self):
        for line in ("$'git' push", "$'\\x67it' push", "$'\\147it' push", "g$'i't push", "git $'push'", "git $'\\x70ush'",
                     "$'\\x67\\x69\\x74' $'\\x70\\x75\\x73\\x68'", "eval $'git push'", "eval $'echo a\\ngit push'",
                     "sh -c $'echo a\\ngit push'", "trap $'git push' EXIT", "alias gp=$'git push'; eval gp",
                     "x=$'git'; $x push", "env $'git' push", "$'\\x67it' $'--no-pager' push"):
            with self.subTest(line=line):
                self.law_7(line)

    def test_a_decoded_write_target_is_the_file_it_names(self):
        for target in ("$'ledger/tickets/SPD-001.md'", "$'ledger\\x2ftickets/SPD-001.md'", "$'\\x6cedger/tickets/SPD-001.md'",
                       "ledger/tickets/$'SPD-001\\x2emd'", "$'\\154edger'/tickets/SPD-001.md"):
            for line in ("echo x > %s" % target, "echo x | tee %s" % target, "cp /dev/null %s" % target):
                with self.subTest(line=line):
                    self.assertRefused(line, "Law 1", agent_id=None)
                    self.assertRefused(line, "generated", AGENT_C)
        self.assertRefused("echo x > $'no\\x74e.txt'", "deliverables")
        self.assertSilent("echo x > $'tests/\\x6b.py'")
        # a value zsh's (e) evaluates
        self.assertRefused("x=$'\\x24(touch note.txt)'; echo ${(e)x}", "deliverables")
        self.assertSilent("x=$'\\x24(touch tests/k.py)'; echo ${(e)x}")

    def test_a_decoded_value_is_literal(self):
        """A `$`, a backtick, `;`, a newline or a glob character the value holds is a character of the word."""
        for line in ("echo $'\\x24(git push)'", "echo $'\\x60git push\\x60'", "echo $'a\\x3b git push'", "echo $'a\\ngit push'",
                     "echo $'*' $'{a,b}' $'~'", "echo $'a\\'b' \"it's\"", "printf $'%s\\n' x", "echo $'\\x27' ; echo ok",
                     "echo $'a\\tb' > /dev/null", "echo $'\\x24HOME'", "x=$'\\x24(git push)'; echo \"$x\""):
            with self.subTest(line=line):
                self.assertIsNone(self.analysis(line).unparseable)
                for agent_id in (AGENT_A, AGENT_C, None):
                    self.assertSilent(line, agent_id)
        self.assertAllowed("%s --as %s member log $'it\\'s done\\n'" % (self.spud_cli, AGENT_A))
        # a target holding a literal `$` reads as the same target single-quoted, whoever writes it
        for agent_id in (AGENT_A, AGENT_C, None):
            with self.subTest(agent_id=agent_id):
                decoded, spelled = self.bash("echo y > $'\\x24HOME'", agent_id), self.bash("echo y > '$HOME'", agent_id)
                self.assertEqual((decoded.decision, decoded.reason), (spelled.decision, spelled.reason))

    # -- a value the shells decode apart stays unresolved ---------------------------------------------------------------
    def test_an_escape_the_shells_read_apart_is_not_decoded(self):
        for word in ("$'\\u0067it'", "$'\\U00000067it'", "$'\\cGit'", "$'\\git'", "$'g\\x'", "$'g\\0it'", "$'g\\x00it'",
                     "$'\\xe9'", "$'\\351'", "$'g\\\nit'", "$'\\400git'"):
            with self.subTest(word=word):
                self.unresolved("%s push" % word)
                self.unresolved("git %s" % word)
                self.assertRefused("echo x > %s" % word, "cannot resolve", agent_id=None)
        self.assertRefused("x=$'\\u0024(date)'; echo ${(e)x}", "zsh's (e) flag")

    def test_an_ansi_c_string_that_never_closes_is_unreadable(self):
        line = "echo $'it\\'s ; git push"
        self.assertEqual(self.analysis(line).unparseable[0], "$'")
        self.unreadable(line)
        self.assertIn("the `$'` that opens `$'it\\'s ; git push` never closes", self.assertRefused(line, UNREADABLE).reason)
        self.unreadable("echo x > $'%s" % self.TARGET)

    def test_a_double_dollar_before_a_quote_is_unreadable(self):
        """zsh reads `$$'...'` as the pid then ANSI-C quoting and bash as the pid then single quotes: where the two end the
        quote apart, one of them runs what the other reads as text.  The words bash's reading finds are read on, so a push
        that only bash runs is Law 7's for a member, and one that only zsh runs is refused as a line the hook cannot read."""
        for line in ("echo $$'\\'' > /dev/null ; git push ; echo '\\'", "echo $$'\\' ; git push ; echo '\\'",
                     "echo x$$'\\'' ; git push ; echo '\\'", "echo $$$$'\\'' ; git push ; echo '\\'"):
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).unparseable[0], "$$'")
            if line.startswith("echo $$'\\' "):  # bash pushes
                self.refused_for_members(line, "Law 7")
                self.assertRefused(line, UNREADABLE, agent_id=None)
            else:  # zsh pushes
                self.unreadable(line)
        reason = self.assertRefused("echo $$'\\'' > /dev/null ; git push ; echo '\\'", UNREADABLE).reason
        self.assertIn("`$$'\\''", reason)
        self.assertIn("${$}", reason)  # ... and how to spell the pid before a quote
        # a quote after `$$` that holds no backslash ends at the same place in both, and so does `${$}`'s and `"$$"`'s
        self.law_7("echo $$'x' ; git push")
        for line in ("echo ${$}'\\'' > /dev/null ; git push ; echo '\\'", "echo \"$$\"'\\'' > /dev/null ; git push ; echo '\\'"):
            with self.subTest(line=line):
                self.assertIsNone(self.analysis(line).unparseable)
                for agent_id in (AGENT_A, AGENT_C, None):
                    self.assertSilent(line, agent_id)

    # -- a here-document delimiter --------------------------------------------------------------------------------------
    def test_a_here_document_delimiter_is_decoded(self):
        for line in ("cat <<$'EOF'\nbody\nEOF\ngit push", "cat <<$'E\\x4fF'\nbody\nEOF\ngit push",
                     "cat <<x$'y'\"z\"\nbody\nxyz\ngit push", "cat <<$'E\\'F'\nbody\nE'F\ngit push",
                     "sh <<$'EOF'\ngit push\nEOF", "cat <<-$'EOF'\n\tbody\n\tEOF\ngit push"):
            with self.subTest(line=line):
                self.assertIsNone(self.analysis(line).unparseable)
            self.law_7(line)
        for line in ("cat <<$'EOF'\ngit push\nEOF", "cat <<\\$'E'\nbody\ngit push\n$E", "cat <<$'EOF'\nit's\nEOF"):
            with self.subTest(line=line):
                for agent_id in (AGENT_A, AGENT_C, None):
                    self.assertSilent(line, agent_id)

    def test_a_delimiter_the_hook_cannot_know_reads_no_body(self):
        """Where the shells end the body apart, or the hook does not decode its delimiter, every line after the operator is
        read as the commands it may be."""
        for line in ("cat <<$'\\u0045OF'\nbody\nEOF\ngit push", "cat <<$\"EOF\"\nbody\nEOF\ngit push\n$EOF",
                     "sh <<$'\\cE'\ngit push\n\\cE", "cat <<$$'E'\nbody\n$E\ngit push\n$$E"):
            with self.subTest(line=line):
                self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
                self.assertEqual(self.analysis(line).kinds.count("git"), 1)
        for line in ("cat <<$'\\u0045OF'\nbody\nEOF\ngit push", "cat <<$\"EOF\"\nbody\nEOF\ngit push\n$EOF",
                     "sh <<$'\\cE'\ngit push\n\\cE"):
            self.law_7(line)
        # zsh ends this one at a line `$E`, whose command word refuses a member before the push does
        self.refused_for_members("cat <<$$'E'\nbody\n$E\ngit push\n$$E", "cannot resolve")

    # -- what stays as it was -------------------------------------------------------------------------------------------
    def test_a_locale_string_stays_text_the_hook_does_not_decode(self):
        for line in ("$\"git\" push", "git $\"push\""):
            self.unresolved(line)
        self.assertRefused("echo x > $\"notes.txt\"", "cannot resolve", agent_id=None)
        self.law_7("echo $\"it's\" ; git push")
        self.law_7("echo $\"a\\\"b\" ; git push")

    def test_no_ansi_c_quoting_inside_double_quotes_or_after_an_escaped_dollar(self):
        self.law_7("echo \"$'\\''\" ; git push ; echo \"'\"")
        self.law_7("echo \\$'a' ; git push")
        for line in ("echo \"it's $'x'\"", "echo \"$'\\''\" > /dev/null"):
            with self.subTest(line=line):
                for agent_id in (AGENT_A, AGENT_C, None):
                    self.assertSilent(line, agent_id)

    def test_an_alias_of_the_shell_in_ansi_c_quoting(self):
        """zsh prints an alias whose body holds a newline in ANSI-C quoting (`alias -L` printed `alias nl=$'echo a\\necho
        b'`, probed), and the snapshot keeps that spelling: the body is its value, two commands, not `echo anecho b`.  A
        body holding an escape the hook does not decode is one it cannot read (zsh printed a carriage return as `\\C-M`)."""
        self.write_snapshot("snapshot-zsh-1700000000004-eeeeee.sh",
                            "alias -- nlp=$'echo a\\ngit push'\nalias -- crp=$'git push\\C-M'\n")
        self.refused_for_members("nlp", "Law 7")
        self.assertIn("crp", self.refused_for_members("crp", "alias your shell already defines").reason)


# SPD-203: functions of the shell's that hand their call's words on, one per shape of reference to them.  `gitfn` is this
# Mac's oh-my-zsh `__git_prompt_git`, and `ccgrep` the shape of Claude Code's own grep shadow on this Mac, whose loop over
# the words and case patterns earn findings of the body's own that the prune drops.
FUNCTION_WORDS_SNAPSHOT = """\
gitfn () {
\tGIT_OPTIONAL_LOCKS=0 command git "$@"
}
gitone () {
\tgit $1
}
gitdq () {
\tgit "$1" "${2}"
}
gitstar () {
\tgit "$*"
}
gitplus () {
\tgit ${1+"$@"}
}
gitdefault () {
\tgit "${1:-status}"
}
gitlast () {
\tgit ${@:$#}
}
gitsub () {
\techo "$(git "$@")"
}
gitsh () {
\tsh -c 'git "$@"' _ "$@"
}
gitshift () {
\tshift
\tcommand git "$@"
}
gitvar () {
\tlocal verb=$1
\tshift
\tgit $verb "$@"
}
gitloop () {
\tfor a
\tdo
\t\tgit $a
\tdone
}
gitmod () {
\tgit $1:t
}
gitten () {
\tgit $10
}
mk () {
\tmkdir -p "$@"
}
selfcall () {
\tgit "$@"
\tselfcall "$@"
}
grow () {
\tgrow x "$@"
\tgrow y "$@"
}
function ccgrep {
  local _cc_a
  for _cc_a in ${1+"$@"}; do
    case "$_cc_a" in -*-filter*|-*-config*|---*|-@*|-[Zz]*|-[!-]*[Zz]*|--null) command grep ${1+"$@"}; return ;; esac
  done
  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"
  ARGV0=ugrep "$_cc_bin" -G --hidden ${1+"$@"}
}
"""


class FunctionWordsTest(ShellSnapshotCase):
    """SPD-203, filed by SPD-201's engineer: analyse_shell_text dropped every finding that says only "the hook cannot read
    this word" from the text a function or an alias of the shell's runs, whatever made it -- the member's own words
    included.  A function receives its call's words as its positional parameters, so this Mac's oh-my-zsh
    `__git_prompt_git () { GIT_OPTIONAL_LOCKS=0 command git "$@" }` pushes for `__git_prompt_git push`; the hook read the
    body as it stands, `git "$@"`, recorded only ('git', ('$@', None)), which Law 7 does not refuse (git_not_allowed leaves
    an unresolvable verb to the expansion's own finding), and dropped the expansion's finding: `gitfn push` and `gitfn
    commit -m x` passed a member.  After an alias the member's words are the line's own: `_ $(echo git) push` (`_='sudo
    '`) recorded no finding at all, where `sudo $(echo git) push` records ('var', '$(...)').

    The rule now (shell/positional, held_text.analyse_shell_text):

    - a function's body is read with the call's words set where it reads its parameters, each quoted again as the line
      spelled it, so `gitfn push` reads as `git push` and `gitfn status` as `git status`;
    - where the substitution cannot follow the words (`shift`, `set`, a loop with no list, a function defined in the body,
      zsh's modifiers and subscripts, `$10`), the body is read as it stands and, for a call with words, keeps every
      finding it earns, as does a reference left in a string another reading takes (`sh -c '... "$@"' _ "$@"`);
    - a finding that spells a word of the member's the hook cannot read (a substitution, a `$NAME`, a glob) is kept
      in the text an alias or a function runs, as a write target the member supplied already was.

    What the shells do with each reference is probed in shell/positional's docstring.  AGENT_A and AGENT_B plan
    home:tests/** and home:bin/spud, and the home is the cwd."""

    def setUp(self):
        super().setUp()
        self.write_snapshot("snapshot-zsh-1700000000005-ffffff.sh", FUNCTION_WORDS_SNAPSHOT)
        self.m = load_spud_module()

    def analysis(self, command):
        env = dict(os.environ)
        env["SPUD_USER_CLAUDE_DIR"] = self.home.env["SPUD_USER_CLAUDE_DIR"]
        with mock.patch.dict(os.environ, env, clear=True):
            return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def reading(self, command):
        """What a refusal rests on (AliasWordsTest.reading): whether the line tokenized, the findings past "the hook
        cannot read a word", and the files it writes."""
        a = self.analysis(command)
        return (a.unparseable, [f for f in a.findings if f[0] not in self.m.SHELL_TEXT_TOLERATED],
                [(e[0], e[1]) for e in a.arg_writes], [r[0] for r in a.redirects], [(g[0], g[1]) for g in a.git_writes])

    def spud_silent(self, *lines):
        """Law 7 and the expansion findings bind members alone: Spud reads on."""
        for line in lines:
            with self.subTest(line=line, agent_id="spud"):
                self.assertSilent(line, agent_id=None)

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence(self):
        for line, verb in (("gitfn push", "git push"), ("gitfn commit -m x", "git commit"), ("gitfn push origin main", "git push")):
            with self.subTest(line=line):
                r = self.refused_for_members(line, "Law 7")
                self.assertIn(verb, r.reason)
                self.assertIn("`gitfn` as a shell function", r.reason)
        self.assertIn(("git", ("push", "push")), self.analysis("gitfn push").findings)
        self.refused_for_members("_ $(echo git) push", "comes from a variable or a substitution")
        self.silent_for_everyone("gitfn status")
        self.silent_for_everyone("gitfn log --oneline -5")
        self.spud_silent("gitfn push", "gitfn commit -m x", "_ $(echo git) push")

    # -- each reference the substitution reads ------------------------------------------------------------------------
    def test_each_reference_reads_the_call_s_words(self):
        for line in ("gitone push", "gitdq push", "gitdq push status", "gitstar push", "gitplus push", "gitdefault push",
                     "gitlast status push", "gitsub push", "gitfn \"push\"", "gitfn 'push'", "gitfn --no-pager push",
                     "gitfn push \"don't\"", "gitdq \"push\""):
            with self.subTest(line=line):
                self.refused_for_members(line, "Law 7")
        for line in ("gitone status", "gitdq status", "gitdq status push", "gitstar status", "gitplus", "gitplus status",
                     "gitdefault", "gitdefault log", "gitlast push status", "gitsub status", "gitfn", "gitfn \"don't\" status"):
            with self.subTest(line=line):
                self.assertNotIn(("git", ("push", "push")), self.analysis(line).findings)
        for line in ("gitone status", "gitdq status push", "gitstar status", "gitplus", "gitdefault", "gitlast push status",
                     "gitsub status", "gitfn"):
            self.silent_for_everyone(line)
        self.spud_silent("gitone push", "gitstar push", "gitplus push", "gitdefault push", "gitlast status push", "gitsub push")

    def test_the_substitution_s_text(self):
        """The text shell/positional makes of a body for a call's words (masked words, as the line's reading gives them),
        each as the shells read the reference (probed in shell/positional's docstring); False where it cannot follow."""
        sub = self.m.substituted
        words = ["x", "", "y"]
        for body, expected in (("git \"$@\"", "git x '' y"), ("git $@", "git x y"), ("git \"$*\"", "git x\\ \\ y"),
                               ("git \"[$1]\"", "git \"[\"x\"]\""), ("git \"$2\"", "git ''"), ("git $#", "git 3"),
                               ("git ${1+\"$@\"}", "git x '' y"), ("git ${@:$#}", "git y"), ("git \"${@:2}\"", "git '' y"),
                               ("git \"${@:2:1}\"", "git ''"), ("echo '$1' \"$1\"", "echo '$1' x"),
                               ("echo \"$(git \"$@\")\"", "echo \"$(git x '' y)\""), ("# $1 x\ngit $1", "# $1 x\ngit x"),
                               ("git \"a $@ b\"", "git \"a \"x '' y\" b\""), ("git ${@:$#} ${0} $$", "git y ${0} $$"),
                               ("(( $# > 1 )) && git $3", "(( 3 > 1 )) && git y"), ("echo $(( $# + 1 ))", "echo $(( 3 + 1 ))"),
                               ("echo ${X:-a} $X", "echo ${X:-a} $X")):
            with self.subTest(body=body):
                self.assertEqual(sub(body, words), (expected, True))
        for body, call, expected in (("git ${1+\"$@\"}", [], "git "), ("git \"${1:-status}\"", [], "git \"status\""),
                                     ("git \"${1:-status}\"", ["push"], "git push"), ("git ${@:-.}", [], "git ."),
                                     ("git \"${@:-.}\"", ["a", "b"], "git a b"), ("git \"$@\"", ["*"], "git *"),
                                     ("git \"$1\"", ["a b"], "git a\\ b"), ("git ${2:+z} ${2-w}", ["a"], "git  w")):
            with self.subTest(body=body, call=call):
                self.assertEqual(sub(body, call), (expected, True))
        for body, call in (("git $1", ["a b"]), ("git x$@", ["", "b"]), ("git x\"$@\"", ["*"]), ("git $1:t", ["a"]),
                           ("git $10", ["a"]), ("git $@[1]", ["a"]), ("git $#x", ["a"]), ("shift; git \"$@\"", ["a"]),
                           ("set -- q; git \"$@\"", ["a"]), ("for a; do git $a; done", ["a"]), ("getopts ab o", ["a"]),
                           ("f () { git \"$@\"; }", ["a"]), ("git ${@:0}", ["a"]), ("git ${@:-.}", [""]),
                           ("git ${(q)1}", ["a"]), ("git ${#1}", ["a"]), ("git ${x:-$1}", ["a"]), ("echo `echo $1`", ["a"]),
                           ("cat <<E\n$1\nE", ["a"]), ("echo $(( $1 + 1 ))", ["a"]), ("git $argv", ["a"])):
            with self.subTest(body=body, call=call):
                self.assertEqual(sub(body, call), (body, False))

    def test_the_words_read_as_they_do_spelled_after_the_body(self):
        """`mk () { mkdir -p "$@" }` with each word AliasWordsTest reads after an alias: the reading of `mk <words>` is the
        reading of `mkdir -p <words>`, quotes, globs, expansions and substitutions as the line spelled them."""
        for words in AliasWordsTest.WORDS:
            with self.subTest(words=words):
                self.assertEqual(self.reading("mk " + words), self.reading("mkdir -p " + words))
        for line in ("mk tests/a", "mk \"tests/a b\"", "mk tests/a tests/b"):
            with self.subTest(line=line):
                self.assertSilent(line, AGENT_A)
                self.assertSilent(line, AGENT_B)
        self.refused_for_members("mk note", "deliverables")
        self.refused_for_members("mk $HOME/planted", "spell the path out")

    def test_a_function_named_twice_reads_each_call(self):
        """Each call is read with its own words: the body was read once per name per line, which let the second call
        of `gitfn status; gitfn push` go unread."""
        for line in ("gitfn status; gitfn push", "gitfn push; gitfn status", "gitfn status && gitfn commit -m x",
                     "gitone status | gitone push"):
            with self.subTest(line=line):
                self.refused_for_members(line, "Law 7")
        self.silent_for_everyone("gitfn status; gitfn log")
        self.assertEqual(self.analysis("gitfn status; gitfn push").shell_expanded,
                         [("gitfn", "a shell function"), ("gitfn", "a shell function")])

    def test_one_name_s_readings_have_a_bound(self):
        """Past READINGS_PER_NAME calls of one name with other words, the body is read once more as it stands, and the
        member's words keep what it earns: refused on doubt, never read short.  A body that calls itself -- with the same
        words, or with more each time -- ends."""
        cap = self.m.READINGS_PER_NAME
        reads = "; ".join("gitfn log -%d" % k for k in range(1, cap + 1))
        self.silent_for_everyone(reads)
        self.refused_for_members(reads + "; gitfn push", "cannot resolve")
        self.refused_for_members(reads + "; gitfn log -%d" % (cap + 1), "cannot resolve")
        self.spud_silent(reads + "; gitfn push")
        for line in ("selfcall push", "grow push", "grow status"):
            with self.subTest(line=line):
                self.assertLessEqual(len(self.analysis(line).shell_expanded), cap + 2)
        self.refused_for_members("selfcall push", "Law 7")

    # -- where the substitution cannot follow the words ---------------------------------------------------------------
    def test_a_body_the_substitution_cannot_follow_keeps_its_findings(self):
        """`shift`, a loop with no list, a variable the body fills from a word it then shifts past, zsh's `:t` and `$10`:
        the body is read as it stands, and for a call with words every finding it earns stands, so the member is
        refused on doubt -- a readable verb too -- and spells the command out."""
        for line in ("gitshift x push", "gitshift x status", "gitvar push", "gitvar status", "gitloop push", "gitmod d/push",
                     "gitten 1 2 3 4 5 6 7 8 9 push"):
            with self.subTest(line=line):
                r = self.refused_for_members(line, "cannot resolve")
                self.assertIn("as a shell function", r.reason)
        self.spud_silent("gitshift x push", "gitvar push", "gitloop push", "gitmod d/push")
        # with no words there is nothing of the member's for the body to read, and it reads as it did before
        for line in ("gitshift", "gitvar", "gitloop", "gitmod"):
            self.silent_for_everyone(line)

    def test_a_reference_another_reading_takes_is_kept(self):
        """`sh -c 'git "$@"' _ "$@"` hands the words to a shell whose string the substitution does not reach (a single-quoted
        string is its reading's, not the body's): the reference the string reads is kept for a call with words."""
        self.refused_for_members("gitsh push", "cannot resolve")
        self.refused_for_members("gitsh status", "cannot resolve")
        self.spud_silent("gitsh push")
        self.silent_for_everyone("gitsh")

    # -- the member's own words the hook cannot read ------------------------------------------------------------------
    def test_a_word_of_the_member_s_the_hook_cannot_read_keeps_its_finding(self):
        for line in ("gitfn $(echo push)", "gitfn $X", "gitfn \"$X\"", "gitone $X", "gitfn `echo push`", "gitplus $X",
                     "g $(echo push)", "g $X", "g \"$X\"", "_ $X push", "_ \"$(echo git)\" push", "_ g$X push"):
            with self.subTest(line=line):
                self.refused_for_members(line, "cannot resolve")
        self.spud_silent("gitfn $(echo push)", "g $X", "_ $X push", "_ $(echo git) push")
        # a value the line settles is read as the word it is
        self.refused_for_members("X=push; gitfn $X", "Law 7")
        self.silent_for_everyone("X=status; gitfn $X")

    def test_what_the_body_itself_cannot_read_stays_dropped(self):
        """The harness's shadows walk the words and dispatch through `"$_cc_bin"`: the findings of the body's own text stay
        dropped whatever the member's words are, a glob, an expansion or a substitution among them."""
        for line in ("ccgrep -rn x .", "ccgrep x *", "ccgrep x $(echo f)", "ccgrep $X f", "ccgrep -e 'a b' f", "ccgrep",
                     "shadowed x *", "shadowed $(echo x)", "grep -rn x .", "grep x *", "grep x $HOME", "_ ls $X", "ll $X",
                     "_ echo $(echo git) push", "gitfn status $X"):
            with self.subTest(line=line):
                self.silent_for_everyone(line)


class ReaderFailsClosedTest(ShellSnapshotCase):
    """SPD-217, Eric's call of 2026-09-23: the one fail-closed rule that replaces the per-form hole tickets.  Where the
    reader cannot read what the shell will run -- text dropped past a bound, an unlifted placeholder, a value a shell
    evaluates as code in a form the reader does not model, or a shell in a substitution fed input the line does not spell
    -- it refuses the member and names the form with a readable respelling, instead of modelling the form or letting the
    text pass.  SPD-191 set the precedent for a line the hook cannot tokenize (UnreadableLineTest); this extends it to
    text the reader reads but does not finish.  Spud is allowed, as under every Law 7 fence: the fence keeps a
    cooperative spudagent in its lane, not an author composing shell to defeat it, and the laws bind Spud where the hook
    cannot see.

    Each case below is a folded ticket, its origin named, each refused a member (Law 7's `git push` a witness), silent
    for Spud, with the plain shape a member types still allowed.  Where the shell's behaviour is load-bearing it was
    probed through tests/probes/shell_probe.py in zsh 5.9 -f -o nobareglobqual, zsh -f and bash 3.2.57.

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud; the home is the cwd."""

    VCS = "git push"  # the write verb a member hides in text the hook does not read
    LEDGER = "ledger/tickets/SPD-001.md"

    def setUp(self):
        super().setUp()
        (self.home.path / "tests").mkdir(exist_ok=True)
        (self.home.path / "docs").mkdir(exist_ok=True)
        target = self.home.path / self.LEDGER
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("orig\n", encoding="utf-8")
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.m = load_spud_module()

    def analysis(self, command):
        return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def refused_unread(self, line):
        """A member is refused the line as text the hook did not read; Spud reads on."""
        with self.subTest(line=line):
            r = self.refused_for_members(line, UNREAD)
            self.assertSilent(line, agent_id=None)
            return r

    def hides_the_push(self, line):
        """The line hides a git write in text the hook does not read: a member refused as unread, Spud silent."""
        with self.subTest(line=line):
            self.refused_for_members(line, UNREAD)
            self.assertSilent(line, agent_id=None)

    # -- SPD-195: a substitution nested past the depth bound is refused, never dropped --------------------------------
    def test_195_a_substitution_past_the_depth_bound_is_refused(self):
        """analyse_command returned at depth > 6 with nothing recorded (proposal by SPUD-189/Joanna): a command seven
        substitutions or seven evals deep earned no finding, a Law 7 bypass needing no other trick.  The bound stays but
        fails closed."""
        deep = self.VCS
        for _ in range(7):
            deep = "echo $(%s)" % deep
        self.assertEqual(self.analysis(deep).findings and [k for k, _ in self.analysis(deep).findings if k == "unread"], ["unread"])
        self.hides_the_push(deep)
        evals = "git push"
        for _ in range(7):
            evals = "eval %s" % single_quoted(evals)
        self.hides_the_push(evals)
        # six levels deep the push is still read as the verb it is
        six = self.VCS
        for _ in range(6):
            six = "echo $(%s)" % six
        self.refused_for_members(six, "Law 7")

    # -- SPD-103: a `${ }` nested past the bound is refused at once, never read to the bottom -------------------------
    def test_103_a_brace_expansion_past_the_bound_is_refused_in_bounded_time(self):
        """analyse_command read a run of nested `${` in bounded but growing time (proposal by SPUD-102/Burbank: 40,000
        nested `${` took 7.7 s), three quarters of the pathological test's budget.  Past the bound the line is refused a
        member at once, the reader reading no further; Spud reads on."""
        cap = self.m.BRACE_DEPTH
        deep = "x"
        for _ in range(cap + 2):
            deep = "${%s}" % deep
        r = self.refused_for_members("echo " + deep, UNREAD)
        self.assertIn("${ }", r.reason)
        self.assertSilent("echo " + deep, agent_id=None)
        start = datetime.now()
        self.assertRefused("echo x > " + "${" * 40000, UNREAD)  # a run of openings, refused before it is all read
        self.assertLess((datetime.now() - start).total_seconds(), 2)

    def test_103_shallow_brace_nesting_reads_as_before(self):
        for line in ("echo ${x:-${y:-${z}}}", "echo ${x}${y}${z}", "echo '${${${'", "echo \"${x:-${y}}\""):
            self.silent_for_everyone(line)

    def test_195_a_shallow_line_reads_as_before(self):
        for line in ("echo $(echo hi)", "echo $(echo $(echo hi))", "x=$(git status)", "echo $(git log --oneline -5)"):
            self.silent_for_everyone(line)

    # -- SPD-177: a cd in the short body of an arithmetic-condition if/while/until is uncertain ------------------------
    def test_177_a_cd_after_an_arithmetic_condition_leaves_either_directory(self):
        """(proposal by SPUD-173/Steve) zsh runs the short body of an if, while or until whose condition ends in
        `(( ... ))` only when the condition holds, but the walk ended a condition only at `]]`, so a `cd` after the `))`
        was read as certain and a write after it checked in the cd's target alone, past the generated ledger file it
        would truly write.  Probed in zsh 5.9 -f and -f -o nobareglobqual: `if (( 0 )) cd o; echo new > l/t` wrote where
        the line began.  Not the fail-closed finding but the directory certainty: seen in either directory, the rendered
        ledger file the write would make in the original directory is refused, as it is after the `[[ ]]` spelling.  A
        wide member (home:**) is the witness: `o/ledger/...` is a file it may write, `ledger/...` the rendered file."""
        (self.home.path / "o").mkdir(exist_ok=True)
        # an if: either directory, so the rendered ledger file the write makes in the original one is refused, as after `[[ ]]`
        for cond in ("if (( 0 )) cd o", "if (( 0 )) { cd o }", "if [[ -z x ]] cd o"):
            self.assertRefused("%s; echo x > %s" % (cond, self.LEDGER), "generated", AGENT_C)
        # a while or until: the cd is in a loop body and may repeat, so the directory after it is unknown and the write refused
        for cond in ("n=0; while (( n++ < 0 )) cd o", "n=1; until (( n-- > 0 )) cd o"):
            r = self.assertRefused("%s; echo x > %s" % (cond, self.LEDGER), "the hook cannot follow", AGENT_C)
            self.assertIn("relative cd in a loop", r.reason)

    def test_177_an_unconditional_cd_still_moves_the_directory(self):
        """A cd certain to run still moves the shell: a write after it is checked in the new directory alone, so the same
        rendered path under the cd's target (`o/ledger/...`, no rendered file) is a file a wide member may write."""
        (self.home.path / "o").mkdir(exist_ok=True)
        self.assertSilent("cd o; echo x > %s" % self.LEDGER, AGENT_C)  # o/ledger/... is no rendered file
        self.assertSilent("(( 1 )); echo x > o/note.txt", AGENT_C)  # a standalone arithmetic command opens no condition

    # -- SPD-213: a shell in a substitution in a pipeline element reads the pipe (bash's reading) ---------------------
    def test_213_a_shell_in_a_substitution_in_a_pipeline_element_reads_the_pipe(self):
        """(proposal by SPUD-210/Marvin) bash expands a pipeline element's words in the subshell the pipe feeds it, so a
        `$(sh)` there reads the pipe's input; zsh reads the input of the list around it.  SPD-210 read only zsh's, so a
        shell in a substitution fed a pipe ran unread.  Probed 2026-09-23: `printf 'touch q1\\n' | echo $(sh) > /dev/null`
        made q1 in bash 3.2.57 and nothing in zsh 5.9."""
        for line in ("printf 'git push\\n' | echo $(sh) > /dev/null", "printf 'git push\\n' | echo `sh` > /dev/null"):
            with self.subTest(line=line):
                self.refused_for_members(line, "Law 7")  # bash's $(sh) runs the spelled pipe: the push is read
                self.assertSilent(line, agent_id=None)
        for line in ("cat x.sh | echo $(sh) > /dev/null", "curl -s http://x | echo $(sh) > /dev/null"):
            with self.subTest(line=line):
                r = self.refused_for_members(line, SCRIPT_WORDING)  # the pipe is another program's output: unspelled
                self.assertIn("standard input that the line does not spell", r.reason)
                self.assertSilent(line, agent_id=None)

    def test_213_a_pipe_with_no_shell_or_a_shell_with_no_pipe_reads_as_before(self):
        self.silent_for_everyone("printf 'git status\\n' | echo $(sh) > /dev/null")  # bash reads a read verb: silent
        self.silent_for_everyone("echo $(sh) > /dev/null")  # no pipe: the substitution's shell reads the terminal, nothing
        self.silent_for_everyone("printf 'git push\\n' | cat")  # no shell in a substitution reads the pipe

    # -- SPD-215: a snapshot alias or function is read with the standard input its call is given ----------------------
    def test_215_a_snapshot_alias_or_function_reads_the_calls_standard_input(self):
        """(proposal by SPUD-212/Bender) held_text.read_shell_name read an alias's or a snapshot function's body with no
        standard input, so with a profile alias to a shell or an interpreter, or a function whose body runs one, a
        member's `xs < x.sh` or `pyx < x.py` ran a file's program, past SPD-145 and SPD-150.  SPD-212 covered a function
        the line itself defines; the snapshot side is the same shape through analyse_shell_text.  Probed on the SPD-212
        tree with a scratch snapshot."""
        self.write_snapshot("snapshot-zsh-1700000000009-999999.sh", "alias xs='sh'\nalias pyx='python3'\nshfn () {\n\tsh\n}\n")
        for line in ("xs < x.sh", "shfn < x.sh"):
            with self.subTest(line=line):
                r = self.refused_for_members(line, SCRIPT_WORDING)
                self.assertIn("standard input that the line does not spell", r.reason)
                self.assertSilent(line, agent_id=None)
        self.refused_for_members("pyx < x.py", INLINE_WORDING)  # python reads a file's program on standard input
        self.assertSilent("pyx < x.py", agent_id=None)
        # a here-string or here-document the line spells is read as the alias's or the function's program
        for line in ("xs <<< 'git push'", "shfn <<< 'git push'", "xs <<'EOF'\ngit push\nEOF"):
            with self.subTest(line=line):
                self.refused_for_members(line, "Law 7")
                self.assertSilent(line, agent_id=None)

    def test_215_a_snapshot_alias_with_no_input_reads_as_before(self):
        self.write_snapshot("snapshot-zsh-1700000000009-999999.sh", "alias xs='sh'\nshfn () {\n\tsh\n}\n")
        for line in ("xs -c 'git status'", "shfn"):  # a -c string is read; a function with no call input reads nothing
            self.silent_for_everyone(line)

    # -- SPD-199: a placeholder the line did not lift is refused, never paired with another body ----------------------
    def test_199_a_substitution_placeholder_the_member_typed_is_refused(self):
        """(proposal by SPUD-190/Kyla) ShellWalk.consume pairs the hook's own substitution placeholder in a word with the
        next lifted `$( )` body, whoever wrote it, so a member typing the placeholder text steals a real substitution's
        body and its write is resolved in the wrong directory.  Refused a member where the theft could happen; Spud reads
        on."""
        subst = self.m.SUBST
        line = "echo %s && echo $(echo hi)" % subst  # the typed placeholder would steal the real (here benign) body
        r = self.refused_for_members(line, UNREAD)
        self.assertIn("marker the hook uses", r.reason)
        self.assertSilent(line, agent_id=None)
        # the ticket's evidence: a write whose directory the theft corrupts is refused unread before the write is read
        self.refused_for_members("echo %s && cd o && echo $(echo hi > y)" % subst, UNREAD)

    def test_199_a_private_use_marker_the_member_typed_is_refused(self):
        """A private-use character the reader uses for a lifted substitution or an operand it cannot spell -- typed or
        pasted on the line -- corrupts the reading, so the line is refused a member."""
        for marker in (self.m.PROCSUB_MARK, self.m.FIND_PATH, self.m.INPUT_OPERAND):
            with self.subTest(marker=repr(marker)):
                self.refused_for_members("echo a%sb" % marker, UNREAD)
                self.assertSilent("echo a%sb" % marker, agent_id=None)

    def test_199_the_placeholder_text_where_nothing_mispairs_reads_as_before(self):
        """A member may name the placeholder text where no lifted body mispairs with it -- grepping the tool's own
        source for it, say -- and an ordinary line with a real substitution is untouched."""
        for line in ("grep %s bin" % self.m.SUBST, "echo %s" % self.m.SUBST, "echo $(echo hi)", "x=$(git status)"):
            self.silent_for_everyone(line)

    # -- SPD-196: an escaped $( ) or backtick a -c string or eval unescapes and runs is refused ----------------------
    def test_196_an_escaped_substitution_a_reparse_runs_is_refused(self):
        """(proposal by SPUD-189/Joanna) shlex leaves the backslash of a double-quoted `\\$` or backtick in the word,
        where the shell removes it, so a `-c` string or eval text holding `\\$( )` or `` \\` `` ran the substitution the
        hook read as escaped and lifted no body.  Probed 2026-09-23 (shell_probe, zsh 5.9 -f, -f -o nobareglobqual, bash
        3.2.57): `sh -c "echo \\$(touch h1)"`, `eval "echo \\$(touch h2)"` and `eval "\\`touch h3\\`"` each made their
        file, while the single-quoted `sh -c 'echo \\$(touch x)'` was a syntax error and made none."""
        for line in ('sh -c "echo \\$(git push)"', 'bash -c "echo \\$(git push)"', 'eval "echo \\$(git push)"',
                     'eval "echo \\`git push\\`"', 'sh -c "true; echo \\$(git push)"'):
            with self.subTest(line=line):
                r = self.refused_for_members(line, UNREAD)
                self.assertIn("unescapes and runs", r.reason)
                self.assertSilent(line, agent_id=None)

    def test_196_an_unescaped_or_quoted_substitution_reads_as_before(self):
        """An unescaped `$( )` runs at the outer level and is read there (Law 7); `\\$NAME` is no substitution; a `\\$(`
        the shell keeps single-quoted is a literal the shell does not run."""
        self.refused_for_members('sh -c "echo $(git push)"', "Law 7")  # runs at the outer double quotes
        for line in ('sh -c "echo \\$HOME"', 'eval "echo \\$HOME"', 'eval "echo hi"', "sh -c 'echo hi'"):
            self.silent_for_everyone(line)

    # -- SPD-198: a process substitution in a for/foreach list or an array value is refused --------------------------
    def test_198_a_process_substitution_in_a_for_or_array_list_is_refused(self):
        """(proposal by SPUD-190/Kyla) ShellWalk joins the tokens of a for/foreach `( ... )` list and of `name=( ... )`
        into one word without walking them, so a `<( )`, `>( )` or `=( )` there was never analysed and a member ran a
        command in it unread.  Probed 2026-09-23 (shell_probe, zsh 5.9 -f, -f -o nobareglobqual): each made its file."""
        for line in ("for f ( <(git push) ) true", "foreach f (<(git push)) true; end", "x=(<(git push))",
                     "x=(a >(git push) b)", "for f (=(git push)) true", "x=(=(git push))"):
            with self.subTest(line=line):
                r = self.refused_for_members(line, UNREAD)
                self.assertIn("process substitution", r.reason)
                self.assertSilent(line, agent_id=None)
        self.refused_for_members("for f in <(git push); do true; done", "Law 7")  # the `in <( )` form is read as commands

    def test_198_a_plain_for_or_array_list_reads_as_before(self):
        for line in ("for f ( a b ) true", "x=(a b c)", "foreach f (a b) true; end", "x=()", "x=(a=b c=d)"):
            self.silent_for_everyone(line)

    # -- SPD-211: bash expands an unquoted here-doc's substitutions with the command's prefix assignments -------------
    def test_211_a_here_doc_substitution_reads_the_commands_prefix(self):
        """(proposal by SPUD-208/Oliver) bash 3.2 expands an unquoted here-document's substitutions with the command's own
        prefix assignments, where zsh reads the value the line holds before the command (SPD-192), so `x=push cat <<EOF`
        with body `$(git $x)` runs git push in bash while the hook read git status.  Probed 2026-09-23 (shell_probe, zsh
        5.9 -f, -f -o nobareglobqual, bash 3.2.57): the body's `$x` was the prefix value in bash, the outer one in zsh,
        the redirection target inside a substitution excepted."""
        for line in ("x=status; x=push cat <<EOF > /dev/null\n$(git $x)\nEOF",
                     "x=push cat <<EOF > /dev/null\n$(git $x)\nEOF",
                     "x=push cat <<EOF > /dev/null\n`git $x`\nEOF"):
            with self.subTest(line=line):
                self.refused_for_members(line, "Law 7")  # bash's reading is git push
                self.assertSilent(line, agent_id=None)
        # a prefix value the hook cannot resolve leaves the substitution's verb unresolved: refused a member either way
        r = self.refused_for_members("x=$(echo push) cat <<EOF > /dev/null\n$(git $x)\nEOF", "spell the")
        self.assertSilent("x=$(echo push) cat <<EOF > /dev/null\n$(git $x)\nEOF", agent_id=None)

    def test_211_a_quoted_delimiter_or_no_prefix_reads_as_before(self):
        for line in ("x=status cat <<'EOF' > /dev/null\n$(git $x)\nEOF",  # quoted delimiter: the body is not expanded
                     "cat <<EOF > /dev/null\n$(git status)\nEOF",  # no prefix, a read verb
                     "x=push cat <<EOF > /dev/null\n$(git status)\nEOF"):  # the prefix does not reach a spelled verb
            self.silent_for_everyone(line)

    # -- SPD-197: the other places a shell evaluates a value as code are refused by name ------------------------------
    def test_197_a_value_evaluated_as_code_is_refused(self):
        """(proposal by SPUD-189/Joanna) five more ways a value the line assigns runs as code, each read by nothing:
        zsh `${(P)n}` evaluates the subscript of the name n holds; `${~x}` under GLOB_SUBST globs a value, running a glob
        qualifier's code; `${(%%)x}`, `print -P` run the substitutions in a value under promptsubst; PS1/PROMPT/PS4 are
        expanded (PS4 per traced command under set -x); bash arithmetic evaluates a variable whose value holds a
        subscript.  Probed 2026-09-23 (shell_probe, zsh 5.9 -f, -f -o nobareglobqual, bash 3.2.57): each ran the value's
        substitution.  Refused a member by name, none modelled; Spud reads on."""
        push = "$(git push)"
        for line in ("n='x[%s]'; echo ${(P)n}" % push,
                     "x='f*(e:\"git push\":)'; echo ${~x}",
                     "setopt promptsubst; x='%s'; echo ${(%%%%)x}" % push,
                     "setopt promptsubst; x='%s'; print -P $x" % push,
                     "PS4='%s'; set -x; echo hi" % push,
                     "x='a[%s]'; [[ $x -eq 0 ]]" % push,
                     "x='a[%s]'; echo $(( x + 1 ))" % push):
            with self.subTest(line=line):
                r = self.refused_for_members(line, UNREAD)
                self.assertIn("evaluates as code", r.reason)
                self.assertSilent(line, agent_id=None)

    def test_197_a_plain_value_or_expansion_reads_as_before(self):
        """A value with no code, and a plain expansion, are not refused."""
        for line in ("n=HOME; echo ${(P)n}", "x='*.txt'; echo ${~x}", "PS1='\\u@\\h'; echo hi",
                     "x=2; echo $(( x + 1 ))", "x='a b'; echo ${(%%%%)x}", "echo ${x:-default}"):
            self.silent_for_everyone(line)

    # -- SPD-194: a $( ) whose end the reader cannot settle is refused, not guessed --------------------------------
    def test_194_a_substitution_whose_end_is_unsettleable_is_refused(self):
        """(proposal by SPUD-188/Linus) prepare.split_substitutions ends a `$( )` at the first parenthesis that balances,
        counting a quoted `)`, a `case` pattern's `)` and a `)` in a here-document body alike, so text past the early end
        was read as the outer line.  A quoted `)` leaves an unbalanced quote (SPD-191, UnreadableLineTest); a `case`
        pattern or a here-document body inside the `$( )` is refused unread.  Probed 2026-09-23 (shell_probe)."""
        # the `)` the naive extent ends at is a case pattern's or in a here-document body: refused unread
        for line in ("echo $(case a in a) true;; esac)", "echo $(cat <<EOF\na)b\nEOF\ntrue)"):
            with self.subTest(line=line):
                r = self.refused_for_members(line, UNREAD)
                self.assertIn("closing `)`", r.reason)
                self.assertSilent(line, agent_id=None)
        # a member is refused whether the mis-parse leaves the hidden command read (Law 7) or unread; Spud reads on
        for line in ("echo $(case a in a) git push;; esac)", "x=$(case a in a) git push;; esac)"):
            with self.subTest(line=line):
                self.refused_for_members(line, "")  # refused for any reason (Law 7 or unread)
                self.assertSilent(line, agent_id=None)
        # a quoted `)` inside is a line the hook cannot tokenize (SPD-191), refused to every caller
        self.assertRefused("echo $(echo ')' && git push)", UNREADABLE, agent_id=None)

    def test_194_a_plain_substitution_reads_as_before(self):
        for line in ("echo $(echo hi)", "x=$(git status)", "echo $(grep -rn case .)", "echo $(echo '(a)')",
                     "echo $(echo 'esac case')"):
            self.silent_for_everyone(line)

    # -- SPD-205: a finding on a variable the member's words fill inside a function's text is kept -------------------
    def test_205_a_finding_on_a_member_filled_variable_is_kept(self):
        """(proposal by SPUD-203/Locutus) SPD-203 kept a tolerated finding that spells a member word the hook cannot
        read, but a variable the member's words fill on the way -- a for-loop over `$@`, or one the line assigned before
        the function's text -- was still pruned as the body's own, so a profile function of these shapes carried a git
        write past Law 7.  A dropped finding is never a pass: the finding is kept and refuses the member on doubt (a read
        verb too, where the hook cannot resolve the loop variable).  None is a git write this Mac's profile defines.
        SPD-269: a loop over words the call spells, none that may be an option, is read once per word, each the word it
        is (expansions.resolve_expansion), so `loopgit push` is refused as `git push` is and `loopgit status` is the read
        it runs.  SPD-274: a word that may be an option (`-m`) no longer unsettles the loop where git's words are read, so
        `loopgit commit -m x` is read as `git commit`, `git -m` and `git x`, and refused under Law 7 as spelled."""
        self.write_snapshot("snapshot-zsh-1700000000009-999999.sh",
            "loopgit () {\n\tfor a in \"$@\"; do git $a; done\n}\nglobalgit () {\n\tgit $GITVERB\n}\n")
        # SPD-274: the `-m` loop is read per value, so its refusal is Law 7's, not the unresolved variable's
        for line, needle in (("GITVERB=$(echo push); globalgit", "cannot resolve"), ("loopgit commit -m x", "Law 7"),
                             ("loopgit $(echo status)", "cannot resolve"), ("loopgit push", "Law 7"), ("loopgit status push", "Law 7")):
            with self.subTest(line=line):
                self.refused_for_members(line, needle)  # the member-filled variable is refused on doubt, or read as spelled
                self.assertSilent(line, agent_id=None)
        self.silent_for_everyone("loopgit status")

    def test_205_a_body_s_own_variable_stays_pruned(self):
        """A variable the body itself fills, not from the member's words, is still the body's own and stays dropped, so a
        member is not refused a profile function it did not fill: a loop over a literal list, a call with no words.
        SPD-269: that literal list is read once per word, so the verbs the body runs are read as spelled -- the reads it
        runs pass, and a write the body's own loop hides (`pushloop`, which the prune passed unread) is refused (Law 7)."""
        self.write_snapshot("snapshot-zsh-1700000000009-999999.sh",
            "ownloop () {\n\tfor a in status log; do git $a; done\n}\nglobalgit () {\n\tgit $GITVERB\n}\n"
            "unknownloop () {\n\tfor a in $(git config x); do git $a; done\n}\npushloop () {\n\tfor a in status push; do git $a; done\n}\n")
        for line in ("ownloop", "globalgit", "unknownloop"):  # no member words fill the loop, and no line assigns GITVERB
            self.silent_for_everyone(line)
        self.refused_for_members("pushloop")
        self.assertSilent("pushloop", agent_id=None)

    # -- SPD-193: a line of many conditional relative cds is read in bounded time, its writes refused ---------------
    def test_193_many_conditional_cds_bound_the_directory_set(self):
        """(proposal by SPUD-188/Linus) analyse_command's time doubled with each `cd dirK && ...` step, since the
        directories the shell may be in grow as 2^n -- the original, dirK/, and every relative combination of the cds
        that may not have run -- so a short multi-line command took seconds to minutes in PreToolUse.  The directory set
        is capped; past it the shell's directory is unknown and a relative write target after it is refused, which a
        member spells around with an absolute path.  Measured 2026-09-23: N=26 steps took 91 s before, 0.01 s after."""
        line = "(\n" + "".join("cd dir%d\necho x > out%d.txt\n" % (k, k) for k in range(16)) + ")"
        # a wide member witnesses the bound: the early writes land in directories it may write, the ones past the cap in
        # a directory the hook can no longer follow, and are refused
        r = self.assertRefused(line, "cannot follow", AGENT_C)
        self.assertIn("use an absolute path", r.reason)

    def test_193_a_few_cds_still_resolve(self):
        """A handful of uncertain cds stays under the cap, so a write is still checked in every directory it may open,
        the one the line began in among them (the cd may fail, its target not existing)."""
        self.assertRefused("cd o\necho x > %s" % self.LEDGER, "generated", AGENT_C)  # seen where the line began

    @wall_clock
    def test_193_bounded_on_pathological_conditional_cds(self):
        m = load_spud_module()
        line = "(\n" + "".join("cd d%d && echo s > o%d.txt && grep -rn x src | head -5\n" % (k, k) for k in range(40)) + ")"
        started = time.monotonic()
        m.analyse_command(line, m.ShellAnalysis(cwd=str(self.home.path)))
        self.assertLess(time.monotonic() - started, 2)


# The harness's own shadows as Claude Code writes them into every snapshot it sources, copied from this Mac's snapshot
# of 2026-09-24 (snapshot-zsh-1790235321145-pm4nbj.sh) with the Mac's own claude path spelled as a scratch one.  find, grep
# and rg keep `_cc_bin` (grep also `_cc_a`) local, settle it with `[[ -x $_cc_bin ]] || _cc_bin=...` -- an assignment that
# may not run, so `"$_cc_bin"` is read in doubt -- and dispatch through it; pkill declares its locals inside a condition.
# SHELL_SNAPSHOT's one-line grep has no `||`, which is why no test met a second shadow's doubt (SPD-246).
HARNESS_SHADOWS = """\
# Functions
setv () {
\tV="$1"
}
globalv () {
\ttypeset -g V="$1"
}
localv () {
\tlocal V="$1"
}
mixv () {
\tV="$1"
\tlocal V=x
}
condlocal () {
\t[[ -n $1 ]] && local V
\tV="$1"
}
sublocal () {
\t(local V)
\tV="$1"
}
iflocal () {
\tif [[ -n $1 ]]; then local V; fi
\tV="$1"
}
vgit () {
\tgit $V
}
loopglobal () {
\tfor W in "$@"; do :; done
}
wgit () {
\tgit $W
}
loopmember () {
\tlocal a
\tfor a in "$@"; do :; done
}
runa () {
\tlocal a=ls
\t[[ -n $X ]] && a=cat
\t$a f
}
globalgit () {
\tgit $GITVERB
}
# Shadow find/grep with embedded bfs/ugrep
unalias find 2>/dev/null || true
unalias grep 2>/dev/null || true
function find {
  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"
  [[ -x $_cc_bin ]] || _cc_bin=/Users/Someone/.local/bin/claude
  if [[ ! -x $_cc_bin ]]; then command find ${1+"$@"}; return; fi
  if [[ -n ${ZSH_VERSION:-} ]]; then
    ARGV0=bfs "$_cc_bin" -S dfs -regextype findutils-default ${1+"$@"}
  elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ "$OSTYPE" == "win32" ]]; then
    ARGV0=bfs "$_cc_bin" -S dfs -regextype findutils-default ${1+"$@"}
  else
    (exec -a bfs "$_cc_bin" -S dfs -regextype findutils-default ${1+"$@"})
  fi
}
function grep {
  local _cc_a
  for _cc_a in ${1+"$@"}; do
    case "$_cc_a" in -*-filter*|-*-pager*|-*-view*|-*-format-open*|-*-config*|---*|-@*|-*-save-config*|-[Zz]*|-[!-]*[Zz]*|--null|--null-data) command grep ${1+"$@"}; return ;; esac
  done
  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"
  [[ -x $_cc_bin ]] || _cc_bin=/Users/Someone/.local/bin/claude
  if [[ ! -x $_cc_bin ]]; then command grep ${1+"$@"}; return; fi
  if [[ -n ${ZSH_VERSION:-} ]]; then
    ARGV0=ugrep "$_cc_bin" -G --ignore-files --hidden -I --exclude-dir=.git --exclude-dir=.svn --exclude-dir=.hg --exclude-dir=.bzr --exclude-dir=.jj --exclude-dir=.sl ${1+"$@"}
  elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ "$OSTYPE" == "win32" ]]; then
    ARGV0=ugrep "$_cc_bin" -G --ignore-files --hidden -I --exclude-dir=.git --exclude-dir=.svn --exclude-dir=.hg --exclude-dir=.bzr --exclude-dir=.jj --exclude-dir=.sl ${1+"$@"}
  else
    (exec -a ugrep "$_cc_bin" -G --ignore-files --hidden -I --exclude-dir=.git --exclude-dir=.svn --exclude-dir=.hg --exclude-dir=.bzr --exclude-dir=.jj --exclude-dir=.sl ${1+"$@"})
  fi
}
# Shadow pkill to refuse patterns matching the CLI process
unalias pkill 2>/dev/null || true
function pkill {
  if [ -n "${CLAUDE_PID:-}" ] && [ -r "/proc/${CLAUDE_PID}/comm" ]; then
    local _cc_skip="" _cc_a
    local -a _cc_probe=()
    for _cc_a in ${1+"$@"}; do
      if [ -n "$_cc_skip" ]; then _cc_skip=""; continue; fi
      case "$_cc_a" in
        --signal) _cc_skip=1 ;;
        --signal=*|-e|--echo) ;;
        -[0-9]*) ;;
        -[PUGOF]?*) _cc_probe+=("$_cc_a") ;;
        -[ABCDEFGHIJKLMNOPQRSTUVWXYZ][ABCDEFGHIJKLMNOPQRSTUVWXYZ0-9]*) ;;
        *) _cc_probe+=("$_cc_a") ;;
      esac
    done
    if command pgrep ${_cc_probe[@]+"${_cc_probe[@]}"} 2>/dev/null | command grep -qx "${CLAUDE_PID}"; then
      printf 'pkill: refusing to run -- this pattern matches the Claude CLI process (PID %s). Narrow the pattern, or target your own children with `pkill -P $$ ...`.\\n' "${CLAUDE_PID}" >&2
      return 1
    fi
  fi
  command pkill ${1+"$@"}
}
if ! (unalias rg 2>/dev/null; command -v rg) >/dev/null 2>&1; then
  function rg {
  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"
  [[ -x $_cc_bin ]] || _cc_bin=/Users/Someone/.local/bin/claude
  if [[ ! -x $_cc_bin ]]; then command rg ${1+"$@"}; return; fi
  if [[ -n ${ZSH_VERSION:-} ]]; then
    ARGV0=rg "$_cc_bin" ${1+"$@"}
  elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ "$OSTYPE" == "win32" ]]; then
    ARGV0=rg "$_cc_bin" ${1+"$@"}
  else
    (exec -a rg "$_cc_bin" ${1+"$@"})
  fi
}
fi
"""


class HarnessShadowTest(ShellSnapshotCase):
    """SPD-246 (proposal by SPUD-134/Billie): analyse_shell_text keeps a body's finding that names a variable the line
    assigned before the text (SPD-205), and took the line's variables to be every name assigned so far -- a function
    body's own locals included.  The first shadow on a line put its `_cc_bin` there, so the second one's `"$_cc_bin"`,
    read in doubt after its `[[ -x $_cc_bin ]] || _cc_bin=...`, was kept as the member's: every member was refused
    `find . -name x | grep y`, `grep a f | grep -v b` and `grep -c a f; grep -c b f` with "the variable $_cc_bin may not
    hold the value this line assigned it".

    The rule now: the line's variables are the names whose assignment reaches the line's shell -- every one the line's own
    text makes, as before, and one a function body the line calls makes to a name it did not declare local, which is
    still set when a later text runs (`setv () { V="$1" }; vgit () { git $V }`: `setv $(echo push); vgit` pushes), and a
    for loop's variable is assigned as `NAME=value` is.  A body's local is gone when it returns, so it is never the
    line's, and the name is again what it was before the call.  A name counts as local only where the declaration surely
    ran in the body's own shell: `local`, or `typeset`/`declare` without -g, at the body's own level -- not after `&&` or
    `||`, in a pipeline, or inside any compound command (a group included, read the same way) -- and so in both of the
    hook's readings.  The variables a call's words fill (SPD-205's member_vars) follow the same rule: a body's local filled
    from its call's words is not the member's in a later call's text.

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud, and the home is the cwd."""

    def setUp(self):
        super().setUp()
        path = self.write_snapshot("snapshot-zsh-1700000000024-246246.sh", HARNESS_SHADOWS)
        newest = path.stat().st_mtime + 60  # newer than SHELL_SNAPSHOT, whose one-line grep it replaces
        os.utime(path, (newest, newest))
        self.m = load_spud_module()

    def analysis(self, command):
        env = dict(os.environ)
        env["SPUD_USER_CLAUDE_DIR"] = self.home.env["SPUD_USER_CLAUDE_DIR"]
        with mock.patch.dict(os.environ, env, clear=True):
            return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def test_the_shadows_are_the_harness_s(self):
        for line in ("find . -name x", "grep a f", "pkill -f x", "rg x"):
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).shell_expanded, [(line.split()[0], "a shell function")])
                self.silent_for_everyone(line)

    def test_the_tickets_evidence(self):
        for line in ("find . -name x | grep y", "grep a f | grep -v b", "grep -c a f; grep -c b f"):
            with self.subTest(line=line):
                self.assertNotIn(("var-doubt", "$_cc_bin"), self.analysis(line).findings)
                self.silent_for_everyone(line)

    def test_any_two_shadows_on_one_line(self):
        for line in ("grep -rn x . | grep -v y | grep -c z", "find . -name x; find . -name y", "rg x && rg y", "rg x | grep y",
                     "grep a f || find . -name x", "pkill -f x; pkill -f y", "pkill -f x; grep a f", "grep a f; pkill -f x",
                     "(grep a f); grep b f", "echo $(grep a f) | grep b", "for f in a b; do grep x $f; done; grep y z",
                     "X=$(echo y); grep a f | grep $X", "grep a f; X=$(echo y); grep $X g"):
            with self.subTest(line=line):
                self.silent_for_everyone(line)

    # -- SPD-205's findings for the line's own variables stand ------------------------------------------------------------
    def test_a_variable_the_line_assigns_keeps_its_finding(self):
        """A variable the line's own text assigns is the member's wherever a later text reads it, a shadow's run between
        or not -- the very `_cc_bin` a shadow reads included, since the line assigned that name itself -- and a for loop's
        variable is assigned as much as `NAME=value` is (it was not counted, and the loop's list passed as the verb)."""
        for line in ("grep a f; GITVERB=$(echo push); globalgit", "GITVERB=$(echo push); grep a f | globalgit",
                     "find . -name x | grep y; GITVERB=$(echo push); globalgit", "GITVERB=$(echo push); grep a f; grep b f; globalgit",
                     "_cc_bin=$(echo x); grep a f", "grep a f; _cc_bin=$(echo x); grep b f",
                     "for GITVERB in $(echo push); do :; done; globalgit", "grep a f; for GITVERB in $(echo push); do grep b f; done; globalgit"):
            with self.subTest(line=line):
                self.refused_for_members(line, "cannot resolve")
                self.assertSilent(line, agent_id=None)
        self.silent_for_everyone("grep a f; globalgit")  # no line assigns GITVERB: the environment's, as SPD-205 reads it

    # -- a function that sets a global a later text reads -------------------------------------------------------------
    def test_a_global_a_function_sets_is_the_line_s(self):
        """A body's assignment to a name it did not declare local is still set when the call returns, so a later text that
        reads it reads what the call put there -- here the member's own word, which the hook cannot read."""
        for line in ("setv $(echo push); vgit", "globalv $(echo push); vgit", "setv $(echo push); grep a f; vgit",
                     "grep a f | setv $(echo push); vgit", "loopglobal $(echo push); wgit", "loopglobal $(echo push); wgit x"):
            with self.subTest(line=line):
                self.refused_for_members(line, "cannot resolve")
                self.assertSilent(line, agent_id=None)
        self.refused_for_members("setv push; vgit", "Law 7")
        self.refused_for_members("globalv push; vgit", "Law 7")

    def test_a_local_that_may_not_be_one_counts_as_global(self):
        """A declaration the body may not run, or runs in a subshell -- after `&&`, in `( )`, in an `if` -- does not make
        the assignment after it local: each left a later `x=1` global in zsh and bash (assignment_words.local_names's probe), and
        the hook reads it as the global it may be."""
        for line in ("condlocal $(echo push); vgit", "sublocal $(echo push); vgit", "iflocal $(echo push); vgit"):
            with self.subTest(line=line):
                self.refused_for_members(line, "cannot resolve")
                self.assertSilent(line, agent_id=None)

    def test_a_function_s_local_is_gone_when_it_returns(self):
        """`local V="$1"` sets nothing after the call: vgit reads the environment's V, as `vgit` alone does, and a V the
        line assigned before the call is what it was -- its value and its certainty (the local's value was read as the
        line's, and the call left the line's V in doubt).  A body that assigned the line's V before declaring its own
        changed it."""
        self.silent_for_everyone("vgit")
        self.silent_for_everyone("localv $(echo push); vgit")
        self.silent_for_everyone("localv push; vgit")
        self.silent_for_everyone("V=status; localv push; git $V")
        self.silent_for_everyone("V=status; localv $(echo push); vgit")
        self.refused_for_members("V=status; mixv push; git $V", "Law 7")
        self.refused_for_members("true && V=status; localv x; git $V", "may not hold the value")  # in doubt before the call

    def test_a_local_the_call_s_words_filled_stays_in_its_call(self):
        """SPD-205's member_vars: loopmember's local `a`, which its call's words fill, is not the member's in runa's text,
        whose own local `a` is read in doubt."""
        self.silent_for_everyone("runa z")
        self.silent_for_everyone("loopmember y; runa z")


# Profile functions that read a name the line may assign through a builtin or an arithmetic evaluation, and ones that
# assign a name from their call's words that way (SPD-225, SPD-254).
ASSIGNING_FUNCTIONS = HARNESS_SHADOWS.replace("# Shadow find/grep", """\
printgit () {
\tprintf -v V %s "$1"
\tgit $V
}
printlocal () {
\tlocal V
\tprintf -v V %s "$1"
\tgit $V
}
countgit () {
\t(( N = $# ))
\tgit log -n $N
}
# Shadow find/grep""")


class AssignedNameThroughFunctionTest(ShellSnapshotCase):
    """SPD-254 (proposal by SPUD-246/Oliver) and SPD-225: SPD-205 keeps a function body's finding that names a variable the
    line assigned before the body's text, and SPD-246 made the line's variables the names whose assignment reaches the
    line's shell.  A name an assigning builtin sets (`read GITVERB < f`, `printf -v GITVERB %s push`) or an arithmetic
    evaluation sets (`((GITVERB=1))`, `let GITVERB++`) was not among them, so `read GITVERB < f; globalgit` (globalgit ()
    { git $GITVERB }) had the body's `$GITVERB` finding dropped as the body's own and passed a member, where
    `GITVERB=$(echo push); globalgit` was refused; and a name a builtin in the body assigns from the call's words was not
    counted as the member's either (`printgit push`, whose body is `printf -v V %s "$1"; git $V`).  Each of these is now
    the line's assignment, with a value the hook does not know unless it is an arithmetic literal, and a name a builtin
    in a body assigns while the call has words is the member's (SPD-205's member_vars).  AGENT_A and AGENT_B plan
    home:tests/** and home:bin/spud, and the home is the cwd."""

    def setUp(self):
        super().setUp()
        path = self.write_snapshot("snapshot-zsh-1700000000025-254254.sh", ASSIGNING_FUNCTIONS)
        newest = path.stat().st_mtime + 60  # newer than SHELL_SNAPSHOT, whose one-line grep it replaces
        os.utime(path, (newest, newest))

    def test_the_tickets_evidence(self):
        for line in ("read GITVERB < f; globalgit", "printf -v GITVERB %s push; globalgit"):
            with self.subTest(line=line):
                self.refused_for_members(line, "cannot resolve")
                self.assertSilent(line, agent_id=None)

    def test_every_builtin_that_assigns_the_name(self):
        for line in ("read -r GITVERB < f", "IFS= read -r GITVERB < f", "read -p P GITVERB < f", "read -A GITVERB < f",
                     "read -a GITVERB < f", "read 'GITVERB?verb: ' < f", "printf -vGITVERB %s push", "print -v GITVERB push",
                     "getopts ab GITVERB", "mapfile GITVERB < f", "readarray -t GITVERB < f", "unset GITVERB", "wait -p GITVERB",
                     "set -A GITVERB push", "zstyle -s ctx st GITVERB", "vared GITVERB", "sysread GITVERB < f", "getln GITVERB",
                     "zparseopts -a GITVERB h", "V=GITVERB; read -r $V < f", ": ${GITVERB:=push}", ": ${GITVERB=push}",
                     "true && read -r GITVERB < f", "cat f | read -r GITVERB", "{ read -r GITVERB; } < f"):
            with self.subTest(line=line):
                self.refused_for_members(line + "; globalgit", "")
                self.assertSilent(line + "; globalgit", agent_id=None)

    def test_every_arithmetic_form_that_assigns_the_name(self):
        """An arithmetic value is a number, which git takes for no verb of its own, and one the hook cannot compute is
        read as the substitution's value was: the finding is the member's either way (SPD-205)."""
        for line in ("((GITVERB=1))", "(( GITVERB++ ))", "echo $((GITVERB=1))", "let GITVERB=1", "let GITVERB++",
                     "for ((GITVERB=0; GITVERB<1; GITVERB++)); do :; done", "typeset -i GITVERB; GITVERB=1",
                     "[[ GITVERB=1 -eq 1 ]]"):
            with self.subTest(line=line):
                self.refused_for_members(line + "; globalgit", "")
                self.assertSilent(line + "; globalgit", agent_id=None)

    def test_a_builtin_in_a_body_assigns_the_member_s_words(self):
        self.refused_for_members("printgit push", "cannot resolve")
        self.refused_for_members("printlocal push", "cannot resolve")  # a local, but its call's words fill it
        self.assertSilent("printgit push", agent_id=None)

    def test_what_assigns_another_name_or_no_variable_stays_silent(self):
        for line in ("globalgit", "read -r X < f; globalgit", "printf '%s' GITVERB; globalgit", "unset -f GITVERB; globalgit",
                     "getopts GITVERB X; globalgit", "((X=1)); globalgit", "(( GITVERB > 1 )); globalgit", "zstyle ':x' GITVERB y; globalgit",
                     "read -r x < f; grep a f | grep b", "grep a f; read -r x < f; grep b f", "(( n = 1 )); grep a f | grep -v b",
                     "countgit", "countgit a b"):
            with self.subTest(line=line):
                self.silent_for_everyone(line)


# Profile functions whose variables the call's words or the line's own variables fill through a substitution, a copy, a
# loop or arithmetic (SPD-258, SPD-253, SPD-248), and ones that fill theirs from their own literals alone.
MEMBER_FILLED_FUNCTIONS = HARNESS_SHADOWS.replace("# Shadow find/grep", """\
subgit () {
\tV=$(echo "$1")
\tgit $V
}
sublocal () {
\tlocal V=$(echo "$1")
\tgit $V
}
subquoted () {
\tV="$(echo "$1" | tr a-z a-z)"
\tgit "$V"
}
subglued () {
\tV=$(echo "x$1" | cut -c2-)
\tgit $V
}
subnested () {
\tV=$(echo $(echo "$1"))
\tgit $V
}
chainlit () {
\tV="$1"
\tW=$(echo $V)
\tgit $W
}
chaincopy () {
\tV=$(echo "$1")
\tW=$V
\tgit $W
}
appendgit () {
\tV=
\tV+=$(echo "$1")
\tgit $V
}
arithgit () {
\t(( N = $1 ))
\tgit $N
}
linesub () {
\tV=$(echo $GITVERB)
\tgit $V
}
linecopy () {
\tlocal V="${GITVERB:-status}"
\t[[ -n $X ]] || V=status
\tgit $V
}
linedefault () {
\t: ${V:=$GITVERB}
\tgit $V
}
lineprint () {
\tprintf -v V %s "$GITVERB"
\tgit $V
}
loopcmd () {
\tfor f in "$@"; do $f push; done
}
zloopcmd () {
\tfor f ("$@") $f push
}
loopsub () {
\tfor f in $(echo "$@"); do git $f; done
}
loopvar () {
\tV=$(echo "$1")
\tfor f in $V; do git $f; done
}
subwrite () {
\tV=$(echo "$1")
\ttouch $V
}
linewrite () {
\ttouch $OUTFILE
}
readstdin () {
\tread V
\tgit $V
}
ownsub () {
\tV=$(echo status)
\tgit $V
}
ownloop () {
\tfor f in git; do $f status; done
}
ownarith () {
\t(( N = RANDOM % 2 ))
\tgit $N
}
# Shadow find/grep""")


class MemberFilledValueTest(ShellSnapshotCase):
    """SPD-258 (proposal by SPUD-225/Bertha), with SPD-253 (SPUD-246/Oliver) and SPD-248 (SPUD-134/Billie) folded in: the
    prune in analyse_shell_text keeps a function body's finding that names a variable the member filled -- SPD-205's for
    list over `$@`, a value holding a positional, SPD-254's builtin -- and dropped every other as the body's own.  Three
    more ways the member's words or the line's own variables reach a body variable were read as the body's:

    - SPD-258: a substitution of a positional, `subgit () { V=$(echo "$1"); git $V }`.  shell/positional sets the call's
      words in the body before it is read, so the value is a lifted substitution holding no positional any more, and
      `subgit push` passed a member while the shell ran git push;
    - SPD-253: a value naming a line variable.  The harness's own shadows copy `${CLAUDE_CODE_EXECPATH:-}` into their
      `_cc_bin` and run it when it is executable, but the hook kept only `_cc_bin`'s last value, the installed claude, and
      pruned the doubt on `"$_cc_bin"`: `CLAUDE_CODE_EXECPATH=/tmp/x.sh grep a f` ran the member's own script unread
      (SPD-145's rule for a file run by its path);
    - SPD-248 (1): a loop variable over `"$@"` used as the command word, `loopcmd () { for f in "$@"; do $f push; done }`,
      whose "var" finding the prune excluded by kind.

    The rule now: a body variable is the member's whenever its value can hold what the call's words or the line's own
    variables supply -- a positional, a substitution shell/positional set the words in, arithmetic that names them, a line
    variable, another such variable, a variable holding one of the call's own words, and since the call's standard input
    is the member's as much as its words are, a builtin that reads it -- and every finding kind that names such a
    variable is kept, a write through one among them.  A variable the body fills from its own literals stays the body's,
    so the harness's shadows still pass every plain grep, find, rg and pkill.

    Probed 2026-09-24 through tests/probes/shell_probe.py in zsh 5.9 (arm64-apple-darwin26.0) -f -o nobareglobqual and -f,
    and GNU bash 3.2.57, all three alike: `subcmd () { V=$(echo "$1"); printf '<%s>' $V; }; subcmd push` printed <push>, and
    so did `V="$1"; W=$(echo $V)`; `for f in "$@"; do $f push; done` with `echo` ran `echo push`, and zsh's `for f ("$@") $f
    zpush` ran it too; a function of the harness's shape run as `CLAUDE_CODE_EXECPATH=/bin/echo shadow a f` ran /bin/echo
    with `-G a f` in place of its fallback; `echo push | readin` (`read V`) printed <push>.

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud, and the home is the cwd."""

    def setUp(self):
        super().setUp()
        path = self.write_snapshot("snapshot-zsh-1700000000026-258258.sh", MEMBER_FILLED_FUNCTIONS)
        newest = path.stat().st_mtime + 60  # newer than SHELL_SNAPSHOT, whose one-line grep it replaces
        os.utime(path, (newest, newest))
        self.m = load_spud_module()

    def analysis(self, command):
        env = dict(os.environ)
        env["SPUD_USER_CLAUDE_DIR"] = self.home.env["SPUD_USER_CLAUDE_DIR"]
        with mock.patch.dict(os.environ, env, clear=True):
            return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def refused_not_spud(self, line, needle):
        with self.subTest(line=line):
            self.refused_for_members(line, needle)
            self.assertSilent(line, agent_id=None)

    # -- SPD-258: a substitution of the call's words ------------------------------------------------------------------
    def test_258_the_tickets_evidence(self):
        self.assertIn(("var-word", "$V"), self.analysis("subgit push").findings)
        self.refused_not_spud("subgit push", "cannot resolve")
        self.refused_not_spud("subgit status", "cannot resolve")  # refused on doubt, a read verb too, as SPD-205's are

    def test_258_every_way_a_substitution_carries_the_words(self):
        for line in ("sublocal push", "subquoted push", "subglued push", "subnested push", "chainlit push", "chaincopy push",
                     "appendgit push", "arithgit push", "loopsub push", "loopvar push", "subgit $(echo push)"):
            self.refused_not_spud(line, "")

    def test_258_a_write_through_a_filled_variable_is_the_member_s(self):
        """Its target is one the hook cannot resolve, which refuses Spud too, as on a plain line (SPD-091)."""
        self.refused_for_members("subwrite /tmp/spd-258-x", VARIABLE_WORDING)
        self.assertRefused("subwrite /tmp/spd-258-x", VARIABLE_WORDING, agent_id=None)

    def test_258_what_the_body_fills_from_its_own_literals_stays_the_body_s(self):
        for line in ("ownsub", "ownsub x", "ownsub status", "ownloop", "ownloop x", "ownarith", "ownarith 1", "subgit",
                     "subwrite", "loopcmd"):
            with self.subTest(line=line):
                self.silent_for_everyone(line)

    # -- SPD-253: a value naming the line's own variable ----------------------------------------------------------------
    def test_253_the_harness_s_shadow_runs_the_line_s_execpath(self):
        for line in ("CLAUDE_CODE_EXECPATH=/tmp/x.sh grep a f", "CLAUDE_CODE_EXECPATH=$(echo /tmp/x.sh) grep a f",
                     "CLAUDE_CODE_EXECPATH=/tmp/x.sh grep", "CLAUDE_CODE_EXECPATH=/tmp/x.sh find . -name x",
                     "CLAUDE_CODE_EXECPATH=/tmp/x.sh rg x", "export CLAUDE_CODE_EXECPATH=/tmp/x.sh; grep a f",
                     "X=/tmp/x.sh; CLAUDE_CODE_EXECPATH=$X grep a f", "grep a f | CLAUDE_CODE_EXECPATH=/tmp/x.sh grep b"):
            self.refused_not_spud(line, "$_cc_bin")

    def test_253_a_body_value_naming_a_line_variable_is_the_member_s(self):
        for line in ("GITVERB=$(echo push); linesub", "GITVERB=$(echo push); linecopy", "GITVERB=push; linecopy",
                     "GITVERB=$(echo push); linedefault", "GITVERB=push; lineprint", "GITVERB=$(echo push); lineprint"):
            self.refused_not_spud(line, "")
        self.refused_for_members("OUTFILE=$(echo /tmp/spd-258-x); linewrite", VARIABLE_WORDING)
        self.assertRefused("OUTFILE=$(echo /tmp/spd-258-x); linewrite", VARIABLE_WORDING, agent_id=None)  # SPD-091

    def test_253_the_shadows_and_the_environment_s_values_stay_silent(self):
        for line in ("grep a f", "find . -name x | grep y", "grep a f | grep -v b", "grep -c a f; grep -c b f", "pkill -f x",
                     "rg x", "rg x | grep y", "grep claude f", "grep x /Users/Someone/.local/bin/claude", "linesub",
                     "linecopy", "linewrite", "linedefault", "lineprint", "X=/tmp/x.sh; grep a f", "cat f | grep x",
                     "echo x | pkill -f y", "_cc_a=1; grep a f"):
            with self.subTest(line=line):
                self.silent_for_everyone(line)

    # -- SPD-248 (1): a loop variable over the words as the command word ------------------------------------------------
    def test_248_a_loop_variable_over_the_words_as_the_command_word(self):
        self.refused_not_spud("loopcmd git", "comes from a variable")
        self.refused_not_spud("loopcmd ls", "comes from a variable")  # refused on doubt, as SPD-205's loop is
        self.refused_not_spud("zloopcmd git", "comes from a variable")  # zsh's `for f ( ... )` list, read as `in`'s is
        for line in ("loopcmd", "zloopcmd", "ownloop x"):
            with self.subTest(line=line):
                self.silent_for_everyone(line)

    # -- the call's standard input is the member's too -------------------------------------------------------------------
    def test_a_builtin_reading_the_call_s_input_fills_the_member_s_variable(self):
        for line in ("echo push | readstdin", "readstdin <<< push", "readstdin < f", "readstdin push"):
            self.refused_not_spud(line, "cannot resolve")
        self.silent_for_everyone("readstdin")


# Profile functions that move the shell's directory (SPD-252): this Mac's mkcd, take and takedir with take's helpers as
# its snapshot prints them (snapshot-zsh-1790235321145-pm4nbj.sh), then functions of the test's own making -- an
# unconditional cd, a subshell body as zsh's `typeset -f` prints `subcd () ( cd "$1" )` (probed), pushd and popd, a cd
# through another function, a cd into a variable the line does not settle, a body that calls itself before its cd -- and
# ones that write or run git relative to where they are called.
DIRECTORY_FUNCTIONS = """\
# Functions
mkcd () {
\tmkdir -p $@ && cd ${@:$#}
}
take () {
\tif [[ $1 =~ ^(https?|ftp).*\\.(tar\\.(gz|bz2|xz)|tgz)$ ]]
\tthen
\t\ttakeurl "$1"
\telif [[ $1 =~ ^(https?|ftp).*\\.(zip)$ ]]
\tthen
\t\ttakezip "$1"
\telif [[ $1 =~ ^([A-Za-z0-9]\\+@|https?|git|ssh|ftps?|rsync).*\\.git/?$ ]]
\tthen
\t\ttakegit "$1"
\telse
\t\ttakedir "$@"
\tfi
}
takedir () {
\tmkdir -p $@ && cd ${@:$#}
}
takegit () {
\tgit clone "$1"
\tcd "$(basename ${1%%.git})"
}
takeurl () {
\tlocal data thedir
\tdata="$(mktemp)"
\tcurl -L "$1" > "$data"
\ttar xf "$data"
\tthedir="$(tar tf "$data" | head -n 1)"
\trm "$data"
\tcd "$thedir"
}
takezip () {
\tlocal data thedir
\tdata="$(mktemp)"
\tcurl -L "$1" > "$data"
\tunzip "$data" -d "./"
\tthedir="$(unzip -l "$data" | awk 'NR==4 {print $4}' | sed 's/\\/.*//')"
\trm "$data"
\tcd "$thedir"
}
gocd () {
\tcd "$1"
}
subcd () {
\t(
\t\tcd "$1"
\t)
}
pushit () {
\tpushd "$1" > /dev/null
}
popit () {
\tpopd > /dev/null
}
nestcd () {
\tgocd "$1"
}
projcd () {
\tcd "$PROJECT_DIR"
}
spin () {
\tspin
\tcd sub
}
noteit () {
\techo hi > note.txt
}
vgit () {
\tgit $V
}
leavecd () {
\ttrap "cd $1" EXIT
}
cdable () {
\tsetopt cdablevars
}
# Aliases
alias -- cdtests='cd tests'
"""


class FunctionDirectoryTest(ShellSnapshotCase):
    """SPD-252 (proposal by SPUD-246/Oliver): analyse_shell_text read a function body the shell holds through
    analyse.isolated, which put the directories back after it, but a function runs in the line's shell and its cd stays:
    after this Mac's `takedir <dir>` (mkcd and take alike) a relative write lands in <dir> while the hook read it where the
    line stood before the call, so a write outside a member's deliverables could pass (Law 5) and one inside be refused.

    The rule now: what a called body does to the shell's directory that outlasts it -- a cd, pushd or popd, one a function
    it calls makes -- reaches the rest of the line as the same cd on the line would, and what does not (a subshell body, a
    call in a substitution, in a pipeline element before the last, in the background, behind coproc) does not.  A body
    that leaves the directory where the hook cannot follow it -- a cd into a variable the line does not settle, popd, take's
    url branches, a body that calls itself before its cd -- leaves the rest of the line's relative writes refused, as that cd
    on the line leaves them.  A body is read once per call's words, standard input and the state the call starts in, the
    directories and the line's variables among it, and a call that reads the same again is given the directories the
    reading left: `noteit; cd ..; noteit` writes a second note.txt, and `V=status; vgit; V=push; vgit` pushes.

    Probed 2026-09-24 through tests/probes/shell_probe.py in zsh 5.9 (arm64-apple-darwin26.0) -f -o nobareglobqual and
    -f, and GNU bash 3.2.57, alike unless named: after `takedir x1`, `mkcd a` (a existing), `mkcd p q`, `take x2/y`,
    `gocd b`, a pushd in a body and a cd through a second function, `pwd` printed the new directory; after a popd in a
    body, the one the pushd before it left; after `mkcd b; mkcd b`, b/b; after a subshell body's cd, `$(gocd b)`, `gocd b |
    cat`, `(gocd b)`, `gocd b &` and `gocd nonexistent`, the directory before the call; after `true | gocd b`, b in both
    zsh readings and the directory before in bash.  zsh's `typeset -f` prints `subcd () ( cd "$1" )` with its subshell
    inside braces.  The Bash tool's own shell (zsh 5.9, the line run by `eval` in `zsh -c` after sourcing the snapshot)
    sets AUTO_CD, AUTO_PUSHD and PUSHD_MINUS from the profile, but AUTO_CD applies only to a shell reading its commands on
    standard input: `cd /usr; share; pwd` there printed "command not found: share" and /usr, so a bare directory name moves
    nothing; AUTO_PUSHD and PUSHD_MINUS change only the stack a popd or a `cd -N` reads, which the hook never follows.

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud."""

    def setUp(self):
        super().setUp()
        path = self.write_snapshot("snapshot-zsh-1700000000027-252252.sh", DIRECTORY_FUNCTIONS)
        newest = path.stat().st_mtime + 60  # newer than SHELL_SNAPSHOT, whose `take` and `noteit` this one replaces
        os.utime(path, (newest, newest))
        self.m = load_spud_module()
        self.tests = self.home.path / "tests"
        (self.tests / "sub").mkdir(parents=True, exist_ok=True)
        self.away = str(self.home.path / "away")  # a directory no member plans, which does not exist

    def analysis(self, command, cwd=None):
        env = dict(os.environ)
        env["SPUD_USER_CLAUDE_DIR"] = self.home.env["SPUD_USER_CLAUDE_DIR"]
        with mock.patch.dict(os.environ, env, clear=True):
            return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=cwd or str(self.home.path), home=str(self.home.path)))

    def written_in(self, command, target, cwd=None):
        """The directories each redirection of `target` on the line may open in, in order."""
        return [c for t, c in self.analysis(command, cwd).redirects if t == target]

    # -- the ticket's evidence ------------------------------------------------------------------------------------------
    def test_the_tickets_evidence(self):
        """`takedir <dir>; echo hi > f` records f where `mkdir -p <dir> && cd <dir>; echo hi > f` does: in <dir>, and where
        the line stood, since the hook does not know the directory will exist."""
        home = str(self.home.path)
        for name in ("takedir", "mkcd"):
            with self.subTest(name=name):
                line = "%s %s; echo hi > f" % (name, self.away)
                self.assertEqual(self.written_in(line, "f"), [frozenset([home, self.away])])
                self.assertEqual(self.written_in(line, "f"), self.written_in("mkdir -p %s && cd %s; echo hi > f" % (self.away, self.away), "f"))
        self.assertEqual(self.written_in("mkcd p %s; echo hi > f" % self.away, "f"), [frozenset([home, self.away])])  # its last word
        self.assertEqual(self.written_in("gocd tests; echo hi > f", "f"), [frozenset([str(self.tests)])])
        # a call reads as its body in a group: the hook does not read which of the body's commands its status is, so a
        # write after `takedir d &&` is read in both directories, as after `{ mkdir -p d && cd d; } &&`
        self.assertEqual(self.written_in("takedir tests/sub && echo hi > f; echo hi > f", "f"),
                         self.written_in("{ mkdir -p tests/sub && cd tests/sub; } && echo hi > f; echo hi > f", "f"))

    def test_a_write_the_function_moved_out_of_the_deliverables_is_refused(self):
        """From tests/, which the members plan, the line's note.txt lands in the home once the function has moved there."""
        cwd = str(self.tests)
        for line in ("gocd %s; echo hi > note.txt", "nestcd %s; echo hi > note.txt", "pushit %s; echo hi > note.txt",
                     "gocd %s && echo hi > note.txt", "true | gocd %s; echo hi > note.txt", "time gocd %s; echo hi > note.txt",
                     "X=1 gocd %s; echo hi > note.txt", "{ gocd %s; }; echo hi > note.txt", "gocd %s; cp f note.txt",
                     "if true; then gocd %s; fi; echo hi > note.txt", "eval gocd %s; echo hi > note.txt"):
            line = line % self.home.path
            with self.subTest(line=line):
                self.refused_for_members(line, "note.txt", cwd)

    def test_a_write_the_function_moved_into_the_deliverables_is_allowed(self):
        """From the home, where note.txt is nobody's, the line writes it in tests/ once the function has moved there; and
        from tests/, takedir's own directory and the one it may not reach are both a member's."""
        for line in ("gocd tests; echo hi > note.txt", "nestcd tests; echo hi > note.txt", "pushit tests; echo hi > note.txt",
                     "gocd tests && echo hi > note.txt", "gocd tests; gocd sub; echo hi > note.txt"):
            with self.subTest(line=line):
                self.assertSilent(line, AGENT_A)
                self.assertSilent(line, AGENT_B)
                self.assertRefused(line, "Law 1", agent_id=None)
        for line in ("takedir sub; echo hi > note.txt", "mkcd sub; echo hi > note.txt", "takedir sub && echo hi > note.txt"):
            with self.subTest(line=line):
                self.assertSilent(line, AGENT_A, str(self.tests))

    def test_what_does_not_outlast_the_call_stays_where_it_was(self):
        """A subshell body, and a call in a substitution, a pipeline element before the last, a subshell, the background
        or a coproc, leaves the line where it was."""
        cwd = str(self.tests)
        for line in ("subcd %s; echo hi > note.txt", "echo $(gocd %s); echo hi > note.txt", "gocd %s | cat; echo hi > note.txt",
                     "(gocd %s); echo hi > note.txt", "gocd %s & echo hi > note.txt", "coproc gocd %s; echo hi > note.txt",
                     "echo hi > note.txt; gocd %s"):
            line = line % self.home.path
            with self.subTest(line=line):
                self.assertSilent(line, AGENT_A, cwd)
                self.assertSilent(line, AGENT_B, cwd)

    def test_the_prefix_decides_whether_the_move_reaches_the_line(self):
        """A function or an alias moves the shell the command runs in: coproc's fork leaves the line where it was, zsh's
        nocorrect moves it and bash finds no nocorrect (either directory), `time` and a prefix assignment move it.  Probed
        in zsh 5.9 -f -o nobareglobqual and -f, and bash 3.2.57, through `eval`: after `coproc gocd b` and `coproc cdb`
        (`alias cdb='cd b'`) `pwd` printed the directory before; after `nocorrect gocd b` b in zsh, the directory before in
        bash; after `time gocd b` and `X=1 gocd b` b.  A line's own alias read inside eval is held to the same rule."""
        for line in ("coproc gocd tests; echo hi > note.txt", "coproc cdtests; echo hi > note.txt",
                     "nocorrect gocd tests; echo hi > note.txt", "nocorrect cdtests; echo hi > note.txt",
                     "alias c='cd tests'; eval coproc c; echo hi > note.txt"):
            with self.subTest(line=line):
                self.refused_for_members(line, "note.txt")
        for line in ("time gocd tests; echo hi > note.txt", "cdtests; echo hi > note.txt", "time cdtests; echo hi > note.txt",
                     "alias c='cd tests'; eval c; echo hi > note.txt"):
            with self.subTest(line=line):
                self.assertSilent(line, AGENT_A)

    def test_a_directory_the_body_leaves_unknown_fails_closed(self):
        """A cd into a variable the line does not settle, a popd, take (its url branches cd into what an archive holds, and
        the hook reads every branch), a body that calls itself before its cd, an EXIT trap set in the body whose action
        cds (zsh runs it as the function returns), and a CDABLE_VARS the body set for a later cd into a name that is no
        directory (tests/test_hooks_writes.py MovedBetweenCommandsTest has both probes): the rest of the line's relative
        writes are refused for everyone, as after `cd "$PROJECT_DIR"` on the line; an absolute one, and one before the
        call, read as they did."""
        for line in ("projcd; echo hi > note.txt", "popit; echo hi > tests/note.txt", "spin; echo hi > tests/note.txt",
                     "takeurl https://h/x.tgz; echo hi > tests/x", "leavecd /tmp; echo hi > tests/note.txt",
                     "cdable; D=/tmp; cd D; echo hi > tests/note.txt"):
            with self.subTest(line=line):
                self.refused_for_members(line, "cannot follow")
                self.assertRefused(line, "cannot follow", agent_id=None)
        self.refused_for_members("take tests/x; echo hi > tests/note.txt", "Law 7")  # take's takegit branch clones first
        self.assertRefused("take tests/x; echo hi > tests/note.txt", "cannot follow", agent_id=None)
        for line in ('cd "$PROJECT_DIR"; echo hi > f', "projcd; echo hi > f", "take tests/x; echo hi > f", "spin; echo hi > f"):
            with self.subTest(line=line):
                self.assertEqual(self.written_in(line, "f"), [None])
        self.assertSilent("projcd; echo hi > %s/tests/note.txt" % self.home.path, AGENT_A)
        self.assertSilent("echo hi > tests/note.txt; projcd", AGENT_A)
        self.assertSilent("PROJECT_DIR=%s; projcd; echo hi > note.txt" % self.tests, AGENT_A)  # a value the line settled

    # -- a call is read from the state it starts in -----------------------------------------------------------------------
    def test_a_call_from_another_directory_is_read_again(self):
        """The body's cd compounds, and its own relative write lands where each call runs."""
        tests, sub = str(self.tests), str(self.tests / "sub")
        self.assertEqual(self.written_in("mkcd sub; mkcd sub; echo hi > f", "f", tests),
                         [frozenset([tests, sub, os.path.join(sub, "sub")])])
        self.refused_for_members("noteit; cd ..; noteit", "note.txt", tests)
        self.refused_for_members("noteit; gocd ..; noteit", "note.txt", tests)
        self.assertSilent("noteit; cd sub; noteit", AGENT_A, tests)

    def test_a_call_read_before_moves_the_line_again(self):
        """A second call that reads exactly as the first -- after a subshell put the directory back, or in the line's
        second reading -- still leaves the shell where the first reading did."""
        cwd = str(self.tests)
        for line in ("(gocd %s); gocd %s; echo hi > note.txt", "(gocd %s) & gocd %s; echo hi > note.txt",
                     "echo $(gocd %s); gocd %s; echo hi > note.txt"):
            line = line % (self.home.path, self.home.path)
            with self.subTest(line=line):
                self.refused_for_members(line, "note.txt", cwd)
        # a compound command's own input has the line walked twice (SPD-210), the second walk from the line's start
        for line in ("{ gocd tests; } < /dev/null; echo hi > note.txt", "gocd tests; { echo x; } < /dev/null; echo hi > note.txt"):
            with self.subTest(line=line):
                self.assertSilent(line, AGENT_A)

    def test_a_call_after_the_line_changed_a_variable_is_read_again(self):
        """A body reads the line's variables where it runs: the second vgit runs git push."""
        for line in ("V=status; vgit; V=push; vgit", "V=status; vgit; V=$(echo push); vgit"):
            with self.subTest(line=line):
                self.refused_for_members(line, "")
                self.assertSilent(line, agent_id=None)
        self.silent_for_everyone("V=status; vgit; vgit")


MODIFIER_FUNCTIONS = """\
# Functions
vgit () {
\tgit $V
}
gocd () {
\tcd "$1"
}
nice () {
\tgit push "$@"
}
# Aliases
alias -- sudo='sudo '
"""


class ModifierLookupTest(ShellSnapshotCase):
    """SPD-262 (proposal by SPUD-252/Bill): zsh still looks the word after its noglob, exec and `-` precommand modifiers up
    as a function, but dispatch_words read the text the shell holds only while the command position held, which those
    take away, so a snapshot function behind them ran unread: `noglob ggp` and `exec ggp` pushed past Law 7, and `V=push;
    noglob vgit` too, while `nocorrect ggp` and `time vgit` were refused.  A function is now read behind every modifier
    after which the shell looks the word up as one, and an alias -- which zsh expands only where the command position
    holds -- where it held before; and the modifier's own word, looked up the same way, is read as a function or an alias
    the shell holds under that name (`nice` below, `alias sudo='sudo '` chaining into the next word).

    Probed 2026-09-24 through tests/probes/shell_probe.py in zsh 5.9 (arm64-apple-darwin26.0) -f -o nobareglobqual and -f,
    alike, and GNU bash 3.2.57, with `foo () { echo FN "$@"; }`: FN ran after noglob, nocorrect, time, `-`, exec (and
    exec -c, -l, -cl, --, -a nm), after each chained with another (`noglob -`, `- noglob`, `noglob exec`, `exec noglob`,
    `exec -`, `- exec`, `nocorrect noglob`, `time noglob`, `X=1 noglob`, `! noglob`, `{ noglob`, `if noglob`, `coproc
    noglob`), after `builtin` naming a modifier (`builtin noglob`, `builtin exec`, `builtin -`, `builtin builtin
    noglob`), and after `$W` holding noglob, exec, `-` or builtin; it did not after builtin or command alone, `command
    -p`, `noglob command`, `noglob builtin`, `command noglob`, `exec command`, `exec builtin`, `builtin nocorrect`, and
    after nocorrect or time where the command position no longer holds (`noglob nocorrect`: "command not found:
    nocorrect"; `noglob time` is /usr/bin/time), nor after NOGLOB or Exec, which are no modifiers.  bash ran FN after
    `time` alone (`exec foo`: "exec: foo: not found").  An alias (`alias al='echo ALIAS'`) expanded after nocorrect and
    time only, in zsh; bash expands none in a script.  A function named noglob, exec, command, builtin or nice ran in
    place of that word in all three, and `alias nice='nice '` expanded the word after it.

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud."""

    def setUp(self):
        super().setUp()
        path = self.write_snapshot("snapshot-zsh-1700000000062-262262.sh", MODIFIER_FUNCTIONS)
        newest = path.stat().st_mtime + 60
        os.utime(path, (newest, newest))

    def test_the_tickets_evidence(self):
        for line in ("noglob ggp", "exec ggp", "V=push; noglob vgit", "V=push; exec vgit", "nocorrect ggp", "V=push; time vgit"):
            with self.subTest(line=line):
                self.assertIn("git push", self.refused_for_members(line).reason)

    def test_every_modifier_after_which_the_shell_looks_a_function_up(self):
        for line in ("- ggp", "builtin noglob ggp", "builtin exec ggp", "builtin - ggp", "builtin builtin noglob ggp",
                     "noglob exec ggp", "exec noglob ggp", "exec -c ggp", "exec -l ggp", "exec -cl ggp", "exec -- ggp",
                     "exec -a nm ggp", "noglob - ggp", "- noglob ggp", "- exec ggp", "exec - ggp", "nocorrect noglob ggp",
                     "time noglob ggp", "X=1 noglob ggp", "! noglob ggp", "{ noglob ggp; }", "if noglob ggp; then :; fi",
                     "coproc noglob ggp", "W=noglob; $W ggp", "W=exec; $W ggp", "W=-; $W ggp", "F=ggp; noglob $F",
                     "echo $(noglob ggp)", "eval noglob ggp"):
            with self.subTest(line=line):
                self.assertIn("git push", self.refused_for_members(line).reason)

    def test_a_modifier_that_resolves_the_word_itself_is_not_read_as_the_function(self):
        """command and builtin find a program or a builtin, never a function, and so does a modifier the shell reads as
        a plain command name where the command position is gone: the word runs as the program it names, which the hook
        reads as it always did (no such program here)."""
        for line in ("command ggp", "command -p ggp", "builtin ggp", "noglob command ggp", "noglob builtin ggp",
                     "noglob nocorrect ggp", "exec nocorrect ggp", "noglob time ggp", "exec command ggp", "exec builtin ggp",
                     "builtin nocorrect ggp", "NOGLOB ggp", "Exec ggp", "nohup ggp", "env ggp", "V=push; command vgit"):
            with self.subTest(line=line):
                self.silent_for_everyone(line)

    def test_an_alias_is_expanded_only_where_the_command_position_holds(self):
        for line in ("noglob gp", "- gp", "exec gp", "command gp", "builtin gp", "builtin noglob gp"):
            with self.subTest(line=line):
                self.silent_for_everyone(line)
        for line in ("nocorrect gp", "time gp"):
            with self.subTest(line=line):
                self.refused_for_members(line)

    def test_the_directory_a_function_behind_noglob_leaves(self):
        """noglob is zsh's (bash finds no noglob), so the line may be in either directory after it, as after `nocorrect
        gocd`: a relative write outside the deliverables is refused."""
        self.refused_for_members("noglob gocd %s; echo hi > note.txt" % self.home.path, "note.txt", str(self.home.path / "tests"))
        self.refused_for_members("- gocd %s; echo hi > note.txt" % self.home.path, "note.txt", str(self.home.path / "tests"))

    def test_a_function_or_alias_named_for_the_modifier_runs_in_its_place(self):
        for line in ("nice ls", "noglob nice ls", "X=1 nice ls", "sudo gp", "time sudo gp"):
            with self.subTest(line=line):
                self.assertIn("git push", self.refused_for_members(line).reason)
        # the chain reaches the next word alone: `-u` is no alias, so gp after it is not expanded
        for line in ("command nice ls", "sudo ggp", "env nice ls", "sudo -u root gp", "noglob sudo gp"):
            with self.subTest(line=line):
                self.silent_for_everyone(line)

    def test_a_function_the_line_defines_behind_builtin_noglob(self):
        """The line's own function shadows the name behind `builtin noglob` as it does behind noglob, and not behind
        `builtin` alone ("no such builtin")."""
        for line in ("git () { :; }; builtin noglob git status", "git () { :; }; noglob git status",
                     "git () { :; }; - git status", "git () { :; }; builtin exec git status"):
            with self.subTest(line=line):
                self.refused_for_members(line, "shell function `git`")
        self.silent_for_everyone("git () { :; }; command git status")


def options_snapshot(*lines):
    """A snapshot holding only a `# Shell Options` section of these lines, as Claude Code writes one after the functions
    (`setopt | sed 's/^/setopt /'`), and the aliases after it."""
    return "# Functions\n# Shell Options\n" + "".join(line + "\n" for line in lines) + "# Aliases\nalias -- gp='git push'\n"


# This Mac's profile, as every snapshot in ~/.claude/shell-snapshots/ wrote it on 2026-09-24 (snapshot-zsh-1790261341840-
# igxh0c.sh lines 3378-3397): oh-my-zsh's options and the interactive shell's own.
THIS_MACS_OPTIONS = ("setopt alwaystoend", "setopt autocd", "setopt autopushd", "setopt completeinword", "setopt extendedhistory",
                     "setopt noflowcontrol", "setopt nohashdirs", "setopt histexpiredupsfirst", "setopt histignoredups",
                     "setopt histignorespace", "setopt histverify", "setopt interactivecomments", "setopt login",
                     "setopt longlistjobs", "setopt nopromptcr", "setopt nopromptsp", "setopt promptsubst",
                     "setopt pushdignoredups", "setopt pushdminus", "setopt sharehistory")


class SnapshotOptionsTest(ShellSnapshotCase):
    """SPD-263 (proposal by SPUD-252/Bill): the snapshot's `# Shell Options` section -- `setopt` lines the shell sources
    before every Bash call -- was never read, so a profile that sets CDABLE_VARS moved every `cd` into a relative name that
    is no directory to a variable's value while the hook read cwd/name.  The options are now read into the table, and a
    line starts from the state they give: cdablevars on is ShellAnalysis.cdable from the line's first word.  An option
    the reader does not model that changes how the shell reads words fails closed for a member, naming the profile line.

    Probed 2026-09-24 through tests/probes/shell_probe.py, zsh 5.9 -f -o nobareglobqual and -f, bash 3.2.57: with
    `setopt cdablevars` (CDABLE_VARS, cdable_vars and `unsetopt nocdablevars` alike) or `shopt -s cdable_vars`, `cd dest`
    went to $dest; with `setopt nocdablevars` it stayed.  shwordsplit split `$X` holding `a b` in two, globsubst globbed
    `$X` holding `g*`, ksharrays made `$A` its first element, and extendedglob made `^keep` a glob, in zsh (bash split and
    globbed already).  This Mac's options change nothing the hook reads: AUTO_CD needs a shell reading standard input
    (FunctionDirectoryTest's docstring), AUTO_PUSHD, PUSHD_MINUS and PUSHD_IGNORE_DUPS change the stack only
    popd, a stack entry and `cd -N` read, which the hook never follows, and the rest are history, completion, prompt, job
    and hashing options.

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud."""

    def setUp(self):
        super().setUp()
        self.m = load_spud_module()
        self.order = 0

    def with_options(self, *lines, shell="zsh"):
        self.order += 1
        path = self.write_snapshot("snapshot-%s-17000000%05d-263263.sh" % (shell, self.order), options_snapshot(*lines))
        newest = path.stat().st_mtime + 60 * self.order
        os.utime(path, (newest, newest))
        return path

    def analysis(self, command, cwd=None):
        env = dict(os.environ)
        env["SPUD_USER_CLAUDE_DIR"] = self.home.env["SPUD_USER_CLAUDE_DIR"]
        with mock.patch.dict(os.environ, env, clear=True):
            forget_process_caches()  # the table this process read before a snapshot the test wrote since
            return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=cwd or str(self.home.path), home=str(self.home.path)))

    def written_in(self, command, target):
        return [c for t, c in self.analysis(command).redirects if t == target]

    def test_the_tickets_cdablevars(self):
        """A cd into a relative name that is no directory is unknown once the profile sets CDABLE_VARS, as after `setopt
        cdablevars` on the line (SPD-252): the write after it is refused for everyone."""
        home = str(self.home.path)
        self.assertEqual(self.written_in("cd dest; echo x > f", "f"), [frozenset([home, os.path.join(home, "dest")])])
        for spelled in ("setopt cdablevars", "setopt CDABLE_VARS", "setopt cdable_vars", "unsetopt nocdablevars",
                        "setopt autocd cdablevars", "setopt Cdable_Vars"):
            with self.subTest(spelled=spelled):
                shutil.rmtree(self.snapshots)
                self.snapshots.mkdir()
                self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", SHELL_SNAPSHOT)
                self.with_options(spelled)
                self.assertEqual(self.written_in("cd dest; echo x > f", "f"), [None])
                self.refused_for_members("cd dest; echo x > f", "cannot follow")
                self.assertRefused("cd dest; echo x > f", "cannot follow", agent_id=None)
                self.assertSilent("cd tests; echo hi > note.txt", AGENT_A)  # a directory, whatever CDABLE_VARS says
                self.assertSilent("cd %s/tests; echo hi > note.txt" % self.home.path, AGENT_A)

    def test_bash_spells_it_cdable_vars(self):
        self.with_options("shopt -s cdable_vars", shell="bash")
        self.assertEqual(self.written_in("cd dest; echo x > f", "f"), [None])

    def test_chaselinks_reads_a_cd_through_a_symlink_physically(self):
        """CHASE_LINKS (and CHASE_DOTS, for the `..` that follows a link) makes `cd lnk/..` the parent of where lnk points,
        as `cd -P` does (probed: zsh 5.9 printed <dir>/a after `cd lnk/..` with lnk -> a/b, and <dir> without): the write
        after it lands in tests/, a member's, where the logical reading put it in the home."""
        (self.home.path / "lnk").symlink_to(self.home.path / "tests" / "sub")
        line = "cd lnk/..; echo hi > note.txt"
        self.refused_for_members(line, "note.txt")
        for option in ("setopt chaselinks", "setopt chasedots"):
            with self.subTest(option=option):
                self.with_options(option)
                self.assertEqual(self.written_in(line, "note.txt"), [frozenset([str(self.home.path / "tests")])])
                self.assertSilent(line, AGENT_A)
                self.assertSilent(line, AGENT_B)

    def test_a_lines_own_option_builtin_reads_a_cd_through_a_symlink_both_ways(self):
        """The same hole on the line itself: `setopt chaselinks` (or bash's `set -P`) before `cd lnk/..` moves the shell to
        the parent of where lnk points, which the hook read as the directory lnk stands in -- a member's tests/, while the
        write landed in away/.  An option builtin may set it or not, so both directories are read, as SPD-252 reads
        CDABLE_VARS after one.  zsh's -L keeps the path as spelled whatever CHASE_LINKS says, and bash's too (probed: zsh
        5.9 and bash 3.2.57 printed <dir> after `cd -L lnk/..`, <dir>/a after `cd -P lnk/..` and `pushd lnk/..` with it
        on; bash's `set -P` turned it on, zsh's did not)."""
        away = self.home.path / "away" / "sub"
        away.mkdir(parents=True)
        (self.home.path / "tests" / "lnk").symlink_to(away)
        cwd = str(self.home.path / "tests")
        self.assertSilent("cd lnk/..; echo hi > note.txt", AGENT_A, cwd)
        for line in ("setopt chaselinks; cd lnk/..; echo hi > note.txt", "set -P; cd lnk/..; echo hi > note.txt",
                     "setopt chase_dots; pushd lnk/..; echo hi > note.txt"):
            with self.subTest(line=line):
                self.refused_for_members(line, "note.txt", cwd)
        self.with_options("setopt chaselinks")
        self.refused_for_members("cd lnk/..; echo hi > note.txt", "note.txt", cwd)
        self.assertSilent("cd -L lnk/..; echo hi > note.txt", AGENT_A, cwd)

    def test_an_arithmetic_option_makes_arithmetic_opaque_from_the_start(self):
        """OCTAL_ZEROES and its kin change the number an arithmetic expansion gives, as the line's own setopt may (SPD-225)."""
        self.assertFalse(self.analysis("echo hi").arith_opaque)
        for option in ("setopt octalzeroes", "setopt cbases", "setopt cprecedences", "setopt forcefloat"):
            with self.subTest(option=option):
                self.with_options(option)
                self.assertTrue(self.analysis("echo hi").arith_opaque)

    def test_an_option_turned_off_changes_nothing(self):
        before = self.written_in("cd dest; echo x > f", "f")
        for spelled in ("setopt nocdablevars", "unsetopt cdablevars", "setopt NO_CDABLE_VARS"):
            with self.subTest(spelled=spelled):
                self.with_options(spelled)
                self.assertEqual(self.written_in("cd dest; echo x > f", "f"), before)

    def test_any_snapshot_that_sets_it_counts(self):
        """Which snapshot a session sources is not in the hook's input, so an older snapshot's CDABLE_VARS may be the one
        in force: every snapshot's options are read."""
        older = self.with_options("setopt cdablevars")
        self.with_options("setopt autocd")
        self.assertLess(older.stat().st_mtime, (self.snapshots / ("snapshot-zsh-17000000%05d-263263.sh" % 2)).stat().st_mtime)
        self.assertEqual(self.written_in("cd dest; echo x > f", "f"), [None])

    def test_this_macs_options_change_nothing(self):
        lines = ("cd dest; echo x > f", "cd tests; echo hi > note.txt", "pushd tests; echo hi > f", "pushd +1; echo hi > f",
                 "cd -1; echo hi > f", "popd; echo hi > f", "tests; echo hi > f", "echo $((010)) > f", "git status", "gp",
                 "X='a b'; $X", "echo *.txt > f", "cd ~; echo hi > f", "noglob ggp", "setopt cdablevars; cd dest; echo x > f")
        before = {line: self.hook_reading(line) for line in lines}
        self.with_options(*THIS_MACS_OPTIONS)
        self.assertFalse([f for f in self.analysis("echo hi").findings if f[0] == "unread"])  # none unmodelled
        options = sys.modules["spudlib.hooks.snapshots"].shell_table(str(self.home.path)).options
        self.assertEqual(len(options), len(THIS_MACS_OPTIONS))  # every line read
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(self.hook_reading(line), before[line])
        self.assertSilent("cd tests; echo hi > note.txt", AGENT_A)

    def test_an_option_the_reader_does_not_model_fails_closed_for_a_member(self):
        """shwordsplit, globsubst, ksharrays and extendedglob change the words the shell makes of a line (probed), and an
        option the hook does not know may too: a member's line is refused, naming the profile's line, while Spud's is
        read as it was."""
        for option in ("shwordsplit", "SH_WORD_SPLIT", "globsubst", "ksharrays", "extendedglob", "rcquotes", "noglob",
                       "posixbuiltins", "somethingnew", "nomultios"):
            with self.subTest(option=option):
                shutil.rmtree(self.snapshots)
                self.snapshots.mkdir()
                self.with_options("setopt " + option)
                found = [f for f in self.analysis("echo hi").findings if f[0] == "unread"]
                self.assertEqual(len(found), 1, found)
                form, shown = found[0][1]
                self.assertEqual(form, "option")
                self.assertIn("setopt " + option, shown)
                self.assertSilent("echo hi", agent_id=None)
                if "option" in sys.modules["spudlib.shell.bash_rule"].UNREAD_MESSAGES:  # the reason a member adds to shell/bash_rule (SPD-262)
                    self.refused_for_members("echo hi", "setopt " + option)

    def test_the_option_lines_as_the_snapshot_reader_takes_them_apart(self):
        """Each name a line lists is one option; a line of another shape is kept whole as one the reader cannot model; a
        function's body -- indented, as zsh prints it -- and a function named like the builtin are no option lines."""
        text = ("# Functions\nquiet () {\n\tsetopt localoptions shwordsplit\n}\nset () {\n\tbuiltin set \"$@\"\n}\n"
                "setup () {\n\ttrue\n}\n# Shell Options\nsetopt autocd NO_hash_dirs\nunsetopt nomatch\nshopt -s cdable_vars\n"
                "shopt -p\nset -o physical +o braceexpand\nsetopt -m 'no*'\nsetopt\n")
        m = load_spud_module()
        _, functions, _, options = m.read_snapshot(text.encode(), 0)
        self.assertEqual(sorted(functions), ["quiet", "set", "setup"])
        self.assertEqual([o[:3] for o in options],
                         [("setopt", "autocd", True), ("setopt", "NO_hash_dirs", True), ("setopt", "nomatch", False),
                          ("shopt", "cdable_vars", True), ("set", "physical", True), ("set", "braceexpand", False),
                          ("setopt", None, True)])
        self.assertEqual([m.option_effect(*o[:3]) for o in options], [None, None, "unread", "cdable", "chase", "unread", "unread"])

    def test_an_old_cache_is_rebuilt_never_misread(self):
        """The table's cache now holds the options too, so one written before them is built again: read as it stands, it
        would give a table with no options, and a CDABLE_VARS profile would read as none."""
        self.with_options("setopt cdablevars")
        cache = self.home.path / ".spud" / "shell-snapshot.json"
        self.assertEqual(self.written_in("cd dest; echo x > f", "f"), [None])
        stored = json.loads(cache.read_text(encoding="utf-8"))
        self.assertIn("options", stored)
        old = {k: v for k, v in stored.items() if k not in ("options", "format")}
        cache.write_text(json.dumps(old), encoding="utf-8")
        self.assertEqual(self.written_in("cd dest; echo x > f", "f"), [None])
        self.assertIn("options", json.loads(cache.read_text(encoding="utf-8")))


# Claude Code's own shadows byte for byte as every snapshot on this Mac held them on 2026-09-24
# (snapshot-zsh-1790263809802-78wrv5.sh, the four before it alike), the harness's comment lines and rg's `if` included, with
# the Mac's claude path spelled as a scratch one: the text hooks/snapshots.HARNESS_SHADOWS recognizes.  HARNESS_SHADOWS above
# spells pkill's dash `--` where the harness writes `—`, so its pkill is other text, read in full; its find, grep and rg
# are the harness's, which every test using it now reads through held_shadows.read_shadow.
SCRATCH_CLAUDE = "/Users/Someone/.local/bin/claude"
THIS_MACS_SHADOWS = """\
# Check for rg availability
if ! (unalias rg 2>/dev/null; command -v rg) >/dev/null 2>&1; then
  function rg {
  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"
  [[ -x $_cc_bin ]] || _cc_bin=/Users/Someone/.local/bin/claude
  if [[ ! -x $_cc_bin ]]; then command rg ${1+"$@"}; return; fi
  if [[ -n ${ZSH_VERSION:-} ]]; then
    ARGV0=rg "$_cc_bin" ${1+"$@"}
  elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ "$OSTYPE" == "win32" ]]; then
    ARGV0=rg "$_cc_bin" ${1+"$@"}
  else
    (exec -a rg "$_cc_bin" ${1+"$@"})
  fi
}
fi
# Shadow find/grep with embedded bfs/ugrep
unalias find 2>/dev/null || true
unalias grep 2>/dev/null || true
function find {
  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"
  [[ -x $_cc_bin ]] || _cc_bin=/Users/Someone/.local/bin/claude
  if [[ ! -x $_cc_bin ]]; then command find ${1+"$@"}; return; fi
  if [[ -n ${ZSH_VERSION:-} ]]; then
    ARGV0=bfs "$_cc_bin" -S dfs -regextype findutils-default ${1+"$@"}
  elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ "$OSTYPE" == "win32" ]]; then
    ARGV0=bfs "$_cc_bin" -S dfs -regextype findutils-default ${1+"$@"}
  else
    (exec -a bfs "$_cc_bin" -S dfs -regextype findutils-default ${1+"$@"})
  fi
}
function grep {
  local _cc_a
  for _cc_a in ${1+"$@"}; do
    case "$_cc_a" in -*-filter*|-*-pager*|-*-view*|-*-format-open*|-*-config*|---*|-@*|-*-save-config*|-[Zz]*|-[!-]*[Zz]*|--null|--null-data) command grep ${1+"$@"}; return ;; esac
  done
  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"
  [[ -x $_cc_bin ]] || _cc_bin=/Users/Someone/.local/bin/claude
  if [[ ! -x $_cc_bin ]]; then command grep ${1+"$@"}; return; fi
  if [[ -n ${ZSH_VERSION:-} ]]; then
    ARGV0=ugrep "$_cc_bin" -G --ignore-files --hidden -I --exclude-dir=.git --exclude-dir=.svn --exclude-dir=.hg --exclude-dir=.bzr --exclude-dir=.jj --exclude-dir=.sl ${1+"$@"}
  elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ "$OSTYPE" == "win32" ]]; then
    ARGV0=ugrep "$_cc_bin" -G --ignore-files --hidden -I --exclude-dir=.git --exclude-dir=.svn --exclude-dir=.hg --exclude-dir=.bzr --exclude-dir=.jj --exclude-dir=.sl ${1+"$@"}
  else
    (exec -a ugrep "$_cc_bin" -G --ignore-files --hidden -I --exclude-dir=.git --exclude-dir=.svn --exclude-dir=.hg --exclude-dir=.bzr --exclude-dir=.jj --exclude-dir=.sl ${1+"$@"})
  fi
}
# Shadow pkill to refuse patterns matching the CLI process
unalias pkill 2>/dev/null || true
function pkill {
  if [ -n "${CLAUDE_PID:-}" ] && [ -r "/proc/${CLAUDE_PID}/comm" ]; then
    local _cc_skip="" _cc_a
    local -a _cc_probe=()
    for _cc_a in ${1+"$@"}; do
      if [ -n "$_cc_skip" ]; then _cc_skip=""; continue; fi
      case "$_cc_a" in
        --signal) _cc_skip=1 ;;
        --signal=*|-e|--echo) ;;
        -[0-9]*) ;;
        -[PUGOF]?*) _cc_probe+=("$_cc_a") ;;
        -[ABCDEFGHIJKLMNOPQRSTUVWXYZ][ABCDEFGHIJKLMNOPQRSTUVWXYZ0-9]*) ;;
        *) _cc_probe+=("$_cc_a") ;;
      esac
    done
    if command pgrep ${_cc_probe[@]+"${_cc_probe[@]}"} 2>/dev/null | command grep -qx "${CLAUDE_PID}"; then
      printf 'pkill: refusing to run — this pattern matches the Claude CLI process (PID %s). Narrow the pattern, or target your own children with `pkill -P $$ ...`.\\n' "${CLAUDE_PID}" >&2
      return 1
    fi
  fi
  command pkill ${1+"$@"}
}
"""
SHADOWS = ("find", "grep", "rg", "pkill")
# The words a call hands a shadow, one of each kind the reading tells apart (held_shadows.read_shadow): literal ones, quoted
# ones whose sentinels it takes off, a dash that keeps grep's loop from being dashless, names the words spell and the
# bodies' own, the primaries find acts on, and what expands -- read in full, and compared all the same
SHADOW_WORDS = ("x", "-rn", "-v", "--color=auto", "--", "-", "-Z", "-9", "-KILL", "--signal", ".", "/tmp", "src/a.py", "~/x",
                "'def foo'", "'*.py'", "*.py", "'a|b'", "'$HOME'", "'^$'", "'$1'", "$X", "$$", "$(echo x)", "{a,b}", "''",
                "-delete", "-exec echo {} ;", "-fprint out", "-name", "_cc_bin", "_cc_a", "ARGV0", "IFS", "in", "a=b")
# ... and the lines a call stands in: pipes, lists, what a line assigns or defines before it, the wrappers and readings it
# is read behind, and every way SPD-253 and SPD-258 set CLAUDE_CODE_EXECPATH for it
SHADOW_CONTEXTS = ("{}", "cd /tmp && {}", "cat f | {}", "{} | head", "x=1; {}", "x=status; {}; git $x", "find . -name y | {}",
                   "grep a f | {}", "{}; {}", "pkill -f z; {}", "for i in a b; do {}; done", "while true; do {}; done",
                   "for ((i = 0; i < 2; i++)); do {}; done", "echo $({})", "{} > out",
                   "echo 'git push' | {}", "noglob {}", "exec {}", "command {}", "xargs {}", "eval '{}'", "sh -c '{}'",
                   "f(){{ :; }}; {}", "alias g=grep; {}", "ARGV0=z; {}", "typeset -i ARGV0; {}", "_cc_bin=/tmp/x; {}",
                   "OSTYPE=msys; {}", "PATH=/x:$PATH; {}", "IFS=:; {}", "setopt x; {}", "source f; {}",
                   "hash grep=/tmp/g; {}", "cd $Q; {}", "n=$(basename a/b); {}; echo $n",
                   "CLAUDE_CODE_EXECPATH=/tmp/x.sh {}", "CLAUDE_CODE_EXECPATH=$(echo /tmp/x.sh) {}",
                   "export CLAUDE_CODE_EXECPATH=/tmp/x.sh; {}", "X=/tmp/x.sh; CLAUDE_CODE_EXECPATH=$X {}",
                   "{} | CLAUDE_CODE_EXECPATH=/tmp/x.sh {}", ": ${{OSTYPE:=x}}; {}", "find . -exec {} {{}} \\;",
                   "echo $(echo $(echo $(echo $(echo $({})))))", "echo $(echo $(echo $(echo $(echo $(echo $({}))))))",
                   "hash command=/tmp/c; {}", "hash exec=/tmp/e; {}", "(IFS=:); {}", "(OSTYPE=msys); {}",
                   "command() {{ :; }}; {}", "alias command=x; eval '{}'", "trap 'cd /' EXIT; {}")
SHADOW_CALLS = ("grep -rn x .", "grep -v y", "grep -c 'a b' f", "find . -name x", "find src -type f", "rg foo",
                "rg -n 'a|b' src", "pkill -f node", "pkill x", "grep", "find", "rg", "pkill")
# The lines the fast reading takes (analyse_shell_text reads nothing), each shadow call on them once
FAST_LINES = (("grep -rn x .", 1), ("find . -name x", 1), ("rg x", 1), ("pkill x", 1), ("grep a f | grep -v b", 2),
              ("find . -name '*.py' | grep y", 2), ("grep -rn 'def foo' .", 1), ("cat f | grep x", 1),
              ("cd /tmp && rg -n 'a|b' src", 1), ("pkill -f 'node server'", 1), ("grep -c a f; grep -c b f", 2),
              ("git log --oneline | grep fix | head -3", 1), ("rg x; find . -type f; grep -v y f; pkill z", 4))
# ... and lines one of whose calls reads its body in full, one for each of read_shadow's conditions
FULL_LINES = ("eval 'grep a f'", "while true; do grep x f; done", "for f in a b; do grep x f; done", "f() { grep x f; }; f",
              "source f; grep a f", "f(){ :; }; grep a f", "hash command=/tmp/c; grep a f", "alias g=grep; grep a f",
              "setopt x; grep a f", "echo $(echo $(echo $(echo $(echo $(echo $(grep a f))))))", "PATH=/x:$PATH; grep a f",
              "grep -rn $X .", "grep x *.py", "find . -exec grep x {} \\;", "grep '$1' f", "grep '$HOME' f", "grep _cc_bin f",
              "find . -name x -delete", "CLAUDE_CODE_EXECPATH=/tmp/x.sh grep a f", "IFS=:; grep a f", "OSTYPE=msys; grep a f",
              "(IFS=:); grep a f", "typeset -i ARGV0; grep a f", ": ${OSTYPE:=x}; grep a f")


def canonical(value, marks):
    """A value of the analysis with every bare object() in it -- a reading's mark, made anew for each reading -- named by
    the order it first appears in, so two readings of one line compare equal wherever they differ only in the marks."""
    if type(value) is object:
        return marks.setdefault(id(value), "mark %d" % len(marks))
    if isinstance(value, dict):
        return {canonical(k, marks): canonical(v, marks) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return frozenset(canonical(v, marks) for v in value)
    if isinstance(value, (list, tuple)):
        return type(value)(canonical(v, marks) for v in value)
    return value


class HarnessShadowReadingTest(ShellSnapshotCase):
    """SPD-247 (proposal 349 by SPUD-134/Billie): Claude Code writes its own grep, find, rg and pkill into every shell
    snapshot, in a fixed shape (THIS_MACS_SHADOWS), and each hands the call's words on through `${1+"$@"}`, so SPD-134's judge
    of inert bodies could never pass them over: each call's body was read in full, at 0.8 to 1.5 ms of the Bash hook a call.
    held_shadows.read_shadow now records for a body that is byte for byte the harness's (hooks/snapshots.harness_shadow) what
    the full reading of it records, without reading it, and reads any other text, or a call whose line the fixed record
    does not fit, in full.

    The proof is here: every field of the analysis and the hook's answer, for member and Spud, are the full reading's --
    forced by making read_shadow record nothing (SPD-290: no longer by making harness_shadow recognize nothing, which
    reads the body as any other function's, parsed before the snapshot's aliases) -- on each shape and every kind of
    word and line the reading tells apart, the CLAUDE_CODE_EXECPATH lines of SPD-253 and SPD-258 among them; a changed byte is read in full; and proposal
    348's leak (a shadow's locals counted as the line's), fixed by SPD-246, stays fixed on both readings.  The fixed record
    was measured on the reader as it stands: a change to it that moves what a harness body records fails here, and
    held_shadows._SHADOW_READINGS is measured again.  AGENT_A and AGENT_B plan home:tests/** and home:bin/spud; the home is the cwd."""

    def setUp(self):
        super().setUp()
        path = self.write_snapshot("snapshot-zsh-1700000000247-247247.sh", THIS_MACS_SHADOWS)
        newest = path.stat().st_mtime + 60  # newer than SHELL_SNAPSHOT, whose one-line grep it replaces
        os.utime(path, (newest, newest))
        self.m = load_spud_module()
        self.held_text = importlib.import_module("spudlib.shell.held_text")
        self.held_shadows = importlib.import_module("spudlib.shell.held_shadows")

    def full(self):
        """The full reading, forced: read_shadow records no body, so each is read in full, as one of the harness's shadows
        -- which the snapshot defines after its aliases, unlike any other function (held_text.read_body, SPD-290)."""
        return mock.patch("spudlib.shell.held_shadows.read_shadow", return_value=False)

    def counted(self):
        """analyse_shell_text, counted: once for each body read in full."""
        return mock.patch("spudlib.shell.held_text.analyse_shell_text", wraps=self.held_text.analyse_shell_text)

    def spied(self):
        """read_shadow, spied: `self.fast` holds its answer for each call it was asked about, True where it recorded the
        body without reading it."""
        real, self.fast = self.held_shadows.read_shadow, []

        def spy(*args):
            self.fast.append(real(*args))
            return self.fast[-1]
        return mock.patch("spudlib.shell.held_shadows.read_shadow", side_effect=spy)

    def analysis(self, command):
        env = dict(os.environ)
        env["SPUD_USER_CLAUDE_DIR"] = self.home.env["SPUD_USER_CLAUDE_DIR"]
        with mock.patch.dict(os.environ, env, clear=True):
            return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def reading(self, command):
        marks = {}
        return {name: canonical(value, marks) for name, value in vars(self.analysis(command)).items()}

    def bodies(self):
        env = dict(os.environ)
        env["SPUD_USER_CLAUDE_DIR"] = self.home.env["SPUD_USER_CLAUDE_DIR"]
        with mock.patch.dict(os.environ, env, clear=True):
            table = self.m.shell_table(str(self.home.path))
            return {name: table.body(name) for name in SHADOWS}

    def corpus(self):
        lines = [name for name in SHADOWS] + ["%s %s" % (name, w) for name in SHADOWS for w in SHADOW_WORDS]
        lines += [context.format(call, call.replace("x", "y")) for context in SHADOW_CONTEXTS for call in SHADOW_CALLS[:10]]
        lines += [one + sep + two for one in SHADOW_CALLS[::2] for two in SHADOW_CALLS[1::2] for sep in (" | ", "; ")]
        return lines + ["; ".join("grep %d f" % i for i in range(10)), "grep a f; grep a f"]

    def test_the_harness_s_text_is_recognized_shape_by_shape(self):
        bodies = self.bodies()
        for name in SHADOWS:
            with self.subTest(name=name):
                self.assertEqual(self.m.harness_shadow(name, bodies[name]), "" if name == "pkill" else SCRATCH_CLAUDE)
                self.assertIsNone(self.m.harness_shadow("egrep", bodies[name]))  # the text of one shadow is no other's
        # SHELL_SNAPSHOT's one-line grep, and HARNESS_SHADOWS's pkill with `--` for the harness's dash, are other text
        self.assertIsNone(self.m.harness_shadow("grep", '  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"\n'
                                                        '  ARGV0=ugrep "$_cc_bin" ${1+"$@"}\n'))
        self.assertIsNone(self.m.harness_shadow("pkill", bodies["pkill"].replace("—", "--")))

    def test_a_changed_byte_is_read_in_full(self):
        """One byte changed anywhere outside the claude path -- or in it, where the path is then no absolute path to a file
        named claude -- and the body is no harness shadow: it is read in full, which the fast reading then equals."""
        bodies = self.bodies()
        for name, body in bodies.items():
            path = body.find(SCRATCH_CLAUDE)
            for at in sorted({0, 1, len(body) // 3, len(body) // 2, body.find('${1+"$@"}') + 2, len(body) - 3, len(body) - 1}):
                if 0 <= path <= at < path + len(SCRATCH_CLAUDE):
                    continue
                changed = body[:at] + ("X" if body[at] != "X" else "Y") + body[at + 1 :]
                with self.subTest(name=name, at=at):
                    self.assertIsNone(self.m.harness_shadow(name, changed))
        for spelled in ("/Users/Someone/.local/bin/claudex", "Users/Someone/.local/bin/claude", "/Users/Some one/claude",
                        "$HOME/.local/bin/claude", "/Users/Someone/.local/bin/claude;x", "~/.local/bin/claude"):
            with self.subTest(spelled=spelled):
                self.assertIsNone(self.m.harness_shadow("rg", bodies["rg"].replace(SCRATCH_CLAUDE, spelled)))
        # ... and a line calling one read in full: a snapshot newer than the fixture's, one byte changed in each shadow
        for name, old, new in (("find", "-S dfs", "-S bfs"), ("grep", " -G ", " -E "), ("rg", "ARGV0=rg", "ARGV0=rG"),
                               ("pkill", "_cc_skip=1", "_cc_skip=2")):
            with self.subTest(name=name):
                text = THIS_MACS_SHADOWS.replace(old, new, 1)
                self.assertEqual(len(text), len(THIS_MACS_SHADOWS))
                path = self.write_snapshot("snapshot-zsh-1700000000248-%s.sh" % name, text)
                newest = time.time() + 120 + SHADOWS.index(name)
                os.utime(path, (newest, newest))
                forget_process_caches()
                self.assertIsNone(self.m.harness_shadow(name, self.bodies()[name]))
                line = "%s -rn x ." % name
                with self.full():
                    full = self.reading(line)
                with self.counted() as read, self.spied():
                    self.assertEqual(self.reading(line), full)
                self.assertEqual((read.call_count, self.fast), (1, []))

    def test_the_fast_reading_records_what_the_full_reading_records(self):
        """Every field of the analysis, on every line of the corpus: each shape with each kind of word, in each kind of line,
        and the shapes in pairs.  The text a call's words are set in is among them (bodies_read), so shadow_text is
        positional.substitution's wherever the reading ran."""
        fast = 0
        for line in self.corpus():
            with self.subTest(line=line):
                with self.full():
                    full = self.reading(line)
                with self.spied():
                    self.assertEqual(self.reading(line), full)
                fast += True in self.fast
        self.assertGreater(fast, 200)  # the fast reading ran on these lines, not only the fallback

    def test_the_fast_reading_runs_where_the_record_fits(self):
        for line, calls in FAST_LINES:
            with self.subTest(line=line):
                with self.counted() as read, self.spied():
                    self.analysis(line)
                self.assertEqual((read.call_count, self.fast), (0, [True] * calls))
                with self.full(), self.counted() as read:
                    self.analysis(line)
                self.assertEqual(read.call_count, calls)
        for line in FULL_LINES:
            with self.subTest(line=line), self.spied():
                self.analysis(line)
                self.assertIn(False, self.fast)
                self.assertNotIn(True, self.fast)

    def assertReadInFull(self, line, in_full=True):
        """The line records what the full reading forced on it records, and no shadow's body is recorded without being
        read (`in_full`), or one is."""
        with self.full():
            full = self.reading(line)
        with self.spied():
            self.assertEqual(self.reading(line), full)
        self.assertEqual(True not in self.fast, in_full, self.fast)

    def with_profile(self, text, number):
        """A snapshot of the profile's own, older than the harness's shadows, so its names are the table's where the
        shadows' snapshot names none of them; the one before it removed."""
        for old in self.snapshots.glob("snapshot-zsh-1600000000000-*.sh"):
            old.unlink()
        path = self.write_snapshot("snapshot-zsh-1600000000000-%06d.sh" % number, text)
        os.utime(path, (1600000000, 1600000000))
        forget_process_caches()

    def test_a_shadow_inside_the_text_the_shell_holds_is_read_in_full(self):
        """A profile function or alias that runs grep: the call is read inside that text, whose prune is not the one the
        fixed record was measured under (analyse_shell_text's outermost reading)."""
        self.with_profile("# Functions\nlsgrep () {\n\tls | grep \"$1\"\n}\n# Aliases\nalias -- gr='grep -n'\n", 1)
        for line in ("lsgrep x", "gr x f", "gr -v y f | lsgrep z", "ls | gr x"):
            with self.subTest(line=line):
                self.assertReadInFull(line)

    def test_a_profile_that_holds_a_word_the_bodies_look_up_is_read_in_full(self):
        """Any of the words a shadow's body looks up (held_shadows._SHADOW_LOOKUPS, and the claude binary where the body runs
        it), defined by the profile, is read as the profile defines it, so the body is read in full."""
        for number, word in enumerate(("local", "[[", "[", "command", "return", "exec", "printf", "continue", SCRATCH_CLAUDE)):
            self.with_profile("# Functions\n%s () {\n\t:\n}\n" % word, number)
            for line in ("grep -rn x .", "pkill x", "find . -name x", "rg x"):
                with self.subTest(word=word, line=line):
                    self.assertReadInFull(line, in_full=(word, line) != (SCRATCH_CLAUDE, "pkill x"))

    def test_a_claude_that_is_a_spud_launcher_is_read_in_full(self):
        """The binary is run by its path, so a link named claude to bin/spud is a spud call, which the fixed record is not."""
        link = self.home.path / "linked" / "claude"
        link.parent.mkdir()
        link.symlink_to(self.home.launcher)
        path = self.write_snapshot("snapshot-zsh-1700000000249-249249.sh", THIS_MACS_SHADOWS.replace(SCRATCH_CLAUDE, str(link)))
        newest = time.time() + 300
        os.utime(path, (newest, newest))
        forget_process_caches()
        self.assertEqual(self.m.harness_shadow("grep", self.bodies()["grep"]), str(link))
        for line in ("grep -rn x .", "find . -name x", "rg x"):
            with self.subTest(line=line):
                self.assertReadInFull(line)

    def test_the_words_are_set_as_positional_sets_them(self):
        bodies, positional = self.bodies(), importlib.import_module("spudlib.shell.positional")
        for line in ["%s %s" % (name, w) for name in SHADOWS for w in SHADOW_WORDS] + ["grep 'a b' '' c", "rg"]:
            for name, readings in self.analysis(line).bodies_read.items():
                for text, words, _ in readings:
                    with self.subTest(line=line):
                        self.assertEqual(positional.substitution(bodies[name], list(words)), (text, True, ()))
                        self.assertEqual(self.m.shadow_text(bodies[name], list(words)), text)

    def test_the_hook_answers_as_the_full_reading_does(self):
        """Member and Spud, allowed or refused, and the reason word for word."""
        lines = ("grep -rn x .", "find . -name x", "rg x", "pkill x", "grep -rn 'def foo' . | head", "find . -name '*.py' | grep y",
                 "grep -c a f; grep -c b f", "cat f | grep x > out.txt", "grep -rn x . > ledger/Home.md", "find . -type f -name x",
                 "rg -n --color=never foo src", "pkill -f 'node server'", "x=status; grep -rn x .; git $x",
                 "grep x f; git push", "find . -exec git push \\;", "grep -rn x .; spud board",
                 "CLAUDE_CODE_EXECPATH=/tmp/x.sh grep a f", "CLAUDE_CODE_EXECPATH=$(echo /tmp/x.sh) grep a f",
                 "export CLAUDE_CODE_EXECPATH=/tmp/x.sh; grep a f", "X=/tmp/x.sh; CLAUDE_CODE_EXECPATH=$X grep a f",
                 "CLAUDE_CODE_EXECPATH=/tmp/x.sh find . -name x", "CLAUDE_CODE_EXECPATH=/tmp/x.sh rg x",
                 "grep a f | CLAUDE_CODE_EXECPATH=/tmp/x.sh grep b", "CLAUDE_CODE_EXECPATH=/tmp/x.sh pkill x")
        for line in lines:
            for caller in (AGENT_A, None):
                with self.subTest(line=line, caller=caller):
                    with self.full():
                        full = self.bash(line, caller)
                    fast = self.bash(line, caller)
                    self.assertEqual((fast.code, fast.stdout, fast.stderr), (full.code, full.stdout, full.stderr))

    def test_the_execpath_lines_stay_refused(self):
        """SPD-253 and SPD-258: a line that sets CLAUDE_CODE_EXECPATH before a shadow runs the member's own file where
        claude would run; the line holds the name the body reads, so the body is read in full, which refuses it."""
        for line in ("CLAUDE_CODE_EXECPATH=/tmp/x.sh grep a f", "CLAUDE_CODE_EXECPATH=$(echo /tmp/x.sh) grep a f",
                     "export CLAUDE_CODE_EXECPATH=/tmp/x.sh; grep a f", "X=/tmp/x.sh; CLAUDE_CODE_EXECPATH=$X grep a f",
                     "CLAUDE_CODE_EXECPATH=/tmp/x.sh find . -name x", "CLAUDE_CODE_EXECPATH=/tmp/x.sh rg x",
                     "grep a f | CLAUDE_CODE_EXECPATH=/tmp/x.sh grep b"):
            with self.subTest(line=line):
                with self.spied():
                    self.analysis(line)
                self.assertEqual(self.fast[-1], False)  # the call CLAUDE_CODE_EXECPATH is set for
                self.refused_for_members(line, "$_cc_bin")
                self.assertSilent(line, agent_id=None)

    def test_proposal_348_a_shadow_s_locals_are_not_the_line_s(self):
        """SPD-246 fixed it: the first shadow's own `_cc_bin` counted as the line's variable, and the second one's
        `"$_cc_bin"` was kept as the member's, refusing every member these lines.  On both readings no shadow leaves its
        locals among the line's variables, and the lines pass."""
        for reading in ("fast", "full"):
            with self.full() if reading == "full" else contextlib.nullcontext():
                for line in ("find . -name x | grep y", "grep a f | grep -v b", "grep -c a f; grep -c b f", "rg x | grep y",
                             "grep a f; rg b; find . -name c", "pkill -f x; grep a f"):
                    with self.subTest(reading=reading, line=line):
                        a = self.analysis(line)
                        self.assertNotIn(("var-doubt", "$_cc_bin"), a.findings)
                        self.assertTrue({"_cc_bin", "_cc_a"}.isdisjoint(a.line_assigned), a.line_assigned)
                        self.assertNotIn("_cc_bin", a.vars)
                        self.silent_for_everyone(line)

    def test_269_a_loop_over_the_call_s_words_leaves_the_line_s_variables(self):
        """SPD-269 (proposal 371 by SPUD-247/Garfield): grep's and pkill's bodies loop over the call's words (`for _cc_a in
        ${1+"$@"}`), and ShellWalk.finish doubted every name a word of a for header spelled, the list's words among them,
        so a line variable a word of the call happened to spell was doubted after it: `f=note.txt; grep -c f file; echo hi
        > $f` left `$f` unresolved, refused to every caller (SPD-091), where `ls f` in grep's place wrote note.txt.  The
        header assigns `_cc_a` alone now, on the fast reading and the full one, and the write is held to the path rule as
        the control's is: refused outside a member's deliverables, allowed inside them."""
        for call in ("grep -c f file", "grep -rn f .", "grep -e f -e in file", "pkill -f f", "pkill f"):
            for target in ("note.txt", "tests/k.py"):
                line = "f=%s; %s; echo hi > $f" % (target, call)
                control = "f=%s; ls f; echo hi > $f" % target
                for reading in ("fast", "full"):
                    with self.subTest(line=line, reading=reading), \
                            self.full() if reading == "full" else contextlib.nullcontext():
                        a = self.analysis(line)
                        self.assertEqual([t for t, _c in a.redirects if t != "/dev/null"], [target])
                        self.assertTrue({"f", "in"}.isdisjoint(a.doubt), a.doubt)
                        for caller in (AGENT_A, AGENT_B, None):
                            # the answer, less the note naming what the snapshot defines the command word as
                            got, want = [(r.code, r.stdout.split("  (The shell this command")[0], r.stderr)
                                         for r in (self.bash(line, caller), self.bash(control, caller))]
                            self.assertEqual(got, want)
        self.assertRefused("f=note.txt; grep -c f file; echo hi > $f", "deliverables")
        self.assertRefused("f=note.txt; pkill -f f; echo hi > $f", "deliverables", AGENT_B)
        self.assertSilent("f=tests/k.py; grep -c f file; echo hi > $f")
        self.assertSilent("f=tests/k.py; pkill -f f; echo hi > $f", AGENT_B)


# SPD-283: a snapshot whose alias block keeps each alias's kind, as `alias -L` prints one (`alias -g GL=...`, and `alias
# -s txt=...` with `-s`).  Claude Code's own snapshots hold neither kind: they list zsh's plain `alias`, which prints a
# global alias with no flag and a suffix alias not at all (hooks/snapshots.ALIAS_RE).  The functions come first, as the
# snapshot writes them, before the aliases.
HELD_WORD_ALIASES = """\
# Snapshot file
# Unset all aliases to avoid conflicts with functions
unalias -a 2>/dev/null || true
# Functions
pushfn () {
\techo in-f GP
}
runtxt () {
\tnotes.txt
}
evalfn () {
\teval "$1"
}
# Shell Options
setopt autocd
# Aliases
alias -- ls='ls -G'
alias -g -- GP='; git push'
alias -g GS='| git status'
alias -g -- NUL='> /dev/null 2>&1'
alias -g -- GW='$(git push)'
alias -g -- GC='git push'
alias -s -- txt='git push'
alias -s md='git status'
alias -- pa='echo pa GP'
alias -g -- BROKEN='git push
alias -s -- cfg='git push
"""
# The reason a member earns where a global or a suffix alias the line's text stands in may run and the hook cannot
# resolve it (bash_rule's unread form "alias-word", SPD-109), and where one the shell holds has a body it cannot read.
ALIAS_WORD_WORDING = "global or suffix alias"
HELD_BODY_WORDING = "whose body the hook cannot read"


class HeldWordAliasTest(ShellSnapshotCase):
    """SPD-283 (SPD-109's proposal): the shell's snapshot may hold a global alias (`alias -g`), which zsh expands in every
    word it reads unquoted, and a suffix alias (`alias -s`), which runs its body before a command word ending in its
    suffix.  The table read a `-g` line as a plain alias, reached only in command position, and dropped every `-s` line,
    so on the member's own line `echo hi GP` (GP='; git push') and `a.txt` (txt='git push') pushed past Law 7.  Each kind
    now has a table of its own (Table.galiases, Table.saliases, cache format 3), and a line starts from them
    (line_aliases.held_aliases).  No snapshot Claude Code writes today holds either kind (hooks/snapshots.ALIAS_RE: its
    alias block is zsh's plain `alias` listing), so this reads a writer that keeps the flag, as `alias -L` does.

    Probed in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py, a fresh `zsh -f -c` sourcing such a
    snapshot (`unalias -a`, functions, then `alias -g X=snapshot`, `alias -s txt='echo SUFFIX'`) and evaluating the line, as
    the Bash tool's shell does (bash 3.2 has neither kind):

    - the line's own words: `echo hi X` printed `hi snapshot`, `a.txt top` ran the suffix alias, and so did every place
      SPD-109 probed in eval's text, `nocorrect a.txt` and `time a.txt`, `(a.txt)`, a pipe's element and a glob's word
      (`a?.txt`); `noglob`, `command`, `builtin`, `env` and `-` before it ran none, nor did `$F` holding a.txt;
    - the line parses its own text before any of it runs: after `alias -g X=line`, `alias -s txt=...` or `unalias -s txt`
      on the same line, its own X and a.txt still ran the snapshot's, and so did an alias body read in it (`unalias 'X';
      pa`, pa='echo pa X', printed `pa snapshot`); `unalias X` had its own X expanded ("no such hash table element:
      snapshot");
    - text parsed as the line runs -- eval's, a `$( )` body's -- after the line's change: `alias -g X=line; echo sub $(echo
      X)` printed `sub line`, `unalias 'X'; eval "echo hi X"` printed `hi X`, `alias X=plain` made X plain, `unalias -a`
      cleared X and left txt, `unalias -s txt` cleared txt, and `alias -s txt="echo LINE"` replaced it;
    - a function the snapshot defines before its aliases expands none (`f() { echo in-f X; }` printed `in-f X`, `g() {
      a.txt; }` found no command a.txt), and a new shell holds none.

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud."""

    def setUp(self):
        super().setUp()
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", HELD_WORD_ALIASES)
        self.m = load_spud_module()

    def refused_for_members(self, command, needle="Law 7", cwd=None):
        r = super().refused_for_members(command, needle, cwd)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None, cwd=cwd)
        return r

    def analysis(self, command):
        env = dict(os.environ)
        env["SPUD_USER_CLAUDE_DIR"] = self.home.env["SPUD_USER_CLAUDE_DIR"]
        with mock.patch.dict(os.environ, env, clear=True):
            forget_process_caches()  # the table this process read before a snapshot the test wrote since
            return self.m.analyse_command(command, self.m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def test_the_table_reads_each_kind_apart(self):
        built = self.m.build_table([str(self.snapshots / "snapshot-zsh-1700000000000-aaaaaa.sh")])
        self.assertEqual(built.aliases, {"ls": "ls -G", "pa": "echo pa GP"})
        self.assertEqual(built.galiases, {"GP": "; git push", "GS": "| git status", "NUL": "> /dev/null 2>&1",
                                          "GW": "$(git push)", "GC": "git push", "BROKEN": None})
        self.assertEqual(built.saliases, {"txt": "git push", "md": "git status", "cfg": None})
        self.assertEqual(sorted(built.functions), ["evalfn", "pushfn", "runtxt"])

    def test_an_alias_line_names_its_kind(self):
        definition = self.m.alias_definition
        self.assertEqual(definition("alias -- gp='git push'"), ("plain", "gp", "git push"))
        self.assertEqual(definition("alias -r -- gp=x"), ("plain", "gp", "x"))
        self.assertEqual(definition("alias -g GL='| cat'"), ("global", "GL", "| cat"))  # `alias -L`'s spelling
        self.assertEqual(definition("alias -g -- GL='| cat'"), ("global", "GL", "| cat"))
        self.assertEqual(definition("alias -s txt='echo SUF'"), ("suffix", "txt", "echo SUF"))
        self.assertEqual(definition("alias -s -- cfg='git push"), ("suffix", "cfg", None))  # quoting that never closes
        self.assertIsNone(definition("alias -gs q=Q"))  # "illegal combination of options": zsh defines nothing (probed)
        self.assertIsNone(definition("alias -g GL"))  # a query

    def test_a_snapshot_is_read_in_line_order_each_kind_in_its_table(self):
        """A plain and a global alias share zsh's one table, so the later replaces the earlier; a suffix alias has a table
        of its own, which `unalias -a` leaves alone and `unalias -s` clears (probed)."""
        text = ("alias -g X=g1\nalias X=p1\nalias Y=p2\nalias -g Y=g2\nalias -s txt=s1\nalias txt=p3\n"
                "alias -g Z=g3\nunalias Z\nalias -s cfg=s3\nunalias -s cfg\n")
        aliases, _, named, _ = self.m.read_snapshot(text.encode(), 0)
        self.assertEqual(aliases, {"plain": {"X": "p1", "txt": "p3"}, "global": {"Y": "g2"}, "suffix": {"txt": "s1"}})
        self.assertEqual(set(named), {"X", "Y", "txt", "Z", ("suffix", "txt"), ("suffix", "cfg")})
        aliases, _, _, _ = self.m.read_snapshot(b"alias -g G=g\nalias p=q\nalias -s md=s\nunalias -a\n", 0)
        self.assertEqual(aliases, {"plain": {}, "global": {}, "suffix": {"md": "s"}})
        aliases, _, _, _ = self.m.read_snapshot(b"alias -g G=g\nalias -s md=s\nalias -s ini=t\nunalias -as\n", 0)
        self.assertEqual(aliases, {"plain": {}, "global": {"G": "g"}, "suffix": {}})

    def test_the_newest_snapshot_that_names_one_decides_it(self):
        older = ("alias -g -- GP='; git status'\nalias -g -- OLD='; git push'\nalias -s -- ini='git push'\n"
                 "alias -s -- md='git push'\nalias -s -- gone='git push'\n")
        self.write_snapshot("snapshot-zsh-1600000000000-bbbbbb.sh", older, mtime=1600000000)
        self.write_snapshot("snapshot-zsh-1800000000000-cccccc.sh", "unalias -s gone\n", mtime=1800000000)
        os.utime(self.snapshots / "snapshot-zsh-1700000000000-aaaaaa.sh", (1700000000, 1700000000))
        self.refused_for_members("echo hi GP")  # the newer file's `; git push`
        self.refused_for_members("echo hi OLD")  # named by the older file alone
        self.refused_for_members("a.ini")
        for line in ("a.md", "a.gone"):  # the newer file's `git status`; a suffix the newest cleared
            self.silent_for_everyone(line)

    def test_a_global_alias_in_every_word_of_the_line(self):
        for line in ("echo hi GP", "echo hi GP; echo after", "true && echo GP", "(echo GP)", "{ echo GP; }",
                     "echo a | cat GP", "echo $(echo GP)", "echo `echo GP`", "cat <(echo GP)", "f() { echo GP; }; f",
                     "if true; then echo GP; fi", "for i in a; do echo GP; done", "eval 'echo GP'", 'eval "echo GP"',
                     "evalfn 'echo GP'", "GC origin", "sudo echo GP", "pa", "[[ GW == x ]]", "case GW in x) :;; esac",
                     "for i in GW; do :; done", "arr=(a GW b)", "X=1 echo GP"):
            with self.subTest(line):
                self.refused_for_members(line)
        for line in ("echo hi > GW", "echo hi 2>GW"):  # a target a substitution names refuses Spud too, spelled out
            with self.subTest(line):
                for agent_id in (AGENT_A, AGENT_B):
                    self.assertRefused(line, "Law 7", agent_id)
                self.assertRefused(line, "", agent_id=None)
        self.assertEqual(self.analysis("echo hi GP").findings, [("git", ("push", "push"))])
        self.assertEqual(self.analysis("echo hi GS").findings, [("git", ("status", None))])
        self.silent_for_everyone("ls NUL")

    def test_a_suffix_alias_on_a_command_word_of_the_line(self):
        for line in ("a.txt", "a.txt x y", "./d/a.txt", "b.a.txt", "X=1 a.txt", "true; a.txt", "echo x | a.txt",
                     "(a.txt)", "{ a.txt; }", "if true; then a.txt; fi", "nocorrect a.txt", "time a.txt",
                     "echo $(a.txt)", "eval a.txt", "a?.txt", "echo G | a.txt"):
            with self.subTest(line):
                self.refused_for_members(line)
        self.assertEqual(self.analysis("x.md").findings, [("git", ("status", None))])
        self.silent_for_everyone("x.md")

    def test_where_no_alias_stands(self):
        """Quoted, escaped or glued to other text, in an assignment's value, a `${ }`, arithmetic or a comment; a suffix
        alias's word as an argument or behind a wrapper that takes the command position; a word an expansion gave; the
        text a new shell reads; and a function the snapshot defines, whose body it parsed before its aliases."""
        for line in ("echo hi 'GP'", 'echo hi "GP"', "echo hi \\GP", "echo hi G\\P", "echo GPX XGP", "X=GP",
                     "echo ${GP}", "echo x # GP", "echo X=GP", "(( GP == 1 ))", "echo a.txt", "cat a.txt",
                     "command a.txt", "noglob a.txt", "env a.txt", "builtin a.txt", "- a.txt", "a.TXT", ".txt", "txt",
                     "F=a.txt; $F", "sh -c 'echo GP'", "sh -c a.txt", "zsh -c 'echo GP; a.txt'", "pushfn",
                     "eval pushfn", "x=$(pushfn)", "runtxt", "eval runtxt"):
            with self.subTest(line):
                self.silent_for_everyone(line)

    def test_the_lines_own_change_stands_in_the_text_the_shell_parses_after_it(self):
        """The line's own `alias -g`, `alias -s`, plain `alias` of the name or `unalias` reaches eval's text and a
        substitution's body after it, never the line's own words or an alias body read in them, which the shell parsed
        first (probed)."""
        for line in ("unalias 'GP'; eval 'echo hi GP'", "unalias \\GP; eval 'echo hi GP'",
                     "alias -g GP=ls; eval 'echo hi GP'", "alias GP=ls; eval 'echo hi GP'",
                     "unalias -a; eval 'echo hi GP'", "unalias -s txt; eval a.txt", "alias -s txt='git status'; eval a.txt",
                     "unalias 'GP'; echo $(echo GP)", "alias -s txt=ls; echo $(a.txt)", "unalias -s txt; echo $(a.txt)"):
            with self.subTest(line):
                self.silent_for_everyone(line)
        for line in ("unalias -a; eval a.txt",  # `unalias -a` clears no suffix alias
                     "unalias GP; eval 'echo hi'",  # its own GP is expanded: `unalias ; git push`
                     "unalias 'GP'; echo hi GP", "alias -g GP=ls; echo hi GP", "unalias -s txt; a.txt",
                     "alias -s txt=ls; a.txt", "unalias 'GP'; pa", "alias -g GP=ls; pa"):
            with self.subTest(line):
                self.refused_for_members(line)

    def test_a_change_that_may_not_have_run_is_refused_a_member(self):
        for line in ("if true; then unalias 'GS'; fi; eval 'echo GS'", "(unalias -s md); eval a.md",
                     "true || alias -s md=ls; eval x.md", "X=GP; eval echo $X", "X=a.md; eval $X"):
            with self.subTest(line):
                self.refused_for_members(line, ALIAS_WORD_WORDING)

    def test_a_body_the_hook_cannot_read_is_refused_a_member(self):
        for line in ("echo BROKEN", "echo $(echo BROKEN)", "eval 'echo BROKEN'", "a.cfg", "eval a.cfg", "x=$(b.cfg)"):
            with self.subTest(line):
                r = self.refused_for_members(line, HELD_BODY_WORDING)
                self.assertIn("shell-snapshots", r.reason)

    def test_the_reason_names_what_the_shell_defined(self):
        r = self.assertRefused("echo hi GP", "Law 7")
        self.assertIn("`GP` as a global alias for `; git push`", r.reason)
        r = self.assertRefused("a.txt", "Law 7")
        self.assertIn("`a.txt` as a suffix alias for `git push`", r.reason)

    def test_the_harness_shadows_still_read_silent(self):
        """A line whose table holds the shell's own aliases takes the full reading of the harness's grep, find, rg and
        pkill (held_shadows' fast path wants an empty one), which is silent all the same."""
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh",
                            HELD_WORD_ALIASES + HARNESS_SHADOWS[HARNESS_SHADOWS.index("# Shadow find/grep"):])
        for line in ("grep -rn x .", "find . -name x", "rg x", "pkill -f x", "grep a f | grep -v b", "x=$(grep -c a f)"):
            with self.subTest(line):
                self.silent_for_everyone(line)
        self.refused_for_members("grep -rn x . GP")

    def test_a_table_with_neither_kind_puts_nothing_in_the_lines(self):
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", SHELL_SNAPSHOT)
        a = self.analysis("echo $(echo hi); eval 'echo hi'")
        self.assertEqual((a.aliases, a.alias_scope), ({}, 0))
        self.assertEqual(self.analysis("echo hi GP").aliases, {})

    def test_the_cache_holds_each_kind_and_an_older_format_is_rebuilt(self):
        """Format 2 held a global alias among the plain ones and no suffix alias: read as it stands, `echo hi GP` would
        expand nothing and `a.txt` run no alias, so a cache of it is built again, never read."""
        cache = self.home.path / ".spud" / "shell-snapshot.json"
        self.refused_for_members("echo hi GP")
        stored = json.loads(cache.read_text(encoding="utf-8"))
        self.assertEqual(stored["format"], sys.modules["spudlib.hooks.snapshots"].CACHE_FORMAT)
        self.assertEqual((stored["galiases"]["GP"], stored["saliases"]["txt"]), ("; git push", "git push"))
        self.assertNotIn("GP", stored["aliases"])
        old = {k: v for k, v in stored.items() if k not in ("galiases", "saliases")}
        old.update(format=2, aliases=dict(stored["aliases"], **stored["galiases"]))
        for written in (old, {k: v for k, v in stored.items() if k != "saliases"}):
            with self.subTest(written=sorted(written)):
                cache.write_text(json.dumps(written), encoding="utf-8")
                self.refused_for_members("echo hi GP")
                self.refused_for_members("a.txt")
                self.assertEqual(json.loads(cache.read_text(encoding="utf-8")), stored)

    def test_doctor_counts_each_kind(self):
        notes = self.home.json("doctor")["notes"]
        self.assertTrue(any("shell snapshot: 11 aliases" in n and "(6 global and 3 suffix aliases among them)" in n
                            and "BROKEN" in n and "cfg" in n for n in notes), notes)


# SPD-288: snapshot functions whose bodies parse text as they run -- eval of the call's words, and a substitution -- and
# hold no alias of their own; `pz` is no alias the snapshot defines, so only the line's own can make it one.
RUNTIME_PARSING_FUNCTIONS = """\
# Snapshot file
# Unset all aliases to avoid conflicts with functions
unalias -a 2>/dev/null || true
# Functions
evalit () {
\teval "$@"
}
subit () {
\techo sub $(pz)
}
# Shell Options
setopt autocd
# Aliases
alias -- ls='ls -G'
"""


class SnapshotBodyAfterLineAliasTest(ShellSnapshotCase):
    """SPD-288: held_text.read_shell_name reads a snapshot function's body once per call's words, standard input and
    ShellAnalysis.reading_state, and that state left out the line's alias table.  A body the snapshot holds was parsed
    before any alias of the line's, but an eval or a substitution in it is parsed when it runs, with the table the line
    holds then: called once before the line's `alias pz='git push'` and once after it, the second call pushes, and the
    hook had read it from the first call's cache and found nothing.  The table (and alias_unknown, alias_view) are in
    reading_state now, so the second call is read afresh.

    Probed in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py, sourcing a file that defines
    `evalit() { eval "$@"; }` and `subit() { echo sub $(pz); }`, then `eval 'evalit pz; subit; alias pz="echo PZ-RAN";
    evalit pz; subit'`: the first two calls found no command pz, the last two printed `PZ-RAN` and `sub PZ-RAN`.  bash 3.2,
    whose non-interactive shell expands no alias, ran none of them.

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud."""

    def setUp(self):
        super().setUp()
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", RUNTIME_PARSING_FUNCTIONS)

    def test_a_call_after_the_lines_alias_is_read_again(self):
        for line in ("evalit pz; alias pz='git push'; evalit pz", "subit; alias pz='git push'; subit",
                     "evalit pz; subit; alias pz='git push'; evalit pz", "subit; evalit pz; alias pz='git push'; subit",
                     "evalit pz; alias pz='git status'; alias pz='git push'; evalit pz"):
            with self.subTest(line):
                r = self.refused_for_members(line)
                self.assertIn("git push", r.reason)

    def test_the_alias_before_every_call_and_after_every_call(self):
        for line in ("alias pz='git push'; evalit pz", "alias pz='git push'; subit"):
            with self.subTest(line):
                self.refused_for_members(line)
        for line in ("evalit pz; alias pz='git push'", "subit; alias pz='git push'", "evalit pz; subit; evalit pz",
                     "evalit pz; alias pz='git status'; evalit pz", "subit; alias pz='git status'; subit"):
            with self.subTest(line):
                self.silent_for_everyone(line)


# SPD-290: functions written before the aliases, as Claude Code writes its snapshot, whose bodies call a plain alias's
# name, run it through eval or a substitution, or run the call's words; `a.txt` is a plain alias whose name ends in a
# suffix, and PROGRAM_ALIAS renames a program the bodies run.
PLAIN_BEFORE_ALIASES = """\
# Snapshot file
# Unset all aliases to avoid conflicts with functions
unalias -a 2>/dev/null || true
# Functions
callgp () {
\tgp
}
pushit () {
\tgit push
}
evalgp () {
\teval gp
}
subgp () {
\techo sub $(gp)
}
runit () {
\t"$@"
}
# Shell Options
setopt autocd
# Aliases
alias -- a.txt=ls
alias -- gp='git push'
alias -- ls='ls -G'
"""
PROGRAM_ALIAS = "alias -- git=hub\n"


class HeldPlainAliasScopeTest(ShellSnapshotCase):
    """SPD-290: line_aliases.shell_aliased expanded the snapshot's plain aliases at every command word the hook read, but
    zsh expands none in a function body the snapshot defines -- it writes its functions before its aliases, so the body was
    parsed with none -- nor in a new shell's text, which never sources the snapshot.  Reading an alias's body in place of a
    command that runs is a reading of other text, not more: under a profile's `alias git=hub`, a snapshot function's `git
    push` and `sh -c 'git push'` were read as `hub push` and let through.  SPD-283 made the snapshot's global and suffix
    aliases stand only where zsh expands them; the plain ones now do too (line_aliases.held_standing): the line's own
    command words, an alias body read in them, and text the Bash tool's shell parses as it runs -- eval's, a substitution's,
    in the line or in a snapshot body -- and a function body the line defines, parsed with the line.

    Probed in zsh 5.9 -f through tests/probes/shell_probe.py, a fresh `zsh -f -c` sourcing a file that holds `unalias -a`,
    these functions and then the aliases (gp='echo GP-RAN', gt='echo GT-ALIAS', a.txt='echo PLAIN-ATXT'), and evaluating
    the line, as the Bash tool's shell does: `gp` printed GP-RAN, and so did `evalgp` (`eval gp`), `subgp` (`sub GP-RAN`),
    `f() { gp; }; f` and `f() { gp; }; runit f`; `callgp`, `eval callgp`, a body's `gt push`, `runit gp` and `runit gt
    push` found no command gp or gt, and so did `zsh -f -c gp`, `zsh -f -c "eval gp"`, `sh -c gp` and `zsh -f -c "gt
    push"`; `alias -s txt="echo SUFFIX"; eval a.txt` ran the plain alias (PLAIN-ATXT), where `zsh -f -c 'alias -s
    txt="echo SUFFIX"; eval a.txt'`, whose shell holds no plain a.txt, ran the suffix alias.

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud."""

    def setUp(self):
        super().setUp()
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", PLAIN_BEFORE_ALIASES)

    def test_the_lines_words_and_the_text_its_shell_parses_later_expand_them(self):
        for line in ("gp", "eval gp", "echo $(gp)", "evalgp", "subgp", "eval evalgp", "x=$(subgp)", "runit evalgp",
                     "f() { gp; }; f", "f() { gp; }; runit f", "sh -c gp; gp", "sh -c gp; echo $(gp)",
                     "zsh -c 'echo $(gp)'; echo $(gp)", "callgp; eval gp"):
            with self.subTest(line):
                self.refused_for_members(line)

    def test_a_snapshot_functions_own_body_expands_none(self):
        for line in ("callgp", "eval callgp", "echo $(callgp)", "runit gp", "runit callgp", "callgp; callgp"):
            with self.subTest(line):
                self.silent_for_everyone(line)

    def test_a_new_shells_text_expands_none(self):
        for line in ("sh -c gp", "zsh -c gp", "bash -c gp", "zsh -c 'eval gp'", "sh -c 'echo $(gp)'", "eval 'sh -c gp'",
                     "zsh -c 'f() { gp; }; f'", "echo gp | sh", "sh <<< gp", "env sh -c gp"):
            with self.subTest(line):
                self.silent_for_everyone(line)

    def test_a_program_the_profile_aliases_runs_as_itself_where_the_alias_does_not_stand(self):
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", PLAIN_BEFORE_ALIASES + PROGRAM_ALIAS)
        for line in ("pushit", "eval pushit", "echo $(pushit)", "runit git push", "sh -c 'git push'",
                     "zsh -c 'git push'", "bash -c 'git push'", "zsh -c 'eval git push'"):
            with self.subTest(line):
                self.refused_for_members(line)

    def test_the_harness_shadows_written_after_the_aliases_expand_them(self):
        """The harness's grep, written after the aliases (`unalias grep`, then `function grep`), was parsed with them, so a
        profile's alias of a word its body runs stands there -- `command grep ...` runs `git push grep ...` -- where a
        function of the profile's own, written before them, runs the builtin."""
        profile = PLAIN_BEFORE_ALIASES.replace("runit () {", "mygrep () {\n\tcommand grep \"$@\"\n}\nrunit () {")
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh",
                            profile + "alias -- command='git push'\n" + THIS_MACS_SHADOWS)
        self.refused_for_members("grep -rn x .")
        self.silent_for_everyone("mygrep -rn x .")

    def test_a_new_shells_suffix_alias_is_no_longer_hidden_by_the_snapshots_plain_one(self):
        self.refused_for_members("zsh -c 'alias -s txt=\"git push\"; eval a.txt'")
        self.silent_for_everyone("alias -s txt='git push'; eval a.txt")  # the snapshot's plain a.txt runs ls there


# SPD-298: functions a profile defines -- one named for a program the hook reads, ones no program is named for, one
# that moves the directory and one that sets a variable -- which a new shell sources none of.
NEW_SHELL_FUNCTIONS = """\
# Snapshot file
# Unset all aliases to avoid conflicts with functions
unalias -a 2>/dev/null || true
# Functions
git () {
\thub "$@"
}
pushit () {
\tgit push
}
toledger () {
\techo hi > ledger/Home.md
}
intests () {
\tcd tests
}
setkept () {
\tKEPT=tests/kept.txt
}
# Shell Options
setopt autocd
# Aliases
alias -- ls='ls -G'
"""


class NewShellFunctionTest(ShellSnapshotCase):
    """SPD-298: held_text.read_shell_name read the snapshot's functions, and the harness's shadows, at every command word,
    a new shell's text included -- but a new shell (`sh -c`, `zsh -c`, `bash -c`, a shell fed its text on standard input)
    never sources the snapshot, so it runs the program of that name, or finds no command.  SPD-290 made the snapshot's
    plain aliases stand only where it is sourced; its functions now do too.  Reading the body there reads other text: a
    body's `cd tests` moved the new shell's reading into tests/, so `sh -c 'intests; echo hi > kept.txt'`, which writes
    ./kept.txt outside a member's deliverables, was read as a write to tests/kept.txt, inside them, and let through; a
    body's assignment set a variable the new shell never has; and a body's git push or ledger write refused `sh -c
    pushit`, which runs nothing.  A snapshot function named for a program the hook reads (`git () { hub "$@"; }`) hid
    nothing, since the dispatch reads the program after a function's body anyway; that reading stays.

    Probed through tests/probes/shell_probe.py in zsh 5.9 -f -o nobareglobqual and -f and bash 3.2.57, after sourcing a
    file that defines pushit (`echo PUSHIT-RAN`), intests (`cd tests`) and grep (`echo GREP-FN`): the shell's own `eval`
    ran all three and moved into tests/, while `sh -c pushit`, `zsh -f -c pushit`, `zsh -c pushit`, `bash -c pushit`,
    `echo pushit | sh`, `zsh -f -c 'eval pushit; echo $(pushit)'` and `sh -c 'intests; pwd'` found no command and left
    the directory where it was, and `sh -c 'grep -c x /dev/null'` ran the program.

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud; the home is the cwd."""

    def setUp(self):
        super().setUp()
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", NEW_SHELL_FUNCTIONS)

    def test_the_tickets_evidence(self):
        """A body's `cd tests` no longer moves the new shell's reading: its write lands in the cwd, outside the
        member's deliverables, where the Bash tool's own shell, which runs the function, writes tests/kept.txt."""
        text = "intests; echo hi > kept.txt"
        for line in ("sh -c '%s'" % text, "zsh -c '%s'" % text, "bash -c '%s'" % text, "echo '%s' | sh" % text,
                     "sh <<< '%s'" % text, "eval \"sh -c '%s'\"" % text, "sh -c 'eval \"%s\"'" % text,
                     "sh -c 'intests; cd tests; echo hi > ../kept.txt'"):
            with self.subTest(line):
                self.refused_for_members(line, "Law 5")
        for line in (text, "eval '%s'" % text, "sh -c 'cd tests; echo hi > kept.txt'"):
            for agent_id in (AGENT_A, AGENT_B):
                with self.subTest(line, agent_id=agent_id):
                    self.assertSilent(line, agent_id)
        self.refused_for_members("sh -c 'setkept; echo hi > $KEPT'", "")  # KEPT is unset there

    def test_a_function_the_new_shell_does_not_hold_runs_nothing(self):
        for line in ("sh -c pushit", "zsh -c pushit", "bash -c pushit", "sh -c toledger", "zsh -c 'eval pushit'",
                     "sh -c 'echo $(pushit)'", "echo pushit | sh", "sh <<< toledger", "env sh -c pushit",
                     "eval 'sh -c pushit'", "sh -c 'sh -c pushit'", "sh -c 'f() { pushit; }; f'"):
            with self.subTest(line):
                self.silent_for_everyone(line)
        for line in ("pushit", "eval pushit", "echo $(pushit)", "sh -c pushit; pushit", "toledger",
                     "sh -c 'f() { git push; }; f'", "f() { pushit; }; f"):
            with self.subTest(line):
                self.refused_for_members(line, "Law 5" if "toledger" in line else "Law 7")

    def test_a_program_the_profile_shadows_is_read_as_the_program(self):
        """`sh -c 'git push'` pushes, and the reason names no shell function, which the new shell does not hold."""
        for line in ("sh -c 'git push'", "zsh -c 'git push'", "bash -c 'git push'", "echo 'git push' | sh",
                     "zsh -c 'eval git push'", "sh -c 'echo $(git push)'"):
            with self.subTest(line):
                r = self.refused_for_members(line)
                self.assertNotIn("already defines", r.reason)
        r = self.refused_for_members("git push")
        self.assertIn("`git` as a shell function", r.reason)

    def test_the_harness_shadows_stand_only_in_the_bash_tools_shell(self):
        """The harness's grep, find and rg run the file CLAUDE_CODE_EXECPATH names (SPD-253); a new shell runs the
        program, so the line's value there names no file that runs."""
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", NEW_SHELL_FUNCTIONS + THIS_MACS_SHADOWS)
        for line in ("CLAUDE_CODE_EXECPATH=/tmp/x.sh grep a f", "CLAUDE_CODE_EXECPATH=/tmp/x.sh find . -name x"):
            with self.subTest(line):
                self.refused_for_members(line, "$_cc_bin")
        for line in ("sh -c 'CLAUDE_CODE_EXECPATH=/tmp/x.sh grep a f'", "zsh -c 'CLAUDE_CODE_EXECPATH=/tmp/x.sh find . -name x'",
                     "CLAUDE_CODE_EXECPATH=/tmp/x.sh sh -c 'grep a f'", "sh -c 'grep -rn x .'"):
            with self.subTest(line):
                self.silent_for_everyone(line)


class HeldPlainUnaliasTest(ShellSnapshotCase):
    """SPD-299: zsh parses eval's words and a substitution's body with the aliases as they stand when it reads them, so
    after the line's `unalias git` (or `unalias -a`, `unalias -m`) the snapshot's plain alias no longer stands there and
    `eval 'git push'` runs git.  line_aliases.shell_aliased still expanded it, since clear_alias_line cleared only the
    names the line's own table held: under a profile's `alias git=hub` the hook read `hub push` -- other text, the push
    missed.  SPD-283 did this for the global and suffix aliases; the plain ones now follow: a line's unalias clears the
    snapshot's plain alias for the text the shell parses after it, and one that may or may not have run, or whose names
    the hook cannot read, doubts it.  The line's own text, parsed before any of it runs, and eval's text that holds the
    unalias itself still expand it.

    Probed through tests/probes/shell_probe.py in zsh 5.9 -f and -f -o nobareglobqual, a fresh `zsh -f -c` sourcing a
    file of `alias gp='echo SNAP-GP'` and `alias go='echo SNAP-GO'` and evaluating the line, as the Bash tool's shell
    does (2026-09-24): after `unalias gp`, `eval "gp x"`, `echo $(gp y)` and `cat <(gp ps)` found no command gp while the
    line's own `gp top` printed SNAP-GP; `unalias -a`, `unalias -m "g*"`, `X=gp; unalias $X`, `unhash -a gp`, `unhash -am
    "g*"`, `disable -a gp` and `true | unalias gp` each cleared it for a later eval; `unalias -s gp`, `(unalias gp)` and
    `eval "unalias gp; gp same"` left it running there.

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud."""

    def setUp(self):
        super().setUp()
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", PLAIN_BEFORE_ALIASES + PROGRAM_ALIAS)

    def test_the_tickets_evidence(self):
        for line in ("unalias git; eval 'git push'", "unalias -a; eval 'git push'", "unalias git; echo $(git push)",
                     "unalias git; cat <(git push)", "unalias -- git; eval 'git push'", "unalias ls git; eval 'git push'",
                     "unalias -a; echo `git push`", "unalias git; eval 'eval git push'", "unalias git; runit eval 'git push'",
                     "unalias git; eval 'echo $(git push)'", "eval 'unalias git'; eval 'git push'"):
            with self.subTest(line):
                r = self.refused_for_members(line)
                self.assertNotIn("hub", r.reason)

    def test_an_unalias_that_may_not_have_run_or_that_the_hook_cannot_read_doubts_it(self):
        for line in ("unalias -m 'g*'; eval 'git push'", "X=git; unalias $X; eval 'git push'",
                     "if [ -n \"$X\" ]; then unalias git; fi; eval 'git push'", "true | unalias git; eval 'git push'",
                     "unalias -m '*'; echo $(git push)"):
            with self.subTest(line):
                self.refused_for_members(line, "an `unalias` may not have run")

    def test_where_the_snapshots_alias_still_stands_it_is_expanded(self):
        """The line's own words and the text holding the unalias were parsed before it ran, `unalias -s` clears suffix
        aliases alone, and eval's text reads the alias the line defines in its place."""
        for line in ("unalias git; git push", "eval 'unalias git; git push'", "unalias -s git; eval 'git push'",
                     "unalias -a; git push", "unalias git; alias git=hub; eval 'git push'"):
            with self.subTest(line):
                self.silent_for_everyone(line)

    def test_a_cleared_alias_leaves_its_word_the_command_it_names(self):
        """`gp` after `unalias gp` finds no command, where the snapshot's alias would have run `git push` (itself `hub
        push` under git=hub, the body's first word being expanded again)."""
        for line in ("unalias gp; eval gp", "unalias -a; echo $(gp)", "unalias gp; cat <(gp)"):
            with self.subTest(line):
                self.silent_for_everyone(line)

    def test_a_word_an_alias_ending_in_a_blank_passes_on_is_read_the_same(self):
        """zsh expands the word after an alias whose body ends in a blank (`please='nice '`), with the aliases as they
        stand where the text is parsed: cleared there, `git` is git; doubted, it is refused a member."""
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh",
                            PLAIN_BEFORE_ALIASES + PROGRAM_ALIAS + "alias -- please='nice '\n")
        self.silent_for_everyone("please git push")
        self.refused_for_members("unalias git; eval 'please git push'")
        self.refused_for_members("X=git; unalias $X; eval 'please git push'", "an `unalias` may not have run")


class HashTableAliasClearTest(ShellSnapshotCase):
    """SPD-306: zsh's `unhash -a NAME` and `disable -a NAME` take an alias out of the table as `unalias NAME` does, and
    `unhash -s` / `disable -s` a suffix alias, but analyse read unhash and disable for their `-f` alone (SPD-281), so
    under a profile's `alias git=hub`, `unhash -a git; eval 'git push'` was still read as `hub push` and the push missed
    (SPD-299's case, another spelling).  They now clear the line's and the snapshot's aliases as unalias does
    (line_aliases.clear_alias_line), each builtin's flags read for the table they name; `enable -a NAME` may bring a
    disabled alias back, so it doubts a name the line cleared.

    Probed through tests/probes/shell_probe.py in zsh 5.9 -f and -f -o nobareglobqual, a fresh `zsh -f -c` sourcing a
    file of `alias gp='echo SNAP-GP'`, `alias go='echo SNAP-GO'`, `alias -s txt='echo SUFFIX'` and evaluating the line
    (2026-09-24): `unhash -a gp`, `unhash -a -- gp`, `unhash -am "g*"`, `unhash -m -a "g*"`, `disable -a gp` and `disable
    -am "g*"` each left `eval "gp x"` finding no command gp (go still ran after the `gp` ones), while the line's own `gp
    top` printed SNAP-GP; `unhash -s txt`, `-as txt`, `-sa txt`, `-a -s txt` and `disable -s txt`, `-as txt` left `eval
    "a.txt z"` finding no command, where `-a txt` did not clear the suffix alias; `-af`, `-ad` (unhash), `-af`, `-ar`
    (disable), `+a gp` (a name, not an option), `-as gp`, bare `unhash -a` ("not enough arguments") and bare `disable
    -a` (a listing) left gp running; `disable -a gp; enable -a gp` and `eval "enable -a gp"` ran SNAP-GP again, where
    `enable -a gp` after `unhash -a gp` or `unalias gp` found "no such hash table element".

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud."""

    def setUp(self):
        super().setUp()
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", PLAIN_BEFORE_ALIASES + PROGRAM_ALIAS)

    def test_the_tickets_evidence(self):
        for line in ("unhash -a git; eval 'git push'", "unhash -a -- git; eval 'git push'", "unhash -a ls git; eval 'git push'",
                     "unhash -a git; echo $(git push)", "unhash -a git; cat <(git push)", "disable -a git; eval 'git push'",
                     "disable -a -- git; echo `git push`", "unhash -a git; eval 'eval git push'",
                     "eval 'unhash -a git'; eval 'git push'", "disable -a nosuch git; eval 'git push'",
                     "alias git=hub; unhash -a git; eval 'git push'", "alias git=hub; disable -a git; eval 'git push'"):
            with self.subTest(line):
                r = self.refused_for_members(line)
                self.assertNotIn("hub", r.reason)

    def test_a_suffix_alias_unhash_or_disable_clears_leaves_its_word_the_command(self):
        """`alias -s txt='command git push'` pushes where b.txt is a command word (`command` keeps the profile's git=hub
        off it, and the snapshot's plain `a.txt` alias would run ls in a.txt's place); `-s` clears it, `-a` alone, `-f`
        and `-r` do not."""
        define = "alias -s txt='command git push'; "
        for clear in ("unhash -s txt", "unhash -as txt", "disable -s txt", "disable -sa txt", "unhash -a -s txt"):
            with self.subTest(clear):
                self.silent_for_everyone(define + clear + "; eval b.txt")
        for clear in ("true", "unhash -a txt", "disable -a txt", "unhash -fs txt", "disable -rs txt"):
            with self.subTest(clear):
                self.refused_for_members(define + clear + "; eval b.txt")

    def test_a_clear_that_may_not_have_run_or_that_the_hook_cannot_read_doubts_it(self):
        for line in ("unhash -am 'g*'; eval 'git push'", "unhash -m -a 'g*'; eval 'git push'", "disable -am 'g*'; eval 'git push'",
                     "X=git; unhash -a $X; eval 'git push'", "X=git; disable -a $X; eval 'git push'",
                     "unhash $o git; eval 'git push'", "unhash -a $o git; eval 'git push'",
                     "true | unhash -a git; eval 'git push'", "disable -am '*'; echo $(git push)"):
            with self.subTest(line):
                self.refused_for_members(line, "an `unalias` may not have run")

    def test_enable_may_bring_back_what_disable_cleared(self):
        """zsh's `enable -a NAME` restores the alias `disable -a` hid, with the body it had; the hook does not keep that
        body, so it reads the name as one whose definition may or may not stand."""
        for line in ("disable -a git; enable -a git; eval 'git push'", "disable -a git; eval 'enable -a git'; eval 'git push'",
                     "disable -a git; enable -am 'g*'; eval 'git push'", "disable -a git; enable -a -- git; eval 'git push'",
                     "disable -a git; enable -a $X; eval 'git push'"):
            with self.subTest(line):
                self.refused_for_members(line, "may not have run")

    def test_where_the_snapshots_alias_still_stands_it_is_expanded(self):
        """The line's own words were parsed before the clear ran; eval's text holding it too; a table other than the
        aliases' (`-f`, `-d`, `-r`, `-s`, none), a bare `-a` (unhash's error, disable's listing), `+a` (a name) and an
        enable of a name nothing cleared leave the alias standing."""
        for line in ("unhash -a git; git push", "disable -a git; git push", "eval 'unhash -a git; git push'",
                     "unhash -as git; eval 'git push'", "unhash -s git; eval 'git push'", "unhash -af git; eval 'git push'",
                     "unhash -ad git; eval 'git push'", "unhash git; eval 'git push'", "unhash +a git; eval 'git push'",
                     "unhash -a; eval 'git push'", "disable -a; eval 'git push'", "disable -as git; eval 'git push'",
                     "disable -af git; eval 'git push'", "disable -ar git; eval 'git push'", "disable git; eval 'git push'",
                     "disable +a git; eval 'git push'", "enable -a git; eval 'git push'", "enable -a; eval 'git push'",
                     "unhash -a git; alias git=hub; eval 'git push'", "disable -a git; alias git=hub; eval 'git push'"):
            with self.subTest(line):
                self.silent_for_everyone(line)

    def test_a_cleared_alias_leaves_its_word_the_command_it_names(self):
        for line in ("unhash -a gp; eval gp", "disable -a gp; echo $(gp)", "unhash -a gp git; cat <(gp)"):
            with self.subTest(line):
                self.silent_for_everyone(line)


# SPD-313: wrappers, a word that is another alias, and an alias whose body ends in one, for chains that cross between
# the snapshot's aliases and the line's; `broken` is a body the hook cannot read.
MIXED_CHAIN_SNAPSHOT = """\
# Snapshot file
# Unset all aliases to avoid conflicts with functions
unalias -a 2>/dev/null || true
# Functions
# Shell Options
setopt autocd
# Aliases
alias -- s='sudo '
alias -- w='s '
alias -- ws=s
alias -- gp='git push'
alias -- gs='git status'
alias -- u=gp
alias -- e='echo '
alias -- we='e '
alias -- broken='git push
"""


class MixedAliasChainTest(ShellSnapshotCase):
    """SPD-313: zsh holds one alias table, the snapshot's aliases and the line's in it, and looks the word after any alias
    whose body ends in a blank up there, and the first word of each body it expands, chained or not.  The hook chained the
    line's aliases into the line's (SPD-310, line_aliases.chained_texts) and the snapshot's into the snapshot's
    (shell_aliased's loop), never one into the other, and the snapshot's loop joined a chained body without looking its
    first word up -- so behind the snapshot's `s='sudo '` a line's `gp` in eval, behind a line's `sn='sudo '` the snapshot's
    `gp`, and behind `s` the snapshot's `u=gp` were each read as a program sudo runs, and a member's push went through.
    One walk now reads the table as zsh has it there: the line's own aliases as eval's or a substitution's text was
    parsed with them, over the snapshot's where they stand.

    Probed in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py, a fresh `zsh -f -c` sourcing a
    file of `alias s='nice '`, `w='s '`, `ws='s'`, `gp='echo SNAP-GP'`, `u='gp'`, `e='echo '` and evaluating the line, as
    the Bash tool's shell does (2026-09-24): `alias lg='echo LINE-LG'; eval 's lg'` printed LINE-LG; `alias ln2='nice ';
    eval 'ln2 gp'`, `w gp`, `s u`, `ws gp`, `eval 'ln2 u'`, `eval 'ln2 w gp'`, `s s gp` and, with `alias lu=gp`, `eval 's
    lu'` printed SNAP-GP; `alias gp='echo LINE-GP'; eval 's gp'` printed LINE-GP where the line's own `s gp` printed
    SNAP-GP; `alias s='echo LS '; eval 'w gp'` printed `LS echo SNAP-GP`; `unalias gp; eval 's gp'`, `eval "s 'gp'"` and
    `alias lg=...; s lg` found no command.

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud."""

    def setUp(self):
        super().setUp()
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", MIXED_CHAIN_SNAPSHOT)

    def expansion(self, command):
        """The names the analysis recorded as the shell's own, for a reason's note."""
        m = load_spud_module()
        env = dict(os.environ)
        env["SPUD_USER_CLAUDE_DIR"] = self.home.env["SPUD_USER_CLAUDE_DIR"]
        with mock.patch.dict(os.environ, env, clear=True):
            a = m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))
        return [name for name, _ in a.shell_expanded]

    def test_the_tickets_evidence(self):
        for line in ("alias lg='git push'; eval 's lg'", "alias sn='sudo '; eval 'sn gp'", "s u",
                     "alias lg='git push'; echo $(s lg)", "alias sn='sudo '; echo $(sn gp)", "echo $(s u)"):
            with self.subTest(line):
                r = self.refused_for_members(line)
                self.assertIn("git push", r.reason)

    def test_the_chain_crosses_the_tables_either_way_as_often_as_it_goes(self):
        """Behind a wrapper the chain goes on from table to table: a chained body's first word, a body's that ends in
        another wrapper, and the word after each, looked up in the line's table first, then the snapshot's.  A name is in
        flight only while its body is read: `ws ws gp` (ws='s') is `sudo sudo git push`, the second ws looked up once the
        first one's body is over (probed: with `alias y='s'`, `y y gp` printed SNAP-GP), where the hook had read it as the
        text of the first."""
        for line in ("w gp", "ws gp", "ws ws gp", "w ws gp", "s s gp", "alias lg='git push'; alias lw=lg; eval 's lw'",
                     "alias lu=gp; eval 's lu'", "alias lw=ws; eval 'lw lw gp'",
                     "alias sn='sudo '; eval 'sn u'", "alias sn='sudo '; eval 'sn w gp'", "alias sn='sudo '; eval 's sn gp'",
                     "alias sn='sudo '; eval 'sn s u'", "alias e='sudo '; eval 'we gp'", "alias sw='w '; eval 'sw u'",
                     "alias lg='git push'; eval 'w lg'", "alias lg='git push'; eval 'eval s lg'"):
            with self.subTest(line):
                self.refused_for_members(line)

    def test_the_lines_alias_stands_over_the_snapshots_of_the_same_name(self):
        """Where eval's text was parsed after the line redefined or cleared a snapshot alias, the chain reads the line's."""
        self.refused_for_members("alias gs='git push'; eval 's gs'")
        self.refused_for_members("alias u='git push'; eval 'w u'")
        for line in ("alias gp='git status'; eval 's gp'", "unalias gp; eval 's gp'", "unalias u; eval 's u'",
                     "alias u=true; eval 'w u'", "alias gp='git status'; eval 's u'"):
            with self.subTest(line):
                self.silent_for_everyone(line)

    def test_the_chain_stops_where_zsh_stops_it(self):
        """A quoted word, a word no alias names, the line's alias in the line's own text (parsed before it ran), and a
        wrapper that runs echo each leave the word what it spells."""
        for line in ("eval \"s 'gp'\"", "alias sn='sudo '; eval \"sn 'gp'\"", "alias lg='git push'; s lg", "s gs", "we gp",
                     "s s 'gp'", "s \\gp", "alias sn='sudo '; eval 'sn x=1 gp'", "alias lg='git push'; eval 's x lg'"):
            with self.subTest(line):
                self.silent_for_everyone(line)

    def test_a_word_written_both_ways_is_read_both_ways_along_the_chain(self):
        """SPD-308 for every word the chain reaches, not the first behind the command word alone: under `alias git=hub`,
        `s s 'git' push` is git's push behind two wrappers, and `s s git status` beside it hub's status."""
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", MIXED_CHAIN_SNAPSHOT + "alias -- git=hub\n")
        self.refused_for_members("s s 'git' push; s s git status")
        self.refused_for_members("alias sn='sudo '; eval \"sn sn 'git' push; sn sn git status\"")
        self.silent_for_everyone("s s git push")
        self.silent_for_everyone("s u")  # u is gp, whose body's first word is git: hub's push

    def test_a_body_the_hook_cannot_read_anywhere_along_the_chain(self):
        for line in ("s broken", "alias sn='sudo '; eval 'sn broken'", "alias lb=broken; eval 's lb'", "w broken"):
            with self.subTest(line):
                self.refused_for_members(line, "cannot read")

    def test_the_reason_names_each_snapshot_alias_the_chain_expanded(self):
        self.assertEqual(self.expansion("s u"), ["s", "u", "gp"])
        self.assertEqual(self.expansion("w gp"), ["w", "s", "gp"])
        self.assertEqual(self.expansion("ws ws gp"), ["ws", "s", "ws", "s", "gp"])
        self.assertEqual(self.expansion("alias sn='sudo '; eval 'sn u'"), ["u", "gp"])
        self.assertEqual(self.expansion("alias gs='git push'; eval 's gs'"), ["s"])


FLIGHT_SCOPE_SNAPSHOT = """\
# Snapshot file
# Unset all aliases to avoid conflicts with functions
unalias -a 2>/dev/null || true
# Functions
# Shell Options
setopt autocd
# Aliases
alias -- s='sudo '
alias -- gp='git push'
alias -- x='echo; s'
alias -- x2='echo X2;'
alias -- x3='echo A3; x3'
alias -- x5='V=1'
alias -- x6='echo X6 && s'
alias -- x7='echo X7 | s'
alias -- z='echo Z; s x'
alias -- w2=x
alias -- ls='ls -G'
alias -- lz='ls; s'
alias -- rx='echo; w3'
alias -- w3=rx
"""


class AliasFlightScopeTest(ShellSnapshotCase):
    """SPD-316: zsh keeps an alias's name in flight while the text of its body is being read -- through its last word,
    where a body's own name is not looked up again -- and no longer: the member's words after the body are read from the
    line once the body is over, so a word there named like the alias is looked up as any other.  A body with a command
    word after a separator (`x='echo; s'`, `s='sudo '`) chains into those words, and `x x gp` runs `echo; sudo echo; sudo
    git push`.  The hook read the body and the words after it as one text with the name in flight for all of it, so the
    second x stayed a word sudo runs, and the push went through unread (findings [], expanded ['x', 's']).

    Probed in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py (2026-09-24), with `alias s='nice
    '`, `gp='echo SNAP-GP'` and the bodies below: `x x gp` printed two blank lines, then SNAP-GP; `x2 x2 gp` X2, X2,
    SNAP-GP; `x5 x5 gp`, `x7 x7 gp` SNAP-GP; `x6 x6 gp` X6, X6, SNAP-GP; `z z gp` (z='echo Z; s x') Z, a blank line, Z,
    a blank line, SNAP-GP -- the z after x's expansion, read from the line, looked up again; and `x3` (x3='echo A3; x3')
    printed A3, then found no command x3: the body's own last word is still in its flight.

    AGENT_A and AGENT_B plan home:tests/** and home:bin/spud."""

    expansion = MixedAliasChainTest.expansion

    def setUp(self):
        super().setUp()
        self.write_snapshot("snapshot-zsh-1700000000000-aaaaaa.sh", FLIGHT_SCOPE_SNAPSHOT)

    def test_the_tickets_evidence(self):
        for line in ("x x gp", "eval 'x x gp'", "echo $(x x gp)"):
            with self.subTest(line):
                r = self.refused_for_members(line)
                self.assertIn("git push", r.reason)

    def test_the_words_after_the_body_are_looked_up_once_it_is_over(self):
        """After a separator, a pipe, an `&&` or an assignment in the body, the member's next word is at a command word or
        chained behind the body's wrapper, and a word there named like the alias -- or like one its body's first word
        expanded (`w2 x gp`, w2=x), or the one a chained body stands for (`s x gp`) -- is looked up as zsh looks it up."""
        for line in ("x2 x2 gp", "x5 x5 gp", "x6 x6 gp", "x7 x7 gp", "z z gp", "x x x gp", "w2 x gp", "s x gp",
                     "s z gp", "lz x gp", "alias lg='git push'; eval 'x x lg'"):
            with self.subTest(line):
                r = self.refused_for_members(line)
                self.assertIn("git push", r.reason)

    def test_a_name_its_own_body_looks_up_stays_in_flight(self):
        """The name is in flight for its body's own words, through the last: `x3 gp` runs `echo A3`, then no command x3
        with gp as its word, and `ls ls` is ls -G listing ls, read without end nowhere; a body that reaches its own name
        through another alias (rx='echo; w3', w3=rx) keeps it too."""
        for line in ("x3 gp", "x3 x3", "x3 x3 gp", "ls ls", "ls gp", "ls -la gp", "lz", "rx gp", "rx rx"):
            with self.subTest(line):
                self.silent_for_everyone(line)

    def test_the_reason_names_each_snapshot_alias_the_chain_expanded(self):
        self.assertEqual(self.expansion("x x gp"), ["x", "s", "x", "s", "gp"])
        self.assertEqual(self.expansion("x2 x2 gp"), ["x2", "x2", "gp"])


if __name__ == "__main__":
    unittest.main()
