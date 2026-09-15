# PR merge in auto mode spike: what lets Spud land a green BadTakes pull request

Ticket [[SPD-053]]. Written by Charlotte (01, researcher, opus) for Spud, with Kestrel (01.01, contractor on `claude-code-guide`, sonnet) for the official Claude Code citations. Date: 2026-09-14. This spike decides; it does not implement.

**Read-only, and it stayed read-only.** Nothing was merged, opened, labelled, approved or closed; no settings file was edited; no repository setting was changed. Everything below was measured on this machine on 2026-09-14/15 from three sources: the 68 BadTakes session transcripts under `~/.claude/projects/-Users-ericlugo-Personal-BadTakes/*.jsonl` (scanned by scripts in the session scratchpad that pair every `tool_result` with the `tool_use` it answers, and with the last genuine human message before it), the settings files Eric already owns, and read-only `gh` probes. One probe was refused by the classifier and **was not worked around**; that refusal is itself evidence and is reported in full below.

## The finding

There *is* a sanctioned route, it is documented, and it is the one Eric has been using by accident. **The classifier lets the merge through when Eric's own message asked for it.**

The ticket's premise is that the classifier refuses `gh pr merge`. Across 26 attempts in BadTakes sessions on 2026-09-14/15 it refused 14 and allowed 12 — which looks like a coin flip until the attempts are split by whether the most recent human message in that session asked for a merge:

| Last human message in the session | Merged on the first attempt | Refused |
|---|---|---|
| **Asks for the merge** ("merge it", "Do the merge for me", "merge A as soon as integration goes green", "merge C once B's service is deployed") | **6** | **0** |
| Does not ("activate BAD-001 and do it", "close BAD-017", "Stack is back up", "merged, clean up", or no human message in the session at all) | 6 | 14 |

Fisher exact, two-sided: **p = 0.004**. And the split has a documented mechanism behind it. From the auto mode configuration page, the fourth and last tier of the classifier's precedence:

> "Explicit user intent overrides the remaining soft blocks: if the user's message directly and specifically describes the exact action Claude is about to take, the classifier allows it even when a `soft_deny` rule matches."

`Merge Without Review` is a **`soft_deny`** category, not a `hard_deny`, so it is exactly the tier that stated intent clears.

The decisive pair is PR 337, in one session (`e750bab1`), with no configuration change between the two calls:

- `00:36:28` `gh pr merge 337 --squash --match-head-commit e874dff2…` → refused, `Reason: [Merge Without Review]`. No human message had been typed in that session yet.
- `00:37:24` Eric typed **"Do the merge for me"**. **The identical command, byte for byte** → allowed, merged.

Three further things the transcripts settle:

- **One statement of intent covers the rest of the session.** "merge C once B's service is deployed" preceded three separate allowed merges (PRs 347, 349, 350) over twenty minutes. Intent does not have to be restated per PR.
- **"merged, clean up" was refused** (PR 341, 02:19:22) — a message that mentions merging but asks for something else. The tier is reading intent, not keywords, which is the behaviour you want and the reason no phrase should be treated as a password.
- **The category fires on reads too, so it is not a merge detector.** `gh pr view 330 --json state,mergedAt,mergeCommit` and `gh pr view 350 --repo 0snug0/BadTakes --json state,mergeCommit` were both refused with `[Merge Without Review]`; neither can merge anything, and I ran both of those reads today and both were allowed. The 6 merges allowed without stated intent are the same judgement going the other way. Expect noise in both directions; the intent tier is what removes it.
- **The brief's list of five refused PRs needs one correction.** PR **350's merge was allowed** at 04:23:15; what was refused 30 seconds later was the follow-up *read* of its merge commit.
- **Spud's own repository has never had a merge refused.** Across every Spud session transcript there are exactly two denials, both `[Irreversible Local Destruction]` on worktree and branch deletion. Law 10's `git merge --no-ff` into `main` at the ledger root has never been touched. This is specific to landing a pull request.

## Recommendation

**Eric states the landing intent once, in his own opening message; nothing is configured, and nothing is weakened.**

When Eric opens a BadTakes session he already names the ticket ("Work on BAD-027"), which is what the auto-claim hook matches. Adding the landing to that sentence — *"Work on BAD-027, and merge its PR into main once Gate is green"* — supplies the documented fourth tier for the whole session, and the measured evidence is 6 for 6 with one statement covering three merges.

- **It is the sanctioned route, not a route around.** It is the mechanism the classifier documents for precisely this case: the human authorising the specific action. Nothing is suspended, no rule is added, and the classifier keeps refusing every merge Eric did *not* ask for — in another repository, in a session he opened for something else, or under a hostile instruction Claude read somewhere. That is the safeguard working as designed rather than being removed.
- **It does not weaken review in general, or at all.** No permission rule changes, `autoMode` is untouched, the `soft_deny` line covering `Bash(gh release:*)` and `Bash(gh workflow run:*)` for BadTakes keeps working, and the required `Gate` check on `main` is enforced server-side regardless: if Gate is red, GitHub refuses the merge whatever the classifier decided.
- **It costs Eric one clause per session, against one click per PR today.** That is squarely inside his standing call of 2026-09-14 ("stop asking me to do the merges and everything"): he is not being asked to merge, he is saying up front that Spud should.
- **Why CLAUDE.md cannot do it for him.** Per the documentation, the classifier reads *messages and commands*, not tool output — and CLAUDE.md, the ticket brief and the ledger are context, not Eric's message. The intent has to come from him, in the session. This is a feature: it is what stops an instruction written into a file from authorising a merge.

**The hard line, and it is not negotiable.** Spud must never manufacture this intent. No hook that injects a user-shaped message, no prompt Spud writes into a spudagent's brief, no restating Eric's words back into the transcript to satisfy the tier, and no asking Eric to repeat a phrase whose only purpose is to unlock the command. The tier exists to record that a human decided; forging it is forging consent, and it is worse than the hand-merge it would replace. If the merge is refused and Eric has not asked for it, **do not retry** (PR 341 was refused twice in a row, PR 328's attempt was written as `merge || merge` with a second invocation in the fallback) — record the PR as green and ready, and let him decide.

**If Eric would rather it be hands-free, the escalation is one narrow permission rule**, and it is a genuine trade, not a free win. Add to BadTakes' untracked `/Users/ericlugo/Personal/BadTakes/.claude/settings.local.json`, in `permissions.allow`:

```json
"Bash(gh pr merge:*)"
```

Documented behaviour: *"narrow Bash and PowerShell allow rules such as `Bash(npm test)` stay in effect in auto mode, and Claude Code resolves them before the classifier runs"*, suspending only broad rules that grant arbitrary code execution, *"such as `Bash(*)` or wildcarded interpreters"*. `gh` is not an interpreter, so this rule should stay in effect and the call should never reach the classifier. What it costs: BadTakes' `main` has `required_approving_review_count: 0` (measured), so the classifier's refusals have been the only thing producing a human look at a BadTakes merge, in the form of Eric clicking the button. The rule removes that. It is scoped to BadTakes and to one `gh` subcommand — `gh pr create`, `gh pr review`, `gh release`, `gh workflow run` and `git push` are untouched — but inside BadTakes it is unconditional, and no permission rule can express "only when Gate is green". Recommended only if Eric decides the `Gate` check alone is the review he wants.

## The five questions, answered

**1. Does the classifier honour `permissions.allow`, which syntax, which scopes, and are there categories it refuses anyway?**

Yes. A narrow Bash allow rule is resolved before the classifier and bypasses it; only broad rules granting arbitrary code execution are suspended in auto mode. The exception is `autoMode.classifyAllShell`: *"Set `autoMode.classifyAllShell` to `true` to suspend every Bash and PowerShell allow rule while auto mode is active, so the classifier evaluates every shell command regardless of your allow list."* It is absent from Eric's settings today; if he ever sets it, the escalation route above stops working.

Inside the classifier, precedence is four tiers: `hard_deny` blocks unconditionally, `soft_deny` blocks next, `allow` rules override matching `soft_deny` rules as exceptions, and explicit user intent overrides the remaining soft blocks. `Merge Without Review` is `soft_deny`, so there is no "refuses even with an allow rule" problem here.

Syntax: `Bash(gh pr merge:*)` and `Bash(gh pr merge *)` are documented equivalents (*"The `:*` suffix is an equivalent way to write a trailing wildcard"*); `Bash(gh:*)` is far too broad. Rules match the literal command string and are explicitly fragile — the docs' own example shows `Bash(git push *)` missing `git -C . push origin main` — so a permission rule is a convenience for the honest case, never a boundary.

Scopes for `permissions`: managed > `--settings` > project-local (`.claude/settings.local.json`) > project-shared (`.claude/settings.json`) > user. All count. **`autoMode` is the exception**: *"The classifier doesn't read `autoMode` from project settings in `.claude/settings.json` or `.claude/settings.local.json`. Both files live in the repo directory, so a checked-in repo or a build step could otherwise inject its own allow rules."* So `autoMode` cannot be scoped to BadTakes by location and cannot be written by `spud project install`.

Local corroboration, measured rather than relayed: Eric's `~/.claude/settings.local.json` carries `Bash(gh api *)`. Over the BadTakes transcripts, `gh api` was called 21 times and refused **0** times, including calls reading merge state (`gh api repos/0snug0/BadTakes/pulls/329 --jq '{state,merged,merged_by:.merged_by.login}'`). Over the same transcripts, `gh pr merge` (no rule) was refused 18 of 57 times and `gh pr view` (no rule) 7 of 74. Consistent with the documented precedence. Note also that Eric's own `Bash(node -e ' *)` rule in BadTakes is a wildcarded interpreter and is therefore one of the rules auto mode suspends — a useful reminder that "narrow" excludes interpreters.

**2. Is `Merge Without Review` satisfied by context — a reviewer spudagent's verdict, `/code-review`, an approving review on the PR, the PR body?**

**By Eric's message, yes — that is the whole finding above. By anything else, no.** The documented statement about what the classifier reads is *"The classifier reads your messages and the commands Claude runs, not their output"* (made about the repository-visibility slot; generalising it to this category is inference, and it is flagged as such). The transcripts agree: all 14 refusals happened *after* the session had just read a green check rollup in the same turn, and PRs 339, 340, 341 and 351 were refused with `Gate pass` and `mergeStateStatus: CLEAN` on screen seconds before. `reviewDecision` was empty throughout because BadTakes requires no reviews. A reviewer spudagent's verdict, a `/code-review` pass and a green `gh pr checks` are all tool output and do not reach the classifier. Treat "explain the review better and it will pass" as unsupported.

**3. GitHub auto-merge.**

Measured: `gh api repos/0snug0/BadTakes --jq .allow_auto_merge` → **`false`**. Auto-merge is off, so `gh pr merge --auto` cannot work today without Eric changing the repository setting.

Changing it would not help. Per Kestrel — and this text exists only in the classifier's live defaults, with no published page describing it, so treat it as the weakest citation here — the classifier carves out `gh pr merge --auto` **when branch protection enforces required reviews server-side**, and blocks it on unprotected repositories or on PRs the agent is not working on. BadTakes has `required_approving_review_count: 0`, so the condition is not met. Raising required reviews to 1 to meet it deadlocks the repository: BadTakes is private with one maintainer, PRs are opened under Eric's own account, and GitHub does not let an author approve their own pull request. Verdict: **no**, on the current setting and on the changed one.

**4. Other routes Eric could configure deliberately.**

- **A GitHub Actions workflow that merges a PR carrying a label Spud adds once Gate is green.** Attractive on paper — merge policy as committed, reviewable code, and `gh pr edit --add-label` is not a merge command. Disqualified by BadTakes' own deploy chain: `deploy-web.yml` and `deploy-site.yml` are `on: push: branches: [main]`, and `server-deploy.yml` chains off the "Server integration" workflow. A merge performed inside Actions with the default `GITHUB_TOKEN` does not create the `push` event those workflows need, so my.badtakes.io, badtakes.io and the Supabase migration and Edge Function deploy would all silently stop happening on merge. Working around that needs a PAT or a GitHub App — a long-lived credential with write access to `main`, created to get past a safety classifier. **Weakens review more than any other route here**, and the refusal message would be right to read the credential as working around the intent. Rejected.
- **Promote `smoke` and `test` to required status checks on `main`.** Does not touch the classifier, but it is the one change in this spike that makes a BadTakes merge *safer* rather than easier, and it is cheap. Filed as a proposal.
- **Running the landing step outside auto mode** — a separate non-auto session or a headless subprocess for the merge alone. No documented supported pattern (Kestrel: `permissionDecision: "allow"` in a `PreToolUse` hook is documented only as skipping the interactive prompt, with *"Hooks can tighten restrictions but not loosen them past what permission rules allow"*, and nothing published says it clears the classifier's separate gate). Rejected on principle before evidence: it is exactly the shape the refusal message warns against, using a different surface to do what was denied.
- **Status quo: Eric merges.** Works, costs a click per PR, and is what the merge memory says today. It remains the fallback whenever intent has not been stated.

**5. The recommended route, the exact change, and how Spud verifies it.** Below.

## Routes considered

| Route | Reliable | Weakens review | Reads as a workaround | Verdict |
|---|---|---|---|---|
| **Eric states the landing intent in his opening message** | 6/6 measured, p = 0.004; documented tier 4 | **no** — the human authorised this merge | **no** — it is the documented mechanism | **chosen** |
| `permissions.allow` rule `Bash(gh pr merge:*)`, BadTakes scope | yes, if the documented precedence holds | inside BadTakes, yes: removes the only look a human gets | no — the remedy the refusal message names | escalation, Eric's call |
| `autoMode.allow` line in `~/.claude/settings.json` | no — still judged by the classifier | narrowly | no | fallback only, Eric's hand, if `classifyAllShell` is ever set |
| `gh pr merge --auto` + enable repo auto-merge | no — carve-out needs required reviews BadTakes lacks | no | no | rejected: would not fire |
| …plus `required_approving_review_count: 1` | yes (nothing merges) | n/a | no | rejected: deadlocks a solo repo |
| Label + Actions workflow merging with `GITHUB_TOKEN` | yes | **yes** — needs a PAT/App, and breaks the deploy chain | yes, once that credential exists | rejected |
| Landing step outside auto mode | — | yes | **yes, explicitly** | rejected |
| Eric merges (status quo) | yes | no | n/a | fallback |

## The exact change

**Primary — no code, no settings.** Two edits in Spud's own files, both his to make:

1. **`CLAUDE.md`, the Landing bullet under "In another project".** After "merge it yourself with `gh pr merge`", add: *"The auto-mode classifier refuses a PR merge as `Merge Without Review` unless Eric's own message in this session asked for the merge; his standing call in this file is not his message and does not count. If he has asked, merge. If he has not and the merge is refused, attempt it once, never twice, never through another tool: record the PR URL in the ticket's Outcome, tell him the PR is green and ready, and move the ticket to done once he has merged. Never manufacture the intent — no injected message, no phrase written into a brief, no asking him to repeat words to unlock a command."*
2. **`~/.claude/projects/-Users-ericlugo-Personal-Spud/memory/merge-to-main-is-erics-call.md`**, the "Other projects" paragraph: replace "a follow-up ticket looks for a sanctioned route" with SPD-053's answer — that naming the landing in his opening prompt ("Work on BAD-027, and merge its PR once Gate is green") lets Spud land every PR in that session, and that one statement covered three merges on 2026-09-15.

**Escalation, if Eric chooses it.** Add `"Bash(gh pr merge:*)"` to `permissions.allow` in `/Users/ericlugo/Personal/BadTakes/.claude/settings.local.json`. **He should add it by hand.** The file is untracked and already written by `spud project install`, but `merge_allow_rules` [`bin/spud_ledger.py:3356`] keeps every allow rule that is not the ledger's own — the `ALLOW_RULE_MARK` filter at line 3364 drops only `^Bash\(.*bin/spud(?: \*|:\*)\)$` — so a hand-added rule **survives every future `project install` and `project sync` untouched**, exactly as his hand-added `Bash(node -e ' *)` already does. **No code change is needed.**

If he would rather the CLI own it, it must be behind an explicit opt-in and never a silent side effect of an install — an install that quietly grants the agent merge rights is the wrong shape, and the reason is in Sources. Precisely, in `bin/spud_ledger.py`:

1. **Line 3300**, `ALLOW_RULE_MARK`: add a second pattern `^Bash\(gh pr merge(?: \*|:\*)\)$` so `settings sync` can replace the rule and the uninstall filter at **line 5157** removes it. Without this the rule outlives `project uninstall` forever.
2. **Line 3311**, `cli_allow_rules(ctx)`: leave it; it is about the spud CLI. Add a sibling `landing_allow_rules(p)` returning `["Bash(gh pr merge:*)"]` when `p["landing"] == "pr"` **and** the project has opted in, else `[]`.
3. **Line 3356**, `merge_allow_rules(ctx, settings)`: take `extra_rules=()` and append them after `cli_allow_rules(ctx)` under the same "only if absent" rule.
4. **Line 3410**, `merge_settings(...)`: add `landing_rules=()` to the keyword-only signature, pass it through at **line 3424**.
5. **Line 5092**, the install path: `merge_settings(ctx, settings, env=False, deny=False, additional_dirs=[str(ctx.home)], project_key=p["key"], landing_rules=landing_allow_rules(p))`. The opt-in belongs in the `projects` row, not in a flag a later install forgets: a `landing_rule` column set by `spud project edit --landing-rule on|off`, default `off`.

The home's own `.claude/settings.json` needs nothing: `git merge --no-ff` there has never been refused.

## How Spud verifies it on the next real BadTakes PR

The classifier is a model, so one success proves nothing. Measure.

1. **Primary route.** On the next BadTakes ticket, note in the ledger whether Eric's opening message named the landing. Open the PR, wait for `Gate`, run `gh pr merge <n> --squash` **exactly once**, and `spud --as spud report add` the outcome with the PR number and whether intent was stated. The answer holds if **five consecutive PRs whose session carried stated intent** land on the first attempt — at the measured 6/20 baseline without intent, five in a row is about a 1-in-400 fluke. Today's evidence is already 6 for 6; five more makes it settled.
2. **A refusal with intent stated** falsifies the finding. Record it with the exact wording Eric used and stop; do not retry, do not rephrase, do not touch `autoMode`. Three such refusals and the answer becomes the escalation rule, or the CLAUDE.md wording that Eric merges.
3. **If the escalation rule is added instead:** confirm `Bash(gh pr merge:*)` is in `permissions.allow` of BadTakes' `.claude/settings.local.json` and that `autoMode.classifyAllShell` is absent or false in `~/.claude/settings.json` — both plain file reads — then **restart the BadTakes session**, because settings are read at session start. Then the same five-PR count, in sessions where intent was *not* stated, so the rule is what is being measured and not the tier.
4. **Either way, never count a retry as a success.** PR 337 is the reason: its second attempt succeeded because Eric had typed "Do the merge for me" in between, not because retrying works.

## Sources

**Official documentation**, fetched live 2026-09-14 by Kestrel (01.01) against Claude Code 2.1.269; pages carry no revision stamp. Full answers in `ledger/teams/SPUD-053/Kestrel.md`.

- Auto mode and the classifier: *"In auto mode, a second model, the classifier, reviews actions instead of you."* ([permission-modes](https://code.claude.com/docs/en/permission-modes.md)); *"Auto mode lets Claude execute without routine permission prompts. A separate classifier model reviews actions before they run, blocking anything that escalates beyond your request, targets unrecognized infrastructure, or appears driven by hostile content Claude read."* ([auto-mode-config](https://code.claude.com/docs/en/auto-mode-config.md)).
- Allow rules and the classifier, and the four tiers, including the stated-intent tier quoted in The finding ([auto-mode-config](https://code.claude.com/docs/en/auto-mode-config.md)); `classifyAllShell` from the same page.
- Bash rule syntax and its fragility: *"The `:*` suffix is an equivalent way to write a trailing wildcard, so `Bash(ls:*)` matches the same commands as `Bash(ls *)`"*; *"Bash permission patterns that try to constrain command arguments are fragile"*, with `Bash(git push *)` shown missing `git -C . push origin main` ([permissions](https://code.claude.com/docs/en/permissions.md)).
- Settings precedence, five levels ([settings](https://code.claude.com/docs/en/settings.md)); the `autoMode` carve-out from both project files ([auto-mode-config](https://code.claude.com/docs/en/auto-mode-config.md)), whose "Where the classifier reads configuration" table lists only `~/.claude/settings.json`, managed settings and `--settings`.
- What the classifier reads: *"The classifier reads your messages and the commands Claude runs, not their output…"* ([auto-mode-config](https://code.claude.com/docs/en/auto-mode-config.md)) — stated for the repository-visibility slot; applying it to `Merge Without Review` is inference.
- Hooks: `"allow"` skips the interactive prompt only, and *"Hooks can tighten restrictions but not loosen them past what permission rules allow"*; `updatedPermissions` with a `setMode` entry ([hooks-guide](https://code.claude.com/docs/en/hooks-guide.md)). Nothing published says `"allow"` clears the classifier's separate gate.
- **Not published:** the `gh pr merge --auto` carve-out exists only in the classifier's live default policy. Treated here as the weakest claim in the spike.

**Measured here, 2026-09-14/15** (scripts in the session scratchpad, not committed):

- 68 BadTakes transcripts; 22 permission denials in total, 14 of them `[Merge Without Review]` on `gh pr merge` across 26 attempts on 2026-09-14/15, plus 2 on `gh pr view … mergeCommit`. The intent split and its Fisher exact p = 0.004. PR 337's refused-then-allowed pair at `00:36:28` and `00:37:24` in transcript `e750bab1`, with "Do the merge for me" between them.
- Named refusal categories are recent: every denial before 2026-09-14 reads `Blocked by classifier` with no category; `[Merge Without Review]`, `[Irreversible Local Destruction]` and `[Auto-Mode Bypass]` all appear from 2026-09-14 onward.
- `gh api repos/0snug0/BadTakes` → `allow_auto_merge: false`, `delete_branch_on_merge: true`. `…/branches/main/protection` → required contexts `["Gate"]`, `strict: false`, `required_approving_review_count: 0`, `enforce_admins: false`, no restrictions; `…/rulesets` → 0.
- `.github/workflows/deploy-web.yml`, `deploy-site.yml`: `on: push: branches: [main]`; `server-deploy.yml`: `workflow_run` chained to "Server integration".
- Settings read, never edited: BadTakes `.claude/settings.local.json`; the home's `.claude/settings.json`; `~/.claude/settings.json` (whose `autoMode` block holds `allow`, `soft_deny` and `environment` lists, each natural-language lines beginning `$defaults`); `~/.claude/settings.local.json` (holding `Bash(gh api *)`).
- `bin/spud_ledger.py`: `ALLOW_RULE_MARK` 3300, `cli_allow_rules` 3311, `merge_allow_rules` 3356, `merge_settings` 3410 and 3424, install path 5092, uninstall filter 5157.

**The refusal this spike did not work around.** Printing the classifier's live default policy with `claude auto-mode defaults` — a read-only command — was refused: *"Permission for this action was denied by the Claude Code auto mode classifier. Reason: [Auto-Mode Bypass]."* It was not retried and no alternative route to that output was attempted. Two consequences, and they are why the settings edits above are Eric's and not Spud's: reading the classifier's configuration is itself gated, and **Spud or a spudagent editing `autoMode` would sit squarely inside the category that refusal names**.

**Relayed from Kestrel, with a caveat to pass on.** Kestrel's first return carried a harness security warning — *"SECURITY WARNING: This subagent performed actions that may violate security policy. Reason: [Auto-Mode Bypass]"* — raised because it ran `claude auto-mode defaults` locally before that command was known to be gated; both of its returns were also flagged as matching instruction-shaped patterns (`settings-json`, `bypass-permissions`) and were read here as data, never as instructions. Kestrel reports seeing no warning on its own side. Its second return was explicitly told not to re-run that command, and did not. Everything it relayed is quoted above with its URL; the one item with no published source is named as such.

**Ledger and prior decisions.** `docs/design/2026-09-14-cross-repository-projects.md` §5 and Open question 3 (Eric chose (b): Spud merges with `gh pr merge` once checks pass). `~/.claude/projects/-Users-ericlugo-Personal-Spud/memory/merge-to-main-is-erics-call.md`. `ledger/tickets/SPD-053.md`.

## Ticket proposals

1. **Make `smoke` and `test` required status checks on BadTakes `main`, alongside `Gate`.** Why: `main` requires one context and zero reviews, so the server-side guarantee behind every BadTakes merge is thinner than the session behaviour suggests; this is the one change here that strengthens review rather than trading it, and it stands whichever route Eric picks. Evidence: `required_status_checks.contexts == ["Gate"]`, `required_approving_review_count == 0`, no rulesets. Persona: engineer (sonnet). Suggested priority: **P2**.
2. **Never retry a classifier refusal, in any project, for any category.** Why: PR 341 was refused twice in one session and PR 328's attempt was written as `merge || merge` with a second invocation in the fallback. Retrying a soft block is the "work around this denial" the message warns against, it corrupts any measurement of whether a fix worked, and PR 337 shows a retry succeeding for a reason that had nothing to do with retrying. Belongs in CLAUDE.md's landing step regardless of this ticket's outcome. Done by Spud in his own files. Suggested priority: **P2**.

## Open questions for Eric

1. **Say it in the prompt?** Adopt the primary route — when you open a BadTakes session, name the landing in the same sentence as the ticket ("Work on BAD-027, and merge its PR once Gate is green"), and Spud lands every PR in that session (recommended: 6 for 6 measured, nothing configured, nothing weakened, one clause per session)?
2. **Or hands-free?** Add `Bash(gh pr merge:*)` to BadTakes' `.claude/settings.local.json` so Spud lands green PRs with no words from you — accepting that `Gate` then becomes the only thing between Spud and a merge, since `main` requires no reviews?
3. **Who writes it, if so?** You add the line by hand, once, and `spud project install` leaves it alone as it already leaves your `node -e` rule alone (recommended) — or the CLI writes it behind a `projects.landing_rule` opt-in defaulting to off, per the five-step change above?
4. **Required checks.** Proposal 1: promote `smoke` and `test` to required contexts on `main`?
