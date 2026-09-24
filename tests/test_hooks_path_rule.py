"""PreToolUse for the edit tools, and the path rule both hooks share: deliverables, generated roots, worktrees elsewhere,
path aliases, the state directory, paths outside every project, deliverable globs."""

import importlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

from helpers import EXIT_ERROR, git, load_spud_module, SpudTestCase
from hookcase import AGENT_A, AGENT_B, AGENT_C, AGENT_D, DB_WORDING, OUTSIDE, SESSION, STATE, case_insensitive_fs, HookCase
from hookcase import quote_split


# =============================================================================
# PreToolUse / Write|Edit|MultiEdit|NotebookEdit
# =============================================================================


class PathRuleAsserts:
    """A Write (or another edit tool) to a path, and what the path rule answers."""

    def edit(self, path, agent_id=AGENT_A, tool="Write"):
        return self.home.hook("PreToolUse", self.pre_edit(path, agent_id=agent_id, tool=tool))

    def assertRefused(self, path, needle, agent_id=AGENT_A, tool="Write"):
        r = self.edit(path, agent_id, tool)
        self.assertEqual((r.code, r.decision), (0, "deny"), (str(path), r))
        self.assertIn(needle, r.reason, (str(path), r.reason))
        return r

    def assertSilent(self, path, agent_id=AGENT_A, tool="Write"):
        r = self.edit(path, agent_id, tool)
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""), (str(path), r))


FIRMLINK = "/System/Volumes/Data"  # macOS: the Data volume's own mount path; /Users, /private ... are firmlinks into it


def same_directory(a, b):
    try:
        return os.path.samefile(str(a), str(b))
    except OSError:
        return False


def mixed_case(text):
    return "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(text))


class PathAliasAsserts(PathRuleAsserts):
    """SPD-029: a project root (the home, a worktree) spelled another way the filesystem honours is the same root, so the
    path rule answers there as it does at the root: through the edit tools and through a shell redirection, for a member
    whose deliverables are tests/** and bin/spud (self.lead, AGENT_A) and for Spud."""

    def alias_or_skip(self, root, spelled, what):
        if spelled == str(root) or not same_directory(root, spelled):
            self.skipTest("%s: this filesystem does not treat %s as the directory %s" % (what, spelled, root))
        return spelled

    def assertBashRefused(self, command, needle, agent_id=AGENT_A):
        r = self.home.hook("PreToolUse", self.pre_bash(command, agent_id=agent_id))
        self.assertEqual((r.code, r.decision), (0, "deny"), (command, r))
        self.assertIn(needle, r.reason, (command, r.reason))

    def assertBashSilent(self, command, agent_id=AGENT_A):
        r = self.home.hook("PreToolUse", self.pre_bash(command, agent_id=agent_id))
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""), (command, r))

    def assertRootHolds(self, spelled):
        a = str(spelled)
        self.assertRefused("%s/ledger/tickets/SPD-001.md" % a, "generated")
        self.assertRefused("%s/ledger/tickets/SPD-001.md" % a, "generated", agent_id=None)
        self.assertRefused("%s/CLAUDE.md" % a, "deliverables")
        self.assertRefused("%s/bin/spud" % a, "Law 1", agent_id=None)
        self.assertSilent("%s/tests/x.py" % a)
        self.assertSilent("%s/CLAUDE.md" % a, agent_id=None)
        self.assertBashRefused("echo x > %s/ledger/tickets/SPD-001.md" % a, "generated")
        self.assertBashRefused("printf x | tee %s/reports/2026-09-13.md" % a, "generated", agent_id=None)
        self.assertBashRefused("cd %s && echo x > ledger/x.md" % a, "generated")
        self.assertBashRefused("echo x > %s/bin/spud" % a, "Law 1", agent_id=None)
        self.assertBashSilent("echo x > %s/tests/out.txt" % a)


class StateDirAsserts(PathAliasAsserts):
    """SPD-031: a path in the state directory is refused to the edit tools and to a shell redirection for every actor, in the Bash
    hook's database wording (never Law 1's or Law 5's), whatever the deliverable globs."""

    def assertStateRefused(self, path, agent_id, tool="Write"):
        r = self.assertRefused(path, DB_WORDING, agent_id=agent_id, tool=tool)
        self.assertNotIn("Law", r.reason, (str(path), r.reason))
        return r

    def assertStateBashRefused(self, command, agent_id):
        r = self.home.hook("PreToolUse", self.pre_bash(command, agent_id=agent_id))
        self.assertEqual((r.code, r.decision), (0, "deny"), (command, r))
        self.assertIn(DB_WORDING, r.reason, (command, r.reason))
        self.assertIn("redirection or tee", r.reason, (command, r.reason))  # the path rule refused it, not the raw-text regex
        self.assertNotIn("Law", r.reason, (command, r.reason))
        return r

    def assertStateHolds(self, target, agents=(AGENT_A, None)):
        for agent_id in agents:
            self.assertStateRefused(target, agent_id)
            self.assertStateBashRefused("echo x > %s" % quote_split(target), agent_id)


class PreEditTest(PathRuleAsserts, HookCase):
    in_process = True  # SPD-231: the edit hook in this process, against the class's home

    def setUp(self):
        super().setUp()
        self.lead = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:tests/**", "home:bin/spud", "home:docs/x/*.md", "home:notes/"]), AGENT_A)
        self.wt = self.home.path / ".claude" / "worktrees" / "spd-099-thing"
        self.wt.mkdir(parents=True)

    def test_member_paths_follow_the_deliverable_globs(self):
        home = self.home.path
        for ok in ("tests/test_x.py", "tests/probes/deep/x.jsonl", "bin/spud", "docs/x/a.md", "notes/a/b.txt"):
            for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                self.assertSilent(home / ok, tool=tool)
        for bad in ("bin/other", "docs/x/sub/a.md", "docs/y.md", "CLAUDE.md", "spud.config.json", ".claude/settings.json", "tests"):
            self.assertRefused(home / bad, "deliverables")

    # SPD-233: what the filesystem makes of a path's case is the machine's, so each half is a test of its own that skips,
    # saying so, on the other kind of filesystem -- where an `if` once dropped it without a word.
    def needs_filesystem(self, folds):
        if case_insensitive_fs(self.home.path) != folds:
            self.skipTest("needs a case-%s filesystem at %s" % ("insensitive" if folds else "sensitive", self.home.path))

    def test_a_deliverable_in_another_case_is_the_same_file_where_the_filesystem_folds_case(self):
        self.needs_filesystem(folds=True)
        self.assertSilent(self.home.path / "Tests" / "x.py")  # tests/x.py

    def test_a_deliverable_in_another_case_is_another_file_where_the_filesystem_keeps_case(self):
        self.needs_filesystem(folds=False)
        self.assertRefused(self.home.path / "Tests" / "x.py", "deliverables")

    def test_a_bracketed_segment_is_a_directory_the_hook_lets_its_member_write(self):
        """SPD-086, end to end: BAD-054/Snowden was refused admin/src/app/accounts/[email]/page.tsx -- its own planned
        deliverable, quoted back at it in the refusal -- because `[` opened a character class, so the glob matched only a
        one-letter directory.  The same plan and the same writes, through `member new` and the PreToolUse edit hook."""
        home = self.home.path
        m = self.spawn(self.plan(deliverable=["home:admin/src/app/accounts/[email]/**", "home:app/[...slug]/page.tsx"]), AGENT_B)
        self.assertEqual(m["deliverables"], ["home:admin/src/app/accounts/[email]/**", "home:app/[...slug]/page.tsx"])
        for ok in ("admin/src/app/accounts/[email]/page.tsx", "admin/src/app/accounts/[email]/_components/UserTab.tsx",
                   "app/[...slug]/page.tsx"):
            for tool in ("Write", "Edit", "MultiEdit"):
                self.assertSilent(home / ok, agent_id=AGENT_B, tool=tool)
        r = self.assertRefused(home / "admin" / "src" / "app" / "accounts" / "e" / "page.tsx", "deliverables", agent_id=AGENT_B)
        self.assertIn("[email]", r.reason)  # the class it used to be is the only thing that ever matched this path
        self.assertRefused(home / "admin" / "src" / "app" / "accounts" / "page.tsx", "deliverables", agent_id=AGENT_B)
        self.assertRefused(home / "app" / "x" / "page.tsx", "deliverables", agent_id=AGENT_B)
        self.assertRefused(home / "admin" / "src" / "app" / "accounts" / "[email]" / "page.tsx", "deliverables")  # not the lead's

    def test_case_variants_cannot_reach_generated_files_or_spuds_set(self):
        """Rooster's HIGH-2: the protected roots are matched whatever the case, on every filesystem."""
        home = self.home.path
        wide = self.spawn(self.plan(deliverable=["home:**"]), AGENT_B)
        for bad in ("Ledger/tickets/SPD-001.md", "LEDGER/x.md", "Reports/x.md", "Ledger/Home.md", "ledger/Tickets/SPD-001.md"):
            self.assertRefused(home / bad, "generated", agent_id=AGENT_B)
        self.assertSilent(home / "docs" / "x.md", agent_id=AGENT_B)
        self.assertSilent(home / "CLAUDE.md", agent_id=AGENT_B)  # a ** member may write CLAUDE.md: Law 1 binds Spud, not members
        self.assertEqual(wide["deliverables"], ["home:**"])

    def test_spuds_own_ledger_files_in_another_case_where_the_filesystem_folds_case(self):
        self.needs_filesystem(folds=True)
        home = self.home.path
        self.assertSilent(home / "Ledger" / "Home.md", agent_id=None)  # the same file as Spud's ledger/Home.md
        self.assertRefused(home / "Ledger" / "Tickets" / "x.md", "generated", agent_id=None)

    def test_every_edit_tool_is_covered_and_denials_are_recorded(self):
        home = self.home.path
        self.assertRefused(home / "bin" / "other", "deliverables")
        for tool in ("Edit", "MultiEdit", "NotebookEdit"):
            self.assertRefused(home / "CLAUDE.md", "deliverables", tool=tool)
        d = self.denied()
        self.assertEqual(d[0]["member"], self.lead["ref"])
        self.assertEqual(d[0]["data"]["tool_name"], "Write")
        self.assertEqual(d[0]["data"]["path"], "bin/other")

    def test_ledger_and_reports_are_generated(self):
        home = self.home.path
        for agent_id in (AGENT_A, None):
            for bad in ("ledger/tickets/SPD-001.md", "ledger/teams/SPUD-001/X.md", "reports/2026-09-12.md", "ledger/x.md"):
                self.assertRefused(home / bad, "generated", agent_id=agent_id)
        for ok in ("ledger/Home.md", "ledger/Spud.md", "ledger/Board.base", "ledger/Fleet.base", "ledger/_templates/ticket.md"):
            self.assertSilent(home / ok, agent_id=None)
            self.assertRefused(home / ok, "deliverables", agent_id=AGENT_A)

    def test_spuds_hand_written_set(self):
        home = self.home.path
        for ok in ("spud.config.json", "CLAUDE.md", ".claude/settings.json", ".claude/agents/spudagent.md", "docs/superpowers/specs/2026-09-12-x.md"):
            self.assertSilent(home / ok, agent_id=None)
        for bad in ("bin/spud", "tests/test_x.py", "docs/spikes/x.md", "README.md"):
            self.assertRefused(home / bad, "Law 1", agent_id=None)

    def test_spuds_own_file_in_another_case_is_his_where_the_filesystem_folds_case(self):
        self.needs_filesystem(folds=True)
        self.assertSilent(self.home.path / "claude.md", agent_id=None)  # the same file as CLAUDE.md here

    def test_spuds_own_file_in_another_case_is_another_file_where_the_filesystem_keeps_case(self):
        self.needs_filesystem(folds=False)
        self.assertRefused(self.home.path / "claude.md", "Law 1", agent_id=None)

    def test_outside_every_project_root_is_spuds_and_the_scratchpad_is_the_members(self):
        """SPD-064: a path outside every registered project was refused to nobody; only the scratchpad and the system temp
        directories stay open to a member now (OutsideProjectTest), and Spud writes out there as he always did."""
        for p in ("/tmp/claude-501/x/scratchpad/notes.md", str(self.home.path.parent / "elsewhere.md"),
                  str(self.home.path) + "-sibling/x.md"):  # the suite's homes live under tempfile, an open root
            self.assertSilent(p)
            self.assertSilent(p, agent_id=None)
        for p in ("/Users/Someone/.claude/projects/-Users-Someone-Personal-Spud/memory/x.md", "/etc/hosts"):
            self.assertSilent(p, agent_id=None)
            self.assertRefused(p, "outside every registered project")

    def test_worktree_paths_map_to_the_repository(self):
        self.assertSilent(self.wt / "tests" / "x.py")
        self.assertSilent(self.wt / "bin" / "spud")
        self.assertRefused(self.wt / "docs" / "spikes" / "x.md", "deliverables")
        self.assertRefused(self.wt / "ledger" / "tickets" / "SPD-001.md", "generated")
        self.assertRefused(self.wt / "bin" / "spud", "Law 1", agent_id=None)
        self.assertSilent(self.wt / ".claude" / "settings.json", agent_id=None)
        self.assertSilent(self.wt / "CLAUDE.md", agent_id=None)

    def test_dot_dot_and_symlinks_cannot_escape(self):
        home = self.home.path
        self.assertRefused(str(home / "tests" / ".." / "CLAUDE.md"), "deliverables")
        self.assertRefused(str(home / "tests" / ".." / ".." / (home.name) / "bin" / "other"), "deliverables")
        (home / "tests").mkdir(exist_ok=True)
        (home / "tests" / "link").symlink_to(home / "CLAUDE.md")
        self.assertRefused(home / "tests" / "link", "deliverables")
        (home / "tests" / "dirlink").symlink_to(home / "ledger")
        self.assertRefused(home / "tests" / "dirlink" / "Home.md", "generated")
        outside = home.parent / ("%s-link-%d" % (home.name, os.getpid()))
        outside.symlink_to(home / "ledger" / "tickets")
        self.addCleanup(outside.unlink)
        self.assertRefused(outside / "SPD-001.md", "generated")
        self.assertRefused(outside / "SPD-001.md", "generated", agent_id=None)
        (home / "tests" / "outlink").symlink_to("/tmp")
        self.assertSilent(home / "tests" / "outlink" / "x.txt")

    def test_relative_paths_resolve_against_cwd(self):
        p = self.pre_edit("CLAUDE.md", agent_id=AGENT_A)
        p["cwd"] = str(self.home.path)
        r = self.home.hook("PreToolUse", p)
        self.assertEqual(r.decision, "deny")
        p = self.pre_edit("tests/x.py", agent_id=AGENT_A)
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.stdout), (0, ""))

    def test_unbound_caller_and_missing_path(self):
        self.assertRefused(self.home.path / "tests" / "x.py", "not bound", agent_id=AGENT_D)
        p = self.pre_edit(self.home.path / "tests" / "x.py")
        p["tool_input"] = {"content": "x"}
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.decision), (0, "deny"))
        self.assertIn("file_path", r.reason)


class WorktreeElsewhereTest(StateDirAsserts, HookCase):
    """SPD-016: every worktree `git worktree list --porcelain` names for a project's root maps to repository-relative paths,
    wherever `git worktree add` put it, so the deliverable globs bind there too.  The project here is project spud, the
    tool checkout beside the home (SPD-233), and `elsewhere` is a worktree of it outside .claude/worktrees; the list is
    cached under the home's .spud/ until a worktree is added, moved or removed, and a list that cannot be read fails the
    enforcing hook closed.  The lead holds tests/** and bin/spud and is planned from `elsewhere`, which binds SPD-001 there
    (SPD-098).

    SPD-097: a worktree of the tool carries none of the home's rules -- no generated roots, no Spud's own set -- because
    the home is a directory of its own and the vault is not checked out there; a `home:` glob binds at the home and never
    in the tool.  test_a_home_glob_does_not_bind_in_a_worktree_of_the_tool pins that."""

    in_process = True  # SPD-231

    def build_home(self):
        super().build_home()
        self.elsewhere = self.add_worktree("elsewhere")
        self.lead = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["tests/**", "bin/spud"], cwd=self.elsewhere), AGENT_A)

    def git(self, *args):
        return git(self.home.tool, *args)

    def add_worktree(self, name):
        """A worktree of the tool beside it, inside the root the class home restores."""
        path = self.home.tool.parent / ("%s-%s" % (self.home.tool.name, name))
        self.git("worktree", "add", "-q", "-b", name, str(path))
        return path

    def cache(self):
        return self.home.path / STATE / "worktrees" / "spud.json"  # one cache per project since SPD-014

    def worktree_stamps(self):
        """What the list's fingerprint stamps: the tool's <common>/worktrees and every worktrees/<id>/gitdir in it."""
        admin = self.home.tool / ".git" / "worktrees"
        return [admin, *sorted(admin.glob("*/gitdir"))]

    def settle_worktrees(self):
        """Every stamp the list is kept under an hour old by mtime (its ctime stays now: nothing but the kernel sets it).
        The list is kept only once every stamp is SETTLED_NS old (SPD-250), and the class home is put back after a test
        that added a worktree by removing its admin entry, which leaves worktrees/ young, so a test that wants the cache
        written settles first."""
        then = time.time() - 3600
        for path in self.worktree_stamps():
            os.utime(path, (then, then))

    def unsettle_worktrees(self):
        """Every stamp the list is kept under touched at one instant, returned in nanoseconds, as `git worktree add` leaves
        them, with no cache left from before: the class home's stamps may already be settled when a test starts."""
        now = time.time_ns()
        for path in self.worktree_stamps():
            os.utime(path, ns=(now, now))
        self.cache().unlink(missing_ok=True)
        return now

    def clock(self, now):
        """The clock the settle rule reads (hooks/worktrees' time.time_ns) answering now[0] for the block."""
        return mock.patch("spudlib.hooks.worktrees.time", mock.Mock(wraps=time, time_ns=lambda: now[0]))

    def put_back(self, stamps):
        """Each path's mtime (and atime) put back to what os.stat read before, as `touch -r` or os.utime can."""
        for path, st in stamps.items():
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))

    def assertCheckoutHolds(self, spelled):
        """The tool's worktree `elsewhere`, spelled another way the filesystem honours: the lead's globs bind there, every
        other path is a deliverable of nobody's, and Spud writes none of it."""
        a = str(spelled)
        self.assertSilent("%s/tests/x.py" % a)
        self.assertRefused("%s/CLAUDE.md" % a, "deliverables")
        self.assertRefused("%s/ledger/tickets/SPD-001.md" % a, "deliverables")
        self.assertRefused("%s/bin/spud" % a, "Law 1", agent_id=None)
        self.assertRefused("%s/CLAUDE.md" % a, "Law 1", agent_id=None)
        self.assertBashRefused("echo x > %s/ledger/tickets/SPD-001.md" % a, "deliverables")
        self.assertBashRefused("cd %s && echo x > docs/x.md" % a, "deliverables")
        self.assertBashRefused("echo x > %s/bin/spud" % a, "Law 1", agent_id=None)
        self.assertBashSilent("echo x > %s/tests/out.txt" % a)

    def test_a_worktree_outside_claude_worktrees_maps_to_the_repository(self):
        wt = self.elsewhere
        self.assertIn("worktree %s\n" % wt, self.git("worktree", "list", "--porcelain"))
        self.assertCheckoutHolds(wt)
        self.assertSilent(wt / "bin" / "spud")
        # a sibling directory that is no worktree stays outside every project root (in the temp roots, so silent), and the
        # home still maps
        self.assertSilent(self.home.tool.parent / ("%s-elsewhere-not" % self.home.tool.name) / "ledger" / "x.md")
        self.assertRefused(self.home.path / "ledger" / "x.md", "generated")

    def test_a_worktree_added_later_is_mapped_at_once(self):
        """A worktree added after the list was cached is project spud's checkout at once: another checkout of the lead's
        bound ticket's project, where it writes nothing (SPD-098)."""
        later = self.home.tool.parent / ("%s-later" % self.home.tool.name)
        self.assertSilent(later / "tests" / "x.py")
        self.add_worktree("later")
        self.assertRefused(later / "tests" / "x.py", "a checkout of project spud")
        self.git("worktree", "remove", "--force", str(later))
        self.assertSilent(later / "tests" / "x.py")

    def test_the_list_is_cached_until_the_worktrees_change(self):
        self.settle_worktrees()
        self.assertSilent(self.elsewhere / "tests" / "x.py")
        path = self.home.env["PATH"]
        self.home.env["PATH"] = "/nonexistent"  # no git to run: the answer comes from the cache
        self.assertSilent(self.elsewhere / "tests" / "x.py")
        self.home.env["PATH"] = path
        self.add_worktree("third")
        self.home.env["PATH"] = "/nonexistent"  # the worktrees changed and git cannot list them: fail closed
        r = self.edit(self.elsewhere / "tests" / "x.py")
        self.assertEqual((r.code, r.stdout), (2, ""), r)
        self.assertIn("failing closed", r.stderr)

    def test_a_worktree_moved_with_its_mtimes_put_back_is_seen(self):
        """SPD-250: `git worktree move` rewrites worktrees/<id>/gitdir, the id kept, so a move to a path of the same length
        leaves its size as it was, and an mtime put back (touch -r, os.utime) leaves the mtime: the ctime, which nothing but
        the kernel sets, is in the stamp, so the list is read again."""
        extra = self.add_worktree("extra")
        moved = self.home.tool.parent / ("%s-extrb" % self.home.tool.name)  # the same length as `extra`
        self.settle_worktrees()
        self.assertRefused(extra / "tests" / "x.py", "a checkout of project spud")  # listed, and kept
        self.assertTrue(self.cache().is_file())
        before = {p: os.stat(p) for p in self.worktree_stamps()}
        self.git("worktree", "move", str(extra), str(moved))
        self.put_back(before)
        self.assertEqual(self.worktree_stamps(), list(before), "the same ids")
        self.assertEqual([(os.stat(p).st_size, os.stat(p).st_mtime_ns) for p in before],
                         [(st.st_size, st.st_mtime_ns) for st in before.values()], "sizes and mtimes as they were")
        self.assertRefused(moved / "tests" / "x.py", "a checkout of project spud")
        self.assertSilent(extra / "tests" / "x.py")  # gone: outside every project root, in the temp roots

    def test_a_worktree_removed_and_another_added_under_its_id_is_seen(self):
        """SPD-250: removing a worktree and adding another whose directory has the same name makes worktrees/<id>/gitdir
        again, the same size when the path is the same length; with the mtimes put back only its inode and ctime differ."""
        first = self.home.tool.parent / "aa" / "tool-w"
        second = self.home.tool.parent / "bb" / "tool-w"
        self.git("worktree", "add", "-q", "--detach", str(first))
        self.settle_worktrees()
        self.assertRefused(first / "tests" / "x.py", "a checkout of project spud")  # listed, and kept
        self.assertTrue(self.cache().is_file())
        before = {p: os.stat(p) for p in self.worktree_stamps()}
        self.git("worktree", "remove", "--force", str(first))
        self.git("worktree", "add", "-q", "--detach", str(second))
        self.put_back(before)
        self.assertEqual(self.worktree_stamps(), list(before), "the same ids")
        self.assertEqual([(os.stat(p).st_size, os.stat(p).st_mtime_ns) for p in before],
                         [(st.st_size, st.st_mtime_ns) for st in before.values()], "sizes and mtimes as they were")
        self.assertRefused(second / "tests" / "x.py", "a checkout of project spud")
        self.assertSilent(first / "tests" / "x.py")

    def test_a_list_is_kept_only_once_its_stamps_have_settled(self):
        """SPD-250: a worktree added, moved or removed in the clock tick of the stamp read leaves the stamp as it was on a
        filesystem that keeps seconds (HFS+) or two (FAT), so the list is kept only once every stamp is SETTLED_NS old, as
        gitrepos' two caches keep theirs (SPD-131, SPD-238), and git is run again until then."""
        target = self.elsewhere / "tests" / "x.py"
        written = self.unsettle_worktrees()
        with self.clock([written]):
            self.assertSilent(target)
        self.assertFalse(self.cache().exists(), "the worktrees changed this second: nothing is kept yet")
        self.settle_worktrees()
        self.assertSilent(target)
        self.assertTrue(self.cache().is_file(), "settled: the list is kept")
        path = self.home.env["PATH"]
        self.home.env["PATH"] = "/nonexistent"  # no git to run: the answer comes from the cache
        self.assertSilent(target)
        self.home.env["PATH"] = path

    def test_the_clock_is_read_before_the_worktrees_are_stamped(self):
        """SPD-250, as SPD-249 for gitrepos: the reading an entry keeps must begin a whole tick after every stamp's mtime,
        so the settle rule's clock is read before the first stamp, not after the git run: a `git worktree list` that took
        SETTLED_NS on a loaded machine would otherwise call settled the stamps read in the change's own tick."""
        worktrees = importlib.import_module("spudlib.hooks.worktrees")
        written = self.unsettle_worktrees()
        real, now, runs = worktrees.git_worktree_list, [written], []

        def slow(root):
            out = real(root)
            runs.append(root)
            now[0] = written + worktrees.SETTLED_NS
            return out

        with self.clock(now), mock.patch.object(worktrees, "git_worktree_list", slow):
            self.assertSilent(self.elsewhere / "tests" / "x.py")
        self.assertEqual(len(runs), 1, "the miss runs git once")
        self.assertFalse(self.cache().exists(), "the stamps were read in the change's second: a slow run makes them no older")

    def test_a_list_git_cannot_give_fails_the_enforcing_hook_closed(self):
        (self.home.tool / ".git" / "HEAD").write_text("garbage\n", encoding="utf-8")  # no longer a repository to git
        r = self.edit(self.elsewhere / "tests" / "x.py")
        self.assertEqual((r.code, r.stdout), (2, ""), r)
        self.assertIn("worktree", r.stderr)
        self.assertIn("failing closed", r.stderr)

    def test_a_worktree_elsewhere_in_upper_case(self):
        """SPD-029: git names the worktree by one spelling; a case variant of it is the same checkout."""
        self.assertCheckoutHolds(self.alias_or_skip(self.elsewhere, str(self.elsewhere).upper(), "upper case"))

    def test_a_worktree_elsewhere_in_mixed_case(self):
        self.assertCheckoutHolds(self.alias_or_skip(self.elsewhere, mixed_case(str(self.elsewhere)), "mixed case"))

    def test_a_worktree_elsewhere_under_the_data_volume_firmlink(self):
        self.assertCheckoutHolds(self.alias_or_skip(self.elsewhere, FIRMLINK + str(self.elsewhere), "the %s firmlink prefix" % FIRMLINK))

    def test_a_linked_worktree_is_fingerprinted_like_the_main_checkout(self):
        """SPD-097: a linked worktree's `.git` is a gitfile, not a directory, so worktrees_fingerprint answered None there,
        checkout_worktrees never wrote its cache, and every hook run that mapped a path shelled out to `git worktree list`
        again -- +13.36 ms on PreToolUse(Write) in the timing probe, which runs the branch's own launcher from a worktree.
        The gitfile is resolved to the repository directory now, so a checkout that is a worktree caches like a root."""
        m = load_spud_module()
        root, wt = str(self.home.tool), str(self.elsewhere)
        self.assertTrue(os.path.isfile(os.path.join(wt, ".git")), "a linked worktree's .git is a gitfile")
        self.assertEqual(os.path.realpath(m.repository_dir(wt)), os.path.realpath(os.path.join(root, ".git")))
        fingerprint = m.worktrees_fingerprint(wt)
        self.assertIsNotNone(fingerprint)
        self.assertEqual(fingerprint, m.worktrees_fingerprint(root))
        self.add_worktree("third")  # what the fingerprint exists to notice, from either checkout
        self.assertNotEqual(m.worktrees_fingerprint(wt), fingerprint)
        self.assertEqual(m.worktrees_fingerprint(wt), m.worktrees_fingerprint(root))
        self.assertIsNone(m.repository_dir(self.home.root / "no-such-checkout"))
        self.assertIsNone(m.worktrees_fingerprint(self.home.root / "no-such-checkout"))

    def test_a_home_glob_does_not_bind_in_a_worktree_of_the_tool(self):
        """SPD-097: a worktree elsewhere is project spud's checkout, so a member whose deliverables name the home is refused
        there and is free at the home, and the reverse for a bare glob, which is the ticket's project's.  The generated
        roots are the home's alone: a worktree of the tool is no vault, so its ledger/ is an ordinary path there."""
        self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_B)
        self.assertRefused(self.elsewhere / "tests" / "x.py", "deliverables", agent_id=AGENT_B)
        self.assertRefused(self.elsewhere / "ledger" / "x.md", "deliverables", agent_id=AGENT_B)
        self.assertSilent(self.home.path / "tests" / "x.py", agent_id=AGENT_B)
        self.assertRefused(self.home.path / "ledger" / "x.md", "generated", agent_id=AGENT_B)
        self.spawn(self.plan(persona="engineer", model="opus", deliverable=["**"], cwd=self.elsewhere), AGENT_C)
        self.assertSilent(self.elsewhere / "tests" / "x.py", agent_id=AGENT_C)
        self.assertSilent(self.elsewhere / "ledger" / "x.md", agent_id=AGENT_C)
        self.assertRefused(self.home.path / "tests" / "x.py", "deliverables", agent_id=AGENT_C)
        self.assertRefused(self.home.path / "ledger" / "x.md", "generated", agent_id=AGENT_C)

    def test_the_state_directory_holds_at_the_home_and_at_a_worktree_elsewhere(self):
        """SPD-031: the worktree cache this class exercises, which the hook itself writes, and a worktree elsewhere's own state
        directory, refused to a member whose glob is ** and to Spud.  Both scopes since SPD-097, so the globs really do reach
        every path the state directory is refused at: `home:**` at the home, the bare `**` in project spud's worktree."""
        self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**", "**"], cwd=self.elsewhere), AGENT_B)
        self.settle_worktrees()
        self.assertSilent(self.elsewhere / "tests" / "x.py", agent_id=AGENT_B)  # lists the worktrees, writing the cache
        cache = self.cache()
        self.assertTrue(cache.is_file())
        agents = (AGENT_B, None)
        self.assertStateHolds(cache, agents)
        self.assertStateHolds(self.elsewhere / STATE / "ledger.db", agents)
        self.assertStateHolds(self.elsewhere / STATE / "pycache" / "x.pyc", agents)
        for what, spelled in (("upper case", str(self.elsewhere).upper()), ("the %s firmlink prefix" % FIRMLINK, FIRMLINK + str(self.elsewhere))):
            with self.subTest(what):
                self.assertStateHolds(self.alias_or_skip(self.elsewhere, spelled, what) + "/" + STATE + "/worktrees.json", agents)


class PathAliasTest(PathAliasAsserts, HookCase):
    """SPD-029 (proposal 24): the path rule found a root by comparing spellings, so on macOS a target spelled with a case
    variant of the home, under the /System/Volumes/Data firmlink, or with a component the filesystem folds, counted as
    outside every project root and Laws 1 and 5 said nothing.  A root is found by file identity now."""

    in_process = True  # SPD-231

    def setUp(self):
        super().setUp()
        self.lead = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:tests/**", "home:bin/spud"]), AGENT_A)
        self.wt = self.home.path / ".claude" / "worktrees" / "spd-099-thing"
        self.wt.mkdir(parents=True)

    def test_the_exact_spellings_hold(self):
        self.assertRootHolds(self.home.path)
        self.assertRootHolds(self.wt)

    def test_the_home_in_upper_case(self):
        self.assertRootHolds(self.alias_or_skip(self.home.path, str(self.home.path).upper(), "upper case"))

    def test_the_home_in_mixed_case(self):
        self.assertRootHolds(self.alias_or_skip(self.home.path, mixed_case(str(self.home.path)), "mixed case"))

    def test_the_home_under_the_data_volume_firmlink(self):
        self.assertRootHolds(self.alias_or_skip(self.home.path, FIRMLINK + str(self.home.path), "the %s firmlink prefix" % FIRMLINK))

    def test_a_claude_worktree_in_upper_and_mixed_case(self):
        home, wt = str(self.home.path), str(self.wt)
        for what, spelled in (("upper case", wt.upper()), ("mixed case", mixed_case(wt)),
                              ("upper-case .claude/worktrees", home + "/.CLAUDE/WORKTREES/spd-099-thing"),
                              ("Kelvin sign in worktrees", home + "/.claude/worKtrees/spd-099-thing")):
            with self.subTest(what):
                self.assertRootHolds(self.alias_or_skip(wt, spelled, what))

    def test_a_claude_worktree_under_the_data_volume_firmlink(self):
        self.assertRootHolds(self.alias_or_skip(self.wt, FIRMLINK + str(self.wt), "the %s firmlink prefix" % FIRMLINK))

    def test_a_generated_root_spelled_with_a_simple_case_fold(self):
        """APFS folds U+017F (long s) to s, as Unicode simple case folding does; str.lower() does not."""
        self.spawn(self.plan(deliverable=["home:**"]), AGENT_B)
        spelled = str(self.home.path / "reportſ" / "2026-09-13.md")
        self.assertRefused(spelled, "generated", agent_id=AGENT_B)
        self.assertBashRefused("echo x > %s" % spelled, "generated", agent_id=AGENT_B)
        self.assertSilent(self.home.path / "docs" / "x.md", agent_id=AGENT_B)

    def test_dot_dot_after_a_symlink_is_resolved_as_the_filesystem_does(self):
        """tests/sub/.. is the parent of the link's target, not tests: the kernel resolves the link first."""
        home = self.home.path
        (home / "ledger" / "tickets").mkdir(parents=True, exist_ok=True)
        (home / "tests").mkdir(exist_ok=True)
        (home / "tests" / "sub").symlink_to(home / "ledger" / "tickets")
        spelled = "%s/tests/sub/../SPD-001.md" % home
        self.assertRefused(spelled, "generated")
        self.assertBashRefused("echo x > %s" % spelled, "generated")
        self.assertSilent(home / "tests" / "sub2" / ".." / "x.py")


class NonAsciiHomeTest(PathAliasAsserts, HookCase):
    """SPD-029: APFS is normalization-insensitive, so the NFD spelling of a home named in NFC is the same directory."""

    home_name = "Spüd"
    in_process = True  # SPD-231

    def setUp(self):
        super().setUp()
        self.lead = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:tests/**", "home:bin/spud"]), AGENT_A)

    def test_the_nfc_home_holds(self):
        self.assertTrue(unicodedata.is_normalized("NFC", str(self.home.path)))
        self.assertRootHolds(self.home.path)

    def test_the_home_spelled_nfd(self):
        nfd = unicodedata.normalize("NFD", str(self.home.path))
        self.assertNotEqual(nfd, str(self.home.path))
        self.assertRootHolds(self.alias_or_skip(self.home.path, nfd, "NFD normalization"))


class StateDirTest(StateDirAsserts, HookCase):
    """SPD-031 (proposal 27): the ledger state directory at a project root holds the database, its WAL and shm files, the worktree
    list cache, the backups and the launcher's cached bytecode, which every hook run loads.  The Bash hook refused a command
    naming it, but the edit hook checked a path there only against Law 1 and the deliverable globs, so a member whose globs
    reached it could Write the database.  Nothing but the CLI writes there now, for any actor."""

    in_process = True  # SPD-231

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_A)
        self.named = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:" + STATE + "/**", "home:" + STATE + "/ledger.db"]), AGENT_B)
        self.state = self.home.path / STATE
        self.pyc = sorted((self.state / "pycache").rglob("*.pyc"))
        self.targets = {
            "database": self.state / "ledger.db",
            "WAL": self.state / "ledger.db-wal",
            "worktree cache": self.state / "worktrees.json",
            "cached bytecode": self.pyc[0] if self.pyc else self.state / "pycache" / "spud_ledger.cpython-314.pyc",
            "backup": self.state / "backups" / "ledger-2026-09-13.db",
            "the directory itself": self.state,
        }

    def test_every_edit_tool_is_refused_for_everyone_whatever_the_globs(self):
        self.assertEqual((self.wide["deliverables"], self.named["deliverables"]), (["home:**"], ["home:" + STATE + "/**", "home:" + STATE + "/ledger.db"]))
        self.assertTrue(self.targets["database"].is_file())
        self.assertTrue(self.pyc, "the launcher caches its bytecode under the state directory")
        for what, target in self.targets.items():
            for agent_id in (AGENT_A, AGENT_B, None):
                for tool in ("Write", "Edit"):
                    with self.subTest(what=what, agent_id=agent_id, tool=tool):
                        self.assertStateRefused(target, agent_id, tool)
        for tool in ("MultiEdit", "NotebookEdit"):
            self.assertStateRefused(self.targets["database"], AGENT_A, tool)
        self.assertStateRefused(self.targets["database"], AGENT_D)  # an unbound caller: refused in the same words
        self.assertIn(STATE + "/ledger.db", [e["data"].get("path") for e in self.denied()])

    def test_a_shell_redirection_is_refused_for_everyone(self):
        home = self.home.path
        for what in ("database", "worktree cache", "cached bytecode"):
            for agent_id in (AGENT_A, AGENT_B, None):
                with self.subTest(what=what, agent_id=agent_id):
                    self.assertStateBashRefused("echo x > %s" % quote_split(self.targets[what]), agent_id)
        for agent_id in (AGENT_A, AGENT_B, None):
            with self.subTest("tee and relative after cd", agent_id=agent_id):
                self.assertStateBashRefused("printf x | tee -a %s" % quote_split(self.targets["worktree cache"]), agent_id)
                self.assertStateBashRefused("cd %s && echo x > .\"spud\"/pycache/x.pyc" % home, agent_id)
                self.assertStateBashRefused("cd %s && echo x >> .'spud'/ledger.'db'-wal" % home, agent_id)

    def test_aliases_of_the_state_directory(self):
        home, state = str(self.home.path), str(self.state)
        for what, spelled in (("upper case", home + "/.SPUD"), ("mixed case", home + "/.SpUd"), ("long s (U+017F)", home + "/.ſpud"),
                              ("upper-case home", state.upper()), ("mixed-case home", mixed_case(home) + "/" + STATE),
                              ("the %s firmlink prefix" % FIRMLINK, FIRMLINK + state)):
            with self.subTest(what):
                spelled = self.alias_or_skip(state, spelled, what)
                for name in ("ledger.db", "worktrees.json"):
                    self.assertStateHolds(spelled + "/" + name, (AGENT_A, AGENT_B, None))

    def test_symlinks_and_dot_dot_into_the_state_directory(self):
        home = self.home.path
        (home / "tests").mkdir(exist_ok=True)
        (home / "tests" / "state").symlink_to(self.state)
        (home / "tests" / "db").symlink_to(self.targets["database"])
        (home / "tests" / "sub").symlink_to(self.state / "pycache")
        outside = home.parent / ("%s-state-%d" % (home.name, os.getpid()))
        outside.symlink_to(self.state)
        self.addCleanup(outside.unlink)
        for what, spelled in (("a symlink in the repository", home / "tests" / "state" / "ledger.db"),
                              ("a symlink to the database file", home / "tests" / "db"),
                              ("a symlink outside every root", outside / "worktrees.json"),
                              (".. after a symlink", "%s/tests/sub/../ledger.db" % home)):
            with self.subTest(what):
                self.assertStateHolds(spelled, (AGENT_A, None))

    def test_the_state_directory_of_a_claude_worktree_root(self):
        """Refused at every project root, not only the home's: a worktree's own bin/spud run without SPUD_HOME takes the worktree
        as its home and keeps its database and cached bytecode in that root's state directory (bin/spud); only the CLI writes one."""
        wt = self.home.path / ".claude" / "worktrees" / "spd-099-thing"
        (wt / STATE).mkdir(parents=True)
        for name in ("ledger.db", "worktrees.json", "pycache/x.pyc"):
            self.assertStateHolds(wt / STATE / name, (AGENT_A, AGENT_B, None))
        with self.subTest("upper case"):
            self.assertStateHolds(self.alias_or_skip(wt, str(wt).upper(), "upper case") + "/" + STATE + "/ledger.db")

    def test_names_like_the_state_directory_stay_under_the_globs(self):
        """Controls.  Only the first component below a project root is the state directory: a nested one (tests/fixtures/.spud) is no
        ledger's state, since bin/spud keeps its state at the root of its home, so it stays under the deliverable globs like any
        similar name.  (The Bash hook's raw-text regex still refuses a command that spells a nested one plainly.)"""
        home = self.home.path
        nested = (home / "tests" / "fixtures" / STATE / "ledger.db", home / "docs" / STATE / "worktrees.json")
        similar = (home / (STATE + "rc"), home / (STATE + "-notes") / "x.md", home / "x.spud", home / "docs" / "spud" / "x.md", home / "spud" / "ledger.db")
        for p in nested + similar:
            with self.subTest(str(p)):
                self.assertSilent(p, agent_id=AGENT_A)
                self.assertRefused(p, "Law 1", agent_id=None)
        self.assertRefused(nested[0], "deliverables", agent_id=AGENT_B)
        self.assertBashSilent("echo x > %s" % quote_split(nested[0]), agent_id=AGENT_A)
        self.assertBashSilent("echo x > %s" % (home / (STATE + "rc")), agent_id=AGENT_A)
        self.assertBashRefused("echo x > %s" % quote_split(nested[0]), "Law 1", agent_id=None)
        self.assertBashSilent("echo x > %s" % (home / "tests" / "out.txt"), agent_id=AGENT_A)


class OutsideProjectTest(PathAliasAsserts, HookCase):
    """SPD-064: edit_reason resolved a target against the registered projects and, when it lay inside none of them, returned
    no reason at all (`inside = project_paths(...); if not inside: return None, None`), so the path rule that holds a member
    to its deliverable globs stopped at project boundaries: a bound member could Write, Edit or redirect into ~/.gitconfig,
    ~/.claude/settings.json and ~/.claude/agents/, the shell rc files, ~/.ssh, a LaunchAgent, or anything else in Eric's
    home.  Spud's design decision is the allowlist: a caller with an agent_id in a Spud session may write outside every
    registered project only under the harness's scratchpad root for this user (/private/tmp/claude-<uid>/) and the system
    temp directories, where members run probes and differential harnesses; everything else outside a project is refused,
    fail closed, with the path named.  Both readings of the target must land under an allowed root, so a symlink planted in
    the scratchpad that points at ~/.gitconfig is refused while /tmp and /private/tmp, which resolve to each other, stay
    open.  Unchanged: Spud himself, who keeps writing his own files and his memory directory under ~/.claude/projects/;
    a plain session and its unbound subagents (tests/test_hooks_projects.py); the harness_file and state-directory refusals,
    which run before the project lookup.

    The suite's homes and registered projects live under tempfile, which the allowlist opens, so every refusal here names a
    path under a HOME that does not exist (/Users/Nobody) or a system directory outside every temp root."""

    in_process = True  # SPD-231

    def setUp(self):
        super().setUp()
        self.lead = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:tests/**", "home:bin/spud"]), AGENT_A)
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.fake_home = "/Users/Nobody"  # a HOME outside every project and every temp root; nothing here is created
        self.home.env["HOME"] = self.fake_home
        self.scratchpad = "/private/tmp/claude-%d/-Users-Someone-Personal-Spud/%s/scratchpad" % (os.getuid(), SESSION)
        self.tmp = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    # The ticket's own list, plus the two files `project sync --all` writes under ~/.claude.  ~/.gitconfig and
    # $XDG_CONFIG_HOME/git/config are here too, but they earn SPD-063's Law 7 reason first (GitConfigFileTest).
    def sensitive(self):
        return [self.fake_home + p for p in ("/.zshrc", "/.bashrc", "/.profile", "/.ssh/config", "/.ssh/authorized_keys",
                                             "/.claude/settings.json", "/.claude/agents/x.md")] \
            + ["/Library/LaunchAgents/x.plist", "/etc/hosts"]

    def open_paths(self):
        return [self.scratchpad + "/notes.md", str(self.tmp / "probe.py"), "/tmp/x", "/private/tmp/x",
                "/private/tmp/claude-%d/x" % os.getuid(), str(self.tmp.parent / "sibling.txt")]

    def test_a_member_may_not_write_outside_every_project(self):
        for p in self.sensitive():
            with self.subTest(p):
                for agent_id in (AGENT_A, AGENT_C):
                    r = self.assertRefused(p, OUTSIDE, agent_id=agent_id)
                    self.assertIn(p, r.reason)  # the reason names the path
        self.assertEqual(self.wide["deliverables"], ["home:**"])  # not even a ** member: outside a project there is no glob

    def test_the_git_config_files_outside_every_project_are_refused_too(self):
        for p in (self.fake_home + "/.gitconfig", self.fake_home + "/.config/git/config"):
            with self.subTest(p):
                r = self.assertRefused(p, p)  # SPD-063's Law 7 reason comes first; both name the path
                self.assertIn("Law 7", r.reason)
                self.assertSilent(p, agent_id=None)

    def test_every_edit_tool_is_covered_and_the_denial_is_recorded(self):
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            with self.subTest(tool):
                self.assertRefused(self.fake_home + "/.zshrc", OUTSIDE, tool=tool)
        self.assertIn(self.fake_home + "/.zshrc", [e["data"].get("path") or "" for e in self.denied()])

    def test_a_redirection_or_tee_outside_every_project(self):
        for p in self.sensitive():
            for command in ("echo x > %s", "echo x >> %s", "printf x | tee %s", "printf x | tee -a %s"):
                with self.subTest(command % p):
                    self.assertBashRefused(command % p, OUTSIDE)
                    self.assertBashSilent(command % p, agent_id=None)  # Spud writes his own files

    def test_a_tilde_spelling_is_expanded_and_a_variable_one_is_refused_as_unresolvable(self):
        self.assertBashRefused("echo x > ~/.zshrc", OUTSIDE)
        self.assertRefused("~/.zshrc", OUTSIDE)
        # A variable the line did not assign stays SPD-043's unresolvable-target refusal, not this one.
        for agent_id in (AGENT_A, None):  # Spud too, since SPD-091
            r = self.home.hook("PreToolUse", self.pre_bash("echo x > $HOME/.zshrc", agent_id=agent_id))
            self.assertEqual((r.code, r.decision), (0, "deny"), r)
            self.assertIn("spell the path out", r.reason)
            self.assertNotIn(OUTSIDE, r.reason)

    def test_the_scratchpad_and_the_system_temp_directories_stay_open(self):
        for p in self.open_paths():
            with self.subTest(p):
                for agent_id in (AGENT_A, AGENT_C, None):
                    self.assertSilent(p, agent_id=agent_id)
                self.assertBashSilent("echo x > %s" % p)
                self.assertBashSilent("printf x | tee %s" % p)

    def test_a_symlink_under_a_temp_root_that_resolves_outside_is_refused(self):
        link = self.tmp / "link"
        link.symlink_to(self.fake_home + "/.zshrc")  # the target need not exist: the check is on the path
        deep = self.tmp / "dir"
        deep.symlink_to(self.fake_home + "/.claude")
        for p in (link, deep / "settings.json"):
            with self.subTest(str(p)):
                self.assertRefused(p, OUTSIDE)
                self.assertBashRefused("echo x > %s" % p, OUTSIDE)
        self.assertSilent(self.tmp / "real.txt")  # the control: a real file beside the links

    def test_a_symlink_inside_the_globs_cannot_reach_outside_either(self):
        """Every reading of the target is accounted for: inside a project (the globs decide) or under an allowed root."""
        tests = self.home.path / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "escape").symlink_to(self.fake_home + "/.ssh")
        self.assertRefused(tests / "escape" / "config", OUTSIDE)
        self.assertBashRefused("echo x > %s/escape/config" % tests, OUTSIDE)
        (tests / "outlink").symlink_to("/tmp")  # a link to an allowed root stays open, as it was before
        self.assertSilent(tests / "outlink" / "x.txt")

    def test_spud_keeps_his_own_files_and_his_memory_directory(self):
        for p in self.sensitive() + [self.fake_home + "/.claude/projects/-Users-Someone-Personal-Spud/memory/MEMORY.md"]:
            with self.subTest(p):
                self.assertSilent(p, agent_id=None)

    def test_the_harness_and_state_directory_refusals_still_come_first(self):
        harness = "/private/tmp/claude-%d/-Users-Someone-Personal-Spud/%s/subagents/agent-%s.meta.json" % (os.getuid(), SESSION, AGENT_A)
        r = self.assertRefused(harness, "harness")
        self.assertNotIn(OUTSIDE, r.reason)
        r = self.assertRefused(self.home.path / STATE / "ledger.db", DB_WORDING)
        self.assertNotIn(OUTSIDE, r.reason)

    def test_the_reason_names_the_path_and_the_roots_that_stay_open(self):
        r = self.assertRefused(self.fake_home + "/.claude/settings.json", OUTSIDE)
        self.assertIn("scratchpad", r.reason)
        self.assertIn("deliverables", r.reason)
        self.assertIn("/private/tmp/claude-%d" % os.getuid(), r.reason)

    def test_an_unbound_agent_id_keeps_its_scratchpad_and_nothing_else(self):
        """A foreground child before its first tool call writes probes in its scratchpad; it has no globs, so nothing else."""
        self.assertSilent(self.scratchpad + "/probe.py", agent_id=AGENT_D)
        self.assertRefused(self.fake_home + "/.zshrc", OUTSIDE, agent_id=AGENT_D)
        self.assertRefused(self.home.path / "tests" / "x.py", "not bound", agent_id=AGENT_D)  # inside a project: unchanged


class DeliverableGlobTest(SpudTestCase):
    def test_member_new_validates_deliverables(self):
        t = self.new_ticket("Globs")
        for bad in ("../x", "/abs/path", "tests/../x", "", "  "):
            proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", "--deliverable", bad, actor="spud", check=False)
            self.assertEqual(proc.returncode, EXIT_ERROR, bad)
            self.assertIn("deliverable", proc.stderr)
        m = self.new_member(t["key"], deliverable=["home:./tests/**", "home:docs/", "home:bin/spud"])
        self.assertEqual(m["deliverables"], ["home:tests/**", "home:docs/**", "home:bin/spud"])
        proc = self.home.run("member", "edit", m["ref"], "--deliverable", "../y", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)


class GlobSemanticsTest(unittest.TestCase):
    """The deliverable glob's own functions, read from the program: every one a function of its arguments, so no home
    (SPD-233; DeliverableGlobTest keeps the one test that plans members)."""

    def test_glob_semantics(self):
        spud = load_spud_module()
        match = spud.path_matches_glob
        self.assertTrue(match("tests/a.py", "tests/**"))
        self.assertTrue(match("tests/a/b/c.py", "tests/**"))
        self.assertTrue(match("tests", "tests/**") is False)
        self.assertTrue(match("bin/spud", "bin/spud"))
        self.assertFalse(match("bin/spud2", "bin/spud"))
        self.assertTrue(match("docs/x/a.md", "docs/x/*.md"))
        self.assertFalse(match("docs/x/a/b.md", "docs/x/*.md"))
        self.assertTrue(match("a/b/c/d.md", "**/d.md"))
        self.assertTrue(match("d.md", "**/d.md"))
        self.assertTrue(match("a/x/b.md", "a/**/b.md"))
        self.assertTrue(match("a/b.md", "a/**/b.md"))
        self.assertTrue(match("ledger/Board.base", "ledger/*.base"))
        self.assertFalse(match("ledger/x/Board.base", "ledger/*.base"))
        # SPD-086 changed this case deliberately: brackets no longer open a character class, so `[?]` is a literal
        # bracket around the one-character wildcard, which is what a reader of the glob would take it for.
        self.assertFalse(match("a/b?c", "a/b[?]c"))
        self.assertTrue(match("a/b[x]c", "a/b[?]c"))
        self.assertFalse(match("Tests/a.py", "tests/**"))

    def test_brackets_are_literal(self):
        """SPD-086: `[` opened a character class, so a Next.js dynamic segment matched nothing and the member that owned
        it was read-only on its own files.  Every character but `*`, `?` and `**` is literal now."""
        spud = load_spud_module()
        match = spud.path_matches_glob
        glob = "admin/src/app/accounts/[email]/**"
        for ok in ("admin/src/app/accounts/[email]/page.tsx", "admin/src/app/accounts/[email]/_components/UserTab.tsx"):
            self.assertTrue(match(ok, glob), ok)
        for bad in ("admin/src/app/accounts/e/page.tsx", "admin/src/app/accounts/page.tsx", "admin/src/app/accounts/[id]/page.tsx"):
            self.assertFalse(match(bad, glob), bad)
        self.assertTrue(match("admin/src/app/accounts/[email]/_components/UserTab.tsx", "admin/src/app/accounts/?email?/**"))  # the workaround still works
        self.assertTrue(match("admin/src/app/accounts/[email]/_components/UserTab.tsx", "admin/src/app/accounts/**"))
        self.assertTrue(match("app/[...slug]/page.tsx", "app/[...slug]/**"))  # a catch-all route: the dots are literal too
        self.assertTrue(match("app/[[...slug]]/page.tsx", "app/[[...slug]]/**"))
        self.assertFalse(match("app/x/page.tsx", "app/[...slug]/**"))
        # Nothing is an escape any more, so the shell's own spelling of a literal bracket names a directory spelled that
        # way -- and, unlike before SPD-086, it compiles instead of raising re.PatternError from inside the hook.
        self.assertTrue(match("a/[[]b[]]/c", "a/[[]b[]]/c"))
        self.assertFalse(match("a/[b]/c", "a/[[]b[]]/c"))
        for glob in ("a/[]]/c", "a/[/c", "a/]/c", "a/[!x]/c", "a/[a-z]/c"):
            self.assertTrue(match(glob, glob), glob)  # each names itself, and none of them raises
            self.assertFalse(match("a/x/c", glob), glob)

    def test_a_globs_literal_directory_prefix(self):
        """SPD-129: the leading segments that hold no wildcard, dropping the segment the first wildcard is in; with no
        wildcard at all the glob names a file, and its prefix is the directory that holds it."""
        spud = load_spud_module()
        for glob, prefix in (("test/fixtures/movecheck/**", "test/fixtures/movecheck"), ("admin/src/*.ts", "admin/src"),
                             ("bin/spud", "bin"), ("dist/", "dist"), ("dist/**", "dist"), ("docs/x/*.md", "docs/x"),
                             ("a/**/b.md", "a"), ("app/[...slug]/**", "app/[...slug]"),  # SPD-086: a bracket is literal, so it is a segment
                             ("admin/src/app/accounts/[email]/**", "admin/src/app/accounts/[email]"),
                             ("**/d.md", ""), ("*.md", ""), ("**", ""), ("spud", ""), ("web/app*.js", "web")):
            with self.subTest(glob):
                self.assertEqual(spud.glob_directory(glob), prefix)

    def test_a_directory_a_glob_covers(self):
        """SPD-129: making one is allowed at a glob's literal prefix and at every ancestor of it, since an empty directory
        writes no content; removing one only where a single glob covers the whole subtree (`D/**` or `D/`)."""
        spud = load_spud_module()
        covers = spud.glob_covers_directory
        for rel in ("test/fixtures/movecheck", "test/fixtures", "test"):
            self.assertTrue(covers(rel, "test/fixtures/movecheck/**", "make"), rel)
        for rel in ("test/fixtures", "test"):  # an ancestor is made, never removed: under it are files no glob covers
            self.assertFalse(covers(rel, "test/fixtures/movecheck/**", "remove"), rel)
        self.assertTrue(covers("test/fixtures/movecheck", "test/fixtures/movecheck/**", "remove"))
        self.assertTrue(covers("test/fixtures/movecheck", "test/fixtures/movecheck/", "remove"))
        self.assertTrue(covers("dist", "dist/**", "remove"))
        self.assertTrue(covers("dist", "dist/", "remove"))
        for glob in ("dist/*", "dist/*.ts", "dist", "**", "dist/**/x"):
            self.assertFalse(covers("dist", glob, "remove"), glob)  # none of these covers the whole subtree
        for rel in ("test/fixtures/movecheck/x", "test/other", "tests", "", "te"):
            self.assertFalse(covers(rel, "test/fixtures/movecheck/**", "make"), rel)
        self.assertFalse(covers("admin", "**/x", "make"))  # a glob that starts with a wildcard has no prefix
        self.assertFalse(covers("", "**", "make"))
        self.assertTrue(covers("bin", "bin/spud", "make"))  # the directory of a file glob
        self.assertFalse(covers("bin/spud", "bin/spud", "remove"))
        # `fold` where the filesystem folds case, exactly as it does for a match
        self.assertTrue(covers("TEST/Fixtures", "test/fixtures/movecheck/**", "make", True))
        self.assertFalse(covers("TEST/Fixtures", "test/fixtures/movecheck/**", "make"))
        self.assertTrue(covers("Dist", "dist/**", "remove", True))
        self.assertFalse(covers("Dist", "dist/**", "remove"))

    def test_a_whole_subtree_a_glob_covers(self):
        """SPD-126: a write anywhere under a directory is inside one glob only when that glob matches every path under it:
        `**`, or a glob ending in `/**` (or `/`) that matches the directory or whose literal root is it.  A glob that matches
        the directory but not everything below it never covers the subtree, nor does one whose root holds a wildcard, at that
        root; and no directory "tree" lets in is one SPD-129's "remove" refuses."""
        spud = load_spud_module()
        covers = spud.glob_covers_directory
        for rel, glob in (("dist", "dist/**"), ("dist", "dist/"), ("dist/sub", "dist/**"), ("dist/a/b", "dist/"), ("x/y", "**"),
                          ("", "**"), ("a/x/b", "a/*/**"), ("a/x/y/z", "**/y/**")):
            with self.subTest(rel=rel, glob=glob):
                self.assertTrue(covers(rel, glob, "tree"))
        for rel, glob in (("bin/sub", "bin/*"), ("docs", "docs/*.md"), ("docs/x", "docs/*.md"), ("bin", "bin/spud"),
                          ("test", "test/fixtures/movecheck/**"), ("a/x", "a/*/**"), ("dist", "dist"), ("dist", "dist/**/x"),
                          ("dist", "**/x"), ("distx", "dist/**")):
            with self.subTest(rel=rel, glob=glob):
                self.assertFalse(covers(rel, glob, "tree"))
        self.assertTrue(covers("Dist/Sub", "dist/**", "tree", True))
        self.assertFalse(covers("Dist/Sub", "dist/**", "tree"))
        for rel in ("dist", "dist/sub", "a/x", "a/x/b", "bin/sub", ""):  # tree never lets in what remove refuses
            for glob in ("dist/**", "dist/", "**", "a/*/**", "bin/*", "bin/**"):
                if covers(rel, glob, "tree"):
                    self.assertTrue(covers(rel, glob, "remove") or spud.path_matches_glob(rel, glob), (rel, glob))

    def test_a_globs_scope_holds_for_the_directory_reading(self):
        """SPD-129: the new reading sits inside path_reason's own loop, so a `<key>:` or `home:` glob opens its project's
        directory and no other's, as it does for a match."""
        spud = load_spud_module()
        member = {"deliverables": json.dumps(["badtakes:src/lib/**", "home:docs/plans/**"])}
        for project, rel, directory, allowed in (("badtakes", "src/lib", "make", True), ("badtakes", "src", "make", True),
                                                 ("badtakes", "src/lib", "remove", True), ("badtakes", "docs/plans", "make", False),
                                                 ("home", "docs/plans", "make", True), ("home", "docs", "make", True),
                                                 ("home", "src/lib", "make", False), ("home", "docs/plans", "remove", True),
                                                 ("spud", "src/lib", "make", False)):
            with self.subTest(project=project, rel=rel, directory=directory):
                reason = spud.path_reason(rel, member, "SPUD-129/X", project_key=project, ticket_project_key="badtakes",
                                          home=(project == "home"), directory=directory)
                self.assertEqual(reason is None, allowed, reason)
        # with no directory kind the same paths are refused, which is every other caller of edit_reason
        for project, rel in (("badtakes", "src/lib"), ("home", "docs/plans")):
            self.assertIsNotNone(spud.path_reason(rel, member, "SPUD-129/X", project_key=project, ticket_project_key="badtakes",
                                                  home=(project == "home")))

    def test_no_accepted_glob_is_unsatisfiable(self):
        """SPD-086 asked whether `member new` should warn about a deliverable that can match no path.  It cannot check the
        checkout -- a member planned to create bin/spudlib/hooks/newmod.py legitimately matches nothing that exists yet --
        and with brackets literal there is nothing left to warn about: every glob normalize_deliverable accepts matches a
        path, because every character is either a wildcard or a literal, and `..`, absolute paths and empty segments are
        already refused.  The witness below is that path."""
        spud = load_spud_module()
        globs = ["tests/**", "bin/spud", "docs/x/*.md", "**/d.md", "a/**/b.md", "ledger/*.base", "web/app*.js",
                 "scripts/ci-changes*", "server/supabase/functions/collab/*.ts", "**", "a/b[?]c",
                 "admin/src/app/accounts/[email]/**", "app/[[...slug]]/page.tsx", "a/[]]/c", "notes/**"]
        for glob in globs:
            witness = glob.replace("**/", "w/").replace("**", "w/w").replace("*", "w").replace("?", "w")
            self.assertTrue(spud.path_matches_glob(witness, glob), (glob, witness))


if __name__ == "__main__":
    unittest.main()
