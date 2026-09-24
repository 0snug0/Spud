"""spud project install|uninstall|sync and doctor's projects section (SPD-014, design sections 2.1 to 2.4 and 2.7).  The
other repository holds a local settings file shaped like BadTakes' and written in its own style, so that uninstall must give
back its exact bytes.  User-scope files go under SPUD_USER_CLAUDE_DIR and the home pointer under SPUD_CONFIG_DIR, both in the
scratch home (tests/helpers.py); git reads none of this machine's configuration, so no global excludes file hides the
local settings file from `git check-ignore`."""

import hashlib
import json
import sys
import unittest
from pathlib import Path

from helpers import EXIT_ERROR, EXIT_OK, RepoMixin, SpudTestCase, git, load_spud_module

# BadTakes' .claude/settings.local.json as it stands (design section 2.2), in a style json.dumps(indent=2) does not write.
BADTAKES_LOCAL = ('{\n    "permissions": {"allow": ["Bash(node -e \' *)"]},\n    "outputStyle": "Concise",\n'
                  '    "disabledMcpjsonServers": ["Blender", "openscad"]\n}\n')
# The spudagent source is a template project install renders for the machine it installs on (SPW-002), so the fixture
# carries the placeholder the shipped definition carries, and what install writes is `rendered()` of it, never its bytes.
AGENT = "---\nname: spudagent\ndescription: A spudagent (test fixture).\n---\nYou are a spudagent, run `python3.14 -I -S {{launcher}}`.\n"
EVENTS = {"PreToolUse": 1, "PostToolUse": 1, "SubagentStart": 1, "SubagentStop": 1, "SessionStart": 1, "Stop": 1, "UserPromptSubmit": 1}


class InstallFixture(RepoMixin):
    """A repository shaped like BadTakes registered as project badtakes beside the scratch home, and what install writes.
    Project 1, the tool, is uninstalled first (SPD-233): init installs it, and what these tests read is the first install
    of all -- the user-scope files it creates, the restart it asks for."""

    def setUp(self):
        super().setUp()
        self.cli("project", "uninstall", "spud", actor="spud")
        # SPW-004: the source is share/agents/spudagent.md under the tool beside the home; Home.agent_source gives the tool
        # its own share/ first, so writing the fixture cannot reach the repository's copy through helpers' symlink.  Every
        # test below that edits or deletes the source uses this path.
        self.source = self.home.agent_source(AGENT)
        self.other = self.make_repo("badtakes-")
        (self.other / ".claude").mkdir()
        self.local = self.other / ".claude" / "settings.local.json"
        self.local.write_text(BADTAKES_LOCAL, encoding="utf-8")
        self.add_project(self.other)
        self.user = self.home.path / ".user-claude"
        self.pointer = self.home.path / ".user-config" / "home"

    def install(self, key="badtakes", check=True):
        return self.cli("project", "install", key, actor="spud", check=check)

    def installed_events(self, key="badtakes"):
        """project.installed events for one project (init writes project 1's)."""
        return [e for e in self.home.json("events", "--kind", "project.installed")["events"] if e["data"]["project"] == key]

    def rendered(self, text=AGENT, tool=None):
        """What install writes from that source text: SPW-002's placeholder filled with the launcher of the tool checkout
        this run names (SPUD_TOOL_DIR is the scratch home itself unless a test moves it)."""
        return text.replace("{{launcher}}", str((tool or self.home.tool) / "bin" / "spud"))

    def variant(self, effort, text=AGENT):
        """What install writes as `spudagent-<effort>.md` from that source text (SPD-222), spelled out rather than rendered
        by the program: the base's frontmatter with its own name and one effort line last, the fixture having no model."""
        return self.rendered(text).replace("name: spudagent\n", "name: spudagent-%s\n" % effort, 1).replace(
            "\n---\n", "\neffort: %s\n---\n" % effort, 1)

    def settings(self):
        return json.loads(self.local.read_text(encoding="utf-8"))

    def exclude(self, repo=None):
        path = (repo or self.other) / ".git" / "info" / "exclude"
        return path.read_text(encoding="utf-8") if path.is_file() else ""


class InstallTest(InstallFixture, SpudTestCase):
    def test_install_writes_the_local_settings_keeping_every_foreign_key(self):
        out = self.cli_json("project", "install", "badtakes", actor="spud")
        self.assertTrue(out["project"]["installed"])
        self.assertTrue(out["restart"])
        data = self.settings()
        original = json.loads(BADTAKES_LOCAL)
        self.assertEqual((data["outputStyle"], data["disabledMcpjsonServers"]), (original["outputStyle"], original["disabledMcpjsonServers"]))
        self.assertNotIn("env", data)
        self.assertNotIn("deny", data["permissions"])
        self.assertEqual(data["permissions"]["additionalDirectories"], [str(self.home.path)])
        home, launcher = str(self.home.path), str(self.home.launcher)
        self.assertEqual(data["permissions"]["allow"], ["Bash(node -e ' *)", "Bash(python3.14 -I -S %s *)" % launcher, "Bash(%s -I -S %s *)" % (sys.executable, launcher)])
        commands = [(event, h["command"]) for event, groups in data["hooks"].items() for g in groups for h in g["hooks"]]
        self.assertEqual(len(commands), 7)
        self.assertEqual({e: sum(1 for x, _ in commands if x == e) for e in EVENTS}, EVENTS)
        for event, command in commands:
            self.assertEqual(command, "SPUD_HOME=%s %s -I -S %s hook %s --project badtakes" % (home, sys.executable, launcher, event))
        self.assertEqual(git(self.other, "status", "--porcelain"), "")
        self.assertEqual(len(self.installed_events()), 1)
        second = self.make_repo("second-")
        self.add_project(second, "second", "SEC", "SECS")
        text = self.install("second").stdout
        self.assertIn("project second installed", text)
        self.assertNotIn("restart open sessions", text)  # the user agents directory already held spudagent

    def test_the_file_is_kept_out_of_git_and_the_exclude_line_is_added_only_when_needed(self):
        before = self.exclude()
        self.install()
        self.assertEqual(self.exclude(), (before if before.endswith("\n") or not before else before + "\n") + "# spud project badtakes\n.claude/settings.local.json\n")
        self.assertEqual(git(self.other, "check-ignore", ".claude/settings.local.json").strip(), ".claude/settings.local.json")
        ignoring = self.make_repo("ignoring-")
        (ignoring / ".gitignore").write_text(".claude/*\n!.claude/settings.json\n", encoding="utf-8")
        git(ignoring, "add", ".gitignore")
        git(ignoring, "commit", "-q", "-m", "ignore")
        excluded = self.exclude(ignoring)
        self.add_project(ignoring, "ignoring", "IGN", "IGNS")
        self.install("ignoring")
        self.assertEqual(self.exclude(ignoring), excluded)
        self.assertEqual(json.loads(self.home.scalar("SELECT installed FROM projects WHERE key = 'ignoring'"))["added_exclude"], False)

    def test_the_user_scope_files_and_the_home_pointer(self):
        self.install()
        self.assertEqual((self.user / "agents" / "spudagent.md").read_text(encoding="utf-8"), self.rendered())
        skill = (self.user / "skills" / "spud" / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\nname: spud\n"), skill)
        self.assertIn("\ndisable-model-invocation: true\n", skill)
        self.assertIn("python3.14 -I -S %s --as spud session claim" % self.home.launcher, skill)
        self.assertIn("%s/CLAUDE.md" % self.home.path, skill)
        self.assertIn("set the session title to `<KEY> - <what this session does>`", skill)  # SPD-057
        self.assertEqual(self.pointer.read_text(encoding="utf-8"), "%s\n" % self.home.path)

    def test_a_second_install_writes_nothing(self):
        self.install()
        stamps = {p: p.stat().st_mtime_ns for p in (self.local, self.user / "agents" / "spudagent.md", self.user / "skills" / "spud" / "SKILL.md", self.pointer)}
        out = self.cli_json("project", "install", "badtakes", actor="spud")
        self.assertEqual(out["written"], [])
        self.assertIn("unchanged, nothing written", self.cli("project", "install", "badtakes", actor="spud").stdout)
        self.assertEqual({p: p.stat().st_mtime_ns for p in stamps}, stamps)
        self.assertEqual(len(self.installed_events()), 1)

    def test_uninstall_gives_back_the_original_bytes(self):
        before_exclude = self.exclude()
        self.install()
        claim = "22222222-3333-4444-8555-666666666666"
        self.cli("session", "claim", actor="spud", cwd=self.other, session=claim)
        out = self.cli_json("project", "uninstall", "badtakes", actor="spud")
        self.assertEqual(out["warnings"], [])
        self.assertEqual(self.local.read_text(encoding="utf-8"), BADTAKES_LOCAL)
        self.assertEqual(self.exclude(), before_exclude if before_exclude.endswith("\n") or not before_exclude else before_exclude + "\n")
        self.assertFalse((self.user / "agents" / "spudagent.md").exists())
        self.assertFalse((self.user / "skills" / "spud").exists())
        self.assertIsNone(self.home.scalar("SELECT installed FROM projects WHERE key = 'badtakes'"))
        self.assertIsNotNone(self.home.scalar("SELECT released_at FROM sessions WHERE session_id = ?", claim))
        self.assertIn("not installed", self.cli("project", "uninstall", "badtakes", actor="spud").stdout)

    def test_uninstall_removes_a_settings_file_install_created(self):
        bare = self.make_repo("fresh-")
        self.add_project(bare, "fresh", "FRS", "FRSS")
        self.install("fresh")
        local = bare / ".claude" / "settings.local.json"
        self.assertTrue(local.is_file())
        self.cli("project", "uninstall", "fresh", actor="spud")
        self.assertFalse(local.exists())

    def test_what_eric_added_after_install_survives_uninstall(self):
        self.install()
        data = self.settings()
        data["permissions"]["allow"].append("Bash(npm test)")
        self.local.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.cli("project", "uninstall", "badtakes", actor="spud")
        left = self.settings()
        self.assertEqual(left["permissions"]["allow"], ["Bash(node -e ' *)", "Bash(npm test)"])
        self.assertNotIn("hooks", left)
        self.assertNotIn("additionalDirectories", left["permissions"])

    def test_user_files_stay_while_another_project_is_installed_and_a_changed_one_stays_with_a_warning(self):
        second = self.make_repo("second-")
        self.add_project(second, "second", "SEC", "SECS")
        self.install()
        self.install("second")
        agent = self.user / "agents" / "spudagent.md"
        self.cli("project", "uninstall", "badtakes", actor="spud")
        self.assertTrue(agent.is_file())
        agent.write_text(self.rendered() + "Eric's own line.\n", encoding="utf-8")
        out = self.cli_json("project", "uninstall", "second", actor="spud")
        self.assertTrue(agent.is_file())
        self.assertFalse((self.user / "skills" / "spud" / "SKILL.md").exists())
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("left in place", out["warnings"][0])

    def test_install_refuses_a_tracked_settings_file_and_a_missing_source(self):
        tracked = self.make_repo("tracked-")
        (tracked / ".claude").mkdir()
        (tracked / ".claude" / "settings.local.json").write_text("{}\n", encoding="utf-8")
        git(tracked, "add", "-f", ".claude/settings.local.json")
        git(tracked, "commit", "-q", "-m", "tracked")
        self.add_project(tracked, "tracked", "TRK", "TRKS")
        proc = self.install("tracked", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("is tracked", proc.stderr)
        self.source.unlink()
        proc = self.install(check=False)
        self.assertIn("spudagent definition is the source", proc.stderr)

    def test_the_homes_settings_sync_is_unchanged(self):
        self.install()
        out = self.home.json("settings", "sync", "--path", self.home.path / "s.json")
        commands = [h["command"] for groups in out["settings"]["hooks"].values() for g in groups for h in g["hooks"]]
        self.assertEqual(len(commands), 7)
        self.assertTrue(all(not c.endswith("--project badtakes") and "--project" not in c for c in commands), commands)
        self.assertEqual(out["settings"]["permissions"]["deny"], ["Agent(isolation:*)", "Agent(model:inherit)"])
        self.assertIn("env", out["settings"])

    def test_sync_follows_the_homes_agent_and_doctor_checks_the_installation(self):
        self.install()
        self.assertEqual(self.cli_json("doctor")["problems"], [])
        projects = self.cli_json("doctor")["projects"]
        self.assertEqual([(p["key"], p["checks"]) for p in projects],
                         [("spud", ["main checkout", "not installed"]), ("badtakes", ["main checkout", "hooks", "ignored", "agent", "skill"])])
        self.source.write_text(AGENT + "A new rule.\n", encoding="utf-8")
        proc = self.cli("--json", "doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("project sync --all", proc.stdout)
        out = self.cli_json("project", "sync", "--all", actor="spud")
        # SPD-222: the base and its five effort variants, each rendered from the one source
        self.assertEqual([(r["project"], len(r["written"])) for r in out["projects"]], [("badtakes", 6)])
        self.assertEqual((self.user / "agents" / "spudagent.md").read_text(encoding="utf-8"), self.rendered(AGENT + "A new rule.\n"))
        self.assertEqual((self.user / "agents" / "spudagent-max.md").read_text(encoding="utf-8"), self.variant("max", AGENT + "A new rule.\n"))
        self.assertEqual(self.cli("doctor").returncode, EXIT_OK)
        (self.user / "skills" / "spud" / "SKILL.md").unlink()
        proc = self.cli("--json", "doctor", check=False)
        self.assertIn("no /spud skill", proc.stdout)
        self.assertEqual(self.cli("project", "sync", "nope", actor="spud", check=False).returncode, EXIT_ERROR)
        self.assertEqual(self.cli("project", "sync", actor="spud", check=False).returncode, 2)

    def test_doctor_says_whether_the_session_it_runs_in_loaded_this_homes_hooks(self):
        """SPW-003, beside the project lines above: those check the files this home installs, and a session launched
        somewhere else reads none of them, so a perfect installation says nothing about the session doctor is in.  A
        note, never a problem -- what is wrong is where the session was launched, not anything in this home, and
        `home move` refuses on this report's problems from the very session most likely to be running it."""
        self.install()
        session = "77777777-8888-4999-8aaa-bbbbbbbbbbbb"
        nowhere = self.scratch_dir("nowhere-")

        def report(launch=None, session=session):
            env = {"CLAUDE_PROJECT_DIR": str(launch)} if launch is not None else {}
            out = self.cli_json("doctor", env=env, session=session)
            text = self.cli("doctor", env=env, session=session).stdout
            self.assertEqual(out["problems"], [])  # never a problem, in any state
            return out, text

        out, text = report(self.other)  # the installed checkout: loaded, and nothing to note
        self.assertEqual(out["hooks"]["state"], "loaded")
        self.assertEqual([n for n in out["notes"] if "ledger hook" in n], [])
        self.assertIn("hooks       this home's ledger hooks are loaded in this session, from %s" % self.local, text)

        out, text = report(nowhere)  # launched outside every project: none loaded, one note, still exit 0
        self.assertEqual((out["hooks"]["state"], out["hooks"]["launch"], out["hooks"]["events"]), ("absent", str(nowhere), []))
        self.assertEqual(self.cli("doctor", env={"CLAUDE_PROJECT_DIR": str(nowhere)}, session=session).returncode, EXIT_OK)
        self.assertIn("hooks       no ledger hook of this home is loaded in this session", text)
        self.assertIn("            relaunch the session in %s" % self.home.path, text)
        note = next(n for n in out["notes"] if "ledger hook" in n)
        self.assertEqual(out["hooks"]["lines"][0], note)  # the note is the state; the fix is the second line, and the text's
        self.assertIn("no ledger hook of this home is loaded in this session", note)
        self.assertIn("note        no ledger hook of this home is loaded", text)

        out, text = report()  # no CLAUDE_PROJECT_DIR: unknown, and no note, because nothing is proven wrong
        self.assertEqual(out["hooks"]["state"], "unknown")
        self.assertEqual([n for n in out["notes"] if "ledger hook" in n], [])
        self.assertIn("hooks       whether this home's ledger hooks are loaded in this session is unknown", text)

        out, text = report(nowhere, session=None)  # no session: nothing loads hooks, and nothing is wrong
        self.assertEqual(out["hooks"]["state"], "no_session")
        self.assertEqual([n for n in out["notes"] if "ledger hook" in n], [])
        self.assertIn("hooks       there is no Claude Code session here", text)

    def test_install_renders_the_launcher_into_the_installed_definition(self):
        """SPW-002: the repository ships a template, so the installed definition names the launcher that actually runs here
        and the source keeps its placeholder -- no machine's absolute path is shipped."""
        self.install()
        agent = (self.user / "agents" / "spudagent.md").read_text(encoding="utf-8")
        self.assertIn("python3.14 -I -S %s" % self.home.launcher, agent)
        self.assertNotIn("{{launcher}}", agent)
        self.assertEqual(agent, self.rendered())
        self.assertIn("{{launcher}}", self.source.read_text(encoding="utf-8"))
        self.assertEqual(self.cli_json("doctor")["problems"], [])

    def test_doctor_reads_the_installed_definition_against_what_this_home_renders(self):
        """SPW-002: doctor compares the installed copy with the definition install renders now, not with the source's bytes:
        a hand-edited copy is a problem `project sync` settles, an untouched one is not, a missing copy says so, and with
        the source gone it names the source."""
        self.install()
        agent = self.user / "agents" / "spudagent.md"
        self.assertEqual(self.cli_json("doctor")["problems"], [])
        agent.write_text(self.rendered() + "Eric's own line.\n", encoding="utf-8")
        proc = self.cli("--json", "doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("is not the spudagent definition this home installs from", proc.stdout)
        self.assertIn("project sync --all", proc.stdout)
        self.cli_json("project", "sync", "--all", actor="spud")
        self.assertEqual(agent.read_text(encoding="utf-8"), self.rendered())
        self.assertEqual(self.cli_json("doctor")["problems"], [])
        agent.unlink()
        self.assertIn("no spudagent definition at %s" % agent, self.cli("--json", "doctor", check=False).stdout)
        self.source.unlink()
        self.assertIn("spudagent definition is the source", self.cli("--json", "doctor", check=False).stdout)

    def test_doctor_reports_a_definition_at_project_scope_and_says_which_one_a_session_there_reads(self):
        """SPW-004: Claude Code reads `<checkout>/.claude/agents/spudagent.md` in preference to the copy install writes at
        user scope, and nothing among the installed files shows it -- which is how the tool repository's own template, an
        unrendered {{launcher}} and all, was the definition every spudagent working a ticket in that checkout read.

        A note, never a problem, and doctor stays green: the file belongs to that repository, and doctor's problems are
        what `home init` and `home move` refuse on.  The report answers the question either way, so `--json` says `no`
        as plainly as the note says `yes`."""
        self.install()
        out = self.cli_json("doctor")
        self.assertEqual(out["problems"], [])
        installed = next(p for p in out["projects"] if p["key"] == "badtakes")
        self.assertIsNone(installed["project_scope_agent"])
        self.assertEqual([n for n in out["notes"] if "project-scope" in n], [])
        own = self.other / ".claude" / "agents" / "spudagent.md"
        own.parent.mkdir(parents=True, exist_ok=True)
        own.write_text(AGENT, encoding="utf-8")
        uninstalled = self.home.tool / ".claude" / "agents" / "spudagent.md"  # project spud's, uninstalled by InstallFixture
        uninstalled.parent.mkdir(parents=True, exist_ok=True)
        uninstalled.write_text(AGENT, encoding="utf-8")
        out = self.cli_json("doctor")
        self.assertEqual(out["problems"], [])
        self.assertEqual(self.cli("doctor").returncode, EXIT_OK)
        installed = next(p for p in out["projects"] if p["key"] == "badtakes")
        self.assertEqual(installed["project_scope_agent"], str(own))
        note = next(n for n in out["notes"] if str(own) in n)
        self.assertIn("reads it and not %s" % (self.user / "agents" / "spudagent.md"), note)
        self.assertIn("prefers a project-scope agent definition", note)
        self.assertIn("note        %s" % note, self.cli("doctor").stdout)
        # Read for installed projects only: project spud is not installed here, so the definition in its checkout shadows
        # no copy this home installed, and doctor names it nowhere.
        self.assertIsNone(next(p for p in out["projects"] if p["key"] == "spud")["project_scope_agent"])
        self.assertEqual([n for n in out["notes"] if str(uninstalled) in n], [])
        own.unlink()
        self.assertEqual([n for n in self.cli_json("doctor")["notes"] if "project-scope" in n], [])

    def test_sync_rewrites_the_definition_when_the_tool_checkout_moves(self):
        """SPW-002's point: the same source under a checkout at another path renders another launcher.  doctor names the
        stale installed copy and `project sync` writes the new one -- which is what a machine other than this one gets."""
        self.install()
        moved = self.make_repo("moved-tool-")
        (moved / "share" / "agents").mkdir(parents=True)
        (moved / "share" / "agents" / "spudagent.md").write_text(AGENT, encoding="utf-8")
        env = {"SPUD_TOOL_DIR": str(moved)}
        proc = self.cli("--json", "doctor", check=False, env=env)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        # every one of the six names the launcher, so every one is stale, and doctor names them in one problem
        self.assertIn("spudagent.md, spudagent-low.md, spudagent-medium.md, spudagent-high.md, spudagent-xhigh.md, spudagent-max.md"
                      " in %s are not the spudagent definitions this home installs from" % (self.user / "agents"), proc.stdout)
        agent = self.user / "agents" / "spudagent.md"
        out = self.cli_json("project", "sync", "badtakes", actor="spud", env=env)
        self.assertIn(str(agent), out["projects"][0]["written"])
        self.assertEqual(agent.read_text(encoding="utf-8"), self.rendered(tool=moved))
        self.assertIn("python3.14 -I -S %s/bin/spud" % moved, agent.read_text(encoding="utf-8"))
        self.assertEqual(self.cli("doctor", env=env).returncode, EXIT_OK)

    def test_sync_writes_the_line_an_installation_lacks_and_doctor_names_the_gap(self):
        """An installation one event short -- a line removed by hand, or an install from before an event joined the table,
        as SPD-057's UserPromptSubmit once was: doctor names the gap and `project sync` writes the line with the project's
        key and the event's matcher, keeping every other entry.  SPD-233: the prompt hook's own upgrade retired once every
        install on this machine carried it; the gap and its repair are any event's."""
        self.install()
        data = self.settings()
        del data["hooks"]["SessionStart"]
        self.local.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        proc = self.cli("--json", "doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("%s lacks this home's ledger hooks; run `spud --as spud project sync badtakes`" % self.local, proc.stdout)
        out = self.cli_json("project", "sync", "badtakes", actor="spud")
        self.assertEqual(out["projects"][0]["written"], [str(self.local)])
        data = self.settings()
        self.assertEqual([h["command"] for g in data["hooks"]["SessionStart"] for h in g["hooks"]],
                         ["SPUD_HOME=%s %s -I -S %s hook SessionStart --project badtakes" % (self.home.path, sys.executable, self.home.launcher)])
        self.assertEqual(data["hooks"]["SessionStart"][0]["matcher"], "startup|resume|clear|compact")
        self.assertEqual(data["outputStyle"], "Concise")
        self.assertEqual(self.cli("doctor").returncode, EXIT_OK)

    def test_remove_uninstalls_first(self):
        self.install()
        self.cli("project", "remove", "badtakes", actor="spud")
        self.assertEqual(self.local.read_text(encoding="utf-8"), BADTAKES_LOCAL)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM projects WHERE key = 'badtakes'"), 0)
        e = self.home.json("events", "--kind", "project.removed")["events"][0]
        self.assertTrue(e["data"]["uninstalled"])

    def test_install_writes_one_variant_per_effort_and_uninstall_leaves_none_behind(self):
        """SPD-222: the Agent tool takes no effort and a definition's `effort:` beats the session's, so beside the base
        install writes `spudagent-<effort>.md` for each level, the definitions the spawn check holds a member planned at
        that effort to.  Uninstall with the last project takes back every one still as install wrote it; one edited by hand
        stays, with a warning, exactly as the base does."""
        out = self.cli_json("project", "install", "badtakes", actor="spud")
        agents = self.user / "agents"
        self.assertEqual(sorted(p.name for p in agents.iterdir()),
                         sorted(["spudagent.md", "spudagent-low.md", "spudagent-medium.md", "spudagent-high.md",
                                 "spudagent-xhigh.md", "spudagent-max.md"]))
        for effort in ("low", "medium", "high", "xhigh", "max"):
            variant = agents / ("spudagent-%s.md" % effort)
            self.assertIn(str(variant), out["written"])
            self.assertEqual(variant.read_text(encoding="utf-8"), self.variant(effort))
        record = json.loads(self.home.scalar("SELECT installed FROM projects WHERE key = 'badtakes'"))
        self.assertEqual(record["variant_sha256"], {"spudagent-%s" % e: hashlib.sha256(self.variant(e).encode("utf-8")).hexdigest()
                                                    for e in ("low", "medium", "high", "xhigh", "max")})
        self.assertEqual(self.cli_json("project", "install", "badtakes", actor="spud")["written"], [])
        self.cli("project", "uninstall", "badtakes", actor="spud")
        self.assertEqual(sorted(agents.iterdir()), [])
        self.install()
        (agents / "spudagent-high.md").write_text(self.variant("high") + "Eric's own line.\n", encoding="utf-8")
        out = self.cli_json("project", "uninstall", "badtakes", actor="spud")
        self.assertEqual([p.name for p in agents.iterdir()], ["spudagent-high.md"])
        self.assertEqual(out["warnings"], ["%s differs from what install wrote, so it is left in place" % (agents / "spudagent-high.md")])

    def test_doctor_checks_every_variant_and_notes_a_project_scope_one(self):
        self.install()
        agents = self.user / "agents"
        (agents / "spudagent-xhigh.md").unlink()
        proc = self.cli("--json", "doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("no spudagent definition at %s; run `spud --as spud project sync --all`" % (agents / "spudagent-xhigh.md"), proc.stdout)
        (agents / "spudagent-low.md").unlink()
        self.assertIn("no spudagent definitions spudagent-low.md, spudagent-xhigh.md in %s;" % agents,
                      self.cli("--json", "doctor", check=False).stdout)
        self.cli_json("project", "sync", "--all", actor="spud")
        self.assertEqual(self.cli_json("doctor")["problems"], [])
        (agents / "spudagent-medium.md").write_text("---\nname: spudagent-medium\n---\nsomething else\n", encoding="utf-8")
        self.assertIn("%s is not the spudagent definition this home installs from" % (agents / "spudagent-medium.md"),
                      self.cli("--json", "doctor", check=False).stdout)
        self.cli_json("project", "sync", "--all", actor="spud")
        own = self.other / ".claude" / "agents" / "spudagent-high.md"
        own.parent.mkdir(parents=True, exist_ok=True)
        own.write_text(self.variant("high"), encoding="utf-8")
        out = self.cli_json("doctor")
        self.assertEqual(out["problems"], [])
        self.assertEqual(next(p for p in out["projects"] if p["key"] == "badtakes")["project_scope_agent"], str(own))
        self.assertIn("reads it and not %s" % (agents / "spudagent-high.md"), next(n for n in out["notes"] if str(own) in n))

    def test_install_refuses_a_source_that_is_no_base_for_the_variants(self):
        """The base sets no effort, so it runs at the spawning session's level; a source that sets one would make the base
        run at it and give each variant two effort lines.  Refused before anything is written."""
        self.source.write_text(AGENT.replace("name: spudagent\n", "name: spudagent\neffort: high\n"), encoding="utf-8")
        proc = self.install(check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("is not a base spudagent definition the effort variants can be rendered from: its frontmatter sets an effort",
                      proc.stderr)
        self.assertEqual(list((self.user / "agents").glob("*")), [])  # nothing written (uninstalling project 1 left the directory)
        self.assertIsNone(self.home.scalar("SELECT installed FROM projects WHERE key = 'badtakes'"))
        self.source.write_text("You are a spudagent with no frontmatter.\n", encoding="utf-8")
        self.assertIn("it has no --- frontmatter block", self.install(check=False).stderr)
        self.source.write_text(AGENT.replace("name: spudagent\n", "name: helper\n"), encoding="utf-8")
        self.assertIn("its frontmatter has no `name: spudagent` line", self.install(check=False).stderr)

    def test_the_install_record_keeps_what_uninstall_needs(self):
        self.install()
        record = json.loads(self.home.scalar("SELECT installed FROM projects WHERE key = 'badtakes'"))
        # SPW-001: `spud init` writes the pointer itself now (step 5), so install finds it there and records False; the
        # write below is the only way the record's True arises any more -- a home whose pointer was removed by hand.
        self.assertEqual({k: record[k] for k in ("path", "created_file", "original", "added_additional_dir", "added_exclude", "wrote_pointer")},
                         {"path": str(self.local), "created_file": False, "original": BADTAKES_LOCAL, "added_additional_dir": True, "added_exclude": True, "wrote_pointer": False})
        pointer = Path(self.home.env["SPUD_CONFIG_DIR"]) / "home"
        self.assertEqual(pointer.read_text(encoding="utf-8").strip(), str(self.home.path))
        pointer.unlink()
        self.cli("project", "sync", "badtakes", actor="spud")
        self.assertTrue(json.loads(self.home.scalar("SELECT installed FROM projects WHERE key = 'badtakes'"))["wrote_pointer"])
        self.assertEqual(pointer.read_text(encoding="utf-8").strip(), str(self.home.path))
        self.assertEqual(record["agent_sha256"], hashlib.sha256(self.rendered().encode("utf-8")).hexdigest())
        shown = self.cli_json("project", "show", "badtakes")["project"]
        self.assertNotIn("original", shown["install_record"])


class QuotedPathInstallTest(InstallFixture, SpudTestCase):
    """SPD-226: install, sync and uninstall in a home whose path shlex.quote quotes, where every line install writes
    reads `... '<home>/bin/spud' hook <event> --project badtakes` and projects/sessions.HOOK_MARK is no substring of it.
    Install and sync must replace those lines rather than append a copy, and uninstall must strip them."""

    home_name = "Sp üd"
    # A hook of Eric's own that runs a quoted `bin/spud` without being a line hook_command writes: never the ledger's.
    USERS_OWN = {"hooks": [{"type": "command", "command": "'/opt/my tools/bin/spud' hook Stop"},
                           {"type": "command", "command": "echo \"'/opt/my tools/bin/spud' hook Stop\""}]}

    def assert_one_line_per_event(self):
        spud = load_spud_module()
        ctx = spud.Ctx(self.home.path, "SPUD_HOME", False, tool=self.home.tool)
        commands = {}
        for event, groups in self.settings()["hooks"].items():
            for g in groups:
                if g != self.USERS_OWN:
                    commands.setdefault(event, []).extend(h["command"] for h in g["hooks"])
        self.assertEqual(commands, {e: [spud.hook_command(ctx, e, "badtakes")] for e in EVENTS})

    def test_a_second_install_or_sync_writes_nothing_and_leaves_one_line_per_event(self):
        self.install()
        self.assertTrue(self.settings()["hooks"]["Stop"][0]["hooks"][0]["command"].endswith("'%s' hook Stop --project badtakes" % self.home.launcher))
        self.assert_one_line_per_event()
        self.assertEqual(self.cli_json("project", "install", "badtakes", actor="spud")["written"], [])
        self.assertEqual(self.cli_json("project", "sync", "badtakes", actor="spud")["projects"][0]["written"], [])
        synced = {r["project"]: r["written"] for r in self.cli_json("project", "sync", "--all", actor="spud")["projects"]}
        self.assertEqual(synced["badtakes"], [])
        self.assert_one_line_per_event()
        # a group of the user's own among them stays where it is through the next install
        data = self.settings()
        data["hooks"]["Stop"].insert(0, self.USERS_OWN)
        self.local.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(self.cli_json("project", "install", "badtakes", actor="spud")["written"], [])
        self.assertEqual(self.settings()["hooks"]["Stop"][0], self.USERS_OWN)
        self.assert_one_line_per_event()

    def test_uninstall_gives_back_the_original_bytes(self):
        self.install()
        self.cli("project", "uninstall", "badtakes", actor="spud")
        self.assertEqual(self.local.read_text(encoding="utf-8"), BADTAKES_LOCAL)

    def test_uninstall_strips_the_quoted_lines_and_keeps_the_users_own(self):
        self.install()
        data = self.settings()
        data["hooks"]["Stop"].insert(0, self.USERS_OWN)
        self.local.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.cli("project", "uninstall", "badtakes", actor="spud")
        left = self.settings()
        self.assertEqual(left["hooks"], {"Stop": [self.USERS_OWN]})
        self.assertEqual(left["permissions"], json.loads(BADTAKES_LOCAL)["permissions"])


if __name__ == "__main__":
    unittest.main()
