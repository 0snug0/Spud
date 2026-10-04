---
name: spud-cleanup
description: Clean up one Spud project's open tickets as {{identity_name}}: fold duplicates into one, decline what is no longer needed, and set every survivor's priority. Only when {{owner_name}} types /spud-cleanup.
argument-hint: "[project]"
disable-model-invocation: true
---
You are cleaning up one project's tickets as {{identity_name}}. The project key is `$ARGUMENTS`, which may be empty. In these steps `spud …` means `python3.14 -I -S {{launcher}} …`.

1. Read {{home}}/CLAUDE.md in full, then {{home}}/spud.config.json, with the Read tool: never cat or another shell command, because the Bash tool cuts its output at about 30,000 characters. They bind you from now on. Run `spud --as spud session claim` (in the home it says there is nothing to claim, which is enough). If it refuses, quote the refusal, say this session is not {{identity_name}}, and stop. Then run the session ritual of CLAUDE.md from step 2.
2. The project is `$ARGUMENTS` when that is not empty, else the one `spud session show` names. If there is none, or `spud project list` has no project by that key, print that list, say which key was not found, and stop. Pass `--project <key>` to every command below that takes it. This works from the home or from any checkout: it changes the ledger and nothing else.
3. Read every open ticket: `spud board --project <key>`, `spud board --parked --project <key>`, and `spud ticket show <KEY>` for each queued, active and parked one; `spud sql --readonly '<select>'` answers what the board does not. For a large board you may plan scout or researcher members, by the protocol of CLAUDE.md on a ticket of their own, with `home:` deliverables, to check which tickets are already done.
4. Duplicates: group the tickets that ask for the same change. Keep the oldest, or the one with the most work behind it (active, a team, a bound worktree). Carry into the survivor anything only a duplicate says (`spud --as spud ticket edit <KEY> --brief @-`, naming the duplicate), then decline each duplicate: `spud --as spud ticket move <KEY> --status declined --reason "duplicate of <KEY>"`.
5. No longer needed: decline with the evidence in `--reason`: the commit or pull request that already did it (`git log --grep` in the project's main checkout, `spud project show <key>` names it), the ticket that superseded it, or the code it was about that no longer exists. Never decline an `active` ticket that has a live member.
6. Anything you are not sure of goes to {{owner_name}} in one AskUserQuestion of up to four questions, each with your recommendation, and you wait for the answers. Never guess.
7. Reprioritize the survivors by the meanings in CLAUDE.md (P0 now, P1 next, P2 soon, P3 someday), weighing what each blocks, what it depends on, and how stale it is: a ticket another one waits on ranks at least as high as that one. Each change is `spud --as spud ticket edit <KEY> --priority <P> --next "…"`; a priority that stays needs no command.
8. Finish with a short table: each ticket, kept, merged, declined or reprioritized, old to new, and one line of why; then `spud board --brief --project <key>`.

You write no file and no code here. Every decision goes through `spud`, so it is written down with its reason; nothing is moved to done.
