"""settings sync: the two env caps generated from spud.config.json limits, the seven
ledger hooks and the CLI allow rules, written into a settings file with every
other key preserved. Always against a temp file."""

import json
import sys
import unittest

from helpers import SpudTestCase

FIXTURE = {
    "model": "claude-fable-5-1",
    "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1", "OTHER": "kept"},
    "permissions": {"allow": ["Bash(date:*)", "Bash(ls:*)"]},
    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]},
}

EVENTS = {
    "PreToolUse": ["Agent", "Bash", "Write|Edit|MultiEdit|NotebookEdit"],
    "PostToolUse": ["Agent"],
    "SubagentStart": [None],
    "SubagentStop": [None],
    "SessionStart": ["startup|resume|clear|compact"],
    "Stop": [None],
    "UserPromptSubmit": [None],  # SPD-057
}


def prescribed_allow_rules(home):
    """The allow rules settings sync writes for a home since SPD-038: the prescribed call, `python3.14 -I -S <home>/bin/spud`,
    by the documented interpreter name and by the absolute interpreter.  No rule for the launcher run by its own path."""
    return ["Bash(python3.14 -I -S %s/bin/spud *)" % home, "Bash(%s -I -S %s/bin/spud *)" % (sys.executable, home)]


def spud_hooks(data):
    """[(event, matcher, hook)] for every hook entry whose command runs `bin/spud hook`."""
    out = []
    for event, groups in data.get("hooks", {}).items():
        for group in groups:
            for h in group.get("hooks", []):
                if "bin/spud hook" in h.get("command", ""):
                    out.append((event, group.get("matcher"), h))
    return out


class SettingsSyncTest(SpudTestCase):
    def test_sync_writes_caps_and_preserves_everything_else(self):
        path = self.home.write_settings(FIXTURE)
        out = self.home.json("settings", "sync", "--path", path)
        self.assertTrue(out["ok"])
        self.assertTrue(out["written"])
        self.assertEqual(out["path"], str(path))
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(list(data.keys()), ["model", "env", "permissions", "hooks"])
        self.assertEqual(data["env"]["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"], "2")
        self.assertEqual(data["env"]["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"], "9")
        self.assertEqual(data["env"]["OTHER"], "kept")
        self.assertEqual(data["permissions"]["allow"][:2], FIXTURE["permissions"]["allow"])
        self.assertEqual(data["hooks"]["PreToolUse"][0], FIXTURE["hooks"]["PreToolUse"][0])
        self.assertEqual(data["model"], "claude-fable-5-1")
        self.assertTrue(path.read_text(encoding="utf-8").endswith("}\n"))

    def test_dry_run_changes_nothing(self):
        path = self.home.write_settings(FIXTURE)
        before = path.read_text(encoding="utf-8")
        out = self.home.json("settings", "sync", "--path", path, "--dry-run")
        self.assertFalse(out["written"])
        self.assertEqual(out["settings"]["env"]["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"], "9")
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        text = self.home.run("settings", "sync", "--path", path, "--dry-run").stdout
        self.assertIn("CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH", text)
        self.assertIn("bin/spud hook", text)
        self.assertIn('"Agent(isolation:*)"', text)
        self.assertIn('"Agent(model:inherit)"', text)
        self.assertEqual(out["settings"]["permissions"]["deny"], ["Agent(isolation:*)", "Agent(model:inherit)"])

    def test_missing_file_is_created_with_caps_hooks_and_allow_rules(self):
        path = self.home.path / "elsewhere" / "settings.json"
        out = self.home.json("settings", "sync", "--path", path)
        self.assertTrue(out["written"])
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(data.keys()), {"env", "permissions", "hooks"})
        self.assertEqual(data["env"], {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "2", "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "9"})

    def test_the_seven_hooks_are_installed_with_absolute_commands(self):
        path = self.home.path / ".claude" / "settings.json"
        self.home.json("settings", "sync", "--path", path)
        data = json.loads(path.read_text(encoding="utf-8"))
        found = spud_hooks(data)
        expected = {(event, matcher) for event, matchers in EVENTS.items() for matcher in matchers}
        self.assertEqual({(e, m) for e, m, _ in found}, expected)
        self.assertEqual(len(found), 9)
        for event, matcher, h in found:
            self.assertEqual(h["type"], "command")
            self.assertEqual(h["timeout"], 30)
            self.assertTrue(h["command"].startswith("SPUD_HOME=%s %s -I -S %s/bin/spud hook %s" % (self.home.path, sys.executable, self.home.path, event)), h["command"])
        # matcher-less events carry no matcher key at all
        for group in data["hooks"]["SubagentStart"] + data["hooks"]["SubagentStop"] + data["hooks"]["Stop"] + data["hooks"]["UserPromptSubmit"]:
            self.assertNotIn("matcher", group)
        # each event has exactly one ledger hook group, so matching hooks never run twice
        for event in EVENTS:
            ours = [g for g in data["hooks"][event] if any("bin/spud hook" in h["command"] for h in g["hooks"])]
            self.assertEqual(len(ours), len(EVENTS[event]), event)

    def test_session_start_matches_clear_too(self):
        # SPD-011: a /clear gets the board injected as startup, resume and compact do
        out = self.home.json("settings", "sync", "--path", self.home.path / "s.json")
        ours = [g for g in out["settings"]["hooks"]["SessionStart"] if any("bin/spud hook" in h["command"] for h in g["hooks"])]
        self.assertEqual([g["matcher"] for g in ours], ["startup|resume|clear|compact"])

    def test_allow_rules_for_the_cli(self):
        # SPD-038: exactly the two prescribed `-I -S` spellings.  The launcher run by its own path goes through its #! line,
        # an interpreter with neither -I nor -S, so PYTHONPATH and user-site .pth files load code first: no rule, a prompt.
        path = self.home.path / ".claude" / "settings.json"
        out = self.home.json("settings", "sync", "--path", path)
        allow = out["settings"]["permissions"]["allow"]
        self.assertEqual(allow, prescribed_allow_rules(self.home.path))
        self.assertNotIn("Bash(%s/bin/spud *)" % self.home.path, allow)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["permissions"]["allow"], allow)

    def test_sync_removes_the_direct_launcher_rule_and_keeps_unrelated_rules(self):
        # SPD-038: a settings file an older sync wrote holds `Bash(<home>/bin/spud *)`.  The rule of the ledger's own shape
        # (ALLOW_RULE_MARK, as for a stale home) is dropped and not written back; every other rule keeps its place.
        home = self.home.path
        unrelated = ["Bash(date:*)", "Bash(git status *)", "Read(./notes/**)", "Bash(%s/bin/spud_ledger.py *)" % home, "Bash(cat %s/bin/spud)" % home]
        old = ["Bash(python3.14 -I -S %s/bin/spud *)" % home, "Bash(%s -I -S %s/bin/spud *)" % (sys.executable, home), "Bash(%s/bin/spud *)" % home]
        legacy = "Bash(%s/bin/spud:*)" % home
        path = self.home.write_settings({"permissions": {"allow": unrelated[:2] + old + [legacy] + unrelated[2:], "deny": ["Bash(rm -rf *)"]}})
        out = self.home.json("settings", "sync", "--path", path)
        self.assertTrue(out["written"])
        allow = json.loads(path.read_text(encoding="utf-8"))["permissions"]["allow"]
        self.assertEqual(allow, unrelated + prescribed_allow_rules(home))
        self.assertFalse(any(a in allow for a in ("Bash(%s/bin/spud *)" % home, legacy)), allow)
        again = self.home.json("settings", "sync", "--path", path)
        self.assertFalse(again["written"])

    def test_a_ledger_shaped_rule_for_another_home_is_replaced_like_a_stale_one(self):
        # What the merge does with another home's rules (SPD-038 brief item 2): every `Bash(... bin/spud *)` rule is the
        # ledger's, whatever home it names, so the direct and prescribed rules of a moved or other home both go, as the
        # /old case below pins; rules of any other shape stay.
        path = self.home.write_settings({"permissions": {"allow": ["Bash(/other/bin/spud *)", "Bash(python3.14 -I -S /other/bin/spud *)", "Bash(/other/bin/tool *)"]}})
        out = self.home.json("settings", "sync", "--path", path)
        self.assertEqual(out["settings"]["permissions"]["allow"], ["Bash(/other/bin/tool *)"] + prescribed_allow_rules(self.home.path))

    def test_deny_rules_for_the_agent_parameters_law_3_forbids(self):
        # SPD-016: the permission system itself refuses these spawns, even when the PreToolUse(Agent) hook is removed or
        # does not run.  The syntax is the documented parameter rule, Tool(param:value), deny and ask rules only.
        out = self.home.json("settings", "sync", "--path", self.home.path / ".claude" / "settings.json")
        self.assertEqual(out["settings"]["permissions"]["deny"], ["Agent(isolation:*)", "Agent(model:inherit)"])

    def test_deny_rules_merge_like_the_allow_rules(self):
        path = self.home.write_settings({"permissions": {"deny": ["Bash(rm -rf *)", "Agent(model:inherit)", 7, {"x": 1}]}})
        first = self.home.json("settings", "sync", "--path", path)
        self.assertEqual(first["settings"]["permissions"]["deny"], ["Bash(rm -rf *)", "Agent(model:inherit)", "Agent(isolation:*)"])
        second = self.home.json("settings", "sync", "--path", path)
        self.assertFalse(second["written"])
        self.assertEqual(second["settings"]["permissions"]["deny"], first["settings"]["permissions"]["deny"])
        path = self.home.write_settings({"permissions": {"deny": "notalist", "ask": ["Bash(curl *)"]}})
        out = self.home.json("settings", "sync", "--path", path)
        self.assertEqual(out["settings"]["permissions"]["deny"], ["Agent(isolation:*)", "Agent(model:inherit)"])
        self.assertEqual(out["settings"]["permissions"]["ask"], ["Bash(curl *)"])

    def test_merge_keeps_foreign_hooks_and_replaces_stale_ledger_hooks(self):
        stale = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo foreign"}]},
                    {"matcher": "Agent", "hooks": [{"type": "command", "command": "SPUD_HOME=/old /old/python -I -S /old/bin/spud hook PreToolUse", "timeout": 5}]},
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo also-mine"}, {"type": "command", "command": "python /old/bin/spud hook PreToolUse"}]},
                ],
                "Stop": [{"hooks": [{"type": "command", "command": "/old/bin/spud hook Stop"}]}],
                "Notification": [{"hooks": [{"type": "command", "command": "say hi"}]}],
            },
            "permissions": {"allow": ["Bash(date:*)", "Bash(/old/bin/spud *)", "Bash(python3.14 -I -S /old/bin/spud *)"], "deny": ["Bash(rm -rf *)"]},
        }
        path = self.home.write_settings(stale)
        out = self.home.json("settings", "sync", "--path", path)
        data = out["settings"]
        commands = [h["command"] for _, _, h in spud_hooks(data)]
        self.assertEqual(len(commands), 9)
        self.assertFalse(any("/old/" in c for c in commands))
        self.assertEqual(data["hooks"]["Notification"], stale["hooks"]["Notification"])
        foreign = [h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"] if "bin/spud hook" not in h["command"]]
        self.assertEqual(foreign, ["echo foreign", "echo also-mine"])
        self.assertEqual(data["permissions"]["deny"], ["Bash(rm -rf *)", "Agent(isolation:*)", "Agent(model:inherit)"])
        self.assertEqual(data["permissions"]["allow"], ["Bash(date:*)"] + prescribed_allow_rules(self.home.path))
        self.assertFalse(any("/old/" in a for a in data["permissions"]["allow"]))

    def test_malformed_input_is_coerced_not_copied(self):
        """Rooster's LOW-6: a string where a list belongs, or non-string entries, must not become junk."""
        path = self.home.write_settings({"hooks": {"PreToolUse": "notalist", "Stop": [{"hooks": "nope"}, "junk", {"hooks": [{"type": "command", "command": 7}, {"type": "command", "command": "echo ok"}]}]}, "permissions": {"allow": [42, {"x": 1}, "Bash(date:*)"]}})
        out = self.home.json("settings", "sync", "--path", path)
        data = out["settings"]
        self.assertTrue(all(isinstance(g, dict) and isinstance(g.get("hooks"), list) for groups in data["hooks"].values() for g in groups))
        self.assertTrue(all(isinstance(h, dict) and isinstance(h.get("command"), str) for groups in data["hooks"].values() for g in groups for h in g["hooks"]))
        self.assertEqual([h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"] if "bin/spud hook" not in h["command"]], ["echo ok"])
        self.assertEqual(len([g for g in data["hooks"]["PreToolUse"]]), 3)
        self.assertEqual([a for a in data["permissions"]["allow"] if not a.startswith("Bash(") or "bin/spud" not in a], ["Bash(date:*)"])

    def test_idempotent(self):
        path = self.home.write_settings(FIXTURE)
        first = self.home.json("settings", "sync", "--path", path)
        self.assertTrue(first["written"])
        text = path.read_text(encoding="utf-8")
        second = self.home.json("settings", "sync", "--path", path)
        self.assertFalse(second["written"])
        self.assertEqual(path.read_text(encoding="utf-8"), text)
        self.assertEqual(first["settings"], second["settings"])
        self.assertIn("unchanged", self.home.run("settings", "sync", "--path", path).stdout)

    def test_config_synced_event(self):
        path = self.home.path / "s.json"
        self.home.json("settings", "sync", "--path", path)
        # SPW-001 phase 4: init syncs <home>/.claude/settings.json itself, so the log holds that event too; this is the
        # one for the path the test named.
        events = [e for e in self.home.json("events", "--kind", "config.synced")["events"] if e["data"]["path"] == str(path)]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["data"]["hooks"], 9)
        self.assertEqual(events[0]["data"]["deny"], ["Agent(isolation:*)", "Agent(model:inherit)"])
        self.assertIn("path", events[0]["data"])

    def test_default_path_is_under_spud_home(self):
        out = self.home.json("settings", "sync")
        self.assertEqual(out["path"], str(self.home.path / ".claude" / "settings.json"))
        self.assertTrue((self.home.path / ".claude" / "settings.json").exists())

    def test_up_to_date_file_is_not_rewritten(self):
        path = self.home.write_settings(FIXTURE)
        self.home.json("settings", "sync", "--path", path)
        mtime = path.stat().st_mtime_ns
        out = self.home.json("settings", "sync", "--path", path)
        self.assertFalse(out["written"])
        self.assertEqual(path.stat().st_mtime_ns, mtime)


if __name__ == "__main__":
    unittest.main()
