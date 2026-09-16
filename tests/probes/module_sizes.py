"""Application-code file sizes, banded and advisory (SPD-080).

  python3.14 -I -S tests/probes/module_sizes.py [PATH ...]

**Advisory.  This is a report, never a gate: it prints what it found and exits 0 whatever it finds.**  unittest does
not collect it, no band is a failure, and nothing in the suite or the hooks depends on what it says.

Eric's rule (SPD-065, 2026-09-15): ~250 lines is the point at which a module is worth a second look, never a cap.  A
module may be as large as it needs to be, and the larger it gets the more it must justify itself.  No cohesive
function, class or region is ever cut to fit a number.  1000 lines is the size that started SPD-065 here and BAD-036 in
BadTakes, so a file that reaches it is worth a ticket rather than a quiet edit.

Application code only, `bin/` by default: the launcher, the entry, and the 58 modules of the package spudlib.  Tests
are out of scope by the same clarification, so no file under a directory named `tests` is ever read, whatever path you
name, and neither is a `test_*.py`; stylesheets, HTML and markdown are not application code.  A path argument may be a
file or a directory, so the probe reports on another project's tree too:

  python3.14 -I -S tests/probes/module_sizes.py ~/Personal/BadTakes/src

For each listed file it prints the line count, then its largest top-level definition — a function, a class or a
constant — and that definition's share of the file, because the share is the evidence the rule asks for: a file that is
one 282-line function is one thing and a move cannot divide it, while a file whose largest definition is a twentieth of
it is a list of things and may hold a seam worth taking.  The shape these modules were built on is `.claude/skills/spudlib-modules/SKILL.md`.
"""

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_ROOTS = [os.path.join(REPO, "bin")]

LOOK_AGAIN = 250  # worth a second look, never a cap (Eric, SPD-065)
TICKET = 1000  # the size that started SPD-065 and BAD-036: worth a ticket, not a quiet edit
BANDS = [
    (TICKET, "%d and over — worth a ticket, not a quiet edit" % TICKET),
    (LOOK_AGAIN, "%d and over — worth a second look" % LOOK_AGAIN),
]
SKIP_DIRS = {"tests", "__pycache__", "node_modules"}  # a test file is out of scope; the others hold no source


def is_python(path):
    """A Python source file: the extension, else a shebang naming python, which is how bin/spud is one."""
    path = os.fspath(path)
    if path.endswith(".py"):
        return True
    if os.path.splitext(path)[1]:
        return False
    try:
        with open(path, "rb") as f:
            first = f.readline(200)
    except OSError:
        return False
    return first.startswith(b"#!") and b"python" in first


def is_application_code(path):
    """Application code by Eric's scope: Python, not a test file, not under a directory that holds none."""
    name = os.path.basename(path)
    if name.startswith("test_") or name.endswith("_test.py"):
        return False
    if any(part in SKIP_DIRS for part in os.path.abspath(path).split(os.sep)[:-1]):
        return False
    return is_python(path)


def sources(root):
    """Every application-code file at or under `root`, sorted.  A file argument is read as given; a walk skips the
    directories above and any dot directory below the root, so a checkout reached through one (`.claude/worktrees/…`)
    still reports on itself."""
    root = os.path.abspath(root)
    if os.path.isfile(root):
        return [root] if is_application_code(root) else []
    found = []
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = sorted(d for d in subdirs if d not in SKIP_DIRS and not d.startswith("."))
        found.extend(p for p in (os.path.join(directory, f) for f in sorted(files)) if is_application_code(p))
    return found


def definitions(tree):
    """[(name, lines)] for every top-level function, class and assignment, largest first: a decorator counts with its
    function, and an assignment counts because 170 lines of SQL or a word table is a region a move cannot divide
    either (bin/spudlib/state/schema.py is one)."""
    found = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            found.append((node.name, node.end_lineno - start + 1))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if names:
                found.append((names[0], node.end_lineno - node.lineno + 1))
    return sorted(found, key=lambda d: (-d[1], d[0]))


def measure(path):
    """(lines, definitions largest first, or None when the file does not parse)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    try:
        defs = definitions(ast.parse(text, path))
    except SyntaxError:
        defs = None
    return len(text.splitlines()), defs


def band(lines):
    """The band's floor: TICKET, LOOK_AGAIN, or 0 for a file this report only counts."""
    return next((floor for floor, _ in BANDS if lines >= floor), 0)


def shown(path):
    """The path as the reader knows it: relative to the checkout when it is inside one."""
    inside = os.path.relpath(path, REPO)
    return path if inside.startswith(os.pardir) else inside


def largest(defs, lines):
    """What makes the file its size, for the reader's judgment, never for a verdict."""
    if defs is None:
        return "does not parse"
    if not defs:
        return "no top-level definition"
    name, span = defs[0]
    return "largest: %s, %d lines, %d%% of the file; %d definition%s" % (name, span, round(100 * span / max(lines, 1)),
                                                                        len(defs), "" if len(defs) == 1 else "s")


def collect(roots):
    """([(lines, path, definitions)] largest first, and what was skipped and why)."""
    files, seen = [], set()
    skipped = ["no such path: %s" % r for r in roots if not os.path.exists(r)]
    for root in roots:
        for path in sources(root):
            if path in seen:
                continue
            seen.add(path)
            try:
                lines, defs = measure(path)
            except OSError as e:  # a report never fails on one file it could not read
                skipped.append("unreadable: %s (%s)" % (shown(path), e))
                continue
            files.append((lines, path, defs))
    return sorted(files, key=lambda f: (-f[0], f[1])), skipped


def report(roots, out=sys.stdout):
    """Print the report.  Returns nothing a caller should branch on: the probe's exit code is 0, always."""
    write = out.write
    files, skipped = collect(roots)
    write("module sizes (SPD-080) — advisory: a report, never a gate; this probe exits 0 whatever it finds\n")
    write("roots: %s\n" % ", ".join(shown(os.path.abspath(r)) for r in roots))
    write("~%d lines is the point at which a module is worth a second look, never a cap.  A module may be as large as\n" % LOOK_AGAIN)
    write("it needs to be, and the larger it gets the more it must justify itself; no cohesive function, class or\n")
    write("region is ever cut to fit a number (Eric, SPD-065).  %d is the size that started SPD-065 and BAD-036.\n\n" % TICKET)

    for why in skipped:
        write("  skipped, %s\n" % why)
    if skipped:
        write("\n")

    for floor, title in BANDS:
        in_band = [f for f in files if band(f[0]) == floor]
        write("%s: %d file%s\n" % (title, len(in_band), "" if len(in_band) == 1 else "s"))
        for lines, path, defs in in_band:
            write("  %5d  %-44s %s\n" % (lines, shown(path), largest(defs, lines)))
        if not in_band:
            write("  (none)\n")
        write("\n")

    under = [f for f in files if band(f[0]) == 0]
    write("%d file%s, %d lines.  Under %d: %d file%s" % (len(files), "" if len(files) == 1 else "s",
                                                         sum(f[0] for f in files), LOOK_AGAIN, len(under),
                                                         "" if len(under) == 1 else "s"))
    write(", largest %d (%s).\n" % (under[0][0], shown(under[0][1])) if under else ".\n")
    write("Nothing above is a failure.  A band is a question — what does this file hold, and is it one thing? — which\n")
    write("the code answers, or a ticket does once a file reaches %d.  The rules these modules were built on:\n" % TICKET)
    write(".claude/skills/spudlib-modules/SKILL.md\n")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in ("-h", "--help"):
        sys.stdout.write(__doc__)
        return 0
    report(argv or DEFAULT_ROOTS)
    return 0  # always: a gate is not this probe's job


if __name__ == "__main__":
    sys.exit(main())
