# BAD-036 spike: extracting `renderer/app.js`

Written by Estima (01, architect) for [[BAD-036]]. Everything below was measured on BadTakes `main` at `20d11e1` with a parser census (`admin/node_modules/typescript`, `createSourceFile` in JS mode) over the file, a cross-reference of every declared name against every other script that can see it, and a full read of the file. Line numbers are the file's today.

## 1. The file today

- **Path:** `renderer/app.js`, **8,991 lines**, 610 top-level statements, **471 top-level bindings** (`const`, `let`, `function`, `async function`; no `var`, no `class`). It opens with `'use strict';` and is one classic script: every top-level binding lives in the page's shared global lexical scope.
- **Runtime:** the Electron renderer (contextIsolation on, nodeIntegration off). It reaches main only through `window.vc`, the bridge `preload.js` exposes with `contextBridge.exposeInMainWorld('vc', …)` (preload.js:5). No build step, no bundler, no module syntax.
- **How it is loaded:** by `<script>` tag from `renderer/index.html` (lines 353–372), the sixteenth of twenty classic scripts, in this order:

  ```
  ../src/jobs.js  ../src/restart.js  ../src/handles.js  ../src/flags.js  ../src/catalog.js
  ../src/takes.js  ../src/collab.js  ../src/align.js  ../src/score.js  ../src/camtrack.js
  ../src/canvaslayout.js  ../src/reellayout.js  ../src/credits.js  reel.js
  app.js                                         ← this file
  ../src/sanitize.js  ../src/announce.js  creator.js  announce.js
  ```

  Everything before it is a UMD module exposing one global (`Jobs`, `Restart`, `Handles`, `Flags`, `Catalog`, `Takes`, `CollabRules`, `Align`, `Score`, `CamTrack`, `CanvasLayout`, `ReelLayout`, `Credits`, `ReelRender`). Everything after it (`creator.js`, `announce.js`) reads app.js's globals by bare name. `sanitize.js` and `announce.js` (the `Announce` module) load after app.js and are only referenced from inside functions, never at app.js's load time.
- **Who loads it:** `main.js`'s `createWindow` loads `renderer/index.html` (the only page that names it). The `--smoke` walkthrough (`main.js` 5992–6540) drives it with `win.webContents.executeJavaScript` strings; `scripts/site-shots.js` drives it over CDP. `web/`, `mobile/`, `cli/` and `server/` never load it (`web/index.html` names no `../renderer/` file, so `scripts/build-web.js` copies none).
- **The CSP** (`index.html:5`): `default-src 'self'; img-src 'self' pack:; media-src 'self' pack: blob:; connect-src 'self' pack: blob:; style-src 'self'`. There is no `script-src`, so scripts fall under `default-src 'self'`: any same-origin file loaded by tag is allowed, inline scripts and injected styles are not.

## 2. Public surface (the compatibility contract)

Everything outside the file that reaches into it. A name in this section must exist under the same identifier, in the same global scope, after the split. Names not listed here are internal and may move freely between files.

### 2a. Bindings read by later script tags

Verified by grepping each of app.js's 471 names in the other renderer scripts and reading every hit; comment-only and same-named-local hits are excluded.

| Binding | Declared at | Read by |
| --- | --- | --- |
| `$` | 5 | creator.js throughout (e.g. 188, 878, 2092). announce.js declares its own `$` inside its IIFE (announce.js:22) and does **not** read this one. |
| `svgIcon` | 46 | creator.js 919, 969, 980, 1016, 1055, 1244 |
| `state` | 61 | creator.js 1655–1656 (`state.packs.find`) |
| `rgba` | 118 | creator.js 461, 462, 482, 493, 505, 514, 544 and 11 more |
| `decodeUrl` | 299 | creator.js 281, 1904 |
| `toast` | 542 | creator.js 272, 1637, 1638, 1951, 1999 |
| `fmtTime` | 550 | creator.js 188, 594, 789, 878, 879, 880, 924 and 4 more |
| `showView` | 679 | creator.js 181, 1654, 2011; announce.js 141, 209 |
| `goHome` | 710 | creator.js 181 |
| `refreshPacks` | 1563 | creator.js 1648 |
| `selectPack` | 1659 | creator.js 1656 |
| `VIDEO_RE` | 2457 | creator.js 1998 |
| `session` | 3846 | announce.js 109 (`typeof session === 'object' && session.packFolder`) |
| `endGameSession` | 3933 | creator.js 1635 |

`reel.js` loads *before* app.js and reads nothing from it (its `$` hits are `${…}` template literals and its `rgba(` hits are CSS colour strings). `preload.js` cannot read renderer globals (context isolation); its `views`/`session`/`mix`/`collab` hits are property names in the bridge object.

### 2b. The `window.*` handshake between the renderer scripts

Set by app.js, read by later scripts (these are the file's deliberate exports; keep the assignment and its line-position relative to the declaration it exports):

| Property | Set at | Read by |
| --- | --- | --- |
| `window.setWorkflowHeader` | 622 | creator.js 130, 141 |
| `window.confirmDialog` | 651 | creator.js 174 |
| `window.renderCreatorQuota` | 906 | creator.js 1854 |
| `window.flagOn` | 1199 | creator.js 2980; announce.js 67 |
| `window.creatorAllowed` | 2593 | creator.js 2033 |

Set by later scripts, read by app.js behind an existence guard (the guard is the existing load-order tolerance and must survive the split):

| Property | Set by | Read in app.js |
| --- | --- | --- |
| `window.creatorView` | creator.js 1847 | 100, 687–688, 731–734, 2477–2496, 2541–2544, 2771–2779, 2331–2344 |
| `window.packsListStale` | creator.js 1658, 1661, 2052, 3014 | 711–713 |
| `window.announceView` | announce.js 319 | 726, 1215 |
| `window.creatorToolsChanged` | creator.js 1780 | 2117 |

### 2c. Names main.js's `--smoke` walkthrough evaluates by string

`main.js` 5992–6540, via `js(code) => win.webContents.executeJavaScript(code, true)` (5992). Bare identifiers inside those strings:

| Name | main.js lines |
| --- | --- |
| `applyLicenseStatus` | 6021, 6032, 6038, 6066, 6076, 6082 |
| `state` | 6109, 6131, 6228, 6293, 6350, 6457, 6458, 6465, 6500 |
| `reportImport` | 6127, 6153 |
| `refreshPacks` | 6141, 6165, 6188 |
| `selectPack` | 6220 |
| `dubbedCamTimeline` | 6467 |
| `enterScreening` | 6475 |
| `screeningScene`, `creditForEntry`, `creditForSchedule`, `buildSchedule` | 6486, 6488, 6500 |
| `creditImage` | 6488 |
| `entryLineCredits`, `entryWindow`, `reelLineCredits`, `activeLines` | 6500 |

It also reads `window.vc` and `Credits` (a `src/` module) and clicks or inspects these DOM ids by `getElementById`: `btn-theme`, `btn-home`, `btn-create-pack`, `btn-add-zips`, `btn-add-zip-folder`, `btn-claude-tip`, `btn-filter`, `filter-menu`, `btn-start-booth`, `btn-edit-pack`, `btn-upgrade`, `license-email`, `license-tier`, `usage-allowance`, `signin-status`, `view-activate`, `toast`, `drop-overlay`, `scene-export-menu`, `wh-back`, `wh-next`, `wh-next-label`, `wh-next-home`, `wh-title`, `wh-step`, `tr-stage-wrap`, `btn-tr-start`, `cue-card`, `cue-cam`, `btn-camera`, `btn-camera-state`, `btn-record`, `btn-play-ref`, `btn-play-take`, `btn-next-line`, `record-caption`, `rec-chip`, `rec-chip-time`, `take-score`, `score-donut`, `score-percent`, `score-band`, `waveform`, `stage-wrap`, `cam-card`, `cam-video-a`, `cam-video-b`, `view-screening`, `scene-score`, `scene-score-takes`, `scene-score-percent`, `scene-score-band`, `btn-export`, `export-menu`, `btn-export-reel` (plus `querySelector`s over `#browse-list`, `#scene-history-list`, `#timeline`, `#tr-timeline`, `#voices-menu`, `#booth-progress`, `#scene-score-cast`, `#scene-donut`, `#creator-step-source`). The ids belong to `index.html`; the split changes none of them.

### 2d. Names driven over CDP

`scripts/site-shots.js` evaluates `state` (117, 145, 337, 348–353), `selectPack` (353), `showView` (366), `enterTableRead` (372), `startBooth` (378), `enterScreening` (384), `goHome` (393) — and creator.js's `cstate`/`renderTakes`. The root `CLAUDE.md` documents ad-hoc CDP driving of `state`, `selectPack`, `buildSchedule`, `renderMixToWav` (and notes `window.state` is undefined: `state` is a top-level `const`, referenced bare — which is exactly why the split must stay classic scripts).

### 2e. IPC channel names

app.js never names a channel; it calls 63 `window.vc` methods, which `preload.js` maps to channels. The mapping is preload's contract with main and is untouched by the split; it is listed so a reviewer can see which file owns which channel after section 4.

| `window.vc` method → channel |
| --- |
| `askCameraAccess`→`media:askCamera` · `collabClaim`→`collab:claim` · `collabCreate`→`collab:create` · `collabHeartbeat`→`collab:heartbeat` · `collabJoin`→`collab:join` · `collabLeave`→`collab:leave` · `collabList`→`collab:list` · `collabPull`→`collab:pull` · `collabPush`→`collab:push` · `collabReopen`→`collab:reopen` · `collabStart`→`collab:start` · `collabStatus`→`collab:status` · `collabTakes`→`collab:takes` · `collabThumb`→`collab:thumb` · `collabUpdate`→`collab:update` · `collabWrap`→`collab:wrap` |
| `deletePack`→`packs:delete` · `deleteRecording`→`rec:delete` · `deleteSceneRecording`→`scene:delete` · `exportMix`→`mix:export` · `exportPackZip`→`packs:exportZip` · `exportVideo`→`video:export` · `getSettings`→`settings:get` · `setSettings`→`settings:set` · `importPackPaths`→`packs:importPaths` · `importZipsFromFolder`→`packs:pickZipFolder` · `pickAndImportZips`→`packs:pickZips` · `scanPacks`→`packs:scan` · `packsReady`→`packs:openReady` |
| `licenseDeleteAccount`→`license:deleteAccount` · `licenseOAuth`→`license:oauth` · `licenseOtpStart`→`license:otpStart` · `licenseOtpVerify`→`license:otpVerify` · `licenseQuota`→`license:quota` · `licenseSignOut`→`license:signout` · `licenseStatus`→`license:status` · `licenseUsage`→`license:usage` · `profileCheckHandle`→`profile:checkHandle` · `profileGet`→`profile:get` · `profileUpdate`→`profile:update` |
| `listRecordings`→`rec:list` · `listSceneRecordings`→`scene:list` · `saveRecording`→`rec:save` · `saveSceneRecording`→`scene:save` · `recordingStart`→`activity:recordingStart` · `recordingFinish`→`activity:recordingFinish` · `prepareVideo`→`video:prepare` · `reelStart`→`reel:start` · `reelFrames`→`reel:frames` · `reelFinish`→`reel:finish` · `reelAbort`→`reel:abort` · `scoreConfig`→`score:config` · `scoreTrend`→`score:trend` · `track`→`analytics:track` · `updatesAct`→`updates:open` · `updatesCheck`→`updates:check` · `updatesStatus`→`updates:status` |
| Pushes from main (subscriptions): `onLicenseChanged`→`license:changed` · `onUpdatesChanged`→`updates:changed` · `onPacksOpened`→`packs:opened` · `onCollabChanged`→`collab:changed` · `onCollabInvite`→`collab:invite` · `onCollabProgress`→`collab:progress` |

### 2f. DOM ids it owns

243 distinct ids through `$('…')` plus 19 through `getElementById`/`querySelector`. They are `index.html`'s and do not change; section 4 lists each file's ids so ownership is visible per file.

### 2g. Text pins in tests and scripts (they name the file path)

- `test/audiorate.test.js` 49–61 reads `renderer/app.js` and asserts (a) the exact construction `new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 44100 })` (app.js 296) and (b) the first `const rate = (\d+);` in the file, expected 44100 (app.js 7946, inside `renderMixBuffer`).
- `test/fonts.test.js` 144–156 counts `await ReelRender.ready();` against `ReelRender.(createOverlayPainter|renderLandscapeCredit)(` in `renderer/app.js` (2 gates at 8563 and 8873; 2 draws at 8564 and 8875) and asserts the total across `renderer/app.js` + the two container files is ≥ 3.
- `test/release-workflow.test.js` 46 lists `/renderer/app.js` in a *fixture* asar listing; the workflow step it tests greps for `voicepacks`, `server/` and `site/` only. No change needed.
- `scripts/site-shots.js` and `main.js` (section 2c/2d) name globals, never the file path.

### 2h. Shared rules restated in this file (never reimplement, never duplicate)

`planUnlimited()` (766) mirrors `isUnlimited()` in `src/activation.js` and `_shared/scenequota.mjs`; `FREE_SCENES` (842) mirrors `_shared/scenequota.mjs`. The split moves these lines into `app/05-account.js` unchanged; it must not create a second copy or move them into `src/`.

## 3. Responsibility map

Line ranges are inclusive and cover the whole file; each range names the top-level bindings it declares (functions with `()`).

### Constants and types
- 1–59 — `'use strict'`, `$`, `ICON_PATHS`, `CATEGORY_ICON`, `svgIcon()`, `badgeSvg()`: the DOM getter and the JS-built icon set.
- 144–154 — `DEFAULT_PAD`, `TIMING_OFFSET_MAX` (from `Align`), `clampTimingOffset`.
- 758, 818–823, 842, 872, 913–918 — `PLANS`, `LOCK_COPY`, `FREE_SCENES`, `clockTime`, `PLAN_HINT`.
- 1887–1889 — `FILTER_LABELS`, `CATEGORY_LABELS`, `CATEGORY_ORDER`.
- 2205–2209, 2298 — `UPDATE_ACTION_LABEL`, `ARRIVAL_WINDOW_MS`. 2457 — `VIDEO_RE`. 2724–2726 — `SHARE_TIP`.
- 3572 — `BOOTH_STEPS`. 3759–3775 — `SCORE_EASE`.
- 4196, 4229–4230, 4407, 4803, 4853, 5415–5420, 5865–5878 — the collab storage keys, `COLLAB_ENTRY_DEFAULT`, `COLLAB_FOCUS_POLL_MS`, meter geometry, poll/debounce/retry constants.

### State
- 61–71 — `state` (packs, pack, recordings, scenes, queue, queueIndex, cast, useDub, videoUrl): the app's one shared model.
- 77, 89 — `theme`, `rgbaCache`. 145–146, 154, 163, 174–183 — settings mirrors (`padLead`, `padTail`, `timingOffset`, `camOn`, `linkImportOn`, `autoUpdateOn`, `analyticsOn`, `catalogTipSeen`, `tipShown`).
- 296–297, 317–318, 376 — `audioCtx`, `bufferCache`, `sceneFill`, `sceneBed` (decode cache and the fill/bed memo).
- 571–572, 626 — workflow-header actions, `confirmResolve`. 678 — `views`.
- 749–816, 911, 945, 954, 1184, 1294, 1460 — licence/plan/quota/profile/flags/sign-in state.
- 1556–1561, 1615, 1657, 1715 — `libState`, `armedDelete`, `selectSeq`, `unviewed`.
- 2214, 2299–2309, 2454 — `lastUpdateStatus`, restart page state, `lastInputAt`, `dragDepth`.
- 2982 — `sceneExportAnchor`. 3182–3183, 3509 — `trState`, `trMix`, `trScrubbing`.
- 3554–3574, 3723, 3785 — booth capture state (`micStream`, `recorder`, `camRecorder`, `analyser`, `levelRaf`, `preview`, `recStartMs`, `recStopMs`, `wave`, `waveSeq`, `boothStep`, `playTakeWhenSaved`, `scorePending`, `scoreRaf`).
- 3846–3856 — `session` (the game session). 4077–4085 — `collab`; 4188, 4205, 4239–4240, 4416, 4840–4841, 5880–5885, 6054, 6176, 6233, 6410 — the collab maps, drafts, sync state, heartbeat timer, event dedupe, public-list sequence.
- 6857 — `scoreCfg`. 7201, 7206 — `mix`, `screeningScene`. 7415, 7451, 7459, 7502, 7576, 7678, 7869, 7899, 7970, 8182–8184 — stage/cam/seek/scrub/autosave/scene-score state.

### Pure utilities
- 224–244 — `leadOf()`, `waveLoader()`, `showLoading()`. 254–282 — the working range: `trimOf()`, `lineActive()`, `activeLines()`, `sceneWindow()`, `displayTime()` (thin wrappers over `Takes`).
- 420–436 — `sliceBuffer()`. 542–562 — `toast()`, `fmtTime()`, `fmtDate()`.
- 766–797, 825–829, 855–866 — plan labels, `signinMessage()`, `scenesLeft()`, `allowanceLine()`. 1074–1099 — `handleReason()`. 1159–1169 — `fillList()`, `countLine`.
- 1574–1593, 1694–1698, 1747–1750 — pack matching, ordering, `railSub()`. 1891–1903 — `filterLabel()`, `filterCount()`. 2145–2152 — `relDay`.
- 2318–2344, 2459–2461, 2603–2610 — `currentViewName()`, `collabRoomActive()`, `restartVerdict()`, `dragHasFiles()`, `checkoutUrl()`.
- 2868–2900 — `renderMarkdown()` (DOM-building, untrusted input). 3052–3098 — `selectedCast()`, `castLines()`, `castPill()`.
- 3675–3677, 3725–3730, 3820–3837 — `currentLine()`, `boothState()`, `computePeaks()`. 3861–3867, 3914–3975, 3994–4053 — session helpers, `activeTake()`, `dubbedResolver()`, `myCredit()`, `linePerformer()`.
- 4092–4181, 4198–4256, 4321–4325, 4409–4428, 4516–4520 — the collab rule helpers (`foldChar`, `collabActive()`, `collabResolved()`, `collabOwnerOf()`, storage read/write, `fmtClockLeft()` …).
- 6816–6818 — `urlOf()`. 6867 — `gradeOf`. 6875–6908 — `takeScore()`, `takeAlignment()`. 7211–7214 — `entryWindow()`. 7445–7448, 7463–7476 — `validFrac()`, `applyRect()`, `cardRect()`. 7620–7625 — `camIndexAt()`. 7687–7696, 7729–7735, 7746–7760, 7826–7829 — seek-strip derivations. 8128–8174 — `sceneScoreData()`. 8513–8554 — `exportName()`, `myCreditName`, `creditForSchedule()`, `creditForEntry()`. 8632–8782 — the reel's pure helpers. 8935–8965 — `encodeWav()`.

### Services and IO (IPC, Web Audio, fetch, storage)
- 91–136 — `applyTheme()`, `rgba()` (computed-style reads). 155–190 — three boot-time `window.vc.getSettings()` reads. 195–222 — `maybeShowCatalogTip()`.
- 299–305, 326–415 — `decodeUrl()`, `sceneFillItems()`, `bedItems()`, `cutSceneBed()`, `cutSceneFill()` (fetch + decode).
- 880–905, 1174–1178, 1201–1262 — `renderCreatorQuota()`, `refreshUsage()`, `applyLicenseStatus()` (IPC-fed). 1308–1421 — the sign-in forms, sign-out, delete-account, `onLicenseChanged`.
- 1563–1572, 1659–1689 — `refreshPacks()`, `selectPack()` (scan/list IPC). 1706–1745 — `unviewed` in localStorage.
- 2013–2042, 2060–2143, 2158–2192 — Settings persistence and the score trend. 2216–2283 — update status + `onUpdatesChanged`. 2346–2447 — the restart page and its idle timer.
- 2463–2532 — drag/drop install, `reportImport()`. 2554–2593 — `creatorAllowed()` (quota peek). 2734–2743, 2760–2805 — share quota peek, delete, edit/share buttons.
- 3101–3122 — `exportSceneAudio()`. 3194–3215 — `enterTableRead()` (video prepare). 3341–3494 — the table read's Web Audio transport.
- 3600–3673 — capture streams and camera permission. 3869–3939 — session start/finish (activity IPC).
- 4724–4798, 4858–4864 — `refreshCollab()`, the 20 s focus poll. 5559–5852 — every collab IPC action. 5911–6062 — the sync engine. 6178–6218 — the heartbeat. 6304–6315, 6358–6380, 6412–6422, 6571–6608 — `onCollabChanged`, join, public list, `onCollabInvite`, `onCollabProgress`.
- 6610–6636, 6762–6789, 6859–6865, 6910–7111 — waveform loads, preview playback, score config, `startRecording()`/`stopRecording()` (MediaRecorder + `rec:save`).
- 7225–7292 — `enterScreening()`. 7698–7725 — `ensureSceneWave()`. 7903–8057 — `buildSchedule()`, offline render, `autoSaveScene()`, `saveScreeningTake()`. 8059–8104, 8296–8452 — live playback. 8561–8626 — credit image, audio/video export. 8787–8929 — `exportReel()`.

### Sub-components and screens
- Jobs tray 440–540 · workflow header 573–622 · confirm dialog 627–674 · sign-in gate 1295–1357 · claim-handle modal 1444–1552 · library rail 1595–1876 · filter dropdown 1905–2009 · Settings 2013–2194 · restart page 2365–2436 · casting call 2641–2972 · history export menu 2984–3040 · table read 3175–3550 · booth (capture, cue card, waveform, take, flow) 3552–3837, 6610–7192 · collab (state, home marks, rows, modal views, actions, sync, booth strip, join, public list) 4055–6608 · screening (entry, stage cards, seek strip, schedule, scene score, transport, dropdowns) 7194–8506 · export + reel 8508–8965.

### Orchestration and wiring
- 103–107, 531–540, 620–651, 736–738, 1993–2009, 2046–2058, 2311–2314, 2443–2447, 2620–2637, 3504–3541, 3612, 4858–4864, 5529–5555, 6304–6315, 6571–6577, 6739, 7551–7572, 7663–7669, 7870–7901, 8463–8506, 8931–8933 — listeners, subscriptions, intervals.
- 8967–8991 — **boot**: `applyTheme()`, `applyFlagClasses()`, `renderFilterUi()`, `onPacksOpened`, `licenseStatus().then(applyLicenseStatus → refreshPacks → packsReady → maybeShowCatalogTip → noteArrival('boot'))`, `updatesStatus().then(renderUpdateStatus)`.

**Two facts the plan rests on.** (1) The parser census found **no top-level statement that reads a later-declared name at load time**: every top-level expression, initializer and bare block reads only `$` and names declared above it (the full list is 125 statements, almost all `$('id').addEventListener(…)`). (2) Every later-declared name is reached only from inside a function body or a callback — i.e. after load. The file therefore already tolerates being cut anywhere between two top-level statements as long as the pieces keep their order.

## 4. Proposed tree

**Shape.** A directory `renderer/app/` of classic scripts, each a contiguous slice of today's file in today's order, loaded by tag in numeric order between `reel.js` and a slim `app.js`. Nothing becomes an ES module (section 2's contract is bare-name global reads from three other scripts, the smoke harness and CDP; classic scripts share the global lexical scope, modules do not). Each part begins with `'use strict';` and a one-line header comment naming the section; nothing else is added and no line is edited.

**Why contiguous, in the file's own order.** The census in section 3 proved the file has no load-time forward reference, so any cut between two top-level statements that keeps the pieces in order cannot introduce a temporal-dead-zone or a "not yet defined" at load. It also keeps section 8's check trivial: the parts concatenated in tag order *are* the original file, line for line. The file's order is already by screen, so slicing it gives the by-screen tree the brief asks for; the by-layer view (constants → state → utilities → services → screens → wiring) is what section 3 gives and what the numbering of the first files reflects. The ten relocations to `app.js` (marked **→ app.js** below, reasoned in section 5) are the only non-contiguous moves, and they move whole statements.

**Sizes.** 250 lines is the review threshold, not a cap (Eric, 2026-09-15): files are cut by responsibility, and section 6 lists every file over 250 with the reason it stays whole.

### The files, in load order

| # | File | Lines today | Size | Responsibility |
| --- | --- | --- | --- | --- |
| 01 | `app/01-foundation.js` | 1–245 | 245 (232 after relocations) | `$`, the JS-built icon set, `state`, theme + `rgba()`, the settings mirrors (`padLead`…`tipShown`), the catalog tip, `leadOf`, `waveLoader`, `showLoading` |
| 02 | `app/02-audio-fill.js` | 246–437 | 192 | the working range (`trimOf`…`displayTime`), `audioCtx` + `decodeUrl`, scene fill, bed, `sliceBuffer` |
| 03 | `app/03-jobs-tray.js` | 438–563 | 126 | the background-jobs tray, `toast`, `fmtTime`, `fmtDate` |
| 04 | `app/04-chrome-routing.js` | 564–739 | 176 | the workflow header, confirm dialogs, `views`/`showView`/`goHome`, the wordmark |
| 05 | `app/05-account.js` | 740–942 | 203 | licence + plan state, allowance copy, `renderCreatorQuota`, the Plan row |
| 06 | `app/06-profile-usage.js` | 943–1179 | 237 | the profile row, `wireHandleField`, `handleReason`, Save profile, the Usage row |
| 07 | `app/07-license-signin.js` | 1180–1422 | 243 (242) | feature flags, `applyLicenseStatus`, sign-in forms, sign-out, delete account |
| 08 | `app/08-claim-handle.js` | 1423–1553 | 131 | the claim-handle modal |
| 09 | `app/09-library-rail.js` | 1554–1882 | 329 | `libState`, `refreshPacks`, matching, armed delete, `selectPack`, unviewed marks, `renderBrowse`, rail keyboard + search |
| 10 | `app/10-library-filters.js` | 1883–2010 | 128 | the filter dropdown, A–Z/category toggle, the window-wide click-away |
| 11 | `app/11-settings.js` | 2011–2195 | 185 | opening Settings, Cmd+, the pad/offset/camera/toggle bindings, difficulty, score trend, Rescan |
| 12 | `app/12-updates.js` | 2196–2448 | 253 (232) | update status rendering, the restart page, the idle clock |
| 13 | `app/13-import-door.js` | 2449–2638 | 190 | drop-to-install, `reportImport`, `openMakeScene`, `creatorAllowed`, checkout |
| 14 | `app/14-scene-details.js` | 2639–2901 | 263 | `renderPackView`, share tip + quota, delete, Edit/Share, source/meta lines, `renderMarkdown` |
| 15 | `app/15-scene-history.js` | 2902–3041 | 140 | "Your takes" rows and their shared export menu |
| 16 | `app/16-cast-selection.js` | 3042–3174 | 133 | `selectedCast`, `castLines`, `castPill`, `exportSceneAudio`, `startBooth`, `setBoothHeader`, Enter scene |
| 17 | `app/17-table-read.js` | 3175–3551 | 377 | the whole table read: state, entry, cast strip, script, stage, transport, scrub strip |
| 18 | `app/18-booth-capture.js` | 3552–3678 | 127 | booth capture state, steps, camera UI, capture streams, `setCameraOn`, `currentLine` |
| 19 | `app/19-booth-cue-card.js` | 3679–3838 | 160 | `renderBooth`, the cue card's three states, the score donut, `computePeaks` |
| 20 | `app/20-game-session.js` | 3839–4054 | 216 | `session`, start/finish/end, `sessionTakeOf`, `activeTake`, `dubbedResolver`, credits |
| 21 | `app/21-collab-state.js` | 4055–4182 | 128 | `collab` + the rule helpers (`foldChar`, `collabActive`, `collabResolved`, `collabOwnerOf`…) |
| 22 | `app/22-collab-sessions.js` | 4183–4398 | 216 | the sessions map, learned scene pins, NEW DUB marks, `collabRailMark`, `renderCollabHome` |
| 23 | `app/23-collab-home-rows.js` | 4399–4719 | 321 | entry marks, tick tooltip + widgets, `collabSessionRow`, `collabTakeRow` |
| 24 | `app/24-collab-refresh.js` | 4720–4947 | 228 (221) | `refreshCollab`, the entry segment, drafts, modal open/close, `renderCollabModal` |
| 25 | `app/25-collab-setup-room.js` | 4948–5157 | 210 | the setup and room views |
| 26 | `app/26-collab-session-view.js` | 5158–5410 | 253 | the live/wrapping/wrapped view, `sessionCastRows`, the sync line |
| 27 | `app/27-collab-meter.js` | 5411–5525 | 115 | the upload meter and the code note |
| 28 | `app/28-collab-actions.js` | 5526–5853 | 328 | every collab button: host, start, cancel, options, Record ◉, End, Reopen |
| 29 | `app/29-collab-sync.js` | 5854–6081 | 228 | the automatic sync engine, polling, retries, auto-pull, Watch the dub |
| 30 | `app/30-collab-booth.js` | 6082–6219 | 138 | the booth's collab strip and the lease heartbeat |
| 31 | `app/31-collab-events-join.js` | 6220–6400 | 181 (169) | tray notices for room events, leave, copy, join-by-code |
| 32 | `app/32-collab-public.js` | 6401–6609 | 209 (179) | the public list, the join modal, invites, transfer progress |
| 33 | `app/33-booth-wave-playback.js` | 6610–6844 | 235 | `loadWaveForLine`, `drawWave`, preview playback, My take, Scrap, Record |
| 34 | `app/34-booth-take.js` | 6845–7112 | 268 | score config, `takeScore`, `takeAlignment`, `startRecording`, `stopRecording` |
| 35 | `app/35-booth-flow.js` | 7113–7193 | 81 | Previous/Next, the Enter/Backspace flow |
| 36 | `app/36-screening.js` | 7194–7410 | 217 | `mix`, `screeningScene`, `entryWindow`, `enterScreening`, `renderScreening` |
| 37 | `app/37-stage-cards.js` | 7411–7670 | 260 | stage sizing, the draggable cards, the cam card's timeline and playback |
| 38 | `app/38-seek-strip.js` | 7671–7902 | 232 | `screenWave`, the strip's source and painter, jump/seek/restart, the scrub block |
| 39 | `app/39-schedule-play.js` | 7903–8105 | 203 | `buildSchedule`, offline render, `autoSaveScene`, `saveScreeningTake`, `playMix`, `startScheduleSources` |
| 40 | `app/40-scene-score.js` | 8106–8270 | 165 | `sceneScoreData`, `renderSceneScore` |
| 41 | `app/41-transport.js` | 8271–8507 | 237 | nameplate, `beginPlayback`, saved-entry playback, stop/pause/resume, Space, the two dropdowns |
| 42 | `app/42-export.js` | 8508–8627 | 120 | `exportName`, credits, `creditImage`, Audio and Video export |
| 43 | `app/43-reel.js` | 8628–8966 | 339 | the reel's helpers, `exportReel`, `encodeWav` |
| — | `app.js` | 8967–8992 | 26 (≈110) | boot, plus the ten relocated subscriptions/timers/boot reads (section 5) |

Every line of the file is in exactly one row (44 ranges, 1–8992 with the trailing newline, no gaps, no overlaps — checked by script). Statement-level detail follows; ranges are the statement's own lines, and each file also carries the comment block that precedes its first statement.

### What moves where, by name and line

#### 01-foundation.js — lines 1–245 (245 lines)
- Declarations: `$` 5; `ICON_PATHS` 12–37; `CATEGORY_ICON` 39–42; `svgIcon()` 46–48; `badgeSvg()` 54–59; `state` 61–71; `theme` 77; `rgbaCache` 89; `applyTheme()` 91–101; `rgba()` 118–136; `DEFAULT_PAD` 144; `padLead` 145; `padTail` 146; `TIMING_OFFSET_MAX` 152; `clampTimingOffset` 153; `timingOffset` 154; `camOn` 163; `linkImportOn` 174; `autoUpdateOn` 175; `analyticsOn` 176; `catalogTipSeen` 180; `tipShown` 183; `maybeShowCatalogTip()` 195–222; `leadOf()` 224–226; `waveLoader()` 232–238; `showLoading()` 242–244.
- Wiring: 'use strict'; 1; #btn-theme click 103–107; vc.getSettings reply 155–159 **→ app.js**; vc.getSettings reply 164 **→ app.js**; vc.getSettings reply 184–190 **→ app.js**.
- DOM ids: `btn-theme` `catalog-tip` `catalog-tip-close` `btn-create-pack`.
- IPC (window.vc): getSettings, setSettings.
- Contract names declared here (section 2): `$`, `svgIcon`, `state`, `theme`, `applyTheme`, `rgba`.

#### 02-audio-fill.js — lines 246–437 (192 lines)
- Declarations: `trimOf()` 254–256; `lineActive()` 258–260; `activeLines()` 262–264; `sceneWindow()` 269–277; `displayTime()` 280–282; `audioCtx` 296; `bufferCache` 297; `decodeUrl()` 299–305; `FILL_FADE` 317; `sceneFill` 318; `sceneFillItems()` 326–363; `sceneBed` 376; `bedItems()` 378–389; `cutSceneBed()` 391–403; `cutSceneFill()` 405–415; `sliceBuffer()` 420–436.
- Wiring: none.
- DOM ids: none.
- IPC (window.vc): none.
- Contract names declared here (section 2): `lineActive`, `activeLines`, `sceneWindow`, `audioCtx`, `decodeUrl`.

#### 03-jobs-tray.js — lines 438–563 (126 lines)
- Declarations: `renderJobTray()` 440–529; `toast()` 542–548; `fmtTime()` 550–555; `fmtDate()` 557–562.
- Wiring: Jobs.subscribe(…) 531; #job-pill click 533; #job-list click 535–540.
- DOM ids: `job-tray` `job-pill-badge` `job-pill-label` `job-list` `job-pill` `job-panel` `toast`.
- IPC (window.vc): none.
- Contract names declared here (section 2): `toast`, `fmtTime`.

#### 04-chrome-routing.js — lines 564–739 (176 lines)
- Declarations: `whBackAction` 571; `whNextAction` 572; `setWorkflowHeader()` 573–619; `confirmResolve` 626; `confirmDialog()` 627–638; `settleConfirm()` 639–645; `confirmTyped()` 659–674; `views` 678; `showView()` 679–706; `goHome()` 710–727; `okToLeaveCreator()` 731–734.
- Wiring: #wh-back click 620; #wh-next click 621; window.setWorkflowHeader = 622; #btn-confirm-ok click 646; #btn-confirm-cancel click 647; document keydown 648–650; window.confirmDialog = 651; #btn-home click 736–738.
- DOM ids: `workflow-header` `wh-back` `wh-back-label` `wh-title` `wh-stage` `wh-step` `btn-camera` `wh-collab` `wh-collab-code` `wh-next` `wh-next-label` `wh-next-icon` `wh-next-home` `confirm-message` `btn-confirm-ok` `confirm-modal` `confirm-typed` `btn-confirm-cancel` `view-creator` `btn-home`.
- IPC (window.vc): listRecordings, listSceneRecordings.
- Contract names declared here (section 2): `setWorkflowHeader`, `confirmDialog`, `showView`, `goHome`.

#### 05-account.js — lines 740–942 (203 lines)
- Declarations: `licenseLocked` 749; `PLANS` 758; `licensePlan` 759; `licenseRenewsAt` 760; `planUnlimited()` 766–768; `planLabel()` 778–786; `renewalLine()` 792–797; `licenseQuota` 798; `licenseShareQuota` 801; `signedInEmail` 802; `signedInHandle` 815; `licenseBypassed` 816; `LOCK_COPY` 818–823; `signinMessage()` 825–829; `FREE_SCENES` 842; `scenesLeft()` 855–857; `allowanceLine()` 859–866; `clockTime` 872; `renderCreatorQuota()` 880–905; `licenseUsage` 911; `PLAN_HINT` 913–918; `renderPlanRow()` 920–941.
- Wiring: window.renderCreatorQuota = 906.
- DOM ids: `signin-status` `creator-quota` `license-field` `usage-field` `license-email` `license-tier` `btn-upgrade` `btn-upgrade-sub` `plan-hint`.
- IPC (window.vc): licenseQuota.
- Contract names declared here (section 2): `renderCreatorQuota`.

#### 06-profile-usage.js — lines 943–1179 (237 lines)
- Declarations: `profileLoaded` 945; `profileReadOk` 954; `renderProfileRow()` 956–1017; `wireHandleField()` 1033–1066; `settingsHandleField` 1068–1072; `handleReason()` 1074–1099; `renderUsageField()` 1133–1157; `fillList()` 1159–1166; `countLine` 1168–1169; `refreshUsage()` 1174–1178.
- Wiring: #btn-save-profile click 1101–1127.
- DOM ids: `profile-field` `set-handle` `set-display-name` `set-bio` `handle-url` `handle-status` `btn-save-profile` `usage-allowance` `usage-month` `usage-none`.
- IPC (window.vc): profileGet, profileCheckHandle, profileUpdate, licenseUsage.

#### 07-license-signin.js — lines 1180–1422 (243 lines)
- Declarations: `featureFlags` 1184; `applyFlagClasses()` 1189–1195; `applyLicenseStatus()` 1201–1262; `handleSigninResult()` 1264–1283; `signinBusy` 1294.
- Wiring: window.flagOn = 1199; querySelectorAll .provider-btn 1295–1306; #signin-email-form submit 1308–1328; #signin-code-form submit 1330–1342; #btn-signin-back click 1344–1349; #btn-upgrade click 1351–1353; querySelectorAll #view-activate .buy-link 1355–1357; #btn-signout click 1359–1361; #btn-delete-account click 1373–1419; vc.onLicenseChanged 1421 **→ app.js**.
- DOM ids: `signin-email-form` `signin-email` `signin-sent-to` `signin-code-form` `signin-code` `btn-signin-back` `btn-upgrade` `view-activate` `btn-signout` `btn-delete-account` `license-email`.
- IPC (window.vc): packsReady, licenseOAuth, licenseOtpStart, licenseOtpVerify, track, licenseSignOut, licenseDeleteAccount, onLicenseChanged.
- Contract names declared here (section 2): `applyLicenseStatus`.

#### 08-claim-handle.js — lines 1423–1553 (131 lines)
- Declarations: `claimHandleField` 1444–1448; `claimProfileSnapshot` 1460; `maybePromptForHandle()` 1474–1517; `closeHandleModal()` 1524–1527.
- Wiring: $(…) 1453; #btn-handle-claim click 1529–1546; #claim-handle keydown 1550–1552.
- DOM ids: `claim-handle` `claim-url` `claim-status` `modal-host` `handle-modal` `btn-handle-claim`.
- IPC (window.vc): profileGet, profileUpdate.

#### 09-library-rail.js — lines 1554–1882 (329 lines)
- Declarations: `libState` 1556–1561; `refreshPacks()` 1563–1572; `packMatches()` 1574–1578; `matchesQuery()` 1580–1588; `visiblePacks()` 1590–1593; `renderLibrary()` 1595–1606; `armedDelete` 1615; `disarmDelete()` 1617–1626; `confirmArmedDelete()` 1628–1633; `armDeleteButton()` 1635–1642; `rowDeleteButton()` 1644–1655; `selectSeq` 1657; `selectPack()` 1659–1689; `orderedPacks()` 1694–1698; `UNVIEWED_KEY` 1706; `readUnviewed()` 1708–1713; `unviewed` 1715; `writeUnviewed()` 1717–1719; `markUnviewed()` 1723–1727; `markViewed()` 1729–1733; `pruneUnviewed()` 1737–1745; `railSub()` 1747–1750; `renderBrowse()` 1752–1852.
- Wiring: window keydown 1854–1876; #pack-search input 1878–1881.
- DOM ids: `packs-root` `library-grid` `library-none` `browse-list` `browse-detail` `view-library` `btn-start-booth` `pack-search`.
- IPC (window.vc): scanPacks, listRecordings, listSceneRecordings.
- Contract names declared here (section 2): `refreshPacks`, `selectPack`.

#### 10-library-filters.js — lines 1883–2010 (128 lines)
- Declarations: `FILTER_LABELS` 1887; `CATEGORY_LABELS` 1888; `CATEGORY_ORDER` 1889; `filterLabel()` 1891–1896; `filterCount()` 1900–1903; `renderFilterUi()` 1905–1913; `renderFilterMenu()` 1917–1955; `renderGroupUi()` 1979–1982.
- Wiring: #btn-filter click 1957–1959; #filter-menu click 1961–1975; #btn-group click 1984–1989; renderGroupUi(…) 1990; window click 1993–2009.
- DOM ids: `filter-label` `btn-filter` `filter-menu` `filter-cats` `btn-group` `scene-export-menu`.
- IPC (window.vc): none.

#### 11-settings.js — lines 2011–2195 (185 lines)
- Declarations: `bindPadInput()` 2060–2073; `bindToggle()` 2100–2110; `relDay` 2145–2152; `renderScoreTrend()` 2158–2192.
- Wiring: #btn-settings click 2013–2042; window keydown 2046–2052; bare block 2053–2058; bindPadInput(…) 2074; bindPadInput(…) 2075; #set-timing-offset change 2079–2090; #set-camera change 2094–2096; bindToggle(…) 2111–2118; bindToggle(…) 2119–2125; bindToggle(…) 2126; #set-score-difficulty change 2133–2143; #btn-rescan click 2194.
- DOM ids: `btn-settings` `set-pad-lead` `set-pad-tail` `set-timing-offset` `set-camera` `set-link-import` `set-auto-update` `set-analytics` `set-score-difficulty` `score-trend` `score-trend-none` `btn-rescan`.
- IPC (window.vc): licenseQuota, setSettings, scoreTrend.

#### 12-updates.js — lines 2196–2448 (253 lines)
- Declarations: `UPDATE_ACTION_LABEL` 2205–2209; `lastUpdateStatus` 2214; `renderUpdateStatus()` 2216–2260; `ARRIVAL_WINDOW_MS` 2298; `arrival` 2299; `restartPageUp` 2300; `restartTicker` 2301; `restartDeadline` 2302; `restartTrigger` 2303; `lastInputAt` 2309; `noteInput()` 2310; `currentViewName()` 2318–2321; `collabRoomActive()` 2326–2329; `restartVerdict()` 2331–2344; `maybeOfferRestart()` 2346–2351; `noteArrival()` 2353–2356; `tickRestart()` 2358–2363; `openRestartPage()` 2365–2376; `bindRestartCancels()` 2381–2385; `unbindRestartCancels()` 2386–2390; `cancelOnInput()` 2392–2396; `closeRestartPage()` 2398–2410; `restartNow()` 2412–2433.
- Wiring: #btn-check-updates click 2262–2265; #btn-get-update click 2267; vc.onUpdatesChanged 2268–2283 **→ app.js**; window input listeners (for-of) 2311–2313; window focus 2314; #btn-update-restart-now click 2435; #btn-update-restart-later click 2436; setInterval 2443–2447 **→ app.js**.
- DOM ids: `update-current` `btn-get-update` `btn-settings` `update-status` `btn-check-updates` `update-restart-count` `update-restart-version` `update-restart` `btn-update-restart-now` `btn-update-restart-later`.
- IPC (window.vc): updatesCheck, updatesAct, onUpdatesChanged, track.

#### 13-import-door.js — lines 2449–2638 (190 lines)
- Declarations: `dragDepth` 2454; `VIDEO_RE` 2457; `dragHasFiles()` 2459–2461; `reportImport()` 2511–2532; `openMakeScene()` 2541–2544; `creatorAllowed()` 2554–2591; `checkoutUrl()` 2603–2610; `openCheckout()` 2612–2616.
- Wiring: window dragenter 2463–2468; window dragleave 2469–2475; window dragover 2476; window drop 2477–2507; window.creatorAllowed = 2593; checkout anchors (for-of) 2620–2626; #btn-create-pack click 2628; #btn-add-zips click 2632–2634; #btn-add-zip-folder click 2635–2637.
- DOM ids: `drop-overlay` `view-creator` `btn-create-pack` `btn-add-zips` `btn-add-zip-folder`.
- IPC (window.vc): importPackPaths, licenseQuota, track, pickAndImportZips, importZipsFromFolder.
- Contract names declared here (section 2): `VIDEO_RE`, `reportImport`, `creatorAllowed`.

#### 14-scene-details.js — lines 2639–2901 (263 lines)
- Declarations: `renderPackView()` 2641–2718; `SHARE_TIP` 2724–2726; `renderShareQuota()` 2734–2743; `setShareTip()` 2745–2754; `deletePackNow()` 2760–2769; `renderSourceLine()` 2809–2835; `renderMetaLine()` 2839–2864; `renderMarkdown()` 2868–2900.
- Wiring: #btn-edit-pack click 2771–2779; #btn-share-pack click 2781–2805.
- DOM ids: `pack-poster` `pack-title` `pack-subtitle` `pack-byline` `readme-label` `pack-readme` `btn-start-booth` `btn-edit-pack` `btn-share-pack` `share-tip` `pack-source` `pack-meta`.
- IPC (window.vc): licenseQuota, deletePack, exportPackZip.

#### 15-scene-history.js — lines 2902–3041 (140 lines)
- Declarations: `renderSceneHistory()` 2908–2972; `sceneExportAnchor` 2982; `exportMenuItem()` 2984–3001; `closeSceneExportMenu()` 3003–3007; `openSceneExportMenu()` 3009–3035.
- Wiring: #scene-history-list scroll 3037–3040.
- DOM ids: `scene-history-list` `scene-export-menu`.
- IPC (window.vc): deleteSceneRecording.

#### 16-cast-selection.js — lines 3042–3174 (133 lines)
- Declarations: `selectedCast()` 3052–3071; `castLines()` 3073–3078; `castPill()` 3080–3098; `exportSceneAudio()` 3101–3122; `startBooth()` 3124–3146; `setBoothHeader()` 3151–3163.
- Wiring: #btn-start-booth click 3165–3173.
- DOM ids: `btn-start-booth`.
- IPC (window.vc): track, exportMix.
- Contract names declared here (section 2): `startBooth`.

#### 17-table-read.js — lines 3175–3551 (377 lines)
- Declarations: `trState` 3182; `trMix` 3183; `exitGame()` 3188–3192; `enterTableRead()` 3194–3215; `renderTableRead()` 3217–3225; `renderTrCast()` 3231–3242; `trToggleCast()` 3244–3257; `renderTrStage()` 3259–3268; `renderTrTimeline()` 3270–3307; `renderTrCounter()` 3309–3316; `trSetActive()` 3318–3325; `trJumpToLine()` 3327–3339; `trPlay()` 3341–3377; `trStartSources()` 3380–3394; `trFrac()` 3396–3399; `trSeek()` 3401–3416; `trTick()` 3418–3449; `trStop()` 3451–3463; `setTrTransportUi()` 3467–3469; `trPause()` 3471–3481; `trResume()` 3483–3494; `trTogglePlayback()` 3499–3503; `trScrubbing` 3509; `trFitStage()` 3543–3545; `drawTrWave()` 3547–3550.
- Wiring: #tr-stage-wrap click 3504; #btn-tr-start click 3505; bare block 3510–3537; #tr-timeline wheel 3540; #tr-timeline pointerdown 3541.
- DOM ids: `tr-stage-video` `tr-progress-fill` `tr-cast` `tr-cast-pills` `tr-stage-image` `tr-stage-caption` `tr-timeline` `tr-progress-text` `tr-stage-wrap` `btn-tr-start` `tr-seek-strip` `tr-stage` `tr-wave`.
- IPC (window.vc): prepareVideo.
- Contract names declared here (section 2): `enterTableRead`.

#### 18-booth-capture.js — lines 3552–3678 (127 lines)
- Declarations: `micStream` 3554; `recorder` 3555; `camRecorder` 3556; `analyser` 3557; `levelRaf` 3558; `preview` 3559; `recStartMs` 3560; `recStopMs` 3561; `wave` 3565; `waveSeq` 3566; `BOOTH_STEPS` 3572; `boothStep` 3573; `playTakeWhenSaved` 3574; `setBoothStep()` 3576–3581; `syncCamUi()` 3583–3595; `getCaptureStreamWith()` 3600–3609; `getCaptureStream` 3610; `syncCamPreview()` 3617–3647; `setCameraOn()` 3652–3673; `currentLine()` 3675–3677.
- Wiring: #btn-camera click 3612.
- DOM ids: `booth-keys` `btn-camera` `btn-camera-state` `set-camera` `cue-cam` `view-booth`.
- IPC (window.vc): askCameraAccess, setSettings.

#### 19-booth-cue-card.js — lines 3679–3838 (160 lines)
- Declarations: `renderBooth()` 3679–3707; `scorePending` 3723; `boothState()` 3725–3730; `renderBoothState()` 3732–3755; `SCORE_EASE` 3759–3775; `scoreRaf` 3785; `renderScore()` 3786–3816; `computePeaks()` 3820–3837.
- Wiring: none.
- DOM ids: `booth-progress` `cue-image` `cue-character` `cue-nameplate` `cue-caption` `btn-play-take` `btn-delete-take` `btn-next-line-label` `btn-prev-line` `cue-card` `rec-chip` `take-score` `record-caption` `btn-record` `booth-keys` `key-record-label` `score-band` `score-percent`.
- IPC (window.vc): none.

#### 20-game-session.js — lines 3839–4054 (216 lines)
- Declarations: `session` 3846–3856; `newSessionId()` 3861–3867; `startGameSession()` 3869–3893; `finishGameSession()` 3897–3909; `recountSessionLines()` 3914–3917; `sessionTakeOf()` 3921–3929; `endGameSession()` 3933–3939; `activeTake()` 3946–3951; `dubbedResolver()` 3961–3975; `myCredit()` 3994–3998; `linePerformer()` 4034–4053.
- Wiring: none.
- DOM ids: none.
- IPC (window.vc): recordingStart, recordingFinish.
- Contract names declared here (section 2): `session`, `endGameSession`.

#### 21-collab-state.js — lines 4055–4182 (128 lines)
- Declarations: `collab` 4077–4085; `collabNow` 4092; `foldChar` 4096; `collabInfo` 4098; `collabPhase()` 4100–4104; `collabCode()` 4106–4110; `castableCharacters()` 4115–4122; `castableCandidates()` 4130–4136; `charLines()` 4139–4142; `linesLabel` 4144; `collabActive()` 4146–4149; `collabClaims` 4151; `collabMembers` 4152; `collabMe` 4153; `collabHostId` 4154; `collabResolved()` 4159–4163; `collabOwnerOf()` 4166–4172; `resolveCollabTakes()` 4174–4181.
- Wiring: none.
- DOM ids: none.
- IPC (window.vc): none.

#### 22-collab-sessions.js — lines 4183–4398 (216 lines)
- Declarations: `collabSessionsById` 4188; `COLLAB_SCENES_KEY` 4196; `readCollabSceneMap()` 4198–4203; `collabSceneMap` 4205; `writeCollabSceneMap()` 4207–4209; `learnCollabPins()` 4211–4223; `COLLAB_FRESH_KEY` 4229; `COLLAB_SCREENED_KEY` 4230; `readIdSet()` 4232–4237; `collabFresh` 4239; `collabScreened` 4240; `writeCollabMarks()` 4242–4247; `wrapUnexpired()` 4251–4254; `collabSessionsForScene()` 4256–4276; `forgetCollabSession()` 4285–4293; `hostedSessionFor()` 4299–4301; `collabRoomInPlay()` 4314–4319; `sceneForCollabId` 4321–4325; `renderCollabHome()` 4331–4335; `applyCollabSessions()` 4338–4359; `markCollabScreened()` 4363–4372; `collabRailMark()` 4377–4397.
- Wiring: none.
- DOM ids: `view-library`.
- IPC (window.vc): none.

#### 23-collab-home-rows.js — lines 4399–4719 (321 lines)
- Declarations: `COLLAB_ENTRIES_KEY` 4407; `readCollabEntryMarks()` 4409–4414; `collabEntryMarks` 4416; `writeCollabEntryMarks()` 4418–4420; `collabEntryMark` 4422; `dropCollabEntryMark()` 4424–4428; `rememberCollabEntry()` 4433–4445; `pruneCollabMarks()` 4449–4461; `showCollabTip()` 4469–4477; `hideCollabTip()` 4479–4481; `tickTipHover()` 4483–4486; `collabTick()` 4490–4499; `hostPillEl` 4501–4506; `hiddenSummary` 4508–4513; `fmtClockLeft()` 4516–4520; `collabPinnedRows()` 4536–4542; `collabSessionRow()` 4544–4630; `collabCreditLine()` 4634–4643; `collabTakeRow()` 4647–4718.
- Wiring: none.
- DOM ids: `collab-tick-tip`.
- IPC (window.vc): deleteSceneRecording.

#### 24-collab-refresh.js — lines 4720–4947 (228 lines)
- Declarations: `refreshCollab()` 4724–4798; `COLLAB_ENTRY_DEFAULT` 4803; `renderCollabButton()` 4805–4835; `collabOpt` 4840; `collabUpdatesInFlight` 4841; `COLLAB_FOCUS_POLL_MS` 4853; `collabFocusedRoomVisible()` 4854–4857; `openCollabModal()` 4866–4886; `closeCollabModal()` 4888–4890; `renderCollabModal()` 4895–4946.
- Wiring: setInterval 4858–4864 **→ app.js**.
- DOM ids: `btn-collab-pack` `collab-modal` `view-booth` `collab-start-status` `collab-live-status` `collab-session-status` `collab-card` `collab-session-head` `collab-head` `collab-start` `collab-loading` `collab-setup` `collab-live` `collab-session` `collab-public-chip` `collab-title` `collab-loading-line` `btn-collab-start`.
- IPC (window.vc): collabStatus, collabPull, collabTakes.

#### 25-collab-setup-room.js — lines 4948–5157 (210 lines)
- Declarations: `renderCollabSetup()` 4950–4992; `renderSetupUpload()` 4996–5016; `uploadErrorTail()` 5023–5027; `renderCollabRoom()` 5031–5084; `renderRoomCast()` 5086–5129; `castRecordButton()` 5132–5148; `castLostLine()` 5151–5156.
- Wiring: none.
- DOM ids: `collab-title` `collab-castable` `collab-multi` `collab-public` `btn-collab-into-room` `btn-collab-cancel-setup` `collab-setup-status` `setup-upload-track` `collab-setup-note` `collab-public-chip` `collab-invite-slot` `collab-upload-track` `collab-code-note` `collab-code-row` `collab-code` `collab-room-hint` `collab-live-status` `btn-collab-back-setup` `btn-collab-leave` `collab-cast`.
- IPC (window.vc): none.

#### 26-collab-session-view.js — lines 5158–5410 (253 lines)
- Declarations: `renderCollabSession()` 5160–5219; `sessionCastRows()` 5223–5257; `renderSessionCast()` 5259–5355; `castBar()` 5357–5366; `collabStatusLine()` 5370–5374; `renderCollabSync()` 5379–5409.
- Wiring: none.
- DOM ids: `collab-session-dot` `collab-session-loader` `collab-session-check` `collab-session-title` `collab-session-chip` `collab-session-invite-slot` `collab-upload-track` `collab-code-note` `collab-code-row` `collab-code` `btn-collab-end` `btn-collab-reopen` `btn-collab-play` `collab-session-foot` `collab-session-hint` `collab-session-cast` `collab-session` `collab-live-status` `collab-session-status` `collab-sync`.
- IPC (window.vc): none.

#### 27-collab-meter.js — lines 5411–5525 (115 lines)
- Declarations: `COLLAB_METER_BARS` 5415; `COLLAB_CREST` 5418; `COLLAB_CREST_MID` 5419; `COLLAB_METER_FLAT` 5420; `buildCollabMeter()` 5425–5438; `paintCollabMeter()` 5446–5460; `paintUploadMeter()` 5464–5480; `renderCollabCodeNote()` 5485–5523.
- Wiring: none.
- DOM ids: `collab-code-note` `collab-upload-track` `btn-collab-copy`.
- IPC (window.vc): none.

#### 28-collab-actions.js — lines 5526–5853 (328 lines)
- Declarations: `hostCollab()` 5559–5612; `startCollabSession()` 5629–5645; `cancelHostedCollab()` 5655–5671; `applyCollabUpdate()` 5677–5711; `recordCollabCharacter()` 5741–5774; `onCastRecordClick()` 5776–5780; `endHostedCollab()` 5789–5838.
- Wiring: #btn-collab-pack click 5529–5550; #btn-collab-close click 5551; #btn-collab-close-session click 5552; #collab-modal click 5553–5555; #btn-collab-start click 5614; #btn-collab-into-room click 5623–5627; #btn-collab-back-setup click 5647–5650; #btn-collab-cancel-setup click 5672; #collab-castable change 5713–5724; #collab-multi change 5726–5730; #collab-public change 5732–5736; #collab-cast click 5781; #collab-session-cast click 5782; #btn-collab-end click 5839; #btn-collab-reopen click 5841–5852.
- DOM ids: `btn-collab-pack` `btn-collab-close` `btn-collab-close-session` `collab-modal` `collab-start-status` `btn-collab-start` `btn-collab-into-room` `btn-collab-back-setup` `btn-collab-cancel-setup` `collab-castable` `collab-multi` `collab-public` `collab-cast` `collab-session-cast` `collab-session` `collab-live-status` `collab-session-status` `btn-collab-end` `btn-collab-reopen`.
- IPC (window.vc): collabCreate, collabStart, collabLeave, collabUpdate, collabClaim, collabWrap, collabReopen.

#### 29-collab-sync.js — lines 5854–6081 (228 lines)
- Declarations: `COLLAB_POLL_MS` 5865; `COLLAB_PUSH_DEBOUNCE_MS` 5868; `COLLAB_SYNC_MAX_RETRIES` 5874; `COLLAB_RETRY_STEP_MS` 5878; `collabSync` 5880–5885; `renderIfCollabOpen()` 5887–5889; `renderCollabTransfer()` 5898–5907; `runCollabSync()` 5911–5976; `scheduleCollabPush()` 5980–5989; `flushCollabPush()` 5994–6000; `startCollabPolling()` 6002–6014; `stopCollabPolling()` 6016–6022; `stopCollabRetries()` 6029–6034; `collabAutoPulled` 6054; `autoPullWrapped()` 6056–6062.
- Wiring: #btn-collab-play click 6075–6080.
- DOM ids: `collab-modal` `collab-setup` `collab-live` `collab-session` `btn-collab-play`.
- IPC (window.vc): collabPush, collabPull.

#### 30-collab-booth.js — lines 6082–6219 (138 lines)
- Declarations: `collabBoothSeat()` 6091–6093; `boothCollabChip()` 6095–6099; `collabMyClaim()` 6102–6110; `renderBoothCollab()` 6115–6133; `boothSeatQuiet()` 6139–6143; `boothStripText()` 6145–6169; `boothHb` 6176; `sendCollabHeartbeat()` 6178–6198; `startBoothHeartbeat()` 6200–6213; `stopBoothHeartbeat()` 6215–6218.
- Wiring: #btn-booth-collab click 6171.
- DOM ids: `view-booth` `booth-collab-strip` `booth-collab-text` `btn-booth-collab`.
- IPC (window.vc): collabHeartbeat.

#### 31-collab-events-join.js — lines 6220–6400 (181 lines)
- Declarations: `collabTrayNotice()` 6225–6229; `collabEventSeen` 6233; `collabEventContext()` 6239–6242; `collabLineTotal()` 6244–6248; `postCollabEvents()` 6250–6299; `copyCollab()` 6344–6348; `joinWithCode()` 6358–6380; `wireJoinField()` 6387–6398.
- Wiring: vc.onCollabChanged 6304–6315 **→ app.js**; #btn-collab-leave click 6320–6332; #btn-collab-copy click 6349–6352; wireJoinField(…) 6399.
- DOM ids: `collab-modal` `view-booth` `btn-collab-leave` `btn-collab-copy` `collab-code`.
- IPC (window.vc): onCollabChanged, collabLeave, collabJoin.

#### 32-collab-public.js — lines 6401–6609 (209 lines)
- Declarations: `publicListSeq` 6410; `renderPublicCollabs()` 6412–6422; `paintPublicCollabs()` 6426–6522; `openJoinModal()` 6538–6546; `closeJoinModal` 6547; `applyCollabInvite()` 6553–6570.
- Wiring: #join-public-list click 6527–6536; vc.onCollabInvite 6571 **→ app.js**; #btn-join-collab click 6573; #btn-join-close click 6574; #join-modal click 6575–6577; vc.onCollabProgress 6580–6608 **→ app.js**.
- DOM ids: `join-public-list` `join-public-note` `join-modal-code` `btn-join-modal-go` `join-modal-status` `join-modal` `btn-join-collab` `btn-join-close`.
- IPC (window.vc): collabList, collabThumb, onCollabInvite, onCollabProgress.

#### 33-booth-wave-playback.js — lines 6610–6844 (235 lines)
- Declarations: `loadWaveForLine()` 6610–6636; `drawWave()` 6638–6737; `jumpToLine()` 6741–6748; `stopPreview()` 6750–6758; `playLineAudio()` 6762–6789; `urlOf()` 6816–6818; `playTake()` 6823–6833.
- Wiring: window resize 6739; #btn-play-ref click 6791–6796; #btn-play-take click 6798–6804; #btn-delete-take click 6806–6814; #btn-record click 6835–6843.
- DOM ids: `waveform` `cue-cam` `btn-play-ref` `btn-play-take` `btn-delete-take` `btn-record`.
- IPC (window.vc): deleteRecording.

#### 34-booth-take.js — lines 6845–7112 (268 lines)
- Declarations: `scoreCfg` 6857; `refreshScoreConfig()` 6859–6865; `gradeOf` 6867; `takeScore()` 6875–6893; `takeAlignment()` 6895–6908; `startRecording()` 6910–7099; `stopRecording()` 7101–7111.
- Wiring: refreshScoreConfig(…) 6868.
- DOM ids: `btn-record` `waveform` `rec-chip-time`.
- IPC (window.vc): scoreConfig, saveRecording.

#### 35-booth-flow.js — lines 7113–7193 (81 lines)
- Declarations: `runBoothStep()` 7139–7167; `boothStepBack()` 7169–7178.
- Wiring: #btn-prev-line click 7113–7115; #btn-next-line click 7116–7135; window keydown 7180–7192.
- DOM ids: `btn-prev-line` `btn-next-line` `view-booth`.
- IPC (window.vc): none.

#### 36-screening.js — lines 7194–7410 (217 lines)
- Declarations: `mix` 7201; `screeningScene` 7206; `entryWindow()` 7211–7214; `enterScreening()` 7225–7292; `renderScreening()` 7294–7409.
- Wiring: none.
- DOM ids: `stage-caption` `btn-export-video` `btn-export-reel` `stage-video` `stage-image` `voices-menu` `btn-voices` `timeline`.
- IPC (window.vc): prepareVideo.
- Contract names declared here (section 2): `screeningScene`, `entryWindow`, `enterScreening`.

#### 37-stage-cards.js — lines 7411–7670 (260 lines)
- Declarations: `stageRatio` 7415; `fitStageBox()` 7417–7437; `validFrac()` 7445–7448; `stageLayout` 7451; `camRatio` 7459; `saveStageLayout()` 7461; `applyRect()` 7463–7468; `cardRect()` 7470–7476; `fitStage()` 7478–7496; `stageCardDragged` 7502; `wireStageCard()` 7503–7550; `camPlay` 7576; `dubbedCamTimeline()` 7580–7597; `setCamTimeline()` 7599–7606; `resetCamVideos()` 7608–7616; `camIndexAt()` 7620–7625; `updateCam()` 7627–7660.
- Wiring: try block 7452–7458; wireStageCard(…) 7551; wireStageCard(…) 7552; #btn-reset-stage click 7553–7557; #stage-video loadedmetadata 7559–7565; #stage-image load 7566–7572; #cam-video-a loadedmetadata 7663–7669.
- DOM ids: `stage-wrap` `scene-card` `cam-card` `btn-reset-stage` `stage-video` `stage-image` `cam-video-a` `cam-video-b`.
- IPC (window.vc): none.
- Contract names declared here (section 2): `dubbedCamTimeline`.

#### 38-seek-strip.js — lines 7671–7902 (232 lines)
- Declarations: `screenWave` 7678; `seekWaveSource()` 7687–7696; `ensureSceneWave()` 7698–7719; `renderScreeningWave()` 7721–7725; `screeningWindow()` 7729–7735; `drawScreeningWave()` 7737–7739; `screeningTakeIntervals()` 7746–7760; `drawSeekWave()` 7764–7796; `jumpTo()` 7801–7807; `seekTo()` 7811–7823; `mixFrac()` 7826–7829; `restartMixAt()` 7833–7865; `scrubbing` 7869; `scriptTouchedAt` 7899.
- Wiring: bare block 7870–7896; #timeline wheel 7900; #timeline pointerdown 7901.
- DOM ids: `screening-wave` `progress-fill` `cam-video-a` `cam-video-b` `seek-strip` `timeline`.
- IPC (window.vc): none.

#### 39-schedule-play.js — lines 7903–8105 (203 lines)
- Declarations: `buildSchedule()` 7903–7941; `renderMixBuffer()` 7945–7959; `renderMixToWav()` 7961–7963; `savedMixes` 7970; `autoSaveScene()` 7976–8041; `saveScreeningTake()` 8050–8057; `playMix()` 8059–8085; `startScheduleSources()` 8090–8104.
- Wiring: none.
- DOM ids: `view-library` `stage-caption`.
- IPC (window.vc): track, saveSceneRecording, listSceneRecordings.
- Contract names declared here (section 2): `buildSchedule`, `renderMixBuffer`.

#### 40-scene-score.js — lines 8106–8270 (165 lines)
- Declarations: `sceneScoreData()` 8128–8174; `sceneScoreRaf` 8182; `sceneScorePct` 8183; `sceneScoreKey` 8184; `renderSceneScore()` 8185–8269.
- Wiring: none.
- DOM ids: `scene-score` `scene-donut` `scene-score-band` `scene-score-takes` `scene-score-cast` `scene-score-percent`.
- IPC (window.vc): none.

#### 41-transport.js — lines 8271–8507 (237 lines)
- Declarations: `setStageNameplate()` 8289–8294; `beginPlayback()` 8296–8364; `playSceneRecording()` 8369–8400; `stopMix()` 8402–8421; `setTransportUi()` 8426–8428; `pauseMix()` 8430–8442; `resumeMix()` 8444–8452; `toggleScreeningPlayback()` 8457–8461.
- Wiring: #stage-wrap click 8463–8467; window keydown 8473–8485; #btn-export click 8489; #export-menu click 8491–8493; #btn-voices click 8495; #voices-menu click 8498–8506.
- DOM ids: `stage-nameplate` `stage-performer` `stage-video` `progress-fill` `timeline` `stage-image` `stage-caption` `stage-wrap` `cam-video-a` `cam-video-b` `view-screening` `view-tableread` `btn-export` `export-menu` `btn-voices` `voices-menu`.
- IPC (window.vc): none.

#### 42-export.js — lines 8508–8627 (120 lines)
- Declarations: `exportName()` 8513–8515; `myCreditName` 8524; `creditForSchedule()` 8530–8537; `creditForEntry()` 8551–8554; `creditImage()` 8561–8570; `exportDubVideo()` 8589–8611.
- Wiring: #btn-export-mix click 8572–8581; #btn-export-video click 8613–8626.
- DOM ids: `btn-export-mix` `btn-export` `btn-export-video`.
- IPC (window.vc): exportMix, track, exportVideo.
- Contract names declared here (section 2): `creditForSchedule`, `creditForEntry`, `creditImage`.

#### 43-reel.js — lines 8628–8966 (339 lines)
- Declarations: `peaksForRange()` 8632–8649; `reelCaptions()` 8658–8667; `reelLineCredits()` 8696–8710; `entryLineCredits()` 8720–8730; `takeIntervalsFromSignature()` 8734–8750; `camClipUsable()` 8755–8772; `usableCamEntries()` 8774–8782; `exportReel()` 8787–8929; `encodeWav()` 8935–8965.
- Wiring: #btn-export-reel click 8931–8933.
- DOM ids: `btn-export` `btn-export-reel`.
- IPC (window.vc): track, reelStart, reelFrames, reelFinish, reelAbort.
- Contract names declared here (section 2): `reelLineCredits`, `entryLineCredits`, `exportReel`.

#### app.js — lines 8967–8992 (26 lines)
- Declarations: none.
- Wiring: applyTheme(…) 8969; applyFlagClasses(…) 8970; renderFilterUi(…) 8971; vc.onPacksOpened 8976; vc.licenseStatus reply 8977–8989; vc.updatesStatus reply 8991.
- DOM ids: none.
- IPC (window.vc): onPacksOpened, licenseStatus, packsReady, updatesStatus.
## 5. The entry point afterwards

`renderer/app.js` keeps its path and its tag position (`<script src="app.js">` stays exactly where it is in `index.html`, after the 43 parts, before `../src/sanitize.js`), so creator.js, announce.js, the smoke harness and CDP see the same page they see today: by the time app.js's boot runs, every one of the 471 bindings exists in the shared global scope under its old name. The contract in section 2 holds byte for byte because nothing is renamed, wrapped or scoped — a binding declared in `app/09-library-rail.js` is the same global `refreshPacks` that `creator.js:1648` calls and that `main.js:6141` evaluates by string.

What stays in app.js, in this order (about 110 lines):

1. `'use strict';` and a header comment naming it the orchestrator.
2. **The three boot-time settings reads** — today's lines 155–159, 164 and 184–190 (`window.vc.getSettings().then(…)` ×3). They are boot work, and one of them (164) calls `syncCamUi()`, which is declared in `18-booth-capture.js`; an IPC reply landing while the parts are still loading would otherwise run it before that file exists (section 10).
3. **Every push subscription**, moved as whole statements: `window.vc.onLicenseChanged(applyLicenseStatus)` (1421), `window.vc.onUpdatesChanged((status) => {…})` (2268–2283), `window.vc.onCollabChanged((payload) => {…})` (6304–6315), `window.vc.onCollabInvite(applyCollabInvite)` (6571), `window.vc.onCollabProgress((p) => {…})` (6580–6608), plus today's `window.vc.onPacksOpened(…)` (8976), which is already in the boot block. A push from main is the one event that can genuinely arrive between two script tags (section 10); registering all of them after the last part loads makes the split as atomic as the monolith was.
4. **The two long-lived timers**: the 15 s idle poll (2443–2447) and the 20 s collab focus poll (4858–4864). Both reach into files declared after their original position; both are app-wide loops, which is the orchestrator's business anyway.
5. **The boot block** exactly as today (8969–8991): `applyTheme()`, `applyFlagClasses()`, `renderFilterUi()`, `onPacksOpened`, `licenseStatus().then(…)`, `updatesStatus().then(…)`.

The relocated statements are moved verbatim, callbacks and comments included. Their `let` targets (`padLead`, `camOn`, `arrival`…) stay declared in their parts; assigning a `let` declared in an earlier classic script from a later one is ordinary shared-scope behaviour, exactly what `theme = …` at line 104 already relies on. Everything else that is not a declaration — the 110-odd `$('id').addEventListener(…)` wirings, `Jobs.subscribe(renderJobTray)`, the `window.X = …` exports, the bare blocks and the `for` loops — stays beside the code it wires, because each reads only names declared above it in the same or an earlier part (section 3's census).

The `window.*` exports (`window.setWorkflowHeader`, `window.confirmDialog`, `window.renderCreatorQuota`, `window.flagOn`, `window.creatorAllowed`) stay in the parts that declare the function they export, on the same line they are today.

## 6. Where 250 lines is exceeded, and why each file stays whole

250 is the line at which a file gets a second look, not a cap (Eric, 2026-09-15). Ten proposed files are over it. For each: the honest size, what it is, and why cutting it would be a boundary rather than a seam.

| File | Size | Why it stays whole |
| --- | --- | --- |
| `17-table-read.js` | 377 | One screen sharing one state pair (`trState`, `trMix`): the view (cast strip, script, stage, counter) and the transport (`trPlay`/`trSeek`/`trTick`/`trStop`/pause/resume) read and write both. A view/transport cut at 3339/3341 is possible and would give 165 + 211, but every transport function re-renders the view (`renderTrStage`, `trSetActive`) and the view's clicks drive the transport (`trJumpToLine` → `trSeek`), so the two files would be one unit with a folder boundary through it. |
| `43-reel.js` | 339 | `exportReel` (143 lines) plus the eight helpers that exist for it (`peaksForRange`, `reelCaptions`, `reelLineCredits`, `entryLineCredits`, `takeIntervalsFromSignature`, `camClipUsable`, `usableCamEntries`, `encodeWav`). Two of the helpers are also read by the seek strip (`takeIntervalsFromSignature`, 7746) and by `renderMixToWav` (`encodeWav`, 7961); moving them elsewhere is a relocation, not a split. A helpers/export cut at 8783/8787 gives 156 + 183 if Eric wants it. |
| `09-library-rail.js` | 329 | The rail is one render function (`renderBrowse`, 101 lines) fed by one state object (`libState`), one selection (`selectPack`), one armed-delete mechanism and the unviewed marks; the keyboard handler (1854–1876) walks the same order `renderBrowse` draws. The filters were cut off into `10-library-filters.js` because they are a separate control with its own labels; a further cut at `selectPack` (1657) gives 102 + 227 but separates the delete arming from the rows that use it. |
| `28-collab-actions.js` | 328 | Every button in the collab modal (host, Start, back to setup, cancel, the three option toggles, Record ◉, End, Reopen). They share one status line (`collabStatusLine`), one confirm pattern and one refresh tail (`refreshCollab(pack, { pull: false })`). A host/seat cut at 5672/5674 gives 148 + 180 — the host-side (create/start/cancel) and the seat-side (options/record/end/reopen) — and is the one cut here that follows a real seam; it is offered, not recommended. |
| `23-collab-home-rows.js` | 321 | The takes table's collab rows: the per-entry marks they read (4407–4461), the tick/tooltip/pill widgets only they use (4469–4520), and the two row builders (`collabSessionRow` 87 lines, `collabTakeRow` 72). A marks/rows cut at 4542/4544 gives 145 + 176 and would put the widgets in a file that does not draw a row. |
| `34-booth-take.js` | 268 | `startRecording` is a 190-line function (MediaRecorder setup, the cam recorder, the finish-take closure, the level meter); the score config and `takeScore`/`takeAlignment` (52 lines) are what that closure calls. Nothing shorter is honest; the only cut is inside the function. |
| `14-scene-details.js` | 263 | `renderPackView` (78) and the seven things it calls to paint the pane (share tip, share quota, source line, meta line, markdown, the Edit/Share buttons' handlers, delete). "Your takes" was cut into `15-scene-history.js` because it has its own menu and rows. |
| `37-stage-cards.js` | 260 | Stage sizing (`fitStage`, `cardRect`, the draggable cards) and the cam card (`camPlay`, `dubbedCamTimeline`, `updateCam`). They share `stageLayout`, `camRatio` and `fitStage()` (the cam card's `setCamTimeline` calls it; the card drag reads `camRatio`). A cut at 7572/7574 gives 162 + 96. |
| `12-updates.js` | 253 | The update status row and the restart page are the two halves of one delivery story (the page reads `lastUpdateStatus`, the row's toggle redraws from the page's `arrival`); the idle interval and `onUpdatesChanged` already leave for app.js, which brings it to 232. |
| `26-collab-session-view.js` | 253 | One view (`renderCollabSession`), the rows it draws (`sessionCastRows`, `renderSessionCast`, `castBar`) and the sync line it ends with. `sessionCastRows` is also read by `boothStripText` (6165) and `collabSessionRow` (4599). |

Files well under 250 that are small because their responsibility is small, not because a bigger unit was shattered: `35-booth-flow.js` (81: Previous/Next and the Enter/Backspace flow), `27-collab-meter.js` (115: the meter and the code note, shared by two views), `42-export.js` (120), `03-jobs-tray.js` (126), `18-booth-capture.js` (127), `21-collab-state.js` (128), `10-library-filters.js` (128), `08-claim-handle.js` (131), `16-cast-selection.js` (133), `30-collab-booth.js` (138), `15-scene-history.js` (140). If Eric prefers fewer files, the merges that keep a single responsibility are: 03 + 04 (302, "the chrome"), 07 + 08 (374, "sign-in and the handle"), 18 + 19 (287, "the booth's card and capture"), 33 + 34 + 35 (584, "the booth's take loop" — too big to recommend), 39 + 40 + 41 (605, "playback" — the score panel sits between the two halves of playback in the file, so a pure-move merge takes it along; not recommended).

One misplacement worth naming because a pure move cannot fix it: the scene-score panel (8106–8270) sits between `playMix`/`startScheduleSources` (8059–8104) and `beginPlayback` (8296), splitting playback's two halves across `39-schedule-play.js` and `41-transport.js`. Reuniting them is a later, non-contiguous relocation of two statements once the split has landed.

## 7. Build and packaging touch points

| Touch point | Change |
| --- | --- |
| `renderer/index.html` 367–368 | Insert 43 `<script src="app/NN-name.js"></script>` tags in numeric order between `reel.js` and `app.js`. Nothing else in the page changes; `app.js` keeps its tag and its position. |
| Renderer CSP (`index.html:5`) | **No change.** There is no `script-src`, so `default-src 'self'` already admits any same-origin script tag; the new files are same-origin. Inline scripts stay forbidden, which the split never needs. |
| `package.json` `build.files` (line 69) | **No change.** `renderer/**/*` already bundles `renderer/app/`. Confirmed: the allowlist is `main.js, preload.js, src/**/*, renderer/**/*, brand/*.png, brand/fonts/*, package.json, node_modules/**/*`. |
| `.github/workflows/release.yml` asar check (178–201) | **No change.** It greps the asar listing for `voicepacks`, `server/` and `site/`; it asserts no file list. |
| `test/audiorate.test.js` 49–61 | Point the `audioCtx` regex at `renderer/app/02-audio-fill.js` and the `const rate = (\d+);` regex at `renderer/app/39-schedule-play.js` (or scan `renderer/app/*.js` and assert exactly one match each). Change in the PR that moves each line, or the test fails on that PR. |
| `test/fonts.test.js` 144–156 | Add `renderer/app/42-export.js` and `renderer/app/43-reel.js` to the file list (or glob `renderer/app/*.js`). Without it, `renderer/app.js` counts 0 gates/0 draws (the per-file equality passes) but the ≥ 3 total fails once the two gates leave app.js. |
| `scripts/site-shots.js`, `main.js --smoke` | **No change** — they name globals, which are unchanged. |
| `web/`, `scripts/build-web.js`, `mobile/`, `cli/`, `server/` | **No change.** None of them loads `renderer/app.js`; `build-web.js` copies only what `web/index.html` names under `../renderer/`, which is nothing. |
| Dev loop (`npm start`, `start:free`, `start:local`) | **No change**: the page is loaded from the tree, so the tags load the new files the moment they exist. |
| CI | **No change** to workflows; the existing `npm test` (with the two test edits above) and the Windows build's asar check cover it. |
| Docs | `renderer/CLAUDE.md` names functions "in `app.js`" in a dozen places (`sceneFillItems`, `bedItems`, `setWorkflowHeader`, `renderBoothState`, `renderJobTray`…); a follow-up doc pass should say `renderer/app/` — not blocking, since every name still resolves. |

## 8. Verification recipe

**Tier.** `renderer/*` → the implementing-changes table: a scoped `npm run smoke` with `BT_LIBRARY_ROOT` at a *copy* of `fixtures/library`, `SMOKE_OK` printed and exit 0, screenshots inspected. Scopes are prefixes, so `SMOKE_SCOPE=screening` also proves home, table read and booth.

```bash
rm -rf /tmp/bt-lib && cp -R fixtures/library /tmp/bt-lib
BT_LIBRARY_ROOT=/tmp/bt-lib SMOKE_SCOPE=<leg> npm run smoke
```

Which leg proves which files: `home` → 01–16 (gate, tray, chrome, account, library, filters, import door, scene details, history, cast selection) and app.js; `tableread` → 17; `booth` → 18–20, 33–35 (and 30 for the strip's hidden state); `screening` → 36–41 plus 39's auto-save; `history` → 15 again with a saved entry, 42's per-row export menu; a full `npm run smoke` (the `reel` leg) → 42–43 and the credit functions the walkthrough evaluates by name (section 2c). The collab files 21–32 have no smoke leg (`main.js` never polls under `--smoke` and the modal is flag-gated): verify them with the concatenation check below plus one manual open of the Collab modal on a dub scene in the running app, which Eric does per PR anyway. Settings (11–12) likewise: open Settings over CDP or by hand after `npm start`. `npm test` runs the two text pins in section 2g (`test/audiorate.test.js`, `test/fonts.test.js`).

**Behaviour preservation: the concatenation check.** Because the parts are contiguous slices in order, the parts concatenated in tag order must reproduce the original file except for (a) each part's header (`'use strict';` and one comment line) and (b) the ten relocated statements. Run from a BadTakes checkout on the branch, against the pre-split file:

```bash
# 1. the parts in tag order, headers stripped, then app.js
tags=$(grep -o 'src="app/[^"]*"' renderer/index.html | sed 's/src="//;s/"//')
{ for f in $tags; do tail -n +3 "renderer/$f"; done; tail -n +3 renderer/app.js; } > /tmp/app.concat.js
# 2. diff against main's file: only the relocated statements may show as moved hunks
git show main:renderer/app.js > /tmp/app.orig.js
diff /tmp/app.orig.js /tmp/app.concat.js
```

The diff must be empty apart from the ten statements of section 5 (each appears once as a deletion at its old line and once as an insertion at the end). For a diff that must be *exactly empty*, compare statement multisets instead — it tolerates moves and proves no statement was edited, dropped or duplicated:

```bash
node -e '
const ts = require("./admin/node_modules/typescript"), fs = require("fs");
const stmts = (p) => { const s = fs.readFileSync(p, "utf8"); const sf = ts.createSourceFile(p, s, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  return sf.statements.map((st) => st.getText(sf)).filter((t) => t !== "\x27use strict\x27;").sort(); };
const a = stmts("/tmp/app.orig.js"), b = stmts("/tmp/app.concat.js");
console.log(a.length, b.length, JSON.stringify(a) === JSON.stringify(b) ? "IDENTICAL statement sets" : "DIFFER");'
```

Expected: `610 610 IDENTICAL statement sets` (the original has one `'use strict'`, the concatenation has 44; both are filtered). Run both checks on every Phase 2 PR; the statement-set check is the one that has to say IDENTICAL, the line diff is the one a reviewer reads. `admin/node_modules/typescript` is already in the repo (the admin dashboard's compiler), so nothing is installed for this.

**Load-order check.** After the tags are in place, `npm start` with DevTools open must show no `ReferenceError` on load, and `SMOKE_SCOPE=home npm run smoke` must print `SMOKE_OK` — that run's `RENDERER:` console lines are where a load-time error would surface.

## 9. Phase 2 order

The file needs several PRs. Each carves the next contiguous run of parts off the *front* of the remaining `app.js`, so after every PR `app.js` is still "the rest of the original, in order, plus the boot" and the app runs; the relocations to the boot block happen in the PR whose part they leave. Order follows Eric's: shared constants and pure utilities first, then state and services, then screens, then the orchestrator.

| PR | Parts | Lines | What it contains | Proof |
| --- | --- | --- | --- | --- |
| 1 | 01–08 | 1–1553 | Foundation (icons, `state`, theme, settings mirrors, utilities), audio + fill, jobs tray, chrome + routing, account, profile/usage, licence status + sign-in, claim-handle. Relocates the three `getSettings` reads and `onLicenseChanged` to the boot block. Adds the 8 tags. Updates `test/audiorate.test.js` (the `audioCtx` regex). | `SMOKE_SCOPE=home`; both concatenation checks; `npm test`. |
| 2 | 09–16 | 1554–3174 | Home: the rail, filters, Settings, updates + restart page, the import door, scene details, "Your takes", cast selection. Relocates `onUpdatesChanged` and the idle interval. | `SMOKE_SCOPE=home` (import + rail + details) and a hand-opened Settings; concatenation checks. |
| 3 | 17–20 | 3175–4054 | Table read, booth capture, cue card, game session. | `SMOKE_SCOPE=booth`; concatenation checks. |
| 4 | 21–32 | 4055–6609 | The whole collab surface (12 parts). Relocates `onCollabChanged`, `onCollabInvite`, `onCollabProgress` and the focus poll. | `SMOKE_SCOPE=booth` (the strip's hidden path), concatenation checks, a manual Collab modal open on a dub scene. |
| 5 | 33–35 | 6610–7193 | Booth waveform + playback, the take (recording, scoring), the flow. | `SMOKE_SCOPE=booth` (records a take, scores it); concatenation checks. |
| 6 | 36–41 | 7194–8507 | Screening: entry + render, stage cards, seek strip, schedule + auto-save + play, scene score, transport. Updates `test/audiorate.test.js` (the `rate` regex). | `SMOKE_SCOPE=history` (screening + auto-save + a saved entry); concatenation checks; `npm test`. |
| 7 | 42–43 + app.js | 8508–8992 | Export + credits, the reel; `app.js` is now the orchestrator alone. Updates `test/fonts.test.js`. | full `npm run smoke` (the reel leg evaluates the credit functions by name); concatenation checks; `npm test`. |

Seven PRs of 1,000–2,600 lines each, every one a pure move a reviewer can verify mechanically. If Eric wants fewer: 1+2 (home, 3,174 lines), 3+4+5 (table read through the booth, 4,019 lines), 6+7 (screening through the reel, 1,799 lines) — three PRs, same proofs. One PR for all 8,991 lines is possible (the checks are the same) but gives a reviewer nothing to hold on to and puts every screen's smoke leg on one green.

Each PR also: adds its tags to `index.html` in numeric order, writes each part's two header lines, and leaves everything under `ledger/` alone. No PR edits a line of code inside a statement; a bug found on the way is a separate PR.

## 10. Risks

1. **A push from main landing between two script tags.** This is the one real load-order hazard and the reason for the relocations in section 5. Chromium fetches each `<script src>` asynchronously and the renderer's event loop runs tasks — IPC deliveries included — while the parser waits for the next file's bytes. `main.js` sends `license:changed` from `setLicenseStatus` (349), which `refreshLicense` (521) calls after a `/validate` round trip that `initLicense` (1240) starts before `createWindow` (6676): its reply can land while the parts are loading. In the monolith that push either finds no listener yet (dropped; the boot's `licenseStatus()` query fetches the same state a moment later) or the whole handler — never half of one. With the subscription left at line 1421 in part 07, a push arriving before part 09 loads would run `applyLicenseStatus` → `refreshPacks` and throw `ReferenceError`. The transitive analysis (every function reachable from each subscription's callback) shows `onLicenseChanged`'s handler reaching 32 of the 43 parts, `onCollabChanged`/`onCollabProgress` reaching 10, `onUpdatesChanged` and the idle poll reaching 2, and the `getSettings` reply at 164 reaching one (`syncCamUi`). Registering all of them in `app.js`, after every part, restores the monolith's atomicity. **Do not leave any `window.vc.on*` call in a part.**
2. **Temporal dead zone across files.** Top-level `const`/`let` in a classic script are hoisted into the shared global lexical scope but stay in the TDZ until their own script runs; a `function` declaration from a later script is simply unresolvable until then. Section 3's census proves no top-level statement reads a later name at load, and the contiguous cut preserves that. The rule for Phase 2 and after: **never reorder parts, and never add top-level code to a part that calls into a later part** — a new `$('x').addEventListener(…, laterFn)` is fine (the reference is inside the callback), a new `const y = laterFn()` is not. The statement-set check catches an accidental edit; it does not catch a reorder of the tags, so the tag order in `index.html` is part of the contract.
3. **Callbacks registered early that reference later parts — DOM events.** Twenty-odd listeners in early parts reach later ones (e.g. the window click-away at 1993 reaches `closeSceneExportMenu` in part 15; the table read's scrub block at 3510 reaches `screenWave` in part 38; `btn-record` at 6835 reaches `startRecording` in part 34; the resize listener at 6739 reaches `fitStage` in 37). They fire only on user input or a resize, which cannot happen during the few milliseconds the tags take to execute — and under `--smoke` the window is never shown. Accepted; listed so nobody "fixes" them by reordering.
4. **The three test pins** (section 2g). Forgetting `test/audiorate.test.js` fails PR 1 and PR 6; forgetting `test/fonts.test.js` fails PR 7 on the `>= 3` total. Both are in section 7 and section 9.
5. **Headers and the concatenation check.** Each part's first two lines must be exactly `'use strict';` and one `//` comment, or `tail -n +3` in section 8 strips code. Keep the header shape fixed; a part that grows a second comment line breaks the check, which is the check working.
6. **Duplicate `'use strict'` semantics.** Each part is its own script, so each needs the directive; the concatenation strips 43 copies. Strict mode was already file-wide, so no behaviour changes.
7. **What the smoke run cannot see.** Settings (11), updates + restart (12), the whole collab surface (21–32), the Video/Audio export dialogs (42, which the walkthrough avoids by design) and everything behind a flag that is off under `--smoke`. For those the proof is the concatenation check (a pure move cannot change behaviour if the load order is right) plus the manual opens in section 8.
8. **Comment blocks straddling a cut.** A section-header comment (`/* ---------- library ---------- */`) belongs to the statement after it; the ranges in section 4 include it in the following part. A reviewer reading a part's first lines should see the same header the monolith had.
9. **`renderer/CLAUDE.md` says "in `app.js`"** in many places (section 7). Not a runtime risk, but a stale doc is how the next reader looks in the wrong file; a doc pass follows PR 7.
10. **Anything else in the repo naming the path.** Grepped: `main.js` names it once in a comment (2081); `test/release-workflow.test.js` uses it as fixture data; nothing else. Adding a file to `renderer/app/` needs no allowlist entry (section 7).
