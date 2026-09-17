"""The shape of the program's package (SPD-065): bin/spud_ledger.py is the entry and bin/spudlib/ holds the code.

Parsed, never imported, except where a hook run's own imports are read: a module imports modules of the package, never
names from them, and only at its top; every name the program defines is defined in one module, and no module binds a
name it imports a module as; an import cycle is tolerated only where no module reads another's name while it is being
imported; every module is reachable from the entry; a hook run imports only its event's modules, all in HOOK_PATH; and
no test sets or patches an attribute of the loaded program, which no call site reads.
"""

import ast
import json
import subprocess
import sys
import unittest

from helpers import REPO, SPUD, SpudTestCase

PACKAGE = REPO / "bin" / "spudlib"
ENTRY = REPO / "bin" / "spud_ledger.py"
TESTS = REPO / "tests"
DISPATCH = "hooks.dispatch"
# Every module some `spud hook <event>` imports.  A hook that needs one more module adds it here and runs
# tests/probes/hook_timing.py against main: the commands, render and import, the parser, project install, schedule and
# doctor stay off this list (Eric, SPD-065).  core.launchagents joined for SessionStart's render watcher line (SPD-048),
# shell.assignment_words for the Bash rule's reading of a subscripted assignment (SPD-085).
HOOK_PATH = {
    "core.homeconf", "core.kernel", "core.launchagents", "core.lazy",
    "state.actors", "state.backup", "state.ledgerdb", "state.lookup", "state.ops", "state.schema", "state.transcripts",
    "render.prices", "projects.sessions",
    "hooks.dispatch", "hooks.hookio", "hooks.pathrule", "hooks.pretool", "hooks.recording", "hooks.sessionhooks",
    "hooks.stophook", "hooks.subagent_stop", "hooks.worktrees",
    "shell.analyse", "shell.assignment_words", "shell.bash_rule", "shell.directories", "shell.expansions", "shell.git_config", "shell.git_programs",
    "shell.git_verbs", "shell.globbing", "shell.prepare", "shell.redirect_globs", "shell.spud_calls", "shell.syntax",
    "shell.walk", "shell.zsh",
}


def modules():
    """{dotted name under spudlib: parsed module}."""
    return {".".join(path.relative_to(PACKAGE).with_suffix("").parts): ast.parse(path.read_text(encoding="utf-8"), str(path))
            for path in sorted(PACKAGE.rglob("*.py"))}


def package_imports(name, node):
    """[(module imported, alias bound)] for an ImportFrom inside module `name`; None when it does not import the package."""
    if not isinstance(node, ast.ImportFrom) or node.level == 0:
        return None
    parts = name.split(".")[:-node.level] + ([node.module] if node.module else [])
    return [(".".join(parts + [a.name]), a.asname or a.name) for a in node.names]


def handler_table(tree):
    """dispatch's HOOK_HANDLERS: {event: (module in spudlib.hooks, handler name)}, the one import made at run time."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and [t.id for t in node.targets if isinstance(t, ast.Name)] == ["HOOK_HANDLERS"]:
            return ast.literal_eval(node.value)
    raise AssertionError("hooks/dispatch.py defines no HOOK_HANDLERS")


def bound_names(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
    return set()


def import_time_reads(tree, aliases):
    """The modules whose names a module reads while it is imported: `alias.name` outside every function and lambda body."""
    found = set()

    def visit(node, in_body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for d in getattr(node, "decorator_list", []) + node.args.defaults + [x for x in node.args.kw_defaults if x]:
                visit(d, in_body)
            for b in node.body if isinstance(node.body, list) else [node.body]:
                visit(b, True)
            return
        if not in_body and isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
            found.add(aliases[node.value.id])
        for child in ast.iter_child_nodes(node):
            visit(child, in_body)

    for node in tree.body:
        visit(node, False)
    return found


def reach(graph, starts):
    seen, todo = set(), list(starts)
    while todo:
        m = todo.pop()
        if m not in seen:
            seen.add(m)
            todo.extend(graph.get(m, ()))
    return seen


def is_program_load(node):
    """A call of load_spud_module(), which returns the entry module spud_ledger."""
    return isinstance(node, ast.Call) and (getattr(node.func, "id", None) == "load_spud_module" or getattr(node.func, "attr", None) == "load_spud_module")


def program_touches(tree):
    """[(line, what)] where a test file sets, deletes or patches an attribute of the module load_spud_module() returns."""
    names, attrs = set(), set()  # `spud = load_spud_module()`; `cls.spud = load_spud_module()`
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and is_program_load(node.value):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, ast.Attribute):
                    attrs.add(target.attr)

    def holds(e):
        return (is_program_load(e) or (isinstance(e, ast.Name) and e.id in names)
                or (isinstance(e, ast.Attribute) and e.attr in attrs and isinstance(e.value, ast.Name) and e.value.id in ("self", "cls")))

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)) and holds(node.value):
            found.append((node.lineno, "sets or deletes .%s" % node.attr))
        elif isinstance(node, ast.Call):
            called = getattr(node.func, "attr", None) or getattr(node.func, "id", "")
            first = node.args[0] if node.args else None
            if called in ("object", "multiple", "setattr", "delattr") and first is not None and holds(first):
                found.append((node.lineno, "%s on the loaded program" % called))
            elif called == "patch" and isinstance(first, ast.Constant) and str(first.value).split(".")[0] == "spud_ledger":
                found.append((node.lineno, "patch(%r)" % first.value))
    return found


class PackageShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = modules()
        cls.graph, cls.aliases = {}, {}
        for name, tree in cls.modules.items():
            cls.graph[name], cls.aliases[name] = set(), {}
            for node in tree.body:
                for target, alias in package_imports(name, node) or ():
                    cls.graph[name].add(target)
                    cls.aliases[name][alias] = target
        cls.handlers = handler_table(cls.modules[DISPATCH])

    def test_a_module_imports_modules_of_the_package_never_names_and_only_at_its_top(self):
        for name, tree in self.modules.items():
            top = set(map(id, tree.body))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)) and (node.level if isinstance(node, ast.ImportFrom) else 0) == 0:
                    words = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                    self.assertFalse(any(w.split(".")[0] in ("spudlib", "spud_ledger") for w in words), "%s line %d: import the package relatively" % (name, node.lineno))
                imported = package_imports(name, node)
                if imported is None:
                    continue
                for target, _ in imported:
                    self.assertTrue(target in self.modules, "%s: `from ... import` names %s, which is not a module of the package" % (name, target))
                self.assertTrue(id(node) in top, "%s line %d: an import inside a function hides a module from these checks" % (name, node.lineno))

    def test_every_name_is_defined_in_one_module_and_no_module_binds_an_alias(self):
        owner = {}
        for name, tree in self.modules.items():
            for node in tree.body:
                for bound in bound_names(node):
                    self.assertFalse(bound in owner, "%s is defined in %s and %s" % (bound, owner.get(bound), name))
                    owner[bound] = name
            bound_anywhere = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            bound_anywhere |= {a.arg for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.Lambda)) for a in ast.walk(n.args) if isinstance(a, ast.arg)}
            for alias in self.aliases[name]:
                self.assertFalse(alias in bound_anywhere, "%s binds %s, the name it imports a module as" % (name, alias))

    def test_no_module_reads_a_name_across_an_import_cycle_while_it_is_imported(self):
        for name, tree in self.modules.items():
            for target in import_time_reads(tree, self.aliases[name]):
                self.assertFalse(name in reach(self.graph, [target]),
                                 "%s reads %s while it is imported, and %s imports %s back: move the name down a layer" % (name, target, target, name))

    def test_every_handler_is_a_function_of_its_module(self):
        for event, (module, handler) in self.handlers.items():
            defined = {n.name for n in self.modules["hooks." + module].body if isinstance(n, ast.FunctionDef)}
            self.assertIn(handler, defined, event)

    def test_every_module_is_reachable_from_the_entry(self):
        entry = ast.parse(ENTRY.read_text(encoding="utf-8"))
        starts = {".".join(node.module.split(".")[1:] + [a.name]) for node in ast.walk(entry)
                  if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("spudlib.") for a in node.names}
        graph = dict(self.graph)
        graph[DISPATCH] = graph[DISPATCH] | {"hooks." + module for module, _ in self.handlers.values()}
        self.assertEqual(set(self.modules) - reach(graph, starts), set())


class PatchTargetTest(unittest.TestCase):
    def test_no_test_sets_or_patches_an_attribute_of_the_loaded_program(self):
        # spud_ledger.<name> reads the defining module's name, but a write lands on the entry, which no call site reads:
        # the patch changes nothing and a test may pass by luck.  Patch the module that defines the name instead,
        # mock.patch("spudlib.<group>.<module>.<name>", ...) (SPD-065).
        found = []
        for path in sorted(TESTS.rglob("*.py")):
            for line, what in program_touches(ast.parse(path.read_text(encoding="utf-8"), str(path))):
                found.append("%s line %d: %s" % (path.relative_to(REPO), line, what))
        self.assertEqual(found, [])


# Runs the launcher as its own __main__ and writes sys.modules as it exits: -X importtime does not see a module imported
# with importlib.import_module, which is how cmd_hook imports its event's handler.
MODULES_AT_EXIT = ("import atexit, sys; atexit.register(lambda: sys.stderr.write('\\nMODULES ' + ' '.join(sorted(sys.modules)) + '\\n')); "
                   "sys.argv = sys.argv[1:]; __file__ = sys.argv[0]; exec(compile(open(__file__, encoding='utf-8').read(), __file__, 'exec'))")


class HookPathTest(SpudTestCase):
    def test_each_hook_imports_its_own_modules_and_nothing_off_the_hook_path(self):
        common = {"session_id": "s", "cwd": str(self.home.path)}
        payloads = {
            "PreToolUse": {"tool_name": "Bash", "tool_use_id": "t", "tool_input": {"command": "git status"}},
            "PostToolUse": {"tool_name": "Read", "tool_use_id": "t", "tool_input": {}, "tool_response": {}},
            "SubagentStart": {"agent_id": "a0123456789abcdef", "agent_type": "Explore"},
            "SubagentStop": {"agent_id": "a0123456789abcdef", "agent_type": "Explore", "stop_hook_active": False},
            "SessionStart": {"source": "startup"},
            "Stop": {"stop_hook_active": False},
            "UserPromptSubmit": {"prompt": "hello"},
        }
        seen = set()
        for event, extra in payloads.items():
            payload = dict(extra, hook_event_name=event, **common)
            proc = subprocess.run([sys.executable, "-I", "-S", "-c", MODULES_AT_EXIT, str(SPUD), "hook", event],
                                  input=json.dumps(payload), capture_output=True, text=True, env=self.home.env)
            self.assertEqual(proc.returncode, 0, proc)
            imported = proc.stderr.rsplit("\nMODULES ", 1)[-1].split()
            ours = {m[len("spudlib."):] for m in imported if m.startswith("spudlib.")} & set(modules())
            self.assertEqual(ours - HOOK_PATH, set(), event)
            seen |= ours
        self.assertEqual(seen, HOOK_PATH)  # the list stays exact: a module no hook imports any more leaves it


if __name__ == "__main__":
    unittest.main()
