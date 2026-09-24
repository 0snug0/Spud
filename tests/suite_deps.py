"""tests/suite_deps.py: measure the dependency table tests/suite.py --changed selects by, tests/suite_deps.json (SPD-233).

  python3.14 -I -S tests/suite_deps.py [-j N] [MODULE ...]

Run it from the checkout (or a worktree of it) after a change that could move what a test module runs -- a new test
module, a test that now reaches a command it did not, a module added to or split in bin/spudlib, a file added to share/
-- and commit the table it writes with the change.  A run takes about as long as the full suite, holds the same machine
lock, and writes nothing but tests/suite_deps.json into the checkout.

How it measures.  The tree git lists is snapshotted as tests/suite.py snapshots it, and each test module runs there in a
process of its own (`-m unittest discover -p <module>.py`, -j at a time), with SPUD_SUITE_TRACE naming a directory of
its own: tests/helpers then records that process, and every CLI and hook process its tests start runs through a shim that
records it too (tests/suite_trace.py), each writing, at exit, the source files under bin/ whose functions it ran and the
files under bin/ and share/ it opened.  The `spud init` that builds the per-process fixture records apart, as `fixture`:
everything it runs or reads, every SpudTestCase test starts from, so a change there, or to a module whose data one of
them reads, runs the full suite.  A module's entry lists the rest.  Named MODULEs measure those modules alone and replace
their entries in the table already written.  The table records the digest of the bin/ and share/ it was measured on
(`code`), and `tests/suite.py --changed` warns when the tree's differ; named MODULEs record the tree's as a full run does,
so measure every module after a change that could move what more than those modules run.

What tests/suite.py does with it is the table's own `about`, its docstring's --changed paragraph and the `about` of
tests/suite_map.json.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import suite  # noqa: E402  (this directory's runner: the tree, the snapshot, the lock)

CHECKOUT = suite.CHECKOUT
TABLE = "tests/suite_deps.json"
ROOTS = suite.TABLE_ROOTS
ABOUT = [
    "The dependency table tests/suite.py --changed selects by (SPD-233): what each test module ran of bin/ and read of bin/",
    "and share/, measured by tests/suite_deps.py (run it again after a change that moves what a module runs; its docstring",
    "says when).  `tree` is the digest of the tree measured, less this file; `code` is the digest of its bin/ and share/",
    "alone, and --changed warns when the tree it runs on has other ones.  `files` is every file under bin/ and share/ in",
    "the tree measured.  `fixture` is every file the per-process fixture's `spud init` ran a function of or opened, which",
    "every SpudTestCase test starts from.  `modules` gives each test module the other files it ran a function of or opened.",
    "What a changed file under bin/spudlib/ or share/ runs (suite.select): its reach is the file and every module of",
    "bin/spudlib that reads its data -- names an attribute of it other than one of its top-level functions: a table, a",
    "constant, a class -- directly or through another such reader, read from the source of the tree the run covers; an",
    "import alone, or a call of a function, is no edge, since a function that runs is measured where it runs.  The full",
    "suite runs when the file is not in `files` (a new file), or when its reach meets `fixture`.  Otherwise every test",
    "module whose entry meets the reach runs, and every test module the table does not name.",
]


def module_names(snap):
    return sorted(p.name[:-3] for p in (snap / "tests").glob("test_*.py"))


def measure(snap, env, trace, module):
    """Run one test module traced; (module, its records, the run's last line of output)."""
    directory = trace / module
    run_env = dict(env, SPUD_SUITE_TRACE=str(directory))
    proc = subprocess.run([sys.executable, "-I", "-S", "-m", "unittest", "discover", "-s", "tests", "-t", "tests", "-p", module + ".py"],
                          cwd=snap, env=run_env, capture_output=True, text=True)
    records = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        with open(path, encoding="utf-8") as f:
            records.append(json.load(f))
    last = (proc.stderr.strip().splitlines() or ["(no output)"])[-1]
    return module, records, last, proc.returncode


def table_of(files, results, previous=None):
    """The table's JSON from the measured records, over the tree `files`."""
    universe = sorted(os.fsdecode(rel) for rel, _, _, _ in files if os.fsdecode(rel).startswith(ROOTS))
    known = set(universe)
    touched = lambda r: {p for p in r["ran"] + r["opened"] if p in known}
    fixture = set(previous["fixture"]) if previous else set()
    modules = dict(previous["modules"]) if previous else {}
    failed = dict(previous.get("failed", {})) if previous else {}
    for module, records, last, code in results:
        fixture.update(p for r in records if r["role"] == "fixture" for p in touched(r))
    for module, records, last, code in results:
        mine = set()
        for r in records:
            if r["role"] != "fixture":
                mine.update(touched(r))
        modules[module] = sorted(mine - fixture)
        failed.pop(module, None)
        if code != 0:
            failed[module] = last
    present = {os.fsdecode(rel)[len("tests/"):-3] for rel, _, _, _ in files
               if os.fsdecode(rel).startswith("tests/test_") and os.fsdecode(rel).endswith(".py")}
    modules = {m: [p for p in paths if p not in fixture] for m, paths in sorted(modules.items()) if m in present}
    # The digest of the tree measured, less the table itself, so that measuring an unchanged tree again names it alike.
    measured = suite.digest([f for f in files if f[0] != TABLE.encode()])
    out = {"about": ABOUT, "tree": measured, "code": suite.code_digest(files), "files": universe, "fixture": sorted(fixture), "modules": modules}
    failed = {m: v for m, v in sorted(failed.items()) if m in present}
    if failed:
        out["failed"] = failed  # modules whose traced run did not pass: their entries may be short of what they reach
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tests/suite_deps.py", description="Measure tests/suite_deps.json (SPD-233).")
    parser.add_argument("modules", nargs="*", metavar="MODULE", help="measure these test modules only, keeping the rest of the table")
    parser.add_argument("-j", "--workers", type=int, default=suite.DEFAULT_WORKERS)
    args = parser.parse_args(argv)
    lock = suite.acquire(suite.lock_path())
    started = time.perf_counter()
    scratch = Path(tempfile.mkdtemp(prefix=suite.SCRATCH_PREFIX)).resolve()
    try:
        (scratch / suite.OWNER).write_text("%d\n" % os.getpid(), encoding="utf-8")
        tmp = scratch / "tmp"
        tmp.mkdir()
        files = suite.tree(CHECKOUT)
        snap = scratch / "tree"
        suite.snapshot(files, snap, scratch / "git")
        env = dict(suite.ENV, TMPDIR=str(tmp))
        env.pop(suite.LOCK_ENV, None)
        env[suite.PYCACHE_ENV] = str(scratch / "pycache")
        compiled = subprocess.run([sys.executable, "-I", "-S", "-c",
                                   "import sys; sys.dont_write_bytecode = True; sys.path.insert(0, %r); import helpers; helpers.compile_program(%r)"
                                   % (str(snap / "tests"), str(scratch / "pycache"))], env=env, capture_output=True, text=True)
        if compiled.returncode != 0:
            raise SystemExit("suite_deps: compiling the warm cache failed: %s" % compiled.stderr.strip())
        names = args.modules or module_names(snap)
        unknown = sorted(set(names) - set(module_names(snap)))
        if unknown:
            raise SystemExit("suite_deps: no such test module: %s" % " ".join(unknown))
        trace = scratch / "trace"
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            results = list(pool.map(lambda m: measure(snap, env, trace, m), names))
        previous = None
        path = CHECKOUT / TABLE
        if args.modules and path.is_file():
            previous = json.loads(path.read_text(encoding="utf-8"))
        table = table_of(files, results, previous)
        path.write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
        for module, records, last, code in results:
            if code != 0:
                sys.stderr.write("suite_deps: %s did not pass traced (%s); its entry may be short\n" % (module, last))
        sys.stdout.write("suite_deps: %d module%s measured in %.1f s; %d files, %d in the fixture; wrote %s (tree %s)\n"
                         % (len(results), "" if len(results) == 1 else "s", time.perf_counter() - started, len(table["files"]),
                            len(table["fixture"]), TABLE, table["tree"]))
        return 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        suite.release(lock)


if __name__ == "__main__":
    sys.exit(main())
