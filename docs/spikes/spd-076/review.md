**Verdict: Changes required.** One small addition before the merge. The branch fixes the ticket: the suite is green, the three BADS-036 notes round-trip, and an import of the HEAD ledger reproduces every prose column of the live database. But two kinds of stored prose now move text silently between columns while the re-render stays byte for byte. One of them HEAD refused with an error; the other HEAD round-tripped correctly. The smallest fix is a write-time refusal of a prose line that is exactly `## <a section name the note owns>`, which is the ticket's option (b) narrowed to owned names, plus a canary assertion in the acceptance scan. No stored row today holds such a line, so the fix refuses no history and touches no BADS-036 row.

# SPD-076 review: Ozette's heading round trip

Reviewer: Cherie (02, reviewer, opus). Subject: the uncommitted change in `.claude/worktrees/spd-076-heading-round-trip`: `bin/spud_ledger.py` (+65/-15), `tests/test_acceptance.py`, `tests/test_markdown.py`, and the new `tests/test_heading_round_trip.py`.

This note is written in the worktree because the harness refuses writes to the shared checkout from a worktree session. It belongs at `docs/spikes/spd-076/review.md` on `main` at the ledger root, and is not part of the code branch.

## How it was checked

- **Adversarial round trips.** 25 cases, run in scratch homes built with `tests/helpers.Home`. Each case stores prose in the rows, runs `render --out`, imports into a fresh home, then renders again. It compares every note byte for byte and every prose column (ticket brief, sizing and outcome; member brief, result, blocked and outcome), plus layouts and kept sections. Each case ran twice: once on the branch code and once on HEAD's `bin/spud_ledger.py` (39cb9d6; main has since moved only in `ledger/` and `reports/`).
- **Live ledger scan.** Read-only (`spud sql --readonly`) over every stored prose column, member log body, handoff, proposal and kept section.
- **HEAD ledger, imported twice.** The committed ledger was imported once with the branch code and once with HEAD's code, and the columns were compared with each other and with the live database.
- **New tests against both codes.** The new tests ran against HEAD's code in a scratch copy, and against the branch.
- The scratch script was `rt.py` in this session's scratchpad. That copy is temporary, so each case below gives the stored value it used. Any `SpudTestCase` can reproduce a case: set the column, `render --out`, `import` into a new `Home`, then compare the columns and `imported_sections`.

## Case table

"Silent" means the second render is byte-identical to the first while the imported columns differ from the stored ones. Neither test in the change can see that.

| Case | Stored prose | HEAD | Branch |
|---|---|---|---|
| A1 | member brief `Do it.\n## Outcome\nprose` | import refused, "a section repeats" | exact |
| A2 | member brief `Do it.\n\n## Outcome\nprose` | refused | exact |
| A3 | ticket brief `B.\n\n## Outcome\nprose` | refused | exact |
| B1 | member brief with fenced `## Log` | exact | exact |
| B2 | member brief, fence holding a blank line then `## Log` | exact | exact |
| B3 | member brief quoting a fenced member-note template (`## Brief` … `## Blocked` … `## Outcome`); member not blocked | exact | **silent**: brief cut to `Template:` plus the fence line and `## Brief`, and `blocked` goes from NULL to the rest of the brief |
| B4 | the same template without `## Blocked` | exact | exact |
| B5 | member result: `Built.`, then a fence holding `# Note`, a blank line, `## Result`, `text` | exact | **silent**: result becomes `text` plus the closing fence; a kept `Ticket proposals` section gets the real `## Result` heading, `Built.`, the opening fence and `# Note` |
| C1 | member result `Built.\n\n## Result\nmore` | refused | **silent**: result becomes `more`; kept `Ticket proposals` gets `\n## Result\nBuilt.` |
| C2 | member result `## Result\nBuilt.` | refused | exact |
| C3 | member outcome `Done.\n\n## Outcome\nmore` | refused | **silent**: result `R.` becomes `R.\n\n## Outcome\nDone.`; outcome becomes `more` |
| C4 | ticket outcome `Done.\n\n## Outcome\nmore` | refused | **silent**: outcome becomes `more`; kept `Proposals received` gets `\n## Outcome\nDone.` |
| C5 | ticket sizing `S.\n\n## Size, persona and model decision\nx` | refused | **silent**: brief gains `\n\n## Size, persona and model decision\nS.`; sizing becomes `x` |
| D1 | ticket brief `B.\n\n## Team\nx\n\n## Handoffs\ny` | refused | exact |
| D2 | ticket brief naming every later ticket section, in order, after blank lines | refused | exact |
| E1 | member result `Built.\n## Blocked\nnothing`; member not blocked | bytes differ; result and blocked moved | same as HEAD |
| E2 | member result `Built.\n\n## Blocked\nnothing` | silent: result `Built.`, blocked `nothing` | same as HEAD |
| E3 | member result `Built.\n\n## Sources\n- a link` | silent: result `Built.`, layout gains Sources, kept Sources | same as HEAD |
| E4 | blocked question `Which?\n\n## Sources\n- a link` | silent, likewise | same as HEAD |
| E5 | member brief `Do it.\n\n## Blocked\nif stuck` | silent: brief cut, blocked set | exact |
| F1 | Result empty, outcome `## Outcome\nDone.` | refused | exact |
| F2 | Result empty, outcome `Done.\n\n## Outcome\nmore` | refused | **silent**: result goes from NULL to `\n## Outcome\nDone.`; outcome becomes `more` |
| F3 | sizing empty, brief `B.\n\n## Size, persona and model decision\nx` | refused | exact |
| G1 | handoff `what` = `x\n\n## Outcome\ny` | refused | columns exact; the Handoffs section is kept as prose (see Out of scope) |
| G2 | handoff `what` = `x\n## Proposals received\ny` | refused | exact |

**The live data.** In the live database only three stored values hold a line starting `## `: the briefs of BADS-036 Marfona, Roseval and Sarpo. None of those headings is an owned section name, and none sits in a fence.

**The HEAD ledger.** The branch's import of the committed ledger matches the live database in every brief, sizing, outcome, result and blocked column: **0 differences across 134 tickets and 117 members**. It differs from HEAD's import only in those three briefs and their layouts, where HEAD kept 8 of their inner headings as sections.

## Findings, by severity

### 1. High: silent column moves where HEAD refused or was exact (C1, C3, C4, C5, F2, B3, B5)

`owned_headings` breaks ties between equally long heading chains by taking the one after a blank line, then the later one. That picks correctly when prose names the *next* section (Ozette's sizing case, A2, D1). It picks wrongly when prose *repeats its own section's name* after a blank line: the prose line wins, and the real heading plus the text above it fall into the previous section.

Dropping fence handling in owned mode adds a second route. A fenced note template can supply a longer chain than the real headings, through a section the note lacks (B3). A fenced block holding a blank line and the section's own name (B5) hits the tie rule. HEAD handled both, because it skipped fenced lines.

Why this is worse than before:

- **Every one of these re-renders byte for byte.** `test_the_rendered_notes_import_and_render_again_byte_for_byte` cannot see them. Neither can the new acceptance scan, `test_stored_prose_survives_a_second_round_trip`: it compares the first import with a second one, and both split the same wrong way.
- **The corpus canary is lost.** On HEAD, a stored row like C1 made the acceptance test fail at once ("a section repeats"), which is how BADS-036 was found. On the branch the same row passes every test, while a disaster-recovery import would store the wrong columns.
- **CLI writes reach these today.** `member result`, `member finish --outcome`, `ticket edit --outcome` or `--sizing`, and `member new|edit --brief` store text as given. A result or brief documenting a note's format in a code block (B3, B5) is a likely thing for this project's own tickets to write.

The docstring's limit understates this. It says the two cases "read as headings", but not that the text lands in another column or a kept section, undetectably. It also says prose may hold any `## ` line "fenced or not", which B3 and B5 contradict.

**Smallest fix (the ticket's option (b), narrowed to owned names, alongside Ozette's (a)):**

1. **Refuse the line at write time.** When any stored-prose value holds a line `## <name>` whose name is a section of that note's kind, refuse with a clear message (for example: "a line `## Result` would read as the note's own section heading; write `### Result` or reword it"). This applies in fences too, since the split no longer sees fences.
   - Ticket prose checks against `TICKET_SECTIONS`.
   - Member prose checks against `IMPORT_MEMBER_SECTIONS`, which includes Blocked and Sources, so finding 2 is covered as well.
   - Check points: `cmd_ticket_new` (brief, sizing, outcome), `ticket edit` and `ticket move` wherever they set brief, sizing or outcome, `cmd_member_new` and `cmd_member_edit` (brief), `cmd_member_finish` (outcome), `cmd_member_own` (result, block), and the column updates in `accept_ticket_edit` and `accept_member_edit`, since `import --file` stores column text too.
   - One helper, about 10 lines, plus the calls. Ozette's `test_a_heading_added_inside_the_prose_is_an_edit_of_that_section` would then expect a refusal, or use names the ticket does not own.
2. **Add a canary to the acceptance scan.** In `RoundTripMixin`, assert that no stored prose value (column or kept section) holds a line `## <owned name>` for its note kind. This restores the canary HEAD had for the committed corpus, whatever wrote the row. Dakota's kept `Sources` body should be checked when this lands; the live scan found no owned-name line anywhere.
3. **Pin the cases in tests.** Add C1 (or F2) and B5 as refusals at write time, and B3 as a refusal of the fenced template line.
4. **Correct the docstring.** Text the CLI refuses cannot reach the ambiguity; hand-written text can, and there it moves between sections.

With 1 in place, the tie rule only matters for hand-written and legacy text. The live scan shows no existing row the guard would refuse, so there is nothing to migrate and nothing to rewrite.

### 2. Medium: section names a member note lacks (E1 to E4) still move text

A member Result or Blocked question holding `## Blocked` (member not blocked) or `## Sources` opens that section on import. Case E1 has no blank line before the heading, so its re-render differs and is visible. Cases E2 to E4 are silent. This is unchanged from HEAD, so it is not a regression. But `## Sources` at the end of a researcher's Result is a plausible line, and `IMPORT_MEMBER_SECTIONS` makes every member note own Sources at import. The member name set in fix 1 covers it. `accept_file` uses `MEMBER_SECTIONS`, which includes Blocked, but it splits the baseline and the edited file alike, so a hand edit there ends in a visible refusal ("the member's own"), not a silent change.

### 3. Low: the new tests pin the fixed cases, not the limit

`tests/test_heading_round_trip.py` and the two `test_markdown` tests cover owned names *before* their real heading (the fixed class) and hand-edit detection. Nothing pins the documented limit, the fenced cases, or the absent-section cases. The second-import scan in `test_acceptance.py` compares one import with another, so it cannot detect a split that both imports get wrong. Missing: C1 or F2, B3, B5, and E3. The table above gives the stored values. The comparison that actually shows a disaster-recovery import is exact is the one run here: imported columns against the source rows.

## Items 3 to 5 of the brief, confirmed

- **Callers.** `split_document(` has five call sites.
  - `import_ticket_file` passes `TICKET_SECTIONS`, and `import_member_file` passes `IMPORT_MEMBER_SECTIONS`. Both are right, and the only layout override in the live database is SPUD-006/Dakota's, which has Sources where `IMPORT_MEMBER_SECTIONS` puts it.
  - `bulk_import`'s member ordering at line 2691 passes none and reads only the frontmatter, which is right.
  - `accept_file` passes the stored layout's sections or the defaults, for tickets and for members; see finding 2.
- **Hand-edit detection.**
  - Render's exit 6 compares hashes, and `restyled_render` compares the parsed frontmatter plus the body bytes. Neither calls `split_document`, so both behave as before: a real edit is a conflict, and an Obsidian restyle passes. The existing `style_only` tests live in `test_render.py`, `test_team_card.py` and `test_cost.py`.
  - `check_section_layout` compares the baseline and the edited file split with the same `owned`. Ozette's test shows a deleted real heading is still refused.
- **Scope.**
  - The diff touches no file under `ledger/` and no BADS-036 row. `KNOWN_PROSE_KINDS` and every existing acceptance assertion are unchanged: the test_acceptance diff only adds.
  - The one exclusion, member summaries in the new scan, is justified. On the pinned corpus (2f11506), 7 of 9 members have a NULL summary after the first import and a summary derived from the rendered Team card after the second. No other member column differs between those two imports.
  - The change is local to the split and its callers.
- **Tests on each code.**
  - On HEAD's code, `test_heading_round_trip.py` fails 4 of 4 and the two new `test_markdown` tests raise `TypeError`. A third failure in that run, `HomeResolutionTest`, comes from the scratch copy not being a git checkout.
  - On the branch, `test_heading_round_trip.py` passes 4 of 4 and `test_markdown.py` passes 18 of 18.
  - The acceptance file was not rerun here, since Spud runs the full suite.

## Out of scope (filed as proposal 78, P3)

A handoff whose `what` holds a blank line (G1) renders as lines the handoff parser cannot read back. The import keeps the whole Handoffs section as prose, and the handoff rows come back split. That is not about headings, so it is filed as a proposal rather than a finding here.
