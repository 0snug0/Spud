"""spud hook <event>: the six harness events (SPD-008).

Payload shapes follow the spike's verbatim captures (docs/spikes/2026-09-12-ledger-database.md,
Enforcement plan, facts 1 to 9; Claude Code 2.1.269) and the hooks reference.  Every run is
against a scratch SPUD_HOME.  Enforcing hooks (PreToolUse for Agent, Bash and the edit tools)
fail closed: a planned refusal is `permissionDecision: deny` on exit 0, anything unexpected is
exit 2.  Recording hooks (PostToolUse for Agent, SubagentStart, SubagentStop, SessionStart) fail
open: exit 0 whatever happens, the gap spooled and drained later as a `hook.error` event.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from helpers import EXIT_ERROR, EXIT_USAGE, PROGRAM, SPUD, SpudTestCase, load_spud_module, real_config, wall_clock
from helpers import git as scratch_git


def case_insensitive_fs(path):
    swapped = str(path).swapcase()
    return swapped != str(path) and os.path.exists(swapped) and os.path.samefile(str(path), swapped)

SESSION = "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b"
SESSION_B = "7d1e6a0c-5b2f-4c8d-9e3a-1f2b3c4d5e6f"  # a second Spud session working in parallel (SPD-018)
TRANSCRIPT = "/Users/eric/.claude/projects/-Users-eric-Personal-Spud/%s.jsonl"
AGENT_A = "ac8c90dafa6697045"  # the spike's background probe
AGENT_B = "adb9ecf5d69362ddd"  # the spike's foreground probe
AGENT_C = "a0cfc2d597e041e6b"
AGENT_D = "0123456789abcdef0"

# What post_agent_completed's tool_response reports beside the child's text, as the ledger keeps it in
# usage_json.completion (SPD-021).  totalTokens and usage cover the final request only.
COMPLETION = {
    "status": "completed",
    "usage": {"input_tokens": 3, "output_tokens": 40, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 40788, "iterations": [{"n": 1}]},
    "totalTokens": 41831, "totalDurationMs": 4791, "totalToolUseCount": 1,
    "toolStats": {"readCount": 0, "searchCount": 0, "bashCount": 1, "editFileCount": 0, "linesAdded": 0, "linesRemoved": 0, "otherToolCount": 0},
}
# HookCase.two_requests summed, 442 tokens: 12 out, 130 in (input plus cache creation), 300 cached.
TWO_REQUESTS_SUM = {"input_tokens": 30, "output_tokens": 12, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 300}
# HookCase.per_block with every entry added, as transcript_usage summed before SPD-023: 994 tokens over 5 entries
# (24 out, 370 in, 600 cached).  Counted once per request it is TWO_REQUESTS_SUM.
PER_ENTRY_SUM = {"input_tokens": 70, "output_tokens": 24, "cache_creation_input_tokens": 300, "cache_read_input_tokens": 600}
# The per-model breakdown a sum keeps beside its usage since SPD-013 (tests/test_cost.py).  two_requests' entries name no
# model and split no cache write, so their 100 writes count as unsplit; per_block's carry a service tier and a TTL split.
TWO_REQUESTS_BREAKDOWN = [{"requests": 2, "input_tokens": 30, "output_tokens": 12, "cache_read_input_tokens": 300, "cache_creation_unsplit_input_tokens": 100}]
PER_BLOCK_BREAKDOWN = [{"service_tier": "standard", "requests": 2, "input_tokens": 30, "output_tokens": 12, "cache_read_input_tokens": 300,
                        "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 0}}]


def common(cwd, agent_id=None, agent_type=None, session=SESSION):
    """The common input fields; inside a subagent the session is still the main session's (the SPD-015 probe
    captures: a lead's PreToolUse(Agent) and SubagentStop carry the main session_id, SPD-018)."""
    d = {
        "session_id": session,
        "transcript_path": TRANSCRIPT % session,
        "cwd": cwd,
        "permission_mode": "default",
        "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
        "scratchpad_dir": "/tmp/claude-501/-Users-eric-Personal-Spud/%s/scratchpad" % session,
    }
    if agent_id:
        d["agent_id"] = agent_id
        d["agent_type"] = agent_type or "spudagent"
    return d


class HookCase(SpudTestCase):
    """Builders for the payloads the harness sends, plus a planned team to spawn."""

    def setUp(self):
        super().setUp()
        self.cwd = str(self.home.path)
        # The ledger root's own launcher, which the Bash hook's allow is for (SPD-032).  A copy, never a link: a test that
        # writes it must not write through to the repository's.
        (self.home.path / "bin").mkdir(exist_ok=True)
        shutil.copyfile(SPUD, self.home.path / "bin" / "spud")
        self.t = self.new_ticket("Hooks", status="active")
        self.team = self.t["team_key"]
        # Spud's Bash runs in the session the payloads name, and `member new` records it (SPD-018).
        self.home.env["CLAUDE_CODE_SESSION_ID"] = SESSION

    # -- payloads ---------------------------------------------------------------
    def pre_agent(self, description, model="haiku", subagent_type="spudagent", agent_id=None, tool_use_id="toolu_01AGENT", session=SESSION, **extra):
        tool_input = {"description": description, "prompt": "Do the thing.", "subagent_type": subagent_type}
        if model is not None:
            tool_input["model"] = model
        tool_input.update(extra)
        p = common(self.cwd, agent_id, session=session)
        p.update({"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_use_id": tool_use_id, "tool_input": tool_input})
        return p

    def post_agent_launched(self, tool_use_id, agent_id, description, resolved="claude-haiku-4-5-20251001", caller=None, session=SESSION):
        p = common(self.cwd, caller, session=session)
        p.update({
            "hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_use_id": tool_use_id,
            "tool_input": {"description": description, "prompt": "Do the thing.", "subagent_type": "spudagent", "model": "haiku", "run_in_background": True},
            "tool_response": {"isAsync": True, "status": "async_launched", "agentId": agent_id, "description": description,
                              "resolvedModel": resolved, "prompt": "Do the thing.", "outputFile": "/tmp/x.txt", "canReadOutputFile": True},
            "duration_ms": 61,
        })
        return p

    def post_agent_completed(self, tool_use_id, agent_id, description, text="potato", caller=None):
        p = common(self.cwd, caller)
        p.update({
            "hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_use_id": tool_use_id,
            "tool_input": {"description": description, "prompt": "Say potato.", "subagent_type": "spudagent", "model": "haiku", "run_in_background": False},
            "tool_response": {
                "status": "completed", "agentId": agent_id, "agentType": "spudagent",
                "content": [{"type": "text", "text": text}, {"type": "text", "text": "(done)"}],
                "resolvedModel": "claude-haiku-4-5-20251001", "totalDurationMs": 4791, "totalTokens": 41831, "totalToolUseCount": 1,
                "usage": {"input_tokens": 3, "output_tokens": 40, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 40788, "iterations": [{"n": 1}]},
                "toolStats": {"readCount": 0, "searchCount": 0, "bashCount": 1, "editFileCount": 0, "linesAdded": 0, "linesRemoved": 0, "otherToolCount": 0},
            },
            "duration_ms": 4800,
        })
        return p

    def sub_start(self, agent_id, agent_type="spudagent", session=SESSION):
        p = common(self.cwd, session=session)
        p.update({"hook_event_name": "SubagentStart", "agent_id": agent_id, "agent_type": agent_type})
        return p

    def sub_stop(self, agent_id, last="potato", agent_type="spudagent", stop_hook_active=False, transcript=None, session=SESSION):
        p = common(self.cwd, session=session)
        p.update({
            "hook_event_name": "SubagentStop", "stop_hook_active": stop_hook_active, "agent_id": agent_id, "agent_type": agent_type,
            "agent_transcript_path": transcript or (str(self.home.path / "transcripts" / SESSION / "subagents" / ("agent-%s.jsonl" % agent_id))),
            "last_assistant_message": last,
            "background_tasks": [{"id": agent_id, "type": "subagent", "status": "running", "description": "x", "agent_type": agent_type}],
            "session_crons": [],
        })
        return p

    def pre_bash(self, command, agent_id=None, cwd=None):
        p = common(cwd or self.cwd, agent_id)
        p.update({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": "toolu_01BASH",
                  "tool_input": {"command": command, "description": "test"}})
        return p

    def pre_edit(self, path, agent_id=None, tool="Write"):
        p = common(self.cwd, agent_id)
        field = "notebook_path" if tool == "NotebookEdit" else "file_path"
        p.update({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_use_id": "toolu_01EDIT", "tool_input": {field: str(path), "content": "x"}})
        return p

    def session_start(self, source="startup"):
        p = common(self.cwd)
        p.update({"hook_event_name": "SessionStart", "source": source, "model": "claude-fable-5-1"})
        return p

    def stop(self, stop_hook_active=False, agent_id=None, session=SESSION):
        p = common(self.cwd, agent_id, session=session)
        p.update({"hook_event_name": "Stop", "stop_hook_active": stop_hook_active, "last_assistant_message": "Done.", "background_tasks": [], "session_crons": []})
        return p

    # -- transcripts --------------------------------------------------------------
    def write_transcript(self, agent_id, messages):
        """The subagent's own transcript, where sub_stop's agent_transcript_path points by default."""
        path = self.home.path / "transcripts" / SESSION / "subagents" / ("agent-%s.jsonl" % agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for m in messages:
                f.write(json.dumps(m) + "\n")
        return path

    def assistant(self, text, usage, ts, tool_uses=0):
        """An assistant entry with neither a message id nor a requestId: a request of its own.  Its tool_use
        ids are unique within a run, as the API issues them (tool uses count distinct ids since SPD-023)."""
        content = [{"type": "text", "text": text}] + [{"type": "tool_use", "id": "toolu_%s%d" % (text, i), "name": "Bash", "input": {}} for i in range(tool_uses)]
        return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": content, "usage": usage}}

    def block(self, message_id, request_id, content, usage, ts):
        """One transcript entry of an API response as the harness writes it (SPD-023): its content block (or
        a list of blocks), with the response's message id and requestId (None leaves either out) and the
        request's usage repeated on every entry."""
        message = {"id": message_id, "type": "message", "role": "assistant", "content": content if isinstance(content, list) else [content], "usage": usage}
        if message_id is None:
            del message["id"]
        entry = {"type": "assistant", "timestamp": ts, "message": message}
        if request_id is not None:
            entry["requestId"] = request_id
        return entry

    @staticmethod
    def tool_use(block_id):
        block = {"type": "tool_use", "name": "Bash", "input": {}}
        if block_id is not None:
            block["id"] = block_id
        return block

    def per_block(self):
        """two_requests as the harness writes it (SPD-023): one entry per content block, each repeating its
        API request's message id, requestId and usage, output_tokens growing to the request's final count.
        Once per request, by its last entry: TWO_REQUESTS_SUM (442 tokens), 3 tool uses, 4500 ms from the
        first entry to the last; every entry added: PER_ENTRY_SUM (994 tokens)."""
        first = {"input_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 0,
                 "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 0}, "service_tier": "standard"}
        second = {"input_tokens": 20, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 300,
                  "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}, "service_tier": "standard"}
        return [
            {"type": "user", "timestamp": "2026-09-12T13:30:00.000Z", "message": {"role": "user", "content": "hi"}},
            self.block("msg_01", "req_01", {"type": "thinking", "thinking": "", "signature": "s"}, dict(first, output_tokens=2), "2026-09-12T13:30:01.000Z"),
            self.block("msg_01", "req_01", self.tool_use("toolu_01"), dict(first, output_tokens=4), "2026-09-12T13:30:01.200Z"),
            self.block("msg_01", "req_01", self.tool_use("toolu_02"), dict(first, output_tokens=5), "2026-09-12T13:30:01.400Z"),
            {"type": "user", "timestamp": "2026-09-12T13:30:02.000Z", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_01", "content": "x"}, {"type": "tool_result", "tool_use_id": "toolu_02", "content": "y"}]}},
            self.block("msg_02", "req_02", {"type": "text", "text": "b"}, dict(second, output_tokens=6), "2026-09-12T13:30:04.000Z"),
            self.block("msg_02", "req_02", self.tool_use("toolu_03"), dict(second, output_tokens=7), "2026-09-12T13:30:04.500Z"),
        ]

    def set_member(self, member_id, **columns):
        """Columns of a members row set directly, for a row the hooks no longer write (a sum stored before SPD-023)."""
        con = self.home.connect()
        try:
            with con:
                con.execute("UPDATE members SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in columns), (*columns.values(), member_id))
        finally:
            con.close()

    def two_requests(self):
        """A run of two API requests as its transcript records them: TWO_REQUESTS_SUM (442 tokens),
        3 tool uses, 4500 ms from the first entry to the last."""
        return [
            {"type": "user", "timestamp": "2026-09-12T13:30:00.000Z", "message": {"role": "user", "content": "hi"}},
            self.assistant("a", {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 0}, "2026-09-12T13:30:01.000Z", tool_uses=2),
            {"type": "user", "timestamp": "2026-09-12T13:30:02.000Z", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}},
            self.assistant("b", {"input_tokens": 20, "output_tokens": 7, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 300}, "2026-09-12T13:30:04.500Z", tool_uses=1),
        ]

    # -- team ---------------------------------------------------------------------
    def plan(self, actor="spud", persona="scout", model="haiku", name=None, **kw):
        # SPD-097: these members write inside the home, which is no project, so their globs name it: `home:<glob>`.
        # A bare glob is relative to the ticket's project checkout, which after `home move` is the tool repository.
        kw.setdefault("deliverable", ["home:tests/**", "home:bin/spud"])
        if name:
            kw["name"] = name
        return self.new_member(self.t["key"], actor=actor, persona=persona, model=model, **kw)

    def description(self, m):
        return "%s/%s (%s, %s)" % (self.team, m["name"], m["lineage"], m["persona"])

    def spawn(self, m, agent_id, caller=None, tool_use_id=None, model=None, session=SESSION):
        """PreToolUse(Agent) allow followed by the background PostToolUse binding, all in session."""
        tool_use_id = tool_use_id or ("toolu_" + agent_id)
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(m), model=model or m["model"], subagent_type=m["agent_type"], agent_id=caller, tool_use_id=tool_use_id, session=session))
        self.assertEqual((pre.code, pre.decision), (0, "allow"), pre)
        st = self.home.hook("SubagentStart", self.sub_start(agent_id, m["agent_type"], session=session))
        self.assertEqual(st.code, 0, st)
        post = self.home.hook("PostToolUse", self.post_agent_launched(tool_use_id, agent_id, self.description(m), caller=caller, session=session))
        self.assertEqual(post.code, 0, post)
        return self.home.json("member", "show", m["ref"])["member"]

    def foreground(self, m, agent_id, order="stop-first", entries=None):
        """A foreground spawn of m run to its end, and its members row after: PreToolUse(Agent),
        SubagentStart, the child's first tool call (which binds it from the harness's meta.json) and
        its Result; then SubagentStop over two_requests (or the given transcript entries) and
        PostToolUse(Agent, completed), in the harness's order (spike, Enforcement plan, fact 8) or
        reversed with order="completion-first"."""
        tool_use_id = "toolu_" + agent_id
        description = self.description(m)
        pre = self.home.hook("PreToolUse", self.pre_agent(description, model=m["model"], subagent_type=m["agent_type"], tool_use_id=tool_use_id, run_in_background=False))
        self.assertEqual((pre.code, pre.decision), (0, "allow"), pre)
        self.assertEqual(self.home.hook("SubagentStart", self.sub_start(agent_id, m["agent_type"])).code, 0)
        transcript = self.write_transcript(agent_id, self.two_requests() if entries is None else entries)
        transcript.with_name("agent-%s.meta.json" % agent_id).write_text(json.dumps({
            "agentType": m["agent_type"], "description": description, "toolUseId": tool_use_id, "spawnDepth": 1,
            "requestShape": "foreground", "requestNonInteractive": False, "model": m["model"]}), encoding="utf-8")
        first_call = self.pre_bash("ls", agent_id=agent_id)
        first_call["transcript_path"] = str(self.home.path / "transcripts" / ("%s.jsonl" % SESSION))
        self.assertEqual(self.home.hook("PreToolUse", first_call).code, 0)
        self.home.json("member", "result", "Built it.", actor=agent_id)
        events = [("SubagentStop", self.sub_stop(agent_id, agent_type=m["agent_type"], transcript=str(transcript))),
                  ("PostToolUse", self.post_agent_completed(tool_use_id, agent_id, description))]
        for event, payload in (events if order == "stop-first" else events[::-1]):
            r = self.home.hook(event, payload)
            self.assertEqual((r.code, r.stdout), (0, ""), (event, r))
        return self.home.rows("SELECT * FROM members WHERE id = ?", m["id"])[0]

    def events(self, kind=None, **filters):
        args = ["events"]
        if kind:
            args += ["--kind", kind]
        for k, v in filters.items():
            args += ["--" + k, v]
        return self.home.json(*args)["events"]

    def denied(self):
        return self.events("hook.denied")


# =============================================================================
# PreToolUse / Agent
# =============================================================================


class PreAgentTest(HookCase):
    def test_allow_matches_the_planned_row_and_records_the_request(self):
        m = self.plan()
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(m)))
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.decision, "allow")
        self.assertEqual(r.json["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(r.stderr, "")
        row = self.home.rows("SELECT * FROM spawn_requests")[0]
        self.assertEqual(row["tool_use_id"], "toolu_01AGENT")
        self.assertEqual(row["session_id"], SESSION)
        self.assertIsNone(row["caller_agent_id"])
        self.assertEqual(row["description"], self.description(m))
        self.assertEqual((row["subagent_type"], row["model"], row["decision"]), ("spudagent", "haiku", "allow"))
        self.assertEqual(row["member_id"], m["id"])
        self.assertIsNone(row["agent_id"])
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual(shown["status"], "planned")  # PostToolUse makes it active
        kinds = [e["kind"] for e in self.events(member=m["ref"])]
        self.assertEqual(kinds, ["member.planned", "member.spawned"])
        spawned = self.events("member.spawned")[0]
        self.assertEqual(spawned["actor"], "hook:PreToolUse")
        self.assertEqual(spawned["data"]["tool_use_id"], "toolu_01AGENT")

    def test_run_in_background_is_recorded_and_not_required(self):
        m = self.plan()
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(m), run_in_background=False))
        self.assertEqual(r.decision, "allow", r)
        self.assertEqual(self.home.scalar("SELECT run_in_background FROM spawn_requests"), 0)

    def test_no_planned_row_is_denied_with_a_reason_and_hook_denied(self):
        r = self.home.hook("PreToolUse", self.pre_agent("%s/Nobody (01, scout)" % self.team))
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.decision, "deny")
        self.assertIn("no planned member", r.reason)
        self.assertIn("Nobody", r.reason)
        row = self.home.rows("SELECT * FROM spawn_requests")[0]
        self.assertEqual(row["decision"], "deny")
        self.assertIsNone(row["member_id"])
        self.assertEqual(row["reason"], r.reason)
        d = self.denied()
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["actor"], "hook:PreToolUse")
        self.assertEqual(d[0]["ticket"], self.t["key"])  # the team key in the description resolves the ticket
        self.assertEqual(d[0]["data"]["tool_name"], "Agent")

    def test_description_must_carry_the_team_key(self):
        m = self.plan()
        for bad in ("%s (%s, %s)" % (m["name"], m["lineage"], m["persona"]), "Do a thing", "%s/%s" % (self.team, m["name"]), ""):
            r = self.home.hook("PreToolUse", self.pre_agent(bad))
            self.assertEqual((r.code, r.decision), (0, "deny"), bad)
            self.assertIn("SPUD-nnn/<Name> (<lineage>, <persona>)", r.reason)
        self.assertEqual(len(self.denied()), 4)

    def test_lineage_persona_and_name_must_match_the_row(self):
        m = self.plan(persona="scout", model="haiku", name="Yukon")  # fixed name: the third case's literal Kestrel must never be what the draw picked (SPD-026)
        for desc, what in (
            ("%s/%s (02, scout)" % (self.team, m["name"]), "lineage"),
            ("%s/%s (01, engineer)" % (self.team, m["name"]), "persona"),
            ("%s/Kestrel (01, scout)" % self.team, "no planned member"),
            ("SPUD-999/%s (01, scout)" % m["name"], "no team"),
        ):
            r = self.home.hook("PreToolUse", self.pre_agent(desc))
            self.assertEqual((r.code, r.decision), (0, "deny"), desc)
            self.assertIn(what, r.reason, desc)
        # a found row that mismatches records member.spawn_denied on that row
        kinds = [e["kind"] for e in self.events(member=m["ref"])]
        self.assertEqual(kinds, ["member.planned", "member.spawn_denied", "member.spawn_denied"])

    def test_law_3_model_and_fork_and_isolation(self):
        m = self.plan(persona="scout", model="haiku")
        cases = [
            (dict(model=None), "model"),
            (dict(model="inherit"), "inherit"),
            (dict(model="opus"), "haiku"),
            (dict(model="claude-haiku-4-5-20251001"), "haiku"),
            (dict(subagent_type="fork"), "fork"),
            (dict(isolation="worktree"), "isolation"),
            (dict(fork=True), "fork"),
            (dict(subagent_type="general-purpose"), "agent_type"),
        ]
        for n, (kw, needle) in enumerate(cases):
            r = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_%02d" % n, **kw))
            self.assertEqual((r.code, r.decision), (0, "deny"), (kw, r))
            self.assertIn(needle, r.reason, (kw, r.reason))
            if needle in ("model", "inherit", "fork", "isolation"):
                self.assertIn("Law 3", r.reason)
        # one request is one row: the same tool_use_id twice updates rather than duplicates
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_00", model=None))
        self.assertEqual(r.decision, "deny")
        self.assertEqual(self.home.scalar("SELECT count(*) FROM spawn_requests WHERE decision = 'deny'"), len(cases))
        self.assertEqual(len(self.events("member.spawn_denied")), len(cases) + 1)  # every attempt is an event

    def test_contractor_spawns_as_its_agent_type(self):
        c = self.plan(persona="contractor", model="sonnet", agent_type="claude-code-guide")
        desc = "%s/%s (01, contractor)" % (self.team, c["name"])
        r = self.home.hook("PreToolUse", self.pre_agent(desc, model="sonnet", subagent_type="spudagent"))
        self.assertEqual(r.decision, "deny")
        self.assertIn("claude-code-guide", r.reason)
        r = self.home.hook("PreToolUse", self.pre_agent(desc, model="sonnet", subagent_type="claude-code-guide"))
        self.assertEqual(r.decision, "allow", r)

    def test_brief_must_be_non_empty(self):
        m = self.plan()
        con = self.home.connect()
        con.execute("UPDATE members SET brief = '  ' WHERE id = ?", (m["id"],))
        con.commit()
        con.close()
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(m)))
        self.assertEqual(r.decision, "deny")
        self.assertIn("Law 2", r.reason)

    def test_parent_must_be_the_caller(self):
        lead = self.plan(persona="engineer", model="opus")
        lead = self.spawn(lead, AGENT_A)
        child = self.plan(actor=lead["ref"])
        # Spud (no agent_id) may not spawn the lead's child
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(child)))
        self.assertEqual(r.decision, "deny")
        self.assertIn("parent", r.reason)
        # another bound agent may not either
        other = self.plan(persona="engineer", model="opus")
        other = self.spawn(other, AGENT_B)
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(child), agent_id=AGENT_B))
        self.assertEqual(r.decision, "deny")
        self.assertIn("parent", r.reason)
        # an unbound agent_id is refused outright
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(child), agent_id=AGENT_D))
        self.assertEqual(r.decision, "deny")
        self.assertIn("not bound", r.reason)
        # the lead itself may
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(child), agent_id=AGENT_A))
        self.assertEqual(r.decision, "allow", r)
        row = self.home.rows("SELECT * FROM spawn_requests WHERE decision = 'allow' AND caller_agent_id = ?", AGENT_A)[0]
        self.assertEqual(row["member_id"], child["id"])
        # and a lead may not spawn Spud's members
        second = self.plan(persona="engineer", model="opus")
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(second), model="opus", agent_id=AGENT_A))
        self.assertEqual(r.decision, "deny")
        self.assertIn("parent", r.reason)

    def test_a_spawned_member_cannot_be_spawned_again(self):
        m = self.plan()
        self.spawn(m, AGENT_A)
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_02"))
        self.assertEqual(r.decision, "deny")
        self.assertIn("active", r.reason)
        self.assertIn("planned", r.reason)

    def test_the_first_allow_reserves_the_planned_row(self):
        """Rooster's HIGH-1: before the binding lands, a second Agent call for the same planned
        member must not be allowed (N running copies of one row would count as one)."""
        m = self.plan()
        r1 = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_a"))
        self.assertEqual(r1.decision, "allow")
        r2 = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_b"))
        self.assertEqual(r2.decision, "deny", r2)
        self.assertIn("toolu_a", r2.reason)
        self.assertIn("not yet bound", r2.reason)
        self.assertIn("member finish", r2.reason)
        # SPD-028: Spud is the caller here (no agent_id), so the way out is --as spud with [--next]
        self.assertIn("`spud --as spud member finish %s --status failed --outcome '<why>' [--next '<what happens next>']`" % m["ref"], r2.reason)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM spawn_requests WHERE decision = 'allow'"), 1)
        # the same tool_use_id again (a deferred call resumed) is the same request, not a second spawn
        r3 = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_a"))
        self.assertEqual(r3.decision, "allow", r3)
        # a spawn that failed in the harness is recorded as failed and re-planned as a new row
        self.home.json("member", "finish", m["ref"], "--status", "failed", "--outcome", "Concurrent subagent limit reached", actor="spud")
        m2 = self.plan()
        r4 = self.home.hook("PreToolUse", self.pre_agent(self.description(m2), tool_use_id="toolu_c"))
        self.assertEqual(r4.decision, "allow", r4)

    def test_the_pending_reservation_refusal_names_a_spudagent_callers_own_actor(self):
        """SPD-028: a lead cannot run `spud --as spud` (Law 6 refuses it inside a subagent), so its
        own second spawn of a reserved child must be refused under its own agent_id, with no --next
        (a nested `member finish` refuses that option)."""
        lead = self.plan(persona="engineer", model="opus")
        lead = self.spawn(lead, AGENT_A)
        child = self.plan(actor=lead["ref"])
        r1 = self.home.hook("PreToolUse", self.pre_agent(self.description(child), agent_id=AGENT_A, tool_use_id="toolu_x"))
        self.assertEqual(r1.decision, "allow", r1)
        r2 = self.home.hook("PreToolUse", self.pre_agent(self.description(child), agent_id=AGENT_A, tool_use_id="toolu_y"))
        self.assertEqual(r2.decision, "deny", r2)
        self.assertIn("toolu_x", r2.reason)
        self.assertIn("not yet bound", r2.reason)
        self.assertIn("`spud --as %s member finish %s --status failed --outcome '<why>'`" % (AGENT_A, child["ref"]), r2.reason)
        self.assertNotIn("--next", r2.reason)
        self.assertNotIn("--as spud", r2.reason)

    def test_limits_are_recomputed_inside_the_hook(self):
        kids = [self.plan() for _ in range(3)]
        config = real_config()
        config["limits"]["root_fan_out"] = 2
        self.home.write_config(config)
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(kids[0])))
        self.assertEqual(r.decision, "deny")
        self.assertIn("fan-out", r.reason)
        self.assertIn("Law 4", r.reason)
        config["limits"]["root_fan_out"] = 3
        config["limits"]["max_concurrent_total"] = 2
        self.home.write_config(config)
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(kids[0])))
        self.assertEqual(r.decision, "deny")
        self.assertIn("concurren", r.reason)
        config["limits"]["max_concurrent_total"] = 9
        self.home.write_config(config)
        lead = self.spawn(kids[0], AGENT_A)
        child = self.plan(actor=lead["ref"])
        config["limits"]["max_depth"] = 1
        self.home.write_config(config)
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(child), agent_id=AGENT_A))
        self.assertEqual(r.decision, "deny")
        self.assertIn("depth", r.reason)
        # a freed slot makes room again
        config["limits"]["max_depth"] = 2
        config["limits"]["root_fan_out"] = 3
        self.home.write_config(config)
        self.home.json("member", "finish", kids[1]["ref"], "--status", "failed", "--outcome", "never spawned", actor="spud")
        r = self.home.hook("PreToolUse", self.pre_agent(self.description(kids[2])))
        self.assertEqual(r.decision, "allow", r)

    def test_other_tools_get_no_decision(self):
        p = self.pre_bash("ls")
        p["tool_name"] = "Read"
        p["tool_input"] = {"file_path": "/etc/hosts"}
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""))

    def test_missing_database_denies_with_run_init(self):
        os.remove(self.home.db)
        for p in (self.pre_agent("x"), self.pre_bash("ls"), self.pre_edit(self.home.path / "x")):
            r = self.home.hook("PreToolUse", p)
            self.assertEqual((r.code, r.decision), (0, "deny"), r)
            self.assertIn("no ledger database at", r.reason)
            self.assertIn("spud init", r.reason)
        self.assertFalse(self.home.spool.exists())

    def test_malformed_payloads_fail_closed(self):
        m = self.plan()
        for raw in ("", "not json", "[1, 2]", '"str"'):
            r = self.home.hook("PreToolUse", raw)
            self.assertEqual(r.code, 2, (raw, r))
            self.assertIn("spud hook PreToolUse", r.stderr)
            self.assertEqual(r.stdout, "")
        p = self.pre_agent(self.description(m))
        del p["tool_use_id"]
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.decision), (0, "deny"), r)
        self.assertIn("tool_use_id", r.reason)
        p = self.pre_agent(self.description(m))
        p["hook_event_name"] = "PostToolUse"
        r = self.home.hook("PreToolUse", p)
        self.assertEqual(r.code, 2, r)
        self.assertIn("hook_event_name", r.stderr)
        p = self.pre_agent(self.description(m))
        p["tool_input"] = "nope"
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.decision), (0, "deny"), r)

    def test_unknown_event_is_a_usage_error(self):
        r = self.home.hook("Bogus", {})
        self.assertEqual(r.code, EXIT_USAGE)


# =============================================================================
# PostToolUse / Agent
# =============================================================================


class PostAgentTest(HookCase):
    def test_background_launch_binds_and_activates(self):
        m = self.plan()
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_bg"))
        self.assertEqual(pre.decision, "allow")
        st = self.home.hook("SubagentStart", self.sub_start(AGENT_A))
        self.assertEqual(st.code, 0)
        started = self.events("member.started")[0]
        self.assertIsNone(started["member"])
        self.assertEqual(started["agent_id"], AGENT_A)
        r = self.home.hook("PostToolUse", self.post_agent_launched("toolu_bg", AGENT_A, self.description(m)))
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""), r)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual(shown["status"], "active")
        self.assertEqual(shown["agent_id"], AGENT_A)
        self.assertEqual(shown["resolved_model"], "claude-haiku-4-5-20251001")
        self.assertIsNotNone(shown["spawned_at"])
        self.assertIsNone(shown["total_tokens"])
        row = self.home.rows("SELECT * FROM members WHERE id = ?", m["id"])[0]
        self.assertEqual((row["session_id"], row["tool_use_id"]), (SESSION, "toolu_bg"))
        self.assertEqual(self.home.scalar("SELECT agent_id FROM spawn_requests WHERE tool_use_id = 'toolu_bg'"), AGENT_A)
        # the child's earlier event is attached now
        started = self.events("member.started")[0]
        self.assertEqual(started["member"], m["ref"])
        self.assertEqual(started["ticket"], self.t["key"])
        kinds = [e["kind"] for e in self.events(member=m["ref"])]
        self.assertEqual(kinds, ["member.planned", "member.spawned", "member.started", "member.status"])
        # and the agent_id is now an actor for the CLI
        out = self.home.json("member", "log", "first line", actor=AGENT_A)
        self.assertTrue(out["ok"])
        self.assertEqual(out["member"]["ref"], m["ref"])

    def test_a_completion_alone_keeps_its_figures_and_leaves_total_tokens_empty(self):
        """SPD-021: totalTokens and usage cover the final request only, so with no transcript sum yet
        total_tokens stays empty and the figures are kept under "completion", away from the usage key
        token_counts reads; the whole-run duration and tool count fill their columns."""
        m = self.plan()
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_fg", run_in_background=False))
        self.assertEqual(pre.decision, "allow")
        r = self.home.hook("PostToolUse", self.post_agent_completed("toolu_fg", AGENT_B, self.description(m)))
        self.assertEqual(r.code, 0, r)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual(shown["status"], "active")
        self.assertEqual(shown["agent_id"], AGENT_B)
        self.assertEqual((shown["total_tokens"], shown["duration_ms"], shown["tool_uses"]), (None, 4791, 1))
        row = self.home.rows("SELECT return_text, usage_json FROM members WHERE id = ?", m["id"])[0]
        self.assertEqual(row["return_text"], "potato\n(done)")
        self.assertEqual(json.loads(row["usage_json"]), {"source": "PostToolUse", "completion": COMPLETION})

    def test_unknown_tool_use_id_is_a_gap_not_a_failure(self):
        r = self.home.hook("PostToolUse", self.post_agent_launched("toolu_unknown", AGENT_A, "whatever"))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        errs = self.events("hook.error")
        self.assertEqual(len(errs), 1)
        self.assertIn("toolu_unknown", errs[0]["body"])

    def test_binding_never_touches_a_finished_member(self):
        m = self.plan()
        self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_x"))
        self.home.json("member", "finish", m["ref"], "--status", "failed", "--outcome", "spawn failed", actor="spud")
        r = self.home.hook("PostToolUse", self.post_agent_launched("toolu_x", AGENT_A, self.description(m)))
        self.assertEqual(r.code, 0, r)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual(shown["status"], "failed")
        self.assertEqual(len(self.events("hook.error")), 1)

    def test_other_tools_are_ignored(self):
        p = self.post_agent_launched("toolu_bg", AGENT_A, "x")
        p["tool_name"] = "Bash"
        p["tool_response"] = {"stdout": "hi", "stderr": "", "interrupted": False, "isImage": False}
        r = self.home.hook("PostToolUse", p)
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""))
        self.assertEqual(self.events(), [e for e in self.events() if e["kind"] != "hook.error"])


# =============================================================================
# SubagentStart
# =============================================================================


class SubagentStartTest(HookCase):
    def test_context_names_the_agent_id_and_the_as_flag(self):
        r = self.home.hook("SubagentStart", self.sub_start(AGENT_A))
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.json["hookSpecificOutput"]["hookEventName"], "SubagentStart")
        self.assertEqual(r.context, "Ledger: your agent_id is `%s`; every `spud` command you run takes `--as %s`." % (AGENT_A, AGENT_A))
        e = self.events("member.started")[0]
        self.assertEqual((e["actor"], e["agent_id"], e["member"]), ("hook:SubagentStart", AGENT_A, None))
        self.assertEqual(e["data"]["agent_type"], "spudagent")

    def test_a_bound_agent_is_attached_and_named(self):
        m = self.plan()
        self.spawn(m, AGENT_A)
        r = self.home.hook("SubagentStart", self.sub_start(AGENT_A))  # a resume fires SubagentStart again
        self.assertIn(m["ref"], r.context)
        started = self.events("member.started")
        self.assertEqual([e["member"] for e in started], [m["ref"], m["ref"]])

    def test_missing_database_is_silent(self):
        os.remove(self.home.db)
        r = self.home.hook("SubagentStart", self.sub_start(AGENT_A))
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""))


# =============================================================================
# SubagentStop
# =============================================================================


class SubagentStopTest(HookCase):
    def test_hold_once_then_let_go_after_result(self):
        m = self.plan()
        self.spawn(m, AGENT_A)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, last="I am done."))
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.json["decision"], "block")
        self.assertIn("spud --as %s member result" % AGENT_A, r.json["reason"])
        self.assertIn("member block", r.json["reason"])
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertIsNone(shown["stopped_at"])
        self.assertEqual(shown["status"], "active")
        held = self.events("member.stopped")[-1]
        self.assertEqual((held["member"], held["data"]["held"]), (m["ref"], True))
        self.home.json("member", "result", "Produced x.", actor=AGENT_A)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, last="I am done."))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertIsNotNone(shown["stopped_at"])
        self.assertEqual(shown["status"], "active")  # the parent's verdict, never the hook's
        row = self.home.rows("SELECT return_text, transcript_path FROM members WHERE id = ?", m["id"])[0]
        self.assertEqual(row["return_text"], "I am done.")
        self.assertIn("agent-%s.jsonl" % AGENT_A, row["transcript_path"])
        final = self.events("member.stopped")[-1]
        self.assertEqual((final["data"]["held"], final["data"]["unrecorded"]), (False, False))

    def test_blocked_counts_as_recorded(self):
        m = self.plan()
        self.spawn(m, AGENT_A)
        self.home.json("member", "block", "Need Eric.", actor=AGENT_A)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual((r.code, r.stdout), (0, ""), r)

    def test_stop_hook_active_lets_an_unrecorded_return_go(self):
        m = self.plan()
        self.spawn(m, AGENT_A)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, stop_hook_active=True))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertIsNotNone(shown["stopped_at"])
        final = self.events("member.stopped")[-1]
        self.assertEqual((final["data"]["held"], final["data"]["unrecorded"], final["data"]["stop_hook_active"]), (False, True, True))

    def test_transcript_usage_is_summed_when_no_totals_were_recorded(self):
        m = self.plan()
        self.spawn(m, AGENT_A)
        self.home.json("member", "result", "ok", actor=AGENT_A)
        path = self.write_transcript(AGENT_A, self.two_requests())
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, transcript=str(path)))
        self.assertEqual(r.code, 0, r)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual(shown["total_tokens"], 442)
        self.assertEqual(shown["tool_uses"], 3)
        self.assertEqual(shown["duration_ms"], 4500)
        usage = json.loads(self.home.scalar("SELECT usage_json FROM members WHERE id = ?", m["id"]))
        self.assertEqual(usage, {"source": "transcript", "counting": "request", "messages": 2, "usage": TWO_REQUESTS_SUM, "breakdown": TWO_REQUESTS_BREAKDOWN})  # a background run: no completion

    def test_a_stop_counts_a_transcript_written_per_block_once_per_request(self):  # SPD-023, proof 4
        m = self.plan()
        self.spawn(m, AGENT_A)
        self.home.json("member", "result", "ok", actor=AGENT_A)
        path = self.write_transcript(AGENT_A, self.per_block())
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, transcript=str(path)))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        row = self.home.rows("SELECT total_tokens, duration_ms, tool_uses, usage_json FROM members WHERE id = ?", m["id"])[0]
        self.assertEqual((row["total_tokens"], row["duration_ms"], row["tool_uses"]), (442, 4500, 3))
        self.assertEqual(json.loads(row["usage_json"]), {"source": "transcript", "counting": "request", "messages": 2, "usage": TWO_REQUESTS_SUM, "breakdown": PER_BLOCK_BREAKDOWN})

    def test_a_transcript_sum_after_the_completion_fills_total_tokens_and_keeps_the_completion(self):
        """SPD-021, the reverse of the harness's foreground order: the completion alone leaves
        total_tokens empty and the later stop's transcript sum fills it; the completion's figures stay
        beside the sum, and its whole-run duration and tool count keep their columns."""
        m = self.plan()
        self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_fg"))
        self.home.hook("PostToolUse", self.post_agent_completed("toolu_fg", AGENT_B, self.description(m)))
        self.assertIsNone(self.home.json("member", "show", m["ref"])["member"]["total_tokens"])
        self.home.json("member", "result", "ok", actor=AGENT_B)
        path = self.write_transcript(AGENT_B, [self.assistant("a", {"input_tokens": 1, "output_tokens": 1}, "2026-09-12T13:30:01.000Z")])
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_B, transcript=str(path)))
        self.assertEqual(r.code, 0, r)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual((shown["total_tokens"], shown["duration_ms"], shown["tool_uses"]), (2, 4791, 1))
        usage = json.loads(self.home.scalar("SELECT usage_json FROM members WHERE id = ?", m["id"]))
        self.assertEqual(usage, {"source": "transcript", "counting": "request", "messages": 1, "completion": COMPLETION,
                                 "usage": {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                                 "breakdown": [{"requests": 1, "input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0}]})

    def test_foreground_binding_through_meta_json(self):
        m = self.plan()
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_meta", run_in_background=False))
        self.assertEqual(pre.decision, "allow")
        path = self.write_transcript(AGENT_B, [])
        meta = path.with_name("agent-%s.meta.json" % AGENT_B)
        meta.write_text(json.dumps({"agentType": "spudagent", "description": self.description(m), "toolUseId": "toolu_meta", "spawnDepth": 1, "requestShape": "foreground", "requestNonInteractive": False, "model": "haiku"}), encoding="utf-8")
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_B, transcript=str(path)))
        self.assertEqual(r.json["decision"], "block", r)  # bound, so the hold applies
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual((shown["status"], shown["agent_id"]), ("active", AGENT_B))
        stopped = self.events("member.stopped")[-1]
        self.assertEqual(stopped["data"]["bound_by"], "meta.json")
        self.assertEqual(self.home.scalar("SELECT agent_id FROM spawn_requests WHERE tool_use_id = 'toolu_meta'"), AGENT_B)

    def test_foreground_binding_falls_back_only_when_one_request_is_unbound(self):
        """Rooster's MEDIUM-5: with two unbound requests and no meta.json the hook does not guess."""
        m1 = self.plan()
        m2 = self.plan()
        self.home.hook("PreToolUse", self.pre_agent(self.description(m1), tool_use_id="toolu_first"))
        self.home.hook("PreToolUse", self.pre_agent(self.description(m2), tool_use_id="toolu_second"))
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_B, stop_hook_active=True))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertIsNone(self.home.json("member", "show", m1["ref"])["member"]["agent_id"])
        self.assertIsNone(self.home.json("member", "show", m2["ref"])["member"]["agent_id"])
        self.assertIsNone(self.events("member.stopped")[-1]["data"]["bound_by"])
        errs = self.events("hook.error")
        self.assertEqual(len(errs), 1)
        self.assertIn("2 unbound", errs[0]["body"])
        # bind one of them through PostToolUse; the other is now the only candidate
        self.home.hook("PostToolUse", self.post_agent_launched("toolu_first", AGENT_A, self.description(m1)))
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_B, stop_hook_active=True))
        self.assertEqual(r.code, 0, r)
        self.assertEqual(self.home.json("member", "show", m2["ref"])["member"]["agent_id"], AGENT_B)
        self.assertEqual(self.events("member.stopped")[-1]["data"]["bound_by"], "oldest-unbound")

    def test_an_unbound_stop_is_recorded_and_not_held(self):
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_C, agent_type="Explore"))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        e = self.events("member.stopped")[-1]
        self.assertEqual((e["member"], e["agent_id"], e["data"]["bound_by"]), (None, AGENT_C, None))

    def test_missing_database_is_silent(self):
        os.remove(self.home.db)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""))


# =============================================================================
# SubagentStop and PostToolUse(Agent): a member's run totals (SPD-021)
# =============================================================================


class RunTotalsTest(HookCase):
    """A foreground spawn fires SubagentStop, then PostToolUse(Agent, completed) (spike, Enforcement
    plan, fact 8).  In either order total_tokens and the usage key token_counts reads are the
    transcript sum; the completion, whose totalTokens and usage cover its final request only (hooks
    reference, Agent tool telemetry), is kept beside the sum; its totalDurationMs and
    totalToolUseCount, whole-run figures, fill duration_ms and tool_uses."""

    USAGE_COLUMNS = ("total_tokens", "duration_ms", "tool_uses", "usage_json")

    def usage_of(self, m):
        return self.home.rows("SELECT total_tokens, duration_ms, tool_uses, usage_json FROM members WHERE id = ?", m["id"])[0]

    def test_the_harness_order_keeps_the_transcript_sum_with_the_completion_beside_it(self):
        row = self.foreground(self.plan(), AGENT_B)
        self.assertEqual((row["total_tokens"], row["duration_ms"], row["tool_uses"]), (442, 4791, 1))
        self.assertEqual(json.loads(row["usage_json"]), {"source": "transcript", "counting": "request", "messages": 2, "usage": TWO_REQUESTS_SUM,
                                                          "breakdown": TWO_REQUESTS_BREAKDOWN, "completion": COMPLETION})

    def test_the_reverse_order_ends_in_the_same_row(self):
        harness_order = self.foreground(self.plan(), AGENT_B)
        reverse = self.foreground(self.plan(), AGENT_C, order="completion-first")
        self.assertEqual(json.loads(reverse["usage_json"])["source"], "transcript")
        self.assertEqual({k: reverse[k] for k in self.USAGE_COLUMNS}, {k: harness_order[k] for k in self.USAGE_COLUMNS})

    def test_a_stop_without_a_readable_transcript_erases_nothing(self):
        summed = self.plan()
        self.foreground(summed, AGENT_B)
        alone = self.plan()
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(alone), tool_use_id="toolu_alone", run_in_background=False))
        self.assertEqual(pre.decision, "allow", pre)
        self.home.hook("PostToolUse", self.post_agent_completed("toolu_alone", AGENT_C, self.description(alone)))
        self.home.json("member", "result", "Built it.", actor=AGENT_C)
        no_usage = self.write_transcript(AGENT_D, [
            {"type": "user", "timestamp": "2026-09-12T13:30:00.000Z", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "timestamp": "2026-09-12T13:30:01.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "a"}]}},
        ])
        kept = [
            (summed, AGENT_B, (442, 4791, 1, {"source": "transcript", "counting": "request", "messages": 2, "usage": TWO_REQUESTS_SUM,
                                              "breakdown": TWO_REQUESTS_BREAKDOWN, "completion": COMPLETION})),
            (alone, AGENT_C, (None, 4791, 1, {"source": "PostToolUse", "completion": COMPLETION})),
        ]
        for m, agent_id, want in kept:
            for label, path in (("a missing transcript", self.home.path / "transcripts" / "missing.jsonl"), ("no assistant usage", no_usage)):
                with self.subTest(member=m["name"], transcript=label):
                    r = self.home.hook("SubagentStop", self.sub_stop(agent_id, transcript=str(path)))
                    self.assertEqual((r.code, r.stdout), (0, ""), r)
                    row = self.usage_of(m)
                    self.assertEqual((row["total_tokens"], row["duration_ms"], row["tool_uses"], json.loads(row["usage_json"])), want)

    def test_a_stored_transcript_sum_is_not_summed_again(self):
        m = self.plan()
        before = self.foreground(m, AGENT_B)
        self.assertEqual(before["total_tokens"], 442)
        longer = self.write_transcript(AGENT_D, self.two_requests() + [
            self.assistant("c", {"input_tokens": 1000, "output_tokens": 1000}, "2026-09-12T13:31:00.000Z", tool_uses=5)])
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_B, transcript=str(longer)))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertEqual(self.usage_of(m), {k: before[k] for k in self.USAGE_COLUMNS})

    def test_both_orders_count_a_transcript_written_per_block_once_per_request(self):  # SPD-023, proof 4
        for order, agent_id in (("stop-first", AGENT_B), ("completion-first", AGENT_C)):
            with self.subTest(order=order):
                row = self.foreground(self.plan(), agent_id, order=order, entries=self.per_block())
                self.assertEqual((row["total_tokens"], row["duration_ms"], row["tool_uses"]), (442, 4791, 1))
                self.assertEqual(json.loads(row["usage_json"]),
                                 {"source": "transcript", "counting": "request", "messages": 2, "usage": TWO_REQUESTS_SUM, "breakdown": PER_BLOCK_BREAKDOWN, "completion": COMPLETION})

    def test_a_stop_leaves_a_sum_counted_per_entry_to_member_resum(self):  # SPD-023: a stored sum is not summed again at a stop
        m = self.plan()
        self.spawn(m, AGENT_A)
        self.home.json("member", "result", "ok", actor=AGENT_A)
        path = self.write_transcript(AGENT_A, self.per_block())
        old = json.dumps({"source": "transcript", "messages": 5, "usage": PER_ENTRY_SUM})
        self.set_member(m["id"], total_tokens=994, duration_ms=4500, tool_uses=3, usage_json=old)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, transcript=str(path)))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertEqual(self.usage_of(m), {"total_tokens": 994, "duration_ms": 4500, "tool_uses": 3, "usage_json": old})


# =============================================================================
# transcript_usage: each API request counted once (SPD-023)
# =============================================================================


class RequestCountingTest(HookCase):
    """The harness writes an API response as one transcript entry per content block, each repeating the
    request's message id, requestId and usage, with only output_tokens growing to its final count.  A sum
    groups the entries by request and counts each once, by its last entry; tool uses count distinct
    tool_use blocks; the sum is marked "counting": "request".  Before SPD-023 every entry was added."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.spud = load_spud_module()

    def sum_of(self, entries):
        return self.spud.transcript_usage(self.write_transcript(AGENT_A, entries))

    def test_a_request_written_as_several_entries_counts_once_by_its_last_entry(self):  # proof 1
        shared = {"input_tokens": 2, "cache_creation_input_tokens": 75049, "cache_read_input_tokens": 0}
        got = self.sum_of([
            {"type": "user", "timestamp": "2026-09-13T09:00:00.000Z", "message": {"role": "user", "content": "go"}},
            self.block("msg_A", "req_A", {"type": "thinking", "thinking": "", "signature": "s"}, dict(shared, output_tokens=5), "2026-09-13T09:00:01.000Z"),
            self.block("msg_A", "req_A", {"type": "text", "text": "a"}, dict(shared, output_tokens=120), "2026-09-13T09:00:02.000Z"),
            self.block("msg_A", "req_A", self.tool_use("toolu_A1"), dict(shared, output_tokens=234), "2026-09-13T09:00:03.000Z"),
            self.block("msg_B", "req_B", {"type": "text", "text": "b"},
                       {"input_tokens": 3, "output_tokens": 40, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 75049}, "2026-09-13T09:00:05.000Z"),
        ])
        self.assertEqual(got, {"total_tokens": 150377, "duration_ms": 5000, "tool_uses": 1, "usage_json": {
            "source": "transcript", "counting": "request", "messages": 2,
            "usage": {"input_tokens": 5, "output_tokens": 274, "cache_creation_input_tokens": 75049, "cache_read_input_tokens": 75049},
            "breakdown": [{"requests": 2, "input_tokens": 5, "output_tokens": 274, "cache_read_input_tokens": 75049, "cache_creation_unsplit_input_tokens": 75049}]}})

    def test_the_request_is_the_message_id_with_its_request_id(self):  # the grouping key
        usage = {"input_tokens": 1, "output_tokens": 2, "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4}
        cases = [
            ("a message id without a requestId", [self.block("msg_A", None, {"type": "text", "text": "a"}, usage, "2026-09-13T09:00:01.000Z"),
                                                  self.block("msg_A", None, self.tool_use("toolu_A1"), usage, "2026-09-13T09:00:02.000Z")], 1),
            ("one message id with two requestIds", [self.block("msg_A", "req_A", {"type": "text", "text": "a"}, usage, "2026-09-13T09:00:01.000Z"),
                                                    self.block("msg_A", "req_B", {"type": "text", "text": "b"}, usage, "2026-09-13T09:00:02.000Z")], 2),
        ]
        for label, entries, requests in cases:
            with self.subTest(label):
                got = self.sum_of(entries)
                self.assertEqual((got["usage_json"]["messages"], got["total_tokens"], got["usage_json"]["counting"]), (requests, 10 * requests, "request"))

    def test_tool_uses_count_distinct_tool_use_blocks(self):  # proof 2
        first = {"input_tokens": 1, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 100}
        second = {"input_tokens": 2, "output_tokens": 3, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 110}
        thinking = {"type": "thinking", "thinking": "", "signature": "s"}
        one_block_each = [
            self.block("msg_A", "req_A", thinking, dict(first, output_tokens=1), "2026-09-13T09:00:01.000Z"),
            self.block("msg_A", "req_A", self.tool_use("toolu_A1"), dict(first, output_tokens=2), "2026-09-13T09:00:02.000Z"),
            self.block("msg_A", "req_A", self.tool_use("toolu_A2"), dict(first, output_tokens=3), "2026-09-13T09:00:03.000Z"),
            self.block("msg_B", "req_B", self.tool_use("toolu_B1"), second, "2026-09-13T09:00:04.000Z"),
        ]
        cases = [
            ("one block per entry", one_block_each),
            ("a block id written twice", one_block_each + [self.block("msg_B", "req_B", self.tool_use("toolu_B1"), second, "2026-09-13T09:00:04.000Z")]),
            ("the same blocks in one entry each", [
                self.block("msg_A", "req_A", [thinking, self.tool_use("toolu_A1"), self.tool_use("toolu_A2")], dict(first, output_tokens=3), "2026-09-13T09:00:03.000Z"),
                self.block("msg_B", "req_B", [self.tool_use("toolu_B1")], second, "2026-09-13T09:00:04.000Z"),
            ]),
        ]
        for label, entries in cases:
            with self.subTest(label):
                got = self.sum_of(entries)
                self.assertEqual((got["tool_uses"], got["usage_json"]["messages"], got["total_tokens"]), (3, 2, 114 + 115))
        with self.subTest("blocks without an id count where they appear"):
            got = self.sum_of([
                self.block("msg_A", "req_A", [self.tool_use(None), self.tool_use(None)], dict(first, output_tokens=2), "2026-09-13T09:00:01.000Z"),
                self.block("msg_A", "req_A", self.tool_use(None), dict(first, output_tokens=3), "2026-09-13T09:00:02.000Z"),
            ])
            self.assertEqual((got["tool_uses"], got["usage_json"]["messages"], got["total_tokens"]), (3, 1, 114))

    def test_entries_without_a_message_id_count_once_each(self):  # proof 3
        usage = {"input_tokens": 1, "output_tokens": 2, "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4}
        got = self.sum_of([
            self.assistant("a", usage, "2026-09-13T09:00:01.000Z"),  # neither a message id nor a requestId
            self.assistant("b", usage, "2026-09-13T09:00:02.000Z"),
            self.block(None, "req_C", {"type": "text", "text": "c"}, usage, "2026-09-13T09:00:03.000Z"),  # a requestId alone
            self.block(None, "req_C", {"type": "text", "text": "d"}, usage, "2026-09-13T09:00:04.000Z"),
        ])
        self.assertEqual(got, {"total_tokens": 40, "duration_ms": 3000, "tool_uses": 0, "usage_json": {
            "source": "transcript", "counting": "request", "messages": 4,
            "usage": {"input_tokens": 4, "output_tokens": 8, "cache_creation_input_tokens": 12, "cache_read_input_tokens": 16},
            "breakdown": [{"requests": 4, "input_tokens": 4, "output_tokens": 8, "cache_read_input_tokens": 16, "cache_creation_unsplit_input_tokens": 12}]}})


# =============================================================================
# SubagentStop / the lead hold (SPD-015)
# =============================================================================


class LeadHoldTest(HookCase):
    """Law 9 below Spud: a member that returns while a child it spawned has returned
    unrecorded (a final stop, no outcome) is held once, in the same block as the Result hold."""

    RESULT_ONLY = ("record your Result with `spud --as %s member result '<what you produced, where, what you verified, what is left>'`"
                   " (or Blocked with `spud --as %s member block '<the question and the options>'`) before returning; then return your summary")

    def lead(self, agent_id=AGENT_A, result="Built the thing."):
        """A root member, spawned and bound, with its own Result recorded unless result is None."""
        m = self.spawn(self.plan(persona="engineer", model="opus"), agent_id)
        if result is not None:
            self.home.json("member", "result", result, actor=agent_id)
        return m

    def child_of(self, lead, agent_id, returned=True):
        """A child planned and spawned by the lead; by default it records its Result and stops."""
        child = self.spawn(self.plan(actor=lead["ref"]), agent_id, caller=lead["agent_id"])
        if returned:
            self.home.json("member", "result", "Child done.", actor=agent_id)
            r = self.home.hook("SubagentStop", self.sub_stop(agent_id))
            self.assertEqual((r.code, r.stdout), (0, ""), r)
        return self.home.json("member", "show", child["ref"])["member"]

    def finish_command(self, agent_id, ref):
        return "`spud --as %s member finish %s --status done|blocked|failed --outcome '<verdict>'`" % (agent_id, ref)

    def stopped_events(self, ref):
        return self.events("member.stopped", member=ref)

    def test_a_lead_with_its_result_recorded_is_held_once_for_a_returned_unrecorded_child(self):
        lead = self.lead()
        child = self.child_of(lead, AGENT_B)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, last="All done."))
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 1 child you spawned returned and is not recorded: "), reason)
        self.assertIn("%s (01.01, scout), stopped %s" % (child["ref"], child["stopped_at"][11:16]), reason)
        self.assertIn(self.finish_command(AGENT_A, child["ref"]), reason)
        self.assertIn("`spud proposal list --open`", reason)
        self.assertIn("`spud --as %s proposal decide " % AGENT_A, reason)
        self.assertNotIn("member result", reason)  # its own Result is recorded
        self.assertNotIn("--next", reason)  # Law 9 one level down never offers it (SPD-027 touched Spud's Stop only)
        self.assertTrue(reason.endswith(" Then return your summary."), reason)
        # held, so nothing is stamped: stopped_at belongs to the final stop
        shown = self.home.json("member", "show", lead["ref"])["member"]
        self.assertIsNone(shown["stopped_at"])
        self.assertEqual(shown["status"], "active")
        held = self.stopped_events(lead["ref"])[-1]
        self.assertEqual((held["data"]["held"], held["data"]["unrecorded"], held["data"]["unrecorded_children"]), (True, False, [child["ref"]]))

    def test_stop_hook_active_lets_the_lead_go_and_records_the_unrecorded_children(self):
        lead = self.lead()
        child = self.child_of(lead, AGENT_B)
        self.assertEqual(self.home.hook("SubagentStop", self.sub_stop(AGENT_A)).json["decision"], "block")
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, stop_hook_active=True))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        shown = self.home.json("member", "show", lead["ref"])["member"]
        self.assertIsNotNone(shown["stopped_at"])
        self.assertEqual(shown["status"], "active")  # the parent's verdict, never the hook's
        final = self.stopped_events(lead["ref"])[-1]
        self.assertEqual((final["data"]["held"], final["data"]["unrecorded"], final["data"]["unrecorded_children"]), (False, False, [child["ref"]]))
        self.assertIn(child["ref"], final["body"])

    def test_after_the_lead_finishes_the_child_it_is_not_held(self):
        lead = self.lead()
        child = self.child_of(lead, AGENT_B)
        self.assertEqual(self.home.hook("SubagentStop", self.sub_stop(AGENT_A)).json["decision"], "block")
        out = self.home.json("member", "finish", child["ref"], "--status", "done", "--outcome", "Accepted.", actor=AGENT_A)
        self.assertEqual(out["member"]["status"], "done")
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))  # a fresh stop, not the let-go continuation
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        final = self.stopped_events(lead["ref"])[-1]
        self.assertEqual((final["data"]["held"], final["data"]["unrecorded"]), (False, False))
        self.assertNotIn("unrecorded_children", final["data"])

    def test_one_block_names_the_child_first_and_then_the_missing_result(self):
        lead = self.lead(result=None)
        child = self.child_of(lead, AGENT_B)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 1 child you spawned returned and is not recorded: "), reason)
        self.assertLess(reason.index(self.finish_command(AGENT_A, child["ref"])), reason.index("member result"), reason)
        self.assertTrue(reason.endswith(" Then " + self.RESULT_ONLY % (AGENT_A, AGENT_A)), reason)
        held = [e for e in self.stopped_events(lead["ref"]) if e["data"]["held"]]
        self.assertEqual(len(held), 1)
        self.assertEqual((held[0]["data"]["unrecorded"], held[0]["data"]["unrecorded_children"]), (True, [child["ref"]]))
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, stop_hook_active=True))
        self.assertEqual((r.code, r.stdout), (0, ""), r)

    def test_a_lead_that_recorded_blocked_is_still_held_for_its_child(self):
        lead = self.lead(result=None)
        self.home.json("member", "block", "Need Eric.", actor=AGENT_A)
        child = self.child_of(lead, AGENT_B)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["decision"], "block", r)
        self.assertIn(self.finish_command(AGENT_A, child["ref"]), r.json["reason"])
        self.assertNotIn("member result", r.json["reason"])

    def test_every_unrecorded_child_is_listed_with_its_own_command(self):
        lead = self.lead()
        first = self.child_of(lead, AGENT_B)
        second = self.child_of(lead, AGENT_C)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 2 children you spawned returned and are not recorded: "), reason)
        self.assertLess(reason.index(first["ref"]), reason.index(second["ref"]), reason)  # oldest stop first
        for m in (first, second):
            self.assertIn("%s (%s, scout), stopped %s" % (m["ref"], m["lineage"], m["stopped_at"][11:16]), reason)
            self.assertIn(self.finish_command(AGENT_A, m["ref"]), reason)
        held = self.stopped_events(lead["ref"])[-1]
        self.assertEqual(held["data"]["unrecorded_children"], [first["ref"], second["ref"]])

    def test_finished_children_and_a_siblings_children_are_not_listed(self):
        lead = self.lead()
        sibling = self.lead(agent_id=AGENT_C)
        recorded = self.child_of(lead, AGENT_B)
        self.home.json("member", "finish", recorded["ref"], "--status", "done", "--outcome", "Accepted.", actor=AGENT_A)
        nephew = self.child_of(sibling, AGENT_D)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual((r.code, r.stdout), (0, ""), r)  # nothing of the lead's is unrecorded
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_C))
        self.assertEqual(r.json["decision"], "block", r)
        self.assertIn(nephew["ref"], r.json["reason"])
        self.assertNotIn(recorded["ref"], r.json["reason"])

    def test_the_result_only_reason_is_unchanged(self):
        self.lead(result=None)  # no children at all
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["reason"], self.RESULT_ONLY % (AGENT_A, AGENT_A))
        other = self.lead(agent_id=AGENT_C, result=None)  # every child recorded
        child = self.child_of(other, AGENT_D)
        self.home.json("member", "finish", child["ref"], "--status", "done", "--outcome", "Accepted.", actor=AGENT_C)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_C))
        self.assertEqual(r.json["reason"], self.RESULT_ONLY % (AGENT_C, AGENT_C))

    def test_a_lead_is_held_once_for_a_child_that_is_still_running(self):
        """The probe of 2026-09-12: a background child is not killed when its lead returns, its
        completion notification goes to the main session, and its row is orphaned.  The block is
        the only thing that keeps the lead alive long enough to collect it."""
        lead = self.lead()
        child = self.child_of(lead, AGENT_B, returned=False)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 1 child you planned is still alive: "), reason)
        self.assertIn("%s (01.01, scout), running since %s" % (child["ref"], child["spawned_at"][11:16]), reason)
        self.assertIn(self.finish_command(AGENT_A, child["ref"]), reason)
        self.assertIn("Wait inside this turn", reason)
        self.assertIn("`spud member show %s`" % child["ref"], reason)
        self.assertNotIn("--next", reason)  # Law 9 one level down never offers it (SPD-027 touched Spud's Stop only)
        held = self.stopped_events(lead["ref"])[-1]
        self.assertEqual(held["data"]["alive_children"], [child["ref"]])
        self.assertNotIn("unrecorded_children", held["data"])
        self.assertIsNone(self.home.json("member", "show", lead["ref"])["member"]["stopped_at"])
        # it waited; the child returns, and the let-go stop records it as unrecorded instead
        self.home.json("member", "result", "Child done.", actor=AGENT_B)
        self.home.hook("SubagentStop", self.sub_stop(AGENT_B))
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, stop_hook_active=True))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        final = self.stopped_events(lead["ref"])[-1]
        self.assertEqual(final["data"]["unrecorded_children"], [child["ref"]])
        self.assertNotIn("alive_children", final["data"])

    def test_a_planned_child_that_was_never_spawned_holds_the_lead_once(self):
        lead = self.lead()
        child = self.plan(actor=lead["ref"])
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertIn("%s (01.01, scout), planned %s and never spawned" % (child["ref"], child["planned_at"][11:16]), reason)
        self.assertIn("`spud --as %s member finish %s --status failed --outcome '<why>'`" % (AGENT_A, child["ref"]), reason)
        self.assertNotIn("Wait inside this turn", reason)  # nothing to wait for: it never started
        # SPD-028: a child with no reservation keeps today's words byte for byte
        expected = ("Law 9: 1 child you planned is still alive: %s (01.01, scout), planned %s and never spawned."
                    " The planned child never started: spawn it now, or record it with `spud --as %s member finish %s --status failed --outcome '<why>'`"
                    " so the row stops counting against your limits. Then return your summary."
                    % (child["ref"], child["planned_at"][11:16], AGENT_A, child["ref"]))
        self.assertEqual(reason, expected)
        self.assertEqual(self.stopped_events(lead["ref"])[-1]["data"]["alive_children"], [child["ref"]])
        out = self.home.json("member", "finish", child["ref"], "--status", "failed", "--outcome", "Never spawned.", actor=AGENT_A)
        self.assertEqual(out["member"]["status"], "failed")
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual((r.code, r.stdout), (0, ""), r)

    def test_a_reserved_spawn_that_never_bound_is_still_alive(self):
        lead = self.lead()
        child = self.plan(actor=lead["ref"])
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(child), agent_id=AGENT_A, tool_use_id="toolu_reserved"))
        self.assertEqual(pre.decision, "allow", pre)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertIn(child["ref"], reason)
        self.assertEqual(self.stopped_events(lead["ref"])[-1]["data"]["alive_children"], [child["ref"]])
        # SPD-028: a reserved child is not "never spawned" and is never offered "spawn it now" (the
        # reservation would refuse it); its item names the reservation, and its way out is the
        # hook.error check, polling inside this turn, and the lead's own actor, no grace period.
        req_at = self.home.scalar("SELECT at FROM spawn_requests WHERE tool_use_id = 'toolu_reserved'")
        self.assertIn("%s (01.01, scout), spawn allowed %s (tool_use_id toolu_reserved), never bound" % (child["ref"], req_at[11:16]), reason)
        self.assertNotIn("spawn it now", reason)
        self.assertNotIn("and never spawned", reason)
        self.assertIn("`spud events --kind hook.error --json`", reason)
        self.assertIn("wait inside this turn", reason)
        self.assertIn("`spud member show %s`" % child["ref"], reason)
        self.assertIn("`spud --as %s member finish %s --status failed --outcome '<why>'`" % (AGENT_A, child["ref"]), reason)
        self.assertIn("plan a new member", reason)
        self.assertIn("reservation refuses a second spawn", reason)
        self.assertNotIn("--next", reason)  # a nested finish refuses it
        self.assertNotIn("--as spud", reason)  # the lead cannot run this
        # the way out works: record it failed under the lead's own actor, then the hold clears
        out = self.home.json("member", "finish", child["ref"], "--status", "failed", "--outcome", "Harness failed the spawn.", actor=AGENT_A)
        self.assertEqual(out["member"]["status"], "failed")
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual((r.code, r.stdout), (0, ""), r)

    def test_one_block_names_a_plain_planned_child_and_a_reserved_one_in_their_own_words(self):
        lead = self.lead()
        plain = self.plan(actor=lead["ref"])
        reserved = self.plan(actor=lead["ref"])
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(reserved), agent_id=AGENT_A, tool_use_id="toolu_reserved2"))
        self.assertEqual(pre.decision, "allow", pre)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 2 children you planned are still alive: "), reason)
        self.assertLess(reason.index(plain["ref"]), reason.index(reserved["ref"]), reason)  # planned first
        self.assertIn("%s (01.01, scout), planned %s and never spawned" % (plain["ref"], plain["planned_at"][11:16]), reason)
        req_at = self.home.scalar("SELECT at FROM spawn_requests WHERE tool_use_id = 'toolu_reserved2'")
        self.assertIn("%s (01.02, scout), spawn allowed %s (tool_use_id toolu_reserved2), never bound" % (reserved["ref"], req_at[11:16]), reason)
        self.assertIn("The planned child never started: spawn it now, or record it with `spud --as %s member finish %s --status failed --outcome '<why>'`"
                      % (AGENT_A, plain["ref"]), reason)
        self.assertIn("The planned child was allowed to spawn and never bound: first look for its tool_use_id in `spud events --kind hook.error --json`", reason)
        self.assertIn("`spud --as %s member finish %s --status failed --outcome '<why>'` and plan a new member" % (AGENT_A, reserved["ref"]), reason)
        self.assertNotIn("spawn %s now" % reserved["ref"], reason)
        held = self.stopped_events(lead["ref"])[-1]
        self.assertEqual(held["data"]["alive_children"], [plain["ref"], reserved["ref"]])

    def test_one_block_covers_a_returned_child_and_a_running_one(self):
        lead = self.lead()
        returned = self.child_of(lead, AGENT_B)
        running = self.child_of(lead, AGENT_C, returned=False)
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A))
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 1 child you spawned returned and is not recorded: "), reason)
        self.assertIn("1 child you planned is still alive: ", reason)
        self.assertLess(reason.index(returned["ref"]), reason.index("is still alive"), reason)
        self.assertIn(self.finish_command(AGENT_A, running["ref"]), reason)
        held = self.stopped_events(lead["ref"])[-1]
        self.assertEqual((held["data"]["unrecorded_children"], held["data"]["alive_children"]), ([returned["ref"]], [running["ref"]]))

    def test_spud_records_an_orphaned_grandchild_once_its_lead_is_finished(self):
        lead = self.lead()
        child = self.child_of(lead, AGENT_B)
        self.assertEqual(self.home.hook("SubagentStop", self.sub_stop(AGENT_A)).json["decision"], "block")
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, stop_hook_active=True))  # ignored the hold
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        r = self.home.hook("Stop", self.stop())
        self.assertEqual(r.json["decision"], "block", r)
        self.assertIn(lead["ref"], r.json["reason"])
        self.assertNotIn(child["ref"], r.json["reason"])  # a grandchild is its living lead's business
        self.home.json("member", "finish", lead["ref"], "--status", "done", "--outcome", "Accepted; its child is not recorded.", actor="spud")
        r = self.home.hook("Stop", self.stop())
        self.assertEqual(r.json["decision"], "block", r)
        self.assertIn(child["ref"], r.json["reason"])
        out = self.home.json("member", "finish", child["ref"], "--status", "done", "--outcome", "Recorded by Spud: orphaned.", actor="spud")
        self.assertEqual((out["member"]["status"], out["member"]["outcome"]), ("done", "Recorded by Spud: orphaned."))
        r = self.home.hook("Stop", self.stop())
        self.assertEqual((r.code, r.stdout), (0, ""), r)


# =============================================================================
# PreToolUse / Bash
# =============================================================================


class BashHookCase(HookCase):
    """Two engineers planned with tests/** and bin/spud (AGENT_A the lead, AGENT_B the other), and PreToolUse(Bash) asserts."""

    def setUp(self):
        super().setUp()
        self.lead = self.spawn(self.plan(persona="engineer", model="opus"), AGENT_A)
        self.other = self.spawn(self.plan(persona="engineer", model="opus"), AGENT_B)
        self.spud_cli = "python3.14 -I -S %s/bin/spud" % self.home.path

    def bash(self, command, agent_id=AGENT_A, cwd=None):
        return self.home.hook("PreToolUse", self.pre_bash(command, agent_id=agent_id, cwd=cwd))

    def assertRefused(self, command, needle, agent_id=AGENT_A, cwd=None):
        r = self.bash(command, agent_id, cwd)
        self.assertEqual((r.code, r.decision), (0, "deny"), (command, r))
        self.assertIn(needle, r.reason, (command, r.reason))
        return r

    def assertSilent(self, command, agent_id=AGENT_A, cwd=None):
        r = self.bash(command, agent_id, cwd)
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""), (command, r))

    def assertAllowed(self, command, agent_id=AGENT_A, cwd=None):
        r = self.bash(command, agent_id, cwd)
        self.assertEqual((r.code, r.decision), (0, "allow"), (command, r))


class PreBashTest(BashHookCase):

    def test_law_7_git_verbs_for_members(self):
        for verb in ("commit -m x", "add .", "stash", "stash pop", "checkout main", "switch -c x", "rebase main", "reset --hard", "push", "merge x", "cherry-pick abc", "worktree add ../x", "branch -D x", "branch new", "pull", "tag v1", "am x.patch", "apply x.patch", "revert HEAD", "restore f", "rm f", "mv a b", "clean -fd"):
            r = self.assertRefused("git " + verb, "Law 7")
            self.assertIn("git " + verb.split()[0], r.reason)
        for ok in ("git status", "git log --oneline -5", "git diff", "git show HEAD", "git blame f", "git worktree list --porcelain", "git branch", "git branch -a", "git branch --show-current", "git rev-parse HEAD", "git ls-files", "git grep x", "git stash list", "git fetch", "git remote -v"):
            self.assertSilent(ok)
        self.assertEqual(len(self.denied()), 23)

    def test_git_hidden_in_shell_constructs(self):
        for cmd in (
            "cd /tmp && git commit -m x",
            "ls; git add .",
            "true || git push",
            "echo $(git commit -m x)",
            "echo `git stash`",
            'echo "$(git commit -m x)"',
            "sh -c 'git commit -m x'",
            'bash -lc "cd /x && git push"',
            "zsh -c \"git rebase main\"",
            "eval 'git checkout main'",
            "gi\"\"t commit",
            "g\\it commit",
            "'git' commit",
            "git -C /tmp commit -m x",
            "git -c user.name=x commit",
            "git --git-dir=/x/.git commit",
            "/usr/bin/git commit",
            "(git commit)",
            "{ git commit; }",
            "if git commit; then echo ok; fi",
            "env GIT_DIR=/x git commit",
            "nohup git push &",
            "xargs git add < list",
            "time git commit",
            "command git commit",
            "bash <<'EOF'\ngit commit -m x\nEOF",
            "python3 -c 'print(1)' && git   commit",
            "git\tcommit",
        ):
            self.assertRefused(cmd, "Law 7")

    def test_law_6_spud_mutations_for_members(self):
        for tail in ("ticket new --title x", "ticket move SPD-001 --status done", "ticket edit SPD-001 --title y", "--as spud member log hi", "init", "migrate", "import", "import --file x.md", "render", "backup", "settings sync", "config sync", "--json --as spud board", "member finish SPUD-001/01 --as spud --status done --outcome x",
                     "member resum --all", "member resum --all --dry-run", "--as %s member resum SPUD-001/01" % AGENT_A,
                     "backup --daily", "backup --daily --keep 3", "--as %s backup --daily" % AGENT_A, "schedule show", "schedule install", "schedule install --at 04:30",
                     "schedule uninstall", "--as %s schedule show" % AGENT_A, "--as spud schedule install"):
            self.assertRefused("%s %s" % (self.spud_cli, tail), "Law 6")
        self.assertIn("member resum", self.assertRefused("%s member resum --all" % self.spud_cli, "Law 6").reason)
        for verb in ("show", "install", "uninstall"):
            reason = self.assertRefused("%s schedule %s" % (self.spud_cli, verb), "Law 6").reason
            self.assertIn("spud schedule", reason)
            self.assertIn("proposal file", reason)
        self.assertRefused("%s/bin/spud ticket new --title x" % self.home.path, "Law 6")
        self.assertRefused("cd %s && python3.14 -I -S bin/spud ticket new --title x" % self.home.path, "Law 6")
        self.assertRefused("spud ticket new --title x", "Law 6")
        self.assertRefused("%s ticket show SPD-001 && %s init" % (self.spud_cli, self.spud_cli), "Law 6")

    def test_spud_runs_backup_daily_and_schedule(self):
        for tail in ("backup --daily", "backup --daily --keep 3", "schedule show", "schedule show --at 04:30", "schedule install", "schedule install --at 04:30", "schedule uninstall"):
            self.assertAllowed("%s --as spud %s" % (self.spud_cli, tail), agent_id=None)
            self.assertAllowed("%s --json --as spud %s" % (self.spud_cli, tail), agent_id=None)

    def test_as_must_resolve_to_the_caller(self):
        lead, other = self.lead, self.other
        for who in (other["ref"], "%s/%s" % (self.team, other["lineage"]), AGENT_B, AGENT_D, "spud", "SPUD-999/Nobody"):
            self.assertRefused("%s --as %s member log hi" % (self.spud_cli, who), "--as")
        for who in (AGENT_A, lead["ref"], "%s/%s" % (self.team, lead["lineage"])):
            self.assertAllowed("%s --as %s member log hi" % (self.spud_cli, who))
            self.assertAllowed("%s --as=%s member log hi" % (self.spud_cli, who))
        # an unbound caller may not use --as at all
        r = self.assertRefused("%s --as %s member log hi" % (self.spud_cli, AGENT_D), "not bound", agent_id=AGENT_D)
        self.assertIn(AGENT_D, r.reason)
        # reads need no --as and are allowed
        for tail in ("board --brief", "events --member %s" % lead["ref"], "member show %s" % lead["ref"], "member list", "member list --ticket SPD-001",
                     "sql --readonly 'select 1'", "doctor", "ticket show SPD-001", "fleet", "card SPD-001", "proposal list"):
            self.assertAllowed("%s %s" % (self.spud_cli, tail))

    def test_hook_and_database_access_are_refused_for_everyone(self):
        for agent_id in (AGENT_A, None):
            self.assertRefused("%s hook PreToolUse" % self.spud_cli, "hook", agent_id=agent_id)
            self.assertRefused("echo '{}' | %s hook Stop" % self.spud_cli, "hook", agent_id=agent_id)
            for cmd in (
                "sqlite3 %s/.spud/ledger.db 'select 1'" % self.home.path,
                "/usr/bin/sqlite3 x.db",
                "python3 -c 'import sqlite3; sqlite3.connect(\"/x/ledger.db\")'",
                "python3.14 -m sqlite3 x.db",
                "cat .spud/ledger.db",
                "ls -la %s/.spud" % self.home.path,
                "cp ledger.db /tmp/x",
                "python3 - <<EOF\nimport sqlite3\nEOF",
                "node -e 'require(\"node:sqlite\")'",
                "rm -rf .spud/",
                "SQLITE3 x.db",
                "cat .SPUD/ledger.db",
                "cat Ledger.DB",
                "python3 -c 'import SQLite3'",
            ):
                self.assertRefused(cmd, "spud sql --readonly", agent_id=agent_id)
        self.assertAllowed("%s sql --readonly 'select count(*) from members'" % self.spud_cli, agent_id=None)

    def test_command_words_from_substitutions_are_refused_for_members(self):
        """Rooster's MEDIUM-3: a command word the hook cannot see is refused like an unresolvable variable."""
        for cmd in (
            "$(echo git) push",
            "git=$(which git); $git push",
            "sh -c \"$(printf 'git push')\"",
            "eval \"$(echo git push)\"",
            "$(printf %s git) push",
            "S=$(echo sqlite3); $S /tmp/copy.db",
            "`which git` push",
            "$(printf %s gi)t push",
        ):
            self.assertRefused(cmd, "spell the command out")
        for cmd in ("$(echo git) push", "`which git` push"):
            self.assertSilent(cmd, agent_id=None)
        self.assertSilent("echo $(git status)")
        self.assertSilent("x=$(date); echo $x")

    def test_law_1_redirections_for_spud(self):
        home = self.home.path
        for cmd in (
            "echo x > %s/bin/spud" % home,
            "echo x >> tests/new.py",
            "cat <<EOF > docs/spikes/x.md\nhi\nEOF",
            "printf x | tee ledger/tickets/SPD-001.md",
            "printf x | tee -a %s/reports/2026-09-12.md" % home,
            "echo x >| bin/x",
            "echo x &> bin/x",
            "ls >& bin/x",
            "cd tests && echo x > new.py",
            "cd %s/docs; echo x > spike.md" % home,
        ):
            self.assertRefused(cmd, "Law 1", agent_id=None)
        for ok in (
            "echo x > CLAUDE.md",
            "echo x >> %s/spud.config.json" % home,
            "echo x > .claude/settings.json",
            "echo x > ledger/Home.md",
            "echo x > ledger/Board.base",
            "echo x > ledger/_templates/ticket.md",
            "echo x > docs/superpowers/specs/x.md",
            "echo x > /tmp/x",
            "echo x > ~/.claude/projects/-Users-x/memory/x.md",
            "make 2>&1",
            "echo hi >&2",
            "ls > /dev/null",
            "cat < bin/spud",
            "echo x > \"$TMPDIR/x\"",
        ):
            self.assertSilent(ok, agent_id=None)
        self.assertEqual(len(self.denied()), 10)
        self.assertEqual(self.denied()[0]["data"]["tool_name"], "Bash")

    def test_member_redirections_follow_the_deliverables(self):
        self.assertSilent("echo x > tests/out.txt")
        self.assertSilent("echo x > /tmp/scratch.txt")
        self.assertRefused("echo x > docs/x.md", "deliverables")
        self.assertRefused("printf x | tee CLAUDE.md", "deliverables")
        self.assertRefused("echo x > ledger/teams/SPUD-001/X.md", "generated")

    def test_well_formed_spud_calls_are_allowed_and_everything_else_is_silent(self):
        self.assertAllowed("%s --as spud ticket new --title x" % self.spud_cli, agent_id=None)
        self.assertAllowed("%s --json board" % self.spud_cli, agent_id=None)
        self.assertAllowed("SPUD_HOME=%s %s --as spud board" % (self.home.path, self.spud_cli), agent_id=None)
        self.assertAllowed("%s --as %s member log 'a; b && c'" % (self.spud_cli, AGENT_A))
        self.assertAllowed("%s --as %s member log @- <<'EOF'\nDid a thing; git status was clean.\nEOF" % (self.spud_cli, AGENT_A))
        for cmd in ("ls -la", "python3.14 -I -S -m unittest discover -s tests -t tests", "%s board | head" % self.spud_cli, "%s bogus" % self.spud_cli, "%s board && ls" % self.spud_cli, "echo 'unterminated"):
            self.assertSilent(cmd)
        self.assertEqual(self.denied(), [])

    def test_missing_command_is_denied(self):
        p = self.pre_bash("ls")
        p["tool_input"] = {}
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.decision), (0, "deny"), r)

    def test_spud_may_not_write_a_members_own_sections(self):
        lead = self.lead
        for who in (AGENT_A, lead["ref"], "%s/%s" % (self.team, lead["lineage"])):
            for tail in ("member log hi", "member result done", "member block why", "proposal file --title x"):
                self.assertRefused("%s --as %s %s" % (self.spud_cli, who, tail), "Law 5", agent_id=None)
            for tail in ("member show %s" % lead["ref"], "member list", "events", "board"):
                self.assertAllowed("%s --as %s %s" % (self.spud_cli, who, tail), agent_id=None)
        self.assertAllowed("%s --as spud member finish %s --status done --outcome x" % (self.spud_cli, lead["ref"]), agent_id=None)
        self.assertAllowed("%s --as spud member log hi" % self.spud_cli, agent_id=None)  # the CLI refuses it (exit 3); not the hook's call

    def test_a_spud_call_is_recognized_however_its_script_is_spelled(self):
        """SPD-029: Law 6's refusals depend on seeing a spud call.  A case variant or a fold of bin/spud's name is the
        same file on macOS, and a symlink by any name runs the launcher (it finds its program from its real path)."""
        home = self.home.path
        (home / "bin").mkdir(exist_ok=True)
        (home / "bin" / "spud").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        (home / "tests").mkdir(exist_ok=True)
        (home / "tests" / "tool").symlink_to(home / "bin" / "spud")
        (home / "tests" / "other.py").write_text("print(1)\n", encoding="utf-8")
        for script in (str(home).upper() + "/BIN/SPUD", "%s/bin/Spud" % home, "%s/bin/ſpud" % home, "%s/tests/tool" % home):
            self.assertRefused("python3.14 -I -S %s --as spud board" % script, "Law 6")
            self.assertRefused("python3.14 -I -S %s ticket new --title x" % script, "Law 6")
        self.assertRefused("%s/tests/tool --as spud board" % home, "Law 6")
        self.assertRefused("%s/bin/ſpud --as spud board" % home, "Law 6")
        self.assertRefused("cd %s && python3.14 -I -S tests/tool init" % home, "Law 6")
        self.assertRefused("cd %s/tests && ./tool --as spud board" % home, "Law 6")
        self.assertAllowed("python3.14 -I -S %s/tests/tool --as %s member log hi" % (home, AGENT_A))
        self.assertSilent("python3.14 -I -S %s/tests/other.py --as spud board" % home)

    def test_cd_before_a_spud_call_keeps_the_allow(self):
        self.assertAllowed("cd %s && %s --as %s member log hi" % (self.home.path, self.spud_cli, AGENT_A))
        self.assertAllowed("cd /tmp; %s board" % self.spud_cli)
        self.assertSilent("cd /tmp && ls")


class SpudAllowIdentityTest(BashHookCase):
    """SPD-032: PreToolUse(Bash) allows a spud call only when the hook can vouch for everything that runs: the ledger root's
    own launcher by file identity, from its own directory (the launcher loads spud_ledger.py beside its real path), under the
    interpreter the hook itself runs on, with exactly -I and -S, no wrapper, no variable assigned anywhere earlier in the line
    but SPUD_HOME naming the root, and no redirection into a file.  A call it recognizes by name but cannot vouch for is still
    refused for Laws 5 and 6 and otherwise stays silent, so the harness's permission rules and prompt decide."""

    def setUp(self):
        super().setUp()
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        self.launcher = self.home.path / "bin" / "spud"
        self.log = "--as %s member log hi" % AGENT_A

    def assertSilentForBoth(self, spelled, cwd=None):
        """`spelled` is a command line with {tail} where the spud arguments go: silent for the lead and for Spud."""
        self.assertSilent(spelled.format(tail=self.log), AGENT_A, cwd)
        self.assertSilent(spelled.format(tail="--as spud board"), None, cwd)

    def assertAllowedForBoth(self, spelled, cwd=None):
        self.assertAllowed(spelled.format(tail=self.log), AGENT_A, cwd)
        self.assertAllowed(spelled.format(tail="--as spud board"), None, cwd)

    def assertStillRefused(self, spelled, cwd=None):
        """Name recognition is unchanged: Law 6 refuses the lead's `--as spud` and `ticket new` however the call is spelled."""
        self.assertRefused(spelled.format(tail="--as spud board"), "Law 6", AGENT_A, cwd)
        self.assertRefused(spelled.format(tail="ticket new --title x"), "Law 6", AGENT_A, cwd)

    def test_the_tickets_evidence_a_script_merely_named_spud_is_silent(self):
        any_dir = self.out / "any"
        any_dir.mkdir()
        (any_dir / "spud").write_text("print('not the ledger')\n", encoding="utf-8")
        copy = self.out / "copy" / "bin"  # a real copy of the launcher and its program: another ledger program, not the root's
        copy.mkdir(parents=True)
        shutil.copyfile(SPUD, copy / "spud")
        shutil.copyfile(PROGRAM, copy / "spud_ledger.py")
        for script in ("/tmp/any/spud", any_dir / "spud", copy / "spud"):
            with self.subTest(script=str(script)):
                self.assertSilentForBoth("python3.14 %s board" % script)
                self.assertSilentForBoth("python3.14 -I -S %s {tail}" % script)
                self.assertStillRefused("python3.14 -I -S %s {tail}" % script)
        self.assertAllowedForBoth("python3.14 -I -S %s {tail}" % self.launcher)

    def test_a_hard_link_to_the_launcher_elsewhere_runs_another_program(self):
        """The same file in another directory: the launcher loads spud_ledger.py beside its own real path, so identity of the
        file alone does not fix the program.  A symlink resolves into bin/, so it does."""
        (self.home.path / "tests").mkdir(exist_ok=True)
        for where in (self.out / "spud", self.home.path / "tests" / "spud"):
            try:
                os.link(self.launcher, where)
            except OSError as e:
                self.skipTest("no hard link from %s to %s: %s" % (self.launcher, where, e))
            with self.subTest(where=str(where)):
                self.assertSilentForBoth("python3.14 -I -S %s {tail}" % where)
                self.assertStillRefused("python3.14 -I -S %s {tail}" % where)
        (self.out / "link").symlink_to(self.launcher)
        self.assertAllowedForBoth("python3.14 -I -S %s {tail}" % (self.out / "link"))

    def test_a_worktrees_launcher_is_not_the_ledgers(self):
        """Root only: a worktree's bin/spud_ledger.py is a member's deliverable on a code ticket (bin/**), so its launcher runs
        code a member may just have written.  The prescribed spelling names the root's launcher, from any directory."""
        wt = self.home.path / ".claude" / "worktrees" / "spd-999-x"
        (wt / "bin").mkdir(parents=True)
        shutil.copyfile(SPUD, wt / "bin" / "spud")
        shutil.copyfile(PROGRAM, wt / "bin" / "spud_ledger.py")
        root = "python3.14 -I -S %s {tail}" % self.launcher
        self.assertAllowedForBoth(root, cwd=str(wt))
        self.assertAllowedForBoth("cd %s && %s" % (wt, root))
        for spelled, cwd in (("python3.14 -I -S bin/spud {tail}", str(wt)), ("python3.14 -I -S %s/bin/spud {tail}" % wt, None),
                             ("cd %s && python3.14 -I -S bin/spud {tail}" % wt, None), ("python3.14 -I -S ./bin/spud {tail}", str(wt))):
            with self.subTest(spelled=spelled, cwd=cwd):
                self.assertSilentForBoth(spelled, cwd)
                self.assertStillRefused(spelled, cwd)

    def test_the_interpreter_must_be_the_one_the_hook_runs_on(self):
        """The same file in the same directory as the hook's own interpreter (a symlink elsewhere is a file anyone who can
        write that directory swaps, and its pyvenv.cfg moves sys.prefix).  A bare name is looked up on the hook's PATH, which
        the Bash tool shares, and a PATH the line changes, or a relative PATH entry ahead of the match, cannot be vouched for
        -- and since SPD-062 a PATH the line changes, or a `hash` it sets, refuses a member outright rather than only
        withholding the vouch."""
        exe = sys.executable
        launcher = self.launcher
        self.assertAllowedForBoth("python3.14 -I -S %s {tail}" % launcher)
        self.assertAllowedForBoth("%s -I -S %s {tail}" % (exe, launcher))
        links = self.out / "links"
        links.mkdir()
        (links / "python3.14").symlink_to(exe)
        other = self.out / "other"
        other.mkdir()
        (other / "python3.14").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (other / "python3.14").chmod(0o755)
        for interpreter, cwd in (("%s/python3.14" % links, None), ("./python3.14", str(links)), ("links/python3.14", str(self.out)),
                                 ("%s/python3.14" % other, None), ("cd %s && ./python3.14" % other, None)):
            with self.subTest(interpreter=interpreter, cwd=cwd):
                spelled = "%s -I -S %s {tail}" % (interpreter, launcher)
                self.assertSilentForBoth(spelled, cwd)
                self.assertStillRefused(spelled, cwd)
        # SPD-062: a PATH the line changes, and a name it hashes, no longer merely withhold the vouch -- the interpreter the
        # hook read by name is not the one the shell would run, so a member is refused outright.  Spud, whom Law 7 does not
        # bind, still sees the unvouched call and stays silent.
        for prefix in ("PATH=%s:$PATH " % other, "PATH=%s; " % other, "export PATH=%s:$PATH; " % other, "path=(%s $path); " % other,
                       "hash -p %s/python3.14 python3.14; " % other):
            with self.subTest(prefix=prefix):
                spelled = "%spython3.14 -I -S %s {tail}" % (prefix, launcher)
                self.assertRefused(spelled.format(tail=self.log), "Law 7", AGENT_A)
                self.assertSilent(spelled.format(tail="--as spud board"), None)
        # an alias reaches no command word outside `eval` (SPD-059), so it withholds the vouch and nothing more
        self.assertSilentForBoth("alias python3.14=%s/python3.14; python3.14 -I -S %s {tail}" % (other, launcher))
        # SPD-084: the line defines a function python3.14, so the later call runs the function, not the interpreter the hook
        # read; the function shadow refuses the lead outright (its body's "$@", words python reads by name, would too --
        # SPD-043).  Spud's call stays silent.  Neither is allowed.
        spelled = "python3.14() {{ %s/python3.14 \"$@\"; }}; python3.14 -I -S %s {tail}" % (other, launcher)
        self.assertRefused(spelled.format(tail=self.log), "shell function", AGENT_A)
        self.assertSilent(spelled.format(tail="--as spud board"), None)
        path = self.home.env["PATH"]
        try:
            for changed in (".:" + path, ":" + path, "%s:%s" % (links, path), "%s:%s" % (other, path)):
                with self.subTest(hook_path=changed.split(":")[0]):
                    self.home.env["PATH"] = changed
                    self.assertSilentForBoth("python3.14 -I -S %s {tail}" % launcher)
                    self.assertAllowedForBoth("%s -I -S %s {tail}" % (exe, launcher))
        finally:
            self.home.env["PATH"] = path

    def test_exactly_dash_I_and_dash_S(self):
        """-I keeps PYTHONPATH and the user site out (probed: a PYTHONPATH json.py runs under -S alone, a PYTHONUSERBASE .pth
        under neither flag); -S keeps site-packages .pth files out; -X pycache_prefix reads bytecode from anywhere (probed: an
        unchecked-hash pyc of json runs under -I -S).  Any other option is not the prescribed call, and -c, -m and - run other code."""
        launcher = self.launcher
        for opts in ("-I -S", "-IS", "-SI", "-S -I"):
            self.assertAllowedForBoth("python3.14 %s %s {tail}" % (opts, launcher))
        for opts in ("", "-I", "-S", "-E -s -S", "-I -S -X pycache_prefix=%s" % self.out, "-I -S -Xpycache_prefix=%s" % self.out,
                     "-I -S -i", "-I -S -u", "-I -S -W error", "-I -S -B", "-I -S -X importtime", "-I -S --"):
            with self.subTest(opts=opts):
                spelled = "python3.14 %s %s {tail}" % (opts, launcher)
                self.assertSilentForBoth(spelled)
                self.assertStillRefused(spelled)
        self.assertSilentForBoth("python3.14 -I -S -c 'import runpy' %s {tail}" % launcher)
        self.assertSilentForBoth("python3.14 -I -S -m spud_ledger {tail}")
        self.assertSilentForBoth("python3.14 -I -S - %s {tail} < %s" % (launcher, launcher))

    def test_the_launcher_alone_runs_without_dash_I_or_dash_S(self):
        """The #! line runs /opt/homebrew/bin/python3.14 with no flags, so the environment's PYTHONPATH and the site .pth files
        load code before the program (probed).  Silent, and since SPD-038 settings sync writes no native allow rule for that
        spelling, so the harness prompts."""
        for spelled in ("%s {tail}" % self.launcher, "cd %s && bin/spud {tail}" % self.home.path, "spud {tail}"):
            with self.subTest(spelled=spelled):
                self.assertSilentForBoth(spelled)
                self.assertStillRefused(spelled)

    def test_no_wrapper_and_no_assignment_before_the_call(self):
        """An assignment reaches the interpreter's environment when it prefixes the call, and when it assigns a variable
        the shell already exports (probed: DYLD_INSERT_LIBRARIES loads a dylib under -I -S).  Wrappers change the
        environment, argv[0] or the user."""
        launcher = self.launcher
        for prefix in ("PYTHONPATH=%s " % self.out, "DYLD_INSERT_LIBRARIES=%s/x.dylib " % self.out, "FOO=1 ", "FOO=1; ", "FOO=1 && ",
                       "export FOO=1; ", "env ", "env -i ", "env PYTHONPATH=%s " % self.out, "ENV ", "command ", "exec ", "exec -a x ",
                       "nohup ", "time ", "nice ", "noglob ", "sudo ", "xargs ", "- "):
            with self.subTest(prefix=prefix):
                spelled = "%spython3.14 -I -S %s {tail}" % (prefix, launcher)
                self.assertSilentForBoth(spelled)
                self.assertStillRefused(spelled)
        self.assertAllowedForBoth("python3.14 -I -S %s {tail}; FOO=1" % launcher)  # an assignment after the call reaches nothing it runs

    def test_spud_home_must_name_the_ledger_root(self):
        """The launcher caches its program's bytecode under <SPUD_HOME>/.spud/pycache when that directory exists, and loads
        it from there (probed: an unchecked-hash pyc in another home's state directory runs under -I -S); the CLI would also
        write another ledger.  Not refused: members probe scratch homes, and the prompt decides."""
        home = self.home.path
        launcher = self.launcher
        self.assertAllowedForBoth("SPUD_HOME=%s python3.14 -I -S %s {tail}" % (home, launcher))
        self.assertAllowedForBoth("SPUD_HOME=%s/ python3.14 -I -S %s {tail}" % (home, launcher))
        if case_insensitive_fs(home):
            self.assertAllowedForBoth("SPUD_HOME=%s python3.14 -I -S %s {tail}" % (str(home).swapcase(), launcher))
        for value in (self.out, "%s/elsewhere" % home, "$HOME/x", "", "'%s'x" % home, "~"):
            with self.subTest(value=str(value)):
                spelled = "SPUD_HOME=%s python3.14 -I -S %s {tail}" % (value, launcher)
                self.assertSilentForBoth(spelled)
                self.assertStillRefused(spelled)
                self.assertSilentForBoth("SPUD_HOME=%s; python3.14 -I -S %s {tail}" % (value, launcher))

    def test_relative_and_indirect_spellings_by_identity(self):
        """A relative launcher is the root's from every directory the shell may be in (SPD-030's candidates), or silent."""
        home = self.home.path
        (home / "tests").mkdir(exist_ok=True)
        (home / "tests" / "tool").symlink_to(self.launcher)
        for spelled, cwd in (("python3.14 -I -S bin/spud {tail}", str(home)), ("python3.14 -I -S ./bin/spud {tail}", str(home)),
                             ("cd %s && python3.14 -I -S bin/spud {tail}" % home, None), ("cd %s/tests && python3.14 -I -S ../bin/spud {tail}" % home, None),
                             ("pushd %s && python3.14 -I -S bin/spud {tail}" % home, None), ("python3.14 -I -S tests/tool {tail}", str(home)),
                             ("python3.14 -I -S %s/tests/../bin/spud {tail}" % home, None)):
            with self.subTest(spelled=spelled, cwd=cwd):
                self.assertAllowedForBoth(spelled, cwd)
        if case_insensitive_fs(home):
            self.assertAllowedForBoth("python3.14 -I -S %s/BIN/SPUD {tail}" % str(home).upper())
            self.assertAllowedForBoth("cd %s && python3.14 -I -S Bin/Spud {tail}" % str(home).swapcase())
        (self.out / "bin").mkdir()
        (self.out / "bin" / "spud").write_text("print('not the ledger')\n", encoding="utf-8")
        for spelled, cwd in (("python3.14 -I -S bin/spud {tail}", str(self.out)), ("cd %s && python3.14 -I -S bin/spud {tail}" % self.out, None),
                             ("cd $X && python3.14 -I -S bin/spud {tail}", None), ("cd %s/not-yet; python3.14 -I -S bin/spud {tail}" % home, None),
                             ("true | cd %s; python3.14 -I -S bin/spud {tail}" % self.out, str(home)),
                             ("command cd %s; python3.14 -I -S bin/spud {tail}" % self.out, str(home))):
            with self.subTest(spelled=spelled, cwd=cwd):
                self.assertSilentForBoth(spelled, cwd)
                self.assertStillRefused(spelled, cwd)

    def test_an_allowed_call_redirects_into_no_file(self):
        """The allow skips the prompt a write outside the repository would get; a spud call's output written into a file (a
        shell profile, a .pth file) is not the prescribed call.  Descriptor duplication and /dev/null stay allowed."""
        cli = "python3.14 -I -S %s" % self.launcher
        for tail in (" > /dev/null", " 2>&1", " 2>/dev/null", " >&2", " < %s" % self.launcher, " &>/dev/null"):
            with self.subTest(tail=tail):
                self.assertAllowedForBoth(cli + " {tail}" + tail)
        for tail in (" > %s/board.txt" % self.out, " >> %s/board.txt" % self.out, " 2> %s/err.txt" % self.out, " &> %s/all.txt" % self.out,
                     " >| %s/x" % self.out):
            with self.subTest(tail=tail):
                self.assertSilentForBoth(cli + " {tail}" + tail)
        # A shell profile or .pth file in Eric's home: not allowed, and since SPD-064 refused to a member outright.
        self.assertRefused(cli + " board > ~/x.pth", "outside every registered project")
        self.assertSilent(cli + " board > ~/x.pth", agent_id=None)
        self.assertRefused(cli + " board > ledger/tickets/SPD-001.md", "generated")

    def test_every_prescribed_spelling_stays_allowed(self):
        """CLAUDE.md's `spud` and the brief template's: Spud's and a member's, --json, @- heredocs, @file, a worktree's cwd."""
        wt = self.home.path / ".claude" / "worktrees" / "spd-999-x"
        wt.mkdir(parents=True)
        cli = "python3.14 -I -S %s" % self.launcher
        lead = self.lead["ref"]
        for cmd, cwd in (
            ("%s --as %s member log 'Read the brief; probed -I'" % (cli, AGENT_A), None),
            ("%s --as %s member show %s" % (cli, AGENT_A, lead), None),
            ("%s --json --as %s member show %s" % (cli, AGENT_A, lead), None),
            ("%s --as %s --json member show %s" % (cli, AGENT_A, lead), None),
            ("%s --as %s member result @- <<'EOF'\nProduced x; the suite is green.\nEOF" % (cli, AGENT_A), None),
            ("%s --as %s member block \"which one?\"" % (cli, AGENT_A), None),
            ("%s --as %s proposal file --title x --why y --evidence z --priority P2" % (cli, AGENT_A), None),
            ("%s --as %s member new --persona scout --model haiku --brief @- --deliverable 'tests/x/**' <<'EOF'\nLook it up.\nEOF" % (cli, AGENT_A), None),
            ("cd %s && %s --as %s member log hi" % (wt, cli, AGENT_A), None),
            ("%s --as %s member log hi" % (cli, AGENT_A), str(wt)),
            ("%s -I -S %s --as %s member log hi" % (sys.executable, self.launcher, AGENT_A), str(wt)),
        ):
            with self.subTest(cmd=cmd, cwd=cwd):
                self.assertAllowed(cmd, AGENT_A, cwd)
        for cmd, cwd in (
            ("%s --as spud ticket new --title x --priority P1 --status active --brief @- --sizing s <<'EOF'\nBrief.\nEOF" % cli, None),
            ("%s --as spud member new --ticket SPD-001 --persona engineer --model opus --brief @brief.md --deliverable 'bin/**'" % cli, None),
            ("%s --as spud member finish %s --status done --outcome x --summary y --next z" % (cli, lead), None),
            ("%s --as spud proposal decide 1 --decision create --priority P2" % cli, None),
            ("%s --as spud render" % cli, None),
            ("%s board --brief" % cli, str(wt)),
            ("%s card SPD-001" % cli, None),
            ("%s events --ticket SPD-001 --limit 50" % cli, None),
            ("cd %s && %s --as spud render" % (self.home.path, cli), str(wt)),
            ("%s -I -S %s --as spud board" % (sys.executable, self.launcher), None),
        ):
            with self.subTest(cmd=cmd, cwd=cwd):
                self.assertAllowed(cmd, None, cwd)


# The wrappers the Bash hook strips before it names a command, as bin/spud_ledger.py WRAPPERS holds them since SPD-030
# (zsh's noglob and nocorrect precommand modifiers added), and the arguments each needs before its command.
SHELL_WRAPPERS = ("builtin", "caffeinate", "chronic", "command", "doas", "env", "exec", "ionice", "nice", "nocorrect", "noglob",
                  "nohup", "script", "setsid", "stdbuf", "sudo", "time", "timeout", "unbuffer", "xargs")
WRAPPER_ARGS = {"timeout": "5 ", "script": "-q /dev/null "}


def spellings(word):
    """A word as the shell sees it and as a case-insensitive PATH lookup finds it (SPD-030): lower, upper, mixed."""
    return (word, word.upper(), "".join(c.upper() if i % 2 == 0 else c for i, c in enumerate(word)))


class ShellModelTest(BashHookCase):
    """SPD-030: PreToolUse(Bash) reads wrappers and the working directory as zsh 5.9 and bash 3.2 do (the Bash tool runs the
    user's shell: zsh on this Mac, bash elsewhere), erring toward refusal where the two could disagree.  macOS PATH lookup is
    case-insensitive, so ENV and NOHUP are env and nohup, and CD, /usr/bin/cd and a cd behind an external wrapper run
    /usr/bin/cd in their own process: the shell's directory stays.  A directory the shell may or may not be in is checked
    too; one the hook cannot know refuses a member's relative redirection and leaves Spud's unchecked.  AGENT_A plans
    tests/** and bin/spud; AGENT_C plans **; note.txt is refused to AGENT_A at the home and silent outside it."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        home = self.home.path
        for d in ("ledger/tickets", "docs/inner", "tests/zzone", "bin"):
            (home / d).mkdir(parents=True, exist_ok=True)

    def test_the_tickets_evidence_commands_are_refused(self):
        home = self.home.path
        redirections = ("CD /tmp && echo x > ledger/tickets/SPD-001.md", "/usr/bin/cd /tmp && echo x > ledger/tickets/SPD-001.md",
                        "pushd %s/ledger && echo x > tickets/SPD-001.md" % home)
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(agent_id=agent_id):
                for cmd in redirections:
                    self.assertRefused(cmd, "generated", agent_id)
                self.assertRefused("ENV git commit -m x", "Law 7", agent_id)
                self.assertRefused("NOHUP git push", "Law 7", agent_id)
        for cmd in redirections:
            self.assertRefused(cmd, "Law 1", agent_id=None)

    def test_every_wrapper_in_any_case_before_a_git_write(self):
        for name in SHELL_WRAPPERS:
            for spelled in spellings(name) + ("/usr/bin/" + name.upper(),):
                with self.subTest(spelled):
                    self.assertRefused("%s %sgit push" % (spelled, WRAPPER_ARGS.get(name, "")), "Law 7")
        self.assertRefused("NOHUP nice -n 5 ENV -u X git commit -m x", "Law 7")
        for ok in ("ENV FOO=1 ls", "NOHUP git status", "TIMEOUT 5 git log", "Env -u X git diff", "COMMAND -v git"):
            self.assertSilent(ok)

    def test_a_wrappers_value_options_do_not_hide_its_command(self):
        for cmd in ("timeout -s KILL 5 git push", "timeout --signal KILL 5 git push", "TIMEOUT -k 1 5 git push", "env -u HOME git push",
                    "env -C /tmp git push", "ENV -S 'git push'", "env --split-string='git push'", "exec -a name git push", "nice -n 5 git push",
                    "NICE -5 git push", "ionice -c 3 git push", "caffeinate -t 5 git push", "Caffeinate -w 1 git push", "xargs -J % git push",
                    "xargs -I {} git push", "XARGS -n 1 git push", "stdbuf -o 0 git push", "stdbuf -oL git push", "sudo -g staff git push",
                    "sudo -Eu root git push", "sudo --user root git push", "doas -u root git push", "script -q /dev/null git push",
                    "script -c 'git push' typescript", "time -o /dev/null git push", "- git push"):
            with self.subTest(cmd):
                self.assertRefused(cmd, "Law 7")

    def test_only_the_builtin_cd_moves_the_directory(self):
        out = self.out
        for what, dir_command in (("builtin", "cd %s"), ("builtin keyword", "builtin cd %s"), ("assignment prefix", "FOO=1 cd %s"),
                                  ("negated", "! cd %s"), ("brace group", "{ cd %s; }"), ("time reserved word", "time cd %s"),
                                  ("--", "cd -- %s"), ("-L", "cd -L %s"), ("-P", "cd -P %s"), ("-LP", "cd -LP %s"),
                                  ("pushd", "pushd %s"), ("pushd --", "pushd -- %s"), ("eval", "eval 'cd %s'")):
            with self.subTest("followed: " + what):
                self.assertSilent((dir_command % out) + " && echo x > note.txt")
        self.assertSilent("cd %s; echo x > note.txt" % out)
        self.assertSilent("arr=(); cd %s; echo x > note.txt" % out)  # an empty array, not a function header
        self.assertSilent("cd %s\necho x > note.txt" % out)
        self.assertSilent("(cd %s && echo x > note.txt)" % out)
        for what, dir_command in (("upper case", "CD %s"), ("mixed case", "Cd %s"), ("cD", "cD %s"), ("/usr/bin/cd", "/usr/bin/cd %s"),
                                  ("/usr/bin/CD", "/usr/bin/CD %s"), ("env", "env cd %s"), ("ENV", "ENV cd %s"), ("nohup", "nohup cd %s"),
                                  ("nice", "nice cd %s"), ("timeout", "timeout 5 cd %s"), ("sudo", "sudo cd %s"), ("xargs", "xargs cd %s"),
                                  ("COMMAND runs /usr/bin/COMMAND", "COMMAND cd %s"), ("BUILTIN is not found", "BUILTIN cd %s"),
                                  ("TIME runs /usr/bin/time", "TIME cd %s"), ("exec", "exec cd %s"), ("coproc", "coproc cd %s"),
                                  ("subshell", "(cd %s)"), ("command substitution", "echo $(cd %s)"), ("backticks", "echo `cd %s`"),
                                  ("process substitution", "cat <(cd %s)"), ("sh -c", "sh -c 'cd %s'"), ("bash -c", "bash -c \"cd %s\""),
                                  ("first pipeline element", "cd %s | cat"), ("group as pipeline element", "{ cd %s; } | cat"),
                                  ("background", "cd %s &")):
            with self.subTest("not moved: " + what):
                sep = " " if what == "background" else "; "
                self.assertRefused((dir_command % out) + sep + "echo x > note.txt", "deliverables")
        self.assertRefused("cd %s > note.txt" % out, "deliverables")
        self.assertRefused("{ cd %s; } > note.txt" % out, "deliverables")
        for what, dir_command in (("command runs the external in zsh", "command cd %s"), ("command -p", "command -p cd %s"),
                                  ("noglob is zsh's", "noglob cd %s"), ("nocorrect is zsh's", "nocorrect cd %s"), ("zsh's - precommand", "- cd %s"),
                                  ("time -p: zsh runs -p", "time -p cd %s"), ("chdir is zsh's", "chdir %s"),
                                  ("last pipeline element: zsh's shell, bash's subshell", "true | cd %s"),
                                  ("builtin command", "builtin command cd %s"), ("command builtin", "command builtin cd %s")):
            with self.subTest("one shell moves: " + what):
                self.assertRefused((dir_command % out) + " && echo x > note.txt", "deliverables")
        self.assertRefused("true | cd %s; echo x > ledger/tickets/SPD-001.md" % out, "Law 1", agent_id=None)
        self.assertSilent("cd %s && echo x > ledger/tickets/SPD-001.md" % out, agent_id=None)
        self.assertSilent("CD %s && echo x > CLAUDE.md" % out, agent_id=None)

    def test_a_cd_that_may_not_run_or_may_fail_leaves_either_directory(self):
        out = self.out
        for cmd in ("true && cd %s; echo x > note.txt", "false || cd %s; echo x > note.txt", "if true; then cd %s; fi; echo x > note.txt",
                    "if false; then true; else cd %s; fi; echo x > note.txt", "case x in x) cd %s;; esac; echo x > note.txt",
                    "case x in\n  x) cd %s ;;\nesac\necho x > note.txt", "while false; do cd %s; done; echo x > note.txt",
                    "f() { cd %s; }; f; echo x > note.txt", "function f { cd %s; }; echo x > note.txt", "cd %s/nonexistent; echo x > note.txt",
                    "cd %s/nonexistent || echo x > note.txt", "case x in (x) cd %s;; esac; echo x > note.txt"):
            with self.subTest(cmd):
                self.assertRefused(cmd % out, "deliverables")
        ledger = self.home.path / "ledger"
        for cmd in ("if true; then cd %s; else cd %s; fi; echo x > tickets/SPD-001.md" % (ledger, out),
                    "if false; then cd %s; elif true; then cd %s; else cd %s; fi; echo x > tickets/SPD-001.md" % (out, ledger, out),
                    "case x in a) cd %s;; b) cd %s;; esac; echo x > tickets/SPD-001.md" % (ledger, out)):
            with self.subTest("every branch's directory: " + cmd):
                self.assertRefused(cmd, "generated", AGENT_C)  # ** : only the ledger branch's target is refused
        self.assertRefused("case x in (x) git push;; esac", "Law 7")
        for cmd in ("true && cd %s && echo x > note.txt", "if true; then cd %s; echo x > note.txt; fi", "mkdir -p %s/new && cd %s/new && echo x > note.txt",
                    "cd %s/nonexistent && echo x > note.txt", "cd %s || exit 1; echo x > note.txt"):
            with self.subTest(cmd):
                self.assertSilent(cmd.replace("%s", str(out)))

    def test_directories_the_hook_cannot_follow(self):
        """Refused for a member's relative redirection or tee target, and since SPD-035 for Spud's too: Laws 1 and 5 held
        for nobody after `popd`, `cd -`, a sourced file or a relative cd in a loop, because the hook could not say which
        directory the target landed in.  Eric's call, made on SPD-035: refuse Spud with the reason members get, his own
        files being few.  An absolute target is checked for both, as it was."""
        out = self.out
        for dir_command in ("popd", "popd -n", "pushd", "pushd +1", "pushd -1", "pushd -q %s", "pushd -n %s", "cd -q %s", "cd -s %s", "cd -e %s",
                            "cd -@ %s", "cd -x %s", "cd +1", "cd -1", "cd -", "cd ~-", "cd ~root", "cd ~nosuchuser-spd-030", "cd $DIR",
                            "cd %s/*", "cd /{tmp,var}", "cd a b c", "source x.sh", ". x.sh", "for d in a b; do cd ..; done",
                            "CDPATH=$HOME; cd sub"):
            command = (dir_command.replace("%s", str(out))) + "; echo x > note.txt"
            with self.subTest(command):
                self.assertRefused(command, "cannot follow")
                self.assertRefused(command.replace("echo x > note.txt", "echo x | tee note.txt"), "cannot follow")
                self.assertRefused(command, "cannot follow", agent_id=None)
                self.assertRefused(command.replace("echo x > note.txt", "echo x | tee note.txt"), "cannot follow", agent_id=None)
                self.assertRefused(command.replace("note.txt", "ledger/tickets/SPD-001.md"), "cannot follow", agent_id=None)
        home = self.home.path
        self.assertRefused("popd; echo x > %s/docs/x.md" % home, "deliverables")
        self.assertRefused("popd; echo x > %s/ledger/tickets/SPD-001.md" % home, "Law 1", agent_id=None)
        self.assertRefused("echo x > ~-/note.txt", "cannot follow")
        self.assertRefused("echo x > ~root/note.txt", "cannot follow")
        self.assertAllowed("pushd %s && %s --as %s member log hi" % (home, self.spud_cli, AGENT_A))
        self.assertSilent("CD %s && %s --as %s member log hi" % (home, self.spud_cli, AGENT_A))

    def test_cd_physical_and_tilde_targets(self):
        home, out = self.home.path, self.out
        (home / "tests" / "link").symlink_to(home / "docs" / "inner")
        self.assertRefused("cd -P tests/link/.. && echo x > note.txt", "deliverables")  # the kernel's parent of docs/inner
        self.assertSilent("cd -L tests/link/.. && echo x > note.txt")  # tests, lexically
        self.assertSilent("cd tests/link/.. && echo x > note.txt")
        self.assertRefused("cd %s/docs && echo x > ~+/note.txt" % home, "deliverables")
        self.assertRefused("cd ~+/docs && echo x > note.txt", "deliverables")
        self.assertSilent("cd %s && cd ~+/tests && echo x > note.txt" % home)
        # ~ is HOME, so this writes no ledger file; since SPD-064 a member is refused there all the same, for being outside
        # every registered project, while Spud writes his own home as before.
        self.assertRefused("cd ~ && echo x > ledger/tickets/SPD-001.md", "outside every registered project")
        self.assertSilent("cd ~ && echo x > ledger/tickets/SPD-001.md", agent_id=None)
        self.assertRefused("cd %s && echo x > ~+/ledger/tickets/SPD-001.md" % home, "Law 1", agent_id=None)

    def test_cdpath_sends_a_relative_cd_elsewhere(self):
        """bash tries CDPATH before the current directory, zsh after it; a target starting with / ./ or ../ skips it."""
        home, out = self.home.path, str(self.out)
        # The non-matching CDPATH entry is under the scratch root: since SPD-064 a member's redirection into a directory
        # outside every registered project is refused, and this control is about which directory the cd lands in.
        for setting in ("CDPATH=%s cd ledger", "CDPATH=%s; cd ledger", "export CDPATH=%s; cd ledger", "cdpath=(%s); cd ledger",
                        "CDPATH=" + out + "/nowhere:%s; cd ledger"):
            with self.subTest(setting):
                self.assertRefused((setting % home) + " && echo x > tickets/SPD-001.md", "generated", AGENT_C, cwd=out)
        self.assertSilent("CDPATH=%s; cd ./ledger && echo x > tickets/SPD-001.md" % home, AGENT_C, cwd=out)
        self.assertRefused("CDPATH=%s; cd ledger && echo x > tickets/SPD-001.md" % home, "Law 1", agent_id=None, cwd=out)
        self.home.env["CDPATH"] = str(home)
        try:
            self.assertRefused("cd ledger && echo x > tickets/SPD-001.md", "generated", AGENT_C, cwd=out)
            self.assertSilent("cd /tmp && echo x > tickets/SPD-001.md", AGENT_C, cwd=out)
        finally:
            del self.home.env["CDPATH"]

    def test_zsh_two_argument_cd(self):
        """zsh replaces the first occurrence of the first argument in the current directory with the second; bash 3.2 takes the first."""
        home = self.home.path
        self.assertRefused("cd %s/tests/zzone && cd tests/zzone docs && echo x > note.txt" % home, "deliverables")
        self.assertRefused("cd %s/tests/zzone && cd zzone ../docs && echo x > note.txt" % home, "deliverables")

    def test_newlines_comments_and_joined_operators(self):
        out, home = self.out, self.home.path
        for cmd in ("ls\ngit push", "ls # don't\ngit push", "ls \\\n&& git push", "cat <(git push)", "tee >(git commit -m x) < /dev/null",
                    "echo ok;\n\ngit push", "for f in a; do\n  git add $f\ndone"):
            with self.subTest(cmd):
                self.assertRefused(cmd, "Law 7")
        self.assertRefused("(true)>ledger/tickets/SPD-001.md", "generated")
        self.assertRefused("(true)>ledger/tickets/SPD-001.md", "Law 1", agent_id=None)
        self.assertRefused("(cd %s)&&echo x > note.txt" % out, "deliverables")
        self.assertRefused("cd %s/docs\necho x > note.txt" % home, "deliverables")
        for ok in ("git status # before git push", "echo 'a\ngit push'", "echo a \\\ngit push", "x=$((1 + 2)); echo $x", "ls *(.)"):
            with self.subTest(ok):
                self.assertSilent(ok)


# zsh's short loop forms (SPD-042): a header, and the body that may follow it with no `do`.  Each fills its `%s`.
SHORT_HEADERS = ("repeat 2 %s", "repeat 2; %s", "repeat 2\n%s", "repeat 2 ;\n; %s", "n=2; repeat $n %s", "repeat $(echo 2) %s",
                 "repeat 1+1 %s", "for f (a b) %s", "for f (a b); %s", "for f (a b)\n%s", "for f (*.py) %s", "for f () %s",
                 "for f in a b; %s", "for f in a b\n%s", "for f; %s", "select f (a b) %s", "select f in a b; %s",
                 "for (( i=0; i<2; i++ )) %s", "for (( i=0; i<2; i++ )); %s")
SHORT_BODIES = ("%s", "{ %s }", "{ %s; }", "( %s )", "do %s; done", "if true; then %s; fi", "case x in x) %s;; esac",
                "true && %s", "false || %s", "%s | cat")


class ZshShortLoopTest(BashHookCase):
    """SPD-042: zsh's SHORT_LOOPS, on under its default options, run a loop body with no `do` and `done`, and the hook read
    that body as header words and discarded it, so a git write, a spud call, a tee, a cd or a redirection there was never
    checked (Laws 1, 5, 6 and 7).  Probed in zsh 5.9 -f and in zsh -f -o nobareglobqual (this Mac's Bash tool), with bash 3.2
    as the control that rejects every one of these forms, in a scratchpad directory with a fake git first on a scratch PATH:

    - the header is `repeat word` (one word, arithmetic: `repeat 1+1` ran twice), `for name ( word ... )`,
      `for name in word ... TERM`, `for name TERM` (the positional parameters), `select` in both spellings, or `for (( ... ))`;
      TERM is `;` or a newline, and more than one may stand between a header and its body (`repeat 2 ;\\n;\\n git push` pushed
      twice).  `for f in a b do ...` is a parse error, so the `in` form always has a terminator before its body;
    - the body is a `do ... done`, a `{ list }`, a `( list )` subshell, or one sublist: a whole and-or list with its pipelines
      (`repeat 3 true && git push` pushed three times; `repeat 2 echo a | wc -l` printed 1 twice, so the pipeline is inside),
      a compound command (`repeat 2 if true; then git push; fi` pushed twice) and the redirections of each, ending at `;`, a
      newline or `&` (`repeat 2 echo a >> f1; echo b >> f2` left two lines in f1 and one in f2; `repeat 2 git push & wait`
      pushed twice, so `&` ends the body and backgrounds the loop);
    - the loop runs in the shell itself, so a cd in a sublist or a `{ }` body moves it and may repeat (`repeat 1 cd d; pwd`
      ended in d), while a `( )` body's does not (`repeat 1 (cd d); pwd` did not), and a cd after the sublist is outside the
      loop (`repeat 1 true; cd d; pwd` ended in d);
    - `for f (a|b)` is a parse error, so a loop's word list is never read as a glob, and a command follows it and a repeat
      count in command position (`for f (a b) (git push)` and `repeat 1 (git push)` open subshells).

    AGENT_A plans tests/** and bin/spud; AGENT_C plans **; note.txt is refused to AGENT_A at the home."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/zzone", "bin"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")

    # -- payloads the body may hold -----------------------------------------------------
    def member_payloads(self):
        """(command, the reason a member is refused for it): Law 7, Law 6, Law 5's --as, the database, and Law 1 through a
        redirection and through tee."""
        home, spud = self.home.path, self.spud_cli
        return (("git push", "Law 7"),
                ("%s ticket new --title x" % spud, "Law 6"),
                ("%s --as spud member log hi" % spud, "Law 6"),
                ("%s --as %s member log hi" % (spud, AGENT_B), "--as"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "generated"),
                ("echo x | tee ledger/tickets/SPD-001.md", "generated"))

    def spud_payloads(self):
        """Spud is never refused for git; these are the checks that do apply to him."""
        home, spud = self.home.path, self.spud_cli
        return (("%s --as %s member log hi" % (spud, AGENT_A), "--as"),
                ("%s hook PreToolUse" % spud, "hook"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "Law 1"),
                ("echo x | tee ledger/tickets/SPD-001.md", "Law 1"))

    def refused_everywhere(self, line, member_needle, spud_needle):
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(line=line, agent_id=agent_id):
                self.assertRefused(line, member_needle, agent_id)
        with self.subTest(line=line, agent_id="spud"):
            self.assertRefused(line, spud_needle, agent_id=None)

    # -- the hole ------------------------------------------------------------------------
    def test_the_tickets_evidence_commands_are_refused(self):
        """The three lines from the ticket: each wrote ledger/tickets/SPD-001.md in zsh and the hook recorded no redirect."""
        for line in ("repeat 3 echo x > ledger/tickets/SPD-001.md",
                     "repeat 1 (echo x > ledger/tickets/SPD-001.md)",
                     "for f (a b) echo x > ledger/tickets/SPD-001.md"):
            self.refused_everywhere(line, "generated", "Law 1")

    def test_every_header_form_reaches_its_body(self):
        for header in SHORT_HEADERS:
            with self.subTest(header=header):
                self.assertRefused(header % "git push", "Law 7")
                self.assertRefused(header % "git push", "Law 7", AGENT_C)

    def test_every_body_form_reaches_its_commands(self):
        for header in ("repeat 2 %s", "for f (a b) %s", "for f in a b; %s", "select f (a b) %s", "for (( i=0; i<2; i++ )) %s"):
            for body in SHORT_BODIES:
                with self.subTest(header=header, body=body):
                    self.assertRefused(header % (body % "git push"), "Law 7")

    def test_every_payload_in_every_body_for_every_caller(self):
        for header in ("repeat 2 %s", "for f (a b) %s", "for f in a b; %s"):
            for body in ("%s", "{ %s }", "( %s )", "do %s; done"):
                for command, needle in self.member_payloads():
                    line = header % (body % command)
                    for agent_id in (AGENT_C, AGENT_A):
                        with self.subTest(line=line, agent_id=agent_id):
                            self.assertRefused(line, needle, agent_id)
                for command, needle in self.spud_payloads():
                    line = header % (body % command)
                    with self.subTest(line=line, agent_id="spud"):
                        self.assertRefused(line, needle, agent_id=None)

    def test_a_target_outside_a_narrow_members_deliverables(self):
        """AGENT_A plans tests/** and bin/spud, so note.txt at the home is refused it and allowed the ** member."""
        for header in ("repeat 2 %s", "for f (a b) %s", "for f in a b; %s", "for (( i=0; i<2; i++ )) %s"):
            for body in ("%s", "{ %s }", "( %s )"):
                line = header % (body % "echo x > note.txt")
                with self.subTest(line=line):
                    self.assertRefused(line, "deliverables")
                    self.assertSilent(line, AGENT_C)

    # -- the loop model -------------------------------------------------------------------
    def test_the_loop_model_matches_the_long_form(self):
        """A relative cd in a short body may repeat, so it is unfollowable; an absolute one leaves the union of before and
        after, as pop computes for `do ... done`; a `( )` body's cd does not carry out; a cd after the sublist is outside the
        loop and followed."""
        out, home = self.out, self.home.path
        for header in ("repeat 2 %s", "for f (a b) %s", "for f in a b; %s", "select f (a b) %s", "for (( i=0; i<2; i++ )) %s"):
            for body in ("%s", "{ %s; }", "do %s; done"):
                with self.subTest(header=header, body=body):
                    # a relative cd that may repeat: the hook cannot follow it, so a member's relative target is refused
                    self.assertRefused((header % (body % "cd docs")) + "; echo x > note.txt", "cannot follow")
                    # an absolute cd: both the old directory and the new one are checked
                    self.assertRefused((header % (body % ("cd %s" % out))) + "; echo x > note.txt", "deliverables")
                    self.assertRefused((header % (body % ("cd %s/ledger" % home))) + "; echo x > tickets/SPD-001.md", "generated", AGENT_C)
        for header in ("repeat 2 %s", "for f (a b) %s"):
            with self.subTest("a subshell body does not move the shell: " + header):
                self.assertSilent((header % ("(cd %s)" % out)) + "; echo x > tests/zzone/k.py")
            with self.subTest("after the sublist, the loop is over: " + header):
                self.assertSilent((header % "true") + "; cd %s; echo x > note.txt" % out)
                self.assertRefused((header % "true") + "; cd %s/ledger; echo x > tickets/SPD-001.md" % home, "generated", AGENT_C)

    def test_the_loop_variable_is_doubted(self):
        """`for name (...)` and `for name in ...` assign the name each turn, so a command word built from it is refused."""
        for line in ("for X (git) $X push", "for X in git; $X push", "select X (git) $X push",
                     "X=ls; for X (git) $X push", "for X (git) { $X push }"):
            with self.subTest(line=line):
                self.assertRefused(line, "cannot resolve")
                self.assertRefused(line, "cannot resolve", AGENT_C)

    # -- where a short loop may stand ------------------------------------------------------
    def test_short_loops_nested_and_enclosed(self):
        for line in ("for f in a b; do repeat 1 git push; done",
                     "repeat 2 for f in a; do git push; done",
                     "repeat 2 repeat 2 git push",
                     "for f (a b) for g (c) git push",
                     "repeat 2 { repeat 1 git push }",
                     "{ repeat 1 git push; }",
                     "( repeat 1 git push )",
                     "f() { repeat 1 git push; }; f",
                     "eval 'repeat 1 git push'",
                     "sh -c 'repeat 1 git push'",
                     "zsh -c 'for f (a) git push'",
                     "echo $(repeat 1 git push)",
                     "echo `for f (a) git push`",
                     "coproc repeat 1 git push",
                     "coproc for f (a) git push",
                     "time repeat 1 git push",
                     "! repeat 1 git push",
                     "for f (a b) (git push)",
                     "for f () git push",
                     "repeat 1 true; repeat 1 git push",
                     "repeat 1 git push & wait",
                     "repeat 1 git push | cat",
                     "repeat 1 true && git push",
                     "repeat 1 false || git push",
                     "for f (a b) git push > out 2>&1",
                     "case x in x) repeat 1 git push;; esac",
                     "case x in x) repeat 1 git push;; y) git status;; esac",
                     "if true; then repeat 1 git push; else echo no; fi",
                     "repeat 2 cat <<< 'x'; git push"):
            with self.subTest(line=line):
                self.assertRefused(line, "Law 7")
                self.assertRefused(line, "Law 7", AGENT_C)
        # a short loop closed above a case frame must not swallow the arm's `;;`
        ledger, out = self.home.path / "ledger", self.out
        self.assertRefused("case x in a) repeat 1 cd %s;; b) cd %s;; esac; echo x > tickets/SPD-001.md" % (ledger, out),
                           "generated", AGENT_C)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        """Deep nesting and very long headers stay bounded and still find the write."""
        m = load_spud_module()
        for line in ("repeat 1 " * 2000 + "git push", "repeat 1 { " * 500 + "git push" + " }" * 500,
                     "repeat 1 ( " * 200 + "git push" + " )" * 200, "for f (a) " * 2000 + "git push",
                     "repeat 1 { " * 1000 + "git push", "for f (" + "a " * 5000 + ") git push", "repeat " * 500):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = m.analyse_command(line, m.ShellAnalysis(cwd=str(self.home.path)))
                self.assertLess(time.monotonic() - started, 5.0)
                if "git push" in line:
                    self.assertIn(("git", ("push", "push")), a.findings, line[:40])

    def test_the_sublist_body_ends_where_zsh_ends_it(self):
        """`repeat 2 echo a; echo b` ran echo a twice and echo b once, so what follows the sublist is outside the loop."""
        home = self.home.path
        m = load_spud_module()
        for line, inside, outside in (("repeat 2 echo a > ledger/tickets/SPD-001.md; echo b > tests/zzone/k.py", 1, 1),
                                      ("for f (a b) echo a > ledger/tickets/SPD-001.md; echo b > tests/zzone/k.py", 1, 1)):
            a = m.analyse_command(line, m.ShellAnalysis(cwd=str(home)))
            self.assertEqual(len(a.redirects), inside + outside, (line, a.redirects))
        # both simple commands are checked, and the one outside the loop for its own directory
        self.assertRefused("repeat 2 true; echo x > ledger/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertSilent("repeat 2 true; echo x > tests/zzone/k.py")

    # -- controls ---------------------------------------------------------------------------
    def test_the_long_forms_and_ordinary_words_keep_todays_reading(self):
        home, out = self.home.path, self.out
        for refused in ("for f in a b; do git push; done", "for f in a b\ndo\n  git push\ndone",
                        "for (( i=0; i<3; i++ )); do git push; done", "select f in a b; do git push; done",
                        "while true; do git push; done", "until false; do git push; done", "repeat 3; do git push; done",
                        "foreach f (a b); git push; end", "case x in x) git push;; esac",
                        "f() { git push; }; f", "function f { git push; }",
                        "(git push)", "{ git push; }", "n=0; while (( n++ < 2 )) { git push }"):
            with self.subTest(refused):
                self.assertRefused(refused, "Law 7")
        for ok in ("echo repeat 3 git push", "echo 'repeat 3 git push'", "echo \"for f (a b) git push\"",
                   "git log --grep 'for f (a b) git push'", "grep -n repeat tests/zzone/k.py",
                   "grep -c 'repeat 2 git push' tests/zzone/k.py", "arr=(a b); echo $arr", "arr=(); echo done",
                   "for f in tests/*.py; do echo $f; done", "echo select repeat for", "ls *(.)",
                   "printf 'repeat 1 git push\\n' > tests/zzone/k.py"):
            with self.subTest(ok):
                self.assertSilent(ok)
        # the directory model of the long forms is untouched
        self.assertRefused("for d in a b; do cd ..; done; echo x > note.txt", "cannot follow")
        self.assertSilent("cd %s; echo x > note.txt" % out)
        self.assertSilent("for f in a b; do echo $f; done; cd %s; echo x > note.txt" % out)
        self.assertRefused("for f in a b; do cd %s/ledger; done; echo x > tickets/SPD-001.md" % home, "generated", AGENT_C)

    def test_a_short_loop_is_not_read_into_a_quoted_or_argument_position(self):
        """`repeat`, `for` and `select` outside command position are ordinary words, and a `(` after a loop name is its word
        list, never a glob: neither changes what the hook reads elsewhere."""
        home = self.home.path
        for ok in ("echo for f (a b) done", "echo x | grep -F 'for f (a b) git push'",
                   "%s --as %s member log 'repeat 3 git push'" % (self.spud_cli, AGENT_A),
                   "%s --as %s member log 'for f (a b) git push'" % (self.spud_cli, AGENT_A)):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
        self.assertRefused("echo x > 'ledger/tickets/SPD-001.md'", "generated", AGENT_C)
        self.assertSilent("echo x > %s/tests/zzone/k.py" % home)


class GlobRedirectTest(BashHookCase):
    """SPD-034: a redirection or tee target the shell expands (an unquoted glob character, a brace list) is checked as
    every file it can open from every candidate directory, not as its literal spelling.  bash and zsh both expand a
    glob in a redirection target before opening it, so `echo x > ledg*/tickets/SPD-00?.md` writes ledger/tickets/SPD-001.md
    (probed in zsh 5.9 and bash 3.2).  A quoted or escaped glob character is literal.  A member is refused when the hook
    cannot know what a glob opens (no match now, the cost bound reached); Spud is checked against every match and the
    literal name bash writes when nothing matches.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        home = self.home.path
        for rel in ("ledger/tickets/SPD-001.md", "ledger/tickets/SPD-002.md", "tests/keep.py", "tests/other.py", "docs/x.md"):
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")

    def test_the_tickets_evidence_glob_writes_a_generated_file(self):
        """The hole from SPUD-030/Elba (proposal 30): the hook saw the literal `ledg*/tickets/SPD-00?.md`, under no root."""
        evidence = "echo overwritten > ledg*/tickets/SPD-00?.md"
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(agent_id=agent_id):
                self.assertRefused(evidence, "generated", agent_id)
        self.assertRefused(evidence, "Law 1", agent_id=None)

    def test_each_glob_character_expands(self):
        for target in ("ledger/tickets/SPD-00?.md", "ledger/tickets/SPD-00[12].md", "ledger/tickets/SPD-00[!x].md",
                       "led*/tickets/SPD-001.md", "led?er/tickets/SPD-001.md", "**/SPD-001.md",
                       "ledger/tickets/SPD-00{1,2}.md", "ledger/tickets/SPD-00{1..2}.md", "{ledger,docs}/tickets/SPD-001.md",
                       "ledger/tickets/../tickets/SPD-00?.md"):
            with self.subTest(target):
                self.assertRefused("echo x > %s" % target, "generated", AGENT_C)
                self.assertRefused("echo x > %s" % target, "Law 1", agent_id=None)

    def test_a_quoted_or_escaped_glob_character_is_literal(self):
        # Quoted or escaped: the literal path ledg*/tickets/SPD-001.md is inside the repo but not a generated file, so it is
        # refused for a member as outside the deliverables (not "generated"), never expanded to ledger/tickets/SPD-001.md.
        for target in ("'ledg*'/tickets/SPD-001.md", '"ledg*"/tickets/SPD-001.md', "ledg\\*/tickets/SPD-001.md",
                       "'ledg*/tickets/SPD-001.md'", "ledg\\?r/tickets/SPD-001.md"):
            with self.subTest(target):
                r = self.assertRefused("echo x > %s" % target, "deliverables", AGENT_A)
                self.assertNotIn("generated", r.reason)
        # Partial quoting still expands the unquoted character.
        self.assertRefused("echo x > 'ledg'*/tickets/SPD-001.md", "generated", AGENT_A)

    def test_a_glob_matching_only_deliverables_is_allowed(self):
        self.assertSilent("echo x > tests/*.py")
        self.assertSilent("echo x > tests/keep.p?")
        self.assertSilent("printf x | tee tests/*.py")
        self.assertSilent("echo x > tests/{keep,other}.py")
        # a quoted glob character in a deliverable target is a literal filename under tests/**
        self.assertSilent("echo x > 'tests/star*.py'")

    def test_several_matches_are_all_checked(self):
        # Both SPD-001.md and SPD-002.md match; a member is refused (generated), Spud on Law 1.
        self.assertRefused("echo x > ledger/tickets/SPD-00?.md", "generated", AGENT_C)
        self.assertRefused("echo x >> ledger/tickets/SPD-00?.md", "Law 1", agent_id=None)
        # a glob that matches a deliverable and a generated file is refused on the generated one
        (self.home.path / "tests" / "tickets").mkdir(parents=True, exist_ok=True)
        (self.home.path / "tests" / "tickets" / "SPD-001.md").write_text("x\n", encoding="utf-8")
        self.assertRefused("echo x > */tickets/SPD-001.md", "generated", AGENT_C)

    def test_a_glob_that_matches_nothing_now(self):
        # A member: refused, since the hook cannot know what the shell opens (a match may appear, or bash writes the name).
        for target in ("tests/nomatch-xyzzy*.py", "ledger/tickets/SPD-09?.md", "no-such-dir-*/x"):
            with self.subTest(target):
                self.assertRefused("echo x > %s" % target, "matches no file", AGENT_A)
                self.assertRefused("echo x | tee %s" % target, "matches no file", AGENT_A)
        # Spud: the literal name a shell writes on no match is checked too -- outside the repository it stays unchecked,
        # inside it is refused (bash writes the file named literally, glob characters and all).
        self.assertSilent("echo x > %s/outside-nomatch*.md" % self.out, agent_id=None)
        self.assertRefused("echo x > tests/nomatch-xyzzy*.py", "Law 1", agent_id=None)

    def test_tee_arguments_expand(self):
        self.assertRefused("printf x | tee ledger/tickets/SPD-00?.md", "generated", AGENT_C)
        self.assertRefused("printf x | tee {ledger,docs}/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertRefused("printf x | tee -a ledger/tickets/SPD-00?.md", "Law 1", agent_id=None)
        self.assertRefused("printf x | tee ledger/tickets/SPD-00?.md tests/keep.py", "generated", AGENT_C)
        self.assertSilent("printf x | tee tests/*.py")

    def test_a_variable_in_a_glob_target_is_refused_for_a_member(self):
        # Existing behaviour, pinned: a $ in a target is unresolvable.
        self.assertRefused("X=ledger; echo x > $X/tickets/SPD-00?.md", "spell the path out", AGENT_C)
        self.assertSilent("X=ledger; echo x > $X/tickets/SPD-001.md", agent_id=None)

    def test_tilde_before_a_glob(self):
        home = self.home.path
        self.assertRefused("cd %s && echo x > ~+/ledger/tickets/SPD-00?.md" % home, "generated", AGENT_C)
        self.assertRefused("cd %s && echo x > ~+/ledger/tickets/SPD-00?.md" % home, "Law 1", agent_id=None)
        self.assertRefused("echo x > ~-/ledger/tickets/SPD-00?.md", "cannot follow", AGENT_C)

    def test_a_glob_over_a_directory_the_hook_cannot_follow(self):
        # cwds unknown (a cd the hook cannot follow) plus a glob: refused for a member, and since SPD-035 for Spud too.
        self.assertRefused("cd $DIR; echo x > ledger/tickets/SPD-00?.md", "cannot follow", AGENT_C)
        self.assertRefused("cd $DIR; echo x > ledger/tickets/SPD-00?.md", "cannot follow", agent_id=None)

    def test_the_glob_cost_is_bounded(self):
        # A glob whose expansion reaches the hook's match budget is refused for a member rather than walked without limit.
        cap = load_spud_module().GLOB_MATCH_CAP
        many = self.home.path / "tests" / "many"
        many.mkdir(parents=True, exist_ok=True)
        for i in range(cap + 20):
            (many / ("f%04d.txt" % i)).write_text("x\n", encoding="utf-8")
        self.assertRefused("echo x > tests/many/*.txt", "budget", AGENT_A)

    def test_controls_stay_allowed(self):
        self.assertSilent("ls > /dev/null")
        self.assertSilent("make 2>&1", agent_id=None)
        self.assertSilent("echo x > tests/plain.py")
        self.assertRefused("echo x > docs/plain.md", "deliverables")


class ZshGlobOperatorTest(BashHookCase):
    """SPD-039: zsh reads parenthesised alternation `(a|b)` and the numeric range `<n-m>` (`<->`, `<n->`, `<-m>`) as glob
    operators in a word it expands, where shlex reads `(` as a subshell and `<` as an input redirection.  Probed in zsh 5.9
    with its default options and with nobareglobqual (this Mac's Bash tool), and in bash 3.2: `echo x > (ledger|x)/tickets/
    SPD-001.md` and `echo x > tests/<1-1>/../../ledger/tickets/SPD-002.md` write the ledger file in zsh; bash rejects the `(`
    line and reads `<1-1>` as `< 1-1 >`.  The hook checks both readings: zsh's, the pattern kept whole and expanded as
    SPD-034 expands a glob, and the other shell's.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for rel in ("ledger/tickets/SPD-001.md", "ledger/tickets/SPD-002.md", "ledger/tickets/SPD-010.md", "tests/keep.py", "tests/other.py", "docs/x.md"):
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")
        for d in ("tests/1", "tests/007"):
            (home / d).mkdir(parents=True, exist_ok=True)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        for d in ("1", "007"):
            (self.out / d).mkdir()

    def outside(self, pattern, rel):
        """A target that opens <home>/<rel> through `pattern` matching a directory outside the repository, so that the other
        shell's reading (`<out>/`, then `/../<home>/<rel>` from the root) names only paths outside it."""
        return "%s/%s/../%s/%s" % (self.out, pattern, os.path.relpath(self.home.path, self.out), rel)

    def test_the_tickets_evidence_alternation(self):
        """The hole from SPUD-034/Atlantic (proposal 32): the hook read `(` as a subshell and checked `/tickets/SPD-001.md`."""
        evidence = "echo x > (ledger|x)/tickets/SPD-001.md"
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(agent_id=agent_id):
                self.assertRefused(evidence, "generated", agent_id)
        self.assertRefused(evidence, "Law 1", agent_id=None)
        for target in ("led(ger|x)/tickets/SPD-001.md", "(x|ledger)/(tickets|y)/SPD-00(1|9).md", "ledger/tickets/SPD-001.m(d|x)"):
            with self.subTest(target):
                self.assertRefused("echo x > %s" % target, "generated", AGENT_C)
                self.assertRefused("echo x >| %s" % target, "Law 1", agent_id=None)
        self.assertRefused("echo x &>(ledger|x)/tickets/SPD-001.md", "generated", AGENT_C)  # zsh: &>( and >|( open a glob target
        self.assertRefused("cd %s && echo x > ~+/(ledger|x)/tickets/SPD-001.md" % self.home.path, "generated", AGENT_C)
        self.assertRefused("echo x > (nomatch|zz)/x.py", "matches no file", AGENT_C)

    def test_the_tickets_evidence_range(self):
        """`SPD-<1-1>.md`: bash opens `SPD-` beside the ledger file, which was refused before SPD-039 on that reading alone; a
        range in a directory component sends bash's fragments elsewhere, and only zsh's reading finds the ledger file."""
        self.assertRefused("echo x > ledger/tickets/SPD-<1-1>.md", "generated", AGENT_C)
        self.assertRefused("echo x > ledger/tickets/SPD-<1-1>.md", "Law 1", agent_id=None)
        for pattern in ("<1-1>", "<7-7>", "<01-01>"):  # tests/1 and tests/007
            with self.subTest(pattern):
                for agent_id in (AGENT_C, AGENT_A):
                    self.assertRefused("echo x > tests/%s/../../ledger/tickets/SPD-001.md" % pattern, "generated", agent_id)
                self.assertRefused("echo x > %s" % self.outside(pattern, "ledger/tickets/SPD-001.md"), "Law 1", agent_id=None)
        self.assertRefused("echo x > tests/<1-1>/../../docs/x.md", "deliverables", AGENT_A)
        # bash's reading opens /../../docs/x.md, which is /docs/x.md at the filesystem root: no ledger file, and since
        # SPD-064 refused to a member (even a ** one) for being outside every registered project.
        self.assertRefused("echo x > tests/<1-1>/../../docs/x.md", "outside every registered project", AGENT_C)

    def test_the_open_range(self):
        for pattern in ("<->", "<1->", "<-9>", "<0-7>"):
            with self.subTest(pattern):
                self.assertRefused("echo x > ledger/tickets/SPD-%s.md" % pattern, "generated", AGENT_C)
                for agent_id in (AGENT_C, AGENT_A):
                    self.assertRefused("echo x > tests/%s/../../ledger/tickets/SPD-002.md" % pattern, "generated", agent_id)
                self.assertRefused("echo x > %s" % self.outside(pattern, "ledger/tickets/SPD-002.md"), "Law 1", agent_id=None)
        self.assertRefused("echo x > tests/<2->/../../ledger/tickets/SPD-001.md", "generated", AGENT_A)  # 007
        self.assertRefused("echo x > tests/<8->/../../ledger/tickets/SPD-001.md", "matches no file", AGENT_A)

    def test_nested_alternation(self):
        for target in ("((ledger|y)|x)/tickets/SPD-001.md", "led(g(e|x)r|zz)/tickets/SPD-001.md", "(x|(y|(ledger)))/tickets/SPD-00(1|(2|3)).md",
                       "{docs,(ledger|x)}/tickets/SPD-001.md", "ledger/tickets/SPD-(<1-1>|zz).md"):
            with self.subTest(target):
                self.assertRefused("echo x > %s" % target, "generated", AGENT_C)
                self.assertRefused("echo x > %s" % target, "Law 1", agent_id=None)
        self.assertRefused("echo x > %s" % self.outside("(1|zz)", "ledger/tickets/SPD-001.md"), "Law 1", agent_id=None)

    def test_a_group_zsh_cannot_read_opens_nothing(self):
        # `/` inside a group is a bad pattern in zsh (probed): nothing opens, so a member is refused as for a glob matching nothing.
        self.assertRefused("echo x > (ledger/tickets|x)/SPD-001.md", "matches no file", AGENT_C)
        # `;` `&` `<` `>` inside a group are parse errors in zsh and nothing on the line runs: the other shell's reading stands.
        self.assertRefused("echo x > ledger/tickets/SPD-00(1;|3).md", "generated", AGENT_C)

    def test_the_range_matcher_reads_numbers_as_zsh_does(self):
        """Any digit string whose value is in range, leading zeros included (`SPD-<1-1>.md` wrote SPD-001.md); a reversed range,
        which zsh matches to nothing, is read as the ordered one (checking more files is the safe side)."""
        m = load_spud_module()
        for lo, hi in (("", ""), ("1", ""), ("", "9"), ("1", "1"), ("001", "002"), ("5", "150"), ("0", "0"), ("99", "101"), ("2", "1"), ("7", "1000")):
            rx = re.compile(m.numeric_range_regex(lo, hi) + r"\Z")
            a, b = int(lo or 0), (int(hi) if hi else None)
            if b is not None and b < a:
                a, b = b, a
            for v in range(0, 1200):
                for name in (str(v), "0" + str(v), "00" + str(v)):
                    self.assertEqual(bool(rx.match(name)), a <= v and (b is None or v <= b), (lo, hi, name))
            for name in ("", "-1", "1a", "a1", " 1"):
                self.assertIsNone(rx.match(name), (lo, hi, name))

    def test_tee_arguments(self):
        self.assertRefused("printf x | tee (ledger|x)/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertRefused("printf x | tee -a tests/keep.py (ledger|x)/tickets/SPD-002.md", "generated", AGENT_C)
        self.assertRefused("printf x | tee tests/<1-1>/../../ledger/tickets/SPD-00<1-2>.md", "generated", AGENT_A)
        self.assertRefused("printf x | tee (ledger|x)/tickets/SPD-001.md", "Law 1", agent_id=None)
        self.assertRefused("printf x | tee %s" % self.outside("<->", "ledger/tickets/SPD-001.md"), "Law 1", agent_id=None)
        self.assertSilent("printf x | tee tests/keep.(py|zz)")

    def test_a_trailing_qualifier_shaped_group_is_read_both_ways(self):
        """Decision: zsh's default bareglobqual reads a trailing group with no `|` as glob qualifiers (`(.)` plain files, `(N)`
        null glob), nobareglobqual (this Mac's Bash tool) as a group; the hook checks the files of both readings."""
        self.assertRefused("echo x > (ledger|x)/tickets/SPD-001.md(.)", "Law 1", agent_id=None)  # the qualifier reading
        self.assertRefused("echo x > (ledger|x)/tickets/SPD-001.md(N)", "generated", AGENT_C)
        self.assertRefused("echo x > (ledger|x)/tickets/SPD-001(.md)", "generated", AGENT_C)  # the group reading
        self.assertRefused("echo x > (ledger|x)/tickets/SPD-001(.md)", "Law 1", agent_id=None)
        self.assertSilent("echo x > tests/keep.py(.)")
        self.assertSilent("echo x > tests/(keep|other).py(N)")

    def test_code_in_a_glob_qualifier_is_analysed(self):
        """With bareglobqual (zsh's default) the string of an `e` qualifier, `oe` included, runs for every file the glob matches,
        in the shell that expands it (probed: `(e:"touch ran":)`, `(oe:...:)`, `(e{...})`, `(e[...])` and `(+f)` ran, and a cd
        there moved the command's directory); the hook reads each string as a command run once or more."""
        for word in ("tests/*(e:'git push':)", "tests/*(oe:'git push':)", "tests/keep.py(e{git push})", "tests/keep.py(e[git push])",
                     "tests/*(.e:'git push':)", "(tests|x)/keep.py(e:'git push':)"):
            with self.subTest(word):
                self.assertRefused("ls %s" % word, "Law 7")
                self.assertRefused("echo x > %s" % word, "Law 7")
        self.assertRefused("ls tests/*(e:'echo y > ledger/tickets/SPD-001.md':)", "Law 1", agent_id=None)
        self.assertRefused("ls tests/*(e:'echo y > ledger/tickets/SPD-001.md':)", "generated", AGENT_C)
        self.assertRefused("ls tests/*(e:'cd ledger':) > tickets/SPD-001.md", "cannot follow", AGENT_C)
        for ok in ("ls tests/*(om[1])", "ls tests/*(.)", "ls tests/*(Lk+1)"):
            with self.subTest(ok):
                self.assertSilent(ok)

    def test_a_cd_into_a_zsh_pattern_is_unfollowable(self):
        """zsh follows `cd (ledger|y)` and `pushd (ledger|y)` to what they match (probed); a glob cd target is unfollowable
        (SPD-030), so these are too, rather than a cd to the home directory beside a subshell or an input redirection."""
        for cd in ("cd (ledger|x)", "cd led(ger|x)", "pushd (ledger|x)", "cd tests/<1-1>", "cd <1-1>", "cd tests/<->", "cd -P (ledger|x)"):
            with self.subTest(cd):
                self.assertRefused("%s && echo x > tickets/SPD-001.md" % cd, "cannot follow", AGENT_C)
                self.assertRefused("%s; echo x | tee tickets/SPD-001.md" % cd, "cannot follow", AGENT_C)
        self.assertRefused("cd (ledger|x) && echo x > %s/ledger/tickets/SPD-001.md" % self.home.path, "Law 1", agent_id=None)

    def test_bashs_reading_of_a_range_is_still_checked(self):
        """bash reads `<1-2>` as `< 1-2 >`: `cat <1-2> out` writes out in bash and reads a glob in zsh (probed)."""
        self.assertRefused("cat <1-2> ledger/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertRefused("cat <1-2> ledger/tickets/SPD-001.md", "Law 1", agent_id=None)

    def test_a_subshell_either_shell_runs_is_still_read(self):
        """bash runs `!(...)`, `{(...)}`, `if(...)`, `time(...)`, `then(...)`, `do(...)`, `else(...)` and `time -p (...)` as
        subshells, zsh `{(...)}`, `else(...)` and `f()(...)` (probed): their commands are checked, whatever zsh's reading."""
        for cmd in ("!(git push)", "{(git push)}", "if(git push) then :; fi", "time(git push)", "if true; then(git push); fi",
                    "for i in 1; do(git push); done", "if false; then :; else(git push); fi", "time -p (git push)", "f()(git push); f",
                    "g () (git push)", "coproc CO (git push)", "echo a; (git push)"):
            with self.subTest(cmd):
                self.assertRefused(cmd, "Law 7")
        self.assertRefused("{(echo x > ledger/tickets/SPD-001.md)}", "generated", AGENT_C)
        self.assertRefused("time -p (echo x > ledger/tickets/SPD-001.md)", "generated", AGENT_C)

    @wall_clock
    def test_pathological_patterns_neither_raise_nor_grow_quadratic(self):
        """A hook that raises refuses everyone, and one that runs past the harness's timeout protects nothing: deep nesting, a line
        of unbalanced openings, huge range bounds and long qualifier lists are read in bounded time (robustness probe)."""
        m = load_spud_module()
        home = str(self.home.path)
        for command in ("echo x > " + "(" * 4000 + "a" + ")" * 4000, "echo x > " + "(" * 40000 + "a", "echo x > " + "$((" * 20000,
                        "echo x > " + "${" * 40000, "echo x > f<%s-%s>" % ("1" * 5000, "9" * 5000), "ls x(" + "+a" * 20000 + ")",
                        "echo " + "a(" * 20000, "echo x > " + "a" * 200000 + "(b|c)"):
            with self.subTest(command[:24]):
                start = datetime.now()
                a = m.analyse_command(command, m.ShellAnalysis(cwd=home))
                for target, cwds in a.redirects:
                    if m.target_has_active_glob(target):
                        m.expand_redirect_target(target, cwds)
                self.assertLess((datetime.now() - start).total_seconds(), 10)

    def test_both_readings_stay_linear_in_nested_substitutions(self):
        m = load_spud_module()
        walks = []
        original = m.ShellWalk.walk

        def counting(walk, tokens):
            walks.append(1)
            return original(walk, tokens)

        m.ShellWalk.walk = counting
        command = "echo (a|b)"
        for _ in range(6):
            command = "echo (a|b) $(%s)" % command
        m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))
        self.assertLessEqual(len(walks), 16)

    def test_controls_stay_as_they_are(self):
        out = self.out
        self.assertRefused("(cd %s) && echo x > note.txt" % out, "deliverables")  # a subshell's cd does not carry out
        self.assertSilent("(cd %s && echo x > note.txt)" % out)
        self.assertSilent("time (cd %s); echo x > tests/keep.py" % out)
        self.assertSilent("cat <tests/keep.py > tests/out.py")
        self.assertSilent("sort < tests/keep.py > tests/out.py")
        self.assertRefused("cat <tests/keep.py > docs/x.md", "deliverables")
        self.assertRefused("cat <(git push)", "Law 7")
        self.assertSilent("diff <(ls tests) <(ls bin)")
        self.assertRefused("echo x >(tee ledger/tickets/SPD-001.md)", "generated", AGENT_C)
        self.assertSilent("echo x > 'tests/(a|b).py'")
        self.assertSilent('echo x > "tests/(keep|other).py"')
        self.assertSilent("echo x > tests/\\(a\\|b\\).py")
        self.assertRefused("echo x > 'ledger/(a|b).md'", "generated")
        self.assertSilent("echo x > tests/(keep|other).py")
        self.assertRefused("echo x > tests/(keep|other).py", "Law 1", agent_id=None)
        self.assertSilent("make 2>&1", agent_id=None)
        self.assertSilent("make > /dev/null 2>&1")
        self.assertSilent("ls > /dev/null")
        for ok in ("f() { echo hi; }; f", "arr=(a b); echo $arr", "typeset -a arr=(a b)", "x=$(( 1<2 )); (( 3 < 2 )) && echo y",
                   "[[ -n x && ( -d tests ) ]] && echo y", "case x in (x|y) echo y;; esac", "for f in tests/(keep|other).py; do echo $f; done",
                   "noglob echo (a|b)", "echo a |(cat)", "! (true)", "{ (true) }"):
            with self.subTest(ok):
                self.assertSilent(ok)
        # The `>` of an arithmetic command was read as an output operator and the word after it as a target (proposal 103,
        # then SPD-088): marked as arithmetic since, and silent here.  ArithmeticCommandTest holds the whole reading.
        self.assertSilent("x=$(( 1<2 )); (( 3 > 2 )) && echo y")
        self.assertSilent("(( a > b ))")
        self.assertRefused("case x in (x) git push;; esac", "Law 7")
        self.assertAllowed("%s --as %s member log 'a (b|c) <1-2>'" % (self.spud_cli, AGENT_A))


class ArithmeticCommandTest(BashHookCase):
    """SPD-088: `(( ... ))` is an arithmetic command and `$(( ... ))` an arithmetic expansion, and both shells evaluate what
    stands between the parentheses -- the `>` of `(( n > 2 ))` is a comparison, the `|` of `(( a | b ))` a bitwise or, the `;`
    of a `for (( ... ))` header separates its three expressions -- so no file is opened and no second command runs.
    mark_zsh_patterns already found the region and copied it into both readings verbatim, so shlex handed separate_redirects
    `n`, `>`, `2`, and a member's `if (( retries > 3 ))` was refused as a write to a file named 2 wherever the shell's
    directory lay outside its deliverables, and Spud's in a protected one.

    Recorded on the ticket, from analyse_command on the working tree when SPD-088 was filed: `(( 3 > 2 ))`,
    `if (( n > 2 )); then echo x; fi` and `x=$(( 1 > 2 ))` each recorded the target ['2'], `(( i > 0 ))` ['0'],
    `(( n >= 3 ))` ['='] and `(( a > b ))` ['b'], while `let "n > 2"` and `[[ 3 -gt 2 ]]` recorded none; SPD-049's
    differential had already found `x=$(( 1<2 )); (( 3 > 2 )) && echo y` among the 32 member lines that went silent -> deny.
    No shell is probed here: this worktree session's harness refuses to run one, and what the fix restores is arithmetic
    evaluation as bash(1) and zsh(1) define it, which the ticket's own evidence measures the hook against.

    The marking (syntax._ARITH_SENTINELS, with the quoted-glob sentinels for `*?[]{},`, which already mean "not expanded
    here", and _LITERAL_DOLLAR for a `$`) goes into both readings unchanged, arithmetic being arithmetic in both shells.  The
    region's outer parenthesis and its match are left as they are, so ShellWalk opens and closes the frame it always opened
    and its `for (( ... ))` header scan still finds the header's end; every character between them is marked, the inner `(`
    of `((` included, so the segment's first word always begins with a sentinel and no word of an arithmetic command is read
    as a command name, a variable, an assignment or a glob.  An arithmetic command keeps its blanks, so a `$( ... )` inside
    it is still analysed and still stays out of the command word; an expansion is marked whole, blanks included, since it is
    part of a word.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    EVIDENCE = ("(( n > 2 ))", "(( i > 0 ))", "(( 3 > 2 ))", "(( n >= 3 ))", "(( a > b ))",
                "if (( retries > 3 )); then echo x; fi", "x=$(( 1 > 2 ))", "x=$(( 1<2 )); (( 3 > 2 )) && echo y")

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for d in ("docs", "tests", "ledger/tickets"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def test_the_tickets_evidence_records_no_redirect_target(self):
        for command in self.EVIDENCE:
            with self.subTest(command):
                a = self.analysis(command)
                self.assertEqual([t for t, _cwds in a.redirects], [])
                self.assertEqual(a.findings, [])

    def test_the_tickets_evidence_is_silent_wherever_the_shell_stands(self):
        for command in self.EVIDENCE:
            with self.subTest(command):
                self.assertSilent("cd docs && " + command)  # a member, its directory outside its deliverables
                self.assertSilent("cd docs && " + command, AGENT_C)
                self.assertSilent("cd ledger/tickets && " + command, agent_id=None)  # Spud, in a generated directory

    def test_the_other_operators_are_read_as_arithmetic_too(self):
        for command in ("(( n < 2 ))", "(( n >> 1 ))", "(( n << 1 ))", "(( a && b ))", "(( a || b ))", "(( a | b ))",
                        "(( a & b ))", "(( a > b ? 1 : 0 ))", "(( a * b ))", "(( a*b ))", "(( a % b ))", "(( a ^ b ))",
                        "(( ! a ))", "(( ~a ))", "(( n++ ))", "(( a[1] + 2 ))", "(( 16#ff ))", "(( (a) > 2 ))",
                        "(( $n > 2 ))", "(( ${n} > 2 ))"):
            with self.subTest(command):
                a = self.analysis(command)
                self.assertEqual([t for t, _cwds in a.redirects], [])  # nothing opened a target
                self.assertEqual(a.kinds, ["other"])  # ... and nothing split the line into more commands
                self.assertEqual(a.findings, [])
                self.assertSilent("cd docs && " + command)

    def test_a_for_arithmetic_header_is_still_a_header(self):
        for header in ("for (( i = 0; i < 10; i++ )); do %s; done", "for (( i = 0; i < 10; i++ )) do %s; done",
                       "for (( i=0; i<3; i++ )) %s"):
            with self.subTest(header):
                self.assertRefused(header % "git push", "Law 7")  # the body still runs in command position
                self.assertRefused(header % "echo x > docs/x.md", "deliverables")
                self.assertSilent(header % "echo x > tests/out.py")  # ... and the header's own `<` opens nothing
                self.assertEqual([t for t, _c in self.analysis(header % "echo x > out.txt").redirects], ["out.txt"])
        # the loop still closes where it closed: what follows `done` is outside the body
        self.assertRefused("for (( i = 0; i < 2; i++ )); do echo x; done; git push", "Law 7")
        self.assertSilent("for (( i = 0; i < 2; i++ )); do echo x; done")

    def test_a_real_redirection_after_an_arithmetic_command_is_still_checked(self):
        for command, targets in (("(( n > 2 )) > out.txt", ["out.txt"]), ("(( n > 2 )) && echo x > out.txt", ["out.txt"]),
                                 ("x=$(( 1 + 1 )) > out.txt", ["out.txt"]), ("(( n > 2 )) 2> err.txt", ["err.txt"]),
                                 ("(( a )) > f1 ; (( b )) >> f2", ["f1", "f2"])):
            with self.subTest(command):
                self.assertEqual([t for t, _cwds in self.analysis(command).redirects], targets)
        for command in ("(( n > 2 )) > docs/x.md", "(( n > 2 )) && echo x > docs/x.md", "x=$(( 1 + 1 )) > docs/x.md"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables")
                self.assertSilent(command, AGENT_C)
        self.assertRefused("(( n > 2 )) > ledger/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertRefused("(( n > 2 )) > ledger/tickets/SPD-001.md", "Law 1", agent_id=None)

    def test_a_redirection_outside_arithmetic_is_unchanged(self):
        for command, targets in (("echo x > 2", ["2"]), ("echo x > b", ["b"]), ("echo x >> -", ["-"]),
                                 ("echo x 2> 12", ["12"]), ("echo x >| 3", ["3"]), ("echo x >&3", []),
                                 ("echo x 2>&1", []), ("echo x >&-", []), ("echo x >& out", ["out"])):
            with self.subTest(command):
                self.assertEqual([t for t, _cwds in self.analysis(command).redirects], targets)
        for command in ("cd docs && echo x > 2", "cd docs && echo x > b", "cd docs && echo x 2> 12"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables")

    def test_an_unbalanced_opener_is_no_crash_and_no_new_silence(self):
        for command in ("(( ", "((", "(( 1 > ", "$(( 1 + ", "x=$(( 1 + ", "for (( i=0; i<3"):
            with self.subTest(command):
                self.assertEqual([t for t, _cwds in self.analysis(command).redirects], [])
                self.assertSilent(command)
        # ... and the reading that stood before SPD-088 still reads what follows an opener it cannot pair
        self.assertRefused("(( 1 ; git push", "Law 7")
        self.assertRefused("x=$(( 1 + ; echo x > docs/x.md", "deliverables")

    def test_a_substitution_inside_arithmetic_is_still_analysed(self):
        for command in ("(( x = $(git push) ))", "(( $(git push) > 2 ))", "x=$(( $(git push) + 1 ))",
                        "(( $(git push) ))", "for (( i=0; i<$(git push); i++ )); do echo x; done"):
            with self.subTest(command):
                self.assertRefused(command, "Law 7")
        self.assertRefused("(( x = $(echo y > docs/x.md) ))", "deliverables")
        self.assertSilent("(( x = $(echo y > tests/out.py) ))")

    def test_an_expansions_own_dollar_still_names_the_command_word(self):
        """SPD-043's reading, deliberately left alone: only the arithmetic between the parentheses is marked, never the `$`
        in front of them, so `$(( ... ))` standing in the command position still names a command the hook cannot read -- an
        executable named by digits, found on a PATH of the line's own choosing -- and still refuses a member."""
        self.assertRefused("$((1)) push", "the command word")
        self.assertRefused("$(( 1 + 1 ))", "the command word")
        self.assertSilent("echo $(( 1 > 2 ))")  # ... where an expansion that is not the command word is read by nobody
        self.assertSilent("x=$(( 1 > 2 ))")
        self.assertSilent("cd docs && echo $(( 1 > 2 ))")

    def test_both_readings_carry_the_same_marking(self):
        m = load_spud_module()
        for command in ("(( n > 2 ))", "x=$(( 1 > 2 ))", "for (( i=0; i<3; i++ )); do echo x; done", "(( a | b ))"):
            with self.subTest(command):
                marked, other = m.mark_zsh_patterns(command)
                self.assertEqual(marked, other)  # an arithmetic command is arithmetic in bash and zsh alike
                self.assertNotEqual(marked, command)  # ... and it is marked in both
                self.assertEqual(m.deglob(marked), command)  # deglob is its inverse, so a reason names what the line spells
        # a line that holds a zsh pattern as well keeps the two readings, with the same arithmetic in each
        line = "(( n > 2 )); echo x > (ledger|x)/tickets/SPD-001.md"
        marked, other = m.mark_zsh_patterns(line)
        self.assertNotEqual(marked, other)
        self.assertEqual(m.deglob(marked), line)
        self.assertEqual(m.deglob(other), line)
        self.assertRefused(line, "generated", AGENT_C)  # the pattern's target is still expanded and checked

    def test_an_arithmetic_assignment_is_no_longer_recorded(self):
        """The other half of marking the whole region: `(( x=1 ))` used to reach analyse_words as the word `x=1` and record
        x in the analysis's variables, and now reaches it behind the arithmetic sentinel that keeps the region inert.  This
        is the half that fails closed -- a later `$x` refuses a member where it used to resolve to 1 -- and an arithmetic
        value is a number, never a command word or a path."""
        self.assertEqual(self.analysis("(( x=1 ))").vars, {})
        self.assertRefused("(( x=1 )); $x", "the command word $x")
        self.assertSilent("x=1; $x")  # an ordinary assignment is read as it always was


class GlobCommandWordTest(BashHookCase):
    """SPD-041: both shells expand an unquoted glob, a brace list and zsh's `(a|b)` and `<n-m>` in every word before running a
    command, the command word and git's verb included, and zsh replaces a leading `=name` with the command's path (EQUALS).
    Probed in zsh 5.9 (-f, and -o nobareglobqual as this Mac's Bash tool runs it) and bash 3.2 with a fake git on a scratch
    PATH: `$FAKE/bin/g?t push`, `touch push; git p?sh`, `{git,push}` (bash), `=git push` (zsh), `git [-]p push` with a file
    named -p, `sh [-]c 'git push'`, `spud t?cket new` all ran the hidden command.  The hook reads such a word, where it
    dispatches on it, as every checked name it can match (its literal spelling too, which bash runs when nothing matches), so
    the refusal does not wait for the file a line may create; a word that can become two checked names at once (`(env|git)`)
    is refused for a member.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **; Spud is never refused for git."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for rel in ("ledger/tickets/SPD-001.md", "tests/keep.py", "docs/x.md"):
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")
        (home / "tests" / "tool").symlink_to(home / "bin" / "spud")  # a launcher by another name (SPD-029)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)

    def refused_for_members(self, command, needle, cwd=None):
        return_value = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                return_value = self.assertRefused(command, needle, agent_id, cwd)
        return return_value

    def test_the_tickets_evidence_commands(self):
        for cmd in ("/usr/bin/g?t push", "/usr/bin/g(i|x)t push"):
            r = self.refused_for_members(cmd, "Law 7")
            self.assertIn("git push", r.reason)
            self.assertSilent(cmd, agent_id=None)

    def test_a_glob_in_the_command_word_is_every_command_it_can_become(self):
        for cmd in ("/usr/bin/gi[t] commit -m x", "/usr/bin/g*t push", "g?t push", "./g?t push", "~/bin/g?t push", "/usr/b?n/g?t push",
                    "/usr/bin/G?T push", "g(i|x)t push", "gi(t|x) reset --hard", "/usr/bin/git(N) push", "g?t(.) push", "touch git; g?t push",
                    "true && /usr/bin/g[a-z]t add .", "X=g?t; $X push", "timeout 5 g?t push", "- g?t push", "f() { g?t push; }; f"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")
                # SPD-121: `touch git` is itself a write by argument, and a file named git in the home is a deliverable, so
                # Spud earns Law 1 for it there; his reading of the glob is unchanged, which the same line outside shows.
                self.assertSilent(cmd, agent_id=None, cwd=str(self.out) if cmd.startswith("touch ") else None)
        self.assertSilent("X=g?t; echo $X")  # an assignment's value is not expanded, and the variable is only echoed

    def test_a_verb_glob_matching_a_file_the_line_creates(self):
        """Decision: the verb is read as every write verb its pattern can match, whether or not a file matches now (the line may
        create it), and a pattern that can match no write verb is read as spelled.  Since SPD-047 the spelled reading is refused
        too, as a verb outside git's own commands: the shell expands the pattern against files, so a member that creates a file
        named like an alias runs that alias (`touch zz; git z?`), and the pattern itself is no git command either.  The refusal
        of a write verb the pattern can match still comes first, so `touch push; git p?sh` keeps naming `git push`."""
        for cmd in ("touch push; git p?sh", "touch push && git p*", "git p?sh", "git pu[s]h", "git p(u|x)sh", "touch commit; git c?mmit -m x",
                    "git ch?ckout main", "git st?sh", "git re[s]et --hard", "git st(a|x)sh pop", "git worktree a?d ../x", "git branch -[D] x"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")
        self.assertIn("git push", self.assertRefused("touch push; git p?sh", "Law 7").reason)
        # A pattern that can match no write verb was silent before SPD-047 and is now refused as a verb git does not have;
        # Spud is still not bound by any of it.
        for cmd in ("git st?tus", "git l?g --oneline", "git d[i]ff", "git sh(o|x)w HEAD", "touch status; git st*tus"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd, "Law 7")
                self.assertIn("not one of git's own commands", r.reason)
                # SPD-121: `touch status` writes a file in the home, which is Law 1 for Spud; outside every project it is his
                self.assertSilent(cmd, agent_id=None, cwd=str(self.out) if cmd.startswith("touch ") else None)

    def test_brace_lists_in_the_command_word_and_the_verb(self):
        for cmd in ("{git,push}", "{/usr/bin/git,push}", "git {push,status}", "command {git,push}", "{env,git} push", "git {-C,.} push",
                    "{/usr/bin/g?t,push}"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")
                self.assertSilent(cmd, agent_id=None)
        # git status push; git gxt push; git x push; git /usr/bin/x push (probed: the second word is the verb)
        self.assertSilent("git {status,push}")  # status is one of git's own commands and its arguments are not read
        # The other three put a word git does not have in the verb position, which SPD-047 refuses as an alias git would expand.
        for cmd in ("g{i,x}t push", "{git,x} push", "/usr/bin/{git,x} push"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd, "Law 7")
                self.assertIn("not one of git's own commands", r.reason)
                self.assertSilent(cmd, agent_id=None)

    def test_zsh_equals_expansion(self):
        """zsh replaces `=git` with git's path (EQUALS, on by default and in the Bash tool); bash runs a command named =git.
        `=(...)` is zsh's process substitution, which runs its command: read as one again, as before SPD-039."""
        for cmd in ("=git push", "command =git push", "nice =git push", "env =git push", "exec =git commit -m x", "echo =(git push)",
                    "cat =(git commit -m x)", "diff =(git push) tests/keep.py"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")
        self.refused_for_members("python3.14 -I -S =spud ticket new --title x", "Law 6")
        for ok in ("echo a=b c==d", "[ a = b ] && echo y", "test x = y", "echo =git"):
            with self.subTest(ok):
                self.assertSilent(ok)

    def test_a_wrappers_command_word_and_options(self):
        for cmd in ("env g?t push", "command g?t push", "exec g?t push", "nice -n 5 g?t push", "NOHUP g?t push", "sudo -u root g?t push",
                    "xargs g?t push", "time g?t push", "env -u X g?t push", "e?v git push", "n?hup git push", "/usr/bin/e[n]v git push",
                    "nice -[n] 5 git push", "sudo -[u] root git push", "env {-u,X} git push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")

    def test_git_global_options_before_a_glob_verb(self):
        for cmd in ("git -C . p?sh", "git -c k=v c?mmit -m x", "git --no-pager p?sh", "git -C /tmp -c k=v p?sh", "git [-]p push",
                    "git -[C] . push", "git -C [.p]ush status", "git nomatch(N) push", "git -C tests/(keep|x) p?sh"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")

    def test_a_glob_in_a_spud_invocation(self):
        home = self.home.path
        for cmd in ("python3.14 -I -S %s/bin/sp?d ticket new --title x" % home, "%s/bin/sp[u]d --as spud board" % home,
                    "python3.14 -I -S %s/bin/sp(u|x)d init" % home, "cd %s && python3.14 -I -S bin/sp?d render" % home,
                    "python3.14 -I -S %s/t*s/tool --as spud board" % home, "%s t?cket new --title x" % self.spud_cli,
                    "%s --a[s] spud board" % self.spud_cli, "%s ticket {new,show} --title x" % self.spud_cli, "python3.1[4] -I -S %s/bin/spud init" % home):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 6")
        self.assertRefused("python3.14 -I -S %s/bin/sp?d --as %s member log hi" % (home, AGENT_A), "Law 5", agent_id=None)
        self.assertRefused("%s/bin/sp?d hook PreToolUse" % home, "hook", agent_id=None)
        self.assertSilent("python3.14 -I -S %s/bin/sp?d --as %s member log hi" % (home, AGENT_A))  # recognized, never allowed

    def test_a_tee_or_cd_behind_a_glob(self):
        home, out = self.home.path, self.out
        self.assertRefused("printf x | t?e ledger/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertRefused("printf x | t?e ledger/tickets/SPD-001.md", "Law 1", agent_id=None)
        self.assertRefused("printf x | /usr/bin/t(e|x)e docs/x.md", "deliverables", AGENT_A)
        # `c[d]` matches cd alone; since SPD-121 cp is a checked name too, so `c?` can become two at once and a member is
        # refused for the glob itself, while Spud's reading of the cd is unchanged.
        cmd = "c[d] %s/ledger && echo x > tickets/SPD-001.md" % home
        self.assertRefused(cmd, "generated", AGENT_C, cwd=str(out))
        self.assertRefused(cmd, "Law 1", agent_id=None, cwd=str(out))
        self.assertRefused("c? %s/ledger && echo x > tickets/SPD-001.md" % home, "glob", AGENT_C, cwd=str(out))
        self.assertRefused("c? %s/ledger && echo x > tickets/SPD-001.md" % home, "Law 1", agent_id=None, cwd=str(out))
        self.assertSilent("cd %s && echo x > tickets/SPD-001.md" % out, AGENT_C, cwd=str(out))

    def test_shell_strings_read_their_own_globs(self):
        """A glob quoted for the outer shell is unquoted in the string a shell or eval runs (probed: `sh -c 'g?t push'` and
        `eval 'g?t push'` pushed): the string is read with its quotes, the hook's quoting marks cleared."""
        for cmd in ("sh -c 'g?t push'", "bash -c \"/usr/bin/g?t push\"", "eval 'g?t push'", "zsh -c 'git p?sh'", "sh [-]c 'git push'",
                    "script -c 'g?t push' typescript"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")
        for cmd in ("sh -c 'echo x > ledg*/tickets/SPD-00?.md'", "eval 'echo x > ledg*/tickets/SPD-001.md'"):
            with self.subTest(cmd):
                self.assertRefused(cmd, "generated", AGENT_C)
                self.assertRefused(cmd, "Law 1", agent_id=None)
        self.assertSilent("sh -c \"echo 'g?t push'\"")

    def test_a_glob_that_can_become_two_checked_words_is_refused_for_a_member(self):
        """`* x` ran `git push x` with files git and push, `* push` ran `env git push` with files env and git (probed): one
        glob that can match two checked names can put both on the line, so a member is refused whatever each reading finds."""
        for cmd in ("[gp][iu][st]* x", "/usr/bin/(env|git) status","%s member log *" % self.spud_cli, "git -C * status", "/usr/bin/" + "*" * 3 + "g*t*x*y push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "glob")
                self.assertSilent(cmd, agent_id=None)
        self.refused_for_members("* push", "Law 7")  # one reading is git push

    @wall_clock
    def test_pathological_glob_words_are_read_in_bounded_time(self):
        m = load_spud_module()
        home = str(self.home.path)
        for command in ("*" * 5000 + "x push", "?" * 3000 + " push", "git " + "*a" * 2000, "/usr/bin/" + "*a" * 50 + "b push",
                        "git " + "{a,b}" * 20, "spud " + " ".join("a%d*" % i for i in range(3000)), "(" + "a|" * 3000 + "b)x push",
                        "git " + "[ab]" * 2000 + "*" * 3 + "c"):
            with self.subTest(command[:24]):
                start = datetime.now()
                m.analyse_command(command, m.ShellAnalysis(cwd=home))
                self.assertLess((datetime.now() - start).total_seconds(), 10)

    def test_controls_keep_todays_reading(self):
        out = self.out
        for ok in ("'g?t' push", "g\\?t push", '"g*t" push', "ls *.py", "ls tests/*", "grep 'a*' tests/keep.py", "grep -n x tests/*.py",
                   "git log -- '*.py'", "git diff HEAD -- tests/*", "git log --oneline -- tests/*.py", "git show HEAD:tests/*.py",
                   "[[ x == g?t ]] && echo y", "case g?t in g?t) echo y;; esac", "arr=(g?t push); echo $arr", "echo $((1*2))",
                   "x=$((2*3)); echo $x", "[ -f tests/keep.py ] && echo y", "for f in tests/*.py; do echo $f; done", "echo g?t push",
                   "git branch --list 'feat*'", "git tag -l v1.*", "python3.14 -I -S -m unittest discover -s tests -t tests",
                   "command -v g?t", "find . -name '*.py'", "(cd %s && echo x > note.txt)" % out, "bash tests/*.sh",
                   "python3 tests/k*.py", "env FOO=1 ls *.py", "git -C tests/* status", "/usr/bin/nomatch-spd-041* push"):
            with self.subTest(ok):
                self.assertSilent(ok)
        self.assertRefused("(cd %s) && echo x > note.txt" % out, "deliverables")
        self.assertAllowed("%s --as %s member log 'a*b (c|d) =e {f,g}'" % (self.spud_cli, AGENT_A))
        self.assertRefused("git push", "Law 7")


class ReadWriteRedirectTest(BashHookCase):
    """SPD-040: the read-write redirection `<>` opens its target O_RDWR|O_CREAT, so it writes or creates the file, and `1<>`
    lets the command overwrite it from the start without truncating.  Before, the hook read `<>` as an input redirection and
    dropped its operand unchecked.  Probed in zsh 5.9 -f, zsh -f -o nobareglobqual (this Mac's Bash tool), bash 3.2 and sh,
    scratchpad files only: bare `<>f`, spaced `<> f`, `0<>`, `1<>`, `2<>`, `9<>`, `10<>`, a leading `<>f echo x`, a quoted
    operand, `<>f<>g` (both), and the same inside `sh -c`, `bash -c`, `zsh -c`, `eval`, a subshell and `$(...)` each created
    its file; `printf abcd > g; echo x 1<>g` left `x` then `cd`.  An operand that is all digits or `-` is a file name after
    `<>` (`<>3` and `<>-` created files named 3 and -); `1<>&2` is a syntax error in both shells, so `<>` has no dup form.
    `exec {fd}<>f` created f in all four (zsh's named descriptor, bash 3.2's command word).  `<>` goes through every check an
    output target gets: the path rule, glob, brace and zsh-pattern readings, the directories before it, Spud's Law 1 and the
    state directory.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    EVIDENCE = "echo x 1<>ledger/tickets/SPD-001.md"

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for rel in ("ledger/tickets/SPD-001.md", "ledger/tickets/SPD-002.md", "tests/keep.py", "docs/x.md"):
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")

    def refused_for_all(self, command):
        """A generated file: refused for both members (whatever their deliverables) and for Spud under Law 1."""
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertRefused(command, "generated", agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertRefused(command, "Law 1", agent_id=None)

    def test_the_tickets_evidence_overwrites_a_generated_file(self):
        """Proposal 33 (SPUD-039/Bintje): `echo x 1<>ledger/tickets/SPD-001.md` changed the file and the hook recorded no redirect."""
        self.refused_for_all(self.EVIDENCE)
        m = load_spud_module()
        self.assertEqual([t for t, _cwds in m.analyse_command(self.EVIDENCE).redirects], ["ledger/tickets/SPD-001.md"])

    def test_every_spelling_of_the_operator_keeps_its_operand(self):
        target = "ledger/tickets/SPD-001.md"
        for command in ("echo x <>%s", "echo x <> %s", "echo x 0<>%s", "echo x 1<> %s", "echo x 2<>%s", "echo x 9<>%s",
                        "echo x 10<>%s", "<>%s echo x", "1<>%s echo x", "echo x 1<>'%s'", 'echo x 1<>"%s"', "cat <>%s",
                        "echo x <>tests/keep.py<>%s", "echo x 1<>tests/keep.py 2<>%s", "echo x 1<>%s 2>&1", "exec {fd}<>%s",
                        "exec 3<>%s", "echo x >/dev/null 1<>%s"):
            self.refused_for_all(command % target)

    def test_the_operand_is_a_file_name_whatever_its_shape(self):
        # `<>3` and `<>-` created files named 3 and - (probed): neither is a descriptor or a close after `<>`.  The home is
        # the cwd, outside AGENT_A's deliverables.
        for command in ("echo x <>3", "echo x 1<>3", "echo x <> -", "echo x <>docs/x.md", "echo x 1<>docs/new.md"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables", AGENT_A)
        self.assertSilent("echo x 1<>tests/keep.py")
        self.assertSilent("echo x <> tests/new.py")
        self.assertSilent("echo x 1<>3", agent_id=AGENT_C)

    def test_inside_shell_strings_eval_and_subshells(self):
        target = "ledger/tickets/SPD-001.md"
        for command in ("sh -c 'echo x 1<>%s'", "bash -c 'echo x <>%s'", "zsh -c 'echo x 2<> %s'", "eval 'echo x 1<>%s'",
                        "(echo x 1<>%s)", "echo $(echo x 1<>%s)", "echo `echo x 1<>%s`", "{ echo x; } 1<>%s", "(echo x) 1<>%s",
                        "if true; then echo x 1<>%s; fi", "for f in a; do echo x 1<>%s; done", "true && echo x <>%s",
                        "echo x | cat 1<>%s", "env sh -c 'echo x 1<>%s'", "sh -c \"eval 'echo x 1<>%s'\""):
            self.refused_for_all(command % target)

    def test_a_glob_brace_or_zsh_pattern_operand_is_expanded(self):
        # SPD-034, SPD-039 and SPD-041's readings of an output target apply to `<>` alike.
        for target in ("ledger/tickets/SPD-00?.md", "led*/tickets/SPD-001.md", "ledger/tickets/SPD-00[12].md",
                       "{ledger,docs}/tickets/SPD-001.md", "ledger/tickets/SPD-00{1,2}.md", "(ledger|x)/tickets/SPD-001.md",
                       "ledger/tickets/SPD-<1-1>.md"):
            with self.subTest(target):
                self.assertRefused("echo x 1<>%s" % target, "generated", AGENT_C)
                self.assertRefused("echo x <>%s" % target, "Law 1", agent_id=None)
        self.assertRefused("sh -c 'echo x 1<>ledg*/tickets/SPD-001.md'", "generated", AGENT_C)
        self.assertRefused("echo x 1<>tests/nomatch-xyzzy*.py", "matches no file", AGENT_A)
        self.assertSilent("echo x 1<>tests/k*.py")

    def test_the_directories_before_it_are_followed(self):
        home = self.home.path
        self.assertRefused("cd ledger && echo x 1<>tickets/SPD-001.md", "generated", AGENT_C)
        self.assertRefused("cd %s/ledger; echo x <>tickets/SPD-001.md" % home, "Law 1", agent_id=None)
        self.assertRefused("{ cd ledger; true; } 1<>tickets/SPD-001.md", "generated", AGENT_C)  # a compound's redirection (redirect_cwds)
        self.assertRefused("cd $DIR; echo x 1<>tests/keep.py", "cannot follow", AGENT_A)
        self.assertRefused("echo x 1<>~+/ledger/tickets/SPD-001.md", "generated", AGENT_C)

    def test_the_state_directory_and_a_variable_target(self):
        for agent_id in (AGENT_A, None):
            with self.subTest(agent_id=agent_id):
                # `>.spud/` escapes DB_PATH_RE (a `>` before `.spud`), so the target itself is what refuses it
                self.assertRefused("echo x 1<>.spud/pycache/x", "ledger database", agent_id)
                self.assertRefused("cd .spud && echo x <>backups/x", "ledger database", agent_id)
        self.assertRefused("echo x 1<>$T", "spell the path out", AGENT_A)

    def test_a_spud_call_with_a_read_write_redirection_is_not_allowed(self):
        # The allow needs every redirection quiet (SPD-032): a `<>` into a file is a write the prompt would ask about.
        log = "%s --as %s member log x" % (self.spud_cli, AGENT_A)
        self.assertSilent(log + " 1<>tests/keep.py")
        self.assertRefused(log + " 1<>docs/x.md", "deliverables")
        self.assertAllowed(log + " <>/dev/null")

    def test_input_redirections_are_unchanged(self):
        for ok in ("cat <ledger/tickets/SPD-001.md", "cat < docs/x.md", "cat 0<docs/x.md", "wc -l <ledger/tickets/SPD-001.md",
                   "cat <<<ledger/tickets/SPD-001.md", "cat <<EOF\nledger/tickets/SPD-001.md\nEOF", "cat <<-EOF\n\tx\n\tEOF",
                   "cat <&0", "cat 3<&0", "cat <&-", "diff <(cat docs/x.md) docs/x.md", "make 2>&1", "ls > /dev/null",
                   "exec 3<&0", "echo x >&2", "sh -c 'cat <docs/x.md'"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        self.assertSilent("cat <tests/keep.py >tests/new.py")
        self.assertAllowed("%s --as %s member log x < docs/x.md" % (self.spud_cli, AGENT_A))
        self.assertRefused("cat <tests/keep.py >docs/x.md", "deliverables")
        m = load_spud_module()
        for command in ("cat <f", "cat 0<f", "cat <<<f", "cat <&3", "cat <<EOF\nf\nEOF"):
            with self.subTest(command):
                self.assertEqual(m.analyse_command(command).redirects, [])


class GitConfigAliasTest(BashHookCase):
    """SPD-044: git expands an alias (a config key `alias.NAME`) into whatever command it names before it dispatches, so a
    member's write can run under a verb Law 7's table does not list.  git_verb skips `-c` and its value and reads the next word
    as the verb, so `git -c alias.p=push p` was read as verb `p` and refused nothing, while git ran push.  Probed (zsh/bash,
    scratch dir, read-only `version` as the stand-in, never push): `git -c alias.v=version v` printed the version; the joined
    `-calias.v=version` is rejected by git (`unknown option`); an alias does not override a builtin (`git -c alias.status=... status`
    ran the builtin status).  The env forms (GIT_CONFIG_COUNT/KEY_n/VALUE_n, GIT_CONFIG_PARAMETERS, GIT_CONFIG*/GLOBAL/SYSTEM) are
    documented git config injection; the harness's own worktree guard refuses them when they redirect writes, and Law 7's hook,
    which runs in every session, closes them too.  The fix refuses a member's git call whose own line defines an alias/include by
    `-c`/`--config-env` or sets a GIT_CONFIG_* variable, as an unresolvable verb; it does not inspect the value, so it also refuses
    an alias to a read (an over-refusal a member never hits in ordinary work).  SPD-046 later narrowed the non-alias case: only
    inert config (color.ui, core.pager=cat, and the rest of GitProgramTest's allowlist) stays silent; user.name, commit.gpgsign
    and other non-allowlisted keys now refuse as a git-program finding.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **;
    Spud (no agent_id) is not bound by Law 7 and sees no change."""

    EVIDENCE = "git -c alias.p=push p"

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7", cwd=None):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id, cwd)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None, cwd=cwd)  # Law 7 does not bind Spud
        return r

    def test_the_tickets_evidence_command(self):
        r = self.refused_for_members(self.EVIDENCE)
        self.assertIn("alias", r.reason)
        m = load_spud_module()
        self.assertEqual(m.analyse_command(self.EVIDENCE, m.ShellAnalysis(cwd=str(self.home.path))).findings,
                         [("git-config", "-c alias.p=push")])

    def test_c_option_alias_and_include_forms(self):
        for cmd in ("git -c alias.p=push p", "git -c alias.co=checkout co", "git -c include.path=/tmp/x v",
                    "git -c includeIf.gitdir:/x/.path=/tmp/y v", "git -C . -c alias.p=push p", "git --no-pager -c alias.p=push p",
                    "git -c ALIAS.p=push p", "git -c Include.Path=/tmp/x v", "git -c alias.p p"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_the_config_env_forms(self):
        for cmd in ("git --config-env alias.p=E p", "git --config-env=alias.p=E p", "git --config-env include.path=E v",
                    "git --config-env=includeIf.gitdir:/x/.path=E v"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_the_alias_value_is_not_inspected(self):
        # A value naming a write, another alias, a `!` shell command, or an alias to a read: refused on the definition alone.
        for cmd in ("git -c alias.p='push --force' p", "git -c alias.a=co p", "git -c alias.x='!echo hi' x",
                    "git -c alias.x='!git push' x", "git -c alias.st=status st", "git -c alias.l=log l"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_env_config_variable_forms(self):
        for cmd in ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=push git p",
                    "GIT_CONFIG_PARAMETERS=\"'alias.p=push'\" git p", "GIT_CONFIG=/tmp/x git v",
                    "GIT_CONFIG_GLOBAL=/tmp/x git v", "GIT_CONFIG_SYSTEM=/tmp/x git v",
                    "export GIT_CONFIG_GLOBAL=/tmp/x; git v", "export GIT_CONFIG_COUNT=1; git p",
                    "env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=push git p",
                    "env GIT_CONFIG_GLOBAL=/tmp/x git v", "/usr/bin/env GIT_CONFIG=/tmp/x git v"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_config_forms_inside_shell_strings_eval_and_subshells(self):
        for cmd in ("sh -c 'git -c alias.p=push p'", "bash -c \"git -c alias.p=push p\"", "zsh -c 'git -c alias.p=push p'",
                    "eval 'git -c alias.p=push p'", "(git -c alias.p=push p)", "echo $(git -c alias.p=push p)",
                    "env sh -c 'git -c alias.p=push p'", "sh -c 'GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=push git p'",
                    "true && git -c alias.p=push p"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_a_defined_alias_beside_a_write_verb_still_refuses(self):
        # The git-config finding dominates; the command is refused whatever the verb the hook reads.
        for cmd in ("git -c alias.p=push commit -m x", "git -c alias.x='!echo hi' status"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_non_alias_c_config_stays_silent(self):
        # SPD-046 narrowed this: only inert, non-program config stays silent (the full allowlist is exercised by GitProgramTest);
        # user.name, commit.gpgsign and http.sslVerify now refuse as non-allowlisted keys.  The alias split of SPD-044 still holds.
        for ok in ("git -c color.ui=never status", "git -c core.pager=cat diff", "git -c core.quotepath=false log",
                   "git -C . -c color.ui=never log"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        # Inert non-alias config does not mask a real write verb: the verb is still read and refused as a git verb.
        r = self.assertRefused("git -c color.ui=never commit -m y", "Law 7")
        self.assertIn("git commit", r.reason)

    def test_a_bare_env_var_that_only_looks_like_git_config_stays_silent(self):
        # GIT_CONFIG_NOSYSTEM disables system config, it does not inject any; an unrelated variable is not git config; and a pager
        # set to `cat` is inert (SPD-046).  GIT_EDITOR and other program-naming variables now refuse -- see GitProgramTest.
        for ok in ("GIT_CONFIG_NOSYSTEM=1 git status", "GIT_PAGER=cat git log", "FOO=1 git status", "env FOO=1 git status"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_globs_in_config_still_refuse(self):
        # SPD-041's glob readings still apply: a glob in the verb or the value is read, and an alias glob refuses.
        for cmd in ("git -c k=v c?mmit -m x", "git -c alias.p=p?sh p", "git -c alias.{p,q}=push p"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_reads_and_writes_without_config_are_unchanged(self):
        for ok in ("git status", "git log --oneline -5", "git diff", "git -C . status", "git show HEAD"):
            with self.subTest(ok):
                self.assertSilent(ok)
        # `git -C /tmp status` was silent here until SPD-047: /tmp lies outside every checkout the ledger knows, and git reads
        # the config of whatever repository it finds there (see GitAliasFileTest).
        self.assertIn("outside", self.assertRefused("git -C /tmp status", "Law 7").reason)
        for verb in ("push", "commit -m x", "merge x"):
            with self.subTest(verb):
                self.assertRefused("git " + verb, "Law 7")


class GitProgramTest(BashHookCase):
    """SPD-046: git config and the environment can name a program git runs -- a pager, editor, ssh or proxy command, diff or
    merge driver, hooks or exec path, credential or askpass helper -- and some verb options name one too, any of which git runs
    under a verb Law 7's table allows.  git_verb reads only the verb, so `git -c core.pager=cmd log`, `GIT_SSH_COMMAND=cmd git
    fetch` and `git ls-remote --upload-pack=cmd host:r` ran the program while the hook saw an allowed read.  Probed (git 2.50.1,
    scratch throwaway repo, markers only in the scratchpad, never a real repo or remote): core.sshCommand, diff.external,
    core.pager, GIT_SSH_COMMAND, GIT_EXTERNAL_DIFF and GIT_PAGER each ran the named program, as did ls-remote/fetch
    --upload-pack, grep -O/--open-files-in-pager, difftool -x/--extcmd and archive --exec; `-c core.pager=cat` ran cat and stayed
    inert.  git help --config lists ~950 keys whose program-naming members are scattered across many sections, so the fix
    allowlists inert keys (color/advice/i18n/column sections; core.quotepath, core.abbrev, log.date, safe.directory; core.pager
    and pager.<cmd> only with an empty or `cat` value, only in the `-c` form the hook can read) and refuses everything else as a
    new git-program finding with its own reason, distinct from SPD-044's git-config alias reason.  It does not inspect the value,
    so it over-refuses an inert program (an editor set to `true`), which a member never needs.  Round 2 (probed by Spud, then me)
    also closes: abbreviated long options (git accepts any unambiguous prefix, `--upload` == --upload-pack); clustered short
    options (`-nO<cmd>`); the global `--exec-path=<dir>` (the command-line form of GIT_EXEC_PATH; bare `--exec-path` prints the
    path and stays silent); and GIT_ALLOW_PROTOCOL, which enables the ext:: transport whose URL is a command.  AGENT_A plans
    tests/** and bin/spud; AGENT_C plans **; Spud (no agent_id) is not bound by Law 7 and sees no change."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7", cwd=None):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id, cwd)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None, cwd=cwd)  # Law 7 does not bind Spud
        return r

    def finding(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path))).findings

    def test_program_config_keys_by_c_are_refused(self):
        for cmd in ("git -c core.pager=less log", "git -c core.editor=vi log", "git -c sequence.editor=vi status",
                    "git -c core.sshCommand=cmd fetch", "git -c core.hooksPath=/tmp/h status", "git -c core.fsmonitor=cmd status",
                    "git -c core.alternateRefsCommand=cmd log", "git -c core.gitProxy=cmd fetch", "git -c core.askPass=cmd fetch",
                    "git -c diff.external=cmd diff", "git -c diff.mine.command=cmd diff", "git -c diff.mine.textconv=cmd diff",
                    "git -c filter.f.clean=cmd status", "git -c filter.f.smudge=cmd status", "git -c credential.helper=cmd fetch",
                    "git -c gpg.program=cmd log", "git -c gpg.ssh.defaultKeyCommand=cmd log", "git -c log.showSignature=true log",
                    "git -c uploadpack.packObjectsHook=cmd fetch", "git -c protocol.ext.allow=always fetch",
                    "git -c url.x.insteadOf=y fetch", "git -c difftool.t.cmd=cmd status", "git -c mergetool.t.cmd=cmd status",
                    "git -c pager.log=less log", "git -c core.pager=less commit -m x"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_program_config_keys_by_config_env_are_refused(self):
        # --config-env reads the value from an environment variable the hook cannot see, so even core.pager is never inert here.
        for cmd in ("git --config-env core.pager=E log", "git --config-env=core.pager=E log",
                    "git --config-env core.sshCommand=E fetch", "git --config-env=core.editor=E log"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_the_finding_names_the_key_var_or_option(self):
        self.assertEqual(self.finding("git -c core.pager=less log"), [("git-program", "-c core.pager=less")])
        self.assertEqual(self.finding("GIT_SSH_COMMAND=cmd git fetch"), [("git-program", "GIT_SSH_COMMAND")])
        self.assertEqual(self.finding("git ls-remote --upload-pack=cmd host:r"),
                         [("git-program", "ls-remote --upload-pack=cmd")])
        r = self.refused_for_members("git -c core.sshCommand=cmd fetch")
        self.assertIn("core.sshCommand", r.reason)
        self.assertNotIn("alias", r.reason)  # its own reason, not SPD-044's git-config alias reason

    def test_program_env_vars_are_refused(self):
        for cmd in ("GIT_EDITOR=vi git log", "GIT_SEQUENCE_EDITOR=vi git log", "EDITOR=vi git log", "VISUAL=vi git log",
                    "GIT_SSH=cmd git fetch", "GIT_SSH_COMMAND=cmd git fetch", "GIT_EXTERNAL_DIFF=cmd git diff",
                    "GIT_ASKPASS=cmd git fetch", "SSH_ASKPASS=cmd git fetch", "GIT_PROXY_COMMAND=cmd git fetch",
                    "GIT_EXEC_PATH=/x git status", "GIT_TEMPLATE_DIR=/x git status", "GIT_PAGER=less git log",
                    "PAGER=less git log"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_program_env_vars_via_export_and_env_wrapper(self):
        for cmd in ("export GIT_SSH_COMMAND=cmd; git fetch", "env GIT_EDITOR=vi git log",
                    "/usr/bin/env GIT_PAGER=less git log", "env GIT_SSH_COMMAND=cmd sh -c 'git fetch'"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_verb_options_that_name_a_program_are_refused(self):
        for cmd in ("git ls-remote --upload-pack=cmd host:r", "git ls-remote --upload-pack cmd host:r",
                    "git fetch --upload-pack=cmd r", "git fetch --upload-pack cmd r", "git grep -Ocat foo", "git grep -O foo",
                    "git grep --open-files-in-pager=cat foo", "git grep --open-files-in-pager foo", "git difftool -x cmd A B",
                    "git difftool --extcmd=cmd A B", "git archive --remote=r --exec=cmd HEAD"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_abbreviated_long_options_are_refused(self):
        # git's parse-options accepts any unambiguous prefix (`git ls-remote --upload=cmd .` runs cmd, probed); refuse every prefix.
        for cmd in ("git ls-remote --upload=cmd .", "git ls-remote --up=cmd .", "git ls-remote --upload-pac cmd .",
                    "git fetch --upload=cmd r", "git grep --open=cat foo", "git grep --open-files=cat foo",
                    "git difftool --ext=cmd A B", "git difftool --extc cmd A B", "git archive --exe=cmd HEAD",
                    "git archive --exec cmd HEAD"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_clustered_short_options_are_refused(self):
        # A short cluster carrying the program letter runs it (`git grep -nO<cmd> hi`, probed); refuse the letter anywhere in the
        # cluster, value attached or not.  A false refusal of a pattern value is acceptable -- the hook fails closed.
        for cmd in ("git grep -nOcat foo", "git grep -nO foo", "git grep -inOcat foo", "git difftool -dx cmd A B",
                    "git difftool -yx cmd A B"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_exec_path_global_option_is_refused(self):
        # `git --exec-path=<dir> ls-remote https://x` runs <dir>/git-remote-https (probed): the command-line form of GIT_EXEC_PATH.
        for cmd in ("git --exec-path=/dir ls-remote x", "git --exec=/dir fetch", "git --exec-p=/dir status",
                    "git --exec-path=/dir status"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        self.assertEqual(self.finding("git --exec-path=/dir status"), [("git-program", "--exec-path=/dir")])

    def test_git_allow_protocol_env_is_refused(self):
        # GIT_ALLOW_PROTOCOL enables the ext:: transport, whose URL is a command git runs.
        for cmd in ("GIT_ALLOW_PROTOCOL=ext git fetch x", "env GIT_ALLOW_PROTOCOL=ext git fetch x",
                    "export GIT_ALLOW_PROTOCOL=ext; git fetch x"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_program_forms_inside_shell_strings_eval_and_subshells(self):
        for cmd in ("sh -c 'git -c core.pager=less log'", "bash -c \"GIT_EDITOR=vi git log\"",
                    "zsh -c 'git ls-remote --upload-pack=cmd host:r'", "eval 'git -c core.sshCommand=cmd fetch'",
                    "(git -c core.editor=vi log)", "echo $(git fetch --upload-pack=cmd r)", "true && GIT_PAGER=less git log"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_a_program_form_beside_a_write_verb_still_refuses(self):
        for cmd in ("git -c core.pager=less commit -m x", "GIT_EDITOR=vi git commit", "git -c core.sshCommand=cmd push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_inert_config_keys_stay_silent(self):
        for ok in ("git -c color.ui=never status", "git -c color.diff.new=green log", "git -c advice.detachedHead=false status",
                   "git -c i18n.logOutputEncoding=utf-8 log", "git -c column.ui=auto status", "git -c core.quotepath=false status",
                   "git -c core.abbrev=12 log", "git -c log.date=iso log", "git -c safe.directory=/x status",
                   "git -c core.pager=cat diff", "git -c core.pager= diff", "git -c pager.log=cat log", "git -c pager.diff= diff",
                   "git -C . -c color.ui=never log"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_inert_pager_env_vars_stay_silent(self):
        for ok in ("GIT_PAGER=cat git log", "PAGER=cat git log", "GIT_PAGER= git log", "env GIT_PAGER=cat git log",
                   "export GIT_PAGER=cat; git log", "GIT_CONFIG_NOSYSTEM=1 git status", "FOO=1 git status", "env FOO=1 git status"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_allowed_verbs_without_a_program_option_stay_silent(self):
        for ok in ("git fetch", "git fetch origin main", "git ls-remote host:r", "git grep x", "git grep -n foo",
                   "git grep -i foo", "git grep -ni foo",
                   # `git difftool` runs the tool diff.tool names whether or not -x is on the line, so SPD-087 refuses
                   # the verb to a member whole; what is pinned here is that no option of it earns a git-program finding
                   "git archive HEAD", "git grep -- -Ofoo",  # after --, -Ofoo is a pattern, not grep -O
                   # options adjacent to a program option but not a prefix of it stay silent
                   "git ls-remote --tags .", "git fetch --unshallow r", "git fetch --update-head-ok r",
                   "git --exec-path", "git --exec-path status"):  # bare --exec-path prints the path, it does not set one
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        for refused in ("git difftool A B", "git difftool --no-prompt A B", "git difftool -d A B"):
            with self.subTest(refused):
                self.assertRefused(refused, "Law 7")  # SPD-087: the verb, with no program option to name
                self.assertSilent(refused, agent_id=None)


class GitAliasFileTest(BashHookCase):
    """SPD-047: git also loads aliases -- and every program-naming key of SPD-046's class -- from config files the hook never
    opens, so a member who plants `[alias] p = push` in one runs a push under a verb Law 7's table does not list.  Probed
    (git 2.54.0, Apple Git-157, a hand-built .git in the scratchpad, read-only `version` as the alias value, nothing committed):
    `HOME=<dir> git v`, `XDG_CONFIG_HOME=<dir> git vv`, `git -C <repo> w`, `git --git-dir=<repo>/.git w` (spaced and `=`) and
    `GIT_DIR=<repo>/.git git w` each expanded an alias defined in the file that setting points git at.  git ignores an alias
    that hides one of its own commands (`alias.log = !echo SHADOWED` and `alias.status` were ignored; git-config(1): "aliases
    that hide existing Git commands are ignored"), so an alias can only introduce a verb git does not have, and a verb outside
    git's own command list is the tell for every alias source at once, on disk or not.  `git --list-cmds=main` is that set (174
    names here, a superset of --list-cmds=builtins' 147); it holds neither an alias in force nor an external `git-<verb>` on
    PATH (`--list-cmds=others`, which the hook cannot read either: `PATH=<dir>:$PATH git foo` ran <dir>/git-foo).

    Three refusals, for a member only (Spud's session is unaffected): an unknown verb; HOME or XDG_CONFIG_HOME in force on the
    line, refused like GIT_CONFIG_GLOBAL, since the file they name can also set core.pager and the rest of SPD-046's class under
    a real verb; and a repository the hook cannot read, named by -C, --git-dir, --work-tree, GIT_DIR, GIT_WORK_TREE or
    GIT_COMMON_DIR, when it lies outside every checkout the ledger knows.  git rejects `-C<dir>`, `-C=<dir>` and abbreviations
    of the global options (`--git-di=`, probed), so only the spelled forms parse; `-C` is repeatable and relative to the
    previous one, and it moves what a relative --git-dir means whichever order they appear in (probed, and git(1) documents the
    equivalence).  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        # A repository outside every checkout the ledger knows, built by hand (git init is a write verb): git reads its
        # config, and the hook cannot.
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        self.repo = self.out / "repo"
        (self.repo / ".git" / "objects").mkdir(parents=True)
        (self.repo / ".git" / "refs" / "heads").mkdir(parents=True)
        (self.repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (self.repo / ".git" / "config").write_text("[core]\n\trepositoryformatversion = 0\n[alias]\n\tp = push\n", encoding="utf-8")

    def refused_for_members(self, command, needle="Law 7", cwd=None):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id, cwd)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None, cwd=cwd)  # Law 7 does not bind Spud
        return r

    def finding(self, command, cwd=None):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=cwd or str(self.home.path))).findings

    # -- 1. an unknown verb -------------------------------------------------------
    def test_the_tickets_evidence_commands(self):
        # Each of these ran an alias defined in a file the hook cannot read; the verb is the tell.
        for cmd in ("git p", "git -C %s p" % self.repo, "git --git-dir=%s/.git p" % self.repo,
                    "GIT_DIR=%s/.git git p" % self.repo):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("git p", r.reason)
        self.assertEqual(self.finding("git p"), [("git-verb", ("verb", "p"))])
        # `HOME=<dir> git p` is refused too, but by the config-file finding, which comes first and names the variable: the
        # unknown verb is the hook's last resort, so a refusal the words as spelled already earn keeps its own reason.
        r = self.refused_for_members("HOME=%s git p" % self.out)
        self.assertIn("HOME", r.reason)

    def test_an_unknown_verb_is_refused_and_named(self):
        for verb in ("p", "foo", "v", "w", "st", "co", "lg", "ci", "amend", "pushf", "git-foo", "Status", "STATUS"):
            with self.subTest(verb):
                r = self.refused_for_members("git " + verb)
                self.assertIn("git " + verb, r.reason)
                self.assertIn("alias", r.reason)
        # An external `git-<verb>` on PATH is the same hole and the same tell.
        r = self.refused_for_members("git foo")
        self.assertIn("git-foo", r.reason)

    def test_an_unknown_verb_behind_global_options_and_in_shell_constructs(self):
        for cmd in ("git --no-pager p", "git -c color.ui=never p", "git -C . p", "sh -c 'git p'", "bash -c \"git p\"",
                    "eval 'git p'", "(git p)", "echo $(git p)", "true && git p", "env git p", "command git p",
                    "/usr/bin/git p", "git --literal-pathspecs p"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_gits_own_read_verbs_stay_silent(self):
        for ok in ("git status", "git log --oneline -5", "git diff", "git show HEAD", "git blame f", "git rev-parse HEAD",
                   "git ls-files", "git grep x", "git stash list", "git fetch", "git remote -v", "git worktree list",
                   "git worktree list --porcelain", "git for-each-ref", "git for-each-ref --format=%(refname)",
                   "git rev-list --count HEAD", "git ls-remote host:r", "git count-objects -v", "git describe --tags",
                   "git shortlog -sn", "git show-ref", "git cat-file -p HEAD", "git check-ignore x", "git merge-base a b",
                   "git name-rev HEAD", "git whatchanged", "git range-diff a b c", "git diff-tree HEAD", "git var GIT_AUTHOR_IDENT",
                   "git verify-tag v1", "git help status", "git version", "git --version", "git --help", "git"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_write_verbs_keep_their_spd_044_era_reason(self):
        for verb in ("commit -m x", "add .", "push", "merge x", "rebase main", "worktree add ../x", "branch -D x",
                     "stash", "config user.name x"):
            with self.subTest(verb):
                r = self.assertRefused("git " + verb, "Law 7")
                self.assertIn("git " + verb.split()[0], r.reason)
                self.assertIn("Spud commits", r.reason)
                self.assertNotIn("not one of git's own", r.reason)

    def test_the_command_list_is_read_from_git_and_fails_closed(self):
        # The hook reads `git --list-cmds=main` with its own sanitised environment; when git cannot be run at all it refuses
        # every verb it cannot check, saying so.  (PATH without git: the CLI itself runs by absolute path.)
        m = load_spud_module()
        self.assertIn("status", m.git_own_commands())
        self.assertNotIn("p", m.git_own_commands())
        path = self.home.env.get("PATH")
        self.home.env["PATH"] = str(self.out / "no-git-here")
        try:
            r = self.assertRefused("git status", "Law 7")
            self.assertIn("--list-cmds=main", r.reason)
            self.assertSilent("git status", agent_id=None)  # Law 7 does not bind Spud
            r = self.assertRefused("git p", "Law 7")
            self.assertIn("--list-cmds=main", r.reason)
            self.assertRefused("git push", "spudagents never run")  # a real write verb keeps its own reason
        finally:
            if path is None:
                self.home.env.pop("PATH", None)
            else:
                self.home.env["PATH"] = path
        self.assertSilent("git status")

    # -- 2. HOME and XDG_CONFIG_HOME ---------------------------------------------
    def test_home_and_xdg_config_home_in_force_are_refused(self):
        for cmd in ("HOME=/tmp/x git status", "XDG_CONFIG_HOME=/tmp/x git status", "HOME=%s git log" % self.out,
                    "export HOME=/tmp/x; git status", "export XDG_CONFIG_HOME=/tmp/x; git diff",
                    "env HOME=/tmp/x git status", "env XDG_CONFIG_HOME=/tmp/x git status",
                    "/usr/bin/env HOME=/tmp/x git status", "HOME=/tmp/x XDG_CONFIG_HOME=/tmp/y git status",
                    "sh -c 'HOME=/tmp/x git status'", "HOME=/tmp/x git commit -m y"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("HOME", r.reason)
        self.assertEqual(self.finding("HOME=/tmp/x git status"), [("git-config", "HOME")])
        self.assertEqual(self.finding("XDG_CONFIG_HOME=/tmp/x git status"), [("git-config", "XDG_CONFIG_HOME")])
        r = self.refused_for_members("HOME=/tmp/x git status")
        self.assertIn("alias", r.reason)  # the git-config reason of SPD-044, now naming the home variables too

    def test_a_home_expansion_inside_a_path_stays_silent(self):
        # Only an assignment puts HOME in force; `$HOME` used in a path is an ordinary word.
        for ok in ("git log -1 $HOME", "git log -- $HOME/x", "echo $HOME", "cat $HOME/.gitconfig", "ls $XDG_CONFIG_HOME",
                   "git diff HEAD -- $HOME"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        for ok in ("SPUD_HOME=/tmp/x git status", "HOMEBREW_PREFIX=/tmp/x git status", "MY_HOME=/tmp/x git status",
                   "XDG_DATA_HOME=/tmp/x git status", "HOME_DIR=/tmp/x git status"):
            with self.subTest(ok):
                self.assertSilent(ok)

    # -- 3. another repository ----------------------------------------------------
    def test_a_repository_outside_every_known_checkout_is_refused(self):
        for cmd in ("git -C %s status" % self.repo, "git -C /tmp status", "git -C /tmp log --oneline",
                    "git --git-dir=%s/.git status" % self.repo, "git --git-dir %s/.git status" % self.repo,
                    "git --work-tree=%s status" % self.repo, "git --work-tree %s status" % self.repo,
                    "GIT_DIR=%s/.git git status" % self.repo, "GIT_WORK_TREE=%s git status" % self.repo,
                    "GIT_COMMON_DIR=%s/.git git status" % self.repo,
                    "env GIT_DIR=%s/.git git status" % self.repo, "export GIT_DIR=/tmp/x/.git; git status",
                    "git -C /tmp -c color.ui=never status", "sh -c 'git -C /tmp status'", "(git -C /tmp status)",
                    "cd /tmp && git -C . status", "git -C ../.. status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("outside", r.reason)

    def test_the_repository_finding_names_the_directory_and_the_chain(self):
        self.assertEqual(self.finding("git -C /tmp status"),
                         [("git", ("status", None)), ("git-repo", ("-C /tmp", "/tmp", frozenset({str(self.home.path)})))])
        # -C is repeatable and each is relative to the previous, so only the composed directory is the one git reads in.
        r = self.refused_for_members("git -C /tmp -C sub status")
        self.assertIn("/tmp/sub", r.reason)
        self.assertNotIn("outside", self.assertRefused("git -C /tmp commit -m x", "Law 7").reason)  # the verb refuses first

    def test_a_target_inside_the_session_checkout_stays_silent(self):
        home = self.home.path
        (home / ".claude" / "worktrees" / "w" / "bin").mkdir(parents=True)
        for ok in ("git -C %s status" % home, "git -C . status", "git -C tests status", "git -C ./tests log",
                   "git -C %s/tests status" % home, "git --git-dir=%s/.git status" % home,
                   "git --git-dir %s/.git log" % home, "git --work-tree=%s status" % home,
                   "GIT_DIR=%s/.git git status" % home, "GIT_WORK_TREE=%s git status" % home,
                   "git -C %s -C tests status" % home, "cd %s/tests && git -C . status" % home,
                   "git -C %s --git-dir=.git status" % home, "git status", "git log",
                   # the common one: a worktree of the session's own checkout
                   "git -C %s/.claude/worktrees/w status" % home, "git -C %s/.claude/worktrees/w/bin log" % home):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_a_target_the_hook_cannot_resolve_is_refused(self):
        # A relative target from a directory the hook cannot follow, and a value it cannot read.
        for cmd in ("cd - && git -C sub status", "popd && git -C sub status", "cd ~x && git -C sub status",
                    "git -C '~x' status", "GIT_DIR=$D git status", "GIT_DIR=`echo /tmp` git status"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "cannot")
        # A variable in an option git reads by name was refused as an unresolvable word (SPD-043) and now names the
        # repository instead: the option is read first, and its reason says what to do about the path.
        for cmd in ("git -C $D status", "git --git-dir=$D status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd, "cannot resolve")
                self.assertIn("points git at a repository", r.reason)

    def test_spud_is_unaffected_by_all_of_it(self):
        for ok in ("git p", "git foo", "git -C /tmp status", "git -C %s p" % self.repo, "HOME=/tmp/x git status",
                   "XDG_CONFIG_HOME=/tmp/x git log", "GIT_DIR=/tmp/x/.git git status", "git --work-tree=/tmp status",
                   "cd - && git -C sub status"):
            with self.subTest(ok):
                self.assertSilent(ok, agent_id=None)


# SPD-063: the refusal needles.  (a) a file git reads with nothing on the line; (b) a program-naming key in force at the
# target repository's local or worktree scope.
GIT_FILE_WORDING = "git reads with nothing on the line"
GIT_SCOPE_WORDING = "scope (the repository in"
# SPD-066: (1) a path with a .git component; (2) a repository that is not the own repository of a checkout the ledger knows.
GIT_DIR_WORDING = "is inside a git directory"
GIT_NESTED_WORDING = "not the own repository of a checkout the ledger knows"


def plant_git_dir(path, config="[core]\n\trepositoryformatversion = 0\n"):
    """A git directory built by hand, as a member could build one without git init (a write verb): HEAD, objects/, refs/
    and a config.  Put at <dir>/.git it makes <dir> a work tree; anywhere else it is a bare layout git discovers."""
    (path / "objects").mkdir(parents=True)
    (path / "refs" / "heads").mkdir(parents=True)
    (path / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (path / "config").write_text(config, encoding="utf-8")
    return path


class GitConfigFileTest(BashHookCase):
    """SPD-063 (a): SPD-064 closes a member's writes to ~/.gitconfig and $XDG_CONFIG_HOME/git/config, but the local scope
    stays open -- a `.git/config` (or `.git/config.worktree`, or a file an `include.path` there names) inside a checkout the
    ledger knows, which a member can craft under its own deliverable globs and git then reads with nothing on the line.  So
    the edit hook, and with it the Bash hook's redirection and tee check, refuses a caller with an agent_id any file named
    `.gitconfig` or whose path ends in `.git/config`, `.git/config.worktree` or `git/config`, anywhere, deliverables
    included.  And `git config edit` -- the 2.46 subcommand syntax, one positional -- slipped through git_refused, which
    counted positionals; the writing subcommands are named now, and `git config get <key>` and `--get-urlmatch <name> <url>`,
    two reads git_refused counted as a key and a value, are silent.  Probed on git 2.54.0 (Apple Git-157)."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.tests = self.home.path / "tests"
        self.tests.mkdir(exist_ok=True)

    def config_files(self):
        t = self.tests
        return [t / ".git" / "config", t / "fake" / ".git" / "config", t / "fake" / ".git" / "config.worktree",
                t / ".gitconfig", t / "sub" / ".gitconfig", t / "git" / "config", t / "xdg" / "git" / "config"]

    def test_a_member_may_not_write_a_git_config_file_even_inside_its_globs(self):
        for p in self.config_files():
            with self.subTest(str(p)):
                for agent_id in (AGENT_A, AGENT_C, AGENT_D):
                    r = self.assertRefused("echo x > %s" % p, GIT_FILE_WORDING, agent_id=agent_id)
                    self.assertIn("Law 7", r.reason)
                    self.assertIn(str(p), r.reason)
                r = self.home.hook("PreToolUse", self.pre_edit(p, agent_id=AGENT_A))
                self.assertEqual((r.code, r.decision), (0, "deny"), (str(p), r))
                self.assertIn(GIT_FILE_WORDING, r.reason)
        self.assertEqual(self.wide["deliverables"], ["home:**"])

    def test_every_edit_tool_and_every_redirection_form(self):
        p = self.tests / ".git" / "config"
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            with self.subTest(tool):
                r = self.home.hook("PreToolUse", self.pre_edit(p, agent_id=AGENT_A, tool=tool))
                self.assertIn(GIT_FILE_WORDING, r.reason)
        commands = [form % p for form in ("echo x > %s", "echo x >> %s", "printf x | tee %s", "printf x | tee -a %s")]
        commands.append("cd %s/tests && echo x > .git/config" % self.home.path)
        for command in commands:
            with self.subTest(command):
                self.assertRefused(command, GIT_FILE_WORDING)

    def test_case_variants_are_refused_too(self):
        for p in (self.tests / ".GITCONFIG", self.tests / ".Git" / "Config", self.tests / "GIT" / "CONFIG"):
            with self.subTest(str(p)):
                self.assertRefused("echo x > %s" % p, GIT_FILE_WORDING)

    def test_similar_names_stay_under_the_globs(self):
        # .git/hooks/x stood here until SPD-066, which refuses every path with a .git component (GitDirectoryPathTest)
        for p in (self.tests / "config", self.tests / "gitconfig", self.tests / "x.gitconfig", self.tests / ".gitconfig.bak",
                  self.tests / ".github" / "config", self.tests / "git" / "config.json", self.tests / "notgit" / "config"):
            with self.subTest(str(p)):
                self.assertSilent("echo x > %s" % p)
                r = self.home.hook("PreToolUse", self.pre_edit(p, agent_id=AGENT_A))
                self.assertEqual((r.code, r.stdout), (0, ""), (str(p), r))

    def test_spud_is_not_bound_by_it(self):
        for p in self.config_files():
            with self.subTest(str(p)):
                r = self.home.hook("PreToolUse", self.pre_edit(p, agent_id=None))
                self.assertNotIn(GIT_FILE_WORDING, r.reason)  # Law 1 may refuse it in the home; never this reason
                self.assertSilent("echo x > /private/tmp/claude-%d/x/.gitconfig" % os.getuid(), agent_id=None)

    # -- `git config` itself ------------------------------------------------------
    def test_the_writing_forms_of_git_config_are_refused(self):
        for cmd in ("git config user.name x", "git config --add a.b c", "git config --append a.b c",
                    "git config --unset a.b", "git config --unset-all a.b", "git config --replace-all a.b c",
                    "git config --rename-section a b", "git config --remove-section a", "git config --edit",
                    "git config -e", "git config --global --edit", "git config --local core.pager less",
                    "git config set core.pager x", "git config unset core.pager", "git config edit",
                    "git config rename-section a b", "git config remove-section a", "git config --file f core.pager x",
                    "git config set --all core.pager x"):
            with self.subTest(cmd):
                for agent_id in (AGENT_A, AGENT_C):
                    r = self.assertRefused(cmd, "Law 7", agent_id=agent_id)
                    self.assertIn("git config", r.reason)
                self.assertSilent(cmd, agent_id=None)

    def test_the_reading_forms_of_git_config_stay_silent(self):
        for cmd in ("git config --get core.pager", "git config --get-all a.b", "git config --get-regexp a",
                    "git config --list", "git config -l", "git config core.pager", "git config list",
                    "git config get core.pager", "git config --list --show-scope --name-only",
                    "git config --get-colorbool color.ui", "git config --get-urlmatch a https://x",
                    "git config -f f core.pager", "git config --global --list", "git config get --all a.b"):
            with self.subTest(cmd):
                self.assertSilent(cmd)
                self.assertSilent(cmd, agent_id=None)


class GitDirectoryPathTest(BashHookCase):
    """SPD-066 (1): SPD-063 closed the config files git reads by itself, and the rest of a git directory stayed a member's to
    write under its globs: .git/hooks/* (git runs a hook with nothing on the line and no config key naming it -- probed on
    git 2.54.0, every `git status` runs post-index-change and every `git fetch` runs reference-transaction),
    .git/info/attributes and info/exclude, .git/shallow, the index, and a worktree's .git gitfile, which points git at any
    git directory.  So the path rule refuses a caller with an agent_id any path with a `.git` component, by any reading of
    it, anywhere, its own deliverables included, matched case-folded like git_config_file; a .git/config keeps SPD-063's
    more specific reason.  The Edit tools and every Bash write target that goes through edit_reason (a redirection, tee,
    SPD-049's file-writing git options) refuse it alike.  .gitignore, .github/, .gitattributes and the other names that
    merely begin with .git stay under the globs, and Spud's answers do not change.  AGENT_A plans home:tests/** and
    home:bin/spud; AGENT_C plans home:**."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.tests = self.home.path / "tests"
        self.fake = self.tests / "fake"
        (self.fake / ".git" / "hooks").mkdir(parents=True)
        (self.fake / ".git" / "info").mkdir()

    def git_dir_paths(self):
        g = self.fake / ".git"
        return [g / "hooks" / "pre-auto-gc", g / "hooks" / "post-index-change", g / "hooks" / "reference-transaction",
                g / "info" / "attributes", g / "info" / "exclude", g / "index", g / "shallow", g / "HEAD",
                g / "objects" / "17" / "0123abcd", g / "refs" / "heads" / "main", g / "modules" / "m" / "hooks" / "pre-auto-gc",
                g, self.tests / "sub" / ".git", self.tests / ".git"]

    def assertEditRefused(self, path, needle, agent_id=AGENT_A, tool="Write"):
        r = self.home.hook("PreToolUse", self.pre_edit(path, agent_id=agent_id, tool=tool))
        self.assertEqual((r.code, r.decision), (0, "deny"), (str(path), agent_id, tool, r))
        self.assertIn(needle, r.reason, (str(path), agent_id, tool))
        return r

    def assertEditSilent(self, path, agent_id=AGENT_A):
        r = self.home.hook("PreToolUse", self.pre_edit(path, agent_id=agent_id))
        self.assertEqual((r.code, r.stdout), (0, ""), (str(path), agent_id, r))

    def test_a_member_may_not_write_under_a_git_directory_even_inside_its_globs(self):
        for p in self.git_dir_paths():
            with self.subTest(str(p)):
                for agent_id in (AGENT_A, AGENT_C):
                    r = self.assertEditRefused(p, GIT_DIR_WORDING, agent_id)
                    self.assertIn("Law 7", r.reason)
                    self.assertIn(str(p), r.reason)  # the reason names the path
                    r = self.assertRefused("echo x > %s" % p, GIT_DIR_WORDING, agent_id=agent_id)
                    self.assertIn(str(p), r.reason)
        self.assertEqual(self.wide["deliverables"], ["home:**"])  # not even a ** member

    def test_the_reason_says_why(self):
        r = self.assertEditRefused(self.fake / ".git" / "hooks" / "pre-auto-gc", GIT_DIR_WORDING)
        for word in ("hook", "attributes", "index", "gitfile", "nothing on the line", "Spud commits"):
            self.assertIn(word, r.reason)

    def test_every_edit_tool_and_every_bash_write_target(self):
        p = self.fake / ".git" / "hooks" / "pre-auto-gc"
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            with self.subTest(tool):
                self.assertEditRefused(p, GIT_DIR_WORDING, tool=tool)
        commands = [form % p for form in ("echo x > %s", "echo x >> %s", "printf x | tee %s", "printf x | tee -a %s",
                                          "printf x | tee /dev/null %s", "echo x 2> %s", "cat > %s <<< x")]
        commands += ["cd %s && echo x > .git/hooks/pre-auto-gc" % self.fake, "cd %s/.git && echo x > index" % self.fake,
                     "cd %s && printf x | tee .git/info/attributes" % self.fake,
                     "sh -c 'echo x > %s'" % p,
                     "git diff --output=%s" % p]  # SPD-049: a file a git option writes goes through the same rule
        for command in commands:
            with self.subTest(command):
                self.assertRefused(command, GIT_DIR_WORDING)
                self.assertRefused(command, GIT_DIR_WORDING, agent_id=AGENT_C)

    def test_case_variants_are_refused_too(self):
        for p in (self.fake / ".GIT" / "hooks" / "pre-auto-gc", self.fake / ".Git" / "index", self.tests / ".gIt",
                  self.tests / "sub" / ".GIT" / "info" / "attributes"):
            with self.subTest(str(p)):
                self.assertRefused("echo x > %s" % p, GIT_DIR_WORDING)
                self.assertEditRefused(p, GIT_DIR_WORDING)

    def test_a_symlink_reading_is_refused(self):
        """By any reading of the target: a link whose own path has no .git component but whose real path does."""
        (self.tests / "link").symlink_to(self.fake / ".git")
        (self.tests / "hooklink").symlink_to(self.fake / ".git" / "hooks" / "post-index-change")  # need not exist
        real = self.fake / ".git"
        for p, resolved in ((self.tests / "link" / "hooks" / "pre-auto-gc", real / "hooks" / "pre-auto-gc"),
                            (self.tests / "hooklink", real / "hooks" / "post-index-change")):
            with self.subTest(str(p)):
                r = self.assertEditRefused(p, GIT_DIR_WORDING)
                self.assertIn(str(resolved), r.reason)  # the reading that has the component
                r = self.assertRefused("echo x > %s" % p, GIT_DIR_WORDING)
                self.assertIn(str(resolved), r.reason)
        (self.tests / "plain").mkdir()
        (self.tests / "plainlink").symlink_to(self.tests / "plain")  # the control: a link to a directory of the tree
        self.assertEditSilent(self.tests / "plainlink" / "x.py")

    def test_similar_names_stay_under_the_globs(self):
        t = self.tests
        similar = [t / ".gitignore", t / ".github" / "workflows" / "ci.yml", t / ".gitattributes", t / ".gitmodules",
                   t / ".gitkeep", t / "x.git" / "hooks" / "pre-auto-gc", t / ".git.bak" / "hooks" / "pre-auto-gc",
                   t / "git" / "hooks" / "pre-auto-gc", t / "dotgit" / "index", t / "hooks" / "pre-auto-gc", t / ".gitx" / "HEAD"]
        for p in similar:
            with self.subTest(str(p)):
                for agent_id in (AGENT_A, AGENT_C):
                    self.assertEditSilent(p, agent_id)
                    self.assertSilent("echo x > %s" % p, agent_id=agent_id)
        # at the home's root they are outside AGENT_A's globs: its answer is the globs', never this rule's
        for p in (self.home.path / ".gitignore", self.home.path / ".github" / "x.yml", self.home.path / ".gitattributes"):
            with self.subTest(str(p)):
                r = self.assertEditRefused(p, "deliverables")
                self.assertNotIn(GIT_DIR_WORDING, r.reason)
                self.assertEditSilent(p, AGENT_C)

    def test_a_git_config_file_keeps_its_own_reason(self):
        """SPD-063's tails stay the more specific reason: a .git/config can also define an alias."""
        for p in (self.fake / ".git" / "config", self.fake / ".git" / "config.worktree", self.fake / ".GIT" / "Config"):
            with self.subTest(str(p)):
                r = self.assertEditRefused(p, GIT_FILE_WORDING)
                self.assertNotIn(GIT_DIR_WORDING, r.reason)
                r = self.assertRefused("echo x > %s" % p, GIT_FILE_WORDING)
                self.assertNotIn(GIT_DIR_WORDING, r.reason)

    def test_the_scratchpad_is_no_exception(self):
        scratch = "/private/tmp/claude-%d/-Users-eric-Personal-Spud/%s/scratchpad" % (os.getuid(), SESSION)
        for p in (scratch + "/repo/.git/hooks/pre-auto-gc", scratch + "/repo/.git", "/tmp/x/.git/index"):
            with self.subTest(p):
                r = self.assertEditRefused(p, GIT_DIR_WORDING)
                self.assertIn(p, r.reason)
                self.assertRefused("echo x > %s" % p, GIT_DIR_WORDING)
                self.assertSilent("echo x > %s" % p, agent_id=None)  # Spud writes there freely, as before
        self.assertEditSilent(scratch + "/repo/notes.md")  # the control

    def test_spud_is_not_bound_by_it(self):
        for p in self.git_dir_paths():
            with self.subTest(str(p)):
                r = self.home.hook("PreToolUse", self.pre_edit(p, agent_id=None))
                self.assertNotIn(GIT_DIR_WORDING, r.reason)  # Law 1 may refuse it in the home; never this reason
                self.assertNotIn(GIT_DIR_WORDING, self.bash("echo x > %s" % p, agent_id=None).reason)

    def test_an_unbound_agent_id_in_a_spud_session_is_held_too(self):
        """The rule binds where SPD-063's does: any agent_id in a Spud session, before its binding too."""
        r = self.assertEditRefused(self.fake / ".git" / "hooks" / "pre-auto-gc", GIT_DIR_WORDING, agent_id=AGENT_D)
        self.assertNotIn("not bound", r.reason)

    def test_a_worktrees_own_gitfile_is_refused(self):
        """The .git of a linked worktree is a gitfile, `gitdir: <path>`, and git follows it to whatever git directory it
        names; a member bound to that very worktree, whose glob is **, is refused it and keeps the rest of the tree."""
        scratch_git(self.home.path, "init", "-q", "-b", "main")
        scratch_git(self.home.path, "commit", "-q", "--allow-empty", "-m", "root")
        wt = self.home.path.parent / ("%s-gitfile" % self.home.path.name)
        self.addCleanup(shutil.rmtree, wt, True)
        scratch_git(self.home.path, "worktree", "add", "-q", "-b", "gitfile", wt)
        self.assertTrue((wt / ".git").is_file(), "a linked worktree's .git is a gitfile")
        self.home.json("member", "finish", self.other["ref"], "--status", "done", "--outcome", "x", actor="spud")  # a slot for D
        self.spawn(self.plan(persona="engineer", model="opus", deliverable=["**"], cwd=wt), AGENT_D)  # SPD-098: binds SPD-001 to wt
        r = self.assertEditRefused(wt / ".git", GIT_DIR_WORDING, agent_id=AGENT_D)
        self.assertIn(str(wt / ".git"), r.reason)
        for command in ("echo gitdir: /tmp/x > %s" % (wt / ".git"), "cd %s && printf x | tee .git" % wt):
            with self.subTest(command):
                self.assertRefused(command, GIT_DIR_WORDING, agent_id=AGENT_D)
        self.assertEditSilent(wt / "tests" / "x.py", agent_id=AGENT_D)  # the control: the rest of its tree
        self.assertEditSilent(wt / ".gitignore", agent_id=AGENT_D)
        self.assertEditRefused(self.home.path / ".git" / "hooks" / "pre-auto-gc", GIT_DIR_WORDING, agent_id=AGENT_C)
        r = self.home.hook("PreToolUse", self.pre_edit(wt / ".git", agent_id=None))
        self.assertIn("Law 1", r.reason)  # Spud's answer in a project checkout, as before
        self.assertNotIn(GIT_DIR_WORDING, r.reason)


class GitLocalConfigTest(BashHookCase):
    """SPD-063 (b): git reads the target repository's own config with nothing on the line, and a member can craft one under
    its deliverable globs inside a checkout the ledger knows (`git -C tests/fake status` resolves inside the home, so
    SPD-047's git-repo refusal, which only fires outside every known checkout, stays silent).  So before a member's git call
    the hook reads the keys in force at that repository's `local` and `worktree` scopes with `git config --list --show-scope
    --name-only`, run there with the module's sanitised environment, and refuses the ones that name or enable a program git
    runs (SPD-046's class) under a verb Law 7's table allows.  The system and global scopes are Eric's own and stay out of
    it: credential.helper is in force at the system scope on this Mac, and this very checkout's local scope holds
    core.filemode, extensions.worktreeconfig, remote.origin.url and branch.main.vscode-merge-base, so a check that refused
    every key outside SPD-046's inert allowlist would refuse every member git call in Spud's own repository.  The answer is
    kept in the home's state directory under the stat fingerprint of the config files git reads there, so only the first
    hook after one of them changes runs git (the hook path never imports subprocess otherwise, SPD-016).

    Since SPD-066 a repository nested in a checkout -- the tests/fake this class planted until then -- is refused before its
    keys are read (NestedRepositoryTest), so the repository here is the checkout's own: the home, which is project spud's
    root in the suite, made a repository by hand.  Its config is still a file no member may write (GitConfigFileTest); the
    check stands for a key Eric or a tool set there."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.repo = self.home.path  # the checkout's own repository: git reads it, the ledger knows it
        plant_git_dir(self.repo / ".git")
        (self.repo / "tests").mkdir()

    def plant(self, text, name="config"):
        (self.repo / ".git" / name).write_text(text, encoding="utf-8")

    def calls(self):
        return ("git -C %s status" % self.repo, "cd %s && git status" % self.repo, "git -C %s log --oneline" % self.repo,
                "git --git-dir=%s/.git status" % self.repo, "cd %s && git diff" % self.repo)

    def assertRepoRefused(self, needle):
        for cmd in self.calls():
            with self.subTest(cmd):
                for agent_id in (AGENT_A, AGENT_C):
                    r = self.assertRefused(cmd, "Law 7", agent_id=agent_id)
                    self.assertIn(needle, r.reason)
                self.assertSilent(cmd, agent_id=None)  # Law 7 does not bind Spud

    def assertRepoSilent(self):
        for cmd in self.calls():
            with self.subTest(cmd):
                self.assertSilent(cmd)
                self.assertSilent(cmd, agent_id=None)

    def test_a_planted_program_key_at_the_local_scope_refuses_every_member_git_call(self):
        for key, text in (("core.pager", "[core]\n\tpager = /bin/echo\n"),
                          ("diff.external", "[diff]\n\texternal = /bin/echo\n"),
                          ("core.sshCommand", "[core]\n\tsshCommand = /bin/echo\n"),
                          ("core.hooksPath", "[core]\n\thooksPath = /tmp/h\n"),
                          ("credential.helper", "[credential]\n\thelper = /bin/echo\n"),
                          ("difftool.t.cmd", '[difftool "t"]\n\tcmd = /bin/echo\n')):
            with self.subTest(key):
                self.plant("[core]\n\trepositoryformatversion = 0\n" + text)
                self.assertRepoRefused("local")
                r = self.assertRefused("git -C %s status" % self.repo, key.split(".")[-1].casefold())
                self.assertIn(GIT_SCOPE_WORDING, r.reason)

    def test_a_program_key_at_the_worktree_scope_is_named_as_such(self):
        self.plant("[core]\n\trepositoryformatversion = 0\n[extensions]\n\tworktreeConfig = true\n")
        self.plant("[core]\n\tpager = /bin/echo\n", name="config.worktree")
        self.assertRepoRefused("worktree")

    def test_a_program_key_reached_by_include_path_is_refused(self):
        """git reports an included file's keys at the including file's scope, so the listing already covers includes."""
        self.plant("[core]\n\trepositoryformatversion = 0\n[include]\n\tpath = extra\n")
        self.plant("[core]\n\tsshCommand = /bin/echo\n", name="extra")
        self.assertRepoRefused("local")

    def test_inert_local_keys_stay_silent(self):
        self.plant("[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n\tignorecase = true\n"
                   '[remote "origin"]\n\turl = https://example.invalid/x\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
                   '[branch "main"]\n\tremote = origin\n\tmerge = refs/heads/main\n\tvscode-merge-base = origin/main\n'
                   "[extensions]\n\tworktreeConfig = false\n[color]\n\tui = never\n")
        self.assertRepoSilent()

    def test_a_key_at_the_global_or_system_scope_is_ignored(self):
        """Eric's own scopes: credential.helper is in force at the system scope on this Mac, so a check over every scope
        would refuse every member git call."""
        planted = self.home.path / "tests" / "global.gitconfig"
        planted.write_text("[core]\n\tpager = /bin/echo\n\tsshCommand = /bin/echo\n", encoding="utf-8")
        self.home.env["GIT_CONFIG_GLOBAL"] = str(planted)
        self.addCleanup(self.home.env.pop, "GIT_CONFIG_GLOBAL", None)
        self.assertRepoSilent()

    def test_a_directory_the_hook_cannot_follow_is_refused(self):
        for cmd in ("cd - && git status", "popd && git status", "cd ~x && git log"):
            with self.subTest(cmd):
                r = self.assertRefused(cmd, "Law 7")
                self.assertIn("cannot", r.reason)
                self.assertSilent(cmd, agent_id=None)

    def test_a_directory_in_no_repository_at_all_needs_no_git_run(self):
        cache = self.home.path / STATE / "git-config-scopes.json"
        shutil.rmtree(self.repo / ".git")
        self.assertSilent("git status")  # the scratch home is not a repository now: no local scope, nothing to read
        self.assertFalse(cache.exists(), "a directory in no repository costs no git run and no cache entry")

    def test_the_answer_is_cached_and_a_config_edit_invalidates_it(self):
        cache = self.home.path / STATE / "git-config-scopes.json"
        self.assertSilent("git -C %s status" % self.repo)
        self.assertTrue(cache.exists(), "the first call writes the cache")
        stored = json.loads(cache.read_text(encoding="utf-8"))
        self.assertTrue(stored, stored)
        self.assertSilent("git -C %s status" % self.repo)  # a hit: the same answer
        self.plant("[core]\n\trepositoryformatversion = 0\n\tpager = /bin/echo\n")
        self.assertRefused("git -C %s status" % self.repo, "core.pager")  # the fingerprint changed
        self.plant("[core]\n\trepositoryformatversion = 0\n")
        self.assertSilent("git -C %s status" % self.repo)

    def test_a_cache_hit_imports_no_subprocess(self):
        """The Bash hook runs on every command line; SPD-016 keeps subprocess off its path, so only a miss may pay for it."""
        cold = self.imports_of("git -C %s status" % self.repo)
        self.assertIn("subprocess", cold)
        self.assertNotIn("subprocess", self.imports_of("git -C %s status" % self.repo))

    def imports_of(self, command):
        payload = self.pre_bash(command, agent_id=AGENT_A)
        proc = subprocess.run([sys.executable, "-I", "-S", "-X", "importtime", str(SPUD), "hook", "PreToolUse"],
                              input=json.dumps(payload), capture_output=True, text=True, env=self.home.env)
        self.assertEqual(proc.returncode, 0, proc)
        return {line.rsplit("|", 1)[-1].strip() for line in proc.stderr.splitlines() if line.startswith("import time:")}

    def test_a_repository_outside_every_known_checkout_keeps_its_own_reason(self):
        outside = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / ".git").mkdir()
        (outside / ".git" / "config").write_text("[core]\n\tpager = /bin/echo\n", encoding="utf-8")
        r = self.assertRefused("git -C %s status" % outside, "outside")
        self.assertNotIn(GIT_SCOPE_WORDING, r.reason)

    def test_a_write_verb_and_a_program_option_still_refuse_first(self):
        self.plant("[core]\n\trepositoryformatversion = 0\n\tpager = /bin/echo\n")
        self.assertNotIn(GIT_SCOPE_WORDING, self.assertRefused("git -C %s commit -m x" % self.repo, "never run").reason)
        self.assertNotIn(GIT_SCOPE_WORDING, self.assertRefused("git -C %s -c core.editor=vi log" % self.repo, "program git never checks").reason)


class NestedRepositoryTest(BashHookCase):
    """SPD-066 (2): a git directory need not be called .git, and the path rule cannot see every route to one.  A bare layout
    (HEAD, objects/, refs/, hooks/) written under a member's globs is a repository git discovers from inside it, a .git
    built by an interpreter, cp or mkdir is one too, and `git -C tests/fake <read verb>` resolves inside a known checkout,
    so SPD-047's refusal of a repository outside every known checkout never fires.  git runs such a repository's hooks with
    nothing on the line (probed on 2.54.0: `git status` runs post-index-change, `git fetch` reference-transaction) and reads
    its config, attributes and index.  So at each member git call the repository git would read must be a known checkout's
    own: discovered from the directory (-C, or the shell's), its work tree is the root of the registered project checkout or
    listed worktree, reached through that root's own .git; named by --git-dir, GIT_DIR or GIT_COMMON_DIR, it is that
    checkout's git or common directory.  --work-tree and GIT_WORK_TREE never choose the repository (probed: git still
    discovers from the directory it runs in), so with only those on the line the shell's directory is read too.  The home
    is a real repository here, project spud's root in the suite; AGENT_A plans home:tests/** and home:bin/spud, AGENT_C
    home:**."""

    VERBS = ("status", "log --oneline", "fetch")

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        scratch_git(home, "init", "-q", "-b", "main")
        scratch_git(home, "commit", "-q", "--allow-empty", "-m", "root")
        self.nested = home / "tests" / "fake"  # a .git planted below the checkout's root
        plant_git_dir(self.nested / ".git")
        (self.nested / "sub").mkdir()
        self.bare = home / "tests" / "bare"  # a bare layout, no .git anywhere: git discovers it from inside
        plant_git_dir(self.bare)
        (self.bare / "hooks").mkdir()

    def refused_for_members(self, command, repository):
        for agent_id in (AGENT_A, AGENT_C):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, GIT_NESTED_WORDING, agent_id=agent_id)
                self.assertIn("Law 7", r.reason)
                self.assertIn(str(repository), r.reason)  # the reason names the repository
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)  # Law 7 does not bind Spud

    def silent_for_all(self, command):
        for agent_id in (AGENT_A, AGENT_C, None):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertSilent(command, agent_id=agent_id)

    def test_a_nested_git_directory_is_refused(self):
        n, home = self.nested, self.home.path
        for verb in self.VERBS:
            for cmd in ("git -C %s %s" % (n, verb), "git -C tests/fake %s" % verb, "cd %s && git %s" % (n, verb),
                        "cd tests/fake/sub && git %s" % verb, "git -C %s/sub %s" % (n, verb),
                        "git --git-dir=%s/.git %s" % (n, verb), "git --git-dir %s/.git %s" % (n, verb),
                        "GIT_DIR=%s/.git git %s" % (n, verb), "env GIT_DIR=%s/.git git %s" % (n, verb),
                        "export GIT_DIR=%s/.git; git %s" % (n, verb), "git -C tests/fake --git-dir=.git %s" % verb,
                        "sh -c 'cd %s && git %s'" % (n, verb), "(cd tests/fake && git %s)" % verb,
                        # the work tree never chooses the repository: git still reads the one it discovers where it runs
                        "cd %s && git --work-tree=%s %s" % (n, home, verb), "cd %s && GIT_WORK_TREE=%s git %s" % (n, home, verb)):
                self.refused_for_members(cmd, n)

    def test_a_bare_layout_is_refused(self):
        b = self.bare
        for verb in self.VERBS:
            for cmd in ("git -C %s %s" % (b, verb), "git -C tests/bare %s" % verb, "cd %s && git %s" % (b, verb),
                        "cd tests/bare/refs/heads && git %s" % verb, "git --git-dir=%s %s" % (b, verb),
                        "git --git-dir %s %s" % (b, verb), "GIT_DIR=%s git %s" % (b, verb),
                        "GIT_DIR=tests/bare git %s" % verb, "GIT_COMMON_DIR=%s git %s" % (b, verb)):
                self.refused_for_members(cmd, b)

    def test_a_gitfile_below_the_root_is_a_nested_repository_too(self):
        """A submodule's shape: a .git file naming a git directory elsewhere.  Here it names the checkout's own, so the git
        directory is known, but the work tree git would read with it is not the checkout's."""
        linked = self.home.path / "tests" / "linked"
        linked.mkdir()
        (linked / ".git").write_text("gitdir: %s\n" % (self.home.path / ".git"), encoding="utf-8")
        self.refused_for_members("git -C %s status" % linked, linked)
        self.refused_for_members("cd %s && git status" % linked, linked)

    def test_the_checkouts_own_repository_stays_silent(self):
        home = self.home.path
        for verb in self.VERBS:
            for cmd in ("git %s" % verb, "git -C %s %s" % (home, verb), "cd %s/tests && git %s" % (home, verb),
                        "git -C tests %s" % verb, "git -C bin %s" % verb, "git --git-dir=%s/.git %s" % (home, verb),
                        "GIT_DIR=%s/.git git %s" % (home, verb), "git -C %s --git-dir=.git %s" % (home, verb),
                        "git --work-tree=%s %s" % (home, verb), "GIT_WORK_TREE=%s git %s" % (home, verb),
                        "GIT_COMMON_DIR=%s/.git git %s" % (home, verb), "sh -c 'cd %s && git %s'" % (home, verb)):
                self.silent_for_all(cmd)

    def test_a_listed_worktree_is_a_checkouts_own_repository(self):
        home = self.home.path
        wt = home.parent / ("%s-nested" % home.name)
        self.addCleanup(shutil.rmtree, wt, True)
        scratch_git(home, "worktree", "add", "-q", "-b", "nested", wt)
        inside = home / ".claude" / "worktrees" / "spd-066-x"
        scratch_git(home, "worktree", "add", "-q", "-b", "worktree-spd-066-x", inside)
        for verb in self.VERBS:
            for cmd in ("git -C %s %s" % (wt, verb), "cd %s && git %s" % (inside, verb), "git -C %s/.claude %s" % (home, verb),
                        "GIT_DIR=%s/.git/worktrees/nested git %s" % (home, verb)):
                self.silent_for_all(cmd)
        # a directory that looks like a worktree of the home but that git does not list is no checkout of it, though the path
        # rule maps a .claude/worktrees/<name>/ of a root as one
        planted = home / ".claude" / "worktrees" / "planted"
        plant_git_dir(planted / ".git")
        self.refused_for_members("git -C %s status" % planted, planted)
        self.refused_for_members("cd %s && git fetch" % planted, planted)

    def test_a_bare_layout_at_a_checkout_root_is_no_repository_of_it(self):
        """The root that has no .git of its own (the home, in the real ledger) made a bare repository by writing HEAD,
        objects/ and refs/ at it, and a root with a .git named as the git directory itself (--git-dir=<root>)."""
        shutil.rmtree(self.home.path / ".git")
        plant_git_dir(self.home.path / "planted-root")  # built beside, then its parts moved to the root
        for part in ("objects", "refs", "HEAD", "config"):
            os.rename(self.home.path / "planted-root" / part, self.home.path / part)
        self.refused_for_members("git status", self.home.path)
        self.refused_for_members("git -C tests log", self.home.path)
        self.refused_for_members("git --git-dir=%s fetch" % self.home.path, self.home.path)

    def test_a_repository_outside_every_checkout_reached_by_cd_is_refused(self):
        """SPD-047 refuses one a line names; the directory the shell is in reaches one too."""
        outside = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        plant_git_dir(outside / ".git")
        self.refused_for_members("cd %s && git status" % outside, outside)
        r = self.assertRefused("git -C %s status" % outside, "outside every checkout")  # SPD-047's own reason, first
        self.assertNotIn(GIT_NESTED_WORDING, r.reason)
        nowhere = Path(tempfile.mkdtemp(prefix="spud-no-repository-")).resolve()
        self.addCleanup(shutil.rmtree, nowhere, True)
        self.silent_for_all("cd %s && git status" % nowhere)  # no repository where it runs: git reads none, nothing to check

    def test_a_directory_the_hook_cannot_follow_with_only_a_work_tree_is_refused(self):
        """With only --work-tree or GIT_WORK_TREE on the line git discovers the repository where the shell is, which the
        hook cannot know after `cd -`: it fails closed, as SPD-063 does for a bare `git status` there."""
        for cmd in ("cd - && git --work-tree=%s status" % self.home.path, "popd && GIT_WORK_TREE=%s git log" % self.home.path):
            with self.subTest(cmd):
                r = self.assertRefused(cmd, "cannot")
                self.assertIn("Law 7", r.reason)
                self.assertSilent(cmd, agent_id=None)

    def test_a_refusal_reads_no_config_and_runs_no_git(self):
        """The nested repository is refused from what SPD-063 already computes (the one walk up to it): no git run, no cache
        entry for it, no subprocess import once the checkout's own caches are warm."""
        self.plant_program_key(self.nested / ".git" / "config")
        self.assertSilent("git status")  # warms the worktree list, git's command list and the checkout's config scopes
        r = self.assertRefused("git -C %s status" % self.nested, GIT_NESTED_WORDING)
        self.assertNotIn(GIT_SCOPE_WORDING, r.reason)  # the repository is refused before its keys are read
        cache = json.loads((self.home.path / STATE / "git-config-scopes.json").read_text(encoding="utf-8"))
        self.assertNotIn(str(self.nested / ".git"), cache)
        payload = self.pre_bash("git -C %s status" % self.nested, agent_id=AGENT_A)
        proc = subprocess.run([sys.executable, "-I", "-S", "-X", "importtime", str(SPUD), "hook", "PreToolUse"],
                              input=json.dumps(payload), capture_output=True, text=True, env=self.home.env)
        self.assertIn('"deny"', proc.stdout)
        imported = {line.rsplit("|", 1)[-1].strip() for line in proc.stderr.splitlines() if line.startswith("import time:")}
        self.assertNotIn("subprocess", imported)

    @staticmethod
    def plant_program_key(config):
        config.write_text("[core]\n\trepositoryformatversion = 0\n\tpager = /bin/echo\n", encoding="utf-8")

    def test_write_verbs_and_spd_047_keep_their_own_reasons(self):
        for cmd, needle in (("git -C %s commit -m x" % self.nested, "never run"), ("git -C %s push" % self.bare, "never run"),
                            ("git -C %s -c core.editor=vi log" % self.nested, "program git never checks")):
            with self.subTest(cmd):
                r = self.assertRefused(cmd, needle)
                self.assertNotIn(GIT_NESTED_WORDING, r.reason)


class ParameterExpansionCommandWordTest(BashHookCase):
    """SPD-043: a word the Bash hook dispatches on by name that the shell builds from an expansion the hook does not resolve
    exactly hid the command.  Probed in zsh 5.9 -f, zsh -f -o nobareglobqual (this Mac's Bash tool), bash 3.2 and sh, with a fake
    git first on a scratch PATH writing a marker in the scratchpad: each of these ran `git push` while the hook read kind other
    with no finding: `${X:-git} push` and every operator form, zsh's `${(L)X}`, `${~X}`, `${=X}`, `$~X`, `$=X` and `$^X`,
    `$X$Y push`, `g$X push`, `${arr[1]}` (zsh) and `${arr[0]}` (bash), `X=env; $X git push` (a wrapper through a variable),
    `X='g?t'; $X push` (bash globs a quoted glob the value holds), `$'\\x67it' push`, `X=; $X git push` (the empty word goes and
    git is the command), `X="git push '"; $X` (bash splits on blanks and keeps the quote), `X=g; X+=it; $X push`,
    `IFS=_; X=git_push; $X`, `arr=(git status); $arr push` (bash reads the first element), and a value the line assigned that
    does not hold when the variable is read: `X=git; (X=ls); $X push`, `X=git; true | X=ls; $X push` (bash),
    `X=git; X=ls true; $X push`, `X=ls; for X in git; do $X push; done`, `X=; : ${X:=git}; $X push`,
    `X=ls; read X <<< git; $X push`, `X=git; echo $(X=ls); $X push`, `X=git; f() { X=ls; }; $X push`.

    For a member such a word is refused, as a command word from a substitution always was: only a bare `$X` or `${X}` whose value
    the line assigned for certain is read, as bash reads it (split on blanks, its glob characters active) and as zsh does (one
    word), and its words go back through the prefixes (a wrapper, an empty word).  `V=push; git $V` is read as `git push`.  A
    `$` in single quotes or escaped is literal and stays silent in a spud call's message.  Spud is not refused by any of it.
    AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        for rel in ("tests/keep.py", "ledger/tickets/SPD-001.md"):
            p = self.home.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")

    def refused_for_members(self, command, needle="cannot resolve", cwd=None):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id, cwd)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None, cwd=cwd)
        return r

    def findings(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path))).findings

    def test_the_tickets_evidence_commands(self):
        for cmd in ("${X:-git} push", "X=g Y=it; $X$Y push", "X=it; g$X push", "arr=(git); ${arr[1]} push", "arr=(git); ${arr[0]} push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        self.refused_for_members("X=env; $X git push", "Law 7")
        self.refused_for_members("X='g?t'; $X push", "Law 7")
        self.assertEqual(self.findings("${X:-git} push"), [("var", "${X:-git}")])

    def test_every_operator_form_and_zsh_flag_is_refused(self):
        for cmd in ("${X:-git} push", "X=; ${X:-git} push", "${X-git} push", "${X:=git} push", "X=1; ${X:+git} push", "X=xgit; ${X#x} push",
                    "X=gitx; ${X%x} push", "X=gitxx; ${X%%x*} push", "X=xxgit; ${X##*x} push", "X=gat; ${X/a/i} push", "X=gaat; ${X//a/i} push",
                    "X=Git; ${X,} push", "X=GIT; ${X,,} push", "X=git; ${X^} push", "X=GIT; ${(L)X} push", "X='g?t'; ${~X} push",
                    "X='git push'; ${=X}", "X='g?t'; $~X push", "X=git; $=X push", "X=git; $^X push", "Y=git; X=Y; ${!X} push",
                    "X=git; ${X:0:3} push", "${#X} push", "$((1)) push", "$[1] push", "X=/usr/bin/git; $X:t push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_concatenation_and_partial_expansion(self):
        for cmd in ("X=g Y=it; $X$Y push", "X=it; g$X push", "X=g; ${X}it push", "X=gi; $X\"t\" push", "X=git; $X$Z push",
                    "X=/usr/bin; $X/git push", "X=git; /usr/bin/$X push", "X=g; \"$X\"it push", "X=gi Xt=ls; $X\"t\" push",
                    "X=gi Xt=ls; $X't' push", "X=gi Xt=ls; $X\\t push", "X=git; $X[1] push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_arrays(self):
        """zsh's `$arr` is every element and its subscripts count from 1; bash's is the first element, from 0: a bare array
        reference is read both ways and refused, a subscript is never resolved."""
        for cmd in ("arr=(git); ${arr[1]} push", "arr=(git); ${arr[0]} push", "arr=(x git); ${arr[@]} push", "arr=(ls); $arr"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        for cmd in ("arr=(git status); $arr push", "arr=(git); ${arr} push", "declare -a arr=(git status); $arr push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")  # bash's reading, the first element: `git push`

    def test_ansi_c_and_locale_quoting(self):
        for cmd in ("$'git' push", "$'\\x67it' push", "$'\\147it' push", "$\"git\" push", "g$'i't push", "git $'push'", "git $'\\x70ush'"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_a_wrapper_or_glob_reached_through_a_variable(self):
        for cmd in ("X=env; $X git push", "X=exec; $X git push", "X=command; $X git push", "X='env git'; $X push", "X=nice; $X -n 5 git push",
                    "X='-n 5'; nice $X git push", "X=/usr/bin/env; ${X} git push", "X=git; \"$X\" push", "X=git; \"${X}\"\"\" push",
                    "X='g?t'; $X push", "X='g*t'; $X push", "X=g?t; $X push", "X='gi[t]'; $X push", "X='env g?t'; $X push",
                    "X=x=./git; $X push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")

    def test_the_value_is_read_as_the_shells_split_it(self):
        refused = ("X=\"git push '\"; $X", "X=; $X git push", "X=\"\"; $X git push", "X='  '; $X git push", "X='git  push'; $X",
                   "X='/usr/local/my tools/git'; $X push", "X=-C; git $X . push", "X='-C . push'; git $X", "V=push; git $V",
                   "V=push; git ${V}", "X='-c alias.p=push'; git $X p")
        for cmd in refused:
            with self.subTest(cmd):
                for agent_id in (AGENT_C, AGENT_A):
                    self.assertEqual(self.bash(cmd, agent_id).decision, "deny", cmd)
                self.assertSilent(cmd, agent_id=None)
        for cmd in ("X=g; X+=it; $X push", "Y=git; X=$Y; $X push", "X=$(echo git); $X push", "IFS=_; X=git_push; $X",
                    "X='$Y'; Y=git; $X push", "X=`echo git`; $X push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_other_words_the_dispatch_reads_by_name(self):
        """git's options, verb and the arguments git_refused reads, a wrapper's options, a shell's options, python's options and
        script, and every word of a spud call."""
        spud = self.spud_cli
        for cmd in ("git $V", "git ${V:-push}", "git $(echo push)", "git `echo push`", "git -C $D status", "git ${(L)V}",
                    "nice -n $N git status", "env -u $U git status", "sudo -u ${U:-root} git status", "sh $O 'git push'",
                    "bash -$O 'git push'", "python3.14 -I -S $S --as spud board", "python3 -m $M", "X=; git $X status"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        # A command the words as spelled already refuse keeps its own reason.
        for cmd, needle in (("git -c $KV log", "Law 7"), ("git stash $S", "Law 7"), ("git branch $B", "Law 7"), ("git config $K $V", "Law 7"),
                            ("python3.14 -I -S ${HOME}/bin/spud ticket new --title x", "Law 6"), ("spud --as ${A:-spud} board", "--as")):
            with self.subTest(cmd):
                self.refused_for_members(cmd, needle)
        # A word of a spud call can become `--as=spud` or another command; Spud's own reading of these calls is unchanged.
        for cmd, needle in (("%s $C new --title x" % spud, "cannot resolve"), ("%s --as $A board" % spud, "--as"),
                            ("%s ticket $S --title x" % spud, "cannot resolve"), ("%s member log \"$M\"" % spud, "cannot resolve"),
                            ("%s member log $M" % spud, "cannot resolve")):
            with self.subTest(cmd):
                for agent_id in (AGENT_C, AGENT_A):
                    self.assertRefused(cmd, needle, agent_id)
                self.assertNotEqual(self.bash(cmd, agent_id=None).decision, "deny")

    def test_a_value_the_line_may_not_have_kept(self):
        for cmd in ("X=git; if false; then X=ls; fi; $X push", "X=git; false && X=ls; $X push", "X=git; (X=ls); $X push",
                    "X=git; X=ls true; $X push", "X=ls; X=git :; $X push", "X=ls; for X in git; do $X push; done",
                    "X=ls; for X in git; do :; done; $X push", "X=; : ${X:=git}; $X push", "X=; echo \"${X:=git}\"; $X push",
                    "X=git; true | X=ls; $X push", "X=git; X=ls | cat; $X push", "X=git; X=ls & wait; $X push",
                    "X=git; { X=ls; } | cat; $X push", "X=git; f() { X=ls; }; $X push", "X=ls; f() { X=git; }; f; $X push",
                    "X=ls; read X <<< git; $X push", "X=ls; printf -v X git; $X push", "X=ls; unset X; $X git push",
                    "X=git; echo $(X=ls); $X push", "X=git; echo `X=ls`; $X push", "X=ls; typeset -n X=Y; Y=git; $X push",
                    "X=ls; source ./x.sh; $X push", "X=ls; . ./x.sh; $X push", "X=ls; while true; do $X push; X=git; done",
                    "X=ls; eval \"$N=git\"; $X push", "X=ls; getopts g X; $X push", "X=ls; mapfile X < f; $X push",
                    "X=ls; sh -c 'X=git'; X=git; (X=ls); $X push"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "")  # Law 7's reason where the line's own value is git, else the doubt's
        self.assertIn("may not hold", self.refused_for_members("X=ls; trap 'X=git' DEBUG; $X push").reason)
        self.assertIn("Law 7", self.refused_for_members("X=ls; X=git :; $X push", "").reason)
        for ok in ("X=git; if true; then X=ls; $X push; fi", "sh -c 'X=ls; $X'", "X=ls; export X; $X", "X=git; (X=ls; $X push)"):
            with self.subTest(ok):  # read where the assignment certainly ran
                self.assertSilent(ok)

    def test_spud_keeps_his_own_checks(self):
        spud = self.spud_cli
        self.assertRefused("%s --as $A member log hi" % spud, "Law 5", agent_id=None)
        self.assertRefused("X=%s; %s --as $X member log hi" % (AGENT_A, spud), "Law 5", agent_id=None)
        self.assertRefused("X='%s --as %s member log'; $X hi" % (spud, AGENT_A), "Law 5", agent_id=None)
        self.assertRefused("X=env; $X %s --as %s member log hi" % (spud, AGENT_A), "Law 5", agent_id=None)
        self.assertRefused("X=; %s --as $X member log hi" % spud, "Law 5", agent_id=None)
        self.assertRefused("D=%s; ${D}/bin/spud --as %s member log hi" % (self.home.path, AGENT_A), "Law 5", agent_id=None)
        self.assertRefused("D=%s; ${D}/bin/spud --as %s member log hi" % (self.home.path, AGENT_A), "cannot resolve")
        self.assertRefused("X=tee; echo x | $X ledger/tickets/SPD-001.md", "Law 1", agent_id=None)
        self.assertRefused("V=ticket; %s --as spud $V new --title x" % spud, "Law 6")
        self.assertRefused("X=git; ${X:-x} push", "cannot resolve")

    def test_controls_stay_silent(self):
        spud = self.spud_cli
        for ok in ("X=ls; $X", "X=ls; $X -la", "X=git; $X status", "X=git; ${X} log --oneline", "export X=ls; $X", "X=ls; echo $X; $X",
                   "echo $HOME", "ls ${DIR:-.}", "echo \"$(date)\"", "cat $F", "grep -n \"$P\" tests/keep.py", "git log -- $F",
                   "git diff $A $B", "git log --format='%h $x'", "echo $'a\\tb'", "printf $'a\\n'", "x=$((2*3)); echo $x", "echo $((1+2))",
                   "for f in tests/*.py; do python3 -m py_compile $f; done", "X=ls; if true; then echo $X; fi", "git show HEAD:$F",
                   "python3 -c \"print('$HOME')\"", "sh -c \"echo $HOME\"", "X='git push'; echo $X", "X=git; echo ${X:-x}",
                   "git log '$V'", "echo ${X:-git} push", "X=ls; Y=$X; echo $Y", "env FOO=1 ls $D", "git '$V'", "git \\$V"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        for allowed in ("%s --as %s member log 'cost $5 for ${X:-git} push'" % (spud, AGENT_A), "%s --as %s member log \\$5" % (spud, AGENT_A),
                        "%s --as %s member log \"cost \\$5\"" % (spud, AGENT_A)):
            with self.subTest(allowed):
                self.assertAllowed(allowed)
        self.assertRefused("git push", "Law 7")
        self.assertRefused("$X push", "spell the command out")


class TrapActionTest(BashHookCase):
    """SPD-054 (Agria's SPD-043 proposal): `trap` stores shell code the shell runs later -- at exit, before every command
    under DEBUG, on ERR, and on every signal by name or number -- and analyse_words dispatched the line as kind other, so a
    member's VCS write inside the action string reached the hook with no finding.  Probed in bash 3.2, zsh 5.9 -f, zsh -f -o
    nobareglobqual (the Bash tool's zsh on this Mac) and sh, with a fake git first on a scratch PATH writing a marker in the
    scratchpad.  All four ran `git push`: `trap 'git push' EXIT`, `trap "git commit -m x" ERR; false`, `trap 'cd /tmp && git
    add .' DEBUG; :`, `trap 'eval "git push"' 0`, `trap 'sh -c "git push"' INT; kill -INT $$`, the signal spelled `0`,
    `SIGHUP`, `15` or several at once, `trap -- 'git push' EXIT`, the command word reached through `builtin`, `time`,
    `X=trap; $X` or `tr?p` with a file named trap beside it, the line inside eval, `sh -c`, a function body, a subshell, a
    loop and another trap's action, and the action read from `"$X"`, `"$(echo git push)"` and `${X:-'git push'}`.  `command
    trap` ran it in bash and sh (in zsh `command` finds only an external) and `noglob trap` in zsh, whose modifier it is;
    `env`, `nohup`, `exec`, `sudo`, `nice` and `xargs` ran no builtin at all.

    The action is now read as the shell text it is, with its own quotes, as eval's rejoined words and a shell's `-c` string
    are: a finding inside it is the finding it would be on the line, so a member is refused under Law 7, Law 6 or the actor
    check exactly as it would be, and Spud is refused by none of it while keeping his own checks inside the action.

    The words that run nothing stay silent, each probed to run nothing in all four shells: `trap` alone, `trap -p`, `trap
    -l`, `trap -lp` and `trap -P EXIT` print or list, `trap -`, `trap - EXIT`, `trap '' EXIT` and `trap "" INT TERM` reset or
    ignore, and `trap EXIT` is a reset (bash and zsh) or a usage error (sh).  Both action positions are read, since the two
    shells disagree about options: bash reads the action after its options and `--`, zsh has none there and reads the word
    right after `trap` whatever it is (`trap -P EXIT` ran `-P` at exit, `trap -p EXIT` ran `-p`).  The single-argument form
    runs nothing anywhere (`trap 'git push'` and `trap git` print bash's and sh's usage and set nothing in zsh), but it is
    read all the same, fail closed and at no real cost: it names code, and the line is a shell error where it is not a
    no-op.  `env trap`, `nohup trap`, `exec trap` and `xargs trap` run no builtin, so they stay kind other, as `env cd` and
    `env source` do.

    The action runs at a working directory the hook cannot know: an EXIT action runs after every later cd (probed: `trap
    'echo trapped >> rel.txt' EXIT; cd /tmp` wrote /tmp/rel.txt, and an EXIT action's `pwd` is the last directory of the
    line), a DEBUG action before every command.  It is therefore read with the directories unknown, as a sourced file is, so
    a relative redirection or tee inside it is refused for a member and left unchecked for Spud; the line's own directories
    are restored afterwards, since defining a trap changes nothing on the line.  The action's own assignments do not reach
    the rest of the line either -- they run later, and SPD-043's `a.all_doubt` after `trap`, which this ticket leaves alone,
    already doubts every variable (probed: a DEBUG action's `X=git` did reach the next command's expansion in all four
    shells, so the doubt, not the value, is what the hook keeps).  An action word holding an expansion the hook cannot
    resolve exactly is refused as SPD-043 refuses one; a value the line assigned resolves as SPD-043 resolves it.
    AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        for rel in ("tests/keep.py", "ledger/tickets/SPD-001.md"):
            p = self.home.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")

    def refused_for_members(self, command, needle="Law 7", cwd=None):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id, cwd)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None, cwd=cwd)
        return r

    def silent_for_everyone(self, command, cwd=None):
        for agent_id in (AGENT_C, AGENT_A, None):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertSilent(command, agent_id, cwd)

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def findings(self, command):
        return self.analysis(command).findings

    def test_the_tickets_evidence_commands(self):
        for cmd in ("trap 'git push' EXIT", 'trap "git commit -m x" ERR', "trap 'cd /tmp && git add .' DEBUG",
                    "trap 'eval \"git push\"' 0", "trap 'sh -c \"git push\"' INT"):
            self.refused_for_members(cmd)
        self.assertEqual(self.findings("trap 'git push' EXIT"), [("git", ("push", "push"))])
        self.assertEqual(self.findings("trap 'git status' EXIT"), [("git", ("status", None))])

    def test_every_signal_spelling(self):
        for sig in ("EXIT", "0", "1", "15", "INT", "SIGINT", "sigint", "TERM", "HUP", "USR1", "ERR", "DEBUG", "RETURN",
                    "INT TERM EXIT", "EXIT INT", "1 2 15"):
            with self.subTest(sig):
                self.refused_for_members("trap 'git push' %s" % sig)

    def test_the_forms_that_run_nothing_stay_silent(self):
        for ok in ("trap", "trap -p", "trap -l", "trap -lp", "trap -p EXIT", "trap -P EXIT", "trap -",
                   "trap - EXIT", "trap - INT TERM", "trap '' EXIT", 'trap "" INT TERM', "trap -- '' EXIT",
                   "trap -- - EXIT", "trap EXIT", "trap git", "trap 'echo done' EXIT", "trap 'git status' EXIT",
                   "trap 'git log --oneline -5' EXIT", "trap 'rm -f /tmp/x' EXIT", "trap 'cd /tmp' EXIT",
                   "trap 'X=1' EXIT", "trap 'echo x > /tmp/out.txt' EXIT", "echo trap", "grep -n trap tests/keep.py"):
            self.silent_for_everyone(ok)
        self.assertEqual(self.findings("trap -p"), [])
        self.assertEqual(self.findings("trap - EXIT"), [])

    def test_both_action_positions_are_read(self):
        """`--` and bash's options are skipped, and the word right after `trap` is read too, since zsh takes it for the
        action.  Only `trap -- 'git push' EXIT` runs in the shells; the rest are read fail closed."""
        for cmd in ("trap -- 'git push' EXIT", "trap -p 'git push' EXIT", "trap -l 'git push' EXIT",
                    "trap -P 'git push' EXIT", "trap -x 'git push' EXIT", "trap --p 'git push' EXIT",
                    "trap -lp 'git push' EXIT", "trap 'git push'", 'trap "git push"'):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_the_command_word_reached_another_way(self):
        for cmd in ("builtin trap 'git push' EXIT", "command trap 'git push' EXIT", "time trap 'git push' EXIT",
                    "noglob trap 'git push' EXIT", "X=trap; $X 'git push' EXIT", "X=trap; ${X} 'git push' EXIT",
                    "tr?p 'git push' EXIT", "tr*p 'git push' EXIT", "'trap' 'git push' EXIT", "t\"\"rap 'git push' EXIT",
                    "tr\\ap 'git push' EXIT"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        for ok in ("env trap 'git push' EXIT", "nohup trap 'git push' EXIT", "exec trap 'git push' EXIT",
                   "sudo trap 'git push' EXIT", "xargs trap 'git push' EXIT", "nice trap 'git push' EXIT",
                   "/usr/bin/trap 'git push' EXIT", "./trap 'git push' EXIT", "TRAP 'git push' EXIT",
                   "traps 'git push' EXIT", "echo trap 'git push' EXIT"):
            self.silent_for_everyone(ok)

    def test_a_coproc_or_a_pipeline_forks_a_shell_where_the_builtin_runs(self):
        """Round 2 of Spud's review: `coproc` is not an external wrapper -- it runs its command in a forked shell of its
        own, where the builtin does run and whose exit fires the action.  Probed in zsh 5.9 -f and zsh -f -o nobareglobqual
        with a fake git on a scratch PATH: `coproc { trap 'git push' EXIT; }`, `coproc ( trap 'git push' EXIT )`,
        `cat /dev/null | trap 'git push' EXIT` and `{ trap 'git push' EXIT; } | cat` each ran git push, and `coproc
        { trap 'echo x > rel.txt' EXIT; }` wrote the file; bash 3.2 and sh have no coproc (a syntax error there) and ran
        nothing for any of them.  `coproc trap 'git push' EXIT`, the simple command, ran nothing in any of the four and is
        read fail closed all the same.  A cd under coproc still changes nothing the line can see: the forked shell's
        directory never comes back, so its effect settles as an external program's does."""
        for cmd in ("coproc { trap 'git push' EXIT; }", "coproc trap 'git push' EXIT",
                    "coproc ( trap 'git push' EXIT )", "cat /dev/null | trap 'git push' EXIT",
                    "{ trap 'git push' EXIT; } | cat", "coproc { trap 'eval \"git push\"' EXIT; }"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        for agent_id in (AGENT_C, AGENT_A, None):  # since SPD-035 a target the hook cannot place refuses Spud too
            self.assertRefused("coproc { trap 'echo x > out.txt' EXIT; }", "cannot follow", agent_id)
        self.silent_for_everyone("coproc { trap 'echo done' EXIT; }")
        # a cd under coproc is unchanged: the forked shell's directory is not the line's
        for cmd in ("coproc { cd /tmp; }", "coproc cd /tmp", "coproc { trap 'cd /tmp' EXIT; }"):
            with self.subTest(cmd):
                self.assertEqual(self.analysis(cmd).cwds, frozenset([str(self.home.path)]))
        self.silent_for_everyone("coproc { cd /tmp; }")

    def test_the_line_inside_every_construct_that_recurses(self):
        for cmd in ("eval \"trap 'git push' EXIT\"", "sh -c \"trap 'git push' EXIT\"", "bash -lc \"trap 'git push' EXIT\"",
                    "zsh -c \"trap 'git push' EXIT\"", "f() { trap 'git push' EXIT; }; f", "( trap 'git push' EXIT )",
                    "{ trap 'git push' EXIT; }", "for i in 1; do trap 'git push' EXIT; done",
                    "if true; then trap 'git push' EXIT; fi", "true && trap 'git push' EXIT",
                    "echo $(trap 'git push' EXIT)", "trap \"trap 'git push' EXIT\" DEBUG",
                    "sh <<'EOF'\ntrap 'git push' EXIT\nEOF"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_an_expansion_in_the_action_word(self):
        for cmd in ('trap "$X" EXIT', "trap $X EXIT", 'trap "$(echo git push)" EXIT', "trap `echo git push` EXIT",
                    "trap ${X:-'git push'} EXIT", "trap ${X} EXIT", "X=g; trap $X$Y EXIT", "X=it; trap \"g$X\" EXIT",
                    "trap $'git push' EXIT", 'trap -- "$X" EXIT', "trap \"$(cat f)\" EXIT"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "cannot resolve")
        for cmd in ("X='git push'; trap \"$X\" EXIT", "X='git push'; trap $X EXIT", "X='git push'; trap ${X} EXIT",
                    "X=push; trap \"git $X\" EXIT"):
            with self.subTest(cmd):  # the value the line assigned, read as bash splits it and as zsh keeps it
                self.refused_for_members(cmd)

    def test_the_action_runs_where_the_hook_cannot_know_the_directory(self):
        # A trap's action runs wherever the shell stands when the signal fires, so a relative target in it lands in a
        # directory the hook cannot know: refused for a member, and since SPD-035 for Spud too (Laws 1 and 5 hold for him).
        for cmd in ("trap 'echo x > out.txt' EXIT", "cd /tmp && trap 'echo x >> out.txt' EXIT",
                    "trap 'echo x | tee out.txt' EXIT", "trap 'echo x > tests/keep.py' EXIT"):
            with self.subTest(cmd):
                for agent_id in (AGENT_C, AGENT_A, None):
                    self.assertRefused(cmd, "cannot follow", agent_id)
        # the action's own absolute cd settles its directory again, and the target is checked there
        self.silent_for_everyone("trap 'cd /tmp; echo x > out.txt' EXIT")
        absolute = "trap 'echo x > %s/ledger/tickets/SPD-001.md' EXIT" % self.home.path
        self.assertRefused(absolute, "ledger/tickets", AGENT_C)
        self.assertRefused(absolute, "Law 1", agent_id=None)  # Spud keeps his own checks inside the action
        self.assertEqual([c for _, c in self.analysis("trap 'echo x > out.txt' EXIT").redirects], [None])
        # defining a trap changes nothing on the line: its directories and its variables are the line's own
        self.assertEqual(self.analysis("trap 'cd /tmp' EXIT").cwds, frozenset([str(self.home.path)]))
        self.assertEqual(self.analysis("X=git; trap 'X=ls' EXIT").vars["X"], "git")
        self.assertSilent("trap 'cd /tmp' EXIT; echo x > tests/keep.py")  # the member's own deliverable, from the line's directory
        self.assertSilent("trap 'cd /tmp' EXIT; echo x > CLAUDE.md", agent_id=None)  # Spud's own file, from the line's directory

    def test_a_spud_call_in_the_action(self):
        spud = self.spud_cli
        self.refused_for_members("trap '%s --as spud ticket new --title x' EXIT" % spud, "Law 6")
        self.refused_for_members("trap '%s init' EXIT" % spud, "Law 6")
        self.assertRefused("trap '%s --as %s member log hi' EXIT" % (spud, AGENT_B), "--as", AGENT_A)
        self.assertRefused("trap '%s --as %s member log hi' EXIT" % (spud, AGENT_A), "Law 5", agent_id=None)
        self.assertRefused("trap 'sqlite3 %s/.spud/ledger.db \"select 1\"' EXIT" % self.home.path, "spud sql --readonly", AGENT_A)
        # a trap is not a spud call, so the line is never allowed, not even one the caller may make
        self.assertSilent("trap '%s --as %s member log hi' EXIT" % (spud, AGENT_A))
        self.assertAllowed("%s --as %s member log hi" % (spud, AGENT_A))

    def test_all_doubt_after_a_trap_is_what_spd_043_left(self):
        self.assertIn("may not hold", self.refused_for_members("X=ls; trap 'X=git' DEBUG; $X push", "").reason)
        self.assertIn("Law 7", self.refused_for_members("X=git; trap 'X=ls' DEBUG; $X push", "").reason)
        self.assertIn("may not hold", self.refused_for_members("X=ls; trap 'echo hi' EXIT; $X push", "").reason)


# zsh's short conditionals (SPD-061): an if, while or until whose condition ends in `[[ ... ]]`, and the body that may
# follow it with no `then` or `do`.  Each fills its `%s`.
SHORT_CONDITIONS = ("if [[ -n x ]] %s", "n=0; while [[ $((n++)) -lt 2 ]] %s", "n=0; until [[ $((n++)) -ge 2 ]] %s",
                    "if [[ -n x ]] && [[ -n y ]] %s", "if true && [[ -n x ]] %s", "if [[ -n x ]] || [[ -z y ]] %s")
SHORT_CONDITION_BODIES = ("%s", "{ %s }", "{ %s; }", "( %s )", "true && %s", "false || %s", "%s | cat",
                          "if true; then %s; fi", "case x in x) %s;; esac")


class ZshShortConditionalTest(BashHookCase):
    """SPD-061 (Caribe's SPD-042 proposal): zsh's SHORT_LOOPS give `if`, `while` and `until` a body with no `then` or `do`
    too, and with a `[[ ... ]]` condition the hook read the whole line as one simple command whose command word was `[[`, so
    the body's git verb or spud call was never checked (Laws 5, 6 and 7).  SPD-042 fixed `for`, `select` and `repeat` and
    named these out of scope, believing they were read; they were not.  Probed in zsh 5.9 -f and zsh -f -o nobareglobqual
    (this Mac's Bash tool), with bash 3.2 and sh as the controls that reject every one of these forms, in a scratchpad with a
    fake git first on a scratch PATH:

    - the condition is a list that ends in `[[ ... ]]`: `if [[ -n x ]] git push` pushed once, `n=0; while [[ $((n++)) -lt 2
      ]] git push` and `until [[ $((n++)) -ge 2 ]] git push` twice, and only the last `]]` ends it (`if [[ -n x ]] && [[ -n y
      ]] echo both`, `if true && [[ -n x ]] echo both` and the `||` form all ran their body, while `if [[ -n x ]] | cat` is a
      parse error);
    - the body is one sublist, a `{ list }`, a `( list )` or, with no terminator between, `then ... fi` or `do ... done`
      (`if [[ -n x ]] then echo t; fi` and `while [[ $((n++)) -lt 2 ]] do echo t; done` both ran).  The sublist ends where a
      short loop's does: `if [[ -n x ]] echo a; echo b` printed a once and b once, and `echo a > f1; echo b > f2` wrote one
      line each;
    - the body runs in the shell itself, so a cd in a sublist or a `{ }` body moves it and a `( )` body's does not (`cd /;
      if [[ -n x ]] cd /tmp; pwd` printed /tmp, `if [[ -z x ]] cd /tmp; pwd` printed /), which is SPD-030's model: either
      directory follows the conditional;
    - the `{ }` body wants a terminator after it, so `if [[ -n x ]] { git push }` alone is a zsh parse error while `if c { a
      }; echo after`, `if c { a } fi` and `if c { a } else { b }` all run (an `elif` needs a trailing `else`); `( )` takes no
      else.  The hook reads the brace body wherever it stands, fail closed and at no real cost: the line is a shell error
      where it is not one of the forms that run.  It also closes a `{ list }` at a `}` that follows a word with no terminator
      before it, as zsh does and bash does not, so both branches of `if c { a } else { b }` are read;
    - the single-bracket spelling runs nothing and is left alone: `if [ -n x ] git push` is a parse error, `while [ ... ]
      body` never iterates, and `until [ -n x ] body` spins with an empty body.  `if true git push` and `if [[ -n x ]]; git
      push` are parse errors too, and `if (( 1 )) git push` and `while (( n++ < 2 )) git push` were read before this ticket,
      because `(( ))` opens a subshell frame and closes it, leaving what follows in the condition list (still so since
      SPD-088 marked the arithmetic between the parentheses: one frame there now, where it used to be two).

    AGENT_A plans tests/** and bin/spud; AGENT_C plans **; note.txt is refused to AGENT_A at the home."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/zzone", "bin"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")

    def member_payloads(self):
        """(command, the reason a member is refused for it): Law 7, Law 6, Law 5's --as, the database, and Law 1 through a
        redirection and through tee."""
        home, spud = self.home.path, self.spud_cli
        return (("git push", "Law 7"),
                ("%s ticket new --title x" % spud, "Law 6"),
                ("%s --as spud member log hi" % spud, "Law 6"),
                ("%s --as %s member log hi" % (spud, AGENT_B), "--as"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "generated"),
                ("echo x | tee ledger/tickets/SPD-001.md", "generated"))

    def spud_payloads(self):
        """Spud is never refused for git; these are the checks that do apply to him."""
        home, spud = self.home.path, self.spud_cli
        return (("%s --as %s member log hi" % (spud, AGENT_A), "--as"),
                ("%s hook PreToolUse" % spud, "hook"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "Law 1"),
                ("echo x | tee ledger/tickets/SPD-001.md", "Law 1"))

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    # -- the hole ------------------------------------------------------------------------
    def test_the_tickets_evidence_commands_are_refused(self):
        """Caribe's four lines: each ran git push in zsh while the hook recorded no finding."""
        for line in ("if [[ -n x ]] git push",
                     "n=0; while [[ $((n++)) -lt 2 ]] git push",
                     "n=0; while [[ $((n++)) -lt 2 ]] { git push }",
                     "n=0; until [[ $((n++)) -ge 2 ]] git push"):
            with self.subTest(line=line):
                r = self.assertRefused(line, "Law 7")
                self.assertIn("git push", r.reason)
                self.assertRefused(line, "Law 7", AGENT_C)
                self.assertSilent(line, agent_id=None)  # Law 7 refuses members only
        self.assertEqual(self.analysis("if [[ -n x ]] git push").findings, [("git", ("push", "push"))])

    def test_every_condition_reaches_its_body(self):
        for condition in SHORT_CONDITIONS:
            with self.subTest(condition=condition):
                self.assertRefused(condition % "git push", "Law 7")
                self.assertRefused(condition % "git push", "Law 7", AGENT_C)

    def test_every_body_form_reaches_its_commands(self):
        for condition in ("if [[ -n x ]] %s", "n=0; while [[ $((n++)) -lt 2 ]] %s", "n=0; until [[ $((n++)) -ge 2 ]] %s"):
            for body in SHORT_CONDITION_BODIES:
                with self.subTest(condition=condition, body=body):
                    self.assertRefused(condition % (body % "git push"), "Law 7")
        # `then` and `do` with no terminator before them, which zsh takes as the long body
        self.assertRefused("if [[ -n x ]] then git push; fi", "Law 7")
        self.assertRefused("n=0; while [[ $((n++)) -lt 2 ]] do git push; done", "Law 7")

    def test_every_payload_in_every_body_for_every_caller(self):
        for condition in ("if [[ -n x ]] %s", "n=0; while [[ $((n++)) -lt 2 ]] %s", "if true && [[ -n x ]] %s"):
            for body in ("%s", "{ %s }", "( %s )"):
                for command, needle in self.member_payloads():
                    line = condition % (body % command)
                    for agent_id in (AGENT_C, AGENT_A):
                        with self.subTest(line=line, agent_id=agent_id):
                            self.assertRefused(line, needle, agent_id)
                for command, needle in self.spud_payloads():
                    line = condition % (body % command)
                    with self.subTest(line=line, agent_id="spud"):
                        self.assertRefused(line, needle, agent_id=None)

    def test_a_target_outside_a_narrow_members_deliverables(self):
        """AGENT_A plans tests/** and bin/spud, so note.txt at the home is refused it and allowed the ** member."""
        for condition in ("if [[ -n x ]] %s", "n=0; while [[ $((n++)) -lt 2 ]] %s", "if [[ -n x ]] && [[ -n y ]] %s"):
            for body in ("%s", "{ %s }", "( %s )"):
                line = condition % (body % "echo x > note.txt")
                with self.subTest(line=line):
                    self.assertRefused(line, "deliverables")
                    self.assertSilent(line, AGENT_C)

    # -- the directory model ---------------------------------------------------------------
    def test_either_directory_follows_a_short_conditional(self):
        """The condition may be false, so what runs after the body may be in either directory (SPD-030's model, which pop
        already computes for `if ... then ... fi`); a `( )` body's cd does not carry out at all."""
        out, home = self.out, self.home.path
        for condition in ("if [[ -n x ]] %s", "if [[ -n x ]] && [[ -n y ]] %s"):
            for body in ("%s", "{ %s; }", "then %s; fi"):
                with self.subTest(condition=condition, body=body):
                    self.assertRefused((condition % (body % ("cd %s" % out))) + "; echo x > note.txt", "deliverables")
                    self.assertRefused((condition % (body % ("cd %s/ledger" % home))) + "; echo x > tickets/SPD-001.md",
                                       "generated", AGENT_C)
            with self.subTest("a subshell body does not move the shell: " + condition):
                self.assertSilent((condition % ("(cd %s)" % out)) + "; echo x > tests/zzone/k.py")
        # a while or until body may repeat, so a relative cd there is unfollowable, as in the long form
        for condition in ("n=0; while [[ $((n++)) -lt 2 ]] %s", "n=0; until [[ $((n++)) -ge 2 ]] %s"):
            for body in ("%s", "{ %s; }"):
                with self.subTest(condition=condition, body=body):
                    self.assertRefused((condition % (body % "cd docs")) + "; echo x > note.txt", "cannot follow")
                    self.assertRefused((condition % (body % ("cd %s" % out))) + "; echo x > note.txt", "deliverables")
        self.assertEqual(self.analysis("if [[ -n x ]] (cd /tmp)").cwds, frozenset([str(self.home.path)]))
        self.assertEqual(sorted(self.analysis("if [[ -n x ]] cd /tmp").cwds), sorted([str(self.home.path), "/tmp"]))

    def test_the_sublist_body_ends_where_zsh_ends_it(self):
        """`if [[ -n x ]] echo a > f1; echo b > f2` left one line in each, so what follows the sublist is outside the body."""
        home = self.home.path
        line = "if [[ -n x ]] echo a > ledger/tickets/SPD-001.md; echo b > tests/zzone/k.py"
        self.assertEqual(len(self.analysis(line).redirects), 2, self.analysis(line).redirects)
        self.assertRefused("if [[ -n x ]] true; echo x > ledger/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertSilent("if [[ -n x ]] true; echo x > tests/zzone/k.py")
        self.assertRefused("if [[ -n x ]] git push; echo b", "Law 7")
        self.assertSilent("if [[ -n x ]] git status; echo b")
        # the `{ }` body ends at its brace, and an else may follow it
        self.assertRefused("if [[ -n x ]] { git status } else { git push }", "Law 7")
        self.assertRefused("if [[ -n x ]] { cd %s } else { cd %s/ledger }; echo x > tickets/SPD-001.md" % (self.out, home),
                           "generated", AGENT_C)

    # -- where a short conditional may stand ------------------------------------------------
    def test_short_conditionals_nested_and_enclosed(self):
        for line in ("if [[ -n x ]] if [[ -n y ]] git push",
                     "if [[ -n x ]] if true; then git push; fi",
                     "if [[ -n x ]] case x in x) git push;; esac",
                     "if [[ -n x ]] repeat 2 git push",
                     "repeat 1 if [[ -n x ]] git push",
                     "for f (a) if [[ -n x ]] git push",
                     "for f in a b; do if [[ -n x ]] git push; done",
                     "if [[ -n x ]] true && git push",
                     "if [[ -n x ]] false || git push",
                     "if [[ -n x ]] git push | cat",
                     "if [[ -n x ]] git push &",
                     "n=0; while [[ $((n++)) -lt 2 ]] git push & wait",
                     "if [[ -n x ]] git push > out 2>&1",
                     "{ if [[ -n x ]] git push; }",
                     "( if [[ -n x ]] git push )",
                     "f() { if [[ -n x ]] git push; }; f",
                     "eval 'if [[ -n x ]] git push'",
                     "sh -c 'if [[ -n x ]] git push'",
                     "zsh -c 'if [[ -n x ]] git push'",
                     "echo $(if [[ -n x ]] git push)",
                     "echo `while [[ -n x ]] git push`",
                     "coproc if [[ -n x ]] git push",
                     "time if [[ -n x ]] git push",
                     "! if [[ -n x ]] git push",
                     "case x in x) if [[ -n x ]] git push;; esac",
                     "case x in x) if [[ -n x ]] git push;; y) git status;; esac",
                     "if true; then if [[ -n x ]] git push; fi"):
            with self.subTest(line=line):
                self.assertRefused(line, "Law 7")
                self.assertRefused(line, "Law 7", AGENT_C)
        # a short conditional closed above a case frame must not swallow the arm's `;;`
        ledger, out = self.home.path / "ledger", self.out
        self.assertRefused("case x in a) if [[ -n x ]] cd %s;; b) cd %s;; esac; echo x > tickets/SPD-001.md" % (ledger, out),
                           "generated", AGENT_C)

    @wall_clock
    def test_bounded_on_pathological_input(self):
        m = load_spud_module()
        for line in ("if [[ -n x ]] " * 2000 + "git push", "if [[ -n x ]] { " * 500 + "git push" + " }" * 500,
                     "while [[ -n x ]] ( " * 200 + "git push" + " )" * 200, "if [[ -n x ]] && " * 2000 + "[[ -n y ]] git push",
                     "if [[ " * 2000, "while [[ -n x ]] " * 2000):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = m.analyse_command(line, m.ShellAnalysis(cwd=str(self.home.path)))
                self.assertLess(time.monotonic() - started, 5.0)
                if "git push" in line:
                    self.assertIn(("git", ("push", "push")), a.findings, line[:40])

    # -- controls -----------------------------------------------------------------------------
    def test_the_forms_that_were_read_before_are_read_the_same_way(self):
        home, out = self.home.path, self.out
        for refused in ("if (( 1 )) git push", "n=0; while (( n++ < 2 )) git push", "n=0; while (( n++ < 2 )) { git push }",
                        "if [[ -n x ]]; then git push; fi", "while [[ -n x ]]; do git push; done",
                        "until [[ -n x ]]; do git push; done", "if [[ -n x ]]; then true; else git push; fi",
                        "if [[ -n x ]]; then true; elif [[ -n y ]]; then git push; fi",
                        "[[ -n x ]] && git push", "[[ -n x ]]; git push", "[[ -n x ]] || git push",
                        "if true; then [[ -n x ]]; git push; fi", "for f in a; do [[ -n x ]]; git push; done"):
            with self.subTest(refused):
                self.assertRefused(refused, "Law 7")
        # an ordinary `[[ ... ]]` test in command position is untouched, and so is the long form's directory model
        for ok in ("[[ -n x ]] && git status", "[[ -n x ]]", "if [[ -n x ]]; then git status; fi",
                   "if true; then [[ -n x ]] && git status; echo after; fi",
                   "echo '[[ -n x ]] git push'", "echo \"if [[ -n x ]] git push\"",
                   "grep -n 'if [[ -n x ]] git push' tests/zzone/k.py",
                   "%s --as %s member log 'if [[ -n x ]] git push'" % (self.spud_cli, AGENT_A),
                   "if [[ -n x ]]; then echo hi; fi", "if [ -n x ]; then git status; fi"):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
        self.assertSilent("if [[ -n x ]]; then cd %s; fi; echo x > tests/zzone/k.py" % out)
        self.assertRefused("if [[ -n x ]]; then cd %s; fi; echo x > note.txt" % out, "deliverables")
        self.assertRefused("if [[ -n x ]]; then cd %s/ledger; fi; echo x > tickets/SPD-001.md" % home, "generated", AGENT_C)

    def test_the_single_bracket_spellings_run_nothing_and_stay_silent(self):
        """`[ ... ]` is an ordinary command, so zsh's short form never reaches a body through it (probed: `if [ -n x ] git
        push` and `if true git push` are parse errors, `while [ $n -lt 0 ] git push` never iterates, and `until [ -n x ] git
        push` spins with an empty body).  The hook leaves all of them as they were."""
        for ok in ("if [ -n x ] git push", "if [ -n x ] { git push }", "n=0; while [ $n -lt 0 ] git push",
                   "if true git push", "if grep -q ']]' tests/zzone/k.py; then git status; fi",
                   "test -n x && git status"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        self.assertEqual(self.analysis("if [ -n x ] git push").findings, [])


class AliasEvalTest(BashHookCase):
    """SPD-059 (Burbank's SPD-054 proposal): `alias NAME=body` stores shell text the hook never read, and `eval NAME` on the
    same line ran it, so a member's VCS write behind an alias reached the hook with no finding (Law 7).  Probed in bash 3.2,
    zsh 5.9 -f, zsh -f -o nobareglobqual (this Mac's Bash tool) and sh, with a fake git first on a scratch PATH:

    - `alias gp='git push'; eval gp` pushed in zsh, zsh-nbgq and sh.  bash 3.2 expands no alias non-interactively without
      `shopt -s expand_aliases`, so it pushed nothing -- noted, and never relied on: the hook reads the line for the shells
      that do;
    - a shell expands an alias when it parses the text, before the line runs, so an alias defined on the line reaches only
      text the line parses again.  `alias g=git; g push` ran in none of the four, and neither did `eval 'sh -c gp'` or `eval
      'zsh -c gp'`; `eval 'echo $(gp)'` did, the substitution being parsed by the shell that holds the alias, and so did
      `eval 'eval gp'`, a function body's eval and an eval in a subshell;
    - the name is expanded in command position only: `eval 'X=1 gp'`, `eval 'time gp'`, `eval '! gp'` and `eval 'coproc gp'`
      ran it, `eval 'command gp'` and `eval 'env gp'` ran nothing.  An alias shadows a function of the same name;
    - the words after the name follow the body (`alias gp=git; eval 'gp push'` and `alias gp='git push'; eval gp extra` both
      pushed), the body may hold operators and redirections of its own, aliases chain, zsh's `alias -g` and several
      definitions on one `alias` line all take, and `unalias` and `unalias -a` clear.

    The body is now read as the shell text it is, with its own quotes, as eval's rejoined words and a trap's action are: a
    finding inside it is the finding it would be on the line.  A body the hook cannot read (it holds an expansion or a
    substitution, whose value is not on the line), a definition or an `unalias` that may not have run, and a definition whose
    name the hook cannot read each refuse a member where eval dispatches the name; Spud is refused by none of those, and
    keeps his own checks inside the body (Law 1, Law 5's `--as`, the database).  The body runs in the line's own shell, so a
    cd there moves the line, exactly as eval's own words do (probed: `alias cdd='cd /tmp'; eval cdd; pwd` printed /tmp, and
    `echo x > note.txt` after it wrote /tmp/note.txt).  AGENT_A plans tests/** and bin/spud; AGENT_C plans **.

    SPD-105: zsh's special `aliases` association defines an alias too, and the hook read it as nothing.  Probed in zsh 5.9 -f
    and -o nobareglobqual: `aliases[gp]='echo SH'; eval gp`, `aliases=(gp 'echo SH'); eval gp` and `typeset
    'aliases[gp]=echo SH'; eval gp` ran it, `galiases[GP]=` and `saliases[txt]=` define what `alias -g` and `alias -s` do,
    `dis_aliases[gp]=` a disabled alias that ran nothing, and `aliases[gp]=...; gp` ran nothing, as `alias` does.
    `aliases+=(gp 'echo SH'); eval gp` defined nothing in 5.9; it is recorded all the same, refusing on doubt.  Each
    element is now recorded as an `alias` line's `name=body` is, and a key the hook cannot read leaves every name in doubt."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        for rel in ("tests/keep.py", "ledger/tickets/SPD-001.md"):
            p = self.home.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")

    def refused_for_members(self, command, needle="Law 7", cwd=None):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id, cwd)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None, cwd=cwd)
        return r

    def silent_for_everyone(self, command, cwd=None):
        for agent_id in (AGENT_C, AGENT_A, None):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertSilent(command, agent_id, cwd)

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def test_the_tickets_evidence_command(self):
        self.refused_for_members("alias gp='git push'; eval gp")
        self.assertEqual(self.analysis("alias gp='git push'; eval gp").findings, [("git", ("push", "push"))])
        self.assertEqual(self.analysis("alias gp='git status'; eval gp").findings, [("git", ("status", None))])
        self.assertEqual(self.analysis("alias gp='git push'; eval gp").aliases, {"gp": "git push"})

    def test_every_spelling_of_the_definition(self):
        for cmd in ("alias gp='git push'; eval gp", 'alias gp="git push"; eval gp', "alias gp=git\\ push; eval gp",
                    "alias -g GP='git push'; eval GP", "alias -- gp='git push'; eval gp",
                    "alias gp='git push'\neval gp", "alias gp=git; eval 'gp push'",
                    "alias gp='git push'; eval \"gp\"", "alias gp='git push'; eval 'gp'",
                    "alias gp='git push'; eval gp extra", "alias gp='git '; eval 'gp push'",
                    "builtin alias gp='git push'; eval gp", "alias gp='git push'; builtin eval gp"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_several_definitions_on_one_alias_line(self):
        line = "alias gp='git push' gs='git status' e='echo hi'"
        self.refused_for_members(line + "; eval gp")
        self.assertEqual(self.analysis(line).aliases, {"gp": "git push", "gs": "git status", "e": "echo hi"})
        self.assertEqual(self.analysis(line + "; eval gs").findings, [("git", ("status", None))])
        self.silent_for_everyone(line + "; eval e")

    def test_the_body_is_read_as_the_shell_text_it_is(self):
        """Operators, redirections, a spud call and a nested construct inside the body are the findings they would be."""
        spud, home = self.spud_cli, self.home.path
        self.refused_for_members("alias gp='echo a; git push'; eval gp")
        self.refused_for_members("alias gp='true && git push'; eval gp")
        self.refused_for_members("alias gp='{ git push; }'; eval gp")
        self.refused_for_members("alias gp='if true; then git push; fi'; eval gp")
        self.refused_for_members("alias gp='sh -c \"git push\"'; eval gp")
        self.refused_for_members("alias gp='eval \"git push\"'; eval gp")
        self.refused_for_members("alias t='%s ticket new --title x'; eval t" % spud, "Law 6")
        self.assertRefused("alias l='%s --as %s member log hi'; eval l" % (spud, AGENT_B), "--as", AGENT_A)
        for agent_id in (AGENT_C, AGENT_A, None):  # the database is refused to everyone, Spud included
            self.assertRefused("alias d='sqlite3 %s/.spud/ledger.db \"select 1\"'; eval d" % home, "spud sql --readonly", agent_id)
        self.assertRefused("alias e='echo x > ledger/tickets/SPD-001.md'; eval e", "generated")
        self.assertRefused("alias e='echo x > ledger/tickets/SPD-001.md'; eval e", "Law 1", agent_id=None)
        self.assertRefused("alias e='echo x | tee ledger/tickets/SPD-001.md'; eval e", "generated")
        self.assertRefused("alias e='echo x > note.txt'; eval e", "deliverables")
        self.assertSilent("alias e='echo x > note.txt'; eval e", AGENT_C)
        # a cd in the body moves the line's own shell, as eval's own words do
        self.assertEqual(self.analysis("alias c='cd /tmp'; eval c").cwds, frozenset(["/tmp"]))
        # ... so a relative target after the eval is checked where the shell really is (probed: it wrote /tmp/note.txt)
        self.assertEqual([c for _, c in self.analysis("alias c='cd /tmp'; eval c; echo x > note.txt").redirects],
                         [frozenset(["/tmp"])])

    def test_the_line_inside_every_construct_that_reaches_the_alias(self):
        for cmd in ("alias gp='git push'; eval 'eval gp'", "alias gp='git push'; eval 'echo $(gp)'",
                    "alias gp='git push'; eval 'true && gp'", "alias gp='git push'; eval 'gp; gp'",
                    "alias gp='git push'; eval '{ gp; }'", "alias gp='git push'; eval 'if true; then gp; fi'",
                    "alias gp='git push'; eval 'for f in a; do gp; done'", "alias gp='git push'; eval 'X=1 gp'",
                    "alias gp='git push'; eval 'time gp'", "alias gp='git push'; eval '! gp'",
                    "alias gp='git push'; eval 'coproc gp'", "alias gp='git push'; f() { eval gp; }; f",
                    "alias gp='git push'; (eval gp)", "alias a=b; alias b='git push'; eval a",
                    "alias gp='git push'; X=gp; eval $X"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_a_definition_that_may_not_have_run_is_refused(self):
        """A branch, a subshell, a pipeline, a background list, a loop or function body, and an `unalias` in any of them:
        the hook cannot be sure which alias the shell holds, so it fails closed."""
        for cmd in ("if true; then alias gp='git status'; fi; eval gp",
                    "false && alias gp='git status'; eval gp",
                    "true || alias gp='git status'; eval gp",
                    "(alias gp='git status'); eval gp",
                    "alias gp='git status' | cat; eval gp",
                    "alias gp='git status' & wait; eval gp",
                    "for f in a; do alias gp='git status'; done; eval gp",
                    "f() { alias gp='git status'; }; f; eval gp",
                    "case x in x) alias gp='git status';; esac; eval gp",
                    "alias gp='git status'; if true; then unalias gp; fi; eval gp",
                    "alias gp='git status'; (unalias gp); eval gp",
                    "alias gp='git status'; unalias -m 'g*'; eval gp"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "cannot resolve")
        # a refusal the body itself earns keeps its own reason
        self.assertIn("Law 7", self.refused_for_members("if true; then alias gp='git push'; fi; eval gp", "").reason)

    def test_a_body_or_a_name_the_hook_cannot_read_is_refused(self):
        for cmd in ("alias gp=\"$UNSET git push\"; eval gp", "alias gp=\"$(echo git push)\"; eval gp",
                    "alias gp=\"`echo git push`\"; eval gp", "X=git; alias gp=\"$X push\"; eval gp",
                    "alias gp=$'git push'; eval gp", "N=gp; alias $N='git push'; eval gp",
                    "alias ${N}='git push'; eval gp"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "cannot resolve")

    def test_unalias_clears(self):
        for ok in ("alias gp='git push'; unalias gp; eval gp",
                   "alias gp='git push'; unalias -a; eval gp",
                   "alias gp='git push' gs='git status'; unalias gp gs; eval gp"):
            self.silent_for_everyone(ok)
        self.assertEqual(self.analysis("alias gp='git push'; unalias gp").aliases, {"gp": None})

    def test_an_alias_reaches_no_further_than_the_shell_that_holds_it(self):
        """Nothing on the line but eval parses the name again, so nothing else expands it (each probed to run nothing)."""
        for ok in ("alias gp='git push'; gp", "alias g=git; g push", "alias gp='git push'; echo gp",
                   "alias gp='git push'; sh -c 'eval gp'", "alias gp='git push'; zsh -c 'eval gp'",
                   "alias gp='git push'; eval 'command gp'", "alias gp='git push'; eval 'env gp'",
                   # SPD-084 confirmed the alias command-position gate is right where a function's is not: zsh keeps a
                   # function lookup after noglob/-/exec but does not expand an alias there (each ran nothing, probed)
                   "alias gp='git push'; eval 'noglob gp'", "alias gp='git push'; eval '- gp'",
                   "alias gp='git push'; eval 'exec gp'",
                   "alias gp='git push'; sh -c gp", "alias gp='git push'; sh <<'EOF'\neval gp\nEOF",
                   "eval gp", "alias", "alias -p", "alias gp", "unalias gp", "echo alias gp='git push'",
                   "grep -n \"alias gp\" tests/keep.py", "env alias gp='git push'; eval gp"):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
                self.assertNotEqual(self.bash(ok, agent_id=None).decision, "deny", ok)
        # the words of a spud call are the call's, whatever they spell
        self.assertAllowed("%s --as %s member log \"alias gp='git push'; eval gp\"" % (self.spud_cli, AGENT_A))

    def test_an_aliasing_line_is_never_allowed_on_its_own(self):
        """`alias` is not a spud call, so a line that defines one is read, never allowed."""
        self.assertSilent("alias gp='git push'; %s --as %s member log hi" % (self.spud_cli, AGENT_A))
        self.assertAllowed("%s --as %s member log hi" % (self.spud_cli, AGENT_A))

    def test_the_zsh_aliases_parameter_defines_an_alias(self):
        for cmd in ("aliases[gp]='git push'; eval gp", "aliases=(gp 'git push'); eval gp", "aliases+=(gp 'git push'); eval gp",
                    "typeset 'aliases[gp]=git push'; eval gp", "galiases[GP]='git push'; eval GP",
                    "aliases[gp]='git push' true; eval gp", "aliases=(e 'echo hi' gp 'git push'); eval gp"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        self.assertEqual(self.analysis("aliases[gp]='git push'").aliases, {"gp": "git push"})
        self.assertEqual(self.analysis("aliases=(e 'echo hi' gp 'git push')").aliases, {"e": "echo hi", "gp": "git push"})
        self.assertEqual(self.analysis("aliases[gp]='git status'; eval gp").findings, [("git", ("status", None))])
        self.silent_for_everyone("aliases[e]='echo hi'; eval e")
        # a key or a body the hook cannot read, or a definition that may not have run
        for cmd in ("aliases[$k]='git status'; eval gp", "aliases+=($pairs); eval gp", "aliases[g.p]='git status'; eval gp",
                    "aliases[gp]+=' status'; eval gp", "aliases[gp]=\"$X\"; eval gp", "(aliases[gp]='git status'); eval gp",
                    "if true; then aliases[gp]='git status'; fi; eval gp"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "cannot resolve")
        # nothing but eval parses the name again, and a disabled alias defines nothing
        for ok in ("aliases[gp]='git push'; gp", "dis_aliases[gp]='git push'; eval gp", "echo $aliases[gp]"):
            with self.subTest(ok):
                self.assertNotEqual(self.bash(ok).decision, "deny", ok)
                self.assertNotEqual(self.bash(ok, agent_id=None).decision, "deny", ok)

    def test_spud_keeps_his_own_checks_inside_the_body(self):
        spud = self.spud_cli
        self.assertRefused("alias l='%s --as %s member log hi'; eval l" % (spud, AGENT_A), "Law 5", agent_id=None)
        self.assertRefused("alias h='%s hook PreToolUse'; eval h" % spud, "hook", agent_id=None)
        self.assertRefused("alias e='echo x | tee ledger/tickets/SPD-001.md'; eval e", "Law 1", agent_id=None)


class NamedCoprocTest(BashHookCase):
    """SPD-060: bash 4 and later accept a name before a coproc's compound command, `coproc NAME compound_command`, and run
    the group in a forked shell; the hook read NAME for the command word, so `coproc NAME { git push; }` was kind other with
    no finding, while `coproc { git push; }`, `coproc git push`, `coproc ( git push )` and `coproc sh -c 'git push'` each
    found it.  No bash 4+ is on this Mac (bash 3.2 has no coproc and zsh's takes a command only), so the forms were probed in
    bash 5.2 in the ubuntu:24.04 podman image, with a file side effect, since the group's own stdout goes to the pipe:

    - `coproc NAME { echo ran >> /tmp/m; }` wrote the file, and so did the `( ... )`, `while`, `for`, `if`, `case` and
      `[[ ... ]]` forms after a name, a group nested in the group, the form with a redirection after it, and one inside a
      pipeline.  A name holding an expansion ran the group too (`N=NAME; coproc $N { ... }`, `${N}`, `$(echo NAME)`);
    - a name that is not a valid identifier ran nothing (`coproc 1bad { ... }` and `coproc na-me { ... }` printed "not a
      valid identifier"), and `coproc NAME{` with no blank is a syntax error;
    - `coproc NAME echo ran` and `coproc NAME sh -c '...'` printed "NAME: command not found": a simple command named NAME,
      not a named coproc.  That reading is left exactly as it was;
    - a cd inside the group never reaches the line (`coproc NAME { cd /tmp; pwd; }; wait; pwd` printed /), as SPD-054 already
      had it for the unnamed form;
    - zsh 5.9 is a parse error for every named form, so nothing runs there.

    The group after a valid identifier is now read exactly as the unnamed form's is, and a name the hook cannot resolve
    refuses a member as SPD-043's command word does.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        for rel in ("tests/keep.py", "ledger/tickets/SPD-001.md"):
            p = self.home.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("orig\n", encoding="utf-8")

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)
        return r

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def test_the_tickets_evidence_command(self):
        self.refused_for_members("coproc NAME { git push; }")
        self.assertEqual(self.analysis("coproc NAME { git push; }").findings, [("git", ("push", "push"))])
        self.assertEqual(self.analysis("coproc NAME { git status; }").findings, [("git", ("status", None))])

    def test_every_compound_command_after_a_name(self):
        for cmd in ("coproc NAME { git push; }", "coproc NAME { git push }", "coproc NAME ( git push )",
                    "coproc NAME while true; do git push; done", "coproc NAME until false; do git push; done",
                    "coproc NAME for i in 1; do git push; done", "coproc NAME select f in a; do git push; done",
                    "coproc NAME if true; then git push; fi", "coproc NAME case x in x) git push;; esac",
                    "coproc NAME { { git push; } }", "coproc NAME { git push; } 2>/dev/null",
                    "coproc _n1 { git push; }", "coproc NAMe { git push; }", "coproc N2 { git push; }",
                    "coproc NAME { sh -c 'git push'; }", "coproc NAME { eval 'git push'; }",
                    "coproc NAME { trap 'git push' EXIT; }", "coproc NAME { for f in a; do git push; done; }"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_a_name_the_hook_cannot_resolve_is_refused(self):
        for cmd in ("coproc $N { git push; }", "coproc ${N} { git push; }", "coproc $(echo NAME) { git push; }",
                    "coproc `echo NAME` { git push; }"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "cannot resolve")
        # a name the line assigned for certain resolves, and the group behind it is read
        self.refused_for_members("N=NAME; coproc $N { git push; }")

    def test_the_payloads_inside_a_named_group(self):
        spud, home = self.spud_cli, self.home.path
        self.refused_for_members("coproc NAME { %s ticket new --title x; }" % spud, "Law 6")
        self.assertRefused("coproc NAME { %s --as %s member log hi; }" % (spud, AGENT_B), "--as", AGENT_A)
        self.assertRefused("coproc NAME { %s --as %s member log hi; }" % (spud, AGENT_A), "Law 5", agent_id=None)
        for agent_id in (AGENT_C, AGENT_A, None):  # the database is refused to everyone, Spud included
            self.assertRefused("coproc NAME { sqlite3 %s/.spud/ledger.db 'select 1'; }" % home, "spud sql --readonly", agent_id)
        self.assertRefused("coproc NAME { echo x > ledger/tickets/SPD-001.md; }", "generated")
        self.assertRefused("coproc NAME { echo x > ledger/tickets/SPD-001.md; }", "Law 1", agent_id=None)
        self.assertRefused("coproc NAME { echo x > note.txt; }", "deliverables")
        self.assertSilent("coproc NAME { echo x > note.txt; }", AGENT_C)

    def test_a_cd_in_the_group_never_reaches_the_line(self):
        """The forked shell's directory is not the line's, as SPD-054 has it for the unnamed form."""
        for cmd in ("coproc NAME { cd /tmp; }", "coproc NAME { cd /tmp }", "coproc NAME ( cd /tmp )",
                    "coproc NAME { cd /tmp; git status; }"):
            with self.subTest(cmd):
                self.assertEqual(self.analysis(cmd).cwds, frozenset([str(self.home.path)]))
        self.assertSilent("coproc NAME { cd /tmp; }; echo x > tests/keep.py")

    def test_a_simple_command_named_by_the_word_is_read_as_before(self):
        """`coproc word args` is a command named `word` in every shell, and an invalid identifier runs nothing anywhere."""
        for ok in ("coproc NAME git push", "coproc NAME sh -c 'git push'", "coproc NAME echo x",
                   "coproc 1bad { git push; }", "coproc na-me { git push; }", "coproc NAME.x { git push; }",
                   "echo coproc NAME { git push; }", "grep -n 'coproc NAME' tests/keep.py",
                   "%s --as %s member log 'coproc NAME { git push; }'" % (self.spud_cli, AGENT_A)):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
        self.assertEqual(self.analysis("coproc NAME git push").findings, [])

    def test_the_unnamed_forms_are_unchanged(self):
        for refused in ("coproc { git push; }", "coproc ( git push )", "coproc git push", "coproc sh -c 'git push'",
                        "coproc repeat 1 git push", "coproc for f (a) git push", "coproc { trap 'git push' EXIT; }",
                        "coproc while true; do git push; done"):
            with self.subTest(refused):
                self.refused_for_members(refused)
        for cmd in ("coproc { cd /tmp; }", "coproc cd /tmp"):
            with self.subTest(cmd):
                self.assertEqual(self.analysis(cmd).cwds, frozenset([str(self.home.path)]))
        for ok in ("coproc { echo hi; }", "coproc echo hi", "coproc git status"):
            with self.subTest(ok):
                self.assertSilent(ok)


# zsh's prefixed groups (SPD-081): a `{ list }` whose `{` follows only `coproc`, `time` and `!`.  Each fills its `%s` with
# the body the group runs, and PREFIXED_GROUP_EVIDENCE is Spud's probe, line for line.
PREFIXED_GROUP_EVIDENCE = ("coproc { repeat 1 %s }", "coproc { repeat 1 %s; }", "coproc { if [[ -n x ]] %s }",
                           "coproc { for f (a) %s }", "time { repeat 1 %s; }", "! { repeat 1 %s; }",
                           "time { for f (a) %s }", "! { while [[ -n x ]] %s }", "time ! { repeat 1 %s }",
                           "! time { repeat 1 %s }", "time coproc { repeat 1 %s; }", "coproc time { repeat 1 %s; }",
                           "time { { repeat 1 %s } }")
# The forms a shell parses but does not run: read all the same, which costs a line no shell accepts.
PREFIXED_GROUP_PARSE_ERRORS = ("! coproc { repeat 1 %s }", "! ! { repeat 1 %s }")
# The prefixes whose group runs in a forked shell of its own, and those whose group runs in the shell that reads the line.
FORKED_GROUPS = ("coproc { %s }", "coproc { %s; }", "time coproc { %s; }", "coproc time { %s; }")
CURRENT_SHELL_GROUPS = ("time { %s; }", "! { %s; }", "time ! { %s; }", "! time { %s; }", "time { { %s; } }")


class PrefixedGroupTest(BashHookCase):
    """SPD-081 (from proposal 85): zsh runs a `{ list }` after `coproc`, `time` and `!`, and ShellWalk opened a group only
    where no word stood before the `{`; after one of those words the brace was appended as an ordinary word, analyse_words
    stripped it as the reserved word it is, and the rest of the line was read as one simple command.  A zsh short loop or
    short conditional inside such a group was flattened into that command's words and never checked, so `coproc { repeat 1
    git push }`, `time { repeat 1 git push; }` and `! { repeat 1 git push; }` were each kind other with no finding, while
    `{ repeat 1 git push }` found the push (Laws 1, 5, 6 and 7).  SPD-042 and SPD-061 had already opened a frame for `for`,
    `select`, `repeat`, `if`, `while` and `until` after those words; `{` was the one left.

    No shell is probed here: this worktree session's harness refuses to run one (SPD-094).  The evidence is Spud's probe of
    2026-09-17, recorded on the ticket -- zsh 5.9 -f and zsh -f -o nobareglobqual (this Mac's Bash tool), identical in both,
    with a function standing in for the VCS program that appends its arguments to a log, since a coproc's stdout goes to its
    pipe and only a file shows that the body ran:

    - the body ran for every form in PREFIXED_GROUP_EVIDENCE above, `coproc { repeat 1 vcs push }` and
      `time { { repeat 1 vcs push } }` included;
    - `! coproc { ... }` and `! ! { ... }` are parse errors and ran nothing.  The hook reads them as groups all the same,
      fail closed and at no real cost: the line is a shell error where it is not one of the forms that run;
    - the line's directory and variables: `time { x=1; cd /tmp; }`, `! { x=1; cd /tmp; }`, `time ! { x=1; cd /tmp; }` and
      `! time { x=1; cd /tmp; }` each left the line in /tmp with x=1 (the current shell), while `coproc { x=1; cd /tmp; }`,
      `time coproc { x=1; cd /tmp; }` and `coproc time { x=1; cd /tmp; }` left the line where it was with x unset (a fork,
      as SPD-054 has it, and as `coproc ( ... )` was already read).  In a pipeline, `time { x=1; cd /tmp; } | cat` and
      `! { x=1; cd /tmp; } | cat` left the line where it was, as `{ cd /tmp; } | cat` does -- the reading main had wrong,
      since it moved the line there.  (A zsh quirk the hook does not chase: `time { x=1; }` alone left x unset, while
      `time { x=1; cd /tmp; }` set it; a variable behind `time` is read as the current shell's, which is what `time x=1`
      already did.)

    So a `{` whose preceding words are all LOOP_PREFIX_WORDS opens a group frame, and the group is read exactly as an
    unprefixed one in the same position -- a short loop, a short conditional, a nested group, a subshell, a spud call, a
    redirection, an eval and an `sh -c` inside it alike.  With `coproc` among those words the frame is a forked shell's:
    its cd and its assignments never reach the line, and a trap set there still fires (SPD-054).  `echo coproc { git push; }`
    and any `{` after an ordinary word stay arguments, SPD-060's `coproc NAME { ... }` keeps its own path, and zsh's
    `{ ... } always { ... }` is SPD-124, not this ticket.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.out = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.out, True)
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/zzone", "bin"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger" / "tickets" / "SPD-001.md").write_text("orig\n", encoding="utf-8")

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path)))

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)  # Law 7 refuses members only
        return r

    def member_payloads(self):
        """(command, the reason a member is refused for it): Law 7, Law 6, Law 5's --as, the database, and Law 1 through a
        redirection and through tee."""
        home, spud = self.home.path, self.spud_cli
        return (("git push", "Law 7"),
                ("%s ticket new --title x" % spud, "Law 6"),
                ("%s --as spud member log hi" % spud, "Law 6"),
                ("%s --as %s member log hi" % (spud, AGENT_B), "--as"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "generated"),
                ("echo x | tee ledger/tickets/SPD-001.md", "generated"))

    def spud_payloads(self):
        """Spud is never refused for git; these are the checks that do apply to him."""
        home, spud = self.home.path, self.spud_cli
        return (("%s --as %s member log hi" % (spud, AGENT_A), "--as"),
                ("%s hook PreToolUse" % spud, "hook"),
                ("sqlite3 %s/.spud/ledger.db 'select 1'" % home, "spud sql --readonly"),
                ("echo x > ledger/tickets/SPD-001.md", "Law 1"),
                ("echo x | tee ledger/tickets/SPD-001.md", "Law 1"))

    # -- the hole --------------------------------------------------------------------------
    def test_the_probes_evidence_forms_reach_the_body(self):
        """Every form Spud's probe ran: the push inside the prefixed group is found and the member refused."""
        for form in PREFIXED_GROUP_EVIDENCE:
            line = form % "git push"
            with self.subTest(line=line):
                r = self.refused_for_members(line)
                self.assertIn("git push", r.reason)
                self.assertIn(("git", ("push", "push")), self.analysis(line).findings, line)
        self.assertEqual(self.analysis("coproc { repeat 1 git push }").findings, [("git", ("push", "push"))])
        self.assertEqual(self.analysis("time { repeat 1 git status; }").findings, [("git", ("status", None))])

    def test_the_forms_no_shell_parses_are_over_read(self):
        """`! coproc { ... }` and `! ! { ... }` run nothing anywhere; reading them as groups refuses a line no shell accepts."""
        for form in PREFIXED_GROUP_PARSE_ERRORS:
            with self.subTest(form=form):
                self.refused_for_members(form % "git push")

    def test_every_body_form_inside_a_prefixed_group(self):
        """A short loop, a short conditional, a nested group, a subshell, an eval and an `sh -c` inside the group are read
        as they are inside an unprefixed one."""
        for form in ("coproc { %s; }", "time { %s; }", "! { %s; }"):
            for body in ("%s", "repeat 1 %s", "for f (a) %s", "if [[ -n x ]] %s", "{ %s }", "( %s )",
                         "if true; then %s; fi", "eval '%s'", "sh -c '%s'", "true && %s", "%s | cat",
                         "for f in a b; do %s; done"):
                line = form % (body % "git push")
                with self.subTest(line=line):
                    self.assertRefused(line, "Law 7")
                    self.assertRefused(line, "Law 7", AGENT_C)

    def test_every_payload_in_every_prefixed_group_for_every_caller(self):
        for form in ("coproc { %s; }", "time coproc { %s; }", "time { %s; }", "! { %s; }"):
            for body in ("%s", "repeat 1 %s", "if [[ -n x ]] %s"):
                for command, needle in self.member_payloads():
                    line = form % (body % command)
                    for agent_id in (AGENT_C, AGENT_A):
                        with self.subTest(line=line, agent_id=agent_id):
                            self.assertRefused(line, needle, agent_id)
                for command, needle in self.spud_payloads():
                    line = form % (body % command)
                    with self.subTest(line=line, agent_id="spud"):
                        self.assertRefused(line, needle, agent_id=None)

    def test_a_target_outside_a_narrow_members_deliverables(self):
        """AGENT_A plans tests/** and bin/spud, so note.txt at the home is refused it and allowed the ** member."""
        for form in ("coproc { %s; }", "coproc time { %s; }", "time { %s; }", "! { %s; }"):
            for body in ("%s", "repeat 1 %s"):
                line = form % (body % "echo x > note.txt")
                with self.subTest(line=line):
                    self.assertRefused(line, "deliverables")
                    self.assertSilent(line, AGENT_C)
                    self.assertSilent(form % (body % "echo x > tests/zzone/k.py"))

    # -- the coproc group's fork (SPD-054) ---------------------------------------------------
    def test_a_coproc_groups_directory_and_assignments_never_reach_the_line(self):
        home, out = str(self.home.path), self.out
        for form in FORKED_GROUPS:
            for body in ("x=1; cd %s" % out, "cd %s" % out, "cd %s; git status" % out, "repeat 1 cd %s" % out):
                line = form % body
                with self.subTest(line=line):
                    self.assertEqual(self.analysis(line).cwds, frozenset([home]), line)
            # ... so what follows the group is in the line's own directory, checked there: the ledger file the same line
            # names through the group's cd is never reached
            self.assertSilent((form % ("cd %s" % out)) + "; echo x > tests/zzone/k.py")
            self.assertSilent((form % ("cd %s/ledger" % home)) + "; echo x > tickets/SPD-001.md", AGENT_C)
        # a variable the forked shell assigns does not hold after it, so a later word that reads it is doubted (SPD-043),
        # exactly as after an unprefixed group
        for line, plain in (("coproc { X=git; }; $X push", "{ X=git; }; $X push"),
                            ("X=git; coproc { X=ls; }; $X push", "X=git; { X=ls; }; $X push")):
            with self.subTest(line=line):
                self.assertIn(("var-doubt", "$X"), self.analysis(line).findings, line)
                self.assertEqual(self.analysis(line).findings, self.analysis(plain).findings, line)
        self.assertRefused("coproc { X=git; }; $X push", "Law 7")
        self.assertRefused("X=git; coproc { X=ls; }; $X push", "may not hold")

    def test_a_trap_set_in_a_coproc_group_still_fires(self):
        """SPD-054: the forked shell's exit fires an EXIT trap set there, so the action is read as it is anywhere else."""
        for line in ("coproc { trap 'git push' EXIT; }", "coproc { repeat 1 trap 'git push' EXIT }",
                     "time coproc { trap 'git push' EXIT; }", "coproc time { trap 'git push' EXIT; }"):
            with self.subTest(line=line):
                self.refused_for_members(line)
        for agent_id in (AGENT_C, AGENT_A, None):  # a target the hook cannot place refuses Spud too (SPD-035)
            self.assertRefused("coproc { trap 'echo x > out.txt' EXIT; }", "cannot follow", agent_id)
        self.assertSilent("coproc { trap 'echo done' EXIT; }")
        self.assertSilent("coproc { trap 'echo done' EXIT; }", agent_id=None)

    # -- the current shell's groups -------------------------------------------------------------
    def test_a_time_or_bang_group_is_read_as_an_unprefixed_group_in_its_place(self):
        home, out = str(self.home.path), self.out
        for form in CURRENT_SHELL_GROUPS:
            for body in ("x=1; cd %s" % out, "cd %s" % out, "git status; cd %s" % out):
                line, plain = form % body, "{ %s; }" % body
                with self.subTest(line=line):
                    a, b = self.analysis(line), self.analysis(plain)
                    self.assertEqual((a.cwds, a.findings, sorted(a.doubt)), (b.cwds, b.findings, sorted(b.doubt)), line)
                    self.assertEqual(a.cwds, frozenset([str(out)]), line)  # the cd moved the line itself
            # ... and what follows the group is checked in the directory the group left it in, not in the line's own
            self.assertRefused((form % ("cd %s/ledger" % home)) + "; echo x > tickets/SPD-001.md", "generated", AGENT_C)
            self.assertSilent((form % ("cd %s" % out)) + "; echo x > note.txt")
            self.assertRefused((form % ("cd %s" % out)) + "; echo x > %s/note.txt" % home, "deliverables")

    def test_a_prefixed_group_in_a_pipeline_leaves_the_line_where_it_was(self):
        """`time { x=1; cd /tmp; } | cat` and `! { x=1; cd /tmp; } | cat` left the line where it was, as `{ cd /tmp; } | cat`
        does; main moved it to /tmp."""
        home, out = str(self.home.path), self.out
        for form in ("time { %s; } | cat", "! { %s; } | cat", "{ %s; } | cat", "time ! { %s; } | cat", "coproc { %s; } | cat"):
            line = form % ("x=1; cd %s" % out)
            with self.subTest(line=line):
                self.assertEqual(self.analysis(line).cwds, frozenset([home]), line)
        self.assertSilent(("time { cd %s; } | cat" % out) + "; echo x > tests/zzone/k.py")
        self.assertRefused(("! { cd %s; } | cat" % out) + "; echo x > note.txt", "deliverables")
        # the commands inside a piped group are still checked
        self.assertRefused("time { git push; } | cat", "Law 7")
        self.assertRefused("! { repeat 1 git push; } | cat", "Law 7")

    def test_the_group_ends_where_its_brace_ends_it(self):
        """What follows the group is outside it: a `}` with no terminator before it closes the group, as zsh closes one."""
        home = str(self.home.path)
        for form in ("time { %s }", "coproc { %s }", "! { %s }"):
            line = form % "echo a > ledger/tickets/SPD-001.md" + "; echo b > tests/zzone/k.py"
            with self.subTest(line=line):
                self.assertEqual(len(self.analysis(line).redirects), 2, self.analysis(line).redirects)
                self.assertRefused(line, "generated")
        self.assertSilent("time { echo a > tests/zzone/k.py }; echo b > tests/zzone/k.py")
        self.assertRefused("coproc { true }; echo x > ledger/tickets/SPD-001.md", "generated", AGENT_C)
        self.assertEqual(self.analysis("time { cd /nowhere-at-all }; git status").kinds[-1], "git")
        self.assertEqual(self.analysis("coproc { git push }").cwds, frozenset([home]))

    # -- controls ----------------------------------------------------------------------------------
    def test_a_brace_after_an_ordinary_word_stays_an_argument(self):
        for ok in ("echo coproc { git push; }", "echo time { git push; }", "echo x coproc { git push; }",
                   "echo '{ git push; }'", "echo \"coproc { git push; }\"",
                   "grep -n 'coproc { git push; }' tests/zzone/k.py",
                   "%s --as %s member log 'coproc { repeat 1 git push }'" % (self.spud_cli, AGENT_A),
                   "printf 'time { git push; }\\n' > tests/zzone/k.py"):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
        self.assertEqual(self.analysis("echo coproc { git push; }").findings, [])

    def test_the_named_form_and_the_simple_commands_are_unchanged(self):
        for refused in ("coproc NAME { git push; }", "coproc NAME { git push }", "coproc git push", "time git push",
                        "! git push", "coproc { git push; }", "coproc ( git push )", "{ git push; }",
                        "coproc repeat 1 git push", "time repeat 1 git push", "! repeat 1 git push",
                        "coproc if [[ -n x ]] git push", "time if [[ -n x ]] git push"):
            with self.subTest(refused):
                self.refused_for_members(refused)
        for ok in ("coproc NAME git push", "coproc { git status; }", "time { git status; }", "! { echo hi; }",
                   "coproc { echo hi; }", "time echo hi", "coproc 1bad { git push; }"):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
        # the named form's group still runs in its own fork, and the unnamed one's cd is still not the line's
        for cmd in ("coproc NAME { cd /tmp; }", "coproc { cd /tmp; }", "coproc cd /tmp"):
            with self.subTest(cmd):
                self.assertEqual(self.analysis(cmd).cwds, frozenset([str(self.home.path)]))

    @wall_clock
    def test_bounded_on_pathological_input(self):
        m = load_spud_module()
        for line in ("coproc { " * 1000 + "git push" + " }" * 1000, "time { " * 1000 + "git push",
                     "! { " * 500 + "repeat 1 git push" + " }" * 500, "coproc time ! { " * 300 + "git push",
                     "time { " * 2000, "coproc { repeat 1 { " * 300 + "git push"):
            with self.subTest(line=line[:40]):
                started = time.monotonic()
                a = m.analyse_command(line, m.ShellAnalysis(cwd=str(self.home.path)))
                self.assertLess(time.monotonic() - started, 5.0)
                if "git push" in line:
                    self.assertIn(("git", ("push", "push")), a.findings, line[:40])


class PathInForceTest(BashHookCase):
    """SPD-062: the hook reads a line's command words by name -- git, spud, python3.14, sqlite3, tee, a shell, a wrapper --
    and the shell then finds each of them on PATH, so a member that puts a directory of its own first runs its own program
    under a name the hook cleared (`PATH=<dir>:$PATH git status` runs <dir>/git).  Probed in zsh 5.9 -f, zsh -f -o
    nobareglobqual as the Bash tool runs it, bash 3.2 and sh, with a fake program in a scratch directory: a prefix
    assignment, a plain assignment, `export`, `typeset -x`, `declare -x`, `local -x` inside a function and
    `env PATH=... cmd` each ran the scratch copy in all four shells, `readonly PATH=...` in bash and sh (zsh refuses to
    write a read-only PATH), and zsh's `path=(<dir> $path)` and `path+=(<dir>)`, the array PATH is tied to.  The
    subscripted forms zsh also takes (`path[1]=<dir>`, `path[1,0]=(<dir>)`) ran it too; they reached the analysis as a
    command word rather than an assignment until SPD-085, and SubscriptAssignmentTest covers them.  bash's `hash -p
    <path> <name>` and zsh's `hash <name>=<path>` put a file of the line's choosing in the shell's command table for the
    same effect, and both ran it; so does an element of zsh's `commands` parameter (SPD-105, probed in zsh 5.9 -f and -o
    nobareglobqual: `commands[foo]=<path>; foo`, `commands+=(foo <path>)`, `commands=(foo <path>)` and `typeset
    'commands[foo]=<path>'` each ran the file, `command foo` too, while `env foo` and `/usr/bin/env foo` ran the real one;
    bash and sh have no such parameter).

    A command run by a path (`/usr/bin/git status`, `./git`) is not looked for on PATH, so a PATH in force does not refuse
    it, and a name the hook grants nothing for (`ls`) stays silent as before; GIT_EXEC_PATH keeps SPD-046's own reason.
    The finding is appended after its own command's, so `PATH=<dir> git push` still answers with Law 7's verb; and since
    SPD-049 `path` and `hashed` carry an entry in FINDING_LAST, so on a line of several commands a refusal the words as
    spelled already earn answers first (`PATH=<dir> git status; git push` names the push -- both refusals, only the
    wording changes).  AGENT_A plans tests/** and bin/spud; AGENT_C plans **; Law 7 does not bind Spud."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)
        return r

    def finding(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path))).findings

    def test_path_by_every_route_before_a_bare_git_is_refused(self):
        for cmd in ("PATH=/tmp/x:$PATH git status", "PATH=/tmp/x git status", "PATH=/tmp/x; git status",
                    "PATH=/tmp/x:$PATH; git status", "export PATH=/tmp/x:$PATH; git status",
                    "typeset -x PATH=/tmp/x; git status", "declare -x PATH=/tmp/x; git status",
                    "readonly PATH=/tmp/x; git status", "local -x PATH=/tmp/x; git status",
                    "env PATH=/tmp/x git status", "/usr/bin/env PATH=/tmp/x:$PATH git status",
                    "PATH=/tmp/x:$PATH git log --oneline", "PATH=/tmp/x:$PATH git diff"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("PATH", r.reason)

    def test_the_zsh_path_array_is_refused(self):
        # zsh ties `path` to PATH (probed): every form of the array assignment replaces the program a bare name finds.
        for cmd in ("path=(/tmp/x $path); git status", "path+=(/tmp/x); git status", "path=(/tmp/x); git status",
                    "path=(/tmp/x $path) git status", "PATH=/tmp/x path=(/tmp/y); git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("path", r.reason)

    def test_every_command_name_the_hook_reads_is_refused(self):
        for cmd in ("PATH=/tmp/x:$PATH git status", "PATH=/tmp/x:$PATH sh -c 'echo hi'",
                    "PATH=/tmp/x:$PATH bash -c 'echo hi'", "PATH=/tmp/x:$PATH zsh -c 'echo hi'",
                    "PATH=/tmp/x:$PATH python3.14 -I -S bin/spud board", "PATH=/tmp/x:$PATH python3 x.py",
                    "PATH=/tmp/x:$PATH tee /tmp/out", "PATH=/tmp/x:$PATH nice git status",
                    "PATH=/tmp/x:$PATH nohup ls", "PATH=/tmp/x:$PATH env ls", "PATH=/tmp/x:$PATH node x.js",
                    "PATH=/tmp/x:$PATH %s board"):
            with self.subTest(cmd):
                self.refused_for_members(cmd if "%s" not in cmd else cmd % self.spud_cli)
        # sqlite3 is refused for everyone by the database rule, which is read before the findings; the finding is there.
        self.assertIn(("path", ("PATH", "sqlite3")), self.finding("PATH=/tmp/x:$PATH sqlite3 x.db"))

    def test_the_finding_names_the_variable_and_the_command(self):
        self.assertEqual(self.finding("PATH=/tmp/x git status"),
                         [("git", ("status", None)), ("path", ("PATH", "git"))])
        self.assertEqual(self.finding("path=(/tmp/x); git status"),
                         [("git", ("status", None)), ("path", ("path", "git"))])
        self.assertEqual(self.finding("PATH=/tmp/x tee /tmp/out"), [("path", ("PATH", "tee"))])
        r = self.refused_for_members("PATH=/tmp/x:$PATH git status")
        self.assertIn("PATH", r.reason)
        self.assertIn("git", r.reason)
        self.assertNotIn("alias", r.reason)  # its own reason, not SPD-044's or SPD-046's

    def test_a_refusal_the_words_as_spelled_earn_keeps_its_own_reason(self):
        r = self.refused_for_members("PATH=/tmp/x:$PATH git push")
        self.assertIn("git push", r.reason)
        r = self.assertRefused("PATH=/tmp/x:$PATH %s --as spud board" % self.spud_cli, "Law 6")
        self.assertIn("--as spud", r.reason)

    def test_a_later_command_that_earns_its_own_refusal_answers_before_the_path_reason(self):
        # SPD-049's last objective: wave 2 added `path` and `hashed` but could not order them, FINDING_LAST living
        # outside its globs, so on a line of several commands the PATH reason answered before a refusal the words as
        # spelled already earn.  Both still refuse; only the wording changes.
        for command in ("PATH=/tmp/x git status; git push", "path=(/tmp/x); git log; git commit -m x",
                        "hash git=/tmp/x/git; git status; git push", "hash -p /tmp/x/git git; git status && git rebase"):
            with self.subTest(command):
                r = self.refused_for_members(command)
                self.assertNotIn("PATH as the session has it", r.reason)
                self.assertNotIn("command table", r.reason)
        r = self.refused_for_members("PATH=/tmp/x git status; git push")
        self.assertIn("git push", r.reason)
        r = self.assertRefused("PATH=/tmp/x git status; %s --as spud board" % self.spud_cli, "Law 6")
        self.assertIn("--as spud", r.reason)

    def test_a_command_run_by_a_path_stays_silent(self):
        # PATH is not searched for a word holding a slash, so the program the hook read is the one that runs.
        for ok in ("PATH=/tmp/x:$PATH /usr/bin/git status", "PATH=/tmp/x:$PATH ./git status",
                   "PATH=/tmp/x:$PATH /bin/sh -c 'echo hi'", "PATH=/tmp/x:$PATH /usr/bin/tee /tmp/out"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_a_name_the_hook_grants_nothing_for_stays_silent(self):
        for ok in ("PATH=/tmp/x ls", "PATH=/tmp/x:$PATH ls -la", "PATH=/tmp/x echo hi", "PATH=/tmp/x:$PATH cd /tmp",
                   "export PATH=/tmp/x:$PATH", "PATH=/tmp/x:$PATH", "path=(/tmp/x $path)"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_reading_path_without_assigning_it_stays_silent(self):
        for ok in ("echo $PATH", "git status # $PATH", "echo \"$PATH\" > /tmp/out", "git -c color.ui=never status",
                   "CDPATH=$PATH git status", "MYPATH=/tmp/x:$PATH git status", "PATHS=/tmp/x git status",
                   "XPATH=/tmp/x git status"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_git_exec_path_keeps_its_own_reason(self):
        r = self.refused_for_members("GIT_EXEC_PATH=/tmp/x git status")
        self.assertIn("GIT_EXEC_PATH", r.reason)
        self.assertEqual(self.finding("GIT_EXEC_PATH=/tmp/x git status"), [("git-program", "GIT_EXEC_PATH")])

    def test_hash_shadowing_a_name_the_hook_reads_is_refused(self):
        # bash: `hash -p <path> <name>`; zsh: `hash <name>=<path>` (both probed).
        for cmd in ("hash -p /tmp/x/git git; git status", "hash git=/tmp/x/git; git status",
                    "hash -p /tmp/x/sh sh; sh -c 'echo hi'", "hash tee=/tmp/x/tee; tee /tmp/out",
                    "hash -p /tmp/x/git git; git log"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("hash", r.reason)
        self.assertEqual(self.finding("hash git=/tmp/x/git; git status"),
                         [("git", ("status", None)), ("hashed", "git")])

    def test_hash_that_names_nothing_the_hook_reads_stays_silent(self):
        for ok in ("hash; git status", "hash -r; git status", "hash -p /tmp/x/ls ls; git status",
                   "hash ls=/tmp/x/ls; git status", "env hash git=/tmp/x/git; git status"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_the_zsh_commands_parameter_hashes_a_name(self):
        # SPD-105: an element of zsh's `commands` fills the table `hash` does, by every spelling zsh takes
        for cmd in ("commands[git]=/tmp/x/git; git status", "commands+=(git /tmp/x/git); git status",
                    "commands=(git /tmp/x/git); git status", "typeset 'commands[git]=/tmp/x/git'; git status",
                    "commands[git]=/tmp/x/git git status", "commands[tee]=/tmp/x/tee; tee /tmp/out",
                    "commands[git]=/tmp/x/git; command git status", "commands[git]=/tmp/x/git; nice git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("command table", r.reason)
        self.assertEqual(self.finding("commands[git]=/tmp/x/git; git status"),
                         [("git", ("status", None)), ("hashed", "git")])
        # a key the hook cannot read may be any name it reads
        for cmd in ("commands[$k]=/tmp/x/git; git status", "commands+=($pairs); git status",
                    "commands[(e)git]=/tmp/x/git; git status", "commands=(git); git status"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        m = load_spud_module()
        self.assertEqual(m.analyse_command("commands[$k]=/tmp/x/git").hashed, {m.UNKNOWN_NAME})
        # a key the hook reads and grants nothing for, or a call it resolves past the table, stays silent
        for ok in ("commands[ls]=/tmp/x/ls; git status", "commands[git]=/tmp/x/git; /usr/bin/git status",
                   "commands[deploy]=/tmp/x/d; deploy", "dis_commands[git]=/tmp/x/git; git status", "echo $commands[git]"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_path_inside_shell_strings_eval_and_subshells(self):
        for cmd in ("sh -c 'PATH=/tmp/x:$PATH git status'", "eval 'PATH=/tmp/x git status'",
                    "(PATH=/tmp/x:$PATH git status)", "true && PATH=/tmp/x:$PATH git status",
                    "echo $(PATH=/tmp/x git status)"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)


class SubscriptAssignmentTest(BashHookCase):
    """SPD-085 (Kennebec's SPD-062 proposal): a subscripted assignment, `name[subscript]=value`, reached the analysis as a
    command word.  `path[1]=/tmp/x; git status` gave kinds other and git and no PATH finding; `x[1]=y git push` gave none
    at all, the push behind the word never read; `path[1,0]=(/tmp/x); git status` was refused only by accident, a glob
    reading of its brackets ending in `sqlite`.  Probed in zsh 5.9 -f, zsh -f -o nobareglobqual (this Mac's Bash tool),
    bash 3.2 and sh, with `foo` first on a scratch PATH (REAL) and a scratch copy of it in another directory (FAKE):

    - zsh ran FAKE for `path[1]=<dir>; foo`, `path[1,0]=(<dir>); foo`, `path[1]=(<dir>)`, `path[1]+=/../fakebin` and
      `PATH[1]=<dir>:/` and `PATH[1,0]=<dir>:` (a character slice of the scalar), each as a prefix too
      (`path[1]=<dir> foo`, also before `command -v` and `exec`), and for `typeset`, `declare`, `local` (at top level),
      `typeset -g` and `export 'path[1]=<dir>'`, the unquoted `typeset path[1]=<dir>` and `typeset path[1]=(<dir>)`,
      `export`/`typeset -x 'PATH[1]=<dir>:/'`, `path["1"]=`, `path[$((1))]=`, `i=1; path[$i]=` and `path[(r)*real*]=`.
      `PATH[0]=` is "assignment to invalid subscript range" and aborts the line; `typeset`, `local` or `readonly` of an
      element inside a function is "can't create local array elements";
    - bash and sh have no `path` array (REAL), and turn PATH into an array for `PATH[0]=<dir>`, `PATH[1]=<dir>:/`,
      `declare 'PATH[0]=<dir>'` and a function's `local 'PATH[1]=...'`: `$PATH` then reads the element and the lookup
      finds nothing ("foo: No such file or directory") -- a PATH change all the same.  `PATH[0]+=x` appended to the scalar
      (bash reads a scalar's [0] as the scalar itself, confirmed).  A subscripted prefix is no assignment to bash, which ran
      REAL and handed the literal `PATH[0]=<dir>` to the program's environment; `export`/`readonly 'PATH[0]=...'` is "not a
      valid identifier"; `path[1,0]=(...)` as a statement is "cannot assign list to array member";
    - none of these is an assignment anywhere: `'path[1]'=x` (a command of that name), `path[ 1 ]=x` ("bad pattern" in
      zsh), `path[1]x=y` and `path[1][1]=x` ("no matches found"); `a[b[1]]=x` is one subscript in both shells, and
      `echo path[1]` an argument ("no matches found" in zsh).

    shell/assignment_words now reads the word as the assignment it is before any glob reading of its brackets -- the
    subscript runs to the `]` that balances its `[`, and `=` or `+=` follows at once -- as the prefix loop, a declaration's
    operand (whose quoted subscript the builtin parses) and walk.py's `name[subscript]=( ... )` join all do.  The variable
    is assigned with a value the hook does not compute, as an append's, so every rule that reads it counts it as a plain
    assignment would: PATH and `path` for shadowed_name, CDPATH, GIT_*; a subscript the hook cannot evaluate changes nothing
    about that, and an element of a variable the hook tracks nowhere changes nothing at all.  Law 7 does not bind Spud.
    AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)
        return r

    def silent_for_everyone(self, command):
        for agent_id in (AGENT_C, AGENT_A, None):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertSilent(command, agent_id)

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def test_the_tickets_evidence_commands(self):
        for cmd in ("path[1]=/tmp/x; git status", "path[1,0]=(/tmp/x); git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("path", r.reason)
                self.assertIn("PATH as the session has it", r.reason)
                self.assertEqual(self.analysis(cmd).findings, [("git", ("status", None)), ("path", ("path", "git"))])
                self.assertEqual(self.analysis(cmd).kinds, ["git"])  # no command word, and no glob reading of the brackets
        # the word is an assignment, so the push behind it is read (it was not: no finding at all)
        self.assertEqual(self.analysis("x[1]=y git push").findings, [("git", ("push", "push"))])
        self.assertIn("git push", self.refused_for_members("x[1]=y git push").reason)

    def test_every_spelling_before_a_bare_name(self):
        for cmd in ("path[1]=/tmp/x; git status", "path[1,0]=(/tmp/x); git status", "path[1]=(/tmp/x); git status",
                    "path[1]+=/../x; git status", "path[-1,-1]=(/tmp/x /bin); git log", "PATH[1]=/tmp/x:/; git status",
                    "PATH[1,0]=/tmp/x:; git status", "PATH[0]=/tmp/x; git status", "PATH[0]+=:/tmp/x; git status",
                    "path[1]=/tmp/x\ngit status", "path[1]=/tmp/x; tee /tmp/out", "path[1]=/tmp/x; sh -c 'echo hi'",
                    "path[1]=/tmp/x; python3.14 x.py", "path[1]=/tmp/x; env ls", "path[1]=/tmp/x; %s board" % self.spud_cli):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_every_spelling_as_a_prefix(self):
        for cmd in ("path[1]=/tmp/x git status", "path[1,0]=(/tmp/x) git status", "path[1]=(/tmp/x) git status",
                    "PATH[1]=/tmp/x:/ git status", "PATH[0]=/tmp/x git status", "path[1]=/tmp/x nice git status",
                    "X=1 path[1]=/tmp/x git status", "path[1]=/tmp/x exec git status", "path[1]=/tmp/x command git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("PATH", r.reason)

    def test_the_declaration_forms(self):
        for cmd in ("typeset 'path[1]=/tmp/x'; git status", "typeset path[1]=/tmp/x; git status",
                    "typeset path[1]=(/tmp/x); git status", "typeset -g 'path[1]=/tmp/x'; git status",
                    "declare 'path[1]=/tmp/x'; git status", "declare 'PATH[0]=/tmp/x'; git status",
                    "declare PATH[0]=/tmp/x; git status", "local 'path[1]=/tmp/x'; git status",
                    "export 'path[1]=/tmp/x'; git status", "export 'PATH[1]=/tmp/x:/'; git status",
                    "export PATH[1]=/tmp/x:/; git status", "typeset -x 'PATH[1]=/tmp/x:/'; git status",
                    "readonly 'PATH[0]=/tmp/x'; git status", "f() { local 'PATH[1]=/tmp/x:/'; git status; }; f",
                    "builtin typeset 'path[1]=/tmp/x'; git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("PATH", r.reason)

    def test_a_subscript_the_hook_cannot_evaluate_still_counts(self):
        for cmd in ("path[$i]=/tmp/x; git status", "i=1; path[$i]=/tmp/x; git status", "path[$(echo 1)]=/tmp/x; git status",
                    "path[$((1))]=/tmp/x; git status", "path[(r)*real*]=/tmp/x; git status", "path[(i)x]=/tmp/x git status",
                    'path["1"]=/tmp/x; git status', "path[b[1]]=/tmp/x; git status", "path[${#path}]+=x; git status"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_every_tracked_variable_counts_as_a_plain_assignment_would(self):
        cases = (("GIT_DIR[1]=/tmp/x git status", "GIT_DIR+=/tmp/x git status", "cannot resolve"),
                 ("GIT_CONFIG_GLOBAL[1]=/tmp/x git status", "GIT_CONFIG_GLOBAL+=/tmp/x git status", "config"),
                 ("HOME[1]=/tmp/x git status", "HOME+=/tmp/x git status", "config"),
                 ("GIT_PAGER[1]=less git log", "GIT_PAGER+=less git log", "program"),
                 ("GIT_TRACE[1]=/tmp/t git status", "GIT_TRACE+=/tmp/t git status", "holds a variable"))
        for subscripted, appended, needle in cases:
            with self.subTest(subscripted):
                r = self.refused_for_members(subscripted, needle)
                self.assertEqual(r.reason, self.bash(appended).reason)
        # CDPATH: the hook cannot read it, so a relative target after the cd cannot be placed -- for everyone, as an append
        for agent_id in (AGENT_A, None):
            r = self.assertRefused("CDPATH[1]=/tmp; cd ledger; echo x > note.txt", "cannot follow", agent_id)
            self.assertEqual(r.reason, self.bash("CDPATH+=/tmp; cd ledger; echo x > note.txt", agent_id).reason)
        self.assertEqual(self.analysis("cdpath[1]=/tmp").vars, {"cdpath": "$"})

    def test_a_name_run_by_path_or_granted_nothing_stays_silent(self):
        for ok in ("path[1]=/tmp/x; /usr/bin/git status", "PATH[1]=/tmp/x ./git status", "path[1]=/tmp/x ls",
                   "PATH[0]=/tmp/x; ls -la", "path[1]=/tmp/x", "path[1,0]=(/tmp/x)", "typeset 'path[1]=/tmp/x'"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)

    def test_an_element_of_an_untracked_variable_changes_nothing(self):
        for ok in ("x[1]=y; git status", "arr[2]=v git status", "x[1]=y", "mine[k]=(a b); git log", "x[1]+=y git diff",
                   "declare 'x[0]=y'; git status", "a[b[1]]=x; git status"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)
        self.assertEqual([f for f in self.analysis("x[1]=y; arr[2]=(a b); git status").findings if f[0] != "git"], [])

    def test_words_that_are_no_assignment_stay_as_they_were(self):
        # a quoted bracket, a blank in the subscript, and text after the subscript are no assignment in any shell
        for ok in ("echo path[1]", "echo path[1]=/tmp/x", "'path[1]'=/tmp/x; git status", "grep -n 'path[1]=' README",
                   "echo 'PATH[0]=/tmp/x'", "path[1]x=/tmp/x", "printf '%s\\n' path[1]=x"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)
        self.assertEqual(self.analysis("'path[1]'=/tmp/x; git status").vars, {})
        self.assertEqual(self.analysis("path[1][1]=/tmp/x").vars, {})

    def test_the_plain_array_forms_read_as_before(self):
        m = load_spud_module()
        self.assertEqual(self.analysis("path=(/tmp/x $path); git status").findings,
                         [("git", ("status", None)), ("path", ("path", "git"))])
        self.assertEqual(self.analysis("path+=(/tmp/x); git status").findings,
                         [("git", ("status", None)), ("path", ("path", "git"))])
        self.assertEqual(self.analysis("x=(a b)").vars, {"x": m._ARRAY_VALUE + "a b"})
        self.assertEqual(self.analysis("x=()").vars, {"x": m._ARRAY_VALUE})
        # a quoted blank inside one element stays inside it, and the readings of `$x` are what they were
        a = self.analysis("x=('a b' c)")
        self.assertEqual(m.deglob(a.vars["x"]), "a b c")
        self.assertEqual(m.variable_readings(a, "x"), ([["a", "b", "c"], ["a"]], True))
        self.assertEqual(self.analysis("x=('a b' c); $x").findings, [("var-doubt", "$x")])
        self.silent_for_everyone("path=(/tmp/x $path)")


class FunctionShadowTest(BashHookCase):
    """SPD-084: the hook reads a line's command words by name -- git, spud, python3.14, sqlite3, tee, a shell, a wrapper --
    and a shell function of that name defined earlier on the line runs in its place, so `git() { git push; }; git status`
    reached the hook as an ordinary `git status` with no finding (Law 7).  SPD-062 closed a PATH the line assigns and a
    `hash`; SPD-059 closed an alias run through eval; this closes the function.  Probed in zsh 5.9 -f, zsh -f -o
    nobareglobqual (this Mac's Bash tool), bash 3.2 and sh, with `foo` a name no command has (SH means the function ran):

    - `foo() { echo SH; }; foo`, `foo () { echo SH; }`, `function foo { echo SH; }` and `function foo () { echo SH; }` each
      ran the function in all four; zsh also takes the body-without-braces short forms `foo () echo SH` and `foo() echo SH`
      (bash and sh: a syntax error), and its several-names form `foo bar () { echo SH; }` defined both foo and bar;
    - a call before the definition ran the real lookup (`foo; foo() {...}` -> not found), and so did a call after a
      definition in a `( ... )` subshell (`(foo() {...}); foo` -> not found): the subshell's function does not reach it.  A
      call inside the subshell (`(foo() {...}; foo)`), and one after a definition in a branch (`if`, `&&`, a case arm), a
      loop body, another function's body, a background list, a pipeline or a command substitution, ran the function;
    - a function is looked up in command position only: `command foo`, `builtin foo`, `nice foo` and `env foo` each ran the
      real lookup (the function bypassed), while `time foo`, `! foo` and zsh's `nocorrect foo`, `noglob foo`, `- foo` and
      `exec foo` kept it -- `exec foo` ran the function in zsh (`SH`, no line after: exec then exits) but skipped it in bash
      and sh, so the hook refuses it, the Bash tool being zsh.  A function named for a wrapper shadows the wrapper itself
      (`env() { echo SH; }; env true` -> SH), but a wrapper the first one runs does not (`env() {...}; nice env true` -> not
      SH); a call by a path (`/usr/bin/foo`) is not looked up.  zsh applies a function lookup after noglob/-/exec where it
      does not expand an alias (SPD-059, probed: `eval 'noglob gp'`, `eval '- gp'`, `eval 'exec gp'` all ran nothing), so
      the function gate is its own (syntax.FUNCTION_KEEP_WRAPPERS), not the alias command-position gate.

    The finding is appended after its own command's and carries an entry in FINDING_LAST beside `path` and `hashed`, so on a
    line of several commands a refusal the words as spelled already earn answers first.  A definition in a `( ... )` subshell
    is dropped when it closes (ShellWalk restores the set); one in a branch, a loop, a function body, a background list, a
    pipeline or a command substitution is kept (refuse on doubt, never allow on doubt), as is one an `unset -f` or
    `unfunction` may have removed (the hook keeps refusing rather than allow on doubt).  Law 7 does not bind Spud.  AGENT_A
    plans tests/** and bin/spud; AGENT_C plans **.

    SPD-105 (Atlantic's SPD-084 proposal): zsh also binds a function through its special `functions` association, which the
    hook read as a command word or an unrelated variable.  Probed in zsh 5.9 -f and -o nobareglobqual (bash and sh have no
    such parameter and ran the real lookup every time): `functions[foo]='echo SH'; foo`, `functions+=(foo 'echo SH'); foo`,
    `functions=(foo 'echo SH'); foo` (a whole assignment adds its pairs: a function defined before it still ran),
    `typeset`, `declare` and `export 'functions[foo]=echo SH'`, a prefix (`functions[foo]='echo SH' true; foo`),
    `functions[foo]+=' x'`, a function body's `functions[foo]=` once called, `k=foo; functions[$k]=`,
    `functions[(e)foo]=` (a subscript flag), `x='echo SH'; functions+=(foo $x)` and `a=(foo 'echo SH'); functions+=($a)`
    each ran the function, and `functions[foo]=...; (foo)` did too.  `(functions[foo]=...); foo` ran the real lookup (the
    subshell's), as did `functions[f?o]=` (the key is `f?o`, a subscript is not globbed), `functions["foo"]=` (the quotes
    are part of the key), `functions+=(foo 'echo SH' bar)` ("bad set of key/value pairs"), `unset 'functions[foo]'` and
    `dis_functions[foo]=` (a disabled function).  The name each spelling keys is now recorded in the same set a definition
    fills, with its subshell scope; a key the hook cannot read (an expansion, a subscript flag, an element that may become
    several words, pairs odd in number, a scalar) records UNKNOWN_NAME, which refuses a member's later call of every name
    the hook reads.  `functions["git"]=` is refused though zsh keys it `"git"`: the hook reads the word with its quotes
    taken, and refuses on that doubt."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)
        return r

    def silent_for_everyone(self, command):
        for agent_id in (AGENT_C, AGENT_A, None):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertSilent(command, agent_id)

    def finding(self, command):
        me = load_spud_module()
        return me.analyse_command(command, me.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path))).findings

    def test_the_tickets_evidence_command(self):
        # a function whose body is harmless still shadows the git the hook read: the reason is the function, not the body
        r = self.refused_for_members("git() { echo pushed; }; git status")
        self.assertIn("shell function", r.reason)
        self.assertIn("git", r.reason)
        self.assertEqual(self.finding("git() { echo pushed; }; git status"),
                         [("git", ("status", None)), ("function", "git")])

    def test_every_spelling_of_the_definition(self):
        for cmd in ("git() { true; }; git status", "git () { true; }; git status",
                    "function git { true; }; git status", "function git () { true; }; git status",
                    "function git() { true; }; git status", "git () true; git status",
                    "git() true; git status", "git() { true; }\ngit status",
                    "builtin git() { true; }; git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("shell function", r.reason)
                self.assertEqual(self.finding(cmd)[-1], ("function", "git"))

    def test_the_several_names_zsh_form_binds_them_all(self):
        # zsh's `a b () { ... }` defines every name; a later call of any of them is shadowed (probed both ran the function)
        self.assertEqual(self.finding("git tee () { true; }; git status"),
                         [("git", ("status", None)), ("function", "git")])
        self.assertEqual(self.finding("git tee () { true; }; tee /tmp/out"), [("function", "tee")])
        self.refused_for_members("git tee () { true; }; git status")
        self.refused_for_members("git tee () { true; }; tee /tmp/out")

    def test_every_dispatched_name(self):
        self.refused_for_members("git() { true; }; git status")
        self.refused_for_members("git() { true; }; git push")
        self.refused_for_members("spud() { true; }; spud board")
        self.refused_for_members("python3.14() { true; }; python3.14 x.py")
        self.refused_for_members("tee() { true; }; tee /tmp/out")
        self.refused_for_members("sh() { true; }; sh -c 'echo hi'")
        self.refused_for_members("bash() { true; }; bash -c 'echo hi'")
        self.refused_for_members("node() { true; }; node x.js")
        # a wrapper name is itself a function in command position: env runs the function, not /usr/bin/env
        r = self.refused_for_members("env() { true; }; env git status")
        self.assertIn("env", r.reason)
        self.assertEqual(self.finding("env() { true; }; env git status")[-1], ("function", "env"))
        # sqlite3 is refused for everyone by the database rule (read before the findings); the function finding is there
        self.assertIn(("function", "sqlite3"), self.finding("sqlite3() { true; }; sqlite3 x.db"))

    def test_a_name_the_hook_does_not_dispatch_on_changes_nothing(self):
        for ok in ("ls() { true; }; ls", "make() { true; }; make all", "cat() { true; }; cat f",
                   "grep() { true; }; grep x f"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)

    def test_a_call_before_the_definition_is_not_shadowed(self):
        for ok in ("git status; git() { true; }", "git log; function git { true; }"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_a_call_by_path_or_a_bypassing_prefix_stays_silent(self):
        # the function is looked up in command position; a path is not, and command/builtin/env/nice resolve their own word
        for ok in ("git() { true; }; /usr/bin/git status", "git() { true; }; ./git status",
                   "git() { true; }; command git status", "git() { true; }; builtin git status",
                   "git() { true; }; nice git status", "git() { true; }; env git status",
                   "git() { true; }; nice env git status", "git() { true; }; sudo git status"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_a_bypassing_prefix_keeps_the_words_own_refusal(self):
        # command/nice bypass the function, but a write verb behind them still earns Law 7 -- its own reason, not the function's
        for cmd in ("git() { true; }; command git push", "git() { true; }; nice git commit -m x"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertNotIn("shell function", r.reason)

    def test_modifiers_that_keep_the_function_are_refused(self):
        # keywords (time, !) run the following function in all four shells; zsh keeps the lookup after noglob, - and exec
        # too (probed: exec ran the function in zsh, skipped it in bash/sh; the hook refuses for the shell the tool runs)
        for cmd in ("git() { true; }; time git status", "git() { true; }; ! git status",
                    "git() { true; }; noglob git status", "git() { true; }; - git status",
                    "git() { true; }; nocorrect git status", "git() { true; }; exec git status",
                    "git() { true; }; noglob exec git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("shell function", r.reason)
        # a write verb still answers first, whichever modifier keeps the function
        r = self.refused_for_members("git() { true; }; exec git push")
        self.assertIn("git push", r.reason)
        self.assertNotIn("shell function", r.reason)

    def test_the_subshell_scope(self):
        # a definition in a ( ... ) subshell does not reach a call after it (probed: `(git(){ :; }); git status` ran real git)
        for ok in ("(git() { true; }); git status", "(function git { true; }); git status",
                   "(git() { true; }) ; git log", "( ( git() { true; } ) ); git status"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        self.assertEqual(self.finding("(git() { true; }); git status"), [("git", ("status", None))])
        # a call inside the subshell is shadowed
        for cmd in ("(git() { true; }; git status)", "(git() { true; }; git push)"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_the_doubt_cases_are_refused(self):
        # a definition that may or may not have run, or that runs in a forked list, is kept: refuse on doubt (never allow)
        for cmd in ("if true; then git() { true; }; fi; git status",
                    "true && git() { true; }; git status",
                    "false || git() { true; }; git status",
                    "case x in x) git() { true; };; esac; git status",
                    "for f in a; do git() { true; }; done; git status",
                    "while false; do git() { true; }; done; git status",
                    "f() { git() { true; }; }; f; git status",
                    "git() { true; } & wait; git status",
                    "git() { true; } | cat; git status",
                    "X=$(git() { true; }; echo d); git status",
                    "git() { true; }; unset -f git; git status",
                    "git() { true; }; unfunction git; git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("shell function", r.reason)

    def test_a_later_command_that_earns_its_own_refusal_answers_first(self):
        # FINDING_LAST orders the function reason after a refusal the words as spelled already earn (like path and hashed)
        for command in ("git() { true; }; git status; git push",
                        "git() { true; }; git log && git commit -m x"):
            with self.subTest(command):
                r = self.refused_for_members(command)
                self.assertNotIn("shell function", r.reason)
        r = self.refused_for_members("git() { true; }; git status; git push")
        self.assertIn("git push", r.reason)

    def test_a_function_body_is_still_analysed_as_today(self):
        # no regression: a body's own commands are read exactly as before (the function name is f, not git)
        self.assertEqual(self.finding("f() { git push; }"), [("git", ("push", "push"))])
        self.refused_for_members("f() { git push; }")
        self.assertEqual(self.finding("deploy() { git status; }; deploy"), [("git", ("status", None))])
        self.silent_for_everyone("deploy() { git status; }; deploy")

    def test_the_finding_names_the_command(self):
        self.assertEqual(self.finding("git() { true; }; git status"),
                         [("git", ("status", None)), ("function", "git")])
        self.assertEqual(self.finding("tee() { true; }; tee /tmp/out"), [("function", "tee")])
        r = self.refused_for_members("git() { true; }; git status")
        self.assertIn("git", r.reason)
        self.assertNotIn("hash", r.reason)  # its own reason, not SPD-062's
        self.assertNotIn("PATH", r.reason)

    def test_the_zsh_functions_parameter_binds_a_function(self):
        # SPD-105's evidence command, then every spelling zsh takes
        self.assertEqual(self.finding("functions[git]='true'; git status"),
                         [("git", ("status", None)), ("function", "git")])
        for cmd in ("functions[git]='true'; git status", "functions+=(git 'true'); git status",
                    "functions=(git 'echo SH'); git status", "functions+=(deploy 'a b' git 'c d'); git status",
                    "typeset 'functions[git]=true'; git status", "declare 'functions[git]=true'; git status",
                    "export 'functions[git]=true'; git status", "functions[git]=true true; git status",
                    "functions[git]+=' x'; git status", "functions[git]=true; (git status)",
                    "f() { functions[git]=true; }; f; git status", 'functions["git"]=true; git status',
                    "functions[tee]=true; tee /tmp/out", "functions[env]=true; env git status",
                    "functions[git]=true; time git status", "functions[git]=true; noglob git status"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("shell function", r.reason)
        self.assertEqual(self.finding("functions+=(git 'a b' tee 'c'); tee /tmp/out"), [("function", "tee")])
        # a write verb still answers with its own reason
        self.assertIn("git push", self.refused_for_members("functions[git]=true; git push").reason)

    def test_a_functions_key_the_hook_cannot_read_refuses_every_name(self):
        me = load_spud_module()
        for cmd in ("functions[$k]=true; git status", "k=git; functions[$k]=true; git status",
                    "functions[(e)git]=true; git status", "functions[$(echo git)]=true; git status",
                    "functions+=($pairs); git status", "x=true; functions+=(git $x); git status",
                    "functions+=(g* true); git status", "functions+=(git true tee); git status",
                    "functions=x; git status", "functions[$k]=true; tee /tmp/out", "functions[$k]=true; sh -c 'echo hi'"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        self.assertEqual(me.analyse_command("functions[$k]=true").functions, {me.UNKNOWN_NAME})
        # the unread name may be the bypassing wrapper's own, which a function of that name shadows in turn
        self.assertEqual(self.finding("functions[$k]=true; command git status")[-1], ("function", "command"))
        # ... and a name the hook grants nothing for is still silent, as is a call by path
        for ok in ("functions[$k]=true; ls", "functions[$k]=true; /usr/bin/git status", "functions[$k]=true; make all"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)

    def test_the_functions_parameter_keeps_the_scope_and_the_names_it_does_not_bind(self):
        # the subshell's own element does not reach a call after it; a branch's, a background list's and a substitution's may
        for ok in ("(functions[git]=true); git status", "(functions+=(git true)); git log"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)
        for cmd in ("if true; then functions[git]=true; fi; git status", "functions[git]=true & wait; git status",
                    "x=$(functions[git]=true); git status", "true && functions[git]=true; git status"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        # a key the hook reads that names nothing it dispatches, a key zsh does not glob, and a disabled function
        for ok in ("functions[deploy]=true; git status", "functions+=(deploy true); git status",
                   "functions[g?t]=true; git status", "dis_functions[git]=true; git status", "functions=(); git status",
                   "echo $functions[git]", "functions[deploy]=true; deploy", "unset 'functions[deploy]'; git status"):
            with self.subTest(ok):
                self.silent_for_everyone(ok)
        me = load_spud_module()
        self.assertEqual(me.analyse_command("functions+=(deploy 'a b' git 'c d')").functions, {"deploy", "git"})

    def test_reading_a_function_name_without_defining_it_stays_silent(self):
        # naming a function in a string is not defining one; a lone definition with no later call refuses nothing new
        for ok in ("echo 'git() { true; }'", "echo git is a function", "git status",
                   "git() { echo hi; }", "git log; deploy() { echo hi; }"):
            with self.subTest(ok):
                self.assertNotEqual(self.bash(ok).decision, "deny", ok)
                self.assertNotEqual(self.bash(ok, agent_id=None).decision, "deny", ok)


class EnvironmentFunctionTest(BashHookCase):
    """SPD-106 (Atlantic's SPD-084 proposal): bash and sh import a shell function from their environment, so a member can
    hand a child a function without defining it on the line, past SPD-084.  `env 'BASH_FUNC_git%%=() { true; }' bash -c
    'git status'` gave no finding at all: strip_wrapper took env's operands by ASSIGNMENT_RE, stopped at the `%%` word and
    dispatched on it, so the `-c` string was never read.  Probed on this Mac in zsh 5.9 -f, zsh -f -o nobareglobqual, bash
    3.2 and sh (every outer shell alike; SH means the function ran):

    - `/usr/bin/env 'BASH_FUNC_foo%%=() { echo SH; }' /bin/bash -c foo` -> SH, and to /bin/sh -> SH; to /bin/zsh, /bin/ksh
      and /bin/dash -> the real foo (none of them imports).  SH through `env`, `ENV`, `env -i`, `env -`, `env --`, `env -u
      HOME`, `env X=1 <function> Y=2` and `env -S "'BASH_FUNC_foo%%=...' /bin/bash -c foo"`.  The `BASH_FUNC_foo()=`,
      `BASH_FUNC_foo=` and `foo=() {` spellings imported nothing: macOS's bash 3.2 reads `%%` alone;
    - an imported function goes on down: `env <function> /bin/sh -c '/bin/sh -c foo'` and `... /bin/sh -c 'exec
      /usr/bin/env /bin/bash -c foo'` -> SH, so a program the hook cannot follow (`env <function> python3.14 x.py`, whose
      x.py runs sh) hands it on too;
    - env puts every operand holding `=` past its first character in the environment (`a b=c`, `x[1]=y` and `a%b=c` each
      reached /usr/bin/env's own listing; `=x` is "setenv =x: Invalid argument"), so none of them is its command;
    - no shell takes `BASH_FUNC_foo%%=...` as an assignment: as a word before a command it is a command not found, and
      `export 'BASH_FUNC_foo%%=...'` is "not valid in this context" (zsh) or "not a valid identifier" (bash, sh), while
      `BASH_FUNC_foo=1 /usr/bin/env` exported the valid spelling;
    - `export -f`: on a zsh line `foo() { echo SH; }; export -f foo; /bin/bash -c foo` printed the function and ran the real
      foo (zsh's `export -f` lists functions), in bash and sh it ran SH, and `typeset -fx` alike; inside `bash -c '...'`,
      `export -f foo` or `declare -fx foo` then a child bash or sh ran SH, a child zsh the real foo.

    A member has no reason to put a function in a program's environment, and the hook cannot follow what the program starts,
    so any env operand, prefix, declaration operand or sudo operand naming a BASH_FUNC_ variable refuses a member outright,
    whatever program follows, with its own reason (FINDING_LAST beside `function`); so does `export -f` or `declare -fx` of
    a name the hook reads, which writes exactly that variable in bash.  `git() { :; }; export -f git; bash -c 'git status'`
    was already refused through SPD-084's set reaching the inner shell, and still is.  Law 7 does not bind Spud.  AGENT_A
    plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)
        return r

    def silent_for_everyone(self, command):
        for agent_id in (AGENT_C, AGENT_A, None):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertSilent(command, agent_id)

    def finding(self, command):
        me = load_spud_module()
        return me.analyse_command(command, me.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path))).findings

    def test_the_tickets_evidence_command(self):
        cmd = "env 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'"
        r = self.refused_for_members(cmd)
        self.assertIn("BASH_FUNC_git%%", r.reason)
        self.assertIn("environment", r.reason)
        self.assertNotIn("shell function `git`", r.reason)  # its own reason, not SPD-084's
        # the -c string is read now: env's operand is no longer taken for its command
        self.assertEqual(self.finding(cmd), [("env-function", "BASH_FUNC_git%%"), ("git", ("status", None))])

    def test_every_env_spelling_whatever_program_follows(self):
        for cmd in ("env 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "/usr/bin/env 'BASH_FUNC_git%%=() { true; }' /bin/sh -c 'git status'",
                    "ENV 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "env -i 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "env - 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "env -- 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "env -u HOME 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "env X=1 'BASH_FUNC_git%%=() { true; }' Y=2 bash -c 'git status'",
                    "env -S \"'BASH_FUNC_git%%=() { true; }' bash -c 'git status'\"",
                    "env 'BASH_FUNC_foo%%=() { git push; }' python3.14 x.py",
                    "env 'BASH_FUNC_git%%=() { true; }' ls", "env 'BASH_FUNC_x%%=() { true; }'",
                    "nice env 'BASH_FUNC_git%%=() { true; }' sh -c 'git status'",
                    "env BASH_FUNC_git=1 bash -c 'git status'",
                    "sudo 'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "echo hi; env 'BASH_FUNC_sh%%=() { true; }' make"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("BASH_FUNC_", r.reason)

    def test_a_prefix_a_declaration_or_a_bare_word_naming_it(self):
        for cmd in ("export 'BASH_FUNC_git%%=() { true; }'; bash -c 'git status'", "export BASH_FUNC_git=1",
                    "export BASH_FUNC_git", "typeset -x 'BASH_FUNC_git%%=() { true; }'", "BASH_FUNC_git=1 bash -c ls",
                    "BASH_FUNC_git=1; export BASH_FUNC_git", "'BASH_FUNC_git%%=() { true; }' bash -c 'git status'",
                    "declare -x BASH_FUNC_git=1 && sh -c ls"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "BASH_FUNC_")

    def test_export_f_of_a_name_the_hook_reads(self):
        # SPD-084's set reaching the inner shell already refused these, and still does (pinned by its own finding)
        for cmd in ("git() { :; }; export -f git; bash -c 'git status'",
                    "bash -c 'git() { :; }; export -f git; bash -c \"git status\"'",
                    "bash -c 'git() { :; }; export -f git; sh -c \"git status\"'"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
                self.assertIn(("function", "git"), self.finding(cmd))
        # ... and the export itself now refuses, whatever the child it reaches (a program the hook cannot follow)
        for cmd in ("git() { :; }; export -f git; python3.14 x.py", "bash -c 'git() { :; }; export -f git; make'",
                    "git() { :; }; declare -fx git", "git() { :; }; typeset -f -x git", "export -f sh",
                    "bash -c 'tee() { :; }; declare -f -x tee; ./run'"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd, "BASH_FUNC_")
                self.assertIn("export", r.reason)
        self.assertIn(("env-function", "BASH_FUNC_git%%"), self.finding("git() { :; }; export -f git; python3.14 x.py"))

    def test_nothing_that_puts_no_function_in_an_environment(self):
        for ok in ("echo 'BASH_FUNC_x'", "echo BASH_FUNC_git%%=x", "grep -rn BASH_FUNC_ tests/keep.py",
                   "env FOO=1 git status", "env GIT_OPTIONAL_LOCKS=0 git status", "env -u BASH_FUNC_git%% git status",
                   "export -f deploy", "deploy() { :; }; export -f deploy; bash -c deploy", "declare -f git",
                   "typeset -f", "export -p", "readonly -f git", "printenv BASH_FUNC_git%%"):
            with self.subTest(ok):
                r = self.bash(ok)
                self.assertNotEqual(r.decision, "deny", (ok, r))
                self.assertNotEqual(self.bash(ok, agent_id=None).decision, "deny", ok)

    def test_env_takes_every_operand_holding_an_equals_sign(self):
        # an env operand no shell would take for an assignment hid the command after it; it is env's, and the command is read
        for cmd, needle in (("env 'a b=c' git push", "git push"), ("env x[1]=y git commit -m x", "git commit"),
                            ("env a%b=c git reset --hard", "git reset")):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn(needle, r.reason)
        self.assertEqual(self.finding("env 'a b=c' git status"), [("git", ("status", None))])
        self.silent_for_everyone("env 'a b=c' git status")

    def test_a_refusal_the_words_as_spelled_earn_answers_first(self):
        r = self.refused_for_members("env 'BASH_FUNC_x%%=() { true; }' git push")
        self.assertIn("git push", r.reason)
        r = self.assertRefused("env 'BASH_FUNC_x%%=() { true; }' %s --as spud board" % self.spud_cli, "Law 6")
        self.assertIn("--as spud", r.reason)


class WrapperCommandWordTest(BashHookCase):
    """SPD-055: after strip_wrapper the prefix loop read the wrapper's remaining words as the shell's own, so
    `nice x=./git push` took `x=./git` for an assignment and dispatched on `push` -- kind other, no finding -- while nice
    execs that word and the shell ran ./git push out of a directory named `x=.`.  Probed in zsh 5.9 -f, zsh -f -o
    nobareglobqual, bash 3.2 and sh with a program in such a directory: `command`, `exec`, `nohup`, `nice`, `caffeinate`,
    `script`, `stdbuf`, `xargs` (with input) and zsh's `noglob` each ran it, while `env x=./prog status` set x and ran
    `status` instead, wherever env stood; sudo(8) documents `sudo [VAR=value] [-i | -s] [command [arg ...]]` and takes it as
    environment too (syntax.WRAPPER_TAKES_ASSIGNMENTS).  The shell's own `time` and zsh's `nocorrect` keep the command
    position, so the shell reads the assignment after them -- but only where they stand in it, which the method below pins.
    For every other wrapper the first remaining word is its command, assignment-shaped or not, and os.path.basename is what
    the hook dispatches on (`x=./git` -> git).

    A word an exec'ing wrapper is handed is no longer an assignment either, so `nice GIT_PAGER=less git log` now reads as
    the command `GIT_PAGER=less` and stays silent: the shell looks for a program of that name and runs neither git nor a
    pager (probed).  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)
        return r

    def finding(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path))).findings

    def test_a_wrapper_execs_an_assignment_shaped_word(self):
        for wrapper in ("nice", "command", "exec", "nohup", "timeout 5", "xargs", "builtin", "caffeinate", "doas",
                        "stdbuf -o0", "chronic", "ionice", "setsid", "unbuffer", "script /dev/null", "noglob",
                        "nice -n 5", "nohup nice", "command -p"):
            cmd = "%s x=./git push" % wrapper
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("git push", r.reason)

    def test_the_word_is_dispatched_by_its_base_name(self):
        self.assertEqual(self.finding("nice x=./git push"), [("git", ("push", "push"))])
        self.assertEqual(self.finding("nice x=../bin/git push"), [("git", ("push", "push"))])
        self.assertEqual(self.finding("nice x=/usr/bin/git push"), [("git", ("push", "push"))])

    def test_env_sudo_time_and_nocorrect_still_read_the_word_as_environment(self):
        # env and sudo take NAME=value as the command's environment; `time` is a reserved word and zsh's `nocorrect` keeps
        # the command position, so the shell's own assignment parsing still applies there (probed).
        for ok in ("env x=./git push", "sudo x=./git push", "time x=./git push", "nocorrect x=./git push",
                   "env -- x=./git push", "sudo -u root x=./git push"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        for refused in ("env GIT_PAGER=less git log", "sudo GIT_PAGER=less git log", "time GIT_PAGER=less git log",
                        "nocorrect GIT_PAGER=less git log", "env x=./git git push", "sudo x=1 git push"):
            with self.subTest(refused):
                self.refused_for_members(refused)

    def test_an_assignment_shaped_word_with_a_harmless_base_stays_silent(self):
        for ok in ("nice x=./ls", "nice x=./ls -la", "nohup x=./make all", "command x=./echo hi",
                   "timeout 5 x=/bin/echo hi", "nice x=1 ls", "nice FOO=1 ls"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_a_wrapper_no_longer_reads_its_word_as_an_assignment(self):
        # The shell looks for a program named `GIT_PAGER=less` and runs nothing: neither git nor the pager (probed), so the
        # hook says nothing either.  `env` and `sudo` keep the old reading, which the test above pins.
        for ok in ("nice GIT_PAGER=less git log", "nohup GIT_SSH_COMMAND=cmd git fetch", "nice FOO=1 git push"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_time_and_nocorrect_take_the_assignment_only_in_the_command_position(self):
        """`time` and zsh's `nocorrect` are the shell's own, so they keep the command position and the shell reads the
        assignment -- but only where they stand in it.  Behind another wrapper `time` is /usr/bin/time, an external program
        that execs its word: probed, `nice time x=./prog status`, `env time x=./prog status` and zsh's `- time x=./prog
        status` each ran ./prog while `time x=./prog status` set x and ran `status`.  `nice nocorrect x=./git push` runs
        nothing at all (nice finds no program called nocorrect), and is refused with it: fail closed."""
        for ok in ("time x=./git push", "nocorrect x=./git push", "nice env x=./git push", "env sudo x=./git push"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)
        for refused in ("nice time x=./git push", "env time x=./git push", "sudo time x=./git push",
                        "- time x=./git push", "nice nocorrect x=./git push", "time nice x=./git push"):
            with self.subTest(refused):
                r = self.refused_for_members(refused)
                self.assertIn("git push", r.reason)

    def test_a_spud_call_behind_a_wrapper_is_still_read(self):
        r = self.assertRefused("nice x=%s/bin/spud --as spud board" % self.home.path, "Law 6")
        self.assertIn("--as spud", r.reason)
        self.assertSilent("nice x=%s/bin/spud --as %s board" % (self.home.path, AGENT_A))

    def test_the_word_is_read_inside_shell_strings_eval_and_subshells(self):
        for cmd in ("sh -c 'nice x=./git push'", "eval 'nice x=./git push'", "(nice x=./git push)",
                    "true && nice x=./git push", "nice x=./git commit -m x"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)


class GitVerbProgramOptionTest(BashHookCase):
    """SPD-051: SPD-046's table of verb options that name a program git runs listed only ls-remote/fetch --upload-pack,
    grep -O/--open-files-in-pager, difftool -x/--extcmd and archive --exec.  The same class lives on other verbs Law 7
    allows.  Probed on git 2.54.0 (Apple Git-157), scratchpad only, no real remote: `git send-email --dry-run
    --to-cmd=<prog> <patch>` and the same with `--cc-cmd` each ran <prog>, and so did the abbreviation `--to-cm=<prog>`;
    `git send-email -h` lists `--sendmail-cmd` ("Command to run to send email") and `--smtp-server`, which
    git-send-email(1) takes as a sendmail-like program when it is a path; `git web--browse` accepts `--browser`, `--tool`
    and `--config` and their spaced short forms `-b`, `-t` and `-c`, each naming the browser or the config key whose value
    git runs; `git instaweb` is not installed on this Mac (absent from `git --list-cmds=main`, so SPD-047 already refuses
    it here as an unknown verb), and its `--httpd`/`-d` and `--browser`/`-b` come from git-instaweb(1).  `git help` names
    no program on the line -- `-m`, `-w` and `-i` pick man, a browser or info, whose program comes from config the
    allowlist already refuses -- but git-help(1) documents GIT_MAN_VIEWER, which was missing from GIT_PROGRAM_ENV_VARS.

    git's parse-options takes any unambiguous prefix, so a `--`-prefix of one of these long options is refused whether or
    not git would resolve it exactly: `git send-email --to=x` is refused because `--to` is a prefix of `--to-cmd` (fail
    closed; a member never runs send-email).  Also pinned here: a clustered short option (`git grep -nO`), which SPD-046's
    round 2 closed, and a glob that expands to one of these options, which took two changes -- GLOB_SAMPLES held none of
    these verbs or options, and git_read_index stopped at the verb, so the option was never a read point at all."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)
        return r

    def finding(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path))).findings

    def test_send_email_program_options_are_refused(self):
        for cmd in ("git send-email --sendmail-cmd=cmd p", "git send-email --sendmail-cmd cmd p",
                    "git send-email --smtp-server=/tmp/x/sendmail p", "git send-email --smtp-server /tmp/x p",
                    "git send-email --to-cmd=cmd p", "git send-email --cc-cmd=cmd p",
                    "git send-email --sendmail=cmd p", "git send-email --to-cm=cmd p", "git send-email --cc-c cmd p",
                    "git send-email --smtp-serv=/tmp/x p", "git send-email --to=x p", "git send-email --cc=x p"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        self.assertEqual(self.finding("git send-email --to-cmd=cmd p"),
                         [("git-program", "send-email --to-cmd=cmd")])

    def test_web_browse_program_options_are_refused(self):
        for cmd in ("git web--browse --browser=x u", "git web--browse --browser x u", "git web--browse -b x u",
                    "git web--browse --tool=x u", "git web--browse -t x u", "git web--browse --config=browser.x.cmd u",
                    "git web--browse -c browser.x.cmd u", "git web--browse --brow=x u", "git web--browse -bt u",
                    "git web--browse -ct browser.x.cmd u"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        self.assertEqual(self.finding("git web--browse --tool=x u"), [("git-program", "web--browse --tool=x")])

    def test_instaweb_program_options_are_refused(self):
        for cmd in ("git instaweb --httpd=lighttpd", "git instaweb --httpd lighttpd", "git instaweb -d lighttpd",
                    "git instaweb --browser=x", "git instaweb -b x", "git instaweb --htt=x", "git instaweb -ld x",
                    "git instaweb -pb x"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        self.assertEqual(self.finding("git instaweb --httpd=lighttpd"),
                         [("git-program", "instaweb --httpd=lighttpd")])

    def test_the_verbs_without_a_program_option_stay_silent(self):
        # Silent for Spud, whom Law 7 does not bind.  Since SPD-087 a member runs neither verb at all (send-email hands
        # the patch to a mailer, web--browse opens a browser), so what these pin is the finding: with no program-naming
        # option on the line there is no git-program finding to earn, only Law 7's own verb.
        for ok, verb in (("git send-email --dry-run p", "send-email"), ("git send-email --smtp-server-port=25 p", "send-email"),
                         ("git send-email --smtp-user=me p", "send-email"), ("git send-email --dump-aliases", "send-email"),
                         ("git web--browse u", "web--browse"), ("git web--browse --version", "web--browse")):
            with self.subTest(ok):
                self.assertSilent(ok, agent_id=None)
                self.assertEqual(self.finding(ok), [("git", (verb, verb))])
        # git instaweb is not installed on this Mac, so SPD-047's unknown-verb check refuses every form of it here; what
        # SPD-051 decides is which finding a program-naming option earns, not whether the verb is refused at all.
        for verb_only in ("git instaweb --port=1234", "git instaweb -p 1234", "git instaweb --local", "git instaweb stop"):
            with self.subTest(verb_only):
                self.assertEqual(self.finding(verb_only), [("git-verb", ("verb", "instaweb"))])

    def test_git_man_viewer_is_refused(self):
        for cmd in ("GIT_MAN_VIEWER=cmd git help git", "env GIT_MAN_VIEWER=cmd git status",
                    "export GIT_MAN_VIEWER=cmd; git log"):
            with self.subTest(cmd):
                r = self.refused_for_members(cmd)
                self.assertIn("GIT_MAN_VIEWER", r.reason)

    def test_git_help_names_no_program_on_the_line(self):
        for ok in ("git help", "git help git", "git help -a", "git help -m git", "git help -w git", "git help -i git",
                   "git help -c", "git help --all"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_clustered_short_options_stay_refused(self):
        # SPD-046's round 2 closed the cluster; pinned here with SPD-051's other short-option verbs.
        for cmd in ("git grep -nO foo", "git grep -nOcat foo", "git grep -inO foo", "git difftool -dx cmd A B"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_a_glob_that_expands_to_a_program_option_is_read(self):
        for cmd in ("git ls-remote --upload-pac? cmd .", "git fetch --upload-pac? cmd r",
                    "git send-email --to-cm? cmd p", "git difftool --extcm? cmd A B",
                    "git archive --exe? cmd HEAD"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)

    def test_the_old_table_is_unchanged(self):
        for cmd in ("git ls-remote --upload-pack=cmd host:r", "git fetch --upload-pack cmd r", "git grep -O foo",
                    "git difftool --extcmd=cmd A B", "git archive --exec=cmd HEAD"):
            with self.subTest(cmd):
                self.refused_for_members(cmd)
        for ok in ("git fetch", "git ls-remote host:r", "git grep -n foo", "git archive HEAD"):  # difftool: SPD-087
            with self.subTest(ok):
                self.assertSilent(ok)


# The GIT_TRACE* siblings, probed on git 2.54.0 (Apple Git-157) in the scratchpad, each set to an absolute path with a
# plain `git status`: GIT_TRACE, GIT_TRACE_PERFORMANCE, GIT_TRACE_SETUP, GIT_TRACE_PACK_ACCESS, GIT_TRACE_REFS,
# GIT_TRACE2, GIT_TRACE2_EVENT and GIT_TRACE2_PERF each appended a file there on that one read verb; GIT_TRACE_PACKET,
# GIT_TRACE_SHALLOW, GIT_TRACE_FSMONITOR, GIT_TRACE_CURL and GIT_TRACE_PACKFILE wrote nothing under `status` (they trace
# transports and packfiles, under fetch, ls-remote and their kin) and take the same value, so the whole family counts.
GIT_TRACE_VARS = ("GIT_TRACE", "GIT_TRACE_PACKET", "GIT_TRACE_PERFORMANCE", "GIT_TRACE_SETUP", "GIT_TRACE_SHALLOW",
                  "GIT_TRACE_PACK_ACCESS", "GIT_TRACE_REFS", "GIT_TRACE_FSMONITOR", "GIT_TRACE_CURL",
                  "GIT_TRACE_PACKFILE", "GIT_TRACE2", "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF")


class GitFileWriteTest(BashHookCase):
    """SPD-049: a git call under a verb Law 7 allows writes a file of its own choosing through an option or the
    environment, and the hook checked neither -- so a member wrote outside its deliverables and Spud past Law 1, with a
    trace appended to a rendered ledger note or the database corrupting it.

    Probed on git 2.54.0 (Apple Git-157), scratchpad only.  Environment: every GIT_TRACE* variable set to an absolute
    path appended its trace there (GIT_TRACE, GIT_TRACE_PERFORMANCE, GIT_TRACE_SETUP, GIT_TRACE_PACK_ACCESS,
    GIT_TRACE_REFS, GIT_TRACE2, GIT_TRACE2_EVENT and GIT_TRACE2_PERF did so under a bare `git status`), a directory
    value made GIT_TRACE2 write one file per process inside it, and `0`, `1`, `2`, `3`, `9`, `true`, `false` and an
    empty value wrote nothing (a descriptor or off), while a relative value only warned ("unknown trace value for
    'GIT_TRACE'") and wrote nothing.  GIT_INDEX_FILE named the index `git read-tree HEAD` wrote (44 KB), and
    GIT_OBJECT_DIRECTORY the directory `git hash-object -w --stdin` wrote a loose object into.

    Options: `--output` (spaced and `=`) opened its file under diff, log, show, whatchanged, format-patch, range-diff,
    diff-tree, diff-index, diff-files and blame; `git archive -o F`, `-oF`, `--output=F` and `--output F` each wrote the
    tar; `git format-patch -o D`, `-oD`, `--output-directory=D`, `--output-directory D` and the cluster `-so D` each
    wrote the patch into D; `git bundle create F HEAD` (with `-q` or `--version=2` before it) wrote the bundle;
    `git bugreport -o D` and `git diagnose -o D` wrote their report and zip into D; `git checkout-index -a --prefix=D/`
    wrote the whole tree there; `git mailsplit -oD mbox` wrote `0001`; `git mailinfo F F2` wrote both; and
    `git pack-objects D/pack --revs` wrote the pack, its index and its rev file.  `git init` and `git clone` also make a
    directory wherever they are pointed, but GIT_WRITE_VERBS already refuses both to a member whole, and Spud's own
    `git init` is his, so their targets are left alone.

    Each such path is checked with the path rule, like a redirection target, against every directory the shell may be
    in; a path the hook cannot resolve refuses a member, fail closed.  Law 1 holds for Spud here as it does for a
    redirection, so a trace appended to a rendered ledger note is refused to everyone.  AGENT_A plans tests/** and
    bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests/out", "bin"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "ledger/tickets/SPD-001.md").write_text("orig\n", encoding="utf-8")
        (home / "docs/x.md").write_text("orig\n", encoding="utf-8")

    def analysis(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path)))

    def out_of_deliverables(self, command, needle="deliverables"):
        """Refused for AGENT_A, whose deliverables are tests/** and bin/spud, and silent for AGENT_C, whose are **."""
        r = self.assertRefused(command, needle, AGENT_A)
        with self.subTest(command=command, agent_id=AGENT_C):
            self.assertSilent(command, AGENT_C)
        return r

    def refused_for_everyone(self, command, needle="generated"):
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertRefused(command, "Law 1", agent_id=None)

    # -- the environment ---------------------------------------------------------------

    def test_every_trace_variable_by_every_route_is_refused(self):
        home = self.home.path
        for var in GIT_TRACE_VARS:
            for line in ("%s=%s/docs/trace.log git status", "env %s=%s/docs/trace.log git status",
                         "export %s=%s/docs/trace.log; git status", "%s=%s/docs/trace.log; git status"):
                command = line % (var, home)
                with self.subTest(command):
                    r = self.out_of_deliverables(command)
                    self.assertIn("docs/trace.log", r.reason)
                    self.assertIn(var, r.reason)

    def test_a_trace_variable_inside_the_deliverables_is_silent(self):
        home = self.home.path
        for var in ("GIT_TRACE", "GIT_TRACE2_EVENT"):
            for line in ("%s=%s/tests/out/trace.log git status", "env %s=%s/tests/out/trace.log git status",
                         "export %s=%s/tests/out/trace.log; git status"):
                with self.subTest(line % (var, home)):
                    self.assertSilent(line % (var, home))

    def test_a_descriptor_or_an_off_value_is_silent(self):
        for value in ("0", "1", "2", "3", "9", "true", "false", "", "docs/trace.log", "./docs/trace.log"):
            for var in ("GIT_TRACE", "GIT_TRACE2"):
                command = "%s=%s git status" % (var, value)
                with self.subTest(command):
                    self.assertSilent(command)
                    self.assertSilent(command, agent_id=None)

    def test_a_trace_value_the_hook_cannot_resolve_is_refused(self):
        for command in ("GIT_TRACE=$T git status", "env GIT_TRACE=$(echo x) git status",
                        "export GIT_TRACE2=$T; git status"):
            with self.subTest(command):
                self.assertRefused(command, "cannot resolve", AGENT_A)
                self.assertSilent(command, agent_id=None)

    def test_a_trace_variable_without_a_git_call_is_silent(self):
        home = self.home.path
        for ok in ("GIT_TRACE=%s/docs/trace.log ls", "GIT_TRACE=%s/docs/trace.log; ls", "echo GIT_TRACE=%s/docs/t"):
            with self.subTest(ok % home):
                self.assertSilent(ok % home)
                self.assertSilent(ok % home, agent_id=None)

    def test_the_index_and_object_directory_variables_are_refused(self):
        home = self.home.path
        for command in ("env GIT_INDEX_FILE=%s/docs/idx git status" % home,
                        "GIT_INDEX_FILE=%s/docs/idx git diff" % home,
                        "GIT_OBJECT_DIRECTORY=%s/docs/objects git status" % home):
            with self.subTest(command):
                self.out_of_deliverables(command)
        # SPD-087 made read-tree and hash-object Law 7's own, so a member is refused by the verb before the path is
        # read; the variable still names the file Spud's own call writes, and Law 1 still refuses him a generated note.
        for command in ("GIT_INDEX_FILE=%s/docs/idx git read-tree HEAD" % home,
                        "GIT_OBJECT_DIRECTORY=%s/docs/objects git hash-object -w --stdin" % home,
                        "export GIT_OBJECT_DIRECTORY=%s/docs/objects; git hash-object -w f" % home,
                        "GIT_INDEX_FILE=%s/ledger/tickets/SPD-001.md git read-tree HEAD" % home):
            with self.subTest(command):
                self.assertRefused(command, "Law 7", AGENT_A)
                self.assertRefused(command, "Law 7", AGENT_C)
                self.assertRefused(command, "Law 1", agent_id=None)  # the variable is still read for Spud
        self.assertSilent("GIT_INDEX_FILE=%s/CLAUDE.md git read-tree HEAD" % home, agent_id=None)

    def test_a_trace_into_a_generated_note_or_the_database_is_refused_for_spud_too(self):
        home = self.home.path
        self.refused_for_everyone("GIT_TRACE=%s/ledger/tickets/SPD-001.md git status" % home)
        self.refused_for_everyone("GIT_TRACE2_EVENT=%s/reports/2026-09-15.md git log -1" % home)
        self.refused_for_everyone("env GIT_TRACE=%s/ledger/teams/SPUD-001/Russet.md git status" % home)
        # ledger/Home.md is Spud's own hand-written note, so the path rule lets him write it and refuses a member
        self.assertSilent("GIT_TRACE=%s/ledger/Home.md git status" % home, agent_id=None)
        self.assertRefused("GIT_TRACE=%s/ledger/Home.md git status" % home, "generated", AGENT_C)
        for agent_id in (AGENT_A, None):
            with self.subTest(agent_id=agent_id):
                self.assertRefused("GIT_TRACE=%s/.spud/ledger.db git status" % home, "ledger database", agent_id)

    # -- the options -------------------------------------------------------------------

    def test_archive_output_in_every_spelling(self):
        home = self.home.path
        for spelling in ("-o %s/docs/a.tar", "-o%s/docs/a.tar", "--output=%s/docs/a.tar", "--output %s/docs/a.tar",
                         "--out=%s/docs/a.tar", "--outp %s/docs/a.tar"):
            command = "git archive %s HEAD" % (spelling % home)
            with self.subTest(command):
                self.out_of_deliverables(command)
        for ok in ("git archive -o tests/out/a.tar HEAD", "git archive --output=tests/out/a.tar HEAD"):
            with self.subTest(ok):
                self.assertSilent(ok)  # inside AGENT_A's deliverables; Law 1 still refuses Spud that path, as a redirect would
                self.assertRefused(ok, "Law 1", agent_id=None)
        for ok in ("git archive HEAD", "git archive --format=tar HEAD", "git archive -o /dev/null HEAD"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_format_patch_output_directory_in_every_spelling(self):
        home = self.home.path
        for spelling in ("-o %s/docs", "-o%s/docs", "--output-directory=%s/docs", "--output-directory %s/docs",
                         "-so %s/docs", "--output=%s/docs/p.patch"):
            command = "git format-patch %s -1" % (spelling % home)
            with self.subTest(command):
                self.out_of_deliverables(command)
        self.assertSilent("git format-patch -o tests/out -1")
        self.assertSilent("git format-patch -1")

    def test_the_diff_output_option_on_every_verb_that_takes_it(self):
        home = self.home.path
        for verb in ("diff", "log", "show", "whatchanged", "range-diff", "diff-tree", "diff-index", "diff-files",
                     "blame"):
            for spelling in ("--output=%s/docs/d.txt", "--output %s/docs/d.txt"):
                command = "git %s %s" % (verb, spelling % home)
                with self.subTest(command):
                    self.out_of_deliverables(command)
        self.assertSilent("git diff --output=tests/out/d.txt")
        self.assertSilent("git log --oneline -5")  # --oneline is no prefix of --output
        self.assertSilent("git log --output-indicator-new=x -1")

    def test_bundle_create_names_the_file_it_writes(self):
        # SPD-087 made `git bundle` Law 7's own whole -- `unbundle` writes the objects and refs the bundle holds into
        # the repository -- so a member runs no form of it; the file `create` names is still held to Spud's path rule.
        home = self.home.path
        for command in ("git bundle create %s/docs/b.bundle HEAD", "git bundle create -q %s/docs/b.bundle HEAD",
                        "git bundle create --version=2 %s/docs/b.bundle HEAD", "git bundle verify %s/docs/b.bundle",
                        "git bundle list-heads %s/docs/b.bundle", "git bundle unbundle %s/docs/b.bundle",
                        "git bundle create tests/out/b.bundle HEAD"):
            filled = command % home if "%s" in command else command
            with self.subTest(filled):
                self.assertRefused(filled, "Law 7", AGENT_A)
                self.assertRefused(filled, "Law 7", AGENT_C)
        for spud_reads_the_path in ("git bundle create %s/docs/b.bundle HEAD", "git bundle create -q %s/docs/b.bundle HEAD",
                                    "git bundle create --version=2 %s/docs/b.bundle HEAD",
                                    "git bundle create %s/ledger/tickets/SPD-001.md HEAD"):
            with self.subTest(spud_reads_the_path % home):
                self.assertRefused(spud_reads_the_path % home, "Law 1", agent_id=None)
        for spud_writes_nothing in ("git bundle verify %s/docs/b.bundle", "git bundle list-heads %s/docs/b.bundle",
                                    "git bundle unbundle %s/docs/b.bundle"):
            with self.subTest(spud_writes_nothing % home):
                self.assertSilent(spud_writes_nothing % home, agent_id=None)

    def test_the_other_verbs_that_name_a_path_they_write(self):
        home = self.home.path
        for command in ("git bugreport -o %s/docs", "git bugreport --output-directory=%s/docs",
                        "git diagnose -o %s/docs", "git diagnose --output-directory %s/docs",
                        "git mailsplit -o%s/docs mbox", "git mailinfo %s/docs/msg %s/docs/patch",
                        "git fast-export --export-marks=%s/docs/marks HEAD"):
            filled = command % ((home,) * command.count("%s"))
            with self.subTest(filled):
                self.out_of_deliverables(filled)
        self.assertSilent("git bugreport -o tests/out")
        # an option that only reads the file it names carries no entry
        for ok in ("git archive --add-file=%s/docs/x.md HEAD", "git grep -f %s/docs/patterns foo",
                   "git ls-files -X %s/docs/exclude"):
            with self.subTest(ok % home):
                self.assertSilent(ok % home)

    def test_a_verb_law_7_now_refuses_keeps_its_entry_for_spud(self):
        """SPD-087 refuses these verbs to a member whole -- each writes the index, the object database, the pack files
        or the credential file -- so the member is refused by the verb before the path is read.  Their entries in
        GIT_VERB_FILE_OPTIONS and GIT_VERB_FILE_POSITIONALS stay, because Law 7 does not bind Spud: his own call still
        names the file, and Law 1 still refuses him every path in the repository that is not his own."""
        home = self.home.path
        for command in ("git checkout-index -a --prefix=%s/docs/", "git checkout-index -a --prefix %s/docs/",
                        "git pack-objects %s/docs/pack", "git index-pack -o %s/docs/x.idx",
                        "git read-tree --index-output=%s/docs/idx HEAD", "git read-tree --index-output %s/docs/idx HEAD",
                        "git commit-graph write --object-dir %s/docs", "git multi-pack-index --object-dir=%s/docs write",
                        "git repack --expire-to=%s/docs", "git repack --filter-to %s/docs",
                        "git credential-store --file %s/docs/creds get", "git checkout-index -a --prefix=tests/out/",
                        "git read-tree --index-output=tests/out/idx HEAD",
                        "git read-tree --index-output=%s/ledger/tickets/SPD-001.md HEAD",
                        "git index-pack -o %s/reports/2026-09-15.md"):
            filled = command % ((home,) * command.count("%s"))
            with self.subTest(filled):
                self.assertRefused(filled, "Law 7", AGENT_A)
                self.assertRefused(filled, "Law 7", AGENT_C)
                r = self.assertRefused(filled, "Law 1", agent_id=None)  # the path is still read for Spud
                self.assertIn("this git call writes", r.reason)
        self.assertRefused("git read-tree --index-output=%s/.spud/ledger.db HEAD" % home, "ledger database", agent_id=None)
        self.assertSilent("git read-tree --index-output=%s/CLAUDE.md HEAD" % home, agent_id=None)  # one of Spud's own

    def test_an_option_target_the_hook_cannot_resolve_is_refused(self):
        for command in ("git archive -o $T HEAD", "git archive --output=$(echo x) HEAD",
                        "git format-patch -o $D -1", "git mailinfo $M $P"):
            with self.subTest(command):
                self.assertRefused(command, "cannot resolve", AGENT_A)
                self.assertSilent(command, agent_id=None)
        # a verb SPD-087 refuses whole earns Law 7's reason first; Spud's own call keeps this one
        self.assertRefused("git bundle create $F HEAD", "Law 7", AGENT_A)
        self.assertSilent("git bundle create $F HEAD", agent_id=None)

    def test_a_relative_target_after_a_directory_the_hook_cannot_follow_is_refused(self):
        for command in ("popd; git archive -o a.tar HEAD", "cd -; git mailinfo m.txt p.patch",
                        "source x.sh; git diff --output=d.txt", "cd $DIR; git format-patch -o out -1"):
            with self.subTest(command):
                self.assertRefused(command, "cannot follow", AGENT_A)
                self.assertRefused(command, "cannot follow", agent_id=None)
        self.assertRefused("cd -; git bundle create b.bundle HEAD", "Law 7", AGENT_A)  # the verb, before the path
        self.assertRefused("cd -; git bundle create b.bundle HEAD", "cannot follow", agent_id=None)

    def test_the_directories_the_shell_may_be_in_are_followed(self):
        home = self.home.path
        self.assertRefused("cd ledger && git archive -o tickets/a.tar HEAD", "generated", AGENT_C)
        self.assertRefused("cd %s/ledger; git archive -o tickets/SPD-001.md HEAD" % home, "Law 1", agent_id=None)
        self.assertRefused("cd docs && git bugreport -o .", "deliverables", AGENT_A)
        self.assertSilent("cd tests/out && git archive -o a.tar HEAD")

    def test_law_1_holds_for_spud_and_his_own_paths_do_not(self):
        home = self.home.path
        self.assertRefused("git archive -o %s/ledger/tickets/SPD-001.md HEAD" % home, "Law 1", agent_id=None)
        self.assertRefused("git diff --output %s/reports/2026-09-15.md" % home, "Law 1", agent_id=None)
        self.assertSilent("git archive -o %s/CLAUDE.md HEAD" % home, agent_id=None)
        self.assertSilent("git archive -o /dev/null HEAD", agent_id=None)
        self.assertRefused("git archive -o %s/CLAUDE.md HEAD" % home, "deliverables", AGENT_A)

    def test_init_and_clone_keep_law_7s_own_reason(self):
        home = self.home.path
        for command in ("git init %s/docs/new" % home, "git clone https://x/y %s/docs/new" % home):
            with self.subTest(command):
                self.assertRefused(command, "Law 7", AGENT_A)
                self.assertRefused(command, "Law 7", AGENT_C)
                self.assertSilent(command, agent_id=None)

    def test_a_refusal_the_words_as_spelled_earn_keeps_its_own_reason(self):
        home = self.home.path
        r = self.assertRefused("git commit -o %s/docs/a.tar -m x" % home, "Law 7", AGENT_A)
        self.assertIn("git commit", r.reason)
        r = self.assertRefused("git archive --exec=cmd -o %s/docs/a.tar HEAD" % home, "Law 7", AGENT_A)
        self.assertIn("--exec", r.reason)
        self.assertRefused("GIT_TRACE=%s/docs/t.log git push" % home, "git push", AGENT_A)

    def test_the_channel_records_what_the_call_writes(self):
        home = self.home.path
        cwds = frozenset([str(home)])
        self.assertEqual(self.analysis("git archive -o docs/a.tar HEAD").git_writes,
                         [("archive -o docs/a.tar", "docs/a.tar", cwds)])
        self.assertEqual(self.analysis("GIT_TRACE=/tmp/t git status").git_writes,
                         [("GIT_TRACE=/tmp/t", "/tmp/t", cwds)])
        self.assertEqual(self.analysis("git status").git_writes, [])
        self.assertEqual(self.analysis("GIT_TRACE=1 git status").git_writes, [])

    def test_inside_shell_strings_eval_and_subshells(self):
        home = self.home.path
        for command in ("sh -c 'git archive -o %s/docs/a.tar HEAD'", "eval 'GIT_TRACE=%s/docs/t.log git status'",
                        "(git archive -o %s/docs/a.tar HEAD)", "true && git archive -o %s/docs/a.tar HEAD",
                        "for f in a; do git archive -o %s/docs/a.tar HEAD; done"):
            with self.subTest(command % home):
                self.assertRefused(command % home, "deliverables", AGENT_A)


# The verbs `git --list-cmds=main` gives on this machine that a member may run, as SPD-087's sweep read them: every name
# whose refusal is None for both git_refused and git_not_allowed with no argument on the line.  `stash` and `worktree`
# are missing because their bare forms write (`git stash` stashes, `git worktree` prints usage but its subcommands add
# and remove), so git_refused refuses them with no subcommand; `branch`, `tag`, `config`, `remote` and `reflog` list.
SPD_087_ALLOWED = (
    "annotate", "archive", "blame", "branch", "bugreport", "cat-file", "check-attr", "check-ignore", "check-mailmap",
    "check-ref-format", "cherry", "column", "config", "count-objects", "describe", "diagnose", "diff", "diff-files",
    "diff-index", "diff-pairs", "diff-tree", "fast-export", "fetch", "fmt-merge-msg", "for-each-ref", "format-patch",
    "get-tar-commit-id", "grep", "help", "last-modified", "log", "ls-files", "ls-remote", "ls-tree", "mailinfo",
    "mailsplit", "merge-base", "name-rev", "pack-redundant", "patch-id", "pickaxe", "range-diff", "reflog", "remote",
    "repo", "request-pull", "rev-list", "rev-parse", "shortlog", "show", "show-branch", "show-index", "show-ref",
    "status", "stripspace", "tag", "var", "verify-commit", "verify-pack", "verify-tag", "version", "whatchanged",
)


class GitVerbAllowlistTest(BashHookCase):
    """SPD-087: Law 7's table named 31 verbs and git answers to about 170 (`git --list-cmds=main`: 174 on git 2.54.0,
    Apple Git-157), so the rest ran silent for a member.  `git stage` is git's own spelling of `git add` (`git stage -h`
    prints "usage: git add [<options>] [--] <pathspec>...") and `git init-db` of `git init` ("usage: git init [-q |
    --quiet] [--bare] ..."), and the plumbing writes the index, the object database and the working tree under two dozen
    more -- read-tree, update-index, write-tree, commit-tree, hash-object -w, checkout-index, repack, pack-refs,
    fast-import, unpack-objects, index-pack, mktag, mktree, sparse-checkout, maintenance, subtree, send-pack,
    receive-pack, update-server-info and their kin.  So the reading is inverted (the ticket's shape (b)):
    syntax.GIT_MEMBER_VERBS names the verbs a member runs, git_verbs.git_not_allowed refuses every other name git knows,
    and a writer a later git adds is refused before anyone has read it.

    The sweep read every name of `git --list-cmds=main` with `git <name> -h` and with its line in `git help -a`, whose
    own groups are git's reading of the same question -- "Low-level Commands / Manipulators" against "/ Interrogators",
    "Syncing Repositories", "Internal Helpers" -- and agree with this one everywhere but two: `git for-each-repo
    --config=<key> -- <arguments>` runs a git command of its own in every repository the key names, and `git unpack-file
    <blob>` "Creates a temporary file with a blob's contents" in the current directory, which no option names, so both
    are refused though git groups them as interrogators.  The synonyms the usage lines gave up: stage (add), init-db
    (init), fsck-objects (fsck), pickaxe and annotate (blame).

    The three refusals keep their own reasons, in this order: Law 7's own table (GIT_WRITE_VERBS, the verbs a member
    would reach for, refused whatever git's command list says), SPD-047's unknown verb (a name git does not know, which
    must be an alias or an external `git-<verb>`), then this allowlist.  AGENT_A plans tests/** and bin/spud; AGENT_C
    plans **; Law 7 binds neither Spud nor a plain session."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)

    def refused_for_members(self, command, needle="Law 7"):
        r = None
        for agent_id in (AGENT_C, AGENT_A):
            with self.subTest(command=command, agent_id=agent_id):
                r = self.assertRefused(command, needle, agent_id)
        with self.subTest(command=command, agent_id="spud"):
            self.assertSilent(command, agent_id=None)  # Law 7 does not bind Spud
        return r

    def finding(self, command):
        m = load_spud_module()
        return m.analyse_command(command, m.ShellAnalysis(cwd=str(self.home.path), home=str(self.home.path))).findings

    def test_gits_own_spellings_of_add_and_init(self):
        for command in ("git stage .", "git stage -p", "git stage -- f", "git init-db x", "git init-db --bare",
                        "git -C . stage .", "sh -c 'git stage .'", "env git init-db x"):
            with self.subTest(command):
                r = self.refused_for_members(command)
                self.assertIn("Spud commits", r.reason)
        self.assertEqual(self.finding("git stage ."), [("git", ("stage", "stage"))])

    def test_the_plumbing_that_writes_the_repository(self):
        for verb in ("checkout-index -a -f", "read-tree HEAD", "update-index --refresh", "write-tree", "commit-tree",
                     "hash-object -w -", "repack -ad", "pack-refs --all", "fast-import", "unpack-objects", "index-pack",
                     "sparse-checkout set x", "maintenance run", "subtree add --prefix=p c", "send-pack host:r",
                     "receive-pack .", "update-server-info", "mktag", "mktree", "prune-packed", "unpack-file abc",
                     "merge-file a o b", "backfill", "history reword HEAD", "replay --onto x y", "fetch-pack host:r",
                     "http-fetch url", "http-push r", "quiltimport", "pack-objects --stdout --revs", "apply x.patch"):
            with self.subTest(verb):
                r = self.refused_for_members("git " + verb)
                self.assertIn("git " + verb.split()[0], r.reason)

    def test_the_verbs_that_run_a_program_or_serve_the_repository(self):
        for verb in ("for-each-repo --config=x -- push", "hook run pre-commit", "merge-index cmd -a", "merge-one-file",
                     "merge-octopus", "merge-ours", "merge-recursive a", "merge-resolve", "merge-subtree a b",
                     "p4 clone //depot", "shell -c x", "daemon --export-all", "http-backend", "upload-pack .",
                     "upload-archive .", "upload-archive--writer .", "remote-ext r url", "remote-fd r url",
                     "remote-http r url", "remote-https r url", "remote-ftp r url", "remote-ftps r url", "imap-send",
                     "send-email p", "web--browse u", "difftool A B", "submodule--helper list", "checkout--worker",
                     "difftool--helper", "fsmonitor--daemon start", "credential fill", "credential-cache exit",
                     "credential-cache--daemon s", "credential-osxkeychain get", "credential-store store",
                     "gui--askpass x", "gui--askyesno x", "sh-i18n--envsubst"):
            with self.subTest(verb):
                r = self.refused_for_members("git " + verb)
                self.assertIn("git " + verb.split()[0], r.reason)

    def test_a_verb_whose_reading_form_is_a_subcommand_or_a_flag_is_refused_whole(self):
        # The verb is what Law 7 reads, and only the seven of git_refused's own cases are read by subcommand; every
        # other name whose read and write forms are told apart that way is refused whole, read form included.
        for verb in ("bundle verify f", "bundle list-heads f", "bundle unbundle f", "commit-graph verify",
                     "commit-graph write", "multi-pack-index verify", "refs verify", "refs list", "refs migrate",
                     "rerere status", "rerere diff", "rerere clear", "sparse-checkout list", "hash-object f",
                     "credential-store get", "fsck", "fsck-objects", "merge-tree a b",
                     "interpret-trailers f", "interpret-trailers --in-place f", "maintenance unregister"):
            with self.subTest(verb):
                self.refused_for_members("git " + verb)

    def test_the_read_verbs_of_the_sweep_stay_silent(self):
        for ok in ("git annotate f", "git pickaxe f", "git cherry", "git column", "git stripspace", "git patch-id",
                   "git get-tar-commit-id", "git check-attr -a f", "git check-mailmap x", "git check-ref-format x",
                   "git check-ref-format --branch x", "git diff-pairs -z", "git last-modified", "git repo info",
                   "git repo structure", "git request-pull v1 url", "git show-branch", "git show-branch -a",
                   "git show-index", "git verify-pack -v p.idx", "git verify-commit HEAD", "git fmt-merge-msg",
                   "git pack-redundant --all", "git ls-tree HEAD", "git diff-files", "git diff-index HEAD",
                   "git name-rev --all", "git merge-base a b", "git cat-file -p HEAD", "git var -l",
                   "git format-patch -1", "git archive HEAD", "git mailsplit mbox", "git fast-export HEAD"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_every_name_git_lists_is_read_one_way_or_the_other(self):
        """The sweep as data.  A git that lists a name neither table holds refuses it, which is the point of the
        allowlist; this fails so the next reader sweeps the new name rather than leaving a member refused a read verb."""
        m = load_spud_module()
        names = subprocess.run(["git", "--list-cmds=main"], capture_output=True, text=True, check=True).stdout.split()
        self.assertGreater(len(names), 100, names)
        allowed = [n for n in names if m.git_refused(n, []) is None and m.git_not_allowed(n) is None]
        self.assertEqual(allowed, sorted(SPD_087_ALLOWED), "git's command list changed: re-read the new names")
        self.assertEqual(sorted(m.GIT_MEMBER_VERBS - set(names)), [])  # no name in the table that git does not have
        self.assertEqual(sorted(m.GIT_MEMBER_VERBS & m.GIT_WRITE_VERBS), [])  # and none in both tables

    def test_a_writer_a_later_git_adds_is_refused(self):
        # git's own command list is the only thing that says a verb exists, so a later git is simulated by a longer one.
        m = load_spud_module()
        commands = frozenset({"status", "push", "stash", "graft-tree"})
        with mock.patch("spudlib.shell.git_verbs.git_own_commands", lambda home=None: commands):
            self.assertEqual(self.finding("git graft-tree HEAD"), [("git", ("graft-tree", "graft-tree"))])
            self.assertEqual(self.finding("git status"), [("git", ("status", None))])
            self.assertEqual(self.finding("git push"), [("git", ("push", "push"))])
        self.assertEqual(m.git_not_allowed("graft-tree"), "graft-tree")

    def test_a_glob_that_can_expand_to_a_refused_verb_is_read_as_it(self):
        # `stage` is one of Law 7's own, so GLOB_SAMPLES holds it and the glob is read as the verb itself.  A name the
        # allowlist refuses is not sampled and needs no sample: the reading as spelled is not one of git's commands
        # either, which is SPD-047's refusal, so the glob is closed whatever it could expand to.
        r = self.refused_for_members("git stag?")
        self.assertIn("git stage", r.reason)
        r = self.refused_for_members("git read-tre?", "not one of git's own commands")
        self.assertIn("read-tre?", r.reason)
        for command in ("git st?ge .", "git sta*", "git ini?-db x", "git updat?-index --refresh", "git *tree"):
            with self.subTest(command):
                self.refused_for_members(command)

    def test_law_7s_own_table_keeps_its_reason_when_git_cannot_be_read(self):
        # The hook fails closed either way; which reason a member gets is what the two tables decide.
        path = self.home.env.get("PATH")
        self.home.env["PATH"] = str(self.home.path / "no-git-here")
        try:
            r = self.assertRefused("git stage .", "Law 7")
            self.assertIn("Spud commits", r.reason)
            self.assertNotIn("--list-cmds=main", r.reason)
            r = self.assertRefused("git read-tree HEAD", "Law 7")  # the allowlist defers to SPD-047's reason here
            self.assertIn("--list-cmds=main", r.reason)
            self.assertSilent("git read-tree HEAD", agent_id=None)
        finally:
            if path is None:
                self.home.env.pop("PATH", None)
            else:
                self.home.env["PATH"] = path
        self.assertRefused("git read-tree HEAD", "Law 7")


class DescriptorRedirectTest(BashHookCase):
    """SPD-045 (proposed by SPUD-040/Bintje): separate_redirects dropped any operand that is all digits or `-` for every
    output operator, though only `>&` reads such a word as a descriptor or a close.  The others open a file of that name,
    so a member wrote outside its deliverables and Spud past Law 1 with nothing checked.

    Probed again in zsh 5.9 -f -o nobareglobqual (this Mac's Bash tool) and bash 3.2, in an empty scratch directory:
    `echo x > 3`, `>3`, `>> 3`, `>| 3`, `&> 3`, `1> 4`, `9> 9`, `2> 12` and `2>12` each created a file named by the
    digits in both shells, `&>> 3` in zsh (a syntax error in bash 3.2), and `> -`, `>> -` and `&> -` created a file named
    `-`.  Only `>& 3`, `>&3` and `1>&3` were a descriptor (both shells: "bad file descriptor") and `>& -`, `>&-` a close,
    while `2>&1` duplicated; `>& out` and `>&out` wrote the file `out` in both.  shlex reads `2>&1` as `2`, `>&`, `1` and
    `2>12` as `2`, `>`, `12`, so the leading descriptor never reaches the operand check.  AGENT_A plans tests/** and
    bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        for d in ("ledger/tickets", "docs", "tests"):
            (self.home.path / d).mkdir(parents=True, exist_ok=True)

    def test_a_digit_or_dash_operand_is_a_file_for_every_operator_but_the_dup(self):
        for operator in (">", ">>", ">|", "&>", "&>>"):
            for operand in ("3", "-", "12", "0"):
                for spacing in ("%s %s", "%s%s"):
                    command = "cd docs && echo x " + (spacing % (operator, operand))
                    with self.subTest(command):
                        self.assertRefused(command, "deliverables", AGENT_A)
                        self.assertSilent(command, AGENT_C)

    def test_a_leading_descriptor_does_not_make_the_operand_one(self):
        for command in ("cd docs && echo x 2> 12", "cd docs && echo x 2>12", "cd docs && echo x 1> 4",
                        "cd docs && echo x 9> 9", "cd docs && echo x 2>> 3"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables", AGENT_A)

    def test_the_dup_operator_keeps_its_descriptor_and_its_close(self):
        for ok in ("echo x >& 3", "echo x >&3", "echo x 1>&3", "echo x 2>&1", "echo x >&-", "echo x >& -",
                   "echo x >&2", "make 2>&1", "exec 3>&1", "cd docs && echo x >& 3", "cd docs && echo x >&-"):
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)

    def test_the_dup_operator_with_a_file_name_is_still_a_target(self):
        for command in ("cd docs && echo x >& out", "cd docs && echo x >&out", "cd docs && ls >& 3x"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables", AGENT_A)

    def test_a_generated_file_named_by_digits_is_refused_for_spud_too(self):
        for command in ("cd ledger/tickets && echo x > 3", "cd ledger/tickets && echo x >> -",
                        "cd ledger/tickets && echo x &> 12"):
            with self.subTest(command):
                self.assertRefused(command, "generated", AGENT_C)
                self.assertRefused(command, "Law 1", agent_id=None)

    def test_the_analysis_records_the_operand(self):
        m = load_spud_module()
        for command, targets in (("echo x > 3", ["3"]), ("echo x >> -", ["-"]), ("echo x 2> 12", ["12"]),
                                 ("echo x &> 3", ["3"]), ("echo x >| 3", ["3"]), ("echo x >&3", []),
                                 ("echo x 2>&1", []), ("echo x >&-", []), ("echo x >& out", ["out"])):
            with self.subTest(command):
                self.assertEqual([t for t, _cwds in m.analyse_command(command).redirects], targets)


class UnenterableDirTest(BashHookCase):
    """SPD-037 (proposed by SPUD-036/Huckleberry): SPD-030's directory model marked a cd uncertain only when the target
    was not a directory (os.path.isdir), so a directory that exists but cannot be entered -- no execute bit, /var/root,
    a directory a member chmod 000's -- passed, the hook assumed the cd succeeded, and a relative write after `;` or
    `||` landed in the original directory unchecked.

    Probed in zsh 5.9 -f -o nobareglobqual and bash 3.2 on a scratch directory with mode 000: `cd <dir>` is "permission
    denied" and PWD stays put in both, while `cd <dir> && pwd` runs nothing (exit 1).  os.path.isdir is True there and
    os.access(d, os.X_OK) is False, which is the difference the model was missing.  The `&&` form is unchanged: the write
    only runs where the cd succeeded.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        home = self.home.path
        for d in ("ledger/tickets", "docs", "tests"):
            (home / d).mkdir(parents=True, exist_ok=True)
        self.locked = home / "locked"
        self.locked.mkdir(exist_ok=True)
        os.chmod(self.locked, 0o000)
        self.addCleanup(os.chmod, self.locked, 0o755)

    def test_a_cd_into_an_unenterable_directory_keeps_both_directories(self):
        for command in ("cd %s; echo x > ledger/tickets/SPD-001.md", "cd %s || echo x > ledger/tickets/SPD-001.md",
                        "cd %s\necho x > ledger/tickets/SPD-001.md", "cd %s; echo x | tee ledger/tickets/SPD-001.md",
                        "pushd %s; echo x > ledger/tickets/SPD-001.md"):
            with self.subTest(command % self.locked):
                self.assertRefused(command % self.locked, "generated", AGENT_C)
                self.assertRefused(command % self.locked, "Law 1", agent_id=None)

    def test_the_and_form_is_unchanged(self):
        # Only the directory the cd may have reached is checked, so `locked/ledger/tickets/SPD-001.md` is no generated
        # root and AGENT_C's `**` covers it.  Spud is refused there as he was before, the locked directory being inside
        # the repository and outside his own paths -- Law 1, not the cd model.
        for ok in ("cd %s && echo x > ledger/tickets/SPD-001.md", "cd %s && echo x > note.txt"):
            with self.subTest(ok % self.locked):
                self.assertSilent(ok % self.locked, AGENT_C)
                self.assertRefused(ok % self.locked, "Law 1", agent_id=None)

    def test_an_enterable_directory_is_unchanged(self):
        home = self.home.path
        self.assertSilent("cd %s/tests; echo x > keep.py" % home)
        self.assertSilent("cd %s/tests || echo x > keep.py" % home)
        self.assertRefused("cd %s/docs; echo x > x.md" % home, "deliverables", AGENT_A)

    def test_a_git_write_after_such_a_cd_is_checked_in_both_directories(self):
        self.assertRefused("cd %s; git archive -o ledger/tickets/SPD-001.md HEAD" % self.locked, "generated", AGENT_C)


ARG_WORDING = "a write by argument"  # SPD-121: a file a command names as an operand and writes, checked as a redirection's target
VARIABLE_WORDING = "spell the path out"  # an unresolvable target, for a redirection and a write by argument alike
WORD_WORDING = "spell the words out"  # SPD-043's var-word: an expansion in a word the hook reads by name

# SPD-121: one line per command the Bash hook reads writes by argument from, `{}` the file it writes.
ARG_WRITERS = (
    ("cp", "cp tests/src.txt {}"),
    ("mv", "mv /tmp/spd-121-src {}"),  # a source every caller may remove: mv's sources are written too
    ("ln -s", "ln -s /tmp/x {}"),
    ("link", "link tests/src.txt {}"),
    ("install", "install -m 644 tests/src.txt {}"),
    ("install -d", "install -d {}"),
    ("mkdir", "mkdir -p {}"),
    ("touch", "touch {}"),
    ("rm", "rm -f {}"),
    ("unlink", "unlink {}"),
    ("rmdir", "rmdir {}"),
    ("truncate", "truncate -s 0 {}"),
    ("chmod", "chmod +x {}"),
    ("chown", "chown nobody {}"),
    ("chgrp", "chgrp staff {}"),
    ("chflags", "chflags nohidden {}"),
    ("sed -i ''", "sed -i '' s/a/b/ {}"),
    ("sed -i.bak", "sed -i.bak s/a/b/ {}"),
    ("sed -I", "sed -I '' -e s/a/b/ {}"),
)


class ArgumentWriteTest(BashHookCase):
    """SPD-121: the Bash hook held a write to the path rule only when it was a redirection, a tee operand or one of SPD-049's
    file-writing git options, so a command that writes the files it names as operands was never read: a bound member's
    `cp tests/src.txt tests/fake/.git/hooks/post-index-change`, `ln -s /tmp/x tests/fake/.git/hooks/x` and
    `cp tests/src.txt /Users/nobody/.zshrc` were silent, past Law 5, SPD-064's outside allowlist and SPD-066's .git rule.

    Spud's design decision: a write by argument is a write by redirection.  The files cp, mv, ln (and link), install, mkdir,
    touch, rm (and unlink), rmdir, truncate, chmod, chown, chgrp, chflags and sed in place name as operands go through the
    check a redirection target goes through, for every caller: a member held to its globs, the outside allowlist and the .git
    rule; Spud held to Law 1 in a project and free in his own files and outside every project.  An operand the hook cannot
    resolve, and a glob, are read as a redirection target is.  Each command is read on this Mac's BSD grammar, probed in a
    scratch directory (getopt stops at the first operand, so `mkdir new -p` made ./-p; `sed -i` always takes the next word as
    its suffix, so `sed -i -e X f` backed f up to f-e; `install -M log` wrote log), and GNU's -t/--target-directory and -T
    are read too.  AGENT_A plans home:tests/** and home:bin/spud; AGENT_C plans home:bin/**."""

    def setUp(self):
        super().setUp()
        self.narrow = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:bin/**"]), AGENT_C)
        home = self.home.path
        self.home.env["HOME"] = "/Users/nobody"  # a HOME outside every project and every temp root, as OutsideProjectTest's
        for d in ("tests/fake/.git/hooks", "tests/out", "tests/copy", "docs", "ledger/tickets", "reports", "bin"):
            (home / d).mkdir(parents=True, exist_ok=True)
        for f in ("tests/src.txt", "tests/b.txt", "docs/x.md", "ledger/tickets/SPD-001.md", "bin/spud"):
            (home / f).write_text("a\n", encoding="utf-8")
        self.scratchpad = "/private/tmp/claude-%d/-Users-eric-Personal-Spud/%s/scratchpad" % (os.getuid(), SESSION)

    def refused_targets(self):
        """(target, the needle its refusal carries) for AGENT_A: a .git component, outside the deliverables, the home's
        generated roots, and outside every project."""
        return (("tests/fake/.git/hooks/post-index-change", GIT_DIR_WORDING), ("docs/x.md", "deliverables"),
                ("ledger/tickets/SPD-001.md", "generated"), ("reports/2026-09-17.md", "generated"),
                ("/Users/nobody/.zshrc", OUTSIDE), ("~/.zshrc", OUTSIDE))

    def silent_targets(self):
        return ("tests/out/new.txt", self.scratchpad + "/probe.txt", "/tmp/spd-121-x")

    def test_the_tickets_lines_are_refused_with_the_path_in_the_reason(self):
        for command, needle, path in (
            ("cp tests/src.txt tests/fake/.git/hooks/post-index-change", GIT_DIR_WORDING, "tests/fake/.git/hooks/post-index-change"),
            ("ln -s /tmp/x tests/fake/.git/hooks/x", GIT_DIR_WORDING, "tests/fake/.git/hooks/x"),
            ("cp tests/src.txt /Users/nobody/.zshrc", OUTSIDE, "/Users/nobody/.zshrc"),
        ):
            with self.subTest(command):
                r = self.assertRefused(command, needle)
                self.assertIn(path, r.reason)
                self.assertIn(ARG_WORDING, r.reason)
        # The fourth line lays out a bare repository's objects/ and refs/ with mkdir.  Both lie inside a tests/** member's
        # globs and no .git component is spelled, so the path rule allows it; SPD-066 already refuses any git call a member
        # points at a repository that is not a known checkout's own, which is what such a layout is for.  A member whose
        # globs do not cover tests/b is refused it, with the path named.
        self.assertSilent("mkdir -p tests/b/objects tests/b/refs")
        r = self.assertRefused("mkdir -p tests/b/objects tests/b/refs", "deliverables", agent_id=AGENT_C)
        self.assertIn("tests/b/objects", r.reason)

    def test_every_command_is_held_to_the_path_rule(self):
        for label, form in ARG_WRITERS:
            for target, needle in self.refused_targets():
                command = form.format(target)
                with self.subTest(command=command):
                    r = self.assertRefused(command, needle)
                    self.assertIn(target if target[0] != "~" else ".zshrc", r.reason)  # the reason names the path

    def test_every_command_is_silent_inside_the_deliverables_the_scratchpad_and_tmp(self):
        for label, form in ARG_WRITERS:
            for target in self.silent_targets():
                command = form.format(target)
                with self.subTest(command=command):
                    self.assertSilent(command)

    def test_a_command_that_writes_nothing_by_argument_stays_silent(self):
        for command in ("sed -n p docs/x.md", "sed s/a/b/ docs/x.md", "sed -e s/a/b/ -e s/c/d/ docs/x.md", "sed -E 's/(a)/b/' docs/x.md",
                        "cat docs/x.md", "ls -la docs", "grep -r x docs", "cp docs/x.md tests/out/", "ln -s docs/x.md tests/x-link",
                        "chmod -R u+w tests/out", "sed s/a/b/ -i '' docs/x.md"):  # BSD: after the script, -i and '' are files sed reads
            with self.subTest(command):
                self.assertSilent(command)

    def test_a_directory_destination_writes_each_source_inside_it(self):
        refused = (
            ("cp tests/src.txt tests/b.txt docs/", "docs/src.txt"),  # several sources: a directory
            ("cp tests/src.txt tests/b.txt docs", "docs/src.txt"),
            ("cp tests/src.txt docs", "docs/src.txt"),  # an existing directory, no slash
            ("cp tests/src.txt tests/fake/.git", "tests/fake/.git/src.txt"),
            ("cp -r tests/fake/.git tests/copy", "tests/copy/.git"),  # the source's own name, joined
            ("cp -R tests/fake/.git tests/copy/", "tests/copy/.git"),
            ("cp -t docs tests/src.txt", "docs/src.txt"),  # GNU's -t, in every spelling
            ("cp -tdocs tests/src.txt", "docs/src.txt"),
            ("cp -rt docs tests/src.txt", "docs/src.txt"),
            ("cp --target-directory=docs tests/src.txt", "docs/src.txt"),
            ("cp --target-directory docs tests/src.txt", "docs/src.txt"),
            ("cp --target docs tests/src.txt", "docs/src.txt"),
            ("cp -t tests/fake/.git/hooks tests/src.txt", "tests/fake/.git/hooks/src.txt"),
            ("cp -T tests/src.txt tests/fake/.git", "tests/fake/.git"),  # -T: the destination itself, never inside it
            ("cp tests/*.txt docs/", "docs/b.txt"),  # a source glob: each file it matches, in order
            ("mv tests/src.txt docs/", "docs/src.txt"),
            ("ln -s tests/src.txt docs/", "docs/src.txt"),
            ("install tests/src.txt docs", "docs/src.txt"),
        )
        for command, path in refused:
            with self.subTest(command):
                r = self.assertRefused(command, GIT_DIR_WORDING if ".git" in path else "deliverables")
                self.assertIn(path, r.reason)
        for command in ("cp tests/src.txt tests/out/", "cp tests/src.txt tests/b.txt tests/out", "cp -r tests/fake/.git tests/newcopy",
                        "cp -t tests/out tests/src.txt", "cp -T tests/src.txt tests/out", "cp tests/*.txt tests/out/",
                        "cp -R tests/out tests/copy/", "cp tests/src.txt tests/newdir/", "install tests/src.txt tests/out"):
            with self.subTest(command):
                self.assertSilent(command)

    def test_a_destination_reached_through_a_symlink_is_read_as_the_link_resolves(self):
        home = self.home.path
        (home / "tests" / "link").symlink_to(home / "tests" / "fake" / ".git")
        for command in ("cp tests/src.txt tests/link", "cp tests/src.txt tests/link/hooks/x", "touch tests/link/hooks/x",
                        "sed -i '' s/a/b/ tests/link/HEAD", "ln -s /tmp/x tests/link/hooks/x"):
            with self.subTest(command):
                r = self.assertRefused(command, GIT_DIR_WORDING)
                self.assertIn(str(home / "tests" / "fake" / ".git"), r.reason)  # the reading that has the component

    def test_a_case_variant_of_the_target_or_the_command_is_refused(self):
        for command in ("cp tests/src.txt tests/fake/.GIT/hooks/x", "touch tests/fake/.Git/index", "CP tests/src.txt docs/x.md",
                        "/bin/cp tests/src.txt docs/x.md", "/BIN/Cp tests/src.txt docs/x.md", "Rm docs/x.md", "SED -i '' s/a/b/ docs/x.md",
                        "/usr/bin/install tests/src.txt docs/x.md", "Mkdir docs/new"):
            with self.subTest(command):
                self.assertRefused(command, GIT_DIR_WORDING if "git" in command.lower() else "deliverables")

    def test_mv_removes_its_sources(self):
        for command, needle, path in (("mv docs/x.md tests/out/", "deliverables", "docs/x.md"),
                                      ("mv ledger/tickets/SPD-001.md /tmp/spd-121-x", "generated", "ledger/tickets/SPD-001.md"),
                                      ("mv tests/fake/.git tests/g2", GIT_DIR_WORDING, "tests/fake/.git"),
                                      ("mv -f -- docs/x.md tests/b.txt tests/out", "deliverables", "docs/x.md")):
            with self.subTest(command):
                r = self.assertRefused(command, needle)
                self.assertIn(path, r.reason)
        self.assertSilent("mv tests/src.txt tests/renamed.txt")
        self.assertSilent("mv tests/src.txt /tmp/spd-121-x")

    def test_ln_writes_its_link_name_never_its_target(self):
        for command in ("ln -s tests/fake/.git/hooks tests/hooks-link", "ln -s docs/x.md tests/x-link", "ln -s /Users/nobody/.zshrc tests/rc",
                        "ln tests/src.txt tests/hard", "ln -sf /tmp/x tests/out/x", "cd tests && ln -s ../docs/x.md"):
            with self.subTest(command):
                self.assertSilent(command)
        for command, needle, path in (("ln -s tests/src.txt docs/link", "deliverables", "docs/link"),
                                      ("ln -s /tmp/x", "deliverables", "x"),  # one operand: ./x, here the home's root
                                      ("ln -s tests/fake/.git/hooks/post-index-change", "deliverables", "post-index-change"),
                                      ("ln -s /tmp/x tests/fake/.git/hooks/pre-commit", GIT_DIR_WORDING, "pre-commit"),
                                      ("link tests/src.txt docs/hard", "deliverables", "docs/hard")):
            with self.subTest(command):
                r = self.assertRefused(command, needle)
                self.assertIn(path, r.reason)

    def test_sed_in_place_and_its_backup(self):
        for command, path in (("sed -i '' s/a/b/ docs/x.md", "docs/x.md"),
                              ("sed -i.bak s/a/b/ docs/x.md", "docs/x.md"),
                              ("sed -i.bak s/a/b/ bin/spud", "bin/spud.bak"),  # bin/spud is AGENT_A's, its backup is not
                              ("sed -i -e s/a/b/ bin/spud", "bin/spud-e"),  # BSD: -i took -e as its suffix (probed)
                              ("sed -e s/a/b/ -i '' docs/x.md", "docs/x.md"),
                              ("sed -Ei '' s/a/b/ docs/x.md", "docs/x.md"),
                              ("sed -n -i '' -e s/a/b/ tests/b.txt docs/x.md", "docs/x.md"),
                              ("sed -I '' s/a/b/ tests/b.txt docs/x.md", "docs/x.md"),
                              ("sed -f tests/s.sed -i '' docs/x.md", "docs/x.md"),
                              ("sed -i '' -- s/a/b/ docs/x.md", "docs/x.md"),
                              ("sed --in-place s/a/b/ docs/x.md", "docs/x.md"),  # GNU's, read too
                              ("sed --in-place=.bak -e s/a/b/ docs/x.md", "docs/x.md"),
                              # a `/` in the suffix BSD would append to the file name fails there (rename: Not a directory,
                              # probed), so the word is also read as GNU reads it: the script, and the files after it
                              ("sed -i 's/a/b/' docs/x.md", "docs/x.md")):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables")
                self.assertIn(path, r.reason)
        for command in ("sed -i '' s/a/b/ bin/spud", "sed -i.bak s/a/b/ tests/b.txt", "sed -i '' s/a/b/ tests/b.txt",
                        "sed -i bak s/a/b/ tests/b.txt"):
            with self.subTest(command):
                self.assertSilent(command)

    def test_chmod_and_its_kin_skip_the_mode_or_owner_and_nothing_else(self):
        for command in ("chmod 755 docs/x.md", "chmod -R u+x docs", "chmod -x docs/x.md", "chmod -v -w docs/x.md", "chmod u+x,g-w tests/b.txt docs/x.md",
                        "chmod +a 'admin allow write' docs/x.md", "chmod =a# 1 'admin allow write' docs/x.md", "chmod -a# 1 docs/x.md",
                        "chmod -N docs/x.md", "chown -R nobody:staff docs", "chown :staff docs/x.md", "chgrp -h staff docs/x.md",
                        "chflags -R nouchg docs", "chmod --reference=tests/b.txt docs/x.md"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables")
        for command in ("chmod 755 tests/b.txt", "chmod +a 'admin allow write' tests/b.txt", "chmod -a# 1 tests/b.txt", "chown nobody tests/b.txt"):
            with self.subTest(command):
                self.assertSilent(command)

    def test_the_options_end_where_this_macs_getopt_ends_them(self):
        for command, path in (("rm -- docs/x.md", "docs/x.md"), ("cp -- tests/src.txt docs/x.md", "docs/x.md"),
                              ("mkdir -- docs/new", "docs/new"), ("touch -- -x", "-x"),  # -x at the home's root
                              ("touch tests/b.txt -c", "-c"),  # BSD: an option after an operand is a file (probed: ./-c)
                              ("install -d tests/out/d -m 700", "-m"),  # probed: made ./-m and ./700
                              ("rm -f -- tests/b.txt docs/x.md", "docs/x.md")):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables")
                self.assertIn(path, r.reason)
        for command in ("rm -rf -- tests/out", "mkdir -m 700 -p tests/out/d", "touch -r docs/x.md tests/b.txt", "touch -t 202609170000 tests/b.txt",
                        "truncate -r docs/x.md tests/b.txt", "install -m 755 -o nobody tests/src.txt tests/out/x"):
            with self.subTest(command):
                self.assertSilent(command)

    def test_install_writes_its_metalog_and_backup_and_install_d_every_operand(self):
        for command, path in (("install -M docs/log tests/src.txt tests/out/x", "docs/log"),
                              ("install -d tests/out/a docs/b", "docs/b"),
                              ("install -b tests/src.txt bin/spud", "bin/spud.old"),
                              ("install -b -B .orig tests/src.txt bin/spud", "bin/spud.orig")):
            with self.subTest(command):
                r = self.assertRefused(command, "deliverables")
                self.assertIn(path, r.reason)
        self.assertSilent("install -b tests/src.txt tests/out/x")

    def test_behind_every_wrapper_and_wherever_tee_is_read(self):
        for command in ("env cp tests/src.txt docs/x.md", "command cp tests/src.txt docs/x.md", "sudo cp tests/src.txt docs/x.md",
                        "sudo -u root rm docs/x.md", "env A=1 touch docs/x.md", "ENV touch docs/x.md", "nohup rm docs/x.md &",
                        "time mkdir docs/new", "nice -n 5 chmod +x docs/x.md", "xargs rm docs/x.md < /dev/null", "exec touch docs/x.md",
                        "timeout 5 sed -i '' s/a/b/ docs/x.md", "command -p mv docs/x.md tests/out/", "noglob rm docs/x.md",
                        "echo x | sed -i '' s/a/b/ docs/x.md", "true && ln -s x docs/y", "false || touch docs/x.md",
                        "(cd tests && cp src.txt ../docs/x.md)", "{ touch docs/x.md; }", "eval 'rm docs/x.md'", "sh -c 'mv tests/src.txt docs/'",
                        "bash -lc 'touch docs/x.md'", "f() { touch docs/x.md; }; f", "echo $(touch docs/x.md)", "bash <<'EOF'\nrm docs/x.md\nEOF",
                        "if true; then mkdir docs/new; fi", "while false; do rm docs/x.md; done", "cd docs && touch x.md", "cd docs; rm -rf ."):
            with self.subTest(command):
                self.assertRefused(command, "deliverables")

    def test_a_glob_command_word_is_read_as_each_command_it_can_be(self):
        (self.home.path / "cp").write_text("", encoding="utf-8")  # so `c?` matches a file here, as zsh would expand it
        for command in ("c? tests/src.txt docs/x.md", "/bin/c? tests/src.txt docs/x.md", "/bin/[c]p tests/src.txt docs/x.md"):
            with self.subTest(command):
                r = self.bash(command)
                self.assertEqual((r.code, r.decision), (0, "deny"), (command, r))

    def test_a_target_the_hook_cannot_resolve_is_refused_for_a_member_alone(self):
        for command in ("cp tests/src.txt $D", "rm \"$F\"", "touch $(date).log", "mkdir -p \"$D\"/x", "sed -i '' s/a/b/ $F",
                        "for f in a b; do touch tests/$f; done", "ln -s /tmp/x `pwd`/x", "cp -t \"$D\" tests/src.txt",
                        "cp \"$SRC\" tests/out/", "mv \"$SRC\" tests/out/"):  # the name a source takes inside a directory
            with self.subTest(command):
                self.assertRefused(command, VARIABLE_WORDING)
                self.assertSilent(command, agent_id=None)  # Spud: the hook cannot read it, as for a redirection
        for command in ("cp \"$SRC\" tests/out.txt", "cp \"$D\"/src.txt tests/out/", "sed -n \"$N\"p docs/x.md", "cat \"$F\""):
            with self.subTest(command):
                self.assertSilent(command)
        # The line's own assignment is resolved, as it is for every other word the hook reads by name: a member writes into
        # its scratchpad through a variable it set on the line, and the hook reads the path it set.
        self.assertSilent("F=tests/b.txt; rm \"$F\"")
        self.assertSilent("S=/tmp/spd-121-x; mkdir -p $S/y && rm -rf $S")
        r = self.assertRefused("S=docs; touch $S/x.md", "deliverables")
        self.assertIn("docs/x.md", r.reason)
        self.assertRefused("D=tests/fake/.git/hooks; cp tests/src.txt $D/post-index-change", GIT_DIR_WORDING)
        # A word where this Mac's getopt still reads an option, holding an expansion, is read by name (SPD-043): for sed and
        # install, whose options change what they write, `sed $X` may be `sed -i`.
        self.assertRefused("X=-i; sed $X '' s/a/b/ docs/x.md", "deliverables")  # resolved, and then in place
        for command in ("sed -$X '' s/a/b/ docs/x.md", "cp -$X tests/src.txt docs/"):
            with self.subTest(command):
                self.assertRefused(command, WORD_WORDING)  # a word spelled with `-` is an option the hook must read
        # A first operand the line cannot settle is read both ways instead, and each reading's files are checked: what a
        # `sed -n "${n},$((n+3))p" f` only prints stays silent, while the files an in-place reading would write do not.
        for command in ("sed $(printf -- -i) '' s/a/b/ docs/x.md", "sed \"$X\" -e p tests/b.txt docs/x.md",
                        "install $(echo -d) tests/out/a docs/b"):
            with self.subTest(command):
                self.assertRefused(command, "deliverables")
        for command in ("sed -n \"$N\"p docs/x.md", "sed -n \"${A},$((A+3))p\" docs/x.md", "for n in 1 2; do sed -n \"${n}p\" docs/x.md; done"):
            with self.subTest(command):
                self.assertSilent(command)

    def test_a_glob_target_is_read_as_a_redirections(self):
        (self.home.path / "tests" / "fake" / ".git" / "hooks" / "pre-commit").write_text("", encoding="utf-8")
        self.assertRefused("rm tests/fake/.git/hooks/*", GIT_DIR_WORDING)
        self.assertRefused("rm tests/fake/.gi?/hooks/pre-commit", GIT_DIR_WORDING)
        self.assertRefused("rm docs/*.md", "deliverables")
        self.assertRefused("rm tests/*.nomatch", "matches no file now")  # a member: what the shell would open is unknown
        self.assertSilent("rm /tmp/spd-121-none/*.nomatch", agent_id=None)  # Spud: the literal name is checked, outside every project
        self.assertSilent("rm tests/*.txt")
        self.assertSilent("rm -f tests/{src,b}.txt")

    def test_spuds_answers(self):
        """Spud is held to Law 1 in a project and is free in his own files and outside every project, as for a redirection."""
        for label, form in ARG_WRITERS:
            with self.subTest(label):
                r = self.assertRefused(form.format("docs/x.md"), "Law 1", agent_id=None)
                self.assertIn(ARG_WORDING, r.reason)
                self.assertRefused(form.format("ledger/tickets/SPD-001.md"), "generated", agent_id=None)
                self.assertSilent(form.format("/Users/nobody/.zshrc"), agent_id=None)
                self.assertSilent(form.format("/tmp/spd-121-x"), agent_id=None)
                self.assertSilent(form.format(".claude/settings.json"), agent_id=None)  # his own, the backup under .claude/** too
        r = self.assertRefused("cp tests/src.txt tests/fake/.git/hooks/post-index-change", "Law 1", agent_id=None)
        self.assertNotIn(GIT_DIR_WORDING, r.reason)  # SPD-066's rule is a caller's with an agent_id

    def test_an_unbound_agent_id_is_held_as_for_a_redirection(self):
        self.assertRefused("cp tests/src.txt /Users/nobody/.zshrc", OUTSIDE, agent_id=AGENT_D)
        self.assertRefused("touch tests/fake/.git/hooks/x", GIT_DIR_WORDING, agent_id=AGENT_D)
        self.assertRefused("touch tests/x.py", "not bound", agent_id=AGENT_D)
        self.assertSilent("touch %s/probe.txt" % self.scratchpad, agent_id=AGENT_D)


# =============================================================================
# PreToolUse / Write|Edit|MultiEdit|NotebookEdit
# =============================================================================


class PathRuleAsserts:
    """A Write (or another edit tool) to a path, and what the path rule answers."""

    def edit(self, path, agent_id=AGENT_A, tool="Write"):
        return self.home.hook("PreToolUse", self.pre_edit(path, agent_id=agent_id, tool=tool))

    def assertRefused(self, path, needle, agent_id=AGENT_A, tool="Write"):
        r = self.edit(path, agent_id, tool)
        self.assertEqual((r.code, r.decision), (0, "deny"), (str(path), r))
        self.assertIn(needle, r.reason, (str(path), r.reason))
        return r

    def assertSilent(self, path, agent_id=AGENT_A, tool="Write"):
        r = self.edit(path, agent_id, tool)
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""), (str(path), r))


FIRMLINK = "/System/Volumes/Data"  # macOS: the Data volume's own mount path; /Users, /private ... are firmlinks into it


def same_directory(a, b):
    try:
        return os.path.samefile(str(a), str(b))
    except OSError:
        return False


def mixed_case(text):
    return "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(text))


class PathAliasAsserts(PathRuleAsserts):
    """SPD-029: a project root (the home, a worktree) spelled another way the filesystem honours is the same root, so the
    path rule answers there as it does at the root: through the edit tools and through a shell redirection, for a member
    whose deliverables are tests/** and bin/spud (self.lead, AGENT_A) and for Spud."""

    def alias_or_skip(self, root, spelled, what):
        if spelled == str(root) or not same_directory(root, spelled):
            self.skipTest("%s: this filesystem does not treat %s as the directory %s" % (what, spelled, root))
        return spelled

    def assertBashRefused(self, command, needle, agent_id=AGENT_A):
        r = self.home.hook("PreToolUse", self.pre_bash(command, agent_id=agent_id))
        self.assertEqual((r.code, r.decision), (0, "deny"), (command, r))
        self.assertIn(needle, r.reason, (command, r.reason))

    def assertBashSilent(self, command, agent_id=AGENT_A):
        r = self.home.hook("PreToolUse", self.pre_bash(command, agent_id=agent_id))
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""), (command, r))

    def assertRootHolds(self, spelled):
        a = str(spelled)
        self.assertRefused("%s/ledger/tickets/SPD-001.md" % a, "generated")
        self.assertRefused("%s/ledger/tickets/SPD-001.md" % a, "generated", agent_id=None)
        self.assertRefused("%s/CLAUDE.md" % a, "deliverables")
        self.assertRefused("%s/bin/spud" % a, "Law 1", agent_id=None)
        self.assertSilent("%s/tests/x.py" % a)
        self.assertSilent("%s/CLAUDE.md" % a, agent_id=None)
        self.assertBashRefused("echo x > %s/ledger/tickets/SPD-001.md" % a, "generated")
        self.assertBashRefused("printf x | tee %s/reports/2026-09-13.md" % a, "generated", agent_id=None)
        self.assertBashRefused("cd %s && echo x > ledger/x.md" % a, "generated")
        self.assertBashRefused("echo x > %s/bin/spud" % a, "Law 1", agent_id=None)
        self.assertBashSilent("echo x > %s/tests/out.txt" % a)


STATE = ".spud"  # the ledger state directory at a project root (SPD-031); the Bash hook refuses a command naming it, so its paths are built here
DB_WORDING = "spud sql --readonly"  # the Bash hook's database refusal, which the edit hook gives for the state directory too


def quote_split(path):
    """A shell spelling of `path` that the Bash hook's raw-text database regex misses and its shell analysis resolves: the state
    directory and the database file names split by adjacent quotes (."spud", ledger."db"), so a refusal comes from the path rule."""
    parts = []
    for part in str(path).split("/"):
        if part.casefold() == STATE or part.casefold().startswith("ledger.db"):
            i = part.index(".")
            part = '%s."%s"' % (part[:i], part[i + 1:])
        parts.append(part)
    return "/".join(parts)


class StateDirAsserts(PathAliasAsserts):
    """SPD-031: a path in the state directory is refused to the edit tools and to a shell redirection for every actor, in the Bash
    hook's database wording (never Law 1's or Law 5's), whatever the deliverable globs."""

    def assertStateRefused(self, path, agent_id, tool="Write"):
        r = self.assertRefused(path, DB_WORDING, agent_id=agent_id, tool=tool)
        self.assertNotIn("Law", r.reason, (str(path), r.reason))
        return r

    def assertStateBashRefused(self, command, agent_id):
        r = self.home.hook("PreToolUse", self.pre_bash(command, agent_id=agent_id))
        self.assertEqual((r.code, r.decision), (0, "deny"), (command, r))
        self.assertIn(DB_WORDING, r.reason, (command, r.reason))
        self.assertIn("redirection or tee", r.reason, (command, r.reason))  # the path rule refused it, not the raw-text regex
        self.assertNotIn("Law", r.reason, (command, r.reason))
        return r

    def assertStateHolds(self, target, agents=(AGENT_A, None)):
        for agent_id in agents:
            self.assertStateRefused(target, agent_id)
            self.assertStateBashRefused("echo x > %s" % quote_split(target), agent_id)


class PreEditTest(PathRuleAsserts, HookCase):
    def setUp(self):
        super().setUp()
        self.lead = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:tests/**", "home:bin/spud", "home:docs/x/*.md", "home:notes/"]), AGENT_A)
        self.wt = self.home.path / ".claude" / "worktrees" / "spd-099-thing"
        self.wt.mkdir(parents=True)

    def test_member_paths_follow_the_deliverable_globs(self):
        home = self.home.path
        for ok in ("tests/test_x.py", "tests/probes/deep/x.jsonl", "bin/spud", "docs/x/a.md", "notes/a/b.txt"):
            for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                self.assertSilent(home / ok, tool=tool)
        for bad in ("bin/other", "docs/x/sub/a.md", "docs/y.md", "CLAUDE.md", "spud.config.json", ".claude/settings.json", "tests"):
            self.assertRefused(home / bad, "deliverables")
        # on a case-insensitive filesystem Tests/x.py is tests/x.py; on a case-sensitive one it is another file
        if case_insensitive_fs(home):
            self.assertSilent(home / "Tests" / "x.py")
        else:
            self.assertRefused(home / "Tests" / "x.py", "deliverables")

    def test_a_bracketed_segment_is_a_directory_the_hook_lets_its_member_write(self):
        """SPD-086, end to end: BAD-054/Snowden was refused admin/src/app/accounts/[email]/page.tsx -- its own planned
        deliverable, quoted back at it in the refusal -- because `[` opened a character class, so the glob matched only a
        one-letter directory.  The same plan and the same writes, through `member new` and the PreToolUse edit hook."""
        home = self.home.path
        m = self.spawn(self.plan(deliverable=["home:admin/src/app/accounts/[email]/**", "home:app/[...slug]/page.tsx"]), AGENT_B)
        self.assertEqual(m["deliverables"], ["home:admin/src/app/accounts/[email]/**", "home:app/[...slug]/page.tsx"])
        for ok in ("admin/src/app/accounts/[email]/page.tsx", "admin/src/app/accounts/[email]/_components/UserTab.tsx",
                   "app/[...slug]/page.tsx"):
            for tool in ("Write", "Edit", "MultiEdit"):
                self.assertSilent(home / ok, agent_id=AGENT_B, tool=tool)
        r = self.assertRefused(home / "admin" / "src" / "app" / "accounts" / "e" / "page.tsx", "deliverables", agent_id=AGENT_B)
        self.assertIn("[email]", r.reason)  # the class it used to be is the only thing that ever matched this path
        self.assertRefused(home / "admin" / "src" / "app" / "accounts" / "page.tsx", "deliverables", agent_id=AGENT_B)
        self.assertRefused(home / "app" / "x" / "page.tsx", "deliverables", agent_id=AGENT_B)
        self.assertRefused(home / "admin" / "src" / "app" / "accounts" / "[email]" / "page.tsx", "deliverables")  # not the lead's

    def test_case_variants_cannot_reach_generated_files_or_spuds_set(self):
        """Rooster's HIGH-2: the protected roots are matched whatever the case, on every filesystem."""
        home = self.home.path
        wide = self.spawn(self.plan(deliverable=["home:**"]), AGENT_B)
        for bad in ("Ledger/tickets/SPD-001.md", "LEDGER/x.md", "Reports/x.md", "Ledger/Home.md", "ledger/Tickets/SPD-001.md"):
            self.assertRefused(home / bad, "generated", agent_id=AGENT_B)
        self.assertSilent(home / "docs" / "x.md", agent_id=AGENT_B)
        self.assertSilent(home / "CLAUDE.md", agent_id=AGENT_B)  # a ** member may write CLAUDE.md: Law 1 binds Spud, not members
        if case_insensitive_fs(home):
            self.assertSilent(home / "Ledger" / "Home.md", agent_id=None)  # the same file as Spud's ledger/Home.md
            self.assertRefused(home / "Ledger" / "Tickets" / "x.md", "generated", agent_id=None)
        self.assertEqual(wide["deliverables"], ["home:**"])

    def test_every_edit_tool_is_covered_and_denials_are_recorded(self):
        home = self.home.path
        self.assertRefused(home / "bin" / "other", "deliverables")
        for tool in ("Edit", "MultiEdit", "NotebookEdit"):
            self.assertRefused(home / "CLAUDE.md", "deliverables", tool=tool)
        d = self.denied()
        self.assertEqual(d[0]["member"], self.lead["ref"])
        self.assertEqual(d[0]["data"]["tool_name"], "Write")
        self.assertEqual(d[0]["data"]["path"], "bin/other")

    def test_ledger_and_reports_are_generated(self):
        home = self.home.path
        for agent_id in (AGENT_A, None):
            for bad in ("ledger/tickets/SPD-001.md", "ledger/teams/SPUD-001/X.md", "reports/2026-09-12.md", "ledger/x.md"):
                self.assertRefused(home / bad, "generated", agent_id=agent_id)
        for ok in ("ledger/Home.md", "ledger/Spud.md", "ledger/Board.base", "ledger/Fleet.base", "ledger/_templates/ticket.md"):
            self.assertSilent(home / ok, agent_id=None)
            self.assertRefused(home / ok, "deliverables", agent_id=AGENT_A)

    def test_spuds_hand_written_set(self):
        home = self.home.path
        for ok in ("spud.config.json", "CLAUDE.md", ".claude/settings.json", ".claude/agents/spudagent.md", "docs/superpowers/specs/2026-09-12-x.md"):
            self.assertSilent(home / ok, agent_id=None)
        for bad in ("bin/spud", "tests/test_x.py", "docs/spikes/x.md", "README.md"):
            self.assertRefused(home / bad, "Law 1", agent_id=None)
        if case_insensitive_fs(home):
            self.assertSilent(home / "claude.md", agent_id=None)  # the same file as CLAUDE.md here
        else:
            self.assertRefused(home / "claude.md", "Law 1", agent_id=None)

    def test_outside_every_project_root_is_spuds_and_the_scratchpad_is_the_members(self):
        """SPD-064: a path outside every registered project was refused to nobody; only the scratchpad and the system temp
        directories stay open to a member now (OutsideProjectTest), and Spud writes out there as he always did."""
        for p in ("/tmp/claude-501/x/scratchpad/notes.md", str(self.home.path.parent / "elsewhere.md"),
                  str(self.home.path) + "-sibling/x.md"):  # the suite's homes live under tempfile, an open root
            self.assertSilent(p)
            self.assertSilent(p, agent_id=None)
        for p in ("/Users/eric/.claude/projects/-Users-eric-Personal-Spud/memory/x.md", "/etc/hosts"):
            self.assertSilent(p, agent_id=None)
            self.assertRefused(p, "outside every registered project")

    def test_worktree_paths_map_to_the_repository(self):
        self.assertSilent(self.wt / "tests" / "x.py")
        self.assertSilent(self.wt / "bin" / "spud")
        self.assertRefused(self.wt / "docs" / "spikes" / "x.md", "deliverables")
        self.assertRefused(self.wt / "ledger" / "tickets" / "SPD-001.md", "generated")
        self.assertRefused(self.wt / "bin" / "spud", "Law 1", agent_id=None)
        self.assertSilent(self.wt / ".claude" / "settings.json", agent_id=None)
        self.assertSilent(self.wt / "CLAUDE.md", agent_id=None)

    def test_dot_dot_and_symlinks_cannot_escape(self):
        home = self.home.path
        self.assertRefused(str(home / "tests" / ".." / "CLAUDE.md"), "deliverables")
        self.assertRefused(str(home / "tests" / ".." / ".." / (home.name) / "bin" / "other"), "deliverables")
        (home / "tests").mkdir(exist_ok=True)
        (home / "tests" / "link").symlink_to(home / "CLAUDE.md")
        self.assertRefused(home / "tests" / "link", "deliverables")
        (home / "tests" / "dirlink").symlink_to(home / "ledger")
        self.assertRefused(home / "tests" / "dirlink" / "Home.md", "generated")
        outside = home.parent / ("%s-link-%d" % (home.name, os.getpid()))
        outside.symlink_to(home / "ledger" / "tickets")
        self.addCleanup(outside.unlink)
        self.assertRefused(outside / "SPD-001.md", "generated")
        self.assertRefused(outside / "SPD-001.md", "generated", agent_id=None)
        (home / "tests" / "outlink").symlink_to("/tmp")
        self.assertSilent(home / "tests" / "outlink" / "x.txt")

    def test_relative_paths_resolve_against_cwd(self):
        p = self.pre_edit("CLAUDE.md", agent_id=AGENT_A)
        p["cwd"] = str(self.home.path)
        r = self.home.hook("PreToolUse", p)
        self.assertEqual(r.decision, "deny")
        p = self.pre_edit("tests/x.py", agent_id=AGENT_A)
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.stdout), (0, ""))

    def test_unbound_caller_and_missing_path(self):
        self.assertRefused(self.home.path / "tests" / "x.py", "not bound", agent_id=AGENT_D)
        p = self.pre_edit(self.home.path / "tests" / "x.py")
        p["tool_input"] = {"content": "x"}
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.decision), (0, "deny"))
        self.assertIn("file_path", r.reason)


class WorktreeElsewhereTest(StateDirAsserts, HookCase):
    """SPD-016: every worktree `git worktree list --porcelain` names for the home maps to repository-relative paths,
    wherever `git worktree add` put it, so the deliverable globs and the generated roots bind there too.  The home is
    a real repository here; the list is cached under .spud/ until a worktree is added, moved or removed, and a list
    that cannot be read fails the enforcing hook closed.

    SPD-097: the home of these tests is also project spud's root (SPUD_TOOL_DIR), the transition window's shape, and a
    worktree of it is that project's checkout.  Its generated roots are the home's (worktrees.home_roots: the vault is
    checked out in every worktree of the tool until `home move`), but its deliverable globs are project spud's, so the
    lead is planned with both scopes: `home:tests/**` binds at the home and the bare `tests/**`, the ticket's project,
    in the worktree.  test_a_home_glob_does_not_bind_in_a_worktree_of_the_tool pins that half."""

    def setUp(self):
        super().setUp()
        self.lead = self.spawn(self.plan(persona="engineer", model="opus",
                                         deliverable=["home:tests/**", "home:bin/spud", "tests/**", "bin/spud"]), AGENT_A)
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "root")
        self.elsewhere = self.add_worktree("elsewhere")

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        proc = subprocess.run(["git", "-C", str(self.home.path), "-c", "user.name=Spud", "-c", "user.email=spud@example.invalid", "-c", "commit.gpgsign=false", *args],
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc)
        return proc.stdout

    def add_worktree(self, name):
        path = self.home.path.parent / ("%s-%s" % (self.home.path.name, name))
        self.addCleanup(shutil.rmtree, path, True)
        self.git("worktree", "add", "-q", "-b", name, str(path))
        return path

    def test_a_worktree_outside_claude_worktrees_maps_to_the_repository(self):
        wt = self.elsewhere
        self.assertIn("worktree %s\n" % wt, self.git("worktree", "list", "--porcelain"))
        self.assertSilent(wt / "tests" / "x.py")
        self.assertSilent(wt / "bin" / "spud")
        self.assertRefused(wt / "CLAUDE.md", "deliverables")
        self.assertRefused(wt / "ledger" / "tickets" / "SPD-001.md", "generated")
        self.assertRefused(wt / "reports" / "2026-09-13.md", "generated", agent_id=None)
        self.assertRefused(wt / "bin" / "spud", "Law 1", agent_id=None)
        self.assertSilent(wt / "CLAUDE.md", agent_id=None)
        r = self.home.hook("PreToolUse", self.pre_bash("echo x > %s" % (wt / "ledger" / "x.md"), agent_id=AGENT_A))
        self.assertEqual((r.code, r.decision), (0, "deny"), r)
        self.assertIn("generated", r.reason)
        # a sibling directory that is no worktree stays outside every project root, and the home still maps
        self.assertSilent(self.home.path.parent / ("%s-elsewhere-not" % self.home.path.name) / "ledger" / "x.md")
        self.assertRefused(self.home.path / "ledger" / "x.md", "generated")

    def test_a_worktree_added_later_is_mapped_at_once(self):
        later = self.home.path.parent / ("%s-later" % self.home.path.name)
        self.assertRefused(self.elsewhere / "ledger" / "x.md", "generated")
        self.assertSilent(later / "ledger" / "x.md")
        self.add_worktree("later")
        self.assertRefused(later / "ledger" / "x.md", "generated")
        self.git("worktree", "remove", "--force", str(later))
        self.assertSilent(later / "ledger" / "x.md")

    def test_the_list_is_cached_until_the_worktrees_change(self):
        self.assertRefused(self.elsewhere / "ledger" / "x.md", "generated")
        path = self.home.env["PATH"]
        self.home.env["PATH"] = "/nonexistent"  # no git to run: the answer comes from the cache
        self.assertRefused(self.elsewhere / "ledger" / "x.md", "generated")
        self.home.env["PATH"] = path
        self.add_worktree("third")
        self.home.env["PATH"] = "/nonexistent"  # the worktrees changed and git cannot list them: fail closed
        r = self.edit(self.elsewhere / "tests" / "x.py")
        self.assertEqual((r.code, r.stdout), (2, ""), r)
        self.assertIn("failing closed", r.stderr)

    def test_a_list_git_cannot_give_fails_the_enforcing_hook_closed(self):
        (self.home.path / ".git" / "HEAD").write_text("garbage\n", encoding="utf-8")  # no longer a repository to git
        r = self.edit(self.home.path / "tests" / "x.py")
        self.assertEqual((r.code, r.stdout), (2, ""), r)
        self.assertIn("worktree", r.stderr)
        self.assertIn("failing closed", r.stderr)

    def test_a_worktree_elsewhere_in_upper_case(self):
        """SPD-029: git names the worktree by one spelling; a case variant of it is the same checkout."""
        self.assertRootHolds(self.alias_or_skip(self.elsewhere, str(self.elsewhere).upper(), "upper case"))

    def test_a_worktree_elsewhere_in_mixed_case(self):
        self.assertRootHolds(self.alias_or_skip(self.elsewhere, mixed_case(str(self.elsewhere)), "mixed case"))

    def test_a_worktree_elsewhere_under_the_data_volume_firmlink(self):
        self.assertRootHolds(self.alias_or_skip(self.elsewhere, FIRMLINK + str(self.elsewhere), "the %s firmlink prefix" % FIRMLINK))

    def test_a_linked_worktree_is_fingerprinted_like_the_main_checkout(self):
        """SPD-097: a linked worktree's `.git` is a gitfile, not a directory, so worktrees_fingerprint answered None there,
        checkout_worktrees never wrote its cache, and every hook run that mapped a path shelled out to `git worktree list`
        again -- +13.36 ms on PreToolUse(Write) in the timing probe, which runs the branch's own launcher from a worktree.
        The gitfile is resolved to the repository directory now, so a checkout that is a worktree caches like a root."""
        m = load_spud_module()
        home, wt = str(self.home.path), str(self.elsewhere)
        self.assertTrue(os.path.isfile(os.path.join(wt, ".git")), "a linked worktree's .git is a gitfile")
        self.assertEqual(os.path.realpath(m.repository_dir(wt)), os.path.realpath(os.path.join(home, ".git")))
        fingerprint = m.worktrees_fingerprint(wt)
        self.assertIsNotNone(fingerprint)
        self.assertEqual(fingerprint, m.worktrees_fingerprint(home))
        self.add_worktree("third")  # what the fingerprint exists to notice, from either checkout
        self.assertNotEqual(m.worktrees_fingerprint(wt), fingerprint)
        self.assertEqual(m.worktrees_fingerprint(wt), m.worktrees_fingerprint(home))
        self.assertIsNone(m.repository_dir(self.home.path.parent / "no-such-checkout"))
        self.assertIsNone(m.worktrees_fingerprint(self.home.path.parent / "no-such-checkout"))

    def test_a_home_glob_does_not_bind_in_a_worktree_of_the_tool(self):
        """SPD-097: a worktree elsewhere is project spud's checkout, so a member whose deliverables name the home is refused
        there and is free at the home; the generated roots hold in both, because in the window the vault is checked out in
        every worktree of the tool.  The reverse for a bare glob, which is the ticket's project's."""
        self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_B)
        self.assertRefused(self.elsewhere / "tests" / "x.py", "deliverables", agent_id=AGENT_B)
        self.assertRefused(self.elsewhere / "ledger" / "x.md", "generated", agent_id=AGENT_B)
        self.assertSilent(self.home.path / "tests" / "x.py", agent_id=AGENT_B)
        self.assertRefused(self.home.path / "ledger" / "x.md", "generated", agent_id=AGENT_B)
        self.spawn(self.plan(persona="engineer", model="opus", deliverable=["**"], cwd=self.elsewhere), AGENT_C)  # SPD-098: binds SPD-001 there
        self.assertSilent(self.elsewhere / "tests" / "x.py", agent_id=AGENT_C)
        self.assertRefused(self.elsewhere / "ledger" / "x.md", "generated", agent_id=AGENT_C)
        self.assertRefused(self.home.path / "tests" / "x.py", "deliverables", agent_id=AGENT_C)

    def test_the_state_directory_holds_at_the_home_and_at_a_worktree_elsewhere(self):
        """SPD-031: the worktree cache this class exercises, which the hook itself writes, and a worktree elsewhere's own state
        directory, refused to a member whose glob is ** and to Spud.  Both scopes since SPD-097, so the globs really do reach
        every path the state directory is refused at: `home:**` at the home, the bare `**` in project spud's worktree."""
        self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**", "**"], cwd=self.elsewhere), AGENT_B)  # SPD-098: binds SPD-001 there
        self.assertSilent(self.elsewhere / "tests" / "x.py", agent_id=AGENT_B)  # lists the worktrees, writing the cache
        cache = self.home.path / STATE / "worktrees" / "spud.json"  # one cache per project since SPD-014
        self.assertTrue(cache.is_file())
        agents = (AGENT_B, None)
        self.assertStateHolds(cache, agents)
        self.assertStateHolds(self.elsewhere / STATE / "ledger.db", agents)
        self.assertStateHolds(self.elsewhere / STATE / "pycache" / "x.pyc", agents)
        for what, spelled in (("upper case", str(self.elsewhere).upper()), ("the %s firmlink prefix" % FIRMLINK, FIRMLINK + str(self.elsewhere))):
            with self.subTest(what):
                self.assertStateHolds(self.alias_or_skip(self.elsewhere, spelled, what) + "/" + STATE + "/worktrees.json", agents)


class PathAliasTest(PathAliasAsserts, HookCase):
    """SPD-029 (proposal 24): the path rule found a root by comparing spellings, so on macOS a target spelled with a case
    variant of the home, under the /System/Volumes/Data firmlink, or with a component the filesystem folds, counted as
    outside every project root and Laws 1 and 5 said nothing.  A root is found by file identity now."""

    def setUp(self):
        super().setUp()
        self.lead = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:tests/**", "home:bin/spud"]), AGENT_A)
        self.wt = self.home.path / ".claude" / "worktrees" / "spd-099-thing"
        self.wt.mkdir(parents=True)

    def test_the_exact_spellings_hold(self):
        self.assertRootHolds(self.home.path)
        self.assertRootHolds(self.wt)

    def test_the_home_in_upper_case(self):
        self.assertRootHolds(self.alias_or_skip(self.home.path, str(self.home.path).upper(), "upper case"))

    def test_the_home_in_mixed_case(self):
        self.assertRootHolds(self.alias_or_skip(self.home.path, mixed_case(str(self.home.path)), "mixed case"))

    def test_the_home_under_the_data_volume_firmlink(self):
        self.assertRootHolds(self.alias_or_skip(self.home.path, FIRMLINK + str(self.home.path), "the %s firmlink prefix" % FIRMLINK))

    def test_a_claude_worktree_in_upper_and_mixed_case(self):
        home, wt = str(self.home.path), str(self.wt)
        for what, spelled in (("upper case", wt.upper()), ("mixed case", mixed_case(wt)),
                              ("upper-case .claude/worktrees", home + "/.CLAUDE/WORKTREES/spd-099-thing"),
                              ("Kelvin sign in worktrees", home + "/.claude/worKtrees/spd-099-thing")):
            with self.subTest(what):
                self.assertRootHolds(self.alias_or_skip(wt, spelled, what))

    def test_a_claude_worktree_under_the_data_volume_firmlink(self):
        self.assertRootHolds(self.alias_or_skip(self.wt, FIRMLINK + str(self.wt), "the %s firmlink prefix" % FIRMLINK))

    def test_a_generated_root_spelled_with_a_simple_case_fold(self):
        """APFS folds U+017F (long s) to s, as Unicode simple case folding does; str.lower() does not."""
        self.spawn(self.plan(deliverable=["home:**"]), AGENT_B)
        spelled = str(self.home.path / "reportſ" / "2026-09-13.md")
        self.assertRefused(spelled, "generated", agent_id=AGENT_B)
        self.assertBashRefused("echo x > %s" % spelled, "generated", agent_id=AGENT_B)
        self.assertSilent(self.home.path / "docs" / "x.md", agent_id=AGENT_B)

    def test_dot_dot_after_a_symlink_is_resolved_as_the_filesystem_does(self):
        """tests/sub/.. is the parent of the link's target, not tests: the kernel resolves the link first."""
        home = self.home.path
        (home / "ledger" / "tickets").mkdir(parents=True, exist_ok=True)
        (home / "tests").mkdir(exist_ok=True)
        (home / "tests" / "sub").symlink_to(home / "ledger" / "tickets")
        spelled = "%s/tests/sub/../SPD-001.md" % home
        self.assertRefused(spelled, "generated")
        self.assertBashRefused("echo x > %s" % spelled, "generated")
        self.assertSilent(home / "tests" / "sub2" / ".." / "x.py")


class NonAsciiHomeTest(PathAliasAsserts, HookCase):
    """SPD-029: APFS is normalization-insensitive, so the NFD spelling of a home named in NFC is the same directory."""

    home_name = "Spüd"

    def setUp(self):
        super().setUp()
        self.lead = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:tests/**", "home:bin/spud"]), AGENT_A)

    def test_the_nfc_home_holds(self):
        self.assertTrue(unicodedata.is_normalized("NFC", str(self.home.path)))
        self.assertRootHolds(self.home.path)

    def test_the_home_spelled_nfd(self):
        nfd = unicodedata.normalize("NFD", str(self.home.path))
        self.assertNotEqual(nfd, str(self.home.path))
        self.assertRootHolds(self.alias_or_skip(self.home.path, nfd, "NFD normalization"))


class StateDirTest(StateDirAsserts, HookCase):
    """SPD-031 (proposal 27): the ledger state directory at a project root holds the database, its WAL and shm files, the worktree
    list cache, the backups and the launcher's cached bytecode, which every hook run loads.  The Bash hook refused a command
    naming it, but the edit hook checked a path there only against Law 1 and the deliverable globs, so a member whose globs
    reached it could Write the database.  Nothing but the CLI writes there now, for any actor."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_A)
        self.named = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:" + STATE + "/**", "home:" + STATE + "/ledger.db"]), AGENT_B)
        self.state = self.home.path / STATE
        self.pyc = sorted((self.state / "pycache").rglob("*.pyc"))
        self.targets = {
            "database": self.state / "ledger.db",
            "WAL": self.state / "ledger.db-wal",
            "worktree cache": self.state / "worktrees.json",
            "cached bytecode": self.pyc[0] if self.pyc else self.state / "pycache" / "spud_ledger.cpython-314.pyc",
            "backup": self.state / "backups" / "ledger-2026-09-13.db",
            "the directory itself": self.state,
        }

    def test_every_edit_tool_is_refused_for_everyone_whatever_the_globs(self):
        self.assertEqual((self.wide["deliverables"], self.named["deliverables"]), (["home:**"], ["home:" + STATE + "/**", "home:" + STATE + "/ledger.db"]))
        self.assertTrue(self.targets["database"].is_file())
        self.assertTrue(self.pyc, "the launcher caches its bytecode under the state directory")
        for what, target in self.targets.items():
            for agent_id in (AGENT_A, AGENT_B, None):
                for tool in ("Write", "Edit"):
                    with self.subTest(what=what, agent_id=agent_id, tool=tool):
                        self.assertStateRefused(target, agent_id, tool)
        for tool in ("MultiEdit", "NotebookEdit"):
            self.assertStateRefused(self.targets["database"], AGENT_A, tool)
        self.assertStateRefused(self.targets["database"], AGENT_D)  # an unbound caller: refused in the same words
        self.assertIn(STATE + "/ledger.db", [e["data"].get("path") for e in self.denied()])

    def test_a_shell_redirection_is_refused_for_everyone(self):
        home = self.home.path
        for what in ("database", "worktree cache", "cached bytecode"):
            for agent_id in (AGENT_A, AGENT_B, None):
                with self.subTest(what=what, agent_id=agent_id):
                    self.assertStateBashRefused("echo x > %s" % quote_split(self.targets[what]), agent_id)
        for agent_id in (AGENT_A, AGENT_B, None):
            with self.subTest("tee and relative after cd", agent_id=agent_id):
                self.assertStateBashRefused("printf x | tee -a %s" % quote_split(self.targets["worktree cache"]), agent_id)
                self.assertStateBashRefused("cd %s && echo x > .\"spud\"/pycache/x.pyc" % home, agent_id)
                self.assertStateBashRefused("cd %s && echo x >> .'spud'/ledger.'db'-wal" % home, agent_id)

    def test_aliases_of_the_state_directory(self):
        home, state = str(self.home.path), str(self.state)
        for what, spelled in (("upper case", home + "/.SPUD"), ("mixed case", home + "/.SpUd"), ("long s (U+017F)", home + "/.ſpud"),
                              ("upper-case home", state.upper()), ("mixed-case home", mixed_case(home) + "/" + STATE),
                              ("the %s firmlink prefix" % FIRMLINK, FIRMLINK + state)):
            with self.subTest(what):
                spelled = self.alias_or_skip(state, spelled, what)
                for name in ("ledger.db", "worktrees.json"):
                    self.assertStateHolds(spelled + "/" + name, (AGENT_A, AGENT_B, None))

    def test_symlinks_and_dot_dot_into_the_state_directory(self):
        home = self.home.path
        (home / "tests").mkdir(exist_ok=True)
        (home / "tests" / "state").symlink_to(self.state)
        (home / "tests" / "db").symlink_to(self.targets["database"])
        (home / "tests" / "sub").symlink_to(self.state / "pycache")
        outside = home.parent / ("%s-state-%d" % (home.name, os.getpid()))
        outside.symlink_to(self.state)
        self.addCleanup(outside.unlink)
        for what, spelled in (("a symlink in the repository", home / "tests" / "state" / "ledger.db"),
                              ("a symlink to the database file", home / "tests" / "db"),
                              ("a symlink outside every root", outside / "worktrees.json"),
                              (".. after a symlink", "%s/tests/sub/../ledger.db" % home)):
            with self.subTest(what):
                self.assertStateHolds(spelled, (AGENT_A, None))

    def test_the_state_directory_of_a_claude_worktree_root(self):
        """Refused at every project root, not only the home's: a worktree's own bin/spud run without SPUD_HOME takes the worktree
        as its home and keeps its database and cached bytecode in that root's state directory (bin/spud); only the CLI writes one."""
        wt = self.home.path / ".claude" / "worktrees" / "spd-099-thing"
        (wt / STATE).mkdir(parents=True)
        for name in ("ledger.db", "worktrees.json", "pycache/x.pyc"):
            self.assertStateHolds(wt / STATE / name, (AGENT_A, AGENT_B, None))
        with self.subTest("upper case"):
            self.assertStateHolds(self.alias_or_skip(wt, str(wt).upper(), "upper case") + "/" + STATE + "/ledger.db")

    def test_names_like_the_state_directory_stay_under_the_globs(self):
        """Controls.  Only the first component below a project root is the state directory: a nested one (tests/fixtures/.spud) is no
        ledger's state, since bin/spud keeps its state at the root of its home, so it stays under the deliverable globs like any
        similar name.  (The Bash hook's raw-text regex still refuses a command that spells a nested one plainly.)"""
        home = self.home.path
        nested = (home / "tests" / "fixtures" / STATE / "ledger.db", home / "docs" / STATE / "worktrees.json")
        similar = (home / (STATE + "rc"), home / (STATE + "-notes") / "x.md", home / "x.spud", home / "docs" / "spud" / "x.md", home / "spud" / "ledger.db")
        for p in nested + similar:
            with self.subTest(str(p)):
                self.assertSilent(p, agent_id=AGENT_A)
                self.assertRefused(p, "Law 1", agent_id=None)
        self.assertRefused(nested[0], "deliverables", agent_id=AGENT_B)
        self.assertBashSilent("echo x > %s" % quote_split(nested[0]), agent_id=AGENT_A)
        self.assertBashSilent("echo x > %s" % (home / (STATE + "rc")), agent_id=AGENT_A)
        self.assertBashRefused("echo x > %s" % quote_split(nested[0]), "Law 1", agent_id=None)
        self.assertBashSilent("echo x > %s" % (home / "tests" / "out.txt"), agent_id=AGENT_A)


# The refusal a caller with an agent_id gets for a path outside every registered project (SPD-064).
OUTSIDE = "outside every registered project"


class OutsideProjectTest(PathAliasAsserts, HookCase):
    """SPD-064: edit_reason resolved a target against the registered projects and, when it lay inside none of them, returned
    no reason at all (`inside = project_paths(...); if not inside: return None, None`), so the path rule that holds a member
    to its deliverable globs stopped at project boundaries: a bound member could Write, Edit or redirect into ~/.gitconfig,
    ~/.claude/settings.json and ~/.claude/agents/, the shell rc files, ~/.ssh, a LaunchAgent, or anything else in Eric's
    home.  Spud's design decision is the allowlist: a caller with an agent_id in a Spud session may write outside every
    registered project only under the harness's scratchpad root for this user (/private/tmp/claude-<uid>/) and the system
    temp directories, where members run probes and differential harnesses; everything else outside a project is refused,
    fail closed, with the path named.  Both readings of the target must land under an allowed root, so a symlink planted in
    the scratchpad that points at ~/.gitconfig is refused while /tmp and /private/tmp, which resolve to each other, stay
    open.  Unchanged: Spud himself, who keeps writing his own files and his memory directory under ~/.claude/projects/;
    a plain session and its unbound subagents (tests/test_hooks_projects.py); the harness_file and state-directory refusals,
    which run before the project lookup.

    The suite's homes and registered projects live under tempfile, which the allowlist opens, so every refusal here names a
    path under a HOME that does not exist (/Users/nobody) or a system directory outside every temp root."""

    def setUp(self):
        super().setUp()
        self.lead = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:tests/**", "home:bin/spud"]), AGENT_A)
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["home:**"]), AGENT_C)
        self.fake_home = "/Users/nobody"  # a HOME outside every project and every temp root; nothing here is created
        self.home.env["HOME"] = self.fake_home
        self.scratchpad = "/private/tmp/claude-%d/-Users-eric-Personal-Spud/%s/scratchpad" % (os.getuid(), SESSION)
        self.tmp = Path(tempfile.mkdtemp(prefix="spud-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    # The ticket's own list, plus the two files `project sync --all` writes under ~/.claude.  ~/.gitconfig and
    # $XDG_CONFIG_HOME/git/config are here too, but they earn SPD-063's Law 7 reason first (GitConfigFileTest).
    def sensitive(self):
        return [self.fake_home + p for p in ("/.zshrc", "/.bashrc", "/.profile", "/.ssh/config", "/.ssh/authorized_keys",
                                             "/.claude/settings.json", "/.claude/agents/x.md")] \
            + ["/Library/LaunchAgents/x.plist", "/etc/hosts"]

    def open_paths(self):
        return [self.scratchpad + "/notes.md", str(self.tmp / "probe.py"), "/tmp/x", "/private/tmp/x",
                "/private/tmp/claude-%d/x" % os.getuid(), str(self.tmp.parent / "sibling.txt")]

    def test_a_member_may_not_write_outside_every_project(self):
        for p in self.sensitive():
            with self.subTest(p):
                for agent_id in (AGENT_A, AGENT_C):
                    r = self.assertRefused(p, OUTSIDE, agent_id=agent_id)
                    self.assertIn(p, r.reason)  # the reason names the path
        self.assertEqual(self.wide["deliverables"], ["home:**"])  # not even a ** member: outside a project there is no glob

    def test_the_git_config_files_outside_every_project_are_refused_too(self):
        for p in (self.fake_home + "/.gitconfig", self.fake_home + "/.config/git/config"):
            with self.subTest(p):
                r = self.assertRefused(p, p)  # SPD-063's Law 7 reason comes first; both name the path
                self.assertIn("Law 7", r.reason)
                self.assertSilent(p, agent_id=None)

    def test_every_edit_tool_is_covered_and_the_denial_is_recorded(self):
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            with self.subTest(tool):
                self.assertRefused(self.fake_home + "/.zshrc", OUTSIDE, tool=tool)
        self.assertIn(self.fake_home + "/.zshrc", [e["data"].get("path") or "" for e in self.denied()])

    def test_a_redirection_or_tee_outside_every_project(self):
        for p in self.sensitive():
            for command in ("echo x > %s", "echo x >> %s", "printf x | tee %s", "printf x | tee -a %s"):
                with self.subTest(command % p):
                    self.assertBashRefused(command % p, OUTSIDE)
                    self.assertBashSilent(command % p, agent_id=None)  # Spud writes his own files

    def test_a_tilde_spelling_is_expanded_and_a_variable_one_is_refused_as_unresolvable(self):
        self.assertBashRefused("echo x > ~/.zshrc", OUTSIDE)
        self.assertRefused("~/.zshrc", OUTSIDE)
        # A variable the line did not assign stays SPD-043's unresolvable-target refusal, not this one.
        r = self.home.hook("PreToolUse", self.pre_bash("echo x > $HOME/.zshrc", agent_id=AGENT_A))
        self.assertEqual((r.code, r.decision), (0, "deny"), r)
        self.assertIn("spell the path out", r.reason)
        self.assertNotIn(OUTSIDE, r.reason)
        self.assertBashSilent("echo x > $HOME/.zshrc", agent_id=None)

    def test_the_scratchpad_and_the_system_temp_directories_stay_open(self):
        for p in self.open_paths():
            with self.subTest(p):
                for agent_id in (AGENT_A, AGENT_C, None):
                    self.assertSilent(p, agent_id=agent_id)
                self.assertBashSilent("echo x > %s" % p)
                self.assertBashSilent("printf x | tee %s" % p)

    def test_a_symlink_under_a_temp_root_that_resolves_outside_is_refused(self):
        link = self.tmp / "link"
        link.symlink_to(self.fake_home + "/.zshrc")  # the target need not exist: the check is on the path
        deep = self.tmp / "dir"
        deep.symlink_to(self.fake_home + "/.claude")
        for p in (link, deep / "settings.json"):
            with self.subTest(str(p)):
                self.assertRefused(p, OUTSIDE)
                self.assertBashRefused("echo x > %s" % p, OUTSIDE)
        self.assertSilent(self.tmp / "real.txt")  # the control: a real file beside the links

    def test_a_symlink_inside_the_globs_cannot_reach_outside_either(self):
        """Every reading of the target is accounted for: inside a project (the globs decide) or under an allowed root."""
        tests = self.home.path / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "escape").symlink_to(self.fake_home + "/.ssh")
        self.assertRefused(tests / "escape" / "config", OUTSIDE)
        self.assertBashRefused("echo x > %s/escape/config" % tests, OUTSIDE)
        (tests / "outlink").symlink_to("/tmp")  # a link to an allowed root stays open, as it was before
        self.assertSilent(tests / "outlink" / "x.txt")

    def test_spud_keeps_his_own_files_and_his_memory_directory(self):
        for p in self.sensitive() + [self.fake_home + "/.claude/projects/-Users-eric-Personal-Spud/memory/MEMORY.md"]:
            with self.subTest(p):
                self.assertSilent(p, agent_id=None)

    def test_the_harness_and_state_directory_refusals_still_come_first(self):
        harness = "/private/tmp/claude-%d/-Users-eric-Personal-Spud/%s/subagents/agent-%s.meta.json" % (os.getuid(), SESSION, AGENT_A)
        r = self.assertRefused(harness, "harness")
        self.assertNotIn(OUTSIDE, r.reason)
        r = self.assertRefused(self.home.path / STATE / "ledger.db", DB_WORDING)
        self.assertNotIn(OUTSIDE, r.reason)

    def test_the_reason_names_the_path_and_the_roots_that_stay_open(self):
        r = self.assertRefused(self.fake_home + "/.claude/settings.json", OUTSIDE)
        self.assertIn("scratchpad", r.reason)
        self.assertIn("deliverables", r.reason)
        self.assertIn("/private/tmp/claude-%d" % os.getuid(), r.reason)

    def test_an_unbound_agent_id_keeps_its_scratchpad_and_nothing_else(self):
        """A foreground child before its first tool call writes probes in its scratchpad; it has no globs, so nothing else."""
        self.assertSilent(self.scratchpad + "/probe.py", agent_id=AGENT_D)
        self.assertRefused(self.fake_home + "/.zshrc", OUTSIDE, agent_id=AGENT_D)
        self.assertRefused(self.home.path / "tests" / "x.py", "not bound", agent_id=AGENT_D)  # inside a project: unchanged


class DeliverableGlobTest(SpudTestCase):
    def test_member_new_validates_deliverables(self):
        t = self.new_ticket("Globs")
        for bad in ("../x", "/abs/path", "tests/../x", "", "  "):
            proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", "--deliverable", bad, actor="spud", check=False)
            self.assertEqual(proc.returncode, EXIT_ERROR, bad)
            self.assertIn("deliverable", proc.stderr)
        m = self.new_member(t["key"], deliverable=["home:./tests/**", "home:docs/", "home:bin/spud"])
        self.assertEqual(m["deliverables"], ["home:tests/**", "home:docs/**", "home:bin/spud"])
        proc = self.home.run("member", "edit", m["ref"], "--deliverable", "../y", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)

    def test_glob_semantics(self):
        spud = load_spud_module()
        match = spud.path_matches_glob
        self.assertTrue(match("tests/a.py", "tests/**"))
        self.assertTrue(match("tests/a/b/c.py", "tests/**"))
        self.assertTrue(match("tests", "tests/**") is False)
        self.assertTrue(match("bin/spud", "bin/spud"))
        self.assertFalse(match("bin/spud2", "bin/spud"))
        self.assertTrue(match("docs/x/a.md", "docs/x/*.md"))
        self.assertFalse(match("docs/x/a/b.md", "docs/x/*.md"))
        self.assertTrue(match("a/b/c/d.md", "**/d.md"))
        self.assertTrue(match("d.md", "**/d.md"))
        self.assertTrue(match("a/x/b.md", "a/**/b.md"))
        self.assertTrue(match("a/b.md", "a/**/b.md"))
        self.assertTrue(match("ledger/Board.base", "ledger/*.base"))
        self.assertFalse(match("ledger/x/Board.base", "ledger/*.base"))
        # SPD-086 changed this case deliberately: brackets no longer open a character class, so `[?]` is a literal
        # bracket around the one-character wildcard, which is what a reader of the glob would take it for.
        self.assertFalse(match("a/b?c", "a/b[?]c"))
        self.assertTrue(match("a/b[x]c", "a/b[?]c"))
        self.assertFalse(match("Tests/a.py", "tests/**"))

    def test_brackets_are_literal(self):
        """SPD-086: `[` opened a character class, so a Next.js dynamic segment matched nothing and the member that owned
        it was read-only on its own files.  Every character but `*`, `?` and `**` is literal now."""
        spud = load_spud_module()
        match = spud.path_matches_glob
        glob = "admin/src/app/accounts/[email]/**"
        for ok in ("admin/src/app/accounts/[email]/page.tsx", "admin/src/app/accounts/[email]/_components/UserTab.tsx"):
            self.assertTrue(match(ok, glob), ok)
        for bad in ("admin/src/app/accounts/e/page.tsx", "admin/src/app/accounts/page.tsx", "admin/src/app/accounts/[id]/page.tsx"):
            self.assertFalse(match(bad, glob), bad)
        self.assertTrue(match("admin/src/app/accounts/[email]/_components/UserTab.tsx", "admin/src/app/accounts/?email?/**"))  # the workaround still works
        self.assertTrue(match("admin/src/app/accounts/[email]/_components/UserTab.tsx", "admin/src/app/accounts/**"))
        self.assertTrue(match("app/[...slug]/page.tsx", "app/[...slug]/**"))  # a catch-all route: the dots are literal too
        self.assertTrue(match("app/[[...slug]]/page.tsx", "app/[[...slug]]/**"))
        self.assertFalse(match("app/x/page.tsx", "app/[...slug]/**"))
        # Nothing is an escape any more, so the shell's own spelling of a literal bracket names a directory spelled that
        # way -- and, unlike before SPD-086, it compiles instead of raising re.PatternError from inside the hook.
        self.assertTrue(match("a/[[]b[]]/c", "a/[[]b[]]/c"))
        self.assertFalse(match("a/[b]/c", "a/[[]b[]]/c"))
        for glob in ("a/[]]/c", "a/[/c", "a/]/c", "a/[!x]/c", "a/[a-z]/c"):
            self.assertTrue(match(glob, glob), glob)  # each names itself, and none of them raises
            self.assertFalse(match("a/x/c", glob), glob)

    def test_no_accepted_glob_is_unsatisfiable(self):
        """SPD-086 asked whether `member new` should warn about a deliverable that can match no path.  It cannot check the
        checkout -- a member planned to create bin/spudlib/hooks/newmod.py legitimately matches nothing that exists yet --
        and with brackets literal there is nothing left to warn about: every glob normalize_deliverable accepts matches a
        path, because every character is either a wildcard or a literal, and `..`, absolute paths and empty segments are
        already refused.  The witness below is that path."""
        spud = load_spud_module()
        globs = ["tests/**", "bin/spud", "docs/x/*.md", "**/d.md", "a/**/b.md", "ledger/*.base", "web/app*.js",
                 "scripts/ci-changes*", "server/supabase/functions/collab/*.ts", "**", "a/b[?]c",
                 "admin/src/app/accounts/[email]/**", "app/[[...slug]]/page.tsx", "a/[]]/c", "notes/**"]
        for glob in globs:
            witness = glob.replace("**/", "w/").replace("**", "w/w").replace("*", "w").replace("?", "w")
            self.assertTrue(spud.path_matches_glob(witness, glob), (glob, witness))


# =============================================================================
# SessionStart and Stop
# =============================================================================


class SessionStartTest(HookCase):
    def test_board_brief_is_injected(self):
        m = self.plan(name="Kestrel")
        r = self.home.hook("SessionStart", self.session_start())
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.json["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn(self.t["key"], r.context)
        self.assertIn("Kestrel", r.context)
        self.assertIn(self.home.run("board", "--brief").stdout.strip(), r.context)
        for source in ("resume", "compact"):
            self.assertEqual(self.home.hook("SessionStart", self.session_start(source)).code, 0)
        # SPD-096: a parked ticket that is not due leaves the injection for the count line alone
        shelved = self.new_ticket("Shelved", status="queued")
        self.home.json("ticket", "move", shelved["key"], "--status", "parked", "--reason", "Eric's go", "--until", "2099-01-01", actor="spud")
        r = self.home.hook("SessionStart", self.session_start())
        self.assertNotIn("Shelved", r.context)
        self.assertIn("1 parked (spud board --parked)", r.context)

    def test_clear_injects_the_board_as_the_other_sources_do(self):
        # SPD-011: the matcher takes clear, and a /clear gets the same context as the other three
        self.plan(name="Kestrel")
        board = self.home.run("board", "--brief").stdout.strip()
        for source in ("startup", "resume", "clear", "compact"):
            r = self.home.hook("SessionStart", self.session_start(source))
            self.assertEqual(r.code, 0, r)
            self.assertEqual(r.json["hookSpecificOutput"]["hookEventName"], "SessionStart", source)
            self.assertIn("source %s)" % source, r.context)
            self.assertIn(board, r.context)

    def test_missing_database_is_silent(self):
        os.remove(self.home.db)
        r = self.home.hook("SessionStart", self.session_start())
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""))


class StopTest(HookCase):
    def returned_unrecorded(self, name=None):
        m = self.spawn(self.plan(name=name), AGENT_A if name != "Yukon" else AGENT_B)
        agent = m["agent_id"]
        self.home.json("member", "result", "done", actor=agent)
        r = self.home.hook("SubagentStop", self.sub_stop(agent))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        return m

    def test_nothing_unrecorded_lets_the_turn_end(self):
        self.spawn(self.plan(), AGENT_A)  # still running
        r = self.home.hook("Stop", self.stop())
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""), r)

    def test_law_9_blocks_once_with_the_list(self):
        m = self.returned_unrecorded("Kestrel")
        r = self.home.hook("Stop", self.stop())
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.json["decision"], "block")
        self.assertIn(m["ref"], r.json["reason"])
        self.assertIn("spud --as spud member finish", r.json["reason"])
        self.assertIn("Law 9", r.json["reason"])
        d = self.denied()
        self.assertEqual((d[-1]["actor"], d[-1]["data"]["hook_event_name"]), ("hook:Stop", "Stop"))
        r = self.home.hook("Stop", self.stop(stop_hook_active=True))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.home.json("member", "finish", m["ref"], "--status", "done", "--outcome", "Accepted.", actor="spud")
        r = self.home.hook("Stop", self.stop())
        self.assertEqual((r.code, r.stdout), (0, ""), r)

    def test_a_grandchild_is_its_leads_business_while_the_lead_lives(self):
        lead = self.spawn(self.plan(persona="engineer", model="opus"), AGENT_A)
        child = self.plan(actor=lead["ref"])
        child = self.spawn(child, AGENT_B, caller=AGENT_A)
        self.home.json("member", "result", "done", actor=AGENT_B)
        self.home.hook("SubagentStop", self.sub_stop(AGENT_B))
        r = self.home.hook("Stop", self.stop())
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.home.json("member", "finish", lead["ref"], "--status", "failed", "--outcome", "died", actor="spud")
        r = self.home.hook("Stop", self.stop())
        self.assertEqual(r.json["decision"], "block")
        self.assertIn(child["ref"], r.json["reason"])

    def test_a_subagents_stop_is_a_no_op(self):
        self.returned_unrecorded("Kestrel")
        r = self.home.hook("Stop", self.stop(agent_id=AGENT_B))
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""), r)

    def test_missing_database_is_silent(self):
        os.remove(self.home.db)
        r = self.home.hook("Stop", self.stop())
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""))


class StopSessionTest(HookCase):
    """Spud's Stop hook, one session at a time (SPD-018).  Eric runs tickets in parallel sessions, so a session is
    held only for what it owes: the members of the trees it spawned, and a row with no known session (from before
    sessions were recorded).  Three kinds, in one block: returned and unrecorded, planned and never spawned, and
    still running under a finished parent (told once per session)."""

    # Today's reason when only returned members are listed, kept byte for byte for a root member; since SPD-027 the
    # printed command also carries --next (member finish's own Next line, since a root member's finish is the one
    # that writes a report entry).
    RETURNED_ONLY = ("Law 9: %d returned spudagent(s) are not recorded: %s. Record each with `spud --as spud member finish <SPUD-nnn/Name>"
                     " --status done|blocked|failed --outcome '<verdict>' [--summary '<one paragraph>'] [--next '<what happens next>']`,"
                     " decide its proposals (spud proposal list --open; spud --as spud proposal decide ...), then end the turn.")
    # A nested returned member (its parent finished): the SPD-008 wording, unchanged, since member finish writes no
    # report entry and so no Next line for a child.
    RETURNED_ONLY_NESTED = ("Law 9: %d returned spudagent(s) are not recorded: %s. Record each with `spud --as spud member finish <SPUD-nnn/Name>"
                           " --status done|blocked|failed --outcome '<verdict>' [--summary '<one paragraph>']`, decide its proposals"
                           " (spud proposal list --open; spud --as spud proposal decide ...), then end the turn.")
    SESSION_C = "5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d"

    def in_session(self, session):
        """The session `member new` runs in from here on (None: outside every session)."""
        if session is None:
            self.home.env.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            self.home.env["CLAUDE_CODE_SESSION_ID"] = session

    def returned(self, agent_id, session=SESSION, name=None):
        """A root member planned and spawned in session that recorded its Result and stopped: returned, unrecorded."""
        self.in_session(session)
        m = self.spawn(self.plan(name=name), agent_id, session=session)
        self.home.json("member", "result", "Built it.", actor=agent_id)
        r = self.home.hook("SubagentStop", self.sub_stop(agent_id, session=session))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        return self.home.json("member", "show", m["ref"])["member"]

    def orphan(self, lead_agent, child_agent, session=SESSION):
        """A lead spawned in session and its child, still running after Spud finished the lead: (lead, child)."""
        self.in_session(session)
        lead = self.spawn(self.plan(persona="engineer", model="opus"), lead_agent, session=session)
        child = self.spawn(self.plan(actor=lead["ref"]), child_agent, caller=lead_agent, session=session)
        self.home.json("member", "finish", lead["ref"], "--status", "done", "--outcome", "Accepted.", actor="spud")
        return lead, child

    def stop_in(self, session, stop_hook_active=False):
        return self.home.hook("Stop", self.stop(stop_hook_active=stop_hook_active, session=session))

    def assertSilent(self, r):
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""), r)

    @staticmethod
    def ago(minutes):
        return (datetime.now().astimezone() - timedelta(minutes=minutes)).isoformat(timespec="seconds")

    def session_of(self, m):
        return self.home.scalar("SELECT session_id FROM members WHERE id = ?", m["id"])

    # -- returned and unrecorded ----------------------------------------------------
    def test_a_returned_member_holds_only_the_session_that_spawned_it(self):
        kestrel = self.returned(AGENT_A, SESSION, name="Kestrel")
        yukon = self.returned(AGENT_B, SESSION_B, name="Yukon")
        r = self.stop_in(SESSION)
        self.assertEqual((r.code, r.json["decision"]), (0, "block"), r)
        self.assertEqual(r.json["reason"], self.RETURNED_ONLY % (1, "SPUD-001/Kestrel (01, scout) on SPD-001, stopped %s" % kestrel["stopped_at"][:16]))
        r = self.stop_in(SESSION_B)
        self.assertEqual(r.json["reason"], self.RETURNED_ONLY % (1, "SPUD-001/Yukon (02, scout) on SPD-001, stopped %s" % yukon["stopped_at"][:16]))
        self.assertSilent(self.stop_in(self.SESSION_C))  # a third session owes neither
        self.assertEqual([d["data"] for d in self.denied()], [
            {"hook_event_name": "Stop", "session_id": SESSION, "members": ["SPUD-001/Kestrel"], "returned": ["SPUD-001/Kestrel"], "planned": [], "unbound": [], "running": []},
            {"hook_event_name": "Stop", "session_id": SESSION_B, "members": ["SPUD-001/Yukon"], "returned": ["SPUD-001/Yukon"], "planned": [], "unbound": [], "running": []},
        ])

    def test_the_printed_next_option_actually_runs(self):
        """The --next this reason offers a root member is not just words: filled in and run, it exits 0 and
        writes a report entry whose last line is the Next line (SPD-027)."""
        kestrel = self.returned(AGENT_A, SESSION, name="Kestrel")
        reason = self.stop_in(SESSION).json["reason"]
        self.assertIn("[--next '<what happens next>']", reason)
        before = len(self.events("report.entry"))
        out = self.home.json("member", "finish", kestrel["ref"], "--status", "done", "--outcome", "x", "--next", "y", actor="spud")
        self.assertEqual(out["member"]["status"], "done")
        entries = self.events("report.entry")
        self.assertEqual(len(entries), before + 1)
        self.assertTrue(entries[-1]["body"].endswith("- Next: y"), entries[-1])
        self.assertSilent(self.stop_in(SESSION))

    def test_the_spawn_request_names_the_session_when_the_row_records_none(self):
        m = self.returned(AGENT_A, SESSION_B)
        self.set_member(m["id"], session_id=None)
        self.assertSilent(self.stop_in(SESSION))
        self.assertEqual(self.stop_in(SESSION_B).json["decision"], "block")

    def test_a_member_with_no_known_session_holds_every_session(self):
        """A row from before sessions were recorded: no spawn request and no session on the row."""
        self.in_session(None)
        m = self.plan(name="Kestrel")
        self.home.json("member", "start", m["ref"], actor="spud")
        self.set_member(m["id"], stopped_at="2026-09-12T20:15:48-07:00")
        for session in (SESSION, SESSION_B):
            r = self.stop_in(session)
            self.assertEqual(r.json["reason"], self.RETURNED_ONLY % (1, "SPUD-001/Kestrel (01, scout) on SPD-001, stopped 2026-09-12T20:15"), (session, r))

    def test_a_stop_payload_without_a_session_owes_every_member(self):
        """session_id is a common input field (hooks reference), so a Stop without one is malformed: the hook holds
        as it did before SPD-018, ledger-wide, rather than letting every member go."""
        yukon = self.returned(AGENT_B, SESSION_B, name="Yukon")
        payload = self.stop()
        del payload["session_id"]
        r = self.home.hook("Stop", payload)
        self.assertEqual(r.json["reason"], self.RETURNED_ONLY % (1, "SPUD-001/Yukon (01, scout) on SPD-001, stopped %s" % yukon["stopped_at"][:16]), r)
        self.assertIsNone(self.denied()[-1]["data"]["session_id"])

    # -- planned and never spawned --------------------------------------------------
    def test_a_planned_row_never_spawned_holds_the_session_that_planned_it(self):
        m = self.plan(name="Kestrel")
        self.assertEqual(self.session_of(m), SESSION)  # member new records CLAUDE_CODE_SESSION_ID
        self.assertSilent(self.stop_in(SESSION_B))
        r = self.stop_in(SESSION)
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 1 planned spudagent(s) were never spawned: SPUD-001/Kestrel (01, scout) on SPD-001, planned %s. " % m["planned_at"][:16]), reason)
        self.assertIn("subagent_type `spudagent`, model `haiku`, description `SPUD-001/Kestrel (01, scout)`", reason)
        # a root row (Kestrel's parent_id is NULL): its failed command also offers --next, since SPD-027
        self.assertIn("`spud --as spud member finish SPUD-001/Kestrel --status failed --outcome '<why>' [--next '<what happens next>']`", reason)
        self.assertTrue(reason.endswith(". Then end the turn."), reason)
        self.assertEqual(self.denied()[-1]["data"]["planned"], ["SPUD-001/Kestrel"])
        self.assertEqual(self.stop_in(SESSION).json["decision"], "block")  # every fresh stop, until it is spawned or recorded
        self.home.json("member", "finish", m["ref"], "--status", "failed", "--outcome", "Never spawned.", actor="spud")
        self.assertSilent(self.stop_in(SESSION))

    def test_a_planned_row_with_no_known_session_holds_any_session_after_ten_minutes(self):
        self.in_session(None)
        m = self.plan(name="Kestrel")
        self.assertIsNone(self.session_of(m))
        self.assertSilent(self.stop_in(SESSION))  # planned a moment ago, perhaps by a session about to spawn it
        self.set_member(m["id"], planned_at=self.ago(9))
        self.assertSilent(self.stop_in(SESSION))
        self.set_member(m["id"], planned_at=self.ago(11))
        for session in (SESSION, SESSION_B):
            r = self.stop_in(session)
            self.assertEqual(r.json["decision"], "block", (session, r))
            self.assertIn("SPUD-001/Kestrel (01, scout) on SPD-001, planned ", r.json["reason"])

    def test_a_spawn_waiting_to_bind_and_a_living_leads_child_are_not_held(self):
        reserved = self.plan(name="Kestrel")
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(reserved), tool_use_id="toolu_reserved"))
        self.assertEqual(pre.decision, "allow", pre)
        lead = self.spawn(self.plan(persona="engineer", model="opus", name="Yukon"), AGENT_A)
        self.plan(actor=lead["ref"], name="Russet")
        self.assertSilent(self.stop_in(SESSION))  # a spawn on its way, and a child its living lead answers for
        self.home.json("member", "finish", lead["ref"], "--status", "failed", "--outcome", "Died.", actor="spud")
        r = self.stop_in(SESSION)
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 1 planned spudagent(s) were never spawned: SPUD-001/Russet (02.01, scout) on SPD-001, planned "), reason)
        self.assertIn("`spud --as spud member finish SPUD-001/Russet --status failed --outcome '<why>'`", reason)
        self.assertNotIn("--next", reason)  # Russet is nested (under Yukon): its failed command stays as it was
        self.assertNotIn("description `SPUD-001/Russet", reason)  # nobody can spawn it: its parent is finished
        self.assertNotIn("Kestrel", reason)

    # -- running under a finished parent --------------------------------------------
    def test_a_child_running_under_a_finished_parent_is_held_once_and_then_as_returned(self):
        lead, child = self.orphan(AGENT_A, AGENT_B)
        r = self.stop_in(SESSION)
        self.assertEqual(r.json["decision"], "block", r)
        reason = r.json["reason"]
        self.assertTrue(reason.startswith("Law 9: 1 spudagent(s) are still running under a finished parent: %s (01.01, scout) on SPD-001, running since %s, under %s (done). "
                                          % (child["ref"], child["spawned_at"][:16], lead["ref"])), reason)
        self.assertIn("`spud --as spud member finish %s --status done|blocked|failed --outcome '<verdict>'`" % child["ref"], reason)
        self.assertNotIn("--next", reason)  # the running clause never offers it (SPD-027)
        self.assertEqual(self.denied()[-1]["data"]["running"], [child["ref"]])
        self.assertSilent(self.stop_in(SESSION))  # told once in this session
        self.assertSilent(self.stop_in(SESSION_B))  # and never another session's to record
        self.home.json("member", "result", "Child done.", actor=AGENT_B)
        self.assertEqual(self.home.hook("SubagentStop", self.sub_stop(AGENT_B)).stdout, "")
        stopped = self.home.json("member", "show", child["ref"])["member"]["stopped_at"]
        r = self.stop_in(SESSION)
        # child is nested (its parent_id is the lead's, not NULL): returned without --next, unlike a root member
        self.assertEqual(r.json["reason"], self.RETURNED_ONLY_NESTED % (1, "%s (01.01, scout) on SPD-001, stopped %s" % (child["ref"], stopped[:16])))

    def test_a_running_child_with_no_known_session_is_held_once_in_each_session(self):
        self.in_session(None)
        lead = self.plan(persona="engineer", model="opus")
        self.home.json("member", "start", lead["ref"], actor="spud")
        child = self.plan(actor=lead["ref"])
        self.home.json("member", "start", child["ref"], actor="spud")
        self.home.json("member", "finish", lead["ref"], "--status", "blocked", "--outcome", "Needs Eric.", actor="spud")
        for session in (SESSION, SESSION_B):
            r = self.stop_in(session)
            self.assertEqual(r.json["decision"], "block", (session, r))
            self.assertIn("%s (01.01, scout) on SPD-001, running since " % child["ref"], r.json["reason"])
            self.assertSilent(self.stop_in(session))

    def test_spuds_own_running_child_never_holds(self):
        self.spawn(self.plan(), AGENT_A)  # a background child at work: ending the turn meanwhile is the design
        for session in (SESSION, SESSION_B):
            self.assertSilent(self.stop_in(session))
        self.assertEqual(self.denied(), [])

    # -- one block --------------------------------------------------------------------
    def three_kinds(self):
        """One member of each kind, all in SESSION: (returned, planned, running)."""
        _, running = self.orphan(AGENT_A, AGENT_B)
        returned = self.returned(AGENT_C)
        planned = self.plan()
        return returned, planned, running

    def test_stop_hook_active_lets_every_kind_through(self):
        self.three_kinds()
        self.assertSilent(self.stop_in(SESSION, stop_hook_active=True))
        self.assertEqual(self.denied(), [])
        r = self.stop_in(SESSION)  # the let-go stop told nobody anything
        self.assertEqual(r.json["decision"], "block", r)
        self.assertEqual(len(self.denied()[-1]["data"]["running"]), 1)

    def test_one_block_names_the_three_kinds_in_order(self):
        returned, planned, running = self.three_kinds()
        reason = self.stop_in(SESSION).json["reason"]
        heads = ["Law 9: 1 returned spudagent(s) are not recorded: %s (" % returned["ref"],
                 "1 planned spudagent(s) were never spawned: %s (" % planned["ref"],
                 "1 spudagent(s) are still running under a finished parent: %s (" % running["ref"]]
        at = [reason.find(h) for h in heads]
        self.assertEqual(at[0], 0, reason)
        self.assertTrue(0 < at[1] < at[2], (at, reason))
        self.assertTrue(reason.endswith(". Then end the turn."), reason)
        self.assertEqual([d["data"] for d in self.denied()], [{
            "hook_event_name": "Stop", "session_id": SESSION, "members": [returned["ref"], planned["ref"], running["ref"]],
            "returned": [returned["ref"]], "planned": [planned["ref"]], "unbound": [], "running": [running["ref"]]}])

    # -- allowed to spawn and never bound (SPD-025) -------------------------------------
    # PreToolUse(Agent) allowed the spawn and reserved the row, and nothing bound it: the harness failed the spawn after the
    # allow, or the binding hooks failed open.  Neither can be produced on demand, so these tests age the spawn request.
    UNBOUND_ONLY = ("Law 9: 1 planned spudagent(s) were allowed to spawn and never bound: %(ref)s (%(lineage)s, %(persona)s) on SPD-001,"
                    " spawn allowed %(at)s (tool_use_id %(tool_use_id)s), never bound. First look for its tool_use_id in"
                    " `spud events --kind hook.error --json`: a gap there means that child may still be running, so wait for its notification."
                    " If the harness failed the spawn, record %(ref)s with `spud --as spud member finish %(ref)s --status failed --outcome '<why>'"
                    " [--next '<what happens next>']` and plan a new member, since the reservation refuses a second spawn of"
                    " `%(ref)s (%(lineage)s, %(persona)s)`. A reserved row holds a slot against the limits until it is bound or recorded."
                    " Then end the turn.")

    def reserve(self, m, tool_use_id, session=SESSION, caller=None):
        """PreToolUse(Agent) allows m's spawn in session, which reserves the row; nothing binds it."""
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(m), model=m["model"], subagent_type=m["agent_type"], agent_id=caller,
                                                          tool_use_id=tool_use_id, session=session))
        self.assertEqual((pre.code, pre.decision), (0, "allow"), pre)

    def age_request(self, tool_use_id, minutes):
        """The spawn request's allow moved minutes into the past; returns its new stamp."""
        con = self.home.connect()
        try:
            with con:
                con.execute("UPDATE spawn_requests SET at = ? WHERE tool_use_id = ?", (self.ago(minutes), tool_use_id))
        finally:
            con.close()
        return self.home.scalar("SELECT at FROM spawn_requests WHERE tool_use_id = ?", tool_use_id)

    def blocked(self, r):
        """The reason of a Stop that blocked."""
        self.assertEqual((r.code, (r.json or {}).get("decision")), (0, "block"), r)
        return r.json["reason"]

    def test_a_spawn_allowed_within_the_grace_holds_nobody(self):
        m = self.plan(name="Kestrel")
        self.set_member(m["id"], planned_at=self.ago(30))  # the grace runs from the allow, not from the plan
        self.reserve(m, "toolu_reserved")
        self.assertSilent(self.stop_in(SESSION))
        self.age_request("toolu_reserved", 9)
        for session in (SESSION, SESSION_B):
            self.assertSilent(self.stop_in(session))  # a spawn in flight
        self.assertEqual(self.denied(), [])

    def test_a_spawn_allowed_past_the_grace_and_never_bound_holds_its_session_with_its_own_clause(self):
        m = self.plan(name="Kestrel")
        self.reserve(m, "toolu_reserved")
        at = self.age_request("toolu_reserved", 11)
        self.assertSilent(self.stop_in(SESSION_B))  # another session's spawn
        reason = self.blocked(self.stop_in(SESSION))
        self.assertEqual(reason, self.UNBOUND_ONLY % {"ref": m["ref"], "lineage": "01", "persona": "scout", "at": at[:16], "tool_use_id": "toolu_reserved"})
        self.assertEqual(self.denied()[-1]["data"], {"hook_event_name": "Stop", "session_id": SESSION, "members": [m["ref"]],
                                                     "returned": [], "planned": [], "unbound": [m["ref"]], "running": []})
        self.blocked(self.stop_in(SESSION))  # every fresh stop, until it is bound or recorded

    def test_the_session_that_asked_for_the_spawn_owes_it_not_the_one_that_planned_it(self):
        m = self.plan(name="Kestrel")  # planned in SESSION
        self.reserve(m, "toolu_reserved", session=SESSION_B)  # spawned from SESSION_B, after a /clear say
        self.age_request("toolu_reserved", 11)
        self.assertSilent(self.stop_in(SESSION))
        self.assertIn(": SPUD-001/Kestrel (01, scout) on SPD-001, spawn allowed ", self.blocked(self.stop_in(SESSION_B)))

    def test_binding_the_spawn_or_recording_the_row_failed_clears_it(self):
        kestrel, yukon = self.plan(name="Kestrel"), self.plan(name="Yukon")
        for m, tool_use_id in ((kestrel, "toolu_kestrel"), (yukon, "toolu_yukon")):
            self.reserve(m, tool_use_id)
            self.age_request(tool_use_id, 11)
        reason = self.blocked(self.stop_in(SESSION))
        self.assertTrue(reason.startswith("Law 9: 2 planned spudagent(s) were allowed to spawn and never bound: SPUD-001/Kestrel (01, scout) on SPD-001, "), reason)
        self.assertIn(" never bound. First look for each tool_use_id in `spud events --kind hook.error --json`: ", reason)
        self.assertIn(" If the harness failed a spawn, record SPUD-001/Kestrel with ", reason)
        self.assertEqual(self.denied()[-1]["data"]["unbound"], ["SPUD-001/Kestrel", "SPUD-001/Yukon"])
        # Kestrel's binding lands after all: Spud's own child at work, which never holds
        post = self.home.hook("PostToolUse", self.post_agent_launched("toolu_kestrel", AGENT_A, self.description(kestrel)))
        self.assertEqual((post.code, post.stdout), (0, ""), post)
        self.assertEqual(self.home.json("member", "show", kestrel["ref"])["member"]["status"], "active")
        self.blocked(self.stop_in(SESSION))
        self.assertEqual(self.denied()[-1]["data"]["unbound"], ["SPUD-001/Yukon"])
        # the harness failed Yukon's spawn: the printed command, --next and all, records a planned row that holds a reservation
        out = self.home.json("member", "finish", yukon["ref"], "--status", "failed", "--outcome", "The harness failed the spawn.",
                             "--next", "Plan a new member for the work.", actor="spud")
        self.assertEqual((out["member"]["status"], bool(out["member"]["finished_at"])), ("failed", True))
        self.assertSilent(self.stop_in(SESSION))
        self.assertEqual(self.home.hook("PreToolUse", self.pre_agent(self.description(yukon), tool_use_id="toolu_again")).decision, "deny")
        self.spawn(self.plan(name="Russet"), AGENT_B)  # the new member takes the work
        self.assertSilent(self.stop_in(SESSION))

    def test_a_nested_spawn_never_bound_under_a_finished_parent_holds_with_the_nested_way_out(self):
        lead = self.spawn(self.plan(persona="engineer", model="opus", name="Yukon"), AGENT_A)
        child = self.plan(actor=lead["ref"], name="Russet")
        self.reserve(child, "toolu_russet", caller=AGENT_A)
        at = self.age_request("toolu_russet", 11)
        self.assertSilent(self.stop_in(SESSION))  # its living lead answers for it (SubagentStop, alive_children)
        self.home.json("member", "finish", lead["ref"], "--status", "failed", "--outcome", "Died.", actor="spud")
        self.assertSilent(self.stop_in(SESSION_B))
        reason = self.blocked(self.stop_in(SESSION))
        self.assertTrue(reason.startswith("Law 9: 1 planned spudagent(s) were allowed to spawn and never bound: SPUD-001/Russet (01.01, scout) on SPD-001,"
                                          " spawn allowed %s (tool_use_id toolu_russet), never bound, under SPUD-001/Yukon (failed). " % at[:16]), reason)
        self.assertIn(" If the harness failed the spawn, record SPUD-001/Russet with `spud --as spud member finish SPUD-001/Russet --status failed"
                      " --outcome '<why>'`: nobody can spawn it again now that SPUD-001/Yukon is failed. ", reason)
        self.assertNotIn("--next", reason)
        self.assertNotIn("plan a new member", reason)
        self.assertEqual(self.denied()[-1]["data"]["unbound"], ["SPUD-001/Russet"])
        refused = self.home.run("member", "finish", child["ref"], "--status", "failed", "--outcome", "x", "--next", "y", actor="spud", check=False)
        self.assertEqual(refused.returncode, EXIT_USAGE, refused.stderr)  # a nested row's finish refuses --next
        out = self.home.json("member", "finish", child["ref"], "--status", "failed", "--outcome", "Its spawn never bound; its lead died.", actor="spud")
        self.assertEqual(out["member"]["status"], "failed")
        self.assertSilent(self.stop_in(SESSION))

    def test_stop_hook_active_lets_an_unbound_spawn_through(self):
        m = self.plan(name="Kestrel")
        self.reserve(m, "toolu_reserved")
        self.age_request("toolu_reserved", 11)
        self.assertSilent(self.stop_in(SESSION, stop_hook_active=True))
        self.assertEqual(self.denied(), [])
        self.assertIn(m["ref"], self.blocked(self.stop_in(SESSION)))

    def test_one_block_names_the_four_kinds_in_order(self):
        _, running = self.orphan(AGENT_A, AGENT_B)
        returned = self.returned(AGENT_C)
        planned = self.plan()
        denied = self.home.hook("PreToolUse", self.pre_agent(self.description(planned), model="opus", tool_use_id="toolu_denied"))
        self.assertEqual(denied.decision, "deny", denied)  # a denied spawn reserves nothing: the row is still never spawned
        unbound = self.plan()
        self.reserve(unbound, "toolu_unbound")
        self.age_request("toolu_unbound", 11)
        reason = self.blocked(self.stop_in(SESSION))
        heads = ["Law 9: 1 returned spudagent(s) are not recorded: %s (" % returned["ref"],
                 "1 planned spudagent(s) were never spawned: %s (" % planned["ref"],
                 "1 planned spudagent(s) were allowed to spawn and never bound: %s (" % unbound["ref"],
                 "1 spudagent(s) are still running under a finished parent: %s (" % running["ref"]]
        at = [reason.find(h) for h in heads]
        self.assertEqual(at[0], 0, reason)
        self.assertTrue(0 < at[1] < at[2] < at[3], (at, reason))
        self.assertTrue(reason.endswith(". Then end the turn."), reason)
        self.assertEqual([d["data"] for d in self.denied()], [{
            "hook_event_name": "Stop", "session_id": SESSION, "members": [returned["ref"], planned["ref"], unbound["ref"], running["ref"]],
            "returned": [returned["ref"]], "planned": [planned["ref"]], "unbound": [unbound["ref"]], "running": [running["ref"]]}])

    def test_the_returned_only_reason_is_unchanged_beside_a_spawn_in_flight_and_another_sessions_unbound_one(self):
        kestrel = self.returned(AGENT_A, SESSION, name="Kestrel")
        self.reserve(self.plan(name="Yukon"), "toolu_in_flight")  # SESSION's own, allowed a moment ago
        self.in_session(SESSION_B)
        self.reserve(self.plan(name="Russet"), "toolu_other", session=SESSION_B)
        self.age_request("toolu_other", 11)
        reason = self.blocked(self.stop_in(SESSION))
        self.assertEqual(reason, self.RETURNED_ONLY % (1, "SPUD-001/Kestrel (01, scout) on SPD-001, stopped %s" % kestrel["stopped_at"][:16]))
        self.assertEqual(self.denied()[-1]["data"], {"hook_event_name": "Stop", "session_id": SESSION, "members": ["SPUD-001/Kestrel"],
                                                     "returned": ["SPUD-001/Kestrel"], "planned": [], "unbound": [], "running": []})
        # once SESSION's own spawn has waited past the grace it joins the block, and the returned clause loses its own ending
        at = self.age_request("toolu_in_flight", 11)
        reason = self.blocked(self.stop_in(SESSION))
        self.assertTrue(reason.startswith("Law 9: 1 returned spudagent(s) are not recorded: SPUD-001/Kestrel (01, scout) on SPD-001, stopped "), reason)
        self.assertIn(". 1 planned spudagent(s) were allowed to spawn and never bound: SPUD-001/Yukon (02, scout) on SPD-001, spawn allowed %s"
                      " (tool_use_id toolu_in_flight), never bound. " % at[:16], reason)
        self.assertTrue(reason.endswith(". Then end the turn."), reason)
        self.assertEqual(self.denied()[-1]["data"]["unbound"], ["SPUD-001/Yukon"])


# =============================================================================
# Failure policy
# =============================================================================


class FailurePolicyTest(HookCase):
    def break_database(self):
        con = self.home.connect()
        con.execute("PRAGMA user_version = 99")
        con.close()

    def test_enforcing_hooks_exit_2_on_an_unexpected_error(self):
        m = self.plan()
        self.break_database()
        for p in (self.pre_agent(self.description(m)), self.pre_bash("ls", agent_id=AGENT_A), self.pre_edit(self.home.path / "x", agent_id=AGENT_A)):
            r = self.home.hook("PreToolUse", p)
            self.assertEqual(r.code, 2, r)
            self.assertEqual(r.stdout, "")
            self.assertIn("ahead of this CLI", r.stderr)
        r = self.home.hook("Stop", self.stop())
        self.assertEqual(r.code, 2, r)
        r = self.home.hook("Stop", self.stop(stop_hook_active=True))
        self.assertEqual(r.code, 0, r)

    def test_recording_hooks_exit_0_and_spool_the_gap(self):
        m1 = self.spawn(self.plan(), AGENT_A)
        m2 = self.plan()
        self.home.hook("PreToolUse", self.pre_agent(self.description(m2), tool_use_id="toolu_dup"))
        # binding a second member to an agent_id that is already bound violates UNIQUE
        r = self.home.hook("PostToolUse", self.post_agent_launched("toolu_dup", AGENT_A, self.description(m2)))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertTrue(self.home.spool.exists())
        lines = [json.loads(l) for l in self.home.spool.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["event"], "PostToolUse")
        self.assertIn("UNIQUE", lines[0]["error"])
        self.assertEqual(self.events("hook.error"), [])
        # the next successful hook drains it
        r = self.home.hook("SubagentStart", self.sub_start(AGENT_C))
        self.assertEqual(r.code, 0)
        errs = self.events("hook.error")
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["actor"], "hook:PostToolUse")
        self.assertIn("UNIQUE", errs[0]["body"])
        self.assertEqual(errs[0]["data"]["tool_use_id"], "toolu_dup")
        self.assertFalse(self.home.spool.exists() and self.home.spool.stat().st_size > 0)
        self.assertEqual(self.home.json("member", "show", m1["ref"])["member"]["agent_id"], AGENT_A)

    def test_recording_hooks_exit_0_on_a_broken_database_and_malformed_input(self):
        for raw in ("", "not json", "[1]"):
            for event in ("PostToolUse", "SubagentStart", "SubagentStop", "SessionStart"):
                r = self.home.hook(event, raw)
                self.assertEqual((r.code, r.stdout), (0, ""), (event, raw, r))
        self.assertTrue(self.home.spool.exists())
        self.break_database()
        for event, p in (("PostToolUse", self.post_agent_launched("toolu_x", AGENT_A, "x")), ("SubagentStart", self.sub_start(AGENT_A)), ("SubagentStop", self.sub_stop(AGENT_A)), ("SessionStart", self.session_start())):
            r = self.home.hook(event, p)
            self.assertEqual((r.code, r.stdout), (0, ""), (event, r))

    def test_the_cli_drains_the_spool_too(self):
        self.home.hook("SubagentStart", "not json")
        self.assertTrue(self.home.spool.exists())
        self.home.json("board")
        errs = self.events("hook.error")
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["actor"], "hook:SubagentStart")
        self.assertEqual(self.home.spool.stat().st_size if self.home.spool.exists() else 0, 0)


# =============================================================================
# spud sql --readonly
# =============================================================================


class SqlTest(SpudTestCase):
    def test_readonly_queries_print_a_table_or_json(self):
        t = self.new_ticket("Query")
        proc = self.home.run("sql", "--readonly", "SELECT key, status FROM tickets")
        self.assertIn(t["key"], proc.stdout)
        self.assertIn("queued", proc.stdout)
        out = self.home.json("sql", "--readonly", "SELECT count(*) AS n FROM tickets")
        self.assertEqual(out["rows"], [{"n": 1}])
        self.assertEqual(out["columns"], ["n"])
        out = self.home.json("sql", "--readonly", "SELECT 1 WHERE 0")
        self.assertEqual(out["rows"], [])

    def test_writes_are_impossible(self):
        for stmt in ("INSERT INTO name_pool (name) VALUES ('X')", "UPDATE tickets SET title = 'x'", "DELETE FROM events", "PRAGMA user_version = 77", "CREATE TABLE x (a)", "DROP TABLE renders", "PRAGMA query_only = OFF; INSERT INTO name_pool (name) VALUES ('X')", "ATTACH '/tmp/x.db' AS x"):
            proc = self.home.run("sql", "--readonly", stmt, check=False)
            self.assertEqual(proc.returncode, EXIT_ERROR, stmt)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM name_pool WHERE name = 'X'"), 0)
        self.assertEqual(self.home.scalar("PRAGMA user_version"), 5)

    def test_one_statement_no_flag_no_actor_needed(self):
        proc = self.home.run("sql", "SELECT 1", check=False)
        self.assertEqual(proc.returncode, EXIT_USAGE)
        proc = self.home.run("sql", "--readonly", "SELECT 1; SELECT 2", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        proc = self.home.run("sql", "--readonly", "SELECT 1", actor="spud")
        self.assertEqual(proc.returncode, 0)
        proc = self.home.run("sql", "--readonly", "SELEC 1", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("syntax", proc.stderr.lower())


class LateBindingTest(HookCase):
    """A foreground spawn is bound at its first tool call from agent-<id>.meta.json beside
    the session's subagent transcripts, so it can write its deliverables before its stop."""

    def write_meta(self, agent_id, tool_use_id, description):
        """What the harness writes beside the subagent transcript (spike fact 6)."""
        meta = self.home.path / "projects" / SESSION / "subagents" / ("agent-%s.meta.json" % agent_id)
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"agentType": "spudagent", "description": description, "toolUseId": tool_use_id, "spawnDepth": 1, "requestShape": "foreground", "requestNonInteractive": True, "model": "haiku"}), encoding="utf-8")
        return str(self.home.path / "projects" / ("%s.jsonl" % SESSION))

    def test_first_tool_call_binds_a_foreground_spawn(self):
        m = self.plan(deliverable=["home:tests/**"])
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_fg", run_in_background=False))
        self.assertEqual(pre.decision, "allow")
        transcript = self.write_meta(AGENT_B, "toolu_fg", self.description(m))
        p = self.pre_edit(self.home.path / "docs" / "x.md", agent_id=AGENT_B)
        p["transcript_path"] = transcript
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.decision), (0, "deny"), r)
        self.assertIn("deliverables", r.reason)
        self.assertNotIn("not bound", r.reason)
        shown = self.home.json("member", "show", m["ref"])["member"]
        self.assertEqual((shown["status"], shown["agent_id"]), ("active", AGENT_B))
        self.assertEqual(self.home.scalar("SELECT agent_id FROM spawn_requests WHERE tool_use_id = 'toolu_fg'"), AGENT_B)
        p = self.pre_edit(self.home.path / "tests" / "x.py", agent_id=AGENT_B)
        p["transcript_path"] = transcript
        r = self.home.hook("PreToolUse", p)
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        p = self.pre_bash("%s --as %s member log hi" % ("python3.14 -I -S %s/bin/spud" % self.home.path, AGENT_B), agent_id=AGENT_B)
        r = self.home.hook("PreToolUse", p)
        self.assertEqual(r.decision, "allow", r)
        status = [e for e in self.events("member.status") if e["member"] == m["ref"]]
        self.assertEqual(status[0]["actor"], "hook:PreToolUse")

    def test_a_forged_meta_cannot_bind_someone_elses_member(self):
        """Rooster's MEDIUM-4: the meta.json must describe the request it names, and the
        harness's subagent files are not writable by spudagents."""
        victim = self.plan(name="Cara", deliverable=["home:tests/**"])
        self.home.hook("PreToolUse", self.pre_agent(self.description(victim), tool_use_id="toolu_victim", run_in_background=False))
        # a meta naming the victim's request but describing something else: no binding
        meta = self.home.path / "projects" / SESSION / "subagents" / ("agent-%s.meta.json" % AGENT_D)
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"agentType": "spudagent", "description": "SPUD-001/Nobody (01, scout)", "toolUseId": "toolu_victim"}), encoding="utf-8")
        transcript = str(self.home.path / "projects" / ("%s.jsonl" % SESSION))
        p = self.pre_edit(self.home.path / "tests" / "x.py", agent_id=AGENT_D)
        p["transcript_path"] = transcript
        r = self.home.hook("PreToolUse", p)
        self.assertEqual(r.decision, "deny")
        self.assertIn("not bound", r.reason)
        self.assertIsNone(self.home.json("member", "show", victim["ref"])["member"]["agent_id"])
        # a meta with a mismatching agentType: no binding either
        meta.write_text(json.dumps({"agentType": "Explore", "description": self.description(victim), "toolUseId": "toolu_victim"}), encoding="utf-8")
        r = self.home.hook("PreToolUse", p)
        self.assertEqual(r.decision, "deny")
        self.assertIsNone(self.home.json("member", "show", victim["ref"])["member"]["agent_id"])
        # the harness's subagent files cannot be written by an agent, bound or not, through the edit tools or a redirection
        bound = self.spawn(self.plan(name="Kestrel"), AGENT_A)
        for who in (AGENT_A, AGENT_D):
            for path in (meta, meta.with_name("agent-%s.jsonl" % who), self.home.path / "projects" / SESSION / "subagents" / "x.txt"):
                r = self.home.hook("PreToolUse", self.pre_edit(path, agent_id=who))
                self.assertEqual((r.code, r.decision), (0, "deny"), (who, str(path), r))
                self.assertIn("harness", r.reason)
            r = self.home.hook("PreToolUse", self.pre_bash("echo x > %s" % meta, agent_id=who))
            self.assertEqual(r.decision, "deny", r)
        r = self.home.hook("PreToolUse", self.pre_edit(meta, agent_id=None))
        self.assertEqual(r.decision, "deny", r)
        # the memory directory beside the sessions (outside the repository) stays writable
        memory = self.home.path.parent / ("%s-projects" % self.home.path.name) / "memory" / "x.md"
        r = self.home.hook("PreToolUse", self.pre_edit(memory, agent_id=AGENT_A))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertEqual(bound["status"], "active")

    def test_no_meta_file_stays_unbound(self):
        m = self.plan()
        self.home.hook("PreToolUse", self.pre_agent(self.description(m), tool_use_id="toolu_fg", run_in_background=False))
        p = self.pre_edit(self.home.path / "tests" / "x.py", agent_id=AGENT_B)
        p["transcript_path"] = str(self.home.path / "projects" / ("%s.jsonl" % SESSION))
        r = self.home.hook("PreToolUse", p)
        self.assertEqual(r.decision, "deny")
        self.assertIn("not bound", r.reason)
        self.assertIsNone(self.home.json("member", "show", m["ref"])["member"]["agent_id"])

    def test_help_and_version_are_well_formed(self):
        m = self.plan()
        self.spawn(m, AGENT_A)
        cli = "python3.14 -I -S %s/bin/spud" % self.home.path
        for tail in ("--help", "-h", "member --help", "member finish --help", "--version"):
            r = self.home.hook("PreToolUse", self.pre_bash("%s %s" % (cli, tail), agent_id=AGENT_A))
            self.assertEqual((r.code, r.decision), (0, "allow"), (tail, r))


class ArgvAnywhereTest(SpudTestCase):
    def test_as_and_json_are_accepted_after_the_subcommand(self):
        t = self.new_ticket("Argv")
        m = self.new_member(t["key"])
        self.home.json("member", "start", m["ref"], actor="spud")
        proc = self.home.run("member", "log", "--as", m["ref"], "hello")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.home.run("member", "result", "produced x", "--as", m["ref"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.home.run("member", "finish", m["ref"], "--status", "done", "--outcome", "fine", "--as", "spud")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.home.run("board", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["ok"])
        proc = self.home.run("member", "show", m["ref"], "--as=spud", "--json")
        self.assertEqual(json.loads(proc.stdout)["member"]["status"], "done")
        # a text value that merely contains the flags is left alone
        out = self.home.json("member", "log", "--as", m["ref"], "ran with --json and --as before")
        self.assertTrue(out["ok"])
        logged = self.home.json("events", "--member", m["ref"], "--kind", "member.log")["events"]
        self.assertEqual(logged[-1]["body"], "ran with --json and --as before")


class ActorEpilogTest(SpudTestCase):
    def test_help_no_longer_defers_to_spd_008(self):
        text = self.home.run("--help").stdout
        self.assertNotIn("Until SPD-008", text)
        self.assertNotIn("unresolvable until then", text)
        self.assertIn("hook", text)
        proc = self.home.run("member", "log", "x", actor=AGENT_D, check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertNotIn("SPD-008", proc.stderr)
        self.assertIn("not bound", proc.stderr)


if __name__ == "__main__":
    unittest.main()
