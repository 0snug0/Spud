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
    CONFIG,
    EXIT_ERROR,
    EXIT_OWNERSHIP,
    EXIT_USAGE,
    GUARD_LAUNCHCTL,
    Home,
    RepoMixin,
    SpudTestCase,
    fake_launchctl,
    git,
    init_report_day,
    isolated_git_env,
    load_spud_module,
    real_config,
)

SESSION = "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b"
# SPW-001: what `spud init` writes into a home besides the database (design sections 2.2 and 4.1).
SCAFFOLDING = ("CLAUDE.md", "ledger/Home.md", "ledger/Spud.md", "ledger/Board.base", "ledger/Fleet.base",
               "ledger/_templates/ticket.md", "ledger/_templates/spudagent.md")
# SPD-157: and every skill the tool ships, which is the one part of the shipped set whose path in a home is not its path
# under share/ -- Claude Code reads a skill from `.claude/skills/`.  Spelled out rather than read from `share/skills/`,
# so that a skill shipped without a thought for the home it lands in fails here as well as in tests/test_share.py.
SHIPPED_SKILLS = (".claude/skills/spud-reference/SKILL.md",)
TOOL_OWNED = SCAFFOLDING + SHIPPED_SKILLS  # `commands/homesync.tool_owned`, which init writes and `home sync` rewrites
DIRECTORIES = ("ledger/tickets", "ledger/teams", "reports", "docs/spikes", "docs/design")
# SPW-005: the block share/spud.config.json carried until SPW-005 dropped it, with every path in it moved under one
# directory of its own -- so a test that finds a note or a folder under DROPPED_ROOT has found a reader of the block,
# which there is none of.  `format` keeps its shipped value, the one that read `markdown-v0` while the ledger's format
# had been `sqlite-v1` since the database landed, because that staleness was harmless too.
DROPPED_ROOT = "elsewhere"
DROPPED_LEDGER_BLOCK = {
    "format": "markdown-v0",
    "tickets": DROPPED_ROOT + "/tickets",
    "teams": DROPPED_ROOT + "/teams",
    "reports": DROPPED_ROOT,
    "spikes": DROPPED_ROOT + "/spikes",
}
UNKNOWN_BLOCK = "a_block_no_version_of_spud_ever_knew"


def extra_block_config():
    """The shipped config plus the dropped `ledger` block and a block no version of the program knew: what a home may
    carry after SPW-005, since nothing validates the config's key set and nothing migrates it."""
    config = real_config()
    config["ledger"] = dict(DROPPED_LEDGER_BLOCK)
    config[UNKNOWN_BLOCK] = {"nested": [1, 2, 3]}
    return config


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
SCHEMA = 9  # user_version since SPD-222


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
        bodies = [e["body"] for e in self.home.rows("SELECT body FROM events WHERE kind = 'config.synced' ORDER BY id")]
        self.assertIn("settings synced to", bodies[0])  # init's own settings sync, step 6 (SPW-001 phase 4)
        self.assertEqual(bodies[1:], ["config synced"])  # and this `config sync`, the only other one
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
        for rel in TOOL_OWNED:
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


class UnknownConfigBlockTest(SpudTestCase):
    """SPW-005: a home's `spud.config.json` may carry a block the program does not read, and nothing complains.

    The shipped config carried a `ledger` block (`format`, `tickets`, `teams`, `reports`, `spikes`) that no module ever
    read: `render/notefiles.render_targets` and `commands/homeinit.DIRECTORIES` hard-code the vault's layout, because the
    hooks (`hooks/hookio.GENERATED_ROOTS`, `imports/accept`, `imports/bulkimport`, `commands/homemove.COPIED_DIRS`) and
    the shipped `Board.base` and `Fleet.base` all depend on that layout.  Eric's call (2026-09-22) dropped the block from
    `share/spud.config.json`, and there is no migration: `Ctx.config` is `json.load` and `homeconf.config_problems`
    checks the blocks it knows, so a home whose file still carries the block -- Eric's own, until he removes it by hand
    -- keeps working untouched, and so does one carrying any other key nothing reads.  This home carries both, with the
    old block's paths deliberately pointing somewhere the renderer does not write, and everything below is green.  A
    key-set or schema check added later fails here rather than in somebody's live home.
    """

    config = None  # built in setUp, so each test gets its own dict (SpudTestCase reads self.config)

    def setUp(self):
        self.config = extra_block_config()
        super().setUp()

    def test_the_config_reader_and_doctor_accept_the_blocks_nothing_reads(self):
        self.assertEqual(load_spud_module().config_problems(self.config), [])
        out = self.home.json("doctor")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["problems"], [])
        self.assertEqual(out["config"]["problems"], [])
        self.assertEqual(out["config"]["limits"], real_config()["limits"])
        # and nothing rewrote or migrated the file underneath the home: both blocks are still in it, byte for byte
        written = json.loads((self.home.path / "spud.config.json").read_text(encoding="utf-8"))
        self.assertEqual(written["ledger"], DROPPED_LEDGER_BLOCK)
        self.assertEqual(written[UNKNOWN_BLOCK], {"nested": [1, 2, 3]})

    def test_config_sync_mirrors_the_blocks_it_knows_and_leaves_the_rest_alone(self):
        out = self.home.json("config", "sync")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["pool"], len(real_config()["naming"]["pool"]))
        self.assertEqual((out["project"]["ticket_prefix"], out["project"]["team_prefix"]), ("SPD", "SPUD"))
        self.assertIn("ledger", json.loads((self.home.path / "spud.config.json").read_text(encoding="utf-8")))

    def test_the_renderer_writes_its_own_folders_whatever_the_dropped_block_says(self):
        """The block was decorative, which is why it could go: its paths never chose a folder, and they still do not."""
        ticket = self.new_ticket("A ticket")
        self.new_member(ticket["key"], name="Russet")
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        self.assertTrue((out / "ledger" / "tickets" / ("%s.md" % ticket["key"])).is_file())
        self.assertTrue((out / "ledger" / "teams" / ticket["team_key"] / "Russet.md").is_file())
        self.assertFalse((out / DROPPED_ROOT).exists())

    def test_the_directories_init_creates_are_the_hard_coded_ones(self):
        """`homeinit.DIRECTORIES`, not `ledger.spikes` and friends: the home this class built has the renderer's folders
        and none of the block's."""
        for rel in DIRECTORIES:
            self.assertTrue((self.home.path / rel).is_dir(), rel)
        self.assertFalse((self.home.path / DROPPED_ROOT).exists())


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
        # SPW-001: init's own report entry (step 3) is the one row a home starts with, and init's own render (step 9)
        # writes its day file -- the day the ledger says init wrote, not the day this line runs, so a run at midnight
        # reads the same.  Which leaves this render nothing to write and both files on disk.
        rendered = self.cli_json("render", actor="spud")
        self.assertEqual((rendered["written"], rendered["conflicts"]), ([], []))
        self.assertEqual(sorted(rendered["unchanged"]), ["ledger/Projects.md", init_report_day(self.home)])
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
        # narrowed to the note's own words (SPD-159): a home's shipped `.base` views can add a note of their own
        # naming a view called "By project" when its plugin's download is off in every test home, and that note is
        # not what this line is checking for.
        self.assertEqual([n for n in self.cli_json("doctor")["notes"] if "no project is registered" in n], [])
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
        self.agents = scratch / "LaunchAgents"
        self.user_claude = scratch / "user-claude"
        env = isolated_git_env()
        for name in ("SPUD_HOME", "SPUD_TOOL_DIR", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR", "SPUD_SUITE_PYCACHE"):
            env.pop(name, None)
        env.update(SPUD_CONFIG_DIR=str(self.config_dir), SPUD_USER_CLAUDE_DIR=str(self.user_claude),
                   SPUD_LAUNCH_AGENTS_DIR=str(self.agents), SPUD_GH="off")
        # A launchctl of this machine's own, because step 8 is init's: SPUD_LAUNCH_AGENTS_DIR moves the plist, and only
        # this moves the job -- `local.spud.backup` and `local.spud.render` are labels in the real user domain, and a
        # `bootout` of one is this Mac's watcher gone.  init_argv passes --no-schedule besides, so the tests that are
        # not about step 8 never call it at all; ScheduleStepTest is the one that does.  SPW-011: what this replaces is
        # helpers' refusing stub, which isolated_git_env() carried in from os.environ, never /bin/launchctl.
        self.assertEqual(env["SPUD_LAUNCHCTL"], str(GUARD_LAUNCHCTL))
        self.launchctl, self.launchctl_state = fake_launchctl(scratch, env)
        self.assertEqual(env["SPUD_LAUNCHCTL"], str(self.launchctl))
        self.env = env
        return scratch

    def launchctl_calls(self):
        path = self.launchctl_state / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []

    def ctx(self):
        """A Ctx for the home init built, for the checks that read the program rather than the CLI."""
        return load_spud_module().Ctx(self.target, "--home", False, tool=self.tool)

    def rows(self, sql):
        con = sqlite3.connect(self.target / ".spud" / "ledger.db")
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql).fetchall()]
        finally:
            con.close()

    def report_day(self, home=None):
        """`reports/<day>.md` for the entry init wrote into this machine's home, read from the home's own event log the
        way `helpers.init_report_day` reads a Home's: a run that crossed midnight between step 3 and this assertion
        still names the day init wrote.  Never `date.today()` (SPW-001, the window phase 3 closed)."""
        con = sqlite3.connect((home or self.target) / ".spud" / "ledger.db")
        try:
            row = con.execute("SELECT at FROM events WHERE kind = 'report.entry'"
                              " AND json_extract(data, '$.generated') = 'init' ORDER BY id LIMIT 1").fetchone()
        finally:
            con.close()
        self.assertIsNotNone(row, "no init report entry in %s" % (home or self.target))
        return "reports/%s.md" % row[0][:10]

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

    def init_argv(self, *extra, home=None, project=True, schedule=False):
        """init's flags for this machine.  `--no-schedule` by default: step 8 is `schedule.install_agents`, which keeps
        its own coverage (SPW-001 design, phase 4), and a run that installs two LaunchAgents to prove something about
        the config is two launchctl calls nobody reads.  `schedule=True` asks for the step itself."""
        target = Path(str(home or self.target))
        argv = ["init", "--home", target]
        argv += (["--project-root", self.repo] if project else ["--no-project"])
        argv += [] if schedule else ["--no-schedule"]
        argv += ["--ticket-prefix", "ZZZ", "--team-prefix", "ZZZS"]
        # `--owner-name` joins the two prefixes as a value with no default (SPD-157), and it is refused once a config
        # exists exactly as `--name` is -- the prefixes are the pair a second run may repeat, because the config's are
        # authoritative and a matching flag is a no-op rather than an edit.  So a second run of this line leaves it out,
        # the way a person rerunning `spud init` would.  A test about the refusal, or about another owner, says so in
        # `extra`; argparse takes the last spelling of a flag.
        if not (target / "spud.config.json").is_file():
            argv += ["--owner-name", "Pat"]
        return argv + list(extra)

    def scaffolding_state(self):
        return {rel: (self.target / rel).read_text(encoding="utf-8") for rel in TOOL_OWNED}


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
        # The pool a real `spud init` writes is the shipped one, never the suite's own (helpers.NAME_POOL, SPD-157):
        # this is the one home in the suite the tool builds its config for rather than a fixture handing it one.
        self.assertEqual(config["naming"]["pool"], json.loads(CONFIG.read_text(encoding="utf-8"))["naming"]["pool"])
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
        # steps 3, 6, 7 and 9, in order: the project and its entry, the settings sync, the install, the first render
        self.assertEqual([e["kind"] for e in events],
                         ["project.added", "report.entry", "config.synced", "project.installed", "render"])
        self.assertTrue(all(e["actor"] == "spud" for e in events), events)  # init resolves no actor (design 2.3)
        self.assertIn("Spud initialized at %s" % self.target, out["report_entry"]["title"])
        # 4. the scaffolding and the directories
        self.assertEqual(out["scaffolding"]["written"], list(TOOL_OWNED))
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
        stamps = {rel: (self.target / rel).stat().st_mtime_ns for rel in TOOL_OWNED}
        proc = self.spud(*self.init_argv())
        self.assertIn("kept %s" % (self.target / "spud.config.json"), proc.stdout)
        self.assertIn("is up to date (user_version %d)" % SCHEMA, proc.stdout)
        self.assertIn("project %s is registered already" % self.REPO_KEY, proc.stdout)
        self.assertIn("no report entry: this run changed nothing", proc.stdout)
        self.assertIn("nothing written, %d kept" % len(TOOL_OWNED), proc.stdout)
        self.assertIn("already", proc.stdout.split("5. ")[1])
        self.assertEqual(self.scaffolding_state(), before)  # never overwrites a file a person may have edited
        self.assertEqual({rel: (self.target / rel).stat().st_mtime_ns for rel in TOOL_OWNED}, stamps)
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
        self.assertEqual(out["scaffolding"]["kept"], [rel for rel in TOOL_OWNED if rel != "ledger/Spud.md"])
        self.assertEqual((self.target / "ledger" / "Home.md").read_text(encoding="utf-8"), "mine\n")
        self.assertIn("name: Spud", (self.target / "ledger" / "Spud.md").read_text(encoding="utf-8"))

    def test_the_shipped_skill_lands_where_the_home_s_own_claude_md_says_it_does(self):
        """SPD-157: the tool ships `share/skills/<name>/`, Claude Code reads a skill from `.claude/skills/`, and the
        home's CLAUDE.md points a new Spud at the reference skill by path -- so the path it names must be the path init
        wrote.  A skill that shipped at its share-relative path would leave that sentence pointing at nothing."""
        self.spud(*self.init_argv())
        claude = (self.target / "CLAUDE.md").read_text(encoding="utf-8")
        for rel in SHIPPED_SKILLS:
            path = self.target / rel
            self.assertTrue(path.is_file(), rel)
            self.assertNotIn("{{", path.read_text(encoding="utf-8"), rel)
            self.assertIn(rel, claude, rel)
            self.assertFalse((self.target / "skills").exists())  # never at its path under share/

    def test_a_config_already_there_is_left_alone_and_is_authoritative(self):
        """Section 6's first rule, and the reason for it: doctor compares project 1's prefixes with the config's, so the
        prefixes init writes into the row must be the ones the config on disk carries."""
        self.target.mkdir(parents=True)
        (self.target / "spud.config.json").write_text(json.dumps(dict(real_config()), indent=2), encoding="utf-8")
        proc = self.spud(*self.init_argv("--ticket-prefix", "ZZZ"), check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE, proc.stderr)
        self.assertIn("--ticket-prefix ZZZ differs from the spud.config.json already in", proc.stderr)
        self.assertIn("which carries SPD", proc.stderr)
        proc = self.spud("init", "--home", self.target, "--project-root", self.repo, "--no-schedule")  # no prefix flags: the config's
        self.assertIn("kept %s" % (self.target / "spud.config.json"), proc.stdout)
        con = sqlite3.connect(self.target / ".spud" / "ledger.db")
        try:
            self.assertEqual(list(con.execute("SELECT ticket_prefix, team_prefix FROM projects").fetchone()), ["SPD", "SPUD"])
        finally:
            con.close()
        self.assertEqual(json.loads(self.spud("--json", "doctor").stdout)["problems"], [])
        for flag in ("--name", "--pronouns", "--owner-name", "--owner-pronouns"):
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
            out = json.loads(self.spud("--json", "init", "--home", home, "--repoint", "--project-root", repo, "--no-schedule",
                                       "--ticket-prefix", "ZZZ", "--team-prefix", "ZZZS", "--owner-name", "Pat").stdout)
            self.assertEqual((out["project"]["key"], out["project"]["name"]), (key, directory), directory)
        # "2026" sanitizes to "2026", which the pattern rejects: a key must start with a letter, and init says so
        refused = self.spud("init", "--home", self.scratch_dir("key-home-") / "SpudHome", "--repoint", "--project-root",
                            self.make_repo("keys-", name="2026"), "--ticket-prefix", "ZZZ", "--team-prefix", "ZZZS",
                            "--owner-name", "Pat", check=False)
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
                         "--ticket-prefix", "YYY", "--team-prefix", "YYYS", "--owner-name", "Pat",
                         "--pronouns", "they/them", check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE, proc.stderr)
        self.assertIn("--pronouns is subject/object/possessive", proc.stderr)

    def test_the_owner_flags_reach_the_config_and_the_files_it_renders(self):
        """SPD-157: the config carries the person the home is *for* beside the assistant it describes, and every file
        the tool generates reads the four marks -- so the name given here is in the home's own CLAUDE.md, not a name
        the tool shipped."""
        self.spud(*self.init_argv("--owner-name", "Robin", "--owner-pronouns", "she/her/her", project=False))
        config = json.loads((self.target / "spud.config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["owner"]["name"], "Robin")
        self.assertEqual(config["owner"]["pronouns"], {"subject": "she", "object": "her", "possessive": "her"})
        claude_md = (self.target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("Robin", claude_md)
        self.assertNotIn("{{", claude_md)
        self.assertIn("Robin", (self.target / "ledger" / "Spud.md").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(self.spud("--json", "doctor").stdout)["problems"], [])

    def test_the_owner_pronouns_default_and_are_read_the_way_the_identitys_are(self):
        self.spud(*self.init_argv(project=False))  # no --owner-pronouns
        config = json.loads((self.target / "spud.config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["owner"]["pronouns"], {"subject": "they", "object": "them", "possessive": "their"})
        proc = self.spud("init", "--home", self.scratch_dir("owner-home-"), "--repoint", "--no-project",
                         "--ticket-prefix", "YYY", "--team-prefix", "YYYS", "--owner-name", "Pat",
                         "--owner-pronouns", "she/her", check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE, proc.stderr)
        self.assertIn("--owner-pronouns is subject/object/possessive", proc.stderr)

    def test_no_project_leaves_an_empty_registry_and_drops_the_lines_about_one(self):
        out = json.loads(self.spud("--json", *self.init_argv(project=False)).stdout)
        self.assertIsNone(out["project"])
        self.assertEqual(json.loads(self.spud("--json", "project", "list").stdout)["projects"], [])
        self.assertGreater(out["scaffolding"]["dropped_lines"], 0)
        self.assertTrue(any("left out, for want of one" in line for line in out["done"]), out["done"])
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
        self.assertEqual(len(out["steps"]), 10)  # all ten, and step 8 says which skip it would take
        self.assertIn("--no-schedule", out["steps"][7])
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
                                   "--team-prefix", "ZZZS", "--owner-name", "Pat", env={"SPUD_HOME": str(self.target)}).stdout)
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
        self.assertEqual(json.loads(self.spud("--json", "init", "--no-schedule", cwd=self.repo, session=SESSION).stdout)["ok"], True)

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


class InstallTailTest(MachineMixin, unittest.TestCase):
    """SPW-001 phase 4: steps 6 to 10, the install tail (design sections 2.1, 2.2 and 8).

    This is where the ticket's definition of done becomes a test rather than a hand ritual: one command from a fresh
    clone, and `spud doctor` reports `problems none`.  `--no-schedule` throughout except in ScheduleStepTest, so
    `schedule.install_agents` keeps its own coverage and init adds none to it.
    """

    def setUp(self):
        self.machine()
        self.repo = self.make_repo("mine-", origin=True, name=self.REPO_DIR)

    def installed_paths(self):
        """Every path `install.install_project` writes, named here rather than read from `install_files`, so this test
        would notice if install stopped writing one of them."""
        return {
            "settings": self.repo / ".claude" / "settings.local.json",
            "agent": self.user_claude / "agents" / "spudagent.md",
            "skill": self.user_claude / "skills" / "spud" / "SKILL.md",
            "pointer": self.pointer,
        }

    def test_init_ends_on_a_green_doctor_with_the_home_installed(self):
        """The ticket's definition of done, end to end: the five assertions of the design's phase 4."""
        out = json.loads(self.spud("--json", *self.init_argv()).stdout)
        self.assertTrue(out["ok"])
        # 10. doctor is green -- the definition of done.  From init's own step 10, and from `spud doctor` after it.
        self.assertEqual(out["doctor"]["problems"], [])
        self.assertEqual(json.loads(self.spud("--json", "doctor").stdout)["problems"], [])
        self.assertIn("problems    none", self.spud("doctor").stdout)
        # 6. the home's own settings carry this home's ledger hooks.  The green doctor above is that assertion since
        # SPW-006, which gave doctor a `settings` check of its own -- a home one event short of the table is a problem
        # there -- so this reads only what doctor does not: the nine lines init reported writing, and the allow rules.
        spud = load_spud_module()
        settings = self.target / ".claude" / "settings.json"
        self.assertEqual(out["settings"], {"path": str(settings), "written": True, "hooks": 9})
        rules = json.loads(settings.read_text(encoding="utf-8"))["permissions"]["allow"]
        self.assertTrue(any(str(self.tool / "bin" / "spud") in rule for rule in rules), rules)
        # 7. every path project install writes is there, and the project's own hooks carry its key
        for name, path in self.installed_paths().items():
            self.assertTrue(path.is_file(), "%s: %s" % (name, path))
        self.assertTrue(spud.settings_hold_hooks(self.ctx(), self.installed_paths()["settings"], self.REPO_KEY))
        self.assertIn(".claude/settings.local.json", (self.repo / ".git" / "info" / "exclude").read_text(encoding="utf-8"))
        self.assertEqual(out["install"]["project"], self.REPO_KEY)
        self.assertIn(str(self.installed_paths()["agent"]), out["install"]["written"])
        # 8. the schedule step is reported as skipped, and launchctl was never called
        self.assertEqual(out["schedule"], {"skipped": "--no-schedule", "agents": []})
        self.assertEqual(self.launchctl_calls(), [])
        self.assertFalse(self.agents.exists())
        # 9. the render wrote ledger/Projects.md and the day file of the entry init wrote, and nothing else
        self.assertEqual(out["render"]["written"], ["ledger/Projects.md", self.report_day()])
        self.assertEqual(out["render"]["conflicts"], [])
        for rel in out["render"]["written"]:
            self.assertTrue((self.target / rel).is_file(), rel)
        self.assertIn("Spud initialized at", (self.target / self.report_day()).read_text(encoding="utf-8"))
        self.assertIn(self.REPO_KEY, (self.target / "ledger" / "Projects.md").read_text(encoding="utf-8"))
        # and what is left by hand is the two lines the design allows it (2.1), neither of them a spud command
        self.assertEqual(out["by_hand"].count("\n  - "), 2)
        self.assertIn("open %s as a vault in Obsidian" % self.target, out["by_hand"])
        self.assertNotIn("settings sync", out["by_hand"])

    def test_no_project_reaches_a_green_doctor_with_step_7_skipped(self):
        out = json.loads(self.spud("--json", *self.init_argv(project=False)).stdout)
        self.assertIsNone(out["install"])
        self.assertTrue(any("no project to install (--no-project)" in line for line in out["done"]), out["done"])
        # step 6 still ran -- the home's own hooks are nobody's project's -- and step 9 rendered the entry's day file
        self.assertEqual(out["render"]["written"], ["ledger/Projects.md", self.report_day()])
        report = json.loads(self.spud("--json", "doctor").stdout)
        self.assertEqual(report["problems"], [])  # step 6 among them, since SPW-006 gave doctor the `settings` check
        self.assertEqual(report["settings"]["missing"], [])
        self.assertTrue(any("no project is registered" in n for n in report["notes"]), report["notes"])
        # and nothing was installed at user scope: those two files are a project's, and there is no project
        self.assertFalse((self.user_claude / "agents" / "spudagent.md").exists())
        self.assertFalse((self.user_claude / "skills" / "spud" / "SKILL.md").exists())
        self.assertTrue(self.pointer.is_file())  # step 5 wrote it, not install

    def test_a_second_run_reports_every_step_of_the_tail_unchanged(self):
        self.spud(*self.init_argv())
        watched = [self.target / ".claude" / "settings.json", *self.installed_paths().values()]
        before = {p: p.read_bytes() for p in watched}
        stamps = {p: p.stat().st_mtime_ns for p in watched}
        rows = self.rows("SELECT path, sha256, through_event_id FROM renders ORDER BY path")
        out = json.loads(self.spud("--json", *self.init_argv()).stdout)
        self.assertFalse(out["settings"]["written"])
        self.assertTrue(any("installed: unchanged" in line for line in out["done"]), out["done"])
        self.assertEqual((out["render"]["written"], out["render"]["conflicts"]), ([], []))
        self.assertEqual(out["done"][-1], "10. doctor ok")
        self.assertEqual({p: p.read_bytes() for p in watched}, before)  # not one byte, and not one mtime
        self.assertEqual({p: p.stat().st_mtime_ns for p in watched}, stamps)
        self.assertEqual(self.rows("SELECT path, sha256, through_event_id FROM renders ORDER BY path"), rows)
        # the project.installed record and the render event follow a run that changed something, and this one did not
        self.assertEqual([r["kind"] for r in self.rows("SELECT kind FROM events WHERE kind IN ('project.installed', 'render') ORDER BY id")],
                         ["project.installed", "render"])


class InstallTailFailureTest(MachineMixin, unittest.TestCase):
    """Design 2.4's last five rows: what a failure in steps 6 to 10 leaves on disk, and that the report names every step
    that completed -- which is why `finish_install` appends each line as its step ends rather than at its own return.
    Init removes nothing in any of them, and each case then reruns and continues from where it stopped."""

    def setUp(self):
        self.machine()
        self.repo = self.make_repo("mine-", origin=True, name=self.REPO_DIR)

    def stopped_after(self, *args, code=EXIT_ERROR):
        proc = self.spud(*(args or self.init_argv()), check=False)
        self.assertEqual(proc.returncode, code, proc.stderr)
        self.assertIn("init stopped after:", proc.stderr)
        self.assertIn("nothing init wrote is removed", proc.stderr)
        return proc.stderr

    def test_6_settings_sync_leaves_a_complete_home_whose_own_hooks_are_missing(self):
        """2.4's sixth row.  A home holding a config is a Spud home already (refusal 2's one exception), so this is a
        first run: steps 1 to 5 complete, and step 6 fails on a settings file that is not JSON."""
        config = dict(real_config())
        config["tickets"], config["teams"] = dict(config["tickets"], prefix="ZZZ"), dict(config["teams"], prefix="ZZZS")
        (self.target / ".claude").mkdir(parents=True)
        (self.target / "spud.config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        settings = self.target / ".claude" / "settings.json"
        settings.write_text("{not json", encoding="utf-8")
        message = self.stopped_after()
        self.assertIn("is not valid JSON", message)
        self.assertIn("5. %s names %s" % (self.pointer, self.target), message)  # every step before it completed
        self.assertNotIn("\n  6. ", message)
        self.assertTrue((self.target / ".spud" / "ledger.db").is_file())  # a complete home, with no hooks of its own
        self.assertFalse(load_spud_module().settings_hold_hooks(self.ctx(), settings))
        settings.unlink()
        out = json.loads(self.spud("--json", *self.init_argv()).stdout)  # and the rerun continues from step 6
        self.assertTrue(out["settings"]["written"])
        self.assertEqual(out["doctor"]["problems"], [])

    def test_7_project_install_stops_with_step_6_named_and_nothing_undone(self):
        """2.4's seventh row.  `install_project` refuses to write into a tracked tree, which is a failure only step 7
        can have: the home is complete and its own hooks are in place, so the report must name step 6 as completed."""
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "settings.local.json").write_text("{}\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "tracked settings")
        message = self.stopped_after()
        self.assertIn("install writes nothing in the tracked tree", message)
        self.assertIn("6. %s written" % (self.target / ".claude" / "settings.json"), message)
        self.assertNotIn("\n  7. ", message)
        self.assertTrue(load_spud_module().settings_hold_hooks(self.ctx(), self.target / ".claude" / "settings.json"))
        self.assertFalse((self.user_claude / "agents" / "spudagent.md").exists())
        git(self.repo, "rm", "-q", "--cached", ".claude/settings.local.json")
        out = json.loads(self.spud("--json", *self.init_argv()).stdout)
        self.assertEqual(out["install"]["project"], self.REPO_KEY)
        self.assertEqual(out["doctor"]["problems"], [])

    def test_9_a_hand_edited_render_target_stops_the_command_at_the_render(self):
        """2.4's ninth row.  The guard test of the design's 4.4 keeps a shipped file off a render target, so a fresh
        home cannot collide by itself -- it takes a hand edit of a file init has already rendered."""
        self.spud(*self.init_argv())
        projects = self.target / "ledger" / "Projects.md"
        projects.write_text("mine, now\n", encoding="utf-8")
        message = self.stopped_after()
        self.assertIn("found 1 conflict(s)", message)
        self.assertIn("render --discard", message)
        self.assertNotIn("\n  9. ", message)  # step 9 is the one that did not complete
        self.assertIn("8. --no-schedule", message)
        self.assertEqual(projects.read_text(encoding="utf-8"), "mine, now\n")  # the hand edit is not overwritten
        self.spud("--as", "spud", "render", "--discard", projects)
        self.assertEqual(json.loads(self.spud("--json", *self.init_argv()).stdout)["doctor"]["problems"], [])

    def test_9_a_vault_behind_the_ledger_is_the_other_reading_of_the_same_refusal(self):
        """Step 9's set is a fresh ledger's, and init is also the migration path ('the database is behind; run `spud
        init`'), so a rerun whose pass writes a note that was never rendered is refused too.  The message says which
        reading it is, and the pass has brought the vault up to date, so the next run is green."""
        self.spud(*self.init_argv())
        self.spud("--as", "spud", "ticket", "new", "--title", "Behind")  # no watcher here: the note is never rendered
        message = self.stopped_after()
        self.assertIn("wrote ledger/tickets/ZZZ-001.md", message)
        self.assertIn("this home was not fresh and its vault was behind", message)
        self.assertIn("a rerun is green", message)
        self.assertTrue((self.target / "ledger" / "tickets" / "ZZZ-001.md").is_file())  # the pass wrote it before refusing
        out = json.loads(self.spud("--json", *self.init_argv()).stdout)
        self.assertEqual((out["render"]["written"], out["doctor"]["problems"]), ([], []))

    def test_10_doctor_red_fails_the_command_with_the_report_attached_and_the_home_complete(self):
        """2.4's tenth row: a problem init does not refuse and doctor does -- here a git hook planted in the project
        init is about to register, which doctor's repositories section reads (SPD-123)."""
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho planted\n", encoding="utf-8")
        proc = self.spud("--json", *self.init_argv(), check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("doctor on %s found 1 problem(s)" % self.target, out["error"])
        self.assertIn(str(hook), out["error"])
        self.assertEqual(len(out["problems"]), 1)  # the report itself is attached, not the message alone
        self.assertIn("9. render", "\n".join(out["done"]))  # step 9 completed; step 10 is the one that failed
        # a complete home: the settings, the install and the pointer all stand, and nothing is undone
        self.assertTrue(load_spud_module().settings_hold_hooks(self.ctx(), self.target / ".claude" / "settings.json"))
        self.assertTrue((self.user_claude / "skills" / "spud" / "SKILL.md").is_file())
        self.assertTrue(self.pointer.is_file())
        hook.unlink()
        self.assertEqual(json.loads(self.spud("--json", *self.init_argv()).stdout)["doctor"]["problems"], [])


class ScheduleStepTest(MachineMixin, unittest.TestCase):
    """Step 8 itself: the two LaunchAgents, its two skips, and 2.4's eighth row.  The one class here that lets init
    reach `launchctl` -- `helpers.fake_launchctl`'s, never this Mac's own, whose domain and two labels are the ones a
    real `schedule install` uses and which SPUD_LAUNCH_AGENTS_DIR does not move."""

    def setUp(self):
        self.machine()
        self.repo = self.make_repo("mine-", origin=True, name=self.REPO_DIR)

    def test_the_fake_launchctl_replaced_helpers_refusing_stub_not_bin_launchctl(self):
        """SPW-011: the default every fixture inherits is helpers' refusing stub, so a class that reaches step 8 without
        `fake_launchctl` fails on a refusal instead of booting out this Mac's two jobs.  This is the class that replaces
        it, and what it replaced was never /bin/launchctl."""
        self.assertEqual(self.env["SPUD_LAUNCHCTL"], str(self.launchctl))
        self.assertNotEqual(str(self.launchctl), str(GUARD_LAUNCHCTL))
        self.assertNotEqual(self.env["SPUD_LAUNCHCTL"], "/bin/launchctl")
        self.assertTrue(self.launchctl.is_file())
        self.assertEqual(self.launchctl_calls(), [])

    @unittest.skipUnless(sys.platform == "darwin", "step 8 installs LaunchAgents only where launchctl lives")
    def test_init_installs_and_loads_both_launch_agents(self):
        out = json.loads(self.spud("--json", *self.init_argv(schedule=True)).stdout)
        self.assertIsNone(out["schedule"]["skipped"])
        self.assertEqual([r["label"] for r in out["schedule"]["agents"]], ["local.spud.backup", "local.spud.render"])
        self.assertEqual(out["schedule"]["at"], "03:00")
        for label in ("local.spud.backup", "local.spud.render"):
            self.assertTrue((self.agents / (label + ".plist")).is_file(), label)
            self.assertTrue((self.launchctl_state / ("loaded-" + label)).exists(), label)
        self.assertEqual([c[0] for c in self.launchctl_calls()], ["bootout", "bootstrap", "bootout", "bootstrap"])
        # and init still ends green: the fake launchctl loads no process, so the vault's watcher is installed and not
        # running -- doctor's one problem, and the one step 10 excuses, because it was bootstrapped seconds ago
        self.assertEqual(out["doctor"]["problems"], [load_spud_module().WATCHER_DOWN])
        self.assertIn("is not up yet", out["done"][-1])

    @unittest.skipUnless(sys.platform == "darwin", "step 8 installs LaunchAgents only where launchctl lives")
    def test_8_a_bootstrap_that_fails_leaves_the_plist_and_names_the_way_out(self):
        """2.4's eighth row: the plist written and not loaded, `install_agents` saying so and naming `schedule
        uninstall`, and every step before it named.  The rerun takes 2.4's own second option, `--no-schedule`."""
        proc = self.spud(*self.init_argv(schedule=True), check=False, env={"FAKE_LAUNCHCTL_BOOTSTRAP_FAILURES": "all"})
        self.assertEqual(proc.returncode, EXIT_ERROR, proc.stderr)
        message = proc.stderr
        self.assertIn("init stopped after:", message)
        self.assertIn("launchctl bootstrap gui/", message)
        self.assertIn("spud schedule uninstall removes it", message)
        self.assertIn("7. project %s installed" % self.REPO_KEY, message)
        self.assertNotIn("\n  8. ", message)
        self.assertTrue((self.agents / "local.spud.backup.plist").is_file())  # written, and never loaded
        self.assertFalse((self.launchctl_state / "loaded-local.spud.backup").exists())
        out = json.loads(self.spud("--json", *self.init_argv()).stdout)  # --no-schedule, and the rest of the tail runs
        self.assertEqual(out["schedule"]["skipped"], "--no-schedule")
        self.assertEqual(out["doctor"]["problems"], [])
        self.assertEqual(out["done"][-1], "10. doctor ok")

    def test_step_8_is_skipped_with_a_note_where_launchctl_does_not_live(self):
        """The `sys.platform != "darwin"` skip, tested as the branch it is rather than by pretending this Mac is Linux:
        `install_schedule` and `init_steps` both read `sys.platform`, and neither may call `install_agents` there."""
        spud = load_spud_module()
        ctx = self.ctx()
        args = mock.Mock(no_schedule=False, landing="merge", sessions="claim")
        plan = {"config_exists": True, "ticket_prefix": "ZZZ", "team_prefix": "ZZZS", "project_root": str(self.repo),
                "project_key": self.REPO_KEY, "project_name": self.REPO_DIR, "name": "Spud", "pronouns": ["he", "him", "his"]}
        with mock.patch("spudlib.commands.schedule.install_agents") as install_agents:
            with mock.patch.object(sys, "platform", "linux"):
                done = []
                self.assertEqual(spud.install_schedule(ctx, args, done),
                                 {"skipped": "linux is not darwin, and launchctl is macOS's", "agents": []})
                self.assertIn("linux is not darwin", done[0])
                self.assertIn("local.spud.render not installed", done[0])
                self.assertIn("schedule install", done[0])
                self.assertIn("linux is not darwin", spud.init_steps(ctx, args, plan)[7])  # and the dry run says so too
            self.assertFalse(install_agents.called)
            args.no_schedule = True  # the other skip, on this platform, naming itself
            done = []
            self.assertEqual(spud.install_schedule(ctx, args, done)["skipped"], "--no-schedule")
            self.assertIn("8. --no-schedule", done[0])
            self.assertIn("--no-schedule", spud.init_steps(ctx, args, plan)[7])
            self.assertFalse(install_agents.called)


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
        self.spud("init", "--home", first, "--no-project", "--no-schedule", "--ticket-prefix", "ZZZ", "--team-prefix", "ZZZS",
                  "--owner-name", "Pat")
        message = self.refused(*self.init_argv())
        self.assertIn("names %s, not %s" % (first, self.target), message)
        self.assertIn("--repoint", message)
        out = json.loads(self.spud("--json", *self.init_argv("--repoint")).stdout)
        self.assertTrue(out["pointer"]["written"])
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), str(self.target))
        self.assertTrue(any("that home is untouched" in line for line in out["done"]), out["done"])
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
        return self.refused("init", "--home", self.target, "--project-root", root, "--ticket-prefix", ticket, "--team-prefix", team,
                            "--owner-name", "Pat", *extra)

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
        message = self.refused("init", "--home", self.target, "--project-root", self.repo, "--owner-name", "Pat", code=EXIT_USAGE)
        self.assertIn("has no spud.config.json yet, and a config carries the two prefixes", message)
        self.assertIn("--ticket-prefix XXX --team-prefix XXXS", message)
        self.assertFalse(self.target.exists())

    def test_the_owner_name_has_no_default_either(self):
        """SPD-157: the third value a run off a tty must be given.  A person's name cannot be guessed, and it goes into
        the CLAUDE.md the tool generates, the brief template and the root note -- so a home is not built without one,
        and the refusal names the flag and says the pronouns have a default."""
        message = self.refused("init", "--home", self.target, "--project-root", self.repo,
                               "--ticket-prefix", "ZZZ", "--team-prefix", "ZZZS", code=EXIT_USAGE)
        self.assertIn("a config carries the name of the person the home is for", message)
        self.assertIn("--owner-name", message)
        self.assertIn("they/them/their", message)
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
        # SPW-001 phase 4: init renders the home it builds, so its own pass leaves the render lock it took and the
        # watermark every pass writes (SPD-117).  Nothing else: no backup, no watch lock, no log directory.
        for name in entries:
            self.assertTrue(name.startswith("ledger.db") or name in ("backups", "render.lock", "rendered.json"), name)


if __name__ == "__main__":
    unittest.main()
