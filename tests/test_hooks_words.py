"""PreToolUse(Bash): the words a shell expands before a command runs -- parameter expansions in command position, trap
actions, zsh's (e) flag, process substitution, aliases for eval, PATH, subscripts, shell functions, wrappers and settled
cd targets."""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from helpers import load_spud_module, wall_clock
from helpers import git as scratch_git
from hookcase import AGENT_A, AGENT_B, AGENT_C, GIT_DIR_WORDING, GIT_NESTED_WORDING, INLINE_WORDING, SCRIPT_WORDING, SESSION
from hookcase import SPUD_PLANTED_WORDING, VARIABLE_WORDING, WORD_WORDING, BashHookCase, plant_git_dir


class ParameterExpansionCommandWordTest(BashHookCase):
    """SPD-043: a word the Bash hook dispatches on by name that the shell builds from an expansion the hook does not resolve
    exactly hid the command.  Probed in zsh 5.9 -f, zsh -f -o nobareglobqual (this Mac's Bash tool), bash 3.2 and sh, with a fake
    git first on a scratch PATH writing a marker in the scratchpad: each of these ran `git push` while the hook read kind other
    with no finding: `${X:-git} push` and every operator form, zsh's `${(L)X}`, `${~X}`, `${=X}`, `$~X`, `$=X` and `$^X`,
    `$X$Y push`, `g$X push`, `${arr[1]}` (zsh) and `${arr[0]}` (bash), `X=env; $X git push` (a wrapper through a variable),
    `X='g?t'; $X push` (bash globs a quoted glob the value holds), `$'\\x67it' push`, `X=; $X git push` (the empty word goes and
    git is the command), `X="git push '"; $X` (bash splits on blanks and keeps the quote), `X=g; X+=it; $X push`,
    `IFS=_; X=git_push; $X`, `arr=(git status); $arr push` (bash reads the first element), and a value the line assigned that
    does not hold when the variable is read: `X=git; (X=ls); $X push`, `X=git; true | X=ls; $X push` (bash),
    `X=git; X=ls true; $X push`, `X=ls; for X in git; do $X push; done`, `X=; : ${X:=git}; $X push`,
    `X=ls; read X <<< git; $X push`, `X=git; echo $(X=ls); $X push`, `X=git; f() { X=ls; }; $X push`.

    For a member such a word is refused, as a command word from a substitution always was: only a bare `$X` or `${X}` whose value
    the line assigned for certain is read, as bash reads it (split on blanks, its glob characters active) and as zsh does (one
    word), and its words go back through the prefixes (a wrapper, an empty word).  `V=push; git $V` is read as `git push`.  A
    `$` in single quotes or escaped is literal and stays silent in a spud call's message.  Spud is not refused by any of it.
    AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        for rel in ("tests/keep.py", "ledger/tickets/SPD-001.md"):
            p = self.home.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")

    def refused_for_members(self, command, needle="cannot resolve", cwd=None):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id, cwd)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None, cwd=cwd)
        return r

    def findings(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path))).findings

    def test_the_tickets_evidence_commands(self):
        for cmd in ("${X:-git} push", "X=g Y=it; $X$Y push", "X=it; g$X push", "arr=(git); ${arr[1]} push", "arr=(git); ${arr[0]} push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        self.refused_for_members("X=env; $X git push", "Law 7")
        self.refused_for_members("X='g?t'; $X push", "Law 7")
        self.assertEqual(self.findings("${X:-git} push"), [("var", "${X:-git}")])

    def test_every_operator_form_and_zsh_flag_is_refused(self):
        for cmd in ("${X:-git} push", "X=; ${X:-git} push", "${X-git} push", "${X:=git} push", "X=1; ${X:+git} push", "X=xgit; ${X#x} push",
                    "X=gitx; ${X%x} push", "X=gitxx; ${X%%x*} push", "X=xxgit; ${X##*x} push", "X=gat; ${X/a/i} push", "X=gaat; ${X//a/i} push",
                    "X=Git; ${X,} push", "X=GIT; ${X,,} push", "X=git; ${X^} push", "X=GIT; ${(L)X} push", "X='g?t'; ${~X} push",
                    "X='git push'; ${=X}", "X='g?t'; $~X push", "X=git; $=X push", "X=git; $^X push", "Y=git; X=Y; ${!X} push",
                    "X=git; ${X:0:3} push", "${#X} push", "$((1)) push", "$[1] push", "X=/usr/bin/git; $X:t push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_concatenation_and_partial_expansion(self):
        for cmd in ("X=g Y=it; $X$Y push", "X=it; g$X push", "X=g; ${X}it push", "X=gi; $X\"t\" push", "X=git; $X$Z push",
                    "X=/usr/bin; $X/git push", "X=git; /usr/bin/$X push", "X=g; \"$X\"it push", "X=gi Xt=ls; $X\"t\" push",
                    "X=gi Xt=ls; $X't' push", "X=gi Xt=ls; $X\\t push", "X=git; $X[1] push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_arrays(self):
        """zsh's `$arr` is every element and its subscripts count from 1; bash's is the first element, from 0: a bare array
        reference is read both ways and refused, a subscript is never resolved."""
        for cmd in ("arr=(git); ${arr[1]} push", "arr=(git); ${arr[0]} push", "arr=(x git); ${arr[@]} push", "arr=(ls); $arr"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        for cmd in ("arr=(git status); $arr push", "arr=(git); ${arr} push", "declare -a arr=(git status); $arr push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")  # bash's reading, the first element: `git push`

    def test_ansi_c_and_locale_quoting(self):
        # SPD-202: an ANSI-C string both shells decode alike is read as its value, the command it spells (AnsiCQuotingTest);
        # one whose escapes they decode apart, and bash's locale string, stay words the hook cannot resolve
        for cmd in ("$'git' push", "$'\\x67it' push", "$'\\147it' push", "g$'i't push", "git $'push'", "git $'\\x70ush'"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")
        for cmd in ("$\"git\" push", "$'\\u0067it' push", "git $'\\u0070ush'", "g$'\\ci't push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_a_wrapper_or_glob_reached_through_a_variable(self):
        for cmd in ("X=env; $X git push", "X=exec; $X git push", "X=command; $X git push", "X='env git'; $X push", "X=nice; $X -n 5 git push",
                    "X='-n 5'; nice $X git push", "X=/usr/bin/env; ${X} git push", "X=git; \"$X\" push", "X=git; \"${X}\"\"\" push",
                    "X='g?t'; $X push", "X='g*t'; $X push", "X=g?t; $X push", "X='gi[t]'; $X push", "X='env g?t'; $X push",
                    "X=x=./git; $X push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")

    def test_the_value_is_read_as_the_shells_split_it(self):
        refused = ("X=\"git push '\"; $X", "X=; $X git push", "X=\"\"; $X git push", "X='  '; $X git push", "X='git  push'; $X",
                   "X='/usr/local/my tools/git'; $X push", "X=-C; git $X . push", "X='-C . push'; git $X", "V=push; git $V",
                   "V=push; git ${V}", "X='-c alias.p=push'; git $X p")
        for cmd in refused:
            with self.subTest(cmd):
                for agent_id in (AGENT_C, AGENT_A):
                    self.assertEqual(self.bash(cmd, agent_id).decision, "deny", cmd)
                self.assertSilent(cmd, agent_id=None)
        for cmd in ("X=g; X+=it; $X push", "Y=git; X=$Y; $X push", "X=$(echo git); $X push", "IFS=_; X=git_push; $X",
                    "X='$Y'; Y=git; $X push", "X=`echo git`; $X push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_other_words_the_dispatch_reads_by_name(self):
        """git's options, verb and the arguments git_refused reads, a wrapper's options, a shell's options, python's options and
        script, and every word of a spud call."""
        spud = self.spud_cli
        for cmd in ("git $V", "git ${V:-push}", "git $(echo push)", "git `echo push`", "git -C $D status", "git ${(L)V}",
                    "nice -n $N git status", "env -u $U git status", "sudo -u ${U:-root} git status", "sh $O 'git push'",
                    "bash -$O 'git push'", "python3.14 -I -S $S --as spud board", "python3 -m $M", "X=; git $X status"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        # A command the words as spelled already refuse keeps its own reason.
        for cmd, needle in (("git -c $KV log", "Law 7"), ("git stash $S", "Law 7"), ("git branch $B", "Law 7"), ("git config $K $V", "Law 7"),
                            ("python3.14 -I -S ${HOME}/bin/spud ticket new --title x", "Law 6"), ("spud --as ${A:-spud} board", "--as")):
            with self.subTest(cmd):
                self.refused_for_members(cmd, needle)
        # A word of a spud call can become `--as=spud` or another command; Spud's own reading of these calls is unchanged.
        for cmd, needle in (("%s $C new --title x" % spud, "cannot resolve"), ("%s --as $A board" % spud, "--as"),
                            ("%s ticket $S --title x" % spud, "cannot resolve"), ("%s member log \"$M\"" % spud, "cannot resolve"),
                            ("%s member log $M" % spud, "cannot resolve")):
            with self.subTest(cmd):
                for agent_id in (AGENT_C, AGENT_A):
                    self.assertRefused(cmd, needle, agent_id)
                self.assertNotEqual(self.bash(cmd, agent_id=None).decision, "deny")

    def test_a_value_the_line_may_not_have_kept(self):
        for cmd in ("X=git; if false; then X=ls; fi; $X push", "X=git; false && X=ls; $X push", "X=git; (X=ls); $X push",
                    "X=git; X=ls true; $X push", "X=ls; X=git :; $X push", "X=ls; for X in git; do $X push; done",
                    "X=ls; for X in git; do :; done; $X push", "X=; : ${X:=git}; $X push", "X=; echo \"${X:=git}\"; $X push",
                    "X=git; true | X=ls; $X push", "X=git; X=ls | cat; $X push", "X=git; X=ls & wait; $X push",
                    "X=git; { X=ls; } | cat; $X push", "X=git; f() { X=ls; }; $X push", "X=ls; f() { X=git; }; f; $X push",
                    "X=ls; read X <<< git; $X push", "X=ls; printf -v X git; $X push", "X=ls; unset X; $X git push",
                    "X=git; echo $(X=ls); $X push", "X=git; echo `X=ls`; $X push", "X=ls; typeset -n X=Y; Y=git; $X push",
                    "X=ls; source ./x.sh; $X push", "X=ls; . ./x.sh; $X push", "X=ls; while true; do $X push; X=git; done",
                    "X=ls; eval \"$N=git\"; $X push", "X=ls; getopts g X; $X push", "X=ls; mapfile X < f; $X push",
                    "X=ls; sh -c 'X=git'; X=git; (X=ls); $X push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "")  # Law 7's reason where the line's own value is git, else the doubt's
        self.assertIn("may not hold", self.refused_for_members("X=ls; trap 'X=git' DEBUG; $X push").reason)
        self.assertIn("Law 7", self.refused_for_members("X=ls; X=git :; $X push", "").reason)
        for ok in ("X=git; if true; then X=ls; $X push; fi", "sh -c 'X=ls; $X'", "X=ls; export X; $X", "X=git; (X=ls; $X push)"):
            with self.subTest(ok):  # read where the assignment certainly ran
                self.assertSilent(ok)

    def test_spud_keeps_his_own_checks(self):
        spud = self.spud_cli
        self.assertRefused("%s --as $A member log hi" % spud, "Law 5", agent_id=None)
        self.assertRefused("X=%s; %s --as $X member log hi" % (AGENT_A, spud), "Law 5", agent_id=None)
        self.assertRefused("X='%s --as %s member log'; $X hi" % (spud, AGENT_A), "Law 5", agent_id=None)
        self.assertRefused("X=env; $X %s --as %s member log hi" % (spud, AGENT_A), "Law 5", agent_id=None)
        self.assertRefused("X=; %s --as $X member log hi" % spud, "Law 5", agent_id=None)
        self.assertRefused("D=%s; ${D}/bin/spud --as %s member log hi" % (self.home.path, AGENT_A), "Law 5", agent_id=None)
        self.assertRefused("D=%s; ${D}/bin/spud --as %s member log hi" % (self.home.path, AGENT_A), "cannot resolve")
        self.assertRefused("X=tee; echo x | $X ledger/tickets/SPD-001.md", "Law 1", agent_id=None)
        self.assertRefused("V=ticket; %s --as spud $V new --title x" % spud, "Law 6")
        self.assertRefused("X=git; ${X:-x} push", "cannot resolve")

    def test_controls_stay_silent(self):
        spud = self.spud_cli
        for ok in ("X=ls; $X", "X=ls; $X -la", "X=git; $X status", "X=git; ${X} log --oneline", "export X=ls; $X", "X=ls; echo $X; $X",
                   "echo $HOME", "ls ${DIR:-.}", "echo \"$(date)\"", "cat $F", "grep -n \"$P\" tests/keep.py", "git log -- $F",
                   "git diff $A $B", "git log --format='%h $x'", "echo $'a\\tb'", "printf $'a\\n'", "x=$((2*3)); echo $x", "echo $((1+2))",
                   "for f in tests/*.py; do python3 -m py_compile $f; done", "X=ls; if true; then echo $X; fi", "git show HEAD:$F",
                   "sh -c \"echo $HOME\"", "X='git push'; echo $X", "X=git; echo ${X:-x}",
                   "git log '$V'", "echo ${X:-git} push", "X=ls; Y=$X; echo $Y", "env FOO=1 ls $D", "git '$V'", "git \\$V"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        for allowed in ("%s --as %s member log 'cost $5 for ${X:-git} push'" % (spud, AGENT_A), "%s --as %s member log \\$5" % (spud, AGENT_A),
                        "%s --as %s member log \"cost \\$5\"" % (spud, AGENT_A)):
            with self.subTest(allowed):
                self.assertAllowed(allowed)
        # An expansion inside python's -c string is still no word the hook reads by name, so it earns neither of this
        # ticket's reasons; since SPD-150 the line is refused a member for the inline program it runs, and stays Spud's.
        self.assertSilent("python3 -c \"print('$HOME')\"", agent_id=None)
        r = self.assertRefused("python3 -c \"print('$HOME')\"", INLINE_WORDING)
        self.assertNotIn("cannot resolve", r.reason)
        self.assertRefused("git push", "Law 7")
        self.assertRefused("$X push", "spell the command out")


class TrapActionTest(BashHookCase):
    """SPD-054 (Agria's SPD-043 proposal): `trap` stores shell code the shell runs later -- at exit, before every command
    under DEBUG, on ERR, and on every signal by name or number -- and analyse_words dispatched the line as kind other, so a
    member's VCS write inside the action string reached the hook with no finding.  Probed in bash 3.2, zsh 5.9 -f, zsh -f -o
    nobareglobqual (the Bash tool's zsh on this Mac) and sh, with a fake git first on a scratch PATH writing a marker in the
    scratchpad.  All four ran `git push`: `trap 'git push' EXIT`, `trap "git commit -m x" ERR; false`, `trap 'cd /tmp && git
    add .' DEBUG; :`, `trap 'eval "git push"' 0`, `trap 'sh -c "git push"' INT; kill -INT $$`, the signal spelled `0`,
    `SIGHUP`, `15` or several at once, `trap -- 'git push' EXIT`, the command word reached through `builtin`, `time`,
    `X=trap; $X` or `tr?p` with a file named trap beside it, the line inside eval, `sh -c`, a function body, a subshell, a
    loop and another trap's action, and the action read from `"$X"`, `"$(echo git push)"` and `${X:-'git push'}`.  `command
    trap` ran it in bash and sh (in zsh `command` finds only an external) and `noglob trap` in zsh, whose modifier it is;
    `env`, `nohup`, `exec`, `sudo`, `nice` and `xargs` ran no builtin at all.

    The action is now read as the shell text it is, with its own quotes, as eval's rejoined words and a shell's `-c` string
    are: a finding inside it is the finding it would be on the line, so a member is refused under Law 7, Law 6 or the actor
    check exactly as it would be, and Spud is refused by none of it while keeping his own checks inside the action.

    The words that run nothing stay silent, each probed to run nothing in all four shells: `trap` alone, `trap -p`, `trap
    -l`, `trap -lp` and `trap -P EXIT` print or list, `trap -`, `trap - EXIT`, `trap '' EXIT` and `trap "" INT TERM` reset or
    ignore, and `trap EXIT` is a reset (bash and zsh) or a usage error (sh).  Both action positions are read, since the two
    shells disagree about options: bash reads the action after its options and `--`, zsh has none there and reads the word
    right after `trap` whatever it is (`trap -P EXIT` ran `-P` at exit, `trap -p EXIT` ran `-p`).  The single-argument form
    runs nothing anywhere (`trap 'git push'` and `trap git` print bash's and sh's usage and set nothing in zsh), but it is
    read all the same, fail closed and at no real cost: it names code, and the line is a shell error where it is not a
    no-op.  `env trap`, `nohup trap`, `exec trap` and `xargs trap` run no builtin, so they stay kind other, as `env cd` and
    `env source` do.

    The action runs at a working directory the hook cannot know: an EXIT action runs after every later cd (probed: `trap
    'echo trapped >> rel.txt' EXIT; cd /tmp` wrote /tmp/rel.txt, and an EXIT action's `pwd` is the last directory of the
    line), a DEBUG action before every command.  It is therefore read with the directories unknown, as a sourced file is, so
    a relative redirection or tee inside it is refused for a member and left unchecked for Spud; the line's own directories
    are restored afterwards, since defining a trap changes nothing on the line.  The action's own assignments do not reach
    the rest of the line either -- they run later, and SPD-043's `a.all_doubt` after `trap`, which this ticket leaves alone,
    already doubts every variable (probed: a DEBUG action's `X=git` did reach the next command's expansion in all four
    shells, so the doubt, not the value, is what the hook keeps).  An action word holding an expansion the hook cannot
    resolve exactly is refused as SPD-043 refuses one; a value the line assigned resolves as SPD-043 resolves it.
    AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        for rel in ("tests/keep.py", "ledger/tickets/SPD-001.md"):
            p = self.home.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")

    def refused_for_members(self, command, needle="Law 7", cwd=None):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id, cwd)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None, cwd=cwd)
        return r

    def silent_for_everyone(self, command, cwd=None):
        for agent_id in (AGENT_C, AGENT_A, None):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertSilent(command, agent_id, cwd)

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def findings(self, command):
        return self.analysis(command).findings

    def test_the_tickets_evidence_commands(self):
        for cmd in ("trap 'git push' EXIT", 'trap "git commit -m x" ERR', "trap 'cd /tmp && git add .' DEBUG",
                    "trap 'eval \"git push\"' 0", "trap 'sh -c \"git push\"' INT"):
            self.refused_for_members(cmd)
        self.assertEqual(self.findings("trap 'git push' EXIT"), [("git", ("push", "push"))])
        self.assertEqual(self.findings("trap 'git status' EXIT"), [("git", ("status", None))])

    def test_every_signal_spelling(self):
        for sig in ("EXIT", "0", "1", "15", "INT", "SIGINT", "sigint", "TERM", "HUP", "USR1", "ERR", "DEBUG", "RETURN",
                    "INT TERM EXIT", "EXIT INT", "1 2 15"):
            with self.subTest(sig):
                self.refused_for_members("trap 'git push' %s" % sig)

    def test_the_forms_that_run_nothing_stay_silent(self):
        for ok in ("trap", "trap -p", "trap -l", "trap -lp", "trap -p EXIT", "trap -P EXIT", "trap -",
                   "trap - EXIT", "trap - INT TERM", "trap '' EXIT", 'trap "" INT TERM', "trap -- '' EXIT",
                   "trap -- - EXIT", "trap EXIT", "trap git", "trap 'echo done' EXIT", "trap 'git status' EXIT",
                   "trap 'git log --oneline -5' EXIT", "trap 'rm -f /tmp/x' EXIT", "trap 'cd /tmp' EXIT",
                   "trap 'X=1' EXIT", "trap 'echo x > /tmp/out.txt' EXIT", "echo trap", "grep -n trap tests/keep.py"):
            self.silent_for_everyone(ok)
        self.assertEqual(self.findings("trap -p"), [])
        self.assertEqual(self.findings("trap - EXIT"), [])

    def test_both_action_positions_are_read(self):
        """`--` and bash's options are skipped, and the word right after `trap` is read too, since zsh takes it for the
        action.  Only `trap -- 'git push' EXIT` runs in the shells; the rest are read fail closed."""
        for cmd in ("trap -- 'git push' EXIT", "trap -p 'git push' EXIT", "trap -l 'git push' EXIT",
                    "trap -P 'git push' EXIT", "trap -x 'git push' EXIT", "trap --p 'git push' EXIT",
                    "trap -lp 'git push' EXIT", "trap 'git push'", 'trap "git push"'):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_the_command_word_reached_another_way(self):
        for cmd in ("builtin trap 'git push' EXIT", "command trap 'git push' EXIT", "time trap 'git push' EXIT",
                    "noglob trap 'git push' EXIT", "X=trap; $X 'git push' EXIT", "X=trap; ${X} 'git push' EXIT",
                    "tr?p 'git push' EXIT", "tr*p 'git push' EXIT", "'trap' 'git push' EXIT", "t\"\"rap 'git push' EXIT",
                    "tr\\ap 'git push' EXIT"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        for ok in ("env trap 'git push' EXIT", "nohup trap 'git push' EXIT", "exec trap 'git push' EXIT",
                   "sudo trap 'git push' EXIT", "xargs trap 'git push' EXIT", "nice trap 'git push' EXIT",
                   "/usr/bin/trap 'git push' EXIT", "TRAP 'git push' EXIT",
                   "traps 'git push' EXIT", "echo trap 'git push' EXIT"):
            self.silent_for_everyone(ok)
        # no trap either, but a file of the checkout's run by its path: a member's file of commands since SPD-145
        self.refused_for_members("./trap 'git push' EXIT", SCRIPT_WORDING)
        self.assertSilent("./trap 'git push' EXIT", None)

    def test_a_coproc_or_a_pipeline_forks_a_shell_where_the_builtin_runs(self):
        """Round 2 of Spud's review: `coproc` is not an external wrapper -- it runs its command in a forked shell of its
        own, where the builtin does run and whose exit fires the action.  Probed in zsh 5.9 -f and zsh -f -o nobareglobqual
        with a fake git on a scratch PATH: `coproc { trap 'git push' EXIT; }`, `coproc ( trap 'git push' EXIT )`,
        `cat /dev/null | trap 'git push' EXIT` and `{ trap 'git push' EXIT; } | cat` each ran git push, and `coproc
        { trap 'echo x > rel.txt' EXIT; }` wrote the file; bash 3.2 and sh have no coproc (a syntax error there) and ran
        nothing for any of them.  `coproc trap 'git push' EXIT`, the simple command, ran nothing in any of the four and is
        read fail closed all the same.  A cd under coproc still changes nothing the line can see: the forked shell's
        directory never comes back, so its effect settles as an external program's does."""
        for cmd in ("coproc { trap 'git push' EXIT; }", "coproc trap 'git push' EXIT",
                    "coproc ( trap 'git push' EXIT )", "cat /dev/null | trap 'git push' EXIT",
                    "{ trap 'git push' EXIT; } | cat", "coproc { trap 'eval \"git push\"' EXIT; }"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        for agent_id in (AGENT_C, AGENT_A, None):  # since SPD-035 a target the hook cannot place refuses Spud too
            self.assertRefused("coproc { trap 'echo x > out.txt' EXIT; }", "cannot follow", agent_id)
        self.silent_for_everyone("coproc { trap 'echo done' EXIT; }")
        # a cd under coproc is unchanged: the forked shell's directory is not the line's
        for cmd in ("coproc { cd /tmp; }", "coproc cd /tmp", "coproc { trap 'cd /tmp' EXIT; }"):
            with self.subTest(cmd):
                self.assertEqual(self.analysis(cmd).cwds, frozenset([str(self.home.path)]))
        self.silent_for_everyone("coproc { cd /tmp; }")

    def test_the_line_inside_every_construct_that_recurses(self):
        for cmd in ("eval \"trap 'git push' EXIT\"", "sh -c \"trap 'git push' EXIT\"", "bash -lc \"trap 'git push' EXIT\"",
                    "zsh -c \"trap 'git push' EXIT\"", "f() { trap 'git push' EXIT; }; f", "( trap 'git push' EXIT )",
                    "{ trap 'git push' EXIT; }", "for i in 1; do trap 'git push' EXIT; done",
                    "if true; then trap 'git push' EXIT; fi", "true && trap 'git push' EXIT",
                    "echo $(trap 'git push' EXIT)", "trap \"trap 'git push' EXIT\" DEBUG",
                    "sh <<'EOF'\ntrap 'git push' EXIT\nEOF"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_an_expansion_in_the_action_word(self):
        for cmd in ('trap "$X" EXIT', "trap $X EXIT", 'trap "$(echo git push)" EXIT', "trap `echo git push` EXIT",
                    "trap ${X:-'git push'} EXIT", "trap ${X} EXIT", "X=g; trap $X$Y EXIT", "X=it; trap \"g$X\" EXIT",
                    "trap $'\\u0067it push' EXIT", 'trap -- "$X" EXIT', "trap \"$(cat f)\" EXIT"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "cannot resolve")
        for cmd in ("X='git push'; trap \"$X\" EXIT", "X='git push'; trap $X EXIT", "X='git push'; trap ${X} EXIT",
                    "X=push; trap \"git $X\" EXIT"):
            with self.subTest(cmd):  # the value the line assigned, read as bash splits it and as zsh keeps it
                self.refused_for_members(cmd)

    def test_the_action_runs_where_the_hook_cannot_know_the_directory(self):
        # A trap's action runs wherever the shell stands when the signal fires, so a relative target in it lands in a
        # directory the hook cannot know: refused for a member, and since SPD-035 for Spud too (Laws 1 and 5 hold for him).
        for cmd in ("trap 'echo x > out.txt' EXIT", "cd /tmp && trap 'echo x >> out.txt' EXIT",
                    "trap 'echo x | tee out.txt' EXIT", "trap 'echo x > tests/keep.py' EXIT"):
            with self.subTest(cmd):
                for agent_id in (AGENT_C, AGENT_A, None):
                    self.assertRefused(cmd, "cannot follow", agent_id)
        # the action's own absolute cd settles its directory again, and the target is checked there
        self.silent_for_everyone("trap 'cd /tmp; echo x > out.txt' EXIT")
        absolute = "trap 'echo x > %s/ledger/tickets/SPD-001.md' EXIT" % self.home.path
        self.assertRefused(absolute, "ledger/tickets", AGENT_C)
        self.assertRefused(absolute, "Law 1", agent_id=None)  # Spud keeps his own checks inside the action
        self.assertEqual([c for _, c in self.analysis("trap 'echo x > out.txt' EXIT").redirects], [None])
        # defining a trap changes nothing on the line: its directories and its variables are the line's own
        self.assertEqual(self.analysis("trap 'cd /tmp' EXIT").cwds, frozenset([str(self.home.path)]))
        self.assertEqual(self.analysis("X=git; trap 'X=ls' EXIT").vars["X"], "git")
        self.assertSilent("trap 'cd /tmp' EXIT; echo x > tests/keep.py")  # the member's own deliverable, from the line's directory
        self.assertSilent("trap 'cd /tmp' EXIT; echo x > CLAUDE.md", agent_id=None)  # Spud's own file, from the line's directory

    def test_a_spud_call_in_the_action(self):
        spud = self.spud_cli
        self.refused_for_members("trap '%s --as spud ticket new --title x' EXIT" % spud, "Law 6")
        self.refused_for_members("trap '%s init' EXIT" % spud, "Law 6")
        self.assertRefused("trap '%s --as %s member log hi' EXIT" % (spud, AGENT_B), "--as", AGENT_A)
        self.assertRefused("trap '%s --as %s member log hi' EXIT" % (spud, AGENT_A), "Law 5", agent_id=None)
        self.assertRefused("trap 'sqlite3 %s/.spud/ledger.db \"select 1\"' EXIT" % self.home.path, "spud sql --readonly", AGENT_A)
        # a trap is not a spud call, so the line is never allowed, not even one the caller may make
        self.assertSilent("trap '%s --as %s member log hi' EXIT" % (spud, AGENT_A))
        self.assertAllowed("%s --as %s member log hi" % (spud, AGENT_A))

    def test_all_doubt_after_a_trap_is_what_spd_043_left(self):
        self.assertIn("may not hold", self.refused_for_members("X=ls; trap 'X=git' DEBUG; $X push", "").reason)
        self.assertIn("Law 7", self.refused_for_members("X=git; trap 'X=ls' DEBUG; $X push", "").reason)
        self.assertIn("may not hold", self.refused_for_members("X=ls; trap 'echo hi' EXIT; $X push", "").reason)


# SPD-189: the words in which zsh's (e) flag evaluates the value of x, which the line settles, each read as the text zsh
# runs (EvalFlagTest has the probes): the flag alone, repeated, beside `@`, nested either way, through (P), and in every
# place a word stands -- an argument, one glued to text, an assignment's value, a command's prefix, a case's word and
# pattern, a condition, a for list, arithmetic, a here-string.
EVAL_FLAG_WORDS = (
    "echo ${(e)x}", 'echo "${(e)x}"', 'echo "e1=${(e)x}"', "echo a${(e)x}b", "echo ${(ee)x}", "echo ${(@e)x}",
    "echo ${(e)${x}}", "echo ${${(e)x}}", 'echo ${(e)"${x}"}', "n=x; echo ${(Pe)n}", "n=x; echo ${(e)${(P)n}}",
    "y=${(e)x}", "y=${(e)x} true", ": ${(e)x}", "echo ${(e)x} > /dev/null", "case ${(e)x} in *) true;; esac",
    "case q in ${(e)x}) true;; esac", "[[ -n ${(e)x} ]]", "for f in ${(e)x}; do true; done", "echo $(( ${(e)x} + 1 ))",
    "(( ${(e)x} ))", "cat <<< ${(e)x}",
)
# ... and the ones whose other flags, modifiers or subscripts change the value before (e) evaluates it: the hook reads
# the value as it is spelled and refuses a member besides, since the text zsh evaluates may differ (a case flag, a removal
# that takes a backslash away, a replacement that writes a `$`)
EVAL_FLAG_CHANGED = (
    "echo ${(ej:,:)x}", "echo ${(Le)x}", "echo ${(e)x:-z}", "echo ${(e)x[1,40]}", "echo ${(e)~x}", "echo ${(e)^x}",
    "echo ${(Qe)x}", "echo ${(%e)x}",
)
# The value's spellings, `%s` its command.  (e) reads the value as the inside of double quotes -- its quotes are text,
# a backslash escapes the next character -- and runs every `$( )` and backtick body in it, a default word's and an
# arithmetic expansion's included, and the value of an (e) expansion it holds.
EVAL_FLAG_VALUES = (
    "x='$(%s)'", 'x="\\$(%s)"', "x='`%s`'", 'x="\\`%s\\`"', "x='\"$(%s)\"'", "x=\"'\\$(%s)'\"", "x='\\\\$(%s)'",
    "x='${zz:-$(%s)}'", "x='$((1+$(%s)))'", "x='$(true\n%s)'", "y='$(%s)'; x='${(e)y}'", "x='$(true)'; x='$(%s)'",
)


class EvalFlagTest(BashHookCase):
    """SPD-189, filed by SPD-184's engineer: zsh's (e) parameter flag performs parameter expansion, command substitution
    and arithmetic expansion on the value it expands, so a line that assigns shell text and expands it with (e) runs that
    text, and the hook read nothing of it.  The ticket's evidence: `x='$(git push)'; echo ${(e)x}` and the same with `case
    ${(e)x} in *) true;; esac` recorded no finding (Law 7 for members), and a value the line does not spell (`x=$(cat f)`)
    is the same hole.  Unquoted, the hook did not even see the expansion: shlex ended the word at the flags' `(`, so
    `echo ${(e)x}` was read as `echo ${`, a subshell running `e`, and a command named `x}`.

    Probed 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, which printed the same for every line, and GNU bash 3.2.57, which fails every one of them with "bad
    substitution", each value a `$(touch <file>)`:

    - (e) runs the value's substitutions wherever the expansion stands: an argument (`echo ${(e)x}`, quoted or not, glued
      to text), a redirection's target, an assignment's value, a command's prefix assignment, a case's word and pattern,
      `[[ ]]`, a for list, `:`'s argument, a here-string, an arithmetic expansion and an arithmetic command (`$((
      ${(e)x} + 1 ))`, `(( ${(e)x} ))`), and inside eval, a substitution, backticks, a branch, a group, a function body,
      a loop, a subshell, a pipeline, `time`, and `zsh -f -c` with x exported (every word of EVAL_FLAG_WORDS and of
      EVAL_FLAG_CHANGED and each enclosing text of test_every_enclosing_text made its file, and so did each value of
      EVAL_FLAG_VALUES under `echo "${(e)x}"`, its double-quoted backtick as `x="\\`touch h5\\`"`);
    - with other flags too -- `(ee)`, `(@e)`, `(Pe)` through a name, `(ej:,:)`, `(Qe)`, `(%e)`, and `(Le)` and `(eL)`,
      the case flag applied first (`$(TOUCH R9)` made r9) -- and nested, `${(e)${x}}`, `${${(e)x}}`, `${(e)"${x}"}`,
      `${(e)${(P)n}}`, `${(e)${:-...}}`, `${(e):-...}`, and `${(e)${(e)x}}`, which evaluates twice (`\\$(touch k4)` made
      k4); with modifiers and subscripts, which change the text first: `${(e)x:-z}`, `${(e)x[1,20]}`, `${(e)~x}`,
      `${(e)^x}`, and `${(e)x#\\\\}` and `${(e)x/X/\\$}` made a substitution the value did not hold (`\\$(touch r11)` and
      `X(touch r13)`), while `(l(10)(x)e)` cut the `$` off `$(touch k9)` and ran nothing;
    - (e) reads the value as the inside of double quotes: `'$(touch re3)'`, `"$(touch re5)"`, `"$(touch q9)` and backticks
      ran, their quotes printed as text; `\\$(touch re4)` did not run and printed `$(touch re4)`, `\\\\$(touch q2)` ran;
      a default word's substitution (`${zz:-$(touch q5)}`), one inside arithmetic (`$((1+$(touch q6; echo 1)))`) and each
      line of a value that holds a newline ran; an (e) expansion in the value ran its own (`x='${(e)y}'`), a plain `$y`
      there did not (`$(touch re9)` printed); a value that expands itself (`x='${(e)x}'`) never returned;
    - `(j:e:)` ran nothing: the `e` there is the separator, not a flag; `${(e)#x}` printed the length and `(qe)` the quoted
      value, running nothing, and `${(P)n}` printed x's value unevaluated; `(foo bar)` failed "error in flags";
    - a command's prefix assignment does not reach its own words (`x=a; x='$(touch w2)' echo "${(e)x}"` printed a, and a
      redirection made `aw3f`), while an assignment-only command's later word sees an earlier one (`x=a; x='$(touch w1)'
      y=${(e)x}` made w1), so the hook reads both values and refuses a member.

    AGENT_A plans tests/** and bin/spud; AGENT_C plans home:**."""

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

    def unreadable(self, line):
        """Both members are refused the value the hook cannot read; Spud reads on."""
        with self.subTest(line=line):
            self.assertIn("eval-flag", [kind for kind, _detail in self.analysis(line).findings])
            self.assertRefused(line, "zsh's (e) flag")
            self.assertRefused(line, "zsh's (e) flag", AGENT_C)
            self.assertSilent(line, agent_id=None)

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_is_read_as_zsh_runs_it(self):
        for line in ("x='$(git push)'; echo ${(e)x}", "x='$(git push)'; case ${(e)x} in *) true;; esac"):
            self.law_7(line)
        self.unreadable("x=$(cat f); echo ${(e)x}")
        line = "x='$(touch re1)'; echo \"e1=${(e)x}\""
        with self.subTest(line=line):
            self.assertRefused(line, "deliverables")
            self.assertSilent(line, AGENT_C)
            self.assertRefused(line, "Law 1", agent_id=None)

    def test_the_word_stays_whole(self):
        """shlex ended an unquoted word at the flags' `(`: the flags are read as part of the expansion, in both readings."""
        line = "echo ${(e)x} a${(Pe)n}b ${(j:,:)x} ${(e)${(P)n}} ${(l(10)(x)e)x} \"${(e)x}\""
        words = [self.m.deglob(t) for t in self.m.shell_tokens(self.m.mark_zsh_patterns(line)[0])]
        self.assertEqual(words, ["echo", "${(e)x}", "a${(Pe)n}b", "${(j:,:)x}", "${(e)${(P)n}}", "${(l(10)(x)e)x}", "${(e)x}"])
        self.assertEqual(self.m.mark_zsh_patterns(line)[0], self.m.mark_zsh_patterns(line)[1])
        self.assertEqual(self.analysis("echo ${(e)x}").findings, [("eval-flag", "${(e)x}")])
        self.assertEqual(self.analysis("echo ${(P)n} ${(L)x}").findings, [])

    # -- the hole -----------------------------------------------------------------------------------------------------
    def test_every_word_that_evaluates_a_settled_value(self):
        for word in EVAL_FLAG_WORDS:
            self.law_7("x='$(git push)'; " + word)

    def test_every_spelling_of_the_value(self):
        for value in EVAL_FLAG_VALUES:
            for word in EVAL_FLAG_WORDS:
                line = value % "git push" + "; " + word
                with self.subTest(line=line):
                    self.assertIn(("git", ("push", "push")), self.analysis(line).findings)
            for word in ("echo ${(e)x}", "case ${(e)x} in *) true;; esac", "(( ${(e)x} ))"):
                self.law_7(value % "git push" + "; " + word)

    def test_a_changed_value_is_read_as_spelled_and_refuses_a_member(self):
        for word in EVAL_FLAG_CHANGED:
            self.law_7("x='$(git push)'; " + word)
            self.unreadable("x='$(date)'; " + word)
        # a removal that takes the escaping backslash away runs what the value only spells (probed: r11), which the hook
        # reads as it reads every escaped `$` (test_an_escaped_dollar_is_read_both_ways); a replacement that writes a `$`
        # (r13) it cannot read
        self.law_7("x='\\$(git push)'; echo ${(e)x#\\\\}")
        self.unreadable("x='X(git push)'; echo ${(e)x/X/\\$}")

    def test_every_enclosing_text(self):
        for form in ("eval 'echo %s'", "echo $(echo %s)", "echo `echo %s`", "if true; then echo %s; fi", "{ echo %s }",
                     "f() { echo %s; }; f", "for f in a; do echo %s; done", "(echo %s)", "true && echo %s",
                     "echo %s | cat", "time echo %s"):
            self.law_7("x='$(git push)'; " + form % "${(e)x}")
        self.law_7("export x='$(git push)'; zsh -f -c 'echo ${(e)x}'")

    def test_every_payload(self):
        """Law 7, Law 6, Law 5's --as, the database and Law 1 for both members; for Spud, the checks that apply to him."""
        home, spud, form = self.home.path, self.spud_cli, 'x="\\$(%s)"; echo ${(e)x}'
        writes = (("echo x > ledger/tickets/SPD-001.md", "generated"), ("echo x | tee ledger/tickets/SPD-001.md", "generated"),
                  ("touch ledger/tickets/SPD-001.md", "generated"))
        for command, needle in (("git push", "Law 7"), ("%s ticket new --title x" % spud, "Law 6"),
                                ("%s --as spud member log hi" % spud, "Law 6"),
                                ("%s --as %s member log hi" % (spud, AGENT_B), "--as"),
                                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly")) + writes:
            for agent_id in (AGENT_C, AGENT_A):
                with self.subTest(line=form % command, agent_id=agent_id):
                    self.assertRefused(form % command, needle, agent_id)
        for command, needle in (("%s --as %s member log hi" % (spud, AGENT_A), "--as"),
                                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly")):
            with self.subTest(line=form % command, agent_id="spud"):
                self.assertRefused(form % command, needle, agent_id=None)
        for command, _needle in writes:
            with self.subTest(line=form % command, agent_id="spud"):
                self.assertRefused(form % command, "Law 1", agent_id=None)

    def test_the_path_rule_in_the_value(self):
        for word in ("echo ${(e)x}", "case q in ${(e)x}) true;; esac", "y=${(e)x}", "echo \"${(e)${x}}\""):
            for write in ("touch note.txt", "rm -rf docs"):
                line = "x='$(%s)'; %s" % (write, word)
                with self.subTest(line=line):
                    self.assertRefused(line, "deliverables")
                    self.assertSilent(line, AGENT_C)
                    self.assertRefused(line, "Law 1", agent_id=None)
            line = "x='$(touch tests/zzone/k.py)'; " + word
            with self.subTest(line=line):
                self.assertSilent(line)
                self.assertSilent(line, AGENT_C)

    def test_a_value_the_line_does_not_settle_refuses_a_member(self):
        for line in ("x=$(cat f); echo ${(e)x}", "x=`cat f`; echo ${(e)x}", 'x="$(cat f)"; echo "${(e)x}"',
                     "echo ${(e)x}", "echo ${(e)HOME}", "x=$y; echo ${(e)x}", "read x; echo ${(e)x}",
                     "true && x='$(date)'; echo ${(e)x}", "for x in a; do echo ${(e)x}; done",
                     "x='$(date)'; for f in a; do echo ${(e)x}; done", "x='$(date)'; f() { echo ${(e)x}; }; f",
                     "x=a; x+=b; echo ${(e)x}", "x=$'\\u0024(date)'; echo ${(e)x}", "x=(a '$(date)'); echo ${(e)x}",
                     "echo ${(e)$(cat f)}", 'echo ${(e)"$(cat f)"}', "echo ${(e):-$y}", "echo ${(e)1} ${(e)@}",
                     "n=HOME; echo ${(Pe)n}", "n=$(cat f); x='$(date)'; echo ${(Pe)n}", "x='\\$(date)'; echo ${(e)${(e)x}}",
                     "x=a; x='$(date)' y=${(e)x}", "x='$(date)'; echo ${x::=b} ${(e)x}", "echo hi > ${(e)x}f",
                     "x='$(date)'; echo $(( ${(e)x} )) ${(e)x#a}", "x='$(date)'; echo ${(e)#x}", "x='$(date)'; echo ${(qe)x}",
                     "x='$(date)'; echo ${(e)x", "x='$(date)'; echo \"${(foo bar)x}\""):
            with self.subTest(line=line):
                self.assertIn("eval-flag", [kind for kind, _detail in self.analysis(line).findings])
                self.assertRefused(line, "zsh's (e) flag")
                self.assertRefused(line, "zsh's (e) flag", AGENT_C)
        for line in ("x=$(cat f); echo ${(e)x}", "echo ${(e)HOME}", "true && x='$(date)'; echo ${(e)x}",
                     "x=a; x='$(date)' y=${(e)x}", "n=HOME; echo ${(Pe)n}"):
            with self.subTest(line=line, agent_id="spud"):
                self.assertSilent(line, agent_id=None)

    def test_spud_reads_every_value_the_line_spells(self):
        """A value the line spells but may not hold where it is expanded is read all the same, for Spud's writes: the
        member's refusal says only that the hook cannot be sure of it."""
        for line in ("true && x='$(echo x > ledger/tickets/SPD-001.md)'; echo ${(e)x}",
                     "x='$(echo x > ledger/tickets/SPD-001.md)'; f() { echo ${(e)x}; }; f",
                     "x=a; x='$(echo x > ledger/tickets/SPD-001.md)' y=${(e)x}",
                     "x='$(echo x > ledger/tickets/SPD-001.md)'; echo ${(Le)x}"):
            with self.subTest(line=line):
                self.assertRefused(line, "Law 1", agent_id=None)
                self.assertRefused(line, "(e) flag")

    def test_an_escaped_dollar_is_read_both_ways(self):
        """shlex leaves the backslash of `"\\$"` in the word, where the shell takes it off, so `x="\\$(git push)"`, which
        (e) runs (probed: `x="\\$(touch h4)"` made h4), reaches the hook as `x='\\$(git push)'` does, which (e) does not
        run (re4): both are read as the first, fail closed, until the masked word holds what the shell passes (proposal
        301, filed with SPD-189).  The same for a backtick (`x="\\`touch h5\\`"` made h5)."""
        for value in ("x='\\$(git push)'", 'x="\\$(git push)"', "x='\\`git push\\`'", 'x="\\`git push\\`"'):
            self.law_7(value + "; echo ${(e)x}")

    def test_what_evaluates_to_nothing_it_runs(self):
        """Probed (see the class): a plain parameter expansion in the value, a separator that spells `e`, a literal or
        escaped expansion, no (e) at all."""
        for line in ("x='$y'; y='$(git push)'; echo ${(e)x}", "x='$(git push)'; echo ${x}",
                     "x=$(cat f); echo ${(j:e:)x}", "x='$(git push)'; echo '${(e)x}'", "x='$(git push)'; echo \\${(e)x}",
                     "x=hello; echo ${(e)x}", "x='$HOME/a'; echo \"${(e)x}\"", "x='$(date)'; echo ${(e)x}",
                     "x='$(git push)'; n=x; echo ${(L)x} ${(P)n}", "x=\"'\"; echo ${(e)x}"):
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).findings, [])
                self.assertSilent(line)
                self.assertSilent(line, AGENT_C)
                self.assertSilent(line, agent_id=None)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        """A value that expands itself never returns in zsh (probed), and a chain of values each expanding the next one is
        read to the analysis's depth bound: past it a member is refused, as it is for a value it cannot read."""
        chain = "; ".join(["y0='$(git push)'"] + ["y%d='${(e)y%d}'" % (k, k - 1) for k in range(1, 40)])
        for line, finding in (("x='${(e)x}'; echo ${(e)x}", "eval-flag"),
                              ("x='$(git push)'; echo " + "${(e)x} " * 3000, "git"),
                              ("x='$(git push)'; echo " + "${(e)" * 3000 + "x" + "}" * 3000, "eval-flag"),
                              ("x='" + "${(e)x}" * 3000 + "'; echo ${(e)x}", "eval-flag"),
                              ("x='$(git push)'; y='" + "${(e)x}" * 3000 + "'" + "; echo ${(e)y}" * 50, "git"),
                              ("x='" + "$(" * 2000 + "git push" + ")" * 2000 + "'; echo ${(e)x}", None),
                              (chain + "; echo ${(e)y39}", "eval-flag"),
                              ("echo " + "${(e" * 3000, None),
                              ("echo " + "${(j:" * 3000, None),
                              ("echo " + "${(l(" * 3000 + "e)x}", None),
                              ("echo " + "${(e)x}" * 3000 + " " + "${(j" + ":" * 3000, None)):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = self.analysis(line)
                self.assertLess(time.monotonic() - started, 5.0)
                if finding is not None:
                    self.assertIn(finding, [kind for kind, _detail in a.findings], line[:40])


# SPD-190: the file name a `<( list )` hands its command, `%s`, where each reader of hookio.SUBST reads it -- a redirection
# target, a tee operand and a write by argument; the directory cd, pushd, `cd old new` and env -C move to; find's and
# xargs's operands; a shell's script, a sourced file and a shell's own startup file; the command word and a wrapper's;
# git's options, verb and arguments; a spud call's words; an interpreter's program; eval's and a builtin's words.  Each
# reads it as it reads a `$( list )` in its place: a word the line does not spell.
PROCSUB_OPERAND_FORMS = (
    "echo x > %s", "echo x | tee %s", "echo x | tee -a docs/y %s", "cp docs/x %s", "mv %s docs/y", "touch %s", "rm -rf %s",
    "chmod -R 644 %s", "sed -i s/a/b/ %s", "dd if=docs/x of=%s", "tar -xf %s", "rsync -a docs/ %s",
    "cd %s", "cd -P %s", "pushd %s", "cd docs %s", "env -C %s touch y",
    "find %s -delete", "find docs -newer %s -delete", "xargs rm %s", "xargs -a %s rm",
    "sh %s", "bash %s arg", "source %s", ". %s", "bash --rcfile %s -i", "env sh %s", "xargs sh %s",
    "%s", "%s arg", "nice %s", "env %s", "exec %s", "command %s",
    "git %s push", "git -C %s push", "git log %s", "git diff --output %s", "git commit -F %s", "git -c %s push",
    "bin/spud %s", "bin/spud --as %s member log hi",
    "python3 %s", "node %s", "perl %s", "awk -f %s docs/x", "sed -f %s docs/x",
    "eval %s", "export X %s", "x=1 %s", "typeset %s",
)
# ... and the lines whose `$( )`, backticks or eval text after a process substitution runs in the directory a cd between
# them moved to (probed; see the class), each writing the generated ledger file there
PROCSUB_BEFORE_CD = (
    "cat <(true) && cd ledger && echo $(echo x > tickets/SPD-001.md)",
    "true >(true) && cd ledger && echo $(echo x > tickets/SPD-001.md)",
    "true =(true) && cd ledger && echo $(echo x > tickets/SPD-001.md)",
    "true <(true) >(true) && cd ledger && echo $(echo x > tickets/SPD-001.md)",
    "diff <(true) <(true); cd ledger && echo $(echo x > tickets/SPD-001.md)",
    "for f in <(true); do true; done; cd ledger && echo $(echo x > tickets/SPD-001.md)",
    "select f in <(true); do break; done; cd ledger && echo $(echo x > tickets/SPD-001.md)",
    "true <(true) && cd ledger && echo `echo x > tickets/SPD-001.md`",
    'true <(true) && cd ledger && echo "$(echo x > tickets/SPD-001.md)"',
    "true <(true) && cd ledger && x=$(echo x > tickets/SPD-001.md)",
    "true <(true) && cd ledger && echo ${z:-$(echo x > tickets/SPD-001.md)}",
    "cat <(true) - <<EOF && cd ledger && echo $(echo x > tickets/SPD-001.md)\nbody\nEOF",
    "eval cat <(true) '; cd ledger && echo $(echo x > tickets/SPD-001.md)'",
    "echo $(cat <(true); cd ledger; echo $(echo x > tickets/SPD-001.md))",
)
# ... and those with a substitution of their own before the cd, which runs where the line started
PROCSUB_AND_EARLIER = (
    "true <(true) $(echo q > tests/zzone/q) && cd ledger && echo $(echo x > tickets/SPD-001.md)",
    "cat <(echo $(echo q > tests/zzone/q)) && cd ledger && echo $(echo x > tickets/SPD-001.md)",
    "true <(true) `echo q > tests/zzone/q`; cd ledger && echo `echo x > tickets/SPD-001.md`",
    "diff <(true) <(true) $(echo q > tests/zzone/q); cd ledger && echo $(echo x > tickets/SPD-001.md)",
)


class ProcessSubstitutionFileTest(BashHookCase):
    """SPD-190, filed by SPD-184's engineer: ShellWalk.pop put hookio.SUBST among a command's words for the file name a
    `<( list )` hands it (SPD-145), and ShellWalk.consume analyses one lifted `$( )` or backtick body for every SUBST a word
    holds, so that file name took the first body after it on the line: analysed with the process substitution's command,
    before any cd between them, while the `$( )` it belonged to found none.  The proposer's evidence, cwd /tmp: `cd /usr &&
    echo $(echo hi > y)` recorded y in /usr, and `cat <(true) && cd /usr && echo $(echo hi > y)` in /tmp; from the home,
    `cat <(true) && cd ledger && echo $(echo x > tickets/SPD-001.md)` resolved the target to <home>/tickets/SPD-001.md, not
    the generated ledger file.  SPD-184 had fixed it in a case's word and pattern alone.  A for or select list, eval's text
    and a substitution's own body held the same hole.  The file name is now walk.PROCSUB_FILE, SUBST with a private-use
    mark after it, which every reader of SUBST takes for one and consume pairs with no body.

    Probed 2026-09-23 through tests/probes/shell_probe.py, in zsh 5.9 (arm64-apple-darwin26.0) under -f -o nobareglobqual
    and under -f, which printed the same for every line, and in GNU bash 3.2.57, each line with a directory d made first and
    its last `$( )` writing y: `cat <(true) && cd d && echo $(echo hi > y)`, the same after `true >(true)`, `true <(true)
    >(true)` and zsh's `true =(true)` (in an eval under `[ -n "$ZSH_VERSION" ]`, TMPPREFIX in the probe's directory: bash
    rejects the line), `for f in <(true); do true; done; cd d && ...`, `select f in <(true); do break; done < /dev/null; cd
    d && ...`, `eval cat <(true) '; cd d; echo $(echo hi > y)'`, `echo $(cat <(true); cd d; echo $(echo hi > y))`, backticks
    for both substitutions, a quoted `"$( )"`, `x=$( )`, `${z:-$( )}`, and a here-document on the `<( )`'s command (`cat
    <(echo proc) - <<EOF && cd d && ...`) each wrote y in d; `diff <(true) <(true) $(echo q > w); cd d && ...`, `cat <(echo
    $(echo a > z)) && cd d && ...` and `true <(true) $(echo q > q) && cd d && ...` wrote y in d and w, z and q where the
    line started.  `echo <(true)` printed /dev/fd/11 in zsh and /dev/fd/63 in bash.

    AGENT_A plans tests/** and bin/spud; AGENT_C plans home:**."""

    TAIL = "; cd ledger && echo $(echo x > tickets/SPD-001.md)"

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

    def plain(self, value):
        """A reading with the file name's word spelled as a substitution's."""
        if isinstance(value, str):
            return value.replace(self.m.PROCSUB_FILE, self.m.SUBST)
        if isinstance(value, dict):
            return {self.plain(k): self.plain(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return type(value)(self.plain(v) for v in value)
        return value

    def reading(self, line):
        """What the analysis reads of a line."""
        a = self.analysis(line)
        return self.plain((a.findings, a.kinds, a.redirects, a.git_calls, a.git_writes, a.arg_writes, a.cwds, a.unparseable,
                           sorted(a.doubt), sorted(a.dashless_loops), sorted(a.functions), sorted(a.hashed)))

    def ledger_target(self, line):
        """The analysis resolves the ledger file's write in the directory the cd moved to, and nowhere else."""
        with self.subTest(line=line):
            found = [(t, c) for t, c in self.analysis(line).redirects if t.endswith("SPD-001.md")]
            self.assertEqual(found, [("tickets/SPD-001.md", frozenset({str(self.home.path / "ledger")}))])

    def ledger_write(self, line):
        """Refused to both members for the generated ledger file, and to Spud on Law 1."""
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(line=line, agent_id=agent_id):
                self.assertRefused(line, "generated", agent_id)
        with self.subTest(line=line, agent_id="spud"):
            self.assertRefused(line, "Law 1", agent_id=None)

    # -- the ticket's evidence ----------------------------------------------------------------------------------------
    def test_the_tickets_evidence_is_read_where_the_shell_runs_it(self):
        for line in ("cd /usr && echo $(echo hi > y)", "cat <(true) && cd /usr && echo $(echo hi > y)"):
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).redirects, [("y", frozenset({"/usr"}))])
        line = "cat <(true) && cd ledger && echo $(echo x > tickets/SPD-001.md)"
        self.ledger_target(line)
        self.ledger_write(line)

    # -- the hole -----------------------------------------------------------------------------------------------------
    def test_every_substitution_after_a_process_substitution(self):
        for line in PROCSUB_BEFORE_CD:
            self.ledger_target(line)
        for line in PROCSUB_AND_EARLIER:
            self.ledger_target(line)
            with self.subTest(line=line):
                self.assertIn(("tests/zzone/q", frozenset({str(self.home.path)})), self.analysis(line).redirects)

    def test_the_hook_refuses_each_ledger_write(self):
        for line in PROCSUB_BEFORE_CD + PROCSUB_AND_EARLIER:
            self.ledger_write(line)

    def test_a_substitution_glued_to_text_keeps_its_body(self):
        """The file name's mark is no text a line spells after a `$( )` in the word it stands in."""
        for glued in ("x", "FILE__", "_x_", "'__'"):
            line = "echo $(echo q > tests/zzone/q)%s && cd ledger && echo $(echo x > tickets/SPD-001.md)" % glued
            self.ledger_target(line)
            with self.subTest(line=line):
                self.assertIn(("tests/zzone/q", frozenset({str(self.home.path)})), self.analysis(line).redirects)

    def test_many_file_names_and_substitutions_pair_up(self):
        line = "true" + " <(true) $(echo q > tests/zzone/q)" * 200 + self.TAIL
        self.ledger_target(line)
        self.assertIn(("tests/zzone/q", frozenset({str(self.home.path)})), self.analysis(line).redirects)

    # -- every reader, as it read the file name -----------------------------------------------------------------------
    def test_each_reader_takes_the_file_name_as_a_word_the_line_does_not_spell(self):
        """Read as its `$( )` twin in every place, and so with the ledger write after it (which the twin always read in
        ledger/)."""
        for form in PROCSUB_OPERAND_FORMS:
            for tail in ("", self.TAIL):
                line = form % "<(true)" + tail
                with self.subTest(line=line):
                    self.assertEqual(self.reading(line), self.reading(form % "$(true)" + tail))

    def test_the_hook_decides_each_reader_as_it_did(self):
        for form in ("sh %s", "source %s", "echo x | tee %s", "echo x > %s", "rm -rf %s", "cd %s && touch docs/x",
                     "git %s push", "git log %s", "find %s -delete", "xargs rm %s", "bin/spud %s", "%s arg", "python3 %s",
                     "env -C %s touch y", "diff <(ls tests) %s"):
            for agent_id in (AGENT_A, None):
                line = form % "<(true)"
                with self.subTest(line=line, agent_id=agent_id):
                    r, twin = self.bash(line, agent_id), self.bash(form % "$(true)", agent_id)
                    self.assertEqual((r.code, r.decision, self.plain(r.reason)), (twin.code, twin.decision, twin.reason))

    def test_a_case_pattern_keeps_no_file_name(self):
        """SPD-184's reading stays: a case's word and pattern are no command's (CaseSubstitutionTest), and a `|` there is
        an alternative (SPD-187, CasePatternAlternativeTest)."""
        for line in ("case x in y) ;; <(true)|x) true;; esac", "case x in y) ;; =(true)|x) true;; esac"):
            for agent_id in (AGENT_A, AGENT_C, None):
                with self.subTest(line=line, agent_id=agent_id):
                    self.assertSilent(line, agent_id)
        self.ledger_write("case x in <(true)) cd ledger && echo $(echo x > tickets/SPD-001.md);; esac")


class AliasEvalTest(BashHookCase):
    """SPD-059 (Burbank's SPD-054 proposal): `alias NAME=body` stores shell text the hook never read, and `eval NAME` on the
    same line ran it, so a member's VCS write behind an alias reached the hook with no finding (Law 7).  Probed in bash 3.2,
    zsh 5.9 -f, zsh -f -o nobareglobqual (this Mac's Bash tool) and sh, with a fake git first on a scratch PATH:

    - `alias gp='git push'; eval gp` pushed in zsh, zsh-nbgq and sh.  bash 3.2 expands no alias non-interactively without
      `shopt -s expand_aliases`, so it pushed nothing -- noted, and never relied on: the hook reads the line for the shells
      that do;
    - a shell expands an alias when it parses the text, before the line runs, so an alias defined on the line reaches only
      text the line parses again.  `alias g=git; g push` ran in none of the four, and neither did `eval 'sh -c gp'` or `eval
      'zsh -c gp'`; `eval 'echo $(gp)'` did, the substitution being parsed by the shell that holds the alias, and so did
      `eval 'eval gp'`, a function body's eval and an eval in a subshell;
    - the name is expanded in command position only: `eval 'X=1 gp'`, `eval 'time gp'`, `eval '! gp'` and `eval 'coproc gp'`
      ran it, `eval 'command gp'` and `eval 'env gp'` ran nothing.  An alias shadows a function of the same name;
    - the words after the name follow the body (`alias gp=git; eval 'gp push'` and `alias gp='git push'; eval gp extra` both
      pushed), the body may hold operators and redirections of its own, aliases chain, zsh's `alias -g` and several
      definitions on one `alias` line all take, and `unalias` and `unalias -a` clear.

    The body is now read as the shell text it is, with its own quotes, as eval's rejoined words and a trap's action are: a
    finding inside it is the finding it would be on the line.  A body the hook cannot read (it holds an expansion or a
    substitution, whose value is not on the line), a definition or an `unalias` that may not have run, and a definition whose
    name the hook cannot read each refuse a member where eval dispatches the name; Spud is refused by none of those, and
    keeps his own checks inside the body (Law 1, Law 5's `--as`, the database).  The body runs in the line's own shell, so a
    cd there moves the line, exactly as eval's own words do (probed: `alias cdd='cd /tmp'; eval cdd; pwd` printed /tmp, and
    `echo x > note.txt` after it wrote /tmp/note.txt).  AGENT_A plans tests/** and bin/spud; AGENT_C plans **.

    SPD-105: zsh's special `aliases` association defines an alias too, and the hook read it as nothing.  Probed in zsh 5.9 -f
    and -o nobareglobqual: `aliases[gp]='echo SH'; eval gp`, `aliases=(gp 'echo SH'); eval gp` and `typeset
    'aliases[gp]=echo SH'; eval gp` ran it, `galiases[GP]=` and `saliases[txt]=` define what `alias -g` and `alias -s` do,
    `dis_aliases[gp]=` a disabled alias that ran nothing, and `aliases[gp]=...; gp` ran nothing, as `alias` does.
    `aliases+=(gp 'echo SH'); eval gp` defined nothing in 5.9; it is recorded all the same, refusing on doubt.  Each
    element is now recorded as an `alias` line's `name=body` is, and a key the hook cannot read leaves every name in doubt."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        for rel in ("tests/keep.py", "ledger/tickets/SPD-001.md"):
            p = self.home.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")

    def refused_for_members(self, command, needle="Law 7", cwd=None):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id, cwd)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None, cwd=cwd)
        return r

    def silent_for_everyone(self, command, cwd=None):
        for agent_id in (AGENT_C, AGENT_A, None):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertSilent(command, agent_id, cwd)

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def test_the_tickets_evidence_command(self):
        self.refused_for_members("alias gp='git push'; eval gp")
        self.assertEqual(self.analysis("alias gp='git push'; eval gp").findings, [("git", ("push", "push"))])
        self.assertEqual(self.analysis("alias gp='git status'; eval gp").findings, [("git", ("status", None))])
        self.assertEqual(self.analysis("alias gp='git push'; eval gp").aliases, {"gp": "git push"})

    def test_every_spelling_of_the_definition(self):
        for cmd in ("alias gp='git push'; eval gp", 'alias gp="git push"; eval gp', "alias gp=git\\ push; eval gp",
                    "alias -g GP='git push'; eval GP", "alias -- gp='git push'; eval gp",
                    "alias gp='git push'\neval gp", "alias gp=git; eval 'gp push'",
                    "alias gp='git push'; eval \"gp\"", "alias gp='git push'; eval 'gp'",
                    "alias gp='git push'; eval gp extra", "alias gp='git '; eval 'gp push'",
                    "builtin alias gp='git push'; eval gp", "alias gp='git push'; builtin eval gp"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_several_definitions_on_one_alias_line(self):
        line = "alias gp='git push' gs='git status' e='echo hi'"
        self.refused_for_members(line + "; eval gp")
        self.assertEqual(self.analysis(line).aliases, {"gp": "git push", "gs": "git status", "e": "echo hi"})
        self.assertEqual(self.analysis(line + "; eval gs").findings, [("git", ("status", None))])
        self.silent_for_everyone(line + "; eval e")

    def test_the_body_is_read_as_the_shell_text_it_is(self):
        """Operators, redirections, a spud call and a nested construct inside the body are the findings they would be."""
        spud, home = self.spud_cli, self.home.path
        self.refused_for_members("alias gp='echo a; git push'; eval gp")
        self.refused_for_members("alias gp='true && git push'; eval gp")
        self.refused_for_members("alias gp='{ git push; }'; eval gp")
        self.refused_for_members("alias gp='if true; then git push; fi'; eval gp")
        self.refused_for_members("alias gp='sh -c \"git push\"'; eval gp")
        self.refused_for_members("alias gp='eval \"git push\"'; eval gp")
        self.refused_for_members("alias t='%s ticket new --title x'; eval t" % spud, "Law 6")
        self.assertRefused("alias l='%s --as %s member log hi'; eval l" % (spud, AGENT_B), "--as", AGENT_A)
        for agent_id in (AGENT_C, AGENT_A, None):  # the database is refused to everyone, Spud included
            self.assertRefused("alias d='sqlite3 %s/.spud/ledger.db \"select 1\"'; eval d" % home, "spud sql --readonly", agent_id)
        self.assertRefused("alias e='echo x > ledger/tickets/SPD-001.md'; eval e", "generated")
        self.assertRefused("alias e='echo x > ledger/tickets/SPD-001.md'; eval e", "Law 1", agent_id=None)
        self.assertRefused("alias e='echo x | tee ledger/tickets/SPD-001.md'; eval e", "generated")
        self.assertRefused("alias e='echo x > note.txt'; eval e", "deliverables")
        self.assertSilent("alias e='echo x > note.txt'; eval e", AGENT_C)
        # a cd in the body moves the line's own shell, as eval's own words do
        self.assertEqual(self.analysis("alias c='cd /tmp'; eval c").cwds, frozenset(["/tmp"]))
        # ... so a relative target after the eval is checked where the shell really is (probed: it wrote /tmp/note.txt)
        self.assertEqual([c for _, c in self.analysis("alias c='cd /tmp'; eval c; echo x > note.txt").redirects],
                         [frozenset(["/tmp"])])

    def test_the_line_inside_every_construct_that_reaches_the_alias(self):
        for cmd in ("alias gp='git push'; eval 'eval gp'", "alias gp='git push'; eval 'echo $(gp)'",
                    "alias gp='git push'; eval 'true && gp'", "alias gp='git push'; eval 'gp; gp'",
                    "alias gp='git push'; eval '{ gp; }'", "alias gp='git push'; eval 'if true; then gp; fi'",
                    "alias gp='git push'; eval 'for f in a; do gp; done'", "alias gp='git push'; eval 'X=1 gp'",
                    "alias gp='git push'; eval 'time gp'", "alias gp='git push'; eval '! gp'",
                    "alias gp='git push'; eval 'coproc gp'", "alias gp='git push'; f() { eval gp; }; f",
                    "alias gp='git push'; (eval gp)", "alias a=b; alias b='git push'; eval a",
                    "alias gp='git push'; X=gp; eval $X"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_a_definition_that_may_not_have_run_is_refused(self):
        """A branch, a subshell, a pipeline, a background list, a loop or function body, and an `unalias` in any of them:
        the hook cannot be sure which alias the shell holds, so it fails closed."""
        for cmd in ("if true; then alias gp='git status'; fi; eval gp",
                    "false && alias gp='git status'; eval gp",
                    "true || alias gp='git status'; eval gp",
                    "(alias gp='git status'); eval gp",
                    "alias gp='git status' | cat; eval gp",
                    "alias gp='git status' & wait; eval gp",
                    "for f in a; do alias gp='git status'; done; eval gp",
                    "f() { alias gp='git status'; }; f; eval gp",
                    "case x in x) alias gp='git status';; esac; eval gp",
                    "alias gp='git status'; if true; then unalias gp; fi; eval gp",
                    "alias gp='git status'; (unalias gp); eval gp",
                    "alias gp='git status'; unalias -m 'g*'; eval gp"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "cannot resolve")
        # a refusal the body itself earns keeps its own reason
        self.assertIn("Law 7", self.refused_for_members("if true; then alias gp='git push'; fi; eval gp", "").reason)

    def test_a_body_or_a_name_the_hook_cannot_read_is_refused(self):
        for cmd in ("alias gp=\"$UNSET git push\"; eval gp", "alias gp=\"$(echo git push)\"; eval gp",
                    "alias gp=\"`echo git push`\"; eval gp", "X=git; alias gp=\"$X push\"; eval gp",
                    "alias gp=$'\\u0067it push'; eval gp", "N=gp; alias $N='git push'; eval gp",
                    "alias ${N}='git push'; eval gp"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "cannot resolve")

    def test_unalias_clears(self):
        for ok in ("alias gp='git push'; unalias gp; eval gp",
                   "alias gp='git push'; unalias -a; eval gp",
                   "alias gp='git push' gs='git status'; unalias gp gs; eval gp"):
            self.silent_for_everyone(ok)
        self.assertEqual(self.analysis("alias gp='git push'; unalias gp").aliases, {"gp": None})

    def test_an_alias_reaches_no_further_than_the_shell_that_holds_it(self):
        """Nothing on the line but eval parses the name again, so nothing else expands it (each probed to run nothing)."""
        for ok in ("alias gp='git push'; gp", "alias g=git; g push", "alias gp='git push'; echo gp",
                   "alias gp='git push'; sh -c 'eval gp'", "alias gp='git push'; zsh -c 'eval gp'",
                   "alias gp='git push'; eval 'command gp'", "alias gp='git push'; eval 'env gp'",
                   # SPD-084 confirmed the alias command-position gate is right where a function's is not: zsh keeps a
                   # function lookup after noglob/-/exec but does not expand an alias there (each ran nothing, probed)
                   "alias gp='git push'; eval 'noglob gp'", "alias gp='git push'; eval '- gp'",
                   "alias gp='git push'; eval 'exec gp'",
                   "alias gp='git push'; sh -c gp", "alias gp='git push'; sh <<'EOF'\neval gp\nEOF",
                   "eval gp", "alias", "alias -p", "alias gp", "unalias gp", "echo alias gp='git push'",
                   "grep -n \"alias gp\" tests/keep.py", "env alias gp='git push'; eval gp"):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
                self.assertNotEqual(self.bash(ok, agent_id=None).decision, "deny", ok)
        # the words of a spud call are the call's, whatever they spell
        self.assertAllowed("%s --as %s member log \"alias gp='git push'; eval gp\"" % (self.spud_cli, AGENT_A))

    def test_an_aliasing_line_is_never_allowed_on_its_own(self):
        """`alias` is not a spud call, so a line that defines one is read, never allowed."""
        self.assertSilent("alias gp='git push'; %s --as %s member log hi" % (self.spud_cli, AGENT_A))
        self.assertAllowed("%s --as %s member log hi" % (self.spud_cli, AGENT_A))

    def test_the_zsh_aliases_parameter_defines_an_alias(self):
        for cmd in ("aliases[gp]='git push'; eval gp", "aliases=(gp 'git push'); eval gp", "aliases+=(gp 'git push'); eval gp",
                    "typeset 'aliases[gp]=git push'; eval gp", "galiases[GP]='git push'; eval GP",
                    "aliases[gp]='git push' true; eval gp", "aliases=(e 'echo hi' gp 'git push'); eval gp"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        self.assertEqual(self.analysis("aliases[gp]='git push'").aliases, {"gp": "git push"})
        self.assertEqual(self.analysis("aliases=(e 'echo hi' gp 'git push')").aliases, {"e": "echo hi", "gp": "git push"})
        self.assertEqual(self.analysis("aliases[gp]='git status'; eval gp").findings, [("git", ("status", None))])
        self.silent_for_everyone("aliases[e]='echo hi'; eval e")
        # a key or a body the hook cannot read, or a definition that may not have run
        for cmd in ("aliases[$k]='git status'; eval gp", "aliases+=($pairs); eval gp", "aliases[g.p]='git status'; eval gp",
                    "aliases[gp]+=' status'; eval gp", "aliases[gp]=\"$X\"; eval gp", "(aliases[gp]='git status'); eval gp",
                    "if true; then aliases[gp]='git status'; fi; eval gp"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "cannot resolve")
        # nothing but eval parses the name again, and a disabled alias defines nothing
        for ok in ("aliases[gp]='git push'; gp", "dis_aliases[gp]='git push'; eval gp", "echo $aliases[gp]"):
            with self.subTest(ok):
                self.assertNotEqual(self.bash(ok).decision, "deny", ok)
                self.assertNotEqual(self.bash(ok, agent_id=None).decision, "deny", ok)

    def test_spud_keeps_his_own_checks_inside_the_body(self):
        spud = self.spud_cli
        self.assertRefused("alias l='%s --as %s member log hi'; eval l" % (spud, AGENT_A), "Law 5", agent_id=None)
        self.assertRefused("alias h='%s hook PreToolUse'; eval h" % spud, "hook", agent_id=None)
        self.assertRefused("alias e='echo x | tee ledger/tickets/SPD-001.md'; eval e", "Law 1", agent_id=None)


class PathInForceTest(BashHookCase):
    """SPD-062: the hook reads a line's command words by name -- git, spud, python3.14, sqlite3, tee, a shell, a wrapper --
    and the shell then finds each of them on PATH, so a member that puts a directory of its own first runs its own program
    under a name the hook cleared (`PATH=<dir>:$PATH git status` runs <dir>/git).  Probed in zsh 5.9 -f, zsh -f -o
    nobareglobqual as the Bash tool runs it, bash 3.2 and sh, with a fake program in a scratch directory: a prefix
    assignment, a plain assignment, `export`, `typeset -x`, `declare -x`, `local -x` inside a function and
    `env PATH=... cmd` each ran the scratch copy in all four shells, `readonly PATH=...` in bash and sh (zsh refuses to
    write a read-only PATH), and zsh's `path=(<dir> $path)` and `path+=(<dir>)`, the array PATH is tied to.  The
    subscripted forms zsh also takes (`path[1]=<dir>`, `path[1,0]=(<dir>)`) ran it too; they reached the analysis as a
    command word rather than an assignment until SPD-085, and SubscriptAssignmentTest covers them.  bash's `hash -p
    <path> <name>` and zsh's `hash <name>=<path>` put a file of the line's choosing in the shell's command table for the
    same effect, and both ran it; so does an element of zsh's `commands` parameter (SPD-105, probed in zsh 5.9 -f and -o
    nobareglobqual: `commands[foo]=<path>; foo`, `commands+=(foo <path>)`, `commands=(foo <path>)` and `typeset
    'commands[foo]=<path>'` each ran the file, `command foo` too, while `env foo` and `/usr/bin/env foo` ran the real one;
    bash and sh have no such parameter).

    A command run by a path (`/usr/bin/git status`, `./git`) is not looked for on PATH, so a PATH in force does not refuse
    it, and a name the hook grants nothing for (`ls`) stays silent as before; GIT_EXEC_PATH keeps SPD-046's own reason.
    The finding is appended after its own command's, so `PATH=<dir> git push` still answers with Law 7's verb; and since
    SPD-049 `path` and `hashed` carry an entry in FINDING_LAST, so on a line of several commands a refusal the words as
    spelled already earn answers first (`PATH=<dir> git status; git push` names the push -- both refusals, only the
    wording changes).  AGENT_A plans tests/** and bin/spud; AGENT_C plans **; Law 7 does not bind Spud."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

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

    def test_path_by_every_route_before_a_bare_git_is_refused(self):
        for cmd in ("PATH=/tmp/x:$PATH git status", "PATH=/tmp/x git status", "PATH=/tmp/x; git status",
                    "PATH=/tmp/x:$PATH; git status", "export PATH=/tmp/x:$PATH; git status",
                    "typeset -x PATH=/tmp/x; git status", "declare -x PATH=/tmp/x; git status",
                    "readonly PATH=/tmp/x; git status", "local -x PATH=/tmp/x; git status",
                    "env PATH=/tmp/x git status", "/usr/bin/env PATH=/tmp/x:$PATH git status",
                    "PATH=/tmp/x:$PATH git log --oneline", "PATH=/tmp/x:$PATH git diff"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("PATH", r.reason)

    def test_the_zsh_path_array_is_refused(self):
        # zsh ties `path` to PATH (probed): every form of the array assignment replaces the program a bare name finds.
        for cmd in ("path=(/tmp/x $path); git status", "path+=(/tmp/x); git status", "path=(/tmp/x); git status",
                    "path=(/tmp/x $path) git status", "PATH=/tmp/x path=(/tmp/y); git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("path", r.reason)

    def test_every_command_name_the_hook_reads_is_refused(self):
        for cmd in ("PATH=/tmp/x:$PATH git status", "PATH=/tmp/x:$PATH sh -c 'echo hi'",
                    "PATH=/tmp/x:$PATH bash -c 'echo hi'", "PATH=/tmp/x:$PATH zsh -c 'echo hi'",
                    "PATH=/tmp/x:$PATH python3.14 -I -S bin/spud board", "PATH=/tmp/x:$PATH python3 x.py",
                    "PATH=/tmp/x:$PATH tee /tmp/out", "PATH=/tmp/x:$PATH nice git status",
                    "PATH=/tmp/x:$PATH nohup ls", "PATH=/tmp/x:$PATH env ls", "PATH=/tmp/x:$PATH node x.js",
                    "PATH=/tmp/x:$PATH %s board"):
            with self.subTest(cmd):
                self.refused_for_members(cmd if "%s" not in cmd else cmd % self.spud_cli)
        # sqlite3 is refused for everyone by the database rule, which is read before the findings; the finding is there.
        self.assertIn(("path", ("PATH", "sqlite3")), self.finding("PATH=/tmp/x:$PATH sqlite3 x.db"))

    def test_the_finding_names_the_variable_and_the_command(self):
        self.assertEqual(self.finding("PATH=/tmp/x git status"),
                         [("git", ("status", None)), ("path", ("PATH", "git"))])
        self.assertEqual(self.finding("path=(/tmp/x); git status"),
                         [("git", ("status", None)), ("path", ("path", "git"))])
        self.assertEqual(self.finding("PATH=/tmp/x tee /tmp/out"), [("path", ("PATH", "tee"))])
        r = self.refused_for_members("PATH=/tmp/x:$PATH git status")
        self.assertIn("PATH", r.reason)
        self.assertIn("git", r.reason)
        self.assertNotIn("alias", r.reason)  # its own reason, not SPD-044's or SPD-046's

    def test_a_refusal_the_words_as_spelled_earn_keeps_its_own_reason(self):
        r = self.refused_for_members("PATH=/tmp/x:$PATH git push")
        self.assertIn("git push", r.reason)
        r = self.assertRefused("PATH=/tmp/x:$PATH %s --as spud board" % self.spud_cli, "Law 6")
        self.assertIn("--as spud", r.reason)

    def test_a_later_command_that_earns_its_own_refusal_answers_before_the_path_reason(self):
        # SPD-049's last objective: wave 2 added `path` and `hashed` but could not order them, FINDING_LAST living
        # outside its globs, so on a line of several commands the PATH reason answered before a refusal the words as
        # spelled already earn.  Both still refuse; only the wording changes.
        for command in ("PATH=/tmp/x git status; git push", "path=(/tmp/x); git log; git commit -m x",
                        "hash git=/tmp/x/git; git status; git push", "hash -p /tmp/x/git git; git status && git rebase"):
            with self.subTest(command):
                r = self.refused_for_members(command)
                self.assertNotIn("PATH as the session has it", r.reason)
                self.assertNotIn("command table", r.reason)
        r = self.refused_for_members("PATH=/tmp/x git status; git push")
        self.assertIn("git push", r.reason)
        r = self.assertRefused("PATH=/tmp/x git status; %s --as spud board" % self.spud_cli, "Law 6")
        self.assertIn("--as spud", r.reason)

    def test_a_command_run_by_a_path_stays_silent(self):
        # PATH is not searched for a word holding a slash, so the program the hook read is the one that runs.
        for ok in ("PATH=/tmp/x:$PATH /usr/bin/git status",
                   "PATH=/tmp/x:$PATH /bin/sh -c 'echo hi'", "PATH=/tmp/x:$PATH /usr/bin/tee /tmp/out"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        # ... and a file of the checkout's run by its path is, since SPD-145, a member's file of commands
        self.assertRefused("PATH=/tmp/x:$PATH ./git status", SCRIPT_WORDING)
        self.assertSilent("PATH=/tmp/x:$PATH ./git status", agent_id=None)

    def test_a_name_the_hook_grants_nothing_for_stays_silent(self):
        for ok in ("PATH=/tmp/x ls", "PATH=/tmp/x:$PATH ls -la", "PATH=/tmp/x echo hi", "PATH=/tmp/x:$PATH cd /tmp",
                   "export PATH=/tmp/x:$PATH", "PATH=/tmp/x:$PATH", "path=(/tmp/x $path)"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_reading_path_without_assigning_it_stays_silent(self):
        for ok in ("echo $PATH", "git status # $PATH", "echo \"$PATH\" > /tmp/out", "git -c color.ui=never status",
                   "CDPATH=$PATH git status", "MYPATH=/tmp/x:$PATH git status", "PATHS=/tmp/x git status",
                   "XPATH=/tmp/x git status"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_git_exec_path_keeps_its_own_reason(self):
        r = self.refused_for_members("GIT_EXEC_PATH=/tmp/x git status")
        self.assertIn("GIT_EXEC_PATH", r.reason)
        self.assertEqual(self.finding("GIT_EXEC_PATH=/tmp/x git status"), [("git-program", "GIT_EXEC_PATH")])

    def test_hash_shadowing_a_name_the_hook_reads_is_refused(self):
        # bash: `hash -p <path> <name>`; zsh: `hash <name>=<path>` (both probed).
        for cmd in ("hash -p /tmp/x/git git; git status", "hash git=/tmp/x/git; git status",
                    "hash -p /tmp/x/sh sh; sh -c 'echo hi'", "hash tee=/tmp/x/tee; tee /tmp/out",
                    "hash -p /tmp/x/git git; git log"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("hash", r.reason)
        self.assertEqual(self.finding("hash git=/tmp/x/git; git status"),
                         [("git", ("status", None)), ("hashed", "git")])

    def test_hash_that_names_nothing_the_hook_reads_stays_silent(self):
        for ok in ("hash; git status", "hash -r; git status", "hash -p /tmp/x/ls ls; git status",
                   "hash ls=/tmp/x/ls; git status", "env hash git=/tmp/x/git; git status"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_the_zsh_commands_parameter_hashes_a_name(self):
        # SPD-105: an element of zsh's `commands` fills the table `hash` does, by every spelling zsh takes
        for cmd in ("commands[git]=/tmp/x/git; git status", "commands+=(git /tmp/x/git); git status",
                    "commands=(git /tmp/x/git); git status", "typeset 'commands[git]=/tmp/x/git'; git status",
                    "commands[git]=/tmp/x/git git status", "commands[tee]=/tmp/x/tee; tee /tmp/out",
                    "commands[git]=/tmp/x/git; command git status", "commands[git]=/tmp/x/git; nice git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("command table", r.reason)
        self.assertEqual(self.finding("commands[git]=/tmp/x/git; git status"),
                         [("git", ("status", None)), ("hashed", "git")])
        # a key the hook cannot read may be any name it reads
        for cmd in ("commands[$k]=/tmp/x/git; git status", "commands+=($pairs); git status",
                    "commands[(e)git]=/tmp/x/git; git status", "commands=(git); git status"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        m = load_spud_module()
        self.assertEqual(m.analyse_command("commands[$k]=/tmp/x/git").hashed, {m.UNKNOWN_NAME})
        # a key the hook reads and grants nothing for, or a call it resolves past the table, stays silent
        for ok in ("commands[ls]=/tmp/x/ls; git status", "commands[git]=/tmp/x/git; /usr/bin/git status",
                   "commands[deploy]=/tmp/x/d; deploy", "dis_commands[git]=/tmp/x/git; git status", "echo $commands[git]"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_path_inside_shell_strings_eval_and_subshells(self):
        for cmd in ("sh -c 'PATH=/tmp/x:$PATH git status'", "eval 'PATH=/tmp/x git status'",
                    "(PATH=/tmp/x:$PATH git status)", "true && PATH=/tmp/x:$PATH git status",
                    "echo $(PATH=/tmp/x git status)"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)


class SubscriptAssignmentTest(BashHookCase):
    """SPD-085 (Kennebec's SPD-062 proposal): a subscripted assignment, `name[subscript]=value`, reached the analysis as a
    command word.  `path[1]=/tmp/x; git status` gave kinds other and git and no PATH finding; `x[1]=y git push` gave none
    at all, the push behind the word never read; `path[1,0]=(/tmp/x); git status` was refused only by accident, a glob
    reading of its brackets ending in `sqlite`.  Probed in zsh 5.9 -f, zsh -f -o nobareglobqual (this Mac's Bash tool),
    bash 3.2 and sh, with `foo` first on a scratch PATH (REAL) and a scratch copy of it in another directory (FAKE):

    - zsh ran FAKE for `path[1]=<dir>; foo`, `path[1,0]=(<dir>); foo`, `path[1]=(<dir>)`, `path[1]+=/../fakebin` and
      `PATH[1]=<dir>:/` and `PATH[1,0]=<dir>:` (a character slice of the scalar), each as a prefix too
      (`path[1]=<dir> foo`, also before `command -v` and `exec`), and for `typeset`, `declare`, `local` (at top level),
      `typeset -g` and `export 'path[1]=<dir>'`, the unquoted `typeset path[1]=<dir>` and `typeset path[1]=(<dir>)`,
      `export`/`typeset -x 'PATH[1]=<dir>:/'`, `path["1"]=`, `path[$((1))]=`, `i=1; path[$i]=` and `path[(r)*real*]=`.
      `PATH[0]=` is "assignment to invalid subscript range" and aborts the line; `typeset`, `local` or `readonly` of an
      element inside a function is "can't create local array elements";
    - bash and sh have no `path` array (REAL), and turn PATH into an array for `PATH[0]=<dir>`, `PATH[1]=<dir>:/`,
      `declare 'PATH[0]=<dir>'` and a function's `local 'PATH[1]=...'`: `$PATH` then reads the element and the lookup
      finds nothing ("foo: No such file or directory") -- a PATH change all the same.  `PATH[0]+=x` appended to the scalar
      (bash reads a scalar's [0] as the scalar itself, confirmed).  A subscripted prefix is no assignment to bash, which ran
      REAL and handed the literal `PATH[0]=<dir>` to the program's environment; `export`/`readonly 'PATH[0]=...'` is "not a
      valid identifier"; `path[1,0]=(...)` as a statement is "cannot assign list to array member";
    - none of these is an assignment anywhere: `'path[1]'=x` (a command of that name), `path[ 1 ]=x` ("bad pattern" in
      zsh), `path[1]x=y` and `path[1][1]=x` ("no matches found"); `a[b[1]]=x` is one subscript in both shells, and
      `echo path[1]` an argument ("no matches found" in zsh).

    shell/assignment_words now reads the word as the assignment it is before any glob reading of its brackets -- the
    subscript runs to the `]` that balances its `[`, and `=` or `+=` follows at once -- as the prefix loop, a declaration's
    operand (whose quoted subscript the builtin parses) and walk.py's `name[subscript]=( ... )` join all do.  The variable
    is assigned with a value the hook does not compute, as an append's, so every rule that reads it counts it as a plain
    assignment would: PATH and `path` for shadowed_name, CDPATH, GIT_*; a subscript the hook cannot evaluate changes nothing
    about that, and an element of a variable the hook tracks nowhere changes nothing at all.  Law 7 does not bind Spud.
    AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)
        return r

    def silent_for_everyone(self, command):
        for agent_id in (AGENT_C, AGENT_A, None):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertSilent(command, agent_id)

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def test_the_tickets_evidence_commands(self):
        for cmd in ("path[1]=/tmp/x; git status", "path[1,0]=(/tmp/x); git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("path", r.reason)
                self.assertIn("PATH as the session has it", r.reason)
                self.assertEqual(self.analysis(cmd).findings, [("git", ("status", None)), ("path", ("path", "git"))])
                self.assertEqual(self.analysis(cmd).kinds, ["git"])  # no command word, and no glob reading of the brackets
        # the word is an assignment, so the push behind it is read (it was not: no finding at all)
        self.assertEqual(self.analysis("x[1]=y git push").findings, [("git", ("push", "push"))])
        self.assertIn("git push", self.refused_for_members("x[1]=y git push").reason)

    def test_every_spelling_before_a_bare_name(self):
        for cmd in ("path[1]=/tmp/x; git status", "path[1,0]=(/tmp/x); git status", "path[1]=(/tmp/x); git status",
                    "path[1]+=/../x; git status", "path[-1,-1]=(/tmp/x /bin); git log", "PATH[1]=/tmp/x:/; git status",
                    "PATH[1,0]=/tmp/x:; git status", "PATH[0]=/tmp/x; git status", "PATH[0]+=:/tmp/x; git status",
                    "path[1]=/tmp/x\ngit status", "path[1]=/tmp/x; tee /tmp/out", "path[1]=/tmp/x; sh -c 'echo hi'",
                    "path[1]=/tmp/x; python3.14 x.py", "path[1]=/tmp/x; env ls", "path[1]=/tmp/x; %s board" % self.spud_cli):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_every_spelling_as_a_prefix(self):
        for cmd in ("path[1]=/tmp/x git status", "path[1,0]=(/tmp/x) git status", "path[1]=(/tmp/x) git status",
                    "PATH[1]=/tmp/x:/ git status", "PATH[0]=/tmp/x git status", "path[1]=/tmp/x nice git status",
                    "X=1 path[1]=/tmp/x git status", "path[1]=/tmp/x exec git status", "path[1]=/tmp/x command git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("PATH", r.reason)

    def test_the_declaration_forms(self):
        for cmd in ("typeset 'path[1]=/tmp/x'; git status", "typeset path[1]=/tmp/x; git status",
                    "typeset path[1]=(/tmp/x); git status", "typeset -g 'path[1]=/tmp/x'; git status",
                    "declare 'path[1]=/tmp/x'; git status", "declare 'PATH[0]=/tmp/x'; git status",
                    "declare PATH[0]=/tmp/x; git status", "local 'path[1]=/tmp/x'; git status",
                    "export 'path[1]=/tmp/x'; git status", "export 'PATH[1]=/tmp/x:/'; git status",
                    "export PATH[1]=/tmp/x:/; git status", "typeset -x 'PATH[1]=/tmp/x:/'; git status",
                    "readonly 'PATH[0]=/tmp/x'; git status", "f() { local 'PATH[1]=/tmp/x:/'; git status; }; f",
                    "builtin typeset 'path[1]=/tmp/x'; git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("PATH", r.reason)

    def test_a_subscript_the_hook_cannot_evaluate_still_counts(self):
        for cmd in ("path[$i]=/tmp/x; git status", "i=1; path[$i]=/tmp/x; git status", "path[$(echo 1)]=/tmp/x; git status",
                    "path[$((1))]=/tmp/x; git status", "path[(r)*real*]=/tmp/x; git status", "path[(i)x]=/tmp/x git status",
                    'path["1"]=/tmp/x; git status', "path[b[1]]=/tmp/x; git status", "path[${#path}]+=x; git status"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_every_tracked_variable_counts_as_a_plain_assignment_would(self):
        cases = (("GIT_DIR[1]=/tmp/x git status", "GIT_DIR+=/tmp/x git status", "cannot resolve"),
                 ("GIT_CONFIG_GLOBAL[1]=/tmp/x git status", "GIT_CONFIG_GLOBAL+=/tmp/x git status", "config"),
                 ("HOME[1]=/tmp/x git status", "HOME+=/tmp/x git status", "config"),
                 ("GIT_PAGER[1]=less git log", "GIT_PAGER+=less git log", "program"))
        for subscripted, appended, needle in cases:
            with self.subTest(subscripted):
                r = self.refused_for_members(subscripted, needle)
                self.assertEqual(r.reason, self.bash(appended).reason)
        # a trace file the line does not settle is a write target, which refuses Spud too (SPD-091)
        for agent_id in (AGENT_C, AGENT_A, None):
            r = self.assertRefused("GIT_TRACE[1]=/tmp/t git status", "holds a variable", agent_id)
            self.assertEqual(r.reason, self.bash("GIT_TRACE+=/tmp/t git status", agent_id).reason)
        # CDPATH: the hook cannot read it, so a relative target after the cd cannot be placed -- for everyone, as an append
        for agent_id in (AGENT_A, None):
            r = self.assertRefused("CDPATH[1]=/tmp; cd ledger; echo x > note.txt", "cannot follow", agent_id)
            self.assertEqual(r.reason, self.bash("CDPATH+=/tmp; cd ledger; echo x > note.txt", agent_id).reason)
        self.assertEqual(self.analysis("cdpath[1]=/tmp").vars, {"cdpath": "$"})

    def test_a_name_run_by_path_or_granted_nothing_stays_silent(self):
        for ok in ("path[1]=/tmp/x; /usr/bin/git status", "path[1]=/tmp/x ls",
                   "PATH[0]=/tmp/x; ls -la", "path[1]=/tmp/x", "path[1,0]=(/tmp/x)", "typeset 'path[1]=/tmp/x'"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)
        # a file of the checkout's run by its path: a member's file of commands since SPD-145
        self.refused_for_members("PATH[1]=/tmp/x ./git status", SCRIPT_WORDING)
        self.assertSilent("PATH[1]=/tmp/x ./git status", None)

    def test_an_element_of_an_untracked_variable_changes_nothing(self):
        for ok in ("x[1]=y; git status", "arr[2]=v git status", "x[1]=y", "mine[k]=(a b); git log", "x[1]+=y git diff",
                   "declare 'x[0]=y'; git status", "a[b[1]]=x; git status"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)
        self.assertEqual([f for f in self.analysis("x[1]=y; arr[2]=(a b); git status").findings if f[0] != "git"], [])

    def test_words_that_are_no_assignment_stay_as_they_were(self):
        # a quoted bracket, a blank in the subscript, and text after the subscript are no assignment in any shell
        for ok in ("echo path[1]", "echo path[1]=/tmp/x", "grep -n 'path[1]=' README",
                   "echo 'PATH[0]=/tmp/x'", "printf '%s\\n' path[1]=x"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)
        # ... and the two such words in command position name a command holding a slash, which the shell runs as the path
        # it is (`path[1]x=/tmp/x` is the file `x` in the directory `path[1]x=` under this one): a member's file of commands
        # since SPD-145
        for cmd in ("'path[1]'=/tmp/x; git status", "path[1]x=/tmp/x"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, SCRIPT_WORDING)
                self.assertSilent(cmd, None)
        self.assertEqual(self.analysis("'path[1]'=/tmp/x; git status").vars, {})
        self.assertEqual(self.analysis("path[1][1]=/tmp/x").vars, {})

    def test_the_plain_array_forms_read_as_before(self):
        m = load_spud_module()
        self.assertEqual(self.analysis("path=(/tmp/x $path); git status").findings,
                         [("git", ("status", None)), ("path", ("path", "git"))])
        self.assertEqual(self.analysis("path+=(/tmp/x); git status").findings,
                         [("git", ("status", None)), ("path", ("path", "git"))])
        self.assertEqual(self.analysis("x=(a b)").vars, {"x": m._ARRAY_VALUE + "a b"})
        self.assertEqual(self.analysis("x=()").vars, {"x": m._ARRAY_VALUE})
        # a quoted blank inside one element stays inside it, and the readings of `$x` are what they were
        a = self.analysis("x=('a b' c)")
        self.assertEqual(m.deglob(a.vars["x"]), "a b c")
        self.assertEqual(m.variable_readings(a, "x"), ([["a", "b", "c"], ["a"]], True))
        self.assertEqual(self.analysis("x=('a b' c); $x").findings, [("var-doubt", "$x")])
        self.silent_for_everyone("path=(/tmp/x $path)")


class FunctionShadowTest(BashHookCase):
    """SPD-084: the hook reads a line's command words by name -- git, spud, python3.14, sqlite3, tee, a shell, a wrapper --
    and a shell function of that name defined earlier on the line runs in its place, so `git() { git push; }; git status`
    reached the hook as an ordinary `git status` with no finding (Law 7).  SPD-062 closed a PATH the line assigns and a
    `hash`; SPD-059 closed an alias run through eval; this closes the function.  Probed in zsh 5.9 -f, zsh -f -o
    nobareglobqual (this Mac's Bash tool), bash 3.2 and sh, with `foo` a name no command has (SH means the function ran):

    - `foo() { echo SH; }; foo`, `foo () { echo SH; }`, `function foo { echo SH; }` and `function foo () { echo SH; }` each
      ran the function in all four; zsh also takes the body-without-braces short forms `foo () echo SH` and `foo() echo SH`
      (bash and sh: a syntax error), and its several-names form `foo bar () { echo SH; }` defined both foo and bar;
    - a call before the definition ran the real lookup (`foo; foo() {...}` -> not found), and so did a call after a
      definition in a `( ... )` subshell (`(foo() {...}); foo` -> not found): the subshell's function does not reach it.  A
      call inside the subshell (`(foo() {...}; foo)`), and one after a definition in a branch (`if`, `&&`, a case arm), a
      loop body, another function's body, a background list, a pipeline or a command substitution, ran the function;
    - a function is looked up in command position only: `command foo`, `builtin foo`, `nice foo` and `env foo` each ran the
      real lookup (the function bypassed), while `time foo`, `! foo` and zsh's `nocorrect foo`, `noglob foo`, `- foo` and
      `exec foo` kept it -- `exec foo` ran the function in zsh (`SH`, no line after: exec then exits) but skipped it in bash
      and sh, so the hook refuses it, the Bash tool being zsh.  A function named for a wrapper shadows the wrapper itself
      (`env() { echo SH; }; env true` -> SH), but a wrapper the first one runs does not (`env() {...}; nice env true` -> not
      SH); a call by a path (`/usr/bin/foo`) is not looked up.  zsh applies a function lookup after noglob/-/exec where it
      does not expand an alias (SPD-059, probed: `eval 'noglob gp'`, `eval '- gp'`, `eval 'exec gp'` all ran nothing), so
      the function gate is its own (syntax.FUNCTION_KEEP_WRAPPERS), not the alias command-position gate.

    The finding is appended after its own command's and carries an entry in FINDING_LAST beside `path` and `hashed`, so on a
    line of several commands a refusal the words as spelled already earn answers first.  A definition in a `( ... )` subshell
    is dropped when it closes (ShellWalk restores the set); one in a branch, a loop, a function body, a background list, a
    pipeline or a command substitution is kept (refuse on doubt, never allow on doubt), as is one an `unset -f` or
    `unfunction` may have removed (the hook keeps refusing rather than allow on doubt).  Law 7 does not bind Spud.  AGENT_A
    plans tests/** and bin/spud; AGENT_C plans **.

    SPD-105 (Atlantic's SPD-084 proposal): zsh also binds a function through its special `functions` association, which the
    hook read as a command word or an unrelated variable.  Probed in zsh 5.9 -f and -o nobareglobqual (bash and sh have no
    such parameter and ran the real lookup every time): `functions[foo]='echo SH'; foo`, `functions+=(foo 'echo SH'); foo`,
    `functions=(foo 'echo SH'); foo` (a whole assignment adds its pairs: a function defined before it still ran),
    `typeset`, `declare` and `export 'functions[foo]=echo SH'`, a prefix (`functions[foo]='echo SH' true; foo`),
    `functions[foo]+=' x'`, a function body's `functions[foo]=` once called, `k=foo; functions[$k]=`,
    `functions[(e)foo]=` (a subscript flag), `x='echo SH'; functions+=(foo $x)` and `a=(foo 'echo SH'); functions+=($a)`
    each ran the function, and `functions[foo]=...; (foo)` did too.  `(functions[foo]=...); foo` ran the real lookup (the
    subshell's), as did `functions[f?o]=` (the key is `f?o`, a subscript is not globbed), `functions["foo"]=` (the quotes
    are part of the key), `functions+=(foo 'echo SH' bar)` ("bad set of key/value pairs"), `unset 'functions[foo]'` and
    `dis_functions[foo]=` (a disabled function).  The name each spelling keys is now recorded in the same set a definition
    fills, with its subshell scope; a key the hook cannot read (an expansion, a subscript flag, an element that may become
    several words, pairs odd in number, a scalar) records UNKNOWN_NAME, which refuses a member's later call of every name
    the hook reads.  `functions["git"]=` is refused though zsh keys it `"git"`: the hook reads the word with its quotes
    taken, and refuses on that doubt."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)
        return r

    def silent_for_everyone(self, command):
        for agent_id in (AGENT_C, AGENT_A, None):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertSilent(command, agent_id)

    def finding(self, command):
        me = load_spud_module()
        return me.analyse_command(command, me.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path))).findings

    def test_the_tickets_evidence_command(self):
        # a function whose body is harmless still shadows the git the hook read: the reason is the function, not the body
        r = self.refused_for_members("git() { echo pushed; }; git status")
        self.assertIn("shell function", r.reason)
        self.assertIn("git", r.reason)
        self.assertEqual(self.finding("git() { echo pushed; }; git status"),
                         [("git", ("status", None)), ("function", "git")])

    def test_every_spelling_of_the_definition(self):
        for cmd in ("git() { true; }; git status", "git () { true; }; git status",
                    "function git { true; }; git status", "function git () { true; }; git status",
                    "function git() { true; }; git status", "git () true; git status",
                    "git() true; git status", "git() { true; }\ngit status",
                    "builtin git() { true; }; git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("shell function", r.reason)
                self.assertEqual(self.finding(cmd)[-1], ("function", "git"))

    def test_the_several_names_zsh_form_binds_them_all(self):
        # zsh's `a b () { ... }` defines every name; a later call of any of them is shadowed (probed both ran the function)
        self.assertEqual(self.finding("git tee () { true; }; git status"),
                         [("git", ("status", None)), ("function", "git")])
        self.assertEqual(self.finding("git tee () { true; }; tee /tmp/out"), [("function", "tee")])
        self.refused_for_members("git tee () { true; }; git status")
        self.refused_for_members("git tee () { true; }; tee /tmp/out")

    def test_every_dispatched_name(self):
        self.refused_for_members("git() { true; }; git status")
        self.refused_for_members("git() { true; }; git push")
        self.refused_for_members("spud() { true; }; spud board")
        self.refused_for_members("python3.14() { true; }; python3.14 x.py")
        self.refused_for_members("tee() { true; }; tee /tmp/out")
        self.refused_for_members("sh() { true; }; sh -c 'echo hi'")
        self.refused_for_members("bash() { true; }; bash -c 'echo hi'")
        self.refused_for_members("node() { true; }; node x.js")
        # a wrapper name is itself a function in command position: env runs the function, not /usr/bin/env
        r = self.refused_for_members("env() { true; }; env git status")
        self.assertIn("env", r.reason)
        self.assertEqual(self.finding("env() { true; }; env git status")[-1], ("function", "env"))
        # sqlite3 is refused for everyone by the database rule (read before the findings); the function finding is there
        self.assertIn(("function", "sqlite3"), self.finding("sqlite3() { true; }; sqlite3 x.db"))

    def test_a_name_the_hook_does_not_dispatch_on_changes_nothing(self):
        for ok in ("ls() { true; }; ls", "cc() { true; }; cc all", "cat() { true; }; cat f",
                   "grep() { true; }; grep x f"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)

    def test_a_call_before_the_definition_is_not_shadowed(self):
        for ok in ("git status; git() { true; }", "git log; function git { true; }"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_a_call_by_path_or_a_bypassing_prefix_stays_silent(self):
        # the function is looked up in command position; a path is not, and command/builtin/env/nice resolve their own word
        for ok in ("git() { true; }; /usr/bin/git status",
                   "git() { true; }; command git status", "git() { true; }; builtin git status",
                   "git() { true; }; nice git status", "git() { true; }; env git status",
                   "git() { true; }; nice env git status", "git() { true; }; sudo git status"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        # ... and a file in the checkout run by its path is no function's, but since SPD-145 a member's file of commands
        self.assertRefused("git() { true; }; ./git status", SCRIPT_WORDING)
        self.assertSilent("git() { true; }; ./git status", agent_id=None)

    def test_a_bypassing_prefix_keeps_the_words_own_refusal(self):
        # command/nice bypass the function, but a write verb behind them still earns Law 7 -- its own reason, not the function's
        for cmd in ("git() { true; }; command git push", "git() { true; }; nice git commit -m x"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertNotIn("shell function", r.reason)

    def test_modifiers_that_keep_the_function_are_refused(self):
        # keywords (time, !) run the following function in all four shells; zsh keeps the lookup after noglob, - and exec
        # too (probed: exec ran the function in zsh, skipped it in bash/sh; the hook refuses for the shell the tool runs)
        for cmd in ("git() { true; }; time git status", "git() { true; }; ! git status",
                    "git() { true; }; noglob git status", "git() { true; }; - git status",
                    "git() { true; }; nocorrect git status", "git() { true; }; exec git status",
                    "git() { true; }; noglob exec git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("shell function", r.reason)
        # a write verb still answers first, whichever modifier keeps the function
        r = self.refused_for_members("git() { true; }; exec git push")
        self.assertIn("git push", r.reason)
        self.assertNotIn("shell function", r.reason)

    def test_the_subshell_scope(self):
        # a definition in a ( ... ) subshell does not reach a call after it (probed: `(git(){ :; }); git status` ran real git)
        for ok in ("(git() { true; }); git status", "(function git { true; }); git status",
                   "(git() { true; }) ; git log", "( ( git() { true; } ) ); git status"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        self.assertEqual(self.finding("(git() { true; }); git status"), [("git", ("status", None))])
        # a call inside the subshell is shadowed
        for cmd in ("(git() { true; }; git status)", "(git() { true; }; git push)"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_the_doubt_cases_are_refused(self):
        # a definition that may or may not have run, or that runs in a forked list, is kept: refuse on doubt (never allow)
        for cmd in ("if true; then git() { true; }; fi; git status",
                    "true && git() { true; }; git status",
                    "false || git() { true; }; git status",
                    "case x in x) git() { true; };; esac; git status",
                    "for f in a; do git() { true; }; done; git status",
                    "while false; do git() { true; }; done; git status",
                    "f() { git() { true; }; }; f; git status",
                    "git() { true; } & wait; git status",
                    "git() { true; } | cat; git status",
                    "X=$(git() { true; }; echo d); git status",
                    "git() { true; }; unset -f git; git status",
                    "git() { true; }; unfunction git; git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("shell function", r.reason)

    def test_a_later_command_that_earns_its_own_refusal_answers_first(self):
        # FINDING_LAST orders the function reason after a refusal the words as spelled already earn (like path and hashed)
        for command in ("git() { true; }; git status; git push",
                        "git() { true; }; git log && git commit -m x"):
            with self.subTest(command):
                r = self.refused_for_members(command)
                self.assertNotIn("shell function", r.reason)
        r = self.refused_for_members("git() { true; }; git status; git push")
        self.assertIn("git push", r.reason)

    def test_a_function_body_is_still_analysed_as_today(self):
        # no regression: a body's own commands are read exactly as before (the function name is f, not git)
        self.assertEqual(self.finding("f() { git push; }"), [("git", ("push", "push"))])
        self.refused_for_members("f() { git push; }")
        self.assertEqual(self.finding("deploy() { git status; }; deploy"), [("git", ("status", None))])
        self.silent_for_everyone("deploy() { git status; }; deploy")

    def test_the_finding_names_the_command(self):
        self.assertEqual(self.finding("git() { true; }; git status"),
                         [("git", ("status", None)), ("function", "git")])
        self.assertEqual(self.finding("tee() { true; }; tee /tmp/out"), [("function", "tee")])
        r = self.refused_for_members("git() { true; }; git status")
        self.assertIn("git", r.reason)
        self.assertNotIn("hash", r.reason)  # its own reason, not SPD-062's
        self.assertNotIn("PATH", r.reason)

    def test_the_zsh_functions_parameter_binds_a_function(self):
        # SPD-105's evidence command, then every spelling zsh takes
        self.assertEqual(self.finding("functions[git]='true'; git status"),
                         [("git", ("status", None)), ("function", "git")])
        for cmd in ("functions[git]='true'; git status", "functions+=(git 'true'); git status",
                    "functions=(git 'echo SH'); git status", "functions+=(deploy 'a b' git 'c d'); git status",
                    "typeset 'functions[git]=true'; git status", "declare 'functions[git]=true'; git status",
                    "export 'functions[git]=true'; git status", "functions[git]=true true; git status",
                    "functions[git]+=' x'; git status", "functions[git]=true; (git status)",
                    "f() { functions[git]=true; }; f; git status", 'functions["git"]=true; git status',
                    "functions[tee]=true; tee /tmp/out", "functions[env]=true; env git status",
                    "functions[git]=true; time git status", "functions[git]=true; noglob git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("shell function", r.reason)
        self.assertEqual(self.finding("functions+=(git 'a b' tee 'c'); tee /tmp/out"), [("function", "tee")])
        # a write verb still answers with its own reason
        self.assertIn("git push", self.refused_for_members("functions[git]=true; git push").reason)

    def test_a_functions_key_the_hook_cannot_read_refuses_every_name(self):
        me = load_spud_module()
        for cmd in ("functions[$k]=true; git status", "k=git; functions[$k]=true; git status",
                    "functions[(e)git]=true; git status", "functions[$(echo git)]=true; git status",
                    "functions+=($pairs); git status", "x=true; functions+=(git $x); git status",
                    "functions+=(g* true); git status", "functions+=(git true tee); git status",
                    "functions=x; git status", "functions[$k]=true; tee /tmp/out", "functions[$k]=true; sh -c 'echo hi'"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        self.assertEqual(me.analyse_command("functions[$k]=true").functions, {me.UNKNOWN_NAME})
        # the unread name may be the bypassing wrapper's own, which a function of that name shadows in turn
        self.assertEqual(self.finding("functions[$k]=true; command git status")[-1], ("function", "command"))
        # ... and a name the hook grants nothing for is still silent, as is a call by path
        for ok in ("functions[$k]=true; ls", "functions[$k]=true; /usr/bin/git status", "functions[$k]=true; cc all"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)

    def test_the_functions_parameter_keeps_the_scope_and_the_names_it_does_not_bind(self):
        # the subshell's own element does not reach a call after it; a branch's, a background list's and a substitution's may
        for ok in ("(functions[git]=true); git status", "(functions+=(git true)); git log"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)
        for cmd in ("if true; then functions[git]=true; fi; git status", "functions[git]=true & wait; git status",
                    "x=$(functions[git]=true); git status", "true && functions[git]=true; git status"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        # a key the hook reads that names nothing it dispatches, a key zsh does not glob, and a disabled function
        for ok in ("functions[deploy]=true; git status", "functions+=(deploy true); git status",
                   "functions[g?t]=true; git status", "dis_functions[git]=true; git status", "functions=(); git status",
                   "echo $functions[git]", "functions[deploy]=true; deploy", "unset 'functions[deploy]'; git status"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)
        me = load_spud_module()
        self.assertEqual(me.analyse_command("functions+=(deploy 'a b' git 'c d')").functions, {"deploy", "git"})

    def test_reading_a_function_name_without_defining_it_stays_silent(self):
        # naming a function in a string is not defining one; a lone definition with no later call refuses nothing new
        for ok in ("echo 'git() { true; }'", "echo git is a function", "git status",
                   "git() { echo hi; }", "git log; deploy() { echo hi; }"):
            with self.subTest(ok):
                self.assertNotEqual(self.bash(ok).decision, "deny", ok)
                self.assertNotEqual(self.bash(ok, agent_id=None).decision, "deny", ok)


class EnvironmentFunctionTest(BashHookCase):
    """SPD-106 (Atlantic's SPD-084 proposal): bash and sh import a shell function from their environment, so a member can
    hand a child a function without defining it on the line, past SPD-084.  `env 'BASH_FUNC_git%%=() { true; }' bash -c
    'git status'` gave no finding at all: strip_wrapper took env's operands by ASSIGNMENT_RE, stopped at the `%%` word and
    dispatched on it, so the `-c` string was never read.  Probed on this Mac in zsh 5.9 -f, zsh -f -o nobareglobqual, bash
    3.2 and sh (every outer shell alike; SH means the function ran):

    - `/usr/bin/env 'BASH_FUNC_foo%%=() { echo SH; }' /bin/bash -c foo` -> SH, and to /bin/sh -> SH; to /bin/zsh, /bin/ksh
      and /bin/dash -> the real foo (none of them imports).  SH through `env`, `ENV`, `env -i`, `env -`, `env --`, `env -u
      HOME`, `env X=1 <function> Y=2` and `env -S "'BASH_FUNC_foo%%=...' /bin/bash -c foo"`.  The `BASH_FUNC_foo()=`,
      `BASH_FUNC_foo=` and `foo=() {` spellings imported nothing: macOS's bash 3.2 reads `%%` alone;
    - an imported function goes on down: `env <function> /bin/sh -c '/bin/sh -c foo'` and `... /bin/sh -c 'exec
      /usr/bin/env /bin/bash -c foo'` -> SH, so a program the hook cannot follow (`env <function> python3.14 x.py`, whose
      x.py runs sh) hands it on too;
    - env puts every operand holding `=` past its first character in the environment (`a b=c`, `x[1]=y` and `a%b=c` each
      reached /usr/bin/env's own listing; `=x` is "setenv =x: Invalid argument"), so none of them is its command;
    - no shell takes `BASH_FUNC_foo%%=...` as an assignment: as a word before a command it is a command not found, and
      `export 'BASH_FUNC_foo%%=...'` is "not valid in this context" (zsh) or "not a valid identifier" (bash, sh), while
      `BASH_FUNC_foo=1 /usr/bin/env` exported the valid spelling;
    - `export -f`: on a zsh line `foo() { echo SH; }; export -f foo; /bin/bash -c foo` printed the function and ran the real
      foo (zsh's `export -f` lists functions), in bash and sh it ran SH, and `typeset -fx` alike; inside `bash -c '...'`,
      `export -f foo` or `declare -fx foo` then a child bash or sh ran SH, a child zsh the real foo.

    A member has no reason to put a function in a program's environment, and the hook cannot follow what the program starts,
    so any env operand, prefix, declaration operand or sudo operand naming a BASH_FUNC_ variable refuses a member outright,
    whatever program follows, with its own reason (FINDING_LAST beside `function`); so does `export -f` or `declare -fx` of
    a name the hook reads, which writes exactly that variable in bash.  `git() { :; }; export -f git; bash -c 'git status'`
    was already refused through SPD-084's set reaching the inner shell, and still is.  Law 7 does not bind Spud.  AGENT_A
    plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)
        return r

    def silent_for_everyone(self, command):
        for agent_id in (AGENT_C, AGENT_A, None):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertSilent(command, agent_id)

    def finding(self, command):
        me = load_spud_module()
        return me.analyse_command(command, me.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path))).findings

    def test_the_tickets_evidence_command(self):
        cmd = "env 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'"
        r = self.refused_for_members(cmd)
        self.assertIn("BASH_FUNC_git%%", r.reason)
        self.assertIn("environment", r.reason)
        self.assertNotIn("shell function `git`", r.reason)  # its own reason, not SPD-084's
        # the -c string is read now: env's operand is no longer taken for its command
        self.assertEqual(self.finding(cmd), [("env-function", "BASH_FUNC_git%%"), ("git", ("status", None))])

    def test_every_env_spelling_whatever_program_follows(self):
        for cmd in ("env 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "/usr/bin/env 'BASH_FUNC_git%%=() { true; }' /bin/sh -c 'git status'",
                    "ENV 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "env -i 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "env - 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "env -- 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "env -u HOME 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "env X=1 'BASH_FUNC_git%%=() { true; }' Y=2 bash -c 'git status'",
                    "env -S \"'BASH_FUNC_git%%=() { true; }' bash -c 'git status'\"",
                    "env 'BASH_FUNC_foo%%=() { git push; }' python3.14 x.py",
                    "env 'BASH_FUNC_git%%=() { true; }' ls", "env 'BASH_FUNC_x%%=() { true; }'",
                    "nice env 'BASH_FUNC_git%%=() { true; }' sh -c 'git status'",
                    "env BASH_FUNC_git=1 bash -c 'git status'",
                    "sudo 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "echo hi; env 'BASH_FUNC_sh%%=() { true; }' make"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("BASH_FUNC_", r.reason)

    def test_a_prefix_a_declaration_or_a_bare_word_naming_it(self):
        for cmd in ("export 'BASH_FUNC_git%%=() { true; }'; bash -c 'git status'", "export BASH_FUNC_git=1",
                    "export BASH_FUNC_git", "typeset -x 'BASH_FUNC_git%%=() { true; }'", "BASH_FUNC_git=1 bash -c ls",
                    "BASH_FUNC_git=1; export BASH_FUNC_git", "'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "declare -x BASH_FUNC_git=1 && sh -c ls"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "BASH_FUNC_")

    def test_export_f_of_a_name_the_hook_reads(self):
        # SPD-084's set reaching the inner shell already refused these, and still does (pinned by its own finding)
        for cmd in ("git() { :; }; export -f git; bash -c 'git status'",
                    "bash -c 'git() { :; }; export -f git; bash -c \"git status\"'",
                    "bash -c 'git() { :; }; export -f git; sh -c \"git status\"'"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
                self.assertIn(("function", "git"), self.finding(cmd))
        # ... and the export itself now refuses, whatever the child it reaches (a program the hook cannot follow)
        for cmd in ("git() { :; }; export -f git; python3.14 x.py", "bash -c 'git() { :; }; export -f git; make'",
                    "git() { :; }; declare -fx git", "git() { :; }; typeset -f -x git", "export -f sh",
                    "bash -c 'tee() { :; }; declare -f -x tee; ./run'"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd, "BASH_FUNC_")
                self.assertIn("export", r.reason)
        self.assertIn(("env-function", "BASH_FUNC_git%%"), self.finding("git() { :; }; export -f git; python3.14 x.py"))

    def test_nothing_that_puts_no_function_in_an_environment(self):
        for ok in ("echo 'BASH_FUNC_x'", "echo BASH_FUNC_git%%=x", "grep -rn BASH_FUNC_ tests/keep.py",
                   "env FOO=1 git status", "env GIT_OPTIONAL_LOCKS=0 git status", "env -u BASH_FUNC_git%% git status",
                   "export -f deploy", "deploy() { :; }; export -f deploy; bash -c deploy", "declare -f git",
                   "typeset -f", "export -p", "readonly -f git", "printenv BASH_FUNC_git%%"):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
                self.assertNotEqual(self.bash(ok, agent_id=None).decision, "deny", ok)

    def test_env_takes_every_operand_holding_an_equals_sign(self):
        # an env operand no shell would take for an assignment hid the command after it; it is env's, and the command is read
        for cmd, needle in (("env 'a b=c' git push", "git push"), ("env x[1]=y git commit -m x", "git commit"),
                            ("env a%b=c git reset --hard", "git reset")):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn(needle, r.reason)
        self.assertEqual(self.finding("env 'a b=c' git status"), [("git", ("status", None))])
        self.silent_for_everyone("env 'a b=c' git status")

    def test_a_refusal_the_words_as_spelled_earn_answers_first(self):
        r = self.refused_for_members("env 'BASH_FUNC_x%%=() { true; }' git push")
        self.assertIn("git push", r.reason)
        r = self.assertRefused("env 'BASH_FUNC_x%%=() { true; }' %s --as spud board" % self.spud_cli, "Law 6")
        self.assertIn("--as spud", r.reason)


class WrapperCommandWordTest(BashHookCase):
    """SPD-055: after strip_wrapper the prefix loop read the wrapper's remaining words as the shell's own, so
    `nice x=./git push` took `x=./git` for an assignment and dispatched on `push` -- kind other, no finding -- while nice
    execs that word and the shell ran ./git push out of a directory named `x=.`.  Probed in zsh 5.9 -f, zsh -f -o
    nobareglobqual, bash 3.2 and sh with a program in such a directory: `command`, `exec`, `nohup`, `nice`, `caffeinate`,
    `script`, `stdbuf`, `xargs` (with input) and zsh's `noglob` each ran it, while `env x=./prog status` set x and ran
    `status` instead, wherever env stood; sudo(8) documents `sudo [VAR=value] [-i | -s] [command [arg ...]]` and takes it as
    environment too (syntax.WRAPPER_TAKES_ASSIGNMENTS).  The shell's own `time` and zsh's `nocorrect` keep the command
    position, so the shell reads the assignment after them -- but only where they stand in it, which the method below pins.
    For every other wrapper the first remaining word is its command, assignment-shaped or not, and os.path.basename is what
    the hook dispatches on (`x=./git` -> git).

    A word an exec'ing wrapper is handed is no longer an assignment either, so `nice GIT_PAGER=less git log` now reads as
    the command `GIT_PAGER=less` and stays silent: the shell looks for a program of that name and runs neither git nor a
    pager (probed).  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

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

    def test_a_wrapper_execs_an_assignment_shaped_word(self):
        for wrapper in ("nice", "command", "exec", "nohup", "timeout 5", "xargs", "builtin", "caffeinate", "doas",
                        "stdbuf -o0", "chronic", "ionice", "setsid", "unbuffer", "script /dev/null", "noglob",
                        "nice -n 5", "nohup nice", "command -p"):
            cmd = "%s x=./git push" % wrapper
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("git push", r.reason)

    def test_the_word_is_dispatched_by_its_base_name(self):
        # ... and, being a path, recorded as the file it is (SPD-145), which bash_rule reads after Law 7's verb
        for word in ("x=./git", "x=../bin/git", "x=/usr/bin/git"):
            with self.subTest(word):
                found = self.finding("nice %s push" % word)
                self.assertEqual([f for f in found if f[0] != "script"], [("git", ("push", "push"))])
                self.assertEqual([d[:3] for k, d in found if k == "script"], [("exec", word, word)])
                self.assertNotIn(SCRIPT_WORDING, self.refused_for_members("nice %s push" % word).reason)

    def test_env_sudo_time_and_nocorrect_still_read_the_word_as_environment(self):
        # env and sudo take NAME=value as the command's environment; `time` is a reserved word and zsh's `nocorrect` keeps
        # the command position, so the shell's own assignment parsing still applies there (probed).
        for ok in ("env x=./git push", "sudo x=./git push", "time x=./git push", "nocorrect x=./git push",
                   "env -- x=./git push", "sudo -u root x=./git push"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        for refused in ("env GIT_PAGER=less git log", "sudo GIT_PAGER=less git log", "time GIT_PAGER=less git log",
                        "nocorrect GIT_PAGER=less git log", "env x=./git git push", "sudo x=1 git push"):
            with self.subTest(refused):
                self.refused_for_members(refused)

    def test_an_assignment_shaped_word_with_a_harmless_base_stays_silent(self):
        for ok in ("nice x=1 ls", "nice FOO=1 ls"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        # a word holding a slash is a file the wrapper runs by its path (`x=./ls` is ls in the directory `x=.`), which since
        # SPD-145 is a member's file of commands whatever its base name
        for cmd in ("nice x=./ls", "nice x=./ls -la", "nohup x=./make all", "command x=./echo hi", "timeout 5 x=/bin/echo hi"):
            with self.subTest(cmd):
                self.assertRefused(cmd, SCRIPT_WORDING)
                self.assertSilent(cmd, agent_id=None)

    def test_a_wrapper_no_longer_reads_its_word_as_an_assignment(self):
        # The shell looks for a program named `GIT_PAGER=less` and runs nothing: neither git nor the pager (probed), so the
        # hook says nothing either.  `env` and `sudo` keep the old reading, which the test above pins.
        for ok in ("nice GIT_PAGER=less git log", "nohup GIT_SSH_COMMAND=cmd git fetch", "nice FOO=1 git push"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_time_and_nocorrect_take_the_assignment_only_in_the_command_position(self):
        """`time` and zsh's `nocorrect` are the shell's own, so they keep the command position and the shell reads the
        assignment -- but only where they stand in it.  Behind another wrapper `time` is /usr/bin/time, an external program
        that execs its word: probed, `nice time x=./prog status`, `env time x=./prog status` and zsh's `- time x=./prog
        status` each ran ./prog while `time x=./prog status` set x and ran `status`.  `nice nocorrect x=./git push` runs
        nothing at all (nice finds no program called nocorrect), and is refused with it: fail closed."""
        for ok in ("time x=./git push", "nocorrect x=./git push", "nice env x=./git push", "env sudo x=./git push"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        for refused in ("nice time x=./git push", "env time x=./git push", "sudo time x=./git push",
                        "- time x=./git push", "nice nocorrect x=./git push", "time nice x=./git push"):
            with self.subTest(refused):
                r = self.refused_for_members(refused)
                self.assertIn("git push", r.reason)

    def test_a_spud_call_behind_a_wrapper_is_still_read(self):
        r = self.assertRefused("nice x=%s/bin/spud --as spud board" % self.home.path, "Law 6")
        self.assertIn("--as spud", r.reason)
        self.assertSilent("nice x=%s/bin/spud --as %s board" % (self.home.path, AGENT_A))

    def test_the_word_is_read_inside_shell_strings_eval_and_subshells(self):
        for cmd in ("sh -c 'nice x=./git push'", "eval 'nice x=./git push'", "(nice x=./git push)",
                    "true && nice x=./git push", "nice x=./git commit -m x"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)


class WrapperDirectoryTest(BashHookCase):
    """SPD-128: strip_wrapper took env's `-C` and sudo's `-D`/`--chdir` as value options and dropped the value, so the command
    such a wrapper runs was read in the line's own directories.  With cwd /Users/X/repo, main (4364362) read `env -C
    tests/fake/.git/hooks tee post-index-change` as a redirect into /Users/X/repo, `env -C ledger/tickets touch SPD-001.md`
    as a write by argument there, `env -C tests/fake git status` as a git call discovering its repository there, `env -C /usr
    sh -c 'echo x > f'` as a redirect there and `sudo -D sub touch f` as a write there: a bound member's tee into a nested
    .git's hooks, its touch of a ticket and its git call into a nested repository were each silent where the plain spelling
    (`cd <dir> && ...`) is refused, and every relative path, SPD-121's writes by argument, SPD-049's git write options, git's
    repository discovery for SPD-047/SPD-063/SPD-066 and a spud call's launcher were read in the wrong directory.

    The member who built this could not run a shell (SPD-094); Spud probed this Mac's /usr/bin/env from bash in `/`
    (2026-09-18): `env -C /usr pwd`, `env -C usr pwd` (relative to the line's directory), `env -C/usr pwd` (glued), `env -iC
    /usr /bin/pwd` (a cluster), `env -P /bin -C /usr pwd`, `env -C /usr X=1 pwd`, `env -C ~ pwd` (the shell expands the
    tilde) and `env -C /usr sh -c pwd` each started in the directory; `env -C /usr -C bin pwd` printed /bin (only the last
    -C counts, relative to the line's directory); `env -C /usr env -C bin pwd` printed /usr/bin (a nested env moves from
    where the outer one left it); `env -C /nonexistent pwd` and `env -C /usr` alone exit 125 and run nothing; BSD env
    refuses GNU's `--chdir` before it runs anything, so reading it too is harmless.  sudo's `-D`/`--chdir` is read from
    sudo(8), the same way (`sudo -n -D /usr pwd` asked for a password).

    So the wrapped command -- its words, a string it hands a shell, a nested wrapper -- is read with its directories set to
    the value, resolved as chdir(2) resolves it (no CDPATH, a symlink before a `..` after it) against every directory the
    shell may be in, and the line's own directories after it are unchanged.  A redirection on the wrapper's own line is
    opened by the shell before env runs, in the line's directory.  The hook cannot know the directory, and fails closed as
    a relative write after `cd $X` does, when the value holds an expansion it did not resolve or a glob, when a leading `~`
    was not the shell's to expand, and when an argument is a filename glob or `~+`, which the shell expands in its own
    directory while the command opens what they name in the one it moved to.  AGENT_A plans home:tests/** and
    home:bin/spud, AGENT_C home:**; the home is a git repository with a nested one at tests/fake, as NestedRepositoryTest's."""

    R = "/Users/X/repo"

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        scratch_git(home, "init", "-q", "-b", "main")
        scratch_git(home, "commit", "-q", "--allow-empty", "-m", "root")
        self.nested = home / "tests" / "fake"
        plant_git_dir(self.nested / ".git")
        for d in ("tests/fake/.git/hooks", "tests/out", "ledger/tickets"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("a\n", encoding="utf-8")
        self.module = load_spud_module()

    def analysis(self, command, cwd=R):
        m = self.module
        return m.analyse_command(command, m.ShellAnalysis(cwd=cwd, home=str(self.home.path)))

    def write_dirs(self, command, cwd=R):
        """The directories each write by argument the line makes is read in, in order."""
        return [w[2] for w in self.analysis(command, cwd).arg_writes]

    def moved(self, *parts):
        return frozenset([os.path.realpath(os.path.join(*parts))])

    # -- the analysis ---------------------------------------------------------------
    def test_the_tickets_forms_are_read_where_the_wrapper_moves_the_command(self):
        R = self.R
        a = self.analysis("env -C tests/fake/.git/hooks tee post-index-change")
        self.assertEqual(a.redirects, [("post-index-change", self.moved(R, "tests/fake/.git/hooks"))])
        self.assertEqual(self.write_dirs("env -C ledger/tickets touch SPD-001.md"), [self.moved(R, "ledger/tickets")])
        a = self.analysis("env -C tests/fake git status")
        self.assertEqual(a.findings, [("git", ("status", None))])
        self.assertEqual(a.git_calls, [((), self.moved(R, "tests/fake"))])
        self.assertEqual(self.analysis("env -C /usr sh -c 'echo x > f'").redirects, [("f", self.moved("/usr"))])
        self.assertEqual(self.write_dirs("sudo -D sub touch f"), [self.moved(R, "sub")])
        # the shell opens a redirection on the wrapper's own line before env runs, in the line's directory
        self.assertEqual(self.analysis("env -C sub echo x > f").redirects, [("f", frozenset([R]))])
        a = self.analysis("env -C sub git -C x log --output=out HEAD")
        self.assertEqual(a.git_calls, [((("-C x", "x"),), self.moved(R, "sub"))])
        self.assertEqual([w[2] for w in a.git_writes], [self.moved(R, "sub")])  # SPD-049's option, where git runs

    def test_every_spelling_of_the_directory_moves_the_command(self):
        for wrapper in ("env -C sub", "env -Csub", "env -iC sub", "env -iCsub", "env -C /usr -C sub", "env -P /bin -C sub",
                        "env -C sub X=1", "env -u HOME -C sub", "env -C sub --", "env --chdir=sub", "env --chdir sub",
                        "ENV -C sub", "/usr/bin/env -C sub", "sudo -D sub", "sudo -Dsub", "sudo --chdir=sub",
                        "sudo --chdir sub", "sudo -u root -D sub", "sudo -n -D sub", "sudo -D /usr --chdir sub"):
            with self.subTest(wrapper):
                self.assertEqual(self.write_dirs(wrapper + " touch f"), [self.moved(self.R, "sub")])

    def test_everything_the_wrapper_runs_starts_there(self):
        R, home = self.R, os.path.expanduser("~")
        for command, where in (("env -C /usr env -C bin touch f", self.moved("/usr/bin")),  # probed: /usr/bin
                               ("env -C sub nice -n 5 env -C x touch f", self.moved(R, "sub/x")),
                               ("nice env -C sub touch f", self.moved(R, "sub")),
                               ("env -C sub nohup touch f", self.moved(R, "sub")),
                               ("env -C sub sh -c 'touch f'", self.moved(R, "sub")),
                               ("env -C sub sh -c 'cd x && touch f'", self.moved(R, "sub/x")),
                               ("env -C sub bash <<'EOF'\ntouch f\nEOF", self.moved(R, "sub")),
                               ("env -C sub script -q -c 'touch f' /dev/null", self.moved(R, "sub")),
                               ("env -C sub -S 'touch f'", self.moved(R, "sub")),
                               ("env -C sub eval 'touch f'", self.moved(R, "sub")),
                               ("env -C ~ touch f", self.moved(home)), ("env -C ~/x touch f", self.moved(home, "x")),
                               ("env -C ~+/sub touch f", self.moved(R, "sub")), ("env -C .. touch f", self.moved("/Users/X")),
                               ("D=sub; env -C $D touch f", self.moved(R, "sub")),  # a value the line assigned, as SPD-043 reads one
                               ("cd tests && env -C sub touch f", self.moved(R, "tests/sub")),
                               ("cd $X; env -C /usr touch f", self.moved("/usr")),  # absolute: known wherever the shell is
                               ("for i in 1 2; do env -C sub touch f; done", self.moved(R, "sub"))):  # no cd compounds
            with self.subTest(command):
                self.assertEqual(self.write_dirs(command), [where])

    def test_the_line_keeps_its_own_directories(self):
        R = frozenset([self.R])
        for command in ("env -C /usr touch f; touch g", "env -C /usr true && touch g", "env -C /usr cd x; touch g",
                        "env -C /usr sh -c 'cd /tmp'; touch g", "env -C /usr env -C bin true; touch g", "env -C /usr; touch g"):
            with self.subTest(command):
                self.assertEqual(self.write_dirs(command)[-1], R)
                self.assertEqual(self.analysis(command).cwds, R)
        self.assertEqual(self.write_dirs("cd sub && env -C /usr true && touch g"), [self.moved(self.R, "sub")])
        self.assertEqual(self.write_dirs("env -C '' touch f"), [R])  # env cannot enter it and runs nothing

    def test_a_directory_the_hook_cannot_resolve_is_unknown(self):
        """Read as a relative write after `cd $X` is, and refused as one (the hook test below)."""
        for command in ("env -C $D touch f", 'env -C "$(pwd)" touch f', "env -C `pwd` touch f", "env -C ${D:-x} touch f",
                        "env -C s?b touch f", "env -C 'sub*' touch f",  # the read loop has quoted a glob by the time it is read
                        "env -C ~- touch f", "env -C ~nobody touch f", "env -C~/x touch f", "sudo --chdir=~/x touch f",
                        "env -S '-C ~/x touch f'",  # a `~` no shell expanded: env would enter a directory named `~`
                        "env -C sub touch *.txt", "env -C sub touch f[12]", "env -C sub touch ~+/f",  # the shell expands those in its own
                        "cd $X; env -C sub touch f", "env -C /usr env -C ~+/bin touch f"):
            with self.subTest(command):
                self.assertEqual(self.write_dirs(command), [None])
        self.assertEqual(self.analysis("env -C sub tee ~+/f").redirects, [("~+/f", None)])
        # a glob that can become a name the hook samples is read each way, the directory unknown among them
        self.assertIn(None, self.write_dirs("env -C gi? touch f"))

    def test_a_value_option_that_moves_nothing_leaves_the_directories(self):
        R = frozenset([self.R])
        for command in ("sudo -C 3 touch f", "doas -C /etc/doas.conf touch f", "nice -n 5 touch f", "timeout -s KILL 5 touch f",
                        "env -u C touch f", "env -S '-u C' touch f", "env --unset=C touch f"):
            with self.subTest(command):
                self.assertEqual(self.write_dirs(command), [R])

    # -- the hook -------------------------------------------------------------------
    def test_the_tickets_lines_are_refused_for_a_member(self):
        """Silent on main for AGENT_C, whose home:** covers the line's own directory."""
        for command, needle in (("env -C tests/fake/.git/hooks tee post-index-change", GIT_DIR_WORDING),
                                ("env -C tests/fake/.git/hooks sh -c 'echo x > post-index-change'", GIT_DIR_WORDING),
                                ("env -C ledger/tickets touch SPD-001.md", "generated"),
                                ("env -C ledger/tickets tee SPD-001.md", "generated"),
                                ("sudo -D ledger/tickets touch SPD-001.md", "generated"),
                                ("sudo --chdir=ledger/tickets touch SPD-001.md", "generated"),
                                ("sudo --chdir ledger/tickets sed -i '' s/a/b/ SPD-001.md", "generated")):
            with self.subTest(command):
                r = self.assertRefused(command, needle, AGENT_C)
                self.assertIn("SPD-001.md" if "ledger" in command else "post-index-change", r.reason)

    def test_a_nested_repository_is_refused_as_a_cd_into_it_is(self):
        for command in ("env -C tests/fake git status", "env -C %s git log --oneline" % self.nested, "sudo -D tests/fake git status",
                        "env -C tests env -C fake git fetch", "cd tests/fake; git status"):
            for agent_id in (AGENT_A, AGENT_C):
                with self.subTest(command=command, agent_id=agent_id):
                    r = self.assertRefused(command, GIT_NESTED_WORDING, agent_id)
                    self.assertIn(str(self.nested), r.reason)
            with self.subTest(command=command, agent_id="spud"):  # SPD-123: it lies in a checkout the ledger knows
                self.assertIn(SPUD_PLANTED_WORDING, self.assertRefused(command, GIT_NESTED_WORDING, agent_id=None).reason)

    def test_a_harmless_move_stays_silent(self):
        scratchpad = "/private/tmp/claude-%d/-Users-Someone-Personal-Spud/%s/scratchpad" % (os.getuid(), SESSION)
        for command in ("env -C tests/out tee f", "env -C tests/out touch new.txt", "env -C %s tee probe.txt" % scratchpad,
                        "env -C /tmp tee spd-128-x", "sudo -D tests/out touch f", "sudo --chdir=tests/out tee f",
                        "env -C tests git status", "env -C tests/fake/.git/hooks ls", "env -C ledger/tickets cat SPD-001.md"):
            for agent_id in (AGENT_A, AGENT_C):
                with self.subTest(command=command, agent_id=agent_id):
                    self.assertSilent(command, agent_id)

    def test_a_redirection_on_the_wrappers_line_opens_where_the_shell_is(self):
        self.assertRefused("env -C tests/out echo x > ledger/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertRefused("env -C tests/out tee f < /dev/null > ledger/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertSilent("env -C ledger/tickets echo x > tests/out/f")
        self.assertRefused("env -C ledger/tickets tee tests/out/f", "generated")  # tee's own operand: in ledger/tickets

    def test_a_spud_call_is_read_where_the_wrapper_moves_it(self):
        """A launcher reached by a relative path under another name (SPD-029: a symlink runs it whatever it is called) is
        the file in the directory the command runs in: from tests/, bin/launch behind `env -C ..` is the tool's launcher,
        where main read tests/bin/launch, no file at all, and let `--as spud` through."""
        tool = self.home.tool
        (tool / "bin" / "launch").symlink_to(self.home.launcher)
        (tool / "tests").mkdir(exist_ok=True)
        tests = str(tool / "tests")
        for command in ("env -C .. bin/launch --as spud ticket new x", "env -C .. python3.14 -I -S bin/launch --as spud board",
                        "sudo -D .. python3.14 -I -S bin/launch --as spud board", "env -C %s ./bin/launch --as spud board" % tool):
            with self.subTest(command):
                self.assertRefused(command, "Law 6", cwd=tests)
        self.assertSilent("env -C .. python3.14 -I -S bin/launch --as %s board" % AGENT_A, cwd=tests)

    def test_a_symlink_resolves_before_a_dotdot_after_it(self):
        """As chdir(2) resolves it: tests/deep/.. is ledger/, the link target's parent, not tests/."""
        (self.home.path / "tests" / "deep").symlink_to(self.home.path / "ledger" / "tickets")
        r = self.assertRefused("env -C tests/deep/.. touch SPD-001.md", "generated")
        self.assertIn("ledger/SPD-001.md", r.reason)

    def test_an_unknown_directory_fails_closed(self):
        """In a scratch directory outside every project, where a relative write is every caller's own: after a move the
        hook cannot follow it is refused, as after `cd $X`, Spud included (SPD-035)."""
        scratch = tempfile.mkdtemp(prefix="spd-128-", dir="/tmp")
        self.addCleanup(shutil.rmtree, scratch, True)
        for command in ("env -C s?b touch f", "env -C sub touch *.txt", "sudo -D s?b touch f", "cd $X; touch f"):
            for agent_id in (AGENT_A, None):
                with self.subTest(command=command, agent_id=agent_id):
                    self.assertRefused(command, "cannot follow", agent_id, cwd=scratch)
        self.assertRefused("env -C $D touch f", "cannot follow", agent_id=None, cwd=scratch)
        self.assertRefused("env -C $D touch f", WORD_WORDING, cwd=scratch)  # a member hears SPD-043's reason first
        for command in ("env -C sub touch f", "env -C /tmp touch f", "cd $X; env -C /tmp touch f"):
            for agent_id in (AGENT_A, None):
                with self.subTest(command=command, agent_id=agent_id):
                    self.assertSilent(command, agent_id, cwd=scratch)


class SettledCdTargetTest(BashHookCase):
    """SPD-147: directories.cd_target returned None for any word holding an expansion, so after `S=<dir>; cd "$S/x"` the
    hook knew no directory and refused every relative write on the line, Spud's too (SPD-035), while SPD-127 already put
    that very value in a redirection target, a tee operand, a git write option and a write by argument through
    arg_writes.resolved.  SPD-126's differential over 945 member commands found a member editing a copy in its own
    scratchpad refused with "cannot follow".  One reading of a variable holds for the directory a write is relative to as
    well: a cd, pushd or chdir target, and an `env -C`/`sudo -D` value, are resolved as a write target is, and only a
    value the line cannot settle (not assigned, doubted, a loop's, holding a blank, a glob or a `~` the expansion does not
    expand, appended, a substitution) keeps the refusal."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.scratch = str(Path(tempfile.mkdtemp(prefix="spd-147-")).resolve())
        self.addCleanup(shutil.rmtree, self.scratch, True)
        (Path(self.scratch) / "perturb" / "collab").mkdir(parents=True)
        (Path(self.scratch) / "perturb" / "collab" / "index.ts").write_text("a\n", encoding="utf-8")
        (self.home.path / "ledger" / "tickets").mkdir(parents=True, exist_ok=True)

    def test_the_differentials_line_is_followed(self):
        line = "set -e\nS=%s\ncd \"$S/perturb/collab\"\nperl -i -pe 's/a/b/' index.ts" % self.scratch
        self.assertSilent(line, agent_id=None, cwd=self.scratch)
        # A member's in-place perl program is refused on its own since SPD-175 (its text writes), not for the directory
        r = self.bash(line, AGENT_A, cwd=self.scratch)
        self.assertNotIn("cannot follow", r.reason)
        self.assertIn("runs a program `-e` carries", r.reason)
        for agent_id in (AGENT_A, None):
            with self.subTest(agent_id=agent_id):
                self.assertSilent(line.replace("perl -i -pe 's/a/b/' index.ts", "echo x > index.ts"), agent_id, cwd=self.scratch)
                self.assertSilent(line.replace("perl -i -pe 's/a/b/' index.ts", "sed -i '' s/a/b/ index.ts"), agent_id, cwd=self.scratch)
                self.assertSilent(line.replace("perl -i -pe 's/a/b/' index.ts", "perl -pe 's/a/b/' index.ts > out.ts"), agent_id,
                                  cwd=self.scratch)

    def test_a_settled_directory_is_read_where_it_leads(self):
        home = self.home.path
        for cd in ('S=%s; cd "$S/ledger/tickets"', 'S=%s/ledger; cd ${S}/tickets', "S=%s/ledger/tickets; cd $S",
                   'S=%s/ledger; pushd "$S/tickets"', 'S=%s; cd -P "$S/ledger/tickets"'):
            cd = cd % home
            with self.subTest(cd):
                self.assertRefused(cd + "; echo x > SPD-001.md", "generated", AGENT_C)
                self.assertRefused(cd + "; perl -i -pe s/a/b/ SPD-001.md", "generated", AGENT_C)
                self.assertRefused(cd + "; echo x > SPD-001.md", "Law 1", agent_id=None)
        self.assertRefused('S=%s/ledger/tickets; env -C "$S" touch SPD-001.md' % home, "generated", AGENT_C)
        # the value in force where the cd runs, not the line's last
        self.assertRefused('S=%s/ledger/tickets; cd "$S"; S=%s; echo x > SPD-001.md' % (home, self.scratch), "generated", AGENT_C)

    def test_an_unsettled_value_stays_unfollowable(self):
        s = self.scratch
        for cd in ('cd "$S/perturb"', 'true && S=%s; cd "$S"' % s, '(S=%s); cd "$S"' % s, 'for S in %s; do :; done; cd "$S"' % s,
                   'for i in 1; do S=%s; cd "$S"; done' % s, 'S="%s/a b"; cd "$S"' % s, "S='%s/*'; cd $S" % s, "S='~'; cd \"$S\"",
                   'S=%s; S+=/perturb; cd "$S"' % s, 'S=; cd "$S"', 'cd "$(pwd)"', 'S=%s; cd "$S$T"' % s, "S=-; cd \"$S\""):
            for agent_id in (AGENT_A, None):
                with self.subTest(cd=cd, agent_id=agent_id):
                    self.assertRefused(cd + "; echo x > note.txt", "cannot follow", agent_id, cwd=s)
        # a wrapper's directory: Spud hears the directory's reason, a member SPD-043's var-word one first
        self.assertRefused('S=%s; env -C "$S$T" touch f' % s, "cannot follow", agent_id=None, cwd=s)
        self.assertRefused('S=%s; env -C "$S$T" touch f' % s, WORD_WORDING, cwd=s)
        self.assertSilent('S=%s; env -C "$S/perturb" touch f' % s, agent_id=None, cwd=s)


class GluedByNameWordTest(BashHookCase):
    """SPD-141: a word the hook reads by name (SPD-043: git's options and the value -c sets, a wrapper's options, a shell's)
    settled only a word that is an expansion whole, so `git -C $D status` was read with D's value in place while
    `git --git-dir=$D/.git status` on the same line earned var-word, and `P=cat; git -c core.pager=$P log` was refused
    although core.pager=cat is inert.  The glued word is now read as SPD-127 reads a write target (arg_writes.resolved):
    every expansion in it put in place when the line settled its value as one plain word, and the word then read as
    spelled; a value the line cannot settle (not assigned, doubted, holding a blank or a glob, one of two expansions
    unsettled) keeps the var-word refusal.  Also (proposal 325): a double-quoted `"${NAME}"` reached the reading with its
    braces as quoted glob sentinels, so resolved settled `$S`, `"$S"` and `${S}` but not `"${S}"`, in a write target and,
    after SPD-147, in a cd target; it is now marked as `"$S"` is and settled the same way."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spd-141-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        self.repo = self.out / "repo"
        (self.repo / ".git" / "objects").mkdir(parents=True)
        (self.repo / ".git" / "refs" / "heads").mkdir(parents=True)
        (self.repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (self.repo / ".git" / "config").write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")
        (self.home.path / "ledger" / "tickets").mkdir(parents=True, exist_ok=True)

    def finding(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path))).findings

    def test_the_tickets_probed_lines_are_read_with_the_value_in_place(self):
        d = self.out
        for spelled in ("--git-dir=$D/.git", "--git-dir=${D}/.git", '--git-dir="$D/.git"', '--git-dir="${D}/.git"',
                        '"--git-dir=$D/.git"'):
            with self.subTest(spelled):
                self.assertEqual(self.finding("D=%s; git %s status" % (d, spelled)),
                                 [("git", ("status", None)),
                                  ("git-repo", ("--git-dir=%s/.git" % d, "%s/.git" % d, frozenset({str(self.home.path)})))])
        self.assertEqual(self.finding("P=cat; git -c core.pager=$P log"), self.finding("git -c core.pager=cat log"))
        self.assertEqual(self.finding('P=cat; git -c "core.pager=$P" log'), self.finding("git -c core.pager=cat log"))
        self.assertEqual(self.finding("P=vim; git -c core.editor=$P log"), self.finding("git -c core.editor=vim log"))

    def test_a_settled_glued_word_is_silent_where_its_value_is(self):
        home = self.home.path
        for ok in ("D=%s; git --git-dir=$D/.git status" % home, "D=%s; git --work-tree=${D}/.claude/worktrees/w status" % home,
                   'D=%s; git --git-dir="${D}/.git" log' % home, "P=cat; git -c core.pager=$P log",
                   "P=cat; git -c core.pager=\"$P\" log", "K=pager; git -c core.$K=cat log"):
            for agent_id in (AGENT_A, None):
                with self.subTest(ok=ok, agent_id=agent_id):
                    self.assertSilent(ok, agent_id)

    def test_the_value_in_place_earns_its_own_refusal(self):
        r = self.assertRefused("D=%s; git --git-dir=$D/.git status" % self.repo, "outside", AGENT_C)
        self.assertIn(str(self.repo), r.reason)
        self.assertRefused("P=vim; git -c core.pager=$P log", "Law 7", AGENT_C)
        self.assertRefused("E=vim; git -c core.$E=x log", "Law 7", AGENT_C)
        self.assertSilent("P=vim; git -c core.pager=$P log", agent_id=None)

    def test_an_unsettled_glued_word_stays_refused(self):
        s = self.out
        for cmd in ("git --git-dir=$D/.git status", "true && D=%s; git --git-dir=$D/.git status" % s,
                    "(D=%s); git --git-dir=$D/.git status" % s, "D=%s; git --git-dir=$D$E/.git status" % s,
                    "D='%s/a b'; git --git-dir=$D/.git status" % s, "D='%s/*'; git --git-dir=$D/.git status" % s,
                    "for D in %s; do git --git-dir=$D/.git status; done" % s, "D=%s; D+=/x; git --git-dir=$D/.git status" % s,
                    "git -c core.pager=$P log", "P='less -R'; git -c core.pager=$P log", "P='c*t'; git -c core.pager=$P log",
                    "true && P=cat; git -c core.pager=$P log", "git --git-dir=$(pwd)/.git status", "P=cat; git -c core.pager=${P:-x} log",
                    # bash splits at IFS's characters; a `~` an expansion gives stays literal
                    "IFS=.; P=cat; git -c core.pager=$P log", "D='~'; git --git-dir=$D/.git status"):
            for agent_id in (AGENT_C, AGENT_A):
                with self.subTest(cmd=cmd, agent_id=agent_id):
                    self.assertEqual(self.bash(cmd, agent_id).decision, "deny")
        # the repository option's own reason comes first, as for `git --git-dir=$D status` (SPD-047); var-word stays found
        self.assertRefused("D=%s; git --git-dir=$D$E/.git status" % s, "cannot resolve", AGENT_C)
        self.assertIn(("var-word", "--git-dir=$D$E/.git"), self.finding("D=%s; git --git-dir=$D$E/.git status" % s))
        self.assertRefused("D=%s; nice -n $D$E git status" % s, WORD_WORDING, AGENT_C)
        self.assertIn(("var-word", "core.pager=$P"), self.finding("git -c core.pager=$P log"))
        self.assertIn(("var-word", "core.pager=$P"), self.finding("P='less -R'; git -c core.pager=$P log"))

    def test_a_quoted_braced_name_is_settled_in_a_write_target(self):
        home, s = self.home.path, self.out
        for write in ('S=%s/ledger/tickets; echo x > "${S}/SPD-001.md"', 'S=%s/ledger; touch "${S}/tickets/SPD-001.md"',
                      'S=%s/ledger/tickets; echo x | tee "${S}/SPD-001.md"'):
            with self.subTest(write):
                self.assertRefused(write % home, "generated", AGENT_C)
        for ok in ('S=%s; echo x > "${S}/f"', 'S=%s; touch "${S}/f"', 'S=%s; echo x | tee "${S}"/f'):
            for agent_id in (AGENT_A, None):
                with self.subTest(ok=ok, agent_id=agent_id):
                    self.assertSilent(ok % s, agent_id, cwd=str(s))
        # unsettled, it keeps the unresolvable-target refusal
        for bad in ('echo x > "${S}/f"', 'S="%s/a b"; echo x > "${S}/f"' % s, 'true && S=%s; echo x > "${S}/f"' % s):
            with self.subTest(bad):
                self.assertRefused(bad, VARIABLE_WORDING, AGENT_A, cwd=str(s))

    def test_a_quoted_braced_name_is_settled_in_a_cd_target(self):
        home, s = self.home.path, self.out
        self.assertRefused('S=%s/ledger/tickets; cd "${S}"; echo x > SPD-001.md' % home, "generated", AGENT_C)
        self.assertRefused('S=%s/ledger; pushd "${S}/tickets"; echo x > SPD-001.md' % home, "generated", AGENT_C)
        for agent_id in (AGENT_A, None):
            with self.subTest(agent_id=agent_id):
                self.assertSilent('S=%s; cd "${S}"; echo x > note.txt' % s, agent_id, cwd=str(s))
        for bad in ('cd "${S}"; echo x > note.txt', 'S="%s/a b"; cd "${S}"; echo x > note.txt' % s):
            with self.subTest(bad):
                self.assertRefused(bad, "cannot follow", AGENT_A, cwd=str(s))


# The unread form (SPD-217) a member earns for an assignment whose name the hook cannot read (SPD-225, SPD-254)
NAME_UNREAD = "whose name the hook cannot read"
EVALUATED_UNREAD = "evaluates as code"


class ArithmeticAssignmentTest(BashHookCase):
    """SPD-225 (proposal by SPUD-221/Howard): the hook settles a variable the line assigned (SPD-127, SPD-146, SPD-221) but
    read no assignment an arithmetic evaluation makes, so `X=tests; ((X=5)); touch $X/a` was read as tests/a while the
    shell writes 5/a -- a member planned tests/** was let write outside it, and `out=$(basename /x/z); ((out=3)); touch
    "tests/$out"` was read as tests/z.

    Probed in zsh 5.9 -f, zsh -f -o nobareglobqual and bash 3.2.57 (tests/probes/shell_probe.py, 2026-09-24), each line
    starting from `X=tests`, printing X after it:
    `((X=5))`, `echo $((X=5))`, `/bin/echo $((X=6))`, `Y=$((X=8))`, `let X=9`, `let 'X = 10' 'Z=11'`, `echo $[X=12]`,
    `(( X = 5, W = 6 ))`, `(( X = W = 3 ))`, `echo "$((X=16))"`, `case $((X=27)) in`, `[[ $(( X = 58 )) == 58 ]]`, a
    redirection target's `$(( X = 63 ))` and `: <<EOF` fed `$((X=26))` assigned in all three; `echo $((X=5)) $((X=6))` left 6;
    `(( X = 012 ))` printed 12 in zsh and 10 in bash, `(( X = 5.5 ))` 5.5 in zsh and an error in bash, `(( X = 99999999999999999999 ))`
    two different wrapped numbers; `Y=$((X=7)) /usr/bin/true` assigned in bash alone, `cat <<EOF` fed `$((X=26))` in
    neither (bash expands an external command's body in its child), `cat /dev/null | (( X = 9 ))` in zsh alone;
    `true && (( X = 13 ))` assigned, and `( (( X = 14 )) )`, `echo $((X=15)) | cat`, `(( X = 10 )) | cat`, `: $((X=11)) &`,
    `(( 0 && (X = 30) ))`, `(( 1 || (X = 31) ))`, `: ${Q3:-$((X=25))}` with Q3 set, `(( X == 5 ))` and `[[ X=61 == 61 ]]`
    did not.  A name's value is evaluated as arithmetic again: `Y='X=18'; (( Y ))`, `echo $((Y))` and `$(( Y + 1 ))` each
    assigned X, and `N=X; (( $N = 21 ))` assigned X.  Every other place the shells evaluate arithmetic assigns as well:
    `[[ X=22 -eq 22 ]]` and `[[ 1 -lt X=60 ]]` (not `[ 1 -eq 1 ]`), `${arr[X=2]}`, `arr[X=1]=q`, a `for (( ... ))` header,
    bash's `${s:X=1:2}` (zsh reads that `:X` as a modifier and stops; `${s:$((X=1)):2}` assigned in both); and a name with
    the integer attribute evaluates what it is assigned: `declare -i X; X=3+4` left 7, `X=tests` 0, `typeset -i X; X='T=12'`
    set T to 12 as well, zsh's `integer X=3+4` 7 and `typeset -i 16 X; X=255` 16#FF.

    The reading now: every such form records its names as the line's assignments where the shell makes them, with the value
    the hook can know -- a decimal literal both shells read alike, assigned where the evaluation surely runs and persists --
    and otherwise a value it cannot know, which refuses a member where the name is read by name or in a write target as a
    value the line did not settle always did.  An assignment whose name the hook cannot read (`(( $N = 5 ))` with N
    unsettled) refuses a member unread (SPD-217).  AGENT_A plans tests/** and bin/spud; the home is the cwd."""

    def analysis(self, line):
        m = load_spud_module()
        return m.analyse_command(line, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def written(self, line):
        """The files the line's writes by argument name, as the reading resolves them."""
        m = load_spud_module()
        return [m.deglob(entry[1]) for entry in self.analysis(line).arg_writes]

    def test_the_tickets_evidence(self):
        self.assertEqual(self.written("X=tests; ((X=5)); touch $X/a"), ["5/a"])
        self.assertEqual(self.written("X=tests; echo $((X=5)); touch $X/a"), ["5/a"])
        self.assertEqual(self.written('out=$(basename /x/z); ((out=3)); touch "tests/$out"'), ["tests/3"])
        # a member planned tests/** was let write where the shell does not
        self.assertRefused("X=tests; ((X=5)); touch $X/a", "deliverables")
        self.assertRefused("X=tests; echo $((X=5)); touch $X/a", "deliverables")
        self.assertSilent('out=$(basename /x/z); ((out=3)); touch "tests/$out"')
        # ... and Spud's write is read where the shell puts it: 5/SPD-001.md, not the generated ticket note
        (self.home.path / "ledger" / "tickets").mkdir(parents=True, exist_ok=True)
        self.assertRefused("X=ledger/tickets; echo x > $X/SPD-001.md", "into ledger/tickets/SPD-001.md", agent_id=None)
        self.assertRefused("X=ledger/tickets; ((X=5)); echo x > $X/SPD-001.md", "into 5/SPD-001.md", agent_id=None)

    def test_each_form_records_the_literal_it_assigns(self):
        for form in ("((X=5))", "(( X = 5 ))", "(( X=5 ))", "echo $((X=5))", 'echo "$((X = 5))"', "echo $[X=5]", 'echo "$[X=5]"',
                     "echo $(( X = 5 ))x", "let X=5", "let 'X = 5'", 'let "X=5"', "let -- X=5", "let Y=1 X=5", "(( Y = 1, X = 5 ))",
                     "(( (X = 5) ))", "(( X = +5 ))", "Y=$((X=5))", "Y=1 Z=$((X=5))", ": $((X=5))", "echo $((X=4)) $((X=5))",
                     "[[ $((X=5)) == 5 ]]", "((X=5)) && true", "((X=5)) || true", "((X=5)); true",
                     "((X=5)) > /dev/null", "cat /dev/null | true; ((X=5))", "N=X; (( $N = 5 ))", "N=X; let $N=5",
                     "Y='X=5'; (( Y ))", "Y='X=5'; echo $(( Y + 1 ))", "N='X = 5'; (( $N ))", "for i in 1; do :; done; ((X=5))",
                     "/usr/bin/true $((X=5))", "X=b /usr/bin/true $((X=4)); X=a; : $((X=5))", "echo $[ X = 5 ]", "echo a$[ X = 5 ]b"):
            with self.subTest(form):
                self.assertEqual(self.written("X=a; %s; touch tests/$X" % form), ["tests/5"])
                self.assertSilent("X=a; %s; touch tests/$X" % form)
        self.assertEqual(self.written("X=a; (( X = -5 )); touch tests/$X"), ["tests/-5"])
        # the shells expand an arithmetic expansion inside the expression before they evaluate it (probed: both left 6)
        self.assertEqual(self.written("X=a; echo $(( (X = 6) + $(( X = 5 )) )); touch tests/$X"), ["tests/6"])

    def test_a_value_the_hook_cannot_compute_is_unknown(self):
        """A compound assignment, an increment, a number zsh and bash read apart (a leading 0 is octal to bash, a float is
        zsh's alone, a number past 64 bits wraps differently), an expression, and an assignment under `?:`, `&&` or `||`,
        which may not run."""
        for form in ("((X++))", "((++X))", "((X--))", "((--X))", "(( X ++ ))", "((X+=1))", "((X-=1))", "((X*=2))", "((X/=2))",
                     "((X%=2))", "((X<<=1))", "((X>>=1))", "((X&=1))", "((X|=1))", "((X^=1))", "((X**=2))", "((X = 012))",
                     "((X = 05))", "((X = 0x10))", "((X = 2#101))", "((X = 1e3))", "((X = 5.5))", "((X = 99999999999999999999))",
                     "((X = 1 + 2))", "((X = Y))", "((X = 5 == 5))", "((X = W = 5))", "((X = 5, X++))", "(( c ? (X = 5) : 0 ))",
                     "(( 0 && (X = 5) ))", "(( 1 || (X = 5) ))", "(( a[X = 1] ))", "let X++", "let 'X += 1'", "echo $((X++))",
                     "echo $[X+=1]", "Y='X++'; (( Y ))", "N=X; (( $N++ ))", "setopt force_float; ((X=5))"):
            with self.subTest(form):
                self.assertEqual(self.written("X=tests; %s; touch $X/a" % form), ["$X/a"])
                self.assertRefused("X=tests; %s; touch $X/a" % form, VARIABLE_WORDING)

    def test_an_assignment_that_may_not_run_or_persist_leaves_the_value_unknown(self):
        for line in ("true && ((X=5))", "false || ((X=5))", "((X=5)) | cat", "cat /dev/null | ((X=5))", "( ((X=5)) )",
                     "{ ((X=5)); }", "if ((X=5)); then :; fi", "while ((X=5)); do break; done", "((X=5)) &", "echo $((X=5)) | cat",
                     "echo $((X=5)) &", ": ${Q:-$((X=5))}", ": ${Q:+$((X=5))}", 'echo "${Q:-$((X=5))}"', "Y=$((X=5)) true",
                     "Y=$((X=5)) /usr/bin/true", "for i in 1; do ((X=5)); done", "f() { ((X=5)); }", "true && let X=5",
                     "true && echo $((X=5))", "echo $(echo $((X=5)))", "cat <<EOF\n$((X=5))\nEOF\ntrue",
                     # a redirection's target, expanded in an external command's own process (`/bin/echo hi > f$((X=5))`
                     # left X in all three shells), and a prefix, which bash expands after the command's own words
                     "/bin/echo hi > f$((X=5))", "echo hi 2> f$((X=5))", "cat <<< $((X=5))", "cat < /dev/null$((X=5))",
                     "Y=$((X=5)) /usr/bin/true $((X=6))",
                     # a compound command's word is doubted with the rest of what the compound assigns
                     "case $((X=5)) in *) ;; esac"):
            with self.subTest(line):
                self.assertEqual(self.written("X=tests; %s; touch $X/a" % line), ["$X/a"])
                self.assertRefused("X=tests; %s; touch $X/a" % line, VARIABLE_WORDING)
        # a quoted delimiter's body is not expanded
        self.assertEqual(self.written("X=tests; cat <<'EOF'\n$((X=5))\nEOF\ntouch $X/a"), ["tests/a"])

    def test_a_name_the_hook_cannot_read_refuses_a_member(self):
        for line in ("(( $N = 5 ))", "(( ${N} = 5 ))", "(( $N++ ))", "(( ++$N ))", "(( $N += 1 ))", "echo $(( $N = 5 ))",
                     'echo "$(( $N = 5 ))"', "let $N=5", 'let "$N = 5"', "(( $(echo X) = 5 ))", "(( ${N:-X} = 5 ))",
                     "(( a[1] = 1, $N = 2 ))", "(( $N[1] = 2 ))", "true && (( $N = 5 ))", "echo $[ $N = 5 ]"):
            with self.subTest(line):
                self.assertRefused(line, NAME_UNREAD)
                self.assertSilent(line, agent_id=None)

    def test_the_older_arithmetic_expansion_is_one_word(self):
        """`$[ ... ]` is one word to both shells whatever blanks and operators it holds (probed: `echo $[ 3 > 2 ]` printed 1
        and made no file 2 in all three), where the walk once read `$[`, `3`, a redirection to 2 and `]`."""
        for line in ("echo $[ 3 > 2 ]", "echo $[ 1 | 2 ]; echo x", "x=$[ 1 < 2 ]"):
            with self.subTest(line):
                self.assertEqual(self.analysis(line).redirects, [])
                self.assertSilent(line)
        self.assertEqual([t for t, _ in self.analysis("echo $[ 3 > 2 ] > out.txt").redirects], ["out.txt"])
        self.assertRefused("$[ 1 ] push", "spell the command out")  # a number names a command on a PATH of the line's own

    def test_a_for_header_assigns_its_names(self):
        self.assertEqual(self.written("X=tests; for ((X=0; X<1; X++)); do :; done; touch $X/a"), ["$X/a"])
        self.assertRefused("X=tests; for ((X=0; X<1; X++)); do :; done; touch $X/a", VARIABLE_WORDING)
        self.assertEqual(self.written("X=tests; for (( i = 0; i < 1; i++ )); do :; done; touch $X/a"), ["tests/a"])

    def test_every_other_place_the_shells_evaluate_arithmetic(self):
        for line in ("[[ X=5 -eq 5 ]]", "[[ 1 -lt X=5 ]]", "[[ X++ -ge 0 ]]", "[[ -n y && X=1 -ne 0 ]]", "a[X=1]=q", "a[X++]=q",
                     "echo ${a[X=1]}", 'echo "${a[X=1]}"', "echo ${s:X=1:2}", "echo ${#a[X=1]}", "declare a[X=1]=q",
                     "unset 'a[X=1]'", "read 'a[X=1]' < f"):
            with self.subTest(line):
                self.assertEqual(self.written("X=tests; %s; touch $X/a" % line), ["$X/a"])
        for line in ("[[ X=5 == 5 ]]", "[ X=5 -eq 5 ]", "test X=5 -eq 5", "[[ X -eq 5 ]]", "echo ${a[1]}", "a[1]=q", 'echo "a[X=1]"',
                     "echo 'a[X=1]'", "echo '${a[X=1]}'", "echo '$((X=5))'", "echo \\${a[X=1]}"):
            with self.subTest(line):
                self.assertEqual(self.written("X=tests; %s; touch $X/a" % line), ["tests/a"])

    def test_a_name_with_the_integer_attribute_evaluates_what_it_is_assigned(self):
        for line in ("typeset -i X; X=3+4", "declare -i X; X=tests", "declare -i X=5", "typeset -i X=tests", "integer X=3+4",
                     "integer X; X=1", "float X=1", "typeset -F X; X=1", "typeset -E X; X=1", "declare -ix X; X=1",
                     "typeset -i 16 X; X=255", "typeset -i X; read X < f; X=1"):
            with self.subTest(line):
                self.assertEqual(self.written("X=tests; %s; touch $X/a" % line), ["$X/a"])
        # ... and what it is assigned may assign another name (`typeset -i X; X='T=12'` set T to 12 in both shells)
        for line, target in (("typeset -i X; X=T=12", "12/a"), ("typeset -i X; X='T = 12'", "12/a"), ("integer X=T=12", "12/a"),
                             ("declare -i X='T++'", "$T/a"), ("typeset -i X; X=T++", "$T/a")):
            with self.subTest(line):
                self.assertEqual(self.written("T=tests; %s; touch $T/a" % line), [target])
        # another name reads as it did
        self.assertEqual(self.written("Y=tests; typeset -i X; X=3; touch $Y/a"), ["tests/a"])

    def test_arithmetic_that_assigns_nothing_changes_nothing(self):
        for line in ("(( X > 2 ))", "(( X == 5 ))", "(( X != 5 ))", "(( X <= 5 ))", "(( X ))", "echo $(( X + 1 ))", "echo $((X*2))",
                     "x=$(( 1 > 2 ))", "(( a[1] + 2 ))", "(( $n > 2 ))", "(( ${n} > 2 ))", "echo $[X+1]", "let 'X > 2'", "let X==5",
                     "(( Y = 1 ))", "echo $(( Y = 1 ))", "for (( i = 0; i < 1; i++ )); do :; done", "Y='Z=1'; (( Y ))"):
            with self.subTest(line):
                self.assertEqual(self.written("X=tests; %s; touch $X/a" % line), ["tests/a"])
                self.assertSilent("X=tests; %s; touch $X/a" % line)


class AssigningBuiltinTest(BashHookCase):
    """SPD-254 (proposal by SPUD-246/Oliver): a name an assigning builtin sets (read, printf -v, getopts, mapfile ...) was
    only doubted, never recorded as the line's assignment, and its words were read loosely -- every identifier in every
    word doubted, `printf '%s' X` and `unset -f X` among them.  SPD-205 keeps a function body's finding that names a
    variable the line assigned (SPD-246: the line's variables are what survives in its shell), so `read GITVERB < f;
    globalgit` passed a member where `GITVERB=$(echo push); globalgit` was refused (test_hooks_snapshots has the bodies).

    Each builtin's own name operands are now read by its own grammar -- the options that take a value, `--`, the options
    whose value is a name -- in zsh's reading and bash's alike, and each name is recorded as the line's assignment with a
    value the hook does not know.  Probed in zsh 5.9 -f, zsh -f -o nobareglobqual and bash 3.2.57 (tests/probes/shell_probe.py,
    2026-09-24), fed `a b`: `read -t 1 X Y` gave X=a Y=b in all three, `read -n 1 X Y` X=b in zsh (its -n takes no value, so
    `1` took a) and X=a in bash; `read -p P X` failed in zsh (-p is its coprocess) and gave X in bash (P the prompt); `read
    'X?prompt'` gave X in zsh alone, `read -a A` bash's array, `read -A A` zsh's; `read -tX Y` took X for the timeout in
    both; `read -e X` echoed in zsh and assigned in bash; `read -r -- X`, `-d , X`, `-d, X`, `-u 0 X` and `-rt1 X` gave X
    in all three.  `printf -v X` assigned in both, `printf -vX` in bash alone (zsh printed `-vX`), `printf -- -v X` in
    neither; `print -v X` and `print -rv X` in zsh.  `getopts ab X -a` gave X=a.  `unset -f X` kept the variable X,
    `unset -v X` unset it, zsh's `unset -m 'X*'` unset X by a pattern.  zsh's `set -A X a b`, `set -sA X b a` and
    `set +A X c`, `zstyle -s ':x' y V` and `zformat -f V '%a' a:1` each assigned their name."""

    def analysis(self, line):
        m = load_spud_module()
        return m.analyse_command(line, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def names(self, line):
        """The names the line assigned whose value the hook does not know."""
        m = load_spud_module()
        a = self.analysis(line)
        return {n for n in a.line_assigned if m.SUBST in a.vars.get(n, "") or a.vars.get(n) == "$"}

    def test_each_builtin_s_names_by_its_own_grammar(self):
        for line, names in (
                ("read X", {"X"}), ("read -r X Y", {"X", "Y"}), ("read", {"REPLY"}), ("read -r", {"REPLY"}), ("read -p P X", {"P", "X"}),
                ("read -p 'Enter a name' X", {"X"}), ("read -t 1 X", {"X"}), ("read -tX Y", {"Y"}), ("read -rt1 X", {"X"}),
                ("read -d , X", {"X"}), ("read -d, X", {"X"}), ("read -u 0 X", {"X"}), ("read -n 1 X", {"X"}), ("read -k 1 X", {"X"}),
                ("read -a A", {"A"}), ("read -A A", {"A"}), ("read -ra A", {"A"}), ("read 'X?prompt: '", {"X"}), ("read -r -- X", {"X"}),
                ("read -e X", {"X"}), ("read -s -r X", {"X"}), ("read 'a[1]'", {"a"}),
                ("printf -v X %s hi", {"X"}), ("printf -vX %s hi", {"X"}), ("printf -v X -- %s", {"X"}), ("printf -- -v X", set()),
                ("printf %s X", set()), ("printf '%s' X Y", set()), ("print -v X hi", {"X"}), ("print -rv X hi", {"X"}),
                ("print -rn X", set()), ("print -u 2 -v X hi", {"X"}),
                ("getopts ab X", {"X", "OPTARG", "OPTIND"}), ("getopts ab X -a", {"X", "OPTARG", "OPTIND"}),
                ("mapfile X", {"X"}), ("mapfile -t X", {"X"}), ("mapfile", {"MAPFILE"}), ("mapfile -u 3 -O 1 -n 2 X", {"X"}),
                ("readarray -d , -s 1 X", {"X"}), ("readarray -t", {"MAPFILE"}),
                ("unset X", {"X"}), ("unset -v X Y", {"X", "Y"}), ("unset -f X", set()), ("unset 'a[1]'", {"a"}), ("unset -n X", {"X"}),
                ("wait -p X", {"X"}), ("wait", set()), ("wait 1", set()),
                ("set -A X a b", {"X"}), ("set -sA X b a", {"X"}), ("set +A X c", {"X"}), ("set -- a b", set()), ("set -e", set()),
                ("set -o pipefail", set()),
                ("vared X", {"X"}), ("vared -p P -c X", {"X"}), ("getln X Y", {"X", "Y"}), ("getln -A X", {"X"}),
                ("zstyle -s ctx st X", {"X"}), ("zstyle -s ctx st X :", {"X"}), ("zstyle -a ctx st X", {"X"}), ("zstyle -b ctx st X", {"X"}),
                ("zstyle -g X", {"X"}), ("zstyle -g X ctx st", {"X"}), ("zstyle ':x' y z", set()), ("zstyle -t ctx st v", set()),
                ("zformat -f X '%a' a:1", {"X"}), ("zformat -a X : a:b", {"X"}),
                ("zparseopts -a A h=H -help=H2", {"A", "H", "H2"}), ("zparseopts -D -E -A O v+:=V", {"O", "V"}), ("zparseopts h", set()),
                ("strftime -s X %Y", {"X"}), ("strftime %Y", set()), ("sysread X", {"X"}), ("sysread -c N X", {"N", "X"}), ("sysread", {"REPLY"}),
                ("zstat -A A f", {"A"}), ("zstat -H H f", {"H"}), ("zselect -a A 0", {"A"}), ("zselect -A A 0", {"A"}), ("zselect 0", {"reply"}),
                ("zsystem flock -f V f", {"V"}), ("zsystem supports x", set()), ("V=X; read $V", {"X"}), ("V=X; printf -v \"$V\" %s y", {"X"}),
                ("zregexparse p q a", {"p", "q"}), ("zpty -r w X", {"X"}), ("zpty -w w hi", set()), ("zsocket -l x", {"REPLY"}),
                ("zle -N X", set()), ("compadd -A X a", set()), ("compset -p 1", set()), ("print -u$((2)) -v X hi", {"X"}),
                ("print -u$((2)) hi", set()), ("unset 'a[$i]'", {"a"}), ("unset a[$i]", {"a"}), ('read -r "a[$i]"', {"a"})):
            with self.subTest(line):
                self.assertEqual(self.names(line), names)

    def test_a_word_that_names_no_variable_leaves_it_as_it_was(self):
        for line in ("printf '%s' X", "printf -- -v X", "unset -f X", "print X", "read -tX Y", "getopts X Y", "zstyle ':x' X y",
                     "set -o X", "read -p X Y"):
            with self.subTest(line):
                expected = ["$X/a"] if line == "read -p X Y" else ["tests/a"]  # bash's prompt, zsh's name
                self.assertEqual([load_spud_module().deglob(e[1]) for e in self.analysis("X=tests; %s; touch $X/a" % line).arg_writes],
                                 expected)

    def test_a_name_assigned_is_unknown_wherever_it_is_read(self):
        for line in ("read X < f", "read -r X <<< a", "printf -v X %s a", "print -v X a", "getopts ab X", "mapfile X < f",
                     "unset X", "wait -p X", "set -A X a", "zstyle -s c s X", "vared X", "sysread X", "V=X; read $V"):
            with self.subTest(line):
                self.assertRefused("X=tests; %s; touch $X/a" % line, VARIABLE_WORDING)
                self.assertRefused("X=git; %s; $X push" % line, "")

    def test_a_name_the_hook_cannot_read_refuses_a_member(self):
        for line in ("read $V", 'read -r "$V"', "read -r ${V}", "read -a $V", "read -r X $V", "printf -v $V x", 'printf -v "$V" x',
                     "print -v $V x", "getopts ab $V", "unset $V", "unset -v $V", "unset -m 'X*'", "mapfile $V", "readarray -t $V",
                     "wait -p $V", "set -A $V a", "zstyle -s c s $V", "vared $V", "read $(echo X)", "read X*", "read $flags X",
                     # an option the line spells, or settles, where the builtin reads its options
                     "printf -v$V x", "X='-v Y'; printf $X hi"):
            with self.subTest(line):
                self.assertRefused(line, NAME_UNREAD)
                self.assertSilent(line, agent_id=None)
        # a value the line does not settle, where a builtin reads options, is read as the operand it is: no text the member
        # wrote makes it an option, and a function's name is no variable's
        for line in ('printf "$f\\n"', "for f in a b; do printf \"$f\\n\"; done", "sleep 1 & pid=$!; wait $pid", 'print -r -- "$x"',
                     "set -- $x", "unset -f $V", "unset -f -- $V", 'X="git %d"; printf $X 1', 'read -p "Enter $what: " x < f'):
            with self.subTest(line):
                self.assertSilent(line)

    def test_a_builtin_that_runs_code_it_is_handed_is_refused(self):
        for line in ("mapfile -C cb -c 1 X < f", "readarray -C 'echo hi' X < f", "mapfile -tC cb X < f",
                     "zstyle -e ':x' y 'reply=(a)'", "zstyle -e ':x' y 'git push'"):
            with self.subTest(line):
                self.assertRefused(line, EVALUATED_UNREAD)
                self.assertSilent(line, agent_id=None)

    def test_where_the_builtin_runs_decides_whether_the_line_keeps_it(self):
        # in the shell: certain; behind a prefix one shell runs as a program or in a fork: unknown all the same
        for line in ("read X < f", "builtin read X < f", "command read X < f", "true && read X < f", "read X < f | cat",
                     "cat f | read X"):
            with self.subTest(line):
                self.assertIn("X", self.analysis("%s; true" % line).line_assigned)
        # a program run by its path assigns nothing in the line's shell
        self.assertEqual([load_spud_module().deglob(e[1]) for e in self.analysis("X=tests; /usr/bin/read X < f; touch $X/a").arg_writes],
                         ["tests/a"])

    def test_the_value_assigned_is_read_as_before(self):
        """The builtins read here assign what the hook does not read -- input, a format's output, an option -- so a read of
        the name is refused a member as a value the line did not settle was, and Spud keeps his own checks."""
        self.assertRefused("read X < f; git $X", WORD_WORDING)
        self.assertRefused("read X < f; $X push", "spell the command out")
        self.assertSilent("read X < f; git $X", agent_id=None)
        self.assertSilent("while read -r line; do echo \"$line\"; done < f")
        self.assertSilent("IFS= read -r line < f; echo \"$line\"")
        self.assertSilent("printf -v x '%s' a; echo \"$x\"")


if __name__ == "__main__":
    unittest.main()
