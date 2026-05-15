# Technical sweep — verification against code

The full "technical sweep" review (executive summary, 10 strengths, 5
critical issues, sectional recommendations, production-readiness scores)
was cross-checked against the working tree. The verdict below names
each claim, marks it **TRUE**, **PARTIAL**, **FALSE**, or **STALE**
(where the claim was true at some past date but the code has moved
since), and ships the citations + concrete fix where applicable.

The headline finding: the review's most prominent claim — *"Only Jumps
Log is wired to backend; My Rig, Inventory, Dropzones, Settings,
stats, modals, and new-jump form are mock/decorative"* — is **STALE**
in the strong sense. It contradicts the actual import graph. The
review appears to have read `frontend/README.md:5-6` (which still
carries the line "The Jumps Log view is wired to the FastAPI backend;
everything else … renders against mock data") and not validated it
against the code.

Findings new to this verification (beyond what's already captured in
the main audit and the two prior addenda) are surfaced at the end.

---

## 1. Headline claim — "Only Jumps Log is wired" — STALE

### What the review said

> Only Jumps Log is wired to backend.
> My Rig, Inventory, Dropzones, Settings, stats, trash, modals, and
> new-jump form are mock/decorative.

### What the code actually shows

```
$ grep -nE "from '../api'" frontend/src/views/*.jsx frontend/src/modals/*.jsx
```

Every view and every modal in the tree imports from `../api` **except
`frontend/src/modals/ComponentModal.jsx`** — and that file is itself
orphan dead code (`grep -rE "<ComponentModal[> ]|import ComponentModal"
frontend/src/` returns nothing). The matrix below covers every UI
surface:

| Surface | Imports from `../api`? | Notes |
|---|---|---|
| `views/Dashboard.jsx` | yes — `getStats`, `listJumps`, `ApiError` | also hosts `<ResumeBanner />` |
| `views/Jumps.jsx` | yes — `listJumps`, `listRigs`, `getMain`, `getStats` | the wired view the README names |
| `views/MyRig.jsx` | yes — multi-API | rig + components |
| `views/Inventory.jsx` | yes — multi-API | the four component lists |
| `views/Dropzones.jsx` | yes — multi-API | full CRUD |
| `views/Settings.jsx` | yes — `runVerify`, `runReindex`, `checkForUpdates` | Verify/Reindex/Updates wired |
| `views/Identity.jsx` | yes — multi-API | jumper + credentials |
| `views/IdentityEditFull.jsx` | yes — `defaultApi` | full identity edit |
| `views/CareerStats.jsx` | yes — `getStats` | stats projection |
| `views/OnboardingWizard.jsx` | yes — `completeOnboarding` | sentinel write |
| `modals/LogJumpModal.jsx` | yes — multi-API (2353 LOC, the real form) | the "new-jump form" the review says is mock |
| `modals/JumpDetailModal.jsx` | yes — multi-API | view + edit + attachments |
| `modals/AddRigModal.jsx` | yes — multi-API | rig assemble |
| `modals/EditRigModal.jsx` | yes — `getRig`, `updateRig` | rig edit |
| `modals/AddComponentModal.jsx` | yes — multi-API | component create |
| `modals/ComponentDetailModal.jsx` | yes — multi-API | 1472 LOC, real edit + reline |
| `modals/RelineModal.jsx` | yes — `updateMain` | lineset reline |
| `modals/DropzoneModal.jsx` | yes — `createDropzone`, `updateDropzone` | DZ form |
| `modals/EditAadModeModal.jsx` | yes — `getAad`, `updateAad` | AAD mode editor |
| `modals/ComponentModal.jsx` | NO — imports from `'../mock'` | **orphan dead code** |

So the actual state is the inverse of the review's claim: **everything
is wired except a single orphan file**. The README is what's stale —
that line predates D33's R.0–R.5 component CRUD slices, the D44
dropzones slice, the D54 people slice, and the D65 onboarding slice,
all of which have shipped.

### What IS still partial / decorative

I went looking for the genuinely-mock surfaces. Three exist; none are
the ones the review named:

1. **`Settings.jsx:376 TrashSection`** — hardcoded text *"2 deleted
   jumps · 1 retired component"* and an "Open trash" button with no
   `onClick`. Confirmed there's no backend endpoint either (`grep -nE
   "trash_route|list_trash|restore_route" backend/api/` returns
   nothing). Trash listing/restoring is fully unbuilt at both layers.
2. **`modals/ComponentModal.jsx`** — the orphan above. Dead code.
3. **A handful of "Coming soon" / "Phase X" buttons** sprinkled
   through the modals (e.g. `ComponentDetailModal` has placeholder
   text about future repack flows, `LogJumpModal` has comments for
   D-entries not yet shipped). These render the message; they're not
   mock data.

### Fix

Two-part:

```diff
- The Jumps Log view is wired to the FastAPI backend; everything else
- (My rig, Inventory, Dropzones, Settings) renders against mock data
- until the corresponding backend endpoints land per D33's R.0–R.5 phases.
+ The Jumps Log, My Rig, Inventory, Dropzones, Identity, Dashboard,
+ Onboarding, and Settings (Verify/Reindex/Updates/Identity) views are
+ all wired to the FastAPI backend. The Trash section in Settings is
+ a visual stub — listing and restoring trashed jumps is not yet wired
+ end-to-end (no backend list_trash/restore route, no UI fetch).
```

Plus the `git rm ComponentModal.jsx mock.js` already proposed in
the deep-dive doc. 5 minutes total. **P1.**

The stale README is the proximate cause of the review's wrong headline
— this is a documentation hygiene fix with disproportionate downstream
value (every future reviewer makes the same mistake).

---

## 2. Error-redaction in 500 responses — TRUE

Already covered in detail in `2026-05-15-chatgpt-findings-deep-dive.md`
§Finding 2. The review's framing is correct; the fix is the
`Settings.expose_internal_errors` flag with a loopback-default-true /
non-loopback-default-false rule. **P1 under the "may become serious
app" framing.**

---

## 3. File upload validation — PARTIAL (some claims true, some false)

The review lists eight purported gaps. Verified one by one:

| Claim | Verdict | Citation |
|---|---|---|
| max file count | **TRUE** — no limit | `backend/api/jumps.py:159` — `files: list[UploadFile] \| None` with no `max_items` |
| max per-file size | **TRUE** — no limit | no `MAX_FILE_BYTES` constant anywhere in backend |
| max total request size | **TRUE** — no limit | no body-size middleware; covered in main audit §1.5 |
| extension allowlist/denylist | **PARTIAL** — no allowlist or denylist, but D4 sanitization (`backend/storage/filesystem.py:144-162`) does reject Windows reserved names (CON, NUL, COM1, etc.) and forbidden characters |
| MIME sniffing | **TRUE** — none. The client-declared `Content-Type` is stored verbatim (`backend/api/jumps.py:179`). Covered in main audit §1.4 + deep-dive Finding 4a |
| duplicate filename behavior | **FALSE** — implemented and tested. `_sanitize_upload_filenames` (`backend/services/jump_service.py:177-229`) rejects duplicates within a single multipart request with 422 + per-index pointer (`#/files/<i>/filename`). `add_attachments` also rejects filenames already in the jump's `<attachments>` (`jump_service.py:778-816`). `track_files` is idempotent on already-tracked names |
| reserved filename rules | **FALSE** — implemented. `_reject_windows_reserved` (`filesystem.py:68-86`) rejects CON, PRN, AUX, NUL, COM1-9, LPT1-9 case-insensitive and trailing space/dot |
| partial-failure error handling | **PARTIAL** — `create_jump` validates filenames pre-write (`jump_service.py:312`) so a bad filename produces 422 with no disk effect. But once the streaming write loop has started, a mid-stream failure leaves an orphaned tmp file (`atomic_write_stream` cleans tmp; later attachments in the same batch are not cleaned). The cleanup posture is documented as deliberate per D25 ("no auto-cleanup; verify reports orphans; reindex skips") |

**Real gaps** (P2): file-count cap, per-file size cap, total-request-size
cap, MIME sniffing. **Already-implemented** (review was wrong):
duplicate-filename handling, reserved-name rules. **Acceptable as-is**
under documented design: partial-batch failure cleanup.

---

## 4. FastAPI/Starlette validation errors not normalized — TRUE

`backend/api/rest.py:152-156` itself acknowledges this:

```python
# * Starlette's ``HTTPException`` and FastAPI's
#   ``RequestValidationError`` retain their own default
#   handlers — acceptable under the narrow reading of D16 (the
#   *service-layer* error envelope is RFC 9457) and already
#   documented in ``backend/api/jumps.py`` for path-param 422s.
```

The "narrow reading of D16" is a real escape hatch — but it means
two error envelopes ride on the same API surface:

- **Service-layer errors** → `application/problem+json` with `code`,
  `request_id`, `errors[]`, etc.
- **FastAPI/Starlette built-in errors** → `application/json` with
  `{"detail": [...]}` (RequestValidationError's default shape) or
  `{"detail": "..."}` (HTTPException's default shape).

A client that wants to render one error widget needs to handle both
shapes. `frontend/src/api.js:34-42` already does this defensively:

```js
try { body = await res.json(); } catch { body = null; }
if (!res.ok) {
  throw new ApiError(body || { status: res.status, title: res.statusText }, requestId);
}
```

So the SPA absorbs the inconsistency. But a third-party SDK generated
from `/openapi.json` (the main audit §1.1 already flagged that
`responses=` is missing across every route) would see two different
schemas and have to dispatch on shape.

### Fix

Register two more exception handlers, mirroring the existing
`ServiceError` and `Exception` handlers:

```python
# backend/api/rest.py — add after the existing handlers
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(RequestValidationError)
async def on_request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Normalise FastAPI body/query/path validation errors to RFC 9457.
    
    FastAPI's default raises a 422 with the shape
    ``{"detail": [{"loc": ["body", "field"], "msg": "...", "type": "..."}]}``.
    Rewrite to a ServiceError-shaped envelope so every 4xx on the
    wire is problem+json (D16 §"narrow reading" finally closed).
    """
    field_errors = [
        FieldError(
            pointer=field_pointer(*err.get("loc", ())),
            detail=err.get("msg", "invalid value"),
        )
        for err in exc.errors()
    ]
    typed = ValidationFailedError("request validation failed", errors=field_errors)
    return error_response(
        typed,
        request_id=request_id_of(request),
        instance=request.url.path,
    )

@app.exception_handler(StarletteHTTPException)
async def on_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Normalise Starlette HTTPException to RFC 9457.
    
    Starlette raises HTTPException for things like 404 on an unknown
    path or 405 method-not-allowed. Default body is
    ``{"detail": "..."}``. Map to a problem+json envelope so the
    contract is uniform.
    """
    # Status → code mapping; extend as needed.
    code_for_status = {
        404: "not_found",
        405: "method_not_allowed",
        413: "payload_too_large",
        415: "unsupported_media_type",
    }
    code = code_for_status.get(exc.status_code, f"http_{exc.status_code}")
    typed = ServiceError(str(exc.detail or "request failed"))
    typed.http_status = exc.status_code
    typed.code = code
    typed.title = exc.status_code in (400, 404, 405, 413, 415) and (
        {"400": "Bad Request", "404": "Not Found", "405": "Method Not Allowed",
         "413": "Payload Too Large", "415": "Unsupported Media Type"}.get(
            str(exc.status_code), "Error"
        )
    ) or "Error"
    return error_response(
        typed,
        request_id=request_id_of(request),
        instance=request.url.path,
    )
```

(The `ServiceError` subclassing approach is cleaner — define
`NotFoundHTTPError`, `MethodNotAllowedError`, etc. — but the above
keeps the change minimal.)

Tests would assert that:

- `POST /api/v1/jumps` with an invalid `Content-Type` (`application/x-yaml`)
  returns problem+json with `code=unsupported_media_type`.
- `GET /api/v1/jumps/<not-a-uuid>` returns problem+json with
  `code=validation_failed` (today this is a default 422 JSON body).
- `OPTIONS /api/v1/jumps` (or some other 405-triggering call) returns
  problem+json.

Effort: ~2 hours including tests. **P2.** Compounds with main audit
§1.1 (`responses=`) — both flow from the same "what shape does this
endpoint return?" question.

---

## 5. Frontend state model — PARTIAL

The review claims the active-view shell pattern won't scale. The
actual shell is exactly the `useState('dashboard')` + dispatch object
the review describes (`App.jsx:16-30`). The review's recommendations:

- **React Router or a clear nav store** — sensible; not urgent.
  The current shell is six tabs; the cost of a router upgrade is
  not balanced by the value at this surface size. **DEFER.**
- **Avoid CustomEvent app coordination** — already covered in
  deep-dive Finding 7. The onboarding channels are the worst
  offenders; the altitude channel is harder to migrate cleanly.
  **P2, fix as proposed.**

Beyond what the review noticed:

- **State for "wizard visibility" + "current tab" are stored
  separately in `App.jsx`** (`onboardingState`, `resumeOverride`,
  `activeTab`). The wizard overlay covers the tab; transitioning
  between them is the source of the `setActiveTab('profile')` bug
  (deep-dive Finding 1). A `useReducer` or a small state machine
  (`'showing-wizard' | 'showing-tab'`) would prevent that whole
  class of bug.

---

## 6. Backend recommendations — point by point

| Recommendation | Verdict | Notes |
|---|---|---|
| Production-safe error redaction | **TRUE** | Covered in deep-dive Finding 2 |
| Normalize all errors to problem+json | **TRUE** | This document §4 |
| Add upload/file policy enforcement | **PARTIAL** | Some implemented (sanitization, reserved names, dupes); gaps are size/MIME/count — §3 above |
| Add explicit request-size controls | **TRUE** | Main audit §1.5 |
| Split oversized service modules | **TRUE** | Main audit §4.4, deep-dive Finding 3 |
| Separate jump metadata, folder ops, attachment ops, manifest ops, index ops | **PARTIAL** | Already separated into services. The "service-level transactional orchestration" is `_write_jump_and_manifest` and similar helpers. Could go further (the split proposed in deep-dive Finding 3 is exactly this) |
| Service-level transactional orchestration comments/tests for each multi-step write | **PARTIAL** | Comments exist throughout (e.g. `jump_service.py:240-256` documents D25 steps); tests for crash points partial (see deep-dive Finding 8) |
| Make CORS origins config-driven | **TRUE** | `rest.py:84-87` hard-codes `["http://localhost:5173", "http://127.0.0.1:5173"]`. Move to `Settings.cors_allowed_origins: list[str]` with default = those two |
| Clear "local desktop mode" vs "remote/server mode" config boundary | **TRUE** | Today there's `bind_host` but no enforced posture difference (no auth on remote, no warning on `0.0.0.0` bind). New D-entry candidate |
| API versioning strategy before external clients depend on v1 | **TRUE but documented** | `architecture.md:194-208` already covers versioning. The strategy exists; what's missing is a v2 example or a CI check that no breaking change lands on `/api/v1/*` |

**New finding from §6 verification — `bind_host` accepts any value with no warning.**

`backend/config.py:75` — `bind_host: str = Field(default="127.0.0.1")`.
No validator rejects `"0.0.0.0"` or a public IP. No log warning when
the value differs from loopback. A user who sets
`SKYDIVE_BIND_HOST=0.0.0.0` thinking "I want to access the app from
my phone" exposes an unauthenticated REST surface to their entire
LAN. Per `SECURITY.md`'s stated threat model this is out of scope —
but a defensive log warning is one line:

```python
# backend/main.py — after settings load, before uvicorn.run
if settings.bind_host not in {"127.0.0.1", "localhost", "::1"}:
    _logger.warning(
        "non_loopback_bind",
        extra={
            "bind_host": settings.bind_host,
            "warning": (
                "API is bound to a non-loopback address with NO auth. "
                "Anyone reachable at this address can read and modify "
                "the logbook. See D48 / SECURITY.md."
            ),
        },
    )
```

Costs nothing. **P2.**

---

## 7. Frontend recommendations — point by point

| Recommendation | Verdict | Notes |
|---|---|---|
| Implement real New Jump form | **STALE** | `modals/LogJumpModal.jsx` is 2353 LOC, fully wired |
| Implement Jump Detail modal | **STALE** | `modals/JumpDetailModal.jsx` is 907 LOC, wired (view + edit + attachments via the multi-API import) |
| Implement Edit Jump | **STALE** | same modal handles edit per its imports of `updateJump` |
| Implement attachment add/remove/track UI | **STALE** | `JumpDetailModal` imports `addAttachments`, `trackJumpFiles`, `deleteAttachment` from `api.js` |
| Wire Settings Verify/Reindex/Trash | **PARTIAL** | Verify and Reindex are wired (`Settings.jsx:11` imports `runVerify, runReindex`). Trash is the stub identified in §1 above. **Only the Trash third is true.** |
| Replace mock rig/inventory/dropzone data | **STALE** | All three views import from real API. Only orphan `ComponentModal.jsx` uses mock |
| Add loading/error/empty states consistently | **PARTIAL** | Many views handle loading + error via the `ApiError` import; consistency varies (deep-dive Finding 6.2 flagged the silent `.catch(() => {})` antipattern in two specific places) |
| Extend JumpSummary or fetch full jump details | **FALSE** | `JumpSummary` (`backend/models/jump.py:196-224`) **already includes** aircraft, discipline, freefall_time_s, rig_id. Title and date too. Has been since v4 schema (cited in code comment at line 212-214). The review appears to have read an older API doc |
| Add frontend tests beyond smoke | **PARTIAL** | 4 real test files (65 tests) — `views.smoke.test.jsx` is just a smoke; the other three (`identityEditFull.test.jsx`, `identityEditOrchestrator.test.js`, `d60-starred-dropzone.test.jsx`) are real integration. Coverage is light but not "smoke only" |
| Hide or clearly label features not implemented | **TRUE** for the Trash stub | The Trash section visually claims data ("2 deleted jumps") that the backend can't even produce — should either build it or hide it |

The Trash stub is the only frontend recommendation that survives
verification. Everything else the review listed is **already done**
or **substantially overstated** because of the stale README.

---

## 8. Data-integrity recommendations — all reasonable, partially verified

| Recommendation | Verdict | Coverage in main audit / this doc |
|---|---|---|
| Failure-injection tests around every multi-step write | **PARTIAL** | `test_dropzone_crash_recovery.py` (396 LOC) covers the dropzone path. `test_main_sigkill_lock_release.py` covers process death. The matrix is incomplete — deep-dive §8 named three specific gaps (rig partial-create, jump rename, track_files race) |
| Test crash after XML write before manifest update | **PARTIAL** | Covered for jumps via `folder_reconcile`; not for rigs |
| Test crash after attachment write before XML update | **PARTIAL** | `create_jump` documents the recovery but the test for SIGKILL between step 2 and step 3 isn't there |
| Test crash after folder rename before index update | **GAP** | Deep-dive §8.2 — new finding, no test today |
| Test reindex from XML after partial SQLite corruption | **PARTIAL** | `test_reindex.py` (660 LOC) is broad; doesn't explicitly cover the half-dropped-tables case (main audit §2.1) |
| One-click "Verify all" and "Rebuild index" in UI | **TRUE — already done** | `Settings.jsx` has both buttons wired |
| Backup ZIP export/import | **TRUE** | Not implemented; deferred per CHANGELOG; design candidate for v0.2 |
| Backup verification | **TRUE** | Same posture |
| Migration tests for future schema versions | **PARTIAL** | `test_index_d47_extension.py` (463 LOC) demonstrates the pattern; future schema bumps need analogous tests |
| User-facing repair guidance when verification fails | **GAP** | `Settings.jsx` Verify section shows results but no "here's what to do next" — `verify_route` returns structured issues, the UI doesn't translate them into action |

The new finding here is the "user-facing repair guidance" — verify
correctly identifies issues but the UI just lists them.

---

## 9. Security recommendations — point by point

| Recommendation | Verdict |
|---|---|
| Redact unhandled exception messages by default | **TRUE** — covered, deep-dive Finding 2 |
| Add attachment upload limits | **TRUE** — main audit §1.5, this doc §3 |
| Add file type validation | **TRUE** — deep-dive Finding 4a |
| Add path traversal tests for every filename entry point | **PARTIAL** — `test_filesystem.py` (478 LOC) covers `sanitize_filename` thoroughly; the cross-cutting test "every endpoint accepting a filename rejects `../escape`" is implicit, not explicit |
| Add extension/content mismatch detection | **TRUE** — same as MIME sniffing above |
| Warn if bind_host is non-loopback | **TRUE** — this doc §6 |
| Optional auth before any LAN/headless mode | **TRUE** — D48 succession |
| Sign desktop binaries when moving past beta | **TRUE — not implemented** — `skydive-logbook.spec` has the PyInstaller spec; no codesign/notarization step in any workflow |
| Document privacy expectations for FlySight/videos/medical/licensing | **PARTIAL** — `SECURITY.md` exists but doesn't explicitly cover attachment-content sensitivity |
| Avoid loading remote fonts/assets in packaged desktop builds | **TRUE — REAL GAP** — `frontend/index.html:7-9` loads Google Fonts directly: `<link rel="preconnect" href="https://fonts.googleapis.com">` plus the actual stylesheet `https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap`. For a local-first / offline-capable app this means: (a) Google logs every app open; (b) app fails fast / falls back to system fonts when offline; (c) third party in the data path |

### Fix for the Google Fonts leak — concrete

This one is a real privacy regression for a "local-first" app and the
review's recommendation is correct. Two ways:

**Option A — bundle the fonts as static assets.** Vite handles this
cleanly. Add the WOFF2 files under `frontend/public/fonts/`, then in
`frontend/src/index.css`:

```css
@font-face {
  font-family: 'Archivo';
  src: url('/fonts/Archivo-Variable.woff2') format('woff2-variations');
  font-weight: 400 700;
  font-display: swap;
}
@font-face {
  font-family: 'JetBrains Mono';
  src: url('/fonts/JetBrainsMono-Variable.woff2') format('woff2-variations');
  font-weight: 400 500;
  font-display: swap;
}
```

And delete the three Google Fonts `<link>` tags from
`frontend/index.html`. Vite's build will fingerprint and copy the
fonts into `dist/` so PyInstaller bundles them as part of the app.

**Option B — drop the custom fonts.** `index.css:42-43` already
lists system fallbacks (`-apple-system, system-ui, sans-serif` and
`ui-monospace, 'SF Mono', Menlo, monospace`). Removing the Google
Fonts link makes the app render in the system font on each platform.
Visual change is non-trivial; ship Option A unless the design system
is loose on typography.

License: Archivo is SIL Open Font License 1.1 (free to bundle).
JetBrains Mono is OFL 1.1 (free to bundle). Both can be downloaded
from `fonts.google.com` or their respective repos.

Effort: ~2 hours including downloading the variable WOFF2s,
verifying visual parity, and updating any docs. **P2** for the
"local-first / offline-capable" framing; **P3** if that framing is
loose.

---

## 10. DevOps / release recommendations — verified

| Recommendation | Verdict |
|---|---|
| Signed builds | **TRUE — not implemented**. No GitHub Actions release workflow exists (`.github/workflows/` contains only `ci.yml`). The PyInstaller spec is present but unsigned binaries only |
| Release checklist | **PARTIAL** — `CHANGELOG.md` exists with structured 0.1.0-beta.1 release notes (`Keep a Changelog` format declared on line 4). No formal pre-release checklist file |
| Checksums for release assets | **TRUE — not implemented**. No release workflow, no checksum step |
| Automated packaged-app smoke test | **TRUE — not implemented**. CI runs `pytest` against the source tree; doesn't build a PyInstaller binary and execute it. `docs/build.md` documents the manual per-platform build commands |
| SBOM / dependency audit | **TRUE — not implemented**. `pyproject.toml` pins CVE-floor versions for `starlette` and `python-multipart` with inline references (which is excellent and the prior audit praised) — but no `cyclonedx-py` or similar SBOM step in CI, no `pip-audit` / `npm audit` step |
| Changelog discipline for schema/API changes | **PARTIAL** — `CHANGELOG.md` discipline is solid. The schema-version bump comment chain in `backend/storage/index.py:32-89` is exemplary inline documentation. What's missing is a CI gate: "any change to `models/`, `xml/schema/`, or `api/` must include either a CHANGELOG entry or a D-entry" |
| Update channel config: stable/beta/dev | **PARTIAL** — `update_check_repo` setting exists (`config.py:91`); the channel concept doesn't. Single repo, single channel today |

The CI gap is genuine: `.github/workflows/ci.yml` is solid for
correctness (matrix testing on Linux/macOS/Windows × Python
3.11/3.12/3.13 + Node 20) but has zero coverage for the **release
posture**. No signing, no SBOM, no packaged-binary smoke, no
checksum, no release workflow. Adding even a minimal release
workflow (build PyInstaller binaries on tagged commits + upload to
the GitHub release with SHA-256 sums) is a one-day project that
unlocks the "trusted desktop app" framing the review aspires to.

**P2 — gates the "potentially serious desktop app" claim.**

---

## 11. Production-readiness scores — sanity check

The review's scores:

- Local personal beta: **7.5/10**
- Public desktop app for non-technical users: **5/10**
- Hosted/LAN/multi-user deployment: **2/10**

These ranges are subjective but the gaps below the headline scores
that the review implies (frontend incomplete, etc.) are **wrong**
per §1. The actual gaps are:

| Score | What it accurately reflects |
|---|---|
| **Local personal beta — 7.5** | Reasonable. Lint/type/test discipline + 1737 backend tests + 65 frontend tests + RFC 9457 errors + atomic writes + hardened parser. Trash stub and a few rough edges keep it short of a 9 |
| **Public desktop app — 5** | Reasonable in aggregate, but the dominant gaps are NOT the ones the review names. The real blockers for a non-technical user are: (a) no signed binaries (Windows SmartScreen + macOS Gatekeeper will reject), (b) no auto-update (manual download per release), (c) no first-run trust model (the Google Fonts request fires invisibly), (d) the Trash UI stub, (e) the exception-leak in 500s. None of those are "frontend is mock" |
| **Hosted/LAN/multi-user — 2** | Accurate. No auth, no multi-tenancy, no rate-limiting, no body-size cap, no MIME validation, no CORS config, single global writer lock per process, no audit log. D48 is the right answer here |

The scores themselves are defensible; the implied **reasons** for the
gaps are not.

---

## New findings surfaced by this verification

Beyond what the review claimed, the verification pass turned up four
items not yet in any of the three audit docs:

### N.1 [P1] `Settings.jsx` Trash section is a visual stub claiming non-existent data

`frontend/src/views/Settings.jsx:376-393`:

```jsx
function TrashSection() {
  return (
    <Card className="p-4 px-5 mb-2.5">
      ...
      <div className="text-[12px] text-neutral-400">
        <span className="font-mono text-neutral-300">2</span> deleted jumps ·{' '}
        <span className="font-mono text-neutral-300">1</span> retired component
      </div>
      ...
      <GhostButton>Open trash</GhostButton>
    </Card>
  );
}
```

The counts (`2`, `1`) are **hardcoded literals**. The "Open trash"
button has no `onClick`. No backend endpoint exists to list trashed
items. Three options:

1. **Build it** — add `GET /api/v1/trash` (returns items in
   `<logbook_root>/.trash/`), `POST /api/v1/trash/restore/{path}`,
   matching SPA wiring. Medium effort (~1 day).
2. **Hide it** — render `null` while it's unbuilt. Five minutes.
3. **Label it honestly** — replace the section body with "Coming
   soon — trash listing lands in v0.2." Five minutes.

Recommend (2) or (3) immediately, (1) as a v0.2 slice. **P1** —
the section misrepresents app capability to the user.

### N.2 [P1] Stale `frontend/README.md` causes wrong reviews

The README's "Only Jumps Log is wired" line is the root cause of the
"technical sweep" review's headline error. A fresh reviewer reading
that line then doing the surface-level grep of `from '../mock'` finds
the one orphan import and concludes the README must be right.

Replace lines 5-7 of `frontend/README.md` with the wired-status
language proposed in §1. **5-minute commit.**

### N.3 [P2] `frontend/index.html` loads Google Fonts from CDN

Already documented in §9 above. Real privacy / offline-capability
regression for a local-first app.

### N.4 [P2] `bind_host` accepts any value with no validator or warning

Already documented in §6 above. One-line `_logger.warning` at boot
when the value is non-loopback is the cheapest defense.

---

## Recommended order for THIS document's findings

Pinned execution order across the three audit docs, with this
verification's items merged in. **Stop-and-think items in bold.**

| Order | Item | Effort | Source |
|---|---|---|---|
| 1 | Update `frontend/README.md` to reflect actual wired status | 5 min | this doc N.2 |
| 2 | Replace `Settings.jsx TrashSection` with "coming soon" or null | 5 min | this doc N.1 |
| 3 | Fix `setActiveTab('profile')` → `'dashboard'` | 5 min | deep-dive Finding 1 |
| 4 | `git rm` `ComponentModal.jsx` and `mock.js` | 2 min | deep-dive Finding 5 |
| 5 | Three inline imports in `jump_service.py` → module top | 5 min | main audit §4.2 |
| 6 | Delete `file_service.py` 6-line stub | 1 min | main audit §4.1 |
| 7 | **`Settings.expose_internal_errors` flag + 500-body redaction** | 30 min + test | deep-dive Finding 2 |
| 8 | `bind_host` non-loopback warning at boot | 10 min | this doc N.4 |
| 9 | OpenAPI route `responses=` + `operation_id` | 2 hr | main audit §1.1, §1.3 |
| 10 | RequestValidationError + HTTPException → problem+json | 2 hr | this doc §4 |
| 11 | OpenAPI ↔ Pydantic ProblemDetails alignment test | 30 min | main audit §1.2 |
| 12 | Move CORS origins to `Settings.cors_allowed_origins` | 30 min | this doc §6 |
| 13 | **`open_index` refuse newer-on-disk schema** | 30 min + test | main audit §2.1 |
| 14 | **`folder_reconcile_rigs` + crash test** | 1 day | main audit §2.2, deep-dive §8.1 |
| 15 | Body-size middleware (per-file + per-request) | 2 hr | main audit §1.5 |
| 16 | Content-type sniffing + allow-list | 4 hr | main audit §1.4, deep-dive §4a |
| 17 | Bundle Archivo + JetBrains Mono as static assets | 2 hr | this doc §9 |
| 18 | Cache-Control / ETag middleware | 4 hr | main audit §1.6 |
| 19 | `<OnboardingProvider>` replace CustomEvent bus | 3 hr | deep-dive Finding 7 |
| 20 | Backend release workflow: PyInstaller build + SHA-256 sums + GitHub release | 1 day | this doc §10 |
| 21 | `Settings.jsx` Trash section: real backend wiring (`GET /api/v1/trash`, restore endpoint, UI) | 1 day | this doc N.1 (deferred path) |

Items 1-8 are the literal afternoon. The review's biggest accidental
contribution is forcing this verification to happen — the stale
README's downstream cost is now quantifiable.

---

## Closing assessment of the sweep review

| Lens | Verdict |
|---|---|
| Strengths section (10 items) | Accurate; matches my own audit's "protect these" list |
| Critical issue #1 ("Frontend not functionally complete") | **WRONG** — based on the stale README, contradicted by the import graph |
| Critical issue #2 (exception leak) | **CORRECT** — independently surfaced as P1 in both my audit and this one |
| Critical issue #3 (file upload limits) | **PARTIAL** — some real gaps, some already-implemented features wrongly listed as missing |
| Critical issue #4 (FastAPI error normalization) | **CORRECT** — concrete fix proposed above |
| Critical issue #5 (frontend state model) | **PARTIAL** — sensible direction, not urgent at current surface size |
| Backend recommendations | Mostly correct or already-tracked |
| Frontend recommendations | Mostly **WRONG** — same root cause as critical issue #1 |
| Data integrity recommendations | Mostly correct; some already-shipped |
| Security recommendations | Mostly correct; Google Fonts gap is a real new find |
| Testing recommendations | Reasonable but light on specifics |
| DevOps recommendations | All correct; signed builds + SBOM are genuine gaps |
| Production-readiness scores | Defensible numbers; wrong reasons for the gaps |
| Best-next-implementation order | Steps 1-2 (New Jump, Jump Detail) **already done**; rest is reasonable |

**Bottom line:** the review is most valuable for the security and
release findings (sign your binaries, host your fonts, redact your
exceptions, normalize your errors). It is most misleading on the
frontend-completeness claim, which would have a contributor or
prospective user believing the SPA is a hollow shell. Trust but
verify — and **delete that line in the README** so the next
reviewer doesn't repeat the mistake.

---

*— end —*
