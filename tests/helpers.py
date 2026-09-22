"""Shared fixtures for the bin/spud test suite.

Every database, render target and settings file a test touches lives under a
tempfile directory: a Home is a temporary SPUD_HOME with its own copy of the
config the tool ships for a real home, share/spud.config.json, rendered with
the suite's marks (SPW-001; the tool repository keeps no home config of its own
since SPD-097). Nothing here writes into the repository.
"""

import atexit
import fcntl
import importlib.machinery
import importlib.util
import json
import os
import py_compile
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# The suite must leave no bytecode in the repo: bin/spud_ledger.py is loaded as a
# module by the unit tests, and `-I -S` does not read PYTHONDONTWRITEBYTECODE.  (A
# CLI run caches it under its scratch SPUD_HOME's .spud/pycache/, never here.)  The
# first test module unittest imports is compiled before this line runs, so the
# cache directories are also removed when the interpreter exits.
sys.dont_write_bytecode = True

# SPW-011: the refusing launchctl the guard block below and every Home name.  A program, not a path that does not exist,
# for two reasons: it can say what the fixture forgot, and `schedule show`'s `launchctl print` still gets an answer -- a
# non-zero one, which is this scratch home's own truth (no job of this home's is loaded anywhere) rather than a read of
# this Mac's running jobs, which is what /bin/launchctl would have answered.  Nothing here reaches launchd.
GUARD_LAUNCHCTL_REFUSAL = "spud test guard: refusing to run launchctl"
# Distinct from the fake launchctl's 64 and from launchctl's own 3 (bootout), 5 (bootstrap) and 113 (print), so no test
# and no reader can mistake this refusal for something launchd said.
GUARD_LAUNCHCTL_EXIT = 70


def write_guard_launchctl():
    """The refusing launchctl, written under a scratch directory of its own and removed at exit: the program."""
    directory = tempfile.mkdtemp(prefix="spud-test-guard-launchctl-")
    atexit.register(shutil.rmtree, directory, True)
    path = Path(directory) / "launchctl"
    path.write_text(
        '#!/bin/sh\n'
        'echo "%s $*" >&2\n'
        "cat >&2 <<'EOF'\n"
        "$SPUD_LAUNCHCTL names tests/helpers.py's refusing stub, so this fixture reached launchctl without one of its\n"
        "own.  gui/%d, local.spud.backup and local.spud.render are this Mac's user domain and this Mac's two running\n"
        "jobs: SPUD_LAUNCH_AGENTS_DIR moves the plist a test writes, never the job a bootout removes.  Nothing was run.\n"
        "Give the fixture a launchctl: helpers.LaunchdMixin (or helpers.fake_launchctl) points SPUD_LAUNCHCTL at the\n"
        "recording fake, and `spud init --no-schedule` skips step 8 rather than installing the two LaunchAgents.\n"
        "EOF\n"
        "exit %d\n" % (GUARD_LAUNCHCTL_REFUSAL, os.getuid(), GUARD_LAUNCHCTL_EXIT), encoding="utf-8")
    path.chmod(0o755)
    return path


# SPD-097: the real home is off limits.  The test process's own environment names a home that does not exist, so a CLI run
# that inherits os.environ without a Home's env fails on "no ledger at" or "no spud.config.json in" the guard path instead
# of opening the real ledger: the cause SPD-092 could not name was a run with no SPUD_HOME, which resolved the real home
# (through git before SPD-097, through the ~/.config/spud/home pointer since).  Every Home derives its env from os.environ
# and sets its own SPUD_HOME, SPUD_CONFIG_DIR and SPUD_TOOL_DIR.
GUARD_HOME = os.path.join(tempfile.gettempdir(), "spud-test-guard-%d-does-not-exist" % os.getpid())
os.environ["SPUD_HOME"] = GUARD_HOME
os.environ["SPUD_CONFIG_DIR"] = os.path.join(GUARD_HOME, "config")
os.environ["SPUD_TOOL_DIR"] = GUARD_HOME
# SPD-133: the Bash rule reads a command word against the aliases and functions of Claude Code's shell snapshot,
# ~/.claude/shell-snapshots/.  This Mac's snapshots must not decide a test in this process either, so the guard path
# stands in for ~/.claude here as it does for the home; every Home sets its own SPUD_USER_CLAUDE_DIR below.
os.environ["SPUD_USER_CLAUDE_DIR"] = os.path.join(GUARD_HOME, "user-claude")
# SPW-011: `schedule show|install|uninstall`, `init`'s step 8 and `home move` run $SPUD_LAUNCHCTL
# (commands/schedule.launchctl, default /bin/launchctl) against gui/<uid> and the labels local.spud.backup and
# local.spud.render -- this Mac's own user domain and its own two jobs.  SPUD_LAUNCH_AGENTS_DIR moves the plist a test
# writes; nothing moves the job a `bootout` removes, so a fixture that reached launchd would unload the real backup and
# the real render watcher whatever else it had overridden.  The guard program stands in for the real thing here as the
# guard path does for the home: it refuses, names the fixture's omission, and runs nothing.  Every Home sets it below,
# and a test about the LaunchAgents replaces it with the recording fake (`fake_launchctl`, LaunchdMixin).
GUARD_LAUNCHCTL = write_guard_launchctl()
os.environ["SPUD_LAUNCHCTL"] = str(GUARD_LAUNCHCTL)

REPO = Path(__file__).resolve().parent.parent

# SPD-102: tests/suite.py, the parallel runner, names in SPUD_SUITE_PYCACHE the warm bytecode cache of its run (below), or
# `off`.  Set, this process is the runner or one of its workers, in a snapshot of the checkout that the runner alone removes;
# none of them writes bytecode, so none removes any at exit, and concurrent workers never race on a cache directory (SPD-083).
SUITE_PYCACHE = os.environ.get("SPUD_SUITE_PYCACHE")


def _remove_bytecode():
    for cache in (REPO / "bin" / "__pycache__", REPO / "tests" / "__pycache__", *(REPO / "bin" / "spudlib").glob("**/__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)


if SUITE_PYCACHE is None:  # the serial command: its first test module and this one were cached before the flag above was set
    atexit.register(_remove_bytecode)

SPUD = REPO / "bin" / "spud"  # the launcher: what the hooks, the allow rules and every test run
PROGRAM = REPO / "bin" / "spud_ledger.py"  # the program it loads (SPD-016)
# The config every Home starts from: the template the tool ships for a real home, rendered with the suite's own marks
# (SPW-001).  SPD-097 took Spud's home and its spud.config.json out of the tool repository and the suite kept a copy of
# that config, tests/fixtures/spud.config.json; SPW-001 deleted the copy, because two files holding one config drift --
# the rendered text is byte for byte what the copy held.  The marks are the file below, so the probes under tests/probes/
# render the same pair without importing this module.  A test that needs a different config passes one to Home.
CONFIG = REPO / "share" / "spud.config.json"
CONFIG_MARKS = REPO / "tests" / "fixtures" / "config_marks.json"  # {the mark, as a shipped file writes it: the suite's value}
MARKER = "<!-- generated by spud from .spud/ledger.db; edit with the spud CLI -->"

# Exit codes the CLI promises.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_OWNERSHIP = 3
EXIT_LIMIT = 4
EXIT_TRANSITION = 5
EXIT_CONFLICT = 6


# SPW-001: `spud init` writes one report entry of its own into every home it builds (design section 2.2, step 3), so a
# fresh home is not an empty event log: it holds that entry, and a render writes today's day file for it.  A test about
# what the caller wrote reads NOT_INIT_ENTRY; a test about a rendered tree expects init_report_day() beside the corpus'.
INIT_ENTRY_TITLE = "Spud initialized at"
NOT_INIT_ENTRY = "json_extract(data, '$.generated') IS NOT 'init'"


def init_report_day(home=None):
    """`reports/<day>.md` for the entry init wrote: read from the home's own first entry when one is given, so a run that
    crosses midnight still names the day init wrote rather than the day the assertion ran; else today."""
    if home is not None:
        at = home.scalar("SELECT at FROM events WHERE kind = 'report.entry' AND json_extract(data, '$.generated') = 'init' ORDER BY id LIMIT 1")
        if at:
            return "reports/%s.md" % at[:10]
    return "reports/%s.md" % datetime.now().astimezone().date().isoformat()


def config_text():
    """The shipped config template rendered with the suite's marks (SPD/SPUD, Spud, he/him/his): what a scratch home holds."""
    text = CONFIG.read_text(encoding="utf-8")
    with open(CONFIG_MARKS, encoding="utf-8") as f:
        for mark, value in json.load(f).items():
            text = text.replace(mark, value)
    return text


def real_config():
    return json.loads(config_text())


def compile_program(prefix):
    """Compile every source file of bin/ into `prefix` as the launcher caches it under a home's .spud/pycache/ (SPD-102): a
    tree mirroring each file's own path, timestamp-checked, so the launcher reads it only while the source is unchanged."""
    before = sys.pycache_prefix
    sys.pycache_prefix = str(prefix)
    try:
        for source in sorted((REPO / "bin").rglob("*.py")):
            try:
                py_compile.compile(str(source), cfile=importlib.util.cache_from_source(str(source)), doraise=True,
                                   invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
            except py_compile.PyCompileError:
                pass  # the launcher compiles it again and the tests say what is wrong
    finally:
        sys.pycache_prefix = before


_WARM = []  # [(the warm cache, its leaf directories, its files)], built once per process


def warm_pycache():
    """The run's warm cache: the runner's, else one this process compiles into a temporary directory it removes at exit."""
    if not _WARM:
        root = SUITE_PYCACHE
        if root is None:
            root = tempfile.mkdtemp(prefix="spud-test-pycache-")
            atexit.register(shutil.rmtree, root, True)
            compile_program(root)
        leaves, files = [], []
        for directory, subdirs, names in os.walk(root):
            rel = os.path.relpath(directory, root)
            if not subdirs:
                leaves.append(rel)
            files += [os.path.join(rel, n) for n in names]
        _WARM.append((root, leaves, files))
    return _WARM[0]


def seed_pycache(home):
    """Link the warm cache into <home>/.spud/pycache/ (SPD-102), before `spud init` creates .spud/: the launcher caches only
    under a .spud/ that exists when it starts, so without this every home's init and first command compile the program.
    Hard links cost a directory entry each; a pyc the launcher rewrites is replaced by a new file, never written through."""
    root, leaves, files = warm_pycache()
    cache = os.path.join(home, ".spud", "pycache")
    for rel in leaves:
        os.makedirs(os.path.join(cache, rel), exist_ok=True)
    for rel in files:
        try:
            os.link(os.path.join(root, rel), os.path.join(cache, rel))
        except OSError:
            shutil.copyfile(os.path.join(root, rel), os.path.join(cache, rel))


def wall_clock(test):
    """Mark a test that asserts an upper bound on the wall time of work it does itself (SPD-102).  tests/suite.py runs such a
    test only after every other test is done, beside no other work but the other marked tests, so the bound it asserts is
    measured on an idle machine as it is in the serial run; 18 workers on this Mac's six performance and twelve efficiency
    cores once took ZshGlobOperatorTest's 7.7 s past its 10 s bound.  The serial command ignores the mark."""
    test.wall_clock = True
    return test


def load_spud_module():
    """Import the program, bin/spud_ledger.py, for unit tests (bin/spud is its launcher since SPD-016)."""
    loader = importlib.machinery.SourceFileLoader("spud_ledger", str(PROGRAM))
    spec = importlib.util.spec_from_loader("spud_ledger", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Home:
    """A temporary SPUD_HOME with a config file; runs the CLI against it.  `warm`: its .spud/pycache/ starts as the run's warm
    bytecode cache (SPD-102), unless SPUD_SUITE_PYCACHE is `off`."""

    def __init__(self, config=None, name=None, warm=False):
        self._tmp = tempfile.TemporaryDirectory(prefix="spud-test-")
        self.path = Path(self._tmp.name).resolve()
        if name is not None:  # a home whose own directory has this name (SPD-029: a non-ASCII home, spelled NFC)
            self.path = self.path / name
            self.path.mkdir()
        self.config = config if config is not None else real_config()
        with open(self.path / "spud.config.json", "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)
        # SPW-001: this home plays the tool (SPUD_TOOL_DIR below), and the tool ships share/ -- the config, CLAUDE.md and
        # the vault scaffolding `spud init` writes through core/shipped.  A symlink, so every home reads the one copy in
        # the repository and no test can write through it into the checkout (the files init writes are the home's own).
        try:
            os.symlink(REPO / "share", self.path / "share", target_is_directory=True)
        except OSError:
            shutil.copytree(REPO / "share", self.path / "share")
        if warm and SUITE_PYCACHE != "off":
            seed_pycache(self.path)
        self.env = dict(os.environ)
        self.env.pop("SPUD_SUITE_PYCACHE", None)  # the runner's word to this module, not to the CLI
        self.env["SPUD_HOME"] = str(self.path)
        # `member new` records the session its Bash runs in (SPD-018): a suite run from a Claude Code session must
        # not stamp that live session on scratch rows, so a test that wants a session names it.
        self.env.pop("CLAUDE_CODE_SESSION_ID", None)
        # SPD-014: the hooks read the session's launch directory from CLAUDE_PROJECT_DIR, which a suite run from a Claude
        # Code session inherits; a test that wants one sets it.  `project install` writes user-scope files and the home
        # pointer: both go under this scratch home, never into ~/.claude or ~/.config/spud.
        self.env.pop("CLAUDE_PROJECT_DIR", None)
        self.env["SPUD_USER_CLAUDE_DIR"] = str(self.path / ".user-claude")
        self.env["SPUD_CONFIG_DIR"] = str(self.path / ".user-config")
        # SPD-097: doctor and `board --brief` ask whether the render watcher's LaunchAgent is installed, so this Mac's
        # ~/Library/LaunchAgents must not decide a test: every home looks in a directory of its own, which nothing
        # creates unless the test installs an agent.  LaunchdMixin points it at its own scratch and its fake launchctl.
        self.env["SPUD_LAUNCH_AGENTS_DIR"] = str(self.path / "LaunchAgents")
        # SPW-011: and the launchctl a run of this home's reaches is the guard block's refusing program, because the
        # directory above moves the plist and only this moves the job: `schedule install`, `init` without
        # --no-schedule and `home move` boot out local.spud.backup and local.spud.render in gui/<uid>, which are this
        # Mac's.  So a fixture that forgets fails loudly and locally instead of unloading the real render watcher; a
        # test about the LaunchAgents calls setup_launchd() (LaunchdMixin) for the recording fake.
        self.env["SPUD_LAUNCHCTL"] = str(GUARD_LAUNCHCTL)
        # SPD-077: the suite never touches the network.  `off` is the one value that stops every `gh pr view` the
        # reconciler would make, `spud board`'s own run included, so no test can reach GitHub by forgetting something; a
        # test that wants a read points this at a fake gh of its own (GhMixin below).
        self.env["SPUD_GH"] = "off"
        # SPD-156: and the same rule for the vault's downloads, which `spud init` now makes on its own (step 4b).  `off`
        # refuses every one of them, so no test reaches GitHub by forgetting something and every init here writes the
        # settings and the views and reports the plugins as refused -- which is the design's own no-network path.  A
        # test that wants a download points this at a directory of its own (VaultMixin, tests/test_vault.py).
        self.env["SPUD_VAULT_DOWNLOADS"] = "off"
        # SPD-097: the tool, the checkout whose bin/spud the hook lines, allow rules, LaunchAgents and the /spud skill name
        # and where the spudagent source is read, is this scratch home unless a test names another.  So the assertions the
        # suite made before the split keep their `<home>/bin/spud` shape; a test of the split builds a separate tool with
        # RepoMixin.make_tool() and sets SPUD_TOOL_DIR before `spud init` (init records the tool as project spud's root).
        self.env["SPUD_TOOL_DIR"] = str(self.path)
        self.db = self.path / ".spud" / "ledger.db"

    def cleanup(self):
        self._tmp.cleanup()

    def run(self, *args, check=True, actor=None, stdin=None, cwd=None):
        """The CLI with this home's env; `cwd` is the directory it runs in (SPD-098: `member new` binds a code ticket to the
        linked worktree it runs in), else the test process's own."""
        cmd = [sys.executable, "-I", "-S", str(SPUD)]
        if actor is not None:
            cmd += ["--as", actor]
        cmd += [str(a) for a in args]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=self.env, input=stdin, cwd=None if cwd is None else str(cwd))
        if check and proc.returncode != 0:
            raise AssertionError(
                "spud %s exited %d\nstdout: %s\nstderr: %s"
                % (" ".join(str(a) for a in args), proc.returncode, proc.stdout, proc.stderr)
            )
        return proc

    def json(self, *args, actor=None, check=True, stdin=None, cwd=None):
        proc = self.run("--json", *args, actor=actor, check=check, stdin=stdin, cwd=cwd)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise AssertionError("not JSON: %r (stderr %r)" % (proc.stdout, proc.stderr)) from e

    def init(self, project=True):
        """`spud init`, and then project 1 unless `project` is false.

        SPW-001: init creates no project.  Project 1 is the project spud.config.json names, no longer presumed to be the
        tool repository, and registering the first one is `spud init`'s own step (phase 3 of the design) or nobody's,
        since a home may hold none.  Until that step exists the suite seeds the row the dropped `sync_config_rows`
        INSERT left behind, so every home here keeps the shape it had; a test of the empty registry passes False.

        `--no-schedule` is not a convenience: since phase 4 `init` installs the two LaunchAgents, and `launchctl`'s
        domain and the two labels are the *machine's*, not this scratch home's -- SPUD_LAUNCH_AGENTS_DIR moves the plist
        and nothing moves the job, so before SPW-011 an init here booted out this Mac's own `local.spud.backup` and
        `local.spud.render` and then failed to bootstrap a plist from /var/folders.  Every Home now names the refusing
        launchctl of the guard block, so such an init fails on the refusal instead; it skips step 8 all the same, and
        the tests that are about the LaunchAgents install them through the fake launchctl (LaunchdMixin,
        `fake_launchctl`).
        """
        out = self.json("init", "--no-schedule")
        if project:
            self.seed_project_one()
        return out

    def seed_project_one(self):
        """Project 1 as `spud init` inserted it before SPW-001: key `spud`, the identity's name, rooted at this home's
        SPUD_TOOL_DIR, the config's two prefixes, `remote` from that checkout's origin when it has one, and every other
        column the schema's default.  Idempotent, so a second `init()` is still a no-op."""
        tool = os.path.abspath(os.path.expanduser(self.env["SPUD_TOOL_DIR"]))  # core/homeconf.tool_root reads it the same way
        remote = None
        if os.path.lexists(os.path.join(tool, ".git")):
            proc = subprocess.run(["git", "-C", tool, "remote", "get-url", "origin"], capture_output=True, text=True, env=isolated_git_env())
            remote = (proc.stdout.strip() or None) if proc.returncode == 0 else None
        con = self.connect()
        try:
            with con:
                con.execute(
                    "INSERT INTO projects (id, key, name, root_path, remote, ticket_prefix, team_prefix, created_at)"
                    " VALUES (1, 'spud', ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (self.config.get("identity", {}).get("name", "Spud"), tool, remote,
                     self.config.get("tickets", {}).get("prefix", "SPD"), self.config.get("teams", {}).get("prefix", "SPUD"),
                     datetime.now().astimezone().isoformat(timespec="seconds")),
                )
        finally:
            con.close()

    def connect(self):
        con = sqlite3.connect(self.db, timeout=5)
        con.row_factory = sqlite3.Row
        return con

    def scalar(self, sql, *params):
        con = self.connect()
        try:
            row = con.execute(sql, params).fetchone()
            return None if row is None else row[0]
        finally:
            con.close()

    def rows(self, sql, *params):
        con = self.connect()
        try:
            return [dict(r) for r in con.execute(sql, params).fetchall()]
        finally:
            con.close()

    def write_settings(self, data):
        p = self.path / ".claude" / "settings.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        return p

    def write_config(self, config):
        """Rewrite the home's spud.config.json (the CLI reads it on every run)."""
        self.config = config
        with open(self.path / "spud.config.json", "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

    def hook(self, event, payload):
        """Run `spud hook <event>` with the payload (a dict, or raw text) on stdin."""
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        proc = self.run("hook", event, check=False, stdin=stdin)
        return HookResult(proc.returncode, proc.stdout, proc.stderr)

    @property
    def spool(self):
        return self.path / ".spud" / "hook-errors.jsonl"


# SPD-097, SPD-117: the render watcher's three ingredients, each faked so no test needs a watcher process.  A plist where
# this home's SPUD_LAUNCH_AGENTS_DIR looks is `installed`; the watch lock held is `running`; and an event log aged in the
# database is a vault a watcher stopped rendering, without a test waiting two minutes for one.


def install_watcher_plist(home):
    """The render watcher's LaunchAgent installed for this home, with nothing running: what `down` looks like."""
    agents = Path(home.env["SPUD_LAUNCH_AGENTS_DIR"])
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "local.spud.render.plist").write_text("<plist/>\n", encoding="utf-8")


def hold_watch_lock(home):
    """What a live watcher does: hold the home's watch lock.  Returns the fd, which the caller closes (addCleanup)."""
    fd = os.open(str(home.path / ".spud" / "watch.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def aged_event(home, seconds, kind="hook.denied", body="refused a command"):
    """One event written `seconds` ago and never rendered: what a watcher that stopped rendering leaves behind, without a
    test waiting for it.  Events are append-only -- their own trigger refuses an UPDATE -- so a test ages the log by adding
    to it.  The default kind is one no note shows, the case the renders table alone cannot report."""
    at = (datetime.now().astimezone() - timedelta(seconds=seconds)).isoformat(timespec="seconds")
    con = home.connect()
    try:
        con.execute("INSERT INTO events (at, actor, kind, body) VALUES (?, 'hook:PreToolUse', ?, ?)", (at, kind, body))
        con.commit()
    finally:
        con.close()
    return at


GIT_IDENTITY = {"GIT_AUTHOR_NAME": "Spud", "GIT_AUTHOR_EMAIL": "spud@example.invalid", "GIT_COMMITTER_NAME": "Spud", "GIT_COMMITTER_EMAIL": "spud@example.invalid"}


def isolated_git_env(base=None):
    """An environment in which git reads none of this machine's configuration (SPD-014): no global or system config, no
    global excludes file (Claude Code adds `**/.claude/settings.local.json` there), no signing, a fixed identity."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_COUNT="2",
               GIT_CONFIG_KEY_0="core.excludesFile", GIT_CONFIG_VALUE_0="/dev/null",
               GIT_CONFIG_KEY_1="commit.gpgsign", GIT_CONFIG_VALUE_1="false", **GIT_IDENTITY)
    return env


def git(repo, *args, check=True):
    proc = subprocess.run(["git", "-C", str(repo), *[str(a) for a in args]], capture_output=True, text=True, env=isolated_git_env())
    if check and proc.returncode != 0:
        raise AssertionError("git %s in %s exited %d: %s" % (" ".join(str(a) for a in args), repo, proc.returncode, proc.stderr))
    return proc.stdout


class RepoMixin:
    """Scratch git repositories beside a SpudTestCase's home (SPD-014), and the CLI run from a directory in a session."""

    def scratch_dir(self, prefix="spud-repo-"):
        path = Path(tempfile.mkdtemp(prefix=prefix)).resolve()  # /var is /private/var: roots are compared resolved
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def make_repo(self, prefix="other-", branch="main", origin=False, name=None):
        repo = self.scratch_dir(prefix)
        if name is not None:
            # SPW-001: a repository whose directory name the test chooses, for the tests that read that name -- `spud
            # init` derives the default project key from it, and a name mkdtemp chose carries an underscore some of the
            # time, which the key sanitizes to a hyphen and the display name keeps.
            repo = repo / name
            repo.mkdir()
        git(repo, "init", "-q", "-b", branch)
        git(repo, "commit", "-q", "--allow-empty", "-m", "root")
        if origin:
            bare = self.scratch_dir(prefix + "origin-")
            git(bare, "init", "-q", "--bare", "-b", branch)
            git(repo, "remote", "add", "origin", bare)
            git(repo, "push", "-q", "origin", branch)
            git(repo, "remote", "set-head", "origin", branch)
            self.origin = bare
        return repo

    def add_worktree(self, repo, name, inside=False):
        path = repo / ".claude" / "worktrees" / name if inside else repo.parent / ("%s-%s" % (repo.name, name))
        self.addCleanup(shutil.rmtree, path, True)
        git(repo, "worktree", "add", "-q", "-b", ("worktree-" + name) if inside else name, path)
        return path

    def make_tool(self):
        """A scratch main checkout playing the tool repository (SPD-097): this checkout's bin/ and share/ and its
        spudagent source, committed on main, with .claude/settings.local.json ignored as the real repository ignores it."""
        tool = self.make_repo("tool-")
        shutil.copytree(REPO / "bin", tool / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "share", tool / "share")  # SPW-001: what `spud init` writes a home from
        (tool / ".claude" / "agents").mkdir(parents=True)
        shutil.copyfile(REPO / ".claude" / "agents" / "spudagent.md", tool / ".claude" / "agents" / "spudagent.md")
        (tool / ".gitignore").write_text(".claude/settings.local.json\n", encoding="utf-8")
        git(tool, "add", "-A")
        git(tool, "commit", "-q", "-m", "tool")
        return tool

    def cli(self, *args, actor=None, cwd=None, session=None, check=True, stdin=None, env=None):
        """bin/spud against the scratch home from `cwd` (default the home), in `session` (None: outside every session), with
        git isolated from this machine's configuration."""
        e = isolated_git_env(self.home.env)
        e.pop("CLAUDE_CODE_SESSION_ID", None)
        if session is not None:
            e["CLAUDE_CODE_SESSION_ID"] = session
        e.update(env or {})
        cmd = [sys.executable, "-I", "-S", str(SPUD)] + (["--as", actor] if actor else []) + [str(a) for a in args]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=e, cwd=str(cwd or self.home.path), input=stdin)
        if check and proc.returncode != 0:
            raise AssertionError("spud %s (cwd %s) exited %d\nstdout: %s\nstderr: %s" % (" ".join(cmd[4:]), cwd or self.home.path, proc.returncode, proc.stdout, proc.stderr))
        return proc

    def cli_json(self, *args, **kw):
        proc = self.cli("--json", *args, **kw)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise AssertionError("not JSON: %r (stderr %r)" % (proc.stdout, proc.stderr)) from e

    def add_project(self, root, key="badtakes", ticket_prefix="BAD", team_prefix="BADS", landing="pr", *extra, check=True):
        return self.cli("project", "add", root, "--key", key, "--ticket-prefix", ticket_prefix, "--team-prefix", team_prefix, "--landing", landing,
                        *extra, actor="spud", check=check)


class HookResult:
    """What a hook run produced: exit code, stdout (parsed as JSON when it is), stderr."""

    def __init__(self, code, stdout, stderr):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.json = None
        if stdout.strip():
            try:
                self.json = json.loads(stdout)
            except json.JSONDecodeError:
                self.json = None

    @property
    def decision(self):
        """PreToolUse: the permissionDecision, or None when the hook stayed silent."""
        if not self.json:
            return None
        return self.json.get("hookSpecificOutput", {}).get("permissionDecision")

    @property
    def reason(self):
        if not self.json:
            return ""
        return self.json.get("hookSpecificOutput", {}).get("permissionDecisionReason") or self.json.get("reason") or ""

    @property
    def context(self):
        if not self.json:
            return ""
        return self.json.get("hookSpecificOutput", {}).get("additionalContext") or ""

    def __repr__(self):
        return "HookResult(code=%r, stdout=%r, stderr=%r)" % (self.code, self.stdout, self.stderr)


class SpudTestCase(unittest.TestCase):
    """A test case with a fresh initialised Home per test, its bytecode cache warm (SPD-102) unless the class sets warm_cache
    False, as the classes do that assert what the launcher caches or what init or a backup leaves in .spud/.  Its registry
    holds project 1, `spud`, seeded by Home.init (SPW-001) unless the class sets seed_project False."""

    config = None
    home_name = None
    warm_cache = True
    seed_project = True  # SPW-001: project 1 seeded as init recorded it before phase 2; False for an empty registry

    def setUp(self):
        self.home = Home(config=self.config, name=self.home_name, warm=self.warm_cache)
        self.addCleanup(self.home.cleanup)
        self.home.init(project=self.seed_project)

    # Small builders used across files.
    def new_ticket(self, title="A ticket", **kw):
        args = ["ticket", "new", "--title", title]
        for k, v in kw.items():
            if isinstance(v, (list, tuple)):
                for item in v:
                    args += ["--" + k.replace("_", "-"), item]
            else:
                args += ["--" + k.replace("_", "-"), v]
        return self.home.json(*args, actor="spud")["ticket"]

    def new_member(self, ticket, actor="spud", persona="scout", model="haiku", cwd=None, **kw):
        """`member new`, run from `cwd` when given: a code member of a project whose root is a git checkout is planned from a
        linked worktree of it (SPD-098)."""
        kw.setdefault("brief", "Do the thing.")
        args = ["member", "new", "--ticket", ticket, "--persona", persona, "--model", model]
        for k, v in kw.items():
            if isinstance(v, (list, tuple)):
                for item in v:
                    args += ["--" + k.replace("_", "-"), item]
            else:
                args += ["--" + k.replace("_", "-"), v]
        return self.home.json(*args, actor=actor, cwd=cwd)["member"]


def normalize_markdown(text):
    """The acceptance test's allowed differences: marker line, trailing
    whitespace, runs of blank lines (and blank lines at either end)."""
    out = []
    for line in text.splitlines():
        if line.strip() == MARKER:
            continue
        line = line.rstrip()
        if line == "" and out and out[-1] == "":
            continue
        out.append(line)
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


# A ticket note's ## Team section is generated from the members table alone since SPD-010
# (docs/design/2026-09-12-team-card.md): a table, the member tree carrying each member's
# worked-on sentence, the embedded Team view.  The round trips compare it by the rule of the
# spec's section 8.3 rather than byte for byte.
TEAM_TABLE_HEADER = "| Member | ID | Persona | Model | Status | Run | Tokens | Cost (list) | Tools |"  # Cost (list) since SPD-013
TEAM_TABLE_DELIMITER = "|---|---|---|---|---|---|---|---|---|"
TEAM_VIEW_EMBED = "![[Fleet.base#Team]]"
WORKED_ON_SUFFIX = " — "


def split_team_section(text):
    """A note without the body of its `## Team` section (the heading line stays), and that body;
    (text, None) for a note that has no such section."""
    lines = text.split("\n")
    if "## Team" not in lines:
        return text, None
    start = lines.index("## Team") + 1
    end = start
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    return "\n".join(lines[:start] + lines[end:]), "\n".join(lines[start:end])


def team_section_problems(source, rendered):
    """What is wrong with a rendered `## Team` body against the body it was imported from, [] when
    nothing: the tree lines (lines opening `- [[`), each without its ` — …` suffix, equal the
    source's in order; every source tree line that has a suffix is rendered unchanged; a section
    with members starts with the table header and delimiter and ends with the embed line, and a
    section without members is empty."""

    def tree(text):
        return [line.rstrip() for line in text.split("\n") if line.lstrip().startswith("- [[")]

    def bare(line):
        return line.split(WORKED_ON_SUFFIX, 1)[0]

    want, got = tree(source), tree(rendered)
    problems = []
    if [bare(line) for line in got] != [bare(line) for line in want]:
        problems.append("the tree lines without their suffixes differ: source %r, rendered %r" % ([bare(l) for l in want], [bare(l) for l in got]))
    for i, line in enumerate(want):
        if WORKED_ON_SUFFIX in line and (i >= len(got) or got[i] != line):
            problems.append("the source tree line %r is not rendered unchanged" % line)
    lines = [line.rstrip() for line in rendered.strip("\n").split("\n")] if rendered.strip() else []
    if want or got:
        if lines[:2] != [TEAM_TABLE_HEADER, TEAM_TABLE_DELIMITER]:
            problems.append("the section does not start with the table header and delimiter: %r" % lines[:2])
        if lines[-1:] != [TEAM_VIEW_EMBED]:
            problems.append("the section does not end with the embed line: %r" % lines[-1:])
    elif lines:
        problems.append("a ticket without members renders an empty section, not %r" % rendered)
    return problems


# A stand-in for launchctl (SPD-012, per label since SPD-097): records each call's arguments and keeps each job's loaded
# state in a file named after its label, so `schedule install` can bootstrap local.spud.backup and local.spud.render in turn.
FAKE_LAUNCHCTL = r'''"""A stand-in for launchctl: records each call's arguments and keeps each job's loaded state in a file."""
import json
import os
import sys

state = os.environ["FAKE_LAUNCHCTL_STATE"]
calls_path = os.path.join(state, "calls.jsonl")
args = sys.argv[1:]
with open(calls_path, "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")
verb = args[0] if args else ""


def loaded_file(label):
    return os.path.join(state, "loaded-" + label)


if verb == "bootout":
    label = args[1].rsplit("/", 1)[-1]
    if os.path.exists(loaded_file(label)):
        os.remove(loaded_file(label))
        sys.exit(0)
    sys.stderr.write("Boot-out failed: 3: No such process\n")
    sys.exit(3)
if verb == "bootstrap":
    label = os.path.basename(args[2])[:-len(".plist")]
    with open(calls_path, encoding="utf-8") as f:
        attempt = sum(1 for line in f if json.loads(line)[:1] == ["bootstrap"] and os.path.basename(json.loads(line)[2]) == os.path.basename(args[2]))
    failures = os.environ.get("FAKE_LAUNCHCTL_BOOTSTRAP_FAILURES", "0")
    if failures == "all" or attempt <= int(failures) or os.path.exists(loaded_file(label)):
        sys.stderr.write("Bootstrap failed: 5: Input/output error\n")
        sys.exit(5)
    open(loaded_file(label), "w").close()
    sys.exit(0)
if verb == "print":
    label = args[1].rsplit("/", 1)[-1]
    if os.path.exists(loaded_file(label)):
        sys.stdout.write("%s = {\n}\n" % args[1])
        sys.exit(0)
    sys.stderr.write('Could not find service "%s" in domain for user gui: %d\n' % (label, os.getuid()))
    sys.exit(113)
sys.stderr.write("fake launchctl: unexpected arguments %r\n" % (args,))
sys.exit(64)
'''


# A stand-in for gh (SPD-077): answers `gh pr view <url> --json <fields>` from a JSON file the test writes and records
# every call with the directory it ran in, so a test can assert both what was asked and where.  Nothing reaches the network.
FAKE_GH = r'''"""A stand-in for gh: answers `pr view <url> --json <fields>` from a JSON file and records every call."""
import json
import os
import sys

state = os.environ["FAKE_GH_STATE"]
args = sys.argv[1:]
with open(os.path.join(state, "calls.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": args, "cwd": os.getcwd()}) + "\n")
with open(os.path.join(state, "answers.json"), encoding="utf-8") as f:
    answers = json.load(f)
url = args[2] if len(args) > 2 else ""
answer = answers.get(url)
if answer is None:
    sys.stderr.write("could not resolve to a PullRequest with the URL %s\n" % url)
    sys.exit(1)
if isinstance(answer, str):  # a failure the test asked for: gh's own complaint on stderr and a non-zero exit
    sys.stderr.write(answer + "\n")
    sys.exit(1)
if answer == {}:  # output the reader cannot parse
    sys.stdout.write("not json at all")
    sys.exit(0)
sys.stdout.write(json.dumps(answer))
sys.exit(0)
'''


class GhMixin:
    """The stand-in for gh wired into self.home.env (SPD-077): `answer_pr` says what one URL answers, `gh_calls` reads what
    the CLI asked for and where.  Without this a Home has SPUD_GH=off and reads nothing."""

    def setup_gh(self):
        scratch = tempfile.TemporaryDirectory(prefix="spud-gh-")
        self.addCleanup(scratch.cleanup)
        self.gh_state = Path(scratch.name).resolve()
        (self.gh_state / "answers.json").write_text("{}", encoding="utf-8")
        fake = self.gh_state / "fake_gh.py"
        fake.write_text(FAKE_GH, encoding="utf-8")
        self.gh = self.gh_state / "gh"
        self.gh.write_text("#!/bin/sh\nexec %s -I -S %s \"$@\"\n" % (shlex.quote(sys.executable), shlex.quote(str(fake))), encoding="utf-8")
        self.gh.chmod(0o755)
        self.home.env.update({"SPUD_GH": str(self.gh), "FAKE_GH_STATE": str(self.gh_state)})

    def answer_pr(self, url, state=None, merged_at=None, number=None, error=None, unparseable=False):
        path = self.gh_state / "answers.json"
        answers = json.loads(path.read_text(encoding="utf-8"))
        if error is not None:
            answers[url] = error
        elif unparseable:
            answers[url] = {}
        else:
            answers[url] = {"state": state, "mergedAt": merged_at, "url": url, "number": number}
        path.write_text(json.dumps(answers), encoding="utf-8")

    def forget_pr(self, url):
        """Make the fake gh answer as it does for a URL GitHub does not know."""
        path = self.gh_state / "answers.json"
        answers = json.loads(path.read_text(encoding="utf-8"))
        answers.pop(url, None)
        path.write_text(json.dumps(answers), encoding="utf-8")

    def gh_calls(self):
        path = self.gh_state / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def fake_launchctl(scratch, env):
    """The stand-in for launchctl written under `scratch` and wired into `env`: (the program, its state directory).

    SPW-001 phase 4 made this the difference between a test and an accident: `spud init` installs the two LaunchAgents,
    and launchctl's domain is this Mac's user domain while `local.spud.backup` and `local.spud.render` are this Mac's
    own labels -- SPUD_LAUNCH_AGENTS_DIR moves the plist a test writes, and nothing moves the job a `bootout` removes.
    So every fixture that can reach `schedule install`, init included, names a launchctl of its own.

    SPW-011: what this replaces is `GUARD_LAUNCHCTL`, the refusing stub the guard block gives this process and every
    Home.  One mechanism, two programs: a fixture that wants to watch launchctl calls it here, and a fixture that
    forgets gets the refusal rather than /bin/launchctl.
    """
    state = scratch / "launchctl-state"
    state.mkdir(parents=True, exist_ok=True)
    fake = scratch / "fake_launchctl.py"
    fake.write_text(FAKE_LAUNCHCTL, encoding="utf-8")
    launchctl = scratch / "launchctl"
    launchctl.write_text("#!/bin/sh\nexec %s -I -S %s \"$@\"\n" % (shlex.quote(sys.executable), shlex.quote(str(fake))), encoding="utf-8")
    launchctl.chmod(0o755)
    env.update({"SPUD_LAUNCHCTL": str(launchctl), "FAKE_LAUNCHCTL_STATE": str(state)})
    env.pop("FAKE_LAUNCHCTL_BOOTSTRAP_FAILURES", None)
    return launchctl, state


class LaunchdMixin:
    """A scratch LaunchAgents directory and the fake launchctl, wired into self.home.env (SPD-012, shared since SPD-097)."""

    def setup_launchd(self):
        scratch = tempfile.TemporaryDirectory(prefix="spud-schedule-")
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name).resolve()
        self.agents = self.scratch / "LaunchAgents"
        self.launchctl, self.state = fake_launchctl(self.scratch, self.home.env)
        self.home.env["SPUD_LAUNCH_AGENTS_DIR"] = str(self.agents)

    def launchctl_calls(self):
        path = self.state / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []
