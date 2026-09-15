# BAD-036 spike: splitting `renderer/creator.js`

*2026-09-15 · BAD-036 · Superior (03, architect) · Phase 1, planning only. No code was changed.*

Line numbers are for `renderer/creator.js` at commit `20d11e1` (3044 lines). Every range in §4 was
cut out of the real file into a scratch tree and checked three ways before it went into this note:
the ranges partition lines 1–3044 exactly once, every fragment parses on its own (`node --check`),
and the fragments loaded in the proposed tag order under a stub DOM without a load-time throw,
while three deliberately wrong orders threw exactly the errors §10 predicts. The commands are in §8.

Eric's rule for this ticket (relayed by Spud mid-spike): **250 lines is the point at which a file
gets looked at, not a cap.** §4 splits by responsibility and lets cohesive units stay whole; §6
lists every proposed file over 250 lines with its honest size and why it stays whole.

## 1. The file today

| | |
| --- | --- |
| Path | `renderer/creator.js` |
| Size | 3044 lines, one classic script, `'use strict'` at line 15 |
| Runtime | Electron renderer (`contextIsolation: true`, `nodeIntegration: false`, `main.js:5953-5955`), loaded as `file://` by `win.loadFile('renderer/index.html')` (`main.js:5962`). No build step, no bundler, no framework. |
| Loaded by | `renderer/index.html:1054`, the 18th of 19 script tags. Order: `../src/jobs.js`, `restart.js`, `handles.js`, `flags.js`, `catalog.js`, `takes.js`, `collab.js`, `align.js`, `score.js`, `camtrack.js`, `canvaslayout.js`, `reellayout.js`, `credits.js`, `reel.js`, **`app.js`**, `../src/sanitize.js`, `../src/announce.js`, **`creator.js`**, `announce.js` (`index.html:1036-1055`). |
| Ships in | the packaged app only — `package.json` `build.files` allows `renderer/**/*`; `web/` does not load it (`web/index.html` names nothing under `../renderer/`). |
| What it is | The scene creator wizard (Video → Scene → Lines → Details → Finish), the edit-mode variant of it (Lines → Details), the Make-a-scene landing page's tool probe and yt-dlp acquisition, the backing-track list, and the community scene catalog on the same page. Every media operation happens in `main.js` through the `vc.creator*` / `vc.catalog*` bridge; this file owns marks, metadata and painting (its own header, lines 1–14). |

It is one script by history, not by design: the strip painter, the lane editor, the tracks list and
the catalog were each added as a banner section (`/* ---------- … ---------- */`) and
share one global lexical scope with `app.js` (151 top-level declarations, 19 banners).

## 2. Public surface (the compatibility contract)

Everything outside the file that reaches into it. Each row cites where the reach happens.

### 2a. Globals other scripts read

| Name | Kind | Defined at | Read by |
| --- | --- | --- | --- |
| `window.creatorView` | object with `enter`, `fetchYtdlp`, `enterEdit`, `leave`, `confirmLeave`, `sessionActive`, `repaint`, `handleDrop`, `startFromVideo` | 1847–2015 | `app.js:100` (`repaint`, inside `applyTheme`, guarded because it runs once at boot before this file loads), `:688` (`leave`), `:733` (`confirmLeave`, via `okToLeaveCreator`), `:2341` (`sessionActive`, the update-restart gate), `:2486` (`handleDrop`), `:2493` (`startFromVideo`), `:2543` (`enter`, the "Make a scene" button), `:2778` (`enterEdit`, the "Edit scene" button); `announce.js:110-111` (`hasStaged` — see 2f) |
| `window.creatorToolsChanged` | function | 1780 | `app.js:2117` (the Settings link-import toggle) |
| `window.packsListStale` | `false` / a scene id | initialised 2052, set 1658, 1661, 3014 | `app.js:711-713` (`goHome` rescans and selects that scene) |
| `cstate` | top-level `const` | 20–49 | nothing in the app; **reachable bare over CDP** (`page.evaluate('cstate.step')`, per the creator-verification memory note). It is in the global *declarative* record, so `window.cstate` is `undefined` — today and after the split. |

No other script names a bare identifier of this file: `grep -w` over `app.js`, `announce.js` and
`reel.js` for `cstate`, `cshow`, `creatorHeader`, `paintStrip`, `creatorReset`,
`confirmLeaveCreator`, `creatorExit`, `committedSessions`, `catalogState`, `renderCatalogSection`,
`sceneNext`, `createPack` finds only a comment (`announce.js:15`).

### 2b. What it reads from `app.js` (the other half of the contract)

Bare top-level names of `app.js`, counted on code lines only (comments and string literals
stripped): `$` (every DOM lookup), `rgba` (18 uses, painting), `svgIcon` (7), `fmtTime` (5),
`toast` (4), `showView` (2: 1654, 2011), `decodeUrl` (2: 281, 1904), `state` (1655, `state.packs`),
`refreshPacks` (1648), `selectPack` (1656), `endGameSession` (1635), `goHome` (181), `VIDEO_RE`
(1998). Through `window`: `setWorkflowHeader` (130, 141), `confirmDialog` (174), `flagOn` (2980),
`renderCreatorQuota` (1854), `creatorAllowed` (2033) — all assigned on `window` by `app.js`
(`:622, :651, :1199, :906, :2593`). Shared UMD modules loaded earlier: `Jobs` (22 uses;
`src/jobs.js`), `Catalog` (3 uses; `src/catalog.js`). The bridge: `window.vc.creatorTools`,
`creatorFetchYtdlp`, `creatorCancelYtdlpFetch`, `creatorPickSource`, `creatorStageDrop`,
`creatorFetchLink`, `creatorEditStage`, `creatorCreate`, `creatorDiscard`, `creatorTrackGenerate`,
`creatorTrackCancel`, `onCreatorProgress`, `catalogIndex`, `catalogThumb`, `catalogInstall`,
`catalogInstallCancel`, `onCatalogProgress`, `track` (`preload.js:114-125, 172-176`). The file's
own header (lines 11–14) lists `views`, `preview` and others it no longer uses; the list above is
the measured one.

### 2c. DOM ids it owns (`renderer/index.html`)

68 literal `$('…')` ids plus 5 `getElementById` literals, all inside `<main id="view-creator">`
(`index.html:300-491`) or the two modals (`lines-help-modal` 852–882, `claude-modal` 885–912),
except `view-library` (1644) and the workflow header's `wh-next` (1603, 1622, 1668), which belong to
`app.js` and are only read/disabled here:

`btn-add-char btn-claude-close btn-claude-tip btn-creator-fetch btn-creator-fetch-ytdlp
btn-creator-pick btn-lines-help-close btn-strip-zoom claude-modal creator-author creator-cast
creator-category creator-demucs-note creator-desc creator-file creator-lanes creator-lanewrap
creator-link-card creator-loading creator-loading-label creator-new-char creator-no-ffmpeg
creator-panbar creator-panthumb creator-scrubhint creator-scrubtime creator-scrubtip
creator-src-artist creator-src-channel creator-src-episode creator-src-id creator-src-platform
creator-src-scene creator-src-season creator-src-title creator-stage creator-staged-len
creator-staged-note creator-status creator-step-source creator-strip creator-striptip
creator-stripwrap creator-subtitle creator-tags creator-takes creator-thumb creator-thumb-hint
creator-time creator-title creator-track-generate creator-track-hint creator-track-upload
creator-tracks creator-url creator-video creator-videobox creator-year creator-ytdlp-note
creator-ytdlp-status creator-zoomhint lines-help-modal scene-in scene-len scene-out view-creator`
+ `catalog-list catalog-count catalog-search catalog-mature catalog-sort` (2958–3039) + two computed
families: `creator-step-${s}` for `source|scene|takes|details` (70) and `btn-hidden-lines-${side}`
(1012, 1024, created by `renderTakes`). Class hooks it toggles: `.step-takes` on `view-creator`
(74), `.busy` on `creator-step-source` (309), `.playing` on `creator-videobox` (2232, 2236),
`.mod-key` fill (57). The split renames none of these.

### 2d. Names driven by string: the smoke walkthrough and CDP

- **`main.js --smoke`** (`main.js:6202-6217`, the `home` leg) clicks `btn-create-pack` (an `app.js`
  handler that calls `window.creatorView.enter()`), then asserts by DOM only: the visible view id,
  `#creator-step-source .creator-card` count, `btn-add-zips`/`btn-add-zip-folder` present,
  `wh-title` text, `wh-step`/`wh-next` hidden, then clicks `wh-back` and **throws if the visible
  view is not `view-library`** — the one fatal `SMOKE_MAKE` assertion (`docs/superpowers/research/
  2026-09-12-desktop-smoke-surface.md:304-308`). No JS name of this file is evaluated by string.
- **CDP** (the creator has no smoke leg; `CLAUDE.md` "Ad-hoc live driving" and the
  creator-verification memory note): `npx electron . --remote-debugging-port=93xx --user-data-dir=…`
  with `BT_LICENSE_BYPASS=1`; `showView('creator')` / `$('btn-create-pack').click()`;
  `window.creatorView` (poll for it — it is defined after `app.js`); bare `cstate` in
  `page.evaluate`; `DOM.setFileInputFiles` on `#creator-file` followed by a `change` event;
  `puppeteer-core`'s `page.mouse` for the strip drags (synthetic `PointerEvent`s break
  `setPointerCapture`); `canvas.toDataURL()` on `#creator-strip` / `#creator-lanes` to read a
  paint without a compositor frame. All of these survive the split unchanged (§5).

### 2e. IPC and storage it owns

Renderer side of the 17 bridge calls in 2b (channels `creator:*`, `catalog:*` — `main.js:4536-4602,
5425-5903`); `localStorage` keys `vc-catalog-mature`, `vc-catalog-sort` (2876–2877); `Jobs` ids
`ytdlp-fetch`, `track-generate-<sessionId>`, `finish-<sessionId>`, `catalog-<id>` — the tray's
labels and cancel handlers are the user-visible contract for the four background operations.

### 2f. A latent mismatch worth knowing before Phase 2

`announce.js:110-111` guards the announcement popup on
`typeof window.creatorView.hasStaged === 'function' && window.creatorView.hasStaged()`, and
`creatorView` has never had a `hasStaged` method (grep: the two announce.js lines are the only
occurrences in `renderer/`). The guard is therefore always false and the popup can open over a
staged session. Not this ticket's to fix — filed as a proposal — but Phase 2 must **not** "helpfully"
add the method while moving `creatorView`: that would change behaviour inside a pure-move PR.

## 3. Responsibility map

Grouped by kind; each row is a contiguous stretch of today's file. Banner comments travel with the
code under them.

### Constants and types

| Lines | What |
| --- | --- |
| 1–14 | file header: the vocabulary (upload / scene / takes) and the "no file paths here" rule |
| 17–18 | `SCENE_MAX` (mirrors `SCENE_MAX_SECONDS` in `src/creator.js`) |
| 53–56 | `MOD_LABEL` (⌘ vs Ctrl) |
| 61 | `CSTEPS` |
| 114 | `CSTEP_NAMES` |
| 327–332, 344 | `LANE_H`, `WAVE_H`, `CROP_WAVE_H`, `ADD_R`, `FRAME_INSET` |
| 405 | `ZOOM_MIN_WINDOW` |
| 852 | `rangeMod` (the one modifier: Shift) |
| 1084–1091 | `CAT_FIELDS` (per-category source fields) |
| 1152 | `TRACK_TITLES` |
| 1734 | `YTDLP_NOTE_DEFAULT` |
| 1789 | `YTDLP_JOB` |
| 2388 | `QUICK_ADD_GAP` |
| 2876–2877 | catalog `localStorage` keys |

### State

| Lines | What |
| --- | --- |
| 20–49 | `cstate` — the whole wizard's record (step, session, scene bounds, trim, takes, tracks, cast, selection, meta, peaks, zoom view, flags, edit target, tools) |
| 51 | `cvid()` — the `<video id="creator-video">` accessor |
| 727 | `panDrag` |
| 807–808 | `zoomHintUsed`, `zoomHintTimer` |
| 844 | `sceneDrag` (crop-step gesture) |
| 1106 | `prefilled` |
| 1203–1206 | `trackAudio` (one shared `<audio>`), `trackPlayingId`, `trackArmed`, `trackArmTimer` |
| 1747 | `toolsProbe` (session memo) |
| 1790–1791 | `ytdlpFetching`, `ytdlpAutoTried` |
| 2048 | `committedSessions` (Finish in flight — never discard) |
| 2052 | `window.packsListStale` initial value |
| 2242–2244 | `takeDrag`, `hoverAdd`, `hoverAddOver` (lane gesture) |
| 2607 | `thumbDrag` |
| 2869–2874, 2886–2897 | `catalogState`, `catalogThumbObserver` |
| 3030 | `catalogSearchDebounce` |

### Pure utilities (read `cstate`, write nothing but a return value)

| Lines | What |
| --- | --- |
| 191–197 | `sceneLen`, `toUpload`, `toScene` |
| 199–228 | `trimBounds`, `takeActive`, `clampTrim` (the working range) |
| 232–244 | `parseLinkStart` |
| 334–397 | `charKey`, `clampFrame`, `frameTime`, `laneColor` (reads `body.light`), `laneCharacters`, `peakRange` |
| 407–422 | `outerDomain`, `visibleDomain` — the one x-domain every px↔time conversion routes through |
| 854–875 | `nearestHandle`, `clampScene` |
| 885–894 | `sortTakes`, `takeInRange` |
| 1121–1125 | `thumbTime` |
| 1154–1199 | `trackTitle`, `trackWhence`, `trackDeletable`, `tfmt`, `tracksFromPack` |
| 1220–1222 | `selectedTrack` |
| 1523–1586 | `creatorSpec` (reads the form; the payload `creator:create` gets) |
| 2246–2326 | `topHit`, `laneHit` (measure a canvas rect, decide what is under the pointer) |
| 2333–2373 | `laneBounds`, `addBadge` |
| 2409–2411 | `sceneClamp` |
| 2879–2882 | `catalogSortMode` |

### Services and IO (IPC through `window.vc`, Web Audio decode, the `Jobs` tray, media elements, `localStorage`)

| Lines | What |
| --- | --- |
| 246–305 | `onStaged`, `loadPeaks` (`decodeUrl` → peaks) |
| 1360–1521 | `wireTracks` (row clicks, seek drag, **Generate** → `creatorTrackGenerate` + tray job, upload stub), `measureTracks` |
| 1588–1672 | `createPack` — Finish: `creatorCreate` as a fire-and-forget tray job, the `committedSessions`/`safeNow()` discipline from `renderer/CLAUDE.md` |
| 1742–1754 | `probeTools` (`creatorTools`, memoised) |
| 1782–1845 | `ensureLinkSupport` (`creatorFetchYtdlp` as a tray job) |
| 2054–2071 | `discardStaged` (`creatorDiscard`) |
| 2073–2102 | the three pickers: `btn-creator-pick`, `creator-file`, `btn-creator-fetch` (+ `btn-creator-fetch-ytdlp`) |
| 2104–2143 | `onCreatorProgress` — the `ytdlp` / `download` / `separate` / `create` / `isolate` phases, gated on `p.sessionId` except `download` |
| 2147–2171, 2197–2203 | `creatorPlay`, `creatorTogglePlay`, `bindMediaKeys` (the `<video>` transport, media session) |
| 2979–3028 | `renderCatalogSection` (`catalogIndex`), `catalogInstall` (`catalogInstall` as a tray job), `onCatalogProgress` |

### Sub-components and screens

| Lines | What |
| --- | --- |
| 184–189, 307–323, 1736–1740, 1756–1775 | the Video step's chrome: `renderStagedNote`, `creatorBusy`, `creatorLoading`, `resetYtdlpNote`, `applyTools` |
| 424–448 | zoom: `setView`, `zoomAt`, `updateZoomChip` |
| 450–700 | the painter: `paintEdgeShade`, `stripCtx`, `paintBracket`, `paintFramePin`, `paintStrip`, `paintLanes` |
| 702–753 | the pan bar: `renderPanBar`, `panTo`, its three listeners |
| 755–839 | the strip's overlays: `renderStripTip`, `showScrubTip`, `hideScrubTip`, `flashZoomHint`, `resetZoomHint`, `stripEl`, `stripTime` |
| 877–881 | `updateSceneReadout` |
| 896–1074 | the Lines list: `renderTakes` (146 lines: rows, shelves, per-row transport), `rowPlaying`, `updateRowPlayIcons`, `updateSelectedRows`, `focusCaption` |
| 1093–1137 | the Details form: `renderCategoryFields`, `renderDetailsPrefill`, `renderThumbModule`, `renderThumbHint` |
| 1208–1356 | the backing-track list: `stopTrackAudio`, `disarmTrack`, `renderTracks` (112 lines), `renderTrackHint` |
| 1711–1730 | `fillEditDetails` (edit-mode prefill of the Details form) |
| 2328–2331, 2375–2405 | `selectTake`, `setHoverAdd`, `quickAddAfter` |
| 2413–2600 | the lane gesture machine: `stripTakesPointerDown`, `lanesPointerDown`, `lanesHover`, `takesPointerMove` (70 lines, seven drag modes), `takesPointerUp` |
| 2602–2626 | the Details thumbnail frame line: `thumbDragTo`, `thumbPointerMove` |
| 2829–2855 | the Lines-step help modal (⌘/Ctrl + ?) |
| 2857–2862 | the "Create with Claude" modal |
| 2899–2977 | catalog rows: `catalogEmptyRow`, `catalogImportIcon`, `catalogRow`, `renderCatalogRows` |

### Orchestration and wiring (runs at load, or moves the wizard between screens)

| Lines | What |
| --- | --- |
| 57 | `.mod-key` fill — **runs at load** |
| 63–110 | `cshow(step)` — the step switch: reparents the video and strip, resets per-step chrome, repaints, redraws the header |
| 116–182 | `wizardSteps`, `creatorHeader` (drives `window.setWorkflowHeader`), `cback`, `confirmLeaveCreator`, `creatorExit` |
| 1674–1707 | `creatorReset` |
| 1777–1780 | `window.creatorToolsChanged` — **assigned at load** |
| 1847–2015 | `window.creatorView` — **assigned at load** |
| 2019–2034 | `creationAllowed` (the courtesy meter check) |
| 2036–2043 | the `input` → `dirty` listener — **registered at load** |
| 2145 | `wireTracks();` — **called at load** |
| 2173–2192 | video-box click, Space — **registered at load** |
| 2205–2238 | `cvid()` `timeupdate` / `pause` / `play` — **registered at load, calls `cvid()` at load** |
| 2628–2744 | the strip's `pointerdown` / `pointermove` / `pointerup` (dispatch by step) and `stripWheel` + its two listeners — **registered at load** |
| 2746–2769 | the lanes' four listeners, `creator-lanewrap` scroll → `renderStripTip`, the zoom chip — **registered at load** |
| 2771–2807 | `sceneNext` (Scene → Lines: re-anchors takes, trim and icon time) |
| 2809–2825 | `addCharacterLane` + its two listeners, the category `change` listener — **registered at load** |
| 2838–2862 | modal listeners — **registered at load** |
| 3026–3028, 3031–3042 | `onCatalogProgress`, the three catalog input listeners — **registered at load** |
| 3044 | `resize` → `paintStrip` — **registered at load** |

## 4. Proposed tree

**Directory: `renderer/creator/`, classic scripts, with `renderer/creator.js` kept as the entry.**
Not `web/lib/`'s namespace-IIFE shape: the file has 151 top-level declarations that call each other
bare, `cstate` must stay a bare global for CDP, and `app.js` calls nothing of this file bare — so
there is nothing a namespace would protect and 400 call sites it would rewrite. Bare top-level
declarations in more script tags is the only shape that is a pure move. `package.json`
`build.files` (`renderer/**/*`) already ships a subdirectory; `renderer/CLAUDE.md`'s vocabulary
note makes `creator/` the obvious name, and the header comment in `state.js` should say that
`src/creator.js` is main's half of the same feature.

Fifteen files. Sizes are measured, not targets; the four over 250 are argued in §6.

| # | File | Lines | Moves there (by name, with today's lines) |
| --- | --- | ---: | --- |
| 1 | `creator/state.js` | 105 | header comment + `'use strict'` + `SCENE_MAX` + `cstate` + `cvid` + `MOD_LABEL` and the `.mod-key` fill (1–57); `sceneLen`, `toUpload`, `toScene`, the working-range banner, `trimBounds`, `takeActive`, `clampTrim` (190–228); `committedSessions`, `window.packsListStale = false` (2044–2052) |
| 2 | `creator/steps.js` | 125 | the step-routing banner, `CSTEPS`, `cshow`, the header banner, `CSTEP_NAMES`, `wizardSteps`, `creatorHeader`, `cback`, `confirmLeaveCreator`, `creatorExit` (58–182) |
| 3 | `creator/source.js` | 178 | `renderStagedNote` (183–189); the source-step banner, `parseLinkStart`, `onStaged`, `loadPeaks`, `creatorBusy`, `creatorLoading` (229–323); the wiring banner + `creationAllowed` (2016–2034); `discardStaged` and the four picker listeners (2053–2102); the "Create with Claude" modal listeners (2856–2862 — the button is inside `creator-step-source`, `index.html:334`) |
| 4 | `creator/tools.js` | 115 | `YTDLP_NOTE_DEFAULT`, `resetYtdlpNote`, `toolsProbe`, `probeTools`, `applyTools`, `window.creatorToolsChanged`, `YTDLP_JOB`, `ytdlpFetching`, `ytdlpAutoTried`, `ensureLinkSupport` (1731–1845) |
| 5 | `creator/strip.js` | 264 | the strip banner, `LANE_H`…`ADD_R`, `charKey`, `FRAME_INSET`, `clampFrame`, `frameTime`, `laneColor`, `laneCharacters`, `peakRange`, the zoom banner, `ZOOM_MIN_WINDOW`, `outerDomain`, `visibleDomain`, `setView`, `zoomAt`, `updateZoomChip` (324–448); the pan banner, `renderPanBar`, `panDrag`, `panTo`, the pan-bar listeners, the tooltip banner, `renderStripTip`, the overlays banner, `showScrubTip`, `hideScrubTip`, `zoomHintUsed`, `zoomHintTimer`, `flashZoomHint`, `resetZoomHint`, `stripEl`, `stripTime` (701–839) |
| 6 | `creator/paint.js` | 252 | `paintEdgeShade`, `stripCtx`, `paintBracket`, `paintFramePin`, `paintStrip`, `paintLanes` (449–700) |
| 7 | `creator/takes.js` | 238 | the takes-step banner, `sortTakes`, `takeInRange`, `renderTakes`, `rowPlaying`, `updateRowPlayIcons`, `updateSelectedRows`, `focusCaption` (882–1074); `addCharacterLane` + its two listeners (2808–2822); the Lines-help banner, `linesHelpOpen`, `toggleLinesHelp`, their listeners and the ⌘/Ctrl+? keydown (2826–2855) |
| 8 | `creator/details.js` | 89 | the details banner, `CAT_FIELDS`, `renderCategoryFields`, `prefilled`, `renderDetailsPrefill`, `thumbTime`, `renderThumbModule`, `renderThumbHint` (1075–1137); `fillEditDetails` (1708–1730); the `// details` line and the category `change` listener (2823–2825) |
| 9 | `creator/tracks.js` | 384 | the backing-tracks banner and its rationale, `TRACK_TITLES`, `trackTitle`, `trackWhence`, `trackDeletable`, `tfmt`, `tracksFromPack`, `trackAudio`, `trackPlayingId`, `trackArmed`, `trackArmTimer`, `stopTrackAudio`, `disarmTrack`, `selectedTrack`, `renderTracks`, `renderTrackHint`, `wireTracks`, `measureTracks` (1138–1521) |
| 10 | `creator/finish.js` | 186 | `creatorSpec`, `createPack`, the lifecycle banner, `creatorReset` (1522–1707) |
| 11 | `creator/transport.js` | 93 | `creatorPlay`, `creatorTogglePlay`, the video-box click, the Space keydown, `bindMediaKeys`, the `timeupdate` / `pause` / `play` listeners on `cvid()` (2146–2238) |
| 12 | `creator/lanes.js` | 362 | the lane-dragging banner, `takeDrag`, `hoverAdd`, `hoverAddOver`, `topHit`, `laneHit`, `selectTake`, `laneBounds`, `addBadge`, `setHoverAdd`, `QUICK_ADD_GAP`, `quickAddAfter`, `sceneClamp`, `stripTakesPointerDown`, `lanesPointerDown`, `lanesHover`, `takesPointerMove`, `takesPointerUp` (2239–2600) |
| 13 | `creator/strip-input.js` | 249 | `sceneDrag`, `rangeMod`, `nearestHandle`, `clampScene`, `updateSceneReadout` (840–881); the thumbnail banner, `thumbDrag`, `thumbDragTo`, `thumbPointerMove`, the strip's three pointer listeners, `stripWheel` + its two `wheel` listeners, the lanes-wiring banner, the lanes' four pointer listeners, the `creator-lanewrap` scroll listener, the zoom-chip click, `sceneNext` (2601–2807) |
| 14 | `creator/catalog.js` | 180 | the catalog banner, `catalogState`, the two storage keys, `catalogSortMode`, `catalogThumbObserver`, `catalogEmptyRow`, `catalogImportIcon`, `catalogRow`, `renderCatalogRows`, `renderCatalogSection`, `catalogInstall`, `onCatalogProgress`, `catalogSearchDebounce`, the three input listeners (2863–3042) |
| 15 | `creator.js` (entry, same path) | 224 | `window.creatorView` (1846–2015); the `input` → `dirty` listener (2035–2043); `onCreatorProgress` and the `wireTracks();` call (2103–2145); the `resize` listener (3043–3044) |

Every file except `state.js` gains two lines at its top: a one-line header comment naming the file
and `'use strict';`. Nothing else is added, and no line is edited.

**Load order (the tags that replace `index.html:1054`, in this order):**

```
creator/state.js      creator/steps.js      creator/source.js    creator/tools.js
creator/strip.js      creator/paint.js      creator/takes.js     creator/details.js
creator/tracks.js     creator/finish.js     creator/transport.js creator/lanes.js
creator/strip-input.js creator/catalog.js   creator.js
```

Three edges are hard (they are the file's only load-time references to a declaration of another
proposed file, and each was confirmed by loading the fragments in the wrong order — §8):
`state.js` before `transport.js` (`cvid()` is *called* at load, 2205/2231/2235); `strip.js` before
`strip-input.js` (`renderStripTip` is passed as a listener at load, 2762); `tracks.js` before
`creator.js` (`wireTracks()` is called at load, 2145, and it touches `trackAudio` at 1436). Every
other cross-file reference is inside a function body or a callback and resolves at call time,
after all tags have run. `app.js` must stay before all fifteen (they call `$` at load).

**Line coverage (sums to 3044; every line assigned exactly once):**

| File | Ranges | Lines |
| --- | --- | ---: |
| state.js | 1–57, 190–228, 2044–2052 | 105 |
| steps.js | 58–182 | 125 |
| source.js | 183–189, 229–323, 2016–2034, 2053–2102, 2856–2862 | 178 |
| strip.js | 324–448, 701–839 | 264 |
| paint.js | 449–700 | 252 |
| strip-input.js | 840–881, 2601–2807 | 249 |
| takes.js | 882–1074, 2808–2822, 2826–2855 | 238 |
| details.js | 1075–1137, 1708–1730, 2823–2825 | 89 |
| tracks.js | 1138–1521 | 384 |
| finish.js | 1522–1707 | 186 |
| tools.js | 1731–1845 | 115 |
| transport.js | 2146–2238 | 93 |
| lanes.js | 2239–2600 | 362 |
| catalog.js | 2863–3042 | 180 |
| creator.js | 1846–2015, 2035–2043, 2103–2145, 3043–3044 | 224 |
| **Total** | | **3044** |

Blank separator lines are attached to the block above them, which is why a range starts one line
before its banner (e.g. 58 is the blank before `/* ---------- step routing ---------- */`).

## 5. The entry point afterwards

`renderer/creator.js` stays at its path and keeps its tag as the last of the group, and it keeps
every name §2 lists:

- `window.creatorView` is assembled there, unchanged (1847–2015). Its nine methods call
  `creatorReset`, `bindMediaKeys`, `probeTools`, `applyTools`, `ensureLinkSupport`,
  `renderCatalogSection`, `tracksFromPack`, `fillEditDetails`, `cshow`, `creatorLoading`,
  `updateSceneReadout`, `renderTakes`, `loadPeaks`, `confirmLeaveCreator`, `paintStrip`,
  `discardStaged`, `creatorBusy`, `onStaged`, `creationAllowed` — all bare, all declared in earlier
  tags, all resolved at click time exactly as they are today. `app.js:2543`'s comment ("creator.js
  loads after app.js; defined by click time") stays true.
- `window.packsListStale` is initialised in `state.js` and written from `finish.js` and
  `catalog.js`; `app.js` reads the same `window` property.
- `window.creatorToolsChanged` moves with the memo it clears (`tools.js:1780`); `app.js:2117` reads
  the same `window` property.
- `cstate` is declared in `state.js`, the first tag, so `page.evaluate('cstate')` keeps working and
  `window.cstate` stays `undefined` — the global declarative record is shared by all classic scripts
  on the page, which is the whole reason this split needs no plumbing.
- The entry also keeps the three page-level registrations that belong to no one screen:
  `input` → `dirty` on `view-creator`, `onCreatorProgress` (it fans out to the tray and to whichever
  session's status line, across `source`, `tracks` and `finish`), and `resize` → `paintStrip`; plus
  the one load-time call, `wireTracks()`, which makes the entry the place where the order of
  load-time side effects is read in one screen.
- Every DOM id, IPC channel, `Jobs` id and `localStorage` key in §2 is untouched: the split moves
  lines, it renames nothing.

## 6. Where ~250 lines would force an artificial boundary

Files over 250, each with its honest size and why it stays whole:

| File | Lines | Why it stays whole |
| --- | --- | --- |
| `creator/tracks.js` | 384 | One component: the backing-track list's model (`tracksFromPack`, `trackTitle`…), its render (`renderTracks`, 112 lines) and its wiring (`wireTracks`, 149 lines, including Generate). The four `let`s at 1203–1206 (`trackAudio`, `trackPlayingId`, `trackArmed`, `trackArmTimer`) are written by both halves — the render reads the playhead off `trackAudio`, the wiring drives it — so a cut at 1358 (render 218 / wiring 164) would put a component's state on one side of a file boundary and half its writers on the other. That cut is the fallback if Eric wants two files; nothing else in the block is a seam. |
| `creator/lanes.js` | 362 | One gesture system: `laneHit` decides what is under the pointer (edge / frame / body / badge / lane, with the four-rule tie-break at 2277–2310), `lanesPointerDown` turns that into one of seven drag modes, `takesPointerMove` runs the mode, `takesPointerUp` commits it, and `addBadge` is both what `paintLanes` draws and what `laneHit` tests. The one seam (2411/2413: hit-testing + badge geometry 172 / the pointer machine 188) splits a state machine from its input classifier; I would not take it, and it is the only alternative. |
| `creator/strip.js` | 264 | The x-domain and everything that reports it: the constants and lane geometry, `outerDomain`/`visibleDomain` ("the one source every px↔time conversion routes through", 401–403), zoom, the pan bar, the tooltip, the scrub tip, the zoom hint, `stripTime`. Cutting the overlays (701–839, 139 lines) into their own file would leave `strip.js` at 125 and make the pan bar live apart from the domain it is a scrollbar for. 14 lines over is not a reason. |
| `creator/paint.js` | 252 | The two painters that `renderer/CLAUDE.md` says must stay in lockstep (`paintStrip` paints the scene lane then calls `paintLanes`; same x-domain, same gutter) plus the four helpers only they call. Two lines over. |

Under 250 on purpose (each is a whole responsibility with nothing else that belongs to it):
`details.js` 89 (the form and its prefills), `transport.js` 93 (the `<video>` transport and the
keys that drive it), `state.js` 105, `tools.js` 115, `steps.js` 125. Two merges were considered and
rejected: `state.js` + `steps.js` (230) — the record and the step machine are different
responsibilities and the file would be named for neither; `source.js` + `tools.js` (293) — the tool
probe also gates the tracks list's Generate button (`applyTools` → `renderTracks`), so it is not the
Video step's alone.

## 7. Build and packaging touch points

| Where | Change |
| --- | --- |
| `renderer/index.html:1054` | Replace the one `<script src="creator.js">` tag with the fifteen tags in §4's order, at the same position (after `../src/announce.js`, before `announce.js`). Line 338's comment ("creator.js and main.js are the other two" kill-switch layers) becomes `creator/catalog.js`. |
| `package.json` `build.files` | **No change.** The allowlist is `["main.js", "preload.js", "src/**/*", "renderer/**/*", "brand/*.png", "brand/fonts/*", "package.json", "node_modules/**/*"]` — `renderer/**/*` already bundles a subdirectory. The asar leak check in the Windows CI job and the macOS release runbook looks for pack media, not scripts. |
| Renderer CSP (`index.html:5`) | **No change.** `default-src 'self'` with no separate `script-src`; the new files are same-origin `file:` siblings of the ones already loading, exactly like `../src/*.js`. |
| `main.js`, `preload.js` | **No change.** The smoke walkthrough evaluates DOM ids only (§2d); the bridge names are unchanged. |
| `scripts/build-web.js`, `web/index.html`, `deploy-web.yml`, `firebase.json` | **Not involved.** `renderer/creator.js` is not on the web page; `sharedModules()` reads `web/index.html`, which names nothing under `../renderer/`. |
| Tests | Nothing reads `renderer/creator.js` by path (grep over `test/`): `test/fonts.test.js:144` sweeps `renderer/app.js` and `renderer/reel.js` only; `test/reelrender.integration.test.js:529` copies `renderer/` with `cpSync(..., { recursive: true })`, so a subdirectory travels. **Add one static test** in the first Phase 2 PR, `test/renderer-creator-split.test.js`, the renderer's twin of `web-ids.test.js`: (a) the `creator/*.js` tags in `renderer/index.html`, in order, equal the directory listing of `renderer/creator/` plus `creator.js` last — a file in the tree with no tag is dead code and a tag with no file is a silent 404 in the asar; (b) each file's first non-comment line is `'use strict';`; (c) no top-level `const|let|class|function` name is declared twice across `renderer/app.js`, `renderer/reel.js`, `renderer/announce.js`, `renderer/creator.js`, `renderer/creator/*.js` and the `src/` files the page loads (a duplicate is a `SyntaxError` that kills only the second script, and the failure surfaces later as "X is not defined"); (d) every `$('literal')` in `renderer/creator/*.js` and `creator.js` names an id in `renderer/index.html`. |
| Docs that go stale (prose, fix in the last PR) | `renderer/CLAUDE.md:92-93` ("`creatorHeader` in `creator.js`" → `creator/steps.js`), `:111` ("`renderer/creator.js`, loaded after `app.js`"; `paintStrip`/`paintLanes`/`topHit`/`laneHit`/`enterEdit` homes), `:157-165` (`createPack()` → `creator/finish.js`; `committedSessions` → `creator/state.js`; `creatorReset()`/`discardStaged()`); root `CLAUDE.md` "Ad-hoc live driving" bullet (add that `cstate` lives in `renderer/creator/state.js`); the creator-verification memory note ("creator.js loads after app.js" stays true). |
| Dev, packaged, CI | Dev: `npm start` loads `file://…/renderer/index.html`, tags resolve relative to `renderer/`. Packaged: `renderer/**/*` in the asar. CI: `npm test` gains the static test; the Windows release job builds the same allowlist. Nothing else to teach. |

## 8. Verification recipe

The `implementing-changes` tier for `renderer/*` is a scoped smoke run; the creator has no leg, so
the proof is layered. Every layer must run on the exact tree of each PR before its commit.

**1. The pure-move check (mechanical, run first).** With the base commit's file and the new tree:

```bash
cd /path/to/BadTakes/renderer
node -e '
const fs=require("fs"),{execFileSync}=require("child_process");
const base=execFileSync("git",["show","main:renderer/creator.js"],{encoding:"utf8"}).split("\n");
if(base.at(-1)==="")base.pop();
const html=fs.readFileSync("index.html","utf8");
const tags=[...html.matchAll(/<script src="(creator(?:\/[a-z-]+)?\.js)"><\/script>/g)].map(m=>m[1]);
let cat=[];for(const f of tags){const l=fs.readFileSync(f,"utf8").split("\n");if(l.at(-1)==="")l.pop();
  for(const x of l){if(/^\/\/ creator(\/[a-z-]+)?\.js /.test(x))continue;cat.push(x);}}
const strip=a=>a.filter(l=>l!=="\x27use strict\x27;");
const s=a=>a.slice().sort().join("\n");
console.log("tags:",tags.join(" "));
console.log("pure move:",s(strip(cat))===s(strip(base)),"| base",base.length,"lines, new",cat.length,"incl.",cat.length-strip(cat).length,"use-strict lines");
for(const f of tags)execFileSync(process.execPath,["--check",f]);console.log("node --check: all parse");'
```

It reads the tag list out of `index.html` (so a forgotten tag fails here), drops only the per-file
header comment and `'use strict'` lines, and compares the two multisets of lines — which is the
statement "every line of the original appears exactly once in the new files, and nothing was
added or edited". `git diff --color-moved=plain` on the PR is the human-readable companion. The
scratch run of this check against the §4 ranges printed `pure move: true` and `node --check: all
parse`.

**2. Load-order check (mechanical).** Run the fifteen files in tag order in one `vm` context with a
stub DOM (the `web-guest-mode.test.js` stub is the model: `document.getElementById` → an element
with `classList`/`style`/`addEventListener`/`getBoundingClientRect`; `Audio`, `IntersectionObserver`,
`localStorage`, `navigator.mediaSession`; `$`, `rgba`, `fmtTime`, `svgIcon`, `window.vc` with
`onCreatorProgress`/`onCatalogProgress`/`track`; `Jobs`, `Catalog`). Pass = no throw, and
afterwards `Object.keys(ctx.creatorView)` lists the nine methods and `typeof cstate === 'object'`.
The scratch run passed, and reordering `transport.js` before `state.js`, loading `strip-input.js`
without `strip.js`, or `creator.js` without `tracks.js` each threw the predicted
`… is not defined`. Fold this into the static test from §7 if it stays cheap; otherwise keep it as
the PR description's second command.

**3. `npm test`** — the new static test plus the existing suite (nothing else names the file).

**4. Scoped smoke, home leg:** `rm -rf /tmp/bt-lib && cp -R fixtures/library /tmp/bt-lib &&
BT_LIBRARY_ROOT=/tmp/bt-lib SMOKE_SCOPE=home npm run smoke` → `SMOKE_OK`, exit 0, and in the log
`SMOKE_MAKE view: view-creator` (read this line by eye: the leg's only fatal assertion is that Exit
returns home, which passes vacuously if the creator never opened), `SMOKE_MAKE sections: 3`,
`header title: Make a scene`, and the `1b-makescene` screenshot showing the three cards and the
Catalog section. This proves the fifteen tags evaluated far enough for `creatorView.enter()` →
`creatorReset()` → `cshow('source')` → `creatorHeader` to run without throwing, and nothing past
the landing page.

**5. CDP drive (the real proof; run for the last PR and for any PR that moves `lanes.js`,
`strip-input.js`, `paint.js`, `tracks.js` or `finish.js`).** Launch
`BT_LICENSE_BYPASS=1 BT_LIBRARY_ROOT=/tmp/bt-lib npx electron . --remote-debugging-port=9333
--user-data-dir=/tmp/bt-ud --disable-features=CalculateNativeWinOcclusion
--disable-backgrounding-occluded-windows --disable-renderer-backgrounding`, connect with
`puppeteer-core` (`browserURL`), and in order:
1. Read `Runtime.exceptionThrown` / console errors from page load: **zero**. (A load-time
   `ReferenceError` in one tag does not stop the others — this is the check the smoke cannot do.)
2. `page.evaluate(() => [typeof cstate, typeof window.creatorView, document.scripts.length])` →
   `['object','object', 33]` (19 tags today − 1 + 15).
3. `$('btn-create-pack').click()` → `cstate.step === 'source'`, `#view-creator` visible, catalog
   rows or its empty row rendered, `#creator-link-card` hidden (link import defaults off).
4. Stage `fixtures/library/fixture-dub-scene-me8horfs0000/1755000002000/dub_video.mp4` with
   `elementHandle.uploadFile` on `#creator-file` → `cstate.step === 'scene'`, `cstate.duration > 0`,
   `#creator-strip` `toDataURL()` not blank, `#scene-len` text set.
5. `page.mouse` Shift-drag across the strip → `cstate.sceneIn/Out` moved, `cstate.dirty === true`;
   wheel with `ctrlKey` → `cstate.view` non-null and `#btn-strip-zoom` visible; click the chip →
   `cstate.view === null`.
6. Header Next (`#wh-next`) → `cstate.step === 'takes'`, `#creator-lanes` painted. Type a name in
   `#creator-new-char` + Enter → `cstate.cast.length === 1`. Shift-drag on the lane →
   `cstate.takes.length === 1`, a row in `#creator-takes`, its caption focused. Drag the line's
   end handle → `takes[0].end` moved. Hover past the line → the `+` badge (`addBadge` non-null),
   click it → `takes.length === 2`. Space → the video plays and pauses. ⌘/Ctrl+? → the help modal.
7. Next → `cstate.step === 'details'`; `#creator-video` reparented into `#creator-thumb`; drag on
   the strip → `cstate.iconTime` set; `#creator-tracks` empty state shown, Generate disabled with
   the demucs note (no demucs on CI). Type a title; Finish → `Jobs.list()` shows `finish-…`;
   await the `creator:create` result → the scene is in `state.packs` and selected on home.
8. Edit: select `fixture-dub-scene`, `$('btn-edit-pack').click()` → `cstate.editing` set,
   `cstate.step === 'takes'` painted **before** `creatorEditStage` resolves (the `enterEdit`
   first-paint rule), `cstate.takes.length === 3`, `cstate.tracks` from `tracksFromPack`; Back →
   Exit confirm dialog (`confirmLeaveCreator` says nothing to lose when `dirty` is false — so
   first toggle a line to make it dirty); Discard → home.
9. The wordmark from the Lines step with a marked take → the same confirm (`app.js` →
   `creatorView.confirmLeave`). Theme toggle → `creatorView.repaint()` repaints without error.
   Settings → toggle link import → `creatorToolsChanged` clears the memo; reopen the creator →
   the link card shows.

Steps 3–9 touch every file in the tree at least once; the memory note's rules apply (poll for
`window.creatorView`, real CDP input for drags, `toDataURL()` for canvases, trust `cstate` over a
screenshot).

**6. Packaged (last PR only):** `npm run dist` and open the creator in `dist/mac-arm64/Bad
Takes.app` — the one place a tag naming a file missing from the asar would show (it 404s silently
under `file:`; the static test is what prevents it).

## 9. Phase 2 order

Three PRs, each a pure move verified by §8 steps 1–4, the fifth step where noted. Each PR inserts
its tags at their **final** positions in §4's order (the order at every stage is then a subsequence
of the final order, so the three hard edges hold throughout) and runs the pure-move check against
`main:renderer/creator.js`.

| PR | Contents | Extra proof |
| --- | --- | --- |
| **1. Constants, state, pure utilities, the painter** | `renderer/creator/state.js`, `strip.js`, `paint.js`; the tags; `test/renderer-creator-split.test.js` (§7); `creator.js` shrinks to 2423 lines. This is the PR that establishes the directory, the header-comment convention and the static guard. | CDP step 5 (zoom, pan, crop paint) — the painter and the domain moved. |
| **2. Services and IO** | `source.js`, `tools.js`, `tracks.js`, `finish.js`, `catalog.js`; `creator.js` shrinks to 1380. | CDP steps 3–4 and 7 (stage, tracks list, Finish, catalog). |
| **3. Screens and their input, entry reduced** | `steps.js`, `takes.js`, `details.js`, `lanes.js`, `strip-input.js`, `transport.js`; `creator.js` is the 224-line orchestrator; `renderer/CLAUDE.md`, `index.html:338` and the root `CLAUDE.md` bullet updated. | The full CDP drive (steps 1–9) and the packaged check. |

One PR for the whole file is possible (the pure-move check is the same either way) but would be a
3044-line diff with fifteen new files; three PRs keep each review to five files or fewer and let a
regression bisect to a PR. The order is Eric's (utilities, then state and services, then
sub-components, then the entry), with `state.js` in the first PR because `transport.js` and
everything else needs `cvid` and `cstate` first.

## 10. Risks

- **Load order is a real hazard with exactly three edges** (§4), and the failure mode is quiet: a
  `ReferenceError` at load kills that one tag and the page keeps going, so the symptom is a
  "`wireTracks is not defined`" in a console nobody is reading and a Details step with no tracks
  list. Mitigations: the tag order in §4, the `vm` load check (§8.2), the console-error assertion
  in CDP step 1. Never move a load-time statement (§3's last table) into a file that loads before
  its callee's file.
- **Temporal-dead-zone across tags.** A top-level `const`/`let` of a classic script is created when
  *that* script runs; an earlier script's load-time code that names it gets "not defined", a later
  one inside the same script's TDZ window gets the TDZ error. After the split, `paint.js` reads
  `takeDrag`/`hoverAdd` (declared in `lanes.js`, a later tag) and `steps.js`'s `creatorHeader`
  reads `committedSessions` (`state.js`, earlier) — both inside functions called after load, so
  both are safe; a future edit that reads either at load would not be. The static test cannot see
  this; the `vm` load check can.
- **Duplicate top-level names.** Two classic scripts declaring the same `let`/`const`/`class` is a
  `SyntaxError` at the second script's instantiation — that script is skipped entirely. A pure
  move cannot create one (the multiset check would show the line twice), but Phase 2 will be
  tempted to add a small helper in two files. The static test's rule (c) is for that. Note the
  existing near-miss: `wireTracks` has a *local* `seekTo` (1414) shadowing `app.js`'s top-level
  `seekTo` (`app.js:7811`) — harmless, and it must stay local.
- **`'use strict'` per file.** A file that loses the directive runs sloppy; nothing in the
  behaviour changes visibly until something relies on `this` or on an assignment to an undeclared
  name throwing. Rule (b) of the static test.
- **`window.cstate` is undefined and must stay so.** Anything that "fixes" CDP access by assigning
  `window.cstate = cstate` in the split changes the contract (and the memory note). Bare it is.
- **What the smoke cannot see:** everything past the landing page — the painter, the lane
  editor, the tracks list, Finish, edit mode, the thumbnail picker, Space and media keys. And the
  home leg's `SMOKE_MAKE` block has one fatal assertion that passes vacuously when the creator
  fails to open. The CDP drive is not optional for PR 3.
- **Behaviour changes hiding in a move.** Three places invite one: `announce.js`'s `hasStaged`
  (§2f — leave it), the header comment's stale globals list (lines 11–14 — leave it or fix it in
  the docs PR, not in a move), and `creatorSpec`'s placement (it reads the form, so it looks like
  `details.js`; it is Finish's payload and stays in `finish.js`, but either home is a pure move).
- **The name `creator/`.** `src/creator.js` is main's half of the same feature (`SCENE_MAX`'s
  comment, line 17). The directory is the renderer's; say so in `state.js`'s header so the next
  reader does not go looking for a third copy of the scene-length rule.
- **Not a risk, but a limit:** the split leaves `app.js` (8991 lines) as the file every creator
  module depends on for `$`, `rgba`, `fmtTime`, `toast`, `showView`, `decodeUrl`, `refreshPacks`,
  `selectPack`, `endGameSession` and `state`. The `app.js` spike owns that; this plan assumes those
  names keep their bare, load-before-creator positions.
