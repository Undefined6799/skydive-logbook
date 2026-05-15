# Code-debt slice plan — prioritized work units

Roll-up of every actionable finding across the four audit docs:

- `reviews/2026-05-15-code-debt-deep-audit.md`
- `reviews/2026-05-15-code-debt-deep-audit.md` Appendix A (ChatGPT cross-check)
- `reviews/2026-05-15-chatgpt-findings-deep-dive.md`
- `reviews/2026-05-15-chatgpt-technical-sweep-verification.md`

Each entry is sized to land as **one focused PR** under the project's
existing CI gates (ruff + pyright + pytest + Vitest). The list is
ordered so that earlier slices unblock or de-risk later ones. Numbers
are **shippable slices**, not estimates of LOC.

## Legend

- **P1** — ship before the next public beta. Either user-visible bug
  or a contract/security hazard that becomes much harder to fix after
  more code lands on top.
- **P2** — ship before D48 (LAN exposure) or before any release the
  app expects non-technical users to install.
- **P3** — structural improvements; ship as the schedule allows.

- **S** — single afternoon, under 1 hour focused work.
- **M** — 2–4 hours including tests.
- **L** — 1 day including a D-entry + tests + cross-platform verify.
- **XL** — multi-day; requires phasing of its own and an architectural
  decision baked in.

---

## Phase 1 — afternoon's work (cumulative ≈ 90 min)

Six slices, all reversible, all touching code the prior audit already
verified. No design decisions required.

### Slice 1 — Documentation truth-up · P1 · S

**Scope:** five doc/dead-code fixes that prevent future reviewers from
making the same mistakes as the prior reviews.

- Update `frontend/README.md:5-7` to reflect actual wired-status of
  views/modals (replace the "Only Jumps Log is wired" line).
- Replace `frontend/src/views/Settings.jsx:376-393 TrashSection` body
  with `return null;` or "Coming in v0.2" copy (it currently claims
  "2 deleted jumps · 1 retired component" — hardcoded literals with
  no backend endpoint behind them).
- `git rm frontend/src/modals/ComponentModal.jsx` (orphan dead code,
  never imported).
- `git rm frontend/src/mock.js` (only importer was ComponentModal).
- `git rm` or repopulate `backend/services/file_service.py` (6-line
  scaffold stub).

**Acceptance:** `pytest`, `ruff check backend`, `npm test` all pass.
No reference to deleted files anywhere in the tree.

**Refs:** sweep verification §1, N.1; deep-dive Findings 1+5; main
audit §4.1.

---

### Slice 2 — Frontend nav bug + backend code-hygiene · P1 · S

**Scope:** three one-line backend hygiene fixes + the verified frontend
nav bug.

- `frontend/src/App.jsx:80` — change `setActiveTab('profile')` →
  `setActiveTab('dashboard')`. The `'profile'` string matches no
  view; only the `|| Dashboard` fallback masks it today.
- `backend/services/jump_service.py:275, 613, 1068` — move three
  function-local imports (`pydantic.ValidationError`, `hashlib`,
  `mimetypes`) to module top.
- `backend/api/jumps.py:287` — `FolderFileResponse(**f.__dict__)` →
  `FolderFileResponse(**dataclasses.asdict(f))`.

**Acceptance:** existing test suite unchanged behaviorally; ruff still
clean.

**Refs:** deep-dive Finding 1; main audit §4.2, §1.7 (NIT).

---

### Slice 3 — Settings-gated exception redaction + bind_host warning · P1 · M

**Scope:** close the two leak gaps that block any non-loopback posture.

- Add `Settings.expose_internal_errors: bool = False` with a
  model-validator that defaults it to `True` only when `bind_host`
  is loopback (D-NEW entry to document the policy).
- `backend/api/rest.py:184` — gate `detail = f"{type(exc).__name__}: {exc}"`
  on the flag; fall back to `"an internal error occurred; see server logs"`
  when the flag is off. Same gating for the stderr traceback at
  `:131-137`.
- `backend/main.py` — `_logger.warning("non_loopback_bind", ...)` when
  `settings.bind_host` is anything other than `127.0.0.1`, `localhost`,
  or `::1`.

**Acceptance:** new test `test_unhandled_exception_redaction.py`
asserts:
1. With the flag off, a deliberate `FileNotFoundError("/home/alice/x")`
   produces a body without `"alice"` or `"/home/"`.
2. With the flag on (loopback default), the body contains the
   exception class name.

**Refs:** deep-dive Finding 2; sweep verification §6 (bind_host).

---

## Phase 2 — API contract completion (cumulative ≈ 1 day)

Three slices that finish the OpenAPI spec and the error envelope so
any future SDK generator produces correct types.

### Slice 4 — OpenAPI `responses=` + `operation_id` on every route · P1 · M

**Scope:** wire `responses=ERROR_RESPONSES` and `operation_id=...` on
all 82 routes across the entity routers.

- Define `ERROR_RESPONSES` in `backend/api/openapi.py` referencing
  the existing `#/components/responses/NotFound`, `Conflict`,
  `ValidationFailed`, `Internal` shapes (currently dead code).
- Touch every `@router.get/post/put/delete` decorator in
  `jumps.py`, `rigs.py`, `jumpers.py`, `dropzones.py`, `containers.py`,
  `aads.py`, `mains.py`, `reserves.py`, `people.py`, `onboarding.py`,
  `ops.py`.
- Each route gets a short `operation_id` (e.g. `create_jump`, not
  `create_jump_route`).

**Acceptance:** snapshot test against `/openapi.json` asserts every
operation declares 4xx/5xx responses with the problem+json schema
ref. SDK generators (openapi-typescript, openapi-generator) produce
typed error shapes.

**Refs:** main audit §1.1, §1.3.

---

### Slice 5 — Normalize built-in FastAPI errors to problem+json · P2 · M

**Scope:** register handlers for `RequestValidationError` and
`StarletteHTTPException` so 422 path-param errors, 404 unknown-path
errors, and 405 method-not-allowed errors all return RFC 9457
problem+json instead of FastAPI's default JSON shape.

- `backend/api/rest.py` — add two handlers, mirroring the existing
  `ServiceError` and `Exception` handlers.
- Tests: hit `GET /api/v1/jumps/not-a-uuid` (422), `GET /api/v1/unknown`
  (404), `OPTIONS /api/v1/jumps` (405 typically) — all return
  `application/problem+json` with `code`, `request_id`, etc.

**Acceptance:** new `test_error_envelope_uniformity.py` asserts the
content type on every error path.

**Refs:** sweep verification §4.

---

### Slice 6 — ProblemDetails OpenAPI ↔ Pydantic alignment test · P2 · S

**Scope:** one regression test ensuring the hand-authored
`PROBLEM_DETAILS_SCHEMA` in `backend/api/openapi.py` covers every
field on the Pydantic `ProblemDetails` model.

- `backend/tests/test_openapi_problem_details_alignment.py` — assert
  `set(ProblemDetails.model_json_schema()["properties"]) <= set(PROBLEM_DETAILS_SCHEMA["properties"])`.
- Future field adds to `ProblemDetails` fail this test until the
  schema is also updated.

**Acceptance:** test passes today; fails when a `ProblemDetails`
field is added without a matching schema edit.

**Refs:** main audit §1.2.

---

## Phase 3 — Concurrency and atomicity gaps (cumulative ≈ 2 days)

Two slices that close the rig recovery gap and add the missing
crash-recovery tests.

### Slice 7 — `folder_reconcile_rigs` + crash-recovery · P2 · L

**Scope:** ship the rig-folder reconcile step that's documented in
`rig_service.py:53` as "a future slice" but is currently absent.

- New function `folder_reconcile_rigs(logbook_root)` in
  `backend/storage/reconcile.py`:
  - Walk `rigs/*/rig.xml`; build set of `(component_kind, id)` refs.
  - For each component, ensure `assigned_rig_id` matches the referring
    rig (repair to None or to the rig id depending on policy).
  - For every component XML, if `assigned_rig_id` is set but the
    referring rig is missing or doesn't ref back, clear it.
  - All repairs go through `_write_*` helpers under WRITER_LOCK.
- New D-entry pins the policy (forward-complete vs revert).
- Call site: `backend/main.py` boot, after bootstrap, before uvicorn
  starts.
- Test `test_rig_partial_create_recovery.py` simulates a SIGKILL
  between component-assigner iters; asserts boot-time reconcile
  converges to a clean state.

**Acceptance:** monkeypatched-failure test passes; explicit policy
in D-entry; no manual recovery required after the documented crash
class.

**Refs:** main audit §2.2; deep-dive §8.1.

---

### Slice 8 — `open_index` refuses newer-on-disk schema · P1 · S

**Scope:** change `backend/storage/index.py:344-355` Branch 3 to
refuse rather than silently downgrade when the on-disk
`PRAGMA user_version` is *greater than* `INDEX_SCHEMA_VERSION`.

- Raise a new `IndexVersionError` (subclass of `ServiceError` with
  http_status=500, code=`index_version_newer`).
- `backend/main.py` catches the error at boot and exits with a
  human-readable message: "logbook index is vN; this app installs
  vM; upgrade the app or delete `<path>/index.sqlite` to rebuild".
- Test `test_index_schema_versioning.py` stamps a v99 `user_version`
  and asserts `open_index` raises.

**Acceptance:** silent downgrade no longer possible; legitimate
older-on-disk → newer-code path still drops + reindexes.

**Refs:** main audit §2.1.

---

### Slice 9 — Partial-write crash-test matrix · P2 · L

**Scope:** add a parametrized test that for each multi-step write
mocks an exception at each documented step and asserts (a) `verify`
detects exactly the expected residue, (b) the next read via the
public service does not crash, (c) `reindex_from_xml` converges.

Operations covered:
- `create_jump`, `update_jump`, `delete_jump`
- `add_attachments`, `track_files`, `delete_attachment`
- `create_rig`, `update_rig`, `delete_rig` (overlaps with Slice 7)
- `jumper_migration.migrate_all_jumpers`

**Acceptance:** `backend/tests/test_partial_write_recovery.py`
exercises ≥12 step-crashes; all converge to a clean state on next
boot.

**Refs:** main audit §5.1, §5.2, §2.3; deep-dive §8.1, §8.2; sweep
verification §8.

---

## Phase 4 — Upload hardening (cumulative ≈ 1 day)

Three slices that close the multipart pipeline gaps before D48.

### Slice 10 — Request + per-file size middleware · P2 · M

**Scope:** add a Starlette middleware that enforces a configurable
total-request cap and a per-file cap on every multipart POST.

- New `Settings.max_request_bytes` (default 5 GiB) and
  `Settings.max_file_bytes` (default 2 GiB).
- Middleware in `backend/api/rest.py` runs before route dispatch;
  413 problem+json with code `payload_too_large` when either limit
  is exceeded. Per-file enforcement happens inside the upload chunk
  loop (`backend/api/jumps.py:_upload_chunks`).
- Tests: oversize total request → 413; one giant file → 413; many
  small files within the cap → 201.

**Acceptance:** every multipart endpoint enforces the cap; no path
can fill the user's disk via a single request.

**Refs:** main audit §1.5; sweep verification §3.

---

### Slice 11 — Content-type sniffing + allow-list · P2 · M

**Scope:** stop trusting the client's multipart `Content-Type`.
Sniff the first chunk of bytes; store the sniffed value; reject or
downgrade on mismatch.

- Add `filetype` (pure-Python, no libmagic binding required) or
  `python-magic` (more reliable, needs libmagic on the host).
- `backend/services/jump_service.py` — wrap the upload pipeline in
  a `_sniff_content_type(first_chunk, declared)` helper.
- Configurable allow-list (default: images, video, PDF, CSV, plain
  text, ZIP); rejection produces 415 problem+json.

**Acceptance:** uploading `evil.html` with declared `image/png`
either rejects (415) or stores the sniffed `text/html`. The
attachment-view endpoint that ships later inherits a trusted MIME.

**Refs:** main audit §1.4; deep-dive §4a; sweep verification §3.

---

### Slice 12 — `Idempotency-Key` for multipart create · P2 · M

**Scope:** accept an `Idempotency-Key` header on `POST /api/v1/jumps`
and `POST /api/v1/jumps/{id}/attachments`. Within a 24-hour window,
a retry with the same key returns the prior response without
recreating.

- Storage: a small SQLite table `idempotency_keys(key, user_id,
  request_hash, response_body, created_at)` with TTL via background
  cleanup at boot.
- Schema bump on `INDEX_SCHEMA_VERSION` (rebuildable per D3).
- Test: retry same multipart POST with same key → identical Jump;
  with different key → second Jump created.

**Acceptance:** retry-on-network-stutter no longer creates
duplicates.

**Refs:** main audit §1.4.

---

## Phase 5 — Performance and caching (cumulative ≈ 1 day)

### Slice 13 — Server-side `Cache-Control` + `ETag` middleware · P2 · M

**Scope:** stop relying on the SPA's defensive `cache: 'no-store'`.

- Middleware: every GET response gets `Cache-Control: private,
  no-cache` (force revalidate) plus an `ETag` derived from a
  per-resource `updated_at`.
- `If-None-Match` short-circuits to 304 when the ETag matches.
- Remove `noStoreInit` from `frontend/src/api.js:51` (clients no
  longer need to be defensive).

**Acceptance:** rerunning a GET after an unchanged resource returns
304; a POST followed by GET returns the new bytes.

**Refs:** main audit §1.6.

---

### Slice 14 — Jumps covering index for ORDER BY · P3 · S

**Scope:** add a SQLite index that supports the list_jumps tiebreak
without a runtime sort.

- `CREATE INDEX idx_jumps_user_date_jump_number ON jumps(user_id, date DESC, jump_number DESC);`
- Bump `INDEX_SCHEMA_VERSION` (Slice 8's refuse-newer is now load-
  bearing for this slice).
- D26-style comment in `index.py` documenting the bump.

**Acceptance:** `EXPLAIN QUERY PLAN` on `list_jumps` shows index
usage with no `USE TEMP B-TREE FOR ORDER BY`.

**Refs:** main audit §3.2.

---

### Slice 15 — Cache `compute_stats` behind the ETag · P3 · M

**Scope:** the known perf bug in `stats_service.compute_stats` —
walks every jump folder per request. With Slice 13's ETag
infrastructure in place, this becomes: compute once, cache by
`(user_id, latest_updated_at)`, return 304 on unchanged.

- In-memory LRU cache keyed by the ETag.
- Invalidated naturally by the ETag when any jump's `updated_at`
  bumps.

**Acceptance:** repeated `GET /api/v1/stats` over a static logbook
hits the cache; one new jump bumps the ETag.

**Refs:** main audit §3.3.

---

## Phase 6 — Frontend hygiene (cumulative ≈ 1 day)

### Slice 16 — Surface API errors instead of swallowing · P1 · M

**Scope:** replace silent `.catch(() => {})` patterns with surfaced
error states. The RFC 9457 envelope is already preserved by
`ApiError`; the UI just isn't reading it.

- `frontend/src/modals/JumpDetailModal.jsx:55` —
  `listJumpFiles().catch(() => {})` → set an error state.
- `frontend/src/views/Jumps.jsx:109` — `getStats().catch(() => {})`
  → render "Stats unavailable" + retry.
- Sweep the rest of the SPA via `grep -nE "\.catch\(\(\)\s*=>"`.

**Acceptance:** every fetch failure produces either a graceful
empty state with a retry, or a banner with the error code from the
problem+json envelope.

**Refs:** deep-dive Finding 6.2.

---

### Slice 17 — Self-host Google Fonts · P2 · M

**Scope:** privacy + offline regression. Local-first app should not
hit Google's CDN on every open.

- Remove the three `<link>` tags at `frontend/index.html:7-9`.
- Download Archivo (variable, weight 400–700) and JetBrains Mono
  (variable, weight 400–500) WOFF2 files. Both are SIL OFL 1.1.
- Place at `frontend/public/fonts/`; add `@font-face` rules in
  `frontend/src/index.css:42-43`.
- Verify the PyInstaller spec bundles `frontend/dist/fonts/` (Vite
  build will fingerprint and copy).

**Acceptance:** packaged binary works fully offline; no DNS lookup
to `fonts.googleapis.com` on app open.

**Refs:** sweep verification §9.

---

### Slice 18 — `<OnboardingProvider>` Context · P2 · M

**Scope:** replace `ONBOARDING_RESUME_EVENT` +
`ONBOARDING_STATE_CHANGED_EVENT` (window-level `CustomEvent` bus) with
a small Context provider.

- New `frontend/src/onboardingContext.jsx` exposing
  `{ state, refresh, requestResume, resumeRequested, acknowledgeResume }`.
- Touch sites: `App.jsx`, `Settings.jsx`, `ResumeBanner.jsx`,
  `Identity.jsx`.
- Keep `ALTITUDE_CHANGE_EVENT` as-is (cross-tab sync genuinely needs
  window-level signals; Context doesn't span tabs).

**Acceptance:** four touched files use the hook; the two onboarding
event constants are gone. `npm test` + manual smoke of the
"Re-run setup wizard" flow.

**Refs:** deep-dive Finding 7.

---

### Slice 19 — Accessibility pass · P3 · M

**Scope:** the WCAG findings from prior audits that didn't fully
land.

- `InlineField` (and similar primitives in `LogJumpModal`) — wire
  `htmlFor={id}` on label, `id={id}` on input. Generate ids via
  `useId()`.
- Sweep Lucide icon usage: every decorative icon gets
  `aria-hidden="true"`.
- Fix any input with no visible label and no `aria-label`.

**Acceptance:** `npm install -D @axe-core/playwright` (or any axe
runner); a smoke run reports zero serious/critical violations on
the main views.

**Refs:** main audit §6.6.

---

## Phase 7 — Backend structural cleanup (cumulative ≈ 2 days)

### Slice 20 — Jump/JumpCreate/JumpUpdate shared-fields mixin · P2 · M

**Scope:** kill the triple-declaration of the same ~16 fields across
the three models.

- Extract `_JumpFields(BaseModel)` with the common fields + their
  validators.
- `Jump`/`JumpCreate`/`JumpUpdate` inherit and add only their
  delta fields (id + audit timestamps + attachments on Jump only).
- Same shape for `Dropzone`/`DropzoneCreate`/`DropzoneUpdate` and
  the four component-model families (`Main`, `Reserve`, `AAD`,
  `Container`).

**Acceptance:** field-level validators applied once; future field
add touches one location, not three. All existing tests pass with
no contract changes.

**Refs:** main audit §4.3; sweep verification §6.

---

### Slice 21 — Configurable CORS origins · P2 · S

**Scope:** move the hardcoded list at `backend/api/rest.py:84-87` to
`Settings.cors_allowed_origins: list[str]` with the current two
values as default.

- Env override: `SKYDIVE_CORS_ALLOWED_ORIGINS=http://foo,http://bar`.
- Validator: warn if any origin is wildcard or non-local while
  `bind_host` is loopback.

**Acceptance:** dev still works; a custom dev port can be added
via env without code edit.

**Refs:** sweep verification §6.

---

### Slice 22 — Split `backend/xml/serialize.py` · P3 · L

**Scope:** 1396 LOC → six per-entity modules under `backend/xml/serialize/`.

- `__init__.py` re-exports the public surface.
- `_helpers.py` hosts `_qn`, `_sub`, `_find`, `_text`,
  `_emit_component_base`, `_parse_component_base`, `_emit_lineset`,
  etc.
- One submodule per entity (jump, dropzone, rig, jumper, component,
  person).

**Acceptance:** no behavior change; pyright + ruff clean; all
existing call sites unchanged.

**Refs:** main audit §4.4; deep-dive Finding 3.

---

### Slice 23 — Split `backend/services/jump_service.py` · P3 · L

**Scope:** 1252 LOC → `backend/services/jump/{crud,attachments,files,_common}.py`.

- `__init__.py` re-exports the public surface.
- Common helpers (`_index_conn`, `_get_jump_folder`,
  `_write_jump_and_manifest`, `_jump_number_is_taken`,
  `_sanitize_upload_filenames`) move to `_common.py`.

**Acceptance:** no test changes required; all imports through
`from backend.services import jump_service` keep working.

**Refs:** main audit §4.4; deep-dive Finding 3.

---

## Phase 8 — Feature gaps that block "serious desktop app" framing

### Slice 24 — Trash listing + restore (backend + UI) · P2 · L

**Scope:** ship the trash feature that Slice 1 currently stubs.

- Backend: `GET /api/v1/trash` returns items in `<logbook_root>/.trash/`
  with structured metadata (kind, deleted_at, original_path).
- Backend: `POST /api/v1/trash/restore` with `{ trashed_path }` →
  moves back to its original location and reindexes.
- Frontend: `Settings.jsx TrashSection` lists items, exposes
  per-item restore + permanent-delete.

**Acceptance:** delete a jump → it shows in the trash list →
restore returns it to the active set.

**Refs:** sweep verification N.1.

---

### Slice 25 — Backup ZIP export/import · P2 · L

**Scope:** one-click "export logbook as ZIP" and "import logbook
from ZIP" for user-controlled backups outside the app.

- Backend: `GET /api/v1/backup/export` streams a ZIP of the logbook
  folder (excluding `.trash/` by default, but with an opt-in
  query param). Resume via Range header is a stretch goal.
- Backend: `POST /api/v1/backup/import` accepts a ZIP, validates
  it, and writes into an isolated subfolder for user review before
  switching.
- Frontend: Settings "Backups" section with export/import buttons.

**Acceptance:** round-trip a logbook through export → import →
verify all XML + SHA256SUMS match; `verify` reports clean.

**Refs:** main audit's data-integrity recommendations; sweep
verification §8.

---

## Phase 9 — Release engineering (cumulative ≈ 2 days)

### Slice 26 — Release workflow + checksums · P2 · L

**Scope:** turn a `git tag v0.x.y` into a GitHub Release with
PyInstaller binaries for each platform, each with a SHA-256 sum.

- `.github/workflows/release.yml` triggers on tag push.
- Matrix builds for Linux / macOS / Windows.
- Uploads each platform's binary to the release.
- Generates `SHA256SUMS.txt` per release.

**Acceptance:** a `v0.1.0-beta.2` tag yields a release page with
three binaries and one checksum file.

**Refs:** sweep verification §10.

---

### Slice 27 — SBOM + dependency audit step · P2 · M

**Scope:** publish a CycloneDX SBOM per release; gate CI on
`pip-audit` (Python) + `npm audit` (frontend).

- Add `cyclonedx-py` to dev deps; CI step generates
  `sbom-{python,node}.json` on each release.
- New CI job: `pip-audit` + `npm audit --omit=dev` (advisory level
  high+).

**Acceptance:** release page carries SBOMs; a new CVE in a pinned
dependency fails CI within hours.

**Refs:** sweep verification §10.

---

### Slice 28 — Signed builds · P2 · XL

**Scope:** Windows Authenticode + macOS codesign + notarization.

- Per-platform certificates: Windows EV code signing cert
  (~$300/yr), Apple Developer ID Application cert ($99/yr).
- `.github/workflows/release.yml` signs after PyInstaller build,
  notarizes the .app bundle, staples the ticket.
- Documentation: `docs/release.md` walks the cert-rotation
  procedure.

**Acceptance:** Windows SmartScreen does not flag; macOS Gatekeeper
opens without right-click-Open ceremony.

**Refs:** sweep verification §10.

**Dependencies:** Slice 26 (release workflow exists).

---

## Phase 10 — Long-horizon (no urgency)

### Slice 29 — TypeScript adoption (incremental) · P3 · XL

**Scope:** introduce TypeScript with `allowJs`, migrate `api.js`
first (taking advantage of Slice 4's full OpenAPI spec for
codegen), then per-view as touched.

- Add TS config, `allowJs: true`, no `strict` initially.
- `openapi-typescript` generates `src/api.types.ts` from
  `/openapi.json` at build time.
- Migrate `api.js → api.ts` (split into per-domain files).
- Each subsequent slice that touches a `.jsx` file migrates it.

**Acceptance:** baseline TS landed; all existing tests pass.
Subsequent slices ratchet up `strict` settings.

**Refs:** main audit §6.3; deep-dive Finding 6.3.

**Dependencies:** Slice 4 (responses=, operation_id) for clean
codegen.

---

### Slice 30 — LogJumpModal decomposition · P3 · XL

**Scope:** 2353 LOC → roughly six sub-components matching the
form's conceptual sections (Header, RigPicker, DropzonePicker,
Notes, Attachments, GroupMembers).

- Each sub-component owns its slice of form state via a parent
  reducer or Zustand slice.
- Tests at the sub-component level.

**Acceptance:** parent file <500 LOC; each sub-component <400 LOC;
hooks count per file ≤8.

**Refs:** main audit §6.4; deep-dive Finding 6.1.

---

### Slice 31 — Property-style round-trip identity tests · P3 · M

**Scope:** add Hypothesis (Python) or fast-check (JS) generators
that produce arbitrary valid entities and assert
`element_to_<E>(<E>_to_element(e)) == e`.

- One test module per entity family.
- Catches "I added a field to the model and forgot to serialize it"
  bugs at PR time.

**Acceptance:** Hypothesis runs for 100 examples per entity in CI
under 10 seconds total.

**Refs:** main audit §4.5.

---

### Slice 32 — FlySight CSV parser · P3 · XL (deferred per D14)

**Scope:** parse the FlySight CSV format attached to jumps and
extract per-jump telemetry (exit, deployment, freefall time, max
speed). Deferred per D14; surfaced here for roadmap completeness.

**Refs:** D14 §"Deferred"; CHANGELOG.md.

---

## Slice dependency graph

```
Slice 1 ─┐
Slice 2 ─┤
Slice 3 ─┴─► (any non-loopback deployment)
Slice 8 ─────► Slice 14 (covering index requires the refuse-newer)
Slice 4 ─────► Slice 5 ─► Slice 6
              └────────► Slice 29 (TS codegen wants full spec)
Slice 7 ─────► (rig partial-write fix; new D-entry)
Slice 9 ─► (general crash recovery confidence)
Slice 10 ─► Slice 11 ─► Slice 12 ─► (D48 LAN exposure)
Slice 13 ─► Slice 15
Slice 17 ─► (offline-first packaging)
Slice 18 ─► (Strict Mode + HMR robustness)
Slice 20 ─► (future field adds become 1-place)
Slice 24 ─► Slice 25 (both lean on the same export-folder helpers)
Slice 26 ─► Slice 27 ─► Slice 28 (release pipeline → SBOM → signing)
```

## Recommended execution waves

**Wave A — this week (≈2 days):** Slices 1, 2, 3, 4, 8.
Outcome: no more silent navigation bug, no exception leak, OpenAPI
spec is correct, index can't silently downgrade. Branch ready for
external contributor review without major caveats.

**Wave B — gate D48 LAN exposure (≈3 days):** Slices 5, 7, 9, 10,
11, 12, 21. Outcome: every error envelope normalized, rig recovery
implemented, partial-write tests passing, multipart hardened,
CORS configurable. The app is now safely bindable beyond loopback
with auth-pending.

**Wave C — gate v0.2 release (≈3 days):** Slices 13, 14, 15, 16,
17, 26, 27. Outcome: cache headers replace the defensive client
hack, perf bug closed, frontend errors surfaced, fonts bundled,
release pipeline with checksums + SBOM.

**Wave D — gate "serious desktop app" framing (≈1 week):**
Slices 18, 19, 20, 22, 23, 24, 25, 28. Outcome: signed binaries,
trash + backup features, model dedup, structural splits, a11y
pass, Context-based onboarding.

**Wave E — long horizon:** Slices 29, 30, 31, 32. TypeScript,
modal decomposition, property tests, FlySight.

---

## Effort summary

| Wave | Slices | Total effort |
|---|---|---|
| A | 5 | ~2 days |
| B | 7 | ~3 days |
| C | 7 | ~3 days |
| D | 8 | ~1 week |
| E | 4 | multi-week |
| **Total** | **31** | **~4–5 weeks** of focused work |

Wave A is the cheapest, highest-readability shipment in the project;
it can land in two PRs (one for Phase 1, one for Phase 2). Wave B is
where the bulk of "scary post-merge" gating gets de-risked.

---

*— end —*
