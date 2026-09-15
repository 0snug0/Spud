# BAD-036 spike: splitting `server/supabase/functions/collab/index.ts`

Phase 1 plan by BADS-036/Sebago (05, architect). Read-only survey of BadTakes at
`main` `20d11e1`. Every line number below is that commit's.

## 1. The file today

- **Path:** `server/supabase/functions/collab/index.ts`, **1230 lines**.
- **Runtime:** Supabase Edge Runtime (Deno 2). Transpiled, never type-checked by
  anything in CI. `deno check` does run on this Mac (deno 2.9.5): today it reports
  **exactly 3 errors, all TS7006** (implicit `any` on `m`, `c`, `t` in the `info`
  branch at 748, 763, 774). That baseline is the tool for section 8.
- **How it is loaded:** it is the function's entrypoint. The Supabase CLI finds
  it by globbing `functions/*/index.ts`. Locally, `supabase functions serve`
  mounts the whole `functions/` directory. In production,
  `supabase functions deploy --use-api` (in `.github/workflows/server-deploy.yml`)
  bundles each entrypoint's **import graph** on the server. `_shared/` has no
  `index.ts`, so it ships only through those imports (see the comment above the
  "Deploy Edge Functions" step). It calls `Deno.serve` once, at module scope.
- **Imports:** only `_shared/` ESM modules, all by relative path with an
  explicit extension: `token.mjs`, `takeover.mjs`, `flags.mjs`, `s3.mjs`,
  `storage.mjs`, `collab.mjs`, `db.mjs` (which brings in `npm:postgres@3.4.5`) and
  `respond.mjs` (lines 1-20). Nothing imports this file.
- **Who calls it:** `POST /functions/v1/collab` from
  - the desktop main process: `collabFetch` in `main.js:3301-3322`, sending
    `{token, mode, app_version, platform: process.platform, ...extra}`;
  - the web and iOS app: `collabFetch` in `web/collabweb.js:100-116`, sending
    `platform: 'web'`;
  - the integration suites: the `collab` helper in
    `test/server/functions.test.mjs:436` and `test/server/account.test.mjs:247`.
- **Config:** `[functions.collab] verify_jwt = false` in
  `server/supabase/config.toml:123-124`. There is no `deno.json`, no import map
  and no `entrypoint` or `static_files` key anywhere under `server/`
  (`find server -name 'deno.json*' -o -name 'import_map*'` finds nothing).
- **Precedent:** every one of the 18 functions is a single `index.ts`
  (`find server/supabase/functions -type f -not -path '*/_shared/*'`). This split
  would make `collab` the first function with sibling modules. The code has been
  moved out once before: `_shared/storage.mjs:3`, "Moved out of collab/index.ts
  when account deletion gained a second caller."

## 2. Public surface (the compatibility contract)

### 2a. HTTP: gate refusals every mode meets, in this order (lines 281-352)

| Order | Condition | Status | Body |
| --- | --- | --- | --- |
| 1 | `OPTIONS` | 200 | `ok`, with CORS headers from `respond.mjs` (the web app needs these) |
| 2 | method is not POST | 405 | `{error,code:'method_not_allowed'}` |
| 3 | body is not a JSON object | 400 | `invalid_json` |
| 4 | `mode` not in `MODES` | none | **quietly becomes `info`** (line 292) |
| 5 | `ACTIVATION_PUBLIC_KEY` unset or invalid | 500 | `activation_not_configured` |
| 6 | over 600/h on `collab:<lid>`, or `collab:ip:<ip>` when there is no lid | 429 | `rate_limited` (spent **before** the token is judged) |
| 7 | `join` over 30/h on `collabjoin:ip:<ip>` | 429 | `rate_limited` |
| 8 | no valid `lid` or `mid` in the token | 400 | `invalid_token` |
| 9 | `decideValidation` refuses | 403 | `{error,code: superseded \| revoked \| unknown_license}`. `main.js` feeds this to `noteLicenseVerdict`; `web/collabweb.js:108` signs out |
| 10 | licence has no email | 403 | `no_email` |
| 11 | `collab` flag is off, or flags could not be read | 403 | `flag_off` (**fails closed**; `web/collabweb.js:110` parks the feature) |

`fail(reason, status)` always answers `{error: reason, code: reason}`
(`respond.mjs:24`). Clients read `data.code || data.error`.

### 2b. HTTP: modes (who sends each: `grep -o "collabFetch('<mode>'" main.js web/collabweb.js`)

**Unscoped modes** run before the membership gate.

| Mode | Lines | Request fields | Success | Refusals | Sent by |
| --- | --- | --- | --- | --- | --- |
| `create` | 366-426 | `title`, `digest` (64 hex), `display`, `visibility`, `character_count`, `scene_cast` | 200 `{collab_id, member_id, code, visibility}` | 400 `missing_digest`; 403 `{code: unknown_license\|too_many_open\|already_hosting}` from SQL; 500 `code_mint_failed`; any other DB error is thrown (runtime 500) | main, web |
| `join` | 428-469 | `code`, `display` | 200 `{allowed:true, rejoined, collab_id, member_id, title, digest, scene_ready}` | 404 `{allowed:false, reason:'unknown_code'}`; 403 `{allowed:false, reason}` from `decideJoin` or `join_collab`. **A different shape: no `code` key** | main, web |
| `list` | 471-539 | none | 200 `{collabs: publicCollabRow[]}`, at most 24; signs a thumbnail URL per row | none | main, web |
| `mine` | 541-602 | none | 200 `{sessions:[{id, code, role, member_id, phase, title, scene_digest, scene_ready, expires_at, went_live_at, wrap_expires_at, counts:{members,claims,takes}}]}`, at most 24 | none | main, web |
| `report` | 604-631 | `collab_id` or `code`, `reason` | 200 `{ok:true}` | 400 `missing_reason`; 404 `unknown_collab` | main |

**Membership gate** (633-686), for every mode below: 400 `missing_collab`,
404 `unknown_collab`, 403 `not_a_member`. **It writes:** a `wrapping` room with
no fresh recording seat becomes `wrapped` on read, and the handler after it sees
the mutated row (`Object.assign(collab, done)`, 672).

| Mode | Lines | Request fields | Success | Refusals | Sent by |
| --- | --- | --- | --- | --- | --- |
| `info` | 688-788 | none | 200 `{collab:{id, code, title, status, phase, visibility, went_live_at, wrap_expires_at, castable, multi_character, scene_cast, digest, scene_ready, expires_at, member_id, role}, members:[{id,display,role}], claims:[{character, memberId, claimedAt, lastSeenAt, recordingAt}], takes:[{memberId, lineId, characterKey, savedAt, bytes}], now}` | none | main, web |
| `heartbeat` | 794-826 | `character_key`, `recording` | 200 `{ok:true, phase}`; 200 `{ok:false, phase}` when the room is not writable | none | main, web |
| `claim` / `release` | 828-872 | `character` | claim: 200 `{character, memberId, mine:true, transferred, claimedAt}`. release: 200 `{character, released}` | 403 `wrapping\|closed`; 400 `missing_character`; claim adds 409 `{code:'character_taken', character, memberId}` and 403 for other SQL refusals | main (both), web (claim) |
| `update` | 874-911 | `visibility`, `castable`, `multi_character` | 200 `{ok:true}` | 403 `host_only\|closed\|locked` | main, web |
| `start` | 913-934 | none | 200 `{ok:true, phase:'live'}` (idempotent) | 403 `decideStart` reason | main, web |
| `wrap` | 936-958 | none | 200 `{ok:true, phase: closed\|wrapping\|wrapped}`; `closed` deletes the objects | 403 `host_only`; 403 `{error, code, phase}` from SQL | main, web |
| `reopen` | 960-969 | none | 200 `{ok:true, phase}` | 403 `host_only`; 403 `{error, code, phase}` | main, web |
| `upload-url` | 971-1035 | `kind` (`thumb`\|`scene`\|take), `bytes`, `line_id`, `cam_bytes` | thumb or scene: 200 `{url, path}`. take: 200 `{url, sidecar_url, cam_url?}` | 403 `closed\|host_only`; 413 `{code: thumb_too_large\|scene_too_large\|take_too_large\|cam_too_large, max}`; 400 `bad_collab\|missing_digest\|bad_line_id`; 502 `storage_unavailable` | main, web |
| `commit` | 1037-1148 | `kind`, `bytes`, `line_id`, `saved_at`, `character`, `cam_bytes` | thumb or scene: 200 `{ok:true}`. take: 200 `{ok:true, line_id, saved_at}`. Also runs the backstop start gun and wrap completion | 403 `closed\|host_only`; 400 `bad_commit` | main, web |
| `download-url` | 1150-1192 | `kind:'scene'`, or `member_id` and `line_id` | scene: 200 `{url, digest, bytes}`. take: 200 `{url, sidecar_url, cam_url?, saved_at}` | 403 `closed`; 410 `scene_expired`; 400 `bad_request`; 404 `unknown_take`; 502 `storage_unavailable` | main, web |
| `leave` | 1194-1204 | none | 200 `{ok:true}` | 403 `closed`; 400 `host_cannot_leave` | web |
| `close` | 1206-1227 | none | 200 `{ok:true}`; deletes the objects | 403 `host_only\|closed` | main, web |
| (none) | 1229 | none | none | 400 `unknown_mode`. **Unreachable** because of rule 4 above. Keep it anyway | none |

Refusal codes clients actually branch on (grepped across `main.js`,
`web/collabweb.js` and `renderer/app.js`): `already_hosting`, `character_taken`,
`bad_phase`, `wrapping`, `locked`, `closed`, `expired`, `scene_expired`,
`host_only`, `not_a_member`, `unknown_collab`, `cam_too_large`,
`scene_too_large`, `flag_off`, `superseded`, `revoked`, `unknown_license`.

### 2c. Environment, logs, database

- **Env vars:** `ACTIVATION_PUBLIC_KEY`, `SUPABASE_URL` and
  `SUPABASE_SERVICE_ROLE_KEY` (Storage REST, 130-134), the four `R2_*` keys plus
  `R2_ENDPOINT` (read by name through `r2EnvFrom`, 108), and
  `LICENSING_DB_URL`/`SUPABASE_DB_URL` through `db.mjs`.
- **Boot log line** (121-126), printed once per isolate:
  `collab: storage backend=r2 bucket=…` or
  `collab: storage backend=supabase (R2 partially set, missing: …)`.
  `server/CLAUDE.md` names it "the thing not to remove". It is the only
  production signal for which backend is live.
- **Error log prefixes** `collab: …` (143, 153, 165, 175, 274, 296, 303, 350)
  are what anyone searches the logs for.
- **Database calls:** `private.create_collab` (10 bound arguments),
  `join_collab`, `claim_character`, `wrap_collab`, `reopen_collab`. Tables
  `collabs`, `collab_members`, `collab_claims`, `collab_takes`, `collab_reports`,
  `profiles`, `licenses`, plus `rate_limits` and the sweep lease through `db.mjs`.

### 2d. Tests and checks that read this file's *text*

These are part of the contract: the split moves the text they match.

| Test | What it reads | What happens after the split |
| --- | --- | --- |
| `test/server/collab.test.mjs:354-363` | `index.ts` must contain `claimLease(sql, SWEEP_LEASE_KEY` and `SWEEP_MODES.has(mode)`, and must not declare `const SWEEP_MODES` | **Breaks.** `claimLease` moves to `sweep.ts` |
| `collab.test.mjs:534-545` | the `const MODES = new Set([…])` literal and every `mode === '…'` in `index.ts` | **Unchanged**, if the dispatcher keeps `mode === '…'` comparisons (section 5) |
| `collab.test.mjs:554-573` | slices `index.ts` from `if (mode === 'create')` to `if (mode === 'join')`, expecting `create_collab(` with 10 `${}` arguments and no `update private.collabs` | **Breaks.** The body moves to `lobby.ts` |
| `collab.test.mjs:575-588` | slices `index.ts` from `if (mode === 'mine')` to `if (mode === 'report')` | **Breaks.** The body moves to `browse.ts` |
| `test/server/schema.test.mjs:817-827` | scans **only** `<fn>/index.ts` for a `select … current_machine_id … from private.licenses` that lacks `web_machine_id` | **Goes silent.** The licence read moves to `gate.ts`, and the guard stops seeing it without failing |
| `test/server/jsonb-params.test.mjs` | walks every `.ts`/`.mjs` under `functions/` recursively | Unaffected; it covers new files automatically |
| `server-deploy.yml`, step "Guard against a silent zero-function deploy" | `find supabase/functions -mindepth 2 -maxdepth 2 -name index.ts` | Unaffected, **as long as no new file is named `index.ts`** |
| `schema.test.mjs:276-280` (`functionNames`) | first-level directories that contain an `index.ts` | Unaffected |

## 3. Responsibility map

**Types and constants**
- Imports: 1-20.
- Header doc (overview, auth, the flag wall, the mode list): 22-63.
- `UUID_RE`: 65.
- `MODES`: 66-79.
- `LIST_LIMIT`: 80-83.
- `MINE_LIMIT`: 84-86.
- `UPLOAD_TTL_S`, `DOWNLOAD_TTL_S`: 87-88.
- `SWEEP_LIMIT`: 208.
- There are no named types. The only type expression is `ReturnType<typeof db>` at 210.

**State** (module scope, once per isolate, nothing mutable)
- `R2_ENV` (108) and `R2` (109), plus the boot-log block (111-126), a load-time side effect.
- `removePrefix`/`removeObject` (182-185): closures built by `makeStorage`, bound to `R2`.
- The Postgres client is cached inside `_shared/db.mjs`, not here.

**Pure utilities.** None of the file's own. Every pure rule already lives in
`_shared/collab.mjs` (phase predicates, `decideJoin`, `decideStart`,
`collabExpired`, `sceneZipFresh`, `listableCollab`, `publicCollabRow`, the
`clean*` clamps, object paths), and in `token`, `takeover`, `flags` and `s3`.
The split must import them from there, never copy them (see section 10, the
shared-rule hazard).

**Services and IO**
- Storage REST helpers `storageBase`/`storageAuth` (128-134).
- Signing: `signUpload` (136-158) and `signDownload` (160-180), each R2 or Supabase Storage.
- Deletion: `removePrefix`/`removeObject` (182-185).
- The expiry sweep `sweepExpired` (187-276): the lease, then two bounded passes (Postgres and storage).
- The auth gate (294-352): WebCrypto key import and token verification, two rate-limit upserts, the licence seat read, and the flag wall.

**Route handlers (mode branches)**
- Unscoped: `create` 366-426, `join` 428-469, `list` 471-539, `mine` 541-602, `report` 604-631.
- Member: `info` 688-788, `heartbeat` 794-826, `claim`/`release` 828-872,
  `update` 874-911, `start` 913-934, `wrap` 936-958, `reopen` 960-969,
  `upload-url` 971-1035, `commit` 1037-1148, `download-url` 1150-1192,
  `leave` 1194-1204, `close` 1206-1227.

**Orchestration**
- `Deno.serve` wrapper and request parsing: 278-292.
- Sweep kick-off after the flag gate: 354-364.
- The membership gate, a shared precondition that also writes: 633-686.
- Comment on the retired blanket gate: 790-792.
- `unknown_mode` and the closing brace: 1229-1230.

Every branch is **terminal**: each path returns a `Response` or throws. That is
what makes "branch body becomes function body" a pure move.

## 4. Proposed tree

Flat, inside the function directory. Siblings keep every `../_shared/x.mjs`
specifier byte-identical, while a subdirectory would turn them into
`../../_shared`. No file may be named `index.ts` except the entrypoint.

```
server/supabase/functions/collab/
  index.ts      ≈125  entry: MODES, parse, gate, sweep kick, dispatch
  gate.ts        ≈85  Sql/Caller types, UUID_RE, authorize()
  bucket.ts     ≈105  backend choice + boot log, sign/remove
  sweep.ts       ≈95  SWEEP_LIMIT, sweepExpired()
  lobby.ts      ≈115  createRoom(), joinRoom()
  browse.ts     ≈150  LIST_LIMIT, MINE_LIMIT, listRooms(), mySessions()
  report.ts      ≈35  reportRoom()
  room.ts        ≈75  Room type, loadRoom()
  info.ts       ≈110  roomInfo()
  seats.ts       ≈90  heartbeat(), claimOrRelease()
  lifecycle.ts  ≈150  updateRoom(), startRoom(), wrapRoom(), reopenRoom(), leaveRoom(), closeRoom()
  push.ts       ≈190  uploadUrl(), commitUpload()
  pull.ts        ≈52  downloadUrl()
```

Total is about 1,377 lines: the 1,230 moved, plus imports, 25 function headers
and one destructuring line per handler.

### The move shape

Each `if (mode === 'x') { BODY }` becomes
`export async function handler(c: Caller[, r: Room]): Promise<Response> { const { …names BODY reads… } = c; BODY }`,
with BODY byte-identical apart from one indentation level. Section 8's
body-diff script checks exactly that.

- **The two context types:**
  - `Caller` carries `{ req, body, mode, sql, lid, mid, email, license }`.
  - `Room` carries `{ collabId, collab, me, isHost, wrapDeadline, roomExpired }`.
    `collab` stays **the same object** the gate mutated (never a spread copy).
- **The two gates** return `Promise<Response | Caller>` and
  `Promise<Response | Room>`. Their `return fail(…)` lines stay as they are, and
  the dispatcher checks `instanceof Response`.
- **Return types.** Annotating every handler `Promise<Response>` lets
  `deno check` report any path that no longer returns (TS2366).

### Assignment table (every top-level declaration and every route, exactly once)

| Current lines | Declaration or block | Goes to |
| --- | --- | --- |
| 1-20 | imports | each file imports only what its body uses (rewritten; specifiers unchanged) |
| 22-63 | header doc: courier model, auth, flag wall, mode list | `index.ts` |
| 65 | `UUID_RE` | `gate.ts` (its only reader is 309) |
| 66-79 | `MODES` | `index.ts` (pinned there by `collab.test.mjs:536`) |
| 80-83 | `LIST_LIMIT` | `browse.ts` |
| 84-86 | `MINE_LIMIT` | `browse.ts` |
| 87-88 | `UPLOAD_TTL_S`, `DOWNLOAD_TTL_S` | `bucket.ts` |
| 90-107 | storage banner and backend doc | `bucket.ts` |
| 108-109 | `R2_ENV`, `R2` | `bucket.ts` |
| 111-126 | boot-log block | `bucket.ts` |
| 128-134 | `storageBase`, `storageAuth` | `bucket.ts` |
| 136-158 | `signUpload` | `bucket.ts` (exported) |
| 160-180 | `signDownload` | `bucket.ts` (exported) |
| 182-185 | `removePrefix`, `removeObject` via `makeStorage` | `bucket.ts` (exported) |
| 187-208 | sweep doc and `SWEEP_LIMIT` | `sweep.ts` |
| 210-276 | `sweepExpired` | `sweep.ts` (exported) |
| 278-292 | serve banner, preflight, method, JSON parse, `mode` default | `index.ts` |
| 294-352 | key import, token verify, `const sql = db()`, both rate limits, `invalid_token`, licence and `decideValidation`, `no_email`, the flag wall | `gate.ts` `authorize(req, body, mode)` |
| 354-364 | sweep kick (`SWEEP_MODES.has(mode)`, `EdgeRuntime.waitUntil`) | `index.ts` |
| 366-426 | `create` | `lobby.ts` `createRoom` |
| 428-469 | `join` | `lobby.ts` `joinRoom` |
| 471-539 | `list` | `browse.ts` `listRooms` |
| 541-602 | `mine` | `browse.ts` `mySessions` |
| 604-631 | `report` | `report.ts` `reportRoom` |
| 633-686 | membership gate, wrap on read, `wrapDeadline`, `roomExpired` | `room.ts` `loadRoom` |
| 688-788 | `info` | `info.ts` `roomInfo` |
| 790-792 | comment on the retired blanket gate | `index.ts`, above the member dispatch |
| 794-826 | `heartbeat` | `seats.ts` `heartbeat` |
| 828-872 | `claim` / `release` (one shared branch) | `seats.ts` `claimOrRelease` |
| 874-911 | `update` | `lifecycle.ts` `updateRoom` |
| 913-934 | `start` | `lifecycle.ts` `startRoom` |
| 936-958 | `wrap` | `lifecycle.ts` `wrapRoom` |
| 960-969 | `reopen` | `lifecycle.ts` `reopenRoom` |
| 971-1035 | `upload-url` | `push.ts` `uploadUrl` |
| 1037-1148 | `commit` | `push.ts` `commitUpload` |
| 1150-1192 | `download-url` | `pull.ts` `downloadUrl` |
| 1194-1204 | `leave` | `lifecycle.ts` `leaveRoom` |
| 1206-1227 | `close` | `lifecycle.ts` `closeRoom` |
| 1229-1230 | `unknown_mode` and close of `Deno.serve` | `index.ts` |

### Why these groups

| File | Responsibility |
| --- | --- |
| `gate.ts` | Who the caller is and whether they may use the feature at all |
| `bucket.ts` | The courier's one seam to storage. The original comment at 98-100 already calls these four functions the seam |
| `sweep.ts` | Housekeeping that rides on requests |
| `lobby.ts` | Entering a room: the two modes that mint membership |
| `browse.ts` | Read-only views across rooms, and the only place the page limits are used |
| `report.ts` | Moderation evidence |
| `room.ts` | The shared "you belong to this room" precondition |
| `info.ts` | The member snapshot the panel polls |
| `seats.ts` | Character ownership and presence |
| `lifecycle.ts` | The host's phase controls and room options, plus a guest's exit |
| `push.ts` | Upload a take or the scene, then record it. Both halves share the per-kind phase split, and `commit` carries the phase machine's backstops |
| `pull.ts` | Signed reads |

### Import graph (acyclic; nothing imports `index.ts`)

```
index.ts ─┬─> gate.ts ──────> _shared/{token,takeover,flags,db,respond}.mjs
          ├─> sweep.ts ─┬───> bucket.ts ─> _shared/{s3,storage,collab}.mjs
          │             └───> _shared/{db,collab}.mjs, (type) gate.ts
          ├─> lobby.ts, browse.ts*, report.ts, room.ts, info.ts,
          │   seats.ts, lifecycle.ts*, push.ts*, pull.ts*
          │     each ─> _shared/{collab,respond}.mjs, (type) gate.ts, (type) room.ts
          │     (* also ─> bucket.ts)
          └─> _shared/{respond,collab}.mjs   (fail, preflight, SWEEP_MODES)
```

Imports of `Caller`, `Room` and `Sql` use `import type`, which Deno erases
entirely.

## 5. The entry point afterwards

`server/supabase/functions/collab/index.ts` stays the entrypoint, so the slug,
the URL, the `config.toml` block and the deploy glob do not move. It keeps:

- the header doc (22-63) and the `MODES` Set literal (66-79), byte for byte;
- `Deno.serve` with the parsing lines 281-292 verbatim, including rule 4
  (an unknown mode becomes `info`);
- the gate call: `const c = await authorize(req, body, mode); if (c instanceof Response) return c;`
- the sweep kick (354-364) verbatim, reading `c.sql`. It stays after the flag
  gate and before any mode, as today;
- the dispatch, **in today's branch order** and as literal `mode === '…'`
  comparisons, which keeps `collab.test.mjs:534` passing untouched:

```ts
if (mode === 'create') return createRoom(c);
if (mode === 'join') return joinRoom(c);
if (mode === 'list') return listRooms(c);
if (mode === 'mine') return mySessions(c);
if (mode === 'report') return reportRoom(c);
const r = await loadRoom(c);
if (r instanceof Response) return r;
if (mode === 'info') return roomInfo(c, r);
// 790-792 comment
if (mode === 'heartbeat') return heartbeat(c, r);
if (mode === 'claim' || mode === 'release') return claimOrRelease(c, r);
if (mode === 'update') return updateRoom(c, r);
if (mode === 'start') return startRoom(c, r);
if (mode === 'wrap') return wrapRoom(c, r);
if (mode === 'reopen') return reopenRoom(c, r);
if (mode === 'upload-url') return uploadUrl(c, r);
if (mode === 'commit') return commitUpload(c, r);
if (mode === 'download-url') return downloadUrl(c, r);
if (mode === 'leave') return leaveRoom(c, r);
if (mode === 'close') return closeRoom(c, r);
return fail('unknown_mode', 400);
```

Why the contract holds:

- **Status codes and bodies.** Every status and body in section 2 is produced by
  the same lines, in the same order, reading the same values.
- **Errors.** A rejected handler promise (`create`'s `throw err`) propagates out
  of the serve callback exactly as the inline `throw` did.
- **Rate limits.** They are still spent before `invalid_token`.
- **The room row.** Wrap on read still mutates the one `collab` object `info`
  then reads.
- **Tests that read this file.** The three section-2d tests that stop matching
  are retargeted in the same PR that moves their code (section 9); the
  `MODES` test needs nothing.

## 6. Files over 250 lines

**None.** The largest is `push.ts` at about 190 lines: `upload-url` (65) and
`commit` (112) plus imports and two headers. It stays whole because both
branches implement one protocol:
- they share the per-kind phase gate (`settingUp ? joinablePhase : writablePhase`, 976-977 and 1039-1040);
- they share the take, sidecar and cam path triple;
- `commit` carries the two phase-machine side effects: the backstop start gun
  (1116-1122) and wrap completion (1128-1145).

Splitting upload from commit would put the two copies of the phase rule in
different files.

Next largest are `browse.ts` and `lifecycle.ts`, each about 150 lines.

## 7. Build and deploy touch points

- **`config.toml`:** no change. `[functions.collab] verify_jwt = false` stays.
  No `entrypoint`, `import_map` or `static_files` key is needed, because every
  new specifier is relative (`./gate.ts`) with its extension.
- **`deno.json` or an import map:** **do not add one.** None exists in
  `server/`. The CLI picks up a per-function `deno.json` and would change how
  this one function resolves imports.
- **`server-deploy.yml`:** no change. `supabase functions deploy --use-api` has
  no slug list and bundles each entrypoint's import graph, the same mechanism
  that already ships `_shared/`. The zero-function guard still counts 18. Every
  merge to `main` that runs Server integration **redeploys every function**,
  including the PR that only adds tests.
- **`server-ci.yml`:** no change is needed for the split. PR 1 (section 9)
  edits it. The comment above "Start the stack" is already stale: "`collab` …
  is not among the thirteen functions functions.test.mjs exercises". In fact
  `functions.test.mjs:2233`/`2290` and `account.test.mjs:300` call it, and
  `account` touches storage too. If PR 1 adds signed-URL success tests, it has
  to remove `storage-api` from the `-x` list.
- **`ci.yml`:** runs `npm test`, which includes the pure and text-reading
  tests in `test/server/` (`collab.test.mjs`, `schema.test.mjs`,
  `jsonb-params.test.mjs`) and the integration tests, which skip there.
- **Tests edited alongside the move** (the tests themselves are not split):
  - `collab.test.mjs:354-363` retargets to `sweep.ts` (lease) and `index.ts` (gate);
  - `collab.test.mjs:554-573` retargets to `createRoom` in `lobby.ts`;
  - `collab.test.mjs:575-588` retargets to `mySessions` in `browse.ts`;
  - `schema.test.mjs:817-827` widens to every `.ts` under each function directory.
- **Comments that name `collab/index.ts`:** update to the new file where
  they describe live code.
  - `_shared/flags.mjs:27` ("collab/index.ts imports normalizeContext" now means `gate.ts`)
  - `server/CLAUDE.md:752` (same)
  - `account/index.ts:38` (the boot line is now in `bucket.ts`) and `account/index.ts:236` (the sweep backgrounding stays in `index.ts`)
  - `main.js:3294` (the wall is now in `gate.ts`)
- **Leave historical references alone:** `_shared/storage.mjs:3`, everything
  under `docs/superpowers/`, and `migrations/0039_account_deletion.sql:152,155`.
  An applied migration is never edited, and its line numbers are already stale.
- **Packaging:** none. `server/**` is outside the electron-builder `files`
  allowlist and must stay out of the asar. `admin/tsconfig.json`'s `**/*.ts` is
  relative to `admin/`, and `admin/src/lib/shared.ts` imports only `_shared/`.

## 8. Verification recipe

Tier: `.claude/skills/server-changes/SKILL.md` for `functions/*/index.ts`,
meaning the integration suite actually running, or the **Server integration**
check green. **For this function that proof is thin.** Integration tests cover 5
of the 18 modes: `create`, `info`, `start`, `close` and `mine`
(`functions.test.mjs:2233`, `:2290`), plus `create`/`report` in
`account.test.mjs:300`. CI starts the stack with `storage-api` excluded, so no
signed-URL success path runs anywhere automated. **The split is therefore proven
in four layers, and PR 1 thickens the last one before any code moves.**

### 8a. Pure-move proof (no stack, run on every Phase 2 PR)

**1. Body diff.** A throwaway script outside the tree (for example
`$TMPDIR/movecheck.mjs`, never committed) loads TypeScript from
`admin/node_modules/typescript` via `createRequire` (6.0.3 today).
`worktree-init.sh` links that tree, but only warns when main's
`admin/node_modules` is missing, so check the link exists before running.
The script then:

1. Parses `git show origin/main:server/supabase/functions/collab/index.ts`.
   Inside the `Deno.serve` arrow body, for every top-level `IfStatement` whose
   condition text matches `^mode === '([a-z-]+)'( \|\| mode === '[a-z-]+')?$`,
   it takes the text from the first to the last statement of the `then` block.
   Unscoped modes key on the mode; the member modes do too.
2. Takes the old slices 294-352 (gate) and 635-686 (room), plus every top-level
   `FunctionDeclaration` and `VariableStatement` by name.
3. Parses every `collab/*.ts` on the branch. For each exported function it takes
   the body statements, drops the leading `const { … } = c;` / `= r;`
   destructures, and for `authorize`/`loadRoom` also drops the final
   `return { … }`. It maps each function to its old key with the section-4 table.
4. Strips each block's common indentation and diffs old against new with
   `diff -u`. **Expected output: no diff for any of the 18 modes, the two gates,
   `signUpload`, `signDownload`, `sweepExpired`, and every constant.** Any other
   line is a behaviour change and belongs in a separate PR.
5. Symbol inventory: every top-level name in the old file (section 4 table)
   appears **exactly once** across `collab/*.ts`, and `Deno.serve` appears only in
   `index.ts`.

**2. Type-error set.**
`DENO_DIR=$(mktemp -d) deno check server/supabase/functions/collab/index.ts`
must print **exactly the 3 TS7006 errors seen on `main`**, now pointing at
`info.ts`, and nothing else. A missed destructure shows up as TS2304 ("Cannot
find name"), and a path that no longer returns as TS2366. The first run
downloads `npm:postgres` into that temporary `DENO_DIR`; nothing lands in the
tree.

**3. What deploys.** `deno info --json server/supabase/functions/collab/index.ts`
lists the module graph. **Every `collab/*.ts` must appear in it.** A file absent
from the graph is a file `--use-api` will not upload.

**4. Text tests and the no-stack suite.**
`node --test test/server/collab.test.mjs test/server/schema.test.mjs test/server/jsonb-params.test.mjs`,
then `SUPABASE_URL=http://127.0.0.1:1 npm test`. The second forces the
integration skip, which proves the tree and says nothing about the stack.

### 8b. Integration run (the server-changes recipe, from the PR's own worktree)

```bash
bash scripts/worktree-init.sh          # fresh worktree only
node scripts/dev-env.js
cd server
export DOCKER_HOST="unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')"
supabase start -x edge-runtime,vector,logflare     # podman, never Docker Desktop or OrbStack
supabase db reset
nohup supabase functions serve --env-file supabase/.env.local > /tmp/serve.log 2>&1 &
lsof -a -p $(pgrep -f "supabase functions serve") -d cwd | tail -1   # must print THIS worktree
podman inspect supabase_edge_runtime_bad-takes \
  --format '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' | grep functions
grep 'collab: storage backend=' /tmp/serve.log     # after the first collab request: backend=supabase
cd .. && node --test test/server/functions.test.mjs test/server/account.test.mjs
```

- One stack serves the machine. Take it over only when no other worktree is
  mid-run (the skill's "Whose stack is answering?").
- The local stack keeps `storage-api`, so the local run can exercise signed URLs
  once the `collab` bucket exists (`server/README.md` § The `collab` storage
  bucket).

### 8c. Golden replay (local, covers what CI cannot)

Until PR 1 lands, and afterwards for the signed-URL success paths, run a
throwaway scenario script outside the tree against both versions:

1. Serve `main`'s function from a `main` worktree and replay the scenario.
2. Stop that serve, serve the branch, and replay again.

**The scenario, mode by mode.** Two fresh accounts, each on its own
`cf-connecting-ip`, with a flag rule for both like `functions.test.mjs:2243`:
- host `create`, then `upload-url` scene/thumb, then `commit` scene/thumb;
- `update`, `start`;
- guest `join`, `claim`, and `claim` of the same character (409);
- `heartbeat` with and without `recording`;
- `upload-url`/`commit` for a take, including `cam_bytes`;
- `info`, `list`, `mine`;
- `download-url` for the scene and for a take, and an unknown take (404);
- `release`;
- `wrap` while a seat is hot, then `info` (wrap on read), then `reopen`, then
  `wrap` again;
- `report`, guest `leave`, host `leave` (400), `close`;
- every 413, and `not_a_member`.

Record `status` and JSON for each call, with UUIDs, `BT-` codes, timestamps,
`now`, `expires_at` and signed-URL query strings masked, then diff the two
recordings. **Expected: identical.**

### 8d. After merge (the deploy is CI's; never a local deploy)

- Watch the `Server deploy` run go green after Server integration on `main`.
- Confirm the production boot line still reads `collab: storage backend=r2
  bucket=…`: Supabase dashboard function logs, or the read-only Supabase MCP's
  log query. Never the MCP's `apply_migration`.
- Eric's hand check in the running app: host a room from desktop, join it from
  the web app, claim, record and push one take, pull it, then End.
- **Rollback is a revert PR,** which CI deploys the same way, about 20 minutes
  of suite later.

## 9. Phase 2 order

The file has no pure utilities of its own (they already live in
`_shared/collab.mjs`), so Eric's order maps to:

| PR | Contents | Proof |
| --- | --- | --- |
| **1: characterization, no code moves** | `test/server/functions.test.mjs` collab cases for the 13 modes nothing tests: `join` (unknown code, rejoin), `list` (public live room appears), `report` (by id, by code, `missing_reason`, `unknown_collab`), `heartbeat` (ok and not writable), `claim` (success, 409, `missing_character`), `release` (host vs guest), `update` (`host_only`, `locked`, writes), `wrap` (closed, wrapping, wrapped), `reopen`, `commit` (take row, backstop gun, wrap completion), `download-url` (`unknown_take`, `scene_expired`, `bad_request`), `upload-url` refusals that return before signing (all four 413s, `host_only`, `bad_line_id`, `closed`), `leave` (`host_cannot_leave`, guest leaves); gate cases `not_a_member`, `missing_collab`, `flag_off`. **Decision for Eric:** the signed-URL success paths either go into CI (drop `storage-api` from the `-x` list and create the `collab` bucket in setup) or stay in the 8c golden replay. The first is recommended: `signUpload`/`signDownload` are exactly what PR 2 moves, and the price is image pull time. Also fix the stale `server-ci.yml` comment. | Green on the current single file: Server integration check, and 8b locally |
| **2: types and services** | `gate.ts` (`Sql`, `Caller`, `UUID_RE`, `authorize`), `bucket.ts`, `sweep.ts`; `index.ts` imports them and keeps every mode branch inline. Retarget `collab.test.mjs:354-363`; widen `schema.test.mjs:817-827`; update the live comment references (section 7). | 8a (the diff covers the gate, signing, sweep and constants), 8b, 8c, Server integration, 8d |
| **3: unscoped handlers** | `lobby.ts`, `browse.ts`, `report.ts`; the five dispatch lines. Retarget `collab.test.mjs:554-573` and `:575-588`. | 8a to 8d |
| **4: member handlers, entry becomes the orchestrator** | `room.ts`, `info.ts`, `seats.ts`, `lifecycle.ts`, `push.ts`, `pull.ts`; `index.ts` reduced to section 5. | 8a to 8d. The type check now lands the 3 TS7006 errors in `info.ts` |

PRs 3 and 4 may go as one if reviewed together. They are listed apart because
each merge is a production deploy of every function, and a smaller diff is
easier to bisect and revert. Land them in a quiet window for collab work. The
file has taken 14 commits since it was created on 2026-08-22, the last on
2026-09-14 (`git log --format='%ad %h' --date=short -- …/collab/index.ts`), and a
concurrent collab PR will conflict on every block.

## 10. Risks

1. **Deno import resolution.**
   - Relative specifiers need the `.ts` extension (`./gate.ts`). Deno does not
     try extensions.
   - A subdirectory would change every `../_shared/` depth, so keep the files
     flat.
   - Use `import type` for the context types. The runtime erases them, while a
     value import of a type-only module is still a real module edge.
2. **What deploys when the function gains files.**
   - `--use-api` uploads the import graph from `index.ts`, not the directory.
   - `supabase functions serve` mounts the whole directory. So a module reached
     only through a computed dynamic `import()` works locally and is missing in
     production. **Static imports only**, checked with `deno info` (8a.3).
   - Any new file named `index.ts` changes nothing at depth 2, but it invites a
     future move to depth 2. Do not use the name.
3. **Module-load side effects.**
   - `bucket.ts` reads the R2 env and prints the boot line when it is evaluated.
     ESM evaluates a module once per isolate however many files import it, so
     the line still prints exactly once.
   - Never copy the block into a second collab module, and never import
     `bucket.ts` from outside `collab/`. `account/` has its own
     `account: storage backend=` line on purpose.
   - Only `index.ts` may call `Deno.serve`. A second call in a sibling would
     register a second handler at load.
4. **Cold start.** 12 more local modules in the graph. `--use-api` bundles them
   server-side into one eszip, so the cost is parse time, not fetches. Locally,
   and in CI's warmup, the first request boots a worker per function exactly as
   today.
5. **Ordering is behaviour.** The dispatcher must keep today's order:
   - body parse, then the rate limits (spent before `invalid_token`), then seat,
     then flag;
   - then the sweep kick;
   - then the unscoped modes, before the membership gate;
   - and the wrap on read before `wrapDeadline`/`roomExpired` are computed.
   Reordering any of these changes a status code or a 429 budget.
6. **Identity of the room row.** `loadRoom` must hand back the `collab` object
   it mutated with `Object.assign`. A copy would give `info` the pre-wrap phase.
7. **Silent test coverage loss.**
   - The `schema.test.mjs:817` `web_machine_id` scan passes vacuously once the
     licence select leaves `index.ts`. Widen it in the same PR (PR 2).
   - The three text tests that do fail loudly must be retargeted, **not
     deleted**.
8. **What no automated test can see.**
   - The R2 branch of signing and deletion. It runs only in production.
     Protected by the 8a body diff and the 8d boot line.
   - `EdgeRuntime.waitUntil`, which exists only in the hosted runtime.
   - Sweep lease timing across a fleet.
   - Rate-limit budgets across real IPs.
   - Signed-URL success paths in CI, unless PR 1 restores `storage-api`.
9. **The shared-rule hazard.** Moving code must not "tidy" a rule into a new
   home.
   - Every predicate stays imported from `_shared/collab.mjs`, and
     `publicCollabRow`/`displayForMember` stay the only place a member's display
     is resolved.
   - The wrap-completion check-and-update is written twice in this file (655-674
     on read, 1128-1145 in commit). After the split it sits in `room.ts` and
     `push.ts`. **Leave both copies as they are in Phase 2;** merging them is a
     behaviour change (one has `returning`). Filed as proposal 69 (P3).
10. **No cycles, by construction.** Handlers import `bucket.ts`, `_shared/`, and
    types from `gate.ts`/`room.ts`. Nothing imports `index.ts`, and `bucket.ts`
    imports no collab module.
11. **Every PR is a production deploy on merge.** A failure after merge is fixed
    forward with a revert PR through CI. Never `supabase functions deploy`,
    `supabase login` or a token export from a laptop.
