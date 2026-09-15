# Bad Takes Smoke Walkthrough Renderer Compatibility Contract

Comprehensive inventory of every renderer component evaluated by the `--smoke` walkthrough in `main.js` lines 5916-6724. This document serves as a compatibility contract for splitting `renderer/app.js` into modules—every name listed here must remain accessible to the walkthrough evaluator.

## A. Renderer names the walkthrough evaluates

| Identifier | Kind | main.js Line(s) | Leg(s) |
|---|---|---|---|
| `#adopt-photo-btn` | DOM id | 6206 | home |
| `#booth-progress` | DOM selector | 6302 | screening |
| `#booth-status` | DOM id | 6276 | booth |
| `#btn-camera` | DOM id | 6284 | booth |
| `#btn-camera-state` | DOM id | 6289 | booth |
| `#btn-create-pack` | DOM id | 6202 | home |
| `#btn-delete-pack` | DOM id | 6226 | home |
| `#btn-export` | DOM id | 6426, 6433 | history |
| `#btn-export-reel` | DOM id | 6447 | reel |
| `#btn-filter` | DOM id | 6192, 6196 | home |
| `#btn-home` | DOM id | 6383 | history |
| `#btn-next-line` | DOM id | 6304, 6372 | screening, history |
| `#btn-play-ref` | DOM id | 6252 | booth |
| `#btn-play-take` | DOM id | 6277 | booth |
| `#btn-record` | DOM id | 6257, 6291 | booth |
| `#btn-start-booth` | DOM id | 6232 | tableread |
| `#btn-theme` | DOM id | 6014 | setup |
| `#btn-tr-start` | DOM id | 6249 | booth |
| `#cam-card` | DOM id | 6354 | screening |
| `#cam-video-a` | DOM id | 6362, 6363 | screening |
| `#cam-video-b` | DOM id | 6362, 6363 | screening |
| `#cue-cam` | DOM id | 6290 | booth |
| `#cue-card` | DOM id | 6260, 6261, 6268, 6269 | booth |
| `#creator-step-source` | DOM selector | 6205 | home |
| `#drop-overlay` | DOM id | 6101 | home |
| `#export-menu` | DOM id | 6428, 6433 | history |
| `#filter-menu` | DOM id | 6193 | home |
| `#license-email` | DOM id | 6036 | home |
| `#license-tier` | DOM id | 6070 | home |
| `#mixer` | DOM selector | 6412 | history |
| `#rec-chip` | DOM id | 6261 | booth |
| `#rec-chip-time` | DOM id | 6261 | booth |
| `#record-caption` | DOM id | 6275 | booth |
| `#scene-cast-val` | DOM selector | 6320 | screening |
| `#scene-date` | DOM selector | 6387 | history |
| `#scene-donut` | DOM selector | 6330 | screening |
| `#scene-export-menu` | DOM id | 6394, 6423 | history |
| `#scene-history-list` | DOM selector | 6386, 6387, 6388, 6392, 6394, 6407 | history |
| `#scene-score` | DOM id | 6312 | screening |
| `#scene-score-band` | DOM id | 6316, 6329 | screening |
| `#scene-score-cast` | DOM selector | 6318 | screening |
| `#scene-score-percent` | DOM id | 6315, 6417 | screening, history |
| `#scene-score-takes` | DOM id | 6317, 6418 | screening, history |
| `#score-band` | DOM id | 6274 | booth |
| `#score-donut` | DOM id | 6274 | booth |
| `#score-emoji` | DOM id | 6274 | booth |
| `#score-percent` | DOM id | 6274 | booth |
| `#signin-status` | DOM id | 6023 | home |
| `#stage-wrap` | DOM id | 6237, 6239, 6241, 6246, 6351, 6410, 6434, 6436, 6441, 6452 | tableread, screening, history, reel |
| `#take-score` | DOM id | 6274 | booth |
| `#timeline` | DOM selector | 6322 | screening |
| `#toast` | DOM id | 6100, 6519 | home, reel |
| `#tr-timeline` | DOM selector | 6235 | tableread |
| `#usage-allowance` | DOM id | 6074 | home |
| `#view-activate` | DOM id | 6017, 6018, 6057 | home |
| `#view-booth` | DOM id | 6204, 6214, 6234, 6296 | home, tableread, booth |
| `#view-library` | DOM id | 6215 | home |
| `#view-screening` | DOM id | 6430 | history |
| `#waveform` | DOM id | 6261 | booth |
| `#wh-back` | DOM id | 6212, 6368 | home, history |
| `#wh-next` | DOM id | 6210, 6340 | home, screening |
| `#wh-next-home` | DOM id | 6341 | screening |
| `#wh-next-label` | DOM id | 6342 | screening |
| `#wh-step` | DOM id | 6209 | home |
| `#wh-title` | DOM id | 6208 | home |
| `#workflow-header` | DOM selector | 6288 | booth |
| `applyLicenseStatus` | top-level function | 6021, 6032, 6038, 6066, 6076, 6082 | home |
| `body.classList.contains('flag-off-subscription')` | class check | 6060 | home |
| `body.classList.contains('light')` | class check | 6014 | setup |
| `body.classList.contains('plan-free')` | class check | 6033, 6039, 6071 | home |
| `Credits` | top-level class | 6486 | reel |
| `creditForEntry` | top-level function | 6486 | reel |
| `creditForSchedule` | top-level function | 6486 | reel |
| `creditImage` | top-level function | 6488 | reel |
| `creditLine` | top-level function | 6485 | reel |
| `document.body.classList` | property | 6014, 6033, 6039, 6060, 6071 | home, setup |
| `dubbedCamTimeline` | top-level function | 6467 | reel |
| `entryLineCredits` | top-level function | 6499 | reel |
| `entryWindow` | top-level function | 6500 | reel |
| `enterScreening` | top-level function | 6475 | reel |
| `getComputedStyle` | built-in | 6274, 6330 | booth, screening |
| `innerHeight` | built-in property | 6400, 6431 | history |
| `innerWidth` | built-in property | 6400, 6431 | history |
| `JSON.stringify` | built-in | 6125, 6151, 6260, 6273 | home, booth |
| `JSON` | built-in | 6127, 6152 | home |
| `Object.keys` | built-in | 6458 | reel |
| `Promise` | built-in | 6026 | home |
| `reelLineCredits` | top-level function | 6500 | reel |
| `refreshPacks` | top-level function | 6141, 6165, 6188, 6197 | home |
| `requestAnimationFrame` | built-in | 6026 | home |
| `screeningScene` | global const/let | 6499, 6500 | reel |
| `state` | global const/let | 6109, 6131, 6293, 6350, 6457, 6458, 6465 | home, booth, screening, reel |
| `vc.listSceneRecordings` | vc bridge method | 6350 | screening |
| `vc.listRecordings` | vc bridge method | 6465 | reel |
| `window.dispatchEvent` | window method | 6244, 6247, 6439 | tableread, history |
| `window.vc` | window property | 6465 | reel |

## B. The js() call list

Complete list of all `js(...)` and `await js(...)` calls in order of appearance.

| # | Line | Leg | Gist |
|---|---|---|---|
| 1 | 6014 | setup | Toggle light/dark theme button if needed |
| 2 | 6017 | home | Check sign-in gate is present |
| 3 | 6018 | home | Check sign-in gate is hidden under bypass |
| 4 | 6021 | home | Apply locked license status (free tier test) |
| 5 | 6022 | home | Find active view when locked |
| 6 | 6023 | home | Check reason copy is shown when locked |
| 7 | 6026 | home | Wait for RAF paints to stabilize frame |
| 8 | 6031 | home | Apply free plan, check paid-only hidden |
| 9 | 6036 | home | Check license email displayed |
| 10 | 6037 | home | Apply paid plan status |
| 11 | 6065 | home | Apply plus plan status, check unlimited |
| 12 | 6076 | home | Re-apply locked status after tier tests |
| 13 | 6082 | home | Apply smoke bypass with paid plan |
| 14 | 6090 | home | Simulate drag+drop, check overlay appears |
| 15 | 6100 | home | Check drop toast message |
| 16 | 6101 | home | Check overlay hidden after drop |
| 17 | 6109 | home | Get first pack ID from state |
| 18 | 6119 | home | Count browse-title elements before import |
| 19 | 6126 | home | Report import result to renderer, count after |
| 20 | 6131 | home | Check pack title updated |
| 21 | 6141 | home | Refresh packs list |
| 22 | 6149 | home | Count browse-title elements before dir import |
| 23 | 6152 | home | Report dir import result, count after |
| 24 | 6176 | home | Count browse-title elements before file open |
| 25 | 6179 | home | Count browse-title elements after file open |
| 26 | 6188 | home | Refresh packs list after open |
| 27 | 6192 | home | Click filter button to open menu |
| 28 | 6193 | home | Check filter menu is open |
| 29 | 6194 | home | Click soundboard filter item |
| 30 | 6195 | home | Count soundboard-filtered browse titles |
| 31 | 6196 | home | Click filter button to close menu |
| 32 | 6197 | home | Click 'all' filter to restore full rail |
| 33 | 6202 | home | Click create pack button |
| 34 | 6204 | home | Check Make a scene view is shown |
| 35 | 6205 | home | Count creator card sections |
| 36 | 6206 | home | Check both import pickers present |
| 37 | 6208 | home | Check Make a scene header title |
| 38 | 6209 | home | Check step counter is hidden |
| 39 | 6210 | home | Check Next button is hidden |
| 40 | 6212 | home | Click back button to exit Make scene |
| 41 | 6214 | home | Check view after back |
| 42 | 6215 | home | Verify returned to library view |
| 43 | 6219 | home | Click first pack title to enter casting |
| 44 | 6225 | home | Check delete action on rail rows |
| 45 | 6226 | home | Check detail pane delete is gone |
| 46 | 6227 | home | Check edit button gate matches video |
| 47 | 6232 | tableread | Click start booth button |
| 48 | 6234 | tableread | Check table read view is active |
| 49 | 6235 | tableread | Count timeline rows |
| 50 | 6237 | tableread | Click stage to play |
| 51 | 6239 | tableread | Check playback is rolling |
| 52 | 6241 | tableread | Click stage to pause |
| 53 | 6244 | tableread | Dispatch spacebar to resume |
| 54 | 6246 | tableread | Check spacebar resumes playback |
| 55 | 6247 | tableread | Dispatch spacebar to pause |
| 56 | 6249 | booth | Click start recording button |
| 57 | 6252 | booth | Click play reference audio |
| 58 | 6257 | booth | Click record button |
| 59 | 6260 | booth | Check recording state and chip visible |
| 60 | 6261 | booth | Check recording countdown and card state |
| 61 | 6268 | booth | Check card is in 'after' state |
| 62 | 6273 | booth | Check score donut and band name |
| 63 | 6275 | booth | Check recording caption text |
| 64 | 6276 | booth | Check booth-status line is gone |
| 65 | 6277 | booth | Check take save button is enabled |
| 66 | 6284 | booth | Toggle camera on if not active |
| 67 | 6289 | booth | Check camera button state in header |
| 68 | 6290 | booth | Check camera preview is visible |
| 69 | 6291 | booth | Click record button for camera take |
| 70 | 6293 | booth | Check camera clip was saved to state |
| 71 | 6296 | booth | Helper to find visible view |
| 72 | 6302 | screening | Click last progress dot to jump to end |
| 73 | 6304 | screening | Click next button to enter screening |
| 74 | 6305 | screening | Check view after next click |
| 75 | 6307 | screening | Check view after ffmpeg wait |
| 76 | 6312 | screening | Check scene score panel visible |
| 77 | 6314 | screening | Check scene score readout format |
| 78 | 6318 | screening | Check cast row scores |
| 79 | 6322 | screening | Check line scores in timeline |
| 80 | 6328 | screening | Check longest band name fits in ring |
| 81 | 6338 | screening | Check exit next button visible |
| 82 | 6349 | screening | Check saved scenes exist before play |
| 83 | 6351 | screening | Click stage to play the dub |
| 84 | 6354 | screening | Check camera card visible |
| 85 | 6362 | screening | Check front camera video visible+has src |
| 86 | 6363 | screening | Check camera video rolling (informational) |
| 87 | 6368 | history | Click back button from screening |
| 88 | 6370 | history | Check view after back |
| 89 | 6372 | history | Click next button in booth loop |
| 90 | 6375 | history | Check re-entry into screening |
| 91 | 6379 | history | Check session takes still present |
| 92 | 6383 | history | Click home button to go back |
| 93 | 6385 | history | Check view after home button |
| 94 | 6386 | history | Count scene history rows |
| 95 | 6387 | history | Check history rows have date stamps |
| 96 | 6388 | history | Check camera marker present in history |
| 97 | 6392 | history | Check export buttons on history rows |
| 98 | 6394 | history | Click export menu on scene row |
| 99 | 6396 | history | Check row export menu items |
| 100 | 6398 | history | Check row menu on screen |
| 101 | 6407 | history | Click scene row to open for viewing |
| 102 | 6409 | history | Check screening viewer view active |
| 103 | 6410 | history | Check viewer opens paused |
| 104 | 6411 | history | Check viewer controls hidden |
| 105 | 6416 | history | Check viewer score from sidecar |
| 106 | 6422 | history | Check row menu closed on nav |
| 107 | 6426 | history | Click transport export button |
| 108 | 6428 | history | Check export menu on screen |
| 109 | 6433 | history | Click export button to close menu |
| 110 | 6434 | history | Click stage to play dub |
| 111 | 6436 | history | Check playback rolling |
| 112 | 6439 | history | Dispatch spacebar to pause |
| 113 | 6441 | history | Check spacebar paused playback |
| 114 | 6447 | reel | Check reel export button visible |
| 115 | 6452 | reel | Pause mix if still rolling |
| 116 | 6457 | reel | Get pack folder name |
| 117 | 6458 | reel | Get first line ID from recordings |
| 118 | 6465 | reel | List recordings and update state |
| 119 | 6467 | reel | Check dubbed camera timeline entries |
| 120 | 6475 | reel | Enter live-mix screening mode |
| 121 | 6502 | reel | Click reel export button |
| 122 | 6505 | reel | Poll reel export button disabled state |
| 123 | 6519 | reel | Check toast message if reel missing |

## C. SMOKE_* log lines

Every SMOKE_* token printed by the walkthrough in chronological order.

| Token | main.js Line(s) | Payload | Leg(s) |
|---|---|---|---|
| `SMOKE_THEME` | 6012, 6013 | env var (process.env.SMOKE_THEME === 'light') | setup |
| `SMOKE_GATE` | 6017, 6018, 6022, 6023 | view present / hidden / locked / reason shown | home |
| `SMOKE_PLAN` | 6031, 6036, 6037, 6065 | free hides paid / email / paid shows paid / plus unlimited | home |
| `SMOKE_FLAG` | 6050 | subscription hidden by default | home |
| `SMOKE_DROP` | 6090, 6100, 6101 | overlay shown / toast text / overlay hidden | home |
| `SMOKE_IMPORT` | 6112, 6125, 6130, 6131 | skipped (empty lib) / report JSON / rail row count / pack title | home |
| `SMOKE_IMPORTDIR` | 6151, 6156 | dir report JSON / dir rail row count | home |
| `SMOKE_OPENFILE` | 6180 | file open rail row count | home |
| `SMOKE_FILTER` | 6193, 6195 | menu open / soundboard row count | home |
| `SMOKE_MAKE` | 6204, 6205, 6206, 6208, 6209, 6210, 6214 | view / sections count / pickers / title / step hidden / next hidden / back to view | home |
| `SMOKE_PACKACTIONS` | 6225, 6226, 6227 | rail delete present / detail delete gone / edit gate | home |
| `SMOKE_TR` | 6234, 6235, 6239, 6246 | view active / rows count / playing status / space resumes | tableread |
| `SMOKE_REC` | 6260, 6268, 6273, 6275, 6276, 6277 | rolling state / card state / score details / caption / status gone / take saved | booth |
| `SMOKE_CAM` | 6288, 6290, 6293, 6354, 6362, 6363, 6388 | toggle / preview / clip saved / card visible / video visible / video rolling / history marker | booth, screening, history |
| `SMOKE_VIEW` | 6305, 6307, 6385 | after screenshot click / after wait / after home button | screening, history |
| `SMOKE_SCORE` | 6312, 6314, 6318, 6322, 6328, 6338 | panel / readout / cast rows / line scores / longest fits / exit visible | screening |
| `SMOKE_SCENES` | 6349, 6386, 6387, 6392, 6396, 6398, 6409, 6410, 6411, 6416, 6422, 6428, 6436, 6441 | saved before play / row count / dated / export buttons / menu items / menu on screen / playback view / paused / controls hidden / viewer score / menu closed / menu on screen / rolling / space pauses | screening, history |
| `SMOKE_NAV` | 6370, 6375, 6379 | back to booth / re-screening / session kept | history |
| `SMOKE_REEL` | 6446, 6448, 6453, 6454, 6467, 6471, 6474, 6508, 6511, 6513, 6520 | visible / mode options / cam clip forge / cam entries / scene mode / Layout A/B / file / dimensions / duration / toast (if missing) | reel |
| `SMOKE_CREDIT` | 6485, 6487, 6499 | credit line / strip bytes / per-line count | reel |
| `SMOKE_VIDEO_WAIT` | 6306 | env var (ms to wait for ffmpeg transcode) | screening |
| `SMOKE_REEL_WAIT` | 6503 | env var (ms timeout for reel export) | reel |
| `SMOKE_OK` | 6532 | printed on walkthrough success, exit 0 | reel |
| `SMOKE_FAIL` | 6535 | printed on walkthrough failure, exit 1 | reel |

## D. Env vars and switches the walkthrough reads

Every environment variable and command-line switch evaluated between main.js lines 74–100 and 5916–6724.

| Variable | Lines | Controls |
|---|---|---|
| `process.env.SMOKE_THEME` | 6013 | Light theme walkthrough (default: dark) |
| `process.env.SMOKE_SCOPE` | 6000, 6528 | Stop walkthrough after named leg: `home\|tableread\|booth\|screening\|history\|reel` (default: walk all) |
| `process.env.SMOKE_DIR` | 5980 | Output directory for SMOKE_SHOT PNG files (default: temp dir) |
| `process.env.SMOKE_VIDEO_WAIT` | 6306 | Milliseconds to wait for ffmpeg transcode before screening screenshot (default: 25000) |
| `process.env.SMOKE_REEL` | 6448, 6453, 6474 | Reel export mode: `0` skip reel leg, `a` forge real VP8 cam clip (Layout A), `scene` viewer mode (default: record live takes for Layout B) |
| `process.env.SMOKE_REEL_WAIT` | 6503 | Milliseconds timeout for reel export completion (default: 8 * 60 * 1000 = 480000) |
| `--smoke` | 74 | Command-line flag enabling walkthrough; sets SMOKE=true, triggers all below |
| `app.commandLine.appendSwitch('use-fake-device-for-media-capture')` | 78 | Enable fake mic for headless recording |
| `app.commandLine.appendSwitch('use-fake-ui-for-media-stream')` | 79 | Enable fake camera for headless recording |
| `app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion')` | 95 | Prevent window occlusion throttling |
| `app.commandLine.appendSwitch('disable-backgrounding-occluded-windows')` | 96 | Prevent window backgrounding throttling |
| `app.commandLine.appendSwitch('disable-renderer-backgrounding')` | 97 | Prevent renderer backgrounding throttling |
| `app.setPath('userData', /tmp/bad-takes-smoke)` | 98 | Throw-away user data directory per run (never touches real profile) |

---

**Document Summary:**
- **Total identifiers:** 95 distinct names
- **Total js() calls:** 123 evaluations (90 unique, some multi-use)
- **Total SMOKE_* tokens:** 24 distinct tokens
- **Environment variables:** 6 SMOKE_* env vars + 6 command-line switches
- **Legs covered:** setup, home, tableread, booth, screening, history, reel
