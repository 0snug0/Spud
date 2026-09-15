# BAD-036 spike: admin/src/app/accounts/[email]/page.tsx

Phase 1 extraction plan. Written against BadTakes `main` at `20d11e1`, 2026-09-15. Every line number below refers to that commit.

## 1. The file today

- **Path:** `admin/src/app/accounts/[email]/page.tsx`, 1207 lines.
- **Runtime:** the App Router page for `/accounts/[email]`, and a React Server Component: no directive, and an async default export `AccountPage`.
  - `export const dynamic = 'force-dynamic'`, so every request renders fresh on Cloud Run.
  - No `metadata`, `generateMetadata` or `generateStaticParams`.
  - The segment has no other route files (`loading`, `error`, `not-found`); `notFound()` falls through to `app/not-found.tsx`.
- **Toolchain:** the admin dashboard (Next.js 16.3, `next build --webpack`, TypeScript 6.0.3 with typescript-eslint 8.68). See `admin-accounts.md` §1 for the tsconfig and ESLint settings.
- **How it loads:** Next's router, by filesystem convention. Nothing imports the file. `/accounts` list rows link to it (`prefetch={false}`).
- **Server imports:**
  - `requireAdmin` from `@/lib/auth`
  - `getAccount`, `type AccountDetail` from `@/lib/accounts`
  - `auditForEmail`, `type AuditEntry` from `@/lib/audit`
  - `isMetricKey`, `metricLabel`, `metricSegmentation`, `segmentedSeries`, `type MetricKey` from `@/lib/activity`
  - `viewerTimeZone` from `@/lib/timezone`, `inSeries` from `@/lib/db`, `listFlags` from `@/lib/flags`
  - `profilePanel`, `type HandleRelease, ProfileRow` from `@/lib/profiles`
  - `COOLDOWN_MS`, `explainAll`, `FREE_SCENES`, `type Bucket, FeatureFlag, QuotaVerdict` from `@/lib/shared`
  - `countdown`, `money`, `place`, `relative`, `shortId` from `@/lib/format`
  - `segmentLabel`, `type SegmentedPoint` from `@/lib/segments`, a plain module
- **Client components** (`'use client'`), all imported by PascalCase name:
  - `Meter`, `SegmentedBars` from `components/Charts`
  - `DeleteControl`, `PlanControl`, `StatusControl` from `components/AccountControls`
  - `ClearTextControl`, `ReleaseHandleControl`, `SuspendControl`, `UnsuspendControl` from `components/ProfileControls`
  - `JsonButton`, `JsonProvider` from `components/JsonViewer`
  - `ThemeToggle`, and `Time`, `TimeAgo`, `TimeDate` from `components/Time`
- **Also:** `Shell` (a server component), `Link` and `notFound` from Next.

## 2. Public surface

**Exports.** Two, listed by the TypeScript compiler API (script in `admin-accounts.md` §8). This is the compatibility contract.

| Export | Line | Contract |
|---|---|---|
| `default` (`AccountPage`) | 84 | `({ params: Promise<{ email: string }>, searchParams: Promise<{ tab?: string; metric?: string }> })` |
| `dynamic` | 28 | `'force-dynamic'` |

**Next.js route conventions**
- **No other named export is possible.** `next build` type-checks page exports through `checkFields<Diff<{ default, dynamic, revalidate, generateMetadata, … }, TEntry>>` (`next/dist/build/webpack/plugins/next-types-plugin/index.js:46`), and any other name fails with "… is not a valid Page export field". Nothing can import helpers from `page.tsx`, so shared constants must live in a separate module.
- **The URL contract:**
  - `?tab=user|usage|logs`, defaulting to `user`; the old `?tab=profile` falls through to `user`.
  - `?metric=<MetricKey>`, defaulting to `scenes_imported`.
  - Tab links are `?tab=<id>`, except usage, which is `?tab=usage&metric=<metric>`.
  - Rail links are `?tab=usage&metric=<m>` with `scroll={false}`.
- **Server-component semantics.** The page and everything it renders directly are server components. The client components above receive serialisable props only, such as `raw` rows (Dates already converted to ISO strings) and strings.

**Text contracts**
- `test/server/admin-pool.test.mjs:72-77` reads this path and requires `await inSeries([` in its source with comments stripped.
- The same test's `FAN_OUT` scan covers every file under `admin/src`.
- `test/server/admin-client-boundary.test.mjs` fails a server file that imports a non-PascalCase value from a `'use client'` file. This page broke once exactly that way (`SEGMENT_SETS` out of `Charts.tsx`), which is why `lib/segments.ts` exists.
- `test/server/admin-timezone.test.mjs:117-125` allows only `lib/timezone.ts` and `app/layout.tsx` to read the timezone cookie. The page goes through `viewerTimeZone()`.

**Prose that names the file** (still true afterwards): `app/scenes/[name]/not-found.tsx:4`, `app/scenes/[name]/page.tsx:45`, and `admin/CLAUDE.md`'s "account detail page" paragraph.

## 3. Responsibility map

Twenty top-level declarations; ranges include leading comments, as the TypeScript AST attaches them.

**Types:** `Tab` 49, derived from `TABS`.

**Constants:** `TABS` 30-47, `USAGE_RAIL` 55-68, `LOGS` 70-81.

**Pure utilities:** `isTab` 51-53, `rowCount` 898-913, `idStub` 1192-1195, `keyStub` 1197-1200, `initials` 1202-1207.

**Data access:** `flagsOrNull` 194-211 (fails to null and logs), plus the fetch sequence inside `AccountPage` at 112-149:
1. `getAccount` on its own, so a 404 costs one query.
2. `inSeries` over `auditForEmail`, then `flagsOrNull` and `profilePanel` on the user tab only.
3. `viewerTimeZone()` on the usage tab.
4. `inSeries` over six `segmentedSeries` on the usage tab.

**Server actions:** none. The controls are client components that import `@/lib/actions`.

**Hooks and client state:** none. State is the URL.

**Sub-components.** Every one is a synchronous server component that takes props only.
- **`UserTab` 213-700**, 488 lines, in sections:
  - This month 239-250
  - Allowances 252-261
  - Feature flags 263-342, an IIFE that evaluates `explainAll` against the representative licence
  - The facts grid 344-489: the `UserCard` call 345, the Licence card 347-404, the Subscription card 406-449, the Purchase card 451-488
  - Controls 491-513
  - Profile controls 515-533
  - Handle history 535-564
  - All licence rows 566-619
  - Activation history 621-668
  - Admin actions 670-697
- **`UserCard` 702-835**
- **`UsageTab` 837-896**
- **`LogTable` 915-1080:** six branches, one per `MetricKey`, and `Wrap` 1082-1089.
- **`LogsTab` 1091-1134**
- **`Allowance` 1136-1181:** its range starts with two leftover section headers, `profile` and `bits`.
- **`Stat` 1183-1190**

**Orchestration:** `AccountPage` 84-192 authorises, parses parameters, resolves the tab and metric, fetches in series, then renders the header, the tab nav and the active tab.

## 4. Proposed tree

```
admin/src/app/accounts/[email]/
  page.tsx                   ~155  the orchestrator: default AccountPage, dynamic, TABS, Tab, isTab
  _lib/
    usage-rail.ts            ~30   USAGE_RAIL, LOGS
    flags-or-null.ts         ~25   flagsOrNull
    stubs.ts                 ~12   idStub, keyStub
  _components/
    UserTab.tsx              ~140  UserTab (sections become components), Allowance, Stat
    FlagsPanel.tsx           ~95   FlagsPanel            new; was the block at 263-342
    UserCard.tsx             ~150  UserCard, initials
    AccountCards.tsx         ~170  LicenceCard, SubscriptionCard, PurchaseCard      new; 347-488
    Controls.tsx             ~65   ControlsSection, ProfileControlsSection           new; 491-533
    UserTables.tsx           ~200  HandleHistory, LicenceRows, ActivationHistory, AdminActions   new; 535-697
    UsageTab.tsx             ~90   UsageTab, rowCount
    LogTable.tsx             ~185  LogTable, Wrap
    LogsTab.tsx              ~50   LogsTab
```

**Why these folders**
- **The underscore.** `_components` and `_lib` are Next's documented private folders, excluded from routing (`next/dist/docs/01-app/01-getting-started/02-project-structure.md` § Private folders). Colocated files are non-routable anyway; the underscore makes the intent explicit.
- **Not `admin/src/components/`.** That directory is the app's shared, mostly client, component library. These are one page's server components, and nothing else renders them.

**Rules for every new file**
- No `'use client'`, no hooks and no data fetching. Every component is a synchronous function of its props (§10, "Pool budget").
- An extracted component's props are named exactly after the variables its block already reads, so the JSX moves verbatim. For example, `LicenceCard({ account, signupLocation, latestLocation, distinctMachines })`.

**Per file.** Declarations move whole with their current range; blocks move verbatim into a new component.

- **`page.tsx`**
  - Keeps: `dynamic` 28, `TABS` 30-47 (with its essay), `Tab` 49, `isTab` 51-53 (all used only here), `AccountPage` 84-192.
  - Imports:
    - `Link` from `next/link`, `notFound` from `next/navigation`
    - `Shell` from `@/components/Shell`, `ThemeToggle` from `@/components/ThemeToggle`, `JsonProvider` from `@/components/JsonViewer`
    - `requireAdmin` from `@/lib/auth`, `getAccount` from `@/lib/accounts`, `auditForEmail` from `@/lib/audit`
    - `isMetricKey`, `segmentedSeries`, `type MetricKey` from `@/lib/activity`
    - `viewerTimeZone` from `@/lib/timezone`, `inSeries` from `@/lib/db`, `profilePanel` from `@/lib/profiles`
    - `USAGE_RAIL` from `./_lib/usage-rail`, `flagsOrNull` from `./_lib/flags-or-null`
    - `UserTab`, `UsageTab`, `LogsTab` from `./_components/…`
- **`_lib/usage-rail.ts`**
  - Moves: `USAGE_RAIL` 55-68 and `LOGS` 70-81. Both are shared by the orchestrator (the series fetch) and `UsageTab`.
  - Imports: `type MetricKey` from `@/lib/activity`.
  - A plain module: no `'use client'`, and nothing that would stop a server component reading it.
- **`_lib/flags-or-null.ts`**
  - Moves: `flagsOrNull` 194-211.
  - Imports: `import 'server-only'`; `listFlags` from `@/lib/flags`; `type FeatureFlag` from `@/lib/shared`.
- **`_lib/stubs.ts`**
  - Moves: `idStub` 1192-1195, `keyStub` 1197-1200. Neither merges into `lib/format.ts`, which would change that module's surface.
- **`_components/UserTab.tsx`**
  - Keeps: `UserTab` 213-700 with its signature, props, `claimed`/`history` locals, This month 239-250 and Allowances 252-261 inline. Every other section becomes one line rendering the component that now holds it.
  - Moves: `Allowance` 1136-1181 and `Stat` 1183-1190, which only this tab uses.
  - Imports:
    - `Meter` from `@/components/Charts`
    - `COOLDOWN_MS`, `FREE_SCENES`, `type Bucket, FeatureFlag, QuotaVerdict` from `@/lib/shared`
    - `countdown` from `@/lib/format`
    - `type AccountDetail`, `type AuditEntry`, `type HandleRelease, ProfileRow`
    - the five sibling component files
  - Trim the destructuring at 226-228 to the names still used here, since `@typescript-eslint/no-unused-vars` warns.
- **`_components/FlagsPanel.tsx`**
  - New `FlagsPanel({ detail, flags })`: the `<h2>Feature flags</h2>` and the `flags === null ? … : (() => { … })()` expression, 263-342, moved **verbatim**, IIFE included, inside a fragment. Do not restructure it in the same PR.
  - Imports: `Link`; `explainAll`, `type FeatureFlag` from `@/lib/shared`; `relative` from `@/lib/format`; `type AccountDetail`.
- **`_components/UserCard.tsx`**
  - Moves: `UserCard` 702-835 and `initials` 1202-1207, which only `UserCard` uses.
  - Imports: `Time`, `TimeDate` from `@/components/Time`; `type AccountDetail`; `type ProfileRow`.
- **`_components/AccountCards.tsx`**
  - New `LicenceCard`: the block at 347-404.
  - New `SubscriptionCard({ subscriptions })`: 406-449.
  - New `PurchaseCard({ purchases, account })`: 451-488. It reads `account.plan`.
  - Imports: `Time`, `TimeDate`; `money`, `place`, `relative` from `@/lib/format`; `type AccountDetail`.
- **`_components/Controls.tsx`**
  - New `ControlsSection({ account, deletion })`: 491-513.
  - New `ProfileControlsSection({ account, claimed })`: the JSX comment and the `claimed ? (…) : null` expression, 515-533, returned as is.
  - Imports: `DeleteControl`, `PlanControl`, `StatusControl` from `@/components/AccountControls`; `ClearTextControl`, `ReleaseHandleControl`, `SuspendControl`, `UnsuspendControl` from `@/components/ProfileControls`; types.
- **`_components/UserTables.tsx`**
  - New `HandleHistory({ history })`: 535-564.
  - New `LicenceRows({ licenses })`: the `licenses.length > 1 ? (…) : null` expression with its comment, 566-619.
  - New `ActivationHistory({ activations })`: 621-668.
  - New `AdminActions({ audit })`: 670-697.
  - Imports: `Time`, `TimeAgo`; `JsonButton`; `place`, `relative`, `shortId` from `@/lib/format`; `idStub`, `keyStub` from `../_lib/stubs`; types.
- **`_components/UsageTab.tsx`**
  - Moves: `UsageTab` 837-896 and `rowCount` 898-913.
  - Imports: `Link`; `SegmentedBars`; `metricLabel`, `metricSegmentation`, `type MetricKey` from `@/lib/activity`; `type SegmentedPoint` from `@/lib/segments`; `USAGE_RAIL`, `LOGS` from `../_lib/usage-rail`; `LogTable` from `./LogTable`; `type AccountDetail`.
- **`_components/LogTable.tsx`**
  - Moves: `LogTable` 915-1080 and `Wrap` 1082-1089 (private).
  - Imports: `Time`; `JsonButton`; `segmentLabel` from `@/lib/segments`; `idStub` from `../_lib/stubs`; `type AccountDetail`, `type MetricKey`.
- **`_components/LogsTab.tsx`**
  - Moves: `LogsTab` 1091-1134.
  - Imports: `Time`; `JsonButton`; `place` from `@/lib/format`; `type AccountDetail`.

**Accounting.** page 5 (`dynamic`, `TABS`, `Tab`, `isTab`, `AccountPage`) + usage-rail 2 + flags-or-null 1 + stubs 2 + UserTab 3 (`UserTab`, `Allowance`, `Stat`) + UserCard 2 + UsageTab 2 + LogTable 2 + LogsTab 1 = **20**, one per top-level declaration of the file.

Ten components are new, built from `UserTab`'s sections: `FlagsPanel`, `LicenceCard`, `SubscriptionCard`, `PurchaseCard`, `ControlsSection`, `ProfileControlsSection`, `HandleHistory`, `LicenceRows`, `ActivationHistory`, `AdminActions`.

**Import graph.** Nothing imports `page.tsx`, and the graph is acyclic.

```
page.tsx      -> _lib/usage-rail, _lib/flags-or-null, _components/UserTab, _components/UsageTab, _components/LogsTab
UserTab       -> FlagsPanel, UserCard, AccountCards, Controls, UserTables
UsageTab      -> LogTable, _lib/usage-rail
UserTables    -> _lib/stubs
LogTable      -> _lib/stubs
```

## 5. The entry point afterwards

`page.tsx` keeps:
- `export const dynamic = 'force-dynamic'` and `export default async function AccountPage`. The body of `AccountPage` is byte for byte what it is today: `UserTab`, `UsageTab`, `LogsTab`, `USAGE_RAIL` and `flagsOrNull` are imported instead of declared below.
- `TABS`, `Tab` and `isTab`, which are used only by the orchestrator.
- `requireAdmin()` first; `notFound()` before any render; the fetch order and both `await inSeries([` calls; `viewerTimeZone()` on the usage tab only; the header, tab nav and tab switch.

**How it keeps §2's contract**
- **Exports:** exactly `default` and `dynamic`, which the build enforces. The export lister prints the same two lines before and after, props type included.
- **Route semantics:** still a server component with no directive. No `metadata` existed and none is added. The URL contract is unchanged because `href()`, the `Link`s and the tab parsing do not move.
- **admin-pool:** its assertion on `page.tsx` still holds, because the fetch sequence stays in this file.

## 6. Files over 250 lines

None. The largest, for Eric's second look:

- **`UserTables.tsx`, ~200.** Four tables on the User tab, each a function of one `AccountDetail` field or of `audit`. They could be four files of about 50 lines; they stay together because they share one table idiom (empty-row message, `JsonButton` column) and are only ever read as the bottom of one tab.
- **`LogTable.tsx`, ~185.** One component with six branches, one table per `MetricKey`. The branch selection *is* the component. Splitting per branch would need a `MetricKey`-to-component map, which is a code change and not a move.
- **`AccountCards.tsx`, ~170.** Three sibling cards of one grid: licence, subscription, purchase.
- **`page.tsx`, ~155.** `AccountPage` is 109 lines, most of them the comments that explain the pool budget.

## 7. Build and packaging touch points

**No change needed**
- **tsconfig.json, eslint.config.mjs, next.config.mjs:** the new folders fall under `@/*` and `include`, and private folders need no configuration.
- **ESLint:** trim the unused destructured names in `UserTab`. The JSX, the `key` props and the `eslint-disable-next-line @next/next/no-img-element` at 730 move unchanged, the last one with `UserCard`.
- **.github/workflows/admin-ci.yml:** the `admin/**` filter triggers it.
- **scripts/worktree-init.sh:** it already links `admin/node_modules`.
- **TypeScript 6.0.3 and typescript-eslint:** no change.

**Tests:** none need re-pointing. `admin-pool.test.mjs:75` reads `app/accounts/[email]/page.tsx` for `await inSeries([`, which stays. Do not move the fetch sequence into a helper or into `_lib/`: the test would fail, and the sequence is the documented reason the page does not hang (`admin/CLAUDE.md`).

**CI reach.** An `admin/**`-only PR skips `npm test` (`scripts/ci-changes.sh:54`), so no CI job runs the admin text tests for this change. Run them locally (§8, step 2).

**Prose:** none needs updating.

## 8. Verification recipe

**Tier.** The admin's own gate is `npm run verify`. No file under `server/**` changes, and no query moves, so the server-changes skill does not apply.

Scripts: `exports.cjs` and `verify-moves.cjs`, reproduced in full in `spud:docs/spikes/bad-036/admin-accounts.md` §8. `$S` is a scratch directory outside the checkout.

**0. Baseline, before the first edit,** on the untouched branch. Spudagents cannot check out `main` later.

```bash
cd admin
node $S/exports.cjs . 'src/app/accounts/[email]/page.tsx' > $S/page.exports.before
git show 'origin/main:admin/src/app/accounts/[email]/page.tsx' > $S/page.old.tsx
```

Then capture the "before" HTML for step 5 from this same untouched tree.

**1. The admin CI build.** Run `npm run verify` in `admin/` with the fake CI environment in `admin-accounts.md` §8 step 1.

**2. Tests.** From the repo root:

```bash
node --test test/server/admin-pool.test.mjs test/server/admin-client-boundary.test.mjs test/server/admin-timezone.test.mjs
```

**3. Symbol diff.** Expect no output from `diff`: `default` and `dynamic`, unchanged.

```bash
cd admin
node $S/exports.cjs . 'src/app/accounts/[email]/page.tsx' > $S/page.exports.after
diff $S/page.exports.before $S/page.exports.after
```

**4. The move check.**

```bash
cd admin
node $S/verify-moves.cjs $S/page.old.tsx 'src/app/accounts/[email]/page.tsx' \
  'src/app/accounts/[email]/_lib/'*.ts 'src/app/accounts/[email]/_components/'*.tsx
```

- **After PR 1 (pure moves):** `OK: 20 statements, every one moved exactly once`.
- **After PR 2 (the extraction), exactly these lines are expected, and any other `CODE` line is a failure:**
  - `CODE MISSING 1->0: function UserTab(…` (it was rebuilt)
  - `CODE ADDED` for the reduced `UserTab` and the ten new components
- **Block check.** Confirm that each section range in §3 appears verbatim in its component, ignoring indentation:

  ```bash
  diff <(sed -n 347,404p $S/page.old.tsx | sed 's/^ *//') \
       <(sed -n '/export function LicenceCard/,/^}/p' 'src/app/accounts/[email]/_components/AccountCards.tsx' | sed 's/^ *//')
  ```

  Only the wrapper lines (signature, `return (`, closing) may differ. Repeat for each of the ten.
- **JSX comments.** `verify-moves` does not see `{/* … */}` comments, and nothing renders them, so eyeball each in the diff; every one must travel with its block.

**5. Render diff, the proof for the extraction.** The page is `force-dynamic`, so every request is a fresh server render.
- **Setup:** the local stack plus seed (`admin/README.md` § Local development: podman with `DOCKER_HOST`, or `npm run migrate` against any Postgres, then `npm run seed`).
- **Before and after,** on the same machine and database:
  - `npm run build && npm run start` (port 3200)
  - sign in at `/login` with `npm run dev:code -- <ADMIN_EMAILS address>`
  - copy the session cookie from the browser's devtools; it is HttpOnly (`admin/README.md` § The session cookie)
- **Fetch every combination:**

  ```bash
  # PHASE=before or after; COOKIE is the session cookie as a "name=value" string
  mkdir -p "$S/render.$PHASE"
  for who in ada brody cass dev ekko fen gil hana ivo; do
    for q in 'tab=user' 'tab=logs' $(for m in scenes_imported scenes_created scenes_shared scenes_recorded takes_recorded takes_exported; do echo "tab=usage&metric=$m"; done); do
      curl -s -b "$COOKIE" "http://localhost:3200/accounts/$who@seed.badtakes.test?$q" \
        | perl -0pe 's#<script\b.*?</script>##gs; s#\b(just now|\d+[mhd] ago)\b#AGE#g' \
        > "$S/render.$PHASE/$who.${q//[&=]/_}.html"
    done
  done
  diff -r $S/render.before $S/render.after
  ```

- **Expected result:** no difference, across 9 accounts and 8 views. The accounts cover paid, free, both cooldowns, a shared licence, a refund, two licence rows, an empty account and a null `account_email`.
- **Why the normalisation:**
  - Scripts carry the build id and the RSC payload.
  - `relative()` and `countdown()` read the clock (cass and dev are on cooldown).
  - `admin/src` has no `useId` today; if one appears, also normalise `«r…»` ids, since the extraction adds component depth.

**6. Hand check,** in `npm run dev`: open all three tabs for `gil` and `ekko`, and open one control dialog (Plan). The controls' props are unchanged, so this confirms only that they still hydrate.

## 9. Phase 2 order

Two PRs, because the two halves have different proofs: the move checker for moves, the render diff for extraction. Mixing them would hide a JSX edit among 1000 moved lines.

**PR 1: shared constants, pure utilities, the data guard, whole components, and the entry reduced**
- **Creates:**
  - `_lib/usage-rail.ts`, `_lib/stubs.ts`, `_lib/flags-or-null.ts`
  - `_components/UserTab.tsx`, holding `UserTab` whole (still 488 lines, plus `Allowance` and `Stat`)
  - `UserCard.tsx`, `UsageTab.tsx`, `LogTable.tsx`, `LogsTab.tsx`
- **Result:** `page.tsx` is already the orchestrator, because steps 1, 2 and 4 of Eric's order land together once nothing else is left in the file.
- **Proof:** §8, steps 0-5. Step 4 must report 20 of 20, and step 5 still applies, because a missed import renders differently.

**PR 2: the sub-component extraction**
- **Splits `UserTab` into:** `FlagsPanel.tsx`, `AccountCards.tsx`, `Controls.tsx`, `UserTables.tsx`.
- **The only non-move change in the ticket:** blocks move verbatim into prop-named components, and the destructuring is trimmed.
- **Proof:** §8, steps 1-6, with step 4's expected `UserTab` lines and the block check.

**Coordination.** Independent of the `accounts.ts` split (the page imports `getAccount` and `AccountDetail` through the barrel) and of the `actions.ts` split (the controls import `@/lib/actions` by the same path). Any order works.

## 10. Risks

- **Page export validation.** A helper exported from `page.tsx` fails `next build`, and a sub-component importing from `./page` would also be a cycle. Shared values go to `_lib/`. `USAGE_RAIL` and `LOGS` are the two that need it.
- **Pool budget: the one refactor that must not happen.** The idiomatic RSC move would give each panel its own async server component that fetches for itself (flags inside `FlagsPanel`, series inside `UsageTab`).
  - React renders sibling async server components concurrently.
  - That reopens the five-connections-against-a-pool-of-three hang in `admin/CLAUDE.md`: a click that does nothing and a tab that spins until Cloud Run's 300-second kill.
  - `admin-pool.test.mjs` would not catch it, because nobody writes `Promise.all`.
  - Every new component stays synchronous and data-free, and all fetching stays in `AccountPage`'s `inSeries` calls.
- **The server/client boundary.**
  - Every new file is a server component. Each imports client components by PascalCase name, which is allowed, and values (`explainAll`, `FREE_SCENES`, `COOLDOWN_MS`, `segmentLabel`, `relative`, `place`, `money`, `countdown`, `shortId`, `metricLabel`, `metricSegmentation`) from server and plain modules, which is allowed *only because* the importing file is a server component.
  - **Adding `'use client'` to one of them** would pull `@/lib/shared` (`server-only`) into the client and fail the build, which is loud. But a client file that imported only `@/lib/format` or `@/lib/segments` would pass and ship those modules to the browser, which is quiet.
  - **The reverse direction.** A server file importing a non-component value from a client file reads `undefined` at render. admin-client-boundary catches that, and it has happened on this page. Never put `USAGE_RAIL`, `LOGS` or the stubs in a `'use client'` file.
- **Props crossing into client components.** `JsonButton value={….raw}`, the controls' `email`, `licenseId`, `plan`, `status` and `preview`, and `Time iso` are unchanged values. Only the server component that passes them changes. Keep `raw` as the mapped plain object from `@/lib/accounts`, never a live Postgres row.
- **Silent JSX drift.** The extraction is the one place a reviewer cannot rely on `verify-moves`:
  - a dropped `key`
  - a conditional wrapper (`claimed ? … : null`, `licenses.length > 1 ? … : null`) moved inside the component on one branch and left outside on another
  - a JSX comment left behind

  The render diff catches the first two; only reading the diff catches the third.
- **`notFound()` and `requireAdmin()` order.** Both stay at the top of `AccountPage`. `notFound()` throws, and a sub-component must never call it after rendering has begun.
- **Near-collision in names.** `_components/Controls.tsx` holds sections that wrap the client components in `@/components/AccountControls` and `@/components/ProfileControls`. Import those by the `@/components/…` alias, never relatively.
- **What the build cannot see:** a render that differs, a component that fetches, and a JSX comment that went missing. Those are steps 5 and 6, and the pool rule above.
