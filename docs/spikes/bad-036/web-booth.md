# BAD-036 · Phase 1 extraction plan: `web/booth.js`

Spike by Marfona (BADS-036/03.02, architect) for [[BAD-036]]. A read-only survey of the BadTakes checkout at `20d11e1dace7a7aac012b30ffcd6c983b92642c2` (`git rev-parse HEAD`, `main`, clean); every line number cites that SHA. Sibling spikes: `web-collabweb.md` (this author) and `web-app.md` (Roseval); where this file depends on a name app.js defines, section 2b says so, for reconciliation.

**Size rule (Eric, via Superior, 2026-09-15):** 250 lines is the point at which a file gets looked at, not a cap. A cohesive unit — a function, a screen, a collab phase — stays whole, and the larger it gets the harder that look should be. Section 4 splits by responsibility; section 6 lists every proposed file over 250 with its honest size and the reason it stays whole, for Eric to judge.

## 1. The file today

| | |
|---|---|
| Path | `web/booth.js` |
| Lines | 1406 (`wc -l`); 66 top-level declarations (46 `function`, 13 `const`, 7 `let`) |
| Runtime | a classic script (`'use strict'` at line 9) in the browser tab at my.badtakes.io, in the Capacitor shell (`mobile/www`, the same tree through `scripts/build-mobile.js`), and under `npm run web` / `web:local` / `web:live`. Never in the render container: `deploy-render.yml:211-214` lists four `web/lib` files (schedule, packsource, codecs, audio). |
| Loaded by | `web/index.html:835`, `<script src="booth.js">` — the second of the eight view scripts (`app.js` 834, `booth.js` 835, `editor.js` 836, `account.js` 837, `ads.js` 838, `billing.js` 839, `collabweb.js` 840, `start.js` 841), after every `lib/` and `../src/` tag. `start.js:4` calls `window.bootApp()`, which `app.js:1909` set — the only `window.*` assignment among app, booth and collabweb. |
| What it is | The recording room (the cue card, its waveform, record/stop with the desktop's sidecar contract, scoring); the screening room (Voices, the scene score panel, the auto-saved dub, the WAV download, a history entry as a viewer); the camera (booth self-view, the screening cam card); the render-service client (`exportSource`, upload names through `RenderGate.checkNames`, `renderToken()`); the Video and Reel exports; the phone's Save sheet. |
| Read by tests | `test/web-guest-mode.test.js:447` (static: `fnBody` of `enterBooth`, `enterScreening`, `exportWav`; the vm at :214-226 stubs its entry points and never runs it), `test/fonts.test.js:144` (the `ReelRender.ready()` gate/draw count, both zero), `test/web-ids.test.js:42` (the `$('literal')` sweep over `web/*.js`, non-recursive). |
| Last touched | `a11255b` (BAD-002 guest mode, #330), `1ec89d0` (#324, the credit drawn in the container), `432fccf` (#322, the reel drawn in the container). |

Top-level statements: none. Every column-0 line is a declaration, a comment or `'use strict'`; nothing in the file reads another script's binding at load time (verified with the unit parser of section 8, which also found no column-0 code).

## 2. Public surface — the compatibility contract

### 2a. Bare names other scripts read

Measured with a word-boundary grep that excludes `.name`, `name:` and quoted text, every hit then read by hand. The brief's list had one false positive: `wave` — app.js:1440 and :1730 are `` $(`${stage}-wave`) `` and editor.js:121 is `$('ed-wave')`; nothing outside this file touches the `let wave`.

| name | defined | read from | how |
|---|---|---|---|
| `enterBooth` | 28 | app.js:1294 (`if (pack.kind === 'soundboard') return enterBooth();`), :1299 (the table read's `next` slot), :1698 (`$('btn-to-booth').onclick = enterBooth`); collabweb.js:592 (`await enterBooth()` in `recordCollabCharacter`) | bare, inside functions |
| `wireBooth` | 502 | app.js:1710 (`wireBooth();` in `boot`) | bare |
| `enterScreening` | 534 | collabweb.js:1519 ("Watch the dub"); app.js:1238 and ads.js:280 are comments | bare, in a handler |
| `onScreeningEnd` | 599 | app.js:1448 (`loadTransport`'s `onEnd`) | bare |
| `exportWav` | 753 | app.js:1699 (`$('btn-export-wav').onclick = exportWav`) | bare |
| `openEntry` | 779 | app.js:931, :1018, :1050, :1053 (history rows, the Takes tab); ads.js:281 is a comment | bare |
| `setCameraOn` | 829 | app.js:1681 (the header camera toggle) | bare |
| `updateCam` | 921 | app.js:1525 (`if (stage === 'sc' && typeof updateCam === 'function') updateCam(at);`) | `typeof`-guarded |
| `exportVideo` | 1165 | app.js:1682 | bare |
| `exportReel` | 1235 | app.js:1683 | bare |
| `renderToken` | 1149 | editor.js:333 (`token: renderToken()` in the creator's assemble call) | bare |
| `closeSaveSheet` | 1366 | app.js:1767 (the Back gesture's topmost-overlay dismissal) | bare |
| `myCreditName` | 962 | account.js:373 is a comment; no code outside this file | — |

The other 53 declarations are file-private in practice. The split keeps them bare anyway: a classic script has no way to hide a top-level name, and none is wanted.

`window.*` assignments: none. `window.bootApp` is app.js's.

### 2b. Names this file reads from other scripts (for reconciling with the app.js and collabweb.js plans)

From **app.js**, all bare, all inside functions, none at load: `$` (app.js:8), `state` (14), `toast` (36), `setHeader` (45), `showView` (83), `showTab` (122), `backToLibrary` (147), `mixScoreItems` (933), `takeDate` (949), `progressUi` (1138), `download` (1163), `playableLines` (1188), `sessionTakeOf` (1202), `takesForMix` (1215), `lineVoiced` (1221), `reportSessionEnd` (1267), `enterTableRead` (1276), `gradeOf` (1356), `renderTimeline` (1377), `loadTransport` (1429), `setPlaying` (1455), `attachStageVideo` (1462), `onTick` (1494), `paintSeek` (1558). Roseval's plan must keep all 24 top-level and bare under these names; `$`, `state` and `progressUi` are `const`s, which is fine because no booth code reads them at load.

From **collabweb.js**, every one `typeof`-guarded or inside a guarded branch — collabweb.js loads *after* this file and the page must run without it, as the vm test proves: `collabBoothSeat` (31), `collab` (31: `collab.boothCharacter`; 552: `collab.remote = …`), `renderBoothCollab` (50, 527), `collabHeartbeat` (348, 373), `scheduleCollabPush` (424), `flushCollabPush` (507), `collabActive` (551), `resolveCollabTakes` (552), `collabPerformerOf` (968). (`mixTakes` at 550 is a comment; the call is app.js:1216, inside `takesForMix`.)

`window.*` namespaces read: `Ads`, `Align`, `Api`, `CamTrack`, `Credits`, `Engine`, `Native`, `PackSource`, `Recorder`, `ReelLayout`, `RenderGate`, `Report`, `Schedule`, `Score`, `Store`, `Takes`, `Transport` — all `lib/` or `../src/` tags, all before every view script.

### 2c. DOM ids it owns — `$('literal')`, 54 distinct, 80 calls, one computed `$(id)` at 905

`booth-at` 80 · `booth-caption` 75, 81 · `booth-character` 74 · `booth-count` 357 · `booth-cue` 88, 89 · `booth-dock` 90, 91 · `booth-dots` 150 · `booth-keys` 92, 93 · `booth-plate` 76 · `booth-rec` 96 · `booth-score` 97, 125 · `booth-still` 73 · `booth-wave` 230 · `btn-camera` 844 · `btn-camera-state` 845 · `btn-export-wav` 756 · `btn-hear-line` 512 · `btn-hear-take` 107, 516 · `btn-next-line` 506 · `btn-next-line-label` 110 · `btn-prev-line` 108, 504 · `btn-record` 54, 100, 310, 320, 470 · `btn-restart` 1400 · `btn-save` 1394 · `btn-scrap` 520 · `btn-voices` 553, 791 · `cam-card` 900 · `cam-video-a` 925 · `cam-video-b` 925 · `cue-cam` 851 · `key-record-label` 106 · `mic-note` 53, 57, 336, 339 · `record-caption` 102 · `save-audio` 1399 · `save-close` 1396 · `save-done-sub` 1386 · `save-done-title` 1385 · `save-note` 1358 · `save-reel` 1362, 1398 · `save-rendering-title` 1378 · `save-sheet` 1350, 1364, 1366, 1367, 1382, 1395 · `save-title` 1355 · `save-video` 1361, 1397 · `sc-wave` 803 · `scene-donut` 638 · `scene-score` 637 · `scene-score-band` 642 · `scene-score-cast` 648 · `scene-score-percent` 641 · `scene-score-takes` 643 · `score-band` 130, 137 · `score-donut` 126 · `score-pct` 129, 136 · `voices-menu` 573.

`test/web-ids.test.js` keeps all 54 in its sweep as long as the new files sit at `web/` top level: its `fs.readdirSync(WEB)` (:42) is non-recursive and never looks in `web/lib/`.

### 2d. Endpoints and IO it owns

No `fetch` of its own. The render service through `window.Api.renderScene` (1178, `what: 'video'`; 1312, `'reel'`), whose PUTs go to URLs `/session` minted: `lib/api.js:64-71` — `upload()` is `storagePut` "unchanged and unwidened", and `storagePut`'s `x-upsert` check "is the rule for which target gets that header — R2's preflight refuses a header it does not know, and a render upload is never Supabase Storage." **This file sets no header and mints no URL; the split moves callers and never adds an upload path of its own.** The usage report through `window.Report.takeRecorded` (420) and `window.Report.share` (765 wav, 1196 mp4, 1329 reel), fire-and-forget, never awaited. IndexedDB through `window.Store` (`putTake` 413, `deleteTake` 523, `listRemoteTakes` 552, `listMixes` 720, `putMix` 725, `setSettings` 837, `sceneTakeBytes` 1162, `ensureSceneDigest` 1173, 1288). Web Audio through `window.Engine` and `window.Schedule`; the mic and camera through `window.Recorder`; the canvas at `booth-wave` (229-295, the only pixels this file draws, and they are not export pixels).

### 2e. Strings the tests scan

- `test/web-guest-mode.test.js:467-469`: `fnBody(booth, name)` for `enterBooth`, `enterScreening`, `exportWav` must not match `/signedIn\(|openSignIn\(|renderToken\(|state\.license/`. `fnBody` (:430-441) finds `(?:^|\n)(?:async )?function name\(` — the three must stay top-level `function` declarations (never a `const` arrow), and `booth` must be whatever text contains all three.
- `test/web-guest-mode.test.js:219-226`: the vm stubs `wireBooth`, `enterBooth`, `enterScreening`, `exportWav`, `exportVideo`, `exportReel`, `setCameraOn`, `closeSaveSheet` — exactly the names app.js calls into this file unguarded (2a, less `updateCam`, which is guarded, `onScreeningEnd`, which only `loadTransport`'s `onEnd` reaches, and `openEntry`, which the test never triggers). A new unguarded call from app.js into the booth family needs a stub there or the vm boot throws a ReferenceError.
- `test/fonts.test.js:144`: `'web/booth.js'` counts `await ReelRender.ready();` against `ReelRender.(createOverlayPainter|renderLandscapeCredit)(` — both zero, kept "so that re-adding a draw there without its gate fails rather than passes" (:139-143).

### 2f. What the phone layout and the session gate depend on here

- `body.in-workflow` is `setHeader`'s (app.js:45). This file sets a header in `enterBooth` (37-45, `camera: true` fills the right slot), `enterScreening` (537-543) and `openEntry` (783-788). The tab bar hides because those calls happen, not because of where they live.
- `body.native` is stamped by app.js:1638 from `Native.isNative()`. **Hold-to-record is `wireRecordButton` (469-500)**: `onclick = toggleRecord` in every browser, press/release with pointer capture on the native shell, both into the same `toggleRecord`/`stopRecord`; `renderBooth` (101-105) words `record-caption` from the same `Native.isNative()`. `body.ios` and the 375px rules are `styles.css`'s.
- The Save sheet (1349-1406) is the export dropdown's phone form; `openSaveSheet` reads `Api.hasRenderService()` / `Api.hasReelRender()` (1351-1352) and `Native.isNative()` (1353, "Save to Photos" / "Shared … from the share sheet").
- The session gate: `body.guest` is stamped by `applyLicenseUi` (account.js:539) and never read here. **`renderToken()` (1149-1156) throws** without `state.license.token` — the message is `'video exports run on our render service, which needs a free account — sign in from Settings.'`, not the *"Sign in to export."* that `web/CLAUDE.md:157-158` and `web/README.md:84-85` still quote (pre-existing doc staleness; section 7 lists it). `enterBooth`, `enterScreening` and `exportWav` consult nothing of the account, pinned by 2e; the WAV path is `Schedule.renderWav` in the browser (760) and never the service.
- The collab seat meets this file at two points, both guarded: `enterBooth:31` narrows the cast to `collab.boothCharacter`; `enterScreening:551-553` asks `collabActive()`, loads `collab.remote`, calls `resolveCollabTakes()` and hides the Voices toggles. That is where the load-bearing rule **`state.takes` stays your takes and the screening room asks `mixTakes()` for the combined dub** lives on this side: `state.takes` is written only at 414 (`set`, the take just recorded) and 524 (`delete`, Scrap); every mix reads `takesForMix()` (app.js:1215 → `mixTakes()`) — 575, 609, 733, 873, 1069 — never `state.takes` directly.

## 3. Responsibility map

| lines | group | what is there |
|---|---|---|
| 1-9 | header, `'use strict'` | the recording contract: the sidecar is the placement truth, corrected once at record time |
| 10-161 | screen — the recording room's cue card | `boothLines` 22, `enterBooth` 28, `currentLine` 60, `renderBooth` 67 (owns the card's three states), `renderScore` 124, `renderDots` 149 |
| 162-295 | sub-component — the cue card's waveform | `PEAKS_N` 168, `let wave` 169, `peaksOf` 171, `let carryLive` 190, `loadBoothWave` 192 (sequence-guarded decodes), `paintBoothWave` 229 (the canvas) |
| 296-323 | service — the live level meter | `LEVEL_MS` 304, `let levelTimer` 305, `runLevelMeter` 307 (a timer, not rAF: the hidden-tab rule) |
| 324-462 | service — one take | `let countdownTimer` 325, `toggleRecord` 327, `lineLength` 364, `stopRecord` 369 (the sidecar: `captureSkew`, `recordedLead`, `Store.putTake`, `Report.takeRecorded`, `scheduleCollabPush`), `scoreTake` 429, `playBuffer` 454 |
| 463-530 | wiring — the record button and the room | `wireRecordButton` 469 (tap vs hold), `wireBooth` 502 (prev/next/hear/scrap; calls `wireSaveSheet`) |
| 531-675 | screen — the screening room | `enterScreening` 534, `renderVoices` 572, `onScreeningEnd` 599, `sceneScoreItems` 607, `renderSceneScore` 630, `paintScenePanel` 636 |
| 676-823 | the dub as a record | `savedMixes` 680, `autoSaveMix` 682 (the desktop's sidecar shape, `sceneSignature` dedupe), `shareContext` 747, `exportWav` 753, `openEntry` 779 (a history row as a viewer) |
| 824-863 | service — the booth cam | `cameraOn` 827, `setCameraOn` 829, `syncCamUi` 842, `syncCamPreview` 850 |
| 864-952 | sub-component — the cam card | `camPlay` 867 (state), `dubbedCamTimeline` 871, `camUrlFor` 886, `setCamTimeline` 895, `resetCamVideos` 903, `camIndexAt` 912, `updateCam` 921 |
| 953-975 | pure — credits | `myCreditName` 962, `performerOf` 967, `creditForSchedule` 972 |
| 976-1073 | pure — the export model | `entryTrim` 1016 (with its 40-line rationale, 989-1015), `exportSource` 1026 |
| 1074-1163 | service — the render-service client | `containerExt` 1079, `nameAccepted` 1103 (`RenderGate.checkNames`), `mediaName` 1106, `takeUploads` 1129, `renderToken` 1149, `sceneTakeFor` 1161 |
| 1164-1337 | services — the two rendered exports | `exportVideo` 1165, `peaksForRange` 1206, `camClipUsable` 1222, `exportReel` 1235 (the overlay model; 104 lines, the longest function) |
| 1338-1406 | sub-component + wiring — the Save sheet | `let saveJob` 1344, `openSaveSheet` 1349, `closeSaveSheet` 1366, `setSaveState` 1367, `runSave` 1373, `wireSaveSheet` 1393 |

State: `wave`, `carryLive`, `levelTimer`, `countdownTimer` (the take), `savedMixes` (the screening), `camPlay` (the cam card), `saveJob` (the sheet). Everything else is a function or a constant.

## 4. Proposed tree

**Where: `web/` top level, bare-scope classic scripts — the shape the file has today.** Not `web/lib/`: the lib convention is `window.Name = (function () { … })();` — one namespace, private scope — and everything in 2a and 2b is a bare identifier resolved in the page's shared lexical scope, including four `typeof x === 'function'` guards in app.js and every collab hook this file calls. Wrapping any piece would mean rewriting call sites to `Name.fn` (a refactor, not a move), breaking `fnBody`'s `function name(` scan (2e), and dropping the code out of `web-ids`' sweep (it never reads `lib/`). Not ES modules: `type="module"` scopes its top level and defers, so every bare name would need a `window.` export and every reader a rewrite. Not a new `web/<dir>/`: `web-ids.test.js:42` is non-recursive, so a subdirectory silently drops 54 ids from the sweep, and nothing is gained (build-web's `copyTree` would ship it; that is all).

**Naming:** every file of the family starts with `booth`, so the tests and the check can address the family by prefix (`web/booth*.js`) and the tags read as one block.

**Contiguity:** every new file is one unbroken line range of the original, in tag order — what makes the concatenation check in section 8 a plain `diff` with no output.

| # | file | lines | size | what moves there (name, current line) |
|---|---|---|---|---|
| 1 | `web/booth.js` (stays) | 1-530 | 530 | the header 1-9; `boothLines` 22, `enterBooth` 28, `currentLine` 60, `renderBooth` 67, `renderScore` 124, `renderDots` 149; `PEAKS_N` 168, `wave` 169, `peaksOf` 171, `carryLive` 190, `loadBoothWave` 192, `paintBoothWave` 229; `LEVEL_MS` 304, `levelTimer` 305, `runLevelMeter` 307; `countdownTimer` 325, `toggleRecord` 327, `lineLength` 364, `stopRecord` 369, `scoreTake` 429, `playBuffer` 454; `wireRecordButton` 469, `wireBooth` 502 |
| 2 | `web/booth-screening.js` | 531-823 | 293 | `enterScreening` 534, `renderVoices` 572, `onScreeningEnd` 599, `sceneScoreItems` 607, `renderSceneScore` 630, `paintScenePanel` 636, `savedMixes` 680, `autoSaveMix` 682, `shareContext` 747, `exportWav` 753, `openEntry` 779 |
| 3 | `web/booth-cam.js` | 824-952 | 129 | `cameraOn` 827, `setCameraOn` 829, `syncCamUi` 842, `syncCamPreview` 850, `camPlay` 867, `dubbedCamTimeline` 871, `camUrlFor` 886, `setCamTimeline` 895, `resetCamVideos` 903, `camIndexAt` 912, `updateCam` 921 |
| 4 | `web/booth-render.js` | 953-1163 | 211 | `myCreditName` 962, `performerOf` 967, `creditForSchedule` 972, `entryTrim` 1016, `exportSource` 1026, `containerExt` 1079, `nameAccepted` 1103, `mediaName` 1106, `takeUploads` 1129, `renderToken` 1149, `sceneTakeFor` 1161 |
| 5 | `web/booth-export.js` | 1164-1406 | 243 | `exportVideo` 1165, `peaksForRange` 1206, `camClipUsable` 1222, `exportReel` 1235, `saveJob` 1344, `openSaveSheet` 1349, `closeSaveSheet` 1366, `setSaveState` 1367, `runSave` 1373, `wireSaveSheet` 1393 |

Each new file opens with a file-head comment (what it is, what it deliberately does not restate — the lib convention's comment discipline, kept) and **`'use strict';`** — required, not cosmetic: strict mode is per classic script, and a file that omits it runs sloppy (an undeclared assignment becomes a global instead of a ReferenceError; `this` in a plain call is `window`). Then the original lines byte for byte, beginning with the blank line and the banner each range starts with (531 blank, 532 `/* ---------------- the screening room ---------------- */`; likewise 824/825, 953/954 (a comment), 1164 is `async function exportVideo() {` directly after 1163's `}`, so `booth-export.js` starts on its first declaration).

Load order — `web/index.html`, line 835 becomes five tags:

```html
<script src="app.js"></script>
<script src="booth.js"></script>
<script src="booth-screening.js"></script>
<script src="booth-cam.js"></script>
<script src="booth-render.js"></script>
<script src="booth-export.js"></script>
<script src="editor.js"></script>
```

Nothing on the page reads a booth name at load (section 1), so the order among the five is free at runtime; original order is chosen so that tag order equals line order and the section 8 check stays a plain diff.

Cross-file bindings after the split — all read or assigned inside functions, none at load:
- `lineLength` (booth.js:364) ← `sceneScoreItems` 619 (booth-screening.js).
- `shareContext` (booth-screening.js:747) ← `exportVideo` 1196, `exportReel` 1329 (booth-export.js).
- `cameraOn` (booth-cam.js:827) ← `toggleRecord` 334 (booth.js).
- `dubbedCamTimeline`, `setCamTimeline` (booth-cam.js) ← `enterScreening` 557, `renderVoices` 592, `autoSaveMix` 733, `openEntry` 813 (booth-screening.js); `exportSource` 1069 (booth-render.js).
- `myCreditName` (booth-render.js:962) ← `exportReel` 1262, `openSaveSheet` 1354 (booth-export.js). `performerOf` (967) ← `autoSaveMix` 710 (booth-screening.js).
- `renderToken`, `exportSource`, `mediaName`, `containerExt`, `takeUploads`, `sceneTakeFor` (booth-render.js) ← `exportVideo`, `exportReel` (booth-export.js); `renderToken` ← editor.js:333.
- `exportWav` (booth-screening.js) ← `wireSaveSheet` 1399 (booth-export.js), app.js:1699. `enterScreening` ← `enterBooth` 43, `wireBooth` 508 (booth.js), collabweb.js:1519. `enterBooth` (booth.js) ← `enterScreening` 538. `wireSaveSheet` (booth-export.js) ← `wireBooth` 529.
- **No `let` is assigned from two files**: `wave`, `carryLive`, `levelTimer`, `countdownTimer` keep every writer inside booth.js; `savedMixes` is booth-screening.js's; `camPlay` booth-cam.js's; `saveJob` booth-export.js's.

**Coverage table** — every line of the original assigned exactly once:

| range | file | lines |
|---|---|---|
| 1-530 | `booth.js` | 530 |
| 531-823 | `booth-screening.js` | 293 |
| 824-952 | `booth-cam.js` | 129 |
| 953-1163 | `booth-render.js` | 211 |
| 1164-1406 | `booth-export.js` | 243 |
| **total** | | **1406** |

## 5. The entry point afterwards

`web/booth.js` keeps its path, its tag (`index.html:835`, second view script, directly after `app.js`), its header and `'use strict'`, and lines 1-530 unchanged: it *is* the recording room. Of section 2's contract it still defines `enterBooth` and `wireBooth` (what `boot` and the table read call) and every private name of the room. `enterScreening`, `onScreeningEnd`, `exportWav`, `openEntry` (→ booth-screening.js), `setCameraOn`, `updateCam` (→ booth-cam.js), `renderToken` (→ booth-render.js), `exportVideo`, `exportReel`, `closeSaveSheet` (→ booth-export.js) move with their ranges as the same bare top-level `function` declarations, so app.js:1448, :1525, :1681-1683, :1698-1699, :1710, :1767, editor.js:333 and collabweb.js:592, :1519 change nothing. `window.bootApp` is untouched (app.js:1909). The four new tags sit between `booth.js` and `editor.js`: no script reads a booth name at load, so "before every reader" is satisfied trivially, and staying before `editor.js` keeps the family one block.

If Eric would rather the entry read as an orchestrator than as the room, the one reorder that does it is moving `wireBooth` (502-530) to the top of the file — a 29-line block move that the concatenation diff would show as a hunk pair. Not proposed: the room is the honest name for what stays.

## 6. Files over 250 lines — honest size, why each stays whole, the cut if Eric wants one

| file | size | why it is one unit | the cut, and its cost |
|---|---|---|---|
| `booth.js` — the recording room | 530 | One screen whose parts drive each other's state: `renderBooth` (67) reloads the waveform (116); `toggleRecord` (327) starts the level meter (352) and re-renders (353); `runLevelMeter` (307) pushes into `wave.live` (318) and repaints (321); `stopRecord` (369) reads `wave.live` / `liveElapsed` (375-376) and writes `carryLive` (376), which `loadBoothWave` (192) consumes and clears (197-201); `wireRecordButton` (469) is the gesture around the same pair. The waveform calls itself "a little state machine" (163-167) but every input it has is the take's. | The only real seam is at 161/162 and 323/324 — three files: the cue card (1-161, 161 lines), the waveform and level meter (162-323, 162), the take and the wiring (324-530, 207). Cost: `carryLive` becomes a `let` assigned from two files (`loadBoothWave` and `stopRecord`) and `wave` a `let` mutated from two; still one binding, still correct, but the recording contract is then read across three files. Contiguous, so the check stays a plain diff. |
| `booth-screening.js` — the screening room | 293 | One screen including its viewer mode: `openEntry` (779) is the same room fed a saved entry, reusing `paintScenePanel` (818) and `setCamTimeline` (813); `autoSaveMix` (682) is what `enterScreening` (562) and `onScreeningEnd` (601) do on arrival and at the end; `exportWav` (753) is the one export that renders here, in the browser, and reads `state.entry` for the viewer case (760). | 675/676: the room (531-675, 145) and "the dub as a record" (676-823, 148: auto-save, the WAV, the viewer). A real seam, contiguous and cheap; `paintScenePanel` would be called from the second file. Not proposed only because 293 is one screen, 43 over the mark. |

Under 250 and at their own size: `booth-cam.js` 129 (the camera is that big — two rooms, one responsibility), `booth-render.js` 211, `booth-export.js` 243 (Video, the reel and the sheet that runs them on a phone: `runSave` (1373) is the phone's only caller of the two exports and reads their two gates at 1351-1352; the sheet alone would be a 69-line file for its own sake).

## 7. Build and packaging touch points

| | change |
|---|---|
| `web/index.html` | four tags after line 835 (section 4). Comments at :799 ("booth.js has to mint upload names") and :809 ("booth.js sends the credit") now describe `booth-render.js`. |
| `scripts/build-web.js` | **No change.** `copyTree(WEB, out, { skip })` (77-86) copies every non-dot file in `web/`; `sharedModules` (99-103) matches only `../src/` and `../renderer/`; lines 249-252 refuse a `src="…"` that is not in the tree, which the new tags satisfy by existing; `stampAssets` (133-143) appends `?v=<sha256[0:10]>` to every `<script src>`, so each new file carries its own digest — the whole answer to the Cloudflare-proxied cache on my.badtakes.io (a returning visitor's page can only resolve to the bytes it was built with). |
| `test/build-web.test.js` | **No change**: it pins the shared `src/` modules (:48), the generated pair (:52-53), stamping (`stamped > 30`, :41) and the CSP; there is no view-script list. |
| `test/web-ids.test.js` | **No change** while the files are at `web/` top level (:42); the 54 ids stay swept, and `calls.length > 100` (:62) stays true. |
| `test/web-guest-mode.test.js` | :447 `const booth = read('web', 'booth.js');` must become the family, since `enterScreening` and `exportWav` leave the file. Recommended: read the family off the page, the way build-web's `sharedModules` reads the shared modules — `const viewScripts = [...INDEX_HTML.matchAll(/<script src="([a-z-]+\.js)"><\/script>/g)].map((m) => m[1]);` then `const booth = viewScripts.filter((f) => f.startsWith('booth')).map((f) => read('web', f)).join('\n');` — so a sixth booth file cannot fall out of the scan. The vm list (:230-231) does not change (booth is stubbed at :214-226), and the stubs do not change (app.js makes no new unguarded call). |
| `test/fonts.test.js` | :144 `'web/booth.js'` → the family, e.g. `...fs.readdirSync(path.join(REPO, 'web')).filter((f) => /^booth.*\.js$/.test(f)).map((f) => `web/${f}`)` spliced into the list, so a painter re-added in any of the five without its gate still fails; the `totalGates >= 3` floor (:151-154) already excludes the web. |
| `test/ios-no-plans.test.js:247` | not ours (`web/ads.js`, `web/billing.js`); no change. |
| CSP (`index.html`, `script-src 'self'`) | no change — same origin, no inline script. |
| `.github/workflows/deploy-web.yml` | no change (`paths: 'web/**'` at :27-28 and :39-40). `deploy-render.yml` lists four `web/lib` files (:211-214), none of ours. |
| `firebase.json` | no change; `**/*.@(css|js)` at `max-age=600, stale-while-revalidate=86400` (:35-39) is covered by the stamp. |
| `scripts/build-mobile.js` | no change (calls the same `build()`, :48, :116; injects the Capacitor tag before `lib/native.js`). `mobile/ios/App/App/public/` is untracked (`git ls-files` → 0 files) and regenerated by `mobile:build`. |
| Doc lines that go stale (a list, not touch points) | `web/CLAUDE.md:157` ("`renderToken()` in `web/booth.js`" → booth-render.js; and its quoted *"Sign in to export."* already disagrees with line 1154), :180 ("`booth.js` can ask `RenderGate.checkNames`" → booth-render.js), :276 ("`web/booth.js` sends the credit" → booth-render.js / booth-export.js), :287 (the fonts test "over … `web/booth.js`" → the family), :334 (`wireRecordButton` in `booth.js` — still true). `web/README.md:84` (the same stale quote), :289 (the file listing: add the four), :478 ("`booth.js` …"). Comments: `web/start.js:1`, `web/app.js:1238`, `:1907`, `web/account.js:373`, `web/index.html:799`, `:809`, `src/rendergate.js:5`, `container/reel.js:11` — each says "booth.js" for what becomes booth-render.js or booth-screening.js. `scripts/build-web.js:41` is history and stays; the plans and specs under `docs/superpowers/` are dated records and stay. |

## 8. Verification recipe

The tier (`.claude/skills/implementing-changes/SKILL.md`): `web/` has no smoke leg, so the proof is the static tests, the build and a browser.

1. `npm test` — `test/web-guest-mode.test.js`, `test/web-ids.test.js`, `test/fonts.test.js`, `test/build-web.test.js` green; the four alone: `node --test test/build-web.test.js test/web-guest-mode.test.js test/web-ids.test.js test/fonts.test.js`. (Pack tests skipping is unrelated here; say so anyway.)
2. `npm run web:build` — the four refusals and the stamping run; `dist/web/index.html` carries five `booth…js?v=` tags.
3. `npm run web` against `web/scenes/` (`npm run web:fixtures` regenerates them), in a browser at desktop width **and** at a 375px viewport: table read → Record › → record a take (the waveform grows while recording; the card scores) → Next past the last line → screening (Voices, the score panel, "Dub saved to Your takes.") → Download the dub (.wav) → open the saved entry from Your takes (`openEntry`). At 375px: `btn-save` opens the sheet with Audio, and Video / Reel only against a render service (`npm run web:live` is the real one; both need a signed-in account through `renderToken()`). Camera toggle in the header on and off, the self-view appears. Console clean of `ReferenceError` — the one symptom a missing or misordered tag would have.
4. Guest mode: sign out (`body.guest`) and repeat booth → screening → WAV; nothing asks for an account.
5. **The concatenation check** — on the exact tree, before each PR's commit. `BASE` is the commit the PR branches from; the family is listed in tag order; the first file is taken whole, every later file from the line after its `'use strict';`:

```bash
# in the BadTakes checkout
BASE=$(git merge-base origin/main HEAD)
cat_family() {
  local first=1 f
  for f in "$@"; do
    if [ "$first" = 1 ]; then cat "web/$f"; first=0
    else awk -v m="'use strict';" 'p { print } $0 == m { p = 1 }' "web/$f"; fi
  done
}
diff <(git show "$BASE:web/booth.js") \
     <(cat_family booth.js booth-screening.js booth-cam.js booth-render.js booth-export.js) \
  && echo CONCAT_OK
```

Expected output: exactly `CONCAT_OK`. Any hunk is a byte that changed during a move. A new file whose head omits `'use strict';` fails loudly (awk never sets `p`, the file contributes nothing, the diff is large) — which is the right failure for a file that would otherwise run sloppy.

6. **The unit check**, for an intermediate commit where the family is only partly split (section 9's commit order extracts from the middle, so tag-order concatenation is not the original until the last commit): every top-level unit — a declaration with the comment block above it — of the base's family appears exactly once, byte-identical, across the current family, whatever the order. Same stripping rule; a file absent at `BASE` is skipped there.

```bash
BASE=$(git merge-base origin/main HEAD) \
FILES="booth.js booth-screening.js booth-cam.js booth-render.js booth-export.js" \
node - <<'EOS'
const fs = require('fs'), { execSync } = require('child_process');
const MARK = "'use strict';\n";
const strip = (t, f) => { const i = t.indexOf(MARK); if (i < 0) throw new Error(`${f} has no 'use strict' line`); return t.slice(i + MARK.length); };
const files = process.env.FILES.split(/\s+/).filter(Boolean);
const atBase = (f) => { try { return execSync(`git show ${process.env.BASE}:web/${f}`, { stdio: ['ignore', 'pipe', 'ignore'] }).toString(); } catch { return null; } };
const join = (texts) => texts.map(([f, t]) => [f, t]).filter(([, t]) => t != null).map(([f, t], i) => (i ? strip(t, f) : t)).join('');
const want = join(files.map((f) => [f, atBase(f)]));
const got = join(files.map((f) => [f, fs.existsSync(`web/${f}`) ? fs.readFileSync(`web/${f}`, 'utf8') : null]));
const bare = (l) => l.replace(/\/\/.*$/, '').replace(/'(?:[^'\\]|\\.)*'/g, "''").replace(/"(?:[^"\\]|\\.)*"/g, '""').replace(/`(?:[^`\\]|\\.)*`/g, '``');
const units = (text) => {
  const L = text.split('\n'), out = new Map(); let prev = -1;
  L.forEach((l, at) => {
    const m = /^(?:async )?function (\w+)\(|^(?:const|let|var) (\w+)\b/.exec(l);
    if (!m || at <= prev) return;
    let depth = 0, seen = false, end = at;
    for (let i = at; i < L.length; i++) {
      for (const c of bare(L[i])) { if (c === '{' || c === '(') { depth++; seen = true; } else if (c === '}' || c === ')') depth--; }
      if ((seen && depth === 0) || (!seen && /;\s*$/.test(bare(L[i])))) { end = i; break; }
    }
    const name = m[1] || m[2];
    if (out.has(name)) throw new Error(`${name} declared twice`);
    out.set(name, L.slice(prev + 1, end + 1).join('\n')); prev = end;
  });
  return out;
};
const a = units(want), b = units(got); let bad = 0;
for (const [k, v] of a) { if (!b.has(k)) { console.log(`MISSING ${k}`); bad++; } else if (b.get(k) !== v) { console.log(`CHANGED ${k}`); bad++; } }
for (const k of b.keys()) if (!a.has(k)) { console.log(`NEW ${k}`); bad++; }
console.log(want === got ? 'CONCAT_OK' : 'CONCAT_DIFF (order differs, or a byte changed — see the units below)');
console.log(bad ? `UNITS_DIFF ${bad}` : `UNITS_OK ${a.size} units`);
EOS
```

Expected on the final commit: `CONCAT_OK` and `UNITS_OK 66 units`; on an intermediate one, `CONCAT_DIFF` with `UNITS_OK 66 units`. `CHANGED x` names the unit whose bytes moved wrong; `x declared twice` is the duplicate-name SyntaxError caught before a browser sees it. Phase 2 may keep this as a scratch one-liner or give it a home under `scripts/` — a reviewer's tool either way.

## 9. Phase 2 order

Eric's order mapped onto this file: (1) shared constants and pure utilities — there are none worth a file: `lineLength` (5 lines), `myCreditName` (1), `peaksOf` / `peaksForRange` (13, 15) each belong to one section, and a `booth-util.js` would be a boundary for its own sake; (2) state and services — `booth-render.js` (the render-service client, no DOM) and `booth-cam.js` (the camera, its `camPlay` state and the two `<video>`s); (3) sub-components and screens — `booth-screening.js`, `booth-export.js`; (4) the entry left as the recording room.

**Recommendation: one PR for the family**, four commits in that order, every commit green on `npm test`, `npm run web:build` and the unit check, the concatenation check exact on the last commit. Reasons: no half-split `booth.js` is a state anyone wants on `main`; the review cost of a pure move is the check, not the line count; the two test edits (`web-guest-mode.test.js:447`, `fonts.test.js:144`) and the doc-line pass (section 7's list, `web/README.md:289`'s listing, `web/CLAUDE.md`) land once, in the PR that makes them true. The PR: the four new files, the four tags, the two tests, the docs.

If Eric wants separate PRs, cut them **tail-first** so each PR's concatenation check is exact against its own base with no intermediate state: PR 1 `booth-export.js` (1164-1406), PR 2 `booth-render.js` (953-1163), PR 3 `booth-cam.js` (824-952), PR 4 `booth-screening.js` (531-823); the fonts test edit rides PR 1, the guest-mode edit PR 4.

Each PR: branch `chore/bad-036-web-booth` (or `-1`…`-4`), `npm test`, `npm run web:build`, the checks of section 8 on the exact tree, then the browser pass of step 3, then `gh pr create` per `committing-and-pushing`.

## 10. Risks

- **Load order / TDZ.** None today: no top-level statement in booth.js reads another script's binding, and app.js's only column-0 statement is `window.bootApp = boot;` (:1909). After the split the same holds — every cross-file read in section 4 is inside a function that runs from `boot()` or a handler, after `start.js`. Keep it so: Phase 2 must not add a column-0 statement to any booth file that reads `state`, `$`, `progressUi` or a collab name.
- **`typeof` on a `const` in its TDZ throws** (`ReferenceError`), unlike on an undeclared name — the guards in app.js (:1525 `updateCam`, and the collab guards) are safe only because they run at runtime and `updateCam` is a hoisted `function`. So: **a moved declaration keeps its kind.** Never tidy a `function` into a `const` arrow (breaks `fnBody`, hoisting and the guards) or a `const` into a `function`.
- **Duplicate top-level names.** Two classic scripts declaring the same `const`/`let` is a SyntaxError that kills the page on its first byte (`index.html:768-775`, the `const path` note). A move creates none (all 66 names are unique across `web/*.js` today — checked with `uniq -d`), but a helper added during Phase 2 must be grepped across `web/*.js` first; the unit check throws on a duplicate within the family.
- **`'use strict'` per file** (section 4) — a file without it silently changes semantics; the concatenation check catches an omission.
- **Timers, not rAF.** `runLevelMeter` (304-323) and the countdown (355-361) are `setInterval` by rule (web/CLAUDE.md:125-131); a reviewer "modernising" during the move would reintroduce the hidden-tab bug.
- **`renderBooth` must not reload the waveform mid-take** (113-116; web/CLAUDE.md:131-133). Both sides of that rule stay in booth.js, but someone reading `loadBoothWave` next to the screening file might think it free to call.
- **The tests' string scans** (2e): `fnBody` needs `function enterScreening(` and `function exportWav(` at column 0 in whatever the test reads as `booth`; the vm stubs must keep matching app.js's unguarded calls; the fonts sweep must cover every file of the family, which the `booth` prefix makes a one-line rule.
- **What `npm run web` in a browser cannot show:** hold-to-record (`wireRecordButton`'s pointer path runs only under `Native.isNative()`; the simulator per `mobile/CLAUDE.md`), "Save to Photos" and the share sheet, a render against the live service (`web:live`, signed in), the collab seat in the booth (`enterBooth:31`, `enterScreening:551-553` — the local stack and two origins, `web:local`), and `Ads.screeningReached()` (545, dormant since #330).
- **Reconciliation with Roseval's app.js split:** the 24 names of 2b. If app.js moves `state`, `$` or `progressUi` into an `app-*.js`, they stay top-level `const`s that exist before `boot()` runs; if `takesForMix` / `sessionTakeOf` / `playableLines` move, they stay `function`s. Both view-script families have no load-time coupling, so their relative tag order is free; keep `app*.js` first anyway for readers.
- **Docs already stale:** the `renderToken()` message quoted by `web/CLAUDE.md:157-158` and `web/README.md:84-85` (2f) — fix in the same doc pass.
