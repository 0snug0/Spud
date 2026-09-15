"""spud project install|uninstall|sync and doctor's projects section (SPD-014, design sections 2.1 to 2.4 and 2.7).  The
other repository holds a local settings file shaped like BadTakes' and written in its own style, so that uninstall must give
back its exact bytes.  User-scope files go under SPUD_USER_CLAUDE_DIR and the home pointer under SPUD_CONFIG_DIR, both in the
scratch home (tests/helpers.py); git reads none of this machine's configuration, so no global excludes file hides the
local settings file from `git check-ignore`."""

import hashlib
import json
import sys
import unittest

from helpers import EXIT_ERROR, EXIT_OK, RepoMixin, SpudTestCase, git

# BadTakes' .claude/settings.local.json as it stands (design section 2.2), in a style json.dumps(indent=2) does not write.
BADTAKES_LOCAL = ('{\n    "permissions": {"allow": ["Bash(node -e \' *)"]},\n    "outputStyle": "Concise",\n'
                  '    "disabledMcpjsonServers": ["Blender", "openscad"]\n}\n')
AGENT = "---\nname: spudagent\ndescription: A spudagent (test fixture).\n---\nYou are a spudagent.\n"
EVENTS = {"PreToolUse": 3, "PostToolUse": 1, "SubagentStart": 1, "SubagentStop": 1, "SessionStart": 1, "Stop": 1, "UserPromptSubmit": 1}


class InstallTest(RepoMixin, SpudTestCase):
    def setUp(self):
        super().setUp()
        agents = self.home.path / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "spudagent.md").write_text(AGENT, encoding="utf-8")
        self.other = self.make_repo("badtakes-")
        (self.other / ".claude").mkdir()
        self.local = self.other / ".claude" / "settings.local.json"
        self.local.write_text(BADTAKES_LOCAL, encoding="utf-8")
        self.add_project(self.other)
        self.user = self.home.path / ".user-claude"
        self.pointer = self.home.path / ".user-config" / "home"

    def install(self, key="badtakes", check=True):
        return self.cli("project", "install", key, actor="spud", check=check)

    def settings(self):
        return json.loads(self.local.read_text(encoding="utf-8"))

    def exclude(self, repo=None):
        path = (repo or self.other) / ".git" / "info" / "exclude"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

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
        home = str(self.home.path)
        self.assertEqual(data["permissions"]["allow"], ["Bash(node -e ' *)", "Bash(python3.14 -I -S %s/bin/spud *)" % home, "Bash(%s -I -S %s/bin/spud *)" % (sys.executable, home)])
        commands = [(event, h["command"]) for event, groups in data["hooks"].items() for g in groups for h in g["hooks"]]
        self.assertEqual(len(commands), 9)
        self.assertEqual({e: sum(1 for x, _ in commands if x == e) for e in EVENTS}, EVENTS)
        for event, command in commands:
            self.assertEqual(command, "SPUD_HOME=%s %s -I -S %s/bin/spud hook %s --project badtakes" % (home, sys.executable, home, event))
        self.assertEqual(git(self.other, "status", "--porcelain"), "")
        self.assertEqual(len(self.home.json("events", "--kind", "project.installed")["events"]), 1)
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
        self.assertEqual((self.user / "agents" / "spudagent.md").read_text(encoding="utf-8"), AGENT)
        skill = (self.user / "skills" / "spud" / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\nname: spud\n"), skill)
        self.assertIn("\ndisable-model-invocation: true\n", skill)
        self.assertIn("python3.14 -I -S %s/bin/spud --as spud session claim" % self.home.path, skill)
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
        self.assertEqual(len(self.home.json("events", "--kind", "project.installed")["events"]), 1)

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
        agent.write_text(AGENT + "Eric's own line.\n", encoding="utf-8")
        out = self.cli_json("project", "uninstall", "second", actor="spud")
        self.assertTrue(agent.is_file())
        self.assertFalse((self.user / "skills" / "spud" / "SKILL.md").exists())
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("left in place", out["warnings"][0])

    def test_install_refuses_the_home_an_archived_project_and_a_tracked_settings_file(self):
        proc = self.install("spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("settings sync", proc.stderr)
        tracked = self.make_repo("tracked-")
        (tracked / ".claude").mkdir()
        (tracked / ".claude" / "settings.local.json").write_text("{}\n", encoding="utf-8")
        git(tracked, "add", "-f", ".claude/settings.local.json")
        git(tracked, "commit", "-q", "-m", "tracked")
        self.add_project(tracked, "tracked", "TRK", "TRKS")
        proc = self.install("tracked", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("is tracked", proc.stderr)
        (self.home.path / ".claude" / "agents" / "spudagent.md").unlink()
        proc = self.install(check=False)
        self.assertIn("spudagent definition is the source", proc.stderr)

    def test_the_homes_settings_sync_is_unchanged(self):
        self.install()
        out = self.home.json("settings", "sync", "--path", self.home.path / "s.json")
        commands = [h["command"] for groups in out["settings"]["hooks"].values() for g in groups for h in g["hooks"]]
        self.assertEqual(len(commands), 9)
        self.assertTrue(all(not c.endswith("--project badtakes") and "--project" not in c for c in commands), commands)
        self.assertEqual(out["settings"]["permissions"]["deny"], ["Agent(isolation:*)", "Agent(model:inherit)"])
        self.assertIn("env", out["settings"])

    def test_sync_follows_the_homes_agent_and_doctor_checks_the_installation(self):
        self.install()
        self.assertEqual(self.cli_json("doctor")["problems"], [])
        projects = self.cli_json("doctor")["projects"]
        self.assertEqual([(p["key"], p["checks"]) for p in projects], [("badtakes", ["main checkout", "hooks", "ignored", "agent", "skill"])])
        (self.home.path / ".claude" / "agents" / "spudagent.md").write_text(AGENT + "A new rule.\n", encoding="utf-8")
        proc = self.cli("--json", "doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("project sync --all", proc.stdout)
        out = self.cli_json("project", "sync", "--all", actor="spud")
        self.assertEqual([(r["project"], len(r["written"])) for r in out["projects"]], [("badtakes", 1)])
        self.assertEqual((self.user / "agents" / "spudagent.md").read_text(encoding="utf-8"), AGENT + "A new rule.\n")
        self.assertEqual(self.cli("doctor").returncode, EXIT_OK)
        (self.user / "skills" / "spud" / "SKILL.md").unlink()
        proc = self.cli("--json", "doctor", check=False)
        self.assertIn("no /spud skill", proc.stdout)
        self.assertEqual(self.cli("project", "sync", "nope", actor="spud", check=False).returncode, EXIT_ERROR)
        self.assertEqual(self.cli("project", "sync", actor="spud", check=False).returncode, 2)

    def test_sync_writes_the_prompt_hook_into_an_installation_made_before_it(self):
        """SPD-057: an installation from before the UserPromptSubmit hook lacks its line; doctor names the gap and `project sync`
        writes the line with the project's key, keeping every other entry."""
        self.install()
        data = self.settings()
        del data["hooks"]["UserPromptSubmit"]
        self.local.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        proc = self.cli("--json", "doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("lacks this home's ledger hooks", proc.stdout)
        out = self.cli_json("project", "sync", "badtakes", actor="spud")
        self.assertEqual(out["projects"][0]["written"], [str(self.local)])
        data = self.settings()
        self.assertEqual([h["command"] for g in data["hooks"]["UserPromptSubmit"] for h in g["hooks"]],
                         ["SPUD_HOME=%s %s -I -S %s/bin/spud hook UserPromptSubmit --project badtakes" % (self.home.path, sys.executable, self.home.path)])
        self.assertNotIn("matcher", data["hooks"]["UserPromptSubmit"][0])
        self.assertEqual(data["outputStyle"], "Concise")
        self.assertEqual(self.cli("doctor").returncode, EXIT_OK)

    def test_remove_uninstalls_first(self):
        self.install()
        self.cli("project", "remove", "badtakes", actor="spud")
        self.assertEqual(self.local.read_text(encoding="utf-8"), BADTAKES_LOCAL)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM projects WHERE key = 'badtakes'"), 0)
        e = self.home.json("events", "--kind", "project.removed")["events"][0]
        self.assertTrue(e["data"]["uninstalled"])

    def test_the_install_record_keeps_what_uninstall_needs(self):
        self.install()
        record = json.loads(self.home.scalar("SELECT installed FROM projects WHERE key = 'badtakes'"))
        self.assertEqual({k: record[k] for k in ("path", "created_file", "original", "added_additional_dir", "added_exclude", "wrote_pointer")},
                         {"path": str(self.local), "created_file": False, "original": BADTAKES_LOCAL, "added_additional_dir": True, "added_exclude": True, "wrote_pointer": True})
        self.assertEqual(record["agent_sha256"], hashlib.sha256(AGENT.encode("utf-8")).hexdigest())
        shown = self.cli_json("project", "show", "badtakes")["project"]
        self.assertNotIn("original", shown["install_record"])


if __name__ == "__main__":
    unittest.main()
