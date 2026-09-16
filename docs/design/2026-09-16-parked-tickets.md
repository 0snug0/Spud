# Parked tickets

Ticket [[SPD-096]]. Written by Osprey (01, architect, fable) on 2026-09-16 for Spud and for the engineer who builds it. It settles how a ticket that is important but deliberately not-now leaves the queue without lying about its priority, and what the board does with it.

Every claim below is one of:

- **[code]** read in the named file at `d4ca139` (main on 2026-09-16), with its line numbers.
- **[probe]** run on 2026-09-16 against the real `DDL_0001`, `DDL_0002` and `VIEWS_AND_TRIGGERS` of `bin/spudlib/state/schema.py` in an in-memory SQLite 3.53.4 (Python 3.14, `-I -S`), by a scratch script that wrote nothing; the four settings it tried are in §4.1.

## The decision

Parking is a **fifth ticket status, `parked`**, not a tag: `queued | active | parked | done | declined`, migration `0003_parked`. A ticket is parked by `spud --as spud ticket move <key> --status parked --reason "<why>" [--until YYYY-MM-DD]` and leaves that state only by `ticket move` to `queued`, `active` or `declined`; the reason is required and stored in a new column `parked_reason`, the date is optional and stored in `parked_until`, and two CHECK constraints make it impossible for the database to hold a parked ticket without a reason or a reason without a parked ticket. **`waiting` and `deferred` are one status, not two:** the only operational difference between them is that a wait can end on its own, and a date is the one thing the board can act on, so a waiting ticket is a parked ticket with `--until`, and a deferred one is a parked ticket without it. **The board sorts parked tickets after queued** (`v_board`), **`spud board --brief` and the `SessionStart` injection leave them out** except for one trailing count line and one full line for each parked ticket whose `until` has arrived, and **`spud board --parked` lists them** with their reason and date. Obsidian gets a `Parked` view in `Board.base`, and its `Board` and `By project` views exclude the status. No note that is not parked changes a byte.

## 1. Why a status, not a tag

The tag was set on 2026-09-16 and nothing read it. That is not an accident of this week; it is what a tag is in this ledger. What goes wrong under each, six months out:

**Under a tag.**

- `ticket edit --tag` replaces the tag list whole [code: `bin/spudlib/commands/ticketcmds.py:95-98`]. The first `ticket edit BAD-041 --tag split` that forgets to retype `deferred` unparks the ticket silently, with an event that says `edited: tags` and no report entry, because `ticket edit` writes one only when the priority changes [code: `ticketcmds.py:101-102, 112-114`]. The day's report never says a ticket was parked or unparked.
- Nothing validates a tag. `defered` parks nothing, and the board is the only thing that would tell you, by nagging again.
- Two axes. `active` plus `deferred` is representable and nothing refuses it, so a board that hides tagged tickets hides one with a live team; and `member new` plans a member on a tagged ticket without a word [code: `bin/spudlib/state/ops.py:176` looks at status alone].
- Every reader must parse JSON. `tags` is a JSON text column; the board is a view [code: `schema.py:185-194`] and three Python callers read it [code: `commands/views.py:41`, `projects/sessions.py:23, 179`]; each needs a `json_each(tags)` clause, `Board.base` needs `!tags.contains("deferred")` on two views in a second syntax, and the next surface (SPD-048's capped injection, a digest, `card`) forgets, which is this week's rot repeated with more code to blame.

**Under a status.**

- One migration that rebuilds `tickets`, because SQLite cannot alter a CHECK (the recipe `0002_projects` already used for `events` [code: `schema.py:219-265`]), with the two wrinkles §4.1 names and the probe settled: foreign keys must be off before `BEGIN`, and the two views that name `tickets` must be dropped before the rename. One-time, tested, backed up by `apply_migrations` before it runs [code: `bin/spudlib/state/ledgerdb.py:56`].
- Every place that enumerates ticket statuses learns one more. The grep is the list, and it is in §5; none of them is a surprise, and after this ticket `TICKET_STATUSES` is the only list left to keep.
- The state machine gets stricter in two places that are the right friction: an active ticket with a live member cannot be parked until the member is finished, and `member new` on a parked ticket is refused with the status in the message, as it is today for `done`.
- The frontmatter of a parked note gains two properties. No other note changes.

**Not a column beside `status`.** `ALTER TABLE tickets ADD COLUMN parked_reason` with `status` staying `queued` would avoid the rebuild, and it was considered. It keeps the lie the ticket is about (`status: queued` on a ticket that is not in the queue), and every status reader then needs `AND parked_reason IS NULL` beside it, which is the tag's problem with a schema. The rebuild is the cheaper thing to be wrong about.

## 2. Why one status with a date, not `waiting`

BAD-021 waits on App Store approval; BAD-041 waits on Eric. The first can end without anyone in this ledger doing anything; the second cannot. A second status would record that difference and act on none of it, because nothing in the ledger watches App Store Connect. What the ledger can do is remember a date and say so: `--until 2026-10-16` means "from this date, put the ticket back in front of me". The brief board then prints the ticket as due back, once, at the top of the backlog, until Spud moves it (`queued` or `active` if the wait is over, `declined` if it is moot, or `parked` again with a later date: `parked -> parked` is a legal renewal, §4.2). Nothing unparks a ticket on its own; a status move is a command with an actor, as every change here is.

So: one status, a required reason, an optional date. The words `deferred` and `waiting` stop being ledger vocabulary; the six tickets lose the tags in §7.

## 3. What the board prints

Today `spud board` prints every ticket, done and declined included, 187 rows, active first then queued by priority [code: `views.py:38-56`, `schema.py:193`]; `spud board --brief` prints the 52 open lines (5,607 bytes) that `SessionStart` injects whole in the home [code: `hooks/sessionhooks.py:25`] and under a 2 KB cap in a project [code: `projects/sessions.py:146`]. The four deferred P1 splits are lines 8 to 11 of the brief.

### 3.1 `spud board`

Unchanged columns. Parked rows sort after every queued row and before done, by priority inside the bucket, because `v_board`'s CASE gains a step (§4.1). The `status` cell reads `parked`.

```
ticket   project   status    P   title                                              lead     origin    proposed by    created
SPD-096  spud      active    P1  Make deferred and waiting a real bucket on the …   Osprey   eric                     2026-09-16
BAD-036  badtakes  active    P1  Refactor every application code file over 1000 …  Estima   eric                     2026-09-15
…
SPD-002  spud      queued    P3  Add an index page for docs/toy/                             eric                     2026-09-12
BAD-048  badtakes  parked    P1  Split scripts/build-release-notes-art.js (1547 …           eric                     2026-09-15
BAD-043  badtakes  parked    P1  Split renderer/creator.js (3044 lines) into ren…           eric                     2026-09-15
BAD-042  badtakes  parked    P1  Split main.js (6724 lines) into a main/ directo…           eric                     2026-09-15
BAD-041  badtakes  parked    P1  Split renderer/app.js (8991 lines) into rendere…           eric                     2026-09-15
BAD-040  badtakes  parked    P2  Guard release-note art bytes against art.lock.j…           eric                     2026-09-15
BAD-021  badtakes  parked    P2  Publish the reconciled App Privacy label to App…           eric                     2026-09-14
BAD-005  badtakes  done      P0  …
```

### 3.2 `spud board --brief`, and what `SessionStart` injects

The brief prints what is being worked or needs a decision, then the backlog: active tickets with their live members (as today), then each parked ticket whose `until` is today or earlier, then queued by priority, then one count line for the parked tickets. A parked ticket that is not due is the count line and nothing else. With no parked ticket the output is byte-identical to today's, so nothing that asserts today's brief moves.

Today, once the six are parked (six lines gone, one added):

```
SPD-096 active P1 Make deferred and waiting a real bucket on the board, not just a tag (lead Osprey)
  Osprey (01, architect, fable) active
BAD-036 active P1 Refactor every application code file over 1000 lines into smaller modules: Phase 1 extraction plans, Phase 2 implementation (lead Estima)
BAD-080 active P2 collab clients: keep the seat hot through a take's push so the server grace is the belt, not the path (lead Ratte)
  Ranger (03, engineer, opus) active
BAD-055 active P2 Browser preview from a BadTakes worktree session serves the main checkout (lead Umatilla)
  Umatilla (01, writer, sonnet) active
BAD-089 queued P2 Move the collab delivery rule into src/collab.js instead of writing it twice
SPD-093 queued P2 A member's format-patch, bugreport and diagnose write a file into the working directory no rule sees
…
SPD-002 queued P3 Add an index page for docs/toy/
6 parked (spud board --parked)
```

On 2026-10-16, if BAD-021 was parked `--until 2026-10-16`:

```
BAD-055 active P2 Browser preview from a BadTakes worktree session serves the main checkout (lead Umatilla)
  Umatilla (01, writer, sonnet) active
BAD-021 parked P2 Publish the reconciled App Privacy label to App Store Connect after iOS 1.0 is approved (due back 2026-10-16: App Store approval of iOS 1.0)
BAD-089 queued P2 Move the collab delivery rule into src/collab.js instead of writing it twice
…
SPD-002 queued P3 Add an index page for docs/toy/
6 parked, 1 due back (spud board --parked)
```

The injection is this text under the header the hook already writes (`Ledger board (\`spud board --brief\` at …, source …):`), in the home whole and in a project through `fit_bytes` [code: `sessions.py:107-123`], which keeps whole lines from the top. That is why a due-back line sits above the queued block and not at the bottom: SPD-048 will cap the home's injection the same way, and the one line meant to nag must survive the cut. The count line is last and may be cut; the cut note already says to run `spud board --brief`. The claim card (`sessions.py:166-180`) and a project session's context (`:136-146`) call the same function with the project's rows and get the same shape, counted per project.

A parked line's suffix is `(due back <until>: <reason>)` when due, `(until <until>: <reason>)` when not yet due (only under `--parked`), `(<reason>)` with no date; a lead, when there is one, goes first inside the same parentheses: `(lead Ratte; due back 2026-10-16: …)`.

### 3.3 `spud board --parked`

Parked tickets only, in `v_board` order, with the two columns the default table does not carry. `--parked --brief` prints the one-line form of §3.2 for each, every parked ticket, due or not.

```
ticket   project   P   title                                                                                     until       reason                                                                        lead  created
BAD-048  badtakes  P1  Split scripts/build-release-notes-art.js (1547 lines) into an entry plus per-illustra…              By choice, 2026-09-16: the remaining BAD-036 splits wait for Eric's go               2026-09-15
BAD-043  badtakes  P1  Split renderer/creator.js (3044 lines) into renderer/creator/ classic scripts                       By choice, 2026-09-16: the remaining BAD-036 splits wait for Eric's go               2026-09-15
BAD-042  badtakes  P1  Split main.js (6724 lines) into a main/ directory and Electron-free src/ modules                    By choice, 2026-09-16: the remaining BAD-036 splits wait for Eric's go               2026-09-15
BAD-041  badtakes  P1  Split renderer/app.js (8991 lines) into renderer/app/ classic scripts                               By choice, 2026-09-16: the remaining BAD-036 splits wait for Eric's go               2026-09-15
BAD-040  badtakes  P2  Guard release-note art bytes against art.lock.json in npm test                                      By choice, 2026-09-16; no outside event brings it back                               2026-09-15
BAD-021  badtakes  P2  Publish the reconciled App Privacy label to App Store Connect after iOS 1.0 is approved  2026-10-16  App Store approval of iOS 1.0                                                        2026-09-14
```

### 3.4 `spud board --help`

The parser's help strings [code: `bin/spudlib/cli/cliparser.py:301-303`] become:

```
usage: spud board [-h] [--brief] [--parked] [--project PROJECT]

the board (v_board): every ticket, active first, then queued, parked, done and declined, by priority inside each

options:
  -h, --help         show this help message and exit
  --brief            open tickets and live members, one line each, as SessionStart injects it: active tickets with their
                     live members, then parked tickets that are due back, then queued, then one count line for the parked
  --parked           parked tickets only, with why and until (with --brief, one line each); without it the board sorts
                     them after queued and --brief counts them on its last line
  --project PROJECT  only this project's tickets
```

(`add_parser("board", help=…, description=…)` so the one-line summary shows in both `spud --help` and `spud board --help`, as `report add` does at `cliparser.py:296`.)

## 4. The mechanism

### 4.1 Schema: migration `0003_parked`

`bin/spudlib/state/schema.py` gains `DDL_0003`, `MIGRATIONS` gains `("0003_parked", DDL_0003)` [code: `:267`], `SCHEMA_VERSION` follows [code: `:268`]. SQLite cannot alter the CHECK at `:28`, so `tickets` is rebuilt the way `0002_projects` rebuilt `events` [code: `:237-264`]:

```sql
-- Parked tickets (SPD-096, docs/design/2026-09-16-parked-tickets.md section 4.1): a fifth status and the two columns
-- that qualify it.  SQLite cannot alter a CHECK, so tickets is rebuilt; the two views that name it are dropped first
-- (the rename re-parses every view, and one naming a missing table fails it), and VIEWS_AND_TRIGGERS re-creates them.
-- apply_migrations turns foreign keys off around the transaction: with them on, DROP TABLE tickets is refused because
-- members, events, handoffs, proposals and proposal_decisions point at it.
DROP VIEW IF EXISTS v_board;
DROP VIEW IF EXISTS v_fleet;
CREATE TABLE tickets_new (
  id          INTEGER PRIMARY KEY,
  project_id  INTEGER NOT NULL REFERENCES projects(id),
  number      INTEGER NOT NULL,
  key         TEXT    NOT NULL UNIQUE,
  team_key    TEXT    NOT NULL UNIQUE,
  title       TEXT    NOT NULL,
  heading     TEXT,
  priority    TEXT    NOT NULL CHECK (priority IN ('P0','P1','P2','P3')),
  status      TEXT    NOT NULL CHECK (status IN ('queued','active','parked','done','declined')),
  origin      TEXT    NOT NULL CHECK (origin IN ('eric','proposal')),
  proposal_id INTEGER REFERENCES proposals(id),
  lead_id     INTEGER REFERENCES members(id),
  brief       TEXT    NOT NULL DEFAULT '',
  sizing      TEXT    NOT NULL DEFAULT '',
  outcome     TEXT    NOT NULL DEFAULT '',
  tags        TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(tags)),
  layout      TEXT    CHECK (layout IS NULL OR json_valid(layout)),
  created_at  TEXT    NOT NULL,
  updated_at  TEXT    NOT NULL,
  closed_at   TEXT,
  parked_until  TEXT  CHECK (parked_until IS NULL OR (status = 'parked' AND parked_until GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')),
  parked_reason TEXT  CHECK ((parked_reason IS NOT NULL) = (status = 'parked')),
  UNIQUE (project_id, number)
) STRICT;
INSERT INTO tickets_new (id, project_id, number, key, team_key, title, heading, priority, status, origin, proposal_id, lead_id,
                         brief, sizing, outcome, tags, layout, created_at, updated_at, closed_at)
  SELECT id, project_id, number, key, team_key, title, heading, priority, status, origin, proposal_id, lead_id,
         brief, sizing, outcome, tags, layout, created_at, updated_at, closed_at FROM tickets;
DROP TABLE tickets;
ALTER TABLE tickets_new RENAME TO tickets;
CREATE INDEX tickets_board ON tickets(status, priority);
```

The column comments of `DDL_0001` are kept on the copied columns; the two new ones read `-- YYYY-MM-DD from which the brief board shows the ticket as due back; NULL = until moved` and `-- why it is parked; NULL unless parked`.

`VIEWS_AND_TRIGGERS` [code: `:185-194`] changes in two places: the SELECT list carries the two columns, `t.status, t.parked_until, t.parked_reason, t.priority, …`, and the ORDER BY becomes

```sql
 ORDER BY CASE t.status WHEN 'active' THEN 1 WHEN 'queued' THEN 2 WHEN 'parked' THEN 3 WHEN 'done' THEN 4 ELSE 5 END,
          t.priority, t.id DESC;
```

`v_fleet` and `v_live` are unchanged in text; `v_fleet` is dropped and re-created because it names `tickets`.

**What the probe settled** [probe], the same DDL in four settings, each in `BEGIN IMMEDIATE … COMMIT` as `apply_migrations` runs it, with a member, an event, a handoff and a proposal pointing at the tickets:

| Setting | Result |
|---|---|
| foreign keys on inside the transaction, views kept | `FOREIGN KEY constraint failed` at `DROP TABLE tickets` |
| foreign keys on, views dropped first | the same |
| `PRAGMA foreign_keys = OFF` before `BEGIN`, views kept | `error in view v_board: no such table: main.tickets` at the rename |
| foreign keys off before `BEGIN`, views dropped first | ok: 0 rows from `PRAGMA foreign_key_check`, ids and rows kept, `members`' foreign key still names `tickets`, `tickets_board` present, the events triggers still refuse a delete, a foreign key still refused after `= ON` |

The same probe confirmed every CHECK: parked without a reason, a reason while queued, an `until` while queued, an `until` not shaped `YYYY-MM-DD`, an unpark that keeps its reason, and a sixth status are each refused by the database; parked with a reason and no date, and an unpark that clears both, are accepted. And `CREATE VIEW` accepts a column that does not exist yet (the SELECT fails later), so `apply_migrations` may keep running the newest `VIEWS_AND_TRIGGERS` after each migration on a fresh `init` [probe].

So `apply_migrations` [code: `ledgerdb.py:46-63`] follows SQLite's own table-rebuild recipe (lang_altertable.html, section 7): `PRAGMA foreign_keys = OFF` before `with write_txn(con)` (the pragma is a no-op inside a transaction), `PRAGMA foreign_key_check` inside it after the two `executescript`s, raising `kernel.SpudError(kernel.EXIT_ERROR, "migration %s broke %d foreign key reference(s); rolled back")` when it returns rows so `write_txn` rolls back, and `PRAGMA foreign_keys = ON` in a `finally`. That applies to every migration and changes nothing for `0001` and `0002`.

### 4.2 State machine and `ticket move`

`bin/spudlib/core/kernel.py`:

- `:22` `TICKET_STATUSES = ("queued", "active", "parked", "done", "declined")`.
- `:29-34` `TICKET_TRANSITIONS` gains `("queued", "parked")`, `("active", "parked")`, `("parked", "queued")`, `("parked", "active")`, `("parked", "declined")` and `("parked", "parked")`. The last is a renewal: a new reason or date, with its event and report entry. Not `("parked", "done")`: nothing was done while parked; a wait that ends goes through `active`.
- `:59` `TICKET_FM_KEYS` gains `"parked_until", "parked_reason"` right after `"status"`, and a new `PARKED_FM_KEYS = ("parked_until", "parked_reason")` beside it names the two the render emits only while parked (§4.3).

`bin/spudlib/commands/ticketcmds.py`:

- `:16` `TICKET_MOVE_VERBS["parked"] = "parked"`, so the report entry is titled `BAD-021 parked: <title>`.
- `cmd_ticket_move` [code: `:55-75`], in order, inside the transaction after `check_transition` at `:64`:
  1. `--status parked` without `--reason`: `kernel.EXIT_USAGE`, "`--status parked` needs `--reason`: why it is parked (and `--until YYYY-MM-DD` when an outside event should bring it back)". `--until` with any other status: `EXIT_USAGE`, "`--until` goes with `--status parked`". Both checked before anything is written, beside `reportentry.check_next` at `:60`.
  2. `--status parked` while a member of the ticket is `planned` or `active`: `EXIT_ERROR`, "`BAD-036` has 2 members alive (Estima, Ranger); finish or fail them before parking it". The brief board shows live members only under active tickets [code: `sessions.py:30`], so a parked ticket must have none.
  3. The `UPDATE` at `:66` sets `parked_until` and `parked_reason` to `--until` and `--reason` when the new status is `parked` and to `NULL` otherwise, in the same statement as `status`, because the CHECKs of §4.1 read the row as one. `closed_at` stays as it is (`parked` is open).
  4. The `ticket.status` event body at `:67` reads `BAD-021 queued -> parked until 2026-10-16: App Store approval of iOS 1.0` (`until <date>` only when given; the `: reason` tail as today); its `data` gains `"until"` and `"reason"` when parking.
  5. The report entry at `:69-70` passes `lines=["parked until 2026-10-16: App Store approval of iOS 1.0"]` (or `"parked: <reason>"`) when parking, so the day's report says why; the Next line follows as today.
  6. The result text at `:75` reads `BAD-021 is now parked until 2026-10-16: App Store approval of iOS 1.0`, `BAD-041 is now parked: <reason>`, or `BAD-041 is now queued` as today.
- `format_ticket` [code: `:122-130`] adds a line after `origin: … created: …` while parked: `parked until 2026-10-16: App Store approval of iOS 1.0`, or `parked: <reason>`.
- `--reason` on a move to any other status keeps today's meaning: a note in the event body, stored nowhere else.

`bin/spudlib/cli/cliparser.py`:

- `:185` `--status` choices follow `TICKET_STATUSES` (no edit). `:186` `--reason` help: "why; required for --status parked, where it is stored and shown wherever the ticket is". New `--until` with `type=date_arg`, `metavar="YYYY-MM-DD"`, help "with --status parked: the date from which `spud board --brief` shows the ticket as due back; without it the ticket stays parked until moved". `:187` `--next` help names `started|done|queued|parked|declined`.
- `date_arg` beside `text_arg` [code: `:25-34`]: `datetime.date.fromisoformat(value)` and `value == d.isoformat()`, else `lazy.argparse.ArgumentTypeError("--until is YYYY-MM-DD, not %r")`; a `from datetime import date` at the top (a standard-library import restated per module; `cli/` is off the hook path). A past date is accepted: the ticket is due back at once.
- `:174` `ticket new --status` keeps `queued|active`. A ticket born parked is `ticket new` then `ticket move`; two commands, two events, and no third place that takes a reason.
- `:301-304` `board`: `--parked` (`store_true`) and the help of §3.4.

`bin/spudlib/state/ops.py`:

- `insert_ticket` [code: `:41-58`] takes `parked_until=None, parked_reason=None` and writes them, for the importer (§4.4).
- `:176` is unchanged: `member new` on a parked ticket is refused with "`BAD-041` is parked; no member can be planned on it", which is the message wanted.
- `:219` unchanged: a proposal becomes a queued ticket.

`bin/spudlib/state/lookup.py`: `ticket_dict` [code: `:74-96`] exposes `parked_until` and `parked_reason`, so `--json` carries them everywhere a ticket is printed.

`bin/spudlib/projects/install.py:326`: `status IN ('queued','active','parked')`, and the message at `:328` reads "move them to done or declined first" as today. A parked ticket is open, and `project remove` waits for it.

`bin/spudlib/hooks/sessionhooks.py:59` is unchanged on purpose: a prompt that names a parked ticket claims the session, as a done one does; Spud's ritual then sees the status and moves it.

### 4.3 Rendered notes

`render_ticket` [code: `bin/spudlib/render/notefiles.py:11-33`] emits `parked_until` (kind `stamp`: the date, or `""`) and `parked_reason` (kind `quoted`) after `status`, **only while the ticket is parked**: the key list drops `PARKED_FM_KEYS` otherwise, and inserts them after `status` when a stored `layout.fm_keys` (three imported tickets, all done) lacks them, as `:14-15` does for `project`. Conditional keys are the house style: a member note carries `agent_type` only for a contractor and `## Blocked` only when blocked [code: `notefiles.py:42-43, 57-58`].

BAD-021, parked `--until 2026-10-16`, after the tag is dropped (§7):

```yaml
---
id: BAD-021
title: "Publish the reconciled App Privacy label to App Store Connect after iOS 1.0 is approved"
priority: P2
status: parked
parked_until: 2026-10-16
parked_reason: "App Store approval of iOS 1.0"
origin: eric
project: badtakes
proposed_by: ""
lead: ""
created: 2026-09-14
tags: [ticket]
---
```

BAD-041, parked with no date: `status: parked`, `parked_until: ""`, `parked_reason: "By choice, 2026-09-16: the remaining BAD-036 splits wait for Eric's go"`, `tags: [ticket]`.

**Bytes.** The migration and the render after it change no note: every ticket keeps its status, and a note that is not parked has no new key. The six notes of §7 change when Spud moves them (the `status` line, two new lines, the `tags` line), and that day's report gains their entries. Nothing under `ledger/teams/` changes.

`bin/spudlib/imports/accept.py` [code: `:74-138`]: a hand edit of `status` to or from `parked`, and any edit of `parked_until` or `parked_reason`, is refused (`refuse(rel, …)`) with "parked is set and cleared by `spud ticket move --status parked --reason …` and `ticket move --status queued|active|declined`; a hand edit cannot carry the reason", beside the refusals at `:80-88`. Every other hand edit is as today; `closed_at` at `:126` stays `NULL` for `parked` by the existing expression.

### 4.4 Import

`import_ticket_file` [code: `bin/spudlib/imports/noteimport.py:61-100`] passes `fm.get("parked_until") or None` and `fm.get("parked_reason") or None` to `insert_ticket`; `require_keys` at `:64` does not grow (an export from before this ticket has neither key), and the validation at `:76` follows `TICKET_STATUSES`. The CHECKs of §4.1 refuse an export that says `status: parked` with no reason, which is the right refusal.

### 4.5 `Board.base`

Spud's edit, under CLAUDE.md's rule that a `.base` view is added only when a ticket specifies it; this ticket does. Four changes to `ledger/Board.base` [code: as it stands at `d4ca139`], in file order.

1. Line 2, the formula:

```yaml
formulas:
  status_rank: if(status == "active", 1, if(status == "queued", 2, if(status == "parked", 3, if(status == "done", 4, 5))))
```

2. The `properties:` block, after `priority:` (line 11):

```yaml
  parked_until:
    displayName: Until
  parked_reason:
    displayName: Why
```

3. The `Board` view's filters (lines 27-32) gain one line, and the `By project` view's status filter (line 66) gains a third name:

```yaml
    filters:
      and:
        - not:
            - file.inFolder("ledger/_templates")
        - file.inFolder("ledger/tickets")
        - status != "done"
        - status != "parked"
```

```yaml
        - '!status.containsAny("done", "declined", "parked")'
```

4. A `Parked` view, pasted between the `By project` view's `columnSize` block (ends line 95) and the `Reports` view (line 96):

```yaml
  - type: table
    name: Parked
    filters:
      and:
        - not:
            - file.inFolder("ledger/_templates")
        - file.inFolder("ledger/tickets")
        - status == "parked"
    groupBy:
      property: project
      direction: ASC
    order:
      - file.name
      - project
      - priority
      - title
      - parked_until
      - parked_reason
      - lead
      - created
    sort:
      - property: priority
        direction: ASC
      - property: parked_until
        direction: ASC
      - property: file.name
        direction: ASC
    columnSize:
      note.title: 420
      note.parked_reason: 360
      note.lead: 140
```

`Closed` (`status == "done"`) and `Fleet.base` are untouched. The `sort`, `order` and `columnSize` above are a starting point; they are Eric's display preference from the moment the view exists.

## 5. Implementation checklist

In this order; each step's tests go red before its code. No new module, no new import on the hook path, and no import inside a function (skill `spudlib-modules`, §1 and §3). The engineer works in the ticket's worktree with scratch homes (`tests/helpers.py`'s `Home`); the branch's `bin/spud` refuses the real ledger as "behind" until the merge, and `migrate` is Spud-only for the Bash hook [code: `bin/spudlib/hooks/hookio.py:38`], so the real database cannot be migrated from the branch.

1. **Schema and migration.** `bin/spudlib/state/schema.py`: `DDL_0003` and the `MIGRATIONS` entry (§4.1); the `v_board` SELECT list and CASE. `bin/spudlib/state/ledgerdb.py:46-63`: the foreign-keys-off, `foreign_key_check`, foreign-keys-on shape around the transaction. Tests: a new `tests/test_migrate_parked.py` modelled on `tests/test_migrate_projects.py` (a v2 fixture built from `spud.DDL_0001`, `spud.DDL_0002` and the v2 `VIEWS_AND_TRIGGERS` copied verbatim from `schema.py:178-217` as it stands on main, `user_version = 2`, with a member, an event, a handoff and a proposal pointing at a ticket), asserting: the CLI refuses the v2 database until `migrate`; `applied == ["0003_parked"]` and a backup named `pre-0003_parked`; every row and id kept and `PRAGMA foreign_key_check` empty; a foreign key refused again after the migration (the pragma is back on); each CHECK of §4.1 refused at the database; `tickets_board` present; `v_board` order active, queued, parked, done, declined; the events triggers still refuse a delete; a fresh `init` applies all three. Version assertions move from 2 to 3: `tests/test_init.py:32`, `tests/test_backup.py:99, 202`, `tests/test_migrate_projects.py:86` (`applied` becomes both names and the version 3; `:88` keeps its regex, `backups` now has two entries) and `:173`, `tests/test_hooks.py:6861`.
2. **Constants.** `bin/spudlib/core/kernel.py:22, 29-34, 59` (§4.2). `tests/test_tickets.py::test_state_machine` (`:70`) gains every new transition, the refusal of `parked -> done`, and `queued -> parked` refused without `--reason` (exit 2).
3. **`ticket move`, `ticket show`, `insert_ticket`, `ticket_dict`.** `ticketcmds.py:16, 55-75, 122-130`; `ops.py:41-58`; `lookup.py:74-96`; `cliparser.py:25-34, 183-188`. Tests in `tests/test_tickets.py`: reason required; `--until` refused with another status and refused when not `YYYY-MM-DD`; a parked ticket's `--json` carries both fields and an unparked one `None` for both; a renewal `parked -> parked` writes its event; parking refused while a member is alive, allowed after `member finish`; `member new` on a parked ticket refused with "is parked". `tests/test_report_entries.py::test_ticket_move_writes_an_entry_named_by_the_new_status` (`:115`) gains a `parked` case whose body is the reason line.
4. **The board and the brief.** `bin/spudlib/projects/sessions.py:20-36` (the order of §3.2, the suffixes, the count line; `today = kernel.now()[:10]`, compared as ISO text, the way `brief_state` already does at `:15`); `bin/spudlib/commands/views.py:38-56` (`--parked`, the two extra columns); `cliparser.py:301-304`. Tests: `tests/test_tickets.py::test_board_lists_tickets_from_v_board` (`:129`) gains a parked ticket sorted after queued in the table and absent from the brief except the count line; a due-back case (`--until` a past date) printed after the active block and before queued, with `1 due back` in the count line; `--parked` and `--parked --brief`; `tests/test_hooks.py::test_board_brief_is_injected` (`:6304`) gains one assertion that the injected context has no line for a parked ticket that is not due and has the count line; `tests/test_sessions.py::test_the_card_stays_within_its_cap_with_a_long_board` (`:53`) is a place to check the count is per project.
5. **`project remove`.** `bin/spudlib/projects/install.py:326`. `tests/test_projects.py::test_remove_deletes_an_empty_project_and_archives_one_with_tickets` (`:141`) gains "refused while a ticket is parked".
6. **Render, hand edits, import.** `notefiles.py:11-33`; `accept.py:74-138`; `noteimport.py:61-100` (§4.3, §4.4). Tests: `tests/test_render.py::test_ticket_file_shape` (`:24`) gains the parked frontmatter of §4.3 and asserts a queued ticket's note is byte-identical before and after parking-and-unparking another; `::test_refused_ticket_properties` (`:517`) and `::test_ticket_status_goes_through_the_state_machine` (`:472`) gain the parked refusals; `tests/test_import.py::test_round_trip_of_the_synthetic_tree` (`:88`) gains a parked ticket that round-trips with both fields, and an export saying `status: parked` with no reason refused by the CHECK.
7. **Verification** (skill §9): the whole suite in the background, `find bin tests -name '*.pyc' -o -name __pycache__` printing nothing, `tests/probes/hook_timing.py 30 /Users/ericlugo/Personal/Spud/bin/spud "$PWD/bin/spud"` within 1 ms of main on every hook case (`ledgerdb`, `kernel`, `ops`, `lookup` and `sessions` are on the hook path; `HOOK_PATH` in `tests/test_package.py` does not change because no import is added), and `tests/probes/module_sizes.py` for the record: `sessions.py` (277 lines) and `schema.py` (268) are already over the look-again point and each grows by a few lines for the reason this note writes down.

## 6. Spud's edits and the rollout

His files, not the engineer's, after the code is verified.

1. **Land and migrate in one Bash line from the main checkout:** `git merge --no-ff worktree-spd-096-<slug> && python3.14 -I -S bin/spud migrate`. The hook that guards the next Bash call runs main's `bin/spud` against the database; after the merge that pair is a v3 CLI on a v2 database, `ledgerdb.connect` refuses it [code: `ledgerdb.py:28-29`], `hook_pre_tool_use` opens the database before it reads the line [code: `pretool.py:218`], and `cmd_hook` fails closed in the home [code: `dispatch.py:52`]. The 0002 window was crossed the same afternoon in one report entry (2026-09-14 12:03) [code: `reports/2026-09-14.md:132`]. If the merge lands alone, `spud migrate` runs from a plain terminal, where no hook runs. Another home session open at that moment loses one Bash call to "the database is behind"; a project session fails open for Eric's own calls [code: `dispatch.py:48-51`].
2. `git push`; remove the worktree and its branch; `report add` the merge.
3. **The six tickets** (§7): six `ticket move` and six `ticket edit --tag`.
4. **`ledger/Board.base`** (§4.5). **`ledger/Home.md:21`**: "status (queued, active, done, declined)" gains `parked`. **`ledger/_templates/ticket.md`**: one HTML comment under the Brief comment at line 16, "while parked (`ticket move --status parked --reason … [--until YYYY-MM-DD]`) the frontmatter carries `parked_until` and `parked_reason` after `status`"; the frontmatter itself stays, as the member template omits `agent_type`. **`CLAUDE.md`**, Ledger v1: `status (\`queued|active|done|declined\`)` becomes `queued|active|parked|done|declined`, and one sentence after it: a ticket is parked with `ticket move --status parked --reason "…" [--until YYYY-MM-DD]`, the brief board shows it again from that date, `spud board --parked` lists them. The suite count in Commands.
5. **SPD-082** names migration `0003` and the six version assertions it moves; after this ticket it is `0004`, `SCHEMA_VERSION` 4, and the same assertions at 3. `ticket edit SPD-082 --brief`.
6. `spud render`, then `spud --as spud ledger commit --message "SPD-096: …"`.

**Rollback.** Before any ticket is parked: restore `.spud/backups/ledger-<stamp>-pre-0003_parked.db` with the previous CLI and re-render. After: forward fixes only, as for every migration.

## 7. The six tickets

After the migration, from the main checkout, the reasons Spud types are his; these are the proposal. Tags are replaced whole by `ticket edit --tag` [code: `ticketcmds.py:96`], so `--tag ticket` leaves `[ticket]`; none of the six carries another tag [code: `spud sql` on 2026-09-16].

| Ticket | Today | Command | Becomes |
|---|---|---|---|
| BAD-041 | queued P1, `[ticket, deferred]` | `ticket move BAD-041 --status parked --reason "By choice, 2026-09-16: the remaining BAD-036 splits wait for Eric's go"`, then `ticket edit BAD-041 --tag ticket` | parked P1, no date, `[ticket]`; off the brief, on `--parked`, in Obsidian's Parked view |
| BAD-042 | queued P1, `[ticket, deferred]` | the same | the same |
| BAD-043 | queued P1, `[ticket, deferred]` | the same | the same |
| BAD-048 | queued P1, `[ticket, deferred]` | the same | the same |
| BAD-040 | queued P2, `[ticket, deferred]` | `ticket move BAD-040 --status parked --reason "By choice, 2026-09-16; no outside event brings it back"`, then `--tag ticket` | parked P2, no date, `[ticket]` |
| BAD-021 | queued P2, `[ticket, waiting]` | `ticket move BAD-021 --status parked --reason "App Store approval of iOS 1.0" --until 2026-10-16`, then `--tag ticket` | parked P2 until 2026-10-16, `[ticket]`; on 2026-10-16 the brief prints it as due back until Spud moves it, or renews it with a later date |

The date on BAD-021 is a review date, not a promise: thirty days is a first guess for Spud to confirm with Eric, and a renewal is one command. The four splits keep P1 because the split is still wanted; that is the whole point of the status. BAD-036, their active parent (lead Estima), is not in the six and stays active; whether it should be parked too, once its members are finished, is Eric's call, and `ticket move --status parked` will refuse it while a member is alive.

Each move writes a `ticket.status` event and a report entry `BAD-041 parked: <title>` with its reason line; each tag edit writes `ticket.edited` and no entry. The tags are the only trace of the two words, and after this they are gone.

## 8. Risks accepted

1. **The rebuild of `tickets`.** The central table is copied once. Mitigated by the pre-migration backup `apply_migrations` writes, `foreign_key_check` inside the transaction (a bad copy rolls back and leaves the v2 database), explicit ids in the copy, and the migration test. The probe ran the exact DDL; the test runs it again on every suite run.
2. **`apply_migrations` turns foreign keys off around every migration**, not only this one. That is SQLite's documented recipe, it is verified by the check before commit, and it changes nothing observable for `0001` and `0002`.
3. **A merge-to-migrate window** during which every enforcing hook in the home fails closed. Seconds, when the merge and the migrate share a line (§6.1).
4. **The board's brief is no longer a pure filter of `v_board`:** it reorders due-back parked tickets above the queue. Deliberate, and stated in `--help`; the full `board` keeps the view's order.
5. **No automatic unpark.** A due-back ticket nags on the brief until a command moves it. That is the ledger's rule (every status change has an actor) and the nag is the feature.
6. **`parked -> parked` is a self-transition** in a state machine that has none today. It exists so a renewal is one evented command rather than a new edit field on `ticket edit`; `check_transition` needs no change, only the pair in the set.
7. **SPD-082's migration number moves.** One brief edit by Spud; the ticket is queued P3 and unstarted.
8. **Two properties appear only on parked notes.** Bases shows an empty cell elsewhere, which is what `agent_type` already does on the Fleet views. A note that is unparked loses the keys at its next render; that render is a CLI change, so the hand-edit detector is not involved.
9. **The reason is free text**, stored once, shown in four places (`board --parked`, `ticket show`, the note, the report). Nothing parses it; nothing should.
