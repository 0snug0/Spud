# BAD-036 · Phase 1 extraction plan: `web/collabweb.js`

Spike by Shepody (BADS-036/06, architect) for [[BAD-036]]. A read-only survey of BadTakes `main` at `20d11e1dace7a7aac012b30ffcd6c983b92642c2` (2026-09-15, clean); every line number cites that SHA. Sibling spikes in this directory: `web-app.md` (`app-<part>.js`) and `web-booth.md` (`booth-<part>.js`). This plan uses the same conventions: files at the top level of `web/` named `<entry>-<part>.js`, bare-scope classic scripts, a concatenation move check, and a duplicate-name sweep. The split was dry-run on a scratch copy (`git archive 20d11e1`) and nothing in the BadTakes tree was touched. Section 8 lists what the dry run proved.

**Size rule (Eric, 2026-09-15):** 250 lines is where a file gets looked at. It is not a cap. A cohesive unit stays whole however big it is, and the bigger it is, the harder that look should be. Split by responsibility, never by line count.

**Summary.** Eight classic scripts at the top level of `web/`: `collabweb-model.js`, `-host`, `-seat`, `-sync`, `-modal`, `-phases`, `-tab`, and `collabweb.js` itself as the last tag, reduced to the `?join=` invite and `wireCollab`. Each file is one unbroken line range of today's file, and the tags go in line order. That is what lets the check in section 8 be a plain `diff` that prints nothing, at the end and after every intermediate commit. The previous architect's log proposed seven pieces (model 259, host 235, seat 103, sync 221, modal and phases 375, tab 287, wiring 100). The first four are confirmed with the same seams. "Modal and phases" is cut in two (108 + 267), because other files call the modal's doors while only the modal paints the phases. The `?join=` invite moves from the tab into the entry, so both writers of `let pendingInvite` stay in one file. Three files end over 250, each by less than 20 lines: `collabweb-model.js` 259, `collabweb-phases.js` 267 and `collabweb-tab.js` 261. Section 6 explains why each stays whole and where the cut would go.

## 1. The file today

| | |
|---|---|
| Path | `web/collabweb.js` |
| Size | 1580 lines (`wc -l`); 108 top-level declarations: 73 `function` (26 `async`), 32 `const`, 3 `let` (`publicListSeq` 1197, `myListSeq` 1198, `pendingInvite` 1480). No class, no `window.*` assignment, and no top-level statement other than declarations, comments and `'use strict'` (line 29). |
| Runtime | A classic browser script. Its `const`/`let`s live in the page's shared global lexical scope and its `function`s become properties of `window`. That is how `app.js`, `booth.js`, `editor.js` and `account.js` call it by bare name. It runs in the tab at my.badtakes.io, in the Capacitor shell (`mobile/www`, the same tree via `scripts/build-mobile.js`) and under `npm run web` / `web:local` / `web:live`. It never runs in the render container: `deploy-render.yml` ships four `web/lib` files and no view script. |
| Loaded by | `web/index.html:840` `<script src="collabweb.js">`. It is the **seventh** of the eight view scripts: `app.js` 834, `booth.js` 835, `editor.js` 836, `account.js` 837, `ads.js` 838, `billing.js` 839, `collabweb.js` 840, `start.js` 841. That puts it after `billing.js`, not directly after `booth.js` as the brief put it. Every `lib/` and `../src/` tag comes earlier, including `../src/collab.js` at 793, which defines `window.CollabRules`. `start.js:4` calls `window.bootApp()`, and `boot` calls `wireCollab` (app.js:1713) and `maybeApplyInviteFromUrl` (app.js:1753). |
| Shared rule it consumes | `src/collab.js` (`CollabRules`), loaded byte-identical, decides every collab rule: who owns a character, which take plays, what is pending to push or pull, whether a seat has lapsed, which phases are live. This file only calls it, and the split keeps it that way. |
| CSP | `index.html:21`: `script-src 'self'`. There are no inline scripts, so every new file is a `<script src>` tag. |
| Ships via | `scripts/build-web.js`: `copyTree` (77) copies every non-dot file, and `stampAssets` (135) turns each tag into `collabweb.js?v=<digest>`. The same `build()` also fills `mobile/www`. |
| Read by tests | `test/web-guest-mode.test.js`: the vm file list (230-231), the static read (446) and the scan for a sign-in with no door (463). The vm evaluates `fetchPublicCollabs` and `renderLiveStrip` (414-415) and reaches the tab and invite through `showTab('collabs')` and `?join=` (379-404). `test/web-ids.test.js:42` sweeps `$('literal')` across `web/*.js`, non-recursively. `test/collab.test.js` and `test/collabthumb.test.js` test `src/` and never read `web/`. |
| Recent history | `03a7162` (#339, BAD-008 atomic create), `a11255b` (#330, BAD-002 guest mode), `a4bd003` (#319), `0310873` (#293). |

What it is: the desktop's collab client in a browser tab. It holds the room model for the selected scene, the one wire (`POST /functions/v1/collab`), the host's lifecycle (create, scene upload, Start, room options, End, Reopen, Cancel), a seat (join by code, leave, Record ◉ as the claim into the booth), automatic push and pull with the booth heartbeat, the modal's state machine and its three phase views, and the Collabs tab with the public list, the Scenes strip, your rooms and Close. It finishes with the wiring.

## 2. Public surface (the compatibility contract)

Measured with a scope analysis (acorn + eslint-scope, borrowed read-only from `admin/node_modules`) over the eight view scripts. Local variables that shadow a global are excluded, and property names do not count as references. Every hit was then read by hand. The Superior brief's grep flagged `icon`, `dead` and `collab` in `editor.js` and `account.js`; those were false positives. No script outside this file references `icon`, `ICON` or `dead`.

### 2a. Bare names other scripts and the vm test read from this file

| name (line, kind) | read from | how | destination |
|---|---|---|---|
| `collab` (31, `const` object) | booth.js:31 `collab.boothCharacter`; booth.js:552 `collab.remote = …` | Not guarded itself. Both sit inside a branch that `collabBoothSeat()` / `collabActive()` already passed. | model |
| `collabActive` (117, `const` arrow) | app.js:1222 `lineVoiced`, :1433 `loadTransport`; booth.js:551 `enterScreening` | `typeof` guard | model |
| `resolveCollabTakes` (148) | booth.js:552 | inside the `inCollab` branch | model |
| `mixTakes` (159) | app.js:1216 `takesForMix` | `typeof` guard | model |
| `collabPerformerOf` (171) | booth.js:968 `performerOf` | `typeof` guard | model |
| `onSceneSelected` (250) | app.js:717 `selectScene` | `typeof` guard | model |
| `collabLicenseChanged` (256) | account.js:585 `applyLicenseUi` | `typeof` guard | model |
| `collabBoothSeat` (597, `const` arrow) | booth.js:31 `enterBooth` | `typeof` guard | seat |
| `scheduleCollabPush` (725) | booth.js:424 `stopRecord` | `typeof` guard | sync |
| `flushCollabPush` (734) | booth.js:507 `wireBooth` | `typeof` guard | sync |
| `collabHeartbeat` (768) | booth.js:348 `toggleRecord`, :373 `stopRecord` | `typeof` guard | sync |
| `stopBoothHeartbeat` (790) | app.js:93 `showView` | `typeof` guard | sync |
| `renderBoothCollab` (794) | booth.js:50 `enterBooth`, :527 `wireBooth` | `typeof` guard | sync |
| `collabEditLock` (846) | app.js:868 `renderEditLock`; editor.js:34 `enterEditor` | `typeof` guard | modal |
| `closeCollabModal` (887) | account.js:596 `accountChanged` | `typeof` guard | modal |
| `fetchPublicCollabs` (1225) | test/web-guest-mode.test.js:414 `run('fetchPublicCollabs({ force: true })')` | vm evaluation | tab |
| `renderLiveStrip` (1290) | app.js:358 `renderLibrary` (`typeof`); the test at :415 | guard / vm | tab |
| `renderCollabsTab` (1435) | app.js:137 `showTab`; account.js:601-602 `accountChanged` | `typeof` guard | tab |
| `maybeApplyInviteFromUrl` (1469) | app.js:1753 `boot` | `typeof` guard | collabweb.js |
| `wireCollab` (1484) | app.js:1713 `boot` | `typeof` guard | collabweb.js |

`start.js`, `ads.js` and `billing.js` read nothing from this file. Every read in the table sits inside a function that runs after `start.js`; nothing reads a collab name while the page loads. This agrees with `web-app.md` §2b (nine names app.js reads) and `web-booth.md` §2b (nine names booth.js reads). The other 88 declarations are private in practice but stay bare globals, as every classic script's do.

The vm test reaches four more names indirectly:
- `showTab('collabs')` (:381, :410, :422) runs `renderCollabsTab`.
- `bootApp()` runs `wireCollab`. That is what makes `page.$('btn-collabs-signin').onclick()` (:389, wired at 1494) callable.
- `bootPage({ search: '?join=BT-ABC234' })` (:398-404) runs `maybeApplyInviteFromUrl` → `openCollabsTab` → `renderCollabsTab`, which reads `pendingInvite`.

**Events and body flags.** The file listens for `license-changed` (1577; dispatched at account.js:586) and dispatches nothing. It sets no `body` class. It toggles `setup`/`session` on `#collab-card` (902-903), and the phone CSS scopes its pinned bar to `.collab-card .modal-actions`.

Stale comment inside the file: lines 81-82 say `collabPin` "is also what booth.js tests". booth.js tests `collabBoothSeat` (booth.js:31), and no file other than this one references `collabPin`. The move keeps the comment byte-identical; section 7 lists it.

### 2b. Names this file reads from the other view scripts

All bare, all inside functions, none at load (the scope analysis found no load-time reference outside the file's own declarations and the `Set`/`Map` builtins).

| defined in | name (definition line) → uses here | where the name sits after the sibling splits |
|---|---|---|
| `app.js` | `$` (8) — 131 literal calls from 795 on; `state` (14) — 88 uses; `toast` (36) — 227, 449, 475, 699, 1217, 1415, 1462, 1524-1536; `setHeader` (45) 1427; `showView` (83) 1214; `markTab` (115) 1214; `showTab` (122) 1426; `goHome` (142) 1427; `backToLibrary` (147) 863, as `collabDoor`'s default parameter, evaluated when the function is called; `isPhone` (159) 1427; `setSceneOpen` (167) 1215 | `app-shell.js` |
| `app.js` | `loadLibrary` (253) 516; `renderLibrary` (344) 518, 534, 539 | `app-library.js` |
| `app.js` | `selectScene` (642) 518, 534, 540; `renderEditLock` (861) 222, 245 (`typeof`) | `app-casting.js` |
| `app.js` | `installScene` (1059) 526 | `app-files.js` |
| `app.js` | `sessionTakes` (1208) 162; `reportSessionStart` (1242) 590; `reportSessionEnd` (1267) 1516 | `app-session.js` |
| `account.js` | `APP_VERSION` (30) 104; `signOut` (493) 109; `signedIn` (513) 102, 1424, 1436, 1477; `applyLicenseUi` (539) 112, 246 (`typeof`), 434, 478, 553, 1405; `collabOn` (477) 117, 194, 257, 357, 826, 847, 880, 1294, 1425, 1461, 1487, 1578; `openSignIn` (622) 880, 1487, 1494 | unchanged (account.js is not being split) |
| `booth.js` | `enterBooth` (28) 592; `enterScreening` (534) 1519 | `booth.js` and `booth-screening.js` |

Namespaces, all from earlier tags:
- `Api`: `supa` 103; `storagePut` 287, 347, 631, 633, 636; `storageGet` 525, 668, 670, 672, 1332.
- `Store`: `putScene` 93, 314, 538, 1411; `listRemoteTakes` 229, 657; `putRemoteTake` 673.
- `CollabRules`: `phaseActive` ×7, `looksLikeCode` ×4, `claimFresh` ×3, `sceneCast` ×2, `pendingPushes` ×2, and one each of `resolveTakes`, `castStatus`, `claimRecording`, `indexKey`, `lineCharacters`, `pendingPulls`, `CLAIM_LEASE_MS`.
- Others: `SceneDigest.sceneDigest` 362, 532; `ZipWrite.writeZip` 277; `PackSource.slugify` 277; `Recorder.recording` 783, 786; `Takes.lineActive` 130; `Native.isNative` 877.

Browser globals: `navigator.clipboard`/`share` (1524, 1534-1535), `history.replaceState` (1472), `location` (1470, 1532), `URL.createObjectURL` (1334), `confirm` (426, 466, 547, 1396), `crypto.randomUUID` (584), `document.addEventListener` (1577), and the timers.

### 2c. DOM ids it owns

There are 69 distinct ids over 131 `$('literal')` calls. Two helpers look ids up with a computed argument, which the sweep skips by design: `setStatus` `$(id)` (854) and `renderUploadNote` `$(noteId)`/`$(meterId)` (963-964). Their callers pass literals: `collab-setup-status`, `collab-start-status`, `collab-live-status`, `collab-session-status`, `collab-setup-note`/`-meter`, `collab-code-note`, `collab-upload-meter`. `test/web-ids.test.js:42` keeps every id in its sweep as long as the files stay at the top level of `web/`. Ids by destination, with first line:

| file | ids |
|---|---|
| model, host, seat | none by literal. host passes status ids through `setStatus` (361, 391, 483, 493). |
| sync | `booth-collab-strip` 795, `booth-collab-text` 817 |
| modal | `btn-collab-pack` 823, `collab-session` 856, `collab-modal` 858, `collab-card` 902, `collab-session-head` 904, `collab-head` 905, `collab-start` 906, `collab-loading` 907, `collab-setup` 908, `collab-live` 909, `collab-public-chip` 911, `collab-title` 914, `collab-loading-line` 915, `btn-collab-start` 920 |
| phases | `collab-castable` 936, `collab-multi` 949, `collab-public` 951, `collab-public-row` 953, `btn-collab-into-room` 954, `btn-collab-cancel-setup` 955, `collab-invite-slot` 1001, `collab-session-invite-slot` 1001, `collab-code-block` 1002, `collab-code-row` 1004, `collab-code` 1005, `btn-collab-copy` 1007, `btn-collab-share` 1008, `collab-room-hint` 1017, `btn-collab-back-setup` 1029, `btn-collab-leave` 1030, `collab-cast` 1041, `collab-sync` 1084, `collab-session-dot` 1100, `collab-session-loader` 1101, `collab-session-check` 1102, `collab-session-title` 1103, `collab-session-chip` 1104, `collab-code-note` 1108, `collab-upload-meter` 1108, `btn-collab-end` 1110, `btn-collab-reopen` 1111, `btn-collab-play` 1112, `btn-collab-leave-session` 1113, `collab-session-foot` 1114, `collab-session-hint` 1116, `collab-session-cast` 1152 (and the modal's `collab-modal`, `-setup`, `-live`, `-session`, `-title`, `-public-chip` again) |
| tab | `join-status` 1202, `btn-join-go` 1203, `join-public-list` 1238, `join-public-note` 1239, `live-collabs` 1291, `live-collabs-strip` 1292, `join-code` 1323, `join-mine-list` 1345, `join-mine` 1346, `collabs-guest` 1437, `collabs-member` 1438, `collabs-invite` 1440, `join-public-wrap` 1451 |
| collabweb.js | only it uses: `btn-collabs-signin` 1494, `btn-collab-close` 1499, `btn-collab-close-session` 1500, `btn-booth-collab` 1551, `btn-join-collab` 1554, `btn-join-card` 1555, `btn-all-collabs` 1556, `join-form` 1558. It also rebinds 24 ids listed above (1464-1567). |

It also uses the selectors `.room-card` (1297), `.join-mine-close` (1560), `li[data-code]` (1561), `.cast-record` (1548) and `input` under `#collab-castable` (1540).

### 2d. The wire, storage and IndexedDB

- **One endpoint.** `collabFetch` (101-115) sends `window.Api.supa('/functions/v1/collab', { body: { token, mode, app_version: APP_VERSION, platform: 'web', ...extra }, timeoutMs })`.
  - Signed out, it answers `{ ok: false, status: 0, data: { code: 'signed_out' } }` without touching the network. This is the "no endpoint without a token" rule the vm test measures.
  - A 403 with `superseded`/`revoked`/`unknown_license` calls `signOut(code)`. A 403 with `flag_off` sets `state.flags.collab = false` and calls `applyLicenseUi()`.
- **Sixteen modes**, with their call lines: `info` 203, 608, 654 · `upload-url` 279, 345, 624, 627 · `commit` 289, 348, 637 · `close` 298, 430, 1397 · `mine` 327, 1348 · `create` 364 · `start` 411 · `update` 445 · `wrap` 470 · `reopen` 490 · `join` 501 · `download-url` 521, 665 · `leave` 550 · `claim` 568 · `heartbeat` 772 · `list` 1229.
  - The server's allowlist is `server/supabase/functions/collab/index.ts:66-79`; it also has `release` and `report`, which the web never sends.
  - Response fields read: `info` → `collab{phase,status,code,visibility}`, `claims`, `members`, `takes`, `castable`, `multi_character`, `visibility`, `now` (skew, 218); `create` → `collab_id`, `member_id`, `code`; `join` → `collab_id`, `member_id`, `digest`, `scene_ready`; `upload-url`/`download-url` → `url`, `sidecar_url`, `cam_url`; `wrap` → `phase`; `heartbeat` → `ok`, `phase`; `list` → `collabs[]`; `mine` → `sessions[]`; error `code`/`reason` strings (283, 385-389, 415, 506-511, 572-578).
  - The split moves callers only; no request or response shape changes.
- **Signed storage URLs.** `Api.storagePut` carries the scene zip (287), thumb (347), take (631), sidecar (633) and cam (636). `Api.storageGet` fetches the scene (525), take (668), sidecar (670), cam (672) and thumb (1332). The **`x-upsert` rule** (sent to Supabase Storage, never to R2) lives in `web/lib/api.js:232-238` (`storagePut`), not here. This file sets no header and mints no URL, and the split must not add either.
- **IndexedDB** through `Store`, as listed in 2b. Incoming takes land in the remote store (`putRemoteTake`); `state.takes` is only ever read here (151, 612, 619, 1024) and never written. The load-bearing rule "**`state.takes` stays your takes** and the screening room asks `mixTakes()` for the combined dub" lives in `mixTakes` (159-168), which moves to `collabweb-model.js`, and in booth.js/app.js, which do not change.

### 2e. Strings the tests read

| test | what it reads today | after the split |
|---|---|---|
| `test/web-guest-mode.test.js:230-231` | runs `['web', 'collabweb.js']` last in one vm context, after `app.js` and `account.js` | the collab family in tag order (section 7). Left unchanged, 9 of the file's 10 tests fail on the split tree (dry run): `boot` → `wireCollab` reads `hostCollab`, which is no longer loaded. |
| `:446`, `:463` | `read('web', 'collabweb.js')` must not match `/openSignIn\(\)/` | the family's concatenation. Left unchanged, the test **still passes but silently stops covering** `collabweb-modal.js:880` (`openSignIn(collabDoor())`): the family has 3 `openSignIn(` calls and `collabweb.js` would hold 2. |
| `:379-426` vm evaluations | `showTab('collabs')`, `fetchPublicCollabs(…)`, `renderLiveStrip()`, `$('btn-collabs-signin').onclick()`, `bootPage({ search: '?join=…' })`, and `/functions/v1/collab#list`/`#mine` recorded through `Api.supa` | unchanged: same bare names in the same context |
| `:8` | header comment names `web/collabweb.js` | reword to the family |
| `test/web-ids.test.js:42-47` | every `$('literal')` in `web/*.js` | unchanged while the files stay at the top level |
| `test/build-web.test.js:34-41` | every `<script src>` in the built page exists and carries its own digest | covers the new tags automatically |
| `test/fonts.test.js`, `test/ios-no-plans.test.js` | `web/booth.js`; `web/ads.js`, `web/billing.js` | not this file |

## 3. Responsibility map

| lines | block(s) | kind |
|---|---|---|
| 1-29 | head comment (the client's design notes), `'use strict'` | — |
| 30-52 | `collab`, `collabSync`, `COLLAB_POLL_MS`, `COLLAB_FOCUS_POLL_MS`, `COLLAB_PUSH_DEBOUNCE_MS`, `COLLAB_SYNC_MAX_RETRIES`, `COLLAB_RETRY_STEP_MS`, `collabAutoPulled` | state + constants |
| 53-71 | `foldChar`, `linesLabel`, `collabNow`; `ICON`, `icon`, `waveLoader` | pure utilities (text, the server clock) and the SVG and loader vocabulary the views draw |
| 72-94 | `/* pins */` `collabPins`, `collabPin`, `writePin` | the scene record's pins + IO (`Store.putScene`) |
| 95-126 | `/* the wire */` `collabFetch`; `collabActive`, `collabInfo`, `collabPhase`, `collabCode`, `collabClaims`, `collabMembers`, `meId`, `hostId`, `isHost`, `roomInPlay` | service (the one endpoint) + pure selectors over `collab.info` |
| 127-175 | `/* the cast */` `activeLines`, `castableCandidates`, `castableCharacters`, `charLines`, `collabMyClaim`, `resolveCollabTakes`, `mixTakes`, `collabPerformerOf` | pure over `CollabRules`: which take plays; what app.js and booth.js consume |
| 176-259 | `/* status */` `refreshCollab`, `dropPin`, `onSceneSelected`, `collabLicenseChanged` | orchestration of the model for the selected scene (info and remote takes, sequence-guarded; repaints the views) |
| 260-494 | `/* host */` `uploadSceneForCollab`, `adoptHostedRoom`, `uploadCollabThumb`, `hostCollab`, `startCollabSession`, `cancelHostedCollab`, `applyCollabUpdate`, `endHostedCollab`, `reopenCollab` | services + flows: zip, signed PUT, create/start/update/wrap/reopen/close, the BAD-008 orphan paths |
| 495-554 | `/* guest */` `joinCollab`, `leaveCollab` | flow: join, download, install, digest check, pin |
| 555-597 | `/* claim → record */` `recordCollabCharacter`, `collabBoothSeat` | flow into `enterBooth` |
| 598-762 | `/* sync */` `pushTakes`, `pullTakes`, `runCollabSync`, `scheduleCollabPush`, `flushCollabPush`, `startCollabPolling`, `stopCollabPolling`, `stopCollabRetries`, `autoPullWrapped` | services: the single-flight transfer queue, retries, the 60s poll |
| 763-790 | `/* presence */` `collabHeartbeat`, `startBoothHeartbeat`, `stopBoothHeartbeat` | service: the 60s booth heartbeat |
| 791-818 | `renderBoothCollab` | view: the recording room's strip (starts and stops the heartbeat) |
| 819-850 | `/* the entry row */` `renderCollabButton`, `collabEditLock` | view (the casting call's LIVE button) + rule consumer (the edit lock app.js and editor.js ask) |
| 851-926 | `/* the modal */` `setStatus`, `collabStatusLine`, `renderIfCollabOpen`, `collabDoor`, `publicListShown`, `openCollabModal`, `closeCollabModal`, `renderCollabModal` | orchestration: the modal's doors and its state machine |
| 927-994 | `renderCollabSetup`, `renderUploadNote`, `renderCollabTransfer` | view: setup and the upload meter |
| 995-1094 | `renderCollabCodeRow`, `renderCollabRoom`, `dead`, `renderRoomCast`, `castRecordButton`, `castLostLine`, `renderCollabSync` | view: the room (phase `open`) |
| 1095-1193 | `renderCollabSession`, `sessionCastRows`, `renderSessionCast`, `castBar` | view: ON AIR / WRAPPING / WRAP |
| 1194-1219 | `publicListSeq`, `myListSeq`, `thumbUrls`; `joinWithCode` | state + flow (join from the tab) |
| 1220-1337 | `PUBLIC_LIST_TTL_MS`, `fetchPublicCollabs`, `paintPublicList`, `renderPublicCollabs`, `renderLiveStrip`, `loadThumb` | service (`list`, the TTL cache) + views (the list, the Scenes strip) |
| 1338-1417 | `renderMyCollabs`, `closeHostedRoom` | view + flow (`mine`; Close by id, BAD-008) |
| 1418-1454 | `openCollabsTab`, `renderCollabsTab` | view: the Collabs tab (guest and member) |
| 1455-1480 | `applyCollabInvite`, `maybeApplyInviteFromUrl`, `pendingInvite` | orchestration: the `?join=` door |
| 1481-1580 | `/* wiring */` `wireCollab` | wiring: every handler, the 20s focus poll (1571), the `license-changed` listener (1577) |

**State**:
- `collab`: one object holding the room, the view flags, the public-list cache and two timers.
- `collabSync`: the transfer queue.
- `collabAutoPulled`: a `Set`.
- `publicListSeq`, `myListSeq`: sequence guards.
- `thumbUrls`: a `Map` cache.
- `pendingInvite`.
- `renderCollabButton.plain` (829): a function-property cache.

**Timers**: `collab.focusTimer` 20s (1571), `collabSync.timer` 60s (744), `collab.hbTimer` 60s (784), `collabSync.debounce` 2s (729), `collabSync.retry` (712). All are timers rather than rAF, per the hidden-tab rule.

## 4. Proposed tree

**Where.** At the top level of `web/`, one classic script per responsibility, named `collabweb-<part>.js`, with bare scope exactly as today. This is the same shape and naming as `app-<part>.js` and `booth-<part>.js`.
- Not `web/lib/`. That convention is `window.Name = (function () { … })()`, a namespace object. Here, 20 names are read by bare name from four other scripts behind `typeof` guards (2a), and the family's own files call each other bare hundreds of times. Wrapping them would rewrite every call site, which is a refactor, not a move.
- Not a `web/collabweb/` directory. `test/web-ids.test.js:42` is non-recursive, so a subdirectory would silently drop 69 ids from the sweep.
- Not ES modules. `type="module"` gives each file its own top-level scope and defers it, so every bare name would need a `window.` export.
- The prefix is chosen so that the `group('collabweb')` helper `web-app.md` §7 proposes (`^collabweb(-[a-z]+)?\.js$`) selects exactly this family.

**Contiguity.** Each file is one unbroken line range of the original, and the tags run in line order. The concatenation check (section 8) is therefore a plain `diff`. Each range starts on the blank line before its section banner, so every cut falls on a blank line between declarations.

**Heads.** `collabweb-model.js` keeps lines 1-29 verbatim: the original header, which is the client's design notes, plus `'use strict'`. Every other file gets one `//` line and `'use strict';`. Strict mode is per script, so a file without it runs sloppy, and the check fails loudly on a missing one. The heads used in the dry run:
- `collabweb-host.js`: Hosting a collab: create, the scene upload, adopt, Start, the room options, End, Reopen, Cancel.
- `collabweb-seat.js`: Taking a seat: join by code, leave, and Record ◉ as the claim into the recording room.
- `collabweb-sync.js`: The room while you work: takes up and down, the booth heartbeat, the recording room's strip.
- `collabweb-modal.js`: The doors into the room: the entry row's button, the edit lock, the modal and its state machine.
- `collabweb-phases.js`: What each phase of the modal paints: setup, the room (open), and ON AIR / WRAPPING / WRAP.
- `collabweb-tab.js`: The Collabs tab: join by code, the public list and the Scenes strip, your rooms and Close.
- `collabweb.js`: The collab client's entry: the `?join=` invite and `wireCollab`. The family is `collabweb-*.js` in tag order; its design notes head `collabweb-model.js`.

| # | file | original lines | lines (with head) | what moves there (name: current line) |
|---|---|---|---|---|
| 1 | `collabweb-model.js` | 1-259 | 259 (259) | header 1-29; `collab` 31, `collabSync` 43, `COLLAB_POLL_MS` 47, `COLLAB_FOCUS_POLL_MS` 48, `COLLAB_PUSH_DEBOUNCE_MS` 49, `COLLAB_SYNC_MAX_RETRIES` 50, `COLLAB_RETRY_STEP_MS` 51, `collabAutoPulled` 52; `foldChar` 54, `linesLabel` 55, `collabNow` 56, `ICON` 58, `icon` 64, `waveLoader` 65; `collabPins` 75, `collabPin` 83, `writePin` 88; `collabFetch` 101; `collabActive` 117, `collabInfo` 118, `collabPhase` 119, `collabCode` 120, `collabClaims` 121, `collabMembers` 122, `meId` 123, `hostId` 124, `isHost` 125, `roomInPlay` 126; `activeLines` 130, `castableCandidates` 131, `castableCharacters` 132, `charLines` 138, `collabMyClaim` 140, `resolveCollabTakes` 148, `mixTakes` 159, `collabPerformerOf` 171; `refreshCollab` 182, `dropPin` 239, `onSceneSelected` 250, `collabLicenseChanged` 256 — 40 declarations |
| 2 | `collabweb-host.js` | 260-494 | 235 (237) | `uploadSceneForCollab` 267, `adoptHostedRoom` 326, `uploadCollabThumb` 341, `hostCollab` 354, `startCollabSession` 408, `cancelHostedCollab` 423, `applyCollabUpdate` 440, `endHostedCollab` 461, `reopenCollab` 486 — 9 |
| 3 | `collabweb-seat.js` | 495-597 | 103 (105) | `joinCollab` 498, `leaveCollab` 544, `recordCollabCharacter` 561, `collabBoothSeat` 597 — 4 |
| 4 | `collabweb-sync.js` | 598-818 | 221 (223) | `pushTakes` 603, `pullTakes` 650, `runCollabSync` 686, `scheduleCollabPush` 725, `flushCollabPush` 734, `startCollabPolling` 740, `stopCollabPolling` 746, `stopCollabRetries` 750, `autoPullWrapped` 756; `collabHeartbeat` 768, `startBoothHeartbeat` 780, `stopBoothHeartbeat` 790; `renderBoothCollab` 794 — 13 |
| 5 | `collabweb-modal.js` | 819-926 | 108 (110) | `renderCollabButton` 822, `collabEditLock` 846; `setStatus` 854, `collabStatusLine` 855, `renderIfCollabOpen` 858, `collabDoor` 863, `publicListShown` 877, `openCollabModal` 879, `closeCollabModal` 887, `renderCollabModal` 891 — 10 |
| 6 | `collabweb-phases.js` | 927-1193 | 267 (269) | `renderCollabSetup` 930, `renderUploadNote` 962, `renderCollabTransfer` 990; `renderCollabCodeRow` 1000, `renderCollabRoom` 1011, `dead` 1032, `renderRoomCast` 1034, `castRecordButton` 1064, `castLostLine` 1073, `renderCollabSync` 1083; `renderCollabSession` 1098, `sessionCastRows` 1124, `renderSessionCast` 1151, `castBar` 1187 — 14 |
| 7 | `collabweb-tab.js` | 1194-1454 | 261 (263) | `publicListSeq` 1197, `myListSeq` 1198, `thumbUrls` 1199, `joinWithCode` 1201, `PUBLIC_LIST_TTL_MS` 1221, `fetchPublicCollabs` 1225, `paintPublicList` 1237, `renderPublicCollabs` 1281, `renderLiveStrip` 1290, `loadThumb` 1330, `renderMyCollabs` 1343, `closeHostedRoom` 1395, `openCollabsTab` 1423, `renderCollabsTab` 1435 — 14 |
| 8 | `collabweb.js` (stays) | 1455-1580 | 126 (128) | `applyCollabInvite` 1460, `maybeApplyInviteFromUrl` 1469, `pendingInvite` 1480, `wireCollab` 1484 — 4 |
| | total | | **1580** (1594) | 108 declarations, each in exactly one file |

**Load order.** `web/index.html:840` becomes eight tags, between `billing.js` and `start.js`:

```html
<script src="billing.js"></script>
<script src="collabweb-model.js"></script>
<script src="collabweb-host.js"></script>
<script src="collabweb-seat.js"></script>
<script src="collabweb-sync.js"></script>
<script src="collabweb-modal.js"></script>
<script src="collabweb-phases.js"></script>
<script src="collabweb-tab.js"></script>
<script src="collabweb.js"></script>
<script src="start.js"></script>
```

No script reads a collab name while the page loads, inside the family or outside it. The dry run loaded the eight in tag order and in reverse without an error, and every name in 2a resolved both ways. The order therefore serves readers and the concatenation check, not the engine.

**Line coverage** (the dry run's cutter counted 1580 lines covered once, none twice, none missed; every piece passes `node --check`):

| lines | file | count |
|---|---|---|
| 1-259 | `collabweb-model.js` | 259 |
| 260-494 | `collabweb-host.js` | 235 |
| 495-597 | `collabweb-seat.js` | 103 |
| 598-818 | `collabweb-sync.js` | 221 |
| 819-926 | `collabweb-modal.js` | 108 |
| 927-1193 | `collabweb-phases.js` | 267 |
| 1194-1454 | `collabweb-tab.js` | 261 |
| 1455-1580 | `collabweb.js` | 126 |
| | **total** | **1580** |

**Cross-file bindings after the split.** The family is one client, so the calls between its files are dense. Every one of them runs inside a function after `boot`:
- **model → views and sync.** `refreshCollab` calls `renderCollabButton`/`renderCollabModal` (modal), `renderBoothCollab`, `startCollabPolling`, `stopCollabPolling`, `stopCollabRetries` and `pullTakes` (sync), and app.js's `renderEditLock`. `dropPin` and `collabLicenseChanged` repaint the same views.
- **host →** modal (`openCollabModal`, `closeCollabModal`, `setStatus`, `collabStatusLine`, `renderCollabModal`), phases (`renderCollabTransfer`), model (`collabFetch`, `writePin`, `refreshCollab`, selectors).
- **seat →** modal (`closeCollabModal`, `collabStatusLine`, `renderCollabModal`), model, app.js, booth.js (`enterBooth`).
- **sync →** modal (`renderIfCollabOpen`), phases (`sessionCastRows` in `renderBoothCollab`), model.
- **phases →** sync (`autoPullWrapped`), modal (`setStatus`, `publicListShown`), model.
- **tab →** seat (`joinCollab`), modal (`openCollabModal`, `publicListShown`), model (`collabFetch`, `collabPins`, `writePin`, `refreshCollab`), collabweb.js (`pendingInvite`, read at 1441-1444).
- **collabweb.js →** host (`hostCollab`, `startCollabSession`, `cancelHostedCollab`, `applyCollabUpdate`, `endHostedCollab`, `reopenCollab`), seat (`leaveCollab`, `recordCollabCharacter`), modal (`openCollabModal`, `closeCollabModal`, `collabDoor`, `publicListShown`, `renderCollabModal`), tab (`openCollabsTab`, `joinWithCode`), model (`collab`, `collabActive`, `collabCode`, `isHost`, `roomInPlay`, `foldChar`, `refreshCollab`, `COLLAB_FOCUS_POLL_MS`).
- **No `let` is written from two files.** The tab writes and reads both `publicListSeq` (1228) and `myListSeq` (1344). `pendingInvite` is written only in `collabweb.js` (1477, 1578) and read in the tab (1441-1444). That is why the invite moved into the entry. `collab` and `collabSync` are `const` objects that every file mutates in place, which is one binding, exactly as today.

## 5. The entry point afterwards

`web/collabweb.js` keeps its path and its tag text (`<script src="collabweb.js">`). It stays after `billing.js` and directly before `start.js`, now as the last of its own group. It holds the `?join=` door (`applyCollabInvite`, `maybeApplyInviteFromUrl`, `let pendingInvite`) and `wireCollab`, in 126 lines plus a two-line head. It keeps section 2's contract:

- **Same bare names, same kinds.** All 108 declarations keep their spelling, their kind (`function` stays `function`, `const` arrow stays `const` arrow) and their bytes. The split adds no name and renames none. The duplicate sweep in section 8 prints nothing on the dry run, over 325 top-level names across the eight view scripts. A name that changed file is still the same global binding, reached by the same `typeof` guards in app.js, booth.js, editor.js and account.js.
- **The two names `boot` calls stay here.** `wireCollab` (app.js:1713) and `maybeApplyInviteFromUrl` (app.js:1753) are what the entry is for. Both run after every family tag has loaded, so every name they reach exists by then.
- **No `window.*` assignment,** before or after. `window.bootApp` is app.js's and is untouched. `start.js` is untouched.
- **The design notes move with lines 1-29** into `collabweb-model.js`, the first file a reader of the family opens, and the entry's head points there. That keeps the concatenation exact. The alternative is to keep the header in `collabweb.js`, which costs a 29-line hunk in the check (section 6).
- **If Eric wants the entry first rather than last**, `collabweb.js` would hold lines 1-259 (the model) and the wiring would move to a last file, `collabweb-wiring.js`. The check stays exact, but `collabweb.js` is then the model rather than the orchestrator, which is the choice `web-booth.md` §5 made. It is not proposed here because Eric's Phase 2 order ends with the entry reduced to an orchestrator, and `web-app.md` §5 put `boot` last for the same reason.

## 6. Files over 250 lines

| file | honest size | why it stays whole | the cut, if Eric wants one |
|---|---|---|---|
| `collabweb-model.js` | 259 (229 of code under the 29-line header plus `'use strict'`) | Everything a view or another script reads about the room for the selected scene: the state object, the one wire, the selectors over `collab.info`, the rule consumers app.js and booth.js call (`mixTakes`, `resolveCollabTakes`, `collabPerformerOf`, `collabActive`), and `refreshCollab`, the only writer of `collab.info` apart from `pushTakes`/`pullTakes`. The selectors read what `refreshCollab` writes, and the cast functions read the selectors. | (a) At 126/127: "state and the wire" (1-126, 126 lines) and "the cast, the dub and the refresh" (127-259, 133). Both halves stay contiguous, but the dub rules end up in a different file from the state they read, for a 9-line overage. (b) Move the 29-line header into `collabweb.js`: the model drops to 230 and the check shows one 29-line hunk pair. **Recommendation: one file.** |
| `collabweb-phases.js` | 267 | The three views of one state machine, sharing widgets. `renderCollabModal` (modal, 891) dispatches to exactly `renderCollabSetup`, `renderCollabRoom` and `renderCollabSession`. `renderUploadNote` (setup) is called by `renderCollabCodeRow` (room), which `renderCollabSession` calls. `castRecordButton`/`castLostLine` (room) are drawn inside `renderSessionCast`. | Three files by phase: setup 927-994 (68), room 995-1094 (100), session 1095-1193 (99). All contiguous. The cost is that each phase reads its widgets from the file before it, and `sessionCastRows` is also read by sync's booth strip, so a reader following the ON AIR view opens three files. **Recommendation: one file**, three `/* ----- … ----- */` sections as today. |
| `collabweb-tab.js` | 261 | One screen plus the public list it shares with the Scenes strip. `fetchPublicCollabs` fills one cache (`collab.publicRows`, `publicListSeq`) that both `paintPublicList` and `renderLiveStrip` paint. Both rows join through `joinWithCode`. `renderCollabsTab` renders the list and "Your collabs" (`renderMyCollabs`, `closeHostedRoom`). | The public list 1220-1337 (118) → `collabweb-public.js`, leaving 143. The cost: `let publicListSeq` (1197) would be declared in the tab file while its only writer (1228) and its readers (1230, 1249, 1299) sit in the public file. The other clean seam, 1338, strands `let myListSeq` the same way. A `let` declared away from its only writer is worse than 11 lines. **Recommendation: one file.** |

**The previous hypothesis, where it changed.** "Modal and phases, 375" is cut at 926/927. The modal file's names are doors that other files call: app.js and editor.js call `collabEditLock`, account.js calls `closeCollabModal`, and host, seat and tab call `openCollabModal`/`setStatus`/`collabStatusLine`/`publicListShown`. The phases are painted only from `renderCollabModal` and `renderCollabTransfer`. "Tab 287 + wiring 100" became tab 261 + entry 126, so that `pendingInvite`'s two writers share a file.

Under 250 and deliberately small: `collabweb-seat.js` (103; joining, leaving and claiming are one responsibility, a seat, whether host or guest) and `collabweb-modal.js` (108). Merging either into a neighbour would put two responsibilities in one file just to save a tag.

## 7. Build and packaging touch points

| where | change |
|---|---|
| `web/index.html` | Line 840 becomes the eight tags of section 4. Nothing reads a collab name at load, so "before every script that reads their names at load time" holds trivially. Keep the group contiguous, prefixed, with the entry last: the check and the test select it by tag. Comments at :209 (`renderCollabsTab in collabweb.js` → `collabweb-tab.js`) and :226 (`publicListShown in collabweb.js` → `collabweb-modal.js`). |
| `scripts/build-web.js` | **No change.** `SKIP` (72) excludes only README.md, CLAUDE.md and scenes/. `copyTree` (77) copies every new file. `sharedModules` (99) matches only `../src/` and `../renderer/` tags. The existence check (251) passes because the files exist. `stampAssets` (135) appends each file's own `?v=<sha256[0:10]>`, which is the whole answer to the Cloudflare-proxied long cache on my.badtakes.io: a new page can only resolve to the bytes it was built with. The comment at :41 records exactly that failure (an old `collabweb.js` wiring an id the new page lacked) and stays as history. |
| `test/build-web.test.js` | **No change.** Lines 34-41 check every `<script src>` for existence and a matching digest; the `stamped > 30` floor only rises. |
| `test/web-ids.test.js` | **No change**, because the files stay at the top level of `web/` (:42). |
| `test/web-guest-mode.test.js` | **Changes.** These are three edits, exactly as run in the dry run: (a) after line 23 (`INDEX_HTML`), add `const VIEW_SCRIPTS = [...INDEX_HTML.matchAll(/<script src="([^"./][^"/]*\.js)"><\/script>/g)].map((m) => m[1]);` and `const group = (entry) => VIEW_SCRIPTS.filter((f) => new RegExp('^' + entry + '(-[a-z]+)?\\.js$').test(f));`; (b) line 231: `['web', 'collabweb.js']` → `...group('collabweb').map((f) => ['web', f])`; (c) line 446: `const collab = group('collabweb').map((f) => read('web', f)).join('\n');`. Also reword the comment at :8. This is the same `group()` that `web-app.md` §7 proposes for the app family. **Whichever of the three splits lands first adds it, and the others reuse it** (`web-booth.md` §7's `startsWith('booth')` also works, but one helper is better than two). With the edit, the test passes 10/10 on the split tree and on the intermediate tree; without it, 9/10 fail. |
| `test/fonts.test.js`, `test/ios-no-plans.test.js`, `test/collab*.test.js` | **No change** (they do not read this file). |
| CSP (`index.html:21`) | **No change**: `script-src 'self'`. |
| `.github/workflows/deploy-web.yml` | **No change**: `paths: 'web/**'` (27-28, 39-40). `deploy-render.yml` ships four `web/lib` files and no view script. |
| `firebase.json` | **No change**: `**/*.@(css\|js)` at `max-age=600, stale-while-revalidate=86400` (35-39) is covered by the stamps. |
| `scripts/build-mobile.js` | **No change**. It calls the same `build()` and inserts the Capacitor tag before `lib/native.js` (56-88). `mobile/www` is a gitignored copy that `npm run mobile:build` regenerates. |
| `scripts/web-server.js`, `web:local`, `render-proxy.js` | **No change**. The dev server serves the repo root with no allowlist (`web-app.md` §7). |

**Doc lines that go stale.** These are not build touch points. Each is fixed in the commit that moves what it names:
- `web/CLAUDE.md:79`: "`publicListShown()` in `collabweb.js`" → `collabweb-modal.js`. The same phrase in `mobile/CLAUDE.md:62` and `docs/MOBILE-STORES.md:136`.
- `web/README.md:209`: "**Collab** (`collabweb.js`)" → the `collabweb-*.js` family. `:292`: the layout listing grows seven rows. `:451`: "their state machine (`collabweb.js`)" → `collabweb-modal.js` / `collabweb-phases.js`.
- `server/CLAUDE.md:687`: "`web/collabweb.js`, matched by scene digest" (the adopt path) → `web/collabweb-host.js`.
- `web/account.js:572` (`renderCollabsTab in collabweb.js` → `collabweb-tab.js`) and `:610` ("the collab buttons (collabweb.js)" → the family). `web/app.js:859` ("The rule is collabweb's") stays true.
- **Inside the moved code**, left byte-identical by the move and fixed in a follow-up commit after the check:
  - `collabweb-model.js:7` says the courier goes "via the backend relay". `web/CLAUDE.md` records that the relay was removed on purpose; the page goes straight to Supabase and the signed URLs.
  - `:81-82` claims booth.js tests `collabPin`; booth.js tests `collabBoothSeat`.
- The six dated plans and specs under `docs/superpowers/` that cite `web/collabweb.js` are records of their date and are left alone.

## 8. Verification recipe

The tier comes from `.claude/skills/implementing-changes/SKILL.md`. `web/` has no smoke leg, so the proof is the static and vm tests, a build, the move checks, and a browser pass. Collab additionally needs the two-account pass against the local stack. Run everything on the exact tree about to be committed, before every commit.

1. **Tests.** Run `node --test test/web-guest-mode.test.js test/web-ids.test.js test/build-web.test.js test/collab.test.js`, then the whole `npm test`, and say that the pack tests skipped. `test/server/collab.test.mjs` needs a local stack and skips without one. It covers the Edge Function, not this client.
2. **Build.** Run `npm run web:build`. It exercises the four refusals and the stamping. `grep -o 'collabweb[a-z-]*\.js?v=[0-9a-f]\{10\}' dist/web/index.html | wc -l` prints `8`.
3. **The concatenation check** (authoritative; the reviewer runs it too). At the base and in the working tree, it reads the collab family from `web/index.html`'s tags, concatenates the files in tag order (the first whole, every later file from the line after its `'use strict';`), and diffs the two. Because Phase 2 peels files off the head of `collabweb.js` (section 9), the check is exact at **every** commit, not only the last. Save the script to a scratch path such as `/tmp/collab-concat.sh` and run it from the BadTakes checkout with **bash** (zsh does not word-split the family list): `bash /tmp/collab-concat.sh` (base defaults to `git merge-base origin/main HEAD`).

```bash
#!/bin/bash
set -u
BASE=${1:-$(git merge-base origin/main HEAD)}
show() { if [ "$1" = WT ]; then cat "web/$2"; else git show "$1:web/$2"; fi; }
family() {
  local first=1 f
  for f in $(show "$1" index.html | grep -oE 'src="collabweb(-[a-z]+)?\.js"' | cut -d'"' -f2); do
    if [ "$first" = 1 ]; then show "$1" "$f"; first=0
    else show "$1" "$f" | awk -v m="'use strict';" 'p { print } $0 == m && !p { p = 1 }'; fi
  done
}
echo "base $BASE: $(show "$BASE" index.html | grep -oE 'src="collabweb(-[a-z]+)?\.js"' | cut -d'"' -f2 | tr '\n' ' ')"
echo "tree: $(show WT index.html | grep -oE 'src="collabweb(-[a-z]+)?\.js"' | cut -d'"' -f2 | tr '\n' ' ')"
diff <(family "$BASE") <(family WT) && echo CONCAT_OK
```

   Dry-run results against `20d11e1`:
   - The eight-file tree prints `CONCAT_OK`.
   - The intermediate tree (`collabweb-model.js` + `collabweb.js` holding 260-1580) prints `CONCAT_OK`.
   - One identifier renamed inside `collabweb-sync.js` prints a one-hunk `727c727` and exits 1.
   - A head missing `'use strict';` drops that whole file from the stream: a 104-line diff.

   Any hunk means a byte changed during the move.
4. **No duplicate top-level name on the page.** `cat web/*.js | grep -oE "^(async function|function|const|let|class) [A-Za-z0-9_\$]+" | awk '{print $NF}' | sort | uniq -d` must print nothing. Today there are 325 names across the eight view scripts and no duplicate; the dry-run split added none. The glob covers the `app-*`/`booth-*` families too, which matters because this family carries generic names (`icon`, `ICON`, `dead`, `meId`, `hostId`, `isHost`, `setStatus`, `foldChar`, `charLines`, `activeLines`, `linesLabel`, `waveLoader`, `castBar`) that a sibling split or a later helper could collide with. A second `const` of the same name is a `SyntaxError` on the second tag and kills the page.
5. **Every piece parses**: `for f in web/collabweb*.js; do node --check "$f" || echo "FAIL $f"; done`.
6. **Load-order probe** (the cheap TDZ check; the guest-mode vm does the real one):

```bash
node -e '
const fs=require("fs"),vm=require("vm");
const files=[...fs.readFileSync("web/index.html","utf8").matchAll(/<script src="(collabweb[^"\/]*\.js)"/g)].map(m=>m[1]);
for (const order of [files, [...files].reverse()]) {
  const ctx={console};ctx.window=ctx;ctx.document={};vm.createContext(ctx);
  for (const f of order) vm.runInContext(fs.readFileSync("web/"+f,"utf8"),ctx,{filename:f});
  console.log(order.join(" "),"|",["collab","mixTakes","collabActive","wireCollab","maybeApplyInviteFromUrl","renderCollabsTab","renderLiveStrip","pendingInvite"].map(n=>n+":"+vm.runInContext("typeof "+n,ctx)).join(" "));
}'
```

   Dry run: both orders load and every name resolves (`object`/`function`).
7. **Browser, `npm run web`** (signed out; no stack needed), at desktop width and in the Browser pane's 375px mobile preset:
   - The Collabs tab (the rail's Join on desktop, the tab bar on the phone) explains itself and shows Sign in.
   - `/?join=BT-ABC234` lands on the tab naming the invite, never on sign-in.
   - The Scenes tab shows no live strip.
   - The console shows no `ReferenceError`, the one symptom a missing or misordered tag would have.
8. **The collab pass, `npm run web:local`.** This is the one that exercises this file end to end. It runs two origins as two accounts: `localhost` is the host and `127.0.0.1` the guest. It needs the local stack up and the functions served (`server/README.md` § Local development: podman, `DOCKER_HOST`, `-x edge-runtime,vector,logflare`); `start-local` and `web:local` refuse rather than start it. Walk it at both widths:
   - Host: a dub scene → the entry row's Collab → setup with the meter running (castable toggles, multi-character, List publicly) → Start Collab → Record ◉ a character → the booth strip reads "Recording … for the collab" → record a take (the debounced push) → back in the room, the sync line settles → copy or share the code.
   - Guest: the Collabs tab → paste the code → Join (scene downloads, digest checked, pinned) → the modal → Record ◉ another character → a take → the host's toast "1 new take from your collab" within the 20s focus poll.
   - Both press Record ◉ on one character: the loser reads "Beaten to it".
   - Public list and Scenes strip at 375px (a browser, so `publicListShown()` is true).
   - "Your collabs" lists the room with the host's Close.
   - Edit scene is locked on both devices while the room is active (`collabEditLock`).
   - Host End → WRAPPING / WRAP; the guest's modal "That's a wrap" → Watch the dub plays both voices (`mixTakes`); Reopen; Leave (guest); Cancel collab (host); Close from "Your collabs".
9. **Sizes.** `wc -l web/collabweb*.js` totals 1594 (1580 plus seven two-line heads), and `git diff --stat` shows `web/collabweb.js` shrinking by exactly what the new files gained, minus heads.

## 9. Phase 2 order

Peel from the head of `collabweb.js`. Each step moves the next contiguous range into a new file, inserts its tag directly before `collabweb.js`'s, and leaves `collabweb.js` as a new head plus the remainder. That order is Eric's, and it keeps the concatenation check exact at every commit:

| step | moves | lines | Eric's stage | also in this step |
|---|---|---|---|---|
| 1 | `collabweb-model.js` (1-259) | 259 | shared constants, pure utilities, state and the wire | **The test harness edit** (section 7, `web-guest-mode.test.js`), unless an app or booth split already added `group()`. `collabweb.js` gets its final head now. `web/README.md:292` gets its first row. |
| 2 | `collabweb-host.js` (260-494) | 235 | services and flows | `server/CLAUDE.md:687` |
| 3 | `collabweb-seat.js` (495-597) | 103 | services and flows | — |
| 4 | `collabweb-sync.js` (598-818) | 221 | services | — |
| 5 | `collabweb-modal.js` (819-926) | 108 | views: the doors and the state machine | `web/CLAUDE.md:79`, `mobile/CLAUDE.md:62`, `docs/MOBILE-STORES.md:136`, `web/index.html:226` |
| 6 | `collabweb-phases.js` (927-1193) | 267 | views | `web/README.md:451` |
| 7 | `collabweb-tab.js` (1194-1454); `collabweb.js` is now the orchestrator (1455-1580) | 261 | views, then the entry reduced | `web/index.html:209`, `web/account.js:572`, `:610`, `web/README.md:209`, the rest of `:292` |
| 8 | fix the two stale comments inside `collabweb-model.js` (7, 81-82) | — | after the move | its own commit after step 7, so the check stays exact on 1-7; the check reports these two hunks and nothing else |

**Recommendation: one PR, one commit per step.** Every commit is green on the section 8 tests, `npm run web:build`, the concatenation check against the PR's base, the duplicate sweep and `node --check`. The `npm run web` pass runs on step 1 and again on the finished tree. The `web:local` two-account pass runs once on the finished tree before `gh pr create`. The reasons:
- A half-split family on `main` is not a state anyone wants.
- The review of a pure move is the check, not the line count.
- The one test edit lands in the commit that makes it necessary.

If Eric wants smaller reviews, cut three PRs at the same seams, each exact against its own base:
- PR 1: step 1 with the harness edit.
- PR 2: steps 2-4 (host, seat, sync).
- PR 3: steps 5-8 (modal, phases, tab, the entry, the comment fix).

Each PR follows `committing-and-pushing` (branch, the checks on the exact tree, then `gh pr create`).

**Order against the sibling splits.** The three families share no load-time coupling, so they can land in any order. Only the `group()` helper is shared: the first PR to land adds it and the others use it. The duplicate sweep in section 8 runs over `web/*.js` in every PR of every family.

## 10. Risks

1. **Load order and the temporal dead zone: none today, and keep it that way.** Every column-0 initializer in this file is a literal, `new Set()`, `new Map()` or an arrow; the scope analysis finds no load-time reference to anything outside the file except `Set`/`Map`. Every reader elsewhere is inside a function that runs after `start.js`. Phase 2 must not add a column-0 statement to any family file that reads `state`, `$`, `collabOn` or a collab name from another file.
2. **A moved declaration keeps its kind.** Readers guard with `typeof X === 'function'`, which works for both a `function` and a `const` arrow once the family has loaded. However, `typeof` on a lexical binding in its dead zone *throws* rather than reading `'undefined'`. Splitting creates one new way into that state: if one family file throws while loading, its `const`/`let`s stay uninitialized for the life of the page, and a later guard such as `typeof collabActive` (app.js:1222) throws instead of skipping. One file used to be all-or-nothing; eight files can fail partway. Steps 5 and 6 of section 8 and the vm test catch this before merge. Never tidy `collabActive`, `collabBoothSeat`, `publicListShown` and similar from `const` to `function` or back during the move.
3. **The family loads as a unit.** booth.js:31 and :552 read `collab` and `resolveCollabTakes` unguarded, behind guards on `collabBoothSeat` (seat) and `collabActive` (model). A page that loaded `collabweb-seat.js` without `collabweb-model.js` would throw inside `enterBooth`. No such configuration exists; do not create one, for example a build or test list that names only some of the eight.
4. **Duplicate top-level names** (section 8, step 4). There are none today and the split adds none, but this family's generic names are the likeliest to collide with a later helper in any `web/*.js`. The error is a `SyntaxError` on the second tag, which kills the page on its first byte (the `index.html` note on `src/scenestats.js`).
5. **The tests' string scans** (2e). The `openSignIn()` scan must read the whole family; left on `collabweb.js` alone, it passes and silently stops covering `collabweb-modal.js:880`. The vm list must load the whole family; left alone, it fails loudly (9 of 10). `fnBody` is not called on this file today; if a future pin uses it, the target must stay a top-level `function` declaration, because an arrow will not match.
6. **Shared rules.** `CollabRules` (`src/collab.js`, loaded byte-identical) decides every rule; the `x-upsert` choice stays in `web/lib/api.js`'s `storagePut`; the digest stays in `web/lib/scenedigest.js`, pinned by `test/scenedigest-web.test.js`. The split moves call sites only. A reviewer who "inlines" `CollabRules.phaseActive` into a helper during the move has created a third copy of a rule, which the check reports as a hunk.
7. **Per-module state is not introduced.** `collab`, `collabSync`, `collabAutoPulled`, `thumbUrls` and `renderCollabButton.plain` stay single bindings in the shared global scope. No `let` is written from two files (section 4). The risk is a reader looking for a writer in the wrong file, not a change in behaviour.
8. **What verification cannot see.**
   - Automated coverage of this file is only the guest paths and the list/mine responders in the vm test. The host, seat and sync flows (create, upload, Start, claim, push, pull, heartbeat, wrap, reopen, close, adopt) are exercised only by the `web:local` two-origin pass.
   - The BAD-008 orphan paths (an upload that fails while the close does not land; `already_hosting` → `adoptHostedRoom`) need a forced failure, such as going offline mid-upload, to see at all.
   - `web:local` uploads to Supabase Storage (with `x-upsert`); production uploads to R2 (without it), which only my.badtakes.io shows.
   - The iOS shell's hidden public list is pinned by the vm test through `Native.isNative()`; the real shell needs `npm run mobile:build` and the simulator.
   - The 60s poll and heartbeat need real waiting.
   - The phone layout's full-screen phases and the pinned `.collab-card .modal-actions` bar show only at 375px.
9. **Git's rename heuristics.** `collabweb.js` keeps 126 of 1580 lines, so `git diff -M` may pair it with a larger new file and show the entry as new. That is cosmetic; the concatenation check is the review, not the diff.
10. **Cache.** Every tag gets its own digest stamp, so a returning my.badtakes.io visitor cannot boot the new page against an old `collabweb.js`. That is exactly the failure `scripts/build-web.js:41` records. Nothing to do, but an `index.html` hand-edited on the host would ship unstamped tags.
