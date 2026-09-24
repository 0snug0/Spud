"""The migration chain as a whole (SPD-233): what every tests/test_migrate_*.py module builds its old database with, and the
one test of a fresh init's migrations.

Every name and version a migration test expects of `spud migrate` is derived here from schema.MIGRATIONS and
SCHEMA_VERSION, so the next migration changes no test that is not about it.  What a module states is its own subject: the
migration it is about and the version before it, the views and triggers that version had (verbatim, since today's may
name a column the old tables lack), and the rows it seeds.  A row compared across a migration is cut to the columns it had
before (`as_before`), so a column a later migration adds is that migration's test's business, not this one's.

The CLI's refusal of an older database is one test, on the oldest one (test_migrate_projects): every version behind
SCHEMA_VERSION meets the same check in ledgerdb.connect.
"""

import re
import sqlite3
import unittest

from helpers import Home, load_spud_module

spud = load_spud_module()


def after(version):
    """The migrations `spud migrate` applies to a database at `version`, in order: every one after it."""
    return [name for name, _ in spud.MIGRATIONS[version:]]


class MigrationCase(unittest.TestCase):
    """A home whose ledger is a database at VERSION, built by hand as that version's CLI left it: every migration up to
    VERSION from schema.MIGRATIONS, then VIEWS, then `PRAGMA user_version = VERSION`.  Project 1 is the tool beside the
    home, as registered since SPD-097, and the name pool is the config's; `seed(con)` writes the module's own rows.

    MIGRATION is the migration the module is about, the one after VERSION.  The home starts with the warm bytecode cache
    (SPD-102): nothing here asserts on .spud/ but the ledger and the backups `spud migrate` names."""

    MIGRATION = None  # e.g. "0009_member_effort"
    VERSION = None  # the schema version the test builds, the one before MIGRATION
    VIEWS = None  # that version's views and triggers, verbatim
    AT = "2026-09-24T10:00:00-07:00"

    def setUp(self):
        self.assertEqual(spud.MIGRATIONS[self.VERSION][0], self.MIGRATION)
        self.home = Home(warm=True)
        self.addCleanup(self.home.cleanup)
        self.home.db.parent.mkdir(parents=True, exist_ok=True)  # the warm cache has made .spud/ already
        con = sqlite3.connect(self.home.db, autocommit=True)
        try:
            con.execute("PRAGMA journal_mode = WAL")
            for _, ddl in spud.MIGRATIONS[:self.VERSION]:
                con.executescript(ddl)
            con.executescript(self.VIEWS)
            con.execute("PRAGMA user_version = %d" % self.VERSION)
            con.execute("INSERT INTO projects (id, key, name, root_path, ticket_prefix, team_prefix, created_at) VALUES (1, 'spud', 'Spud', ?, 'SPD', 'SPUD', ?)",
                        (str(self.home.tool), self.AT))
            for name in self.home.config["naming"]["pool"]:
                con.execute("INSERT INTO name_pool (name) VALUES (?)", (name,))
            self.seed(con)
        finally:
            con.close()

    def seed(self, con):
        """The module's rows, written on the VERSION database before any migration."""

    def migrate(self):
        return self.home.json("migrate")

    def assert_migrated(self, out):
        """`spud migrate`'s answer: every migration after VERSION applied in order, MIGRATION first, the database at
        SCHEMA_VERSION, and one backup before each, named for it.  Returns the first backup, opened read-only: the
        database as the test built it."""
        applied = after(self.VERSION)
        self.assertEqual(applied[0], self.MIGRATION)
        self.assertEqual((out["applied"], out["user_version"]), (applied, spud.SCHEMA_VERSION))
        self.assertEqual(len(out["backups"]), len(applied))
        for path, name in zip(out["backups"], applied):
            self.assertRegex(path, r"/ledger-\d{8}T\d{6}-pre-%s\.db$" % re.escape(name))
        backup = sqlite3.connect("file:%s?mode=ro" % out["backups"][0], uri=True)
        self.addCleanup(backup.close)
        self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], self.VERSION)
        return backup

    def as_before(self, rows, before):
        """`rows`, read after the migration, cut to the columns `before` had: one row for each, every old column still there."""
        self.assertEqual(len(rows), len(before))
        return [{k: row[k] for k in old} for row, old in zip(rows, before)]


class FreshInitTest(unittest.TestCase):
    """A new home's init applies the whole chain with no backup, which no other test reads: test_init sees only the
    user_version its fixture's init left.  The five per-migration tests this replaces (SPD-233) each built a home of their
    own for it; their spot checks of what each migration made are kept below."""

    def test_a_fresh_init_applies_every_migration_and_backs_up_nothing(self):
        home = Home(warm=True)
        self.addCleanup(home.cleanup)
        out = home.init()
        self.assertEqual((out["applied"], out["user_version"], out["backups"]), (after(0), spud.SCHEMA_VERSION, []))
        self.assertEqual(home.scalar("PRAGMA user_version"), spud.SCHEMA_VERSION)
        self.assertEqual(home.rows("SELECT key, landing, sessions FROM projects"), [{"key": "spud", "landing": "merge", "sessions": "claim"}])  # 0002
        self.assertEqual(home.scalar("SELECT count(*) FROM pragma_table_info('tickets') WHERE name IN ('parked_until','parked_reason')"), 2)  # 0003
        self.assertEqual(home.scalar("SELECT count(*) FROM pragma_table_info('tickets') WHERE name = 'worktree'"), 1)  # 0004
        self.assertEqual(home.scalar("SELECT count(*) FROM pull_requests"), 0)  # 0005
        self.assertIn("CHECK (origin IN ('owner','proposal'))", home.scalar("SELECT sql FROM sqlite_master WHERE name = 'tickets'"))  # 0006


if __name__ == "__main__":
    unittest.main()
