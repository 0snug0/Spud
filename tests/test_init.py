"""init, migrate, backup, config sync, doctor, and the CLI's error shapes."""

import json
import re
import sqlite3
import sys
import unittest
from pathlib import Path

from helpers import (
    EXIT_ERROR,
    EXIT_USAGE,
    Home,
    RepoMixin,
    SpudTestCase,
    real_config,
)

SESSION = "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b"

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
        self.assertEqual(self.cli_json("render", actor="spud")["written"], ["ledger/Projects.md"])
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
