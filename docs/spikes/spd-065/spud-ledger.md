# SPD-065 spike: splitting `bin/spud_ledger.py`

*2026-09-15 · [[SPD-065]] · Burbank (02, architect, opus) · Phase 1, planning only: nothing under `bin/` or `tests/` changed.*

Every line number below is `bin/spud_ledger.py` on `main` at `9e1deda`: **11065 lines**, not the 10663 of the ticket's survey (SPD-063 and SPD-064 landed since). The file has **727 top-level statements binding 734 names**: 13 are standard-library imports, 721 are the program's own. The ticket's "664 symbols" is an older count. The spans, the move list, the dependency figures and the hook-path closure were computed from the file's syntax tree, not by eye. The prototype, the timings and the suite runs used a scratch copy built mechanically from the move list, in the session's scratchpad. The scripts are not committed; §6 says how Phase 2 reproduces them.

## The recommendation in six lines

1. **One namespace, many files.** The program stays one module, `spud_ledger`. Its 721 names move verbatim into **57 part files** under `bin/spud_ledger.d/`, which the entry file executes into its own namespace in a fixed load order. Every name stays an attribute of `spud_ledger`, every call site looks names up in that same dictionary, and every monkeypatch lands (§4.2).
2. **Why not real modules with imports:** the parts' reference graph has a strongly connected group of 10 shell parts and 27 references from a part to one later in load order. `from x import name` would need cycles broken by code changes, and it moves patch targets. Rejected on that evidence (§2.4).
3. **The launcher keeps the one cache rule** and hands the entry its `get_code`, so every part's bytecode is cached under `<home>/.spud/pycache/<mirror of the part's path>`, and nothing is written when that directory is absent (§2.3).
4. **A hook run loads 34 of the 57 parts.** Render, import, the commands, project install, schedule, doctor and the parser load only when a command runs, or when the suite asks the module for one of their names. Measured: a hook run is at parity with today (+0.0 to 0.4 ms). Loading all 57 would cost +2.3 ms (§5).
5. **Behaviour:** a scripted session of 36 CLI and hook calls is byte-identical after masking paths and times; the only differences are the member name `draw_name` picks at random. The suite results are in §6.3.
6. **Phase 2** is eight commits on one branch in Eric's order (§6). Nine parts sit over ~250 lines; each is listed with its reason in §7.

## 1. Responsibility map

The file's own banners (`# ----` headings) divide it into sixteen regions. "Depends on" counts the references from the region's statements to names defined in another region, standard-library imports excluded. Every reference is resolved when the function runs, never when it is defined; no statement needs a later-defined name at definition time.

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
| Hook plumbing and path rule | 5543-6374 | hook constants 5555-5583; **207 lines of shell, git and glob constants** 5585-5791, used only by shell analysis; `HookError`, `HookOutput`, `pre_decision` 5794-5808; the spool 5811-5875; globs and case folding 5878-5957; worktrees and checkouts 5960-6068; file identity and path readings 6071-6181; outside roots 6191-6236; git config files 6245-6263; `path_reason`, the state directory, `edit_reason` 6266-6374 | header 4, database 3, rows 2, time 1, domain 1, render 1 |
| Shell analysis | 6377-9492 | `ShellAnalysis` 6380; text preparation 6419-6612; zsh patterns 6617-6901; tokens, redirects, wrappers, prefixes 6904-7059; git line options 7062-7233; git's own commands 7236-7289; a repository's own config 7292-7483; git verbs and targets 7486-7619; spud call parsing and `spud_launcher` 7622-7686; `analyse_command` 7689-7748; qualifiers 7751-7800; `ShellFrame`, `ShellWalk` 7803-8125; cd 8128-8220; glob readings 8223-8382; expansions 8387-8494; read points 8497-8620; `analyse_segment`, `analyse_words` 8623-8827; vouching 8830-8926; spud-call checks 8929-8947; git targets and scopes 8950-9025; `bash_reason` 9028-9196; redirection globs 9199-9492 | hook plumbing 159 (the constants above), database 2 (`git_env`), header 2, rows 2 |
| Hook handlers | 9495-10606 | PreToolUse handlers 9498-9720; binding 9726-9750; **stored usage** 9763-9801; completion, PostToolUse, SubagentStart 9804-9910; **transcript sums** 9920-10043; children holds and SubagentStop 10046-10260; SessionStart 10263-10280; UserPromptSubmit 10286-10358; Stop 10367-10559; `HOOK_HANDLERS`, `cmd_hook` 10562-10606 | hook plumbing 28, database 20, projects 18, rows 14, time 12, header 4, commands 3 (`Result`, `board_brief_text`, `member_status_change`), shell analysis 2, domain 1, actors 1 |
| Parser and main | 10610-11065 | help texts 10613-10685; `build_parser` 10688-10969; `normalize_argv` 10972-10993; `HookCall` 10996-11007; `main` 11010-11060; the refusal 11063-11065 | commands 36, projects 12, header 10, hook plumbing 3, database 2, hook handlers 2, home 2, markdown 1, actors 1 |

**The banners are not the seams.** Four regions hold code their neighbours own. The move list puts each piece where its users are:

- **Render holds cost and file helpers.** `price_table` (1678-1741) is read by `Ctx` and `doctor`. `sha256_bytes` and `write_whole` (2336-2351) are used by render, import, settings sync and project install.
- **Commands holds what the hooks call.** `Result` (2978), `board_brief_text` (4327, SessionStart), `member_status_change` (3831, `bind_member`) and `planning_session` (3796, `unclaimed_session_project`).
- **Hook handlers holds the transcript sums** (9920-10043) that `member resum` and cost read, and the stored-usage helpers (9763-9801).
- **Hook plumbing holds shell analysis's constants** (5585-5791), and `GIT_REDIRECTS` (5962), which `git_env` in the database region reads.

## 2. Proposed tree and loader

### 2.1 The tree

```
bin/
  spud                 the launcher; stays. 46 lines today (the ticket says 55), about 60 after it hands the entry its get_code (§2.3)
  spud_ledger.py       the entry: docstring, loader, PARTS and HOOK_PARTS, HookCall, main, the refusal (~200 lines)
  spud_ledger.d/       the program's parts, executed in the entry's namespace, never imported
    core/      base 175 · markdown 263 · home 159
    state/     schema 270 · db 98 · rows 188 · actors 107 · domain 224 · usage 186 · backup 146
    render/    cost 207 · teamcard 197 · workedon 171 · sections 145 · notes 151
    imports/   notes 186 · bulk 183 · accept 256
    commands/  common 37 · admin 110 · doctor 154 · schedule 169 · settings 205 · publish 187
               tickets 128 · proposals 159 · members 146 · resum 158 · views 190
    projects/  sessions 243 · registry 229 · install 336
    hooks/     base 130 · checkouts 249 · pathrule 249 · pre 230 · record 141
               subagent_stop 219 · session 100 · stop 203 · dispatch 49
    shell/     syntax 164 · prepare 198 · zsh 291 · dirs 233 · git_verbs 230 · git_programs 231
               git_config 244 · spud_calls 189 · globs 235 · expansions 240 · walk 327
               analyse 271 · redirect_globs 275 · bash_rule 226
    cli/       help 81 · parser 322
```

Counts are projected lines: each moved statement with the comments and blank lines above it, plus a two-line comment header. The parts total 11090 lines, and the entry about 200. The extra ~225 lines over 11065 are the 57 headers (114 lines), the loader and the two part lists.

The directories follow Eric's phase order. `core/` is shared types and pure utilities. `state/` is state and services. `render/`, `imports/`, `commands/`, `projects/`, `hooks/`, `shell/` and `cli/` are the sub-components. The entry is the orchestrator. Inside a directory, a part is one responsibility a reader can hold: `shell/walk.py` is `ShellWalk`; `hooks/stop.py` is what a stopping session owes.

### 2.2 The name `bin/spud_ledger.d/`

- **`.d` says what the directory is.** `.d` is the Unix convention for "the pieces of X" (`conf.d`, `profile.d`), and it sorts beside `spud_ledger.py`.
- **It cannot be imported by name**, because a dot is not a Python identifier. That is exactly true of the parts: they are loaded by path and executed in one namespace, never imported.
- **Not `bin/spud_ledger/`.** A directory with the entry's stem reads as a Python package, which it is not: its files are not modules. And if `bin/` ever reached `sys.path`, `import spud_ledger` would find the directory before `spud_ledger.py`.
- **Not `bin/ledger/`.** Every brief, deliverable glob, `SPUD_PATHS` and `GENERATED_ROOTS` (5560-5562) already mean the rendered `ledger/` at the root by that word.
- **Not `bin/spudlib/`.** It loses the tie to the entry file and promises an importable library.

The parts are "parts" in this spike, not "modules", because they share one namespace. They are modules in the sense Eric's brief means: files a spudagent can hold and edit with a disjoint deliverable glob.

### 2.3 The loader

The entry keeps its path, `bin/spud_ledger.py`, so the launcher, `tests/helpers.py` and every hook line reach the program exactly as today. Its loader is about 40 lines:

```python
import os
import sys
from importlib.machinery import SourceFileLoader

PARTS_DIR = "spud_ledger.d"
PARTS = ("core/base", "core/markdown", "core/home", "state/schema", ...)   # all 57, in load order
HOOK_PARTS = ("core/base", "core/home", "state/schema", "state/db", ...)   # the 34 a hook run needs, same order
_LOADED_PARTS = []


def load_parts(names):
    """Execute each named part of the program once, in load order, in this module's own namespace."""
    get_code = globals().get("_launcher_get_code")
    for name in names:
        if name in _LOADED_PARTS:
            continue
        path = os.path.join(os.path.dirname(__file__), PARTS_DIR, name + ".py")
        if get_code is None:  # loaded by something other than the launcher (the suite): compile, write no bytecode
            quiet = sys.dont_write_bytecode
            sys.dont_write_bytecode = True
            try:
                code = SourceFileLoader(__name__, path).get_code(__name__)
            finally:
                sys.dont_write_bytecode = quiet
        else:
            code = get_code(path)
        exec(code, globals())
        _LOADED_PARTS.append(name)


load_parts(HOOK_PARTS)


def __getattr__(name):
    """A name from a part a hook run does not load, asked for from outside (the suite): every part is loaded first."""
    if len(_LOADED_PARTS) < len(PARTS):
        load_parts(PARTS)
        if name in globals():
            return globals()[name]
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
```

`HookCall` and `main` follow, verbatim from 10996-11060. The one change is a `load_parts(PARTS)` line before `build_parser()` in the branch that is not a hook. The refusal (11063-11065) stays last.

The launcher changes in one place. Today `load()` (lines 21-42) sets `sys.pycache_prefix` or `sys.dont_write_bytecode` around one `get_code`. After the split it wraps that in a local `get_code(source)`, uses it for the entry, and hands it to the entry:

```python
    cached = os.path.isdir(state)

    def get_code(source):
        """Compile one file of the program, reading and writing its bytecode by the rule above."""
        prefix, quiet = sys.pycache_prefix, sys.dont_write_bytecode
        try:
            if cached:
                sys.pycache_prefix = os.path.join(state, "pycache")
            else:
                sys.dont_write_bytecode = True
            return SourceFileLoader(NAME, source).get_code(NAME)
        finally:
            sys.pycache_prefix, sys.dont_write_bytecode = prefix, quiet
    ...
    module._launcher_get_code = get_code  # the program's parts are compiled and cached by the same rule (SPD-065)
```

The loader, reasoned through:

- **Found by path, never by name.** `__file__` is `join(realpath(dirname(launcher)), "spud_ledger.py")` from the launcher (lines 22-23), or `REPO/bin/spud_ledger.py` from `tests/helpers.py` (line 60). So the parts directory is always the one beside the program that was loaded. Nothing reads `sys.path` or the working directory. Under `-I -S` nothing else is on `sys.path` anyway.
- **Bytecode mirrors the path.** `SourceFileLoader.get_code` with `sys.pycache_prefix` set writes `<prefix>/<source directory without its leading slash>/<stem>.cpython-314.pyc`. Two examples:
  - the main checkout's `shell/walk.py` caches at `/Users/ericlugo/Personal/Spud/.spud/pycache/Users/ericlugo/Personal/Spud/bin/spud_ledger.d/shell/walk.cpython-314.pyc`;
  - a worktree's caches at `…/.spud/pycache/Users/ericlugo/Personal/Spud/.claude/worktrees/spd-065-split/bin/spud_ledger.d/shell/walk.cpython-314.pyc`.

  A worktree's program and the main checkout's never share a file, as the launcher's docstring promises today.
- **Nothing is written without the state directory.** `cached` is decided once per run, as today. Without `<home>/.spud/`, every `get_code` runs with `dont_write_bytecode` set. The suite's in-process load (`helpers.load_spud_module`) passes no `get_code`, so the fallback compiles with `dont_write_bytecode` forced. `bin/spud_ledger.py` run directly is refused (below) and takes the same fallback. In the prototype, loading every part through the fallback wrote no `.pyc` anywhere under the tree.
- **Standard-library imports keep Python's defaults**, as today: the prefix is set only around each part's `get_code` and restored before `exec`.
- **Parts open with a comment, never a docstring.** A code object compiled from a file that opens with a string stores it as `__doc__`, and executing it in the entry's namespace would replace `spud_ledger.__doc__`. The prototype's parts open with a two-line comment, and `spud_ledger.__doc__` is unchanged.
- **The refusal is unchanged.** `python3.14 -I -S bin/spud_ledger.py` loads the 34 hook parts through the fallback (writing nothing), then reaches the `__main__` guard. The guard writes the same line and exits 2.
- **Order is the contract.** `PARTS` is the load order. No statement needs, at definition time, a name defined by a later part: checked for all 727 statements against this order. The dependencies that do fix order are few: `GLOB_SAMPLES` (5777) is built from `shell/syntax`, `shell/git_verbs` and `hooks/base` constants, `PROMPT_CLAIM_CAP` (10288) from `SESSION_CONTEXT_CAP`, `HOOK_HANDLERS` (10562) from the handlers, and `analyse_segment`'s default `_CURRENT` (8623) from `shell/walk`. §9 Q5 proposes a guard test that keeps both invariants true: definition-time order, and the hook set's closure.

### 2.4 Why not real modules

A package whose modules import each other (`from .shell.walk import ShellWalk`) is the conventional shape, and the ticket's constraint text anticipates it. On this file it is the wrong trade:

- **Cycles.** Grouping references by the proposed parts gives 299 part-to-part edges, one strongly connected group of 10 shell parts (`analyse`, `bash_rule`, `dirs`, `expansions`, `git_config`, `git_programs`, `git_verbs`, `globs`, `redirect_globs`, `walk`), two smaller ones (`render/cost` with `render/teamcard`, `state/backup` with `state/db`), and 27 edges from a part to one later in load order. `from x import name` fails on a cycle unless the name is already defined when the import runs. Breaking the cycles means moving imports into functions or rewriting call sites as `module.name`: code changes across the program, which a zero-behaviour-change ticket should not carry.
- **Import headers.** Each part would import a median of 16 and at most 66 names from the others: generated boilerplate that drifts.
- **Patches.** A name imported by value no longer patches at its call site. `tests/test_backup.py` patches `do_backup` (lines 264, 303) and `backup_stamp` (228) on the module and then runs `main`. Both would need new targets, and so would every future test that patches.
- **Globals.** `LazyModule.__getattr__` rebinds the name in `globals()` of the module that defines the class (line 43), not of the module that uses it. `git_own_commands` rebinds `_GIT_OWN_COMMANDS` with `global` (7261). Both work unchanged only in one namespace.
- **Cost.** Every module through the import system adds finder, spec and module creation on top of the file read, and would still need a custom finder, or the standard `PathFinder` would write `__pycache__` into `bin/spud_ledger.d/`.

What one namespace gives up is explicit dependencies: a part does not say what it uses from other parts. Two things make up for it:

- **The guard test (Q5)** makes the load order and the hook set checked facts.
- **A reader loses nothing against today:** the single file has exactly this namespace.

## 3. Move list

One table per part, in load order. A row is a run of consecutive top-level statements that move together. "Lines on main" runs from the first statement's first line to the last statement's last line, and the comments and blank lines above the first statement move with it. Every one of the 734 names appears in exactly one row, checked by script: none missing, none twice. "hook path" marks the 34 parts a hook run loads.

### `core/base.py` (175 lines, 51 symbols, hook path)

Shared types, constants, the clock and small pure utilities.

| Lines on main | Symbols |
| --- | --- |
| 19-143 | `bisect`, `contextlib`, `fcntl`, `functools`, `json`, `os`, `re`, `shlex`, `sqlite3`, `sys`, `time`, `datetime`, `Path`, `LazyModule`, `argparse`, `fractions`, `hashlib`, `subprocess`, `tempfile`, `VERSION`, `MARKER`, `EXIT_OK`, `EXIT_ERROR`, `EXIT_USAGE`, `EXIT_OWNERSHIP`, `EXIT_LIMIT`, `EXIT_TRANSITION`, `EXIT_CONFLICT`, `PRIORITIES`, `TICKET_STATUSES`, `MEMBER_STATUSES`, `MODELS`, `PERSONAS`, `ALIVE`, `TICKET_TRANSITIONS`, `MEMBER_TRANSITIONS`, `EVENT_KINDS`, `TICKET_FM_KEYS`, `TICKET_SECTIONS`, `MEMBER_SECTIONS`, `TICKET_COLUMN_SECTIONS`, `MEMBER_COLUMN_SECTIONS`, `SECTION_TABLES`, `SpudError`, `now`, `fm_date`, `fm_minute` |
| 2336-2351 | `sha256_bytes`, `write_whole` |
| 2978-2999 | `Result`, `table` |

### `core/markdown.py` (263 lines, 18 symbols)

markdown-v0: the YAML subset, documents, log/handoff/report entries.

| Lines on main | Symbols |
| --- | --- |
| 736-989 | `yaml_scalar`, `LIST_ITEM`, `parse_frontmatter`, `yaml_quote`, `yaml_list`, `emit_frontmatter`, `strip_blank_edges`, `strip_trailing_blank`, `frontmatter_block`, `restyled_render`, `split_document`, `LOG_ENTRY`, `parse_log_entries`, `HANDOFF_LINE`, `parse_handoff_line`, `REPORT_HEADING`, `parse_report_entries`, `normalize_markdown` |

### `core/home.py` (159 lines, 9 symbols, hook path)

Where the ledger is: home resolution, Ctx and config, git subprocess helpers, user-scope directories.

| Lines on main | Symbols |
| --- | --- |
| 155-262 | `resolve_home`, `Ctx`, `config_problems` |
| 697-720 | `git_env`, `run_git`, `git_remote_url` |
| 4675-4683 | `user_claude_dir`, `spud_config_dir` |
| 5962-5962 | `GIT_REDIRECTS` |

### `state/schema.py` (270 lines, 5 symbols, hook path)

The SQL schema and its migrations as data.

| Lines on main | Symbols |
| --- | --- |
| 269-530 | `DDL_0001`, `VIEWS_AND_TRIGGERS`, `DDL_0002`, `MIGRATIONS`, `SCHEMA_VERSION` |

### `state/db.py` (98 lines, 6 symbols, hook path)

Connections, transactions, migrations applied, config rows mirrored, events written.

| Lines on main | Symbols |
| --- | --- |
| 533-565 | `open_connection`, `connect`, `write_txn` |
| 644-694 | `apply_migrations`, `sync_config_rows` |
| 723-728 | `write_event` |

### `state/rows.py` (188 lines, 13 symbols, hook path)

Row lookups and row dicts; member and project handles.

| Lines on main | Symbols |
| --- | --- |
| 1009-1164 | `member_ref`, `get_ticket`, `get_ticket_by_id`, `get_member_by_id`, `HANDLE_RE`, `get_member`, `ticket_dict`, `member_dict`, `proposal_dict`, `event_dict` |
| 1464-1467 | `persona_label` |
| 2234-2236 | `project_key_of` |
| 4686-4690 | `get_project` |

### `state/actors.py` (107 lines, 10 symbols, hook path)

Actors, ownership and the session a command runs in.

| Lines on main | Symbols |
| --- | --- |
| 1172-1261 | `Actor`, `resolve_actor`, `ACTIVE_CTX`, `require_spud`, `claim_of`, `unclaimed_session_project`, `require_member`, `is_ancestor`, `require_ancestor` |
| 3796-3801 | `planning_session` |

### `state/domain.py` (224 lines, 15 symbols, hook path)

Domain operations: tickets, transitions, names, deliverables, planning, member status changes.

| Lines on main | Symbols |
| --- | --- |
| 1269-1456 | `next_ticket_number`, `ticket_key`, `insert_ticket`, `check_transition`, `draw_name`, `QUALIFIED_GLOB`, `glob_scope`, `check_deliverable_projects`, `normalize_deliverable`, `normalize_bare_deliverable`, `normalize_deliverables`, `plan_member`, `create_ticket_for_proposal` |
| 3817-3841 | `status_stamps`, `member_status_change` |

### `state/usage.py` (186 lines, 12 symbols, hook path)

Transcript sums and stored usage columns.

| Lines on main | Symbols |
| --- | --- |
| 9749-9750 | `as_int` |
| 9766-9801 | `usage_parts`, `run_totals` |
| 9904-10043 | `parse_timestamp`, `REQUEST_COUNTING`, `TOKEN_KEYS`, `BREAKDOWN_IDENTITY`, `BREAKDOWN_TOKENS`, `usage_count`, `usage_breakdown`, `request_key`, `transcript_usage` |

### `state/backup.py` (146 lines, 12 symbols)

Backups: the copy, the daily keep, and `spud backup`.

| Lines on main | Symbols |
| --- | --- |
| 146-147 | `backup_stamp` |
| 568-641 | `do_backup`, `DAILY_LABEL`, `DAILY_KEEP`, `DAILY_NAME_RE`, `BACKUP_NAME_RE`, `backups_dir`, `backup_listing`, `backup_quick_check`, `prune_daily_backups` |
| 3040-3101 | `cmd_backup`, `daily_backup` |

### `render/cost.py` (207 lines, 17 symbols, hook path)

List-price cost from spud.config.json pricing.

| Lines on main | Symbols |
| --- | --- |
| 1648-1839 | `PRICE_RATES`, `SPLIT_RATES`, `UNSPLIT_RATE`, `PRICE_MULTIPLIERS`, `PRICE_DATE`, `COST_USD`, `NO_TABLE`, `NO_BREAKDOWN`, `price_number`, `price_rates`, `price_table`, `bucket_cost`, `run_cost`, `cents_of`, `usd_text`, `money`, `cost_number` |

### `render/teamcard.py` (197 lines, 20 symbols)

The Team table and card cells.

| Lines on main | Symbols |
| --- | --- |
| 1470-1634 | `team_line`, `prose_for`, `join_prose`, `TEAM_TABLE_HEAD`, `TEAM_TOTAL_LABEL`, `TEAM_VIEW_EMBED`, `NOT_RECORDED`, `WORKED_ON_CAP`, `humanize`, `run_duration`, `known_stamp`, `run_cell`, `token_counts`, `card_cell`, `team_table_row`, `team_order`, `usage_pairs`, `tokens_text` |
| 1842-1867 | `team_totals`, `team_total_row` |

### `render/workedon.py` (171 lines, 26 symbols)

The worked-on sentence: markdown blocks to one line.

| Lines on main | Symbols |
| --- | --- |
| 1872-2036 | `MD_COMMENT`, `MD_FENCE`, `MD_HEADING`, `MD_RULE`, `MD_SETEXT`, `MD_ITEM`, `MD_QUOTE`, `MD_TABLE`, `MD_CODE`, `MD_WIKILINK`, `MD_LINK`, `STOP_LABELS`, `BOLD_LABEL`, `PLAIN_LABEL`, `STATUS_START`, `WHERE_START`, `HANDLE_START`, `markdown_blocks`, `paragraph_label`, `is_stop_label`, `is_status_line`, `protected_spans`, `sentence_ends`, `cut_worked_on`, `worked_on_text`, `worked_on` |

### `render/sections.py` (145 lines, 11 symbols)

Section bodies rendered from rows.

| Lines on main | Symbols |
| --- | --- |
| 2039-2179 | `render_team_section`, `handoff_party`, `render_handoff_rows`, `decision_trail`, `render_received_rows`, `render_log_rows`, `render_subagent_rows`, `render_proposal_rows`, `ticket_section_text`, `member_section_text`, `body_with_sections` |

### `render/notes.py` (151 lines, 10 symbols)

Whole notes: ticket, member, Projects.md, report; the render target list.

| Lines on main | Symbols |
| --- | --- |
| 2182-2231 | `render_ticket`, `render_member` |
| 2240-2333 | `PROJECTS_NOTE`, `PROJECTS_COLUMNS`, `table_cell`, `table_cells`, `render_projects`, `import_projects_file`, `render_report`, `render_targets` |

### `imports/notes.py` (186 lines, 10 symbols)

markdown-v0 ticket and member notes into rows.

| Lines on main | Symbols |
| --- | --- |
| 2359-2535 | `parse_link`, `store_prose_if_needed`, `ticket_layout`, `member_layout`, `require_keys`, `import_ticket_file`, `MEMBER_USAGE_KEYS`, `SQLITE_INTEGER_MAX`, `usage_columns`, `import_member_file` |

### `imports/bulk.py` (183 lines, 6 symbols)

The markdown-v0 tree import.

| Lines on main | Symbols |
| --- | --- |
| 2538-2716 | `import_handoffs`, `TEAM_LINE`, `import_team_summaries`, `handoff_party_id`, `link_proposal`, `bulk_import` |

### `imports/accept.py` (256 lines, 12 symbols)

import --file: accepting a hand edit of a rendered file.

| Lines on main | Symbols |
| --- | --- |
| 2719-2970 | `classify_path`, `heading_tail`, `edited_keys`, `refuse`, `check_section_layout`, `TICKET_SECTION_COMMANDS`, `MEMBER_OWN_SECTIONS`, `MEMBER_SECTION_COMMANDS`, `accept_ticket_edit`, `accept_member_edit`, `accept_report_edit`, `accept_file` |

### `commands/common.py` (37 lines, 5 symbols)

What several commands share: report entries and --next.

| Lines on main | Symbols |
| --- | --- |
| 3642-3674 | `check_next`, `no_entry_for_next`, `write_report_entry`, `report_entry_line`, `with_report_entry` |

### `commands/admin.py` (110 lines, 6 symbols)

init, migrate, config sync, sql, import.

| Lines on main | Symbols |
| --- | --- |
| 3002-3037 | `cmd_init`, `cmd_migrate` |
| 3271-3281 | `cmd_config_sync` |
| 3466-3520 | `READ_ONLY_FIRST_WORDS`, `cmd_sql`, `cmd_import` |

### `commands/doctor.py` (154 lines, 2 symbols)

doctor: interpreter, SQLite, home, database, config, backups, projects.

| Lines on main | Symbols |
| --- | --- |
| 4476-4625 | `cmd_doctor`, `doctor_projects` |

### `commands/schedule.py` (169 lines, 13 symbols)

The daily-backup LaunchAgent.

| Lines on main | Symbols |
| --- | --- |
| 3106-3268 | `SCHEDULE_LABEL`, `SCHEDULE_AT`, `SCHEDULE_LOG`, `BOOTSTRAP_RETRY_DELAYS`, `at_arg`, `require_spud_flag`, `schedule_plist_path`, `schedule_plist`, `launchctl`, `write_plist`, `cmd_schedule_show`, `cmd_schedule_install`, `cmd_schedule_uninstall` |

### `commands/settings.py` (205 lines, 15 symbols)

settings sync and the settings merge project install shares.

| Lines on main | Symbols |
| --- | --- |
| 3285-3463 | `HOOK_TABLE`, `HOOK_TIMEOUT`, `HOOK_MARK`, `ALLOW_RULE_MARK`, `hook_command`, `cli_allow_rules`, `is_ledger_hook`, `merge_hooks`, `merge_allow_rules`, `AGENT_DENY_RULES`, `merge_deny_rules`, `merge_additional_dirs`, `merge_settings`, `cmd_settings_sync` |
| 4852-4870 | `settings_hold_hooks` |

### `commands/publish.py` (187 lines, 3 symbols)

render and ledger commit.

| Lines on main | Symbols |
| --- | --- |
| 3523-3632 | `cmd_render` |
| 4643-4643 | `SUBJECT_TICKET_KEY` |
| 5470-5539 | `cmd_ledger_commit` |

### `commands/tickets.py` (128 lines, 6 symbols)

ticket new, move, edit, show.

| Lines on main | Symbols |
| --- | --- |
| 3639-3639 | `TICKET_MOVE_VERBS` |
| 3677-3793 | `cmd_ticket_new`, `cmd_ticket_move`, `cmd_ticket_edit`, `format_ticket`, `cmd_ticket_show` |

### `commands/proposals.py` (159 lines, 5 symbols)

proposal file, decide, list; handoff add; report add.

| Lines on main | Symbols |
| --- | --- |
| 4131-4285 | `cmd_proposal_file`, `cmd_proposal_decide`, `cmd_proposal_list`, `cmd_handoff_add`, `cmd_report_add` |

### `commands/members.py` (146 lines, 7 symbols)

member new, start, finish, edit, log/result/block, show.

| Lines on main | Symbols |
| --- | --- |
| 3804-3814 | `cmd_member_new` |
| 3844-3972 | `cmd_member_start`, `cmd_member_finish`, `cmd_member_edit`, `cmd_member_own`, `format_member`, `cmd_member_show` |

### `commands/resum.py` (158 lines, 8 symbols)

member resum.

| Lines on main | Symbols |
| --- | --- |
| 3979-4128 | `RESUM_FIGURES`, `RESUM_KEPT`, `RESUM_LEFT`, `stored_counting`, `find_transcript`, `resum_row`, `resum_table`, `cmd_member_resum` |

### `commands/views.py` (190 lines, 11 symbols, hook path)

events, board, fleet, card, member list.

| Lines on main | Symbols |
| --- | --- |
| 4288-4473 | `cmd_events`, `brief_state`, `board_brief_text`, `cmd_board`, `cmd_fleet`, `team_tree`, `flatten_team`, `format_tree`, `card_total_line`, `cmd_card`, `cmd_member_list` |

### `projects/sessions.py` (243 lines, 21 symbols, hook path)

Session mode, claims, the /spud skill text, session commands.

| Lines on main | Symbols |
| --- | --- |
| 4640-4642 | `CLAIM_CARD_CAP`, `SESSION_CONTEXT_CAP`, `PLAIN_NOTICE_CAP` |
| 4646-4672 | `SKILL_HEAD`, `SKILL_STEPS`, `SKILL_CLAIM`, `HOOK_CLAIM`, `SKILL_TITLE`, `skill_steps`, `skill_markdown` |
| 4693-4768 | `spudagent_shaped`, `pending_spawn`, `session_mode`, `fit_bytes`, `plain_session_notice`, `project_session_context` |
| 5339-5467 | `record_claim`, `claim_card`, `cmd_session_claim`, `cmd_session_release`, `cmd_session_show` |

### `projects/registry.py` (229 lines, 13 symbols)

project add, list, show, edit and their validation.

| Lines on main | Symbols |
| --- | --- |
| 4636-4637 | `PROJECT_KEY_RE`, `PREFIX_RE` |
| 4771-4849 | `identity_chain`, `validate_project_root`, `check_project_key`, `check_prefixes`, `origin_head_branch` |
| 4873-5004 | `project_dict`, `format_project`, `cmd_project_add`, `cmd_project_list`, `cmd_project_show`, `cmd_project_edit` |

### `projects/install.py` (336 lines, 14 symbols)

project install, uninstall, sync, remove.

| Lines on main | Symbols |
| --- | --- |
| 4638-4639 | `SETTINGS_LOCAL`, `EXCLUDE_COMMENT` |
| 5007-5336 | `install_files`, `read_json_object`, `git_common_dir`, `ensure_ignored`, `remove_exclude_block`, `install_project`, `strip_ledger_settings`, `uninstall_project`, `cmd_project_install`, `cmd_project_uninstall`, `cmd_project_sync`, `cmd_project_remove` |

### `hooks/base.py` (130 lines, 25 symbols, hook path)

Hook constants, HookError and HookOutput, the spool.

| Lines on main | Symbols |
| --- | --- |
| 5555-5583 | `HOOK_EVENTS`, `EDIT_TOOLS`, `ENFORCED_TOOLS`, `SPUD_PATHS`, `GENERATED_ROOTS`, `DESCRIPTION`, `AGENT_ID_RE`, `SPUD_COMMANDS`, `SPUD_ONLY_COMMANDS`, `SPUD_ONLY_SUBCOMMANDS`, `READ_ONLY_COMMANDS`, `READ_ONLY_SUBCOMMANDS`, `MEMBER_OWN_COMMANDS`, `DB_PATH_RE`, `STATE_DIR`, `DB_REASON`, `SUBST` |
| 5794-5875 | `HookError`, `HookOutput`, `SILENT`, `pre_decision`, `spool_path`, `spool_lock`, `spool_write`, `spool_drain` |

### `hooks/checkouts.py` (249 lines, 17 symbols, hook path)

Worktrees, project checkouts, file identity, path readings.

| Lines on main | Symbols |
| --- | --- |
| 5922-5947 | `_CASE_CACHE`, `case_insensitive_fs`, `folds_case` |
| 5963-6181 | `_WORKTREES`, `worktrees_fingerprint`, `git_worktree_list`, `project_root`, `checkout_worktrees`, `project_checkouts`, `project_of_path`, `cli_project_of`, `file_identity`, `same_entry`, `map_into_checkouts`, `path_readings`, `path_placements`, `project_paths` |

### `hooks/pathrule.py` (249 lines, 21 symbols, hook path)

Law 1 and Law 5 over a path: globs, outside roots, git config files, the state directory, edit_reason.

| Lines on main | Symbols |
| --- | --- |
| 5881-5919 | `glob_to_regex`, `path_matches_glob` |
| 5950-5957 | `HARNESS_FILES_RE`, `harness_file` |
| 6191-6374 | `SCRATCHPAD_ROOT`, `FIXED_TEMP_ROOTS`, `TEMP_ROOT_VARS`, `DEV_WRITE_ROOTS`, `OUTSIDE_PROJECT_REASON`, `outside_roots`, `under_outside_root`, `outside_project_reason`, `GIT_CONFIG_FILE_NAMES`, `GIT_CONFIG_FILE_TAILS`, `GIT_CONFIG_FILE_REASON`, `git_config_file`, `NOT_SPUD_HOME`, `path_reason`, `in_state_dir`, `state_dir_reason`, `edit_reason` |

### `shell/syntax.py` (164 lines, 54 symbols, hook path)

Shell grammar constants, sentinels, ShellAnalysis, tokens.

| Lines on main | Symbols |
| --- | --- |
| 5588-5681 | `OUT_REDIRECTS`, `IN_REDIRECTS`, `RESERVED_WORDS`, `WRAPPERS`, `WRAPPER_VALUE_OPTIONS`, `DURATION_RE`, `SHELL_OPERATORS`, `SHELL_PUNCTUATION`, `LIST_TERMINATORS`, `LOOP_PREFIX_WORDS`, `DIRECTORY_COMMANDS`, `SHELL_DECLARATIONS`, `_GLOB_META`, `_GLOB_SENTINELS`, `_GLOB_UNSENTINEL`, `_ZSH_PATTERN_CHARS`, `_ZSH_SENTINELS`, `ZSH_OPEN`, `ZSH_BAR`, `ZSH_CLOSE`, `ZSH_RANGE_OPEN`, `ZSH_RANGE_CLOSE`, `_ZSH_UNSENTINEL`, `_LITERAL_EQUALS`, `_LITERAL_DOLLAR`, `_QUOTED_DOLLAR`, `_ARRAY_VALUE`, `_NAME_END`, `_SENTINEL_TEXT`, `_LITERALIZE`, `_GLOB_SENTINEL_RE`, `GLOB_RE`, `ZSH_RANGE_RE`, `GLOB_MATCH_CAP`, `GLOB_SCAN_CAP`, `ARRAY_ASSIGNMENT_RE`, `ASSIGNMENT_WORD_RE`, `SHELLS`, `PYTHON_RE`, `JS_RUNTIMES`, `ASSIGNMENT_RE`, `VARREF_RE`, `_EXPANDING_DOLLAR_RE`, `_ASSIGNING_EXPANSION_RE`, `_NAME_RE`, `_NAME_CHAR_RE`, `_BARE_NAME_TAIL_RE`, `_IFS_BLANKS_RE`, `ASSIGNING_COMMANDS`, `DYNAMIC_VARIABLES`, `HEREDOC_RE` |
| 6380-6416 | `ShellAnalysis` |
| 6904-6923 | `shell_tokens`, `operator_parts` |

### `shell/prepare.py` (198 lines, 5 symbols, hook path)

Heredocs, substitutions, newlines and quoted globs before tokenizing.

| Lines on main | Symbols |
| --- | --- |
| 6419-6612 | `strip_heredocs`, `split_substitutions`, `newlines_as_separators`, `neutralize_quoted_globs`, `deglob` |

### `shell/zsh.py` (291 lines, 6 symbols, hook path)

zsh's own glob operators.

| Lines on main | Symbols |
| --- | --- |
| 6617-6901 | `ZSH_COMMAND_POSITION_WORDS`, `_PLAIN_RUN_RE`, `_ARRAY_NAME_RE`, `_scan_pairs`, `_zsh_group`, `mark_zsh_patterns` |

### `shell/dirs.py` (233 lines, 12 symbols, hook path)

Redirects, wrappers, prefixes and the directories the shell may be in.

| Lines on main | Symbols |
| --- | --- |
| 6926-7059 | `union_dirs`, `separate_redirects`, `strip_wrapper`, `prefix_effect`, `EFFECT_ORDER`, `builtin_runs`, `builtin_runs_here`, `settle` |
| 8128-8220 | `cdpath_entries`, `cd_target`, `cd_destinations`, `directory_change` |

### `shell/git_verbs.py` (230 lines, 21 symbols, hook path)

Law 7's verb table, git's own commands, repository targets.

| Lines on main | Symbols |
| --- | --- |
| 5682-5685 | `GIT_WRITE_VERBS`, `GIT_GLOBAL_VALUE_FLAGS` |
| 5755-5772 | `BRANCH_READ_FLAGS`, `BRANCH_READ_VALUE_FLAGS`, `TAG_READ_FLAGS`, `TAG_READ_VALUE_FLAGS`, `CONFIG_READ_FLAGS`, `CONFIG_VALUE_FLAGS`, `CONFIG_WRITE_FLAGS`, `CONFIG_READ_SELECTORS`, `CONFIG_WRITE_SUBCOMMANDS`, `CONFIG_READ_SUBCOMMANDS` |
| 7062-7073 | `git_verb` |
| 7236-7289 | `_GIT_OWN_COMMANDS`, `GIT_COMMANDS_CACHE`, `git_binary_fingerprint`, `git_own_commands` |
| 7486-7619 | `git_unknown_verb`, `git_repo_targets`, `flag_list_refused`, `git_refused` |

### `shell/git_programs.py` (231 lines, 24 symbols, hook path)

git config, environment and options that name a program.

| Lines on main | Symbols |
| --- | --- |
| 5689-5754 | `GIT_ALIAS_SECTIONS`, `GIT_CONFIG_FILE_VARS`, `GIT_CONFIG_INLINE_VARS`, `GIT_CONFIG_INDEXED_RE`, `GIT_CONFIG_HOME_VARS`, `GIT_REPO_ENV_VARS`, `GIT_REPO_OPTIONS`, `GIT_INERT_CONFIG_SECTIONS`, `GIT_INERT_CONFIG_KEYS`, `GIT_PAGER_ENV_VARS`, `GIT_PROGRAM_ENV_VARS`, `GIT_EXEC_PATH_OPTION`, `GIT_VERB_PROGRAM_OPTIONS` |
| 7076-7233 | `git_config_section`, `git_line_defines_alias`, `is_git_config_var`, `is_git_repo_var`, `git_env_defines_alias`, `git_inert_pager_value`, `git_config_key_allowed`, `git_line_names_program`, `is_git_program_var`, `git_env_names_program`, `git_verb_names_program` |

### `shell/git_config.py` (244 lines, 20 symbols, hook path)

The config a repository sets for itself (SPD-063).

| Lines on main | Symbols |
| --- | --- |
| 7306-7483 | `GIT_CONFIG_SCOPES_CACHE`, `GIT_OWN_SCOPES`, `GIT_CONFIG_SCOPES_KEPT`, `GIT_CONFIG_INCLUDE_FILES`, `GIT_CONFIG_READ_LIMIT`, `GIT_WALK_LIMIT`, `_GIT_INCLUDE_PATH_RE`, `GIT_PROGRAM_KEY_SECTIONS`, `GIT_PROGRAM_KEY_WORDS`, `git_config_key_names_program`, `git_repo_common_dir`, `git_repository_dirs`, `git_scope_config_files`, `git_config_fingerprint`, `git_run_config_scopes`, `git_own_config_keys` |
| 8980-9025 | `GIT_SCOPE_REASON`, `GIT_SCOPE_UNRESOLVED_REASON`, `GIT_SCOPE_UNREADABLE_REASON`, `git_local_config_reason` |

### `shell/spud_calls.py` (189 lines, 15 symbols, hook path)

Recognizing and vouching for a spud call.

| Lines on main | Symbols |
| --- | --- |
| 7622-7686 | `parse_spud_call`, `python_interpreter_args`, `spud_launcher` |
| 8830-8947 | `any_spud_launcher`, `PYTHON_FLAGS_RE`, `python_options_vouched`, `unresolvable_word`, `interpreter_vouched`, `launcher_vouched`, `spud_home_vouched`, `vouched_spud_call`, `QUIET_TARGETS`, `FINDING_LAST`, `actor_is_self`, `spud_call_writes` |

### `shell/globs.py` (235 lines, 20 symbols, hook path)

Glob words, qualifiers and their readings.

| Lines on main | Symbols |
| --- | --- |
| 5775-5791 | `GLOB_COMMAND_SAMPLES`, `GLOB_SAMPLES`, `GLOB_OPTION`, `GLOB_WORD_LIMIT`, `GLOB_READING_BUDGET`, `_EQUALS_RE`, `_STAR_RUN_RE` |
| 7751-7800 | `_QUALIFIER_CLOSERS`, `trailing_group`, `qualifier_code`, `_QUALIFIER_NAME_RE` |
| 8223-8382 | `active_glob_word`, `literalize`, `may_start_with_dash`, `glob_too_complex`, `glob_sample_matches`, `command_path`, `glob_readings`, `resolve_glob`, `analyse_readings` |

### `shell/expansions.py` (240 lines, 21 symbols, hook path)

Parameter expansions and the read points of a command.

| Lines on main | Symbols |
| --- | --- |
| 8387-8620 | `_AGAIN`, `_STOP`, `_FLAGGED`, `_ACTIVATE_GLOBS`, `_ZSH_PLAIN`, `_BRACES_PLAIN`, `expansion_word`, `variable_reference`, `active_read_word`, `assign_variable`, `variable_readings`, `resolve_expansion`, `first_read_index`, `git_read_index`, `shell_read_index`, `trap_action_indices`, `trap_read_index`, `analyse_trap`, `python_read_index`, `option_point`, `script_point` |

### `shell/walk.py` (327 lines, 3 symbols, hook path)

ShellFrame and ShellWalk: one pass over a line's tokens.

| Lines on main | Symbols |
| --- | --- |
| 7803-8125 | `ShellFrame`, `_CURRENT`, `ShellWalk` |

### `shell/analyse.py` (271 lines, 5 symbols, hook path)

analyse_command and analyse_words.

| Lines on main | Symbols |
| --- | --- |
| 7689-7748 | `analyse_command`, `isolated`, `analyse_isolated` |
| 8623-8827 | `analyse_segment`, `analyse_words` |

### `shell/redirect_globs.py` (275 lines, 11 symbols, hook path)

The files a redirection glob opens.

| Lines on main | Symbols |
| --- | --- |
| 9222-9492 | `_split_brace`, `_expand_one_brace`, `brace_expand`, `_has_bare_glob`, `_digit_span`, `numeric_range_regex`, `_GROUP_REGEX`, `_segment_regex`, `bounded_glob`, `expand_redirect_target`, `qualifier_readings` |

### `shell/bash_rule.py` (226 lines, 5 symbols, hook path)

bash_reason: the Bash hook's rule over a line, and where a git call's repository lands.

| Lines on main | Symbols |
| --- | --- |
| 8950-8977 | `git_target_dirs`, `git_repo_outside` |
| 9028-9219 | `bash_reason`, `redirection_paths`, `target_has_active_glob` |

### `hooks/pre.py` (230 lines, 8 symbols, hook path)

The PreToolUse handlers.

| Lines on main | Symbols |
| --- | --- |
| 9498-9720 | `deny_and_record`, `hook_agent_spawn`, `hook_bash`, `hook_edit`, `request_from_meta`, `bind_request`, `late_bind`, `hook_pre_tool_use` |

### `hooks/record.py` (141 lines, 5 symbols, hook path)

Binding and the recording handlers PostToolUse and SubagentStart.

| Lines on main | Symbols |
| --- | --- |
| 9726-9746 | `bind_member` |
| 9763-9763 | `COMPLETION_KEYS` |
| 9804-9901 | `record_completion`, `hook_post_tool_use`, `hook_subagent_start` |

### `hooks/subagent_stop.py` (219 lines, 8 symbols, hook path)

A parent's finishing rule and SubagentStop.

| Lines on main | Symbols |
| --- | --- |
| 10046-10260 | `RESULT_HOLD`, `unrecorded_children`, `alive_children`, `finish_commands`, `children_hold_reason`, `alive_children_reason`, `stop_hold_reason`, `hook_subagent_stop` |

### `hooks/session.py` (100 lines, 10 symbols, hook path)

SessionStart and UserPromptSubmit.

| Lines on main | Symbols |
| --- | --- |
| 10263-10358 | `hook_session_start`, `PROMPT_KEY_SHAPE`, `SPUD_COMMAND`, `PROMPT_CLAIM_CAP`, `prompt_is_spud_command`, `prompt_ticket_keys`, `prompted_ticket`, `released_by_command`, `prompt_claim_context`, `hook_user_prompt_submit` |

### `hooks/stop.py` (203 lines, 13 symbols, hook path)

What a stopping session owes, and Stop.

| Lines on main | Symbols |
| --- | --- |
| 10367-10559 | `PLANNED_GRACE_SECONDS`, `member_session`, `planned_long_ago`, `told_running`, `reservations`, `stop_owed`, `stop_item`, `returned_clause`, `planned_clause`, `unbound_clause`, `running_clause`, `stop_reason`, `hook_stop` |

### `hooks/dispatch.py` (49 lines, 2 symbols, hook path)

HOOK_HANDLERS and cmd_hook.

| Lines on main | Symbols |
| --- | --- |
| 10562-10606 | `HOOK_HANDLERS`, `cmd_hook` |

### `cli/help.py` (81 lines, 6 symbols)

The long help texts the parser shows.

| Lines on main | Symbols |
| --- | --- |
| 10613-10685 | `EPILOG`, `RESUM_DESCRIPTION`, `RESUM_EPILOG`, `BACKUP_DESCRIPTION`, `BACKUP_EPILOG`, `SCHEDULE_DESCRIPTION` |

### `cli/parser.py` (322 lines, 3 symbols)

build_parser, normalize_argv, text_arg.

| Lines on main | Symbols |
| --- | --- |
| 992-1001 | `text_arg` |
| 10688-10993 | `build_parser`, `normalize_argv` |


### `bin/spud_ledger.py` (the entry, about 200 lines)

The orchestrator: what stays, plus the loader of §2.3.

| Lines on main | What |
| --- | --- |
| 1-17 | the module docstring, amended to name `bin/spud_ledger.d/` |
| new | `import os`, `import sys`, `from importlib.machinery import SourceFileLoader`; `PARTS_DIR`, `PARTS`, `HOOK_PARTS`, `_LOADED_PARTS`, `load_parts`, `load_parts(HOOK_PARTS)`, `__getattr__` |
| 10996-11007 | `HookCall` |
| 11010-11060 | `main`, with `load_parts(PARTS)` before `build_parser()` |
| 11063-11065 | the refusal |

## 4. Compatibility

### 4.1 (a) The entry point

**What the launcher gets back.** `load()` returns the module `spud_ledger`: `__file__` is `bin/spud_ledger.py` beside the launcher's real path, and the 34 hook parts have executed. Line 45 is unchanged: `sys.exit(load().main())`.

**`main` as an orchestrator** is today's `main` (11010-11060) with one added line:

```python
def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) == 2 and argv[0] == "hook" and argv[1] in HOOK_EVENTS:
        args = HookCall(argv[1])
    elif len(argv) == 4 and argv[0] == "hook" and argv[1] in HOOK_EVENTS and argv[2] == "--project":
        args = HookCall(argv[1], argv[3])
    else:
        load_parts(PARTS)                                   # the one new line: a command loads every part
        args = build_parser().parse_args(normalize_argv(argv))
    ...                                                     # 11021-11060 unchanged: home, Ctx, dispatch, output, spool drain
```

**Why every command, exit code and hook behaves the same.**

- **The same statements run.** The parts are the file's own statements, verbatim, in an order that satisfies every definition-time reference.
- **Into the same dictionary.** They execute under the same `__name__`, so every function, class and constant is what the single file defines, and every name looked up at run time resolves in that dictionary as before.
- **Checked on the prototype.** Loaded side by side, the single file and the split have the same 734 names, with equal `__module__`, `__qualname__`, types and constant values.
- **Checked end to end.** A scripted session gave identical output after masking paths and timestamps. It covered `--version`, `--help`, a usage error, init, board, ticket new, move and show, member new (a planned member and a refused tier), card, render, events, sql, settings sync `--dry-run`, project list, session show, schedule show and doctor. It also covered hooks: SessionStart, PreToolUse Bash (a silent line, a `.spud/` read, a member's `git push`, a redirect into `ledger/`), PreToolUse Write into `ledger/`, a spudagent spawn with no planned row, Stop, UserPromptSubmit, a malformed payload, and a non-JSON Stop. The only differences were the member name `draw_name` picks at random.
- **The suite.** The results are in §6.3.

**What does change, all of it additive or invisible in output:**

1. **Six names join the namespace:** `PARTS`, `HOOK_PARTS`, `PARTS_DIR`, `SourceFileLoader`, `_LOADED_PARTS`, `load_parts`. So do `__getattr__` and, under the launcher, `_launcher_get_code`. None collides with the 734.
2. **Tracebacks name the part file and its line.** A hook failure is spooled as `type(e).__name__: e` (10593), so no ledger output carries a path.
3. **The state directory holds up to 58 `.pyc` files instead of one.**
4. **A syntax error in a part a hook run does not load** surfaces when a command first loads it, not on every run. The suite loads every part, so such an error cannot land.
5. **A merge is not atomic across 58 files.** A hook process that starts while `git merge` rewrites `bin/spud_ledger.d/` at the ledger root could, for that one run, execute old and new parts together. Today the window is one file. An enforcing hook fails closed on any exception (10597-10604), so the realistic worst case is one refused tool call during a merge Spud runs himself. Q7 asks whether that needs a mechanism.

### 4.2 (b) The test suite

`tests/helpers.py` loads the program as module `spud_ledger` with `SourceFileLoader(...).exec_module` (58-63). That is unchanged: the entry is still `bin/spud_ledger.py`. Its module-level `load_parts(HOOK_PARTS)` runs inside `exec_module` without a launcher-supplied `get_code`, so it takes the fallback and writes no bytecode. The helpers already set `dont_write_bytecode` (line 26).

**Every attribute the suite touches.** I found them by walking the syntax tree of every file under `tests/`. The walk took every name bound to `load_spud_module()` (`spud`, `m`, `cls.spud`, `self.spud`, `load_spud_module().X`), collected attribute reads on it, and collected `mock.patch.object` and `setattr` targets. The result: 40 names read, 2 names patched, plus `main` and `__file__`. The walk also flagged `get` and `group` in `tests/probes/headless_projects.py`, but those are dict and regex-match methods on other variables. In one namespace the completeness of this list is not load-bearing: any attribute of `spud_ledger` resolves as it does today, listed or not.

| Attribute | Used in | Part | How it resolves |
| --- | --- | --- | --- |
| `DDL_0001`, `EVENT_KINDS` | test_migrate_projects | state/schema, core/base | hook part: present after load |
| `MARKER`, `SpudError`, `now`, `fm_date`, `fm_minute`, `resolve_home` | test_markdown | core/base, core/home | hook part |
| `emit_frontmatter`, `parse_frontmatter`, `parse_handoff_line`, `parse_log_entries`, `parse_report_entries`, `split_document` | test_markdown | core/markdown | first access runs `__getattr__`, which loads every part |
| `__file__` | test_markdown (220, 228) | the entry | unchanged: `bin/spud_ledger.py`, so `resolve_home`'s git fallback still finds the checkout |
| `GLOB_MATCH_CAP`, `ShellAnalysis` | test_hooks | shell/syntax | hook part |
| `ShellWalk` | test_hooks | shell/walk | hook part |
| `analyse_command` | test_hooks | shell/analyse | hook part |
| `expand_redirect_target`, `numeric_range_regex` | test_hooks | shell/redirect_globs | hook part |
| `git_own_commands` | test_hooks | shell/git_verbs | hook part; its `global _GIT_OWN_COMMANDS` (7261) rebinds the one namespace |
| `path_matches_glob` | test_hooks | hooks/pathrule | hook part |
| `target_has_active_glob` | test_hooks | shell/bash_rule | hook part |
| `transcript_usage` | test_cost, test_hooks | state/usage | hook part |
| `HOOK_CLAIM`, `skill_markdown`, `skill_steps` | test_auto_claim | projects/sessions | hook part |
| `prompt_is_spud_command`, `prompt_ticket_keys` | test_auto_claim | hooks/session | hook part |
| `config_problems` | test_cost | core/home | hook part |
| `cost_number`, `money`, `price_table`, `run_cost` | test_cost | render/cost | hook part |
| `token_counts` | test_cost, test_team_card | render/teamcard | `__getattr__` loads every part |
| `humanize`, `run_cell`, `run_duration` | test_team_card | render/teamcard | `__getattr__` |
| `worked_on` | test_team_card | render/workedon | `__getattr__` |
| `backup_quick_check` | test_backup | state/backup | `__getattr__` |
| `main` | test_backup (231) | the entry | present after load; it calls `load_parts(PARTS)`, which re-executes nothing already loaded |
| **`backup_stamp`** (patched, 228) | test_backup | state/backup | `mock.patch.object` first reads the attribute, which loads every part, then sets it in the one namespace. `daily_backup` and `do_backup` (state/backup) look `backup_stamp` up in that namespace, so the patch lands. `main` then re-executes no part, so the patch survives. |
| **`do_backup`** (patched, 264, 303) | test_backup | state/backup | the same: `daily_backup` (3056-3101) calls `do_backup` through the one namespace |

**Mechanical test edits: the complete list.** None changes what a test asserts about behaviour. Each follows from there being 58 program files instead of two.

| File | Lines | Edit |
| --- | --- | --- |
| `tests/test_launcher.py` | 46-56 | `test_the_programs_bytecode_is_cached_under_the_homes_spud_directory`: `board` loads every part, so the expected cache listing is the entry's `.pyc` plus one per part, each under the mirror of its own source directory. The "read, not rewritten" stamp check applies to every file. |
| `tests/test_launcher.py` | 98-99 | `WithoutSpudHomeTest.setUp`: copy `bin/spud_ledger.d` with `shutil.copytree` beside the two files, or the copy cannot run `--version`. |
| `tests/test_launcher.py` | 112-116 | `test_the_checkouts_spud_directory_holds_the_cache`: the expected names, as in the first row. |
| `tests/probes/headless_projects.py` | 78-79 | copy `bin/spud_ledger.d` into the scratch home too |
| `tests/probes/headless.py` | 233 | copy `bin/spud_ledger.py` and `bin/spud_ledger.d`. Today it copies only `bin/spud`, so the probe cannot have run since SPD-016 (checked: a launcher copied alone fails at load; proposal 76). |

**Not required, recommended.** `tests/test_init.py` 171-183 (`test_no_bytecode_is_written_under_bin_or_tests`) and `helpers._remove_bytecode` (31-33) look only at `bin/__pycache__` and `tests/__pycache__`. Adding `bin/spud_ledger.d/**/__pycache__` keeps the no-bytecode promise as wide as the program.

**Not mechanical, and Eric's call:** the guard test of Q5.

`tests/test_hooks.py` copies the launcher and the program at 1548 and 1577 to test vouching. Those copies are only named on command lines the Bash hook analyses; nothing runs them, so they need no edit.

### 4.3 (c) Hook self-recognition

| Mechanism | Lines | Keys on | With the program as parts |
| --- | --- | --- | --- |
| `spud_launcher`, `any_spud_launcher` | 7672-7686, 8830-8832 | the script word's name, or its real path's name, case-folded, is `spud` | Unchanged: the launcher's name and place do not change, and no command line names a part. |
| `launcher_vouched` | 8893-8908 | the identity of `realpath(<home>/bin/spud)` and of its directory | Unchanged. Its premise (comment 8837-8840) is that the launcher loads the program beside its own real path, so a hard link elsewhere runs another program. The entry now also finds `spud_ledger.d/` beside that same real path (§2.3), so the premise extends to the parts: the vouched root launcher runs the root's parts, and a worktree's launcher, never vouched, runs the worktree's. |
| `interpreter_vouched` | 8867-8890 | `sys.executable` by identity, in the same directory | Unchanged. |
| `spud_home_vouched`, `vouched_spud_call` | 8911-8926 | a `SPUD_HOME` assignment names the root by identity | Unchanged and still needed: the launcher loads unchecked-hash bytecode from `<SPUD_HOME>/.spud/pycache/`, now for up to 58 files instead of one. |
| `DB_PATH_RE`, `DB_REASON` (Bash hook) | 5576-5582 | a command line naming `.spud/` or `ledger.db` | Unchanged: every part's bytecode is under `.spud/`. |
| `in_state_dir`, `state_dir_reason`, `edit_reason` (edit hook) | 6301-6374 | the first component of the repository path, case-folded, is `.spud` | Unchanged. `.spud/pycache/Users/…/bin/spud_ledger.d/shell/walk.cpython-314.pyc` starts with `.spud` whichever part it is. A worktree's parts cache under the home's state directory too, since the hooks run with `SPUD_HOME=<home>`. `state_dir_reason` already names the launcher's cached bytecode. |
| `git_binary_fingerprint`, `git_own_commands` | 7240-7289 | the path and stat of the git binary; `_GIT_OWN_COMMANDS` once per process | Unchanged: nothing about the program's files, and the `global` at 7261 rebinds the one namespace. |
| `worktrees_fingerprint`, `checkout_worktrees` | 5966-6042 | stats of `.git/worktrees` and each `gitdir`; `_WORKTREES` once per process | Unchanged: nothing about the program's files. |
| `file_identity`, `same_entry` | 6071-6091 | `(st_dev, st_ino)` | Unchanged. |
| `resolve_home` from `main` | 155-177, 11023 | the git common directory of `Path(__file__)` | Unchanged: `__file__` is `bin/spud_ledger.py` for every part, because they share the namespace. |
| `settings sync` | `hook_command` 3303-3308, `cli_allow_rules` 3311-3322 | `<home>/bin/spud` | Unchanged: it names only the launcher. |
| `project install`, `project sync` | `install_project` 5074-5125 | hook lines built by `hook_command` | Unchanged: the project gets hook lines naming the home's launcher, and no program files. |
| `schedule install` | `schedule_plist` 3136-3152 | the home's `bin/spud` | Unchanged. |

**In the main checkout and in every worktree** the same holds:

- A checkout's `bin/spud` loads the entry and the parts beside its own real path.
- With `SPUD_HOME` set (every hook line), it caches under the home's state directory, in the mirror of that checkout's path.
- Without `SPUD_HOME`, in a worktree that has no `.spud/`, it writes nothing, as today.

During Phase 2 the engineer edits a worktree's `bin/spud_ledger.d/` while the main checkout's hooks run the main checkout's parts; the two never meet. One Law 5 detail: the engineer's deliverable globs must name `bin/spud_ledger.d/**` beside `bin/spud` and `bin/spud_ledger.py`.

## 5. Hook latency

### 5.1 Today

**SPD-016** (events 789-794) took a hook run from 65 to 22 ms. Caching the bytecode removed a 25 ms compile. Making `argparse`, `subprocess`, `tempfile`, `hashlib` and `fractions` lazy (`LazyModule`, 34-51) removed 11 ms of imports.

**Measured today** at `9e1deda`, with the harness of §5.3: scratch home, three warm-up rounds, then 30 interleaved rounds. The second run was interleaved with the two prototypes.

| Event | Median, run 1 | Median, run 2 |
| --- | --- | --- |
| PreToolUse (Bash `git status`) | 23.9 ms | 22.7 ms |
| PreToolUse (Write) | 24.7 ms | 23.3 ms |
| SessionStart | 24.2 ms | 22.9 ms |
| Stop | 24.1 ms | 22.8 ms |
| `python3.14 -I -S -c pass` | 11.6 ms | 10.8 ms |

**Where the ~12 ms above the bare interpreter goes**, measured in-process over 20 cold processes:

- **`get_code`** (stat, read and unmarshal of the 790 KB `.pyc`): 1.1 ms.
- **The module body:** 8.5 ms.
- **Inside the body:** standard-library imports cost about 3.0 ms (`sqlite3` 1.3, `pathlib` 1.0). The program's own statements cost about 1.9 ms, mostly regex compiles: 0.7 ms in the hook constants, 0.5 ms in render's `MD_*` patterns. That per-statement profile ran in a process that had already imported `ast`, so its import figures are low bounds.
- **The rest** is the handler's own work: the connection, the queries.

### 5.2 After the split

**The import set of `spud hook PreToolUse` is unchanged.**

- **The standard library:** the same 13 imports (now `core/base`, 19-31) and the same five lazy names (47-51). `importlib.machinery` is already imported by the launcher. `test_the_hook_path_imports_nothing_it_does_not_use` (test_launcher 66-76) holds as written.
- **Of the program, a hook run executes 34 parts, 6971 lines.** It skips 23 parts, 4119 lines: `core/markdown`, `state/backup`, `render/teamcard`, `render/workedon`, `render/sections`, `render/notes`, `imports/*`, every `commands/*` part but `views`, `projects/registry`, `projects/install`, `cli/help`, `cli/parser`.

**Four parts are on the hook path that a reader might not expect.** Each is there because the static closure of names reached from `cmd_hook` and `main`'s hook branch reaches it:

- **`commands/views`:** `board_brief_text` feeds SessionStart (10263).
- **`projects/sessions`:** `session_mode`, `record_claim`, `claim_card` and the skill steps feed every handler and UserPromptSubmit.
- **`state/domain`:** `member_status_change` feeds `bind_member`.
- **`render/cost`:** `Ctx.pricing` calls `price_table`. Only command code reads `ctx.pricing` (2947, 2955, 3527, 4443, 4466), so `render/cost` could be skipped by a guard that follows attribute reads. It would save about 0.05 ms, not worth a weaker guard.

**Which commands load what.** Every run that is not exactly `hook <event>` loads all 57 parts before building the parser, because `build_parser` (10688-10969) names every `cmd_*` function in `set_defaults`. Per-command laziness would need a parser that names commands without touching their functions. That is a code change, not a move, so it stays out of this ticket (§7, `cli/parser`).

**Measured with the prototypes**, same harness, all three launchers interleaved:

| Event | Today | All 57 parts (eager) | 34 hook parts (lazy) |
| --- | --- | --- | --- |
| PreToolUse (Bash) | 22.7 ms | 25.1 ms | 23.1 ms |
| PreToolUse (Write) | 23.3 ms | 25.5 ms | 23.4 ms |
| SessionStart | 22.9 ms | 24.9 ms | 23.0 ms |
| Stop | 22.8 ms | 25.0 ms | 22.8 ms |

Each part costs about 0.04 ms (stat, read, unmarshal, execute). Loading every part on every hook costs about 2.3 ms, +10% on every Bash, Edit and Agent call; the lazy split is at parity. A command that is not a hook loads everything: `board` and `events --limit 5` take 35.1 and 35.0 ms today, 37.2 and 37.1 ms with the lazy split, and 37.5 and 37.2 ms eager, over 20 interleaved rounds. That is about +2 ms (+6%) on every command Spud or a spudagent runs; hooks are unaffected.

### 5.3 The timing recipe

Save the script to `/tmp/hooktime.py`. Then run it with main's launcher and the branch's, interleaved:

```bash
python3.14 -I -S /tmp/hooktime.py 30 /Users/ericlugo/Personal/Spud/bin/spud /Users/ericlugo/Personal/Spud/.claude/worktrees/spd-065-split/bin/spud
```

It gives each launcher its own scratch home, so nothing is written into any ledger. **Pass:** each event's median on the branch is within 1 ms of main's in the same run.

```python
"""Hook-run medians for one or more launchers, interleaved: python3.14 -I -S hooktime.py N LAUNCHER [LAUNCHER ...]"""
import json, os, shutil, statistics, subprocess, sys, tempfile, time

N, LAUNCHERS, PY = int(sys.argv[1]), [os.path.abspath(a) for a in sys.argv[2:]], sys.executable
CONFIG = "/Users/ericlugo/Personal/Spud/spud.config.json"


def setup(launcher):
    home = tempfile.mkdtemp(prefix="spud-hooktime-")
    shutil.copy(CONFIG, home)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR")}
    env.update(SPUD_HOME=home, SPUD_USER_CLAUDE_DIR=home + "/.user-claude", SPUD_CONFIG_DIR=home + "/.user-config")
    subprocess.run([PY, "-I", "-S", launcher, "init"], env=env, check=True, capture_output=True)
    base = {"session_id": "0f4b1d2e-3c5a-4e6f-8a9b-0c1d2e3f4a5b", "transcript_path": "/tmp/x.jsonl", "cwd": home, "permission_mode": "default"}
    cases = {
        "PreToolUse(Bash)": ("PreToolUse", dict(base, hook_event_name="PreToolUse", tool_name="Bash", tool_use_id="t1", tool_input={"command": "git status"})),
        "PreToolUse(Write)": ("PreToolUse", dict(base, hook_event_name="PreToolUse", tool_name="Write", tool_use_id="t2", tool_input={"file_path": home + "/n.md", "content": "x"})),
        "SessionStart": ("SessionStart", dict(base, hook_event_name="SessionStart", source="startup")),
        "Stop": ("Stop", dict(base, hook_event_name="Stop", stop_hook_active=False)),
    }
    return launcher, home, env, cases


def once(argv, stdin, env):
    t = time.perf_counter()
    p = subprocess.run(argv, input=stdin, capture_output=True, text=True, env=env)
    if p.returncode:
        sys.exit("%s: exit %d %s" % (argv, p.returncode, p.stderr))
    return (time.perf_counter() - t) * 1000


runs, times = [setup(l) for l in LAUNCHERS], {}
for rnd in range(3 + N):  # three warm-up rounds fill the bytecode and git-command caches
    for launcher, home, env, cases in runs:
        for name, (event, payload) in cases.items():
            ms = once([PY, "-I", "-S", launcher, "hook", event], json.dumps(payload), env)
            if rnd >= 3:
                times.setdefault((launcher, name), []).append(ms)
for (launcher, name), v in times.items():
    print("%-70s %-18s median %5.1f ms  min %5.1f  max %5.1f" % (launcher[-70:], name, statistics.median(v), min(v), max(v)))
for _, home, _, _ in runs:
    shutil.rmtree(home, ignore_errors=True)
```

## 6. Phase 2: sequence and verification

### 6.1 Commits

One worktree, `spd-065-split`, and one branch. Every commit leaves the suite green. During commits 1-7 the entry loads its parts eagerly at the top, before the statements not yet moved. Parts move in `PARTS` order, so the moved set is always a prefix of the load order, and no moved statement ever needs a name that has not moved at definition time. The not-yet-moved statements keep their original order after the parts.

| # | Commit | Eric's phase |
| --- | --- | --- |
| 1 | The loader with `PARTS = ()`: `bin/spud` hands `_launcher_get_code`; `bin/spud_ledger.py` gains the loader under its docstring; the `tests/test_launcher.py` edits and both probe copies of §4.2. Nothing has moved, so behaviour is trivially the same. | (scaffolding) |
| 2 | `core/`: base, markdown, home (597 lines) | shared types and pure utilities |
| 3 | `state/`: schema, db, rows, actors, domain, usage, backup (1219) | state and services |
| 4 | `render/` and `imports/` (1496) | sub-components |
| 5 | `commands/` and `projects/` (2451) | sub-components |
| 6 | `hooks/base`, `hooks/checkouts`, `hooks/pathrule` and `shell/` (3982) | sub-components |
| 7 | the hook handlers (`hooks/pre` … `hooks/dispatch`) and `cli/` (1345) | sub-components |
| 8 | The entry as orchestrator: only its docstring, the loader, `HookCall`, `main` and the refusal remain. `HOOK_PARTS` is filled; `load_parts(HOOK_PARTS)` and `__getattr__` replace the eager load; `main` loads `PARTS` before the parser. The guard test, if Q5 is yes. | the entry reduced to an orchestrator |

**How the moves are made.** A script reads this spike's ranges and cuts each statement verbatim, with the comments above it, into its part. Nothing is retyped. The ranges live in §3 and are machine-checkable: every one of the 727 statements falls in exactly one. The reviewer re-runs the reassembly check below on their own copy.

### 6.2 Verification recipe

At every commit, run the suite. Before the merge, run all six:

1. **The suite.** `python3.14 -I -S -m unittest discover -s tests -t tests`: 702 tests at `9e1deda` (a scratch copy printed `Ran 702` in 504 s), about eight and a half minutes. CLAUDE.md line 170 says 732; §8 corrects it.
2. **Reassembly.** Take every part's statements and the entry's remaining ones, and put each back at its first line on `main`. The result must equal `git show main:bin/spud_ledger.py` byte for byte, apart from the part headers and the loader. It is a scratch script of about 30 lines; Q6 asks whether to commit it beside the timing harness.
3. **No bytecode in the repository.** After the suite, `find bin tests -name '*.pyc'` prints nothing.
4. **Hook timing.** §5.3, main against the branch: each median within 1 ms.
5. **The probes, once on the branch, not per commit.** `python3.14 -I -S tests/probes/headless_projects.py project-spud` runs a real `claude -p` session through the installed hooks in a scratch home. `python3.14 tests/probes/headless.py background` does the same for the spawn and binding hooks, once its copy is fixed. Each costs real API usage (their docstrings say $0.10 to $0.50 on haiku).
6. **After the merge, at the ledger root.** Run `spud doctor` and one hook-bearing session. The first hook run caches the entry and 34 parts under `.spud/pycache/`. Only the CLI and the suite can see that directory, so `test_launcher` is the check.

### 6.3 Evidence from Phase 1's prototype

I ran the suite three times. Each run used a scratch copy of `tests/`, `spud.config.json`, `CLAUDE.md` and `.gitignore`, with a different `bin/`: today's program (control), the eager prototype, and the lazy prototype.

The copies are not git repositories, so four tests that read git history or the checkout's `.git` fail in all three runs:

- `test_acceptance.CutoverReadinessTest` and `test_acceptance.PinnedLedgerTest`, in `setUpClass` (`git archive`, test_acceptance line 74);
- `test_markdown.HomeResolutionTest.test_git_common_dir_of_the_script_is_the_fallback`;
- `test_report_entries.RenderedDaysPinTest.test_the_days_rendered_before_render_byte_for_byte` (`git archive`, line 416).

| Run | Ran | Failing | Wall time |
| --- | --- | --- | --- |
| control: today's program | 702 | 4, the git four | 504 s |
| eager prototype: all 57 parts | 702 | 7 | 527 s |
| lazy prototype: 34 hook parts | 702 | 7 | 511 s |

In both prototypes the three extra failures are exactly the three `tests/test_launcher.py` tests on §4.2's list of mechanical edits, and nothing else:

- `LauncherTest.test_the_programs_bytecode_is_cached_under_the_homes_spud_directory` expects a single `.pyc`;
- `WithoutSpudHomeTest.test_no_spud_directory_means_no_bytecode_anywhere` and `WithoutSpudHomeTest.test_the_checkouts_spud_directory_holds_the_cache` copy a `bin/` that has no `spud_ledger.d/`.

The other 695 tests pass against both prototypes, as they do against today's program. They include every hook test and the backup tests that monkeypatch `do_backup` and `backup_stamp`.

That is the zero-behaviour-change claim, tested before any code is written. The Phase 2 branch should reproduce it with the three edits made, in a real checkout where the git four pass too.

## 7. Parts over ~250 lines

Nine parts are over. None was cut to fit a number; each holds either one function or class too large for a move to divide, or one cohesive responsibility. The six functions the brief named as candidates are placed in the last table.

| Part | Lines | What makes it large | Recommendation |
| --- | --- | --- | --- |
| `projects/install` | 336 | `install_project` 52 (5074-5125), `strip_ledger_settings` 38, `uninstall_project` 46 (5168-5213), four commands 121 (5216-5336) | **Keep whole.** Uninstall undoes install record by record, and `project sync` re-runs install; read apart, they drift. If Eric wants the seam: mechanics 5007-5213 (~210) and commands 5216-5336 (~125). |
| `shell/walk` | 327 | `ShellWalk`, 304 lines (7822-8125): one class whose methods share the walk's frames, directories and redirects; its own sub-banners (7847, 7865, 7909, 7936) group methods of one object | **Keep whole.** Dividing a class across files takes mixins, which is a code change. |
| `cli/parser` | 322 | `build_parser`, 282 lines (10688-10969): one function registering every command | **Keep whole.** A move cannot cut a function. The seam is one `add_<family>_parser(sub)` per command family, living in that family's part. It is a code change, and the first step to per-command lazy loading (§5.2): a later ticket if the CLI's +2 ms ever matters. The help texts are already split off into `cli/help` (81). |
| `shell/zsh` | 291 | `mark_zsh_patterns` 185 (6717-6901), `_zsh_group` 53, `_scan_pairs` 38 | **Keep whole.** Three functions of one scanner. |
| `shell/redirect_globs` | 275 | `bounded_glob` 71, `_segment_regex` 51, brace expansion 9222-9290 | **Keep whole, I lean.** The thin seam is `shell/braces` (`_split_brace`, `_expand_one_brace`, `brace_expand`, ~75 lines), whose one user is `expand_redirect_target`. Eric decides. |
| `shell/analyse` | 271 | `analyse_words` 198 (8630-8827), `analyse_command`, `isolated`, `analyse_isolated` 60, `analyse_segment` | **Keep whole.** `analyse_words` is the dispatch every other shell part serves, and `analyse_command` is its way in. |
| `state/schema` | 270 | `DDL_0001` 170 lines of SQL (269-438), `DDL_0002` 44 | **Keep whole.** The schema is data read as one. A file per migration is a design for the next migration, not a move. |
| `core/markdown` | 263 | the YAML subset, the document splitter, the entry parsers | **Keep whole.** 13 over; parsing and emitting frontmatter belong together. |
| `imports/accept` | 256 | `accept_ticket_edit` 64, `accept_member_edit` 51, `accept_file` 44 | **Keep whole.** 6 over. |

Just under the look-again point: `hooks/checkouts` 249, `hooks/pathrule` 249, `shell/git_config` 244, `projects/sessions` 243, `shell/expansions` 240.

The candidates named in the brief, where they land:

| Candidate | Size | Part |
| --- | --- | --- |
| `cmd_ledger_commit` | 70 lines (5470-5539) | `commands/publish`, 187 |
| `build_parser` | 282 | `cli/parser`, 322 |
| `ShellWalk` | 304 | `shell/walk`, 327 |
| `analyse_words` | 198 | `shell/analyse`, 271 |
| `bash_reason` | 169 (9028-9196) | `shell/bash_rule`, 226 |
| `mark_zsh_patterns` | 185 | `shell/zsh`, 291 |

## 8. CLAUDE.md lines that change

Spud edits these; the spike only lists them.

| Line | Section | Today | After |
| --- | --- | --- | --- |
| 19 | The ledger CLI | "(since SPD-016 a launcher for `bin/spud_ledger.py`, whose bytecode it caches under `.spud/pycache/`; the module run directly refuses)" | "(since SPD-016 a launcher for `bin/spud_ledger.py`, which since SPD-065 executes its parts from `bin/spud_ledger.d/` in its own namespace; the launcher caches the bytecode of both under `.spud/pycache/`, and the module run directly refuses)" |
| 170 | Commands | "`# 732 tests, about eight minutes, leaves no bytecode`" | the count the branch's final run prints: `Ran 702` at `9e1deda`, 703 with the guard test of Q5 |
| after 170 | Commands | none | if Q6 commits the harness: a block `python3.14 -I -S tests/probes/hook_timing.py 30 bin/spud <other checkout>/bin/spud   # hook-run medians, interleaved` |

Nothing else changes. Line 168 ("the test suite is `bin/spud`'s") stays true. "Main and worktrees" puts `bin/` on the branch, which covers `bin/spud_ledger.d/`. `.claude/agents/spudagent.md` and `ledger/Home.md` do not name the file. `docs/design/2026-09-14-cross-repository-projects.md` names it at lines 17, 48 and 412 as a record of that design, and stays as written.

## 9. Open questions for Eric

| # | Question | Options | Recommendation |
| --- | --- | --- | --- |
| Q1 | How are the parts joined? | (a) one namespace: the entry executes part files into itself; (b) real modules that import each other | **(a).** Zero behaviour change by construction: every name, patch and `global` works as today. (b) meets a 10-part cycle, 27 back-edges, median 16 imports per module, and moved patch targets (§2.4). |
| Q2 | Does a hook run load only its parts? | (a) lazy: 34 parts, plus `__getattr__` for the suite; (b) eager: all 57 | **(a).** Measured parity against +2.3 ms (+10%) on every tool call. It costs a 34-name list, `__getattr__` and the guard test. |
| Q3 | Who owns the bytecode rule? | (a) the launcher hands the entry its `get_code` (46 to ~60 lines); (b) the entry recomputes the home rule (launcher byte-identical, rule in two files) | **(a).** One owner for where bytecode goes, as SPD-016 set it. |
| Q4 | The directory's name | `bin/spud_ledger.d/`, `bin/spud_ledger/`, `bin/ledger/`, `bin/spudlib/` | **`bin/spud_ledger.d/`** (§2.2). |
| Q5 | A guard test: the one test that is not a mechanical edit | (a) add one test to `tests/test_launcher.py` that parses every part and asserts four things: no statement needs a later part's name at definition time; the static closure from `cmd_hook` and `main`'s hook branch stays inside `HOOK_PARTS`; `HOOK_PARTS` is a subsequence of `PARTS`; every `.py` under `bin/spud_ledger.d/` is in `PARTS`. (b) no test | **(a)**, and required if Q2 is (a). Without it, a later edit that makes a hook call a command-only function fails closed at run time (exit 2 on PreToolUse) instead of in the suite. |
| Q6 | Commit the timing harness (§5.3) and the reassembly check (§6.2) as probes? | `tests/probes/hook_timing.py`, `tests/probes/split_check.py`, not collected by unittest; or keep them as scratch scripts | **Commit the timing harness**, since every later hook change wants it. **Leave the reassembly check as a scratch script**: it matters once, in this branch. |
| Q7 | A merge rewriting 58 files while a hook runs (§4.1, item 5) | (a) accept: one refused tool call at worst, during a merge Spud runs; (b) the entry and `core/base` carry a stamp, and a mismatch raises a clean `HookError` | **(a).** (b) adds a moving part to guard a millisecond window with a fail-closed outcome already. |
| Q8 | The nine parts over ~250 (§7) | keep all whole; or take the two thin seams (`projects/install` commands, `shell/braces`) | **Keep all whole.** |
| Q9 | `tests/probes/headless.py` copies only `bin/spud` (line 233) | fix it inside SPD-065, where the same copy line gains `bin/spud_ledger.d` anyway; or leave it to the proposal filed from this spike | **Fix it inside SPD-065.** It is one line, next to an edit the split needs. |
