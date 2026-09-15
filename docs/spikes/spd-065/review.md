# SPD-065 review: Atlantic's Phase 2 split of `bin/spud_ledger.py`

**Verdict: Approve with follow-ups.** The split is the approved spike, built faithfully, and I found no behaviour change. Nothing on this branch needs a change before the merge. The follow-ups are about the guard test's own reach, not about the program: filed as a proposal, not fixed here.

*2026-09-15 · [[SPD-065]] · Cherie (05, reviewer, opus) reviewing Atlantic (04)'s uncommitted work in the worktree `spd-065-split`, against `docs/spikes/spd-065/spud-ledger.md` and main at `7abae09`. Read-only on code and tests; every check below is my own, written fresh and run outside the repository.*

## What the claim is, and what I ran

The claim is zero behaviour change. I tested it six ways, and every tool below is mine, not Atlantic's or the spike's prototype's:

| # | Check | Tool (scratch, not committed) | Result |
| --- | --- | --- | --- |
| 1 | Statement accounting, and where SPD-076's four names went | `account.py`: AST plus a token-level unqualifier, byte for byte | 712 of 731 statements identical; the other 19 accounted for |
| 2 | Qualification hazards | `account.py` (`symtable` scopes, alias shadowing, cross-module stores), `mutables.py` | 0 problems |
| 3 | Import graph and each hook's import set | `account.py` (Tarjan), `hook_imports.py` (`-X importtime` and `sys.modules` at exit, main against the branch), `breakage2.py` | one cycle, as designed; hook sets as the spike says; edit 7 fails closed |
| 4 | Loader, bytecode, self-recognition | `loader_check.py` (scratch checkouts, one reached through a symlink), `diff_corpus.py` | all pass |
| 5 | Tests and probes | reading the diff, `breakage.py` (13 damaged copies) | no assertion weakened; the guard catches what it promises, with gaps |
| 6 | Atlantic's evidence, re-run | `tests/probes/session_diff.py`, `tests/probes/hook_timing.py` | reproduced |

I did not run the full suite (Spud does) and did not run the headless probes (they cost API usage).

## 1. Accounting, and the placement of SPD-076's four names

`account.py` parses main's `bin/spud_ledger.py` at `7abae09` (11133 lines, 731 top-level statements) and every module of `bin/spudlib/`. For each module it builds the alias map from the relative imports, removes every `alias.` qualifier **at the token level** (so a qualifier inside a string or a comment would survive and show up as a difference), and matches each module statement against a main statement **by its exact text**, comments included.

- **712 of 731 statements match byte for byte.** The remaining 19 are: the module docstring (amended, as the spike prescribes), the 13 standard-library import statements (restated per module), edit 7's two statements (`HOOK_HANDLERS` and `cmd_hook`), and the three the entry keeps (`HookCall`, `main`, the refusal).
- **Line multiset:** main's non-blank lines that appear in no module: 86, of which 73 are the entry's. The 13 left are the three amended docstring lines, edit 7's 9 lines and `sys.exit(EXIT_USAGE)`, which became `sys.exit(2)` with the constant named in a comment (spike §2.3: naming it would import `core/kernel`; `EXIT_USAGE` is 2). Module lines that are not on main: 10, all of them edit 7's. Nothing else was added, reworded or dropped.
- **Names:** 733 names bound at main's top level (8 of them module-level comprehension variables, which are attributes of neither main's module nor the entry). Each is bound in exactly one module; none is bound twice; none is missing; no module binds a name main does not have. `HookCall` and `main` stay in the entry.
- **Entry:** `HookCall`, `main` and the refusal differ from main only by the function-local `from spudlib… import` lines, one blank line each, and `sys.exit(2)`.
- **The public surface:** loading the entry alone imports **no** module of the package; reading all 733 names through `__getattr__` imports 58 and resolves every one (`entry.now is kernel.now`; a patch of the defining module reads through the entry; `__all__`, `__path__` and an unknown name raise `AttributeError`). The 8 comprehension names raise `AttributeError` on the entry **and on main's module**, so the surface is exact, not merely close.
- **1709 references qualified** (the spike counted 1706 before SPD-076).

**SPD-076's four names.** `IMPORT_MEMBER_SECTIONS` and `owned_headings` to `core/markdown`, beside `split_document`, which is their only reader besides `imports/noteimport`: right, and what the brief asked.

`check_prose_headings` and `proposal_brief` went to `state/ops`, not beside `text_arg`. **I judge that correct, and not merely the lesser evil.** `text_arg` lives in `cli/cliparser`, and `cli` is the top of the graph: nothing imports it but the entry, while these two are called from `state/ops.create_ticket_for_proposal`, `commands/ticketcmds`, `commands/membercmds`, `commands/proposalcmds` and `imports/accept`. Putting them in `cliparser` would make `state`, `commands` and `imports` import `cli`, which imports all of them back: Atlantic's measured 26-module cycle. `core/markdown` would work for the graph but would pull `markdown` onto the hook path, because `ops` is on it. `state/ops` keeps the spike's graph exactly. The only cost is that the two functions are not "domain operations" in the sense of that region's banner, and Atlantic extended the module docstring to say so; that is honest and enough.

## 2. Qualification hazards

Each class, what I searched, and what I found. All of it is over the built tree, with `symtable` for scope questions rather than a grep.

| Hazard | How I looked | Found |
| --- | --- | --- |
| A local, parameter, comprehension variable or `global` shadowing a module alias | `symtable` over every scope of all 58 modules: an alias must never be local, parameter, free, assigned, nonlocal or declared global | none. The 55 aliases are disjoint from main's 733 program names, from its 13 standard-library import names, and from the builtins |
| A qualification that was a **local** on main | for every `alias.X`, walk the enclosing function and comprehension scopes: if `X` is a parameter or is assigned there, the unqualified name was local on main and the qualifier changes meaning | none, over all 1709 |
| A name that should have been qualified | every implicit-global reference must be bound at that module's top or be a builtin | none. Also checked the nastier variant: no program name on main is also a builtin, so a missed qualifier could not resolve silently to `format`, `id` or the like |
| String lookups (`globals()`, `getattr` on a module, `HOOK_HANDLERS`, `set_defaults(func=…)`, `EVENT_KINDS`, `__name__`, `__module__`, `repr`, pickling) | grep over main plus the qualified-reference check | `globals()` appears once, in `LazyModule`, and it rebinds `core/lazy`'s own globals, where the 33 `lazy.<name>` call sites read it; `getattr` is used on `args`, on `sqlite3` and by edit 7; `set_defaults(func=…)` passes function objects, now `module.name`, read when `build_parser` runs; `__name__` only as `type(e).__name__`; no `__module__`, `__qualname__`, `repr` or pickling of program objects reaches any output |
| A default argument or decorator evaluated at import | AST scan of decorators, defaults and every statement outside a function body | the only import-time reads are the seven the spike names (listed in check 3); decorators are `property`, `contextlib.contextmanager` and one `functools.lru_cache(maxsize=512)`, each on a function of its own module |
| `LazyModule` rebinding | read `core/lazy`; searched every module for a top-level `import argparse|fractions|hashlib|subprocess|tempfile` | none: the five stay lazy, and `hook_imports.py` confirms no hook run imports them |
| Every `global` statement | AST | one: `_GIT_OWN_COMMANDS` in `shell/git_verbs`, written and read by `git_own_commands` in that module |
| Mutable module state read from two modules | `mutables.py`: every module-level container that any function mutates by method call, subscript store or `global`, including through a local alias of it | exactly the spike's §2.5 list: `_CASE_CACHE` and `_WORKTREES` (owner and only reader `hooks/worktrees`), `_GIT_OWN_COMMANDS` (`shell/git_verbs`), `glob_sample_matches`'s cache (its own function object). `ACTIVE_CTX` is mutated only by the entry's `main`, by slice assignment on the one list. No module ever stores or deletes an attribute of another module (`alias.X = …`): zero occurrences |

## 3. Import graph and the hook path

- **Graph:** 283 top-level edges (282 before SPD-076's extra call), one strongly connected component: the ten shell modules `analyse, bash_rule, directories, expansions, git_config, git_programs, git_verbs, globbing, redirect_globs, walk`. Every other module is acyclic.
- **Import-time reads:** `hooks/hookio` reads `core/kernel`, `hooks/sessionhooks` reads `projects/sessions`, `shell/globbing` reads `shell/syntax` and `hooks/hookio`, and `shell/analyse`, `shell/expansions` and `shell/redirect_globs` read `shell/syntax`. For each, I computed whether the module read can reach the reader through top-level imports: none can. So no import order can fail, whichever module a run imports first.
- **Each hook's import set,** measured my own way — `sys.modules` at exit (`-X importtime` does not log edit 7's `importlib.import_module`) — for 24 payloads against **both** launchers in their own scratch homes:

| Run | Modules of the package | Off-limits modules (render but `prices`, imports, commands, cli, `projects/install`, `projects/registry`) |
| --- | --- | --- |
| PreToolUse (Bash, a spud call, a redirect, Write, Edit, Agent, `--project`) | 32 | none |
| PostToolUse, SubagentStart, SessionStart, UserPromptSubmit | 16 | none |
| Stop, Stop `--project` | 15 | none |
| SubagentStop | 34 | none |
| a malformed payload | 13 | none |
| `board`, `render`, `doctor`, `--version`, `init`, `ticket new` | 38 | (commands and the parser, as expected) |

  `render/prices` is the one `render` module a hook imports, through `core/homeconf` (`Ctx.pricing`), exactly as the approved spike's §2.1 table marks it. No hook imports the parser, `commands/*`, `imports/*`, `projects/install`, `projects/registry`, `commands/schedule` or `commands/doctor`.
- **Standard library:** for every one of the 24 runs, the branch imports **no** standard-library module main does not. It imports fewer: `bisect` never, and `shlex` only where a command line is analysed. That, not the module count, is where Stop's and SessionStart's −2 ms come from.
- **Output parity:** all 24 runs, stdout, stderr and exit code, identical after masking the home and the clock.
- **Edit 7 fails closed.** With `hooks/pretool.py` (a) raising at import, (b) not compiling, (c) deleted, `PreToolUse` answers exit 2 `… (failing closed)` for Spud and for a member, `Stop`, `Stop` with `stop_hook_active`, `SessionStart` and `SubagentStop` answer exit 0, the error is spooled to `hook-errors.jsonl`, and `PreToolUse --project spud` with no `agent_id` fails open with the same wording. Main, with its handler function raising, answers identically in every row. A module the **entry** imports (`core/kernel`) breaking gives a traceback and exit 1 — which is what main does when its one file breaks, so that case is unchanged too; a **shell** module breaking now fails closed where main's break failed open, which is stricter, not weaker.

## 4. Loader, bytecode cache and self-recognition

In scratch checkouts outside the repository (`loader_check.py`), with `spud init` only in scratch homes:

- **The finder answers only its own package,** from the directory beside the entry's real path: `None` for `json`, `os.path`, `spud_ledger`, `spudlibx`, `spudlib_x`, `xspudlib`; one finder, first on `sys.meta_path`; modules load with the launcher's `CachedLoader`.
- **Through a symlink:** a checkout reached as `linkA/bin/spud` (a symlinked directory) and as `lonely/spud` (a symlinked launcher file) loads every module from the real directory, and caches under the mirror of the **real** path. No mirror is ever created under the symlink's path.
- **With `SPUD_HOME` set:** 58 `.pyc` files, all under `<home>/.spud/pycache/<mirror of the real bin>`; each maps to an existing source; nothing under `bin/`; no standard-library bytecode in the cache.
- **With no state directory:** `--version`, `board` and `--help` create and change **no file at all** under the scratch tree (I compared the full file list with mtimes before and after). Running the entry directly exits 2, says to run the launcher, prints nothing on stdout and writes nothing. With the checkout's own `.spud/` present, 39 `.pyc` land there and none under `bin/`.
- **Two checkouts, one home:** each process loads only its own package; the caches are separate mirrors; an edit to A's `core/kernel` shows in A's `--version` and not in B's, and reverting it recompiles rather than serving stale bytecode.
- **In process, the suite's way:** loading the entry with a plain `SourceFileLoader` under `sys.dont_write_bytecode` and calling `owners()` (all 58 modules) writes **no** `__pycache__` under `bin/`; loading root A, then B, then A again gives one finder at a time and each root's own modules.
- **Self-recognition:** `diff_corpus.py` ran main's program and the split in one process against one scratch home whose `bin/` holds the split, over **5576 Bash payloads** (2788 distinct command lines: every string constant of `tests/test_hooks.py` with the home substituted, plus lines written for this review — the launcher vouched and unvouched, `SPUD_HOME=` assignments pointing at the home and elsewhere, the entry and a module run directly, reads of `.spud/pycache`, `cd` into `bin/spudlib`) and **1116 path payloads** (186 paths × Write, Edit, NotebookEdit × Spud and a member, including `bin/spudlib/state/ops.py`, `bin/spudlib/shell/walk.py`, a cached `.pyc` under `.spud/pycache/…/bin/spudlib/…`, `.spud/pycache` itself and the database). **Zero differences** in exit code, stdout or stderr. So `spud_launcher`, `launcher_vouched`, `interpreter_vouched`, `spud_home_vouched`, `DB_PATH_RE` and `in_state_dir`/`edit_reason` all answer as they do today, including over the new files and the new bytecode paths.

## 5. Tests and probes

- **No assertion is weakened.** `test_launcher`'s bytecode test used to assert one cached file named `spud_ledger…pyc`; it now asserts the set is under the mirror, contains the entry **and** `spudlib/core/kernel.py`, that every cached file maps to an existing source, that **all** of them keep their mtime on a second run (main checked only the one), and that `cached_programs(REPO / "bin")` is empty — a recursive glob, so it now also covers `bin/spudlib/**`. `WithoutSpudHomeTest` swaps an exact one-element list for "the entry is among them and all are under `.spud/`", plus the existing "none under `bin/`": the same statement about a set that is now larger. `test_init` moved from `bin/__pycache__/*` to a recursive `bin/**/*.pyc` and now imports every module in-process first — strictly stronger. `helpers._remove_bytecode` adds the package's caches to the tidy-up it already did for `bin/__pycache__`.
- **The `test_backup` patch targets still test what they tested.** `commands/schedule.daily_backup` reads `backup.do_backup` and `backup.backup_stamp` at the call site, so the string targets inject the fault. I proved it by putting the three lines back to `mock.patch.object(self.spud, …)` in a scratch copy: exactly the 5 tests (7 subtests) the spike predicts fail, and `test_package`'s `PatchTargetTest` fires as well.
- **The guard test asserts what the spike and Q5 say, and it bites.** I built 9 damaged copies; every one fails, each in the test that owns it: an import naming no module; a name defined twice; an unreachable module; a handler that is not a function of its module; `hooks/stophook` importing `commands/views`; an import-time read across the shell cycle; an import inside a function; a test patching `spud_ledger.<name>` by string; a test patching the loaded program object.
- **The probes do what their docstrings say.** `hook_timing.py` gives each launcher its own scratch home, warms up three rounds and interleaves; `session_diff.py` runs its 44 steps with `cwd` set to the scratch home and **exits 1 on a difference** — I checked by changing one reason string in a copy: `steps 44, identical after masking 42`, exit 1. The `headless.py` fix is the copy the spike §4.2 names (the entry and the package beside the launcher, `__pycache__` ignored), and `headless_projects.py` gets the same one line. I did not run either headless probe.
- **Hygiene:** `bin/spudlib` is not gitignored (I checked with `git check-ignore`), holds only `.py` files, none executable, each with a one-line docstring; `bin/spud` keeps mode 755.

## Findings

Ranked. None of them blocks the merge.

**1 (low, follow-up). The guard test's reach is narrower than its own docstring.** It says "no module binds a name it imports a module as, in any scope", but it collects bound names from `Name` stores and parameters only. In scratch copies it passes with a nested `def walk(): …` inside a function of `shell/analyse`, and with `except Exception as syntax:` inside a function of `shell/globbing` — both of which would shadow a module alias at run time. Its cycle check reads only `alias.name` written outside a function body, so it passes when the read is reached through a function called at import (`def _probe(): return walk.ShellWalk` with `_PROBE = _probe()` at the top of `shell/analyse`). `PatchTargetTest` passes when a test patches through an alias (`m = spud; mock.patch.object(m, "now", …)`). Reproduction: each of the four is one edit in a copy, then `python3.14 -I -S -m unittest discover -s tests -t tests -p test_package.py`. Smallest fix: use `symtable` for the binding question (it sees every binding form), and for the cycle question treat a top-level call of the module's own function as import-time. **The current tree has none of these**: my `symtable` pass over all 58 modules reports zero shadowed aliases, and the only import-time reads are the seven safe ones. So this is a guard for the next change, not a defect in this one. Filed as proposal 81; not fixed here.

**2 (informational). The suite writes no bytecode because `tests/helpers.py` sets `sys.dont_write_bytecode`.** A test process that imports the entry without that flag now leaves `__pycache__` in nine directories under `bin/spudlib/` instead of one under `bin/`. `helpers` sets the flag at import and now removes the package's caches at exit, and `test_init` asserts none are written in-process, so the suite is covered; a probe or a one-off script that loads the program itself is not. No action; worth knowing.

**3 (informational). Two deliberate departures from a literal "nothing changes".** The entry's docstring is rewritten (the spike prescribes it), and the refusal's `sys.exit(EXIT_USAGE)` is `sys.exit(2)` with the constant in a comment, so the refusal imports nothing. Both are in the spike; both are correct; the exit code is unchanged.

**4 (informational). `render/prices` is on the hook path.** Eric's decision 2 says render stays off it; the approved spike's §2.1 table nonetheless marks `render/prices` "hook path: yes", because `core/homeconf` imports it for `Ctx.pricing`. Measured, it is the only `render` module any hook imports, and the timing pass holds with room. Nothing to change; naming it here so the exception is on the record rather than discovered later.

## For Spud, at the landing

- The count for CLAUDE.md line 170 is **755** (748 on main plus the guard test's 7); line 19 and the timing-harness block are the spike's §8 wording. Atlantic correctly left CLAUDE.md alone.
- This review file is written in the worktree because the harness refuses writes to the home from a worktree session; it belongs on `main` beside the spike.
- Still outstanding after the merge, by the spike's §6.2: `spud doctor` and one session with hooks at the ledger root, and the two headless probes (they cost API usage).
