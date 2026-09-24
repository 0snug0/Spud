"""PreToolUse(Bash) and the path rule on git: config aliases and the programs git runs, git's own config files and
directories, the repository a call reads, nested repositories, the files git writes, and the verb allowlist."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import SPUD, load_spud_module
from helpers import git as scratch_git
from hookcase import AGENT_A, AGENT_C, AGENT_D, GIT_DIR_WORDING, GIT_FILE_WORDING, GIT_NESTED_WORDING, GIT_SCOPE_WORDING
from hookcase import SESSION, SPUD_PLANTED_WORDING, STATE, BashHookCase, plant_git_dir, spellings


class GitConfigAliasTest(BashHookCase):
    """SPD-044: git expands an alias (a config key `alias.NAME`) into whatever command it names before it dispatches, so a
    member's write can run under a verb Law 7's table does not list.  git_verb skips `-c` and its value and reads the next word
    as the verb, so `git -c alias.p=push p` was read as verb `p` and refused nothing, while git ran push.  Probed (zsh/bash,
    scratch dir, read-only `version` as the stand-in, never push): `git -c alias.v=version v` printed the version; the joined
    `-calias.v=version` is rejected by git (`unknown option`); an alias does not override a builtin (`git -c alias.status=... status`
    ran the builtin status).  The env forms (GIT_CONFIG_COUNT/KEY_n/VALUE_n, GIT_CONFIG_PARAMETERS, GIT_CONFIG*/GLOBAL/SYSTEM) are
    documented git config injection; the harness's own worktree guard refuses them when they redirect writes, and Law 7's hook,
    which runs in every session, closes them too.  The fix refuses a member's git call whose own line defines an alias/include by
    `-c`/`--config-env` or sets a GIT_CONFIG_* variable, as an unresolvable verb; it does not inspect the value, so it also refuses
    an alias to a read (an over-refusal a member never hits in ordinary work).  SPD-046 later narrowed the non-alias case: only
    inert config (color.ui, core.pager=cat, and the rest of GitProgramTest's allowlist) stays silent; user.name, commit.gpgsign
    and other non-allowlisted keys now refuse as a git-program finding.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **;
    Spud (no agent_id) is not bound by Law 7 and sees no change."""

    EVIDENCE = "git -c alias.p=push p"

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7", cwd=None):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id, cwd)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None, cwd=cwd)  # Law 7 does not bind Spud
        return r

    def test_the_tickets_evidence_command(self):
        r = self.refused_for_members(self.EVIDENCE)
        self.assertIn("alias", r.reason)
        m = load_spud_module()
        self.assertEqual(m.analyse_command(self.EVIDENCE, m.ShellAnalysis(cwd=str(self.home.path))).findings,
                         [("git-config", "-c alias.p=push")])

    def test_c_option_alias_and_include_forms(self):
        for cmd in ("git -c alias.p=push p", "git -c alias.co=checkout co", "git -c include.path=/tmp/x v",
                    "git -c includeIf.gitdir:/x/.path=/tmp/y v", "git -C . -c alias.p=push p", "git --no-pager -c alias.p=push p",
                    "git -c ALIAS.p=push p", "git -c Include.Path=/tmp/x v", "git -c alias.p p"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_the_config_env_forms(self):
        for cmd in ("git --config-env alias.p=E p", "git --config-env=alias.p=E p", "git --config-env include.path=E v",
                    "git --config-env=includeIf.gitdir:/x/.path=E v"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_the_alias_value_is_not_inspected(self):
        # A value naming a write, another alias, a `!` shell command, or an alias to a read: refused on the definition alone.
        for cmd in ("git -c alias.p='push --force' p", "git -c alias.a=co p", "git -c alias.x='!echo hi' x",
                    "git -c alias.x='!git push' x", "git -c alias.st=status st", "git -c alias.l=log l"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_env_config_variable_forms(self):
        for cmd in ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=push git p",
                    "GIT_CONFIG_PARAMETERS=\"'alias.p=push'\" git p", "GIT_CONFIG=/tmp/x git v",
                    "GIT_CONFIG_GLOBAL=/tmp/x git v", "GIT_CONFIG_SYSTEM=/tmp/x git v",
                    "export GIT_CONFIG_GLOBAL=/tmp/x; git v", "export GIT_CONFIG_COUNT=1; git p",
                    "env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=push git p",
                    "env GIT_CONFIG_GLOBAL=/tmp/x git v", "/usr/bin/env GIT_CONFIG=/tmp/x git v"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_config_forms_inside_shell_strings_eval_and_subshells(self):
        for cmd in ("sh -c 'git -c alias.p=push p'", "bash -c \"git -c alias.p=push p\"", "zsh -c 'git -c alias.p=push p'",
                    "eval 'git -c alias.p=push p'", "(git -c alias.p=push p)", "echo $(git -c alias.p=push p)",
                    "env sh -c 'git -c alias.p=push p'", "sh -c 'GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=push git p'",
                    "true && git -c alias.p=push p"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_a_defined_alias_beside_a_write_verb_still_refuses(self):
        # The git-config finding dominates; the command is refused whatever the verb the hook reads.
        for cmd in ("git -c alias.p=push commit -m x", "git -c alias.x='!echo hi' status"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_non_alias_c_config_stays_silent(self):
        # SPD-046 narrowed this: only inert, non-program config stays silent (the full allowlist is exercised by GitProgramTest);
        # user.name, commit.gpgsign and http.sslVerify now refuse as non-allowlisted keys.  The alias split of SPD-044 still holds.
        for ok in ("git -c color.ui=never status", "git -c core.pager=cat diff", "git -c core.quotepath=false log",
                   "git -C . -c color.ui=never log"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        # Inert non-alias config does not mask a real write verb: the verb is still read and refused as a git verb.
        r = self.assertRefused("git -c color.ui=never commit -m y", "Law 7")
        self.assertIn("git commit", r.reason)

    def test_a_bare_env_var_that_only_looks_like_git_config_stays_silent(self):
        # GIT_CONFIG_NOSYSTEM disables system config, it does not inject any; an unrelated variable is not git config; and a pager
        # set to `cat` is inert (SPD-046).  GIT_EDITOR and other program-naming variables now refuse -- see GitProgramTest.
        for ok in ("GIT_CONFIG_NOSYSTEM=1 git status", "GIT_PAGER=cat git log", "FOO=1 git status", "env FOO=1 git status"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_globs_in_config_still_refuse(self):
        # SPD-041's glob readings still apply: a glob in the verb or the value is read, and an alias glob refuses.
        for cmd in ("git -c k=v c?mmit -m x", "git -c alias.p=p?sh p", "git -c alias.{p,q}=push p"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_reads_and_writes_without_config_are_unchanged(self):
        for ok in ("git status", "git log --oneline -5", "git diff", "git -C . status", "git show HEAD"):
            with self.subTest(ok):
                self.assertSilent(ok)
        # `git -C /tmp status` was silent here until SPD-047: /tmp lies outside every checkout the ledger knows, and git reads
        # the config of whatever repository it finds there (see GitAliasFileTest).
        self.assertIn("outside", self.assertRefused("git -C /tmp status", "Law 7").reason)
        for verb in ("push", "commit -m x", "merge x"):
            with self.subTest(verb):
                self.assertRefused("git " + verb, "Law 7")


class GitProgramTest(BashHookCase):
    """SPD-046: git config and the environment can name a program git runs -- a pager, editor, ssh or proxy command, diff or
    merge driver, hooks or exec path, credential or askpass helper -- and some verb options name one too, any of which git runs
    under a verb Law 7's table allows.  git_verb reads only the verb, so `git -c core.pager=cmd log`, `GIT_SSH_COMMAND=cmd git
    fetch` and `git ls-remote --upload-pack=cmd host:r` ran the program while the hook saw an allowed read.  Probed (git 2.50.1,
    scratch throwaway repo, markers only in the scratchpad, never a real repo or remote): core.sshCommand, diff.external,
    core.pager, GIT_SSH_COMMAND, GIT_EXTERNAL_DIFF and GIT_PAGER each ran the named program, as did ls-remote/fetch
    --upload-pack, grep -O/--open-files-in-pager, difftool -x/--extcmd and archive --exec; `-c core.pager=cat` ran cat and stayed
    inert.  git help --config lists ~950 keys whose program-naming members are scattered across many sections, so the fix
    allowlists inert keys (color/advice/i18n/column sections; core.quotepath, core.abbrev, log.date, safe.directory; core.pager
    and pager.<cmd> only with an empty or `cat` value, only in the `-c` form the hook can read) and refuses everything else as a
    new git-program finding with its own reason, distinct from SPD-044's git-config alias reason.  It does not inspect the value,
    so it over-refuses an inert program (an editor set to `true`), which a member never needs.  Round 2 (probed by Spud, then me)
    also closes: abbreviated long options (git accepts any unambiguous prefix, `--upload` == --upload-pack); clustered short
    options (`-nO<cmd>`); the global `--exec-path=<dir>` (the command-line form of GIT_EXEC_PATH; bare `--exec-path` prints the
    path and stays silent); and GIT_ALLOW_PROTOCOL, which enables the ext:: transport whose URL is a command.  AGENT_A plans
    tests/** and bin/spud; AGENT_C plans **; Spud (no agent_id) is not bound by Law 7 and sees no change."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7", cwd=None):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id, cwd)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None, cwd=cwd)  # Law 7 does not bind Spud
        return r

    def finding(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path))).findings

    def test_program_config_keys_by_c_are_refused(self):
        for cmd in ("git -c core.pager=less log", "git -c core.editor=vi log", "git -c sequence.editor=vi status",
                    "git -c core.sshCommand=cmd fetch", "git -c core.hooksPath=/tmp/h status", "git -c core.fsmonitor=cmd status",
                    "git -c core.alternateRefsCommand=cmd log", "git -c core.gitProxy=cmd fetch", "git -c core.askPass=cmd fetch",
                    "git -c diff.external=cmd diff", "git -c diff.mine.command=cmd diff", "git -c diff.mine.textconv=cmd diff",
                    "git -c filter.f.clean=cmd status", "git -c filter.f.smudge=cmd status", "git -c credential.helper=cmd fetch",
                    "git -c gpg.program=cmd log", "git -c gpg.ssh.defaultKeyCommand=cmd log", "git -c log.showSignature=true log",
                    "git -c uploadpack.packObjectsHook=cmd fetch", "git -c protocol.ext.allow=always fetch",
                    "git -c url.x.insteadOf=y fetch", "git -c difftool.t.cmd=cmd status", "git -c mergetool.t.cmd=cmd status",
                    "git -c pager.log=less log", "git -c core.pager=less commit -m x"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_program_config_keys_by_config_env_are_refused(self):
        # --config-env reads the value from an environment variable the hook cannot see, so even core.pager is never inert here.
        for cmd in ("git --config-env core.pager=E log", "git --config-env=core.pager=E log",
                    "git --config-env core.sshCommand=E fetch", "git --config-env=core.editor=E log"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_the_finding_names_the_key_var_or_option(self):
        self.assertEqual(self.finding("git -c core.pager=less log"), [("git-program", "-c core.pager=less")])
        self.assertEqual(self.finding("GIT_SSH_COMMAND=cmd git fetch"), [("git-program", "GIT_SSH_COMMAND")])
        self.assertEqual(self.finding("git ls-remote --upload-pack=cmd host:r"),
                         [("git-program", "ls-remote --upload-pack=cmd")])
        r = self.refused_for_members("git -c core.sshCommand=cmd fetch")
        self.assertIn("core.sshCommand", r.reason)
        self.assertNotIn("alias", r.reason)  # its own reason, not SPD-044's git-config alias reason

    def test_program_env_vars_are_refused(self):
        for cmd in ("GIT_EDITOR=vi git log", "GIT_SEQUENCE_EDITOR=vi git log", "EDITOR=vi git log", "VISUAL=vi git log",
                    "GIT_SSH=cmd git fetch", "GIT_SSH_COMMAND=cmd git fetch", "GIT_EXTERNAL_DIFF=cmd git diff",
                    "GIT_ASKPASS=cmd git fetch", "SSH_ASKPASS=cmd git fetch", "GIT_PROXY_COMMAND=cmd git fetch",
                    "GIT_EXEC_PATH=/x git status", "GIT_TEMPLATE_DIR=/x git status", "GIT_PAGER=less git log",
                    "PAGER=less git log"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_program_env_vars_via_export_and_env_wrapper(self):
        for cmd in ("export GIT_SSH_COMMAND=cmd; git fetch", "env GIT_EDITOR=vi git log",
                    "/usr/bin/env GIT_PAGER=less git log", "env GIT_SSH_COMMAND=cmd sh -c 'git fetch'"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_verb_options_that_name_a_program_are_refused(self):
        for cmd in ("git ls-remote --upload-pack=cmd host:r", "git ls-remote --upload-pack cmd host:r",
                    "git fetch --upload-pack=cmd r", "git fetch --upload-pack cmd r", "git grep -Ocat foo", "git grep -O foo",
                    "git grep --open-files-in-pager=cat foo", "git grep --open-files-in-pager foo", "git difftool -x cmd A B",
                    "git difftool --extcmd=cmd A B", "git archive --remote=r --exec=cmd HEAD"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_abbreviated_long_options_are_refused(self):
        # git's parse-options accepts any unambiguous prefix (`git ls-remote --upload=cmd .` runs cmd, probed); refuse every prefix.
        for cmd in ("git ls-remote --upload=cmd .", "git ls-remote --up=cmd .", "git ls-remote --upload-pac cmd .",
                    "git fetch --upload=cmd r", "git grep --open=cat foo", "git grep --open-files=cat foo",
                    "git difftool --ext=cmd A B", "git difftool --extc cmd A B", "git archive --exe=cmd HEAD",
                    "git archive --exec cmd HEAD"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_clustered_short_options_are_refused(self):
        # A short cluster carrying the program letter runs it (`git grep -nO<cmd> hi`, probed); refuse the letter anywhere in the
        # cluster, value attached or not.  A false refusal of a pattern value is acceptable -- the hook fails closed.
        for cmd in ("git grep -nOcat foo", "git grep -nO foo", "git grep -inOcat foo", "git difftool -dx cmd A B",
                    "git difftool -yx cmd A B"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_exec_path_global_option_is_refused(self):
        # `git --exec-path=<dir> ls-remote https://x` runs <dir>/git-remote-https (probed): the command-line form of GIT_EXEC_PATH.
        for cmd in ("git --exec-path=/dir ls-remote x", "git --exec=/dir fetch", "git --exec-p=/dir status",
                    "git --exec-path=/dir status"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        self.assertEqual(self.finding("git --exec-path=/dir status"), [("git-program", "--exec-path=/dir")])

    def test_git_allow_protocol_env_is_refused(self):
        # GIT_ALLOW_PROTOCOL enables the ext:: transport, whose URL is a command git runs.
        for cmd in ("GIT_ALLOW_PROTOCOL=ext git fetch x", "env GIT_ALLOW_PROTOCOL=ext git fetch x",
                    "export GIT_ALLOW_PROTOCOL=ext; git fetch x"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_program_forms_inside_shell_strings_eval_and_subshells(self):
        for cmd in ("sh -c 'git -c core.pager=less log'", "bash -c \"GIT_EDITOR=vi git log\"",
                    "zsh -c 'git ls-remote --upload-pack=cmd host:r'", "eval 'git -c core.sshCommand=cmd fetch'",
                    "(git -c core.editor=vi log)", "echo $(git fetch --upload-pack=cmd r)", "true && GIT_PAGER=less git log"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_a_program_form_beside_a_write_verb_still_refuses(self):
        for cmd in ("git -c core.pager=less commit -m x", "GIT_EDITOR=vi git commit", "git -c core.sshCommand=cmd push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_inert_config_keys_stay_silent(self):
        for ok in ("git -c color.ui=never status", "git -c color.diff.new=green log", "git -c advice.detachedHead=false status",
                   "git -c i18n.logOutputEncoding=utf-8 log", "git -c column.ui=auto status", "git -c core.quotepath=false status",
                   "git -c core.abbrev=12 log", "git -c log.date=iso log", "git -c safe.directory=/x status",
                   "git -c core.pager=cat diff", "git -c core.pager= diff", "git -c pager.log=cat log", "git -c pager.diff= diff",
                   "git -C . -c color.ui=never log"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_inert_pager_env_vars_stay_silent(self):
        for ok in ("GIT_PAGER=cat git log", "PAGER=cat git log", "GIT_PAGER= git log", "env GIT_PAGER=cat git log",
                   "export GIT_PAGER=cat; git log", "GIT_CONFIG_NOSYSTEM=1 git status", "FOO=1 git status", "env FOO=1 git status"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_allowed_verbs_without_a_program_option_stay_silent(self):
        for ok in ("git fetch", "git fetch origin main", "git ls-remote host:r", "git grep x", "git grep -n foo",
                   "git grep -i foo", "git grep -ni foo",
                   # `git difftool` runs the tool diff.tool names whether or not -x is on the line, so SPD-087 refuses
                   # the verb to a member whole; what is pinned here is that no option of it earns a git-program finding
                   "git archive HEAD", "git grep -- -Ofoo",  # after --, -Ofoo is a pattern, not grep -O
                   # options adjacent to a program option but not a prefix of it stay silent
                   "git ls-remote --tags .", "git fetch --unshallow r", "git fetch --update-head-ok r",
                   "git --exec-path", "git --exec-path status"):  # bare --exec-path prints the path, it does not set one
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        for refused in ("git difftool A B", "git difftool --no-prompt A B", "git difftool -d A B"):
            with self.subTest(refused):
                self.assertRefused(refused, "Law 7")  # SPD-087: the verb, with no program option to name
                self.assertSilent(refused, agent_id=None)


class GitAliasFileTest(BashHookCase):
    """SPD-047: git also loads aliases -- and every program-naming key of SPD-046's class -- from config files the hook never
    opens, so a member who plants `[alias] p = push` in one runs a push under a verb Law 7's table does not list.  Probed
    (git 2.54.0, Apple Git-157, a hand-built .git in the scratchpad, read-only `version` as the alias value, nothing committed):
    `HOME=<dir> git v`, `XDG_CONFIG_HOME=<dir> git vv`, `git -C <repo> w`, `git --git-dir=<repo>/.git w` (spaced and `=`) and
    `GIT_DIR=<repo>/.git git w` each expanded an alias defined in the file that setting points git at.  git ignores an alias
    that hides one of its own commands (`alias.log = !echo SHADOWED` and `alias.status` were ignored; git-config(1): "aliases
    that hide existing Git commands are ignored"), so an alias can only introduce a verb git does not have, and a verb outside
    git's own command list is the tell for every alias source at once, on disk or not.  `git --list-cmds=main` is that set (174
    names here, a superset of --list-cmds=builtins' 147); it holds neither an alias in force nor an external `git-<verb>` on
    PATH (`--list-cmds=others`, which the hook cannot read either: `PATH=<dir>:$PATH git foo` ran <dir>/git-foo).

    Three refusals, for a member only (Spud's session is unaffected): an unknown verb; HOME or XDG_CONFIG_HOME in force on the
    line, refused like GIT_CONFIG_GLOBAL, since the file they name can also set core.pager and the rest of SPD-046's class under
    a real verb; and a repository the hook cannot read, named by -C, --git-dir, --work-tree, GIT_DIR, GIT_WORK_TREE or
    GIT_COMMON_DIR, when it lies outside every checkout the ledger knows.  git rejects `-C<dir>`, `-C=<dir>` and abbreviations
    of the global options (`--git-di=`, probed), so only the spelled forms parse; `-C` is repeatable and relative to the
    previous one, and it moves what a relative --git-dir means whichever order they appear in (probed, and git(1) documents the
    equivalence).  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        # A repository outside every checkout the ledger knows, built by hand (git init is a write verb): git reads its
        # config, and the hook cannot.
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        self.repo = self.out / "repo"
        (self.repo / ".git" / "objects").mkdir(parents=True)
        (self.repo / ".git" / "refs" / "heads").mkdir(parents=True)
        (self.repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (self.repo / ".git" / "config").write_text("[core]\n\trepositoryformatversion = 0\n[alias]\n\tp = push\n", encoding="utf-8")

    def refused_for_members(self, command, needle="Law 7", cwd=None):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id, cwd)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None, cwd=cwd)  # Law 7 does not bind Spud
        return r

    def finding(self, command, cwd=None):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=cwd or str(self.home.path))).findings

    # -- 1. an unknown verb -------------------------------------------------------
    def test_the_tickets_evidence_commands(self):
        # Each of these ran an alias defined in a file the hook cannot read; the verb is the tell.
        for cmd in ("git p", "git -C %s p" % self.repo, "git --git-dir=%s/.git p" % self.repo,
                    "GIT_DIR=%s/.git git p" % self.repo):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("git p", r.reason)
        self.assertEqual(self.finding("git p"), [("git-verb", ("verb", "p"))])
        # `HOME=<dir> git p` is refused too, but by the config-file finding, which comes first and names the variable: the
        # unknown verb is the hook's last resort, so a refusal the words as spelled already earn keeps its own reason.
        r = self.refused_for_members("HOME=%s git p" % self.out)
        self.assertIn("HOME", r.reason)

    def test_an_unknown_verb_is_refused_and_named(self):
        for verb in ("p", "foo", "v", "w", "st", "co", "lg", "ci", "amend", "pushf", "git-foo", "Status", "STATUS"):
            with self.subTest(verb):
                r = self.refused_for_members("git " + verb)
                self.assertIn("git " + verb, r.reason)
                self.assertIn("alias", r.reason)
        # An external `git-<verb>` on PATH is the same hole and the same tell.
        r = self.refused_for_members("git foo")
        self.assertIn("git-foo", r.reason)

    def test_an_unknown_verb_behind_global_options_and_in_shell_constructs(self):
        for cmd in ("git --no-pager p", "git -c color.ui=never p", "git -C . p", "sh -c 'git p'", "bash -c \"git p\"",
                    "eval 'git p'", "(git p)", "echo $(git p)", "true && git p", "env git p", "command git p",
                    "/usr/bin/git p", "git --literal-pathspecs p"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_gits_own_read_verbs_stay_silent(self):
        for ok in ("git status", "git log --oneline -5", "git diff", "git show HEAD", "git blame f", "git rev-parse HEAD",
                   "git ls-files", "git grep x", "git stash list", "git fetch", "git remote -v", "git worktree list",
                   "git worktree list --porcelain", "git for-each-ref", "git for-each-ref --format=%(refname)",
                   "git rev-list --count HEAD", "git ls-remote host:r", "git count-objects -v", "git describe --tags",
                   "git shortlog -sn", "git show-ref", "git cat-file -p HEAD", "git check-ignore x", "git merge-base a b",
                   "git name-rev HEAD", "git whatchanged", "git range-diff a b c", "git diff-tree HEAD", "git var GIT_AUTHOR_IDENT",
                   "git verify-tag v1", "git help status", "git version", "git --version", "git --help", "git"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_write_verbs_keep_their_spd_044_era_reason(self):
        for verb in ("commit -m x", "add .", "push", "merge x", "rebase main", "worktree add ../x", "branch -D x",
                     "stash", "config user.name x"):
            with self.subTest(verb):
                r = self.assertRefused("git " + verb, "Law 7")
                self.assertIn("git " + verb.split()[0], r.reason)
                self.assertIn("Spud commits", r.reason)
                self.assertNotIn("not one of git's own", r.reason)

    def test_the_command_list_is_read_from_git_and_fails_closed(self):
        # The hook reads `git --list-cmds=main` with its own sanitised environment; when git cannot be run at all it refuses
        # every verb it cannot check, saying so.  (PATH without git: the CLI itself runs by absolute path.)
        m = load_spud_module()
        self.assertIn("status", m.git_own_commands())
        self.assertNotIn("p", m.git_own_commands())
        path = self.home.env.get("PATH")
        self.home.env["PATH"] = str(self.out / "no-git-here")
        try:
            r = self.assertRefused("git status", "Law 7")
            self.assertIn("--list-cmds=main", r.reason)
            self.assertSilent("git status", agent_id=None)  # Law 7 does not bind Spud
            r = self.assertRefused("git p", "Law 7")
            self.assertIn("--list-cmds=main", r.reason)
            self.assertRefused("git push", "spudagents never run")  # a real write verb keeps its own reason
        finally:
            if path is None:
                self.home.env.pop("PATH", None)
            else:
                self.home.env["PATH"] = path
        self.assertSilent("git status")

    # -- 2. HOME and XDG_CONFIG_HOME ---------------------------------------------
    def test_home_and_xdg_config_home_in_force_are_refused(self):
        for cmd in ("HOME=/tmp/x git status", "XDG_CONFIG_HOME=/tmp/x git status", "HOME=%s git log" % self.out,
                    "export HOME=/tmp/x; git status", "export XDG_CONFIG_HOME=/tmp/x; git diff",
                    "env HOME=/tmp/x git status", "env XDG_CONFIG_HOME=/tmp/x git status",
                    "/usr/bin/env HOME=/tmp/x git status", "HOME=/tmp/x XDG_CONFIG_HOME=/tmp/y git status",
                    "sh -c 'HOME=/tmp/x git status'", "HOME=/tmp/x git commit -m y"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("HOME", r.reason)
        self.assertEqual(self.finding("HOME=/tmp/x git status"), [("git-config", "HOME")])
        self.assertEqual(self.finding("XDG_CONFIG_HOME=/tmp/x git status"), [("git-config", "XDG_CONFIG_HOME")])
        r = self.refused_for_members("HOME=/tmp/x git status")
        self.assertIn("alias", r.reason)  # the git-config reason of SPD-044, now naming the home variables too

    def test_a_home_expansion_inside_a_path_stays_silent(self):
        # Only an assignment puts HOME in force; `$HOME` used in a path is an ordinary word.
        for ok in ("git log -1 $HOME", "git log -- $HOME/x", "echo $HOME", "cat $HOME/.gitconfig", "ls $XDG_CONFIG_HOME",
                   "git diff HEAD -- $HOME"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        for ok in ("SPUD_HOME=/tmp/x git status", "HOMEBREW_PREFIX=/tmp/x git status", "MY_HOME=/tmp/x git status",
                   "XDG_DATA_HOME=/tmp/x git status", "HOME_DIR=/tmp/x git status"):
            with self.subTest(ok):
                self.assertSilent(ok)

    # -- 3. another repository ----------------------------------------------------
    def test_a_repository_outside_every_known_checkout_is_refused(self):
        for cmd in ("git -C %s status" % self.repo, "git -C /tmp status", "git -C /tmp log --oneline",
                    "git --git-dir=%s/.git status" % self.repo, "git --git-dir %s/.git status" % self.repo,
                    "git --work-tree=%s status" % self.repo, "git --work-tree %s status" % self.repo,
                    "GIT_DIR=%s/.git git status" % self.repo, "GIT_WORK_TREE=%s git status" % self.repo,
                    "GIT_COMMON_DIR=%s/.git git status" % self.repo,
                    "env GIT_DIR=%s/.git git status" % self.repo, "export GIT_DIR=/tmp/x/.git; git status",
                    "git -C /tmp -c color.ui=never status", "sh -c 'git -C /tmp status'", "(git -C /tmp status)",
                    "cd /tmp && git -C . status", "git -C ../.. status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("outside", r.reason)

    def test_the_repository_finding_names_the_directory_and_the_chain(self):
        self.assertEqual(self.finding("git -C /tmp status"),
                         [("git", ("status", None)), ("git-repo", ("-C /tmp", "/tmp", frozenset({str(self.home.path)})))])
        # -C is repeatable and each is relative to the previous, so only the composed directory is the one git reads in.
        r = self.refused_for_members("git -C /tmp -C sub status")
        self.assertIn("/tmp/sub", r.reason)
        self.assertNotIn("outside", self.assertRefused("git -C /tmp commit -m x", "Law 7").reason)  # the verb refuses first

    def test_a_target_inside_the_session_checkout_stays_silent(self):
        home = self.home.path
        (home / ".claude" / "worktrees" / "w" / "bin").mkdir(parents=True)
        for ok in ("git -C %s status" % home, "git -C . status", "git -C tests status", "git -C ./tests log",
                   "git -C %s/tests status" % home, "git --git-dir=%s/.git status" % home,
                   "git --git-dir %s/.git log" % home, "git --work-tree=%s status" % home,
                   "GIT_DIR=%s/.git git status" % home, "GIT_WORK_TREE=%s git status" % home,
                   "git -C %s -C tests status" % home, "cd %s/tests && git -C . status" % home,
                   "git -C %s --git-dir=.git status" % home, "git status", "git log",
                   # the common one: a worktree of the session's own checkout
                   "git -C %s/.claude/worktrees/w status" % home, "git -C %s/.claude/worktrees/w/bin log" % home):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_a_target_the_hook_cannot_resolve_is_refused(self):
        # A relative target from a directory the hook cannot follow, and a value it cannot read.
        for cmd in ("cd - && git -C sub status", "popd && git -C sub status", "cd ~x && git -C sub status",
                    "git -C '~x' status", "GIT_DIR=$D git status", "GIT_DIR=`echo /tmp` git status"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "cannot")
        # A variable in an option git reads by name was refused as an unresolvable word (SPD-043) and now names the
        # repository instead: the option is read first, and its reason says what to do about the path.
        for cmd in ("git -C $D status", "git --git-dir=$D status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd, "cannot resolve")
                self.assertIn("points git at a repository", r.reason)

    def test_spud_is_unaffected_by_all_of_it(self):
        for ok in ("git p", "git foo", "git -C /tmp status", "git -C %s p" % self.repo, "HOME=/tmp/x git status",
                   "XDG_CONFIG_HOME=/tmp/x git log", "GIT_DIR=/tmp/x/.git git status", "git --work-tree=/tmp status",
                   "cd - && git -C sub status"):
            with self.subTest(ok):
                self.assertSilent(ok, agent_id=None)


class GitConfigFileTest(BashHookCase):
    """SPD-063 (a): SPD-064 closes a member's writes to ~/.gitconfig and $XDG_CONFIG_HOME/git/config, but the local scope
    stays open -- a `.git/config` (or `.git/config.worktree`, or a file an `include.path` there names) inside a checkout the
    ledger knows, which a member can craft under its own deliverable globs and git then reads with nothing on the line.  So
    the edit hook, and with it the Bash hook's redirection and tee check, refuses a caller with an agent_id any file named
    `.gitconfig` or whose path ends in `.git/config`, `.git/config.worktree` or `git/config`, anywhere, deliverables
    included.  And `git config edit` -- the 2.46 subcommand syntax, one positional -- slipped through git_refused, which
    counted positionals; the writing subcommands are named now, and `git config get <key>` and `--get-urlmatch <name> <url>`,
    two reads git_refused counted as a key and a value, are silent.  Probed on git 2.54.0 (Apple Git-157)."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.tests = self.home.path / "tests"
        self.tests.mkdir(exist_ok=True)

    def config_files(self):
        t = self.tests
        return [t / ".git" / "config", t / "fake" / ".git" / "config", t / "fake" / ".git" / "config.worktree",
                t / ".gitconfig", t / "sub" / ".gitconfig", t / "git" / "config", t / "xdg" / "git" / "config"]

    def test_a_member_may_not_write_a_git_config_file_even_inside_its_globs(self):
        for p in self.config_files():
            with self.subTest(str(p)):
                for agent_id in (AGENT_A, AGENT_C, AGENT_D):
                    r = self.assertRefused("echo x > %s" % p, GIT_FILE_WORDING, agent_id=agent_id)
                    self.assertIn("Law 7", r.reason)
                    self.assertIn(str(p), r.reason)
                r = self.home.hook("PreToolUse", self.pre_edit(p, agent_id=AGENT_A))
                self.assertEqual((r.code, r.decision), (0, "deny"), (str(p), r))
                self.assertIn(GIT_FILE_WORDING, r.reason)
        self.assertEqual(self.wide["deliverables"], ["home:**"])

    def test_every_edit_tool_and_every_redirection_form(self):
        p = self.tests / ".git" / "config"
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            with self.subTest(tool):
                r = self.home.hook("PreToolUse", self.pre_edit(p, agent_id=AGENT_A, tool=tool))
                self.assertIn(GIT_FILE_WORDING, r.reason)
        commands = [form % p for form in ("echo x > %s", "echo x >> %s", "printf x | tee %s", "printf x | tee -a %s")]
        commands.append("cd %s/tests && echo x > .git/config" % self.home.path)
        for command in commands:
            with self.subTest(command):
                self.assertRefused(command, GIT_FILE_WORDING)

    def test_case_variants_are_refused_too(self):
        for p in (self.tests / ".GITCONFIG", self.tests / ".Git" / "Config", self.tests / "GIT" / "CONFIG"):
            with self.subTest(str(p)):
                self.assertRefused("echo x > %s" % p, GIT_FILE_WORDING)

    def test_similar_names_stay_under_the_globs(self):
        # .git/hooks/x stood here until SPD-066, which refuses every path with a .git component (GitDirectoryPathTest)
        for p in (self.tests / "config", self.tests / "gitconfig", self.tests / "x.gitconfig", self.tests / ".gitconfig.bak",
                  self.tests / ".github" / "config", self.tests / "git" / "config.json", self.tests / "notgit" / "config"):
            with self.subTest(str(p)):
                self.assertSilent("echo x > %s" % p)
                r = self.home.hook("PreToolUse", self.pre_edit(p, agent_id=AGENT_A))
                self.assertEqual((r.code, r.stdout), (0, ""), (str(p), r))

    def test_spud_is_not_bound_by_it(self):
        for p in self.config_files():
            with self.subTest(str(p)):
                r = self.home.hook("PreToolUse", self.pre_edit(p, agent_id=None))
                self.assertNotIn(GIT_FILE_WORDING, r.reason)  # Law 1 may refuse it in the home; never this reason
                self.assertSilent("echo x > /private/tmp/claude-%d/x/.gitconfig" % os.getuid(), agent_id=None)

    # -- `git config` itself ------------------------------------------------------
    def test_the_writing_forms_of_git_config_are_refused(self):
        for cmd in ("git config user.name x", "git config --add a.b c", "git config --append a.b c",
                    "git config --unset a.b", "git config --unset-all a.b", "git config --replace-all a.b c",
                    "git config --rename-section a b", "git config --remove-section a", "git config --edit",
                    "git config -e", "git config --global --edit", "git config --local core.pager less",
                    "git config set core.pager x", "git config unset core.pager", "git config edit",
                    "git config rename-section a b", "git config remove-section a", "git config --file .claude/f core.pager x",
                    "git config set --all core.pager x"):
            with self.subTest(cmd):
                for agent_id in (AGENT_A, AGENT_C):
                    r = self.assertRefused(cmd, "Law 7", agent_id=agent_id)
                    self.assertIn("git config", r.reason)
                self.assertSilent(cmd, agent_id=None)
        # SPD-227: the file a write form's --file names is one git writes, held to Law 1 for Spud (.claude/ is his own)
        self.assertRefused("git config --file f core.pager x", "Law 1", agent_id=None)

    def test_the_reading_forms_of_git_config_stay_silent(self):
        for cmd in ("git config --get core.pager", "git config --get-all a.b", "git config --get-regexp a",
                    "git config --list", "git config -l", "git config core.pager", "git config list",
                    "git config get core.pager", "git config --list --show-scope --name-only",
                    "git config --get-colorbool color.ui", "git config --get-urlmatch a https://x",
                    "git config -f f core.pager", "git config --global --list", "git config get --all a.b"):
            with self.subTest(cmd):
                self.assertSilent(cmd)
                self.assertSilent(cmd, agent_id=None)


class GitDirectoryPathTest(BashHookCase):
    """SPD-066 (1): SPD-063 closed the config files git reads by itself, and the rest of a git directory stayed a member's to
    write under its globs: .git/hooks/* (git runs a hook with nothing on the line and no config key naming it -- probed on
    git 2.54.0, every `git status` runs post-index-change and every `git fetch` runs reference-transaction),
    .git/info/attributes and info/exclude, .git/shallow, the index, and a worktree's .git gitfile, which points git at any
    git directory.  So the path rule refuses a caller with an agent_id any path with a `.git` component, by any reading of
    it, anywhere, its own deliverables included, matched case-folded like git_config_file; a .git/config keeps SPD-063's
    more specific reason.  The Edit tools and every Bash write target that goes through edit_reason (a redirection, tee,
    SPD-049's file-writing git options) refuse it alike.  .gitignore, .github/, .gitattributes and the other names that
    merely begin with .git stay under the globs, and Spud's answers do not change.  AGENT_A plans home:tests/** and
    home:bin/spud; AGENT_C plans home:**."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.tests = self.home.path / "tests"
        self.fake = self.tests / "fake"
        (self.fake / ".git" / "hooks").mkdir(parents=True)
        (self.fake / ".git" / "info").mkdir()

    def git_dir_paths(self):
        g = self.fake / ".git"
        return [g / "hooks" / "pre-auto-gc", g / "hooks" / "post-index-change", g / "hooks" / "reference-transaction",
                g / "info" / "attributes", g / "info" / "exclude", g / "index", g / "shallow", g / "HEAD",
                g / "objects" / "17" / "0123abcd", g / "refs" / "heads" / "main", g / "modules" / "m" / "hooks" / "pre-auto-gc",
                g, self.tests / "sub" / ".git", self.tests / ".git"]

    def assertEditRefused(self, path, needle, agent_id=AGENT_A, tool="Write"):
        r = self.home.hook("PreToolUse", self.pre_edit(path, agent_id=agent_id, tool=tool))
        self.assertEqual((r.code, r.decision), (0, "deny"), (str(path), agent_id, tool, r))
        self.assertIn(needle, r.reason, (str(path), agent_id, tool))
        return r

    def assertEditSilent(self, path, agent_id=AGENT_A):
        r = self.home.hook("PreToolUse", self.pre_edit(path, agent_id=agent_id))
        self.assertEqual((r.code, r.stdout), (0, ""), (str(path), agent_id, r))

    def test_a_member_may_not_write_under_a_git_directory_even_inside_its_globs(self):
        for p in self.git_dir_paths():
            with self.subTest(str(p)):
                for agent_id in (AGENT_A, AGENT_C):
                    r = self.assertEditRefused(p, GIT_DIR_WORDING, agent_id)
                    self.assertIn("Law 7", r.reason)
                    self.assertIn(str(p), r.reason)  # the reason names the path
                    r = self.assertRefused("echo x > %s" % p, GIT_DIR_WORDING, agent_id=agent_id)
                    self.assertIn(str(p), r.reason)
        self.assertEqual(self.wide["deliverables"], ["home:**"])  # not even a ** member

    def test_the_reason_says_why(self):
        r = self.assertEditRefused(self.fake / ".git" / "hooks" / "pre-auto-gc", GIT_DIR_WORDING)
        for word in ("hook", "attributes", "index", "gitfile", "nothing on the line", "Spud commits"):
            self.assertIn(word, r.reason)

    def test_every_edit_tool_and_every_bash_write_target(self):
        p = self.fake / ".git" / "hooks" / "pre-auto-gc"
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            with self.subTest(tool):
                self.assertEditRefused(p, GIT_DIR_WORDING, tool=tool)
        commands = [form % p for form in ("echo x > %s", "echo x >> %s", "printf x | tee %s", "printf x | tee -a %s",
                                          "printf x | tee /dev/null %s", "echo x 2> %s", "cat > %s <<< x")]
        commands += ["cd %s && echo x > .git/hooks/pre-auto-gc" % self.fake, "cd %s/.git && echo x > index" % self.fake,
                     "cd %s && printf x | tee .git/info/attributes" % self.fake,
                     "sh -c 'echo x > %s'" % p,
                     "git diff --output=%s" % p]  # SPD-049: a file a git option writes goes through the same rule
        for command in commands:
            with self.subTest(command):
                self.assertRefused(command, GIT_DIR_WORDING)
                self.assertRefused(command, GIT_DIR_WORDING, agent_id=AGENT_C)

    def test_case_variants_are_refused_too(self):
        for p in (self.fake / ".GIT" / "hooks" / "pre-auto-gc", self.fake / ".Git" / "index", self.tests / ".gIt",
                  self.tests / "sub" / ".GIT" / "info" / "attributes"):
            with self.subTest(str(p)):
                self.assertRefused("echo x > %s" % p, GIT_DIR_WORDING)
                self.assertEditRefused(p, GIT_DIR_WORDING)

    def test_a_symlink_reading_is_refused(self):
        """By any reading of the target: a link whose own path has no .git component but whose real path does."""
        (self.tests / "link").symlink_to(self.fake / ".git")
        (self.tests / "hooklink").symlink_to(self.fake / ".git" / "hooks" / "post-index-change")  # need not exist
        real = self.fake / ".git"
        for p, resolved in ((self.tests / "link" / "hooks" / "pre-auto-gc", real / "hooks" / "pre-auto-gc"),
                            (self.tests / "hooklink", real / "hooks" / "post-index-change")):
            with self.subTest(str(p)):
                r = self.assertEditRefused(p, GIT_DIR_WORDING)
                self.assertIn(str(resolved), r.reason)  # the reading that has the component
                r = self.assertRefused("echo x > %s" % p, GIT_DIR_WORDING)
                self.assertIn(str(resolved), r.reason)
        (self.tests / "plain").mkdir()
        (self.tests / "plainlink").symlink_to(self.tests / "plain")  # the control: a link to a directory of the tree
        self.assertEditSilent(self.tests / "plainlink" / "x.py")

    def test_similar_names_stay_under_the_globs(self):
        t = self.tests
        similar = [t / ".gitignore", t / ".github" / "workflows" / "ci.yml", t / ".gitattributes", t / ".gitmodules",
                   t / ".gitkeep", t / "x.git" / "hooks" / "pre-auto-gc", t / ".git.bak" / "hooks" / "pre-auto-gc",
                   t / "git" / "hooks" / "pre-auto-gc", t / "dotgit" / "index", t / "hooks" / "pre-auto-gc", t / ".gitx" / "HEAD"]
        for p in similar:
            with self.subTest(str(p)):
                for agent_id in (AGENT_A, AGENT_C):
                    self.assertEditSilent(p, agent_id)
                    self.assertSilent("echo x > %s" % p, agent_id=agent_id)
        # at the home's root they are outside AGENT_A's globs: its answer is the globs', never this rule's
        for p in (self.home.path / ".gitignore", self.home.path / ".github" / "x.yml", self.home.path / ".gitattributes"):
            with self.subTest(str(p)):
                r = self.assertEditRefused(p, "deliverables")
                self.assertNotIn(GIT_DIR_WORDING, r.reason)
                self.assertEditSilent(p, AGENT_C)

    def test_a_git_config_file_keeps_its_own_reason(self):
        """SPD-063's tails stay the more specific reason: a .git/config can also define an alias."""
        for p in (self.fake / ".git" / "config", self.fake / ".git" / "config.worktree", self.fake / ".GIT" / "Config"):
            with self.subTest(str(p)):
                r = self.assertEditRefused(p, GIT_FILE_WORDING)
                self.assertNotIn(GIT_DIR_WORDING, r.reason)
                r = self.assertRefused("echo x > %s" % p, GIT_FILE_WORDING)
                self.assertNotIn(GIT_DIR_WORDING, r.reason)

    def test_the_scratchpad_is_no_exception(self):
        scratch = "/private/tmp/claude-%d/-Users-Someone-Personal-Spud/%s/scratchpad" % (os.getuid(), SESSION)
        for p in (scratch + "/repo/.git/hooks/pre-auto-gc", scratch + "/repo/.git", "/tmp/x/.git/index"):
            with self.subTest(p):
                r = self.assertEditRefused(p, GIT_DIR_WORDING)
                self.assertIn(p, r.reason)
                self.assertRefused("echo x > %s" % p, GIT_DIR_WORDING)
                self.assertSilent("echo x > %s" % p, agent_id=None)  # Spud writes there freely, as before
        self.assertEditSilent(scratch + "/repo/notes.md")  # the control

    def test_spud_is_not_bound_by_it(self):
        for p in self.git_dir_paths():
            with self.subTest(str(p)):
                r = self.home.hook("PreToolUse", self.pre_edit(p, agent_id=None))
                self.assertNotIn(GIT_DIR_WORDING, r.reason)  # Law 1 may refuse it in the home; never this reason
                self.assertNotIn(GIT_DIR_WORDING, self.bash("echo x > %s" % p, agent_id=None).reason)

    def test_an_unbound_agent_id_in_a_spud_session_is_held_too(self):
        """The rule binds where SPD-063's does: any agent_id in a Spud session, before its binding too."""
        r = self.assertEditRefused(self.fake / ".git" / "hooks" / "pre-auto-gc", GIT_DIR_WORDING, agent_id=AGENT_D)
        self.assertNotIn("not bound", r.reason)

    def test_a_worktrees_own_gitfile_is_refused(self):
        """The .git of a linked worktree is a gitfile, `gitdir: <path>`, and git follows it to whatever git directory it
        names; a member bound to that very worktree, whose glob is **, is refused it and keeps the rest of the tree."""
        scratch_git(self.home.path, "init", "-q", "-b", "main")
        scratch_git(self.home.path, "commit", "-q", "--allow-empty", "-m", "root")
        wt = self.home.path.parent / ("%s-gitfile" % self.home.path.name)
        self.addCleanup(shutil.rmtree, wt, True)
        scratch_git(self.home.path, "worktree", "add", "-q", "-b", "gitfile", wt)
        self.assertTrue((wt / ".git").is_file(), "a linked worktree's .git is a gitfile")
        self.home.json("member", "finish", self.other["ref"], "--status", "done", "--outcome", "x", actor="spud")  # a slot for D
        self.spawn(self.plan(persona="engineer", model="opus", deliverable=["**"], cwd=wt), AGENT_D)  # SPD-098: binds SPD-001 to wt
        r = self.assertEditRefused(wt / ".git", GIT_DIR_WORDING, agent_id=AGENT_D)
        self.assertIn(str(wt / ".git"), r.reason)
        for command in ("echo gitdir: /tmp/x > %s" % (wt / ".git"), "cd %s && printf x | tee .git" % wt):
            with self.subTest(command):
                self.assertRefused(command, GIT_DIR_WORDING, agent_id=AGENT_D)
        self.assertEditSilent(wt / "tests" / "x.py", agent_id=AGENT_D)  # the control: the rest of its tree
        self.assertEditSilent(wt / ".gitignore", agent_id=AGENT_D)
        self.assertEditRefused(self.home.path / ".git" / "hooks" / "pre-auto-gc", GIT_DIR_WORDING, agent_id=AGENT_C)
        r = self.home.hook("PreToolUse", self.pre_edit(wt / ".git", agent_id=None))
        self.assertIn("Law 1", r.reason)  # Spud's answer in a project checkout, as before
        self.assertNotIn(GIT_DIR_WORDING, r.reason)


class GitLocalConfigTest(BashHookCase):
    """SPD-063 (b): git reads the target repository's own config with nothing on the line, and a member can craft one under
    its deliverable globs inside a checkout the ledger knows (`git -C tests/fake status` resolves inside the home, so
    SPD-047's git-repo refusal, which only fires outside every known checkout, stays silent).  So before a member's git call
    the hook reads the keys in force at that repository's `local` and `worktree` scopes with `git config --list --show-scope
    --name-only`, run there with the module's sanitised environment, and refuses the ones that name or enable a program git
    runs (SPD-046's class) under a verb Law 7's table allows.  The system and global scopes are Eric's own and stay out of
    it: credential.helper is in force at the system scope on this Mac, and this very checkout's local scope holds
    core.filemode, extensions.worktreeconfig, remote.origin.url and branch.main.vscode-merge-base, so a check that refused
    every key outside SPD-046's inert allowlist would refuse every member git call in Spud's own repository.  The answer is
    kept in the home's state directory under the stat fingerprint of the config files git reads there, so only the first
    hook after one of them changes runs git (the hook path never imports subprocess otherwise, SPD-016).

    Since SPD-066 a repository nested in a checkout -- the tests/fake this class planted until then -- is refused before its
    keys are read (NestedRepositoryTest), so the repository here is the checkout's own: the home, which is project spud's
    root in the suite, made a repository by hand.  Its config is still a file no member may write (GitConfigFileTest); the
    check stands for a key Eric or a tool set there, or a member wrote through an interpreter.  Since SPD-123 it binds
    Spud's own git call too (assertRepoRefused's Spud line flipped from silent): the home is a checkout the ledger knows."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.repo = self.home.path  # the checkout's own repository: git reads it, the ledger knows it
        plant_git_dir(self.repo / ".git")
        (self.repo / "tests").mkdir()

    def plant(self, text, name="config"):
        (self.repo / ".git" / name).write_text(text, encoding="utf-8")

    def calls(self):
        return ("git -C %s status" % self.repo, "cd %s && git status" % self.repo, "git -C %s log --oneline" % self.repo,
                "git --git-dir=%s/.git status" % self.repo, "cd %s && git diff" % self.repo)

    def assertRepoRefused(self, needle):
        for cmd in self.calls():
            with self.subTest(cmd):
                for agent_id in (AGENT_A, AGENT_C):
                    r = self.assertRefused(cmd, "Law 7", agent_id=agent_id)
                    self.assertIn(needle, r.reason)
                # SPD-123: Law 7 does not bind Spud, but the home is a checkout the ledger knows, and git would run the
                # program the key names under his own call with nothing on the line
                r = self.assertRefused(cmd, SPUD_PLANTED_WORDING, agent_id=None)
                self.assertIn(needle, r.reason)
                self.assertNotIn("Law 7", r.reason)

    def assertRepoSilent(self):
        for cmd in self.calls():
            with self.subTest(cmd):
                self.assertSilent(cmd)
                self.assertSilent(cmd, agent_id=None)

    def test_a_planted_program_key_at_the_local_scope_refuses_every_member_git_call(self):
        for key, text in (("core.pager", "[core]\n\tpager = /bin/echo\n"),
                          ("diff.external", "[diff]\n\texternal = /bin/echo\n"),
                          ("core.sshCommand", "[core]\n\tsshCommand = /bin/echo\n"),
                          ("core.hooksPath", "[core]\n\thooksPath = /tmp/h\n"),
                          ("credential.helper", "[credential]\n\thelper = /bin/echo\n"),
                          ("difftool.t.cmd", '[difftool "t"]\n\tcmd = /bin/echo\n')):
            with self.subTest(key):
                self.plant("[core]\n\trepositoryformatversion = 0\n" + text)
                self.assertRepoRefused("local")
                r = self.assertRefused("git -C %s status" % self.repo, key.split(".")[-1].casefold())
                self.assertIn(GIT_SCOPE_WORDING, r.reason)

    def test_a_program_key_at_the_worktree_scope_is_named_as_such(self):
        self.plant("[core]\n\trepositoryformatversion = 0\n[extensions]\n\tworktreeConfig = true\n")
        self.plant("[core]\n\tpager = /bin/echo\n", name="config.worktree")
        self.assertRepoRefused("worktree")

    def test_a_program_key_reached_by_include_path_is_refused(self):
        """git reports an included file's keys at the including file's scope, so the listing already covers includes."""
        self.plant("[core]\n\trepositoryformatversion = 0\n[include]\n\tpath = extra\n")
        self.plant("[core]\n\tsshCommand = /bin/echo\n", name="extra")
        self.assertRepoRefused("local")

    def test_inert_local_keys_stay_silent(self):
        self.plant("[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n\tignorecase = true\n"
                   '[remote "origin"]\n\turl = https://example.invalid/x\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
                   '[branch "main"]\n\tremote = origin\n\tmerge = refs/heads/main\n\tvscode-merge-base = origin/main\n'
                   "[extensions]\n\tworktreeConfig = false\n[color]\n\tui = never\n")
        self.assertRepoSilent()

    def test_a_key_at_the_global_or_system_scope_is_ignored(self):
        """Eric's own scopes: credential.helper is in force at the system scope on this Mac, so a check over every scope
        would refuse every member git call."""
        planted = self.home.path / "tests" / "global.gitconfig"
        planted.write_text("[core]\n\tpager = /bin/echo\n\tsshCommand = /bin/echo\n", encoding="utf-8")
        self.home.env["GIT_CONFIG_GLOBAL"] = str(planted)
        self.addCleanup(self.home.env.pop, "GIT_CONFIG_GLOBAL", None)
        self.assertRepoSilent()

    def test_a_directory_the_hook_cannot_follow_is_refused(self):
        for cmd in ("cd - && git status", "popd && git status", "cd ~x && git log"):
            with self.subTest(cmd):
                r = self.assertRefused(cmd, "Law 7")
                self.assertIn("cannot", r.reason)
                self.assertSilent(cmd, agent_id=None)

    def test_a_directory_in_no_repository_at_all_needs_no_git_run(self):
        cache = self.home.path / STATE / "git-config-scopes.json"
        shutil.rmtree(self.repo / ".git")
        self.assertSilent("git status")  # the scratch home is not a repository now: no local scope, nothing to read
        self.assertFalse(cache.exists(), "a directory in no repository costs no git run and no cache entry")

    def test_the_answer_is_cached_and_a_config_edit_invalidates_it(self):
        cache = self.home.path / STATE / "git-config-scopes.json"
        self.assertSilent("git -C %s status" % self.repo)
        self.assertTrue(cache.exists(), "the first call writes the cache")
        stored = json.loads(cache.read_text(encoding="utf-8"))
        self.assertTrue(stored, stored)
        self.assertSilent("git -C %s status" % self.repo)  # a hit: the same answer
        self.plant("[core]\n\trepositoryformatversion = 0\n\tpager = /bin/echo\n")
        self.assertRefused("git -C %s status" % self.repo, "core.pager")  # the fingerprint changed
        self.plant("[core]\n\trepositoryformatversion = 0\n")
        self.assertSilent("git -C %s status" % self.repo)

    def test_a_cache_hit_imports_no_subprocess(self):
        """The Bash hook runs on every command line; SPD-016 keeps subprocess off its path, so only a miss may pay for it."""
        cold = self.imports_of("git -C %s status" % self.repo)
        self.assertIn("subprocess", cold)
        self.assertNotIn("subprocess", self.imports_of("git -C %s status" % self.repo))

    def imports_of(self, command):
        payload = self.pre_bash(command, agent_id=AGENT_A)
        proc = subprocess.run([sys.executable, "-I", "-S", "-X", "importtime", str(SPUD), "hook", "PreToolUse"],
                              input=json.dumps(payload), capture_output=True, text=True, env=self.home.env)
        self.assertEqual(proc.returncode, 0, proc)
        return {line.rsplit("|", 1)[-1].strip() for line in proc.stderr.splitlines() if line.startswith("import time:")}

    def test_a_repository_outside_every_known_checkout_keeps_its_own_reason(self):
        outside = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / ".git").mkdir()
        (outside / ".git" / "config").write_text("[core]\n\tpager = /bin/echo\n", encoding="utf-8")
        r = self.assertRefused("git -C %s status" % outside, "outside")
        self.assertNotIn(GIT_SCOPE_WORDING, r.reason)

    def test_a_write_verb_and_a_program_option_still_refuse_first(self):
        self.plant("[core]\n\trepositoryformatversion = 0\n\tpager = /bin/echo\n")
        self.assertNotIn(GIT_SCOPE_WORDING, self.assertRefused("git -C %s commit -m x" % self.repo, "never run").reason)
        self.assertNotIn(GIT_SCOPE_WORDING, self.assertRefused("git -C %s -c core.editor=vi log" % self.repo, "program git never checks").reason)


class NestedRepositoryTest(BashHookCase):
    """SPD-066 (2): a git directory need not be called .git, and the path rule cannot see every route to one.  A bare layout
    (HEAD, objects/, refs/, hooks/) written under a member's globs is a repository git discovers from inside it, a .git
    built by an interpreter, cp or mkdir is one too, and `git -C tests/fake <read verb>` resolves inside a known checkout,
    so SPD-047's refusal of a repository outside every known checkout never fires.  git runs such a repository's hooks with
    nothing on the line (probed on 2.54.0: `git status` runs post-index-change, `git fetch` reference-transaction) and reads
    its config, attributes and index.  So at each member git call the repository git would read must be a known checkout's
    own: discovered from the directory (-C, or the shell's), its work tree is the root of the registered project checkout or
    listed worktree, reached through that root's own .git; named by --git-dir, GIT_DIR or GIT_COMMON_DIR, it is that
    checkout's git or common directory.  --work-tree and GIT_WORK_TREE never choose the repository (probed: git still
    discovers from the directory it runs in), so with only those on the line the shell's directory is read too.  The home
    is a real repository here, project spud's root in the suite; AGENT_A plans home:tests/** and home:bin/spud, AGENT_C
    home:**.  Since SPD-123 Spud is refused a repository nested in a known checkout too (refused_for_members' Spud line
    flipped from silent); one outside every known checkout stays his own business."""

    VERBS = ("status", "log --oneline", "fetch")

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        scratch_git(home, "init", "-q", "-b", "main")
        scratch_git(home, "commit", "-q", "--allow-empty", "-m", "root")
        self.nested = home / "tests" / "fake"  # a .git planted below the checkout's root
        plant_git_dir(self.nested / ".git")
        (self.nested / "sub").mkdir()
        self.bare = home / "tests" / "bare"  # a bare layout, no .git anywhere: git discovers it from inside
        plant_git_dir(self.bare)
        (self.bare / "hooks").mkdir()

    def refused_for_members(self, command, repository, spud=True):
        for agent_id in (AGENT_A, AGENT_C):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, GIT_NESTED_WORDING, agent_id=agent_id)
                self.assertIn("Law 7", r.reason)
                self.assertIn(str(repository), r.reason)  # the reason names the repository
        with self.subTest(command=command, agent_id="spud"):
            if spud:  # SPD-123: it lies in a checkout the ledger knows, so git would run its hooks under Spud's own call
                r = self.assertRefused(command, GIT_NESTED_WORDING, agent_id=None)
                self.assertIn(SPUD_PLANTED_WORDING, r.reason)
                self.assertIn(str(repository), r.reason)
            else:  # Law 7 does not bind Spud, and a repository outside every known checkout is his own business
                self.assertSilent(command, agent_id=None)

    def silent_for_all(self, command):
        for agent_id in (AGENT_A, AGENT_C, None):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertSilent(command, agent_id=agent_id)

    def test_a_nested_git_directory_is_refused(self):
        n, home = self.nested, self.home.path
        for verb in self.VERBS:
            for cmd in ("git -C %s %s" % (n, verb), "git -C tests/fake %s" % verb, "cd %s && git %s" % (n, verb),
                        "cd tests/fake/sub && git %s" % verb, "git -C %s/sub %s" % (n, verb),
                        "git --git-dir=%s/.git %s" % (n, verb), "git --git-dir %s/.git %s" % (n, verb),
                        "GIT_DIR=%s/.git git %s" % (n, verb), "env GIT_DIR=%s/.git git %s" % (n, verb),
                        "export GIT_DIR=%s/.git; git %s" % (n, verb), "git -C tests/fake --git-dir=.git %s" % verb,
                        "sh -c 'cd %s && git %s'" % (n, verb), "(cd tests/fake && git %s)" % verb,
                        # the work tree never chooses the repository: git still reads the one it discovers where it runs
                        "cd %s && git --work-tree=%s %s" % (n, home, verb), "cd %s && GIT_WORK_TREE=%s git %s" % (n, home, verb)):
                self.refused_for_members(cmd, n)

    def test_a_bare_layout_is_refused(self):
        b = self.bare
        for verb in self.VERBS:
            for cmd in ("git -C %s %s" % (b, verb), "git -C tests/bare %s" % verb, "cd %s && git %s" % (b, verb),
                        "cd tests/bare/refs/heads && git %s" % verb, "git --git-dir=%s %s" % (b, verb),
                        "git --git-dir %s %s" % (b, verb), "GIT_DIR=%s git %s" % (b, verb),
                        "GIT_DIR=tests/bare git %s" % verb, "GIT_COMMON_DIR=%s git %s" % (b, verb)):
                self.refused_for_members(cmd, b)

    def test_a_gitfile_below_the_root_is_a_nested_repository_too(self):
        """A submodule's shape: a .git file naming a git directory elsewhere.  Here it names the checkout's own, so the git
        directory is known, but the work tree git would read with it is not the checkout's."""
        linked = self.home.path / "tests" / "linked"
        linked.mkdir()
        (linked / ".git").write_text("gitdir: %s\n" % (self.home.path / ".git"), encoding="utf-8")
        self.refused_for_members("git -C %s status" % linked, linked)
        self.refused_for_members("cd %s && git status" % linked, linked)

    def test_the_checkouts_own_repository_stays_silent(self):
        home = self.home.path
        for verb in self.VERBS:
            for cmd in ("git %s" % verb, "git -C %s %s" % (home, verb), "cd %s/tests && git %s" % (home, verb),
                        "git -C tests %s" % verb, "git -C bin %s" % verb, "git --git-dir=%s/.git %s" % (home, verb),
                        "GIT_DIR=%s/.git git %s" % (home, verb), "git -C %s --git-dir=.git %s" % (home, verb),
                        "git --work-tree=%s %s" % (home, verb), "GIT_WORK_TREE=%s git %s" % (home, verb),
                        "GIT_COMMON_DIR=%s/.git git %s" % (home, verb), "sh -c 'cd %s && git %s'" % (home, verb)):
                self.silent_for_all(cmd)

    def test_a_listed_worktree_is_a_checkouts_own_repository(self):
        home = self.home.path
        wt = home.parent / ("%s-nested" % home.name)
        self.addCleanup(shutil.rmtree, wt, True)
        scratch_git(home, "worktree", "add", "-q", "-b", "nested", wt)
        inside = home / ".claude" / "worktrees" / "spd-066-x"
        scratch_git(home, "worktree", "add", "-q", "-b", "worktree-spd-066-x", inside)
        for verb in self.VERBS:
            for cmd in ("git -C %s %s" % (wt, verb), "cd %s && git %s" % (inside, verb), "git -C %s/.claude %s" % (home, verb),
                        "GIT_DIR=%s/.git/worktrees/nested git %s" % (home, verb)):
                self.silent_for_all(cmd)
        # a directory that looks like a worktree of the home but that git does not list is no checkout of it, though the path
        # rule maps a .claude/worktrees/<name>/ of a root as one
        planted = home / ".claude" / "worktrees" / "planted"
        plant_git_dir(planted / ".git")
        self.refused_for_members("git -C %s status" % planted, planted)
        self.refused_for_members("cd %s && git fetch" % planted, planted)

    def test_a_bare_layout_at_a_checkout_root_is_no_repository_of_it(self):
        """The root that has no .git of its own (the home, in the real ledger) made a bare repository by writing HEAD,
        objects/ and refs/ at it, and a root with a .git named as the git directory itself (--git-dir=<root>)."""
        shutil.rmtree(self.home.path / ".git")
        plant_git_dir(self.home.path / "planted-root")  # built beside, then its parts moved to the root
        for part in ("objects", "refs", "HEAD", "config"):
            os.rename(self.home.path / "planted-root" / part, self.home.path / part)
        self.refused_for_members("git status", self.home.path)
        self.refused_for_members("git -C tests log", self.home.path)
        self.refused_for_members("git --git-dir=%s fetch" % self.home.path, self.home.path)

    def test_a_repository_outside_every_checkout_reached_by_cd_is_refused(self):
        """SPD-047 refuses one a line names; the directory the shell is in reaches one too."""
        outside = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        plant_git_dir(outside / ".git")
        self.refused_for_members("cd %s && git status" % outside, outside, spud=False)
        r = self.assertRefused("git -C %s status" % outside, "outside every checkout")  # SPD-047's own reason, first
        self.assertNotIn(GIT_NESTED_WORDING, r.reason)
        nowhere = Path(tempfile.mkdtemp(prefix="spud-no-repository-")).resolve()
        self.addCleanup(shutil.rmtree, nowhere, True)
        self.silent_for_all("cd %s && git status" % nowhere)  # no repository where it runs: git reads none, nothing to check

    def test_a_directory_the_hook_cannot_follow_with_only_a_work_tree_is_refused(self):
        """With only --work-tree or GIT_WORK_TREE on the line git discovers the repository where the shell is, which the
        hook cannot know after `cd -`: it fails closed, as SPD-063 does for a bare `git status` there."""
        for cmd in ("cd - && git --work-tree=%s status" % self.home.path, "popd && GIT_WORK_TREE=%s git log" % self.home.path):
            with self.subTest(cmd):
                r = self.assertRefused(cmd, "cannot")
                self.assertIn("Law 7", r.reason)
                self.assertSilent(cmd, agent_id=None)

    def test_a_refusal_reads_no_config_and_runs_no_git(self):
        """The nested repository is refused from what SPD-063 already computes (the one walk up to it): no git run, no cache
        entry for it, no subprocess import once the checkout's own caches are warm."""
        self.plant_program_key(self.nested / ".git" / "config")
        self.assertSilent("git status")  # warms the worktree list, git's command list and the checkout's config scopes
        r = self.assertRefused("git -C %s status" % self.nested, GIT_NESTED_WORDING)
        self.assertNotIn(GIT_SCOPE_WORDING, r.reason)  # the repository is refused before its keys are read
        cache = json.loads((self.home.path / STATE / "git-config-scopes.json").read_text(encoding="utf-8"))
        self.assertNotIn(str(self.nested / ".git"), cache)
        payload = self.pre_bash("git -C %s status" % self.nested, agent_id=AGENT_A)
        proc = subprocess.run([sys.executable, "-I", "-S", "-X", "importtime", str(SPUD), "hook", "PreToolUse"],
                              input=json.dumps(payload), capture_output=True, text=True, env=self.home.env)
        self.assertIn('"deny"', proc.stdout)
        imported = {line.rsplit("|", 1)[-1].strip() for line in proc.stderr.splitlines() if line.startswith("import time:")}
        self.assertNotIn("subprocess", imported)

    @staticmethod
    def plant_program_key(config):
        config.write_text("[core]\n\trepositoryformatversion = 0\n\tpager = /bin/echo\n", encoding="utf-8")

    def test_write_verbs_and_spd_047_keep_their_own_reasons(self):
        for cmd, needle in (("git -C %s commit -m x" % self.nested, "never run"), ("git -C %s push" % self.bare, "never run"),
                            ("git -C %s -c core.editor=vi log" % self.nested, "program git never checks")):
            with self.subTest(cmd):
                r = self.assertRefused(cmd, needle)
                self.assertNotIn(GIT_NESTED_WORDING, r.reason)


class GitVerbProgramOptionTest(BashHookCase):
    """SPD-051: SPD-046's table of verb options that name a program git runs listed only ls-remote/fetch --upload-pack,
    grep -O/--open-files-in-pager, difftool -x/--extcmd and archive --exec.  The same class lives on other verbs Law 7
    allows.  Probed on git 2.54.0 (Apple Git-157), scratchpad only, no real remote: `git send-email --dry-run
    --to-cmd=<prog> <patch>` and the same with `--cc-cmd` each ran <prog>, and so did the abbreviation `--to-cm=<prog>`;
    `git send-email -h` lists `--sendmail-cmd` ("Command to run to send email") and `--smtp-server`, which
    git-send-email(1) takes as a sendmail-like program when it is a path; `git web--browse` accepts `--browser`, `--tool`
    and `--config` and their spaced short forms `-b`, `-t` and `-c`, each naming the browser or the config key whose value
    git runs; `git instaweb` is not installed on this Mac (absent from `git --list-cmds=main`, so SPD-047 already refuses
    it here as an unknown verb), and its `--httpd`/`-d` and `--browser`/`-b` come from git-instaweb(1).  `git help` names
    no program on the line -- `-m`, `-w` and `-i` pick man, a browser or info, whose program comes from config the
    allowlist already refuses -- but git-help(1) documents GIT_MAN_VIEWER, which was missing from GIT_PROGRAM_ENV_VARS.

    git's parse-options takes any unambiguous prefix, so a `--`-prefix of one of these long options is refused whether or
    not git would resolve it exactly: `git send-email --to=x` is refused because `--to` is a prefix of `--to-cmd` (fail
    closed; a member never runs send-email).  Also pinned here: a clustered short option (`git grep -nO`), which SPD-046's
    round 2 closed, and a glob that expands to one of these options, which took two changes -- GLOB_SAMPLES held none of
    these verbs or options, and git_read_index stopped at the verb, so the option was never a read point at all."""

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

    def test_send_email_program_options_are_refused(self):
        for cmd in ("git send-email --sendmail-cmd=cmd p", "git send-email --sendmail-cmd cmd p",
                    "git send-email --smtp-server=/tmp/x/sendmail p", "git send-email --smtp-server /tmp/x p",
                    "git send-email --to-cmd=cmd p", "git send-email --cc-cmd=cmd p",
                    "git send-email --sendmail=cmd p", "git send-email --to-cm=cmd p", "git send-email --cc-c cmd p",
                    "git send-email --smtp-serv=/tmp/x p", "git send-email --to=x p", "git send-email --cc=x p"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        self.assertEqual(self.finding("git send-email --to-cmd=cmd p"),
                         [("git-program", "send-email --to-cmd=cmd")])

    def test_web_browse_program_options_are_refused(self):
        for cmd in ("git web--browse --browser=x u", "git web--browse --browser x u", "git web--browse -b x u",
                    "git web--browse --tool=x u", "git web--browse -t x u", "git web--browse --config=browser.x.cmd u",
                    "git web--browse -c browser.x.cmd u", "git web--browse --brow=x u", "git web--browse -bt u",
                    "git web--browse -ct browser.x.cmd u"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        self.assertEqual(self.finding("git web--browse --tool=x u"), [("git-program", "web--browse --tool=x")])

    def test_instaweb_program_options_are_refused(self):
        for cmd in ("git instaweb --httpd=lighttpd", "git instaweb --httpd lighttpd", "git instaweb -d lighttpd",
                    "git instaweb --browser=x", "git instaweb -b x", "git instaweb --htt=x", "git instaweb -ld x",
                    "git instaweb -pb x"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        self.assertEqual(self.finding("git instaweb --httpd=lighttpd"),
                         [("git-program", "instaweb --httpd=lighttpd")])

    def test_the_verbs_without_a_program_option_stay_silent(self):
        # Silent for Spud, whom Law 7 does not bind.  Since SPD-087 a member runs neither verb at all (send-email hands
        # the patch to a mailer, web--browse opens a browser), so what these pin is the finding: with no program-naming
        # option on the line there is no git-program finding to earn, only Law 7's own verb.
        for ok, verb in (("git send-email --dry-run p", "send-email"), ("git send-email --smtp-server-port=25 p", "send-email"),
                         ("git send-email --smtp-user=me p", "send-email"), ("git send-email --dump-aliases", "send-email"),
                         ("git web--browse u", "web--browse"), ("git web--browse --version", "web--browse")):
            with self.subTest(ok):
                self.assertSilent(ok, agent_id=None)
                self.assertEqual(self.finding(ok), [("git", (verb, verb))])
        # git instaweb is not installed on this Mac, so SPD-047's unknown-verb check refuses every form of it here; what
        # SPD-051 decides is which finding a program-naming option earns, not whether the verb is refused at all.
        for verb_only in ("git instaweb --port=1234", "git instaweb -p 1234", "git instaweb --local", "git instaweb stop"):
            with self.subTest(verb_only):
                self.assertEqual(self.finding(verb_only), [("git-verb", ("verb", "instaweb"))])

    def test_git_man_viewer_is_refused(self):
        for cmd in ("GIT_MAN_VIEWER=cmd git help git", "env GIT_MAN_VIEWER=cmd git status",
                    "export GIT_MAN_VIEWER=cmd; git log"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("GIT_MAN_VIEWER", r.reason)

    def test_git_help_names_no_program_on_the_line(self):
        for ok in ("git help", "git help git", "git help -a", "git help -m git", "git help -w git", "git help -i git",
                   "git help -c", "git help --all"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_clustered_short_options_stay_refused(self):
        # SPD-046's round 2 closed the cluster; pinned here with SPD-051's other short-option verbs.
        for cmd in ("git grep -nO foo", "git grep -nOcat foo", "git grep -inO foo", "git difftool -dx cmd A B"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_a_glob_that_expands_to_a_program_option_is_read(self):
        for cmd in ("git ls-remote --upload-pac? cmd .", "git fetch --upload-pac? cmd r",
                    "git send-email --to-cm? cmd p", "git difftool --extcm? cmd A B",
                    "git archive --exe? cmd HEAD"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_the_old_table_is_unchanged(self):
        for cmd in ("git ls-remote --upload-pack=cmd host:r", "git fetch --upload-pack cmd r", "git grep -O foo",
                    "git difftool --extcmd=cmd A B", "git archive --exec=cmd HEAD"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        for ok in ("git fetch", "git ls-remote host:r", "git grep -n foo", "git archive HEAD"):  # difftool: SPD-087
            with self.subTest(ok):
                self.assertSilent(ok)


# The GIT_TRACE* siblings, probed on git 2.54.0 (Apple Git-157) in the scratchpad, each set to an absolute path with a
# plain `git status`: GIT_TRACE, GIT_TRACE_PERFORMANCE, GIT_TRACE_SETUP, GIT_TRACE_PACK_ACCESS, GIT_TRACE_REFS,
# GIT_TRACE2, GIT_TRACE2_EVENT and GIT_TRACE2_PERF each appended a file there on that one read verb; GIT_TRACE_PACKET,
# GIT_TRACE_SHALLOW, GIT_TRACE_FSMONITOR, GIT_TRACE_CURL and GIT_TRACE_PACKFILE wrote nothing under `status` (they trace
# transports and packfiles, under fetch, ls-remote and their kin) and take the same value, so the whole family counts.
GIT_TRACE_VARS = ("GIT_TRACE", "GIT_TRACE_PACKET", "GIT_TRACE_PERFORMANCE", "GIT_TRACE_SETUP", "GIT_TRACE_SHALLOW",
                  "GIT_TRACE_PACK_ACCESS", "GIT_TRACE_REFS", "GIT_TRACE_FSMONITOR", "GIT_TRACE_CURL",
                  "GIT_TRACE_PACKFILE", "GIT_TRACE2", "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF")


class GitFileWriteTest(BashHookCase):
    """SPD-049: a git call under a verb Law 7 allows writes a file of its own choosing through an option or the
    environment, and the hook checked neither -- so a member wrote outside its deliverables and Spud past Law 1, with a
    trace appended to a rendered ledger note or the database corrupting it.

    Probed on git 2.54.0 (Apple Git-157), scratchpad only.  Environment: every GIT_TRACE* variable set to an absolute
    path appended its trace there (GIT_TRACE, GIT_TRACE_PERFORMANCE, GIT_TRACE_SETUP, GIT_TRACE_PACK_ACCESS,
    GIT_TRACE_REFS, GIT_TRACE2, GIT_TRACE2_EVENT and GIT_TRACE2_PERF did so under a bare `git status`), a directory
    value made GIT_TRACE2 write one file per process inside it, and `0`, `1`, `2`, `3`, `9`, `true`, `false` and an
    empty value wrote nothing (a descriptor or off), while a relative value only warned ("unknown trace value for
    'GIT_TRACE'") and wrote nothing.  GIT_INDEX_FILE named the index `git read-tree HEAD` wrote (44 KB), and
    GIT_OBJECT_DIRECTORY the directory `git hash-object -w --stdin` wrote a loose object into.

    Options: `--output` (spaced and `=`) opened its file under diff, log, show, whatchanged, format-patch, range-diff,
    diff-tree, diff-index, diff-files and blame; `git archive -o F`, `-oF`, `--output=F` and `--output F` each wrote the
    tar; `git format-patch -o D`, `-oD`, `--output-directory=D`, `--output-directory D` and the cluster `-so D` each
    wrote the patch into D; `git bundle create F HEAD` (with `-q` or `--version=2` before it) wrote the bundle;
    `git bugreport -o D` and `git diagnose -o D` wrote their report and zip into D; `git checkout-index -a --prefix=D/`
    wrote the whole tree there; `git mailsplit -oD mbox` wrote `0001`; `git mailinfo F F2` wrote both; and
    `git pack-objects D/pack --revs` wrote the pack, its index and its rev file.  `git init` and `git clone` also make a
    directory wherever they are pointed, but GIT_WRITE_VERBS already refuses both to a member whole, and Spud's own
    `git init` is his, so their targets are left alone.

    Each such path is checked with the path rule, like a redirection target, against every directory the shell may be
    in; a path the hook cannot resolve refuses a member, fail closed.  Law 1 holds for Spud here as it does for a
    redirection, so a trace appended to a rendered ledger note is refused to everyone.  AGENT_A plans tests/** and
    bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/out", "bin", ".claude/patches"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger/tickets/SPD-001.md").write_text("orig\n", encoding="utf-8")
        (home / "docs/x.md").write_text("orig\n", encoding="utf-8")

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def out_of_deliverables(self, command, needle="deliverables"):
        """Refused for AGENT_A, whose deliverables are tests/** and bin/spud, and silent for AGENT_C, whose are **."""
        r = self.assertRefused(command, needle, AGENT_A)
        with self.subTest(command=command, agent_id=AGENT_C):
            self.assertSilent(command, AGENT_C)
        return r

    def refused_for_everyone(self, command, needle="generated"):
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertRefused(command, "Law 1", agent_id=None)

    # -- the environment ---------------------------------------------------------------

    def test_every_trace_variable_by_every_route_is_refused(self):
        home = self.home.path
        for var in GIT_TRACE_VARS:
            for line in ("%s=%s/docs/trace.log git status", "env %s=%s/docs/trace.log git status",
                         "export %s=%s/docs/trace.log; git status", "%s=%s/docs/trace.log; git status"):
                command = line % (var, home)
                with self.subTest(command):
                    r = self.out_of_deliverables(command)
                    self.assertIn("docs/trace.log", r.reason)
                    self.assertIn(var, r.reason)

    def test_a_trace_variable_inside_the_deliverables_is_silent(self):
        home = self.home.path
        for var in ("GIT_TRACE", "GIT_TRACE2_EVENT"):
            for line in ("%s=%s/tests/out/trace.log git status", "env %s=%s/tests/out/trace.log git status",
                         "export %s=%s/tests/out/trace.log; git status"):
                with self.subTest(line % (var, home)):
                    self.assertSilent(line % (var, home))

    def test_a_descriptor_or_an_off_value_is_silent(self):
        for value in ("0", "1", "2", "3", "9", "true", "false", "", "docs/trace.log", "./docs/trace.log"):
            for var in ("GIT_TRACE", "GIT_TRACE2"):
                command = "%s=%s git status" % (var, value)
                with self.subTest(command):
                    self.assertSilent(command)
                    self.assertSilent(command, agent_id=None)

    def test_a_trace_value_the_hook_cannot_resolve_is_refused(self):
        for command in ("GIT_TRACE=$T git status", "env GIT_TRACE=$(echo x) git status",
                        "export GIT_TRACE2=$T; git status"):
            with self.subTest(command):
                self.assertRefused(command, "cannot resolve", AGENT_A)
                self.assertRefused(command, "cannot resolve", agent_id=None)  # SPD-091: Spud too

    def test_a_trace_variable_without_a_git_call_is_silent(self):
        home = self.home.path
        for ok in ("GIT_TRACE=%s/docs/trace.log ls", "GIT_TRACE=%s/docs/trace.log; ls", "echo GIT_TRACE=%s/docs/t"):
            with self.subTest(ok % home):
                self.assertSilent(ok % home)
                self.assertSilent(ok % home, agent_id=None)

    def test_the_index_and_object_directory_variables_are_refused(self):
        home = self.home.path
        for command in ("env GIT_INDEX_FILE=%s/docs/idx git status" % home,
                        "GIT_INDEX_FILE=%s/docs/idx git diff" % home,
                        "GIT_OBJECT_DIRECTORY=%s/docs/objects git status" % home):
            with self.subTest(command):
                self.out_of_deliverables(command)
        # SPD-087 made read-tree and hash-object Law 7's own, so a member is refused by the verb before the path is
        # read; the variable still names the file Spud's own call writes, and Law 1 still refuses him a generated note.
        for command in ("GIT_INDEX_FILE=%s/docs/idx git read-tree HEAD" % home,
                        "GIT_OBJECT_DIRECTORY=%s/docs/objects git hash-object -w --stdin" % home,
                        "export GIT_OBJECT_DIRECTORY=%s/docs/objects; git hash-object -w f" % home,
                        "GIT_INDEX_FILE=%s/ledger/tickets/SPD-001.md git read-tree HEAD" % home):
            with self.subTest(command):
                self.assertRefused(command, "Law 7", AGENT_A)
                self.assertRefused(command, "Law 7", AGENT_C)
                self.assertRefused(command, "Law 1", agent_id=None)  # the variable is still read for Spud
        self.assertSilent("GIT_INDEX_FILE=%s/CLAUDE.md git read-tree HEAD" % home, agent_id=None)

    def test_a_trace_into_a_generated_note_or_the_database_is_refused_for_spud_too(self):
        home = self.home.path
        self.refused_for_everyone("GIT_TRACE=%s/ledger/tickets/SPD-001.md git status" % home)
        self.refused_for_everyone("GIT_TRACE2_EVENT=%s/reports/2026-09-15.md git log -1" % home)
        self.refused_for_everyone("env GIT_TRACE=%s/ledger/teams/SPUD-001/Russet.md git status" % home)
        # ledger/Home.md is Spud's own hand-written note, so the path rule lets him write it and refuses a member
        self.assertSilent("GIT_TRACE=%s/ledger/Home.md git status" % home, agent_id=None)
        self.assertRefused("GIT_TRACE=%s/ledger/Home.md git status" % home, "generated", AGENT_C)
        for agent_id in (AGENT_A, None):
            with self.subTest(agent_id=agent_id):
                self.assertRefused("GIT_TRACE=%s/.spud/ledger.db git status" % home, "ledger database", agent_id)

    # -- the options -------------------------------------------------------------------

    def test_archive_output_in_every_spelling(self):
        home = self.home.path
        for spelling in ("-o %s/docs/a.tar", "-o%s/docs/a.tar", "--output=%s/docs/a.tar", "--output %s/docs/a.tar",
                         "--out=%s/docs/a.tar", "--outp %s/docs/a.tar"):
            command = "git archive %s HEAD" % (spelling % home)
            with self.subTest(command):
                self.out_of_deliverables(command)
        for ok in ("git archive -o tests/out/a.tar HEAD", "git archive --output=tests/out/a.tar HEAD"):
            with self.subTest(ok):
                self.assertSilent(ok)  # inside AGENT_A's deliverables; Law 1 still refuses Spud that path, as a redirect would
                self.assertRefused(ok, "Law 1", agent_id=None)
        for ok in ("git archive HEAD", "git archive --format=tar HEAD", "git archive -o /dev/null HEAD"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_format_patch_output_directory_in_every_spelling(self):
        home = self.home.path
        for spelling in ("-o %s/docs", "-o%s/docs", "--output-directory=%s/docs", "--output-directory %s/docs",
                         "-so %s/docs", "--output=%s/docs/p.patch"):
            command = "git format-patch %s -1" % (spelling % home)
            with self.subTest(command):
                self.out_of_deliverables(command)
        self.assertSilent("git format-patch -o tests/out -1")
        self.assertSilent("git format-patch --stdout -1")
        # with no -o the files land here, at the home's root, outside tests/** (SPD-093, GitCwdWriteTest)
        self.assertRefused("git format-patch -1", "default form")

    def test_the_diff_output_option_on_every_verb_that_takes_it(self):
        home = self.home.path
        for verb in ("diff", "log", "show", "whatchanged", "range-diff", "diff-tree", "diff-index", "diff-files",
                     "blame"):
            for spelling in ("--output=%s/docs/d.txt", "--output %s/docs/d.txt"):
                command = "git %s %s" % (verb, spelling % home)
                with self.subTest(command):
                    self.out_of_deliverables(command)
        self.assertSilent("git diff --output=tests/out/d.txt")
        self.assertSilent("git log --oneline -5")  # --oneline is no prefix of --output
        self.assertSilent("git log --output-indicator-new=x -1")

    def test_bundle_create_names_the_file_it_writes(self):
        # SPD-087 made `git bundle` Law 7's own whole -- `unbundle` writes the objects and refs the bundle holds into
        # the repository -- so a member runs no form of it; the file `create` names is still held to Spud's path rule.
        home = self.home.path
        for command in ("git bundle create %s/docs/b.bundle HEAD", "git bundle create -q %s/docs/b.bundle HEAD",
                        "git bundle create --version=2 %s/docs/b.bundle HEAD", "git bundle verify %s/docs/b.bundle",
                        "git bundle list-heads %s/docs/b.bundle", "git bundle unbundle %s/docs/b.bundle",
                        "git bundle create tests/out/b.bundle HEAD"):
            filled = command % home if "%s" in command else command
            with self.subTest(filled):
                self.assertRefused(filled, "Law 7", AGENT_A)
                self.assertRefused(filled, "Law 7", AGENT_C)
        for spud_reads_the_path in ("git bundle create %s/docs/b.bundle HEAD", "git bundle create -q %s/docs/b.bundle HEAD",
                                    "git bundle create --version=2 %s/docs/b.bundle HEAD",
                                    "git bundle create %s/ledger/tickets/SPD-001.md HEAD"):
            with self.subTest(spud_reads_the_path % home):
                self.assertRefused(spud_reads_the_path % home, "Law 1", agent_id=None)
        for spud_writes_nothing in ("git bundle verify %s/docs/b.bundle", "git bundle list-heads %s/docs/b.bundle",
                                    "git bundle unbundle %s/docs/b.bundle"):
            with self.subTest(spud_writes_nothing % home):
                self.assertSilent(spud_writes_nothing % home, agent_id=None)

    def test_the_other_verbs_that_name_a_path_they_write(self):
        home = self.home.path
        for command in ("git bugreport -o %s/docs", "git bugreport --output-directory=%s/docs",
                        "git diagnose -o %s/docs", "git diagnose --output-directory %s/docs",
                        "git mailsplit -o%s/docs mbox", "git mailinfo %s/docs/msg %s/docs/patch",
                        "git fast-export --export-marks=%s/docs/marks HEAD"):
            filled = command % ((home,) * command.count("%s"))
            with self.subTest(filled):
                self.out_of_deliverables(filled)
        self.assertSilent("git bugreport -o tests/out")
        # an option that only reads the file it names carries no entry
        for ok in ("git archive --add-file=%s/docs/x.md HEAD", "git grep -f %s/docs/patterns foo",
                   "git ls-files -X %s/docs/exclude"):
            with self.subTest(ok % home):
                self.assertSilent(ok % home)

    def test_a_verb_law_7_now_refuses_keeps_its_entry_for_spud(self):
        """SPD-087 refuses these verbs to a member whole -- each writes the index, the object database, the pack files
        or the credential file -- so the member is refused by the verb before the path is read.  Their entries in
        GIT_VERB_FILE_OPTIONS and GIT_VERB_FILE_POSITIONALS stay, because Law 7 does not bind Spud: his own call still
        names the file, and Law 1 still refuses him every path in the repository that is not his own."""
        home = self.home.path
        for command in ("git checkout-index -a --prefix=%s/docs/", "git checkout-index -a --prefix %s/docs/",
                        "git pack-objects %s/docs/pack", "git index-pack -o %s/docs/x.idx",
                        "git read-tree --index-output=%s/docs/idx HEAD", "git read-tree --index-output %s/docs/idx HEAD",
                        "git commit-graph write --object-dir %s/docs", "git multi-pack-index --object-dir=%s/docs write",
                        "git repack --expire-to=%s/docs", "git repack --filter-to %s/docs",
                        "git credential-store --file %s/docs/creds get", "git checkout-index -a --prefix=tests/out/",
                        "git read-tree --index-output=tests/out/idx HEAD",
                        "git read-tree --index-output=%s/ledger/tickets/SPD-001.md HEAD",
                        "git index-pack -o %s/reports/2026-09-15.md"):
            filled = command % ((home,) * command.count("%s"))
            with self.subTest(filled):
                self.assertRefused(filled, "Law 7", AGENT_A)
                self.assertRefused(filled, "Law 7", AGENT_C)
                r = self.assertRefused(filled, "Law 1", agent_id=None)  # the path is still read for Spud
                self.assertIn("this git call writes", r.reason)
        self.assertRefused("git read-tree --index-output=%s/.spud/ledger.db HEAD" % home, "ledger database", agent_id=None)
        self.assertSilent("git read-tree --index-output=%s/CLAUDE.md HEAD" % home, agent_id=None)  # one of Spud's own

    def test_an_option_target_the_hook_cannot_resolve_is_refused(self):
        for command in ("git archive -o $T HEAD", "git archive --output=$(echo x) HEAD",
                        "git format-patch -o $D -1", "git mailinfo $M $P"):
            with self.subTest(command):
                self.assertRefused(command, "cannot resolve", AGENT_A)
                self.assertRefused(command, "cannot resolve", agent_id=None)  # SPD-091: Spud too
        # a verb SPD-087 refuses whole earns Law 7's reason first; Spud, whom Law 7 does not bind, meets the target's
        self.assertRefused("git bundle create $F HEAD", "Law 7", AGENT_A)
        self.assertRefused("git bundle create $F HEAD", "cannot resolve", agent_id=None)
        # a value the line settles is read, for Spud as for a member (SPD-127)
        self.assertRefused("T=%s/docs/a.tar; git archive -o $T HEAD" % self.home.path, "Law 1", agent_id=None)
        self.assertSilent("T=/tmp/spd-091.tar; git archive -o $T HEAD", agent_id=None)

    # -- a glob or an expansion that becomes one of the options (SPD-089) -------------------

    # One line per verb of syntax.GIT_VERB_FILE_OPTIONS, and `git log` for GIT_FILE_OPTIONS on a verb with no entry:
    # (the line, {opt} where the option goes; its long option or None; its short letter or None).
    FILE_OPTION_LINES = (
        ("git archive {opt} HEAD", "--output", "o"),
        ("git format-patch {opt} -1", "--output-directory", "o"),
        ("git bugreport {opt}", "--output-directory", "o"),
        ("git diagnose {opt}", "--output-directory", "o"),
        ("git checkout-index -a {opt}", "--prefix", None),
        ("git mailsplit {opt} mbox", None, "o"),
        ("git index-pack {opt} p.pack", None, "o"),
        ("git read-tree {opt} HEAD", "--index-output", None),
        ("git fast-export {opt} HEAD", "--export-marks", None),
        ("git commit-graph write {opt}", "--object-dir", None),
        ("git multi-pack-index {opt} write", "--object-dir", None),
        ("git repack {opt}", "--expire-to", None),
        ("git credential-store {opt} get", "--file", None),
        ("git log {opt}", "--output", None),
    )

    def refused_to_members(self, command):
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertRefused(command, "", agent_id)

    def test_a_glob_that_expands_to_a_file_option_is_read(self):
        """SPD-049 read every literal spelling of these options, but a word that becomes one only once the shell has
        expanded it was read as spelled: `git archive --outp?t=<path> HEAD` passed.  Probed with tests/probes/shell_probe.py
        (zsh -f -o nobareglobqual, zsh -f, bash 3.2), printf standing in for git: with a directory `--output=` holding
        etc/x, `--outp?t=/etc/x` became `--output=/etc/x`, which git reads as --output with the absolute value /etc/x;
        `--outp?t=x` became `--output=x` and `-?x.tar` became `-ox.tar` from files of those names; and `--outp?t=y`
        matched a file `--outp=t=y`, a `?` standing for the `=` itself, which git reads as --outp=<t=y>.  So which option
        such a glob becomes, and with which value, is the files' to decide: a member is refused it whatever it may
        become, and Spud's call is read as each option its key can match, so Law 1 still holds his path."""
        note = "%s/ledger/tickets/SPD-001.md" % self.home.path
        for line, long, short in self.FILE_OPTION_LINES:
            spellings = []
            if long:
                spellings += ["%s? %s" % (long[:-1], note), "%s?%s=%s" % (long[:-2], long[-1], note)]
            if short:
                spellings += ["-? %s" % note]
            for spelling in spellings:
                command = line.format(opt=spelling)
                self.refused_to_members(command)
                with self.subTest(command=command, agent_id="spud"):
                    self.assertRefused(command, "Law 1", agent_id=None)

    def test_an_expansion_that_may_become_a_file_option_is_read(self):
        """`git archive $OPT HEAD`: git's read points stopped at every verb outside GIT_VERB_PROGRAM_OPTIONS, so the word
        was never read.  On a verb of GIT_VERB_FILE_OPTIONS a word that starts with its expansion may become any option,
        so an unsettled one refuses a member (Spud reads on, as for SPD-051's options), and a settled one is read as
        the value the line gave it (probed: `OPT='--output=/tmp/a'; printf '[%s]' $OPT` printed `--output=/tmp/a`)."""
        note = "%s/ledger/tickets/SPD-001.md" % self.home.path
        for line, long, short in self.FILE_OPTION_LINES:
            if line.startswith("git log"):
                continue  # a verb with no entry: its words are read only where spelled with a leading `-` (below)
            command = line.format(opt="$OPT")
            self.refused_to_members(command)
            # format-patch, bugreport and diagnose with no -o write where they run (SPD-093), and mailsplit with none writes
            # into its last word (SPD-228), which Law 1 lets Spud do only among his own paths; an unsettled $OPT may
            # not be their -o
            cwd = (str(self.home.path / ".claude" / "patches")
                   if line.split()[1] in ("format-patch", "bugreport", "diagnose", "mailsplit") else None)
            with self.subTest(command=command, agent_id="spud"):
                self.assertSilent(command, agent_id=None, cwd=cwd)
            settled = "OPT=%s; %s" % ("%s=%s" % (long, note) if long else "-%s%s" % (short, note), command)
            with self.subTest(command=settled, agent_id="spud"):
                self.assertRefused(settled, "Law 1", agent_id=None, cwd=cwd)
        for command in ("git log -$X", "git log --outp$X=d.txt", "git diff --o$(echo utput)=d.txt"):
            self.refused_to_members(command)

    def test_a_word_that_cannot_become_a_file_option_is_left_as_spelled(self):
        # Its literal head has settled it already (an option with its `=`, a short option that names no file, a path),
        # or it stands where a spaced option takes its value, which the path rule reads.
        for ok in ("git log --grep=$P", "git log --author=$ME -1", "git log -S$X", "git diff $A $B", "git show HEAD:$F",
                   "git log --oneline src/*.py", "git format-patch --stdout --subject-prefix=$P -1", "git archive HEAD src/*.py",
                   "git log --format=%h*"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        self.assertSilent("git format-patch -o tests/out -1")  # inside AGENT_A's deliverables (Law 1 refuses Spud)
        self.assertRefused("git archive -o $T HEAD", "cannot resolve", AGENT_A)
        self.assertRefused("git format-patch -o $D -1", "cannot resolve", AGENT_A)

    def test_a_relative_target_after_a_directory_the_hook_cannot_follow_is_refused(self):
        for command in ("popd; git archive -o a.tar HEAD", "cd -; git mailinfo m.txt p.patch",
                        "source x.sh; git diff --output=d.txt", "cd $DIR; git format-patch -o out -1"):
            with self.subTest(command):
                self.assertRefused(command, "cannot follow", AGENT_A)
                self.assertRefused(command, "cannot follow", agent_id=None)
        self.assertRefused("cd -; git bundle create b.bundle HEAD", "Law 7", AGENT_A)  # the verb, before the path
        self.assertRefused("cd -; git bundle create b.bundle HEAD", "cannot follow", agent_id=None)

    def test_the_directories_the_shell_may_be_in_are_followed(self):
        home = self.home.path
        self.assertRefused("cd ledger && git archive -o tickets/a.tar HEAD", "generated", AGENT_C)
        self.assertRefused("cd %s/ledger; git archive -o tickets/SPD-001.md HEAD" % home, "Law 1", agent_id=None)
        self.assertRefused("cd docs && git bugreport -o .", "deliverables", AGENT_A)
        self.assertSilent("cd tests/out && git archive -o a.tar HEAD")

    def test_law_1_holds_for_spud_and_his_own_paths_do_not(self):
        home = self.home.path
        self.assertRefused("git archive -o %s/ledger/tickets/SPD-001.md HEAD" % home, "Law 1", agent_id=None)
        self.assertRefused("git diff --output %s/reports/2026-09-15.md" % home, "Law 1", agent_id=None)
        self.assertSilent("git archive -o %s/CLAUDE.md HEAD" % home, agent_id=None)
        self.assertSilent("git archive -o /dev/null HEAD", agent_id=None)
        self.assertRefused("git archive -o %s/CLAUDE.md HEAD" % home, "deliverables", AGENT_A)

    def test_init_and_clone_keep_law_7s_own_reason(self):
        home = self.home.path
        for command in ("git init %s/docs/new" % home, "git clone https://x/y %s/docs/new" % home):
            with self.subTest(command):
                self.assertRefused(command, "Law 7", AGENT_A)
                self.assertRefused(command, "Law 7", AGENT_C)
                self.assertSilent(command, agent_id=None)

    def test_a_refusal_the_words_as_spelled_earn_keeps_its_own_reason(self):
        home = self.home.path
        r = self.assertRefused("git commit -o %s/docs/a.tar -m x" % home, "Law 7", AGENT_A)
        self.assertIn("git commit", r.reason)
        r = self.assertRefused("git archive --exec=cmd -o %s/docs/a.tar HEAD" % home, "Law 7", AGENT_A)
        self.assertIn("--exec", r.reason)
        self.assertRefused("GIT_TRACE=%s/docs/t.log git push" % home, "git push", AGENT_A)

    def test_the_channel_records_what_the_call_writes(self):
        home = self.home.path
        cwds = frozenset([str(home)])
        self.assertEqual(self.analysis("git archive -o docs/a.tar HEAD").git_writes,
                         [("archive -o docs/a.tar", "docs/a.tar", cwds)])
        self.assertEqual(self.analysis("GIT_TRACE=/tmp/t git status").git_writes,
                         [("GIT_TRACE=/tmp/t", "/tmp/t", cwds)])
        self.assertEqual(self.analysis("git status").git_writes, [])
        self.assertEqual(self.analysis("GIT_TRACE=1 git status").git_writes, [])

    def test_inside_shell_strings_eval_and_subshells(self):
        home = self.home.path
        for command in ("sh -c 'git archive -o %s/docs/a.tar HEAD'", "eval 'GIT_TRACE=%s/docs/t.log git status'",
                        "(git archive -o %s/docs/a.tar HEAD)", "true && git archive -o %s/docs/a.tar HEAD",
                        "for f in a; do git archive -o %s/docs/a.tar HEAD; done"):
            with self.subTest(command % home):
                self.assertRefused(command % home, "deliverables", AGENT_A)


AGENT_PATCHES = "a4b5c6d7e8f9a0b1c"  # SPD-093: a member holding out/* and patches/*.patch
PICKED = ""  # hooks/pathrule.NAME_CHAR + NAME_MORE: a name git picks, of any length; a reason shows `?*`


class GitCwdWriteTest(BashHookCase):
    """SPD-093: `git format-patch -1`, `git bugreport` and `git diagnose`, in their default forms, write a file of git's
    own naming into the directory git runs in, and no rule saw it: a member dropped files into a checkout outside its
    deliverables, and Spud past Law 1.  syntax.GIT_VERB_CWD_WRITES reads that form as a new file directly in that
    directory whose name git picks (PICKED), held to the path rule like a redirection target, so a glob covers it only
    when it covers every file directly there.

    Probed on git 2.54.0 (Apple Git-157), in a directory under this worktree's tests/ (removed after) and in the
    scratchpad.  `git format-patch -1 <rev>` wrote 0001-<subject>.patch into the directory it ran in, `git -C a -C b
    format-patch` into a/b and `git -C a -C ""` into a; `git bugreport` wrote git-bugreport-<date>.txt there, `git
    diagnose` git-diagnostics-<date>.zip, and `git bugreport --diagnose` both.  `-o ''`, `--output-directory=` and
    `--stdout --no-stdout` wrote there too; `--output=F` and `--output F` wrote one file F.  format-patch takes no
    abbreviation of any option (`--std`, `--output-dir=D`, `--outp=F` and `--no-std` were each "unrecognized
    argument") and refuses `--no-output-directory`; bugreport took `--out=D` as -o and `--no-o` set it back.
    `--subject-prefix --stdout -1` and `--to --stdout -1` wrote 0001-...patch here: an option of format-patch's own
    took --stdout as its value, as the diff option --src-prefix took `--output=F`.  `-h` and `--help` printed usage and
    wrote nothing wherever they stood (exit 129).  The
    names: `-v ../x/y` wrote v.-x-y-0001-... (git sanitises the reroll count), but `--suffix=/../b/x` wrote b/x through a
    directory named for the patch, bugreport's `-s /../x` wrote ./x.txt (making git-bugreport-/), `-o D -s /../../x`
    wrote beside D, diagnose's `-s /../../dx` two levels up, and `-s %D` wrote git-bugreport-09/23/26.txt, as %x, %Ex,
    %OD, %_D, %0x and %-D did.

    AGENT_A plans tests/** and bin/spud, AGENT_C home:** and AGENT_PATCHES out/* and patches/*.patch; each line runs at
    the home's root unless it says otherwise."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.narrow = self.spawn(self.plan(actor=self.lead["ref"], persona="engineer", model="opus",
                                           deliverable=["home:out/*", "home:patches/*.patch"]), AGENT_PATCHES, caller=AGENT_A)
        for d in ("docs/superpowers/specs", "tests/out", "out", "patches", ".claude/patches", "ledger/tickets"):
            (self.home.path / d).mkdir(parents=True, exist_ok=True)

    def at(self, *parts):
        return str(self.home.path.joinpath(*parts))

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def test_the_default_forms_outside_every_glob_are_refused(self):
        for command in ("git format-patch -1", "git format-patch HEAD~3", "git format-patch --cover-letter -n -3",
                        "git bugreport", "git bugreport --diagnose", "git diagnose", "git diagnose --mode=all"):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables", AGENT_A)
                self.assertIn("default form, into ./?*", r.reason)
                self.assertRefused(command, "deliverables", AGENT_PATCHES)
                self.assertSilent(command, AGENT_C)

    def test_a_directory_whose_every_file_a_glob_covers_is_allowed(self):
        for command, cwd in (("git format-patch -1", ("tests",)), ("git format-patch -3", ("tests", "out")),
                             ("git bugreport", ("tests", "out")), ("git diagnose", ("tests",))):
            with self.subTest(command, cwd=cwd):
                self.assertSilent(command, AGENT_A, cwd=self.at(*cwd))
        for command in ("cd tests/out && git format-patch -1", "cd tests; git bugreport", "(cd tests/out && git diagnose)"):
            with self.subTest(command):
                self.assertSilent(command, AGENT_A)
        for command in ("cd out && git format-patch -1", "cd out; git bugreport --diagnose", "cd out && git diagnose"):
            with self.subTest(command):
                self.assertSilent(command, AGENT_PATCHES)

    def test_a_glob_that_covers_some_names_git_may_pick_does_not_cover_it(self):
        """patches/*.patch matches 0001-<subject>.patch, but not what --numbered-files, --suffix or a format.suffix in a
        config the hook does not read make of it, so a name git picks is read as any name at all."""
        for command in ("cd patches && git format-patch -1", "git -C patches format-patch -1",
                        "cd patches && git format-patch --suffix=.patch -1"):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables", AGENT_PATCHES)
                self.assertIn("patches/?*", r.reason)

    def test_spud_is_held_to_law_1(self):
        for command in ("git format-patch -1", "git bugreport", "cd docs && git diagnose", "git -C ledger/tickets format-patch -1"):
            with self.subTest(command):
                self.assertRefused(command, "Law 1", agent_id=None)
        for command in ("cd .claude/patches && git format-patch -1", "git -C docs/superpowers/specs bugreport",
                        "git -C .claude/patches diagnose -s %Y"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=None)
        self.assertRefused("cd ledger/tickets && git format-patch -1", "generated", AGENT_C)

    def test_minus_C_and_the_directories_the_shell_may_be_in_are_followed(self):
        home = self.home.path
        for ok in ("git -C tests/out format-patch -1", "git -C tests -C out format-patch -1",
                   "git -C docs -C %s/tests/out format-patch -1" % home, "cd docs && git -C ../tests/out format-patch -1",
                   "cd tests/out; git -C .. bugreport", "git -C tests/out -C '' diagnose"):
            with self.subTest(ok):
                self.assertSilent(ok, AGENT_A)
        for refused in ("git -C docs format-patch -1", "git -C tests -C ../docs format-patch -1",
                        "cd tests/out && git -C ../../docs bugreport", "cd docs && git diagnose",
                        "git -C tests/out -C %s/docs diagnose" % home):
            with self.subTest(refused):
                r = self.assertRefused(refused, "deliverables", AGENT_A)
                self.assertIn("docs/?*", r.reason)

    def test_a_directory_the_hook_cannot_follow_refuses_everyone(self):
        for command in ("cd $DIR; git format-patch -1", "popd; git bugreport", "cd -; git diagnose"):
            with self.subTest(command):
                self.assertRefused(command, "cannot follow", AGENT_A)
                self.assertRefused(command, "cannot follow", agent_id=None)
        for command in ("git -C $D format-patch -1", "git -C $(pwd)/x bugreport"):
            with self.subTest(command):
                self.assertRefused(command, "cannot resolve", AGENT_A)
                self.assertRefused(command, "cannot resolve", agent_id=None)

    def test_the_forms_that_send_the_files_elsewhere_keep_their_own_reading(self):
        """-o's directory stays GitFileWriteTest's (SPD-049); --stdout and --output write nothing here."""
        for ok in ("git format-patch --stdout -1", "git format-patch -1 --stdout", "git format-patch HEAD~2 --stdout -k",
                   "git format-patch -o tests/out -1", "git format-patch -otests/out -1",
                   "git format-patch --output-directory=tests/out -3", "git format-patch --output-directory tests/out -1",
                   "git format-patch -ko tests/out -1", "git format-patch -so tests/out -1", "git format-patch -nNkso tests/out -1",
                   "git format-patch --output=tests/out/all.patch -2", "git format-patch --output tests/out/all.patch -1",
                   "git format-patch HEAD~2 --output=tests/out/all.patch", "git format-patch --no-stdout --stdout -1",
                   "git format-patch --subject-prefix=RFC --stdout -1", "git format-patch --subject-prefix RFC --stdout -1",
                   "git format-patch -1 -M --stdout", "git format-patch --to x@y --cc z@w --stdout -1",
                   "git format-patch --stdout -- -o", "git bugreport -o tests/out", "git bugreport --output-directory=tests/out --diagnose",
                   "git bugreport --out=tests/out", "git bugreport --output tests/out", "git bugreport -o tests/out --no-suffix",
                   "git diagnose -otests/out", "git diagnose --mode stats -o tests/out"):
            with self.subTest(ok):
                self.assertSilent(ok, AGENT_A)
                self.assertEqual([w for w in self.analysis(ok).git_writes if "default form" in w[0]], [])

    def test_a_word_git_takes_as_an_options_value_sends_nothing_away(self):
        for command in ("git format-patch --subject-prefix --stdout -1", "git format-patch --to --stdout -1",
                        "git format-patch -v --stdout -1", "git format-patch --stdout --no-stdout -1",
                        "git format-patch --signature --output=tests/out/x.patch -1", "git format-patch --subject-prefix -h -1",
                        "git format-patch --src-prefix --output=tests/out/x.patch -1", "git bugreport -s --output-directory=tests/out"):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables", AGENT_A)
                self.assertIn("default form", r.reason)

    def test_a_later_parses_option_the_hook_does_not_know_keeps_the_reading(self):
        """format-patch hands every word it does not know to the revision and diff options, where --output is read, and
        one of those that takes a value takes --output as its value: `--src-prefix --output=F -1` wrote 0001-...patch here
        and no F (probed).  Which of them take one is not read, so any such option before --output keeps the default
        reading, fail closed, at the price of `-M --output=F -1`, which wrote F alone (probed)."""
        self.assertRefused("git format-patch -M --output=tests/out/x.patch -1", "default form", AGENT_A)
        self.assertSilent("git format-patch -3 --output=tests/out/x.patch", AGENT_A)
        self.assertSilent("git format-patch --output=tests/out/x.patch -M -1", AGENT_A)

    def test_help_writes_nothing(self):
        for ok in ("git format-patch -h", "git format-patch --help", "git format-patch -1 -h", "git format-patch -1 --help",
                   "git bugreport -h", "git bugreport -s x -h", "git diagnose --help", "git diagnose -s x -h"):
            with self.subTest(ok):
                self.assertSilent(ok, AGENT_A)

    def test_a_suffix_with_a_slash_is_read_where_it_lands(self):
        # out/* covers every file directly in out, so the default form runs there; a name a suffix sends through a
        # directory is not directly there
        for ok in ("cd out && git bugreport -s %Y%m%d-%H%M", "cd out && git bugreport --suffix=%F", "cd out && git diagnose -s x",
                   "cd out && git format-patch --suffix=.txt -1", "cd out && git format-patch -v /../../x -1"):
            with self.subTest(ok):
                self.assertSilent(ok, AGENT_PATCHES)
        for refused in ("cd out && git bugreport -s /../../docs/x", "cd out && git bugreport -s x/y",
                        "cd out && git bugreport -s %D", "cd out && git bugreport -s %-D", "cd out && git bugreport -s %Ex",
                        "cd out && git bugreport --suf=/../docs/x", "cd out && git diagnose -s /../../docs/x",
                        "cd out && git diagnose --suffix %x", "cd out && git format-patch --suffix=/../../docs/x -1",
                        "git -C out bugreport -s /../../x"):
            with self.subTest(refused):
                self.assertRefused(refused, "deliverables", AGENT_PATCHES)
        # tests/** covers any depth, so a date's slashes stay inside it; a suffix that climbs out does not
        self.assertSilent("cd tests/out && git bugreport -s %D", AGENT_A)
        r = self.assertRefused("cd tests/out && git bugreport -s /../../../docs/x", "deliverables", AGENT_A)
        self.assertIn("docs/x.txt", r.reason)

    def test_a_suffix_is_read_under_the_directory_minus_o_names(self):
        for ok in ("git bugreport -o tests/out -s %x", "git format-patch -o tests/out --suffix=.txt -1",
                   "git diagnose -o tests/out -s a/b"):
            with self.subTest(ok):
                self.assertSilent(ok, AGENT_A)
        for refused in ("git bugreport -o tests/out -s /../../../docs/x", "git diagnose --output-directory=tests/out -s /../../../docs/x",
                        "git format-patch -o tests/out --suffix=/../../../docs/x -1",
                        "git -C docs bugreport -o %s/tests/out -s /../../../docs/x" % self.home.path):
            with self.subTest(refused):
                r = self.assertRefused(refused, "deliverables", AGENT_A)
                self.assertIn("docs/x", r.reason)

    def test_a_word_that_may_become_another_option_keeps_the_reading(self):
        """A glob or an expansion the hook cannot read may become a reset or an option that takes the next word as its
        value, so a line holding one before `--` is read as the default form, fail closed.  One whose option the line
        settles (`--subject-prefix=$P`) is read as spelled."""
        for command in ("git format-patch --stdout --no-std?ut -1", "git format-patch --stdout --subject-pre* -1",
                        "git bugreport -o tests/out --no-o*", "git bugreport -o tests/out --suf?ix=/../../../docs/x"):
            with self.subTest(command):
                self.assertRefused(command, "", AGENT_A)
        self.assertRefused("git format-patch --stdout $X -1", "Law 1", agent_id=None)
        self.assertSilent("git format-patch --stdout $X -1", agent_id=None, cwd=self.at(".claude", "patches"))
        self.assertSilent("git format-patch --stdout --subject-prefix=$P -1", AGENT_A)

    def test_config_the_line_sets_moves_the_directory_for_spud(self):
        """-c format.outputDirectory moves format-patch's default directory, and -o and --stdout win over it
        (git-format-patch(1): "The -o option takes precedence over format.outputDirectory"); format.suffix shapes the
        names as --suffix does.  A member sets no format.* key at all (Law 7's inert allowlist), so this is Spud's
        reading, and the one the prober could not run: the hook refuses a member the -c."""
        spec = ".claude/patches"
        for ok in ("git -c format.outputDirectory=%s format-patch -1" % spec,
                   "git -c format.outputdirectory=docs format-patch --stdout -1",
                   "git -c format.outputDirectory=docs format-patch -o %s -1" % spec,
                   "git -c format.outputDirectory=docs -c format.outputDirectory=%s format-patch -1" % spec):
            with self.subTest(ok):
                self.assertSilent(ok, agent_id=None)
        for refused in ("git -c format.outputDirectory=docs format-patch -1", "git -c FORMAT.OUTPUTDIRECTORY=docs format-patch -1",
                        "git -C %s -c format.outputDirectory=../../docs format-patch -1" % spec,
                        "git -c format.suffix=/../../../docs/x -C %s format-patch -1" % spec,
                        "git -c format.outputDirectory=%s format-patch -o '' -1" % spec):
            with self.subTest(refused):
                self.assertRefused(refused, "Law 1", agent_id=None)
        self.assertRefused("git --config-env=format.outputDirectory=D format-patch -1", "cannot resolve", agent_id=None)
        self.assertRefused("git -c format.outputDirectory=tests/out format-patch -1", "Law 7", AGENT_A)

    def test_inside_shell_strings_eval_and_subshells(self):
        for command in ("sh -c 'git format-patch -1'", "eval 'git bugreport'", "(git diagnose)", "true && git format-patch -1",
                        "for f in a; do git format-patch -1; done", "git status && git format-patch -1"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables", AGENT_A)

    def test_the_channel_records_the_file_git_names(self):
        cwds = frozenset([str(self.home.path)])
        self.assertEqual(self.analysis("git format-patch -1").git_writes,
                         [("format-patch's default form, into ./" + PICKED, PICKED, cwds)])
        self.assertEqual(self.analysis("git -C docs diagnose -s x/y").git_writes,
                         [("diagnose's default form, into ./docs/" + PICKED, "docs/" + PICKED, cwds),
                          ("diagnose's suffix x/y, into ./docs/git-diagnostics-x/y.zip", "docs/git-diagnostics-x/y.zip", cwds)])
        for writes_nothing in ("git format-patch --stdout -1", "git bugreport -h", "git diagnose -o tests/out"):
            with self.subTest(writes_nothing):  # nothing here: -o's own directory is GitDirectoryOptionTest's (SPD-227)
                self.assertEqual([w for w in self.analysis(writes_nothing).git_writes if "default form" in w[0]], [])

    def test_the_verbs_the_survey_read(self):
        """Every verb of GIT_MEMBER_VERBS read against its man page for "current (working) directory" and "temporary
        file": format-patch, bugreport and diagnose are the three that write there by default.  The others that write a
        file write only what the line names, or to standard output."""
        m = load_spud_module()
        self.assertEqual(sorted(m.GIT_VERB_CWD_WRITES), ["bugreport", "diagnose", "format-patch"])
        self.assertTrue(set(m.GIT_VERB_CWD_WRITES) <= m.GIT_MEMBER_VERBS)
        for ok in ("git archive HEAD", "git fast-export HEAD", "git shortlog -s", "git request-pull v1 url", "git log -1"):
            with self.subTest(ok):
                self.assertEqual(self.analysis(ok).git_writes, [])

    def test_each_verbs_option_table_is_gits_own(self):
        """Which words are options is read from the table, so a value option missing from it would let the word after it
        be read as --stdout or -o.  `git <verb> --help-all` lists every option (git 2.54.0 hides none of these three's),
        each with whether it takes a separate value; a git that adds one fails this, so the next reader reads it in."""
        m = load_spud_module()
        spec = re.compile(r"^\s+(?:-(\w), )?--(?:\[no-\])?([\w-]+)(?:\[=<[^>]*>\])?( [<(])?", re.M)
        for verb, entry in m.GIT_VERB_CWD_WRITES.items():
            with self.subTest(verb):
                usage = subprocess.run(["git", verb, "--help-all"], capture_output=True, text=True,
                                       stdin=subprocess.DEVNULL).stdout
                listed = {("--" + name, short, bool(value)) for short, name, value in spec.findall(usage)}
                self.assertGreater(len(listed), 2, usage)
                ours = {(long, short, kind not in ("flag", "stdout")) for long, short, kind in entry[1]}
                self.assertEqual(ours, listed)


AGENT_DIRS = "b8c9d0e1f2a3b4c5d"  # SPD-227: a member holding out/**, tests/* and patches/*.patch


class GitDirectoryOptionTest(BashHookCase):
    """SPD-227: what a git option names is read as git reads it, on two counts SPD-049's reading missed.

    The base: git runs every relative path it is given from the directory the composed -C chain names, so `git -C docs
    format-patch -o tests/out -1` wrote docs/tests/out/0001-*.patch while the hook read ./tests/out.  The shape: a
    directory option names the directory git writes files of its own naming into, so `cd tests/out && git format-patch
    -o . -1` passed a member holding tests/* (`.` is tests/out, which tests/* matches) while git wrote
    tests/out/0001-*.patch, and `-o out` was refused to a member holding out/** although every file lands under it.

    Probed on git 2.54.0 (Apple Git-157) in a scratch repository in the scratchpad, from its top, under `-C sub`, from
    sub, under `-C sub -C ..` and under `--git-dir=<repo>/.git -C sub`: archive -o, format-patch -o and --output, diff
    --output, bugreport -o, diagnose -o, mailsplit -o, mailinfo's two files, bundle create, index-pack -o and
    credential-store --file each wrote under the directory -C (or cd) left git in.  format-patch, bugreport and diagnose
    -o a/b/c made a/b/c and wrote 0001-second.patch, git-bugreport-<date>.txt and git-diagnostics-<date>.zip directly in
    it; mailsplit -oD wrote D/0001 and D/0002 into a D that must exist.  `index-pack -o o1.idx` wrote o1.idx and o1.rev;
    `repack --filter-to=zz/pk` wrote zz/pk-<hash>.idx/.pack/.rev and `--expire-to=xx/pk` the same with .mtimes, as
    `pack-objects o1pack` wrote o1pack-<hash>.*: a pack base name, not the directory the man page calls it;
    `commit-graph write --object-dir D` wrote D/info/commit-graph and `multi-pack-index --object-dir=D write`
    D/pack/multi-pack-index; `checkout-index -a --prefix=o1/` wrote o1/f and o1/sub/g and `--prefix=o1-` o1-f and
    o1-sub/g.  `-o~/t.tar` and `--output=~/u.tar` reached git as spelled and wrote ./~/t.tar and ./~/u.tar: no shell
    expands a tilde inside a word (tests/probes/shell_probe.py: zsh 5.9 -f, -f -o nobareglobqual and bash 3.2.57 printed
    `-o~/x` and `--output=~/x`, and `-o ~/x` as $HOME/x).

    AGENT_A plans tests/** and bin/spud, AGENT_C home:**, AGENT_DIRS out/**, tests/* and patches/*.patch; the home is
    no repository here, so the options git reads from the work tree's top are GitTopRelativeTest's."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.dirs = self.spawn(self.plan(actor=self.lead["ref"], persona="engineer", model="opus",
                                         deliverable=["home:out/**", "home:tests/*", "home:patches/*.patch"]), AGENT_DIRS, caller=AGENT_A)
        for d in ("docs/superpowers/specs", "tests/out", "out", "patches", ".claude/patches", "ledger/tickets"):
            (self.home.path / d).mkdir(parents=True, exist_ok=True)

    def analysis(self, command, cwd=None):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=cwd or str(self.home.path), home=str(self.home.path)))

    # -- the -C base ---------------------------------------------------------------------

    def test_minus_C_is_the_base_of_a_relative_option_target(self):
        home = self.home.path
        for refused, where in (("git -C docs format-patch -o tests/out -1", "docs/tests/out/?*"),  # the ticket's line
                               ("git -C docs archive -o tests/a.tar HEAD", "docs/tests/a.tar"),
                               ("git -C docs diff --output=tests/d.txt", "docs/tests/d.txt"),
                               ("git -C docs mailinfo tests/m tests/p", "docs/tests/m"),
                               ("git -C docs bugreport -o tests/out", "docs/tests/out/?*"),
                               ("git -C docs mailsplit -otests/out box", "docs/tests/out/?*"),
                               ("git -C tests -C ../docs archive -o a.tar HEAD", "docs/a.tar"),
                               ("cd tests && git -C ../docs diagnose -o out", "docs/out/?*")):
            with self.subTest(refused):
                r = self.assertRefused(refused, "deliverables", AGENT_A)
                self.assertIn(where, r.reason)
                self.assertIn("this git call writes", r.reason)
        for ok in ("git -C tests format-patch -o out -1", "git -C docs format-patch -o ../tests/out -1",
                   "git -C docs archive -o %s/tests/a.tar HEAD" % home, "git -C tests diff --output=d.txt",
                   "git -C tests mailinfo m p", "cd docs && git -C ../tests archive -o a.tar HEAD",
                   "git -C docs -C ../tests bugreport -o out"):
            with self.subTest(ok):
                self.assertSilent(ok, AGENT_A)
        # Spud's own paths under -C: .claude/patches is his, the home's root and ledger/ are not
        self.assertSilent("git -C .claude/patches archive -o a.tar HEAD", agent_id=None)
        self.assertRefused("git -C ledger archive -o tickets/SPD-002.md HEAD", "Law 1", agent_id=None)

    def test_a_tilde_is_the_home_only_where_the_shell_expands_it(self):
        """A word that starts with `~` is expanded by the shell (the house reading of a leading tilde), so a -C naming one
        starts the chain again rather than joining the one before it; a `~` inside a word reaches git as spelled."""
        cwds = frozenset([str(self.home.path)])
        a = self.analysis("git -C docs -C ~/x format-patch -1")
        self.assertEqual(a.git_writes, [("format-patch's default form, into ~/x/" + PICKED, "~/x/" + PICKED, cwds)])
        self.assertEqual(a.git_calls, [((("-C ~/x", "~/x"),), cwds)])
        self.assertEqual(self.analysis("git -C docs --git-dir ~/x status").git_calls,
                         [((("-C docs", "docs"), ("--git-dir ~/x", "~/x")), cwds)])
        self.assertEqual(self.analysis("git -C docs --git-dir=~/x status").git_calls,
                         [((("-C docs", "docs"), ("--git-dir=~/x", "docs/./~/x")), cwds)])
        self.assertEqual(self.analysis("git -C docs archive -o ~/t.tar HEAD").git_writes, [("archive -o ~/t.tar", "~/t.tar", cwds)])
        for attached, spelled in (("-o~/t.tar", "archive -o~/t.tar"), ("--output=~/t.tar", "archive --output=~/t.tar")):
            with self.subTest(attached):
                self.assertEqual(self.analysis("git archive %s HEAD" % attached).git_writes, [(spelled, "./~/t.tar", cwds)])
                self.assertEqual(self.analysis("git -C docs archive %s HEAD" % attached).git_writes, [(spelled, "docs/./~/t.tar", cwds)])
                self.assertSilent("git archive %s HEAD" % attached, AGENT_C)  # ./~ is in the home, which AGENT_C holds whole
                self.assertSilent("cd tests && git archive %s HEAD" % attached, AGENT_A)
        self.assertRefused("git -C tests -C ~/x format-patch -1", "", AGENT_C)  # the home directory, outside every project

    # -- a directory option holds the files git names -------------------------------------

    def test_a_directory_option_is_read_as_the_files_git_writes_in_it(self):
        for ok in ("git format-patch -o out -1", "git format-patch -oout -3", "git format-patch --output-directory=out -1",
                   "git format-patch --output-directory out -1", "git format-patch -o out/a/b -1", "git format-patch -o tests -1",
                   "git bugreport -o out", "git bugreport --output-directory out", "git bugreport --out=out", "git diagnose -o out",
                   "git diagnose --output-directory=out/x", "git mailsplit -oout box", "cd out && git format-patch -o . -1"):
            with self.subTest(ok):
                self.assertSilent(ok, AGENT_DIRS)
        for refused, where in (("cd tests/out && git format-patch -o . -1", "tests/out/?*"),  # the ticket's line
                               ("git format-patch -o tests/out -1", "tests/out/?*"), ("git bugreport -o tests/out", "tests/out/?*"),
                               ("git diagnose -o tests/out", "tests/out/?*"), ("git mailsplit -otests/out box", "tests/out/?*"),
                               ("git format-patch -o patches -1", "patches/?*"), ("git format-patch -o docs -1", "docs/?*")):
            with self.subTest(refused):
                r = self.assertRefused(refused, "deliverables", AGENT_DIRS)
                self.assertIn(where, r.reason)
        for ok in ("git format-patch -o tests/out -1", "cd tests/out && git format-patch -o . -1", "git mailsplit -otests/out box"):
            with self.subTest(ok):
                self.assertSilent(ok, AGENT_A)

    def test_the_channel_records_the_files_the_directory_holds(self):
        cwds = frozenset([str(self.home.path)])
        self.assertEqual(self.analysis("git format-patch -o out -1").git_writes, [("format-patch -o out", "out/" + PICKED, cwds)])
        self.assertEqual(self.analysis("git mailsplit -oout box").git_writes, [("mailsplit -oout", "out/" + PICKED, cwds)])
        self.assertIn(("bugreport -o out", "out/" + PICKED, cwds), self.analysis("git bugreport -o out -s x").git_writes)
        self.assertEqual(self.analysis("git archive -o out/a.tar HEAD").git_writes, [("archive -o out/a.tar", "out/a.tar", cwds)])
        self.assertEqual(self.analysis("git format-patch --output=out/all.patch -1").git_writes,
                         [("format-patch --output=out/all.patch", "out/all.patch", cwds)])
        self.assertEqual(self.analysis("git format-patch -o '' -1").git_writes,  # here: the default form's reading
                         [("format-patch's default form, into ./" + PICKED, PICKED, cwds)])

    def test_the_forms_of_spuds_own_verbs(self):
        """Law 7 refuses a member these verbs whole; the files Spud's own call writes are read in their own shapes: a
        pack base name, a tree under an object directory, a prefix before every path of the index, an index and its
        reverse index.  Each line flips an answer SPD-049's reading of one file gave."""
        for refused in ("git repack -a -d --filter-to=ledger/Home.md", "git repack --cruft -d --expire-to=ledger/Home.md",
                        "git pack-objects ledger/Home.md --revs", "git commit-graph write --object-dir ledger/Home.md",
                        "git multi-pack-index --object-dir=ledger/Home.md write", "git checkout-index -a --prefix=ledger/Home.md",
                        "git index-pack -o ledger/Home.idx p.pack"):
            with self.subTest(refused):
                self.assertRefused(refused, "Law 1", agent_id=None)
                self.assertRefused(refused, "Law 7", AGENT_C)
        for ok in ("git commit-graph write --object-dir .claude", "git multi-pack-index --object-dir=.claude write",
                   "git checkout-index -a --prefix=.claude/", "git repack -a -d --filter-to=.claude/pk", "git pack-objects .claude/pk"):
            with self.subTest(ok):
                self.assertSilent(ok, agent_id=None)
        cwds = frozenset([str(self.home.path)])
        self.assertEqual(self.analysis("git index-pack -o out/p.idx p.pack").git_writes,
                         [("index-pack -o out/p.idx", "out/p.idx", cwds), ("index-pack -o out/p.idx", "out/p.rev", cwds)])
        self.assertEqual(self.analysis("git pack-objects out/pk").git_writes, [("pack-objects out/pk", "out/pk-" + PICKED, cwds)])
        self.assertEqual(self.analysis("git commit-graph write --object-dir o").git_writes,
                         [("commit-graph --object-dir o", p, cwds) for p in
                          ("o/" + PICKED, "o/%s/%s" % (PICKED, PICKED), "o/%s/%s/%s" % (PICKED, PICKED, PICKED))])


    def test_config_files_file_in_a_write_form(self):
        """`git config --file` writes its file in a write form and reads it in a read form (probed on git 2.54.0: `-f c1
        a.b c`, `--file=c2`, `config set --file c3`, `-fc4` and `--fil=c8` wrote, `-f c5 --get a.b` did not, and `-C sub`
        wrote sub/c6).  Law 7 refuses a member every write form of config, so the file is Spud's to be held to Law 1."""
        for refused in ("git config -f ledger/tickets/SPD-002.md a.b c", "git config --file=ledger/tickets/SPD-002.md a.b c",
                        "git config set --file ledger/tickets/SPD-002.md a.b c", "git config -fledger/tickets/SPD-002.md a.b c",
                        "git -C ledger config -f tickets/SPD-002.md a.b c", "git config --fil=ledger/tickets/SPD-002.md a.b c"):
            with self.subTest(refused):
                self.assertRefused(refused, "Law 1", agent_id=None)
                self.assertRefused(refused, "Law 7", AGENT_C)
        for ok in ("git config -f ledger/tickets/SPD-002.md --get a.b", "git config --file=.claude/x a.b c",
                   "git config -f .gitmodules --get-regexp path", "git config get --file ledger/tickets/SPD-002.md a.b"):
            with self.subTest(ok):
                self.assertSilent(ok, agent_id=None)
        self.assertSilent("git config -f .gitmodules --get-regexp path", AGENT_A)


class GitTopRelativeTest(BashHookCase):
    """SPD-227's adjacent gap: from a subdirectory of the work tree git chdirs to its top before it reads some values,
    and those it does not rebase on the directory it started in, so they land at the top however the line reached the
    subdirectory.  Probed on git 2.54.0 (Apple Git-157) in a scratch repository in the scratchpad: from sub, sub/deep and
    under -C sub, `fast-export --export-marks=m` (a verb Law 7 lets a member run) wrote <top>/m, as `read-tree
    --index-output`, `pack-objects <base>`, `repack --filter-to`, `checkout-index --prefix` and `--object-dir` did and a
    relative GIT_INDEX_FILE did; `--work-tree=..` from sub wrote at the work tree, `--work-tree=sub` from the top wrote in
    the directory git ran in, and `--git-dir=../.git` from sub, or a run inside .git, wrote in the directory git ran in
    (no work tree to go to).  So such a value is read in the directory git runs in and at the top of the work tree git
    finds from there (the nearest directory holding a .git), and at a --work-tree the line names.

    The home is a repository here (a planted .git), as a checkout is; AGENT_A plans tests/** and bin/spud."""

    def setUp(self):
        super().setUp()
        plant_git_dir(self.home.path / ".git")
        for d in ("tests/out", ".claude", "ledger/tickets"):
            (self.home.path / d).mkdir(parents=True, exist_ok=True)

    def analysis(self, command, cwd=None):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=cwd or str(self.home.path), home=str(self.home.path)))

    def test_fast_exports_marks_land_at_the_top(self):
        for refused, where in (("cd tests && git fast-export --export-marks=m HEAD", "home:m "),
                               ("git -C tests fast-export --export-marks=m HEAD", "home:m "),
                               ("cd tests/out && git fast-export --export-marks m HEAD", "home:m "),
                               ("git --work-tree=tests fast-export --export-marks=m HEAD", "home:m "),
                               ("cd tests && GIT_INDEX_FILE=idx git status", "home:idx "),
                               ("git -C tests/out -c core.quotepath=off fast-export --export-marks=out/m HEAD", "home:out/m "),
                               ("cd tests && GIT_OBJECT_DIRECTORY=objects git status", "home:objects/?* ")):
            with self.subTest(refused):
                r = self.assertRefused(refused, "deliverables", AGENT_A)
                self.assertIn(where, r.reason)
        for ok in ("git fast-export --export-marks=tests/m HEAD", "cd tests && git fast-export --export-marks=tests/m HEAD",
                   "git -C tests fast-export --export-marks=%s/tests/m HEAD" % self.home.path,
                   "GIT_INDEX_FILE=tests/idx git status", "GIT_OBJECT_DIRECTORY=tests/objects git status"):
            with self.subTest(ok):
                self.assertSilent(ok, AGENT_A)

    def test_the_channel_reads_both_places(self):
        top = os.path.realpath(str(self.home.path))
        cwds = frozenset([str(self.home.path)])
        self.assertEqual(self.analysis("git -C tests fast-export --export-marks=m HEAD").git_writes,
                         [("fast-export --export-marks=m", "tests/m", cwds), ("fast-export --export-marks=m", top + "/m", cwds)])
        self.assertEqual(self.analysis("git --work-tree=tests read-tree --index-output=i HEAD").git_writes,
                         [("read-tree --index-output=i", p, cwds) for p in ("i", top + "/i", "tests/i")])
        self.assertEqual(self.analysis("git fast-export --export-marks=/tmp/m HEAD").git_writes,
                         [("fast-export --export-marks=/tmp/m", "/tmp/m", cwds)])

    def test_spud_is_held_to_law_1_at_the_top(self):
        self.assertRefused("cd .claude && git read-tree --index-output=idx HEAD", "Law 1", agent_id=None)
        self.assertRefused("cd .claude && git checkout-index -a --prefix=x/", "Law 1", agent_id=None)
        self.assertSilent("cd .claude && git read-tree --index-output=.claude/idx HEAD", agent_id=None)
        self.assertSilent("git read-tree --index-output=.claude/idx HEAD", agent_id=None)


class GitMailsplitTest(BashHookCase):
    """SPD-228: with no -o, `git mailsplit` writes into its last word, the older form GIT_VERB_FILE_OPTIONS never read.
    Probed on git 2.54.0 (Apple Git-157) in the scratchpad: `mailsplit box o2` wrote o2/0001 and o2/0002, and so did
    `mailsplit o3` with the mailbox on standard input -- one word is the directory, not a mailbox (an empty input wrote
    nothing, which is what the ticket saw); `mailsplit -- box o5` and `-b -d3 box o6` (o6/001) the same; options end at the
    first word that is not one, so `mailsplit box -oo4` wrote into ./-oo4; with -o the words are mailboxes (`-oo1 box o2`
    read o2 as one); three words and none printed usage (129); a spaced `-o o1` died "unknown option: -o"; and every
    form needs the directory to exist ("unable to create 'missing/0001'").  Under `-C sub`, `-om1 <box>` and `../../box
    m2` wrote sub/m1/0001 and sub/m2/0001, and `mailsplit m3` run from sub wrote sub/m3/0001: the directory is read
    where every other relative path git is given is.  AGENT_A plans tests/** and bin/spud."""

    def setUp(self):
        super().setUp()
        for d in ("tests/out", "docs", "ledger/tickets"):
            (self.home.path / d).mkdir(parents=True, exist_ok=True)

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def test_the_older_form_writes_into_its_last_word(self):
        for refused in ("git mailsplit box docs", "git mailsplit docs < box", "cat box | git mailsplit docs",
                        "git mailsplit -b -d3 box docs", "git mailsplit -- box docs", "git mailsplit --keep-cr box docs",
                        "git -C docs mailsplit box out", "cd docs && git mailsplit box out", "git mailsplit box -odocs"):
            with self.subTest(refused):
                r = self.assertRefused(refused, "deliverables", AGENT_A)
                self.assertIn("home:docs/out/?*" if refused.endswith(" out") else "home:docs/?*", r.reason)
        for ok in ("git mailsplit box tests/out", "git mailsplit tests/out < box", "git mailsplit -otests/out box docs",
                   "git mailsplit -otests/out", "git mailsplit box docs tests/out", "git mailsplit", "cd tests && git mailsplit box out"):
            with self.subTest(ok):
                self.assertSilent(ok, AGENT_A)
        self.assertRefused("git mailsplit box ledger/tickets", "Law 1", agent_id=None)

    def test_the_channel_records_the_directory(self):
        cwds = frozenset([str(self.home.path)])
        self.assertEqual(self.analysis("git mailsplit box out2").git_writes, [("mailsplit box out2", "out2/" + PICKED, cwds)])
        self.assertEqual(self.analysis("git mailsplit out2").git_writes, [("mailsplit out2", "out2/" + PICKED, cwds)])
        self.assertEqual(self.analysis("git mailsplit box -oo4").git_writes,
                         [("mailsplit -oo4", "o4/" + PICKED, cwds), ("mailsplit box -oo4", "-oo4/" + PICKED, cwds)])
        for nothing in ("git mailsplit", "git mailsplit a b c", "git mailsplit -oout a b"):
            with self.subTest(nothing):
                self.assertEqual([w for w in self.analysis(nothing).git_writes if not w[1].startswith("out/")], [])


GIT_INPUT_WORDING = "end git's own words with `--`"  # SPD-230: a git call whose words xargs extends


class GitXargsTest(BashHookCase):
    """SPD-230: xargs hands git words the line does not spell -- appended after the spelled ones, or where -I or -J puts
    them -- and the git reading read only the spelled ones: `cat list | xargs git archive HEAD` wrote whatever -o the list
    held, `xargs git format-patch --stdout -1` wrote into the directory under a --no-stdout from the list, and `echo push |
    xargs git` pushed past Law 7 (its verb was None).  git takes such a word as its verb or an option wherever it stands
    before `--`, so a member is refused the call unless the line ends git's words with `--` (every word xargs adds is then
    a path, however xargs splits its input: the `--` is in each call), and a word git takes as a file -- mailinfo's,
    mailsplit's last, a spaced -o's value -- is a write the line does not spell.  The subcommand verbs read by their
    operands (branch, tag, config, stash, worktree, remote, reflog) are refused whatever `--` does, since a name or a
    subcommand after it still writes.  find's {} is a path under its starting points and stays one, except as the verb.
    Spud is not refused (Law 7 is his to keep), but the default form an input may reset is still read for him (Law 1)."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        for d in ("tests/out", ".claude/patches", "push"):
            (self.home.path / d).mkdir(parents=True, exist_ok=True)
        (self.home.path / "list").write_text("a\n", encoding="utf-8")

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def refused_to_members(self, command, needle=GIT_INPUT_WORDING):
        for agent_id in (AGENT_A, AGENT_C):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertRefused(command, needle, agent_id)

    def test_a_word_xargs_adds_where_git_reads_an_option_or_its_verb_is_refused(self):
        for command in ("xargs git archive HEAD < list", "cat list | xargs git archive HEAD", "xargs git format-patch --stdout -1 < list",
                        "echo push | xargs git", "xargs git < list", "xargs git log < list", "xargs -0 git grep foo < list",
                        "xargs git fetch < list", "xargs -n1 git log --oneline < list", "xargs -I% git log -1 % < list",
                        "xargs -J % git log % -1 < list", "xargs -I% git % status < list", "xargs -I% git log -o % -1 < list",
                        "xargs git -C tests log < list", "xargs git branch --list < list", "xargs git remote < list",
                        "xargs git reflog < list"):
            self.refused_to_members(command)
        # `--` makes nothing a path for these, whose operand writes: Law 7's own reading of the verb comes first
        for command in ("xargs git branch -- < list", "xargs git config -- < list", "xargs git tag < list"):
            self.refused_to_members(command, "Law 7")
        r = self.assertRefused("xargs git archive HEAD < list", GIT_INPUT_WORDING)
        self.assertIn("git archive HEAD {input}", r.reason)
        self.refused_to_members("find push -maxdepth 0 -exec git {} \\;", "")  # the verb is a path find found

    def test_a_line_that_ends_gits_words_is_read_as_before(self):
        for ok in ("xargs git log -- < list", "xargs git archive HEAD -- < list", "xargs git format-patch --stdout -1 -- < list",
                   "xargs -I% git log -1 -- % < list", "xargs -I% git log --author=% -1 < list", "xargs git grep -l foo -- < list",
                   "find . -name '*.py' -exec git log -1 {} \\;", "xargs -n1 git log --oneline -- < list"):
            with self.subTest(ok):
                self.assertSilent(ok, AGENT_A)

    def test_a_file_git_writes_from_the_input_is_the_inputs(self):
        for command in ("xargs -I% git archive -o % HEAD < list", "xargs -J % git archive -o % HEAD < list",
                        "xargs git mailinfo -- < list", "xargs git mailsplit -- < list", "xargs -I% git mailinfo % tests/p < list"):
            with self.subTest(command):
                self.assertRefused(command, "xargs reads from its input", AGENT_A)

    def test_law_7s_own_reason_comes_first(self):
        for command, verb in (("xargs git push < list", "git push"), ("xargs git add < list", "git add"),
                              ("xargs git stash < list", "git stash")):
            with self.subTest(command):
                r = self.assertRefused(command, "Law 7", AGENT_A)
                self.assertIn(verb, r.reason)

    def test_spuds_answers(self):
        for ok in ("xargs git log < list", "xargs git archive HEAD < list", "xargs git push < list", "echo push | xargs git",
                   "xargs git format-patch --stdout -1 -- < list"):
            with self.subTest(ok):
                self.assertSilent(ok, agent_id=None)
        # an input word may be --no-stdout, which sends the patches into the directory git runs in: Law 1 there
        self.assertRefused("xargs git format-patch --stdout -1 < list", "Law 1", agent_id=None)
        self.assertSilent("cd .claude/patches && xargs git format-patch --stdout -1 < list", agent_id=None)

    def test_the_analysis(self):
        a = self.analysis("xargs git archive HEAD < list")
        self.assertIn("git-input", [f[0] for f in a.findings])
        self.assertEqual(a.git_writes, [])
        self.assertNotIn("git-input", [f[0] for f in self.analysis("xargs git archive HEAD -- < list").findings])
        self.assertNotIn("git-input", [f[0] for f in self.analysis("git archive HEAD").findings])


# The verbs `git --list-cmds=main` gives on this machine that a member may run, as SPD-087's sweep read them: every name
# whose refusal is None for both git_refused and git_not_allowed with no argument on the line.  `stash` and `worktree`
# are missing because their bare forms write (`git stash` stashes, `git worktree` prints usage but its subcommands add
# and remove), so git_refused refuses them with no subcommand; `branch`, `tag`, `config`, `remote` and `reflog` list.
SPD_087_ALLOWED = (
    "annotate", "archive", "blame", "branch", "bugreport", "cat-file", "check-attr", "check-ignore", "check-mailmap",
    "check-ref-format", "cherry", "column", "config", "count-objects", "describe", "diagnose", "diff", "diff-files",
    "diff-index", "diff-pairs", "diff-tree", "fast-export", "fetch", "fmt-merge-msg", "for-each-ref", "format-patch",
    "get-tar-commit-id", "grep", "help", "last-modified", "log", "ls-files", "ls-remote", "ls-tree", "mailinfo",
    "mailsplit", "merge-base", "name-rev", "pack-redundant", "patch-id", "pickaxe", "range-diff", "reflog", "remote",
    "repo", "request-pull", "rev-list", "rev-parse", "shortlog", "show", "show-branch", "show-index", "show-ref",
    "status", "stripspace", "tag", "var", "verify-commit", "verify-pack", "verify-tag", "version", "whatchanged",
)


class GitVerbAllowlistTest(BashHookCase):
    """SPD-087: Law 7's table named 31 verbs and git answers to about 170 (`git --list-cmds=main`: 174 on git 2.54.0,
    Apple Git-157), so the rest ran silent for a member.  `git stage` is git's own spelling of `git add` (`git stage -h`
    prints "usage: git add [<options>] [--] <pathspec>...") and `git init-db` of `git init` ("usage: git init [-q |
    --quiet] [--bare] ..."), and the plumbing writes the index, the object database and the working tree under two dozen
    more -- read-tree, update-index, write-tree, commit-tree, hash-object -w, checkout-index, repack, pack-refs,
    fast-import, unpack-objects, index-pack, mktag, mktree, sparse-checkout, maintenance, subtree, send-pack,
    receive-pack, update-server-info and their kin.  So the reading is inverted (the ticket's shape (b)):
    syntax.GIT_MEMBER_VERBS names the verbs a member runs, git_verbs.git_not_allowed refuses every other name git knows,
    and a writer a later git adds is refused before anyone has read it.

    The sweep read every name of `git --list-cmds=main` with `git <name> -h` and with its line in `git help -a`, whose
    own groups are git's reading of the same question -- "Low-level Commands / Manipulators" against "/ Interrogators",
    "Syncing Repositories", "Internal Helpers" -- and agree with this one everywhere but two: `git for-each-repo
    --config=<key> -- <arguments>` runs a git command of its own in every repository the key names, and `git unpack-file
    <blob>` "Creates a temporary file with a blob's contents" in the current directory, which no option names, so both
    are refused though git groups them as interrogators.  The synonyms the usage lines gave up: stage (add), init-db
    (init), fsck-objects (fsck), pickaxe and annotate (blame).

    The three refusals keep their own reasons, in this order: Law 7's own table (GIT_WRITE_VERBS, the verbs a member
    would reach for, refused whatever git's command list says), SPD-047's unknown verb (a name git does not know, which
    must be an alias or an external `git-<verb>`), then this allowlist.  AGENT_A plans tests/** and bin/spud; AGENT_C
    plans **; Law 7 binds neither Spud nor a plain session."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)  # Law 7 does not bind Spud
        return r

    def finding(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path))).findings

    def test_gits_own_spellings_of_add_and_init(self):
        for command in ("git stage .", "git stage -p", "git stage -- f", "git init-db x", "git init-db --bare",
                        "git -C . stage .", "sh -c 'git stage .'", "env git init-db x"):
            with self.subTest(command):
                r = self.refused_for_members(command)
                self.assertIn("Spud commits", r.reason)
        self.assertEqual(self.finding("git stage ."), [("git", ("stage", "stage"))])

    def test_the_plumbing_that_writes_the_repository(self):
        for verb in ("checkout-index -a -f", "read-tree HEAD", "update-index --refresh", "write-tree", "commit-tree",
                     "hash-object -w -", "repack -ad", "pack-refs --all", "fast-import", "unpack-objects", "index-pack",
                     "sparse-checkout set x", "maintenance run", "subtree add --prefix=p c", "send-pack host:r",
                     "receive-pack .", "update-server-info", "mktag", "mktree", "prune-packed", "unpack-file abc",
                     "merge-file a o b", "backfill", "history reword HEAD", "replay --onto x y", "fetch-pack host:r",
                     "http-fetch url", "http-push r", "quiltimport", "pack-objects --stdout --revs", "apply x.patch"):
            with self.subTest(verb):
                r = self.refused_for_members("git " + verb)
                self.assertIn("git " + verb.split()[0], r.reason)

    def test_the_verbs_that_run_a_program_or_serve_the_repository(self):
        for verb in ("for-each-repo --config=x -- push", "hook run pre-commit", "merge-index cmd -a", "merge-one-file",
                     "merge-octopus", "merge-ours", "merge-recursive a", "merge-resolve", "merge-subtree a b",
                     "p4 clone //depot", "shell -c x", "daemon --export-all", "http-backend", "upload-pack .",
                     "upload-archive .", "upload-archive--writer .", "remote-ext r url", "remote-fd r url",
                     "remote-http r url", "remote-https r url", "remote-ftp r url", "remote-ftps r url", "imap-send",
                     "send-email p", "web--browse u", "difftool A B", "submodule--helper list", "checkout--worker",
                     "difftool--helper", "fsmonitor--daemon start", "credential fill", "credential-cache exit",
                     "credential-cache--daemon s", "credential-osxkeychain get", "credential-store store",
                     "gui--askpass x", "gui--askyesno x", "sh-i18n--envsubst"):
            with self.subTest(verb):
                r = self.refused_for_members("git " + verb)
                self.assertIn("git " + verb.split()[0], r.reason)

    def test_a_verb_whose_reading_form_is_a_subcommand_or_a_flag_is_refused_whole(self):
        # The verb is what Law 7 reads, and only the seven of git_refused's own cases are read by subcommand; every
        # other name whose read and write forms are told apart that way is refused whole, read form included.
        for verb in ("bundle verify f", "bundle list-heads f", "bundle unbundle f", "commit-graph verify",
                     "commit-graph write", "multi-pack-index verify", "refs verify", "refs list", "refs migrate",
                     "rerere status", "rerere diff", "rerere clear", "sparse-checkout list", "hash-object f",
                     "credential-store get", "fsck", "fsck-objects", "merge-tree a b",
                     "interpret-trailers f", "interpret-trailers --in-place f", "maintenance unregister"):
            with self.subTest(verb):
                self.refused_for_members("git " + verb)

    def test_the_read_verbs_of_the_sweep_stay_silent(self):
        for ok in ("git annotate f", "git pickaxe f", "git cherry", "git column", "git stripspace", "git patch-id",
                   "git get-tar-commit-id", "git check-attr -a f", "git check-mailmap x", "git check-ref-format x",
                   "git check-ref-format --branch x", "git diff-pairs -z", "git last-modified", "git repo info",
                   "git repo structure", "git request-pull v1 url", "git show-branch", "git show-branch -a",
                   "git show-index", "git verify-pack -v p.idx", "git verify-commit HEAD", "git fmt-merge-msg",
                   "git pack-redundant --all", "git ls-tree HEAD", "git diff-files", "git diff-index HEAD",
                   "git name-rev --all", "git merge-base a b", "git cat-file -p HEAD", "git var -l",
                   # --stdout: with no -o format-patch writes where it runs, which GitCwdWriteTest reads (SPD-093); and
                   # `git mailsplit mbox` writes into ./mbox/, its one word the directory (SPD-228, GitMailsplitTest)
                   "git format-patch --stdout -1", "git archive HEAD", "git mailsplit -o/tmp/spd-228 mbox",
                   "git fast-export HEAD"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_every_name_git_lists_is_read_one_way_or_the_other(self):
        """The sweep as data.  A git that lists a name neither table holds refuses it, which is the point of the
        allowlist; this fails so the next reader sweeps the new name rather than leaving a member refused a read verb."""
        m = load_spud_module()
        names = subprocess.run(["git", "--list-cmds=main"], capture_output=True, text=True, check=True).stdout.split()
        self.assertGreater(len(names), 100, names)
        allowed = [n for n in names if m.git_refused(n, []) is None and m.git_not_allowed(n) is None]
        self.assertEqual(allowed, sorted(SPD_087_ALLOWED), "git's command list changed: re-read the new names")
        self.assertEqual(sorted(m.GIT_MEMBER_VERBS - set(names)), [])  # no name in the table that git does not have
        self.assertEqual(sorted(m.GIT_MEMBER_VERBS & m.GIT_WRITE_VERBS), [])  # and none in both tables

    def test_a_writer_a_later_git_adds_is_refused(self):
        # git's own command list is the only thing that says a verb exists, so a later git is simulated by a longer one.
        m = load_spud_module()
        commands = frozenset({"status", "push", "stash", "graft-tree"})
        with mock.patch("spudlib.shell.git_verbs.git_own_commands", lambda home=None: commands):
            self.assertEqual(self.finding("git graft-tree HEAD"), [("git", ("graft-tree", "graft-tree"))])
            self.assertEqual(self.finding("git status"), [("git", ("status", None))])
            self.assertEqual(self.finding("git push"), [("git", ("push", "push"))])
        self.assertEqual(m.git_not_allowed("graft-tree"), "graft-tree")

    def test_a_glob_that_can_expand_to_a_refused_verb_is_read_as_it(self):
        # `stage` is one of Law 7's own, so GLOB_SAMPLES holds it and the glob is read as the verb itself.  A name the
        # allowlist refuses needs no sample: the reading as spelled is not one of git's commands either, which is
        # SPD-047's refusal, so the glob is closed whatever it could expand to.  `read-tree` is sampled since SPD-089 (it
        # names the file its --index-output writes), so its glob is read as the verb, and Law 7 names it.
        r = self.refused_for_members("git stag?")
        self.assertIn("git stage", r.reason)
        r = self.refused_for_members("git read-tre?")
        self.assertIn("git read-tree", r.reason)
        r = self.refused_for_members("git ls-tre?", "not one of git's own commands")
        self.assertIn("ls-tre?", r.reason)
        for command in ("git st?ge .", "git sta*", "git ini?-db x", "git updat?-index --refresh", "git *tree"):
            with self.subTest(command):
                self.refused_for_members(command)

    def test_law_7s_own_table_keeps_its_reason_when_git_cannot_be_read(self):
        # The hook fails closed either way; which reason a member gets is what the two tables decide.
        path = self.home.env.get("PATH")
        self.home.env["PATH"] = str(self.home.path / "no-git-here")
        try:
            r = self.assertRefused("git stage .", "Law 7")
            self.assertIn("Spud commits", r.reason)
            self.assertNotIn("--list-cmds=main", r.reason)
            r = self.assertRefused("git read-tree HEAD", "Law 7")  # the allowlist defers to SPD-047's reason here
            self.assertIn("--list-cmds=main", r.reason)
            self.assertSilent("git read-tree HEAD", agent_id=None)
        finally:
            if path is None:
                self.home.env.pop("PATH", None)
            else:
                self.home.env["PATH"] = path
        self.assertRefused("git read-tree HEAD", "Law 7")


if __name__ == "__main__":
    unittest.main()
