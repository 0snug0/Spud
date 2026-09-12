"""settings sync: the two env caps generated from spud.config.json limits into a
settings file, every other key preserved. Always against a temp file."""

import json
import unittest

from helpers import SpudTestCase

FIXTURE = {
    "model": "claude-fable-5-1",
    "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1", "OTHER": "kept"},
    "permissions": {"allow": ["Bash(date:*)", "Bash(ls:*)"]},
    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]},
}


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
        self.assertEqual(data["permissions"], FIXTURE["permissions"])
        self.assertEqual(data["hooks"], FIXTURE["hooks"])
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

    def test_missing_file_is_created_with_only_the_caps(self):
        path = self.home.path / "elsewhere" / "settings.json"
        out = self.home.json("settings", "sync", "--path", path)
        self.assertTrue(out["written"])
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data, {"env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "2", "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "9"}})

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
