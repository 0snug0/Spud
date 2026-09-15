# BAD-036 spike: admin/src/lib/actions.ts

Phase 1 extraction plan. Written against BadTakes `main` at `20d11e1`, 2026-09-15. Every line number below refers to that commit.

## 1. The file today

- **Path:** `admin/src/lib/actions.ts`, 1263 lines.
- **Runtime:** a Next.js server-actions module; line 1 is `'use server'`.
  - Next compiles every export into a POST endpoint with a server-reference id.
  - Every export is an `async function(_prev: ActionState, form: FormData): Promise<ActionState>` that a client component calls through `useActionState`.
  - The directive allows only async function exports, which is why `ActionState` and `IDLE` live in `lib/action-state.ts`; its header says so.
- **Toolchain:** the admin dashboard (Next.js 16.3, `next build --webpack`, TypeScript 6.0.3 with typescript-eslint 8.68). See `admin-accounts.md` §1 for the tsconfig and ESLint settings.
- **How it loads:** only from client components, which receive server references. No server component imports it.
- **What it imports:**
  - `revalidatePath` from `next/cache`, `redirect` from `next/navigation`
  - `requireAdmin` from `./auth`, `writeAudit` from `./audit`, `db` from `./db`
  - `secretKey`, `supabaseUrl` from `./env`
  - `generateKey`, the five announcement lists (`ANCHORS`, `ACTIONS`, `URL_HOSTS`, `ANNOUNCEMENT_KINDS`, `ANNOUNCEMENT_WEIGHTS`) and `type Plan` from `./shared`
  - `type ActionState` from `./action-state`, `type postgres` from `postgres`

  `auth.ts`, `audit.ts`, `db.ts` and `shared.ts` start with `import 'server-only'`; `env.ts` and `action-state.ts` do not. That is why `auth-admin.ts`, which imports `env.ts`, opens with its own `server-only` line (§4).
- **What it touches:**
  - Postgres: `private.licenses`, `profiles`, `release_handle()`, `reserved_handles`, `feature_flags`, `feature_flag_rules`, `announcements`, `announcement_rules` and `collabs`, plus `admin_audit` through `writeAudit`.
  - The Supabase Auth admin REST API, called with the service key by `deleteAuthUser`.
- **The rule it carries (lines 24-28):** every export mutates something and opens with `await requireAdmin()`. The proxy does not cover a server action, which is a POST to an endpoint Next generates.

## 2. Public surface

**Exports.** Twenty, all `(_prev: ActionState, form: FormData) => Promise<ActionState>`, listed by the TypeScript compiler API (script in `admin-accounts.md` §8). This is the compatibility contract. Every importer is a `'use client'` component; the grep is `grep -rn "@/lib/actions" admin/src`.

| Export | Line | Domain | Imported by |
|---|---|---|---|
| `createAccount` | 64 | account | `components/CreateAccountForm.tsx:5` |
| `setPlan` | 116 | account | `components/AccountControls.tsx:4` |
| `setStatus` | 159 | account | `components/AccountControls.tsx:4` |
| `deleteAccount` | 210 | account | `components/AccountControls.tsx:4` |
| `saveAnnouncement` | 459 | announcements | `components/AnnounceForms.tsx:4` |
| `saveAnnouncementRule` | 555 | announcements | `components/AnnounceForms.tsx:4` |
| `deleteAnnouncementRule` | 611 | announcements | `components/AnnounceForms.tsx:4` |
| `createFlag` | 634 | flags | `components/FlagForms.tsx:4` |
| `updateFlag` | 695 | flags | `components/FlagForms.tsx:4` |
| `deleteFlag` | 741 | flags | `components/FlagForms.tsx:4` |
| `saveRule` | 774 | flags | `components/FlagForms.tsx:4` |
| `deleteRule` | 852 | flags | `components/FlagForms.tsx:4` |
| `moveRule` | 885 | flags | `components/FlagForms.tsx:4` |
| `suspendProfile` | 934 | profiles | `components/ProfileControls.tsx:4-11` |
| `unsuspendProfile` | 975 | profiles | `components/ProfileControls.tsx:4-11` |
| `clearProfileText` | 1018 | profiles | `components/ProfileControls.tsx:4-11` |
| `releaseHandle` | 1065 | profiles | `components/ProfileControls.tsx:4-11` |
| `reserveHandle` | 1115 | profiles | `components/ProfileControls.tsx:4-11` |
| `unreserveHandle` | 1149 | profiles | `components/ProfileControls.tsx:4-11` |
| `closeCollab` | 1211 | collabs | `components/CollabControls.tsx:4` |

**Next.js conventions the file carries**
- `'use server'` at the top of the file, as the docs require for an action a client component imports (`next/dist/docs/01-app/03-api-reference/01-directives/use-server.md:48`).
- Only async functions are exported. The compiler error is "Only async functions are allowed to be exported in a 'use server' file".
- Module-private sync helpers and consts are allowed and present: `EMAIL_RE`, `normalizeEmail` and others.
- `redirect()` sits outside the `try` in `saveAnnouncement` (line 551) and `deleteFlag` (line 770), because `redirect()` throws.

**Text contracts** (tests that read the file by path)
- `test/server/admin-moderation.test.mjs:12` reads `lib/actions.ts`.
  - `body(name)` runs from `export async function <name>(` to the next `\nexport async function `.
  - For each of `suspendProfile`, `unsuspendProfile`, `clearProfileText`, `releaseHandle`, `reserveHandle` and `unreserveHandle`, the body contains `await requireAdmin()`, `writeAudit(` and `adminEmail: admin.email`.
  - `body('deleteAccount')` contains `private.release_handle(`, matches `release_handle(${email}, false,`, and has a `before: {` … `after: null` block naming `handle:`, `displayName:` and `bio:`.
  - The whole file must never match `update private.profiles … set … handle =`.
- `test/server/admin-pool.test.mjs` scans all of `admin/src` for concurrent promise starts; this file has none.
- `test/server/admin-client-boundary.test.mjs` skips client files as importers. It flags only server files that import values from client files, and neither side of this split is a client file.

**Prose that names the file.** These are not contracts, but they go stale:
- `server/supabase/functions/account/index.ts:23,57` cite `admin/src/lib/actions.ts:216` and `:363-366` by line number. Filed as proposal 71; editing an Edge Function redeploys it, so it is not touched here.
- The header of `lib/action-state.ts` ("outside actions.ts because that file is `'use server'`").
- The comment at `lib/accounts.ts:1658` ("deleteAccount in lib/actions.ts").
- `docs/superpowers/plans/*`, which are historical.

## 3. Responsibility map

Ranges include each declaration's leading comment, as the TypeScript AST attaches it: 34 top-level declarations plus the directive.

**Types:** `Tx` 21-22 (the `sql.begin` transaction handle).

**Constants**
- The rule comment 24-28, attached to `EMAIL_RE`.
- `EMAIL_RE` 30, `UUID_RE` 32-36.
- `FLAG_KEY_RE` 43-46, `FLAG_VERSION_RE` 48-50.
- `ANNOUNCE_KEY_RE` 425-432 and `VERSION_RE` 433. The AST also attaches two orphans to `ANNOUNCE_KEY_RE`: the feature-flags section header at 416 and a doc comment, "Create a flag.", at 418-424. Both belong to `createFlag` at 634.

**Pure utilities**
- `normalizeEmail` 38-41, `message` 412-414.
- `announceCta` 435-457, which parses the CTA fields of the form.
- `flagList` 677-687, `flagChoices` 689-693.

**Data access and services**
- `withOwnedLicense` 377-410: a transaction that proves the licence belongs to the account, `for update of l`.
- `deleteAuthUser` 336-375: the Supabase Auth admin REST API, using `supabaseUrl()` and `secretKey()`. It is **module-private and unauthenticated**; it is safe only because it is not exported.

**Server actions**
- **Account, 52-334:** `createAccount` 52-112, `setPlan` 114-155, `setStatus` 157-199, `deleteAccount` 201-334.
- **Announcements, 459-632:** `saveAnnouncement` 459-553, `saveAnnouncementRule` 555-609, `deleteAnnouncementRule` 611-632.
- **Flags, 634-924:** `createFlag` 634-675, `updateFlag` 695-730, `deleteFlag` 732-771, `saveRule` 773-850, `deleteRule` 852-875, `moveRule` 877-924.
- **Profiles, 926-1180:** `suspendProfile` 926-973, `unsuspendProfile` 975-1010, `clearProfileText` 1012-1053, `releaseHandle` 1055-1113, `reserveHandle` 1115-1147, `unreserveHandle` 1149-1180.
- **Collabs:** `closeCollab` 1182-1263.

**Hooks and client state, sub-components, orchestration:** none. The client forms own the state.

## 4. Proposed tree

```
admin/src/lib/actions.ts           ~25   the barrel; NO directive; re-exports the 20 actions
admin/src/lib/actions/
  support.ts                       ~60   server-only, NO directive
  auth-admin.ts                    ~50   server-only, NO directive
  accounts.ts                      ~300  'use server'
  announcements.ts                 ~225  'use server'
  flags.ts                         ~320  'use server'
  profiles.ts                      ~270  'use server'
  collabs.ts                       ~95   'use server'
```

**The two kinds of leaf**
- **Action leaves** (`accounts`, `announcements`, `flags`, `profiles`, `collabs`):
  - Line 1 is `'use server';`, followed by one comment line: `// The rule in ../actions.ts holds here: every export opens with await requireAdmin().`
  - Then imports. They export only the actions they define, and every other declaration stays private.
- **Support leaves** (`support`, `auth-admin`):
  - Line 1 is `import 'server-only';`, and they carry **no directive**.
  - They export plain helpers for the action leaves. A directive here would turn each exported async helper into a public endpoint (§10).

**Per file.** What moves, with the declaration's current range.

- **`support.ts`**
  - Moves: `Tx` 21-22, `EMAIL_RE` 30, `UUID_RE` 32-36, `normalizeEmail` 38-41, `withOwnedLicense` 377-410, `message` 412-414.
  - Imports: `import type postgres from 'postgres'`; `db` from `../db`.
  - Exports added: all six.
  - Why together: the shared vocabulary of a mutating form, meaning the transaction type, the id and email checks, the ownership transaction and the error text. `UUID_RE`'s comment is generic ("any uuid a form hands back"), even though only `closeCollab` uses it today.
- **`auth-admin.ts`**
  - Moves: `deleteAuthUser` 336-375.
  - Imports: `secretKey`, `supabaseUrl` from `../env`; `message` from `./support`.
  - Export added: `deleteAuthUser`.
  - Why its own file: it is the only code that calls an external HTTP API with the service key. Keeping it out of every `'use server'` file means no action leaf reads `secretKey()`.
- **`accounts.ts`** (`'use server'`)
  - Moves: `createAccount` 52-112, `setPlan` 114-155, `setStatus` 157-199, `deleteAccount` 201-334.
  - Imports: `revalidatePath`; `requireAdmin`; `writeAudit`; `db`; `generateKey`, `type Plan` from `../shared`; `type ActionState`; `normalizeEmail`, `withOwnedLicense`, `message` from `./support`; `deleteAuthUser` from `./auth-admin`.
- **`announcements.ts`** (`'use server'`)
  - Moves: `ANNOUNCE_KEY_RE` 425-432 (only the announcements header at 425-430 comes with it; the flags header and the orphaned doc at 416-424 go to `flags.ts`), `VERSION_RE` 433, `announceCta` 435-457, `saveAnnouncement` 459-553, `saveAnnouncementRule` 555-609, `deleteAnnouncementRule` 611-632.
  - Imports: `revalidatePath`; `redirect`; `requireAdmin`; `writeAudit`; `db`; the five announcement lists from `../shared` under their existing `ANNOUNCE_*` aliases; `type ActionState`; `type Tx` from `./support`.
- **`flags.ts`** (`'use server'`)
  - Moves: `FLAG_KEY_RE` 43-46, `FLAG_VERSION_RE` 48-50, the flags section header and the orphaned doc 416-424, placed directly above `createFlag` 634-675, `flagList` 677-687, `flagChoices` 689-693, `updateFlag` 695-730, `deleteFlag` 732-771, `saveRule` 773-850, `deleteRule` 852-875, `moveRule` 877-924.
  - Imports: `revalidatePath`; `redirect`; `requireAdmin`; `writeAudit`; `db`; `type ActionState`; `EMAIL_RE`, `type Tx` from `./support`.
- **`profiles.ts`** (`'use server'`)
  - Moves: `suspendProfile` 926-973, `unsuspendProfile` 975-1010, `clearProfileText` 1012-1053, `releaseHandle` 1055-1113, `reserveHandle` 1115-1147, `unreserveHandle` 1149-1180, in this order.
  - Imports: `revalidatePath`; `requireAdmin`; `writeAudit`; `db`; `type ActionState`; `normalizeEmail`, `message`, `type Tx` from `./support`.
- **`collabs.ts`** (`'use server'`)
  - Moves: `closeCollab` 1182-1263.
  - Imports: `revalidatePath`; `requireAdmin`; `writeAudit`; `db`; `type ActionState`; `UUID_RE`, `message`, `type Tx` from `./support`.

In every action leaf the imports resolve to `../auth`, `../audit`, `../db`, `../shared` and `../action-state`.

**Accounting.** support 6 + auth-admin 1 + accounts 4 + announcements 6 + flags 10 + profiles 6 + collabs 1 = **34**, one per top-level declaration. The directive becomes one per action leaf and none on the barrel.

**Do not merge `FLAG_VERSION_RE` and `VERSION_RE`.** They are two equivalent, unpinned copies of `parseVersion`'s strictness, and unifying them is a rule change rather than a move. Filed as proposal 70.

**Import graph.** Acyclic, and no leaf imports the barrel.

```
support       -> ../db
auth-admin    -> ../env, support
accounts      -> ../auth, ../audit, ../db, ../shared, ../action-state, support, auth-admin, next/cache
announcements -> ../auth, ../audit, ../db, ../shared, ../action-state, support, next/cache, next/navigation
flags         -> ../auth, ../audit, ../db, ../action-state, support, next/cache, next/navigation
profiles      -> ../auth, ../audit, ../db, ../action-state, support, next/cache
collabs       -> ../auth, ../audit, ../db, ../action-state, support, next/cache
actions.ts    -> accounts, announcements, flags, profiles, collabs      (never support or auth-admin)
client forms  -> actions.ts                                            (unchanged)
```

## 5. The entry point afterwards

`admin/src/lib/actions.ts` stays at its path, reduced to this barrel:

```ts
// Every export in this file mutates something, and every one of them opens
// with `await requireAdmin()`. That call is not a formality and is not covered
// by the proxy: a server action is a POST to an endpoint Next generates, and
// the check that protects a row has to live in the same function as the query.
// If you add an action here, it starts with requireAdmin() or it does not ship.
//
// The actions live in ./actions/, each file under 'use server'. This file
// re-exports them and nothing else: it is imported by client components, so a
// helper re-exported here would pull server-only code into the browser bundle.

export { createAccount, deleteAccount, setPlan, setStatus } from './actions/accounts';
export { deleteAnnouncementRule, saveAnnouncement, saveAnnouncementRule } from './actions/announcements';
export { createFlag, deleteFlag, deleteRule, moveRule, saveRule, updateFlag } from './actions/flags';
export {
  clearProfileText, releaseHandle, reserveHandle, suspendProfile, unreserveHandle, unsuspendProfile,
} from './actions/profiles';
export { closeCollab } from './actions/collabs';
```

**How it keeps §2's contract**
- **Same 20 names and signatures.** The export lister's output for `src/lib/actions.ts` is identical before and after.
- **Same import path.** `@/lib/actions` still resolves to the file, since a file with an extension is tried before the directory. The six client components are untouched.
- **Same "use server" semantics.** Each action is still defined in a module whose first line is `'use server'`, so each is still a server action.
  - A client component imports the plain barrel, and the barrel re-exports from `'use server'` modules. The client layer turns those modules into server-reference stubs, so their bodies and their `server-only` imports never reach the browser.
  - The build proves this: the leaves import `../db` and `../auth`, which are `server-only`, so a leaked body fails `next build`. §8 adds a grep of the client output.

**Why the barrel has no `'use server'`**
- **The docs back the leaf.** They say to create server functions in a dedicated file with the directive at the top (`use-server.md:48`), which is exactly what each leaf is.
- **The re-export route is unverified.** A `'use server'` barrel of `export { … } from` lines depends on the compiler accepting re-exports under the directive. Next's bundled docs do not document that, and it cannot be confirmed offline.
- **The rule stays local.** With the directive on the leaf that defines the function, "every export opens with `requireAdmin()`" can be checked file by file (§8, step 5).

**Why not `actions/index.ts`:** the same reasons as `admin-accounts.md` §5. The entry path keeps its prose pointers, and with both present `actions.ts` wins silently.

## 6. Files over 250 lines

- **`flags.ts`, ~320.** Six actions over two tables, `private.feature_flags` and `private.feature_flag_rules` (deleting a flag cascades to its rules), sharing `FLAG_KEY_RE`, `FLAG_VERSION_RE`, `flagList` and `flagChoices`.
  - The natural seam would be flag CRUD against rule CRUD, but `deleteFlag` reads the rules to audit them and `saveRule` locks the flag row.
  - Splitting would put two halves of one lock order and one key rule in different files. Kept whole.
- **`accounts.ts`, ~300.** Four actions over the licence row. `deleteAccount` alone is 134 lines, mostly the reasons it releases the handle without reserving it and audits the destroyed profile text. The others share `withOwnedLicense`'s ownership rule. Kept whole.
- **`profiles.ts`, ~270.** Six moderation actions: three on profile text and state, three on the handle pool. admin-moderation pins them as one set. Splitting text from handles would split a set that test asserts together. Kept whole.

## 7. Build and packaging touch points

**No change needed**
- **tsconfig.json, eslint.config.mjs, next.config.mjs:** new files fall under `@/*` and `include`; no server-actions config exists or is needed. `next.config.mjs` sets no `serverActions` options (body size, allowed origins), so moving the functions changes nothing there.
- **.github/workflows/admin-ci.yml:** the `admin/**` filter triggers it. `check:secrets` scans client-served files for the secret values, but `env.ts` reads them lazily at runtime, so it would not catch a leaked action body; the build's `server-only` failure and the grep in §8 do.
- **scripts/worktree-init.sh:** it already links `admin/node_modules`.
- **TypeScript 6.0.3 and typescript-eslint:** no change.

**Tests that change in the same PR.** They only re-point file paths; no assertion changes.
- **`test/server/admin-moderation.test.mjs`:**
  - `body(name)` for the six moderation actions reads `lib/actions/profiles.ts`.
  - `body('deleteAccount')` reads `lib/actions/accounts.ts`. `deleteAccount` is the last export there, so its body now runs to the end of the file, which changes nothing the assertions look for.
  - The "never sets a handle" regex runs over `lib/actions.ts` plus every file in `lib/actions/`. Concatenating is correct here because it is a "nowhere" assertion.
- **Recommended addition,** the one thing here that is not a re-point. Put it in `admin-moderation.test.mjs`, or a new `test/server/admin-actions-boundary.test.mjs`, with four assertions:
  - (a) every `export async function` in a `lib/actions/*.ts` file whose first line is `'use server'` contains `await requireAdmin()`
  - (b) `support.ts` and `auth-admin.ts` do not start with a directive
  - (c) `lib/actions.ts` has no directive and contains only comments and `export { … } from './actions/<leaf>'`
  - (d) no file outside `lib/actions/` imports `@/lib/actions/`

  (a) generalises the moderation test's "authorises before it mutates" from 6 actions to all 20, and it is what catches §10's worst hazard in either form. Eric's call whether it rides with the move.

**CI reach.** An `admin/**`-only PR skips `npm test` (`scripts/ci-changes.sh:54`). This PR also edits `test/server/**`, so `ci.yml` runs it.

**Comments updated in the same PR**
- The header of `lib/action-state.ts`: "outside actions.ts" becomes "outside lib/actions/".
- The comment at `lib/accounts.ts:1658`, or its new home if the accounts split landed first, becomes `lib/actions/accounts.ts`.

**Left alone:** `server/supabase/functions/account/index.ts:23,57`, which is proposal 71. Editing an Edge Function triggers `server-ci.yml` and a redeploy through `server-deploy.yml`.

## 8. Verification recipe

**Tier.** The admin's own gate is `npm run verify` (`admin/README.md` § Verifying a change).

**The server-changes skill does not apply.** No file under `server/**` changes. The actions write the Supabase contract (`private.release_handle(email, reserve, note)`, the tables in §1), but every SQL string moves byte for byte, and step 4 proves it.

Scripts: `exports.cjs` and `verify-moves.cjs`, reproduced in full in `spud:docs/spikes/bad-036/admin-accounts.md` §8. `$S` is a scratch directory outside the checkout.

**0. Baseline, before the first edit.**

```bash
cd admin
node $S/exports.cjs . src/lib/actions.ts > $S/actions.exports.before
git show origin/main:admin/src/lib/actions.ts > $S/actions.old.ts
```

**1. The admin CI build.** Run in `admin/` with the same fake environment CI uses: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, `LICENSING_DB_URL` and `ADMIN_EMAILS`, with the values in `admin-accounts.md` §8 step 1. Then `npm run verify` (typecheck, lint, build, `check:standalone`, `check:secrets`).

**2. Tests.** From the repo root:

```bash
node --test test/server/admin-moderation.test.mjs test/server/admin-pool.test.mjs \
  test/server/admin-client-boundary.test.mjs
```

Add the boundary test file if §7's recommendation is adopted.

**3. Symbol diff.** Expect no output from `diff`: 20 identical lines.

```bash
cd admin
node $S/exports.cjs . src/lib/actions.ts > $S/actions.exports.after
diff $S/actions.exports.before $S/actions.exports.after
```

**4. Pure-move check.**

```bash
cd admin
node $S/verify-moves.cjs $S/actions.old.ts src/lib/actions.ts src/lib/actions/*.ts
```

- **Expected result:** `OK: 34 statements, every one moved exactly once`, exit 0. The directive is skipped, so five directives against one is not a difference.
- **`COMMENT ADDED`:** only the barrel's second paragraph and the five one-line pointers at the top of the action leaves.
- **`COMMENT LOST`:** none. The rule block (24-28) moves verbatim to the barrel, and the flags header and orphaned doc (416-424) move verbatim to `flags.ts`.

**5. The directive audit** (what the build cannot fully see):
- **Directives in place.** `head -1` of `accounts`, `announcements`, `flags`, `profiles` and `collabs` prints `'use server';`. `head -1` of `support.ts` and `auth-admin.ts` prints `import 'server-only';`. `lib/actions.ts` has no directive.
- **Every action authorises first.** Every `export async function` in an action leaf opens with `await requireAdmin()`:

  ```bash
  cd admin/src/lib/actions && for f in accounts announcements flags profiles collabs; do
    node -e "const s=require('fs').readFileSync('$f.ts','utf8');const bad=[...s.matchAll(/export async function (\w+)\([^)]*\)[^{]*\{\s*(const admin = )?await requireAdmin\(\)|export async function (\w+)/g)].filter(m=>m[3]).map(m=>m[3]);console.log('$f', bad.length?'MISSING requireAdmin: '+bad:'ok')"
  done
  ```

- **No action body in the browser.** After `npm run build`, `grep -rlE "release_handle|auth/v1/admin/users|feature_flag_rules" admin/.next/static` prints nothing.

**6. Runtime smoke.** The local stack plus seed, as in `admin/README.md` § Local development: `npm run migrate` or the local Supabase stack on podman, then `npm run seed`, `npm run dev`, and `npm run dev:code -- <ADMIN_EMAILS address>`. Submit one form per action leaf and confirm each writes one row on `/audit`:
- **accounts:** Plan on `/accounts/brody@seed.badtakes.test`, set to paid and back.
- **profiles:** reserve, then unreserve, a handle at `/accounts/handles/reserved`.
- **flags:** at `/flags`, create a flag, add a rule, move it, delete the rule, delete the flag. This exercises `redirect()` from `deleteFlag`.
- **announcements:** at `/announce/new`, save a draft, which exercises the create redirect; add a rule; delete it.
- **collabs:** `closeCollab` on `/collabs/<id>`, if the local database has a collab row. The seed creates none, so skip it when absent and say so in the PR.

The seed also has no profiles, so suspend and clear-text can only be exercised on an account that has claimed a handle. Their bodies are proven byte-identical by step 4.

## 9. Phase 2 order

Two PRs, in Eric's order. There are no sub-components; the action leaves are the services, and the barrel is the reduced entry.

**PR 1: shared types, pure utilities and services**
- **Creates:** `support.ts` (`Tx`, `EMAIL_RE`, `UUID_RE`, `normalizeEmail`, `message`, `withOwnedLicense`) and `auth-admin.ts` (`deleteAuthUser`).
- **In `actions.ts`:** it keeps `'use server'` and all 20 actions, and imports the seven helpers. Its only exports are still the 20 async actions, so the intermediate state is itself legal.
- **Adds:** the boundary test from §7, if adopted, so the new support leaves are guarded from their first commit.
- **Proof:** §8, steps 0-5. The move check runs over `actions.ts` plus the two support files and reports 34.

**PR 2: action leaves and the barrel**
- **Creates:** `accounts.ts`, `announcements.ts`, `flags.ts`, `profiles.ts`, `collabs.ts`. `actions.ts` becomes the barrel in §5.
- **Test re-points:** admin-moderation.
- **Comments:** updates the `action-state.ts` and `accounts.ts` comments.
- **Proof:** §8, steps 0-6, with a fresh baseline from `main` after PR 1 merges.

**Coordination.** Independent of the other two spikes. The account page uses client controls that import `@/lib/actions`, whose path does not change.

## 10. Risks

- **An exported helper in a `'use server'` file becomes a public, unauthenticated endpoint.**
  - The build refuses only *non-async* exports; an async helper exported from a directive file compiles.
  - `deleteAuthUser(email)`, which deletes any Supabase Auth user by address with the service key, and `withOwnedLicense` are async.
  - Hazard (a): export either from an action leaf. Hazard (b): add `'use server'` to `support.ts` or `auth-admin.ts`. Either would publish it.
  - For `support.ts`, (b) is loud: it also exports non-async values (`EMAIL_RE`, `normalizeEmail`, `message`), so the build fails. For `auth-admin.ts`, which exports one async function, (b) compiles silently.
  - The same one-keyword hazard exists today (`export` on `deleteAuthUser` inside `actions.ts`); the split does not make it worse.
  - §8 step 5 and the recommended boundary test catch both forms: `deleteAuthUser` has no `requireAdmin()`, and support files must not carry a directive.
- **Directive in the wrong place.**
  - **Directive on the barrel and none on the leaves:** the leaves become plain modules exporting action bodies. Any client component importing a leaf by path would bundle server code; the build fails on `server-only`.
  - **No directive anywhere:** the barrel pulls action bodies into the client graph, and the build fails the same way. Both failures are loud.
  - **A quieter one:** a server component importing a leaf directly calls the function in-process, which is harmless today.
- **Circular imports:** none possible within the tree, since support leaves import no action leaf. A leaf that imports `'../actions'` (the barrel) would make one. **Check:** `grep -rn "'\.\./actions'\|'@/lib/actions'" admin/src/lib/actions/` prints nothing.
- **Sibling name collisions.** `lib/actions/flags.ts`, `profiles.ts` and `collabs.ts` sit one level below `lib/flags.ts`, `lib/profiles.ts` and `lib/collabs.ts`. Inside `lib/actions/`, `./flags` is the leaf itself. The actions import none of those lib modules today; any future one must be `../flags`.
- **Server-action ids change.** An action's id derives from its module and export name, so after deploy a dashboard tab opened before it gets "Failed to find Server Action" on its next submit, and a reload fixes it. This is an internal operator tool, so accept it and say so in the PR body.
- **Function boundaries.** `redirect()` must stay outside each `try` (`saveAnnouncement`, `deleteFlag`). Moving whole declarations keeps that, so never split an action.
- **`body()` boundaries in admin-moderation.** They are per file after the re-point. Keep each action leaf's exports as whole `export async function` declarations; a non-exported helper between two actions would land inside the preceding action's body.
- **What the build cannot see.** The SQL text, which only step 4 checks. Which function is an endpoint, which only step 5 checks. Behaviour against a real database, which only step 6 checks.
