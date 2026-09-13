"""settings sync: the two env caps generated from spud.config.json limits, the six
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
}


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

    def test_missing_file_is_created_with_caps_hooks_and_allow_rules(self):
        path = self.home.path / "elsewhere" / "settings.json"
        out = self.home.json("settings", "sync", "--path", path)
        self.assertTrue(out["written"])
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(data.keys()), {"env", "permissions", "hooks"})
        self.assertEqual(data["env"], {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "2", "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "9"})

    def test_the_six_hooks_are_installed_with_absolute_commands(self):
        path = self.home.path / ".claude" / "settings.json"
        self.home.json("settings", "sync", "--path", path)
        data = json.loads(path.read_text(encoding="utf-8"))
        found = spud_hooks(data)
        expected = {(event, matcher) for event, matchers in EVENTS.items() for matcher in matchers}
        self.assertEqual({(e, m) for e, m, _ in found}, expected)
        self.assertEqual(len(found), 8)
        for event, matcher, h in found:
            self.assertEqual(h["type"], "command")
            self.assertEqual(h["timeout"], 30)
            self.assertTrue(h["command"].startswith("SPUD_HOME=%s %s -I -S %s/bin/spud hook %s" % (self.home.path, sys.executable, self.home.path, event)), h["command"])
        # matcher-less events carry no matcher key at all
        for group in data["hooks"]["SubagentStart"] + data["hooks"]["SubagentStop"] + data["hooks"]["Stop"]:
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
        path = self.home.path / ".claude" / "settings.json"
        out = self.home.json("settings", "sync", "--path", path)
        allow = out["settings"]["permissions"]["allow"]
        self.assertIn("Bash(python3.14 -I -S %s/bin/spud *)" % self.home.path, allow)
        self.assertIn("Bash(%s -I -S %s/bin/spud *)" % (sys.executable, self.home.path), allow)
        self.assertIn("Bash(%s/bin/spud *)" % self.home.path, allow)
        self.assertEqual(len(allow), len(set(allow)))
        self.assertEqual(out["settings"]["permissions"].get("deny", []), [])

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
        self.assertEqual(len(commands), 8)
        self.assertFalse(any("/old/" in c for c in commands))
        self.assertEqual(data["hooks"]["Notification"], stale["hooks"]["Notification"])
        foreign = [h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"] if "bin/spud hook" not in h["command"]]
        self.assertEqual(foreign, ["echo foreign", "echo also-mine"])
        self.assertEqual(data["permissions"]["deny"], ["Bash(rm -rf *)"])
        self.assertIn("Bash(date:*)", data["permissions"]["allow"])
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
        self.home.json("settings", "sync", "--path", self.home.path / "s.json")
        events = self.home.json("events", "--kind", "config.synced")["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["data"]["hooks"], 8)
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
