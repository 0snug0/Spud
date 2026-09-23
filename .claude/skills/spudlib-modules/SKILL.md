---
name: spudlib-modules
description: The rules the spud program's package bin/spudlib/ was built on. Read this before writing or changing any code under bin/ — adding, moving, splitting, renaming or growing a module under bin/spudlib/, editing bin/spud or bin/spud_ledger.py, touching anything a hook imports, adding an import, or judging whether a file has grown too large. Covers the import rule (a module imports modules, never names) and why a name imported by value breaks the suite's patching, where a new module goes among the nine directories, what must stay off the hook path and how to time a change to it, the launcher's `python3.14 -I -S` loader and its bytecode cache, the one guarded import cycle, module naming, and the size rule (~250 lines is a look-again point, never a cap).
---

# The spud program's package

`bin/spud` is the only program that writes the ledger. Since SPD-065 its code is a package:

```
bin/
  spud               the launcher: loads the entry by path, with today's bytecode rule
  spud_ledger.py     the entry: the finder, the public surface, HookCall, main, the refusal
  spudlib/           the program in nine directories, no __init__.py
    core/      kernel · lazy · markdown · homeconf · launchagents · shipped
    state/     schema · ledgerdb · lookup · actors · ops · transcripts · backup
    render/    prices · teamcard · workedon · sectiontext · notefiles
    imports/   noteimport · bulkimport · accept
    commands/  reportentry · admincmds · vaultlock · vaultinstall · homesync · ghread · prcmds · doctor · schedule · settings_sync · publish · homeinit · ticketcmds · proposalcmds · membercmds · resumcmd · views · homemove · renderwatch · worktreebind · vaultcapture
    projects/  sessions · registry · install · agentdef
    hooks/     hookio · worktrees · pathrule · gitrepos · snapshots · pretool · recording · subagent_stop · sessionhooks · stophook · dispatch
    shell/     syntax · prepare · heredocs · assignment_words · zsh · directories · git_verbs · git_programs · git_config · spud_calls · globbing · expansions · reevaluation · positional · runtime_shells · walk · analyse · redirect_globs · inline_programs · interpreter_words · bash_rule · arg_writes · tree_walk · tree_writes · find_xargs · spelled_writes · downloads · script_text · stdin_text · script_files · runner_files · script_runners
    cli/       helptexts · cliparser
```

It was one file before SPD-065, and `tests/test_package.py` is the guard test that keeps the shape. Everything below is a constraint that bites: breaking one of them either fails the suite, costs every hook run milliseconds, or makes a test pass by luck.

## 1. A module imports modules, never names

At the top of the module, and only there:

```python
from . import walk, zsh                 # its own directory
from ..state import ledgerdb, lookup    # another directory
from ..core import kernel
```

and at the call site, `ledgerdb.connect(ctx)`, `kernel.now()`. Never `from ..state.ledgerdb import connect`.

Three things depend on it.

**The suite patches at the call site.** `mock.patch("spudlib.state.backup.do_backup", ...)` reaches every caller because each caller reads `backup.do_backup` when the line runs. Import the name by value and each importer gets its own copy bound at import time: the patch on the defining module then changes nothing where the call is, the test exercises the real function and passes for the wrong reason. A test patches `spudlib.<group>.<module>.<name>`, never `spud_ledger.<name>` and never an attribute of the module `load_spud_module()` returns — a write there lands on the entry, which no call site reads. `PatchTargetTest` fails the suite if a test does.

**A cycle survives.** When `from . import walk` runs while `walk` is itself mid-import, Python hands back the partly initialised module. A peer's name read inside a function body is complete by the time the function runs (see §5).

**Module-global state keeps one owner.** `core/lazy` rebinds its own globals; `_GIT_OWN_COMMANDS` lives in `shell/git_verbs`, `_WORKTREES` and `_CASE_CACHE` in `hooks/worktrees`, `ACTIVE_CTX` in `state/actors`, written only by the entry's `main` with `ACTIVE_CTX[:] = [ctx]`. No module ever stores an attribute of another module.

Also:

- **No import inside a function.** It hides the edge from the guard test. The one exception is `hooks/dispatch`, which imports its event's handler module with `importlib` on purpose, to keep a hook's import set small — do not copy it elsewhere, and do not "simplify" it away.
- **Standard-library imports are restated per module**, each module importing only what it uses.
- **The five lazy modules stay lazy.** `argparse`, `fractions`, `hashlib`, `subprocess` and `tempfile` are reached as `lazy.subprocess.run(...)` through `core/lazy`. Importing one at a module's top puts 11 ms back on every hook run (SPD-016).

## 2. Where a new module goes

| Directory | What belongs in it | It imports modules of |
| --- | --- | --- |
| `core/` | shared types and pure utilities: exit codes, statuses, state machines, `SpudError`, the clock, `Result`, the markdown-v0 parser, home resolution and `Ctx` | `render` (only `homeconf` → `prices`, for `Ctx.pricing`) |
| `state/` | the database and the domain: schema, connections, row lookups, actors and ownership, domain operations, transcript sums, backups | `core`, `hooks` (only `actors` → `worktrees`) |
| `render/` | rows to the markdown Eric reads: cost, the Team card, the worked-on sentence, section bodies, whole notes | `core`, `state` |
| `imports/` | markdown back into rows: the v0 tree import, and `import --file` accepting a hand edit | `core`, `render`, `state` |
| `commands/` | one module per command family; each command is `cmd_<name>(ctx, args)` returning `kernel.Result` | `core`, `hooks`, `imports`, `projects`, `render`, `state` |
| `projects/` | registered projects: the registry, install and uninstall, session mode and claims | `commands`, `core`, `hooks`, `state` |
| `hooks/` | the seven hook events' handlers and their plumbing: the spool, worktrees, the path rule, the repository reading, dispatch | `core`, `projects`, `shell`, `state` |
| `shell/` | the Bash rule's reading of a command line: tokens, zsh globs, git verbs, expansions, the walk | `core`, `hooks`, `state` |
| `cli/` | `build_parser`, `normalize_argv`, the help texts. Nothing imports `cli` but the entry | `commands`, `core`, `hooks`, `projects`, `state` |

Two habits decide most placements:

- **Put a name with its users, not with the banner it came from.** `token_counts` reads like render, but cost and the Team card both use it, so it is in `render/prices`; `board_brief_text` reads like a command, but `SessionStart` injects it, so it is in `projects/sessions`.
- **A name two layers both need goes to the lower layer.** If a hook module and a command module both call it, it belongs in `core/` or `state/` — never in `commands/`, which no hook may import (§3).

A new module is a new file in one of these directories. Nothing registers it: no `__init__.py`, no list to edit. The guard test asserts every module is reachable from the entry's imports, so a module nothing imports fails the suite.

## 3. The hook path

Every Bash, Edit and Agent call in every session runs `spud hook PreToolUse`, and `SessionStart`, `Stop`, `PostToolUse`, `SubagentStart`, `SubagentStop` and `UserPromptSubmit` run on their own events. A hook run is a whole process: about 23 ms, and every module it imports is part of that.

`hooks/dispatch.HOOK_HANDLERS` maps an event to `(module, handler)` and imports that module only when it calls it, so a hook run imports its own event's modules alone.

`HOOK_PATH` in `tests/test_package.py` is the **exact** set of modules the seven hooks import between them. The test runs each hook through the launcher and compares. So:

- **To keep a new module off the path** (the default, and what you want): let nothing in `hooks/`, `shell/`, `state/`, `core/`, `projects/sessions` or `render/prices` import it. A module reached only from `commands/` or `cli/` is off it.
- **To put one on it** is a reviewed edit: add it to `HOOK_PATH`, and run the timing probe below against `main` before you land.

**What Eric's decision 2 (SPD-065) keeps off the path, permanently:** the parser, every `commands/*` module, `imports/*`, `projects/install`, `projects/registry`, `commands/schedule`, `commands/doctor`, and `render/*`. The one exception, on the record: `render/prices` is on the path because `core/homeconf` reads it for `Ctx.pricing`.

```bash
main=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")   # the main checkout, wherever this machine keeps it
python3.14 -I -S tests/probes/hook_timing.py 30 "$main/bin/spud" "$PWD/bin/spud"
```

Main's launcher first, your branch's second; each gets its own scratch home, so no ledger is touched. **The pass: every hook case's median within 1 ms of main's in the same run.** A command case (`board`) may move more.

One more property worth not breaking: a handler module that fails to import is spooled and `PreToolUse` fails closed, because `cmd_hook` imports it inside its own `try`. Importing handlers eagerly to "simplify" dispatch would both cost the path and lose that.

## 4. The launcher, the finder, and where bytecode goes

- Everything runs as **`python3.14 -I -S`**: isolated, no `site`, nothing on `sys.path` but the interpreter's own. **Standard library only** — there is no third-party dependency and there will not be one — and no directory of this repository is importable by name.
- **`bin/spud` is the launcher.** It loads the entry by file path with a `SourceFileLoader` whose bytecode is written under `<home>/.spud/pycache/`, mirroring the file's real path, and only when that directory already exists. The hooks, the allow rules, `settings sync` and `project install` all name `bin/spud`; nothing else is an entry point.
- **The entry installs `PackageFinder` on `sys.meta_path`.** It answers `spudlib` and `spudlib.*` from the directory beside the entry's **real** path and returns `None` for every other name, so no module here can shadow the standard library or be shadowed by it, and a checkout reached through a symlink still loads its own code. Each module gets a loader of the class that loaded the entry, which is how the launcher's cache rule reaches every module and the suite's plain loader writes none.
- **Running `bin/spud_ledger.py` directly refuses** with exit 2 and imports nothing. Keep that.
- **Never** add a `sys.path` entry, an absolute `import spudlib`, an `__init__.py`, or a file outside `bin/spudlib/` that the program imports.
- **The repository ends every run with no bytecode.** `tests/suite.py` never writes any: it sets `sys.dont_write_bytecode` before it imports a test, runs the suite in a scratch copy of the tree, and only its own process removes that copy, so no two workers race on a cache directory (SPD-083, SPD-102). The serial command cannot stop its first test module and `tests/helpers.py` from being cached before the flag is set, so `tests/helpers.py` removes those caches at its exit. A one-off script that loads the program sets the flag first. After either, `find bin tests -name '*.pyc' -o -name __pycache__` prints nothing.
- **The suite warms each scratch home's cache.** A `SpudTestCase` home starts with the program already compiled into its `.spud/pycache/` (hard links to one cache per run, built by `helpers.compile_program` the way the launcher writes it), so `spud init` and the first command skip the compile. The classes that assert the launcher's own caching, `init` or backups set `warm_cache = False` and start empty; `tests/suite.py --cold` starts every home empty.

## 5. The one import cycle

These `shell/` modules are one strongly connected component: `analyse`, `arg_writes`, `bash_rule`, `directories`, `downloads`, `expansions`, `find_xargs`, `git_config`, `git_programs`, `git_verbs`, `globbing`, `inline_programs`, `interpreter_words`, `redirect_globs`, `reevaluation`, `runner_files`, `runtime_shells`, `script_files`, `script_runners`, `script_text`, `spelled_writes`, `stdin_text`, `tree_walk`, `tree_writes`, `walk`. That is real recursion — `analyse_words` builds a `ShellWalk` whose segments call `analyse_words` again — not tangled layering, and breaking it would take either one large module or function-local imports that hide the edges.

It is safe under one condition the guard test enforces: **no module reads another module's name while that module is being imported, unless the module read cannot reach the reader.** In practice, a cross-module read belongs **inside a function body**. These run at import time and can fail depending on which module a run imports first:

- a default argument (`def analyse_segment(..., redirect_cwds=syntax._CURRENT)`),
- a module-level constant built from a peer's table (`GLOB_SAMPLES`),
- a top-level call of your own function that reads a peer.

That is why `_CURRENT` and the git flag tables live in `shell/syntax`, which imports nothing of the package: the import-time reads that do exist all point at a module that cannot reach back.

**A second cycle is a smell, not a precedent.** Two directories importing each other means a shared name is sitting too high; move it down a layer, into `core/` or `state/`. Do not reach for a function-local import — it hides the edge from the test that would have caught the problem.

## 6. How large a module may be

Eric's rule, exactly (SPD-065, 2026-09-15):

> ~250 lines is the point at which a module is worth a second look, never a cap. A module may be as large as it needs to be, and the larger it gets the more it must justify itself. No cohesive function, class or region is ever cut to fit a number.

Scope is application code — Python here, and the same in another project. Tests, stylesheets and HTML are out.

```bash
python3.14 -I -S tests/probes/module_sizes.py        # advisory; it always exits 0
```

It bands what it finds (250 and over, 1000 and over — the size that started SPD-065 here and BAD-036 in BadTakes) and names each file's largest top-level definition and that definition's share, which is the evidence the rule asks for. It is a report and never a gate: no band is a failure and nothing depends on it.

When a module passes 250, the argument takes one of these shapes:

- **Keep whole** when the file is one class whose methods share state (`shell/walk`), one function and its way in (`shell/analyse`, `cli/cliparser`), one scanner (`shell/zsh`), one body of data read as one (`state/schema`), or one pair that drifts apart if separated (install and uninstall; parsing and emitting frontmatter).
- **Take the seam** when the file is a list of things and some caller wants only part of it — and then the seam is a real one, with its own name and its own users, not a page break.
- **Write the reason down** where the next reader will look: the module's docstring, or the ticket.

When you do split, nothing above changes: the new module still imports modules, still goes in the directory its users make sense in, and still has to stay off the hook path unless a hook needs it.

## 7. Module names

- **Unique basename across the package**, so `ledgerdb.connect` means one file wherever it is read.
- **Never a name any local variable, parameter or comprehension uses anywhere in the program** — a function with a parameter `db` would shadow a module imported as `db` inside it. That is why the modules are `ledgerdb` (not `db`), `lookup` (not `rows`), `ops` (not `domain`), `transcripts` (not `usage`), `prices` (not `cost`), `worktrees` (not `checkouts`), `stophook` (not `stop`), `globbing` (not `globs`) and `cliparser` (not `parser`). The guard test checks it.
- **A one-line docstring naming the directory and the module**, in the package's voice: `"""shell/walk: ShellFrame and ShellWalk: one pass over a line's tokens."""`

## 8. What the guard test already checks

`tests/test_package.py`, parsing every file — imports the package relatively, names only modules that exist, imports only at the top; every name defined in one module; no module binds a name it imports a module as; no import-time read across a cycle; every handler a function of its module; every module reachable from the entry; each hook's import set inside `HOOK_PATH`, and together equal to it; and no test patching the loaded program.

```bash
python3.14 -I -S -m unittest discover -s tests -t tests -p test_package.py   # seconds, while you work
```

Its reach has known gaps (SPD-079): it does not see an alias shadowed by a nested `def` or an `except … as`, nor an import-time read reached through a function call. Those are still violations of §1 and §5 — the test simply will not catch them for you.

## 9. Before you call the work done

1. `python3.14 -I -S tests/suite.py` — the whole suite on every core, about 80 seconds; run the modules you touched by name while you work (`tests/suite.py test_package test_hooks`) and the whole suite once at the end, and put its final line, with the tree's digest, in your result. The serial fallback, `python3.14 -I -S -m unittest discover -s tests -t tests`, takes about eleven minutes, prints no digest, and wants the checkout to itself.
2. `find bin tests -name '*.pyc' -o -name __pycache__` prints nothing.
3. If you touched the hook path: `HOOK_PATH` updated, and `tests/probes/hook_timing.py` within 1 ms of main.
4. If you changed how the program is loaded or how a hook answers: `python3.14 -I -S tests/probes/session_diff.py "$main/bin/spud" "$PWD/bin/spud"` — main's launcher (`$main` as in §3) and this worktree's, scripted CLI and hook calls against both, every step identical after masking.
5. If you added a module or grew one: `tests/probes/module_sizes.py`, and the reason written down.
6. If a comment or test you wrote says what a shell does: a live probe behind it, named where the claim is — which shell, which options, what it printed. A member in a worktree is refused every command that runs zsh or bash, so it asks through the probe runner, which runs the snippet in the three shells the `shell/` comments cite, sandboxed so that it can run no git and write nothing outside its own directory:

   ```bash
   python3.14 -I -S tests/probes/shell_probe.py snippet.sh   # zsh -f -o nobareglobqual, zsh -f, /bin/bash; or the snippet on stdin
   ```

   Pass the snippet as a file (a scratchpad path) when its text names git: the Bash hook reads a here-document fed to the probe and refuses one that does. Cite the versions it prints with what it printed.
