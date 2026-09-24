"""tests/suite.py: the whole suite on every core, over a snapshot of the tree it names (SPD-102).

  python3.14 -I -S tests/suite.py [-j N] [--chunk N] [--cold] [--no-wait] [--background] [NAME ...]
                                                   the suite, or the modules, classes or tests named
  python3.14 -I -S tests/suite.py --changed [BASE] [--dry-run] [-j N] ...
                                                   the modules tests/suite_map.json names for what changed against BASE
  python3.14 -I -S tests/suite.py --digest         the digest of the checkout's tree alone; runs nothing, waits for nothing

Standard library only, and unittest discovery never collects this file (it is not test*.py).  The serial command,
`python3.14 -I -S -m unittest discover -s tests -t tests`, still runs the same tests the same way.  A run goes:

0. One run per machine (SPD-232).  Two runs side by side each take every core, double each other's time and fail the
   wall_clock tests on contention alone, so a run first takes an exclusive flock on `spud-suite.lock` in the user's temp
   directory (tempfile.gettempdir(); SPUD_SUITE_LOCK names another file) and writes into it its pid, checkout and start
   time.  A second run, named or full, waits, says on stderr whose run it waits for, and starts when that one ends; with
   --no-wait it exits 75 at once with one line instead.  The kernel releases the lock when the run's last process dies:
   the workers inherit the descriptor and never lock anything themselves, so a run killed with SIGKILL keeps the lock
   until its orphaned workers have gone too.  Holding it, the run removes every `spud-suite-*` directory in the temp
   directory whose owner (the pid in its `owner` file) is gone, or which has no owner file and is over a day old (a run
   from before SPD-232); nothing else there is the runner's.  --background then drops the run to macOS's background
   priority (os.PRIO_DARWIN_BG), which every worker and every process a test starts inherits: quieter and slower.
   SIGTERM and SIGHUP end a run as Ctrl-C does: the workers' process groups are killed and waited for, and the scratch
   directory is removed.
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
4. Workers.  -j interpreters (this file with --worker, inside the snapshot, each the leader of a process group of its
   own) each take the largest chunk left when idle and report every test as it ends.  Their temp directory, and this
   process's from the snapshot on, is the scratch directory's `tmp/` (TMPDIR and tempfile.tempdir), so every home and
   directory a test or a program it runs makes through the temp directory goes with the run, however it ends, and a
   nested run inside a test locks and sweeps inside it, never beside this one (SPUD_SUITE_LOCK is not passed on).  The
   tests marked tests/helpers.wall_clock, which assert an upper bound on wall time, are held back until every other test
   is done and then run beside nothing but each other; under --background, at normal priority again, in fresh workers.  A worker that dies fails the
   test it was running, its chunk's untouched tests go back on the queue, and a new worker takes its place.
5. The report, as unittest prints it: a character per test while it runs, then the errors and failures with their
   tracebacks (the snapshot's paths spelled as the checkout's), `Ran N tests in S`, OK or FAILED, and one final line on
   stdout: the result, the count, the wall time, the workers and the tree's digest.  The exit status is 1 on any
   failure, error, crashed worker or test never reported, 75 when --no-wait found another run, 128 plus the signal's
   number when SIGINT, SIGTERM or SIGHUP ended it, else 0.  Nothing is written into the checkout; bytecode is never
   written at all, and the scratch directory is removed by this process alone, or by the next run if this one was killed.

--changed [BASE] (SPD-234; BASE defaults to main) runs, in place of the whole suite, the test modules the path map
tests/suite_map.json names for the files changed against the merge base of BASE and HEAD: committed on the branch,
staged, unstaged, deleted (both sides of a rename), and untracked but not ignored.  The map is data, read from the tree
the run covers; its `about` says how a path is matched.  A path the map sends to `full`, or that no rule names, runs the
full suite.  Under bin/spudlib/ and share/ the map's rule is the dependency table, tests/suite_deps.json (SPD-233), which
tests/suite_deps.py measures by running each test module traced: a file the fixture's `spud init` runs or reads, or whose
data a module of it reads, runs the full suite (`reached by the fixture's init`), and any other runs the test modules
that ran or read it.  The table records the digest of the bin/ and share/ it was measured on (`code`); when the tree's
differ, the run warns that the selection may be short and names the command that measures the table again.  Before
running, the run says on stderr each path and the rule that took it.  The final line then
says `affected`, the rules and the base, `(affected: deps+tests against main, <n> modules)`, or, when it fell back,
`(full, --changed against main: bin/spud by rule full)`; either way it ends with the digest, so a landing never takes a
selection for a full run.  Nothing changed runs nothing, and says so.  --dry-run prints the paths, their rules and the
modules on stdout and runs nothing, taking no lock.
"""

import argparse
import ast
import collections
import fcntl
import fnmatch
import hashlib
import io
import json
import os
import selectors
import shutil
import signal
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
LOCK_ENV = "SPUD_SUITE_LOCK"  # the lock file, in place of spud-suite.lock in the temp directory; never passed to a worker
LOCK_NAME = "spud-suite.lock"
SCRATCH_PREFIX = "spud-suite-"  # a run's scratch directory in the temp directory: the only name there the runner owns
OWNER = "owner"  # the file in a run's scratch directory naming the pid of the run that made it
LEGACY_AGE = 24 * 3600  # a scratch directory with no owner file (a run from before SPD-232) is swept past this age
EXIT_BUSY = 75  # EX_TEMPFAIL: --no-wait, and another run holds the lock
POLL = 0.5  # seconds between two looks at a held lock
DEFAULT_WORKERS = os.cpu_count() or 4  # 18 on this Mac, where 6 and 12 were slower and 24 and 32 no faster (SPD-102)
DEFAULT_CHUNK = 4
KINDS = ("errors", "failures", "skipped", "expectedFailures", "unexpectedSuccesses")
MARKS = {"errors": "E", "failures": "F", "skipped": "s", "expectedFailures": "x", "unexpectedSuccesses": "u"}
MAP = "tests/suite_map.json"  # the path map --changed selects by, read from the tree a run covers
DEFAULT_BASE = "main"


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


# -- what changed, and the modules it reaches (SPD-234) ---------------------------------------------------------------


# paths: every changed path; why: [(path, the rule that took it, "full", or None when no rule names it)]; full: whether
# the full suite runs; rules: the rules that chose modules, in the order the paths met them; modules: the test modules;
# stale: when a rule read the dependency table and the table was measured on another bin/ and share/, the warning to say.
Selection = collections.namedtuple("Selection", "paths why full rules modules stale", defaults=(None,))
TABLE_ROOTS = ("bin/", "share/")  # what the dependency table measures, and the part of the tree its `code` digest covers


def changed(root, base):
    """(merge base, [path]): every file changed in `root` against the merge base of `base` and HEAD -- committed on the
    branch, staged, unstaged, deleted, both sides of a rename -- and every untracked file git does not ignore, sorted."""
    merge = git(root, "merge-base", base, "HEAD").decode().strip()
    listed = git(root, "diff", "--name-only", "--no-renames", "-z", merge, "--") + git(root, "ls-files", "-z", "-o", "--exclude-standard")
    return merge, sorted({os.fsdecode(p) for p in listed.split(b"\0") if p and not p.endswith(b"/")})  # `dir/`: a nested repository


def load_map(files):
    for rel, _, _, content in files:
        if rel == MAP.encode():
            return json.loads(content)
    raise SystemExit("suite: --changed: the tree has no %s" % MAP)


def imports(source):
    """The top-level names a module's source imports, anywhere in it; nothing for a source that does not parse."""
    try:
        parsed = ast.parse(source)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def load_table(files, rel):
    """The dependency table a rule names (tests/suite_deps.json, SPD-233), read from the tree the run covers."""
    for path, _, _, content in files:
        if path == rel.encode():
            return json.loads(content)
    raise SystemExit("suite: --changed: the tree has no %s, which %s names" % (rel, MAP))


def code_digest(files):
    """The digest (as `digest` makes it) of the files under bin/ and share/ alone: what the dependency table records as
    `code` when it is measured, so that --changed can tell a table measured on another program from one measured on this."""
    return digest([f for f in files if os.fsdecode(f[0]).startswith(TABLE_ROOTS)])


def stale_table(deps, files, rel):
    """None when the table `rel` was measured on the bin/ and share/ of the tree `files`, else the warning --changed says.
    A change since may reach a module the table does not say it reaches, so a selection can be short; the full suite at
    landing is never a selection."""
    now = code_digest(files)
    if deps.get("code") == now:
        return None
    measured = "on bin/ and share/ at %s" % deps["code"] if deps.get("code") else "before it recorded what it was measured on"
    return ("suite: warning: %s was measured %s, and this tree's bin/ and share/ are %s: the selection may miss a module a "
            "change since reaches; measure it again with `python3.14 -I -S tests/suite_deps.py`" % (rel, measured, now))


PACKAGE = "bin/spudlib/"


def data_readers(files):
    """{module path: the modules of bin/spudlib that read its data} over the tree `files` (SPD-233).

    A module reads another's data when it names, anywhere in its source, an attribute of that module other than one of
    its top-level functions: a table, a constant, a class.  A function only called is measured where it runs (the
    dependency table records whose functions a test ran), and a reference to one changes nothing until it is called;
    what the table cannot see is a value one module holds and another reads, and this is that edge, read from the source
    so that it is the tree's own however the imports moved since the table was measured."""
    trees, functions = {}, {}
    for rel, kind, _, content in files:
        path = os.fsdecode(rel)
        if path.startswith(PACKAGE) and path.endswith(".py") and kind != "l":
            try:
                trees[path] = ast.parse(content)
            except SyntaxError:
                continue
            functions[path] = {n.name for n in trees[path].body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    readers = collections.defaultdict(set)
    for path, parsed in trees.items():
        package = path[:-3].split("/")[:-1]  # bin/spudlib/shell/walk.py: [bin, spudlib, shell]
        bound = {}
        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom) and node.level:
                base = package[:len(package) - (node.level - 1)] + (node.module.split(".") if node.module else [])
                for alias in node.names:
                    target = "/".join(base + [alias.name]) + ".py"
                    if target in trees:
                        bound[alias.asname or alias.name] = target
        for node in ast.walk(parsed):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in bound:
                target = bound[node.value.id]
                if node.attr not in functions[target]:
                    readers[target].add(path)
    return readers


def reach(path, readers):
    """The changed file and every module that reads its data, directly or through another's (data_readers)."""
    reached, todo = {path}, [path]
    while todo:
        for reader in readers.get(todo.pop(), ()):
            if reader not in reached:
                reached.add(reader)
                todo.append(reader)
    return reached


def select(paths, files, table=None, readers=None):
    """The Selection the map makes for `paths` over the tree `files`: each path goes to `full` if a full pattern matches
    it, else to the first rule one of whose patterns does, else nowhere, which is the full suite too.  A rule's module
    names and patterns expand against the tree's test modules; with `importers`, the changed file's own module joins
    them, and every test module that imports it, directly or through another module of tests/.  A rule with a `table`
    (SPD-233) chooses from the dependency table instead: a path the table never saw runs the full suite, and so does one
    whose reach (the file and the modules that read its data) meets what the fixture's init ran or read; else every test
    module whose entry meets that reach runs, and every test module the table does not name."""
    table = load_map(files) if table is None else table
    matches = lambda path, patterns: any(fnmatch.fnmatchcase(path, p) for p in patterns)
    sources = {}
    for rel, kind, _, content in files:
        folder, _, name = os.fsdecode(rel).rpartition("/")
        if folder == "tests" and name.endswith(".py") and kind != "l":
            sources[name[:-3]] = content
    tests = sorted(n for n in sources if fnmatch.fnmatchcase(n + ".py", "test*.py"))
    graph = deps = stale = None  # read on first use, as `readers` is when the caller has not read it already
    why, rules, modules, full = [], [], set(), False
    for path in paths:
        rule = None if matches(path, table["full"]) else next((r for r in table["rules"] if matches(path, r["paths"])), None)
        if rule is None:
            full = True
            why.append((path, "full" if matches(path, table["full"]) else None))
            continue
        if "table" in rule:
            if deps is None:
                deps = load_table(files, rule["table"])
                stale = stale_table(deps, files, rule["table"])
            readers = readers if readers is not None else data_readers(files)
            reached = reach(path, readers)
            if path not in deps["files"] or reached & set(deps["fixture"]):
                full = True
                why.append((path, None if path not in deps["files"] else "fixture"))
                continue
            why.append((path, rule["name"]))
            if rule["name"] not in rules:
                rules.append(rule["name"])
            modules.update(t for t in tests if t not in deps["modules"] or reached.intersection(deps["modules"][t]))
            continue
        why.append((path, rule["name"]))
        if rule["name"] not in rules:
            rules.append(rule["name"])
        for pattern in rule["modules"]:
            modules.update(t for t in tests if fnmatch.fnmatchcase(t, pattern))
        if rule.get("importers"):
            graph = graph if graph is not None else {n: imports(s) for n, s in sources.items()}
            folder, _, name = path.rpartition("/")
            own = name[:-3] if folder == "tests" and name.endswith(".py") else None
            reached, todo = ({own}, [own]) if own else (set(), [])
            while todo:
                done = todo.pop()
                for n, names in graph.items():
                    if done in names and n not in reached:
                        reached.add(n)
                        todo.append(n)
            modules.update(reached.intersection(tests))
    return Selection(list(paths), why, full, rules, sorted(modules), stale)


# The three answers that send a path to the full suite, as the final line and --dry-run spell them.
FORCED = {"full": "by rule full", "fixture": "reached by the fixture's init", None: "unmapped"}


def scope(selection, base):
    """What the final line of a --changed run says after its count."""
    if not selection.paths:
        return " (affected: nothing changed against %s)" % base
    if selection.full:
        forced = [(p, r) for p, r in selection.why if r in FORCED]
        path, rule = forced[0]
        more = ", and %d more" % (len(forced) - 1) if len(forced) > 1 else ""
        return " (full, --changed against %s: %s %s%s)" % (base, path, FORCED[rule], more)
    n = len(selection.modules)
    return " (affected: %s against %s, %d module%s)" % ("+".join(selection.rules), base, n, "" if n == 1 else "s")


def explain(selection, base, merge):
    """The lines that say, before a --changed run, what changed and which rule took each path."""
    lines = ["suite: --changed against %s (merge base %s): %d changed file%s" % (base, merge[:12], len(selection.paths), "" if len(selection.paths) == 1 else "s")]
    lines += ["  %s: %s" % (p, "unmapped" if r is None else "full: the fixture's init reaches it" if r == "fixture" else r)
              for p, r in selection.why]
    if selection.full:
        lines.append("suite: the full suite")
    else:
        lines.append("suite: %d module%s: %s" % (len(selection.modules), "" if len(selection.modules) == 1 else "s", " ".join(selection.modules) or "none"))
    if selection.stale:
        lines.append(selection.stale)
    return lines


# -- one run per machine (SPD-232) ------------------------------------------------------------------------------------


def lock_path():
    """The machine's suite lock: $SPUD_SUITE_LOCK, else spud-suite.lock in the temp directory."""
    return os.environ.get(LOCK_ENV) or os.path.join(tempfile.gettempdir(), LOCK_NAME)


def read_holder(path):
    """{pid, checkout, started} as the run holding the lock at `path` wrote them, or None while unwritten or unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            holder = json.loads(f.read() or "null")
        return holder if isinstance(holder, dict) and "pid" in holder else None
    except (OSError, ValueError):
        return None


def alive(pid):
    """Whether a process `pid` exists; one of another user's counts."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def describe(holder):
    if holder is None:
        return "another run (it has not said whose yet)"
    text = "the run of pid %s in %s, started %s" % (holder.get("pid"), holder.get("checkout"), holder.get("started"))
    if isinstance(holder.get("pid"), int) and not alive(holder["pid"]):
        text += " (it has exited, and its workers are still finishing)"
    return text


def acquire(path, wait=True, say=None, poll=POLL):
    """Take the lock at `path` for this run: the open file holding it (the kernel drops the lock when the last process
    holding the descriptor dies), or None when `wait` is false and another run holds it.  While waiting, says whose run
    it waits for, again whenever that changes.  Records this run's pid, checkout and start time in the file."""
    say = say or (lambda text: (sys.stderr.write(text + "\n"), sys.stderr.flush()))
    f = open(path, "a+", encoding="utf-8")
    announced, looks = False, 0
    try:
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                holder = read_holder(path)
                if not wait:
                    say("suite: not waiting (--no-wait): %s holds %s" % (describe(holder), path))
                    f.close()
                    return None
                looks += 1
                if (holder is not None or looks > 2) and holder != announced:
                    say("suite: waiting for %s" % describe(holder))
                    announced = holder
                time.sleep(poll)
        f.seek(0)
        f.truncate()
        f.write(json.dumps({"pid": os.getpid(), "checkout": str(CHECKOUT), "started": time.strftime("%Y-%m-%d %H:%M:%S %z")}) + "\n")
        f.flush()
        return f
    except BaseException:
        f.close()
        raise


def release(f):
    """Give the lock up, leaving the file empty: nobody holds it."""
    try:
        f.seek(0)
        f.truncate()
        f.flush()
    finally:
        f.close()


def sweep(directory, now=None):
    """Remove every `spud-suite-*` directory under `directory` whose run is gone, while this run holds the lock: its
    owner file names a pid that no longer exists, or it has none and is older than LEGACY_AGE.  Returns their names.  A
    reused pid keeps a dead run's directory until the next sweep; with the lock held, no other run is using one."""
    now = time.time() if now is None else now
    removed = []
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError:
        return removed
    for entry in entries:
        if not entry.name.startswith(SCRATCH_PREFIX) or not entry.is_dir(follow_symlinks=False):
            continue
        try:
            with open(os.path.join(entry.path, OWNER), encoding="utf-8") as f:
                owner = int(f.read().strip())
        except (OSError, ValueError):
            owner = None
        if owner is None:
            try:
                if now - entry.stat(follow_symlinks=False).st_mtime < LEGACY_AGE:
                    continue
            except OSError:
                continue
        elif alive(owner):
            continue
        shutil.rmtree(entry.path, ignore_errors=True)
        removed.append(entry.name)
    return removed


def background():
    """Drop this process, and so every process it starts from now on, to macOS's background priority.  None when done,
    else why not."""
    if not (hasattr(os, "PRIO_DARWIN_BG") and hasattr(os, "PRIO_DARWIN_PROCESS")):
        return "this platform has no os.PRIO_DARWIN_BG"
    try:
        os.setpriority(os.PRIO_DARWIN_PROCESS, 0, os.PRIO_DARWIN_BG)
    except OSError as e:
        return str(e)
    return None


def foreground():
    """Back to normal priority, for the processes this one starts from now on (the wall_clock tests' workers)."""
    os.setpriority(os.PRIO_DARWIN_PROCESS, 0, 0)


class Interrupted(KeyboardInterrupt):
    """SIGINT, SIGTERM or SIGHUP: each ends a run as Ctrl-C always has."""

    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


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
    def __init__(self, snap, env, keep=()):
        # A process group of its own, which the parent kills whole on an interrupt, tests' programs included; `keep` is the
        # lock's descriptor, held and never locked, so the lock outlives a killed parent until its last worker is gone.
        self.proc = subprocess.Popen([sys.executable, "-I", "-S", str(snap / "tests" / "suite.py"), "--worker"], cwd=snap, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, process_group=0, pass_fds=keep)
        self.buffer = b""
        self.unit = None  # the chunk it is running: [ids]
        self.running = None  # [id, name, doc] of the test it started last and has not finished
        self.finished = set()

    def assign(self, unit):
        self.unit, self.running, self.finished = unit, None, set()
        self.proc.stdin.write((json.dumps(unit) + "\n").encode())
        self.proc.stdin.flush()


def kill(w):
    """Kill a worker's process group, the programs its test started with it, and reap the worker."""
    try:
        os.killpg(w.proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    w.proc.wait()


class Run:
    def __init__(self, order, snap, env, workers, total, keep=(), on_last=None):
        self.order, self.snap, self.env = order, snap, env
        self.count = workers
        self.total = total
        self.keep = keep  # descriptors every worker inherits: the lock's
        self.on_last = on_last  # called before the wall_clock tests' fresh workers start (--background: normal priority)
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
            w = Worker(self.snap, self.env, self.keep)
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
                    if self.on_last is not None:  # the idle workers go, and the wall_clock tests get workers started after it
                        self.on_last()
                        for w in workers:
                            sel.unregister(w.proc.stdout)
                            w.proc.stdin.close()
                            w.proc.wait()
                        workers.clear()
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
        except BaseException:  # an interrupt, a signal, or too many dead workers: nothing waits for a chunk to end
            for w in workers:
                kill(w)
            raise
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
    """A module, class or test name as unittest spells it; tests/test_hooks_session.py is test_hooks_session."""
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


def report(run, wall, scope_text, tree_digest, workers):
    """unittest's report on stderr, then the final line on stdout; `scope_text` follows the count: empty for the full
    suite, ` (partial: NAME ...)` for a named run, and what scope() says for --changed."""
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
    sys.stdout.write("%s: %d tests%s in %.1f s on %d worker%s; tree %s\n" % (status, run.run, scope_text, wall, workers, "" if workers == 1 else "s", tree_digest))
    sys.stdout.flush()
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tests/suite.py", description="The suite in parallel, over a snapshot of the checkout (SPD-102).")
    parser.add_argument("names", nargs="*", metavar="NAME", help="modules, classes or tests to run (test_hooks_session, tests/test_hooks_session.py, test_hooks_session.StopTest); default all")
    parser.add_argument("-j", "--workers", type=int, default=DEFAULT_WORKERS, help="worker interpreters (default %d)" % DEFAULT_WORKERS)
    parser.add_argument("--chunk", type=int, default=DEFAULT_CHUNK, help="most tests of one class a worker takes at once (default %d)" % DEFAULT_CHUNK)
    parser.add_argument("--cold", action="store_true", help="no warm bytecode cache: every scratch home compiles the program itself")
    parser.add_argument("--digest", action="store_true", help="print the digest of the checkout's tree and exit, running nothing")
    parser.add_argument("--no-wait", action="store_true", help="exit %d at once if another run holds the machine's suite lock, rather than wait for it" % EXIT_BUSY)
    parser.add_argument("--background", action="store_true", help="run at macOS's background priority, workers included: quieter and slower (the wall_clock tests run at normal priority)")
    parser.add_argument("--changed", nargs="?", const=DEFAULT_BASE, metavar="BASE",
                        help="run only the test modules %s names for the files changed against the merge base of BASE (default %s) and HEAD; the full suite for a path it sends there or does not name" % (MAP, DEFAULT_BASE))
    parser.add_argument("--dry-run", action="store_true", help="with --changed: print the changed paths, their rules and the modules, and run nothing")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        return worker()
    if args.digest:
        sys.stdout.write(digest(tree(CHECKOUT)) + "\n")
        return 0
    if args.workers < 1 or args.chunk < 1:
        parser.error("-j and --chunk take a positive number")
    if args.changed is not None and args.names:
        parser.error("--changed chooses the modules itself, and takes no NAME")
    if args.dry_run:
        if args.changed is None:
            parser.error("--dry-run goes with --changed")
        files = tree(CHECKOUT)
        merge, paths = changed(CHECKOUT, args.changed)
        sys.stdout.write("\n".join(explain(select(paths, files), args.changed, merge)) + "\n")
        return 0
    caught = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    before = {s: signal.getsignal(s) for s in caught}

    def interrupted(signum, frame):  # the first of them ends the run; the cleanup that follows runs to its end
        for s in caught:
            signal.signal(s, signal.SIG_IGN)
        raise Interrupted(signum)

    for s in caught:
        signal.signal(s, interrupted)
    lock = None
    try:
        lock = acquire(lock_path(), wait=not args.no_wait)
        if lock is None:
            return EXIT_BUSY
        return run_locked(args, lock)
    except KeyboardInterrupt as e:
        signum = getattr(e, "signum", signal.SIGINT)
        sys.stderr.write("\nsuite: interrupted%s\n" % ("" if signum == signal.SIGINT else " (%s)" % signal.Signals(signum).name))
        return 128 + signum
    finally:
        if lock is not None:
            release(lock)
        for s, handler in before.items():
            signal.signal(s, handler)


def run_locked(args, lock):
    """The run itself, holding the lock: sweep, snapshot, discover, run, report; the scratch directory removed after."""
    if args.background:
        why = background()
        if why is not None:
            sys.stderr.write("suite: --background: %s; running at normal priority\n" % why)
            args.background = False
    removed = sweep(tempfile.gettempdir())
    if removed:
        sys.stderr.write("suite: removed %d scratch director%s of runs that are gone: %s\n" % (len(removed), "y" if len(removed) == 1 else "ies", " ".join(removed)))
    started = time.perf_counter()
    scratch = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX)).resolve()
    try:
        (scratch / OWNER).write_text("%d\n" % os.getpid(), encoding="utf-8")
        tmp = scratch / "tmp"
        tmp.mkdir()
        tempfile.tempdir = str(tmp)  # discovery imports tests/helpers here, which makes its guard launchctl in it
        os.environ["TMPDIR"] = str(tmp)
        files = tree(CHECKOUT)
        tree_digest = digest(files)
        names = [name_of(n) for n in args.names]
        scope_text = " (partial: %s)" % " ".join(args.names) if args.names else ""
        if args.changed is not None:
            merge, paths = changed(CHECKOUT, args.changed)
            selection = select(paths, files)
            sys.stderr.write("\n".join(explain(selection, args.changed, merge)) + "\n")
            scope_text = scope(selection, args.changed)
            if not selection.full:
                names = selection.modules
                if not names:  # nothing changed, or no module left to run: an empty list must not become discovery's all
                    sys.stdout.write("OK: 0 tests%s in %.1f s on 0 workers; tree %s\n" % (scope_text, time.perf_counter() - started, tree_digest))
                    return 0
        snap = scratch / "tree"
        snapshot(files, snap, scratch / "git")
        env = dict(ENV)
        env["TMPDIR"] = str(tmp)
        env.pop(LOCK_ENV, None)  # a nested run in a test locks inside this run's temp directory, never this run's lock
        env[PYCACHE_ENV] = "off" if args.cold else str(scratch / "pycache")
        os.environ[PYCACHE_ENV] = env[PYCACHE_ENV]  # this process imports tests/helpers too, and leaves the cleanup to itself
        sys.path.insert(0, str(snap / "tests"))
        loader = unittest.TestLoader()
        suite = load(loader, names, str(snap / "tests"))
        tests = list(flatten(suite))
        if not args.cold:
            import helpers  # the snapshot's, which discovery imported

            helpers.compile_program(scratch / "pycache")
        failed_imports = [t for t in tests if isinstance(t, unittest.loader._FailedTest)]
        runnable = [t for t in tests if not isinstance(t, unittest.loader._FailedTest)]
        units, last = chunks([t for t in runnable if not timed(t)], args.chunk), chunks([t for t in runnable if timed(t)], args.chunk)
        run = Run([t.id() for t in tests], snap, env, args.workers, len(tests), keep=(lock.fileno(),),
                  on_last=foreground if args.background else None)
        if failed_imports:  # a module discovery could not import: loadable by no name, so it runs here, and fails as it does serially
            here = argparse.Namespace(unit=[t.id() for t in failed_imports], running=None, finished=set())
            result = Reporter(io.StringIO())
            unittest.TestSuite(failed_imports).run(result)
            result.send(unit=True, entries=result.entries())
            for line in result.pipe.getvalue().splitlines():
                run.handle(here, json.loads(line))
        run.go(units, last)
        sys.stderr.write("\n")
        return 0 if report(run, time.perf_counter() - started, scope_text, tree_digest, min(args.workers, len(units) + len(last)) or 1) else 1
    finally:
        for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):  # a signal now would leave the scratch half removed
            signal.signal(s, signal.SIG_IGN)
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
