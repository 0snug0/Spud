"""init, migrate, backup, config sync, doctor, and the CLI's error shapes."""

import json
import os
import re
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import (
    EXIT_ERROR,
    EXIT_OWNERSHIP,
    EXIT_USAGE,
    Home,
    RepoMixin,
    SpudTestCase,
    init_report_day,
    isolated_git_env,
    load_spud_module,
    real_config,
)

SESSION = "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b"
# SPW-001: what `spud init` writes into a home besides the database (design sections 2.2 and 4.1).
SCAFFOLDING = ("CLAUDE.md", "ledger/Home.md", "ledger/Spud.md", "ledger/Board.base", "ledger/Fleet.base",
               "ledger/_templates/ticket.md", "ledger/_templates/spudagent.md")
DIRECTORIES = ("ledger/tickets", "ledger/teams", "reports", "docs/spikes", "docs/design")

TABLES = {
    "projects",
    "tickets",
    "members",
    "spawn_requests",
    "events",
    "handoffs",
    "proposals",
    "proposal_decisions",
    "name_pool",
    "renders",
    "imported_sections",
    "sessions",  # SPD-014, migration 0002_projects
    "pull_requests",  # SPD-077, migration 0005_pull_requests
}
SCHEMA = 5  # user_version since SPD-077


class InitTest(SpudTestCase):
    warm_cache = False  # SPD-102: init runs as it does in a new home, before any .spud/ exists

    def test_init_creates_a_wal_database_under_spud_home(self):
        self.assertTrue(self.home.db.exists())
        self.assertEqual(self.home.scalar("PRAGMA user_version"), SCHEMA)
        self.assertEqual(self.home.scalar("PRAGMA journal_mode"), "wal")

    def test_init_is_idempotent(self):
        again = self.home.init()
        self.assertTrue(again["ok"])
        self.assertFalse(again["created"])
        self.assertEqual(again["user_version"], SCHEMA)
        self.assertEqual(again["applied"], [])

    def test_init_refuses_a_database_written_by_a_newer_cli(self):
        con = sqlite3.connect(self.home.db)
        con.execute("PRAGMA user_version = 99")
        con.commit()
        con.close()
        proc = self.home.run("init", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("ahead", proc.stderr.lower())
        proc = self.home.run("board", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)

    def test_migrate_reports_up_to_date(self):
        out = self.home.json("migrate")
        self.assertTrue(out["ok"])
        self.assertEqual(out["applied"], [])
        self.assertEqual(out["user_version"], SCHEMA)

    def test_schema_tables_are_strict_and_complete(self):
        rows = self.home.rows("SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
        names = {r["name"] for r in rows}
        self.assertEqual(names, TABLES)
        for r in rows:
            self.assertTrue(r["sql"].rstrip().endswith("STRICT"), r["name"])
        views = {r["name"] for r in self.home.rows("SELECT name FROM sqlite_master WHERE type = 'view'")}
        self.assertEqual(views, {"v_board", "v_fleet", "v_live"})
        triggers = {r["name"] for r in self.home.rows("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        self.assertEqual(triggers, {"events_no_update", "events_no_delete"})

    def test_project_1_carries_the_configs_prefixes(self):
        # SPW-001: the row is the suite's seed (helpers.Home.init), not init's -- init registers no project since phase 2
        # of docs/design/2026-09-21-spud-init.md, and EmptyRegistryTest below is the home it leaves.  What this asserts is
        # the meaning of project 1 that survived: whatever project it is, its prefixes are spud.config.json's.
        row = self.home.rows("SELECT id, key, ticket_prefix, team_prefix, root_path FROM projects")
        self.assertEqual(
            row,
            [
                {
                    "id": 1,
                    "key": "spud",
                    "ticket_prefix": "SPD",
                    "team_prefix": "SPUD",
                    "root_path": str(self.home.path),
                }
            ],
        )

    def test_name_pool_mirrors_config(self):
        pool = real_config()["naming"]["pool"]
        rows = self.home.rows("SELECT name, active FROM name_pool ORDER BY name")
        self.assertEqual(sorted(r["name"] for r in rows), sorted(pool))
        self.assertTrue(all(r["active"] == 1 for r in rows))

    def test_config_sync_refreshes_pool_and_prefixes(self):
        cfg = real_config()
        cfg["naming"]["pool"].remove("Russet")
        cfg["naming"]["pool"].append("Tuber")
        cfg["tickets"]["prefix"] = "TKT"
        cfg["teams"]["prefix"] = "TEAM"
        with open(self.home.path / "spud.config.json", "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        out = self.home.json("config", "sync")
        self.assertTrue(out["ok"])
        # SPW-001: the line names project 1 by its key, since project 1 is no longer presumed to be `spud`
        self.assertEqual(out["project"], {"key": "spud", "ticket_prefix": "TKT", "team_prefix": "TEAM"})
        self.assertEqual(self.home.scalar("SELECT active FROM name_pool WHERE name = 'Russet'"), 0)
        self.assertEqual(self.home.scalar("SELECT active FROM name_pool WHERE name = 'Tuber'"), 1)
        self.assertEqual(self.home.scalar("SELECT ticket_prefix FROM projects WHERE id = 1"), "TKT")
        self.assertEqual(self.home.scalar("SELECT team_prefix FROM projects WHERE id = 1"), "TEAM")
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events WHERE kind = 'config.synced'"), 1)
        self.assertIn("project spud's prefixes TKT / TEAM", self.home.run("config", "sync").stdout)  # a second sync: the line

    def test_backup_writes_a_vacuumed_copy(self):
        out = self.home.json("backup")
        path = Path(out["path"])
        self.assertTrue(path.exists())
        self.assertEqual(path.parent, self.home.path / ".spud" / "backups")
        self.assertRegex(path.name, r"^ledger-\d{8}T\d{6}\.db$")
        con = sqlite3.connect(path)
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], SCHEMA)
        con.close()

    def test_doctor_reports_interpreter_sqlite_and_pragmas(self):
        out = self.home.json("doctor")
        self.assertTrue(out["ok"])
        self.assertEqual(Path(out["interpreter"]["path"]).resolve(), Path(sys.executable).resolve())
        self.assertTrue(out["interpreter"]["version"].startswith("3.14"))
        self.assertEqual(out["sqlite"]["library"], sqlite3.sqlite_version)
        self.assertEqual(out["spud_home"]["path"], str(self.home.path))
        self.assertEqual(out["spud_home"]["resolved_by"], "SPUD_HOME")
        db = out["database"]
        self.assertEqual(db["path"], str(self.home.db))
        self.assertTrue(db["exists"])
        self.assertEqual(db["user_version"], SCHEMA)
        self.assertEqual(db["journal_mode"], "wal")
        self.assertEqual(db["foreign_keys"], 1)
        self.assertEqual(db["busy_timeout"], 5000)
        self.assertEqual(db["synchronous"], 1)  # NORMAL
        self.assertEqual(out["problems"], [])
        text = self.home.run("doctor").stdout
        self.assertIn("python", text.lower())
        self.assertIn(sqlite3.sqlite_version, text)

    def test_doctor_flags_config_problems_with_nonzero_exit(self):
        cfg = real_config()
        cfg["naming"]["pool"].append(cfg["naming"]["pool"][0])
        cfg["limits"]["max_depth"] = "two"
        with open(self.home.path / "spud.config.json", "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        proc = self.home.run("--json", "doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        out = json.loads(proc.stdout)
        self.assertFalse(out["ok"])
        joined = " ".join(out["problems"]).lower()
        self.assertIn("pool", joined)
        self.assertIn("max_depth", joined)

    def test_json_error_shape(self):
        proc = self.home.run("--json", "ticket", "show", "SPD-999", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        out = json.loads(proc.stdout)
        self.assertFalse(out["ok"])
        self.assertEqual(out["exit"], EXIT_ERROR)
        self.assertIn("SPD-999", out["error"])
        self.assertIn("SPD-999", proc.stderr)

    def test_usage_error_exit_code(self):
        proc = self.home.run("no-such-command", check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE)

    def test_no_bytecode_is_written_under_bin_or_tests(self):
        # Loading bin/spud as a module and running the CLI must add no .pyc under
        # bin/ (a stray `py_compile` by hand may have left one before; that is not
        # the suite's doing, so the check is for new files only).
        from helpers import REPO, load_spud_module

        self.assertTrue(sys.dont_write_bytecode)
        before = set((REPO / "bin").rglob("*.pyc"))
        load_spud_module().owners()  # imports every module of the package in this process
        self.home.run("board")
        after = set((REPO / "bin").rglob("*.pyc"))
        self.assertEqual(after - before, set())

    def test_init_leaves_the_scaffolding_the_directories_and_the_pointer(self):
        """SPW-001 steps 4 and 5, as every scratch home now gets them (this class runs init cold, warm_cache False)."""
        for rel in SCAFFOLDING:
            path = self.home.path / rel
            self.assertTrue(path.is_file(), rel)
            self.assertNotIn("{{", path.read_text(encoding="utf-8"), rel)  # no mark reaches a home unrendered
        for rel in DIRECTORIES:
            self.assertTrue((self.home.path / rel).is_dir(), rel)
        pointer = self.home.path / ".user-config" / "home"  # helpers.Home's scratch SPUD_CONFIG_DIR
        self.assertEqual(pointer.read_text(encoding="utf-8").strip(), str(self.home.path))
        spud_md = (self.home.path / "ledger" / "Spud.md").read_text(encoding="utf-8")
        self.assertIn("name: Spud", spud_md)
        self.assertIn("model: %s" % real_config()["identity"]["model"], spud_md)

    def test_help_says_the_hooks_bind_and_check_as(self):
        text = self.home.run("--help").stdout
        self.assertIn("--as", text)
        self.assertIn("bound to its", text)
        self.assertIn("spud hook", text)
        self.assertNotIn("SPD-008", text)


class EmptyRegistryTest(RepoMixin, SpudTestCase):
    """SPW-001, phase 2 of docs/design/2026-09-21-spud-init.md: project 1 is the project spud.config.json names, not the
    tool repository, and `spud init` inserts none -- so this is the home every `spud init` now leaves, and the home
    `spud init --no-project` will leave.  It is safe (section 1.2) and not usable: doctor is green with a note, and
    `ticket new` refuses, because tickets.project_id references a project.  The first project registered gets id 1 and is
    project 1, whatever its key: its name and prefixes are the config's from then on."""

    seed_project = False  # the whole point: `spud init` and nothing else

    def test_init_registers_no_project(self):
        self.assertEqual(self.home.rows("SELECT * FROM projects"), [])
        self.assertEqual(self.cli_json("project", "list")["projects"], [])
        self.assertEqual(self.home.init(project=False)["applied"], [])  # a second init still registers none
        self.assertEqual(self.home.rows("SELECT * FROM projects"), [])

    def test_doctor_is_green_with_a_note_naming_project_add(self):
        out = self.cli_json("doctor")  # exit 0: check=True would have raised on a problem
        self.assertTrue(out["ok"])
        self.assertEqual(out["problems"], [])
        self.assertEqual(out["projects"], [])
        note = next((n for n in out["notes"] if "no project is registered" in n), None)
        self.assertIsNotNone(note, out["notes"])
        self.assertIn("spud --as spud project add", note)
        self.assertIn("--ticket-prefix SPD --team-prefix SPUD", note)
        text = self.cli("doctor").stdout
        self.assertIn("note        no project is registered", text)
        self.assertIn("problems    none", text)

    def test_ticket_new_refuses_and_names_the_fix(self):
        proc = self.cli("ticket", "new", "--title", "Nowhere", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)  # a SpudError, not the TypeError of subscripting None
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("no project is registered", proc.stderr)
        self.assertIn("spud --as spud project add", proc.stderr)
        self.assertIn("--project", proc.stderr)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM tickets"), 0)

    def test_the_home_itself_still_works_and_the_path_rule_still_holds(self):
        """Section 1.2: an empty registry is safe.  `projects/sessions.session_mode` short-circuits to Spud when no
        project claims sessions, which is right, because the only sessions carrying ledger hooks are sessions in the
        home -- and a session in the home is Spud's either way."""
        self.assertEqual(self.cli("board").stdout.strip(), "(none)")
        # SPW-001: init's own report entry (step 3) is the one row a home starts with, so the first render writes its day
        # too -- the day the ledger says init wrote, not the day this line runs, so a run at midnight reads the same
        self.assertEqual(self.cli_json("render", actor="spud")["written"], ["ledger/Projects.md", init_report_day(self.home)])
        self.assertIn("project   none:", self.cli("session", "show").stdout)
        base = {"session_id": SESSION, "transcript_path": "/tmp/x.jsonl", "cwd": str(self.home.path), "permission_mode": "default"}
        start = self.home.hook("SessionStart", dict(base, hook_event_name="SessionStart", source="startup"))
        self.assertEqual(start.code, 0)
        self.assertIn("Ledger board", start.context)
        write = self.home.hook("PreToolUse", dict(base, hook_event_name="PreToolUse", tool_name="Write", tool_use_id="t1",
                                                  tool_input={"file_path": str(self.home.path / "ledger" / "x.md"), "content": "x"}))
        self.assertEqual(write.decision, "deny")
        self.assertIn("Law 5", write.reason)

    def test_config_sync_says_no_project_is_registered(self):
        out = self.cli_json("config", "sync")
        self.assertIsNone(out["project"])
        self.assertEqual(out["pool"], len(real_config()["naming"]["pool"]))
        self.assertEqual(self.home.scalar("SELECT count(*) FROM name_pool WHERE active = 1"), out["pool"])
        text = self.cli("config", "sync").stdout
        self.assertIn("no project is registered", text)
        self.assertIn("spud --as spud project add", text)
        self.assertIn("SPD / SPUD", text)

    def test_the_first_project_added_is_project_1(self):
        repo = self.make_repo("mine-")
        added = self.add_project(repo, "mine", "SPD", "SPUD", "merge")  # the config's own prefixes: what init will write
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(self.home.rows("SELECT id, key FROM projects"), [{"id": 1, "key": "mine"}])
        self.assertEqual([n for n in self.cli_json("doctor")["notes"] if "project" in n], [])  # the note is gone
        self.assertEqual(self.cli_json("config", "sync")["project"], {"key": "mine", "ticket_prefix": "SPD", "team_prefix": "SPUD"})
        # project 1's name and prefixes are the config's, and it is never removed -- because it is project 1
        for args, needle in ((["--name", "X"], "project mine is project 1"), (["--ticket-prefix", "ZZ"], "spud.config.json")):
            proc = self.cli("project", "edit", "mine", *args, actor="spud", check=False)
            self.assertEqual(proc.returncode, EXIT_ERROR, proc)
            self.assertIn(needle, proc.stderr)
        proc = self.cli("project", "remove", "mine", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("never removed", proc.stderr)
        # and an unqualified `ticket new` lands in it, with the config's prefixes and no project key on the line
        line = self.cli("ticket", "new", "--title", "The first", actor="spud").stdout
        self.assertTrue(line.startswith("SPD-001 (SPUD-001) created: The first [queued]"), line)

    def test_config_sync_takes_project_1s_prefixes_over(self):
        repo = self.make_repo("mine-")
        self.add_project(repo, "mine", "ZZZ", "ZZZS", "merge")
        proc = self.cli("doctor", check=False)  # the config names SPD / SPUD, which project 1 does not carry
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("project mine is project 1 and its prefixes differ from spud.config.json", proc.stderr)
        self.cli("config", "sync")
        self.assertEqual(self.home.rows("SELECT ticket_prefix, team_prefix FROM projects"), [{"ticket_prefix": "SPD", "team_prefix": "SPUD"}])
        self.assertEqual(self.cli_json("doctor")["problems"], [])


class MachineMixin(RepoMixin):
    """A machine with no Spud on it: no `~/.config/spud`, no home, no `~/.claude`, no LaunchAgents -- the four environment
    overrides that make the ticket's definition of done a test rather than a hand ritual (SPW-001 design section 9) --
    and a fresh clone of the tool whose own `bin/spud` is the launcher, so `tool_root()` finds a main checkout the way it
    does for a person who has just cloned and has nothing else."""

    # The first project's directory name, chosen rather than left to mkdtemp, because init derives the default project
    # key from it: `_` is a character registry.PROJECT_KEY_RE rejects, so the key is sanitized and the display name is
    # not, and a name mkdtemp chose would carry an underscore only some of the time -- a test that reads both must be
    # able to say they differ every run.  test_the_project_key_default_is_sanitized_and_never_guessed pins the rule.
    REPO_DIR = "My_Notes"
    REPO_KEY = "my-notes"

    def machine(self):
        self.tool = self.make_tool()
        scratch = self.scratch_dir("spud-machine-")
        self.config_dir = scratch / "config"
        self.target = scratch / "SpudHome"  # deliberately absent: init creates it
        self.pointer = self.config_dir / "home"
        env = isolated_git_env()
        for name in ("SPUD_HOME", "SPUD_TOOL_DIR", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR", "SPUD_SUITE_PYCACHE"):
            env.pop(name, None)
        env.update(SPUD_CONFIG_DIR=str(self.config_dir), SPUD_USER_CLAUDE_DIR=str(scratch / "user-claude"),
                   SPUD_LAUNCH_AGENTS_DIR=str(scratch / "LaunchAgents"), SPUD_GH="off")
        self.env = env
        return scratch

    def spud(self, *args, check=True, cwd=None, session=None, launcher=None, env=None):
        """The scratch clone's own launcher, from `cwd` (default the clone), with stdin a pipe -- so `isatty()` is false
        and no value is ever prompted for, which is how a scripted run and the suite see init."""
        e = dict(self.env, **(env or {}))
        if session is not None:
            e["CLAUDE_CODE_SESSION_ID"] = session
        cmd = [sys.executable, "-I", "-S", str(launcher or (self.tool / "bin" / "spud"))] + [str(a) for a in args]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=e, cwd=str(cwd or self.tool), input="")
        if check and proc.returncode != 0:
            raise AssertionError("spud %s exited %d\nstdout: %s\nstderr: %s" % (" ".join(str(a) for a in args), proc.returncode, proc.stdout, proc.stderr))
        return proc

    def init_argv(self, *extra, home=None, project=True):
        argv = ["init", "--home", home or self.target]
        argv += (["--project-root", self.repo] if project else ["--no-project"])
        return argv + ["--ticket-prefix", "ZZZ", "--team-prefix", "ZZZS", *extra]

    def scaffolding_state(self):
        return {rel: (self.target / rel).read_text(encoding="utf-8") for rel in SCAFFOLDING}


class FreshMachineTest(MachineMixin, unittest.TestCase):
    """SPW-001 phase 3: `spud init` from a fresh clone on a machine that has never had a home (design sections 2.2 and 8).

    The steps it proves are 1 to 5 -- the config, the database, the first project with its report entry, the vault
    scaffolding, the pointer.  Steps 6 to 10 (settings sync, project install, the LaunchAgents, the render, doctor) are
    phase 4's, so nothing here asserts a hook line or an installed project; `doctor` is still green without them."""

    def setUp(self):
        self.machine()
        self.repo = self.make_repo("mine-", origin=True, name=self.REPO_DIR)

    def test_init_builds_a_home_from_nothing(self):
        out = json.loads(self.spud("--json", *self.init_argv()).stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["home"], str(self.target))
        self.assertEqual(out["how"], "--home")
        # 1. the config, rendered from the shipped template and cleared by config_problems (doctor runs it)
        config = json.loads((self.target / "spud.config.json").read_text(encoding="utf-8"))
        self.assertEqual((config["tickets"]["prefix"], config["teams"]["prefix"]), ("ZZZ", "ZZZS"))
        self.assertEqual(config["identity"]["name"], "Spud")
        self.assertEqual(config["identity"]["pronouns"], {"subject": "he", "object": "him", "possessive": "his"})
        self.assertEqual(config["naming"]["pool"], real_config()["naming"]["pool"])
        self.assertNotIn("{{", (self.target / "spud.config.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(self.spud("--json", "doctor").stdout)["problems"], [])
        # 2. the database
        con = sqlite3.connect(self.target / ".spud" / "ledger.db")
        try:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], SCHEMA)
            self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            con.row_factory = sqlite3.Row
            # 3. the first project: id 1, the flags' prefixes, and project add's own defaults for the rest
            rows = [dict(r) for r in con.execute("SELECT * FROM projects").fetchall()]
            events = [dict(r) for r in con.execute("SELECT actor, kind, body FROM events ORDER BY id").fetchall()]
        finally:
            con.close()
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        # the key is the directory name sanitized towards registry.PROJECT_KEY_RE; the display name is that name as it is
        self.assertEqual((row["id"], row["key"], row["name"]), (1, self.REPO_KEY, self.REPO_DIR))
        self.assertEqual((row["ticket_prefix"], row["team_prefix"]), ("ZZZ", "ZZZS"))
        self.assertEqual((row["sessions"], row["landing"], row["default_branch"]), ("claim", "merge", "main"))
        self.assertEqual(row["root_path"], str(self.repo))
        self.assertEqual(row["remote"], str(self.origin))
        self.assertEqual([e["kind"] for e in events], ["project.added", "report.entry"])
        self.assertTrue(all(e["actor"] == "spud" for e in events), events)  # init resolves no actor (design 2.3)
        self.assertIn("Spud initialized at %s" % self.target, out["report_entry"]["title"])
        # 4. the scaffolding and the directories
        self.assertEqual(out["scaffolding"]["written"], list(SCAFFOLDING))
        self.assertEqual(out["scaffolding"]["dropped_lines"], 0)  # a project is registered, so every line is renderable
        for rel, text in self.scaffolding_state().items():
            self.assertNotIn("{{", text, rel)
            for path in (self.target, self.tool, self.repo):  # {{memory_dir}} spells the home's path with hyphens
                text = text.replace(str(path), "").replace(str(path).replace("/", "-").replace(".", "-"), "")
            self.assertNotIn("/Users/", text, rel)  # no shipped file names a machine's own path (SPW-002's rule)
        for rel in DIRECTORIES:
            self.assertTrue((self.target / rel).is_dir(), rel)
        claude = (self.target / "CLAUDE.md").read_text(encoding="utf-8")
        for fact in (str(self.target), str(self.tool), str(self.tool / "bin" / "spud"), str(self.repo), "ZZZ-nnn", "ZZZS-nnn", self.REPO_KEY):
            self.assertIn(fact, claude, fact)
        # 5. the pointer, and the home resolvable through it alone
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), str(self.target))
        self.assertEqual(json.loads(self.spud("--json", "doctor").stdout)["spud_home"]["resolved_by"], "~/.config/spud/home")

    def test_a_second_run_changes_nothing(self):
        self.spud(*self.init_argv())
        before = self.scaffolding_state()
        stamps = {rel: (self.target / rel).stat().st_mtime_ns for rel in SCAFFOLDING}
        proc = self.spud(*self.init_argv())
        self.assertIn("kept %s" % (self.target / "spud.config.json"), proc.stdout)
        self.assertIn("is up to date (user_version %d)" % SCHEMA, proc.stdout)
        self.assertIn("project %s is registered already" % self.REPO_KEY, proc.stdout)
        self.assertIn("no report entry: this run changed nothing", proc.stdout)
        self.assertIn("nothing written, 7 kept", proc.stdout)
        self.assertIn("already", proc.stdout.split("5. ")[1])
        self.assertEqual(self.scaffolding_state(), before)  # never overwrites a file a person may have edited
        self.assertEqual({rel: (self.target / rel).stat().st_mtime_ns for rel in SCAFFOLDING}, stamps)
        out = json.loads(self.spud("--json", *self.init_argv()).stdout)
        self.assertNotIn("report_entry", out)
        self.assertEqual(out["scaffolding"]["written"], [])
        con = sqlite3.connect(self.target / ".spud" / "ledger.db")
        try:
            self.assertEqual(con.execute("SELECT count(*) FROM projects").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM events WHERE kind = 'report.entry'").fetchone()[0], 1)
        finally:
            con.close()

    def test_init_keeps_a_hand_edited_scaffolding_file_and_writes_back_a_missing_one(self):
        """Section 6: the scaffolding is in hooks/hookio.SPUD_PATHS, Spud's hand-written set, so a second init must not
        take an edit back -- and a run that finds a file gone writes that one and only that one."""
        self.spud(*self.init_argv())
        (self.target / "ledger" / "Home.md").write_text("mine\n", encoding="utf-8")
        (self.target / "ledger" / "Spud.md").unlink()
        out = json.loads(self.spud("--json", *self.init_argv()).stdout)
        self.assertEqual(out["scaffolding"]["written"], ["ledger/Spud.md"])
        self.assertEqual(out["scaffolding"]["kept"], [rel for rel in SCAFFOLDING if rel != "ledger/Spud.md"])
        self.assertEqual((self.target / "ledger" / "Home.md").read_text(encoding="utf-8"), "mine\n")
        self.assertIn("name: Spud", (self.target / "ledger" / "Spud.md").read_text(encoding="utf-8"))

    def test_a_config_already_there_is_left_alone_and_is_authoritative(self):
        """Section 6's first rule, and the reason for it: doctor compares project 1's prefixes with the config's, so the
        prefixes init writes into the row must be the ones the config on disk carries."""
        self.target.mkdir(parents=True)
        (self.target / "spud.config.json").write_text(json.dumps(dict(real_config()), indent=2), encoding="utf-8")
        proc = self.spud(*self.init_argv("--ticket-prefix", "ZZZ"), check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE, proc.stderr)
        self.assertIn("--ticket-prefix ZZZ differs from the spud.config.json already in", proc.stderr)
        self.assertIn("which carries SPD", proc.stderr)
        proc = self.spud("init", "--home", self.target, "--project-root", self.repo)  # no prefix flags: the config's
        self.assertIn("kept %s" % (self.target / "spud.config.json"), proc.stdout)
        con = sqlite3.connect(self.target / ".spud" / "ledger.db")
        try:
            self.assertEqual(list(con.execute("SELECT ticket_prefix, team_prefix FROM projects").fetchone()), ["SPD", "SPUD"])
        finally:
            con.close()
        self.assertEqual(json.loads(self.spud("--json", "doctor").stdout)["problems"], [])
        for flag in ("--name", "--pronouns"):
            proc = self.spud("init", "--home", self.target, "--no-project", flag, "x", check=False)
            self.assertEqual(proc.returncode, EXIT_USAGE, proc.stderr)
            self.assertIn("%s has nothing to write" % flag, proc.stderr)

    def test_the_project_key_default_is_sanitized_and_never_guessed(self):
        """The rule homeinit.project_key_for documents: the default key is the root's directory name lower-cased and
        sanitized towards registry.PROJECT_KEY_RE, while the display name is that directory name as it is -- and a name
        that cannot become a key at all is refused, naming --project-key, rather than guessed at."""
        for directory, key in (("My_Notes", "my-notes"), ("Notes (2026)!", "notes-2026"), ("UPPER.CASE", "upper-case")):
            repo = self.make_repo("keys-", name=directory)
            home = self.scratch_dir("key-home-") / "SpudHome"
            out = json.loads(self.spud("--json", "init", "--home", home, "--repoint", "--project-root", repo,
                                       "--ticket-prefix", "ZZZ", "--team-prefix", "ZZZS").stdout)
            self.assertEqual((out["project"]["key"], out["project"]["name"]), (key, directory), directory)
        # "2026" sanitizes to "2026", which the pattern rejects: a key must start with a letter, and init says so
        refused = self.spud("init", "--home", self.scratch_dir("key-home-") / "SpudHome", "--repoint", "--project-root",
                            self.make_repo("keys-", name="2026"), "--ticket-prefix", "ZZZ", "--team-prefix", "ZZZS", check=False)
        self.assertEqual(refused.returncode, EXIT_ERROR, refused.stderr)
        self.assertIn("--project-key '2026' must be lower-case letters, digits and hyphens, starting with a letter", refused.stderr)
        self.assertIn("init refused", refused.stderr)

    def test_the_identity_flags_reach_the_config(self):
        self.spud(*self.init_argv("--name", "Tater", "--pronouns", "they/them/their", project=False))
        config = json.loads((self.target / "spud.config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["identity"]["name"], "Tater")
        self.assertEqual(config["identity"]["pronouns"], {"subject": "they", "object": "them", "possessive": "their"})
        self.assertIn("name: Tater", (self.target / "ledger" / "Spud.md").read_text(encoding="utf-8"))
        proc = self.spud("init", "--home", self.scratch_dir("other-home-"), "--repoint", "--no-project",
                         "--ticket-prefix", "YYY", "--team-prefix", "YYYS", "--pronouns", "they/them", check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE, proc.stderr)
        self.assertIn("--pronouns is subject/object/possessive", proc.stderr)

    def test_no_project_leaves_an_empty_registry_and_drops_the_lines_about_one(self):
        out = json.loads(self.spud("--json", *self.init_argv(project=False)).stdout)
        self.assertIsNone(out["project"])
        self.assertEqual(json.loads(self.spud("--json", "project", "list").stdout)["projects"], [])
        self.assertGreater(out["scaffolding"]["dropped_lines"], 0)
        self.assertIn("left out, for want of one", out["done"][-2])
        for rel in ("CLAUDE.md", "ledger/Home.md"):
            text = (self.target / rel).read_text(encoding="utf-8")
            self.assertNotIn("{{", text, rel)
            self.assertNotIn("**project", text, rel)
        report = json.loads(self.spud("--json", "doctor").stdout)
        self.assertEqual(report["problems"], [])
        self.assertTrue(any("no project is registered" in n for n in report["notes"]), report["notes"])
        self.assertIn("--ticket-prefix ZZZ --team-prefix ZZZS", " ".join(report["notes"]))  # the config's, waiting for a project

    def test_the_main_branch_lets_init_dry_run_where_every_other_command_cannot(self):
        """design 2.3's first trap, as a regression test: main resolves the home before it dispatches, so `spud init`
        never ran on a machine with no SPUD_HOME and no pointer -- which is every machine before its first init."""
        self.assertFalse(self.pointer.exists())
        self.assertNotIn("SPUD_HOME", self.env)
        out = json.loads(self.spud("--json", *self.init_argv("--dry-run")).stdout)
        self.assertTrue(out["ok"])
        self.assertTrue(out["dry_run"])
        self.assertEqual(len(out["steps"]), 5)  # 1 to 5; 6 to 10 arrive with finish_install
        self.assertIn("the preconditions hold", self.spud(*self.init_argv("--dry-run")).stdout)
        self.assertFalse(self.target.exists())  # writes nothing at all
        self.assertFalse(self.config_dir.exists())
        for argv in (["board"], ["doctor"], ["migrate"], ["project", "list"], ["--as", "spud", "render"]):
            failed = self.spud(*argv, check=False)
            self.assertEqual(failed.returncode, EXIT_ERROR, argv)
            self.assertIn("cannot find Spud's home: set SPUD_HOME, or write the home's path to", failed.stderr, argv)
        bare = self.spud("init", "--dry-run", check=False)  # and with no --home there is nothing to build
        self.assertEqual(bare.returncode, EXIT_USAGE, bare.stderr)
        self.assertIn("no home to build: give `--home <dir>` (~/SpudHome is the usual choice)", bare.stderr)
        self.assertNotIn("cannot find Spud's home", bare.stderr)

    def test_dry_run_takes_the_home_from_spud_home_and_from_the_pointer(self):
        out = json.loads(self.spud("--json", "init", "--dry-run", "--no-project", "--ticket-prefix", "ZZZ",
                                   "--team-prefix", "ZZZS", env={"SPUD_HOME": str(self.target)}).stdout)
        self.assertEqual((out["home"], out["how"]), (str(self.target), "SPUD_HOME"))
        self.spud(*self.init_argv(project=False))
        out = json.loads(self.spud("--json", "init", "--dry-run").stdout)  # the pointer alone, and the config's values
        self.assertEqual((out["home"], out["how"]), (str(self.target), "~/.config/spud/home"))

    def test_the_actor_trap(self):
        """design 2.3: init registers a repository as a `claim` project, which makes the session doing the work
        unclaimed in a claim project -- exactly what actors.require_spud refuses.  So init resolves no actor at all."""
        refused = self.spud("--as", "SPUW-001/Ranger", *self.init_argv("--dry-run"), check=False)
        self.assertEqual(refused.returncode, EXIT_OWNERSHIP, refused.stderr)
        self.assertIn("init resolves no actor", refused.stderr)
        self.assertIn("--as SPUW-001/Ranger", refused.stderr)
        self.assertFalse(self.target.exists())
        self.spud("--as", "spud", *self.init_argv("--dry-run"))
        # the regression: a session launched in the repository init is about to register, with the work being done there
        out = json.loads(self.spud("--json", *self.init_argv(), cwd=self.repo, session=SESSION).stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["project"]["sessions"], "claim")
        self.assertEqual(json.loads(self.spud("--json", "init", cwd=self.repo, session=SESSION).stdout)["ok"], True)

    def test_next_goes_on_the_report_entry_init_writes(self):
        out = json.loads(self.spud("--json", *self.init_argv("--next", "Open the vault in Obsidian.")).stdout)
        self.assertIn("- Next: Open the vault in Obsidian.", out["report_entry"]["body"])
        self.assertIn("Project %s:" % self.REPO_KEY, out["report_entry"]["body"])
        empty = self.spud(*self.init_argv("--next", ""), check=False)
        self.assertEqual(empty.returncode, EXIT_USAGE, empty.stderr)
        self.assertIn("--next is empty", empty.stderr)
        again = self.spud(*self.init_argv("--next", "Twice."), check=False)  # a second run writes no entry to put it on
        self.assertEqual(again.returncode, EXIT_USAGE, again.stderr)
        self.assertIn("--next has no report entry to go on", again.stderr)

    def test_a_step_that_fails_names_the_steps_that_completed_and_removes_nothing(self):
        """design 2.4: init never removes anything, the message names every step that completed, and a rerun continues.
        The failure here is step 3's own: a second repository cannot take project 1's prefixes (registry.check_prefixes)."""
        self.spud(*self.init_argv())
        other = self.make_repo("other-")
        proc = self.spud("init", "--home", self.target, "--project-root", other, "--project-key", "other", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR, proc.stderr)
        self.assertIn("init stopped after:", proc.stderr)
        self.assertIn("1. kept", proc.stderr)
        self.assertIn("2. %s is up to date" % (self.target / ".spud" / "ledger.db"), proc.stderr)
        self.assertIn("prefix ZZZ is project %s's already" % self.REPO_KEY, proc.stderr)
        self.assertIn("nothing init wrote is removed", proc.stderr)
        con = sqlite3.connect(self.target / ".spud" / "ledger.db")
        try:
            self.assertEqual([r[0] for r in con.execute("SELECT key FROM projects").fetchall()], [self.REPO_KEY])
        finally:
            con.close()
        self.spud(*self.init_argv())  # and the home is still usable, unchanged


class InitRefusalTest(MachineMixin, unittest.TestCase):
    """Each of the design's section 6 refusals, with its own message, before anything is written.  Five are
    `homemove.move_preconditions`', one is the pointer's, one is the first project's shape, and one is the interpreter's."""

    def setUp(self):
        self.machine()
        self.repo = self.make_repo("mine-", origin=True, name=self.REPO_DIR)

    def refused(self, *args, code=EXIT_ERROR):
        proc = self.spud(*args, check=False)
        self.assertEqual(proc.returncode, code, proc.stderr)
        self.assertFalse((self.target / ".spud").exists())  # nothing written on a refusal
        return proc.stderr

    def test_the_target_is_not_a_directory(self):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_text("a file\n", encoding="utf-8")
        self.assertIn("exists and is not a directory", self.refused(*self.init_argv()))

    def test_the_target_is_not_empty_and_is_no_spud_home(self):
        (self.target / "notes").mkdir(parents=True)
        (self.target / "a.txt").write_text("x", encoding="utf-8")
        message = self.refused(*self.init_argv())
        self.assertIn("is not an empty directory and is no Spud home", message)
        self.assertIn("a.txt, notes", message)

    def test_the_target_inside_a_git_work_tree(self):
        message = self.refused(*self.init_argv(home=self.repo / "SpudHome"))
        self.assertIn("is inside a git work tree", message)
        self.assertIn("the home is a plain directory", message)

    def test_the_pointer_names_another_home_unless_repointed(self):
        first = self.scratch_dir("first-home-")
        self.spud("init", "--home", first, "--no-project", "--ticket-prefix", "ZZZ", "--team-prefix", "ZZZS")
        message = self.refused(*self.init_argv())
        self.assertIn("names %s, not %s" % (first, self.target), message)
        self.assertIn("--repoint", message)
        out = json.loads(self.spud("--json", *self.init_argv("--repoint")).stdout)
        self.assertTrue(out["pointer"]["written"])
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), str(self.target))
        self.assertIn("that home is untouched", out["done"][-1])
        self.assertTrue((first / ".spud" / "ledger.db").is_file())  # the orphaned home is left exactly as it was

    def test_spud_home_names_another_home(self):
        """After init the shell's SPUD_HOME beats the pointer in resolve_home, so the person would be working on a home
        other than the one init just built."""
        elsewhere = self.scratch_dir("elsewhere-")
        proc = self.spud(*self.init_argv(), check=False, env={"SPUD_HOME": str(elsewhere)})
        self.assertEqual(proc.returncode, EXIT_ERROR, proc.stderr)
        self.assertIn("SPUD_HOME is %s, not %s" % (elsewhere, self.target), proc.stderr)
        self.assertFalse(self.target.exists())

    def test_the_running_launcher_is_in_a_linked_worktree(self):
        worktree = self.add_worktree(self.tool, "wt")
        message = self.spud(*self.init_argv(), check=False, launcher=worktree / "bin" / "spud").stderr
        self.assertIn("the running bin/spud is in a linked worktree", message)
        self.assertIn(str(worktree), message)
        self.assertFalse(self.target.exists())

    def rooted(self, root, ticket="ZZZ", team="ZZZS", *extra):
        return self.refused("init", "--home", self.target, "--project-root", root, "--ticket-prefix", ticket, "--team-prefix", team, *extra)

    def test_the_first_projects_root(self):
        inside = self.repo / "sub"
        inside.mkdir()
        self.assertIn("is not a git repository with a work tree", self.rooted(self.scratch_dir("not-a-repo-")))
        self.assertIn("is not an existing directory", self.rooted(self.repo / "nope"))
        self.assertIn("not its root; register the root", self.rooted(inside))
        self.assertIn("is a linked worktree; register the main checkout", self.rooted(self.add_worktree(self.repo, "mine-wt")))

    def test_the_first_projects_key_and_prefixes(self):
        self.assertIn("--project-key 'Mine' must be lower-case", self.rooted(self.repo, "ZZZ", "ZZZS", "--project-key", "Mine"))
        self.assertIn("--project-key home is reserved", self.rooted(self.repo, "ZZZ", "ZZZS", "--project-key", "home"))
        self.assertIn("--ticket-prefix 'zzz' must be upper-case", self.rooted(self.repo, "zzz"))
        self.assertIn("--team-prefix 'zz-1' must be upper-case", self.rooted(self.repo, "ZZZ", "zz-1"))
        self.assertIn("must differ (both ZZZ)", self.rooted(self.repo, "ZZZ", "ZZZ"))

    def test_the_prefixes_have_no_default_when_a_config_is_written(self):
        message = self.refused("init", "--home", self.target, "--project-root", self.repo, code=EXIT_USAGE)
        self.assertIn("has no spud.config.json yet, and a config carries the two prefixes", message)
        self.assertIn("--ticket-prefix XXX --team-prefix XXXS", message)
        self.assertFalse(self.target.exists())

    def test_the_home_is_the_home_and_never_a_project(self):
        self.spud(*self.init_argv(project=False))
        message = self.spud("init", "--home", self.target, "--project-root", self.target, "--project-key", "mine", check=False).stderr
        self.assertIn("is Spud's home, which is not a project", message)

    def test_the_interpreter_must_be_3_14(self):
        """The one refusal a subprocess cannot stage: doctor makes a wrong interpreter a problem, so init cannot end
        green without it, and `init_preconditions` is called here directly with the version faked."""
        spud = load_spud_module()
        ctx = spud.Ctx(self.target, "--home", False, tool=self.tool)
        args = mock.Mock(repoint=False, yes=True, home=str(self.target), landing="merge", sessions="claim")
        plan = {"config_exists": False, "ticket_prefix": "ZZZ", "team_prefix": "ZZZS", "project_root": None,
                "project_key": None, "project_name": None}
        with mock.patch.dict(os.environ, {"SPUD_CONFIG_DIR": str(self.config_dir)}, clear=False):
            os.environ.pop("SPUD_HOME", None)
            self.assertEqual(spud.init_preconditions(ctx, args, plan), [])
            with mock.patch.object(sys, "version_info", (3, 13, 2, "final", 0)):
                self.assertEqual(spud.init_preconditions(ctx, args, plan), ["interpreter is Python 3.13.2, not 3.14"])


class WithoutDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.cleanup)

    def test_commands_refuse_to_run_without_a_ledger(self):
        proc = self.home.run("board", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("spud init", proc.stderr)
        self.assertFalse((self.home.path / ".spud").exists())

    def test_doctor_reports_the_missing_database(self):
        proc = self.home.run("--json", "doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        out = json.loads(proc.stdout)
        self.assertFalse(out["database"]["exists"])
        self.assertTrue(out["interpreter"]["version"].startswith("3.14"))

    def test_init_creates_backups_dir_lazily_and_no_stray_files(self):
        self.home.init()
        entries = sorted(p.name for p in (self.home.path / ".spud").iterdir())
        self.assertIn("ledger.db", entries)
        for name in entries:
            self.assertTrue(name.startswith("ledger.db") or name == "backups", name)


if __name__ == "__main__":
    unittest.main()
