# BAD-036 spike: splitting `main.js`

*2026-09-15 · BAD-036 · Amandine (02, architect) · Phase 1, planning only. No code was changed.*

Line numbers below are for `main.js` at commit `20d11e1` (6724 lines). Every span was
measured, not eyeballed: the file was loaded under a stubbed `electron` and each top-level
function's and each registered handler's `Function.prototype.toString()` was located in the
source (the script is reproducible from §8). `main-smoke.md` beside this file is the scout's
raw survey of the walkthrough; where the two differ, §2 here is the one to trust.

Eric's rule for this ticket (relayed by Spud mid-spike): **250 lines is the point at which a
file gets looked at, not a cap.** §4 splits by responsibility and lets cohesive units stay
whole; §6 lists every proposed file over 250 with its honest size and the reason.

## 1. The file today

| | |
| --- | --- |
| Path | `/Users/ericlugo/Personal/BadTakes/main.js` |
| Lines | 6724 (123 top-level functions, 84 `ipcMain.handle` registrations, 36 helpers nested inside `registerIpc`, 0 `ipcMain.on`) |
| Runtime | Electron **main process** (Electron 44, Node 22 ABI). CommonJS, `'use strict'`, no build step, no lint config in the repo. |
| Loaded by | `package.json` → `"main": "main.js"`; `electron .` (`npm start`, `npm run smoke`, `npm run start:free`, `scripts/start-local.js`), and the packaged app's `app.asar`. Nothing `require`s it: `cli/`, `scripts/`, `web/`, `server/` and `admin/` never load it (grep: the only hits are comments, plus two tests that read it *as text*, see §2). |
| Shape | One flat module: 72 lines of requires, then subsystems separated by `// ----------` banners, then one 2953-line `registerIpc()` holding every handler and 36 closure-scoped helpers, then `createWindow()` with the 570-line smoke walkthrough inlined, then `app.whenReady()` holding the `pack://` protocol handler, then four lifecycle hooks. |
| Module-scope side effects | `app.commandLine.appendSwitch` ×5 and `app.setPath('userData')` under `--smoke` (76-98); `app.requestSingleInstanceLock()` + `app.on('second-instance'|'open-file'|'open-url')` (179-199, deliberately before `ready`); `protocol.registerSchemesAsPrivileged` (201-203, must precede `ready`); `app.whenReady().then(...)` (6555); `app.on('window-all-closed'|'before-quit'|'will-quit')` (6702-6724). |

## 2. Public surface (the compatibility contract)

Everything outside the file that reaches into it. A split may change nothing in this section.

### 2a. IPC channels — `preload.js` ↔ `main.js`

`preload.js` exposes `window.vc` with 84 `ipcRenderer.invoke` channels and 8 `ipcRenderer.on`
push channels. Every one of the 84 invoke channels has exactly one `ipcMain.handle` in
`main.js` (verified: the handler set registered under a stubbed `ipcMain` equals the invoke
set in `preload.js`). The channel names are the contract; the bridge method names are
`preload.js`'s and do not change either.

| Group | Channels (main.js line of the handler) |
| --- | --- |
| licence | `license:status` 2959, `license:oauth` 3038, `license:otpStart` 3041, `license:otpVerify` 3043, `license:quota` 3048, `license:usage` 3068, `license:signout` 3073, `license:deleteAccount` 3098 |
| flags | `flags:get` 3033, `flags:refresh` 3034 |
| announcements | `announce:list` 2969, `announce:record` 2983, `announce:act` 3020 |
| profile | `profile:get` 3152, `profile:update` 3197, `profile:checkHandle` 3258 |
| collab | `collab:status` 3512, `collab:create` 3737, `collab:list` 3855, `collab:thumb` 3865, `collab:join` 3915, `collab:claim` 3982, `collab:release` 3994, `collab:push` 4006, `collab:pull` 4087, `collab:takes` 4155, `collab:leave` 4172, `collab:update` 4194, `collab:heartbeat` 4240, `collab:start` 4257, `collab:wrap` 4283, `collab:reopen` 4293, `collab:mine` 4306, `collab:report` 4317 |
| catalog | `catalog:index` 4536, `catalog:thumb` 4546, `catalog:install` 4570, `catalog:installCancel` 4602 |
| updates | `updates:check` 4612, `updates:status` 4613, `updates:open` 4617 |
| packs | `packs:importZips` 4619, `packs:importPaths` 4626, `packs:delete` 4635, `packs:exportZip` 4671, `packs:versions` 4739, `packs:setHead` 4749, `packs:fork` 4759, `packs:scan` 4770, `packs:pickZips` 4812, `packs:pickZipFolder` 4827, `packs:openReady` 4843 |
| activity | `activity:recordingStart` 4854, `activity:recordingFinish` 4869 |
| takes | `rec:save` 4873, `rec:list` 5003, `rec:delete` 5129, `scene:save` 5008, `scene:list` 5112, `scene:delete` 5117 |
| scoring | `score:config` 4964, `score:trend` 4969 |
| app | `settings:get` 4978, `settings:set` 4979, `analytics:track` 4992, `media:askCamera` 4998 |
| video / exports | `video:prepare` 5141, `video:export` 5155, `mix:export` 5409 |
| reel | `reel:start` 5278, `reel:frames` 5300, `reel:abort` 5313, `reel:finish` 5318 |
| creator | `creator:tools` 5425, `creator:fetchYtdlp` 5480, `creator:cancelYtdlpFetch` 5504, `creator:pickSource` 5509, `creator:stage` 5522, `creator:editStage` 5561, `creator:fetchLink` 5641, `creator:create` 5729, `creator:discard` 5865, `creator:trackGenerate` 5875, `creator:trackCancel` 5903 |

Push channels main sends (`webContents.send`), all subscribed in `preload.js`:
`license:changed` (342-351 `setLicenseStatus`), `updates:changed` (1587-1598 `setUpdateStatus`),
`packs:opened` (139 `flushOpens`), `collab:invite` (167 `flushInvites`), `collab:progress`
(3421 `collabProgress`), `collab:changed` (4351 `collabPushChanged`), `catalog:progress`
(inside `catalog:install`, 4570-4600), `creator:progress` (inside `creator:fetchYtdlp`,
`creator:fetchLink`, `creator:create` and `creator:trackGenerate`). Payload shapes are the renderer's contract too; a move keeps them
byte for byte.

### 2b. The `pack://` protocol

- Scheme registered privileged at 201-203 (`standard, secure, supportFetchAPI, corsEnabled, stream, bypassCSP`), **before `ready`**.
- URL shape: `pack://media/<base64url of the absolute path>` — encoder `mediaUrl` (2403-2405), decoder inline in `protocol.handle('pack', …)` (6617-6659). Takes append `?v=<mtime>` (2465, 2480, 4909, 4956).
- Allowlist: `allowedRoots()` (2373-2377) = `packsRoot` (set at 6568 from `libraryMode().root`), `recordingsRoot()`, `sceneRecordingsRoot()`, `videoCacheRoot()`, `creatorRoot()`, `catalogRoot()`, `collabThumbRoot()`, `announceRoot()`, checked with `isUnderAllowedRoot` from `src/allowlist.js` (already plain Node and unit-tested in `test/allowlist.test.js`).
- Responses: 200 with `Accept-Ranges`, 206 slices for `Range: bytes=a-b` / `bytes=a-` / `bytes=-n`, 416 with `Content-Range: bytes */size`, 403 outside the roots, 404 for a non-file, 400 on any throw. MIME table 6610-6615. The renderer's `<video>` seeking and the creator's trim strip depend on the 206 path (`src/CLAUDE.md`).
- Consumers: every `pack://` URL the renderer holds (`packForRenderer` 2407-2431, `listRecordings`, `listSceneRecordings`, `announceAsset` 918, `catalog:thumb` 4563, `collab:thumb` 3907, `creator:stage` result 2921, `creator:trackGenerate` 5896). The renderer CSP allows `img`/media from `pack:` only (`renderer/CLAUDE.md`).

### 2c. CLI flags, environment variables, userData files

| Kind | Name | Where read | Meaning |
| --- | --- | --- | --- |
| argv | `--smoke` | 74 | `SMOKE` — the walkthrough; also flips bypass (245), analytics off (1382), updates off (1620), yt-dlp update off (2581), collab polling off (4349, 4453), catalog refresh off (4498), camera prompt off (4999), dialog-less reel save (5280), window hidden (5950), single-instance lock skipped (179), migration skipped (6569), protocol client registration skipped (6565), mic prompt skipped (6671). Only `argv.includes('--smoke')`; **no other switch is parsed** — `--remote-debugging-port` and `--user-data-dir` are Chromium's own, consumed before main.js runs. |
| argv | `*.take` paths | `takesInArgv` 112-117 | Windows/Linux cold start and `second-instance` file open (queued until `packs:openReady`). |
| argv | `badtakes://…` | `invitesInArgv` 155-160 via `Collab.parseInviteUrl` | Invite links on Windows/Linux; macOS arrives via `open-url` (194). |
| env | `BT_LICENSE_BYPASS` (`1`/`free`/`adfree`/`plus`) | 250, 272 | Unpackaged licence bypass and its simulated tier (`npm run start:free`). |
| env | `BT_FREE_USED`, `BT_FREE_SHARED` | 307-308 | Seed the simulated meter. |
| env | `BT_SHARED_SCENES`, `BT_SHARED_TAKES`, `BT_TAKES_RECORDED` | 1055-1056, 1103 | Seed the simulated usage report. |
| env | `BT_FLAGS` | 664-665 | Flag overrides, unpackaged, never under `--smoke`. |
| env | `BT_ANNOUNCE` | 781-783 | Announcement queue override, same guards. |
| env | `BT_ANALYTICS_DEV` | 1387 | Send analytics from an unpackaged run. |
| env | `BT_PACKS_ROOT` | 2005 | Flat legacy library; wins over `BT_LIBRARY_ROOT`. |
| env | `BT_LIBRARY_ROOT` | read inside `Library.resolveLibraryRoot` (`src/library.js`), called at 2007 | Layout-2 tree root. |
| env | `BT_DEMUCS`, `BT_DEMUCS_MODEL` | 2617-2618 | demucs.cpp binary/model for `isolateBackground`. (`BT_FFMPEG`/`BT_FFPROBE`/`BT_YTDLP`/`BT_WHISPER_CLI` are read in `src/tools.js`, not here.) |
| env | `BT_CREATOR_DEBUG` | 5683, 5689 | Dump yt-dlp argv/stderr into userData. |
| env | `BT_SUPABASE_URL`, `BT_SUPABASE_ANON_KEY`, `BT_ACTIVATION_PUBLIC_KEY` | via `licenseConfig({env})` in `src/license-config.js`, called at 332 | `npm run start:local`'s overrides. |
| env | `SMOKE_DIR`, `SMOKE_SCOPE`, `SMOKE_THEME`, `SMOKE_VIDEO_WAIT`, `SMOKE_REEL` (`0`/`a`/`scene`), `SMOKE_REEL_WAIT` | 5980, 6000, 6013, 6306, 6448/6453/6474, 6503 | Walkthrough controls (see 2e). |
| env | `SystemRoot`, `PATH` | 413, 5686 | Windows `reg.exe` location; debug dump. |
| userData | `license.json`, `settings.json`, `analytics.json`, `scores.jsonl`, `machine-id.json`, `recordings/`, `scenes/`, `videocache/`, `creator/`, `catalog/`, `collabthumbs/`, `announce/` (+ `seen.json`), `library/` (via `src/library.js`), `tools/` (via `src/ytdlp.js`), `creator-debug*.{json,log}` | 222, 1904-1907, 1357, 1937, 419, 2373-2401, 2873 | On-disk contract with existing profiles; a split changes no path. |

### 2d. Tests and docs that reach into the file's *text*

- `test/collabthumb.test.js:31` reads `main.js` as a string and asserts every `uploadCollabThumb(` call site (there are two: 3844, 4224) passes a pack rather than `.iconFile`. **After the move it must read `main/**/*.js` too, or it finds zero call sites and fails.**
- `test/release-workflow.test.js:40` lists `/main.js` in a *simulated* asar listing for the leak-check step; the check greps for `voicepack` and `(^|/)(server|site)/`, so a new `main/` directory needs no change there (and must not be named `server` or `site`).
- Docs that name `main.js` locations, all prose, no test: `src/CLAUDE.md` (the `main.js` bullet, `resolveScene`, `transcodeVideo`, `sceneVideo`, `noteLicenseVerdict`…), `renderer/CLAUDE.md` (`transcodeVideo`, `applyGeneratedBed`, `carryExistingBed`), `server/CLAUDE.md:38-43, 103, 241, 269, 565, 686`, `admin/README.md:101` (`sceneBucket()` "in the app's `main.js`; a fourth copy would be a bug"), `web/lib/report.js`, `web/account.js`, `web/collabweb.js` comments, `server/supabase/config.toml:21`. Sweep in the last PR (§9).

### 2e. The smoke walkthrough's renderer contract

`createWindow()` under `SMOKE` (5979-6551) drives the renderer through one evaluator,
`js = (code) => win.webContents.executeJavaScript(code, true)` (5992), 123 times, and reads
back through `console.log('SMOKE_…')`. **These names are what the walkthrough evaluates
by string in the renderer.** They are reachable today because `renderer/app.js` is a classic
script whose top-level bindings share one global lexical scope with the page; a split of
`renderer/app.js` must keep every one of them resolvable by that exact name from a bare
`executeJavaScript` — Spud is handing this table to the renderer architect.

**Top-level renderer functions and bindings called by name** (main.js line of each `js()`):

| Name | Kind in `renderer/app.js` | Called at | Leg |
| --- | --- | --- | --- |
| `applyLicenseStatus(status)` | function | 6021, 6031, 6037, 6065, 6076, 6082 | home |
| `state` — `.packs[0].id`, `.pack.title`, `.pack.kind`, `.pack.videoFile`, `.pack.id`, `.pack.folderName`, `.recordings` (read **and assigned**: `state.recordings = r` at 6465), `.queue`, `.queueIndex` | top-level `const` object | 6109, 6131, 6227, 6293, 6350, 6457, 6458, 6465, 6500 | home, booth, screening, reel |
| `reportImport(report)` | async function | 6127, 6153 | home (main interpolates a JSON report into the string) |
| `refreshPacks()` | async function | 6141, 6165, 6188 | home |
| `enterScreening()` | function | 6475 | reel |
| `buildSchedule()` | async function | 6486, 6488, 6500 | reel |
| `activeLines(pack)` | function | 6500 | reel |
| `dubbedCamTimeline()` | function | 6467 | reel (`SMOKE_REEL=a` only) |
| `screeningScene` | top-level `let` | 6486, 6488, 6500 | reel |
| `creditForEntry()`, `creditForSchedule(schedule)`, `creditImage(credit)`, `entryLineCredits(entry, start)`, `entryWindow(entry)`, `reelLineCredits(schedule, lines, start)` | functions | 6486, 6488, 6500 | reel |
| `Credits.creditLine(...)` | UMD global from `src/credits.js` (script tag) | 6486 | reel |
| `window.vc.listSceneRecordings(id)`, `window.vc.listRecordings(folder)` | preload bridge | 6350, 6465 | screening, reel |

**DOM ids read or clicked by `getElementById`** (one row per id; `view-<name>` is built as
`'view-'+v` over `['activate','library','booth']` at 6022 and `['library','tableread','booth','screening']` at 6234/6296):
`btn-theme` 6014 · `view-activate` 6017, 6018, 6022, 6057 · `view-library`, `view-booth`, `view-tableread`, `view-screening` 6022, 6234, 6296, 6430 · `signin-status` 6023 · `btn-claude-tip` 6034 · `license-email` 6036 · `btn-upgrade` 6040, 6072 · `license-tier` 6070 · `usage-allowance` 6074 · `drop-overlay` 6095, 6101 · `toast` 6100, 6519 · `btn-filter` 6192, 6196 · `filter-menu` 6193 · `btn-create-pack` 6202 · `btn-add-zips`, `btn-add-zip-folder` 6207 · `wh-title` 6208 · `wh-step` 6209 · `wh-next` 6210, 6339 · `wh-back` 6212, 6368 · `btn-delete-pack` 6226 (asserted **absent**) · `btn-edit-pack` 6228 · `btn-start-booth` 6232 · `tr-stage-wrap` 6237, 6239, 6241, 6246 · `btn-tr-start` 6249 · `btn-play-ref` 6252 · `btn-record` 6257, 6291 · `rec-chip`, `rec-chip-time`, `cue-card`, `record-caption`, `waveform` 6261, 6269, 6274, 6275 · `take-score`, `score-donut`, `score-percent`, `score-band` 6274 · `score-emoji` 6274 (asserted **absent**) · `booth-status` 6276 (asserted **absent**) · `btn-play-take` 6277 · `btn-camera` 6284, 6289 · `btn-camera-state` 6289 · `cue-cam` 6290 · `btn-next-line` 6304, 6372 · `scene-score` 6313 · `scene-score-percent`, `scene-score-band`, `scene-score-takes` 6315-6317, 6329, 6417-6418 · `wh-next-home`, `wh-next-label` 6341-6342 · `stage-wrap` 6351, 6410, 6434, 6436, 6441, 6452 · `cam-card` 6354 · `cam-video-a`, `cam-video-b` 6362-6363 · `btn-home` 6383 · `scene-export-menu` 6399, 6423 · `btn-export` 6426, 6433 · `export-menu` 6429 · `btn-export-reel` 6447, 6501-6505.

**Selectors (`querySelector[All]`)**: `.view:not(.hidden)` 6204, 6214-6215 · `.buy-link.onetime-only`, `.buy-link.subscription-only` 6052-6053 · `#view-activate *` 6057 · `.browse-title` 6119, 6128, 6149, 6154, 6176, 6179, 6195, 6219 · `#filter-menu .filter-item[data-kind="soundboard"|"all"]` 6194, 6197 · `#creator-step-source .creator-card` 6205 · `#browse-list .browse-item .row-del` 6225 · `#tr-timeline li` 6235 · `#booth-progress .dot` 6302 · `#workflow-header` (via `closest`) 6289 · `#scene-score-cast li`, `.scene-cast-name`, `.scene-cast-val` 6319-6321, 6419-6421 · `#timeline .line-score` 6323 · `#scene-donut .score-donut-face` 6330 · `#voices-menu .filter-item.on` 6380 · `#scene-history-list .scene-row | .scene-date | .scene-cam | .scene-export` 6386-6394, 6407 · `#scene-export-menu .export-item` 6397 · `.script-filter` 6412 · `.mixer` 6412 (asserted **absent**).

**Body classes and CSS state the walkthrough asserts**: `body.light` 6014, `body.plan-free` 6033, 6039, 6071, `body.flag-off-subscription` 6060, `.hidden` (everywhere), `.playing` (stage wraps), `.recording` / `.scored` on `#cue-card`, `.active` on `#btn-camera`, `disabled` on `#btn-edit-pack`, `#btn-play-take`, `#btn-export-reel`, `dataset.band` on `#take-score`, CSS variable `--hot` on `body` 6274. **Events it synthesises**: `DragEvent('dragenter'|'drop')` on `window` 6094-6096; `KeyboardEvent('keydown', {key:' '})` on `window` 6244, 6247, 6439.

**Main-side functions the walkthrough calls directly** (these constrain the *main* split, not the renderer's): `libraryMode()` 6108, 6137, 6161, 6184 · `resolveScene()` 6110, 6459 · `writeTake()` 6116, 6173 · `importZipsIntoLibrary()` 6124 · `importPathsIntoLibrary()` 6150 · `queueOpen()` 6177 · `Library.scanLibrary()` 6137, 6161, 6184 · `Library.TAKES_DIR` 6148 · `ffmpegPath()` 6461 · `camPathIn()` 6462 · `probeMedia()` 6510 · `REEL_W`/`REEL_H` 6512 · `app.getPath('temp')`, `app.getVersion()` · `win.webContents.{invalidate,capturePage,executeJavaScript,on('console-message'),once('did-finish-load')}` · `app.quit()` 6533 / `app.exit(1)` 6548.

**The `SMOKE_*` log-line contract** (what CI, the skills and the research doc grep). Only five
lines are backed by a `throw`: `SMOKE_IMPORT rail rows` 6132, `SMOKE_IMPORTDIR rail rows` 6157,
`SMOKE_OPENFILE rail rows` 6181, the Make-a-scene exit 6215, and `SMOKE_REEL file` 6522; plus
the folder-copy check 6160 (throws without logging) and the scope-name check 6528. Everything
else prints a value (`docs/superpowers/research/2026-09-12-desktop-smoke-surface.md`). The
harness lines every consumer relies on: `SMOKE_SHOT <path>` 5990, `SMOKE_SCOPE stopped after
<leg>` 6003, `SMOKE_OK` 6532 (then `app.quit()`), `SMOKE_FAIL <err>` 6535 (then `app.exit(1)`,
**never** `app.quit()`), and `RENDERER: <message>` 6007 relaying the page console. Per leg:
`SMOKE_GATE` ×4, `SMOKE_PLAN` ×4, `SMOKE_FLAG` ×1, `SMOKE_DROP` ×3, `SMOKE_IMPORT` ×4 (+ the
`skipped` branch), `SMOKE_IMPORTDIR` ×2, `SMOKE_OPENFILE` ×1, `SMOKE_FILTER` ×2, `SMOKE_MAKE`
×7, `SMOKE_PACKACTIONS` ×3 (home); `SMOKE_TR` ×4 (tableread); `SMOKE_REC` ×6, `SMOKE_CAM` ×3
(booth); `SMOKE_VIEW` ×2, `SMOKE_SCORE` ×6, `SMOKE_SCENES` ×1, `SMOKE_CAM` ×3 (screening);
`SMOKE_NAV` ×3, `SMOKE_VIEW` ×1, `SMOKE_SCENES` ×13, `SMOKE_CAM` ×1 (history); `SMOKE_REEL`
×5 (+ `cam timeline entries` under `=a`, `toast` on failure), `SMOKE_CREDIT` ×3 (reel).
Consumers: `.github/workflows/ci.yml` (`SMOKE_SCOPE=home`, exit code via `xvfb-run`,
screenshots from `SMOKE_DIR` on failure), `.claude/skills/implementing-changes/SKILL.md`,
`cutting-a-release`, `docs/RELEASING.md` §7 (packaged smoke), `build/CLAUDE.md` (`SMOKE_CREDIT
line:` / `strip bytes:`), `CLAUDE.md` (the `SMOKE_TR view: tableread` stale-frame note).
Screenshot names are part of it too: `0-signin-gate`, `1-library`, `1b-makescene`, `2-casting`,
`2b-tableread`, `3-booth`, `3b-booth-playing`, `3c-booth-recording`, `3d-booth-recorded`,
`3e-booth-camera`, `4-screening`, `5-playing`, `6-scene-history`, `7-scene-playback`,
`8-reel-exported`, and the reel lands at `<SMOKE_DIR>/smoke-reel.mp4` (5281, 6449).

### 2f. Other externally visible behaviour

- Window: title `Bad Takes` packaged, `Bad Takes - <branch>` / `Bad Takes - SMOKE TEST - <branch>` unpackaged (5916-5926); size from the work area capped 1480×1040, min 900×620, `#12101c`; `show: !SMOKE`, `paintWhenInitiallyHidden: true`; `page-title-updated` suppressed; external links to the system browser; `will-navigate` blocked.
- Permissions: only `media` and `clipboard-sanitized-write` (6667-6669). macOS mic prompt at boot (6671-6673, not under smoke); camera prompt lazily via `media:askCamera`.
- Single instance (Windows/Linux argv + `second-instance`; macOS `open-file`/`open-url`), and `app.setAsDefaultProtocolClient('badtakes')` packaged only (6565).
- Boot order (6555-6700): lock check → protocol client → `libraryMode()` + `packsRoot` → layout-2 migration (tree mode, not smoke) → `await initLicense()` → dev dock icon → `pack://` handler → permission handler → mic prompt → `registerIpc()` → `createWindow()` → `track('app_open')` → update check at +3s and every 6h → `activate`.
- Quit: `before-quit` → `finishRecording(null)` + `flushAnalytics()`; `will-quit` → drop every creator session and `rm -rf creatorRoot()`.

## 3. Responsibility map

Ranges include the banner comment that introduces each block. "EF" = Electron-free (Node only,
could be unit-tested outside Electron); "EB" = Electron-bound (`app`, `BrowserWindow`,
`dialog`, `shell`, `protocol`, `session`, `systemPreferences`, `webContents`, `electron-updater`,
or `process.getSystemVersion`).

### Constants and config
| Lines | What | |
| --- | --- | --- |
| 1-72 | 35 `require`s: `electron` (8 names), `path`, `fs`, `child_process`, `os`, `http`, and 27 `src/` modules | |
| 74 | `SMOKE` | EF |
| 201-203 | `pack://` scheme privileges | EB, must run before `ready` |
| 336-338 | `UNLIMITED_QUOTA` | EF |
| 520, 608, 645, 648 | `REVALIDATE_INTERVAL_MS`, `LICENSE_VERDICTS`, `FLAGS_INTERVAL_MS`, `FLAGS_FOCUS_MIN_MS` | EF |
| 898 | `ANNOUNCE_ASSET_RE` | EF |
| 1358-1359 | `ANALYTICS_FLUSH_MS`, `ANALYTICS_BATCH` | EF |
| 1536 | `RELEASES_PAGE` | EF |
| 1952 | `SCORE_LOG_MAX` | EF |
| 2017-2021 | `ARCHIVES` (`.take` + `.zip` opener injected into `src/import.js`) | EF |
| 2450 | `COLLAB_MEMBER_RE` | EF |
| 2886 | `PLAYABLE_EXTS` | EF |
| 4335-4340, 4473 | collab poll cadence, `CATALOG_REFRESH_MS` (inside `registerIpc`) | EF |
| 6610-6615 | `MIME` (inside `whenReady`) | EF |

### State (module-level `let`/`Map`, and the closures over it)
| Lines | Binding | Owner today | Read outside its block |
| --- | --- | --- | --- |
| 107-108, 153 | `pendingOpens`, `openTarget`, `pendingInvites` | open queue | `openTarget` set at 4844 (`packs:openReady`), cleared at 5968-5970 (`createWindow`) |
| 306-309 | `devFree` | simulated meter | `simulatedUsage` 1105 |
| 330 | `activeLicenseCfg` | `licenseCfg` | — |
| 340 | `licenseStatus` | `setLicenseStatus` | **20 read sites**: 1035, 2959, 3061, 3070, 3076, 3088, 3123, 3128, 3187, 3215, 3245, 3247, 4349, 4773, 6682-6684, plus every `{ ...licenseStatus }` spread |
| 357 | `machineIdPromise` | `machineId` | — |
| 627 | `licenseVerdictRecheck` | `noteLicenseVerdict` | — |
| 649, 744, 755 | `flagsFocusAt`, `announceFocusAt`, `lastRunVersion` | `initLicense` | `lastRunVersion` read by `refreshAnnouncements` 827 |
| 1054-1057, 1096, 1103, 1199 | `devShares`, `usageTotals`, `devTakesRecorded`, `openRecording` | usage / activity | `usageTotals` 3070; `openRecording` mutated at 4859-4865, 4954, 5137 |
| 1361-1365 | analytics queue/identity/cfg/timer/flushing | analytics | — |
| 1559-1585 | `updateStatus`, `updateCheckInFlight`, `updaterWired`, `updatePhase`, `lastUpdateFailure`, `stagedVersion` | updater | `updateStatus` 4613 |
| 1823 | `oauthServer` | `oauthLogin` | — |
| 2368 | `packsRoot` | boot (6568) | `allowedRoots` 2374, `packs:scan` 4773 |
| 2552 | `ytdlpFetch` | tools | 5483-5505 |
| 2739 | `transcodeJobs` | `transcodeVideo` | — |
| 2870-2871, 2939 | `creatorSessions`, `creatorSeq`, `audioCodecCache` | creator | 5600, 5629, 5651, 5706, 5730, 5761, 5876-5904, 6722 |
| 3571-3572 (nested) | `collabUploads`, `collabUploadAborts` | upload | `collab:status` 3527 |
| 4341-4344, 4435 (nested) | `collabSessions`, `collabInfoSnaps`, `collabInfoAt`, `collabPollStarted`, `collabInfoChain` | poller | `collabSessions` written 3714 (`adoptHostedRoom`), 4311 (`collab:mine`); read 3368 (`hostedRoomInPlay`), 5584 (`creator:editStage`) |
| 4471-4472 (nested) | `catalogInstalls`, `catalogFetchedAt` | catalog | — |
| 5268-5269 (nested) | `reelSessions`, `reelSeq` | reel | — |

### Pure utilities (Electron-free today, no state)
| Lines | Functions |
| --- | --- |
| 42-50 | `activeSceneLines` (wraps `src/takes.lineActive`) |
| 112-117, 155-160 | `takesInArgv`, `invitesInArgv` |
| 302-305 | `seedDevBucket` |
| 392-402 | `runtimeName` |
| 937-966, 1221-1238 | `normalizeQuota`, `sceneBucket` (one of the three shared copies, see §2d), `quotaField`, `quotaRefusalMessage` |
| 1487-1513 | `sceneEventProps` |
| 1939 | `round3` |
| 2086-2088 | `stripDiagnostics` |
| 2403-2405 | `mediaUrl` |
| 2437-2443 | `recordingPathIn`, `camPathIn` |
| 2945 | `str` |
| 3421-3423 | `collabProgress` (takes a sender) |
| 4737 | `safeVersionId` |

### Services and IO
| Lines | Subsystem | Functions | |
| --- | --- | --- | --- |
| 101-199 | `.take` / `badtakes://` open queue and single-instance | `focusMainWindow`, `flushOpens`, `queueOpen`, `flushInvites`, `queueInvite`, the `app.on` trio | EB |
| 205-351 | licence store, bypass, config, status | `loadLicenseRecord`, `saveLicenseRecord`, `clearLicenseRecord`, `licenseBypass`, `bypassPlan`, `licenseCfg`, `setLicenseStatus` | EB (`app.isPackaged`, `app.getPath`, `BrowserWindow`) |
| 282-328 | simulated free meter | `simulatedFreeQuota` (dynamic-imports `server/_shared/scenequota.mjs`) | EF |
| 353-447 | seat identity + Supabase client | `machineId`, `osVersion`, `resolveMachineId`, `execFileOut`, `supaFetch` | EB (`process.getSystemVersion`, `app.getPath`) |
| 449-633 | activation and heartbeat | `activateWithAccessToken`, `refreshLicense`, `noteLicenseVerdict` | EB (`app.getVersion`) |
| 635-727 | feature flags | `flagValues`, `refreshFlags` | EB (`app.isPackaged`) |
| 729-918 | announcements | `takeLastRunVersion`, `announceOverride`, `announceEnabled`, `announceQueue`, `refreshAnnouncements`, `announceSeenFile`, `loadAnnounceSeen`, `saveAnnounceSeen`, `announceBucketUrl`, `announceAsset` | EB (`app.isPackaged`, `app.getVersion`) |
| 920-1039 | the scene meter | `askSceneQuota` | EB via status push |
| 1041-1238 | share/usage/activity pipes | `recordShare`, `recordUsage`, `recordActivity`, `sceneDetailFor`, `finishRecording` | EF except the status push |
| 1240-1340 | licence boot | `initLicense` (schedules the three refreshes, two `browser-window-focus` throttles) | EB |
| 1342-1513 | product analytics | `analyticsCfg`, `analyticsEnabled`, `analyticsClientId`, `scheduleAnalyticsFlush`, `track`, `flushAnalytics` | EB (`app.isPackaged`, `app.getVersion`) |
| 1515-1797 | auto-update | `setUpdateStatus`, `autoRestartEnabled`, `updatesUsable`, `needsSquirrelStaging`, `markUpdateStaged`, `noteUpdateFailure`, `updater`, `checkForUpdates`, `actOnUpdate` | EB (`electron-updater`, `shell`, `BrowserWindow`) |
| 1800-1902 | OAuth loopback + email OTP | `oauthCallbackServer`, `oauthResultPage`, `oauthLogin`, `otpStart`, `otpVerify` | server + page EF; `oauthLogin` EB (`shell`) |
| 1904-1919 | settings.json | `loadSettings`, `saveSettings` | EB (path only) |
| 1921-1985 | score log | `scoreDifficulty`, `appendScore`, `readScores` | EF given the path |
| 1986-2008 | link-import gate, library mode | `linkImportEnabled`, `libraryMode` | EB (`app.getPath`) |
| 2010-2205 | scene resolution and installers | `resolveScene`, `importZipsIntoLibrary`, `importPathsIntoLibrary`, `trackImported`, `trackImportFailure`, `countSceneTakes` | EF given the root; `trackImport*` reach analytics |
| 2207-2366 | the creator's write path | `installNewScene`, `carryExistingBed`, `saveSceneEdit` | EF given the root |
| 2368-2401 | `pack://` roots | `allowedRoots`, `announceRoot`, `catalogRoot`, `collabThumbRoot` | EB (`app.getPath`) |
| 2407-2547 | renderer shapes of a pack and its takes | `packForRenderer`, `camSourceDir`, `listRecordings`, `listSceneRecordings` | EF |
| 2549-2711 | tool resolution | `managedYtdlpPath`, `toolPath`, `maybeUpdateManagedYtdlp`, `ffmpegPath`, `separatorPaths`, `ffprobePath`, `demucsPath`, `demucsModelPath` | EB (`process.resourcesPath`, `app.isPackaged`) |
| 2622-2693 | the background bed | `applyGeneratedBed`, `isolateBackground` | EF |
| 2713-2795 | video: direct file, transcode cache | `directVideoFile`, `transcodeVideo`, `sceneVideo`, `dropVideoCache` | EF given roots |
| 2797-2860 | branded export | `brandAssetForFfmpeg` (uses `__dirname`), `probeMedia`, `brandedExport` | EB (`app.getPath('temp')`) |
| 2862-2956 | creator sessions | `dropCreatorSession`, `probeSource`, `finishCreatorStage`, `newCreatorSession`, `pickAudioCodec`, `creatorSpecFromRenderer` | EF given the root |
| 3289-3510 (nested) | collab client, pins, transfer | `collabFetch`, `readCollabPins`, `hostedRoomInPlay`, `collabPinFor`, `writeCollabPin`, `readPullLedger`, `writePullLedger`, `uploadFileTo`, `downloadFileTo` | EF (fetch + fs) |
| 3566-3731 (nested) | scene upload for a room | `abortCollabUpload`, `uploadCollabThumb`, `uploadSceneForCollab`, `adoptHostedRoom` | EB (`app.getPath('temp')`) |
| 4326-4458 (nested) | collab polling | `collabPollAllowed`, `collabPushChanged`, `pollCollabMine`, `absorbCollabInfo`, `pollCollabInfosOnce`, `pollCollabInfos`, `pokeCollabPoll`, `startCollabPolling` | EB (`BrowserWindow`) |
| 4460-4534 (nested) | catalog cache | `catalogBucketUrl`, `dropStaleCatalogThumbs`, `refreshCatalogIndex`, `recordCatalogDownload` | EF |
| 4725-4737 (nested) | version guards | `sceneForVersions`, `safeVersionId` | EF |
| 5262-5276 (nested) | reel sessions | `dropReelSession` | EF |
| 5524-5547, 5612-5639 (nested) | creator staging | `stageCreatorSource`, `stageEditSource` | EF |
| 5916-5978, 6552-6553 | window | `windowTitle`, `createWindow` (minus smoke) | EB |
| 6604-6659 | `pack://` handler | inline in `whenReady` | EB (`protocol`) |

### IPC handler groups (all inside `registerIpc`, 2958-5910)
| Lines | Group | Count |
| --- | --- | --- |
| 2959-3129 | licence + flags + announcements + profile prelude | 13 (`license:*` 8, `flags:*` 2, `announce:*` 3) |
| 3131-3287 | profile | 3 |
| 3289-4324 | collab (with 16 nested helpers) | 18 |
| 4326-4458 | collab polling (0 handlers, called from `packs:openReady`) | 0 |
| 4460-4610 | catalog | 4 |
| 4612-4617 | updates | 3 |
| 4619-4851 | packs, versions, pickers, openReady | 11 |
| 4853-4871 | activity | 2 |
| 4873-5139 | takes, scoring, settings, analytics relay, camera, scene history | 13 |
| 5141-5260 | video | 2 |
| 5262-5407 | reel | 4 |
| 5409-5421 | mix export | 1 |
| 5423-5909 | creator | 11 |

### The smoke walkthrough
| Lines | Leg |
| --- | --- |
| 5979-6011 | harness: `outDir`, `sleep`, `shot`, `js`, `SMOKE_LEGS`, `smokeScope`, `endOfScope`, console relay, `did-finish-load` |
| 6012-6229 | home (theme, gate, plan rows, flag, drop, `.take` import, folder import, file-open, filter, Make-a-scene page, casting, pack actions) |
| 6230-6248 | tableread |
| 6249-6297 | booth (record, score card, camera, `visibleView` helper at 6295) |
| 6298-6364 | screening |
| 6365-6442 | history |
| 6443-6525 | reel |
| 6526-6551 | scope validation, `walk()`, `SMOKE_OK`/`app.quit()`, `SMOKE_FAIL`/`app.exit(1)` |

### Orchestration and lifecycle
| Lines | |
| --- | --- |
| 74-99 | `--smoke` command-line switches and userData redirect (before `ready`) |
| 179-199 | single-instance lock and the three OS-open handlers (before `ready`) |
| 2958, 5910 | `registerIpc()` frame |
| 6555-6700 | `app.whenReady`: boot order in §2f |
| 6702-6724 | `window-all-closed`, `before-quit`, `will-quit` |

## 4. Proposed tree

### Where main-process modules live

A new top-level directory **`main/`**, beside `main.js`, with the handler groups under
`main/ipc/` and the walkthrough under `main/smoke/`. Reasons, and the alternatives considered:

- `src/` is off limits by rule (plain Node, no Electron imports; `web/` loads fourteen of its
  modules by script tag and the kit stages others). Everything in `main/` may `require('electron')`.
- `main/` beside `main.js` mirrors `renderer/` beside `preload.js`: the entry file and its
  directory share a name, so a reader lands in the right place. Node resolves `require('./main')`
  to the *file* `main.js` before the directory, so the name is unambiguous for anything that
  ever wanted the entry (nothing does), and every module is addressed as `./main/<name>`.
  Alternatives: `desktop/` (says platform, not process) or `electron/` (collides in the reader's
  head with `require('electron')`). Either works if Eric prefers; nothing below depends on the name
  except the `build.files` line and `test/collabthumb.test.js`'s glob.
- The directory **must not** be named `server` or `site`: the asar leak check greps for
  `(^|/)(server|site)/` (§7).
- **Electron-free pieces that earn a unit test move to `src/`** (four new modules below, plus
  one function into `src/library.js`). Everything else that is Node-only but exists to wire
  Electron state together (the meter, usage, the write path, transfer of collab state) stays in
  `main/`: moving it would mean injecting `app.getPath` and the status push into a dozen signatures
  for no test we would write.

Two conventions every `main/` module follows, because they are what the risks in §10 turn on:

1. **No work at load time.** A module exports functions and `Map`s; it never calls
   `app.getPath`, registers an `app.on` handler, or reads `settings.json` while being required.
   Registration happens in an exported `register(ipcMain)` / `start()` / `wire()` that
   `main.js` calls at the same moment the code runs today.
2. **A module never exports a `let`.** State that other modules read is exposed through an
   accessor (`License.status()`, `Updates.status()`, `Usage.totals()`, `CollabClient.sessions()`,
   `Protocol.packsRoot()`), and state that only its owner touches stays private. Destructuring a
   `let` out of `module.exports` copies the value at require time and is the bug this rule exists
   to make impossible.

Handlers live in two places by one rule: a module that owns a subsystem end to end keeps its
own `ipcMain.handle` calls (announcements, usage/activity, updates, tools, catalog, media); a
handler group that only orchestrates several services lives under `main/ipc/`. Every module
with handlers exports `register(ipcMain)`; `main.js`'s `registerIpc()` becomes the ordered list
of those calls.

### The files

Sizes are the measured spans plus their banner comments plus ~10 lines of requires/exports.
Marks: **EF→src** moves to `src/` with a test; **EF** Electron-free but stays in `main/`
(wiring); **EB** Electron-bound.

#### `main.js` — entry and orchestrator (≈170) — §5

#### `main/env.js` — `SMOKE` (≈12, EF)
`const SMOKE = process.argv.includes('--smoke')` (74). Required by every module that gates on it
(§2c lists the 28 sites). The `if (SMOKE) { … }` switch block (75-99) stays in `main.js`: it must run
before `ready`, and it is the one place `app.setPath('userData')` is changed.

#### `main/paths.js` — every userData location (≈60, EB)
`licenseFile` 222 · `analyticsFile` 1357 · `settingsFile`, `recordingsRoot`, `sceneRecordingsRoot`,
`videoCacheRoot` 1904-1907 · `scoreLogFile` 1937 · `announceRoot` 2379-2386 · `catalogRoot` 2388-2392 ·
`collabThumbRoot` 2394-2401 · `creatorRoot` 2873-2875 · `announceSeenFile` 851-856 ·
`catalogIndexFile`, `catalogEtagFile` 4469-4470. All are functions of `app.getPath('userData')`
evaluated at call time, never at load (the smoke redirect at 98 must come first).

#### `src/scenequota.js` — the quota shapes (≈60, **EF→src**, new)
`UNLIMITED_QUOTA` 336-338 · `normalizeQuota` 920-949 · `sceneBucket` 951-959 · `quotaField`
961-966 · `quotaRefusalMessage` 1221-1238. Plain CommonJS, main-only (no `web/` consumer, so no
UMD). **`sceneBucket` is one of the three shared copies** (SQL `private.scene_bucket()`,
`_shared/scenequota.mjs`, and this one): the move deletes the `main.js` copy in the same commit,
so the count stays three, and `test/scenequota.test.js` asserts parity against
`server/supabase/functions/_shared/scenequota.mjs` by dynamic import (skipping when `server/` is
absent, exactly the guard `simulatedFreeQuota` already uses). `admin/README.md:101` and
`server/CLAUDE.md:241` name `main.js` as the app's copy and get updated in the same PR.

#### `src/scorelog.js` — the append-only score log (≈65, **EF→src**, new)
`round3` 1939 · `SCORE_LOG_MAX` 1949-1952 · `appendScore(file, record)` 1954-1960 ·
`readScores(file)` 1962-1985, with the file path a parameter (callers pass
`Paths.scoreLogFile()`). Test: appends one line per record, skips torn/corrupt lines, trims to
the newest `SCORE_LOG_MAX` and rewrites, `round3` on non-finite.

#### `src/packurl.js` — the `pack://media/<base64url>` pair (≈20, **EF→src**, new)
`mediaUrl` 2403-2405 and its inverse, the two lines at 6621-6622 (`pathname → path.resolve(Buffer.from(…, 'base64url'))`),
so encoder and decoder sit together and a round-trip test pins them (ASCII, spaces, non-ASCII,
Windows drive paths). Required by eight `main/` modules and by `src/takelist.js`. *Optional
extension, Eric's call:* the Range arithmetic at 6627-6646 (`bytes=a-b`, `bytes=a-`, `bytes=-n`,
416) and the `MIME` table 6610-6615 could move here as `planRange(header, size)` — it is the one
piece of the protocol worth a table-driven test — but that changes the handler's shape rather than
moving it, so it is not part of the pure-move plan.

#### `src/takelist.js` — takes and scene mixes as the renderer sees them (≈110, **EF→src**, new)
`recordingPathIn` 2433-2439 · `camPathIn` 2441-2443 · `listRecordings(dir)` 2458-2492 ·
`listSceneRecordings(dir)` 2494-2547. Requires `src/takes.js` (`sanitizeTakeMeta`) and
`src/packurl.js`. Test against a temp directory: orphan `.cam.webm` ignored, cam URL merged onto
its take, a vanished cam clip drops that entry, `size` sums clips, `window`/`lines`/`signature`
pass through, newest first. `collab:takes` (4155) and `collab:push` (4006) call `listRecordings`
too, so this is shape three consumers share.

#### `src/library.js` gains `countTakes(sceneDir)` (+32, **EF→src**)
`countSceneTakes` 2167-2205 verbatim; it only uses `fs`, `Library.lineTakesDir`, `Library.sceneTakesDir`
and the inode dedupe. Test in `test/library.test.js`: hardlinked takes across two versions count once.

#### `src/transfer.js` — streamed upload and download (≈85, **EF→src**, new)
`uploadFileTo` 3425-3470 · `downloadFileTo` 3472-3502, out of `registerIpc`'s closure. Requires
`src/collab.js` (`storageErrorMessage`). Used by collab (upload, push) and by `catalog:install`
(4577), which today reaches into the collab section for it. Test with a local `http.createServer`:
`content-length` set on the PUT, one `onPct` per whole percent, `AbortSignal` destroys the read
stream, download writes `.part` then renames, a non-2xx names the body.

#### `main/license-store.js` — license.json, bypass, config, status (≈150, EB)
Banner 205-220 · `loadLicenseRecord` 224-230 · `saveLicenseRecord` 231-234 · `clearLicenseRecord`
235-237 · `licenseBypass` 239-255 · `bypassPlan` 257-274 (+ the `isUnlimited` note 276-280) ·
`activeLicenseCfg`, `licenseCfg` 330-334 · `licenseStatus` 340 (private; exported as `status()`) ·
`setLicenseStatus` 342-351 · `flagValues` 651-672 with its comment 635-644. Requires `env`, `paths`,
`src/flags`, `src/license-config`, `src/scenequota` (`UNLIMITED_QUOTA` for the initial shape — or
keep 338's literal here; either way one definition).

#### `main/supa.js` — the Supabase client and the seat it reports (≈100, EB)
`machineIdPromise`, `machineId` 353-361 · `osVersion` 363-390 · `runtimeName` 392-402 ·
`execFileOut` 403-405 · `resolveMachineId` 406-430 · `supaFetch` 432-447. Requires `license-store`,
`src/activation`.

#### `main/license-session.js` — sign-in: activation, OAuth loopback, email OTP (≈175, EB)
`activateWithAccessToken` 449-513 · OAuth banner 1800-1803 · `oauthCallbackServer` 1805-1815 ·
`oauthResultPage` 1817-1821 · `oauthServer` 1823 · `oauthLogin` 1824-1868 · `otpStart` 1870-1887 ·
`otpVerify` 1888-1902. Requires `supa`, `license-store`, `license-refresh` (`refreshFlags`),
`analytics` (`track`), `src/activation`, `src/license-config`, `src/scenequota`.

#### `main/license-refresh.js` — the heartbeats and licence boot (≈300, EB) — §6
`REVALIDATE_INTERVAL_MS` 515-520 · `refreshLicense` 521-603 · `LICENSE_VERDICTS` 605-608 ·
`licenseVerdictRecheck`, `noteLicenseVerdict` 610-633 · `FLAGS_INTERVAL_MS`, `FLAGS_FOCUS_MIN_MS`,
`flagsFocusAt` 645-649 · `refreshFlags` 674-727 · `initLicense` 1240-1340 (its announcement half,
1322-1340, becomes a call to `Announcements.start()`). Requires `license-store`, `supa`,
`announcements`, `src/activation`, `src/scenequota`.

#### `main/announcements.js` — the whole announcement subsystem (≈275, EB) — §6
Banner 729-755 · `announceFocusAt` 744 · `lastRunVersion` 755 · `takeLastRunVersion` 757-769 ·
`announceOverride` 771-789 · `announceEnabled` 791-800 · `announceQueue` 802-809 ·
`refreshAnnouncements` 811-849 · `loadAnnounceSeen` 858-878 · `saveAnnounceSeen` 880-887 ·
`announceBucketUrl` 889-891 · `ANNOUNCE_ASSET_RE` 893-898 · `announceAsset` 900-918 · `start()` =
1322-1340 · `register(ipcMain)` = `announce:list` 2962-2981, `announce:record` 2983-3018,
`announce:act` 3020-3031. Requires `env`, `paths`, `license-store`, `supa`, `src/announce`,
`src/sanitize`, `src/packurl`, `electron.shell`.

#### `main/meter.js` — the scene meter (≈120, EF)
Comments 282-300 · `seedDevBucket` 302-305 · `devFree` 306-309 · `simulatedFreeQuota` 311-328 ·
banner 920-936 · `askSceneQuota` 968-1039. Requires `license-store`, `supa`, `usage`
(`sceneDetailFor`), `src/activation` (`isUnlimited`), `src/scenequota`. Exports `devFree` for
`usage.simulatedUsage` (or `usage` re-reads it through `Meter.devBuildUsed()`).

#### `main/usage.js` — share tallies, the usage report, the operator's log (≈215, EF)
Banner 1041-1053 · `devShares` 1054-1057 · `recordShare` 1059-1086 · banner 1088-1102 ·
`usageTotals` 1096 (exported as `totals()`) · `devTakesRecorded` 1103 · `simulatedUsage` 1104-1109 ·
`recordUsage` 1111-1142 · banner 1144-1152 · `recordActivity` 1153-1166 · `sceneDetailFor` 1168-1188 ·
`openRecording` 1190-1199 · `finishRecording` 1201-1219 · `register(ipcMain)` =
`activity:recordingStart` 4853-4867, `activity:recordingFinish` 4869-4871. Two one-line accessors
replace the direct mutations at 4954 and 5137: `noteLineRecorded(recordingId, lineId)` and
`noteLineScrapped(lineId)`. Requires `license-store`, `supa`, `scenes` (`resolveScene`), `src/packs`,
`src/scenestats`.

#### `main/analytics.js` — product analytics (≈175, EB)
Banner 1342-1355 · constants 1358-1359 · state 1361-1365 · `analyticsCfg` 1367-1372 ·
`analyticsEnabled` 1374-1393 · `analyticsClientId` 1395-1415 · `scheduleAnalyticsFlush` 1417-1428 ·
`track` 1430-1439 · `flushAnalytics` 1441-1485 · `sceneEventProps` 1487-1513 (EF; could join
`src/scenestats.js` later — optional). Requires `env`, `paths`, `license-store`, `supa`, `settings`,
`src/analytics`, `src/analytics-config`, `src/scenestats`.

#### `main/updates.js` — auto-update, whole (≈300, EB) — §6
Banner 1515-1535 · `RELEASES_PAGE` 1536 · state and its comment 1538-1585 · `setUpdateStatus`
1587-1598 · `autoRestartEnabled` 1600-1617 · `updatesUsable` 1619-1621 · `needsSquirrelStaging`
1623-1627 · `markUpdateStaged` 1629-1655 · `noteUpdateFailure` 1657-1682 · `updater` 1684-1749 ·
`checkForUpdates` 1751-1774 · `actOnUpdate` 1776-1797 · `register(ipcMain)` = `updates:check`,
`updates:status`, `updates:open` 4612-4617 · `schedule()` = the two timers 6688-6695. Exports
`status()`. Requires `env`, `settings`, `analytics`, `src/updates`, `electron-updater` (lazily, as today).

#### `main/settings.js` — settings.json and its readers (≈50, EB)
`loadSettings` 1909-1915 · `saveSettings` 1916-1919 · `scoreDifficulty` 1941-1947 · `linkImportEnabled`
1986-1998. Requires `paths`, `src/score`. No handlers here (they would pull `analytics` in and close a
cycle, §10); `settings:get`/`settings:set` live in `main/ipc/app.js`.

#### `main/scenes.js` — the library as main sees it (≈110, EB)
`libraryMode` 2000-2008 · `resolveScene` 2023-2053 · `packForRenderer` 2407-2431 · `COLLAB_MEMBER_RE`
2445-2450 · `camSourceDir` 2451-2456. Re-exports nothing; callers take `recordingPathIn`/`camPathIn`/
`listRecordings`/`listSceneRecordings` from `src/takelist.js` and `countTakes` from `src/library.js`.
Requires `paths`, `src/library`, `src/packurl`, `src/takes`.

#### `main/imports.js` — the three install paths and the OS open queue (≈245, EB)
Banner 101-110 · `pendingOpens`, `openTarget` 107-108 · `takesInArgv` 112-117 · `focusMainWindow`
119-125 · `flushOpens` 127-140 · `queueOpen` 142-145 · banner 147-153 · `pendingInvites` 153 ·
`invitesInArgv` 155-160 · `flushInvites` 162-168 · `queueInvite` 170-173 · `wireInstance()` = the
three `app.on` registrations 183-199 · `setOpenTarget(wc)` / `clearOpenTarget(wc)` for 4844 and
5968-5970 · `ARCHIVES` 2010-2021 · `importZipsIntoLibrary` 2055-2067 · `importPathsIntoLibrary`
2069-2078 · `stripDiagnostics` 2080-2088 · `trackImported` 2090-2131 · `trackImportFailure`
2133-2165. Requires `license-store`, `supa`, `analytics`, `usage`, `scenes`, `src/import`, `src/zip`,
`src/takefile` (`readTake` for `ARCHIVES`), `src/packs`, `src/scenestats`, `src/collab`
(`parseInviteUrl`), `electron` (`app`, `BrowserWindow`).

#### `main/protocol.js` — `pack://` (≈90, EB)
`registerPrivileged()` = 201-203 (called from `main.js` at load) · `packsRoot` 2368 with
`packsRoot()` / `setPacksRoot(root)` (6568) · `allowedRoots` 2370-2377 · `MIME` 6604-6615 ·
`install()` = `protocol.handle('pack', …)` 6616-6659, decoding through `src/packurl`. Requires
`paths`, `src/allowlist`, `src/packurl`, `electron.protocol`.

#### `main/tools.js` — ffmpeg, ffprobe, yt-dlp, demucs resolution and the tools probe (≈195, EB)
`ytdlpFetch` 2549-2552 · `managedYtdlpPath` 2554-2561 · `toolPath` 2563-2570 · `maybeUpdateManagedYtdlp`
2572-2602 · `ffmpegPath` 2604-2606 · `ffprobePath` 2695-2697 · `demucsPath` 2699-2706 · `demucsModelPath`
2708-2711 · `register(ipcMain)` = `creator:tools` 5423-5473, `creator:fetchYtdlp` 5475-5502,
`creator:cancelYtdlpFetch` 5504-5507. Requires `env`, `settings`, `src/tools`, `src/ytdlp`,
`src/createpack` (`runTool`, `demucsToolCandidates`, `creatorHome`, `DEMUCS_MODELS`), `electron.app`.

#### `main/media.js` — playable video, the transcode cache, branding (≈165, EB)
`directVideoFile` 2713-2719 · `transcodeJobs`, `transcodeVideo` 2721-2767 · `sceneVideo` 2769-2779 ·
`dropVideoCache` 2781-2795 · banner 2797-2803 · `brandAssetForFfmpeg` 2804-2816 (**`__dirname` →
`path.join(__dirname, '..', 'brand', name)`**) · `probeMedia` 2818-2847 · `brandedExport` 2849-2860 ·
`register(ipcMain)` = `video:prepare` 5141-5148. Requires `paths`, `scenes`, `tools`, `src/brand`,
`src/library`, `src/packurl`, `electron.app`.

#### `main/creator-stage.js` — creator sessions and staging (≈290, EB) — §6
Banner 2862-2868 · `creatorSessions`, `creatorSeq` 2870-2871 (private; `get`, `has`, `drop`, `dropAll`
exported) · `dropCreatorSession` 2877-2883 · `PLAYABLE_EXTS` 2885-2886 · `probeSource` 2888 ·
`finishCreatorStage` 2890-2927 · `newCreatorSession` 2929-2936 · `audioCodecCache`, `pickAudioCodec`
2938-2943 · `creatorSpecFromRenderer` 2947-2956 · `register(ipcMain)` = `creator:pickSource` 5509-5518,
`creator:stage` 5520-5522, `creator:editStage` 5549-5602, `creator:discard` 5865-5868 ·
`stageCreatorSource` 5524-5547 · `stageEditSource` 5604-5639 · `dropAll()` = 6722-6723 (`will-quit`).
Requires `paths`, `tools`, `collab-client` (the edit-blocking check at 5579-5595), `src/packurl`,
`src/createpack`, `src/creator`, `src/packs`, `src/collab`, `electron` (`dialog`, `BrowserWindow`).

#### `main/creator-link.js` — link import (≈95, EB)
`str` 2945 · `register(ipcMain)` = `creator:fetchLink` 5641-5727. Requires `paths` (the debug dump),
`tools`, `creator-stage`, `settings`, `src/creator`, `src/createpack`.

#### `main/creator-bed.js` — the background bed (≈150, EF)
`separatorPaths` 2608-2620 · `applyGeneratedBed` 2622-2642 · `isolateBackground` 2644-2693 ·
`carryExistingBed` 2264-2274 · `register(ipcMain)` = `creator:trackGenerate` 5870-5898,
`creator:trackCancel` 5900-5909. Requires `tools`, `creator-stage`, `src/separate`, `src/createpack`,
`src/packurl`.

#### `main/creator-write.js` — creating a scene, saving an edit (≈150, EF)
Banner 2207-2218 · `installNewScene` 2219-2262 · `saveSceneEdit` 2276-2366. Requires `scenes`
(`libraryMode`), `creator-bed` (`applyGeneratedBed`, `carryExistingBed`), `src/createpack`,
`src/library`, `src/creator`, `src/packs`.

#### `main/ipc/creator.js` — `creator:create` (≈145, EB)
The one handler 5729-5863 and its registration. Requires `creator-stage`, `creator-write`, `tools`,
`meter`, `analytics`, `media` (`dropVideoCache`), `scenes`, `src/scenequota`, `src/createpack`,
`src/creator`, `src/packs`.

#### `main/collab-client.js` — the collab wire, pins, ledger, session list (≈150, EF)
Banner 3289-3295 · `collabFlagOn` 3296 · `collabFetch` 3298-3324 · `readCollabPins` 3326-3346 ·
`hostedRoomInPlay` 3348-3370 · `collabPinFor` 3372-3386 · `writeCollabPin` 3388-3404 ·
`readPullLedger`, `writePullLedger` 3406-3419 · `collabProgress` 3421-3423 · `collabSessions` 4341
(private; `sessions()` / `setSessions(list)` for the writes at 3714 and 4311 and the reads at 3368,
4364-4407, 5584). Requires `license-store`, `supa`, `license-refresh` (`noteLicenseVerdict`), `scenes`,
`src/library`, `src/collab`, `electron.app` (`getVersion`).

#### `main/collab-upload.js` — putting a scene into a room (≈170, EB)
Comment 3566-3570 · `collabUploads`, `collabUploadAborts` 3571-3572 (private; `uploadState(sceneId)`,
`clearUpload(sceneId)` for 3527 and 3841) · `abortCollabUpload` 3574-3581 · `uploadCollabThumb`
3583-3628 · `uploadSceneForCollab` 3630-3700 · `adoptHostedRoom` 3702-3731. Requires `paths`,
`collab-client`, `scenes` (`COLLAB_MEMBER_RE`), `src/transfer`, `src/zip`, `src/library`, `src/collab`,
`electron.app` (`getPath('temp')`).

#### `main/collab-poll.js` — main-owned polling and the `collab:changed` push (≈140, EB)
Banner 4326-4334 · constants 4335-4340 · `collabInfoSnaps`, `collabInfoAt`, `collabPollStarted`
4342-4344 · `collabPollAllowed` 4346-4349 · `collabPushChanged` 4351-4356 · `pollCollabMine` 4358-4370 ·
`absorbCollabInfo` 4372-4395 · `pollCollabInfosOnce` 4397-4426 · `collabInfoChain`, `pollCollabInfos`
4428-4440 · `pokeCollabPoll` 4442-4447 · `startCollabPolling` 4449-4458. Requires `env`, `license-store`,
`collab-client`, `src/collab`, `electron.BrowserWindow`.

#### `main/ipc/collab-host.js` — the host's verbs (≈235, EB)
`activeSceneLines` 42-50 · `collab:create` 3733-3850 · `collab:update` 4191-4232 · `collab:start`
4253-4277 · `collab:wrap` 4279-4291 · `collab:reopen` 4293-4301. Requires `collab-client`,
`collab-upload`, `collab-poll`, `scenes`, `analytics`, `src/packs`, `src/scenedigest`, `src/collab`,
`src/takes`.

#### `main/ipc/collab-room.js` — finding, reading, entering and leaving rooms (≈230, EB)
`collab:status` 3504-3564 · `collab:list` 3852-3860 · `collab:thumb` 3862-3908 · `collab:join`
3910-3980 · `collab:leave` 4172-4189 · `collab:mine` 4303-4313 · `collab:report` 4315-4324. Requires
`paths`, `collab-client`, `collab-upload`, `collab-poll`, `imports`, `scenes`, `analytics`,
`src/transfer`, `src/library`, `src/collab`, `src/scenedigest`, `src/packurl`, `electron.app`.

#### `main/ipc/collab-sync.js` — parts, takes and presence (≈215, EB)
`collab:claim` 3982-3992 · `collab:release` 3994-4001 · `collab:push` 4003-4082 · `collab:pull`
4084-4148 · `collab:takes` 4150-4170 · `collab:heartbeat` 4234-4251. Requires `collab-client`,
`collab-poll`, `scenes`, `analytics`, `src/transfer`, `src/takelist`, `src/library`, `src/collab`,
`src/packs`, `src/takes`.

#### `main/catalog.js` — the scene catalog, whole (≈150, EB)
Banner 4460-4467 · `catalogFlagOn` 4468 · `catalogInstalls`, `catalogFetchedAt`, `CATALOG_REFRESH_MS`
4471-4473 · `catalogBucketUrl` 4475-4477 · `dropStaleCatalogThumbs` 4479-4490 · `refreshCatalogIndex`
4492-4520 · `recordCatalogDownload` 4522-4534 · `register(ipcMain)` = `catalog:index` 4536-4544,
`catalog:thumb` 4546-4564, `catalog:install` 4566-4600, `catalog:installCancel` 4602-4610. Requires
`env`, `paths`, `license-store`, `supa`, `imports`, `src/transfer`, `src/catalog`, `src/packurl`,
`electron.app`.

#### `main/ipc/packs.js` — the library's channels (≈210, EB)
`packs:importZips` 4619-4622 · `packs:importPaths` 4624-4629 · `packs:delete` 4631-4662 · banner
4725-4731 · `sceneForVersions` 4732-4736 · `safeVersionId` 4737 · `packs:versions` 4739-4743 ·
`packs:setHead` 4745-4754 · `packs:fork` 4756-4768 · `packs:scan` 4770-4809 · `packs:pickZips`
4811-4821 · `packs:pickZipFolder` 4823-4835 · `packs:openReady` 4837-4851. Requires `license-store`,
`scenes`, `imports`, `media` (`dropVideoCache`), `protocol` (`packsRoot()`), `collab-poll`
(`startCollabPolling`), `analytics`, `src/library`, `src/packs`, `electron` (`dialog`, `shell`,
`BrowserWindow`).

#### `main/ipc/takes.js` — recording, scene history, the camera prompt (≈240, EB)
`rec:save` 4873-4957 · `media:askCamera` 4996-5001 · `rec:list` 5003-5006 · `scene:save` 5008-5110 ·
`scene:list` 5112-5115 · `scene:delete` 5117-5127 · `rec:delete` 5129-5139. Requires `env`, `paths`,
`scenes`, `settings` (`scoreDifficulty`), `usage`, `analytics`, `src/takelist`, `src/scorelog`,
`src/library`, `src/takes`, `src/score`, `src/packurl`, `electron.systemPreferences`.

#### `main/ipc/app.js` — scoring config, settings, the analytics relay (≈45, EB)
`score:config`, `score:trend` 4959-4976 · `settings:get`, `settings:set` 4978-4986 · `analytics:track`
4988-4994. Requires `paths`, `settings`, `analytics`, `src/scorelog`, `src/score`.

#### `main/ipc/exports.js` — the three save-dialog exports (≈180, EB)
`packs:exportZip` 4664-4723 · `video:export` 5150-5260 · `mix:export` 5409-5421. Requires `paths`,
`scenes`, `media`, `tools`, `meter`, `usage`, `analytics`, `src/takefile`, `src/scenequota`,
`electron` (`dialog`, `BrowserWindow`, `app`).

#### `main/ipc/reel.js` — the reel pipeline (≈150, EB)
Banner 5262-5267 · `reelSessions`, `reelSeq` 5268-5269 · `dropReelSession` 5271-5276 · `reel:start`
5278-5298 · `reel:frames` 5300-5311 · `reel:abort` 5313-5316 · `reel:finish` 5318-5407. Requires `env`,
`scenes`, `media`, `tools`, `usage`, `analytics`, `src/reel`, `src/takelist` (`camPathIn`),
`electron` (`dialog`, `BrowserWindow`, `app`).

#### `main/ipc/license.js` — the licence and flags channels (≈115, EB)
`license:status` 2959 · `flags:get`, `flags:refresh` 3033-3037 · `license:oauth`, `license:otpStart`,
`license:otpVerify` 3038-3045 · `license:quota` 3046-3063 · `license:usage` 3064-3071 ·
`license:signout` 3073-3089 · `license:deleteAccount` 3091-3129. Requires `license-store`,
`license-session`, `license-refresh`, `meter`, `usage`, `supa`, `analytics`, `src/scenequota`.

#### `main/ipc/profile.js` — handles and profiles (≈160, EB)
Comment 3131-3149 · `profileFlagOn` 3150 · `profile:get` 3152-3195 · `profile:update` 3197-3256 ·
`profile:checkHandle` 3258-3287. Requires `license-store`, `supa`, `analytics`, `src/handles`.

#### `main/window.js` — the window (≈85, EB)
`windowTitle` 5912-5926 (**`cwd: __dirname` → `path.join(__dirname, '..')`**) · `createWindow`
5928-5978 + 6552-6553, with `if (SMOKE) Smoke.attach(win)` where 5979-6551 were, and
`preload: path.join(__dirname, '..', 'preload.js')`, `loadFile(path.join(__dirname, '..', 'renderer',
'index.html'))` · `installSessionPolicy()` = 6661-6669 · `applyDevDockIcon()` = 6598-6603
(**`__dirname` → `..`**). Requires `env`, `imports` (`clearOpenTarget`), `smoke`, `electron`.

#### `main/smoke/index.js` — the harness (≈90, EB)
5979-6011 (`outDir`, `sleep`, `shot`, `js`, `SMOKE_LEGS`, `smokeScope`, `endOfScope`, the
`console-message` relay, `did-finish-load`) and 6526-6551 (scope validation, `SMOKE_OK` +
`app.quit()`, `SMOKE_FAIL` + `app.exit(1)`), plus `visibleView` 6295-6296 on the shared context.
`attach(win)` builds `ctx = { win, js, shot, sleep, outDir, visibleView }` and runs
`for (const [leg, run] of LEGS) { await run(ctx); if (endOfScope(leg)) return; }` — the same six
`endOfScope` checks at the same six points (6229, 6248, 6297, 6364, 6442, end).

#### `main/smoke/home.js` (≈220) · `tableread.js` (≈20) · `booth.js` (≈48) · `screening.js` (≈68) · `history.js` (≈80) · `reel.js` (≈85) — all EB
Verbatim 6012-6229, 6230-6248, 6249-6294, 6298-6364, 6365-6442, 6443-6525, each as
`module.exports = async (ctx) => { … }`, with `js`, `shot`, `sleep`, `outDir`, `visibleView` taken
off `ctx`. The `js()` strings, the `console.log('SMOKE_…')` lines and the `throw`s do not change by
a character (§2e). `home.js` requires `scenes`, `imports`, `src/takefile`, `src/library`;
`reel.js` requires `scenes`, `tools`, `media`, `src/takelist`, `src/reellayout`.

### Every top-level function, assigned

The 123 top-level functions and 36 nested helpers, each to exactly one file (handlers are listed
by channel in the file entries above; the four `env`/`paths`/`main.js` bindings are covered in §5):

`activeSceneLines`→ipc/collab-host · `takesInArgv`, `focusMainWindow`, `flushOpens`, `queueOpen`,
`invitesInArgv`, `flushInvites`, `queueInvite`→imports · `loadLicenseRecord`, `saveLicenseRecord`,
`clearLicenseRecord`, `licenseBypass`, `bypassPlan`, `licenseCfg`, `setLicenseStatus`,
`flagValues`→license-store · `seedDevBucket`, `simulatedFreeQuota`, `askSceneQuota`→meter ·
`machineId`, `osVersion`, `runtimeName`, `resolveMachineId`, `supaFetch`→supa ·
`activateWithAccessToken`, `oauthCallbackServer`, `oauthLogin`, `otpStart`, `otpVerify`→license-session ·
`refreshLicense`, `noteLicenseVerdict`, `refreshFlags`, `initLicense`→license-refresh ·
`takeLastRunVersion`, `announceOverride`, `announceEnabled`, `announceQueue`, `refreshAnnouncements`,
`loadAnnounceSeen`, `saveAnnounceSeen`, `announceBucketUrl`, `announceAsset`→announcements ·
`announceSeenFile`, `announceRoot`, `catalogRoot`, `collabThumbRoot`, `creatorRoot`→paths ·
`normalizeQuota`, `sceneBucket`, `quotaField`, `quotaRefusalMessage`→src/scenequota ·
`recordShare`, `recordUsage`, `recordActivity`, `sceneDetailFor`, `finishRecording`→usage ·
`analyticsCfg`, `analyticsEnabled`, `analyticsClientId`, `scheduleAnalyticsFlush`, `track`,
`flushAnalytics`, `sceneEventProps`→analytics · `setUpdateStatus`, `autoRestartEnabled`,
`updatesUsable`, `needsSquirrelStaging`, `markUpdateStaged`, `noteUpdateFailure`, `updater`,
`checkForUpdates`, `actOnUpdate`→updates · `loadSettings`, `saveSettings`, `scoreDifficulty`,
`linkImportEnabled`→settings · `appendScore`, `readScores`→src/scorelog · `libraryMode`,
`resolveScene`, `packForRenderer`, `camSourceDir`→scenes · `importZipsIntoLibrary`,
`importPathsIntoLibrary`, `stripDiagnostics`, `trackImported`, `trackImportFailure`→imports ·
`countSceneTakes`→src/library (`countTakes`) · `installNewScene`, `saveSceneEdit`→creator-write ·
`carryExistingBed`, `applyGeneratedBed`, `isolateBackground`, `separatorPaths`→creator-bed ·
`allowedRoots`→protocol · `mediaUrl`→src/packurl · `recordingPathIn`, `camPathIn`, `listRecordings`,
`listSceneRecordings`→src/takelist · `managedYtdlpPath`, `toolPath`, `maybeUpdateManagedYtdlp`,
`ffmpegPath`, `ffprobePath`, `demucsPath`, `demucsModelPath`→tools · `directVideoFile`,
`transcodeVideo`, `sceneVideo`, `dropVideoCache`, `brandAssetForFfmpeg`, `probeMedia`,
`brandedExport`→media · `dropCreatorSession`, `finishCreatorStage`, `newCreatorSession`,
`pickAudioCodec`, `creatorSpecFromRenderer`, `stageCreatorSource`, `stageEditSource`→creator-stage ·
`windowTitle`, `createWindow`→window · `registerIpc`→main.js (as the ordered list of `register` calls).
Nested: `profileFlagOn`→ipc/profile · `collabFlagOn`, `collabFetch`, `readCollabPins`,
`hostedRoomInPlay`, `collabPinFor`, `writeCollabPin`, `readPullLedger`, `writePullLedger`,
`collabProgress`→collab-client · `uploadFileTo`, `downloadFileTo`→src/transfer ·
`abortCollabUpload`, `uploadCollabThumb`, `uploadSceneForCollab`, `adoptHostedRoom`→collab-upload ·
`collabPollAllowed`, `collabPushChanged`, `pollCollabMine`, `absorbCollabInfo`, `pollCollabInfosOnce`,
`pollCollabInfos`, `pokeCollabPoll`, `startCollabPolling`→collab-poll · `catalogFlagOn`,
`catalogBucketUrl`, `dropStaleCatalogThumbs`, `refreshCatalogIndex`, `recordCatalogDownload`→catalog ·
`catalogIndexFile`, `catalogEtagFile`→paths · `sceneForVersions`, `safeVersionId`→ipc/packs ·
`dropReelSession`→ipc/reel. Constants: `str`→creator-link · `round3`, `SCORE_LOG_MAX`→src/scorelog ·
`UNLIMITED_QUOTA`→src/scenequota · `ARCHIVES`→imports · `COLLAB_MEMBER_RE`→scenes · `PLAYABLE_EXTS`,
`probeSource`→creator-stage · `execFileOut`→supa · `oauthResultPage`→license-session · `MIME`→protocol
· `RELEASES_PAGE`→updates · `LICENSE_VERDICTS`, `REVALIDATE_INTERVAL_MS`, `FLAGS_*`→license-refresh ·
`ANNOUNCE_ASSET_RE`→announcements · `ANALYTICS_*`→analytics · `SMOKE_LEGS`→smoke/index.

### The require graph (acyclic by construction)

```
L0  env  paths  src/*  (src/scenequota, src/scorelog, src/packurl, src/takelist, src/transfer new)
L1  license-store            ← env paths src/flags src/license-config src/scenequota
L2  supa                     ← license-store src/activation
    settings                 ← paths src/score
    protocol                 ← paths src/allowlist src/packurl
    scenes                   ← paths src/library src/packurl src/takes
L3  analytics                ← env paths license-store supa settings src/analytics* src/scenestats
    announcements            ← env paths license-store supa src/announce src/sanitize src/packurl
    usage                    ← license-store supa scenes src/packs src/scenestats
    tools                    ← env settings src/tools src/ytdlp src/createpack
L4  license-refresh          ← license-store supa announcements src/activation src/scenequota
    meter                    ← license-store supa usage src/activation src/scenequota
    imports                  ← license-store supa analytics usage scenes src/import src/zip src/takefile src/packs src/scenestats src/collab
    updates                  ← env settings analytics src/updates
    media                    ← paths scenes tools src/brand src/library src/packurl
    creator-bed              ← tools src/separate src/createpack src/packurl   (creator-stage: see L5)
L5  license-session          ← supa license-store license-refresh analytics src/activation src/license-config src/scenequota
    collab-client            ← license-store supa license-refresh scenes src/library src/collab
    creator-write            ← scenes creator-bed src/createpack src/library src/creator src/packs
    catalog                  ← env paths license-store supa imports src/transfer src/catalog src/packurl
L6  collab-upload            ← paths collab-client scenes src/transfer src/zip src/library src/collab
    collab-poll              ← env license-store collab-client src/collab
    creator-stage            ← paths tools collab-client src/packurl src/createpack src/creator src/packs src/collab
L7  creator-link             ← paths tools creator-stage settings src/creator src/createpack
    creator-bed.register     ← creator-stage  (the two handlers only; the functions above are L4)
    ipc/*                    ← whatever each lists above
    smoke/*                  ← env scenes imports media tools src/takefile src/library src/takelist src/reellayout
L8  window                   ← env imports smoke
L9  main.js                  ← everything
```

The three cycles the flat file would have handed a naive split, and how the layering avoids them:
`license-store ↔ flags` (broken by keeping `flagValues` in the store and `refreshFlags` in
`license-refresh`), `imports ↔ meter/usage` (broken by keeping `resolveScene` in `scenes`, which
neither requires), and `protocol ↔ creator` (broken by putting `creatorRoot` in `paths`). One
more that appears only inside the collab split, `collab-client ↔ collab-poll` over
`collabSessions`, is broken by making the list the client's state with `setSessions()`, so the
poller depends on the client and never the reverse. `creator-bed` is split across two layers on
paper only because its two handlers need `creator-stage`'s session map; in the file that is one
`register()` at the bottom, and Node is fine with it because `creator-stage` never requires
`creator-bed`.

## 5. The entry point afterwards

`main.js` shrinks to the orchestrator, ≈170 lines, in this order — the order is the contract,
because three things must happen before `ready` and one must happen after `initLicense`:

```js
'use strict';
const { app, ipcMain } = require('electron');
const path = require('path');
const { SMOKE } = require('./main/env');
if (SMOKE) { /* 75-99 verbatim: the five appendSwitch calls and app.setPath('userData') */ }

const Imports = require('./main/imports');
const singleInstance = SMOKE || app.requestSingleInstanceLock();      // 179-180 verbatim
if (!singleInstance) app.quit();
if (singleInstance && !SMOKE) Imports.wireInstance();                  // 181-199: argv scan + the three app.on

const Protocol = require('./main/protocol');
Protocol.registerPrivileged();                                         // 201-203, still before ready

const License = require('./main/license-store');
const LicenseRefresh = require('./main/license-refresh');
/* … one require per main/ module … */

function registerIpc() {                                               // same name, same call site
  require('./main/ipc/license').register(ipcMain);
  require('./main/announcements').register(ipcMain);
  require('./main/ipc/profile').register(ipcMain);
  require('./main/ipc/collab-room').register(ipcMain);
  require('./main/ipc/collab-host').register(ipcMain);
  require('./main/ipc/collab-sync').register(ipcMain);
  require('./main/catalog').register(ipcMain);
  require('./main/updates').register(ipcMain);
  require('./main/ipc/packs').register(ipcMain);
  require('./main/usage').register(ipcMain);
  require('./main/ipc/takes').register(ipcMain);
  require('./main/ipc/app').register(ipcMain);
  require('./main/media').register(ipcMain);
  require('./main/ipc/exports').register(ipcMain);
  require('./main/ipc/reel').register(ipcMain);
  require('./main/tools').register(ipcMain);
  require('./main/creator-stage').register(ipcMain);
  require('./main/creator-link').register(ipcMain);
  require('./main/ipc/creator').register(ipcMain);
  require('./main/creator-bed').register(ipcMain);
}

app.whenReady().then(async () => {                                     // 6555-6700, minus what moved
  if (!singleInstance) return;
  if (app.isPackaged && !SMOKE) app.setAsDefaultProtocolClient('badtakes');
  const lib = Scenes.libraryMode();
  Protocol.setPacksRoot(lib.root);
  if (lib.mode === 'tree' && !SMOKE) { /* 6569-6593 migration block verbatim */ }
  await LicenseRefresh.initLicense();
  Window.applyDevDockIcon();
  Protocol.install();                                                  // the pack:// handler
  Window.installSessionPolicy();
  if (process.platform === 'darwin' && !SMOKE) { /* 6671-6673 mic prompt */ }
  registerIpc();
  Window.createWindow();
  Analytics.track('app_open', { plan: License.status().plan || null, /* 6681-6686 */ });
  Updates.schedule();                                                  // 6688-6695: the +3s check and the 6h interval
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) Window.createWindow(); });
});

app.on('window-all-closed', () => { app.quit(); });
app.on('before-quit', () => { Usage.finishRecording(null); Analytics.flush().catch(() => {}); });
app.on('will-quit', () => { CreatorStage.dropAll(); });                // 6722-6723 inside dropAll
```

How §2's contract survives, item by item:

- **IPC names.** Every `ipcMain.handle('<channel>', …)` keeps its literal; only the enclosing
  function changes from `registerIpc` to a module's `register(ipcMain)`. `ipcMain.handle` throws on
  a duplicate channel, so a channel registered by two modules fails the first `npm start`, and the
  channel-set test in §8 pins the set against `preload.js`. `preload.js` and `renderer/` are not
  touched. Push channels keep their literals inside the moved functions.
- **The protocol.** `registerPrivileged()` runs at `main.js` load, exactly where 201-203 ran;
  `install()` runs at the same point of `whenReady` as 6617; the handler body is verbatim except
  the decode line calling `src/packurl`. `allowedRoots()` lists the same eight roots.
- **Flags, env, argv.** `SMOKE` is computed by the same expression in `env.js`; every
  `process.env.*` read moves with the function that reads it; `takesInArgv`/`invitesInArgv` still run
  synchronously at load through `wireInstance()`, so a cold-start `open-file` on macOS still finds a
  handler registered before `ready`.
- **Log lines and screenshots.** The `js()` strings and every `console.log('SMOKE_…')` move into
  `main/smoke/<leg>.js` unchanged; `SMOKE_OK`/`app.quit()` and `SMOKE_FAIL`/`app.exit(1)` stay
  together in `smoke/index.js`. The `RENDERER:` relay stays.
- **Boot order** (§2f) is the order of the `whenReady` body above, which is the order of 6555-6700
  with the moved blocks called at their original positions.

Not pure moves — the mechanical rewrites the split needs, so a reviewer knows what to expect in
the diff beyond relocated text (all are one-line, and the §8 function-body diff lists them):

| Today | Afterwards | Sites |
| --- | --- | --- |
| `licenseStatus` (read) | `License.status()` | 20 reads listed in §3 State, plus 15 `{ ...licenseStatus }` spreads (976, 1036, 1069, 1116, 1137, 3078, 3128, 3188, 3216, 3246 and inside `refreshLicense`/`refreshFlags`) |
| `updateStatus` (read at 4613) | `Updates.status()` | 1 |
| `usageTotals` (3070) | `Usage.totals()` | 1 |
| `openRecording.lines.add/delete` (4954, 5137) | `Usage.noteLineRecorded(id, lineId)`, `Usage.noteLineScrapped(lineId)` | 2 |
| `openTarget = …` (4844, 5968-5970) | `Imports.setOpenTarget(wc)`, `Imports.clearOpenTarget(wc)` | 2 |
| `collabSessions = …` (3714, 4311) / reads (3368, 4364-4407, 5584) | `CollabClient.setSessions(list)` / `sessions()` | 2 / 4 |
| `collabUploads.get/delete` (3527, 3841) | `CollabUpload.uploadState(sceneId)`, `clearUpload(sceneId)` | 2 |
| `packsRoot` (4773) / `packsRoot = lib.root` (6568) | `Protocol.packsRoot()` / `setPacksRoot()` | 2 |
| `creatorSessions.get/has` outside the creator (5584 no; 5730, 5876, 5904 in handlers that move with it) | `CreatorStage.get(id)` etc. | inside the creator modules only |
| `appendScore(rec)`, `readScores()` | `ScoreLog.appendScore(Paths.scoreLogFile(), rec)`, `readScores(Paths.scoreLogFile())` | 2 |
| `countSceneTakes(scene)` (4638, 4795) | `Library.countTakes(scene.sceneDir)` | 2 |
| `require('./src/x')` | `require('../src/x')` | every `main/` module |
| `path.join(__dirname, 'brand'|'preload.js'|'renderer'|'build')`, `cwd: __dirname` | `path.join(__dirname, '..', …)` | 5 (2805, 5921, 5953, 5962, 6602) |
| `import('./server/supabase/functions/_shared/scenequota.mjs')` (313) | `import('../server/…')` | 1 — the dynamic import is relative to the file that contains it |

## 6. Where ~250 lines would force an artificial boundary

Eric's rule: over 250 is where a file gets looked at. These are the ones that will be looked at,
with the honest size and why each stays whole. Sizes are estimates from the measured spans; the
final count is whatever the moved text plus requires comes to.

| File | Honest size | Why it stays whole (and the cut if Eric wants one anyway) |
| --- | --- | --- |
| `main/license-refresh.js` | ≈300 | `refreshLicense` (83), `refreshFlags` (54) and `initLicense` (101) are one mechanism: three heartbeats on one cadence, all with the same re-read-then-save rule against concurrent writers (the comment at 546-550 explains it once and the other two refer back). `initLicense` is what schedules them, and the focus throttles it installs read `flagsFocusAt` beside `refreshFlags`. Splitting `initLicense` off would put the scheduler in one file and the constants it schedules by in another. *Cut:* `main/license-boot.js` holding `initLicense` alone (≈105) — then `license-refresh` is ≈195. |
| `main/updates.js` | ≈300 | One state machine (`idle → checking → … → ready`) whose whole reasoning is the 60-line header comment on why `staging` exists on macOS. `updater()` wires the events, `markUpdateStaged` and `noteUpdateFailure` are their targets, `checkForUpdates`/`actOnUpdate` are the two entry points, and `autoRestartEnabled` is read by three of them. Every function reads `updateStatus`. *Cut:* none that does not separate an event from its handler. |
| `main/creator-stage.js` | ≈290 | A session's life: mint, stage a file / an edit source / a picked file, probe and preview-transcode, discard, sweep at quit. `stageCreatorSource`, `stageEditSource` and `finishCreatorStage` call each other; the handlers are thin wrappers over them. *Cut:* move `creator:editStage` + `stageEditSource` (≈80) into `main/ipc/creator.js`, which grows to ≈225 and then owns both halves of an edit (stage and save). Reasonable either way. |
| `main/announcements.js` | ≈275 | The feature end to end: the cache, the receipts ledger, the asset fetch, the three handlers, and `start()`. Its failure posture ("fail to silence", 735-740) is one rule the whole file obeys, and the handlers are the only readers of the ledger. *Cut:* the seen-ledger (`loadAnnounceSeen`/`saveAnnounceSeen`, ≈35) to `src/` with a test — small win. |
| `main/imports.js` | ≈245 | Borderline. The OS open queue (99 lines) and the three install wrappers are "how a scene gets in", and `flushOpens` calls `importPathsIntoLibrary`. *Cut:* `main/open-queue.js` (≈105) and `main/imports.js` (≈145). |
| `main/ipc/takes.js` | ≈240 | `rec:save` (85) and `scene:save` (103) are two long linear handlers; the other five are short. Coherent as "what the booth and the screening room write". |
| `main/ipc/collab-host.js`, `collab-room.js`, `collab-sync.js` | ≈235 / 230 / 215 | The 18 collab handlers split by who acts (host / anyone / a member syncing); `collab:create` (114) and `collab:join` (66) are single linear flows and dominate their files. |
| `main/smoke/home.js` | ≈220 | The home leg is one script with shared locals (`source`, `zipPath`, `before/after`) and three `throw`s that read each other's counts; cutting it into "gate", "import" and "casting" would thread those through. It reads top-to-bottom as the walkthrough log does. |

The opposite case — files that come out **small** and are honest at that size, so nobody
"rounds them up" by merging unrelated things: `main/env.js` (12: one constant twenty-eight
modules read), `src/packurl.js` (20: an encoder and its decoder), `main/smoke/tableread.js` (20: a
leg is a leg — `SMOKE_SCOPE=tableread` names it), `main/ipc/app.js` (45), `main/settings.js` (50:
its handlers cannot live here without a cycle), `main/smoke/booth.js` (48), `main/paths.js` (60).

## 7. Build and packaging touch points

| Where | Change | Why |
| --- | --- | --- |
| `package.json` → `build.files` | add `"main/**/*"` | `files` is an allowlist (`main.js`, `preload.js`, `src/**/*`, `renderer/**/*`, `brand/*.png`, `brand/fonts/*`, `package.json`, `node_modules/**/*`); without the entry the packaged app throws `Cannot find module './main/env'` on launch and every unpackaged run is fine — exactly the failure the packaged-smoke tier row exists for. New `src/` files are covered by `src/**/*` already. |
| The asar leak check (`.github/workflows/release.yml` Windows job; `docs/RELEASING.md` §7; `cutting-a-release`) | none, **provided the directory is not named `server` or `site`** | it greps `voicepack` and `(^|/)(server|site)/`; `main/` matches neither. |
| `test/release-workflow.test.js` `BUNDLE` | optional: add `/main` and `/main/env.js` | the simulated listing; the step's behaviour does not depend on it. |
| `test/collabthumb.test.js:31` | read `main.js` **and** `main/**/*.js` (`fs.readdirSync` + `readFileSync`) | it pins the two `uploadCollabThumb(` call sites by text; after the move both are in `main/ipc/collab-host.js`, and the assertion `calls.length >= 2` fails with the file list unchanged. |
| `.github/workflows/ci.yml` / `scripts/ci-changes.sh` | none | `main/` is not on the ignore list, so a change there runs `test` and `smoke`; `Gate` still requires both. |
| `scripts/build-kit.js`, `test/kit.test.js` | none | the kit stages named `src/` modules and checks their requires; `src/library.js` gains a function and no require; the four new `src/` modules are not staged and nothing staged requires them. `src/takelist.js` requires `src/takes.js`, which is not staged — fine, since `takelist` is not staged either. |
| `web/index.html`, `scripts/build-web.js`, `mobile/` | none — and **do not add** the new `src/` modules there | they are CommonJS, main-only; `web/` loads its fourteen `src/` modules by tag and must not grow a fifteenth for this. |
| `npm start`, `npm run smoke`, `start:free`, `start:local` | none | `electron .` resolves `main.js` from `package.json`, and `require('./main/…')` is plain Node. |
| Dev-only paths in the moved code | the five `__dirname` sites and the one dynamic `import()` in §5 | a moved file resolves `__dirname` to `main/`; `brand/`, `preload.js`, `renderer/`, `build/` and `server/` are one level up. |
| Docs that name `main.js` as a location | sweep in the last PR: `src/CLAUDE.md`, `renderer/CLAUDE.md`, `server/CLAUDE.md`, `admin/README.md:101`, `build/CLAUDE.md` (`reel:*` IPC "in main.js"), root `CLAUDE.md` (the "Architecture at a glance" `main.js` bullet gains a sentence for `main/`), `web/lib/report.js` and `web/account.js` comments | prose only, no test reads them; `admin/README.md` and `server/CLAUDE.md` name `main.js` as the app's `sceneBucket` copy, which becomes `src/scenequota.js` in PR A. |
| A packaged build once | `npm run dist:win:dir` (fastest asar on this Mac) or `npm run dist`, then `npx @electron/asar list … \| grep '^/main/'` and the packaged smoke | proves the allowlist line; see §8. |

## 8. Verification recipe

The `implementing-changes` tier table, applied to what each PR touches:

| Touched | Proof |
| --- | --- |
| `src/*.js` (the five new modules, `src/library.js`) | `npm test`; say whether the pack tests ran or skipped (they skip without `voicepacks/`, which a worktree has after `scripts/worktree-init.sh`). New tests: `test/scenequota.test.js`, `test/scorelog.test.js`, `test/packurl.test.js`, `test/takelist.test.js`, `test/transfer.test.js`, and `countTakes` in `test/library.test.js`. |
| `main.js`, `main/**` | `rm -rf /tmp/bt-lib && cp -R fixtures/library /tmp/bt-lib; BT_LIBRARY_ROOT=/tmp/bt-lib SMOKE_SCOPE=<leg> npm run smoke` → `SMOKE_OK` and exit 0, screenshots opened. Which scope proves which module is below. |
| `package.json` `build.files` (PR B) | packaged smoke once: `npm run dist` (or `dist:win:dir` for a quicker asar), `npx @electron/asar list "dist/mac-arm64/Bad Takes.app/Contents/Resources/app.asar" \| grep -c '^/main/'` > 0, then `mkdir -p /tmp/bt-smoke; BT_LIBRARY_ROOT=/tmp/bt-lib SMOKE_DIR=/tmp/bt-smoke "dist/mac-arm64/Bad Takes.app/Contents/MacOS/Bad Takes" --smoke` → `SMOKE_OK`. |

What each `SMOKE_SCOPE` exercises after the split (scopes are prefixes; every earlier leg runs):

| Scope | Modules and handlers it drives |
| --- | --- |
| `home` (CI's leg; the only one with `throw`s behind its counts) | `env` switches, `license-store` (`initLicense` bypass path, `setLicenseStatus` push), `protocol` (every icon in the rail is a `pack://` fetch through `allowedRoots`), `scenes` (`libraryMode`, `resolveScene`, `packForRenderer`), `imports` (drop wiring, `importZipsIntoLibrary`, `importPathsIntoLibrary`, `queueOpen → flushOpens → packs:opened`), `ipc/packs` (`packs:scan`, `packs:openReady`), `settings`, `ipc/app`, `src/takelist` (`rec:list`/`scene:list` on select), `src/library.countTakes` (rail take counts), `window`, `smoke/index` + `home` |
| `tableread` | `media` (`video:prepare` → `directVideoFile`; fixtures carry no `.ogv`, so `transcodeVideo` resolves null — the ffmpeg path is only exercised by a real pack), `smoke/tableread` |
| `booth` | `ipc/takes` (`rec:save` with score → `src/scorelog.appendScore`, `media:askCamera`), `usage.noteLineRecorded`, `analytics.track` (queued, never sent under smoke), `smoke/booth` |
| `screening` | `ipc/takes` (`scene:save` scorecard, `scene:list`), `src/takelist.listSceneRecordings`, `settings.scoreDifficulty`, `smoke/screening` |
| `history` | scene-history rows and the viewer (`scene:list` again, `ipc/exports` menus opened but no dialog), `smoke/history` |
| full run | `ipc/reel` (all four channels, `reel:start` dialog-less), `media.probeMedia`, `tools.ffmpegPath` (a real ffmpeg render), `src/takelist.camPathIn`, `smoke/reel` |

Not reachable by any smoke scope, and how each is covered instead: everything gated on `SMOKE`
or a bypass — `updates` (`updatesUsable` false), `collab-poll`, `catalog` refresh, `announcements`
(bypass suppresses), `analytics` sends, `tools.maybeUpdateManagedYtdlp`, the `badtakes://` client
registration, the single-instance `app.on` trio (the queue *is* exercised, the wiring is not), the
mic prompt; every dialog (`packs:exportZip`, `video:export`, `mix:export`, the two pickers,
`creator:pickSource`, `reel:start` outside smoke); `packs:delete/versions/setHead/fork`; `profile:*`;
`license:oauth/otpStart/otpVerify/deleteAccount`; every `collab:*` and `catalog:*` handler; every
`creator:*` handler; the flat-library (`BT_PACKS_ROOT`) branches; Windows/Linux argv handling. For
these the proof is (a) the function-body diff below showing the moved text is unchanged, (b) the
module-load test, (c) the manual checks named per PR in §9 (`npm run start:local` sign-in for the
licence modules, an ad-hoc CDP creator run per the root `CLAUDE.md`, a two-account collab against
the local stack for PR D), and (d) launching the app after each PR, which Eric does by hand anyway.

**Behaviour-preservation checks** (these are what make the refactor reviewable as moves rather
than as 6700 lines of diff; the first two become `test/main-modules.test.js` and stay):

1. **Module-load test.** Load `main.js` under a stubbed `electron` (the stub in
   `scripts/check-main-split.js` below: `app` with `commandLine`, `setPath`, `requestSingleInstanceLock → true`,
   `on`, `whenReady → never`, `getPath → tmp`, `getVersion`, `isPackaged: false`; `ipcMain.handle`
   recording channels; `protocol`, `BrowserWindow.getAllWindows → []`; `electron-updater` → `{ autoUpdater: { on } }`).
   Assert: every `main/**/*.js` loads; **no require cycle** (wrap `Module._load` and fail if a
   module is re-entered while on the load stack); no module called `app.getPath` during load
   (count calls before `registerIpc()` runs: must be 0 — today the only pre-ready `getPath` is the
   smoke `setPath`, which is not a read).
2. **Channel set.** After `registerIpc()`, the set of `ipcMain.handle` channels equals the set of
   `ipcRenderer.invoke('…')` literals parsed out of `preload.js` (84 today), and no channel was
   registered twice (the stub records duplicates; Electron would throw). Also assert the eight
   `webContents.send` channel literals in `main/**` are a subset of `preload.js`'s `ipcRenderer.on` set.
3. **Function-body diff (the "pure move" proof).** Under the same stub, at the pre-split commit and
   at the post-split tree: for every top-level function of old `main.js` (123 names, list in §4) and
   every nested helper reachable by the `globalThis.__nested` patch used for this spike, take
   `fn.toString()`, normalise leading indentation, and diff against the new tree's function of the
   same name (found by loading each `main/` module's exports plus its closures via the same patch).
   Expected differences are exactly the rows of the §5 table: an accessor call, a `..` in a path, a
   file argument. Anything else is a behaviour change to explain in the PR. The 84 handlers are
   compared the same way, keyed by channel. Keep this as `scripts/check-main-split.js` for the four
   PRs and delete it in the last one (or keep it as a test that compares against a committed
   fingerprint — Eric's call; the module-load and channel-set tests are the durable half).
4. **Untouched surfaces.** `git diff --stat main..HEAD -- preload.js renderer/ web/ src/CLAUDE.md`
   shows only the documented doc edits; no change under `renderer/` or `web/` at all.
5. **Log-line parity.** Run `SMOKE_SCOPE=home npm run smoke` before and after PR C and `diff` the two
   logs with timestamps and paths stripped (`sed -E 's#/private/tmp/[^ ]+##; s/[0-9]+ -> [0-9]+//'`):
   the `SMOKE_*` sequence must be identical line for line, and the screenshot set the same fifteen
   names. This is the concatenation check's equivalent for a walkthrough.

## 9. Phase 2 order

Four PRs, each leaving `main.js` runnable and each reviewable as moves. Sizes are lines relocated.

**PR A — Electron-free extractions to `src/`, with tests (≈340 lines moved, 6 test files).**
`src/scenequota.js`, `src/scorelog.js`, `src/packurl.js`, `src/takelist.js`, `src/transfer.js`,
`Library.countTakes`; `main.js` requires them and loses the originals; `admin/README.md:101` and
`server/CLAUDE.md:241` re-point the app's `sceneBucket` copy. No `main/` yet, no `build.files`
change (all under `src/**/*`). Proof: `npm test` (new tests green, note the pack-test skip count),
`SMOKE_SCOPE=screening` (covers `takelist`, `scorelog` through `rec:save`/`scene:save`, `packurl`
through every media URL), and the scenequota parity test against `_shared/scenequota.mjs`.

**PR B — `main/` skeleton and the stateful services (≈1900 lines moved).**
`package.json` `build.files` + `"main/**/*"`; `env`, `paths`, `license-store`, `supa`, `settings`,
`analytics`, `announcements` (with its three handlers), `usage` (with `activity:*`), `meter`,
`updates` (with `updates:*` and `schedule()`), `license-session`, `license-refresh`; `main.js` keeps
everything else and calls the new modules; `test/main-modules.test.js` lands (module-load, no-cycle,
channel-set). Proof: `npm test`; `SMOKE_SCOPE=home`; **the packaged smoke** (the allowlist changed);
`npm run start:local` and sign in with the email code to drive `license-session` →
`activateWithAccessToken` → `refreshFlags` → `initLicense`'s intervals; `npm run start:free` to see
the simulated meter and usage rows move.

**PR C — library, media, tools, the creator, the protocol, the window and the smoke tree (≈2300 lines moved).**
`scenes`, `imports` (with `wireInstance`), `protocol`, `tools` (with `creator:tools`, `fetchYtdlp`,
`cancelYtdlpFetch`), `media` (with `video:prepare`), `creator-stage`, `creator-link`, `creator-bed`,
`creator-write`, `ipc/creator`, `window`, `main/smoke/*`; the five `__dirname` fixes and the
`import('../server/…')` fix; `main.js`'s `createWindow` and protocol handler go. Proof: the full
`npm run smoke` (every leg, reel included) with the log-line parity diff of §8.5; `SMOKE_THEME=light`
once; an ad-hoc CDP creator run (stage a fixture video, mark two lines, Finish, then Edit and save)
per the root `CLAUDE.md`'s live-driving bullet; `npm start` opened and a `.take` double-clicked from
Finder against the *packaged* build only if Eric wants the file association re-proven (it is
packaged-only by nature and did not change).

**PR D — the handler groups and the orchestrator (≈2100 lines moved).**
`collab-client`, `collab-upload`, `collab-poll`, `ipc/collab-host`, `ipc/collab-room`,
`ipc/collab-sync`, `catalog`, `ipc/license`, `ipc/profile`, `ipc/packs`, `ipc/takes`, `ipc/app`,
`ipc/exports`, `ipc/reel`; `registerIpc()` becomes the ordered list in §5; `test/collabthumb.test.js`
reads `main/**`; the doc sweep of §7; `scripts/check-main-split.js` retired or pinned. Proof: full
`npm run smoke` again; `npm test`; the channel-set test; `npm run start:local` with two accounts
(the `server-changes` skill's local stack) to create, join, push and pull a collab, and to install
one catalog scene — the two subsystems no smoke leg can see.

Why not one PR: the four together move ~6600 lines through ~45 files, and a reviewer cannot see a
one-line accessor rewrite inside that. Why not more than four: each PR must leave a runnable
`main.js`, and the services (B) are what the handlers (D) call, so the boundaries above are the
ones where the require graph has a clean cut. A could fold into B if Eric prefers three.

## 10. Risks

1. **Require cycles.** The flat file hides three (§4, end) and the collab split adds a fourth; a
   CommonJS cycle does not throw, it hands the second module a half-filled `exports`, and the usual
   `const { x } = require(…)` copies `undefined` — the failure surfaces as `x is not a function`
   somewhere unrelated, at runtime. The layering avoids them; the no-cycle assertion in
   `test/main-modules.test.js` keeps them out.
2. **Load-time side effects moving in time.** Three registrations must precede `ready` and today
   sit at module scope: the `--smoke` switches (76-97) and userData redirect (98), the OS-open
   handlers (183-199), and `registerSchemesAsPrivileged` (201). The plan keeps the first in `main.js`
   and calls the other two synchronously from `main.js`'s module scope. A module that registered
   them at its own load would also work today but only by accident of require order — hence rule 1
   in §4. Conversely, nothing may call `app.getPath` at load: under `--smoke` the userData path
   changes at line 98, after `env.js` and before everything else.
3. **`__dirname` and relative paths.** Five `__dirname` sites and one dynamic `import()` resolve
   differently from `main/` (§5). The brand one fails silently — `brandAssetForFfmpeg` returns null
   and every `.mp4` export quietly ships unbranded, which no smoke leg checks (`video:export` needs a
   dialog). Grep for `__dirname` and `import(` in every moved file before each PR.
4. **Closures over shared mutable state.** The 27 module-level `let`s and `Map`s in §3 State are
   read across what become module boundaries; the accessor rule (§4, rule 2) is what keeps a
   `require`-time copy from going stale. The subtle one is `licenseStatus`: fifteen sites spread it
   (`{ ...licenseStatus, x }`) and write back through `setLicenseStatus`; each must read
   `License.status()` at the moment of the spread, not hold an earlier copy.
5. **Duplicate registration.** Two modules registering one channel throws at `registerIpc()` —
   loud and immediate, which is good, but it is the failure mode of a botched move of a handler that
   lives with its service (announcements, updates, tools, media, usage, catalog). The channel-set
   test names the channel.
6. **The `js()` strings are code inside strings.** Escapes like `\\$5` (6062), `rgba?\\(` (6274)
   and the `${JSON.stringify(report)}` interpolations (6127, 6153) survive a move only if the
   template literals are moved verbatim; a formatter run over `main/smoke/` would break them
   silently (the assertion still logs a value — the wrong one). No formatter exists in the repo
   today; keep it that way for these files, or mark them.
7. **`did-finish-load` registration order.** The walkthrough hooks `once('did-finish-load')`
   (6009) *after* `win.loadFile` (5962) but in the same synchronous body, so it never misses the
   event. `Smoke.attach(win)` must be called synchronously inside `createWindow` — not after an
   `await`, not from `whenReady`.
8. **`test/collabthumb.test.js`** reads `main.js` by path and would pass vacuously-inverted
   (zero call sites → assertion fails, so at least loudly) once `uploadCollabThumb(` moves. It is
   part of PR D, not an afterthought.
9. **What the smoke run cannot see** (§8 list): every network subsystem, every dialog, the
   updater, collab, the catalog, the creator, the flat library, and the `open-file` wiring. The
   function-body diff is the proof for those; for the updater in particular there is no local proof
   at all (§ "Auto-update cannot be verified by a `--dir` build" in the root `CLAUDE.md`), so its
   file is moved whole and unchanged (§6) and the next release's update is its test.
10. **`registerIpc`-scoped state becomes module state.** `collabUploads`, `collabSessions`,
    `catalogFetchedAt`, `reelSeq` and friends initialise at `require` time instead of at
    `registerIpc()` time. All are empty `Map`s, `[]`, `0` or `Promise.resolve()`, so nothing
    observable changes — but any *future* value that needs `app` there would now run before
    `ready`. Rule 1 in §4 again.
11. **The walkthrough's main-side calls** (§2e last block) pin `Scenes.libraryMode`,
    `resolveScene`, `Imports.importZipsIntoLibrary`/`importPathsIntoLibrary`/`queueOpen`,
    `Tools.ffmpegPath`, `Media.probeMedia`, `Takelist.camPathIn` as names the smoke tree requires;
    renaming any of them in a later cleanup means editing `main/smoke/*` too.
12. **Docs drift.** Seven documents name `main.js` as where something lives (§2d). None is
    load-bearing for a test, but `src/CLAUDE.md`'s "`main.js` owns…" sentences are what the next
    session reads first; the sweep in PR D is not optional.
13. **The CI smoke job is Gate.** A flake introduced by the smoke split (a leg file that forgets a
    `sleep`, a `visibleView` taken off the wrong context) reddens every PR's Gate, not just this
    one's. Run the home leg three times before PR C lands, as `ci.yml`'s comment already asks for
    any widening.
