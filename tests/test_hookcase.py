"""tests/hookcase.py's own premises, checked against the hook's source (SPD-231).

BashHookCase.assertAnsweredAs lets a payload's answers stand for a form that holds it wherever the two read alike, and
"alike" is hook_reading: the raw text's database match and the analysis fields named in hookcase.HOOK_READING.  That is
only sound while the hook's decision is a function of nothing else, so HookReadingCoverTest reads the Bash hook's decision
path -- shell/bash_rule and hooks/pretool -- and fails when it reads a field of the analysis HOOK_READING does not compare,
hands the analysis somewhere this test cannot follow, or uses the raw command text beyond the uses tabled here.  A change
that trips it adds the field to HOOK_READING (the matrices then assert more forms on the line), or tables the new use of
the text with why it cannot tell a form from the command it holds.
"""

import ast
import unittest

from helpers import REPO
from hookcase import HOOK_READING

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


if __name__ == "__main__":
    unittest.main()
