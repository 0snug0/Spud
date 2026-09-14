# Cross-repository projects

Ticket [[SPD-014]]. Written by Anya (01, architect, opus) on 2026-09-14 for Spud and for the engineer who builds it. It settles how Spud works in a repository other than his home. A ticket for BadTakes runs from a Claude Code session started in `/Users/ericlugo/Personal/BadTakes`, and it lands in the one ledger at `/Users/ericlugo/Personal/Spud`.

Settled before this document, and not reopened here:

- One database, at `<home>/.spud/ledger.db`.
- Per-project ticket and team prefixes (Eric, 2026-09-12; `projects.ticket_prefix`, `team_prefix` and the per-project `number` counter already exist in schema v1).
- Home resolution: `SPUD_HOME`, then the git common dir of the CLI's own path, then `~/.config/spud/home`.

The brief's eight questions map to sections: 1 → §1, 2 → §2, 3 → §3, 4 → §4, 5 → §5, 6 → §6, 7 → §7, 8 → §8. Spud's edits to his own files at merge are §9. What only Eric can decide is under Open questions for Eric.

Every claim about the harness carries one of these marks:

- **[doc]** is verified against the official documentation, fetched 2026-09-14: `code.claude.com/docs/en/` `hooks`, `settings`, `permissions`, `worktrees`, `sub-agents` and `memory`.
- **[probe]** is verified by the headless probe described below.
- **[code]** is verified by reading `bin/spud_ledger.py` at `3b79b0d`, or the named file.
- **[assumed]** is not verified. The engineer verifies each one, and §7.4 names the probe that settles it.

**The probe.** It ran once on 2026-09-14, on Claude Code 2.1.269, as `claude -p --model haiku --setting-sources project,local`, and cost $0.10. The launch directory was a scratch directory outside any git repository, holding an untracked `.claude/settings.local.json`. Its hooks logged every payload for SessionStart, PreToolUse, PostToolUse, SubagentStart, SubagentStop, Stop and WorktreeCreate, and the WorktreeCreate hook made a directory beside the launch directory. The directory also held a `.claude/agents/probeagent.md`. The run made no git writes, and nothing in either repository was written. Its findings:

- **P1.** Every hook in the untracked local settings file fired, SessionStart through WorktreeCreate.
- **P2.** A SessionStart `additionalContext` of 39 KB did not reach the model whole. The model saw a persisted-output preview: the start marker and filler up to line 26, about 2 KB, and no end marker.
- **P3.** A SubagentStart `additionalContext` reached the child.
- **P4.** The project agent from the launch directory stayed listed and spawnable after `EnterWorktree`, and that child's `pwd` was the worktree.
- **P5.** After `EnterWorktree`, the hook process's cwd and the payload's `cwd` were the worktree. `CLAUDE_PROJECT_DIR` stayed the launch directory. `CLAUDE_CODE_SESSION_ID` was set in the hook environment and equal to the payload's `session_id`.
- **P6.** `git -C /Users/ericlugo/Personal/Spud log --oneline -1` ran unrefused from the plain session. It also ran unrefused after `EnterWorktree` into the hook-made directory. That second result says nothing about a worktree of a git launch repository (§5.1, G2).
- **P7.** The WorktreeCreate payload carries `cwd`, `hook_event_name`, `name`, `prompt_id`, `session_id` and `transcript_path`.

## Decisions

1. **Registry.** `spud project add|list|show|edit|install|uninstall|sync|remove`, all but the reads Spud's. A path belongs to the project whose main checkout, or a worktree git lists for it, is the nearest root by file identity. A session's project is the project of `CLAUDE_PROJECT_DIR`, and a ticket's is `tickets.project_id`. `ticket new` defaults `--project` to the project of the working directory (§1).
2. **Installation.** `spud project install` writes nothing in the other repository's tracked tree:
   - the ledger hooks, the CLI allow rules and `additionalDirectories: [<home>]` go into the project's untracked `.claude/settings.local.json`;
   - `spudagent` and a `/spud` skill go to user scope (`~/.claude/agents/`, `~/.claude/skills/`);
   - the native Law 3 deny rules are **not** written, because BadTakes' own `parallel-worktrees` skill spawns `Agent(isolation: "worktree")`.

   Spud's identity reaches a BadTakes session when Eric types `/spud`. That runs `spud --as spud session claim`. Sessions in a project are opt-in by claim (a per-project setting, `sessions: claim|always`), so Eric's ordinary BadTakes sessions stay his (§2).
3. **Path rule.** `map_into_repository` generalizes to every project root and its worktrees. Deliverable globs are relative to the ticket's project checkout, and `<key>:<glob>` names another project's. In another project Spud has no own files: every path there is a deliverable. `ledger/**` and `reports/**` are generated only in the home. A session that is not Spud is not checked in its own project, and is refused in Spud's home (§3).
4. **Rendered ledger.** Every note still renders flat at the home: `ledger/tickets/TAKE-001.md`, `ledger/teams/TAKES-001/<Name>.md`. Ticket and member notes gain a `project` property. A new generated `ledger/Projects.md` lists the projects and is the disaster-recovery import source for the `projects` table. `Board.base`, `Fleet.base` and `Home.md` gain a project column or link, and those edits are Spud's (§4).
5. **Git and worktrees.** Code is built in a worktree of the other repository and lands by that project's policy: `merge` at home, `pr` for BadTakes, whose `main` is protected. The ledger is committed at the home on `main` by a new `spud --as spud ledger commit`. It renders, stages only `ledger/` and `reports/`, commits, pushes, and refuses to run from any linked worktree. So the recipe stays today's: commit the code in the worktree, `ExitWorktree` (keep), commit the ledger from the main checkout (§5).
6. **Hooks from another cwd.**
   - Every hook resolves the session's mode, `spud`, `plain` or `outside`, before it enforces or records.
   - A plain session's SessionStart gets a one-line notice.
   - Stop, SubagentStart, SubagentStop and PostToolUse stay silent for plain sessions.
   - Injected context stays under 2 KB (P2).
   - Ritual step 1 becomes `spud session show`, which names the home, the project, the checkout and the mode (§6).
7. **Split and tests.** All code is in `bin/spud_ledger.py`, one file, so one engineer builds it in three phases, each ending green. At most one child writes `tests/test_hooks_projects.py` in parallel. Unit tests build temporary git repositories the way `tests/test_hooks.py` already does. Three headless scenarios run on scratch repositories. Spud runs the real acceptance from BadTakes after merge (§7).
8. **Migration.** Migration `0002_projects` adds five columns to `projects`, a `sessions` table and seven event kinds, and updates two views. The 47 existing tickets are already `project_id = 1`. After merge, one render adds the `project` property to every note (§8).

## 1. Registry

### 1.1 Terms

- **Home**: Spud's home, project 1, key `spud`. It holds the database, the CLI, the hooks' target, and the rendered ledger.
- **Project**: a registered repository, one `projects` row. Its **root** is `projects.root_path`, the absolute path of the main checkout.
- **Checkout**: the root, or any linked worktree `git worktree list` names for that root, wherever it is (SPD-016's rule, per project).
- **Spud session**: a Claude Code session in which Spud is acting. Every session launched in the home is one. A session launched in another project is one after it claims (§2.5).
- **Plain session**: a session launched in a project with `sessions = claim` that has not claimed. It is Eric's own session, and the ledger stays out of its way (§6.1).

### 1.2 Commands

| Command | Actor | What it does |
|---|---|---|
| `spud --as spud project add <path> --key <key> --ticket-prefix <P> --team-prefix <T> --landing merge\|pr [--name <text>] [--sessions claim\|always] [--default-branch <b>] [--next <text>]` | Spud | Validates (below), inserts the row, writes a `project.added` event and a report entry. Installs nothing. |
| `spud project list` / `spud project show <key>` | anyone | Key, name, prefixes, root, default branch, landing, sessions, remote, ticket count, and the install state computed now: whether the root's `.claude/settings.local.json` holds this home's ledger hooks. |
| `spud --as spud project edit <key> [--name] [--landing] [--sessions] [--default-branch] [--root <path>]` | Spud | `--root` is validated like `add` (the repository moved on disk). Prefixes are immutable once the project has a ticket, because they are in rendered file names and wikilinks. |
| `spud --as spud project install <key>` / `uninstall <key>` / `sync [<key>\|--all]` | Spud | §2.2 to §2.7. |
| `spud --as spud project remove <key>` | Spud | Uninstalls. It deletes the row when the project has no ticket; otherwise it sets `archived_at` and keeps the row, since tickets reference it. Refused while a ticket of the project is `queued` or `active`, and always refused for project 1. |
| `spud --as spud session claim [--project <key>]` / `session release` / `spud session show` | Spud; `show` anyone | §2.5 and §6.3. |
| `spud --as spud ledger commit --message <text\|@file\|@-> [--no-push]` | Spud | §5.3. |

Validation in `project add`. Each failure exits 1, or 2 for a usage error, with the reason:

1. `<path>` resolves (`Path.resolve()`) to an existing directory.
2. Run `git -C <path> rev-parse --path-format=absolute --show-toplevel --git-common-dir`, with `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR` and `GIT_INDEX_FILE` stripped (`GIT_REDIRECTS`). The toplevel must be the path, by file identity (`file_identity`, SPD-029). The common dir must be `<toplevel>/.git`. A linked worktree is refused with the main checkout's path in the message ("register the main checkout, `<common dir's parent>`").
3. The root is not the home, not inside any active project's root, and does not contain one, all by file identity. Nested roots would make "nearest root" ambiguous. They would also make the home's `.claude/worktrees/` a project inside the home.
4. `--key` matches `[a-z][a-z0-9-]{0,31}`, is unique, and is not `spud`.
5. Both prefixes match `[A-Z][A-Z0-9]*` (the rule `config_problems` already applies to the home's prefixes) and differ from each other. Neither may equal either prefix of any other project, archived projects included. So no ticket key can ever equal a team key: `DESCRIPTION` and `get_member` look teams up by `team_key`, and tickets are looked up by `key` [code].
6. `--landing` is required. Nothing guesses whether a default branch is protected.
7. `remote` is `git remote get-url origin` when there is one, else NULL. `default_branch` is `--default-branch`, else the tail of `git rev-parse --abbrev-ref origin/HEAD`, else `main`.

`project`, `session` and `ledger` join `SPUD_COMMANDS`. `("project", "add")`, `("project", "edit")`, `("project", "install")`, `("project", "uninstall")`, `("project", "sync")`, `("project", "remove")`, `("session", "claim")`, `("session", "release")` and `("ledger", "commit")` join `SPUD_ONLY_SUBCOMMANDS`, so the Bash hook refuses them to every subagent under Law 6 [code: `bash_reason`]. `parse_spud_call` learns the three new subcommand groups.

### 1.3 Which project a path is in

`project_checkout(ctx, con, path)` returns `(project row, checkout root, repository-relative path)` or None, and replaces `repository_paths` and `map_into_repository` (§3.1).

1. Every active project contributes its root and its worktrees. A project's worktrees come from `checkout_worktrees(ctx, project)`, the generalization of `home_worktrees`. It runs `git -C <root> worktree list --porcelain -z`, cached in `<home>/.spud/worktrees/<key>.json` under the fingerprint of `<root>/.git/worktrees`, as SPD-016 does for the home.
2. The nearest existing ancestor of the path whose file identity is a root's wins. The `.claude/worktrees/<name>/` spelling rule applies per root.
3. The projects are read in the hook's transaction. The stat-only fingerprint means a hook runs git only after a project's worktrees change.

### 1.4 A session's project, a command's project, a ticket's project

- **Hooks: the session's launch project.** It is the project of `CLAUDE_PROJECT_DIR` from the hook's environment. That variable stays at the launch directory after `EnterWorktree` [doc, probe P5], and for a session the desktop app starts inside a worktree it is that worktree, which still maps to its project. When the variable is absent, fall back to the payload's `cwd`.
- **Hooks: where a call acts.** It is the project of the payload's `cwd` (which follows `EnterWorktree` and `cd` [doc, probe P5]) for relative paths, and of each absolute target path for the path rule.
- **CLI commands.** The project of `os.getcwd()`, since the Bash tool runs in the session's current directory. The session id comes from `CLAUDE_CODE_SESSION_ID` (`planning_session`, SPD-018 [code]).
- **A ticket.** `tickets.project_id`, as in v1. `ticket new --project <key>` keeps its meaning. Without the option, the default becomes the project of the working directory instead of `spud`, and the output names the project (`TAKE-001 (TAKES-001, badtakes) created: …`). A ticket created from a proposal keeps its origin ticket's project (`create_ticket_for_proposal` [code]).

## 2. Installation in another repository

### 2.1 What is written where

For project `badtakes` at root `R = /Users/ericlugo/Personal/BadTakes`:

| Path | Written by | In git? | Content |
|---|---|---|---|
| `R/.claude/settings.local.json` | `project install`, `project sync` (merge, §2.2) | untracked; install makes sure it is ignored (§2.3) | the eight ledger hooks; the two CLI allow rules; `permissions.additionalDirectories` gains the home |
| `<R's git common dir>/info/exclude` | `project install`, only when `git -C R check-ignore -q .claude/settings.local.json` fails | not part of the tree | `.claude/settings.local.json` under a `# spud project badtakes` comment line |
| `~/.claude/agents/spudagent.md` | `project install` and `project sync` | outside every repository | a byte copy of `<home>/.claude/agents/spudagent.md` |
| `~/.claude/skills/spud/SKILL.md` | `project install` and `project sync` | outside every repository | the `/spud` skill (§2.5), with the home path filled in |
| `~/.config/spud/home` | `project install`, when the file is absent | outside every repository | the home path, one line |

Nothing is written to `R/CLAUDE.md`, `R/.claude/settings.json`, `R/.claude/agents/`, `R/.claude/skills/` or anything else in R's tracked tree. `~/.claude` is the default. The environment variable `SPUD_USER_CLAUDE_DIR` overrides it, for tests only (the precedent is `SPUD_LAUNCH_AGENTS_DIR`).

Why each location:

- **The local settings file, not `R/.claude/settings.json`.**
  - BadTakes commits `settings.json` (its `.gitignore` un-ignores it [code: BadTakes `.gitignore` lines 76 and 77]), and the hook lines hold Eric's absolute paths.
  - An untracked `settings.local.json` is "normally your own file, so Claude Code applies its allow rules and additional directories without the trust step" [doc: permissions].
  - Hooks in settings files run under `claude -p` in a folder never trusted [doc: permissions; probe P1].
  - In a worktree, Claude Code "uses the file at the main checkout's root" [doc: settings], so every BadTakes worktree session gets the hooks without a copy.
- **Not user settings.** A hook in `~/.claude/settings.json` fires in every repository on the machine. An enforcing hook fails closed, so a broken ledger would stall every session Eric has. It would also fire twice in the home, beside the home's committed hooks.
- **User-scope agent and skill, not project scope.**
  - Project subagents are found "by walking up from the current working directory" to the repository root [doc: sub-agents]. For a worktree the repository root is the worktree itself, so `R/.claude/agents/` may not be scanned from `R/.claude/worktrees/<name>/` [assumed]. A session the desktop app starts inside a worktree would then have no `spudagent`.
  - User scope (`~/.claude/agents/`, priority 4) is found everywhere [doc: sub-agents]. The home's committed project copy (priority 3) still wins inside the home.
  - A project skill would be `R/.claude/skills/spud/`. BadTakes un-ignores `.claude/skills/` [code: BadTakes `.gitignore` line 78], and a `.gitignore` negation outranks `info/exclude` [doc: gitignore(5), precedence order], so it would show as untracked in `git status` and could be committed by accident.
  - The cost of user scope is two short descriptions in every session's agent and skill lists. Both descriptions tell the model not to use them unasked.

### 2.2 The settings file

Install reads the file if it exists, keeps every key it does not own, and writes it whole (`write_whole`). For BadTakes today [code: BadTakes `.claude/settings.local.json`] the result is:

```json
{
  "permissions": {
    "allow": [
      "Bash(node -e ' *)",
      "Bash(python3.14 -I -S /Users/ericlugo/Personal/Spud/bin/spud *)",
      "Bash(/opt/homebrew/bin/python3.14 -I -S /Users/ericlugo/Personal/Spud/bin/spud *)"
    ],
    "additionalDirectories": ["/Users/ericlugo/Personal/Spud"]
  },
  "outputStyle": "Concise",
  "disabledMcpjsonServers": ["Blender", "openscad"],
  "hooks": {
    "PreToolUse": [
      {"matcher": "Agent", "hooks": [{"type": "command", "timeout": 30,
        "command": "SPUD_HOME=/Users/ericlugo/Personal/Spud /opt/homebrew/bin/python3.14 -I -S /Users/ericlugo/Personal/Spud/bin/spud hook PreToolUse --project badtakes"}]}
    ]
  }
}
```

The `hooks` object carries all eight rows of `HOOK_TABLE`; only the first is shown. The rules for the merge:

- **Hooks.** `merge_hooks` and `merge_allow_rules` already keep everything that is not the ledger's and replace the ledger's own entries, found by `HOOK_MARK` and `ALLOW_RULE_MARK` [code]. Install reuses them through one refactored `merge_settings(ctx, settings, *, env, deny, additional_dirs, project_key)`.
  - `settings sync` for the home calls it with `env=True, deny=True`.
  - `project install` calls it with `env=False, deny=False`, `additional_dirs=[home]` and `project_key=<key>`. The key is appended to each hook command as `--project <key>`, used only for the failure policy of §6.4.
- **No `env` block.** `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` and `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` would cap Eric's plain BadTakes sessions too. For spudagents the limits are already enforced by `member new` and `PreToolUse(Agent)`.
- **No `deny` rules.** `Agent(isolation:*)` would refuse the `Agent(isolation: "worktree")` spawns BadTakes' `parallel-worktrees` skill prescribes [code: that skill], in every BadTakes session. Deny rules cannot be scoped to one session, and they "apply right away" [doc: settings]. In a project, Law 3 therefore rests on the `PreToolUse(Agent)` hook alone. The hook refuses a spudagent-shaped spawn with `isolation`, a missing model, `inherit`, or a fork, as it does in the home [code: `hook_agent_spawn`]. SPD-016's "Law 3 holds without the hook" is a home guarantee.
- **`additionalDirectories` gains the home.** Files in additional directories "become readable without prompts, and file editing permissions follow the current permission mode" [doc: permissions]. That is how a BadTakes session reads `<home>/CLAUDE.md` and `spud.config.json` at claim. The edit hook still guards every home path (§3.2).
- **Idempotent.** A second install writes nothing when the rendered JSON is byte-identical, which is `cmd_settings_sync`'s rule [code].
- **What install recorded.** `projects.installed` gets a JSON note: path, whether install created the file, whether it added the home to `additionalDirectories`, whether it added the exclude line, and when. Uninstall undoes only what install did.

### 2.3 Keeping it out of git

Claude Code adds `**/.claude/settings.local.json` to the global git excludes only "the first time Claude Code writes the file" [doc: settings]. Install writes it by hand, so install checks `git -C R check-ignore -q .claude/settings.local.json` itself. When the check fails, it appends the path to `<common dir>/info/exclude` under its comment line; one exclude file serves every worktree. BadTakes already ignores the file through `.claude/*` [code: BadTakes `.gitignore` line 76], so there install adds nothing. `spud doctor` repeats the check.

### 2.4 The `spudagent` definition

`project install` and `project sync` copy `<home>/.claude/agents/spudagent.md` to `~/.claude/agents/spudagent.md`, and doctor compares the two by SHA-256. The home's copy stays the source, since it is one of Spud's files and committed. Agent files are watched and re-read without a restart, except when a scope's first agent file is created [doc: sub-agents]. The first install therefore says: "restart open sessions in badtakes to see spudagent."

### 2.5 How Spud's identity reaches a session there, and opt-in

**Mechanism: a `/spud` skill that claims the session.** The skill file, templated with the home path:

```markdown
---
name: spud
description: Make this session Spud, Eric's second brain, in a repository registered as a Spud project. Only when Eric types /spud or asks for Spud in this session.
disable-model-invocation: true
---
You are becoming Spud in this session.

1. Read /Users/ericlugo/Personal/Spud/CLAUDE.md in full, then /Users/ericlugo/Personal/Spud/spud.config.json. They bind you from now on, with the rule in step 3.
2. Run `python3.14 -I -S /Users/ericlugo/Personal/Spud/bin/spud --as spud session claim`. If it refuses, quote the refusal, say this session is not Spud, and stop following these steps.
3. The claim names the project. This repository's own CLAUDE.md and .claude/skills govern how deliverables are built, verified, committed and landed. Spud's laws govern delegation, the ledger, and who writes what. In a conflict about the first, the project wins; about the second, Spud's laws win.
4. Run the session ritual of CLAUDE.md from step 2.
```

`disable-model-invocation` keeps the model from invoking the skill on its own [assumed: the frontmatter key; §7.4 checks it].

`spud --as spud session claim` does the following:

- It requires `CLAUDE_CODE_SESSION_ID`. With the variable absent, it exits 1: "outside a Claude Code session there is nothing to claim".
- It resolves the project from `--project`, else from the working directory, else it refuses: "not a registered project".
- It upserts `sessions(session_id, project_id, claimed_at, released_at = NULL, cwd)` and writes a `session.claimed` event.
- It prints a card of at most 1.5 KB: the home, the project's key, name, root, prefixes, default branch, landing and sessions mode, the precedence line of step 3, the `ledger commit` command, and `board --brief` lines for that project's open tickets.
- Claiming the home project prints "every session in the home is Spud" and writes nothing.

`session release` sets `released_at`.

**Why not the alternatives.**

- **SessionStart injection of CLAUDE.md.** The home's CLAUDE.md is 30.9 KB [code: `wc -c`]. A 39 KB `additionalContext` reached the model as a 2 KB preview (probe P2). Injection would also make every session in the project Spud.
- **A `CLAUDE.local.md` in BadTakes with `@/Users/ericlugo/Personal/Spud/CLAUDE.md`.**
  - It loads in every session there, so it cannot be opt-in.
  - An import outside the working directory needs a one-time approval dialog [doc: memory].
  - A gitignored `CLAUDE.local.md` "only exists in the worktree where you created it" [doc: memory].
- **An import in `~/.claude/CLAUDE.md`.** It would make every session in every repository read Spud's laws.

**Opt-in per session, by project setting.**

- `projects.sessions = 'claim'` (the default for `project add`): a session is Spud only after it claims. Eric's BadTakes sessions today build things themselves, and Law 1 would refuse every edit they make if each one were Spud. This is question 1 for Eric.
- `projects.sessions = 'always'`: every session launched in the project is Spud, as in the home, and the home is `always`. For an `always` project, SessionStart tells the session to run `/spud` now (§6.2).

A claim survives resume and compaction, because it is keyed by `session_id`. That `--resume` keeps the id and `--fork-session` and `/clear` do not is [assumed]. A session that loses its claim is plain again and says so at its next SessionStart.

### 2.6 Cost to a plain session

Every Bash, Edit, Write and Agent call in BadTakes runs one ledger hook: a Python start with cached bytecode and one short read transaction. SPD-016 measured the hook run at 65 ms before the bytecode cache took out 25 ms of it [code: module docstring]. SessionStart adds one line of context. Everything else is silent (§6.2).

### 2.7 Uninstall

`spud --as spud project uninstall <key>`:

1. Removes the ledger hook entries, the ledger allow rules and, when install added it, the home from `additionalDirectories`.
2. Deletes the file when install created it and nothing else is left in it (`{}`). Otherwise it writes the rest back.
3. Removes the exclude line and its comment when install added them.
4. Removes `~/.claude/agents/spudagent.md` and `~/.claude/skills/spud/` when no other non-home project is still installed and the files still match what install wrote (by SHA-256). A changed file is left, with a warning.
5. Releases every open claim on the project, sets `projects.installed` to NULL, and writes a `project.uninstalled` event and a report entry.

## 3. Path rule for Laws 1 and 5

### 3.1 Mapping

`map_into_repository(home, path, worktrees)` [code, line 4685] becomes `map_into_checkouts(roots, path)`:

- `roots` is `[(project_id, root, worktrees)]` for every active project.
- It returns `(project_id, checkout, rel)` for the nearest root by file identity, with the same spelling fallbacks (SPD-029) and the `.claude/worktrees/<name>` rule.
- Canonicalizing a case variant of `ledger`, `reports` or `.spud` to the canonical name happens only when the project is 1, except `.spud`, which is canonicalized under every root.

`repository_paths(ctx, path, cwd)` becomes `project_paths(ctx, con, path, cwd)`: every reading of the path, lexical and real, mapped with `map_into_checkouts`. `folds_case(root)` stays per root [code].

### 3.2 Who may write where

`edit_reason` and `path_reason` implement this table. The Bash hook's redirection and `tee` targets go through `edit_reason` already [code: `redirect_reason`], so they follow it unchanged. "Mode" is the session's mode (§6.1). A member is a caller whose `agent_id` is bound to a member row.

| Target | Spud session, no agent_id | Plain session, no agent_id | Member | Unbound agent_id, Spud session | Unbound agent_id, plain session |
|---|---|---|---|---|---|
| `.spud/**` at any project root | refused (SPD-031) | refused | refused | refused | refused |
| Home `ledger/**`, `reports/**` | refused, Law 5 | refused, Law 5 | refused, Law 5 | refused, Law 5 | refused, Law 5 |
| Home, `SPUD_PATHS` | allowed | refused: "a session that is not Spud does not write in Spud's home" | its globs | refused: not bound | refused, as for the plain session |
| Home, any other path | refused, Law 1 | refused, as above | its globs | refused: not bound | refused, as above |
| A checkout of project P ≠ home | refused, Law 1: in another project every path is a deliverable | allowed (silent) | its globs, when they name P (§3.3) | refused: not bound | allowed (silent) |
| Outside every project | allowed | allowed | allowed | allowed | allowed |

The last row is unchanged: the ledger does not police the rest of the disk. Two changes from v1 follow from the table. A Spud session in the home editing a BadTakes path is refused under Law 1, where v1 says nothing because the path is outside the home [code: `edit_reason` returns early]. A plain BadTakes session is refused everywhere in the home, but never in BadTakes.

**"Spud's own files" in another project: none.**

- `SPUD_PATHS` and `GENERATED_ROOTS` are the home's. BadTakes' `CLAUDE.md`, `.claude/skills/**` and `.claude/settings.json` are the project's and are deliverables.
- The one file Spud keeps there, `.claude/settings.local.json`, is written by `spud project install|sync` from Python and never by the Edit tool. So the Law 1 row needs no exception.
- A directory named `ledger/` or `reports/` in BadTakes is ordinary code.

### 3.3 Deliverable globs

- **A bare glob** (`src/**`, `test/render.test.js`) is relative to the ticket's project, in any checkout of it: the root or a worktree. This is v1's rule with "the home" replaced by "the ticket's project" [code: `path_reason`].
- **A qualified glob** `<key>:<glob>` names another project's checkout, as in `spud:docs/design/2026-09-20-take-001.md`. A BadTakes ticket's spike note can then go to the home's `docs/`, the "mixed tickets" split of CLAUDE.md.
- **Normalizing.** `normalize_deliverable` accepts an optional prefix matching `[a-z][a-z0-9-]*:` and applies its existing rules to the rest. `plan_member` and `member edit` refuse a key that is no active project.
- **Matching.** A path in project P matches a member's glob when the glob's project, the qualifier or else the ticket's project, is P and the relative path matches.
- **Rendering.** Deliverables render only in `member show` and the SubagentStart context, never in frontmatter [code: `render_member`], so the `word:text` rule for Obsidian properties is not at stake.

## 4. Rendered ledger

**Files stay flat at the home.** Examples are `ledger/tickets/TAKE-001.md` and `ledger/teams/TAKES-001/Russet.md`.

- `render_targets` and `classify_path` already build paths from `tickets.key` and `team_key` and need no change [code].
- Wikilinks `[[TAKE-001]]` and `[[TAKES-001/Russet|Russet]]` resolve by unique basename and folder, because prefixes are unique across projects (§1.2).
- The alternative, per-project folders (`ledger/projects/badtakes/tickets/`), breaks every `file.inFolder` filter in both `.base` files and every generated-path check, and buys nothing the property below does not.

**A `project` property.**

- **Tickets.** `render_ticket` emits `project: badtakes`, a plain scalar, right after `origin`. A stored `layout.fm_keys` (an imported ticket's) that lacks the key gets it inserted after `origin`.
- **Members.** `render_member` emits it after `ticket`.
- **Where.** Every note carries it, `project: spud` in the home included, so Bases group and filter without an "(empty)" bucket.
- **Import.** `import --file` refuses a change to `project`.
- **Graph.** The value is a plain key, not a link, so projects do not become graph nodes. A link would need project notes, and a note for the home named `spud` would clash with `ledger/Spud.md` under Obsidian's case-insensitive link resolution.

**`ledger/Projects.md`, generated.**

- **Content.** Frontmatter `tags: [projects]`, the generated marker, then one table: Key, Name, Tickets (prefix), Team (prefix), Root, Default branch, Landing, Sessions, Remote. Archived projects are listed with their archive date.
- **Rendering.** `render_targets` adds it, `classify_path` knows it, and `import --file` never accepts it.
- **Why it exists.** It is the disaster-recovery source for the `projects` table. `bulk_import` reads it before any ticket, because `import_ticket_file` finds a ticket's project by its prefix [code, line 2183]. Without it, a BadTakes ticket could not be re-imported.

**The Obsidian views.** These edits are Spud's, since the `.base` files and `Home.md` are his files (§9).

- `Board.base`: a `project` property with display name Project, added to the `Board` and `Closed` views right after `file.name`. A new view `By project` has the Board filter and `groupBy: project`. Existing `sort`, `order` and `columnSize` stay Eric's.
- `Fleet.base`: `project` added to the `All` view after `ticket`.
- `Home.md`: a line under "Where to look" linking `[[Projects]]`. One sentence under "How to read a name": a ticket key's prefix names its project, SPD for Spud and the new prefix for BadTakes.

**CLI and reports.**

- `spud board` shows a project column when more than one project exists, and `board`, `card` and `events` take `--project <key>`.
- `board --brief` lines stay as they are, since the key's prefix names the project.
- Report titles already carry ticket keys. The `project` commands write their own report entries.

## 5. Git and worktrees across repositories

### 5.1 What a session whose cwd is BadTakes can do

| # | Claim | Status |
|---|---|---|
| G1 | While a session is isolated in a worktree, four checks refuse: an Edit, Write or NotebookEdit targeting the main checkout; a Bash command whose working directory resolves to the main checkout; git redirected into the main checkout (`git -C`, `--git-dir`, `GIT_DIR`, `GIT_WORK_TREE`, or a `cd` first); a command whose text cannot show that its git stays inside the worktree. "The checks apply to the repository you launched Claude Code from. They also cover the main checkout a linked worktree is linked from." | [doc: worktrees, "How Claude Code enforces isolation"] |
| G2 | From a BadTakes worktree session, `git -C /Users/ericlugo/Personal/Spud add\|commit\|push` is refused by the command-shape check, even though Spud's home is not BadTakes' main checkout. SPD-007's observed refusal text was "a worktree-isolated session's git operations must target its own worktree" [code: memory note `worktree-session-git-guard`]. The probe's unrefused `git -C` after entering a worktree was made from a launch directory with no git repository, so G1 had nothing to protect (P6). | [assumed]; the design treats it as refused; scenario `project-worktree` settles it (§7.4) |
| G3 | From a plain (not worktree-isolated) session launched in BadTakes, `git -C <home> …` is not refused by the harness. It is subject only to the permission prompt for Bash. | [doc: G1's checks apply only "while a session is isolated"]; [probe P6: read-only `git -C <home> log` from a session launched elsewhere] |
| G4 | A `spud` subprocess that runs git itself is invisible to the command-text checks. | [assumed]. The design does not rely on it: `ledger commit` refuses from any linked worktree (§5.3), so it never goes around the harness's intent. |
| G5 | `spud render` writes the home's `ledger/` from Python, not through the Edit tool, so G1's file-edit check does not apply. Worktree sessions in the home already render right after a spawn. | [code: `write_whole`]; the render-after-spawn practice |
| G6 | The local settings file and saved approvals resolve to the main checkout in a worktree. | [doc: settings, permissions] |
| G7 | `EnterWorktree` with a name creates `.claude/worktrees/<name>/` on branch `worktree-<name>`, from the remote default branch (`worktree.baseRef` `fresh`). It copies no gitignored file unless `.worktreeinclude` names it. | [doc: worktrees] |
| G8 | BadTakes' `main` is protected and takes pull requests only. Branches are `feat/`, `fix/` or `chore/`. Staging is explicit paths. PRs are ready, not draft, and their body ends with the Claude Code line. `bash scripts/worktree-init.sh` runs first in every fresh worktree. | [code: BadTakes `CLAUDE.md`, skills `committing-and-pushing` and `parallel-worktrees`] |

### 5.2 The recipe for a BadTakes code ticket

1. The session is launched in BadTakes' main checkout. Eric types `/spud`, and the session claims.
2. `spud --as spud ticket new --title … --priority P1 --status active --brief @-` (project defaults to badtakes), then `spud --as spud ledger commit --message "TAKE-001: created …"`. The session is not isolated, so this runs (G3).
3. `EnterWorktree` with name `take-001-<slug>`. Then `git branch -m feat/take-001-<slug>` (or `fix/`, `chore/`), a plain single git command inside the worktree, as BadTakes' branch rule requires (G8).
4. `member new` with bare globs relative to BadTakes. The engineer's brief says to run `bash scripts/worktree-init.sh` before anything else (G8) and names the verification tier from BadTakes' `implementing-changes` skill. Spawn, then `spud render` (G5).
5. On return: `member finish`, proposals decided, `spud render`. Spud commits the code on the branch inside the worktree, per `committing-and-pushing` (explicit `git add <paths>`, the leak check, the message rules). He pushes with `git push -u origin <branch>`. Landing follows the project's policy (§5.4).
6. `ExitWorktree` (keep). The session is back in the main checkout: `spud --as spud ledger commit --message "TAKE-001: done; PR #<n>"`.
7. The worktree is removed after the PR merges, per BadTakes' `parallel-worktrees`, by whichever session sees the merge.

The home's own recipe is unchanged, except that step 6's commit may use `ledger commit` too.

### 5.3 `spud ledger commit`

`spud --as spud ledger commit --message <text|@file|@-> [--no-push]`, Spud's only (Law 6, §1.2). The steps, in order:

1. **Refuses from a linked worktree** of any repository: when `git rev-parse --git-dir` and `--git-common-dir` differ for `os.getcwd()`. The message: "run it from a main checkout: ExitWorktree (keep) first". This mirrors G1 and G2 instead of slipping past them.
2. **Refuses when the home is not on its default branch** (`projects.default_branch` of project 1).
3. **Refuses when anything outside `ledger/` and `reports/` is already staged in the home**: `git diff --cached --name-only`, with `GIT_REDIRECTS` stripped. Spud's own files are committed with plain git, not swept into a ledger commit.
4. **Runs the render** of `cmd_render`. A conflict exits 6 and commits nothing.
5. **Stages** with `git -C <home> add -- ledger reports`. This covers the `.base` files, `Home.md` and `Projects.md`.
6. **Nothing staged?** Exit 0 with "nothing to commit".
7. **Requires a ticket key in the subject**: it must contain `[A-Z][A-Z0-9]*-\d{3}`, because every commit names its ticket (CLAUDE.md). Exit 2 otherwise. The message is taken whole, attribution lines included, since Spud writes them.
8. **Commits, then pushes** unless `--no-push`. A failed push exits 1 and keeps the commit.
9. **Records** a `commit` event (an existing kind) with the SHA, the files, the branch and whether it pushed.

### 5.4 Landing, per project

`projects.landing`:

- **`merge`.** Law 10 and Eric's standing call of 2026-09-14: verify, `git merge --no-ff` into the default branch, push. The home is `merge`.
- **`pr`.** Push the branch, then `gh pr create` (ready, not draft, per BadTakes). Record the PR URL in the ticket's Outcome. When the ticket moves to done, and who merges, is question 3 for Eric.

The project's own instructions win for how a deliverable is built, verified, committed and landed; Spud's laws win for who does it. So Law 7 still holds (no spudagent commits or opens a PR), and BadTakes' "branch, commit, push and `gh pr create` are one motion, run without being asked" is carried out by Spud after the outcome is recorded.

## 6. Session ritual and hooks from another cwd

### 6.1 Session mode

`session_mode(ctx, con, payload, env)` runs once per hook call:

1. **`outside`**: `CLAUDE_PROJECT_DIR`, else the payload's `cwd`, maps to no active project. This is a scratch home run with `--settings`, or a hook left behind after `project remove`. It behaves as `spud`, which is today's behaviour and the strict one.
2. **`spud`**: the session has an unreleased `sessions` row, or the launch project's `sessions` is `always`.
3. **`plain`**: otherwise.

The CLI has a mode too: `require_spud` also refuses (exit 3) when `CLAUDE_CODE_SESSION_ID` is set, the working directory's project is a `claim` project, and the session has no claim. `session claim` is exempt. Home sessions and tests without the variable are unaffected.

### 6.2 Each hook

| Hook | Spud session (and outside) | Plain session |
|---|---|---|
| `SessionStart` | Home launch: `board --brief`, as today. Claimed session in project P (resume, compact, clear): a header line naming P, its root, prefixes and landing, plus "you are Spud here; re-read `<home>/CLAUDE.md` now", then `board --brief`. At most 2 KB in all (P2). | One line of at most 300 bytes: "`<root>` is Spud project `<key>` (`<P>-nnn` tickets). This session is not Spud; type /spud to make it Spud." For an `always` project: "This session is Spud: run /spud now to load his instructions." |
| `PreToolUse(Agent)` | As today [code: `hook_agent_spawn`]. | A spawn with `subagent_type` `spudagent` or a description matching `DESCRIPTION` gets the full check, which refuses it (no Spud session planned it). Any other spawn is silent and writes no `spawn_requests` row. |
| `PreToolUse(Bash)` | As today, with the path rule of §3.2. | Checks kept: the `.spud`/`ledger.db` refusal, the `spud hook` refusal, the refusal of `--as spud` on writing commands other than `session claim` ("Law 6: this session is not Spud; /spud claims it"), the member-own `--as` refusal, and every refusal for a bound member. A caller with no agent_id or an unbound one gets no Law 7 refusal and no redirect check outside the home, since those are Eric's own subagents. |
| `PreToolUse(Write\|Edit…)` | §3.2. | §3.2. |
| `PostToolUse(Agent)` | As today. | Silent when no `spawn_requests` row exists: no `hook.error` for Eric's own subagents. |
| `SubagentStart` | As today. When the member's ticket is in a project other than the home, it adds: "Your ticket's project is `<key>`; bare deliverables are relative to `<root>` or a worktree of it; that repository's CLAUDE.md and skills govern how you build and verify." | Silent, and no event, unless the session has an allowed, unbound spawn request. |
| `SubagentStop` | As today. | Silent, and no event, for an agent bound to no member with no candidate request. A bound member is recorded and held as today. |
| `Stop` | As today [code: `stop_owed`]. | Silent. Without this, a member with no known session, which "holds every session" [code], would hold Eric's plain BadTakes sessions. |

### 6.3 The ritual, step 1

CLAUDE.md's step 1 resolves the ledger root from the first line of `git worktree list`, which names BadTakes' checkout when the session runs there. It becomes: read `spud.config.json` at the home, then run `spud session show`. That command prints:

- the home (the ledger root);
- the project of the working directory: key, root and prefixes;
- the checkout: root or worktree, and branch;
- the session id, and the mode `spud` or `plain` with the claim time.

`--json` gives the same.

The home is found by `resolve_home` wherever the cwd is. Hook lines carry `SPUD_HOME` [code: `hook_command`]. A Bash call names the launcher by absolute path, and the launcher resolves the home from the git common dir of its own path [code: `resolve_home`]. `~/.config/spud/home`, written by install, is the fallback for a launcher run from outside any checkout, such as a copy.

Steps 2 to 4 stay: the board, answering, the session title (`TAKE-001 - …`). A session in another project that is not Spud does not run the ritual at all.

### 6.4 Failure policy

The `--project <key>` on a project's hook lines exists for one case: the hook fails before it can read the database, for example during a migration. Then:

- a call with an agent_id still fails closed, as today;
- a call with none fails open, and the gap is spooled as today's recording hooks do.

So a ledger outage cannot stall Eric's plain BadTakes sessions. The home's hook lines carry no `--project` and keep today's policy.

## 7. Implementation split and test plan

### 7.1 Files and functions

All in `bin/spud_ledger.py` unless named otherwise.

- **Schema:**
  - `MIGRATIONS` gains `("0002_projects", DDL_0002)` and `SCHEMA_VERSION` becomes 2;
  - `EVENT_KINDS` gains seven kinds (§8);
  - `VIEWS_AND_TRIGGERS`: `v_board` and `v_fleet` gain `project`;
  - `sync_config_rows` fills project 1's `remote` from `git remote get-url origin` when NULL.
- **Registry and sessions:**
  - new: `validate_project_root`, `project_dict`, `cmd_project_add`, `cmd_project_list`, `cmd_project_show`, `cmd_project_edit`, `cmd_project_install`, `cmd_project_uninstall`, `cmd_project_sync`, `cmd_project_remove`, `cmd_session_claim`, `cmd_session_release`, `cmd_session_show`, `session_mode`;
  - changed: `require_spud` (§6.1), `cmd_ticket_new` (default project), `create_ticket_for_proposal` (unchanged rule, tested).
- **Paths:**
  - `home_worktrees` becomes `checkout_worktrees(ctx, project)`, with its cache at `.spud/worktrees/<key>.json`;
  - `map_into_repository` becomes `map_into_checkouts`, and `repository_paths` becomes `project_paths`;
  - changed: `path_reason`, `edit_reason`, `in_state_dir` (any root), `normalize_deliverable`, `normalize_deliverables`, `plan_member` and `cmd_member_edit` (qualifier keys).
- **Hooks:**
  - `hook_pre_tool_use` computes the mode once and passes it to `hook_agent_spawn`, `hook_bash` / `bash_reason` and `hook_edit`;
  - `hook_post_tool_use`, `hook_subagent_start`, `hook_subagent_stop`, `hook_session_start` and `hook_stop` take the mode;
  - `cmd_hook` accepts `--project` and applies §6.4;
  - `hook_command` takes the optional key.
- **Settings:** `merge_settings` (§2.2), used by `cmd_settings_sync` and `cmd_project_install`; new `merge_additional_dirs`, `ensure_ignored`, `user_claude_dir` (honours `SPUD_USER_CLAUDE_DIR`), and a `SKILL_TEMPLATE` constant.
- **Render and import:**
  - changed: `render_ticket`, `render_member` (the `project` key), `render_targets` (adds `ledger/Projects.md` through a new `render_projects`), `classify_path`, `bulk_import` (reads `Projects.md` first; new `import_projects_file`), `accept_ticket_edit` and `accept_member_edit` (refuse `project`);
  - `cmd_board`, `cmd_card` and `cmd_events` take `--project`.
- **Git:** new `cmd_ledger_commit`.
- **Doctor:** `cmd_doctor` gains a projects section: root exists and is a main checkout; the settings file carries this home's hooks; the file is ignored; the user agent matches the home's; the skill is present; the home pointer.
- **Parser and hook tables:** `build_parser` adds `project`, `session` and `ledger`; `SPUD_COMMANDS`, `SPUD_ONLY_SUBCOMMANDS` and `parse_spud_call` change as in §1.2.
- **Spud's files** (§9): `CLAUDE.md`, `.claude/agents/spudagent.md`, `ledger/Board.base`, `ledger/Fleet.base`, `ledger/Home.md`.

### 7.2 Phases and deliverable globs

The code is one file, so implementation is sequential. **Recommended:** one engineer, Anya's successor as lead (`01`-level member, opus), running three phases. Each phase ends with the full suite green and a `member log` line.

| Phase | Builds | Deliverables (this phase's additions) |
|---|---|---|
| A. Schema, registry, sessions, render, ledger commit | §1, §2.5's claim commands, §4, §5.3, §8 | `bin/spud_ledger.py`, `tests/test_projects.py`, `tests/test_sessions.py`, `tests/test_ledger_commit.py`, `tests/test_migrate_projects.py`, and the existing tests the `project` property changes: `tests/test_render.py`, `tests/test_import.py`, `tests/test_acceptance.py`, `tests/test_team_card.py`, `tests/helpers.py`, `tests/fixtures/**` |
| B. Installation and enforcement across repositories | §2.1 to §2.4, §2.7, §3, §6 | `tests/test_install.py` (the lead); `tests/test_hooks_projects.py` (the child below) |
| C. Headless scenarios | §7.4 | `tests/probes/headless.py` |

**Parallel variant, within `child_fan_out: 2`.** From the start of phase A, the lead may plan one child engineer on opus with the single deliverable `tests/test_hooks_projects.py`. The child writes that file from the tables in §3.2 and §6.2, against the spec, with its own temporary-repository helpers inside the file; `tests/helpers.py` stays the lead's. The child's tests are red until phase B lands, so the lead runs them only then. No other split has disjoint globs, since every behaviour is in the one module.

### 7.3 Unit tests

The unit tests use temporary directories. Git repositories are built in `setUp` through `subprocess`, with a clean environment, the way `WorktreeElsewhereTest` does [code: `tests/test_hooks.py` line 2883]: `git init -b main`, an empty commit, `git worktree add`. The home is a temporary copy, as in `tests/helpers.py`.

- **`test_migrate_projects.py`**
  - A v1 database with rows migrates to v2: pre-migration backup written, project 1 defaults, `sessions` exists, a `session.claimed` event inserts, the append-only triggers still refuse update and delete, the views carry `project`.
  - The CLI refuses a v1 database until `migrate` [code: `connect`].
- **`test_projects.py`**
  - `add` validation, one test per rule of §1.2: not a directory, not a repository, a linked worktree (message names the main checkout), the home, a root inside the home, a root containing another project, a duplicate key, the key `spud`, a lower-case prefix, equal ticket and team prefixes, a prefix equal to another project's team prefix.
  - `list` and `show`; `edit --root`; prefixes immutable after a ticket; `remove` deletes an empty project and archives one with tickets; `remove` refused with an active ticket and for project 1.
  - `ticket new` from a subprocess whose cwd is the other repository, then one of its worktrees, then the home: `TAKE-001`, `TAKE-002` and `SPD-nnn` numbered independently.
  - A proposal from a `TAKE` ticket creates a `TAKE` ticket.
  - `render` writes `ledger/tickets/TAKE-001.md` with `project: badtakes`, `ledger/teams/TAKES-001/<Name>.md`, and `ledger/Projects.md`.
  - `import` of a rendered tree into an empty database round-trips both projects.
- **`test_sessions.py`**
  - `session claim` needs `CLAUDE_CODE_SESSION_ID`; its project default comes from cwd; it refuses outside a project; its card is at most 1.5 KB.
  - Claiming the home is a no-op; `release`; `show --json` fields.
  - `require_spud` refuses `--as spud ticket new` from an unclaimed `claim` project and allows it after the claim and in the home.
- **`test_ledger_commit.py`**
  - The home has a bare remote.
  - It commits only `ledger/` and `reports/` and pushes.
  - Refusals: from a linked worktree cwd (of the home and of the other repository); off the default branch; with another path staged; with a subject lacking a ticket key.
  - Nothing to commit exits 0; a render conflict exits 6 with no commit; `--no-push`.
- **`test_install.py`**
  - The other repository holds a pre-existing `.claude/settings.local.json` shaped like BadTakes'. After install:
    - foreign keys are kept byte for byte in value;
    - eight hooks carry `SPUD_HOME=<home>` and `--project <key>`;
    - two allow rules;
    - no `env`, no `deny`;
    - `additionalDirectories` holds the home;
    - the file is ignored (exclude line added only when needed);
    - `SPUD_USER_CLAUDE_DIR/agents/spudagent.md` equals the home's;
    - the skill has the home path;
    - a second install writes nothing.
  - Uninstall restores the original bytes, or removes a file install created. It keeps the user files while a second project is installed and removes them after the last. It leaves a user file that was changed.
  - `settings sync` for the home is unchanged: its existing tests pass.
- **`test_hooks_projects.py`** (the child, or the lead in phase B)
  - Every cell of §3.2 through `PreToolUse(Write)` and through a Bash redirect, for a member of a `TAKE` ticket with globs `src/**` and `spud:docs/x.md`: in the root and in a worktree of the other repository, in the home, outside.
  - Every row of §6.2 with `CLAUDE_PROJECT_DIR` set in the hook's environment, for a claimed session, an unclaimed one and a home session:
    - the SessionStart notice and its size;
    - silence and no `spawn_requests` row for an ordinary Agent;
    - a refused spudagent-shaped spawn in a plain session;
    - `--as spud ticket new` refused and `session claim` allowed in a plain session;
    - no `hook.error` from PostToolUse;
    - SubagentStart and SubagentStop silence;
    - Stop silent with a sessionless planned member older than ten minutes.
  - The §6.4 failure policy with the database made unreadable: exit 2 for a payload with agent_id, exit 0 without.
  - A description `TAKES-001/Name (01, engineer)` binds exactly like `SPUD-nnn`.

### 7.4 Headless scenarios

These run in `tests/probes/headless.py`, one at a time, on haiku, at roughly $0.10 to $0.50 each. Each builds a scratch home (a copy of `bin/spud` and the config, `spud init`) and a scratch "other" repository with a commit and a bare `origin` (git run by the probe script in its scratch directory, as the unit tests do). Then `spud project add` and `project install` with `SPUD_USER_CLAUDE_DIR` pointed into the scratch directory. It passes `spudagent` with `--agents`, since a headless run cannot redirect user scope. It launches `claude -p` in the other repository with `--setting-sources project,local`, so the installed local settings are what load. Nothing touches Spud's or BadTakes' repositories.

1. **`project-plain`.** Without a claim, the session:
   - edits `src/a.txt` in the other repository (allowed);
   - spawns an ordinary subagent with `isolation: "worktree"` (allowed: no deny rule);
   - tries `--as spud ticket new` (refused, with the /spud message);
   - ends its turn while the scratch ledger holds a sessionless planned member older than ten minutes (the grace is overridden in the scratch, not held).

   Afterwards the ledger shows no `hook.error` and no `spawn_requests` row from the session, and the SessionStart context was the one-line notice.
2. **`project-spud`.** The session:
   - runs `session claim`, `ticket new` (TAKE-001), and `member new` with globs `src/**` and `spud:docs/x.md`;
   - spawns a background spudagent. The child writes `src/hello.txt` (allowed), `README.md` (refused, Law 5), `<scratch home>/docs/x.md` (allowed) and `<scratch home>/bin/x` (refused), then records its Result;
   - runs `member finish`, then `ledger commit`.

   Afterwards the scratch home's `git log -1` names TAKE-001, the bare remote has it, and `ledger/tickets/TAKE-001.md` carries `project: badtakes`. This is "done when" on scratch repositories.
3. **`project-worktree`.** The session:
   - claims, then enters a worktree with `EnterWorktree`, and checks the settings file still loads there (G6: a hook fires);
   - spawns a spudagent that writes `src/w.txt` in the worktree (allowed, and bound from the worktree cwd);
   - runs `git -C <scratch home> log --oneline -1` (records G2: refused or not);
   - runs `ledger commit` (refused by the CLI in the worktree), then `ExitWorktree` keep, then `ledger commit` (succeeds).

   The same run records whether `spudagent` from `--agents` and the `disable-model-invocation` key behaved as §2.4 and §2.5 assume. It also records whether `--resume` keeps the claim.

### 7.5 Acceptance in BadTakes, by Spud after merge

Spud, not a spudagent, runs this; it needs Eric's answers to the open questions.

1. `spud --as spud project add /Users/ericlugo/Personal/BadTakes --key badtakes --ticket-prefix <P> --team-prefix <T> --landing pr --sessions claim`, then `spud --as spud project install badtakes`, then `spud doctor`.
2. A session in BadTakes, `/spud`, and one small real ticket Eric picks, run by the recipe of §5.2 through a ready PR.

Proven when:

- `spud card <P>-001` shows the team with hook-bound members whose events carry a `cwd` in BadTakes or its worktree;
- `ledger/tickets/<P>-001.md` and `ledger/teams/<T>-001/` are committed on the home's `main` and pushed;
- the ticket's Outcome carries the PR URL;
- a plain BadTakes session opened the same day was never held or refused.

## 8. Migration

**`0002_projects`** runs inside `apply_migrations` (backup first, one transaction) [code]:

```sql
ALTER TABLE projects ADD COLUMN default_branch TEXT NOT NULL DEFAULT 'main';
ALTER TABLE projects ADD COLUMN landing  TEXT NOT NULL DEFAULT 'merge'  CHECK (landing  IN ('merge','pr'));
ALTER TABLE projects ADD COLUMN sessions TEXT NOT NULL DEFAULT 'always' CHECK (sessions IN ('always','claim'));
ALTER TABLE projects ADD COLUMN installed TEXT CHECK (installed IS NULL OR json_valid(installed));
ALTER TABLE projects ADD COLUMN archived_at TEXT;

CREATE TABLE sessions (                        -- claims (§2.5); the home's sessions need none
  session_id  TEXT    PRIMARY KEY,
  project_id  INTEGER NOT NULL REFERENCES projects(id),
  claimed_at  TEXT    NOT NULL,
  released_at TEXT,
  cwd         TEXT
) STRICT;

-- events.kind's CHECK widens by rebuilding the table (SQLite cannot alter a CHECK).
DROP TRIGGER IF EXISTS events_no_update;
DROP TRIGGER IF EXISTS events_no_delete;
CREATE TABLE events_new ( /* events' v1 columns, the kind CHECK widened with:
  'project.added','project.edited','project.installed','project.uninstalled','project.removed',
  'session.claimed','session.released' */ );
INSERT INTO events_new SELECT * FROM events;
DROP TABLE events;
ALTER TABLE events_new RENAME TO events;
-- the four indexes re-created; VIEWS_AND_TRIGGERS then re-creates both triggers and the views
```

Notes:

- The triggers are dropped before `DROP TABLE` so that no reading of SQLite's trigger rules is needed.
- `ADD COLUMN` with a `CHECK` on a `STRICT` table is [assumed] supported by SQLite 3.53.4. `test_migrate_projects.py` settles it.
- `v_board` gains `(SELECT key FROM projects WHERE id = t.project_id) AS project`, and `v_fleet` the same through the ticket.

**Data.**

- Every existing ticket is already `project_id = 1` (47 of 47 [code: `spud sql`]); nothing is backfilled.
- Project 1 takes the defaults: `main`, `merge`, `always`.
- The worktree cache `.spud/worktrees.json` is superseded by `.spud/worktrees/spud.json`. The old file is ignored, and doctor notes it once.
- The home's hook lines do not change, so `settings sync` has nothing to do.

**Rollout, Spud at merge.**

1. Merge. Then `spud migrate`, which writes the pre-migration backup.
2. `spud render`: every note gains `project`, and `ledger/Projects.md` appears.
3. Spud's edits (§9). Then `spud --as spud ledger commit --message "SPD-014: …"` in the home.
4. On Eric's answers, `project add`, `project install` and the acceptance of §7.5.
5. The suite count in CLAUDE.md is updated.

**Rollback.**

- Before any project ticket exists: `project uninstall`, restore `.spud/backups/ledger-<stamp>-pre-0002_projects.db` with the previous CLI, and re-render.
- After a project ticket exists: forward fixes only, as for every migration.

## 9. Spud's edits at merge

These are his files, not a spudagent's.

- **`CLAUDE.md`:**
  - ritual step 1 as in §6.3;
  - a section "In another project": the claim, the precedence rule of §2.5 step 3, the recipe of §5.2, landing per project, the §3.2 table in two sentences;
  - protocol step 5 names `spud --as spud ledger commit` for the ledger commit;
  - Law 10 reads "merged into the project's default branch by its landing policy";
  - Ledger v1 gains `Projects.md` and the `project` property;
  - Main and worktrees notes that G1's checks bind the launch repository and that `ledger commit` refuses from any worktree.
- **`.claude/agents/spudagent.md`:** bare deliverables are relative to the ticket's project checkout, which SubagentStart names; `<key>:<glob>` names another project; the project's CLAUDE.md and skills govern how to build and verify there.
- **`ledger/Board.base`, `ledger/Fleet.base`, `ledger/Home.md`:** as in §4.
- **`spud --as spud project sync --all`** after editing the agent file, so the user copy follows.

## Open questions for Eric

1. **Which BadTakes sessions are Spud?**
   - (a) Only a session where you type `/spud`: `--sessions claim`. Your other BadTakes sessions keep working as they do today. The ledger stays silent there, except a one-line notice at start and refusing writes into Spud's home.
   - (b) Every session launched in BadTakes: `--sessions always`. Law 1 then refuses the main session every edit in BadTakes.

   **Recommendation: (a).** BadTakes' own CLAUDE.md describes sessions that build and ship changes themselves, and (b) would stop all of them.
2. **BadTakes' ticket and team prefixes.**
   - (a) `TAKE` / `TAKES` (TAKE-001, TAKES-001).
   - (b) `BT` / `BTS`.
   - (c) `BAD` / `BADS`.

   BadTakes branch names already carry `bad-2` and `bad-25` (`claude/bad-2-…`, `chore/bad-25-smoke-surface-spike`), so `BAD-002` would read as that older series. **Recommendation: (a),** distinct from both the old series and SPD/SPUD, and readable in a branch name (`feat/take-001-…`).
3. **When is a BadTakes ticket done, and who merges?** `main` there is protected and PR-only.
   - (a) Spud opens a ready PR, waits for the required checks to pass, records the ticket done with the PR URL, and leaves the merge to you (or to GitHub auto-merge if you turn it on).
   - (b) As (a), then Spud merges with `gh pr merge` once checks pass, mirroring your standing call for Spud's own repository.
   - (c) Done when the PR opens, without waiting for checks.

   **Recommendation: (a).** BadTakes' own rules end a change at "PR open", and your standing merge call was made for Spud's repository. (b) is a one-line change to the landing step if you want it.
