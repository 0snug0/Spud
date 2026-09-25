"""The shape of the program's package (SPD-065): bin/spud_ledger.py is the entry and bin/spudlib/ holds the code.

Parsed, never imported, except where a hook run's own imports are read: a module imports modules of the package, never
names from them, and only at its top; every name the program defines is defined in one module, no module binds a name
it imports a module as, in any scope, and no function binds any module's name (SPD-237); an import cycle is tolerated
only where no module reads another's name while it is being imported, at its top or in a function its import calls;
every module is reachable from the entry; a hook run imports only its event's modules, all in HOOK_PATH; and no test
sets or patches an attribute of the loaded program, through any name that holds it, since no call site reads one.
DamagedCopyTest holds each check to its promise: a copy of the package damaged the way the check says it catches, held
in memory, must fail it (SPD-079).
"""

import ast
import functools
import json
import re
import subprocess
import symtable
import sys
import tarfile
import textwrap
import unittest
from pathlib import Path

from helpers import REPO, SPUD, SpudTestCase, load_spud_module

PACKAGE = REPO / "bin" / "spudlib"
ENTRY = REPO / "bin" / "spud_ledger.py"
TESTS = REPO / "tests"
DISPATCH = "hooks.dispatch"
# Every module some `spud hook <event>` imports.  A hook that needs one more module adds it here and runs
# tests/probes/hook_timing.py against main: the commands, render and import, the parser, project install, schedule and
# doctor stay off this list (Eric, SPD-065).  core.launchagents joined for SessionStart's render watcher line (SPD-048),
# shell.assignment_words for the Bash rule's reading of a subscripted assignment (SPD-085), shell.arg_writes for the files a
# command names as operands and writes (SPD-121), hooks.gitrepos for the repository check the Bash rule and SessionStart's
# line share (SPD-123), hooks.snapshots for the aliases and functions the Bash tool's shell already holds (SPD-133),
# shell.find_xargs, shell.tree_writes and shell.tree_walk for the writes a line does not spell, shell.spelled_writes and
# shell.downloads for the files a line names past SPD-121's table (SPD-126), shell.stdin_text for the commands a line
# feeds a shell on standard input (SPD-143), shell.script_text for the files a sed script or an awk program writes and the
# commands it runs (SPD-139), shell.inline_programs for the program an interpreter run spells rather than reads from a
# file (SPD-150), shell.interpreter_words for the words such a run is read with -- one the line cannot settle where an
# option may stand, and one an xargs reads from its input (SPD-152), shell.runtime_shells for the shell text a runtime's or a
# package manager's subcommand runs (SPD-154), shell.script_files for a shell whose commands come from a file and the
# project allow-list of repository scripts (SPD-145), shell.script_runners and shell.runner_files for a script runner and the project allow-list
# of runner names (SPD-168), shell.heredocs for where a here-document's body starts, taken out of shell.prepare (SPD-188),
# shell.reevaluation for the text zsh's (e) flag evaluates in a word (SPD-189), shell.positional for the words a call hands a
# function of the shell's, set where its body reads them (SPD-203), shell.unread for the one fail-closed finding, text the
# reader did not read (SPD-217), shell.program_writes for the write markers an inline program's text shows (SPD-175),
# shell.loop_bindings for the values a for loop's words and a basename substitution give a write target (SPD-146),
# shell.git_writes for the files a git call writes, taken out of shell.git_verbs (SPD-229),
# shell.arithmetic_assignments and shell.assigning_builtins for the names an arithmetic evaluation and an assigning
# builtin assign, taken out of shell.assignment_words (SPD-259), shell.held_text for the text the shell holds, an alias's
# body and a function's, read and pruned, taken out of shell.analyse (SPD-264), shell.held_options for the options the
# shell holds and shell.held_shadows for a call of Claude Code's own grep, find, rg or pkill, taken out of shell.held_text
# (SPD-267), shell.archive_names for the names an archive or a patch the line names would write (SPD-144), which reaches
# tarfile and zipfile only through core.lazy (HookPathTest.test_a_hook_lists_an_archive_only_on_a_line_that_extracts_one),
# shell.line_functions for a function body the line defines and its reading at each call, taken out of shell.walk
# (SPD-280), shell.line_aliases for the aliases a line defines for eval and the ones the shell already holds, taken out
# of shell.expansions (SPD-284).
HOOK_PATH = {
    "core.homeconf", "core.kernel", "core.launchagents", "core.lazy",
    "state.actors", "state.backup", "state.ledgerdb", "state.lookup", "state.ops", "state.schema", "state.transcripts",
    "render.prices", "projects.sessions",
    "hooks.dispatch", "hooks.gitrepos", "hooks.hookio", "hooks.pathrule", "hooks.pretool", "hooks.recording", "hooks.sessionhooks",
    "hooks.snapshots", "hooks.stophook", "hooks.subagent_stop", "hooks.worktrees",
    "shell.analyse", "shell.archive_names", "shell.arg_writes", "shell.arithmetic_assignments", "shell.assigning_builtins", "shell.assignment_words", "shell.bash_rule", "shell.directories", "shell.downloads", "shell.expansions", "shell.find_xargs",
    "shell.git_config", "shell.git_programs", "shell.git_verbs", "shell.git_writes", "shell.globbing", "shell.held_options", "shell.held_shadows", "shell.held_text", "shell.heredocs", "shell.inline_programs",
    "shell.interpreter_words", "shell.line_aliases", "shell.line_functions","shell.loop_bindings", "shell.positional", "shell.prepare", "shell.program_writes", "shell.redirect_globs", "shell.reevaluation", "shell.runner_files", "shell.runtime_shells", "shell.spud_calls",
    "shell.script_files", "shell.script_runners", "shell.script_text", "shell.spelled_writes", "shell.stdin_text", "shell.syntax", "shell.tree_walk", "shell.tree_writes",
    "shell.unread", "shell.walk", "shell.zsh",
}


def module_name(path):
    """shell.walk for bin/spudlib/shell/walk.py."""
    return ".".join(path.relative_to(PACKAGE).with_suffix("").parts)


def module_names():
    """Every module of the package by its dotted name under spudlib, from the paths alone."""
    return {module_name(path) for path in PACKAGE.rglob("*.py")}


def package_imports(name, node):
    """[(module imported, alias bound)] for an ImportFrom inside module `name`; None when it does not import the package."""
    if not isinstance(node, ast.ImportFrom) or node.level == 0:
        return None
    parts = name.split(".")[:-node.level] + ([node.module] if node.module else [])
    return [(".".join(parts + [a.name]), a.asname or a.name) for a in node.names]


class Package:
    """The package as the checks read it, parsed and never imported: each module's text and tree, the modules each
    imports at its top, the alias each of those imports binds, and each module by its basename.  `edited` is a copy with
    one module's text replaced or added, which parses that module alone -- the damaged copies DamagedCopyTest holds the
    checks to (SPD-079)."""

    def __init__(self, texts, trees=None):
        trees = trees or {}
        self.texts = texts
        self.trees = {name: trees[name] if name in trees else ast.parse(text, name.replace(".", "/") + ".py") for name, text in texts.items()}
        self.graph, self.aliases = {}, {}
        self.basenames = {name.rsplit(".", 1)[-1]: name for name in texts}  # walk -> shell.walk: unique, §7
        for name, tree in self.trees.items():
            self.graph[name], self.aliases[name] = set(), {}
            for node in tree.body:
                for target, alias in package_imports(name, node) or ():
                    self.graph[name].add(target)
                    self.aliases[name][alias] = target

    def edited(self, name, text):
        return Package({**self.texts, name: text}, {n: tree for n, tree in self.trees.items() if n != name})


@functools.cache
def real_package():
    """The package as it is in this checkout, read once per process."""
    return Package({module_name(path): path.read_text(encoding="utf-8") for path in sorted(PACKAGE.rglob("*.py"))})


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


def reach(graph, starts):
    seen, todo = set(), list(starts)
    while todo:
        m = todo.pop()
        if m not in seen:
            seen.add(m)
            todo.extend(graph.get(m, ()))
    return seen


def import_problems(package, name):
    """Module `name` imports the package relatively, names only modules that exist, and imports only at its top."""
    tree, problems = package.trees[name], []
    top = set(map(id, tree.body))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and (node.level if isinstance(node, ast.ImportFrom) else 0) == 0:
            words = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            if any(w.split(".")[0] in ("spudlib", "spud_ledger") for w in words):
                problems.append("%s line %d: import the package relatively" % (name, node.lineno))
        imported = package_imports(name, node)
        if imported is None:
            continue
        problems += ["%s: `from ... import` names %s, which is not a module of the package" % (name, target)
                     for target, _ in imported if target not in package.trees]
        if id(node) not in top:
            problems.append("%s line %d: an import inside a function hides a module from these checks" % (name, node.lineno))
    return problems


def definition_problems(package):
    """Every name bound at a module's top is bound there once, and in no other module."""
    owner, problems = {}, []
    for name, tree in package.trees.items():
        for node in tree.body:
            for bound in sorted(bound_names(node)):
                if bound in owner:
                    problems.append("%s is defined in %s and %s" % (bound, owner[bound], name))
                owner[bound] = name
    return problems


def comprehension_targets(tree):
    """[(line, name)] for each name a comprehension binds outside every function and lambda -- at a module's top or in a
    class body, where symtable reads it into the enclosing scope (the compiler inlines a comprehension, PEP 709) and so
    cannot tell it from a global or a class attribute."""
    found, todo = [], [tree]
    while todo:
        node = todo.pop()
        if isinstance(node, FUNCTIONS + (ast.Lambda,)):  # its body is a function scope, which symtable reads
            todo.extend(getattr(node, "decorator_list", []) + node.args.defaults + [d for d in node.args.kw_defaults if d])
            continue
        if isinstance(node, ast.comprehension):
            found += [(n.lineno, n.id) for n in ast.walk(node.target) if isinstance(n, ast.Name)]
        todo.extend(ast.iter_child_nodes(node))
    return found


def alias_bindings(package, name):
    """Where module `name` binds a name it imports a module as, in any scope and by any binding form, and where it binds
    the basename of any module of the package as a local variable, a parameter or a comprehension's name, whether it
    imports that module or not: a function binding one shadows the module the day its module imports it (§7 of the
    spudlib-modules skill; SPD-237).  symtable, the compiler's own reading, answers what binds a name in each scope --
    an assignment, a parameter, a def or a class, an except, with or for target, a match capture, a del, a walrus, a
    type parameter, an import inside a function -- and at the top, where the alias is itself an import, a second import
    binding the same name is the one form it cannot tell apart (SPD-079).  A module name is read in every function scope
    (a def, a lambda, a type parameter list) and in each comprehension; a module's globals and a class's attributes are
    not locals, and are left out."""
    aliases, top = package.aliases[name], symtable.symtable(package.texts[name], name.replace(".", "/") + ".py", "exec")
    problems, tables = [], [top]
    for table in tables:  # the list grows as it is read: every scope of the module, each once
        tables.extend(table.get_children())
        local = table is not top and table.get_type() != symtable.SymbolTableType.CLASS
        for symbol in table.get_symbols():
            bound = symbol.is_assigned() or symbol.is_parameter() or (symbol.is_imported() and table is not top)
            where = "at its top" if table is top else "in %s %s" % (table.get_type().value, table.get_name())
            if bound and symbol.get_name() in aliases:
                problems.append("%s binds %s, the name it imports a module as, %s" % (name, symbol.get_name(), where))
            elif bound and local and symbol.get_name() in package.basenames:
                problems.append("%s binds %s, the name of module %s, %s" % (name, symbol.get_name(), package.basenames[symbol.get_name()], where))
    problems += ["%s binds %s, the name of module %s, in a comprehension on line %d" % (name, bound, package.basenames[bound], line)
                 for line, bound in comprehension_targets(package.trees[name]) if bound in package.basenames and bound not in aliases]
    imported = [a.asname or a.name.split(".")[0] for node in package.trees[name].body if isinstance(node, (ast.Import, ast.ImportFrom)) for a in node.names]
    return problems + ["%s imports two modules as %s" % (name, alias) for alias in aliases if imported.count(alias) > 1]


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def scope_defs(body):
    """{name: [def or class statement]} for what a scope's statements define: inside its compound statements too, never
    inside a nested function or class."""
    found, todo = {}, list(body)
    while todo:
        node = todo.pop()
        if isinstance(node, FUNCTIONS + (ast.ClassDef,)):
            found.setdefault(node.name, []).append(node)
        elif not isinstance(node, ast.expr):
            todo.extend(ast.iter_child_nodes(node))
    return found


def import_time_reads(tree, aliases):
    """{module read: (line, the calls it is read through)} for each module whose names a module reads while it is
    imported: `alias.name` wherever the import runs it -- the module's top, a class body, a decorator, a default -- and in
    the body of every function the import runs by calling it by name (SPD-079).

    A call is followed all the way down, as far as calls by name go: to a def of the module's top, or of the function or
    class body the call stands in (the scope a bare name reads first); into whatever that function calls in turn,
    recursion included, each function once; and a class called runs its __new__ and __init__.  That is the whole of
    what the module's own code runs at import, short of a function reached some other way: handed to another callable
    (`sorted(key=f)`), called through a table, an attribute or an instance's method.  A call into another module is not
    followed, because it needs no following: `peer.f()` reads `peer` here, and a peer that cannot reach this module
    back has finished its own import, and every module it reads has too -- one still importing would reach this
    module, and so would the peer."""
    found, entered = {}, set()

    def run(node, scopes, via):
        """Walk what running `node` runs; `scopes`: [{name: [def]}], innermost first; `via`: the calls that got here."""
        if isinstance(node, FUNCTIONS + (ast.Lambda,)):  # a definition runs its decorators and defaults, never its body
            for d in getattr(node, "decorator_list", []):
                run(d, scopes, via)
                call(d, scopes, via)
            for d in node.args.defaults + [x for x in node.args.kw_defaults if x]:
                run(d, scopes, via)
            return
        if isinstance(node, ast.ClassDef):  # a class statement runs its body, where a bare name reads the body's own defs first
            for d in node.decorator_list:
                run(d, scopes, via)
                call(d, scopes, via)
            for child in node.bases + node.keywords:
                run(child, scopes, via)
            inner = [scope_defs(node.body)] + scopes
            for child in node.body:
                run(child, inner, via)
            return
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
            found.setdefault(aliases[node.value.id], (node.lineno, via))
        if isinstance(node, ast.Call):
            call(node.func, scopes, via)
        for child in ast.iter_child_nodes(node):
            run(child, scopes, via)

    def call(func, scopes, via):
        """Run what a call of `func` runs, where it is a lambda or names a function or class of this module."""
        if isinstance(func, ast.Lambda):
            run(func.body, scopes, via)
        elif isinstance(func, ast.Name):
            for i, scope in enumerate(scopes):
                if func.id in scope:
                    for target in scope[func.id]:
                        enter(target, scopes[i:], via + (func.id,))
                    return

    def enter(target, scopes, via):
        """Run a function's body, or a class's __new__ and __init__, once; `scopes`: where it is defined."""
        if id(target) not in entered:
            entered.add(id(target))
            if isinstance(target, ast.ClassDef):
                methods = scope_defs(target.body)
                for method in methods.get("__new__", []) + methods.get("__init__", []):
                    enter(method, scopes, via)
            else:
                inner = [scope_defs(target.body)] + scopes
                for child in target.body:
                    run(child, inner, via)

    module = [scope_defs(tree.body)]
    for node in tree.body:
        run(node, module, ())
    return found


def cycle_problems(package, name):
    """Module `name` reads no module's name while it is imported, unless that module cannot reach it back."""
    problems = []
    for target, (line, via) in sorted(import_time_reads(package.trees[name], package.aliases[name]).items()):
        if name in reach(package.graph, [target]):
            through = " through %s" % ", then ".join(f + "()" for f in via) if via else ""
            problems.append("%s line %d reads %s while it is imported%s, and %s imports %s back: move the name down a layer" % (
                name, line, target, through, target, name))
    return problems


def handler_problems(package):
    """Every handler dispatch's table names is a function of its module."""
    problems = []
    for event, (module, handler) in handler_table(package.trees[DISPATCH]).items():
        tree = package.trees.get("hooks." + module)
        if tree is None or handler not in {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}:
            problems.append("%s: hooks/%s defines no function %s" % (event, module, handler))
    return problems


def unreachable_modules(package):
    """The modules no chain of imports reaches from the entry, dispatch's run-time import of each handler included."""
    entry = ast.parse(ENTRY.read_text(encoding="utf-8"))
    starts = {".".join(node.module.split(".")[1:] + [a.name]) for node in ast.walk(entry)
              if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("spudlib.") for a in node.names}
    graph = dict(package.graph)
    graph[DISPATCH] = graph[DISPATCH] | {"hooks." + module for module, _ in handler_table(package.trees[DISPATCH]).values()}
    return sorted(set(package.trees) - reach(graph, starts))


def is_program_load(node):
    """A call of load_spud_module(), which returns the entry module spud_ledger."""
    return isinstance(node, ast.Call) and (getattr(node.func, "id", None) == "load_spud_module" or getattr(node.func, "attr", None) == "load_spud_module")


def argument_bindings(call, functions):
    """[(parameter, argument)] for a call of a function the same file defines, by name or as `self.`/`cls.` method."""
    method = isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id in ("self", "cls")
    found = []
    for function in functions.get(call.func.attr if method else getattr(call.func, "id", None), ()):
        params = function.args.posonlyargs + function.args.args
        params = params[1:] if method else params  # the method's own self or cls
        for param, arg in zip(params, call.args):
            if isinstance(arg, ast.Starred):
                break
            found.append((ast.Name(param.arg), arg))
        by_name = {p.arg for p in params + function.args.kwonlyargs}
        found += [(ast.Name(k.arg), k.value) for k in call.keywords if k.arg in by_name]
    return found


def program_touches(tree):
    """[(line, what)] where a test file sets, deletes or patches an attribute of the module load_spud_module() returns.

    Through every name that holds it (SPD-079): the call itself, and a name or a `self.`/`cls.` attribute bound to what
    holds it -- by an assignment, an annotated one, a walrus, a tuple unpacked element by element, a for loop over a
    literal, or an argument to a function or method the file defines, which binds its parameter -- followed until nothing
    new holds, so a chain holds in whatever order the file spells it; a conditional or a boolean expression holds it where
    either side does.  A name is read file-wide, never per scope: one that holds the program anywhere in the file holds
    it everywhere in it."""
    names, attrs = set(), set()  # `spud = load_spud_module()`, `m = spud`; `cls.spud = load_spud_module()`, `self.m = spud`

    def leaves(e):
        """What `e`'s value may be: either side of a conditional or a boolean expression, a walrus's value, else `e`."""
        if isinstance(e, ast.IfExp):
            return leaves(e.body) + leaves(e.orelse)
        if isinstance(e, ast.BoolOp):
            return [leaf for v in e.values for leaf in leaves(v)]
        return leaves(e.value) if isinstance(e, ast.NamedExpr) else [e]

    def own(e):
        return isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name) and e.value.id in ("self", "cls")

    def holds(e):
        return any(is_program_load(x) or (isinstance(x, ast.Name) and x.id in names) or (own(x) and x.attr in attrs) for x in leaves(e))

    functions, calls, stores, bindings = {}, [], [], []

    def bind(target, value):
        """Keep a binding by which `target` may come to hold the program: a tuple's element by element."""
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts):
            for t, v in zip(target.elts, value.elts):
                bind(t, v)
        elif isinstance(target, (ast.Name, ast.Attribute)) and any(is_program_load(x) or isinstance(x, ast.Name) or own(x) for x in leaves(value)):
            bindings.append((target, value))

    for node in ast.walk(tree):
        if isinstance(node, FUNCTIONS):
            functions.setdefault(node.name, []).append(node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                bind(target, node.value)
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            bind(node.target, node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)) and isinstance(node.iter, (ast.Tuple, ast.List, ast.Set)):
            for e in node.iter.elts:
                bind(node.target, e)
        elif isinstance(node, ast.Call):
            calls.append(node)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            stores.append(node)
    for node in calls:
        for target, value in argument_bindings(node, functions):
            bind(target, value)
    changed = True
    while changed:  # until nothing new holds: a chain holds in whatever order the file spells it
        changed = False
        for target, value in bindings:
            held, key = (names, target.id) if isinstance(target, ast.Name) else (attrs, target.attr)
            if key not in held and holds(value):
                held.add(key)
                changed = True

    found = [(node.lineno, "sets or deletes .%s" % node.attr) for node in stores if holds(node.value)]
    for node in calls:
        called = getattr(node.func, "attr", None) or getattr(node.func, "id", "")
        first = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "target"), None)
        if called in ("object", "multiple", "setattr", "delattr") and first is not None and holds(first):
            found.append((node.lineno, "%s on the loaded program" % called))
        elif called == "patch" and isinstance(first, ast.Constant) and str(first.value).split(".")[0] == "spud_ledger":
            found.append((node.lineno, "patch(%r)" % first.value))
    return sorted(found)


class PackageShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = real_package()

    def test_a_module_imports_modules_of_the_package_never_names_and_only_at_its_top(self):
        self.assertEqual([p for name in self.package.trees for p in import_problems(self.package, name)], [])

    def test_every_name_is_defined_in_one_module_and_no_module_binds_an_alias_or_a_module_name(self):
        self.assertEqual(definition_problems(self.package), [])
        self.assertEqual([p for name in self.package.trees for p in alias_bindings(self.package, name)], [])

    def test_no_module_reads_a_name_across_an_import_cycle_while_it_is_imported(self):
        self.assertEqual([p for name in self.package.trees for p in cycle_problems(self.package, name)], [])

    def test_every_handler_is_a_function_of_its_module(self):
        self.assertEqual(handler_problems(self.package), [])

    def test_every_module_is_reachable_from_the_entry(self):
        self.assertEqual(unreachable_modules(self.package), [])


class DamagedCopyTest(unittest.TestCase):
    """Each check above fails a copy of the package damaged the way it promises to catch, so a check that has quietly
    narrowed fails here instead of passing the next real change (SPD-079).  A copy is this checkout's package with one
    module's text added to or replaced, held in memory and never written; a test file's damage is source text
    program_touches reads.  Eight are the SPD-065 review's damaged copies (docs/spikes/spd-065/review.md in Spud's home;
    its ninth, a hook importing a module off HOOK_PATH, is HookPathTest's, which runs the launcher rather than read a
    copy); the rest each passed the checks before SPD-079, finding 1 of that review."""

    @classmethod
    def setUpClass(cls):
        cls.package = real_package()

    def appended(self, name, text):
        """The package with `text` added at the end of module `name`."""
        return self.package.edited(name, self.package.texts[name] + "\n\n" + textwrap.dedent(text))

    # -- the review's eight

    def test_an_import_naming_no_module_fails(self):
        copy = self.appended("shell.analyse", "from . import no_such_module\n")
        self.assertIn("shell.analyse: `from ... import` names shell.no_such_module, which is not a module of the package",
                      import_problems(copy, "shell.analyse"))

    def test_an_import_inside_a_function_fails(self):
        copy = self.appended("core.lazy", "def _scratch():\n    from . import kernel\n")
        self.assertTrue([p for p in import_problems(copy, "core.lazy") if "an import inside a function" in p])

    def test_a_name_defined_in_two_modules_fails(self):
        name = next(n.name for n in self.package.trees["core.kernel"].body if isinstance(n, ast.FunctionDef))
        copy = self.appended("core.lazy", "def %s():\n    pass\n" % name)
        self.assertIn("%s is defined in core.kernel and core.lazy" % name, definition_problems(copy))

    def test_a_module_nothing_imports_fails(self):
        copy = self.package.edited("core.orphan", '"""core/orphan: nothing imports it."""\n')
        self.assertEqual(unreachable_modules(copy), ["core.orphan"])

    def test_a_handler_that_is_no_function_of_its_module_fails(self):
        event, (module, handler) = sorted(handler_table(self.package.trees[DISPATCH]).items())[0]
        text = self.package.texts["hooks." + module]
        self.assertEqual(text.count("def %s(" % handler), 1)
        copy = self.package.edited("hooks." + module, text.replace("def %s(" % handler, "def %s_renamed(" % handler))
        self.assertEqual(handler_problems(copy), ["%s: hooks/%s defines no function %s" % (event, module, handler)])

    def test_a_read_across_the_cycle_at_the_top_of_a_module_fails(self):
        self.assertIn("shell.analyse", reach(self.package.graph, ["shell.walk"]))  # the cycle this case reads across
        copy = self.appended("shell.analyse", "_SCRATCH = walk.ShellWalk\n")
        line = copy.texts["shell.analyse"].splitlines().index("_SCRATCH = walk.ShellWalk") + 1
        self.assertEqual(cycle_problems(copy, "shell.analyse"), [
            "shell.analyse line %d reads shell.walk while it is imported, and shell.walk imports shell.analyse back: move the name down a layer" % line])

    def test_a_test_patching_the_entry_by_name_or_the_loaded_program_fails(self):
        for damage in ('mock.patch("spud_ledger.now", lambda: 0)', 'mock.patch.object(spud, "now", lambda: 0)', "spud.now = None", "del spud.now"):
            with self.subTest(damage):
                self.assertTrue(program_touches(ast.parse(TEST_FILE_HEAD + damage + "\n")))

    # -- what the checks passed before SPD-079

    def test_an_alias_bound_in_any_scope_by_any_binding_form_fails(self):
        alias = {"shell.analyse": "walk", "shell.globbing": "syntax"}
        self.assertEqual(self.package.aliases["shell.analyse"]["walk"], "shell.walk")
        self.assertEqual(self.package.aliases["shell.globbing"]["syntax"], "shell.syntax")
        for module, damage in ALIAS_BINDINGS:
            with self.subTest(damage):
                problems = alias_bindings(self.appended(module, damage), module)
                self.assertTrue(problems)
                self.assertEqual([p for p in problems if " %s" % alias[module] not in p], [])

    def test_a_module_name_bound_where_its_module_is_not_imported_fails(self):
        # core/lazy imports no module of the package, so the check before SPD-237, which read only the aliases a module
        # imports, passed every one of these.
        self.assertNotIn("walk", self.package.aliases["core.lazy"])
        for damage in MODULE_NAME_BINDINGS:
            with self.subTest(damage):
                problems = alias_bindings(self.appended("core.lazy", damage), "core.lazy")
                self.assertTrue(problems)
                self.assertEqual([p for p in problems if not p.startswith("core.lazy binds walk, the name of module shell.walk, ")], [])

    def test_a_module_name_as_a_global_or_a_class_attribute_passes(self):
        for damage in MODULE_NAME_ATTRIBUTES:
            with self.subTest(damage):
                self.assertEqual(alias_bindings(self.appended("core.lazy", damage), "core.lazy"), [])

    def test_a_read_across_the_cycle_through_a_call_at_import_fails(self):
        lines = len(self.package.texts["shell.analyse"].splitlines())
        for damage in CALLED_READS:
            with self.subTest(damage):
                problems = cycle_problems(self.appended("shell.analyse", damage), "shell.analyse")
                self.assertEqual(len(problems), 1, problems)
                self.assertRegex(problems[0], r"^shell\.analyse line \d+ reads shell\.walk while it is imported")
                self.assertGreater(int(problems[0].split()[2]), lines, problems)  # the read the damage added, and no other
        copy = self.appended("shell.analyse", CALLED_READS[0])
        line = copy.texts["shell.analyse"].splitlines().index("    return walk.ShellWalk", lines) + 1
        self.assertEqual(cycle_problems(copy, "shell.analyse"), [
            "shell.analyse line %d reads shell.walk while it is imported through _probe(), and shell.walk imports shell.analyse back: "
            "move the name down a layer" % line])

    def test_a_read_no_call_at_import_reaches_passes(self):
        for damage in UNCALLED_READS:
            with self.subTest(damage):
                self.assertEqual(cycle_problems(self.appended("shell.analyse", damage), "shell.analyse"), [])

    def test_a_test_patching_the_loaded_program_through_another_name_fails(self):
        for damage in PROGRAM_PATCHES:
            with self.subTest(damage):
                self.assertTrue(program_touches(ast.parse(TEST_FILE_HEAD + textwrap.dedent(damage))))

    def test_a_test_reading_the_loaded_program_passes(self):
        for damage in PROGRAM_READS:
            with self.subTest(damage):
                self.assertEqual(program_touches(ast.parse(TEST_FILE_HEAD + textwrap.dedent(damage))), [])


# (module, text appended to it): each binds an alias the module imports -- shell/analyse imports shell/walk as `walk`,
# shell/globbing shell/syntax as `syntax` -- by a form the check read no binding from before SPD-079.  The first two are
# the review's own.
ALIAS_BINDINGS = [
    ("shell.analyse", "def _scratch():\n    def walk():\n        pass\n"),
    ("shell.globbing", "def _scratch():\n    try:\n        pass\n    except Exception as syntax:\n        pass\n"),
    ("shell.analyse", "def _scratch():\n    class walk:\n        pass\n"),
    ("shell.analyse", "def _scratch():\n    async def walk():\n        pass\n"),
    ("shell.analyse", "async def _scratch(walk):\n    pass\n"),
    ("shell.analyse", "def _scratch(x):\n    match x:\n        case [walk]:\n            pass\n"),
    ("shell.analyse", "def _scratch(x):\n    match x:\n        case [*walk]:\n            pass\n"),
    ("shell.analyse", "def _scratch(x):\n    match x:\n        case {**walk}:\n            pass\n"),
    ("shell.analyse", "def _scratch():\n    import json as walk\n"),
    ("shell.analyse", "def _scratch():\n    del walk\n"),
    ("shell.analyse", "def _scratch[walk]():\n    pass\n"),
    ("shell.analyse", "def walk():\n    pass\n"),
    ("shell.analyse", "class walk:\n    pass\n"),
    ("shell.analyse", "from . import zsh as walk\n"),
]
# Text appended to core/lazy, which imports no module of the package: each binds `walk`, shell/walk's basename, as a
# local, a parameter or a comprehension's name, which shadows that module the day core/lazy imports it (SPD-237).
MODULE_NAME_BINDINGS = [
    "def _scratch(walk):\n    return walk\n",
    "def _scratch():\n    walk = None\n    return walk\n",
    "def _scratch(x):\n    for walk in x:\n        pass\n",
    "def _scratch():\n    with open('x') as walk:\n        return walk\n",
    "def _scratch(x):\n    return [walk for walk in x]\n",
    "def _scratch(x):\n    return {k: walk for k, walk in x}\n",
    "class _Scratch:\n    def method(self, *walk):\n        pass\n",
    "_SCRATCH = lambda walk: walk\n",
    "_SCRATCH = [walk for walk in ()]\n",
    "class _Scratch:\n    KINDS = [walk for walk in ()]\n",
]
# Text appended to core/lazy that names `walk` without binding it as a local, a parameter or a comprehension's name.
MODULE_NAME_ATTRIBUTES = [
    "class _Scratch:\n    walk = None\n",
    "class _Scratch:\n    def walk(self):\n        pass\n",
    "class _Scratch:\n    def __init__(self):\n        self.walk = None\n",
    "def _scratch(x):\n    return x.walk\n",
]
# Text appended to shell/analyse: each reads shell/walk, which imports shell/analyse back, in a function the import runs.
# The first is the review's own.
CALLED_READS = [
    "def _probe():\n    return walk.ShellWalk\n\n\n_PROBE = _probe()\n",
    "def _inner():\n    return walk.ShellWalk\n\n\ndef _outer():\n    return _inner()\n\n\n_PROBE = _outer()\n",
    "def _outer():\n    def _inner():\n        return walk.ShellWalk\n    return _inner()\n\n\n_PROBE = _outer()\n",
    "def _probe(n):\n    return _probe(n - 1) if n else walk.ShellWalk\n\n\n_PROBE = _probe(2)\n",
    "def _mark(fn):\n    fn.kind = walk.ShellWalk\n    return fn\n\n\n@_mark\ndef _scratch():\n    pass\n",
    "def _marks(kind):\n    return lambda fn: fn\n\n\ndef _kind():\n    return walk.ShellWalk\n\n\n@_marks(_kind())\ndef _scratch():\n    pass\n",
    "class _Probe:\n    def __init__(self):\n        self.kind = walk.ShellWalk\n\n\n_PROBE = _Probe()\n",
    "def _probe():\n    return walk.ShellWalk\n\n\nclass _Scratch:\n    KIND = _probe()\n",
    "def _probe():\n    return walk.ShellWalk\n\n\ndef _scratch(kind=_probe()):\n    return kind\n",
    "def _probe():\n    return walk.ShellWalk\n\n\n_PROBES = [_probe() for _ in range(2)]\n",
    "_PROBE = (lambda: walk.ShellWalk)()\n",
]
# Text appended to shell/analyse: each reads shell/walk only when something calls it after the import is done.
UNCALLED_READS = [
    "def _probe():\n    return walk.ShellWalk\n",
    "def _probe():\n    return walk.ShellWalk\n\n\n_PROBES = (_probe,)\n",
    "def _factory():\n    return lambda: walk.ShellWalk\n\n\n_PROBE = _factory()\n",
    "def _factory():\n    def _inner():\n        return walk.ShellWalk\n    return _inner\n\n\n_PROBE = _factory()\n",
    "class _Probe:\n    def __init__(self):\n        self.kind = None\n\n    def kind_now(self):\n        return walk.ShellWalk\n\n\n_PROBE = _Probe()\n",
]
TEST_FILE_HEAD = "from unittest import mock\n\nfrom helpers import load_spud_module\n\nspud = load_spud_module()\n\n"
# Test-file text after TEST_FILE_HEAD: each patches the loaded program through a name the check did not follow before
# SPD-079, or names it by a keyword.  The first is the review's own.
PROGRAM_PATCHES = [
    'm = spud\nmock.patch.object(m, "now", lambda: 0)\n',
    'm = spud\nmodule = m\nsetattr(module, "now", None)\n',
    'def later():\n    mock.patch.object(program, "now", lambda: 0)\n\n\nprogram = spud\n',
    'class T:\n    def setUp(self):\n        self.program = spud\n\n    def test(self):\n        self.program.now = None\n',
    'class T:\n    def setUp(self):\n        self.program = load_spud_module()\n\n    def test(self):\n        m = self.program\n        del m.now\n',
    'm, other = spud, None\nmock.patch.multiple(m, now=lambda: 0)\n',
    'm = spud if other else None\ndelattr(m, "now")\n',
    'if (m := spud) is not None:\n    mock.patch.object(m, "now", lambda: 0)\n',
    'for m in (spud,):\n    mock.patch.object(m, "now", lambda: 0)\n',
    'def patched(module):\n    return mock.patch.object(module, "now", lambda: 0)\n\n\npatched(spud)\n',
    'class T:\n    def patched(self, name, module=None):\n        setattr(module, name, None)\n\n    def test(self):\n        self.patched("now", module=spud)\n',
    'mock.patch.object(target=spud, attribute="now", new=lambda: 0)\n',
    'mock.patch(target="spud_ledger.now", new=lambda: 0)\n',
]
# Test-file text after TEST_FILE_HEAD that reads the loaded program, or patches what is no attribute of it.
PROGRAM_READS = [
    'm = spud\nnow = m.now\n',
    'm = spud.now\nmock.patch.object(m, "__doc__", "")\n',
    'mock.patch("spudlib.core.kernel.now", lambda: 0)\n',
    'def patched(module):\n    return mock.patch.object(module, "now", lambda: 0)\n\n\npatched(mock.Mock())\n',
    'class T:\n    def patched(self, module):\n        module.now = None\n\n    def test(self):\n        self.patched(spud.now)\n',
]


class ShippedPathsTest(unittest.TestCase):
    """SPW-002: no file this repository ships names a machine's home directory -- the installed spudagent definition is
    rendered from Ctx at install (projects/agentdef), the way the /spud skill's text is, and every command in the prose
    finds the launcher from git instead of spelling one machine's path.  Recorded test data (a spudagent's return text in
    tests/fixtures/team_card.json, a Bash hook payload in tests/hookcase.py) spells a neutral /Users/Someone, so no file
    is exempt (SPD-158)."""

    RECORDED = set()
    SKIP = (".git", ".claude/worktrees")  # git's own store, and a linked worktree checked out inside the main one

    def shipped(self):
        """[(path, bytes)] for every file this repository ships: the tree, less what git ignores -- the rule
        tests/suite.py's snapshot copies by, and the one that leaves out a session's own runtime state (.omc/,
        .claude/settings.local.json), which is nobody's deliverable and names this machine freely."""
        rels = [p.relative_to(REPO).as_posix() for p in sorted(REPO.rglob("*")) if p.is_file()]
        rels = [r for r in rels if r not in self.RECORDED and not any(r == s or r.startswith(s + "/") for s in self.SKIP)]
        proc = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-z", "--stdin"], input="\0".join(rels), capture_output=True, text=True)
        ignored = {r for r in proc.stdout.split("\0") if r} if proc.returncode in (0, 1) else set()
        return [(rel, (REPO / rel).read_bytes()) for rel in rels if rel not in ignored]

    def test_no_shipped_file_names_a_machines_home_directory(self):
        # This machine's home, whichever machine runs the suite, and the one path SPW-002 took out of six shipped files.
        needles = sorted({str(Path.home()), "/Users/" + "ericlug" + "o"})
        shipped = self.shipped()
        self.assertGreater(len(shipped), 50, "the shipped files were not found; this guard would pass vacuously")
        self.assertEqual([(rel, needle) for rel, data in shipped for needle in needles if needle.encode("utf-8") in data], [])

    def test_the_spudagent_definition_is_a_template_install_renders(self):
        text = (REPO / "share" / "agents" / "spudagent.md").read_text(encoding="utf-8")  # SPW-004: under share/, not .claude/
        self.assertTrue(text.startswith("---\nname: spudagent\n"), text[:64])  # the frontmatter the installed copy needs
        spud = load_spud_module()
        self.assertIn(spud.LAUNCHER_MARK, text)
        rendered = spud.render_launcher(text, "/somewhere/Spud/bin/spud")
        self.assertIn("python3.14 -I -S /somewhere/Spud/bin/spud", rendered)
        self.assertNotIn(spud.LAUNCHER_MARK, rendered)

    def test_this_repository_tracks_no_project_scope_spudagent_definition(self):
        """SPW-004: Claude Code reads `<checkout>/.claude/agents/spudagent.md` in preference to the user-scope copy
        `project install` writes, so the template this repository used to keep there was the definition every spudagent
        working a ticket in this checkout actually read -- an unrendered `{{launcher}}` and all.  A file full of {{marks}}
        was never a usable agent definition; it only shadowed the one that was.  It lives under share/ now, and nothing
        here puts one back: this guard is the whole defect, in one line.  SPD-222 made it six definitions, the base and one
        per effort level, and a project-scope copy of any of them would shadow its installed copy the same way."""
        spud = load_spud_module()
        for name in spud.SPUDAGENT_TYPES:
            shadow = spud.project_scope_agent(REPO, name)
            self.assertFalse(shadow.exists(), "%s is back and shadows the installed definition (SPW-004)" % shadow)
        # And this repository's CLAUDE.md, which named that path twice: the source is share/agents/spudagent.md, and the
        # only .claude/agents/spudagent.md left in the prose is the installed user-scope copy `project sync` refreshes.
        claude_md = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("share/agents/spudagent.md", claude_md)
        named = set(re.findall(r"[-~\w./]*\.claude/agents/spudagent\.md", claude_md))
        self.assertEqual(named, {"~/.claude/agents/spudagent.md"})


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
        package = module_names()
        seen = set()
        for event, extra in payloads.items():
            payload = dict(extra, hook_event_name=event, **common)
            proc = subprocess.run([sys.executable, "-I", "-S", "-c", MODULES_AT_EXIT, str(SPUD), "hook", event],
                                  input=json.dumps(payload), capture_output=True, text=True, env=self.home.env)
            self.assertEqual(proc.returncode, 0, proc)
            imported = proc.stderr.rsplit("\nMODULES ", 1)[-1].split()
            ours = {m[len("spudlib."):] for m in imported if m.startswith("spudlib.")} & package
            self.assertEqual(ours - HOOK_PATH, set(), event)
            seen |= ours
        self.assertEqual(seen, HOOK_PATH)  # the list stays exact: a module no hook imports any more leaves it

    def test_a_hook_lists_an_archive_only_on_a_line_that_extracts_one(self):
        # SPD-144: shell/archive_names reads an archive's names through core/lazy's tarfile and zipfile, which with the
        # compression modules they import would cost every hook run; a line holding no archive to list loads none of them.
        with tarfile.open(self.home.path / "a.tar", "w") as tf:
            tf.addfile(tarfile.TarInfo("a.txt"))
        (self.home.path / "out").mkdir()
        archive_modules = {"tarfile", "zipfile", "gzip", "bz2", "lzma", "compression.zstd"}
        for command, loads in (("git status", set()), ("tar -tf a.tar", set()), ("tar -xf missing.tar -C out", set()),
                               ("rsync -a out/ /tmp/spd-144-r/", set()), ("patch -d out -p1 -i missing.patch", set()),
                               ("tar -xf a.tar -C out", {"tarfile"}), ("unzip a.tar -d out", {"zipfile"})):
            payload = {"session_id": "s", "cwd": str(self.home.path), "hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "tool_use_id": "t", "tool_input": {"command": command}}
            proc = subprocess.run([sys.executable, "-I", "-S", "-c", MODULES_AT_EXIT, str(SPUD), "hook", "PreToolUse"],
                                  input=json.dumps(payload), capture_output=True, text=True, env=self.home.env)
            self.assertEqual(proc.returncode, 0, proc)
            imported = set(proc.stderr.rsplit("\nMODULES ", 1)[-1].split())
            with self.subTest(command):
                if loads:
                    self.assertLessEqual(loads, imported)
                    self.assertNotIn("tarfile" if "zipfile" in loads else "zipfile", imported)
                else:
                    self.assertEqual(imported & archive_modules, set())

    def test_a_command_imports_no_module_of_the_shell_package(self):
        # Every command loads commands/doctor, and so hooks/snapshots: its ANSI-C decoder sat in shell/prepare, and every
        # `spud` run -- each `member log` a spudagent makes -- paid for shell/prepare and shell/syntax (SPD-202, SPD-216).
        proc = subprocess.run([sys.executable, "-I", "-S", "-c", MODULES_AT_EXIT, str(SPUD), "board"],
                              capture_output=True, text=True, env=self.home.env)
        self.assertEqual(proc.returncode, 0, proc)
        imported = proc.stderr.rsplit("\nMODULES ", 1)[-1].split()
        self.assertIn("spudlib.hooks.snapshots", imported)
        self.assertEqual([m for m in imported if m.startswith("spudlib.shell")], [])


if __name__ == "__main__":
    unittest.main()
