---
name: spud-autopilot
description: Work one Spud project's open tickets as {{identity_name}}, one at a time in board order, each through landing, until none is left or {{owner_name}} says stop. Only when {{owner_name}} types /spud-autopilot in that project's main checkout.
argument-hint: "[project]"
disable-model-invocation: true
---
You are working through one project's tickets as {{identity_name}}. The project key is `$ARGUMENTS`, which may be empty. In these steps `spud …` means `python3.14 -I -S {{launcher}} …`.

1. Read {{home}}/CLAUDE.md in full, then {{home}}/spud.config.json, with the Read tool: never cat or another shell command, because the Bash tool cuts its output at about 30,000 characters. They bind you from now on. Run `spud --as spud session claim`. If it refuses, quote the refusal, say this session is not {{identity_name}}, and stop. Then run the session ritual of CLAUDE.md from step 2.
2. The project is `$ARGUMENTS` when that is not empty, else the one `spud session show` names. If there is none, or `spud project list` has no project by that key, print that list, say which key was not found, and stop. This session must have been launched in that project's main checkout (`spud session show` prints its checkout as `root`); anywhere else, the home or a worktree included, say to launch Claude Code in the root `spud project show <key>` prints and type /spud-autopilot there, and stop. Work on no other project.
3. {{owner_name}} typed /spud-autopilot in this session, which asks for every ticket this run finishes to be landed: each merge and pull-request merge below is that request. The rule of CLAUDE.md on the auto-mode classifier still stands: a refused merge is never retried, rephrased or reached another way. Record the green pull request in the ticket's Outcome, tell {{owner_name}} it is ready, and go on to the next ticket while that one waits.
4. Loop:
   1. Re-read the board: `spud board --project <key>`. Take the first open ticket that is not parked, in board order: priority, then active before queued, then number. Skip a ticket another session holds (live members this session did not spawn, `spud card <KEY>`, or a worktree another session works in), and say which you skipped and why.
   2. Set the session title to `<KEY> - <title>`.
   3. Work it by the full protocol of CLAUDE.md: activate it, `EnterWorktree` named `<key>-<slug>` (the ticket key lower-cased), plan the members, spawn them, record every return before anything else, and verify by the project's own CLAUDE.md.
   4. Land it by the project's `landing`. `pr`: a ready pull request, `spud --as spud pr record` right after `gh pr create`, wait for the required checks, then `gh pr merge`. `merge`: the merge landing of CLAUDE.md, with the full suite once on the tree that merges. Then `ExitWorktree` (keep), remove the worktree and delete its branch by the rules of CLAUDE.md, run the sync the project's CLAUDE.md says the change needs, and `spud --as spud ticket move <KEY> --status done --next "…"`.
   5. Say in one line: the ticket, its pull request or merge, and what landed.
5. Stop when no open ticket you may take is left; when {{owner_name}} says stop (record the current member's return first, and leave an unlanded worktree in place, saying where it is); or when this session's context is near its end (say where the run stopped and which ticket is next).
6. A blocked member, a failing suite or a merge conflict: ask {{owner_name}} with AskUserQuestion (up to four questions, each with your recommendation) and wait. If the answer is to defer it, `ExitWorktree` (keep), park the ticket with the reason (`spud --as spud ticket move <KEY> --status parked --reason "…"`), and go on to the next.
7. At the end, a summary: each ticket worked and how it ended (landed, waiting on a merge, parked), and what is left on the board.
