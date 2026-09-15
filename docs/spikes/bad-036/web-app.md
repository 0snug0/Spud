# BAD-036 spike: splitting `web/app.js`

Phase 1 extraction plan for `web/app.js`, written against BadTakes `main` at `20d11e1dace7a7aac012b30ffcd6c983b92642c2` (2026-09-15); every line number below is that revision's. Read-only: nothing in BadTakes was changed. The split was dry-run in a scratch directory (§8 says what was proved and how to repeat it).

**Summary.** Twelve classic scripts at the top level of `web/`, named `app-<part>.js`, bare-scope exactly as today, tagged contiguously before `booth.js`; `web/app.js` stays as the entry (`boot`, Android Back, `window.bootApp`) and becomes the last tag of its own group. Every top-level name keeps its spelling and body; the split is a pure move, and a paragraph-level check (§8) proves it and fails on a one-token rewrite. Two files stay over 250 lines because each is one screen: `app-library.js` (344) and `app-stage.js` (276) — §6 gives their honest sizes and the seams if Eric wants them cut anyway.

## 1. The file today

| | |
|---|---|
| Path | `web/app.js` |
| Size | 1909 lines; 93 top-level declarations: 17 `const`, 75 `function` (24 of them `async`), and one `window.bootApp = boot` (line 1909). No top-level `let`, no class. |
| Runtime | A classic browser script under `'use strict'` (line 6). No bundler, no build step, no module system: its `const`s live in the page's shared global lexical scope and its `function`s become properties of `window`, which is how the seven other view scripts call them bare. |
| Also runs in | `test/web-guest-mode.test.js`, which evaluates the file in a Node `vm` context against a stub DOM seeded from `web/index.html` (lines 229-233), then calls `ctx.bootApp()` (234). |
| Loaded by | `web/index.html:834` `<script src="app.js">` — the first view script, right after the last `lib/` tag (`lib/zipwrite.js`, 833), followed by `booth.js` 835, `editor.js` 836, `account.js` 837, `ads.js` 838, `billing.js` 839, `collabweb.js` 840, `start.js` 841. `start.js:4` calls `window.bootApp()` at load. |
| CSP | `script-src 'self'` (`index.html:21`): no inline scripts; every new file is a `<script src>` tag. |
| Ships via | `scripts/build-web.js` (`copyTree` → `dist/web/app.js?v=<digest>`, my.badtakes.io) and the same `build()` into `mobile/www` for the iOS shell (`scripts/build-mobile.js`); `mobile/www` and `mobile/ios/App/App/public` are gitignored copies. |
| Recent history | #330 guest mode (BAD-002), #322 the reel in the container, #319 the web on the render service — all three touched `boot`. |

What it is: the browser app's shell and every screen that is not a room. Boot; the chrome (toast, the workflow header, `showView`/`showTab`, the pushed Scene screen); the library rail and its filters; the Catalog; the casting call (the detail panel, Share, Delete, the edit lock); take cards and the Takes tab; installs and drops, downloads and the progress card; the game session; the table read; the stage both rooms share (the script list, the transport, the seek strip); Settings. The rooms themselves are `booth.js`; the collab client is `collabweb.js`.

## 2. Public surface (the compatibility contract)

### 2a. Bare names the other view scripts call

Measured by grep over `web/{start,booth,editor,account,ads,billing,collabweb}.js` with comments and quoted strings stripped and template literals kept (the brief's first count stripped templates too and missed `booth.js:821` `${takeDate(…)}`; its `openShowcase` and `boot` hits were comment mentions). 39 of the 93 names are reached from outside; the cells are uses per file.

| name (line) | booth | editor | account | ads | billing | collabweb | destination |
|---|---|---|---|---|---|---|---|
| `$` (8) | 130 | 46 | 92 | 3 | 49 | 193 | app-shell |
| `state` (14) | 110 | 13 | 50 | 3 | 7 | 85 | app-shell |
| `toast` (36) | 13 | 10 | 4 | | 4 | 11 | app-shell |
| `setHeader` (45) | 3 | 1 | | | | 1 | app-shell |
| `signedInNow` (81) | | | 2 | | | | app-shell |
| `showView` (83) | 3 | 1 | 1 | | | 1 | app-shell |
| `markTab` (115) | | | | | | 1 | app-shell |
| `showTab` (122) | 1 | | 1 | | | 1 | app-shell |
| `goHome` (142) | | | 4 | | | 1 | app-shell |
| `backToLibrary` (147) | 4 | 2 | | | | 1 | app-shell |
| `isPhone` (159) | | | 1 | | | 1 | app-shell |
| `setSceneOpen` (167) | | | | | | 1 | app-shell |
| `loadLibrary` (253) | | 1 | | | | 1 | app-library |
| `renderLibrary` (344) | | | | | | 3 | app-library |
| `renderBrowse` (455) | | | 2 | | | | app-library |
| `refreshEntryCta` (316) | | | 2 | | | | app-catalog |
| `selectScene` (642) | | | | | | 3 | app-casting |
| `renderEditLock` (861) | | | | | | 4 | app-casting |
| `takeDate` (949) | 1 | | | | | | app-util |
| `gradeOf` (1356) | 4 | | | | | | app-util |
| `mixScoreItems` (933) | 1 | | | | | | app-takes |
| `installScene` (1059) | | | | | | 1 | app-files |
| `loadStarter` (1107) | | | 2 | | | | app-files |
| `progressUi` (1138) | 13 | 5 | | | | | app-files |
| `download` (1163) | 3 | | | | | | app-files |
| `playableLines` (1188) | 5 | 1 | | | | | app-session |
| `sessionTakeOf` (1202) | 4 | | | | | | app-session |
| `sessionTakes` (1208) | | | | | | 1 | app-session |
| `takesForMix` (1215) | 5 | | | | | | app-session |
| `lineVoiced` (1221) | 2 | | | | | | app-session |
| `reportSessionStart` (1242) | | | | | | 1 | app-session |
| `reportSessionEnd` (1267) | 1 | | | | | 1 | app-session |
| `enterTableRead` (1276) | 1 | | | | | | app-tableread |
| `renderTimeline` (1377) | 3 | | | | | | app-stage |
| `loadTransport` (1429) | 2 | | | | | | app-stage |
| `setPlaying` (1455) | 2 | | | | | | app-stage |
| `attachStageVideo` (1462) | 1 | | | | | | app-stage |
| `onTick` (1494) | 3 | | | | | | app-stage |
| `paintSeek` (1558) | 1 | | | | | | app-stage |
| `openSettings` (1900) | | | 1 | | | | app-settings |
| `boot` (1626), as `window.bootApp` | | | | | | | app.js — read by `start.js:4` and `test/web-guest-mode.test.js:234` |

The other 54 names are private to app.js today (nothing else calls them) and stay private after the split, still global like every name here.

**Load-time reads, the only ordering that matters.** Every use in the table is inside a function that runs after `start.js`, except two: `billing.js:294` calls `$('store-sheet')` inside its IIFE while the page loads, so `$` must be defined by an earlier tag; and `start.js:4` reads `window.bootApp` at load. Inside app.js itself there are two more: `CATEGORY_ORDER = Object.keys(CATEGORY_LABELS)` (209 reads 208) and `window.bootApp = boot` (1909 reads 1626). No other view script reads an app.js name at load — the column-0 initializers in `booth.js` (169, 867), `editor.js` (17, 26, 102) and `account.js` (30, 221, 477, 513) are literals or arrow functions, and `ads.js`/`billing.js` only register listeners.

### 2b. Names app.js calls bare in the other view scripts (for the reconciliation with the booth and collabweb spikes)

All runtime calls, each guarded with `typeof … === 'function'` where the caller may run before or without the file. None is a load-time read, so the sibling splits may name their files freely as long as the names stay bare globals and the `<entry>-<part>.js` prefix convention of §7 is shared.

| defined in | names, with the app.js caller after the split |
|---|---|
| `booth.js` | `wireBooth`, `enterBooth`, `exportWav`, `exportVideo`, `exportReel`, `setCameraOn`, `closeSaveSheet` (app.js `boot`, `wireBackButton`); `enterBooth` (app-tableread `enterTableRead`); `openEntry` (app-takes `renderHistory`, `openMixFromTab`); `onScreeningEnd`, `updateCam` (app-stage `loadTransport`, `onTick`) |
| `collabweb.js` | `stopBoothHeartbeat`, `renderCollabsTab` (app-shell `showView`, `showTab`); `renderLiveStrip` (app-library `renderLibrary`); `onSceneSelected`, `collabEditLock` (app-casting `selectScene`, `renderEditLock`); `mixTakes`, `collabActive` (app-session `takesForMix`, `lineVoiced`; app-stage `loadTransport`); `wireCollab`, `maybeApplyInviteFromUrl` (app.js `boot`) |
| `account.js` | `signedIn` (app-shell `signedInNow`); `collabOn` (app-shell `showTab`); `applyLicenseUi` (app-casting `selectScene`, app-catalog `selectShowcase`); `openSignIn` (app-catalog `openShowcase`; app.js `boot`, the `btn-signin-empty` onclick); `restoreLicense`, `refreshLicense`, `wireAccount` (app.js `boot`); `renderProfileRow` (app-settings) |
| `editor.js` | `enterEditor`, `wireEditor` (app.js `boot`) |

The names the guest-mode test stubs on the vm context (`wireBooth, enterBooth, enterScreening, exportWav, exportVideo, exportReel, setCameraOn, closeSaveSheet`, lines 216-225) are booth's; nothing here changes them.

### 2c. DOM ids app.js owns

122 `$('literal')` calls over 77 distinct ids. `test/web-ids.test.js` sweeps every `web/*.js` non-recursively (line 42), which is the reason the new files stay at the top level. By destination, with the first line of each id:

| file | ids |
|---|---|
| app-shell | `toast` 37; `workflow-header` 46, `btn-camera` 55, `wh-back-label` 56, `wh-back` 57, `wh-title` 58, `wh-stage` 59, `wh-next` 60, `wh-next-label` 62, `wh-action` 65; `btn-share-pack` 177; the computed `` `view-${view}` `` 109 |
| app-library | `library-grid` 349, `lib-empty` 350, `scene-count` 352, `browse-list` 456, `library-none` 467, `browse-detail` 550, `filter-label` 561, `btn-filter` 562, `btn-group` 563, `filter-menu` 566, `cat-chips` 605 |
| app-catalog | `catalog-note` 319, `btn-enter` 321 and 798; the hosted casting call's `browse-detail`, `pack-title`, `pack-subtitle`, `pack-poster`, `pack-byline`, `pack-source`, `pack-meta`, `readme-label`, `pack-readme`, `scene-warn` (738-778) |
| app-casting | `pack-cast` 188, `cast-label` 191; `browse-detail`, `pack-title`, `pack-subtitle`, `pack-poster`, `pack-byline`, `pack-source`, `pack-meta`, `readme-label`, `pack-readme`, `scene-warn`, `btn-enter`, `btn-share-pack`, `btn-edit-pack` (652-712, 862, 882) |
| app-takes | `scene-history-list` 1002, `history-empty` 1005, `takes-list` 1025, `takes-empty` 1032, `recent-takes` 1039, `recent-takes-list` 1040 |
| app-files | `save-progress-slot` 1141, `save-sheet` 1142, `drop-veil` 1792 |
| app-tableread | `tr-cast-row` 1326, `tr-cast` 1327, `tr-progress-text` 1351 |
| app-stage | `sc-time` 1503, `sc-time-strip` 1504; and the computed `` `${stage}-wave`, `-stage-wrap`, `-video`, `-still`, `-stage`, `-progress-fill`, `-caption`, `-nameplate`, `-script`, `-seek-strip` `` (1440-1618), which the sweep skips by design |
| app-settings | `set-difficulty` 1843, `set-difficulty-seg` 1855, `set-theme-seg` 1863, `set-offset` 1866, `set-offset-val` 1868, `set-offset-minus` 1879, `set-offset-plus` 1880, `set-storage` 1883, `set-codec` 1887 |
| app.js | `btn-home` 1661, `btn-all-takes` 1664, `btn-theme` 1665, `btn-settings` 1671, `btn-add-scene` 1672, `file-scene` 1672, `btn-add-scene-empty` 1673, `btn-signin-empty` 1675, `btn-enter` 1677, `btn-delete-scene` 1678, `btn-share-pack` 1679, `btn-edit-pack` 1680, `btn-camera` 1681, `btn-export-video` 1682, `export-menu` 1682, `btn-export-reel` 1683, `pack-search` 1687, `btn-filter` 1688, `filter-menu` 1688, `btn-group` 1689, `btn-to-booth` 1698, `btn-export-wav` 1699, `btn-export` 1702, `btn-voices` 1704, `voices-menu` 1704, `save-sheet` 1766, `workflow-header` 1783, `wh-back` 1783 |

Plus the `querySelectorAll` selectors `#tab-bar .tab` (116, 1662), `.tab-brand` (1663), and `wireBackButton`'s `.filter-menu:not(.hidden)`, `.modal-backdrop:not(.hidden)`, `.modal-close`, `[data-required]` (1764-1779).

**Body flags and events it owns** — the phone layout's contract the brief asks about. `body.in-workflow` (`setHeader`, 50); `body.scene-open` (`setSceneOpen` 168; cleared in `showView` 105 and `showTab` 133); `body.dataset.view` and the `view-changed` CustomEvent (`showView` 107-108; `ads.js:287` and `billing.js:300` listen); `body.light` (1632, 1666, 1858); `body.native`, `no-render`, `no-reel`, `no-plans`, `ios` (`boot` 1638-1657); `localStorage['vc-browse-mode']` (231, 1691). `body.guest` is **not** app.js's: `applyLicenseUi` in `account.js` stamps it; app.js only asks `signedInNow()` (81), which is `typeof signedIn === 'function' && signedIn()`. The phone layout therefore depends on: `isPhone` (159, the one JS breakpoint, matching the CSS's 759px), `setHeader` and `setSceneOpen` in app-shell, `showTab`/`markTab`/`backToLibrary` in app-shell calling `renderLibrary` (app-library), `renderTakesTab` (app-takes), `renderSettingsFields` (app-settings), `selectScene` (app-casting) and `renderCollabsTab` (collabweb) at runtime, and `boot` (app.js) for the `ios`/`native` flags. The session gate depends on: `signedInNow` (app-shell) → `canDownload` (app-catalog) → `refreshEntryCta`/`openShowcase`/`catalogDoor` (app-catalog) → `openSignIn(door)` (account.js), and on `boot`'s `btn-signin-empty` onclick (1675), the only other door; `boot`, `showView`, `showTab`, `backToLibrary` and `renderLibrary` never call it, which the test pins by name (§2e).

### 2d. Network and services it owns

No `Api.supa` call and no token: app.js never talks to Supabase. It fetches the public catalog index and zips (`fetchShowcaseIndex` 267; `openShowcase` 803 through `fetchWithProgress` 839) and the dev loop's starter set (`loadStarter` 1109, 1114), all through `Showcase.*Url`. It reports, fire-and-forget: `Report.imported` (1075), `Report.share('take', …)` (897), `Report.recordingStart`/`recordingFinish` (1249, 1269). It reads `Api.hasRenderService()`/`hasReelRender()` (1639, 1644) and `Flags.resolveAll(null)` (1631), and drives `Store`, `PackSource`, `Codecs`, `Engine`, `Schedule`, `Transport`, `Recorder`, `Native`, `Takes`, `Score` and `Ads` — 85 `window.X.method` call sites over 57 methods, none at load.

### 2e. Strings the tests read

| test | what it reads today | after the split |
|---|---|---|
| `test/web-guest-mode.test.js:229-233` | runs `web/app.js` (with `account.js`, `collabweb.js`, `lib/report.js` and four `src/` modules) in one vm context, then `ctx.bootApp()` (234) | the twelve app files in tag order — §7 |
| `:443-460` | `fnBody(app, 'boot' \| 'showView' \| 'showTab' \| 'backToLibrary' \| 'renderLibrary')` must not contain `openSignIn(` outside `.onclick =` lines | `boot` stays in app.js; `showView`, `showTab`, `backToLibrary` are app-shell's; `renderLibrary` is app-library's; the test scans the group's concatenation — §7 |
| `:448` | app.js contains no `locked()` | over the group |
| `:463-465` | app.js never calls `openSignIn()` with no door | over the group |
| its vm evaluations: `run('state.view')`, `showView(…)`, `showTab(…)`, `backToLibrary()`, `enterTableRead()`, `addFiles([…])`, `selectShowcase(…)`, `openShowcase(state.showcaseEntry)`, `state.showcaseEntry`, `state.pack` | bare names in the context | unchanged: same names, same context |
| `test/ios-no-plans.test.js:151` | `web/app.js` matches `classList.toggle('no-plans', window.Native.isNative() && !window.Native.purchases.available())` | still true: the line is `boot`'s (1652) and `boot` stays in app.js |
| `test/web-ids.test.js:42-47` | every `$('literal')` in every `web/*.js` | unchanged coverage: the new files are `web/*.js` |
| `test/build-web.test.js:35-41` | every `<script src>` in the built page exists and is stamped | covers the new tags automatically |
| `test/fonts.test.js:144` | `web/booth.js` only | not app.js's |

## 3. Responsibility map

| lines | block(s) | kind |
|---|---|---|
| 1-7 | head comment, `'use strict'` | — |
| 8-13 | `$`, `VIEWS`, `TABS` | constants |
| 14-33 | `state` | state |
| 34-160 | `/* chrome */`: `toast`, `setHeader`, `signedInNow`, `showView`, `markTab`, `showTab`, `goHome`, `backToLibrary`, `isPhone` | orchestration: which screen is up |
| 161-162 | `SHARE_PATH` | constant |
| 163-184 | `setSceneOpen` | orchestration: the pushed Scene screen |
| 185-203 | `renderPackCast` | sub-component of the casting call |
| 204-224 | `/* the library */`, `CATEGORY_LABELS`/`ORDER`/`PATHS`, `catIcon` | constants + pure utility |
| 225-257 | `libState`, `sourceLine`, `matchesFilters`, `loadLibrary` | state + pure predicates + IO (`Store.listScenes`) |
| 258-330 | `fetchShowcaseIndex`, `showcaseEntries`, `canDownload`, `catalogDoor`, `refreshEntryCta`, `matchesShowcaseFilters` | service (fetch) + the guest rule + screen: the Catalog |
| 331-360 | `bestScoresByScene`, `renderLibrary` | screen: the Scenes tab |
| 361-454 | `browseRow`, `packMeta`, `groupHeader` | sub-component: a rail row |
| 455-557 | `renderBrowse` | screen: the rail |
| 558-629 | `renderFilterUi`, `filterLabel` | sub-component: the filter dropdown and chips |
| 630-639 | `clearDetail` | screen |
| 640-724 | `/* the casting call */`, `selectScene` | screen: the casting call (installed scene) |
| 725-856 | `selectShowcase`, `openShowcase`, `fetchWithProgress` | screen + service: the hosted casting call and its download |
| 857-875 | `renderEditLock` | screen: the casting call |
| 876-904 | `/* share */`, `shareScene` | screen + IO (`Store.sceneTakeBytes`, `download`) |
| 905-923 | `sourceBits`, `sceneSpan`, `fmtTime` | pure utilities |
| 924-928 | `/* take cards */`, `PLAY_PATH`, `svgOf` | constants |
| 929-945 | `mixScoreItems`, `mixGrade` | pure (over `Score`) |
| 946-955 | `takeDate` | pure utility |
| 956-1055 | `takeCard`, `renderHistory`, `/* the Takes tab */`, `renderTakesTab`, `renderRecentTakes`, `openMixFromTab` | sub-component + three listings |
| 1056-1135 | `installScene`, `addFiles`, `loadStarter` | services/IO: install (`PackSource`, `Codecs`, `Store`, `Report`) |
| 1136-1185 | `progressUi`, `download` | UI widget + IO (blob handover, `Native.exportFile`) |
| 1186-1225 | `/* the table read */`, `playableLines`, `sessionTakeOf`, `sessionTakes`, `takesForMix`, `lineVoiced` | state and rules: the sitting |
| 1226-1235 | `badgeSvg` | pure (SVG) |
| 1236-1275 | `reportSessionStart`, `reportSessionEnd` | service (`Report`) over the sitting |
| 1276-1355 | `enterTableRead`, `castPill`, `renderCast`, `renderTrCounter` | screen: the table read |
| 1356-1357 | `gradeOf` | pure utility (reads settings) |
| 1358-1425 | `dubbedResolver`, `renderTimeline` | sub-component: the script list (both rooms) |
| 1426-1550 | `loadTransport`, `setPlaying`, `attachStageVideo`, `onTick`, `activeLineAt` | services (`Schedule`, `Transport`, `Codecs`, `PackSource`) + the stage (both rooms) |
| 1551-1623 | `/* the seek strip */`, `paintSeek`, `wireSeek`, `wireStage` | sub-component: the seek strip (Web Audio buffers, canvas) and the play control |
| 1624-1790 | `/* boot */`, `boot`, `wireBackButton` | orchestration and wiring |
| 1791-1806 | `wireDrops` | wiring: drops → `addFiles` |
| 1807-1825 | `deleteOpenScene` | screen: the casting call (IO: `Store`) |
| 1826-1905 | `renderSettingsFields`, `openSettings` | screen: Settings |
| 1906-1909 | `window.bootApp = boot` | the entry |

## 4. Proposed tree

**Where.** The top level of `web/`, one classic script per responsibility, named `app-<part>.js`, bare scope exactly as today.

- Not `web/lib/`. That convention is `window.Name = (function () { … })()`: a private scope with a namespace object, for a service with an API. These files are the opposite by design — view code sharing one lexical scope, called bare from six other files (`$` 513 times, `state` 268) and evaluated bare by the vm test (`run('state.view')`). Wrapping them would rewrite every call site and every `fnBody`-scanned signature, which the "moved byte for byte" law forbids (§10 says what a namespace would cost if it is ever wanted).
- Not a `web/app/` directory. `scripts/build-web.js` would ship it unchanged (`copyTree` recurses), but `test/web-ids.test.js` sweeps `web/*.js` non-recursively (line 42), so a subdirectory silently leaves the id sweep. A name prefix gives the same grouping in a flat listing while every `web/*.js` convention (the id sweep, the guest-mode list, `fonts.test.js`, `ios-no-plans`) keeps working untouched.

**Load order** — the `index.html` tags, all between `lib/zipwrite.js` and `booth.js`: `app-shell.js`, `app-util.js`, `app-library.js`, `app-catalog.js`, `app-casting.js`, `app-takes.js`, `app-files.js`, `app-session.js`, `app-tableread.js`, `app-stage.js`, `app-settings.js`, `app.js`. Only two constraints are real (§2a): `app-shell.js` (`$`) before `billing.js`, and `boot` in the same file as `window.bootApp = boot`. The rest mirrors the original file's reading order. The dry run loaded the twelve in tag order and with `app.js` first, without a load-time error, so a later reordering could confuse a reader but not the engine.

**Every file has the same head shape**: one or more `//` lines, then `'use strict';`, then a blank line. Strictness is per script (a file without it runs sloppy), and the move check in §8 recognises exactly that shape.

| # | file | orig. lines | what moves there (name: current lines) |
|---|---|---|---|
| 1 | `app-shell.js` | 182 | the head 1-7 (reworded: this is now the group's first file); `$`, `VIEWS`, `TABS` 8-13; `state` 14-33; `/* chrome */` 34-35; `toast` 36-44; `setHeader` 45-75; `signedInNow` 76-82; `showView` 83-112; `markTab` 113-121; `showTab` 122-141; `goHome` 142-143; `backToLibrary` 144-156; `isPhone` 157-160; `setSceneOpen` 163-184 |
| 2 | `app-util.js` | 55 | `SHARE_PATH` 161-162; `CATEGORY_LABELS`, `CATEGORY_ORDER`, `CATEGORY_PATHS`, `catIcon` 206-224 (kept together and in this order: 209 reads 208 at load); `sourceBits` 905-912; `sceneSpan` 913-918; `fmtTime` 919-923; `PLAY_PATH`, `svgOf` 926-928; `takeDate` 946-955; `gradeOf` 1356-1357 |
| 3 | `app-library.js` | 344 | `/* the library */` 204-205; `libState` 225-233; `sourceLine` 234-241; `matchesFilters` 242-250; `loadLibrary` 251-257; `bestScoresByScene` 331-343; `renderLibrary` 344-360; `browseRow` 361-442; `packMeta` 443-447; `groupHeader` 448-454; `renderBrowse` 455-557; `renderFilterUi` 558-622; `filterLabel` 623-629; `clearDetail` 630-639 |
| 4 | `app-catalog.js` | 205 | `fetchShowcaseIndex` 258-275; `showcaseEntries` 276-284; `canDownload` 285-292; `catalogDoor` 293-310; `refreshEntryCta` 311-324; `matchesShowcaseFilters` 325-330; `selectShowcase` 725-787; `openShowcase` 788-834; `fetchWithProgress` 835-856 |
| 5 | `app-casting.js` | 171 | `renderPackCast` 185-203; `/* the casting call */` 640-641; `selectScene` 642-724; `renderEditLock` 857-875; `/* share */` 876-877; `shareScene` 878-904; `deleteOpenScene` 1807-1825 |
| 6 | `app-takes.js` | 119 | `/* take cards */` 924-925; `mixScoreItems` 929-938; `mixGrade` 939-945; `takeCard` 956-1000; `renderHistory` 1001-1021; `/* the Takes tab */` 1022-1023; `renderTakesTab` 1024-1035; `renderRecentTakes` 1036-1048; `openMixFromTab` 1049-1055 |
| 7 | `app-files.js` | 146 | `installScene` 1056-1078; `addFiles` 1079-1103; `loadStarter` 1104-1135; `progressUi` 1136-1156; `download` 1157-1185; `wireDrops` 1791-1806 |
| 8 | `app-session.js` | 78 | `playableLines` 1188-1191; `sessionTakeOf` 1192-1206; `sessionTakes` 1207-1214; `takesForMix` 1215-1218; `lineVoiced` 1219-1225; `reportSessionStart` 1236-1257; `reportSessionEnd` 1258-1275 |
| 9 | `app-tableread.js` | 82 | `/* the table read */` 1186-1187; `enterTableRead` 1276-1307; `castPill` 1308-1323; `renderCast` 1324-1345; `renderTrCounter` 1346-1355 |
| 10 | `app-stage.js` | 276 | `badgeSvg` 1226-1235; `dubbedResolver` 1358-1372; `renderTimeline` 1373-1425; `loadTransport` 1426-1454; `setPlaying` 1455-1458; `attachStageVideo` 1459-1493; `onTick` 1494-1538; `activeLineAt` 1539-1550; `/* the seek strip */` 1551-1552; `paintSeek` 1553-1597; `wireSeek` 1598-1614; `wireStage` 1615-1623 |
| 11 | `app-settings.js` | 80 | `renderSettingsFields` 1826-1897; `openSettings` 1898-1905 |
| 12 | `app.js` | 171 | a new head; `/* boot */` 1624-1625; `boot` 1626-1756; `wireBackButton` 1757-1790; the closing comment and `window.bootApp = boot` 1906-1909 |
| | total | **1909** | plus eleven new heads of about three lines each: the dry run weighed 1931 lines |

Each range is a block with its leading comment and the blank line after it, so every cut falls on a blank line and never inside a paragraph — which is what lets §8's check say "pure move". File heads, one line each, as used in the dry run:

- `app-shell.js` — The shell: `$`, the view list, `state`, and the chrome (toast, the workflow header, `showView`/`showTab`, the pushed Scene screen). Its head should also say that `state` grows two fields elsewhere: `state.flags` (`boot`, app.js 1631; account.js) and `state.license` (account.js).
- `app-util.js` — Vocabulary, icons and formatters shared by every screen: the category set, the SVG paths, `fmtTime`, `takeDate`, `gradeOf`.
- `app-library.js` — The Scenes tab: the rail, its two filter dimensions, the rows, the dropdown and the chips.
- `app-catalog.js` — The Catalog: the hosted index, the guest rule and its door, the hosted casting call, the download.
- `app-casting.js` — The casting call for an installed scene: the detail panel, its cast pills, the edit lock, Share, Delete.
- `app-takes.js` — Take cards and the three places they are listed: Your takes, the Takes tab, the recent three.
- `app-files.js` — Files in and out: install a `.take`/`.zip` (picker, drop, the starter set); hand a blob back (`download`, the progress card).
- `app-session.js` — A sitting: which lines can play, which takes are this session's, and the operator's log's two rows.
- `app-tableread.js` — The table read: the session starts, the cast pills, the counter.
- `app-stage.js` — What the two rooms share: the script list, the transport load, the picture, the tick, the seek strip, the play control.
- `app-settings.js` — Settings: one renderer for the desktop controls and the phone's segmented ones.
- `app.js` — The entry: `boot` wires every handler and lands the first screen; Android Back; `window.bootApp`.

### Line coverage (sums to 1909)

| lines | file | count |
|---|---|---|
| 1-160 | app-shell | 160 |
| 161-162 | app-util | 2 |
| 163-184 | app-shell | 22 |
| 185-203 | app-casting | 19 |
| 204-205 | app-library | 2 |
| 206-224 | app-util | 19 |
| 225-257 | app-library | 33 |
| 258-330 | app-catalog | 73 |
| 331-639 | app-library | 309 |
| 640-724 | app-casting | 85 |
| 725-856 | app-catalog | 132 |
| 857-904 | app-casting | 48 |
| 905-923 | app-util | 19 |
| 924-925 | app-takes | 2 |
| 926-928 | app-util | 3 |
| 929-945 | app-takes | 17 |
| 946-955 | app-util | 10 |
| 956-1055 | app-takes | 100 |
| 1056-1185 | app-files | 130 |
| 1186-1187 | app-tableread | 2 |
| 1188-1225 | app-session | 38 |
| 1226-1235 | app-stage | 10 |
| 1236-1275 | app-session | 40 |
| 1276-1355 | app-tableread | 80 |
| 1356-1357 | app-util | 2 |
| 1358-1623 | app-stage | 266 |
| 1624-1790 | app.js | 167 |
| 1791-1806 | app-files | 16 |
| 1807-1825 | app-casting | 19 |
| 1826-1905 | app-settings | 80 |
| 1906-1909 | app.js | 4 |
| | **total** | **1909** |

Per file: shell 182, util 55, library 344, catalog 205, casting 171, takes 119, files 146, session 78, tableread 82, stage 276, settings 80, app.js 171 = 1909. The dry run's cutter assigned every line exactly once (it counted 1909 covered, none twice, none missed) and every piece parsed under `node --check`.

## 5. The entry point afterwards

`web/app.js` stays at the same path with the same tag text (`<script src="app.js">`), holding `boot` (1626-1755), `wireBackButton` (1757-1789) and, as its last line, `window.bootApp = boot;` unchanged. It keeps §2's contract because:

- **Same bare names.** Every name in §2a keeps its spelling and body; the split adds no name and renames none (the page-wide sweep for duplicate top-level declarations in §8 printed nothing on the dry run). A name that changed file is the same global binding, reachable by the same callers.
- **`window.bootApp` unchanged.** It is the one `window.*` assignment app.js makes, and it stays in app.js, in the same file as `boot`, because it reads `boot` at load and a function declared in a later tag does not exist yet.
- **Same tag path, same position relative to the other view scripts.** `app.js` is still before `booth.js`, `editor.js`, `account.js`, `ads.js`, `billing.js`, `collabweb.js` and `start.js`. What changes is that it is now the last of its own group rather than the first view script on the page: the eleven `app-*.js` tags are inserted before it. That is the position the entry earns — it is the orchestrator and everything it wires is above it — and it is what keeps `fnBody(app, 'boot')` and `test/ios-no-plans.test.js:151` reading `web/app.js` unchanged. If Eric would rather `app.js` keep the first slot, the shell has to share the file with boot (§6, the alternative).
- **`start.js` unchanged.** Its `window.bootApp()` at load (`start.js:4`) still finds the function. Its comment ("once both files are evaluated") goes stale in wording only (§7).
- **The closing comment** (1906-1908) stays true with one edit: "handlers that booth.js defines" becomes "handlers that the other view scripts define".

## 6. Where 250 lines would force an artificial boundary

Eric's rule (2026-09-15): 250 is where a file gets looked at, not a cap; a cohesive unit stays whole, and the larger it is the harder the look. Two proposed files are over it, and one alternative would be.

| file | honest size | why it stays whole | the seam, if Eric wants it cut anyway |
|---|---|---|---|
| `app-library.js` | 344 | One screen: the rail's model (`libState`, its two predicates, `loadLibrary`, `bestScoresByScene`) and its render (`renderLibrary`, `renderBrowse`, `clearDetail`) with the two components only it uses. Everything here reads and writes the same `libState` and re-renders the rest (`renderFilterUi` → `renderBrowse`; `renderBrowse` → `selectScene`/`selectShowcase`/`clearDetail`). | Two clean cuts, neither of which shatters a function: `renderFilterUi` + `filterLabel` (558-629, 72 lines: the dropdown and the phone's chips, with their own five ids) → `app-filters.js`, leaving 272; and `browseRow` + `packMeta` + `groupHeader` (361-454, 94 lines: one rail row, a pure DOM builder) → `app-rows.js`, leaving 178. Three files of 178/94/72 for one screen is the cost. Recommendation: one file. |
| `app-stage.js` | 276 | What both rooms share, coupled through the DOM it drives: `onTick` moves the progress fill, the still, the caption, the nameplate and the script's active row (1527-1535), which `renderTimeline` builds; `loadTransport` paints the strip and installs the tick; `wireSeek`/`wireStage` bind the same elements. `booth.js` reaches into six of its names. | The script list (`badgeSvg`, `dubbedResolver`, `renderTimeline`: 1226-1235 + 1358-1425, 78 lines) → `app-script.js`, leaving 198. It is a real component, but the tick scrolls its rows, so a reader following a screening-room bug opens both. Recommendation: one file. |
| `app.js`, first-tag alternative | 353 | Not proposed. It is what `app.js` weighs if it must stay the **first** view tag: the shell (182) cannot follow a file that reads `$` at load, and `window.bootApp = boot` cannot precede `boot`, so a first-tag `app.js` holds shell + boot — two units in one file. | Listed so the §5 choice is Eric's: entry-last (proposed, 171) or entry-first (353). |

Under the threshold and deliberately small, so nobody wonders: `app-util.js` (55 — the formatters and icons every screen reads, a home a reader can guess), `app-session.js` (78 — the desktop's `sessionTakeOf` rule, which `booth.js` reaches into eleven times; the model of a sitting, not a screen), `app-tableread.js` (82) and `app-settings.js` (80), one screen each. Merging any of them into a neighbour would put two responsibilities in one file to save a tag.

## 7. Build and packaging touch points

| where | change |
|---|---|
| `web/index.html` | Eleven new tags between line 833 (`lib/zipwrite.js`) and the existing `app.js` tag (834), in the §4 order; `app.js`'s own tag is untouched. Every name they define is read at load only by `billing.js:294` (`$`) and `start.js:4` (`window.bootApp`), both later on the page, so "before every script that reads their names at load time" holds with room. Keep the group contiguous and prefixed: the tests below select it by tag. |
| `scripts/build-web.js` | **No change.** `copyTree` (77-86) copies every non-dot file in `web/`, so the new files ship; `sharedModules` (99-103) matches only `../src/` and `../renderer/` tags, so they are not "shared modules"; the tree check (249-252) verifies each new `src="app-…js"` exists in the build; `stampAssets` (135-143) stamps every one with its own digest. That stamp is the whole answer to the Cloudflare-proxied cache on my.badtakes.io: a new page can only resolve `app-shell.js?v=…` to the bytes it was built with, and `app.js`'s own stamp changes because its bytes do. `firebase.json`'s `max-age=600, stale-while-revalidate=86400` rule needs nothing. |
| `test/build-web.test.js` | **No change.** Lines 35-41 iterate every `<script src>` in the built page and assert existence and stamp; the `stamped > 30` floor only rises. |
| `test/web-ids.test.js` | **No change**, because the files are `web/*.js`. (Under `web/app/`, `dollarCalls` at line 42 would need a recursive walk.) Update the comment at line 17 (`$` is app-shell's now). |
| `test/web-guest-mode.test.js` | **Changes, made once in Phase 2's first PR** so no later PR needs any. (a) Lines 229-233: replace the hard-coded `['web', 'app.js']` entry with the app group read from the page's own tags, in order: `const VIEW_SCRIPTS = [...INDEX_HTML.matchAll(/<script src="([^"./][^"/]*\.js)"><\/script>/g)].map((m) => m[1]);` and `const group = (entry) => VIEW_SCRIPTS.filter((f) => new RegExp('^' + entry + '(-[a-z]+)?\\.js$').test(f));`, then `...group('app').map((f) => ['web', f])` where `['web', 'app.js']` was, keeping `account.js` and the collab group after it as today. (b) Lines 444 and 448-465: `const app = group('app').map((f) => read('web', f)).join('\n');` in place of `read('web', 'app.js')`; `fnBody` finds each function by name in the concatenation (names are unique page-wide) and the `openSignIn()` / `locked()` scans run over the same string. The dry run ran exactly these scans over the twelve files and they pass. The same `group()` serves the booth and collabweb splits if their files follow the `<entry>-<part>.js` prefix — **the one naming convention the three plans must share**, and the reconciliation point with Marfona's spikes. Also refresh the header comment at line 8. |
| `test/ios-no-plans.test.js:151` | **No change** while `boot` stays in `app.js`. If boot ever moves, this regex moves with it. |
| `test/fonts.test.js` | **No change** for app.js (it scans `web/booth.js`). |
| CSP (`index.html:21`) | **No change**: `script-src 'self'` covers same-origin tags. |
| `.github/workflows/deploy-web.yml` | **No change**: triggers on `web/**` (28, 40). `deploy-render.yml` lists four `web/lib` files for the container (211-214); no view script is in it. |
| `scripts/build-mobile.js` | **No change**: it calls the same `build()` and injects the Capacitor tag before `lib/native.js` (56-88); `mobile/www` and `mobile/ios/App/App/public` are gitignored copies regenerated by `npm run mobile:build`. |
| `scripts/web-server.js`, `web:local`, `render-proxy.js` | **No change**: the dev server serves the repo root by extension (66), with no allowlist. |

**Doc lines that go stale** — not build touch points; each is fixed in the PR that moves the code it names:

- `web/README.md:288` — the layout table's `app.js` row becomes twelve rows; `:293` "calls bootApp() last, once both files are evaluated" → "every view script".
- `web/CLAUDE.md:67` — "`canDownload` and `openShowcase` in `app.js`" → `app-catalog.js`.
- `web/index.html:699` — "`web/app.js:1633-1640`" → name it (`wireBackButton` in `web/app.js`); `:132` and `:740` stay true (`no-plans` and `wireBackButton` are `boot`'s and app.js's).
- `web/lib/native.js:179` and `docs/MOBILE-STORES.md:185` — "body.no-plans (web/app.js)": still true.
- `test/web-ids.test.js:17` and `test/web-guest-mode.test.js:8` — comments naming the file.
- `scripts/web-fixtures.js:11` — "`canDownload`/`openShowcase` in web/app.js" → `app-catalog.js`.
- `web/app.js:1906-1908` — "handlers that booth.js defines" → "that the other view scripts define".
- The dated plans, specs and research under `docs/superpowers/` cite old `web/app.js:NNN` lines (for example `specs/2026-09-14-ios-app-review-guest-mode-design.md:50-51`, `research/2026-09-12-desktop-smoke-surface.md:175-190`). They are records of their date and are left alone.

## 8. Verification recipe

The tier from `.claude/skills/implementing-changes/SKILL.md`: `web/` has no smoke leg, so the proof is the static and vm tests, a build, and a browser pass at two widths. Everything below runs on the exact tree about to be committed, before every PR's commit.

1. `node --test test/build-web.test.js test/web-guest-mode.test.js test/web-ids.test.js test/fonts.test.js test/ios-no-plans.test.js`, then the whole `npm test`.
2. `npm run web:build` — the four refusals (media, a missing module, an unconfigured Supabase config, apiBase/CSP) and the stamping; expect `build-web: dist/web — N files, 14 shared modules, …`.
3. **The move check** (authoritative; the reviewer runs it too). It is order-free by design, because the tag-order concatenation cannot reproduce the original line order (`wireDrops` 1791 and `deleteOpenScene` 1807 sit between boot and Settings in the original and belong to `app-files` and `app-casting`). It splits the base revision's `web/app.js` and the concatenation of the app group (read from `web/index.html`'s tags) into paragraphs (blank-line separated), drops each file's head paragraph (`//` lines + `'use strict';`) and the `/* ---- … ---- */` section headers, and compares the two multisets. A pure move prints "identical"; a changed token, a dropped comment or a re-blank-lined function is printed in full and the exit code is 1. From the repo root, with the last argument the commit before the PR's first change (`main` for a fresh branch):

```bash
node -e '
const fs=require("fs"),cp=require("child_process");
const base=process.argv[1]||"main";
const paras=(s)=>s.split(/\n{2,}/).map(p=>p.trim()).filter(p=>p&&!/^(\/\/[^\n]*\n)*\x27use strict\x27;$/.test(p)&&!/^\/\* -{4,}.*-{4,} \*\/$/.test(p));
const before=paras(cp.execFileSync("git",["show",base+":web/app.js"],{encoding:"utf8"}));
const html=fs.readFileSync("web/index.html","utf8");
const files=[...html.matchAll(/<script src="(app[^"\/]*\.js)"/g)].map(m=>m[1]);
const after=paras(files.map(f=>fs.readFileSync("web/"+f,"utf8")).join("\n\n"));
const count=(xs)=>{const m=new Map();for(const x of xs)m.set(x,(m.get(x)||0)+1);return m;};
const a=count(before),b=count(after);let bad=0;
for(const [p,n] of a) if((b.get(p)||0)!==n){bad++;console.log("MISSING or CHANGED in the split:\n"+p+"\n");}
for(const [p,n] of b) if((a.get(p)||0)!==n){bad++;console.log("ADDED or CHANGED in the split:\n"+p+"\n");}
console.log(files.length+" files: "+files.join(" ")+"\n"+before.length+" paragraphs before, "+after.length+" after: "+(bad?bad+" DIFFERENCES":"identical (pure move)"));
process.exit(bad?1:0);' main
```

   On the dry run it printed `12 files: app-shell.js … app.js` / `134 paragraphs before, 134 after: identical (pure move)` with exit 0; with one parameter renamed in `fmtTime` it printed both paragraphs, `2 DIFFERENCES`, exit 1. Two things it relies on: file heads are `//` lines only (a `/* */` head counts as a paragraph and shows as ADDED), and the `<script src="app…">` regex names the group, so the tags must stay `app.js` and `app-<part>.js`. For a PR that moves one contiguous slice the quick look is a plain diff, for example `diff <(git show main:web/app.js | sed -n '1056,1185p') <(sed -n '4,133p' web/app-files.js)`, which prints nothing when the head is three lines.

4. **No duplicate top-level name on the page**: `cat web/app*.js web/{booth,editor,account,ads,billing,collabweb,start}.js | grep -oE "^(async function|function|const|let) [A-Za-z0-9_$]+" | awk '{print $NF}' | sort | uniq -d` prints nothing. (A `const` declared in two classic scripts is a `SyntaxError` on the second tag and the page dies on its first byte.) Once the booth and collabweb splits land, their files are `web/*.js` too and the glob covers them.
5. **Every piece parses on its own**: `for f in web/app*.js; do node --check "$f" || echo "FAIL $f"; done`.
6. **Load order in a vm**, the cheap TDZ probe (the guest-mode test does the same with real stubs):

```bash
node -e '
const fs=require("fs"),vm=require("vm");
const files=[...fs.readFileSync("web/index.html","utf8").matchAll(/<script src="(app[^"\/]*\.js)"/g)].map(m=>m[1]);
const ctx={localStorage:{getItem:()=>null,setItem(){}},console};ctx.window=ctx;ctx.self=ctx;ctx.document={};vm.createContext(ctx);
for(const f of files) vm.runInContext(fs.readFileSync("web/"+f,"utf8"),ctx,{filename:f});
console.log(files.join(" "),"| bootApp:",typeof ctx.bootApp,"| $:",typeof vm.runInContext("$",ctx));'
```

7. **The browser**, `npm run web` (after `npm run web:fixtures` once; `web:seed` runs itself). At desktop width: the library → a rail row → the casting call → Enter scene → Record ›; back with Exit; Share; Delete; Settings (⚙); the Takes tab through "All takes"; drop a `.take` onto the window. At a 375px viewport (the Browser pane's mobile preset): the tab bar; a rail tap pushing the Scene screen (`body.scene-open`, the Casting-call header with Share); Scenes ← back; Enter scene into the table read (`body.in-workflow`, the tab bar gone); Settings as a tab with the segmented controls. Signed out: a Catalog row marked for accounts reads "Sign in to download" and its button opens sign-in with the catalog door, and Not now lands back on that entry.
8. `wc -l web/app*.js` totals §4's 1909 plus the heads (1931 on the dry run), and `git diff --stat` shows `web/app.js` shrinking by exactly what the new files gained, minus heads.

**Repeating the dry run.** The scratch cutter used for this spike is not committed anywhere and should not be: Phase 2 makes the cuts by hand from the §4 ranges and lets steps 3-6 be the proof. If a helper is wanted it is thirty lines — read `web/app.js`, write each file as its ranges joined under a head, assert every line landed once — and a scratch directory, never the tree.

## 9. Phase 2 order

Eric's order, one branch and PR per step, each leaving `npm test`, `npm run web:build`, the move check (against `main`) and the browser pass green. The shell's tag goes first in the group; every other new tag goes immediately **before** `app.js`'s, so the group ends in the §4 order. Each PR rewrites the stale doc lines that name what it moved (§7) and touches nothing else. The test changes happen once, in PR 1, because the check and the vm test read the tag list from then on.

| PR | moves | lines | notes |
|---|---|---|---|
| 1 | **`app-util.js`** (shared constants and pure utilities) **and the test harness** | 55 | The one PR with test edits: `test/web-guest-mode.test.js` reads the app group from the tags (§7); `web/README.md`'s layout table grows its first new row. Proves the mechanism on the smallest file. |
| 2 | **`app-shell.js`** (state and chrome) | 182 | First tag of the group — it must precede `billing.js`'s load-time `$`. `fnBody(…, 'showView' \| 'showTab' \| 'backToLibrary')` now finds them in the concatenation: no test edit, thanks to PR 1. Fix `test/web-ids.test.js:17`'s comment. |
| 3 | **`app-session.js`** and **`app-files.js`** (state and services) | 78 + 146 | Two files, one PR: both are what the rooms consume rather than screens. `wireDrops` leaves boot's neighbourhood. |
| 4 | **`app-library.js`** | 344 | The rail. `renderLibrary` leaves app.js and the guest-mode scan follows it through the group. If Eric picks the §6 seams, `app-filters.js`/`app-rows.js` land in this PR. |
| 5 | **`app-catalog.js`** | 205 | `canDownload`, `openShowcase`, `catalogDoor`: the guest door. Fix `web/CLAUDE.md:67` and `scripts/web-fixtures.js:11`. The browser pass includes the signed-out Catalog row. |
| 6 | **`app-casting.js`** and **`app-takes.js`** | 171 + 119 | The casting call and the cards it lists. |
| 7 | **`app-tableread.js`** and **`app-stage.js`** | 82 + 276 | The table read and the shared stage; `booth.js`'s six stage names keep resolving. Browser pass: the table read and the screening both play, seek and show the script. If Eric picks the §6 seam, `app-script.js` lands here. |
| 8 | **`app-settings.js`**, and **`app.js` reduced to the orchestrator** | 80 | Settings out; app.js is now boot + Back + `bootApp` (171). Reword app.js's head and closing comment, `web/README.md:293`, `web/index.html:699`. Run the full recipe (§8, 1-8) on the finished tree. |

Eight PRs. Three could fold into their predecessors (3 into 2, 6 into 5, 8 into 7) if review bandwidth is the constraint, at the cost of larger diffs in a file whose shrinkage already confuses git's rename heuristics (§10).

## 10. Risks

1. **Load order and the temporal dead zone.** After the split only two load-time reads cross a file boundary, and the §4 order satisfies both: `billing.js:294` reads `$` inside its IIFE (`app-shell.js` is the first view tag; a reorder that put `billing.js` before it throws `ReferenceError: $ is not defined` at load), and `start.js:4` reads `window.bootApp` (set by `app.js`, always before `start.js`). Inside the group, `CATEGORY_ORDER` (209) reads `CATEGORY_LABELS` (208) at load — keep the three category consts in one file in that order — and `window.bootApp = boot` (1909) reads `boot` at load — keep the assignment in `boot`'s file (a later `let`/`const` is a TDZ; a later `function` is simply not there yet across tags). The dry run loaded the group in tag order and with `app.js` first without error: the group's internal order is fixed for readers, not for the engine.
2. **A `let` two files would both assign.** None. app.js has no top-level `let`; its mutable state is three `const` objects mutated in place (`state`, `libState`, `progressUi`) and one function property (`toast.timer`). The nearest thing is `state.flags` and `state.license`, fields set outside the literal (app.js 1631; account.js) — the shell's head says so.
3. **Duplicate top-level names.** A `const`/`let`/`class` declared in two classic scripts, or a `function` in one and a lexical declaration of the same name in another, is a `SyntaxError` on the second tag and kills the page (the `index.html` comment on `src/scenestats.js` is that lesson). The split introduces no new name; §8's `uniq -d` sweep is the guard for every later PR and must include the booth and collabweb groups once those land.
4. **Strict mode is per script.** A new file without `'use strict';` runs sloppy: no error, and a typo'd assignment becomes a global. Every head carries it, and the move check strips exactly that head shape, so a head written another way shows up as ADDED rather than passing silently.
5. **The tests' string scans.** `fnBody(app, …)` over `read('web', 'app.js')` breaks the moment `showView` moves (PR 2) unless PR 1's tag-derived group is in first. `ios-no-plans.test.js:151` keeps reading `web/app.js` and is right as long as `boot` stays there. `web-ids.test.js` is right as long as the files stay at the top level of `web/`; a later file under `web/app/` would leave the id sweep silently — the class of failure that test exists for.
6. **Git's rename heuristics.** `web/app.js` keeps 171 of 1909 lines, so a PR's `git diff -M` may pair the old `app.js` with `app-library.js` (344) or `app-shell.js` and show the entry as a new file. Cosmetic, but a reviewer reading the diff alone is misled; §8's move check is the review, not the diff.
7. **What `npm run web` in a browser cannot see.** The phone layout needs a 375px viewport (`body.in-workflow`, `scene-open`; `ios` comes from `Native.platform()`, which a browser answers null → the iOS chrome, the neutral one). The iOS shell — `wireBackButton`'s Back walk, `download` through `Native.exportFile`, `no-plans` — shows only under `npm run mobile:build` and the simulator, and the Browser pane blocks `getUserMedia`. Collab against the local stack (`npm run web:local`, two origins as two accounts) is what exercises `onSceneSelected`, `collabEditLock`, `renderLiveStrip`, `mixTakes` and `takesForMix`'s collab branch; the fixture catalog does not. The Catalog's guest door needs an index entry not marked `anonymous` (the vm test seeds one). Report calls are fire-and-forget and visible only in the dashboard.
8. **Cache.** Every tag is digest-stamped, so a returning my.badtakes.io visitor cannot boot the new page against an old `app.js` — the failure #269 fixed. Nothing to do, but a hand-edited `index.html` on the host would ship unstamped tags.
9. **The `state` literal's shape** is read by five files and the vm test; the split moves it and changes nothing in it. Adding a field later still means adding it to the literal in `app-shell.js`, not to whichever file first writes it.
10. **The lib/ question, for the record.** A namespace (`window.App = (function () { … })()`) would give private scope and an API, at the price of rewriting 781 `$`/`state` call sites in six files plus every `fnBody` target and every bare `run('…')` in the vm test. A different ticket, if ever.
