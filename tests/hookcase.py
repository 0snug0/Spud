"""spud hook <event>: the six harness events (SPD-008).

Payload shapes follow the verbatim captures of the ledger-database spike of 2026-09-12 (its Enforcement plan, facts 1
to 9; Claude Code 2.1.269), which went to Spud's home with docs/ at SPD-097, and the hooks reference.  Every run is
against a scratch SPUD_HOME.  Enforcing hooks (PreToolUse for Agent, Bash and the edit tools)
fail closed: a planned refusal is `permissionDecision: deny` on exit 0, anything unexpected is
exit 2.  Recording hooks (PostToolUse for Agent, SubagentStart, SubagentStop, SessionStart) fail
open: exit 0 whatever happens, the gap spooled and drained later as a `hook.error` event.

This module is what the hook tests share (SPD-231): the payload builders and HookCase, BashHookCase, and the
constants and wordings more than one test module reads.  The tests are in tests/test_hooks_<subject>.py, one subject
each.
"""

import contextlib
import copy
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import types
from pathlib import Path
from unittest import mock

from helpers import HOOK_CACHES, HOOK_MEMOS, PURE_TABLES, Home, HookResult, Snapshot, SpudTestCase, git, load_spud_module, spawn_type


def case_insensitive_fs(path):
    swapped = str(path).swapcase()
    return swapped != str(path) and os.path.exists(swapped) and os.path.samefile(str(path), swapped)

SESSION = "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b"
TRANSCRIPT = "/Users/Someone/.claude/projects/-Users-Someone-Personal-Spud/%s.jsonl"
AGENT_A = "ac8c90dafa6697045"  # the spike's background probe
AGENT_B = "adb9ecf5d69362ddd"  # the spike's foreground probe
AGENT_C = "a0cfc2d597e041e6b"
AGENT_D = "0123456789abcdef0"
# SPD-145: a member's shell whose commands come from a file -- a script operand, `source`, a path run as a command, standard
# input the line does not spell -- refused, fail closed (shell/script_files; ScriptFileTest)
SCRIPT_WORDING = "the hook reads no file's commands"

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
        "scratchpad_dir": "/tmp/claude-501/-Users-Someone-Personal-Spud/%s/scratchpad" % session,
    }
    if agent_id:
        d["agent_id"] = agent_id
        d["agent_type"] = agent_type or "spudagent"
    return d


# =============================================================================
# The in-process hook (SPD-231)
# =============================================================================
#
# A `spud hook PreToolUse` process costs about 0.03 s of CPU, and the Bash reader's tests ran tens of thousands of them,
# each in a home twelve more processes built: two thirds of every suite run.  The hook is bin/spud's main called with the
# payload on standard input, so a test can call that main in its own process instead, for about a tenth of the cost, and
# get the same answer as long as the call starts where a process starts: with the home's environment as the whole of
# os.environ, its own standard input and working directory, and nothing a run before it left behind.  HOOK_CACHES names
# what a run leaves behind -- the per-process caches of the hook path -- and fresh_process empties them before every call.
# InProcessParityTest asserts that the two runs answer alike, byte for byte, for a sample that reaches every law and every
# family of reason, and that no table on the hook path other than these changes during a run: none a module holds, and
# none its functions and classes keep (defaults, closure cells, attributes, followed into the objects they hold).

ENTRY = load_spud_module()  # bin/spud_ledger.py, whose main the launcher calls; never patched here (PatchTargetTest)

# HOOK_CACHES, HOOK_MEMOS and PURE_TABLES (imported above) are tests/helpers' since SPD-233: a home restored at the fixture's
# path must forget what an in-process call cached about that path, whatever class made the call.


def fresh_process():
    """Empty every per-process cache on the hook path, as a new `spud hook` process starts with none."""
    for module, name in HOOK_CACHES:
        getattr(importlib.import_module(module), name).clear()
    setattr(importlib.import_module("spudlib.shell.git_verbs"), "_GIT_OWN_COMMANDS", None)
    importlib.import_module("spudlib.shell.globbing").glob_sample_matches.cache_clear()


def run_main(env, argv, stdin=None, cwd=None):
    """(exit code, stdout, stderr) of bin/spud's main for `argv`, run in this process the way the launcher runs it in a process
    of its own: `env` is the whole environment, `stdin` (text, or None for none) its standard input, `cwd` its working
    directory (None: this process's), and every per-process cache of the hook path starts empty.  An exception main does not
    catch ends it as it ends the process: a traceback on stderr and exit 1."""
    fresh_process()
    out, err = io.StringIO(), io.StringIO()
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
        stack.enter_context(mock.patch.object(sys, "stdin", io.StringIO(stdin or "")))
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(err))
        if cwd is not None:
            stack.enter_context(contextlib.chdir(str(cwd)))
        try:
            code = ENTRY.main(argv)
        except SystemExit as e:  # argparse's usage errors
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            if isinstance(e.code, str):
                err.write(e.code + "\n")
        except Exception:
            traceback.print_exc(file=err)
            code = 1
    return code, out.getvalue(), err.getvalue()


# -- the leak guard (SPD-231; past the Bash and edit hooks, SPD-241) ----------------------------------------------------
#
# run_main answers as a process only while nothing a run changes outlives it but what fresh_process empties.  program_tables
# reads every table the program holds; tables_changed imports the whole program anew (fresh_program), reads the tables,
# runs a sample, reads them again and names what changed.  A guard asserts that nothing changed outside LEAK_ALLOWED:
# InProcessParityTest over the Bash and edit hooks' sample, test_hookcase.InProcessLeakTest over every other hook event's
# and the CLI commands the in-process test classes call.  A table a later change adds on any of those paths fails the
# guard until fresh_process (and helpers.forget_process_caches) empties it, or PURE_TABLES says why no later run can read
# it differently.

LEAK_ALLOWED = frozenset(HOOK_CACHES) | frozenset(HOOK_MEMOS) | frozenset(PURE_TABLES)


def table_shape(value, depth=0):
    """What a table holds, down to the objects in it: a container's contents as they are now, a scalar by its value, a
    functools cache by its size, an object by its identity and, while depth allows (four levels), what its __dict__ holds.
    An object with no __dict__ (a __slots__ class) is kept by identity only, so a memo inside one is not seen."""
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return value
    if hasattr(value, "cache_info"):
        return ("lru_cache", value.cache_info().currsize)
    if isinstance(value, importlib.import_module("spudlib.core.lazy").LazyPattern):  # binds its methods at first use
        return ("LazyPattern", value._args)
    if depth >= 4:
        return ("object", id(value))
    if isinstance(value, dict):
        return ("dict", tuple((k, table_shape(v, depth + 1)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return (type(value).__name__, tuple(table_shape(v, depth + 1) for v in value))
    if isinstance(value, (set, frozenset)):
        return (type(value).__name__, frozenset(value))
    if isinstance(value, bytearray):
        return ("bytearray", bytes(value))
    if isinstance(value, (types.ModuleType, type)) or not hasattr(value, "__dict__"):
        return ("object", id(value))
    return ("object", id(value), table_shape(dict(vars(value)), depth + 1))


def function_tables(fn):
    """What a function holds between calls: its defaults, closure cells and attributes (a decorator's __wrapped__ aside,
    which is the function itself)."""
    cells = []
    for cell in fn.__closure__ or ():
        try:
            cells.append(cell.cell_contents)
        except ValueError:  # a cell not filled yet
            cells.append("<empty>")
    own = {k: v for k, v in vars(fn).items() if k != "__wrapped__"}
    return {"__defaults__": fn.__defaults__, "__kwdefaults__": fn.__kwdefaults__, "__closure__": tuple(cells), "__dict__": own}


def own_tables(prefix, value, module):
    """(key, what it holds) for a name `prefix` a module holds: a function or class of the module's own is walked for the
    tables it keeps (a class's attributes, its methods' defaults, cells and attributes), a functools cache and any other
    value that is no callable is a table itself."""
    if isinstance(value, types.ModuleType):
        return
    fn = value.__func__ if isinstance(value, (staticmethod, classmethod)) else value
    if isinstance(fn, types.FunctionType):
        if fn.__module__ == module:
            for key, held in function_tables(fn).items():
                yield "%s.%s" % (prefix, key), held
        return
    if isinstance(value, type):
        if value.__module__ == module:
            for attr, member in vars(value).items():
                if not (attr.startswith("__") and attr.endswith("__")) and not isinstance(member, type):
                    yield from own_tables("%s.%s" % (prefix, attr), member, module)
        return
    if isinstance(value, property) or (callable(value) and not hasattr(value, "cache_info")):
        return
    yield prefix, value


def program_tables():
    """{(module, key): table_shape} over every table the program holds: each spudlib module this process has loaded and the
    entry, bin/spud_ledger.py (ENTRY), by the names they hold (own_tables).  spudlib.core.lazy is left out: it rebinds its
    own imports and holds no state."""
    modules = [(name, module) for name, module in list(sys.modules.items())
               if name.startswith("spudlib.") and name != "spudlib.core.lazy"] + [(ENTRY.__name__, ENTRY)]
    held = {}
    for name, module in modules:
        for attr, value in list(vars(module).items()):
            if not attr.startswith("__"):
                for key, table in own_tables(attr, value, name):
                    held[(name, key)] = table_shape(table)
    return held


def program_modules():
    """The names of spudlib and every module of it this process has loaded."""
    return [name for name in sys.modules if name == "spudlib" or name.startswith("spudlib.")]


@contextlib.contextmanager
def fresh_program():
    """The program imported anew for the block, every module of it, as a process that has run nothing holds it; the
    modules this process had before are put back after, untouched by what the block ran.  Every import inside the block,
    run_main's and a mock.patch target's alike, gets the new modules, so a patch that must reach the run starts inside."""
    held = {name: sys.modules.pop(name) for name in program_modules()}
    try:
        root = Path(ENTRY.__file__).resolve().parent / "spudlib"
        for directory, subdirs, files in os.walk(root):  # ENTRY.owners' order, which the guarded cycle in shell/ imports in
            subdirs[:] = sorted(d for d in subdirs if d.isidentifier() and not d.startswith("__"))
            for f in sorted(files):
                if f.endswith(".py"):
                    rel = os.path.relpath(os.path.join(directory, f[:-3]), root)
                    importlib.import_module("spudlib." + rel.replace(os.sep, "."))
        yield
    finally:
        for name in program_modules():
            del sys.modules[name]
        sys.modules.update(held)


def tables_changed(run, prepare=None):
    """The keys of program_tables that a call of `run` changes, run against the program freshly imported (fresh_program):
    every table starts as the import left it, whatever this process ran before -- an earlier test's run of the same
    sample would otherwise have filled a memo already, under the same keys, and the run would change nothing the walk
    could see -- and no module a run imports for the first time can hide a table it fills.  `prepare`, when given, is
    called on the new program before the first reading (test_hookcase plants a table with it)."""
    with fresh_program():
        if prepare is not None:
            prepare()
        before = program_tables()
        run()
        after = program_tables()
    return {key for key in before.keys() | after.keys() if before.get(key, "absent") != after.get(key, "absent")}


class InProcessHome(Home):
    """A Home whose CLI runs in this process (run_main): run(), and with it json() and hook(), answer as bin/spud would in a
    process of its own.  process() and hook_process() run the launcher itself, for the tests that keep the subprocess."""

    def run(self, *args, check=True, actor=None, stdin=None, cwd=None):
        argv = (["--as", actor] if actor is not None else []) + [str(a) for a in args]
        code, out, err = run_main(self.env, argv, stdin, cwd)
        if check and code != 0:
            raise AssertionError("spud %s exited %d (in process)\nstdout: %s\nstderr: %s" % (" ".join(argv), code, out, err))
        return subprocess.CompletedProcess(["spud"] + argv, code, out, err)

    def process(self, *args, **kw):
        """The CLI as a process of its own: Home.run."""
        return Home.run(self, *args, **kw)

    def hook_process(self, event, payload):
        """`spud hook <event>` as a process of its own: Home.hook."""
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        proc = self.process("hook", event, check=False, stdin=stdin)
        return HookResult(proc.returncode, proc.stdout, proc.stderr)


class ClassHome(Snapshot):
    """A class's home as its first test built it (SPD-231): a Snapshot of the InProcessHome's root taken after build_home,
    with the attributes the build set on the test, each put back before every later test of the class.  Its first test
    starts from the process's fixture (helpers.fixture, SPD-233), so the class home lives at the fixture's path."""

    def __init__(self, home, attrs):
        super().__init__(home)
        self.attrs = copy.deepcopy(attrs)

    def restore(self, test):
        test.home = super().restore()
        for name, value in copy.deepcopy(self.attrs).items():
            setattr(test, name, value)

    def cleanup(self):
        self.home.cleanup()  # a home of its own (warm_cache False); the fixture's is the fixture's to remove
        super().cleanup()


class HookCase(SpudTestCase):
    """Builders for the payloads the harness sends, plus a planned team to spawn.

    SPD-231: a class that sets `in_process` runs its CLI calls and hooks in this process (InProcessHome) against one home per
    class: its first test builds it exactly as setUp builds one for every test of any other class, and each later test
    starts from that home restored (ClassHome).  What a subclass's own setUp adds after super().setUp() is added per test, as
    before.  A test that must reach the launcher itself uses self.home.process() or self.home.hook_process().

    SPD-233: the class home is the process's fixture (helpers.fixture) with build_home run over it, so it lives at the
    fixture's path, and the snapshot restores the fixture's root -- the home, the tool, and whatever else build_home put
    under `self.home.root`.  A repository or directory build_home makes elsewhere (tempfile, RepoMixin.scratch_dir) is no
    part of the snapshot, and the first test's cleanups remove it: build it under self.home.root instead."""

    in_process = False
    # The fixture's project spud has init's default sessions, `claim`, as the live one does: a session in the tool or a
    # worktree of it is Spud's only once claimed.  A class whose payloads run Spud's calls there and read them as Spud's
    # sets this, and build_home makes every session there Spud's (`--sessions always`) rather than claim one per test.
    tool_sessions_always = False

    @classmethod
    def tearDownClass(cls):
        held = cls.__dict__.get("class_home")
        if held is not None:
            cls.class_home = None
            held.cleanup()
        super().tearDownClass()

    def setUp(self):
        if not self.in_process:
            super().setUp()
            self.build_home()
            return
        held = type(self).__dict__.get("class_home")
        if held is not None:
            held.restore(self)
            return
        before = set(vars(self))
        self.home = self.fresh_home(InProcessHome)
        try:
            self.build_home()
            type(self).class_home = ClassHome(self.home, {k: v for k, v in vars(self).items() if k not in before and k != "home"})
        except BaseException:
            self.home.cleanup()
            raise

    def decide(self, payload):
        """PreToolUse's answer to a payload, run as the class runs its hooks."""
        return self.home.hook("PreToolUse", payload)

    def build_home(self):
        """What every hook test's home holds before its first payload."""
        self.cwd = str(self.home.path)
        if self.tool_sessions_always:
            self.home.json("project", "edit", "spud", "--sessions", "always", actor="spud")
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
            "agent_transcript_path": transcript or str(self.transcript_root() / SESSION / "subagents" / ("agent-%s.jsonl" % agent_id)),
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
    def transcript_root(self):
        """Where the transcripts of a test are written: under its home, or, for a test that builds none (a class whose setUp
        does not call HookCase's, SPD-233), in a scratch directory of its own."""
        home = getattr(self, "home", None)
        if home is not None:
            return home.path / "transcripts"
        if "_transcripts" not in self.__dict__:
            self._transcripts = Path(tempfile.mkdtemp(prefix="spud-transcripts-")).resolve()
            self.addCleanup(shutil.rmtree, self._transcripts, True)
        return self._transcripts

    def write_transcript(self, agent_id, messages):
        """The subagent's own transcript, where sub_stop's agent_transcript_path points by default."""
        path = self.transcript_root() / SESSION / "subagents" / ("agent-%s.jsonl" % agent_id)
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
    # SPD-097: these members write inside the home, which is no project, so their globs name it: `home:<glob>`.  A bare glob
    # is relative to the ticket's project checkout, the tool (project spud) here; ProjectCheckoutCase's members write there.
    deliverables = ("home:tests/**", "home:bin/spud")

    def plan(self, actor="spud", persona="scout", model="haiku", name=None, **kw):
        kw.setdefault("deliverable", list(self.deliverables))
        if name:
            kw["name"] = name
        return self.new_member(self.t["key"], actor=actor, persona=persona, model=model, **kw)

    def description(self, m):
        return "%s/%s (%s, %s)" % (self.team, m["name"], m["lineage"], m["persona"])

    def spawn(self, m, agent_id, caller=None, tool_use_id=None, model=None, session=SESSION):
        """PreToolUse(Agent) allow followed by the background PostToolUse binding, all in session."""
        tool_use_id = tool_use_id or ("toolu_" + agent_id)
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(m), model=model or m["model"], subagent_type=spawn_type(m), agent_id=caller, tool_use_id=tool_use_id, session=session))
        self.assertEqual((pre.code, pre.decision), (0, "allow"), pre)
        st = self.home.hook("SubagentStart", self.sub_start(agent_id, spawn_type(m), session=session))
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
        pre = self.home.hook("PreToolUse", self.pre_agent(description, model=m["model"], subagent_type=spawn_type(m), tool_use_id=tool_use_id, run_in_background=False))
        self.assertEqual((pre.code, pre.decision), (0, "allow"), pre)
        self.assertEqual(self.home.hook("SubagentStart", self.sub_start(agent_id, spawn_type(m))).code, 0)
        transcript = self.write_transcript(agent_id, self.two_requests() if entries is None else entries)
        transcript.with_name("agent-%s.meta.json" % agent_id).write_text(json.dumps({
            "agentType": spawn_type(m), "description": description, "toolUseId": tool_use_id, "spawnDepth": 1,
            "requestShape": "foreground", "requestNonInteractive": False, "model": m["model"]}), encoding="utf-8")
        first_call = self.pre_bash("ls", agent_id=agent_id)
        first_call["transcript_path"] = str(self.transcript_root() / ("%s.jsonl" % SESSION))
        self.assertEqual(self.home.hook("PreToolUse", first_call).code, 0)
        self.home.json("member", "result", "Built it.", actor=agent_id)
        events = [("SubagentStop", self.sub_stop(agent_id, agent_type=spawn_type(m), transcript=str(transcript))),
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


class InProcessCase(HookCase):
    """A SpudTestCase class whose CLI calls run in this process (SPD-242): HookCase's in-process class home with nothing built
    over the fixture -- no ticket, no session -- so each test starts from the fixture's home, as a SpudTestCase's does, and
    its calls answer as bin/spud's own process would (run_main).  A subclass that wants rows before every test makes them
    in build_home, once per class.  HookCase rather than SpudTestCase, because the leak guard reads the commands it must
    sample from the in-process HookCase classes (test_hookcase.in_process_commands): a class that runs the CLI in process
    is one it reads."""

    in_process = True

    def build_home(self):
        pass


# =============================================================================
# PreToolUse / Bash
# =============================================================================


# The fields of the analysis (shell/syntax.ShellAnalysis) PreToolUse(Bash)'s decision reads, which hook_reading compares:
# test_hookcase.HookReadingCoverTest reads shell/bash_rule and hooks/pretool and fails when the hook reads one not named
# here, or uses the raw command text beyond its database match (SPD-231).
HOOK_READING = ("findings", "redirects", "git_calls", "git_writes", "arg_writes", "unparseable", "shell_expanded", "all_spud")


class BashHookCase(HookCase):
    """Two engineers planned with tests/** and bin/spud (AGENT_A the lead, AGENT_B the other), and PreToolUse(Bash) asserts,
    in process against the class's home (SPD-231); InProcessParityTest, and the HookCase classes that leave in_process unset
    (FailurePolicyTest and LateBindingTest among them), keep the hook's own process."""

    in_process = True

    def build_home(self):
        super().build_home()
        self.lead = self.spawn(self.plan(persona="engineer", model="opus"), AGENT_A)
        self.other = self.spawn(self.plan(persona="engineer", model="opus"), AGENT_B)
        self.spud_cli = "python3.14 -I -S %s" % self.home.launcher

    def bash(self, command, agent_id=AGENT_A, cwd=None):
        return self.decide(self.pre_bash(command, agent_id=agent_id, cwd=cwd))

    def hook_reading(self, command, cwd=None):
        """Everything PreToolUse(Bash)'s answer to `command` is a function of, beside the caller and the ledger: whether the
        raw text names the database (hookio.DB_PATH_RE, read before anything else) and the fields of the analysis
        bash_refusal and hook_bash read, HOOK_READING, which test_hookcase.HookReadingCoverTest holds to the hook's source
        -- the findings in their order, the redirections, git calls, git writes and writes
        by argument with the directories each may open in, what the hook could not tokenize, what the shell's own aliases
        and functions expanded, and whether every command is a spud call (the allow's condition, so a form whose own
        commands -- a `true`, a `[[ ]]` -- are none reads apart from the spud call it holds).  Made as bash_refusal makes
        it: the Ctx main builds from the home's environment, the payload's cwd (the home by default), in this process with
        every cache fresh (SPD-231).  Each list keeps its order with a repeat dropped: both of a line's readings (zsh's
        and the other shell's) record what they share, and the rule's answer is the first refusal an entry earns in that
        order, which a repeat of an earlier entry cannot change.  Two lines that read alike get the same answer from the
        hook for every caller (InProcessParityTest.test_lines_that_read_alike_are_answered_alike checks it on the hook), so
        a form that reads as the bare command it wraps needs no answer of its own: the bare command's is its."""
        fresh_process()
        analyse = importlib.import_module("spudlib.shell.analyse")
        syntax = importlib.import_module("spudlib.shell.syntax")
        homeconf = importlib.import_module("spudlib.core.homeconf")
        hookio = importlib.import_module("spudlib.hooks.hookio")
        with mock.patch.dict(os.environ, self.home.env, clear=True):
            home, how = homeconf.resolve_home(os.environ)
            ctx = homeconf.Ctx(home, how, False)
            a = analyse.analyse_command(command, syntax.ShellAnalysis(cwd=cwd or self.cwd, home=str(ctx.home), launcher=str(ctx.launcher)))

        def once(entries):
            kept = []
            for entry in entries:
                if entry not in kept:
                    kept.append(entry)
            return kept

        reading = {"database": hookio.DB_PATH_RE.search(command) is not None}
        for field in HOOK_READING:
            value = getattr(a, field)
            reading[field] = once(value) if isinstance(value, list) else value
        return reading

    def assertAnswers(self, line, answers, cwd=None):
        """Each (caller, needle) of `answers`: the caller refused `line` with the needle in the reason, or, where the needle
        is None, left silent."""
        for caller, needle in answers:
            with self.subTest(line=line, caller=caller):
                if needle is None:
                    self.assertSilent(line, caller, cwd)
                else:
                    self.assertRefused(line, needle, caller, cwd)

    def assertAnsweredAs(self, line, command, answers, cwd=None):
        """`line`, a form holding `command`, earns the command's `answers` [(caller, needle, or None for silence)] (SPD-231).
        Where the line reads as the command alone (hook_reading), the construct reaches its body and the hook answers the two
        alike for every caller, so the answers are asserted on the command, once per test however many forms hold it;
        where it does not -- the form adds a reading of its own (bash keeping a brace glued to a word), a redirection or
        an assignment before a spud call -- they are asserted on the line itself, as they were per form before."""
        if self.hook_reading(line, cwd) != self.hook_reading(command, cwd):
            self.assertAnswers(line, answers, cwd)
            return
        answered = self.__dict__.setdefault("_answered", set())
        key = (command, cwd, tuple(answers))
        if key not in answered:
            answered.add(key)
            self.assertAnswers(command, answers, cwd)

    def assertPayloadsAnswered(self, form, member_payloads, spud_payloads, members=(AGENT_C, AGENT_A), cwd=None):
        """Every payload in `form` earns its own answers: each (command, needle) of `member_payloads` refuses every member in
        `members` for its reason, each of `spud_payloads` refuses Spud (assertAnsweredAs, which asserts an answer on the
        form only where the form does not read as the payload alone)."""
        for command, needle in member_payloads:
            self.assertAnsweredAs(form % command, command, [(m, needle) for m in members], cwd)
        for command, needle in spud_payloads:
            self.assertAnsweredAs(form % command, command, [(None, needle)], cwd)

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


class ProjectCheckoutCase(BashHookCase):
    """A BashHookCase whose shell is in a checkout of project spud, the tool beside the home (SPD-233): where a repository
    script, a runner's files and a project's own programs live, which the home -- no project -- never holds.  The ticket is
    bound to `checkout`, a linked worktree of the tool, by planning AGENT_A and AGENT_B from it with tests/** and bin/spud,
    as a session that entered the worktree plans them (SPD-098), and every payload's cwd is that worktree."""

    deliverables = ("tests/**", "bin/spud")
    tool_sessions_always = True  # every payload's shell is in the worktree, and Spud's calls there are his without a claim

    def build_home(self):
        self.checkout = self.home.tool / ".claude" / "worktrees" / "spd-001-hooks"
        git(self.home.tool, "worktree", "add", "-q", "-b", "worktree-spd-001-hooks", self.checkout)
        super().build_home()
        self.cwd = str(self.checkout)

    def plan(self, *args, **kw):
        kw.setdefault("cwd", self.checkout)
        return super().plan(*args, **kw)


def spellings(word):
    """A word as the shell sees it and as a case-insensitive PATH lookup finds it (SPD-030): lower, upper, mixed."""
    return (word, word.upper(), "".join(c.upper() if i % 2 == 0 else c for i, c in enumerate(word)))


# SPD-063: the refusal needles.  (a) a file git reads with nothing on the line; (b) a program-naming key in force at the
# target repository's local or worktree scope.
GIT_FILE_WORDING = "git reads with nothing on the line"
GIT_SCOPE_WORDING = "scope (the repository in"
# SPD-066: (1) a path with a .git component; (2) a repository that is not the own repository of a checkout the ledger knows.
GIT_DIR_WORDING = "is inside a git directory"
GIT_NESTED_WORDING = "not the own repository of a checkout the ledger knows"
# SPD-123: an entry of a repository's hooks directory that is not a *.sample file, for every caller the check runs for; and
# Spud's own refusal, which names what it found and leaves inspecting and removing it to Eric.
GIT_HOOK_WORDING = "not a *.sample file"
SPUD_PLANTED_WORDING = "is Eric's call"


def plant_git_dir(path, config="[core]\n\trepositoryformatversion = 0\n"):
    """A git directory built by hand, as a member could build one without git init (a write verb): HEAD, objects/, refs/
    and a config.  Put at <dir>/.git it makes <dir> a work tree; anywhere else it is a bare layout git discovers."""
    (path / "objects").mkdir(parents=True)
    (path / "refs" / "heads").mkdir(parents=True)
    (path / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (path / "config").write_text(config, encoding="utf-8")
    return path


RUNNER_WORDING = "is a script runner, which runs commands a project file holds"


# The wording every refusal of an inline program shares (SPD-175): the rule it holds a member to, which is the spudagent
# definition's glob rule and no law of Spud's, and then one of three reasons the hook gives.
INLINE_WORDING = "the deliverable paths your parent planned"
VARIABLE_WORDING = "spell the path out"  # an unresolvable target, for a redirection and a write by argument alike
WORD_WORDING = "spell the words out"  # SPD-043's var-word: an expansion in a word the hook reads by name


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


# The refusal a caller with an agent_id gets for a path outside every registered project (SPD-064).
OUTSIDE = "outside every registered project"
