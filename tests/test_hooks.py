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
import unicodedata
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from helpers import EXIT_ERROR, EXIT_USAGE, PROGRAM, SPUD, SpudTestCase, load_spud_module, real_config


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
        kw.setdefault("deliverable", ["tests/**", "bin/spud"])
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
        the Bash tool shares, and a PATH the line changes, or a relative PATH entry ahead of the match, cannot be vouched for."""
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
        for prefix in ("PATH=%s:$PATH " % other, "PATH=%s; " % other, "export PATH=%s:$PATH; " % other, "path=(%s $path); " % other,
                       "hash -p %s/python3.14 python3.14; " % other, "alias python3.14=%s/python3.14; " % other,
                       "python3.14() {{ %s/python3.14 \"$@\"; }}; " % other):
            with self.subTest(prefix=prefix):
                self.assertSilentForBoth("%spython3.14 -I -S %s {tail}" % (prefix, launcher))
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
                     " > ~/x.pth", " >| %s/x" % self.out):
            with self.subTest(tail=tail):
                self.assertSilentForBoth(cli + " {tail}" + tail)
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
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["**"]), AGENT_C)
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
        """Refused for a member's relative redirection or tee target; Spud's relative target is left unchecked (the hook cannot
        know the directory), an absolute one is checked for both."""
        out = self.out
        for dir_command in ("popd", "popd -n", "pushd", "pushd +1", "pushd -1", "pushd -q %s", "pushd -n %s", "cd -q %s", "cd -s %s", "cd -e %s",
                            "cd -@ %s", "cd -x %s", "cd +1", "cd -1", "cd -", "cd ~-", "cd ~root", "cd ~nosuchuser-spd-030", "cd $DIR",
                            "cd %s/*", "cd /{tmp,var}", "cd a b c", "source x.sh", ". x.sh", "for d in a b; do cd ..; done",
                            "CDPATH=$HOME; cd sub"):
            command = (dir_command.replace("%s", str(out))) + "; echo x > note.txt"
            with self.subTest(command):
                self.assertRefused(command, "cannot follow")
                self.assertRefused(command.replace("echo x > note.txt", "echo x | tee note.txt"), "cannot follow")
                self.assertSilent(command.replace("note.txt", "ledger/tickets/SPD-001.md"), agent_id=None)
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
        self.assertSilent("cd ~ && echo x > ledger/tickets/SPD-001.md")
        self.assertRefused("cd %s && echo x > ~+/ledger/tickets/SPD-001.md" % home, "Law 1", agent_id=None)

    def test_cdpath_sends_a_relative_cd_elsewhere(self):
        """bash tries CDPATH before the current directory, zsh after it; a target starting with / ./ or ../ skips it."""
        home, out = self.home.path, str(self.out)
        for setting in ("CDPATH=%s cd ledger", "CDPATH=%s; cd ledger", "export CDPATH=%s; cd ledger", "cdpath=(%s); cd ledger",
                        "CDPATH=/nowhere:%s; cd ledger"):
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


class GlobRedirectTest(BashHookCase):
    """SPD-034: a redirection or tee target the shell expands (an unquoted glob character, a brace list) is checked as
    every file it can open from every candidate directory, not as its literal spelling.  bash and zsh both expand a
    glob in a redirection target before opening it, so `echo x > ledg*/tickets/SPD-00?.md` writes ledger/tickets/SPD-001.md
    (probed in zsh 5.9 and bash 3.2).  A quoted or escaped glob character is literal.  A member is refused when the hook
    cannot know what a glob opens (no match now, the cost bound reached); Spud is checked against every match and the
    literal name bash writes when nothing matches.  AGENT_A plans tests/** and bin/spud; AGENT_C plans **."""

    def setUp(self):
        super().setUp()
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["**"]), AGENT_C)
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
        # cwds unknown (a cd the hook cannot follow) plus a glob: refused for a member, unchecked for Spud.
        self.assertRefused("cd $DIR; echo x > ledger/tickets/SPD-00?.md", "cannot follow", AGENT_C)
        self.assertSilent("cd $DIR; echo x > ledger/tickets/SPD-00?.md", agent_id=None)

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
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["**"]), AGENT_C)
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
        self.assertSilent("echo x > tests/<1-1>/../../docs/x.md", AGENT_C)

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
        for ok in ("f() { echo hi; }; f", "arr=(a b); echo $arr", "typeset -a arr=(a b)", "x=$(( 1<2 )); (( 3 > 2 )) && echo y",
                   "[[ -n x && ( -d tests ) ]] && echo y", "case x in (x|y) echo y;; esac", "for f in tests/(keep|other).py; do echo $f; done",
                   "noglob echo (a|b)", "echo a |(cat)", "! (true)", "{ (true) }"):
            with self.subTest(ok):
                self.assertSilent(ok)
        self.assertRefused("case x in (x) git push;; esac", "Law 7")
        self.assertAllowed("%s --as %s member log 'a (b|c) <1-2>'" % (self.spud_cli, AGENT_A))


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
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["**"]), AGENT_C)
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
                self.assertSilent(cmd, agent_id=None)
        self.assertSilent("X=g?t; echo $X")  # an assignment's value is not expanded, and the variable is only echoed

    def test_a_verb_glob_matching_a_file_the_line_creates(self):
        """Decision: the verb is read as every write verb its pattern can match, whether or not a file matches now (the line may
        create it), and a pattern that can match no write verb is read as spelled."""
        for cmd in ("touch push; git p?sh", "touch push && git p*", "git p?sh", "git pu[s]h", "git p(u|x)sh", "touch commit; git c?mmit -m x",
                    "git ch?ckout main", "git st?sh", "git re[s]et --hard", "git st(a|x)sh pop", "git worktree a?d ../x", "git branch -[D] x"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")
        self.assertIn("git push", self.assertRefused("touch push; git p?sh", "Law 7").reason)
        for ok in ("git st?tus", "git l?g --oneline", "git d[i]ff", "git sh(o|x)w HEAD", "touch status; git st*tus"):
            with self.subTest(ok):
                self.assertSilent(ok)

    def test_brace_lists_in_the_command_word_and_the_verb(self):
        for cmd in ("{git,push}", "{/usr/bin/git,push}", "git {push,status}", "command {git,push}", "{env,git} push", "git {-C,.} push",
                    "{/usr/bin/g?t,push}"):
            with self.subTest(cmd):
                self.refused_for_members(cmd, "Law 7")
                self.assertSilent(cmd, agent_id=None)
        # git status push; git gxt push; git x push; git /usr/bin/x push (probed: the second word is the verb)
        for ok in ("git {status,push}", "g{i,x}t push", "{git,x} push", "/usr/bin/{git,x} push"):
            with self.subTest(ok):
                self.assertSilent(ok)

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
        cmd = "c? %s/ledger && echo x > tickets/SPD-001.md" % home
        self.assertRefused(cmd, "generated", AGENT_C, cwd=str(out))
        self.assertRefused(cmd, "Law 1", agent_id=None, cwd=str(out))
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
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["**"]), AGENT_C)
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
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["**"]), AGENT_C)

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
        for ok in ("git status", "git log --oneline -5", "git diff", "git -C /tmp status", "git show HEAD"):
            with self.subTest(ok):
                self.assertSilent(ok)
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
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["**"]), AGENT_C)

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
                   "git grep -i foo", "git grep -ni foo", "git difftool A B", "git difftool --no-prompt A B",
                   "git difftool -d A B", "git archive HEAD", "git grep -- -Ofoo",  # after --, -Ofoo is a pattern, not grep -O
                   # options adjacent to a program option but not a prefix of it stay silent
                   "git ls-remote --tags .", "git fetch --unshallow r", "git fetch --update-head-ok r",
                   "git --exec-path", "git --exec-path status"):  # bare --exec-path prints the path, it does not set one
            with self.subTest(ok):
                self.assertSilent(ok)
                self.assertSilent(ok, agent_id=None)


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
        self.lead = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["tests/**", "bin/spud", "docs/x/*.md", "notes/"]), AGENT_A)
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

    def test_case_variants_cannot_reach_generated_files_or_spuds_set(self):
        """Rooster's HIGH-2: the protected roots are matched whatever the case, on every filesystem."""
        home = self.home.path
        wide = self.spawn(self.plan(deliverable=["**"]), AGENT_B)
        for bad in ("Ledger/tickets/SPD-001.md", "LEDGER/x.md", "Reports/x.md", "Ledger/Home.md", "ledger/Tickets/SPD-001.md"):
            self.assertRefused(home / bad, "generated", agent_id=AGENT_B)
        self.assertSilent(home / "docs" / "x.md", agent_id=AGENT_B)
        self.assertSilent(home / "CLAUDE.md", agent_id=AGENT_B)  # a ** member may write CLAUDE.md: Law 1 binds Spud, not members
        if case_insensitive_fs(home):
            self.assertSilent(home / "Ledger" / "Home.md", agent_id=None)  # the same file as Spud's ledger/Home.md
            self.assertRefused(home / "Ledger" / "Tickets" / "x.md", "generated", agent_id=None)
        self.assertEqual(wide["deliverables"], ["**"])

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

    def test_outside_every_project_root_is_allowed(self):
        for p in ("/Users/eric/.claude/projects/-Users-eric-Personal-Spud/memory/x.md", "/tmp/claude-501/x/scratchpad/notes.md", "/etc/hosts", str(self.home.path.parent / "elsewhere.md"), str(self.home.path) + "-sibling/x.md"):
            self.assertSilent(p)
            self.assertSilent(p, agent_id=None)

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
    that cannot be read fails the enforcing hook closed."""

    def setUp(self):
        super().setUp()
        self.lead = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["tests/**", "bin/spud"]), AGENT_A)
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

    def test_the_state_directory_holds_at_the_home_and_at_a_worktree_elsewhere(self):
        """SPD-031: the worktree cache this class exercises, which the hook itself writes, and a worktree elsewhere's own state
        directory, refused to a member whose glob is ** and to Spud."""
        self.spawn(self.plan(persona="engineer", model="opus", deliverable=["**"]), AGENT_B)
        self.assertSilent(self.elsewhere / "tests" / "x.py", agent_id=AGENT_B)  # lists the worktrees, writing the cache
        cache = self.home.path / STATE / "worktrees.json"
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
        self.lead = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["tests/**", "bin/spud"]), AGENT_A)
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
        self.spawn(self.plan(deliverable=["**"]), AGENT_B)
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
        self.lead = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["tests/**", "bin/spud"]), AGENT_A)

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
        self.wide = self.spawn(self.plan(persona="engineer", model="opus", deliverable=["**"]), AGENT_A)
        self.named = self.spawn(self.plan(persona="engineer", model="opus", deliverable=[STATE + "/**", STATE + "/ledger.db"]), AGENT_B)
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
        self.assertEqual((self.wide["deliverables"], self.named["deliverables"]), (["**"], [STATE + "/**", STATE + "/ledger.db"]))
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


class DeliverableGlobTest(SpudTestCase):
    def test_member_new_validates_deliverables(self):
        t = self.new_ticket("Globs")
        for bad in ("../x", "/abs/path", "tests/../x", "", "  "):
            proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", "--deliverable", bad, actor="spud", check=False)
            self.assertEqual(proc.returncode, EXIT_ERROR, bad)
            self.assertIn("deliverable", proc.stderr)
        m = self.new_member(t["key"], deliverable=["./tests/**", "docs/", "bin/spud"])
        self.assertEqual(m["deliverables"], ["tests/**", "docs/**", "bin/spud"])
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
        self.assertTrue(match("a/b?c", "a/b[?]c"))
        self.assertFalse(match("Tests/a.py", "tests/**"))


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
        for stmt in ("INSERT INTO name_pool (name) VALUES ('X')", "UPDATE tickets SET title = 'x'", "DELETE FROM events", "PRAGMA user_version = 5", "CREATE TABLE x (a)", "DROP TABLE renders", "PRAGMA query_only = OFF; INSERT INTO name_pool (name) VALUES ('X')", "ATTACH '/tmp/x.db' AS x"):
            proc = self.home.run("sql", "--readonly", stmt, check=False)
            self.assertEqual(proc.returncode, EXIT_ERROR, stmt)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM name_pool WHERE name = 'X'"), 0)
        self.assertEqual(self.home.scalar("PRAGMA user_version"), 1)

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
        m = self.plan(deliverable=["tests/**"])
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
        victim = self.plan(name="Cara", deliverable=["tests/**"])
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
