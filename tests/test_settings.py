"""settings sync: the two env caps generated from spud.config.json limits, the seven
ledger hooks and the CLI allow rules, written into a settings file with every
other key preserved. Always against a temp file.

HookEvidenceTest is SPW-003: the one reading of a settings file, which `settings sync`
writes, doctor asks per project and `session show` asks of the files its own session
loads; HookTableTest pins the two hand-rolled pieces that reading rests on against the
standard library and against the table sync installs, and needs no home.

SPD-233 retired OlderInstallTest (SPD-223's three PreToolUse rows read as installed and
rewritten as one, in a plain and a quoted home) and the direct launcher allow rule's own
test (SPD-038): on 2026-09-24 `spud doctor` on this machine, and the settings files it
reads, showed the home and both installed projects past both shapes.  What they reached beside the upgrade is still held:
foreign rows kept through a merge (test_merge_keeps_foreign_hooks_and_replaces_stale_ledger_hooks),
the quoted home's lines replaced in place (QuotedPathHomeTest, test_install.QuotedPathInstallTest),
and every rule of the ledger's shape dropped while its near misses stay
(test_a_ledger_shaped_rule_for_another_home_is_replaced_like_a_stale_one).

DoctorHomeSettingsTest is SPW-006: the same reading turned on the home's own
.claude/settings.json, which is the file this command writes by default and the whole
of what makes a session launched in the home Spud -- and which no check of doctor's
read until then, so a home that had never been synced passed a green doctor."""

import json
import shlex
import sys
import unittest

from helpers import EXIT_ERROR, EXIT_OK, SpudTestCase, load_spud_module

FIXTURE = {
    "model": "claude-fable-5-1",
    "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1", "OTHER": "kept"},
    "permissions": {"allow": ["Bash(date:*)", "Bash(ls:*)"]},
    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]},
}

EVENTS = {
    "PreToolUse": ["Agent|Bash|Write|Edit|MultiEdit|NotebookEdit"],  # SPD-223: one row, where every sync before it wrote three
    "PostToolUse": ["Agent"],
    "SubagentStart": [None],
    "SubagentStop": [None],
    "SessionStart": ["startup|resume|clear|compact"],
    "Stop": [None],
    "UserPromptSubmit": [None],  # SPD-057
}


def prescribed_allow_rules(tool):
    """The allow rules settings sync writes for a home since SPD-038: the prescribed call, `python3.14 -I -S <tool>/bin/spud`,
    by the documented interpreter name and by the absolute interpreter.  No rule for the launcher run by its own path."""
    return ["Bash(python3.14 -I -S %s/bin/spud *)" % tool, "Bash(%s -I -S %s/bin/spud *)" % (sys.executable, tool)]


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
        self.assertEqual(len(found), 7)
        for event, matcher, h in found:
            self.assertEqual(h["type"], "command")
            self.assertEqual(h["timeout"], 30)
            self.assertTrue(h["command"].startswith("SPUD_HOME=%s %s -I -S %s hook %s" % (self.home.path, sys.executable, self.home.launcher, event)), h["command"])
        # matcher-less events carry no matcher key at all
        for group in data["hooks"]["SubagentStart"] + data["hooks"]["SubagentStop"] + data["hooks"]["Stop"] + data["hooks"]["UserPromptSubmit"]:
            self.assertNotIn("matcher", group)
        # each event has exactly one ledger hook group, so matching hooks never run twice
        for event in EVENTS:
            ours = [g for g in data["hooks"][event] if any("bin/spud hook" in h["command"] for h in g["hooks"])]
            self.assertEqual(len(ours), 1, event)

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
        self.assertEqual(allow, prescribed_allow_rules(self.home.tool))
        self.assertNotIn("Bash(%s *)" % self.home.launcher, allow)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["permissions"]["allow"], allow)

    def test_a_ledger_shaped_rule_for_another_home_is_replaced_like_a_stale_one(self):
        # What the merge does with another home's rules (SPD-038 brief item 2): every `Bash(... bin/spud *)` rule is the
        # ledger's (ALLOW_RULE_MARK), whatever home it names, so the direct, the colon-form and the prescribed rules of a
        # moved or other home -- or of this one, rewritten -- all go, as the /old case below pins, and the prescribed pair
        # is written after the rest.  Rules of any other shape keep their places, the near misses too: a rule on
        # bin/spud_ledger.py, and one whose command is not the launcher's.
        tool = self.home.tool
        unrelated = ["Bash(date:*)", "Bash(git status *)", "Read(./notes/**)", "Bash(/other/bin/tool *)",
                     "Bash(%s/bin/spud_ledger.py *)" % tool, "Bash(cat %s/bin/spud)" % tool]
        ledgers = ["Bash(/other/bin/spud *)", "Bash(python3.14 -I -S /other/bin/spud *)", "Bash(/other/bin/spud:*)",
                   "Bash(%s/bin/spud *)" % tool] + prescribed_allow_rules(tool)
        path = self.home.write_settings({"permissions": {"allow": unrelated[:2] + ledgers + unrelated[2:], "deny": ["Bash(rm -rf *)"]}})
        out = self.home.json("settings", "sync", "--path", path)
        self.assertTrue(out["written"])
        self.assertEqual(out["settings"]["permissions"]["allow"], unrelated + prescribed_allow_rules(tool))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["permissions"]["allow"], unrelated + prescribed_allow_rules(tool))
        self.assertFalse(self.home.json("settings", "sync", "--path", path)["written"])

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
        self.assertEqual(len(commands), 7)
        self.assertFalse(any("/old/" in c for c in commands))
        self.assertEqual(data["hooks"]["Notification"], stale["hooks"]["Notification"])
        foreign = [h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"] if "bin/spud hook" not in h["command"]]
        self.assertEqual(foreign, ["echo foreign", "echo also-mine"])
        self.assertEqual(data["permissions"]["deny"], ["Bash(rm -rf *)", "Agent(isolation:*)", "Agent(model:inherit)"])
        self.assertEqual(data["permissions"]["allow"], ["Bash(date:*)"] + prescribed_allow_rules(self.home.tool))
        self.assertFalse(any("/old/" in a for a in data["permissions"]["allow"]))

    def test_malformed_input_is_coerced_not_copied(self):
        """Rooster's LOW-6: a string where a list belongs, or non-string entries, must not become junk."""
        path = self.home.write_settings({"hooks": {"PreToolUse": "notalist", "Stop": [{"hooks": "nope"}, "junk", {"hooks": [{"type": "command", "command": 7}, {"type": "command", "command": "echo ok"}]}]}, "permissions": {"allow": [42, {"x": 1}, "Bash(date:*)"]}})
        out = self.home.json("settings", "sync", "--path", path)
        data = out["settings"]
        self.assertTrue(all(isinstance(g, dict) and isinstance(g.get("hooks"), list) for groups in data["hooks"].values() for g in groups))
        self.assertTrue(all(isinstance(h, dict) and isinstance(h.get("command"), str) for groups in data["hooks"].values() for g in groups for h in g["hooks"]))
        self.assertEqual([h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"] if "bin/spud hook" not in h["command"]], ["echo ok"])
        self.assertEqual(len([g for g in data["hooks"]["PreToolUse"]]), 1)
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
        self.assertEqual(events[0]["data"]["hooks"], 7)
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


class HookEvidenceTest(SpudTestCase):
    def ctx(self):
        spud = load_spud_module()
        return spud.Ctx(self.home.path, "SPUD_HOME", False, tool=self.home.tool)

    def test_the_events_a_settings_file_carries_are_read_once_for_every_reader(self):
        """SPW-003 moved the reading to projects/sessions.settings_hook_events, where `session show` can reach it;
        settings_hold_hooks is the whole-table answer over it and keeps its meaning for install, home move and doctor."""
        spud = load_spud_module()
        ctx = self.ctx()
        path = self.home.path / "elsewhere" / "settings.json"
        self.assertEqual(spud.settings_hook_events(ctx, path), set())  # absent
        self.assertFalse(spud.settings_hold_hooks(ctx, path))
        self.home.json("settings", "sync", "--path", path)
        self.assertEqual(spud.settings_hook_events(ctx, path), {e for e, _ in spud.HOOK_TABLE})
        self.assertTrue(spud.settings_hold_hooks(ctx, path))
        # a project's lines end in `--project <key>`, and only that key's reader may count them
        project = self.home.path / "p" / "settings.local.json"
        project.parent.mkdir(parents=True)
        data = json.loads(path.read_text(encoding="utf-8"))
        for groups in data["hooks"].values():
            for g in groups:
                for h in g["hooks"]:
                    if "bin/spud hook" in h["command"]:
                        h["command"] += " --project badtakes"
        project.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(spud.settings_hook_events(ctx, project, "badtakes"), {e for e, _ in spud.HOOK_TABLE})
        self.assertEqual(spud.settings_hook_events(ctx, project, "elsewhere"), set())
        self.assertTrue(spud.settings_hold_hooks(ctx, project, "badtakes"))
        self.assertFalse(spud.settings_hold_hooks(ctx, project, "elsewhere"))
        # junk is not evidence: a file that is not JSON, not an object, or not the shape sync writes carries nothing
        for junk in ("not json", "[]", '{"hooks": "nope"}', '{"hooks": {"Stop": [{"hooks": [{"command": 7}]}]}}'):
            path.write_text(junk, encoding="utf-8")
            self.assertEqual(spud.settings_hook_events(ctx, path), set(), junk)

    def test_the_missing_events_are_the_table_minus_what_the_file_carries(self):
        """settings_missing_hooks (SPW-006): settings_hold_hooks is it being empty, so the two cannot drift, and the
        list is what doctor's `settings` line and its partial problem name."""
        spud = load_spud_module()
        ctx = self.ctx()
        path = self.home.path / "elsewhere" / "settings.json"
        self.assertEqual(spud.settings_missing_hooks(ctx, path), list(spud.TABLE_EVENTS))  # absent: every event
        self.home.json("settings", "sync", "--path", path)
        self.assertEqual(spud.settings_missing_hooks(ctx, path), [])
        self.assertTrue(spud.settings_hold_hooks(ctx, path))
        data = json.loads(path.read_text(encoding="utf-8"))
        del data["hooks"]["SessionStart"]
        data["hooks"]["Stop"] = []
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        # the table's order, not the file's and not sorted: SessionStart before Stop
        self.assertEqual(spud.settings_missing_hooks(ctx, path), ["SessionStart", "Stop"])
        self.assertFalse(spud.settings_hold_hooks(ctx, path))
        # a project's key still decides whose lines count, as it does for the whole-table answer
        self.home.json("settings", "sync", "--path", path)
        self.assertEqual(spud.settings_missing_hooks(ctx, path, "badtakes"), list(spud.TABLE_EVENTS))


class HookTableTest(unittest.TestCase):
    """The two hand-rolled pieces HookEvidenceTest's reading rests on (SPW-003), read from the program alone: no home."""

    def test_the_two_spellings_of_a_shell_word_are_the_two_shlex_quote_writes(self):
        """SPW-003: projects/sessions generates both forms instead of importing shlex, which every hook run would pay
        0.11 ms for.  Exact for any text, this pins it: whatever shlex.quote writes is one of the two."""
        spud = load_spud_module()
        for text in ("/Users/Someone/Spud", "/Users/Someone/My Home", "/tmp/it's here", "a$b`c", "", "plain", "a'b'c", "/a\nb"):
            self.assertIn(shlex.quote(text), spud.shell_word_forms(text), text)

    def test_the_hook_table_installs_exactly_the_events_the_program_handles(self):
        """Why `missing` may be measured against hookio.HOOK_EVENTS (SPW-003): the events settings sync installs are the
        events a hook run can be dispatched to, so an event in one list and not the other would be a gap either way.
        TABLE_EVENTS is the table's own order (SPW-006), which is what a `settings` line's missing events are named in."""
        spud = load_spud_module()
        self.assertEqual({e for e, _ in spud.HOOK_TABLE}, set(spud.HOOK_EVENTS))
        self.assertEqual(set(spud.HOOK_HANDLERS), set(spud.HOOK_EVENTS))
        self.assertEqual(spud.TABLE_EVENTS, tuple(spud.HOOK_EVENTS))
        # SPD-223: one row per event, since a row's line is hook_command(event) and a second row of one event would carry
        # the same command; and PreToolUse's matcher names exactly the tools the hook enforces, the list pretool reads
        # back from the payload's tool_name, so the harness's filter and the program's are one list of names.
        self.assertEqual(len(spud.HOOK_TABLE), len(spud.TABLE_EVENTS))
        self.assertEqual(dict(spud.HOOK_TABLE)["PreToolUse"], "Agent|Bash|Write|Edit|MultiEdit|NotebookEdit")
        self.assertEqual(dict(spud.HOOK_TABLE)["PreToolUse"].split("|"), list(spud.ENFORCED_TOOLS))


class DoctorHomeSettingsTest(SpudTestCase):
    """doctor's `settings` line (SPW-006): the home's own .claude/settings.json read for a ledger hook line of this home
    for every event of HOOK_TABLE.

    Every home here is one `spud init` built, so the green case is the state a working home is really in and each wrong
    state is reached by editing that file, which is the only way to reach it once init's step 6 has run.  A problem in
    every wrong state, never a note: what is broken is this home's own installation, `spud --as spud settings sync`
    fixes it, and doctor's non-zero exit is what makes `init`'s step 10 and `home move`'s 7b refuse until it is run.
    """

    def settings(self):
        return self.home.path / ".claude" / "settings.json"

    def doctor(self):
        """(proc, report): the report either way -- a red doctor raises with it attached, and prints no lines at all."""
        proc = self.home.run("--json", "doctor", check=False)
        return proc, json.loads(proc.stdout)

    def write(self, data):
        self.settings().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def test_a_home_init_built_reports_every_event_on_its_own_line_beside_the_config(self):
        proc, report = self.doctor()
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        self.assertEqual(report["problems"], [])
        events = list(load_spud_module().TABLE_EVENTS)
        self.assertEqual(report["settings"], {"path": str(self.settings()), "exists": True, "unreadable": None,
                                             "missing": [], "events": events,
                                             "line": "%s: this home's ledger hooks, all 7 events" % self.settings()})
        lines = self.home.run("doctor").stdout.splitlines()
        labels = [line.split("  ")[0] for line in lines]
        self.assertIn("settings    %s" % report["settings"]["line"], lines)
        # where a reader will find it: under the config line, the home's own other state, and not among the project
        # lines or SPW-003's `hooks` line -- which is in the same report, under a label sharing no word with this one.
        self.assertEqual(labels[labels.index("config") + 1], "settings")
        self.assertEqual((labels.count("settings"), labels.count("hooks")), (1, 1))

    def test_a_home_that_was_never_synced_is_a_problem_naming_the_command_that_fixes_it(self):
        self.settings().unlink()
        proc, report = self.doctor()
        self.assertEqual(proc.returncode, EXIT_ERROR, proc.stdout)
        s = report["settings"]
        self.assertEqual((s["exists"], s["missing"], s["events"]), (False, list(load_spud_module().TABLE_EVENTS), []))
        self.assertEqual(s["line"], "%s (missing): none of this home's ledger hooks (see problems)" % self.settings())
        self.assertEqual(report["problems"], ["there is no %s, the settings file every session launched in the home"
                                              " reads, so a session launched in the home records and guards nothing --"
                                              " no SessionStart board, no path rule, no Agent guard and no Bash guard:"
                                              " run `spud --as spud settings sync`" % self.settings()])
        self.assertIn("settings sync", self.home.run("doctor", check=False).stderr)  # and in the text run's refusal
        self.home.json("settings", "sync")  # the named command, and nothing else, ends it
        self.assertEqual(self.doctor()[1]["problems"], [])

    def test_a_file_with_other_keys_and_none_of_the_hooks_is_the_same_problem_said_of_a_file_that_is_there(self):
        self.write({"model": "claude-fable-5-1", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]}})
        proc, report = self.doctor()
        self.assertEqual(proc.returncode, EXIT_ERROR, proc.stdout)
        self.assertTrue(report["settings"]["exists"])
        self.assertEqual(report["settings"]["line"], "%s: none of this home's ledger hooks (see problems)" % self.settings())
        self.assertEqual(report["problems"], ["%s carries none of this home's ledger hook lines, so a session launched"
                                              " in the home records and guards nothing -- no SessionStart board, no path"
                                              " rule, no Agent guard and no Bash guard: run `spud --as spud settings"
                                              " sync`" % self.settings()])

    def test_a_file_one_event_short_is_a_problem_that_names_the_events(self):
        """The partial case: an event short is an event whose hook never runs, so it is a problem too -- and it says
        which events, in the table's order, because the file is otherwise Eric's to diff."""
        data = json.loads(self.settings().read_text(encoding="utf-8"))
        del data["hooks"]["SubagentStart"]
        data["hooks"]["UserPromptSubmit"] = []
        self.write(data)
        proc, report = self.doctor()
        self.assertEqual(proc.returncode, EXIT_ERROR, proc.stdout)
        s = report["settings"]
        self.assertEqual((s["exists"], s["missing"]), (True, ["SubagentStart", "UserPromptSubmit"]))
        self.assertEqual(s["events"], ["PreToolUse", "PostToolUse", "SubagentStop", "SessionStart", "Stop"])
        self.assertEqual(s["line"], "%s: no ledger hook line for SubagentStart, UserPromptSubmit (see problems)" % self.settings())
        self.assertEqual(report["problems"], ["%s carries no ledger hook line of this home for SubagentStart,"
                                              " UserPromptSubmit, so those events record and guard nothing in a session"
                                              " launched in the home: run `spud --as spud settings sync`" % self.settings()])
        self.home.json("settings", "sync")
        self.assertEqual(self.doctor()[1]["problems"], [])
        # one event short is the likeliest shape of this, and the plural would read wrong of it
        data = json.loads(self.settings().read_text(encoding="utf-8"))
        del data["hooks"]["Stop"]
        self.write(data)
        _proc, report = self.doctor()
        self.assertEqual(report["problems"], ["%s carries no ledger hook line of this home for Stop, so that event"
                                              " records and guards nothing in a session launched in the home:"
                                              " run `spud --as spud settings sync`" % self.settings()])

    def test_a_file_that_is_not_a_readable_json_object_says_so_and_puts_the_hand_edit_first(self):
        """The fourth state, and the one where naming the command alone would be a lie: `settings sync` refuses a file
        it cannot read rather than dropping whatever a hand put there, so the message asks for the hand edit first."""
        for junk in ("{not json", "[]", ""):
            with self.subTest(junk=junk):
                self.settings().write_text(junk, encoding="utf-8")
                proc, report = self.doctor()
                self.assertEqual(proc.returncode, EXIT_ERROR, proc.stdout)
                s = report["settings"]
                self.assertEqual((s["exists"], s["missing"]), (True, list(load_spud_module().TABLE_EVENTS)))
                self.assertEqual(s["line"], "%s: not a readable JSON object, so no ledger hook of this home (see problems)" % self.settings())
                self.assertEqual(len(report["problems"]), 1, report["problems"])
                problem = report["problems"][0]
                self.assertIn(str(self.settings()), problem)
                self.assertIn("is not a %s" % ("JSON object" if junk == "[]" else "readable JSON file"), problem)
                self.assertIn("`spud --as spud settings sync` refuses it as it stands", problem)
                self.assertIn("make it a JSON object, or remove it, and then run that", problem)
                self.assertEqual(self.home.run("settings", "sync", check=False).returncode, EXIT_ERROR)  # as the problem says


class QuotedPathHomeTest(SpudTestCase):
    """A home whose path shlex.quote quotes: a space, and SPD-029's non-ASCII character (`\\w` under re.ASCII).  Every
    hook line this home installs then reads `SPUD_HOME='<home>' <python> -I -S '<home>/bin/spud' hook <event>`, in
    which projects/sessions.HOOK_MARK, `bin/spud hook`, is not a substring -- so a reading by the mark alone finds nothing
    in a file `settings sync` wrote itself, and SPW-006's check would have called every such home broken.  Which is why
    settings_missing_hooks reads the generated line too (settings_exact_hooks).  SPD-226: the mark's blindness cost more
    than that check -- `merge_hooks` could not see its own previous entries, so every `settings sync` in such a home
    appended a second copy of all seven lines, and `project uninstall` left them behind.  projects/sessions.is_ledger_command
    is the one rule now: the mark, or a line of the whole shape hook_command writes whatever its words' spelling.
    """

    home_name = "Sp üd"

    def ctx(self):
        return load_spud_module().Ctx(self.home.path, "SPUD_HOME", False, tool=self.home.tool)

    def test_a_home_whose_path_needs_quoting_reads_as_installed_and_doctor_is_green(self):
        spud = load_spud_module()
        ctx = self.ctx()
        settings = self.home.path / ".claude" / "settings.json"
        command = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["Stop"][0]["hooks"][0]["command"]
        # the premise: the line is this home's own, and the mark cannot see it
        self.assertEqual(command, spud.hook_command(ctx, "Stop"))
        self.assertIn("'%s' hook Stop" % self.home.launcher, command)
        self.assertNotIn(spud.HOOK_MARK, command)
        # SPD-226: the marked reading sees it anyway, by its shape, and so does `session show`
        self.assertEqual(spud.settings_hook_events(ctx, settings), set(spud.TABLE_EVENTS))
        # and the whole-table answer, and so doctor, read it too
        self.assertEqual(spud.settings_exact_hooks(ctx, settings), set(spud.TABLE_EVENTS))
        self.assertEqual(spud.settings_missing_hooks(ctx, settings), [])
        self.assertTrue(spud.settings_hold_hooks(ctx, settings))
        proc = self.home.run("doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        self.assertIn("settings    %s: this home's ledger hooks, all 7 events" % settings, proc.stdout.splitlines())

    # Hooks of the user's own that run some `bin/spud` spelled quoted, or quote the ledger's words, without being a line
    # hook_command writes: no sync, install or uninstall may take one of them for the ledger's.
    USERS_OWN = (
        "'/opt/my tools/bin/spud' hook Stop",
        "echo \"'/opt/my tools/bin/spud' hook Stop\"",
        "SPUD_HOME='/x y' python3 -I -S '/x y/bin/spud' hook Stop; say done",
        "SPUD_HOME='/x y' python3 '/x y/bin/spud' hook Stop",
        "SPUD_HOME='/x y' python3 -I -S '/x y/bin/spudder' hook Stop",
        "SPUD_HOME='/x y' python3 -I -S '/x y/bin/spud' hook Stop --project 'a' --verbose",
    )

    def test_one_rule_tells_the_ledgers_lines_from_the_users_own(self):
        """projects/sessions.is_ledger_command: HOOK_MARK as it has always been read (any home, any spelling around it),
        or a line of exactly the shape hook_command writes -- `SPUD_HOME=<w> <w> -I -S <w> hook <event>`, then
        ` --project <w>` or nothing, each word in one of the two spellings shlex.quote writes and the launcher word naming
        a path that ends in /bin/spud -- whatever home, interpreter or key it names, since home move strips an old home's
        lines by it."""
        spud = load_spud_module()
        ctx = self.ctx()
        for event in spud.TABLE_EVENTS:
            for key in (None, "badtakes", "it's mine"):
                self.assertTrue(spud.is_ledger_command(spud.hook_command(ctx, event, key)), (event, key))
        q = shlex.quote
        ledgers = (
            "SPUD_HOME=/old /old/python -I -S /old/bin/spud hook Stop",
            "/old/bin/spud hook Stop",  # the mark alone, as test_merge_keeps_foreign_hooks_and_replaces_stale_ledger_hooks pins it
            "SPUD_HOME=%s %s -I -S %s hook Stop" % (q("/other home"), q("/usr/local/bin/python 3"), q("/other home/bin/spud")),
            "SPUD_HOME=%s /usr/bin/python3 -I -S %s hook PreToolUse --project %s" % (q("/o's hüm"), q("/o's hüm/bin/spud"), q("a b")),
        )
        for command in ledgers:
            self.assertTrue(spud.is_ledger_command(command), command)
        for command in self.USERS_OWN + ("echo hi", "", "SPUD_HOME='/x y' python3 -I -S '/x y/bin/spud' hook"):
            self.assertFalse(spud.is_ledger_command(command), command)

    def test_every_sync_replaces_this_homes_lines_and_keeps_the_users_own(self):
        """The defect itself: `spud init` synced this home once already, so each sync after it must find the seven lines
        it wrote and put the seven it writes now in their place -- not a second copy beside them."""
        spud = load_spud_module()
        ctx = self.ctx()
        path = self.home.path / ".claude" / "settings.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        mine = {"hooks": [{"type": "command", "command": c} for c in self.USERS_OWN]}
        data["hooks"]["Stop"].insert(0, mine)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        for _ in range(2):
            self.home.json("settings", "sync")
            hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
            self.assertEqual(hooks["Stop"][0], mine)
            for event, matcher in spud.HOOK_TABLE:
                ours = [g for g in hooks[event] if g is not hooks["Stop"][0]] if event == "Stop" else hooks[event]
                entry = {"type": "command", "command": spud.hook_command(ctx, event), "timeout": spud.HOOK_TIMEOUT}
                self.assertEqual(ours, [{"matcher": matcher, "hooks": [entry]} if matcher else {"hooks": [entry]}], event)
        self.assertFalse(self.home.json("settings", "sync")["written"])


if __name__ == "__main__":
    unittest.main()
