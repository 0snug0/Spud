"""tests/suite_trace.py: what one process runs of bin/ and reads of share/, recorded for the dependency table (SPD-233).

tests/suite_deps.py runs each test module in a process of its own with SPUD_SUITE_TRACE naming a directory.  In that
process tests/helpers calls start() before any test runs, and every `bin/spud` a test starts runs through a shim that
calls start() first (helpers.trace_launcher), so each process -- the test process and each CLI or hook process it starts
-- writes one record into the directory when it exits: the source files under bin/ whose functions it ran, and the files
under bin/ and share/ it opened other than to import them.  A process started for the per-process fixture's `spud init`
(helpers.fixture) records under the role `fixture`, every other under `test`.

What counts as run is a function, not a module: a module's body and its classes' bodies, which run at import, are left
out, because the entry's `spud_ledger.<name>` imports every module of the package at once for the suite, and an import is
not a use.  A module whose own functions never run but whose tables or classes another module reads is reached through
that module at selection time (tests/suite.py reads, from the tree it covers, which module reads which module's data),
which is also why a table measured on one tree stays sound while a later commit changes who reads whom.

Standard library only, no import of the program, nothing written but the one record at exit.
"""

import atexit
import json
import os
import sys

CO_OPTIMIZED = 0x0001  # inspect.CO_OPTIMIZED: set on a function's code object, never on a module's or a class body's
ENV = "SPUD_SUITE_TRACE"  # the directory the records go into
ROLE_ENV = "SPUD_SUITE_TRACE_ROLE"  # `fixture` for the fixture's own init, else unset (a test's)
_STARTED = []


def _imported_by_the_import_system():
    """Whether the open being audited is the import system reading a module's source: a frame of importlib's own."""
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_filename.startswith("<frozen importlib"):
            return True
        frame = frame.f_back
    return False


def start(repo, directory, role="test"):
    """Record, from now until this process exits, what it runs under `repo`/bin and opens under `repo`/bin and `repo`/share,
    into a file of its own in `directory`.  A second call in one process does nothing."""
    if _STARTED or not directory:
        return
    _STARTED.append(True)
    root = os.path.realpath(str(repo))
    roots = tuple(os.path.join(root, d) + os.sep for d in ("bin", "share"))
    bin_root = roots[0]
    ran, opened = set(), set()
    monitoring = sys.monitoring
    tool = next((t for t in (monitoring.COVERAGE_ID, monitoring.PROFILER_ID, 3, 4) if monitoring.get_tool(t) is None), None)

    def on_start(code, offset):
        name = code.co_filename
        if name.startswith(bin_root) and code.co_flags & CO_OPTIMIZED:  # a function's code: never a module's or a class's body
            ran.add(name)
        return monitoring.DISABLE  # one event per code object is all a record needs

    if tool is not None:
        monitoring.use_tool_id(tool, "spud-suite-trace")
        monitoring.register_callback(tool, monitoring.events.PY_START, on_start)
        monitoring.set_events(tool, monitoring.events.PY_START)

    def on_audit(event, args):
        if event != "open" or not args:
            return
        path = args[0]
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        if not isinstance(path, str):
            return  # a descriptor
        try:
            real = os.path.realpath(path if os.path.isabs(path) else os.path.join(os.getcwd(), path))
        except (OSError, ValueError):
            return
        if real.startswith(roots) and not _imported_by_the_import_system():
            opened.add(real)

    sys.addaudithook(on_audit)

    def write():
        rel = lambda paths: sorted(os.path.relpath(p, root) for p in paths)
        record = {"role": role, "pid": os.getpid(), "argv": sys.argv[1:4], "ran": rel(ran), "opened": rel(opened)}
        try:
            os.makedirs(directory, exist_ok=True)
            with open(os.path.join(directory, "%s-%d.json" % (role, os.getpid())), "w", encoding="utf-8") as f:
                json.dump(record, f)
        except OSError:
            pass  # a process whose directory went away records nothing; the table is only as good as its records

    atexit.register(write)


SHIM = '''"""The launcher, traced: records what this run of bin/spud runs and reads (tests/suite_trace.py), then runs it."""
import os
import sys
sys.path.insert(0, %(tests)r)
import suite_trace
suite_trace.start(%(repo)r, %(directory)r, os.environ.get(suite_trace.ROLE_ENV) or "test")
import runpy
runpy.run_path(%(launcher)r, run_name="__main__")
'''


def write_shim(path, repo, directory, launcher):
    """A file named `spud` that runs `launcher` traced into `directory`: what helpers.SPUD names while tracing.  Named spud
    so that argparse's program name, which is the basename of sys.argv[0], is the launcher's own."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SHIM % {"tests": os.path.dirname(os.path.abspath(__file__)), "repo": str(repo), "directory": str(directory),
                            "launcher": str(launcher)}, encoding="utf-8")
    return path
