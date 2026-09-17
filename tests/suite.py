"""tests/suite.py: the whole suite on every core, over a snapshot of the tree it names (SPD-102).

  python3.14 -I -S tests/suite.py [-j N] [--chunk N] [--cold] [NAME ...]   the suite, or the modules, classes or tests named
  python3.14 -I -S tests/suite.py --digest                                 the digest of the checkout's tree alone; runs nothing

Standard library only, and unittest discovery never collects this file (it is not test*.py).  The serial command,
`python3.14 -I -S -m unittest discover -s tests -t tests`, still runs the same tests the same way.  A run goes:

1. Snapshot.  Every file `git ls-files -c -o --exclude-standard` lists (tracked, and untracked but not ignored) is read
   once, hashed and written into a scratch directory, and the tests run there: a file another member half-writes in the
   checkout during the run reaches no worker, and two runs in one checkout share nothing (SPD-083).  The digest is a
   SHA-256 over those files as read, in path order: each path, its kind (file, executable file, symlink) and a SHA-256 of
   its content, cut to 16 hex digits.  Editing, adding, deleting or chmod-ing a listed file changes it; bytecode, `.spud/`
   and anything else git ignores never counts, and neither does a nested repository or a linked worktree inside the
   checkout.  The snapshot's `.git` is a file naming a private git directory whose commondir is the checkout's, so the
   tests that `git archive` a pinned commit read the same objects, and any index git writes there is scratch.
2. A warm bytecode cache.  The snapshot's bin/ is compiled once into the tree the launcher would write under a home's
   .spud/pycache/, and SPUD_SUITE_PYCACHE names it to the workers: tests/helpers links it into the home of every
   SpudTestCase before `spud init`, except in the classes that assert the cold path (the launcher's own cache, init,
   backups); a Home a test builds itself starts empty.  --cold sets it to `off`, and every home starts empty, as before.
3. Discovery, exactly as the serial command's, in this process.  Tests stay grouped by class; a class larger than --chunk
   is cut into consecutive chunks, each run with its own setUpClass and tearDownClass.
4. Workers.  -j interpreters (this file with --worker, inside the snapshot) each take the largest chunk left when idle and
   report every test as it ends.  The tests marked tests/helpers.wall_clock, which assert an upper bound on wall time, are
   held back until every other test is done and then run beside nothing but each other.  A worker that dies fails the
   test it was running, its chunk's untouched tests go back on the queue, and a new worker takes its place.
5. The report, as unittest prints it: a character per test while it runs, then the errors and failures with their
   tracebacks (the snapshot's paths spelled as the checkout's), `Ran N tests in S`, OK or FAILED, and one final line on
   stdout: the result, the count, the wall time, the workers and the tree's digest.  The exit status is 1 on any
   failure, error, crashed worker or test never reported, else 0.  Nothing is written into the checkout; bytecode is
   never written at all, and the scratch directory is removed by this process alone.
"""

import argparse
import hashlib
import io
import json
import os
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # before a test or the program is imported: nothing a run starts writes bytecode

ENV = dict(os.environ)  # before tests/helpers, imported by discovery, points SPUD_HOME at its guard
CHECKOUT = Path(__file__).resolve().parent.parent
PYCACHE_ENV = "SPUD_SUITE_PYCACHE"  # read by tests/helpers.py
DEFAULT_WORKERS = os.cpu_count() or 4  # 18 on this Mac, where 6 and 12 were slower and 24 and 32 no faster (SPD-102)
DEFAULT_CHUNK = 4
KINDS = ("errors", "failures", "skipped", "expectedFailures", "unexpectedSuccesses")
MARKS = {"errors": "E", "failures": "F", "skipped": "s", "expectedFailures": "x", "unexpectedSuccesses": "u"}


# -- the tree ---------------------------------------------------------------------------------------------------------


def git(root, *args):
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if proc.returncode != 0:
        raise SystemExit("suite: git %s in %s failed: %s" % (" ".join(args), root, proc.stderr.decode("utf-8", "replace").strip()))
    return proc.stdout


def tree(root):
    """[(path, kind, mode, content)] for each file git lists in `root`, tracked or untracked and not ignored, read once:
    kind is `f`, `x` (executable) or `l` (a symlink, whose content is its target); paths are bytes, sorted."""
    files = []
    for rel in sorted(set(p for p in git(root, "ls-files", "-z", "-c", "-o", "--exclude-standard").split(b"\0") if p)):
        path = os.path.join(os.fsencode(root), rel)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            continue  # tracked, and deleted in the working tree
        if stat.S_ISLNK(st.st_mode):
            files.append((rel, "l", 0, os.readlink(path)))
        elif stat.S_ISREG(st.st_mode):
            with open(path, "rb") as f:
                files.append((rel, "x" if st.st_mode & 0o111 else "f", stat.S_IMODE(st.st_mode), f.read()))
        # anything else git lists is a nested repository or worktree (`dir/`): not this tree
    return files


def digest(files):
    h = hashlib.sha256()
    for rel, kind, _, content in files:
        h.update(rel + b"\0" + kind.encode() + b"\0" + hashlib.sha256(content).hexdigest().encode() + b"\n")
    return h.hexdigest()[:16]


def snapshot(files, dest, admin):
    """Write the files under dest, and dest/.git naming `admin`, a git directory of its own over the checkout's objects."""
    for rel, kind, mode, content in files:
        path = os.path.join(os.fsencode(dest), rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if kind == "l":
            os.symlink(content, path)
        else:
            with open(path, "wb") as f:
                f.write(content)
            os.chmod(path, mode)
    common = git(CHECKOUT, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
    head = git(CHECKOUT, "rev-parse", "HEAD").decode().strip()
    admin.mkdir()
    (admin / "commondir").write_text(common + "\n", encoding="utf-8")
    (admin / "HEAD").write_text(head + "\n", encoding="utf-8")
    (dest / ".git").write_text("gitdir: %s\n" % admin, encoding="utf-8")


# -- a worker ---------------------------------------------------------------------------------------------------------


class Reporter(unittest.TextTestResult):
    """A result that sends each test's outcome up the protocol pipe as the test ends."""

    def __init__(self, pipe):
        super().__init__(io.StringIO(), descriptions=True, verbosity=0)
        self.pipe = pipe
        self.sent = dict.fromkeys(KINDS, 0)

    def send(self, **message):
        self.pipe.write(json.dumps(message) + "\n")
        self.pipe.flush()

    def entries(self):
        new = []
        for kind in KINDS:
            items = getattr(self, kind)
            for item in items[self.sent[kind]:]:
                test, text = (item, None) if kind == "unexpectedSuccesses" else item
                owner = getattr(test, "test_case", test)  # a subtest reports under its test
                new.append([kind, owner.id(), str(test), test.shortDescription(), text])
            self.sent[kind] = len(items)
        return new

    def startTest(self, test):
        super().startTest(test)
        self.send(start=[test.id(), str(test), test.shortDescription()])

    def stopTest(self, test):
        super().stopTest(test)
        self.send(done=test.id(), entries=self.entries())


def worker():
    commands = os.fdopen(os.dup(0), "r", encoding="utf-8")
    pipe = os.fdopen(os.dup(1), "w", encoding="utf-8")
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)  # a CLI a test runs without input inherits no command line of this protocol
    os.close(null)
    os.dup2(2, 1)  # whatever a test prints goes to stderr, never into the protocol
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    loader = unittest.TestLoader()
    for line in commands:
        result = Reporter(pipe)  # a fresh result per chunk: its class and module fixtures run again
        loader.loadTestsFromNames(json.loads(line)).run(result)
        result.send(unit=True, entries=result.entries())  # a tearDownClass or tearDownModule error comes after the last test


# -- the parent -------------------------------------------------------------------------------------------------------


class Reported:
    """A test as a worker described it, for unittest's own error printing."""

    def __init__(self, name, doc):
        self.name, self.doc = name, doc

    def __str__(self):
        return self.name

    def shortDescription(self):
        return self.doc


class Worker:
    def __init__(self, snap, env):
        self.proc = subprocess.Popen([sys.executable, "-I", "-S", str(snap / "tests" / "suite.py"), "--worker"], cwd=snap, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.buffer = b""
        self.unit = None  # the chunk it is running: [ids]
        self.running = None  # [id, name, doc] of the test it started last and has not finished
        self.finished = set()

    def assign(self, unit):
        self.unit, self.running, self.finished = unit, None, set()
        self.proc.stdin.write((json.dumps(unit) + "\n").encode())
        self.proc.stdin.flush()


class Run:
    def __init__(self, order, snap, env, workers, total):
        self.order, self.snap, self.env = order, snap, env
        self.count = workers
        self.total = total
        self.entries = []  # [kind, owner id, name, doc, text]
        self.run = 0
        self.unrun = 0
        self.crashes = 0

    def mark(self, entries):
        marks = [MARKS[e[0]] for e in entries if e[0] in ("errors", "failures")] or [MARKS[e[0]] for e in entries] or ["."]
        sys.stderr.write(marks[0])
        sys.stderr.flush()

    def handle(self, w, message):
        if "start" in message:
            w.running = message["start"]
        elif "done" in message:
            w.running = None
            w.finished.add(message["done"])
            self.run += 1
            self.entries += message["entries"]
            self.mark(message["entries"])
        elif "unit" in message:
            self.entries += message["entries"]
            missing = [i for i in w.unit if i not in w.finished]
            if any(e[0] == "errors" for e in message["entries"]):
                self.unrun += len(missing)  # a setUpClass or setUpModule failed: unittest runs none of its tests, and counts none
            else:
                for test_id in missing:
                    self.run += 1
                    self.entries.append(["errors", test_id, test_id, None, "suite: sent to a worker and never reported\n"])
                    self.mark([["errors"]])
            w.unit = None

    def crashed(self, w, queue):
        code = w.proc.wait()
        self.crashes += 1
        if w.unit is None:
            return
        left = [i for i in w.unit if i not in w.finished]
        victim = w.running or ([left[0], left[0], None] if left else None)
        if victim is not None:
            self.run += 1
            self.entries.append(["errors", *victim, "suite: the worker running this test exited with status %s\n" % code])
            self.mark([["errors"]])
        rest = [i for i in left if victim is None or i != victim[0]]
        if rest:
            queue.insert(0, rest)
        w.unit = None

    def go(self, units, last):
        """Run the chunks, the largest first, then the chunks of `last` (the wall_clock tests) once every other test is done."""
        queue = sorted(units, key=len, reverse=True)
        last = list(last)
        sel = selectors.DefaultSelector()
        workers = []

        def spawn():
            w = Worker(self.snap, self.env)
            os.set_blocking(w.proc.stdout.fileno(), False)
            sel.register(w.proc.stdout, selectors.EVENT_READ, w)
            workers.append(w)
            return w

        for _ in range(min(self.count, len(queue) + len(last)) or 1):
            spawn()
        try:
            while queue or last or any(w.unit is not None for w in workers):
                if not queue and last and all(w.unit is None for w in workers):
                    queue, last = last, []
                    while len(workers) < min(self.count, len(queue)):
                        spawn()
                for w in workers:
                    if w.unit is None and queue:
                        w.assign(queue.pop(0))
                for key, _ in sel.select():
                    w = key.data
                    data = os.read(w.proc.stdout.fileno(), 1 << 16)
                    if not data:
                        sel.unregister(w.proc.stdout)
                        workers.remove(w)
                        self.crashed(w, queue)
                        if self.crashes > self.count + 3:
                            raise SystemExit("suite: %d workers died; stopping" % self.crashes)
                        if queue or last:
                            spawn()
                        continue
                    w.buffer += data
                    *lines, w.buffer = w.buffer.split(b"\n")
                    for line in lines:
                        self.handle(w, json.loads(line))
        finally:
            for w in workers:
                w.proc.stdin.close()
            for w in workers:
                try:
                    w.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    w.proc.kill()
                    w.proc.wait()


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def load(loader, names, top):
    """Discovery as the serial command's, or the named tests; a name that fails to load is a failed test, as in discovery."""
    if not names:
        return loader.discover(top, top_level_dir=top)
    suites = []
    for name in names:
        try:
            suites.append(loader.loadTestsFromName(name))
        except Exception as e:
            suites.append(unittest.loader._make_failed_test(name, e, loader.suiteClass, traceback.format_exc())[0])
    return loader.suiteClass(suites)


def timed(test):
    """Whether the test carries tests/helpers.wall_clock."""
    return getattr(getattr(test, getattr(test, "_testMethodName", ""), None), "wall_clock", False)


def chunks(tests, size):
    """The tests' ids grouped by class in discovery order, each class cut into runs of at most `size`."""
    classes = {}
    for t in tests:
        classes.setdefault((type(t).__module__, type(t).__qualname__), []).append(t.id())
    return [ids[i:i + size] for ids in classes.values() for i in range(0, len(ids), size)]


def name_of(arg):
    """A module, class or test name as unittest spells it; tests/test_hooks.py is test_hooks."""
    if arg.endswith(".py"):
        arg = arg[:-3]
    parts = arg.replace(os.sep, ".").split(".")
    return ".".join(parts[1:] if parts[0] == "tests" else parts)


def place(order):
    """{test id, class or module name: its first position in discovery order}, to print what failed in the order the serial
    run meets it; a fixture error names its class or module in parentheses, `setUpClass (test_x.SomeTest)`."""
    index = {}
    for n, test_id in enumerate(order):
        cls = test_id.rsplit(".", 1)[0]
        for key in (test_id, cls, cls.rsplit(".", 1)[0]):
            index.setdefault(key, n)
    return lambda entry: index.get(entry[1], index.get(entry[1].rpartition("(")[2].rstrip(")"), len(index)))


def report(run, wall, names, tree_digest, workers):
    entries = sorted(run.entries, key=place(run.order))
    home = str(run.snap)
    spelled = lambda text: None if text is None else text.replace(home, str(CHECKOUT)).replace(home.replace("/private/", "/", 1), str(CHECKOUT))
    out = unittest.runner._WritelnDecorator(sys.stderr)
    result = unittest.TextTestResult(out, descriptions=True, verbosity=1)
    for kind in KINDS:
        items = [(Reported(e[2], e[3]), spelled(e[4])) for e in entries if e[0] == kind]
        setattr(result, kind, [t for t, _ in items] if kind == "unexpectedSuccesses" else items)
    result.testsRun = run.run
    result.printErrors()
    out.writeln(result.separator2)
    out.writeln("Ran %d test%s in %.3fs" % (run.run, "" if run.run == 1 else "s", wall))
    out.writeln()
    ok = result.wasSuccessful() and run.run + run.unrun == run.total and not run.crashes
    infos = []
    if result.failures:
        infos.append("failures=%d" % len(result.failures))
    if result.errors:
        infos.append("errors=%d" % len(result.errors))
    if run.run + run.unrun != run.total:
        infos.append("reported=%d of %d" % (run.run + run.unrun, run.total))
    for kind, label in (("skipped", "skipped"), ("expectedFailures", "expected failures"), ("unexpectedSuccesses", "unexpected successes")):
        if getattr(result, kind):
            infos.append("%s=%d" % (label, len(getattr(result, kind))))
    status = ("OK" if ok else "FAILED") + (" (%s)" % ", ".join(infos) if infos else "")
    out.writeln(status)
    out.stream.flush()
    scope = " (partial: %s)" % " ".join(names) if names else ""
    sys.stdout.write("%s: %d tests%s in %.1f s on %d worker%s; tree %s\n" % (status, run.run, scope, wall, workers, "" if workers == 1 else "s", tree_digest))
    sys.stdout.flush()
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tests/suite.py", description="The suite in parallel, over a snapshot of the checkout (SPD-102).")
    parser.add_argument("names", nargs="*", metavar="NAME", help="modules, classes or tests to run (test_hooks, tests/test_hooks.py, test_hooks.StopTest); default all")
    parser.add_argument("-j", "--workers", type=int, default=DEFAULT_WORKERS, help="worker interpreters (default %d)" % DEFAULT_WORKERS)
    parser.add_argument("--chunk", type=int, default=DEFAULT_CHUNK, help="most tests of one class a worker takes at once (default %d)" % DEFAULT_CHUNK)
    parser.add_argument("--cold", action="store_true", help="no warm bytecode cache: every scratch home compiles the program itself")
    parser.add_argument("--digest", action="store_true", help="print the digest of the checkout's tree and exit, running nothing")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        return worker()
    if args.digest:
        sys.stdout.write(digest(tree(CHECKOUT)) + "\n")
        return 0
    if args.workers < 1 or args.chunk < 1:
        parser.error("-j and --chunk take a positive number")
    started = time.perf_counter()
    scratch = Path(tempfile.mkdtemp(prefix="spud-suite-")).resolve()
    try:
        files = tree(CHECKOUT)
        tree_digest = digest(files)
        snap = scratch / "tree"
        snapshot(files, snap, scratch / "git")
        env = dict(ENV)
        env[PYCACHE_ENV] = "off" if args.cold else str(scratch / "pycache")
        os.environ[PYCACHE_ENV] = env[PYCACHE_ENV]  # this process imports tests/helpers too, and leaves the cleanup to itself
        sys.path.insert(0, str(snap / "tests"))
        loader = unittest.TestLoader()
        names = [name_of(n) for n in args.names]
        suite = load(loader, names, str(snap / "tests"))
        tests = list(flatten(suite))
        if not args.cold:
            import helpers  # the snapshot's, which discovery imported

            helpers.compile_program(scratch / "pycache")
        failed_imports = [t for t in tests if isinstance(t, unittest.loader._FailedTest)]
        runnable = [t for t in tests if not isinstance(t, unittest.loader._FailedTest)]
        units, last = chunks([t for t in runnable if not timed(t)], args.chunk), chunks([t for t in runnable if timed(t)], args.chunk)
        run = Run([t.id() for t in tests], snap, env, args.workers, len(tests))
        if failed_imports:  # a module discovery could not import: loadable by no name, so it runs here, and fails as it does serially
            here = argparse.Namespace(unit=[t.id() for t in failed_imports], running=None, finished=set())
            result = Reporter(io.StringIO())
            unittest.TestSuite(failed_imports).run(result)
            result.send(unit=True, entries=result.entries())
            for line in result.pipe.getvalue().splitlines():
                run.handle(here, json.loads(line))
        run.go(units, last)
        sys.stderr.write("\n")
        return 0 if report(run, time.perf_counter() - started, args.names, tree_digest, min(args.workers, len(units) + len(last)) or 1) else 1
    except KeyboardInterrupt:
        sys.stderr.write("\nsuite: interrupted\n")
        return 130
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
