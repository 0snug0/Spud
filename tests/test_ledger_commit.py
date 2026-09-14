"""spud --as spud ledger commit (SPD-014, design section 5.3): render, stage only ledger/ and reports/ at the home, commit on
the home's default branch and push.  The home is a scratch git repository with a bare origin; git reads none of this
machine's configuration."""

import unittest

from helpers import EXIT_CONFLICT, EXIT_ERROR, EXIT_OK, EXIT_USAGE, RepoMixin, SpudTestCase, git


class LedgerCommitTest(RepoMixin, SpudTestCase):
    def setUp(self):
        super().setUp()
        home = self.home.path
        (home / ".gitignore").write_text(".spud/\n.user-claude/\n.user-config/\n", encoding="utf-8")
        git(home, "init", "-q", "-b", "main")
        git(home, "add", ".gitignore", "spud.config.json")
        git(home, "commit", "-q", "-m", "home")
        self.origin = self.scratch_dir("home-origin-")
        git(self.origin, "init", "-q", "--bare", "-b", "main")
        git(home, "remote", "add", "origin", self.origin)
        git(home, "push", "-q", "-u", "origin", "main")
        self.t = self.new_ticket("Ledger", status="active")

    def commit(self, message="SPD-001: created", *extra, cwd=None, check=False):
        return self.cli("ledger", "commit", "--message", message, *extra, actor="spud", cwd=cwd or self.home.path, check=check)

    def head(self, repo=None):
        return git(repo or self.home.path, "rev-parse", "HEAD").strip()

    def test_it_commits_only_the_ledger_and_reports_and_pushes(self):
        (self.home.path / "notes.txt").write_text("not the ledger's\n", encoding="utf-8")
        (self.home.path / "CLAUDE.md").write_text("Spud's own file, modified and unstaged\n", encoding="utf-8")
        proc = self.commit("SPD-001: created\n\nThe body is kept whole.\n")
        self.assertEqual(proc.returncode, EXIT_OK, proc)
        self.assertIn("pushed", proc.stdout)
        files = git(self.home.path, "show", "--name-only", "--format=", "HEAD").split()
        self.assertTrue(files)
        self.assertTrue(all(f.startswith("ledger/") or f.startswith("reports/") for f in files), files)
        self.assertIn("ledger/tickets/SPD-001.md", files)
        self.assertIn("ledger/Projects.md", files)
        self.assertEqual(git(self.home.path, "log", "-1", "--format=%B").strip(), "SPD-001: created\n\nThe body is kept whole.")
        self.assertEqual(git(self.origin, "rev-parse", "main").strip(), self.head())
        status = git(self.home.path, "status", "--porcelain")
        self.assertIn("?? notes.txt", status)
        self.assertIn("?? CLAUDE.md", status)
        event = self.home.json("events", "--kind", "commit")["events"][-1]
        self.assertEqual((event["data"]["sha"], event["data"]["branch"], event["data"]["pushed"]), (self.head(), "main", True))
        self.assertEqual(sorted(event["data"]["files"]), sorted(files))

    def test_nothing_to_commit_exits_0(self):
        self.assertEqual(self.commit().returncode, EXIT_OK)
        before = self.head()
        proc = self.commit("SPD-001: again")
        self.assertEqual((proc.returncode, proc.stdout.strip()), (EXIT_OK, "nothing to commit"))
        self.assertEqual(self.head(), before)

    def test_the_subject_must_name_a_ticket(self):
        before = self.head()
        # git drops leading blank lines, so the subject is the first line with text; a key only in the body does not count
        for message in ("update the ledger", "SPD-1: short number", "spd-001: lower case", "render\n\nSPD-001 only in the body"):
            with self.subTest(message=message):
                proc = self.commit(message)
                self.assertEqual(proc.returncode, EXIT_USAGE, proc)
                self.assertIn("must name its ticket", proc.stderr)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.commit("BAD-001: another project's key counts too").returncode, EXIT_OK)

    def test_refused_from_a_linked_worktree_of_the_home_or_of_another_repository(self):
        before = self.head()
        for cwd in (self.add_worktree(self.home.path, "spd-001"), self.add_worktree(self.make_repo("badtakes-"), "bad-001")):
            with self.subTest(cwd=str(cwd)):
                proc = self.commit(cwd=cwd)
                self.assertEqual(proc.returncode, EXIT_ERROR, proc)
                self.assertIn("ExitWorktree (keep) first", proc.stderr)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.commit(cwd=self.scratch_dir("no-repo-")).returncode, EXIT_OK)  # a directory in no repository is no worktree

    def test_refused_off_the_default_branch(self):
        git(self.home.path, "switch", "-q", "-c", "side")
        proc = self.commit()
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("not its default branch main", proc.stderr)

    def test_refused_with_another_path_already_staged(self):
        (self.home.path / "CLAUDE.md").write_text("staged by hand\n", encoding="utf-8")
        git(self.home.path, "add", "CLAUDE.md")
        before = self.head()
        proc = self.commit()
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("already staged outside ledger/ and reports/: CLAUDE.md", proc.stderr)
        self.assertEqual(self.head(), before)
        self.assertFalse((self.home.path / "ledger").exists())  # refused before the render

    def test_a_render_conflict_exits_6_and_commits_nothing(self):
        self.assertEqual(self.commit().returncode, EXIT_OK)
        path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        path.write_text(path.read_text(encoding="utf-8").replace("## Brief\n", "## Brief\nA hand edit.\n"), encoding="utf-8")
        self.home.json("ticket", "edit", "SPD-001", "--title", "Renamed", actor="spud")
        before = self.head()
        proc = self.commit("SPD-001: renamed")
        self.assertEqual(proc.returncode, EXIT_CONFLICT, proc)
        self.assertEqual(self.head(), before)
        self.assertEqual(git(self.home.path, "diff", "--cached", "--name-only"), "")

    def test_no_push_and_a_failed_push(self):
        remote = git(self.origin, "rev-parse", "main").strip()
        proc = self.commit("SPD-001: local only", "--no-push")
        self.assertEqual(proc.returncode, EXIT_OK, proc)
        self.assertIn("not pushed", proc.stdout)
        self.assertEqual(git(self.origin, "rev-parse", "main").strip(), remote)
        self.assertFalse(self.home.json("events", "--kind", "commit")["events"][-1]["data"]["pushed"])
        git(self.home.path, "remote", "set-url", "origin", self.home.path.parent / "no-such-origin.git")
        self.home.json("ticket", "edit", "SPD-001", "--title", "Changed again", actor="spud")
        proc = self.commit("SPD-001: push fails")
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        self.assertIn("push failed (the commit is kept)", proc.stderr)
        self.assertEqual(git(self.home.path, "log", "-1", "--format=%s").strip(), "SPD-001: push fails")

    def test_only_spud_commits_the_ledger(self):
        m = self.new_member(self.t["key"])
        proc = self.cli("ledger", "commit", "--message", "SPD-001: x", actor=m["ref"], check=False)
        self.assertEqual(proc.returncode, 3, proc)


if __name__ == "__main__":
    unittest.main()
