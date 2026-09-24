"""PreToolUse(Bash): Claude Code's shell snapshot (the aliases and functions the shell already defines), and the reader
failing closed on text it cannot read."""

import json
import os
import shutil
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from helpers import load_spud_module, wall_clock
from hookcase import AGENT_A, AGENT_B, AGENT_C, INLINE_WORDING, SCRIPT_WORDING, BashHookCase


# A snapshot of the shape Claude Code writes (SPD-133), with a name for each reading the hook makes of one.  The real
# files on this Mac are 4,100 lines and 124 KB; nothing here reads them, and a test never touches ~/.claude.
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
        is this reading's premise, not an accident of it.  Nothing is written: the hook is asked about the path."""
        planted = os.path.expanduser("~/.claude/shell-snapshots/snapshot-zsh-1900000000000-planted.sh")
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


class RealShellSnapshotTest(BashHookCase):
    """The same rule against this Mac's own ~/.claude/shell-snapshots, skipped where there is none: the ticket's evidence
    was Eric's oh-my-zsh profile, and nothing here is asserted about a name that profile does not define.  Never fails the
    suite on Eric's dotfiles -- it only checks that a name the shell really does define as a git write is refused, and
    that the names members run all day are not."""

    def setUp(self):
        super().setUp()
        m = load_spud_module()
        directory = Path(os.path.expanduser("~/.claude")) / "shell-snapshots"
        if not directory.is_dir() or not list(directory.glob("snapshot-*.sh")):
            self.skipTest("no shell snapshot in %s" % directory)
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
                    "find . -name x", "diff /etc/hosts /etc/hosts"):
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

    The rule now (shell/positional, analyse.analyse_shell_text):

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
        """(proposal by SPUD-212/Bender) analyse.read_shell_name read an alias's or a snapshot function's body with no
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
        verb too, since the hook cannot resolve the loop variable).  None is a git write this Mac's profile defines."""
        self.write_snapshot("snapshot-zsh-1700000000009-999999.sh",
            "loopgit () {\n\tfor a in \"$@\"; do git $a; done\n}\nglobalgit () {\n\tgit $GITVERB\n}\n")
        for line in ("loopgit push", "loopgit status", "GITVERB=$(echo push); globalgit", "loopgit commit -m x"):
            with self.subTest(line=line):
                self.refused_for_members(line, "cannot resolve")  # the member-filled variable is refused on doubt
                self.assertSilent(line, agent_id=None)

    def test_205_a_body_s_own_variable_stays_pruned(self):
        """A variable the body itself fills, not from the member's words, is still the body's own and stays dropped, so a
        member is not refused a profile function it did not fill: a loop over a literal list, a call with no words."""
        self.write_snapshot("snapshot-zsh-1700000000009-999999.sh",
            "ownloop () {\n\tfor a in one two; do git $a; done\n}\nglobalgit () {\n\tgit $GITVERB\n}\n")
        for line in ("ownloop", "globalgit"):  # no member words fill the loop, and no line assigns GITVERB
            self.silent_for_everyone(line)

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


if __name__ == "__main__":
    unittest.main()
