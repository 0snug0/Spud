# BAD-036 spike: admin/src/lib/accounts.ts

Phase 1 extraction plan. Written against BadTakes `main` at `20d11e1`, 2026-09-15. Every line number below refers to that commit.

## 1. The file today

- **Path:** `admin/src/lib/accounts.ts`, 1706 lines.
- **Runtime:** server only. Line 1 is `import 'server-only'`. The module runs inside server components of the admin dashboard, a Next.js 16.3 app built with `next build --webpack` and deployed standalone to Cloud Run through Firebase App Hosting. Its queries go to Postgres (`LICENSING_DB_URL`) through `lib/db.ts`, whose pool is **three** connections behind the Supavisor transaction pooler.
- **Toolchain:** TypeScript 6.0.3 with typescript-eslint 8.68, held there by the pin memory records. The tsconfig sets `moduleResolution: bundler`, `isolatedModules: true`, `strict`, and the alias `@/*` → `admin/src/*`. ESLint runs `eslint-config-next/core-web-vitals` plus `/typescript`, with `@typescript-eslint/no-explicit-any: error`.
- **How it loads:** a plain ES module import. It carries no directive and is not a route file.
- **What it imports:**
  - `./db`: `db`, `inSeries`, `MONTH_START_SQL`, `type Sql`
  - `./shared`: `bothAllowances`, `planOf`, `sceneBucket`, and the types `Bucket`, `LicenseStatus`, `MonthUsage`, `Plan`, `QuotaVerdict`

  `shared.ts` is the seam to `server/supabase/functions/_shared/`. This file calls those rules and copies none of them.
- **Who imports it:** four modules, and three tests read it as text (§2). The grep is `grep -rn "@/lib/accounts\|from './accounts'" admin/src`.

## 2. Public surface

**Exports.** Twenty-nine, listed by the TypeScript compiler API (script in §8). This is the compatibility contract.

| Export | Kind | Line | Imported by |
|---|---|---|---|
| `ACTIVITY_LABELS` | const | 156 | `app/accounts/page.tsx:5` |
| `ActivityKind` | type | 153 | none |
| `VERSION_MATCH` | const | 412 | `lib/activity.ts:3` (`from './accounts'`) |
| `SortKey` | type | 506 | `app/accounts/page.tsx:5` |
| `SortDir` | type | 507 | none |
| `SORT_KEYS` | const | 509 | none |
| `isSortKey` | function | 511 | `app/accounts/page.tsx:5` |
| `PurchaseSummary` | interface | 517 | none |
| `LastActivity` | interface | 529 | none |
| `AccountRow` | interface | 534 | none |
| `AccountsQuery` | interface | 587 | none |
| `AccountsPage` | interface | 602 | none |
| `listAccounts` | async function | 731 | `app/accounts/page.tsx:5` |
| `accountFacets` | async function | 846 | `app/accounts/page.tsx:5` |
| `LicenseRow` | interface | 885 | none |
| `Location` | interface | 918 | none |
| `ActivationRow` | interface | 925 | none |
| `SceneSaveRow` | interface | 947 | none |
| `ShareRow` | interface | 961 | none |
| `RecordedTakeRow` | interface | 972 | none |
| `SceneRecordingRow` | interface | 981 | none |
| `SceneImportRow` | interface | 997 | none |
| `EventRow` | interface | 1009 | none |
| `DeletionPreview` | interface | 1025 | `components/AccountControls.tsx:6` (`import type`, from a `'use client'` file) |
| `AuthUser` | interface | 1055 | none |
| `MarketingConsent` | interface | 1071 | none |
| `SubscriptionSummary` | interface | 1084 | none |
| `AccountDetail` | interface | 1099 | `app/accounts/[email]/page.tsx:12` |
| `getAccount` | async function | 1360 | `app/accounts/[email]/page.tsx:12` |

"None" means no importer today. The name is still part of the contract, and the barrel keeps it.

**Text contracts.** These tests read the file by path. The repo-root `npm test` runs them; none needs a database.

- `test/server/admin-accounts-list.test.mjs:21` reads `lib/accounts.ts` and asserts:
  - `const ACCOUNTS_WHERE = \`` is a module-level template literal.
  - `body('export async function listAccounts(')` and `body('async function countAccounts(')` both contain `${ACCOUNTS_WHERE}`, and neither contains `a.account_email like`.
  - The `ACCOUNTS_WHERE` literal contains `a.account_email like`, `handle`, `lower(pr.handle::text)`, `pr.suspended_at is not null` and `'suspended'`, and never `a.status = 'suspended'`.
  - `countAccounts` contains `${PROFILE_JOIN}`, and the `PROFILE_JOIN` literal is `left join private.profiles pr on pr.email = a.account_email`.
  - `status?: LicenseStatus | 'suspended' | 'all'` appears, from `AccountsQuery`.
  - The `accountFacets` body has `suspended: number`, the `count(*) filter (where pr.suspended_at is not null) as suspended` SQL, `${PROFILE_JOIN}`, `suspended: int(r.suspended)`, and exactly one `sql.unsafe(`.
  - The `listAccounts` body has `const search = ….trim().replace(/^@/, '').toLowerCase();`.
  - `profileSuspendedAt: string | null` appears (from `AccountRow`), as does `profileSuspendedAt: iso(row.profile_suspended_at)` (from `accountFrom`).
  - The `PROFILE_COLUMNS` literal has `pr.suspended_at as profile_suspended_at`.

  Its `body()` helper ends a function at the next `\nexport async function ` or `\nasync function `.
- `test/server/admin-moderation.test.mjs`:
  - Lines 120-123 assert `profileHandle:` (from `DeletionPreview`) and `from private\.profiles where email =` (from `deletionPreview`).
  - Lines 145-155 take a 2000-character slice starting at the **first** occurrence of `ACCOUNTS_CTE`. They assert an unqualified `status <> 'deleted'` inside it and no `l.status <> 'deleted'`. Today the predicate sits 1601 characters in.
- `test/server/admin-version-key.test.mjs:19-27` reads `admin/src/lib/accounts.ts` for `const VERSION_MATCH = String.raw\`…\`;` and pins it against `parseVersion` in `_shared/clientversion.mjs`.
- `test/server/admin-pool.test.mjs` scans every `.ts`/`.tsx` under `admin/src` for `Promise.all`, `allSettled`, `race`, `any` and `.map(async`. `getAccount` passes because it uses `inSeries`, and new files are scanned automatically.
- `test/server/admin-client-boundary.test.mjs` resolves `@/…` specifiers through `.ts`, `.tsx`, `/index.ts` and `/index.tsx`, and fails any server module that imports a non-component value from a `'use client'` file. This module imports no client file.

**Next.js conventions:** none. The file is not a route and has no directive; `server-only` is its only guard.

**Prose that names the file.** These are not contracts, but they go stale:
- `server/CLAUDE.md:531` (`locationsFor` in `admin/src/lib/accounts.ts`)
- `admin/README.md:118`
- the comment at `lib/actions.ts:281`
- the comment at `app/accounts/[email]/page.tsx:201`
- `server/supabase/migrations/0036_backfill_signup_location.sql:45,104`, an applied migration that is never edited
- `docs/superpowers/plans/*`, which are historical

## 3. Responsibility map

Ranges include each declaration's leading comment, as the TypeScript AST attaches it: 58 top-level declarations in all.

**Types**
- **List side, 515-608:** `PurchaseSummary` 515-527, `LastActivity` 529-532, `AccountRow` 534-585, `AccountsQuery` 587-600, `AccountsPage` 602-608.
- **Detail side, 883-1147:**
  - `LicenseRow` 883-907, `Location` 909-923, `ActivationRow` 925-945, `SceneSaveRow` 947-959, `ShareRow` 961-970, `RecordedTakeRow` 972-978, `SceneRecordingRow` 980-994
  - `SceneImportRow` 996-1007, `EventRow` 1009-1022, `DeletionPreview` 1024-1046, `AuthUser` 1048-1068, `MarketingConsent` 1070-1076, `SubscriptionSummary` 1078-1097, `AccountDetail` 1099-1147
- **Aliases:** `ActivityKind` 153, `SortKey` 506, `SortDir` 507.

**Constants.** SQL fragments and vocabularies; every one is module-private except `VERSION_MATCH`.
- **Identity:** the essay and `ACCOUNTS_CTE`, 15-83.
- **Last activity:** `ACTIVITY_KINDS` 85-151, `ACTIVITY_LABELS` 155-156, `ACTIVITY_JOINS` 162-217, `ACTIVITY_COLUMNS` 219-226.
- **Handle:** `PROFILE_JOIN` 228-242, `PROFILE_COLUMNS` 244-246, and a suspension note at 248-253. The AST attaches that note to the next declaration, `MONTH_COUNT_COLUMNS`.
- **Month counts:** `MONTH_COUNT_COLUMNS` 255-309.
- **Signup location:** an essay at 311-353 that describes `SIGNUP_*`. It sits above the version section, so the AST attaches it to `VERSION_MATCH`. The code is `SIGNUP_CTE` 458-471, `SIGNUP_JOIN` 473-474, `SIGNUP_COLUMNS` 476-486.
- **Best version:** the essay at 354-411, `VERSION_MATCH` 412, `VERSION_CTE` 414-446, `VERSION_JOIN` 448-449, `VERSION_COLUMNS` 451-456.
- **Sorting:** `SORTS` 488-504, `SORT_KEYS` 509.
- **Filter:** `ACCOUNTS_WHERE` 684-718.
- **Detail window:** `DETAIL_LIMIT` 1276.

**Pure utilities**
- `activityKind` 158-160, `isSortKey` 511-513.
- Row mapping: `iso` 610-616, `int` 618-621, `purchaseFrom` 623-636, `usageFrom` 638-645, `accountFrom` 647-682, `raw` 1149-1165, `shareRows` 1167-1183.

**Data access** (Postgres through `lib/db.ts`)
- **The `/accounts` list:** `listAccounts` 720-800, `countAccounts` 802-824, `accountFacets` 826-881.
- **Guarded reads.** Each fails to null and logs:
  - `authUserFor` 1185-1220 (the `auth` schema may not be granted)
  - `subscriptionsFor` 1222-1274 (the deploy window before migration 0017)
  - `locationsFor` 1278-1358 (the deploy window before migration 0020)
- **Delete preview:** `deletionPreview` 1637-1706.
- **The account page read:** `getAccount` 1360-1635. It runs one account query and one licence-id query, then 16 reads through `inSeries`, then maps all 16 results.

**Server actions, hooks and client state, sub-components:** none.

**Orchestration:** `getAccount` orchestrates its own read. There is no module-level wiring.

## 4. Proposed tree

```
admin/src/lib/accounts.ts          ~20   the barrel; same path, same 29 names
admin/src/lib/accounts/
  identity.ts                      ~140  what an account is, and the filter that finds one
  last-activity.ts                 ~145  the "last activity" derivation and its labels
  month-counts.ts                  ~60   the five counts over the UTC month
  signup.ts                        ~80   signup location
  version.ts                       ~110  best version, and the pinned VERSION_MATCH
  sort.ts                          ~30   the fixed sort lookup
  types.ts                         ~115  the list row and its query
  detail-types.ts                  ~255  the account page's shapes
  rows.ts                          ~115  row mappers, no IO
  list.ts                          ~175  the /accounts queries
  guarded.ts                       ~180  the three reads that fail to null
  deletion.ts                      ~75   the delete preview
  detail.ts                        ~295  getAccount
```

**Rules for every leaf**
- Line 1 is `import 'server-only';`, the guard the whole file had.
- Its imports follow.
- Any header comment of its own goes above the imports, so the move check in §8 attributes it to no declaration.

**Per file.** What moves, with the declaration's current range. "Export added" marks a declaration that is module-private today and becomes an export of its leaf only; the barrel does not re-export it.

- **`identity.ts`**
  - Moves: `ACCOUNTS_CTE` 15-83, `PROFILE_JOIN` 228-242, `PROFILE_COLUMNS` 244-246 followed by the suspension note 248-253, `ACCOUNTS_WHERE` 684-718.
  - Order: `ACCOUNTS_CTE` first, and no prose naming `ACCOUNTS_CTE` above it (§10).
  - Imports: none. Exports added: all four.
- **`last-activity.ts`**
  - Moves: `ACTIVITY_KINDS` 85-151 (stays private), `ActivityKind` 153, `ACTIVITY_LABELS` 155-156, `activityKind` 158-160, `ACTIVITY_JOINS` 162-217, `ACTIVITY_COLUMNS` 219-226.
  - Imports: none. Exports added: `activityKind`, `ACTIVITY_JOINS`, `ACTIVITY_COLUMNS`.
- **`month-counts.ts`**
  - Moves: `MONTH_COUNT_COLUMNS` 255-309.
  - Imports: `MONTH_START_SQL` from `../db`. Export added: `MONTH_COUNT_COLUMNS`.
- **`signup.ts`**
  - Moves: the signup essay 311-353, then `SIGNUP_CTE` 458-471, `SIGNUP_JOIN` 473-474, `SIGNUP_COLUMNS` 476-486. The essay goes back beside the code it describes.
  - Imports: none. Exports added: all three.
- **`version.ts`**
  - Moves: the essay 354-411, `VERSION_MATCH` 412, `VERSION_CTE` 414-446, `VERSION_JOIN` 448-449, `VERSION_COLUMNS` 451-456.
  - `export const VERSION_MATCH = String.raw\`…\`;` stays byte for byte, because the version-key test matches it with a regex.
  - Imports: none. Exports added: `VERSION_CTE`, `VERSION_JOIN`, `VERSION_COLUMNS`.
- **`sort.ts`**
  - Moves: `SORTS` 488-504, `SortKey` 506, `SortDir` 507, `SORT_KEYS` 509, `isSortKey` 511-513.
  - Imports: none. Export added: `SORTS`.
- **`types.ts`**
  - Moves: `PurchaseSummary` 515-527, `LastActivity` 529-532, `AccountRow` 534-585, `AccountsQuery` 587-600, `AccountsPage` 602-608, and `Location` 909-923. `Location` moves up beside `AccountRow`, which already references it.
  - Imports, all `import type`: `LicenseStatus`, `MonthUsage`, `Plan` from `../shared`; `ActivityKind` from `./last-activity`; `SortKey`, `SortDir` from `./sort`.
- **`detail-types.ts`**
  - Moves: `LicenseRow` 883-907, `ActivationRow` 925-945, `SceneSaveRow` 947-959, `ShareRow` 961-970, `RecordedTakeRow` 972-978, `SceneRecordingRow` 980-994, `SceneImportRow` 996-1007, `EventRow` 1009-1022, `DeletionPreview` 1024-1046, `AuthUser` 1048-1068, `MarketingConsent` 1070-1076, `SubscriptionSummary` 1078-1097, `AccountDetail` 1099-1147.
  - Imports, all `import type`: `Bucket`, `LicenseStatus`, `Plan`, `QuotaVerdict` from `../shared`; `AccountRow`, `Location`, `PurchaseSummary` from `./types`.
- **`rows.ts`**
  - Moves: `iso` 610-616, `int` 618-621, `purchaseFrom` 623-636 (private), `usageFrom` 638-645 (private), `accountFrom` 647-682, `raw` 1149-1165, `shareRows` 1167-1183.
  - Imports: `planOf` and `type MonthUsage` from `../shared`; `activityKind` from `./last-activity`; `type AccountRow, PurchaseSummary` from `./types`; `type ShareRow` from `./detail-types`.
  - Exports added: `iso`, `int`, `accountFrom`, `raw`, `shareRows`.
- **`list.ts`**
  - Moves: `listAccounts` 720-800, `countAccounts` 802-824 (stays private), `accountFacets` 826-881.
  - Order: exactly this, with nothing between them (§10).
  - Imports:
    - `db`, `type Sql` from `../db`
    - `ACCOUNTS_CTE`, `ACCOUNTS_WHERE`, `PROFILE_JOIN`, `PROFILE_COLUMNS` from `./identity`
    - `ACTIVITY_JOINS`, `ACTIVITY_COLUMNS` from `./last-activity`
    - `MONTH_COUNT_COLUMNS` from `./month-counts`
    - the three `SIGNUP_*` from `./signup`
    - `VERSION_CTE`, `VERSION_JOIN`, `VERSION_COLUMNS` from `./version`
    - `SORTS`, `isSortKey`, `type SortKey, SortDir` from `./sort`
    - `type AccountsQuery, AccountsPage` from `./types`
    - `int`, `accountFrom` from `./rows`
- **`guarded.ts`**
  - Moves: `authUserFor` 1185-1220, `subscriptionsFor` 1222-1274, `locationsFor` 1278-1358.
  - Imports: `type Sql` from `../db`; `type AuthUser, SubscriptionSummary` from `./detail-types`; `type Location` from `./types`; `iso`, `int` from `./rows`.
  - Exports added: all three.
  - Why together: the three make one decision. A read against a grant or a column that a deploy window can leave missing logs and returns null instead of taking the page down. `locationsFor`'s own comment names the other two as the same shape.
- **`deletion.ts`**
  - Moves: `deletionPreview` 1637-1706.
  - Imports: `type Sql` from `../db`; `type DeletionPreview` from `./detail-types`; `int` from `./rows`.
  - Export added: `deletionPreview`.
- **`detail.ts`**
  - Moves: `DETAIL_LIMIT` 1276 (private), `getAccount` 1360-1635.
  - Imports:
    - `db`, `inSeries` from `../db`
    - `bothAllowances`, `planOf`, `sceneBucket` from `../shared`
    - `ACCOUNTS_CTE`, `PROFILE_JOIN`, `PROFILE_COLUMNS` from `./identity`
    - `ACTIVITY_JOINS`, `ACTIVITY_COLUMNS` from `./last-activity`
    - `MONTH_COUNT_COLUMNS` from `./month-counts`
    - the three `SIGNUP_*` from `./signup`
    - `VERSION_CTE`, `VERSION_JOIN`, `VERSION_COLUMNS` from `./version`
    - `type AccountDetail` from `./detail-types`
    - `iso`, `int`, `accountFrom`, `raw`, `shareRows` from `./rows`
    - `authUserFor`, `subscriptionsFor`, `locationsFor` from `./guarded`
    - `deletionPreview` from `./deletion`

**Accounting.** 4 + 6 + 1 + 3 + 4 + 5 + 6 + 13 + 7 + 3 + 3 + 1 + 2 = **58**, one per top-level declaration of the file.

**Import graph.** No leaf imports the barrel, and the graph is acyclic.

```
sort, identity, signup, version, last-activity   no local imports
month-counts  -> ../db
types         -> ../shared, last-activity, sort                       (type-only)
detail-types  -> ../shared, types                                     (type-only)
rows          -> ../shared, last-activity, types, detail-types
list          -> ../db, identity, last-activity, month-counts, signup, version, sort, types, rows
guarded       -> ../db, types, detail-types, rows
deletion      -> ../db, detail-types, rows
detail        -> ../db, ../shared, identity, last-activity, month-counts, signup, version,
                 detail-types, rows, guarded, deletion
accounts.ts   -> last-activity, version, sort, types, detail-types, list, detail
lib/activity.ts -> accounts.ts                                        (unchanged)
```

## 5. The entry point afterwards

`admin/src/lib/accounts.ts` stays at its path, reduced to this barrel:

```ts
import 'server-only';

// The account read model. The code lives in ./accounts/; this file is the path
// every caller imports, and it re-exports exactly the names it always exported.

export { ACTIVITY_LABELS, type ActivityKind } from './accounts/last-activity';
export { VERSION_MATCH } from './accounts/version';
export { SORT_KEYS, isSortKey, type SortDir, type SortKey } from './accounts/sort';
export type { AccountRow, AccountsPage, AccountsQuery, LastActivity, Location, PurchaseSummary } from './accounts/types';
export type {
  AccountDetail, ActivationRow, AuthUser, DeletionPreview, EventRow, LicenseRow, MarketingConsent,
  RecordedTakeRow, SceneImportRow, SceneRecordingRow, SceneSaveRow, ShareRow, SubscriptionSummary,
} from './accounts/detail-types';
export { accountFacets, listAccounts } from './accounts/list';
export { getAccount } from './accounts/detail';
```

**How it keeps §2's contract**
- **Same 29 names, same kinds, same types.** The export lister's output for `src/lib/accounts.ts` is identical before and after, fully-qualified member types included (§8).
- **Same import paths, no importer edits.** `@/lib/accounts` and `./accounts` still resolve to `accounts.ts`: TypeScript's bundler resolution and webpack both try a file with an extension before a directory. So `app/accounts/page.tsx`, `app/accounts/[email]/page.tsx`, `components/AccountControls.tsx` and `lib/activity.ts` stay untouched.
- **Types re-export as types.** `export type` and inline `type` are required by `isolatedModules` (TS1205), and they leave no runtime reference to a type-only module.
- **Same guard, same runtime semantics.** `server-only` stays on the entry and goes on every leaf. There was no directive before and there is none after.
- **The widening stays inside the directory.** Private helpers gain `export` only within `lib/accounts/`; the barrel does not re-export them. Nothing outside `lib/accounts/` imports a leaf (§10).

**Why a file barrel beside the directory, rather than `accounts/index.ts`.** The entry path does not move, so git history and every prose pointer to `lib/accounts.ts` still land on the module, and no resolver or importer changes. `index.ts` also resolves everywhere here, including the client-boundary test's `/index.ts` probe. If Eric prefers it, delete `accounts.ts` in the same commit: with both files present, `accounts.ts` wins silently.

## 6. Files over 250 lines

- **`detail.ts`, ~295.** `getAccount` alone is 276 lines, and it is one query plan.
  - The 16 thunks passed to `inSeries` and the 16 names destructured from its result are positional, and the mapping below reads those names.
  - Every element is cast through `unknown`, so if the thunks and the destructuring lived in different files, a swapped pair would type-check and render the wrong table.
  - It stays whole in Phase 2.
  - A later, separate change could name the inline mappers for licences, activations, scene saves, recordings, imports, events and purchases, the way `shareRows` already is, bringing it near 180. That is a code change, not a move, so it is outside this ticket.
- **`detail-types.ts`, ~255.** Thirteen interfaces, about 60% doc comments, and every one is a part of `AccountDetail`. The comments record migration-era reasons (0010, 0011, 0017, 0020). Splitting further would separate `AccountDetail` from the rows it is built from.

**Close to the line:** `guarded.ts` ~180, `list.ts` ~175, `last-activity.ts` ~145 and `identity.ts` ~140. All four are mostly the essays that justify their SQL, and none is over 250.

## 7. Build and packaging touch points

**No change needed**
- **tsconfig.json:** `@/*` covers `src/lib/accounts/*`, and `include: **/*.ts` picks up the new files.
- **eslint.config.mjs:** nothing required. Optionally, add a guard against deep imports (§10), but not in a move PR.
- **next.config.mjs:** standalone tracing is unaffected, because these are ordinary imports that webpack compiles. `outputFileTracingRoot` stays.
- **.github/workflows/admin-ci.yml:** the `admin/**` path filter triggers it, and its `typecheck`, `lint`, `build`, `check:standalone` and `check:secrets` steps run as today. The migrations job also runs; it proves nothing about this change and needs nothing.
- **scripts/worktree-init.sh:** it already symlinks `admin/node_modules` into a worktree (lines 66-88). A worktree that skipped it has no `tsc`, `eslint` or `next`.
- **TypeScript 6.0.3 and typescript-eslint:** no version change, and nothing here needs one.

**Tests that change in the same PR.** They only re-point file paths; no assertion changes.
- **`test/server/admin-accounts-list.test.mjs`:** give `body()` and `literal()` a source argument, then:
  - `/const ACCOUNTS_WHERE = \`/` and `literal('ACCOUNTS_WHERE' | 'PROFILE_JOIN' | 'PROFILE_COLUMNS')` read `lib/accounts/identity.ts`
  - the `body()` calls for `listAccounts`, `countAccounts` and `accountFacets` read `lib/accounts/list.ts`
  - `status?: LicenseStatus | 'suspended' | 'all'` and `profileSuspendedAt: string | null` read `lib/accounts/types.ts`
  - `profileSuspendedAt: iso(row.profile_suspended_at)` reads `lib/accounts/rows.ts`
  - failure messages that name `lib/accounts.ts` name the new file
- **`test/server/admin-moderation.test.mjs`:** `profileHandle:` reads `lib/accounts/detail-types.ts`; `from private.profiles where email =` reads `lib/accounts/deletion.ts`; line 146's `ACCOUNTS_CTE` slice reads `lib/accounts/identity.ts`.
- **`test/server/admin-version-key.test.mjs:20,26`:** read `admin/src/lib/accounts/version.ts`.
- **Do not concatenate the directory instead.** `body()` ends a function at the next `async function`, so under concatenation `accountFacets` would run into the next file and pass or fail on another file's SQL.

**CI reach.** `scripts/ci-changes.sh:54` treats `admin/*` as not touching the app, so an admin-only PR never runs `npm test`. This PR also edits `test/server/**`, which is not ignored, so `ci.yml` does run `npm test` and the three re-pointed tests with it. Run them locally anyway (§8, step 2).

**Prose updated in the same PR**
- `server/CLAUDE.md:531` becomes `admin/src/lib/accounts/guarded.ts`. It is markdown, which both path filters ignore.
- The comment at `lib/actions.ts:281` becomes `lib/accounts/deletion.ts`. If the actions split has landed first, the comment lives in `lib/actions/accounts.ts`.

**Left alone:** `admin/README.md:118` (`accounts.ts` still names the module), `page.tsx:201`, migration 0036 (applied; never edited), and `docs/superpowers/plans/*` (historical).

## 8. Verification recipe

**Tier.** The admin has no row of its own in the implementing-changes table. The root CLAUDE.md names "the admin's own lint and build", and `admin/README.md` § Verifying a change names `npm run verify`.

**The server-changes skill does not apply.** No file under `server/**` changes, and the Supabase contract (tables, functions, grants) is untouched. Every SQL string moves byte for byte, and step 4 proves it; without that, nothing here parses the SQL.

Run from a worktree after `bash scripts/worktree-init.sh`. Keep scratch files outside the checkout; `$S` below is any scratch directory.

**0. Baseline, before the first edit.** The branch starts at `main`. Spudagents cannot check out or stash, so this is the only moment to capture it.

```bash
cd admin
node $S/exports.cjs . src/lib/accounts.ts > $S/accounts.exports.before
git show origin/main:admin/src/lib/accounts.ts > $S/accounts.old.ts
```

**1. The admin CI build, locally.** `npm run verify` runs typecheck, lint, build, `check:standalone` and `check:secrets`. `check:secrets` refuses to pass with the variables unset; these fake values are the ones CI uses.

```bash
cd admin
NEXT_PUBLIC_SUPABASE_URL=https://ci.example.invalid \
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_ci-fake-not-a-real-credential \
SUPABASE_SECRET_KEY=sb_secret_ci-fake-must-never-be-bundled \
LICENSING_DB_URL=postgresql://ci-fake-user:ci-fake-password@db.example.invalid:6543/postgres \
ADMIN_EMAILS=ci-fake-admin@example.invalid \
npm run verify
```

**2. The text-assertion tests.** From the repo root; all five must pass with no skips.

```bash
node --test test/server/admin-accounts-list.test.mjs test/server/admin-moderation.test.mjs \
  test/server/admin-version-key.test.mjs test/server/admin-pool.test.mjs test/server/admin-client-boundary.test.mjs
```

**3. Symbol diff (the export contract).** Expect no output from `diff`.

```bash
cd admin
node $S/exports.cjs . src/lib/accounts.ts > $S/accounts.exports.after
diff $S/accounts.exports.before $S/accounts.exports.after
```

The lister prints member types fully qualified and collapses `lib/accounts.ts` and `lib/accounts/*` to one token. A pure move therefore diffs clean, while a leaf whose `Location` silently resolves to the DOM's global type (§10) shows up as a changed line.

**4. Pure-move check.**

```bash
cd admin
node $S/verify-moves.cjs $S/accounts.old.ts src/lib/accounts.ts src/lib/accounts/*.ts
```

- **Expected result:** `OK: 58 statements, every one moved exactly once`, exit 0.
- **What it compares:** each statement printed without comments, with an added `export` ignored. It skips imports, `export … from` lines and directives.
- **`COMMENT ADDED`:** allowed only for the barrel's header comment and any leaf header comment you added.
- **`COMMENT LOST`:** must not appear.
- **Relocated essays:** the signup essay and the suspension note move but keep their text, so they show as neither lost nor added.

**5. Runtime smoke.** This catches an import that builds but throws at render. Follow `admin/README.md` § Local development: podman with `DOCKER_HOST` for the local Supabase stack, or `npm run migrate` against any Postgres. Then `npm run seed`, `npm run dev` (port 3200), and sign in with `npm run dev:code -- <an ADMIN_EMAILS address>`.
- Open `/accounts` with the default sort, then `?sort=version`, `?sort=signup_location`, `?status=suspended` and `?q=@<a handle>`, and one empty page (`?page=99`, which exercises `countAccounts`).
- Open `/accounts/gil@seed.badtakes.test` (two licence rows), `/accounts/ivo@seed.badtakes.test` (null `account_email`, reached through its purchase) and `/accounts/fen@seed.badtakes.test` (the delete preview's survivors).
- Numbers must match `main`, and nothing may 500.

**The two scripts.** Both were tested against this file, `actions.ts` and the account page, including a synthetic split that must pass and a one-token change that must fail.

`exports.cjs`: run from `admin/` as `node exports.cjs . <file>...`.

```js
const path = require('path');
const adminDir = path.resolve(process.argv[2]);
const ts = require(path.join(adminDir, 'node_modules/typescript'));
const cfgPath = path.join(adminDir, 'tsconfig.json');
const cfg = ts.parseJsonConfigFileContent(ts.readConfigFile(cfgPath, ts.sys.readFile).config, ts.sys, adminDir);
const files = process.argv.slice(3).map((f) => path.resolve(adminDir, f));
const program = ts.createProgram(files, { ...cfg.options, noEmit: true, incremental: false });
const checker = program.getTypeChecker();
for (const f of files) {
  const sf = program.getSourceFile(f);
  const mod = checker.getSymbolAtLocation(sf);
  const out = [];
  for (let s of checker.getExportsOfModule(mod)) {
    const name = s.getName();
    if (s.flags & ts.SymbolFlags.Alias) s = checker.getAliasedSymbol(s);
    const kind = s.flags & ts.SymbolFlags.Function ? 'function'
      : s.flags & ts.SymbolFlags.Interface ? 'interface'
      : s.flags & ts.SymbolFlags.TypeAlias ? 'type'
      : s.flags & ts.SymbolFlags.Variable ? 'const' : 'other';
    const decl = s.declarations?.[0];
    const type = kind === 'interface' || kind === 'type'
      ? checker.typeToString(checker.getDeclaredTypeOfSymbol(s), undefined, ts.TypeFormatFlags.NoTruncation | ts.TypeFormatFlags.InTypeAlias)
      : checker.typeToString(checker.getTypeOfSymbolAtLocation(s, decl), undefined, ts.TypeFormatFlags.NoTruncation);
    let members = '';
    if (kind === 'interface') {
      const t = checker.getDeclaredTypeOfSymbol(s);
      members = ' { ' + checker.getPropertiesOfType(t).map((p) =>
        `${p.getName()}${p.flags & ts.SymbolFlags.Optional ? '?' : ''}: ${checker.typeToString(checker.getTypeOfSymbolAtLocation(p, p.declarations[0]), undefined, ts.TypeFormatFlags.NoTruncation | ts.TypeFormatFlags.UseFullyQualifiedType)}`).join('; ') + ' }';
    }
    out.push(`${name}\t${kind}\t${type}${members}`);
  }
  // Fully qualified names carry the declaring file; collapse a module and its
  // split directory to one token so a pure move diffs clean while a type that
  // silently resolves to a global (DOM Location) does not.
  const normalize = (l) => l.replace(/"[^"]*\/src\/lib\/(accounts|actions)(\/[^"]*)?"/g, '"@/lib/$1"')
    .replace(/"[^"]*\/src\/app\/accounts\/\[email\][^"]*"/g, '"@/app/accounts/[email]"');
  console.log(`## ${path.relative(adminDir, f)} (${out.length} exports)`);
  out.splice(0, out.length, ...out.map(normalize));
  console.log(out.sort().join('\n'));
}
```

`verify-moves.cjs`: run from `admin/` as `node verify-moves.cjs <old file> <new file>...`.

```js
// CODE: every top-level statement of the old file, printed without comments and
// without an added `export`, must appear exactly as often across the new files.
// Imports, export-from re-exports and directive prologues are skipped (they are
// the wrapper lines a split adds). COMMENT: every comment lost or added is
// listed for a human to read; comments do not fail the run.
const fs = require('fs'), path = require('path');
const ts = require(path.resolve('node_modules/typescript'));
const [oldFile, ...newFiles] = process.argv.slice(2);
const printer = ts.createPrinter({ removeComments: true });
function read(file) {
  const text = fs.readFileSync(file, 'utf8');
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true,
    file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const code = [], comments = new Map();
  for (const s of sf.statements) {
    if (ts.isImportDeclaration(s) || ts.isExportDeclaration(s)) continue;
    if (ts.isExpressionStatement(s) && ts.isStringLiteral(s.expression)) continue;
    code.push(printer.printNode(ts.EmitHint.Unspecified, s, sf).replace(/^export (?!default\b)/, ''));
  }
  const visit = (n) => {
    for (const r of [...(ts.getLeadingCommentRanges(text, n.getFullStart()) || []),
                     ...(ts.getTrailingCommentRanges(text, n.getEnd()) || [])]) comments.set(r.pos, text.slice(r.pos, r.end));
    ts.forEachChild(n, visit);
  };
  visit(sf);
  return { code, comments: [...comments.values()] };
}
const tally = (xs) => xs.reduce((m, x) => m.set(x, (m.get(x) || 0) + 1), new Map());
const head = (s) => s.split('\n')[0].slice(0, 110);
const before = read(oldFile), after = newFiles.map(read);
const oc = tally(before.code), nc = tally(after.flatMap((a) => a.code));
let bad = 0;
for (const [s, n] of oc) { const m = nc.get(s) || 0; if (m !== n) { bad++; console.log(`CODE ${m < n ? 'MISSING' : 'DUPLICATED'} ${n}->${m}: ${head(s)}`); } }
for (const [s] of nc) if (!oc.has(s)) { bad++; console.log(`CODE ADDED: ${head(s)}`); }
const ok = tally(before.comments), nk = tally(after.flatMap((a) => a.comments));
for (const [c, n] of ok) if ((nk.get(c) || 0) < n) console.log(`COMMENT LOST: ${head(c)}`);
for (const [c, n] of nk) if ((ok.get(c) || 0) < n) console.log(`COMMENT ADDED: ${head(c)}`);
console.log(bad ? `FAIL: ${bad} code difference(s)` : `OK: ${before.code.length} statements, every one moved exactly once`);
process.exitCode = bad ? 1 : 0;
```

`verify-moves` does not see JSX comments (`{/* … */}`); this file has none.

## 9. Phase 2 order

The whole split fits in one PR: it is pure moves, one barrel and three test re-points, and review means reading the `verify-moves` output rather than 1700 lines of diff. Two PRs keep Eric's order visible, though, and each stays small.

**PR 1: types, constants and pure utilities**
- **Creates:** `sort.ts`, `identity.ts`, `last-activity.ts`, `month-counts.ts`, `signup.ts`, `version.ts`, `types.ts`, `detail-types.ts`, `rows.ts`.
- **In `accounts.ts`:** it imports what it uses from the new files and re-exports the public names among them. `listAccounts`, `countAccounts`, `accountFacets`, `authUserFor`, `subscriptionsFor`, `locationsFor`, `deletionPreview`, `DETAIL_LIMIT` and `getAccount` stay in place.
- **Test re-points:** admin-version-key; the literal and type assertions of admin-accounts-list; `ACCOUNTS_CTE` and `profileHandle:` in admin-moderation.
- **Proof:** §8, steps 0-5. The move check runs over `accounts.ts` plus the nine new files and reports 58.

**PR 2: data access, and the entry reduced to the barrel**
- **Creates:** `list.ts`, `guarded.ts`, `deletion.ts`, `detail.ts`. `accounts.ts` becomes the barrel in §5.
- **Test re-points:** the `body()` assertions in admin-accounts-list and the deletion SQL assertion in admin-moderation.
- **Prose:** updates `server/CLAUDE.md:531` and the comment at `lib/actions.ts:281`.
- **Proof:** §8, steps 0-5, with a fresh baseline from `main` taken after PR 1 merges.

This file has no sub-component step. `getAccount` is its own orchestrator and moves whole in PR 2.

**Coordination.** This split is independent of the `actions.ts` and account-page spikes. The page imports only `getAccount` and `AccountDetail`, through the barrel, so the order between the three tickets is free.

## 10. Risks

- **Circular imports through the barrel.** A leaf that imports `'../accounts'` or `'@/lib/accounts'` instead of a sibling creates a cycle (barrel → `detail` → barrel), and tsc does not report cycles.
  - Two constants interpolate another constant when their module loads, not when a query runs: `VERSION_CTE` embeds `${VERSION_MATCH}`, and `MONTH_COUNT_COLUMNS` embeds `${MONTH_START_SQL}`.
  - A cycle through either would throw on load, or bake `undefined` into the SQL, depending on how the bundler lowers `const`. The build passes either way.
  - `VERSION_CTE` and `VERSION_MATCH` therefore stay in one file, and `db.ts` imports nothing from here.
  - **Rule:** leaves import siblings as `./name` only. **Check:** `grep -rn "'\.\./accounts'\|'@/lib/accounts'" admin/src/lib/accounts/` prints nothing.
- **`Location` shadows the DOM global.** Lines 909-923 declare a `Location` interface that shadows `lib.dom`'s `Location`.
  - A leaf that uses `Location` without `import type { Location } from './types'` does not fail to resolve. It type-checks against `window.location`'s type instead.
  - Most uses would then fail (the object literals lack `href`), but a pass-through annotation would not.
  - §8 step 3 catches it: `AccountRow.signupLocation` prints `import("@/lib/accounts").Location | null` when correct and a bare `Location | null` when wrong.
- **The moderation test's 2000-character window.** The slice starts at the first occurrence of `ACCOUNTS_CTE` in the file the test reads, and the predicate is at 1601 today. A header comment in `identity.ts` that names `ACCOUNTS_CTE` above the constant, or growth in the CTE's own comments, pushes it past 2000 and fails the test for a reason unrelated to deleted accounts.
- **Function boundaries in text assertions.** `body()` ends a function at the next `\nexport async function ` or `\nasync function `. Keep `listAccounts`, `countAccounts` and `accountFacets` adjacent and in that order: a helper placed between them changes what each body contains.
- **"Use server" and "use client" boundaries:** none in this file.
  - `components/AccountControls.tsx` is a client component that imports `type DeletionPreview` through the barrel. That works because `import type` is erased.
  - A future value import from any client file would pull `server-only` into the client and fail the build. That failure is loud, and correct.
  - Keep `server-only` on every leaf, including the type-only ones, so the rule stays "everything under `lib/accounts` is server-only".
- **What the build cannot see.**
  - The SQL inside template literals: tsc never parses it, so step 4's byte-identity is the only guard and is not optional.
  - The positional `inSeries` tuple in `getAccount`, which is why it stays whole.
  - A render that throws, which only step 5 exercises.
- **Surface widening.** The leaves export helpers that were private, and anything can import them by path; nothing enforces "barrel only". If Eric wants enforcement, add it in a separate PR: an ESLint `no-restricted-imports` pattern for `@/lib/accounts/*`, overridden for `src/lib/accounts.ts` and `src/lib/accounts/**`.
- **Stale prose.** See §7. The migration 0036 comment stays stale by design.
