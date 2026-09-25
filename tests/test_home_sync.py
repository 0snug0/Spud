"""SPD-157: `spud home sync`, the command that writes every file the tool owns in a home again from `<tool>/share/`.

The set of those files, and the rendering, are `commands/homesync`'s and shared with `spud init`, which writes the same
files into a home that does not exist yet -- so a good half of what is asserted here is that the two commands agree:
what init wrote, sync calls unchanged, and what sync writes, an init of a fresh home would have written.

Three properties every case leans on.

- **Nothing is written until everything can be.**  A `{{mark}}` with no value refuses the whole sync, and the home is
  as it was: the hand edit that would have been replaced is still there and no copy was kept.
- **A copy is kept before every overwrite**, named in the output, and `--check` names the copy it *would* keep while
  writing nothing at all -- no file, no copy, and no download (every home here has SPUD_VAULT_DOWNLOADS=off, so a
  check that fetched anything would be refused and say so).
- **The home's own files are never touched.**  The ledger's rendered notes, the reports, docs/, the database and
  spud.config.json belong to the home; a sync that moved one of them would be a sync that ate somebody's work.

`tests/helpers.Home.init` registers project 1 through init's own step 3 (SPD-233), so a fresh home here already holds
what the tool ships for it and `settle()`, the sync every case that wants a settled home calls first, finds nothing to
do; NoProjectTest is the first real sync that reconciles a home whose registry changed after init.
"""

import json
import os
import shutil
import unittest
from unittest import mock

from helpers import EXIT_ERROR, EXIT_OWNERSHIP, Home, RepoMixin, SpudTestCase, load_spud_module

spud = load_spud_module()

HAND_EDIT = "\n\nA line Spud wrote by hand, which a sync takes back.\n"
SKILL = "---\nname: spud-reference\ndescription: The detail behind the laws.\n---\n\n# In {{home}}\n"


class HomeSyncCase(SpudTestCase):
    """An initialised scratch home, with the CLI helpers every case below shares."""

    def sync(self, *extra, actor="spud", check=True):
        return self.home.json("home", "sync", *extra, actor=actor, check=check)

    def text(self, *extra, actor="spud"):
        return self.home.run("home", "sync", *extra, actor=actor).stdout

    def settle(self):
        """The one real sync that makes the home hold exactly what the tool ships now; its record."""
        return self.sync()

    def home_file(self, rel):
        return self.home.path / rel

    def read(self, rel):
        return self.home_file(rel).read_text(encoding="utf-8")

    def backups(self):
        return self.home.path / ".spud" / "backups" / spud.HOME_SYNC_BACKUPS

    def stamps(self):
        """The `<when>` directories this command's copies are kept under, as a set: a run that keeps no copy adds none,
        and `settle()` has usually added one already, so a case asserts the set is the one it started with."""
        return set(os.listdir(self.backups())) if self.backups().is_dir() else set()

    def snapshot(self):
        """{path in the home: its bytes} for every file in it, and what "nothing was written" is asserted against.

        `.spud/` is left out because the CLI opens the ledger before it renders anything and a read moves the WAL; the
        copies a sync keeps live under it, and `stamps()` is what watches those.  The shipped files a test edits on
        purpose, and a sync only ever reads, are the tool's share/, beside the home."""
        out = {}
        for root, dirs, files in os.walk(self.home.path):
            dirs[:] = [d for d in dirs if os.path.join(root, d) != str(self.home.path / ".spud")]
            for name in files:
                path = os.path.join(root, name)
                with open(path, "rb") as f:
                    out[os.path.relpath(path, self.home.path)] = f.read()
        return out

    def ctx(self):
        return spud.Ctx(self.home.path, "SPUD_HOME", False, tool=self.home.tool)

    def owned(self):
        """Every tool-owned file's path in the home, as `commands/homesync.tool_owned` gives it."""
        return [home_rel for _rel, home_rel in spud.tool_owned(self.ctx())]


class SyncTest(HomeSyncCase):
    """What a sync writes, what it leaves alone, and the copy it keeps of everything it replaces."""

    def test_a_sync_of_a_home_init_just_built_changes_nothing_and_neither_does_a_second(self):
        """Init registered project 1 before it wrote the home (SPD-233), so its lines are there already and nothing is
        settled; NoProjectTest brings them back for a project registered after init."""
        first = self.settle()
        self.assertFalse(first["check"])
        self.assertEqual((first["written"], first["replaced"]), ([], []))
        self.assertNotIn("{{project_key}}", self.read("CLAUDE.md"))
        second = self.sync()
        self.assertEqual((second["written"], second["replaced"]), ([], []))
        self.assertEqual(sorted(second["unchanged"]), sorted(self.owned()))

    def test_every_file_the_tool_owns_is_one_of_the_three_answers_and_no_other_file_is_named(self):
        record = self.settle()
        named = record["written"] + [r["path"] for r in record["replaced"]] + record["unchanged"]
        self.assertEqual(sorted(named), sorted(self.owned()))
        for rel in ("CLAUDE.md", "ledger/Home.md", "ledger/Spud.md", "ledger/Board.base", "ledger/Fleet.base",
                    "ledger/_templates/ticket.md", "ledger/_templates/spudagent.md"):
            self.assertIn(rel, named)

    def test_a_tool_owned_file_the_home_does_not_have_is_written(self):
        self.settle()
        os.remove(self.home_file("ledger/Spud.md"))
        record = self.sync()
        self.assertEqual(record["written"], ["ledger/Spud.md"])
        self.assertEqual(record["replaced"], [])
        self.assertTrue(self.home_file("ledger/Spud.md").is_file())

    def test_a_hand_edited_file_is_replaced_and_the_copy_it_held_is_kept_and_named(self):
        self.settle()
        shipped = self.read("CLAUDE.md")
        self.home_file("CLAUDE.md").write_text(shipped + HAND_EDIT, encoding="utf-8")
        record = self.sync()
        replaced = {r["path"]: r["backup"] for r in record["replaced"]}
        self.assertEqual(sorted(replaced), ["CLAUDE.md"])
        self.assertEqual(self.read("CLAUDE.md"), shipped)
        with open(replaced["CLAUDE.md"], encoding="utf-8") as f:
            self.assertEqual(f.read(), shipped + HAND_EDIT)
        # the copy says what was overwritten and when: <backups>/home-sync/<the run's time>/<its path in the home>
        self.assertEqual(os.path.basename(replaced["CLAUDE.md"]), "CLAUDE.md")
        self.assertEqual(os.path.dirname(os.path.dirname(replaced["CLAUDE.md"])), str(self.backups()))
        self.assertTrue(replaced["CLAUDE.md"].startswith(record["backups"] + os.sep))

    def test_a_copy_is_kept_of_a_file_in_a_directory_too_at_its_own_path_under_the_stamp(self):
        self.settle()
        self.home_file("ledger/_templates/ticket.md").write_text("mine now\n", encoding="utf-8")
        record = self.sync()
        kept = {r["path"]: r["backup"] for r in record["replaced"]}["ledger/_templates/ticket.md"]
        self.assertEqual(kept, os.path.join(record["backups"], "ledger", "_templates", "ticket.md"))
        with open(kept, encoding="utf-8") as f:
            self.assertEqual(f.read(), "mine now\n")

    def test_a_second_run_in_the_same_second_keeps_its_copies_in_a_folder_of_its_own(self):
        """SPD-162: the stamp reads to the second, and two runs of this command inside one second are ordinary -- the
        real sync right after the `--check` that settled it, a run repeated while a download is fixed.  Sharing the
        folder, the second run's copy of a file both replaced overwrote the first run's, which was the only copy of
        the hand edit that run took back.  The clock is frozen here because that is the case: every run answers the
        same second, and each still gets a folder no other run has used, in an order a listing still reads as time."""
        with mock.patch("spudlib.core.kernel.now", lambda: "2026-09-22T14:55:09-07:00"):
            ctx, stamps = self.ctx(), []
            for _ in range(3):
                stamp = spud.copy_stamp(ctx, spud.HOME_SYNC_BACKUPS)
                self.assertNotIn(stamp, stamps)
                (self.backups() / stamp).mkdir(parents=True)
                stamps.append(stamp)
        self.assertEqual(stamps, ["2026-09-22T14-55-09-07-00", "2026-09-22T14-55-09-07-00-02",
                                  "2026-09-22T14-55-09-07-00-03"])
        self.assertEqual(stamps, sorted(stamps))  # oldest first in any listing, as the folder's whole point is

    def test_two_syncs_that_each_take_back_an_edit_leave_two_copies(self):
        """The property the stamp is for, through the command itself: the copy a run keeps is still there after the
        next run keeps its own of the same file."""
        self.settle()
        shipped = self.read("ledger/Home.md")
        kept = []
        for text in ("first edit\n", "second edit\n"):
            self.home_file("ledger/Home.md").write_text(text, encoding="utf-8")
            record = self.sync()
            kept.append({r["path"]: r["backup"] for r in record["replaced"]}["ledger/Home.md"])
        self.assertEqual(self.read("ledger/Home.md"), shipped)
        self.assertNotEqual(kept[0], kept[1])
        for path, text in zip(kept, ("first edit\n", "second edit\n")):
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), text)

    def test_the_output_names_every_file_it_wrote_and_every_copy_it_kept(self):
        self.settle()
        self.home_file("ledger/Home.md").write_text("mine now\n", encoding="utf-8")
        os.remove(self.home_file("ledger/Board.base"))
        out = self.text()
        self.assertIn("wrote ledger/Board.base", out)
        self.assertIn("replaced ledger/Home.md", out)
        self.assertIn("a copy of each replaced file is kept under %s/" % self.backups(), out)
        self.assertIn(spud.UNTOUCHED, out)
        self.assertNotIn(spud.NOTHING_WRITTEN, out)

    def test_it_never_touches_the_ledger_s_own_files_the_reports_the_config_or_the_database(self):
        self.settle()
        ticket = self.new_ticket("A ticket", status="active")
        self.home.run("render", actor="spud")
        note = "ledger/tickets/%s.md" % ticket["key"]
        mine = {rel: self.read(rel) for rel in ("spud.config.json", "ledger/Projects.md", note)}
        self.home_file("docs/design/mine.md").parent.mkdir(parents=True, exist_ok=True)
        self.home_file("docs/design/mine.md").write_text("a design note\n", encoding="utf-8")
        record = self.sync()
        named = record["written"] + [r["path"] for r in record["replaced"]] + record["unchanged"]
        for rel, before in mine.items():
            self.assertEqual(self.read(rel), before, rel)
            self.assertNotIn(rel, named)
        self.assertEqual(self.read("docs/design/mine.md"), "a design note\n")


class NoProjectTest(RepoMixin, HomeSyncCase):
    """A home with an empty registry: the lines about project 1 stay out, and the command says how many it left out; once a
    project is registered, a sync brings them back."""

    seed_project = False

    def test_a_first_sync_after_project_1_is_registered_reconciles_the_home_and_a_second_changes_nothing(self):
        self.add_project(self.make_repo("first-"), "first", "SPD", "SPUD", "merge")
        first = self.settle()
        self.assertIn("CLAUDE.md", [r["path"] for r in first["replaced"]])
        self.assertNotIn("{{project_key}}", self.read("CLAUDE.md"))
        second = self.sync()
        self.assertEqual((second["written"], second["replaced"]), ([], []))

    def test_the_lines_about_project_1_stay_out_and_the_count_is_reported(self):
        record = self.sync()
        self.assertGreater(record["dropped_lines"], 0)
        self.assertNotIn("{{project_key}}", self.read("CLAUDE.md"))
        self.assertIn("line(s) about project 1 left out", self.text())

    def test_a_sync_of_a_home_init_just_built_changes_nothing(self):
        """Init and sync render the same file the same way: with no project on either side there is nothing to settle."""
        record = self.sync()
        self.assertEqual((record["written"], record["replaced"]), ([], []))


class NoOwnerTest(HomeSyncCase):
    """SPD-157: a config written before the `owner` block existed -- which is every home built until now.

    The sync must run, and every file it writes must read as prose rather than as a template with a hole in it: the
    four owner marks fall back to `core/shipped.DEFAULT_OWNER` and never to nothing, because a generated file is one
    nobody may fix by hand.  `spud doctor` is what says the name is missing (tests/test_doctor.OwnerCheckTest)."""

    def setUp(self):
        super().setUp()
        path = self.home.path / "spud.config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        del config["owner"]
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    def test_a_sync_writes_every_file_and_the_placeholder_stands_in_for_the_name(self):
        record = self.settle()
        named = record["written"] + [r["path"] for r in record["replaced"]] + record["unchanged"]
        self.assertEqual(sorted(named), sorted(self.owned()))
        claude_md = self.read("CLAUDE.md")
        self.assertNotIn("{{", claude_md)
        self.assertIn("the owner is the person you work for", claude_md)
        self.assertIn("the owner's first fork", self.read("ledger/Spud.md"))
        self.assertEqual((self.sync()["written"], self.sync()["replaced"]), ([], []))


class CheckTest(HomeSyncCase):
    """`--check`: the report Spud shows Eric before the first real sync, and the writing it does not do."""

    def test_check_writes_nothing_and_leaves_every_difference_as_it_was(self):
        self.settle()
        edited = self.read("ledger/Spud.md") + HAND_EDIT
        self.home_file("ledger/Spud.md").write_text(edited, encoding="utf-8")
        os.remove(self.home_file("ledger/Fleet.base"))
        before = self.stamps()
        record = self.sync("--check")
        self.assertTrue(record["check"])
        self.assertEqual(record["written"], ["ledger/Fleet.base"])
        self.assertEqual([r["path"] for r in record["replaced"]], ["ledger/Spud.md"])
        self.assertEqual(self.read("ledger/Spud.md"), edited)
        self.assertFalse(self.home_file("ledger/Fleet.base").exists())
        self.assertEqual(self.stamps(), before)
        self.assertFalse(os.path.exists(record["replaced"][0]["backup"]))

    def test_the_report_names_what_would_be_written_what_is_identical_and_the_copy_it_would_keep(self):
        self.settle()
        self.home_file("CLAUDE.md").write_text("mine\n", encoding="utf-8")
        os.remove(self.home_file("ledger/Fleet.base"))
        out = self.text("--check")
        self.assertIn("would write ledger/Fleet.base", out)
        self.assertIn("would replace CLAUDE.md", out)
        self.assertIn("a copy of each replaced file would be kept under %s/" % self.backups(), out)
        self.assertIn("ledger/Spud.md", out.split("\n  unchanged ", 1)[1])
        self.assertTrue(out.rstrip().endswith(spud.NOTHING_WRITTEN), out)

    def test_a_check_of_a_settled_home_reports_every_file_identical_and_none_to_write(self):
        self.settle()
        record = self.sync("--check")
        self.assertEqual((record["written"], record["replaced"]), ([], []))
        self.assertEqual(sorted(record["unchanged"]), sorted(self.owned()))

    def test_a_check_makes_no_download_where_a_real_sync_refuses_them(self):
        """Every home in the suite has SPUD_VAULT_DOWNLOADS=off, so a plugin a real sync would fetch is refused by
        name.  A check that reported the same refusals would be a check that had tried to fetch them."""
        real = self.settle()["vault"]
        self.assertTrue(real["refused"])
        check = self.sync("--check")["vault"]
        self.assertEqual(check["refused"], [])
        self.assertTrue(check["check"])

    def test_a_check_keeps_no_copy_of_a_vault_file_it_would_replace(self):
        self.settle()
        settings = self.home.path / spud.OBSIDIAN / "app.json"
        settings.write_text('{"defaultViewMode": "source"}\n', encoding="utf-8")
        record = self.sync("--check")["vault"]
        kept = [r["backup"] for r in record["replaced"] if r["path"] == "app.json"]
        self.assertEqual(len(kept), 1)
        self.assertFalse(os.path.exists(kept[0]))
        self.assertEqual(settings.read_text(encoding="utf-8"), '{"defaultViewMode": "source"}\n')


class BoardSyncTest(RepoMixin, HomeSyncCase):
    """SPD-324: the Kanban board of every non-archived project, `ledger/<project name>.base`, written when absent and
    never replaced.  Init writes none, so the first sync of every home here writes project 1's."""

    def board_of(self, key):
        return "ledger/%s.base" % self.cli_json("project", "show", key)["project"]["name"]

    def test_a_sync_backfills_every_project_s_board_and_a_second_writes_none(self):
        self.add_project(self.make_repo("second-"), "second", "SEC", "SECS", "merge", "--name", "Second Project")
        os.remove(self.home_file("ledger/Second Project.base"))  # add wrote it; a home from before this ticket has none
        spud_board = self.board_of("spud")
        self.assertFalse(self.home_file(spud_board).exists())
        first = self.sync()
        self.assertEqual([(b["project"], b["path"], b["board"]) for b in first["boards"]],
                         [("spud", spud_board, "written"), ("second", "ledger/Second Project.base", "written")])
        self.assertNotIn(spud_board, first["written"] + first["unchanged"])  # a board is no tool-owned file
        self.assertIn('project == "second"', self.read("ledger/Second Project.base"))
        second = self.sync()
        self.assertEqual([b["board"] for b in second["boards"]], ["kept", "kept"])
        self.assertEqual((second["written"], second["replaced"]), ([], []))
        out = self.text()
        self.assertIn("ledger/Second Project.base is already there, so project second's board is left as it is", out)

    def test_a_board_somebody_tuned_is_kept_byte_for_byte_and_no_copy_is_made(self):
        self.settle()
        board = self.board_of("spud")
        self.home_file(board).write_text("views: []\n", encoding="utf-8")
        stamps = self.stamps()
        self.assertEqual(self.sync()["boards"][0]["board"], "kept")
        self.assertEqual(self.read(board), "views: []\n")
        self.assertEqual(self.stamps(), stamps)

    def test_an_archived_project_gets_no_board(self):
        repo = self.make_repo("gone-")
        self.add_project(repo, "gone", "GON", "GONS", "merge", "--name", "Gone")
        ticket = self.new_ticket("Keeps it archived", project="gone")
        self.cli("ticket", "move", ticket["key"], "--status", "declined", actor="spud")
        self.assertIn("archived", self.cli("project", "remove", "gone", actor="spud").stdout)
        os.remove(self.home_file("ledger/Gone.base"))
        record = self.sync()
        self.assertEqual([b["project"] for b in record["boards"]], ["spud"])
        self.assertFalse(self.home_file("ledger/Gone.base").exists())

    def test_check_names_the_board_it_would_write_and_writes_none(self):
        board = self.board_of("spud")
        record = self.sync("--check")
        self.assertEqual(record["boards"][0]["board"], "written")
        self.assertFalse(self.home_file(board).exists())
        self.assertIn("would write %s, project spud's board" % board, self.text("--check"))
        self.assertFalse(self.home_file(board).exists())

    def test_a_board_template_that_is_gone_refuses_before_anything_is_written(self):
        self.home_file("ledger/Home.md").write_text("mine now\n", encoding="utf-8")
        os.remove(self.home.own_share() / spud.BOARD_TEMPLATE)
        proc = self.home.run("home", "sync", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("no shipped", proc.stderr)
        self.assertEqual(self.read("ledger/Home.md"), "mine now\n")
        self.assertFalse(self.home_file(self.board_of("spud")).exists())


class RefusalTest(HomeSyncCase):
    """The refusals that come before any write, and the one about who may run the command."""

    def test_a_mark_with_no_value_refuses_the_whole_sync_before_anything_is_written(self):
        """The mark goes in the *last* file the sync would reach, and every file before it is one the sync would have
        touched: the first of them hand-edited, the second missing, a vault settings file changed.  A command that
        rendered and wrote file by file would have replaced the edit, written the missing file and kept copies of both
        before it ever read the skill, so it would pass a mark planted in the first file and fail here.  What is
        asserted is the whole home, byte for byte: not one file written, not one copy kept."""
        self.settle()
        last = spud.tool_owned(self.ctx())[-1][0]
        self.assertEqual(last, "skills/spud-reference/SKILL.md")  # the order the refusal has to survive
        self.home.shipped(last, self.home.shipped(last) + "\n{{nobody_fills_this}}\n")
        self.home_file("CLAUDE.md").write_text("mine now\n", encoding="utf-8")  # the first file, hand-edited
        os.remove(self.home_file("ledger/Home.md"))                             # the second, gone
        vault = self.home.path / spud.OBSIDIAN / "app.json"
        vault.write_text('{"defaultViewMode": "source"}\n', encoding="utf-8")
        before, stamps = self.snapshot(), self.stamps()
        proc = self.home.run("home", "sync", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("{{nobody_fills_this}}", proc.stderr)
        self.assertIn(last, proc.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.stamps(), stamps)

    def test_a_mark_with_no_value_refuses_a_check_too_and_the_message_is_the_same(self):
        self.home.shipped("ledger/Home.md", "{{nobody_fills_this}}\n")
        proc = self.home.run("home", "sync", "--check", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("{{nobody_fills_this}}", proc.stderr)
        self.assertIn("ledger/Home.md", proc.stderr)

    def test_a_shipped_file_that_is_gone_refuses_before_anything_is_written(self):
        self.settle()
        os.remove(self.home.own_share() / "ledger" / "Spud.md")
        self.home_file("ledger/Home.md").write_text("mine now\n", encoding="utf-8")
        proc = self.home.run("home", "sync", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("no shipped", proc.stderr)
        self.assertEqual(self.read("ledger/Home.md"), "mine now\n")

    def test_a_lock_this_tool_will_not_install_from_refuses_before_anything_is_written(self):
        self.settle()
        self.home.shipped(spud.LOCK, json.dumps(
            {"plugins": [{"id": "../escape", "views": [], "version": "1", "repo": "n/x",
                          "files": [{"name": "main.js", "url": "https://x/y", "sha256": "0"}]}], "themes": []}))
        self.home_file("ledger/Home.md").write_text("mine now\n", encoding="utf-8")
        proc = self.home.run("home", "sync", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("is not a usable lock", proc.stderr)
        self.assertEqual(self.read("ledger/Home.md"), "mine now\n")

    def test_the_sync_is_spud_s(self):
        t = self.new_ticket("A ticket", status="active")
        m = self.new_member(t["key"], deliverable=["home:docs/x.md"])  # no bare glob: this ticket binds no worktree
        for extra in ([], ["--check"]):
            proc = self.home.run("home", "sync", *extra, actor=m["ref"], check=False)
            self.assertEqual(proc.returncode, EXIT_OWNERSHIP, proc.stderr)
            self.assertIn("is Spud's", proc.stderr)


class SpudOnlyTest(unittest.TestCase):
    """RefusalTest's Law 6 half that is a table of the program's: no home."""

    def test_the_command_is_one_the_bash_hook_holds_to_spud_alone(self):
        """`home sync` rewrites CLAUDE.md, the two ledger notes, the templates and the views -- every one of them a
        `SPUD_PATHS` file the edit hook already refuses a member -- so Law 6 refuses the command line too."""
        self.assertIn(("home", "sync"), spud.SPUD_ONLY_SUBCOMMANDS)


class SkillPathTest(unittest.TestCase):
    """`share/skills/<name>/` is the one shipped directory whose path in a home is not its path under share/: a home
    reads a skill from `.claude/skills/`, where Claude Code looks for one.  The map is a function of the path alone, and
    a new home's init is the test's own home: neither needs the class fixture ShippedSkillTest restores."""

    def test_the_share_relative_path_maps_to_the_home_s_claude_skills(self):
        self.assertEqual(spud.home_relative("skills/spud-reference/SKILL.md"),
                         ".claude/skills/spud-reference/SKILL.md")
        self.assertEqual(spud.home_relative("skills"), ".claude/skills")
        self.assertEqual(spud.home_relative("CLAUDE.md"), "CLAUDE.md")
        self.assertEqual(spud.home_relative("ledger/Home.md"), "ledger/Home.md")

    def test_a_new_home_gets_it_through_init(self):
        second = Home(warm=True)
        self.addCleanup(second.cleanup)
        second.shipped_skill("spud-reference", SKILL)
        data = second.json("init", "--no-schedule")
        self.assertIn(".claude/skills/spud-reference/SKILL.md", data["scaffolding"]["written"])
        written = (second.path / ".claude" / "skills" / "spud-reference" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("# In %s" % second.path, written)


class ShippedSkillTest(HomeSyncCase):
    """A skill shipped under the tool's share/ reaches an initialised home through a sync (SkillPathTest: the path)."""

    def test_a_home_built_before_the_skill_shipped_gets_it_from_a_sync(self):
        """A tool from before any skill shipped, and the home it built: neither has one, and one sync writes it."""
        shutil.rmtree(self.home.own_share() / spud.SKILLS, ignore_errors=True)
        shutil.rmtree(self.home.path / ".claude" / "skills", ignore_errors=True)
        self.settle()
        self.home.shipped_skill("spud-reference", SKILL)
        record = self.sync()
        self.assertEqual(record["written"], [".claude/skills/spud-reference/SKILL.md"])
        self.assertIn("# In %s" % self.home.path, self.read(".claude/skills/spud-reference/SKILL.md"))
        self.assertIn(".claude/skills/spud-reference/SKILL.md", self.sync()["unchanged"])

    def test_a_tool_that_ships_no_skill_owns_the_scaffolding_alone(self):
        shutil.rmtree(self.home.own_share() / spud.SKILLS, ignore_errors=True)
        ctx = self.ctx()
        self.assertEqual(spud.shipped_skills(ctx), [])
        self.assertEqual([rel for rel, _home_rel in spud.tool_owned(ctx)], list(spud.SCAFFOLDING))


if __name__ == "__main__":
    unittest.main()
