# BAD-036 spike: splitting `scripts/web-api.js`

*2026-09-15 · BAD-036 · Goldrush (05.02, architect) · Phase 1, planning only. No code was changed.*

Line numbers below are for `scripts/web-api.js` at commit `20d11e1` (1005 lines). Spans were
measured, not eyeballed. The file was parsed with acorn (`admin/node_modules/acorn`), and for each
of its 67 top-level statements the parse recorded the start of its leading comment block, its end
line, and which other top-level names its code references, with comments blanked first. The same
parse drives the check in §8.

Eric's rule for this ticket: **250 lines is the point at which a file gets looked at, not a cap.**
§4 splits by responsibility; §6 reports every proposed file over 250 (there are none) and the
largest ones anyway.

The finding that shapes the whole plan: **this file ships in a container that includes files by
name.** The `.dockerignore` excludes everything by default and adds back exactly
`scripts/web-api.js` and `scripts/render-server.js`, and `container/check-image.js` fails the image
build unless the service loads. A new module that the `.dockerignore` does not name is missing from
the image, and nothing checks for that before merge. See §7 and risk 8.

## 1. The file today

| | |
| --- | --- |
| Path | `/Users/ericlugo/Personal/BadTakes/scripts/web-api.js` |
| Lines | 1005. 67 top-level statements: 1 `'use strict'`, 18 `require` statements (5 Node core, 13 `src/` modules binding 22 names), 18 non-require bindings (14 `const`, 4 module-level `let`), 29 function declarations (two more functions are arrow `const`s: `ffmpeg`, `ffprobe`), 1 `module.exports`. 411 lines (41%) are comment-only; that prose explains incidents and must survive the move (§8 F, step 5). |
| Runtime | Plain Node 22 (`.nvmrc`; the image is `node:22-trixie-slim`). CommonJS, no dependencies beyond Node core and first-party `src/`, no build step, no lint config at the repo root. |
| What it is | The web app's backend: a stateless, token-gated render service. It renders the branded `.mp4`, the vertical reel and the creator's edit assembly, plus the dev-only `/dev-account` and `/blob/*` routes. It exports a request handler and never listens itself. |
| Loaded by | **`scripts/web-server.js:22`**, the dev server behind `npm run web`, `npm run web:https` and, via `scripts/web-local.js`, `npm run web:local`. It mounts `handle` under `/api` (web-server.js:90-95). **`scripts/render-server.js:33`** is the container entry: `Dockerfile:102` runs `CMD ["node", "scripts/render-server.js"]` and deploys to Cloud Run as `badtakes-render`. **`container/check-image.js:328`** requires it as the last build assertion. Tests: `test/webapi.test.js:19,619` and `test/render-server.test.js:22`. |
| Not loaded by | `npm run web:live` (`scripts/render-proxy.js` forwards `/api/*` to the deployed container over HTTP and never requires this file), `scripts/build-web.js`, `scripts/build-mobile.js`, the Electron app (`scripts/` is outside `build.files`), `cli/`, `server/`, `admin/`. |
| Imports from `src/` (kept, never copied) | `brand.brandedExportArgs`, `reel.reelExportArgs`, `tools.resolveTool`, `createpack.{probeMedia, runTool, validateSpec, assemblePack}`, `creator.{packAudioMuxArgs, withThreads}`, `zip.zipDir`, `license-config.licenseConfig`, `packs.loadPack`, `rendergate.{gateToken, takePrefixFor, scenePrefixFor, checkNames, checkDigest, originAllowed}`, `renderstore.storeFrom`, `scenecache.{cacheScene, fetchScene, SCENE_OBJECT}`, `mixrender.renderMix`, `reelrender.{renderReelVideo, renderCreditImage, CREDIT_TIMEOUT}`. Lazily, inside a function: `src/renderlocal.localStore` (188) and `../package.json` (676). The shared rules this service depends on already live in `src/` (`rendergate`'s prefix and name rules, `creator.withThreads`'s thread splice, `scenecache`'s digest recheck). The split moves the call sites and adds no copy. |
| Module-load side effects | Only `DEV_STORE_ROOT` (167), which reads `process.env.BT_RENDER_STORE` at require time. Nothing listens and no request is handled until a host calls `handle`. The `web-api — object store: …` log line prints on the first `currentStore()` call, not at load. |

## 2. Public surface (the compatibility contract)

Everything outside the file that reaches into it. A split may change nothing in this section.

### 2a. Exports

`module.exports = { handle, configure, sendFile }` (1005).

| Export | Consumers |
| --- | --- |
| `handle(req, res)` → Promise | `scripts/web-server.js:91` (rejection → 500 `{error}`), `scripts/render-server.js:78` (rejection → 500, or `res.destroy()` if headers were sent), `test/webapi.test.js:19` (over a real `http.Server`). The comment at 144-145 calls it "the container's public contract", which "must not grow options". |
| `configure({ store, now, container })` | `scripts/render-server.js:76` (`{ store: storeFrom(env), container: true }`), `test/webapi.test.js` `afterEach` and per test, `test/render-server.test.js:22,163,170`. Every call resets all three settings; `realStore`'s memo is *not* reset. |
| `sendFile(res, file, type, name)` | `test/webapi.test.js:619`, which pins chunked transfer with no `Content-Length` (the Cloud Run 32 MiB rule). |

### 2b. Routes

Mounting: `handle` strips an optional leading `/api` (884). The dev server only forwards `/api` and
`/api/*`. The container forwards every path, so **both `/status` and `/api/status` answer there**,
and the deployed web app, whose `apiBase` is the `run.app` origin, calls `/session` with no `/api`.
The deploy's candidate probe curls `$PROBE_URL/status`. **The optional prefix is part of the
contract.** Every JSON response carries `Content-Type: application/json; charset=utf-8` and
`Cache-Control: no-store`.

| Route | Request | Responses | Client callers |
| --- | --- | --- | --- |
| any path, `OPTIONS` (898-911) | `Origin` header | 204. For an origin in `BT_CORS_ORIGINS`: `Access-Control-Allow-Origin: <origin>`, `-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS`, `-Allow-Headers: Content-Type`, `-Max-Age: 86400`. Otherwise a bare 204. | browsers cross-origin (production: `https://my.badtakes.io`, `capacitor://localhost`, set in `deploy-render.yml`) |
| every response (891-896) | `Origin` | allowed origin → `Access-Control-Allow-Origin` and `Access-Control-Expose-Headers: X-Branded, X-Lines` | the same |
| `/dev-account`, `/blob/*`, **container mode** (913-916, list 217-220) | any method | 404 `{error:'not found'}`, byte-identical to an unknown path | pinned by `test/webapi.test.js:501,522,559` and `test/render-server.test.js:123` |
| `GET /dev-account` (918, handler 657-689) | none (the host header picks the account) | 404 `{error:'not found'}` unless `BT_WEB_DEV_ACCOUNTS` and `BT_SUPABASE_SERVICE_KEY` are set **and** the Supabase URL is loopback. 502 `{error:'dev account: …'}` on create, sign-in or activation failure. 200 with the `/functions/v1/activate` response body passed through verbatim. **Outside the router's try**, so a throw here is the host's 500. | `web/account.js:193` on a loopback origin only; reads `token, expires_at, email, plan, handle, profile.handle, renews_at, ads, license_id` |
| `GET /status` (920-930) | none | 200 `{ffmpeg: boolean (runs "ffmpeg -version", 3s), supabase: cfg.configured, brand: existsSync(brand/watermark@2x.png)}`. **Outside the try.** | `.github/workflows/deploy-render.yml` candidate step: `jq -e '.ffmpeg == true and .brand == true'`; `web/lib/api.js:37` `status()` (exported, no caller in `web/` today); `test/webapi.test.js:522,587,600`; `test/render-server.test.js:123` (`brand === true`) |
| `PUT /blob/<key>?exp&sig` (937-945) | raw bytes | 404 `{error:'not found'}` unless `currentStore().isLocal`. 200 `{ok:true, bytes}`. 400 `{error:'renderlocal: bad signature' \| '…expired'}` or any store throw. | the signed URLs `src/renderlocal.js:216` `putUrls` mints under `/api/blob` (dev only), PUT by `web/lib/api.js:232` `storagePut` (expects 2xx); `test/webapi.test.js:479` |
| `POST /session` (947-962) | JSON `{token, sceneDigest, names}` ≤ 4 MiB | 401 `{error: gate.reason}`. 500 `{error}` if the store cannot be built. 400 `{error: digest.reason}` / `{error: names.reason}`. 400 `{error}` on unparseable or oversize JSON (thrown inside the try). 200 `{id: randomUUID, needScene: boolean, urls: {name: url}}`. | `web/lib/api.js:55` `session()` ← `renderScene()` :122 ← `web/booth.js:1178` (video), `:1312` (reel), `web/editor.js:332` (assemble); `test/webapi.test.js:137-172,269` |
| `DELETE /session/:id` (969-990), id `[A-Za-z0-9-]+` | JSON `{token}` | 401 / 500 as above; 200 `{ok:true, deleted: number}`. Deletes only `takePrefixFor(licenseId, id)`. | `web/lib/api.js:102` `drop()` has no caller (api.js:143-146; web-api.js:978-983 calls the route operator-only); `test/webapi.test.js:225,247` |
| `POST /session/:id/video` (992-994 → `runRender` 768) | `{token, sceneDigest, window:{start,end}, card:{credit:{names,more}}}` | 401/500 gate. 400 digest. 400 `takePrefixFor` refusal. 400 `{error:'no uploads found for this session'}`. **409 `{needScene:true, error:'this service has not got that scene yet'}`**. 400 `{error: cached.reason}`. 400 `{error}` for any throw before headers. 200 streamed `video/mp4`, `Content-Disposition: attachment; filename="dub.mp4"`, **`X-Branded: 1\|0`**, chunked, no `Content-Length`. A throw after headers destroys the response. | `web/booth.js:1178`; reads `X-Branded` at :1195; `web/lib/api.js:93-97` treats 409 `needScene` as "upload the scene and retry once" |
| `POST /session/:id/reel` | `{token, sceneDigest, duration, videoStart, cam:[{name,at}], model:{hasCam,title,duration,peaks,takeIntervals,captions,credits}, card:{credit}}` | as video; 200 `video/mp4` `"reel.mp4"` | `web/booth.js:1312`; `test/webapi.test.js:522,675` |
| `POST /session/:id/assemble` | `{token, sceneDigest, spec}` | as video, but **no** "no uploads" check; 200 `application/zip` `"edit.zip"`, **`X-Lines: base64(JSON [{slug,lineId}])`** | `web/editor.js:332`; reads `X-Lines` at :342 |
| anything else (996, 964-965) | | 404 `{error:'not found'}` (including the deleted `PUT /session/:id/file/*` and `/frames/*`) | `test/webapi.test.js:469,513` |

Two timing couplings belong to the contract: `EDIT_BUDGET_MS` (84) is 12 minutes because
`web/lib/api.js:51` aborts at `RENDER_TIMEOUT_MS = 20 min` (comment 76-83), and `FFMPEG_TIMEOUT`
(61, 15 min) sits under `deploy-render.yml`'s `--timeout 1800`.

### 2c. Environment variables

| Name | Read at | Meaning |
| --- | --- | --- |
| `BT_RENDER_THREADS` | 105 (call time) | ffmpeg `-threads`; required by `render-server.js`, which prints it |
| `BT_RENDER_STORE` | 167, **at module load** | the dev store's directory; default `os.tmpdir()/bt-render-store` |
| `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | 168, 177, 179; then `storeFrom` | any one set → R2 store (a missing one is named); none set → `src/renderlocal.js` |
| `BT_CORS_ORIGINS` | 234 | comma-separated origin allowlist, no wildcard |
| `BT_RENDER_PULL_MAX_BYTES` | 318 | cap on one render's pulled bytes, default 1 GiB |
| `BT_WEB_DEV_ACCOUNTS`, `BT_SUPABASE_SERVICE_KEY` | 652, 660 | the dev-account guard and admin key (`scripts/web-local.js:131-132`) |
| `BT_SUPABASE_URL`, `BT_SUPABASE_ANON_KEY`, `BT_ACTIVATION_PUBLIC_KEY` | via `licenseConfig({env: process.env, packaged: false})` at 227, 653, 659, 921, **all at call time** | `web:local` overrides; the token trust root (`deploy-render.yml:147` deliberately never sets them). `test/webapi.test.js:27` sets the key *after* requiring the module. |
| `BT_FFMPEG`, `BT_FFPROBE` | via `resolveTool` in `src/tools.js` | tool override; `test/webapi.test.js:587` uses it |
| `BT_CHROME` | via `src/mixrender.js:103` | the browser the mix and credit harness spawn |

The hosts add their own variables: `PORT` (both servers), `--https` (web-server.js:26, which writes
`.web-cert/` under the repo root if it is absent), and `BT_WEB_SEED_OUT` / `BT_WEB_API_BASE` (the
seed that `npm run web` runs first; it does not touch this file).

### 2d. Logs and exit codes

No CLI of its own, no stdin, no `process.exit`. Operators and the handoff doc grep these log lines,
so their text is contract: `console.log('web-api — object store: R2 bucket %s')` (179),
`'web-api — object store: local directory %s (no R2 configured)'` (194),
`console.warn('web-api — the rendered mix has no scene fill: …')` (398),
`'web-api — the end card credit could not be drawn, exporting without it: %s'` (443),
`'web-api — the cached scene %s could not be read (%s); asking the client for it again'` (755),
`` `reel: the page said hasCam=…, ffprobe says …` `` (515).

### 2e. Things that name the path

- **Build and deploy**, all load-bearing: `.dockerignore:14` (`!scripts/web-api.js`), `container/check-image.js:63` (`MUST_EXIST`) and `:328` (require), `.github/workflows/deploy-render.yml:207` (push path filter) and `:196` (comment). `Dockerfile:80` copies `scripts/` whole; the `.dockerignore` decides what is in it.
- **npm scripts**, indirectly: `web`, `web:https`, `web:local` (through `scripts/web-server.js`). `web:live` does not load the file.
- **Skills**: none. `grep -rn web-api .claude/` finds nothing.
- **Comments and docs that name a function's location** (these go stale when the function moves): `src/creator.js:261,265` (`threadCount`), `src/renderlocal.js:2,20,38` (the lazy require and the sweep), `src/renderstore.js:8`, `src/rendergate.js:15,41,73`, `scripts/render-server.js:6,9,13,101` (`UNROUTED_IN_CONTAINER`, `currentStore`, `threadCount`), `scripts/web-local.js:13`, `scripts/web-seed.js:43,58`, `.dockerignore:17,23`, `container/check-image.js:21`, `test/render-server.test.js:6,78`, `test/rendergate.test.js:59`.
- **Comments and docs that name only the path** (still true afterwards, because the entry keeps it): `web/CLAUDE.md:146`, `web/README.md:65,375,398`, `web/lib/api.js:1`, `web/editor.js:7`, `web/account.js:180`, `web/lib/report.js:31`, and the generated copies under `mobile/www/` and `mobile/ios/App/App/public/` (both gitignored).
- **Historical plans and specs** citing old line numbers: `docs/superpowers/plans/2026-09-10-render-service-{plan,handoff}.md`, `2026-09-11-reel-in-the-container.md`, `2026-09-11-web-on-render-service.md`, `docs/superpowers/specs/2026-09-10-web-render-service-design.md`, `2026-09-01-badtakes-on-the-web-design.md`, `docs/superpowers/research/2026-09-12-desktop-smoke-surface.md:366`. These are records of the past and are already stale; leave them.

## 3. Responsibility map

Ranges start at the leading comment block (from the parse). Callers are other top-level names that
reference the binding.

### Constants and config
| Lines | Binding | Used by |
| --- | --- | --- |
| 1-34 | file header, `'use strict'` | |
| 36-56 | 18 `require`s | |
| 58, 59 | `ROOT` (`path.join(__dirname, '..')`), `BRAND` | `ROOT`: `mixFor`, `creditFor`. `BRAND`: `renderVideo`, `handle` (/status). **The two `__dirname`-relative values.** |
| 60 | `MAX_BODY` (600 MiB) | `readBody` default |
| 61 | `FFMPEG_TIMEOUT` | `renderVideo`, `renderReel`, `assembleEdit` |
| 62 | `MIX_TIMEOUT` | `mixFor` |
| 63-65 | `STATUS_TIMEOUT` | `handle` (/status) |
| 66-67 | `JSON_BODY_MAX` (4 MiB) | `runRender`, `handle` (/session POST, DELETE) |
| 69-84 | `EDIT_BUDGET_MS` | `assembleEdit` |
| 86-87 | `ffmpeg`, `ffprobe` (arrow consts over `resolveTool`) | the jobs, `handle` (/status) |
| 89-109 | `threadCount()` (env read, `os.availableParallelism`) | `withThreads`, `assembleEdit` |
| 111-118 | `withThreads(args)` → `src/creator.withThreads` | the jobs |
| 159-168 | `DEV_STORE_ROOT` (env, **load time**), `R2_NAMES` | `currentStore` |
| 203-220 | `UNROUTED_IN_CONTAINER` | `unroutedInContainer` |
| 287-316 | `PULL_MAX_BYTES` (with the memory-budget table) | `pullMaxBytes` |

### State (module-level `let`)
| Lines | Binding | Written by | Read by |
| --- | --- | --- | --- |
| 120-149 | `storeOverride` (120-135 is the "sessions" banner, 136-148 the seam comment) | `configure` | `currentStore` |
| 150 | `nowOverride` | `configure` | `currentNow` |
| 151 | `containerMode` | `configure` | **`handle`** (916) |
| 170 | `realStore` (memo; `configure` does not reset it) | `currentStore` | `currentStore` |

### Pure utilities (no IO, no state)
| Lines | Function |
| --- | --- |
| 222-224 | `unroutedInContainer(p)` |
| 230-235 | `corsAllowlist()` (env only) |
| 317-320 | `pullMaxBytes()` (env only) |
| 253-256 | `json(res, status, data)` (writes a response; no other IO) |

### Services and IO
| Lines | Group | Functions | IO |
| --- | --- | --- | --- |
| 136-201 | the configuration seam and store selection | `configure`, `currentStore` (lazy `require('../src/renderlocal')` at 188), `currentNow` | env, filesystem store or R2 |
| 226-228, 698-715 | the gate | `activationPublicKey`, `gateOf`, `gatedStore` | reads the body, verifies the token, resolves the store |
| 237-285, 691-696 | HTTP plumbing | `readBody`, `json`, `sendFile` (awaitable, chunked), `readJson` | request and response streams, `fs.createReadStream` |
| 287-409 | the render preamble: a request's inputs | `pullPrefix`, `readTakeSidecars`, `mixFor` (headless-browser mix via `renderMix`), `sceneVideo` | store → request dir, `fs`, child browser |
| 411-488 | the video job | `creditFor` (drawn in a headless browser; **swallows every failure**), `renderVideo` | ffprobe, ffmpeg, browser |
| 490-546 | the reel job | `renderReel` | ffprobe, ffmpeg, browser (`renderReelVideo`) |
| 548-637 | the edit job | `uploadFromProject`, `assembleEdit` | ffprobe, ffmpeg (`assemblePack`), `zipDir` |
| 717-761 | the scene cache read with self-repair | `loadCachedScene` | `fetchScene` → temp dir, `loadPack` |
| 639-689 | dev accounts | `devAccountsEnabled`, `devAccount` | `fetch` to the local Supabase admin, token and `activate` endpoints |

### Route handlers
| Lines | Route | Where it lives today |
| --- | --- | --- |
| 657-689 | `GET /dev-account` | `devAccount`, dispatched at 918 |
| 920-930 | `GET /status` | inline in `handle`, **before** the try |
| 933-945 | `PUT /blob/*` | inline in `handle`, inside the try |
| 947-962 | `POST /session` | inline in `handle`, inside the try |
| 969-990 | `DELETE /session/:id` | inline in `handle`, inside the try |
| 763-880 | `POST /session/:id/{video,reel,assemble}` | `runRender`, dispatched at 992-994 with `return await` |

### Orchestration
| Lines | |
| --- | --- |
| 882-916 | `handle`: parse the URL and strip `/api`, CORS on every response, OPTIONS, the container unroute |
| 918-1001 | `handle`: dispatch, the `/session/:id` match, the 404, and the try/catch that turns throws into 400s (and `res.destroy()` once headers are sent) |
| 1003-1005 | `module.exports` |

## 4. Proposed tree

### Where the modules live

A new directory **`scripts/web-api/`**, beside the entry file, flat (no subdirectories). Reasons,
and the alternatives rejected:

- **Beside the entry, same name**, the way `main/` sits beside `main.js` in the sibling spike: a
  reader who opens the entry finds its modules next to it. Node resolves `require('./web-api')` to
  the *file* before the *directory*, and that was proven, not assumed: in the scratchpad, a
  `scripts/web-api.js` beside a `scripts/web-api/index.js` makes `require('./web-api')` return the
  file. The directory gets **no `index.js`**, so nothing can reach it by that name.
- **Flat, one depth.** Every module requires `../../src/…`, and `ROOT` is computed once, in
  `config.js`. A `jobs/` and `routes/` split would put modules at two depths and double the
  `__dirname` hazard in risk 1. The `job-` and `route-` prefixes group the files in a listing
  instead.
- **Not `src/`.** `src/**/*` is in the electron-builder `files` allowlist, so service code there
  would ship inside the desktop app's asar. `src/` is also the shared-rules layer: `web/` loads
  modules from it by script tag, the kit stages from it, and `src/mixrender.js`'s harness server
  serves `/src` to its headless browser (`REPO_ROUTES`, mixrender.js:169). None of these functions
  is a shared rule; they are this service's orchestration. The one attraction, that `.dockerignore`
  already includes `src/` whole, is a single line in `.dockerignore` for `scripts/web-api/` (§7).
- **Not `container/`.** It holds the browser harness pages, and the harness server serves
  `/container` to the headless browser too.
- **Not a new top-level `render/`.** It needs the same `.dockerignore` and path-filter entries, and
  the name loses its link to the entry path every doc already cites.

Five conventions every module follows, because the risks in §10 turn on them:

1. **Nothing under `scripts/web-api/` requires `../web-api`.** The entry is the only root, so the
   graph below stays acyclic.
2. **No module exports a `let`.** The seam's four `let`s stay private to `seam.js` and are read
   through functions (`inContainer()`, `currentStore()`, `currentNow()`). Destructuring a `let` out
   of `module.exports` copies its value at require time.
3. **Lazy requires stay inside their functions:** `require('../../src/renderlocal')` in
   `currentStore`, `require('../../package.json')` in `devAccount`.
4. **Each module re-requires only the `src/` names it uses**, from the same module as today. No
   module gains a copy of a rule.
5. **Log strings are moved verbatim**, `web-api — ` prefix included, even though the text now
   lives in another file.

### The files

Sizes are the measured spans, plus ~5 lines of requires per `src/` module used, a 2-4 line header
naming the responsibility, and the exports.

#### `scripts/web-api.js`: entry and router (≈125). See §5.
Keeps the header 1-34, `corsAllowlist` 230-235, `handle` 882-1001 reduced to a dispatcher, and
`module.exports` 1003-1005. Requires `url`, `src/rendergate` (`originAllowed`), `./web-api/http`
(`json`, `sendFile`), `./web-api/seam` (`configure`, `inContainer`, `unroutedInContainer`), and
the four `route-*` modules.

#### `scripts/web-api/config.js`: the service's tunables and repo paths (≈55)
`ROOT` 58, which becomes `path.join(__dirname, '..', '..')` (**the one change to a value**, see
risk 1) · `BRAND` 59 · `FFMPEG_TIMEOUT` 61 · `ffmpeg`, `ffprobe` 86-87 · `threadCount` 89-109 ·
`withThreads` 111-118. Requires `os`, `path`, `src/tools` (`resolveTool`), `src/creator`
(`withThreads as spliceThreads`). Exports all seven.

#### `scripts/web-api/http.js`: request and response plumbing (≈65)
`MAX_BODY` 60 · `JSON_BODY_MAX` 66-67 · the `http plumbing` banner 237 · `readBody` 239-251 ·
`json` 253-256 · `sendFile` 258-285 with its Cloud Run comment · `readJson` 693-696. Requires
`fs`. Exports all six.

#### `scripts/web-api/seam.js`: the configuration seam, store selection, container mode (≈105)
The seam comment 136-148 · `storeOverride` 149, `nowOverride` 150, `containerMode` 151 (private) ·
`configure` 153-157 · `DEV_STORE_ROOT` 159-167 · `R2_NAMES` 168 · `realStore` 170 (private) ·
`currentStore` 171-197 (lazy `renderlocal` require kept inside) · `currentNow` 199-201 ·
`UNROUTED_IN_CONTAINER` 203-220 · `unroutedInContainer` 222-224 · **new** `inContainer()`
(`return containerMode;`, 3 lines), which replaces the router's one direct read at 916. Requires
`os`, `path`, `src/renderstore` (`storeFrom`). Exports `configure`, `currentStore`, `currentNow`,
`inContainer`, `unroutedInContainer`.

#### `scripts/web-api/gate.js`: token and store, before any gated route runs (≈35)
`activationPublicKey` 226-228 · `gateOf` 698-709 with its "centralised so neither check can be
forgotten" comment · `gatedStore` 711-715. Requires `src/license-config`, `src/rendergate`
(`gateToken`), `./seam` (`currentStore`, `currentNow`), `./http` (`readJson`). Exports `gateOf`,
`gatedStore`.

#### `scripts/web-api/scene-inputs.js`: the render preamble, i.e. what a job reads (≈140)
`MIX_TIMEOUT` 62 · the `render preamble` banner and `PULL_MAX_BYTES` 287-316 · `pullMaxBytes`
317-320 · `pullPrefix` 322-343 · `readTakeSidecars` 345-364 · `mixFor` 366-402 · `sceneVideo`
404-409. Requires `fs`, `path`, `src/rendergate` (`checkNames`), `src/mixrender` (`renderMix`),
`./config` (`ROOT`). Exports `pullPrefix`, `mixFor`, `sceneVideo`.

#### `scripts/web-api/job-video.js`: the branded `.mp4` (≈90)
The `render: video` banner 411 · `creditFor` 413-446 · `renderVideo` 448-488. Requires `fs`,
`path`, `src/brand`, `src/createpack` (`probeMedia`, `runTool`), `src/reelrender`
(`renderCreditImage`, `CREDIT_TIMEOUT`), `./config` (`ROOT`, `BRAND`, `FFMPEG_TIMEOUT`, `ffmpeg`,
`ffprobe`, `withThreads`), `./scene-inputs` (`mixFor`, `sceneVideo`). Exports `renderVideo`.

#### `scripts/web-api/job-reel.js`: the vertical reel (≈70)
`renderReel` 490-546. Requires `fs`, `path`, `src/reel`, `src/createpack` (`probeMedia`),
`src/rendergate` (`checkNames`), `src/reelrender` (`renderReelVideo`), `./config`
(`FFMPEG_TIMEOUT`, `ffmpeg`, `ffprobe`, `withThreads`), `./scene-inputs` (`mixFor`,
`sceneVideo`). Exports `renderReel`.

#### `scripts/web-api/job-assemble.js`: an edit, as a new scene version (≈120)
`EDIT_BUDGET_MS` 69-84 (its comment is about this function only) · the `edit` banner and
`uploadFromProject` 548-562 · `assembleEdit` 564-637. Requires `fs`, `path`, `src/createpack`
(`probeMedia`, `runTool`, `validateSpec`, `assemblePack`), `src/creator` (`packAudioMuxArgs`),
`src/zip`, `./config` (`FFMPEG_TIMEOUT`, `ffmpeg`, `ffprobe`, `threadCount`, `withThreads`),
`./scene-inputs` (`sceneVideo`). Exports `assembleEdit`.

#### `scripts/web-api/route-render.js`: `POST /session/:id/{video,reel,assemble}` (≈180)
`loadCachedScene` 717-761 · `runRender` 763-880. Requires `fs`, `os`, `path`, `src/packs`,
`src/rendergate` (`takePrefixFor`, `checkDigest`), `src/scenecache`, `./http` (`json`,
`sendFile`, `JSON_BODY_MAX`), `./gate` (`gatedStore`), `./scene-inputs` (`pullPrefix`), and the
three `job-*` modules. Exports `runRender`.

#### `scripts/web-api/route-session.js`: `POST /session`, `DELETE /session/:id` (≈70)
The `sessions` banner 120-135 becomes this file's header · `createSession(req, res)`, whose body
is 948-961 verbatim · `deleteSession(req, res, id)`, whose body is 970-989 verbatim with its
comment. Requires `crypto`, `src/rendergate` (`takePrefixFor`, `scenePrefixFor`, `checkNames`,
`checkDigest`), `./http` (`json`, `JSON_BODY_MAX`), `./gate` (`gatedStore`). Exports both.

#### `scripts/web-api/route-status.js`: `GET /status`, the readiness probe (≈30)
`STATUS_TIMEOUT` 63-65 · `status(req, res)`, whose body is 921-929 verbatim with its comment.
Requires `fs`, `path`, `src/license-config`, `src/createpack` (`runTool`), `./config` (`ffmpeg`,
`BRAND`), `./http` (`json`). Exports `status`.

#### `scripts/web-api/route-dev.js`: the two routes the container does not have (≈80)
The `dev accounts` banner and `devAccountsEnabled` 639-655 · `devAccount` 657-689 (lazy
`package.json` require kept inside) · `putBlob(req, res, url, blob)`, whose body is 933-945
verbatim with its comment; it takes the `/blob/` regex match, not the key, because that body
declares its own `const key`. It is exactly the pair `UNROUTED_IN_CONTAINER` names, so one file
answers "what the image does not serve". Requires `url`, `src/license-config`, `./seam`
(`currentStore`), `./http` (`json`, `readBody`). Exports `devAccount`, `putBlob`.

### Every top-level statement, assigned

All 67 statements, to exactly one place each. The 18 `require` statements are imports, not
definitions: each binding is re-stated in every module that uses it (convention 4), and the table
lists those consumers so no binding is left dangling or picked up by a file that does not use it.

| Lines | Statement | → File |
| --- | --- | --- |
| 1-34 | header comment, `'use strict'` | `web-api.js` (every new file gets its own `'use strict'` and a short header) |
| 36 | `fs` | http, scene-inputs, job-video, job-reel, job-assemble, route-render, route-status |
| 37 | `os` | config, seam, route-render |
| 38 | `path` | config, seam, scene-inputs, job-video, job-reel, job-assemble, route-render, route-status |
| 39 | `crypto` | route-session |
| 40 | `URL` | web-api.js, route-dev |
| 42 | `brandedExportArgs` | job-video |
| 43 | `reelExportArgs` | job-reel |
| 44 | `resolveTool` | config |
| 45 | `probeMedia` · `runTool` · `validateSpec`, `assemblePack` | job-video, job-reel, job-assemble · job-video, job-assemble, route-status · job-assemble |
| 46 | `packAudioMuxArgs` · `spliceThreads` | job-assemble · config |
| 47 | `zipDir` | job-assemble |
| 48 | `licenseConfig` | gate, route-status, route-dev |
| 49 | `loadPack` | route-render |
| 50-52 | `gateToken` · `takePrefixFor` · `scenePrefixFor` · `checkNames` · `checkDigest` · `originAllowed` | gate · route-render, route-session · route-session · scene-inputs, job-reel, route-session · route-render, route-session · web-api.js |
| 53 | `storeFrom` | seam |
| 54 | `cacheScene`, `fetchScene`, `SCENE_OBJECT` | route-render |
| 55 | `renderMix` | scene-inputs |
| 56 | `renderReelVideo` · `renderCreditImage`, `CREDIT_TIMEOUT` | job-reel · job-video |
| 58, 59 | `ROOT`, `BRAND` | config |
| 60 | `MAX_BODY` | http |
| 61 | `FFMPEG_TIMEOUT` | config |
| 62 | `MIX_TIMEOUT` | scene-inputs |
| 63-65 | `STATUS_TIMEOUT` | route-status |
| 66-67 | `JSON_BODY_MAX` | http |
| 69-84 | `EDIT_BUDGET_MS` | job-assemble |
| 86, 87 | `ffmpeg`, `ffprobe` | config |
| 89-109 | `threadCount` | config |
| 111-118 | `withThreads` | config |
| 120-135 | the "sessions" banner (parsed as the lead comment of 149) | route-session (header) |
| 136-149 | seam comment + `let storeOverride` | seam |
| 150, 151 | `let nowOverride`, `let containerMode` | seam |
| 153-157 | `configure` | seam |
| 159-167 | `DEV_STORE_ROOT` | seam |
| 168 | `R2_NAMES` | seam |
| 170 | `let realStore` | seam |
| 171-197 | `currentStore` | seam |
| 199-201 | `currentNow` | seam |
| 203-220 | `UNROUTED_IN_CONTAINER` | seam |
| 222-224 | `unroutedInContainer` | seam |
| 226-228 | `activationPublicKey` | gate |
| 230-235 | `corsAllowlist` | web-api.js |
| 237-251 | `readBody` | http |
| 253-256 | `json` | http |
| 258-285 | `sendFile` | http |
| 287-316 | `PULL_MAX_BYTES` | scene-inputs |
| 317-320 | `pullMaxBytes` | scene-inputs |
| 322-343 | `pullPrefix` | scene-inputs |
| 345-364 | `readTakeSidecars` | scene-inputs |
| 366-402 | `mixFor` | scene-inputs |
| 404-409 | `sceneVideo` | scene-inputs |
| 411-446 | `creditFor` | job-video |
| 448-488 | `renderVideo` | job-video |
| 490-546 | `renderReel` | job-reel |
| 548-562 | `uploadFromProject` | job-assemble |
| 564-637 | `assembleEdit` | job-assemble |
| 639-655 | `devAccountsEnabled` | route-dev |
| 657-689 | `devAccount` | route-dev |
| 691 | the `router` banner | web-api.js (above `handle`) |
| 693-696 | `readJson` | http |
| 698-709 | `gateOf` | gate |
| 711-715 | `gatedStore` | gate |
| 717-761 | `loadCachedScene` | route-render |
| 763-880 | `runRender` | route-render |
| 882-1001 | `handle` | web-api.js (the four inline route bodies leave it, see below) |
| 1003-1005 | `module.exports` | web-api.js |

Counting check: 1 directive + 18 requires + 18 bindings + 29 functions + 1 export = 67, with every
non-require row naming one file.

### Every route, assigned

| Route | Today | → File · handler |
| --- | --- | --- |
| `OPTIONS *`, CORS headers on every response | `handle` 886-911 | `web-api.js` · `handle` (verbatim) |
| container unroute of `/dev-account`, `/blob/*` | `handle` 916 + `UNROUTED_IN_CONTAINER` | `web-api.js` · `handle`, with the list in `seam.js` |
| `GET /dev-account` | `devAccount` 657-689, dispatched 918 | `route-dev.js` · `devAccount` |
| `GET /status` | inline 920-930 | `route-status.js` · `status` |
| `PUT /blob/*` | inline 937-945 | `route-dev.js` · `putBlob` |
| `POST /session` | inline 947-962 | `route-session.js` · `createSession` |
| `DELETE /session/:id` | inline 969-990 | `route-session.js` · `deleteSession` |
| `POST /session/:id/video` · `/reel` · `/assemble` | `runRender` 768 + dispatch 992-994 | `route-render.js` · `runRender`, which calls `job-video.renderVideo` / `job-reel.renderReel` / `job-assemble.assembleEdit` |
| every other path (404) | `handle` 965, 996 | `web-api.js` · `handle` |

### The require graph (acyclic by construction)

```
L0  node core (fs os path crypto url) · src/{tools creator renderstore rendergate license-config
    mixrender brand reel createpack reelrender zip packs scenecache}
L1  config        ← os path src/tools src/creator
    http          ← fs
    seam          ← os path src/renderstore                 (lazy: src/renderlocal)
L2  gate          ← src/license-config src/rendergate seam http
    scene-inputs  ← fs path src/rendergate src/mixrender config
    route-status  ← fs path src/license-config src/createpack config http
    route-dev     ← url src/license-config seam http        (lazy: ../../package.json)
L3  job-video     ← fs path src/brand src/createpack src/reelrender config scene-inputs
    job-reel      ← fs path src/reel src/createpack src/rendergate src/reelrender config scene-inputs
    job-assemble  ← fs path src/createpack src/creator src/zip config scene-inputs
    route-session ← crypto src/rendergate http gate
L4  route-render  ← fs os path src/packs src/rendergate src/scenecache http gate scene-inputs
                    job-video job-reel job-assemble
L5  scripts/web-api.js ← url src/rendergate http seam route-status route-dev route-session route-render
    ▲ scripts/web-server.js · scripts/render-server.js · container/check-image.js ·
      test/webapi.test.js · test/render-server.test.js   (unchanged: they require only the entry)
```

`seam.js` is the only module holding mutable state, and exactly one instance of it exists, because
every requirer names it by the same relative path (risk 3).

## 5. The entry point afterwards

`scripts/web-api.js` stays at its path with the same three exports, and becomes the router. Its
shape after the last PR (bodies elided where they are 886-911 verbatim):

```js
'use strict';                                   // header 1-33 above, plus a module map (≈12 lines)

const { URL } = require('url');

const { originAllowed } = require('../src/rendergate');
const { json, sendFile } = require('./web-api/http');
const { configure, inContainer, unroutedInContainer } = require('./web-api/seam');
const { status } = require('./web-api/route-status');
const { devAccount, putBlob } = require('./web-api/route-dev');
const { createSession, deleteSession } = require('./web-api/route-session');
const { runRender } = require('./web-api/route-render');

function corsAllowlist() { /* 230-235 verbatim */ }

async function handle(req, res) {
  const url = new URL(req.url, 'http://x');
  const p = url.pathname.replace(/^\/api/, '');
  /* CORS 886-896 and OPTIONS 898-911, verbatim */
  if (inContainer() && unroutedInContainer(p)) return json(res, 404, { error: 'not found' });

  // Outside the try, as today: a throw here rejects handle and the host answers 500.
  if (p === '/dev-account' && req.method === 'GET') return devAccount(req, res);
  if (p === '/status' && req.method === 'GET') return status(req, res);

  try {
    // Inside the try, and every call is `return await`: a bare `return promise` would let a
    // rejection escape the catch and turn today's 400 into the host's 500 (risk 4).
    const blob = /^\/blob\/(.+)$/.exec(p);
    if (blob && req.method === 'PUT') return await putBlob(req, res, url, blob);
    if (p === '/session' && req.method === 'POST') return await createSession(req, res);

    const m = /^\/session\/([A-Za-z0-9-]+)(?:\/(.*))?$/.exec(p);
    if (!m) return json(res, 404, { error: 'not found' });
    const id = m[1];
    const rest = m[2] || '';

    if (req.method === 'DELETE' && !rest) return await deleteSession(req, res, id);
    if (req.method === 'POST' && (rest === 'video' || rest === 'reel' || rest === 'assemble')) {
      return await runRender(req, res, id, rest);
    }
    return json(res, 404, { error: 'not found' });
  } catch (err) {
    if (res.headersSent) { res.destroy(); return undefined; }
    return json(res, 400, { error: err.message || String(err) });
  }
}

module.exports = { handle, configure, sendFile };
```

How each part of §2 is kept:

| Contract | Kept by |
| --- | --- |
| Path `scripts/web-api.js` | unchanged. The directory beside it has no `index.js`, and Node resolves the file first (§4). |
| Exports `handle`, `configure`, `sendFile` | the same keys. `configure` is `seam.configure` and `sendFile` is `http.sendFile`, re-exported, so they are identity-equal to the moved functions. `handle` keeps its `(req, res)` signature with no options (comment 144-145). |
| Route order, methods, regexes, 404 bodies | verbatim. The only change inside `handle` is four inline blocks becoming calls with the same arguments in scope (`url`, `blob`, `id`). |
| The try boundary | `/dev-account` and `/status` stay outside the try, everything else inside with `return await`. Today's 500-versus-400 split is unchanged. |
| The optional `/api` strip | line 884 verbatim. A new test pins `/status` without `/api` (§8), because the deploy probe and the deployed web app depend on it and no test does today. |
| CORS: `X-Branded`, `X-Lines` exposed | verbatim in `handle`. Both headers are still set in `runRender` just before `sendFile`, in `route-render.js`. |
| `configure` semantics (reset all three settings, keep the `realStore` memo) | `seam.js` holds all four `let`s and `configure` verbatim. |
| Env reads and their timing | same function, same moment. `BT_RENDER_STORE` is still read while `require('./web-api')` runs, because the entry requires `seam.js` at top level. Everything else is read at call time. |
| Log lines | moved verbatim (convention 5). |
| Container image | `.dockerignore`, `check-image.js` and `deploy-render.yml` gain the directory (§7). `render-server.js` is unchanged. |
| Hosts and tests | `web-server.js`, `render-server.js`, `check-image.js` and both test files require only the entry and need no change. |

## 6. Files over 250 lines

**None.** The largest proposed file is `route-render.js` at ≈180. The service is 1005 lines, 411 of them
comment-only incident prose, and its responsibilities are small. The honest sizes, largest
first, so Eric can judge whether any split went too far:

| File | Honest size | Why it is this size, and whether to cut or merge |
| --- | --- | --- |
| `route-render.js` | ≈180 | `runRender` is one 118-line function, 41 lines of which are the lifecycle comment (789-829) explaining why there is no sweep. `loadCachedScene` (45) exists only for `runRender`'s 409 path, and its comment refers back to it. Cutting either off puts an explanation in a different file from the code it explains. |
| `scene-inputs.js` | ≈140 | 30 lines are the `PULL_MAX_BYTES` memory-budget table. `mixFor` is 37. All of it is "what a job reads before it runs". |
| `web-api.js` | ≈125 | the 34-line header, the CORS and OPTIONS block with its comments, and the dispatch. |
| `job-assemble.js` | ≈120 | `assembleEdit` is one 74-line flow, plus `EDIT_BUDGET_MS` whose 16-line comment is about this function alone. |
| `seam.js` | ≈105 | four `let`s, the setter, the store picker with its dev/container comments, and the unroute list. Nowhere else may hold that state. |

Small files that are honest at that size, so nobody rounds them up by merging unrelated things:
`route-status.js` (≈30: the readiness probe is a contract the deploy asserts on), `gate.js` (≈35:
the one place a gated route gets its licence and store), `config.js` (≈55: the `__dirname`
values live in one file on purpose). If Eric wants fewer files, the defensible merges are
(a) `gate.js` into `http.js` (≈100, "a request's preconditions"); (b) `/status` left inline in
`handle` (entry +11, and that route keeps its exact shape); (c) `job-video.js` + `job-reel.js` as
`job-export.js` (≈155). Twelve modules is the recommendation, because each maps to one question a
reviewer asks.

## 7. Build and deploy touch points

| Where | Change | Why |
| --- | --- | --- |
| `.dockerignore` | add `!scripts/web-api` beside line 14. The directory form is the one `!src` (21) and `!container` already use. Re-point the comments at 17 and 23, which describe web-api's lazy require. | The file excludes everything by default. Without the line the build context has no `scripts/web-api/`, `COPY scripts/ ./scripts/` copies two files, and `check-image.js`'s last check (`require('../scripts/web-api')`) fails the build. |
| `Dockerfile` | none | `COPY scripts/ ./scripts/` (80) copies whatever the context holds; `CMD` (102) is unchanged. |
| `container/check-image.js` `MUST_EXIST` (62-80) | add each `scripts/web-api/*.js` in the PR that creates it | The final require already proves the service loads, but a missing module then surfaces as `Cannot find module './web-api/seam'` from inside the service. The named list says which file the copy list dropped, which is the check's stated purpose (comment 1-43). |
| `.github/workflows/deploy-render.yml` `on.push.paths` (203-222) | add `'scripts/web-api/**'` beside 207; update the comment at 196 | **The dangerous omission.** Without it, a later PR that touches only `scripts/web-api/job-video.js` merges and never deploys: the service keeps running the old code while every check is green. |
| `package.json` scripts | none | `web` and `web:https` run `scripts/web-server.js`, `web:local` runs `scripts/web-local.js` which spawns it, and `web:live` runs `scripts/render-proxy.js`, which never loads the file. The `test` glob `test/**/*.test.js` already covers where new tests go (`test/webapi.test.js`). |
| `package.json` `build.files` | **none. Confirmed:** `scripts/` is outside the allowlist | The allowlist is `main.js, preload.js, src/**/*, renderer/**/*, brand/*.png, brand/fonts/*, package.json, node_modules/**/*`. Neither the entry nor the directory is in the desktop app, and the asar leak check (`voicepack`, `(^|/)(server|site)/`) is unaffected. |
| `scripts/build-web.js`, `scripts/build-mobile.js`, `deploy-web.yml` | **none. Confirmed:** `build-web.js` neither copies nor references `web-api.js` | Its only `/api` mention (111) is a CSP comment (same-origin `/api` is covered by `'self'`). The static `my.badtakes.io` deploy has no backend (`web/README.md:375`), and `build-mobile.js` sets `apiBase` to `none`. |
| `scripts/render-proxy.js` | none | It forwards to the deployed service over HTTP. |
| `scripts/ci-changes.sh`, `.github/workflows/ci.yml` | none | `scripts/web-api/**` is not on the ignore list, so a change runs the app jobs, whose `npm test` includes both test files. **No workflow builds the image for a pull request**: only `deploy-render.yml` and `deploy-profiles.yml` name a Dockerfile (§8 E, risk 8). |
| Lint, format, jsconfig | none | There is no eslint, prettier, biome, jsconfig or tsconfig at the repo root (`ls -a`); `admin/` has its own, scoped to `admin/`. |
| `.claude/skills/` | none | No skill names the path. |
| Comments naming a moved function (§2e) | re-point in PR 4: `src/creator.js:261,265` (`threadCount` → `scripts/web-api/config.js`), `src/renderlocal.js:20` (the lazy require → `seam.js`'s `currentStore`), `scripts/render-server.js:6,13,101` (`UNROUTED_IN_CONTAINER`, `currentStore()`, `threadCount()` → `seam.js`/`config.js`), `.dockerignore:17,23`, `deploy-render.yml:147,196`, `test/render-server.test.js:6,78` | Prose; nothing parses them. |
| Docs naming the path | `web/CLAUDE.md:146` and `web/README.md` § The backend each gain one sentence naming `scripts/web-api/`. Leave the `web/*.js` comments alone. | The path those comments cite stays true, so there is no reason to touch shipped page code for prose. |

## 8. Verification recipe

### What already covers the file

- **`test/webapi.test.js`** (686 lines, 27 tests). It requires `handle`, `configure` (19) and
  `sendFile` (619), drives the real `handle` over a real `http.Server` with a fake in-memory
  Store, and mints real Ed25519 tokens. It builds its `.take` from the committed
  `fixtures/library`, so it **never skips** and needs no packs, ffmpeg or browser. It covers:
  `POST /session` (401, 200 with UUID and urls, 400s), prefix derivation across licences, scoped
  `DELETE`, `needScene` false and 409, cache-poison refusal and self-repair (`loadCachedScene`),
  the pull cap (`pullPrefix`), the deleted routes, `/blob` local-only, the exact container-unroute
  set, CORS, `/status` shape and `ffmpeg: false`, chunked `sendFile`, and the videoless refusal on
  `/video` and `/reel` (reaches `sceneVideo`).
- **`test/render-server.test.js`** (172 lines, 7 tests). It requires `configure` from this file
  and `createServer`/`renderConfig` from the entry point, and spawns the entry. It covers the
  container unroutes, `/status` with **`brand === true`** (which pins `BRAND`'s path from the
  tree), and `POST /session` answering 401 rather than 404.
- **`container/check-image.js`**, in the image only.
- **Covered by nothing:** a successful `renderVideo`, `renderReel` or `assembleEdit`; `mixFor`'s
  browser mix; `creditFor`, which fails silently; `devAccount`'s success path; the R2 branch of
  `currentStore`; whether `ROOT` is right.

### Which tier applies

`implementing-changes`' tier table has no `scripts/` row. What applies:

- **`npm test`**, reporting the skip count. Neither test file depends on `voicepacks/`, so both
  run in any worktree after `scripts/worktree-init.sh`. While iterating:
  `node --test test/webapi.test.js test/render-server.test.js`.
- **No smoke run.** Electron never loads this file (`main.js`/`renderer/` row not touched).
- **No `server-changes` skill.** `server/**` is not touched.
- **A browser export (D)** for PR 2. `web/` is not touched, but the four job files are reachable
  only through one, and `web/CLAUDE.md` says the web app's proof is a browser, not smoke.
- **An image build (E)** whenever the copy list changes. `deploy-render.yml` itself calls the copy
  list "a build-time fact".

### New tests (added to `test/webapi.test.js`, in the PR named)

1. **PR 1**: loading the service does not load `src/renderlocal.js`. The test spawns
   `node -e "require('./scripts/web-api'); process.exit(Object.keys(require.cache).some((k) => k.endsWith('/src/renderlocal.js')) ? 1 : 0)"`
   and expects exit 0. That pins convention 3 on a laptop, not only in the image.
2. **PR 1**: `GET /status` **without** `/api` answers 200 with the same shape, the contract
   the deploy probe and the deployed web app rely on (§2b).
3. **PR 3**: `POST /session` with an unparseable body answers 400 `{error}`, not 500. `PUT /blob/*`
   against a local-shaped store whose `acceptPut` throws answers 400 with that message. These are
   the two `return await` boundaries in risk 4.

### Dry runs that write nothing into the tree

Run each on the base commit and on the PR head, save to a temp dir, and `diff`. Output must be
identical after the temp path is normalised.

**A. The dev server** (`npm run web` without its seed step, which regenerates the gitignored
`web/lib/takeseed.js` and `webconfig.js` and never touches this file):

```bash
T=$(mktemp -d); U=$(node -e 'console.log(require("crypto").randomUUID())')
PORT=18901 BT_RENDER_STORE="$T/store" BT_CORS_ORIGINS=https://my.badtakes.io \
  node scripts/web-server.js > "$T/server.log" 2>&1 & PID=$!; sleep 1
B=http://localhost:18901/api; J='content-type: application/json'
{
  curl -si "$B/status"
  curl -si -X OPTIONS -H 'Origin: https://my.badtakes.io' "$B/session"
  curl -si -X OPTIONS -H 'Origin: https://evil.test' "$B/session"
  curl -si -X POST   -H "$J" -d '{}'    "$B/session"                    # 401
  curl -si -X POST   -H "$J" -d '{nope' "$B/session"                    # 400 (thrown inside the try)
  curl -si -X POST   -H "$J" -d '{}'    "$B/session/$U/video"           # 401
  curl -si -X POST   -H "$J" -d '{}'    "$B/session/$U/reel"            # 401
  curl -si -X POST   -H "$J" -d '{}'    "$B/session/$U/assemble"        # 401
  curl -si -X DELETE -H "$J" -d '{}'    "$B/session/$U"                 # 401
  curl -si -X PUT --data-binary x "$B/blob/render/sessions/a/$U/t.webm?exp=1&sig=00"  # 400 bad signature
  curl -si "$B/dev-account"                                             # 404
  curl -si -X PUT --data-binary x "$B/session/$U/file/x"                # 404
  curl -si "$B/nope"                                                    # 404
} | grep -vi '^date:\|^connection:\|^keep-alive:' | sed "s/$U/UUID/g" > "$T/routes.txt"
kill $PID; sed -i '' "s#$T#T#g" "$T/server.log"
```

The only disk write is the dev store under `$T/store`, created by the `/blob` request's
`currentStore()`. That call also prints the `web-api — object store: local directory …` line into
`server.log`, which the diff compares too. `--https` is left out: it creates `.web-cert/` under the
repo root when absent, and `handle` is the same under both schemes.

**B. The container entry** (what the `Dockerfile` runs), on the laptop with no bucket:

```bash
PORT=18080 BT_RENDER_THREADS=2 BT_CORS_ORIGINS=https://my.badtakes.io \
R2_ACCOUNT_ID=x R2_BUCKET=x R2_ACCESS_KEY_ID=x R2_SECRET_ACCESS_KEY=x \
  node scripts/render-server.js & PID=$!; sleep 1
curl -si localhost:18080/status; curl -si localhost:18080/api/status        # both 200: the optional /api
curl -si localhost:18080/api/dev-account                                    # 404 {"error":"not found"}
curl -si -X PUT --data-binary x localhost:18080/api/blob/x                  # 404 {"error":"not found"}
curl -si -X POST -H 'content-type: application/json' -d '{}' localhost:18080/session   # 401
kill $PID
```

It uses port 18080 because host port 8080 is taken on this Mac. `storeFrom` only validates the
four names when built, and none of these requests passes the gate, so the fake R2 values reach
nothing.

**C. `npm run web:local`** (the dev account). This needs the local Supabase stack up and functions
served (podman; `server/README.md` § Local development). `BT_WEB_SEED_OUT=$(mktemp -d) npm run
web:local` keeps the seed out of the tree (`web-local.js` passes its environment to the seed), then
`curl -s http://localhost:8901/api/dev-account | jq -e .token` and the same against
`http://127.0.0.1:8901`: two origins, two accounts.

**D. End-to-end exports** (PR 2; the only proof for `scene-inputs`, `job-video`, `job-reel`,
`job-assemble`). Plain `npm run web:local` (it regenerates the gitignored page config, as every
web session does). Open `http://localhost:8901/web/` and use a scene with video. Claim a handle
for the dev account before the first export (PR 2 used the page's own `profileUpdate` and
`cacheHandle`): dev accounts never get the handle prompt, and without a handle the page sends no
credit, so the end card draws no strip and the credit check below proves nothing. Record a take,
then export:

- **Video** from the live room. The toast reads "Video exported.", meaning `X-Branded: 1`, and the
  end card shows the credit strip.
- **Video** from a saved history entry with a trimmed window (the uploaded `mix.wav` branch and the
  cut).
- **Reel**, once without and once with a camera take (the `hasCam` layouts).
- **Edit** saved as a new version (`X-Lines` and the `edit.zip` install).

It needs Homebrew ffmpeg and a Chrome that `src/mixrender.js` can resolve (`BT_CHROME` otherwise).
**The server log must contain no `web-api — the end card credit could not be drawn` line**: that
swallowed failure is how a wrong `ROOT` would hide (risk 1).

**E. The image** (PR 1 and any PR that adds a module):
`podman build -t badtakes-render:split .` writes only to podman's storage, and its last layer runs
`check-image.js`, which requires the service. Then:
`podman run --rm -d --name bt-split -p 18080:8080 -e BT_RENDER_THREADS=2 -e R2_ACCOUNT_ID=x -e R2_BUCKET=x -e R2_ACCESS_KEY_ID=x -e R2_SECRET_ACCESS_KEY=x badtakes-render:split`,
`curl -fsS localhost:18080/status | jq -e '.ffmpeg == true and .brand == true'` (the deploy's own
assertion), and `podman rm -f bt-split`.

### F. The behaviour-preservation check (pure moves)

A small script, **kept outside the repo** (the scratchpad or `$T`), run on every PR, with its
output pasted into the PR body:

```bash
git show 20d11e1:scripts/web-api.js > "$T/old.js"          # or the PR's base
node "$T/check-web-api-split.js" "$T/old.js" scripts/web-api.js scripts/web-api/*.js
```

It uses `admin/node_modules/acorn`, which `scripts/worktree-init.sh` links (BAD-034), and does
eight things:

1. **Collects the declarations.** It parses old and new and records every top-level
   `FunctionDeclaration` and non-`require` `VariableDeclaration` as name → normalised source
   (indentation stripped) plus the file it lives in.
2. **Checks each is declared exactly once.** Every old name is declared in exactly one new file,
   and the only new top-level names allowed are `inContainer`, `status`, `putBlob`,
   `createSession`, `deleteSession`. This is §4's table, checked by a machine.
3. **Checks the bodies are identical.** Each name's text is diffed after three allowlisted
   rewrites: `'../src/` → `'../../src/`, `'../package.json'` → `'../../package.json'`,
   `path.join(__dirname, '..')` → `path.join(__dirname, '..', '..')`. Only `handle` may differ,
   and its diff is printed.
4. **Checks the extracted routes.** In the old `handle` it finds the four `IfStatement`s whose test
   text is `p === '/status' && req.method === 'GET'`, `blob && req.method === 'PUT'`,
   `p === '/session' && req.method === 'POST'` and `req.method === 'DELETE' && !rest`. Each block's
   statements must equal the body of `status`, `putBlob`, `createSession` or `deleteSession`.
5. **Checks no prose was lost.** The multiset of normalised comment lines in the old file must be
   contained in the new files': headers may add lines, but no explanation may disappear.
6. **Loads the service with a cycle guard.** It wraps `Module._load`, requires the entry, and fails
   if any module is re-entered while on the load stack. It also asserts `require.cache` holds
   exactly one `scripts/web-api/seam.js`.
7. **Checks the exports.** `Object.keys(require('./scripts/web-api')).sort()` is
   `configure,handle,sendFile`, and `require('./scripts/web-api').configure ===
   require('./scripts/web-api/seam').configure`.
8. **Checks the lazy require.** `src/renderlocal.js` is not in `require.cache` after step 6.

Expected report: `47 declarations: 46 identical, 1 expected diff (handle); 4 extracted routes
identical; 0 comment lines lost; no cycle; exports configure,handle,sendFile`. Whether to commit
the script for the four PRs is Eric's call. The tests above are the durable half.

## 9. Phase 2 order

Four PRs in Eric's order. Each leaves the service runnable and each is reviewable as moves.

**PR 1: constants and pure utilities, plus the container plumbing (≈120 lines moved).**
`scripts/web-api/config.js` and `http.js`; the entry requires them and re-exports `sendFile`. The
same PR adds `!scripts/web-api` to `.dockerignore`, `'scripts/web-api/**'` to `deploy-render.yml`,
and the two new paths to `MUST_EXIST`, plus new tests 1 and 2. The plumbing goes first because the
first PR to create the directory is the first that can break the image, and the path filter must
exist before any later PR touches only the directory. *Proof:* `npm test`; A and B diffs
identical; **E**; F.

**PR 2: services and IO (≈520 lines moved).**
`seam.js` (with `inContainer()`, which `handle` now reads), `gate.js`, `scene-inputs.js`,
`job-video.js`, `job-reel.js`, `job-assemble.js`. `runRender` and `loadCachedScene` stay in the
entry for now and require the jobs. Six `MUST_EXIST` rows. *Proof:* `npm test`; A, B, E, F;
**D (all the exports)**, since this is the one PR whose code no test reaches; C, because the seam
moved under `/dev-account`. *Optional split:* 2a `seam` + `gate` (≈95 lines: the state move and
its `let` hazard alone) and 2b inputs + jobs (≈425).

**PR 3: route handlers (≈260 lines moved).**
`route-render.js` (`loadCachedScene`, `runRender`), `route-session.js`, `route-status.js`,
`route-dev.js`. The four inline bodies become calls, with `return await` inside the try. Four
`MUST_EXIST` rows, plus new test 3. *Proof:* `npm test`; A and B diffs identical (the 400/401/404
matrix is exactly what this PR can break); E; F, whose step 4 is this PR's check.

**PR 4: the entry reduced to an orchestrator, and the sweep (≈40 lines changed).**
The entry keeps only its requires, `corsAllowlist`, `handle` and exports, and its header gains
the module map. The comment re-points and the two doc sentences of §7 land here. *Proof:*
`npm test`; A; F run once against `20d11e1` for the whole refactor. It can fold into PR 3 if Eric
prefers three.

Why four for a 1005-line file: each PR carries one kind of risk and its one proof. PR 1 is the
only one that changes the image's copy list, PR 2 the only one that needs a browser, and PR 3 the
only one that changes control flow in the router. Land them back to back: the file took seven
commits between 2026-09-10 and 2026-09-14 (#312-#324), and any render-service branch cut before
PR 1 would have to rebase through the moves. No such branch exists today: the one other worktree,
`render-fetch-catalog-scenes`, has no commits ahead of `main`.

## 10. Risks

1. **`__dirname` and relative requires.** Every moved `require('../src/…')` becomes
   `'../../src/…'`, plus the two lazy ones, and `ROOT` becomes `path.join(__dirname, '..', '..')`.
   A missing `../` on a require fails at load and the tests catch it. A wrong `BRAND` is caught by
   `render-server.test.js` (`brand === true`) and the deploy's `jq`. **A wrong `ROOT` is caught by
   nothing that runs without a browser.** `mixFor` then fails the export with a 400, but
   `creditFor` swallows the failure and ships the video without its end-card credit, which looks
   correct. D's log check is the guard.
2. **Lazy requires hoisted.** Moving `require('../../src/renderlocal')` to the top of `seam.js`
   "for tidiness" kills the container on load, because that file is deliberately absent from the
   image. `check-image.js` catches it at build time on `main`, not on the PR; new test 1 catches it
   on a laptop.
3. **Mutable state becomes module state.** `storeOverride`, `nowOverride`, `containerMode` and
   `realStore` live in `seam.js`. There are two traps:
   - `const { containerMode } = require('./seam')` copies `false` at load, and container mode
     never unroutes. The `inContainer()` accessor exists so nobody writes that, and
     `test/webapi.test.js:501` fails if someone does.
   - **Two instances.** macOS is case-insensitive, so `require('./Seam')` works on this Mac and
     creates a second module: `configure` writes one, the router reads the other. It then fails
     outright on Linux (CI, the container). Use one lower-case spelling.

   The suite's `afterEach(() => configure({}))` resets the right instance only because the entry's
   `configure` *is* the seam's (§8 F step 7).
4. **`return await` at the try boundary.** Today `handle` does `return await runRender(…)` (993)
   inside the try, and the inline routes throw inside it. An extracted route called as
   `return createSession(req, res)`, without `await`, lets a rejection escape the `catch`. Both
   hosts then answer 500 where today it is 400. No existing test pins a thrown 400 on those routes;
   new test 3 does. The converse holds too: `/status` and `/dev-account` sit outside the try and
   must stay there.
5. **When env vars are read.** `DEV_STORE_ROOT` must still read `BT_RENDER_STORE` while
   `require('./web-api')` runs, so `seam.js` is required at the entry's top, never lazily.
   Conversely, `activationPublicKey`, `corsAllowlist`, `threadCount`, `pullMaxBytes` and
   `devAccountsEnabled` must keep reading at call time. `test/webapi.test.js:27-28` sets
   `BT_ACTIVATION_PUBLIC_KEY` and `BT_CORS_ORIGINS` after requiring the module, so caching either
   at load turns the suite red. Caching `BT_RENDER_THREADS` would not; it would silently pin a value.
6. **Require cycles.** A job that takes `json` from the entry, or a route that requires
   `../web-api` for `configure`, creates a cycle. CommonJS hands back a half-filled `exports` and
   the destructured name is `undefined`, so the failure is `x is not a function` on the first
   render, not at load. Convention 1 and F's cycle guard prevent it.
7. **Hoisting and temporal dead zone.** Low risk here. Every moved `const` is read inside functions
   at call time, except `BRAND` (from `ROOT`) and `DEV_STORE_ROOT` (from `os` and `path`), and each
   moves into the same file as what it reads, in the same order. `withThreads` and `threadCount`
   stay together. Keep `ROOT`/`BRAND` in one file, with nothing `config.js` requires ever requiring
   it back.
8. **The container copy list is invisible to PR CI.** No workflow builds the image for a pull
   request.
   - Forgetting `!scripts/web-api` in `.dockerignore`: the merge to `main` fails
     `check-image.js`, no revision is created, and traffic stays on the old image. A red deploy,
     safe but noisy.
   - Forgetting the path filter is worse: a later change confined to the directory merges and
     **never deploys**.

   E is required on PR 1, and the filter lands in PR 1, before any later PR can touch only the
   directory.
9. **Log text is contract.** Operators grep `web-api — object store:` to see which store a service
   picked. A tidy rename to the new file's name breaks that silently (convention 5).
10. **What the tests cannot see:** every successful ffmpeg job, the browser mix and credit, the
    dev-account success path, the R2 branch of `currentStore`, and `ROOT`. Hence D on PR 2 and E
    whenever the copy list changes.
11. **Duplicates moved, not consolidated.** The `BT_CORS_ORIGINS` parse exists in `corsAllowlist`
    (234) and `scripts/render-server.js:62`; proposal 72 (P3) covers it. The loopback-host
    pattern in `devAccountsEnabled` (654) also appears in `web/lib/api.js:218` and
    `web/account.js:190`, but as a client-side hint against a server-side guard, which is not a
    rule that has to agree. The split moves both verbatim and creates no third copy of either.
12. **Stale prose.** The comments in §7 that say `threadCount`, `currentStore` or
    `UNROUTED_IN_CONTAINER` live "in scripts/web-api.js" mislead the next reader until PR 4
    re-points them. The dated plans and specs keep their old line numbers as history.
