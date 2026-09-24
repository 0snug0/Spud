"""spud project add|list|show|edit|remove, a ticket's project, the rendered `project` property and ledger/Projects.md
(SPD-014, docs/design/2026-09-14-cross-repository-projects.md sections 1, 3.3 and 4).  The other repository stands in for
BadTakes with the key badtakes and the prefixes BAD / BADS (Eric, 2026-09-14)."""

import json
import sqlite3
import unittest
from pathlib import Path

from helpers import EXIT_ERROR, EXIT_OK, EXIT_OWNERSHIP, EXIT_USAGE, Home, RepoMixin, SpudTestCase, git, load_spud_module

spud = load_spud_module()


class ProjectAddTest(RepoMixin, SpudTestCase):
    def setUp(self):
        super().setUp()
        self.other = self.make_repo("badtakes-")

    def refused(self, *args, needle, code=EXIT_ERROR, **kw):
        proc = self.cli(*args, check=False, **kw)
        self.assertEqual(proc.returncode, code, proc)
        self.assertIn(needle, proc.stderr)
        return proc

    def test_add_registers_the_project_with_claim_sessions_and_installs_nothing(self):
        out = self.cli_json("project", "add", self.other, "--key", "badtakes", "--ticket-prefix", "BAD", "--team-prefix", "BADS", "--landing", "pr",
                            actor="spud")["project"]
        self.assertEqual({k: out[k] for k in ("key", "name", "ticket_prefix", "team_prefix", "root", "default_branch", "landing", "sessions", "remote", "tickets", "installed")},
                         {"key": "badtakes", "name": self.other.name, "ticket_prefix": "BAD", "team_prefix": "BADS", "root": str(self.other), "default_branch": "main",
                          "landing": "pr", "sessions": "claim", "remote": None, "tickets": 0, "installed": False})
        self.assertFalse((self.other / ".claude").exists())
        self.assertEqual(git(self.other, "status", "--porcelain"), "")
        events = self.home.json("events", "--kind", "project.added")["events"]
        self.assertEqual([e["data"]["project"] for e in events], ["spud", "badtakes"])  # init registered project 1
        entries = self.home.json("events", "--kind", "report.entry")["events"]
        self.assertTrue(any("Project badtakes added" in e["data"]["title"] for e in entries), entries)

    def test_add_reads_the_remote_and_its_default_branch(self):
        repo = self.make_repo("trunked-", branch="trunk", origin=True)
        out = self.cli_json("project", "add", repo, "--key", "trunked", "--ticket-prefix", "TRK", "--team-prefix", "TRKS", "--landing", "merge", "--sessions", "always",
                            actor="spud")["project"]
        self.assertEqual((out["remote"], out["default_branch"], out["sessions"]), (str(self.origin), "trunk", "always"))
        out = self.cli_json("project", "add", self.other, "--key", "badtakes", "--ticket-prefix", "BAD", "--team-prefix", "BADS", "--landing", "pr",
                            "--default-branch", "develop", "--name", "Bad Takes", actor="spud")["project"]
        self.assertEqual((out["default_branch"], out["name"]), ("develop", "Bad Takes"))

    def test_rule_1_an_existing_directory(self):
        self.refused("project", "add", self.other / "nope", "--key", "x", "--ticket-prefix", "X", "--team-prefix", "XS", "--landing", "pr", actor="spud",
                     needle="not an existing directory")
        (self.other / "file.txt").write_text("x", encoding="utf-8")
        self.refused("project", "add", self.other / "file.txt", "--key", "x", "--ticket-prefix", "X", "--team-prefix", "XS", "--landing", "pr", actor="spud",
                     needle="not a directory")

    def test_rule_2_the_root_of_a_main_checkout(self):
        plain = self.scratch_dir("not-a-repo-")
        self.refused("project", "add", plain, "--key", "x", "--ticket-prefix", "X", "--team-prefix", "XS", "--landing", "pr", actor="spud", needle="not a git repository")
        (self.other / "src").mkdir()
        self.refused("project", "add", self.other / "src", "--key", "x", "--ticket-prefix", "X", "--team-prefix", "XS", "--landing", "pr", actor="spud", needle="not its root")
        wt = self.add_worktree(self.other, "feature")
        proc = self.refused("project", "add", wt, "--key", "x", "--ticket-prefix", "X", "--team-prefix", "XS", "--landing", "pr", actor="spud", needle="linked worktree")
        self.assertIn("register the main checkout, %s" % self.other, proc.stderr)

    def test_rule_3_not_the_home_and_never_nested(self):
        self.refused("project", "add", self.home.path, "--key", "x", "--ticket-prefix", "X", "--team-prefix", "XS", "--landing", "pr", actor="spud", needle="Spud's home")
        inner = self.home.tool / "vendor" / "lib"
        inner.mkdir(parents=True)
        git(inner, "init", "-q", "-b", "main")
        self.refused("project", "add", inner, "--key", "x", "--ticket-prefix", "X", "--team-prefix", "XS", "--landing", "pr", actor="spud", needle="inside project spud's root")
        outer = self.scratch_dir("outer-")
        git(outer, "init", "-q", "-b", "main")
        child = outer / "child"
        child.mkdir()
        git(child, "init", "-q", "-b", "main")
        self.add_project(child, "child", "CHD", "CHDS")
        self.refused("project", "add", outer, "--key", "outer", "--ticket-prefix", "OUT", "--team-prefix", "OUTS", "--landing", "pr", actor="spud",
                     needle="contains project child's root")

    def test_project_root_shape_answers_the_first_refusals_with_no_connection(self):
        """SPW-001: the seam of rules 1 and 2, which `spud init` will check a candidate root with before it has created
        the database -- so it takes no connection, and the registry scan validate_project_root adds needs one."""
        ctx = spud.Ctx(self.home.path, "SPUD_HOME", False, tool=self.home.path)
        self.assertEqual(spud.project_root_shape(ctx, self.other), str(self.other))
        for path, needle in ((self.other / "nope", "not an existing directory"), (self.home.path, "Spud's home"),
                             (self.scratch_dir("plain-"), "not a git repository")):
            with self.subTest(path=path):
                with self.assertRaises(spud.SpudError) as caught:
                    spud.project_root_shape(ctx, path)
                self.assertIn(needle, caught.exception.message)

    def test_rule_4_the_key(self):
        for key, needle in (("Bad", "lower-case"), ("1bad", "lower-case"), ("spud", "exists already"), ("home", "reserved"), ("b" * 33, "lower-case")):
            with self.subTest(key=key):
                self.refused("project", "add", self.other, "--key", key, "--ticket-prefix", "BAD", "--team-prefix", "BADS", "--landing", "pr", actor="spud", needle=needle)
        self.add_project(self.other)
        second = self.make_repo("second-")
        self.refused("project", "add", second, "--key", "badtakes", "--ticket-prefix", "SEC", "--team-prefix", "SECS", "--landing", "pr", actor="spud", needle="exists already")

    def test_rule_5_the_prefixes(self):
        for tp, tm, needle in (("bad", "BADS", "upper-case"), ("BAD", "BAD", "must differ"), ("SPD", "BADS", "project spud's"), ("BAD", "SPUD", "project spud's")):
            with self.subTest(ticket_prefix=tp, team_prefix=tm):
                self.refused("project", "add", self.other, "--key", "badtakes", "--ticket-prefix", tp, "--team-prefix", tm, "--landing", "pr", actor="spud", needle=needle)
        self.add_project(self.other)
        second = self.make_repo("second-")
        self.refused("project", "add", second, "--key", "second", "--ticket-prefix", "BADS", "--team-prefix", "SECS", "--landing", "pr", actor="spud",
                     needle="project badtakes's")

    def test_rule_6_landing_is_required_and_only_spud_adds(self):
        proc = self.cli("project", "add", self.other, "--key", "badtakes", "--ticket-prefix", "BAD", "--team-prefix", "BADS", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE)
        t = self.new_ticket("Home ticket", status="active")
        m = self.new_member(t["key"])
        proc = self.cli("project", "add", self.other, "--key", "badtakes", "--ticket-prefix", "BAD", "--team-prefix", "BADS", "--landing", "pr", actor=m["ref"], check=False)
        self.assertEqual(proc.returncode, 3, proc)

    def test_list_and_show(self):
        self.add_project(self.other)
        rows = self.cli_json("project", "list")["projects"]
        self.assertEqual([r["key"] for r in rows], ["spud", "badtakes"])
        self.assertEqual(rows[0]["root"], str(self.home.tool))
        text = self.cli("project", "show", "badtakes").stdout
        self.assertIn("BAD-nnn tickets, BADS-nnn teams", text)
        self.assertIn("installed       no", text)
        self.assertEqual(self.cli("project", "show", "nope", check=False).returncode, EXIT_ERROR)

    def test_show_reads_installed_from_the_settings_file_itself(self):
        """`installed` is settings_hold_hooks over the project's local settings, whose reading SPW-003 moved into
        projects/sessions so that `session show` could ask the same question: it still takes the whole hook table, so a
        file one event short reads as not installed, here as in doctor."""
        self.home.agent_source("---\nname: spudagent\n---\nRun `{{launcher}}`.\n")  # SPW-004: share/agents/, under the tool
        self.add_project(self.other)
        self.cli("project", "install", "badtakes", actor="spud")
        self.assertTrue(self.cli_json("project", "show", "badtakes")["project"]["installed"])
        self.assertIn("installed       yes", self.cli("project", "show", "badtakes").stdout)
        settings = self.other / ".claude" / "settings.local.json"
        data = json.loads(settings.read_text(encoding="utf-8"))
        del data["hooks"]["SubagentStart"]
        settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.assertFalse(self.cli_json("project", "show", "badtakes")["project"]["installed"])


class ProjectEditRemoveTest(RepoMixin, SpudTestCase):
    def setUp(self):
        super().setUp()
        self.other = self.make_repo("badtakes-")
        self.add_project(self.other)

    def test_edit_fields_root_and_prefixes_until_the_first_ticket(self):
        moved = self.make_repo("moved-")
        out = self.cli_json("project", "edit", "badtakes", "--root", moved, "--landing", "merge", "--sessions", "always", "--ticket-prefix", "BT", "--team-prefix", "BTS",
                            actor="spud")
        self.assertEqual(sorted(out["changed"]), ["landing", "root_path", "sessions", "team_prefix", "ticket_prefix"])
        self.assertEqual((out["project"]["root"], out["project"]["ticket_prefix"]), (str(moved), "BT"))
        self.assertEqual(self.cli("project", "edit", "badtakes", "--root", self.add_worktree(moved, "wt"), actor="spud", check=False).returncode, EXIT_ERROR)
        self.cli("ticket", "new", "--project", "badtakes", "--title", "First", actor="spud")
        proc = self.cli("project", "edit", "badtakes", "--ticket-prefix", "BAD", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("has tickets", proc.stderr)
        self.assertEqual(len(self.home.json("events", "--kind", "project.edited")["events"]), 1)

    def test_project_spud_keeps_its_name_and_prefixes(self):
        for args, needle in ((["--name", "X"], "spud.config.json"), (["--ticket-prefix", "ZZ"], "spud.config.json")):
            with self.subTest(args=args):
                proc = self.cli("project", "edit", "spud", *args, actor="spud", check=False)
                self.assertEqual(proc.returncode, EXIT_ERROR, proc)
                self.assertIn(needle, proc.stderr)
        self.assertEqual(self.cli_json("project", "edit", "spud", "--default-branch", "trunk", actor="spud")["changed"], ["default_branch"])
        self.assertEqual(self.cli_json("project", "edit", "spud", "--sessions", "always", actor="spud")["changed"], ["sessions"])

    def test_remove_deletes_an_empty_project_and_archives_one_with_tickets(self):
        out = self.cli_json("project", "remove", "badtakes", actor="spud")
        self.assertFalse(out["archived"])
        self.assertEqual([p["key"] for p in self.cli_json("project", "list")["projects"]], ["spud"])
        self.add_project(self.other)
        t = self.cli_json("ticket", "new", "--project", "badtakes", "--title", "Open", "--status", "active", actor="spud")["ticket"]
        proc = self.cli("project", "remove", "badtakes", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn(t["key"], proc.stderr)
        self.cli("ticket", "move", t["key"], "--status", "parked", "--reason", "Eric's go", actor="spud")
        proc = self.cli("project", "remove", "badtakes", actor="spud", check=False)  # SPD-096: parked is open
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn(t["key"], proc.stderr)
        self.cli("ticket", "move", t["key"], "--status", "active", actor="spud")
        self.cli("ticket", "move", t["key"], "--status", "done", actor="spud")
        out = self.cli_json("project", "remove", "badtakes", actor="spud")
        self.assertTrue(out["archived"])
        row = self.home.rows("SELECT archived_at FROM projects WHERE key = 'badtakes'")[0]
        self.assertIsNotNone(row["archived_at"])
        proc = self.cli("ticket", "new", "--project", "badtakes", "--title", "x", actor="spud", check=False)
        self.assertIn("archived", proc.stderr)
        proc = self.cli("project", "remove", "spud", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("never removed", proc.stderr)
        # an archived project's prefixes stay taken
        second = self.make_repo("second-")
        proc = self.add_project(second, "second", "BAD", "SECS", check=False)
        self.assertIn("project badtakes's", proc.stderr)


class ProjectScriptsTest(RepoMixin, SpudTestCase):
    """SPD-145: a project's allow-list of repository scripts, which the Bash hook lets a member of that project's tickets run
    (tests/test_hooks_programs.py ScriptFileTest): set by Spud with `project edit --allow-script/--drop-script`, kept in
    projects.scripts, and printed by `project show` and `project list`."""

    def setUp(self):
        super().setUp()
        self.other = self.make_repo("badtakes-")
        for rel in ("scripts/worktree-init.sh", "scripts/seed.sh", "tools/b.sh"):
            (self.other / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.other / rel).write_text("#!/bin/sh\n", encoding="utf-8")
        self.add_project(self.other)

    def scripts(self):
        return json.loads(self.home.scalar("SELECT scripts FROM projects WHERE key = 'badtakes'"))

    def test_a_new_project_allows_no_script(self):
        self.assertEqual(self.scripts(), [])
        self.assertEqual(self.cli_json("project", "show", "badtakes")["project"]["scripts"], [])
        self.assertIn("scripts         -", self.cli("project", "show", "badtakes").stdout)

    def test_spud_allows_and_drops_scripts(self):
        out = self.cli_json("project", "edit", "badtakes", "--allow-script", "scripts/worktree-init.sh", "--allow-script", "tools/b.sh", actor="spud")
        self.assertEqual(out["changed"], ["scripts"])
        self.assertEqual(out["project"]["scripts"], ["scripts/worktree-init.sh", "tools/b.sh"])
        self.assertEqual(self.scripts(), ["scripts/worktree-init.sh", "tools/b.sh"])
        self.assertIn("scripts         scripts/worktree-init.sh, tools/b.sh", self.cli("project", "show", "badtakes").stdout)
        listing = self.cli("project", "list").stdout
        self.assertIn("scripts", listing.splitlines()[0])
        self.assertIn("scripts/worktree-init.sh, tools/b.sh", listing)
        self.assertEqual(self.cli_json("project", "edit", "badtakes", "--allow-script", "tools/b.sh", actor="spud")["changed"], [])  # already there
        out = self.cli_json("project", "edit", "badtakes", "--drop-script", "tools/b.sh", "--allow-script", "scripts/seed.sh", actor="spud")
        self.assertEqual(out["project"]["scripts"], ["scripts/seed.sh", "scripts/worktree-init.sh"])
        events = self.home.json("events", "--kind", "project.edited")["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1]["data"]["fields"], ["scripts"])
        self.assertEqual(json.loads(events[-1]["data"]["to"]["scripts"]), ["scripts/seed.sh", "scripts/worktree-init.sh"])

    def test_a_path_that_is_no_repository_script_is_refused(self):
        for path, code, needle in (("/etc/profile", EXIT_USAGE, "not a repository path"), ("../x.sh", EXIT_USAGE, "not a repository path"),
                                   ("./scripts/seed.sh", EXIT_USAGE, "not a repository path"), ("scripts/", EXIT_USAGE, "not a repository path"),
                                   ("~/x.sh", EXIT_USAGE, "not a repository path"), (".git/hooks/pre-commit", EXIT_USAGE, "not a repository path"),
                                   (".spud/x.sh", EXIT_USAGE, "not a repository path"), ("a,b.sh", EXIT_USAGE, "not a repository path"),
                                   ("scripts/missing.sh", EXIT_ERROR, "no file in project badtakes's main checkout")):
            with self.subTest(path):
                proc = self.cli("project", "edit", "badtakes", "--allow-script", path, actor="spud", check=False)
                self.assertEqual(proc.returncode, code, proc)
                self.assertIn(needle, proc.stderr)
        proc = self.cli("project", "edit", "badtakes", "--drop-script", "scripts/seed.sh", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("allows no script scripts/seed.sh", proc.stderr)
        self.assertEqual(self.scripts(), [])

    def test_a_member_may_not_set_the_list(self):
        t = self.new_ticket("Home", status="active")
        m = self.new_member(t["key"])
        proc = self.cli("project", "edit", "badtakes", "--allow-script", "scripts/seed.sh", actor=m["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP, proc)
        self.assertIn("Spud's", proc.stderr)
        self.assertEqual(self.scripts(), [])

    def test_the_column_refuses_anything_but_a_json_list(self):
        con = self.home.connect()
        try:
            for value in ("x", "{}", "\"a\""):
                with self.subTest(value):
                    with self.assertRaises(sqlite3.IntegrityError):
                        with con:
                            con.execute("UPDATE projects SET scripts = ? WHERE key = 'badtakes'", (value,))
        finally:
            con.close()

    def test_projects_md_carries_the_list_and_imports_it_back(self):
        self.cli("project", "edit", "badtakes", "--allow-script", "scripts/seed.sh", "--allow-script", "tools/b.sh", actor="spud")
        self.home.json("render", actor="spud")
        text = (self.home.path / "ledger" / "Projects.md").read_text(encoding="utf-8")
        self.assertIn("| Archived | Scripts |", text)
        self.assertIn("| scripts/seed.sh, tools/b.sh |", text)
        fresh = Home()
        self.addCleanup(fresh.cleanup)
        fresh.init()
        con = fresh.connect()
        try:
            with con:
                inserted = spud.import_projects_file(con, "2026-09-22T10:00:00-07:00", self.home.path / "ledger" / "Projects.md", "ledger/Projects.md")
        finally:
            con.close()
        self.assertEqual(inserted, 1)
        self.assertEqual(json.loads(fresh.scalar("SELECT scripts FROM projects WHERE key = 'badtakes'")), ["scripts/seed.sh", "tools/b.sh"])

    def test_a_projects_md_from_before_the_list_imports_with_none(self):
        self.home.json("render", actor="spud")
        path = self.home.path / "ledger" / "Projects.md"
        # every table line without its last two cells, the Scripts column 0007_project_scripts added and the Runners
        # column 0008_project_runners added
        old = path.read_text(encoding="utf-8")
        for _ in range(2):
            old = "\n".join(line[: line[:-1].rstrip().rfind("|") + 1] if line.startswith("|") else line for line in old.split("\n"))
        self.assertIn("| Remote | Archived |\n", old)
        legacy = self.home.path / "legacy-Projects.md"
        legacy.write_text(old, encoding="utf-8")
        fresh = Home()
        self.addCleanup(fresh.cleanup)
        fresh.init()
        con = fresh.connect()
        try:
            with con:
                self.assertEqual(spud.import_projects_file(con, "2026-09-22T10:00:00-07:00", legacy, "legacy-Projects.md"), 1)
        finally:
            con.close()
        self.assertEqual(fresh.scalar("SELECT scripts FROM projects WHERE key = 'badtakes'"), "[]")


class ProjectRunnersTest(RepoMixin, SpudTestCase):
    """SPD-168: a project's allow-list of runner names -- npm scripts, deno tasks, make targets -- which the Bash hook lets
    a member of that project's tickets run through a script runner (tests/test_hooks_programs.py ScriptRunnerTest): set by Spud with
    `project edit --allow-runner/--drop-runner`, kept in projects.runners, printed by `project show` and `project list`,
    and carried by Projects.md."""

    def setUp(self):
        super().setUp()
        self.other = self.make_repo("badtakes-")
        self.add_project(self.other)

    def runners(self):
        return json.loads(self.home.scalar("SELECT runners FROM projects WHERE key = 'badtakes'"))

    def test_a_new_project_allows_no_name(self):
        self.assertEqual(self.runners(), [])
        self.assertEqual(self.cli_json("project", "show", "badtakes")["project"]["runners"], [])
        self.assertIn("runners         -", self.cli("project", "show", "badtakes").stdout)

    def test_spud_allows_and_drops_names(self):
        out = self.cli_json("project", "edit", "badtakes", "--allow-runner", "test", "--allow-runner", "check:functions", actor="spud")
        self.assertEqual(out["changed"], ["runners"])
        self.assertEqual(out["project"]["runners"], ["check:functions", "test"])
        self.assertEqual(self.runners(), ["check:functions", "test"])
        self.assertIn("runners         check:functions, test", self.cli("project", "show", "badtakes").stdout)
        listing = self.cli("project", "list").stdout
        self.assertIn("runners", listing.splitlines()[0])
        self.assertIn("check:functions, test", listing)
        self.assertEqual(self.cli_json("project", "edit", "badtakes", "--allow-runner", "test", actor="spud")["changed"], [])
        out = self.cli_json("project", "edit", "badtakes", "--drop-runner", "check:functions", "--allow-runner", "web:build", actor="spud")
        self.assertEqual(out["project"]["runners"], ["test", "web:build"])
        events = self.home.json("events", "--kind", "project.edited")["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1]["data"]["fields"], ["runners"])

    def test_a_name_no_runner_would_read_as_one_name_is_refused(self):
        for name in ("", "-x", "a b", "a,b", "build-*", "t?", "[x]", "$X", "a`b`", "a'b", 'a"b', "a\\b", "{a,b}"):
            with self.subTest(name):
                proc = self.cli("project", "edit", "badtakes", "--allow-runner=" + name, actor="spud", check=False)
                self.assertEqual(proc.returncode, EXIT_USAGE, proc)
                self.assertIn("not a runner name", proc.stderr)
        proc = self.cli("project", "edit", "badtakes", "--drop-runner", "test", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("allows no runner name test", proc.stderr)
        self.assertEqual(self.runners(), [])

    def test_a_member_may_not_set_the_list(self):
        t = self.new_ticket("Home", status="active")
        m = self.new_member(t["key"])
        proc = self.cli("project", "edit", "badtakes", "--allow-runner", "test", actor=m["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP, proc)
        self.assertEqual(self.runners(), [])

    def test_projects_md_carries_the_list_and_imports_it_back(self):
        self.cli("project", "edit", "badtakes", "--allow-runner", "test", "--allow-runner", "lint", actor="spud")
        self.home.json("render", actor="spud")
        path = self.home.path / "ledger" / "Projects.md"
        text = path.read_text(encoding="utf-8")
        self.assertIn("| Archived | Scripts | Runners |", text)
        self.assertIn("|  | lint, test |", text)
        # and a Projects.md from before 0008, with no Runners column, imports with none allowed
        v7 = "\n".join(line[: line[:-1].rstrip().rfind("|") + 1] if line.startswith("|") else line for line in text.split("\n"))
        self.assertIn("| Archived | Scripts |\n", v7)
        for source, expected in ((path, ["lint", "test"]), (v7, [])):
            fresh = Home()
            self.addCleanup(fresh.cleanup)
            fresh.init()
            if isinstance(source, str):
                legacy = fresh.path / "legacy-Projects.md"
                legacy.write_text(source, encoding="utf-8")
                source = legacy
            con = fresh.connect()
            try:
                with con:
                    self.assertEqual(spud.import_projects_file(con, "2026-09-22T10:00:00-07:00", source, "ledger/Projects.md"), 1)
            finally:
                con.close()
            self.assertEqual(json.loads(fresh.scalar("SELECT runners FROM projects WHERE key = 'badtakes'")), expected)


class TicketProjectTest(RepoMixin, SpudTestCase):
    def setUp(self):
        super().setUp()
        self.other = self.make_repo("badtakes-")
        self.add_project(self.other)
        self.wt = self.add_worktree(self.other, "bad-002")

    def test_ticket_new_numbers_each_project_from_the_working_directory(self):
        first = self.cli("ticket", "new", "--title", "From the root", actor="spud", cwd=self.other).stdout
        self.assertTrue(first.startswith("BAD-001 (BADS-001, badtakes) created: From the root [queued]"), first)
        second = self.cli_json("ticket", "new", "--title", "From a worktree", actor="spud", cwd=self.wt / ".")["ticket"]
        self.assertEqual((second["key"], second["team_key"], second["project"]), ("BAD-002", "BADS-002", "badtakes"))
        home = self.cli_json("ticket", "new", "--title", "From the home", actor="spud", cwd=self.home.path)["ticket"]
        self.assertEqual((home["key"], home["project"]), ("SPD-001", "spud"))
        outside = self.cli_json("ticket", "new", "--title", "From nowhere", actor="spud", cwd=self.scratch_dir("nowhere-"))["ticket"]
        self.assertEqual(outside["key"], "SPD-002")
        explicit = self.cli_json("ticket", "new", "--project", "spud", "--title", "Explicit", actor="spud", cwd=self.other)["ticket"]
        self.assertEqual(explicit["key"], "SPD-003")
        self.assertEqual(self.cli("ticket", "new", "--project", "nope", "--title", "x", actor="spud", check=False).returncode, EXIT_ERROR)

    def test_a_proposal_from_a_bad_ticket_creates_a_bad_ticket(self):
        t = self.cli_json("ticket", "new", "--project", "badtakes", "--title", "Origin", "--status", "active", actor="spud")["ticket"]
        m = self.cli_json("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "Look.", "--deliverable", "src/**", actor="spud", cwd=self.wt)["member"]
        self.cli("member", "start", m["ref"], actor="spud")
        p = self.cli_json("proposal", "file", "--title", "Follow-up", "--why", "Found it.", actor=m["ref"])["proposal"]
        out = self.cli_json("proposal", "decide", str(p["id"]), "--decision", "create", "--priority", "P2", actor="spud")
        self.assertEqual((out["ticket"]["key"], out["ticket"]["project"]), ("BAD-002", "badtakes"))

    def test_qualified_deliverables(self):
        t = self.cli_json("ticket", "new", "--project", "badtakes", "--title", "Globs", "--status", "active", actor="spud")["ticket"]
        m = self.cli_json("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "x",
                          "--deliverable", "src/", "--deliverable", "home:docs/x.md", "--deliverable", "./test/**", actor="spud", cwd=self.wt)["member"]
        self.assertEqual(m["deliverables"], ["src/**", "home:docs/x.md", "test/**"])
        for bad, needle in (("nope:x.md", "no active project"), ("spud:/abs", "not absolute"), ("spud:../x", "`..`"), ("badtakes:", "non-empty")):
            with self.subTest(bad=bad):
                proc = self.cli("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", "--deliverable", bad, actor="spud", check=False)
                self.assertEqual(proc.returncode, EXIT_ERROR, proc)
                self.assertIn(needle, proc.stderr)
        proc = self.cli("member", "edit", m["ref"], "--deliverable", "gone:x", actor="spud", check=False)
        self.assertIn("no active project", proc.stderr)


class ProjectRenderTest(RepoMixin, SpudTestCase):
    def setUp(self):
        super().setUp()
        self.other = self.make_repo("bad|takes-")
        self.add_project(self.other)
        self.home_ticket = self.new_ticket("Home", status="active")
        self.bad = self.cli_json("ticket", "new", "--project", "badtakes", "--title", "Bad one", "--status", "active", actor="spud")["ticket"]
        self.member = self.cli_json("member", "new", "--ticket", self.bad["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", "--name", "Russet",
                                    "--deliverable", "src/**", actor="spud", cwd=self.add_worktree(self.other, "bad-001"))["member"]  # SPD-098: binds it

    def test_render_writes_the_project_property_and_projects_md(self):
        out = self.home.json("render", actor="spud")
        self.assertIn("ledger/tickets/BAD-001.md", out["written"])
        self.assertIn("ledger/teams/BADS-001/Russet.md", out["written"])
        self.assertIn("ledger/Projects.md", out["written"])
        ticket = (self.home.path / "ledger" / "tickets" / "BAD-001.md").read_text(encoding="utf-8")
        self.assertIn("\nstatus: active\norigin: owner\nproject: badtakes\nproposed_by: \"\"\n", ticket)
        self.assertIn("\nproject: spud\n", (self.home.path / "ledger" / "tickets" / "SPD-001.md").read_text(encoding="utf-8"))
        member = (self.home.path / "ledger" / "teams" / "BADS-001" / "Russet.md").read_text(encoding="utf-8")
        self.assertIn('\nticket: "[[BAD-001]]"\nproject: badtakes\nstatus: planned\n', member)
        projects = (self.home.path / "ledger" / "Projects.md").read_text(encoding="utf-8")
        self.assertTrue(projects.startswith("---\ntags: [projects]\n---\n<!-- generated"), projects)
        self.assertIn("| badtakes | %s | BAD | BADS | %s | main | pr | claim |  |  |" % (self.other.name.replace("|", "\\|"), str(self.other).replace("|", "\\|")), projects)
        self.assertEqual(self.home.json("render", actor="spud")["written"], [])

    def test_import_file_refuses_project_edits_and_projects_md(self):
        self.home.json("render", actor="spud")
        path = self.home.path / "ledger" / "tickets" / "BAD-001.md"
        path.write_text(path.read_text(encoding="utf-8").replace("project: badtakes", "project: spud"), encoding="utf-8")
        proc = self.home.run("import", "--file", path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("project is not editable by hand", proc.stderr)
        self.home.run("render", "--discard", path, actor="spud")
        member = self.home.path / "ledger" / "teams" / "BADS-001" / "Russet.md"
        member.write_text(member.read_text(encoding="utf-8").replace("project: badtakes", "project: spud"), encoding="utf-8")
        proc = self.home.run("import", "--file", member, actor="spud", check=False)
        self.assertIn("project is not editable by hand", proc.stderr)
        proc = self.home.run("import", "--file", self.home.path / "ledger" / "Projects.md", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("never accepted by hand", proc.stderr)

    def test_import_of_a_rendered_tree_round_trips_both_projects(self):
        self.home.json("render", actor="spud")
        fresh = Home()
        self.addCleanup(fresh.cleanup)
        fresh.init()
        # SPW-001: both homes hold init's own report entry for today, and a bulk import refuses a day it already has, so
        # the tree this round trip takes is the ledger's.  (Proposal filed: `spud init` then `spud import` of a vault
        # rendered today, the disaster-recovery path, hits the same refusal.)
        refused = fresh.run("import", self.home.path / "ledger", self.home.path / "reports", check=False)
        self.assertEqual(refused.returncode, EXIT_ERROR)
        self.assertIn("already has entries in the ledger", refused.stderr)
        counts = fresh.json("import", self.home.path / "ledger")
        self.assertEqual((counts["projects"], counts["tickets"], counts["members"]), (1, 2, 1))
        cols = "key, name, root_path, ticket_prefix, team_prefix, default_branch, landing, sessions, remote, archived_at"
        self.assertEqual(fresh.rows("SELECT %s FROM projects WHERE key = 'badtakes'" % cols), self.home.rows("SELECT %s FROM projects WHERE key = 'badtakes'" % cols))
        self.assertEqual(fresh.rows("SELECT t.key, p.key AS project FROM tickets t JOIN projects p ON p.id = t.project_id ORDER BY t.key"),
                         [{"key": "BAD-001", "project": "badtakes"}, {"key": "SPD-001", "project": "spud"}])
        out = fresh.path / "out"
        fresh.json("render", "--out", out)
        for rel in ("ledger/tickets/BAD-001.md", "ledger/teams/BADS-001/Russet.md", "ledger/Projects.md"):
            mine = (self.home.path / rel).read_text(encoding="utf-8")
            theirs = (out / rel).read_text(encoding="utf-8")
            if rel.endswith("Projects.md"):  # the home row's root is each home's own, and project spud's each home's tool
                mine = mine.replace(str(self.home.path), "<home>").replace(str(self.home.tool), "<tool>")
                theirs = theirs.replace(str(fresh.path), "<home>").replace(str(fresh.tool), "<tool>")
            self.assertEqual(theirs, mine, rel)

    def test_board_card_and_events_by_project(self):
        board = self.cli("board").stdout
        self.assertIn("project", board.split("\n")[0])
        rows = self.cli_json("board", "--project", "badtakes")["tickets"]
        self.assertEqual([r["key"] for r in rows], ["BAD-001"])
        self.assertEqual(self.cli("board", "--brief", "--project", "spud").stdout.split()[0], "SPD-001")
        kinds = {e["kind"] for e in self.cli_json("events", "--project", "badtakes")["events"]}
        self.assertLessEqual({"project.added", "ticket.created", "member.planned"}, kinds)
        self.assertNotIn("SPD-001", json.dumps(self.cli_json("events", "--project", "badtakes")["events"]))
        self.assertEqual(self.cli("card", "BAD-001", "--project", "badtakes").returncode, EXIT_OK)
        proc = self.cli("card", "BAD-001", "--project", "spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertEqual(self.cli("board", "--project", "nope", check=False).returncode, EXIT_ERROR)


if __name__ == "__main__":
    unittest.main()
