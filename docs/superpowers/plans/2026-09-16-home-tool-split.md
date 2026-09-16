# Home and tool split (SPD-097) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Spud's home a plain directory (database, config, vault) that the `spud` tool, a git repository like any project, serves from wherever it runs: no ledger write needs git, the vault follows the database within seconds, and one command moves the home out of the tool repository.

**Architecture:** The program learns two roots instead of one: `ctx.home` (SPUD_HOME or the `~/.config/spud/home` pointer, never git) and `ctx.tool` (the checkout whose `bin/spud` is running), and every durable reference (hook lines, allow rules, LaunchAgents, the `/spud` skill, the spudagent source) names the tool while the database, the rendered notes and Spud's own files stay in the home. The home enters the path rule as one synthetic row under the reserved key `home`, so project 1 (`spud`) becomes an ordinary project rooted at the tool repository. `spud render` is split into a lockable pass that writes nothing when nothing changed, a watcher (`render --watch`, LaunchAgent `local.spud.render`) repeats that pass whenever the event log moves, and `spud home move` copies the home to its new directory under the preconditions of the spec and re-syncs everything that named the old one.

**Tech Stack:** Python 3.14 standard library only (`sqlite3`, `fcntl`, `signal`, `plistlib`, `shutil`), run as `python3.14 -I -S`; macOS launchd; `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-16-home-tool-split-design.md` (Eric approved it section by section on 2026-09-16). This plan covers its sections 2, 3, 4 and 5, the tool-side text of section 7, and the SPD-097 tests of section 9. Section 6 and the SPD-098 tests are SPD-098's. The two `CLAUDE.md` rewrites and the memory copy of section 7 are Spud's own work and appear only in the runbook at the end.

## Global Constraints

- Standard library only, every run `python3.14 -I -S`; no third-party dependency, no `sys.path` entry, no `__init__.py`, no import inside a function (`.claude/skills/spudlib-modules/SKILL.md` §1 and §4).
- The home is `~/Personal/SpudHome`, "a plain directory, not a git repository, and the Obsidian vault". The tool stays `~/Personal/Spud`, "registered as project `spud` with sessions `claim` and landing `merge`".
- "The key `home` is reserved, and `project add` refuses it."
- A deliverable glob is "bare (the ticket's project checkout …), `<key>:<glob>` (another project's), or `home:<glob>` (the home)".
- "`spud ledger commit` is removed, along with every message, hint and template line that names it."
- "Hooks are unchanged, so their timing budget is untouched": `HOOK_PATH` in `tests/test_package.py` stays exact, and `tests/probes/hook_timing.py` passes within 1 ms of `main` for every hook case.
- No schema change in SPD-097 ("any schema change beyond one column" is a non-goal, and the one column is SPD-098's). `events.kind` carries a CHECK constraint, so no new event kind: the move is recorded with `report.entry`, `project.edited`, `config.synced` and `project.installed` events.
- "The real home is off limits. Every test builds its home in a temporary directory through `SPUD_HOME`, and the suite fails if the real home's database is opened."
- "A render that changes nothing writes nothing."
- `~250` lines is a look-again point for a module, never a cap; every module placed by the nine-directory table of `spudlib-modules` §2, and every new command module off the hook path.
- No `git commit` by the engineer (Law 7: Spud commits); no ticket; nothing written under `ledger/` or `reports/`.

---

## How this plan is run

- **Phases, not commits.** The work is five phases. Each phase ends with the whole suite green and no bytecode left behind; a phase boundary is where a fresh engineer can take over with only this file and the spec, and where Spud can commit the branch. There is no commit step anywhere below: Spud commits.
- **The engineer's deliverables** (Spud plans them): `bin/**`, `tests/**`, `.claude/agents/spudagent.md`. Nothing else is touched.
- **Verification commands** (from the worktree's root):
  - one file: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home.py`
  - one test: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home.py -k test_the_pointer_is_next`
  - the guard test, seconds: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_package.py`
  - the suite, about ten minutes, in the background: `python3.14 -I -S -m unittest discover -s tests -t tests`
  - no bytecode: `find bin tests -name '*.pyc' -o -name __pycache__` prints nothing.
- **The engineer's Bash hook refuses `spud init`, `spud render`, `spud settings sync` and every other Spud-only command by name (Law 6), against any home.** Every check in this plan therefore runs through the suite or a probe script under `tests/probes/`, never as `bin/spud … --as spud` on the command line. The tests run the CLI as a subprocess against scratch homes, which the hook does not see.
- **Progress lines.** At the end of each phase: `spud --as <id> member log "Phase N done: <what changed>, suite green (<count> tests)"`.
- **Style.** Docstrings in the package's voice (one line naming the directory and module first), comments that name the ticket, `%`-formatting as the codebase does, no f-strings, `kernel.SpudError` with an exit code for every refusal.

## The transition choice

Between the merge of SPD-097 and the switch-over there is a window in which the main checkout runs the new code while `~/Personal/Spud` is still the home. This plan makes that window safe and short rather than keeping two code paths alive:

- **Resolution.** `~/.config/spud/home` already names `/Users/ericlugo/Personal/Spud` (written by `project install badtakes`), so once the git fallback is gone every `spud` call without `SPUD_HOME` still finds the home, and every hook line carries `SPUD_HOME=` explicitly. Nothing has to be written before the merge.
- **One directory, two roles.** During the window the home and project `spud`'s root are the same directory. `project_checkouts` lists the home first and `map_into_checkouts` keeps the first root of an identity, so paths under `~/Personal/Spud` get the home's rules (generated roots, Spud's own paths) exactly as today, and `session_mode` sees a session launched there as the home's. Project `spud` keeps `sessions = 'always'` until `home move` flips it to `claim`, so nothing in the window is refused that is allowed today.
- **`ledger commit` is gone at the merge**, as the spec says. In the window Spud renders with `spud render` and commits `ledger/` and `reports/` with plain git; the runbook at the end says exactly when. The window is meant to be one sitting: merge, then `home move`.
- **Rejected:** keeping `ledger commit` until the removal commit. It would keep every git-on-the-home code path, its tests and its hook wording alive for one more commit, and the spec removes it in SPD-097.

## File structure

| File | Change | Responsibility after SPD-097 |
| --- | --- | --- |
| `bin/spud` | modify | the launcher: the bytecode cache under the home named by `SPUD_HOME` or the pointer |
| `bin/spud_ledger.py` | modify | `main` resolves the home without the script's path |
| `bin/spudlib/core/kernel.py` | modify | `HOME_KEY` |
| `bin/spudlib/core/homeconf.py` | modify | `resolve_home` (no git), `tool_root`, `tool_checkout_kind`, `Ctx.tool`, `Ctx.launcher` |
| `bin/spudlib/hooks/worktrees.py` | modify | the home as a synthetic row: `home_row`, `is_home`, `project_root`, `project_checkouts` |
| `bin/spudlib/hooks/pathrule.py` | modify | the home's rules keyed on `is_home`, not `id == 1` |
| `bin/spudlib/hooks/recording.py` | modify | the SubagentStart sentence names `home:` globs for every ticket |
| `bin/spudlib/hooks/sessionhooks.py` | modify | the home's board for a session in the home; the claim text names the tool's launcher |
| `bin/spudlib/state/actors.py` | modify | the unclaimed-session check over every claim project, the home excepted |
| `bin/spudlib/state/ledgerdb.py` | modify | project 1 is inserted with the tool's root and remote |
| `bin/spudlib/state/ops.py` | modify | `home:` accepted in a deliverable |
| `bin/spudlib/projects/registry.py` | modify | the reserved key; project `spud` may change sessions and root; every project's settings file is `settings.local.json` |
| `bin/spudlib/projects/install.py` | modify | project `spud` installs like any project once its root is not the home; the spudagent source is the tool's |
| `bin/spudlib/projects/sessions.py` | modify | `session_mode` and `session show` know the home; the claim card loses `ledger commit`; the skill names the tool's launcher |
| `bin/spudlib/shell/syntax.py`, `shell/spud_calls.py`, `shell/bash_rule.py` | modify | the Bash hook vouches for the tool's launcher |
| `bin/spudlib/commands/settings_sync.py` | modify | hook lines and allow rules name the tool's launcher; a worktree warning |
| `bin/spudlib/commands/schedule.py` | modify | two LaunchAgents: backup and render |
| `bin/spudlib/commands/publish.py` | modify | `render_pass`, `render_lock`, no write when nothing changed, a conflict logged once; `ledger commit` removed |
| `bin/spudlib/commands/renderwatch.py` | create | `render --watch`, the watch lock, whether a watcher is alive |
| `bin/spudlib/commands/doctor.py` | modify | `doctor_report`; the tool line, the watcher, open conflicts |
| `bin/spudlib/commands/views.py` | modify | `board --brief` says when the watcher is down |
| `bin/spudlib/commands/ticketcmds.py` | modify | a ticket created in the home goes to project `spud` |
| `bin/spudlib/commands/homemove.py` | create | `spud --as spud home move` |
| `bin/spudlib/hooks/hookio.py` | modify | the command tables: `home move` in, `ledger commit` out |
| `bin/spudlib/cli/cliparser.py`, `cli/helptexts.py` | modify | the parsers and help texts |
| `.claude/agents/spudagent.md` | modify | `home:` globs, no path to the ledger root as a checkout |
| `tests/helpers.py` | modify | the real-home guard, `SPUD_TOOL_DIR`, `make_tool`, the fake launchctl |
| `tests/test_home.py` | create | resolution, the tool root, the launcher references, the guard |
| `tests/test_home_split.py` | create | the home in the path rule, `home:` globs, the reserved key, project `spud` |
| `tests/test_watch.py` | create | the watcher, the render lock, a conflict logged once |
| `tests/test_home_move.py` | create | `home move` |
| `tests/probes/render_timing.py` | create | a full pass and a no-change pass timed over a synthetic ledger |
| `tests/test_ledger_commit.py` | delete | |
| `tests/test_markdown.py`, `test_launcher.py`, `test_render.py`, `test_backup.py`, `test_projects.py`, `test_sessions.py`, `test_auto_claim.py`, `test_hooks_projects.py`, `tests/probes/headless_projects.py` | modify | the assertions named in the tasks |

---

# Phase 1: the home is a directory, the tool is a repository

Spec section 2 (resolution and the launcher every reference names) and the test guard of section 9. After this phase every durable reference to the program names `ctx.launcher`, the tool's `bin/spud`, and `resolve_home` never asks git.

### Task 1: The real-home guard and the tool override in the test helpers

**Files:**
- Modify: `tests/helpers.py`
- Test: `tests/test_home.py` (created here, grown in Tasks 2 to 4)

**Interfaces:**
- Produces: `helpers.GUARD_HOME` (str), `Home.env["SPUD_TOOL_DIR"]` (the scratch home by default), `RepoMixin.make_tool()` returning a `Path`, `helpers.FAKE_LAUNCHCTL` (str) and `LaunchdMixin.setup_launchd()`.

- [ ] **Step 1: Write the failing guard test**

Create `tests/test_home.py`:

```python
"""SPD-097: the home is a plain directory and the tool is a repository.  Home resolution without git, the tool root and the
launcher every durable reference names, and the guard that keeps the suite off the real home."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import EXIT_ERROR, GUARD_HOME, REPO, SPUD, RepoMixin, SpudTestCase, load_spud_module

spud = load_spud_module()
SESSION = "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b"


class RealHomeGuardTest(unittest.TestCase):
    def test_the_test_process_names_a_home_that_does_not_exist(self):
        self.assertEqual(os.environ["SPUD_HOME"], GUARD_HOME)
        self.assertFalse(os.path.exists(GUARD_HOME))
        proc = subprocess.run([sys.executable, "-I", "-S", str(SPUD), "board"], capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        self.assertIn(GUARD_HOME, proc.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to see it fail**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home.py`
Expected: FAIL with `ImportError: cannot import name 'GUARD_HOME'`.

- [ ] **Step 3: Add the guard, the override, `make_tool` and the fake launchctl to the helpers**

In `tests/helpers.py`, right after `sys.dont_write_bytecode = True` (line 26), add:

```python
# SPD-097: the real home is off limits.  The test process's own environment names a home that does not exist, so a CLI run
# that inherits os.environ without a Home's env fails on "no ledger at" or "no spud.config.json in" the guard path instead
# of opening the real ledger: the cause SPD-092 could not name was a run with no SPUD_HOME, which resolved the real home
# (through git before SPD-097, through the ~/.config/spud/home pointer since).  Every Home derives its env from os.environ
# and sets its own SPUD_HOME, SPUD_CONFIG_DIR and SPUD_TOOL_DIR.
GUARD_HOME = os.path.join(tempfile.gettempdir(), "spud-test-guard-%d-does-not-exist" % os.getpid())
os.environ["SPUD_HOME"] = GUARD_HOME
os.environ["SPUD_CONFIG_DIR"] = os.path.join(GUARD_HOME, "config")
os.environ["SPUD_TOOL_DIR"] = GUARD_HOME
```

In `Home.__init__`, after the line `self.env["SPUD_CONFIG_DIR"] = str(self.path / ".user-config")`, add:

```python
        # SPD-097: the tool, the checkout whose bin/spud the hook lines, allow rules, LaunchAgents and the /spud skill name
        # and where the spudagent source is read, is this scratch home unless a test names another.  So the assertions the
        # suite made before the split keep their `<home>/bin/spud` shape; a test of the split builds a separate tool with
        # RepoMixin.make_tool() and sets SPUD_TOOL_DIR before `spud init` (init records the tool as project spud's root).
        self.env["SPUD_TOOL_DIR"] = str(self.path)
```

In `RepoMixin`, after `add_worktree`, add:

```python
    def make_tool(self):
        """A scratch main checkout playing the tool repository (SPD-097): this checkout's bin/ and its spudagent source,
        committed on main, with .claude/settings.local.json ignored as the real repository ignores it."""
        tool = self.make_repo("tool-")
        shutil.copytree(REPO / "bin", tool / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        (tool / ".claude" / "agents").mkdir(parents=True)
        shutil.copyfile(REPO / ".claude" / "agents" / "spudagent.md", tool / ".claude" / "agents" / "spudagent.md")
        (tool / ".gitignore").write_text(".claude/settings.local.json\n", encoding="utf-8")
        git(tool, "add", "-A")
        git(tool, "commit", "-q", "-m", "tool")
        return tool
```

At the end of `tests/helpers.py`, add the fake launchctl (moved here from `tests/test_backup.py`, now keyed per label so two agents can be loaded at once) and the mixin that installs it:

```python
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


class LaunchdMixin:
    """A scratch LaunchAgents directory and the fake launchctl, wired into self.home.env (SPD-012, shared since SPD-097)."""

    def setup_launchd(self):
        scratch = tempfile.TemporaryDirectory(prefix="spud-schedule-")
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name).resolve()
        self.agents = self.scratch / "LaunchAgents"
        self.state = self.scratch / "launchctl-state"
        self.state.mkdir()
        fake = self.scratch / "fake_launchctl.py"
        fake.write_text(FAKE_LAUNCHCTL, encoding="utf-8")
        self.launchctl = self.scratch / "launchctl"
        self.launchctl.write_text("#!/bin/sh\nexec %s -I -S %s \"$@\"\n" % (shlex.quote(sys.executable), shlex.quote(str(fake))), encoding="utf-8")
        self.launchctl.chmod(0o755)
        self.home.env.update({"SPUD_LAUNCH_AGENTS_DIR": str(self.agents), "SPUD_LAUNCHCTL": str(self.launchctl), "FAKE_LAUNCHCTL_STATE": str(self.state)})
        self.home.env.pop("FAKE_LAUNCHCTL_BOOTSTRAP_FAILURES", None)

    def launchctl_calls(self):
        path = self.state / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []
```

Add `import shlex` to the helpers' imports (alphabetically, after `import os`).

In `tests/test_backup.py`: delete the `FAKE_LAUNCHCTL` string (lines 374 to 409), make `ScheduleTest` inherit `LaunchdMixin, SpudTestCase` (import `LaunchdMixin` from helpers), and replace its `setUp` body from `scratch = tempfile.TemporaryDirectory(...)` through `self.home.env.pop("FAKE_LAUNCHCTL_BOOTSTRAP_FAILURES", None)` with one line, `self.setup_launchd()`. Keep the lines that set `self.plist_path`, `self.uid`, `self.service` and `self.interpreter`. A test there that reads `calls.jsonl` directly keeps working: the file has the same name in the same place.

- [ ] **Step 4: Run the guard test and the backup tests**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home.py`
Expected: OK (1 test).
Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_backup.py`
Expected: OK. The fake now answers `print gui/<uid>/local.spud.backup` from `loaded-local.spud.backup`; if a test asserted the exact `print` output line `gui/501/local.spud.backup = {`, it still matches, since the fake echoes the service it was asked about.

### Task 2: `resolve_home` without git; `ctx.tool` and `ctx.launcher`

**Files:**
- Modify: `bin/spudlib/core/homeconf.py:17-39` (`resolve_home`), `:42-51` (`Ctx.__init__`)
- Modify: `bin/spud_ledger.py:147`
- Modify: `tests/test_markdown.py:250-265` (delete `HomeResolutionTest`)
- Test: `tests/test_home.py`

**Interfaces:**
- Produces: `homeconf.resolve_home(env) -> (Path, str)`; `homeconf.tool_root(env=None) -> Path`; `homeconf.tool_checkout_kind(tool) -> "main" | "worktree" | "none"`; `homeconf.Ctx(home, resolved_by, json_mode, tool=None)` with `ctx.tool: Path` and `ctx.launcher: Path` (`ctx.tool / "bin" / "spud"`).
- Consumes: `helpers.load_spud_module()`, `RepoMixin.make_repo`, `add_worktree`, `scratch_dir`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_home.py` (before the `if __name__` block):

```python
class HomeResolutionTest(unittest.TestCase):
    def test_spud_home_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, how = spud.resolve_home({"SPUD_HOME": tmp, "SPUD_CONFIG_DIR": tmp})
            self.assertEqual((home, how), (Path(tmp).resolve(), "SPUD_HOME"))

    def test_the_pointer_is_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config"
            config.mkdir()
            (config / "home").write_text("%s/the-home\n" % tmp, encoding="utf-8")
            home, how = spud.resolve_home({"SPUD_CONFIG_DIR": str(config)})
            self.assertEqual((home, how), ((Path(tmp) / "the-home").resolve(), "~/.config/spud/home"))

    def test_neither_names_both_and_git_is_never_asked(self):
        # this test runs inside a git checkout: before SPD-097 the fallback would have answered with its common dir
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(spud.SpudError) as caught:
                spud.resolve_home({"SPUD_CONFIG_DIR": tmp})
            self.assertIn("SPUD_HOME", caught.exception.message)
            self.assertIn(os.path.join(tmp, "home"), caught.exception.message)


class ToolRootTest(RepoMixin, unittest.TestCase):
    def test_the_tool_is_the_checkout_holding_the_package_unless_overridden(self):
        self.assertEqual(spud.tool_root({}), REPO)
        self.assertEqual(spud.tool_root({"SPUD_TOOL_DIR": "/tmp/../tmp/tool"}), Path("/tmp/tool"))

    def test_the_launcher_is_the_tools_bin_spud(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = spud.Ctx(Path(tmp), "SPUD_HOME", False, tool=Path(tmp) / "tool")
            self.assertEqual((ctx.tool, ctx.launcher), (Path(tmp) / "tool", Path(tmp) / "tool" / "bin" / "spud"))
            self.assertEqual(spud.Ctx(Path(tmp), "SPUD_HOME", False).tool, Path(os.environ["SPUD_TOOL_DIR"]))

    def test_checkout_kind(self):
        repo = self.make_repo("tool-")
        self.assertEqual(spud.tool_checkout_kind(repo), "main")
        self.assertEqual(spud.tool_checkout_kind(self.add_worktree(repo, "wt")), "worktree")
        self.assertEqual(spud.tool_checkout_kind(self.scratch_dir("plain-")), "none")
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home.py`
Expected: `HomeResolutionTest.test_neither_names_both_and_git_is_never_asked` fails (no exception: the git fallback answers), `ToolRootTest` errors with `AttributeError: module 'spud_ledger' has no attribute 'tool_root'`.

- [ ] **Step 3: Implement**

Replace `resolve_home` in `bin/spudlib/core/homeconf.py` (lines 17 to 39) with:

```python
def resolve_home(env):
    """SPUD_HOME, else the ~/.config/spud/home pointer (SPD-097: the home is a plain directory, so nothing about the running
    script says where it is; the git fallback of SPD-007 is gone).  Returns (path, how)."""
    if env.get("SPUD_HOME"):
        return Path(env["SPUD_HOME"]).expanduser().resolve(), "SPUD_HOME"
    pointer = spud_config_dir(env) / "home"
    if pointer.is_file():
        target = pointer.read_text(encoding="utf-8").strip()
        if target:
            return Path(target).expanduser().resolve(), "~/.config/spud/home"
    raise kernel.SpudError(kernel.EXIT_ERROR, "cannot find Spud's home: set SPUD_HOME, or write the home's path to %s" % pointer)


def tool_root(env=None):
    """The checkout whose bin/spud is running: the parent of the bin/ directory this package sits in, symlinks resolved;
    SPUD_TOOL_DIR overrides it, for tests (SPD-097).  What the hook lines, the allow rules, the LaunchAgents and the /spud
    skill name, where the spudagent source is read, and project spud's root at `spud init`."""
    env = os.environ if env is None else env
    if env.get("SPUD_TOOL_DIR"):
        return Path(os.path.abspath(os.path.expanduser(env["SPUD_TOOL_DIR"])))
    return Path(__file__).resolve().parents[3]


def tool_checkout_kind(tool):
    """`main` when the tool is the main checkout of a git repository, `worktree` for a linked worktree, `none` when git names
    no repository there.  A command that writes the tool's path somewhere durable warns on `worktree`: a worktree is deleted
    when its ticket lands, and a hook line naming it would die with it (SPD-097)."""
    if not os.path.lexists(os.path.join(str(tool), ".git")):
        return "none"
    try:
        proc = run_git(tool, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir", timeout=30)
    except kernel.SpudError:
        return "none"
    lines = proc.stdout.strip().split("\n") if proc.returncode == 0 else []
    if len(lines) != 2:
        return "none"
    return "main" if os.path.realpath(lines[0]) == os.path.realpath(lines[1]) else "worktree"
```

Replace `Ctx.__init__` (lines 45 to 51) with:

```python
    def __init__(self, home, resolved_by, json_mode, tool=None):
        self.home = home
        self.resolved_by = resolved_by
        self.json = json_mode
        self.db_path = home / ".spud" / "ledger.db"
        self.tool = tool_root() if tool is None else Path(tool)  # SPD-097: the tool repository; the home is not one
        self._config = None
        self.hook_project = None  # `spud hook <event> --project <key>`: the failure policy of a project's hook line (SPD-014)

    @property
    def launcher(self):
        """The running tool's bin/spud: what every durable reference to the program names (SPD-097)."""
        return self.tool / "bin" / "spud"
```

In `bin/spud_ledger.py` line 147, `home, how = homeconf.resolve_home(os.environ, Path(__file__))` becomes `home, how = homeconf.resolve_home(os.environ)`. `Path` is still used by the launcher's `ModuleSpec` handling (`from pathlib import Path` stays only if something else in the file reads it; if `Path` is now unused, remove that import).

In `tests/test_markdown.py`, delete the class `HomeResolutionTest` (lines 250 to 265) and the docstring words "and for SPUD_HOME resolution" on line 2; `tests/test_home.py` owns resolution now. Remove `tempfile` and `Path` from its imports if nothing else there uses them.

- [ ] **Step 4: Run the tests**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home.py`
Expected: OK (7 tests).
Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_markdown.py`
Expected: OK.
Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_package.py`
Expected: OK (`tool_root` and `tool_checkout_kind` are new names in one module; `homeconf` imports nothing new).

### Task 3: The launcher's cache directory from the pointer

**Files:**
- Modify: `bin/spud:9-12` (docstring), `:39-45` (`load`)
- Test: `tests/test_launcher.py`

**Interfaces:**
- Produces: `home_dir()` in `bin/spud` (a module-level function of the launcher; nothing imports it).

- [ ] **Step 1: Write the failing test**

Add to `LauncherTest` in `tests/test_launcher.py`:

```python
    def test_the_cache_dir_comes_from_the_pointer_when_spud_home_is_unset(self):
        config = self.home.path / ".user-config"
        config.mkdir(exist_ok=True)
        (config / "home").write_text(str(self.home.path) + "\n", encoding="utf-8")
        env = dict(self.home.env)
        env.pop("SPUD_HOME")
        proc = subprocess.run([sys.executable, "-I", "-S", str(SPUD), "board"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc)
        self.assertTrue(cached_programs(self.home.path / ".spud" / "pycache"))
        self.assertEqual(cached_programs(REPO / "bin"), [])

    def test_no_home_at_all_means_no_cache_and_a_clear_refusal(self):
        env = dict(self.home.env)
        env.pop("SPUD_HOME")
        env["SPUD_CONFIG_DIR"] = str(self.home.path / "no-such-config")
        proc = subprocess.run([sys.executable, "-I", "-S", str(SPUD), "board"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 1, proc)
        self.assertIn("cannot find Spud's home", proc.stderr)
        self.assertEqual(cached_programs(REPO / "bin"), [])
```

- [ ] **Step 2: Run it to see it fail**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_launcher.py -k pointer`
Expected: FAIL: with SPUD_HOME unset the launcher caches under the checkout it sits in (its `os.path.dirname(here)` fallback), so `cached_programs(self.home.path / ".spud" / "pycache")` is empty.

- [ ] **Step 3: Implement**

In `bin/spud`, replace lines 9 to 12 of the docstring with:

```
Its bytecode goes under <home>/.spud/pycache/, in a tree mirroring each file's own path, so a worktree's program and
the main checkout's never share a file.  <home> is SPUD_HOME, else what ~/.config/spud/home names (SPD-097: the home
is a plain directory, never the checkout this launcher sits in), and nothing is cached unless <home>/.spud/ already
exists: refused to edits and to shell commands like the database beside it.  Only the program's bytecode goes there;
the standard-library modules it imports keep Python's defaults.
```

Add before `def load():`:

```python
def home_dir():
    """SPUD_HOME, else the ~/.config/spud/home pointer (SPD-097): the home whose state directory holds the bytecode cache;
    None when neither names one, and then nothing is cached."""
    home = os.environ.get("SPUD_HOME")
    if not home:
        pointer = os.path.join(os.path.expanduser(os.environ.get("SPUD_CONFIG_DIR") or "~/.config/spud"), "home")
        try:
            with open(pointer, encoding="utf-8") as f:
                home = f.read().strip()
        except OSError:
            return None
    return os.path.abspath(os.path.expanduser(home)) if home else None
```

In `load()`, replace

```python
    home = os.path.abspath(os.path.expanduser(os.environ.get("SPUD_HOME") or os.path.dirname(here)))
    state = os.path.join(home, ".spud")
    if os.path.isdir(state):
        CachedLoader.pycache = os.path.join(state, "pycache")
```

with

```python
    home = home_dir()
    state = os.path.join(home, ".spud") if home else None
    if state and os.path.isdir(state):
        CachedLoader.pycache = os.path.join(state, "pycache")
```

- [ ] **Step 4: Run the launcher tests**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_launcher.py`
Expected: OK, including `test_the_launcher_is_small_and_the_program_is_a_module_beside_it` (the launcher stays under 80 lines: 58 plus 12).

### Task 4: Every `<home>/bin/spud` becomes `ctx.launcher`

**Files:**
- Modify: `bin/spudlib/commands/settings_sync.py:32-51` (`hook_command`, `cli_allow_rules`), `:162-192` (`cmd_settings_sync`)
- Modify: `bin/spudlib/commands/schedule.py:112-128` (`schedule_plist`)
- Modify: `bin/spudlib/projects/sessions.py:83-96` (the skill text)
- Modify: `bin/spudlib/projects/install.py:19-28` (`install_files`), `:116`
- Modify: `bin/spudlib/hooks/sessionhooks.py:75`
- Modify: `bin/spudlib/commands/doctor.py:123`, `:148-151`
- Modify: `bin/spudlib/shell/syntax.py:315-317`, `bin/spudlib/shell/spud_calls.py:141-174`, `bin/spudlib/shell/bash_rule.py:100`
- Test: `tests/test_home.py`

**Interfaces:**
- Consumes: `ctx.launcher`, `ctx.tool` (Task 2).
- Produces: `settings_sync.tool_warning(ctx) -> str | None`; `sessions.skill_markdown(ctx)` (was `skill_markdown(home)`); `sessions.SKILL_CLAIM` and `HOOK_CLAIM` formatted with `{launcher}`; `spud_calls.launcher_vouched(script, cwds, launcher)`; `syntax.ShellAnalysis(cwd=None, home=None, launcher=None)` with `.launcher`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_home.py`:

```python
class SeparateToolTest(RepoMixin, SpudTestCase):
    """With SPUD_TOOL_DIR naming a checkout other than the home, every durable reference names the tool's bin/spud."""

    def setUp(self):
        super().setUp()
        self.tool = self.make_tool()
        self.home.env["SPUD_TOOL_DIR"] = str(self.tool)
        self.home.env["SPUD_LAUNCHCTL"] = "/usr/bin/false"
        self.home.env["SPUD_LAUNCH_AGENTS_DIR"] = str(self.scratch_dir("agents-"))
        self.launcher = str(self.tool / "bin" / "spud")

    def test_settings_sync_names_the_tools_launcher_and_the_home(self):
        out = self.home.json("settings", "sync", "--dry-run", actor="spud")
        commands = [h["command"] for groups in out["settings"]["hooks"].values() for g in groups for h in g["hooks"]]
        self.assertTrue(commands)
        for command in commands:
            self.assertTrue(command.startswith("SPUD_HOME=%s %s -I -S %s hook " % (self.home.path, sys.executable, self.launcher)), command)
        self.assertIn("Bash(python3.14 -I -S %s *)" % self.launcher, out["settings"]["permissions"]["allow"])
        self.assertNotIn("Bash(python3.14 -I -S %s/bin/spud *)" % self.home.path, out["settings"]["permissions"]["allow"])

    def test_settings_sync_warns_when_the_launcher_is_in_a_linked_worktree(self):
        wt = self.add_worktree(self.tool, "spd-999-x")
        self.home.env["SPUD_TOOL_DIR"] = str(wt)
        proc = self.home.run("settings", "sync", "--dry-run", actor="spud")
        self.assertIn("linked worktree", proc.stderr)
        self.home.env["SPUD_TOOL_DIR"] = str(self.tool)
        self.assertEqual(self.home.run("settings", "sync", "--dry-run", actor="spud").stderr, "")

    def test_the_plist_the_skill_and_the_agent_come_from_the_tool(self):
        plist = self.home.json("schedule", "show", actor="spud")["plist"]
        self.assertIn("<string>%s</string>" % self.launcher, plist)
        other = self.make_repo("badtakes-")
        self.add_project(other)
        self.cli("project", "install", "badtakes", actor="spud")
        skill = (self.home.path / ".user-claude" / "skills" / "spud" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("python3.14 -I -S %s --as spud session claim" % self.launcher, skill)
        self.assertIn("Read %s/CLAUDE.md in full" % self.home.path, skill)
        agent = (self.home.path / ".user-claude" / "agents" / "spudagent.md").read_text(encoding="utf-8")
        self.assertEqual(agent, (self.tool / ".claude" / "agents" / "spudagent.md").read_text(encoding="utf-8"))
        self.assertEqual(self.home.run("doctor").returncode, 0)

    def test_the_bash_hook_vouches_for_the_tools_launcher_not_the_homes(self):
        (self.home.path / "bin").mkdir(exist_ok=True)
        shutil.copyfile(SPUD, self.home.path / "bin" / "spud")

        def decision(script):
            payload = {"hook_event_name": "PreToolUse", "session_id": SESSION, "transcript_path": "/tmp/x.jsonl", "cwd": str(self.home.path),
                       "permission_mode": "default", "tool_name": "Bash", "tool_use_id": "t1",
                       "tool_input": {"command": "python3.14 -I -S %s --as spud board" % script}}
            return self.home.hook("PreToolUse", payload).decision

        self.assertEqual(decision(self.launcher), "allow")
        self.assertIsNone(decision(self.home.path / "bin" / "spud"))
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home.py -k SeparateToolTest`
Expected: all four FAIL: the hook lines, rules, plist and skill still name `<home>/bin/spud`, the agent copy is read from the home, and the vouch goes to the home's copy.

- [ ] **Step 3: Implement**

`bin/spudlib/commands/settings_sync.py`: change the import line to `from ..core import homeconf, kernel`; replace `hook_command` and `cli_allow_rules` with:

```python
def hook_command(ctx, event, project_key=None):
    """`SPUD_HOME=<home> <interpreter> -I -S <tool>/bin/spud hook <event>`, absolute, resolved at sync time (SPD-097: the
    launcher is the tool repository's and the home is a plain directory SPUD_HOME alone names); a project's line (SPD-014)
    ends in `--project <key>`, which sets the failure policy of the design's section 6.4."""
    home = str(ctx.home)
    line = "SPUD_HOME=%s %s -I -S %s hook %s" % (shlex.quote(home), shlex.quote(sys.executable), shlex.quote(str(ctx.launcher)), event)
    return line + (" --project %s" % shlex.quote(project_key) if project_key else "")


def cli_allow_rules(ctx):
    """The permission rules for the CLI in the prescribed form, `python3.14 -I -S <tool>/bin/spud ...`: by the documented
    interpreter name and by the absolute interpreter.  None for the script alone: its #! line runs the interpreter with
    neither -I nor -S, so PYTHONPATH and user-site .pth files inherited from the shell load code before the program, and
    that spelling gets the harness's prompt (SPD-038).  merge_allow_rules drops an older sync's rule for it."""
    script = str(ctx.launcher)
    rules = ["Bash(python3.14 -I -S %s *)" % script, "Bash(%s -I -S %s *)" % (sys.executable, script)]
    out = []
    for r in rules:
        if r not in out:
            out.append(r)
    return out


def tool_warning(ctx):
    """The stderr line of a command that writes the tool's path somewhere durable (settings sync, project install, schedule
    install) when that path is a linked worktree (SPD-097): a worktree is deleted when its ticket lands, and a hook line
    naming it would die with it.  None otherwise; a tool with no git at all is deliberate (a copied tree) and says nothing."""
    if homeconf.tool_checkout_kind(ctx.tool) != "worktree":
        return None
    return ("the running bin/spud is in a linked worktree, %s: the lines written name it and will break when the worktree is removed;"
            " rerun this from the main checkout's bin/spud before relying on them" % ctx.tool)
```

In `cmd_settings_sync`, change the last line to `return kernel.Result({...same dict...}, text, stderr=tool_warning(ctx) or "")`.

`bin/spudlib/commands/schedule.py`, `schedule_plist`: the docstring's "the home's bin/spud (never a worktree's copy)" becomes "the tool's bin/spud (SPD-097)", and `str(ctx.home / "bin" / "spud")` becomes `str(ctx.launcher)`.

`bin/spudlib/projects/sessions.py`: replace lines 83 to 96 with:

```python
SKILL_CLAIM = "Run `python3.14 -I -S {launcher} --as spud session claim`. If it refuses, quote the refusal, say this session is not Spud, and stop following these steps."
HOOK_CLAIM = ("The ledger's hook has made the claim (the card below); do not run `session claim`. If Eric says this session is not to be Spud,"
              " run `python3.14 -I -S {launcher} --as spud session release`.")
SKILL_TITLE = "<KEY> - <what this session does>"


def skill_steps(home, claim, title):
    """The numbered steps, one per line, with the claim step and the title filled in."""
    return "".join("%d. %s\n" % (n, step.format(home=home, claim=claim, title=title)) for n, step in enumerate(SKILL_STEPS, start=1))


def skill_markdown(ctx):
    """What project install writes to ~/.claude/skills/spud/SKILL.md: the home's CLAUDE.md and config, the tool's launcher (SPD-097)."""
    return SKILL_HEAD + "\n" + skill_steps(ctx.home, SKILL_CLAIM.format(launcher=ctx.launcher), SKILL_TITLE)
```

`bin/spudlib/projects/install.py`: in `install_files`, `"source_agent": ctx.home / ".claude" / "agents" / "spudagent.md"` becomes `"source_agent": ctx.tool / ".claude" / "agents" / "spudagent.md",  # SPD-097: the source lives in the tool repository`; line 116 `sessions.skill_markdown(ctx.home)` becomes `sessions.skill_markdown(ctx)`; the error on line 97 says "the tool repository's spudagent definition is the source".

`bin/spudlib/hooks/sessionhooks.py` line 75: `sessions.HOOK_CLAIM.format(home=ctx.home)` becomes `sessions.HOOK_CLAIM.format(launcher=ctx.launcher)`.

`bin/spudlib/commands/doctor.py`: line 123 `home_agent = ctx.home / ".claude" / "agents" / "spudagent.md"` becomes `source_agent = ctx.tool / ".claude" / "agents" / "spudagent.md"`, and the two uses on lines 148 and 151 read `source_agent`; the docstring's "the user-scope agent matching the home's" becomes "matching the tool's".

`bin/spudlib/shell/syntax.py`: `ShellAnalysis.__init__(self, cwd=None, home=None)` becomes `__init__(self, cwd=None, home=None, launcher=None)`, with `self.launcher = launcher` after `self.home = home`, and the docstring gains: "`launcher` (SPD-097) is the running tool's bin/spud, the file a spud call must run for the hook to allow it; `home` is what a SPUD_HOME assignment on the line must name."

`bin/spudlib/shell/spud_calls.py`: `launcher_vouched(script, cwds, home)` becomes:

```python
def launcher_vouched(script, cwds, launcher):
    """The script is the running tool's own bin/spud (SPD-097: the tool repository's, which the hook itself runs; never the
    home's, which has none), the same file in the same directory, from every directory the shell may be in."""
    if unresolvable_word(script) or (script.startswith("~") and not script.startswith("~/")):
        return False
    real = os.path.realpath(launcher)
```

with the rest of the body unchanged. In `vouched_spud_call`, `if prefixed or a.home is None or not python_options_vouched(options):` becomes `if prefixed or a.home is None or a.launcher is None or not python_options_vouched(options):`, and `launcher_vouched(script, a.cwds, a.home)` becomes `launcher_vouched(script, a.cwds, a.launcher)`. In the comment block above `PYTHON_FLAGS_RE`, "the launcher: the ledger root's bin/spud by file identity" becomes "the launcher: the running tool's bin/spud by file identity (SPD-097)".

`bin/spudlib/shell/bash_rule.py` line 100: `syntax.ShellAnalysis(cwd=cwd, home=str(ctx.home))` becomes `syntax.ShellAnalysis(cwd=cwd, home=str(ctx.home), launcher=str(ctx.launcher))`.

- [ ] **Step 4: Run the tests**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home.py`
Expected: OK (11 tests).
Run, each: `-p test_settings.py`, `-p test_install.py`, `-p test_backup.py`, `-p test_hooks.py -k SpudCall`, `-p test_hooks_projects.py -k Bash`, `-p test_auto_claim.py`, `-p test_package.py`
Expected: OK everywhere. These files keep their `<home>/bin/spud` expectations because `Home` sets `SPUD_TOOL_DIR` to the scratch home (Task 1).

### Task 5: Phase 1 suite

- [ ] **Step 1: Run the whole suite in the background and the bytecode check**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests`
Expected: OK. Then `find bin tests -name '*.pyc' -o -name __pycache__` prints nothing.

- [ ] **Step 2: Log the phase**

Run: `spud --as <id> member log "Phase 1 done: resolve_home without git, ctx.tool and ctx.launcher, the launcher's cache from the pointer, the test guard; suite green"`

---

# Phase 2: the home in the path rule, `home:` globs, project `spud`, `ledger commit` gone

Spec sections 2 (the plain-directory home under `session show`, `settings sync`, `schedule install`, `doctor`; project `spud` re-registered; the reserved key; `ledger commit` removed) and 3 (paths), plus the tool-side text of section 7. After this phase the home is a row-shaped dict under the reserved key `home`, first in `project_checkouts`, and project 1 is an ordinary project whose root is the tool.

### Task 6: `HOME_KEY`, the home row, and the path rule keyed on it

**Files:**
- Modify: `bin/spudlib/core/kernel.py:26` (after `ALIVE`)
- Modify: `bin/spudlib/hooks/worktrees.py:77-79` (`project_root`), `:119-122` (`project_checkouts`), `:213`
- Modify: `bin/spudlib/hooks/pathrule.py:152-158`, `:233`, `:240`, `:246`, `:253`
- Modify: `bin/spudlib/state/ops.py:103-109`
- Modify: `bin/spudlib/state/ledgerdb.py:89-105` (`sync_config_rows`: project 1 at the tool's root)
- Test: `tests/test_home_split.py` (created here)

**Interfaces:**
- Produces: `kernel.HOME_KEY == "home"`; `worktrees.home_row(ctx) -> dict`; `worktrees.is_home(project) -> bool`; `worktrees.project_root(ctx, project)` returning `project["root_path"]` for every row, the home row included; `worktrees.project_checkouts(ctx, con)` yielding the home row first; `spud init` records `str(ctx.tool)` as project 1's `root_path`.
- Consumes: `spud.Ctx(home, how, json, tool=)` (Task 2), `spud.connect`, `spud.edit_reason` (existing).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_home_split.py`:

```python
"""SPD-097: the home is not a project.  It enters the path rule under the reserved key `home`; project spud is the tool
repository; `home:<glob>` names the home in a deliverable; `session show` names a session launched in the home."""

import json
import unittest

from helpers import EXIT_ERROR, EXIT_OK, Home, RepoMixin, SpudTestCase, load_spud_module

spud = load_spud_module()
SESSION = "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b"
AGENT = "a0123456789abcdef"


class SplitCase(RepoMixin, SpudTestCase):
    """A scratch home whose tool is a separate scratch main checkout, named before `spud init` records it as project spud's root."""

    def setUp(self):
        self.tool = self.make_tool()
        self.home = Home()
        self.addCleanup(self.home.cleanup)
        self.home.env["SPUD_TOOL_DIR"] = str(self.tool)
        self.home.init()

    def ctx(self):
        return spud.Ctx(self.home.path, "SPUD_HOME", False, tool=self.tool)


class HomeRowTest(SplitCase):
    def test_project_spud_is_rooted_at_the_tool_and_the_home_is_no_project(self):
        p = self.home.json("project", "show", "spud")["project"]
        self.assertEqual((p["root"], p["sessions"], p["landing"]), (str(self.tool), "always", "merge"))
        self.assertEqual(spud.home_row(self.ctx())["key"], "home")
        self.assertTrue(spud.is_home(spud.home_row(self.ctx())))
        con = spud.connect(self.ctx())
        try:
            checkouts = spud.project_checkouts(self.ctx(), con)
        finally:
            con.close()
        self.assertEqual([(p["key"], roots[0]) for p, roots in checkouts], [("home", str(self.home.path)), ("spud", str(self.tool))])

    def test_the_path_rule_for_spud_and_for_a_member(self):
        t = self.new_ticket("Split", status="active")
        m = self.new_member(t["key"], name="Russet", deliverable=["bin/**", "home:docs/x.md"])
        ctx = self.ctx()
        con = spud.connect(ctx)
        try:
            row = con.execute("SELECT * FROM members WHERE id = ?", (m["id"],)).fetchone()
            home, tool = self.home.path, self.tool

            def reason(path, agent_id=None, member=None):
                return spud.edit_reason(ctx, con, agent_id, member, str(path), str(home))[0]

            # Spud: his own set in the home, nothing else in the home, nothing in project spud
            self.assertIsNone(reason(home / "CLAUDE.md"))
            self.assertIsNone(reason(home / "docs" / "superpowers" / "specs" / "x.md"))
            self.assertIn("Law 1", reason(home / "docs" / "x.md"))
            self.assertIn("Law 5", reason(home / "ledger" / "tickets" / "SPD-001.md"))
            self.assertIn("project spud, where every path is a deliverable", reason(tool / "CLAUDE.md"))
            # the member: bare globs in the tool, home: globs in the home, generated roots nowhere
            self.assertIsNone(reason(tool / "bin" / "x.py", AGENT, row))
            self.assertIsNone(reason(home / "docs" / "x.md", AGENT, row))
            self.assertIn("Law 5", reason(home / "bin" / "x.py", AGENT, row))
            self.assertIn("Law 5", reason(tool / "docs" / "x.md", AGENT, row))
            self.assertIn("Law 5", reason(home / "ledger" / "teams" / "SPUD-001" / "Russet.md", AGENT, row))
        finally:
            con.close()

    def test_a_deliverable_may_name_the_home_and_must_name_an_active_project_otherwise(self):
        t = self.new_ticket("Globs")
        m = self.new_member(t["key"], deliverable=["home:docs/", "spud:bin/x.py", "tests/**"])
        self.assertEqual(m["deliverables"], ["home:docs/**", "spud:bin/x.py", "tests/**"])
        proc = self.home.run("member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", "--deliverable", "nope:x", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("no active project", proc.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home_split.py`
Expected: `test_project_spud_is_rooted...` fails (root is the home, `settings_file` is `settings.json`, `home_row` missing); `test_the_path_rule...` fails (project spud's root is the home, so `tool / "bin" / "x.py"` is outside every project); `test_a_deliverable...` fails ("deliverable 'home:docs/' names project 'home', which is no active project").

- [ ] **Step 3: Implement**

`bin/spudlib/core/kernel.py`, after `ALIVE = ("planned", "active")`:

```python
# SPD-097: the reserved key of Spud's home in a deliverable glob (home:<glob>) and in the path rule.  The home is not a
# project and never a projects row; project 1, `spud`, is the tool repository.
HOME_KEY = "home"
```

`bin/spudlib/hooks/worktrees.py`: replace `project_root` (lines 77 to 79) with:

```python
def home_row(ctx):
    """The home as the path rule and the session mode see it (SPD-097): a row-shaped dict under the reserved key, so the code
    that walks project rows treats the home as one more checkout, the one whose generated roots and Spud's own paths apply."""
    return {"id": 0, "key": kernel.HOME_KEY, "name": kernel.HOME_KEY, "root_path": str(ctx.home), "remote": None, "ticket_prefix": None,
            "team_prefix": None, "created_at": None, "default_branch": None, "landing": None, "sessions": "always", "installed": None,
            "archived_at": None}


def is_home(project):
    """True for home_row's dict; every projects row, project 1 (the tool repository) included, is a project."""
    return project["key"] == kernel.HOME_KEY


def project_root(ctx, project):
    """A project's main checkout, the row's root_path (SPD-097: project 1 is the tool repository, whose root is recorded like
    any other's; the home row's root_path is the home)."""
    return project["root_path"]
```

Replace `project_checkouts` (lines 119 to 122) with:

```python
def project_checkouts(ctx, con):
    """[(row, [root, *worktrees])] for the home and every active project, the home first: it has no worktrees, and where it
    and a project's root are one directory (the tool repository before `home move`) the home's rules win for paths under
    it, since map_into_checkouts keeps the first root of an identity (SPD-097)."""
    return [(home_row(ctx), [str(ctx.home)])] + [(p, [project_root(ctx, p), *checkout_worktrees(ctx, p)])
                                                  for p in con.execute("SELECT * FROM projects WHERE archived_at IS NULL ORDER BY id").fetchall()]
```

Line 213: `named = (hookio.GENERATED_ROOTS + (hookio.STATE_DIR,)) if project["id"] == 1 else (hookio.STATE_DIR,)` becomes `named = (hookio.GENERATED_ROOTS + (hookio.STATE_DIR,)) if is_home(project) else (hookio.STATE_DIR,)`. In that function's docstring, "A generated root of the home" stays true.

`bin/spudlib/hooks/pathrule.py`:
- line 152: `def path_reason(rel, member, ref, fold=False, project_key="spud", ticket_project_key="spud", home=True):` becomes `def path_reason(rel, member, ref, fold=False, project_key=kernel.HOME_KEY, ticket_project_key=None, home=True):` and add `from ..core import kernel` to the imports (`from . import hookio, worktrees` / `from ..core import kernel` / `from ..state import lookup, ops`). Its docstring's last sentence becomes: "In another project Spud has no own files (SPD-014): every path there is a deliverable, and a member's bare glob is relative to its ticket's project, a `<key>:<glob>` to that project's, a `home:<glob>` to the home (SPD-097)."
- line 233: `project["id"] == 1` becomes `worktrees.is_home(project)`.
- line 240: `if project["id"] == 1 and rel.split("/")[0].casefold() in hookio.GENERATED_ROOTS:` becomes `if worktrees.is_home(project) and rel.split("/")[0].casefold() in hookio.GENERATED_ROOTS:`.
- line 246: `home = project["id"] == 1` becomes `home = worktrees.is_home(project)`.
- line 253: `path_reason(rel, None, "Spud", worktrees.folds_case(root), project["key"], "spud", home)` becomes `path_reason(rel, None, "Spud", worktrees.folds_case(root), project["key"], None, home)`.

`bin/spudlib/state/ops.py`, `check_deliverable_projects`:

```python
def check_deliverable_projects(con, globs):
    """A qualified deliverable must name an active project, or the home by its reserved key (SPD-097)."""
    keys = {r["key"] for r in con.execute("SELECT key FROM projects WHERE archived_at IS NULL").fetchall()} | {kernel.HOME_KEY}
    for g in globs:
        key = glob_scope(g)[0]
        if key is not None and key not in keys:
            raise kernel.SpudError(kernel.EXIT_ERROR, "deliverable %r names project %r, which is no active project and not the home (spud project list)" % (g, key))
```

and in `normalize_deliverable`'s docstring: "An optional `<key>:` in front names the project whose checkout it is relative to (SPD-014), or `home:` Spud's home (SPD-097)."

`bin/spudlib/state/ledgerdb.py`, `sync_config_rows`: the insert of row 1 records `str(ctx.tool)` as `root_path` (was `str(ctx.home)`), the remote reads `homeconf.git_remote_url(ctx.tool)` (was `ctx.home`), the local `home_key` is renamed `tool_key`, and the docstring reads "Mirror naming.pool into name_pool and project spud's prefixes; project spud is inserted at the tool's root (SPD-097: the home is no project)". The `Home` of the suite sets `SPUD_TOOL_DIR` to the scratch home, so every existing test keeps project spud rooted where it was.

- [ ] **Step 4: Run the tests**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home_split.py`
Expected: OK (3 tests).
Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_package.py`
Expected: OK (`pathrule` gains the import `kernel`, which is on the hook path already; `home_row`, `is_home` are new names in one module).

### Task 7: The session mode, `session show`, the claim card, the unclaimed-session check, ticket creation in the home, the SubagentStart sentence

**Files:**
- Modify: `bin/spudlib/projects/sessions.py:113-132`, `:194-208`, `:233-234`, `:267-305`
- Modify: `bin/spudlib/hooks/sessionhooks.py:22`
- Modify: `bin/spudlib/state/actors.py:74`, `:81`
- Modify: `bin/spudlib/commands/ticketcmds.py:39`
- Modify: `bin/spudlib/hooks/recording.py:174-178`
- Modify: `tests/test_sessions.py:28`, `:88-89`; `tests/test_auto_claim.py:137-138`
- Test: `tests/test_home_split.py`

**Interfaces:**
- Produces: `sessions.session_mode(ctx, con, payload, env=None) -> (mode, project | None, claim | None)` returning `("spud", None, claim)` for a launch in the home; `session show` JSON `checkout: {"path", "kind": "home", "branch": None}` with `project: None` for a cwd in the home.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_home_split.py` (before the `if __name__` block):

```python
class SessionInTheHomeTest(SplitCase):
    def test_session_show_names_the_home_and_the_tool(self):
        shown = self.cli_json("session", "show", cwd=self.home.path, session=SESSION)
        self.assertEqual((shown["project"], shown["checkout"], shown["mode"]), (None, {"path": str(self.home.path), "kind": "home", "branch": None}, "spud"))
        text = self.cli("session", "show", cwd=self.home.path, session=SESSION).stdout
        self.assertIn("checkout  %s (home)" % self.home.path, text)
        self.assertIn("Spud's home, not a project", text)
        shown = self.cli_json("session", "show", cwd=self.tool, session=SESSION)
        self.assertEqual((shown["project"]["key"], shown["checkout"]["kind"], shown["checkout"]["branch"], shown["mode"]), ("spud", "root", "main", "spud"))

    def test_the_session_start_hook_gives_the_board_in_the_home_and_the_project_context_in_the_tool_once_it_claims(self):
        self.new_ticket("Board me", status="active")
        r = self.home.hook("SessionStart", {"hook_event_name": "SessionStart", "session_id": SESSION, "cwd": str(self.home.path), "source": "startup"})
        self.assertTrue(r.context.startswith("Ledger board ("), r.context)
        self.assertIn("SPD-001 active", r.context)
        self.cli("project", "edit", "spud", "--sessions", "claim", actor="spud")
        r = self.home.hook("SessionStart", {"hook_event_name": "SessionStart", "session_id": SESSION, "cwd": str(self.tool), "source": "startup"})
        self.assertIn("is Spud project `spud`", r.context)
        self.assertIn("This session is not Spud", r.context)
        proc = self.cli("ticket", "new", "--title", "Too early", actor="spud", cwd=self.tool, session=SESSION, check=False)
        self.assertEqual(proc.returncode, 3, proc)
        self.assertIn("claims", proc.stderr)
        self.assertEqual(self.cli_json("session", "claim", actor="spud", cwd=self.tool, session=SESSION)["claimed"], True)
        card = self.cli("session", "claim", actor="spud", cwd=self.tool, session=SESSION).stdout
        self.assertNotIn("ledger commit", card)
        self.assertEqual(self.cli_json("session", "show", cwd=self.tool, session=SESSION)["mode"], "spud")
        self.assertEqual(self.cli_json("session", "claim", actor="spud", cwd=self.home.path, session=SESSION)["claimed"], False)

    def test_a_ticket_created_in_the_home_is_project_spuds(self):
        t = self.cli_json("ticket", "new", "--title", "From the home", actor="spud", cwd=self.home.path)["ticket"]
        self.assertEqual((t["key"], t["project"]), ("SPD-001", "spud"))

    def test_the_subagent_start_context_names_the_tool_and_the_home_for_a_spud_ticket(self):
        self.home.env["CLAUDE_CODE_SESSION_ID"] = SESSION  # the row records the session that plans it, and the spawn is checked against it
        t = self.new_ticket("Context", status="active")
        m = self.new_member(t["key"], name="Russet", deliverable=["bin/**", "home:docs/x.md"])
        common = {"session_id": SESSION, "transcript_path": "/tmp/x.jsonl", "cwd": str(self.home.path), "permission_mode": "default"}
        allow = self.home.hook("PreToolUse", dict(common, hook_event_name="PreToolUse", tool_name="Agent", tool_use_id="toolu_01",
                                                  tool_input={"description": "SPUD-001/Russet (01, scout)", "subagent_type": "spudagent", "model": "haiku", "prompt": "Do the thing."}))
        self.assertEqual(allow.decision, "allow", allow)
        r = self.home.hook("SubagentStart", dict(common, hook_event_name="SubagentStart", agent_id=AGENT, agent_type="spudagent"))
        self.assertIn("Your ticket's project is `spud`; bare deliverables are relative to `%s`" % self.tool, r.context)
        self.assertIn("`home:<glob>` Spud's home (%s)" % self.home.path, r.context)
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home_split.py -k SessionInTheHomeTest`
Expected: `test_session_show...` fails (`checkout.kind` is `root`, `project` is `spud`); the SessionStart test fails on the tool's context (project 1 is skipped: `project["id"] != 1`); the ticket test passes already if the home wins the mapping, keep it; the SubagentStart test fails (no sentence for project 1). The SessionStart test also runs `project edit spud --sessions claim`, which Task 8 allows: until then it stops at that line with "the home's sessions stays always"; it passes in full at Task 8's Step 4.

- [ ] **Step 3: Implement**

`bin/spudlib/projects/sessions.py`, `session_mode` (lines 113 to 132):

```python
def session_mode(ctx, con, payload, env=None):
    """(mode, launch project row or None, claim row or None), once per hook call (design section 6.1).  The launch project
    is the project of CLAUDE_PROJECT_DIR, which stays at the launch directory after EnterWorktree (probe P5), else of the
    payload's cwd.  `outside`: it is in no active project and not the home, and a hook behaves as `spud` there, today's
    strict behaviour.  `spud`: launched in the home (SPD-097: the home is not a project, so the row is None), or the session
    holds a claim, or its project's sessions is `always`.  `plain` otherwise."""
    env = os.environ if env is None else env
    session = payload.get("session_id")
    claim = actors.claim_of(con, session) if isinstance(session, str) and session else None
    if con.execute("SELECT 1 FROM projects WHERE archived_at IS NULL AND sessions = 'claim' LIMIT 1").fetchone() is None:
        return "spud", None, claim  # no claim project: every session, wherever it is launched, is Spud's, and no path is mapped
    launch = env.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
    if not isinstance(launch, str) or not launch:
        return "outside", None, claim
    mapped = worktrees.project_of_path(ctx, con, launch)
    if mapped is None:
        return "outside", None, claim
    project = mapped[0]
    if worktrees.is_home(project):
        return "spud", None, claim
    if claim is not None or project["sessions"] == "always":
        return "spud", project, claim
    return "plain", project, None
```

`claim_card` (lines 194 to 208): delete the two lines that build the `ledger commit:` entry of `head` (`"ledger commit: python3.14 -I -S %s/bin/spud --as spud ledger commit --message '%s-nnn: <what>' (from a main checkout, never a worktree)" % (ctx.home, project["ticket_prefix"]),`), leaving `"rule: …"` followed by `"board (%s):" % project["key"]`.

`cmd_session_claim` lines 233 to 234: `if project["id"] == 1:` becomes `if worktrees.is_home(project):`.

`cmd_session_show`: replace the block from `project = checkout = None` through the end of the `if mapped is not None:` branch with:

```python
        project = checkout = None
        if mapped is not None and worktrees.is_home(mapped[0]):  # SPD-097: launched in the home, which is not a project
            checkout = {"path": str(ctx.home), "kind": "home", "branch": None}
        elif mapped is not None:
            p, checkout_root, _rel = mapped
            project = {"key": p["key"], "root": worktrees.project_root(ctx, p), "ticket_prefix": p["ticket_prefix"], "team_prefix": p["team_prefix"],
                       "landing": p["landing"], "sessions": p["sessions"]}
            branch = homeconf.run_git(checkout_root, "symbolic-ref", "--quiet", "--short", "HEAD", timeout=10) if os.path.isdir(os.path.join(checkout_root, ".git")) or os.path.isfile(os.path.join(checkout_root, ".git")) else None
            checkout = {"path": checkout_root, "kind": "root" if worktrees.file_identity(checkout_root) == worktrees.file_identity(project["root"]) else "worktree",
                        "branch": branch.stdout.strip() if branch is not None and branch.returncode == 0 else None}
```

and the text lines:

```python
    lines = ["home      %s" % ctx.home]
    if project:
        lines.append("project   %s: %s (%s-nnn tickets, %s-nnn teams; landing %s, sessions %s)" % (
            project["key"], project["root"], project["ticket_prefix"], project["team_prefix"], project["landing"], project["sessions"]))
        lines.append("checkout  %s (%s%s)" % (checkout["path"], checkout["kind"], ", branch %s" % checkout["branch"] if checkout["branch"] else ""))
    elif checkout:
        lines.append("project   none: %s is in Spud's home, not a project" % cwd)
        lines.append("checkout  %s (home)" % checkout["path"])
    else:
        lines.append("project   none: %s is in no registered project's checkout" % cwd)
```

`bin/spudlib/hooks/sessionhooks.py` line 22: `elif project is not None and project["id"] != 1:` becomes `elif project is not None:`.

`bin/spudlib/state/actors.py`: line 74's query becomes `"SELECT 1 FROM projects WHERE archived_at IS NULL AND sessions = 'claim' LIMIT 1"`; line 81 `mapped[0]["id"] == 1` becomes `worktrees.is_home(mapped[0])`; the docstring's "in a `claim` project other than the home" becomes "in a `claim` project (the home is none, SPD-097)".

`bin/spudlib/commands/ticketcmds.py` line 39: `project = mapped[0] if mapped else con.execute(...)` becomes `project = mapped[0] if mapped and not worktrees.is_home(mapped[0]) else con.execute("SELECT * FROM projects WHERE id = 1").fetchone()`, and the comment on line 34 reads `# SPD-014: the project of the working directory, else project spud (the home is none, SPD-097)`.

`bin/spudlib/hooks/recording.py` lines 174 to 178: drop the `if ticket["project_id"] != 1:` condition and dedent its body, so every member hears it:

```python
                project = con.execute("SELECT * FROM projects WHERE id = ?", (ticket["project_id"],)).fetchone()
                context += (" Your ticket's project is `%s`; bare deliverables are relative to `%s` or a worktree of it, `<key>:<glob>` names"
                            " another project's checkout and `home:<glob>` Spud's home (%s); that repository's CLAUDE.md and skills govern how"
                            " you build and verify." % (project["key"], worktrees.project_root(ctx, project), ctx.home))
```

Tests to update:
- `tests/test_sessions.py` line 28: remove `"ledger commit"` from the tuple of needles.
- `tests/test_sessions.py` lines 88 to 89: replace with `home = self.cli_json("session", "show", cwd=self.home.path, session=SESSION)` and `self.assertEqual((home["project"], home["checkout"]["kind"], home["mode"]), (None, "home", "spud"))`.
- `tests/test_auto_claim.py` line 138: remove the needle `"ledger commit: python3.14 -I -S %s/bin/spud" % home` from the tuple.
- Any test that asserts the SubagentStart context of a spud ticket does **not** contain "Your ticket's project" now asserts it does; find them with `grep -n "Your ticket's project" tests/*.py`.

- [ ] **Step 4: Run the tests**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home_split.py`
Expected: 6 of 7 OK; `test_the_session_start_hook…` fails at `project edit spud --sessions claim` until Task 8.
Run, each: `-p test_sessions.py`, `-p test_auto_claim.py`, `-p test_hooks_projects.py`, `-p test_hooks.py`, `-p test_tickets.py`
Expected: OK. In `test_hooks_projects.py` the row `("home: docs/x.md (spud:docs/x.md)", …)` and `plan_bad`'s default `"spud:docs/x.md"` still pass in this task because `spud:` names project spud, whose root is the scratch home there; Task 8 rewrites them to `home:`.

### Task 8: The reserved key, project `spud` as an ordinary project, install and doctor

**Files:**
- Modify: `bin/spudlib/projects/registry.py:57-59`, `:75-81`, `:107-118`, `:199-221`
- Modify: `bin/spudlib/projects/install.py:86-99`, `:212`, `:228-236`, `:257-265`, `:284-297`, `:317-325`
- Modify: `bin/spudlib/commands/doctor.py:113-160`
- Modify: `tests/test_projects.py:78`, `:132-139`; `tests/test_hooks_projects.py:216`, `:270`; `tests/probes/headless_projects.py:234`
- Test: `tests/test_home_split.py`, `tests/test_projects.py`

**Interfaces:**
- Produces: `project add --key home` refused with "reserved"; `project edit spud --sessions|--root` allowed, `--name|--ticket-prefix|--team-prefix` refused naming `spud.config.json`; `project install spud` allowed once project spud's root is not the home, refused with "root is the home" before; `doctor` JSON `report["tool"] = {"path", "launcher", "checkout"}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_home_split.py`:

```python
class ProjectSpudTest(SplitCase):
    def test_the_key_home_is_reserved(self):
        other = self.make_repo("other-")
        proc = self.add_project(other, key="home", ticket_prefix="HOM", team_prefix="HOMS", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("reserved", proc.stderr)
        proc = self.add_project(other, key="spud", ticket_prefix="HOM", team_prefix="HOMS", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("exists already", proc.stderr)
        proc = self.add_project(self.home.path, key="vault", ticket_prefix="HOM", team_prefix="HOMS", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("Spud's home, which is not a project", proc.stderr)

    def test_project_spud_changes_sessions_and_root_but_keeps_its_name_and_prefixes(self):
        self.assertEqual(self.cli_json("project", "edit", "spud", "--sessions", "claim", actor="spud")["changed"], ["sessions"])
        moved = self.make_repo("moved-")
        self.assertEqual(self.cli_json("project", "edit", "spud", "--root", moved, actor="spud")["project"]["root"], str(moved))
        for args, needle in ((["--name", "X"], "spud.config.json"), (["--ticket-prefix", "ZZ"], "spud.config.json")):
            with self.subTest(args=args):
                proc = self.cli("project", "edit", "spud", *args, actor="spud", check=False)
                self.assertEqual(proc.returncode, EXIT_ERROR, proc)
                self.assertIn(needle, proc.stderr)
        proc = self.cli("project", "remove", "spud", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("never removed", proc.stderr)

    def test_project_spud_installs_like_any_project(self):
        self.assertEqual(self.home.json("project", "show", "spud")["project"]["settings_file"], str(self.tool / ".claude" / "settings.local.json"))
        out = self.cli_json("project", "install", "spud", actor="spud")
        local = self.tool / ".claude" / "settings.local.json"
        self.assertIn(str(local), out["written"])
        data = json.loads(local.read_text(encoding="utf-8"))
        commands = [h["command"] for groups in data["hooks"].values() for g in groups for h in g["hooks"]]
        self.assertTrue(all(c.startswith("SPUD_HOME=%s " % self.home.path) and c.endswith(" --project spud") for c in commands), commands)
        self.assertIn(str(self.home.path), data["permissions"]["additionalDirectories"])
        self.assertTrue(self.cli_json("project", "show", "spud")["project"]["installed"])
        self.assertEqual(self.cli_json("project", "sync", "--all", actor="spud")["projects"], [{"project": "spud", "written": [], "restart": False}])
        report = self.cli_json("doctor")
        self.assertEqual(report["tool"], {"path": str(self.tool), "launcher": str(self.tool / "bin" / "spud"), "checkout": "main"})
        self.assertEqual([p["checks"] for p in report["projects"]], [["main checkout", "hooks", "ignored", "agent", "skill"]])
        self.assertEqual(self.cli_json("project", "uninstall", "spud", actor="spud")["changed"][:1], ["removed %s" % local])

    def test_project_spud_is_not_installed_while_its_root_is_the_home(self):
        home = Home()
        self.addCleanup(home.cleanup)
        home.init()  # SPUD_TOOL_DIR is the scratch home itself: the transition window's shape
        proc = home.run("project", "install", "spud", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("root is the home", proc.stderr)
        report = home.json("doctor")
        self.assertEqual(report["projects"][0]["checks"], ["root is the home (before home move)", "not installed"])
        self.assertEqual(report["tool"]["checkout"], "none")
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home_split.py -k ProjectSpudTest`
Expected: all four FAIL ("the key spud is the home's"; "the home's sessions stays always"; "the home's hooks come from `spud --as spud settings sync`"; no `tool` key in doctor's report).

- [ ] **Step 3: Implement**

`bin/spudlib/projects/registry.py`:
- `validate_project_root`: move the home check (lines 57 to 59, `ident = worktrees.file_identity(root)` and the `raise`) up to right after the `is_dir` check, before git is asked, so a home that is no git repository is refused as the home and not as "not a git repository"; its message becomes `"%s is Spud's home, which is not a project (SPD-097); register the tool repository or another checkout" % root`. The later use of `ident` (`chain = identity_chain(root)` and `if ident in identity_chain(other_root)`) keeps the variable.
- `check_project_key` (lines 75 to 81):

```python
def check_project_key(con, key):
    if not PROJECT_KEY_RE.fullmatch(key or ""):
        raise kernel.SpudError(kernel.EXIT_ERROR, "--key %r must be lower-case letters, digits and hyphens, starting with a letter, at most 32 characters" % key)
    if key == kernel.HOME_KEY:
        raise kernel.SpudError(kernel.EXIT_ERROR, "the key %s is reserved: it names Spud's home in a deliverable glob (home:<glob>), and the home is not a project (SPD-097)" % key)
    if con.execute("SELECT 1 FROM projects WHERE key = ?", (key,)).fetchone():
        raise kernel.SpudError(kernel.EXIT_ERROR, "project %s exists already" % key)
```

- `project_dict` (lines 107 to 118): `settings = os.path.join(root, ".claude", "settings.local.json")` and `"installed": settings_sync.settings_hold_hooks(ctx, settings, p["key"])`: every project's hooks live in its untracked local settings (SPD-097: the home's own `.claude/settings.json` belongs to no project).
- `cmd_project_edit` (lines 199 to 221): rename the local `home` to `tool_project = p["id"] == 1`; the `--name` refusal reads `"project spud's name comes from spud.config.json (identity.name)"`; delete the `--sessions` refusal (`if home and args.sessions != "always": …`) and the `--root` refusal (`if home: raise … "the home's root is the home itself (SPUD_HOME)"`), so both fall through to the ordinary code; the prefixes refusal reads `"project spud's prefixes come from spud.config.json (`spud config sync`)"`.
- The comment block at lines 18 to 20: "the home is project 1" becomes "project 1 is the tool repository, `spud`; the home is no project (SPD-097)".

`bin/spudlib/projects/install.py`:
- In `install_project`, after `root = Path(worktrees.project_root(ctx, p))` and its `is_dir` check, add:

```python
    if worktrees.file_identity(root) is not None and worktrees.file_identity(root) == worktrees.file_identity(ctx.home):
        raise kernel.SpudError(kernel.EXIT_ERROR, "project %s's root is the home %s; the home's hooks are `spud --as spud settings sync`'s until"
                               " `spud --as spud home move` separates the two (SPD-097)" % (p["key"], root))
```

- Line 212: `"SELECT count(*) FROM projects WHERE id NOT IN (1, ?) AND installed IS NOT NULL"` becomes `"SELECT count(*) FROM projects WHERE id != ? AND installed IS NOT NULL"`.
- `cmd_project_install`: delete the `if p["id"] == 1: raise …` (lines 235 to 236).
- `cmd_project_uninstall`: delete the `if p["id"] == 1: raise …` (lines 264 to 265).
- `cmd_project_sync`: line 293 `if p["id"] == 1 or not p["installed"]:` becomes `if not p["installed"]:`; line 297's query becomes `"SELECT * FROM projects WHERE archived_at IS NULL AND installed IS NOT NULL ORDER BY id"`.
- `cmd_project_remove` line 325: the message becomes `"project spud is the tool repository, project 1, and is never removed"`.

`bin/spudlib/commands/doctor.py`, `doctor_projects`:
- The docstring: "each active project, its root a main checkout (or the home itself, before `home move`), …, the user-scope agent matching the tool's".
- Line 120's query becomes `"SELECT * FROM projects WHERE archived_at IS NULL ORDER BY id"`.
- Replace the `if not os.path.isdir(root): … else: try: … except …` block (lines 126 to 137) with:

```python
        if not os.path.isdir(root):
            bad.append("root %s is not a directory" % root)
        elif worktrees.file_identity(root) == worktrees.file_identity(ctx.home):
            checks.append("root is the home (before home move)")  # SPD-097: project spud during the transition window
        else:
            try:
                proc = homeconf.run_git(root, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir", timeout=10)
                lines = proc.stdout.strip().split("\n") if proc.returncode == 0 else []
                if len(lines) == 2 and worktrees.file_identity(lines[0]) == worktrees.file_identity(root) and worktrees.file_identity(lines[1]) == worktrees.file_identity(os.path.join(root, ".git")):
                    checks.append("main checkout")
                else:
                    bad.append("root %s is not the main checkout of a git repository" % root)
            except kernel.SpudError as e:
                bad.append(e.message)
```

In `cmd_doctor`, after the `"spud_home"` entry of `report`, add `"tool": {"path": str(ctx.tool), "launcher": str(ctx.launcher), "checkout": homeconf.tool_checkout_kind(ctx.tool)},` and, right after the `SPUD_HOME` text line, `"tool        %s (bin/spud; %s)" % (ctx.tool, {"main": "main checkout", "worktree": "linked worktree", "none": "no git checkout"}[report["tool"]["checkout"]]),`; when the kind is `worktree`, `notes.append("the running bin/spud is in a linked worktree: hook lines written from here name it")` (place it where `notes` exists, before `report["notes"] = notes`).

Tests to update:
- `tests/test_projects.py` line 78: `("spud", "home's")` becomes `("spud", "exists already")`, and add `("home", "reserved")` to the tuple.
- `tests/test_projects.py` lines 132 to 139: replace the whole test with

```python
    def test_project_spud_keeps_its_name_and_prefixes(self):
        for args, needle in ((["--name", "X"], "spud.config.json"), (["--ticket-prefix", "ZZ"], "spud.config.json")):
            with self.subTest(args=args):
                proc = self.cli("project", "edit", "spud", *args, actor="spud", check=False)
                self.assertEqual(proc.returncode, EXIT_ERROR, proc)
                self.assertIn(needle, proc.stderr)
        self.assertEqual(self.cli_json("project", "edit", "spud", "--default-branch", "trunk", actor="spud")["changed"], ["default_branch"])
        self.assertEqual(self.cli_json("project", "edit", "spud", "--sessions", "claim", actor="spud")["changed"], ["sessions"])
```

- `tests/test_hooks_projects.py` line 216: `deliverables=("src/**", "spud:docs/x.md")` becomes `deliverables=("src/**", "home:docs/x.md")`; line 250's docstring `spud:docs/x.md` becomes `home:docs/x.md`; line 270's label `"home: docs/x.md (spud:docs/x.md)"` becomes `"home: docs/x.md (home:docs/x.md)"`. The expectations in that row stay.
- `tests/test_projects.py` lines 202 to 204: `"spud:docs/x.md"` becomes `"home:docs/x.md"` in both the arguments and the expected list; `("spud:/abs", "not absolute")` and `("spud:../x", "`..`")` stay (project spud exists).
- `tests/probes/headless_projects.py` line 234: `--deliverable 'spud:docs/x.md'` becomes `--deliverable 'home:docs/x.md'`.

- [ ] **Step 4: Run the tests**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home_split.py`
Expected: OK (11 tests).
Run, each: `-p test_projects.py`, `-p test_install.py`, `-p test_hooks_projects.py`, `-p test_backup.py`
Expected: OK. If `test_install.py` asserted that `project install spud` is refused with "settings sync", it now expects the "root is the home" refusal (the scratch home is its own tool there).

### Task 9: `ledger commit` removed, with every mention

**Files:**
- Modify: `bin/spudlib/commands/publish.py:1-11`, `:124-196`
- Modify: `bin/spudlib/cli/cliparser.py:158-164`
- Modify: `bin/spudlib/hooks/hookio.py:36-41`
- Modify: `bin/spudlib/shell/bash_rule.py:211`
- Delete: `tests/test_ledger_commit.py`
- Modify: `tests/probes/headless_projects.py:241`, `:287`, `:290`; `tests/test_acceptance.py:39` (comment)

- [ ] **Step 1: Remove the command**

In `bin/spudlib/commands/publish.py`: delete everything from `SUBJECT_TICKET_KEY = re.compile(...)` (line 124) to the end of `cmd_ledger_commit`; the docstring becomes `"""commands/publish: render.  Moved from bin/spud_ledger.py (SPD-065); ledger commit removed by SPD-097: the home is no git checkout."""`; drop the imports that only it used (`os`, `re`, `homeconf`, `lazy`, `hookio`, `worktrees`), leaving

```python
from pathlib import Path

from ..core import kernel, markdown
from ..imports import accept
from ..render import notefiles
from ..state import actors, ledgerdb
```

In `bin/spudlib/cli/cliparser.py`: delete the `ledger` parser block (lines 158 to 164, from `p = sub.add_parser("ledger", …` to `q.set_defaults(func=publish.cmd_ledger_commit)`).

In `bin/spudlib/hooks/hookio.py`: remove `"ledger"` from `SPUD_COMMANDS` and `("ledger", "commit")` from `SPUD_ONLY_SUBCOMMANDS`.

In `bin/spudlib/shell/bash_rule.py` line 211, the Law 6 wording `"… and schedule installs the ledger's daily backup on this Mac);"` becomes `"… schedule installs the ledger's daily backup and its render watcher on this Mac, and home move moves the home);"` (Tasks 15 and 19 add those commands to the tables; the wording is written once here).

Delete `tests/test_ledger_commit.py`.

In `tests/probes/headless_projects.py`: the three steps that run `ledger commit` (lines 241, 287, 290) become `Bash: %(cli)s --as spud render` with the same step numbers, and any step text that told the model to "commit the ledger" now says "render the ledger". In `tests/test_acceptance.py` line 39 the comment "The last ledger commit on main before bin/spud existed" is history and stays.

- [ ] **Step 2: Check nothing names it**

Run: `grep -rn "ledger commit\|ledger_commit\|cmd_ledger" bin tests .claude/agents`
Expected: no output.

- [ ] **Step 3: Run the tests**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_package.py`
Expected: OK (`publish` still reachable through `cliparser`; no unused alias).
Run, each: `-p test_render.py`, `-p test_hooks.py`, `-p test_init.py`
Expected: OK. A test in `test_hooks.py` that asserted the Law 6 wording word for word now expects the new sentence of `bash_rule.py` line 211.

### Task 10: The tool-side text: `spudagent.md`, the help texts

**Files:**
- Modify: `.claude/agents/spudagent.md:16-17`, `:22-23`
- Modify: `bin/spudlib/cli/helptexts.py:22-26`
- Modify: `bin/spudlib/cli/cliparser.py:106`, `:193`, `:231`

- [ ] **Step 1: Rewrite the four paragraphs of `spudagent.md`**

Line 16 becomes:

```
1. Read `spud.config.json` (limits, personas, name pool) and `CLAUDE.md` (Spud's laws, which bind you too) in Spud's home, the directory `spud session show` prints as `home`. Also read your ticket's project's own `CLAUDE.md`: the `SubagentStart` context names the project, and `spud` (the tool repository) is one like any other.
```

Line 17 becomes:

```
2. `spud --as <id> member show SPUD-nnn/<YourName>`: your brief, your deliverable globs, your parent, your limits. The rendered copy at `ledger/teams/SPUD-nnn/<YourName>.md` in the home may lag behind the database; the command never does.
```

Line 22 becomes:

```
- Write only to the deliverable paths your parent planned. A bare glob is relative to your ticket's project checkout (the root or a worktree of it), which the `SubagentStart` context names; a glob written `<key>:<glob>` names another project's checkout, and `home:<glob>` names Spud's home, as `home:docs/…` does. The edit hook refuses every other path in any registered project and in the home, and everything under the home's `ledger/` and `reports/` is refused for everyone: those files are rendered from the database. Never touch a ticket, a sibling, `spud.config.json`, Spud's `CLAUDE.md`, or the home's `.claude/` unless your deliverables name it.
```

Line 23 becomes:

```
- In every project, `spud` included, that repository's `CLAUDE.md` and skills govern how you build and verify (setup scripts, test tiers, style); Spud's laws still govern the ledger, delegation and who writes what.
```

- [ ] **Step 2: The help texts**

In `bin/spudlib/cli/helptexts.py` `EPILOG`, the deliverable globs paragraph becomes:

```
deliverable globs (--deliverable): repository-relative, no leading slash and no `..`;
       `*` and `?` match inside one path segment, `**` crosses segments, a trailing `/`
       means everything under that directory, `<key>:` in front names another
       project's checkout and `home:` Spud's home (SPD-097).  Every other character is
       literal, brackets included: write a Next.js segment plainly,
       admin/src/app/accounts/[email]/** (SPD-086).
```

In `bin/spudlib/cli/cliparser.py`: line 106's help becomes `"lower-case key, [a-z][a-z0-9-]{0,31}; home is reserved"`; line 193's becomes `"project key (default: the project of the working directory, else spud; the home is no project)"`; line 231's becomes `"a path glob the member may write (repeatable): bare for the ticket's project, <key>:<glob> for another, home:<glob> for the home"`.

- [ ] **Step 3: Run the tests that read these texts**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_init.py`
Expected: OK. Run `grep -n "spud:docs\|ledger root" .claude/agents/spudagent.md`: no output.

### Task 11: Phase 2 suite

- [ ] **Step 1: Run the whole suite in the background and the bytecode check**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests`
Expected: OK. Failures of these three shapes are the residue of this phase and are fixed in the test, not the code: an assertion that `session show` in the home has `project.key == "spud"` (now `project is None`, `checkout.kind == "home"`); an assertion that a `spud:` glob writes into the home (now `home:`); an assertion that a spud ticket's SubagentStart context lacks "Your ticket's project". Then `find bin tests -name '*.pyc' -o -name __pycache__` prints nothing.

- [ ] **Step 2: Log the phase**

Run: `spud --as <id> member log "Phase 2 done: the home is a synthetic row keyed home, project spud is the tool, home: globs, the reserved key, ledger commit removed, spudagent.md and help rewritten; suite green"`

---

# Phase 3: the vault follows the database

Spec section 4. After this phase `spud render` is one lockable pass that writes the database only when something changed, `spud render --watch` repeats it whenever the event log moves, `local.spud.render` keeps the watcher alive, and `doctor` and `board --brief` say when it is down.

### Task 12: `render_pass`, the render lock, no write when nothing changed, a conflict logged once

**Files:**
- Modify: `bin/spudlib/commands/publish.py` (whole file: `render_lock`, `conflict_logged`, `render_pass`, `cmd_render`)
- Modify: `tests/test_render.py:222`
- Test: `tests/test_render.py`, `tests/test_watch.py` (created here)

**Interfaces:**
- Produces: `publish.RENDER_LOCK == "render.lock"`; `publish.render_lock(ctx)` (a context manager); `publish.conflict_logged(con, rel, on_disk) -> bool`; `publish.render_pass(ctx, con, out_root=None, check_only=False) -> dict` with keys `out`, `written`, `unchanged`, `conflicts`, `restyled`, `new_conflicts` (lists of relative paths) and `through` (int); `publish.cmd_render(ctx, args)` unchanged in its output; a conflict event's `data` gains `sha256`, the on-disk hash it refused.

- [ ] **Step 1: Write the failing tests**

In `tests/test_render.py` `test_render_records_hashes_and_is_idempotent`, the last line `self.assertEqual(self.home.scalar("SELECT count(*) FROM events WHERE kind = 'render'"), 2)` becomes `self.assertEqual(self.home.scalar("SELECT count(*) FROM events WHERE kind = 'render'"), 1)  # SPD-097: the second pass changed nothing and wrote nothing`. Add to `RenderConflictTest`:

```python
    def test_a_no_change_render_writes_nothing(self):
        self.new_ticket("Quiet")
        self.home.json("render")
        snapshot = lambda: (self.home.scalar("SELECT max(id) FROM events"), self.home.rows("SELECT path, sha256, rendered_at, through_event_id FROM renders ORDER BY path"))
        before = snapshot()
        again = self.home.json("render")
        self.assertEqual((again["written"], again["conflicts"], again["restyled"]), ([], [], []))
        self.assertEqual(snapshot(), before)

    def test_a_conflict_is_logged_once_per_path_and_on_disk_hash(self):
        t = self.new_ticket("Edited", brief="Original brief.")
        self.home.json("render")
        path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        path.write_text(path.read_text(encoding="utf-8").replace("Original brief.", "Edited once."), encoding="utf-8")
        self.home.json("ticket", "edit", t["key"], "--title", "Renamed", actor="spud")
        for _ in range(3):
            self.assertEqual(self.home.run("render", check=False).returncode, EXIT_CONFLICT)
        conflicts = lambda: [json.loads(r["data"]) for r in self.home.rows("SELECT data FROM events WHERE kind = 'render' AND json_extract(data, '$.conflict') = 1 ORDER BY id")]
        self.assertEqual([c["path"] for c in conflicts()], ["ledger/tickets/SPD-001.md"])
        self.assertEqual(conflicts()[0]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        path.write_text(path.read_text(encoding="utf-8").replace("Edited once.", "Edited twice."), encoding="utf-8")
        self.assertEqual(self.home.run("render", check=False).returncode, EXIT_CONFLICT)
        self.assertEqual([c["sha256"] for c in conflicts()][1], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(len(conflicts()), 2)
```

Create `tests/test_watch.py` with the lock test (the watcher tests join it in Task 13):

```python
"""SPD-097: the vault follows the database.  The render lock, the watcher (`render --watch`), a conflict logged once, and
what doctor and board say about a watcher that is down."""

import subprocess
import sys
import time
import unittest

from helpers import EXIT_ERROR, EXIT_OK, SPUD, LaunchdMixin, SpudTestCase, load_spud_module

spud = load_spud_module()


class WatchCase(SpudTestCase):
    def ctx(self):
        return spud.Ctx(self.home.path, "SPUD_HOME", False, tool=self.home.path)

    def watcher(self, *extra):
        """`spud --as spud render --watch` as a subprocess with a fast tick, stopped and reaped by the test."""
        proc = subprocess.Popen([sys.executable, "-I", "-S", str(SPUD), "--as", "spud", "render", "--watch", "--interval", "0.05", *extra],
                                env=self.home.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, proc)
        return proc

    def stop(self, proc):
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def render_events(self):
        return self.home.scalar("SELECT count(*) FROM events WHERE kind = 'render'")

    def conflict_events(self):
        return self.home.rows("SELECT data FROM events WHERE kind = 'render' AND json_extract(data, '$.conflict') = 1 ORDER BY id")


class RenderLockTest(WatchCase):
    def test_a_manual_render_waits_for_the_lock(self):
        self.new_ticket("Locked")
        with spud.render_lock(self.ctx()):
            proc = subprocess.Popen([sys.executable, "-I", "-S", str(SPUD), "--as", "spud", "render"], env=self.home.env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(0.5)
            self.assertIsNone(proc.poll(), "the render must wait while the lock is held")
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, EXIT_OK, err)
        self.assertIn("written", out)
        self.assertTrue((self.home.path / ".spud" / "render.lock").is_file())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_render.py -k "no_change or logged_once or idempotent"`
Expected: three FAIL (a second render event is written; a conflict is logged on every pass; `sha256` missing).
Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_watch.py`
Expected: ERROR, `AttributeError: … no attribute 'render_lock'`.

- [ ] **Step 3: Implement**

Rewrite `bin/spudlib/commands/publish.py` as:

```python
"""commands/publish: render, one pass at a time.  Moved from bin/spud_ledger.py (SPD-065); since SPD-097 the pass the watcher
repeats, under a lock, writing the database only when it wrote a file, restyled one or found a conflict it had not logged."""

import contextlib
import fcntl
import os
from pathlib import Path

from ..core import kernel, markdown
from ..imports import accept
from ..render import notefiles
from ..state import actors, ledgerdb

RENDER_LOCK = "render.lock"  # <home>/.spud/render.lock: one render at a time (SPD-097); a manual render waits for the watcher's pass


@contextlib.contextmanager
def render_lock(ctx):
    """Exclusive flock on <home>/.spud/render.lock for the pass; waits for a pass in progress.  A no-op before `spud init`
    (no state directory yet)."""
    state = ctx.home / ".spud"
    if not state.is_dir():
        yield
        return
    fd = os.open(str(state / RENDER_LOCK), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def conflict_logged(con, rel, on_disk):
    """Whether a render event already refused this path at this on-disk hash: a conflict is logged once, not on every pass."""
    return con.execute("SELECT 1 FROM events WHERE kind = 'render' AND json_extract(data, '$.conflict') = 1 AND json_extract(data, '$.path') = ?"
                       " AND json_extract(data, '$.sha256') = ? LIMIT 1", (rel, on_disk)).fetchone() is not None


def render_pass(ctx, con, out_root=None, check_only=False):
    """One pass over every generated file.  Into the home (out_root None): records the hashes, writes each new conflict once
    per path and on-disk hash, and touches the database only when it wrote a file, re-rendered a style-only rewrite,
    found a new conflict or has a hash to record (SPD-097: a render that changes nothing writes nothing).  Into --out:
    checks and records nothing.  check_only: what the pass would write and refuse, writing nothing at all (home move's
    preconditions, doctor).  Returns written, unchanged, conflicts, restyled and new_conflicts as relative paths, and
    `through`, the highest event id the pass rendered."""
    into_home = out_root is None
    root = ctx.home if into_home else out_root
    targets = notefiles.render_targets(con, ctx.pricing)
    records = {r["path"]: r["sha256"] for r in con.execute("SELECT path, sha256 FROM renders").fetchall()} if into_home else {}
    written, unchanged, conflicts, restyled, unreadable, to_record, new_conflicts = [], [], [], [], {}, [], []
    for rel, content in targets:
        path = root / rel
        data = content.encode("utf-8")
        new_hash = kernel.sha256_bytes(data)
        raw = path.read_bytes() if path.is_file() else None
        on_disk = kernel.sha256_bytes(raw) if raw is not None else None
        if into_home:
            recorded = records.get(rel)
            if recorded is not None and on_disk is not None and on_disk not in (recorded, new_hash):
                # Neither the last render nor the new one, byte for byte.  A note may still be one of them with its
                # frontmatter restyled (Obsidian rewrites the YAML of the notes it has open): that is no hand edit, so
                # the render goes over it and the event keeps what it replaced.  A report has no frontmatter: any
                # change to it is the conflict.
                style_only, why = False, None
                if content.startswith("---\n"):
                    last = con.execute("SELECT content FROM renders WHERE path = ?", (rel,)).fetchone()["content"]
                    style_only, why = markdown.restyled_render(raw, (last, content))
                if not style_only:
                    conflicts.append(rel)
                    if why:
                        unreadable[rel] = why
                    if not conflict_logged(con, rel, on_disk):
                        new_conflicts.append((rel, on_disk))
                    continue
                restyled.append((rel, raw.decode("utf-8")))
            if on_disk == new_hash:
                unchanged.append(rel)
                if recorded != new_hash:
                    to_record.append((rel, new_hash, content))
                continue
            written.append(rel)
            if check_only:
                continue
            kernel.write_whole(path, content)
            to_record.append((rel, new_hash, content))
        elif on_disk == new_hash:
            unchanged.append(rel)
        else:
            written.append(rel)
            if not check_only:
                kernel.write_whole(path, content)
    through = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
    if into_home and not check_only and (to_record or restyled or new_conflicts):
        at = kernel.now()
        with ledgerdb.write_txn(con):
            through = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
            for rel, new_hash, content in to_record:
                con.execute(
                    "INSERT INTO renders (path, sha256, content, rendered_at, through_event_id) VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(path) DO UPDATE SET sha256 = excluded.sha256, content = excluded.content,"
                    " rendered_at = excluded.rendered_at, through_event_id = excluded.through_event_id",
                    (rel, new_hash, content, at, through),
                )
            for rel, text in restyled:
                ledgerdb.write_event(con, at, "spud", "render", "re-rendered %s over a style-only frontmatter rewrite" % rel,
                                     data={"style_only": True, "path": rel, "text": text})
            for rel, on_disk in new_conflicts:
                conflict = {"conflict": True, "path": rel, "sha256": on_disk}
                if rel in unreadable:
                    conflict["unreadable"] = unreadable[rel]
                ledgerdb.write_event(con, at, "spud", "render", "refused to overwrite hand-edited %s" % rel, data=conflict)
            if written or restyled or new_conflicts:
                ledgerdb.write_event(con, at, "spud", "render", "rendered %d files, %d unchanged, %d conflicts" % (len(written), len(unchanged), len(conflicts)),
                                     data={"written": written, "unchanged": len(unchanged), "conflicts": conflicts,
                                           "restyled": [rel for rel, _ in restyled], "through_event_id": through})
    return {"out": str(root), "written": written, "unchanged": unchanged, "conflicts": conflicts, "restyled": [rel for rel, _ in restyled],
            "new_conflicts": [rel for rel, _ in new_conflicts], "through": through}


def cmd_render(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        out_root = Path(args.out).expanduser().resolve() if args.out else None
        discarded = []
        with render_lock(ctx) if out_root is None else contextlib.nullcontext():
            if args.discard:
                if out_root is not None:
                    raise kernel.SpudError(kernel.EXIT_USAGE, "--discard applies to the files under SPUD_HOME, not to --out")
                actor = actors.resolve_actor(con, args.actor)
                actors.require_spud(con, actor, "discarding a hand edit")
                kind, rel = accept.classify_path(ctx, args.discard)
                path = ctx.home / rel
                content = dict(notefiles.render_targets(con, ctx.pricing)).get(rel)
                if content is None:
                    raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not a file this ledger generates" % rel)
                if not path.is_file():
                    raise kernel.SpudError(kernel.EXIT_ERROR, "no such file: %s" % path)
                old_text = path.read_text(encoding="utf-8")
                kernel.write_whole(path, content)
                discarded.append(rel)
                at = kernel.now()
                with ledgerdb.write_txn(con):
                    con.execute(
                        "INSERT INTO renders (path, sha256, content, rendered_at, through_event_id) VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(id), 0) FROM events))"
                        " ON CONFLICT(path) DO UPDATE SET sha256 = excluded.sha256, content = excluded.content, rendered_at = excluded.rendered_at, through_event_id = excluded.through_event_id",
                        (rel, kernel.sha256_bytes(content.encode("utf-8")), content, at),
                    )
                    ledgerdb.write_event(con, at, actor.label, "render", "discarded the hand edit of %s" % rel, data={"discarded": True, "path": rel, "text": old_text})
            result = render_pass(ctx, con, out_root)
    finally:
        con.close()
    written, unchanged, conflicts, restyled = result["written"], result["unchanged"], result["conflicts"], result["restyled"]
    data = {"out": result["out"], "written": written, "unchanged": unchanged, "conflicts": conflicts, "discarded": discarded, "restyled": restyled}
    lines = ["rendered into %s: %d written, %d unchanged%s%s" % (
        result["out"], len(written), len(unchanged),
        (", %d hand edit discarded" % len(discarded)) if discarded else "",
        (", %d style-only rewrite re-rendered" % len(restyled)) if restyled else "",
    )]
    lines += ["  discarded the hand edit of %s" % rel for rel in discarded]
    lines += ["  re-rendered %s over a style-only frontmatter rewrite" % rel for rel in restyled]
    lines += ["  wrote %s" % rel for rel in written]
    if conflicts:
        raise kernel.SpudError(
            kernel.EXIT_CONFLICT,
            "hand-edited, not overwritten (accept with `spud import --file <path>`): " + ", ".join(conflicts),
            data=data,
        )
    return kernel.Result(data, "\n".join(lines))
```

- [ ] **Step 4: Run the tests**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_render.py`
Expected: OK. `test_a_ticket_restyled_by_obsidian_is_rendered_over_and_named` still finds its summary event: a restyle is a change.
Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_watch.py`
Expected: OK (1 test).
Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_import.py`
Expected: OK (`import --file` reads the `renders` row, which the pass still records).

### Task 13: `render --watch`, the watch lock, whether a watcher is alive

**Files:**
- Create: `bin/spudlib/commands/renderwatch.py`
- Modify: `bin/spudlib/cli/cliparser.py:8-19` (import), `:176-179` (the `render` parser)
- Test: `tests/test_watch.py`

**Interfaces:**
- Produces: `renderwatch.WATCH_LOCK == "watch.lock"`, `renderwatch.WATCH_INTERVAL == 2.0`; `renderwatch.render_entry(ctx, args)` (the parser's `render`); `renderwatch.cmd_render_watch(ctx, args)`; `renderwatch.watcher_alive(ctx) -> bool`; `renderwatch.watcher_installed() -> bool` (Task 14 supplies `schedule.agent_plist_path`; until then the function body below reads `schedule.schedule_plist_path().with_name("local.spud.render.plist")`, replaced in Task 14); `renderwatch.latest_event_id(con) -> int`; `renderwatch.log_line(text)`; `renderwatch.truncate_log()`. The `render` parser gains `--watch`, `--interval SECONDS`, `--ticks N`.
- Consumes: `publish.render_pass`, `publish.render_lock` (Task 12).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_watch.py`:

```python
class WatcherTest(WatchCase):
    def test_the_watcher_renders_once_after_an_event_and_not_after_its_own(self):
        t = self.new_ticket("Watched")
        path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        proc = self.watcher()
        self.assertTrue(self.wait_for(path.is_file), "the first pass renders the ticket")
        self.assertTrue(self.wait_for(lambda: self.render_events() == 1))
        time.sleep(0.4)  # eight ticks with nothing new
        self.assertEqual(self.render_events(), 1)
        self.home.json("ticket", "edit", t["key"], "--title", "Renamed", actor="spud")
        self.assertTrue(self.wait_for(lambda: "Renamed" in path.read_text(encoding="utf-8")))
        self.assertTrue(self.wait_for(lambda: self.render_events() == 2))
        time.sleep(0.4)
        self.assertEqual(self.render_events(), 2)
        proc.terminate()
        out, err = proc.communicate(timeout=10)
        self.assertEqual((proc.returncode, err), (0, ""), (out, err))
        self.assertIn("stopped after 2 pass(es): signal 15", out)
        self.assertEqual(out.count(" rendered "), 2)

    def test_ticks_ends_the_watcher_and_a_second_watcher_is_refused(self):
        self.new_ticket("Ticks")
        first = self.watcher()
        self.assertTrue(self.wait_for(lambda: spud.watcher_alive(self.ctx())))
        proc = self.home.run("render", "--watch", "--interval", "0.05", "--ticks", "1", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("already running", proc.stderr)
        first.terminate()
        first.communicate(timeout=10)
        self.assertFalse(spud.watcher_alive(self.ctx()))
        proc = self.home.run("render", "--watch", "--interval", "0.05", "--ticks", "2", actor="spud")
        self.assertIn("stopped after 0 pass(es): 2 ticks", proc.stdout)  # the first watcher rendered the ticket already
        self.assertFalse(spud.watcher_alive(self.ctx()))
        self.assertFalse(spud.watcher_alive(spud.Ctx(self.home.path / "nowhere", "SPUD_HOME", False, tool=self.home.path)))

    def test_watch_takes_no_out_and_no_discard(self):
        for extra in (("--out", str(self.home.path / "x")), ("--discard", "ledger/tickets/SPD-001.md")):
            with self.subTest(extra=extra):
                proc = self.home.run("render", "--watch", *extra, actor="spud", check=False)
                self.assertEqual(proc.returncode, 2, proc)


class ConflictOnceTest(WatchCase):
    def test_the_watcher_logs_a_conflict_once_and_keeps_rendering_the_rest(self):
        self.new_ticket("Edited", brief="Original brief.")
        self.home.json("render")
        path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        path.write_text(path.read_text(encoding="utf-8").replace("Original brief.", "By hand."), encoding="utf-8")
        proc = self.watcher()
        other = self.new_ticket("Second")
        second = self.home.path / "ledger" / "tickets" / "SPD-002.md"
        self.assertTrue(self.wait_for(second.is_file))
        self.home.json("ticket", "edit", other["key"], "--title", "Renamed", actor="spud")
        self.assertTrue(self.wait_for(lambda: "Renamed" in second.read_text(encoding="utf-8")))
        time.sleep(0.3)
        self.assertEqual(len(self.conflict_events()), 1)
        self.assertIn("By hand.", path.read_text(encoding="utf-8"))
        proc.terminate()
        out, _ = proc.communicate(timeout=10)
        self.assertEqual(out.count("conflicts 1"), 2)
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_watch.py`
Expected: the new tests FAIL: `render --watch` is "unrecognized arguments" (exit 2), and `spud.watcher_alive` does not exist.

- [ ] **Step 3: Implement**

Create `bin/spudlib/commands/renderwatch.py`:

```python
"""commands/renderwatch: render --watch, the loop LaunchAgent local.spud.render runs, and whether a watcher is alive (SPD-097)."""

import contextlib
import fcntl
import os
import signal
import sqlite3
import stat
import sys
import time

from . import publish, schedule
from ..core import kernel
from ..state import ledgerdb

WATCH_LOCK = "watch.lock"  # <home>/.spud/watch.lock, held for the watcher's life: doctor and board ask it whether a watcher is alive
WATCH_INTERVAL = 2.0       # seconds between two reads of the event log


def render_entry(ctx, args):
    """The parser's `render`: the watcher with --watch, else one pass."""
    return cmd_render_watch(ctx, args) if args.watch else publish.cmd_render(ctx, args)


def watch_lock_path(ctx):
    return ctx.home / ".spud" / WATCH_LOCK


def watcher_alive(ctx):
    """True when a watcher holds the watch lock: taken non-blocking and released at once.  False with no lock file (no
    watcher ever ran for this home) and before `spud init`."""
    path = watch_lock_path(ctx)
    if not path.is_file():
        return False
    fd = os.open(str(path), os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def watcher_installed():
    """Whether the render watcher's LaunchAgent plist is installed: `down` means installed and not alive."""
    return schedule.agent_plist_path("render").is_file()


def latest_event_id(con):
    """The highest event id whose kind is not `render`: what the vault must have caught up with.  A render event never
    triggers a pass, so the watcher does not chase its own writes."""
    return con.execute("SELECT COALESCE(MAX(id), 0) FROM events WHERE kind != 'render'").fetchone()[0]


def log_line(text):
    """One timestamped line on stdout, flushed: launchd redirects it into <home>/.spud/logs/render.log."""
    sys.stdout.write("%s %s\n" % (kernel.now(), text))
    sys.stdout.flush()


def truncate_log():
    """launchd appends to StandardOutPath; the watcher starts its log afresh when stdout is a regular file, and leaves a
    terminal or a pipe alone."""
    with contextlib.suppress(OSError, ValueError):
        fd = sys.stdout.fileno()
        if stat.S_ISREG(os.fstat(fd).st_mode):
            sys.stdout.flush()
            os.ftruncate(fd, 0)


def cmd_render_watch(ctx, args):
    """render --watch: every `interval` seconds compare the highest non-render event id with the id the last pass rendered
    through; when the database is ahead, run one pass under the render lock.  The watermark lives in this process (the
    renders table's through_event_id at start, then the id read before each pass), so a pass that changes nothing writes
    nothing.  Runs until SIGTERM or SIGINT, or `ticks` reads (tests).  Refused while another watcher holds the lock."""
    if args.out or args.discard:
        raise kernel.SpudError(kernel.EXIT_USAGE, "--watch renders into the home on its own: no --out, no --discard")
    if not ctx.db_path.is_file():
        raise kernel.SpudError(kernel.EXIT_ERROR, "no ledger at %s; run `spud init`" % ctx.db_path)
    fd = os.open(str(watch_lock_path(ctx)), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise kernel.SpudError(kernel.EXIT_ERROR, "a render watcher is already running for %s (it holds %s)" % (ctx.home, watch_lock_path(ctx)))
    stop = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda signum, frame: stop.append(signum))
    truncate_log()
    log_line("watching %s every %.2f s" % (ctx.db_path, args.interval))
    passes, ticks, seen = 0, 0, None
    try:
        while not stop and (args.ticks is None or ticks < args.ticks):
            try:
                con = ledgerdb.connect(ctx)
                try:
                    if seen is None:
                        seen = con.execute("SELECT COALESCE(MAX(through_event_id), 0) FROM renders").fetchone()[0]
                    latest = latest_event_id(con)
                    if latest > seen:
                        with publish.render_lock(ctx):
                            result = publish.render_pass(ctx, con, None)
                        passes += 1
                        seen = latest
                        if result["written"] or result["restyled"] or result["new_conflicts"]:
                            log_line("rendered %d, unchanged %d, restyled %d, conflicts %d (through event %d)" % (
                                len(result["written"]), len(result["unchanged"]), len(result["restyled"]), len(result["conflicts"]), latest))
                finally:
                    con.close()
            except (kernel.SpudError, sqlite3.Error) as e:
                log_line("error: %s" % (e.message if isinstance(e, kernel.SpudError) else e))
            ticks += 1
            slept = 0.0
            while not stop and slept < args.interval:
                time.sleep(min(0.1, args.interval - slept))
                slept += 0.1
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    why = ("signal %d" % stop[0]) if stop else ("%d ticks" % ticks)
    log_line("stopped after %d pass(es): %s" % (passes, why))
    return kernel.Result({"passes": passes, "ticks": ticks, "stopped_by": why}, "")
```

Until Task 14 lands, `watcher_installed` reads `schedule.schedule_plist_path().with_name("local.spud.render.plist")`; Task 14 replaces that line with `schedule.agent_plist_path("render")` as shown.

In `bin/spudlib/cli/cliparser.py`: add `renderwatch,` to the `from ..commands import (…)` list after `publish,`; replace the `render` parser block with:

```python
    p = sub.add_parser("render", help="regenerate ledger/ and reports/ from the database; a hand-edited file is left alone (exit 6), a note whose frontmatter only changed YAML style (Obsidian's rewrite) is rendered over and kept in the event; --watch keeps doing it (Spud's)")
    p.add_argument("--out", help="render into this directory instead of SPUD_HOME (no hash checks)")
    p.add_argument("--discard", metavar="PATH", help="overwrite this hand-edited file with the current render, keeping the discarded text in the event (Spud only)")
    p.add_argument("--watch", action="store_true", help="run until SIGTERM, rendering whenever the event log moves: the LaunchAgent local.spud.render's run (SPD-097)")
    p.add_argument("--interval", type=float, default=renderwatch.WATCH_INTERVAL, metavar="SECONDS", help="with --watch: seconds between reads of the event log (default %.0f)" % renderwatch.WATCH_INTERVAL)
    p.add_argument("--ticks", type=int, metavar="N", help="with --watch: stop after N reads (tests)")
    p.set_defaults(func=renderwatch.render_entry)
```

- [ ] **Step 4: Run the tests**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_watch.py`
Expected: OK (5 tests).
Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_package.py`
Expected: OK: `renderwatch` is reachable from `cliparser`, imports modules only, and stays off `HOOK_PATH`.
Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_render.py`
Expected: OK.

### Task 14: Two LaunchAgents: `local.spud.backup` and `local.spud.render`

**Files:**
- Modify: `bin/spudlib/commands/schedule.py:80-88`, `:106-128`, `:158-244`
- Modify: `bin/spudlib/commands/renderwatch.py` (`watcher_installed`, one line)
- Modify: `bin/spudlib/cli/helptexts.py:79-85` (`SCHEDULE_DESCRIPTION`), `cliparser.py:67`
- Test: `tests/test_backup.py`

**Interfaces:**
- Produces: `schedule.RENDER_LABEL == "local.spud.render"`, `schedule.LABELS`, `schedule.RENDER_LOG == ".spud/logs/render.log"`; `schedule.agent_plist_path(agent) -> Path` for `"backup"` or `"render"`; `schedule.schedule_plist_path()` (unchanged: the backup's); `schedule.render_plist(ctx) -> dict`; `schedule.agent_plists(ctx, at) -> ((agent, plist), …)`; `schedule.plist_state(path, plist) -> (exists, matches, loaded)`; `schedule.bootstrap_agent(path, label) -> (booted_out, attempts, code, stdout, stderr)`; `schedule.install_agents(ctx, at) -> [record, record]`. `schedule show|install` keep every JSON key they had for the backup agent and add `render` (the same shape); `schedule uninstall` handles both.

- [ ] **Step 1: Write the failing test**

In `tests/test_backup.py`, next to `LABEL`, add `RENDER_LABEL = "local.spud.render"`. Add to `ScheduleTest`:

```python
    def test_install_writes_and_loads_the_render_watcher_too(self):
        code, out, proc = self.answer("install")
        self.assertEqual(code, EXIT_OK, proc)
        render_path = self.agents / (RENDER_LABEL + ".plist")
        self.assertEqual((out["render"]["label"], out["render"]["path"], out["render"]["replaced"]), (RENDER_LABEL, str(render_path), False))
        plist = plistlib.loads(render_path.read_bytes())
        self.assertEqual(plist["ProgramArguments"], [self.interpreter, "-I", "-S", str(self.home.path / "bin" / "spud"), "--as", "spud", "render", "--watch"])
        self.assertEqual((plist["RunAtLoad"], plist["KeepAlive"], plist["EnvironmentVariables"], plist["StandardOutPath"], plist["StandardErrorPath"]),
                         (True, True, {"SPUD_HOME": str(self.home.path)}, str(self.home.path / ".spud" / "logs" / "render.log"), str(self.home.path / ".spud" / "logs" / "render.log")))
        self.assertTrue((self.home.path / ".spud" / "logs").is_dir())
        calls = [c[:2] for c in self.calls()]
        self.assertEqual(calls, [["bootout", self.service], ["bootstrap", "gui/%d" % self.uid], ["bootout", "gui/%d/%s" % (self.uid, RENDER_LABEL)], ["bootstrap", "gui/%d" % self.uid]])
        shown = self.answer("show")[1]
        self.assertEqual((shown["render"]["exists"], shown["render"]["matches"], shown["render"]["loaded"]), (True, True, True))
        self.assertIn("KeepAlive", shown["render"]["plist"])
        text = self.schedule("show", json_mode=False).stdout
        self.assertIn("label       %s" % RENDER_LABEL, text)
        self.assertIn("whenever it exits (KeepAlive)", text)
        gone = self.answer("uninstall")[1]
        self.assertFalse(render_path.exists())
        self.assertFalse(self.plist_path.exists())
        self.assertEqual((gone["removed"], gone["render"]["removed"]), (True, True))
```

`test_backup.py` already imports `plistlib`; `self.answer(*args)` there returns `(exit code, the parsed JSON, the process)`, `self.schedule(*args, json_mode=False)` the process of a plain run, and `self.calls()` the fake launchctl's recorded calls.

- [ ] **Step 2: Run it to see it fail**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_backup.py -k render_watcher`
Expected: FAIL with `KeyError: 'render'`.

- [ ] **Step 3: Implement**

In `bin/spudlib/commands/schedule.py`, change the import line `from ..core import kernel, lazy` to stay, add `from . import settings_sync`, and replace lines 80 to 88 with:

```python
# schedule (SPD-012): the macOS LaunchAgents.  Eric chose launchd over a Claude scheduled task: the CLI runs alone, with
# the app closed, and a run missed during sleep fires at wake.  Two agents since SPD-097: the daily backup, and the
# render watcher that keeps the vault current (RunAtLoad and KeepAlive, so launchd restarts it whenever it exits).
SCHEDULE_LABEL = "local.spud.backup"
RENDER_LABEL = "local.spud.render"
LABELS = {"backup": SCHEDULE_LABEL, "render": RENDER_LABEL}
SCHEDULE_AT = "03:00"
SCHEDULE_LOG = "~/Library/Logs/spud-backup.log"
RENDER_LOG = ".spud/logs/render.log"  # under the home; the watcher truncates it at each start
# Seconds slept before each bootstrap retry: launchd can refuse a bootstrap while the job it has just booted
# out is still going away, so five attempts over about two seconds.
BOOTSTRAP_RETRY_DELAYS = (0.25, 0.5, 0.5, 0.75)
```

Replace `schedule_plist_path` (lines 106 to 109) with:

```python
def agent_plist_path(agent):
    """$SPUD_LAUNCH_AGENTS_DIR/<label>.plist for `backup` or `render`, default ~/Library/LaunchAgents."""
    agents = os.environ.get("SPUD_LAUNCH_AGENTS_DIR") or "~/Library/LaunchAgents"
    return Path(os.path.abspath(os.path.expanduser(agents))) / (LABELS[agent] + ".plist")


def schedule_plist_path():
    """The backup agent's plist: the name every caller from before SPD-097 knows."""
    return agent_plist_path("backup")
```

After `schedule_plist` (its docstring now says "the tool's bin/spud", Task 4), add:

```python
def render_plist(ctx):
    """The watcher's LaunchAgent (SPD-097): `spud --as spud render --watch` under the tool's bin/spud with SPUD_HOME set,
    started at load and restarted by launchd whenever it exits (KeepAlive), its output in <home>/.spud/logs/render.log."""
    if not sys.executable:
        raise kernel.SpudError(kernel.EXIT_ERROR, "cannot tell which Python runs spud: sys.executable is empty")
    log = str(ctx.home / RENDER_LOG)
    return {
        "Label": RENDER_LABEL,
        "ProgramArguments": [sys.executable, "-I", "-S", str(ctx.launcher), "--as", "spud", "render", "--watch"],
        "EnvironmentVariables": {"SPUD_HOME": str(ctx.home)},
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "ProcessType": "Background",
    }


def agent_plists(ctx, at):
    """Both agents' plists, backup first, as (agent, plist)."""
    return (("backup", schedule_plist(ctx, at)), ("render", render_plist(ctx)))


def plist_state(path, plist):
    """(exists, matches, loaded) for one agent's plist on disk and its job in gui/<uid>."""
    import plistlib  # see cmd_schedule_show

    exists = path.is_file()
    matches = False
    if exists:
        try:
            matches = plistlib.loads(path.read_bytes()) == plist
        except Exception:  # unreadable, or not a plist: it does not match
            matches = False
    loaded = launchctl("print", "gui/%d/%s" % (os.getuid(), plist["Label"]))[0] == 0
    return exists, matches, loaded


def bootstrap_agent(path, label):
    """launchctl bootout (a job that was not loaded is fine), then bootstrap with retries:
    (booted_out, attempts, code, stdout, stderr)."""
    domain = "gui/%d" % os.getuid()
    booted_out = launchctl("bootout", "%s/%s" % (domain, label))[0] == 0
    attempts, code, stdout, stderr = 0, None, "", ""
    for delay in (0,) + BOOTSTRAP_RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        attempts += 1
        code, stdout, stderr = launchctl("bootstrap", domain, str(path))
        if code == 0:
            break
    return booted_out, attempts, code, stdout, stderr


def install_agents(ctx, at):
    """Write both plists atomically and (re)load both jobs, backup first; home move runs this too (SPD-097).  Returns one
    record per agent: label, path, replaced, booted_out, attempts."""
    import plistlib  # see cmd_schedule_show

    if (ctx.home / ".spud").is_dir():
        (ctx.home / RENDER_LOG).parent.mkdir(parents=True, exist_ok=True)  # launchd opens the log itself; its directory must exist
    out = []
    for agent, plist in agent_plists(ctx, at):
        path = agent_plist_path(agent)
        replaced = os.path.lexists(path)
        try:
            write_plist(path, plistlib.dumps(plist))
        except OSError as e:
            raise kernel.SpudError(kernel.EXIT_ERROR, "cannot write %s: %s" % (path, e))
        booted_out, attempts, code, stdout, stderr = bootstrap_agent(path, plist["Label"])
        record = {"label": plist["Label"], "path": str(path), "replaced": replaced, "booted_out": booted_out, "attempts": attempts}
        if code != 0:
            said = (stderr or stdout).strip() or "no output"
            raise kernel.SpudError(kernel.EXIT_ERROR, "launchctl bootstrap gui/%d %s failed %d times, the last with exit %d: %s; the plist stays at %s (spud schedule uninstall removes it)"
                                   % (os.getuid(), path, attempts, code, said, path), data=dict(record, at="%02d:%02d" % at, stderr=stderr))
        out.append(record)
    return out
```

Replace `cmd_schedule_show`, `cmd_schedule_install` and `cmd_schedule_uninstall` (lines 158 to 244) with:

```python
def cmd_schedule_show(ctx, args):
    require_spud_flag(args, "spud schedule show")
    import plistlib  # imported here, not at the top: the hooks run on every tool call and never need it

    at = "%02d:%02d" % args.at
    blocks, lines = {}, []
    for agent, plist in agent_plists(ctx, args.at):
        path = agent_plist_path(agent)
        exists, matches, loaded = plist_state(path, plist)
        xml = plistlib.dumps(plist).decode("utf-8")
        blocks[agent] = {"label": plist["Label"], "path": str(path), "exists": exists, "matches": matches, "loaded": loaded, "plist": xml}
        service = "gui/%d/%s" % (os.getuid(), plist["Label"])
        state = ("installed, matches" if matches else "installed, differs from the plist below") if exists else "not installed"
        lines += [
            "label       %s" % plist["Label"],
            "plist       %s (%s)" % (path, state),
            "loaded      %s (launchctl print %s)" % ("yes" if loaded else "no", service),
            "runs        %s" % shlex.join(plist["ProgramArguments"]),
            ("when        at load, and daily at %s (a run missed during sleep fires at wake)" % at) if agent == "backup"
            else "when        at load, and again whenever it exits (KeepAlive)",
            "log         %s" % plist["StandardOutPath"],
            "",
            xml.rstrip("\n"),
            "",
        ]
    data = dict(blocks["backup"], at=at, render=blocks["render"])
    return kernel.Result(data, "\n".join(lines).rstrip("\n"))


def cmd_schedule_install(ctx, args):
    require_spud_flag(args, "spud schedule install")
    at = "%02d:%02d" % args.at
    records = install_agents(ctx, args.at)
    data = dict(records[0], at=at, render=records[1])
    domain = "gui/%d" % os.getuid()
    lines = []
    for record in records:
        lines += [
            "wrote %s%s" % (record["path"], " (replacing the plist that was there)" if record["replaced"] else ""),
            "launchctl bootout %s/%s: %s" % (domain, record["label"], "booted out the loaded job" if record["booted_out"] else "no job was loaded"),
            "launchctl bootstrap %s %s: loaded%s" % (domain, record["path"], "" if record["attempts"] == 1 else " on attempt %d" % record["attempts"]),
        ]
    lines.append("%s runs `spud backup --daily` at load and daily at %s; its output goes to %s" % (SCHEDULE_LABEL, at, os.path.abspath(os.path.expanduser(SCHEDULE_LOG))))
    lines.append("%s runs `spud render --watch` at load and again whenever it exits; its output goes to %s" % (RENDER_LABEL, ctx.home / RENDER_LOG))
    return kernel.Result(data, "\n".join(lines), stderr=settings_sync.tool_warning(ctx) or "")


def cmd_schedule_uninstall(ctx, args):
    require_spud_flag(args, "spud schedule uninstall")
    records, lines = {}, []
    for agent, label in LABELS.items():
        path = agent_plist_path(agent)
        service = "gui/%d/%s" % (os.getuid(), label)
        booted_out = launchctl("bootout", service)[0] == 0  # a job that was not loaded is fine
        removed = False
        if os.path.lexists(path):
            try:
                path.unlink()
            except OSError as e:
                raise kernel.SpudError(kernel.EXIT_ERROR, "cannot remove %s: %s" % (path, e), data={"label": label, "path": str(path), "booted_out": booted_out, "removed": False})
            removed = True
        records[agent] = {"label": label, "path": str(path), "booted_out": booted_out, "removed": removed}
        if not booted_out and not removed:
            lines.append("%s is not installed: no plist at %s and no job loaded (launchctl bootout %s); nothing to do" % (label, path, service))
        else:
            lines.append("launchctl bootout %s: %s\n%s" % (service, "booted out the loaded job" if booted_out else "no job was loaded", ("removed %s" % path) if removed else ("no plist at %s" % path)))
    return kernel.Result(dict(records["backup"], render=records["render"]), "\n".join(lines))
```

In `bin/spudlib/commands/renderwatch.py`, `watcher_installed` now reads `return schedule.agent_plist_path("render").is_file()`.

In `bin/spudlib/cli/helptexts.py`, `SCHEDULE_DESCRIPTION` becomes:

```python
SCHEDULE_DESCRIPTION = """\
Two macOS LaunchAgents (SPD-012, SPD-097).  local.spud.backup runs `spud --as spud backup --daily` at load and daily at
the --at time; launchd fires a run missed during sleep at wake, and backup --daily writes one copy a day however often
it runs.  local.spud.render runs `spud --as spud render --watch` at load and again whenever it exits (KeepAlive), so
the vault follows the database within seconds; its log is <home>/.spud/logs/render.log, started afresh at each start.
The plists are $SPUD_LAUNCH_AGENTS_DIR/<label>.plist (default ~/Library/LaunchAgents), launchctl is $SPUD_LAUNCHCTL
(default /bin/launchctl), and the backup's output goes to ~/Library/Logs/spud-backup.log.  Every verb is Spud's
(--as spud) and handles both agents.
"""
```

In `bin/spudlib/cli/cliparser.py` line 67, the `schedule` help becomes `"the macOS LaunchAgents %s (the daily backup) and %s (the render watcher) (Spud's)" % (schedule.SCHEDULE_LABEL, schedule.RENDER_LABEL)`.

- [ ] **Step 4: Run the tests**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_backup.py`
Expected: OK. A test there that compared the exact `launchctl` call list now sees the render agent's `bootout` and `bootstrap` after the backup's (extend its expected list); one that compared `schedule show`'s whole text gains the render block (extend it); `data["label"]`, `data["path"]`, `data["at"]` and the rest keep their meaning for the backup agent.
Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_watch.py`
Expected: OK.

### Task 15: `doctor` and `board --brief` say when the watcher is down; `doctor` lists open conflicts

**Files:**
- Modify: `bin/spudlib/commands/doctor.py` (`cmd_doctor` split into `doctor_report`; `doctor_render`; `WATCHER_DOWN`)
- Modify: `bin/spudlib/commands/views.py:47-48`
- Test: `tests/test_watch.py`

**Interfaces:**
- Produces: `doctor.doctor_report(ctx) -> (report, problems, lines)`; `doctor.WATCHER_DOWN` (str); `doctor.doctor_render(ctx, problems, notes) -> {"watcher": "running" | "installed, not running" | "not installed", "conflicts": [rel, …]}`; `report["render"]`.
- Consumes: `renderwatch.watcher_alive`, `renderwatch.watcher_installed`, `publish.render_pass(..., check_only=True)`, `schedule.RENDER_LABEL`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_watch.py`:

```python
class DoctorAndBoardTest(LaunchdMixin, WatchCase):
    def setUp(self):
        super().setUp()
        self.setup_launchd()

    def test_doctor_and_board_say_when_the_watcher_is_down(self):
        self.new_ticket("Board")
        self.assertNotIn("render watcher", self.home.run("board", "--brief").stdout)
        report = self.home.json("doctor")
        self.assertEqual(report["render"], {"watcher": "not installed", "conflicts": []})
        self.assertTrue(any("no render watcher installed" in n for n in report["notes"]), report["notes"])
        self.home.json("schedule", "install", actor="spud")  # the fake launchctl loads nothing
        self.assertIn("render watcher: installed but not running", self.home.run("board", "--brief").stdout)
        proc = self.home.run("doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("installed but not running", proc.stderr)
        self.watcher()
        self.assertTrue(self.wait_for(lambda: spud.watcher_alive(self.ctx())))
        self.assertNotIn("render watcher", self.home.run("board", "--brief").stdout)
        self.assertEqual(self.home.json("doctor")["render"]["watcher"], "running")

    def test_doctor_lists_a_hand_edited_file_with_its_two_commands(self):
        self.new_ticket("Edited", brief="Original brief.")
        self.home.json("render")
        path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        path.write_text(path.read_text(encoding="utf-8").replace("Original brief.", "By hand."), encoding="utf-8")
        proc = self.home.run("--json", "doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("import --file ledger/tickets/SPD-001.md", proc.stderr)
        self.assertIn("render --discard ledger/tickets/SPD-001.md", proc.stderr)
        self.assertIn("ledger/tickets/SPD-001.md", proc.stdout)
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_watch.py -k DoctorAndBoard`
Expected: both FAIL (`KeyError: 'render'`; doctor exits 0 with a hand edit).

- [ ] **Step 3: Implement**

In `bin/spudlib/commands/doctor.py`, the imports become:

```python
from . import publish, renderwatch, schedule, settings_sync
from ..core import homeconf, kernel
from ..hooks import hookio, worktrees
from ..projects import install
from ..render import prices
from ..state import backup, ledgerdb, lookup, schema

WATCHER_DOWN = ("the render watcher %s is installed but not running: the vault is stale until `spud --as spud schedule install` reloads it"
                % schedule.RENDER_LABEL)
```

Rename `cmd_doctor` to `doctor_report(ctx)` with the docstring `"""(report, problems, lines): what cmd_doctor prints and raises on; home move reads it too (SPD-097)."""`, ending in `return report, problems, lines` instead of the `result = …` / `if problems: raise …` / `return result` tail, and add above it:

```python
def cmd_doctor(ctx, args):
    report, problems, lines = doctor_report(ctx)
    if problems:
        raise kernel.SpudError(kernel.EXIT_ERROR, "doctor found %d problem(s): %s" % (len(problems), "; ".join(problems)), data=report)
    return kernel.Result(report, "\n".join(lines))
```

Inside `doctor_report`, after `report["projects"] = …`, add:

```python
    report["render"] = doctor_render(ctx, problems, notes) if db["exists"] and db.get("user_version") == schema.SCHEMA_VERSION and config is not None else None
```

Then, after the `project` lines in the text and before the `note` lines:

```python
    if report["render"] is not None:
        r = report["render"]
        lines.append("render      watcher %s%s" % (r["watcher"], ("; %d hand-edited file(s)" % len(r["conflicts"])) if r["conflicts"] else ""))
```

And the new function, after `doctor_projects`:

```python
def doctor_render(ctx, problems, notes):
    """doctor's render section (SPD-097): whether the watcher is alive (a problem when its plist is installed and it is not,
    a note when it was never installed), and every rendered file whose on-disk text is neither the last render's nor the
    current one, each with the two commands that settle it."""
    installed = renderwatch.watcher_installed()
    alive = renderwatch.watcher_alive(ctx)
    if installed and not alive:
        problems.append(WATCHER_DOWN)
    elif not installed:
        notes.append("no render watcher installed (%s): `spud --as spud schedule install`" % schedule.RENDER_LABEL)
    con = ledgerdb.connect(ctx)
    try:
        conflicts = publish.render_pass(ctx, con, None, check_only=True)["conflicts"]
    finally:
        con.close()
    for rel in conflicts:
        problems.append("hand-edited %s: accept it with `spud --as spud import --file %s`, or overwrite it with `spud --as spud render --discard %s`" % (rel, rel, rel))
    return {"watcher": "running" if alive else ("installed, not running" if installed else "not installed"), "conflicts": conflicts}
```

In `bin/spudlib/commands/views.py`: add `from . import renderwatch` to the imports and change the `--brief` branch to:

```python
        if args.brief:
            text = sessions.board_brief_text(con, rows, parked=args.parked)
            if renderwatch.watcher_installed() and not renderwatch.watcher_alive(ctx):  # SPD-097: the vault is stale
                text += "\nrender watcher: installed but not running; the vault is stale (spud --as spud schedule install reloads it)"
```

- [ ] **Step 4: Run the tests**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_watch.py`
Expected: OK (7 tests).
Run, each: `-p test_backup.py` (its doctor tests: no plist in their scratch, so a note and exit 0), `-p test_hooks.py -k board_brief`, `-p test_package.py`
Expected: OK. `views` and `doctor` are off the hook path, so `HOOK_PATH` is untouched.

### Task 16: The render timing probe

**Files:**
- Create: `tests/probes/render_timing.py`

- [ ] **Step 1: Write the probe**

```python
"""A full pass and a no-change pass of `spud render` timed over a synthetic ledger (SPD-097).

  python3.14 -I -S tests/probes/render_timing.py [TICKETS] [MEMBERS_PER_TICKET] [LAUNCHER]

Builds a scratch SPUD_HOME with TICKETS tickets (default 60) of MEMBERS_PER_TICKET members each (default 3), a log line
and a result per member, renders it once (the full pass: every file written) and again (the no-change pass: nothing
written, nothing recorded), and prints both times in milliseconds with the file count.  Nothing outside the scratch home
is touched.  The real ledger's numbers are Spud's to take: two runs of `spud render --out <scratch>`, the second a
no-change pass, recorded on SPD-097.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONFIG = os.path.join(ROOT, "spud.config.json")
PY = sys.executable


def run(launcher, env, *args):
    proc = subprocess.run([PY, "-I", "-S", launcher, "--json", *args], env=env, capture_output=True, text=True)
    if proc.returncode:
        sys.exit("%s: exit %d %s" % (" ".join(args), proc.returncode, proc.stderr))
    return json.loads(proc.stdout)


def main():
    args = sys.argv[1:]
    tickets = int(args.pop(0)) if args and args[0].isdigit() else 60
    per_ticket = int(args.pop(0)) if args and args[0].isdigit() else 3
    launcher = os.path.abspath(args[0]) if args else os.path.join(ROOT, "bin", "spud")
    home = tempfile.mkdtemp(prefix="spud-render-timing-")
    shutil.copy(CONFIG, home)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR")}
    env.update(SPUD_HOME=home, SPUD_TOOL_DIR=home, SPUD_USER_CLAUDE_DIR=os.path.join(home, ".user-claude"), SPUD_CONFIG_DIR=os.path.join(home, ".user-config"))
    try:
        run(launcher, env, "init")
        for n in range(tickets):
            t = run(launcher, env, "--as", "spud", "ticket", "new", "--title", "Ticket %d" % n, "--status", "active", "--brief", "b", "--sizing", "s")["ticket"]
            for _ in range(per_ticket):
                m = run(launcher, env, "--as", "spud", "member", "new", "--ticket", t["key"], "--persona", "scout", "--model", "haiku", "--brief", "x", "--deliverable", "docs/**")["member"]
                run(launcher, env, "--as", m["ref"], "member", "log", "started")
                run(launcher, env, "--as", m["ref"], "member", "result", "done")
        started = time.perf_counter()
        full = run(launcher, env, "--as", "spud", "render")
        full_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        again = run(launcher, env, "--as", "spud", "render")
        again_ms = (time.perf_counter() - started) * 1000
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("%d files: full pass %.0f ms (%d written), no-change pass %.0f ms (%d written, %d unchanged)"
          % (len(full["written"]), full_ms, len(full["written"]), again_ms, len(again["written"]), len(again["unchanged"])))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `python3.14 -I -S tests/probes/render_timing.py 60 3`
Expected: one line such as `250 files: full pass … ms (250 written), no-change pass … ms (0 written, 250 unchanged)`; the no-change pass takes well under a second. Keep both numbers for the phase's log line.

### Task 17: Phase 3 suite

- [ ] **Step 1: Run the whole suite in the background and the bytecode check**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests`
Expected: OK. Then `find bin tests -name '*.pyc' -o -name __pycache__` prints nothing.

- [ ] **Step 2: Log the phase with the probe's numbers**

Run: `spud --as <id> member log "Phase 3 done: render_pass under a lock, no write when nothing changed, conflicts logged once, render --watch and local.spud.render, doctor and board say when the watcher is down; synthetic ledger of N files: full pass X ms, no-change pass Y ms; suite green"`

---

# Phase 4: the switch-over command

Spec section 5. One command, `spud --as spud home move --to <dir> [--dry-run]`, over two contexts: the old home's and the new one's. Two deviations from the spec's step list, each for a reason written into the code: the zero-write render runs right after the copy (step 3b), before anything writes the new database, because steps 5 and 6 themselves add events the vault must then show (the render of step 7 is allowed to write exactly `ledger/Projects.md` and today's report); and step 3 also copies `CLAUDE.md` and the two `.claude/settings*.json` files, so a session launched in the new home is Spud on day one and `settings sync` keeps Eric's own keys and rules.

### Task 18: `spud --as spud home move`

**Files:**
- Create: `bin/spudlib/commands/homemove.py`
- Modify: `bin/spudlib/cli/cliparser.py` (import; a `home` parser after `session`), `bin/spudlib/cli/helptexts.py` (`HOME_MOVE_DESCRIPTION`), `bin/spudlib/hooks/hookio.py:36-41`
- Test: `tests/test_home_move.py` (created here)

**Interfaces:**
- Produces: `homemove.cmd_home_move(ctx, args)` with `args.to`, `args.dry_run`, `args.next`, `args.actor`; `homemove.move_preconditions(ctx, con, target) -> [str]`; `homemove.move_steps(ctx, target) -> [str]`; `homemove.move_copy_database(con, new_path) -> {table: rows}`; `homemove.move_copy_files(ctx, target) -> [str]`; `homemove.move_check_vault(new) -> str`; `homemove.strip_home_settings(ctx, settings) -> dict`; `homemove.move_resync(old, new, args) -> [str]`; `homemove.move_verify(new) -> [str]`; constants `COPIED_DIRS`, `COPIED_FILES`, `MOVED_STATE == ".spud-moved"`, `CAPS`, `BY_HAND`. JSON: `{"to", "done": [str], "by_hand"}`, or with `--dry-run` `{"dry_run": True, "to", "steps": [str]}`.
- Consumes: `publish.render_pass`, `publish.render_lock` (Task 12); `schedule.install_agents`, `schedule.at_arg`, `schedule.SCHEDULE_AT`, `schedule.SCHEDULE_LABEL`, `schedule.RENDER_LABEL` (Task 14); `doctor.doctor_report`, `doctor.WATCHER_DOWN` (Task 15); `settings_sync.cmd_settings_sync`, `settings_sync.settings_hold_hooks`, `settings_sync.AGENT_DENY_RULES` (Task 4); `install.install_project`, `install.read_json_object`, `install.strip_ledger_settings` (Task 8); `registry.identity_chain`; `backup.do_backup`, `backup.backup_quick_check`, `backup.backups_dir`; `reportentry.write_report_entry`, `reportentry.report_entry_line`, `reportentry.check_next`; `homeconf.spud_config_dir`, `homeconf.tool_checkout_kind`, `homeconf.Ctx(…, tool=)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_home_move.py`:

```python
"""SPD-097: `spud --as spud home move --to <dir>`: --dry-run, each precondition, a full move between scratch directories with
the zero-write render, and the rollback.  The scratch home is shaped like the transition window: the tool repository's main
checkout, with tracked settings written by settings sync, project spud rooted there, BadTakes installed."""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import EXIT_ERROR, REPO, SPUD, Home, LaunchdMixin, RepoMixin, SpudTestCase, git, load_spud_module

spud = load_spud_module()
TODAY_REPORT = "reports/%s.md" % spud.now()[:10]


def tree(home, *dirs, skip=()):
    """{path relative to home: bytes} of every file under each of `dirs` in `home`, the skipped relative paths left out."""
    out = {}
    for name in dirs:
        for p in sorted((Path(home) / name).rglob("*")):
            rel = str(p.relative_to(home))
            if p.is_file() and rel not in skip:
                out[rel] = p.read_bytes()
    return out


class HomeMoveCase(LaunchdMixin, RepoMixin, SpudTestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.cleanup)
        home = self.home.path
        shutil.copytree(REPO / "bin", home / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        (home / ".claude" / "agents").mkdir(parents=True)
        shutil.copyfile(REPO / ".claude" / "agents" / "spudagent.md", home / ".claude" / "agents" / "spudagent.md")
        (home / ".gitignore").write_text(".spud/\n.user-claude/\n.user-config/\n.claude/settings.local.json\n", encoding="utf-8")
        (home / "docs").mkdir()
        (home / "docs" / "note.md").write_text("a doc\n", encoding="utf-8")
        (home / ".obsidian").mkdir()
        (home / ".obsidian" / "app.json").write_text("{}\n", encoding="utf-8")
        (home / "CLAUDE.md").write_text("You are Spud.\n", encoding="utf-8")
        self.home.write_settings({"model": "claude-fable-5-1", "permissions": {"allow": ["Bash(date:*)"]}})
        self.setup_launchd()
        self.home.init()
        self.cli("settings", "sync", actor="spud")
        git(home, "init", "-q", "-b", "main")
        git(home, "add", "-A")
        git(home, "commit", "-q", "-m", "home and tool")
        self.bad = self.make_repo("badtakes-")
        self.add_project(self.bad)
        self.cli("project", "install", "badtakes", actor="spud")
        t = self.new_ticket("Before the move", status="active")
        m = self.new_member(t["key"], name="Russet", deliverable=["bin/**"])
        self.cli("member", "finish", m["ref"], "--status", "done", "--outcome", "fine", "--summary", "Did the thing before the move.", actor="spud")
        self.cli("render", actor="spud")
        self.cli("schedule", "install", actor="spud")
        self.target = self.scratch_dir("new-home-")
        self.pointer = home / ".user-config" / "home"
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), str(home))  # project install wrote it, as it did on Eric's Mac

    def move(self, *extra, check=False, actor="spud"):
        return self.cli("home", "move", "--to", self.target, *extra, actor=actor, check=check)

    def new_env(self):
        """For RepoMixin.cli's `env=`: what makes a run address the new home."""
        return {"SPUD_HOME": str(self.target)}

    def board_through_the_pointer(self):
        """`spud board` with no SPUD_HOME in the environment: only the pointer under SPUD_CONFIG_DIR says where the home is."""
        env = dict(self.home.env)
        env.pop("SPUD_HOME")
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, "-I", "-S", str(SPUD), "board"], capture_output=True, text=True, env=env, cwd=str(self.home.path))

    def hook_commands(self, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return [h["command"] for groups in data.get("hooks", {}).values() for g in groups for h in g["hooks"] if "bin/spud hook" in h["command"]]


class PreconditionsTest(HomeMoveCase):
    def assertRefused(self, needle, *extra):
        proc = self.move(*extra)
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        self.assertIn(needle, proc.stderr)
        self.assertFalse((self.home.path / ".spud-moved").exists())
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), str(self.home.path))

    def test_dry_run_checks_and_prints_the_steps_and_moves_nothing(self):
        out = self.cli_json("home", "move", "--to", self.target, "--dry-run", actor="spud")
        self.assertEqual((out["dry_run"], out["to"], len(out["steps"])), (True, str(self.target), 9))
        self.assertIn(str(self.target / ".spud"), out["steps"][1])
        self.assertIn("rename %s" % (self.home.path / ".spud"), out["steps"][7])
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertTrue((self.home.path / ".spud" / "ledger.db").is_file())

    def test_a_live_member_refuses(self):
        t = self.new_ticket("Live", status="active")
        self.new_member(t["key"], name="Yukon")
        self.assertRefused("planned or active (SPUD-002/Yukon)")

    def test_a_target_that_is_not_empty_or_is_inside_a_work_tree_or_the_home_refuses(self):
        (self.target / "x").write_text("x", encoding="utf-8")
        proc = self.move()
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        self.assertIn("is not an empty directory", proc.stderr)
        self.assertFalse((self.home.path / ".spud-moved").exists())
        (self.target / "x").unlink()
        proc = self.cli("home", "move", "--to", self.make_repo("repo-") / "sub", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("inside a git work tree", proc.stderr)
        proc = self.cli("home", "move", "--to", self.home.path / "vault", actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("current home or inside it", proc.stderr)

    def test_a_hand_edit_refuses(self):
        path = self.home.path / "ledger" / "tickets" / "SPD-001.md"
        path.write_text(path.read_text(encoding="utf-8").replace("## Brief", "## Brief\nBy hand."), encoding="utf-8")
        self.assertRefused("hand-edited rendered file(s): ledger/tickets/SPD-001.md")

    def test_a_worktree_launcher_and_an_earlier_move_refuse(self):
        wt = self.add_worktree(self.home.path, "spd-999-x")
        self.home.env["SPUD_TOOL_DIR"] = str(wt)
        self.assertRefused("linked worktree")
        self.home.env["SPUD_TOOL_DIR"] = str(self.home.path)
        (self.home.path / ".spud-moved").mkdir()
        proc = self.move()
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("earlier move", proc.stderr)

    def test_only_spud_moves_the_home(self):
        t = self.new_ticket("Actor", status="active")
        m = self.new_member(t["key"], name="Kennebec")
        self.assertEqual(self.move(actor=m["ref"]).returncode, 3)


class FullMoveTest(HomeMoveCase):
    def test_the_move_and_what_it_leaves(self):
        old = self.home.path
        before = tree(old, "ledger", "reports")
        events_before = self.home.scalar("SELECT count(*) FROM events")
        proc = self.move(check=True)
        out = json.loads(self.cli("--json", "events", "--kind", "report.entry", env=self.new_env()).stdout)
        # the old home: state renamed, vault untouched
        self.assertFalse((old / ".spud").exists())
        self.assertTrue((old / ".spud-moved" / "ledger.db").is_file())
        self.assertEqual(tree(old, "ledger", "reports"), before)
        self.assertIn("end this session", proc.stdout)
        self.assertIn("removal commit", proc.stdout)
        # the new home: the vault, the docs, the config, CLAUDE.md, the backups, the database
        new = self.target
        self.assertEqual(tree(new, "ledger", "reports", skip=("ledger/Projects.md", TODAY_REPORT)),
                         {k: v for k, v in before.items() if k not in ("ledger/Projects.md", TODAY_REPORT)})
        self.assertEqual((new / "docs" / "note.md").read_text(encoding="utf-8"), "a doc\n")
        self.assertTrue((new / ".obsidian" / "app.json").is_file() and (new / "CLAUDE.md").is_file() and (new / "spud.config.json").is_file())
        self.assertTrue(any(p.name.endswith("-pre-move.db") for p in (new / ".spud" / "backups").iterdir()))
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), str(new))
        # the database: every row, plus the move's own events; project spud claims now
        counts = json.loads(self.cli("--json", "sql", "--readonly", "select count(*) as n from events", env=self.new_env()).stdout)["rows"][0]["n"]
        self.assertGreaterEqual(counts, events_before + 5)  # project.edited, report.entry, config.synced, project.installed x2
        self.assertEqual([e["data"]["title"] for e in out["events"]][-1], "Home moved from %s to %s" % (old, new))
        project = json.loads(self.cli("--json", "project", "show", "spud", env=self.new_env()).stdout)["project"]
        self.assertEqual((project["sessions"], project["root"], project["installed"]), ("claim", str(old), True))
        # the settings: the new home's own, the tool's stripped tracked file, the tool's and BadTakes' local files
        commands = self.hook_commands(new / ".claude" / "settings.json")
        self.assertEqual(len(commands), 9)
        self.assertTrue(all(c.startswith("SPUD_HOME=%s " % new) and " %s/bin/spud hook " % old in c for c in commands), commands)
        self.assertEqual(json.loads((new / ".claude" / "settings.json").read_text(encoding="utf-8"))["model"], "claude-fable-5-1")
        tracked = json.loads((old / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(tracked, {"model": "claude-fable-5-1", "permissions": {"allow": ["Bash(date:*)"]}})
        self.assertIn(" M .claude/settings.json", git(old, "status", "--porcelain"))
        local = self.hook_commands(old / ".claude" / "settings.local.json")
        self.assertEqual(len(local), 9)
        self.assertTrue(all(c.startswith("SPUD_HOME=%s " % new) and c.endswith(" --project spud") for c in local), local)
        self.assertTrue(all(c.startswith("SPUD_HOME=%s " % new) for c in self.hook_commands(self.bad / ".claude" / "settings.local.json")))
        skill = (old / ".user-claude" / "skills" / "spud" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Read %s/CLAUDE.md in full" % new, skill)
        # the agents name the new home; the calls: two pairs before the move, two pairs by it
        for label in ("local.spud.backup", "local.spud.render"):
            self.assertIn("<string>SPUD_HOME</string>", (self.agents / (label + ".plist")).read_text(encoding="utf-8"))
            self.assertIn("<string>%s</string>" % new, (self.agents / (label + ".plist")).read_text(encoding="utf-8"))
        self.assertEqual([c[0] for c in self.launchctl_calls()], ["bootout", "bootstrap"] * 4)
        # the new home answers through the pointer alone; the old one has no ledger any more
        proc = self.board_through_the_pointer()
        self.assertEqual(proc.returncode, 0, proc)
        self.assertIn("SPD-001", proc.stdout)
        proc = self.cli("board", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("no ledger at", proc.stderr)
        # a render in the new home has nothing left to write
        self.assertEqual(json.loads(self.cli("--json", "render", actor="spud", env=self.new_env()).stdout)["written"], [])

    def test_the_rollback(self):
        old = self.home.path
        self.move(check=True)
        self.pointer.write_text(str(old) + "\n", encoding="utf-8")
        os.rename(old / ".spud-moved", old / ".spud")
        self.cli("settings", "sync", actor="spud")
        self.cli("project", "install", "badtakes", actor="spud")
        self.cli("schedule", "install", actor="spud")
        self.assertIn("SPD-001", self.board_through_the_pointer().stdout)
        self.assertTrue(all(c.startswith("SPUD_HOME=%s " % old) for c in self.hook_commands(old / ".claude" / "settings.json")))
        self.assertTrue(all(c.startswith("SPUD_HOME=%s " % old) for c in self.hook_commands(self.bad / ".claude" / "settings.local.json")))
        self.assertIn("<string>%s</string>" % old, (self.agents / "local.spud.render.plist").read_text(encoding="utf-8"))
        self.assertEqual(self.home.json("project", "show", "spud")["project"]["sessions"], "always")  # the old database never changed


if __name__ == "__main__":
    unittest.main()
```

`RepoMixin.cli` takes `env=` for extra variables and merges them over the home's, so `new_env()` overrides `SPUD_HOME` alone; a run with no `SPUD_HOME` at all cannot be made that way (a merge removes nothing), which is why `board_through_the_pointer` runs the launcher itself.

- [ ] **Step 2: Run them to see them fail**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home_move.py`
Expected: every test FAILS with exit 2 from the CLI, `invalid choice: 'home'`.

- [ ] **Step 3: Implement the command**

Create `bin/spudlib/commands/homemove.py`:

```python
"""commands/homemove: spud --as spud home move, the switch-over from a home inside the tool repository to a plain directory
(SPD-097, design section 5).  One procedure over two contexts, the old home's and the new one's, kept whole because every
step reads what the ones before it did."""

import json
import os
import shutil
import sqlite3
from pathlib import Path

from . import doctor, publish, reportentry, schedule, settings_sync
from ..core import homeconf, kernel, lazy
from ..hooks import worktrees
from ..projects import install, registry
from ..state import actors, backup, ledgerdb, schema

COPIED_DIRS = ("ledger", "reports", "docs", ".obsidian")
# The config, and three the spec's list leaves out for a reason each: CLAUDE.md, so a session in the new home is Spud before
# he rewrites it there; the two settings files, so settings sync keeps Eric's own keys and rules on top of the ledger's.
COPIED_FILES = ("spud.config.json", "CLAUDE.md", ".claude/settings.json", ".claude/settings.local.json")
MOVED_STATE = ".spud-moved"
CAPS = ("CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH", "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS")
BY_HAND = """by hand, now:
  - end this session: its hooks still name %(old)s, whose state directory is now %(moved)s, so every tool call is refused from here on
  - open a new session in %(new)s (it is Spud there: the board, then its CLAUDE.md rewritten for the new layout and the memories copied)
  - open %(new)s as the vault in Obsidian
  - in a session in %(tool)s claimed with /spud: the removal commit (ledger/, reports/, docs/, spud.config.json and the stripped .claude/settings.json), then push
rollback, until the removal commit: write %(old)s into %(pointer)s, rename %(moved)s back to .spud, and with SPUD_HOME=%(old)s rerun `settings sync`, `project install <key>` for each installed project and `schedule install`; ledger writes made in %(new)s meanwhile are lost"""


def move_preconditions(ctx, con, target):
    """Every reason the move is refused, each naming what is in the way (section 5): live members, the target, an earlier
    move's state directory, the running launcher's checkout, and hand-edited rendered files."""
    problems = []
    live = ["%s/%s" % (r["team_key"], r["name"]) for r in con.execute(
        "SELECT t.team_key, m.name FROM members m JOIN tickets t ON t.id = m.ticket_id WHERE m.status IN ('planned', 'active') ORDER BY m.id").fetchall()]
    if live:
        problems.append("%d member(s) planned or active (%s); finish them first" % (len(live), ", ".join(live)))
    if target.exists():
        if not target.is_dir():
            problems.append("%s exists and is not a directory" % target)
        elif any(target.iterdir()):
            problems.append("%s is not an empty directory" % target)
    nearest = target
    while not nearest.exists():
        nearest = nearest.parent
    home_ident = worktrees.file_identity(ctx.home)
    if home_ident is not None and home_ident in registry.identity_chain(nearest):
        problems.append("%s is the current home or inside it" % target)
    proc = homeconf.run_git(nearest, "rev-parse", "--is-inside-work-tree", timeout=10)
    if proc.returncode == 0 and proc.stdout.strip() == "true":
        problems.append("%s is inside a git work tree (%s); the home is a plain directory" % (target, nearest))
    if (ctx.home / MOVED_STATE).exists():
        problems.append("%s exists from an earlier move; delete or rename it first" % (ctx.home / MOVED_STATE))
    if homeconf.tool_checkout_kind(ctx.tool) == "worktree":
        problems.append("the running bin/spud is in a linked worktree (%s); run the main checkout's" % ctx.tool)
    conflicts = publish.render_pass(ctx, con, None, check_only=True)["conflicts"]
    if conflicts:
        problems.append("hand-edited rendered file(s): %s; accept each with `spud --as spud import --file <path>` or overwrite it with"
                        " `spud --as spud render --discard <path>`" % ", ".join(conflicts))
    return problems


def move_steps(ctx, target):
    """The nine steps as --dry-run prints them, with this home's paths filled in."""
    return [
        "write a checked backup in %s" % backup.backups_dir(ctx),
        "copy the database into %s with SQLite's online backup, run integrity_check, compare row counts table by table" % (target / ".spud"),
        "copy %s, %s and %s; render into %s and require zero files written" % (
            ", ".join(d + "/" for d in COPIED_DIRS), ", ".join(COPIED_FILES), backup.backups_dir(ctx), target),
        "write %s" % (homeconf.spud_config_dir() / "home"),
        "set project spud's sessions to claim; settings sync into %s; strip the ledger's entries from %s; project install spud and re-sync"
        " every installed project and the ~/.claude copies" % (target / ".claude" / "settings.json", ctx.tool / ".claude" / "settings.json"),
        "install %s and %s with the new paths" % (schedule.SCHEDULE_LABEL, schedule.RENDER_LABEL),
        "render again (the move's own events) and run doctor on %s" % target,
        "rename %s to %s" % (ctx.home / ".spud", ctx.home / MOVED_STATE),
        "print what is left by hand",
    ]


def move_copy_database(con, new_path):
    """SQLite's online backup of the open database into new_path, then integrity_check, the schema version, WAL, and a row
    count per table compared with the source's.  Returns {table: rows}."""
    dst = sqlite3.connect(str(new_path))
    try:
        con.backup(dst)
        check = [str(r[0]) for r in dst.execute("PRAGMA integrity_check").fetchall()]
        if check != ["ok"]:
            raise kernel.SpudError(kernel.EXIT_ERROR, "integrity_check of the copy %s: %s" % (new_path, "; ".join(check)[:1000]))
        version = dst.execute("PRAGMA user_version").fetchone()[0]
        if version != schema.SCHEMA_VERSION:
            raise kernel.SpudError(kernel.EXIT_ERROR, "the copy %s has user_version %d, not %d" % (new_path, version, schema.SCHEMA_VERSION))
        if dst.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            dst.execute("PRAGMA journal_mode = WAL")
        counts = {}
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
            source = con.execute('SELECT count(*) FROM "%s"' % name).fetchone()[0]
            copied = dst.execute('SELECT count(*) FROM "%s"' % name).fetchone()[0]
            if source != copied:
                raise kernel.SpudError(kernel.EXIT_ERROR, "table %s has %d rows in the source and %d in the copy" % (name, source, copied))
            counts[name] = source
    finally:
        dst.close()
    return counts


def move_copy_files(ctx, target):
    """Step 3's copies: the vault's directories, the config and the files a first session needs, then the backups."""
    copied = []
    for name in COPIED_DIRS:
        src = ctx.home / name
        if src.is_dir():
            shutil.copytree(src, target / name, symlinks=True, dirs_exist_ok=True)
            copied.append(name + "/")
    for name in COPIED_FILES:
        src = ctx.home / name
        if src.is_file():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target / name)
            copied.append(name)
    backups = backup.backups_dir(ctx)
    if backups.is_dir():
        shutil.copytree(backups, target / ".spud" / "backups", dirs_exist_ok=True)
        copied.append(".spud/backups/")
    return copied


def move_check_vault(new):
    """Step 3b: the copied vault must already be what the copied database renders, before anything writes that database."""
    con = ledgerdb.connect(new)
    try:
        result = publish.render_pass(new, con, None, check_only=True)
    finally:
        con.close()
    if result["written"] or result["conflicts"]:
        raise kernel.SpudError(kernel.EXIT_ERROR, "the copied vault in %s would need %d file(s) rendered (%s) and has %d conflict(s): the copy does not match the database"
                               % (new.home, len(result["written"]), ", ".join(result["written"][:5]), len(result["conflicts"])), data=result)
    return "3b. the copied vault matches the copied database: 0 to write, %d unchanged" % len(result["unchanged"])


def strip_home_settings(ctx, settings):
    """The tool repository's tracked .claude/settings.json without what settings sync wrote for the home: the ledger hooks,
    the CLI allow rules, the Agent deny rules and the two env caps; every other key (the model, Eric's own rules) stays."""
    install.strip_ledger_settings(ctx, settings, None, False)
    permissions = settings.get("permissions")
    if isinstance(permissions, dict):
        if isinstance(permissions.get("deny"), list):
            permissions["deny"] = [d for d in permissions["deny"] if d not in settings_sync.AGENT_DENY_RULES]
            if not permissions["deny"]:
                del permissions["deny"]
        if not permissions:
            del settings["permissions"]
    env = settings.get("env")
    if isinstance(env, dict):
        for key in CAPS:
            env.pop(key, None)
        if not env:
            del settings["env"]
    return settings


def move_resync(old, new, args):
    """Step 5 over the new home: project spud claims, the report entry, the new home's settings, the tool's tracked settings
    stripped, project spud installed and every installed project re-synced (the user-scope agent and skill with them)."""
    done = []
    con = ledgerdb.connect(new)
    try:
        at = kernel.now()
        with ledgerdb.write_txn(con):
            row = con.execute("SELECT * FROM projects WHERE id = 1").fetchone()
            if row["sessions"] != "claim":
                con.execute("UPDATE projects SET sessions = 'claim' WHERE id = 1")
                ledgerdb.write_event(con, at, "spud", "project.edited", "project %s edited: sessions" % row["key"],
                                     data={"project": row["key"], "fields": ["sessions"], "from": {"sessions": row["sessions"]}, "to": {"sessions": "claim"}})
            entry = reportentry.write_report_entry(con, at, "Home moved from %s to %s" % (old.home, new.home), "home move", None,
                                                   lines=["Tool: %s (project %s, sessions claim)" % (new.tool, row["key"])], next_line=args.next)
        done.append("5a. project %s: sessions claim; %s" % (row["key"], reportentry.report_entry_line(entry)))
        synced = settings_sync.cmd_settings_sync(new, lazy.argparse.Namespace(path=None, dry_run=False))
        done.append("5b. %s %s" % (synced.data["path"], "written" if synced.data["written"] else "unchanged"))
        tracked = old.tool / ".claude" / "settings.json"
        if tracked.is_file() and settings_sync.settings_hold_hooks(old, tracked):
            data = install.read_json_object(tracked)
            strip_home_settings(new, data)
            kernel.write_whole(tracked, json.dumps(data, indent=2) + "\n")
            done.append("5c. the ledger's hooks and rules removed from %s (uncommitted: the removal commit takes it)" % tracked)
        for p in con.execute("SELECT * FROM projects WHERE archived_at IS NULL ORDER BY id").fetchall():
            if p["id"] != 1 and not p["installed"]:
                continue
            record, written, _first = install.install_project(new, con, p)
            at = kernel.now()
            with ledgerdb.write_txn(con):
                con.execute("UPDATE projects SET installed = ? WHERE id = ?", (json.dumps(record), p["id"]))
                ledgerdb.write_event(con, at, "spud", "project.installed", "project %s installed by home move: %d file(s) written" % (p["key"], len(written)),
                                     data={"project": p["key"], "written": written, "sync": True})
            done.append("5d. project %s: %s" % (p["key"], ", ".join(written) or "unchanged"))
    finally:
        con.close()
    return done


def move_verify(new):
    """Step 7: the pass after the move's own events, which touch Projects.md and today's report and nothing else, then doctor
    with every problem but the just-bootstrapped watcher's."""
    con = ledgerdb.connect(new)
    try:
        with publish.render_lock(new):
            result = publish.render_pass(new, con, None)
    finally:
        con.close()
    allowed = {"ledger/Projects.md", "reports/%s.md" % kernel.now()[:10]}
    unexpected = [rel for rel in result["written"] if rel not in allowed]
    if unexpected or result["conflicts"]:
        raise kernel.SpudError(kernel.EXIT_ERROR, "the render into %s wrote %s and found %d conflict(s); the copied vault does not match the copied database"
                               % (new.home, ", ".join(unexpected) or "nothing unexpected", len(result["conflicts"])), data=result)
    done = ["7a. render into %s: %s written, %d unchanged" % (new.home, ", ".join(result["written"]) or "nothing", len(result["unchanged"]))]
    report, problems, _lines = doctor.doctor_report(new)
    real = [p for p in problems if p != doctor.WATCHER_DOWN]
    if real:
        raise kernel.SpudError(kernel.EXIT_ERROR, "doctor on %s: %s" % (new.home, "; ".join(real)), data=report)
    done.append("7b. doctor ok" + ("; the watcher was bootstrapped a moment ago and is not up yet: `spud doctor` in the new session confirms it" if len(real) != len(problems) else ""))
    return done


def cmd_home_move(ctx, args):
    """spud --as spud home move --to <dir> [--dry-run]: design section 5, in its order, each step reported; a step that fails
    stops the move with the old home untouched (its state directory is renamed last)."""
    target = Path(os.path.abspath(os.path.expanduser(args.to)))
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "moving the home")
        reportentry.check_next(con, actor, args)
        problems = move_preconditions(ctx, con, target)
        if problems:
            raise kernel.SpudError(kernel.EXIT_ERROR, "home move refused: " + "; ".join(problems), data={"problems": problems, "to": str(target)})
        steps = move_steps(ctx, target)
        if args.dry_run:
            return kernel.Result({"dry_run": True, "to": str(target), "steps": steps},
                                 "home move --dry-run: the preconditions hold; the move would\n" + "\n".join("  %d. %s" % (n, s) for n, s in enumerate(steps, start=1)))
        done = []
        with publish.render_lock(ctx):  # steps 1 and 2: no pass touches the database while it is copied
            copy = backup.do_backup(ctx, con, "pre-move")
            check = backup.backup_quick_check(copy)
            if check != ["ok"]:
                raise kernel.SpudError(kernel.EXIT_ERROR, "the pre-move backup %s did not check ok (%s); nothing moved" % (copy, "; ".join(check)[:1000]))
            done.append("1. backup %s (quick_check ok)" % copy)
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            (target / ".spud").mkdir(parents=True, exist_ok=True)
            counts = move_copy_database(con, target / ".spud" / "ledger.db")
            done.append("2. database copied into %s: integrity ok, %d tables, %d rows" % (target / ".spud", len(counts), sum(counts.values())))
    finally:
        con.close()
    pointer = homeconf.spud_config_dir() / "home"
    new = homeconf.Ctx(target, "home move", ctx.json, tool=ctx.tool)
    try:
        done.append("3. copied " + ", ".join(move_copy_files(ctx, target)))
        done.append(move_check_vault(new))
        kernel.write_whole(pointer, str(target) + "\n")
        done.append("4. %s names %s" % (pointer, target))
        done.extend(move_resync(ctx, new, args))
        for record in schedule.install_agents(new, schedule.at_arg(schedule.SCHEDULE_AT)):
            done.append("6. %s loaded from %s" % (record["label"], record["path"]))
        done.extend(move_verify(new))
    except kernel.SpudError as e:
        pointed = pointer.is_file() and pointer.read_text(encoding="utf-8").strip() == str(target)
        hint = "the old home %s is untouched%s" % (ctx.home, ("; %s names %s now: write %s back into it to return" % (pointer, target, ctx.home)) if pointed else "")
        raise kernel.SpudError(e.code, "home move stopped after:\n  %s\n%s\n%s; remove %s and rerun once that is fixed" % ("\n  ".join(done), e.message, hint, target),
                               data=dict(e.data, done=done))
    os.rename(ctx.home / ".spud", ctx.home / MOVED_STATE)
    done.append("8. %s renamed to %s" % (ctx.home / ".spud", ctx.home / MOVED_STATE))
    by_hand = BY_HAND % {"old": ctx.home, "moved": MOVED_STATE, "new": target, "tool": ctx.tool, "pointer": pointer}
    return kernel.Result({"to": str(target), "done": done, "by_hand": by_hand}, "\n".join(done) + "\n" + by_hand)
```

In `bin/spudlib/cli/cliparser.py`: add `homemove,` to the `from ..commands import (…)` list (after `doctor,`), and after the `session` parser block add:

```python
    p = sub.add_parser("home", help="Spud's home: the directory holding the database, the config and the vault (SPD-097)")
    ps = p.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ps.required = True
    q = ps.add_parser("move", help="move the home to an empty directory outside every git work tree: backup, copy, re-point, re-sync hooks and agents, verify (Spud's)",
                      description=helptexts.HOME_MOVE_DESCRIPTION, formatter_class=lazy.argparse.RawDescriptionHelpFormatter)
    q.add_argument("--to", required=True, help="the new home: a directory that does not exist or is empty, not inside a git work tree")
    q.add_argument("--dry-run", action="store_true", help="check the preconditions and print the steps; move nothing")
    q.add_argument("--next", type=text_arg, help="Spud's Next line, last in the report entry the move writes")
    q.set_defaults(func=homemove.cmd_home_move)
```

In `bin/spudlib/cli/helptexts.py`, add:

```python
HOME_MOVE_DESCRIPTION = """\
Move Spud's home to a plain directory (SPD-097, design section 5).  Refused while any member is planned or active, when
--to exists and is not empty, lies inside a git work tree or inside the current home, when a rendered file is hand-edited
(spud doctor lists them with the commands that settle each), when an earlier move left .spud-moved behind, and when the
running bin/spud sits in a linked worktree.  Then, each step reported: a checked backup; the database copied with
SQLite's online backup, integrity-checked and compared row by row; ledger/, reports/, docs/, .obsidian/, the config,
CLAUDE.md, the two .claude settings files and the backups copied, and the copied vault checked against the copied
database (zero files to render); ~/.config/spud/home re-pointed; project spud set to sessions claim, the new home's
.claude/settings.json synced, the ledger's entries stripped from the tool's tracked .claude/settings.json (left
uncommitted for the removal commit), project spud installed and every installed project re-synced; both LaunchAgents
reinstalled; a render and spud doctor in the new home; the old .spud renamed .spud-moved.  Ends by printing what is
left by hand and how to roll back until the removal commit.  --dry-run checks the preconditions and prints the steps.
"""
```

In `bin/spudlib/hooks/hookio.py`: add `"home"` to `SPUD_COMMANDS` (after `"session"`) and `("home", "move")` to `SPUD_ONLY_SUBCOMMANDS`.

- [ ] **Step 4: Run the tests**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_home_move.py`
Expected: OK (8 tests).
Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_package.py`
Expected: OK: `homemove` reachable from `cliparser`, off the hook path, no name defined twice (`move_*` names are new), no alias shadowed (no local is named `install`, `schedule`, `doctor`, `backup` or `publish` in the module).
Run: `python3.14 -I -S -m unittest discover -s tests -t tests -p test_hooks.py`
Expected: OK (`home move` is refused to members by name, as every entry of `SPUD_ONLY_SUBCOMMANDS` is).

### Task 19: Phase 4 suite

- [ ] **Step 1: Run the whole suite in the background and the bytecode check**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests`
Expected: OK. Then `find bin tests -name '*.pyc' -o -name __pycache__` prints nothing.

- [ ] **Step 2: Log the phase**

Run: `spud --as <id> member log "Phase 4 done: home move with its preconditions, dry run, full move and rollback under test; suite green"`

---

# Phase 5: probes, sizes, the last suite

### Task 20: The hook timing probe, the session diff, the module sizes

- [ ] **Step 1: Time the hooks against main**

Run: `python3.14 -I -S tests/probes/hook_timing.py 30 /Users/ericlugo/Personal/Spud/bin/spud "$PWD/bin/spud"`
Expected: every hook case's median within 1 ms of main's in the same run. The branch does one more thing in the Write case than main, once per process: `project_checkouts` lists project spud's worktrees, whose root is now the launcher's own checkout (a `.git` directory: `worktrees_fingerprint` is one `os.listdir` and a few `stat` calls, then the cache file under the scratch home's state directory is read), where main listed the scratch home's, which had no `.git` and cost nothing. Expected cost: under 0.2 ms. If a hook case is still over by more than 1 ms after a second run, record the medians with `member block "hook timing: <case> is +X ms over main after SPD-097's worktree listing of project spud; accept, or exempt a root whose .git/worktrees directory is absent from the cache read?"` and return: the budget is Eric's.

- [ ] **Step 2: Diff a scripted session against main**

Run: `python3.14 -I -S tests/probes/session_diff.py /Users/ericlugo/Personal/Spud/bin/spud "$PWD/bin/spud"`
Expected: differences only in these steps, each an intended change of this ticket: `--help` (the `home` command, `render --watch`, the reworded `schedule` line, no `ledger`), `session show` (`checkout … (home)` for the scratch home), `--as spud schedule show` (the render agent's block), `doctor` (the `tool` and `render` lines, the watcher note), `settings sync --dry-run` only if the two launchers' checkouts mask differently. Every other step identical. Note the differing step numbers for the log line.

- [ ] **Step 3: The sizes, with the reasons written down**

Run: `python3.14 -I -S tests/probes/module_sizes.py`
Expected: `commands/schedule.py` (about 300 lines: the two agents are one pair that drifts apart if separated, docstring says so), `commands/homemove.py` (about 240: one procedure, docstring says so), `commands/renderwatch.py` (about 130) and `commands/publish.py` (about 170) are where the plan puts them; `hooks/worktrees.py` grows by about 20. No module was split; none crosses 350.

- [ ] **Step 4: The last suite and the bytecode check**

Run: `python3.14 -I -S -m unittest discover -s tests -t tests`
Expected: OK. `find bin tests -name '*.pyc' -o -name __pycache__` prints nothing. `grep -rn "ledger commit\|git common dir\|spud:docs" bin tests .claude/agents` prints nothing.

- [ ] **Step 5: Log the phase and the result**

Run: `spud --as <id> member log "Phase 5 done: hook timing within 1 ms of main (medians: …), session diff differs only in steps …, module sizes as planned; suite green (N tests)"`, then `spud --as <id> member result "…"` naming every file created and deleted, the two probe results, the synthetic render timings, and that Spud still owes the real-ledger timings and the runbook below.

---

# Runbook for Spud: from the engineer's return to SPD-098

Spud's own steps, in order. Nothing here is the engineer's. Every `spud` is the main checkout's launcher, `python3.14 -I -S /Users/ericlugo/Personal/Spud/bin/spud`, which runs the **old** code until step 3 lands and the **new** code from then on.

**A. Record and land (old code, the home is still the checkout)**

1. `member finish SPUD-097/<Engineer> …`, decide its proposals, `handoff add`, `ticket edit --outcome` with the probe numbers, then `spud render` and `spud --as spud ledger commit --message "SPD-097: …"` from the main checkout. This is the last ledger commit ever; it still exists because the main checkout runs the old code.
2. Rerun the full suite on the branch; `git merge-tree --write-tree main <branch>`; merge `main` into the branch and rerun the suite if another session merged meanwhile.
3. From the main checkout: `git merge --no-ff worktree-spd-097-home-split`, push. **The new code is live for every session now.** Remove the worktree and its branch as Main and worktrees says.
4. Immediately after the merge: `spud doctor`. Expected: `SPUD_HOME … (via ~/.config/spud/home)` when SPUD_HOME is unset in the shell, `tool /Users/ericlugo/Personal/Spud (bin/spud; main checkout)`, project `spud` with `root is the home (before home move)`, project `badtakes` as before, a note that no render watcher is installed, no problem. If the pointer were missing (`cannot find Spud's home`), `spud --as spud project sync --all` writes it; it is present today.
5. `spud --as spud settings sync` (it rewrites the same hook lines with the same paths: the home and the launcher are unchanged so far; the `config.synced` event is the proof the new code ran), `spud --as spud project sync --all` (BadTakes' hook lines, the user-scope agent and skill from the new `spudagent.md` and the reworded skill).
6. Time the real ledger: `spud render --out /tmp/spud-render-timing` twice (the first is the full pass over the current 410 files, the second a no-change pass); `ticket edit SPD-097 --outcome` with both numbers next to the synthetic ones; `spud render`; commit `ledger/` and `reports/` with plain git (`git add ledger reports && git commit -m "SPD-097: …" && git push`). Any further ledger write in this window is rendered and committed the same way.

**B. The switch-over (one sitting, no spudagent alive)**

7. `spud board --brief`: no member planned or active in any project. `spud doctor`: no hand-edited file.
8. `spud --as spud home move --to /Users/ericlugo/Personal/SpudHome --dry-run`, read the nine steps.
9. `spud --as spud home move --to /Users/ericlugo/Personal/SpudHome --next "Spud continues in the new home; the removal commit follows"`. Read every `done` line. The last two hooks of this session may fail closed from here on: that is the point of the next step.
10. **End this session.** Its hooks name the old home, whose state directory is now `.spud-moved`.
11. Eric opens `/Users/ericlugo/Personal/SpudHome` as the vault in Obsidian; the hidden-templates setting is local to the vault and is set again there.
12. A new session launched in `/Users/ericlugo/Personal/SpudHome` is Spud (the copied `CLAUDE.md`). First `spud doctor`: the watcher running, both projects installed, no problem. Then, as Spud's own files: rewrite `CLAUDE.md` there for the new layout (spec section 7: no render or ledger commit steps; Law 10 reads "code is built in the ticket's bound worktree; the ledger needs no git"; Main and worktrees replaced by a short section on the home and ticket worktrees; the Ledger v1 paths; In another project covering the tool repository); copy `~/.claude/projects/-Users-ericlugo-Personal-Spud/memory/` to `~/.claude/projects/-Users-ericlugo-Personal-SpudHome/memory/` and rewrite `worktree-session-git-guard` and `render-after-spawn` for the new flow (the watcher renders; Spud never renders after a spawn). Nothing is committed: the home is not a repository.
13. A session in `/Users/ericlugo/Personal/Spud` (plain until `/spud` claims it, or a prompt naming an SPD ticket does): plan a writer for the tool repository's `CLAUDE.md` (developing `spud`: the suite, the probes, the `spudlib-modules` skill, the main checkout as the running copy so a merge is a deploy), then the removal commit on `main`: `git rm -r ledger reports docs spud.config.json`, the already-stripped `.claude/settings.json`, the new `CLAUDE.md`; push. History keeps the files. `.claude/settings.local.json` (written by `project install spud`) stays untracked.
14. `spud --as spud report add "Home moved to /Users/ericlugo/Personal/SpudHome; removal commit <sha> landed" --next "SPD-098 under the new flow"`. The watcher renders it into the vault within seconds; nothing to commit.
15. Once Eric is satisfied, `.spud-moved` in the tool repository is his to delete.

**C. Rollback, until the removal commit**

Write `/Users/ericlugo/Personal/Spud` into `~/.config/spud/home`, rename `.spud-moved` back to `.spud`, and with `SPUD_HOME=/Users/ericlugo/Personal/Spud` run `spud --as spud settings sync`, `spud --as spud project install badtakes`, `spud --as spud schedule install`; then `git checkout -- .claude/settings.json` in the tool repository. Ledger writes made in the new home after step 9 exist only there.

**D. Related tickets**

SPD-092 closes with this ticket's test guard (Task 1). SPD-083 is rechecked after the split. SPD-033's native deny rules follow the new state directory path, `/Users/ericlugo/Personal/SpudHome/.spud/`.

---

# Self-review against the spec

| Spec                                                                                                                                        | Where in this plan                                                                                                     |
| ------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| 2: `resolve_home` tries SPUD_HOME, then the pointer, else fails naming both; no git fallback                                                | Task 2                                                                                                                 |
| 2: hook lines keep `SPUD_HOME=<home>` and run the tool's main checkout's `bin/spud`                                                         | Task 4 (`ctx.launcher`), the worktree warning, the doctor `tool` line (Task 8)                                         |
| 2: `session show`, the path rule, `settings sync`, `schedule install`, `doctor` on a plain-directory home                                   | Tasks 6, 7, 4, 14, 8 and 15; every test home is a plain directory                                                      |
| 2: `ledger commit` removed with every message, hint and template line                                                                       | Task 9, and the claim card in Task 7                                                                                   |
| 2: the home is not a project; project `spud` re-registered rooted at the tool, sessions `claim`, landing `merge`; key `home` reserved       | Tasks 6 and 8 (`sync_config_rows`, `project edit`, `check_project_key`); `sessions claim` set by `home move` (Task 18) |
| 3: bare, `<key>:` and `home:` globs; `spud:docs/…` becomes `home:docs/…`                                                                    | Task 6 (`check_deliverable_projects`, `path_reason`), Tasks 8 and 10 (tests, probe, `spudagent.md`)                    |
| 3: Law 5 in the home; Spud's own files are the home's                                                                                       | Task 6 (the home row carries `GENERATED_ROOTS` and `SPUD_PATHS`)                                                       |
| 4: `render --watch`, 2 s, the highest non-render event id against the last render's `through_event_id`                                      | Task 13                                                                                                                |
| 4: `local.spud.render`, RunAtLoad, KeepAlive, `<home>/.spud/logs/render.log` truncated at each start; `schedule install\|show` handles both | Task 14, `truncate_log` in Task 13                                                                                     |
| 4: a render that changes nothing writes nothing                                                                                             | Task 12                                                                                                                |
| 4: a conflict event once per path and on-disk sha256; `doctor` lists open conflicts with both commands                                      | Tasks 12 and 15                                                                                                        |
| 4: one render at a time, `<home>/.spud/render.lock`, a manual render waits                                                                  | Task 12                                                                                                                |
| 4: `doctor` and `board --brief` say when the watcher is down                                                                                | Task 15 (down = installed and not alive; never installed is a note)                                                    |
| 4: hooks unchanged                                                                                                                          | `HOOK_PATH` untouched; Task 20 times them                                                                              |
| 4: measure a no-change pass and a full pass over the current ledger                                                                         | Task 16 (synthetic), runbook step 6 (the real ledger)                                                                  |
| 5: the command, its preconditions, its nine steps, `--dry-run`, the rollback                                                                | Task 18; runbook B and C                                                                                               |
| 7 (tool side): `spudagent.md` and the `/spud` skill lose every mention of `ledger commit` and of the ledger root as a checkout              | Task 10 and Task 4 (`skill_markdown`)                                                                                  |
| 9: the real home off limits, the suite fails if it is opened                                                                                | Task 1 (`GUARD_HOME`)                                                                                                  |
| 9: the resolution order                                                                                                                     | Task 2                                                                                                                 |
| 9: a plain-directory home under `session show`, `settings sync`, `schedule install`, `doctor`                                               | Tasks 7, 4, 14, 8                                                                                                      |
| 9: `home:` globs and the reserved key                                                                                                       | Tasks 6 and 8                                                                                                          |
| 9: a no-change render writes nothing; the watcher once after an event, not after its own; a conflict logged once; the render lock           | Tasks 12, 13                                                                                                           |
| 9: `home move` `--dry-run`, each precondition, a full move with the zero-write render, a rollback                                           | Task 18                                                                                                                |

Placeholder scan: every code step carries its code, every test its assertions, and no step defers to another task's text; the only match for the skill's red-flag words in this file is this sentence. Names used across tasks were checked against their definitions: `ctx.launcher` (2, used 4, 14, 18), `render_pass`/`render_lock` (12, used 13, 15, 18), `install_agents`/`agent_plist_path` (14, used 13, 18), `doctor_report`/`WATCHER_DOWN` (15, used 18), `home_row`/`is_home` (6, used 7, 8), `tool_warning` (4, used 14), `make_tool`/`setup_launchd`/`GUARD_HOME` (1, used 2, 4, 6, 15, 18).
