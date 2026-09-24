"""tests/hookcase.py's own premises, checked against the hook's source (SPD-231).

BashHookCase.assertAnsweredAs lets a payload's answers stand for a form that holds it wherever the two read alike, and
"alike" is hook_reading: the raw text's database match and the analysis fields named in hookcase.HOOK_READING.  That is
only sound while the hook's decision is a function of nothing else, so HookReadingCoverTest reads the Bash hook's decision
path -- shell/bash_rule and hooks/pretool -- and fails when it reads a field of the analysis HOOK_READING does not compare,
hands the analysis somewhere this test cannot follow, or uses the raw command text beyond the uses tabled here.  A change
that trips it adds the field to HOOK_READING (the matrices then assert more forms on the line), or tables the new use of
the text with why it cannot tell a form from the command it holds.

run_main answers as a process only while no table a run changes outlives it but what fresh_process empties (hookcase's
leak guard).  InProcessLeakTest runs that guard over every hook event but the Bash and edit hooks' (InProcessParityTest
covers those) and over the CLI commands the in-process test classes call, read from their source, and shows the guard
names a table a command or a hook leaves behind (SPD-241).
"""

import argparse
import ast
import importlib
import inspect
import json
import os
import textwrap
import time
import unittest
from unittest import mock

from helpers import REPO
from hookcase import AGENT_A, AGENT_B, HOOK_CACHES, HOOK_READING, LEAK_ALLOWED, SESSION, HookCase, common, program_tables, run_main, tables_changed

SPUDLIB = REPO / "bin" / "spudlib"
# The modules on PreToolUse(Bash)'s decision path that hold the analysis or the raw command text, by short name.
DECISION_PATH = {"bash_rule": SPUDLIB / "shell" / "bash_rule.py", "pretool": SPUDLIB / "hooks" / "pretool.py"}
# The functions that return (reason, analysis): their callers unpack it into a name `analysis`, which is then read here.
RETURNS_ANALYSIS = ("bash_refusal", "bash_reason")
# Every use of the raw command text on the decision path, (module, function, the use), and why it cannot answer a form
# differently from the command it holds when the two read alike.
RAW_TEXT_USES = {
    ("bash_rule", "bash_refusal", "hookio.DB_PATH_RE.search"): "hook_reading's own `database`",
    ("bash_rule", "bash_refusal", "analyse.analyse_command"): "the analysis, compared field by field",
    ("bash_rule", "bash_reason", "bash_refusal"): "passed on to bash_refusal, read here",
    ("pretool", "hook_bash", "bash_rule.bash_reason"): "passed on to bash_reason, read here",
    ("pretool", "hook_bash", "isinstance"): "the malformed-payload check: a form holding a command is a string too",
    ("pretool", "hook_bash", "command.strip"): "the malformed-payload check: a form holding a command is not blank either",
    ("pretool", "hook_bash", "command[:2000]"): "the copy a refusal's hook.denied event keeps, after the decision",
}


def parents_of(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def enclosing_function(node, parents):
    """The outermost function holding `node` (a closure's reads are its enclosing function's), or None at module level."""
    found = None
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found = node
    return found


def callee_name(call):
    """A call's function as written (`bash_rule.bash_reason`, `written_targets`) and its last name."""
    written = ast.unparse(call.func)
    last = written.rsplit(".", 1)[-1]
    return written, last


class HookReadingCoverTest(unittest.TestCase):
    """What PreToolUse(Bash) decides on is what hook_reading compares, read from the hook's source (see the module)."""

    @classmethod
    def setUpClass(cls):
        cls.trees = {name: ast.parse(path.read_text(encoding="utf-8"), str(path)) for name, path in DECISION_PATH.items()}
        cls.functions = {}  # the module-level functions of the decision path, by name: (module, node)
        for module, tree in cls.trees.items():
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cls.functions[node.name] = (module, node)

    def decision_path_function(self, call):
        """(module, node) of the decision path's function a call runs, or None for anything else."""
        written, last = callee_name(call)
        if written == last or written in ("%s.%s" % (m, last) for m in DECISION_PATH):
            return self.functions.get(last)
        return None

    def analysis_reads(self):
        """(the attributes read on the analysis, the uses of it this test cannot follow), over both modules."""
        fields, unfollowed = set(), []
        for module, tree in self.trees.items():
            parents = parents_of(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and callee_name(node)[1] == "analyse_command":
                    parent = parents[node]
                    if not (isinstance(parent, ast.Assign) and [ast.unparse(t) for t in parent.targets] == ["analysis"]):
                        unfollowed.append("%s: the analysis held by another name: %s" % (module, ast.unparse(parent)))
                if not (isinstance(node, ast.Name) and node.id == "analysis"):
                    continue
                parent = parents[node]
                where = "%s.%s" % (module, getattr(enclosing_function(node, parents), "name", "<module>"))
                if isinstance(node.ctx, ast.Store):
                    ok = (isinstance(parent, ast.Assign) and isinstance(parent.value, ast.Call)
                          and callee_name(parent.value)[1] == "analyse_command")
                    if isinstance(parent, ast.Tuple):
                        assign = parents[parent]
                        ok = (isinstance(assign, ast.Assign) and isinstance(assign.value, ast.Call)
                              and callee_name(assign.value)[1] in RETURNS_ANALYSIS)
                    if not ok:
                        unfollowed.append("%s: `analysis` bound to %s" % (where, ast.unparse(parent)))
                elif isinstance(parent, ast.Attribute) and parent.value is node:
                    fields.add(parent.attr)
                elif isinstance(parent, ast.Compare) and all(isinstance(op, (ast.Is, ast.IsNot)) for op in parent.ops) \
                        and all(isinstance(c, ast.Constant) and c.value is None for c in [parent.left, *parent.comparators] if c is not node):
                    pass  # `analysis is (not) None`: whether the line got as far as the analysis, which the database match decides
                elif isinstance(parent, ast.Tuple) and isinstance(parents[parent], ast.Return) \
                        and getattr(enclosing_function(node, parents), "name", None) in RETURNS_ANALYSIS:
                    pass  # handed back to a caller that unpacks it into `analysis`, read here
                elif isinstance(parent, ast.Call) and node in parent.args and self.decision_path_function(parent):
                    _m, callee = self.decision_path_function(parent)
                    params = [a.arg for a in callee.args.posonlyargs + callee.args.args]
                    index = parent.args.index(node)
                    if index >= len(params) or params[index] != "analysis":
                        unfollowed.append("%s: the analysis passed as %s's parameter %s" % (where, callee.name, params[index] if index < len(params) else index))
                else:
                    unfollowed.append("%s: the analysis used as %s" % (where, ast.unparse(parent)))
        return fields, unfollowed

    def raw_text_uses(self):
        """Every use of the raw command text in a function that holds it (a parameter `command`, or `command` taken from
        the tool input), as (module, function, the use), and every read of the tool input's command anywhere else."""
        uses, stray = set(), []
        for module, tree in self.trees.items():
            parents = parents_of(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value == "command":
                    parent = parents[node]
                    holder = parent.func.value if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute) else \
                        parent.value if isinstance(parent, ast.Subscript) else None
                    if holder is not None and ast.unparse(holder) in ("tool_input", "payload['tool_input']", 'payload["tool_input"]'):
                        assign = parents[parent]
                        if not (isinstance(assign, ast.Assign) and [ast.unparse(t) for t in assign.targets] == ["command"]):
                            stray.append("%s: the tool input's command read as %s" % (module, ast.unparse(assign)))
            for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                holds = "command" in [a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs] or any(
                    isinstance(n, ast.Assign) and [ast.unparse(t) for t in n.targets] == ["command"] for n in ast.walk(fn))
                if not holds or enclosing_function(fn, parents) is not None:
                    continue
                for node in ast.walk(fn):
                    if not (isinstance(node, ast.Name) and node.id == "command" and isinstance(node.ctx, ast.Load)):
                        continue
                    parent = parents[node]
                    if isinstance(parent, ast.Call) and node in parent.args:
                        use = callee_name(parent)[0]
                    elif isinstance(parent, ast.Attribute):
                        use = "command." + parent.attr
                    else:
                        use = ast.unparse(parent)
                    uses.add((module, fn.name, use))
        return uses, stray

    def test_hook_reading_compares_every_field_of_the_analysis_the_hook_reads(self):
        fields, unfollowed = self.analysis_reads()
        self.assertEqual(unfollowed, [], "the hook hands the analysis where this test cannot read what it takes from it")
        self.assertTrue(fields, "no field of the analysis found on the decision path: this test reads nothing")
        self.assertEqual(fields - set(HOOK_READING), set(),
                         "the hook's decision reads these fields of the analysis, which hook_reading does not compare, so"
                         " assertAnsweredAs would let a form that differs in them go unasserted: add them to HOOK_READING")

    def test_the_raw_command_text_is_used_only_as_tabled(self):
        uses, stray = self.raw_text_uses()
        self.assertEqual(stray, [])
        self.assertEqual(uses - set(RAW_TEXT_USES), set(),
                         "the hook's decision uses the raw command text beyond what hook_reading reads, so two lines that"
                         " read alike may be answered apart: read it through the analysis, or table the use with why it"
                         " cannot tell a form from the command it holds")
        self.assertEqual(set(RAW_TEXT_USES) - uses, set(), "a tabled use the hook no longer makes: drop it from RAW_TEXT_USES")

    def test_every_field_hook_reading_compares_is_one_the_analysis_has(self):
        syntax = ast.parse((SPUDLIB / "shell" / "syntax.py").read_text(encoding="utf-8"))
        cls = next(n for n in syntax.body if isinstance(n, ast.ClassDef) and n.name == "ShellAnalysis")
        names = {n.attr for n in ast.walk(cls) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self"}
        names |= {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
        self.assertEqual(set(HOOK_READING) - names, set())


# -- the leak guard past the Bash and edit hooks (SPD-241) ----------------------------------------------------------------

TESTS = REPO / "tests"
# A second session, never claimed before the prompt that names a ticket (UserPromptSubmit's auto-claim).
PROMPT_SESSION = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"


def cli_groups():
    """{command: its subcommands} for every command of the parser that has them (member, ticket, pr, session ...)."""
    def subcommands(parser):
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                return action.choices
        return {}
    top = subcommands(importlib.import_module("spudlib.cli.cliparser").build_parser())
    return {name: set(subcommands(sub)) for name, sub in top.items()}


def command_key(words, groups):
    """A CLI call's command as the parser dispatches it: the command, and its subcommand where it has them (`member
    show`, `board`), the global `--json` and `--as ACTOR` before it skipped.  None when the words name no command."""
    words = list(words)
    while words and words[0] in ("--json", "--as"):
        words = words[2:] if words[0] == "--as" else words[1:]
    if not words or words[0] not in groups:
        return None
    if groups[words[0]] and len(words) > 1 and words[1] in groups[words[0]]:
        return (words[0], words[1])
    return (words[0],)


def leading_words(args):
    """The string constants a call's positional arguments begin with, up to the first that is not one."""
    words = []
    for arg in args:
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            break
        words.append(arg.value)
    return words


def in_process_commands(groups):
    """{command key: where it is called} for every CLI command an in-process test calls, read from the source: in each test
    module that sets `in_process` or calls run_main, every class that runs in process (a HookCase with in_process set) and
    each test class it inherits from, a call of `<...>home.json` or `<...>home.run` whose arguments begin with the command's
    words, and a run_main call anywhere in the module whose argv is a list that begins with them.  A call whose words are
    not spelled out (a wrapper's *args) is not seen."""
    found = {}
    for path in sorted(TESTS.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        if path.stem == __name__ or ("in_process = True" not in text and "run_main(" not in text):
            continue
        module = importlib.import_module(path.stem)
        sources = set()
        for cls in vars(module).values():
            if isinstance(cls, type) and issubclass(cls, HookCase) and cls.in_process and cls.__module__ == module.__name__:
                sources |= {c for c in cls.__mro__ if c.__module__ in ("helpers", "hookcase") or c.__module__.startswith("test_")}
        trees = [(c.__qualname__, ast.parse(textwrap.dedent(inspect.getsource(c)))) for c in sources]
        for where, tree in trees:
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("json", "run") \
                        and ast.unparse(node.func.value).endswith("home"):
                    key = command_key(leading_words(node.args), groups)
                    if key:
                        found.setdefault(key, set()).add("%s.%s" % (path.stem, where))
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("run_main") and len(node.args) >= 2 \
                    and isinstance(node.args[1], ast.List):
                key = command_key(leading_words(node.args[1].elts), groups)
                if key:
                    found.setdefault(key, set()).add(path.stem)
    return found


class InProcessLeakTest(HookCase):
    """hookcase's leak guard (tables_changed) past the Bash and edit hooks, which InProcessParityTest walks: every other hook
    event and every CLI command an in-process test class calls runs in this process, between two readings of every table
    the program holds, and nothing may change but what fresh_process empties or PURE_TABLES names.  A table added later on
    any of these paths -- a memo in a command, a cache in a recording hook -- fails here until hookcase resets it, and the
    last test shows the guard names one.  gitrepos' caches (SPD-131's findings cache and SPD-238's settled scopes cache)
    are read and written under a clock an hour ahead, so the samples reach both caches' writes and reads."""

    in_process = True

    def build_home(self):
        super().build_home()
        self.lead = self.spawn(self.plan(persona="engineer", model="opus"), AGENT_A)
        self.other = self.spawn(self.plan(persona="engineer", model="opus"), AGENT_B)
        self.planned = self.plan()
        self.tool = str(self.home.tool)

    def clock_ahead(self):
        """gitrepos' clock (its time.time_ns) an hour ahead for the block, so every stamp either cache is kept under has
        settled and each writes its entries (the settle rule; test_hooks_projects.PlantedCacheTest.clock_at)."""
        ahead = time.time_ns() + 3600 * 10**9
        return mock.patch("spudlib.hooks.gitrepos.time", mock.Mock(wraps=time, time_ns=lambda: ahead))

    def assertNoLeak(self, samples):
        """The samples, run with gitrepos' clock ahead, leave no table behind but LEAK_ALLOWED's and record no hook error.
        Each run empties the caches first, so what the walk sees of them is the last run's; a table no run empties is seen
        as every run left it."""
        def clocked():
            with self.clock_ahead():  # inside the run: the patch must reach the program tables_changed imports anew
                samples()

        changed = tables_changed(clocked)
        self.assertEqual(changed - LEAK_ALLOWED, set(), "a table the program holds outlives an in-process run: reset it in"
                         " hookcase.fresh_process and helpers.forget_process_caches (HOOK_CACHES), or name it in PURE_TABLES"
                         " with why no later run can read it differently")
        self.assertEqual(self.events("hook.error"), [])
        self.assertFalse(self.home.spool.exists() and self.home.spool.read_text(encoding="utf-8").strip(), "a hook failed open")
        return changed

    # -- the samples ------------------------------------------------------------------------------------------------------
    def hook_samples(self, ran):
        """Every hook event, most of them more than once: a foreground spawn run to its end (PreToolUse(Agent),
        SubagentStart, the binding first call, SubagentStop over a transcript, PostToolUse completed), a refused spawn,
        a member's git call in the tool checkout (the scopes cache, twice), SessionStart from the home and the tool
        (the findings cache, twice), UserPromptSubmit's auto-claim, and Stop, Spud's and a lead's held return."""
        def hook(event, payload, cwd=None):
            if cwd is not None:
                payload["cwd"] = cwd
            r = self.home.hook(event, payload)
            ran.append(event)
            self.assertEqual(r.code, 0, (event, r))
            return r

        self.foreground(self.planned, "a1b2c3d4e5f60718a")
        ran.extend(("PreToolUse", "SubagentStart", "SubagentStop", "PostToolUse"))
        self.assertEqual(hook("PreToolUse", self.pre_agent("%s/Nobody (09, scout)" % self.team)).decision, "deny")
        for _ in range(2):
            hook("PreToolUse", self.pre_bash("git -C %s status" % self.tool, agent_id=AGENT_A))
        for source in ("startup", "resume", "compact", "clear"):
            hook("SessionStart", self.session_start(source))
        hook("SessionStart", self.session_start(), cwd=self.tool)
        prompt = common(self.tool, session=PROMPT_SESSION)
        prompt.update({"hook_event_name": "UserPromptSubmit", "prompt": "Pick up %s, please." % self.t["key"]})
        self.assertIn("additionalContext", hook("UserPromptSubmit", prompt).stdout)  # claimed
        hook("UserPromptSubmit", dict(prompt, prompt="Nothing to claim here."))
        self.assertEqual(hook("SubagentStop", self.sub_stop(AGENT_A)).json.get("decision"), "block")  # no Result: held once
        hook("SubagentStop", self.sub_stop(AGENT_A, stop_hook_active=True))
        hook("Stop", self.stop())
        hook("Stop", self.stop(stop_hook_active=True), cwd=self.tool)

    def cli_samples(self, ran):
        """The CLI commands the in-process test classes call (in_process_commands), each run as they run it."""
        def cli(*args, actor="spud", check=True, cwd=None):
            proc = self.home.run(*args, actor=actor, check=check, cwd=cwd)
            ran.append([str(a) for a in args])
            return proc

        planned, key = self.planned["ref"], self.t["key"]
        other = self.new_ticket("Another", status="active")
        ran.append(["ticket", "new"])
        cli("ticket", "show", key)
        cli("--json", "ticket", "show", key)
        cli("ticket", "edit", key, "--brief", "The brief, edited.")
        cli("ticket", "move", other["key"], "--status", "parked", "--reason", "Eric's go", "--until", "2099-01-01")
        cli("member", "show", planned)
        cli("member", "list")
        cli("member", "edit", planned, "--brief", "Do it.")
        cli("member", "log", "Half way.", actor=AGENT_A)
        cli("member", "block", "Need Eric.", actor=AGENT_B)
        cli("member", "result", "Built it.", actor=AGENT_A)
        cli("member", "finish", self.lead["ref"], "--status", "done", "--outcome", "Accepted.", "--summary",
            "Built the thing in tests/ and bin/spud, with the tests that hold it, and left the rest of the tree as it was.")
        extra = self.plan()  # in the lead's slot, freed (limits.root_fan_out)
        ran.append(["member", "new"])
        cli("member", "start", extra["ref"])
        cli("member", "resum", "--all")
        cli("--json", "member", "resum", "--all")
        cli("proposal", "file", "--title", "A follow-up", "--why", "Seen in passing.", "--evidence", "tests/x.py",
            "--priority", "P3", actor=AGENT_B)
        proposals = json.loads(cli("--json", "proposal", "list").stdout)["proposals"]
        cli("proposal", "decide", str(proposals[0]["id"]), "--decision", "decline", "--reason", "Not now.", actor="spud")
        cli("report", "add", "Merged into main", "--next", "Nothing.")
        cli("handoff", "add", "--ticket", key, "--from", self.lead["ref"], "--to", "spud", "--what", "the merged file")
        cli("render")
        note = self.home.path / "ledger" / "teams" / self.team / ("%s.md" % self.planned["name"])
        for edited in ("Do it, edited.", "Do it, edited again."):  # a hand edit is a conflict: discarded, then accepted
            note.write_text(note.read_text(encoding="utf-8").replace("Do it.", edited), encoding="utf-8")
            self.assertEqual(cli("render", check=False).returncode, 6)
        cli("render", "--discard", note)
        note.write_text(note.read_text(encoding="utf-8").replace("Do it.", "Do it, edited."), encoding="utf-8")
        cli("import", "--file", note)
        rendered, corpus = self.home.root / "rendered", self.home.root / "corpus"
        cli("render", "--out", rendered)
        # the bulk import, of a ticket and a day's report the ledger does not hold: the other ticket's note under a new key
        (corpus / "ledger" / "tickets").mkdir(parents=True)
        (corpus / "reports").mkdir()
        text = (rendered / "ledger" / "tickets" / ("%s.md" % other["key"])).read_text(encoding="utf-8")
        text = text.replace(other["key"], "SPD-900").replace(other["team_key"], "SPUD-900")
        (corpus / "ledger" / "tickets" / "SPD-900.md").write_text(text, encoding="utf-8")
        report = next((rendered / "reports").glob("*.md"))
        (corpus / "reports" / "2020-01-01.md").write_text(report.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertIn("imported 1 tickets", cli("import", corpus).stdout)
        for view in (("board",), ("board", "--brief"), ("board", "--json"), ("board", "--no-reconcile"), ("card", key),
                     ("--json", "card", key), ("fleet",), ("doctor",), ("--json", "doctor"), ("events", "--kind", "hook.denied"),
                     ("events", "--member", self.lead["ref"]), ("events", "--ticket", key), ("sql", "--readonly", "SELECT 1")):
            cli(*view)
        self.assertIn("read-only statements only", cli("sql", "SELECT 1", check=False).stderr)
        cli("pr", "record", "--ticket", key, "--url", "https://github.com/o/r/pull/1", "--branch", "feat/x", "--worktree", "/tmp/wt-x")
        cli("pr", "list")
        cli("pr", "reconcile")
        cli("project", "edit", "spud", "--sessions", "claim")
        cli("session", "claim", cwd=self.tool)
        cli("session", "release", cwd=self.tool)
        for argv in (["--help"], ["member", "--help"], ["member", "resum", "--help"], ["pr", "--help"], ["board", "--help"]):
            code, _out, err = run_main(dict(self.home.env, COLUMNS="80"), argv)
            self.assertEqual(code, 0, err)
            ran.append(argv)

    # -- the tests ------------------------------------------------------------------------------------------------------
    def test_no_hook_event_leaves_a_table_behind(self):
        ran = []
        self.assertNoLeak(lambda: self.hook_samples(ran))
        self.assertEqual(set(ran), set(importlib.import_module("spudlib.hooks.hookio").HOOK_EVENTS),
                         "a hook event the samples do not run: add it to hook_samples")

    def test_no_cli_command_an_in_process_test_calls_leaves_a_table_behind(self):
        groups, ran = cli_groups(), []
        self.assertNoLeak(lambda: self.cli_samples(ran))
        called = in_process_commands(groups)
        self.assertGreater(len(called), 10, "the in-process test classes call too few commands to read: the scan is broken")
        sampled = {command_key(argv, groups) for argv in ran}
        self.assertEqual({k: sorted(v) for k, v in called.items() if k not in sampled}, {},
                         "an in-process test calls a command cli_samples does not run: add it there")

    def test_gitrepos_tables_are_reset_or_left_unchanged_by_the_runs_that_read_them(self):
        """Every table gitrepos holds at module level is emptied by fresh_process (HOOK_CACHES) or walked and left unchanged
        by runs that go through both of its caches: a member's git call writes and then reads the scopes cache
        (git-config-scopes.json), and board --brief and SessionStart write and then read the findings cache
        (git-checkout-findings.json, SPD-131), each through _SCOPES_READ, and doctor reads every checkout without it."""
        name = "spudlib.hooks.gitrepos"
        reached = set()

        def note():
            gitrepos = importlib.import_module(name)  # the run's own, imported anew
            reached.update(os.path.basename(k) for k in gitrepos._SCOPES_READ)
            if gitrepos._PROGRAM_KEYS:
                reached.add("_PROGRAM_KEYS")

        def samples():
            for _ in range(2):
                self.home.hook("PreToolUse", self.pre_bash("git -C %s status" % self.tool, agent_id=AGENT_A))
                note()
            for _ in range(2):
                self.home.run("board", "--brief")
                note()
                self.home.hook("SessionStart", self.session_start())
                note()
            self.home.run("doctor", check=False)
            note()

        changed = self.assertNoLeak(samples)
        gitrepos = importlib.import_module(name)
        self.assertEqual(reached, {gitrepos.GIT_CONFIG_SCOPES_CACHE, gitrepos.GIT_FINDINGS_CACHE, "_PROGRAM_KEYS"},
                         "the samples do not reach both of gitrepos' caches and its key memo")
        tables = {attr for attr, value in vars(gitrepos).items() if not attr.startswith("__")
                  and (isinstance(value, (dict, list, set, bytearray)) or hasattr(value, "cache_info"))}
        self.assertIn("_SCOPES_READ", tables)
        walked = {key for mod, key in program_tables() if mod == name}
        for attr in sorted(tables):
            with self.subTest(table=attr):
                if (name, attr) not in HOOK_CACHES:
                    self.assertIn(attr, walked)
                    self.assertNotIn((name, attr), changed)

    def test_the_guard_names_a_table_a_command_or_a_hook_leaves_behind(self):
        """A module-level dict planted, empty, in the board command's module and in SessionStart's before the first reading,
        each written by its own run: the guard names both, and nothing else; and a run that writes neither leaves the
        planted pair unnamed.  The plant is in the program tables_changed imports anew, which is dropped after, so nothing
        here needs undoing."""
        planted = {"spudlib.commands.views": "cmd_board", "spudlib.hooks.sessionhooks": "hook_session_start"}

        def plant():
            for name, function in planted.items():
                module = importlib.import_module(name)
                module._PLANTED = {}

                def remembering(ctx, arg, module=module, fn=getattr(module, function)):
                    module._PLANTED[str(ctx.home)] = True
                    return fn(ctx, arg)
                setattr(module, function, remembering)

        def board_and_session_start():
            self.home.run("board")
            self.home.hook("SessionStart", self.session_start())

        self.assertEqual(tables_changed(board_and_session_start, prepare=plant) - LEAK_ALLOWED,
                         {(name, "_PLANTED") for name in planted})
        self.assertEqual(tables_changed(lambda: self.home.run("member", "show", self.lead["ref"]), prepare=plant) - LEAK_ALLOWED, set())


if __name__ == "__main__":
    unittest.main()
