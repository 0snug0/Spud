# BAD-036 spike: splitting `scripts/build-release-notes-art.js`

*BAD-036 · Rooster (05.01, architect) · Phase 1, planning only. No code in BadTakes was changed.*

Line numbers are for `scripts/build-release-notes-art.js` at commit `20d11e1` (1547 lines).
Spans were measured, not read by eye. The file was parsed with acorn (`admin/node_modules/acorn`),
and every top-level statement's range and the top-level names it references were extracted.
The proposed tree was then checked end to end on a **throwaway prototype in the session
scratchpad, never in the BadTakes tree**. A script cut the file along the §4 ranges and added the
requires and exports. The copy was run and compared with a copy of the original. Result: 81 of 81
declarations assigned once, zero source-text differences, and a byte-identical SVG tree and
stdout. The file sizes in §4 are the prototype's measured sizes, not estimates.

**The short version.** This file is not a renderer. It is a deterministic SVG string generator:
three shared layers (palette constants, frame-index math, SVG primitives), twelve independent
illustrations (one per published image), and a 42-line build loop. Rasterizing is done by
`scripts/render-release-notes.js` (Electron plus ffmpeg), which is out of scope. The proposal is
16 files. The largest is 224 lines and none is over 250. The compatibility contract is the bytes
of every SVG it writes, because `docs/release-notes/art.lock.json` digests them.

## 1. The file today

| | |
| --- | --- |
| Path | `/Users/ericlugo/Personal/BadTakes/scripts/build-release-notes-art.js` |
| Lines | 1547. There are 81 top-level statements: 79 named declarations (48 `const`, 29 `function`, 2 `let`) and 2 unnamed statements (the `for…of` build loop at 1530-1546 and the summary `console.log` at 1547). |
| Runtime | Plain Node (`engines.node >=22.14`, package.json:6-8). CommonJS with no `'use strict'`. It requires only `fs` and `path` (19-20). **No Electron, no canvas, no ffmpeg, no child processes, no network, and nothing from `src/`, `web/`, `build/` or `server/`.** |
| What it makes | 12 illustrations as SVG text under `docs/release-notes/<version>/`. One is a still (`0.6.0/free.svg`) and eleven are animations (`<name>.frames/0000.svg…`). At `20d11e1` that is 853 SVG files. |
| What it does not do | Rasterize. `scripts/render-release-notes.js` (265 lines, out of scope) loads each SVG into one offscreen Electron `BrowserWindow`, captures a PNG, and encodes frame directories to GIF with ffmpeg (`palettegen`/`paletteuse`). It skips any output whose source digest still matches `docs/release-notes/art.lock.json`. |
| Invoked by | `npm run release-notes:art` → `node scripts/build-release-notes-art.js` (package.json:50). `npm run release-notes:render` → `npm run release-notes:art && electron scripts/render-release-notes.js` (package.json:51). A person or agent runs these while cutting a release, per `.claude/skills/cutting-a-release/SKILL.md` § "Illustrations before screenshots — always" (256-319; the path itself at 269). |
| Required by | Nothing. Grepping the whole repo (`.github/`, `docs/`, `.claude/`, `test/`, `scripts/`, excluding node_modules/dist/voicepacks) for `build-release-notes-art` finds only prose (§2e). No test, workflow or script `require`s or spawns it. |
| Shape | Header comment 1-18; requires and paths 19-23; palette, type and canvas constants 24-48; a `helpers` banner (motion 50-71, SVG primitives 73-150); twelve illustration sections, each under a `// ---- <version> · <name> ---` banner (151-1504); the build banner at 1506; `ART` 1508-1521; `emit` 1523-1526; the loop 1528-1547. Two irregularities: `free` (0.6.0) sits after the 0.10.0 catalog, and three shared primitives (`tick`, `cross`, `moment`) are declared inside the 0.13.1 section, where `moment` is also used by 0.13.2. |
| Module-scope side effects | **The whole build runs on load** (1528-1547): it `fs.rmSync`s each `.frames` directory, does `mkdirSync` and `writeFileSync` for 853 files, and prints `console.log`. Every line above 1528 is a pure declaration. |
| Change cadence | Every art-bearing release has edited it: created in `652608c` (#154), then `54bd3eb` v0.10.0, `8e9eb1e` v0.11.0, `bc8bdf0` v0.13.0, `09cf23d` v0.13.1, `83e67db` v0.13.2. Each one appends a section and an `ART` row. §4 is shaped around that. |

## 2. Public surface (the compatibility contract)

### 2a. Exports

**None.** There is no `module.exports` (grep `module\.exports\|exports\.` → no hits), and nothing requires the
file. Requiring it would run the build and write into `docs/`, so the refactor adds no exports
and no `require.main` guard (§5).

### 2b. CLI arguments, environment variables, stdin

**None.** `process` is never referenced (grep `process\.` → no hits). `npm run release-notes:render -- --force`
appends `--force` to the *last* command in the script string, so it reaches
`render-release-notes.js:74` (`FORCE`), not this file. The same holds for `BT_NOTES_FORCE` and `BT_FFMPEG`,
which that script reads.

### 2c. stdout and exit code

| Line | When | Source |
| --- | --- | --- |
| `ART <version>/<name>.svg` | per still | 1536 |
| `ART <version>/<name>.frames  <N> frames (<N/FPS, 1 dp>s at 14fps)` (two spaces after `.frames`) | per animation | 1543-1544 |
| `ART done: <stills> still, <anims> animated` | once, last | 1547 |

At `20d11e1` this is 13 lines in `ART` order, ending `ART done: 1 still, 11 animated` (measured on a
copy). Exit code 0 on success. Any throw is uncaught, so Node exits 1 and the `&&` in
`release-notes:render` never starts Electron. No program parses these lines (the render script walks the
directory instead), but they are what the release author reads. A move keeps them byte for byte,
order included.

### 2d. The files it writes: the real contract

- **Root**: `ROOT = path.join(__dirname, '..')`, `OUT = path.join(ROOT, 'docs', 'release-notes')` (22-23).
  It is resolved from the script's own location, not the cwd. That fact decides where `ROOT`/`OUT` may live after the split (§5, §10).
- **Still**: `<OUT>/<version>/<name>.svg`. Today that is only `0.6.0/free.svg`, and it **is tracked in git**
  (`git ls-files docs/release-notes`), so every run rewrites a committed file (identical bytes on a clean tree).
- **Animation**: `<OUT>/<version>/<name>.frames/` is deleted first (`fs.rmSync(…, { recursive: true, force: true })`, 1540),
  then `0000.svg`…`NNNN.svg` are written (`String(f).padStart(4, '0')`, 1542). The directory is gitignored
  (`.gitignore:91-95`: `docs/release-notes/*/*.frames/`).
- **Bytes**: `lines.join('\n') + '\n</svg>\n'` (`emit`, 1525). `head()` (91-99) opens every document with
  `viewBox="0 0 W H" width=… height=…` and an opaque `BG` rect.
- **Consumer**: `scripts/render-release-notes.js`. `walk()` (99) picks `*.svg` and `*.frames` per version
  directory. `sizeOf()` (120) parses the `viewBox` that `head()` writes. Frames are read in sorted name order.
  `digest()` (137) hashes the recipe (`png settle=300`, or `gif fps=14 settle=90 filter=<FILTER>`, 60-72), then
  each source's **basename and bytes**, and compares against `docs/release-notes/art.lock.json`.
  **So every SVG byte and every frame file name is the contract.** One changed byte in a shared
  helper changes a digest, and the next `release-notes:render` re-renders and re-encodes already-published
  GIFs on whatever ffmpeg is present. ffmpeg 8.0 and 8.1 differ by 384 bytes on identical frames (SKILL.md:278-288).
  Measured at `20d11e1`: all 12 lock entries match a fresh generation (§8, check 3).
- **Downstream**: `scripts/sync-releases-repo.js` copies the rasters into the public releases repo's
  `notes/<version>/` (94-100), and published release bodies reference them by raw URL.

### 2e. Who names the path

| Where | What | Kind |
| --- | --- | --- |
| `package.json:50` | `"release-notes:art": "node scripts/build-release-notes-art.js"` | runs it |
| `package.json:51` | `"release-notes:render": "npm run release-notes:art && electron scripts/render-release-notes.js"` | runs it first |
| `.claude/skills/cutting-a-release/SKILL.md:269-272` | "**Author it in `scripts/build-release-notes-art.js`**, which emits SVG into `docs/release-notes/<version>/`…" | **load-bearing instruction**: this is where agents are told to author art |
| `SKILL.md:274-319` | every frame a pure function of its index; lock; PNG/GIF only; palette; GIF size | rules, no path |
| `.gitignore:92` | comment above the `.frames/` pattern | prose |
| `scripts/render-release-notes.js:25` | "see build-release-notes-art.js" | prose |
| `scripts/check-site.js:98` | email illustrations are "drawn (scripts/build-release-notes-art.js)" | prose |
| `test/fonts.test.js:65-68` | a comment explaining why the file is deliberately absent from `STACK_FILES` | prose inside a test; the test never reads the file |
| `docs/superpowers/plans/2026-09-10-jost-font-swap.md:24, 271, 1606, 1645` | defers swapping `DISPLAY` out of Futura to its own re-render change | dated plan |
| `docs/superpowers/specs/2026-09-10-reel-render-in-the-container-design.md:78` | lists it among the Futura surfaces | dated spec |
| `docs/RELEASING.md` | does **not** name it; 454-458 require artwork to be pushed before the body and defer to the skill | n/a |
| `RELEASE-NOTES.md`, every `CLAUDE.md`, `.github/workflows/*` | no hits | n/a |

### 2f. Values duplicated elsewhere (kept where they are)

This file shares no rule with `src/`, `web/` or `server/supabase/functions/_shared/` (no `sceneBucket`,
no `isUnlimited`, and no imports at all). It does hold three values that another file repeats. A move
keeps each copy where it is and unifies none of them:

- `FPS = 14` (48) and `render-release-notes.js:60`. The art's stdout reports seconds at its own FPS, and the renderer encodes at its own.
- The palette is copied from `renderer/styles.css` (dark theme, comment 25-26), and `BG` `#1a1626` is repeated as the render window's `backgroundColor` (`render-release-notes.js:192`).
- The determinism rule is shared in prose with `renderer/reel.js` (header 16-18). No code is shared.

## 3. Responsibility map

### Constants and types
| Lines | What |
| --- | --- |
| 19-23 | `fs`, `path`, `ROOT`, `OUT` (output location; see 2d) |
| 24-36 | palette: `BG`, `SURFACE`, `SURFACE2`, `RAISED`, `INSET`, `AMBER`, `HOT`, `TEXT`, `DIM`, `ONACC` |
| 37-40 | type stacks: `DISPLAY` (Futura first), `BODY`, `MONO` |
| 41-48 | canvas and timing: `W_STILL` 1600, `W_ANIM` 1200, `MARGIN` 48, `FPS` 14 |
| per illustration | `CREATOR_FRAMES` 156 · `ALIGN_FRAMES` 322 · `BED_FRAMES` 384 · `VERSIONS_FRAMES` 458 · `SCORING_FRAMES` 532 · `TAKE_FRAMES` 617 · `CATALOG_FRAMES` 691, `CATALOG_SCENES` 693-700, `CATALOG_PICK` 701 · `COLLAB_FRAMES` 864, `COLLAB_CAST` 866-870 · `CAMSYNC_FRAMES` 991 · `STAGING_FRAMES` 1126, `STAGE_SPLIT` 1130, `QUIT_AT` 1132 · `LISTING_FRAMES` 1303, `START_AT` 1306, `TAKE_AT` 1307, `F_START` 1311, `F_TAKE` 1312 |
| 1508-1521 | `ART`: the ordered registry `{ version, name, frames, draw }` ×12 |

No types (plain JS, no JSDoc typedefs).

### State
| Lines | Binding | Notes |
| --- | --- | --- |
| 1528-1529 | `let stills`, `let anims` | counters, read by the summary line only |

Nothing else is mutable at module scope. `CATALOG_SCENES` and `COLLAB_CAST` are `const` arrays that are only
read. `rng` state lives in a per-call closure. No state crosses illustrations.

### Pure utilities
| Lines | What |
| --- | --- |
| 50-60 | frame-index math: `clamp01` 52, `lerp` 53, `seg` 57 (calls `clamp01`), `ease` 59, `easeOut` 60 |
| 62-71 | `rng`: seeded mulberry32 |
| 73-150 | SVG primitives, all returning strings or string arrays: `esc` 73 (used only by `text`), `text` 75-80, `rect` 82-89, `head` 91-99, `wave` 101-121 (uses `rng`), `playhead` 123-127, `arrow` 129-132, `donut` 134-150 (uses `clamp01`) |
| 1133-1159 | more SVG primitives declared in the 0.13.1 section: `tick` 1134-1141, `cross` 1143-1151 (both used only by `staging`), `moment` 1153-1159 (used by `staging` **and** `listing`) |
| per illustration | private helpers: `catalogCard` 703-726, `camCard` 993-1014, `listProgress` 1314-1339, `listRow` 1341-1358, `listEmpty` 1360-1371 |

### Services and IO
| Lines | What |
| --- | --- |
| 1523-1526 | `emit(dir, file, lines)`: `fs.mkdirSync` + `fs.writeFileSync` |
| 1540 | `fs.rmSync(frameDir, { recursive: true, force: true })` inside the loop |
| 1536, 1543-1544, 1547 | `console.log` |

That is all the IO: no fetch, no child processes, no Electron, no canvas.

### Commands (the illustrations: `draw(f) → string[]`, pure)
| Lines (banner → end) | Function | Output | Frames |
| --- | --- | --- | --- |
| 152-318 | `creator` 158-318 | `0.2.0/creator.gif` | 84 |
| 320-380 | `align` 324-380 | `0.4.0/auto-align.gif` | 56 |
| 382-454 | `bed` 386-454 | `0.5.0/background-bed.gif` | 56 |
| 456-528 | `versions` 460-528 | `0.5.0/versions.gif` | 60 |
| 813-851 | `free` 816-851 | `0.6.0/free.png` (still) | n/a |
| 530-613 | `scoring` 534-613 | `0.9.0/scoring.gif` | 84 |
| 615-682 | `takefile` 619-682 | `0.10.0/take-file.gif` | 60 |
| 684-811 | `catalog` 728-811 | `0.10.0/catalog.gif` | 76 |
| 853-983 | `collabArt` 872-983 | `0.11.0/collab.gif` | 100 |
| 985-1113 | `camsync` 1016-1113 | `0.13.0/cam-sync.gif` | 96 |
| 1115-1132, 1160-1291 | `staging` 1161-1291 | `0.13.1/update-staging.gif` | 104 |
| 1293-1504 | `listing` 1373-1504 | `0.13.2/collab-listing.gif` | 76 |

### Orchestration
| Lines | What |
| --- | --- |
| 1506-1547 | build banner, `ART`, `emit`, counters, `for (const art of ART)` (still → `emit` one file; animation → wipe the frame dir, `emit` each frame), summary |

**Coupling (measured by an acorn identifier walk).** No illustration references another. Every
illustration uses `head`, `rect`, `text` and the palette. The only name shared across illustrations
that is not already a primitive is `moment` (`staging`, `listing`). No function-local binding
shadows a top-level name.

## 4. Proposed tree

### Where it lives

`scripts/release-notes-art/`, beside the entry. This would be the **first subdirectory under
`scripts/`** (today `find scripts -type d` → `scripts` only). The other candidates are wrong:
- `src/` is app code: it is allowlisted into the asar, and `web/` loads `src/` modules by tag.
- `build/` is packaging.
- `docs/release-notes/` is the output tree the render script walks.

### The files

Sizes are measured on the scratchpad prototype. 1698 total = the 1547 original lines, plus 151 lines
of requires, exports and one- or two-line headers.

```
scripts/
  build-release-notes-art.js                 80  entry: header, requires, ROOT/OUT, ART, emit, build loop
  release-notes-art/
    constants.js                             32  palette, type stacks, canvas sizes, FPS
    motion.js                                27  clamp01 lerp seg ease easeOut rng
    svg.js                                  115  esc text rect head wave playhead arrow donut tick cross moment
    illustrations/
      creator.js                            178  0.2.0
      auto-align.js                          70  0.4.0
      background-bed.js                      84  0.5.0
      versions.js                            84  0.5.0
      free.js                                47  0.6.0 (still)
      scoring.js                             95  0.9.0
      take-file.js                           79  0.10.0
      catalog.js                            139  0.10.0
      collab.js                             143  0.11.0
      cam-sync.js                           140  0.13.0
      update-staging.js                     161  0.13.1
      collab-listing.js                     224  0.13.2
```

**Naming.** Each illustration file is named after its output: the `name` in `ART`, which is the
GIF/PNG basename. The published image is what anyone starts from ("fix `update-staging.gif`"). The
function names (`takefile`, `collabArt`, `staging`, `listing`, `align`, `bed`) differ from those
basenames, and two versions carry two images each. **Function names do not change**: a rename is
not a move, and the §8 symbol diff keys on names.

**Conventions for every new module.** CommonJS, no `'use strict'` (the monolith is sloppy mode; see §10).
Each file has a one- or two-line header comment, then `const { … } = require(…)` in the order constants → motion → svg,
with names listed in their original declaration order. Then the moved block, verbatim, including its banner
and leading comments. Then `module.exports = { … }` last, naming only what another file uses.
An illustration exports exactly `{ <X>_FRAMES, <draw> }` (`free.js` exports `{ free }`), and its helpers stay private.

### What moves where

"Moves" gives current line ranges, each starting at the declaration's leading comment or banner.
Blank separator lines between blocks are dropped.

| File | Moves | Requires | Exports |
| --- | --- | --- | --- |
| `release-notes-art/constants.js` (32) | 24-48: comment 24-26, `BG` 27, `SURFACE` 28, `SURFACE2` 29, `RAISED` 30, `INSET` 31, `AMBER` 32, `HOT` 33, `TEXT` 34, `DIM` 35, `ONACC` 36, `DISPLAY` 38, `BODY` 39, `MONO` 40, comment 41-44, `W_STILL` 45, `W_ANIM` 46, `MARGIN` 47, `FPS` 48 | none | all 17 |
| `release-notes-art/motion.js` (27) | 50-71: banner 50, `clamp01` 52, `lerp` 53, comment 54-56 + `seg` 57, `ease` 59, `easeOut` 60, comment 62 + `rng` 63-71 | none | `clamp01, lerp, seg, ease, easeOut, rng` |
| `release-notes-art/svg.js` (115) | 73-150: `esc` 73, `text` 75-80, `rect` 82-89, `head` 91-99, comment 101-102 + `wave` 103-121, `playhead` 123-127, `arrow` 129-132, `donut` 134-150; and 1133-1159: `tick` 1134-1141, `cross` 1143-1151, comment 1153 + `moment` 1154-1159 | constants `{ BG, SURFACE, SURFACE2, RAISED, HOT, TEXT, DIM, DISPLAY, BODY, MARGIN }`; motion `{ clamp01, rng }` | `text, rect, head, wave, playhead, arrow, donut, tick, cross, moment` (`esc` stays private) |
| `illustrations/creator.js` (178) | 152-318: banner 152-155, `CREATOR_FRAMES` 156, `creator` 158-318 | constants `{ SURFACE, SURFACE2, RAISED, INSET, AMBER, HOT, TEXT, DIM, ONACC, DISPLAY, MONO, W_ANIM, MARGIN }`; motion `{ clamp01, lerp, seg, ease, easeOut }`; svg `{ text, rect, head, wave, playhead }` | `CREATOR_FRAMES, creator` |
| `illustrations/auto-align.js` (70) | 320-380: banner 320, `ALIGN_FRAMES` 322, `align` 324-380 | constants `{ SURFACE, AMBER, HOT, TEXT, DIM, DISPLAY, MONO, W_ANIM, MARGIN }`; motion `{ lerp, seg, ease }`; svg `{ text, rect, head, wave }` | `ALIGN_FRAMES, align` |
| `illustrations/background-bed.js` (84) | 382-454: banner 382, `BED_FRAMES` 384, `bed` 386-454 | constants `{ SURFACE, RAISED, INSET, AMBER, HOT, TEXT, DIM, DISPLAY, MONO, W_ANIM, MARGIN }`; motion `{ clamp01, seg, rng }`; svg `{ text, rect, head, wave, playhead }` | `BED_FRAMES, bed` |
| `illustrations/versions.js` (84) | 456-528: banner 456, `VERSIONS_FRAMES` 458, `versions` 460-528 | constants `{ SURFACE, SURFACE2, RAISED, AMBER, HOT, TEXT, DIM, DISPLAY, MONO, W_ANIM, MARGIN }`; motion `{ lerp, seg, ease, easeOut }`; svg `{ text, rect, head, arrow }` | `VERSIONS_FRAMES, versions` |
| `illustrations/free.js` (47) | 813-851: banner 813-814, `free` 816-851 | constants `{ SURFACE, SURFACE2, RAISED, AMBER, TEXT, DIM, DISPLAY, W_STILL }`; svg `{ text, rect, head }` | `free` |
| `illustrations/scoring.js` (95) | 530-613: banner 530, `SCORING_FRAMES` 532, `scoring` 534-613 | constants `{ SURFACE, SURFACE2, INSET, AMBER, HOT, TEXT, DIM, DISPLAY, MONO, W_ANIM, MARGIN }`; motion `{ lerp, seg, ease, easeOut }`; svg `{ text, rect, head, donut }` | `SCORING_FRAMES, scoring` |
| `illustrations/take-file.js` (79) | 615-682: banner 615, `TAKE_FRAMES` 617, `takefile` 619-682 | constants `{ SURFACE, SURFACE2, INSET, AMBER, HOT, TEXT, DIM, DISPLAY, MONO, W_ANIM, MARGIN }`; motion `{ lerp, seg, ease }`; svg `{ text, rect, head }` | `TAKE_FRAMES, takefile` |
| `illustrations/catalog.js` (139) | 684-811: banner 684-689, `CATALOG_FRAMES` 691, `CATALOG_SCENES` 693-700, `CATALOG_PICK` 701, `catalogCard` 703-726, `catalog` 728-811 | constants `{ SURFACE, SURFACE2, RAISED, INSET, AMBER, TEXT, DIM, DISPLAY, BODY, MONO, W_ANIM, MARGIN }`; motion `{ lerp, seg, ease }`; svg `{ text, rect, head, wave }` | `CATALOG_FRAMES, catalog` |
| `illustrations/collab.js` (143) | 853-983: banner 853-862, `COLLAB_FRAMES` 864, `COLLAB_CAST` 866-870, `collabArt` 872-983 | constants `{ SURFACE, SURFACE2, RAISED, INSET, AMBER, HOT, TEXT, DIM, ONACC, DISPLAY, BODY, MONO, W_ANIM, MARGIN }`; motion `{ clamp01, seg, easeOut }`; svg `{ text, rect, head, wave, playhead }` | `COLLAB_FRAMES, collabArt` |
| `illustrations/cam-sync.js` (140) | 985-1113: banner 985-989, `CAMSYNC_FRAMES` 991, comment 993-994 + `camCard` 995-1014, `camsync` 1016-1113 | constants `{ SURFACE, SURFACE2, INSET, AMBER, HOT, TEXT, DIM, DISPLAY, BODY, MONO, W_ANIM, MARGIN }`; motion `{ clamp01, lerp, seg, ease, easeOut }`; svg `{ text, rect, head, wave, playhead, arrow }` | `CAMSYNC_FRAMES, camsync` |
| `illustrations/update-staging.js` (161) | 1115-1132: banner 1115-1124, `STAGING_FRAMES` 1126, comment 1128-1129 + `STAGE_SPLIT` 1130, comment 1131 + `QUIT_AT` 1132; and 1161-1291: `staging` | constants `{ BG, SURFACE, RAISED, INSET, AMBER, HOT, TEXT, DIM, DISPLAY, BODY, MONO, W_ANIM, MARGIN }`; motion `{ clamp01, seg, ease, easeOut }`; svg `{ text, rect, head, tick, cross, moment }` | `STAGING_FRAMES, staging` |
| `illustrations/collab-listing.js` (224) | 1293-1504: banner 1293-1301, `LISTING_FRAMES` 1303, comment 1305 + `START_AT` 1306, `TAKE_AT` 1307, comment 1309-1310 + `F_START` 1311, `F_TAKE` 1312, comment 1314-1322 + `listProgress` 1323-1339, comment 1341-1342 + `listRow` 1343-1358, comment 1360-1361 + `listEmpty` 1362-1371, `listing` 1373-1504 | constants `{ BG, SURFACE, SURFACE2, RAISED, INSET, AMBER, HOT, TEXT, DIM, ONACC, DISPLAY, BODY, MONO, W_ANIM, MARGIN }`; motion `{ seg, ease, easeOut }`; svg `{ text, rect, head, wave, moment }` | `LISTING_FRAMES, listing` |
| `build-release-notes-art.js` (80, stays) | keeps 1-23 (header, `fs`, `path`, `ROOT`, `OUT`) and 1505-1547 (banner, `ART`, `emit`, `stills`, `anims`, loop, summary) | `fs`, `path`; constants `{ FPS }`; one line per illustration (§5) | none |

Every original line is accounted for: 1-23 entry · 24-48 constants · 50-71 motion · 73-150 svg ·
152-318 creator · 320-380 auto-align · 382-454 background-bed · 456-528 versions · 530-613 scoring ·
615-682 take-file · 684-811 catalog · 813-851 free · 853-983 collab · 985-1113 cam-sync ·
1115-1132 update-staging · 1133-1159 svg · 1161-1291 update-staging · 1293-1504 collab-listing ·
1505-1547 entry. The lines in between are single blank separators.

### Every top-level declaration, assigned

81 statements, each in exactly one file. The acorn check reported `decls 81 unassigned [] planned-but-missing []`,
and the §8 symbol diff on the prototype reported `missing 0, added 0, changed 0, defined twice 0`.

| Declaration | Kind | Lines | File |
| --- | --- | --- | --- |
| `fs` | const | 19 | entry |
| `path` | const | 20 | entry |
| `ROOT` | const | 22 | entry |
| `OUT` | const | 23 | entry |
| `BG` | const | 27 | constants.js |
| `SURFACE` | const | 28 | constants.js |
| `SURFACE2` | const | 29 | constants.js |
| `RAISED` | const | 30 | constants.js |
| `INSET` | const | 31 | constants.js |
| `AMBER` | const | 32 | constants.js |
| `HOT` | const | 33 | constants.js |
| `TEXT` | const | 34 | constants.js |
| `DIM` | const | 35 | constants.js |
| `ONACC` | const | 36 | constants.js |
| `DISPLAY` | const | 38 | constants.js |
| `BODY` | const | 39 | constants.js |
| `MONO` | const | 40 | constants.js |
| `W_STILL` | const | 45 | constants.js |
| `W_ANIM` | const | 46 | constants.js |
| `MARGIN` | const | 47 | constants.js |
| `FPS` | const | 48 | constants.js |
| `clamp01` | const | 52 | motion.js |
| `lerp` | const | 53 | motion.js |
| `seg` | const | 57 | motion.js |
| `ease` | const | 59 | motion.js |
| `easeOut` | const | 60 | motion.js |
| `rng` | function | 63-71 | motion.js |
| `esc` | const | 73 | svg.js |
| `text` | function | 75-80 | svg.js |
| `rect` | function | 82-89 | svg.js |
| `head` | function | 91-99 | svg.js |
| `wave` | function | 103-121 | svg.js |
| `playhead` | function | 123-127 | svg.js |
| `arrow` | function | 129-132 | svg.js |
| `donut` | function | 134-150 | svg.js |
| `CREATOR_FRAMES` | const | 156 | illustrations/creator.js |
| `creator` | function | 158-318 | illustrations/creator.js |
| `ALIGN_FRAMES` | const | 322 | illustrations/auto-align.js |
| `align` | function | 324-380 | illustrations/auto-align.js |
| `BED_FRAMES` | const | 384 | illustrations/background-bed.js |
| `bed` | function | 386-454 | illustrations/background-bed.js |
| `VERSIONS_FRAMES` | const | 458 | illustrations/versions.js |
| `versions` | function | 460-528 | illustrations/versions.js |
| `SCORING_FRAMES` | const | 532 | illustrations/scoring.js |
| `scoring` | function | 534-613 | illustrations/scoring.js |
| `TAKE_FRAMES` | const | 617 | illustrations/take-file.js |
| `takefile` | function | 619-682 | illustrations/take-file.js |
| `CATALOG_FRAMES` | const | 691 | illustrations/catalog.js |
| `CATALOG_SCENES` | const | 693-700 | illustrations/catalog.js |
| `CATALOG_PICK` | const | 701 | illustrations/catalog.js |
| `catalogCard` | function | 703-726 | illustrations/catalog.js |
| `catalog` | function | 728-811 | illustrations/catalog.js |
| `free` | function | 816-851 | illustrations/free.js |
| `COLLAB_FRAMES` | const | 864 | illustrations/collab.js |
| `COLLAB_CAST` | const | 866-870 | illustrations/collab.js |
| `collabArt` | function | 872-983 | illustrations/collab.js |
| `CAMSYNC_FRAMES` | const | 991 | illustrations/cam-sync.js |
| `camCard` | function | 995-1014 | illustrations/cam-sync.js |
| `camsync` | function | 1016-1113 | illustrations/cam-sync.js |
| `STAGING_FRAMES` | const | 1126 | illustrations/update-staging.js |
| `STAGE_SPLIT` | const | 1130 | illustrations/update-staging.js |
| `QUIT_AT` | const | 1132 | illustrations/update-staging.js |
| `tick` | function | 1134-1141 | svg.js |
| `cross` | function | 1143-1151 | svg.js |
| `moment` | function | 1154-1159 | svg.js |
| `staging` | function | 1161-1291 | illustrations/update-staging.js |
| `LISTING_FRAMES` | const | 1303 | illustrations/collab-listing.js |
| `START_AT` | const | 1306 | illustrations/collab-listing.js |
| `TAKE_AT` | const | 1307 | illustrations/collab-listing.js |
| `F_START` | const | 1311 | illustrations/collab-listing.js |
| `F_TAKE` | const | 1312 | illustrations/collab-listing.js |
| `listProgress` | function | 1323-1339 | illustrations/collab-listing.js |
| `listRow` | function | 1343-1358 | illustrations/collab-listing.js |
| `listEmpty` | function | 1362-1371 | illustrations/collab-listing.js |
| `listing` | function | 1373-1504 | illustrations/collab-listing.js |
| `ART` | const | 1508-1521 | entry |
| `emit` | function | 1523-1526 | entry |
| `stills` | let | 1528 | entry |
| `anims` | let | 1529 | entry |
| build loop | `for…of` | 1530-1546 | entry |
| summary | `console.log` | 1547 | entry |

Per file: entry 10 · constants 17 · motion 6 · svg 11 · creator 2 · auto-align 2 · background-bed 2 ·
versions 2 · free 1 · scoring 2 · take-file 2 · catalog 5 · collab 3 · cam-sync 3 · update-staging 4 ·
collab-listing 9 = **81**.

### The require graph (acyclic by construction)

```
constants.js            requires nothing
motion.js               requires nothing
svg.js                  -> constants, motion
illustrations/*.js      -> constants, motion (all but free.js), svg      never each other
build-release-notes-art.js -> fs, path, constants (FPS), illustrations/* (12)
```

Nothing requires the entry, and no illustration requires another. `moment` moves to `svg.js`
precisely so that `collab-listing.js` never has to require `update-staging.js`.

## 5. The entry point afterwards

`scripts/build-release-notes-art.js` stays at its path. At 80 lines (measured), it is:

1. Lines 1-20 verbatim: the header comment, `const fs = require('fs');` and `const path = require('path');`.
2. The requires:
   ```js
   const { FPS } = require('./release-notes-art/constants');
   const { CREATOR_FRAMES, creator } = require('./release-notes-art/illustrations/creator');
   const { ALIGN_FRAMES, align } = require('./release-notes-art/illustrations/auto-align');
   const { BED_FRAMES, bed } = require('./release-notes-art/illustrations/background-bed');
   const { VERSIONS_FRAMES, versions } = require('./release-notes-art/illustrations/versions');
   const { free } = require('./release-notes-art/illustrations/free');
   const { SCORING_FRAMES, scoring } = require('./release-notes-art/illustrations/scoring');
   const { TAKE_FRAMES, takefile } = require('./release-notes-art/illustrations/take-file');
   const { CATALOG_FRAMES, catalog } = require('./release-notes-art/illustrations/catalog');
   const { COLLAB_FRAMES, collabArt } = require('./release-notes-art/illustrations/collab');
   const { CAMSYNC_FRAMES, camsync } = require('./release-notes-art/illustrations/cam-sync');
   const { STAGING_FRAMES, staging } = require('./release-notes-art/illustrations/update-staging');
   const { LISTING_FRAMES, listing } = require('./release-notes-art/illustrations/collab-listing');
   ```
3. Lines 22-23 verbatim: `ROOT` and `OUT`.
4. Lines 1506-1547 verbatim: the build banner, `ART` (unchanged text, same order), `emit`, `stills`, `anims`, the loop and the summary.

How it keeps §2:

| Contract | Kept by |
| --- | --- |
| Same path | `package.json:50-51` and `SKILL.md` keep working unchanged |
| Same flags, env, stdin | there are none, before or after |
| Same exports | none; it still runs the build on load and nothing requires it |
| Same stdout and exit code | `ART` text and order unchanged, loop verbatim (measured identical, §8 check 1) |
| Same files and bytes | draw functions and primitives moved verbatim. Measured: 853 SVGs byte-identical, aggregate digest unchanged, lock 12/12 (§8) |
| Output location | `ROOT`/`OUT` stay in this file, so `__dirname` is still `scripts/`. Moved into `release-notes-art/`, `path.join(__dirname, '..')` would resolve to `scripts/` and write into `scripts/docs/release-notes/` |

**Adding art afterwards** means one new `illustrations/<name>.js` exporting `{ <X>_FRAMES, <draw> }`,
plus one require line and one `ART` row in the entry. That is the `cutting-a-release` update in §7.

**Deliberately not done in Phase 2**, since each would change behaviour or shape beyond a move:
- no `require.main === module` guard and no exports from the entry;
- no directory scan of `illustrations/` in place of `ART` (stdout order would follow `readdir`, and a half-written file would be picked up silently);
- no merging of `FPS` with `render-release-notes.js:60`;
- no renames and no `'use strict'`;
- no change to `DISPLAY` (the Jost swap stays its own change).

## 6. Files over 250 lines

**None.** The largest proposed files, with the reason each stays whole:

| File | Lines | Why it stays one file |
| --- | --- | --- |
| `illustrations/collab-listing.js` | 224 | One illustration. `listing()` is 132 lines, plus three private helpers (`listProgress`, `listRow`, `listEmpty`) and five timing constants that only it reads. The constants are the illustration's own timeline, commented against its frames. |
| `illustrations/creator.js` | 178 | One illustration. `creator()` is 161 lines: four beats of one animation branching on `active`, all reading the same frame index and stage geometry. Splitting beats into functions would be a rewrite, not a move. |
| `illustrations/update-staging.js` | 161 | One illustration; `staging()` is 131 lines of the same shape. |
| `illustrations/collab.js`, `cam-sync.js`, `catalog.js` | 143, 140, 139 | One illustration each, with its private card helper or cast table. |
| `release-notes-art/svg.js` | 115 | The primitive set every illustration draws with. It is small, but it is the one file whose edits restyle published art (§10). |

Four draw functions are over 100 lines (`creator` 161, `listing` 132, `staging` 131, `collabArt` 112).
They are noted here for Eric's judgment, and whether to break one into beat helpers is an authoring
choice for whoever next re-renders that image. They are not part of a refactor whose proof is byte identity.

## 7. Build and deploy touch points

| Where | Change | Why |
| --- | --- | --- |
| `package.json` scripts 50-51 | none | the path is unchanged, and Node resolves the new relative requires from the entry |
| electron-builder `build.files` (package.json:65-74) | **none; confirmed outside it** | the allowlist is `main.js`, `preload.js`, `src/**/*`, `renderer/**/*`, `brand/*.png`, `brand/fonts/*`, `package.json`, `node_modules/**/*`. No `scripts/` entry, and neither `extraResources` block names a `scripts/` path. The new directory never ships; do not add it. |
| asar leak check (release.yml Windows job, RELEASING §7) | none | nothing new reaches the app |
| `.github/workflows/*` | none | no workflow names the file or `release-notes`. The path-filtered workflows list specific scripts only (`deploy-render.yml`: `web-api.js`, `render-server.js`; `deploy-site.yml`: `check-site.js`; `deploy-web.yml`: `build-web.js`, `web-seed.js`, `ensure-web-hosting.js`), and `scripts/release-notes-art/**` matches none |
| `scripts/ci-changes.sh` `ignored()` | none | only `scripts/check-site.js` is ignored under `scripts/`, so these PRs report `app=true` and run `ci.yml`'s `test` and `smoke` (Gate requires both), though neither exercises this script. Expect the minutes, and don't widen the ignore list in this change |
| `npm test` glob (package.json:21: `test/**/*.test.js`, `test/**/*.test.mjs`) | none | new files sit outside `test/` |
| eslint / prettier / tsconfig | none | none at the repo root; `admin/` has its own eslint, scoped to `admin/` |
| `.gitignore:91-95` | none | the pattern is `docs/release-notes/*/*.frames/`, and the comment names the entry, which still exists |
| `.claude/skills/cutting-a-release/SKILL.md:269-272` | **update, in the last PR** | new wording: author in `scripts/release-notes-art/illustrations/<name>.js`, register it in `ART` in `scripts/build-release-notes-art.js`, and treat `svg.js`/`motion.js`/`constants.js` as shared, because editing them changes published art (the lock re-renders it). The old sentence still "works", but it sends the next author to write art into the orchestrator. |
| `test/fonts.test.js:65-68` comment | optional prose | the `DISPLAY` constant now lives in `scripts/release-notes-art/constants.js`. When the deferred font swap lands, that is the path it adds to `STACK_FILES`. `DECLARES_A_FACE` would match `const MONO` there, and `font-family` in `svg.js`'s `text()` template names no face, so it passes. |
| `scripts/render-release-notes.js:25`, `scripts/check-site.js:98` comments | none | the entry they name still exists |
| `docs/superpowers/plans/2026-09-10-jost-font-swap.md`, `docs/superpowers/specs/2026-09-10-reel-render-in-the-container-design.md` | leave | dated records |
| `scripts/build-kit.js`, `web/`, `mobile/`, `server/` | none | none reference the script |

## 8. Verification recipe

**Tier.** `implementing-changes`'s tier table has no row for `scripts/`. The refactor touches no `src/`,
no `main.js`/`preload.js`/`renderer/`, no `server/`, no `build.*` config and no `site/`. The art's
contract is bytes, so the proof is checks 1-2 below, check 3 for the lock, and `npm test` as a
floor. No local smoke run is needed (CI will run one anyway, §7), and no packaged build (`scripts/`
never ships).

**Tests that cover the file: none.** `grep -rn "build-release-notes-art\|release-notes-art" test/` finds
only the comment at `test/fonts.test.js:65`. No test requires it or any helper in it, and none can
today, because requiring it runs the build. The only existing guard is `art.lock.json`: on a clean
tree, `release-notes:render` prints `SKIP` for everything if and only if every SVG byte is unchanged.

### Dry run that writes nothing into the tree

The script has no flag or environment variable for its output directory, since `OUT` derives from `__dirname`.
So the dry run copies `scripts/` into a temp root and runs the copy, which writes `$TMP/docs/release-notes/`.
**Do not use `npm run release-notes:art` as the check**: it rewrites the tracked `0.6.0/free.svg` and
the gitignored frames in place.

### Check 1: byte identity, before and after (the gate)

From the PR's worktree:

```bash
git fetch origin
BEFORE=$(mktemp -d); AFTER=$(mktemp -d)
git archive origin/main scripts | tar -x -C "$BEFORE"   # whole scripts/: after PR 1, main's entry requires release-notes-art/
cp -R scripts "$AFTER/"                                  # the tree under test, uncommitted edits included
node "$BEFORE/scripts/build-release-notes-art.js" > "$BEFORE/stdout"
node "$AFTER/scripts/build-release-notes-art.js"  > "$AFTER/stdout"
diff "$BEFORE/stdout" "$AFTER/stdout" && diff -r "$BEFORE/docs" "$AFTER/docs" && echo BYTE-IDENTICAL
(cd "$AFTER/docs/release-notes" && find . -name '*.svg' | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256)
git status --porcelain docs/                             # must print nothing
```

Expected, measured on copies at `20d11e1`: 13 stdout lines ending `ART done: 1 still, 11 animated`, and
853 SVG files. The aggregate digest is `b241ef6dbb4f8f94c7cc8ac8bb68dc68718fead738205868011d5af60f9fa6d3`.
That constant holds only until main gains a new illustration; the `BEFORE` side is the real oracle.
The prototype split printed `BYTE-IDENTICAL` with that same digest.

### Check 2: symbol diff (pure move)

This check proves that every top-level declaration's source text is unchanged and defined exactly once,
whichever file it now lives in. The only statements a move may add are `const { … } = require(…)` and
`module.exports = …`. It compares two `scripts/` directories, so the same command serves both PRs.
It needs acorn, which lives in `admin/node_modules` in the main checkout and is linked into worktrees
by `scripts/worktree-init.sh` since BAD-034. Otherwise point `ACORN=` at any acorn. Save it outside the
repo (for example `$BEFORE/symdiff.js`) and do not commit it:

```js
// node symdiff.js <old scripts/ dir> <new scripts/ dir>
const acorn = require(process.env.ACORN || './admin/node_modules/acorn');
const fs = require('fs'); const path = require('path');
const walk = (d) => (fs.existsSync(d) ? fs.readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(path.join(d, e.name)) : e.name.endsWith('.js') ? [path.join(d, e.name)] : [])) : []);
function side(dir) {
  const map = new Map(); const twice = [];
  for (const file of [path.join(dir, 'build-release-notes-art.js'), ...walk(path.join(dir, 'release-notes-art'))]) {
    const src = fs.readFileSync(file, 'utf8'); const seen = {};
    for (const n of acorn.parse(src, { ecmaVersion: 'latest' }).body) {
      if (n.type === 'VariableDeclaration' && n.declarations.every((d) => d.id.type === 'ObjectPattern' && d.init && d.init.type === 'CallExpression' && d.init.callee.name === 'require')) continue;
      if (n.type === 'ExpressionStatement' && n.expression.type === 'AssignmentExpression' && src.slice(n.expression.left.start, n.expression.left.end) === 'module.exports') continue;
      let key = n.type === 'FunctionDeclaration' ? n.id.name : n.type === 'VariableDeclaration' ? n.declarations.map((d) => d.id.name).join(',') : `<${n.type}>`;
      if (key.startsWith('<')) { seen[key] = (seen[key] || 0) + 1; key += `#${seen[key]}`; }
      if (map.has(key)) twice.push(`${key} in ${path.relative(dir, file)}`);
      map.set(key, src.slice(n.start, n.end));
    }
  }
  return { map, twice };
}
const a = side(process.argv[2]); const b = side(process.argv[3]);
const missing = [...a.map.keys()].filter((k) => !b.map.has(k));
const added = [...b.map.keys()].filter((k) => !a.map.has(k));
const changed = [...a.map.keys()].filter((k) => b.map.has(k) && a.map.get(k) !== b.map.get(k));
const twice = [...a.twice, ...b.twice];
console.log(`old ${a.map.size} declarations, new ${b.map.size}; missing ${missing.length}, added ${added.length}, changed ${changed.length}, defined twice ${twice.length}`);
for (const [label, list] of [['MISSING', missing], ['ADDED', added], ['CHANGED', changed], ['TWICE', twice]]) for (const k of list) console.log(`${label} ${k}`);
process.exit(missing.length + added.length + changed.length + twice.length ? 1 : 0);
```

```bash
node "$BEFORE/symdiff.js" "$BEFORE/scripts" "$AFTER/scripts"
```

Expected output: `old 81 declarations, new 81; missing 0, added 0, changed 0, defined twice 0`, exit 0. That was measured
monolith → prototype, and prototype → prototype. **Negative control:** changing `rect`'s default `rx = 10`
to `11` in the prototype's `svg.js` printed `CHANGED rect` and exited 1. Check 2 names *which*
declaration moved wrong. Check 1 catches anything that changes output, including encoding.

### Check 3: the lock agrees (render would SKIP everything)

This is the real render, still inside a temp root. Run it from the worktree root so `npx` finds Electron:

```bash
E2E=$(mktemp -d)
git archive HEAD docs/release-notes | tar -x -C "$E2E"   # committed GIFs, free.png/.svg, art.lock.json
cp -R scripts "$E2E/"
node "$E2E/scripts/build-release-notes-art.js" > /dev/null
npx electron "$E2E/scripts/render-release-notes.js"      # expect 12 lines starting SKIP, no RENDER, exit 0
cmp "$E2E/docs/release-notes/art.lock.json" docs/release-notes/art.lock.json && echo LOCK-UNCHANGED
```

This spike did **not** launch Electron. It replicated `digest()` (render-release-notes.js:137-146, with the
recipes from 60-72) in plain Node over a fresh generation instead. Result at `20d11e1`: `lock entries 12,
outputs 12, match 12, mismatch 0`, and the same on the prototype's output. Check 1 implies check 3.
Check 3 is for the reviewer who wants to see the lock itself agree.

### Check 4: floor

`npm test`: green, and the pack-test skip count is irrelevant here. It proves nothing about the art,
but the PR must not break it.

## 9. Phase 2 order

The order is Eric's: constants and pure utilities, then services and IO, then commands, then the
entry reduced to an orchestrator.

**PR 1: constants and pure utilities.** Add `release-notes-art/constants.js`, `motion.js` and
`svg.js`, holding the spans in §4 (24-48, 50-71, 73-150, 1133-1159). The entry deletes those
spans and gains three requires. Its `constants` require lists every palette name the still-inline
illustrations use, which is temporary. The entry ends at roughly 1,400 lines. Proof: checks 1, 2 and 4.
This is the PR that relocates the shared primitives, which is the seam where a stray edit would
restyle published art, so it deserves its own review.

**Services and IO: no PR, deliberately.** The only IO is `emit` (4 lines) and the loop's `rmSync` and
`console.log`, and that loop *is* the orchestration. A 4-line `io.js` would be a split by line count.
This is stated here so the step is not silently skipped.

**PR 2: commands, and the entry as orchestrator.** Add the twelve `illustrations/*.js` files, reduce the
entry to §5 (80 lines, `constants` require down to `{ FPS }`), and update `cutting-a-release`
SKILL.md:269-272 (§7). The `test/fonts.test.js:65` comment update is optional. Proof: checks 1-4. The
review is mostly mechanical: twelve independent moves, each one confirmed by check 2.

**One PR instead?** It is acceptable if Eric prefers fewer reviews, since the proof is identical and
mechanical. Two PRs are recommended because they isolate the shared-primitive move from the twelve
leaf moves.

**Timing.** Land both **between releases**. Every art-bearing release has edited this file (§1). A
release branch in flight that adds an illustration would conflict on `ART` and its new section; the
fix is to move that section into its own `illustrations/` file on rebase, then re-run checks 1-2.

**Mechanics.** The prototype was produced by a script that slices the §4 ranges and computes each file's
require list from an acorn identifier walk (property keys and member names excluded). An implementer
can do the same. Headers and require formatting still deserve a human read, and check 2 is the gate either way.

## 10. Risks

- **The entry runs on load.** `require`-ing `scripts/build-release-notes-art.js` writes 853 files into
  `docs/`. Nothing requires it today, and the checks above run a *copy* or *parse* it. Do not add a
  test that requires the entry. The new modules are declarations plus `module.exports` only, with
  no load-time side effects, so a future test can require them safely.
- **`__dirname` moves with the code.** `ROOT`/`OUT` must stay in the entry (§5). Relocated, the art
  lands in `scripts/docs/release-notes/` and the real tree goes stale; check 1's `diff -r` fails on
  the missing `$AFTER/docs`.
- **Require cycles.** There are none by construction (§4 graph). The tempting one is leaving `moment`
  in `update-staging.js` and requiring it from `collab-listing.js`. Illustrations must never require
  each other.
- **Hoisting and TDZ.** In the monolith, function declarations hoist and every `const` is initialised
  before the loop at 1530 calls anything, so declaration order never mattered. After the split each
  module's requires run before its body, and every draw runs only from the entry's loop. The one
  real trap is a *module-scope call*: for example an illustration precomputing a table with
  `rng()`/`seg()` at top level, above or below its require line. None exists today; keep it that way.
  Moved order is preserved inside each file (`seg` after `clamp01`; `START_AT…F_TAKE` before
  `listProgress`).
- **Shared mutable state becoming per-module.** There is none. `stills`/`anims` stay in the entry,
  `rng` state is per call, and `CATALOG_SCENES`/`COLLAB_CAST` are read-only and private to their files.
- **Strict mode.** The monolith is sloppy-mode. Measured: a copy with `'use strict';` prepended writes
  byte-identical output (no implicit globals or legacy octals on any executed path). Phase 2 still adds
  none, so that check 2 remains the whole argument.
- **Template strings are published bytes.** Every SVG byte feeds an `art.lock.json` digest. A reindented
  multi-line `s.push(\`<path …\`)` template, a touched `toFixed`, or a normalised smart quote changes a
  digest. The next render then re-encodes already-published GIFs on whatever ffmpeg is installed
  (8.0 and 8.1 differ, SKILL.md:278-288). 68 lines carry non-ASCII (`— · ’ “ ” … →`), including drawn
  strings at 452, 694-699, 1228-1253 and 1477. Files must stay UTF-8 with LF (today: UTF-8, 0 CR).
  No formatter is configured, so editor format-on-save is the realistic hazard. Check 1 catches all
  of it, and check 2 names the declaration.
- **What the tests cannot see.** No test covers this script. A green `npm test` says nothing about the
  art, and CI's smoke never runs it. After the split, `svg.js` is a visible "shared helpers" module
  that invites tidying, and a tidy there silently restyles every published illustration the next
  time anyone renders, usually mid-release. Filed as a proposal: an `npm test` guard that generates
  the art into a temp root and compares digests with `art.lock.json` (pure Node, milliseconds, no Electron).
- **Pairs that must not be "fixed" in passing.** `FPS` (48) vs `render-release-notes.js:60`; `BG` vs
  `render-release-notes.js:192` and `renderer/styles.css`; `DISPLAY` stays Futura until the Jost plan's
  own re-render change (`docs/superpowers/plans/2026-09-10-jost-font-swap.md:24`), which must not ride
  along with this refactor.
- **Release collisions.** See §9 timing.
- **History.** Plain `git blame` on a moved block shows the move commit. `git blame -C -C` (or
  `git log --follow -M`) recovers authorship. This is cosmetic, but it explains why each moved block
  keeps its banner comment verbatim.
