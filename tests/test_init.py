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
    SpudTestCase,
    real_config,
)

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
}


class InitTest(SpudTestCase):
    def test_init_creates_a_wal_database_under_spud_home(self):
        self.assertTrue(self.home.db.exists())
        self.assertEqual(self.home.scalar("PRAGMA user_version"), 1)
        self.assertEqual(self.home.scalar("PRAGMA journal_mode"), "wal")

    def test_init_is_idempotent(self):
        again = self.home.init()
        self.assertTrue(again["ok"])
        self.assertFalse(again["created"])
        self.assertEqual(again["user_version"], 1)
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
        self.assertEqual(out["user_version"], 1)

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

    def test_home_project_row_comes_from_config(self):
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
        self.assertEqual(self.home.scalar("SELECT active FROM name_pool WHERE name = 'Russet'"), 0)
        self.assertEqual(self.home.scalar("SELECT active FROM name_pool WHERE name = 'Tuber'"), 1)
        self.assertEqual(self.home.scalar("SELECT ticket_prefix FROM projects WHERE id = 1"), "TKT")
        self.assertEqual(self.home.scalar("SELECT team_prefix FROM projects WHERE id = 1"), "TEAM")
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events WHERE kind = 'config.synced'"), 1)

    def test_backup_writes_a_vacuumed_copy(self):
        out = self.home.json("backup")
        path = Path(out["path"])
        self.assertTrue(path.exists())
        self.assertEqual(path.parent, self.home.path / ".spud" / "backups")
        self.assertRegex(path.name, r"^ledger-\d{8}T\d{6}\.db$")
        con = sqlite3.connect(path)
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
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
        self.assertEqual(db["user_version"], 1)
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
        cache = REPO / "bin" / "__pycache__"
        before = set(cache.glob("*")) if cache.exists() else set()
        load_spud_module()
        self.home.run("board")
        after = set(cache.glob("*")) if cache.exists() else set()
        self.assertEqual(after - before, set())

    def test_help_says_as_is_trusted_by_prose_until_hooks(self):
        text = self.home.run("--help").stdout
        self.assertIn("--as", text)
        self.assertIn("trusted", text)
        self.assertIn("SPD-008", text)


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
