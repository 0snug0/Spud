# SPD-065 spike: splitting `bin/spud_ledger.py` into a package

*2026-09-15 · [[SPD-065]] · Russet (03, architect, opus), revising Burbank (02)'s one-namespace spike of `6d412b4` after Eric's decisions · Phase 1, planning only: nothing under `bin/` or `tests/` changed.*

Every line number below is `bin/spud_ledger.py` on `main` at `9e1deda`; `git log -1 --format=%h -- bin/spud_ledger.py` still prints it. The file is 11065 lines: 727 top-level statements binding 734 names, 13 of them standard-library imports and 721 the program's own. The move list, the import graph, the hook path and every count were computed from syntax trees by script. The prototype was built from the move list by script in scratch copies of the repository, never in it; §6 says how Phase 2 reproduces each check.

## The recommendation in ten lines

1. **A real package, `bin/spudlib/`**, beside the entry `bin/spud_ledger.py`: 58 modules in nine directories (§2.1). 719 of the program's 721 names move verbatim; `HookCall` and `main` stay in the entry, which becomes the orchestrator (191 lines).
2. **A module imports modules, never names:** `from . import walk` at its top, `walk.ShellWalk(...)` at the call. 1706 references change from `name` to `module.name`, mechanically; nothing else in a moved statement changes (§2.4).
3. **Cycles:** six moves of a name to another module leave one import cycle, the ten modules of the shell analysis, which recurse into each other by design. It is safe because no module on it reads a peer's name while being imported, and the guard test keeps that true (§2.4).
4. **A patch names the module that defines the name:** three lines of `tests/test_backup.py` (228, 264, 303) become `mock.patch("spudlib.state.backup.do_backup", ...)`. An entry that forwards writes also works and was run; it is not recommended (§4.2).
5. **`spud_ledger.<name>` reads as today** for every name the program defines: the entry's module `__getattr__` finds the defining module. `main` and `__file__` are the entry's own (§4.1).
6. **Loader:** the launcher's loader class keeps today's bytecode rule; the entry puts one finder first on `sys.meta_path` that answers only `spudlib`, from the directory beside it, and loads each module with the class that loaded the entry. Nothing lands under `bin/`, nothing is written without `<home>/.spud/` (§2.3).
7. **A hook imports its own event's modules only:** one code edit in `hooks/dispatch`, where the handler table names modules and `cmd_hook` imports the one it calls. PreToolUse imports 32 modules; Stop 15; no hook imports a command, render, import or the parser (§5).
8. **Latency, 30 rounds interleaved with main:** PreToolUse(Bash) +0.70 ms, PreToolUse(Write) +0.34, SessionStart −1.94, Stop −1.99, and the command `board` +0.40. That is inside the 1 ms pass. Without the dispatch edit the hooks run +0.6 to +1.1 ms and Stop misses it (§5).
9. **Behaviour:** the control suite ran 732 tests with 2 failures (SPD-076). The prototype with the listed test edits and the guard test: 738 tests (the guard test adds 6) with the same 2 failures and no other. A scripted session of 44 CLI and hook calls matches main's output after masking paths and times (§6.3).
10. **Phase 2** is eight commits in Eric's order, each leaving the suite green, with the guard test, `tests/probes/hook_timing.py` and the `headless.py` copy fix placed (§6). Thirteen modules sit over ~250 lines; each is one job (§7).

## What changed from Burbank's version

| Burbank (`6d412b4`) | This revision | Why |
| --- | --- | --- |
| One namespace: the entry executes 57 part files into its own globals | 58 real modules that import what they use | Eric's decision 1 |
| `bin/spud_ledger.d/`, loaded by path, not importable | `bin/spudlib/`, importable, found by a finder that answers only its own name | an `import` needs an identifier (§2.2) |
| Every call site unchanged | 1706 references qualified as `module.name`; six names moved to settle cycles | cycles and patch targets (§2.4) |
| Patches land by construction | Three test lines patch the defining module | §4.2 |
| A hook loads 34 parts; the rest load on first attribute read | A hook imports its own event's modules, 15 to 34; commands import no shell analysis | decision 2 and the latency pass (§5) |
| The launcher hands `_launcher_get_code` to the entry | The entry's finder reuses the class of the loader that loaded it (`__spec__.loader`) | no attribute set behind the module's back |
| Hook latency at parity (+0.0 to 0.4 ms) | +0.34 to +0.70 ms on PreToolUse, about −2 ms on Stop and SessionStart | measured (§5) |
| Guard test over definition order and the part lists | Guard test over imports, cycles, names, reachability and each hook's modules | §6.2 |

**Kept, and re-checked:** the responsibility map (§1); 47 of Burbank's 57 parts become modules holding exactly the same statements, renamed where a name was a local variable somewhere (§2.2); the suite survey (re-run: 43 attributes read, 2 names patched); the hook self-recognition table (§4.3); the timing harness, extended; the verification recipe (§6.2).

## 1. Responsibility map

The file's own banners (`# ----` headings) divide it into sixteen regions. "Depends on" counts references from the region's statements to names defined in another region, standard-library imports excluded. This is Burbank's table; every span was re-checked against `9e1deda`.

| Region | Lines | What it holds | Depends on (references) |
| --- | --- | --- | --- |
| Header: types and constants | 1-125 | docstring 1-17; stdlib imports 19-31; `LazyModule` 34-44 and the five lazy stdlib names 47-51; `VERSION`, `MARKER` 53-54; exit codes 56-62; enums 64-69; the two state machines 72-85; `EVENT_KINDS` 87-96; note layout keys 100-115; `SpudError` 118-125 | nothing |
| Time | 129-147 | `now` 133-135, `fm_date`, `fm_minute`, `backup_stamp` 146-147 | nothing |
| Home and config | 151-262 | `resolve_home` 155-177, `Ctx` 180-234, `config_problems` 237-262 | header 6, render 2 (`Ctx.pricing` and `config_problems` call `price_table`), projects 1 (`spud_config_dir`) |
| Database | 266-728 | schema `DDL_0001` 269-438, `VIEWS_AND_TRIGGERS` 440-479, `DDL_0002` 484-527; connections 533-565; backups 568-641; migrations 644-662; config rows 665-694; git subprocess helpers 697-720; `write_event` 723-728 | header 12, time 1 (`backup_stamp`), hook plumbing 1 (`GIT_REDIRECTS`) |
| markdown-v0 | 732-1001 | YAML subset 736-834; documents 837-926; log, handoff and report entries 929-976; `normalize_markdown` 979-989; `text_arg` 992-1001 | header 10 |
| Rows as dicts | 1005-1164 | `member_ref` … `event_dict` | header 4, render 1 (`project_key_of`) |
| Actors and ownership | 1168-1261 | `Actor`, `resolve_actor`, `ACTIVE_CTX` 1204, `require_*`, `claim_of`, `unclaimed_session_project` | header 8, rows 3, hook plumbing 1 (`cli_project_of`), commands 1 (`planning_session`) |
| Domain operations | 1265-1456 | ticket numbers and insert 1269-1293; `check_transition` 1296; `draw_name` 1306; deliverables 1325-1369; `plan_member` 1372-1443; `create_ticket_for_proposal` 1446-1456 | header 17, rows 5, database 3, commands 1 (`table`), time 1 |
| Render | 1460-2351 | Team card cells 1464-1634; **cost** 1636-1867; worked-on sentence 1869-2036; section bodies 2039-2179; whole notes 2182-2231; Projects.md, including its *import* 2234-2305; report and targets 2308-2333; file helpers 2336-2351 | header 17, rows 10, time 5, commands 3 (`table`), markdown 3, hook handlers 2 (`usage_parts`, `BREAKDOWN_TOKENS`), database 1 |
| Import | 2355-2970 | ticket and member note importers 2359-2535; handoffs, team summaries, proposals, `bulk_import` 2538-2716; accepting a hand edit 2719-2970 | header 38, markdown 10, render 10, rows 10, database 7, commands 2, domain 2, time 2 |
| Commands | 2974-4625 | `Result`, `table` 2978-2999; init, migrate, backup 3002-3101; schedule 3106-3268; config sync 3271-3281; settings sync 3285-3463; sql, import, render 3466-3632; report entries 3639-3674; tickets 3677-3793; members 3796-3972; resum 3979-4128; proposals, handoff, report add 4131-4285; events, board, fleet, card, member list 4288-4473; doctor 4476-4625 | database 77, header 54, rows 50, actors 28, time 25, render 19, domain 7, hook handlers 6, projects 6, import 3, hook plumbing 3, markdown 1, home 1 |
| Projects, sessions, ledger commit | 4629-5539 | constants and the /spud skill text 4636-4672; user-scope dirs 4675-4683; `get_project` 4686; session mode and notices 4693-4768; validation 4771-4870; project commands 4873-5004; install and uninstall 5007-5336; claims and session commands 5339-5467; `cmd_ledger_commit` 5470-5539 | header 42, database 38, commands 34, hook plumbing 20, actors 18, time 16, render 6 |
| Hook plumbing and path rule | 5543-6374 | hook constants 5555-5583; 207 lines of shell, git and glob constants 5585-5791, used only by shell analysis; `HookError`, `HookOutput`, `pre_decision` 5794-5808; the spool 5811-5875; globs and case folding 5878-5957; worktrees and checkouts 5960-6068; file identity and path readings 6071-6181; outside roots 6191-6236; git config files 6245-6263; `path_reason`, the state directory, `edit_reason` 6266-6374 | header 4, database 3, rows 2, time 1, domain 1, render 1 |
| Shell analysis | 6377-9492 | `ShellAnalysis` 6380; text preparation 6419-6612; zsh patterns 6617-6901; tokens, redirects, wrappers, prefixes 6904-7059; git line options 7062-7233; git's own commands 7236-7289; a repository's own config 7292-7483; git verbs and targets 7486-7619; spud call parsing and `spud_launcher` 7622-7686; `analyse_command` 7689-7748; qualifiers 7751-7800; `ShellFrame`, `ShellWalk` 7803-8125; cd 8128-8220; glob readings 8223-8382; expansions 8387-8494; read points 8497-8620; `analyse_segment`, `analyse_words` 8623-8827; vouching 8830-8926; spud-call checks 8929-8947; git targets and scopes 8950-9025; `bash_reason` 9028-9196; redirection globs 9199-9492 | hook plumbing 159 (the constants above), database 2 (`git_env`), header 2, rows 2 |
| Hook handlers | 9495-10606 | PreToolUse handlers 9498-9720; binding 9726-9750; stored usage 9763-9801; completion, PostToolUse, SubagentStart 9804-9910; transcript sums 9920-10043; children holds and SubagentStop 10046-10260; SessionStart 10263-10280; UserPromptSubmit 10286-10358; Stop 10367-10559; `HOOK_HANDLERS`, `cmd_hook` 10562-10606 | hook plumbing 28, database 20, projects 18, rows 14, time 12, header 4, commands 3 (`Result`, `board_brief_text`, `member_status_change`), shell analysis 2, domain 1, actors 1 |
| Parser and main | 10610-11065 | help texts 10613-10685; `build_parser` 10688-10969; `normalize_argv` 10972-10993; `HookCall` 10996-11007; `main` 11010-11060; the refusal 11063-11065 | commands 36, projects 12, header 10, hook plumbing 3, database 2, hook handlers 2, home 2, markdown 1, actors 1 |

**The banners are not the seams.** Where a region holds code its neighbours use, the move list puts the code with its users:

- **Render holds cost and two file helpers.** `price_table` (1678) is read by `Ctx` and `doctor`, so cost is `render/prices`, on the hook path; `token_counts` (1556) moves there too, because cost and the Team card both use it. `sha256_bytes` and `write_whole` (2336-2351) go to `core/kernel`.
- **Commands holds what the hooks call.** `Result` and `table` (2978-2999) go to `core/kernel`; `board_brief_text` and `brief_state` (4317-4343), which SessionStart injects, to `projects/sessions`; `member_status_change` (3831) to `state/ops`; `planning_session` (3796) to `state/actors`; `cmd_backup` and `daily_backup` (3040-3101) to `commands/schedule`, beside the LaunchAgent that runs them.
- **Hook handlers holds the transcript sums** (9920-10043) and the stored-usage helpers (9763-9801) that `member resum` and cost read: they are `state/transcripts`.
- **Hook plumbing holds the shell analysis's constants** (5585-5791): they go to the shell modules that use them, and `GIT_REDIRECTS` (5962), which `git_env` reads, to `core/homeconf`.

## 2. Tree, loader, and cycles

### 2.1 The tree

```
bin/
  spud               the launcher: a loader class that applies today's bytecode rule (58 lines; 46 today)
  spud_ledger.py     the entry: docstring, PackageFinder, install_finder, owners and __getattr__, HookCall, main, the refusal (191 lines)
  spudlib/           the program: nine directories, 58 modules, no __init__.py
    core/      kernel · lazy · markdown · homeconf
    state/     schema · ledgerdb · lookup · actors · ops · transcripts · backup
    render/    prices · teamcard · workedon · sectiontext · notefiles
    imports/   noteimport · bulkimport · accept
    commands/  reportentry · admincmds · doctor · schedule · settings_sync · publish · ticketcmds · proposalcmds · membercmds · resumcmd · views
    projects/  sessions · registry · install
    hooks/     hookio · worktrees · pathrule · pretool · recording · subagent_stop · sessionhooks · stophook · dispatch
    shell/     syntax · prepare · zsh · directories · git_verbs · git_programs · git_config · spud_calls · globbing · expansions · walk · analyse · redirect_globs · bash_rule
    cli/       helptexts · cliparser
```

The directories follow Eric's phase order: `core/` is shared types and pure utilities, `state/` state and services, the other seven the sub-components, and the entry the orchestrator. Line counts below are the prototype's files: each module's statements with the comments above them, a one-line docstring, and its import lines. They total 11401 lines in the modules plus 191 in the entry.

| Module | Lines | Names | Imports of the package | Hook path |
| --- | --- | --- | --- | --- |
| `core/kernel` | 147 | 32 | 1 | yes |
| `core/lazy` | 20 | 6 | 0 | yes |
| `core/markdown` | 266 | 18 | 1 |  |
| `core/homeconf` | 166 | 9 | 3 | yes |
| `state/schema` | 268 | 5 | 0 | yes |
| `state/ledgerdb` | 104 | 6 | 4 | yes |
| `state/lookup` | 192 | 13 | 1 | yes |
| `state/actors` | 113 | 10 | 3 | yes |
| `state/ops` | 229 | 15 | 3 | yes |
| `state/transcripts` | 188 | 12 | 0 | yes |
| `state/backup` | 88 | 10 | 1 | yes |
| `render/prices` | 238 | 18 | 2 | yes |
| `render/teamcard` | 174 | 19 | 3 |  |
| `render/workedon` | 174 | 26 | 1 |  |
| `render/sectiontext` | 148 | 11 | 4 |  |
| `render/notefiles` | 157 | 10 | 6 |  |
| `imports/noteimport` | 192 | 10 | 7 |  |
| `imports/bulkimport` | 190 | 6 | 7 |  |
| `imports/accept` | 264 | 12 | 7 |  |
| `commands/reportentry` | 39 | 5 | 3 |  |
| `commands/admincmds` | 116 | 6 | 5 |  |
| `commands/doctor` | 164 | 2 | 11 |  |
| `commands/schedule` | 244 | 15 | 4 |  |
| `commands/settings_sync` | 213 | 15 | 2 |  |
| `commands/publish` | 196 | 3 | 10 |  |
| `commands/ticketcmds` | 135 | 6 | 7 |  |
| `commands/proposalcmds` | 163 | 5 | 7 |  |
| `commands/membercmds` | 152 | 7 | 6 |  |
| `commands/resumcmd` | 162 | 8 | 5 |  |
| `commands/views` | 165 | 9 | 6 |  |
| `projects/sessions` | 277 | 23 | 7 | yes |
| `projects/registry` | 238 | 13 | 8 |  |
| `projects/install` | 348 | 14 | 10 |  |
| `hooks/hookio` | 138 | 25 | 2 | yes |
| `hooks/worktrees` | 255 | 17 | 4 | yes |
| `hooks/pathrule` | 255 | 21 | 4 | yes |
| `shell/syntax` | 192 | 67 | 0 | yes |
| `shell/prepare` | 200 | 5 | 2 | yes |
| `shell/zsh` | 295 | 6 | 1 | yes |
| `shell/directories` | 238 | 12 | 4 | yes |
| `shell/git_verbs` | 215 | 9 | 8 | yes |
| `shell/git_programs` | 236 | 24 | 2 | yes |
| `shell/git_config` | 252 | 20 | 5 | yes |
| `shell/spud_calls` | 195 | 15 | 3 | yes |
| `shell/globbing` | 243 | 20 | 7 | yes |
| `shell/expansions` | 242 | 21 | 6 | yes |
| `shell/walk` | 326 | 2 | 6 | yes |
| `shell/analyse` | 275 | 5 | 11 | yes |
| `shell/redirect_globs` | 279 | 11 | 4 | yes |
| `shell/bash_rule` | 231 | 5 | 10 | yes |
| `hooks/pretool` | 238 | 8 | 9 | yes |
| `hooks/recording` | 147 | 5 | 8 | yes |
| `hooks/subagent_stop` | 226 | 8 | 8 | yes |
| `hooks/sessionhooks` | 106 | 10 | 6 | yes |
| `hooks/stophook` | 209 | 13 | 5 | yes |
| `hooks/dispatch` | 57 | 2 | 7 | yes |
| `cli/helptexts` | 79 | 6 | 0 |  |
| `cli/cliparser` | 342 | 3 | 20 |  |

"Imports of the package" counts the modules a module names in its `from . import …` lines. "Hook path" marks the 35 modules some hook imports; which ones each event imports is in §5.2.

### 2.2 The names

**`bin/spudlib/`.**

- **An identifier,** so the package is importable and each module can say `from ..state import ledgerdb`. An editor opened on `bin/` resolves the imports.
- **Not `bin/spud_ledger/`.** A directory beside `spud_ledger.py` with the same stem: any finder searching `bin/` takes the package before the module, and every conversation about "spud_ledger" means two things.
- **Not `bin/spud_ledger.d/`.** A dot is not an identifier: fine for part files executed by path, not for `import`.
- **Not `bin/spud/`.** `bin/spud` is the launcher's file.
- **Not `bin/ledger/`.** Every brief, deliverable glob, `SPUD_PATHS` and `GENERATED_ROOTS` (5560-5562) mean the rendered `ledger/` by that word.
- **It shadows nothing.** No directory goes on `sys.path`; the finder answers `spudlib` and `spudlib.*` and returns `None` for every other name, so no module of the package can stand in for a standard-library module, and the standard library has no `spudlib`.

**Module names.** Two rules, both checked by the guard test:

- **Each basename is unique across the package,** so `ledgerdb.connect` means one file wherever it is read.
- **No module is named like a local variable anywhere in the program.** A function with a parameter `db` would shadow a module imported as `db` inside that function. The prototype checked 735 local names across every function, lambda and comprehension. That is why the modules are `state/ledgerdb` (not `db`), `state/lookup` (not `rows`), `state/ops` (not `domain`), `state/transcripts` (not `usage`), `render/prices` (not `cost`), `hooks/worktrees` (not `checkouts`), `hooks/stophook` (not `stop`), `shell/globbing` (not `globs`) and `cli/cliparser` (not `parser`). §9 Q3 asks whether Eric wants other spellings; any rename is mechanical and the guard test re-checks it.

### 2.3 The loader and the bytecode cache

**The launcher** keeps its one rule, the home's state directory or no bytecode at all, and states it once as a loader class:

```python
#!/opt/homebrew/bin/python3.14
"""spud: the launcher of the one program that writes the ledger (SPD-007; the launcher since SPD-016).

Run it as  python3.14 -I -S bin/spud [--as ACTOR] [--json] <command> ...

The program is spud_ledger.py beside this file's real path, never found through sys.path or the working directory, and
the package spudlib beside it, which spud_ledger.py imports by the class of the loader that loaded it (SPD-065).
Python compiles a script on every run but caches a module's bytecode, so this launcher loads the program as a module.
Its bytecode goes under <home>/.spud/pycache/, in a tree mirroring each file's own path, so a worktree's program and
the main checkout's never share a file.  <home> is SPUD_HOME, else the checkout this launcher sits in, and nothing is
cached unless <home>/.spud/ already exists: gitignored, and refused to edits and to shell commands like the database
beside it.  Only the program's bytecode goes there; the standard-library modules it imports keep Python's defaults.
"""

import os
import sys
from importlib.machinery import ModuleSpec, SourceFileLoader

NAME = "spud_ledger"


class CachedLoader(SourceFileLoader):
    """A source loader whose bytecode is read and written under `pycache`, or never written when that is None."""

    pycache = None

    def get_code(self, fullname):
        prefix, quiet = sys.pycache_prefix, sys.dont_write_bytecode
        try:
            if self.pycache:
                sys.pycache_prefix = self.pycache
            else:
                sys.dont_write_bytecode = True
            return super().get_code(fullname)  # reads the cached bytecode when it matches the source, else compiles and caches
        finally:
            sys.pycache_prefix, sys.dont_write_bytecode = prefix, quiet


def load():
    here = os.path.dirname(os.path.realpath(__file__))
    path = os.path.join(here, NAME + ".py")
    home = os.path.abspath(os.path.expanduser(os.environ.get("SPUD_HOME") or os.path.dirname(here)))
    state = os.path.join(home, ".spud")
    if os.path.isdir(state):
        CachedLoader.pycache = os.path.join(state, "pycache")
    loader = CachedLoader(NAME, path)
    code = loader.get_code(NAME)
    module = type(sys)(NAME)
    module.__file__ = path
    module.__loader__ = loader
    module.__spec__ = ModuleSpec(NAME, loader, origin=path)
    sys.modules[NAME] = module
    exec(code, module.__dict__)
    return module


if __name__ == "__main__":
    sys.exit(load().main())
```

**The entry** installs one finder when it is loaded, and imports nothing of the package until `main` or an attribute read asks:

```python
PACKAGE = "spudlib"


class PackageFinder:
    """Finds spudlib and its modules in the directory beside this file, and nothing else: never through sys.path or the
    working directory, so no module of the package shadows the standard library or is shadowed by it.  spudlib and its
    groups are directories, packages with no code of their own; a module gets a loader of the class that loaded this
    file: the launcher's, which caches bytecode under the home's state directory, or the suite's plain one, which writes
    none under sys.dont_write_bytecode."""

    def __init__(self, root, loader_class):
        self.root = root
        self.loader_class = loader_class

    def find_spec(self, fullname, path=None, target=None):
        if fullname != PACKAGE and not fullname.startswith(PACKAGE + "."):
            return None
        parts = fullname.split(".")[1:]
        base = os.path.join(self.root, *parts)
        if len(parts) < 2:  # spudlib, spudlib.shell
            spec = ModuleSpec(fullname, None, is_package=True)
            spec.submodule_search_locations = [base]
            return spec
        origin = base + ".py"  # spudlib.shell.walk
        spec = ModuleSpec(fullname, self.loader_class(fullname, origin), origin=origin)
        spec.has_location = True
        return spec


def install_finder():
    """Put the finder for this file's own package first on sys.meta_path, once: a second load of this file (the suite
    loads it many times) keeps the first finder and every module already imported."""
    root = os.path.join(os.path.dirname(__file__), PACKAGE)
    for finder in list(sys.meta_path):
        if type(finder).__name__ == "PackageFinder":
            if finder.root == root:
                return
            sys.meta_path.remove(finder)
            for name in [n for n in sys.modules if n == PACKAGE or n.startswith(PACKAGE + ".")]:
                del sys.modules[name]
    sys.meta_path.insert(0, PackageFinder(root, type(__spec__.loader)))


_OWNERS = {}


def owners():
    """{name: the module of spudlib that defines it}, importing every module once.  Only an in-process reader of
    spud_ledger.<name>, the suite, builds it; a run through the launcher goes through main."""
    if not _OWNERS:
        import importlib
        import types

        root = os.path.join(os.path.dirname(__file__), PACKAGE)
        found = []
        for directory, subdirs, files in os.walk(root):
            subdirs[:] = sorted(d for d in subdirs if d.isidentifier() and not d.startswith("__"))
            for f in sorted(files):
                if f.endswith(".py"):
                    rel = os.path.relpath(os.path.join(directory, f[:-3]), root)
                    found.append(importlib.import_module(PACKAGE + "." + rel.replace(os.sep, ".")))
        imported = {}
        for module in found:
            for key, value in vars(module).items():
                if key.startswith("__") or (isinstance(value, types.ModuleType) and value.__name__.startswith(PACKAGE)):
                    continue
                if isinstance(value, types.ModuleType) or getattr(value, "__module__", module.__name__) != module.__name__:
                    imported.setdefault(key, module)  # a standard-library module or class the module imported
                else:
                    _OWNERS.setdefault(key, module)
        for key, module in imported.items():
            _OWNERS.setdefault(key, module)
    return _OWNERS


def __getattr__(name):
    """spud_ledger.<name>: the name as the module of spudlib that defines it holds it now."""
    owner = None if name.startswith("__") else owners().get(name)
    if owner is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    return getattr(owner, name)


# ... HookCall and main (§4.1) ...


if __name__ == "__main__":
    sys.stderr.write("spud: run the launcher, bin/spud, not %s: the hooks and the allow rules know the launcher\n" % os.path.basename(__file__))
    sys.exit(2)  # kernel.EXIT_USAGE; the refusal imports nothing, so running this file directly writes no bytecode
else:
    install_finder()  # reads no file: from here on spudlib's modules import, as mock.patch("spudlib.<group>.<module>.<name>") needs
```

Reasoned through:

- **Found by path, never by name.** The launcher sets `__file__` to `spud_ledger.py` beside its own real path (launcher line 22-23 today); `tests/helpers.py` loads `REPO/bin/spud_ledger.py` (line 60). The finder's root is `spudlib/` beside that `__file__`, so a worktree's launcher imports the worktree's package and the main checkout's imports its own. Nothing reads `sys.path` or the working directory.
- **Every module's bytecode mirrors its own path.** `SourceFileLoader.get_code` under `sys.pycache_prefix` writes `<prefix>/<source directory without its leading slash>/<stem>.cpython-314.pyc`. The main checkout's `shell/walk.py` caches at `/Users/ericlugo/Personal/Spud/.spud/pycache/Users/ericlugo/Personal/Spud/bin/spudlib/shell/walk.cpython-314.pyc`; a worktree's at `…/.spud/pycache/Users/ericlugo/Personal/Spud/.claude/worktrees/spd-065-split/bin/spudlib/shell/walk.cpython-314.pyc`. They never share a file. Measured in a scratch home: PreToolUse caches the entry and its 32 modules, and the smoke's five CLI calls and one hook left 56 files, all under `.spud/pycache/`.
- **Nothing is written without the state directory.** `CachedLoader.pycache` stays `None` and every `get_code` runs with `sys.dont_write_bytecode` set. `tests/helpers.py` loads the entry with a plain `SourceFileLoader` under `sys.dont_write_bytecode = True` (its line 26), so the finder gives every module that plain loader and the suite writes nothing. Checked after both prototype suite runs: no `.pyc` and no `__pycache__` under `bin/`.
- **Running the entry directly refuses and writes nothing.** Under `__main__` the entry installs no finder and imports no module; the refusal exits 2, spelled `sys.exit(2)` with `kernel.EXIT_USAGE` in its comment, because naming the constant would import `core/kernel`. Checked: exit 2, the same message, no bytecode anywhere.
- **The standard library keeps Python's defaults.** The prefix is set only inside `CachedLoader.get_code` and restored before the module executes, as SPD-016 set it.
- **A directory is a package with no code of its own.** The finder returns a spec with no loader for `spudlib` and each group, so a group costs a module object and no file (the namespace-package path of `importlib`). With an `__init__.py` in each directory and a finder that stats each file, the hook imports took 4.31 ms in-process; without, 4.01 ms (§5.2).
- **The finder is called for every import in the process.** For a name that is not the package's it returns `None` after one string test. The harness's `python -I -S -c pass` row is unchanged, and the in-process figures in §5.2 include it.
- **A second load reuses the first.** `install_finder` keeps an existing finder for the same root, so the suite's 24 `load_spud_module()` calls share one import of the package (§2.5 says what that changes).
- **A missing module fails as `FileNotFoundError`, not `ModuleNotFoundError`,** because the finder does not stat. The guard test's first assertion makes every package import name an existing module, so the difference never reaches a run.
- **`__cached__`** is computed by `importlib` as the `__pycache__` path beside the source, which is never written. Nothing reads it; Phase 2 can set `spec.cached` to the real path if Eric wants the attribute truthful.

### 2.4 Import style and cycles (brief item a)

**The rule.** A module imports modules of the package, never names from them, and only at its top: `from . import walk, zsh` for its own directory, `from ..state import ledgerdb` for another. A reference to another module's name is `module.name`, read when the line runs. Standard-library imports stay as they are, each module restating the ones it uses.

Three things follow from the rule:

- **Python tolerates a cycle at import time.** When `from . import walk` runs while `walk` is itself mid-import, Python hands back the partly initialised module from `sys.modules` (since 3.7). A module that uses a peer's names only inside functions finds them complete when the function runs.
- **A patch on the defining module lands at every call site,** because every call site reads the name from that module when it runs (§4.2).
- **Module-global state keeps one owner** (§2.5).

**What had to change.** Burbank's 57 parts, taken as modules, gave 287 import edges, three cycles and two unsafe reads:

- `render/cost` ⇄ `render/teamcard`: cost calls `token_counts`, the card calls `cost_number`, `money`, `run_cost`.
- `state/backup` ⇄ `state/db`: `cmd_backup` calls `connect`; `apply_migrations` (644) calls `do_backup` (655).
- ten shell parts in one cycle: `analyse`, `bash_rule`, `dirs`, `expansions`, `git_config`, `git_programs`, `git_verbs`, `globs`, `redirect_globs`, `walk`.
- Two reads at import time across a cycle: `analyse_segment`'s default `redirect_cwds=_CURRENT` (8623) reads `walk` while `analyse` is imported, and `GLOB_SAMPLES` (5777) is built from `git_verbs`' flag tables while `globs` is imported. Either could fail, depending on which module a run imports first.
- The hook path's imports pulled in `commands/views` (for `board_brief_text`), `render/teamcard` (through cost) and the backup commands (through `state/db`).

**The code edits, complete.** Every edit but the last only moves a statement to another module.

| # | Edit | Lines on main | Settles |
| --- | --- | --- | --- |
| 1 | `token_counts` to `render/prices` | 1556-1579 | the cost ⇄ card cycle; `render/teamcard` leaves the hook path |
| 2 | `cmd_backup`, `daily_backup` to `commands/schedule` | 3040-3101 | the backup ⇄ database cycle; `state/backup` keeps the copy, listing, check and prune that `apply_migrations` and `doctor` use |
| 3 | `brief_state`, `board_brief_text` to `projects/sessions` | 4317-4343 | `commands/views` leaves the hook path |
| 4 | `_CURRENT` to `shell/syntax` | 7819 | the default argument read across the shell cycle |
| 5 | `GIT_WRITE_VERBS`, `GIT_GLOBAL_VALUE_FLAGS` and the ten `BRANCH_`, `TAG_`, `CONFIG_` flag tables to `shell/syntax` | 5682-5685, 5755-5772 | `GLOB_SAMPLES` read across the shell cycle |
| 6 | `LazyModule` and the five lazy names to `core/lazy` | 34-51 | the rebinding's owner (§2.5); a call reads `lazy.subprocess.run(...)` |
| 7 | `HOOK_HANDLERS` maps an event to `(module, handler)`, and `cmd_hook` imports that module when it calls it | 10562-10570, 10589 | a hook imports its own event's modules (§5) |

Edit 7 is the one change to code rather than to where code lives:

```python
import contextlib
import importlib
import json
import sys

from . import hookio
from ..core import kernel


HOOK_HANDLERS = {  # event -> (its module in spudlib.hooks, its handler): a hook run imports its own handler's modules alone (SPD-065)
    "PreToolUse": ("pretool", "hook_pre_tool_use"),
    "PostToolUse": ("recording", "hook_post_tool_use"),
    "SubagentStart": ("recording", "hook_subagent_start"),
    "SubagentStop": ("subagent_stop", "hook_subagent_stop"),
    "SessionStart": ("sessionhooks", "hook_session_start"),
    "Stop": ("stophook", "hook_stop"),
    "UserPromptSubmit": ("sessionhooks", "hook_user_prompt_submit"),
}


def cmd_hook(ctx, args):
    ...
        module, handler = HOOK_HANDLERS[event]
        out = getattr(importlib.import_module("." + module, __package__), handler)(ctx, payload)   # was: out = HOOK_HANDLERS[event](ctx, payload)
```

It changes no output. The import runs inside `cmd_hook`'s existing `try`, so a handler module that fails to import is spooled and fails closed like any other handler exception (10590-10604); before the split, a broken handler broke the whole file.

**The mechanical rest**, generated and checked by script:

- **1706 references qualified** as `module.name`: commands 451, shell 340, projects 237, hooks 185, imports 153, state 117, render 83, cli 79, core 45. The most in one module: `projects/install` 98, `shell/analyse` 91, `projects/registry` 83.
- **Each module's import lines:** the standard-library imports it uses, then one `from . import …` line for its own directory and one `from ..<group> import …` per other directory it names.
- **The entry's `HookCall` and `main`** qualify their names and import inside the function (§4.1).
- **Three test lines** (§4.2).

**The final graph** has 282 top-level import edges between the 58 modules, and one cycle: the ten shell modules above, which recurse by design (`analyse_words` builds a `ShellWalk`, whose segments call `analyse_segment` and `analyse_words`; `analyse_trap` and the glob readings analyse nested commands). By directory:

| Directory | Imports modules of |
| --- | --- |
| `core` | `render` (`homeconf` → `prices`: `Ctx.pricing`) |
| `state` | `core`, `hooks` (only `actors` → `worktrees`: `cli_project_of`) |
| `render` | `core`, `state` |
| `imports` | `core`, `render`, `state` |
| `commands` | `core`, `hooks`, `imports`, `projects`, `render`, `state` |
| `projects` | `commands` (`install` and `registry` → `reportentry`, `settings_sync`), `core`, `hooks`, `state` |
| `hooks` | `core`, `projects`, `shell`, `state`; `dispatch` imports a handler at run time |
| `shell` | `core`, `hooks`, `state` |
| `cli` | `commands`, `core`, `hooks`, `projects`, `state` |

**Why no import can fail.** A module reads another module's name while it is being imported in exactly seven places: `hooks/hookio` reads `core/kernel` (`EXIT_OK` in `HookOutput`), `hooks/sessionhooks` reads `projects/sessions` (`PROMPT_CLAIM_CAP = SESSION_CONTEXT_CAP`, 10288), `shell/globbing` reads `hooks/hookio` and `shell/syntax` (`GLOB_SAMPLES`), and `shell/analyse`, `shell/expansions` and `shell/redirect_globs` read `shell/syntax` (a default argument and sentinels). For each such read of `b` by `a`, `b` cannot reach `a` through top-level imports. So `b` has finished executing before `a`'s next line runs, whichever module a run imports first. Every other cross-module read is inside a function body and runs after all imports have finished. The guard test asserts this for every read, and it failed as it should when a copy added `_PROBE = walk.ShellWalk` to `shell/analyse` (§6.3).

**Why not break the shell cycle.** It is real recursion, not tangled layering. Breaking it needs either merging the ten modules (about 2,590 lines) or function-local imports at every call that crosses it, which hide those edges from the guard test. Neither buys safety the rule and the test do not already give.

### 2.5 Module-global state (brief item d)

| State | Owner | Written by | Read by | Why every reader sees one value |
| --- | --- | --- | --- | --- |
| `argparse`, `fractions`, `hashlib`, `subprocess`, `tempfile` (34-51) | `core/lazy` | `LazyModule.__getattr__` rebinds `globals()[name]` (43): lazy's own globals | 33 call sites as `lazy.<name>`: argparse 8, fractions 10, hashlib 1, subprocess 12, tempfile 2 | Each reads the attribute when it runs: the LazyModule before first use, the module after. `test_the_hook_path_imports_nothing_it_does_not_use` passes unchanged. |
| `_GIT_OWN_COMMANDS` (7236) | `shell/git_verbs` | `git_own_commands`, with `global` (7261) | only `git_own_commands` | writer and reader are one function |
| `_WORKTREES` (5963) | `hooks/worktrees` | `checkout_worktrees` (6008) | the same function | one module |
| `_CASE_CACHE` (5922) | `hooks/worktrees` | `case_insensitive_fs` (5925) | the same function | one module |
| `ACTIVE_CTX` (1204) | `state/actors` | the entry's `main`: `actors.ACTIVE_CTX[:] = [ctx]` (11025) | `require_spud` (1207) | slice assignment mutates the one list; nothing rebinds the name |
| `glob_sample_matches`' `lru_cache` (8263) | `shell/globbing` | the decorator | the function | the cache belongs to the function object |
| the finder on `sys.meta_path`, `_OWNERS` | the entry | `install_finder`, `owners` | every import of the package; `__getattr__` | one per process; a second load of the entry reuses both |
| `CachedLoader.pycache` | the launcher | `load()`, once, before anything imports | every module's `get_code` | set before the first read |

These are all of them. A script listed every module-level object any function mutates (method calls, subscript and slice assignment) or rebinds (`global`): only the rows above, and no function rebinds a global of another module (the builder refuses one).

**One change in the suite's process.** Today every `load_spud_module()` executes the program afresh, so the 24 loads in eight test files each get their own globals. Now the first load imports the package and the others share its modules. The caches above are keyed by path or by the git binary, the tests use separate scratch homes, and the suite runs in §6.3 show no test depending on a fresh copy. If one ever must, `install_finder`'s other branch is the tool: drop `spudlib.*` from `sys.modules` before loading.

## 3. Move list

One table per module. A row is a run of consecutive top-level statements that move together. "Lines on main" runs from the first statement's first line, decorators included, to the last statement's last line; the comments and blank lines above the first statement move with it.

Checked by script, three ways, independently of the builder:

- **Names:** the 719 names bound at the top of the 58 modules are each bound once; with `HookCall` and `main` in the entry they are exactly the original's 721 non-import names.
- **Lines:** strip each module's docstring and import lines and remove every `module.` qualifier. The non-blank lines of all modules, plus the entry's retained spans (1-17, 10996-11065), plus the 13 standard-library import lines, equal the original's 9904 non-blank lines as multisets. For the final prototype 9 original lines are missing and 10 extra, and they are exactly edit 7's table and call.
- **Statements:** each of the 710 moved statements' syntax trees matches one module statement's, with edit 7's two statements the only pair that differ.

The 13 standard-library import statements (19-31) are restated in each module that uses them and appear in no table.

### `core/kernel.py` (147 lines, 32 names, hook path)

Shared types and constants: exit codes, statuses and state machines, event kinds, note layout, SpudError, the clock, Result and table, two file helpers.

| Lines on main | Names |
| --- | --- |
| 53-143 | `VERSION`, `MARKER`, `EXIT_OK`, `EXIT_ERROR`, `EXIT_USAGE`, `EXIT_OWNERSHIP`, `EXIT_LIMIT`, `EXIT_TRANSITION`, `EXIT_CONFLICT`, `PRIORITIES`, `TICKET_STATUSES`, `MEMBER_STATUSES`, `MODELS`, `PERSONAS`, `ALIVE`, `TICKET_TRANSITIONS`, `MEMBER_TRANSITIONS`, `EVENT_KINDS`, `TICKET_FM_KEYS`, `TICKET_SECTIONS`, `MEMBER_SECTIONS`, `TICKET_COLUMN_SECTIONS`, `MEMBER_COLUMN_SECTIONS`, `SECTION_TABLES`, `SpudError`, `now`, `fm_date`, `fm_minute` |
| 2336-2351 | `sha256_bytes`, `write_whole` |
| 2978-2999 | `Result`, `table` |

### `core/lazy.py` (20 lines, 6 names, hook path)

LazyModule and the five standard-library modules no hook imports.

| Lines on main | Names |
| --- | --- |
| 34-51 | `LazyModule`, `argparse`, `fractions`, `hashlib`, `subprocess`, `tempfile` |

### `core/markdown.py` (266 lines, 18 names)

markdown-v0: the YAML subset, documents, log, handoff and report entries.

| Lines on main | Names |
| --- | --- |
| 736-989 | `yaml_scalar`, `LIST_ITEM`, `parse_frontmatter`, `yaml_quote`, `yaml_list`, `emit_frontmatter`, `strip_blank_edges`, `strip_trailing_blank`, `frontmatter_block`, `restyled_render`, `split_document`, `LOG_ENTRY`, `parse_log_entries`, `HANDOFF_LINE`, `parse_handoff_line`, `REPORT_HEADING`, `parse_report_entries`, `normalize_markdown` |

### `core/homeconf.py` (166 lines, 9 names, hook path)

Where the ledger is: home resolution, Ctx and config, git subprocess helpers, user-scope directories.

| Lines on main | Names |
| --- | --- |
| 155-262 | `resolve_home`, `Ctx`, `config_problems` |
| 697-720 | `git_env`, `run_git`, `git_remote_url` |
| 4675-4683 | `user_claude_dir`, `spud_config_dir` |
| 5962-5962 | `GIT_REDIRECTS` |

### `state/schema.py` (268 lines, 5 names, hook path)

The SQL schema and its migrations, as data.

| Lines on main | Names |
| --- | --- |
| 269-530 | `DDL_0001`, `VIEWS_AND_TRIGGERS`, `DDL_0002`, `MIGRATIONS`, `SCHEMA_VERSION` |

### `state/ledgerdb.py` (104 lines, 6 names, hook path)

Connections, transactions, migrations applied, config rows mirrored, events written.

| Lines on main | Names |
| --- | --- |
| 533-565 | `open_connection`, `connect`, `write_txn` |
| 644-694 | `apply_migrations`, `sync_config_rows` |
| 723-728 | `write_event` |

### `state/lookup.py` (192 lines, 13 names, hook path)

Row lookups and row dicts; member and project handles.

| Lines on main | Names |
| --- | --- |
| 1009-1164 | `member_ref`, `get_ticket`, `get_ticket_by_id`, `get_member_by_id`, `HANDLE_RE`, `get_member`, `ticket_dict`, `member_dict`, `proposal_dict`, `event_dict` |
| 1464-1467 | `persona_label` |
| 2234-2236 | `project_key_of` |
| 4686-4690 | `get_project` |

### `state/actors.py` (113 lines, 10 names, hook path)

Actors, ownership, and the session a command runs in.

| Lines on main | Names |
| --- | --- |
| 1172-1261 | `Actor`, `resolve_actor`, `ACTIVE_CTX`, `require_spud`, `claim_of`, `unclaimed_session_project`, `require_member`, `is_ancestor`, `require_ancestor` |
| 3796-3801 | `planning_session` |

### `state/ops.py` (229 lines, 15 names, hook path)

Domain operations: ticket numbers, transitions, names, deliverables, planning, member status changes.

| Lines on main | Names |
| --- | --- |
| 1269-1456 | `next_ticket_number`, `ticket_key`, `insert_ticket`, `check_transition`, `draw_name`, `QUALIFIED_GLOB`, `glob_scope`, `check_deliverable_projects`, `normalize_deliverable`, `normalize_bare_deliverable`, `normalize_deliverables`, `plan_member`, `create_ticket_for_proposal` |
| 3817-3841 | `status_stamps`, `member_status_change` |

### `state/transcripts.py` (188 lines, 12 names, hook path)

Transcript sums and the stored usage columns.

| Lines on main | Names |
| --- | --- |
| 9749-9750 | `as_int` |
| 9766-9801 | `usage_parts`, `run_totals` |
| 9904-10043 | `parse_timestamp`, `REQUEST_COUNTING`, `TOKEN_KEYS`, `BREAKDOWN_IDENTITY`, `BREAKDOWN_TOKENS`, `usage_count`, `usage_breakdown`, `request_key`, `transcript_usage` |

### `state/backup.py` (88 lines, 10 names, hook path)

The backup copy, its listing, quick check and daily prune.

| Lines on main | Names |
| --- | --- |
| 146-147 | `backup_stamp` |
| 568-641 | `do_backup`, `DAILY_LABEL`, `DAILY_KEEP`, `DAILY_NAME_RE`, `BACKUP_NAME_RE`, `backups_dir`, `backup_listing`, `backup_quick_check`, `prune_daily_backups` |

### `render/prices.py` (238 lines, 18 names, hook path)

List-price cost from spud.config.json pricing, and the token counts it prices.

| Lines on main | Names |
| --- | --- |
| 1556-1579 | `token_counts` |
| 1648-1839 | `PRICE_RATES`, `SPLIT_RATES`, `UNSPLIT_RATE`, `PRICE_MULTIPLIERS`, `PRICE_DATE`, `COST_USD`, `NO_TABLE`, `NO_BREAKDOWN`, `price_number`, `price_rates`, `price_table`, `bucket_cost`, `run_cost`, `cents_of`, `usd_text`, `money`, `cost_number` |

### `render/teamcard.py` (174 lines, 19 names)

The Team table and its cells.

| Lines on main | Names |
| --- | --- |
| 1470-1553 | `team_line`, `prose_for`, `join_prose`, `TEAM_TABLE_HEAD`, `TEAM_TOTAL_LABEL`, `TEAM_VIEW_EMBED`, `NOT_RECORDED`, `WORKED_ON_CAP`, `humanize`, `run_duration`, `known_stamp`, `run_cell` |
| 1582-1634 | `card_cell`, `team_table_row`, `team_order`, `usage_pairs`, `tokens_text` |
| 1842-1867 | `team_totals`, `team_total_row` |

### `render/workedon.py` (174 lines, 26 names)

The worked-on sentence: markdown blocks to one line.

| Lines on main | Names |
| --- | --- |
| 1872-2036 | `MD_COMMENT`, `MD_FENCE`, `MD_HEADING`, `MD_RULE`, `MD_SETEXT`, `MD_ITEM`, `MD_QUOTE`, `MD_TABLE`, `MD_CODE`, `MD_WIKILINK`, `MD_LINK`, `STOP_LABELS`, `BOLD_LABEL`, `PLAIN_LABEL`, `STATUS_START`, `WHERE_START`, `HANDLE_START`, `markdown_blocks`, `paragraph_label`, `is_stop_label`, `is_status_line`, `protected_spans`, `sentence_ends`, `cut_worked_on`, `worked_on_text`, `worked_on` |

### `render/sectiontext.py` (148 lines, 11 names)

Section bodies rendered from rows.

| Lines on main | Names |
| --- | --- |
| 2039-2179 | `render_team_section`, `handoff_party`, `render_handoff_rows`, `decision_trail`, `render_received_rows`, `render_log_rows`, `render_subagent_rows`, `render_proposal_rows`, `ticket_section_text`, `member_section_text`, `body_with_sections` |

### `render/notefiles.py` (157 lines, 10 names)

Whole notes: ticket, member, Projects.md, report; the render targets.

| Lines on main | Names |
| --- | --- |
| 2182-2231 | `render_ticket`, `render_member` |
| 2240-2333 | `PROJECTS_NOTE`, `PROJECTS_COLUMNS`, `table_cell`, `table_cells`, `render_projects`, `import_projects_file`, `render_report`, `render_targets` |

### `imports/noteimport.py` (192 lines, 10 names)

markdown-v0 ticket and member notes into rows.

| Lines on main | Names |
| --- | --- |
| 2359-2535 | `parse_link`, `store_prose_if_needed`, `ticket_layout`, `member_layout`, `require_keys`, `import_ticket_file`, `MEMBER_USAGE_KEYS`, `SQLITE_INTEGER_MAX`, `usage_columns`, `import_member_file` |

### `imports/bulkimport.py` (190 lines, 6 names)

The markdown-v0 tree import.

| Lines on main | Names |
| --- | --- |
| 2538-2716 | `import_handoffs`, `TEAM_LINE`, `import_team_summaries`, `handoff_party_id`, `link_proposal`, `bulk_import` |

### `imports/accept.py` (264 lines, 12 names)

import --file: accepting a hand edit of a rendered file.

| Lines on main | Names |
| --- | --- |
| 2719-2970 | `classify_path`, `heading_tail`, `edited_keys`, `refuse`, `check_section_layout`, `TICKET_SECTION_COMMANDS`, `MEMBER_OWN_SECTIONS`, `MEMBER_SECTION_COMMANDS`, `accept_ticket_edit`, `accept_member_edit`, `accept_report_edit`, `accept_file` |

### `commands/reportentry.py` (39 lines, 5 names)

Report entries and --next, shared by the recording commands.

| Lines on main | Names |
| --- | --- |
| 3642-3674 | `check_next`, `no_entry_for_next`, `write_report_entry`, `report_entry_line`, `with_report_entry` |

### `commands/admincmds.py` (116 lines, 6 names)

init, migrate, config sync, sql, import.

| Lines on main | Names |
| --- | --- |
| 3002-3037 | `cmd_init`, `cmd_migrate` |
| 3271-3281 | `cmd_config_sync` |
| 3466-3520 | `READ_ONLY_FIRST_WORDS`, `cmd_sql`, `cmd_import` |

### `commands/doctor.py` (164 lines, 2 names)

doctor.

| Lines on main | Names |
| --- | --- |
| 4476-4625 | `cmd_doctor`, `doctor_projects` |

### `commands/schedule.py` (244 lines, 15 names)

spud backup, and the daily-backup LaunchAgent that runs it.

| Lines on main | Names |
| --- | --- |
| 3040-3268 | `cmd_backup`, `daily_backup`, `SCHEDULE_LABEL`, `SCHEDULE_AT`, `SCHEDULE_LOG`, `BOOTSTRAP_RETRY_DELAYS`, `at_arg`, `require_spud_flag`, `schedule_plist_path`, `schedule_plist`, `launchctl`, `write_plist`, `cmd_schedule_show`, `cmd_schedule_install`, `cmd_schedule_uninstall` |

### `commands/settings_sync.py` (213 lines, 15 names)

settings sync, and the settings merge project install shares.

| Lines on main | Names |
| --- | --- |
| 3285-3463 | `HOOK_TABLE`, `HOOK_TIMEOUT`, `HOOK_MARK`, `ALLOW_RULE_MARK`, `hook_command`, `cli_allow_rules`, `is_ledger_hook`, `merge_hooks`, `merge_allow_rules`, `AGENT_DENY_RULES`, `merge_deny_rules`, `merge_additional_dirs`, `merge_settings`, `cmd_settings_sync` |
| 4852-4870 | `settings_hold_hooks` |

### `commands/publish.py` (196 lines, 3 names)

render and ledger commit.

| Lines on main | Names |
| --- | --- |
| 3523-3632 | `cmd_render` |
| 4643-4643 | `SUBJECT_TICKET_KEY` |
| 5470-5539 | `cmd_ledger_commit` |

### `commands/ticketcmds.py` (135 lines, 6 names)

ticket new, move, edit, show.

| Lines on main | Names |
| --- | --- |
| 3639-3639 | `TICKET_MOVE_VERBS` |
| 3677-3793 | `cmd_ticket_new`, `cmd_ticket_move`, `cmd_ticket_edit`, `format_ticket`, `cmd_ticket_show` |

### `commands/proposalcmds.py` (163 lines, 5 names)

proposal file, decide, list; handoff add; report add.

| Lines on main | Names |
| --- | --- |
| 4131-4285 | `cmd_proposal_file`, `cmd_proposal_decide`, `cmd_proposal_list`, `cmd_handoff_add`, `cmd_report_add` |

### `commands/membercmds.py` (152 lines, 7 names)

member new, start, finish, edit, log, result, block, show.

| Lines on main | Names |
| --- | --- |
| 3804-3814 | `cmd_member_new` |
| 3844-3972 | `cmd_member_start`, `cmd_member_finish`, `cmd_member_edit`, `cmd_member_own`, `format_member`, `cmd_member_show` |

### `commands/resumcmd.py` (162 lines, 8 names)

member resum.

| Lines on main | Names |
| --- | --- |
| 3979-4128 | `RESUM_FIGURES`, `RESUM_KEPT`, `RESUM_LEFT`, `stored_counting`, `find_transcript`, `resum_row`, `resum_table`, `cmd_member_resum` |

### `commands/views.py` (165 lines, 9 names)

events, board, fleet, card, member list.

| Lines on main | Names |
| --- | --- |
| 4288-4314 | `cmd_events` |
| 4346-4473 | `cmd_board`, `cmd_fleet`, `team_tree`, `flatten_team`, `format_tree`, `card_total_line`, `cmd_card`, `cmd_member_list` |

### `projects/sessions.py` (277 lines, 23 names, hook path)

Session mode and claims, the /spud skill text, the board brief, the session commands.

| Lines on main | Names |
| --- | --- |
| 4317-4343 | `brief_state`, `board_brief_text` |
| 4640-4642 | `CLAIM_CARD_CAP`, `SESSION_CONTEXT_CAP`, `PLAIN_NOTICE_CAP` |
| 4646-4672 | `SKILL_HEAD`, `SKILL_STEPS`, `SKILL_CLAIM`, `HOOK_CLAIM`, `SKILL_TITLE`, `skill_steps`, `skill_markdown` |
| 4693-4768 | `spudagent_shaped`, `pending_spawn`, `session_mode`, `fit_bytes`, `plain_session_notice`, `project_session_context` |
| 5339-5467 | `record_claim`, `claim_card`, `cmd_session_claim`, `cmd_session_release`, `cmd_session_show` |

### `projects/registry.py` (238 lines, 13 names)

project add, list, show, edit, and their validation.

| Lines on main | Names |
| --- | --- |
| 4636-4637 | `PROJECT_KEY_RE`, `PREFIX_RE` |
| 4771-4849 | `identity_chain`, `validate_project_root`, `check_project_key`, `check_prefixes`, `origin_head_branch` |
| 4873-5004 | `project_dict`, `format_project`, `cmd_project_add`, `cmd_project_list`, `cmd_project_show`, `cmd_project_edit` |

### `projects/install.py` (348 lines, 14 names)

project install, uninstall, sync, remove.

| Lines on main | Names |
| --- | --- |
| 4638-4639 | `SETTINGS_LOCAL`, `EXCLUDE_COMMENT` |
| 5007-5336 | `install_files`, `read_json_object`, `git_common_dir`, `ensure_ignored`, `remove_exclude_block`, `install_project`, `strip_ledger_settings`, `uninstall_project`, `cmd_project_install`, `cmd_project_uninstall`, `cmd_project_sync`, `cmd_project_remove` |

### `hooks/hookio.py` (138 lines, 25 names, hook path)

Hook constants, HookError and HookOutput, the spool.

| Lines on main | Names |
| --- | --- |
| 5555-5583 | `HOOK_EVENTS`, `EDIT_TOOLS`, `ENFORCED_TOOLS`, `SPUD_PATHS`, `GENERATED_ROOTS`, `DESCRIPTION`, `AGENT_ID_RE`, `SPUD_COMMANDS`, `SPUD_ONLY_COMMANDS`, `SPUD_ONLY_SUBCOMMANDS`, `READ_ONLY_COMMANDS`, `READ_ONLY_SUBCOMMANDS`, `MEMBER_OWN_COMMANDS`, `DB_PATH_RE`, `STATE_DIR`, `DB_REASON`, `SUBST` |
| 5794-5875 | `HookError`, `HookOutput`, `SILENT`, `pre_decision`, `spool_path`, `spool_lock`, `spool_write`, `spool_drain` |

### `hooks/worktrees.py` (255 lines, 17 names, hook path)

Case folding, worktrees and project checkouts, file identity, path readings.

| Lines on main | Names |
| --- | --- |
| 5922-5947 | `_CASE_CACHE`, `case_insensitive_fs`, `folds_case` |
| 5963-6181 | `_WORKTREES`, `worktrees_fingerprint`, `git_worktree_list`, `project_root`, `checkout_worktrees`, `project_checkouts`, `project_of_path`, `cli_project_of`, `file_identity`, `same_entry`, `map_into_checkouts`, `path_readings`, `path_placements`, `project_paths` |

### `hooks/pathrule.py` (255 lines, 21 names, hook path)

Laws 1 and 5 over a path: globs, outside roots, git config files, the state directory, edit_reason.

| Lines on main | Names |
| --- | --- |
| 5881-5919 | `glob_to_regex`, `path_matches_glob` |
| 5950-5957 | `HARNESS_FILES_RE`, `harness_file` |
| 6191-6374 | `SCRATCHPAD_ROOT`, `FIXED_TEMP_ROOTS`, `TEMP_ROOT_VARS`, `DEV_WRITE_ROOTS`, `OUTSIDE_PROJECT_REASON`, `outside_roots`, `under_outside_root`, `outside_project_reason`, `GIT_CONFIG_FILE_NAMES`, `GIT_CONFIG_FILE_TAILS`, `GIT_CONFIG_FILE_REASON`, `git_config_file`, `NOT_SPUD_HOME`, `path_reason`, `in_state_dir`, `state_dir_reason`, `edit_reason` |

### `shell/syntax.py` (192 lines, 67 names, hook path)

Shell and git word tables, sentinels, ShellAnalysis, tokens.

| Lines on main | Names |
| --- | --- |
| 5588-5685 | `OUT_REDIRECTS`, `IN_REDIRECTS`, `RESERVED_WORDS`, `WRAPPERS`, `WRAPPER_VALUE_OPTIONS`, `DURATION_RE`, `SHELL_OPERATORS`, `SHELL_PUNCTUATION`, `LIST_TERMINATORS`, `LOOP_PREFIX_WORDS`, `DIRECTORY_COMMANDS`, `SHELL_DECLARATIONS`, `_GLOB_META`, `_GLOB_SENTINELS`, `_GLOB_UNSENTINEL`, `_ZSH_PATTERN_CHARS`, `_ZSH_SENTINELS`, `ZSH_BAR`, `ZSH_CLOSE`, `ZSH_OPEN`, `ZSH_RANGE_CLOSE`, `ZSH_RANGE_OPEN`, `_ZSH_UNSENTINEL`, `_LITERAL_EQUALS`, `_ARRAY_VALUE`, `_LITERAL_DOLLAR`, `_NAME_END`, `_QUOTED_DOLLAR`, `_SENTINEL_TEXT`, `_LITERALIZE`, `_GLOB_SENTINEL_RE`, `GLOB_RE`, `ZSH_RANGE_RE`, `GLOB_MATCH_CAP`, `GLOB_SCAN_CAP`, `ARRAY_ASSIGNMENT_RE`, `ASSIGNMENT_WORD_RE`, `SHELLS`, `PYTHON_RE`, `JS_RUNTIMES`, `ASSIGNMENT_RE`, `VARREF_RE`, `_EXPANDING_DOLLAR_RE`, `_ASSIGNING_EXPANSION_RE`, `_NAME_RE`, `_NAME_CHAR_RE`, `_BARE_NAME_TAIL_RE`, `_IFS_BLANKS_RE`, `ASSIGNING_COMMANDS`, `DYNAMIC_VARIABLES`, `HEREDOC_RE`, `GIT_WRITE_VERBS`, `GIT_GLOBAL_VALUE_FLAGS` |
| 5755-5772 | `BRANCH_READ_FLAGS`, `BRANCH_READ_VALUE_FLAGS`, `TAG_READ_FLAGS`, `TAG_READ_VALUE_FLAGS`, `CONFIG_READ_FLAGS`, `CONFIG_VALUE_FLAGS`, `CONFIG_WRITE_FLAGS`, `CONFIG_READ_SELECTORS`, `CONFIG_WRITE_SUBCOMMANDS`, `CONFIG_READ_SUBCOMMANDS` |
| 6380-6416 | `ShellAnalysis` |
| 6904-6923 | `shell_tokens`, `operator_parts` |
| 7819-7819 | `_CURRENT` |

### `shell/prepare.py` (200 lines, 5 names, hook path)

Heredocs, substitutions, newlines and quoted globs before tokenizing.

| Lines on main | Names |
| --- | --- |
| 6419-6612 | `strip_heredocs`, `split_substitutions`, `newlines_as_separators`, `neutralize_quoted_globs`, `deglob` |

### `shell/zsh.py` (295 lines, 6 names, hook path)

zsh's own glob operators.

| Lines on main | Names |
| --- | --- |
| 6617-6901 | `ZSH_COMMAND_POSITION_WORDS`, `_PLAIN_RUN_RE`, `_ARRAY_NAME_RE`, `_scan_pairs`, `_zsh_group`, `mark_zsh_patterns` |

### `shell/directories.py` (238 lines, 12 names, hook path)

Redirects, wrappers, prefixes, and the directories the shell may be in.

| Lines on main | Names |
| --- | --- |
| 6926-7059 | `union_dirs`, `separate_redirects`, `strip_wrapper`, `prefix_effect`, `EFFECT_ORDER`, `builtin_runs`, `builtin_runs_here`, `settle` |
| 8128-8220 | `cdpath_entries`, `cd_target`, `cd_destinations`, `directory_change` |

### `shell/git_verbs.py` (215 lines, 9 names, hook path)

Law 7's verbs: git's own commands, unknown verbs, repository targets.

| Lines on main | Names |
| --- | --- |
| 7062-7073 | `git_verb` |
| 7236-7289 | `_GIT_OWN_COMMANDS`, `GIT_COMMANDS_CACHE`, `git_binary_fingerprint`, `git_own_commands` |
| 7486-7619 | `git_unknown_verb`, `git_repo_targets`, `flag_list_refused`, `git_refused` |

### `shell/git_programs.py` (236 lines, 24 names, hook path)

git config, environment and options that name a program.

| Lines on main | Names |
| --- | --- |
| 5689-5754 | `GIT_ALIAS_SECTIONS`, `GIT_CONFIG_FILE_VARS`, `GIT_CONFIG_INLINE_VARS`, `GIT_CONFIG_INDEXED_RE`, `GIT_CONFIG_HOME_VARS`, `GIT_REPO_ENV_VARS`, `GIT_REPO_OPTIONS`, `GIT_INERT_CONFIG_SECTIONS`, `GIT_INERT_CONFIG_KEYS`, `GIT_PAGER_ENV_VARS`, `GIT_PROGRAM_ENV_VARS`, `GIT_EXEC_PATH_OPTION`, `GIT_VERB_PROGRAM_OPTIONS` |
| 7076-7233 | `git_config_section`, `git_line_defines_alias`, `is_git_config_var`, `is_git_repo_var`, `git_env_defines_alias`, `git_inert_pager_value`, `git_config_key_allowed`, `git_line_names_program`, `is_git_program_var`, `git_env_names_program`, `git_verb_names_program` |

### `shell/git_config.py` (252 lines, 20 names, hook path)

The config a repository sets for itself (SPD-063).

| Lines on main | Names |
| --- | --- |
| 7306-7483 | `GIT_CONFIG_SCOPES_CACHE`, `GIT_OWN_SCOPES`, `GIT_CONFIG_SCOPES_KEPT`, `GIT_CONFIG_INCLUDE_FILES`, `GIT_CONFIG_READ_LIMIT`, `GIT_WALK_LIMIT`, `_GIT_INCLUDE_PATH_RE`, `GIT_PROGRAM_KEY_SECTIONS`, `GIT_PROGRAM_KEY_WORDS`, `git_config_key_names_program`, `git_repo_common_dir`, `git_repository_dirs`, `git_scope_config_files`, `git_config_fingerprint`, `git_run_config_scopes`, `git_own_config_keys` |
| 8980-9025 | `GIT_SCOPE_REASON`, `GIT_SCOPE_UNRESOLVED_REASON`, `GIT_SCOPE_UNREADABLE_REASON`, `git_local_config_reason` |

### `shell/spud_calls.py` (195 lines, 15 names, hook path)

Recognizing and vouching for a spud call.

| Lines on main | Names |
| --- | --- |
| 7622-7686 | `parse_spud_call`, `python_interpreter_args`, `spud_launcher` |
| 8830-8947 | `any_spud_launcher`, `PYTHON_FLAGS_RE`, `python_options_vouched`, `unresolvable_word`, `interpreter_vouched`, `launcher_vouched`, `spud_home_vouched`, `vouched_spud_call`, `QUIET_TARGETS`, `FINDING_LAST`, `actor_is_self`, `spud_call_writes` |

### `shell/globbing.py` (243 lines, 20 names, hook path)

Glob words, qualifiers and their readings.

| Lines on main | Names |
| --- | --- |
| 5775-5791 | `GLOB_COMMAND_SAMPLES`, `GLOB_SAMPLES`, `GLOB_OPTION`, `GLOB_WORD_LIMIT`, `GLOB_READING_BUDGET`, `_EQUALS_RE`, `_STAR_RUN_RE` |
| 7751-7800 | `_QUALIFIER_CLOSERS`, `trailing_group`, `qualifier_code`, `_QUALIFIER_NAME_RE` |
| 8223-8382 | `active_glob_word`, `literalize`, `may_start_with_dash`, `glob_too_complex`, `glob_sample_matches`, `command_path`, `glob_readings`, `resolve_glob`, `analyse_readings` |

### `shell/expansions.py` (242 lines, 21 names, hook path)

Parameter expansions and a command's read points.

| Lines on main | Names |
| --- | --- |
| 8387-8620 | `_AGAIN`, `_FLAGGED`, `_STOP`, `_ACTIVATE_GLOBS`, `_ZSH_PLAIN`, `_BRACES_PLAIN`, `expansion_word`, `variable_reference`, `active_read_word`, `assign_variable`, `variable_readings`, `resolve_expansion`, `first_read_index`, `git_read_index`, `shell_read_index`, `trap_action_indices`, `trap_read_index`, `analyse_trap`, `python_read_index`, `option_point`, `script_point` |

### `shell/walk.py` (326 lines, 2 names, hook path)

ShellFrame and ShellWalk: one pass over a line's tokens.

| Lines on main | Names |
| --- | --- |
| 7803-7816 | `ShellFrame` |
| 7822-8125 | `ShellWalk` |

### `shell/analyse.py` (275 lines, 5 names, hook path)

analyse_command and analyse_words.

| Lines on main | Names |
| --- | --- |
| 7689-7748 | `analyse_command`, `isolated`, `analyse_isolated` |
| 8623-8827 | `analyse_segment`, `analyse_words` |

### `shell/redirect_globs.py` (279 lines, 11 names, hook path)

The files a redirection glob opens.

| Lines on main | Names |
| --- | --- |
| 9222-9492 | `_split_brace`, `_expand_one_brace`, `brace_expand`, `_has_bare_glob`, `_digit_span`, `numeric_range_regex`, `_GROUP_REGEX`, `_segment_regex`, `bounded_glob`, `expand_redirect_target`, `qualifier_readings` |

### `shell/bash_rule.py` (231 lines, 5 names, hook path)

bash_reason: the Bash hook's rule over a line.

| Lines on main | Names |
| --- | --- |
| 8950-8977 | `git_target_dirs`, `git_repo_outside` |
| 9028-9219 | `bash_reason`, `redirection_paths`, `target_has_active_glob` |

### `hooks/pretool.py` (238 lines, 8 names, hook path)

The PreToolUse handlers.

| Lines on main | Names |
| --- | --- |
| 9498-9720 | `deny_and_record`, `hook_agent_spawn`, `hook_bash`, `hook_edit`, `request_from_meta`, `bind_request`, `late_bind`, `hook_pre_tool_use` |

### `hooks/recording.py` (147 lines, 5 names, hook path)

Binding, and the recording handlers PostToolUse and SubagentStart.

| Lines on main | Names |
| --- | --- |
| 9726-9746 | `bind_member` |
| 9763-9763 | `COMPLETION_KEYS` |
| 9804-9901 | `record_completion`, `hook_post_tool_use`, `hook_subagent_start` |

### `hooks/subagent_stop.py` (226 lines, 8 names, hook path)

A parent's finishing rule, and SubagentStop.

| Lines on main | Names |
| --- | --- |
| 10046-10260 | `RESULT_HOLD`, `unrecorded_children`, `alive_children`, `finish_commands`, `children_hold_reason`, `alive_children_reason`, `stop_hold_reason`, `hook_subagent_stop` |

### `hooks/sessionhooks.py` (106 lines, 10 names, hook path)

SessionStart and UserPromptSubmit.

| Lines on main | Names |
| --- | --- |
| 10263-10358 | `hook_session_start`, `PROMPT_KEY_SHAPE`, `SPUD_COMMAND`, `PROMPT_CLAIM_CAP`, `prompt_is_spud_command`, `prompt_ticket_keys`, `prompted_ticket`, `released_by_command`, `prompt_claim_context`, `hook_user_prompt_submit` |

### `hooks/stophook.py` (209 lines, 13 names, hook path)

What a stopping session owes, and Stop.

| Lines on main | Names |
| --- | --- |
| 10367-10559 | `PLANNED_GRACE_SECONDS`, `member_session`, `planned_long_ago`, `told_running`, `reservations`, `stop_owed`, `stop_item`, `returned_clause`, `planned_clause`, `unbound_clause`, `running_clause`, `stop_reason`, `hook_stop` |

### `hooks/dispatch.py` (57 lines, 2 names, hook path)

HOOK_HANDLERS and cmd_hook.

| Lines on main | Names |
| --- | --- |
| 10562-10606 | `HOOK_HANDLERS`, `cmd_hook` |

### `cli/helptexts.py` (79 lines, 6 names)

The long help texts the parser shows.

| Lines on main | Names |
| --- | --- |
| 10613-10685 | `EPILOG`, `RESUM_DESCRIPTION`, `RESUM_EPILOG`, `BACKUP_DESCRIPTION`, `BACKUP_EPILOG`, `SCHEDULE_DESCRIPTION` |

### `cli/cliparser.py` (342 lines, 3 names)

build_parser, normalize_argv, text_arg.

| Lines on main | Names |
| --- | --- |
| 992-1001 | `text_arg` |
| 10688-10993 | `build_parser`, `normalize_argv` |

### `bin/spud_ledger.py` (the entry, 191 lines)

| Lines on main | What |
| --- | --- |
| 1-17 | the module docstring, amended to name `bin/spudlib/` |
| new | `PACKAGE`, `PackageFinder`, `install_finder`, `_OWNERS`, `owners`, `__getattr__` (§2.3, §4.1); `from importlib.machinery import ModuleSpec` |
| 19-31, in part | `contextlib`, `json`, `os`, `sqlite3`, `sys`, `Path`, which `main` uses |
| 10996-11007 | `HookCall`: `self.func = dispatch.cmd_hook`, with `from spudlib.hooks import dispatch` inside `__init__` |
| 11010-11060 | `main`: its names qualified; the hook modules imported at its top, the parser in its `else` branch |
| 11063-11065 | the refusal, `sys.exit(2)` (§2.3), then `install_finder()` for any other load |

## 4. Compatibility

### 4.1 The entry point and the public surface (brief item c)

**What the launcher gets back** is unchanged: `load()` returns module `spud_ledger`, with `__file__` beside the launcher's real path, and `sys.exit(load().main())`.

**`main` as orchestrator** imports what the run needs and nothing else:

```python
def main(argv=None):
    from spudlib.core import homeconf, kernel
    from spudlib.hooks import hookio
    from spudlib.state import actors

    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) == 2 and argv[0] == "hook" and argv[1] in hookio.HOOK_EVENTS:
        args = HookCall(argv[1])
    elif len(argv) == 4 and argv[0] == "hook" and argv[1] in hookio.HOOK_EVENTS and argv[2] == "--project":  # a project's hook line (SPD-014)
        args = HookCall(argv[1], argv[3])
    else:
        from spudlib.cli import cliparser

        args = cliparser.build_parser().parse_args(cliparser.normalize_argv(argv))
    ...  # 11020-11060 as today, with kernel., homeconf., actors. and hookio. before the names they read
```

**Every attribute read on `spud_ledger` still resolves.** The entry defines `main`, `HookCall` and `__file__` itself; any other name reaches the module `__getattr__` (PEP 562). On first use, `owners()` imports every module of the package and maps each name to the module that defines it: the module whose own function, class or constant it is. A standard-library module or class a module imports (`os`, `Path`) is mapped only if no module defines the name. Each read then returns `getattr(owner, name)` at that moment, so a value the program rebinds (a lazy module, a patch) reads as the defining module holds it.

**A hook never builds the index.** `__getattr__` runs only when something reads a name the entry does not have. The launcher calls `main`, and `main` imports modules directly. The guard test reads `sys.modules` as each hook exits (§6.2).

The suite reads 43 attributes. A script walked every name bound to `load_spud_module()` in `tests/` and `tests/probes/`, and collected attribute reads, writes, `mock.patch.object`, `setattr`, `delattr` and string `mock.patch` targets. It found 43 attributes read, 2 names patched at 3 sites, no writes and no string targets. Where each read resolves:

| Module | Attributes the suite reads |
| --- | --- |
| `core/homeconf` | `config_problems`, `resolve_home` |
| `core/kernel` | `EVENT_KINDS`, `MARKER`, `SpudError`, `fm_date`, `fm_minute`, `now` |
| `core/markdown` | `emit_frontmatter`, `parse_frontmatter`, `parse_handoff_line`, `parse_log_entries`, `parse_report_entries`, `split_document` |
| `hooks/pathrule` | `path_matches_glob` |
| `hooks/sessionhooks` | `prompt_is_spud_command`, `prompt_ticket_keys` |
| `projects/sessions` | `HOOK_CLAIM`, `skill_markdown`, `skill_steps` |
| `render/prices` | `cost_number`, `money`, `price_table`, `run_cost`, `token_counts` |
| `render/teamcard` | `humanize`, `run_cell`, `run_duration` |
| `render/workedon` | `worked_on` |
| `shell/analyse` | `analyse_command` |
| `shell/bash_rule` | `target_has_active_glob` |
| `shell/git_verbs` | `git_own_commands` |
| `shell/redirect_globs` | `expand_redirect_target`, `numeric_range_regex` |
| `shell/syntax` | `GLOB_MATCH_CAP`, `ShellAnalysis` |
| `shell/walk` | `ShellWalk` |
| `state/backup` | `backup_quick_check`, `backup_stamp` (patched), `do_backup` (read and patched) |
| `state/schema` | `DDL_0001` |
| `state/transcripts` | `transcript_usage` |

`main` and `__file__` (test_backup 231; test_markdown 220, 228) are the entry's own. `resolve_home`'s git fallback still receives `Path(__file__)` of `bin/spud_ledger.py` from `main`, unchanged.

**What does change, all of it additive or invisible in output:**

1. **Six names join the entry:** `PACKAGE`, `PackageFinder`, `install_finder`, `_OWNERS`, `owners`, `__getattr__`. None collides with a program name.
2. **`dir(spud_ledger)` lists the entry's names,** not the program's. Nothing in the suite or the program calls `dir` on it.
3. **`__module__` and `__qualname__`** of the program's classes and functions name `spudlib.<group>.<module>`. Neither the program nor the suite reads either: no occurrence in `bin/spud_ledger.py` or `tests/`.
4. **Tracebacks name the module file and line.** A hook failure is spooled as `type(e).__name__: e` (10593), so no ledger output carries a path.
5. **The state directory holds up to 60 `.pyc` files instead of one.**
6. **A syntax error in a module no hook imports** surfaces when a command first imports it. The suite imports every module, so one cannot land.
7. **A merge is not atomic across 60 files.** A hook process that starts while `git merge` rewrites `bin/spudlib/` at the ledger root could, for that one run, import old and new modules together. An enforcing hook fails closed on any exception (10597-10604), so the realistic worst case is one refused tool call during a merge Spud runs himself (§9 Q7).

### 4.2 The suite, and where a name is patched (brief item b)

`tests/helpers.py` still loads `bin/spud_ledger.py` as module `spud_ledger` (58-63), and the entry still installs its finder as that load finishes. What breaks without an edit is the three patches in `tests/test_backup.py`:

```python
stack.enter_context(mock.patch.object(self.spud, "backup_stamp", lambda: stamp))                    # 228
with self.subTest(fault=fault.__name__), mock.patch.object(self.spud, "do_backup", fault):           # 264
with self.subTest(damage=damage.__name__), mock.patch.object(self.spud, "do_backup", damaging):     # 303
```

`mock.patch.object` sets the attribute on the entry, and `daily_backup` (in `commands/schedule`) reads `backup.backup_stamp` and `backup.do_backup` from `state/backup`, so the fault is never injected. Two answers, each run against `tests/test_backup.py` (30 tests) on the prototype:

| | (i) The tests patch the defining module | (ii) The entry forwards writes to the defining module |
| --- | --- | --- |
| Test edits | 3 lines: `mock.patch("spudlib.state.backup.backup_stamp", …)` and `mock.patch("spudlib.state.backup.do_backup", …)` | none |
| Program code | none | about 25 lines: a `ModuleType` subclass whose `__setattr__` and `__delattr__` send a program name to `owners()[name]`, swapped in with `module.__class__ = …`. The entry must find its own module object: `sys.modules` under the launcher, but `tests/helpers.py` loads the entry without registering it, so the prototype found it with `gc.get_referrers(globals())`. |
| `test_backup`, 30 tests | OK | OK (`mock.patch.object`'s exit calls `delattr`, then `setattr` of the original; both forward) |
| Neither | 5 tests fail (7 subtests): the fault never happens | |
| A future test that patches through the entry | patches nothing, and fails its own assertion, as the five did | lands |

**Recommendation: (i).** A patch that names the module whose globals the call site reads is what real modules mean, and it needs no mechanism. (ii) keeps a second path for writes, and a garbage-collector walk, to save three lines. §9 Q5 proposes one more guard assertion so a future patch through the entry fails in the suite rather than silently.

**The test edits, complete.** None changes what a test asserts about the program's behaviour.

| File | Lines | Edit |
| --- | --- | --- |
| `tests/test_backup.py` | 228, 264, 303 | patch `spudlib.state.backup.backup_stamp` and `…do_backup` by string target, as above |
| `tests/test_launcher.py` | 46-56 | `test_the_programs_bytecode_is_cached_under_the_homes_spud_directory`: the cache holds the entry's `.pyc` and one per imported module, each under the mirror of its own source directory; `board` caches `spudlib/core/kernel`, which every run imports (the prototype asserted `spudlib/cli/cliparser`, true only once the parser has moved); every cached file maps to an existing source; the "read, not rewritten" stamp check covers every file; no `.pyc` under `bin/` |
| `tests/test_launcher.py` | 98-99 | `WithoutSpudHomeTest.setUp` copies `bin/spudlib` with the two files |
| `tests/test_launcher.py` | 112-116 | `test_the_checkouts_spud_directory_holds_the_cache`: the entry's `.pyc` is among the cached files, and all are under `.spud/` |
| `tests/test_init.py` | 175-183 | recommended: `test_no_bytecode_is_written_under_bin_or_tests` imports every module in-process (`load_spud_module().owners()`) and looks for any new `.pyc` under `bin/`, not only `bin/__pycache__` |
| `tests/helpers.py` | 31-33 | recommended: `_remove_bytecode` also removes `bin/spudlib/**/__pycache__` |
| `tests/probes/headless_projects.py` | 78-79 | copy `bin/spudlib` into the scratch home |
| `tests/probes/headless.py` | 233 | copy `bin/spud_ledger.py` and `bin/spudlib` beside `bin/spud`; today it copies only the launcher, so the probe cannot run (SPD-052) |
| `tests/test_package.py` | new | the guard test (§6.2) |
| `tests/probes/hook_timing.py` | new | the timing harness (§5.3) |

`tests/test_hooks.py` copies the launcher and the program at 1548 and 1577 only to name them on command lines the Bash hook analyses; nothing runs the copies, so they need no edit. Its state-directory test at 4314 takes the first cached `.pyc` it finds, whichever module that is.

**Evidence that the list is complete.** The suite on prototype A with no test edits failed exactly 12 tests: the 2 control failures, the 5 `test_backup` tests (7 subtests) and the 3 `test_launcher` tests above. On the final prototype with no test edits: the same 12, of 732. With every edit above and the guard test: 738 run, and only the control's 2 failures (§6.3).

### 4.3 Hook self-recognition

| Mechanism | Lines | Keys on | With the package |
| --- | --- | --- | --- |
| `spud_launcher`, `any_spud_launcher` | 7672-7686, 8830-8832 | the script word's name, or its real path's name, case-folded, is `spud` | Unchanged: the launcher's name and place do not change, and no command line names a module. |
| `launcher_vouched` | 8893-8908 | the identity of `realpath(<home>/bin/spud)` and of its directory | Unchanged. Its premise (comment 8837-8840) is that the launcher loads the program beside its own real path. The entry's finder roots `spudlib/` beside that same path (§2.3), so the vouched root launcher runs the root's package, and a worktree's launcher, never vouched, runs the worktree's. |
| `interpreter_vouched` | 8867-8890 | `sys.executable` by identity, in the same directory | Unchanged. |
| `spud_home_vouched`, `vouched_spud_call` | 8911-8926 | a `SPUD_HOME` assignment names the root by identity | Unchanged and still needed: the launcher loads unchecked-hash bytecode from `<SPUD_HOME>/.spud/pycache/`, now for up to 60 files. |
| `DB_PATH_RE`, `DB_REASON` (Bash hook) | 5576-5582 | a command line naming `.spud/` or `ledger.db` | Unchanged: every module's bytecode is under `.spud/`. |
| `in_state_dir`, `state_dir_reason`, `edit_reason` (edit hook) | 6301-6374 | the first component of the repository path, case-folded, is `.spud` | Unchanged: `.spud/pycache/…/bin/spudlib/shell/walk.cpython-314.pyc` starts with `.spud`. A worktree's modules cache under the home's state directory too, since the hooks run with `SPUD_HOME=<home>`. |
| `git_binary_fingerprint`, `git_own_commands` | 7240-7289 | the path and stat of the git binary; `_GIT_OWN_COMMANDS` once per process | Unchanged: nothing about the program's files; the `global` at 7261 rebinds `shell/git_verbs`, where its only reader is. |
| `worktrees_fingerprint`, `checkout_worktrees` | 5966-6042 | stats of `.git/worktrees` and each `gitdir`; `_WORKTREES` once per process | Unchanged. |
| `file_identity`, `same_entry` | 6071-6091 | `(st_dev, st_ino)` | Unchanged. |
| `resolve_home` from `main` | 155-177, 11023 | the git common directory of `Path(__file__)` | Unchanged: `main` stays in the entry, and `__file__` is `bin/spud_ledger.py`. |
| `settings sync`, `project install`, `project sync`, `schedule install` | 3303-3322, 5074-5125, 3136-3152 | `<home>/bin/spud` | Unchanged: each names only the launcher. |

In the main checkout and in every worktree the same holds. A checkout's `bin/spud` loads the entry and the package beside its own real path. With `SPUD_HOME` set, as on every hook line, it caches under the home's state directory in the mirror of that checkout's path; without it, in a worktree with no `.spud/`, it writes nothing, as today. During Phase 2 the engineer's deliverable globs must name `bin/spudlib/**` beside `bin/spud` and `bin/spud_ledger.py`.

## 5. Hook latency (brief item f)

### 5.1 Today

SPD-016 took a hook run from 65 to 22 ms by caching the bytecode (25 ms of compile) and making five imports lazy (11 ms). Main at `9e1deda`, in the runs below: PreToolUse(Bash) 23.15 ms, PreToolUse(Write) 23.94, SessionStart 23.34, Stop 23.30, `board` 34.71, and the bare `python3.14 -I -S -c pass` 11.26.

### 5.2 After the split

**Four prototypes, measured against main with the harness of §5.3,** in two runs of 30 interleaved rounds each. The figures are the difference of each median from main's in the same run.

| Prototype | PreToolUse(Bash) | PreToolUse(Write) | SessionStart | Stop | `board` |
| --- | --- | --- | --- | --- | --- |
| A: one `from . import` line per module, `__init__.py` in each directory, every handler imported | +1.12 | +1.21 | +0.95 | +1.27 | +4.02 |
| B: A with one `from . import a, b` line per directory | +1.13 | +1.18 | +1.18 | +1.21 | +3.80 |
| F: B with directories that have no `__init__.py`, and a finder that makes no stat | +0.83 | +0.95 | +0.97 | +1.13 | +3.39 |
| **G: F with edit 7, the handler table (recommended)** | **+0.70** | **+0.34** | **−1.94** | **−1.99** | **+0.40** |

A, B and F ran together. G ran with F a second time, where F measured +0.82, +0.75, +0.62, +0.77 and `board` +3.26, so F sits right at the pass and missed it on Stop in the first run. G passes on every hook with 0.3 ms or more to spare.

**Where the time goes.** In-process, over 20 cold processes, from the launcher's code to a hook ready to run: main 8.66 ms, which is the program's `get_code` and body, standard-library imports included; F 9.44 ms, which is 5.50 ms for the entry with the standard-library imports it makes and 3.94 ms for the package's modules. Each module costs importlib's own work (lock, spec, module object, `get_code`) on top of its body: about 18 µs a module in a synthetic benchmark of 40 modules, imported against executed into one namespace. The monolith pays one of those and executes all 11065 lines; the package pays one per module and executes only what it imports. So the modules a hook does not import pay for the ones it does:

| Hook | Modules it imports | Of them, shell analysis |
| --- | --- | --- |
| PreToolUse | 32 | 14 |
| PostToolUse, SubagentStart | 16 | 0 |
| SubagentStop | 34 (it reuses PreToolUse's and Stop's rules) | 14 |
| SessionStart, UserPromptSubmit | 16 | 0 |
| Stop | 15 | 0 |
| `board`, `--version` (any command) | 38, eleven of them commands | 0 |

Counted from `sys.modules` as each run exited, in a scratch home.

A command imports the parser, which names every command module, but under G no handler module, so `board` no longer loads the 14 shell modules: +0.40 ms.

**What would buy more,** if a later hook change needs it: fewer, larger modules on PreToolUse's path, or one bytecode file for all of them. At +0.70 ms neither is worth its cost now.

**One measuring trap.** `-X importtime` does not log a module imported with `importlib.import_module`, which is how edit 7 imports a handler, so an import list read from it misses the handler. The guard test reads `sys.modules` as the hook exits instead.

### 5.3 The harness: `tests/probes/hook_timing.py`

Committed in Phase 2 as a probe; unittest does not collect it. Run it with main's launcher first and the branch's second:

```bash
python3.14 -I -S tests/probes/hook_timing.py 30 /Users/ericlugo/Personal/Spud/bin/spud /Users/ericlugo/Personal/Spud/.claude/worktrees/spd-065-split/bin/spud
```

It gives each launcher its own scratch home, so nothing is written into any ledger. **Pass:** each hook's median on the branch within 1 ms of main's in the same run.

```python
"""Hook-run and command medians for one or more launchers, interleaved (SPD-065).

  python3.14 -I -S tests/probes/hook_timing.py [ROUNDS] LAUNCHER [LAUNCHER ...]

Each launcher gets its own scratch SPUD_HOME (a copy of spud.config.json and `spud init`), so nothing is written into
any ledger.  Three warm-up rounds fill each home's bytecode and git-command caches; then every round runs every case
once per launcher, in turn, so a machine slowing down slows every launcher alike.  Prints, per case, each launcher's
median, min and max in ms, and the difference of each median from the first launcher's.  The pass for a change to the
hook path: every hook case within 1 ms of main's median in the same run.
"""

import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(os.path.dirname(os.path.dirname(HERE)), "spud.config.json")
PY = sys.executable
SESSION = "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b"


def cases(home):
    base = {"session_id": SESSION, "transcript_path": "/tmp/x.jsonl", "cwd": home, "permission_mode": "default"}
    pre = dict(base, hook_event_name="PreToolUse")
    return [
        ("PreToolUse(Bash)", ["hook", "PreToolUse"], dict(pre, tool_name="Bash", tool_use_id="t1", tool_input={"command": "git status"})),
        ("PreToolUse(Write)", ["hook", "PreToolUse"], dict(pre, tool_name="Write", tool_use_id="t2", tool_input={"file_path": home + "/n.md", "content": "x"})),
        ("SessionStart", ["hook", "SessionStart"], dict(base, hook_event_name="SessionStart", source="startup")),
        ("Stop", ["hook", "Stop"], dict(base, hook_event_name="Stop", stop_hook_active=False)),
        ("board (a command)", ["board"], None),
        ("python -I -S -c pass", None, None),
    ]


def setup(launcher):
    home = tempfile.mkdtemp(prefix="spud-hook-timing-")
    shutil.copy(CONFIG, home)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR")}
    env.update(SPUD_HOME=home, SPUD_USER_CLAUDE_DIR=os.path.join(home, ".user-claude"), SPUD_CONFIG_DIR=os.path.join(home, ".user-config"))
    subprocess.run([PY, "-I", "-S", launcher, "init"], env=env, check=True, capture_output=True)
    return home, env


def once(argv, stdin, env):
    started = time.perf_counter()
    proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, env=env)
    elapsed = (time.perf_counter() - started) * 1000
    if proc.returncode:
        sys.exit("%s: exit %d %s" % (" ".join(argv), proc.returncode, proc.stderr))
    return elapsed


def main():
    args = sys.argv[1:]
    rounds = int(args.pop(0)) if args and args[0].isdigit() else 30
    launchers = [os.path.abspath(a) for a in args]
    if not launchers:
        sys.exit(__doc__)
    homes = [setup(launcher) for launcher in launchers]
    times = {}
    try:
        for rnd in range(3 + rounds):
            for launcher, (home, env) in zip(launchers, homes):
                for name, argv, payload in cases(home):
                    command = [PY, "-I", "-S", "-c", "pass"] if argv is None else [PY, "-I", "-S", launcher, *argv]
                    ms = once(command, json.dumps(payload) if payload else None, env)
                    if rnd >= 3:
                        times.setdefault((name, launcher), []).append(ms)
    finally:
        for home, _ in homes:
            shutil.rmtree(home, ignore_errors=True)
    print("%d rounds, launchers interleaved; ms" % rounds)
    for name, _, _ in cases("/tmp"):
        first = statistics.median(times[(name, launchers[0])])
        for launcher in launchers:
            v = times[(name, launcher)]
            med = statistics.median(v)
            print("%-22s %-60s median %6.2f  min %6.2f  max %6.2f  vs first %+6.2f" % (name, launcher[-60:], med, min(v), max(v), med - first))


if __name__ == "__main__":
    main()
```

## 6. Phase 2: commits and verification

### 6.1 Commits, in Eric's order

One worktree, `spd-065-split`, and one branch, landed in one merge. Every commit leaves the suite green.

**While modules move,** `bin/spud_ledger.py` is both the entry and the rest of the program. A moved module may reference only modules already moved, never the entry; the entry imports the moved modules at its top and qualifies its references to them; `__getattr__` serves the suite's reads of moved names. A script checked that each commit below references only modules of that commit or earlier ones.

| # | Commit | Modules | Eric's phase |
| --- | --- | --- | --- |
| 1 | **Scaffolding.** `bin/spud`'s `CachedLoader`; the entry's `PackageFinder`, `install_finder`, `owners`, `__getattr__`; the refusal moved above the entry's other statements, so a direct run imports nothing while moved modules are imported at the entry's top; `tests/probes/hook_timing.py`; the `headless.py` copy line (closes SPD-052). Nothing moves, so behaviour is trivially the same. | 0 | (scaffolding) |
| 2 | `core/kernel`, `core/lazy`, `core/markdown`, `state/schema`, `state/transcripts`, `render/prices`; with the first modules, the `test_launcher`, `helpers` and `headless_projects.py` edits | 6, 1127 lines | shared types and pure utilities |
| 3 | `core/homeconf`, `state/ledgerdb`, `state/lookup`, `state/ops`, `state/backup`, `state/actors`, `hooks/hookio`, `hooks/worktrees`; the three `test_backup` edits land here, with `state/backup` | 8, 1285 | state and services |
| 4 | `render/teamcard`, `render/workedon`, `render/sectiontext`, `render/notefiles`, `imports/*` | 7, 1299 | sub-components |
| 5 | `projects/*` and `commands/*` | 14, 2612 | sub-components |
| 6 | `shell/*` and `hooks/pathrule` | 15, 3674 | sub-components |
| 7 | `hooks/pretool`, `recording`, `subagent_stop`, `sessionhooks`, `stophook`, `dispatch` (with edit 7), `cli/helptexts`, `cli/cliparser` | 8, 1404 | sub-components |
| 8 | **The entry as orchestrator:** only its docstring, the finder, `HookCall`, `main` and the refusal remain, the refusal last again because the entry now imports nothing at its top; `main` imports per branch; `tests/test_package.py`, the guard test; the recommended `test_init` edit. | entry, 191 lines | the entry reduced to an orchestrator |

Commit 2 takes `state/schema`, `state/transcripts` and `render/prices` with `core/` because `core/homeconf` needs `prices` (through `Ctx.pricing`), and these three depend only on `core/kernel` and `core/lazy`. They are data and pure functions, so the phase still fits.

**How the moves are made.** A script reads §3's ranges and cuts each statement verbatim, with the comments above it, into its module, then qualifies references and writes the import lines. Nothing is retyped. The reviewer re-runs the three checks of §3 on their own copy.

### 6.2 Verification recipe, and the guard test (brief item g)

**The guard test, `tests/test_package.py`,** parses every file under `bin/spudlib/` with `ast`, the entry's `from spudlib… import` statements, and `hooks/dispatch`'s `HOOK_HANDLERS` literal. It imports nothing but helpers. Six tests:

1. **Imports:** a module imports the package relatively, names only modules that exist, and imports them only at its top.
2. **Names:** every name bound at a module's top is bound in one module only, and no module binds a name it imports a module as, in any scope.
3. **Cycles:** for every `alias.name` read outside a function or lambda body, the module read cannot reach the reader through top-level imports.
4. **Handlers:** each `HOOK_HANDLERS` entry names a function defined in its module.
5. **Reachability:** every module is reachable from the entry's imports, with `dispatch`'s handler modules as edges.
6. **The hook path:** each of the seven hooks, run through the launcher in a scratch home, imports only modules on `HOOK_PATH`, and together they import all of it. `HOOK_PATH` is the exact set, 35 modules, so a module that joins or leaves it is a reviewed edit, with the timing harness run again.

In the prototype all six pass, and three broken copies each failed: an import naming no module; a module reading `walk.ShellWalk` at import time across the cycle; a hook module importing `commands/views`.

**At every commit,** run the suite. **Before the merge,** run all of these:

1. **The suite.** `python3.14 -I -S -m unittest discover -s tests -t tests`: 732 tests at `9e1deda`, 738 with the guard test; about eight minutes.
2. **The three checks of §3:** names, lines and statements against `git show main:bin/spud_ledger.py`. A scratch script of about 110 lines; §9 Q6 says whether to commit it.
3. **No bytecode in the repository.** After the suite, `find bin tests -name '*.pyc' -o -name __pycache__` prints nothing.
4. **Hook timing,** §5.3, main against the branch: each hook median within 1 ms.
5. **The session diff.** The same 44 CLI and hook calls against main's launcher and the branch's, each in a scratch home, compared after masking paths and times: all identical. A scratch script of about 85 lines.
6. **The probes, once on the branch.** `python3.14 -I -S tests/probes/headless_projects.py project-spud` and, with its copy fixed, `python3.14 tests/probes/headless.py background`: real `claude -p` sessions through the installed hooks, $0.10 to $0.50 each on haiku.
7. **After the merge, at the ledger root:** `spud doctor` and one session with hooks. The first hook caches the entry and its modules under `.spud/pycache/`. The hook lines name only `bin/spud`, so `settings sync` has nothing to change.

### 6.3 Evidence from the prototype (brief item h)

**The prototype** is `bin/spud` with `CachedLoader`, the entry above, and the 58 modules generated from §3 by script with edits 1-7. It was built in scratch copies of the repository that include its `.git`, so the tests that read git history run as they do in a checkout.

| Run | Tests | Failing | Wall time |
| --- | --- | --- | --- |
| Control: main's program | 732 | 2, `test_acceptance.CutoverReadinessTest` (SPD-076) | 473 s |
| Prototype A, tests unedited | 732 | 12: the control's 2, `test_backup` 5 (7 subtests), `test_launcher` 3 | 491 s |
| Prototype G, tests unedited | 732 | 12, the same as A | 481 s |
| **Prototype G, §4.2's edits and the guard test** | **738** | **2, the control's (SPD-076)** | 481 s |

**Other checks on G:**

- **`test_backup` alone,** for §4.2: (i) OK, (ii) OK, neither 5 failures.
- **Smoke:** `--version`, `init`, `board`, `ticket new`, `card` and a member's `git push` through PreToolUse answer as main. A hook cached the entry and its modules in the scratch home and nothing under `bin/`. The entry run directly exits 2 and writes nothing.
- **Session diff:** 44 steps, every one identical to main after masking. The steps: `--version`, `--help`, a usage error, init, board, ticket new, move and show, member new (a planned member and a refused tier), card `--json`, render, events, sql, settings sync `--dry-run`, project list, session show, schedule show, doctor, member resum, fleet, member show; SessionStart; PreToolUse on `git status`, a `.spud/` read, a member's `git push`, a redirect into `ledger/`, a Write into `ledger/`, an unplanned spawn and a planned one; PostToolUse binding it; SubagentStart; a member's Edit; SubagentStop holding for a Result; Stop; UserPromptSubmit; a malformed payload and a non-object Stop; the member's log and result; member finish; board `--brief`; the last 40 events.
- **The three checks of §3:** names 719 plus 2, each once; lines equal except edit 7's; statements 710 matched except edit 7's pair.
- **Timing** as in §5.2.

## 7. Modules over ~250 lines

Thirteen modules are over, counting each module's docstring and import lines. None was cut to fit a number. Each holds one function or class a move cannot divide, or one job.

| Module | Lines | What makes it large | Recommendation |
| --- | --- | --- | --- |
| `projects/install` | 348 | `install_project` 52 (5074-5125), `strip_ledger_settings` 38, `uninstall_project` 46 (5168-5213), four commands 121 (5216-5336); 10 modules imported, 98 qualified references | **Keep whole.** Uninstall undoes install record by record, and `project sync` re-runs install; apart they drift. The seam, if wanted: mechanics 5007-5213, commands 5216-5336. |
| `cli/cliparser` | 342 | `build_parser`, 282 lines (10688-10969), one function registering every command; imports 20 modules | **Keep whole.** The seam is an `add_<family>_parser(sub)` in each command module: a code change, and the step to a command importing only its own module. A later ticket if a command's +0.4 ms ever matters. |
| `shell/walk` | 326 | `ShellWalk`, 304 lines (7822-8125): one class whose methods share the walk's frames, directories and redirects | **Keep whole.** Dividing a class across files takes mixins: a code change. |
| `shell/zsh` | 295 | `mark_zsh_patterns` 185 (6717-6901), `_zsh_group` 53, `_scan_pairs` 38 | **Keep whole.** Three functions of one scanner. |
| `shell/redirect_globs` | 279 | `bounded_glob` 71, `_segment_regex` 51, brace expansion 9222-9290 | **Keep whole, leaning.** The thin seam is `shell/braces` (`_split_brace`, `_expand_one_brace`, `brace_expand`, about 75 lines), whose one user is `expand_redirect_target`. |
| `projects/sessions` | 277 | session mode and notices 4693-4768, claims and session commands 5339-5467, the skill text 4646-4672, and the board brief (4317-4343) moved in by edit 3 | **Keep whole.** It is what a session is told and how it is claimed; SessionStart and UserPromptSubmit read it together. The seam, if wanted: the three session commands (5373-5467) to a `commands/` module. |
| `shell/analyse` | 275 | `analyse_words` 198 (8630-8827), `analyse_command`, `isolated`, `analyse_isolated`, `analyse_segment` | **Keep whole.** `analyse_words` is the dispatch every other shell module serves, and `analyse_command` is its way in. |
| `state/schema` | 268 | `DDL_0001`, 170 lines of SQL (269-438), `DDL_0002` 44 | **Keep whole.** The schema is data read as one. |
| `core/markdown` | 266 | the YAML subset, the document splitter, the entry parsers | **Keep whole.** Parsing and emitting frontmatter belong together. |
| `imports/accept` | 264 | `accept_ticket_edit` 64, `accept_member_edit` 51, `accept_file` 44 | **Keep whole.** |
| `hooks/worktrees`, `hooks/pathrule`, `shell/git_config` | 255, 255, 252 | two to five lines over, from their import lines | **Keep whole.** |

Just under the look-again point: `commands/schedule` 244 (with `spud backup` moved in), `shell/globbing` 243, `shell/expansions` 242.

The candidates named in the ticket's plan, and where they land:

| Candidate | Size | Module |
| --- | --- | --- |
| `cmd_ledger_commit` | 70 lines (5470-5539) | `commands/publish`, 196 |
| `build_parser` | 282 | `cli/cliparser`, 342 |
| `ShellWalk` | 304 | `shell/walk`, 326 |
| `analyse_words` | 198 | `shell/analyse`, 275 |
| `bash_reason` | 169 (9028-9196) | `shell/bash_rule`, 231 |
| `mark_zsh_patterns` | 185 | `shell/zsh`, 295 |

## 8. CLAUDE.md lines that change

Spud edits these; the spike only lists them.

| Line | Section | Today | After |
| --- | --- | --- | --- |
| 19 | The ledger CLI | "(since SPD-016 a launcher for `bin/spud_ledger.py`, whose bytecode it caches under `.spud/pycache/`; the module run directly refuses)" | "(since SPD-016 a launcher for `bin/spud_ledger.py`, the entry of the package `bin/spudlib/` since SPD-065; it caches every module's bytecode under `.spud/pycache/`, and the entry run directly refuses)" |
| 170 | Commands | "`# 732 tests, about eight minutes, leaves no bytecode`" | the count the branch's final run prints: 738 with the guard test |
| after 170 | Commands | none | a block: `python3.14 -I -S tests/probes/hook_timing.py 30 bin/spud <other checkout>/bin/spud   # hook-run medians, interleaved; a hook change passes within 1 ms of main` |

Nothing else changes. "Main and worktrees" puts `bin/` on the branch, which covers `bin/spudlib/`. `.claude/agents/spudagent.md` and `ledger/Home.md` do not name the file. `docs/design/2026-09-14-cross-repository-projects.md` names it as a record of that design and stays as written. `.gitignore` needs nothing, because no `__pycache__` is ever written under `bin/`. The launcher's and the entry's docstrings change with the code.

## 9. Open questions for Eric

Eric's four decisions of 2026-09-15 closed Burbank's Q1 (how the parts join), Q2 (a hook loads only what it needs), Q6 (commit the harness) and Q9 (fix `headless.py` in this ticket). What remains:

| # | Question | Options | Recommendation |
| --- | --- | --- | --- |
| Q1 | Where a patch lands | (i) tests patch the defining module: 3 lines; (ii) the entry forwards writes: 25 lines of program and a `gc` walk | **(i)** (§4.2). |
| Q2 | Edit 7: a hook imports only its own event's handler module | yes: one code edit in `hooks/dispatch`, measured within the pass with room; no: pure moves, F's +0.6 to +1.1 ms, which misses the pass on Stop in one run of two | **Yes** (§5.2). |
| Q3 | Module names | unique basenames no local variable uses (`ledgerdb`, `lookup`, `ops`, `hookio`, `stophook`, `cliparser`, …); or short names (`db`, `rows`, `stop`) with `import … as` wherever a function shadows one | **Unique names:** one spelling per module everywhere, which the guard test checks. Any spelling Eric prefers passes the same check. |
| Q4 | The package's name | `bin/spudlib/`; `bin/spud_ledger/` (collides with the entry), `bin/spud_ledger.d/` (not importable), `bin/ledger/` (the rendered ledger's word) | **`bin/spudlib/`** (§2.2). |
| Q5 | One more guard assertion: no test sets or patches an attribute of the module `load_spud_module()` returns | add it (the survey script of §4.1, about 20 lines, inside `tests/test_package.py`); or rely on review | **Add it.** Under (i) such a patch lands nowhere and may pass by luck. |
| Q6 | Commit the three checks of §3 and the session diff as probes? | `tests/probes/split_check.py`, `tests/probes/session_diff.py`; or scratch scripts | **The session diff, yes:** any later refactor of the program wants it. **The split check, no:** it compares against the one-file program and matters only on this branch. |
| Q7 | A merge rewriting 60 files while a hook runs (§4.1, item 7) | accept: one refused tool call at worst, during a merge Spud runs; or a version stamp in the entry and `core/kernel`, and a clean `HookError` on a mismatch | **Accept.** The stamp guards a millisecond window whose outcome is already fail-closed. |
| Q8 | The 13 modules over ~250 (§7) | keep all whole; or take the thin seams (`projects/install` commands, `shell/braces`, the session commands) | **Keep all whole.** |
| Q9 | Directories without `__init__.py` | as prototyped: the finder makes each directory a package with no file, 0.3 ms faster in-process; or an `__init__.py` holding each directory's docstring | **Without.** Each directory's purpose is in §2.1 and in its modules' docstrings. |
